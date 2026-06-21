"""FIX-4 — Corners history fetcher with 24h Mongo cache.

Loads each team's last-N (default 15) match corners-for / corners-against
vector for the pre-match Corners Profile engine.

Cascade (per team):
  1. **Cache hit** in ``db.team_corners_history`` (24h TTL).
  2. **TheStatsAPI** (preferred, batched per-match):
       - Resolve recent match IDs via ``fetch_recent_match_ids``.
       - Per match: ``GET /football/matches/{id}/stats`` and read
         ``overview.corner_kicks.all.{home,away}``.
       - Determine which side the team was on the match via
         ``event.home_team_id`` / ``event.away_team_id``.
  3. **API-Sports fallback** (when TheStatsAPI returns < min_sample):
       - Use ``af.fixtures_last_n`` to list recent fixtures.
       - Per fixture: ``GET /fixtures/statistics?fixture={id}``, find
         ``Corner Kicks``.

Cache:
  * Document key: ``(team_id, source)``.
  * TTL: 24h. Stale entries are ignored and refreshed.

Fail-soft everywhere — every step swallows exceptions and accumulates
``reason_codes`` so the caller can render the debug breakdown.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import api_football as af
from .external_sources import (
    thestatsapi_client as ts_client,
)
from .external_sources.thestatsapi_client import _request as _ts_request

log = logging.getLogger("services.football_corners_history")

_CACHE_COLL_NAME = "team_corners_history"
_CACHE_TTL_HOURS = 24
_DEFAULT_N = 15
_TS_PER_MATCH_TIMEOUT = 6.0
_AS_PER_FIXTURE_TIMEOUT = 6.0
_MAX_CONCURRENT_FETCHES = 4


# ─────────────────────────────────────────────────────────────────────
#   Cache helpers
# ─────────────────────────────────────────────────────────────────────

def _is_fresh(doc: dict | None) -> bool:
    if not isinstance(doc, dict):
        return False
    ts = doc.get("fetched_at")
    if not ts:
        return False
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        else:
            return False
    except Exception:
        return False
    age = datetime.now(timezone.utc) - dt
    return age < timedelta(hours=_CACHE_TTL_HOURS)


async def _read_cache(db, team_id, source: str) -> Optional[list[dict]]:
    if db is None or not team_id:
        return None
    try:
        coll = getattr(db, _CACHE_COLL_NAME, None)
        if coll is None:
            return None
        doc = await coll.find_one({"team_id": str(team_id), "source": source})
        if not _is_fresh(doc):
            return None
        history = (doc or {}).get("history") or []
        return list(history) if isinstance(history, list) else None
    except Exception as exc:  # fail-soft
        log.debug("[corners_history] cache read failed team=%s: %s", team_id, exc)
        return None


async def _write_cache(db, team_id, source: str, history: list[dict]) -> None:
    if db is None or not team_id:
        return
    try:
        coll = getattr(db, _CACHE_COLL_NAME, None)
        if coll is None:
            return
        await coll.update_one(
            {"team_id": str(team_id), "source": source},
            {"$set": {
                "team_id":    str(team_id),
                "source":     source,
                "history":    history,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as exc:
        log.debug("[corners_history] cache write failed team=%s: %s", team_id, exc)


# ─────────────────────────────────────────────────────────────────────
#   TheStatsAPI path
# ─────────────────────────────────────────────────────────────────────

def _extract_corners_pair(stats_payload: dict) -> tuple[Optional[int], Optional[int]]:
    """Extract ``(home_corners, away_corners)`` from a TheStatsAPI
    ``/football/matches/{id}/stats`` payload.

    Supports both shapes observed live:
      * ``data.overview.corner_kicks.all.{home,away}`` (current, primary)
      * ``data.attack.corners.all.{home,away}`` (alternate / future)
    """
    if not isinstance(stats_payload, dict):
        return (None, None)
    data = stats_payload.get("data") if isinstance(stats_payload.get("data"), dict) else stats_payload

    candidates = []
    ov = data.get("overview") if isinstance(data, dict) else None
    if isinstance(ov, dict):
        candidates.append(ov.get("corner_kicks"))
    atk = data.get("attack") if isinstance(data, dict) else None
    if isinstance(atk, dict):
        candidates.append(atk.get("corners"))

    for block in candidates:
        if not isinstance(block, dict):
            continue
        allb = block.get("all") if isinstance(block.get("all"), dict) else block
        if not isinstance(allb, dict):
            continue
        h = allb.get("home")
        a = allb.get("away")
        if h is not None or a is not None:
            try:
                return (int(h) if h is not None else None,
                        int(a) if a is not None else None)
            except (TypeError, ValueError):
                continue
    return (None, None)


def _extract_event_team_ids(stats_payload: dict) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(stats_payload, dict):
        return (None, None)
    data = stats_payload.get("data") if isinstance(stats_payload.get("data"), dict) else stats_payload
    if not isinstance(data, dict):
        return (None, None)
    ev = data.get("event") if isinstance(data.get("event"), dict) else {}
    return (ev.get("home_team_id"), ev.get("away_team_id"))


def _resolve_match_side(match_obj: dict, team_id: str) -> Optional[str]:
    """Inspect a match listing dict and decide which side ``team_id`` was on.

    Returns ``"home"`` | ``"away"`` | ``None`` (cannot determine).
    """
    if not isinstance(match_obj, dict):
        return None
    home_tid = (
        match_obj.get("home_team_id")
        or ((match_obj.get("home_team") or {}).get("id") if isinstance(match_obj.get("home_team"), dict) else None)
    )
    away_tid = (
        match_obj.get("away_team_id")
        or ((match_obj.get("away_team") or {}).get("id") if isinstance(match_obj.get("away_team"), dict) else None)
    )
    if str(team_id) == str(home_tid):
        return "home"
    if str(team_id) == str(away_tid):
        return "away"
    return None


async def _fetch_one_thestatsapi(
    client: httpx.AsyncClient, match_id: str, team_id: str,
    side_hint: Optional[str] = None,
) -> Optional[dict]:
    """Fetch one match's corners for the given team via TheStatsAPI.

    ``side_hint`` (``"home"`` | ``"away"``) is the authoritative answer
    derived from the listing endpoint; it bypasses the brittle
    ``event.home_team_id`` lookup (which the stats endpoint sometimes
    returns as ``None``).

    Returns ``{"match_id", "corners_for", "corners_against"}`` or None
    (treated as a miss — the loop keeps going).
    """
    try:
        payload = await _ts_request(client, "GET", f"/football/matches/{match_id}/stats",
                                    timeout=_TS_PER_MATCH_TIMEOUT)
    except Exception as exc:
        log.debug("[corners_history.ts] match=%s fetch_stats failed: %s", match_id, exc)
        return None
    if not isinstance(payload, dict):
        return None
    home_c, away_c = _extract_corners_pair(payload)
    if home_c is None and away_c is None:
        return None  # match exists but no corner block

    # Prefer the side hint from the listing endpoint.
    side = side_hint
    if side is None:
        home_id, away_id = _extract_event_team_ids(payload)
        if str(team_id) == str(home_id):
            side = "home"
        elif str(team_id) == str(away_id):
            side = "away"
    if side == "home":
        return {"match_id": str(match_id),
                "corners_for": home_c, "corners_against": away_c}
    if side == "away":
        return {"match_id": str(match_id),
                "corners_for": away_c, "corners_against": home_c}
    # Unknown side mapping — skip (rather than misattribute).
    return None


async def fetch_team_corners_history_thestatsapi(
    client: Optional[httpx.AsyncClient],
    db,
    *,
    team_id: str,
    n: int = _DEFAULT_N,
) -> tuple[list[dict], list[str]]:
    """Return ``(history, reason_codes)`` for one team via TheStatsAPI."""
    reasons: list[str] = []
    if not team_id:
        reasons.append("TS_TEAM_ID_MISSING")
        return ([], reasons)
    if not ts_client.is_enabled():
        reasons.append("TS_DISABLED")
        return ([], reasons)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_TS_PER_MATCH_TIMEOUT)
    try:
        # 1. Recent matches (with side info inline).
        try:
            matches = await ts_client.fetch_recent_matches(
                team_id, n=n, client=client, status="finished",
            )
        except Exception as exc:
            log.debug("[corners_history.ts] recent_matches team=%s failed: %s", team_id, exc)
            matches = []
        if not matches:
            reasons.append("TS_NO_RECENT_MATCH_IDS")
            return ([], reasons)

        # 2. Fetch per-match stats with bounded concurrency, passing
        #    the side hint from the listing.
        sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

        async def _bounded(m):
            mid = m.get("id") or m.get("match_id")
            if not mid:
                return None
            side = _resolve_match_side(m, team_id)
            async with sem:
                return await _fetch_one_thestatsapi(client, str(mid), team_id, side_hint=side)

        results = await asyncio.gather(*(_bounded(m) for m in matches),
                                       return_exceptions=False)
        history = [r for r in results if isinstance(r, dict)]
        if not history:
            reasons.append("TS_ALL_MATCHES_NO_CORNERS")
        return (history, reasons)
    finally:
        if owns_client and client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
#   API-Sports fallback path
# ─────────────────────────────────────────────────────────────────────

def _corners_from_apisports_statistics(
    stat_payload: list | dict, team_id_int: int,
) -> tuple[Optional[int], Optional[int]]:
    """Find ``(team_corners, opponent_corners)`` in /fixtures/statistics output."""
    resp = stat_payload.get("response") if isinstance(stat_payload, dict) else stat_payload
    if not isinstance(resp, list):
        return (None, None)
    team_c = None
    opp_c = None
    for team_block in resp:
        if not isinstance(team_block, dict):
            continue
        tid = (team_block.get("team") or {}).get("id")
        stats = team_block.get("statistics") or []
        if not isinstance(stats, list):
            continue
        for s in stats:
            if (s.get("type") or "").lower() in ("corner kicks", "corners"):
                try:
                    val = int(s.get("value")) if s.get("value") is not None else None
                except (TypeError, ValueError):
                    val = None
                if tid == team_id_int:
                    team_c = val
                else:
                    opp_c = val
                break
    return (team_c, opp_c)


async def fetch_team_corners_history_apisports(
    client: Optional[httpx.AsyncClient],
    db,
    *,
    team_id: int | str,
    season: int | str | None,
    n: int = _DEFAULT_N,
    include_all_competitions: bool = False,
) -> tuple[list[dict], list[str]]:
    """Return ``(history, reason_codes)`` via API-Sports per-fixture stats.

    Sprint-D9.2 Block A — pass ``include_all_competitions=True`` for
    national-team windows so friendlies + qualifiers + tournaments are
    glued together (the legacy ``season=YEAR`` filter only surfaces
    the partidos del torneo principal, lo cual destruía L1/L5/L15 para
    selecciones del Mundial).
    """
    reasons: list[str] = []
    try:
        team_id_int = int(team_id)
    except (TypeError, ValueError):
        reasons.append("AS_TEAM_ID_INVALID")
        return ([], reasons)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_AS_PER_FIXTURE_TIMEOUT)
    try:
        try:
            fixtures = await af.fixtures_last_n(
                client, team_id_int, n=n, season=season, db=db,
                include_all_competitions=include_all_competitions,
            )
            if include_all_competitions:
                reasons.append("AS_LAST_N_GLOBAL_USED")
        except Exception as exc:
            log.debug("[corners_history.as] fixtures_last_n team=%s failed: %s",
                      team_id, exc)
            fixtures = []
        if not fixtures:
            reasons.append("AS_NO_RECENT_FIXTURES")
            return ([], reasons)

        sem = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

        async def _one(fx):
            fid = (fx.get("fixture") or {}).get("id") if isinstance(fx, dict) else None
            if not fid:
                return None
            async with sem:
                try:
                    stat = await af.fixture_statistics(client, fid, db=db)
                except Exception as exc:
                    log.debug("[corners_history.as] fid=%s stats failed: %s", fid, exc)
                    return None
            tc, oc = _corners_from_apisports_statistics(stat, team_id_int)
            if tc is None and oc is None:
                return None
            return {"match_id": str(fid), "corners_for": tc, "corners_against": oc}

        results = await asyncio.gather(*(_one(fx) for fx in fixtures),
                                       return_exceptions=False)
        history = [r for r in results if isinstance(r, dict)]
        if not history:
            reasons.append("AS_ALL_FIXTURES_NO_CORNERS")
        return (history, reasons)
    finally:
        if owns_client and client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
#   Public entry point
# ─────────────────────────────────────────────────────────────────────

async def fetch_team_corners_history(
    client: Optional[httpx.AsyncClient],
    db,
    *,
    team_id_thestatsapi: Optional[str] = None,
    team_id_apisports: Optional[int | str] = None,
    season: int | str | None = None,
    n: int = _DEFAULT_N,
    min_sample: int = 5,
    use_cache: bool = True,
    include_all_competitions: bool = False,
) -> dict[str, Any]:
    """Public, multi-provider entry point.

    Sprint-D9.2 Block A — pass ``include_all_competitions=True`` for
    national-team windows so friendlies + qualifiers + tournaments are
    glued together (the legacy ``season=YEAR`` filter only surfaced
    WC2022 matches, breaking L1/L5/L15 for selecciones).

    Returns::

        {
          "history":      [...],   # list of {"match_id","corners_for","corners_against"}
          "source":       "thestatsapi" | "api_sports" | "cache" | "none",
          "reason_codes": [...],
        }
    """
    reasons: list[str] = []

    # ── 1) Cache hit (TheStatsAPI cache key first, then API-Sports). ──
    if use_cache:
        for tid, src in (
            (team_id_thestatsapi, "thestatsapi"),
            (team_id_apisports,   "api_sports"),
        ):
            if not tid:
                continue
            cached = await _read_cache(db, tid, src)
            if cached and len(cached) >= 1:
                reasons.append(f"CORNERS_CACHE_HIT_{src.upper()}")
                return {"history": cached, "source": "cache", "reason_codes": reasons}

    # ── 2) TheStatsAPI primary. ──
    if team_id_thestatsapi:
        hist, rc = await fetch_team_corners_history_thestatsapi(
            client, db, team_id=str(team_id_thestatsapi), n=n,
        )
        reasons.extend(rc)
        if len(hist) >= min_sample:
            await _write_cache(db, team_id_thestatsapi, "thestatsapi", hist)
            return {"history": hist, "source": "thestatsapi", "reason_codes": reasons}
        # If we got SOMETHING but not enough, keep it as a partial.
        ts_partial = hist

    else:
        ts_partial = []

    # ── 3) API-Sports fallback. ──
    if team_id_apisports:
        hist_as, rc_as = await fetch_team_corners_history_apisports(
            client, db, team_id=team_id_apisports, season=season, n=n,
            include_all_competitions=include_all_competitions,
        )
        reasons.extend(rc_as)
        # Merge with TheStatsAPI partial (deduplicated by match_id).
        seen_ids = {h["match_id"] for h in ts_partial}
        merged = list(ts_partial) + [h for h in hist_as if h["match_id"] not in seen_ids]
        if merged:
            src_label = "thestatsapi" if not hist_as else (
                "api_sports" if not ts_partial else "thestatsapi+api_sports"
            )
            # Cache the better source (api_sports if it produced rows).
            cache_key_id = team_id_apisports if hist_as else team_id_thestatsapi
            cache_src = "api_sports" if hist_as else "thestatsapi"
            await _write_cache(db, cache_key_id, cache_src, merged)
            return {"history": merged, "source": src_label, "reason_codes": reasons}

    # ── 4) Nothing found. ──
    if ts_partial:
        await _write_cache(db, team_id_thestatsapi, "thestatsapi", ts_partial)
        return {"history": ts_partial, "source": "thestatsapi",
                "reason_codes": reasons}

    return {"history": [], "source": "none", "reason_codes": reasons}


# ============================================================
# Sprint-D9 Iteration-3 · V2 cascade: offline_seed first + promote
# ============================================================

async def fetch_team_corners_history_v2(
    client: Optional[httpx.AsyncClient],
    db,
    *,
    team_name: str,
    league: Optional[str] = None,
    team_id_thestatsapi: Optional[str] = None,
    team_id_apisports: Optional[int | str] = None,
    season: int | str | None = None,
    n: int = _DEFAULT_N,
    min_sample: int = 5,
    use_cache: bool = True,
    include_all_competitions: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Cascade reordenado: offline_seed primero → online sources → promote.

    Hermano de ``services.football_xg_real_client.get_team_xg_history``
    pero para corners. Acepta ``team_name`` (no requiere team_id) y usa
    el módulo de aliases compartido para resolver el equipo en el seed
    permanente.

    Pipeline::

        1. Cache TTL hit (``team_corners_history`` 24h, keyed por team_id)
           — solo si se pasa team_id_thestatsapi o team_id_apisports.
        2. **offline_seed primary** (NEW):
              * Si tiene ≥ min_sample y NO force_refresh → short-circuit.
              * Si tiene < min_sample → guardar como ``seed_partial``.
        3. Online cascade (TheStatsAPI → API-Sports) idéntico a v1.
        4. Si online OK → **promote_online_matches_to_seed** (NEW).
        5. Si online FALLA → devolver ``seed_partial`` si existe.

    Returns::

        {
          "history":      [...],
          "source":       "offline_seed" | "thestatsapi" | "api_sports"
                          | "thestatsapi+api_sports" | "cache" | "none",
          "available":    bool,
          "reason_codes": [...],
        }
    """
    reasons: list[str] = []
    tried: list[str] = []

    # 1) Cache TTL legacy (team_id keyed).
    if use_cache and not force_refresh:
        for tid, src in (
            (team_id_thestatsapi, "thestatsapi"),
            (team_id_apisports,   "api_sports"),
        ):
            if not tid:
                continue
            cached = await _read_cache(db, tid, src)
            if cached and len(cached) >= 1:
                reasons.append(f"CORNERS_CACHE_HIT_{src.upper()}")
                return {
                    "history":      cached,
                    "source":       "cache",
                    "available":    len(cached) >= min_sample,
                    "reason_codes": reasons,
                }

    # 2) offline_seed PRIMARY (Sprint-D9 Iteration-3).
    seed_partial: list[dict] = []
    seed_team_name: Optional[str] = None
    try:
        from .football_corners_offline_seed import (
            get_offline_corners_history,
        )
        seed_res = await get_offline_corners_history(
            db, team_name, league=league,
        )
        tried.append("offline_seed")
        if seed_res and seed_res.get("matches"):
            ms_seed = seed_res["matches"]
            # Adaptar al shape esperado por v1 (match_id, corners_for, corners_against)
            adapted = [{
                "match_id":         _seed_match_id(m, idx),
                "corners_for":      m.get("corners_for"),
                "corners_against":  m.get("corners_against"),
                "date":             m.get("date"),
                "opponent":         m.get("opponent"),
                "venue":            m.get("venue"),
                "season":           m.get("season"),
                "_from_seed":       True,
            } for idx, m in enumerate(ms_seed)]
            seed_team_name = seed_res.get("team_name")
            if not force_refresh and len(adapted) >= min_sample:
                reasons.append("CORNERS_OFFLINE_SEED_HIT")
                return {
                    "history":      adapted,
                    "source":       "offline_seed",
                    "available":    True,
                    "reason_codes": reasons,
                }
            seed_partial = adapted
    except Exception as _exc_seed:  # noqa: BLE001
        log.info("[corners.cascade_v2] offline_seed lookup failed: %s",
                  _exc_seed)

    # 3) Online sources (TheStatsAPI + API-Sports).
    online_matches: list[dict] = []
    chosen_source: Optional[str] = None
    if team_id_thestatsapi:
        tried.append("thestatsapi")
        hist, rc = await fetch_team_corners_history_thestatsapi(
            client, db, team_id=str(team_id_thestatsapi), n=n,
        )
        reasons.extend(rc)
        if hist:
            online_matches = hist
            chosen_source = "thestatsapi"
        elif team_id_apisports:
            ts_partial = []
    if team_id_apisports:
        tried.append("api_sports")
        hist_as, rc_as = await fetch_team_corners_history_apisports(
            client, db, team_id=team_id_apisports, season=season, n=n,
            include_all_competitions=include_all_competitions,
        )
        reasons.extend(rc_as)
        if hist_as:
            # Merge con TheStatsAPI partial (dedupe por match_id).
            seen_ids = {h["match_id"] for h in online_matches}
            merged_online = list(online_matches) + [
                h for h in hist_as if h["match_id"] not in seen_ids
            ]
            online_matches = merged_online
            chosen_source = (
                "thestatsapi+api_sports" if chosen_source else "api_sports"
            )

    # 4) Si online produjo data → cache TTL + promote al seed permanente.
    if online_matches:
        # Cache TTL legacy (mantiene back-compat con código que lo lee).
        cache_key_id = team_id_apisports if (
            chosen_source and "api_sports" in chosen_source
        ) else team_id_thestatsapi
        cache_src = "api_sports" if (
            chosen_source and "api_sports" in chosen_source
        ) else "thestatsapi"
        if cache_key_id:
            await _write_cache(db, cache_key_id, cache_src, online_matches)

        # Promote al seed permanente (merge inteligente).
        try:
            from .football_corners_offline_seed import (
                promote_online_matches_to_seed,
            )
            # Adaptar el shape v1 → shape seed (necesita date + opponent).
            promote_payload = [{
                "date":            h.get("date") or h.get("event_date"),
                "opponent":        h.get("opponent") or h.get("opponent_name"),
                "corners_for":     h.get("corners_for"),
                "corners_against": h.get("corners_against"),
                "venue":           h.get("venue"),
                "season":          h.get("season") or str(season) if season else None,
                "match_id":        h.get("match_id"),
            } for h in online_matches if (h.get("date") or h.get("event_date"))]
            if promote_payload:
                promo = await promote_online_matches_to_seed(
                    db, team_name=team_name, league=league or "Unknown",
                    matches=promote_payload,
                    underlying_source=chosen_source,
                )
                log.info("[corners.cascade_v2] promote→seed: %s",
                          promo.get("action"))
        except Exception as _exc_promote:  # noqa: BLE001
            log.info("[corners.cascade_v2] promote→seed failed: %s",
                      _exc_promote)

        return {
            "history":      online_matches,
            "source":       chosen_source or "online",
            "available":    len(online_matches) >= min_sample,
            "reason_codes": reasons,
        }

    # 5) Online falló → usar seed_partial como fallback.
    if seed_partial:
        reasons.append("CORNERS_OFFLINE_SEED_FALLBACK")
        return {
            "history":      seed_partial,
            "source":       "offline_seed",
            "available":    len(seed_partial) >= min_sample,
            "reason_codes": reasons,
        }

    reasons.append("CORNERS_ALL_SOURCES_FAILED")
    return {
        "history":      [],
        "source":       "none",
        "available":    False,
        "reason_codes": reasons,
    }


def _seed_match_id(m: dict, idx: int) -> str:
    """Genera un match_id sintético para matches venidos del seed
    (no provienen de un proveedor con match_id real)."""
    explicit = m.get("match_id")
    if explicit:
        return str(explicit)
    return f"seed:{(m.get('date') or '')}:{(m.get('opponent') or '')}:{idx}"


__all__ = [
    "fetch_team_corners_history",
    "fetch_team_corners_history_thestatsapi",
    "fetch_team_corners_history_apisports",
    "fetch_team_corners_history_v2",
]
