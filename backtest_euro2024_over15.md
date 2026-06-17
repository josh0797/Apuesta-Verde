# Football OVER_1_5 Backtest Report — Euro 2024

Generated: 2026-06-17T12:41:57.774204Z

> ⚠️  Sprint-D3 · **observe_only + calibration_only**. No odds, no ROI. Metrics focus on calibration (Brier + reliability) + label hit-rate.

## Configuration
- `market` = `OVER_1_5`
- `min_pred_prob_pp` = `75.0`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Sample size
- n_matches_total:  `51`
- n_predictions:    `39`
- n_candidates:     `39`
- n_picks_fired:    `18`
- n_won:            `13`
- n_lost:           `5`
- hit_rate (fired): `0.7222`
- small_sample_flag: `True`

## Quantitative calibration metrics

### Group Stage
- n_predictions: `24`
- n_hits: `17`
- base_rate: `0.7083`
- brier_score: `0.24666` *(lower is better)*
- log_loss: `0.73659`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 1 | 0.384 | 1.0 | 1.0 |
| [0.40,0.50) | 3 | 0.453 | 1.0 | 1.0 |
| [0.50,0.60) | 3 | 0.5217 | 0.3333 | 0.3333 |
| [0.60,0.70) | 5 | 0.663 | 0.6 | 0.6 |
| [0.70,0.80) | 3 | 0.767 | 1.0 | 1.0 |
| [0.80,0.90) | 4 | 0.8685 | 0.5 | 0.5 |
| [0.90,1.00) | 5 | 0.9652 | 0.8 | 0.8 |

### Knockout
- n_predictions: `15`
- n_hits: `12`
- base_rate: `0.8`
- brier_score: `0.31182` *(lower is better)*
- log_loss: `3.26609`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 1 | 0.333 | 1.0 | 1.0 |
| [0.40,0.50) | 0 | None | None | None |
| [0.50,0.60) | 7 | 0.5 | 1.0 | 1.0 |
| [0.60,0.70) | 1 | 0.6 | 0.0 | 0.0 |
| [0.70,0.80) | 1 | 0.794 | 1.0 | 1.0 |
| [0.80,0.90) | 2 | 0.8 | 1.0 | 1.0 |
| [0.90,1.00) | 3 | 1.0 | 0.3333 | 0.3333 |

### Combined
- n_predictions: `39`
- n_hits: `29`
- base_rate: `0.7436`
- brier_score: `0.27172` *(lower is better)*
- log_loss: `1.70948`
- calibration_label: `MISCALIBRATED`

**Reliability curve (predicted vs actual)**

| Bucket | n | predicted_avg | actual_avg | hit_rate |
|---|---|---|---|---|
| [0.00,0.10) | 0 | None | None | None |
| [0.10,0.20) | 0 | None | None | None |
| [0.20,0.30) | 0 | None | None | None |
| [0.30,0.40) | 2 | 0.3585 | 1.0 | 1.0 |
| [0.40,0.50) | 3 | 0.453 | 1.0 | 1.0 |
| [0.50,0.60) | 10 | 0.5065 | 0.8 | 0.8 |
| [0.60,0.70) | 6 | 0.6525 | 0.5 | 0.5 |
| [0.70,0.80) | 4 | 0.7738 | 1.0 | 1.0 |
| [0.80,0.90) | 6 | 0.8457 | 0.6667 | 0.6667 |
| [0.90,1.00) | 8 | 0.9782 | 0.625 | 0.625 |

## Label hit-rate (only fired picks)

### Group Stage
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 7 | 5 | 0.7143 |
| VALUE_CANDIDATE | 5 | 4 | 0.8 |

### Knockout
| Label | n | won | hit_rate |
|---|---|---|---|
| VALUE_CANDIDATE | 3 | 3 | 1.0 |
| STRONG_VALUE | 3 | 1 | 0.3333 |

### Combined
| Label | n | won | hit_rate |
|---|---|---|---|
| STRONG_VALUE | 10 | 6 | 0.6 |
| VALUE_CANDIDATE | 8 | 7 | 0.875 |

## False positive examples (high confidence, did NOT hit)

### Combined
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-07-01 | France vs Belgium | 1.0 | STRONG_VALUE | KNOCKOUT | 1-0 | True |
| 2024-07-01 | Portugal vs Slovenia | 1.0 | STRONG_VALUE | KNOCKOUT | 0-0 | True |
| 2024-06-20 | Spain vs Italy | 0.97 | STRONG_VALUE | GROUP | 1-0 | True |
| 2024-06-24 | Albania vs Spain | 0.895 | STRONG_VALUE | GROUP | 0-1 | True |
| 2024-06-21 | Netherlands vs France | 0.84 | VALUE_CANDIDATE | GROUP | 0-0 | True |

## False negative examples (low confidence, DID hit)

### Combined
| Date | Match | Predicted | Label | Phase | Result | Fired |
|---|---|---|---|---|---|---|
| 2024-06-30 | England vs Slovakia | 0.333 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-06-19 | Croatia vs Albania | 0.384 | NO_VALUE | GROUP | 2-2 | False |
| 2024-06-20 | Slovenia vs Serbia | 0.453 | NO_VALUE | GROUP | 1-1 | False |
| 2024-06-21 | Poland vs Austria | 0.453 | NO_VALUE | GROUP | 1-3 | False |
| 2024-06-21 | Slovakia vs Ukraine | 0.453 | NO_VALUE | GROUP | 1-2 | False |
| 2024-06-29 | Germany vs Denmark | 0.5 | NO_VALUE | KNOCKOUT | 2-0 | False |
| 2024-06-30 | Spain vs Georgia | 0.5 | NO_VALUE | KNOCKOUT | 4-1 | False |
| 2024-07-06 | England vs Switzerland | 0.5 | NO_VALUE | KNOCKOUT | 1-1 | False |
| 2024-07-06 | Netherlands vs Turkey | 0.5 | NO_VALUE | KNOCKOUT | 2-1 | False |
| 2024-07-09 | Spain vs France | 0.5 | NO_VALUE | KNOCKOUT | 2-1 | False |

## Verdict

⚠️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 fired picks. Treat metrics as qualitative only.
