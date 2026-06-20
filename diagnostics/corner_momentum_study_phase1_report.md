# Corner Momentum Study — Fase 1 (Opción B)

_Generado: 2026-06-20T22:06:13.478656Z_

## 1. Resumen ejecutivo

- Partidos totales analizados: **4338**
- Ligas: **Bundesliga, EPL, LaLiga, SerieA** (12 temporadas en total, 2021/22 → 2023/24)
- Liga MX: **excluida**. `football-data.co.uk` ofrece el archivo `new/MEX.csv` (4655 partidos) pero **no contiene columnas HC/AC** ni stats de tiros; archivado en `extra_no_corners/`.
- Umbral de descarte acordado: **|r| < 0.15**
- Features supervivientes (|r| ≥ 0.15, global): **0** de 46 candidatas.

### ⚠️ Veredicto principal

**Ninguna feature prematch L5/L15 supera el umbral acordado de |r| ≥ 0.15.**

La señal con mayor correlación absoluta es `away_corners_against_atAway_L15` con **|r|=0.0976** (~9% de correlación), claramente por debajo del corte.

**Interpretación pragmática:** los córners individuales por partido tienen una varianza inherente muy alta (típicamente 10±5). Las features prematch basadas en historial reciente capturan poco más del 1% de la varianza total. Esto no significa que los córners sean impredecibles en agregado (la **suma** y la **media** sí son estables), sino que **predecir el total exacto de un partido a partir de features prematch lineales es estadísticamente débil**.

**Implicaciones operativas para Fase 2:**

- Un motor de córners construido sobre estas features tendrá un **techo de R² muy bajo** (ver §4 abajo) — probablemente no superará al modelo poissoniano que usa solo la media histórica del par de equipos.
- Antes de invertir en arquitectura (Bivariate Poisson / Negative Binomial), conviene **revisar el listado** y decidir si:
  1. Relajar el umbral a `|r| ≥ 0.10` y aceptar señales débiles pero estadísticamente significativas (con n>1000 incluso r=0.10 es significativo).
  2. Buscar fuentes con stats más granulares (xG, posesión, ataques peligrosos) que `football-data.co.uk` no expone.
  3. Abandonar el enfoque feature-based y modelar córners con **goles esperados (xG)** como input dominante (no disponible aquí gratis).

## 2. Top 15 features por |r| (global)

| # | Feature | n | r | Walk-fwd MAE | Baseline MAE | Δ MAE | RMSE | Decisión |
|---|---------|---|---|--------------|--------------|-------|------|----------|
| 1 | `away_corners_against_atAway_L15` | 4243 | 0.0976 | 2.679 | 2.695 | 0.016 | 3.362 | ❌ drop |
| 2 | `away_corners_against_L15` | 4291 | 0.0911 | 2.679 | 2.691 | 0.012 | 3.361 | ❌ drop |
| 3 | `away_corners_against_atAway_L5` | 4243 | 0.0847 | 2.684 | 2.695 | 0.010 | 3.366 | ❌ drop |
| 4 | `away_corners_against_L5` | 4291 | 0.0846 | 2.682 | 2.691 | 0.009 | 3.362 | ❌ drop |
| 5 | `away_shots_against_L5` | 4291 | 0.0720 | 2.686 | 2.691 | 0.006 | 3.368 | ❌ drop |
| 6 | `away_shots_against_L15` | 4291 | 0.0712 | 2.684 | 2.691 | 0.007 | 3.367 | ❌ drop |
| 7 | `fav_implied_prob` | 4337 | 0.0601 | 2.685 | 2.691 | 0.006 | 3.371 | ❌ drop |
| 8 | `home_corners_for_L15` | 4290 | 0.0579 | 2.685 | 2.692 | 0.007 | 3.368 | ❌ drop |
| 9 | `home_shots_for_L5` | 4290 | 0.0544 | 2.687 | 2.692 | 0.005 | 3.369 | ❌ drop |
| 10 | `abs_implied_prob_diff` | 4337 | 0.0533 | 2.686 | 2.691 | 0.005 | 3.372 | ❌ drop |
| 11 | `home_corners_for_atHome_L15` | 4243 | 0.0530 | 2.688 | 2.693 | 0.005 | 3.372 | ❌ drop |
| 12 | `home_pressure_proxy_L5` | 4290 | 0.0518 | 2.687 | 2.692 | 0.005 | 3.370 | ❌ drop |
| 13 | `sum_corners_for_L15` | 4282 | 0.0477 | 2.689 | 2.692 | 0.003 | 3.373 | ❌ drop |
| 14 | `home_corners_for_atHome_L5` | 4243 | 0.0472 | 2.690 | 2.693 | 0.004 | 3.373 | ❌ drop |
| 15 | `home_corners_against_L15` | 4290 | 0.0469 | 2.690 | 2.692 | 0.002 | 3.371 | ❌ drop |

