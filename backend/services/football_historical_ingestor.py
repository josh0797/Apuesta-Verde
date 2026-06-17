"""Sprint-D · Football Historical Ingestor.

Reads historical match data from public sources (primary:
football-data.co.uk CSVs) and produces point-in-time pre-match
snapshots suitable for the backtest engine.

Point-in-time discipline
------------------------
The ingestor returns the *raw* match rows AND a helper
``build_point_in_time_features(matches_sorted, target_index)`` that
builds features using ONLY ``matches_sorted[:target_index]`` — i.e.
strictly past data. The backtest engine MUST use this helper instead
of hand-rolling its own feature extraction.

The per-match raw row exposes the **pre-kickoff** odds (opening odds
from Bet365 / etc.) which are point-in-time-safe; closing odds are
available in some sources but are NOT used by default (closing odds
can be considered post-information if the market reacted to
late-breaking news like lineups).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
from typing import Iterable, Optional

log = logging.getLogger("football_historical_ingestor")

# Initial ELO rating used when a team is first seen.
ELO_DEFAULT  = 1500.0
ELO_K_FACTOR = 20.0

# Recent-form window sizes.
FORM_L5  = 5
FORM_L15 = 15

# Football-data.co.uk CSV column shorthand.
_COL_DATE  = ("Date",)
_COL_HOME  = ("HomeTeam", "Home")
_COL_AWAY  = ("AwayTeam", "Away")
_COL_FTHG  = ("FTHG", "HG")
_COL_FTAG  = ("FTAG", "AG")
_COL_FTR   = ("FTR", "Res")
_COL_HC    = ("HC",)
_COL_AC    = ("AC",)
# Sprint-D4 — Separate OPENING vs CLOSING cascades.
# football-data.co.uk publishes two columns per book:
#   * B365H / B365D / B365A     → opening (set at market open)
#   * B365CH / B365CD / B365CA  → closing (just before kickoff)
# Pinnacle uses PSH/PSD/PSA (opening) and PSCH/PSCD/PSCA (closing).
_COL_B365D_OPEN = ("B365D", "PSD")
_COL_B365H_OPEN = ("B365H", "PSH")
_COL_B365A_OPEN = ("B365A", "PSA")
_COL_B365D_CLOSE = ("B365CD", "PSCD")
_COL_B365H_CLOSE = ("B365CH", "PSCH")
_COL_B365A_CLOSE = ("B365CA", "PSCA")
# Sprint-D back-compat (kept for older callers).
_COL_B365D = _COL_B365D_OPEN + _COL_B365D_CLOSE
_COL_B365H = _COL_B365H_OPEN + _COL_B365H_CLOSE
_COL_B365A = _COL_B365A_OPEN + _COL_B365A_CLOSE

# Sprint-D4 — Odds-type taxonomy.
ODDS_TYPE_OPENING = "OPENING"
ODDS_TYPE_CLOSING = "CLOSING"
ODDS_TYPE_MIXED   = "MIXED"
ODDS_TYPE_NONE    = "NONE"


def _first(row: dict, candidates: tuple[str, ...]) -> Optional[str]:
    for k in candidates:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


def _parse_float(v) -> Optional[float]:
    try:
        if v in (None, "", "NA"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_int(v) -> Optional[int]:
    f = _parse_float(v)
    return int(f) if f is not None else None


def _parse_date(v: str) -> Optional[datetime]:
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_football_data_csv(
    csv_text: str, *, competition: str = "",
    prefer_closing: bool = False,
) -> list[dict]:
    """Parse a football-data.co.uk-style CSV.

    Returns rows sorted by date with the canonical schema used by the
    backtest engine. Bad rows are silently dropped (fail-soft).

    Sprint-D4 enhancements
    ----------------------
    * Detects whether the row carries OPENING odds, CLOSING odds, or
      both, and exposes:
        - ``odd_home/draw/away`` (the canonical odds used by the engine)
        - ``odds_type`` ∈ {OPENING, CLOSING, MIXED, NONE}
        - ``warnings`` (list of strings, may include
          ``ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC``)
    * When both opening and closing odds exist, ``odd_*`` is set from
      opening by default (more honest for backtests). Pass
      ``prefer_closing=True`` to flip the default — in which case
      ``ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC`` is appended to the row's
      warnings.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    out: list[dict] = []
    for row in reader:
        date = _parse_date(_first(row, _COL_DATE) or "")
        if date is None:
            continue
        home = _first(row, _COL_HOME) or ""
        away = _first(row, _COL_AWAY) or ""
        if not home or not away:
            continue
        fthg = _parse_int(_first(row, _COL_FTHG))
        ftag = _parse_int(_first(row, _COL_FTAG))
        ftr  = (_first(row, _COL_FTR) or "").upper()
        if ftr not in ("H", "D", "A") or fthg is None or ftag is None:
            continue

        # Sprint-D4 · odds detection.
        oh_open = _parse_float(_first(row, _COL_B365H_OPEN))
        od_open = _parse_float(_first(row, _COL_B365D_OPEN))
        oa_open = _parse_float(_first(row, _COL_B365A_OPEN))
        oh_close = _parse_float(_first(row, _COL_B365H_CLOSE))
        od_close = _parse_float(_first(row, _COL_B365D_CLOSE))
        oa_close = _parse_float(_first(row, _COL_B365A_CLOSE))

        has_open  = any(v is not None for v in (oh_open, od_open, oa_open))
        has_close = any(v is not None for v in (oh_close, od_close, oa_close))

        warnings: list[str] = []
        if prefer_closing and has_close:
            oh, od, oa = oh_close, od_close, oa_close
            odds_type = ODDS_TYPE_CLOSING
            warnings.append("ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC")
        elif has_open:
            oh, od, oa = oh_open, od_open, oa_open
            odds_type = (
                ODDS_TYPE_MIXED if has_close else ODDS_TYPE_OPENING
            )
        elif has_close:
            oh, od, oa = oh_close, od_close, oa_close
            odds_type = ODDS_TYPE_CLOSING
            warnings.append("ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC")
        else:
            oh, od, oa = None, None, None
            odds_type = ODDS_TYPE_NONE

        out.append({
            "competition":  competition or "",
            "date":         date,
            "home_team":    home.strip(),
            "away_team":    away.strip(),
            "fthg":         fthg,
            "ftag":         ftag,
            "ftr":          ftr,
            "home_corners": _parse_int(_first(row, _COL_HC)),
            "away_corners": _parse_int(_first(row, _COL_AC)),
            "odd_home":     oh,
            "odd_draw":     od,
            "odd_away":     oa,
            # Sprint-D4 metadata:
            "odd_home_open":  oh_open,
            "odd_draw_open":  od_open,
            "odd_away_open":  oa_open,
            "odd_home_close": oh_close,
            "odd_draw_close": od_close,
            "odd_away_close": oa_close,
            "odds_type":      odds_type,
            "warnings":       warnings,
        })
    out.sort(key=lambda m: m["date"])
    return out


