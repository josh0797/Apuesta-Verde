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

- `/app/backend/scripts/ingest_understat_corners.py`
- `/app/data/corners_history/understat_matches_consolidated.json`
- `/app/backend/scripts/merge_corners_with_understat.py`
- `/app/data/corners_history/all_leagues_enriched_dataset.json`
- `/app/backend/scripts/run_corner_momentum_study_phase15.py`
- `/app/diagnostics/corner_momentum_study_phase15_stats.json` y `corner_momentum_study_phase15_report.md`

### Restricciones cumplidas

- ✅ Cero cambios al código de producción.
- ✅ Cero APIs de pago.
- ✅ Pytest backend completo: **4421 passed / 2 skipped / 0 failures**.

---

## Phase Corner Momentum Study — Fase 1 (Opción B) — **✅ COMPLETADA**

---

## Phase Sprint Corner-1 + Corner-2 · Fase A — Motor de córners (módulos puros + backtest) — **✅ COMPLETADA (P0)**

> **Alcance:** módulos algorítmicos puros (zero touch a producción) + backtest probabilístico walk-forward sobre 4338 partidos. **No incluye** ROI financiero real (REAL_ODDS_NOT_AVAILABLE).

### Módulos creados

- `/app/backend/services/football/corners/corner_diff_model.py`
- `/app/backend/services/football/corners/corner_most_model.py`
- `/app/backend/services/football/corners/corner_diff_distribution.py`
- `/app/backend/services/football/corners/corner_backtest.py`

### Tests obligatorios

- `/app/backend/tests/test_corner_engine_phase_a.py`

### Resultados del backtest (walk-forward)

- Brier **0.5074** (lineal) como baseline.

### Restricciones cumplidas

- ✅ Cero cambios a producción.
- ✅ Cero nuevas dependencias.
- ✅ REAL_ODDS_NOT_AVAILABLE cuando aplica.

---

## Phase Sprint Corner — Fase B — Skellam + Endpoint/UI — **✅ COMPLETADA (P0)**

> **Alcance:** modelo alternativo Skellam + endpoint REST + UI card detrás de feature flags.

### Modelo Skellam (estado base)
- IRLS Poisson (numpy) + convolución Poisson-Poisson para PMF Skellam.
- Caps λ ∈ [1, 18].

### Endpoint/UI
- `POST /api/football/corner-engine/predict`
- `GET /api/football/corner-engine/health`
- UI: `CornerEngineCard` integrado en `MatchDetailPage`.

### Tests
- `test_corner_engine_router.py` + `test_corner_engine_phase_a.py`.

### Validación previa
- ✅ Pytest: **4440 passed / 2 skipped**.

---

## Phase Sprint Corner — Fase B.1 — Skellam P0: Estabilidad out-of-sample + Guards + Validación avanzada — **✅ COMPLETADA (P0)**

> **Motivación:** se reportó un bug histórico donde el Skellam saturaba `λ=18` fuera de muestra. Objetivo: diagnosticar sin “tapar” bajando `LAMBDA_MAX`, instrumentar explicabilidad y endurecer el motor.

### Diagnóstico (hallazgo clave)
- El bug **NO se reproduce** con el dataset enriquecido completo (4338 partidos) y los coefs persistidos en `calibrated_defaults.json`.
- Rango observado en test out-of-sample (2324): **λ_max ≈ 8.95**.
- Conclusión: el `λ=18` fue un escenario transitorio de exploración (subsets / interacción), no un fallo sistémico actual.

### Multicolinealidad documentada (no bloqueante)
- Coefs persistidos muestran signos opuestos en:
  - `deep_allowed_L15`: **-0.569 home vs +1.329 away**
- `xg_for_L15` con signo negativo (redundancia con `corners_for_L15` + implied_prob).
- Se decide **no** “arreglar por fuerza” (cambiar cap) sino:
  - reportar explícitamente el riesgo,
  - instrumentar guards para identificar el driver culpable si vuelve a ocurrir.

