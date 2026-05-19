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

- 🟨 **Objetivo inmediato (P1, en progreso):** completar **Custom Saved Filter Views** como parte final del “Decision Intelligence Terminal”:
  - Persistencia **multi-dispositivo** en Backend/MongoDB (vía JWT)
  - Máximo **10** vistas por usuario
  - Funciones: **aplicar + editar + eliminar**
  - Campos: nombre + `sport` + filtros avanzados (ligas, motivación, mercado, odds, confianza mín, etc.)

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
  - `POST /api/picks/track`
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
✅ Filtros y export CSV base ya existen (evolucionaron en Phase D).

---

### Phase 4 — Polish (post-MVP)
🟨 **Estado: COMPLETADO (P2 + Phase D ejecutados); P1 Saved Views backend sync pendiente**

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
  - rail de presets de engine (5)
  - drawer (Sheet) con vistas (built-in + user)
  - ⚠️ **persistencia actual:** LocalStorage (debe migrar a backend)
- ✅ **EmptyStateNoValue → EmptyStateCoaching** (`EmptyStateNoValue.jsx`)
- ✅ **MatchIntelligencePanel (NUEVO)** (`MatchIntelligencePanel.jsx`)

4) **Wiring en páginas**
- ✅ `DashboardPage`: presets + filtros + secciones descartadas visibles + match cards con inteligencia.
- ✅ `MatchDetailPage`: panel de inteligencia completo.

**Verificación end-to-end (preview):**
- ✅ Render de dashboard y match detail validado mediante screenshots.

---

## 3) Next Actions (inmediatas)

### P1 — Custom Saved Filter Views (Backend/MongoDB) — FINALIZAR
**Contexto actual (descubierto en exploración):**
- ✅ Backend YA expone:
  - `GET /api/profile/saved-views`
  - `POST /api/profile/saved-views`
  - `DELETE /api/profile/saved-views/{view_id}`
  - colección Mongo: `saved_views` (con índices)
- ✅ Frontend YA tiene UI (Sheet) para:
  - aplicar
  - guardar
  - eliminar
- ❌ Problema: Frontend usa LocalStorage (`vbi_saved_views`) y **no sincroniza con backend**.
- ❌ Falta: endpoint de **edición** (PATCH/PUT) y UI de edición.
- ❌ Ajustes: límite backend actualmente 12; requerido 10.

**Objetivo P1 (acordado):**
- Persistencia en backend (JWT) multi-dispositivo
- Máximo 10 vistas por usuario
- Aplicar + editar + eliminar
- Campos: nombre, `sport` y `filters` flexible (incluye ligas/motivación/mercado/odds/confianza mínima, etc.)

**Implementación propuesta (pasos):**
1) Backend (FastAPI)
   - Ajustar cap de vistas por usuario: **12 → 10** (evicción por `created_at`).
   - Añadir endpoint de edición:
     - `PATCH /api/profile/saved-views/{view_id}` (actualiza `name`, `filters`, `enginePreset`, `sport`).
   - Validación mínima:
     - `name` longitud 1–60
     - `filters` debe ser dict
     - `sport` opcional en {football,basketball,baseball}

2) Frontend (React)
   - Remover LocalStorage como fuente de verdad.
   - Cargar vistas al montar:
     - `api.get('/profile/saved-views')`
   - Guardar vista actual:
     - `api.post('/profile/saved-views', payload)`
   - Eliminar vista:
     - `api.delete('/profile/saved-views/{id}')`
   - Editar vista:
     - UI inline o modal en el Sheet para renombrar/actualizar filtros actuales
     - `api.patch('/profile/saved-views/{id}', payload)`
   - Respetar límite 10 desde UI (UX: mostrar contador 10/10 y mensaje si se evicta la más antigua).

3) Modelo de filtros (frontend)
   - Expandir `filters` más allá de {league, market, minConfidence}:
     - soportar `leagues[]` (o `league`), `market`, `minConfidence`, `motivationMin`, `oddsMin`, `oddsMax`, etc.
   - Mantener compatibilidad con filtros actuales (si no existen campos, fallback a defaults).

4) Testing
   - ✅ Usar **testing agent** al final.
   - Cobertura mínima:
     - guardar vista → aparece en listado
     - recargar página → persiste
     - editar nombre/filtros → persiste
     - eliminar → desaparece
     - crear >10 → se mantiene tope 10 (evicción o bloqueo coherente)

### P1 — Proxy residencial para Sofascore (si se desea)
- Integrar proxy residencial en Crawlee/Playwright.
- Añadir variables `.env` (host/usuario/pass o API key proveedor).
- Re-test Sofascore scheduled-events.

### P2 — LLM prompt extension (opcional)
- Extender output para devolver nativamente:
  - `match_state`
  - `best_for` / `avoid`
- Mantener derivación client-side como fallback.

### P2 — Stats segmentados por `sport`
- ROI/Winrate por deporte y por mercado en `/api/stats/dashboard` + UI.

### P2 — Provenance visible
- Indicar fuente dominante por match: API-Sports vs ESPN/Flashscore.

---

## 4) Criterios de Éxito
- ✅ **POC:** picks/no-value en JSON estricto con motivación + riesgos + freshness.
- ✅ **MVP App:** dashboard + match detail + tracking + history + ES/EN + dark theme.
- ✅ **Auth:** login disponible (JWT) + usuario demo.
- ✅ **Resiliencia:** rate-limit + cache; fallback robusto (ESPN + Flashscore via Crawlee).
- ✅ **Multi-deporte:** Football/NBA/MLB con prompts específicos y selector UI.
- ✅ **UX análisis largo:** background jobs + progreso persistido + modal.
- ✅ **Decision Intelligence Terminal:** UI explica WHY/HOW con drivers, fragilidad/volatilidad, mercados evitados, motivación contextual y panel de inteligencia en detalle.
- 🟨 **Saved Filter Views (P1):**
  - Vistas de filtros persistidas en MongoDB por usuario (JWT)
  - Límite 10, con UX clara
  - Soporta aplicar + editar + eliminar
  - Testing agent valida el flujo end-to-end
- 🔁 **Operativo:** créditos LLM sostenibles para que análisis siga disponible.
