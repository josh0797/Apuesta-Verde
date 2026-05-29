# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (`code/label/severity/explanation`) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Hardening de pipeline: evitar bloqueos en `stage=enriching` con timeouts + degradación elegante.

- **(✅ COMPLETADO)** Robustez multi-deporte en LIVE:
  - Detectar correctamente partidos LIVE en **basketball/baseball**.
  - Evitar “zombies LIVE” en fútbol.
  - Firewall de vocabulario para impedir **fugas de terminología**.

- **(✅ COMPLETADO)** Enriquecimiento histórico fútbol (últimos 15): mejorar explicabilidad y señales para rescate Under.

- **(✅ COMPLETADO)** **P3 — Editorial Context Engine (Scrapy)**:
  - Capa opcional y **fail-soft** de enriquecimiento editorial profundo **solo para fútbol** y **solo para matches shortlisteados**.
  - Separación **dato vs opinión** (heurístico regex) + interpretación Moneyball.
  - UI: bloque “Contexto editorial”.

- **(✅ COMPLETADO / VALIDADO EN VIVO)** Tuning de selectores + fuentes nuevas:
  - **AS.com** y **Marca** server-rendered.
  - Añadido filtrado fino para Marca (evitar `mercado-fichajes` / `-directo.html`).
  - Spider con dedupe por URL y soporte de exclusión por patrón.

- **(✅ COMPLETADO)** **P4 — Playwright** para fuentes JS-heavy:
  - Subprocess + stealth + dispatch paralelo Scrapy/Playwright.
  - Detecta challenges anti-bot y degrada sin romper análisis.

- **(🆕 NUEVO OBJETIVO)** **Bright Data Web Unlocker** como **tercer backend**:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball**.

- **(🆕 NUEVO OBJETIVO)** **Historical Detail Enrichment**:
  - Antes de analizar/descartar **basketball/baseball**, enriquecer con histórico profundo (10–15 juegos) y generar perfiles por equipo + combinado.
  - Añadir capas de rescate específicas:
    - `basketballTotalPointsRescueLayer(match)`
    - `baseballRunsRescueLayer(match)`
  - Todo pasa por Moneyball (edge/guardrails), con traps históricas.
  - UI: sección “Historial profundo” por deporte.

- **(🆕 NUEVO OBJETIVO)** **MLB Margin & Total Script Engine v2 (solo Baseball)**:
  - Evolucionar el engine MLB para comportarse como un **sistema especializado en guion MLB**:
    - Predecir **margen de victoria** (enfasis Run Line -1.5 favoritos dominantes)
    - Seleccionar líneas **Over/Under más protegidas** (6.5/7.5/8/8.5/9 y unders equivalentes)
    - Análisis **pitcher-first** con pitchers confirmados como gate duro
    - Parlays **MLB-only** con validación de correlación positiva
  - Restricción crítica: **NO tocar basketball/football** (backend y UI).

---

## 2) Implementation Steps

### Phase 1 — Core POC (aislado) para el flujo “tolerancia + decisión contextual + señales trampa”
**Estado:** ✅ COMPLETADO

---

### Phase 2 — V1 App Development (backend + wiring de rescate)
**Estado:** ✅ COMPLETADO

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

---

### Phase 4 — P0 LIVE Hardening + P2 Historical Profile (fútbol)
**Estado:** ✅ COMPLETADO

---

### Phase 5 — P3 Editorial Context Engine (Scrapy) — MVP
**Estado:** ✅ COMPLETADO

---

### Phase 6 — P3 Selector Tuning + New Sources (AS.com, Marca) + limpieza de falsos positivos
**Estado:** ✅ COMPLETADO (validación real 2026-05-28)

**Cambios confirmados**
- Marca:
  - `article_url_patterns` endurecidos (previa/crónica/analisis/alineaciones)
  - `article_url_exclude_patterns` (directos, fichajes, opinión, etc.)
- Spider:
  - soporte exclusión + dedupe estricto por URL+match

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy)
**Estado:** ✅ COMPLETADO (infra lista; desbloqueo real requiere unlocking)

**Observación de producción**
- Sportytrader (Cloudflare 403) y BeSoccer (Client Challenge/PerimeterX) bloquean incluso con Playwright sin proxy residencial.

---

## Phase G3 — Critical Bug Fixes (Defense-in-Depth Time Filter + Pitcher Quality Rewrite)
**Estado:** ✅ COMPLETADO (2026-05-29)

