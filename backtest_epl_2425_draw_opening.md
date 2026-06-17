# Football DRAW Backtest Report

Generated: 2026-06-17T14:20:26.698463Z

## Configuration

- `market` = `DRAW`
- `min_edge_pp` = `4.0`
- `stake_mode` = `flat`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Core results

- **n_matches_total**: `380`
- **n_bets**: `120`
- **n_won**: `30`
- **n_lost**: `90`
- **hit_rate**: `0.25`
- **total_staked**: `120.0`
- **total_returned**: `141.73`
- **net_pnl**: `21.73`
- **roi**: `0.1811`
- **yield_per_bet**: `0.1811`
- **max_drawdown**: `18.4`
- **sharpe_like**: `0.932`
- **avg_edge_predicted_pp**: `12.893`
- **avg_edge_realised_pp**: `4.453`
- **roi_ci_low**: `-0.1967`
- **roi_ci_high**: `0.5713`
- **is_significant**: `False`
- **is_roi_significant**: `False`
- **calibration_label**: `ACCEPTABLE_CALIBRATION`
- **sample_status**: `SMALL_SAMPLE_CAUTION`
- **small_sample_flag**: `False`
- **small_sample_warning**: `None`

## ⚠️  Warnings

- `SMALL_SAMPLE_CAUTION`
- `ROI_NOT_STATISTICALLY_SIGNIFICANT`

## Reliability curve (predicted vs actual)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 0 | None | None |
| [0.20,0.30) | 53 | 0.2628 | 0.2453 |
| [0.30,0.40) | 51 | 0.3255 | 0.2157 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 16 | 0.6 | 0.375 |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

## Breakdown by edge bucket

| Bucket | n | won | roi | hit_rate |
|---|---|---|---|---|
| 6-10pp | 45 | 10 | 0.04288888888888888 | 0.2222222222222222 |
| 10-15pp | 24 | 6 | 0.5729166666666666 | 0.25 |
| 15pp+ | 28 | 6 | -0.18571428571428572 | 0.21428571428571427 |
| 4-6pp | 23 | 8 | 0.4891304347826087 | 0.34782608695652173 |

## Breakdown by tier

| Tier | n | won | roi | hit_rate |
|---|---|---|---|---|
| VALUE_DRAW_CANDIDATE | 49 | 15 | 0.32 | 0.30612244897959184 |
| STRONG_VALUE_DRAW | 71 | 15 | 0.08521126760563381 | 0.2112676056338028 |

## Verdict

🟡 **SMALL_SAMPLE_CAUTION** — between 50 and 200 picks; CI interpretation should remain qualitative.
🟡 **ROI POSITIVO PERO NO SIGNIFICATIVO** — CI cruza 0; resultado nominalmente positivo no respaldado por la muestra.
