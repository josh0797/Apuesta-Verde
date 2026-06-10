# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests + Game Openness + Unilateral Dominance + Corner Settlement + Pattern Memory Voids + Basketball Possessions/Four Factors + Live Reeval UX + **MLB Offensive Injury Impact** + **Engine vs User Pick Divergence** (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

---

### Objetivo (MLB): Offensive Injury Impact Score ✅ COMPLETADO (end-to-end)
**Problema:** el motor trataba todas las lesiones igual (“52 jugadores lesionados”).

**Solución implementada:** medir si los lesionados son realmente **bates importantes** (top-5 ofensivo) y cuantificar el daño.

- ✅ `services/mlb_offensive_injury_impact.py`
  - Score 0–100 por equipo (`offensive_injury_score`) basado en ranking top-5 por score compuesto:
    - OPS / wRC+ (35%)
    - Runs + RBI (25%)
    - HR + XBH (20%)
    - OBP (10%)
    - PA / volumen (10%)
  - Buckets: `LOW` / `MEDIUM` / `HIGH`.
  - Reason codes explícitos (incluye señales de Under cuando ambos equipos están depletados).
  - Reglas duras:
    - Pitchers y lesiones de banca **no penalizan**.
    - Two-way players tipo Ohtani: `P/DH` con `PA ≥ 50` cuenta como ofensivo.
    - Fail-soft cuando hay datos insuficientes (pool < 5 ofensivos): `{available: False, ...}`.
  - Ajustes pipeline: `apply_impact_to_pipeline` devuelve multiplicadores con cap de supresión `0.85×`.
  - **Nunca auto-flip** de polaridad de mercado (solo supresión y narrativa).

- ✅ Tests
  - `tests/test_mlb_offensive_injury_impact.py`: **19/19 passing**.
  - Suite completa backend (previo): **1445/1445 passing** sin regresiones.

- ✅ Integración backend
  - `services/mlb_stats_api.py`: `hydrate_team_offensive_roster()` (cache 6h; roster activo + stats de bateo).
  - `services/mlb_day_orchestrator.py`:
    - Hidrata roster ofensivo en paralelo con IL.
    - Calcula `compute_offensive_injury_impact`.
    - Persiste en `pick_payload['offensive_injury_impact']` y `pipeline_meta['offensive_injury_impact']`.
    - Aplica supresión al `_mean_eff` antes de `compute_expected_runs_distribution` usando el promedio de multipliers home/away.
    - No toca el pick principal: observe-only, supresión de runs/λ/traffic.
  - Smoke: backend levanta OK; `/api/picks/today?sport=baseball` responde 200.

- ✅ Integración frontend
  - `components/OffensiveInjuryImpactPanel.jsx`: panel colapsable estilo `TailRiskPanel`.
    - Colores: `LOW=emerald`, `MEDIUM=amber`, `HIGH=rose/destructive`.
    - Header: pills por equipo con bucket + missing_count.
    - Contenido: narrativa ES, top bates ausentes (OPS/HR), runs/game perdidos estimados, badge “Apoyo al Under” cuando aplica.
    - Fail-soft: no renderiza si `available:false` o sin datos relevantes.
  - `components/MatchCard.jsx`: panel cableado después de `TailRiskPanel` (gated por MLB).

**Notas:**
- Picks previos sin `offensive_injury_impact` → panel oculto (compatibilidad backward).
- Warning F821 `traffic_score_payload` en `mlb_day_orchestrator.py` es preexistente/latente.

---

### Objetivo (MLB): `hydrate_team_offensive_roster` fail-soft interno ✅ COMPLETADO
**Problema:** el contrato decía “nunca levantar excepción”, pero el primer draft dependía del `try/except` del orchestrator.

**Solución implementada (defensa en profundidad):**
- ✅ `services/mlb_stats_api.py::hydrate_team_offensive_roster()` reescrito como fail-soft de extremo a extremo:
  - `db=None` → bypass cache; warm fetch directo.
  - cache_get exceptions → ignoradas, continúa.
  - cache_put exceptions → ignoradas, retorna payload igualmente.
  - HTTP/JSON/parse failures → retorna payload seguro `{available: False, reason: ..., players: []}`.
  - parse-per-player con `try/except` defensivo.
  - debug logs con contexto (team_id, error).
- ✅ Tests nuevos:
  - `tests/test_hydrate_team_offensive_roster_failsoft.py`: **8/8 passing** (db=None, cache read/write error, http error, malformed JSON, malformed player record, etc.).

---

### Objetivo nuevo (Fix 1 + Fix 2): Validar apuesta real del usuario + Comparador Engine vs User ✅ COMPLETADO
**Problema actual:** al liquidar un pick, se asumía que el usuario apostó exactamente la recomendación del engine.

