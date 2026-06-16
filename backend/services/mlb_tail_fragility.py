"""
services.mlb_tail_fragility
===========================

Phase 55 — **Tail Fragility Engine**.

Pure-Python module that turns the PMF/CDF tail probabilities produced by
``mlb_expected_runs_distribution.compute_tail_risk`` into a quality-weighted
*explosive tail score* and a fragility delta. The score answers the
question:

    "Two games project the same Expected Runs (8.0). Which one is
     actually more fragile to a late blow-up?"

The module NEVER recomputes the run distribution. It consumes the
probabilities that already live on ``tail_risk_payload`` and combines
them with structural risk drivers (bullpen fatigue, defensive
breakdown, series familiarity, vulnerable starter) to surface a single
``tail_fragility_adjustment``.

Critical contract
-----------------
*   **NEVER raises.** All branches are fail-soft and return
    ``available: False`` plus reason codes when inputs are missing.
*   **NEVER mutates** ``expected_runs``, ``run_distribution`` or
    market polarity (Over/Under). Output is *additive* on fragility and
    confidence only.
*   **Cap +20** on the total contribution to fragility (base +
    interactions combined).
*   The interaction modifiers only fire when the **tail bucket is
    already HIGH or EXTREME**; the spec is explicit that structural
    risks compound an existing explosive tail, not the reverse.

Output shape
------------
::

    {
        "available":               True,
        "engine_version":          ENGINE_VERSION,
        "p_ge_12":                 0.22,
        "p_ge_14":                 0.10,
        "p_ge_16":                 0.04,
        "p_ge_18":                 0.015,
        "explosive_tail_score":    62,            # 0..100
        "tail_bucket":             "HIGH",        # LOW|MEDIUM|HIGH|EXTREME
        "base_adjustment":         10,            # from bucket
        "interactions": [
            {"code": "TAIL_BULLPEN_INTERACTION",  "delta": 5},
            {"code": "TAIL_DEFENSE_INTERACTION",  "delta": 4},
            ...
        ],
        "interaction_total":       12,
        "total_adjustment":        20,            # capped at +20
        "cap_hit":                 True,
        "reason_codes": ["EXPLOSIVE_TAIL_HIGH",
                         "TAIL_BULLPEN_INTERACTION",
                         "TAIL_STARTER_INTERACTION",
                         "TAIL_FRAGILITY_CAP_HIT"],
        "narrative_es":           "Aunque el modelo proyecta...",
    }
"""
from __future__ import annotations

from typing import Optional


# ─── Versioning ───────────────────────────────────────────────────────────────
ENGINE_VERSION = "tail_fragility.v1"


# ─── Bucket thresholds (0..100 score) ────────────────────────────────────────
BUCKET_LOW       = "LOW"
BUCKET_MEDIUM    = "MEDIUM"
BUCKET_HIGH      = "HIGH"
BUCKET_EXTREME   = "EXTREME"

# Bucket → base adjustment to fragility delta.
BUCKET_BASE_ADJUSTMENT = {
    BUCKET_LOW:     0,
    BUCKET_MEDIUM:  5,
    BUCKET_HIGH:    10,
    BUCKET_EXTREME: 15,
}

# Total adjustment cap (base + interactions combined).
CAP_TOTAL_ADJUSTMENT = 20

# Score weights (sum = 1.00; reflects the spec exactly).
W_P12 = 0.30
W_P14 = 0.30
W_P16 = 0.25
W_P18 = 0.15

# Score-to-bucket thresholds.
_BUCKET_THRESHOLDS = (
    (75, BUCKET_EXTREME),
    (50, BUCKET_HIGH),
    (25, BUCKET_MEDIUM),
    (0,  BUCKET_LOW),
)