### G3.1 Bug #1 — Partidos jugados como picks (RECURRENTE)
- **Causa raíz**: No había filtro de tiempo antes de mandar al LLM. El normalizer aceptaba todo y el scheduler permitía cuentas ya jugadas.
- **Fix defense-in-depth (5 capas)**:
  1. `services/time_filter.py` — utilidades canónicas: `is_match_upcoming`, `is_match_finished`, `filter_upcoming`, `validate_pick_before_output`, `filter_blocked_picks`, `STATUS_FINISHED`.
  2. `analyst_engine.analyze_matches` — Stage 0 filter al INICIO (todos los deportes).
  3. `mlb_day_orchestrator` — Stage 0 después de confirmar pitchers. abort_reason='all_games_already_played_or_finished' si no queda nada.
  4. `parlay_correlation_validator.parlay_builder` — drop picks con status Final / past kickoff antes de construir parlay. `time_blocked` field expuesto.
  5. `server._run_analysis_pipeline` — última línea: `filter_blocked_picks` sobre picks/rescued/watchlist/protected_acceptable. Los bloqueados van a `summary.blocked_picks[]` y `total_recommended` se decrementa.

### G3.2 Bug #2 — Under recomendado incorrecto (Cubs-Pirates 7-2 con Under 4.5)
- **Causa raíz**: `_pitcher_quality_score` solo usaba ERA/WHIP/K-BB, no xERA/FIP.
- **Fix**:
  - Reescritura completa de `_pitcher_quality_score` (mlb_intelligence): prioridad xERA → FIP → xFIP → ERA, con weight extra para xERA/FIP. Incorpora Hard Hit % y Barrel %.
  - Detección de regresión: ERA vs xERA divergence ≥1.0 → tag `_regression_signal = PITCHER_OVERPERFORMING` (penalty -0.15) o `PITCHER_UNDERVALUED` (bonus +0.10).
  - `UNDER_SAFETY_RULES` + `under_pick_passes_safety_rules()`: bloquea Under cuando hay overperforming ace, pitcher quality baja, buffer insuficiente, aperturas insuficientes.
  - `validate_pick_before_output` bloquea Under cuando aparece `PITCHER_OVERPERFORMING`.

### Testing — Iteration 32
- **22/23 tests passed (95.7%)** — solo un test-expectation issue.
- Verificado E2E: `/api/mlb/day?date=2025-08-15` (pasado) ahora devuelve 0 picks con `abort_reason='all_games_already_played_or_finished'`.

---

## Phase G2 — Baseball Savant + Parlay Correlation Validator
**Estado:** ✅ COMPLETADO (2026-05-29)

### G2A — Enrichment Layer
- `services/baseball_savant.py` — `fetch_pitcher_savant()` + `enrich_pitcher_dict()` (xERA/FIP/xFIP/HardHit/Barrel/EV). Cache 24h.
- `services/mlb_team_stats.py` — `get_team_hand_splits()` + `get_team_bullpen_usage()` cache 30min, `_source_url` literal.
- `mlb_day_orchestrator.py` — enriquecimiento paralelo (6s wait_for por task), `MAX_GAMES_PER_CALL=8`, `per_source_urls` completos.

### G2B — Parlay Correlation Validator
- `services/parlay_correlation_validator.py` — reglas positivas/negativas + `parlay_builder()` genérico.
- Orchestrator response añade `parlay_suggested`.

### Testing — Iteration 31
- 22/22 tests passed.

---

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

### G1.1 Filosofía
Motor MLB dedicado a **edge repetible en mercados protegidos**.

### G1.2 Módulos creados
- `services/mlb_pregame_analytics.py` — funciones puras + `mlb_starter_lineup_under_profile`.
- `services/mlb_day_orchestrator.py` — endpoint `GET /api/mlb/day?date=YYYY-MM-DD`.

### G1.3 Testing
- Iteration 30: 76/76.

---

## Phase G4 — Multi-Source Sports Scrapers Wiring (MLB + Basketball)
**Estado:** ✅ COMPLETADO (2026-05-29)

### G4.1 MLB Scrapers (rescate de pitchers/lineups)
- Integrados en `services/external_sources/mlb_lineup_rescue.py`:
  - `rotogrinders_mlb.py` (NEW)
  - `fantasyalarm_mlb.py` (NEW)
  - Añadidos a `ALL_SCRAPERS`, a la ejecución paralela (`asyncio.gather`) y a la prioridad de matching.

### G4.2 Basketball scrapers (telemetría + fallback terciario)
- Nuevo `services/external_sources/basketball_rescue.py`:
  - `rescue_basketball_day(date_str)`
  - `attach_evidence(matches, rescue_payload)`
