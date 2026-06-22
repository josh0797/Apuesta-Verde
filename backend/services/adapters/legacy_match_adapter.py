"""Sprint-F98 · Legacy match-doc → F98 envelope adapter (pure).

This adapter converts the LIVE match-doc shape produced by
``data_ingestion.py`` (legacy schema) into the F98 envelope. It is the
key piece that lets us migrate consumers to read F74 first WITHOUT
forcing every upstream writer to migrate at the same time.

The legacy shape we expect (any-of):

  match["home_team"]["context"]["recent_fixtures"] : list[dict]
  match["away_team"]["context"]["recent_fixtures"] : list[dict]
  match["home_team"]["context"]["goals_scored_l5"]            # if present
  match["home_team"]["goals_scored_l5"]                       # alternate
  match["home_xg"], match["away_xg"]                          # flat
  match["home_corners_for_l5"], match["away_corners_for_l5"]  # flat
  match["h2h_recent"] : list[dict]
  match["odds"]       : list / dict
  match["_thestatsapi_enrichment"], match["thestatsapi_snapshot"]
  match["external_context"]["thestatsapi"]

We aggregate `recent_fixtures` ourselves (this is the missing piece —
data_ingestion stores the fixtures but never produces L5 averages).
"""
from __future__ import annotations

from typing import Any, Optional