# ─── Reason codes ────────────────────────────────────────────────────────────
RC_TAIL_FRAGILITY_USED        = "TAIL_FRAGILITY_USED"
RC_EXPLOSIVE_TAIL_LOW         = "EXPLOSIVE_TAIL_LOW"
RC_EXPLOSIVE_TAIL_MEDIUM      = "EXPLOSIVE_TAIL_MEDIUM"
RC_EXPLOSIVE_TAIL_HIGH        = "EXPLOSIVE_TAIL_HIGH"
RC_EXPLOSIVE_TAIL_EXTREME     = "EXPLOSIVE_TAIL_EXTREME"
RC_TAIL_BULLPEN_INTERACTION   = "TAIL_BULLPEN_INTERACTION"
RC_TAIL_DEFENSE_INTERACTION   = "TAIL_DEFENSE_INTERACTION"
RC_TAIL_SERIES_INTERACTION    = "TAIL_SERIES_INTERACTION"
RC_TAIL_STARTER_INTERACTION   = "TAIL_STARTER_INTERACTION"
RC_TAIL_FRAGILITY_CAP_HIT     = "TAIL_FRAGILITY_CAP_HIT"
RC_TAIL_FRAGILITY_UNAVAILABLE = "TAIL_FRAGILITY_UNAVAILABLE"
# Polarity guard (post-fix) — fires when the explosive-tail probability
# distribution clearly signals HIGH risk (either via the explicit
# probability thresholds or via the external ``tail_risk.tail_bucket``)
# but the weighted score still buckets at LOW. Without this guard the UI
# could simultaneously render "Riesgo de cola explosiva: Alta" and
# "Tail Fragility: Bajo", contradicting the statistical reading.
RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL = "TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL"

# Probability-based escalation thresholds (per spec).
_ESCALATION_P12_THRESHOLD = 0.25  # p_ge_12 >= 25% forces non-LOW.
_ESCALATION_P14_THRESHOLD = 0.10  # p_ge_14 >= 10% forces non-LOW.
_ESCALATION_MIN_SCORE     = 40    # floor for escalated score.


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _explosive_tail_score(
    p12: Optional[float], p14: Optional[float],
    p16: Optional[float], p18: Optional[float],
) -> Optional[int]:
    """Weighted blend of upper-tail probabilities. Returns 0..100 (int)
    or ``None`` when the inputs are missing / unusable.

    The spec defines:
        tail = p12·0.30 + p14·0.30 + p16·0.25 + p18·0.15
    where each ``p_X`` is a probability in [0,1]. Since the weights
    sum to 1.0, the raw blend lies in [0,1] and we multiply by 100 to
    obtain a 0..100 score.
    """
    parts = [p12, p14, p16, p18]
    if all(p is None for p in parts):
        return None
    p12 = max(0.0, min(1.0, p12 or 0.0))
    p14 = max(0.0, min(1.0, p14 or 0.0))
    p16 = max(0.0, min(1.0, p16 or 0.0))
    p18 = max(0.0, min(1.0, p18 or 0.0))
    raw = p12 * W_P12 + p14 * W_P14 + p16 * W_P16 + p18 * W_P18
    return int(round(raw * 100))


def _bucket_from_score(score: Optional[int]) -> str:
    if score is None:
        return BUCKET_LOW
    for threshold, label in _BUCKET_THRESHOLDS:
        if score >= threshold:
            return label
    return BUCKET_LOW


def _bucket_reason_code(bucket: str) -> str:
    return {
        BUCKET_LOW:     RC_EXPLOSIVE_TAIL_LOW,
        BUCKET_MEDIUM:  RC_EXPLOSIVE_TAIL_MEDIUM,
        BUCKET_HIGH:    RC_EXPLOSIVE_TAIL_HIGH,
        BUCKET_EXTREME: RC_EXPLOSIVE_TAIL_EXTREME,
    }.get(bucket, RC_EXPLOSIVE_TAIL_LOW)


def _bucket_rank(bucket: str) -> int:
    return {BUCKET_LOW: 0, BUCKET_MEDIUM: 1, BUCKET_HIGH: 2, BUCKET_EXTREME: 3}.get(bucket, 0)


