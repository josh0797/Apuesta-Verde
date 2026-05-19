# plan.md — Value Bet Intelligence (Actualizado)

## 1) Objetivos
- ✅ **Workflow core validado end-to-end** con datos reales: fixtures/odds/contexto → normalización → LLM produce picks **estrictos, gestionados por riesgo** (máx 3–8/día según reglas) o “Hoy no hay valor…”.
- ✅ **MVP entregado** con UI tipo sportsbook en dark mode, ES/EN, transparencia por partido (match detail) y tracking de picks.
- ✅ **Autenticación desde el día 1** (email+password + JWT; usuario demo sembrado).
- ✅ **Resiliencia mejorada vía fallbacks**: cuando el proveedor principal no alcanza, el sistema sigue mostrando partidos desde fuentes públicas.
- ✅ **Multi-deporte COMPLETO (P0)**: Fútbol + NBA/Basket + MLB/Béisbol con selector global en UI, prompts LLM por deporte, y persistencia/consulta por `sport`.
- 🔁 **Objetivo operativo (en curso):** mantener generación de picks fiable pese a:
  - límites de API-Sports (10 req/min) + bloqueo de temporadas actuales (usar 2024 como “proxy season”)
  - costes/créditos LLM
  - bloqueos anti-bot en fuentes web (Cloudflare)

---

## 2) Pasos de Implementación

### Phase 1 — Core POC (aislado; no avanzar hasta verde)
**Goal:** `/app/poc/test_core.py` valida API-Football + fallback scraping + salida JSON estricta del LLM.

✅ **Estado: COMPLETADO**

Completado:
1) **Cliente API-Football + sampling**
   - Fixtures (próx 48h) y live.
   - Odds por fixture.
   - Contexto de equipos cuando disponible.

2) **Normalización a esquema de 3 capas**
   - `odds_snapshots`, `team_context`, `live_stats`.
   - Soporte de “data freshness” y penalizaciones.

3) **Pipeline de análisis LLM**
   - Persona analista en español.
   - Salida JSON estricta parseada/validada.

4) **Fallback chain smoke test**
   - ESPN scoreboard como fallback verificado.

5) **POC acceptance loop**
   - Checks críticos completados.

**User stories Phase 1 (✅ validadas)**
1. Obtener fixtures reales próximas 48h.
2. Obtener odds multi-bookmaker.
3. Etiquetar urgencia/motivación (1–5).
4. Devolver JSON estructurado estricto.
5. Devolver “Hoy no hay valor…” explícito.

---

### Phase 2 — V1 App Development (MVP alrededor del core; auth incluido)
**Goal:** App funcional con login, dashboard + match detail + histórico, tracking y KPIs.

✅ **Estado: COMPLETADO**

#### 2.1 Backend (FastAPI + MongoDB/Motor)
Implementado:
- `/app/backend/server.py`
- `/app/backend/services/`
  - `api_football.py`:
    - rate limiting tipo token bucket (≈8 req/min para respetar 10/min)
    - cache Mongo agresiva (odds TTL ~30m, contexto TTL ~6h)
    - usa **proxy season 2024** cuando el plan bloquea temporadas actuales
  - `data_ingestion.py`: priorización ligas + enriquecimiento serial (evolucionará en Phase A)
  - `analyst_engine.py`: persona analista ES + JSON estricto
  - `normalizer.py`: normalización a esquema interno
  - `fallback_scraper.py`: ESPN + scrapers fallback
  - `auth.py`: JWT + seed usuario demo

Endpoints entregados (auth salvo indicación):
- Public:
  - `GET /api/` health
- Auth:
  - `POST /api/auth/register`
  - `POST /api/auth/login`
  - `GET /api/auth/me`
  - `POST /api/auth/logout`
  - `PATCH /api/auth/me/language`
- Matches:
  - `GET /api/matches/upcoming?refresh=bool`
  - `GET /api/matches/live?refresh=bool`
  - `GET /api/matches/{match_id}`
- Analysis:
  - `POST /api/analysis/run`
- Picks:
  - `GET /api/picks/today`
  - `GET /api/picks/history`
  - `GET /api/picks/run/{run_id}`
  - `POST /api/picks/track`
  - `GET /api/picks/tracked`
- Stats:
  - `GET /api/stats/dashboard`
