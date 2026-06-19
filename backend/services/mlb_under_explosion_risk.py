"""
MLB Under Explosion Risk — Nivel 1 (Sprint D11).

Capa de riesgo enfocada en detectar volatilidad real, colapsos tempranos
y fragilidad de bullpen para evaluar picks de totales MLB (especialmente
Under). NO sustituye la proyección central — extiende `fragility`,
`explosive_tail_risk`, `survival_score`, `reason_codes` y `verdict`
cuando detecta condiciones peligrosas.

Caso motivador
--------------
Toronto Blue Jays @ Chicago Cubs · UNDER 9.5 recomendado con score 82
→ Final 16-2 Cubs (18 runs, ~2x línea). El modelo central no detectó:
  - Volatilidad del starter visitante combinada con
  - Lineup local explosivo + bullpen frágil
La nueva capa marca estos casos antes de pasar el pick.

Módulos
-------
1. `compute_starter_volatility(...)`            — score 0-100, bucket LOW/MEDIUM/HIGH/EXTREME
2. `compute_first_inning_collapse(...)`         — score 0-100, idem
3. `compute_recent_offensive_quality(team)`     — score 0-100, bucket COLD/NEUTRAL/HOT/EXPLOSIVE
4. `compute_lineup_explosiveness(team_lineup)`  — score 0-100, bucket LOW/AVG/STRONG/EXPLOSIVE
5. `aggregate_under_explosion_risk(...)`        — orquestador final con reason codes y
                                                  ajustes a fragility/tail_risk/survival.

Reglas de calidad
-----------------
* Fail-soft: ningún campo faltante bloquea el cálculo. Se usa neutral
  default y baja `confidence`. Los campos faltantes se reportan en
  `missing_fields`.
* No inventar datos: cuando falta una variable crítica, el score tiende
  al neutral (50) y la confidence baja.
* Distinción entre `drivers` (señales reales que mueven el score) y
  `missing_fields` (variables ausentes que bajan confidence).
* PURO: sin Mongo, sin APIs.
"""

from __future__ import annotations

import math
from typing import Any, Optional


# ── Helpers ─────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    return None if f is None else int(f)


def _bucket(score: float, breaks: list[tuple[float, str]]) -> str:
    """`breaks` ordenado ascendente: [(threshold, label_for_above)]."""
    label = breaks[0][1]
    for thr, lab in breaks:
        if score >= thr:
            label = lab
    return label