- Nuevo fallback en `services/data_ingestion.py`:
  - `ingest_basketball_sofascore_fallback()` (NBA-only filter)
  - `normalize_sofascore_basketball_game()`
- `server.py`:
  - tras ESPN NBA fallback vacío, intenta SofaScore (fail-soft)
  - ejecuta rescue telemétrico para adjuntar `_external_evidence` sin afectar football/baseball.

### G4.3 Testing
- Smoke tests de import + endpoint basketball `abort_reason=no_games_all_sources` (sin crash).
- Nota: sandbox sin BrightData ⇒ 403 esperables en SofaScore/Flashscore (comportamiento correcto).

---

## Phase MLB-V2 — MLB Margin & Total Script Engine v2
**Estado:** ⏳ EN PROGRESO

### MLB-V2.0 Restricciones de diseño
- **No reemplazar** `mlb_pregame_analytics.py`.
- Crear capa nueva `mlb_pregame_analytics_v2.py` que **importa** el módulo base y añade lógica avanzada.
- Activación automática **solo** si `sport=baseball`.
- `parlay_builder()` genérico se mantiene intacto (football/basketball no se toca).

### MLB-V2.1 Backend foundation (nuevo módulo v2)
**Entregable:** `/app/backend/services/mlb_pregame_analytics_v2.py`
- `favorite_margin_profile(recent_games)`
  - Últimos 15: wins, wins by 2+, wins by 3+, avg run differential, marginReliability.
- `run_line_dominance_model(ctx)`
  - Predice `marginProjection`, `runLineScore`, `coverProbability`, `confidence`, `fragilityScore`, `reasons`, `risks`.
  - Regla: recomendar `Run Line -1.5` si `runLineScore>=72`, `projectedMargin>=1.8`, winsBy2Rate>=50, lossesBy2Rate>=45, bullpen ok, lineup fuerte.
  - Agrega tag/signal `RUN_LINE_MARGIN_EDGE`.
- `smart_total_line_selector(expected_runs, ctx, market_lines)`
  - Escoge `bestLine`, `safeLine`, `aggressiveLine`, `recommendedLine` con `lineSafetyScore` y `fragilityScore`.
  - Agrega signal `SMART_OVER_LINE_SELECTED`.
- `pitcher_centered_evaluation(ctx)`
  - Gate duro: **no recomendar sin ambos pitchers confirmados**.
  - Emite señales:
    - `STRONG_STARTING_PITCHER_EDGE` (ya existe)
    - `PITCHER_MISMATCH_DETECTED`
    - `LINEUP_VS_PITCHER_EDGE`
- `same_game_correlation_rule(pair_ctx)`
  - Regla positiva Run Line favorito -1.5 + Over (mismo juego) bajo condiciones (expectedRuns alto, projectedTeamRuns>=4.5, margin>=2.0, bullpen rival vulnerable).
  - Emite `SAME_GAME_CORRELATED_PAIR` cuando aplique.
- `classify_pick_type(pick_ctx)`
  - `DOMINANT_FAVORITE_RUN_LINE`, `SMART_LOW_OVER`, `PITCHER_UNDER`, `F5_EDGE`, `TEAM_TOTAL_EDGE`, `SAME_GAME_CORRELATED_PAIR`.
- `mlb_parlay_builder(candidates, max_size=4)`
  - **MLB-only** (no mezclar deportes) y mercados permitidos:
    - Run Line ±1.5, Total Runs O/U, Team Totals O/U, F5 ML, F5 Totals, NRFI/YRFI.
  - Ranking:
    - `finalParlayScore = avgPickScore*0.45 + avgFragilityInverse*0.20 + correlationScore*0.20 + pitcherConfidence*0.15`
  - Reglas: max 4 picks, preferir 2–4, evitar datos incompletos y sin pitchers confirmados, bloquear parlay si correlation_score<60.

### MLB-V2.2 Signal catalog + orchestrator wiring
**Back-end**
- `services/signal_catalog.py`:
  - Añadir códigos: `RUN_LINE_MARGIN_EDGE`, `SMART_OVER_LINE_SELECTED`, `PITCHER_MISMATCH_DETECTED`, `LINEUP_VS_PITCHER_EDGE`, `SAME_GAME_CORRELATED_PAIR`.
  - Mantener `applicable_sports={'baseball'}`.

**Orchestrator** (`services/mlb_day_orchestrator.py`)
- Importar v2:
  - `from .mlb_pregame_analytics_v2 import ...`
