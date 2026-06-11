#!/usr/bin/env python3
"""
Phase 56 — MLB Layer Interaction Audit (observe-only).

Goal
====
Audit the interaction between the three MLB analytics layers to detect
*double-counting* of signals (traffic, defense, bullpen, series, tail,
starter) without changing engine behaviour:

    1. mlb_expected_runs_distribution  → PMF/CDF + tail probabilities
    2. mlb_tail_fragility              → explosive-tail score + interactions
    3. mlb_fragility_calibrator        → hidden-over-route component deltas

The script runs a fully reproducible synthetic ablation by default
(``--mode synthetic``), comparing 4 modes:

    * ``FULL_CURRENT``                            — current baseline
    * ``NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR`` — mask traffic/defense
                                                    inputs to calibrator
    * ``NO_DISPERSION_SIGNAL_MODULATION``         — neutralise dispersion
                                                    (force Poisson ratio≈1)
    * ``LEGACY_SCALAR``                           — disable Phase-55 tail
                                                    fragility → calibrator
                                                    falls back to legacy
                                                    p_ge_12 logic

A ``real`` mode (``--mode real --days N``) is supported but only used
opt-in; default remains ``synthetic`` for deterministic CI runs.

Outputs
-------
* JSON: ``/app/backend/scripts/out/mlb_layer_interaction_audit_YYYYMMDD.json``
  (folder auto-created).
* stdout: compact summary (totals + guardrails + top overlaps).

Strictly observe-only
---------------------
* Does NOT mutate orchestrator state.
* Does NOT modify picks, market selection or polarity.
* Used to detect potential double-counting before any refactor.

Guardrails (sample-size)
------------------------
* ``n < 10``         → ``HIGH_RISK_WARNING``
* ``10 <= n < 30``   → ``LOW_SAMPLE_WARNING``
* ``30 <= n < 100``  → ``USEFUL_SAMPLE``
* ``n >= 100``       → ``VALIDATED_SAMPLE``
* ``tail_medium_high_samples < 20`` → ``TAIL_SAMPLE_TOO_LOW``
  (audit reported only; the script never promotes changes regardless).

CLI
---
::

    python scripts/audit_mlb_layer_interactions.py
    python scripts/audit_mlb_layer_interactions.py --mode synthetic --n 200
    python scripts/audit_mlb_layer_interactions.py --mode real --days 30
    python scripts/audit_mlb_layer_interactions.py --out /tmp/audit.json --quiet
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure backend on path when run as `python scripts/audit_...` from /app/backend
_THIS = Path(__file__).resolve()
_BACKEND_ROOT = _THIS.parents[1]    # /app/backend
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from services.mlb_tail_fragility import (
    compute_tail_fragility,
    BUCKET_HIGH,
    BUCKET_EXTREME,
)
from services.mlb_fragility_calibrator import calibrate_fragility


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_VERSION = "phase56.layer_interaction_audit.v1"
DEFAULT_OUT_DIR = _BACKEND_ROOT / "scripts" / "out"

MODES = (
    "FULL_CURRENT",
    "NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR",
    "NO_DISPERSION_SIGNAL_MODULATION",
    "LEGACY_SCALAR",
)

# Signal-family keys used for overlap detection.
FAMILY_BULLPEN  = "bullpen"
FAMILY_DEFENSE  = "defense"
FAMILY_SERIES   = "series"
FAMILY_STARTER  = "starter"
FAMILY_TAIL     = "tail"
FAMILY_TRAFFIC  = "traffic"

# Guardrail thresholds
GR_HIGH_RISK         = 10
GR_LOW_SAMPLE        = 30
GR_USEFUL            = 100
GR_TAIL_MIN_SAMPLES  = 20


# ─────────────────────────────────────────────────────────────────────────────
# Pure-Python PMF helpers (Poisson + Negative Binomial)
# Mirrors mlb_expected_runs_distribution._poisson_pmf / _nb_pmf so we can
# reconstruct tail probabilities synthetically without importing private
# helpers. Keeps the script self-contained.
# ─────────────────────────────────────────────────────────────────────────────
def _poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-mu) * (mu ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _nb_pmf(k: int, n: float, p: float) -> float:
    if n <= 0 or not (0.0 < p <= 1.0):
        return 0.0
    try:
        log_binom = (
            math.lgamma(k + n) - math.lgamma(k + 1) - math.lgamma(n)
        )
        log_p = log_binom + n * math.log(p) + k * math.log(1.0 - p)
        return math.exp(log_p)
    except (ValueError, OverflowError):
        return 0.0


def _tail_probs_from_mean_ratio(mean: float, ratio: float, max_k: int = 40) -> dict:
    """Return P(X>=12), P(>=14), P(>=16), P(>=18) for the (mean, ratio) pair.

    When ratio <= 1.001 we treat the distribution as Poisson; else Negative
    Binomial with ``p = 1/ratio`` and ``n = mean / (ratio - 1)``.
    """
    if ratio > 1.001:
        p_nb = 1.0 / ratio
        n_nb = mean / (ratio - 1.0)
        pmf = [_nb_pmf(k, n_nb, p_nb) for k in range(max_k + 1)]
    else:
        pmf = [_poisson_pmf(k, mean) for k in range(max_k + 1)]
    cdf: list[float] = []
    cum = 0.0
    for v in pmf:
        cum = min(1.0, cum + v)
        cdf.append(cum)

    def _p_ge(target: int) -> float:
        idx = max(0, target - 1)
        return round(1.0 - (cdf[idx] if idx < len(cdf) else 1.0), 6)

    return {
        "p_ge_12": _p_ge(12),
        "p_ge_14": _p_ge(14),
        "p_ge_16": _p_ge(16),
        "p_ge_18": _p_ge(18),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic case generator
# ─────────────────────────────────────────────────────────────────────────────
def generate_synthetic_cases(n: int, seed: int = 56) -> list[dict]:
    """Generate ``n`` deterministic synthetic cases covering a broad range
    of inputs (mean, dispersion ratio, bullpen fatigue, defensive
    breakdown, series familiarity, starter quality, traffic).

    Each case carries the *raw* inputs only; layer outputs are computed
    per-mode by ``run_modes_on_case``.
    """
    rng = random.Random(seed)
    cases: list[dict] = []
    for i in range(n):
        # We deliberately oversample HIGH-tail scenarios so the audit
        # can exercise the interaction code paths (TAIL_BULLPEN_/_DEFENSE_/
        # _SERIES_/_STARTER_ INTERACTION). Note: in real MLB the HIGH
        # tail bucket is rare — this synthetic boost exists ONLY to
        # exercise the overlap detector. The guardrail
        # ``TAIL_SAMPLE_TOO_LOW`` is the canonical signal that real
        # MLB samples rarely hit HIGH tail buckets.
        scenario = rng.random()
        if scenario < 0.25:
            # EXTREME-tail scenario: blow up mean to push tail_score≥50.
            mean = round(rng.uniform(14.5, 18.0), 2)
            ratio = round(rng.uniform(1.75, 2.50), 3)
        elif scenario < 0.60:
            # High-tail-ish scenario: NB + elevated mean.
            mean = round(rng.uniform(11.0, 14.0), 2)
            ratio = round(rng.uniform(1.45, 2.20), 3)
        elif scenario < 0.85:
            # Moderate dispersion.
            mean = round(rng.uniform(8.0, 11.0), 2)
            ratio = round(rng.uniform(1.15, 1.45), 3)
        else:
            # Control / Poisson realistic MLB.
            mean = round(rng.uniform(7.0, 9.5), 2)
            ratio = 1.0
        tail_probs = _tail_probs_from_mean_ratio(mean, ratio)

        # Bullpen fatigue: HIGH ~35% of the time.
        bullpen_usage_3d_h = round(rng.uniform(0.20, 0.85), 2)
        bullpen_usage_3d_a = round(rng.uniform(0.20, 0.85), 2)
        # Tail-fragility uses an OR over both sides (workload bucket HIGH).
        bullpen_high_either = (bullpen_usage_3d_h >= 0.65) or (bullpen_usage_3d_a >= 0.65)

        # Defensive breakdown score 0-100.
        defensive_score = round(rng.uniform(15.0, 85.0), 1)
        # Series familiarity score 0-100 (drives both calibrator and
        # tail interactions via bucket).
        series_score = round(rng.uniform(10.0, 80.0), 1)
        series_bucket = (
            "EXTREME" if series_score >= 80 else
            "HIGH" if series_score >= 60 else
            "MEDIUM" if series_score >= 40 else
            "LOW"
        )

        # Vulnerable starter — pick worst of two.
        era_h = round(rng.uniform(2.80, 6.20), 2)
        era_a = round(rng.uniform(2.80, 6.20), 2)
        whip_h = round(rng.uniform(0.95, 1.60), 2)
        whip_a = round(rng.uniform(0.95, 1.60), 2)
        starter_era_worst  = max(era_h, era_a)
        starter_whip_worst = max(whip_h, whip_a)

        # Traffic.
        traffic_score = round(rng.uniform(20.0, 90.0), 1)

        # Base fragility (pre-calibration).
        base_fragility = round(rng.uniform(15.0, 60.0), 1)

        # Inning lambda projection (forces λ7-9 elevated route in some).
        l13 = round(rng.uniform(1.5, 3.5), 2)
        l46 = round(rng.uniform(1.5, 3.5), 2)
        l79 = round(rng.uniform(1.5, 4.5), 2)

        # Market metadata
        market_line = round(rng.uniform(7.5, 10.5) * 2) / 2.0
        market_side = rng.choice(("under", "over"))

        cases.append({
            "case_id": i,
            "mean": mean,
            "effective_dispersion_ratio": ratio,
            "market_line": market_line,
            "market_side": market_side,
            "tail_probs": tail_probs,
            "bullpen_usage_3d_h": bullpen_usage_3d_h,
            "bullpen_usage_3d_a": bullpen_usage_3d_a,
            "bullpen_high_either": bullpen_high_either,
            "defensive_breakdown_score": defensive_score,
            "defensive_bucket": (
                "HIGH" if defensive_score >= 70 else
                "MEDIUM" if defensive_score >= 50 else
                "LOW"
            ),
            "series_familiarity_score": series_score,
            "series_familiarity_bucket": series_bucket,
            "starter_era_worst": starter_era_worst,
            "starter_whip_worst": starter_whip_worst,
            "traffic_score": traffic_score,
            "base_fragility": base_fragility,
            "inning_lambda_projection": {
                "lambda_1_3": l13, "lambda_4_6": l46, "lambda_7_9": l79,
            },
            "home_pitcher": {"era": era_h, "whip": whip_h},
            "away_pitcher": {"era": era_a, "whip": whip_a},
        })
    return cases


# ─────────────────────────────────────────────────────────────────────────────
# Per-mode runner
# ─────────────────────────────────────────────────────────────────────────────
def _build_tail_risk_payload(tail_probs: dict, market_line: float, market_side: str) -> dict:
    """Mock a ``compute_tail_risk`` payload that satisfies the contracts
    of both ``compute_tail_fragility`` and ``calibrate_fragility``
    (legacy fallback)."""
    return {
        "available":   True,
        "p_ge_12":     tail_probs["p_ge_12"],
        "p_ge_14":     tail_probs["p_ge_14"],
        "p_ge_16":     tail_probs["p_ge_16"],
        "p_ge_18":     tail_probs["p_ge_18"],
        "market_line": market_line,
        "side":        market_side,
    }


def run_modes_on_case(case: dict) -> dict:
    """Run the 4 modes on a single case. Returns the per-mode layer
    outputs + signal overlap diagnostics. Pure / deterministic."""
    per_mode: dict[str, dict] = {}

    # --- Pre-compute tail_probs variants for NO_DISPERSION_SIGNAL_MODULATION
    tp_full = case["tail_probs"]
    tp_no_disp = _tail_probs_from_mean_ratio(case["mean"], 1.0)

    for mode in MODES:
        # Pick tail probs to feed compute_tail_fragility.
        if mode == "NO_DISPERSION_SIGNAL_MODULATION":
            tail_probs = tp_no_disp
            effective_ratio = 1.0
        else:
            tail_probs = tp_full
            effective_ratio = case["effective_dispersion_ratio"]

        tail_risk_payload = _build_tail_risk_payload(
            tail_probs, case["market_line"], case["market_side"],
        )

        # ── Tail Fragility ──
        # In LEGACY_SCALAR we disable Phase-55 entirely; the calibrator
        # falls back to the legacy p_ge_12 path.
        if mode == "LEGACY_SCALAR":
            tail_fragility = {"available": False}
        else:
            tail_fragility = compute_tail_fragility(
                tail_risk_payload=tail_risk_payload,
                bullpen_fatigue_high=bool(case["bullpen_high_either"]),
                defensive_breakdown_bucket=case["defensive_bucket"],
                series_familiarity_bucket=case["series_familiarity_bucket"],
                starter_era=case["starter_era_worst"],
                starter_whip=case["starter_whip_worst"],
                market_side=case["market_side"],
            )

        # ── Fragility Calibrator inputs ──
        if mode == "NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR":
            traffic_in = None
            defense_in = None
        else:
            traffic_in = case["traffic_score"]
            defense_in = case["defensive_breakdown_score"]

        # Bullpens — give the calibrator the per-side usage so its
        # _is_tired logic can fire correctly.
        bullpen_home = {
            "bullpen_usage_3d": case["bullpen_usage_3d_h"],
            "bullpen_fatigue":  case["bullpen_usage_3d_h"],
        }
        bullpen_away = {
            "bullpen_usage_3d": case["bullpen_usage_3d_a"],
            "bullpen_fatigue":  case["bullpen_usage_3d_a"],
        }

        # Series familiarity — pass the score so the calibrator's >=40
        # rule lights up consistently.
        series_familiarity = {
            "series_familiarity_score": case["series_familiarity_score"],
            "bucket": case["series_familiarity_bucket"],
        }

        # When the tail_fragility payload is available, the calibrator
        # consumes it as the single tail source. In LEGACY_SCALAR mode
        # we pass `tail_risk` only and let the legacy fallback run.
        tail_fragility_for_calibrator = (
            tail_fragility if tail_fragility.get("available") else None
        )
        tail_risk_for_calibrator = (
            tail_risk_payload if (mode == "LEGACY_SCALAR") else None
        )

        calibration = calibrate_fragility(
            base_fragility=case["base_fragility"],
            market_side=case["market_side"],
            expected_runs=case["mean"],
            market_line=case["market_line"],
            inning_lambda_projection=case["inning_lambda_projection"],
            home_pitcher=case["home_pitcher"],
            away_pitcher=case["away_pitcher"],
            bullpen_home=bullpen_home,
            bullpen_away=bullpen_away,
            series_familiarity=series_familiarity,
            traffic_score=traffic_in,
            defensive_breakdown_score=defense_in,
            tail_risk=tail_risk_for_calibrator,
            tail_fragility=tail_fragility_for_calibrator,
        )

        # ── Signal overlap detection per case+mode ──
        overlap = _detect_signal_overlap(tail_fragility, calibration)

        per_mode[mode] = {
            "tail_fragility":       tail_fragility,
            "fragility_calibration": calibration,
            "effective_ratio_used":  effective_ratio,
            "tail_probs_used":       tail_probs,
            "signal_overlap":        overlap,
        }

    return per_mode


# ─────────────────────────────────────────────────────────────────────────────
# Overlap / double-counting detection
# ─────────────────────────────────────────────────────────────────────────────
def _detect_signal_overlap(tail_fragility: dict, calibration: dict) -> dict:
    """Identify whether the same signal family fired in both layers.

    Returns a dict keyed by family with ``{tf_delta, cal_delta,
    potential_double_count}`` plus a ``risks`` list of severity-tagged
    findings.
    """
    tf_interactions = tail_fragility.get("interactions") or []
    tf_total        = int(tail_fragility.get("total_adjustment") or 0)
    tf_available    = bool(tail_fragility.get("available"))

    tf_deltas_by_code = {
        i.get("code"): int(i.get("delta") or 0) for i in tf_interactions
    }
    cal_components = calibration.get("component_deltas") or {}

    families: dict[str, dict] = {
        FAMILY_BULLPEN: {
            "tf_delta": tf_deltas_by_code.get("TAIL_BULLPEN_INTERACTION", 0),
            "cal_delta": int(cal_components.get("both_bullpens_tired") or 0),
        },
        FAMILY_DEFENSE: {
            "tf_delta": tf_deltas_by_code.get("TAIL_DEFENSE_INTERACTION", 0),
            "cal_delta": int(cal_components.get("defense") or 0),
        },
        FAMILY_SERIES: {
            "tf_delta": tf_deltas_by_code.get("TAIL_SERIES_INTERACTION", 0),
            "cal_delta": int(cal_components.get("series_familiarity") or 0),
        },
        FAMILY_STARTER: {
            "tf_delta": tf_deltas_by_code.get("TAIL_STARTER_INTERACTION", 0),
            "cal_delta": int(cal_components.get("volatile_starter") or 0),
        },
        FAMILY_TAIL: {
            # tail family: the tail_fragility *base* is consumed by the
            # calibrator as ``component_deltas.tail_fragility`` when
            # available, OR via legacy ``component_deltas.tail_risk``.
            "tf_delta":  int(tail_fragility.get("base_adjustment") or 0) if tf_available else 0,
            "cal_delta": int(
                cal_components.get("tail_fragility")
                or cal_components.get("tail_risk")
                or 0
            ),
        },
        FAMILY_TRAFFIC: {
            "tf_delta": 0,   # traffic does NOT fire inside tail_fragility
            "cal_delta": int(cal_components.get("traffic") or 0),
        },
    }

    risks: list[dict] = []
    for family, deltas in families.items():
        tf_d  = deltas["tf_delta"]
        cal_d = deltas["cal_delta"]
        # Tail family is intentionally additive (tf base + cal envelope
        # of tf total), so we only flag when LEGACY path coexists with
        # tf path (which shouldn't happen — tf consumes it).
        potential = False
        severity = None
        if family == FAMILY_TAIL:
            # If calibrator consumed tail_fragility AND legacy tail_risk
            # at the same time (shouldn't happen with current code, but
            # the audit must catch any regression).
            both_paths = bool(
                cal_components.get("tail_fragility") and cal_components.get("tail_risk")
            )
            if both_paths:
                potential = True
                severity = "HIGH"
        else:
            if tf_d > 0 and cal_d > 0:
                potential = True
                # Severity scales with the smaller delta (the redundant
                # contribution). >= 5 → HIGH; otherwise MEDIUM.
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

    return {
        "families":              families,
        "risks":                  risks,
        "tail_fragility_total":   tf_total,
        "calibrator_delta":       int(calibration.get("delta") or 0),
        "calibrator_adjusted":    int(calibration.get("adjusted_fragility") or 0),
        "calibrator_base":        int(calibration.get("base_fragility") or 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────
def aggregate_results(cases: list[dict], per_case: list[dict]) -> dict:
    """Aggregate per-case mode outputs into mode-level summaries."""
    n = len(cases)
    mode_summary: dict[str, dict] = {}
    for mode in MODES:
        deltas       = []
        cap_hits     = 0
        tail_buckets = Counter()
        risk_counts  = Counter()
        family_double_count = Counter()
        family_avg_redundant = defaultdict(list)
        tail_high_or_extreme_samples = 0
        tf_total_sum = 0
        cal_delta_sum = 0
        adjusted_sum = 0
        for case_out in per_case:
            m = case_out[mode]
            tf  = m["tail_fragility"] or {}
            cal = m["fragility_calibration"] or {}
            overlap = m["signal_overlap"] or {}
            adjusted_sum += int(cal.get("adjusted_fragility") or 0)
            cal_delta_sum += int(cal.get("delta") or 0)
            tf_total_sum += int(tf.get("total_adjustment") or 0)
            deltas.append(int(cal.get("delta") or 0))
            if cal.get("reason_codes") and "FRAGILITY_DELTA_CAPPED" in cal.get("reason_codes"):
                cap_hits += 1
            bucket = tf.get("tail_bucket") or "LOW"
            tail_buckets[bucket] += 1
            if bucket in (BUCKET_HIGH, BUCKET_EXTREME):
                tail_high_or_extreme_samples += 1
            for risk in overlap.get("risks", []):
                risk_counts[(risk["family"], risk["severity"])] += 1
                family_double_count[risk["family"]] += 1
                family_avg_redundant[risk["family"]].append(
                    min(risk["tf_delta"], risk["cal_delta"])
                )

        mode_summary[mode] = {
            "n":                      n,
            "tail_bucket_distribution": dict(tail_buckets),
            "tail_high_or_extreme_samples": tail_high_or_extreme_samples,
            "cap_hits":               cap_hits,
            "cap_hit_rate":           round(cap_hits / n, 3) if n else 0.0,
            "calibrator_delta_avg":   round(cal_delta_sum / n, 3) if n else 0.0,
            "tail_fragility_total_avg": round(tf_total_sum / n, 3) if n else 0.0,
            "adjusted_fragility_avg": round(adjusted_sum / n, 3) if n else 0.0,
            "double_count_by_family": {
                fam: {
                    "count":         family_double_count[fam],
                    "rate":          round(family_double_count[fam] / n, 3) if n else 0.0,
                    "avg_redundant": round(
                        sum(family_avg_redundant[fam]) / len(family_avg_redundant[fam])
                        if family_avg_redundant[fam] else 0.0,
                        3,
                    ),
                }
                for fam in (FAMILY_BULLPEN, FAMILY_DEFENSE, FAMILY_SERIES,
                            FAMILY_STARTER, FAMILY_TAIL, FAMILY_TRAFFIC)
            },
            "risk_severity_counts": {
                f"{fam}::{sev}": count for (fam, sev), count in risk_counts.items()
            },
        }
    return mode_summary


def classify_sample_size(n: int) -> str:
    if n < GR_HIGH_RISK:
        return "HIGH_RISK_WARNING"
    if n < GR_LOW_SAMPLE:
        return "LOW_SAMPLE_WARNING"
    if n < GR_USEFUL:
        return "USEFUL_SAMPLE"
    return "VALIDATED_SAMPLE"


def build_guardrails(n: int, mode_summary: dict) -> dict:
    """Build observe-only guardrails. Mode-agnostic + tail-specific.

    Tail-sample is read from ``FULL_CURRENT`` only — ``LEGACY_SCALAR``
    deliberately disables Phase-55 (tail_fragility) so its bucket is
    always LOW and would skew the audit.
    """
    sample_label = classify_sample_size(n)
    full_mode = mode_summary.get("FULL_CURRENT") or {}
    tail_full = int(full_mode.get("tail_high_or_extreme_samples") or 0)
    tail_low = tail_full < GR_TAIL_MIN_SAMPLES
    return {
        "sample_size":            n,
        "sample_size_label":      sample_label,
        "tail_high_or_extreme_samples_full_current": tail_full,
        "tail_sample_too_low":    tail_low,
        "labels": (
            [sample_label]
            + (["TAIL_SAMPLE_TOO_LOW"] if tail_low else [])
        ),
        "promote_changes_allowed": (sample_label == "VALIDATED_SAMPLE" and not tail_low),
        "note": (
            "Observe-only audit. Even when promote_changes_allowed=True "
            "the script never mutates engine code; it is a diagnostic "
            "signal to the maintainer."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Real-mode (optional)
# ─────────────────────────────────────────────────────────────────────────────
async def _collect_real_picks(days: int) -> list[dict]:
    """Best-effort collection of real picks from recent MLB days. Each
    pick is converted into a synthetic-shaped case so the same modes/
    overlap detector apply uniformly. Fail-soft: returns ``[]`` if the
    orchestrator or DB are not reachable.
    """
    cases: list[dict] = []
    try:
        from datetime import timedelta
        from services.mlb_day_orchestrator import analyze_mlb_day
    except Exception:
        return cases
    today = datetime.now(timezone.utc).date()
    for d_off in range(days):
        the_date = today - timedelta(days=d_off)
        date_str = the_date.strftime("%Y-%m-%d")
        try:
            payload = await analyze_mlb_day(date_str, db=None)
        except Exception:
            continue
        for pick in (payload or {}).get("picks", []) or []:
            case = _real_pick_to_case(pick)
            if case is not None:
                cases.append(case)
    return cases


def _real_pick_to_case(pick: dict) -> Optional[dict]:
    """Project a live pick_payload into the synthetic case shape.
    Returns ``None`` when essential fields are missing."""
    if not isinstance(pick, dict):
        return None
    tail_risk_p = (pick.get("tail_risk") or {})
    if not tail_risk_p.get("available"):
        return None
    erd = pick.get("expected_runs_distribution") or {}
    mean = erd.get("mean") or pick.get("expected_runs")
    if mean is None:
        return None
    market = (pick.get("recommendation") or {}).get("market") or pick.get("market") or ""
    market_side = (
        "under" if "under" in str(market).lower()
        else "over" if "over" in str(market).lower()
        else "under"
    )
    market_line = (
        (pick.get("recommendation") or {}).get("line")
        or pick.get("line") or pick.get("book_total") or 9.0
    )
    series_score = (pick.get("series_familiarity") or {}).get("series_familiarity_score") or 0.0
    series_bucket = (pick.get("series_familiarity") or {}).get("bucket") or "LOW"
    defensive_score = (pick.get("defensive_breakdown") or {}).get("defensive_breakdown_score") or 0.0
    traffic_score = (pick.get("traffic_score_obj") or {}).get("traffic_score") or 0.0
    return {
        "case_id": pick.get("game_pk"),
        "mean": float(mean),
        "effective_dispersion_ratio": float(erd.get("effective_dispersion_ratio") or 1.0),
        "market_line": float(market_line),
        "market_side": market_side,
        "tail_probs": {
            "p_ge_12": tail_risk_p.get("p_ge_12") or 0.0,
            "p_ge_14": tail_risk_p.get("p_ge_14") or 0.0,
            "p_ge_16": tail_risk_p.get("p_ge_16") or 0.0,
            "p_ge_18": tail_risk_p.get("p_ge_18") or 0.0,
        },
        "bullpen_usage_3d_h": 0.55,
        "bullpen_usage_3d_a": 0.55,
        "bullpen_high_either": False,
        "defensive_breakdown_score": float(defensive_score),
        "defensive_bucket": (
            "HIGH" if defensive_score >= 70 else
            "MEDIUM" if defensive_score >= 50 else
            "LOW"
        ),
        "series_familiarity_score": float(series_score),
        "series_familiarity_bucket": series_bucket,
        "starter_era_worst": 4.50,
        "starter_whip_worst": 1.30,
        "traffic_score": float(traffic_score),
        "base_fragility": float(pick.get("fragility_score") or 25.0),
        "inning_lambda_projection": (pick.get("inning_lambda_projection") or {}),
        "home_pitcher": {"era": 4.0, "whip": 1.25},
        "away_pitcher": {"era": 4.0, "whip": 1.25},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Output writers
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_out_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _default_out_path(out_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return out_dir / f"mlb_layer_interaction_audit_{stamp}.json"


def write_report(path: Path, report: dict) -> None:
    _ensure_out_dir(path.parent)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False, default=str)


def print_stdout_summary(report: dict) -> None:
    print(f"\n=== PHASE 56 — MLB Layer Interaction Audit ({report['mode_label']}) ===")
    print(f"version          : {report['script_version']}")
    print(f"generated_at     : {report['generated_at']}")
    print(f"sample_size      : {report['guardrails']['sample_size']}"
          f"  ({report['guardrails']['sample_size_label']})")
    print(f"tail_full_samples: {report['guardrails']['tail_high_or_extreme_samples_full_current']}"
          f"  too_low={report['guardrails']['tail_sample_too_low']}")
    print(f"guardrail_labels : {report['guardrails']['labels']}")
    print(f"promote_changes  : {report['guardrails']['promote_changes_allowed']}")
    print("")
    print("Mode-level summary (adjusted_avg | cal_delta_avg | tf_total_avg | cap_hit_rate):")
    for mode in MODES:
        ms = report["modes"][mode]
        print(
            f"  {mode:<45}  "
            f"adj={ms['adjusted_fragility_avg']:>6}  "
            f"cal_d={ms['calibrator_delta_avg']:>6}  "
            f"tf_t={ms['tail_fragility_total_avg']:>6}  "
            f"cap={ms['cap_hit_rate']:>5}"
        )
    print("")
    print("Double-counting hot-spots (FULL_CURRENT mode, rate by family):")
    full = report["modes"]["FULL_CURRENT"]
    for fam, info in full["double_count_by_family"].items():
        marker = " <!>" if info["rate"] >= 0.20 else ""
        print(f"  {fam:<10}  count={info['count']:<4}  rate={info['rate']:.2f}  "
              f"avg_redundant={info['avg_redundant']}{marker}")
    print("")
    print(f"JSON report      : {report['output_path']}")
    print("=== END ===\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def build_report(
    mode_label: str,
    cases: list[dict],
    per_case: list[dict],
    output_path: Path,
) -> dict:
    mode_summary = aggregate_results(cases, per_case)
    guardrails   = build_guardrails(len(cases), mode_summary)
    # Trim per-case so the JSON stays manageable (default include first 25).
    per_case_excerpt = per_case[:25]
    return {
        "script_version": SCRIPT_VERSION,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "mode_label":     mode_label,
        "modes":          mode_summary,
        "guardrails":     guardrails,
        "modes_audited":  list(MODES),
        "n_cases":        len(cases),
        "per_case_excerpt": per_case_excerpt,
        "output_path":    str(output_path),
        "notes": [
            "Strictly observe-only. No engine code is modified.",
            "Phase 56 audit — looks for signal double-counting between "
            "expected_runs_distribution / mlb_tail_fragility / "
            "mlb_fragility_calibrator.",
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 56 — MLB Layer Interaction Audit (observe-only).",
    )
    p.add_argument("--mode", choices=("synthetic", "real"), default="synthetic",
                   help="Data source. Default: synthetic (deterministic).")
    p.add_argument("--n", type=int, default=200,
                   help="Number of synthetic cases (default 200). "
                        "Ignored when --mode real.")
    p.add_argument("--seed", type=int, default=56,
                   help="RNG seed for synthetic mode (default 56).")
    p.add_argument("--days", type=int, default=30,
                   help="Days back to scan when --mode real (default 30).")
    p.add_argument("--out", type=str, default=None,
                   help="Optional output path. Defaults to "
                        "/app/backend/scripts/out/mlb_layer_interaction_audit_YYYYMMDD.json")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress stdout summary (JSON still written).")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.mode == "synthetic":
        cases = generate_synthetic_cases(args.n, seed=args.seed)
        mode_label = f"synthetic[n={len(cases)},seed={args.seed}]"
    else:
        import asyncio
        try:
            cases = asyncio.run(_collect_real_picks(args.days))
        except Exception as exc:
            print(f"[phase56] real-mode collection failed ({exc}); "
                  f"falling back to synthetic.", file=sys.stderr)
            cases = generate_synthetic_cases(args.n, seed=args.seed)
            mode_label = f"synthetic_fallback[n={len(cases)},seed={args.seed}]"
        else:
            mode_label = f"real[days={args.days},n={len(cases)}]"
            if not cases:
                print("[phase56] real-mode returned 0 picks; "
                      "falling back to synthetic for a non-empty report.",
                      file=sys.stderr)
                cases = generate_synthetic_cases(args.n, seed=args.seed)
                mode_label = f"synthetic_fallback[n={len(cases)},seed={args.seed}]"

    per_case = [run_modes_on_case(c) for c in cases]

    out_path = Path(args.out) if args.out else _default_out_path(DEFAULT_OUT_DIR)
    report = build_report(mode_label, cases, per_case, out_path)
    write_report(out_path, report)

    if not args.quiet:
        print_stdout_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
