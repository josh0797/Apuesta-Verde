"""
MLB Under Veto Layer
====================

Última línea de defensa contra Unders MLB con sesgo perdedor.

Cualquier capa del pipeline que recomiende un Under (full game / F5 /
team total / NRFI, etc.) debe consultar `evaluate_under_veto()` antes
de finalizar el pick. Si el resultado es `veto=True` (severity BLOCKED),
el Under se descarta o se reemplaza por un mercado alternativo.

Diseño
------
- **Función pura**: sin I/O, determinística, fail-soft frente a datos
  faltantes (ningún campo es obligatorio).
- **Single source of truth**: todas las capas (`mlb_pregame_analytics`,
  `baseball_runs_rescue`, `mlb_under_market_selector`) consumen el mismo
  módulo. Una sola regla, una sola lista de razones.
- **Mapper de compatibilidad**: `build_under_veto_context(profile)`
  toma el `baseballHistoricalProfile` actual y emite la estructura
  esperada (`home_pitcher`, `away_pitcher`, `park`, bullpen), incluyendo
  un `quality_score` heurístico derivado de ERA + WHIP. No se inventa
  xERA: si la fuente no existe, la regla correspondiente queda inactiva.

Severidades
-----------
- ``BLOCKED``  → el Under se rechaza por completo.
- ``WARNING``  → el Under se permite pero la confianza pierde puntos
                 (penalty 8 o 15 según gravedad acumulada).
- ``PASS``     → ninguna razón activa, Under sigue normalmente.
"""

from __future__ import annotations

from typing import Optional


# ── Catálogo de razones (clave → mensaje humano en español) ───────────
VETO_REASONS = {
    "INSUFFICIENT_PITCHER_SAMPLE":  "Pitcher con <3 aperturas — muestra no confiable",
    "PITCHER_OVERPERFORMING_HOME":  "Abridor local sobre-rinde (ERA mucho mejor que xERA) — regresión inminente",
    "PITCHER_OVERPERFORMING_AWAY":  "Abridor visitante sobre-rinde (ERA mucho mejor que xERA) — regresión inminente",
    "WEAK_PITCHER_QUALITY":         "Pitcher quality score por debajo del umbral del parque",
    "OFFENSIVE_PARK_THIN_MARGIN":   "Margen <1.2 carreras en parque ofensivo (factor >1.10)",
    "NO_PITCHER_DATA":              "Sin datos de pitcher en el profile — Under no auditable",
    "BULLPEN_BLOWUP_RISK":          "Bullpen ERA últimos 7d >5.00 — riesgo de explosión tardía",
    "RECENT_OVER_PATTERN":          "Últimos enfrentamientos H2H promediaron ≥9.0 carreras",
}

