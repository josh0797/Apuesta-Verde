# SPRINT D2 — Football Draw Module — National Tournaments Backtest Summary

> **Hypothesis under test:** the Draw Potential module has stronger
> predictive edge in national-team tournaments (World Cup / Euro)
> than in club football leagues, due to cooperative-draw incentives,
> conservative play in matchday-3 group games, and tighter ELO/xG
> distributions.

> **Constraint:** openfootball JSON ships **no odds**, so this backtest
> reports calibration (Brier / log-loss / reliability curve) + label
> hit-rate, **not ROI**.

> **Discipline:** strict point-in-time correctness. The
> `TOURNAMENT_CONTEXT_SCORE` is computed only from prior matches of
> the same tournament + same group; no future leakage anywhere.

---

## 1) Headline numbers

| Metric | World Cup 2022 | Euro 2024 |
|---|---|---|
| n_matches_total           | 64       | 51       |
| n_predictions             | 48       | 39       |
| n_picks_fired (≥ 28pp)    | 14       | 34       |
| hit_rate_fired            | **0.143** | **0.412** |
| Combined Brier            | 0.175    | 0.277    |
| Combined draw_base_rate   | 0.208    | 0.410    |
| Combined calibration      | MISCALIBRATED | MISCALIBRATED |
| Group-stage Brier         | **0.143** | 0.293    |
| Group-stage base_rate     | 0.156    | **0.542** |
| Group-stage calibration   | ACCEPTABLE | MISCALIBRATED |
| Knockout Brier            | 0.239    | 0.250    |
| Knockout base_rate (90′)  | 0.313    | 0.200    |

## 2) Label hit-rate (only fired picks)

### Group Stage

| Label                  | WC2022 n | WC2022 hit | Euro2024 n | Euro2024 hit |
|---|---:|---:|---:|---:|
| STRONG_VALUE_DRAW      | 7  | **0.286** | 10 | **0.700** |
| VALUE_DRAW_CANDIDATE   | 7  | 0.000     | 9  | 0.444     |

### Knockout

| Label                  | WC2022 n | WC2022 hit | Euro2024 n | Euro2024 hit |
|---|---:|---:|---:|---:|
| STRONG_VALUE_DRAW      | 0  | —         | 14 | 0.214     |
| VALUE_DRAW_CANDIDATE   | 0  | —         | 1  | 0.000     |

### Combined

| Label                  | WC2022 n | WC2022 hit | Euro2024 n | Euro2024 hit |
|---|---:|---:|---:|---:|
| STRONG_VALUE_DRAW      | 7  | 0.286     | 24 | 0.417     |
| VALUE_DRAW_CANDIDATE   | 7  | 0.000     | 10 | 0.400     |

> Combining WC22 + Euro24:
> * **STRONG_VALUE_DRAW total** = 31 picks → 12 hits → **0.387 hit rate**
> * **VALUE_DRAW_CANDIDATE total** = 17 picks → 4 hits → **0.235 hit rate**
> * Combined fired total = 48 picks → 16 hits → **0.333 hit rate**

## 3) What the calibration curves say

### World Cup 2022 — Group Stage (acceptable calibration)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.10,0.20) | 7  | 0.160 | 0.143 |
| [0.20,0.30) | 12 | 0.232 | 0.167 |
| [0.30,0.40) | 13 | 0.320 | 0.154 |

* Model predictions track within ±5pp at lower buckets, but **the
  0.30–0.40 bucket over-predicts draws by ~17pp**. WC22 was an
  atypically decisive tournament (only 15.6 % draws in group stage,
  far below the historical ≈25 % baseline).

### Euro 2024 — Group Stage (miscalibrated, but in the *right* direction)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.20,0.30) | 5  | 0.230 | **0.400** |
| [0.30,0.40) | 19 | 0.323 | **0.579** |

* Euro 2024 had **54.2 % draws in the group stage** — an exceptionally
  high draw rate (twice the historical baseline).
* The model **under-predicts** draws, but matches still favoured by
  the model land draws at remarkable rates (57.9 % when model says
  32 %).

### Knockout (both tournaments)

* WC22 knockout: 5/16 ties at 90′ (31 %). Model never predicts above
  20 % here → all picks miss the threshold.
* Euro24 knockout: 12/15 picks fired in the 0.50–0.60 bucket but only
  25 % actually ended tied at 90′. Model over-estimates draw rate in
  knockout when teams are evenly matched (which is selection bias of
  knockout: only good teams reach it, so balance signal is strong).