# ─── Public API ──────────────────────────────────────────────────────────────
def compute_tail_fragility(
    *,
    tail_risk_payload: Optional[dict] = None,
    bullpen_fatigue_high: bool = False,
    defensive_breakdown_bucket: Optional[str] = None,
    series_familiarity_bucket: Optional[str] = None,
    starter_era: Optional[float] = None,
    starter_whip: Optional[float] = None,
    market_side: Optional[str] = None,
) -> dict:
    """Compute the Tail Fragility payload.

    Parameters
    ----------
    tail_risk_payload
        The dict returned by
        ``mlb_expected_runs_distribution.compute_tail_risk``. Must
        carry ``p_ge_12``, ``p_ge_14``, ``p_ge_16``, ``p_ge_18``.
    bullpen_fatigue_high
        True when at least one team enters with a HIGH bullpen fatigue
        bucket (or workload score ≥ HIGH threshold).
    defensive_breakdown_bucket
        ``LOW`` / ``MEDIUM`` / ``HIGH`` / ``EXTREME``.
    series_familiarity_bucket
        ``LOW`` / ``MEDIUM`` / ``HIGH`` / ``EXTREME``.
    starter_era, starter_whip
        Composite values for the side most exposed (i.e. the weaker
        starter of the matchup). When either ``ERA > 4.50`` OR
        ``WHIP > 1.35`` fires, the starter is considered vulnerable.
    market_side
        Optional ``"over"`` / ``"under"`` — purely for narrative; does
        NOT flip polarity.

    Returns
    -------
    dict
        ``available=False`` when the tail_risk payload is missing or
        unusable; ``available=True`` with the full breakdown otherwise.
    """
    # ── Fail-soft when no usable tail_risk payload ───────────────────
    if not isinstance(tail_risk_payload, dict) or not tail_risk_payload.get("available"):
        return {
            "available":            False,
            "engine_version":       ENGINE_VERSION,
            "p_ge_12":              None,
            "p_ge_14":              None,
            "p_ge_16":              None,
            "p_ge_18":              None,
            "explosive_tail_score": None,
            "tail_bucket":          BUCKET_LOW,
            "base_adjustment":      0,
            "interactions":         [],
            "interaction_total":    0,
            "total_adjustment":     0,
            "cap_hit":              False,
            "reason_codes":         [RC_TAIL_FRAGILITY_UNAVAILABLE],
            "narrative_es":         None,
        }

    p12 = _safe_float(tail_risk_payload.get("p_ge_12"))
    p14 = _safe_float(tail_risk_payload.get("p_ge_14"))
    p16 = _safe_float(tail_risk_payload.get("p_ge_16"))
    p18 = _safe_float(tail_risk_payload.get("p_ge_18"))

    # ── Score + bucket ──────────────────────────────────────────────
    score  = _explosive_tail_score(p12, p14, p16, p18)
    bucket = _bucket_from_score(score)
    base   = BUCKET_BASE_ADJUSTMENT.get(bucket, 0)

    reason_codes: list[str] = [RC_TAIL_FRAGILITY_USED, _bucket_reason_code(bucket)]
    interactions: list[dict] = []

    # ── Polarity guard (post-fix) ───────────────────────────────────
    # Prevent the contradictory "Riesgo de cola explosiva: Alta" +
    # "Tail Fragility: Bajo" simultaneous render. The weighted score
    # can drift LOW when most mass concentrates on p_ge_12 alone (e.g.
    # 31% / 14% / 5% / 2% gives score=15). When the explicit probability
    # tail clearly fires HIGH risk, escalate the bucket to MEDIUM and
    # floor the score at 40.
    external_bucket = (tail_risk_payload.get("tail_bucket") or "").upper()
    explosive_high_signal = (
        (p12 is not None and p12 >= _ESCALATION_P12_THRESHOLD)
        or (p14 is not None and p14 >= _ESCALATION_P14_THRESHOLD)
        or external_bucket in (BUCKET_HIGH, BUCKET_EXTREME)
    )
    if explosive_high_signal and bucket == BUCKET_LOW:
        bucket = BUCKET_MEDIUM
        score  = max(int(score or 0), _ESCALATION_MIN_SCORE)
        base   = BUCKET_BASE_ADJUSTMENT.get(bucket, 0)
        # Replace the prior LOW reason-code with the new MEDIUM one + the
        # explicit escalation tracer.
        reason_codes = [RC_TAIL_FRAGILITY_USED, _bucket_reason_code(bucket),
                        RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL]

    # ── Interactions only fire when tail is already HIGH+ ───────────
    tail_high_or_above = _bucket_rank(bucket) >= _bucket_rank(BUCKET_HIGH)

    if tail_high_or_above:
        # 1) Bullpen fatigue HIGH + tail HIGH → +5
        if bullpen_fatigue_high:
            interactions.append({
                "code":   RC_TAIL_BULLPEN_INTERACTION,
                "delta":  5,
                "label":  "Bullpen fatigado",
            })
            reason_codes.append(RC_TAIL_BULLPEN_INTERACTION)

        # 2) Defensive breakdown MEDIUM+ + tail HIGH → +4
        if (defensive_breakdown_bucket
                and _bucket_rank(str(defensive_breakdown_bucket).upper())
                    >= _bucket_rank(BUCKET_MEDIUM)):
            interactions.append({
                "code":   RC_TAIL_DEFENSE_INTERACTION,
                "delta":  4,
                "label":  "Defensa quebrándose",
            })
            reason_codes.append(RC_TAIL_DEFENSE_INTERACTION)

        # 3) Series familiarity MEDIUM+ + tail HIGH → +3
        if (series_familiarity_bucket
                and _bucket_rank(str(series_familiarity_bucket).upper())
                    >= _bucket_rank(BUCKET_MEDIUM)):
            interactions.append({
                "code":   RC_TAIL_SERIES_INTERACTION,
                "delta":  3,
                "label":  "Series con familiaridad",
            })
            reason_codes.append(RC_TAIL_SERIES_INTERACTION)

        # 4) Vulnerable starter (ERA > 4.50 OR WHIP > 1.35) + tail HIGH → +5
        era_f = _safe_float(starter_era)
        whip_f = _safe_float(starter_whip)
        starter_weak = ((era_f is not None and era_f > 4.50)
                        or (whip_f is not None and whip_f > 1.35))
        if starter_weak:
            interactions.append({
                "code":   RC_TAIL_STARTER_INTERACTION,
                "delta":  5,
                "label":  "Abridor vulnerable",
            })
            reason_codes.append(RC_TAIL_STARTER_INTERACTION)

    interaction_total = sum(int(i.get("delta") or 0) for i in interactions)
    raw_total         = base + interaction_total
    capped_total      = min(raw_total, CAP_TOTAL_ADJUSTMENT)
    cap_hit           = raw_total > CAP_TOTAL_ADJUSTMENT
    if cap_hit:
        reason_codes.append(RC_TAIL_FRAGILITY_CAP_HIT)

    # ── Narrative (Spanish) ─────────────────────────────────────────
    narrative_es = _build_narrative_es(
        bucket=bucket, score=score, p12=p12, p14=p14, p16=p16, p18=p18,
        interactions=interactions, market_side=market_side,
        escalated_by_explosive_tail=(
            RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL in reason_codes
        ),
    )

    # De-duplicate reason codes preserving order.
    reason_codes = list(dict.fromkeys(reason_codes))

    return {
        "available":            True,
        "engine_version":       ENGINE_VERSION,
        "p_ge_12":              p12,
        "p_ge_14":              p14,
        "p_ge_16":              p16,
        "p_ge_18":              p18,
        "explosive_tail_score": score,
        "tail_bucket":          bucket,
        "base_adjustment":      base,
        "interactions":         interactions,
        "interaction_total":    interaction_total,
        "total_adjustment":     capped_total,
        "cap_hit":              cap_hit,
        "reason_codes":         reason_codes,
        "narrative_es":         narrative_es,
    }


