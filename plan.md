# Development Plan — PHASE 56 (✅) + PHASE 57 (✅ Backend) + PHASE F57 (✅) + PHASE 58 (✅) + PHASE 59 (✅)

## 1) Objectives

### PHASE 56 — MLB Layer Interaction Audit (observe-only) ✅ COMPLETADO
- Detectar y **medir posible double-counting** de señales entre:
  - `mlb_expected_runs_distribution` (PMF/CDF + tail probs)
  - `mlb_tail_fragility` (Phase 55: base + interactions)
  - `mlb_fragility_calibrator` (hidden over routes: component_deltas)
- Añadir **telemetría profunda** en `mlb_day_orchestrator.py` sin alterar picks, mercado elegido ni polaridad (**observe-only**).
- Entregar un **script de auditoría** reproducible (default synthetic) que compare 4 modos y genere JSON + resumen por stdout.
- Incorporar **guardrails observe-only** (warnings/labels) basados en tamaño de muestra, incluyendo regla especial para tails.
- **Estado actual**: ✅ Objetivos completados.

### PHASE 57 — MLB Player Props Discovery (Moneyball) ✅ COMPLETADO (Backend + endpoint)
Construir un motor de descubrimiento de player props “Moneyball” centrado en props **repetibles**, **baja fragilidad** y **alta probabilidad**, evitando longshots.

Decisiones implementadas:
- **Mercados soportados**:
  - Principales: **H+R+RBI**, **Total Bases**
  - Conservadores adicionales: **Hits 1+**, **RBI 1+**, **Runs 1+**
- **Datos**:
  - Base obligatoria: **MLB Stats API** (season stats + roster/hydrate)
  - Enriquecimiento opcional: **Baseball Savant para bateadores** (xwOBA, xSLG, Barrel %, Hard Hit %, Exit Velocity, etc.)
    - Fail-soft, timeout corto, cache (TTL ~24h)
    - `data_quality` por prop: `COMPLETE|PARTIAL|MINIMAL`
- **Scoring / Edge**: Pure Python determinístico (Poisson + multiplicadores conservadores).
- **Alcance entregado**: Backend + endpoint **/api/mlb/player-props**.
- **UI**: diferida (no implementada en este turno por decisión de alcance).

### PHASE F57 — Football Context + Trend Discovery ✅ COMPLETADO (observe-only)
Implementar una nueva capa de fútbol para capturar contexto humano + tendencias que el engine actual omite:
- Squad disruption (indisciplina, apartados, conflictos internos, etc.) vía ingestión de noticias
- Recent form streaks (racha, goles a favor/en contra)
- Corners trend (comparar promedio últimos 10 vs últimos 5)
- Protected goals trend (prefiere Over 1.5 / Over 1.75 vs mercados agresivos)
- Missed match rescue (si el engine descarta/omite un partido con señales fuertes)

**Estado actual**: ✅ módulo backend + endpoint + UI en MatchDetailPage (self-gating) completados.

### PHASE 58 — MLB Structural Symmetry + Hierarchical Source of Truth ✅ COMPLETADO
Eliminar sesgos estructurales MLB y formalizar una jerarquía de “source-of-truth”:
- **Distribución NB** canoniza proyección y probabilidades base.
- **Calibradores simétricos** ajustan fragility sin alterar polaridad.
- **Patrones históricos** ajustan confianza (penalización simétrica por contradicción).
- **UI** refleja conflictos/penalizaciones con un badge explícito y tooltip.

Resultados / entregables (CAMBIOS 1–5):
1) ✅ Swap simétrico Over/Under en `mlb_over_discovery.py` con umbral exacto:
   - `o_edge - u_edge >= 1.0` **o** `o_score - u_score >= 6.0`
   - Telemetría: `symmetric_swap_applied: True`
2) ✅ Nuevo `backend/services/mlb_under_fragility_calibrator.py` (mirror del calibrador Over)
   - Guard de polaridad estricto: solo corre para `market_side == "over"`
   - Caps idénticos: `MAX_DELTA=20`, `MAX_CEILING=85`
3) ✅ Source of Truth jerárquico integrado en `mlb_day_orchestrator.py`
   - Escribe **fragility efectiva** en `pick_payload.fragility.score` y `pick_payload.fragility_score`
   - Telemetría: `pick_payload.mlb_source_of_truth` + `under_fragility_calibration` cuando aplica
