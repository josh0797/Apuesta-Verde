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
- 🟡 **SPRINT D9.2 — Block C** Residual Model con xG real (después de D9.3).
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.
- ⏳ **D8 Fase 2** — selecciones (DRAW + cohorte favorito-dominante) con MAX_CREDITS=2500 (bloqueado por ground truth Copa América 2024).

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
  - El ratio efectivo permanece clamped a **[0.90, 3.00]** (invariante), evitando “colas absurdas”.

- ✅ **B2 — Orquestador M5.6** (`backend/services/mlb_day_orchestrator.py`):
  - Invoca `compute_total_risk_overlay()`.
  - Calcula `bullpen_stress` (por lado) y `domino_risk` (por lado) usando datos ya en scope.
  - Expone `pick_payload["total_risk_overlay"]` incluyendo:
    - `components.{starter_volatility, first_inning_collapse, recent_offensive_quality, lineup_explosiveness, bullpen_stress, domino_risk}` para UI.
  - Si `verdict ∈ {AVOID, BLOCK}` y `dispersion_multiplier > 1.0`:
    - **recomputa** `expected_runs_distribution` pasando `overlay_*`.
    - preserva el anterior en `expected_runs_distribution_pre_overlay`.
    - promueve `reason_codes` del overlay al listado canónico.
    - `pipeline_meta["expected_runs_distribution"]` refleja:
      - `overlay_applied=True`, `overlay_verdict`, `overlay_multiplier`.

- ✅ **B3 — Tests**:
  - Nuevo archivo: `backend/tests/test_mlb_d12_nb_overlay_wiring.py` (**13 tests**).
  - Suite backend total: **3996 passed / 2 skipped** (0 regresiones).

#### Entregables frontend
- ✅ **F1 — UI “Riesgos ocultos del Under”**
  - Nuevo componente: `frontend/src/components/UnderHiddenRisksCard.jsx`:
    - 6 tarjetas (grid 2×3):
      1) Volatilidad starter
      2) Riesgo 1ª entrada
      3) Ofensiva reciente
      4) Explosividad lineup
      5) Estrés bullpen
      6) Riesgo dominó
    - Badges:
      - veredicto: Permitido/Precaución/Evitar/Bloqueado,
      - cola: baja/media/alta/extrema,
      - `NB ×multiplier` cuando aplica.
    - `editorial_summary` + chips de `reason_codes` traducidos.
    - `data-testid` por tarjeta y por badge.
  - Integración:
    - `MLBScriptPanel.jsx` (nuevo prop `totalRiskOverlay`).
    - `MatchCard.jsx` y `pages/MatchDetailPage.jsx` pasan `m.total_risk_overlay`.
  - Tests:
    - `frontend/src/components/__tests__/UnderHiddenRisksCard.test.jsx` (**16 tests**, verdes).
  - Build/lint:
    - `esbuild` manual (via `npx esbuild`) y lint JS: OK.

#### Nota sobre tests frontend
- La suite FE global tiene **2 fallos pre-existentes** en `InlineManualOddsInputF93_1.test.jsx` (no relacionados con D12).

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - `observe_only` (sin apuestas automáticas) — **excepto** recalibración NB: afecta solo probabilidades/colas (no pick-binding).
  - Backend: ejecutar `pytest` completo tras cambios.
  - Frontend: `yarn` + `esbuild` tras cambios; RTL tests por componente cuando aplique.

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **Observe-only**: no implementar apuestas automáticas.

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

---

## SPRINT D9.2 — Block C: Residual Model con xG real (CONFIRMADO, PENDIENTE)

Sin cambios (ver bitácora previa).
