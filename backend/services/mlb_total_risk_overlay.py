"""
MLB Total Risk Overlay — Sprint D12 (Nivel 2 final).

Aggregator que integra TODAS las señales L1 + L2 sobre la proyección
base del modelo y emite verdict + dispersion calibration + reason
codes + editorial_summary.

NO reemplaza la proyección base. Agrega una capa de ajuste posterior.

Inputs:
  * baseline_expected_runs, baseline_distribution (opt)
  * pick: dict {selection, line}
  * starter_volatility: {home, away}        (de mlb_under_explosion_risk)
  * first_inning_collapse: {home, away}
  * recent_offensive_quality: {home, away}
  * lineup_explosiveness: {home, away}
  * bullpen_stress: {home, away}            (de mlb_bullpen_stress)
  * domino_risk: {home, away}                (de mlb_domino_risk)

Output:
  {
    "adjusted_expected_runs", "adjusted_distribution",
    "under_survival_score", "fragility_score",
    "explosive_tail_risk", "verdict", "reason_codes",
    "dispersion_multiplier", "editorial_summary", "debug"
  }
"""

from __future__ import annotations

from typing import Any, Optional


TAIL_LEVELS = ["LOW", "MEDIUM", "HIGH", "EXTREME"]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _bump_tail(level: str, n: int) -> str:
    if level not in TAIL_LEVELS:
        level = "LOW"
    idx = TAIL_LEVELS.index(level)
    new_idx = int(_clamp(idx + n, 0, len(TAIL_LEVELS) - 1))
    return TAIL_LEVELS[new_idx]


def _peak_score(block: Optional[dict], key: str) -> float:
    """Pick the higher of home/away score."""
    if not isinstance(block, dict):
        return 0.0
    h = (block.get("home") or {}).get(key) if isinstance(block.get("home"), dict) else None
    a = (block.get("away") or {}).get(key) if isinstance(block.get("away"), dict) else None
    h_f = _safe_float(h) or 0.0
    a_f = _safe_float(a) or 0.0
    return max(h_f, a_f)


def _bucket_of(block: Optional[dict], side: str) -> str:
    if not isinstance(block, dict):
        return "UNKNOWN"
    side_block = block.get(side)
    if not isinstance(side_block, dict):
        return "UNKNOWN"
    return str(side_block.get("bucket") or "UNKNOWN").upper()