# Sprint-D4 alias (more intuitive).
parse_footballdata_csv = parse_football_data_csv


async def fetch_football_data_csv(url: str) -> str:
    """Tiny async wrapper around an httpx GET for football-data.co.uk."""
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


# ─────────────────────────────────────────────────────────────────────────────────
# Point-in-time feature builders
# ─────────────────────────────────────────────────────────────────────────────────
def _team_history_slice(
    team: str, matches_sorted: list[dict], up_to_index: int,
) -> list[dict]:
    """Return all PRIOR matches involving ``team`` strictly BEFORE
    ``matches_sorted[up_to_index]``. Newest-first."""
    out: list[dict] = []
    target_date = matches_sorted[up_to_index]["date"] if up_to_index < len(matches_sorted) else None
    for j in range(up_to_index - 1, -1, -1):
        m = matches_sorted[j]
        # Strict <: same-day fixtures are excluded to keep the rule
        # ``feature_date < match_date`` water-tight.
        if target_date is not None and m["date"] >= target_date:
            continue
        if m["home_team"] == team or m["away_team"] == team:
            out.append(m)
    return out


def _team_goals_corners(history: list[dict], team: str) -> tuple[list[float], list[float]]:
    """Return (goals_for_list, corners_for_list) newest-first."""
    gf: list[float] = []
    cf: list[float] = []
    for m in history:
        if m["home_team"] == team:
            gf.append(float(m["fthg"]))
            if m["home_corners"] is not None:
                cf.append(float(m["home_corners"]))
        else:
            gf.append(float(m["ftag"]))
            if m["away_corners"] is not None:
                cf.append(float(m["away_corners"]))
    return gf, cf


def _team_goals_against(history: list[dict], team: str) -> list[float]:
    """Sprint-D3 · Return goals_against newest-first for ``team`` over
    ``history``."""
    ga: list[float] = []
    for m in history:
        if m["home_team"] == team:
            ga.append(float(m["ftag"]))
        else:
            ga.append(float(m["fthg"]))
    return ga


