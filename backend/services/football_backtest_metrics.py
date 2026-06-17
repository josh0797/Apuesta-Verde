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
SMALL_SAMPLE_CAUTION_THRESHOLD = 200    # Sprint-D4 · 50 ≤ n < 200
DEFAULT_BOOTSTRAP_ITERS = 5000

# Sprint-D4 · Sample-status taxonomy.
SAMPLE_STATUS_INSUFFICIENT = "INSUFFICIENT_SAMPLE_DO_NOT_TRUST"
SAMPLE_STATUS_CAUTION      = "SMALL_SAMPLE_CAUTION"
SAMPLE_STATUS_ADEQUATE     = "ADEQUATE_SAMPLE"

# Sprint-D4 · Warning codes (canonical).
W_NOT_SIGNIFICANT          = "ROI_NOT_STATISTICALLY_SIGNIFICANT"
W_CLOSING_ODDS_OPTIMISTIC  = "ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC"
W_NO_ODDS_HITRATE_ONLY     = "NO_ODDS_HIT_RATE_ONLY"
W_INSUFFICIENT_SAMPLE      = "INSUFFICIENT_SAMPLE_DO_NOT_TRUST"
W_SMALL_SAMPLE_CAUTION     = "SMALL_SAMPLE_CAUTION"
W_ROI_SIGNIFICANTLY_NEGATIVE = "ROI_SIGNIFICANTLY_NEGATIVE"


def _resolve_sample_status(n_bets: int) -> str:
    if n_bets < SMALL_SAMPLE_THRESHOLD:
        return SAMPLE_STATUS_INSUFFICIENT
    if n_bets < SMALL_SAMPLE_CAUTION_THRESHOLD:
        return SAMPLE_STATUS_CAUTION
    return SAMPLE_STATUS_ADEQUATE


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
    no_market = bool(backtest_result.get("no_market"))
    if no_market:
        return _compute_no_market_metrics(backtest_result)

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
    is_roi_significant = None
    if per_pick_yield:
        ci_lo, ci_hi = _ci_bootstrap(per_pick_yield)
        # Sprint-D · legacy: significant in either direction.
        is_significant = (ci_lo > 0) or (ci_hi < 0)
        # Sprint-D4 · strict positive ROI significance (CI excludes 0
        # from below).
        is_roi_significant = (ci_lo > 0)

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
    sample_status = _resolve_sample_status(n_bets)

    # Sprint-D4 · Warnings list (canonical codes).
    warnings: list[str] = []
    if sample_status == SAMPLE_STATUS_INSUFFICIENT:
        warnings.append(W_INSUFFICIENT_SAMPLE)
    elif sample_status == SAMPLE_STATUS_CAUTION:
        warnings.append(W_SMALL_SAMPLE_CAUTION)
    if is_roi_significant is False and is_significant is False:
        warnings.append(W_NOT_SIGNIFICANT)
    if (ci_hi is not None and not (isinstance(ci_hi, float)
                                    and math.isnan(ci_hi))
            and ci_hi < 0):
        warnings.append(W_ROI_SIGNIFICANTLY_NEGATIVE)
    # Surface any per-pick warnings (e.g. closing-odds optimism).
    seen: set[str] = set()
    for p in picks:
        for w in (p.get("warnings") or []):
            if w not in seen:
                seen.add(w)
                warnings.append(w)
    # ROI undefined → degrade to no-odds informational warning.
    if roi is None and n_bets > 0:
        warnings.append(W_NO_ODDS_HITRATE_ONLY)

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
        # Sprint-D4 alias keys (lo/hi → low/high for the spec).
        "roi_ci_low":        round(ci_lo, 4) if ci_lo is not None and not math.isnan(ci_lo) else None,
        "roi_ci_high":       round(ci_hi, 4) if ci_hi is not None and not math.isnan(ci_hi) else None,
        "is_significant":    is_significant,
        "is_roi_significant": is_roi_significant,
        # Calibration & sample flags.
        "reliability_curve": curve,
        "calibration_label": calib_label,
        "small_sample_flag": small_sample_flag,
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "small_sample_warning":
            W_INSUFFICIENT_SAMPLE if small_sample_flag else None,
        # Sprint-D4 sample status + warnings.
        "sample_status":     sample_status,
        "warnings":          warnings,
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
    "SMALL_SAMPLE_CAUTION_THRESHOLD",
    "SAMPLE_STATUS_INSUFFICIENT",
    "SAMPLE_STATUS_CAUTION",
    "SAMPLE_STATUS_ADEQUATE",
    "W_NOT_SIGNIFICANT",
    "W_CLOSING_ODDS_OPTIMISTIC",
    "W_NO_ODDS_HITRATE_ONLY",
    "W_INSUFFICIENT_SAMPLE",
    "W_SMALL_SAMPLE_CAUTION",
    "W_ROI_SIGNIFICANTLY_NEGATIVE",
    "_resolve_sample_status",
    "_ci_bootstrap",
    "_max_drawdown",
    "_sharpe_like",
    "_calibration_label",
    # Sprint D2 (no-market) exports:
    "_compute_no_market_metrics",
    "_brier_score",
    "_log_loss",
    "_hit_rate_by_label",
    "_metrics_for_predictions_subset",
    # Sprint D3 exports:
    "_false_positive_examples",
    "_false_negative_examples",
    "_reliability_by_bucket",
]


