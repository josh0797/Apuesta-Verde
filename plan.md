# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

### Objetivos en curso (Injury Intelligence Layer)
- Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
- Arquitectura: **fail-soft**, multi-source, cache-aware, sport-specific, explicable, conservadora.
- Entregar un bloque `injury_intelligence` en el payload que ajuste (conservadoramente) confidence/fragility/market warnings **sin forzar picks**.
- UI: `InjuryIntelligencePanel` para football/basketball (no MLB por ahora) mostrando bajas clave, severidad, impacto y freshness.

### Objetivos completados (Football Moneyball Intelligence Layer + Pattern Memory)
- ✅ Convertir el motor de fútbol de “análisis por partido” a un sistema tipo **Moneyball histórico** con:
  - snapshots pregame/live
  - perfiles diarios por equipo (cache)
  - pattern memory conservadora
  - selección de mercado protegida y feedback post-settle
- ✅ Replicar **fielmente la arquitectura MLB** (warehouse + pressure/profile + snapshot + pattern memory + market selection + feedback), pero con señales **football-specific**.
- ✅ **Fail-soft estricto**: si falla DB o faltan señales → fallback a análisis base actual (sin romper picks ni UI).
- ✅ No tocar ni romper MLB ni Basketball (código aislado por módulos y gating por `sport`).
- ✅ UI mínima viable en MatchCard para football: paneles de inteligencia, pattern memory, y live vs pregame.

### Objetivos completados (Football Totals Calibration: Dixon-Coles + NB condicional)
- ✅ Corregir la matemática de totales football (evitar colapsar a `lambda_total` Poisson) implementando:
  - **Matriz bivariada** `P(home=i, away=j)` truncada por lado y renormalizada.
  - **Dixon–Coles tau(i,j)** aplicado a (0,0), (1,0), (0,1), (1,1) con clamp asimétrico ρ∈[-0.20, 0.0].
  - **NB condicional** como modulador de dispersión por lado (marginal widening) con ratio clamp [1.0, 2.0] e **inert** por defecto (ratio=1.0).
- ✅ Integración en `compute_match_features` con telemetría completa:
  - `dc_rho_used`, `goals_dispersion_ratio`
  - `p_under_2_5_poisson`, `p_under_3_5_poisson`
  - `dc_nb_delta_2_5_pts`, `dc_nb_delta_3_5_pts`
- ✅ Feedback loop de calibración tipo MLB pero football-correcto:
  - `football_totals_calibration.py` con regla **global-antes-de-bucket**
  - **n global < 100 → defaults** (ρ=-0.05, ratio=1.0)
  - buckets siempre **OBSERVE_ONLY** hasta n≥100 propios (sin aplicar por ahora)
- ✅ Wiring en orquestador (analyst_engine Phase 8.9): load 1 vez por run y propagar `match["_dc_rho"]` + `match["_goals_dispersion_ratio"]` antes de cualquier `compute_match_features`.
- ✅ Endpoint público fail-soft: `GET /api/football/totals-calibration/summary?days=90`.
- ✅ Backtest runner: `football_backtest_runner.py` (cohort `_slate_backtest`) para poblar `football_market_results` sin contaminar el cohort live.
- ✅ Persistencia extendida en `football_market_results` para calibración sin recomputar.

### Objetivos completados (UI Football DC/NB + Over Support)
- ✅ Crear `FootballDcNbPanels.jsx` con:
  - `FootballTotalsModelPanel` (Poisson vs DC/NB, ρ, NB ratio, deltas, modo defaults/empirical)
  - `FootballOverSupportPanel` (soportes Over 1.5/2.5, presión 0–30, fragilidad)
- ✅ Integración en `MatchCard.jsx` (gated por `sport === 'football'`, fail-soft por `available`).

