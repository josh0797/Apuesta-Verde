# Football DRAW Backtest Report

Generated: 2026-06-17T06:59:24.843044Z

## Configuration

- `market` = `DRAW`
- `min_edge_pp` = `4.0`
- `stake_mode` = `flat`
- `use_calibration` = `True`
- `walk_forward` = `True`

## Core results

- **n_matches_total**: `380`
- **n_bets**: `95`
- **n_won**: `13`
- **n_lost**: `82`
- **hit_rate**: `0.1368`
- **total_staked**: `95.0`
- **total_returned**: `57.14`
- **net_pnl**: `-37.86`
- **roi**: `-0.3985`
- **yield_per_bet**: `-0.3985`
- **max_drawdown**: `37.19`
- **sharpe_like**: `-2.536`
- **avg_edge_predicted_pp**: `8.506`
- **avg_edge_realised_pp**: `-6.682`
- **roi_ci_lo**: `-0.6807`
- **roi_ci_hi**: `-0.0753`
- **is_significant**: `True`
- **calibration_label**: `MISCALIBRATED`
- **small_sample_flag**: `False`
- **small_sample_warning**: `None`

## Reliability curve (predicted vs actual)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 7 | 0.1681 | 0.0 |
| [0.20,0.30) | 63 | 0.2815 | 0.1429 |
| [0.30,0.40) | 25 | 0.3406 | 0.16 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

## Breakdown by edge bucket

| Bucket | n | won | roi | hit_rate |
|---|---|---|---|---|
| 10-15pp | 17 | 1 | -0.6617647058823529 | 0.058823529411764705 |
| 15pp+ | 8 | 0 | -1.0 | 0.0 |
| 6-10pp | 40 | 7 | -0.24675000000000002 | 0.175 |
| 4-6pp | 30 | 5 | -0.29133333333333333 | 0.16666666666666666 |

## Breakdown by tier

| Tier | n | won | roi | hit_rate |
|---|---|---|---|---|
| STRONG_VALUE_DRAW | 48 | 5 | -0.5285416666666667 | 0.10416666666666667 |
| VALUE_DRAW_CANDIDATE | 47 | 8 | -0.26574468085106384 | 0.1702127659574468 |

## Verdict

❌ **NO APTO** — ROI negativo o no significativo.
