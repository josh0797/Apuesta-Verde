"""
services.mlb_layer_interaction_audit
====================================

Phase 56 — observe-only telemetry detector for double-counting between:

    * ``mlb_expected_runs_distribution`` (PMF/CDF + tail probabilities)
    * ``mlb_tail_fragility``             (Phase-55 explosive-tail score)
    * ``mlb_fragility_calibrator``       (hidden-over-route component deltas)

This module is **pure**, **fail-soft** and **read-only**: it never
mutates engine state, picks, market selection, or polarity. It only
builds two diagnostic payloads:

    1. ``layer_interaction_audit``         — attached to ``pick_payload``
    2. ``distribution_market_selection_effect`` — attached to
       ``pick_payload``

A condensed version of (1) is also surfaced on ``pipeline_meta`` so
ops can scan a whole slate at once.

Contract
--------
``build_layer_interaction_audit(...)`` always returns a dict with
``available: True/False`` and ``reason_codes``. No exceptions escape.

The detector consumes the already-computed layer payloads — it does
NOT recompute distributions, tails or fragility. This avoids any
risk of altering engine behaviour.
"""
from __future__ import annotations

from typing import Any, Optional


ENGINE_VERSION = "layer_interaction_audit.v1"

# ── Family keys (must match scripts/audit_mlb_layer_interactions.py) ─
FAMILY_BULLPEN = "bullpen"
FAMILY_DEFENSE = "defense"
FAMILY_SERIES  = "series"
FAMILY_STARTER = "starter"
FAMILY_TAIL    = "tail"
FAMILY_TRAFFIC = "traffic"

# ── Reason codes ─────────────────────────────────────────────────────
RC_LAYER_AUDIT_USED                  = "LAYER_INTERACTION_AUDIT_USED"
RC_LAYER_AUDIT_UNAVAILABLE           = "LAYER_INTERACTION_AUDIT_UNAVAILABLE"
RC_DOUBLE_COUNT_DETECTED             = "DOUBLE_COUNT_DETECTED"
RC_DISTRIBUTION_MARKET_AGREEMENT     = "DISTRIBUTION_MARKET_AGREEMENT"
RC_DISTRIBUTION_MARKET_DISAGREEMENT  = "DISTRIBUTION_MARKET_DISAGREEMENT"
RC_FRAGILITY_SWING_DETECTED          = "FRAGILITY_SWING_DETECTED"
RC_TAIL_FRAGILITY_SOURCE             = "TAIL_FRAGILITY_SOURCE"
RC_LEGACY_TAIL_FALLBACK_USED         = "LEGACY_TAIL_FALLBACK_USED"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:    # NaN guard
            return None
        return f
    except (TypeError, ValueError):
        return None


def _tf_interactions_to_dict(tail_fragility: Optional[dict]) -> dict:
    """Index tail_fragility.interactions by their reason code so the
    overlap detector can read deltas by family quickly."""
    out: dict = {}
    if not isinstance(tail_fragility, dict):
        return out
    for interaction in (tail_fragility.get("interactions") or []):
        code = interaction.get("code")
        delta = _safe_int(interaction.get("delta"))
        if code:
            out[code] = delta
    return out


