# Corner Momentum Study — Fase 1.5 (datos ricos vía Understat)

_Generado: 2026-06-20T23:23:27.952444Z_

## 1. Resumen ejecutivo

- Partidos enriquecidos: **4338** (de 4338 base, cobertura **99.91%** matching Understat).
- Ligas: **Bundesliga, EPL, LaLiga, SerieA**, 3 temporadas (2021/22 → 2023/24).
- Features evaluadas: **58** (clásicas=20 + ricas=38).
- Umbral acordado: **|r| ≥ 0.15**.
- Features supervivientes globales: **0** (ricas=0, clásicas=0).

### Veredicto principal

❌ Tampoco con datos ricos hay features que crucen |r| ≥ 0.15. El techo del enfoque feature-based lineal es muy bajo para `total_corners`.

## 2. Top 20 features por |r| (combinado clásicas + ricas)

| # | Feature | Tipo | n | r | Walk-fwd MAE | Δ MAE vs baseline | Decisión |
|---|---------|------|---|---|--------------|---------------------|----------|
| 1 | `sum_deep_allowed_L15` | 🆕 rich | 4282 | 0.0925 | 2.682 | 0.011 | ❌ drop |
| 2 | `away_corners_against_L15` | classic | 4291 | 0.0911 | 2.679 | 0.012 | ❌ drop |
| 3 | `away_corners_against_L5` | classic | 4291 | 0.0846 | 2.682 | 0.009 | ❌ drop |
| 4 | `away_deep_allowed_L15` | 🆕 rich | 4291 | 0.0835 | 2.682 | 0.010 | ❌ drop |
| 5 | `away_deep_allowed_L5` | 🆕 rich | 4291 | 0.0792 | 2.684 | 0.007 | ❌ drop |
| 6 | `away_npxg_against_L15` | 🆕 rich | 4291 | 0.0655 | 2.686 | 0.006 | ❌ drop |
| 7 | `away_npxg_against_L5` | 🆕 rich | 4291 | 0.0606 | 2.687 | 0.004 | ❌ drop |
| 8 | `fav_implied_prob` | classic | 4337 | 0.0601 | 2.685 | 0.006 | ❌ drop |
| 9 | `away_xg_against_L15` | 🆕 rich | 4291 | 0.0580 | 2.687 | 0.004 | ❌ drop |
| 10 | `home_corners_for_L15` | classic | 4290 | 0.0579 | 2.685 | 0.007 | ❌ drop |
| 11 | `home_deep_allowed_L15` | 🆕 rich | 4290 | 0.0576 | 2.688 | 0.004 | ❌ drop |
| 12 | `home_shots_for_L5` | classic | 4290 | 0.0544 | 2.687 | 0.005 | ❌ drop |
| 13 | `abs_implied_prob_diff` | classic | 4337 | 0.0533 | 2.686 | 0.005 | ❌ drop |
| 14 | `away_xg_against_L5` | 🆕 rich | 4291 | 0.0505 | 2.689 | 0.002 | ❌ drop |
| 15 | `home_deep_for_L5` | 🆕 rich | 4290 | 0.0473 | 2.689 | 0.003 | ❌ drop |
| 16 | `home_npxg_against_L15` | 🆕 rich | 4290 | 0.0471 | 2.691 | 0.001 | ❌ drop |
| 17 | `home_corners_against_L15` | classic | 4290 | 0.0469 | 2.690 | 0.002 | ❌ drop |
| 18 | `home_xg_against_L15` | 🆕 rich | 4290 | 0.0460 | 2.691 | 0.001 | ❌ drop |
| 19 | `home_deep_for_L15` | 🆕 rich | 4290 | 0.0441 | 2.690 | 0.002 | ❌ drop |
| 20 | `home_shots_for_L15` | classic | 4290 | 0.0440 | 2.688 | 0.004 | ❌ drop |

## 3. Comparación: features clásicas vs ricas (top-5 de cada grupo)

**Top-5 clásicas (Fase 1):**

