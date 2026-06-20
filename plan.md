# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
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
- **Bloques:**
  - **Bloque 1 (Mixer):** mezcla Poisson/NB dinámica ✅
  - Bloque 2: `tail_calibration` (pendiente)
  - Bloque 3: `threshold_probability_model` (pendiente)

### Objetivos nuevos / extendidos (D9.2-C) — Residual Model con xG real (Bonferroni estricto)
- Fortalecer el backtest residual para evitar falsos positivos por múltiples comparaciones:
  - Clasificador puro y testeable ✅
  - Corrección Bonferroni estricta ✅
  - Auditoría explícita de umbral ajustado y resultados por métrica ✅

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

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

### Objetivo
Validar un módulo puro `compute_draw_potential()` (fail-soft, explicable) antes de invertir en infraestructura completa.

### Implementación realizada
- ✅ `services/football_draw_potential.py` (módulo puro, reason codes, labeler)
- ✅ Backtest piloto retrospectivo (sin fugas de futuro)

### Resultado
- ✅ Señal inicial verificada; listo para integración en backtest framework.

---

## Phase SPRINT B — Football Learning Dataset + Loops + UI (COMPLETED ✅)

### Objetivo
Infraestructura para snapshots pre/post partido, cascada de scraping pre-match, loops de aprendizaje y panel UI.

### Implementación realizada
- ✅ Colección `football_match_learning_snapshots`
- ✅ APScheduler jobs (pre-match snapshots)
- ✅ Cascada TheStatsAPI / CONCACAF / CAF hydration
- ✅ 4 learning loops + DC/NB calibration
- ✅ UI: `LearningSnapshotPanel.jsx`

### Estado
- ✅ Suites verdes y sin regresiones.

---

## Phase SPRINT D — Framework Backtest Histórico Point-in-Time (COMPLETED ✅)

### Objetivo
Crear un motor de backtest riguroso con disciplina point-in-time (sin leakage) y walk-forward.

### Implementación realizada
- ✅ `services/football_historical_ingestor.py` (backtest PIT + `build_point_in_time_features`)
- ✅ `services/football_backtest_engine.py`
- ✅ `services/football_backtest_metrics.py`
- ✅ `scripts/run_backtest.py`
- ✅ Ejecución validada en Premier League 23/24

---

## Phase SPRINT D2 — Backtest histórico en torneos nacionales (WC2022 + Euro2024) — COMPLETADO ✅

### Objetivo
Validar si el módulo **Draw Potential** mejora en torneos nacionales manteniendo disciplina point-in-time.

### Estado
- ✅ Implementado: parser openfootball + standings PIT + `TOURNAMENT_CONTEXT_SCORE` + modo no-market + reportes.

---

## Phase SPRINT D3 — Backtest National Tournaments: OVER 1.5 + DOUBLE CHANCE (calibration-only) — COMPLETADO ✅

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT D5 — Multi-league + multi-tournament DRAW + cohortes (observe_only) — EN PROGRESO 🟡 (P0)

### Nota de estado
- Este bloque se mantiene como “D5” en el plan histórico.
- La ejecución y comparativa nueva (Sprint D7) reutiliza componentes de D5 (cohortes + comparativa) pero agrega The Odds API **historical** y cap de créditos.

---

## Phase SPRINT D6 — Probar que el Walk-Forward Calibrator NO es un no-op — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1 — Live Odds Monitor (Base) + persistencia `odds_snapshots` (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1-d — Hook automático (Scheduler) para Market Identity Resolver — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1-f — 365Scores “Tendencias Top” (reemplazo SportyTrader editorial) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

# Phase SPRINT D7 — Backtest comparativo DRAW (Ligas vs Selecciones) + Post-mortem & Remediación — COMPLETADO ✅ (P1)

(Sin cambios; ver bitácora previa.)

---

# Phase SPRINT D7-E — Threshold parametrization + honest sweep + multi-season sanity check (DRAW) — COMPLETADO ✅ (P1)

(Sin cambios; ver bitácora previa.)

---

# Phase SPRINT D7-F — OVER_2_5 / UNDER_2_5 con la misma disciplina (D7-E) — COMPLETADO ✅ (P1)

(Sin cambios; ver bitácora previa.)

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

(Sin cambios; ver bitácora previa.)

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳

(Sin cambios; ver bitácora previa.)

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- 🟡 **SPRINT D5** (histórico en curso): cohortes + reportes multi-competición.

### Pendientes P1
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.
- ⏳ **D8 Fase 2** — selecciones (DRAW + cohorte favorito-dominante) con MAX_CREDITS=2500 (bloqueado por ground truth Copa América 2024).
- ⏳ **NIVEL 3 Bloque 2:** Tail calibration.
- ⏳ **NIVEL 3 Bloque 3:** Threshold probability model.
- ⏳ (Opcional) UI para exponer:
  - `matchup_familiarity_overlay` (impact + snapshots)
  - `run_distribution_mixer` (comparación vs NB canónico)

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Nuevas hipótesis de señal para O/U 2.5:
  - features adicionales (lineups, std de xG, matchup/estilos, fatiga),
  - calibración por liga (pero con guardrails anti-overfitting),
  - o pivotear a otro mercado/línea.

---

## 4) Cierres recientes (bitácora)

### ✅ SPRINT D12 — Cierre (NB Recalibration Wiring + UI “Riesgos ocultos del Under”) — COMPLETADO

**Decisiones del usuario aplicadas (confirmadas):**
- **1b:** aplicar `dispersion_multiplier` activamente SOLO al NB cuando `verdict ∈ {AVOID, BLOCK}` (la polaridad/recomendación se mantiene observe-only).
- **2a:** UI en **grid 2×3**, colorizado por bucket.
- **3:** reason codes **traducidos al español**.

#### Entregables backend
- ✅ **B1 — Wire intra-módulo** (`backend/services/mlb_expected_runs_distribution.py`):
  - `compute_expected_runs_distribution(...)` ahora **propaga** `overlay_dispersion_multiplier` + `overlay_verdict` hacia `_compute_effective_dispersion(...)`.
  - El ratio efectivo permanece clamped a **[0.90, 3.00]**.

- ✅ **B2 — Orquestador M5.6** (`backend/services/mlb_day_orchestrator.py`):
  - Invoca `compute_total_risk_overlay()`.
  - Calcula `bullpen_stress` y `domino_risk` por lado.
  - Expone `pick_payload["total_risk_overlay"]` con `components.{starter_volatility, first_inning_collapse, recent_offensive_quality, lineup_explosiveness, bullpen_stress, domino_risk}`.
  - Si `verdict ∈ {AVOID, BLOCK}` y `dispersion_multiplier > 1.0`:
    - recomputa `expected_runs_distribution` con `overlay_*`.
    - preserva pre-overlay.

- ✅ **B3 — Tests**:
  - `backend/tests/test_mlb_d12_nb_overlay_wiring.py` (**13 tests**).

#### Entregables frontend
- ✅ UI “Riesgos ocultos del Under” (6 cards + reason codes traducidos) con tests RTL.

---

### ✅ SPRINT D13 — MLB Matchup Familiarity Overlay (D13.1) — COMPLETADO

**Módulo puro:** `backend/services/mlb_matchup_familiarity_overlay.py`
- Ventanas + métricas H2H + score + impacto en Totales.
- Hard cap 16 días (no contribuye a métricas/puntos si 16 días).
- Tests: `backend/tests/test_mlb_matchup_familiarity_overlay.py` (**46 tests**).

**Cableo:** `mlb_day_orchestrator.py` M5.7 (observe-only) publicando payload.

---

### ✅ SPRINT D13.2 — Matchup Familiarity Overlay extendido a ML/RL + Active Scoring — COMPLETADO

**Decisiones del usuario aplicadas:**
- A=a: módulos en `backend/services/mlb_*.py`.
- B=a: rename `totals_overlay` → `over_under_impact` con alias retro-compatible.
- C1: aplicar overlay.points al score de **todos** los mercados (TOTAL/ML/RL).
- C2: snapshots `pick_score_pre_d13` y `pick_score_post_d13`.
- C3=a: veto automático RL con **umbral |base_projected_margin| < 2.0**.
- D=a: NIVEL 3 solo Bloque 1 en esta entrega.

#### Cambios en `mlb_matchup_familiarity_overlay.py`
- Nuevas constantes:
  - `LEAN_HOME/AWAY/HOME_RL/AWAY_RL`
  - `MAX_ML_WIN_PROB_DELTA = 0.05`
  - `MAX_RL_MARGIN_DELTA   = 1.5`
  - `RL_BASE_MARGIN_VETO_THRESHOLD = 2.0`
