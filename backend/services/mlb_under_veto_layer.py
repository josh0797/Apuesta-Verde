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
    "POWER_BAT_PRESENT":            "Equipo con OPS > 0.770 — riesgo de inning explosivo",
    "BULLPEN_PITCH_STRESS_HIGH":    "Bullpen con pitch-stress >1.5 (≥67 pitches en 48h) — fatiga real",
}

# Umbral OPS para considerar a un equipo "power bat" (regresión a la
# media históricamente alrededor del 0.770 league-average).
POWER_BAT_OPS_THRESHOLD = 0.770

# Razones que por sí solas bloquean el Under (sin necesidad de acumular).
#
# NOTA — POWER_BAT_PRESENT ya NO es bloqueante (re-balance acordado con
# usuario, run #2). OPS alto por sí solo no debe vetar un Under contra
# starters de élite. La señal sigue activa como WARNING acumulable y
# alimenta `compute_explosive_inning_risk()` que decide si el Under se
# debe degradar (MEDIUM) o reemplazar (HIGH + triple gate Over).
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

    # FIX #2 — extract team OPS from the batting profile so the veto can
    # apply POWER_BAT_PRESENT. Profile shape (current): `batting.home.ops`
    # / `batting.away.ops`. We accept several spellings so any upstream
    # normaliser change doesn't silently drop the signal.
    batting = profile.get("batting") or {}
    home_bat = batting.get("home") or {}
    away_bat = batting.get("away") or {}

    def _ops(b: dict) -> Optional[float]:
        for k in ("ops", "OPS", "team_ops", "teamOps", "season_ops"):
            v = b.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    home_team_ops = _ops(home_bat)
    away_team_ops = _ops(away_bat)

    # FIX #3 — surface real bullpen workload (pitch_stress_index) so the
    # veto layer can flag fresh-bullpen exhaustion. The orchestrator
    # injects `home_bullpen_real` / `away_bullpen_real` directly into the
    # profile dict before calling us.
    home_bp_real = profile.get("home_bullpen_real") or {}
    away_bp_real = profile.get("away_bullpen_real") or {}

    return {
        "home_pitcher":         _starter_block(hs),
        "away_pitcher":         _starter_block(as_),
        "home_bullpen":         _bullpen_block(hb),
        "away_bullpen":         _bullpen_block(ab),
        "park":                 {"run_factor": park_factor},
        "recent_h2h_avg_runs":  recent_h2h,
        "home_team_ops":        home_team_ops,
        "away_team_ops":        away_team_ops,
        "home_bullpen_real":    home_bp_real,
        "away_bullpen_real":    away_bp_real,
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
    # FIX #1 — power-bat detection.
    home_team_ops:       Optional[float] = None,
    away_team_ops:       Optional[float] = None,
    # FIX #3 — real bullpen workload (pitch_stress_index from MLB Stats API).
    home_bullpen_real:   Optional[dict] = None,
    away_bullpen_real:   Optional[dict] = None,
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

    # 7) Power-bat detection (FIX #1 — Yankees @ A's 13-8 fail).
    # Any team with OPS > 0.770 carries non-trivial explosive-inning risk
    # even against a quality starter. We blocked Under once either side
    # crosses the threshold so a single 5-run frame doesn't sink the pick.
    for label, ops in (("HOME", home_team_ops), ("AWAY", away_team_ops)):
        if ops is None:
            continue
        try:
            if float(ops) > POWER_BAT_OPS_THRESHOLD:
                reasons.append("POWER_BAT_PRESENT")
                break   # solo una vez aunque ambos sean power
        except (TypeError, ValueError):
            continue

    # 8) Real bullpen pitch-stress (FIX #3 — Twins @ Pirates case).
    # `pitch_stress_index > 1.5` means the bullpen threw >67 pitches in the
    # last 48h, regardless of season ERA. We add a separate reason so the
    # UI can show this distinctly from BULLPEN_BLOWUP_RISK.
    for bp_real in (home_bullpen_real, away_bullpen_real):
        if not isinstance(bp_real, dict):
            continue
        try:
            psi = float(bp_real.get("pitch_stress_index") or 0)
        except (TypeError, ValueError):
            psi = 0.0
        if psi > 1.5:
            reasons.append("BULLPEN_PITCH_STRESS_HIGH")
            break

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


# ════════════════════════════════════════════════════════════════════════════
# EXPLOSIVE INNING RISK SCORE
# ════════════════════════════════════════════════════════════════════════════
#
# Modelo cuantitativo (0-100+) que mide la probabilidad de que un Under
# explote por un inning grande (5+ carreras). Suma señales de OPS,
# bullpen (ERA7d + pitch_stress_index), active series H2H, park, gap
# entre línea de book y expected runs, y Script Survival.
#
# Calibración (acordada con el usuario):
#   LOW:    0–49   → Under permitido normal.
#   MEDIUM: 50–84  → -10 pts confianza, preferir F5 Under sobre FG Under,
#                    NO flip a Over.
#   HIGH:   85+    → bloquear Full Game Under. Evaluar Over protegido,
#                    flip SOLO si los 3 gates se cumplen:
#                      over_survival.score    > 55  AND
#                      best_over_market.edge  >= 1.0 AND
#                      best_over_market.score >= 60
#                    Si no cumple → descartar partido (no forzar Over).
#
# Pesos por categoría (single category, no double count):
#   OPS:        max>.800 → 20  |  max>.770 → 15
#   Bullpen:    era_7d>5.00 → +15  (suma con) pitch_stress>2.0 → 20  |  >1.5 → 10
#   Series:     h2h_avg>12 → 20  |  >10 → 10
#   Park:       run_factor>1.10 → 10
#   Line gap:   gap<1.0 → 20  |  gap<1.5 → 15   (gap = book_total - expected_runs)
#   Script:     survival<50 → 20  |  <60 → 15


# Códigos de razón (clave → mensaje humano en español).
EXPLOSIVE_RISK_REASONS = {
    "POWER_BAT_MAX_OPS_GT_800":     "Equipo con OPS > 0.800 (slugging extremo)",
    "POWER_BAT_MAX_OPS_GT_770":     "Equipo con OPS > 0.770 (slugging por encima del league avg)",
    "BULLPEN_ERA7D_GT_5":           "Bullpen ERA últimos 7d > 5.00",
    "BULLPEN_PITCH_STRESS_GT_2":    "Bullpen con pitch-stress > 2.0 (≥90 pitches/48h, fatiga severa)",
    "BULLPEN_PITCH_STRESS_GT_1_5":  "Bullpen con pitch-stress > 1.5 (≥67 pitches/48h)",
    "ACTIVE_SERIES_H2H_GT_12":      "Active series H2H promedio > 12 carreras",
    "ACTIVE_SERIES_H2H_GT_10":      "Active series H2H promedio > 10 carreras",
    "HITTER_PARK_FACTOR":           "Parque hitter-friendly (run factor > 1.10)",
    "LINE_GAP_LT_1_0":              "Gap línea-modelo < 1.0 carreras (margen casi nulo)",
    "LINE_GAP_LT_1_5":              "Gap línea-modelo < 1.5 carreras (margen frágil)",
    "SCRIPT_SURVIVAL_LT_50":        "Script Survival < 50 (guion Under colapsa muy probable)",
    "SCRIPT_SURVIVAL_LT_60":        "Script Survival < 60 (guion Under inestable)",
}


def _to_float(v) -> Optional[float]:
    """Coerce arbitrary value to float or return None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_explosive_inning_risk(
    *,
    home_team_ops:        Optional[float] = None,
    away_team_ops:        Optional[float] = None,
    bullpen_home:         Optional[dict]  = None,
    bullpen_away:         Optional[dict]  = None,
    recent_h2h_avg_runs:  Optional[float] = None,
    park_factor:          Optional[float] = None,
    expected_runs:        Optional[float] = None,
    book_total:           Optional[float] = None,
    script_survival:      Optional[float] = None,
) -> dict:
    """Calcula Explosive Inning Risk Score (0-100+) + nivel + razones.

    Función pura, fail-soft: si una señal es None se omite la categoría
    correspondiente sin penalizar el cálculo.

    `bullpen_home` / `bullpen_away` aceptan ``era_7d`` y/o
    ``pitch_stress_index`` en el mismo dict. El orchestrator suele
    fusionar ``home_bullpen`` con ``home_bullpen_real`` antes de llamar.

    Retorno
    -------
    {
        "risk_score":  int,                       # 0-100+
        "risk_level":  "LOW" | "MEDIUM" | "HIGH",
        "reasons":     [str, ...],                # códigos crudos
        "explanation": str,                       # texto humano (es)
        "breakdown":   {category: pts, ...},      # debug por categoría
    }
    """
    reasons: list[str] = []
    breakdown: dict[str, int] = {
        "ops":             0,
        "bullpen":         0,
        "active_series":   0,
        "park":            0,
        "line_gap":        0,
        "script_survival": 0,
    }

    # ── 1) OPS (single category, no double count) ───────────────────
    h_ops = _to_float(home_team_ops)
    a_ops = _to_float(away_team_ops)
    ops_values = [v for v in (h_ops, a_ops) if v is not None]
    if ops_values:
        max_ops = max(ops_values)
        if max_ops > 0.800:
            breakdown["ops"] = 20
            reasons.append("POWER_BAT_MAX_OPS_GT_800")
        elif max_ops > POWER_BAT_OPS_THRESHOLD:  # > 0.770
            breakdown["ops"] = 15
            reasons.append("POWER_BAT_MAX_OPS_GT_770")

    # ── 2) Bullpen (era_7d + pitch_stress; no-double en PSI) ────────
    def _bp_signals(b):
        if not isinstance(b, dict):
            return None, None
        return _to_float(b.get("era_7d")), _to_float(b.get("pitch_stress_index"))

    eras: list[float] = []
    psis: list[float] = []
    for b in (bullpen_home, bullpen_away):
        era_7d, psi = _bp_signals(b)
        if era_7d is not None:
            eras.append(era_7d)
        if psi is not None:
            psis.append(psi)

    bp_pts = 0
    if eras and max(eras) > 5.0:
        bp_pts += 15
        reasons.append("BULLPEN_ERA7D_GT_5")
    if psis:
        max_psi = max(psis)
        # no double count: aplicar solo el mayor umbral cruzado.
        if max_psi > 2.0:
            bp_pts += 20
            reasons.append("BULLPEN_PITCH_STRESS_GT_2")
        elif max_psi > 1.5:
            bp_pts += 10
            reasons.append("BULLPEN_PITCH_STRESS_GT_1_5")
    breakdown["bullpen"] = bp_pts

    # ── 3) Active series H2H (single category) ──────────────────────
    h2h = _to_float(recent_h2h_avg_runs)
    if h2h is not None:
        if h2h > 12:
            breakdown["active_series"] = 20
            reasons.append("ACTIVE_SERIES_H2H_GT_12")
        elif h2h > 10:
            breakdown["active_series"] = 10
            reasons.append("ACTIVE_SERIES_H2H_GT_10")

    # ── 4) Park factor ──────────────────────────────────────────────
    pf = _to_float(park_factor)
    if pf is not None and pf > 1.10:
        breakdown["park"] = 10
        reasons.append("HITTER_PARK_FACTOR")

    # ── 5) Line gap (single category, no double count) ─────────────
    bt = _to_float(book_total)
    er = _to_float(expected_runs)
    if bt is not None and er is not None:
        gap = bt - er
        if gap < 1.0:
            breakdown["line_gap"] = 20
            reasons.append("LINE_GAP_LT_1_0")
        elif gap < 1.5:
            breakdown["line_gap"] = 15
            reasons.append("LINE_GAP_LT_1_5")

    # ── 6) Script Survival (single category) ────────────────────────
    ss = _to_float(script_survival)
    if ss is not None:
        if ss < 50:
            breakdown["script_survival"] = 20
            reasons.append("SCRIPT_SURVIVAL_LT_50")
        elif ss < 60:
            breakdown["script_survival"] = 15
            reasons.append("SCRIPT_SURVIVAL_LT_60")

    # ── Total + nivel ──────────────────────────────────────────────
    risk_score = sum(breakdown.values())
    if risk_score >= 85:
        level = "HIGH"
    elif risk_score >= 50:
        level = "MEDIUM"
    else:
        level = "LOW"

    explanation = "; ".join(
        EXPLOSIVE_RISK_REASONS.get(r, r) for r in reasons
    )

    return {
        "risk_score":  int(risk_score),
        "risk_level":  level,
        "reasons":     reasons,
        "explanation": explanation,
        "breakdown":   breakdown,
    }
