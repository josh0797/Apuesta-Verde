# Football DRAW Backtest Report (No-Market)

Generated: 2026-06-17T07:28:58.265052Z

> ⚠️ This backtest runs on openfootball JSON (no odds available). ROI / yield are not reported. Metrics focus on model calibration (Brier + log-loss + reliability curve) and the hit-rate of fired labels.

## Configuration
- `market` = `DRAW`
- `min_pred_prob_pp` = `28.0`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Sample size
- n_matches_total:  `51`
- n_predictions:    `39`
- n_picks_fired:    `34`
- n_won:            `14`
- n_lost:           `20`
- hit_rate_fired:   `0.4118`
- small_sample_flag: `True`

## Quantitative calibration metrics

### Group Stage
- n_predictions: `24`
- n_draws: `13`
- draw_base_rate: `0.5417`
- brier_score: `0.2933`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.78553`
- calibration_label: `MISCALIBRATED`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 0 | None | None |
| [0.20,0.30) | 5 | 0.2304 | 0.4 |
| [0.30,0.40) | 19 | 0.3227 | 0.5789 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

### Knockout
- n_predictions: `15`
- n_draws: `3`
- draw_base_rate: `0.2`
- brier_score: `0.24995`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.692`
- calibration_label: `MISCALIBRATED`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 0 | None | None |
| [0.20,0.30) | 0 | None | None |
| [0.30,0.40) | 1 | 0.315 | 0.0 |
| [0.40,0.50) | 2 | 0.4 | 0.0 |
| [0.50,0.60) | 12 | 0.55 | 0.25 |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

### Combined
- n_predictions: `39`
- n_draws: `16`
- draw_base_rate: `0.4103`
- brier_score: `0.27663`  *(lower is better; ≤0.18 = decent for draw market)*
- log_loss: `0.74956`
- calibration_label: `MISCALIBRATED`

**Calibration curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 0 | None | None |
| [0.20,0.30) | 5 | 0.2304 | 0.4 |
| [0.30,0.40) | 20 | 0.3224 | 0.55 |
| [0.40,0.50) | 2 | 0.4 | 0.0 |
| [0.50,0.60) | 12 | 0.55 | 0.25 |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

## Label hit-rate (only fired picks)

### Group Stage
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_DRAW_CANDIDATE | 9 | 4 | 0.4444 |
| STRONG_VALUE_DRAW | 10 | 7 | 0.7 |

### Knockout
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_DRAW_CANDIDATE | 1 | 0 | 0.0 |
| STRONG_VALUE_DRAW | 14 | 3 | 0.2143 |

### Combined
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_DRAW_CANDIDATE | 10 | 4 | 0.4 |
| STRONG_VALUE_DRAW | 24 | 10 | 0.4167 |

## Verdict

⚠️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 fired picks. Treat metrics as qualitative only.
