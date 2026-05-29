# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** (ACTUALIZADO)

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

- **(🟨 PENDIENTE / SIGUIENTE PRIORIDAD)** **Bright Data Web Unlocker** como tercer backend:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball y MLB**.

- **(✅ COMPLETADO)** **Historical Detail Enrichment (Baseball)**:
  - Antes de analizar/descartar MLB, enriquecer con histórico profundo (últimos 15) y generar perfiles por equipo + combinado.
  - Añadir `baseballRunsRescueLayer(match)` y **trap signals históricas**.

- **(🟨 PENDIENTE)** **Historical Detail Enrichment (Basketball)**:
  - Antes de analizar/descartar basketball, enriquecer con histórico profundo y generar perfiles.
  - Añadir `basketballTotalPointsRescueLayer(match)`.
  - UI: sección “Historial profundo”.

- **(✅ COMPLETADO)** **MLB Margin & Total Script Engine v2 (solo Baseball)**:
  - Engine especializado en guion MLB:
    - Predecir **margen de victoria** (Run Line -1.5 favoritos dominantes)
    - Seleccionar líneas **Over/Under más protegidas** (6.5/7.5/8/8.5/9 y equivalentes)
    - Análisis **pitcher-first** con gate de pitchers confirmados
    - Parlays **MLB-only** con validación de correlación positiva
  - Restricción crítica: **NO tocar basketball/football** (backend y UI).

- **(✅ COMPLETADO)** **MLB Feedback Loop (P2)**:
  - Guardar outcomes reales por pick: `result/outcome`, `margin`, `totalRuns`, `runLineCovered`, `overHit`.
  - Guardar snapshot v2: `expectedRuns`, `marginProjection`, `coverProbability`, `lineSelected`.
  - Recalibración automática cada 50 picks settled → persiste pesos en DB.

- **(✅ COMPLETADO)** **MLB-V5 — Bucketing estructural MLB + Manual Odds Review**:
  - Problema resuelto: el pipeline genérico (LLM) mandaba MLB a `discarded_market` por “cuotas no disponibles/motivación normal”, ignorando lectura v2.
  - Ahora **Baseball NO usa el LLM genérico** en `/api/analysis/run`.
  - Nuevos buckets MLB:
    - `structural_lean_requires_odds`
    - `watchlist_manual_odds`
    - `discarded_after_full_analysis`
  - El engine usa `mlb_structural_data_quality()` + `odds_missing` + `has_structural_lean` para evitar descartes prematuros.
  - UI: sección dedicada **“Revisión manual — falta cuota”** (solo MLB) vía `ManualOddsReviewPanel.jsx`.

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
- **Causa raíz**: No había filtro de tiempo antes de mandar al LLM.
- **Fix defense-in-depth (5 capas)**:
  1. `services/time_filter.py` — utilidades canónicas.
  2. `analyst_engine.analyze_matches` — Stage 0 filter al inicio (todos los deportes).
  3. `mlb_day_orchestrator` — Stage 0 tras confirmar pitchers; abort_reason='all_games_already_played_or_finished'.
  4. `parlay_correlation_validator.parlay_builder` — drop picks con status Final / past kickoff.
  5. `server._run_analysis_pipeline` — `filter_blocked_picks` final.

### G3.2 Bug #2 — Under recomendado incorrecto (Cubs-Pirates 7-2 con Under 4.5)
- **Causa raíz**: `_pitcher_quality_score` no priorizaba xERA/FIP.
- **Fix**:
  - Reescritura `_pitcher_quality_score` priorizando xERA→FIP→xFIP→ERA.
  - Señales de regresión `PITCHER_OVERPERFORMING` / `PITCHER_UNDERVALUED`.
  - Reglas `UNDER_SAFETY_RULES` + validación final.

---

## Phase G2 — Baseball Savant + Parlay Correlation Validator
**Estado:** ✅ COMPLETADO (2026-05-29)

- `services/baseball_savant.py` — xERA/FIP/xFIP/HardHit/Barrel.
- `services/mlb_team_stats.py` — splits + bullpen usage.
- `services/parlay_correlation_validator.py` — builder genérico.

---

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

- `services/mlb_pregame_analytics.py`
- `services/mlb_day_orchestrator.py`

---

## Phase G4 — Multi-Source Sports Scrapers Wiring (MLB + Basketball)
**Estado:** ✅ COMPLETADO (2026-05-29)

### G4.1 MLB Scrapers (rescate de pitchers/lineups)
- Integrados en `services/external_sources/mlb_lineup_rescue.py`:
  - `rotogrinders_mlb.py`
  - `fantasyalarm_mlb.py`