# Razones que por sí solas bloquean el Under (sin necesidad de acumular).
_BLOCKING_REASONS = {
    "INSUFFICIENT_PITCHER_SAMPLE",
    "NO_PITCHER_DATA",
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def derive_pitcher_quality_score(
    era:  Optional[float],
    whip: Optional[float],
) -> float:
    """Heurística simple ERA + WHIP → quality_score en [0.0, 1.0].

    Base = 0.50 (pitcher promedio). ERA aporta hasta ±0.25, WHIP hasta
    ±0.20. Cuando ambos valores faltan, devuelve 0.0 para que la regla
    NO_PITCHER_DATA del veto se active limpiamente.
    """
    if era is None and whip is None:
        return 0.0
    score = 0.50
    if era is not None:
        try:
            era_f = float(era)
            if era_f <= 2.75:
                score += 0.25
            elif era_f <= 3.50:
                score += 0.18
            elif era_f <= 4.25:
                score += 0.10
            elif era_f <= 5.00:
                score += 0.00
            else:
                score -= 0.12
        except (TypeError, ValueError):
            pass
    if whip is not None:
        try:
            whip_f = float(whip)
            if whip_f <= 1.05:
                score += 0.20
            elif whip_f <= 1.20:
                score += 0.14
            elif whip_f <= 1.35:
                score += 0.06
            elif whip_f <= 1.50:
                score += 0.00
            else:
                score -= 0.10
        except (TypeError, ValueError):
            pass
    return round(_clamp(score, 0.0, 1.0), 3)


def _pick_first_int(d: dict, *keys: str) -> int:
    """Devuelve el primer valor entero válido entre `keys` (0 si nada)."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            iv = int(v)
            if iv >= 0:
                return iv
        except (TypeError, ValueError):
            continue
    return 0


def build_under_veto_context(profile: Optional[dict]) -> dict:
    """Mapper de compatibilidad: traduce `baseballHistoricalProfile`
    actual al shape esperado por `evaluate_under_veto()`.

    Política:
      - Si una métrica no existe en la fuente, se omite (None / 0). La
        regla del veto correspondiente queda inactiva por fail-soft.
      - `xera` NUNCA se inventa: si no hay fuente, no se incluye.
      - `quality_score` se deriva de ERA+WHIP cuando ambos faltan vale 0
        y dispara NO_PITCHER_DATA correctamente.
    """
    profile = profile or {}
    pitching = profile.get("pitching") or {}
    hs = pitching.get("homeStarter") or {}
    as_ = pitching.get("awayStarter") or {}
    hb = pitching.get("homeBullpen") or {}
    ab = pitching.get("awayBullpen") or {}

    def _starter_block(s: dict) -> dict:
        era  = s.get("era")
        whip = s.get("whip")
        quality = derive_pitcher_quality_score(era, whip)
        games = _pick_first_int(s, "games_pitched", "gamesStarted", "starts",
                                "gamesPlayed", "appearances")
        block = {
            "name":          s.get("name"),
            "era":           era,
            "whip":          whip,
            "games_pitched": games,
            "quality_score": quality,
        }
        # xERA: solo si la fuente ya la trae (no se inventa).
        if s.get("xera") is not None:
            block["xera"] = s.get("xera")
        return block

    def _bullpen_block(b: dict) -> dict:
        return {
            "era_7d":       b.get("era_7d") or b.get("era7d") or b.get("recent_era"),
            "fatigue_score": b.get("fatigueScore") or b.get("fatigue_score"),
            "fatigue_label": b.get("fatigueLabel") or b.get("fatigue_label"),
        }

    # Park factor: aceptar varias rutas comunes y default 1.0.
    park = profile.get("park") or {}
    context_block = profile.get("context") or {}
    park_factor = (
        park.get("run_factor")
        or park.get("runFactor")
        or context_block.get("parkFactor")
        or context_block.get("park_factor")
        or 1.0
    )
    try:
        park_factor = float(park_factor)
    except (TypeError, ValueError):
        park_factor = 1.0

    # H2H reciente (promedio de carreras) — si está disponible.
    combined = profile.get("combined") or {}
    recent_h2h = combined.get("h2hTotalRunsAvg") or combined.get("h2h_recent_runs_avg")

    return {
        "home_pitcher":         _starter_block(hs),
        "away_pitcher":         _starter_block(as_),
        "home_bullpen":         _bullpen_block(hb),
        "away_bullpen":         _bullpen_block(ab),
        "park":                 {"run_factor": park_factor},
        "recent_h2h_avg_runs":  recent_h2h,
        # Provenance / debug
        "_source":              "mapped_from_profile.pitching",
        "_xera_available":      (hs.get("xera") is not None) or (as_.get("xera") is not None),
    }


def evaluate_under_veto(
    *,
    pitcher_home:        Optional[dict],
    pitcher_away:        Optional[dict],
    park:                Optional[dict],
    book_total:          Optional[float],
    expected_runs:       Optional[float],
    bullpen_home:        Optional[dict] = None,
    bullpen_away:        Optional[dict] = None,
    recent_h2h_avg_runs: Optional[float] = None,
) -> dict:
    """Evalúa todas las reglas del veto y devuelve el verdicto.

    Retorna:
      {
        "veto":               bool,            # True si severity == BLOCKED
        "veto_reasons":       list[str],       # códigos crudos
        "severity":           "BLOCKED" | "WARNING" | "PASS",
        "confidence_penalty": int,             # puntos a restar si WARNING
        "explanation":        str,             # texto humano en español
        "debug":              dict,             # campos auxiliares para UI
      }
    """
    pitcher_home = pitcher_home or {}
    pitcher_away = pitcher_away or {}
    park         = park or {}
    bullpen_home = bullpen_home or {}
    bullpen_away = bullpen_away or {}

    reasons: list[str] = []

    # 1) Muestra mínima de aperturas.
    h_starts = int(pitcher_home.get("games_pitched") or 0)
    a_starts = int(pitcher_away.get("games_pitched") or 0)
    if h_starts < 3 or a_starts < 3:
        reasons.append("INSUFFICIENT_PITCHER_SAMPLE")

    # 2) Overperforming (ERA-xERA divergencia). Solo activa si la fuente
    #    proporciona xera (no se inventa).
    for label, p in (("HOME", pitcher_home), ("AWAY", pitcher_away)):
        era  = p.get("era")
        xera = p.get("xera")
        if era is None or xera is None:
            continue
        try:
            if float(xera) - float(era) >= 1.0:
                reasons.append(f"PITCHER_OVERPERFORMING_{label}")
        except (TypeError, ValueError):
            continue

    # 3) Quality score con umbral dinámico por parque.
    try:
        park_factor = float(park.get("run_factor") or 1.0)
    except (TypeError, ValueError):
        park_factor = 1.0
    min_q = 0.65 if park_factor > 1.10 else 0.55

    h_q = float(pitcher_home.get("quality_score") or 0)
    a_q = float(pitcher_away.get("quality_score") or 0)
    if (h_q > 0 and h_q < min_q) or (a_q > 0 and a_q < min_q):
        reasons.append("WEAK_PITCHER_QUALITY")
    if h_q == 0 and a_q == 0:
        reasons.append("NO_PITCHER_DATA")

    # 4) Buffer en parque ofensivo.
    if park_factor > 1.10 and expected_runs is not None and book_total is not None:
        try:
            gap = float(book_total) - float(expected_runs)
            if gap < 1.2:
                reasons.append("OFFENSIVE_PARK_THIN_MARGIN")
        except (TypeError, ValueError):
            pass

    # 5) Bullpen blow-up risk (ERA 7d > 5.00).
    def _bp_era(b: dict) -> float:
        v = b.get("era_7d")
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    if _bp_era(bullpen_home) > 5.0 or _bp_era(bullpen_away) > 5.0:
        reasons.append("BULLPEN_BLOWUP_RISK")

    # 6) H2H reciente con totales altos.
    if recent_h2h_avg_runs is not None:
        try:
            if float(recent_h2h_avg_runs) >= 9.0:
                reasons.append("RECENT_OVER_PATTERN")
        except (TypeError, ValueError):
            pass

    # ── Severidad final ──────────────────────────────────────────────
    has_blocking = any(r in _BLOCKING_REASONS for r in reasons)
    has_overperform = any(r.startswith("PITCHER_OVERPERFORMING_") for r in reasons)

    if has_blocking or len(reasons) >= 3:
        severity = "BLOCKED"
        penalty = 0
    elif has_overperform or len(reasons) >= 2:
        severity = "WARNING"
        penalty = 15
    elif reasons:
        severity = "WARNING"
        penalty = 8
    else:
        severity = "PASS"
        penalty = 0

    explanation = "; ".join(VETO_REASONS.get(r, r) for r in reasons[:3])

    return {
        "veto":               severity == "BLOCKED",
        "veto_reasons":       reasons,
        "severity":           severity,
        "confidence_penalty": penalty,
        "explanation":        explanation,
        "debug": {
            "home_pitcher_quality_score": h_q,
            "away_pitcher_quality_score": a_q,
            "home_pitcher_games_pitched": h_starts,
            "away_pitcher_games_pitched": a_starts,
            "park_factor":                park_factor,
            "min_quality_threshold":      min_q,
            "xera_available":             any(
                p.get("xera") is not None for p in (pitcher_home, pitcher_away)
            ),
        },
    }
