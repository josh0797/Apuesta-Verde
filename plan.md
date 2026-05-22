# plan.md — Value Bet Intelligence (Actualizado)

## 1) Objetivos
- ✅ **Workflow core validado end-to-end** con datos reales: fixtures/odds/contexto → normalización → LLM produce picks **estrictos, gestionados por riesgo** (máx 3–8/día según reglas) o “Hoy no hay valor…”.
- ✅ **MVP entregado** con UI dark, ES/EN, match detail, histórico, tracking y KPIs.
- ✅ **Autenticación desde el día 1** (email+password + JWT; usuario demo sembrado).
- ✅ **Resiliencia mejorada vía fallbacks**: si el proveedor principal no alcanza, el sistema sigue mostrando eventos desde fuentes públicas.
- ✅ **Multi-deporte COMPLETO (P0)**: Fútbol + NBA/Basket + MLB/Béisbol con selector global, prompts LLM por deporte, y persistencia/consulta por `sport`.
- ✅ **UX mejorada para análisis lento (P2)**: `analysis/run` soporta ejecución en background con progreso persistido y modal de progreso en UI.
- ✅ **Lenguaje neutro por deporte (P2)**: labels/copy se ajustan automáticamente (partidos/juegos; goles/puntos/carreras).
- ✅ **Phase D — Decision Intelligence Terminal (COMPLETADO)**: evolución de UI/UX desde “predicciones” a **plataforma explicable de inteligencia contextual**:
  - explica **por qué existe** el pick
  - explica **por qué** la confianza es alta/baja
  - explica **por qué** se evitaron mercados
  - expone **señales contextuales** y **fragilidad/volatilidad**
  - refuerza disciplina de bankroll (“no apostar también es ganar”)

- 🔁 **Objetivo operativo (en curso):** mantener generación de picks fiable pese a:
  - límites de API-Sports (10 req/min) + bloqueo de temporadas actuales (usar 2024 como “proxy season”)
  - costes/créditos LLM
  - bloqueos anti-bot en fuentes web (Cloudflare)

- ✅ **Objetivo inmediato (P1): Custom Saved Filter Views — COMPLETADO**
  - Persistencia **multi-dispositivo** en Backend/MongoDB (vía JWT)
  - Máximo **10** vistas por usuario con **evicción** de la más antigua
  - Funciones: **aplicar + editar + eliminar**
  - UI/UX: contador X/10, renombrado inline, sobrescribir con filtros actuales, warning al llegar al límite
  - Sin LocalStorage como fuente de verdad
  - Testing agent validó **100%** de la feature (backend+frontend)

- ✅ **Objetivo P0 (Phase 5) — COMPLETADO:** mejorar disciplina y claridad en operación diaria
  - ✅ **Selección explícita con nombres de equipo** en las recomendaciones (LLM + guard rails)
  - ✅ **Flujo de picks “Pending” multi-deporte** para poder “guardar y liquidar después”
  - ✅ **LivePage consistente con Big Five** (fútbol) también en la lista “En vivo ahora”

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
    - rate limiting (≈8 req/min)
    - cache Mongo agresiva
    - usa **proxy season 2024** cuando el plan bloquea temporadas actuales
  - `data_ingestion.py`: priorización ligas + enriquecimiento (evolucionó en Phase A)
  - `analyst_engine.py`: analista ES + JSON estricto
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
  - `GET /api/matches/upcoming?refresh=bool&sport=`
  - `GET /api/matches/live?refresh=bool&sport=`
  - `GET /api/matches/{match_id}`
- Analysis:
  - `POST /api/analysis/run` (sync o background)
  - `GET /api/analysis/jobs/{job_id}`
  - `GET /api/analysis/jobs`
- Picks:
  - `GET /api/picks/today?sport=`
  - `GET /api/picks/history?sport=`
  - `GET /api/picks/run/{run_id}`
  - `POST /api/picks/track` (incluye `outcome: won|lost|push|pending`)
  - `GET /api/picks/tracked`