4) ✅ Penalización simétrica de confianza por contradicción de patrones (CAMBIO 4)
   - Estados: `VALUE_CON_CONFLICTO` (ámbar), `VALUE_REVISAR` (azul/cyan)
   - Campos: `pick_conflict_state`, `pattern_penalty_applied`, `confidence_pre_pattern_penalty`
   - Añade `PATTERN_CONTRADICTION_CONFIDENCE_PENALTY` a `recommendation.reason_codes`
   - Normalización: `pick_payload.pattern_alignment` (atajo root) + telemetría SOT actualizada
5) ✅ UI: `frontend/src/components/ConfidenceBadge.jsx` + integración en `MatchDetailPage.jsx`
   - Props mínimas (establecidas por usuario):
     ```jsx
     <ConfidenceBadge
       confidence={recommendation.confidence_score}
       conflictState={pick.pick_conflict_state}
       penaltyApplied={pick.pattern_penalty_applied}
       confidencePrePenalty={pick.confidence_pre_pattern_penalty}
     />
     ```
   - Tooltip explicativo y estilo por estado

Calidad / verificación:
- ✅ Pytest backend: **1649/1649 passed** (10 tests nuevos para CAMBIO 4)
- ✅ Verificación visual: badge validado en 4 estados via screenshot tool
- ✅ Página de debug temporal creada y eliminada tras la validación

### PHASE 59 — MLB L5 vs L15 Run Profile Cross Analysis ✅ COMPLETADO
Mejorar la interpretación del “Historial profundo / últimos 15 juegos” separando **ofensiva (anota)** vs **prevención (permite)** en L5 vs L15, y generando un **cruce** entre ambos equipos que aporte:
- Clasificación de perfil (5 buckets)
- Señal contextual (apoya UNDER/OVER/NEUTRAL)
- Ajuste simétrico de **confianza** y **fragility** (sin cambiar polaridad)
- Integración con `pattern_alignment` como **entrada visual/auditable** (`visual_only=true`) sin alterar el ratio del CAMBIO 4

Perfiles combinados soportados:
- `STRONG_UNDER_CROSS`
- `LOW_SCORING_CROSS`
- `HIGH_SCORING_CROSS`
- `STRONG_OVER_CROSS`
- `MIXED_PROFILE`

Contrato de capa (alineado con jerarquía SOT de Phase 58):
- La distribución NB canoniza proyección y probabilidades base.
- CAMBIO 4 (contradicción de patrones) ajusta confianza primero.
- **Phase 59 aplica DESPUÉS** como capa contextual:
  - bonus máximo: **+8**
  - penalty máximo: **-12**
  - fragility clamp: **0–100**
- Fail-soft y no-op cuando faltan datos.

Implementación completada:
- ✅ Nuevo `backend/services/mlb_run_profile_cross.py`
  - Per-team signals (reason codes):
    - `TEAM_OFFENSE_COOLING`, `TEAM_OFFENSE_HEATING`
    - `TEAM_RUN_PREVENTION_IMPROVING`, `TEAM_RUN_PREVENTION_WEAKENING`
  - Combined cross payload: `combined_run_profile_cross`
  - Helper simétrico: `apply_run_profile_cross_to_pick`
  - Entry visual para `pattern_alignment.entries`: `visual_only=true`
- ✅ Extensión `backend/services/mlb_recent_form_split.py`
  - Deriva `runs_allowed_avg_last_5/15` desde el **mismo boxscore** (sin nuevas llamadas API)
  - Propaga al payload `recent_run_split`:
    - `runs_allowed_avg_last_5_home/away`, `runs_allowed_avg_last_15_home/away`
    - `runs_allowed_delta_5_vs_15_home/away`
- ✅ Integración en `backend/services/mlb_day_orchestrator.py`
  - Se ejecuta **después** del bloque CAMBIO 4
  - Telemetría:
    - `combined_run_profile_cross`
    - `run_profile_cross_applied`
    - `mlb_source_of_truth.run_profile_cross_profile` / `run_profile_cross_supports`
  - Entrada visual en `pattern_alignment.entries` (no afecta counts)
  - Mirror a `baseballHistoricalProfile.combinedRunProfileCross`
- ✅ Frontend
  - `RunProfileCrossBlock` (exportado) dentro de `HistoricalProfilePanel.jsx`
  - Integrado en el panel existente **“Tendencia carreras 5 vs 15 juegos”**
  - UI: badge semántico (verde=Under, rose=Over, ámbar=Mixto), flechas direccionales, narrativa ES