# ═════════════════════════════════════════════════════════════════════
# 1) Starter Volatility Profile
# ═════════════════════════════════════════════════════════════════════
def compute_starter_volatility(starter: Optional[dict]) -> dict:
    """Mide qué tan propenso es el abridor a permitir innings grandes.

    `starter` puede contener (todos opcionales):
      - whip, bb_pct, k_pct, hr_per_9, hard_hit_pct, barrel_pct,
        xwoba, era, fip, xera, xfip_era_gap
      - last5_starts: lista de hasta 5 dicts:
          {er, hits, walks, hr_allowed, ip, pitches, faced_4plus_er(bool),
           short_outing(bool, <5 IP), bb_3plus(bool), hr_2plus(bool)}

    Sin datos → score 50 (neutral), bucket MEDIUM, confidence 0.
    """
    if not isinstance(starter, dict):
        starter = {}

    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0  # neutral baseline
    contributions = 0  # cuántas variables aportaron

    def _add(
        key: str,
        value: Optional[float],
        thresholds: tuple[float, float, float],
        weights: tuple[float, float, float],
        driver_label: str,
    ) -> None:
        """thresholds = (low, mid, high). values ≥ high → +w3; ≥ mid → +w2; ≥ low → +w1; else → -w1."""
        nonlocal score, contributions
        if value is None:
            missing.append(key)
            return
        lo_t, mid_t, hi_t = thresholds
        w_lo, w_mid, w_hi = weights
        if value >= hi_t:
            score += w_hi
            drivers.append(f"{driver_label}_HIGH")
        elif value >= mid_t:
            score += w_mid
        elif value >= lo_t:
            score += w_lo
        else:
            score -= w_lo
        contributions += 1

    whip = _safe_float(starter.get("whip"))
    bb_pct = _safe_float(starter.get("bb_pct"))
    hr_per_9 = _safe_float(starter.get("hr_per_9"))
    hard_hit = _safe_float(starter.get("hard_hit_pct"))
    barrel = _safe_float(starter.get("barrel_pct"))
    xwoba = _safe_float(starter.get("xwoba"))
    xera_gap = _safe_float(starter.get("xera_fip_gap"))

    _add("whip", whip, (1.10, 1.25, 1.35), (3, 6, 10), "WHIP")
    _add("bb_pct", bb_pct, (7.0, 8.5, 9.5), (3, 6, 10), "BB_PCT")
    _add("hr_per_9", hr_per_9, (1.00, 1.20, 1.35), (3, 6, 10), "HR_PER_9")
    _add("hard_hit_pct", hard_hit, (36.0, 39.0, 42.0), (2, 4, 8), "HARD_HIT")
    _add("barrel_pct", barrel, (6.0, 7.5, 9.0), (2, 4, 8), "BARREL")
    _add("xwoba", xwoba, (0.300, 0.320, 0.340), (2, 4, 6), "xwOBA")
    _add("xera_fip_gap", xera_gap, (0.30, 0.50, 0.70), (1, 2, 4), "XERA_FIP_GAP")

    last5 = starter.get("last5_starts") or []
    if isinstance(last5, list) and last5:
        n_4plus = sum(
            1 for g in last5 if isinstance(g, dict) and g.get("faced_4plus_er")
        )
        n_short = sum(1 for g in last5 if isinstance(g, dict) and g.get("short_outing"))
        n_bb3 = sum(1 for g in last5 if isinstance(g, dict) and g.get("bb_3plus"))
        n_hr2 = sum(1 for g in last5 if isinstance(g, dict) and g.get("hr_2plus"))
        if n_4plus >= 2:
            score += 12
            drivers.append("L5_TWO_PLUS_4ER_STARTS")
        elif n_4plus >= 1:
            score += 5
        if n_short >= 2:
            score += 10
            drivers.append("L5_TWO_PLUS_SHORT_OUTINGS")
        elif n_short >= 1:
            score += 4
        if n_bb3 >= 2:
            score += 6
            drivers.append("L5_HIGH_WALK_STARTS")
        if n_hr2 >= 1:
            score += 6
            drivers.append("L5_TWO_HR_STARTS")
        contributions += 1
    else:
        missing.append("last5_starts")

    score = _clamp(score, 0, 100)
    bucket = _bucket(score, [(0, "LOW"), (40, "MEDIUM"), (62, "HIGH"), (80, "EXTREME")])
    # Confidence: cuanto más datos, mayor.
    # 8 variables × 1 + last5; max 9 → 100%.
    max_inputs = 8
    confidence = round(_clamp(contributions / max_inputs * 100, 0, 100), 1)
    return {
        "starter_volatility_score": round(score, 2),
        "bucket": bucket,
        "drivers": drivers,
        "missing_fields": missing,
        "confidence": confidence,
    }


