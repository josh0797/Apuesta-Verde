# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests + Game Openness + Unilateral Dominance + Corner Settlement + Pattern Memory Voids + Basketball Possessions/Four Factors + Live Reeval UX (plan.md)

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

> **Estado tests (actual):** ✅ Backend `pytest tests/` **1188 passing**. Frontend `craco test` **50 passing**.

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

### 37.1 Backend — Nuevo módulo `services/basketball_possession_layer.py`
- Crear funciones puras (testeables):
  - `estimate_possessions(game_stats)`
  - `calculate_four_factors(team_stats)`
  - `calculate_team_efficiency_profile(team_games)`
  - `calculate_matchup_possession_context(home_profile, away_profile)`
  - `derive_basketball_market_adjustments(matchup_context)`
- Contrato de salida (shape estable para UI/pipeline):
  - `basketball_possession_profile.available`
  - bloques `home`, `away`
  - bloque `matchup`:
    - `projected_possessions`, `projected_total_points`, `projected_margin`
    - `pace_environment`, `efficiency_edge`, `total_points_lean`, `spread_support`
    - `fragility_score`, `reason_codes`, `summary`

### 37.2 Reglas + reason codes
- Implementar reason codes requeridos:
  - `HIGH_PACE_ENVIRONMENT`, `LOW_PACE_ENVIRONMENT`
  - `STRONG_OFFENSIVE_RATING_EDGE`, `STRONG_DEFENSIVE_RATING_EDGE`
  - `THREE_POINT_VARIANCE_RISK`, `TURNOVER_RISK`, `OFFENSIVE_REBOUND_EDGE`, `FREE_THROW_RATE_SUPPORT`
  - `SPREAD_MARGIN_SUPPORTED`, `MONEYLINE_SAFER_THAN_SPREAD`
  - `TOTAL_OVER_SUPPORTED`, `TOTAL_UNDER_SUPPORTED`
  - `DATA_INSUFFICIENT_FALLBACK`

### 37.3 Integraciones (sin romper MLB/football)
- `historical_enrichment/basketball_historical.py`:
  - exponer/usar `pace_proxy` como fallback.
  - (opcional) enriquecer `total_points_std` desde histórico (ya hay `statistics`).
- `basketball_pace_layer.py`:
  - consumir `basketball_possession_profile` si está disponible como reemplazo/upgrade de proxies.
- `basketball_trap_signals.py`:
  - añadir fragility adicional por `THREE_POINT_VARIANCE_RISK` y/o `pace_volatility` cuando exista.
- `basketball_intelligence_warehouse.py`:
  - permitir persistir snapshot del nuevo bloque (sin hacerlo obligatorio).
- `analyst_engine.py`:
  - Phase 10b/10x: prefetch (best-effort) y attach al match entry.

### 37.4 Fail-soft + performance
- Si faltan stats (o API rate limit):
  - `available:false`
  - `reason_codes=['DATA_INSUFFICIENT_FALLBACK']`
  - no abortar pipeline.
- Evitar I/O dentro de funciones puras: separar “fetch” (si aplica) de “compute”.

### 37.5 Tests (pytest)
- Nuevo módulo: `backend/tests/test_basketball_possession_layer.py` con:
  - estimación de posesiones (casos típicos + missing keys)
  - cálculo four factors (eFG/TOV/ORB/FTr)
  - derivación de leans (Over/Under) por reglas
  - fragility + reason codes (3P variance, sample insufficiente)
- Regresión: ejecutar `pytest` completo.

---

## 14) Phase 39 — Fixes 7 + 3 + 5/6 + 2: live reeval UX + tracking source + DNB amistosos ✅ COMPLETADO
- Fix 7: `/api/live/reevaluate` valida Over/Under 0.5 + market whitelist fail-soft; `TrackIn` acepta `cancelled`/`refund` + entry_minute/score.
- Fix 3: `LiveReevalPanel` post-eval radio engine vs manual + nuevo botón "Cancelada"; tracking envía `source`, `is_live`, `entry_*`.
- Fix 5/6: `track_pick` mirror automático a `live_recommendation_events` con source; `settle_live_recommendation_event` acepta cancelled/refund → status=void.
- Fix 2: nuevo módulo `services/friendly_dnb_rule.py` (hard rule) + `lookup_friendly_dnb_pattern` / `record_friendly_dnb_outcome` en warehouse (≥60 muestras activa amplify/dampen). Interpreter consume el learned pattern vía `match["learned_patterns"]`.
- Tests: 21 friendly_dnb + 3 RTL Fix 3 = 24 nuevos.

