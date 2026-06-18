"""Sprint-D7-G · Calibration diagnostics (pure Python, fail-soft).

Builds a comprehensive diagnostic report for a predictive market model
given a list of *prediction records*. Each record must carry:

    * ``predicted_prob``       (model prob, ∈ [0, 1])
    * ``market_implied_raw``   (1 / odd, ∈ (0, 1))      — opening
    * ``market_implied_devig`` (de-vigged opening, ∈ (0, 1))
    * ``hit``                  (bool, the realized outcome)
    * ``odd_open``             (canonical pre-market odd)
    * ``odd_close``            (closing line odd; may be None)
    * ``market_implied_raw_close``   (optional)
    * ``market_implied_devig_close`` (optional)
    * ``label``                (informative; not required)

The module computes:

* Reliability curve (10 bins of width 0.10)
* Calibration intercept / slope (linear regression of y on p_pred)
* Brier score (model vs market vs market_devig)
* Log-loss (model vs market vs market_devig)
* Sharpness (stdev of p_pred + mean |p - 0.5|)
* AUC (Mann-Whitney form, ties = 0.5)
* Realized edge per bucket (hit_rate − market_devig)
* CLV diagnostics (opening vs closing line)
* Sample sizes and base rate

Every metric is fail-soft: if a record is missing inputs the row is
skipped and the audit tracks how many got dropped per metric.

Pure Python — no numpy / scipy dependency.
"""
from __future__ import annotations

import math
import statistics as stats
from typing import Iterable, Optional

# ── Defensive constants ───────────────────────────────────────────────
_EPS = 1e-12                   # numerical floor for log()
_LOGLOSS_CLIP = (1e-6, 1 - 1e-6)


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════
def _safe_prob(p) -> Optional[float]:
    """Coerce ``p`` to a float in (0, 1) or return None."""
    if p is None:
        return None
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return max(_LOGLOSS_CLIP[0], min(_LOGLOSS_CLIP[1], v))


def _brier(probs: list[float], outcomes: list[int]) -> Optional[float]:
    if not probs:
        return None
    return sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


def _log_loss(probs: list[float], outcomes: list[int]) -> Optional[float]:
    if not probs:
        return None
    s = 0.0
    for p, y in zip(probs, outcomes):
        p = max(_LOGLOSS_CLIP[0], min(_LOGLOSS_CLIP[1], p))
        s += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return s / len(probs)


def _auc_mann_whitney(probs: list[float], outcomes: list[int]
                        ) -> Optional[float]:
    """Mann-Whitney U formulation of AUC. Ties contribute 0.5."""
    pos = [p for p, y in zip(probs, outcomes) if y == 1]
    neg = [p for p, y in zip(probs, outcomes) if y == 0]
    if not pos or not neg:
        return None
    score = 0.0
    for pp in pos:
        for pn in neg:
            if pp > pn:
                score += 1.0
            elif pp == pn:
                score += 0.5
    return score / (len(pos) * len(neg))


def _linear_regression(xs: list[float], ys: list[float]
                        ) -> tuple[Optional[float], Optional[float],
                                     Optional[float]]:
    """Return (intercept, slope, r2) of OLS y ~ a + b·x.

    Used here as a *calibration regression*: a well-calibrated model
    yields a≈0, b≈1.
    """
    n = len(xs)
    if n < 2:
        return None, None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= _EPS:
        return None, None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    intercept = my - slope * mx
    r2 = (sxy * sxy / (sxx * syy)) if syy > _EPS else None
    return intercept, slope, r2


def _wilson_ci(k: int, n: int, z: float = 1.96
                ) -> tuple[Optional[float], Optional[float]]:
    """Wilson score interval — robust for small n."""
    if n <= 0:
        return None, None
    p = k / n
    denom = 1 + (z * z) / n
    centre = (p + (z * z) / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + (z * z) / (4 * n * n))) / denom
    return centre - half, centre + half


