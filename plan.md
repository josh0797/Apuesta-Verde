# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** (ACTUALIZADO)

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

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

### G1.1 Filosofía
Motor MLB dedicado a **edge repetible en mercados protegidos** (NRFI, F5, Team Totals, Run Line +1.5, Under con soporte estadístico). NO persigue cuotas altas — busca dominancia real (pitchers, bullpen, ofensa, parque, fragilidad).

### G1.2 Módulos creados
- `services/mlb_pregame_analytics.py` — 9 funciones puras:
  - `starting_pitcher_edge()`, `pitcher_quality_score()` con detección PITCHER_OVERPERFORMING/UNDERVALUED (xERA divergence ≥1.20)
  - `bullpen_fatigue_score()` con guardrails 8+/10+ IP en 48h
  - `offense_vs_pitcher_type()` (LHP/RHP)
  - `park_factor_analyzer()` (Coors/Oracle/etc.) + Weather Impact Score
  - `mlb_fragility_score()` 0-100 con labels MUY_PROTEGIDO/PROTEGIDO/RIESGO_MEDIO/FRAGIL
  - `run_line_predictor()` + RUN_LINE_TRAP guardrail (bullpen_era_7d>4.75 ∨ ip_48h>8 ∨ 1run_win%>40%)
  - `over_under_predictor()` con `expected_runs` model
  - `nrfi_yrfi_analyzer()` con 1st-inning specific stats + top-3 lineup
  - `mlb_alternative_rescue()`
  - `emit_signals()` con `source_url` literal de confirmación
- `services/mlb_day_orchestrator.py` — `analyze_mlb_day(date_str, db)`. Confirma pitchers via `statsapi.mlb.com/api/v1/schedule?hydrate=probablePitcher` (URL literal incluida en `pitcher_confirmation_source_url` y en cada signal).
- `signal_catalog.py` — 8 nuevos códigos BASEBALL_ONLY: PITCHER_OVERPERFORMING, PITCHER_UNDERVALUED, RUN_LINE_TRAP, STRONG_PITCHER_EDGE, PARK_OVER_SIGNAL, PARK_UNDER_SIGNAL, NRFI_SIGNAL, YRFI_SIGNAL + RESCUED_MARKET (ALL_SPORTS).
- `server.py` — Nuevo endpoint `GET /api/mlb/day?date=YYYY-MM-DD`.

### G1.3 Testing
- **Iteration 30: 76/76 tests passed (100%)** incluyendo todas las unit tests de scoring + integración endpoint + sport-aware regression (PITCHER_OVERPERFORMING → None para football/basketball).
- 15 partidos MLB confirmados en 2025-08-15; cada uno con `pitcher_confirmation_source_url` literal.

### G1.4 Diferido a G2
- `parlay_builder()` (top 3-4 picks combinados)
- `feedback_loop()` con recalibración cada 50 juegos
- `historical_matchup_memory` colección Mongo
- Enriquecimiento avanzado: Baseball Savant (xERA / FIP / Hard Hit % / Barrel %) + bullpen detallado + weather feed
- UI MLB dashboard dedicado (signals + source_url ya se ven en Phase E1 panel)

---

## Phase E1 — Editorial Context Signals Transparency
**Estado:** ✅ COMPLETADO (2026-05-28)

### E1.1 Backend
- Nuevo `services/signal_catalog.py` — 33 códigos canónicos con label/severity/category/signal_type (positive|negative|neutral)/explanation/default_impact/applicable_sports.
- Sport-aware (validado en tests):
  - `RED_CARD_CONTEXT` ✅ fútbol, ❌ basketball, ❌ baseball
  - `CORNER_VOLUME_DETECTED` ✅ fútbol únicamente
  - `PACE_OVER_SIGNAL` ✅ basketball únicamente
  - `PITCHER_DUEL_SIGNAL`, `BULLPEN_FATIGUE_SIGNAL` ✅ baseball únicamente
  - Editoriales (INJURY/MARKET/MOTIVATION/CONTRADICTION) ✅ todos los deportes
