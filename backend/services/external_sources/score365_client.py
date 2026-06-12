"""Phase F82 — 365Scores client (via scrape.do proxy).

Provides match statistics (corners, shots, possession, cards) and
h2h/odds context using the public 365Scores endpoints at
``webws.365scores.com``. We route through ``scrape.do`` because
365Scores ships Cloudflare on the front pages; the JSON endpoints
are often accessible without challenges but we go through proxy to
be safe.

Label externo: ``365scores``.
Nombre interno: ``score365``.

Fail-soft everywhere: HTTP errors / missing data → empty dicts, never
raises. Stamped with ``source = '365scores'`` and ``fetched_at`` ISO.

Fase 1 ID resolution:
  * ``match_doc['external_ids']['365scores']['game_id']`` direct.
  * Parse from ``match_url`` if present (``#id=...`` or ``-(H-A-G)`` pattern).

Fase 2 ID resolution (by date + names):
  * Fetches games of the day endpoint and matches by normalized
    team names with ±1 day tolerance + alias map (USA / United States,
    Bosnia & Herzegovina / Bosnia-Herzegovina, etc.).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────
SOURCE_LABEL  = '365scores'
WEBWS_BASE    = 'https://webws.365scores.com/web'
DEFAULT_TZ    = 'America/Mexico_City'
DEFAULT_LANG  = 'en'

# ID patterns for ``match_url`` parsing.
_GAME_ID_RX     = re.compile(r'(?:[?&#]id=|/game/)(\d+)', re.IGNORECASE)
_MATCHUP_RX     = re.compile(r'-(\d+)-(\d+)-(\d+)(?:[/?#]|$)')

# Stat name aliases (multi-language). Keys are canonical labels.
# IMPORTANT: aliases must be **accent-stripped, lowercase** because
# the matcher uses `_strip_accents_lower(name)` before comparing.
_CORNER_ALIASES = {
    'corners', 'corner kicks', 'corners', 'corner', 'tiros de esquina',
    'escanteios', 'corners totales', 'total corners',
}
_SHOTS_ALIASES        = {'shots', 'total shots', 'tiros'}
_SHOTS_ON_TGT_ALIASES = {'shots on target', 'shots on goal', 'sot',
                          'tiros a puerta', 'tiros a porteria',
                          'remates al arco'}
_POSSESSION_ALIASES   = {'possession', 'ball possession', 'posesion'}
_YELLOW_ALIASES       = {'yellow cards', 'yellow', 'tarjetas amarillas', 'amarillas'}
_RED_ALIASES          = {'red cards', 'red', 'tarjetas rojas', 'rojas'}

# Country / team alias map for fuzzy resolution (Phase 2).
_TEAM_ALIASES: dict[str, set[str]] = {
    'usa': {'usa', 'united states', 'united states of america', 'eeuu',
            'estados unidos', 'usmnt'},
    'bosnia': {'bosnia', 'bosnia and herzegovina', 'bosnia & herzegovina',
               'bosnia-herzegovina', 'bosnia y herzegovina', 'bih'},
    'south korea':  {'south korea', 'korea republic', 'korea south', 'corea del sur'},
    'north macedonia': {'north macedonia', 'macedonia', 'macedonia del norte'},
    'ivory coast': {'ivory coast', 'cote d ivoire', 'côte d ivoire', 'costa de marfil'},
}


def _strip_accents_lower(s: str) -> str:
    if not isinstance(s, str):
        return ''
    nf = unicodedata.normalize('NFD', s)
    out = ''.join(c for c in nf if unicodedata.category(c) != 'Mn').lower().strip()
    # remove 'National Team' suffix and 'national football team'
    out = re.sub(r'\b(national football team|national team|fc|football club)\b',
                  '', out).strip()
    out = re.sub(r'\s+', ' ', out)
    return out


def _team_canonical(name: str) -> set[str]:
    """Returns the set of canonical names that ``name`` matches.

    Always includes the normalized name itself + its alias bucket(s).
    """
    norm = _strip_accents_lower(name)
    result: set[str] = {norm}
    for canon, aliases in _TEAM_ALIASES.items():
        if norm == canon or norm in aliases:
            result.add(canon)
            result.update(aliases)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# ID extraction (Fase 1)
# ─────────────────────────────────────────────────────────────────────
def extract_game_id_from_url(match_url: str) -> Optional[str]:
    """Parse a 365Scores match URL and return the ``game_id``.

    Accepts shapes like:
        https://www.365scores.com/football/match/usa-paraguay-12345#id=12345
        https://www.365scores.com/football/match/.../-123-456-789
    """
    if not isinstance(match_url, str) or not match_url:
        return None
    m = _GAME_ID_RX.search(match_url)
    if m:
        return m.group(1)
    m2 = _MATCHUP_RX.search(match_url)
    if m2:
        return m2.group(3)  # third group = game_id
    return None


def extract_matchup_id_from_url(match_url: str) -> Optional[str]:
    """Parse ``matchup_id`` from a 365Scores URL.

    Pattern: ``-(home_team_id)-(away_team_id)-(game_id)`` → matchup = ``H-A``.
    """
    if not isinstance(match_url, str) or not match_url:
        return None
    m = _MATCHUP_RX.search(match_url)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    return None


def resolve_game_id_from_match_doc(match_doc: dict) -> tuple[Optional[str], Optional[str]]:
    """Returns (game_id, matchup_id) when resolvable from ``external_ids``
    or ``match_url`` fields. Both can be ``None``.
    """
    if not isinstance(match_doc, dict):
        return None, None
    ext = match_doc.get('external_ids') or {}
    s365 = ext.get('365scores') if isinstance(ext, dict) else None
    if isinstance(s365, dict):
        gid = s365.get('game_id') or s365.get('id')
        mid = s365.get('matchup_id')
        if gid:
            return str(gid), str(mid) if mid else None
    url = (match_doc.get('match_url')
           or match_doc.get('365scores_url')
           or (s365 or {}).get('url'))
    return extract_game_id_from_url(url or ''), extract_matchup_id_from_url(url or '')


# ─────────────────────────────────────────────────────────────────────
# HTTP via scrape.do
# ─────────────────────────────────────────────────────────────────────
async def _fetch_json(target_url: str, *, timeout: float = 45.0) -> Optional[dict]:
    """GET via scrape.do and parse JSON. Returns ``None`` on any failure."""
    try:
        from ..scrape_do_client import fetch_via_scrapedo
    except Exception:  # noqa: BLE001
        return None
    text = await fetch_via_scrapedo(target_url, timeout=timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        log.debug('365scores JSON parse failed for %s: %s', target_url, exc)
        return None


async def fetch_game_stats(client: Optional[httpx.AsyncClient], game_id: str,
                            *, timezone_name: str = DEFAULT_TZ,
                            lang: str = DEFAULT_LANG) -> dict:
    """GET ``/web/game/stats/?gameId=...``. Returns raw payload or {}.

    The ``client`` param is unused (we route through scrape.do) but kept
    for symmetry with sibling clients.
    """
    if not game_id:
        return {}
    url = (f'{WEBWS_BASE}/game/stats/?appTypeId=5&langId=1&timezoneName=' 
           f'{timezone_name}&gameId={game_id}')
    data = await _fetch_json(url)
    return data or {}


async def fetch_game_data(client: Optional[httpx.AsyncClient], game_id: str,
                          matchup_id: Optional[str] = None,
                          *, timezone_name: str = DEFAULT_TZ,
                          lang: str = DEFAULT_LANG) -> dict:
    """GET ``/web/game/?gameId=...``. Returns raw payload or {}."""
    if not game_id:
        return {}
    base = (f'{WEBWS_BASE}/game/?appTypeId=5&langId=1&timezoneName=' 
            f'{timezone_name}&gameId={game_id}')
    if matchup_id:
        base += f'&matchupId={matchup_id}'
    data = await _fetch_json(base)
    return data or {}


# ─────────────────────────────────────────────────────────────────────
# Normalizer
# ─────────────────────────────────────────────────────────────────────
def _stat_matches(stat_name: str, aliases: set[str]) -> bool:
    sn = _strip_accents_lower(stat_name)
    if not sn:
        return False
    return sn in aliases or any(a in sn for a in aliases)


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def normalize_365scores_match_stats(raw: dict) -> dict:
    """Translate 365Scores ``statistics`` payload to canonical shape.

    Returns ``{available: false, ...}`` when no corner data is found.

    Accepted input shapes:
      * ``raw['game']['statistics']`` (game endpoint).
      * ``raw['statistics']``         (stats endpoint).

    Each statistic entry follows::

        {'name': 'Corner Kicks', 'home': '6', 'away': '3'}
        # or:
        {'name': 'Corners', 'competitorId': 123, 'value': '6'} (split per side)
    """
    if not isinstance(raw, dict):
        return {'available': False, 'source': SOURCE_LABEL,
                'reason_codes': ['SCORE365_RAW_INVALID']}

    game = raw.get('game') if isinstance(raw.get('game'), dict) else raw
    stats = game.get('statistics') or raw.get('statistics') or []
    if not isinstance(stats, list):
        stats = []

    competitors = (game.get('competitors') or raw.get('competitors') or [])
    home_team_id = None
    away_team_id = None
    home_team_name = None
    away_team_name = None
    if isinstance(competitors, list) and len(competitors) >= 2:
        home, away = competitors[0], competitors[1]
        if isinstance(home, dict):
            home_team_id   = home.get('id')
            home_team_name = home.get('name') or home.get('symbolicName')
        if isinstance(away, dict):
            away_team_id   = away.get('id')
            away_team_name = away.get('name') or away.get('symbolicName')

    raw_stat_names: list[str] = []
    home_corners = None
    away_corners = None
    home_shots = away_shots = None
    home_sot = away_sot = None
    home_pos = away_pos = None
    home_yellow = away_yellow = None
    home_red = away_red = None

    for s in stats:
        if not isinstance(s, dict):
            continue
        name = s.get('name') or s.get('shortName') or ''
        raw_stat_names.append(str(name))
        h = s.get('home')
        a = s.get('away')
        # Some payloads use 'homeValue'/'awayValue' or competitor-split rows.
        if h is None and 'homeValue' in s:
            h = s.get('homeValue')
        if a is None and 'awayValue' in s:
            a = s.get('awayValue')

        if _stat_matches(name, _CORNER_ALIASES):
            home_corners = _safe_int(h) if home_corners is None else home_corners
            away_corners = _safe_int(a) if away_corners is None else away_corners
        elif _stat_matches(name, _SHOTS_ON_TGT_ALIASES):
            home_sot = _safe_int(h) if home_sot is None else home_sot
            away_sot = _safe_int(a) if away_sot is None else away_sot
        elif _stat_matches(name, _SHOTS_ALIASES):
            home_shots = _safe_int(h) if home_shots is None else home_shots
            away_shots = _safe_int(a) if away_shots is None else away_shots
        elif _stat_matches(name, _POSSESSION_ALIASES):
            home_pos = _safe_int(h) if home_pos is None else home_pos
            away_pos = _safe_int(a) if away_pos is None else away_pos
        elif _stat_matches(name, _YELLOW_ALIASES):
            home_yellow = _safe_int(h) if home_yellow is None else home_yellow
            away_yellow = _safe_int(a) if away_yellow is None else away_yellow
        elif _stat_matches(name, _RED_ALIASES):
            home_red = _safe_int(h) if home_red is None else home_red
            away_red = _safe_int(a) if away_red is None else away_red

    have_corners = (home_corners is not None or away_corners is not None)
    total_corners = None
    if home_corners is not None and away_corners is not None:
        total_corners = home_corners + away_corners

    out = {
        'available':         have_corners,
        'source':            SOURCE_LABEL,
        'provider_game_id':  str(game.get('id') or raw.get('id') or '') or None,
        'home': {
            'team_id':           home_team_id,
            'team':              home_team_name,
            'corners':           home_corners,
            'shots':             home_shots,
            'shots_on_target':   home_sot,
            'possession':        home_pos,
            'yellow_cards':      home_yellow,
            'red_cards':         home_red,
        },
        'away': {
            'team_id':           away_team_id,
            'team':              away_team_name,
            'corners':           away_corners,
            'shots':             away_shots,
            'shots_on_target':   away_sot,
            'possession':        away_pos,
            'yellow_cards':      away_yellow,
            'red_cards':         away_red,
        },
        'total_corners':     total_corners,
        'raw_stat_names':    raw_stat_names,
        'fetched_at':        _now_iso(),
        'reason_codes':      ['SCORE365_STATS_NORMALIZED'
                                if have_corners else 'SCORE365_NO_CORNERS_IN_PAYLOAD'],
    }
    return out


# ─────────────────────────────────────────────────────────────────────
# Resolver Fase 2: fecha + nombres
# ─────────────────────────────────────────────────────────────────────
async def resolve_game_id_by_date_and_names(
    home: str, away: str, date_iso: str,
    *, sport_id: int = 1, timezone_name: str = DEFAULT_TZ,
) -> Optional[str]:
    """List 365Scores games for the day (±1 day tolerance) and match
    by normalized team names + alias map.

    Returns ``game_id`` (str) or ``None``. Fail-soft.
    """
    if not (home and away and date_iso):
        return None
    try:
        target_dt = datetime.fromisoformat(date_iso.replace('Z', '+00:00'))
    except Exception:  # noqa: BLE001
        try:
            target_dt = datetime.strptime(date_iso[:10], '%Y-%m-%d').replace(
                tzinfo=timezone.utc,
            )
        except Exception:  # noqa: BLE001
            return None

    home_aliases = _team_canonical(home)
    away_aliases = _team_canonical(away)

    for delta_days in (0, -1, 1):  # tolerancia ±1 día
        day = (target_dt + timedelta(days=delta_days)).strftime('%d/%m/%Y')
        url = (f'{WEBWS_BASE}/games/allscores/?appTypeId=5&langId=1' 
               f'&timezoneName={timezone_name}&sports={sport_id}' 
               f'&startDate={day}&endDate={day}')
        data = await _fetch_json(url)
        games = []
        if isinstance(data, dict):
            games = data.get('games') or []
        elif isinstance(data, list):
            games = data
        if not games:
            continue
        for g in games:
            if not isinstance(g, dict):
                continue
            competitors = g.get('competitors') or g.get('teams') or []
            if not isinstance(competitors, list) or len(competitors) < 2:
                continue
            h = (competitors[0].get('name') or competitors[0].get('symbolicName')
                 if isinstance(competitors[0], dict) else '')
            a = (competitors[1].get('name') or competitors[1].get('symbolicName')
                 if isinstance(competitors[1], dict) else '')
            h_norm = _strip_accents_lower(h or '')
            a_norm = _strip_accents_lower(a or '')
            home_ok = bool(h_norm) and (
                h_norm in home_aliases
                or any(alias in h_norm for alias in home_aliases)
                or any(h_norm in alias for alias in home_aliases)
            )
            away_ok = bool(a_norm) and (
                a_norm in away_aliases
                or any(alias in a_norm for alias in away_aliases)
                or any(a_norm in alias for alias in away_aliases)
            )
            if home_ok and away_ok:
                gid = g.get('id') or g.get('gameId')
                if gid:
                    return str(gid)
    log.info('[365scores] game_id not found by names: %s vs %s @%s',
             home, away, date_iso[:10])
    return None


__all__ = [
    'SOURCE_LABEL',
    'fetch_game_stats',
    'fetch_game_data',
    'normalize_365scores_match_stats',
    'extract_game_id_from_url',
    'extract_matchup_id_from_url',
    'resolve_game_id_from_match_doc',
    'resolve_game_id_by_date_and_names',
]