## 16) Phase 41 — Fix 1/2 wiring (hydrate prefetch + DNB settlement) + Fix 3 mobile UX ✅ COMPLETADO
- **Fix 1**: `prefetch_basketball_profiles` y `prefetch_baseball_profiles` ahora invocan `hydrate_match_with_box_scores` con timeout estricto (5s default, configurable). Activado por defecto, deshabilitable con `BASKETBALL_BOX_SCORES_HYDRATE=0` o `BASEBALL_BOX_SCORES_HYDRATE=0`. Nuevo endpoint `POST /api/analysis/box-scores/hydrate` para hidratar manualmente y persistir en mongo.
- **Fix 2**: `settle_open_live_events_for_match` ahora alimenta `record_friendly_dnb_outcome` cuando el partido es football amistoso y ya terminó. Detección de favorito vía pre-match 1X2; detección de `used_dnb` mirando si algún evento del match usó mercado DNB. Push automático cuando hay empate y DNB (sin penalizar pattern memory).
- **Fix 3 mobile**: `normalizeManualOddsInput` helper agregado (alias estricto con floor 1.01). Input acepta tanto `1.21` como `1,21`. Validación lazy en `onBlur` (no rechaza intermediates como `1,`). Atributos `autoComplete/autoCapitalize/autoCorrect/spellCheck=false` para mobile clean. `inputMode=decimal` + `type=text` (no `number`) + `pattern=[0-9]+([.,][0-9]+)?`.
- **Fix 3 per-card endpoint**: nuevo alias `POST /api/analysis/live/reevaluate-one` con el mismo contrato que `/live/reevaluate`. URL explícita para metrics y per-card flow. Frontend `LiveReevalPanel` ahora apunta al nuevo endpoint.
- **Fix 3 inline error**: banner inline `reeval-error-${matchId}` con botón dismiss, persiste hasta el próximo run() exitoso. Otras cards no afectadas.
- Tests: +6 backend (Phase 41 wiring) + 8 RTL (mobile + per-card + error) = 14 nuevos.
- Nuevo paquete `services/box_score_providers/` con API-Sports primary + Balldontlie/MLB StatsAPI fallback.
- `fetch_basketball_team_games`, `fetch_baseball_team_games` (async, fail-soft).
- `hydrate_match_with_box_scores(match)` attacha `_box_score_games` que `analyst_engine` Phase 12b.2 ya consume para mejorar Four Factors reales.
- Tests pure-normalizer (sin red): 11 casos.

### 38.1 Frontend — Timeout y manejo fail-soft
- Aumentar timeout en `LiveReevalPanel.jsx` para `/api/live/reevaluate` cuando `useManual=true`.
  - recomendado: 45s–60s (mantener 20s para path normal si queremos).
- No “romper” tarjeta:
  - mantener `manualOdds` y `manualMarket` intactos tras error.
  - mensaje más útil:
    - “Estamos recalculando con tu cuota manual. Si tarda demasiado, puedes intentar de nuevo sin perder los datos ingresados.”
- Mantener spinner/estado de carga visible.

### 38.2 Frontend — Normalización coma decimal (helper)
- Extraer helper en `frontend/src/lib/normalizeDecimalOdds.js` (o similar):
  - aceptar `1.20`, `1,20`, `1.2`, `1,2`
  - normalizar `,`→`.` y validar `>1.01`.
- Reusar helper en `LiveReevalPanel` (y en cualquier otro input de odds manual).

### 38.3 Frontend — Mercados Over/Under 0.5
- Añadir a `DEFAULT_MARKETS_FOOTBALL`:
  - `Under 0.5`, `Over 0.5`
- Verificar que el backend parser de `manual_market` ya soporta 0.5 (si no, ajustar el parser en backend de forma fail-soft).

### 38.4 Frontend — Registrar resultado para selección manual
- Extender UI para que el tracking use:
  - `result.*` cuando existe
  - o `manual_market`/`manual_odds` como fallback si no hay recomendación clara.
- Mantener compatibilidad con `/api/picks/track` actual.

### 38.5 Tests (frontend)
- Añadir RTL tests:
  - timeout: mock de request que tarda más de 20s y validar mensaje + persistencia de inputs
  - coma decimal: ingresar `1,35` y validar body enviado con `1.35`
  - mercados 0.5: dropdown contiene Over/Under 0.5

---

## 14) Next Actions (Actualizado)

### En curso (prioridad)
- (P0/P1) Phase 37: Basketball Possession & Four Factors Layer.
- (P0/P1) Phase 38: LiveReevalPanel UX/timeout + coma decimal + mercados 0.5 + tracking manual.

### Pendiente / futuro
- (P1) Injury Intelligence Basketball (Phase 5–7) — retomar tras estabilizar Phase 37.
- (P2) Tests end-to-end live → settlement (con partidos live reales).
- (P2) Extender settlement a más mercados (handicap asiático completo, tarjetas, etc.).
- (P2) Injury Intelligence Football (Phase 2) cuando Basketball Phase 1 esté estable.

---

## 15) Success Criteria (Actualizado)

### Basketball Possession & Four Factors (Phase 37)
- El payload incluye `basketball_possession_profile` con `available:true` cuando hay datos.
- Reason codes correctos y consistentes con reglas.
- Mejora de explicabilidad: summary en español y `fragility_score` coherente.
- Fallback: si faltan datos, `available:false` y no se rompe el pipeline.
- No-regresión: MLB y football permanecen intactos.

### Live reevaluación con cuota manual (Phase 38)
- No hay timeout UI fijo a 20s que bloquee el flujo (al menos para manual odds).
- Mensaje útil y posibilidad de reintentar sin perder inputs.
- Input acepta coma decimal en móvil y se normaliza correctamente.
- Dropdown incluye Over/Under 0.5.
- Tracking: se puede registrar outcome de recomendación o selección manual.

### Global
- ✅ Backend: `pytest` completo en verde.
- ✅ Frontend: `craco test` en verde.
- Fail-soft mantenido en todas las rutas.
