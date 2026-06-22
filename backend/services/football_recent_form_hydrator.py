"""Sprint-F99.4 · Recent-form hydrator (opt-in, fail-soft).

Bridge IO layer between the four fixture-history sources and the pure
``football_recent_form_consolidator``. The hydrator:

  1. Reads ``football_team_recent_fixtures_seed`` (Mongo).
  2. Best-effort calls TheSportsDB / SofaScore / TheStatsAPI when the
     wrapper raws are already attached to the match (no new IO is
     started here — we reuse what the existing hydrators produced).
  3. Calls the pure consolidator with all four lists.
  4. Attaches the consolidated payload to ``match["_recent_form_consolidated_raw"]``.

Strict binding:
  * Fail-soft: never raises. Any failure degrades to ``False`` + trace.
  * Opt-in: ``ENABLE_F99_RECENT_FORM_EXTENDER`` (default off).
  * Telemetry: ``match[TRACE_KEY]["recent_form_consolidated"]``.
  * The raw payload is INTERNAL — the editorial / UI never see it.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

FLAG_ENV_VAR = "ENABLE_F99_RECENT_FORM_EXTENDER"
TRACE_KEY    = "football_data_enrichment_source_trace"
SOURCE_KEY   = "recent_form_consolidated"
RAW_KEY      = "_recent_form_consolidated_raw"


def is_enabled() -> bool:
    raw = os.environ.get(FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _team_name(match: dict, side: str) -> str:
    if not isinstance(match, dict):
        return ""
    block = match.get(f"{side}_team")
    if isinstance(block, dict):
        n = block.get("name")
        if n:
            return str(n)
    if isinstance(block, str):
        return block
    flat = match.get(f"{side}_team_name")
    return str(flat) if flat else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_team(name: str) -> str:
    import unicodedata as _u
    s = "".join(c for c in _u.normalize("NFKD", str(name))
                 if not _u.combining(c))
    return " ".join(s.lower().strip().split())


def _record_trace(match: dict, payload: dict) -> None:
    if not isinstance(match, dict):
        return
    trace = match.get(TRACE_KEY)
    if not isinstance(trace, dict):
        trace = {}
        match[TRACE_KEY] = trace
    trace[SOURCE_KEY] = payload


async def _read_seed(db: Any, team_norm: str) -> list[dict]:
    """Read ``football_team_recent_fixtures_seed`` for the given team_norm."""
    if db is None or not team_norm:
        return []
    try:
        doc = await db["football_team_recent_fixtures_seed"].find_one(
            {"team_norm": team_norm},
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[f99.4_hydrator] seed read failed for %s: %s", team_norm, exc)
        return []
    if not isinstance(doc, dict):
        return []
    matches = doc.get("matches") or doc.get("recent") or []
    if not isinstance(matches, list):
        return []
    # The seed shape (from football_national_team_seed) is::
    #   { matches: [{fixture_id, date, league, team_scored, team_conceded, is_home}, ...]}
    out: list[dict] = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        out.append({
            "source_id":      m.get("fixture_id"),
            "kickoff_utc":    m.get("date"),
            "competition_name": m.get("league"),
            "venue":          "home" if m.get("is_home") else "away",
            # Note: seed does not expose the opponent name. The
            # consolidator falls back to identity via composite-key.
            "opponent_name":  m.get("opponent_name") or m.get("opponent"),
            "goals_for":      m.get("team_scored")  or m.get("goals_for"),
            "goals_against":  m.get("team_conceded") or m.get("goals_against"),
        })
    return out


def _extract_sofascore_rows(match: dict, side: str) -> list[dict]:
    """Pull ``home_form``/``away_form`` from the existing SofaScore raw."""
    if not isinstance(match, dict):
        return []
    wrapper = match.get("_sofascore_raw")
    if not isinstance(wrapper, dict):
        return []
    key = "home_form" if side == "home" else "away_form"
    rows = wrapper.get(key) or []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    team_perspective_norm = _normalize_team(_team_name(match, side))
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Determine perspective from home team norm only.
        home_norm = _normalize_team(r.get("home_team"))
        is_home = (home_norm == team_perspective_norm)
        opp_name = r.get("away_team") if is_home else r.get("home_team")
        try:
            hs = int(r.get("home_score")) if r.get("home_score") is not None else None
            as_ = int(r.get("away_score")) if r.get("away_score") is not None else None
        except (TypeError, ValueError):
            hs, as_ = None, None
        if hs is None and as_ is None:
            continue
        gf, ga = (hs, as_) if is_home else (as_, hs)
        # Pull per-team stats when SofaScore stats-enriched.
        team_stats = r.get("home_stats") if is_home else r.get("away_stats")
        team_stats = team_stats if isinstance(team_stats, dict) else {}
        out.append({
            "source_id":        r.get("event_id"),
            "kickoff_utc":      r.get("date"),
            "venue":            "home" if is_home else "away",
            "opponent_name":    opp_name,
            "goals_for":        gf,
            "goals_against":    ga,
            "xg_for":           team_stats.get("xg"),
            "shots":            team_stats.get("shots"),
            "shots_on_target":  team_stats.get("shots_on_target"),
            "possession":       team_stats.get("possession"),
            "corners_for":      team_stats.get("corners"),
        })
    return out


def _extract_thestatsapi_rows(match: dict, side: str) -> list[dict]:
    if not isinstance(match, dict):
        return []
    payload = match.get("_thestatsapi_raw") or match.get("_thestatsapi_enrichment")
    if not isinstance(payload, dict):
        return []
    key = "home_recent_fixtures" if side == "home" else "away_recent_fixtures"
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
    return out


def _extract_thesportsdb_rows(match: dict, side: str) -> list[dict]:
    if not isinstance(match, dict):
        return []
    payload = match.get("_thesportsdb_raw")
    if not isinstance(payload, dict):
        return []
    key = "home_recent_fixtures" if side == "home" else "away_recent_fixtures"
    rows = payload.get(key) or []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
    return out


async def hydrate_match_recent_form(
    match: Any,
    db: Any,
    *,
    sport: str = "football",
) -> bool:
    """Consolidate recent form for both teams and attach the result.

    Returns ``True`` iff a non-empty consolidated payload was attached.

    The hydrator NEVER raises. Telemetry is always written.
    """
    if not isinstance(match, dict):
        return False
    if isinstance(match.get(RAW_KEY), dict):
        return True  # idempotent

    if not is_enabled():
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     "feature_flag_off",
            "checked_at": _now_iso(),
        })
        return False

    if sport != "football":
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     f"sport_not_supported:{sport}",
            "checked_at": _now_iso(),
        })
        return False

    home = _team_name(match, "home")
    away = _team_name(match, "away")
    if not home or not away:
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     "missing_team_names",
            "checked_at": _now_iso(),
        })
        return False

    home_norm = _normalize_team(home)
    away_norm = _normalize_team(away)

    # Lazy import to keep the hydrator import-light.
    try:
        from .football_recent_form_consolidator import consolidate_recent_form
    except Exception as exc:  # noqa: BLE001
        log.debug("[f99.4_hydrator] consolidator import failed: %s", exc)
        _record_trace(match, {
            "attempted":  True,
            "status":     "NO_DATA",
            "reason":     "import_error",
            "checked_at": _now_iso(),
        })
        return False

    consolidated: dict = {}
    counts: dict = {}
    for side, team_norm in (("home", home_norm), ("away", away_norm)):
        seed_rows = []
        try:
            seed_rows = await _read_seed(db, team_norm)
        except Exception as exc:  # noqa: BLE001
            log.debug("[f99.4_hydrator] seed read crash (%s): %s", team_norm, exc)
        sofa_rows = _extract_sofascore_rows(match, side)
        tsa_rows  = _extract_thestatsapi_rows(match, side)
        tsdb_rows = _extract_thesportsdb_rows(match, side)
        sources = {
            "seed":        seed_rows,
            "sofascore":   sofa_rows,
            "thestatsapi": tsa_rows,
            "thesportsdb": tsdb_rows,
        }
        try:
            payload = consolidate_recent_form(sources, team_norm=team_norm)
        except Exception as exc:  # noqa: BLE001
            log.debug("[f99.4_hydrator] consolidate crash (%s): %s", team_norm, exc)
            payload = {
                "recent":        [],
                "windows":       {"l5": [], "l15": []},
                "partial_flags": {
                    "l5_is_partial": True, "l15_is_partial": True,
                    "l5_insufficient": True, "l15_insufficient": True,
                },
                "summary":       {"team_norm": team_norm, "sample_size_total": 0,
                                   "counts_per_source": {}},
            }
        consolidated[side] = payload
        counts[side] = (payload.get("summary") or {}).get("sample_size_total", 0)

    has_any = any(counts.values())
    status  = "RICH" if (counts.get("home", 0) >= 5 and counts.get("away", 0) >= 5) else \
              "PARTIAL" if has_any else "NO_DATA"

    _record_trace(match, {
        "attempted":         True,
        "status":            status,
        "sample_sizes":      counts,
        "sources_per_side": {
            "home": (consolidated.get("home", {}).get("summary") or {}).get("counts_per_source", {}),
            "away": (consolidated.get("away", {}).get("summary") or {}).get("counts_per_source", {}),
        },
        "checked_at":        _now_iso(),
    })

    if has_any:
        match[RAW_KEY] = consolidated
        return True
    return False


__all__ = [
    "FLAG_ENV_VAR",
    "TRACE_KEY",
    "SOURCE_KEY",
    "RAW_KEY",
    "is_enabled",
    "hydrate_match_recent_form",
]
