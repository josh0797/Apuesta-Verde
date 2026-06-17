# Football DRAW Backtest Report

Generated: 2026-06-17T06:59:43.252024Z

## Configuration

- `market` = `DRAW`
- `min_edge_pp` = `8.0`
- `stake_mode` = `flat`
- `use_calibration` = `False`
- `walk_forward` = `True`

## Core results

- **n_matches_total**: `380`
- **n_bets**: `91`
- **n_won**: `13`
- **n_lost**: `78`
- **hit_rate**: `0.1429`
- **total_staked**: `91.0`
- **total_returned**: `72.66`
- **net_pnl**: `-18.34`
- **roi**: `-0.2015`
- **yield_per_bet**: `-0.2015`
- **max_drawdown**: `22.5`
- **sharpe_like**: `-0.96`
- **avg_edge_predicted_pp**: `11.385`
- **avg_edge_realised_pp**: `-3.564`
- **roi_ci_lo**: `-0.5889`
- **roi_ci_hi**: `0.2298`
- **is_significant**: `False`
- **calibration_label**: `MISCALIBRATED`
- **small_sample_flag**: `False`
- **small_sample_warning**: `None`

## Reliability curve (predicted vs actual)

| Bucket | n | predicted_avg | actual_avg |
|---|---|---|---|
| [0.00,0.10) | 0 | None | None |
| [0.10,0.20) | 3 | 0.1857 | 0.0 |
| [0.20,0.30) | 45 | 0.2758 | 0.1556 |
| [0.30,0.40) | 43 | 0.317 | 0.1395 |
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
| 6-10pp | 35 | 5 | -0.3097142857142857 | 0.14285714285714285 |

## Breakdown by tier

| Tier | n | won | roi | hit_rate |
|---|---|---|---|---|
| STRONG_VALUE_DRAW | 91 | 13 | -0.20153846153846158 | 0.14285714285714285 |

## Verdict

⚠️  **REQUIERE MÁS DATOS** — CI cruza 0, no significativo.
