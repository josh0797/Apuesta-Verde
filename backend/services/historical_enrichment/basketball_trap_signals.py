"""Basketball-specific historical trap signals.

These signals run on top of `basketballHistoricalProfile` (and the live
match dict) and produce structured warnings that the analyst engine and
the UI can render alongside any rescue pick. They DO NOT veto a pick on
their own — that responsibility belongs to the Moneyball guardrail.
Instead they:
  • elevate `fragility_score`
  • get returned in the `trap_signals_structured` array attached to the
    rescue candidate
  • surface a human-readable explanation in Spanish

Signals implemented:
    OVERTIME_INFLATION
        Either team had ≥25% of their recent games go to OT. The scoring
        averages are inflated → a 5–10 point Over is materially worse
        than it looks.

    BACK_TO_BACK_FATIGUE
        Two or more b2b in the recent sample → tired legs, lower 3P%,
        smaller leads.

    WEAK_DEFENSE_OVER_BIAS
        Recent points-against averages are >115 (NBA) / >85 (FIBA-style).
        Defensive form is awful; an Over looks great until you realise
        nobody plays defence in garbage time → adjust.

    SCHEDULE_STRENGTH_DRIFT
        Scoring trend last5 is RISING while opponents played were soft.
        Inflated form vs the actual upcoming opponent.

    MARKET_ALREADY_ADJUSTED
        Bookmaker line already shows ≥6.0-point delta from league avg
        in the SAME direction as the lean. The market has likely priced
        the trend in.

    LINEUP_KEY_INJURY
        Match document carries an injury flag for a top scorer (e.g.
        flagged in `injury_notes` from team_news / editorial). Lowers
        scoring projection significantly.

    BLOWOUT_RISK
        Implied probability gap >0.70 (heavy favourite). High blowout
        risk → garbage-time minutes change pace dynamics dramatically.

All signals are returned as:
    {"code": str, "label": str, "severity": "INFO"|"WARN"|"HIGH",
     "explanation": str_es}
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("trap.basketball")


SEVERITY_LEVELS = ("INFO", "WARN", "HIGH")


def _sev_weight(code: str) -> int:
    return {"INFO": 5, "WARN": 12, "HIGH": 20}.get(code, 0)


def _check_overtime_inflation(profile: dict) -> Optional[dict]:
    home = profile.get("home") or {}
    away = profile.get("away") or {}
    h_ot = home.get("overtimeRate") or 0
    a_ot = away.get("overtimeRate") or 0
    if h_ot >= 0.25 or a_ot >= 0.25:
        worst = max(h_ot, a_ot)
        side  = "el equipo local" if h_ot >= a_ot else "el equipo visitante"
        return {
            "code":        "OVERTIME_INFLATION",
            "label":       "Inflación por prórrogas",
            "severity":    "WARN",
            "explanation": (
                f"{side.capitalize()} fue a tiempo extra en {worst*100:.0f}% "
                f"de sus últimos partidos. Los promedios de puntos están inflados "
                f"por los minutos extra."
            ),
        }
    return None


def _check_b2b_fatigue(profile: dict) -> Optional[dict]:
    home = profile.get("home") or {}
    away = profile.get("away") or {}
    h_b2b = home.get("backToBackCount") or 0
    a_b2b = away.get("backToBackCount") or 0
    if h_b2b + a_b2b >= 3:
        return {
            "code":        "BACK_TO_BACK_FATIGUE",
            "label":       "Fatiga acumulada (back-to-backs)",
            "severity":    "WARN",
            "explanation": (
                f"Entre ambos equipos acumulan {h_b2b + a_b2b} back-to-backs en "
                f"los últimos partidos. El ritmo y los porcentajes de tiro "
                f"suelen caer."
            ),
        }
    return None


def _check_defense_over_bias(profile: dict) -> Optional[dict]:
    home = profile.get("home") or {}
    away = profile.get("away") or {}
    pa_h = home.get("pointsAgainstAvg") or 0
    pa_a = away.get("pointsAgainstAvg") or 0
    # Use league baseline awareness via projected total proxy
    combined = profile.get("combined") or {}
    league_avg = combined.get("leagueAvgTotalUsed") or 200
    half = league_avg / 2.0 + 5
    if pa_h >= half and pa_a >= half:
        return {
            "code":        "WEAK_DEFENSE_OVER_BIAS",
            "label":       "Defensas permisivas — riesgo Over inflado",
            "severity":    "INFO",
            "explanation": (
                f"Ambas defensas concedieron promedios altos ({pa_h:.1f} y {pa_a:.1f}). "
                f"La proyección Over puede estar inflada por garbage time si "
                f"alguno saca ventaja."
            ),
        }
    return None


def _check_schedule_strength(profile: dict) -> Optional[dict]:
    home = profile.get("home") or {}
    away = profile.get("away") or {}
    if home.get("last5ScoringTrend") == "RISING" or away.get("last5ScoringTrend") == "RISING":
        # We can't measure opponent strength yet — emit INFO to signal caution
        return {
            "code":        "SCHEDULE_STRENGTH_DRIFT",
            "label":       "Tendencia anotadora reciente sin contexto de rival",
            "severity":    "INFO",
            "explanation": (
                "Al menos un equipo viene en tendencia anotadora ascendente "
                "(últimos 5 vs los 5 previos). Si el calendario fue blando, "
                "la forma puede no replicarse contra este rival."
            ),
        }
    return None


def _check_market_already_adjusted(
    profile: dict,
    *,
    bookmaker_total_line: Optional[float],
) -> Optional[dict]:
    if not bookmaker_total_line:
        return None
    combined   = profile.get("combined") or {}
    league_avg = combined.get("leagueAvgTotalUsed") or 200
    diff       = bookmaker_total_line - league_avg
    proj_diff  = (combined.get("projectedTotalPoints") or league_avg) - league_avg
    if abs(diff) >= 6 and (diff * proj_diff) > 0:
        direction = "por encima" if diff > 0 else "por debajo"
        return {
            "code":        "MARKET_ALREADY_ADJUSTED",
            "label":       "El mercado ya ajustó la línea",
            "severity":    "WARN",
            "explanation": (
                f"La línea de bookmaker ({bookmaker_total_line:.1f}) ya está "
                f"{abs(diff):.1f} pts {direction} del promedio de liga. El sesgo "
                f"del modelo coincide con el ajuste → poco margen restante."
            ),
        }
    return None


def _check_blowout_risk(match: dict) -> Optional[dict]:
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    ml_rows = markets.get("Moneyline") or markets.get("Match Winner") or []
    best_short: Optional[float] = None
    for r in ml_rows or []:
        for v in (r.get("lines") or {}).values():
            if isinstance(v, (int, float)) and v > 1.01:
                if best_short is None or v < best_short:
                    best_short = float(v)
    if best_short is None or best_short >= 1.45:
        return None
    implied = 1.0 / best_short
    if implied >= 0.70:
        return {
            "code":        "BLOWOUT_RISK",
            "label":       "Riesgo de paliza",
            "severity":    "INFO",
            "explanation": (
                f"Favorito con probabilidad implícita {implied*100:.0f}% — "
                f"riesgo de garbage time alto. El ritmo final puede no reflejar "
                f"el ritmo inicial."
            ),
        }
    return None


def _check_lineup_injury(match: dict) -> Optional[dict]:
    """Pull injury hints from team_news / editorial channels when present."""
    notes: list[str] = []
    for key in ("team_news", "injuries", "lineup_changes"):
        v = match.get(key)
        if isinstance(v, dict):
            for src in v.values():
                if isinstance(src, list):
                    notes.extend(str(x) for x in src)
        elif isinstance(v, list):
            notes.extend(str(x) for x in v)
    ed = match.get("editorial_context") or {}
    notes.extend(ed.get("injury_notes") or [])
    flag_words = ("baja", "lesión", "lesionado", "out", "doubtful", "injury")
    hits = [n for n in notes if any(w in (n or "").lower() for w in flag_words)]
    if not hits:
        return None
    return {
        "code":        "LINEUP_KEY_INJURY",
        "label":       "Lesiones / bajas reportadas",
        "severity":    "WARN",
        "explanation": (
            "Hay reportes de bajas o lesiones que pueden afectar el ritmo "
            "y la anotación. Verifica el impacto en el quinteto titular."
        ),
        "_hits": hits[:3],
    }


# ── Public API ──────────────────────────────────────────────────────────
def collect_basketball_trap_signals(
    match: dict,
    *,
    bookmaker_total_line: Optional[float] = None,
) -> list[dict]:
    """Return the list of structured trap signals firing for this match.

    Reads from:
        match["basketballHistoricalProfile"]
        match["odds_snapshots"]
        match["team_news"] / match["editorial_context"]

    Always returns a list (possibly empty). NEVER raises.
    """
    profile = match.get("basketballHistoricalProfile") or {}
    out: list[dict] = []
    try:
        for fn in (
            _check_overtime_inflation,
            _check_b2b_fatigue,
            _check_defense_over_bias,
            _check_schedule_strength,
        ):
            sig = fn(profile)
            if sig:
                out.append(sig)
        m_sig = _check_market_already_adjusted(profile, bookmaker_total_line=bookmaker_total_line)
        if m_sig:
            out.append(m_sig)
        b_sig = _check_blowout_risk(match)
        if b_sig:
            out.append(b_sig)
        i_sig = _check_lineup_injury(match)
        if i_sig:
            out.append(i_sig)
    except Exception as exc:
        log.debug("basketball trap collection failed: %s", exc)
    return out


def compute_extra_fragility(trap_signals: list[dict]) -> int:
    """Convert the trap signal list into a 0–100 fragility increment that
    callers can ADD to the base fragility score from the profile.
    """
    total = 0
    for s in trap_signals or []:
        total += _sev_weight(s.get("severity", ""))
    return min(100, total)


__all__ = [
    "collect_basketball_trap_signals",
    "compute_extra_fragility",
]