- ✅ Tests
  - 28 tests unitarios: `backend/tests/test_mlb_run_profile_cross.py`
  - 7 tests integración de wiring/order: `backend/tests/test_phase59_run_profile_cross_integration.py`
- ✅ Verificación visual: 5 estados validados via screenshot tool (página debug temporal creada y eliminada)

### Cleanup técnico ✅ COMPLETADO
- Resolver errores pre-existentes (ruff blocking) en `mlb_day_orchestrator.py`:
  - F821 `traffic_score_payload` undefined
  - E701/E702 statements múltiples en una línea
- **Estado actual**: ✅ 0 errores blocking + suite completa verde.


## 2) Implementation Steps

### Phase 1 — PHASE 56 (✅ COMPLETADO): Auditoría sintética reproducible + telemetría
> Core workflow = generar dataset sintético, ejecutar 4 modos, producir reporte JSON con métricas y flags de overlap/double-count.

**Implementación (✅ COMPLETADA)**
- ✅ `backend/scripts/audit_mlb_layer_interactions.py`
- ✅ `backend/services/mlb_layer_interaction_audit.py`
- ✅ Integración en `backend/services/mlb_day_orchestrator.py` (observe-only)
- ✅ Tests: `backend/tests/test_phase56_layer_interaction_audit.py` (17)

### Phase 2 — PHASE 57 (✅ COMPLETADO): Datos y Enriquecimiento (Stats API + Savant batter)
- ✅ `backend/services/baseball_savant_batter.py`
- ✅ `backend/services/mlb_player_props_discovery.py`

### Phase 3 — PHASE 57 (✅ COMPLETADO): API (server.py)
- ✅ Endpoint `/api/mlb/player-props`
- ✅ Tests: `backend/tests/test_mlb_player_props_discovery.py`

### Phase 4 — PHASE 57: UI básica (⏸️ DIFERIDA)
- ⏸️ `frontend/src/pages/MLBPlayerPropsPage.jsx` + route `/mlb/player-props`

### Phase 5 — PHASE F57 (✅ COMPLETADO): Football Context + Trend Discovery Engine (observe-only)
- ✅ `services/football_news_context_ingestion.py`
- ✅ `services/football_context_trend_discovery.py`
- ✅ Endpoint `/api/football/context-trend`
- ✅ UI: `FootballContextTrendCard.jsx` en `MatchDetailPage.jsx`
- ✅ Tests: `backend/tests/test_football_context_trend_discovery.py`

### Phase 6 — Post-work: Cleanup técnico (✅ COMPLETADO)
- ✅ Fixes ruff + wiring

### Phase 7 — PHASE 58 (✅ COMPLETADO): Symmetry + Hierarchical SOT + UI conflict badge
**Backend**
- ✅ Swap simétrico, calibrador under-routes, SOT jerárquico
- ✅ Penalización simétrica por contradicción de patrones
- ✅ Tests: `test_cambio4_pattern_contradiction_penalty.py` (10)

**Frontend**
- ✅ `ConfidenceBadge.jsx` + integración en `MatchDetailPage.jsx`
- ✅ Verificación visual

### Phase 8 — PHASE 59 (✅ COMPLETADO): L5 vs L15 Run Profile Cross Analysis
**Backend**
1) ✅ Crear `services/mlb_run_profile_cross.py` (5 perfiles + per-team reason codes + clamps simétricos)
2) ✅ Extender `services/mlb_recent_form_split.py` para runs_allowed L5/L15 (derivado del boxscore)
3) ✅ Integrar en `services/mlb_day_orchestrator.py` **después** del CAMBIO 4
   - Ajuste simétrico de confianza/fragility (sin flip)
   - Telemetría + entry visual `pattern_alignment.entries` (`visual_only=true`)
   - Mirror a `baseballHistoricalProfile.combinedRunProfileCross`
4) ✅ Tests
   - `test_mlb_run_profile_cross.py` (28)
   - `test_phase59_run_profile_cross_integration.py` (7)

**Frontend**
1) ✅ Export + integración `RunProfileCrossBlock` dentro del panel **“Tendencia carreras 5 vs 15 juegos”**
2) ✅ Badges semánticos + flechas direccionales + narrativa ES
3) ✅ Verificación visual (página debug temporal creada y eliminada)