# ════════════════════════════════════════════════════════════════════════
# Reliability curve (10 equal-width buckets in [0, 1])
# ════════════════════════════════════════════════════════════════════════
def _reliability_buckets(records: list[dict], *, n_buckets: int = 10
                          ) -> list[dict]:
    buckets: list[dict] = []
    edges = [(i / n_buckets, (i + 1) / n_buckets) for i in range(n_buckets)]
    for lo, hi in edges:
        ps:  list[float] = []
        ys:  list[int]   = []
        mks: list[float] = []   # market_devig
        for r in records:
            p = _safe_prob(r.get("predicted_prob"))
            if p is None:
                continue
            in_bucket = (lo <= p < hi) if hi < 1.0 else (lo <= p <= hi)
            if not in_bucket:
                continue
            y = int(bool(r.get("hit")))
            mk = r.get("market_implied_devig")
            ps.append(p)
            ys.append(y)
            if mk is not None and 0 < mk < 1:
                mks.append(float(mk))
        n = len(ps)
        hit = (sum(ys) / n) if n else None
        mean_p = (sum(ps) / n) if n else None
        mean_mk = (sum(mks) / len(mks)) if mks else None
        edge_real = (hit - mean_mk) if (hit is not None and mean_mk is not None) else None
        ci_lo, ci_hi = _wilson_ci(sum(ys), n) if n else (None, None)
        buckets.append({
            "bucket":           f"[{lo:.1f}, {hi:.1f}]",
            "lo":               lo,
            "hi":               hi,
            "n":                n,
            "mean_p_pred":      round(mean_p, 4) if mean_p is not None else None,
            "hit_rate":         round(hit, 4) if hit is not None else None,
            "hit_rate_ci95":    (round(ci_lo, 4) if ci_lo is not None else None,
                                  round(ci_hi, 4) if ci_hi is not None else None),
            "mean_p_market_devig":
                round(mean_mk, 4) if mean_mk is not None else None,
            "realized_edge_pp":
                round(edge_real * 100.0, 2) if edge_real is not None else None,
        })
    return buckets


# ════════════════════════════════════════════════════════════════════════
# CLV (Closing Line Value)
# ════════════════════════════════════════════════════════════════════════
def _clv(records: list[dict], *, side_picks_only: bool = False
          ) -> dict:
    """Compute CLV between opening and closing implied probabilities.

    * ``clv_pp_mean`` = mean (close_devig − open_devig) × 100   (pp)
      Positive ⇒ market moved AGAINST the model's side
      (closing implied prob > opening implied prob).
      Negative ⇒ model beat the closing line.
    * ``clv_log_odds_mean`` = mean(log(odd_open / odd_close))
      Positive ⇒ closing odd dropped (line moved towards the side
      the bookmaker was eventually right about).
    """
    diffs_pp: list[float] = []
    diffs_logodds: list[float] = []
    n_have_close = 0
    n_picks_with_close = 0
    for r in records:
        if side_picks_only and not r.get("fired"):
            continue
        odd_o = r.get("odd_open")
        odd_c = r.get("odd_close")
        if odd_o is None or odd_c is None:
            continue
        if odd_o <= 1.0 or odd_c <= 1.0:
            continue
        po_d = r.get("market_implied_devig")
        pc_d = r.get("market_implied_devig_close")
        if po_d is None or pc_d is None:
            continue
        diffs_pp.append((pc_d - po_d) * 100.0)
        diffs_logodds.append(math.log(odd_o / odd_c))
        n_have_close += 1
        if r.get("fired"):
            n_picks_with_close += 1
    if not diffs_pp:
        return {"n_with_close":          0,
                "n_picks_with_close":    0,
                "clv_pp_mean":           None,
                "clv_pp_median":         None,
                "clv_pp_stdev":          None,
                "clv_log_odds_mean":     None,
                "clv_log_odds_median":   None,
                "clv_log_odds_stdev":    None}
    return {
        "n_with_close":         n_have_close,
        "n_picks_with_close":   n_picks_with_close,
        "clv_pp_mean":          round(stats.mean(diffs_pp), 4),
        "clv_pp_median":        round(stats.median(diffs_pp), 4),
        "clv_pp_stdev":         (round(stats.stdev(diffs_pp), 4)
                                   if len(diffs_pp) > 1 else None),
        "clv_log_odds_mean":    round(stats.mean(diffs_logodds), 6),
        "clv_log_odds_median":  round(stats.median(diffs_logodds), 6),
        "clv_log_odds_stdev":   (round(stats.stdev(diffs_logodds), 6)
                                    if len(diffs_logodds) > 1 else None),
    }