### Cambios implementados (código)
1. **Guards defensivos en `_compute_lambda`**
   - Ahora retorna: `(lam, drivers, warnings)`.
   - Warnings:
     - `LAMBDA_SATURATED` si λ ≥ 18
     - `LAMBDA_HIGH_WARNING` si λ ∈ [12, 18)
     - `DRIVER_DOMINANT_<FEATURE>` si una contribución al exponente `z` excede 2.0

2. **Nueva función pública `validate_skellam_coefs(coefs_home, coefs_away)`**
   - Detecta:
     - |β|>2.0 (excl. intercept) → warning con magnitud
     - signos opuestos no-triviales entre home/away por feature

3. **`predict_skellam_corner_diff` propaga warnings**
   - Agrega reason codes con prefijos `HOME_` y `AWAY_`.
   - Agrega issues de coeficientes (`SKELLAM_COEFS_SUSPICIOUS_*`).

4. **Calibración endurecida/configurable**
   - `calibrate_skellam_lambdas(..., ridge_strength=...)` y `_poisson_mle(..., ridge_strength=...)`.
   - Default actualizado a `ridge_strength=0.5`.
   - Se compararon 0.1/0.5/1.0/2.0: coefs prácticamente iguales → la colinealidad es mayormente estructural.

### Tests agregados
- `tests/test_corner_engine_skellam_guards.py` (12 tests):
  - saturación, high-warning, driver dominante, validación coefs, sanity λ-range.
- `tests/test_corner_engine_advanced_models.py` (11 tests):
  - Ensemble: suma probs, EDCD entre componentes, reason tag
  - Monte Carlo: media/monotonía/BTGC
  - Jerárquico: calibración y fallback

### Validación
- ✅ Pytest suite completa: **4463 passed / 2 skipped / 0 failures** (antes 4440; +23 nuevos).
- ✅ Sin regresiones.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ Corner Momentum Study (Fase 1) completada.
- ✅ Sprint Corner-2 (Understat) completada.
- ✅ Sprint Corner Fase A (módulos + backtest probabilístico) completada.
- ✅ Sprint Corner Fase B (Skellam + endpoint + UI) completada.
- ✅ **Skellam P0 estabilidad/guards/validación avanzada** completada.

### Pendientes P1 (próximo)
- ⏳ **Backtest financiero con TheOddsAPI (P1)**
  - Objetivo: 100–150 partidos (muestra controlada por coste de créditos).
  - Mercados: **Asian Corners** y/o “Most Corners” (si hay odds históricas disponibles).
  - Condición: marcar explícitamente `REAL_ODDS_NOT_AVAILABLE` cuando falten cuotas.
  - Entregables:
    - reporte ROI/CLV/hit-rate por línea
    - breakdown por liga + por bucket de `dominant_favorite_strength`
    - auditoría de sesgo (solo picks recomendados vs todos)

- 🟡 SPRINT D5 (histórico en curso): cohortes + reportes multi-competición.
- 🟡 REFACTOR-1 (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ F84.c/F84.d Lineups + Standings.

### Pendientes P2
- ⏳ (Acordado) **NO construir aún Total Corners O/U** como motor principal.

---

## 4) Cierres recientes (bitácora)

### ✅ Sprint Corner — Fase B.1 (Skellam P0): Guards + validación + tests + suite verde
- Añadidos reason codes explicables para saturación y drivers dominantes.
- `validate_skellam_coefs` para documentar colinealidad y magnitudes sospechosas.
- +23 tests nuevos.
- Suite backend en verde: **4463 passed**.

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

**Estado actual de la suite backend:** `4463 passed / 2 skipped` (0 regresiones).

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
  - Corner Engine:
    - `ENABLE_CORNER_MOST_MODEL=true`
    - `ENABLE_ASIAN_CORNERS_MODEL=true`

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
