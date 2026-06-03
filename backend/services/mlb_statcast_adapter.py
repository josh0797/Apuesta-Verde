"""MLB Statcast Adapter — hybrid pybaseball + Bright Data + TheStatsAPI.

This module is the **single entry point** the MLB engine uses to obtain
advanced metrics (xERA / xwOBA / barrel% / hard-hit% / exit velocity /
whiff% / chase% / wRC+ / OPS). Callers never import pybaseball,
brightdata, or TheStatsAPI directly — that's the whole point of the
adapter pattern. This keeps the engine portable, dependency-light, and
easy to test.

Architecture (high-level)::

         caller
           │
           ▼
    get_mlb_advanced_profile()
           │
           ├── 1. Cache (Mongo `external_source_cache`)        ← hit → return
           │
           ├── 2. fetch_with_pybaseball()    ← lazy import; tolerated absence
           │
           ├── 3. fetch_with_brightdata()    ← only if needed (gaps)
           │
           ├── 4. fetch_with_thestatsapi()   ← complementary (OPS, wRC+, etc.)
           │
           └── 5. merge_advanced_sources()   ← priority-aware merge
                       │
                       └── write cache → return

Public surface:

    async def get_mlb_advanced_profile(...) -> dict
    async def fetch_with_pybaseball(...)     -> dict
    async def fetch_with_brightdata(...)     -> dict
    async def fetch_with_thestatsapi(...)    -> dict
    def is_pybaseball_available()            -> bool
    def normalize_mlb_advanced_payload(...)  -> dict
    def merge_advanced_sources(...)          -> dict
    async def read_mlb_advanced_cache(...)   -> dict | None
    async def write_mlb_advanced_cache(...)  -> None

All paths are fail-soft: a partial failure NEVER raises; every helper
returns a dict (possibly empty) so the orchestrator branch can keep
running. The MLB engine MUST NOT crash because Statcast is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Feature flags / env
# ─────────────────────────────────────────────────────────────────────
def _env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if not v:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def is_adapter_enabled() -> bool:
    return _env_flag("ENABLE_MLB_STATCAST_ADAPTER", default=False)


def is_pybaseball_enabled() -> bool:
    return _env_flag("ENABLE_PYBASEBALL_ENRICHMENT", default=True)


def is_brightdata_enabled() -> bool:
    return _env_flag("ENABLE_BRIGHTDATA_STATCAST_FALLBACK", default=False)


def is_thestatsapi_baseball_enabled() -> bool:
    return _env_flag("ENABLE_THE_STATS_API_BASEBALL", default=True)


def cache_ttl_seconds(role: str | None) -> int:
    """Per-role TTL (player season 24h, team season 12h, live 5m)."""
    hours = _env_int("MLB_ADVANCED_STATS_CACHE_HOURS", 24)
    if role == "team":
        return max(60, hours * 3600 // 2)   # team → half the player TTL
    if role == "live":
        return 5 * 60
    return hours * 3600


def timeout_seconds() -> int:
    return _env_int("MLB_ADVANCED_STATS_TIMEOUT_SECONDS", 12)


# ─────────────────────────────────────────────────────────────────────
# Normalised payload skeleton
# ─────────────────────────────────────────────────────────────────────
_PITCHER_FIELDS = (
    "xera", "era", "whip",
    "xwoba_allowed", "xba_allowed", "xslg_allowed",
    "hard_hit_pct_allowed", "barrel_pct_allowed", "avg_exit_velocity_allowed",
    "whiff_pct", "chase_pct",
    "k_pct", "bb_pct", "ground_ball_pct",
)
_BATTING_FIELDS = (
    "ops", "wrc_plus",
    "xwoba", "xba", "xslg",
    "hard_hit_pct", "barrel_pct", "avg_exit_velocity",
    "k_pct", "bb_pct",
)
_TEAM_FIELDS = (
    "team_ops", "team_wrc_plus", "team_xwoba",
    "team_hard_hit_pct", "team_barrel_pct", "team_avg_exit_velocity",
    "runs_per_game",
)


def _empty_section(fields: tuple[str, ...]) -> dict:
    return {f: None for f in fields}


def _is_section_nonempty(section: dict | None) -> bool:
    if not section:
        return False
    return any(v is not None for v in section.values())


def normalize_mlb_advanced_payload(
    *,
    available: bool = False,
    source: str = "unknown",
    player_id: Any = None,
    player_name: str | None = None,
    team_id: Any = None,
    team_name: str | None = None,
    season: int | None = None,
    role: str | None = None,
    pitcher: dict | None = None,
    batting: dict | None = None,
    team: dict | None = None,
    data_quality: str | None = None,
    source_status: dict | None = None,
    sources_consulted: list | None = None,
    field_sources: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Return the canonical normalized payload shape used everywhere.

    ``data_quality`` is auto-computed when not provided:
      * "strong"   → all 3 (when applicable) sections non-empty.
      * "partial"  → at least one section non-empty.
      * "missing"  → all empty / available=False.
    """
    pitcher = {**_empty_section(_PITCHER_FIELDS), **(pitcher or {})} if (pitcher is not None or role == "pitcher") else None
    batting = {**_empty_section(_BATTING_FIELDS), **(batting or {})} if (batting is not None or role == "batter") else None
    team_block = {**_empty_section(_TEAM_FIELDS), **(team or {})} if (team is not None or role == "team") else None

    if data_quality is None:
        sections = [s for s in (pitcher, batting, team_block) if s is not None]
        if not available or not sections:
            data_quality = "missing"
        else:
            # "strong" requires that **most** of the fields in **every**
            # relevant section be populated (≥ 60%). This avoids labelling
            # a single field as a strong profile.
            def _coverage(sec: dict) -> float:
                if not sec:
                    return 0.0
                filled = sum(1 for v in sec.values() if v is not None)
                return filled / max(1, len(sec))

            coverages = [_coverage(s) for s in sections]
            if coverages and all(c >= 0.6 for c in coverages):
                data_quality = "strong"
            elif any(c > 0 for c in coverages):
                data_quality = "partial"
            else:
                data_quality = "missing"

    out = {
        "available":        available and data_quality != "missing",
        "source":           source,
        "player_id":        player_id,
        "player_name":      player_name,
        "team_id":          team_id,
        "team_name":        team_name,
        "season":           season,
        "role":             role,
        "data_quality":     data_quality,
        "source_status":    source_status or {},
        "sources_consulted": list(sources_consulted or []),
        "field_sources":    field_sources or {},
        "fetched_at":       datetime.now(timezone.utc).isoformat(),
    }
    if pitcher is not None:
        out["pitcher"] = pitcher
    if batting is not None:
        out["batting"] = batting
    if team_block is not None:
        out["team"] = team_block
    if extra:
        out["extra"] = extra
    return out