### Objetivo nuevo (P0): Live Recommendation History / Timeline
- Implementar un historial/auditoría de **recomendaciones live** que:
  - guarde eventos (auto + manual) **solo cuando cambie realmente la recomendación** (dedupe)
  - permita settlement MVP (BTTS y Totals) y/o confirmación/edición manual posterior
  - exponga endpoints de consulta con filtros completos
  - muestre un timeline en UI + formulario de entrada manual
  - sea **fail-soft**: nunca rompe el motor live si falla DB

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC (aislado, obligatorio) ✅ COMPLETADO
**Core probado:** “Game → pipeline Moneyball → `market_selection` final → payload persistible + live/pregame linkage por `game_pk` (fail-soft).”

Entregables:
- ✅ Contrato de payload Moneyball sellado.
- ✅ Warehouse/source status agregados.

---

### Phase 2 — V1 Backend Development (Moneyball alignment) ✅ COMPLETADO

#### 2.1 `mlb_day_orchestrator.py` (refactor de orden y responsabilidades) ✅
- ✅ `market_selection` como capa final.
- ✅ Buckets `structural_lean_requires_odds` / `watchlist_manual_odds`.
- ✅ Payload contract + `pipeline_meta.external_sources`.

#### 2.2 `mlb_run_evaluations_summary.py` (Moneyball breakdowns) ✅
- ✅ Nuevos breakdowns + `summary_schema_version=moneyball.1`.

#### 2.3 `editorial_context_service.py` (contexto, no motor) ✅
- ✅ `p4-moneyball-context.1` + anotación vs Moneyball.

#### 2.4 `editorial_signal_mapper.py` (vocabulario y `sport_hint`) ✅
- ✅ MLB/NBA vocab + sport discrimination + neutralización de motivación MLB.

---

### Phase 3 — V1 Frontend Development (UI Moneyball) ✅ COMPLETADO

#### 3.1 MatchCard + Panels (explicabilidad) ✅
- ✅ Secciones/paneles Moneyball (Market Selection, Ghost Edges, Fragility/Survival, Pattern Memory, Manual Odds Review, etc.).

#### 3.2 Dashboard buckets + empty states + manual odds ✅
- ✅ Buckets separados (structural lean vs watchlist) + copy MLB.

#### 3.3 Live Analysis (live vs pregame por `game_pk`) ✅
- ✅ Warning hits-pressure, filtro de líneas ya superadas.

---

### Phase 4 — Comprehensive Testing & Regression ✅ COMPLETADO
- ✅ Suite backend sin regresiones.
- ✅ Nuevos tests Moneyball + live polish.

**Estado tests (actual):** ✅ `pytest` **1025 passing** (backend 100% verde).

---

## 3) Injury Intelligence Layer — Basketball (Phase 1) (EN CURSO)

## Context
El usuario solicita Injury Intelligence para basketball y football. Alcance confirmado:
- Phase 1 = Basketball backend + UI
- Phase 2 = Football

Fuentes disponibles:
- **API-Sports + Bright Data scraping (ESPN/Rotowire/Transfermarkt) + TheStatsAPI**

Estrategia roles:
- Lista hardcodeada de superstars/estrellas por equipo + fallback heurístico (minutos/usage/ppg si player-stats existen)

### Phase 5 — Injury Intelligence (Basketball) — Backend (NEW)
(Se mantiene el plan existente; no es el foco inmediato de Football Moneyball / DC-NB / Timeline.)

### Phase 6 — Injury Intelligence (Basketball) — Frontend/UI (NEW)

### Phase 7 — Tests (Basketball Injury Intelligence) (NEW)

---

## 4) Football Moneyball Intelligence Layer + Pattern Memory (P0) ✅ COMPLETADO

### Context
Épico confirmado y entregado:
- **Orden:** backend completo primero y al final UI mínima viable.
- **Colecciones Mongo (NUEVAS, confirmadas):**
  - `football_team_daily_profiles`
  - `football_match_intelligence_snapshots`
  - `football_market_results`
  - `football_pattern_memory`
- **Importante:** pattern memory NO fuerza picks automáticamente.

(Phases 8–16 completadas; ver historial en el plan previo.)