- Nuevo `services/signal_aggregator.py` — función pura `aggregate_signals_for_payload(payload, sport)`. Unifica:
  - trap_signals_structured (moneyball)
  - editorial_context.signals (Scrapy/Playwright/BrightData)
  - form_guard.signals
  - protected_market / fragility (alternative_rescue)
  - histórico (encounter_history, basketball_pace_form, baseball_stats)
- `build_signal_summary()` para el resumen global.
- Hook Phase 13 en `analyst_engine.analyze_matches`: adjunta `editorial_context_signals` a TODOS los buckets (picks, disc_mot, disc_mkt, incomp, rescued, watchlist, protected_acc) + `summary.editorial_signal_summary` + `_pipeline.editorial_signal_aggregation`.
- Bug fix encontrado por testing agent: `parsed['_pipeline'] = pipeline_meta` sobrescribía la metadata recién añadida. Cambiado a merge.

### E1.2 Frontend
- Nuevo `components/EditorialSignalsPanel.jsx` con dos exports:
  - `EditorialSignalsPanel` (compact|expanded, filter=positive|negative|undefined)
  - `EditorialSignalsSummary` (top strip con 5 chips: protected/trap/historical/positive/negative)
- `DashboardPage`: renderiza `EditorialSignalsSummary` después del KPI strip cuando `total_signals > 0`. `DiscardedRow` ahora muestra el panel expandido con todas las señales cuando hay editorial_context_signals.
- `MatchCard`: renderiza dos paneles compact (positivos + negativos) para auditar el "por qué" del engine.

### E1.3 Testing
- Iteration 29: 7/7 backend tests passed (catalog completeness, signal contract, sport-aware filtering, aggregator unit, summary builder, E2E baseball, auth regression).
- Bug crítico encontrado y arreglado en pipeline metadata merge.

---

## Phase D — UX hardening (Mobile + Carry-over + Editorial NBA/MLB)
**Estado:** ✅ COMPLETADO (2026-05-28)

### D.1 P0 — Re-run discarda picks previos (Smart Carry-over)
- Nuevo módulo `/app/backend/services/carryover_picks.py` con `apply_carryover()`.
- Hooked en `_run_analysis_pipeline` (server.py) ANTES de persistir el record.
- Reglas:
  - sólo picks con `confidence_score >= 60`
  - partido con status NS/TBD/SCHEDULED/PST (no live, no finished)
  - sin invalidador duro en la nueva corrida (LOW_BOTH motivation, lesión/injury/suspend/red card)
  - prior_run dentro de las últimas 24h
  - tope MAX_CARRYOVER=6
- UI: nueva sección "Picks previos preservados" en `DashboardPage` + badge "PREVIO/CARRYOVER" en `MatchCard`.
- `summary.carryover_picks` aparece en el payload con `_carryover` metadata.

### D.2 P1 — Editorial Context NBA/MLB no se ejecutaba
- Causa raíz: filtro hardcoded `if sport != "football"` en `editorial_context_service.py`.
- Fix: ahora usa `SUPPORTED_EDITORIAL_SPORTS = {football, basketball, baseball}`.
- Las fuentes NBA (as_com_nba, marca_com_nba, covers_nba) y MLB (as_com_mlb, covers_mlb, espn_mlb) ya estaban en el registry — el dispatcher las ignoraba antes del fix.
- Testing agent confirma: `editorial_context_evaluated=2` para basketball y baseball.

### D.3 P1 — Mobile UI overflow
- `flex-wrap` en headers, `w-full sm:w-auto` en botones, `overflow-x-hidden` en contenedor, KPIs 2 cols en mobile / 5 en desktop. Validado con screenshots a 375px y 390px.

### D.4 P1 — Aceptar coma en cuotas
- Confirmado funcionando en `LiveReevalPanel.jsx` (input regex `[0-9]+([.,][0-9]+)?`, sanitize `replace(',', '.')` antes de `parseFloat`).
- Stake input en `HistoryPage.jsx` también acepta coma.

---

## Phase A — Bright Data Web Unlocker (P1) + Editorial Basketball Sources
**Estado:** ⏳ EN PROGRESO (nuevo)

### A.1 Config & secretos
1. Añadir a `/app/backend/.env`:
   - `BRIGHTDATA_API_KEY=708ff637-d3c2-47b2-b950-ff700f8e1c47`
   - `BRIGHTDATA_ZONE=web_unlocker1`
