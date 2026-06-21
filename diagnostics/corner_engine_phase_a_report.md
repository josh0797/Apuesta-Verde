# Corner Engine — Fase A (Sprint Corner-1 + Corner-2)

_Generado: 2026-06-21T02:42:00.987025Z_

## 1. Resumen ejecutivo

- Dataset: **4338** partidos enriquecidos (4 ligas europeas × 3 temporadas).
- Walk-forward: Fold 1 (train 2021/22 → test 2022/23) + Fold 2 (train 2021/22+2022/23 → test 2023/24).
- Total predicciones evaluadas: **2892**.
- **Cuotas reales (Asian Corners) disponibles**: `False`. ⚠️ REAL_ODDS_NOT_AVAILABLE

### Métricas globales (acumulado walk-forward)

- **Brier Score** (3-way home/away/tie): **0.5074**
- **Log Loss**: **0.8480**
- **Hit rate (entre decided, ignorando ties)**: **0.6577** (2647 casos)
- **Bet hit rate** (solo cuando el motor recomendó BET): **0.7126** (925/1298)

## 2. Calibración del modelo (por fold)

### Fold 1 — train=['2122'] → test=['2223']

- n test: **1446**; n decided: **1315**
- Brier: **0.5184** | LogLoss: **0.8654** | Hit rate decided: **0.6456**

**β del corner_diff_model (OLS sobre `home_corners - away_corners`):**

| Coef | Valor |
|------|-------|
| `intercept` | 0.3041 |
| `implied_prob_diff` | 3.6902 |
| `corners_for_diff_L15` | 0.0446 |
| `corners_against_diff_L15` | 0.0766 |
| `deep_allowed_diff_L15` | 0.0402 |
| `dominant_favorite_signal` | 0.8708 |
| `venue_corner_split_diff` | 0.0720 |

**Sigmoid del corner_most_model:** a = 0.4525, b = -0.0070

**Tie buckets calibrados (frecuencia empírica de empates por |edcd|):**

| max abs(edcd) | P(tie) |
|---------------|--------|
| 0.5 | 0.1136 |
| 1.5 | 0.1228 |
| 2.5 | 0.0733 |
| 3.5 | 0.0976 |
| 5.5 | 0.0597 |
| 99.0 | 0.0550 |

### Fold 2 — train=['2122', '2223'] → test=['2324']

- n test: **1446**; n decided: **1332**
- Brier: **0.4964** | LogLoss: **0.8307** | Hit rate decided: **0.6697**

**β del corner_diff_model (OLS sobre `home_corners - away_corners`):**

| Coef | Valor |
|------|-------|
| `intercept` | 0.3027 |
| `implied_prob_diff` | 3.5942 |
| `corners_for_diff_L15` | 0.1002 |
| `corners_against_diff_L15` | 0.0874 |
| `deep_allowed_diff_L15` | 0.0129 |
| `dominant_favorite_signal` | 0.6757 |
| `venue_corner_split_diff` | 0.1876 |

**Sigmoid del corner_most_model:** a = 0.4398, b = -0.0193

**Tie buckets calibrados (frecuencia empírica de empates por |edcd|):**

| max abs(edcd) | P(tie) |
|---------------|--------|
| 0.5 | 0.0947 |
| 1.5 | 0.1156 |
| 2.5 | 0.0912 |
| 3.5 | 0.0793 |
| 5.5 | 0.0598 |
| 99.0 | 0.0550 |

## 3. Métricas por liga

| Liga | n | Brier | LogLoss | Hit rate decided | Bet hit rate |
|------|---|-------|---------|------------------|--------------|
| Bundesliga | 612 | 0.5140 | 0.8628 | 0.6697 | 0.7096 |
| EPL | 760 | 0.4838 | 0.8127 | 0.6752 | 0.7533 |
| LaLiga | 760 | 0.5212 | 0.8656 | 0.6436 | 0.6757 |
| SerieA | 760 | 0.5119 | 0.8537 | 0.6447 | 0.7057 |

## 4. Calibración (probabilidad predicha vs realizada, P(home_most))