**Lectura:**

- `r` es la correlación de Pearson entre la feature (PIT, solo historia previa) y el `total_corners` del partido objetivo.
- `Walk-fwd MAE/RMSE` proviene de 5 folds temporales con regresión lineal **univariate** (una feature a la vez).
- `Baseline MAE` predice siempre con la media del bloque de entrenamiento. **Δ MAE > 0** ⇒ la feature mejora respecto al baseline naïf.
- La decisión `keep` se basa **únicamente** en `|r| ≥ 0.15` (umbral acordado).

## 3. Consistencia por liga (solo supervivientes globales)

| Feature | r global | EPL r | Bundesliga r | LaLiga r | SerieA r | Ligas pasan umbral |
|---------|----------|-------|--------------|----------|----------|---------------------|

## 4. OLS multivariada (importancia relativa entre supervivientes)

_No hubo supervivientes; ajustamos la OLS con las **top-10 features por |r|** para estimar el techo conjunto realista._

- Muestra (n con todas las features no nulas): **4233**
- R² de la OLS estandarizada: **0.0210**

**Coeficientes estandarizados (magnitud comparable):**

| Feature | β estandarizado | |β| |
|---------|-----------------|-----|
| `fav_implied_prob` | 0.8597 | 0.8597 |
| `abs_implied_prob_diff` | -0.8195 | 0.8195 |
| `away_corners_against_atAway_L15` | 0.0554 | 0.0554 |
| `away_corners_against_L15` | 0.0551 | 0.0551 |
| `away_shots_against_L15` | -0.0330 | 0.0330 |
| `away_shots_against_L5` | 0.0268 | 0.0268 |
| `away_corners_against_L5` | 0.0242 | 0.0242 |
| `home_corners_for_L15` | 0.0169 | 0.0169 |
| `away_corners_against_atAway_L5` | -0.0159 | 0.0159 |
| `home_shots_for_L5` | 0.0099 | 0.0099 |

## 5. Hallazgos clave (interpretación)

- **Ninguna feature** supera el umbral acordado de |r| ≥ 0.15.

- **L5 vs L15** (¿cuál ventana es más informativa?):

| Par | r L5 | r L15 | Ganador |
|-----|------|-------|---------|
| `home_corners_for_L5` vs `home_corners_for_L15` | 0.0398 | 0.0579 | L15 |
| `home_corners_against_L5` vs `home_corners_against_L15` | 0.0338 | 0.0469 | L15 |
| `away_corners_for_L5` vs `away_corners_for_L15` | 0.0021 | 0.0126 | L15 |
| `sum_corners_for_L5` vs `sum_corners_for_L15` | 0.0283 | 0.0477 | L15 |
| `match_corner_momentum_L5` vs `match_corner_momentum_L15` | -0.0348 | -0.0311 | L5 |

- **Home/Away split**: comparamos la versión global de la feature contra la versión filtrada por venue (atHome / atAway):

| Par | r global | r venue | Ganador |
|-----|----------|---------|---------|
| `home_corners_for_L15` vs `home_corners_for_atHome_L15` | 0.0579 | 0.0530 | global |
| `home_corners_against_L15` vs `home_corners_against_atHome_L15` | 0.0469 | 0.0203 | global |
| `away_corners_for_L15` vs `away_corners_for_atAway_L15` | 0.0126 | -0.0000 | global |
| `away_corners_against_L15` vs `away_corners_against_atAway_L15` | 0.0911 | 0.0976 | venue |

- **Favorito dominante**: 
  - `fav_implied_prob` r = 0.0601
  - `abs_implied_prob_diff` r = 0.0533

- **Serie activa** (rachas over 9.5 en últimos 5):
  - home r = 0.0285 | away r = 0.0360