# ─── Narrative builder ───────────────────────────────────────────────────────
def _build_narrative_es(
    *,
    bucket: str,
    score: Optional[int],
    p12: Optional[float],
    p14: Optional[float],
    p16: Optional[float],
    p18: Optional[float],
    interactions: list,
    market_side: Optional[str] = None,
    escalated_by_explosive_tail: bool = False,
) -> str:
    score_txt = f"{score}/100" if score is not None else "—/100"

    if bucket == BUCKET_LOW:
        head = (
            f"Riesgo de cola explosiva BAJO ({score_txt}). La distribución "
            f"concentra la masa cerca de la media; pocas rutas hacia un "
            f"blow-up tardío."
        )
    elif bucket == BUCKET_MEDIUM:
        head = (
            f"Riesgo de cola explosiva MEDIO ({score_txt}). La probabilidad "
            f"de superar 12+ carreras se sitúa en "
            f"{int((p12 or 0) * 100)}% y existen rutas alternativas hacia "
            f"el Over que conviene vigilar."
        )
    elif bucket == BUCKET_HIGH:
        head = (
            f"Riesgo de cola explosiva ALTO ({score_txt}). Aunque la media "
            f"pueda lucir contenida, P(12+) ≈ {int((p12 or 0) * 100)}% y "
            f"P(14+) ≈ {int((p14 or 0) * 100)}% revelan rutas verosímiles "
            f"hacia un blow-up tardío."
        )
    else:  # EXTREME
        head = (
            f"Riesgo de cola EXTREMO ({score_txt}). La distribución carga "
            f"masa peligrosa en la cola: P(12+) ≈ "
            f"{int((p12 or 0) * 100)}%, P(14+) ≈ {int((p14 or 0) * 100)}%, "
            f"P(16+) ≈ {int((p16 or 0) * 100)}%. La fragilidad estructural "
            f"debe ser evaluada con cautela."
        )

    parts = [head]

    # Polarity-guard explanatory line (post-fix). Surfaces when the
    # weighted score would otherwise read LOW but the explicit
    # probability distribution forces a MEDIUM bucket.
    if escalated_by_explosive_tail:
        parts.append(
            "Tail Fragility escalado porque la distribución asigna alta "
            f"probabilidad a escenarios de 12+ (≈ {int((p12 or 0) * 100)}%) "
            f"/ 14+ (≈ {int((p14 or 0) * 100)}%) carreras."
        )

    if interactions:
        drivers_txt = " · ".join((i.get("label") or i.get("code")) for i in interactions)
        parts.append(
            f"Drivers estructurales activos: {drivers_txt}. "
            f"Estas señales amplifican la ruta hacia el Over sin "
            f"voltear la polaridad del pick."
        )

    return " ".join(parts)