**Objetivo:** separar completamente:
- **LO QUE RECOMENDÓ EL ENGINE** (engine_accuracy)
- **LO QUE REALMENTE APOSTÓ EL USUARIO** (user_accuracy)

**Scope confirmado:**
- ✅ Deportes: **MLB + Fútbol**.
- ✅ Modal obligatorio en dos momentos: **pre-bet (Track In live)** y **settlement**.
- ✅ Backfill retroactivo desde Historial.
- ✅ Dashboard dedicado: `/dashboard/calibration`.
- ✅ Liquidación dual SIEMPRE: el engine_pick se auto-liquida con el score oficial para medir Engine Accuracy pura.

#### Backend ✅
- ✅ Nuevo módulo fail-soft: `services/pick_divergence_analysis.py` (puro Python, sin numpy/scipy)
  - `parse_pick`: normaliza mercados y lenguaje ES/EN
    - MLB: `total_runs`, `f5_total_runs`, `run_line`, `moneyline`
    - Football: `total_goals`, `btts`, `double_chance`, `moneyline_1x2`, `handicap`
    - Soporta ES: “menos de / más de / primeros 5 / empate”
  - `settle_pick_against_score`: computa `WIN/LOSS/PUSH/PENDING/VOID` por mercado.
  - `compute_divergence`: detecta `NONE / USER_PROTECTED_LINE / USER_AGGRESSIVE_LINE / DIFFERENT_MARKET / OPPOSITE_SIDE` + `line_difference` + direction.
  - `evaluate_engine_vs_user`: wrapper end-to-end.

- ✅ Inyección en `POST /api/picks/track` (`track_pick` en `server.py`):
  - Si hay `actual_*` + `final_score`, persiste `doc['divergence']` y setea `engine_result` y `user_result` top-level.
  - Si `actual_*` faltan → asume followed_engine=true (fail-soft).
  - **Nunca sobrescribe** `engine_recommendation`.

- ✅ Endpoints nuevos:
  - `GET /api/calibration/summary?days=N&sport=...`
    - retorna: totals, `engine`/`user` win rates, `followed_engine_rate`, `delta_breakdown`, `avg_line_protection`, `engine_won_user_lost`, `engine_lost_user_won`.
  - `GET /api/calibration/divergences?days=N&limit=L&sport=...`
    - lista de picks con divergence (followed_engine=false).
  - `PATCH /api/picks/{pick_uid}/user-bet`
    - backfill/edición de apuesta real; recomputa divergence; no toca engine.

- ✅ Tests:
  - `tests/test_pick_divergence_analysis.py`: **43/43 passing** (incluye 4 casos canónicos del spec).
  - Suite backend actualizada: **1496/1496 passing** sin regresiones.

#### Frontend ✅
- ✅ `components/EnginePickConfirmModal.jsx`
  - Modal 2 pasos:
    1) “El engine recomendó X. ¿Fue exactamente tu apuesta?” [Sí] [No]
    2) Si No: form de mercado/lado/línea/cuota con normalización automática (decimal/americana).
  - Integrado en `LiveReevalPanel.jsx` antes de registrar outcomes `won/lost/push` para picks `source=engine`.
  - `key={...}` en parent para reset de estado sin `setState` en effects.

- ✅ `components/UserBetBackfillModal.jsx`
  - Editor retroactivo desde Historial.
  - Botón lápiz en filas liquidadas:
    - desktop: `row-backfill-N`
    - mobile: `card-backfill-N`
  - PATCH al endpoint `/api/picks/{uid}/user-bet`.

- ✅ `pages/CalibrationPage.jsx` + ruta `/dashboard/calibration`
  - 3 KPIs: picks registrados, tasa followed_engine, protección media.
  - 2 tarjetas: “Precisión del Engine” (emerald) y “Tu Precisión” (cyan).
  - Panel divergencias con badges:
    - Engine PERDIÓ / Usuario GANÓ
    - Engine GANÓ / Usuario PERDIÓ
  - Tabla de divergencias (muestra delta + line_difference).

- ✅ Navegación:
  - `AppHeader.jsx` incluye tab “Calibración” con icono Target y `data-testid='nav-calibration'`.

- ✅ Validación:
  - build/lint OK.
  - screenshot de `/dashboard/calibration` OK.

#### Testing agent ✅
- ✅ `iteration_71.json`: backend 1496/1496, endpoints 8/8, UI 6/6 — 0 críticos.

---

### Objetivos en curso (Injury Intelligence Layer)
- ⏳ Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
- Arquitectura: **fail-soft**, multi-source, cache-aware, sport-specific, explicable, conservadora.
- Entregar un bloque `injury_intelligence` en el payload que ajuste (conservadoramente) confidence/fragility/market warnings **sin forzar picks**.
- UI: `InjuryIntelligencePanel` para football/basketball (no MLB) mostrando bajas clave, severidad, impacto y freshness.