- Stats:
  - `GET /api/stats/dashboard`
- Meta/System:
  - `GET /api/meta/sports`
  - `GET /api/meta/leagues?sport=`
  - `GET /api/system/fallback-sources?use_browser=true`

#### 2.2 Frontend (React + Tailwind + shadcn/ui)
✅ Dark theme base evolucionable.
✅ Toggle ES/EN.
✅ Páginas: login, dashboard, live, match detail, history, profile.

#### 2.3 Testing
✅ Backend tests anteriores OK.

---

### Phase 3 — Operational Hardening + Optional Enhancements
**Goal:** mejorar fiabilidad, automatización y amplitud de fuentes.

🟨 **Estado: EN PROGRESO (Phase B completada; proxy residencial pendiente)**

#### 3.1 Scheduler / refresh strategy
✅ APScheduler activo (refresh upcoming/live) integrado.

#### 3.2 Fallback expansion (Cloudflare + scraping)
✅ **Phase B — Cloudflare bypass con Crawlee (COMPLETADA)**

Cambios implementados:
- ✅ Instalado `crawlee==1.7.0` + `browserforge` + `impit`.
- ✅ Reinstalado Playwright Chromium v1223 en `/pw-browsers`.
- ✅ `/app/backend/services/crawlee_scraper.py`:
  - fingerprinting + session pool
  - `--no-sandbox`
  - reset de estado global de Crawlee por ejecución
  - `flashscore_via_crawlee` extrae eventos robustamente (aria-label) con live/minuto/score
- ✅ `fallback_scraper.py`: httpx paralelo + browser serial, `browser_engine`.
- ✅ `server.py`: `/api/system/fallback-sources` acepta `use_browser=true`.

Resultados y limitaciones:
- ✅ ESPN (httpx): ~36 eventos
- ✅ Flashscore (Crawlee): ~106 eventos con live/minuto/score
- ❌ Sofascore: JSON 403 por **bloqueo IP datacenter** a nivel app → **requiere proxy residencial**.

#### 3.3 User-facing filters & workflow improvements
✅ Filtros, presets y export CSV base ya existen (evolucionaron en Phase D).
✅ Saved Views sincronizadas en backend (ver Phase 4 / P1).

---

### Phase 4 — Polish (post-MVP)
✅ **Estado: COMPLETADO (P2 + Phase D + P1 ejecutados)**

#### 4.1 P2 — Background queue + UX neutral por deporte
✅ **Estado: DONE**

Backend:
- ✅ `services/job_queue.py` con persistencia `analysis_jobs` y stages.
- ✅ `POST /api/analysis/run` soporta `background=true`.
- ✅ endpoints jobs + cleanup stale.

Frontend:
- ✅ `AnalysisProgressModal` con polling + estados.

#### 4.2 Phase D — Decision Intelligence Terminal
✅ **Estado: DONE**

**Objetivo:** transformar la app de “dashboard de predicciones” a **terminal profesional de decisión** con narrativa explicable y señales contextuales.

Cambios implementados (inspiración Apple + Stripe + Bloomberg + AI decision systems):

1) **Design system / visual hierarchy**
- ✅ `design_guidelines.md` actualizado (terminal financiero sobre base dark existente).
- ✅ `index.css` extendido con tokens semánticos + utilidades:
  - tokens: volatilidad/fragilidad tiers
  - clases: `.micro-label`, `.font-mono-tabular`, `.glass-surface`, `.terminal-row`, `.tone-*`, `.noise-overlay`

2) **Derivation layer (explicabilidad sin coste de tokens)**
- ✅ Nuevo `/app/frontend/src/lib/intelligence.js`:
  - deriva `drivers`, `risk`, `volatility`, `fragility`, `match_state` (fallback), `best_for`/`avoid` (fallback)
  - incluye `applyEnginePreset()` para presets de estilo de motor

