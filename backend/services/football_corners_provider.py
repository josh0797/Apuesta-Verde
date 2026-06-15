"""Phase F82 — Football corners provider (cascade).

Phase F82.1 — Non-blocking enrichment + timeout protection
==========================================================
The corners cascade is split in TWO tiers:

  * **FAST tier** (default, runs INLINE in the ingest pipeline):
      1. API-Sports fixture statistics (already in match_doc).
      2. TheStatsAPI snapshot (already in match_doc).
      → never makes HTTP calls. Max ~1ms per match.

  * **EXTERNAL tier** (opt-in, runs ONLY when explicit flag is set):
      1. 365Scores via scrape.do.
      → makes HTTP calls; bounded by ``FOOTBALL_365SCORES_TIMEOUT_MS``.

Feature flags (env-driven, fail-safe defaults)::

    ENABLE_INLINE_365SCORES_CORNERS      = False  # never inline by default
    ENABLE_BACKGROUND_365SCORES_CORNERS  = True
    FOOTBALL_CORNERS_FAST_TIMEOUT_MS     = 1200
    FOOTBALL_365SCORES_TIMEOUT_MS        = 3500

The fast tier is what ``data_ingestion._enrich_football`` calls. The
external tier is only reached when an out-of-band job (or a UI action)
explicitly opts in via ``enrich_match_corners(..., allow_external=True)``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

log = logging.getLogger(__name__)

RC_APISPORTS         = 'CORNERS_FROM_APISPORTS'
RC_365SCORES         = 'CORNERS_FROM_365SCORES'
RC_THESTATSAPI       = 'CORNERS_FROM_THESTATSAPI'
RC_UNAVAILABLE       = 'CORNERS_UNAVAILABLE'
RC_NO_API_SPORTS     = 'CORNERS_NO_APISPORTS_STATS'
RC_NO_365_ID         = 'SCORE365_ID_MISSING'
RC_365_BLOCKED       = 'SCORE365_BLOCKED_OR_EMPTY'
RC_365_TIMEOUT       = 'SCORE365_FETCH_TIMEOUT'
RC_365_SKIPPED_INLINE = 'SCORE365_SKIPPED_INLINE_FLAG_DISABLED'
RC_NO_THESTATSAPI    = 'CORNERS_NO_THESTATSAPI_BLOCK'
RC_PROVIDER_BREAKER  = 'CORNERS_PROVIDER_BREAKER_OPEN'
# Phase F82.1-adjust — when fast tier has no data but background
# enrichment is enabled, mark the snapshot as deferred so the UI can
# offer the "Actualizar córners con 365Scores" button.
RC_DEFERRED          = 'CORNERS_EXTERNAL_ENRICHMENT_DEFERRED'
STATUS_PENDING_BG    = 'PENDING_BACKGROUND_ENRICHMENT'

# Phase F83-update — explicit cascade order under feature flag.
RC_NO_PROVIDER_AVAILABLE = 'NO_CORNERS_PROVIDER_AVAILABLE'
RC_THESTATSAPI_EMPTY     = 'THESTATSAPI_CORNERS_EMPTY'
RC_APISPORTS_NO_STATS    = 'CORNERS_NOT_IN_FIXTURE_STATS'

# Phase F93 — TotalCorner + FootyStats reason codes / source tokens.
RC_TOTALCORNER           = 'CORNERS_FROM_TOTALCORNER'
RC_FOOTYSTATS            = 'CORNERS_FROM_FOOTYSTATS'
RC_TOTALCORNER_EMPTY     = 'TOTALCORNER_CORNERS_EMPTY'
RC_FOOTYSTATS_EMPTY      = 'FOOTYSTATS_CORNERS_EMPTY'


def is_f93_cascade_order_enabled() -> bool:
    """When True the cascade uses the F93 spec:
    TheStatsAPI → API-Sports → TotalCorner → 365Scores → FootyStats.

    Default ``True`` because F93 supersedes F83 once shipped. Operators
    can still pin to the legacy F82.2 order by exporting
    ``ENABLE_F93_CASCADE_ORDER=false``.
    """
    return _flag_bool('ENABLE_F93_CASCADE_ORDER', True)


def is_f83_cascade_order_enabled() -> bool:
    """When True the cascade runs as: API-Sports → 365Scores → TheStatsAPI.

    When False (default) the legacy F82.2 order applies: TheStatsAPI →
    API-Sports → 365Scores. Toggled via ``ENABLE_F83_CASCADE_ORDER``.
    """
    return _flag_bool('ENABLE_F83_CASCADE_ORDER', False)


# ── Feature flags (env, fail-safe defaults) ──────────────────────────
def _flag_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _flag_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def is_inline_365scores_enabled() -> bool:
    return _flag_bool('ENABLE_INLINE_365SCORES_CORNERS', False)


def is_background_365scores_enabled() -> bool:
    return _flag_bool('ENABLE_BACKGROUND_365SCORES_CORNERS', True)


def score365_timeout_seconds() -> float:
    return _flag_int('FOOTBALL_365SCORES_TIMEOUT_MS', 3500) / 1000.0


def corners_fast_timeout_seconds() -> float:
    return _flag_int('FOOTBALL_CORNERS_FAST_TIMEOUT_MS', 1200) / 1000.0


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _extract_apisports_corners(match_doc: dict) -> Optional[dict]:
    """Look at match_doc['live_stats'] (API-Sports) for 'Corner Kicks'.

    API-Sports shape (live_stats normalized) often is::

        {'home_stats': {'Corner Kicks': '6', 'Shots': '12', ...},
         'away_stats': {'Corner Kicks': '3', ...}}
    """
    live = match_doc.get('live_stats') or {}
    if not isinstance(live, dict):
        return None
    hs = live.get('home_stats') or {}
    as_ = live.get('away_stats') or {}
    if not isinstance(hs, dict) or not isinstance(as_, dict):
        return None

    def _find(blob: dict, keys: tuple) -> Optional[int]:
        for k, v in blob.items():
            if not isinstance(k, str):
                continue
            low = k.lower()
            for needle in keys:
                if needle in low:
                    return _safe_int(v)
        return None

    keys = ('corner kick', 'corners', 'córner', 'corner')
    home = _find(hs, keys)
    away = _find(as_, keys)
    if home is None and away is None:
        return None
    return {
        'source': 'api_sports',
        'home':   home,
        'away':   away,
        'total':  (home or 0) + (away or 0) if (home is not None and away is not None) else None,
    }


def _extract_thestatsapi_corners(match_doc: dict) -> Optional[dict]:
    """Look for corners inside ``_thestatsapi_enrichment`` / ``thestatsapi_snapshot``."""
    for key in ('_thestatsapi_enrichment', 'thestatsapi_snapshot',
                 'football_data_enrichment'):
        blob = match_doc.get(key)
        if not isinstance(blob, dict):
            continue
        corners = blob.get('corners') or blob.get('corner_stats')
        if isinstance(corners, dict):
            home = _safe_int(corners.get('home'))
            away = _safe_int(corners.get('away'))
            total = _safe_int(corners.get('total'))
            if home is None and away is None and total is None:
                continue
            if total is None and home is not None and away is not None:
                total = home + away
            return {
                'source': 'thestatsapi',
                'home':   home,
                'away':   away,
                'total':  total,
            }
    return None


async def _extract_365scores_corners(
    client, match_doc: dict, *, allow_name_resolver: bool = True,
    timeout_s: Optional[float] = None,
) -> tuple[Optional[dict], list[str]]:
    """Try 365Scores. Returns (payload or None, reason_codes).

    Phase F82.1 — wrapped in an outer ``asyncio.wait_for`` so a slow
    scrape.do response cannot block the ingest pipeline.
    """
    codes: list[str] = []
    try:
        from .external_sources import score365_client as _s365
    except Exception as exc:  # noqa: BLE001
        log.debug('score365_client unavailable: %s', exc)
        return None, [RC_PROVIDER_BREAKER]

    async def _do_fetch() -> tuple[Optional[dict], list[str]]:
        inner_codes: list[str] = []
        game_id, matchup_id = _s365.resolve_game_id_from_match_doc(match_doc)
        if not game_id and allow_name_resolver:
            home_name = (match_doc.get('home_team') or {}).get('name') if isinstance(
                match_doc.get('home_team'), dict) else None
            away_name = (match_doc.get('away_team') or {}).get('name') if isinstance(
                match_doc.get('away_team'), dict) else None
            kickoff = match_doc.get('kickoff_iso') or match_doc.get('date')
            if home_name and away_name and kickoff:
                try:
                    game_id = await _s365.resolve_game_id_by_date_and_names(
                        home_name, away_name, kickoff,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug('365scores name resolver failed: %s', exc)
                    game_id = None
        if not game_id:
            inner_codes.append(RC_NO_365_ID)
            return None, inner_codes
        try:
            raw = await _s365.fetch_game_stats(client, game_id)
            if not raw:
                raw = await _s365.fetch_game_data(client, game_id, matchup_id)
        except Exception as exc:  # noqa: BLE001
            log.debug('365scores fetch failed for %s: %s', game_id, exc)
            inner_codes.append(RC_365_BLOCKED)
            return None, inner_codes
        normalised = _s365.normalize_365scores_match_stats(raw)
        if not normalised.get('available'):
            inner_codes.append(RC_365_BLOCKED)
            return None, inner_codes
        return {
            'source': '365scores',
            'home':   normalised['home'].get('corners'),
            'away':   normalised['away'].get('corners'),
            'total':  normalised.get('total_corners'),
            '_raw_provider': normalised,
        }, inner_codes

    effective_timeout = timeout_s if timeout_s is not None else score365_timeout_seconds()
    try:
        return await asyncio.wait_for(_do_fetch(), timeout=effective_timeout)
    except asyncio.TimeoutError:
        log.info('[corners_provider] 365scores timed out after %.1fs', effective_timeout)
        codes.append(RC_365_TIMEOUT)
        return None, codes
    except Exception as exc:  # noqa: BLE001
        log.debug('365scores outer wrapper failed: %s', exc)
        codes.append(RC_365_BLOCKED)
        return None, codes


def _confidence_from(source: str, home: Optional[int], away: Optional[int]) -> str:
    if home is None or away is None:
        return 'LIMITED'
    if source == 'api_sports':
        return 'STRONG'
    if source == '365scores':
        return 'USABLE'
    return 'LIMITED'


async def enrich_match_corners(client, db, match_doc: dict,
                                *, allow_external: Optional[bool] = None) -> dict:
    """Resolve corners for a match using the cascade.

    Phase F82.1 — by default this only uses **FAST** sources (API-Sports
    + TheStatsAPI already in ``match_doc``). External 365Scores fetches
    are gated by:
      * the ``allow_external`` argument (if explicitly set), OR
      * the ``ENABLE_INLINE_365SCORES_CORNERS`` env flag.

    Background jobs / UI actions can call this with
    ``allow_external=True`` to opt-in.

    Always returns a dict with ``available`` boolean and ``reason_codes``.
    Persists to multiple locations for compat.
    """
    if not isinstance(match_doc, dict):
        return {'available': False, 'reason_codes': [RC_UNAVAILABLE]}

    fid = match_doc.get('match_id')
    codes: list[str] = []
    if allow_external is None:
        allow_external = is_inline_365scores_enabled()

    # Phase F82.2 — reorder: TheStatsAPI → API-Sports → 365Scores.
    # TheStatsAPI is the new baseline because API-Sports does not cover
    # every league (esp. lower divisions / minor regions), while
    # TheStatsAPI provides a broader baseline for free.
    #
    # 1) TheStatsAPI (FAST — already in match_doc, no HTTP)
    tsa = _extract_thestatsapi_corners(match_doc)
    if tsa is not None:
        payload = {
            'available':     True,
            'source':        'thestatsapi',
            'current_match': {'home': tsa['home'], 'away': tsa['away'], 'total': tsa['total']},
            'confidence':    _confidence_from('thestatsapi', tsa['home'], tsa['away']),
            'reason_codes':  [RC_THESTATSAPI],
        }
        _persist(match_doc, payload)
        log.info('[corners_fast] fixture=%s source=thestatsapi total=%s home=%s away=%s',
                 fid, tsa['total'], tsa['home'], tsa['away'])
        return payload
    codes.append(RC_NO_THESTATSAPI)

    # 2) API-Sports (FAST — no HTTP, but only when statistics are
    # already on the match doc; many fixtures lack them).
    aps = _extract_apisports_corners(match_doc)
    if aps is not None and (aps['home'] is not None or aps['away'] is not None):
        payload = {
            'available':     True,
            'source':        aps['source'],
            'current_match': {'home': aps['home'], 'away': aps['away'], 'total': aps['total']},
            'confidence':    _confidence_from('api_sports', aps['home'], aps['away']),
            'reason_codes':  [RC_APISPORTS],
        }
        _persist(match_doc, payload)
        log.info('[corners_fast] fixture=%s source=api_sports total=%s home=%s away=%s',
                 fid, aps['total'], aps['home'], aps['away'])
        return payload
    codes.append(RC_NO_API_SPORTS)

    # 3) 365Scores (SLOW — HTTP via scrape.do). Only if allowed.
    if not allow_external:
        codes.append(RC_365_SKIPPED_INLINE)
        # Phase F82.1-adjust — when background enrichment is enabled and
        # the fast tier yielded no corners, mark the snapshot as
        # PENDING_BACKGROUND_ENRICHMENT so the UI can show the manual
        # refresh button. Otherwise behave like the original F82.1 path
        # (plain unavailable).
        if is_background_365scores_enabled():
            payload = {
                'available':    False,
                'status':       STATUS_PENDING_BG,
                'reason_codes': codes + [RC_DEFERRED],
            }
        else:
            payload = {
                'available':    False,
                'reason_codes': codes + [RC_UNAVAILABLE],
            }
        _persist(match_doc, payload)
        log.info('[corners_provider] fixture=%s unavailable (fast tier) reason=%s',
                 fid, ','.join(payload['reason_codes']) or RC_UNAVAILABLE)
        return payload

    s365_payload, s365_codes = await _extract_365scores_corners(client, match_doc)
    codes.extend(s365_codes)
    if s365_payload is not None:
        payload = {
            'available':     True,
            'source':        '365scores',
            'current_match': {'home': s365_payload['home'], 'away': s365_payload['away'],
                                'total': s365_payload['total']},
            'confidence':    _confidence_from('365scores', s365_payload['home'], s365_payload['away']),
            'reason_codes':  [RC_365SCORES],
            '_raw_provider': s365_payload.get('_raw_provider'),
        }
        _persist(match_doc, payload)
        log.info('[corners_provider] fixture=%s source=365scores total=%s home=%s away=%s',
                 fid, s365_payload['total'], s365_payload['home'], s365_payload['away'])
        return payload

    payload = {
        'available':    False,
        'reason_codes': codes + [RC_UNAVAILABLE],
    }
    _persist(match_doc, payload)
    log.info('[corners_provider] fixture=%s unavailable reason=%s', fid,
             ','.join(codes) or RC_UNAVAILABLE)
    return payload


async def enrich_match_corners_fast(client, db, match_doc: dict) -> dict:
    """Convenience wrapper — corners enrichment WITHOUT external HTTP.

    This is the call used from ``data_ingestion._enrich_football``.
    Guaranteed to never block on scrape.do / TheStatsAPI HTTP fetches.
    """
    return await enrich_match_corners(client, db, match_doc, allow_external=False)


async def enrich_match_corners_external(client, db, match_doc: dict) -> dict:
    """Opt-in wrapper for background jobs / UI actions.

    Forces the 365Scores cascade regardless of the env flag. Still bounded
    by ``FOOTBALL_365SCORES_TIMEOUT_MS``.
    """
    return await enrich_match_corners(client, db, match_doc, allow_external=True)


def _persist(match_doc: dict, payload: dict) -> None:
    """Persist to all 3 compat locations."""
    # top-level
    match_doc['corners_snapshot'] = payload
    # football_data_enrichment
    fde = match_doc.setdefault('football_data_enrichment', {})
    if isinstance(fde, dict):
        fde['corners'] = payload
    # thestatsapi_snapshot (legacy alias)
    ts_snap = match_doc.get('thestatsapi_snapshot')
    if isinstance(ts_snap, dict):
        ts_snap['corners'] = payload


__all__ = [
    'enrich_match_corners',
    'enrich_match_corners_fast',
    'enrich_match_corners_external',
    'is_inline_365scores_enabled',
    'is_background_365scores_enabled',
    'is_f83_cascade_order_enabled',
    'is_f93_cascade_order_enabled',
    'score365_timeout_seconds',
    'corners_fast_timeout_seconds',
    'RC_APISPORTS', 'RC_365SCORES', 'RC_THESTATSAPI', 'RC_UNAVAILABLE',
    'RC_NO_API_SPORTS', 'RC_NO_365_ID', 'RC_365_BLOCKED', 'RC_365_TIMEOUT',
    'RC_365_SKIPPED_INLINE', 'RC_NO_THESTATSAPI', 'RC_PROVIDER_BREAKER',
    'RC_DEFERRED', 'STATUS_PENDING_BG',
    'RC_NO_PROVIDER_AVAILABLE', 'RC_THESTATSAPI_EMPTY', 'RC_APISPORTS_NO_STATS',
    'RC_TOTALCORNER', 'RC_FOOTYSTATS',
    'RC_TOTALCORNER_EMPTY', 'RC_FOOTYSTATS_EMPTY',
    # Phase F83-update
    'debug_corners_cascade', 'enrich_match_corners_f83',
]


# ─────────────────────────────────────────────────────────────────────
# Phase F83-update / F93 — UI-grade cascade + debug helper
# ─────────────────────────────────────────────────────────────────────
# Maps a reason_code (whatever stage) to a Spanish user-facing message.
_F83_USER_MESSAGES: dict[str, str] = {
    RC_NO_365_ID:              "No se pudo cargar córners: falta ID de 365Scores.",
    'SCORE365_ID_MISSING':     "No se pudo cargar córners: falta ID de 365Scores.",
    'SCRAPEDO_TOKEN_MISSING':  "No se pudo cargar córners: Scrape.do no tiene token configurado.",
    'SCRAPEDO_BREAKER_OPEN':   "No se pudo cargar córners: Scrape.do está pausado temporalmente.",
    'SCRAPEDO_HTTP_ERROR':     "No se pudo cargar córners: 365Scores no respondió correctamente.",
    'SCRAPEDO_TIMEOUT':        "No se pudo cargar córners: la solicitud a 365Scores tardó demasiado.",
    'SCRAPEDO_EMPTY_BODY':     "No se pudo cargar córners: 365Scores respondió pero sin contenido.",
    'SCRAPEDO_EXCEPTION':      "No se pudo cargar córners: error de transporte con Scrape.do.",
    'SCORE365_BLOCKED_OR_FORBIDDEN': "No se pudo cargar córners: 365Scores bloqueó o no devolvió la página.",
    'SCORE365_STATS_EMPTY':    "No se pudo cargar córners: la página cargó, pero no contiene estadísticas.",
    'SCORE365_CORNERS_NOT_FOUND': "No se pudo cargar córners: se encontraron estadísticas, pero no córners.",
    'SCORE365_JSON_PARSE_FAILED': "No se pudo cargar córners: el formato de la respuesta de 365Scores no es válido.",
    RC_THESTATSAPI_EMPTY:      "TheStatsAPI no tiene córners para este partido.",
    RC_APISPORTS_NO_STATS:     "API-Sports no devolvió estadísticas de córners en este fixture.",
    RC_NO_PROVIDER_AVAILABLE:  "No hay datos confiables de córners para este partido.",
    'TOTALCORNER_URL_MISSING':       "TotalCorner no tiene una URL de partido conocida.",
    'TOTALCORNER_BLOCKED_OR_FORBIDDEN': "TotalCorner bloqueó la solicitud o devolvió un error.",
    'TOTALCORNER_STATS_EMPTY':       "TotalCorner cargó la página pero no contiene estadísticas.",
    'TOTALCORNER_CORNERS_NOT_FOUND': "TotalCorner cargó la página pero no incluye córners.",
    'TOTALCORNER_HTML_PARSE_FAILED': "TotalCorner devolvió HTML pero no se pudo analizar.",
    'FOOTYSTATS_URL_MISSING':        "FootyStats no tiene una URL de partido conocida.",
    'FOOTYSTATS_BLOCKED_OR_FORBIDDEN': "FootyStats bloqueó la solicitud o devolvió un error.",
    'FOOTYSTATS_STATS_EMPTY':        "FootyStats cargó la página pero no contiene estadísticas.",
    'FOOTYSTATS_CORNERS_NOT_FOUND':  "FootyStats cargó la página pero no incluye córners.",
    'FOOTYSTATS_HTML_PARSE_FAILED':  "FootyStats devolvió HTML pero no se pudo analizar.",
}


def _f83_user_message(reason_code: Optional[str]) -> str:
    return _F83_USER_MESSAGES.get(
        reason_code or '',
        "No se pudieron cargar los córners en este partido.",
    )


def _f83_check_thestatsapi(match_doc: dict) -> dict:
    """Run the TheStatsAPI probe (no HTTP) → structured entry."""
    tsa = _extract_thestatsapi_corners(match_doc)
    if tsa is None:
        return {
            "provider":     "thestatsapi",
            "transport":    "match_doc",
            "available":    False,
            "stage":        "READ_MATCH_DOC",
            "reason_code":  RC_THESTATSAPI_EMPTY,
            "message_user": _f83_user_message(RC_THESTATSAPI_EMPTY),
            "retryable":    False,
        }
    return {
        "provider":     "thestatsapi",
        "transport":    "match_doc",
        "available":    True,
        "stage":        "READ_MATCH_DOC",
        "data":         {"home": tsa["home"], "away": tsa["away"], "total": tsa["total"]},
        "reason_code":  RC_THESTATSAPI,
        "confidence":   _confidence_from("thestatsapi", tsa["home"], tsa["away"]),
    }


def _f83_check_apisports(match_doc: dict) -> dict:
    """Run the API-Sports probe (no HTTP) → structured entry."""
    aps = _extract_apisports_corners(match_doc)
    if aps is None or (aps["home"] is None and aps["away"] is None):
        return {
            "provider":     "api_sports",
            "transport":    "match_doc",
            "available":    False,
            "stage":        "READ_MATCH_DOC",
            "reason_code":  RC_APISPORTS_NO_STATS,
            "message_user": _f83_user_message(RC_APISPORTS_NO_STATS),
            "retryable":    False,
        }
    return {
        "provider":     "api_sports",
        "transport":    "match_doc",
        "available":    True,
        "stage":        "READ_MATCH_DOC",
        "data":         {"home": aps["home"], "away": aps["away"], "total": aps["total"]},
        "reason_code":  RC_APISPORTS,
        "confidence":   _confidence_from("api_sports", aps["home"], aps["away"]),
    }


async def _f83_check_365scores(match_doc: dict, *, timeout_s: float) -> dict:
    """Run the 365Scores probe via the new scrape.do client.

    Order of resolution:
      1. ID resolution (fail fast with SCORE365_ID_MISSING if absent).
      2. Try the JSON stats endpoint (cheap, no rendering).
      3. If JSON path fails, attempt the rendered match-page URL.
      4. Parse / normalize → structured entry.
    """
    try:
        from .external_sources import score365_scrapedo_client as s365
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":     "365scores",
            "transport":    "scrape_do",
            "available":    False,
            "stage":        "MODULE_IMPORT",
            "reason_code":  "SCORE365_SCRAPEDO_MODULE_MISSING",
            "message_user": _f83_user_message(None),
            "message_debug": f"score365_scrapedo_client import failed: {exc}",
            "retryable":    False,
        }

    ids = s365.extract_365scores_ids(match_doc)
    if not ids["available"]:
        return {
            "provider":      "365scores",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "ID_RESOLUTION",
            "reason_code":   s365.RC_ID_MISSING,
            "message_user":  _f83_user_message(s365.RC_ID_MISSING),
            "message_debug": "Missing external_ids.365scores.game_id and match_url",
            "retryable":     False,
        }

    # JSON stats endpoint first.
    if ids.get("game_id"):
        stats_res = await s365.fetch_365scores_game_stats(
            None, ids["game_id"], ids.get("matchup_id"), timeout_s=timeout_s,
        )
        if stats_res.get("available"):
            normalised = s365.normalize_365scores_corners(stats_res["json"])
            if normalised.get("available"):
                return {
                    "provider":     "365scores",
                    "transport":    "scrape_do",
                    "available":    True,
                    "stage":        "FETCH_STATS",
                    "data":         {
                        "home":  normalised["home"].get("corners"),
                        "away":  normalised["away"].get("corners"),
                        "total": normalised.get("total_corners"),
                    },
                    "reason_code":  s365.RC_CORNERS_FOUND,
                    "confidence":   normalised.get("confidence", "USABLE"),
                    "raw_provider": normalised,
                    "ids":          ids,
                }
            # Stats came through but no corners → try the rendered page.
            stats_failure = {
                "provider":      "365scores",
                "transport":     "scrape_do",
                "available":     False,
                "stage":         "PARSE_STATS",
                "reason_code":   normalised.get("reason_code") or s365.RC_CORNERS_NOT_FOUND,
                "message_user":  _f83_user_message(normalised.get("reason_code")),
                "message_debug": normalised.get("message_debug"),
                "retryable":     True,
                "ids":           ids,
            }
        else:
            stats_failure = {
                "provider":     "365scores",
                "transport":    "scrape_do",
                "available":    False,
                "stage":        stats_res.get("stage", "FETCH_STATS"),
                "reason_code":  stats_res.get("reason_code"),
                "message_user": stats_res.get("message_user")
                                or _f83_user_message(stats_res.get("reason_code")),
                "message_debug": stats_res.get("message_debug"),
                "status_code":  stats_res.get("status_code"),
                "retryable":    stats_res.get("retryable", True),
                "ids":          ids,
            }
    else:
        stats_failure = None

    # Fall back to fetching the rendered match-page HTML.
    if ids.get("match_url"):
        page_res = await s365.fetch_365scores_match_page(
            None, ids["match_url"], timeout_s=timeout_s,
        )
        if page_res.get("available"):
            normalised = s365.parse_365scores_corners_from_html(page_res["html"])
            if normalised.get("available"):
                return {
                    "provider":     "365scores",
                    "transport":    "scrape_do",
                    "available":    True,
                    "stage":        "PARSE_HTML",
                    "data":         {
                        "home":  normalised["home"].get("corners"),
                        "away":  normalised["away"].get("corners"),
                        "total": normalised.get("total_corners"),
                    },
                    "reason_code":  s365.RC_CORNERS_FOUND,
                    "confidence":   normalised.get("confidence", "USABLE"),
                    "raw_provider": normalised,
                    "ids":          ids,
                }
            return {
                "provider":      "365scores",
                "transport":     "scrape_do",
                "available":     False,
                "stage":         "PARSE_HTML",
                "reason_code":   normalised.get("reason_code") or s365.RC_CORNERS_NOT_FOUND,
                "message_user":  _f83_user_message(normalised.get("reason_code")),
                "message_debug": normalised.get("message_debug"),
                "retryable":     True,
                "ids":           ids,
            }
        return {
            "provider":      "365scores",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         page_res.get("stage", "FETCH_PAGE"),
            "reason_code":   page_res.get("reason_code"),
            "message_user":  page_res.get("message_user")
                              or _f83_user_message(page_res.get("reason_code")),
            "message_debug": page_res.get("message_debug"),
            "status_code":   page_res.get("status_code"),
            "retryable":     page_res.get("retryable", True),
            "ids":           ids,
        }

    return stats_failure or {
        "provider":      "365scores",
        "transport":     "scrape_do",
        "available":     False,
        "stage":         "FETCH_STATS",
        "reason_code":   s365.RC_ID_MISSING,
        "message_user":  _f83_user_message(s365.RC_ID_MISSING),
        "message_debug": "No game_id nor match_url available after resolution",
        "retryable":     False,
        "ids":           ids,
    }


# ─────────────────────────────────────────────────────────────────────
# Phase F93 — TotalCorner + FootyStats probes via scrape.do
# ─────────────────────────────────────────────────────────────────────
async def _f93_check_totalcorner(match_doc: dict, *, timeout_s: float) -> dict:
    """Run the TotalCorner probe (HTTP via scrape.do) → structured entry."""
    try:
        from .external_sources import totalcorner_scrapedo_client as tc
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":      "totalcorner",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "MODULE_IMPORT",
            "reason_code":   "TOTALCORNER_MODULE_MISSING",
            "message_user":  _f83_user_message(None),
            "message_debug": f"totalcorner_scrapedo_client import failed: {exc}",
            "retryable":     False,
        }

    resolver = tc.extract_totalcorner_match_url(match_doc)
    if not resolver.get("available"):
        return {
            "provider":      "totalcorner",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "URL_RESOLUTION",
            "reason_code":   tc.RC_URL_MISSING,
            "message_user":  _f83_user_message(tc.RC_URL_MISSING),
            "message_debug": "external_ids.totalcorner.{match_id|match_url} missing",
            "retryable":     False,
            "resolver":      resolver,
        }

    page_res = await tc.fetch_totalcorner_match_page(
        None, resolver["match_url"], timeout_s=timeout_s,
    )
    if not page_res.get("available"):
        return {
            "provider":      "totalcorner",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         page_res.get("stage", "FETCH_PAGE"),
            "reason_code":   page_res.get("reason_code"),
            "message_user":  page_res.get("message_user")
                              or _f83_user_message(page_res.get("reason_code")),
            "message_debug": page_res.get("message_debug"),
            "status_code":   page_res.get("status_code"),
            "retryable":     page_res.get("retryable", True),
            "resolver":      resolver,
        }

    normalised = tc.parse_totalcorner_corners_from_html(page_res.get("html"))
    if not normalised.get("available"):
        return {
            "provider":      "totalcorner",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "PARSE_HTML",
            "reason_code":   normalised.get("reason_code") or tc.RC_CORNERS_NOT_FOUND,
            "message_user":  _f83_user_message(normalised.get("reason_code")),
            "message_debug": normalised.get("message_debug"),
            "retryable":     True,
            "resolver":      resolver,
        }

    return {
        "provider":     "totalcorner",
        "transport":    "scrape_do",
        "available":    True,
        "stage":        "PARSE_HTML",
        "data":         {
            "home":  normalised["home"].get("corners"),
            "away":  normalised["away"].get("corners"),
            "total": normalised.get("total_corners"),
        },
        "reason_code":  tc.RC_CORNERS_FOUND,
        "confidence":   normalised.get("confidence", "USABLE"),
        "raw_provider": normalised,
        "resolver":     resolver,
    }


async def _f93_check_footystats(match_doc: dict, *, timeout_s: float) -> dict:
    """Run the FootyStats probe (HTTP via scrape.do) → structured entry."""
    try:
        from .external_sources import footystats_scrapedo_client as fs
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":      "footystats",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "MODULE_IMPORT",
            "reason_code":   "FOOTYSTATS_MODULE_MISSING",
            "message_user":  _f83_user_message(None),
            "message_debug": f"footystats_scrapedo_client import failed: {exc}",
            "retryable":     False,
        }

    resolver = fs.extract_footystats_match_url(match_doc)
    if not resolver.get("available"):
        return {
            "provider":      "footystats",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "URL_RESOLUTION",
            "reason_code":   fs.RC_URL_MISSING,
            "message_user":  _f83_user_message(fs.RC_URL_MISSING),
            "message_debug": "external_ids.footystats.{match_url|slug} missing",
            "retryable":     False,
            "resolver":      resolver,
        }

    page_res = await fs.fetch_footystats_match_page(
        None, resolver["match_url"], timeout_s=timeout_s,
    )
    if not page_res.get("available"):
        return {
            "provider":      "footystats",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         page_res.get("stage", "FETCH_PAGE"),
            "reason_code":   page_res.get("reason_code"),
            "message_user":  page_res.get("message_user")
                              or _f83_user_message(page_res.get("reason_code")),
            "message_debug": page_res.get("message_debug"),
            "status_code":   page_res.get("status_code"),
            "retryable":     page_res.get("retryable", True),
            "resolver":      resolver,
        }

    normalised = fs.parse_footystats_corners_from_html(page_res.get("html"))
    if not normalised.get("available"):
        return {
            "provider":      "footystats",
            "transport":     "scrape_do",
            "available":     False,
            "stage":         "PARSE_HTML",
            "reason_code":   normalised.get("reason_code") or fs.RC_CORNERS_NOT_FOUND,
            "message_user":  _f83_user_message(normalised.get("reason_code")),
            "message_debug": normalised.get("message_debug"),
            "retryable":     True,
            "resolver":      resolver,
        }

    return {
        "provider":     "footystats",
        "transport":    "scrape_do",
        "available":    True,
        "stage":        "PARSE_HTML",
        "data":         {
            "home":  normalised["home"].get("corners"),
            "away":  normalised["away"].get("corners"),
            "total": normalised.get("total_corners"),
        },
        "reason_code":  fs.RC_CORNERS_FOUND,
        "confidence":   normalised.get("confidence", "USABLE"),
        "raw_provider": normalised,
        "resolver":     resolver,
    }


# ─────────────────────────────────────────────────────────────────────
# Cascade resolver — picks order based on the active flags
# ─────────────────────────────────────────────────────────────────────
def _resolve_cascade_order() -> tuple[list[str], str]:
    """Return ``(order, flag_name_active)``.

    Priority of flags (most recent wins):
      1. ``ENABLE_F93_CASCADE_ORDER=true`` (default) — F93 5-step cascade:
         ``thestatsapi → api_sports → totalcorner → 365scores → footystats``.
      2. ``ENABLE_F83_CASCADE_ORDER=true`` — F83 legacy debug order:
         ``api_sports → 365scores → thestatsapi``.
      3. Otherwise — F82.2 default: ``thestatsapi → api_sports → 365scores``.

    F93 takes precedence so legacy ops who explicitly opt-out (``=false``)
    still get the F83/F82.2 behavior.
    """
    if is_f93_cascade_order_enabled():
        return (
            ["thestatsapi", "api_sports", "totalcorner", "365scores", "footystats"],
            "F93",
        )
    if is_f83_cascade_order_enabled():
        return (["api_sports", "365scores", "thestatsapi"], "F83")
    return (["thestatsapi", "api_sports", "365scores"], "F82.2")


async def debug_corners_cascade(match_doc: dict, *, allow_external: bool = True) -> dict:
    """Run the corners cascade in *diagnostic* mode and return a dict
    suitable for the corners debug endpoint.

    Honours :func:`is_f93_cascade_order_enabled` first (default ON), then
    :func:`is_f83_cascade_order_enabled`, then the F82.2 default.

    The function ALWAYS returns a dict; never raises.
    """
    if not isinstance(match_doc, dict):
        match_doc = {}

    cascade_order, flag_label = _resolve_cascade_order()

    home = (match_doc.get("home_team") or {}).get("name") \
        if isinstance(match_doc.get("home_team"), dict) else None
    away = (match_doc.get("away_team") or {}).get("name") \
        if isinstance(match_doc.get("away_team"), dict) else None

    # Always include scrape.do diagnostics (single transport shared by
    # 365scores, totalcorner and footystats).
    try:
        from .external_sources.score365_scrapedo_client import (
            breaker_status as _bs, is_enabled as _ise,
        )
        scrapedo_block = {
            "enabled":        _ise(),
            "breaker_status": _bs(),
        }
    except Exception as exc:  # noqa: BLE001
        scrapedo_block = {"enabled": False, "error": str(exc)}

    providers_checked: list[dict] = []
    winner: Optional[dict] = None
    timeout_s = score365_timeout_seconds()

    for prov in cascade_order:
        if winner is not None:
            break
        if prov == "thestatsapi":
            entry = _f83_check_thestatsapi(match_doc)
        elif prov == "api_sports":
            entry = _f83_check_apisports(match_doc)
        elif prov == "365scores":
            if not allow_external:
                entry = {
                    "provider":     "365scores",
                    "transport":    "scrape_do",
                    "available":    False,
                    "stage":        "SKIPPED",
                    "reason_code":  RC_365_SKIPPED_INLINE,
                    "message_user": "365Scores no se consultó porque está deshabilitado en modo rápido.",
                    "retryable":    True,
                }
            else:
                entry = await _f83_check_365scores(match_doc, timeout_s=timeout_s)
        elif prov == "totalcorner":
            if not allow_external:
                entry = {
                    "provider":     "totalcorner",
                    "transport":    "scrape_do",
                    "available":    False,
                    "stage":        "SKIPPED",
                    "reason_code":  "TOTALCORNER_SKIPPED_INLINE",
                    "message_user": "TotalCorner no se consultó porque está deshabilitado en modo rápido.",
                    "retryable":    True,
                }
            else:
                entry = await _f93_check_totalcorner(match_doc, timeout_s=timeout_s)
        elif prov == "footystats":
            if not allow_external:
                entry = {
                    "provider":     "footystats",
                    "transport":    "scrape_do",
                    "available":    False,
                    "stage":        "SKIPPED",
                    "reason_code":  "FOOTYSTATS_SKIPPED_INLINE",
                    "message_user": "FootyStats no se consultó porque está deshabilitado en modo rápido.",
                    "retryable":    True,
                }
            else:
                entry = await _f93_check_footystats(match_doc, timeout_s=timeout_s)
        else:
            continue
        providers_checked.append(entry)
        if entry.get("available"):
            winner = entry

    final: dict
    if winner is not None:
        final = {
            "available":    True,
            "provider":     winner["provider"],
            "transport":    winner.get("transport"),
            "stage":        winner.get("stage"),
            "data":         winner.get("data"),
            "confidence":   winner.get("confidence"),
            "reason_code":  winner.get("reason_code"),
            "message_user": "Córners cargados correctamente.",
        }
    else:
        # Pick the most actionable reason from the last provider checked.
        last = providers_checked[-1] if providers_checked else {}
        final = {
            "available":     False,
            "reason_code":   RC_NO_PROVIDER_AVAILABLE,
            "message_user":  _f83_user_message(RC_NO_PROVIDER_AVAILABLE),
            "message_debug": last.get("message_debug"),
            "last_reason":   last.get("reason_code"),
        }

    return {
        "match_id":           match_doc.get("match_id"),
        "home":               home,
        "away":               away,
        "cascade_order_used": cascade_order,
        "cascade_flag":       flag_label,
        "flag_enabled":       flag_label != "F82.2",
        "scrapedo":           scrapedo_block,
        "providers_checked":  providers_checked,
        "winner":             winner,
        "final":              final,
    }


async def enrich_match_corners_f83(client, db, match_doc: dict) -> dict:
    """F83-grade corners enrichment.

    Runs :func:`debug_corners_cascade` and adapts the winner (if any) to
    the legacy ``payload`` shape so the rest of the pipeline (persist
    + UI) keeps working with the new diagnostics surfaced.
    """
    res = await debug_corners_cascade(match_doc, allow_external=True)
    final = res["final"]
    if final.get("available"):
        winner = res["winner"]
        data   = winner.get("data") or {}
        payload = {
            "available":     True,
            "source":        winner["provider"],
            "current_match": {
                "home":  data.get("home"),
                "away":  data.get("away"),
                "total": data.get("total"),
            },
            "confidence":    winner.get("confidence")
                              or _confidence_from(winner["provider"],
                                                   data.get("home"),
                                                   data.get("away")),
            "reason_codes":  [winner.get("reason_code")] if winner.get("reason_code") else [],
            "stage":         winner.get("stage"),
            "transport":     winner.get("transport"),
            "cascade_order": res["cascade_order_used"],
            "_raw_provider": (winner.get("raw_provider")
                               if isinstance(winner.get("raw_provider"), dict) else None),
        }
        _persist(match_doc, payload)
        return payload

    payload = {
        "available":     False,
        "reason_code":   final.get("reason_code", RC_NO_PROVIDER_AVAILABLE),
        "reason_codes":  [final.get("reason_code") or RC_NO_PROVIDER_AVAILABLE],
        "message_user":  final.get("message_user"),
        "cascade_order": res["cascade_order_used"],
        "providers_checked": [
            {"provider": e.get("provider"),
             "reason_code": e.get("reason_code"),
             "stage": e.get("stage")}
            for e in res["providers_checked"]
        ],
    }
    _persist(match_doc, payload)
    return payload