2. Añadir validación “import-safe” (si no hay key, no rompe; retorna vacío).

### A.2 Nuevo backend: `brightdata_fetcher.py`
**Entregables backend**
- `/app/backend/services/editorial_context/brightdata_fetcher.py`
  - Función: `fetch_with_brightdata(matches, sources, timeout_sec, user_agent) -> list[raw_items]`
  - Implementación:
    - Para cada `index_url` de la fuente: request BrightData → HTML.
    - Extraer anchors con una regex/BeautifulSoup (sin Scrapy) usando `preview_anchors` como hint si es posible.
    - Filtrar anchors por:
      - `article_url_patterns`
      - `article_url_exclude_patterns`
      - `_article_matches_pair(home, away)`
    - Para cada artículo elegido: request BrightData → HTML → extraer `title/published_at/body` con selectores (CSS) usando parsel/bs4.
    - Emitir items en el MISMO shape que Scrapy/Playwright (`source, source_url, raw_text, title, published_at, scraped_at, _match_payload`).
  - Fail-soft:
    - Timeouts por request
    - 0 items ante errores
    - Logs `[BRIGHTDATA_EDITORIAL_*]`

### A.3 Runner subprocess (opcional)
- Decidir: (recomendado) correr BrightData dentro del proceso principal porque no usa reactor/Chromium.
- Si se prefiere aislamiento:
  - `brightdata_runner.py` + `brightdata_main.py` estilo Scrapy/Playwright.

### A.4 Dispatch tri-backend en `editorial_context_service.py`
- Ampliar dispatcher:
  - `Scrapy` para `requires_js=False` (server-rendered)
  - `Playwright` para `requires_js=True`
  - **BrightData** para fuentes con `requires_unlocker=True` o `anti_bot_level='hard'`
- Política:
  - Para Sportytrader/BeSoccer/scores24: intentar BrightData primero; si falla, no romper.
  - Mantener paralelismo: `asyncio.gather(scrapy, playwright, brightdata)`.

### A.5 Registry: flags de desbloqueo + nuevas fuentes NBA/basketball
1. Extender el schema de fuente:
   - `requires_unlocker: bool` (nuevo)
   - `anti_bot_level: 'none'|'soft'|'hard'` (opcional)
2. Marcar:
   - `sportytrader_es`: `requires_unlocker=True`
   - `besoccer_es`: `requires_unlocker=True`
   - `scores24_live`: `requires_unlocker=True`
3. **Añadir fuentes basketball editoriales (NBA)** a `editorial_source_registry.py`:
   - `covers_nba` (previas/picks)
   - `actionnetwork_nba` o alternativa accesible
   - `espn_nba_preview` (si estructura permite; si no, omit)
   - Cada fuente con:
     - `sport: 'basketball'`
     - `index_urls` de previews
     - patrones de URL
     - selectores `title/published_at/body`
     - `requires_unlocker=True` si tienen bot protection

### A.6 Testing
- Tests manuales:
  - `python -c` llamando a `fetch_editorial_context_bulk` para 1 partido NBA dummy.
  - Confirmar que el payload se adjunta con `available=true` cuando hay items.
- Test report: `/app/test_reports/iteration_28.json`.

---

