# Sprint-D5 · Domestic Leagues DRAW Backtest Summary

Generated: 2026-06-17T15:20:00.910329Z

> Mode: `observe_only` · DRAW market · min_edge=4pp · walk-forward + calibration.
> Odds: opening (B365 / Pinnacle); closing warnings propagated.

## Headline (per league)

| League | N bets | hit_rate | ROI | CI 95% | sample_status | sig? |
|---|---:|---:|---:|:---|:---|:---:|
| Premier League 2024-25 | 120 | 0.2500 | 0.1811 | [-0.1967, 0.5713] | SMALL_SAMPLE_CAUTION | ❌ |
| La Liga 2024-25 | 10 | 0.0000 | -1.0000 | [-1.0000, -1.0000] | INSUFFICIENT_SAMPLE_DO_NOT_TRUST | ❌ |
| Serie A 2024-25 | 95 | 0.1368 | -0.3444 | [-0.6625, 0.0096] | SMALL_SAMPLE_CAUTION | ❌ |
| Bundesliga 2024-25 | 104 | 0.2308 | 0.0792 | [-0.2993, 0.5141] | SMALL_SAMPLE_CAUTION | ❌ |
| Ligue 1 2024-25 | 68 | 0.1765 | -0.2106 | [-0.6050, 0.2310] | SMALL_SAMPLE_CAUTION | ❌ |

## Aggregate (combined 5 leagues)

- **n_bets total**: 397
- **hits**: 79
- **hit_rate aggregate**: 0.1990
- **net_pnl aggregate**: -27.07
- **total_staked aggregate**: 397.00
- **ROI aggregate**: -0.0682

## Cohort breakdown (aggregate across 5 leagues)

| Cohorte | n | won | hit_rate |
|---|---:|---:|---:|
| DOMINANT_FAVORITE_DRAW_VALUE | 41 | 8 | 0.1951 |
| TOURNAMENT_GROUP_STAGE_DRAW_VALUE | 0 | 0 | - |
| LOW_GOAL_UNDERDOG_BLOCK | 14 | 4 | 0.2857 |
| TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS | 72 | 13 | 0.1806 |

**DOMINANT_FAVORITE_DRAW_VALUE** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2025-01-19 | Premier League 202 | Nott'm Forest vs Southampton | 0.300 | 0.211 | 8.9 | 4.75 | False | 3-2 |
| 2025-01-25 | Premier League 202 | Liverpool vs Ipswich | 0.300 | 0.083 | 21.7 | 12.00 | False | 4-1 |
| 2025-01-25 | Premier League 202 | Southampton vs Newcastle | 0.300 | 0.190 | 11.0 | 5.25 | False | 1-3 |
| 2025-02-15 | Premier League 202 | Leicester vs Arsenal | 0.283 | 0.200 | 8.3 | 5.00 | False | 0-2 |
| 2025-02-16 | Premier League 202 | Liverpool vs Wolves | 0.283 | 0.125 | 15.8 | 8.00 | False | 2-1 |

_(sin ejemplos en `TOURNAMENT_GROUP_STAGE_DRAW_VALUE`)_

**LOW_GOAL_UNDERDOG_BLOCK** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2024-12-26 | Premier League 202 | Man City vs Everton | 0.265 | 0.182 | 8.3 | 5.50 | True | 1-1 |
| 2025-01-14 | Premier League 202 | Chelsea vs Bournemouth | 0.265 | 0.211 | 5.4 | 4.75 | True | 2-2 |
| 2024-08-31 | La Liga 2024-25 | Barcelona vs Valladolid | 0.222 | 0.160 | 6.2 | 6.25 | False | 7-0 |
| 2024-09-13 | La Liga 2024-25 | Betis vs Leganes | 0.324 | 0.270 | 5.4 | 3.70 | False | 2-0 |
| 2024-09-22 | Serie A 2024-25 | Roma vs Udinese | 0.290 | 0.238 | 5.2 | 4.20 | False | 3-0 |

**TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2024-09-14 | Premier League 202 | Man City vs Brentford | 0.290 | 0.125 | 16.5 | 8.00 | False | 2-1 |
| 2024-09-21 | Premier League 202 | Liverpool vs Bournemouth | 0.318 | 0.160 | 15.8 | 6.25 | False | 3-0 |
| 2024-09-28 | Premier League 202 | Arsenal vs Leicester | 0.313 | 0.125 | 18.8 | 8.00 | False | 4-2 |
| 2024-09-28 | Premier League 202 | Wolves vs Liverpool | 0.600 | 0.174 | 42.6 | 5.75 | False | 1-2 |
| 2024-09-29 | Premier League 202 | Ipswich vs Aston Villa | 0.600 | 0.267 | 33.3 | 3.75 | True | 2-2 |

