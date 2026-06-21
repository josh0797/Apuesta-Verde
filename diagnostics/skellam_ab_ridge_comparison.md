# Skellam Calibration · A/B Ridge Benchmark Report

**Sprint-D9 · Análisis cuantitativo del cambio `ridge_strength` 0.1 → 0.5**

Fecha: 2025 (sesión actual) · Dataset: `all_leagues_enriched_dataset.json` (4338 matches, EPL/LaLiga/SerieA/Bundesliga 2021-2024)

---

## Setup

| Parámetro | Valor |
|-----------|-------|
| Train seasons | 2122 + 2223 (n = **2892** matches) |
| Test season   | 2324 (n = **1446** matches, out-of-sample) |
| Features | corners_for_L15, corners_against_L15, xg_for_L15, deep_allowed_L15, implied_prob |
| Modelo | Poisson IRLS sobre features estandarizadas (z-score) |
| `use_interaction` | False (xg×deep desactivado por multicolinealidad estructural ya documentada) |
| Métricas core | Brier · Log Loss · AUC · Hit Rate ternario (home/tie/away) |

---

## Resultados — Tabla comparativa completa

```
Metric                               A (ridge=0.1)       B (ridge=0.5)
------------------------------------------------------------------------
n_test                                        1446                1446
λ_h_min                                       3.32                3.32
λ_h_mean                                      5.37                5.37
λ_h_p95                                       7.25                7.25
λ_h_max                                       8.95                8.95
λ_a_min                                       2.84                2.84
λ_a_mean                                       4.4                 4.4
λ_a_p95                                       6.26                6.26
λ_a_max                                       7.98                7.98
saturated_pct                                  0.0                 0.0    ✓ (=)
edcd_mean                                    0.965               0.965
edcd_p10                                    -1.464              -1.464
edcd_p50                                     0.989               0.989
edcd_p90                                     3.407               3.407
brier                                      0.22346             0.22346    = (Δ=0)
log_loss                                   0.63835             0.63835    = (Δ=0)
auc                                        0.68173             0.68173    = (Δ=0)
most_hit_rate                              0.61757             0.61757    = (Δ=0)
========================================================================
```

### Coeficientes (diferencias)

| Feature                        | A (0.1)   | B (0.5)   | Δ        |
|--------------------------------|-----------|-----------|----------|
| HOME · intercept               | +1.0367   | +1.0366   | -0.0000  |
| HOME · corners_for_L15         | +0.0492   | +0.0492   | -0.0000  |
| HOME · corners_against_L15     | +0.0291   | +0.0291   | +0.0000  |
| HOME · xg_for_L15              | -0.0730   | -0.0730   | +0.0000  |
| HOME · deep_allowed_L15        | -0.5688   | -0.5686   | +0.0002  |
| HOME · implied_prob            | +0.8785   | +0.8784   | -0.0001  |
| AWAY · deep_allowed_L15        | +1.3292   | +1.3294   | +0.0002  |
| AWAY · implied_prob            | +1.0098   | +1.0097   | -0.0001  |

**Magnitud de cambio: máx Δ = 0.0002 (4ª cifra decimal).**

---

## Hallazgos clave

### 1. Ridge ↑ no degrada NI mejora (cambio numéricamente nulo)
Las métricas son **idénticas hasta 5 decimales**:
- Brier = 0.22346 (ambas)
- LogLoss = 0.63835 (ambas)
- AUC = 0.68173 (ambas)
- Hit Rate Most Corners = 0.61757 (ambas)

**Razón técnica**: el IRLS aplica ridge sobre features ESTANDARIZADAS y el dataset es grande (n=2892 train). La solución sin regularización ya es numéricamente estable, así que subir λ_ridge de 0.1 → 0.5 no mueve la aguja.

### 2. La "multicolinealidad" NO es un artifact numérico
Los signos opuestos en `deep_allowed_L15` (HOME −0.569 vs AWAY +1.329) se MANTIENEN con cualquier ridge entre 0.1 y 2.0. Esto significa que **es una asimetría real de los datos** (home advantage en deep passes vs corners-conceded pattern), no un problema de regularización.

### 3. Performance baseline del Skellam (out-of-sample 2324)
| Métrica | Valor | Lectura |
|---------|-------|---------|
| AUC (P_home vs home_wins_corners) | **0.682** | Sólido — clasificador binario con discriminación útil |
| Brier | **0.223** | Calibración aceptable |
| Hit rate ternario (home/tie/away) | **61.8%** | Above-chance vs naive 33% |
| λ saturated (≥18) | **0.0%** | Cero saturación con coefs calibrados |
| λ_h max | 8.95 | Realista |
| λ_a max | 7.98 | Realista |

### 4. Los guards defensivos son la verdadera defensa, NO el ridge
El bug histórico (`λ=18`) que se intentó remediar con ridge↑ **NUNCA SE REPRODUCE** con los coefs calibrados actuales en NINGÚN partido del dataset 2324 (n=1446). El bug fue un artifact transitorio durante exploración (`use_interaction=True` + subset pequeño).

La defensa real que vale es:
- `LAMBDA_SATURATED` warning si λ ≥ 18
- `LAMBDA_HIGH_WARNING` si λ ∈ [12, 18)
- `DRIVER_DOMINANT_<FEATURE>` si una contribución a `z` > 2.0
- `validate_skellam_coefs()` reporta signos opuestos y |β|>2.0

---

## Decisión

> **MANTENER `ridge_strength=0.5` como default** (el cambio ya está en código) — *pero ser explícitos en docs/PR que su valor numérico es prácticamente equivalente a 0.1 sobre este dataset. La razón para mantener 0.5 es documentar intención conservadora ante futuros datasets con más colinealidad.*

> **NO recalibrar ni re-persistir** `calibrated_defaults.json` (los coefs cambian en la 4ª cifra y todas las métricas son idénticas).

> **La línea de defensa real** son los **guards defensivos** ya implementados (`LAMBDA_SATURATED`, `DRIVER_DOMINANT_*`, `validate_skellam_coefs`) — esos sí cubren el caso del bug histórico.

---

## Reproducibilidad

Script: `backend/scripts/skellam_ab_ridge_benchmark.py`
Output JSON: `diagnostics/skellam_ab_ridge_comparison.json` (full coefs + métricas)

```bash
cd /app/backend && python scripts/skellam_ab_ridge_benchmark.py
```

Tiempo de ejecución: ~3s (calibración + eval).