## 6. Decisiones operativas

- **Features supervivientes (0)** para considerar en el diseño del motor (Fase 2):


- **Features descartadas** (no pasan |r| ≥ 0.15):

  - `home_corners_for_L5` (r=0.0398).
  - `home_corners_for_L15` (r=0.0579).
  - `home_corners_against_L5` (r=0.0338).
  - `home_corners_against_L15` (r=0.0469).
  - `away_corners_for_L5` (r=0.0021).
  - `away_corners_for_L15` (r=0.0126).
  - `away_corners_against_L5` (r=0.0846).
  - `away_corners_against_L15` (r=0.0911).
  - `home_corners_for_delta_L5_L15` (r=-0.0062).
  - `home_corners_against_delta_L5_L15` (r=-0.0021).
  - `away_corners_for_delta_L5_L15` (r=-0.0114).
  - `away_corners_against_delta_L5_L15` (r=0.0234).
  - `home_corners_for_atHome_L5` (r=0.0472).
  - `home_corners_for_atHome_L15` (r=0.0530).
  - `home_corners_against_atHome_L5` (r=0.0074).
  - `home_corners_against_atHome_L15` (r=0.0203).
  - `away_corners_for_atAway_L5` (r=-0.0017).
  - `away_corners_for_atAway_L15` (r=-0.0000).
  - `away_corners_against_atAway_L5` (r=0.0847).
  - `away_corners_against_atAway_L15` (r=0.0976).
  - `home_shots_for_L5` (r=0.0544).
  - `home_shots_for_L15` (r=0.0440).
  - `home_sot_for_L5` (r=0.0428).
  - `home_sot_for_L15` (r=0.0298).
  - `away_shots_for_L5` (r=0.0116).
  - `away_shots_for_L15` (r=0.0056).
  - `away_sot_for_L5` (r=0.0173).
  - `away_sot_for_L15` (r=0.0091).
  - `home_pressure_proxy_L5` (r=0.0518).
  - `home_pressure_proxy_L15` (r=0.0388).
  - `away_pressure_proxy_L5` (r=0.0153).
  - `away_pressure_proxy_L15` (r=0.0076).
  - `home_shots_against_L5` (r=0.0267).
  - `home_shots_against_L15` (r=0.0350).
  - `away_shots_against_L5` (r=0.0720).
  - `away_shots_against_L15` (r=0.0712).
  - `home_active_over_9_5_streak` (r=0.0285).
  - `away_active_over_9_5_streak` (r=0.0360).
  - `home_active_over_10_5_streak` (r=0.0177).
  - `away_active_over_10_5_streak` (r=0.0355).
  - `fav_implied_prob` (r=0.0601).
  - `abs_implied_prob_diff` (r=0.0533).
  - `match_corner_momentum_L5` (r=-0.0348).
  - `match_corner_momentum_L15` (r=-0.0311).
  - `sum_corners_for_L5` (r=0.0283).
  - `sum_corners_for_L15` (r=0.0477).

## 7. Limitaciones honestas

- **Modelo univariate** para walk-forward: no captura interacciones. El R² multivariado al final cuantifica el techo conjunto entre supervivientes.
- **No usamos sklearn/scipy** (decisión deliberada para no contaminar `requirements.txt`); todas las métricas son numpy puro.
- **Liga MX**: excluida por ausencia de columnas HC/AC en `football-data.co.uk` (extra leagues solo tienen goles + odds). Para incluirla habría que recurrir a otra fuente (API-Sports, scraping con créditos), lo que no se hizo para conservar créditos.
- El umbral `|r| ≥ 0.15` es **estricto** (acordado contigo). Algunas features con r en torno a 0.10-0.14 podrían tener señal interactiva — el motor en Fase 2 podría revisitar este corte con un test multivariado dedicado.
- **Sin tunear hiperparámetros**: walk-forward usa 5 folds y un OLS univariate puro. No es producción — es diagnóstico.

## 8. Siguientes pasos sugeridos (NO ejecutados aún)

- Fase 2: diseño del motor (Bivariate Poisson vs Negative Binomial) usando las features supervivientes como inputs.
- Análisis adicional opcional: hyperparameter-free interacciones (p.ej. `home_corners_for * away_corners_against`) para verificar señal multiplicativa.
- Validación cruzada estratificada por liga (no solo temporal).
