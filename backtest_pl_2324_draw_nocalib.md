# Football DRAW Backtest Report

Generated: 2026-06-17T06:59:42.961904Z

## Configuration

- `market` = `DRAW`
- `min_edge_pp` = `4.0`
- `stake_mode` = `flat`
- `use_calibration` = `False`
- `walk_forward` = `True`

## Core results

- **n_matches_total**: `380`
- **n_bets**: `224`
- **n_won**: `40`
- **n_lost**: `184`
- **hit_rate**: `0.1786`
- **total_staked**: `224.0`
- **total_returned**: `182.14`
- **net_pnl**: `-41.86`
- **roi**: `-0.1869`
- **yield_per_bet**: `-0.1869`
- **max_drawdown**: `40.86`
- **sharpe_like**: `-1.554`
- **avg_edge_predicted_pp**: `8.05`
- **avg_edge_realised_pp**: `-4.089`
- **roi_ci_lo**: `-0.4087`
- **roi_ci_hi**: `0.0574`
- **is_significant**: `False`
- **calibration_label**: `MISCALIBRATED`
- **small_sample_flag**: `False`
- **small_sample_warning**: `None`

## Reliability curve (predicted vs actual)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 9 | 0.1887 | 0.0 |
| [0.20,0.30) | 73 | 0.2785 | 0.1918 |
| [0.30,0.40) | 142 | 0.3181 | 0.1831 |
| [0.40,0.50) | 0 | None | None |
| [0.50,0.60) | 0 | None | None |
| [0.60,0.70) | 0 | None | None |
| [0.70,0.80) | 0 | None | None |
| [0.80,0.90) | 0 | None | None |
| [0.90,1.00) | 0 | None | None |

## Breakdown by edge bucket

| Bucket | n | won | roi | hit_rate |
|---|---|---|---|---|
| 10-15pp | 46 | 7 | -0.11956521739130435 | 0.15217391304347827 |
| 15pp+ | 10 | 1 | -0.2 | 0.1 |
| 6-10pp | 89 | 17 | -0.1641573033707865 | 0.19101123595505617 |
| 4-6pp | 79 | 15 | -0.25 | 0.189873417721519 |

## Breakdown by tier

| Tier | n | won | roi | hit_rate |
|---|---|---|---|---|
| STRONG_VALUE_DRAW | 91 | 13 | -0.20153846153846158 | 0.14285714285714285 |
| VALUE_DRAW_CANDIDATE | 133 | 27 | -0.17684210526315788 | 0.20300751879699247 |

## Verdict

⚠️  **REQUIERE MÁS DATOS** — CI cruza 0, no significativo.