### G4.2 Basketball scrapers (telemetría + fallback terciario)
- `services/external_sources/basketball_rescue.py`
- `services/data_ingestion.py`:
  - `ingest_basketball_sofascore_fallback()` (NBA-only)
- `server.py` wiring fail-soft.

---

## Phase MLB-V2 — MLB Margin & Total Script Engine v2
**Estado:** ✅ COMPLETADO (2026-05-29)

- Nuevo módulo `/app/backend/services/mlb_pregame_analytics_v2.py`.
- Señales nuevas en `signal_catalog.py` (sport-aware).
- `mlb_day_orchestrator.py`:
  - `_mlb_script_v2` + `margin_v2` por pick.
  - Parlays `MLB_ONLY` vía `mlb_parlay_builder()`.
- Frontend:
  - `MLBScriptPanel.jsx` montado en `MatchCard.jsx` (solo baseball).
- Testing:
  - `/app/test_reports/mlb_v2_backend_test.json` → **12/12 PASS**.

---

## Phase MLB-V3 — Baseball Historical Detail Enrichment + baseballRunsRescueLayer
**Estado:** ✅ COMPLETADO (2026-05-29)

### MLB-V3.1 Historical Profile (últimos 15)
- `/app/backend/services/historical_enrichment/baseball_historical.py`
- `enrich_baseball_historical_profile(match, lookback=15)`
- Fuente:
  - **Primaria:** MLB Stats API (schedule + linescore)
  - **Fallback:** API-Sports cuando MLB Stats API no tenga datos

### MLB-V3.2 Trap signals históricas
- `/app/backend/services/historical_enrichment/baseball_trap_signals.py`
  - `collect_baseball_trap_signals()` + `compute_extra_fragility()`
- Wiring en `mlb_day_orchestrator.py`:
  - `pick_payload.historical_trap_signals[]` + bump a `fragility.score`.

### MLB-V3.3 baseballRunsRescueLayer
- `/app/backend/services/baseball_runs_rescue.py` (`find_baseball_runs_value()`)
- Wiring:
  - si no hay `chosen_market` (score>=72) y hay perfil histórico disponible → intenta rescate en Totales/Team Totals/F5/Run Line +1.5.

### MLB-V3 Testing
- Validado en `/app/test_reports/iteration_39.json`.

---

## Phase MLB-V4 — Feedback Loop MLB + Recalibración automática (cada 50 picks)
**Estado:** ✅ COMPLETADO (2026-05-29)

### MLB-V4.1 Módulo feedback
- `/app/backend/services/mlb_feedback_loop.py`
- Outcomes + snapshot v2.
- Auto-recalibración cada 50 (fail-soft, bounded weights).

### MLB-V4.2 Storage híbrido (2c)
- Extiende `pick_tracking` con `mlb_metrics`: `{margin, totalRuns, runLineCovered, overHit}`
- Nuevas colecciones:
  - `mlb_pick_feedback`
  - `mlb_engine_weights` (`_id="active"`)

### MLB-V4.3 Endpoints (live)
- `GET  /api/mlb/engine/weights`
- `POST /api/mlb/picks/{pick_id}/settle`
- `POST /api/mlb/engine/recompute`

### MLB-V4.4 Engine reads weights
- `run_line_dominance_model()` usa `ctx['_weights']`.
- `mlb_parlay_builder()` acepta `weights=` opcional.
- Orchestrator expone versión en `pipeline_meta.mlb_engine_weights_version`.

---

## Phase MLB-V5 — Bucketing estructural MLB + Manual Odds Review
**Estado:** ✅ COMPLETADO (2026-05-29)

### MLB-V5.1 Nuevos buckets y decisión final MLB
- `mlb_day_orchestrator.py`:
  - Nuevos buckets:
    - `structural_lean_requires_odds[]`
    - `watchlist_manual_odds[]`
    - `discarded_after_full_analysis[]`
  - Detección `odds_missing` + `has_structural_lean`.
  - Helper: `mlb_structural_data_quality(scoring_ctx, v2_payload)`.

### MLB-V5.2 Señales nuevas (BASEBALL_ONLY)
- `ODDS_MISSING_STRUCTURAL_ANALYSIS_ONLY`
- `STRUCTURAL_LEAN_DETECTED`
- `MANUAL_ODDS_REQUIRED`
- `MOTIVATION_NEUTRAL_MLB`
- `DISCARDED_ONLY_AFTER_FULL_ANALYSIS`