# ═════════════════════════════════════════════════════════════════════
# 2) First Inning Collapse Score
# ═════════════════════════════════════════════════════════════════════
def compute_first_inning_collapse(
    starter: Optional[dict],
    opposing_lineup: Optional[dict],
) -> dict:
    """Probabilidad de que el partido se rompa temprano (1er inning).

    `starter`:  first_inning_era, first_inning_whip,
                inning1_er_l5, inning1_walks_l5, inning1_hits_l5,
                inning1_hr_l5, inning1_pitch_count_avg, first_pitch_strike_pct,
                bb_pct, whip (fallback).
    `opposing_lineup`: top5_ops_l7, top5_obp_l7, top5_iso_l7,
                bb_pct, k_pct, hard_hit_pct, barrel_pct,
                inning1_runs_l10.
    """
    starter = starter or {}
    lineup = opposing_lineup or {}

    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0
    contribs = 0

    fi_era = _safe_float(starter.get("first_inning_era"))
    if fi_era is not None:
        contribs += 1
        if fi_era >= 6.5:
            score += 12
            drivers.append("FI_ERA_EXTREME")
        elif fi_era >= 5.0:
            score += 7
            drivers.append("FI_ERA_HIGH")
        elif fi_era >= 3.5:
            score += 0
        else:
            score -= 6
            drivers.append("FI_ERA_STRONG")
    else:
        missing.append("first_inning_era")

    fi_whip = _safe_float(starter.get("first_inning_whip"))
    if fi_whip is not None:
        contribs += 1
        if fi_whip >= 1.60:
            score += 8
            drivers.append("FI_WHIP_HIGH")
        elif fi_whip >= 1.35:
            score += 4
        elif fi_whip <= 1.00:
            score -= 4
    else:
        missing.append("first_inning_whip")

    inning1_er_l5 = _safe_int(starter.get("inning1_er_l5"))
    if inning1_er_l5 is not None:
        contribs += 1
        if inning1_er_l5 >= 4:
            score += 10
            drivers.append("L5_INNING1_RUNS_4PLUS")
        elif inning1_er_l5 >= 2:
            score += 5
        elif inning1_er_l5 == 0:
            score -= 4
    else:
        missing.append("inning1_er_l5")

    inning1_walks_l5 = _safe_int(starter.get("inning1_walks_l5"))
    if inning1_walks_l5 is not None:
        contribs += 1
        if inning1_walks_l5 >= 4:
            score += 6
            drivers.append("L5_INNING1_WALKS_HIGH")
        elif inning1_walks_l5 >= 2:
            score += 3

    fps = _safe_float(starter.get("first_pitch_strike_pct"))
    if fps is not None:
        contribs += 1
        if fps < 55.0:
            score += 5
            drivers.append("LOW_FIRST_PITCH_STRIKE")
        elif fps >= 65.0:
            score -= 4
    else:
        missing.append("first_pitch_strike_pct")

    bb_pct = _safe_float(starter.get("bb_pct"))
    whip = _safe_float(starter.get("whip"))
    # fallback general — sólo cuenta si faltan los específicos.
    if fi_era is None and bb_pct is not None:
        contribs += 1
        if bb_pct >= 9.5:
            score += 3
    if fi_whip is None and whip is not None:
        contribs += 1
        if whip >= 1.35:
            score += 3

    # Lineup rival.
    top5_ops_l7 = _safe_float(lineup.get("top5_ops_l7"))
    if top5_ops_l7 is not None:
        contribs += 1
        if top5_ops_l7 >= 0.820:
            score += 10
            drivers.append("OPPONENT_TOP5_OPS_EXPLOSIVE")
        elif top5_ops_l7 >= 0.760:
            score += 5
            drivers.append("OPPONENT_TOP5_OPS_HOT")
        elif top5_ops_l7 <= 0.650:
            score -= 5
    else:
        missing.append("top5_ops_l7")

    top5_iso = _safe_float(lineup.get("top5_iso_l7"))
    if top5_iso is not None:
        contribs += 1
        if top5_iso >= 0.220:
            score += 6
        elif top5_iso >= 0.180:
            score += 3

    barrel_lineup = _safe_float(lineup.get("barrel_pct"))
    if barrel_lineup is not None:
        contribs += 1
        if barrel_lineup >= 9.0:
            score += 5

    inning1_runs_l10 = _safe_float(lineup.get("inning1_runs_l10"))
    if inning1_runs_l10 is not None:
        contribs += 1
        if inning1_runs_l10 >= 1.20:
            score += 8
            drivers.append("OPPONENT_INNING1_HOT")
        elif inning1_runs_l10 >= 0.80:
            score += 4

    score = _clamp(score, 0, 100)
    bucket = _bucket(score, [(0, "LOW"), (40, "MEDIUM"), (65, "HIGH"), (80, "EXTREME")])
    max_inputs = 9
    confidence = round(_clamp(contribs / max_inputs * 100, 0, 100), 1)
    return {
        "first_inning_collapse_score": round(score, 2),
        "bucket": bucket,
        "drivers": drivers,
        "missing_fields": missing,
        "confidence": confidence,
    }