| Bin | n | Predicted P | Observed P | Gap |
|-----|---|-------------|------------|-----|
| 0.0-0.1 | 0 | — | — | — |
| 0.1-0.2 | 79 | 0.1702 | 0.1646 | 0.0056 |
| 0.2-0.3 | 109 | 0.2608 | 0.2752 | -0.0144 |
| 0.3-0.4 | 382 | 0.3461 | 0.4031 | -0.0570 |
| 0.4-0.5 | 657 | 0.4564 | 0.4673 | -0.0109 |
| 0.5-0.6 | 594 | 0.5407 | 0.5842 | -0.0435 |
| 0.6-0.7 | 605 | 0.6467 | 0.6661 | -0.0194 |
| 0.7-0.8 | 257 | 0.7610 | 0.7471 | 0.0139 |
| 0.8-0.9 | 209 | 0.8234 | 0.8565 | -0.0331 |
| 0.9-1.0 | 0 | — | — | — |

## 5. Asian Corner markets — Probabilidades vs realización

| Market | n | Win rate observado | Prob predicha media | Gap |
|--------|---|--------------------|---------------------|-----|
| `AWAY_CORNERS_-0.5` | 2892 | 0.3534 | 0.3681 | 0.0147 |
| `AWAY_CORNERS_-1.0` | 2892 | 0.2725 | 0.2880 | 0.0155 |
| `AWAY_CORNERS_-1.5` | 2892 | 0.2725 | 0.2880 | 0.0155 |
| `AWAY_CORNERS_-2.0` | 2892 | 0.1995 | 0.2112 | 0.0117 |
| `AWAY_CORNERS_-2.5` | 2892 | 0.1995 | 0.2112 | 0.0117 |
| `AWAY_CORNERS_-3.0` | 2892 | 0.1397 | 0.1487 | 0.0090 |
| `AWAY_CORNERS_-3.5` | 2892 | 0.1397 | 0.1487 | 0.0090 |
| `HOME_CORNERS_-0.5` | 2892 | 0.5619 | 0.5340 | -0.0279 |
| `HOME_CORNERS_-1.0` | 2892 | 0.4672 | 0.4365 | -0.0306 |
| `HOME_CORNERS_-1.5` | 2892 | 0.4672 | 0.4365 | -0.0306 |
| `HOME_CORNERS_-2.0` | 2892 | 0.3766 | 0.3512 | -0.0254 |
| `HOME_CORNERS_-2.5` | 2892 | 0.3766 | 0.3512 | -0.0254 |
| `HOME_CORNERS_-3.0` | 2892 | 0.2998 | 0.2707 | -0.0290 |
| `HOME_CORNERS_-3.5` | 2892 | 0.2998 | 0.2707 | -0.0290 |

## 6. Hallazgos clave

- **Brier score global**: 0.5074. Para referencia, el baseline trivial (home_prob=0.45, away=0.40, tie=0.15) tiene Brier ≈ 0.60-0.65. Un modelo informativo debería estar por debajo de 0.60.
- ✅ **Hit rate decided = 65.77%** supera al baseline 50/50 (excluyendo ties).
- 📈 **Cuando el motor recomendó BET (1298 casos), acertó 71.26%**. Comparar con el threshold de confidence ≥ 55 y prob ≥ 0.58.

## 7. Limitaciones honestas

- **REAL_ODDS_NOT_AVAILABLE**: este backtest es PROBABILÍSTICO puro. No afirma ROI. El siguiente paso (no incluido en Fase A) es backtest con cuotas reales del endpoint histórico de TheOddsAPI (~60 créditos por evento; muestra recomendada: 100-150 eventos con DOMINANT_FAVORITE).
- Liga MX sigue ausente (sin xG ni córners disponibles en fuentes gratuitas).
- Los β del modelo son lineales sin interacciones; un modelo logístico multivariado o un boosting podría dar un salto adicional pequeño.
- El modelo Most Corners reduce el techo del problema (3-way → binario con tie) y aprovecha el hallazgo DOMINANT_FAVORITE. Su valor depende de la cobertura de cuotas reales en producción.

## 8. Próximos pasos sugeridos

1. **Fase B** — Integrar al endpoint /api/football/picks con feature flags `ENABLE_CORNER_MOST_MODEL` y `ENABLE_ASIAN_CORNERS_MODEL`. UI cards.
2. **Backtest con cuotas reales**: ~100-150 partidos seleccionados (DOMINANT_FAVORITE detectado), endpoint histórico de TheOddsAPI, mercados `alternate_spreads_corners`. ROI real sobre Asian Corners.
3. **Refinamientos opcionales**: Skellam si calibramos lambdas Poisson por equipo, interacciones xG × deep_allowed.
