# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball (plan.md)

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

**Estado tests:** ✅ `pytest` 979 passing.

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
(Se mantiene el plan existente; no es el foco inmediato del épico Football Moneyball, ya completado.)

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
- **Índices sugeridos por el usuario:** implementados best-effort (16 índices creados en startup).
- **Importante:** pattern memory NO fuerza picks automáticamente.
- **Reutilizar módulos existentes:** `football_quality.py`, `football_market_trace.py`, `football_live_aggregator.py`, `football_corner_pregame.py`, etc.

### Phase 8 — Football Moneyball (Backend) — Scaffolding + DB Warehouse ✅
**Entregado:** `services/football_moneyball/` con módulos:
- ✅ `football_intelligence_warehouse.py` (IO Mongo: perfiles, snapshots, results, pattern memory)
- ✅ `ensure_football_indexes(db)` llamado en `server.py` startup
- ✅ Esquema fail-soft + writes idempotentes (replace_one upsert)

**Estado:**
- ✅ Índices creados en startup: `created=16 errors=0` (verificado en logs).

### Phase 9 — Football Goal Pressure Profile ✅
- ✅ Implementado `football_goal_pressure_profile.py` (pure, determinista, explicable)
- ✅ Señales: under rates, btts, clean sheets, gf/ga avg, early goal profile; live override con tiros a puerta.

### Phase 10 — Snapshot Builder (Pregame + Live) ✅
- ✅ Implementado `football_snapshot_builder.py` (pure) con:
  - `build_pregame_snapshot`
  - `build_live_snapshot`
  - `build_full_intelligence_snapshot`
- ✅ Persistencia idempotente en `football_match_intelligence_snapshots`.

### Phase 11 — Pattern Memory (derive + lookup + conservative adjustment) ✅
- ✅ Implementado `football_pattern_memory.py` (derive keys conservador)
- ✅ Lookup + attach en warehouse (`attach_pattern_match_to_payload`) con gates:
  - n<20 sin ajuste
  - 20≤n<50 ajuste moderado
  - n≥50 y roi>0 ajuste mayor (caps más conservadores que MLB)
- ✅ `enabled:false` bloquea ajustes.

### Phase 12 — Football Market Selection Layer ✅
- ✅ Implementado `football_market_selection.py` (pure)
- ✅ Política conservadora: preferencia por mercados protegidos (ej: Under 3.5 como protección), evita over/agresivos por defecto.
- ✅ No “forzar pick”: sugiere `protected_alternative`, y usa `watchlist/manual_odds` cuando corresponde.

### Phase 13 — Feedback Loop + Market Results ✅
- ✅ Implementado `football_feedback_loop.py` + persistencia `persist_football_market_result`.
- ✅ Actualiza `football_pattern_memory` (sample_size, wins, hit_rate, roi, ledger, best_market).

### Phase 14 — Live vs Pregame Comparison ✅
- ✅ Extendido `live_reevaluation.py`:
  - agrega `football_live_vs_pregame` al envelope
  - comparación `compare_live_vs_pregame` (KEEP/REDUCE/AVOID) fail-soft

### Phase 15 — Endpoint `/api/football/pattern-memory/summary` ✅
- ✅ Implementado `GET /api/football/pattern-memory/summary`.
- ✅ Fail-soft: si DB falla → `available:false` con HTTP 200.

### Phase 15a-FM — Integración en `analyst_engine.py` ✅
- ✅ Integrado en Phase 12a-FM (gated por `sport == 'football'`).
- ✅ Adjunta por entry:
  - `goal_pressure_profile`
  - `football_pregame_snapshot`
  - `football_pattern_keys`
  - `historical_pattern_match`
  - `market_selection`
- ✅ Persistencia de snapshot best-effort para picks/rescued (fail-soft).

### Phase 16 — Tests (Football Moneyball Backend) ✅
- ✅ Añadido `tests/test_football_moneyball.py` con 36 tests.
- ✅ Suite completa: `pytest` 979 passing (sin regresiones MLB/Basketball).
- ✅ Testing agent verificó backend 100% verde (logs + endpoint + suite).

---

## 5) UI mínima viable (Football Moneyball) ✅ COMPLETADA

### Phase 17 — Frontend: MatchCard panels ✅
- ✅ UI mínima viable integrada en `MatchCard.jsx` (gated por `sport === 'football'`).
- ✅ Paneles:
  - `FootballIntelligencePanel`
  - `FootballPatternMemoryPanel`
  - `FootballLiveVsPregamePanel`
- ✅ Implementados en: `FootballMoneyballPanels.jsx` (fail-soft: renderiza `null` si falta data).

### Phase 18 — Frontend: consumo endpoint summary (Opcional / pendiente)
- (Opcional) Consumir `GET /api/football/pattern-memory/summary` para una vista compacta agregada (dashboard/panel global).

### Phase 19 — Tests frontend (RTL) (Pendiente)
- (Pendiente) Tests RTL para render condicional de paneles football y no-regresión en MLB/Basketball.

---

## 6) Next Actions (Actualizado)

### Inmediato
1. (Opcional) Añadir **vista agregada** en UI consumiendo `/api/football/pattern-memory/summary`.
2. Implementar endpoint/flujo de **settle football** en UI/Backend (si se desea exponer al usuario) para alimentar el feedback loop de forma end-to-end.
3. Añadir tests RTL para los paneles (`FootballMoneyballPanels.jsx`) y asegurar no-regresión visual.

### Posterior
4. Retomar Injury Intelligence Basketball (Phase 1) según el plan existente.

---

## 7) Success Criteria (Actualizado)

### Moneyball MLB (ya cumplido)
- ✅ Market Selection decide el pick final.
- ✅ Missing odds → buckets manuales, no discard automático.
- ✅ Payload contract fail-soft; editorial confirmación; UI explicable; live sin contradicciones.

### Injury Intelligence Basketball (Phase 1)
- Injury Intelligence fail-soft: missing → `available:false` y no altera engine.
- Multi-source con provenance, conflictos conservadores.
- Roles NBA: superstars hardcoded + fallback heurístico.
- Ajustes conservadores con caps (±12 confidence, +15 fragility).
- HIGH/CRITICAL bloquea picks agresivos (sin forzar picks).
- Payload incluye `injury_intelligence` en basketball.
- UI muestra bajas y edge neto en <5s, no rompe picks viejos.
- Tests pasan: backend sin regresiones + nuevos tests injury.

### Football Moneyball Intelligence Layer + Pattern Memory (cumplido)
- ✅ Backend completo en `services/football_moneyball/` con arquitectura estilo MLB.
- ✅ 4 colecciones Mongo nuevas + índices best-effort (16 creados).
- ✅ Snapshots pregame/live persistidos idempotentemente.
- ✅ Goal pressure profile operativo con datos parciales y explicable.
- ✅ Pattern memory conservadora: no fuerza picks; solo ajusta levemente + sugiere protección.
- ✅ Market selection football prefiere mercados protegidos (ej: Under 3.5 sobre Under 2.5 cuando hay volatilidad).
- ✅ Feedback loop actualiza `football_pattern_memory` tras settle.
- ✅ Live vs pregame comparison disponible y fail-soft.
- ✅ Endpoint `/api/football/pattern-memory/summary` funcional y fail-soft.
- ✅ Tests: `pytest` 979 passing, sin regresiones (MLB/Basketball intactos).
