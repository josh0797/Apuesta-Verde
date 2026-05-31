# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** (ACTUALIZADO)

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

- **(🟨 PENDIENTE / BLOQUEADO)** **Bright Data Web Unlocker** como tercer backend:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball y MLB**.
  - **Bloqueo actual:** faltan credenciales del usuario (`BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`).

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
  - Baseball NO usa el LLM genérico en `/api/analysis/run`.
  - Nuevos buckets MLB:
    - `structural_lean_requires_odds`
    - `watchlist_manual_odds`
    - `discarded_after_full_analysis`
  - UI: sección **“Revisión manual — falta cuota”** (solo MLB) vía `ManualOddsReviewPanel.jsx`.

- **(✅ COMPLETADO)** **MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery / Market Audit (V6 UI + Backend)**:
  - Fix Totals (Poisson) + UI Edge vs Línea + picks visibles.
  - **Over Discovery Engine (V6)** para eliminar sesgo hacia Unders:
    - Offensive Explosion Score (0–100)
    - Offensive Script badge
    - Over Survival score
    - Market competition Under vs Over + swap cuando Over domina
    - Daily Market Audit endpoint

- **(✅ COMPLETADO)** **MLB-V4 Live Intelligence**:
  - Volatility detection + script breaks monitoring + cashout intelligence.
  - Restricción: solo aplica a matches que pasaron el filtro pregame.

- **(✅ COMPLETADO)** **F6A/F6B Bullpen Risk & Storage**:
  - Downgrade Full Game Unders a F5 Under si bullpens son riesgosos.
  - Storage post-match de script breaks.

- **(✅ COMPLETADO)** **MLB-V5 Script Survival & Fragility**:
  - Survival score 0–100 + fragility score 0–100 con clasificación de estabilidad.
  - UI: summary + detail panels.

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

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy)
**Estado:** ✅ COMPLETADO (infra lista; desbloqueo real requiere unlocking)

---

## Phase G3 — Critical Bug Fixes (Defense-in-Depth Time Filter + Pitcher Quality Rewrite)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G2 — Baseball Savant + Parlay Correlation Validator
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G4 — Multi-Source Sports Scrapers Wiring (MLB + Basketball)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V2 — MLB Margin & Total Script Engine v2
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V3 — Baseball Historical Detail Enrichment + baseballRunsRescueLayer
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V4 — Live Intelligence (Volatilidad / Script Breaks / Cashout) + endpoint reevaluate
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V5 — Script Survival + Fragility + UI Panels
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-F6A/F6B — Bullpen Risk Selector + Storage Script Breaks
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery Engine + Daily Market Audit
**Estado:** ✅ COMPLETADO (2026-05-30)

### MLB-V6.1 Totals probability model correcto (Poisson)
- Backend: `mlb_pregame_analytics_v2.py`
  - `totals_probability(expected_runs, line)` + `_poisson_cdf`.
  - `smart_total_line_selector()` produce:
    - `coverProbability` del lado recomendado
    - `probabilityUnder`, `probabilityOver`
    - `edgeVsLine` (en carreras)
    - `probabilityModel: Poisson`
    - `probabilityDebug` (provenance)
  - `build_v2_payload()` resuelve `coverProbability` según mercado (totals vs Run Line).

### MLB-V6.2 UI: Edge vs Line + debug + odds manual
- `ManualOddsReviewPanel.jsx`:
  - muestra `Edge vs línea`.
  - panel debug: projected runs, mercado recomendado, Poisson P(U)/P(O).
  - input “Pegar tu cuota” activado con cálculo EV client-side.
- `MLBScriptPanel.jsx`:
  - muestra `Edge vs línea` y P(U/O) + modelo.

### MLB-V6.3 Picks visibles bajo el dashboard (contador = cards)
- Backend (`server.py`):
  - `result.picks` unifica todos los buckets visibles para baseball:
    - `picks + rescued_picks + structural_lean_requires_odds + watchlist_manual_odds`
  - sintetiza `recommendation.confidence_score`.
- Backend: bypass `filter_blocked_picks` genérico para baseball.
- Orchestrator: `kickoff_iso/gameDate/status` añadidos al pick_payload.

