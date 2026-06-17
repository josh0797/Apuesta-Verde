# Sprint-D5 · National Tournaments DRAW Backtest Summary

Generated: 2026-06-17T15:20:00.910494Z

> Mode: `observe_only` · DRAW · `no_market` (calibration-only).
> No odds → metrics focus on Brier / calibration / label hit-rate.

## Headline (per tournament)

| Tournament | N preds | N fired | hit_rate | base_rate | Brier | calibration |
|---|---:|---:|---:|---:|---:|:---|
| World Cup 2018 | 48 | 15 | 0.2000 | 0.2292 | 0.1808 | ACCEPTABLE_CALIBRATION |
| World Cup 2022 | 48 | 14 | 0.1429 | 0.2083 | 0.1750 | MISCALIBRATED |
| Euro 2024 | 39 | 34 | 0.4118 | 0.4103 | 0.2766 | MISCALIBRATED |

## Cohort breakdown (aggregate across 3 tournaments)

| Cohorte | n | won | hit_rate |
|---|---:|---:|---:|
| DOMINANT_FAVORITE_DRAW_VALUE | 0 | 0 | - |
| TOURNAMENT_GROUP_STAGE_DRAW_VALUE | 48 | 16 | 0.3333 |
| LOW_GOAL_UNDERDOG_BLOCK | 19 | 8 | 0.4211 |
| TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS | 0 | 0 | - |

_(sin ejemplos en `DOMINANT_FAVORITE_DRAW_VALUE`)_

**TOURNAMENT_GROUP_STAGE_DRAW_VALUE** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2018-06-20 | World Cup 2018 | Uruguay vs Saudi Arabia | 0.302 | - | - | - | False | 1-0 |
| 2018-06-21 | World Cup 2018 | Denmark vs Australia | 0.335 | - | - | - | True | 1-1 |
| 2018-06-21 | World Cup 2018 | Argentina vs Croatia | 0.304 | - | - | - | False | 0-3 |
| 2018-06-22 | World Cup 2018 | Nigeria vs Iceland | 0.304 | - | - | - | False | 2-0 |
| 2018-06-22 | World Cup 2018 | Brazil vs Costa Rica | 0.304 | - | - | - | False | 2-0 |

**LOW_GOAL_UNDERDOG_BLOCK** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2018-06-22 | World Cup 2018 | Brazil vs Costa Rica | 0.304 | - | - | - | False | 2-0 |
| 2018-06-22 | World Cup 2018 | Serbia vs Switzerland | 0.338 | - | - | - | False | 1-2 |
| 2018-06-23 | World Cup 2018 | South Korea vs Mexico | 0.302 | - | - | - | False | 1-2 |
| 2018-06-23 | World Cup 2018 | Germany vs Sweden | 0.302 | - | - | - | False | 2-1 |
| 2018-06-26 | World Cup 2018 | Australia vs Peru | 0.303 | - | - | - | False | 0-2 |

_(sin ejemplos en `TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS`)_