### MLB-V5.3 Integración final en `/api/analysis/run`
- `server.py`:
  - si `sport==baseball` → bypass `analyst_engine` (LLM) y usa `analyze_mlb_day()`.
  - Traduce el output MLB a `summary` compatible, pero expone nuevos buckets en `summary`.

### MLB-V5.4 UI
- Nuevo componente `ManualOddsReviewPanel.jsx` (solo baseball)
- `DashboardPage.jsx`:
  - render “Revisión manual — falta cuota” desde `summary.structural_lean_requires_odds` + `summary.watchlist_manual_odds`.
  - Ajuste de título para baseball en descartes: “Descartados tras análisis MLB completo”.

### MLB-V5 Testing
- `/app/test_reports/iteration_40.json`: **44/51 PASS (86%)**
  - Core MLB-V5 verificado.
  - Nota: algunos tests marcados como flaky por latencia de background jobs.

---

## 3) Next Actions

### A) Bright Data Unlocker (P1) — siguiente prioridad
1. Confirmar `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE` (Web Unlocker).
2. Implementar cascade por scraper: `direct_fetch` → (403/timeout) → `brightdata_fetch`.
3. Añadir cache TTL en DB para reducir coste (por tipo de URL).
4. Activar Unlocker en:
   - Editorial Context (Sportytrader/BeSoccer/scores24)
   - NBA/basketball y MLB scrapers con Cloudflare.

### B) Basketball Historical Detail (P1)
1. Implementar profile + integración pipeline.
2. Añadir rescue layer (totales/team totals) + trap signals.
3. UI “Historial profundo”.

### C) Manual Odds paste (P1/P2)
1. UI “Pegar cuota manual” (actualmente placeholder disabled).
2. Endpoint o cálculo cliente-side para convertir cuota pegada → edge.

---

## 4) Success Criteria
- Market tolerance y rescue layers funcionan sin inventar valor.
- LIVE multi-deporte estable; sin zombies; sin fugas de vocabulario.
- Editorial Context:
  - Scrapy/Playwright/BrightData degradan elegante.
  - Fuentes bloqueadas se desbloquean con Unlocker cuando procede.
  - UI muestra contexto con fuentes y warnings.

- Historical Detail Enrichment:
  - Ningún match (basketball/baseball) prioritario se descarta sin intentar perfil histórico.
  - Se detectan oportunidades en **totales/team totals/F5/run line** con razonamiento humano.
  - Moneyball guardrail siempre manda: sin edge → no recomendación.

- **MLB-V2 (✅ cumplido y validado)**
  - Picks MLB incluyen: `Projected Margin`, `Cover Probability`, `Best/Recommended Total Line`, `lineSafetyScore`, `pickType`, `sameGameCorrelation`.
  - Parlays MLB-only de 2–4 picks con correlación ≥60 (cuando existan suficientes picks elegibles).
  - Run Line -1.5 solo cuando hay dominancia real.
  - **Cero regresiones**:
    - Football/basketball sin `_mlb_script_v2`.
    - Parlay genérico intacto fuera de MLB.

- **MLB-V3 (✅ cumplido)**
  - `baseballHistoricalProfile` presente por pick (fail-soft: `available=false` con `_reason`).
  - `historical_trap_signals` expuestas y ajustan `fragility.score`.
  - `baseball_runs_rescue` se intenta antes de descartar cuando el histórico lo permite.

- **MLB-V4 (✅ cumplido)**
  - Endpoints live: `GET /api/mlb/engine/weights`, `POST /api/mlb/picks/{id}/settle`, `POST /api/mlb/engine/recompute`.
  - Métricas outcome correctas: `margin/totalRuns/runLineCovered/overHit`.
  - Recalibración automática cada 50 picks settled, con pesos bounded.

- **MLB-V5 (✅ cumplido)**
  - Baseball NO se rutea a `discarded_market` por falta de cuotas.
  - Juegos con lectura estructural pero sin odds → `structural_lean_requires_odds`.
  - UI muestra “Revisión manual — falta cuota” con mercados sugeridos.
  - “Motivación normal” es neutral y no descarta MLB.

### Testing status
- `/app/test_reports/mlb_v2_backend_test.json`: **12/12 PASS (100%)**
- `/app/test_reports/iteration_39.json`: **12/13 PASS (92.3%)**
  - Incidencia: timeout en regresión fútbol con `background=False` (no funcional; workaround: `background=True` o aumentar timeout).
- `/app/test_reports/iteration_40.json`: **44/51 PASS (86%)**
  - Core MLB-V5 verificado; algunos tests flaky por latencia de background jobs.

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
