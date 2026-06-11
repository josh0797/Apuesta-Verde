# Development Plan — PHASE 56 (✅) + PHASE 57 (✅ Backend) + PHASE F57 (✅) + PHASE 58 (✅)

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
  - 4 modos: `FULL_CURRENT`, `NO_DIRECT_TRAFFIC_DEFENSE_IN_CALIBRATOR`, `NO_DISPERSION_SIGNAL_MODULATION`, `LEGACY_SCALAR`
  - Default: `--mode synthetic` (determinístico, `--seed 56`)
  - Soporte opt-in: `--mode real --days N` (fail-soft con fallback a synthetic)
  - Output JSON en `/app/backend/scripts/out/` + resumen por stdout
  - Guardrails:
    - `n<10` → `HIGH_RISK_WARNING`
    - `10<=n<30` → `LOW_SAMPLE_WARNING`
    - `30<=n<100` → `USEFUL_SAMPLE`
    - `n>=100` → `VALIDATED_SAMPLE`
    - tails: `tail_high_or_extreme_samples_full_current < 20` → `TAIL_SAMPLE_TOO_LOW`
- ✅ `backend/services/mlb_layer_interaction_audit.py`
  - `build_layer_interaction_audit()`
  - `build_distribution_market_selection_effect()`
  - `summarise_for_pipeline_meta()`
- ✅ Integración en `backend/services/mlb_day_orchestrator.py` (observe-only)
  - `pick_payload.layer_interaction_audit`
  - `pick_payload.distribution_market_selection_effect`
  - `pipeline_meta.layer_interaction_audit` (agregado por slate)
- ✅ Tests: `backend/tests/test_phase56_layer_interaction_audit.py` (17)

### Phase 2 — PHASE 57 (✅ COMPLETADO): Datos y Enriquecimiento (Stats API + Savant batter)
**User stories (Phase 57 - Data)**
1. Como usuario, quiero props aun si Savant falla (fail-soft).
2. Como usuario, quiero saber si el prop fue generado con datos completos o parciales (`data_quality`).
3. Como sistema, quiero cache por jugador para evitar latencia y rate limits.

**Implementación (✅ COMPLETADA)**
- ✅ `backend/services/baseball_savant_batter.py`
  - Fetch de CSV Savant para bateadores, fail-soft
  - Cache (mem + Mongo opcional) TTL 24h
- ✅ `backend/services/mlb_player_props_discovery.py`
  - Motor determinístico Poisson + multiplicadores
  - Mercados: H+R+RBI, TB, Hits 1+, RBI 1+, Runs 1+
  - Moneyball filters: prob min, edge min, anti-longshot
  - `data_quality`: COMPLETE/PARTIAL/MINIMAL

### Phase 3 — PHASE 57 (✅ COMPLETADO): API (server.py)
**User stories (Phase 57 - API)**
1. Endpoint estable para consultar props por fecha.
2. Respuesta fail-soft.

**Implementación (✅ COMPLETADA)**
- ✅ Endpoint:
  - `GET /api/mlb/player-props?date=YYYY-MM-DD&use_savant=true&max_games=20`
  - Llama `compute_player_props_for_day()`
- ✅ Tests:
  - `backend/tests/test_mlb_player_props_discovery.py` (26)

### Phase 4 — PHASE 57: UI básica (⏸️ DIFERIDA)
**User stories (Phase 57 - UI)**
1. Tabla simple con props recomendados.
2. Filtros por juego/mercado.
3. Badges de `data_quality`.

**Estado**
- ⏸️ No implementado (por alcance). Siguiente paso sugerido:
  - `frontend/src/pages/MLBPlayerPropsPage.jsx` + route `/mlb/player-props`.

### Phase 5 — PHASE F57 (✅ COMPLETADO): Football Context + Trend Discovery Engine (observe-only)
**Submódulos implementados**
1. ✅ `services/football_news_context_ingestion.py`
   - Ingestión de noticias via Google News RSS
   - Reglas: opcional, timeout corto, cache 6h, fail-soft
   - Detección por frases clave (ES + fallback EN)
   - Transparencia: `source_url`, `source_name`, `queried_url`, `fetched_at`
2. ✅ `services/football_context_trend_discovery.py`
   - Squad Disruption Detector (score 0-100 + bucket)
   - Recent Form Streak Detector
   - Corners Trend Engine (prom últimos 10 vs últimos 5)
   - Protected Goals Trend Engine (Over 1.5 / Over 1.75 preferidos)
   - Missed Match Rescue
   - Output: `observe_only: True`, `recommended_markets`, `narrative_es`