3) **Component upgrades (preservan exports + data-testid)**
- ✅ **ConfidenceMeter → ConfidenceIntelligenceCard** (`ConfidenceMeter.jsx`)
- ✅ **MotivationBadge → MotivationContextBlock** (`MotivationBadge.jsx`)
- ✅ **PicksFilterBar → FilterIntelligenceBar** (`PicksFilterBar.jsx`)
- ✅ **EmptyStateNoValue → EmptyStateCoaching** (`EmptyStateNoValue.jsx`)
- ✅ **MatchIntelligencePanel (NUEVO)** (`MatchIntelligencePanel.jsx`)

4) **Wiring en páginas**
- ✅ `DashboardPage`: presets + filtros + secciones descartadas visibles + match cards con inteligencia.
- ✅ `MatchDetailPage`: panel de inteligencia completo + acciones de tracking (Gané/Perdí/Push) basadas en el `sport` del match.

**Verificación end-to-end (preview):**
- ✅ Render de dashboard y match detail validado mediante screenshots.

#### 4.3 P1 — Custom Saved Filter Views (Backend/MongoDB)
✅ **Estado: DONE (feature 100% operativa y testeada)**

**Cambios realizados:**
- Backend `/app/backend/server.py`
  - ✅ `SAVED_VIEWS_MAX = 10`
  - ✅ `POST /api/profile/saved-views` con evicción de la más antigua cuando se supera el límite; retorna `_evicted_id` si aplica
  - ✅ `PATCH /api/profile/saved-views/{view_id}` para editar `name`, `filters`, `enginePreset`, `sport`
  - ✅ Validación de `sport` en POST y PATCH
  - ✅ `GET /api/profile/saved-views` retorna `{ items, max: 10 }`

- Frontend `/app/frontend/src/components/PicksFilterBar.jsx`
  - ✅ Migración completa a backend (sin LocalStorage)
  - ✅ Carga inicial desde API + recarga al abrir el Sheet
  - ✅ Edición inline (Pencil + Enter/Escape)
  - ✅ Botón “sobrescribir con filtros actuales” (Save)
  - ✅ Contador visible X/10 y warning ámbar al llegar al límite
  - ✅ Descripción bajo cada vista (league · market · ≥conf · preset)
  - ✅ Toast feedback por acción (guardar, renombrar, actualizar, eliminar, evicción)

**Testing:**
- ✅ Testing agent:
  - Backend: **17/17** tests específicos de saved-views (CRUD, eviction, sport validation, aislamiento por usuario, auth)
  - Frontend: **18/18** tests UI (save/apply/rename/update/delete + persistencia)

---

### Phase 5 — Multi-sport Pending Picks + Big Five Live Filter + Explicit Team Names (P0)
✅ **Estado: COMPLETADO**

#### 5.1 Nombres explícitos en `selection` (LLM + Guard Rails)
✅ **Hecho**
- Backend:
  - Prompt de Stage 2 actualizado con **REGLAS DE `recommendation.selection`** (por mercado) + ejemplos explícitos.
  - Guard rail post-LLM: `analyst_engine._apply_explicit_selection()` que reescribe patrones opacos a nombres reales.
    - Rewrites cubiertos: `Home/Draw`, `1X`, `Home`/`Away`/`Visitante` en moneyline-like, `Home -1.5` (spread/run line), `Under 2.5` (totals sin unidad).
- Frontend:
  - `HistoryPage` ahora usa `humanizeSelection()` para picks tracked (incluye picks antiguos/legacy).

Smoke test:
- ✅ 5/5 rewrites correctos + **idempotencia OK** (si ya viene un nombre explícito, no se toca).

#### 5.2 Pending Picks Flow (cross-sport)
✅ **Hecho**
- Backend:
  - `/api/picks/track` ya soportaba `outcome="pending"` y hace upsert por `pick_id = run_id-match_id`.