---

## 5) UI mínima viable (Football Moneyball + DC/NB + Over Support) ✅ COMPLETADA

### Phase 17 — Frontend: MatchCard panels ✅
- ✅ UI integrada en `MatchCard.jsx` (gated por `sport === 'football'`).
- ✅ Paneles Moneyball:
  - `FootballIntelligencePanel`
  - `FootballPatternMemoryPanel`
  - `FootballLiveVsPregamePanel`
- ✅ Paneles Totals/Over Support:
  - `FootballTotalsModelPanel`
  - `FootballOverSupportPanel`

### Phase 18 — Frontend: consumo endpoint summary (Opcional / pendiente)
- (Opcional) Consumir:
  - `GET /api/football/pattern-memory/summary`
  - `GET /api/football/totals-calibration/summary`

### Phase 19 — Tests frontend (RTL) (Pendiente)
- (Pendiente) Tests RTL para render condicional de paneles football y no-regresión en MLB/Basketball.

---

## 6) Football Totals Calibration — Dixon-Coles + NB Conditional (P0) ✅ COMPLETADO

(Phases 20–26 completadas; ver historial en el plan previo.)

---

## 7) Live Recommendation History / Timeline (P0) (NUEVO)

### Context
Se requiere un sistema persistente para registrar eventos de recomendación (auto y manual) durante el live:
- Debe crear una **línea de tiempo** por partido.
- Debe permitir **settlement MVP** para BTTS y Totals.
- Debe permitir que el usuario **confirme/cambie** posteriormente si una recomendación fue acertada (sin perder el histórico).
- Debe ser **fail-soft**: si DB falla o falta match doc, nunca debe romper el engine live ni devolver 500.

### Decisiones confirmadas (del usuario)
- ✅ Esquema: **mínimo** + extensiones (ver abajo).
- ✅ Auto-save: **solo cambios reales**, con deduplicación (no guardar recomendaciones idénticas).
- ✅ Auto-settle MVP: solo mercados BTTS/Totals (lista específica).
- ✅ Endpoints: `POST manual` + `GET` con filtros completos + defaults de orden/limit.
- ✅ Regla clave: si una recomendación cambia **después** de haberse cumplido, **la anterior sigue siendo `hit`** y puede registrar `superseded_by_event_id`.
- ✅ Manual entry: permitir guardar aunque **no exista `match_id` real** (usar `match_label`).

---

### Phase 27 — Backend: Colección + Servicio + Auto-save (NEW)

#### 27.1 MongoDB: nueva colección `live_recommendation_events`
- Crear colección y `ensure_live_recommendation_indexes(db)` (best-effort en startup).
- Índices (propuestos):
  - `{ match_id: 1, sport: 1, created_at: 1 }`
  - `{ match_id: 1, minute: 1, created_at: 1 }` (timeline)
  - `{ sport: 1, status: 1, created_at: -1 }`
  - `{ sport: 1, source: 1, created_at: -1 }`
  - `{ sport: 1, event_type: 1, created_at: -1 }`
  - `{ settled: 1, created_at: -1 }`
  - (Opcional) unique parcial para dedupe (si aplica):
    - clave lógica: `dedupe_key` = hash de `(match_id|match_label, sport, state/event_type, recommendation.market, recommendation.selection)`

#### 27.2 Esquema de documento (mínimo + extensible)
- Campos mínimos (MVP):
  - `event_id` (uuid)
  - `sport` (default: `football` si falta)
  - `match_id` (string; puede ser manual)
  - `match_label` (string; requerido si no existe match real)
  - `league` (string opcional)
  - `minute` (int opcional)
  - `score` `{ home, away, label }` (opcional)
  - `recommendation` `{ title, market, selection, confidence, risk_level, recommended_action }`
  - `reason` (string opcional)
  - `reason_codes` (array opcional)
  - `status` (enum: `open`, `hit`, `miss`, `push`, `void`, `watchlist`, `superseded`)
  - `source` (enum: `engine`, `manual`, `system`) — manual por `POST`, engine por autosave
  - `event_type` (enum: `PREGAME`, `LIVE_REEVALUATED`, `MANUAL_ENTRY`, `SETTLED`) (o equivalente)
  - `settled` (bool)
  - `outcome` (obj opcional):
    - `result` (`hit`/`miss`/`push`/`void`)
    - `settled_minute` (int)
    - `settled_score` (string)
    - `settlement_reason` (string)
  - `superseded_by_event_id` (string uuid opcional)
  - `notes` (string opcional)
  - `created_at`, `updated_at`

