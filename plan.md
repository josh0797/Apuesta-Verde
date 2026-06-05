# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests + Game Openness + Unilateral Dominance + Corner Settlement + Pattern Memory Voids (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

### Objetivos en curso (Injury Intelligence Layer)
- ⏳ Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
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
- ✅ Modelo robusto para totales football:
  - Matriz bivariada `P(home=i, away=j)` truncada y renormalizada.
  - Dixon–Coles tau aplicado a low-score con clamp ρ∈[-0.20, 0.0].
  - NB condicional por lado con ratio clamp [1.0, 2.0], por defecto inert (1.0).
- ✅ Telemetría completa en `compute_match_features`.
- ✅ Calibración `global-antes-de-bucket` (n<100 defaults; buckets OBSERVE_ONLY hasta n≥100).
- ✅ Endpoint `GET /api/football/totals-calibration/summary?days=90`.
- ✅ Persistencia extendida en `football_market_results`.

### Objetivos completados (UI Football DC/NB + Over Support)
- ✅ `FootballDcNbPanels.jsx`:
  - `FootballTotalsModelPanel` (Poisson vs DC/NB, ρ, NB ratio, deltas, modo defaults/empirical).
  - `FootballOverSupportPanel` (Over 1.5/2.5 support, presión 0–30, fragilidad, reason codes).
  - ✅ Badge adicional: **OBSERVE ONLY** cuando `mode=observe_only` o `recommended_over_market` vacío.
- ✅ Integración en `MatchCard.jsx` (gated por `sport === 'football'`, fail-soft por `available`).

### Objetivo completado (P0): Live Recommendation History / Timeline
- ✅ Historial/auditoría de recomendaciones live:
  - autosave con dedupe (solo cambios reales)
  - manual entry (sin requerir match doc real)
  - auto-settle MVP (BTTS + Over/Under)
  - endpoints con filtros completos
  - UI timeline + formulario manual
  - fail-soft end-to-end

### Objetivo completado (P1): Over Support Market Selection + Frontend RTL Tests
- ✅ Integrar `football_over_support` en `football_market_selection.py` como señal **de soporte** para mercados Over, manteniendo **protected-market-first**.
- ✅ Permitir **Over 1.5** como mercado protegido condicional.
- ✅ Permitir **Over 2.5** solo en escenarios de soporte extremo y baja fragilidad, con gates por DC/NB y lesiones.
- ✅ Bloquear recomendaciones de **líneas muertas** (ya cumplidas) para entradas live.
- ✅ Suite **frontend RTL** para timeline live y paneles football (incluye gating por deporte).
- ✅ Tests backend pytest para selección de mercado (Over Support integration).

### Objetivo completado (P1): Bug Fix BTTS live + auto-settle (desde “badge”/narrativa)
- ✅ Normalización robusta de mercados (`normalize_live_market_label`) para detectar:
  - BTTS (Ambos marcan) aunque el `title` sea “momentum local”
  - Over/Under X.5 desde textos heterogéneos
- ✅ Persist automático de recomendación live cuando BTTS/Over aparece en narrativa/why/reason.
- ✅ `settle_open_live_events_for_match` invocado en `/api/live/reevaluate` para auto-settle inmediato.
- ✅ Backfill manual México vs Serbia (`match_id=1528284`) insertado como HIT.

### Objetivo completado (P1): Game Openness Guard (bilateral live-threat para TOTAL markets)
- ✅ Nuevo guard para evitar **Over 3.5** cuando el juego es **one-sided** y el total no tiene respaldo bilateral.
- ✅ Se usa para degradar Over 3.5 a Over 2.5 / BTTS cuando corresponde, o marcarlo como no accionable.

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC (aislado, obligatorio) ✅ COMPLETADO
**Core probado:** “Game → pipeline Moneyball → `market_selection` final → payload persistible + live/pregame linkage por `game_pk` (fail-soft).”

---