def _detect_signal_overlap(
    tail_fragility:     Optional[dict],
    fragility_calibration: Optional[dict],
) -> dict:
    """Identify which signal families fired in both layers.

    Returns ``{families, risks, summary}`` where:
        * ``families`` — per-family ``{tf_delta, cal_delta,
          potential_double_count, severity}``
        * ``risks``    — list of ``{family, severity, tf_delta,
          cal_delta, evidence}`` (only families flagged as
          potential double counts)
        * ``summary``  — light counters useful at the pipeline level.
    """
    tf_avail = bool((tail_fragility or {}).get("available"))
    cal_avail = bool((fragility_calibration or {}).get("available"))

    tf_codes_to_delta = _tf_interactions_to_dict(tail_fragility)
    cal_components = (fragility_calibration or {}).get("component_deltas") or {}

    # tail_fragility "base_adjustment" is the spec value attributed to
    # the bucket; it is consumed by the calibrator as a single component
    # named "tail_fragility" — used to detect legacy parallel paths.
    tf_base = _safe_int((tail_fragility or {}).get("base_adjustment"), 0) if tf_avail else 0
    cal_tail_via_tf      = _safe_int(cal_components.get("tail_fragility"), 0)
    cal_tail_via_legacy  = _safe_int(cal_components.get("tail_risk"), 0)

    families: dict[str, dict] = {
        FAMILY_BULLPEN: {
            "tf_delta":  tf_codes_to_delta.get("TAIL_BULLPEN_INTERACTION", 0),
            "cal_delta": _safe_int(cal_components.get("both_bullpens_tired")),
        },
        FAMILY_DEFENSE: {
            "tf_delta":  tf_codes_to_delta.get("TAIL_DEFENSE_INTERACTION", 0),
            "cal_delta": _safe_int(cal_components.get("defense")),
        },
        FAMILY_SERIES: {
            "tf_delta":  tf_codes_to_delta.get("TAIL_SERIES_INTERACTION", 0),
            "cal_delta": _safe_int(cal_components.get("series_familiarity")),
        },
        FAMILY_STARTER: {
            "tf_delta":  tf_codes_to_delta.get("TAIL_STARTER_INTERACTION", 0),
            "cal_delta": _safe_int(cal_components.get("volatile_starter")),
        },
        FAMILY_TAIL: {
            # Tail family: tf_base on one side; calibrator consumed
            # either the Phase-55 path OR the legacy fallback.
            "tf_delta":  tf_base,
            "cal_delta": cal_tail_via_tf + cal_tail_via_legacy,
            "via_tail_fragility": cal_tail_via_tf,
            "via_legacy_fallback": cal_tail_via_legacy,
            "consumes_phase55_only": cal_tail_via_tf > 0 and cal_tail_via_legacy == 0,
        },
        FAMILY_TRAFFIC: {
            # Traffic never fires inside tail_fragility — calibrator-only.
            "tf_delta":  0,
            "cal_delta": _safe_int(cal_components.get("traffic")),
        },
    }

    risks: list[dict] = []
    for family, deltas in families.items():
        tf_d  = deltas["tf_delta"]
        cal_d = deltas["cal_delta"]
        potential = False
        severity: Optional[str] = None

        if family == FAMILY_TAIL:
            # The tail family is engineered so the calibrator picks
            # exactly ONE source. If both fire, it is a regression.
            both_paths = (cal_tail_via_tf > 0 and cal_tail_via_legacy > 0)
            if both_paths:
                potential = True
                severity = "HIGH"
        else:
            if tf_d > 0 and cal_d > 0:
                potential = True
                redundant = min(tf_d, cal_d)
                severity = "HIGH" if redundant >= 5 else "MEDIUM"

        deltas["potential_double_count"] = potential
        deltas["severity"] = severity
        if potential:
            risks.append({
                "family":   family,
                "severity": severity,
                "tf_delta":  tf_d,
                "cal_delta": cal_d,
                "evidence": (
                    f"{family} contributed +{tf_d} via tail_fragility "
                    f"and +{cal_d} via fragility_calibrator"
                ),
            })

    summary = {
        "families_with_double_count": [r["family"] for r in risks],
        "double_count_count":          len(risks),
        "tail_fragility_available":    tf_avail,
        "fragility_calibration_available": cal_avail,
        "tail_consumed_via_phase55":   cal_tail_via_tf > 0,
        "tail_consumed_via_legacy":    cal_tail_via_legacy > 0,
    }
    return {"families": families, "risks": risks, "summary": summary}