#### 27.3 Servicio `services/live_recommendation_history.py`
- Funciones:
  - `create_manual_event(db, payload)` (valida mínimo; fail-soft)
  - `list_events(db, filters)` (filtros completos; sorting por reglas)
  - `maybe_append_engine_event(db, match, recommendation, state, minute, score_snapshot)`
    - **dedupe**: no insertar si el evento anterior (para el mismo match) tiene mismo `market+selection+event_type/state` (o mismo `dedupe_key`).
  - `settle_live_event_from_score(event, score, minute, is_final=False)`
    - retorna `{settled, status/result, outcome}` sin excepción
  - `apply_manual_override(event_id, new_status/outcome/notes)` (permitir “confirm/change” posterior)

#### 27.4 Integración autosave (engine)
- En `live_reevaluation.py` / `analyst_engine.py`:
  - Al computar recomendación live, llamar `maybe_append_engine_event(...)`.
  - Guardar solo cuando:
    - cambia `recommendation.market` o `recommendation.selection` o `event_type/state`.
    - (Opcional) cambio de `recommended_action` relevante.
- Fail-soft:
  - cualquier fallo de DB → log warning y continuar (sin afectar picks).

---

### Phase 28 — Backend: Endpoints (NEW)

#### 28.1 `POST /api/live/recommendation-events/manual`
- Permite registrar evento manual incluso si no existe match doc.
- Payload mínimo válido (confirmado):
  - `sport` (default football si falta)
  - `match_id`
  - `match_label` (obligatorio si match_id no existe en DB; en práctica lo aceptamos siempre)
  - `league` (opcional)
  - `minute` (opcional)
  - `score` (opcional)
  - `recommendation` (obligatorio)
  - `reason`/`reason_codes` (opcionales)
  - `outcome` (opcional: permite backfill ya settled)
  - `notes` (opcional)
- Respuesta: doc insertado + `event_id`.

#### 28.2 `GET /api/live/recommendation-events`
- Filtros completos:
  - `match_id`, `sport`, `status`, `result`, `source`, `event_type`, `settled=true|false`, `date_from`, `date_to`, `limit`.
- Defaults:
  - `sport="football"` si no viene
  - `limit=50`
  - Sorting:
    - si `match_id` viene: `minute asc`, luego `created_at asc`
    - si consulta general: `created_at desc`

---

### Phase 29 — Settlement MVP (BTTS + Totals) (NEW)
- Implementar settlement automático (cuando haya score/minute):
  - **BTTS YES**
  - **Over 0.5**, **Over 1.5**, **Over 2.5**, **Over 3.5**
  - **Under 2.5**, **Under 3.5**
- Reglas:
  - Si el mercado se cumple (ej BTTS YES cuando ambos marcan) → `status/result = hit` y `settled=true`.
  - Si cambia la recomendación posteriormente:
    - no convertir el evento anterior en `miss`
    - mantener `hit` y usar `superseded_by_event_id` si corresponde.

---

### Phase 30 — Backfill Francia vs Costa de Marfil + UI Manual Entry (NEW)

#### 30.1 Lookup match real (best-effort)
- Antes de usar placeholder, buscar match real en DB por:
  - `home_team.name ∈ ["France", "Francia"]`
  - `away_team.name ∈ ["Ivory Coast", "Costa de Marfil"]`
  - `league` contiene `"Friendlies"` o `"Amistosos"`
  - fecha `2026-06-04`
  - incluir current/live/archived