| Feature | r | Δ MAE | Decisión |
|---------|---|-------|----------|
| `away_corners_against_L15` | 0.0911 | 0.012 | ❌ |
| `away_corners_against_L5` | 0.0846 | 0.009 | ❌ |
| `fav_implied_prob` | 0.0601 | 0.006 | ❌ |
| `home_corners_for_L15` | 0.0579 | 0.007 | ❌ |
| `home_shots_for_L5` | 0.0544 | 0.005 | ❌ |

**Top-5 ricas (Fase 1.5, nuevas):**

| Feature | r | Δ MAE | Decisión |
|---------|---|-------|----------|
| `sum_deep_allowed_L15` | 0.0925 | 0.011 | ❌ |
| `away_deep_allowed_L15` | 0.0835 | 0.010 | ❌ |
| `away_deep_allowed_L5` | 0.0792 | 0.007 | ❌ |
| `away_npxg_against_L15` | 0.0655 | 0.006 | ❌ |
| `away_npxg_against_L5` | 0.0606 | 0.004 | ❌ |

## 4. OLS multivariada — top-10 features combinadas

- Muestra: **n=4281**
- **R² = 0.0211**

| Feature | β estandarizado | |β| |
|---------|-----------------|-----|
| `sum_deep_allowed_L15` | 0.1359 | 0.1359 |
| `away_xg_against_L15` | -0.0802 | 0.0802 |
| `away_deep_allowed_L15` | -0.0779 | 0.0779 |
| `home_corners_for_L15` | 0.0688 | 0.0688 |
| `away_npxg_against_L15` | 0.0545 | 0.0545 |
| `fav_implied_prob` | 0.0532 | 0.0532 |
| `away_corners_against_L15` | 0.0525 | 0.0525 |
| `away_corners_against_L5` | 0.0259 | 0.0259 |
| `away_deep_allowed_L5` | 0.0178 | 0.0178 |
| `away_npxg_against_L5` | 0.0101 | 0.0101 |

## 5. Validación DOMINANT_FAVORITE → Most Corners (revalidación)

> Replicamos el hallazgo del Sprint-D8 (n=90, t=9.68, p≈0) sobre el dataset ampliado.

- Partidos con `DOMINANT_FAVORITE` (implied_prob ≥ 0.65): **851**
- Partidos decididos (no empate en córners): **795**
- Favorito gana Most Corners: **665 / 795** = **83.65%**
- Z-score vs H0=0.5: **25.65**
- Diferencia promedio de córners (favorito - inferior): **3.82** (σ=4.28)

**Por liga:**

| Liga | n | n_fav_won | win_rate |
|------|---|-----------|----------|
| Bundesliga | 183 | 144 | 78.69% |
| EPL | 266 | 227 | 85.34% |
| LaLiga | 158 | 132 | 83.54% |
| SerieA | 188 | 162 | 86.17% |

**Por venue del favorito:**

| Side | n | n_fav_won | win_rate |
|------|---|-----------|----------|
| away | 166 | 133 | 80.12% |
| home | 629 | 532 | 84.58% |

## 6. Conclusiones operativas

- Las features ricas NO cruzan el umbral `|r| ≥ 0.15` para `total_corners`. **NO se recomienda construir un motor de Total Corners** sobre estas features con regresión lineal.
- Sin embargo, **la hipótesis DOMINANT_FAVORITE → Most Corners SIGUE confirmándose** en el dataset ampliado (ver §5). Esto es coherente con el pivote propuesto: **motor de mercado Most Corners (clasificación binaria), NO de total absoluto (regresión)**.

## 7. Limitaciones honestas

- **xG y npxG** son señales conocidas como predictoras de **goles**, no necesariamente de **córners**. La relación xG↔córners pasa por el estilo de juego, pero no es directa.
- Walk-forward usa OLS univariate; no captura interacciones (deep_for × deep_allowed, xG × ppda).
- Para predicción Most Corners necesitaríamos otro pipeline (clasificación binaria + AUC + Brier).
- Liga MX sigue ausente (no cubierta por Understat). Cobertura: solo top-4 europeas.