- Por juego:
  - Ejecutar `run_line_dominance_model()` y `smart_total_line_selector()` (usando `expected_runs` del predictor base).
  - Agregar bloque `_mlb_script_v2` al `pick_payload` (también en `rescued` si aplica):
    - `marginProjection`, `coverProbability`, `projectedMargin`, `expectedRuns`, `bestLine`, `lineSafetyScore`, `sameGameCorrelation`, `pickType`, `reasons`, `risks`.
  - No alterar lógica para football/basketball.
- Parlay:
  - Reemplazar (solo en MLB orchestrator) el call a `parlay_builder()` por `mlb_parlay_builder()`.
  - Mantener el validador genérico para el resto del sistema.

**Storage hook (P1 mínimo, feedback loop P2)**
- Persistir campos mínimos en el doc de pick (o en el match asociado) sin recalibración automática:
  - `market`, `lineSelected`, `expectedRuns`, `projectedMargin`, `marginProjection`, `coverProbability`, `pickType`, `result` placeholder.

### MLB-V2.3 Frontend — MLBScriptPanel (solo baseball)
- Crear `/app/frontend/src/components/MLBScriptPanel.jsx`
  - Colapsable.
  - Renderiza:
    - Pitcher matchup
    - `Projected margin`, `Cover probability`
    - `Expected runs`, `Best total line`, `lineSafetyScore`
    - `sameGameCorrelation` (nota de correlación)
    - `whyThisParlayWorks`, `whyThisParlayCanFail` (cuando existan en payload)
    - `tipo de pick`.
- `MatchCard.jsx`:
  - Montar `MLBScriptPanel` **solo** si `sport === 'baseball'`.
  - No modificar UI base de basketball/football.

### MLB-V2.4 Testing (backend)
- Unit tests (ctx sintéticos) para:
  - `favorite_margin_profile`, `run_line_dominance_model`, `smart_total_line_selector`, `mlb_parlay_builder`.
- E2E:
  - `GET /api/mlb/day?date=YYYY-MM-DD` (regresión + campos nuevos presentes).
  - `POST /api/analysis/run` sport=baseball (no timeouts, no crash; parlay MLB-only).
- Guardarraíles:
  - Confirmar que football/basketball **no** incluyen `_mlb_script_v2` ni cambian su parlay.

---

## 3) Next Actions

### A) MLB Margin & Total Script Engine v2 (P0) — inmediato
1. Crear `mlb_pregame_analytics_v2.py`.
2. Añadir señales nuevas al `signal_catalog.py`.
3. Wire en `mlb_day_orchestrator.py`:
   - adjuntar `_mlb_script_v2` por pick
   - usar `mlb_parlay_builder()` para parlay_suggested.
4. Crear `MLBScriptPanel.jsx` y montarlo en `MatchCard.jsx` (baseball-only).
5. Añadir storage hook mínimo (sin recalibración automática).

### B) Bright Data Unlocker (P1)
1. Añadir `.env` keys y `brightdata_fetcher.py`.
2. Activar unlocker para Sportytrader/BeSoccer/scores24.
3. Añadir 2–3 fuentes NBA/basketball al registry con `requires_unlocker=True`.

### C) Basketball Historical Detail (P1)
1. Implementar profile + integración pipeline.
2. Añadir rescue layer totales/team totals.
3. UI “Historial profundo”.

### D) Baseball Historical Detail (P1)
1. Implementar profile + rescue + UI.

---

## 4) Success Criteria
- Market tolerance y rescue layers funcionan sin inventar valor.
- LIVE multi-deporte estable; sin zombies; sin fugas de vocabulario.
- Editorial Context:
  - Scrapy/Playwright/BrightData degradan elegante.
  - Fuentes bloqueadas se desbloquean con Unlocker cuando procede.
  - UI muestra contexto con fuentes y warnings.
- Historical Detail Enrichment:
  - Ningún match basketball/baseball prioritario se descarta sin histórico profundo.
  - Se detectan oportunidades en **totales/team totals/F5/run line** con razonamiento humano.
  - Moneyball guardrail siempre manda: sin edge → no recomendación.
- MLB-V2:
  - Picks MLB incluyen `Projected Margin`, `Cover Probability`, `Best Total Line`, `lineSafetyScore`, `pickType`.
  - Parlays MLB-only de 2–4 picks con correlación ≥60.
  - Run Line -1.5 solo cuando hay dominancia real (no “favoritos por 1 carrera”).
  - **Cero regresiones**: endpoints existentes responden, `_market_edge` intacto, `asyncio.wait_for(timeout=3.0)` intacto, narrativa ES intacta.
  - Regla crítica cumplida: **no tocar basketball/football**.