- Si existe → usar su `match_id`.
- Si no → usar `match_id = "manual-france-ivory-coast-2026-06-04"`.

#### 30.2 Backfill manual inicial
- Insertar (vía endpoint o servicio) el evento manual con:
  - minute estimado `42` (editable luego en UI)
  - score `1-0`
  - recommendation BTTS YES
  - outcome: `hit`, settled_minute `53`, settled_score `1-1`
  - notes: referencia a marcador final observado `1-2` y que el hit fue antes del empate

---

### Phase 31 — Frontend: Timeline UI + Form (P1) (NEW)
- Crear `LiveRecommendationTimeline.jsx`:
  - lista por match_id, orden por `minute asc, created_at asc`
  - chips: status, source, event_type
  - mostrar recommendation + score + reason_codes
  - mostrar outcome (hit/miss/push/void) y settlement info
  - permitir “confirm/change” (manual override) si se habilita endpoint PATCH/POST adicional
- Crear formulario `ManualRecommendationEventForm`:
  - validaciones mínimas
  - permite match_id + match_label (no bloquea si no hay match real)
  - permite outcome opcional (backfill settled)
- Integración en vista live match (MatchCard o modal) sin afectar otros deportes.

---

### Phase 32 — Tests + Verificación (NEW)
- Backend tests (`pytest`):
  - inserción manual mínima válida
  - dedupe: no duplica por market+selection+state
  - list con filtros + orden
  - settle BTTS YES y totals
  - supersede: hit permanece hit aunque luego haya otro evento
  - fail-soft: DB errors no rompen endpoints ni engine (200 con vacío o logging)
- Frontend:
  - render timeline vacío vs con data
  - submit manual ok
  - no-regresión en MatchCard para otros sports

---

## 8) Next Actions (Actualizado)

### Inmediato (P0)
1. Implementar Phase 27–29 (colección + servicio + endpoints + settlement MVP).
2. Añadir backfill inicial Francia vs Costa de Marfil (Phase 30) + habilitar edición posterior desde UI.
3. Integrar UI timeline + form (Phase 31).

### Posterior (P1/P2)
4. Tests frontend RTL para paneles football + timeline + no-regresión.
5. (Opcional) Extender settlement a más mercados (córners/handicap) si se desea.
6. Retomar Injury Intelligence Basketball (Phase 1) según el plan existente.

---

## 9) Success Criteria (Actualizado)

### Moneyball MLB (ya cumplido)
- ✅ Market Selection decide el pick final.
- ✅ Missing odds → buckets manuales, no discard automático.
- ✅ Payload contract fail-soft; editorial confirmación; UI explicable; live sin contradicciones.

### Football Moneyball + DC/NB + Over Support (cumplido)
- ✅ Moneyball football con warehouse/snapshots/pattern memory/market selection + UI.
- ✅ Totals DC/NB (matriz bivariada + tau + NB condicional) + calibración global-antes-de-bucket.
- ✅ UI DC/NB + Over Support integrada en MatchCard, fail-soft.

### Live Recommendation History / Timeline (nuevo)
- Guardado de eventos:
  - ✅ Manual: permite registrar aunque no exista match doc (usa `match_label`).
  - ✅ Auto: se guardan solo **cambios reales** (dedupe), no spam.
- Consulta:
  - ✅ `GET /api/live/recommendation-events` con filtros completos, defaults y orden correctos.
- Settlement MVP:
  - ✅ BTTS YES + Over/Under especificados se auto-settlean cuando aplica.
  - ✅ Si una recomendación se cumple y luego cambia, el evento original permanece `hit` (puede tener `superseded_by_event_id`).
- UI:
  - ✅ Timeline visible por partido + formulario de entrada manual + posibilidad de ajustar minute/outcome.
- Robustez:
  - ✅ Fail-soft total: fallos de DB no rompen engine live ni tiran 500; retornos conservadores.