# ─────────────────────────────────────────────────────────────────────
# Sprint-D2 · No-market (openfootball) metrics
# ─────────────────────────────────────────────────────────────────────
def _brier_score(predictions: list[dict]) -> Optional[float]:
    """Mean of (predicted_prob - outcome)**2 over all predictions.

    Lower is better. Perfect predictor = 0. Always-predicting-base-rate
    in football (~0.24) typically lands around 0.18..0.20.
    """
    if not predictions:
        return None
    vals = []
    for p in predictions:
        pp = p.get("predicted_prob")
        y = 1 if p.get("hit") else 0
        if pp is None:
            continue
        vals.append((float(pp) - y) ** 2)
    if not vals:
        return None
    return round(sum(vals) / len(vals), 5)


def _log_loss(predictions: list[dict]) -> Optional[float]:
    """Binary cross-entropy. Lower is better. Clamps predictions to
    [eps, 1-eps] to avoid log(0)."""
    if not predictions:
        return None
    import math
    eps = 1e-9
    vals = []
    for p in predictions:
        pp = p.get("predicted_prob")
        y = 1 if p.get("hit") else 0
        if pp is None:
            continue
        pp = max(eps, min(1.0 - eps, float(pp)))
        vals.append(-(y * math.log(pp) + (1 - y) * math.log(1 - pp)))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 5)


def _hit_rate_by_label(picks: list[dict]) -> dict[str, dict]:
    """Hit-rate of the label (VALUE_DRAW_CANDIDATE / STRONG_VALUE_DRAW /
    FAIR_DRAW_NO_EDGE / etc.)."""
    out: dict[str, dict] = {}
    for p in picks:
        lab = p.get("label") or "UNKNOWN"
        out.setdefault(lab, {"n": 0, "won": 0})
        out[lab]["n"] += 1
        out[lab]["won"] += int(bool(p.get("hit")))
    for v in out.values():
        v["hit_rate"] = round(v["won"] / v["n"], 4) if v["n"] else None
    return out


# ─────────────────────────────────────────────────────────────────────
# Sprint-D3 · False positive / false negative example extraction
# ─────────────────────────────────────────────────────────────────────
def _false_positive_examples(predictions: list[dict],
                              top_n: int = 10) -> list[dict]:
    """Return up to ``top_n`` predictions where the model FIRED (or
    would have, at high confidence) but the outcome did NOT hit.

    Sorted by descending predicted_prob (the most embarrassing misses
    first). We include both ``fired`` and ``non-fired`` high-confidence
    predictions so the user can audit threshold sensitivity.
    """
    fps = [p for p in predictions
           if not p.get("hit") and p.get("predicted_prob") is not None]
    fps.sort(key=lambda p: -float(p.get("predicted_prob", 0)))
    return [
        {
            "date":             p.get("date"),
            "home":             p.get("home"),
            "away":             p.get("away"),
            "predicted_prob":   p.get("predicted_prob"),
            "label":            p.get("label"),
            "actual_score":     p.get("actual_score"),
            "tournament_phase": p.get("tournament_phase"),
            "matchday":         p.get("matchday"),
            "group_label":      p.get("group_label"),
            "fired":            p.get("fired"),
        }
        for p in fps[:top_n]
    ]


