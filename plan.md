# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration (plan.md)

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
- ✅ Persistencia extendida en `football_market_results` para calibración sin recomputar:
  - `league_tier`, `offense_bucket`, `lambda_total/lam_h/lam_a`, `dc_rho_used`, `goals_dispersion_ratio`, `p_under_*_poisson`, `p_under_*_dc_nb`, `dc_nb_delta_*_pts`.

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

**Estado tests (post Football DC/NB):** ✅ `pytest` 1004 passing.

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
(Se mantiene el plan existente; no es el foco inmediato de Football Moneyball / DC-NB.)

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
- **Índices sugeridos por el usuario:** implementados best-effort (**16 índices** creados en startup).
- **Importante:** pattern memory NO fuerza picks automáticamente.
- **Reutilizar módulos existentes:** `football_quality.py`, `football_market_trace.py`, `football_live_aggregator.py`, `football_corner_pregame.py`, etc.

### Phase 8 — Football Moneyball (Backend) — Scaffolding + DB Warehouse ✅
**Entregado:** `services/football_moneyball/` con módulos:
- ✅ `football_intelligence_warehouse.py` (IO Mongo: perfiles, snapshots, results, pattern memory)
- ✅ `ensure_football_indexes(db)` llamado en `server.py` startup
- ✅ Esquema fail-soft + writes idempotentes (replace_one upsert)

### Phase 9 — Football Goal Pressure Profile ✅
- ✅ Implementado `football_goal_pressure_profile.py` (pure, determinista, explicable)
- ✅ Señales: under rates, btts, clean sheets, gf/ga avg, early goal profile; live override con tiros a puerta.

### Phase 10 — Snapshot Builder (Pregame + Live) ✅
- ✅ Implementado `football_snapshot_builder.py` (pure)
- ✅ Persistencia idempotente en `football_match_intelligence_snapshots`.

### Phase 11 — Pattern Memory (derive + lookup + conservative adjustment) ✅
- ✅ Implementado `football_pattern_memory.py` (derive keys conservador)
- ✅ Lookup + attach en warehouse con gates (n<20 sin ajuste; 20≤n<50 moderado; n≥50 fuerte con ROI>0; caps conservadores).

### Phase 12 — Football Market Selection Layer ✅
- ✅ Implementado `football_market_selection.py` (pure)
- ✅ Política conservadora: preferencia por mercados protegidos (ej: Under 3.5 como protección), evita over/agresivos por defecto.

### Phase 13 — Feedback Loop + Market Results ✅
- ✅ Implementado `football_feedback_loop.py` + persistencia `persist_football_market_result`.
- ✅ Actualiza `football_pattern_memory` (sample_size, wins, hit_rate, roi, ledger, best_market).

### Phase 14 — Live vs Pregame Comparison ✅
- ✅ Extendido `live_reevaluation.py` con `football_live_vs_pregame` (KEEP/REDUCE/AVOID) fail-soft.

### Phase 15 — Endpoint `/api/football/pattern-memory/summary` ✅
- ✅ Implementado `GET /api/football/pattern-memory/summary` (fail-soft HTTP 200).

### Phase 15a-FM — Integración en `analyst_engine.py` ✅
- ✅ Integrado en Phase 12a-FM (gated por `sport == 'football'`).
- ✅ Adjunta: `goal_pressure_profile`, `football_pregame_snapshot`, `football_pattern_keys`, `historical_pattern_match`, `market_selection`.

### Phase 16 — Tests (Football Moneyball Backend) ✅
- ✅ `tests/test_football_moneyball.py` (36 tests).
- ✅ Testing agent verificó backend 100% verde.

---

## 5) UI mínima viable (Football Moneyball) ✅ COMPLETADA

### Phase 17 — Frontend: MatchCard panels ✅
- ✅ UI integrada en `MatchCard.jsx` (gated por `sport === 'football'`).
- ✅ Paneles:
  - `FootballIntelligencePanel`
  - `FootballPatternMemoryPanel`
  - `FootballLiveVsPregamePanel`

### Phase 18 — Frontend: consumo endpoint summary (Opcional / pendiente)
- (Opcional) Consumir `GET /api/football/pattern-memory/summary` para vista agregada.

### Phase 19 — Tests frontend (RTL) (Pendiente)
- (Pendiente) Tests RTL para render condicional de paneles football y no-regresión en MLB/Basketball.

---

## 6) Football Totals Calibration — Dixon-Coles + NB Conditional (P0) ✅ COMPLETADO

### Context
El modelo anterior colapsaba el marcador a `lambda_total` y aplicaba Poisson al total, destruyendo la estructura de marcador conjunto necesaria para Dixon–Coles. Se corrigió implementando matriz bivariada + tau DC + NB condicional por lado.

### Phase 20 — `statsbomb_features.py`: DC matrix + NB conditional ✅
- ✅ Añadido:
  - `DIXON_COLES_RHO_DEFAULT = -0.05`
  - clamps asimétricos: `_DC_RHO_MIN=-0.20`, `_DC_RHO_MAX=0.0`
  - `build_score_matrix()` con renormalización
  - `under_prob_from_matrix()` (Under 2.5/3.5 por suma de celdas)
  - `FOOTBALL_GOALS_DISPERSION_RATIO_DEFAULT = 1.0`
  - `build_score_matrix_nb()` (NB conditional + DC)
  - `derive_offense_bucket()` con umbrales 2.25/2.85
