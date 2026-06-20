"""Sprint-D8/E PASO 2 · Cards ingestor (point-in-time strict).

Construye los **features prematch** que alimentan
``football_cards_potential.compute_cards_potential`` sin leakage:

  * ``referee_cards_avg`` (PIT): promedio de tarjetas del árbitro
    calculado **solo con partidos cuya fecha < D** (D = fecha del
    partido objetivo). Si la muestra previa < ``min_sample`` (default
    5), levanta ``LOW_REFEREE_SAMPLE`` y aplica fallback al promedio
    de liga.
  * ``team_cards_for_avg`` por equipo: idem, solo partidos < D del
    equipo.
  * ``team_fouls_avg`` por equipo: idem.

Disciplina
==========
* **Sin I/O acoplado**: el ingestor recibe los datasets ya cargados
  (``list[dict]`` de partidos históricos canonicalizados). El I/O
  (scrape.do, BD, CSV) lo hace el caller.
* **PIT estrictamente verificado por test**: el partido objetivo
  NUNCA puede entrar en su propio promedio del árbitro.
* **Fail-soft sobre filas malformadas**: rows sin fecha o sin
  árbitro se ignoran con ``log.debug``.
* **Liga-fallback documentado**: cuando ``n_prior < min_sample``,
  ``referee_cards_avg`` se setea al ``league_avg_cards`` calculado
  sobre todos los partidos < D (igualmente PIT).

Formato de input (cada match dict)
==================================
``{
   "match_id":      str (opcional pero recomendado),
   "date":          datetime | str ISO (REQUERIDO),
   "league":        str (opcional, usado para calcular league_avg PIT),
   "home_team":     str (REQUERIDO para team-level features),
   "away_team":     str (REQUERIDO),
   "referee":       str (opcional; sin referee, no se levanta error),
   "home_cards":    int (yellow + red home, REQUERIDO para ground truth),
   "away_cards":    int (REQUERIDO),
   "home_fouls":    int (opcional),
   "away_fouls":    int (opcional),
}``
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("services.football_cards_ingestor")

MIN_REFEREE_SAMPLE_DEFAULT = 5
LOW_REFEREE_SAMPLE_RC      = "LOW_REFEREE_SAMPLE"
REFEREE_FALLBACK_RC        = "REFEREE_FALLBACK_LEAGUE_AVG"
REFEREE_OK_RC              = "REFEREE_OK_PIT"
NO_REFEREE_RC              = "REFEREE_MISSING_FROM_FIXTURE"


# ── Date parsing helpers (PIT-critical) ─────────────────────────────
def _to_dt(v) -> Optional[datetime]:
    """Parse anything reasonable into a tz-aware UTC datetime.

    Returns ``None`` for unparseable inputs (fail-soft).
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        s = v.strip()
        # Try ISO first.
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y/%m/%d",
                    "%d/%m/%Y", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc) if not dt.tzinfo else dt
            except ValueError:
                continue
        # Last resort: ISO with offset normalisation.
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _total_cards(row: dict) -> Optional[int]:
    h = _safe_int(row.get("home_cards"))
    a = _safe_int(row.get("away_cards"))
    if h is None or a is None:
        return None
    return h + a


# ── PIT feature builders ─────────────────────────────────────────────
def referee_cards_avg_pit(
    *,
    target_date: datetime,
    referee: Optional[str],
    history: list[dict],
    league: Optional[str] = None,
    min_sample: int = MIN_REFEREE_SAMPLE_DEFAULT,
) -> dict:
    """Compute ``referee_cards_avg`` with strict PIT discipline.

    Returns dict ``{value, n_prior, reason_codes, used_fallback}``.

    The function NEVER reads rows whose date >= ``target_date``.
    Rows missing ``date`` or ``total_cards`` are skipped.
    """
    reason_codes: list[str] = []
    if not referee:
        reason_codes.append(NO_REFEREE_RC)
        league_avg_info = _league_avg_cards_pit(target_date, history, league)
        return {
            "value":          league_avg_info["value"],
            "n_prior":        0,
            "reason_codes":   reason_codes + league_avg_info["reason_codes"],
            "used_fallback":  True,
            "fallback_source": "league_avg_pit",
        }

    target_dt = _to_dt(target_date)
    if not target_dt:
        return {
            "value":         None,
            "n_prior":       0,
            "reason_codes":  ["TARGET_DATE_UNPARSEABLE"],
            "used_fallback": False,
        }

    referee_l = referee.strip().lower()
    prior_totals: list[int] = []
    for row in history:
        row_ref = (row.get("referee") or "").strip().lower()
        if row_ref != referee_l:
            continue
        row_dt = _to_dt(row.get("date"))
        if not row_dt or row_dt >= target_dt:
            continue
        tot = _total_cards(row)
        if tot is None:
            continue
        prior_totals.append(tot)

    n_prior = len(prior_totals)
    if n_prior < min_sample:
        reason_codes.append(LOW_REFEREE_SAMPLE_RC)
        reason_codes.append(REFEREE_FALLBACK_RC)
        league_avg_info = _league_avg_cards_pit(target_date, history, league)
        return {
            "value":          league_avg_info["value"],
            "n_prior":        n_prior,
            "reason_codes":   reason_codes + league_avg_info["reason_codes"],
            "used_fallback":  True,
            "fallback_source": "league_avg_pit",
        }

    avg = round(statistics.mean(prior_totals), 3)
    reason_codes.append(REFEREE_OK_RC)
    return {
        "value":         avg,
        "n_prior":       n_prior,
        "reason_codes":  reason_codes,
        "used_fallback": False,
    }