# ════════════════════════════════════════════════════════════════════════
# Main entry
# ════════════════════════════════════════════════════════════════════════
def compute_calibration_diagnostics(
    records: Iterable[dict],
    *,
    market: str = "UNKNOWN",
    n_buckets: int = 10,
) -> dict:
    """Compute a comprehensive calibration / discrimination report.

    Returns a dict with sections ``reliability_curve``, ``calibration``,
    ``discrimination``, ``model_vs_market``, ``clv``, ``meta``.
    """
    recs = [r for r in records
            if _safe_prob(r.get("predicted_prob")) is not None
            and r.get("hit") is not None]
    n_total = len(recs)
    audit: dict = {
        "n_records_total":       n_total,
        "n_with_market_implied":
            sum(1 for r in recs if r.get("market_implied_raw") is not None),
        "n_with_market_devig":
            sum(1 for r in recs if r.get("market_implied_devig") is not None),
        "n_with_close":
            sum(1 for r in recs if r.get("odd_close") is not None),
    }

    # 1) Reliability curve.
    buckets = _reliability_buckets(recs, n_buckets=n_buckets)

    # 2) Calibration intercept / slope (linear OLS y ~ a + b·p_pred).
    xs = [float(r["predicted_prob"]) for r in recs]
    ys = [int(bool(r["hit"])) for r in recs]
    intercept, slope, r2 = _linear_regression(xs, ys)

    # 3) Brier & log-loss — model.
    brier_model     = _brier(xs, ys)
    logloss_model   = _log_loss(xs, ys)

    # 4) Brier & log-loss — market (raw and de-vigged) on the same rows.
    xs_mk_raw  = [r.get("market_implied_raw")   for r in recs]
    xs_mk_dev  = [r.get("market_implied_devig") for r in recs]
    pairs_raw  = [(p, y) for p, y in zip(xs_mk_raw, ys)
                    if _safe_prob(p) is not None]
    pairs_dev  = [(p, y) for p, y in zip(xs_mk_dev, ys)
                    if _safe_prob(p) is not None]
    brier_market_raw     = (_brier([p for p, _ in pairs_raw],
                                      [y for _, y in pairs_raw])
                            if pairs_raw else None)
    logloss_market_raw   = (_log_loss([p for p, _ in pairs_raw],
                                         [y for _, y in pairs_raw])
                            if pairs_raw else None)
    brier_market_devig   = (_brier([p for p, _ in pairs_dev],
                                       [y for _, y in pairs_dev])
                            if pairs_dev else None)
    logloss_market_devig = (_log_loss([p for p, _ in pairs_dev],
                                          [y for _, y in pairs_dev])
                            if pairs_dev else None)

    # 5) Sharpness (model and market_devig).
    sharpness_stdev = stats.stdev(xs) if len(xs) > 1 else None
    sharpness_dist  = sum(abs(p - 0.5) for p in xs) / len(xs) if xs else None
    market_devig_xs = [p for (p, _) in pairs_dev]
    sharpness_mk_stdev = (stats.stdev(market_devig_xs)
                          if len(market_devig_xs) > 1 else None)
    sharpness_mk_dist  = (sum(abs(p - 0.5) for p in market_devig_xs)
                            / len(market_devig_xs)) if market_devig_xs else None

    # 6) AUC.
    auc = _auc_mann_whitney(xs, ys)
    auc_market_devig = _auc_mann_whitney(market_devig_xs,
                                           [y for _, y in pairs_dev])

    # 7) CLV.
    clv_all   = _clv(recs, side_picks_only=False)
    clv_picks = _clv(recs, side_picks_only=True)

    # 8) Base rate.
    base_rate = sum(ys) / n_total if n_total else None

    return {
        "market":     market,
        "meta":       {
            "n_records": n_total,
            "n_buckets": n_buckets,
            "audit":     audit,
            "base_rate_hit": round(base_rate, 4) if base_rate is not None else None,
        },
        "reliability_curve":   buckets,
        "calibration": {
            "intercept":   round(intercept, 4) if intercept is not None else None,
            "slope":       round(slope, 4) if slope is not None else None,
            "r_squared":   round(r2, 4) if r2 is not None else None,
            "interpretation":
                "Well-calibrated ⇒ intercept ≈ 0, slope ≈ 1.",
        },
        "model_vs_market": {
            "brier_model":          (round(brier_model, 6)
                                       if brier_model is not None else None),
            "brier_market_raw":     (round(brier_market_raw, 6)
                                       if brier_market_raw is not None else None),
            "brier_market_devig":   (round(brier_market_devig, 6)
                                       if brier_market_devig is not None else None),
            "logloss_model":        (round(logloss_model, 6)
                                       if logloss_model is not None else None),
            "logloss_market_raw":   (round(logloss_market_raw, 6)
                                       if logloss_market_raw is not None else None),
            "logloss_market_devig": (round(logloss_market_devig, 6)
                                       if logloss_market_devig is not None else None),
            "delta_brier_vs_devig":
                (round(brier_model - brier_market_devig, 6)
                  if brier_model is not None and brier_market_devig is not None
                  else None),
            "delta_logloss_vs_devig":
                (round(logloss_model - logloss_market_devig, 6)
                  if logloss_model is not None and logloss_market_devig is not None
                  else None),
            "interpretation":
                "Negative delta_brier_vs_devig OR delta_logloss_vs_devig "
                "⇒ model beats the de-vigged market on that metric.",
        },
        "discrimination": {
            "auc_model":          round(auc, 4) if auc is not None else None,
            "auc_market_devig":   (round(auc_market_devig, 4)
                                     if auc_market_devig is not None else None),
            "sharpness_stdev_model": (round(sharpness_stdev, 4)
                                        if sharpness_stdev is not None else None),
            "sharpness_dist_model":  (round(sharpness_dist, 4)
                                        if sharpness_dist is not None else None),
            "sharpness_stdev_market_devig":
                (round(sharpness_mk_stdev, 4)
                  if sharpness_mk_stdev is not None else None),
            "sharpness_dist_market_devig":
                (round(sharpness_mk_dist, 4)
                  if sharpness_mk_dist is not None else None),
            "interpretation":
                "AUC ≈ 0.50 ⇒ no discrimination; higher = better ordering. "
                "Sharpness too high vs market ⇒ overconfident; too low ⇒ flat.",
        },
        "clv": {
            "all_predictions":   clv_all,
            "picks_only":        clv_picks,
            "interpretation":
                "clv_pp_mean < 0 (or clv_log_odds_mean > 0) ⇒ model side "
                "beat the closing line on average.",
        },
    }