- System:
  - `GET /api/system/fallback-sources` (fuentes públicas agregadas)

#### 2.2 Frontend (React + Tailwind + shadcn/ui)
✅ Dark sportsbook-modern theme (`design_guidelines.md`).
✅ Toggle ES/EN.
✅ Páginas: login, dashboard, live, match detail, history, profile.

#### 2.3 Testing
✅ Backend tests anteriores OK (histórico del proyecto).

---

### Phase 3 — Operational Hardening + Optional Enhancements
**Goal:** mejorar fiabilidad, automatización y amplitud de fuentes.

🟨 **Estado: EN PROGRESO (Phase B completada; proxy residencial pendiente)**

#### 3.1 Scheduler / refresh strategy
✅ APScheduler activo (refresh upcoming/live y purge de contexto) ya integrado.

#### 3.2 Fallback expansion (Cloudflare + scraping)
✅ **Phase B — Cloudflare bypass con Crawlee (COMPLETADA)**

Cambios implementados:
- ✅ Instalado `crawlee==1.7.0` + `browserforge` + `impit`.
- ✅ Reinstalado Playwright Chromium **v1223** en `/pw-browsers`.
- ✅ Nuevo módulo `/app/backend/services/crawlee_scraper.py`:
  - `PlaywrightCrawler` + `DefaultFingerprintGenerator` (rotación browser/OS/locale)
  - `--no-sandbox` para entorno root en contenedor
  - **Reset por ejecución** del estado global de Crawlee (service locator):
    - `service_locator.storage_instance_manager.clear_cache()`
    - `service_locator.set_storage_client(MemoryStorageClient())`
  - `sofascore_via_crawlee`: warm-up en dominio web + llamada API con `page.context.request.get`
  - `flashscore_via_crawlee`: extracción robusta por `aria-label` desde `.event__match`, con live/minutos/scores
  - Sanitización de scores (evita NaN/inf que rompían JSON)
- ✅ `fallback_scraper.py` actualizado:
  - scrapers httpx en paralelo
  - scrapers browser **en serie** (evita conflictos de estado global de Crawlee)
  - fallback automático a `playwright_scraper.py` legacy si Crawlee falla
  - añade campo `browser_engine` en respuesta
- ✅ `server.py` actualizado:
  - `/api/system/fallback-sources` acepta `use_browser=true` (alias legacy `use_playwright=true`)
  - retorna `browser_engine`

Resultados y limitaciones:
- ✅ ESPN (httpx): ~36 matches
- ✅ Flashscore (Crawlee): ~106 matches con live/minuto/score
- ❌ Sofascore: devuelve JSON 403 por **bloqueo por clase de IP (datacenter)** a nivel aplicación. **Requiere proxy residencial** (Bright Data/IPRoyal/Apify Proxy) para funcionar.
- ✅ Smoke tests: endpoints críticos 200 OK, sin regresiones.
- ⏱️ `use_browser=true` tarda ~21s (aceptable porque es explícito; no se usa por defecto).

#### 3.3 User-facing filters & workflow improvements
✅ Filtros y export CSV ya existen.

#### 3.4 Export + reporting
✅ CSV export y KPIs base (ROI/Winrate) ya integrados.

#### 3.5 Auth enhancement (opcional)
🔲 Google OAuth opcional (no prioritario).

**User stories Phase 3 (parcialmente cumplidas)**
1. ✅ App sigue funcionando si el proveedor primario falla (ESPN/Flashscore fallback disponible).
2. ✅ Se puede inspeccionar el estado de fallback desde `/api/system/fallback-sources`.
3. 🔲 Mostrar “provenance/fallback used” más visible en UI (opcional).

---

### Phase 4 — Polish (post-MVP)
🔲 **Estado: NO INICIADO**
- Alertas para picks nuevos.
- Filtros avanzados + vistas guardadas.
- Dashboard de stats enriquecido (ROI por mercado/liga, rachas).
- Mejoras de rendimiento (virtualización de tablas si aplica).

---

## 3) Next Actions (inmediatas)

### ✅ P0 — Phase A: Multi-deporte (Fútbol + NBA + MLB) — COMPLETADA
**Estado:** ✅ DONE

#### Backend (completado)
- ✅ `api_sports.py` (hub central) usado para **basketball** y **baseball**.
- ✅ `analyst_engine.py`:
  - `analyze_matches(payload, sport)`
  - prompt dinámico via `_build_system_prompt(sport)` con `SPORT_RULES`:
    - football / basketball / baseball
  - meta `_sport` en respuesta.