# ═════════════════════════════════════════════════════════════════════
# 3) Recent Offensive Quality
# ═════════════════════════════════════════════════════════════════════
def compute_recent_offensive_quality(team: Optional[dict]) -> dict:
    """Forma ofensiva real de un equipo en L7/L15."""
    team = team or {}
    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0
    contribs = 0

    def _ops_score(ops: Optional[float], window: str) -> None:
        nonlocal score, contribs
        if ops is None:
            missing.append(f"ops_{window}")
            return
        contribs += 1
        if ops >= 0.820:
            score += 14
            drivers.append(f"OPS_{window}_EXPLOSIVE")
        elif ops >= 0.760:
            score += 8
            drivers.append(f"OPS_{window}_HOT")
        elif ops >= 0.700:
            score += 0
        elif ops >= 0.640:
            score -= 4
        else:
            score -= 10
            drivers.append(f"OPS_{window}_COLD")

    _ops_score(_safe_float(team.get("ops_l7")), "L7")
    _ops_score(_safe_float(team.get("ops_l15")), "L15")

    rpg_l7 = _safe_float(team.get("runs_per_game_l7"))
    if rpg_l7 is not None:
        contribs += 1
        if rpg_l7 >= 5.0:
            score += 8
            drivers.append("RPG_L7_HOT")
        elif rpg_l7 >= 4.3:
            score += 3
        elif rpg_l7 <= 3.0:
            score -= 6
            drivers.append("RPG_L7_COLD")
    else:
        missing.append("runs_per_game_l7")

    iso = _safe_float(team.get("iso_l7"))
    if iso is not None:
        contribs += 1
        if iso >= 0.200:
            score += 6
        elif iso <= 0.120:
            score -= 4

    hard_hit = _safe_float(team.get("hard_hit_pct"))
    if hard_hit is not None:
        contribs += 1
        if hard_hit >= 42.0:
            score += 6
            drivers.append("HARD_HIT_EXPLOSIVE")
        elif hard_hit <= 33.0:
            score -= 4

    barrel = _safe_float(team.get("barrel_pct"))
    if barrel is not None:
        contribs += 1
        if barrel >= 9.0:
            score += 6
            drivers.append("BARREL_EXPLOSIVE")

    obp_l7 = _safe_float(team.get("obp_l7"))
    if obp_l7 is not None:
        contribs += 1
        if obp_l7 >= 0.350:
            score += 4
        elif obp_l7 <= 0.290:
            score -= 4

    risp = _safe_float(team.get("risp_avg"))
    if risp is not None:
        contribs += 1
        if risp >= 0.280:
            score += 4
        elif risp <= 0.210:
            score -= 3

    score = _clamp(score, 0, 100)
    bucket = _bucket(
        score,
        [(0, "COLD"), (38, "NEUTRAL"), (62, "HOT"), (78, "EXPLOSIVE")],
    )
    max_inputs = 8
    confidence = round(_clamp(contribs / max_inputs * 100, 0, 100), 1)
    return {
        "recent_offense_score": round(score, 2),
        "bucket": bucket,
        "l7": {
            k: team.get(k)
            for k in ("ops_l7", "runs_per_game_l7", "iso_l7", "obp_l7")
            if k in team
        },
        "l15": {k: team.get(k) for k in ("ops_l15", "runs_per_game_l15") if k in team},
        "drivers": drivers,
        "missing_fields": missing,
        "confidence": confidence,
    }