def _league_avg_cards_pit(
    target_date,
    history: list[dict],
    league: Optional[str],
) -> dict:
    """Average total cards over all history rows with date < target_date
    (optionally filtered by league).
    """
    target_dt = _to_dt(target_date)
    if not target_dt:
        return {"value": None, "reason_codes": ["TARGET_DATE_UNPARSEABLE"]}
    totals: list[int] = []
    for row in history:
        if league and row.get("league") != league:
            continue
        row_dt = _to_dt(row.get("date"))
        if not row_dt or row_dt >= target_dt:
            continue
        t = _total_cards(row)
        if t is not None:
            totals.append(t)
    if not totals:
        return {"value": None, "reason_codes": ["LEAGUE_AVG_NO_PRIOR_DATA"]}
    return {"value": round(statistics.mean(totals), 3), "reason_codes": []}


def team_cards_for_avg_pit(
    *,
    target_date,
    team: str,
    history: list[dict],
) -> Optional[float]:
    """Promedio prematch de tarjetas que el ``team`` recibe por partido.

    Solo cuenta partidos con fecha < target_date donde el equipo
    aparece como home o away.
    """
    target_dt = _to_dt(target_date)
    if not target_dt or not team:
        return None
    cards: list[int] = []
    for row in history:
        row_dt = _to_dt(row.get("date"))
        if not row_dt or row_dt >= target_dt:
            continue
        if row.get("home_team") == team:
            h = _safe_int(row.get("home_cards"))
            if h is not None:
                cards.append(h)
        elif row.get("away_team") == team:
            a = _safe_int(row.get("away_cards"))
            if a is not None:
                cards.append(a)
    if not cards:
        return None
    return round(statistics.mean(cards), 3)


def team_fouls_avg_pit(
    *,
    target_date,
    team: str,
    history: list[dict],
) -> Optional[float]:
    target_dt = _to_dt(target_date)
    if not target_dt or not team:
        return None
    fouls: list[int] = []
    for row in history:
        row_dt = _to_dt(row.get("date"))
        if not row_dt or row_dt >= target_dt:
            continue
        if row.get("home_team") == team:
            f = _safe_int(row.get("home_fouls"))
            if f is not None:
                fouls.append(f)
        elif row.get("away_team") == team:
            f = _safe_int(row.get("away_fouls"))
            if f is not None:
                fouls.append(f)
    if not fouls:
        return None
    return round(statistics.mean(fouls), 3)


# ── High-level builder ──────────────────────────────────────────────
def build_cards_features_pit(
    target_match: dict,
    history: list[dict],
    *,
    min_referee_sample: int = MIN_REFEREE_SAMPLE_DEFAULT,
) -> dict:
    """Build the full feature dict for a target match.

    The function is **pure**. ``history`` should include ALL matches
    available up to the target — the function itself enforces the
    "< target_date" filter so the caller doesn't need to pre-slice.

    Returns:
      ``{features, audit}``, where ``features`` is the kwargs dict
      ready to pass to ``compute_cards_potential``.
    """
    target_date = target_match.get("date")
    referee     = target_match.get("referee")
    home_team   = target_match.get("home_team")
    away_team   = target_match.get("away_team")
    league      = target_match.get("league")

    ref_info = referee_cards_avg_pit(
        target_date=target_date, referee=referee, history=history,
        league=league, min_sample=min_referee_sample,
    )

    home_cards_for = team_cards_for_avg_pit(
        target_date=target_date, team=home_team, history=history,
    )
    away_cards_for = team_cards_for_avg_pit(
        target_date=target_date, team=away_team, history=history,
    )
    home_fouls = team_fouls_avg_pit(
        target_date=target_date, team=home_team, history=history,
    )
    away_fouls = team_fouls_avg_pit(
        target_date=target_date, team=away_team, history=history,
    )

    features = {
        "referee_cards_avg":   ref_info["value"],
        "referee_n_prior":     ref_info["n_prior"],
        "home_cards_for_avg":  home_cards_for,
        "away_cards_for_avg":  away_cards_for,
        "home_fouls_avg":      home_fouls,
        "away_fouls_avg":      away_fouls,
        "is_derby":            False,   # Fase 1: derbies deshabilitados.
        "min_referee_sample":  min_referee_sample,
    }
    audit = {
        "source_audit": {
            "referee_name":         referee,
            "n_referee_prior":      ref_info["n_prior"],
            "referee_fallback":     ref_info.get("used_fallback", False),
            "referee_fallback_src": ref_info.get("fallback_source"),
            "reason_codes":         ref_info["reason_codes"],
        },
        "target_match": {
            "date":      str(target_date),
            "home_team": home_team,
            "away_team": away_team,
            "league":    league,
        },
    }
    return {"features": features, "audit": audit}


__all__ = [
    "build_cards_features_pit",
    "referee_cards_avg_pit",
    "team_cards_for_avg_pit",
    "team_fouls_avg_pit",
    "MIN_REFEREE_SAMPLE_DEFAULT",
    "LOW_REFEREE_SAMPLE_RC",
    "REFEREE_FALLBACK_RC",
    "REFEREE_OK_RC",
    "NO_REFEREE_RC",
]
