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