### Phase 2 — V1 Backend Development (Moneyball alignment) ✅ COMPLETADO
(MLB pipeline Moneyball, summary + editorial mapper)

---

### Phase 3 — V1 Frontend Development (UI Moneyball) ✅ COMPLETADO
(MatchCard panels + dashboard buckets + live analysis)

---

### Phase 4 — Comprehensive Testing & Regression ✅ COMPLETADO
- ✅ Suite backend sin regresiones.

> **Estado tests (actual):** ✅ `pytest tests/` **1129 passing**.

---

## 3) Injury Intelligence Layer — Basketball (Phase 1) (EN CURSO)

### Phase 5 — Injury Intelligence (Basketball) — Backend (pendiente)
### Phase 6 — Injury Intelligence (Basketball) — Frontend/UI (pendiente)
### Phase 7 — Tests (Basketball Injury Intelligence) (pendiente)

---

## 4) Football Moneyball Intelligence Layer + Pattern Memory (P0) ✅ COMPLETADO
(Phases 8–16 completadas; warehouse + snapshots + pattern memory + market selection + feedback)

---

## 5) UI mínima viable (Football Moneyball + DC/NB + Over Support) ✅ COMPLETADA

### Phase 17 — Frontend: MatchCard panels ✅
- ✅ Paneles Moneyball football.
- ✅ Paneles DC/NB Totals + Over Support.

### Phase 18 — Frontend: consumo endpoint summary (opcional)
- (Opcional) Consumir endpoints summary para dashboards agregados.

### Phase 19 — Tests frontend (RTL) ✅ COMPLETADO
- ✅ RTL para timeline live + paneles football + gating por deporte (ver Phase 33.3–33.4).

---

## 6) Football Totals Calibration — Dixon-Coles + NB Conditional (P0) ✅ COMPLETADO
(Phases 20–26 completadas.)

---

## 7) Live Recommendation History / Timeline (P0) ✅ COMPLETADO

### Phase 27–32 ✅
- ✅ Colección + índices + servicio + endpoints + auto-settle + UI timeline.
- ✅ Backfill France vs Ivory Coast (match_id=1536931) insertado.
- ✅ Testing agent backend 30/30 (100%): `/app/test_reports/iteration_63.json`.

---

## 8) Phase 33 — P1: Football Over Support Market Selection + RTL Tests ✅ COMPLETADO + Bug Fix BTTS

### 33.1 Backend — Integración Over Support en `football_market_selection.py`
- ✅ Implementado `_evaluate_over_support` con gates conservadores.
- ✅ Helper puro `is_total_line_already_hit(match_or_snapshot, market_label)`.
- ✅ Conflictos DC/NB, lesiones, match controlado, odds faltantes → watchlist/manual.

### 33.2 Backend — Tests pytest (Market Selection)
- ✅ Añadido `tests/test_football_over_support_market_selection.py` (16 casos).

### 33.3 Frontend — Setup RTL (CRA/Jest)
- ✅ Instalado:
  - `@testing-library/react@^16`
  - `@testing-library/jest-dom`
  - `@testing-library/user-event`
  - `@testing-library/dom`
- ✅ `src/setupTests.js` + mapeo jest para alias `@/*`.

### 33.4 Frontend — RTL Tests ✅
- ✅ `LiveRecommendationTimeline` (9 casos).
- ✅ `FootballTotalsModelPanel` (11 casos).
- ✅ `FootballOverSupportPanel` (7 casos).
- ✅ MatchCard gating (4 casos).

### 33.5 Validación y No-regresión ✅
- ✅ Backend: `pytest tests/` verde.
- ✅ Frontend: `craco test` verde.
- ✅ Testing agent backend 71/71 (100%): `/app/test_reports/iteration_64.json`.

---

## 9) Phase 34 — P1: Game Openness (bilateral live-threat for TOTAL markets) ✅ COMPLETADO