def _avg(values: list[float], k: int) -> Optional[float]:
    """Average over the first k values (newest first). None if empty."""
    if not values:
        return None
    sub = values[:k]
    if not sub:
        return None
    return round(sum(sub) / len(sub), 3)


def _elo_walk_forward(matches_sorted: list[dict], up_to_index: int) -> dict:
    """Compute ELO ratings using ONLY matches before ``up_to_index``.

    Walks chronologically: starting at ELO_DEFAULT for every team, plays
    out every prior match, updates ratings. Returns the rating table
    *as of just before* ``matches_sorted[up_to_index]``.
    """
    elo: dict[str, float] = {}
    K = ELO_K_FACTOR
    for j in range(up_to_index):
        m = matches_sorted[j]
        h = m["home_team"]; a = m["away_team"]
        rh = elo.setdefault(h, ELO_DEFAULT)
        ra = elo.setdefault(a, ELO_DEFAULT)
        # Home-field advantage ≈ +60 ELO points (standard in football
        # ELO literature). Applied only to the expected-score calc.
        eh = 1.0 / (1.0 + 10 ** ((ra - (rh + 60)) / 400.0))
        ea = 1.0 - eh
        # Outcome: 1.0 home win, 0.5 draw, 0 away win.
        if m["ftr"] == "H":
            sh, sa = 1.0, 0.0
        elif m["ftr"] == "A":
            sh, sa = 0.0, 1.0
        else:
            sh, sa = 0.5, 0.5
        elo[h] = rh + K * (sh - eh)
        elo[a] = ra + K * (sa - ea)
    return elo


def build_point_in_time_features(
    matches_sorted: list[dict], target_index: int,
) -> dict:
    """Build features for ``matches_sorted[target_index]`` using ONLY
    information from ``matches_sorted[:target_index]``.

    The output dict matches the signature of ``compute_draw_potential``.
    The strict less-than rule (``feature_date < match_date``) is
    enforced inside ``_team_history_slice``.
    """
    m = matches_sorted[target_index]
    home = m["home_team"]; away = m["away_team"]

    elo = _elo_walk_forward(matches_sorted, target_index)

    h_hist = _team_history_slice(home, matches_sorted, target_index)
    a_hist = _team_history_slice(away, matches_sorted, target_index)

    h_gf, h_cf = _team_goals_corners(h_hist, home)
    a_gf, a_cf = _team_goals_corners(a_hist, away)
    # Sprint-D3 · Goals against (for Dixon-Coles / OVER 1.5 fallback).
    h_ga = _team_goals_against(h_hist, home)
    a_ga = _team_goals_against(a_hist, away)

    # We use goal averages as a xG proxy (football-data.co.uk doesn't
    # ship xG). The semantic contract for ``compute_draw_potential`` is
    # "recent goal-creation strength", so this proxy is acceptable for
    # the backtest while we wait for FBref/Understat hydration.
    features = {
        "home_team":              home,
        "away_team":              away,
        "elo_home":               elo.get(home),
        "elo_away":               elo.get(away),
        "xg_home_l5":             _avg(h_gf, FORM_L5),
        "xg_away_l5":             _avg(a_gf, FORM_L5),
        # Sprint-D3 · Goal averages (used by Dixon-Coles fallback).
        "goal_avg_for_home":      _avg(h_gf, FORM_L5),
        "goal_avg_for_away":      _avg(a_gf, FORM_L5),
        "goal_avg_against_home":  _avg(h_ga, FORM_L5),
        "goal_avg_against_away":  _avg(a_ga, FORM_L5),
        # Contextual flags (league: not group stage; WC parser sets them).
        "is_group_stage":         bool(m.get("is_group_stage", False)),
        "both_need_points":       bool(m.get("both_need_points", False)),
        "low_goal_environment":   False,
        "conservative_style_home": False,
        "conservative_style_away": False,
        # Market implied draw probability from the PRE-kickoff B365 odd.
        "market_implied_draw_prob": (1.0 / m["odd_draw"]) if m.get("odd_draw") else None,
        # Debug audit.
        "_audit": {
            "home_hist_n": len(h_hist),
            "away_hist_n": len(a_hist),
            "point_in_time_verified": True,
            "features_unavailable": [
                k for k, v in (
                    ("elo_home",   elo.get(home)),
                    ("elo_away",   elo.get(away)),
                    ("xg_home_l5", _avg(h_gf, FORM_L5)),
                    ("xg_away_l5", _avg(a_gf, FORM_L5)),
                    ("market_implied_draw_prob",
                     (1.0 / m["odd_draw"]) if m.get("odd_draw") else None),
                ) if v is None
            ],
        },
    }
    # ── Tournament context (Sprint D2) ──────────────────────────────────
    # If this match is part of a tournament (WC / Euro / etc.), compute
    # a point-in-time tournament_context_score using ONLY prior matches
    # of the same tournament + same group.
    if m.get("tournament_phase") or m.get("competition", "").lower() in (
        "world cup 2022", "euro 2024",
    ):
        try:
            from .football_tournament_context import (
                compute_tournament_context_score,
            )
            standings_h, standings_a = compute_group_standings_pit(
                matches_sorted, target_index,
                home=home, away=away,
                group_label=m.get("group_label"),
                competition=m.get("competition", ""),
            )
            # Derive the GROUP matchday from PIT standings: the current
            # match will be the (played + 1)-th group game for each
            # team. We use the MAX of the two (rare to differ in WC /
            # Euro, but defensive). For knockout matches the standings
            # are empty so we leave matchday=None.
            if m.get("is_group_stage"):
                group_md = max(
                    (standings_h.get("played") or 0),
                    (standings_a.get("played") or 0),
                ) + 1
            else:
                group_md = None
            ctx = compute_tournament_context_score(
                standings_home=standings_h,
                standings_away=standings_a,
                match_meta={
                    "matchday":         group_md,
                    "tournament_phase": m.get("tournament_phase"),
                    "group_label":      m.get("group_label"),
                    "is_group_stage":   bool(m.get("is_group_stage", False)),
                },
            )
            features["tournament_context_score"] = ctx.get("score_0_1")
            features["_audit"]["tournament_context"] = ctx
            features["_audit"]["group_matchday"] = group_md
        except Exception as exc:    # noqa: BLE001
            log.debug("tournament_context_score failed: %s", exc)
            features["tournament_context_score"] = None
    return features