# ═════════════════════════════════════════════════════════════════════
# 4) Lineup Explosiveness Index
# ═════════════════════════════════════════════════════════════════════
def compute_lineup_explosiveness(lineup: Optional[dict]) -> dict:
    """Score específico del lineup probable o confirmado."""
    lineup = lineup or {}
    drivers: list[str] = []
    missing: list[str] = []
    score = 50.0
    contribs = 0

    top5_ops = _safe_float(lineup.get("top5_ops"))
    top5_iso = _safe_float(lineup.get("top5_iso"))
    top5_obp = _safe_float(lineup.get("top5_obp"))
    top5_bb = _safe_float(lineup.get("top5_bb_pct"))
    top5_k = _safe_float(lineup.get("top5_k_pct"))
    hard_hit = _safe_float(lineup.get("hard_hit_pct"))
    barrel = _safe_float(lineup.get("barrel_pct"))
    hr_rate = _safe_float(lineup.get("hr_rate"))
    confirmed = bool(lineup.get("confirmed_lineup"))

    if top5_ops is not None:
        contribs += 1
        if top5_ops >= 0.860:
            score += 18
            drivers.append("TOP5_OPS_ELITE")
        elif top5_ops >= 0.820:
            score += 12
            drivers.append("TOP5_OPS_EXPLOSIVE")
        elif top5_ops >= 0.760:
            score += 6
            drivers.append("TOP5_OPS_STRONG")
        elif top5_ops <= 0.680:
            score -= 8
            drivers.append("TOP5_OPS_LOW")
    else:
        missing.append("top5_ops")

    if top5_iso is not None:
        contribs += 1
        if top5_iso >= 0.220:
            score += 8
            drivers.append("TOP5_ISO_HIGH")
        elif top5_iso >= 0.190:
            score += 4
        elif top5_iso <= 0.130:
            score -= 4

    if top5_obp is not None:
        contribs += 1
        if top5_obp >= 0.370:
            score += 6
        elif top5_obp <= 0.310:
            score -= 3

    if top5_bb is not None:
        contribs += 1
        if top5_bb >= 10.5:
            score += 3

    if top5_k is not None:
        contribs += 1
        if top5_k >= 27.0:
            score -= 4
        elif top5_k <= 20.0:
            score += 3

    if hard_hit is not None:
        contribs += 1
        if hard_hit >= 42.0:
            score += 5
            drivers.append("LINEUP_HARD_HIT_EXPLOSIVE")

    if barrel is not None:
        contribs += 1
        if barrel >= 9.0:
            score += 5
            drivers.append("LINEUP_BARREL_EXPLOSIVE")

    if hr_rate is not None:
        contribs += 1
        if hr_rate >= 0.045:
            score += 3

    score = _clamp(score, 0, 100)
    bucket = _bucket(
        score,
        [(0, "LOW"), (45, "AVG"), (62, "STRONG"), (78, "EXPLOSIVE")],
    )
    max_inputs = 8
    confidence_base = contribs / max_inputs * 100
    if not confirmed:
        confidence_base *= 0.7
        drivers.append("PROBABLE_LINEUP_NOT_CONFIRMED")
    confidence = round(_clamp(confidence_base, 0, 100), 1)
    return {
        "lineup_explosiveness_score": round(score, 2),
        "bucket": bucket,
        "top5_summary": {
            "ops": top5_ops,
            "iso": top5_iso,
            "obp": top5_obp,
            "bb_pct": top5_bb,
            "k_pct": top5_k,
        },
        "confirmed_lineup": confirmed,
        "drivers": drivers,
        "missing_fields": missing,
        "confidence": confidence,
    }


