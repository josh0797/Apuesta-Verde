"""Sprint-D · Football Backtest Metrics.

Produces the quantitative report the user asked for:
* ROI + 95% bootstrap confidence interval + significance flag
* Reliability curve by decile (predicted vs actual)
* Max drawdown, Sharpe-like score, hit rate
* Small-sample flag (n < 50 → INSUFFICIENT_SAMPLE_DO_NOT_TRUST)
* Breakdowns by competition / edge bucket / label tier

All outputs are pure dicts so the CLI can dump them as JSON / MD.
"""
from __future__ import annotations

import math
import random
from statistics import mean
from typing import Optional

SMALL_SAMPLE_THRESHOLD = 50
DEFAULT_BOOTSTRAP_ITERS = 5000


def _ci_bootstrap(values: list[float], *, iters: int = DEFAULT_BOOTSTRAP_ITERS,
                   alpha: float = 0.05, seed: int = 42) -> tuple[float, float]:
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(iters):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[int((1 - alpha / 2) * iters) - 1]
    return lo, hi


def _max_drawdown(pnl_curve: list[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    cum = 0.0
    for x in pnl_curve:
        cum += x
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def _sharpe_like(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mu = mean(values)
    var = sum((x - mu) ** 2 for x in values) / (len(values) - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        return None
    # Scale by sqrt(n) so the metric loosely tracks t-stat magnitude.
    return round((mu / sd) * math.sqrt(len(values)), 3)


def reliability_curve(picks: list[dict], *, n_buckets: int = 10) -> list[dict]:
    """Per-decile predicted vs actual hit rate."""
    buckets: list[list[dict]] = [[] for _ in range(n_buckets)]
    for p in picks:
        pp = p.get("predicted_prob")
        if pp is None:
            continue
        idx = max(0, min(n_buckets - 1, int(pp * n_buckets)))
        buckets[idx].append(p)
    out: list[dict] = []
    for i, b in enumerate(buckets):
        lo = round(i / n_buckets, 2)
        hi = round((i + 1) / n_buckets, 2)
        if not b:
            out.append({"bucket": f"[{lo:.2f},{hi:.2f})", "n": 0,
                        "predicted_avg": None, "actual_avg": None})
            continue
        out.append({
            "bucket":        f"[{lo:.2f},{hi:.2f})",
            "n":             len(b),
            "predicted_avg": round(sum(p["predicted_prob"] for p in b) / len(b), 4),
            "actual_avg":    round(sum(int(p["hit"]) for p in b) / len(b), 4),
        })
    return out


def _calibration_label(curve: list[dict]) -> str:
    """Return WELL_CALIBRATED / MISCALIBRATED / INSUFFICIENT based on
    the bucket-level absolute mean error (weighted by n)."""
    weighted_err = 0.0
    total_n     = 0
    for b in curve:
        if b["predicted_avg"] is None or b["actual_avg"] is None:
            continue
        weighted_err += b["n"] * abs(b["predicted_avg"] - b["actual_avg"])
        total_n     += b["n"]
    if total_n == 0:
        return "INSUFFICIENT_CALIBRATION_DATA"
    mae = weighted_err / total_n
    if mae <= 0.05:
        return "WELL_CALIBRATED"
    if mae <= 0.10:
        return "ACCEPTABLE_CALIBRATION"
    return "MISCALIBRATED"


def compute_backtest_metrics(backtest_result: dict) -> dict:
    picks = backtest_result.get("picks") or []
    n_bets = len(picks)
    n_won  = sum(1 for p in picks if p.get("hit"))
    n_lost = n_bets - n_won
    total_staked = sum(p["stake"] for p in picks)
    total_returned = sum(
        (p["odd_draw"] * p["stake"]) if p.get("hit") else 0.0 for p in picks
    )
    net_pnl = sum(p["pnl"] for p in picks)
    hit_rate = (n_won / n_bets) if n_bets else None

    # ROI = net_pnl / total_staked. Per-pick "yield" = pnl/stake.
    per_pick_yield = [p["pnl"] / p["stake"] for p in picks if p["stake"] > 0]
    roi = (net_pnl / total_staked) if total_staked > 0 else None
    yield_per_bet = (sum(per_pick_yield) / len(per_pick_yield)
                     if per_pick_yield else None)

    pnl_curve = [p["pnl"] for p in picks]
    max_dd = _max_drawdown(pnl_curve) if pnl_curve else 0.0
    sharpe = _sharpe_like(per_pick_yield)

    # ROI CI via bootstrap of per-pick yields.
    ci_lo, ci_hi = (None, None)
    is_significant = None
    if per_pick_yield:
        ci_lo, ci_hi = _ci_bootstrap(per_pick_yield)
        is_significant = (ci_lo > 0) or (ci_hi < 0)

    # Average predicted/realised edge (in pp).
    avg_edge_pred = (mean([p["edge_pp"] for p in picks])
                     if picks else None)
    if picks:
        avg_edge_real_pp = round(
            100.0 * (hit_rate - mean([p["market_prob"] for p in picks])), 3
        )
    else:
        avg_edge_real_pp = None

    curve = reliability_curve(picks)
    calib_label = _calibration_label(curve)
    small_sample_flag = (n_bets < SMALL_SAMPLE_THRESHOLD)

    # Breakdowns.
    by_competition: dict[str, dict] = {}
    by_edge_bucket: dict[str, dict] = {}
    by_tier:        dict[str, dict] = {}
    for p in picks:
        # Competition
        comp = p.get("competition") or "UNKNOWN"
        by_competition.setdefault(comp, {"n": 0, "won": 0, "pnl": 0.0,
                                          "staked": 0.0})
        by_competition[comp]["n"]   += 1
        by_competition[comp]["won"]  += int(p["hit"])
        by_competition[comp]["pnl"]  += p["pnl"]
        by_competition[comp]["staked"] += p["stake"]
        # Edge bucket
        e = p["edge_pp"]
        if e is None:
            eb = "UNKNOWN"
        elif e < 6.0:
            eb = "4-6pp"
        elif e < 10.0:
            eb = "6-10pp"
        elif e < 15.0:
            eb = "10-15pp"
        else:
            eb = "15pp+"
        by_edge_bucket.setdefault(eb, {"n": 0, "won": 0, "pnl": 0.0,
                                        "staked": 0.0})
        by_edge_bucket[eb]["n"]   += 1
        by_edge_bucket[eb]["won"]  += int(p["hit"])
        by_edge_bucket[eb]["pnl"]  += p["pnl"]
        by_edge_bucket[eb]["staked"] += p["stake"]
        # Tier
        t = p.get("label") or "UNKNOWN"
        by_tier.setdefault(t, {"n": 0, "won": 0, "pnl": 0.0, "staked": 0.0})
        by_tier[t]["n"]   += 1
        by_tier[t]["won"]  += int(p["hit"])
        by_tier[t]["pnl"]  += p["pnl"]
        by_tier[t]["staked"] += p["stake"]

    # Roll-up roi per bucket.
    def _attach_roi(bucket: dict) -> None:
        for k, v in bucket.items():
            v["roi"] = (v["pnl"] / v["staked"]) if v["staked"] > 0 else None
            v["hit_rate"] = (v["won"] / v["n"]) if v["n"] else None
    for d in (by_competition, by_edge_bucket, by_tier):
        _attach_roi(d)

    return {
        # Core summary.
        "n_bets":            n_bets,
        "n_won":             n_won,
        "n_lost":            n_lost,
        "hit_rate":          round(hit_rate, 4) if hit_rate is not None else None,
        "total_staked":      round(total_staked, 4),
        "total_returned":    round(total_returned, 4),
        "net_pnl":           round(net_pnl, 4),
        "roi":               round(roi, 4) if roi is not None else None,
        "yield_per_bet":     round(yield_per_bet, 4) if yield_per_bet is not None else None,
        "max_drawdown":      max_dd,
        "sharpe_like":       sharpe,
        "avg_edge_predicted_pp": round(avg_edge_pred, 3) if avg_edge_pred is not None else None,
        "avg_edge_realised_pp":  avg_edge_real_pp,
        # CI + significance.
        "roi_ci_lo":         round(ci_lo, 4) if ci_lo is not None and not math.isnan(ci_lo) else None,
        "roi_ci_hi":         round(ci_hi, 4) if ci_hi is not None and not math.isnan(ci_hi) else None,
        "is_significant":    is_significant,
        # Calibration & sample flags.
        "reliability_curve": curve,
        "calibration_label": calib_label,
        "small_sample_flag": small_sample_flag,
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "small_sample_warning":
            "INSUFFICIENT_SAMPLE_DO_NOT_TRUST" if small_sample_flag else None,
        # Breakdowns.
        "breakdown_by_competition": by_competition,
        "breakdown_by_edge_bucket": by_edge_bucket,
        "breakdown_by_tier":        by_tier,
        # Provenance.
        "n_matches_total":   backtest_result.get("n_matches_total"),
        "market":            backtest_result.get("market"),
        "min_edge_pp":       backtest_result.get("min_edge_pp"),
        "stake_mode":        backtest_result.get("stake_mode"),
        "use_calibration":   backtest_result.get("use_calibration"),
        "walk_forward":      backtest_result.get("walk_forward"),
    }


__all__ = [
    "compute_backtest_metrics",
    "reliability_curve",
    "SMALL_SAMPLE_THRESHOLD",
    "_ci_bootstrap",
    "_max_drawdown",
    "_sharpe_like",
    "_calibration_label",
]