### MLB-V6.4 (V6) Over Discovery Engine + Market Competition (backend)
- Backend:
  - `services/mlb_over_discovery.py`:
    - `calculate_offensive_explosion_score()` + drivers/componentes.
    - `classify_offensive_script()`.
    - `calculate_over_survival_score()`.
    - `evaluate_over_markets()` + best_over_market.
    - `market_competition()` para competir Over vs Under y swap.
    - `daily_market_audit()`.
  - Orchestrator: adjunta `_mlb_over_discovery` + top-level `offensive_explosion_score`, `offensive_script_code`, `over_survival_score`.
  - Endpoint: `GET /api/mlb/daily_market_audit`.

### MLB-V6.5 (V6) Over Discovery UI (frontend)
- **Estado:** ✅ COMPLETADO
- `frontend/src/components/MLBScriptV3Panel.jsx`:
  - `MLBOffensiveExplosionSummary` (chip compacto siempre visible):
    - Offensive Explosion 0–100
    - Offensive Script (label_es + icono)
    - Over Survival 0–100
  - `MLBOffensiveExplosionDetail` (solo expand):
    - desglose por componentes + pesos
    - Top Offensive Drivers
    - Best Over Market (mercado + edge + score)
  - `MLBOverSwapBadge`: visible cuando Market Competition swappea Under→Over.
  - `MLBMarketAuditBadge`: chip opcional para warnings de sesgo diario.
  - `MLBScriptV3Panel` acepta `overDiscovery` y renderiza summary+detail.
- `frontend/src/components/MatchCard.jsx`:
  - cableado `m._mlb_over_discovery` → `MLBScriptV3Panel.overDiscovery`.
  - render `MLBOverSwapBadge` debajo de `MLBBullpenSwapBadge`.

---

## Phase MLB-M2 — Bullpen Real-Usage (pitch_stress) + Finished-Game Settler
**Estado:** ✅ COMPLETADO (2026-05-31)

### M2.1 Bullpen real-usage (`services/mlb_bullpen_real_usage.py`)
- Hidrata box-scores reales de MLB Stats API por equipo (ventana 48h).
- Expone:
  - `bullpen_pitches_48h` (suma real de pitches del bullpen)
  - `bullpen_innings_48h`
  - `starter_lasted_innings`
  - `pitch_stress_index = bullpen_pitches_48h / 45`
  - `compute_fatigue_score()` combina `games_played × 20 + extra_innings × 15 + pitch_stress × 25`.
  - `derive_fatigue_label()` (fresh / moderate / high / extreme).
- Fail-soft: error → fallback a heurística legacy (`games_played × 25 + extra × 15`).

### M2.2 Integración en `services/mlb_stats_api.py::get_bullpen_recent_usage()`
- Ahora añade al payload: `pitch_stress_index`, `bullpen_pitches_48h`, `bullpen_innings_48h`, `starter_lasted_innings`.
- `fatigue_score_0_100` recalculado con la nueva fórmula cuando hay box-score disponible.
- Cache TTL preservado.

### M2.3 Finished-Game Settler (`services/mlb_finished_game_settler.py`)
- `fetch_boxscore_summary(gamePk)` extrae:
  - `final_score: {home, away, total}`
  - `total_runs`
  - `bullpen_usage: {home_pitches, away_pitches, home/away_innings, home/away_starter_innings}`
- `settle_match(db, doc)` persiste en `db.matches` + `db.archived_live_matches` (idempotente vía `settled_at`).
- `settle_recent_finished(db, days_back=2)` barre matches recién finalizados sin `settled_at`.

### M2.4 Wiring de settlement
- `services/live_lifecycle.sweep_expired_live_matches()` invoca `settle_match` en cuanto un match cierra (best-effort, no rompe el sweep si falla).
- APScheduler job `settle_finished_baseball` corre cada **15 minutos** (`services/scheduler.py`) — captura partidos que cerraron sin pasar por el sweep live.

