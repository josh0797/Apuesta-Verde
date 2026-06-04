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

## 7) Live Recommendation History / Timeline (P0) ✅ COMPLETADO

### Context
Se requiere un sistema persistente para registrar eventos de recomendación (auto y manual) durante el live:
- Debe crear una **línea de tiempo** por partido.
- Debe permitir **settlement MVP** para BTTS y Totals.
- Debe permitir que el usuario **confirme/cambie** posteriormente si una recomendación fue acertada (sin perder el histórico).
- Debe ser **fail-soft**: si DB falla o falta match doc, nunca debe romper el engine live ni devolver 500.

### Decisiones confirmadas (del usuario) — entregadas
- ✅ Esquema: **mínimo** + extensiones (`match_label`, `league`, `reason`, `reason_codes`, `outcome`, `notes`, `superseded_by_event_id`, `source`, `event_type`, `status`).
- ✅ Auto-save: **solo cambios reales**, con deduplicación por `(user_id|sport|match_id|minute|score|market|selection)`.
- ✅ Auto-settle MVP: BTTS YES/NO + Over/Under 0.5/1.5/2.5/3.5 (función pura `settle_live_event_from_score`).
- ✅ Endpoints: `POST /api/live/recommendation-events/manual` + `GET /api/live/recommendation-events` con filtros completos.
- ✅ Regla preservación HIT: si una rec previa es `hit` y luego cambia, el evento original **permanece `hit`** y solo agrega `superseded_by_event_id` (sin tocar el status).
- ✅ Manual entry: permite guardar aunque **no exista `match_id` real** (usa `match_label`).

---

### Phase 27 — Backend: Colección + Servicio + Auto-save ✅
- ✅ Colección `live_recommendation_events` + 5 índices best-effort en startup.
- ✅ `services/live_recommendation_history.py`:
  - `ensure_live_recommendation_indexes(db)`
  - `settle_live_event_from_score(event, score, minute, match_ended)` (puro)
  - `persist_live_recommendation_event(...)` (engine autosave + dedupe + supersede)
  - `record_manual_live_event(...)` (manual backfill sin match doc real)
  - `settle_live_recommendation_event(...)` (override manual)
  - `query_live_recommendation_events(...)` (filtros completos + sort condicional)
  - `link_supersede_only(...)` (link superseded_by_event_id sin cambiar status)
- ✅ Autosave en `server.py` `/live/reevaluate` (football-only).

### Phase 28 — Backend: Endpoints ✅
- ✅ `POST /api/live/recommendation-events/manual` (Pydantic body, fail-soft, devuelve 422 ante payload mal formado).
- ✅ `GET /api/live/recommendation-events` con filtros:
  - `match_id`, `sport`, `status`, `result`, `source`, `event_type`, `settled`, `date_from`, `date_to`, `limit`.
  - Defaults: `sport=football`, `limit=50` clamp [1, 200].
  - Sorting: `(minute asc, created_at asc)` si viene match_id; `(created_at desc)` en caso contrario.
  - `auto_settle=true` (default) re-evalúa eventos abiertos contra el score actual del partido.

### Phase 29 — Settlement MVP ✅
- ✅ Implementado para: BTTS YES, BTTS NO, Over 0.5/1.5/2.5/3.5/4.5/5.5, Under 0.5/1.5/2.5/3.5/4.5/5.5.
- ✅ Hit cuando el mercado se cumple; miss si no se cumple al cierre del partido; pending en cualquier otro caso.
- ✅ Preservación HIT: nuevos eventos no degradan a `miss` un evento previo ya en `hit`.

### Phase 30 — Backfill France vs Ivory Coast ✅
- ✅ Lookup en DB encontró match real: `match_id=1536931, France vs Ivory Coast (Friendlies)`.
- ✅ Backfill manual realizado: BTTS YES @ minuto 42, score 1-0, outcome=hit settled@53 1-1.
- ✅ Event ID resultante: `b85a5144-75ea-4cd1-92b4-163c0516898a`.

### Phase 31 — Frontend: Timeline + Manual Entry Form ✅
- ✅ `LiveRecommendationTimeline.jsx`:
  - Lista por match_id ordenada cronológicamente.
  - Status badges (HIT/MISS/OPEN/MANUAL/SUPERSEDED/VOID).
  - Source chips (engine/manual), reason codes, outcome info.
  - Form inline para registro manual + backfill (validación mínima).
  - Usa `api` axios + token JWT correcto (`vbi_token`).
- ✅ Integrado en `MatchCard.jsx` gated por `sport === 'football'`.

### Phase 32 — Tests + Verificación ✅
- ✅ 18 tests `tests/test_live_recommendation_history.py` (pytest local: 100% verde).
- ✅ Suite backend total: **1043 tests passing** (sin regresiones, +18 desde 1025).
- ✅ Testing agent backend: **30/30 tests passed (100%)** (`/app/test_reports/iteration_63.json`).
- ✅ No regresiones en endpoints existentes (`/api/picks/today`, `/api/live/reevaluate`, `/api/football/*`).


## 8) Next Actions (Actualizado)

### Inmediato
- ✅ Live Recommendation History / Timeline (Phase 27–32) completado.

### Posterior (P1/P2)
1. Tests frontend RTL para paneles football + timeline + no-regresión.
2. (Opcional) Extender settlement a más mercados (córners/handicap) si se desea.
3. Retomar Injury Intelligence Basketball (Phase 1) según el plan existente.

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