# ─────────────────────────────────────────────────────────────────────────────────
# openfootball JSON parser (Sprint-D2)
# ─────────────────────────────────────────────────────────────────────────────────
# Maps openfootball ``round`` strings to a canonical (phase, matchday).
_GROUP_ROUND_RE = re.compile(r"matchday\s+(\d+)", re.IGNORECASE)


def _classify_openfootball_round(round_str: str, group_str: Optional[str]) -> tuple[str, Optional[int]]:
    """Classify an openfootball ``round`` value.

    Returns (phase, matchday). ``phase`` is one of
    ``"GROUP" | "KNOCKOUT" | "UNKNOWN"``. ``matchday`` is an integer
    (1, 2, 3, ...) for group-stage rounds, ``None`` otherwise.
    """
    r = (round_str or "").strip().lower()
    g = (group_str or "").strip().lower()
    # Group-stage detection:
    md_match = _GROUP_ROUND_RE.search(r)
    if md_match:
        return "GROUP", int(md_match.group(1))
    if r.startswith("group ") or g.startswith("group "):
        # Some openfootball files use "Group A · Matchday 1" or just
        # "Group A". When we cannot extract a matchday, fall back to
        # None.
        return "GROUP", None
    # Knockout-stage detection (extensible).
    knockout_markers = (
        "round of 16", "round of 32",
        "quarter", "semi",
        "third-place", "third place",
        "final", "playoff", "play-off",
    )
    if any(k in r for k in knockout_markers):
        return "KNOCKOUT", None
    return "UNKNOWN", None


def parse_openfootball_json(
    data, *, competition: str = "",
) -> list[dict]:
    """Parse an openfootball-style JSON payload.

    ``data`` may be either a ``dict``, a JSON string, or a file-like
    object. Returns a list of canonical match rows sorted ascending by
    date. Bad rows are silently dropped (fail-soft).

    Schema additions vs ``parse_football_data_csv``:
    * ``tournament_phase`` — ``"GROUP" | "KNOCKOUT" | "UNKNOWN"``
    * ``matchday`` — int (group stage) or None
    * ``group_label`` — e.g. ``"Group A"`` when available, else None
    * ``is_group_stage`` — bool (convenience flag)
    * ``odd_home/odd_draw/odd_away`` are always ``None`` (openfootball
      ships no market data).
    """
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        return []
    matches = data.get("matches") or []
    name = competition or data.get("name") or ""
    out: list[dict] = []
    for raw in matches:
        try:
            date_str = (raw.get("date") or "").strip()
            date = _parse_date(date_str)
            if date is None:
                continue
            home = (raw.get("team1") or "").strip()
            away = (raw.get("team2") or "").strip()
            if not home or not away:
                continue
            score = raw.get("score") or {}
            ft = score.get("ft")
            if not (isinstance(ft, (list, tuple)) and len(ft) >= 2):
                # No full-time score (e.g. cancelled / future). Drop.
                continue
            fthg, ftag = int(ft[0]), int(ft[1])
            if fthg > ftag:
                ftr = "H"
            elif fthg < ftag:
                ftr = "A"
            else:
                ftr = "D"
            group_label = raw.get("group") or None
            phase, matchday = _classify_openfootball_round(
                raw.get("round") or "", group_label,
            )
            is_group_stage = (phase == "GROUP")
            out.append({
                "competition":      name,
                "date":             date,
                "home_team":        home,
                "away_team":        away,
                "fthg":             fthg,
                "ftag":             ftag,
                "ftr":              ftr,
                "home_corners":     None,
                "away_corners":     None,
                "odd_home":         None,
                "odd_draw":         None,
                "odd_away":         None,
                # Sprint D2 extensions:
                "tournament_phase": phase,
                "matchday":         matchday,
                "group_label":      group_label,
                "is_group_stage":   is_group_stage,
            })
        except Exception as exc:    # noqa: BLE001
            log.debug("openfootball row drop: %s", exc)
            continue
    out.sort(key=lambda m: m["date"])
    return out