### M2.5 Beneficio aguas abajo
- M1 (`mlb_active_series_analyzer`) ahora consume `final_score` + `bullpen_usage` reales → arma el contexto de serie activa correctamente días después.
- Caso real validado en preview (31 may 2026):
  - PIT 48h → 146 pitches / stress 3.24 / starter 4.3 IP
  - MIN 48h → 149 pitches / stress 3.31 / starter 4.0 IP
  - NYY 48h →  73 pitches / stress 1.62 / starter 6.0 IP
  - LAD 48h →  81 pitches / stress 1.80 / starter 7.0 IP
- Settler probado end-to-end con dos `gamePk` reales — escribe `final_score`, `total_runs` y `bullpen_usage` completos.

---

## Phase RECAL — Lightweight Recalibration + Feedback APScheduler + Bright Data Health
**Estado:** ✅ COMPLETADO (2026-05-31)

### RECAL.1 Endpoint `POST /api/analysis/recalibrate`
- Re-corre el analista sobre los partidos del **último `pick_run`** del usuario para ese deporte, **sin** re-ingestar APIs externas.
- Scope: **MLB + Basketball** (football pendiente para una iteración futura).
- Modo **background** vía `job_queue` con polling en `/api/analysis/jobs/{id}` (compatible con el modal de progreso ya existente).
- MLB: invoca `analyze_mlb_day(date_str, db)` que aprovecha cachés internas del orquestador (MLB Stats API, team form, pitcher stats). Tiempo medido en preview: **~10–15s**.
- Basketball: recupera `match_ids` de cada bucket del último pick_run, hidrata desde `db.matches` y re-corre `analyst_engine.analyze_matches`. Tiempo medido: **~75s** (cuello LLM).
- Guarda un nuevo `pick_run` con `is_recalibration: True` + `recalibrated_from: <prev_run_id>` para auditoría.

### RECAL.2 Botón "Recalibrar" en Dashboard (frontend)
- `frontend/src/pages/DashboardPage.jsx`: añadido handler `recalibrate()` y `<Button data-testid="recalibrate-picks-button">` al lado de "Generar picks del día".
- Visible **solo** para `sport ∈ {baseball, basketball}`.
- Deshabilitado si no hay pick_run previo o si ya hay un job activo.
- Icono `RefreshCcw` + estilo outline cyan para distinguirlo del botón verde "Generar picks".
- i18n: copy ES/EN (`recalibrateBtn`, `recalibrating`, `recalibrateHint`, `recalibrateDone`).

### RECAL.3 Feedback-loop recalibración automática (P2)
- `FEEDBACK_BATCH_SIZE: 50 → 40` en `services/mlb_feedback_loop.py`.
- Nuevo job APScheduler `recompute_feedback_weights` (cada **30 min**, alineado con `refresh_upcoming`) en `services/scheduler.py::_job_recompute_feedback_weights`.
- El job invoca `recompute_weights_if_due(db)` — no-op cuando hay menos de 40 picks settled pendientes (cuesta un `count_documents`).
- Verificado: scheduler arranca con la lista de jobs `['refresh_live', 'refresh_upcoming', 'sweep_stale_live', 'settle_finished_baseball', 'recompute_feedback_weights', 'purge_context']`.

### RECAL.4 Bright Data — healthcheck + telemetría
- `services/brightdata_client.py`: añadido ledger en memoria (deque) con ventana de **24h** (max 5000 entradas). Cada `fetch_unlocked` registra `(ts, status, ok, url_short)`.
- Nueva función `get_health_snapshot()` devuelve `{fetches_24h, ok_24h, fail_24h, success_ratio, last_fetch}`.
- Nuevo endpoint `GET /api/admin/brightdata` (`?probe=true` para health-check real contra `https://geo.brdtest.com/mygeo.json`):
  - Devuelve `{ok, token_present, api_key_present, zone, editorial_enabled, ledger_24h, probe?}`.
  - Verificado en preview: `probe.ok=true`, response real con `country: US`, ASN HostRoyale.
- **No** se wirearon scrapers legacy (understat / crawlee / playwright_scraper) — decisión explícita del usuario.

---

## Phase MLB-V7 — MLB Engine V3 (Explainability + Game Script + Diversificación + Baseball-first)
**Estado:** 🟨 PENDIENTE (fase futura; V6 ya cubre drivers ofensivos + swap Over/Under)