---

### Objetivo nuevo (Basketball): Possessions + Pace + Efficiency + Four Factors (Fix 1)
- 🎯 Crear una capa avanzada de basketball basada en **posesiones reales** y **Four Factors** para mejorar:
  - Moneyline
  - Spread
  - Total Points
  - Team Totals
- Métricas objetivo (por equipo y por matchup):
  - `possessions`, `pace`, `offensive_rating`, `defensive_rating`, `net_rating`
  - Four Factors: `eFG%`, `TOV%`, `ORB%`, `FTr`
  - Complementarias: `3PA rate`, `3P variance`, `free_throw_rate`, `pace_volatility`, `total_points_std`
- Reglas de mercado (high-level):
  - pace alto + eficiencia alta → soporte Over
  - pace bajo + eFG bajo → soporte Under
  - 3P variance alta → subir fragility
  - TOV alto → bajar eficiencia ofensiva
  - ORB alto → subir segundas oportunidades
  - FTr alto → soporte puntos (reloj detenido)
  - net rating edge fuerte → soporte Moneyline/Spread
  - Spread solo si margin projection cubre la línea con colchón
- Fail-soft estricto:
  - si stats incompletas → fallback a `basketball_historical` y `pace_proxy`
  - si API timeouts/rate-limit → `available:false` y el pipeline continúa

---

### Objetivo nuevo (Live UX/Timeout): Reevaluación live con cuota manual + mercados 0.5 (Fix 2)
- 🎯 Corregir el error de timeout UI (>20s) al reevaluar con cuota manual.
- 🎯 Mejorar el input móvil:
  - aceptar coma decimal (`1,20`) y punto (`1.20`) sin bloquear
  - normalización consistente antes de enviar al backend
- 🎯 Mejorar selección de mercados:
  - añadir Over/Under **0.5** en fútbol
- 🎯 Mejorar tracking de resultados:
  - permitir registrar outcome también para selección manual cuando aplique
- Fail-soft UI:
  - no romper MatchCard si hay timeout
  - no perder `manual_odds` / `manual_market` ingresados

---

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

### Objetivo completado (P1): Game Openness Guard + Unilateral Dominance + Corner Settlement + Pattern Memory Voids
- ✅ Game openness (bilateral) + guard Over 3.5.
- ✅ Dominancia unilateral computada y consultada por interpreter.
- ✅ Corner settlement determinista.
- ✅ Fix pattern memory: void/push/refund no incrementa sample_size.
- ✅ Tests: `test_interpreter_dominance.py`, `test_pattern_memory_voids.py`, `test_game_openness.py`, `test_live_recommendation_corner_settlement.py`.

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

> **Estado tests (actual):** ✅ Backend `pytest tests/` **1496 passing**.

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
- ✅ RTL para timeline live + paneles football + gating por deporte.

---

## 6) Football Totals Calibration — Dixon-Coles + NB Conditional (P0) ✅ COMPLETADO
(Phases 20–26 completadas.)

---

## 7) Live Recommendation History / Timeline (P0) ✅ COMPLETADO

### Phase 27–32 ✅
- ✅ Colección + índices + servicio + endpoints + auto-settle + UI timeline.

---

## 8) Phase 33 — P1: Football Over Support Market Selection + RTL Tests ✅ COMPLETADO + Bug Fix BTTS
(33.1–33.5 completadas.)

---

## 9) Phase 34 — P1: Game Openness (bilateral live-threat for TOTAL markets) ✅ COMPLETADO
(34.1–34.6 completadas.)

---

## 10) Phase 35 — P1: Tres fixes integrados ✅ COMPLETADO
(Dominancia unilateral vs apertura bilateral, guards estrictos, corner settlement.)

---

## 11) Phase 36 — P1: Tres cambios integrados desde archivos subidos ✅ COMPLETADO
(unilateral_dominance payload, interpreter consulta dominancia, pattern memory voids.)

---

## 12) Phase 37 — Fix 1: Basketball Possession & Four Factors Layer ✅ COMPLETADO
(Ver detalles en secciones 37.1–37.5; se mantiene como referencia del roadmap histórico.)

---

## 13) Phase 43 — MLB Offensive Injury Impact Score ✅ COMPLETADO
(Ver secciones 43.1–43.5 arriba.)

---

## 14) Phase 44 — `hydrate_team_offensive_roster` fail-soft interno ✅ COMPLETADO
- ✅ Reescritura robusta con bypass cache cuando `db=None`.
- ✅ try/except por bloque (cache_get/cache_put/http/parse) y payload seguro en fallo.
- ✅ Tests `test_hydrate_team_offensive_roster_failsoft.py` (8/8).