3. ✅ Endpoint:
   - `GET /api/football/context-trend?home_team=X&away_team=Y&match_id=...&use_news=true|false&locale=es`
4. ✅ UI:
   - `frontend/src/components/FootballContextTrendCard.jsx`
   - Integrado en `frontend/src/pages/MatchDetailPage.jsx`
   - Self-gating: solo renderiza si hay señales reales
5. ✅ Tests:
   - `backend/tests/test_football_context_trend_discovery.py` (35)

### Phase 6 — Post-work: Cleanup técnico (✅ COMPLETADO)
- ✅ `mlb_day_orchestrator.py`
  - Reemplazo de referencias a `traffic_score_payload` por `pick_payload["traffic_score_obj"]`
  - Corrección de estilos E701/E702
  - Ruff sin errores blocking

### Phase 7 — PHASE 58 (✅ COMPLETADO): Symmetry + Hierarchical SOT + UI conflict badge
**Backend**
1. ✅ `mlb_over_discovery.py`
   - Swap simétrico Over/Under con umbral 1.0/6.0
   - Telemetría `symmetric_swap_applied`
2. ✅ `mlb_under_fragility_calibrator.py`
   - Calibración de fragility “hidden Under routes” para picks Over
   - Guard de polaridad (no-op si no es Over)
3. ✅ `mlb_day_orchestrator.py`
   - Source of Truth jerárquico:
     - NB canoniza mean y probs
     - Calibradores ajustan fragility
     - Patrones ajustan confianza
   - `mlb_source_of_truth` + `under_fragility_calibration`
4. ✅ Penalización simétrica por contradicción de patrones
   - Campos: `pick_conflict_state`, `pattern_penalty_applied`, `confidence_pre_pattern_penalty`
   - Ajuste de `recommendation.confidence_score`
   - `pattern_alignment` root-normalizado
5. ✅ Tests
   - `backend/tests/test_cambio4_pattern_contradiction_penalty.py` (10)

**Frontend**
1. ✅ `frontend/src/components/ConfidenceBadge.jsx` (reutilizable)
2. ✅ Integración en `MatchDetailPage.jsx`
3. ✅ Verificación visual vía screenshot tool (4 estados) + eliminación de página debug temporal


## 3) Next Actions

### Próximos pasos recomendados (prioridad)
1) **Completar adopción del ConfidenceBadge (UI)**
   - Integrar `ConfidenceBadge` en:
     - `frontend/src/components/MatchCard.jsx` (listado / dashboard)
     - Panel histórico (HistoricalProfilePanel o equivalente)
   - Asegurar que se pasen las props mínimas (confianza final + estado de conflicto + pre-penalty + payload de penalización).

2) **Observabilidad en producción / logs reales**
   - Monitorear frecuencia real de:
     - `pick_conflict_state`
     - `pattern_penalty_applied.ratio` y distribución de penalties
   - Validar que el ajuste de confianza no cause confusión (ej. picks con score alto pero “VALUE_CON_CONFLICTO”).

3) **Tests de UI (RTL) para el badge**
   - Añadir tests de React Testing Library para:
     - Render de `VALUE_REVISAR` vs `VALUE_CON_CONFLICTO`
     - Tooltip con breakdown
     - Tachado del `confidencePrePenalty`

4) **MLB Phase 57 UI (pendiente)**
   - Implementar `MLBPlayerPropsPage.jsx` + ruta `/mlb/player-props`

5) **Football F57 integración híbrida con orchestrator (opcional)**
   - Agregar flag para inyectar el bloque `context_trend` dentro del output del engine principal de football, manteniendo observe-only.


## 4) Success Criteria

### Phase 56 (✅)
- ✅ Script genera JSON + resumen stdout.
- ✅ Telemetría per-pick + pipeline_meta agregada.
- ✅ Observe-only verificado.

### Phase 57 (✅ Backend)
- ✅ Endpoint `/api/mlb/player-props` devuelve props Moneyball por fecha, fail-soft.
- ✅ Savant batter enrichment opcional con cache + timeout, sin bloquear generación.
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

### Calidad / regresiones
- ✅ `pytest tests/` → **1649/1649 passed**
- ✅ Verificación visual: badge en 4 estados validado via screenshot tool

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
