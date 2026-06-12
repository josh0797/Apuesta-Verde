"""Phase F82 — Rich H2H Context Builder.

Transforma ``match_doc['h2h_recent']`` (lista cruda con date/home/away/score/
status) en un payload editorial rico que la UI puede renderizar como
lista de partidos + métricas accionables (Under 3.5 rate, BTTS rate,
promedio de goles, etc.).

Reglas (acordadas con producto):
  * Si hay H2H con marcador → siempre renderizar la lista, no solo el
    conteo. Si solo hay conteo → la UI dice 'X H2H sin marcador'.
  * Si no hay H2H → 'No hay H2H reciente confiable.'.
  * H2H **solo como contexto secundario** (jamás fuente primaria de pick).
  * En partidos de selecciones, permitir años atrás pero marcar
    ``sample_quality: LIMITED`` cuando son amistosos / muy antiguos.

Output contract (canónico)::

    {
      'available':     bool,
      'sample_size':   int,
      'sample_quality':'STRONG|USABLE|LIMITED|NONE',
      'matches':       [{date, home, away, score, total_goals, btts,
                          over_1_5, over_2_5, under_3_5, status, result}],
      'summary': {
        'avg_goals':           float,
        'over_1_5_rate':       float,
        'over_2_5_rate':       float,
        'under_3_5_rate':      float,
        'btts_rate':           float,
        'home_unbeaten_rate':  float,   # nota: 'home' = side actual del match_doc
      },
      'editorial_text': str,                  # texto compacto listo para UI
      'reason_codes':   [str, ...],
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

RC_AVAILABLE             = 'H2H_CONTEXT_AVAILABLE'
RC_NO_H2H                = 'H2H_CONTEXT_NO_RECENT'
RC_NO_SCORE              = 'H2H_CONTEXT_NO_SCORE_AVAILABLE'
RC_FRIENDLY_OR_OLD       = 'H2H_CONTEXT_FRIENDLY_OR_OLD_LIMITED'
RC_NATIONAL_TEAMS        = 'H2H_CONTEXT_NATIONAL_TEAMS'
RC_SAMPLE_LOW            = 'H2H_CONTEXT_SAMPLE_LOW'

QUALITY_NONE     = 'NONE'
QUALITY_LIMITED  = 'LIMITED'
QUALITY_USABLE   = 'USABLE'
QUALITY_STRONG   = 'STRONG'

# Status codes that indicate a finished match with a real score.
_FT_STATUSES = {'FT', 'AET', 'PEN', 'AWD', 'WO', 'FT_PEN', 'ENDED'}


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_score(score_str: Any) -> tuple[Optional[int], Optional[int]]:
    """Parse '1-0' / '2-1' / '0-0'. Returns (home_goals, away_goals).

    Accepts also dict shapes {home: 1, away: 0}.
    """
    if isinstance(score_str, dict):
        return _safe_int(score_str.get('home')), _safe_int(score_str.get('away'))
    if not isinstance(score_str, str):
        return None, None
    s = score_str.strip()
    if '-' not in s:
        return None, None
    try:
        h, a = s.split('-', 1)
        return _safe_int(h.strip()), _safe_int(a.strip())
    except Exception:  # noqa: BLE001
        return None, None


def _parse_iso_date(date_str: Any) -> Optional[datetime]:
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        # API-Sports uses 'YYYY-MM-DDTHH:MM:SS+00:00'.
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:  # noqa: BLE001
        try:
            return datetime.strptime(date_str[:10], '%Y-%m-%d').replace(
                tzinfo=timezone.utc,
            )
        except Exception:  # noqa: BLE001
            return None


def _years_ago(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days / 365.25


def _is_friendly(league_name: Any, h2h_entry: dict) -> bool:
    blob = ' '.join([
        str(league_name or ''),
        str(h2h_entry.get('league') or ''),
        str(h2h_entry.get('competition') or ''),
    ]).lower()
    if not blob.strip():
        return False
    for kw in ('friendly', 'amistoso', 'amistos', 'club friendly'):
        if kw in blob:
            return True
    return False


def _format_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%d')


def _format_result(home: str, score: str, away: str) -> str:
    h = home or 'Home'
    a = away or 'Away'
    s = score or '?-?'
    return f'{h} {s} {a}'


def build_h2h_context(match_doc: dict) -> dict:
    """Builds the canonical h2h_context payload from match_doc.

    Never raises — returns ``{'available': False, 'reason_codes': [...]}
    on any error.
    """
    if not isinstance(match_doc, dict):
        return {
            'available':      False,
            'sample_size':    0,
            'sample_quality': QUALITY_NONE,
            'matches':        [],
            'summary':        {},
            'editorial_text': 'No hay H2H reciente confiable.',
            'reason_codes':   [RC_NO_H2H],
        }

    h2h_raw = match_doc.get('h2h_recent') or match_doc.get('h2h_matches') or []
    if not isinstance(h2h_raw, list) or not h2h_raw:
        return {
            'available':      False,
            'sample_size':    0,
            'sample_quality': QUALITY_NONE,
            'matches':        [],
            'summary':        {},
            'editorial_text': 'No hay H2H reciente confiable.',
            'reason_codes':   [RC_NO_H2H],
        }

    league_name = match_doc.get('league') or match_doc.get('competition')
    is_national = bool(match_doc.get('is_national_team'))

    matches: list[dict] = []
    matches_no_score: list[dict] = []
    reason_codes: list[str] = []
    any_friendly = False
    oldest_years: float = 0.0

    for entry in h2h_raw:
        if not isinstance(entry, dict):
            continue
        home  = str(entry.get('home') or entry.get('home_team') or '').strip()
        away  = str(entry.get('away') or entry.get('away_team') or '').strip()
        score_raw = entry.get('score')
        h_goals, a_goals = _parse_score(score_raw)
        status = str(entry.get('status') or '').upper().strip()
        dt = _parse_iso_date(entry.get('date'))
        date_label = _format_date(dt) or str(entry.get('date') or '')[:10]
        friendly = _is_friendly(league_name, entry)
        any_friendly = any_friendly or friendly
        if dt is not None:
            yrs = _years_ago(dt)
            if yrs > oldest_years:
                oldest_years = yrs

        # Si no hay marcador concreto, separar a la lista 'sin score'.
        if h_goals is None or a_goals is None:
            matches_no_score.append({
                'date':   date_label,
                'home':   home,
                'away':   away,
                'status': status,
            })
            continue

        total = h_goals + a_goals
        matches.append({
            'date':         date_label,
            'home':         home,
            'away':         away,
            'score':        f'{h_goals}-{a_goals}',
            'home_goals':   h_goals,
            'away_goals':   a_goals,
            'total_goals':  total,
            'btts':         (h_goals >= 1 and a_goals >= 1),
            'over_1_5':     total >= 2,
            'over_2_5':     total >= 3,
            'under_3_5':    total <= 3,
            'status':       status or ('FT' if total is not None else ''),
            'result':       _format_result(home, f'{h_goals}-{a_goals}', away),
            'friendly':     friendly,
        })

    sample_size = len(matches)

    # ── No score in any H2H → degrade gracefully ─────────────────────
    if sample_size == 0:
        if matches_no_score:
            text = f'Hay {len(matches_no_score)} H2H registrados, pero sin marcador disponible.'
            codes = [RC_NO_SCORE]
        else:
            text = 'No hay H2H reciente confiable.'
            codes = [RC_NO_H2H]
        return {
            'available':      False,
            'sample_size':    len(matches_no_score),
            'sample_quality': QUALITY_NONE,
            'matches':        [],
            'matches_no_score': matches_no_score,
            'summary':        {},
            'editorial_text': text,
            'reason_codes':   codes,
        }

    # ── Métricas agregadas ───────────────────────────────────────────
    avg_goals      = round(sum(m['total_goals'] for m in matches) / sample_size, 2)
    over_1_5_rate  = round(sum(1 for m in matches if m['over_1_5']) / sample_size, 2)
    over_2_5_rate  = round(sum(1 for m in matches if m['over_2_5']) / sample_size, 2)
    under_3_5_rate = round(sum(1 for m in matches if m['under_3_5']) / sample_size, 2)
    btts_rate      = round(sum(1 for m in matches if m['btts']) / sample_size, 2)

    # Home (del match_doc) winrate (sin contar empates).
    home_name_doc = ((match_doc.get('home_team') or {}).get('name')
                      or match_doc.get('home_team_name') or '')
    home_name_norm = str(home_name_doc).strip().lower()
    home_unbeaten = 0
    for m in matches:
        # H2H entries traen home/away que pueden invertirse vs match_doc.
        # 'Unbeaten' del equipo local actual = ganó o empató cuando le tocó jugar.
        if home_name_norm and m['home'].strip().lower() == home_name_norm:
            # Era local en ese H2H.
            if m['home_goals'] >= m['away_goals']:
                home_unbeaten += 1
        elif home_name_norm and m['away'].strip().lower() == home_name_norm:
            # Era visitante en ese H2H.
            if m['away_goals'] >= m['home_goals']:
                home_unbeaten += 1
    home_unbeaten_rate = round(home_unbeaten / sample_size, 2) if home_name_norm else None

    # ── Calidad de la muestra ────────────────────────────────────────
    sample_quality = QUALITY_USABLE
    if sample_size >= 5 and not any_friendly and oldest_years < 5:
        sample_quality = QUALITY_STRONG
    elif sample_size <= 2 or any_friendly or oldest_years > 7:
        sample_quality = QUALITY_LIMITED
        reason_codes.append(RC_FRIENDLY_OR_OLD if any_friendly else RC_SAMPLE_LOW)
    if is_national:
        reason_codes.append(RC_NATIONAL_TEAMS)
    reason_codes.append(RC_AVAILABLE)

    # ── Editorial text compacto ──────────────────────────────────────
    list_compact = ', '.join(m['result'] for m in matches[:4])
    tendencias: list[str] = []
    if under_3_5_rate >= 0.75:
        tendencias.append(f'{int(under_3_5_rate * sample_size)}/{sample_size} Under 3.5')
    if btts_rate >= 0.5:
        tendencias.append(f'BTTS en {int(btts_rate * sample_size)}/{sample_size}')
    if over_2_5_rate >= 0.75:
        tendencias.append(f'{int(over_2_5_rate * sample_size)}/{sample_size} Over 2.5')
    tend_text = ('. Tendencia: ' + ', '.join(tendencias) + '.') if tendencias else '.'
    editorial_text = (
        f'Últimos {sample_size} H2H: {list_compact}{tend_text} '
        f'Promedio de goles: {avg_goals}.'
    )

    summary: dict[str, Any] = {
        'avg_goals':       avg_goals,
        'over_1_5_rate':   over_1_5_rate,
        'over_2_5_rate':   over_2_5_rate,
        'under_3_5_rate':  under_3_5_rate,
        'btts_rate':       btts_rate,
    }
    if home_unbeaten_rate is not None:
        summary['home_unbeaten_rate'] = home_unbeaten_rate

    return {
        'available':      True,
        'sample_size':    sample_size,
        'sample_quality': sample_quality,
        'matches':        matches,
        'matches_no_score': matches_no_score,
        'summary':        summary,
        'editorial_text': editorial_text,
        'reason_codes':   reason_codes,
    }


__all__ = [
    'RC_AVAILABLE', 'RC_NO_H2H', 'RC_NO_SCORE',
    'RC_FRIENDLY_OR_OLD', 'RC_NATIONAL_TEAMS', 'RC_SAMPLE_LOW',
    'QUALITY_NONE', 'QUALITY_LIMITED', 'QUALITY_USABLE', 'QUALITY_STRONG',
    'build_h2h_context',
]
