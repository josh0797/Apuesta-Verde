# SPRINT D4 — Final Honest Backtest Report

> **What changed in D4:**
> 1. Real opening **and** closing odds on a closed top league (Premier League 2024/25).
> 2. **Bootstrap CI** + **strict-positive significance** (`is_roi_significant = (roi_ci_low > 0)`).
> 3. **Sample-status taxonomy** (INSUFFICIENT / SMALL_SAMPLE_CAUTION / ADEQUATE).
> 4. **Closing-odds optimism warning** propagated end-to-end.
> 5. **Walk-forward `_calibration_audit`** on every prediction with strict-anti-leakage invariant `max_calib_date < target_date` (tests verify this).

---

## 1) Headline (Premier League 2024/25, DRAW market, walk-forward + calibration)

| Setup | N bets | hit_rate | ROI | CI 95% | Significant? | Sample status |
|---|---:|---:|---:|:---|:---:|:---|
| **Opening odds** (B365H/D/A) | **120** | 25.0% | **+18.11%** | `[-19.67%, +57.13%]` | ❌ **No** | 🟡 SMALL_SAMPLE_CAUTION |
| **Closing odds** (B365CH/CD/CA) | 120 | 24.2% | **+13.28%** | `[-22.58%, +51.23%]` | ❌ **No** | 🟡 SMALL_SAMPLE_CAUTION |

**Honesty deltas:**
- Switching from opening → closing odds **erodes ROI by ~5pp** — the classic sharp-line phenomenon.
- Neither setup is statistically significant (CI crosses 0 for both).
- Sample size is **below the 200-bet ADEQUATE threshold**, hence the `SMALL_SAMPLE_CAUTION` flag.

## 2) Reliability curve (opening odds, calibrated)

| Bucket | n | predicted_avg | actual_avg |
|---|---:|---:|---:|
| [0.20, 0.30) | 53 | 0.263 | 0.245 |
| [0.30, 0.40) | 51 | 0.326 | 0.216 |
| [0.60, 0.70) | 16 | 0.600 | 0.375 |

- Lower bucket [0.20–0.30) is well-calibrated (±2pp).
- The **[0.30–0.40) bucket over-predicts draws by 11pp** — likely culprit for some of the variance.
- The post-calibration "high-confidence" bucket [0.60–0.70) is **highly over-confident** (predicted 60%, actual 37.5%). This is the calibrator's reaction to past good runs, then mean-reverting.

## 3) Edge-bucket breakdown

| Edge bucket | n | won | ROI | hit_rate |
|---|---:|---:|---:|---:|
| 4-6pp   | 23 | 8  | **+48.9%** | 34.8% |
| 6-10pp  | 45 | 10 | +4.3%      | 22.2% |
| 10-15pp | 24 | 6  | **+57.3%** | 25.0% |
| 15pp+   | 28 | 6  | **-18.6%** | 21.4% |

⚠️ **Bimodal pattern**: edges in 4–6pp and 10–15pp do well, but extreme edges (15pp+) **lose money**. Classic sign that the model is over-confident at the tails (consistent with the reliability curve above).

## 4) Verdict (Premier League 2024/25)

> 🟡 **ROI nominally positive (+18% opening / +13% closing), but BOTH CIs straddle 0 → NOT statistically significant.**
> 🟡 **Sample size 120 picks → between INSUFFICIENT (50) and ADEQUATE (200).**
> ⚠️ Tail edges (15pp+) **lose money** — model is over-confident at the extreme.

**Operational implication:**
> Module remains in **observe-only** mode. Do NOT deploy. Either:
> 1. Expand the dataset to the 5 main European leagues 2024/25 (target ≥ 600 picks combined), or
> 2. Cap the firing edge at 10–15pp (the tail looks dangerous), and re-evaluate.

---

## 5) Cross-reference (Sprint D2/D3 calibration-only baselines)

| Tournament | Mode | Brier (combined) | hit_rate of fired | Sample status |
|---|---|---:|---:|---|
| WC 2022 (DRAW)    | no_market | 0.175 | 14.3% | INSUFFICIENT |
| Euro 2024 (DRAW)  | no_market | 0.277 | 41.2% | INSUFFICIENT |
| EPL 24/25 (DRAW, opening) | with_odds | n/a | 25.0% | SMALL_SAMPLE_CAUTION |

Across both **calibration-only** (D2/D3 national tournaments) and **with-odds** (D4 EPL), the framework now produces a **single, honest verdict per setup**: never declares production-readiness while sample size or CI says otherwise.

---

## 6) Engineering invariants verified

* ✅ **Point-in-time** discipline: every prediction's calibrator window had `max_calib_date < target_date`. Verified by 7 dedicated tests in `test_sprint_d4_walk_forward.py`.
* ✅ **Walk-forward is not a no-op**: comparing `walk_forward=True` vs `False` produces measurable differences in predicted probabilities.
* ✅ **ROI bootstrap CI**: 5000-resample non-parametric. CI excludes 0 only when there is genuine edge (verified by 25 unit tests in `test_sprint_d4_roi_significance.py`).
* ✅ **Closing-odds optimism warning**: propagates from CSV row → pick row → metrics → MD report.
* ✅ **Sample-status taxonomy**: clearly distinguishes INSUFFICIENT (<50) / CAUTION (50-200) / ADEQUATE (≥200).

---

## 7) Files

* `/app/backtest_epl_2425_draw_opening.md` + `.json` (opening odds)
* `/app/backtest_epl_2425_draw_closing.json` (closing odds, programmatic)
* `/app/backtest_d4_summary.md` (this file)

Tests:
* `/app/backend/tests/test_sprint_d4_roi_significance.py` (25 tests)
* `/app/backend/tests/test_sprint_d4_walk_forward.py` (7 tests)

Engine + metrics:
* `/app/backend/services/football_backtest_engine.py` (`_calibration_audit` per prediction)
* `/app/backend/services/football_backtest_metrics.py` (ROI CI + sample_status + warnings)
* `/app/backend/services/football_historical_ingestor.py` (parser with `odds_type` + warnings)
* `/app/backend/services/external_sources/the_odds_api_client.py` (historical snapshots client + cache)

---

*Generated by Sprint D4 backtest framework. The framework now meets the
honesty bar: every numeric claim is annotated with its confidence
interval and sample-status warning.*