---

## 15) Phase 45 — Fix 1 + Fix 2: Engine vs User Pick Divergence ✅ COMPLETADO

### 45.1 Backend — Divergence Analysis + auto-liquidación engine_pick
- ✅ Nuevo módulo: `services/pick_divergence_analysis.py`.
- ✅ Persistencia `divergence` + `engine_result` + `user_result` en `track_pick`.
- ✅ Nunca sobrescribir `engine_recommendation`.

### 45.2 Backend — Endpoints Calibration + Backfill
- ✅ `GET /api/calibration/summary`.
- ✅ `GET /api/calibration/divergences`.
- ✅ `PATCH /api/picks/{pick_uid}/user-bet`.

### 45.3 Frontend — Modal + Backfill + Dashboard
- ✅ `EnginePickConfirmModal.jsx` integrado en LiveReeval.
- ✅ `UserBetBackfillModal.jsx` en History.
- ✅ `/dashboard/calibration` con vista comparativa + tabla.
- ✅ Navegación `AppHeader`.

### 45.4 Tests
- ✅ `tests/test_pick_divergence_analysis.py` (43/43).
- ✅ `pytest tests/` completo (1496/1496).
- ✅ `iteration_71.json` (backend+api+ui) verde.

---

## 16) Phase 39 — Fixes 7 + 3 + 5/6 + 2: live reeval UX + tracking source + DNB amistosos ✅ COMPLETADO
(Sin cambios; se mantiene histórico.)

## 17) Phase 41 — Fix 1/2 wiring + Mobile UX + per-card endpoint ✅ COMPLETADO
(Sin cambios; se mantiene histórico.)

## 18) Phase 42 — Line Learning Engine (Entrega A) + Box-score hydrate UI ✅ COMPLETADO
(Sin cambios; se mantiene histórico.)

---

## 19) Next Actions (Actualizado)

### En curso (prioridad)
- (P1) Injury Intelligence Basketball (Phase 5–7) — retomar ahora que:
  - MLB Offensive Injury Impact está estable.
  - `hydrate_team_offensive_roster` es fail-soft.
  - Engine vs User Divergence ya entrega métricas separadas.

### Pendiente / futuro
- (P2) Retomar Injury Intelligence Football (Phase 2) cuando Basketball Phase 1 esté estable.
- (P3) Consumir el endpoint `POST /api/analysis/box-scores/hydrate` desde la UI (botón “Hidratar Four Factors” en cards basket/baseball) — mejorar UX/descubribilidad.
- (P2) Tests end-to-end live → settlement (con partidos live reales).
- (P2) Extender settlement a más mercados (handicap asiático completo, tarjetas, etc.).

---

## 20) Success Criteria (Actualizado)

### MLB Offensive Injury Impact (Phase 43)
- ✅ Payload incluye `offensive_injury_impact` con `available:true` cuando hay roster + IL suficientes.
- ✅ No penaliza pitchers ni lesiones de banca.
- ✅ Two-way players (P/DH con PA≥50) cuentan como ofensivos.
- ✅ Cap de supresión 0.85×.
- ✅ No auto-flip de polaridad (observe-only).
- ✅ UI colapsable muestra bucket + top missing bats + runs/game perdidos + narrativa.
- ✅ No-regresión: `pytest` completo verde.

### `hydrate_team_offensive_roster` fail-soft (Phase 44)
- ✅ `db=None` no rompe.
- ✅ cache read/write failures no rompen.
- ✅ API failure retorna payload seguro con `players: []`.
- ✅ No-regresión: suite pytest verde.

### Engine vs User Divergence (Phase 45)
- ✅ Nunca sobrescribir recomendación original del engine.
- ✅ Liquidación dual: `engine_result` y `user_result` siempre separados.
- ✅ Divergence tags correctos (protected/aggressive/different_market/opposite_side).
- ✅ Backfill retroactivo desde Historial.
- ✅ Dashboard `/dashboard/calibration` muestra:
  - Engine Accuracy
  - User Accuracy
  - Followed Engine Rate
  - Divergences (engine_won_user_lost / engine_lost_user_won)
  - Protección media de línea
- ✅ No-regresión: `pytest tests/` verde y UI sin errores.

### Injury Intelligence Basketball (Phase 5–7)
- Payload incluye `injury_intelligence` cuando hay datos.
- Reason codes correctos y explicabilidad en español.
- Fail-soft: si faltan datos, `available:false` y el pipeline continúa.

### Global
- ✅ Backend: `pytest` completo en verde.
- ✅ Frontend: build/lint sin errores; RTL en verde cuando aplique.
- Fail-soft mantenido en todas las rutas.