### 34.1 Contexto / Bug atacado
- Caso real: **France vs Ivory Coast** al min ~54.
- El engine recomendaba **Over 3.5 @ 79%** porque el xG local era alto (1.85), pero el visitante aportaba muy poco.
- `_momentum_score` ya calculaba el total, pero el pipeline **descartaba** la señal bilateral necesaria para **Over/BTTS**.

### 34.2 Backend — Nuevo módulo `services/game_openness.py` ✅
- ✅ `compute_game_openness(home_stats, away_stats, *, minute, current_total)`:
  - `combined_xg`, `home_xg`, `away_xg`
  - `one_sided_ratio` (= weaker_xg / combined)
  - flags: `is_bilateral`, `is_one_sided`, `supports_over_35`, `supports_over_25`, `supports_btts`
  - `recommended_total` (Over 3.5 / Over 2.5 / BTTS / None)
  - `reason_es`
- ✅ Umbrales calibrados:
  - `MIN_SIDE_XG_FOR_OPEN=0.55`
  - `MIN_COMBINED_XG_FOR_OVER35=2.40`
  - `MIN_COMBINED_XG_FOR_OVER25=1.60`
  - `ONE_SIDED_RATIO_THRESHOLD=0.22`

### 34.3 Backend — Guard `guard_total_recommendation()` ✅
- ✅ Si se propone Over 3.5 sin respaldo → degrada a Over 2.5/BTTS o `not_actionable`.

### 34.4 Integración en `live_reevaluation.py` ✅
- ✅ Compute openness antes del interpreter.
- ✅ Inyecta `reeval_for_interpreter['game_openness']`.
- ✅ Expone `game_openness` en la respuesta final del reeval.

### 34.5 Integración en `human_live_interpreter.py` ✅
- ✅ Aplica guard antes de surfacing de totales agresivos.
- ✅ Expone `game_openness` en output del interpreter.

### 34.6 Tests ✅
- ✅ `tests/test_game_openness.py` (base).
- ✅ Testing agent backend: **1089/1089** (100%): `/app/test_reports/iteration_65.json`.

---

## 10) Phase 35 — P1: Tres fixes integrados ✅ COMPLETADO

### Fix 1 — Dominancia unilateral vs apertura bilateral ✅
- ✅ Corrección del fixture Mexico–Serbia: no usarlo como señal bilateral.
- ✅ Nuevo helper `compute_unilateral_dominance_over_profile(...)` + tests.

### Fix 2 — Guards estrictos ✅
- ✅ `human_live_interpreter.py`: BTTS strip si ya ocurrió (1-1); Over 2.5/3.5 strip si openness no lo soporta.

### Fix 3 — Corner settlement ✅
- ✅ `services/live_recommendation_settlement.py` + 21 tests.
- ✅ Integración en `settle_open_live_events_for_match`.

---

## 11) Phase 36 — P1: Tres cambios integrados desde archivos subidos ✅ COMPLETADO

### Cambio 1 — `live_reevaluation.py`: unilateral_dominance en payload ✅
- ✅ Junto al `compute_game_openness()` ahora se computa **`compute_unilateral_dominance_over_profile()`**.
- ✅ Se pasa al interpreter:
  - `reeval_for_interpreter['unilateral_dominance']`.
- ✅ Se expone en respuesta API:
  - `unilateral_dominance` top-level.
- ✅ Context usado:
  - `{ minute, score_diff=abs(score_diff), current_total }`.

### Cambio 2 — `human_live_interpreter.py`: dominancia antes de anular Over 3.5 ✅
- ✅ Antes de anular Over 3.5 por `openness.supports_over_35=False`, consulta `unilateral_dominance`.
- ✅ Comportamiento:
  - si `supports_match_over_high=True` → **mantiene Over 3.5** y añade `dominance.reason_es` a `why`.
  - si `supports_team_total=True` (sin colapso) → **degrada** a `Over equipo — {dom_name} (>1.5)`.
  - si dominancia no aplica → fallback al guard genérico (downgrade o strip).