- ✅ `data_ingestion.py` refactor completo:
  - `ingest_upcoming/ingest_live/enrich_fixture` aceptan `sport`
  - football usa `api_football` (compat)
  - basketball/baseball usan `api_sports`
  - `_enrich_generic` para NBA/MLB
- ✅ `normalizer.py`:
  - `normalize_odds_generic`, `normalize_team_context_generic`, `normalize_live_stats_generic`
  - `summarize_match_for_llm` incluye `sport`
- ✅ `server.py`:
  - helpers `SUPPORTED_SPORTS`, `_norm_sport()`, `_sport_filter()` (compat: docs sin `sport` se tratan como football)
  - nuevo endpoint `GET /api/meta/sports`
  - soporte `?sport=` en:
    - `/matches/upcoming`, `/matches/live`
    - `/analysis/run`
    - `/picks/today`, `/picks/history`, `/picks/today/filtered`, `/picks/today/export.csv`
    - `/meta/leagues`
  - persistencia de `sport` en matches/odds_snapshots/picks
  - índices: `matches.sport` + `picks(user_id, sport, generated_at)`
- ✅ `scheduler.py`:
  - jobs explícitos para `sport="football"`
  - NBA/MLB **opt-in** vía `analysis/run` para preservar cuota compartida 10 req/min.

#### Frontend (completado)
- ✅ `/app/frontend/src/lib/sport.jsx`:
  - `SportProvider` + `useSport()`
  - persistencia `localStorage`
  - fetch de `/api/meta/sports`
- ✅ `App.js`: app envuelta en `<SportProvider>`
- ✅ `AppHeader.jsx`: SportSwitcher dropdown global (icono+label+activo)
- ✅ `DashboardPage.jsx`: incluye `sport` en llamadas:
  - `GET /picks/today?sport=`
  - `POST /analysis/run` con `sport`
  - `GET /picks/today/export.csv?sport=`
  - badge + icono del deporte activo
- ✅ `LivePage.jsx`: `GET /matches/live?sport=`
- ✅ `i18n.js`: traducciones `sport.*` en ES/EN

#### Verificación End-to-End (completado)
- ✅ `/api/meta/sports` retorna 3 deportes
- ✅ `GET /api/matches/upcoming?sport=basketball` devuelve partidos (incl. Knicks vs Cavaliers) + odds
- ✅ `GET /api/matches/upcoming?sport=baseball` devuelve partidos (incl. Marlins vs Braves) + odds
- ✅ `POST /api/analysis/run` con `sport=basketball` y `sport=baseball` completan:
  - provider: OpenAI gpt-4o-mini
  - prompts específicos por deporte aplican reglas/descartes correctos
- ✅ UI: selector cambia deporte, dashboard se refresca con picks por deporte
- ✅ Smoke tests: endpoints críticos 200 OK (sin regresiones)

### P1 — Proxy residencial para Sofascore (si se desea)
- Integrar proxy residencial en Crawlee/Playwright.
- Añadir variables `.env` (host/usuario/pass o API key proveedor).
- Re-test Sofascore scheduled-events.

### P1 — Operación / estabilidad
- Mantener `analysis/run` cache-first por defecto.
- Ajustar límites de análisis/enriquecimiento para minimizar rate-limit churn.
- Considerar cola/background para análisis multi-deporte (por latencias 80–120s en NBA/MLB).

### P2 — Traducción / UX polish
- Revisar labels que asumen fútbol (“partidos”, “goles”, etc.) y hacerlos neutros por deporte.

---

## 4) Criterios de Éxito
- ✅ **POC:** picks/no-value en JSON estricto con motivación + riesgos + freshness.
- ✅ **MVP App:** dashboard + match detail + tracking + history + ES/EN + dark theme.
- ✅ **Auth:** login disponible (JWT) + usuario demo.
- ✅ **Resiliencia:** rate-limit + cache; fallback robusto (ESPN + Flashscore via Crawlee).
- ✅ **Multi-deporte:** usuario puede generar y trackear picks para Football/NBA/MLB con prompts específicos y selector en UI.
- 🔁 **Operativo:** créditos LLM sostenibles para que análisis siga disponible.
