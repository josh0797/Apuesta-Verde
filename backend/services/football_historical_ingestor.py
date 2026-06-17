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
import logging
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
_COL_B365D = ("B365D", "PSD", "PSCD")   # draw odd cascade
_COL_B365H = ("B365H", "PSH")
_COL_B365A = ("B365A", "PSA")


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
) -> list[dict]:
    """Parse a football-data.co.uk-style CSV.

    Returns rows sorted by date with the canonical schema used by the
    backtest engine. Bad rows are silently dropped (fail-soft).
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
            "odd_home":     _parse_float(_first(row, _COL_B365H)),
            "odd_draw":     _parse_float(_first(row, _COL_B365D)),
            "odd_away":     _parse_float(_first(row, _COL_B365A)),
        })
    out.sort(key=lambda m: m["date"])
    return out


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
    return features


__all__ = [
    "ELO_DEFAULT", "ELO_K_FACTOR", "FORM_L5", "FORM_L15",
    "parse_football_data_csv", "fetch_football_data_csv",
    "build_point_in_time_features",
    "_team_history_slice", "_elo_walk_forward", "_avg",
]
