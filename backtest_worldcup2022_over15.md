# Football OVER_1_5 Backtest Report — World Cup 2022

Generated: 2026-06-17T12:41:57.760357Z

> ⚠️  Sprint-D3 · **observe_only + calibration_only**. No odds, no ROI. Metrics focus on calibration (Brier + reliability) + label hit-rate.

## Configuration
- `market` = `OVER_1_5`
- `min_pred_prob_pp` = `75.0`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Sample size
- n_matches_total:  `64`
- n_predictions:    `48`
- n_candidates:     `48`
- n_picks_fired:    `21`
- n_won:            `15`
- n_lost:           `6`
- hit_rate (fired): `0.7143`
- small_sample_flag: `True`

## Quantitative calibration metrics

### Group Stage
- n_predictions: `32`
- n_hits: `23`
- base_rate: `0.7188`
- brier_score: `0.32235` *(lower is better)*
- log_loss: `2.0598`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 2 | 0.0 | 1.0 | 1.0 |
| [0.10,0.20) | 3 | 0.1281 | 1.0 | 1.0 |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 2 | 0.3585 | 0.0 | 0.0 |
| [0.40,0.50) | 3 | 0.453 | 1.0 | 1.0 |
| [0.50,0.60) | 1 | 0.521 | 0.0 | 0.0 |
| [0.60,0.70) | 6 | 0.6688 | 0.8333 | 0.8333 |
| [0.70,0.80) | 3 | 0.7493 | 0.3333 | 0.3333 |
| [0.80,0.90) | 6 | 0.8547 | 0.6667 | 0.6667 |
| [0.90,1.00) | 6 | 0.9655 | 0.8333 | 0.8333 |

### Knockout
- n_predictions: `16`
- n_hits: `13`
- base_rate: `0.8125`
- brier_score: `0.27763` *(lower is better)*
- log_loss: `0.74525`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 5 | 0.333 | 1.0 | 1.0 |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 0 | None | None | None |
| [0.60,0.70) | 4 | 0.667 | 0.75 | 0.75 |
| [0.70,0.80) | 0 | None | None | None |
| [0.80,0.90) | 6 | 0.8 | 0.6667 | 0.6667 |
| [0.90,1.00) | 1 | 1.0 | 1.0 | 1.0 |

### Combined
- n_predictions: `48`
- n_hits: `36`
- base_rate: `0.75`
- brier_score: `0.30744` *(lower is better)*
- log_loss: `1.62161`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 2 | 0.0 | 1.0 | 1.0 |
| [0.10,0.20) | 3 | 0.1281 | 1.0 | 1.0 |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 7 | 0.3403 | 0.7143 | 0.7143 |
| [0.40,0.50) | 3 | 0.453 | 1.0 | 1.0 |
| [0.50,0.60) | 1 | 0.521 | 0.0 | 0.0 |
| [0.60,0.70) | 10 | 0.6681 | 0.8 | 0.8 |
| [0.70,0.80) | 3 | 0.7493 | 0.3333 | 0.3333 |
| [0.80,0.90) | 12 | 0.8273 | 0.6667 | 0.6667 |
| [0.90,1.00) | 7 | 0.9704 | 0.8571 | 0.8571 |

## Label hit-rate (only fired picks)

### Group Stage
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 8 | 7 | 0.875 |
| VALUE_CANDIDATE | 6 | 3 | 0.5 |

### Knockout
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_CANDIDATE | 6 | 4 | 0.6667 |
| STRONG_VALUE | 1 | 1 | 1.0 |

### Combined
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 9 | 8 | 0.8889 |
| VALUE_CANDIDATE | 12 | 7 | 0.5833 |

## False positive examples (high confidence, did NOT hit)

### Combined
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-11-25 | England vs USA | 0.976 | STRONG_VALUE | GROUP | 0-0 | True |
| 2022-11-30 | Tunisia vs France | 0.846 | VALUE_CANDIDATE | GROUP | 1-0 | True |
| 2022-11-28 | Brazil vs Switzerland | 0.84 | VALUE_CANDIDATE | GROUP | 1-0 | True |
| 2022-12-06 | Morocco vs Spain | 0.8 | VALUE_CANDIDATE | KNOCKOUT | 0-0 | True |
| 2022-12-10 | Morocco vs Portugal | 0.8 | VALUE_CANDIDATE | KNOCKOUT | 1-0 | True |
| 2022-11-29 | Iran vs USA | 0.761 | VALUE_CANDIDATE | GROUP | 0-1 | True |

## False negative examples (low confidence, DID hit)

### Combined
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2022-12-01 | Costa Rica vs Germany | 0.0 | NO_VALUE | GROUP | 2-4 | False |
| 2022-12-01 | Canada vs Morocco | 0.0 | NO_VALUE | GROUP | 1-2 | False |
| 2022-11-27 | Croatia vs Canada | 0.128 | NO_VALUE | GROUP | 4-1 | False |
| 2022-11-28 | Cameroon vs Serbia | 0.128 | NO_VALUE | GROUP | 3-3 | False |
| 2022-11-25 | Qatar vs Senegal | 0.1283 | NO_VALUE | GROUP | 1-3 | False |
| 2022-12-03 | Netherlands vs USA | 0.333 | NO_VALUE | KNOCKOUT | 3-1 | False |
| 2022-12-03 | Argentina vs Australia | 0.333 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2022-12-04 | France vs Poland | 0.333 | NO_VALUE | KNOCKOUT | 3-1 | False |
| 2022-12-05 | Japan vs Croatia | 0.333 | NO_VALUE | KNOCKOUT | 1-1 | False |
| 2022-12-05 | Brazil vs South Korea | 0.333 | NO_VALUE | KNOCKOUT | 4-1 | False |

## Verdict

⚠️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 fired picks. Treat metrics as qualitative only.
