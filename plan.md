# Plan — Phases F58–F97.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F97 completadas / en curso según bitácora.
> **Idioma operativo:** Español.

---

## 1) Objetivos

### Objetivos originales (F58)
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (shots/SOT/minutos)
  - FBref (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con override contextual.
- Añadir smoke tests y mantener suite global verde.
- (P2) UI wiring: panel independiente para Cross + Override + Player Props.

### Objetivos nuevos / extendidos (F69–F74)
- Editorial interno específico por partido (no genérico).
- Scrapers externos (Forebet / Sportytrader) como fallback.
- Normalización de identidad de mercado y reconciliación interno vs externo.
- Auditoría de predicciones externas contra fuerza del rival.
- Guardrails para **mercados UNKNOWN** (no edge, no trap, no discard).
- F74: **schema canónico** para enriquecimiento fútbol + probabilidades estimadas.
- F74-post: **adaptadores** para eliminar fragmentación de datos anidados.
- F74-post v2: **fallback de odds con TheStatsAPI** (incluye opening/last_seen → line movement sin snapshots históricos).
- F74-post v2.5: **line movement desde día 1** usando `opening` TheStatsAPI + `last_seen`.

### Objetivos nuevos / extendidos (F82)
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales.
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia.

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners desde datos presentes. **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual (cuota manual, selector de mercado, recalcular).
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular promedios xG no-penal (a favor / en contra) L1/L5/L15 por equipo usando shotmap TheStatsAPI.
- Arquitectura **background-first** con cache + timeouts.
- Señales contextuales (nunca pick-binding) + señales de cobertura/muestra parcial.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable (alinear tests con copy y flujos reales).

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - F84.a Team Stats ✅
  - F84.b H2H ✅
  - F84.e Odds + line movement ✅
- Flags + auditoría `_provenance_*`.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Scraping fail-soft y background-first con endpoints run-now/background/status.
- UI panel para disparo y render.
- Phase 2: resolver FBref search-page + fuzzy matching ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Definir cuándo H2H puede influir en scoring vs. cuándo es solo narrativo.
- Output: `h2h_context` enriquecido + `h2h_decision` (points_by_market + signals).

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football`
- Integrar H2H decision + xG recent averages (background) sin bloquear el camino crítico.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer
- Editorial output y UI consumen `h2h_decision` + `xg_recent_averages`.
- Scoring aplica bump H2H al mercado (clamp +8 + guards).

### Objetivos nuevos / extendidos (F89 / Sprint F86.1) — Calibración H2H rules + guards explícitas
- Recalibrar `H2H_POINT_RULES` contra baselines típicas (más robusto).
- Introducir `get_active_rules()` con override por env (JSON) leído en tiempo de llamada.
- Agregar polarity guard explícito (OVER/UNDER por línea + BTTS YES/NO) con auditoría.
- Agregar sample guard por regla (`min_sample`) + señal `LOW_SAMPLE_H2H_SIGNAL`.
- Agregar DNB overlap guard suave (HOME_DNB + AWAY_DNB no es hard-conflict).
- Agregar cap agregado de puntos H2H (`MAX_H2H_POINTS_TOTAL=8`).
- Mantener back-compat con consumers/editorial UI.

### Objetivos nuevos / extendidos (F90 / Sprint F83-update) — Corners cascade con diagnóstico estructurado (Scrape.do)
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (D13) — MLB Matchup Familiarity Overlay (secundario)
- Implementar una capa contextual MLB basada en enfrentamientos recientes (preferencia: últimos 15 días) que:
  - NO sea pick principal.
  - Sea puro/fail-soft, auditable.
  - **D13.1:** Totales (O/U) ✅
  - **D13.2:** extender a Moneyline + Runline y **permitir impacto en scoring real** con límites y veto ✅

### Objetivos nuevos / extendidos (NIVEL 3) — MLB Totals: Distribution Mixing + Tail Calibration + Threshold Models
- Agregar una capa compatible/auditable que NO reemplace el sistema actual, pero mejore:
  - probas O/U por umbral (7.5/8.5/…)
  - juegos de alta varianza (colas)
- **Bloques (estado actual):**
  - **Bloque 1 (Mixer):** mezcla Poisson/NB dinámica ✅
  - **Bloque 2 (§1-§4):** fórmula de pesos + tail calibration + threshold model + blender **(ACTIVO)** ✅
  - **Bloque 3 (§5-§6):** reglas hard de Under + UI “Distribución y colas” ✅

### Objetivos nuevos / extendidos (D9.2-C) — Residual Model con xG real (Bonferroni estricto)
- Fortalecer el backtest residual para evitar falsos positivos por múltiples comparaciones:
  - Clasificador puro y testeable ✅
  - Corrección Bonferroni estricta ✅
  - Auditoría explícita de umbral ajustado y resultados por métrica ✅

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivos nuevos / extendidos (F95) — Football Post-Match Settlement Hotfix (P0)
(…sin cambios; ver bitácora inferior.)

### Objetivos nuevos / extendidos (F96) — Football: Settler corners + TheSportsDB experimental + ingest fallback (P1)
(…sin cambios; ver bitácora inferior.)

### Objetivos nuevos / extendidos (F97) — NIVEL 3 Bloque 3 (§5-§6): Under hard rules + UI “Distribución y colas” (P1)
(…sin cambios; ver bitácora inferior.)

### Objetivo nuevo (Sprint Corner Momentum Study — Fase 1, Opción B) — **P0 (ACTUAL)**
**Meta:** obtener evidencia cuantitativa (sin heurísticas arbitrarias) sobre qué señales prematch explican/mejoran la predicción de córners.

**Decisión del usuario (confirmada):**
- Dataset: **3 temporadas** por liga.
- Ligas: **EPL + Bundesliga + La Liga + Serie A + Liga MX (exótica)**.
- Umbral de descarte: **|r| < 0.15 → descartar feature**.
- Fuentes: **football-data.co.uk (gratis)** + alternativa documentada para Liga MX.

**Salida requerida (métricas):**
- Correlación (Pearson), MAE, RMSE, Feature Importance.

---

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador
**(COMPLETADO)** — sin cambios.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**(COMPLETADO)** — sin cambios.

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**(COMPLETADO)** — sin cambios.

### Fase 4 — Integración en Football pipeline (override incluido)
**(COMPLETADO)** — sin cambios.

### Fase 5 — UI Wiring (P2)
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase SPRINT A — Draw Potential (piloto retrospectivo) (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT B — Football Learning Dataset + Loops + UI (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT D — Framework Backtest Histórico Point-in-Time (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT D2 — Backtest histórico en torneos nacionales (WC2022 + Euro2024) — COMPLETADO ✅
(Sin cambios.)

---

## Phase SPRINT D3 — Backtest National Tournaments: OVER 1.5 + DOUBLE CHANCE (calibration-only) — COMPLETADO ✅
(Sin cambios.)

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT D5 — Multi-league + multi-tournament DRAW + cohortes (observe_only) — EN PROGRESO 🟡 (P0)
(Sin cambios.)

---

## Phase SPRINT D6 — Probar que el Walk-Forward Calibrator NO es un no-op — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1 — Live Odds Monitor (Base) + persistencia `odds_snapshots` (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1-d — Hook automático (Scheduler) para Market Identity Resolver — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1-f — 365Scores “Tendencias Top” (reemplazo SportyTrader editorial) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

# Phase SPRINT D7 — Backtest comparativo DRAW (Ligas vs Selecciones) + Post-mortem & Remediación — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase SPRINT D7-E — Threshold parametrization + honest sweep + multi-season sanity check (DRAW) — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase SPRINT D7-F — OVER_2_5 / UNDER_2_5 con la misma disciplina (D7-E) — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡
(Sin cambios.)

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳
(Sin cambios.)

---

## Phase Sprint Corner-2 — Datos ricos (Understat) — **✅ COMPLETADA (P0)**

> **Alcance:** ingerir datos avanzados (xG, xGA, npxG, deep, PPDA, forecast) desde Understat y re-evaluar el techo del modelo. Pivote propuesto: validar DOMINANT_FAVORITE → Most Corners sobre el dataset ampliado.

### Resumen ejecutivo

- **Ingesta Understat**: 12/12 jobs OK (4 ligas × 3 temporadas), 4338 partidos con 100% de cobertura en xG/xGA/npxG/deep/PPDA/forecast.
- **Merge con dataset base**: 99.91% match rate (4334/4338) tras aplicar alias canónico de equipos (Man United, Dortmund, RB Leipzig, etc.).
- **Re-evaluación cuantitativa**: 0/58 features (clásicas + ricas) cruzan |r| ≥ 0.15 para `total_corners`. R² conjunto top-10 OLS = **0.0211** (Fase 1: 0.0210). **Los datos ricos NO mueven la aguja en regresión sobre total_corners.**
- **Top feature global**: `sum_deep_allowed_L15` con r=0.0925 (rich), apenas supera a las clásicas.

### Validación DOMINANT_FAVORITE → Most Corners (revalidación del Sprint D8)

| Métrica                       | Sprint D8 original | Sprint Corner-2 (ahora) |
|-------------------------------|--------------------|--------------------------|
| Tamaño de muestra             | 90                 | **851**                  |
| Win rate Most Corners         | 81.11%             | **83.65%**               |
| Estadística                   | t=9.68             | **z=25.65**              |
| Diff promedio de córners      | 4.63               | **3.82** (σ=4.28)        |
| Consistencia por liga         | n/d                | **78–86%** (EPL 85.34%, Serie A 86.17%, La Liga 83.54%, Bundesliga 78.69%) |
| Por venue del favorito        | n/d                | **home 84.58% / away 80.12%** |

**Hallazgo robustísimo y replicado**. Es la base del Sprint Corner-1 (motor Most Corners).

### Entregables

- `/app/backend/scripts/ingest_understat_corners.py` — ingestor (12 jobs, cache local en `/app/data/corners_history/understat_raw/`).
- `/app/data/corners_history/understat_matches_consolidated.json` — 4338 matches Understat.
- `/app/backend/scripts/merge_corners_with_understat.py` — merger con alias canónico (99.91% cobertura).
- `/app/data/corners_history/all_leagues_enriched_dataset.json` — dataset enriquecido final.
- `/app/backend/scripts/run_corner_momentum_study_phase15.py` — pipeline cuantitativo extendido.
- `/app/diagnostics/corner_momentum_study_phase15_stats.json` y `corner_momentum_study_phase15_report.md`.

### Restricciones cumplidas

- ✅ Cero cambios al código de producción.
- ✅ Cero APIs de pago (Understat es gratis, scraping de endpoint AJAX legítimo con 1s entre requests).
- ✅ Pytest backend completo: **4421 passed / 2 skipped / 0 failures**.

---


## Phase Corner Momentum Study — Fase 1 (Opción B) — **✅ COMPLETADA**


## Phase Sprint Corner-1 + Corner-2 · Fase A — Motor de córners (módulos puros + backtest) — **✅ COMPLETADA (P0)**

> **Alcance:** módulos algorítmicos puros (zero touch a producción) + backtest probabilístico walk-forward sobre 4338 partidos. **No incluye** integración API/UI (eso es Fase B). **No incluye** ROI financiero real (REAL_ODDS_NOT_AVAILABLE).

### Módulos creados

- `/app/backend/services/football/corners/corner_diff_model.py` — estima `expected_corner_diff` con 6 drivers, cap ±5.5, drivers explícitos + reason_codes.
- `/app/backend/services/football/corners/corner_most_model.py` — clasificador binario `predict_most_corners` con sigmoid calibrado + tie prob por bucket + reglas NO_BET (confidence < 55, prob < 0.58, data_quality LOW).
- `/app/backend/services/football/corners/corner_diff_distribution.py` — distribución empírica por buckets + `build_asian_corner_markets` para 14 líneas (Home/Away × 7 handicaps).
- `/app/backend/services/football/corners/corner_backtest.py` — walk-forward 21/22→22/23, 21/22+22/23→23/24. Calibración: OLS para β del corner_diff, MLE numpy puro para sigmoid (a, b), frecuencia empírica para tie buckets.

### Tests obligatorios — 11/11 pasando

- `/app/backend/tests/test_corner_engine_phase_a.py`
- Cubre los 8 escenarios del brief + extras: dominant fav home/away, sin favorito, missing data, no inputs, Asian -1.5 vs -3.5, líneas enteras con push, backtest sin cuotas, isolation de producción, caps ±5.5, suma probs = 1.

### Resultados del backtest (4338 partidos enriquecidos, walk-forward)

| Métrica | Global | Fold 1 (test 22/23) | Fold 2 (test 23/24) |
|---|---|---|---|
| n test | 2892 | 1446 | 1446 |
| n decided (sin tie) | 2647 | 1315 | 1332 |
| **Brier Score** | **0.5074** | 0.5184 | **0.4964** |
| **Log Loss** | **0.848** | 0.8654 | 0.8307 |
| **Hit rate decided** | **65.77%** | 64.56% | **66.97%** |
| **Bet hit rate** (recommended ≠ NO_BET) | **71.26%** (925/1298) | — | — |

**Por liga** (acumulado):

| Liga | n | Brier | Hit rate decided | Bet hit rate |
|---|---|---|---|---|
| EPL | 760 | 0.4838 | 67.52% | **75.33%** |
| Bundesliga | 612 | 0.5140 | 66.97% | 70.96% |
| Serie A | 760 | 0.5119 | 64.47% | 70.57% |
| La Liga | 760 | 0.5212 | 64.36% | 67.57% |

### Calibración

- **Gap entre probabilidad predicha y observada**: máximo ~3% en cualquier mercado Asian Corners (0.5074 Brier es 17%+ mejor que baseline naïf 0.60-0.65).
- **β calibrados son interpretables**: `implied_prob_diff` ≈ 3.6 (peso dominante), `dominant_favorite_signal` ≈ 0.7-0.9 (boost extra cuando hay DOM_FAV), L15 corners diff ≈ 0.05-0.1 (pequeño pero direccional).
- **Sigmoid**: a ≈ 0.44-0.45 — coherente con el hallazgo de Fase 1.5 (dominant fav diff +3.82 → P ≈ 0.84).

### Entregables

- `/app/backend/scripts/run_corner_engine_phase_a_backtest.py` — script de calibración + backtest.
- `/app/diagnostics/corner_engine_phase_a_stats.json` (stats raw).
- `/app/diagnostics/corner_engine_phase_a_report.md` (8 secciones, tablas comparativas, calibración).

### Restricciones cumplidas

- ✅ Cero cambios a código de producción.
- ✅ Cero APIs de pago (TheOddsAPI no consumido aún para Asian Corners; pendiente Fase B con muestra).
- ✅ Cero nuevas dependencias.
- ✅ Feature flags listos: `ENABLE_CORNER_MOST_MODEL`, `ENABLE_ASIAN_CORNERS_MODEL` (no encendidos aún).
- ✅ Pytest: **4432 passed / 2 skipped / 0 failures** (4421 originales + 11 nuevos).
- ✅ Point-in-time estricto en walk-forward.
- ✅ REAL_ODDS_NOT_AVAILABLE marcado en todos los outputs sin cuotas reales.

---

> **Alcance:** SOLO evidencia cuantitativa (no diseño de motor, no heurísticas, no integración). Fuentes gratis. Sin consumo de créditos.

### Resumen ejecutivo

- **Dataset construido**: 4338 partidos (3 temporadas × 4 ligas europeas: EPL, Bundesliga, La Liga, Serie A, periodos 2021/22 → 2023/24).
- **Liga MX**: excluida — `football-data.co.uk new/MEX.csv` (4655 partidos) no contiene columnas `HC/AC` ni stats de tiros. Archivada en `/app/data/football_data_co_uk/extra_no_corners/`.
- **Features evaluadas**: 46 candidatas cubriendo los 7 ejes pedidos por el brief (L5 vs L15, corners FOR/AGAINST, splits H/A, ofensivas, defensivas, serie activa, favorito dominante, momentum compuesto).
- **Veredicto**: **0 / 46 features superan |r| ≥ 0.15**. Máximo `|r|` = 0.0976 (`away_corners_against_atAway_L15`). R² conjunto de las top-10 vía OLS multivariada estandarizada = **0.0210** (solo ~2% de varianza explicada).
- **Pytest**: 4421 passed / 2 skipped / 0 failures — cero regresiones.

### Hallazgos cualitativos (de la tabla del reporte)

- **L5 vs L15**: en TODOS los pares evaluados (corners_for, corners_against, sum, momentum), `L15` gana por margen pequeño pero consistente. La ventana corta (L5) tiene más ruido.
- **Home/Away split (venue filter)**: para `away_corners_against`, filtrar por venue MEJORA r (0.0976 vs 0.0911); para las otras features lo empeora. No hay regla universal.
- **Favorito dominante**: r individual bajísimo (0.06 y 0.05). En OLS multivariado aparecen con β grandes pero esto es **multicolinealidad espuria** (`fav_implied_prob` y `abs_implied_prob_diff` están casi perfectamente correlacionados entre sí).
- **Serie activa** (rachas over 9.5 en L5): r ≈ 0.03 (home) y 0.04 (away). Sin señal.
- **Δ MAE vs baseline naïf** (media del training): la mejor feature mejora apenas 0.016 córners sobre ~10±5 → ~0.16% relativo. Insignificante operativamente.

### Recomendaciones para el usuario (Fase 2)

Tres caminos posibles, ordenados por costo/beneficio:

1. **Relajar el umbral a |r| ≥ 0.10**. Con `n=4338`, las top-6 features (todas en `away_corners_against*` + `away_shots_against*`) son estadísticamente significativas y podrían formar un mini-pool. Riesgo: motor con techo R²≈2%, irrelevante en producción.
2. **Cambiar de fuente** a una con xG/posesión/ataques peligrosos (API-Sports, Understat). El xG **a favor** del oponente es predictor probado de córners. Costo: créditos.
3. **Pivotear de mercado**: dado el techo bajo, considerar O/U corners como mercado "informativo" no como "predictivo activo" (no recomendarlo; mostrar contexto). Costo: cero.

### Entregables

- `/app/backend/scripts/build_corner_momentum_dataset.py` — constructor del dataset unificado (4338 partidos).
- `/app/data/corners_history/all_leagues_dataset.json` — dataset canónico.
- `/app/backend/scripts/run_corner_momentum_study.py` — pipeline cuantitativo (Pearson, walk-forward, OLS multivariada).
- `/app/diagnostics/corner_momentum_study_phase1_stats.json` — métricas raw.
- `/app/diagnostics/corner_momentum_study_phase1_report.md` — reporte legible (8 secciones).

### Restricciones cumplidas

- ✅ Cero cambios al código de producción.
- ✅ Cero APIs de pago.
- ✅ Cero nuevas dependencias en `requirements.txt`.
- ✅ Punto-en-tiempo estricto en el cálculo de features.
- ✅ Pytest backend completo en verde (4421/4421).

---

## Phase Corner Momentum Study — Fase 1 (Opción B) — **(referencia previa, mantenida abajo)**

> **Alcance:** SOLO evidencia cuantitativa (no diseño de motor, no heurísticas, no integración). Fuentes gratis. Sin consumo de créditos.

### Estado actual (inputs disponibles)
- Backend estable: **4421 tests passing / 2 skipped** (regla: cero regresiones).
- CSVs ya disponibles en `/app/data/football_data_co_uk/`:
  - EPL: `E0_2122.csv`, `E0_2223.csv`, `E0_2324.csv` (+ `E0_2425.csv` extra, opcional)
  - Temporada 24/25 suelta (no multiseason): `D1_2425.csv`, `SP1_2425.csv`, `I1_2425.csv` (y `F1_2425.csv` si se quisiera Ligue 1, no requerida)

### Sub-fase 1.1 — Descarga de datos (gratis, football-data.co.uk)
**Objetivo:** completar 3 temporadas por liga para Bundesliga/LaLiga/SerieA y añadir Liga MX si existe.

- Descargar:
  - Bundesliga: `D1_2122.csv`, `D1_2223.csv`, `D1_2324.csv`
  - La Liga: `SP1_2122.csv`, `SP1_2223.csv`, `SP1_2324.csv`
  - Serie A: `I1_2122.csv`, `I1_2223.csv`, `I1_2324.csv`
- Liga MX:
  - Intentar localizar división/código (p.ej., `MX1`/`MEX`/`M0`) en football-data.
  - **Condición de aceptación**: debe incluir columnas de córners (`HC`, `AC`).
  - Si no existe o no trae `HC/AC`, documentar explícitamente en el reporte como **no disponible** y proceder solo con europeas.

**Entregable:** CSVs almacenados en `/app/data/football_data_co_uk/`.

### Sub-fase 1.2 — Constructor de dataset unificado (PIT-friendly)
- Nuevo script: `backend/scripts/build_corner_momentum_dataset.py`
- Input: CSVs por liga/temporada.
- Normalización → records canónicos con:
  - `match_id`, `date`, `league`, `season`
  - `home_team`, `away_team`
  - Ground truth: `home_corners`(HC), `away_corners`(AC), `total_corners`
  - Variables de partido (si están):
    - `home_shots`(HS), `away_shots`(AS)
    - `home_shots_on_target`(HST), `away_shots_on_target`(AST)
    - `home_fouls`(HF), `away_fouls`(AF)
    - `home_cards`(HY+HR), `away_cards`(AY+AR)
    - `FTHG`, `FTAG`
    - Odds: `B365H`, `B365D`, `B365A` (fallback a `AvgH/AvgD/AvgA` si faltan)
- Output:
  - `/app/data/corners_history/all_leagues_dataset.json`
- Reglas de calidad:
  - Drop rows sin `Date` o sin `HC/AC`.
  - Parse robusto de fechas (`%d/%m/%Y`, `%d/%m/%y`, `%Y-%m-%d`).

**Nota PIT:** el dataset se usa para cálculo de features con historia estricta (`row_dt < target_dt`).

### Sub-fase 1.3 — Estudio cuantitativo (L5 vs L15 + splits + momentum)
- Nuevo script: `backend/scripts/run_corner_momentum_study.py`
- Target principal:
  - `total_corners` del partido.
- Features por equipo y match (todas PIT):
  1) **L5 vs L15**
     - `home_corners_for_avg_L5/L15`, `away_corners_for_avg_L5/L15`
     - `home_corners_against_avg_L5/L15`, `away_corners_against_avg_L5/L15`
     - deltas L5-L15.
  2) **Home/Away split**
     - stats L5/L15 separadas por local/visitante.
  3) **Ofensivas**
     - `shots_avg_L5/L15`, `sot_avg_L5/L15` (por equipo)
     - proxies simples documentados (si aplican): p.ej. `pressure_proxy = shots + 2*sot`.
  4) **Defensivas**
     - `corners_conceded_avg_L5/L15`, `shots_conceded_avg_L5/L15`.
  5) **Serie activa**
     - rachas de over/under sobre línea base (p.ej. 9.5/10.5) como features *diagnósticas* (no reglas).
  6) **Favorito dominante**
     - `implied_prob_home/away` desde B365; `abs_implied_prob_diff`.
  7) **Corner Momentum (compuesta)**
     - feature estandarizada con documentación exacta (ej. z-score del diferencial de córners L5, con std computed PIT).

- Métricas requeridas (por liga y global):
  - Correlación Pearson de cada feature vs `total_corners`.
  - Modelos simples (diagnósticos, no producción):
    - Regresión lineal: MAE/RMSE por validación temporal (walk-forward simple).
    - RandomForestRegressor: Feature Importance (con seed fija) como señal secundaria.
- Regla de descarte (acordada):
  - **Descartar feature si |r| < 0.15** (global y/o consistentemente por liga).

### Sub-fase 1.4 — Reporte y artefactos
- Reporte: `/app/diagnostics/corner_momentum_study_phase1_report.md`
  - Tabla por feature: r, MAE/RMSE (cuando aplique), importancia, consistencia por liga.
  - Sección específica para Liga MX (éxito/fracaso de obtención, cobertura, columnas disponibles).
  - Conclusiones accionables: qué señales pasan el umbral y cuáles se descartan.
- Stats raw: `/app/diagnostics/corner_momentum_study_phase1_stats.json`

### Sub-fase 1.5 — Validación (cero regresión)
- Ejecutar:
  - `pytest backend/tests -x --timeout=60`
- Restricción:
  - **Cero cambios a código de producción** (solo scripts + data + diagnostics).
  - **Cero uso de APIs de pago**.

---

## Phase F95 — Football Post-Match Settlement Hotfix (P0) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## Phase F96 — Football: Settler corners + TheSportsDB experimental + ingest fallback (P1) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## Phase Sprint-D8-Research — Cards vs Corners (P1) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## Phase Sprint-D8/E-LIVE — Corners diagnostic + Cards Fase 1 (P1) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## Phase Sprint-D8-Fase2 — DRAW selecciones + cascada TheSportsDB primaria (P1) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## Phase F99 — Refactor estructural `mlb_day_orchestrator.py` (P0) — ✅ COMPLETADO
(…sin cambios; ver bitácora previa.)

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ **Corner Momentum Study — Fase 1 (Opción B)** completada.
- ✅ **Sprint Corner-2 (datos ricos Understat)** completada.
- ✅ **Sprint Corner-1 + Corner-2 · Fase A** completada (módulos puros + backtest probabilístico).
- ⏳ **Fase B**: integración endpoint /api/football/picks + UI cards detrás de feature flags. Decisión del usuario pendiente.
- ⏳ **Backtest con cuotas reales** (~100-150 eventos, ~6-9k créditos TheOddsAPI). Decisión del usuario pendiente.

### Pendientes P1
- 🟡 **SPRINT D5** (histórico en curso): cohortes + reportes multi-competición.
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Nuevas hipótesis de señal para O/U 2.5:
  - features adicionales (lineups, std de xG, matchup/estilos, fatiga),
  - calibración por liga (pero con guardrails anti-overfitting),
  - o pivotear a otro mercado/línea.

---

## 4) Cierres recientes (bitácora)

### ✅ SPRINT D12 — Cierre (NB Recalibration Wiring + UI “Riesgos ocultos del Under”) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ SPRINT D13 — MLB Matchup Familiarity Overlay (D13.1) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ SPRINT D13.2 — Matchup Familiarity Overlay extendido a ML/RL + Active Scoring — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ NIVEL 3 — Bloque 1 · Dynamic Run Distribution Mixer — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ NIVEL 3 — Bloque 2 (§1-§4) · Tail Calibration + Threshold Model + Blender **ACTIVO** — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ NIVEL 3 — Bloque 3 (§5-§6) · Under hard rules + UI “Distribución y colas” — COMPLETADO
(Ver fases F97.1–F97.4.)

---

### ✅ SPRINT D9.2 Block C — Residual Model con xG real (Bonferroni estricto) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - Observe-only por defecto; excepciones explícitas:
    - D13.2: scoring activo con clamps + snapshots.
    - NIVEL 3 Bloque 2: ACTIVE writeback a `expected_runs_distribution`.

- Backend: ejecutar `pytest` completo tras cambios.

**Estado actual de la suite backend:** `4421 passed / 2 skipped` (0 regresiones).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **No tocar** `MONGO_URL` ni `REACT_APP_BACKEND_URL`.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - TheSportsDB: `THESPORTSDB_KEY=...`.

---

## SPRINT F — Ingesta de Tendencias Top desde 365Scores — COMPLETADO ✅
(Sin cambios.)

---

## SPRINT D8 — UNDER_3_5 (ligas) + DRAW/cohorte (selecciones)
(Sin cambios.)

---

## SPRINT D9.2 — Block 0 + A + B (COMPLETADO ✅)
(Sin cambios.)

---

## SPRINT D9.3 — Active Series Context Fix + Expansion (P0 hotfix)
(Sin cambios.)