## 3) Next Actions

### Próximos pasos recomendados (prioridad)
1) **Completar adopción del ConfidenceBadge (UI) — pendiente parcial**
   - Integrar `ConfidenceBadge` en:
     - `frontend/src/components/MatchCard.jsx` (listado / dashboard)
     - Panel histórico (si se desea mostrar también allí; actualmente se integró en MatchDetailPage)

2) **Observabilidad en producción / logs reales**
   - Monitorear frecuencia real de:
     - `pick_conflict_state`
     - `pattern_penalty_applied.ratio` y distribución de penalties
     - `combined_run_profile_cross.profile` y `run_profile_cross_applied.interaction`

3) **Tests de UI (RTL)**
   - Añadir tests de React Testing Library para:
     - `ConfidenceBadge` (render + tooltip + tachado pre-penalty)
     - `RunProfileCrossBlock` (render de 5 perfiles, badges, flechas)

4) **MLB Phase 57 UI (pendiente)**
   - Implementar `MLBPlayerPropsPage.jsx` + ruta `/mlb/player-props`

5) **Football F57 integración híbrida con orchestrator (opcional)**
   - Inyectar bloque `context_trend` en output del engine principal, manteniendo observe-only.


## 4) Success Criteria

### Phase 56 (✅)
- ✅ Script genera JSON + resumen stdout.
- ✅ Telemetría per-pick + pipeline_meta agregada.
- ✅ Observe-only verificado.

### Phase 57 (✅ Backend)
- ✅ Endpoint `/api/mlb/player-props` devuelve props Moneyball por fecha, fail-soft.
- ✅ Savant batter enrichment opcional con cache + timeout.
- ✅ Motor determinístico con filtros anti-longshot + `data_quality`.
- ⏸️ UI básica pendiente.

### Phase F57 (✅)
- ✅ Endpoint `/api/football/context-trend` devuelve señales (news/form/corners/goals/rescue), observe-only.
- ✅ News ingestion fail-soft con cache + URLs de fuente.
- ✅ UI integrada en MatchDetailPage con self-gating.

### Phase 58 (✅)
- ✅ Simetría Over/Under en swap de mercado.
- ✅ Calibrador simétrico Under-routes agregado y separado por polaridad.
- ✅ Jerarquía SOT aplicada (NB canoniza, calibradores fragility, patrones confianza).
- ✅ Penalización simétrica de confianza por contradicción de patrones.
- ✅ UI actualizada con `ConfidenceBadge.jsx` y tooltips.

### Phase 59 (✅)
- ✅ Panel separa claramente:
  - Carreras anotadas L5 vs L15
  - Carreras recibidas (permitidas) L5 vs L15
- ✅ Detecta 5 perfiles:
  - `STRONG_UNDER_CROSS`, `LOW_SCORING_CROSS`, `HIGH_SCORING_CROSS`, `STRONG_OVER_CROSS`, `MIXED_PROFILE`
- ✅ Integración con `pattern_alignment` como entrada visual (`visual_only=true`) sin alterar counts
- ✅ Ajuste simétrico de confianza/fragility con clamps; no sobreescribe NB ni cambia polaridad
- ✅ Fail-soft
- ✅ Tests sintéticos cubren los 5 casos

### Calidad / regresiones
- ✅ `pytest tests/` → **1684/1684 passed**
- ✅ Verificación visual: cross UI validado para 5 perfiles via screenshot tool

---

## Apéndice — Findings preliminares (Phase 56 synthetic n=200, seed=56)
- Overlap (FULL_CURRENT) por familia:
  - `starter`: **22%** (avg_redundant≈5.0) — candidato principal
  - `series`: **15%** (avg_redundant≈3.0)
  - `defense`: **9%** (avg_redundant≈4.0)
  - `bullpen`: **6%** (avg_redundant≈5.0)
  - `tail`: 0% (correcto: Phase-55 consume tail una sola vez)
  - `traffic`: 0% (solo calibrator; no existe en tail_fragility)
- Comparación de modos:
  - `NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR` reduce `cal_delta_avg` ~1.74 pts y baja `cap_hit_rate`.
  - `LEGACY_SCALAR` incrementa `cal_delta_avg` y cap-hit.

> Nota: findings synthetic sirven para ejercitar y detectar patrones de overlap; antes de refactor se recomienda correr `--mode real` y aplicar guardrails de sample size.