- ✅ Garantía clave: `dispersion_ratio==1.0` → NB **inert** (equivale a Poisson por lado).

### Phase 21 — Integración `compute_match_features` ✅
- ✅ Reemplazo del cálculo `poisson_total_under(lam_total, line)` por:
  - `score_matrix = build_score_matrix_nb(lam_h, lam_a, rho, ratio)`
  - `p_under_25/35 = under_prob_from_matrix(score_matrix, line)`
- ✅ Telemetría para calibración y debugging:
  - `p_under_2_5_poisson`, `p_under_3_5_poisson`
  - `dc_nb_delta_2_5_pts`, `dc_nb_delta_3_5_pts`
  - `dc_rho_used`, `goals_dispersion_ratio`

### Phase 22 — `football_totals_calibration.py` ✅
- ✅ Implementado `compute_football_totals_calibration(db, days=90, user_id="_slate")`.
- ✅ Regla codificada (más conservadora):
  - **n global < 100 → defaults** (ρ=-0.05, ratio=1.0)
  - **n global ≥ 100 → aplicar empírico global** (con clamps)
  - buckets por liga/ofensa: **siempre OBSERVE_ONLY** hasta n≥100 propios
- ✅ Helper `apply_calibration_to_match(match, calibration)`.

### Phase 23 — Wiring en orquestador + endpoint ✅
- ✅ `analyst_engine.py` Phase 8.9:
  - carga summary 1 vez por run
  - propaga `match["_dc_rho"]` + `match["_goals_dispersion_ratio"]` a todos los matches football antes de `compute_match_features`.
- ✅ Endpoint fail-soft:
  - `GET /api/football/totals-calibration/summary?days=90`

### Phase 24 — Persistencia extendida en settle ✅
- ✅ `persist_football_market_result()` extendido para guardar:
  - buckets: `league_tier`, `offense_bucket`
  - lambdas: `lambda_total`, `lambda_home`, `lambda_away`
  - DC/NB: `dc_rho_used`, `goals_dispersion_ratio`
  - probs: `p_under_2_5_poisson`, `p_under_3_5_poisson`, `p_under_2_5_dc_nb`, `p_under_3_5_dc_nb`
  - deltas: `dc_nb_delta_2_5_pts`, `dc_nb_delta_3_5_pts`
- ✅ `record_football_pick_outcome()` deriva `offense_bucket` automáticamente si falta.

### Phase 25 — Backtest runner ✅
- ✅ `football_backtest_runner.py`:
  - descarga fixtures finalizados (API-Sports) por rango de fechas
  - ejecuta `compute_match_features` con defaults (ρ=-0.05, ratio=1.0)
  - persiste en `football_market_results` con cohort `user_id="_slate_backtest"`

### Phase 26 — Tests + Verificación ✅
- ✅ 25 tests nuevos `tests/test_football_dc_nb_calibration.py`.
- ✅ Suite total: **1004 tests passing**.
- ✅ Testing agent backend: 100% verde (endpoints + regresiones + matemática NB inert + clamps).

---

## 7) Next Actions (Actualizado)

### Inmediato
1. (Opcional) UI: añadir vista agregada en dashboard consumiendo:
   - `/api/football/pattern-memory/summary`
   - `/api/football/totals-calibration/summary`
2. Implementar/confirmar flujo de **settle football end-to-end** (si se desea exponer al usuario) para alimentar calibración/pattern memory sin intervención manual.
3. Ejecutar el backtest runner en rango controlado (si hay quota API-Sports) para acelerar `n` hacia 100 en cohort `_slate_backtest`.

### Posterior
4. Retomar Injury Intelligence Basketball (Phase 1) según el plan existente.
5. Frontend tests RTL para paneles football + no-regresión en MLB/Basketball.

---

## 8) Success Criteria (Actualizado)

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
- ✅ Market selection football prefiere mercados protegidos.
- ✅ Feedback loop actualiza `football_pattern_memory` tras settle.
- ✅ Live vs pregame comparison disponible y fail-soft.
- ✅ Endpoint `/api/football/pattern-memory/summary` funcional y fail-soft.

### Football Totals Calibration (DC + NB condicional) (cumplido)
- ✅ Totals calculados desde **matriz bivariada** con DC tau y NB condicional (no Poisson total simplificado).
- ✅ NB ratio=1.0 es **inert** (no distorsiona partido típico).
- ✅ Clamps conservadores (ρ≤0, ratio≥1) evitan invertir DC o forzar overs.
- ✅ Regla global-antes-de-bucket codificada: n<100 → defaults; buckets OBSERVE_ONLY.
- ✅ Wiring aplicado antes de `compute_match_features` en flujo football.
- ✅ Persistencia de telemetría completa para calibración sin recomputar.
- ✅ Endpoint `/api/football/totals-calibration/summary` funcional y fail-soft.
- ✅ Tests: `pytest` 1004 passing, sin regresiones (MLB/Basketball intactos).
