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

### Objetivos nuevos (Football Moneyball Intelligence Layer + Pattern Memory)
- Convertir el motor de fútbol de “análisis por partido” a un sistema tipo **Moneyball histórico** con:
  - snapshots pregame/live
  - perfiles diarios por equipo (cache)
  - pattern memory conservadora
  - selección de mercado protegida y feedback post-settle
- Replicar **fielmente la arquitectura MLB** (warehouse + pressure/profile + snapshot + pattern memory + market selection + feedback), pero con señales **football-specific**.
- **Fail-soft estricto**: si falla DB o faltan señales → fallback a análisis base actual (sin romper picks ni UI).
- No tocar ni romper MLB ni Basketball (código aislado por módulos y gating por `sport`).

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

**Estado tests:** ✅ `pytest` 940+ passing (último estado reportado por el usuario: >940).

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
(Se mantiene el plan existente; no es el foco inmediato del nuevo épico de Football Moneyball.)

### Phase 6 — Injury Intelligence (Basketball) — Frontend/UI (NEW)

### Phase 7 — Tests (Basketball Injury Intelligence) (NEW)

---

## 4) NEW: Football Moneyball Intelligence Layer + Pattern Memory (P0)

### Context
Nuevo épico confirmado por el usuario:
- **Orden:** backend completo primero (warehouse + snapshots + pattern memory + market selection + feedback) y al final UI.
- **MVP:** todo el backend completo + UI mínima viable.
- **Colecciones Mongo (NUEVAS, confirmadas):**
  - `football_team_daily_profiles`
  - `football_match_intelligence_snapshots`
  - `football_market_results`
  - `football_pattern_memory`
- **Importante:** pattern memory NO debe forzar picks automáticamente; solo memoria histórica conservadora.
- **Reutilizar módulos existentes:** `football_quality.py`, `football_market_trace.py`, `football_live_aggregator.py`, `football_corner_pregame.py`, etc.
- **Arquitectura a replicar:** MLB (warehouse + pressure base + market selection + feedback loop), adaptado a football.

### Phase 8 — Football Moneyball (Backend) — Scaffolding + DB Warehouse (NEW)
#### 8.1 Crear package `services/football_moneyball/`
Módulos (mínimo):
- `__init__.py`
- `football_intelligence_warehouse.py` (IO Mongo: perfiles, snapshots, results, pattern memory)
- `football_goal_pressure_profile.py` (equivalente a `mlb_pressure_base.py`, pure)
- `football_snapshot_builder.py` (construye digest pregame/live para persistir)
- `football_pattern_memory.py` (derive keys + lookup + update; conservador)
- `football_market_selection.py` (pure; capa final de selección protegida)
- `football_feedback_loop.py` (persist results post-settle + update pattern memory)
- `football_pattern_matcher.py` (match patterns → summary para payload/UI)

#### 8.2 Índices Mongo (obligatorio)
Crear helper de inicialización (best-effort) llamado en startup o lazy:
- `football_team_daily_profiles`:
  - index `{team_id, day}` único o compuesto
  - index `{team_name, day}`
- `football_match_intelligence_snapshots`:
  - index `{match_id, day}`
  - index `{league}`
  - index `{selected_market}`
- `football_market_results`:
  - index `{user_id, match_id}`
  - index `{match_id, market}`
  - index `{settled_at}`
  - index `{result}`
  - index `{pattern_keys}`
- `football_pattern_memory`:
  - index `{pattern_key}`
  - index `{sport}`
  - index `{enabled}`
  - index `{sample_size}`
  - index `{last_updated}`

Reglas:
- Fail-soft: si el índice falla (permisos/cluster), solo log debug/warn, nunca romper.

### Phase 9 — Football Goal Pressure Profile (NEW)
Implementar `football_goal_pressure_profile.py` (pure, determinista, explicable) con señales disponibles:
- Pre-match / historial (desde `normalizer.normalize_recent_fixtures` y context):
  - `under_2_5_rate`, `under_3_5_rate`, `btts_rate`, `clean_sheet_rate`, `goals_for_avg`, `goals_against_avg`
  - `early_goal_profile` / `early_goal_pct` (ya existe `derived_early_goal.py`)