# ─────────────────────────────────────────────────────────────────────
# Cache helpers (Mongo `external_source_cache`)
# ─────────────────────────────────────────────────────────────────────
_CACHE_COLL = "external_source_cache"
_CACHE_SRC = "mlb_advanced"


def _build_cache_key(
    *, role: str | None, season: int | None,
    player_id: Any, player_name: str | None,
    team_id: Any, team_name: str | None,
) -> str:
    ident = (
        str(player_id) if player_id is not None
        else (player_name or "")
    ) or (
        str(team_id) if team_id is not None
        else (team_name or "")
    ) or "unknown"
    return f"mlb_advanced:{role or 'any'}:{season or 'any'}:{ident.lower().replace(' ', '_')}"


async def read_mlb_advanced_cache(db, cache_key: str) -> dict | None:
    """Return cached payload if still fresh (TTL is enforced by caller)."""
    if db is None or not cache_key:
        return None
    try:
        doc = await db[_CACHE_COLL].find_one({
            "source": _CACHE_SRC, "endpoint": "profile", "key": cache_key,
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("[mlb_adv_cache] read failed: %s", exc)
        return None
    if not isinstance(doc, dict):
        return None
    ts_str = doc.get("_cached_at")
    ttl = doc.get("_ttl_seconds")
    try:
        cached_at = datetime.fromisoformat((ts_str or "").replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
    except Exception:
        return None
    if ttl and age > ttl:
        return None
    return doc.get("data")


async def write_mlb_advanced_cache(
    db, cache_key: str, payload: dict, ttl_seconds: int,
) -> None:
    if db is None or not cache_key or not isinstance(payload, dict):
        return
    try:
        await db[_CACHE_COLL].update_one(
            {"source": _CACHE_SRC, "endpoint": "profile", "key": cache_key},
            {"$set": {
                "source": _CACHE_SRC,
                "endpoint": "profile",
                "key": cache_key,
                "data": payload,
                "_cached_at": datetime.now(timezone.utc).isoformat(),
                "_ttl_seconds": int(ttl_seconds),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[mlb_adv_cache] write failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────
# pybaseball — lazy primary
# ─────────────────────────────────────────────────────────────────────
def is_pybaseball_available() -> bool:
    """True iff `import pybaseball` would succeed RIGHT NOW.

    We deliberately do NOT cache this so a hot-installed pybaseball
    flips the answer on the next call.
    """
    try:
        import pybaseball  # noqa: F401
        return True
    except Exception:
        return False


def _normalise_float(value: Any) -> float | None:
    """Coerce to float when possible, else None. Treats NaN / empty as None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _pct_from_ratio(value: Any) -> float | None:
    """If `value` ∈ [0, 1] interpret as fraction → percentage (×100).
    Otherwise return as-is. Useful because pybaseball sometimes ships
    ratios and sometimes already-multiplied percentages."""
    f = _normalise_float(value)
    if f is None:
        return None
    if 0.0 <= f <= 1.0:
        return round(f * 100.0, 2)
    return round(f, 2)


async def fetch_with_pybaseball(
    *, player_id: Any = None, player_name: str | None = None,
    team_id: Any = None, team_name: str | None = None,
    season: int | None = None, role: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Best-effort pybaseball fetch. Fully fail-soft.

    Returns ``{}`` on:
      * library not installed
      * `ENABLE_PYBASEBALL_ENRICHMENT=false`
      * any internal exception
      * timeout exhausted

    Returns a normalized payload (single section populated based on role)
    on success. Field set depends on what's actually in the response —
    callers must `.get(...)` for everything.
    """
    if not is_pybaseball_enabled():
        return {"source_status": "skipped", "_reason": "feature_flag_off"}
    if not is_pybaseball_available():
        return {"source_status": "skipped", "_reason": "not_installed"}

    timeout = timeout or timeout_seconds()

    def _blocking_fetch() -> dict:
        try:
            import pybaseball as pb   # type: ignore
        except Exception as exc:
            return {"source_status": "failed", "_reason": f"import: {exc}"}
        try:
            if role == "pitcher":
                return _pyb_fetch_pitcher(pb, player_id, player_name, season)
            if role == "batter":
                return _pyb_fetch_batter(pb, player_id, player_name, season)
            if role == "team":
                return _pyb_fetch_team(pb, team_id, team_name, season)
            return {"source_status": "skipped", "_reason": "unknown_role"}
        except Exception as exc:  # noqa: BLE001
            log.warning("[pybaseball] fetch raised: %s", exc)
            return {"source_status": "failed", "_reason": str(exc)}

    try:
        return await asyncio.wait_for(asyncio.to_thread(_blocking_fetch), timeout=timeout)
    except asyncio.TimeoutError:
        return {"source_status": "failed", "_reason": "timeout"}


def _pyb_fetch_pitcher(pb, player_id, player_name, season) -> dict:
    """Pull pitcher Statcast aggregates. Returns shape::

        {"source_status": "success",
         "pitcher": {xera, era, whip, xwoba_allowed, ...}}
    """
    season = season or datetime.now(timezone.utc).year
    out: dict = {}

    # Resolve player id if only name was provided.
    resolved_id = player_id
    if resolved_id is None and player_name:
        try:
            parts = (player_name or "").strip().split()
            if len(parts) >= 2:
                lookup = pb.playerid_lookup(parts[-1], parts[0])
                if lookup is not None and not lookup.empty:
                    resolved_id = int(lookup.iloc[0]["key_mlbam"])
        except Exception as exc:
            log.debug("[pybaseball] playerid_lookup failed: %s", exc)

    pitcher_block: dict = {}
    if resolved_id is not None:
        try:
            df = pb.statcast_pitcher(
                start_dt=f"{season}-03-01", end_dt=f"{season}-11-30",
                player_id=int(resolved_id),
            )
            if df is not None and not df.empty:
                pitcher_block.update({
                    "xwoba_allowed": _normalise_float(df.get("estimated_woba_using_speedangle", []).mean() if "estimated_woba_using_speedangle" in df else None),
                    "xba_allowed":   _normalise_float(df.get("estimated_ba_using_speedangle", []).mean() if "estimated_ba_using_speedangle" in df else None),
                    "avg_exit_velocity_allowed": _normalise_float(df["launch_speed"].mean() if "launch_speed" in df else None),
                    "hard_hit_pct_allowed": _pct_from_ratio(
                        (df["launch_speed"] >= 95).mean() if "launch_speed" in df else None
                    ),
                    "barrel_pct_allowed":   _pct_from_ratio(
                        (df.get("barrel", df.get("is_barrel", []))).mean() if ("barrel" in df or "is_barrel" in df) else None
                    ),
                })
        except Exception as exc:
            log.debug("[pybaseball] statcast_pitcher failed: %s", exc)
        # Pitching summary (whiff%, chase%, K%, BB%, ERA-style).
        try:
            df_sum = pb.pitching_stats(season, season)
            if df_sum is not None and not df_sum.empty:
                row = df_sum[df_sum.get("xMLBAMID", df_sum.get("IDfg", -1)) == int(resolved_id)]
                if row.empty and player_name:
                    row = df_sum[df_sum.get("Name", "") == player_name]
                if not row.empty:
                    r = row.iloc[0]
                    pitcher_block.update({
                        "era":         _normalise_float(r.get("ERA")),
                        "xera":        _normalise_float(r.get("xERA")),
                        "whip":        _normalise_float(r.get("WHIP")),
                        "k_pct":       _pct_from_ratio(r.get("K%")),
                        "bb_pct":      _pct_from_ratio(r.get("BB%")),
                        "whiff_pct":   _pct_from_ratio(r.get("Whiff%") or r.get("SwStr%")),
                        "chase_pct":   _pct_from_ratio(r.get("O-Swing%")),
                        "ground_ball_pct": _pct_from_ratio(r.get("GB%")),
                    })
        except Exception as exc:
            log.debug("[pybaseball] pitching_stats failed: %s", exc)

    if pitcher_block and any(v is not None for v in pitcher_block.values()):
        out["pitcher"] = pitcher_block
        out["source_status"] = "success"
        out["player_id_resolved"] = resolved_id
    else:
        out["source_status"] = "failed"
        out["_reason"] = "no_rows"
    return out


def _pyb_fetch_batter(pb, player_id, player_name, season) -> dict:
    """Pull batter Statcast + season aggregates."""
    season = season or datetime.now(timezone.utc).year
    out: dict = {}
    resolved_id = player_id

    if resolved_id is None and player_name:
        try:
            parts = player_name.strip().split()
            if len(parts) >= 2:
                lookup = pb.playerid_lookup(parts[-1], parts[0])
                if lookup is not None and not lookup.empty:
                    resolved_id = int(lookup.iloc[0]["key_mlbam"])
        except Exception:
            pass

    block: dict = {}
    if resolved_id is not None:
        try:
            df = pb.statcast_batter(
                start_dt=f"{season}-03-01", end_dt=f"{season}-11-30",
                player_id=int(resolved_id),
            )
            if df is not None and not df.empty:
                block.update({
                    "xwoba": _normalise_float(df.get("estimated_woba_using_speedangle", []).mean() if "estimated_woba_using_speedangle" in df else None),
                    "xba":   _normalise_float(df.get("estimated_ba_using_speedangle", []).mean() if "estimated_ba_using_speedangle" in df else None),
                    "avg_exit_velocity": _normalise_float(df["launch_speed"].mean() if "launch_speed" in df else None),
                    "hard_hit_pct":      _pct_from_ratio((df["launch_speed"] >= 95).mean() if "launch_speed" in df else None),
                    "barrel_pct":        _pct_from_ratio(
                        (df.get("barrel", df.get("is_barrel", []))).mean() if ("barrel" in df or "is_barrel" in df) else None
                    ),
                })
        except Exception as exc:
            log.debug("[pybaseball] statcast_batter failed: %s", exc)
        try:
            df_sum = pb.batting_stats(season, season)
            if df_sum is not None and not df_sum.empty:
                row = df_sum[df_sum.get("xMLBAMID", df_sum.get("IDfg", -1)) == int(resolved_id)]
                if row.empty and player_name:
                    row = df_sum[df_sum.get("Name", "") == player_name]
                if not row.empty:
                    r = row.iloc[0]
                    block.update({
                        "ops":      _normalise_float(r.get("OPS")),
                        "wrc_plus": _normalise_float(r.get("wRC+")),
                        "k_pct":    _pct_from_ratio(r.get("K%")),
                        "bb_pct":   _pct_from_ratio(r.get("BB%")),
                    })
        except Exception as exc:
            log.debug("[pybaseball] batting_stats failed: %s", exc)

    if block and any(v is not None for v in block.values()):
        out["batting"] = block
        out["source_status"] = "success"
        out["player_id_resolved"] = resolved_id
    else:
        out["source_status"] = "failed"
        out["_reason"] = "no_rows"
    return out


def _pyb_fetch_team(pb, team_id, team_name, season) -> dict:
    """Pull team-aggregate offensive stats."""
    season = season or datetime.now(timezone.utc).year
    out: dict = {}
    block: dict = {}
    try:
        df = pb.team_batting(season, season)
        if df is not None and not df.empty:
            # match by abbreviated team if id is short, else by name
            row = None
            if team_name:
                row = df[df.get("Team", "").astype(str).str.lower() == team_name.lower()]
                if row.empty:
                    row = df[df.get("Team", "").astype(str).str.contains(team_name, case=False, na=False)]
            if row is None or row.empty:
                row = None
            if row is not None and not row.empty:
                r = row.iloc[0]
                games = _normalise_float(r.get("G")) or 1
                runs = _normalise_float(r.get("R"))
                block.update({
                    "team_ops":      _normalise_float(r.get("OPS")),
                    "team_wrc_plus": _normalise_float(r.get("wRC+")),
                    "runs_per_game": (runs / games) if (runs and games) else None,
                })
    except Exception as exc:
        log.debug("[pybaseball] team_batting failed: %s", exc)

    if block and any(v is not None for v in block.values()):
        out["team"] = block
        out["source_status"] = "success"
    else:
        out["source_status"] = "failed"
        out["_reason"] = "no_rows"
    return out


# ─────────────────────────────────────────────────────────────────────
# Bright Data fallback (stub-friendly)
# ─────────────────────────────────────────────────────────────────────
async def fetch_with_brightdata(
    *, player_id: Any = None, player_name: str | None = None,
    team_id: Any = None, team_name: str | None = None,
    season: int | None = None, role: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Try Bright Data Web Unlocker against Baseball Savant / FanGraphs.

    Stub-friendly: if `ENABLE_BRIGHTDATA_STATCAST_FALLBACK=false` or
    Bright Data is not configured, returns ``{}`` with
    ``source_status="skipped"``. The actual scraping module lives in
    ``services.external_sources.mlb_statcast_brightdata`` and is
    imported lazily — its absence does NOT break this adapter.
    """
    if not is_brightdata_enabled():
        return {"source_status": "skipped", "_reason": "feature_flag_off"}
    try:
        from .external_sources import mlb_statcast_brightdata as bd
    except Exception as exc:
        log.debug("[brightdata_statcast] module unavailable: %s", exc)
        return {"source_status": "skipped", "_reason": f"module_missing: {exc}"}

    timeout = timeout or timeout_seconds()
    try:
        return await asyncio.wait_for(
            bd.fetch_advanced(
                player_id=player_id, player_name=player_name,
                team_id=team_id, team_name=team_name,
                season=season, role=role,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {"source_status": "failed", "_reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.debug("[brightdata_statcast] fetch raised: %s", exc)
        return {"source_status": "failed", "_reason": str(exc)}


# ─────────────────────────────────────────────────────────────────────
# TheStatsAPI complement (baseball)
# ─────────────────────────────────────────────────────────────────────
async def fetch_with_thestatsapi(
    *, player_id: Any = None, player_name: str | None = None,
    team_id: Any = None, team_name: str | None = None,
    season: int | None = None, role: str | None = None,
    timeout: int | None = None,
) -> dict:
    """Complementary fetch from TheStatsAPI for OPS / wRC+ / team stats.

    TheStatsAPI does NOT ship Statcast metrics (xERA / barrel / etc.),
    so this is ONLY used to fill OPS / wRC+ / season-level batting stats
    when pybaseball missed them. Fully fail-soft.
    """
    if not is_thestatsapi_baseball_enabled():
        return {"source_status": "skipped", "_reason": "feature_flag_off"}
    try:
        from .external_sources import thestatsapi_client as ts
        import httpx
    except Exception:
        return {"source_status": "skipped", "_reason": "module_missing"}

    if not ts.is_enabled():
        return {"source_status": "skipped", "_reason": "disabled_or_no_key"}

    timeout = timeout or timeout_seconds()

    async def _do_fetch() -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if role == "team":
                tid = team_id
                if tid is None:
                    return {"source_status": "failed", "_reason": "no_team_id"}
                raw = await ts.fetch_team_stats(client, tid, sport="baseball", season=season)
                team_block = _ts_team_to_block(raw) if raw else {}
                if team_block:
                    return {"team": team_block, "source_status": "success"}
                return {"source_status": "failed", "_reason": "empty"}
            if role in ("pitcher", "batter"):
                pid = player_id
                if pid is None:
                    return {"source_status": "failed", "_reason": "no_player_id"}
                raw = await ts.fetch_player_stats(client, pid, sport="baseball", season=season)
                if role == "pitcher":
                    block = _ts_player_to_pitcher_block(raw)
                    return ({"pitcher": block, "source_status": "success"}
                            if block else {"source_status": "failed", "_reason": "empty"})
                block = _ts_player_to_batter_block(raw)
                return ({"batting": block, "source_status": "success"}
                        if block else {"source_status": "failed", "_reason": "empty"})
            return {"source_status": "skipped", "_reason": "unknown_role"}

    try:
        return await asyncio.wait_for(_do_fetch(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"source_status": "failed", "_reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        log.debug("[thestatsapi_baseball] fetch raised: %s", exc)
        return {"source_status": "failed", "_reason": str(exc)}


def _ts_team_to_block(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        "team_ops":              _normalise_float(raw.get("ops")),
        "team_wrc_plus":         _normalise_float(raw.get("wrc_plus") or raw.get("wRC+")),
        "team_xwoba":            _normalise_float(raw.get("xwoba")),
        "team_hard_hit_pct":     _pct_from_ratio(raw.get("hard_hit_pct") or raw.get("hard_hit_percent")),
        "team_barrel_pct":       _pct_from_ratio(raw.get("barrel_pct") or raw.get("barrel_percent")),
        "team_avg_exit_velocity": _normalise_float(raw.get("avg_exit_velocity") or raw.get("avg_ev")),
        "runs_per_game":         _normalise_float(raw.get("runs_per_game") or raw.get("rpg")),
    }


def _ts_player_to_pitcher_block(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        "era":           _normalise_float(raw.get("era")),
        "xera":          _normalise_float(raw.get("xera")),
        "whip":          _normalise_float(raw.get("whip")),
        "xwoba_allowed": _normalise_float(raw.get("xwoba_allowed")),
        "xba_allowed":   _normalise_float(raw.get("xba_allowed")),
        "k_pct":         _pct_from_ratio(raw.get("k_pct")),
        "bb_pct":        _pct_from_ratio(raw.get("bb_pct")),
        "whiff_pct":     _pct_from_ratio(raw.get("whiff_pct")),
        "chase_pct":     _pct_from_ratio(raw.get("chase_pct")),
    }


def _ts_player_to_batter_block(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        "ops":               _normalise_float(raw.get("ops")),
        "wrc_plus":          _normalise_float(raw.get("wrc_plus") or raw.get("wRC+")),
        "xwoba":             _normalise_float(raw.get("xwoba")),
        "xba":               _normalise_float(raw.get("xba")),
        "hard_hit_pct":      _pct_from_ratio(raw.get("hard_hit_pct")),
        "barrel_pct":        _pct_from_ratio(raw.get("barrel_pct")),
        "avg_exit_velocity": _normalise_float(raw.get("avg_exit_velocity")),
        "k_pct":             _pct_from_ratio(raw.get("k_pct")),
        "bb_pct":            _pct_from_ratio(raw.get("bb_pct")),
    }


# ─────────────────────────────────────────────────────────────────────
# Merge — priority-aware
# ─────────────────────────────────────────────────────────────────────
# Per-field priority order. Sources earlier in the tuple win.
_FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    # Statcast-pure metrics → pybaseball > brightdata > thestatsapi
    "pitcher.xera":                      ("pybaseball", "brightdata", "thestatsapi"),
    "pitcher.xwoba_allowed":             ("pybaseball", "brightdata", "thestatsapi"),
    "pitcher.xba_allowed":               ("pybaseball", "brightdata", "thestatsapi"),
    "pitcher.hard_hit_pct_allowed":      ("pybaseball", "brightdata"),
    "pitcher.barrel_pct_allowed":        ("pybaseball", "brightdata"),
    "pitcher.avg_exit_velocity_allowed": ("pybaseball", "brightdata"),
    "pitcher.whiff_pct":                 ("pybaseball", "brightdata", "thestatsapi"),
    "pitcher.chase_pct":                 ("pybaseball", "brightdata", "thestatsapi"),
    "pitcher.ground_ball_pct":           ("pybaseball", "brightdata"),
    "batting.xwoba":                     ("pybaseball", "brightdata", "thestatsapi"),
    "batting.xba":                       ("pybaseball", "brightdata", "thestatsapi"),
    "batting.hard_hit_pct":              ("pybaseball", "brightdata"),
    "batting.barrel_pct":                ("pybaseball", "brightdata"),
    "batting.avg_exit_velocity":         ("pybaseball", "brightdata"),
    "team.team_xwoba":                   ("pybaseball", "brightdata", "thestatsapi"),
    "team.team_hard_hit_pct":            ("pybaseball", "brightdata"),
    "team.team_barrel_pct":              ("pybaseball", "brightdata"),
    "team.team_avg_exit_velocity":       ("pybaseball", "brightdata"),
}
# Conventional metrics — TheStatsAPI may be fresher → prefer it first.
_FIELD_PRIORITY_CONVENTIONAL = ("thestatsapi", "pybaseball", "brightdata")
for _f in ("pitcher.era", "pitcher.whip", "pitcher.k_pct", "pitcher.bb_pct",
           "batting.ops", "batting.wrc_plus", "batting.k_pct", "batting.bb_pct",
           "team.team_ops", "team.team_wrc_plus", "team.runs_per_game"):
    _FIELD_PRIORITY[_f] = _FIELD_PRIORITY_CONVENTIONAL


def _resolve_field(field: str, sources_data: dict[str, dict]) -> tuple[Any, str | None]:
    """Walk the priority chain; return (value, source_label)."""
    priority = _FIELD_PRIORITY.get(field) or ("pybaseball", "brightdata", "thestatsapi")
    section, key = field.split(".", 1)
    for src in priority:
        payload = sources_data.get(src) or {}
        block = payload.get(section) or {}
        v = block.get(key)
        if v is not None:
            return v, src
    return None, None


def merge_advanced_sources(
    pybaseball_payload: dict | None = None,
    brightdata_payload: dict | None = None,
    thestatsapi_payload: dict | None = None,
    *, role: str | None = None,
) -> dict:
    """Combine the three providers into a single normalized payload.

    Each input is the raw output from `fetch_with_*` — they may
    contain none, one, or more of `pitcher`/`batting`/`team` blocks.

    The output payload follows the contract documented at the top of
    this module. `field_sources` records which provider each non-null
    value came from for traceability.
    """
    sources_data = {
        "pybaseball":  pybaseball_payload or {},
        "brightdata":  brightdata_payload or {},
        "thestatsapi": thestatsapi_payload or {},
    }
    field_sources: dict[str, str] = {}
    pitcher: dict | None = None
    batting: dict | None = None
    team_block: dict | None = None
    sources_consulted: list[str] = []

    if role == "pitcher" or any((p or {}).get("pitcher") for p in sources_data.values()):
        pitcher = {}
        for f in _PITCHER_FIELDS:
            v, src = _resolve_field(f"pitcher.{f}", sources_data)
            pitcher[f] = v
            if src:
                field_sources[f"pitcher.{f}"] = src
    if role == "batter" or any((p or {}).get("batting") for p in sources_data.values()):
        batting = {}
        for f in _BATTING_FIELDS:
            v, src = _resolve_field(f"batting.{f}", sources_data)
            batting[f] = v
            if src:
                field_sources[f"batting.{f}"] = src
    if role == "team" or any((p or {}).get("team") for p in sources_data.values()):
        team_block = {}
        for f in _TEAM_FIELDS:
            v, src = _resolve_field(f"team.{f}", sources_data)
            team_block[f] = v
            if src:
                field_sources[f"team.{f}"] = src

    for src_name, payload in sources_data.items():
        status = (payload or {}).get("source_status")
        if status == "success":
            sources_consulted.append(src_name)

    source_label = "merged" if len(sources_consulted) > 1 else (
        sources_consulted[0] if sources_consulted else "unknown"
    )

    return normalize_mlb_advanced_payload(
        available=bool(field_sources),
        source=source_label,
        role=role,
        pitcher=pitcher, batting=batting, team=team_block,
        source_status={k: (v or {}).get("source_status", "skipped")
                       for k, v in sources_data.items()},
        sources_consulted=sources_consulted,
        field_sources=field_sources,
    )


# ─────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────
async def get_mlb_advanced_profile(
    *,
    db=None,
    player_id: Any = None,
    player_name: str | None = None,
    team_id: Any = None,
    team_name: str | None = None,
    season: int | None = None,
    role: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """Public entry point. Cache → fetch → merge → cache → return.

    Returns the canonical normalized payload (never raises).
    """
    if not is_adapter_enabled():
        return normalize_mlb_advanced_payload(
            available=False, source="disabled", role=role,
            source_status={"adapter": "disabled"},
        )

    cache_key = _build_cache_key(
        role=role, season=season,
        player_id=player_id, player_name=player_name,
        team_id=team_id, team_name=team_name,
    )

    # Cache lookup
    if not force_refresh:
        cached = await read_mlb_advanced_cache(db, cache_key)
        if cached:
            cached = dict(cached)
            cached.setdefault("source_status", {})
            cached["source_status"] = {**cached["source_status"], "cache": "hit"}
            return cached

    # Parallel fetch from all 3 sources (each is internally fail-soft).
    try:
        pyb_task = fetch_with_pybaseball(
            player_id=player_id, player_name=player_name,
            team_id=team_id, team_name=team_name,
            season=season, role=role,
        )
        bd_task = fetch_with_brightdata(
            player_id=player_id, player_name=player_name,
            team_id=team_id, team_name=team_name,
            season=season, role=role,
        )
        ts_task = fetch_with_thestatsapi(
            player_id=player_id, player_name=player_name,
            team_id=team_id, team_name=team_name,
            season=season, role=role,
        )
        pyb_res, bd_res, ts_res = await asyncio.gather(
            pyb_task, bd_task, ts_task, return_exceptions=True,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        log.warning("[mlb_adv_adapter] gather failed: %s", exc)
        return normalize_mlb_advanced_payload(
            available=False, source="error", role=role,
            source_status={"_error": str(exc)},
        )

    def _safe(r):
        return r if isinstance(r, dict) else {}

    merged = merge_advanced_sources(
        pybaseball_payload=_safe(pyb_res),
        brightdata_payload=_safe(bd_res),
        thestatsapi_payload=_safe(ts_res),
        role=role,
    )
    # Stamp identifiers + names so callers don't need to re-attach them.
    merged["player_id"]   = player_id
    merged["player_name"] = player_name
    merged["team_id"]     = team_id
    merged["team_name"]   = team_name
    merged["season"]      = season
    merged["source_status"]["cache"] = "miss"

    # Persist (only when there's actual data).
    if merged.get("available"):
        await write_mlb_advanced_cache(
            db, cache_key, merged, ttl_seconds=cache_ttl_seconds(role),
        )
    return merged