def _false_negative_examples(predictions: list[dict],
                              top_n: int = 10) -> list[dict]:
    """Return up to ``top_n`` predictions where the outcome HIT but the
    model gave it a LOW probability (and presumably did not fire).

    Sorted by ascending predicted_prob (the most embarrassing misses
    first).
    """
    fns = [p for p in predictions
           if p.get("hit") and p.get("predicted_prob") is not None]
    fns.sort(key=lambda p: float(p.get("predicted_prob", 0)))
    return [
        {
            "date":             p.get("date"),
            "home":             p.get("home"),
            "away":             p.get("away"),
            "predicted_prob":   p.get("predicted_prob"),
            "label":            p.get("label"),
            "actual_score":     p.get("actual_score"),
            "tournament_phase": p.get("tournament_phase"),
            "matchday":         p.get("matchday"),
            "group_label":      p.get("group_label"),
            "fired":            p.get("fired"),
        }
        for p in fns[:top_n]
    ]


def _reliability_by_bucket(preds: list[dict],
                            n_buckets: int = 10) -> list[dict]:
    """Sprint-D3 expanded reliability table — same as ``reliability_curve``
    but each row also includes the bucket's hit-rate (so a single table
    can be rendered without computing it externally)."""
    rc = reliability_curve(preds, n_buckets=n_buckets)
    for row in rc:
        row["hit_rate"] = row.get("actual_avg")
    return rc


def _metrics_for_predictions_subset(preds: list[dict]) -> dict:
    """Brier + log-loss + reliability + base_rate for any subset of
    predictions. Used for both DRAW and Sprint-D3 protected markets."""
    n = len(preds)
    n_hits = sum(1 for p in preds if p.get("hit"))
    rc = _reliability_by_bucket(preds, n_buckets=10)
    return {
        "n_predictions":      n,
        # Sprint-D3 generic key:
        "n_hits":             n_hits,
        "base_rate":          round(n_hits / n, 4) if n else None,
        # Back-compat (Sprint-D2):
        "n_draws":            n_hits,
        "draw_base_rate":     round(n_hits / n, 4) if n else None,
        "brier_score":        _brier_score(preds),
        "log_loss":           _log_loss(preds),
        "reliability_curve":  rc,
        "reliability_by_bucket": rc,
        "calibration_label":  _calibration_label(rc),
    }