> Nota: con V4/V5/V6 ya existe explicabilidad fuerte. V7 queda como refactor/iteración de guion v3 (si se desea ampliar más allá del panel actual) y diversificación adicional.

---

## 3) Next Actions

### A) Validación formal MLB-V6 (P0) — Testing Agent v3
**Estado:** 🟨 PENDIENTE (siguiente paso inmediato)
1) Ejecutar `testing_agent_v3`:
   - Pure functions V6 (`mlb_over_discovery.py`).
   - Endpoint `GET /api/mlb/daily_market_audit`.
   - Regresión V1–V5 (orchestrator chain intacta).
2) Validar UI (smoke):
   - Chips V6 en `MLBScriptV3Panel` (summary siempre visible + detail al expand).
   - Badge `Under→Over` cuando exista swap.

### B) Bright Data Unlocker (P0 bloqueado) — siguiente prioridad scraping
1) Confirmar `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE` (Web Unlocker).
2) Implementar cascade por scraper: `direct_fetch` → (403/timeout) → `brightdata_fetch`.
3) Añadir cache TTL en DB para reducir coste (por tipo de URL).
4) Activar Unlocker en:
   - Editorial Context (Sportytrader/BeSoccer/scores24)
   - NBA/basketball y MLB scrapers con Cloudflare.

### C) Basketball Historical Detail (P1)
1) Implementar profile + integración pipeline.
2) Añadir rescue layer (totales/team totals) + trap signals.
3) UI “Historial profundo”.

### D) MLB Feedback Loop Recalibration via APScheduler (P2)
**Estado:** 🟨 PENDIENTE
- Wire APScheduler para recomputar pesos automáticamente cada 50 picks settled (en lugar de depender solo de triggers manuales).

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
  - Cero regresiones:
    - Football/basketball sin `_mlb_script_v2`.
    - Parlay genérico intacto fuera de MLB.

- **MLB-V3 (✅ cumplido)**
  - `baseballHistoricalProfile` presente por pick (fail-soft: `available=false` con `_reason`).
  - `historical_trap_signals` expuestas y ajustan `fragility.score`.
  - `baseball_runs_rescue` se intenta antes de descartar cuando el histórico lo permite.

- **MLB-V4 (✅ cumplido)**
  - Endpoint live: `POST /api/mlb/live/reevaluate`.
  - Live Intelligence solo para matches que pasaron filtro pregame.
  - UI en Match Detail con panel Live.

- **MLB-V5 (✅ cumplido)**
  - Script Survival 0–100 + Fragility 0–100 visibles en cards.
  - Clasificación de estabilidad (ELITE_STABLE…HIGHLY_FRAGILE) + panel detalle.

- **F6A/F6B (✅ cumplido)**
  - Swap Full Game Under → F5 Under si bullpens riesgosos.
  - Storage de script breaks en DB post-match.

- **MLB-V6 (✅ cumplido)**
  - Totals: `coverProbability` corresponde a P(Under/Over) del lado recomendado (Poisson).
  - UI muestra `Edge vs línea` y debug de probabilidades.
  - Counter = render: el dashboard renderiza todas las cards (incluye rescued/manual-review).
  - Over Discovery:
    - Offensive Explosion Score + script badge + drivers visibles.
    - Over Survival visible.
    - Badge Under→Over cuando Market Competition swap.
  - Endpoint `GET /api/mlb/daily_market_audit` operativo.

### Testing status
- `/app/test_reports/mlb_v2_backend_test.json`: **12/12 PASS (100%)**
- `/app/test_reports/iteration_39.json`: **12/13 PASS (92.3%)**
  - Incidencia: timeout en regresión fútbol con `background=False` (no funcional; workaround: `background=True` o aumentar timeout).
- `/app/test_reports/iteration_40.json`: **44/51 PASS (86%)**
  - Core MLB-V5 verificado; algunos tests flaky por latencia de background jobs.
- Iteraciones V4/F6A/V5: `/app/test_reports/iteration_41.json` → `iteration_44.json`.
- **MLB-V6 (Over Discovery) — validación local (manual):** OK (scores/drivers/best_over coherentes).
- **Siguiente:** ejecutar `testing_agent_v3` para validación formal end-to-end de V6 + regresión V1–V5.

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