- ✅ `unilateral_dominance` se expone también en el output del interpreter.

### Cambio 3 — `football_intelligence_warehouse.update_pattern_memory_from_result`: voids/push/refund ✅
- ✅ Se añadió `outcome: str | None` keyword.
- ✅ Si `outcome ∈ {void, push, refund, refunded, cancelled, canceled, no_action}`:
  - incrementa `voids`
  - **NO** incrementa `sample_size`
  - **NO** suma wins/losses
  - ROI se mantiene neutro si payout=stake.
- ✅ Se añade/propaga `voids: int` a:
  - `market_ledger`
  - `pattern_memory` doc
- ✅ Evita inflar denominadores: ejemplo 6W/4L/5V → hit_rate=60% (10 intentos válidos) y no 40%.

### Tests (Phase 36) ✅
- ✅ Reescritos los tests aportados por el usuario para imports normales:
  - `tests/test_interpreter_dominance.py` (4 casos)
  - `tests/test_pattern_memory_voids.py` (7 casos)
- ✅ Suite global: **1129 tests passing** (1118 → 1129, sin regresiones).
- ✅ Focused tests: **85/85** verdes (dominance + voids + openness + settlement + selection + lrh).

---

## 12) Next Actions (Actualizado)

### En curso
- (P0/P1) Injury Intelligence Basketball (Phase 5–7).

### Pendiente / futuro
- (P2) Tests end-to-end live → settlement (con partidos live reales).
- (P2) Extender settlement a más mercados (handicap asiático completo, tarjetas, etc.).
- (P2) Retomar Injury Intelligence Football (Phase 2) cuando Basketball Phase 1 esté estable.

---

## 13) Success Criteria (Actualizado)

### Football Over Support en Market Selection (P1)
- Over Support participa en market selection **sin forzar picks**.
- Over 1.5 puede salir como **protected** condicional.
- Over 2.5 solo con soporte fuerte y baja fragilidad; degradación/warning cuando aplique.
- Conflicto DC/NB (Under 3.5 alto) bloquea Over 2.5; Over 1.5 solo con gates más estrictos.
- Líneas ya cumplidas nunca se recomiendan como nueva entrada (`OVER_LINE_ALREADY_HIT`).
- Fail-soft total y sin regresión MLB/Basketball.

### Live Recommendation History + BTTS bug fix (P0)
- Cuando el engine sugiera BTTS en live (incluso como badge/narrativa), se guarda automáticamente.
- Si el marcador cumple (1-1), el evento se auto-settlea como HIT.
- Un evento HIT no se marca superseded por nuevas recomendaciones.

### Game Openness + Unilateral Dominance (Phase 34–36)
- Over 3.5 **no** se recomienda por xG unilateral sin respaldo.
- Dominancia unilateral se usa para:
  - permitir Over 3.5 solo con colapso (supports_match_over_high)
  - o degradar a team total si hay dominancia sin colapso.
- El interpreter bloquea:
  - BTTS si ya ocurrió (1-1)
  - Over 2.5/3.5 si openness no lo soporta (salvo escape hatch dominance).
- `game_openness` y `unilateral_dominance` quedan expuestos para UI y auditoría.

### Pattern memory: voids/push/refund
- Voids/push/refund/cancelled **no** inflan `sample_size` ni degradan `hit_rate`.
- `voids` queda persistido y visible en pattern_memory y market_ledger.

### Settlement córners
- Corner settlement es determinista y auditable.
- Missing stats no marca miss.
- Asian ¼ routes a `requires_manual_settlement`.
- Integrado en auto-settle sin romper BTTS/totales legacy.

### Frontend RTL
- Timeline live tiene tests RTL.
- Paneles football (DC/NB + Over Support) tienen tests RTL.
- MatchCard gating por deporte validado.