# ═════════════════════════════════════════════════════════════════════
# 5) Aggregator — applies the Nivel 1 risk overlay
# ═════════════════════════════════════════════════════════════════════
def aggregate_under_explosion_risk(
    *,
    home_starter: Optional[dict] = None,
    away_starter: Optional[dict] = None,
    home_lineup: Optional[dict] = None,
    away_lineup: Optional[dict] = None,
    home_offense: Optional[dict] = None,
    away_offense: Optional[dict] = None,
    home_bullpen_fatigue: Optional[float] = None,  # 0-100, 100 = fresh
    away_bullpen_fatigue: Optional[float] = None,
    base_fragility: Optional[float] = None,  # 0-100
    base_explosive_tail_risk: Optional[str] = None,
    base_survival_score: Optional[float] = None,  # 0-100
    line: Optional[float] = None,
    expected_runs: Optional[float] = None,
    selection: Optional[str] = None,  # "UNDER" / "OVER" / None
) -> dict:
    """Combina los 4 sub-scores en una capa de riesgo Nivel 1 sobre
    picks UNDER. Devuelve los sub-scores + reason codes + ajustes
    propuestos a fragility / explosive_tail_risk / survival_score.

    El consumidor (pick engine) puede aplicar los ajustes o ignorarlos
    (observe_only por defecto).
    """
    sv_home = compute_starter_volatility(home_starter)
    sv_away = compute_starter_volatility(away_starter)
    fi_home = compute_first_inning_collapse(home_starter, away_lineup)
    fi_away = compute_first_inning_collapse(away_starter, home_lineup)
    off_home = compute_recent_offensive_quality(home_offense)
    off_away = compute_recent_offensive_quality(away_offense)
    le_home = compute_lineup_explosiveness(home_lineup)
    le_away = compute_lineup_explosiveness(away_lineup)

    reason_codes: list[str] = []
    drivers: list[str] = []

    # ── Cross-detection: VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP ──────
    # (a) Visiting starter HIGH/EXTREME volatility vs home lineup EXPLOSIVE.
    if sv_away["bucket"] in ("HIGH", "EXTREME") and le_home["bucket"] == "EXPLOSIVE":
        reason_codes.append("VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP")
        drivers.append("AWAY_STARTER_VS_HOME_EXPLOSIVE_LINEUP")
    # (b) Home starter HIGH/EXTREME volatility vs away lineup EXPLOSIVE.
    if sv_home["bucket"] in ("HIGH", "EXTREME") and le_away["bucket"] == "EXPLOSIVE":
        if "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" not in reason_codes:
            reason_codes.append("VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP")
        drivers.append("HOME_STARTER_VS_AWAY_EXPLOSIVE_LINEUP")

    # ── First-inning collapse ──────────────────────────────────────
    max_fi = max(
        fi_home["first_inning_collapse_score"], fi_away["first_inning_collapse_score"]
    )
    if max_fi >= 80:
        reason_codes.append("EXTREME_FIRST_INNING_COLLAPSE_RISK")
        reason_codes.append("FIRST_INNING_COLLAPSE_RISK")
    elif max_fi >= 65:
        reason_codes.append("FIRST_INNING_COLLAPSE_RISK")

    # ── Both teams HOT/EXPLOSIVE → bilateral offense risk ──────────
    if off_home["bucket"] in ("HOT", "EXPLOSIVE") and off_away["bucket"] in (
        "HOT",
        "EXPLOSIVE",
    ):
        reason_codes.append("BILATERAL_HOT_OFFENSE")
        drivers.append("BOTH_TEAMS_HOT_OR_EXPLOSIVE")

    # ── Bullpen fatigue (lower = more fatigued) ────────────────────
    bp_home_f = _safe_float(home_bullpen_fatigue)
    bp_away_f = _safe_float(away_bullpen_fatigue)
    bp_min = None
    if bp_home_f is not None or bp_away_f is not None:
        candidates = [v for v in (bp_home_f, bp_away_f) if v is not None]
        bp_min = min(candidates)
        if bp_min < 40:
            reason_codes.append(
                "BULLPEN_FATIGUED_BOTH_SIDES"
                if len(candidates) > 1 and max(candidates) < 50
                else "BULLPEN_FATIGUED"
            )
            drivers.append(f"BULLPEN_MIN_FATIGUE_{bp_min:.0f}")

    # ── Compute fragility delta (suma controlada). ──────────────────
    # Cada reason code clave aporta una cantidad fija.
    fragility_delta = 0.0
    if "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in reason_codes:
        fragility_delta += 25
    if "EXTREME_FIRST_INNING_COLLAPSE_RISK" in reason_codes:
        fragility_delta += 25
    elif "FIRST_INNING_COLLAPSE_RISK" in reason_codes:
        fragility_delta += 15
    if "BILATERAL_HOT_OFFENSE" in reason_codes:
        fragility_delta += 10
    if bp_min is not None and bp_min < 40:
        fragility_delta += 10
    # Cap incremental: máx +60 puntos por la capa.
    fragility_delta = _clamp(fragility_delta, 0, 60)

    base_frag = _safe_float(base_fragility) or 0.0
    adjusted_fragility = _clamp(base_frag + fragility_delta, 0, 100)

    # ── Explosive tail risk ────────────────────────────────────────
    base_tail = (base_explosive_tail_risk or "LOW").upper()
    tail_levels = ["LOW", "MEDIUM", "HIGH", "EXTREME"]
    base_idx = tail_levels.index(base_tail) if base_tail in tail_levels else 0
    bump = 0
    if "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in reason_codes:
        bump += 2
    if "EXTREME_FIRST_INNING_COLLAPSE_RISK" in reason_codes:
        bump += 2
    elif "FIRST_INNING_COLLAPSE_RISK" in reason_codes:
        bump += 1
    if "BILATERAL_HOT_OFFENSE" in reason_codes:
        bump += 1
    adjusted_tail = tail_levels[_clamp(base_idx + bump, 0, len(tail_levels) - 1)]

    # ── Survival score ─────────────────────────────────────────────
    survival_delta = (
        -fragility_delta * 0.5
    )  # cada punto de fragility resta 0.5 a survival
    if "EXTREME_FIRST_INNING_COLLAPSE_RISK" in reason_codes:
        survival_delta -= 10
    base_surv = _safe_float(base_survival_score)
    adjusted_survival = (
        round(_clamp(base_surv + survival_delta, 0, 100), 2)
        if base_surv is not None
        else None
    )

    # ── Verdict for UNDER picks ────────────────────────────────────
    sel_norm = (selection or "").upper()
    verdict = "OBSERVE"
    cushion = (
        (line - expected_runs)
        if (line is not None and expected_runs is not None)
        else None
    )
    if sel_norm == "UNDER":
        if "EXTREME_FIRST_INNING_COLLAPSE_RISK" in reason_codes:
            if (
                cushion is not None
                and cushion >= 2.0
                and (bp_min is None or bp_min >= 60)
            ):
                verdict = "DEGRADE_UNDER"
            else:
                verdict = "BLOCK_UNDER"
        elif "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" in reason_codes:
            if cushion is not None and cushion >= 2.5:
                verdict = "DEGRADE_UNDER"
            else:
                verdict = "BLOCK_UNDER"
        elif (
            "FIRST_INNING_COLLAPSE_RISK" in reason_codes
            or "BILATERAL_HOT_OFFENSE" in reason_codes
        ):
            verdict = "DEGRADE_UNDER"
        elif adjusted_fragility >= 75:
            verdict = "DEGRADE_UNDER"
        else:
            verdict = "ALLOW_UNDER"

    return {
        "starter_volatility": {"home": sv_home, "away": sv_away},
        "first_inning_collapse": {
            "home": fi_home,
            "away": fi_away,
            "max_score": round(max_fi, 2),
        },
        "recent_offensive_quality": {"home": off_home, "away": off_away},
        "lineup_explosiveness": {"home": le_home, "away": le_away},
        "bullpen_fatigue_min": bp_min,
        "reason_codes": reason_codes,
        "drivers": drivers,
        "fragility_delta": round(fragility_delta, 2),
        "adjusted_fragility": round(adjusted_fragility, 2),
        "adjusted_explosive_tail_risk": adjusted_tail,
        "adjusted_survival_score": adjusted_survival,
        "verdict": verdict,
        "cushion_runs": None if cushion is None else round(cushion, 2),
        "observe_only": True,
    }


__all__ = [
    "compute_starter_volatility",
    "compute_first_inning_collapse",
    "compute_recent_offensive_quality",
    "compute_lineup_explosiveness",
    "aggregate_under_explosion_risk",
]