## Phase B — Historical Detail Enrichment (Basketball) — vertical slice
**Estado:** ⏳ PENDIENTE (prioridad #1 del enrichment)

### B.1 Backend: `enrichBasketballHistoricalProfile(match)`
**Objetivo**: computar últimos 10–15 partidos y métricas avanzadas.

**Fuentes de datos**
- API-Sports basketball endpoints disponibles (ver límites de plan; si rate-limit, cache agresiva).
- Persistencia opcional en Mongo (cache TTL) para no recalcular.

**Implementación**
1. Nuevo módulo:
   - `/app/backend/services/historical/basketball_historical.py`
2. Funciones:
   - `fetch_last_n_games(team_id, n=15)`
   - `compute_basketball_profile(games) -> dict`
   - `enrichBasketballHistoricalProfile(match) -> basketballHistoricalProfile`
3. Métricas:
   - puntos for/against, total, tendencias last5, home/away split
   - pace estimado (posesiones aproximadas si hay FGA/FTA/TO/ORB)
   - offensive/defensive rating (si se puede aproximar)
   - %FG / %3PT / FTA / TO / REB
   - back-to-back / descanso (por fechas)
   - H2H recientes si existe endpoint; si no, aproximar por partidos cruzados disponibles
   - over/under rate vs líneas recientes si existen; si no, vs umbrales internos

### B.2 Integración en pipeline (regla: no descartar sin histórico)
- Nuevo flujo:
  - `selectedMatches → enrichHistoricalProfileBySport() → sportSpecificAnalysis() → alternativeMarketRescueLayer() → MoneyballGuardrail() → finalRecommendation`
- Implementar `enrichHistoricalProfileBySport()` en `analyst_engine.py` antes de análisis por deporte.

### B.3 Rescue layer: `basketballTotalPointsRescueLayer(match)`
- Se ejecuta si moneyline/spread no aportan valor.
- Evalúa:
  - Totales (Over/Under)
  - Team totals
  - Alternate spread protegido
- Reglas basadas en:
  - `projectedTotalPoints` vs `bookmakerLine ± margen`
  - `paceTrend`, consistencia de anotación, defensa, fatiga/b2b
- Devuelve candidato(s) a Moneyball.

### B.4 Moneyball + traps históricas
- Todas las propuestas pasan por:
  - `impliedProbability = 1/odds`
  - `estimatedProbability` del modelo
  - `edge` y clasificación
- Trap signals basketball:
  - overtime inflation
  - schedule strength
  - lesión ofensiva
  - b2b
  - blowout risk
  - línea ya ajustada

### B.5 UI — “Historial profundo” (Basketball)
- Nuevo panel en MatchCard:
  - últimos 15
  - promedios, trends O/U, pace, splits
  - frases humanas

### B.6 Testing
- Dataset sintético + 2 partidos reales cuando el plan API lo permita.
- Test report: `/app/test_reports/iteration_29.json`.

---

## Phase C — Historical Detail Enrichment (Baseball) — vertical slice
**Estado:** ⏳ PENDIENTE (después de Basketball)

### C.1 Backend: `enrichBaseballHistoricalProfile(match)`
- Nuevo módulo:
  - `/app/backend/services/historical/baseball_historical.py`
- Métricas:
  - runs for/against, hits, HR, errores
  - OBP/SLG/OPS (si la API lo expone; si no, aproximación con hits/BB/AB)
  - K/BB trends
  - bullpen usage 3–5 días y fatiga
  - starters last5: ERA/WHIP, innings
  - H2H, O/U trends

### C.2 Rescue layer: `baseballRunsRescueLayer(match)`
- Evalúa:
  - total runs O/U
  - team totals
  - F5 ML / F5 totals
  - Run Line +1.5
- Reglas: ofensiva + pitchers + bullpen + (park/weather si disponible).

### C.3 Moneyball + traps históricas
- Trap signals baseball:
  - producción inflada por serie previa
  - bullpen agotado no considerado
  - pitch count limitado
  - parque/clima
  - sobrevaloración por nombre
  - ofensiva fría con cuota inflada

### C.4 UI — “Historial profundo” (Baseball)
- Panel con:
  - últimos 15
  - carreras, OPS/hits, bullpen fatigue, pitchers, O/U, F5 trend
  - frases humanas

### C.5 Testing
- Reporte: `/app/test_reports/iteration_30.json`.

---

## 3) Next Actions

### A) Bright Data Unlocker (P1) — inmediato
1. Añadir `.env` keys y `brightdata_fetcher.py`.
2. Activar unlocker para Sportytrader/BeSoccer/scores24.
3. Añadir 2–3 fuentes NBA/basketball al registry con `requires_unlocker=True`.

### B) Basketball Historical Detail (P1) — siguiente
1. Implementar profile + integración pipeline.
2. Añadir rescue layer totales/team totals.
3. UI “Historial profundo”.

### C) Baseball Historical Detail (P1) — después
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
- No regresiones:
  - endpoints existentes responden
  - `_market_edge` y payload legacy intactos
  - `asyncio.wait_for(timeout=3.0)` intacto
  - narrativa ES intacta
