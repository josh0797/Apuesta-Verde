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

- 🟨 **Objetivo P0 actual (EN PROGRESO):** mejorar disciplina y claridad en operación diaria
  - **Selección explícita con nombres de equipo** en las recomendaciones del LLM
  - **Flujo de picks “Pending” multi-deporte** para poder “guardar y liquidar después”
  - **LivePage consistente con Big Five** (para fútbol) también en la lista “En vivo ahora”, no solo en el botón de análisis

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
🟨 **Estado: EN PROGRESO**

Contexto (hallazgos):
- El frontend ya tiene `humanizeSelection()` (`/app/frontend/src/lib/format.js`) y se usa en `MatchCard`/`MatchDetail`.
- `HistoryPage` actualmente muestra `selection` crudo, por eso se ven códigos ("Home/Draw", "1X").
- El backend ya soporta `outcome: pending` en `/picks/track` (upsert por `pick_id = run_id-match_id`).
- `LivePage` ya fuerza `big_five_only=true` en el **análisis en vivo**, pero la lista “En vivo ahora” viene sin filtro desde `/matches/live`.

#### 5.1 Nombres explícitos en `selection` (LLM + Fallback)
**Objetivo:** que el LLM emita selecciones comprensibles y no ambiguas.

Backend (P0):
- Actualizar prompts en `/app/backend/services/analyst_engine.py` (Stage A prefilter + Stage B deep analysis):
  - Forzar `recommendation.selection` a contener nombres explícitos del equipo cuando aplique.
  - Prohibir placeholders/códigos opacos:
    - ❌ "Home/Draw" / "1X" / "Home" / "Away" / "X2" / "1"
    - ✅ "Bayern Munich o empate" / "Knicks gana" / "Empate o Bremen".
  - Mantener formato actual para spreads/totals:
    - "Bayern -1.5", "Más de 2.5 goles" (sport-aware puntos/carreras).
  - Añadir una validación/guard rails post-proceso (si el LLM emite código, reescribir a nombres con `home_team.name`/`away_team.name` antes de persistir y devolver payload).

Frontend (P0):
- `HistoryPage.jsx`: mostrar `selection` con `humanizeSelection()` como fallback (especialmente para picks viejos ya guardados).
- Mantener `humanizeSelection()` como red de seguridad para:
  - picks antiguos
  - mercados no previstos
  - outputs residuales del LLM

Criterio de éxito:
- En Dashboard/MatchDetail/History, la selección siempre se entiende sin ambigüedad (nunca “Home/Draw”).

#### 5.2 Pending Picks Flow (cross-sport)
**Objetivo:** permitir “guardar para seguir” y liquidar más tarde, multi-deporte.

Backend (P0):
- Confirmado: `/picks/track` ya acepta `outcome: pending` y hace upsert por `pick_id`.
- Ajustes si hicieran falta:
  - Asegurar que `market`, `selection`, `confidence_score`, `sport`, `league`, `match_label` se conserven al pasar de pending→settled.
  - Mantener compatibilidad con picks de días anteriores (settlement tardío).

Frontend (P0):
- Dashboard — `MatchCard.jsx`:
  - Añadir botón “Marcar para seguir” (BookmarkPlus) que llama `POST /picks/track` con `outcome: pending`.
  - Mostrar estado visual si el pick ya está marcado como pending (evitar duplicados/confusión).
- History — `HistoryPage.jsx`:
  - Añadir columna/acciones inline SOLO para filas con `outcome === 'pending'`:
    - “Gané” (BadgeCheck)
    - “Perdí” (ThumbsDown)
    - “Push” (Equal)
  - Para picks settled: mantener pill actual (sin edición por simplicidad).
  - Al click: llamar `/picks/track` con mismo `run_id` y `match_id` (upsert) para actualizar `outcome`.

i18n (P0):
- Añadir keys nuevas en `/app/frontend/src/lib/i18n.js`:
  - `dashboard.savePending` / `history.settlePick` / `history.markWon` / `history.markLost` / `history.markPush`
  - Mensajes toast: guardado pending, settle ok/error.

Criterio de éxito:
- El usuario puede marcar un pick como pending desde el Dashboard y liquidarlo desde History sin depender del “run de hoy”.

#### 5.3 LivePage filtra por Big Five (football)
**Objetivo:** que “En vivo ahora” sea consistente con el enfoque Big Five cuando `sport === 'football'`.

Frontend (P0):
- Crear helper compartido:
  - `/app/frontend/src/lib/competitions.js` con `isBigFive(leagueName)` (puerto del backend `is_big_five()` basado en canonical names / alias matching simplificado para frontend).
- `LivePage.jsx`:
  - Filtrar `items` cuando `sport === 'football'` para mostrar solo ligas Big Five.
  - Ajustar contador para reflejar items filtrados.

Opcional (P2, no bloquear P0):
- Toggle “Ver todas las ligas” para desactivar filtro bajo demanda.

Backend (si se necesita, P1):
- Alternativa: aceptar `big_five_only` en `/matches/live` y filtrar servidor-side.
  - (No requerido si el filtrado frontend es suficiente, pero útil para payload menor y consistencia.)

Criterio de éxito:
- En fútbol, LivePage no muestra partidos fuera del Big Five en la lista “En curso ahora”.

#### 5.4 Testing
✅ Requisito: usar testing agent al terminar.

Backend:
- Verificar:
  - prompt updates (Stage A/B) producen `selection` con nombres explícitos
  - `/picks/track` soporta `pending` y actualizaciones idempotentes
  - no regresión en endpoints existentes (saved-views, analysis/run guard, etc.)

Frontend:
- Verificar:
  - `HistoryPage` muestra selection humanizada (fallback)
  - botones settlement aparecen SOLO en pending y actualizan outcome
  - botón pending en `MatchCard` crea pick tracked con outcome pending
  - `LivePage` filtra Big Five en football y el contador coincide

---

## 3) Next Actions (inmediatas)

### P0 — Phase 5 (en orden recomendado)
1) **LLM selection explícita** (backend prompts + guard rewrite) + History fallback con `humanizeSelection()`.
2) **Pending picks flow** (Dashboard bookmark + History settlement actions + i18n).
3) **LivePage Big Five filter** (helper competitions.js + filtro y contador).
4) **Testing agent (backend + frontend)**.

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

- 🟨 **Phase 5 (P0) — Nuevos criterios de éxito**
  - `recommendation.selection` siempre incluye nombre(s) explícito(s) de equipo (nunca “Home/Draw”, “1X”).
  - El usuario puede **guardar picks como pending** desde Dashboard (multi-deporte) y **liquidarlos después** desde History.
  - LivePage (fútbol) muestra “En vivo ahora” solo con ligas Big Five (consistente con el análisis Big Five).
  - Testing agent pasa (backend + frontend) sin regresiones.
  
- 🔁 **Operativo:** créditos LLM sostenibles para que análisis siga disponible.