def compute_total_risk_overlay(
    *,
    baseline_expected_runs: Optional[float] = None,
    baseline_distribution: Optional[dict] = None,
    pick: Optional[dict] = None,
    starter_volatility: Optional[dict] = None,
    first_inning_collapse: Optional[dict] = None,
    recent_offensive_quality: Optional[dict] = None,
    lineup_explosiveness: Optional[dict] = None,
    bullpen_stress: Optional[dict] = None,
    domino_risk: Optional[dict] = None,
    base_fragility: Optional[float] = None,
    base_survival: Optional[float] = None,
    base_explosive_tail_risk: Optional[str] = "LOW",
) -> dict:
    pick = pick or {}
    sel = str(pick.get("selection") or "").upper()
    _line = _safe_float(pick.get("line"))  # reservado para uso futuro (cushion-aware bloqueo)

    reason_codes: list[str] = []

    # Sub-scores (peak / per-side).
    sv_max = _peak_score(starter_volatility, "starter_volatility_score")
    fi_max = _peak_score(first_inning_collapse, "first_inning_collapse_score")
    off_home_bucket = _bucket_of(recent_offensive_quality, "home")
    off_away_bucket = _bucket_of(recent_offensive_quality, "away")
    lineup_max = _peak_score(lineup_explosiveness, "lineup_explosiveness_score")
    bp_max = _peak_score(bullpen_stress, "bullpen_stress_score")
    domino_max = _peak_score(domino_risk, "domino_risk_score")

    fragility = float(_safe_float(base_fragility) or 0.0)
    survival = float(_safe_float(base_survival) or 70.0)
    tail = (base_explosive_tail_risk or "LOW").upper()
    adj_runs_delta = 0.0

    is_under = "UNDER" in sel
    if is_under:
        # Rule 1 — Volatile starter vs explosive lineup.
        if sv_max >= 70 and lineup_max >= 70:
            fragility += 15
            tail = _bump_tail(tail, 1)
            if "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP" not in reason_codes:
                reason_codes.append("VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP")

        # Rule 2 — First inning collapse.
        if fi_max >= 85:
            fragility += 15
            survival -= 12
            tail = _bump_tail(tail, 1)
            reason_codes.append("EXTREME_FIRST_INNING_COLLAPSE_RISK")
            reason_codes.append("FIRST_INNING_COLLAPSE_RISK")
            reason_codes.append("EARLY_COLLAPSE_RISK")
        elif fi_max >= 70:
            fragility += 10
            survival -= 10
            reason_codes.append("FIRST_INNING_COLLAPSE_RISK")
            reason_codes.append("EARLY_COLLAPSE_RISK")

        # Rule 3 — Both offenses HOT/EXPLOSIVE.
        if off_home_bucket in ("HOT", "EXPLOSIVE") and off_away_bucket in ("HOT", "EXPLOSIVE"):
            fragility += 10
            # +0.4..+0.8 runs depending on intensity.
            both_explosive = off_home_bucket == "EXPLOSIVE" and off_away_bucket == "EXPLOSIVE"
            adj_runs_delta += 0.8 if both_explosive else 0.4
            reason_codes.append("BOTH_OFFENSES_HOT")

        # Rule 4 — Bullpen exhausted.
        if bp_max >= 70:
            fragility += 12
            survival -= 8
            reason_codes.append("BULLPEN_EXHAUSTION_RISK")

        # Rule 5 — Domino risk.
        if domino_max >= 70:
            fragility += 15
            survival -= 12
            tail = _bump_tail(tail, 1)
            reason_codes.append("DOMINO_RISK_STARTER_TO_BULLPEN")

    fragility = _clamp(fragility, 0, 100)
    survival = _clamp(survival, 0, 100)

    # ── Verdict ────────────────────────────────────────────────────
    critical_count = sum(1 for c in reason_codes if c in {
        "VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP",
        "EXTREME_FIRST_INNING_COLLAPSE_RISK",
        "BULLPEN_EXHAUSTION_RISK",
        "DOMINO_RISK_STARTER_TO_BULLPEN",
    })
    off_explosive_either = "EXPLOSIVE" in (off_home_bucket, off_away_bucket)

    verdict = "ALLOW"
    if is_under:
        if tail == "EXTREME":
            verdict = "BLOCK"
        elif domino_max >= 80 and off_explosive_either:
            verdict = "BLOCK"
        elif fi_max >= 85 and bp_max >= 70:
            verdict = "BLOCK"
        elif fragility >= 75:
            verdict = "BLOCK"
        elif fragility >= 60 or tail == "HIGH" or critical_count >= 2:
            verdict = "AVOID"
        elif fragility >= 45:
            verdict = "WARN"
        else:
            verdict = "ALLOW"

    # ── Dispersion calibration ────────────────────────────────────
    # baseline 1.0 → HIGH 1.35 → EXTREME 1.65..1.90
    if tail == "EXTREME":
        dispersion = 1.80
        reason_codes.append("UNDER_TAIL_RISK_RECALIBRATED")
    elif tail == "HIGH":
        dispersion = 1.35
        reason_codes.append("UNDER_TAIL_RISK_RECALIBRATED")
    elif tail == "MEDIUM":
        dispersion = 1.15
    else:
        dispersion = 1.0

    # ── Adjusted expected runs ────────────────────────────────────
    base_er = _safe_float(baseline_expected_runs)
    adj_runs = None if base_er is None else round(base_er + adj_runs_delta, 3)

    # ── Editorial summary ─────────────────────────────────────────
    if verdict == "BLOCK":
        editorial = "Riesgo de explosión muy alto — Under no recomendado."
    elif verdict == "AVOID":
        editorial = "Múltiples señales adversas para el Under — preferir evitar."
    elif verdict == "WARN":
        editorial = "Hay un riesgo relevante; considerar línea con colchón."
    else:
        editorial = "Sin señales adversas relevantes para el Under." if is_under else "Capa Under no aplica."

    return {
        "adjusted_expected_runs":    adj_runs,
        "adjusted_distribution":     baseline_distribution,  # passthrough hasta wire NB
        "under_survival_score":      round(survival, 2),
        "fragility_score":           round(fragility, 2),
        "explosive_tail_risk":       tail,
        "verdict":                   verdict,
        "reason_codes":              reason_codes,
        "dispersion_multiplier":     dispersion,
        "editorial_summary":         editorial,
        "debug": {
            "sv_max":      sv_max,
            "fi_max":      fi_max,
            "lineup_max":  lineup_max,
            "bp_max":      bp_max,
            "domino_max":  domino_max,
            "off_home_bucket": off_home_bucket,
            "off_away_bucket": off_away_bucket,
            "adj_runs_delta":  adj_runs_delta,
        },
    }


__all__ = ["compute_total_risk_overlay"]