- Live (desde `live_stats` si existe):
  - proxies: tiros, tiros a puerta, posesión, corners, xG si está presente

Outputs (shape estilo MLB):
- `available`, `home`, `away`, `combined`, `reason_codes`, `flags`, `inputs`
- Tiers sugeridos: `HIGH_PRESSURE / MODERATE / LOW / NEUTRAL / UNAVAILABLE`

Reglas de diseño:
- No copiar thresholds MLB; definir thresholds football-specific y testables.
- Debe poder operar con datos parciales (por ejemplo solo recent_fixtures sin xG).

### Phase 10 — Snapshot Builder (Pregame + Live) (NEW)
Implementar `football_snapshot_builder.py` + persistencia en warehouse:
- Construir snapshot canónico:
  - ids: `match_id`, `home_team_id`, `away_team_id`, `league`, `day`
  - `pregame_snapshot`: odds digest + context digest + goal_pressure_profile + corner_form + form_guard + football_quality
  - `live_snapshot` (si existe): live_stats digest + live goal pressure override + cambios vs pregame
  - `selected_market` (si ya existe pick)
  - `pattern_keys` (derivados)
  - timestamps

Persistir en `football_match_intelligence_snapshots`:
- `replace_one({match_id, day}, upsert=True)` (idempotente)

### Phase 11 — Pattern Memory (derive + lookup + conservative adjustment) (NEW)
Implementar `football_pattern_memory.py`:
- `derive_pattern_keys(pick_payload/match_context) -> list[str]`
  - claves basadas en: presión, under-profile fuerte, early-goal risk, corners volatility, form_guard flags, league tier, etc.
- `lookup_pattern_match(db, keys)`:
  - agrega métricas: `sample_size`, `hit_rate`, `roi`, `best_market`
  - **con gates** por tamaño de muestra (conservador, similar a MLB)
  - si `enabled:false` → no aplicar ajuste
- `attach_pattern_match_to_payload(db, pick_payload)`:
  - añade `historical_pattern_match` + top-level mirrors (sin romper payloads viejos)

Regla crítica:
- Pattern memory solo sugiere ajuste leve y recomendaciones de mercado protegido (ej: preferir Under 3.5 vs Under 2.5) **sin forzar**.

### Phase 12 — Football Market Selection Layer (NEW)
Implementar `football_market_selection.py` (pure):
- Inputs: goal_pressure_profile, under_profile (recent_fixtures), early_goal risk, corners trap, form_guard, league quality, odds availability
- Output canónico estilo MLB:
  - `recommended_market`, `protected_alternative`, `market_confidence`, `fragility`, `reason_codes`, `requires_manual_odds`, `watchlist`

Políticas:
- Conservador: evitar Over y mercados agresivos por defecto.
- Preferir mercados protegidos (ej: Under 3.5 como “protección” frente a Under 2.5 si hay volatilidad).
- Nunca “forzar pick”: si no hay suficiente evidencia, `watchlist/manual_odds`.

### Phase 13 — Feedback Loop + Market Results (NEW)
Implementar `football_feedback_loop.py` + `football_intelligence_warehouse.persist_football_market_result`:
- Guardar settle outcome en `football_market_results` (por user+match+market)
- Guardar `pattern_keys` usados en el pick/snapshot
- Actualizar `football_pattern_memory` agregando:
  - `sample_size`, `wins`, `hit_rate`, `roi`, `market_ledger`, `best_market`, timestamps

Integración con flujo de settle:
- Añadir endpoint/handler settle football (o hook en tracking existente si ya existe) de forma fail-soft.

### Phase 14 — Live vs Pregame Comparison (NEW)
Extender `live_reevaluation.py`:
- Para football: comparar `live_snapshot` vs `pregame_snapshot`:
  - cambios de presión (goal_pressure_profile)
  - cambios en fragilidad esperada
  - recomendación de “mantener / reducir / evitar” mercado
