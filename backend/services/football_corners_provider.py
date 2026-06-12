"""Phase F82 — Football corners provider (cascade).

Priority of sources (highest → lowest):
  1. API-Sports fixture statistics (if 'Corner Kicks' present).
  2. 365Scores (via scrape.do) when a game_id can be resolved.
  3. TheStatsAPI (when ``corners`` / ``corner_stats`` block exists).
  4. None → ``available=false`` with explicit ``reason_codes``.

Persists the resolved payload into three locations for maximum
compatibility downstream:
  * ``match_doc['football_data_enrichment']['corners']``
  * ``match_doc['thestatsapi_snapshot']['corners']`` (alias / legacy reader)
  * ``match_doc['corners_snapshot']`` (top-level)

Fail-soft everywhere.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

RC_APISPORTS         = 'CORNERS_FROM_APISPORTS'
RC_365SCORES         = 'CORNERS_FROM_365SCORES'
RC_THESTATSAPI       = 'CORNERS_FROM_THESTATSAPI'
RC_UNAVAILABLE       = 'CORNERS_UNAVAILABLE'
RC_NO_API_SPORTS     = 'CORNERS_NO_APISPORTS_STATS'
RC_NO_365_ID         = 'SCORE365_ID_MISSING'
RC_365_BLOCKED       = 'SCORE365_BLOCKED_OR_EMPTY'
RC_NO_THESTATSAPI    = 'CORNERS_NO_THESTATSAPI_BLOCK'
RC_PROVIDER_BREAKER  = 'CORNERS_PROVIDER_BREAKER_OPEN'


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
) -> tuple[Optional[dict], list[str]]:
    """Try 365Scores. Returns (payload or None, reason_codes)."""
    codes: list[str] = []
    try:
        from .external_sources import score365_client as _s365
    except Exception as exc:  # noqa: BLE001
        log.debug('score365_client unavailable: %s', exc)
        return None, [RC_PROVIDER_BREAKER]

    game_id, matchup_id = _s365.resolve_game_id_from_match_doc(match_doc)

    if not game_id and allow_name_resolver:
        # Fase 2 — resolver por fecha + nombres.
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
        codes.append(RC_NO_365_ID)
        return None, codes

    try:
        raw = await _s365.fetch_game_stats(client, game_id)
        if not raw:
            raw = await _s365.fetch_game_data(client, game_id, matchup_id)
    except Exception as exc:  # noqa: BLE001
        log.debug('365scores fetch failed for %s: %s', game_id, exc)
        codes.append(RC_365_BLOCKED)
        return None, codes

    normalised = _s365.normalize_365scores_match_stats(raw)
    if not normalised.get('available'):
        codes.append(RC_365_BLOCKED)
        return None, codes

    return {
        'source': '365scores',
        'home':   normalised['home'].get('corners'),
        'away':   normalised['away'].get('corners'),
        'total':  normalised.get('total_corners'),
        '_raw_provider': normalised,
    }, codes


def _confidence_from(source: str, home: Optional[int], away: Optional[int]) -> str:
    if home is None or away is None:
        return 'LIMITED'
    if source == 'api_sports':
        return 'STRONG'
    if source == '365scores':
        return 'USABLE'
    return 'LIMITED'


async def enrich_match_corners(client, db, match_doc: dict) -> dict:
    """Resolve corners for a match using the cascade.

    Always returns a dict with ``available`` boolean and ``reason_codes``.
    Persists to multiple locations for compat.
    """
    if not isinstance(match_doc, dict):
        return {'available': False, 'reason_codes': [RC_UNAVAILABLE]}

    fid = match_doc.get('match_id')
    codes: list[str] = []

    # 1) API-Sports
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
        log.info('[corners_provider] fixture=%s source=api_sports total=%s home=%s away=%s',
                 fid, aps['total'], aps['home'], aps['away'])
        return payload
    codes.append(RC_NO_API_SPORTS)

    # 2) 365Scores
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

    # 3) TheStatsAPI
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
        log.info('[corners_provider] fixture=%s source=thestatsapi total=%s', fid, tsa['total'])
        return payload
    codes.append(RC_NO_THESTATSAPI)

    payload = {
        'available':    False,
        'reason_codes': codes + [RC_UNAVAILABLE],
    }
    _persist(match_doc, payload)
    log.info('[corners_provider] fixture=%s unavailable reason=%s', fid,
             ','.join(codes) or RC_UNAVAILABLE)
    return payload


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
    'RC_APISPORTS', 'RC_365SCORES', 'RC_THESTATSAPI', 'RC_UNAVAILABLE',
    'RC_NO_API_SPORTS', 'RC_NO_365_ID', 'RC_365_BLOCKED',
    'RC_NO_THESTATSAPI', 'RC_PROVIDER_BREAKER',
]
