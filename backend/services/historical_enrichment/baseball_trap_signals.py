"""Baseball-specific historical trap signals.

Mirrors `basketball_trap_signals.py`. These run on top of
`baseballHistoricalProfile` and the live match dict, and emit structured
warnings the analyst engine + UI can render alongside any rescue pick.

Signals implemented:
    BULLPEN_FATIGUE_OVERSTRESS
        One or both bullpens carry fatigueScore >= 60 (heavy IP load over
        the last 3 days). Adds Over pressure to late innings and inflates
        bust risk on a Pitcher-led Under play.

    PRIOR_SERIES_INFLATION
        Team's last5 runs avg is well above the season runs/G (≥2 runs).
        The recent production may be inflated by a soft prior series →
        don't blindly project the trend forward.

    COLD_OFFENSE_WITH_INFLATED_LINE
        Team failed to reach the team-total threshold in ≥50% of recent
        games. If the bookmaker line still sits over the team-total,
        there's value on the Under team-total or Run Line +1.5.

    PITCHER_NAME_INFLATION
        Probable starter has elite season ERA (<3.00) BUT last 5 starts
        show ERA >4.00 — the market may still price the name, not the
        recent form. Caller must pass the recent ERA via the match dict
        (`home_probable_last5_era` / `away_probable_last5_era`).

    F5_OVER_UNDER_MISMATCH
        Combined F5 lean disagrees with full-game lean by 2+ ticks.
        Useful: F5 Under + Game Over is a known pattern (slow starts +
        late bullpen meltdown).

    EXTRA_INNINGS_FATIGUE
        ≥2 extra-inning games in recent sample → real fatigue load not
        always reflected in box scores.

    WEATHER_PARK_NOT_INCLUDED
        Always emits INFO when weather/park data isn't available yet,
        reminding the analyst that the projection is weather-blind.

Each signal:
    {"code": str, "label": str, "severity": "INFO"|"WARN"|"HIGH",
     "explanation": str_es}
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("trap.baseball")

SEVERITY_LEVELS = ("INFO", "WARN", "HIGH")


def _sev_weight(code: str) -> int:
    return {"INFO": 5, "WARN": 12, "HIGH": 20}.get(code, 0)


def _check_bullpen_fatigue(profile: dict) -> Optional[dict]:
    pitching = profile.get("pitching") or {}
    h_bp = (pitching.get("homeBullpen") or {})
    a_bp = (pitching.get("awayBullpen") or {})
    h_score = h_bp.get("fatigueScore") or 0
    a_score = a_bp.get("fatigueScore") or 0
    if h_score < 60 and a_score < 60:
        return None
    sev = "HIGH" if max(h_score, a_score) >= 80 else "WARN"
    sides: list[str] = []
    if h_score >= 60:
        sides.append(f"local ({h_bp.get('gamesPlayedRecent', '?')} juegos en {h_bp.get('lookbackDays', 3)}d)")
    if a_score >= 60:
        sides.append(f"visitante ({a_bp.get('gamesPlayedRecent', '?')} juegos en {a_bp.get('lookbackDays', 3)}d)")
    return {
        "code":        "BULLPEN_FATIGUE_OVERSTRESS",
        "label":       "Bullpen cargado",
        "severity":    sev,
        "explanation": (
            f"El bullpen llega cargado: {' y '.join(sides)}. Las entradas finales "
            f"pueden volverse caóticas; el riesgo sube si la apuesta depende del "
            f"cierre limpio del pitcheo."
        ),
    }


def _check_prior_series_inflation(profile: dict) -> Optional[dict]:
    inflated_sides: list[str] = []
    for side_key, label in (("home", "local"), ("away", "visitante")):
        block = profile.get(side_key) or {}
        season_rpg = (block.get("runsForAvg") or 0)
        last5 = block.get("last5RunsForAvg")
        if isinstance(last5, (int, float)) and isinstance(season_rpg, (int, float)):
            if last5 - season_rpg >= 2.0 and season_rpg > 0:
                inflated_sides.append(f"{label} ({last5:.1f} vs {season_rpg:.1f})")
    if not inflated_sides:
        return None
    return {
        "code":        "PRIOR_SERIES_INFLATION",
        "label":       "Producción reciente inflada",
        "severity":    "WARN",
        "explanation": (
            f"Producción ofensiva reciente por encima de la media de temporada: "
            f"{' y '.join(inflated_sides)}. La serie previa pudo inflar el promedio — "
            f"la línea actual puede no reflejar la oferta real del enfrentamiento."
        ),
    }


def _check_cold_offense(profile: dict, bookmaker_total_line: Optional[float]) -> Optional[dict]:
    cold_sides: list[str] = []
    for side_key, label in (("home", "local"), ("away", "visitante")):
        block = profile.get(side_key) or {}
        failed = block.get("failedToReachTeamTotalRate") or 0
        if failed >= 0.55:
            cold_sides.append(label)
    if not cold_sides:
        return None
    extra = ""
    if bookmaker_total_line and bookmaker_total_line:
        extra = f" La línea de bookmaker ({bookmaker_total_line:.1f}) podría estar inflada."
    return {
        "code":        "COLD_OFFENSE_WITH_INFLATED_LINE",
        "label":       "Ofensiva fría",
        "severity":    "WARN",
        "explanation": (
            f"Ofensiva fría reciente: el equipo {' y '.join(cold_sides)} no alcanzó "
            f"su team-total en más del 50% de los últimos partidos.{extra} "
            f"Considera Under team-total o Run Line +1.5."
        ),
    }


def _check_pitcher_name_inflation(profile: dict, match: dict) -> Optional[dict]:
    pitching = profile.get("pitching") or {}
    out_sides: list[str] = []
    for side_key, label, recent_key in (
        ("homeStarter", "local",     "home_probable_last5_era"),
        ("awayStarter", "visitante", "away_probable_last5_era"),
    ):
        sp = pitching.get(side_key) or {}
        season_era = sp.get("era")
        recent_era = match.get(recent_key)
        if isinstance(season_era, (int, float)) and isinstance(recent_era, (int, float)):
            if season_era < 3.0 and recent_era > 4.0:
                out_sides.append(f"{label} ({sp.get('name','?')}: temp {season_era:.2f} | últ.5 {recent_era:.2f})")
    if not out_sides:
        return None
    return {
        "code":        "PITCHER_NAME_INFLATION",
        "label":       "El mercado sobrevalora el nombre",
        "severity":    "WARN",
        "explanation": (
            f"Abridor con ERA de temporada elite pero recientes aperturas mediocres: "
            f"{' y '.join(out_sides)}. El mercado puede estar pricing el nombre, no la forma actual."
        ),
    }


def _check_f5_mismatch(profile: dict) -> Optional[dict]:
    combined = profile.get("combined") or {}
    full_lean = combined.get("overUnderLean")
    f5_lean = combined.get("f5Lean")
    if not full_lean or not f5_lean or full_lean == "NEUTRAL" or f5_lean == "NEUTRAL":
        return None
    if full_lean == f5_lean:
        return None
    return {
        "code":        "F5_OVER_UNDER_MISMATCH",
        "label":       "Discrepancia F5 vs partido completo",
        "severity":    "INFO",
        "explanation": (
            f"Lean del partido completo ({full_lean}) difiere del F5 ({f5_lean}). "
            f"Patrón típico: inicios contenidos + bullpen volátil → considera "
            f"jugar el F5 por separado."
        ),
    }


def _check_extra_innings_fatigue(profile: dict) -> Optional[dict]:
    h = (profile.get("home") or {}).get("extraInningGames") or 0
    a = (profile.get("away") or {}).get("extraInningGames") or 0
    if h + a < 2:
        return None
    return {
        "code":        "EXTRA_INNINGS_FATIGUE",
        "label":       "Fatiga por entradas extras",
        "severity":    "INFO",
        "explanation": (
            f"Entre ambos equipos acumulan {h + a} juegos de entradas extras en el "
            f"sample reciente. La carga acumulada no siempre se ve en box scores."
        ),
    }


def _check_weather_park_blind(profile: dict, match: dict) -> Optional[dict]:
    if match.get("weather") or match.get("park_factor") or match.get("venue_factor"):
        return None
    return {
        "code":        "WEATHER_PARK_NOT_INCLUDED",
        "label":       "Sin datos de clima/parque",
        "severity":    "INFO",
        "explanation": (
            "El motor no recibió datos de clima ni park-factor — la proyección "
            "podría desviarse en parques extremos (Coors, etc.) o con viento fuerte."
        ),
    }


def collect_baseball_trap_signals(
    match: dict,
    *,
    bookmaker_total_line: Optional[float] = None,
) -> list[dict]:
    """Return the list of structured trap signals firing for this match."""
    profile = match.get("baseballHistoricalProfile") or {}
    out: list[dict] = []
    try:
        sig = _check_bullpen_fatigue(profile)
        if sig:
            out.append(sig)
        sig = _check_prior_series_inflation(profile)
        if sig:
            out.append(sig)
        sig = _check_cold_offense(profile, bookmaker_total_line)
        if sig:
            out.append(sig)
        sig = _check_pitcher_name_inflation(profile, match)
        if sig:
            out.append(sig)
        sig = _check_f5_mismatch(profile)
        if sig:
            out.append(sig)
        sig = _check_extra_innings_fatigue(profile)
        if sig:
            out.append(sig)
        sig = _check_weather_park_blind(profile, match)
        if sig:
            out.append(sig)
    except Exception as exc:
        log.debug("baseball trap collection failed: %s", exc)
    return out


def compute_extra_fragility(trap_signals: list[dict]) -> int:
    total = 0
    for s in trap_signals or []:
        total += _sev_weight(s.get("severity", ""))
    return min(100, total)


__all__ = [
    "collect_baseball_trap_signals",
    "compute_extra_fragility",
]
