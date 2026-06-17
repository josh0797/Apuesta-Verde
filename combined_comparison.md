# Sprint-D5 · Combined Comparison · Domestic vs National Tournaments

Generated: 2026-06-17T15:20:00.910580Z

> **Question:** Does Draw Potential perform better in national
> tournaments than in domestic leagues? Is there a repeatable
> 'Spain vs Cape Verde' archetype?

## Domestic vs National headline

| Setting | N picks | hit_rate | base_rate | Brier (combined) |
|---|---:|---:|---:|---:|
| Domestic (5 leagues, opening odds) | 397 | 0.1990 | ~0.24 (typical) | n/a (with odds) |
| National (3 tournaments, no_market) | 63 | 0.3016 | 0.2741 | 0.2108 |

## Cohort comparison (Domestic vs National)

| Cohorte | n_total | n_domestic | hit_rate_dom | n_national | hit_rate_nat |
|---|---:|---:|---:|---:|---:|
| DOMINANT_FAVORITE_DRAW_VALUE | 41 | 41 | 0.1951 | 0 | - |
| TOURNAMENT_GROUP_STAGE_DRAW_VALUE | 48 | 0 | - | 48 | 0.3333 |
| LOW_GOAL_UNDERDOG_BLOCK | 33 | 14 | 0.2857 | 19 | 0.4211 |
| TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS | 72 | 72 | 0.1806 | 0 | - |

## Patrón arquetipo «España vs Cabo Verde»

- Cohorte `DOMINANT_FAVORITE_DRAW_VALUE` total: **n=41**, won=8
- Hit-rate global del arquetipo: **0.1951**

> 🟡 El arquetipo NO supera consistentemente la base rate del 24%.

**DOMINANT_FAVORITE_DRAW_VALUE** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2025-01-19 | Premier League 202 | Nott'm Forest vs Southampton | 0.300 | 0.211 | 8.9 | 4.75 | False | 3-2 |
| 2025-01-25 | Premier League 202 | Liverpool vs Ipswich | 0.300 | 0.083 | 21.7 | 12.00 | False | 4-1 |
| 2025-01-25 | Premier League 202 | Southampton vs Newcastle | 0.300 | 0.190 | 11.0 | 5.25 | False | 1-3 |
| 2025-02-15 | Premier League 202 | Leicester vs Arsenal | 0.283 | 0.200 | 8.3 | 5.00 | False | 0-2 |
| 2025-02-16 | Premier League 202 | Liverpool vs Wolves | 0.283 | 0.125 | 15.8 | 8.00 | False | 2-1 |

**TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS** — top 5 ejemplos:

| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |
|---|---|---|---|---|---|---|---|---|
| 2024-09-14 | Premier League 202 | Man City vs Brentford | 0.290 | 0.125 | 16.5 | 8.00 | False | 2-1 |
| 2024-09-21 | Premier League 202 | Liverpool vs Bournemouth | 0.318 | 0.160 | 15.8 | 6.25 | False | 3-0 |
| 2024-09-28 | Premier League 202 | Arsenal vs Leicester | 0.313 | 0.125 | 18.8 | 8.00 | False | 4-2 |
| 2024-09-28 | Premier League 202 | Wolves vs Liverpool | 0.600 | 0.174 | 42.6 | 5.75 | False | 1-2 |
| 2024-09-29 | Premier League 202 | Ipswich vs Aston Villa | 0.600 | 0.267 | 33.3 | 3.75 | True | 2-2 |

## Veredicto final D5

- ✅ **Hit-rate global**: national `0.3016` vs domestic `0.1990` (≥5pp ventaja)
- ✅ **LOW_GOAL_UNDERDOG_BLOCK**: national `0.4211` vs domestic `0.2857` (≥5pp ventaja)
- ✅ **TOURNAMENT_GROUP_STAGE_DRAW_VALUE**: `0.3333` supera baseline ~24% en torneos (n=48)
- ⚠️ **DOMINANT_FAVORITE_DRAW_VALUE en ligas**: `0.1951` (n=41) NO supera el baseline; el arquetipo "España vs Cabo Verde" NO funciona en clubes europeos.
- ℹ️ `DOMINANT_FAVORITE_DRAW_VALUE` no aparece en torneos nacionales — los equipos clasificados tienen ELOs similares, así que la asimetría no se materializa con el threshold actual (Δ ELO ≥ 150).

> 🟢 **VEREDICTO: SÍ** — Draw Potential funciona mejor en torneos nacionales que en ligas domésticas en esta muestra. Múltiples cohortes confirman el efecto.

> ⚠️ `observe_only` mantenido. No se activan picks reales. El módulo permanece en modo observación.