# ════════════════════════════════════════════════════════════════════════
# Convenience: classify model quality vs market using the user's rubric
# ════════════════════════════════════════════════════════════════════════
def classify_model_quality(report: dict) -> dict:
    """Apply the rubric:

    * **WELL_CALIBRATED_AND_BEATS_MARKET** — Brier_model < Brier_devig AND
      Log-loss_model < Log-loss_devig.
    * **WELL_CALIBRATED_BUT_NO_EDGE** — Brier_model ≈/≥ Brier_devig AND
      CLV ≤ 0 (model did not beat closing line) AND realized ROI ≈ -vig.
    * **MIS_CALIBRATED_BUT_DISCRIMINATIVE** — AUC > 0.55 AND calibration
      slope far from 1 or intercept far from 0.
    * **MIS_CALIBRATED_AND_FLAT** — AUC ≈ 0.50 AND reliability curve flat.
    """
    mvs = report.get("model_vs_market") or {}
    disc = report.get("discrimination") or {}
    cal  = report.get("calibration") or {}
    clv  = (report.get("clv") or {}).get("all_predictions") or {}
    b_m = mvs.get("brier_model")
    b_d = mvs.get("brier_market_devig")
    ll_m = mvs.get("logloss_model")
    ll_d = mvs.get("logloss_market_devig")
    auc  = disc.get("auc_model")
    slope = cal.get("slope")
    intercept = cal.get("intercept")
    clv_pp = clv.get("clv_pp_mean")

    verdict: list[str] = []

    def _is_close(a, b, tol=0.002):
        return (a is not None and b is not None and abs(a - b) <= tol)

    if (b_m is not None and b_d is not None and ll_m is not None
            and ll_d is not None
            and b_m < b_d - 0.001 and ll_m < ll_d - 0.001):
        verdict.append("WELL_CALIBRATED_AND_BEATS_MARKET")
    elif (b_m is not None and b_d is not None
            and (b_m > b_d or _is_close(b_m, b_d))
            and clv_pp is not None and clv_pp >= 0):
        verdict.append("WELL_CALIBRATED_BUT_NO_EDGE_OR_WORSE")
    if (auc is not None and auc > 0.55
            and ((slope is not None and (slope < 0.5 or slope > 1.5))
                  or (intercept is not None and abs(intercept) > 0.10))):
        verdict.append("MIS_CALIBRATED_BUT_DISCRIMINATIVE")
    if (auc is not None and 0.45 <= auc <= 0.55):
        verdict.append("LOW_DISCRIMINATION_AUC_NEAR_0_50")

    if not verdict:
        verdict.append("INCONCLUSIVE_OR_MIXED")

    return {
        "tags": verdict,
        "explanations": {
            "WELL_CALIBRATED_AND_BEATS_MARKET":
                "Modelo bien calibrado y mejor que mercado.",
            "WELL_CALIBRATED_BUT_NO_EDGE_OR_WORSE":
                "Modelo bien calibrado pero igual o peor que mercado.",
            "MIS_CALIBRATED_BUT_DISCRIMINATIVE":
                "Modelo descalibrado pero discriminativo (rescatable).",
            "LOW_DISCRIMINATION_AUC_NEAR_0_50":
                "Discriminación pobre (AUC ≈ 0.50): no rescatable solo con calibración.",
            "INCONCLUSIVE_OR_MIXED":
                "Resultado mixto; revisar métricas individualmente.",
        },
    }


__all__ = [
    "compute_calibration_diagnostics",
    "classify_model_quality",
]