- Output: `football_live_vs_pregame` adjunto al payload live (fail-soft)

### Phase 15 — Endpoint nuevo: `/api/football/pattern-memory/summary` (NEW)
FastAPI:
- `GET /api/football/pattern-memory/summary`
- Devuelve resumen agregado (top patterns por sample_size/roi/hit_rate) + flags `enabled`, `last_updated`
- Fail-soft: si DB no responde → `{available:false, reason:"db_error"}`

### Phase 16 — Tests (Football Moneyball Backend) (NEW)
Pytest (obligatorio, incremental):
- Warehouse fail-soft: db None, operaciones fallidas
- Índices: helper no rompe
- Goal pressure profile: thresholds deterministas + missing-data behavior
- Snapshot builder: digest correcto + idempotencia
- Pattern memory gates: n bajo → no adjustment; enabled false → no effect
- Market selection: no-over-forcing; protected alternative preferencia
- Feedback: persist_market_result actualiza pattern memory
- Live vs pregame: comparación con datos parciales
- Cross-sport: MLB/Basketball no afectados

---

## 5) UI mínima viable (Football Moneyball) (P1, después del backend completo)

### Phase 17 — Frontend: MatchCard panels (NEW)
Actualizar `MatchCard.jsx` para football con 3 paneles nuevos:
- `FootballIntelligencePanel.jsx`
  - goal_pressure_profile + under-profile + selected_market (si aplica)
- `FootballPatternMemoryPanel.jsx`
  - historical_pattern_match summary (sample_size/hit_rate/roi/best_market) + warnings
- `FootballLiveVsPregamePanel.jsx`
  - diferencias live vs pregame + reason codes

Reglas:
- Render conditional + fail-soft: si data no existe, panel muestra empty state o no renderiza.

### Phase 18 — Frontend: consumo endpoint summary (NEW)
- Consumir `GET /api/football/pattern-memory/summary` para una vista compacta (si se decide incluirlo en UI; mínimo viable puede ser solo en MatchCard).

### Phase 19 — Tests frontend (RTL) (NEW)
- Render condicional con payloads parciales
- No romper MLB/Basketball cards
- Empty states correctos

---

## 6) Next Actions (Actualizado)

### Inmediato (Football Moneyball P0)
1. Crear `services/football_moneyball/` + `football_intelligence_warehouse.py` con 4 colecciones nuevas + helper índices.
2. Implementar `football_goal_pressure_profile.py` (pure) con inputs existentes (`recent_fixtures`, `early_goal_profile`, `live_stats`).
3. Implementar snapshot builder + persistencia `football_match_intelligence_snapshots`.
4. Implementar pattern memory (derive/lookup/attach) + gates conservadores.
5. Implementar market selection football (protegido) + reason codes.
6. Implementar feedback loop + persist results + update `football_pattern_memory`.
7. Extender `live_reevaluation.py` con `football_live_vs_pregame`.
8. Añadir endpoint `/api/football/pattern-memory/summary`.
9. Tests pytest por módulo; correr suite completa.

### Luego (UI mínima viable)
10. Actualizar `MatchCard.jsx` + crear 3 paneles.
11. Tests frontend mínimos.

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

### Football Moneyball Intelligence Layer + Pattern Memory (P0)
- Backend completo en `services/football_moneyball/` con arquitectura estilo MLB.
- 4 colecciones Mongo nuevas + índices best-effort.
- Snapshots pregame/live persistidos idempotentemente.
- Goal pressure profile operativo con datos parciales (recent_fixtures / live_stats) y explicable.
- Pattern memory conservadora: no fuerza picks; solo ajusta levemente + sugiere protección.
- Market selection football prefiere mercados protegidos (ej: Under 3.5 sobre Under 2.5 cuando hay volatilidad).
- Feedback loop actualiza `football_pattern_memory` tras settle.
- Live vs pregame comparison disponible y fail-soft.
- Endpoint `/api/football/pattern-memory/summary` funcional y fail-soft.
- Tests: `pytest` sin regresiones (MLB/Basketball intactos) + nuevos tests football.
