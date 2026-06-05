# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests + Game Openness Guard + Unilateral Dominance + Corner Settlement (plan.md)

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

> **Estado tests (actual):** ✅ `pytest tests/` **1118 passing**.

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
- El engine recomendaba **Over 3.5 @ 79%** porque el xG local era alto (1.85), pero el visitante aportaba muy poco (0.50).
- `_momentum_score` ya calculaba `total = h_idx + a_idx`, pero el pipeline usaba principalmente el delta direccional y **descartaba** la señal bilateral necesaria para **Over/BTTS**.

### 34.2 Backend — Nuevo módulo `services/game_openness.py` ✅
- ✅ Módulo puro, sin IO, fail-soft.
- ✅ `compute_game_openness(home_stats, away_stats, *, minute, current_total)`:
  - `combined_xg`, `home_xg`, `away_xg`
  - `one_sided_ratio` (= weaker_xg / combined)
  - flags: `is_bilateral`, `is_one_sided`, `supports_over_35`, `supports_over_25`, `supports_btts`
  - `recommended_total` (Over 3.5 / Over 2.5 / BTTS / None)
  - `reason_es`
- ✅ Umbrales calibrados con casos reales:
  - `MIN_SIDE_XG_FOR_OPEN=0.55`
  - `MIN_COMBINED_XG_FOR_OVER35=2.40`
  - `MIN_COMBINED_XG_FOR_OVER25=1.60`
  - `ONE_SIDED_RATIO_THRESHOLD=0.22`

### 34.3 Backend — Guard `guard_total_recommendation()` ✅
- ✅ `guard_total_recommendation(proposed_market, openness)`:
  - si se propone **Over 3.5** y `supports_over_35` es falso → degrada a `recommended_total` (Over 2.5 / BTTS) o marca `not_actionable`.
  - si se propone **Over 2.5** y `supports_over_25` es falso → `not_actionable=True`.

### 34.4 Integración en `live_reevaluation.py` ✅
- ✅ Compute openness antes del interpreter.
- ✅ Inyecta `reeval_for_interpreter['game_openness']`.
- ✅ Expone `game_openness` en la respuesta final del reeval.

### 34.5 Integración en `human_live_interpreter.py` ✅
- ✅ Después de fijar `suggested_market`, aplica `guard_total_recommendation()` usando `reeval.game_openness`.
- ✅ Expone `game_openness` en el output del interpreter (para UI “Evidencia Live”).

### 34.6 Tests ✅
- ✅ `tests/test_game_openness.py` (base).
- ✅ Testing agent backend: **1089/1089** (100%): `/app/test_reports/iteration_65.json`.

---

## 10) Phase 35 — P1: Tres fixes integrados ✅ COMPLETADO

### Fix 1 — Game Openness: dominancia unilateral vs apertura bilateral ✅
- ✅ Test Mexico–Serbia 5-1 **corregido**: es **UNILATERAL DOMINANCE + colapso**, no bilateral openness.
- ✅ Renombrado:
  - `test_mexico_serbia_supports_balanced_totals` → `test_mexico_serbia_is_unilateral_dominance_not_bilateral_openness`.
- ✅ Aserciones ahora obligatorias (Moneyball strict):
  - `is_one_sided=True`
  - `is_bilateral=False`
  - `supports_btts=False`
  - `supports_over_35=False`
- ✅ Nuevo helper puro: `compute_unilateral_dominance_over_profile(home_stats, away_stats, match_context=None)`:
  - gates: `dom_xg≥1.75`, `dom_shots≥14`, `dom_sot≥5`, `opp_shots≤5`
  - colapso (cualquiera): own goals, errors_to_shot/goal, red cards, GK saves≥4, late fatigue, score_diff≥2, set-piece flood, high_total_snowball.
  - output:
    - `profile_type="UNILATERAL_DOMINANCE_OVER"`
    - `supports_team_total` (si is_dominant)
    - `supports_match_over_high` (solo si is_dominant AND has_collapse)
    - `reason_codes`, `reason_es`
- ✅ 6 tests nuevos: Mexico–Serbia positive, dominance sin colapso (team total only), France–IVC no pasa gates, opponent crea demasiado (fail), fail-soft, jamás sugiere BTTS.

### Fix 2 — Wire final game_openness + BTTS/Over guards en interpreter ✅
- ✅ `human_live_interpreter.py`:
  - si `suggested_market` es BTTS y **current_score ya es 1-1** → strip BTTS + “BTTS ya ocurrió” en `why`.
  - si `suggested_market` es **Over 3.5** y `supports_over_35=False` → aplicar guard fallback o strip.
  - si `suggested_market` es **Over 2.5** y `supports_over_25=False` → strip.
  - fail-soft si `game_openness` falta.
- ✅ 2 tests nuevos: BTTS strip cuando 1-1; Over 2.5 strip cuando unsupported.

### Fix 3 — Settlement extendido para córners ✅
- ✅ Nuevo módulo puro/fail-soft: `services/live_recommendation_settlement.py`.
- ✅ `settle_corner_market(event, final_match_stats)`:
  - total corners Over/Under (X.5 e integer con push)
  - team corners Over/Under (EN/ES + detección por nombre de equipo)
  - corner handicap simple (half/integer con void)
  - Asian ¼ (±0.25/±0.75) → `requires_manual_settlement`
  - missing stats → `pending` (nunca miss)
- ✅ Dispatcher: `settle_event_extended(event, final_match_stats)` retorna None si no es corners.
- ✅ Integración: `settle_open_live_events_for_match` prueba settlement extendido primero y cae a legacy BTTS/Over-Under.
- ✅ 21 tests nuevos cubren todos los casos solicitados.

### Tests y verificación (Phase 35) ✅
- ✅ `pytest tests/` verde: **1118 passing**.
- ✅ Focused tests: **96/96** verdes (game_openness + over_support + market_selection + corners + lrh).
- ✅ Testing agent backend: **100%** (0 critical bugs): `/app/test_reports/iteration_66.json`.

---

## 11) Next Actions (Actualizado)

### En curso
- (P0/P1) Injury Intelligence Basketball (Phase 5–7).

### Pendiente / futuro
- (P2) Tests end-to-end live → settlement (con partidos live reales).
- (P2) Extender settlement a más mercados (handicap asiático completo, tarjetas, etc.).
- (P2) Retomar Injury Intelligence Football (Phase 2) cuando Basketball Phase 1 esté estable.

---

## 12) Success Criteria (Actualizado)

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

### Game Openness Guard (P1) + Dominancia unilateral (Phase 35)
- Over 3.5 **no** se recomienda si el juego es one-sided y el total no tiene respaldo bilateral.
- Mexico–Serbia 5-1 se clasifica como dominancia unilateral (no bilateral openness).
- Over 3.5 en dominancia unilateral solo se respalda vía `UNILATERAL_DOMINANCE_OVER` cuando existe colapso defensivo.
- El interpreter bloquea BTTS si ya ocurrió (1-1) y bloquea Over 2.5/3.5 si openness no lo soporta.
- `game_openness` queda expuesto para UI en modo explicable.

### Settlement córners (Phase 35)
- Corner settlement es determinista y auditable.
- Missing stats no marca miss.
- Asian ¼ routes a `requires_manual_settlement`.
- Integrado en auto-settle sin romper BTTS/totales legacy.

### Frontend RTL
- Timeline live tiene tests RTL.
- Paneles football (DC/NB + Over Support) tienen tests RTL.
- MatchCard gating por deporte validado.