- Frontend:
  - Dashboard (`MatchCard`): botón **“Marcar para seguir”** (multi-deporte) que guarda el pick como `pending`.
  - History: acciones inline **Gané/Perdí/Push** solo para filas `pending`.

Smoke test:
- ✅ pending → settled mantiene **1 sola fila** (sin duplicado) vía upsert.

#### 5.3 LivePage Big Five Filter (fútbol)
✅ **Hecho**
- Root cause:
  - El filtrado por nombre confundía **Bundesliga Alemania** con **Bundesliga Austria** y “Premier League” oficiales con otras ligas homónimas.
- Fix:
  - Backend: `services/football_competitions.is_big_five(name, league_id)` usa `league_id` como fuente de verdad.
  - IDs Big Five (API-Sports): **39, 140, 135, 78, 61**.
  - Frontend: `/src/lib/competitions.js` ahora es **id-aware** y acepta match object (preferido) o string (fallback).
- UI:
  - LivePage: toggle **“Solo 5 grandes” / “Ver todas”** + contador `N/Total` + hint de ocultos.

Resultado verificado:
- ✅ Se pasó de **8/50 (con ruido)** a **2/50 (solo EPL real)** en el entorno de prueba.

#### 5.4 Testing
✅ **Hecho**
- Reporte: `/app/test_reports/iteration_11.json`
- Backend: **100%** (15/15) ✅
- Frontend: **95%** (14/15) ✅
- 0 bugs críticos, 0 regresiones.
- 2 issues menores detectados corresponden a **data legacy pre-Phase 5** (picks antiguos guardados con tokens opacos).

---

## 3) Next Actions (inmediatas)

### P2 — Proxy residencial para Sofascore (opcional, requiere credenciales)
- Integrar proxy residencial en Crawlee/Playwright.
- Añadir variables `.env` (host/usuario/pass o API key proveedor).
- Re-test Sofascore scheduled-events.

### P2 — Stats segmentados por `sport`
- ROI/Winrate por deporte y por mercado en `/api/stats/dashboard` + UI.

### P2 — Provenance visible
- Indicar fuente dominante por match: API-Sports vs ESPN/Flashscore.

### P2 — LLM prompt extension (opcional)
- Extender output para devolver nativamente:
  - `match_state`
  - `best_for` / `avoid`
- Mantener derivación client-side como fallback.

---

## 4) Criterios de Éxito
- ✅ **POC:** picks/no-value en JSON estricto con motivación + riesgos + freshness.
- ✅ **MVP App:** dashboard + match detail + tracking + history + ES/EN + dark theme.
- ✅ **Auth:** login disponible (JWT) + usuario demo.
- ✅ **Resiliencia:** rate-limit + cache; fallback robusto (ESPN + Flashscore via Crawlee).
- ✅ **Multi-deporte:** Football/NBA/MLB con prompts específicos y selector UI.
- ✅ **UX análisis largo:** background jobs + progreso persistido + modal.
- ✅ **Decision Intelligence Terminal:** UI explica WHY/HOW con drivers, fragilidad/volatilidad, mercados evitados, motivación contextual y panel de inteligencia en detalle.
- ✅ **Saved Filter Views (P1):**
  - Vistas persistidas en MongoDB por usuario (JWT)
  - Límite 10 con evicción + UX clara (contador + warning)
  - Aplicar + editar + eliminar
  - Testing agent valida el flujo end-to-end

- ✅ **Phase 5 (P0) — Criterios cumplidos**
  - `recommendation.selection` legible y específica; guard rail reescribe outputs opacos.
  - El usuario puede **guardar picks como pending** desde Dashboard (multi-deporte) y **liquidarlos después** desde History.
  - LivePage (fútbol) muestra “En vivo ahora” solo con **Big Five reales** usando `league_id` (consistente con el análisis Big Five).
  - Testing agent pasa (backend + frontend) sin regresiones.

- 🔁 **Operativo:** créditos LLM sostenibles para que análisis siga disponible.