# ─── Pipeline helper ─────────────────────────────────────────────────────────
def apply_to_fragility(
    *,
    current_fragility: Optional[float],
    tail_fragility_payload: Optional[dict],
) -> dict:
    """Apply the ``total_adjustment`` from a tail-fragility payload to a
    pre-existing fragility score. Returns a small envelope summarising
    the operation; fail-soft on missing inputs.

    Caller decides where to persist (pick_payload, pipeline_meta, etc).
    """
    base = _safe_float(current_fragility) or 0.0
    if not isinstance(tail_fragility_payload, dict) or not tail_fragility_payload.get("available"):
        return {
            "applied":            False,
            "delta":              0,
            "fragility_before":   base,
            "fragility_after":    base,
            "reason":             "tail_fragility_unavailable",
        }
    delta = int(tail_fragility_payload.get("total_adjustment") or 0)
    new   = max(0.0, min(100.0, base + delta))
    return {
        "applied":          True,
        "delta":            delta,
        "fragility_before": base,
        "fragility_after":  round(new, 2),
        "tail_bucket":      tail_fragility_payload.get("tail_bucket"),
        "explosive_tail_score": tail_fragility_payload.get("explosive_tail_score"),
        "reason_codes":     tail_fragility_payload.get("reason_codes") or [],
    }


__all__ = [
    "ENGINE_VERSION",
    "BUCKET_LOW", "BUCKET_MEDIUM", "BUCKET_HIGH", "BUCKET_EXTREME",
    "CAP_TOTAL_ADJUSTMENT",
    "RC_TAIL_FRAGILITY_USED",
    "RC_EXPLOSIVE_TAIL_LOW", "RC_EXPLOSIVE_TAIL_MEDIUM",
    "RC_EXPLOSIVE_TAIL_HIGH", "RC_EXPLOSIVE_TAIL_EXTREME",
    "RC_TAIL_BULLPEN_INTERACTION", "RC_TAIL_DEFENSE_INTERACTION",
    "RC_TAIL_SERIES_INTERACTION", "RC_TAIL_STARTER_INTERACTION",
    "RC_TAIL_FRAGILITY_CAP_HIT", "RC_TAIL_FRAGILITY_UNAVAILABLE",
    "RC_TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL",
    "compute_tail_fragility",
    "apply_to_fragility",
]