from services.adapters._envelope import (
    RC_MAPPING_OK,
    RC_MAPPING_PARTIAL,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    _safe_float,
    _safe_int,
    _safe_mean,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE = "legacy_match_doc"


def _team_block(match: dict, side: str) -> dict:
    """Return the team block (home or away). Tolerates several shapes."""
    block = match.get(f"{side}_team")
    if isinstance(block, dict):
        return block
    return {}


def _coalesce(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _aggregate_from_fixtures(
    fixtures: Any,
    *,
    team_name: str = "",
    team_id: Any = None,
    n: int = 5,
) -> dict:
    """Compute L5 aggregates from recent_fixtures.

    Each fixture is expected to be a normalised dict with at minimum:
      home_team / away_team (str or {"id","name"})
      home_goals / away_goals (int)
    Optional rich stats:
      home_stats / away_stats with keys
        {shots, shots_on_target, possession, corners, xg}

    The function works out which side "ours" is on a per-fixture basis
    so a team's L5 averages reflect their perspective regardless of
    whether they played home or away.
    """
    if not isinstance(fixtures, list):
        return {}
    pick = list(fixtures)[-int(n):] if int(n) > 0 else []
    if not pick:
        return {}
    goals_for = []
    goals_against = []
    shots_for = []
    shots_sot = []
    poss = []
    corners_for = []
    corners_ag  = []
    xg_for = []
    xg_ag  = []
    btts_count = 0
    clean_sheets = 0
    form_letters: list[str] = []

    def _team_id_of(side_block: Any) -> Any:
        if isinstance(side_block, dict):
            return side_block.get("id")
        return None

    def _team_name_of(side_block: Any) -> str:
        if isinstance(side_block, dict):
            return str(side_block.get("name") or "").strip().lower()
        return str(side_block or "").strip().lower()

    team_name_norm = (team_name or "").strip().lower()

    for fx in pick:
        if not isinstance(fx, dict):
            continue
        home_block = fx.get("home_team") if "home_team" in fx else fx.get("home")
        away_block = fx.get("away_team") if "away_team" in fx else fx.get("away")
        h_goals = _safe_int(_coalesce(fx, "home_goals", "home_score"))
        a_goals = _safe_int(_coalesce(fx, "away_goals", "away_score"))
        if h_goals is None or a_goals is None:
            continue
        # Resolve which side we are on.
        is_home = False
        if team_id is not None:
            if _team_id_of(home_block) == team_id:
                is_home = True
            elif _team_id_of(away_block) == team_id:
                is_home = False
            else:
                # fall back to name compare
                is_home = team_name_norm == _team_name_of(home_block)
        else:
            is_home = team_name_norm == _team_name_of(home_block)

        ours, theirs = (h_goals, a_goals) if is_home else (a_goals, h_goals)
        goals_for.append(ours)
        goals_against.append(theirs)
        # Form letter
        if ours > theirs:
            form_letters.append("W")
        elif ours < theirs:
            form_letters.append("L")
        else:
            form_letters.append("D")
        if ours > 0 and theirs > 0:
            btts_count += 1
        if theirs == 0:
            clean_sheets += 1
        # Rich stats — optional
        side_stats = fx.get("home_stats" if is_home else "away_stats") or {}
        opp_stats  = fx.get("away_stats" if is_home else "home_stats") or {}
        if isinstance(side_stats, dict):
            s = _safe_float(_coalesce(side_stats, "shots", "total_shots"))
            if s is not None:
                shots_for.append(s)
            ssot = _safe_float(_coalesce(side_stats, "shots_on_target", "shots_on_goal"))
            if ssot is not None:
                shots_sot.append(ssot)
            p = _safe_float(side_stats.get("possession"))
            if p is not None:
                poss.append(p)
            cf = _safe_int(side_stats.get("corners"))
            if cf is not None:
                corners_for.append(cf)
            xf = _safe_float(_coalesce(side_stats, "xg", "expected_goals"))
            if xf is not None:
                xg_for.append(xf)
        if isinstance(opp_stats, dict):
            ca = _safe_int(opp_stats.get("corners"))
            if ca is not None:
                corners_ag.append(ca)
            xa = _safe_float(_coalesce(opp_stats, "xg", "expected_goals"))
            if xa is not None:
                xg_ag.append(xa)

    sample = len(goals_for)
    return {
        "sample":              sample,
        "form_string_l5":      "".join(form_letters) if form_letters else None,
        "goals_scored_l5":     _safe_mean(goals_for),
        "goals_conceded_l5":   _safe_mean(goals_against),
        "shots_for_l5":        _safe_mean(shots_for),
        "shots_on_target_l5":  _safe_mean(shots_sot),
        "possession_avg_l5":   _safe_mean(poss),
        "corners_for_l5":      _safe_mean(corners_for),
        "corners_against_l5":  _safe_mean(corners_ag),
        "xg_for_l5":           _safe_mean(xg_for),
        "xg_against_l5":       _safe_mean(xg_ag),
        "btts_rate_l5":        (btts_count / sample) if sample else None,
        "clean_sheets_l5":     clean_sheets if sample else None,
        "recent_fixtures":     list(pick),
    }


def _populate_side(env: dict, side: str, agg: dict) -> bool:
    """Populate envelope side from aggregates dict. Returns True if any
    field was written."""
    wrote_any = False
    if not isinstance(agg, dict):
        return False
    sample = _safe_int(agg.get("sample")) or 0
    for metric, value in agg.items():
        if metric == "sample":
            continue
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and len(value) == 0:
            continue
        if set_field(env, f"{side}.{metric}", value, sample_size=sample):
            wrote_any = True
    return wrote_any


def adapt_legacy_match_to_f74(match: Any) -> dict:
    """Convert a live match document (legacy schema) into the F98 envelope.

    This is the **bridge adapter** for the F98 migration: existing
    consumers keep writing into the legacy shape, while the F74
    canonical schema is populated on read by feeding the same match
    doc through this adapter.
    """
    if not isinstance(match, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
    if not match:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([k for k in match.keys() if isinstance(k, str)])[:30]
    env["reason_codes"].append("LEGACY_MATCH_DOC_ADAPTED")

    home_t = _team_block(match, "home")
    away_t = _team_block(match, "away")

    home_ctx = home_t.get("context") if isinstance(home_t.get("context"), dict) else {}
    away_ctx = away_t.get("context") if isinstance(away_t.get("context"), dict) else {}

    home_name = home_t.get("name") or match.get("home_team_name") or ""
    away_name = away_t.get("name") or match.get("away_team_name") or ""
    home_id   = home_t.get("id")
    away_id   = away_t.get("id")

    # ── 1) Aggregate from recent_fixtures (THE KEY FIX) ───────────────
    home_recent = home_ctx.get("recent_fixtures") if isinstance(home_ctx, dict) else None
    away_recent = away_ctx.get("recent_fixtures") if isinstance(away_ctx, dict) else None
    home_agg = _aggregate_from_fixtures(
        home_recent, team_name=home_name, team_id=home_id, n=5,
    )
    away_agg = _aggregate_from_fixtures(
        away_recent, team_name=away_name, team_id=away_id, n=5,
    )
    wrote_home = _populate_side(env, "home", home_agg)
    wrote_away = _populate_side(env, "away", away_agg)

    # ── 2) Pre-computed flat fields (legacy)  ─────────────────────────
    # These OVERRIDE the aggregates (highest-quality wins). The cascade
    # selector later picks across all sources — here we just project.
    flat_pairs = [
        ("home", "xg_for_l5",            match.get("home_xg")),
        ("away", "xg_for_l5",            match.get("away_xg")),
        ("home", "corners_for_l5",       match.get("home_corners_for_l5")),
        ("away", "corners_for_l5",       match.get("away_corners_for_l5")),
        ("home", "corners_against_l5",   match.get("home_corners_against_l5")),
        ("away", "corners_against_l5",   match.get("away_corners_against_l5")),
    ]
    for side, metric, value in flat_pairs:
        v = _safe_float(value)
        if v is not None:
            # Use a generous sample of 5 (we don't know the real sample
            # because the legacy writer didn't record it).
            set_field(env, f"{side}.{metric}", v, sample_size=5,
                      reason_codes=["LEGACY_FLAT_FIELD"])

    # ── 3) Per-team L5/L15 averages stored under home_team.* (legacy)  ─
    for side, side_block in (("home", home_t), ("away", away_t)):
        for metric in ("goals_scored_l5", "goals_conceded_l5",
                        "btts_rate_l5", "clean_sheets_l5"):
            v = _safe_float(_coalesce(side_block, metric))
            if v is not None and side_block:
                set_field(env, f"{side}.{metric}", v, sample_size=5,
                          reason_codes=["LEGACY_TEAM_BLOCK"])

    # ── 4) H2H ────────────────────────────────────────────────────────
    h2h_raw = match.get("h2h_recent") or match.get("h2h")
    if isinstance(h2h_raw, list) and h2h_raw:
        hh: list[dict] = []
        hw = aw = dr = 0
        for it in h2h_raw[-10:]:
            if not isinstance(it, dict):
                continue
            h_g = _safe_int(_coalesce(it, "home_goals", "home_score"))
            a_g = _safe_int(_coalesce(it, "away_goals", "away_score"))
            if h_g is None or a_g is None:
                continue
            hh.append({
                "date":       it.get("date"),
                "home_team":  _coalesce(it, "home_team") or it.get("home"),
                "away_team":  _coalesce(it, "away_team") or it.get("away"),
                "home_goals": h_g,
                "away_goals": a_g,
            })
            if h_g > a_g:
                hw += 1
            elif h_g < a_g:
                aw += 1
            else:
                dr += 1
        if hh:
            set_field(env, "h2h.matches",   hh, sample_size=len(hh))
            set_field(env, "h2h.home_wins", hw, sample_size=len(hh))
            set_field(env, "h2h.away_wins", aw, sample_size=len(hh))
            set_field(env, "h2h.draws",     dr, sample_size=len(hh))
            set_field(env, "h2h.sample",    len(hh), sample_size=len(hh))

    # ── 5) Odds pass-through ──────────────────────────────────────────
    odds_raw = match.get("odds")
    if isinstance(odds_raw, dict):
        for market, sels in odds_raw.items():
            if sels is None:
                continue
            set_field(env, f"odds.{str(market)}", sels)
    # Some legacy writers use list-of-bookmakers shape — skip silently.

    codes: list[str] = []
    if env["home"] or env["away"] or env["h2h"] or env["odds"]:
        codes.append(RC_MAPPING_OK if (env["home"] and env["away"]) else RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