def _compute_no_market_metrics(backtest_result: dict) -> dict:
    """Sprint-D2 metrics for backtests run on datasets WITHOUT odds
    (openfootball WC2022 / Euro2024). The contract is intentionally
    different from the market-driven metrics: no ROI, no PnL, no CI.

    The user explicitly requested:
      * Brier Score + calibration curve
      * Hit-rate of the VALUE_DRAW_CANDIDATE label
      * Breakdown by group_stage / knockout / combined
    """
    predictions = backtest_result.get("predictions") or []
    picks       = backtest_result.get("picks") or []

    # Phase splits.
    group_preds    = [p for p in predictions if p.get("is_group_stage")]
    knockout_preds = [p for p in predictions
                      if (p.get("tournament_phase") == "KNOCKOUT"
                          or (p.get("tournament_phase") not in ("GROUP", None)
                              and not p.get("is_group_stage")))]
    group_picks    = [p for p in picks if p.get("is_group_stage")]
    knockout_picks = [p for p in picks
                      if (p.get("tournament_phase") == "KNOCKOUT"
                          or (p.get("tournament_phase") not in ("GROUP", None)
                              and not p.get("is_group_stage")))]

    # Core metrics: full sample.
    combined_metrics  = _metrics_for_predictions_subset(predictions)
    group_metrics     = _metrics_for_predictions_subset(group_preds)
    knockout_metrics  = _metrics_for_predictions_subset(knockout_preds)

    # Hit-rate of the label (only on FIRED picks).
    label_hit_rate_combined = _hit_rate_by_label(picks)
    label_hit_rate_group    = _hit_rate_by_label(group_picks)
    label_hit_rate_knockout = _hit_rate_by_label(knockout_picks)

    # Number of fires + overall hit-rate of fired picks.
    n_picks = len(picks)
    n_won   = sum(1 for p in picks if p.get("hit"))
    fired_hit_rate = round(n_won / n_picks, 4) if n_picks else None

    small_sample_flag = (n_picks < SMALL_SAMPLE_THRESHOLD)
    sample_status = _resolve_sample_status(n_picks)

    # Sprint-D4 · Warnings list (canonical codes).
    warnings: list[str] = []
    if sample_status == SAMPLE_STATUS_INSUFFICIENT:
        warnings.append(W_INSUFFICIENT_SAMPLE)
    elif sample_status == SAMPLE_STATUS_CAUTION:
        warnings.append(W_SMALL_SAMPLE_CAUTION)
    if n_picks > 0:
        warnings.append(W_NO_ODDS_HITRATE_ONLY)

    # Sprint-D3 · False positive / false negative auditing.
    # Focus on FIRED picks for false positives (high confidence missed);
    # focus on ALL predictions for false negatives (low confidence hit).
    fp_combined = _false_positive_examples(
        [p for p in predictions if p.get("fired")], top_n=10,
    )
    fn_combined = _false_negative_examples(
        [p for p in predictions if not p.get("fired")], top_n=10,
    )
    fp_group = _false_positive_examples(
        [p for p in group_preds if p.get("fired")], top_n=5,
    )
    fn_group = _false_negative_examples(
        [p for p in group_preds if not p.get("fired")], top_n=5,
    )
    fp_knockout = _false_positive_examples(
        [p for p in knockout_preds if p.get("fired")], top_n=5,
    )
    fn_knockout = _false_negative_examples(
        [p for p in knockout_preds if not p.get("fired")], top_n=5,
    )

    return {
        # ── Provenance ───────────────────────────────────────────────
        "mode":             "NO_MARKET",
        "market":           backtest_result.get("market"),
        "min_edge_pp":      backtest_result.get("min_edge_pp"),
        "min_pred_prob_pp": backtest_result.get("min_pred_prob_pp"),
        "stake_mode":       backtest_result.get("stake_mode"),
        "use_calibration":  backtest_result.get("use_calibration"),
        "walk_forward":     backtest_result.get("walk_forward"),
        "n_matches_total":  backtest_result.get("n_matches_total"),
        # ── Picks summary ────────────────────────────────────────────
        "n_predictions":    len(predictions),
        "n_candidates":     len(predictions),         # Sprint-D3 alias
        "n_picks_fired":    n_picks,
        "n_won":            n_won,
        "n_lost":           n_picks - n_won,
        "hit_rate":         fired_hit_rate,           # Sprint-D3 generic
        "hit_rate_fired":   fired_hit_rate,           # back-compat
        "small_sample_flag": small_sample_flag,
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "small_sample_warning":
            "INSUFFICIENT_SAMPLE_DO_NOT_TRUST" if small_sample_flag else None,
        # Sprint-D4 · sample status + warnings.
        "sample_status":     sample_status,
        "warnings":          warnings,
        # ── Quantitative metrics ─────────────────────────────────────
        "combined_metrics":   combined_metrics,
        "group_stage_metrics": group_metrics,
        "knockout_metrics":   knockout_metrics,
        # ── Label hit-rate breakdowns ────────────────────────────────
        "label_hit_rate_combined":    label_hit_rate_combined,
        "label_hit_rate_group_stage": label_hit_rate_group,
        "label_hit_rate_knockout":    label_hit_rate_knockout,
        # ── False positive / negative examples (Sprint D3) ───────────
        "false_positive_examples":           fp_combined,
        "false_negative_examples":           fn_combined,
        "false_positive_examples_group":     fp_group,
        "false_negative_examples_group":     fn_group,
        "false_positive_examples_knockout":  fp_knockout,
        "false_negative_examples_knockout":  fn_knockout,
        # ── For backwards-compatible dumps ───────────────────────────
        "no_market": True,
        # ROI fields kept None so downstream renderers do not break.
        "n_bets": n_picks,
        "roi": None,
        "yield_per_bet": None,
        "net_pnl": 0.0,
        "total_staked": 0.0,
        "total_returned": 0.0,
        "max_drawdown": 0.0,
        "sharpe_like": None,
        "roi_ci_lo": None,
        "roi_ci_hi": None,
        "is_significant": None,
        "calibration_label": combined_metrics.get("calibration_label"),
        "reliability_curve": combined_metrics.get("reliability_curve"),
    }