- Nuevas funciones:
  - `_compute_moneyline_overlay()`:
    - 2+ wins con margen ≥2; avg diff ≥2 carreras; bullpen edge (1..3); starter seen recently (1..2).
    - Safety: no premia “ganó ayer” como señal única.
    - Clamps: puntos ±5; ajuste de probabilidad ±5%.
  - `_compute_runline_overlay()`:
    - 2+ wins por ≥2; avg_margin ≥|2.0|; late-inning scoring; bullpen fatigue.
    - Veto automático si |margen base| < 2.0 → puntos=0, `RL_VETOED_LOW_BASE_MARGIN`.
    - Clamps: puntos ±5; ajuste de margen ±1.5.
- Output canónico:
  - `over_under_impact`, `moneyline_impact`, `runline_impact`
  - alias `totals_overlay` (back-compat D13.1).
- Tests: `backend/tests/test_mlb_matchup_familiarity_overlay_d13_2.py` (**27 tests**).

#### Cableo activo en `mlb_day_orchestrator.py` (M5.7 extendido)
- Pasa `base_projected_margin` al overlay:
  - heurística: `(h_q.score - a_q.score) / 25.0`.
- Aplica el delta de scoring al pick real (defense in depth con clamp ±5):
  - TOTAL: por alineación lean vs side.
  - ML: por HOME/AWAY.
  - RL: por HOME_RL/AWAY_RL (si vetoed → 0).
- Snapshots:
  - `pick_payload["pick_score_pre_d13"]`, `pick_payload["pick_score_post_d13"]`
  - `pick_payload["d13_score_delta"]`, `pick_payload["d13_applied_block"]`
- Log `[D13.2_APPLY]` solo cuando delta ≠ 0.

---

### ✅ NIVEL 3 — Bloque 1 · Dynamic Run Distribution Mixer — COMPLETADO

**Módulo puro:** `backend/services/mlb_run_distribution_mixer.py`
- `build_dynamic_run_distribution(context)`:
  - Lambda desde baseline + park_factor + weather (clamped [2, 22]).
  - Volatility score 0..100 desde señales D11/D12.
  - Selección dinámica: POISSON / NB / MIXTURE; mixture forzado si datos parciales.
  - Dispersión mapeada a [1.0, 3.0].
  - Probabilidades O/U por umbral (.5): 6.5..14.5.
  - Percentiles p10/p25/p50/p75/p90/p95/p99.
  - Tail risk score/bucket + drivers.
  - NB PMF estable con log-gamma + renormalización tras truncamiento (MAX_RUNS=40).
- Tests: `backend/tests/test_mlb_run_distribution_mixer.py` (**33 tests**).

**Cableo observe-only:** bloque **M5.8** en `mlb_day_orchestrator.py`
- Publica `pick_payload["run_distribution_mixer"]` y `pipeline_meta["run_distribution_mixer"]`.
- NO muta el `expected_runs_distribution` canónico ni el pick.

---

### ✅ SPRINT D9.2 Block C — Residual Model con xG real (Bonferroni estricto) — COMPLETADO

- Módulo puro `backend/services/football_residual_verdict_classifier.py`.
- Script `backend/scripts/run_d9_residual_backtest.py` con flags `--alpha` y `--bonferroni-m` + bloque `bonferroni` persistido.
- Tests: `backend/tests/test_football_residual_verdict_classifier.py` (**21 tests**).

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - Observe-only por defecto; excepciones explícitas (D13.2: scoring activo con clamps + snapshots).
  - Backend: ejecutar `pytest` completo tras cambios.

**Estado actual de la suite backend:** `4123 passed / 2 skipped` (0 regresiones).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.

---

## SPRINT F — Ingesta de Tendencias Top desde 365Scores — COMPLETADO ✅

Sin cambios (ver bitácora previa).

---

## SPRINT D8 — UNDER_3_5 (ligas) + DRAW/cohorte (selecciones)

Sin cambios (ver bitácora previa).

---

## SPRINT D9.2 — Block 0 + A + B (COMPLETADO ✅)

Sin cambios (ver bitácora previa).

---

## SPRINT D9.3 — Active Series Context Fix + Expansion (P0 hotfix)

Sin cambios (ver bitácora previa; D9.3-A/B/C cerradas).