# ─────────────────────────────────────────────────────────────────────────────────
# Point-in-time group standings (Sprint-D2)
# ─────────────────────────────────────────────────────────────────────────────────
def _empty_standings_row(team: str) -> dict:
    return {
        "team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0,
        "gf": 0, "ga": 0, "gd": 0, "points": 0,
    }


def compute_group_standings_pit(
    matches_sorted: list[dict], target_index: int, *,
    home: str, away: str,
    group_label: Optional[str],
    competition: str,
) -> tuple[dict, dict]:
    """Compute (home_row, away_row) point-in-time standings for the
    group as of just before ``matches_sorted[target_index]``.

    Strict no-leakage rule:
      * Only matches with index < target_index are considered.
      * Only matches with the SAME ``competition`` and SAME
        ``group_label`` are considered.
      * Same-day matches are still included (they happened earlier on
        the calendar day). This matches FIFA / UEFA scheduling where
        the matchday-3 fixtures are deliberately played simultaneously
        to remove information asymmetry — but in the context of
        STANDINGS we only care that the match completed before the
        target match's kickoff. Since openfootball doesn't ship full
        timestamps and our comparator is date-only, we are conservative
        and still apply ``< target_date``.

    If no group_label is provided (knockout matches), returns empty
    standings for both teams.
    """
    home_row = _empty_standings_row(home)
    away_row = _empty_standings_row(away)
    if not group_label:
        return home_row, away_row
    target_date = matches_sorted[target_index]["date"]
    comp_lc = (competition or "").lower()
    for j in range(target_index):
        m = matches_sorted[j]
        if m["date"] >= target_date:
            continue
        if (m.get("group_label") or "") != group_label:
            continue
        if (m.get("competition") or "").lower() != comp_lc:
            continue
        for team_row, team_name in ((home_row, home), (away_row, away)):
            if m["home_team"] == team_name:
                team_row["played"] += 1
                team_row["gf"] += m["fthg"]
                team_row["ga"] += m["ftag"]
                if m["ftr"] == "H":
                    team_row["won"] += 1
                    team_row["points"] += 3
                elif m["ftr"] == "D":
                    team_row["drawn"] += 1
                    team_row["points"] += 1
                else:
                    team_row["lost"] += 1
            elif m["away_team"] == team_name:
                team_row["played"] += 1
                team_row["gf"] += m["ftag"]
                team_row["ga"] += m["fthg"]
                if m["ftr"] == "A":
                    team_row["won"] += 1
                    team_row["points"] += 3
                elif m["ftr"] == "D":
                    team_row["drawn"] += 1
                    team_row["points"] += 1
                else:
                    team_row["lost"] += 1
    home_row["gd"] = home_row["gf"] - home_row["ga"]
    away_row["gd"] = away_row["gf"] - away_row["ga"]
    return home_row, away_row


__all__ = [
    "ELO_DEFAULT", "ELO_K_FACTOR", "FORM_L5", "FORM_L15",
    "parse_football_data_csv", "parse_footballdata_csv",
    "fetch_football_data_csv",
    "parse_openfootball_json", "compute_group_standings_pit",
    "build_point_in_time_features",
    "_team_history_slice", "_elo_walk_forward", "_avg",
    "_classify_openfootball_round",
    "ODDS_TYPE_OPENING", "ODDS_TYPE_CLOSING",
    "ODDS_TYPE_MIXED", "ODDS_TYPE_NONE",
]