# ─────────────────────────────────────────────────────────────────────
# Public API — Layer Interaction Audit
# ─────────────────────────────────────────────────────────────────────
def build_layer_interaction_audit(
    *,
    expected_runs_distribution: Optional[dict] = None,
    tail_risk:                  Optional[dict] = None,
    tail_fragility:             Optional[dict] = None,
    fragility_calibration:      Optional[dict] = None,
    # Raw signals (for transparency in the audit; observe-only).
    raw_traffic_score:                  Optional[float] = None,
    raw_defensive_breakdown_score:      Optional[float] = None,
    raw_defensive_breakdown_bucket:     Optional[str]   = None,
    raw_series_familiarity_score:       Optional[float] = None,
    raw_series_familiarity_bucket:      Optional[str]   = None,
    raw_bullpen_fatigue_high:           Optional[bool]  = None,
    raw_bullpen_usage_3d_home:          Optional[float] = None,
    raw_bullpen_usage_3d_away:          Optional[float] = None,
    raw_starter_era_worst:              Optional[float] = None,
    raw_starter_whip_worst:             Optional[float] = None,
) -> dict:
    """Build the Phase-56 layer-interaction-audit payload.

    All arguments are optional — when missing the function returns an
    ``available: False`` payload with explanatory reason codes.
    """
    reason_codes: list[str] = []

    erd_available = bool((expected_runs_distribution or {}).get("available"))
    tf_available  = bool((tail_fragility or {}).get("available"))
    cal_available = bool((fragility_calibration or {}).get("available"))

    if not (cal_available or tf_available or erd_available):
        return {
            "available":     False,
            "engine_version": ENGINE_VERSION,
            "reason_codes":  [RC_LAYER_AUDIT_UNAVAILABLE],
            "note":          "No layer payloads provided.",
        }

    reason_codes.append(RC_LAYER_AUDIT_USED)

    # ── Raw signals ────────────────────────────────────────────────
    raw_signals = {
        "traffic_score":              _safe_float(raw_traffic_score),
        "defensive_breakdown_score":  _safe_float(raw_defensive_breakdown_score),
        "defensive_breakdown_bucket": raw_defensive_breakdown_bucket,
        "series_familiarity_score":   _safe_float(raw_series_familiarity_score),
        "series_familiarity_bucket":  raw_series_familiarity_bucket,
        "bullpen_fatigue_high":       bool(raw_bullpen_fatigue_high) if raw_bullpen_fatigue_high is not None else None,
        "bullpen_usage_3d_home":      _safe_float(raw_bullpen_usage_3d_home),
        "bullpen_usage_3d_away":      _safe_float(raw_bullpen_usage_3d_away),
        "starter_era_worst":          _safe_float(raw_starter_era_worst),
        "starter_whip_worst":         _safe_float(raw_starter_whip_worst),
        "tail_p_ge_12":               _safe_float((tail_risk or {}).get("p_ge_12")),
        "tail_p_ge_14":               _safe_float((tail_risk or {}).get("p_ge_14")),
        "tail_p_ge_16":               _safe_float((tail_risk or {}).get("p_ge_16")),
        "tail_p_ge_18":               _safe_float((tail_risk or {}).get("p_ge_18")),
        "tail_bucket":                (tail_risk or {}).get("tail_bucket"),
    }

    # ── Layer outputs (compact) ───────────────────────────────────
    erd = expected_runs_distribution or {}
    tf  = tail_fragility or {}
    cal = fragility_calibration or {}
    layers = {
        "expected_runs_distribution": {
            "available":                 erd_available,
            "mean":                       _safe_float(erd.get("mean")),
            "median":                     _safe_float(erd.get("median")),
            "effective_dispersion_ratio": _safe_float(erd.get("effective_dispersion_ratio")),
            "uncertainty_bucket":         erd.get("uncertainty_bucket"),
            "distribution":               erd.get("distribution"),
        },
        "tail_fragility": {
            "available":             tf_available,
            "tail_bucket":           tf.get("tail_bucket"),
            "explosive_tail_score":  tf.get("explosive_tail_score"),
            "base_adjustment":       _safe_int(tf.get("base_adjustment")),
            "interactions":          list(tf.get("interactions") or []),
            "interaction_total":     _safe_int(tf.get("interaction_total")),
            "total_adjustment":      _safe_int(tf.get("total_adjustment")),
            "cap_hit":               bool(tf.get("cap_hit")),
            "reason_codes":          list(tf.get("reason_codes") or []),
        },
        "fragility_calibrator": {
            "available":            cal_available,
            "base_fragility":       _safe_int(cal.get("base_fragility")),
            "adjusted_fragility":   _safe_int(cal.get("adjusted_fragility")),
            "delta":                _safe_int(cal.get("delta")),
            "component_deltas":     dict(cal.get("component_deltas") or {}),
            "hidden_over_routes":   list(cal.get("hidden_over_routes") or []),
            "reason_codes":         list(cal.get("reason_codes") or []),
        },
    }

    overlap = _detect_signal_overlap(tail_fragility, fragility_calibration)

    if overlap["risks"]:
        reason_codes.append(RC_DOUBLE_COUNT_DETECTED)

    if overlap["summary"]["tail_consumed_via_phase55"]:
        reason_codes.append(RC_TAIL_FRAGILITY_SOURCE)
    if overlap["summary"]["tail_consumed_via_legacy"]:
        reason_codes.append(RC_LEGACY_TAIL_FALLBACK_USED)

    return {
        "available":      True,
        "engine_version": ENGINE_VERSION,
        "raw_signals":    raw_signals,
        "layers":         layers,
        "signal_overlap": overlap,
        "double_counting_risks": overlap["risks"],
        "reason_codes":   list(dict.fromkeys(reason_codes)),
        "note": (
            "Observe-only audit. This payload diagnoses possible "
            "signal double-counting; it does not change engine logic."
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# Public API — Distribution / Market Selection Effect
# ─────────────────────────────────────────────────────────────────────
def build_distribution_market_selection_effect(
    *,
    expected_runs_distribution: Optional[dict] = None,
    tail_risk:                  Optional[dict] = None,
    fragility_calibration:      Optional[dict] = None,
    market:                     Optional[str]  = None,
    market_line:                Optional[float] = None,
    market_side:                Optional[str]  = None,
    chosen_market_score:        Optional[float] = None,
    fragility_score_pre:        Optional[float] = None,
    # Threshold used by the orchestrator to kill picks (frag > 60 → no bet).
    fragility_kill_threshold:   float = 60.0,
    # Swing detector: how big a calibrator delta we consider a "swing"
    # (purely diagnostic; observe-only).
    swing_delta_threshold:      int = 8,
) -> dict:
    """Diagnose the relationship between what the distribution suggests
    (Under/Over by ``under_probability``/``over_probability``) and what
    the engine actually chose (``market``).

    Also surfaces a ``fragility_swing_detected`` flag when the
    calibrator delta moves fragility across the kill threshold (or
    moves it by ``swing_delta_threshold`` points or more).
    """
    if not (expected_runs_distribution or tail_risk or fragility_calibration):
        return {
            "available":     False,
            "engine_version": ENGINE_VERSION,
            "reason_codes":  [RC_LAYER_AUDIT_UNAVAILABLE],
        }

    erd = expected_runs_distribution or {}
    tr  = tail_risk or {}
    cal = fragility_calibration or {}

    under_prob = _safe_float(tr.get("under_probability"))
    over_prob  = _safe_float(tr.get("over_probability"))
    distribution_side: Optional[str] = None
    if under_prob is not None and over_prob is not None:
        if under_prob > over_prob + 0.02:
            distribution_side = "under"
        elif over_prob > under_prob + 0.02:
            distribution_side = "over"
        else:
            distribution_side = "neutral"

    engine_side_norm = (market_side or "").lower() or None
    if engine_side_norm not in ("under", "over"):
        if isinstance(market, str):
            ml = market.lower()
            engine_side_norm = (
                "under" if "under" in ml
                else "over" if "over" in ml
                else None
            )

    agreement: Optional[bool] = None
    if distribution_side and engine_side_norm and distribution_side != "neutral":
        agreement = (distribution_side == engine_side_norm)

    base_frag = _safe_float(fragility_score_pre)
    if base_frag is None:
        base_frag = _safe_float(cal.get("base_fragility"))
    adj_frag  = _safe_float(cal.get("adjusted_fragility"))
    cal_delta = _safe_int(cal.get("delta"))
    swing_kill_threshold = (
        base_frag is not None and adj_frag is not None
        and base_frag <= fragility_kill_threshold
        and adj_frag  >  fragility_kill_threshold
    )
    swing_magnitude = cal_delta >= swing_delta_threshold
    fragility_swing_detected = bool(swing_kill_threshold or swing_magnitude)

    reason_codes: list[str] = [RC_LAYER_AUDIT_USED]
    if agreement is True:
        reason_codes.append(RC_DISTRIBUTION_MARKET_AGREEMENT)
    elif agreement is False:
        reason_codes.append(RC_DISTRIBUTION_MARKET_DISAGREEMENT)
    if fragility_swing_detected:
        reason_codes.append(RC_FRAGILITY_SWING_DETECTED)

    return {
        "available":                True,
        "engine_version":            ENGINE_VERSION,
        "distribution_under_probability": under_prob,
        "distribution_over_probability":  over_prob,
        "distribution_natural_side":     distribution_side,
        "engine_chosen_market":          market,
        "engine_chosen_side":            engine_side_norm,
        "engine_chosen_market_score":    _safe_float(chosen_market_score),
        "market_line":                   _safe_float(market_line),
        "agreement":                     agreement,
        "fragility_score_pre":           base_frag,
        "fragility_score_post":          adj_frag,
        "fragility_calibrator_delta":    cal_delta,
        "fragility_swing_detected":      fragility_swing_detected,
        "fragility_swing_crosses_kill_threshold": swing_kill_threshold,
        "fragility_kill_threshold":      fragility_kill_threshold,
        "expected_runs_mean":            _safe_float(erd.get("mean")),
        "tail_bucket":                   tr.get("tail_bucket"),
        "reason_codes":                  list(dict.fromkeys(reason_codes)),
        "note": (
            "Observe-only audit. Market selection is NOT changed by "
            "this telemetry; it diagnoses whether the engine's "
            "selection agrees with the distribution's natural side "
            "and whether the calibrator's delta swings fragility "
            "across the kill threshold."
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# Pipeline meta summarisation
# ─────────────────────────────────────────────────────────────────────
def summarise_for_pipeline_meta(audit_payload: dict) -> dict:
    """Condense a per-pick audit into a few scalars for the slate-level
    ``pipeline_meta.layer_interaction_audit`` aggregate."""
    if not isinstance(audit_payload, dict) or not audit_payload.get("available"):
        return {"available": False}
    overlap = audit_payload.get("signal_overlap") or {}
    summary = overlap.get("summary") or {}
    return {
        "available":                  True,
        "engine_version":             ENGINE_VERSION,
        "double_count_count":         summary.get("double_count_count", 0),
        "families_with_double_count": summary.get("families_with_double_count", []),
        "tail_consumed_via_phase55":  summary.get("tail_consumed_via_phase55", False),
        "tail_consumed_via_legacy":   summary.get("tail_consumed_via_legacy", False),
        "reason_codes":               list(audit_payload.get("reason_codes") or []),
    }


__all__ = [
    "ENGINE_VERSION",
    "FAMILY_BULLPEN", "FAMILY_DEFENSE", "FAMILY_SERIES",
    "FAMILY_STARTER", "FAMILY_TAIL", "FAMILY_TRAFFIC",
    "RC_LAYER_AUDIT_USED", "RC_LAYER_AUDIT_UNAVAILABLE",
    "RC_DOUBLE_COUNT_DETECTED",
    "RC_DISTRIBUTION_MARKET_AGREEMENT", "RC_DISTRIBUTION_MARKET_DISAGREEMENT",
    "RC_FRAGILITY_SWING_DETECTED",
    "RC_TAIL_FRAGILITY_SOURCE", "RC_LEGACY_TAIL_FALLBACK_USED",
    "build_layer_interaction_audit",
    "build_distribution_market_selection_effect",
    "summarise_for_pipeline_meta",
]