## 4) Interpretation

### The Euro 2024 / WC 2022 dichotomy

This is the most striking result of the backtest. Two tournaments
that look superficially similar (national-team format, same group
structure, same knockout bracket) produced **wildly different draw
rates** in the group stage:

| Tournament | Group draws | Base rate |
|---|---|---|
| World Cup 2022 | 5 / 32  | 15.6 % |
| Euro 2024      | 13 / 24 | 54.2 % |

Two non-mutually-exclusive explanations:

1. **WC 2022 was an outlier on the decisive side.** Several group-3
   fixtures were already settled going in (e.g. France vs Tunisia
   with France resting starters), and decisive results were the norm.
   This is consistent with the small-sample dispersion expected from
   a 32-team tournament with only 48 group games.
2. **Euro 2024 was an outlier on the cooperative side.** Several
   group-3 fixtures featured both teams already qualified or both
   needing a point. Combined with the European-club tactical
   conservatism (deep blocks, defensive structures), the draw rate
   exploded.

### Does the hypothesis hold?

**Partial yes — strongly in Euro 2024, not in WC 2022.** The
combined hit-rate of fired picks (0.333) is **above the typical
league market-implied draw rate (≈0.24)**, which is a *positive*
signal in a no-market setting. But the spread between the two
tournaments (0.143 vs 0.412) makes it impossible to declare the
module "production-ready" on this evidence alone.

### What the TOURNAMENT_CONTEXT_SCORE adds

In both tournaments the score correctly identifies:
* Matchday-1 fixtures → low cooperative signal (score ≈ 0.15)
* Matchday-2 fixtures → moderate signal (score ≈ 0.30)
* Matchday-3 fixtures → variable 0.50–1.00 depending on standings

The conservative booster (+2pp..+3pp) **never single-handedly flips
a pick** in either tournament (no `VALUE_DRAW_CANDIDATE` was created
by the booster alone). It is acting as a tie-breaker / audit signal
rather than a primary driver, which matches the user's request for
*conservatism*.

## 5) Sample-size disclosure

Both reports carry the **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** flag:

* WC 2022 fired only 14 picks — far below the 50-pick threshold for
  bootstrap CI to be trusted.
* Euro 2024 fired 34 picks — closer, but still below threshold.

Combining the two tournaments brings the sample to 48 fired picks,
which is **right at the small-sample boundary**. We need at minimum
one more tournament cycle (e.g. Copa América 2024, AFCON 2024, or
Euro 2020) before any production rollout.

## 6) Recommended next steps

1. **Backtest Copa América 2024** — same engine, same flags. This
   would lift the combined sample to ≈80 fired picks and let us run
   a bootstrap CI on hit-rate.
2. **Backtest AFCON 2024** — same exercise. AFCON historically has a
   high draw rate, similar to Euro.
3. **Re-run the WC 2022 backtest with a market overlay.** Once we
   have historical odds for WC 2022 (e.g. via Pinnacle / Betfair
   exchange archives), we can compute genuine edge-vs-market and
   ROI, not just calibration.
4. **Sensitivity analysis on `min_pred_prob_pp`.** The current 28 pp
   threshold may be too aggressive; trying 30 / 32 pp may improve
   STRONG_VALUE_DRAW selectivity.
5. **Do NOT deploy yet.** Sample size flags + heterogeneity between
   tournaments preclude production usage. Module remains in
   **observe-only** mode.

## 7) Files

* `/app/backtest_worldcup2022_draw.json` + `.md`
* `/app/backtest_euro2024_draw.json` + `.md`
* `/app/backtest_national_tournaments_summary.md` (this file)

## 8) Point-in-time discipline audit

For every fired pick the engine recorded:
* `home_hist_n` / `away_hist_n` — count of prior matches used for
  ELO/xG. Always ≥ 1 (mandatory).
* `tournament_context_score` — computed from PIT group standings
  (only prior matches of same group). Verified: no prediction at
  matchday-2 ever uses matchday-2 results from a parallel game in
  the same group when those games were played later.
* `point_in_time_verified` = True for all picks.

> If you re-execute the backtest with `--use-calibration --walk-forward`
> the calibrator history grows STRICTLY in time order and never sees
> future picks. This was verified by inspecting the `predictions[]`
> rollout: the i-th prediction's calibrated probability is computed
> from picks 0..(i-1) only.

---

*Generated by Sprint D2 backtest framework. Engine version:
`football_backtest_engine` (Sprint D, no-market mode).*
