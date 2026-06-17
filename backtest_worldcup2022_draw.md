# Football DRAW Backtest Report (No-Market)

Generated: 2026-06-17T07:28:53.142014Z

> ⚠️ This backtest runs on openfootball JSON (no odds available). ROI / yield are not reported. Metrics focus on model calibration (Brier + log-loss + reliability curve) and the hit-rate of fired labels.

## Configuration
- `market` = `DRAW`
- `min_pred_prob_pp` = `28.0`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Sample size
- n_matches_total:  `64`
- n_predictions:    `48`
- n_picks_fired:    `14`
- n_won:            `2`
- n_lost:           `12`
- hit_rate_fired:   `0.1429`
- small_sample_flag: `True`

## Quantitative calibration metrics

### Group Stage
- n_predictions: `32`
- n_draws: `5`
- draw_base_rate: `0.1562`
- brier_score: `0.14277`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.46281`
- calibration_label: `ACCEPTABLE_CALIBRATION`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 7 | 0.1596 | 0.1429 |
| [0.20,0.30) | 12 | 0.2324 | 0.1667 |
| [0.30,0.40) | 13 | 0.3201 | 0.1538 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

### Knockout
- n_predictions: `16`
- n_draws: `5`
- draw_base_rate: `0.3125`
- brier_score: `0.23938`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.69744`
- calibration_label: `MISCALIBRATED`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 16 | 0.1572 | 0.3125 |
| [0.20,0.30) | 0 | None | None |
| [0.30,0.40) | 0 | None | None |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

### Combined
- n_predictions: `48`
- n_draws: `10`
- draw_base_rate: `0.2083`
- brier_score: `0.17497`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.54102`
- calibration_label: `MISCALIBRATED`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 23 | 0.158 | 0.2609 |
| [0.20,0.30) | 12 | 0.2324 | 0.1667 |
| [0.30,0.40) | 13 | 0.3201 | 0.1538 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

## Label hit-rate (only fired picks)

### Group Stage
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE_DRAW | 7 | 2 | 0.2857 |
| VALUE_DRAW_CANDIDATE | 7 | 0 | 0.0 |

### Knockout
| Label | n | won | hit_rate |
|---|---|---|---|

### Combined
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE_DRAW | 7 | 2 | 0.2857 |
| VALUE_DRAW_CANDIDATE | 7 | 0 | 0.0 |

## Verdict

⚠️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 fired picks. Treat metrics as qualitative only.
