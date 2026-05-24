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

- ✅ **Objetivo P0/P1 (Phase 6) — COMPLETADO:** dejar de analizar MLB como fútbol + disciplina de EV (universal)
  - ✅ **MLB Intelligence Engine (Fase 1)**: estructura/weighting MLB + sanitización de mercados inválidos (no empate)
  - ✅ **Universal Market Implied Probability Guardrail** (TODOS los deportes): validación por edge real vs implícito + calibración por deporte
  - ✅ **UI de Market Edge + “Why this pick can fail”**: visibilidad de implícita/estimada/edge/umbral y riesgos

- ✅ **Objetivo P0 (Phase 8 + 8.1) — COMPLETADO:** Football Search & Selection Engine (anti-ligas exóticas) + discovery activo
  - ✅ **League Quality Score (0–100)** + **Market Liquidity Score (0–100)** + **Football Selection Score (0–100)**.
  - ✅ **Relevancy-first + Dynamic Match Discovery** (waterfall Tier 1 → Tier 2 → Tier 3).
  - ✅ **Hard gate** contra ligas exóticas / reservas / U17 (antes de LLM).
  - ✅ **Active Priority Fixture Discovery (8.1)**: discovery upfront de Tier 1/2 para poblar análisis con alta liquidez.
  - ✅ **Pre-LLM filtering**: Tier 4 / ligas exóticas / baja relevancia se saltan antes del LLM (ahorro de costes y mejor latencia).
  - ✅ **Estados de match**: `PRIORITY_MATCH`, `HIGH_LIQUIDITY`, `STANDARD`, `LOW_DATA_QUALITY`, `LOW_MARKET_SUPPORT`, `EXOTIC_LEAGUE_WARNING`, `SKIPPED_LOW_RELEVANCE`.
  - ✅ **UI**: `FootballQualityBadge` en cada pick + sección “Saltados — baja relevancia” con razones.
  - ✅ **Testing end-to-end** (ver `/app/test_reports/iteration_14.json`).

- ✅ **Objetivo P0 (Phase 9) — COMPLETADO:** Protected Alternative Market Scan
  - ✅ Cuando **no hay edge** en el mercado directo, el motor escanea alternativas protegidas:
    - Under 3.5 / Under 2.5
    - (opcional) combinaciones con Doble Oportunidad (según reglas del guardrail)
  - ✅ UI marca la alternativa con `ProtectedMarketBadge` y el pick incluye `_alternative_market`.

- ✅ **Objetivo P0/P1 (Phase 10) — COMPLETADO:** Live & Alternative Market Re-Evaluation Engine + manual odds
  - ✅ Endpoint `POST /api/live/reevaluate` calcula edge live usando score/minuto/momentum (ESPN/Flashscore) y odds manuales.
  - ✅ **Manual odds input** en UI (LiveReevalPanel) para calcular edge real cuando API-Sports no ofrece live odds.
  - ✅ Testing completo: `/app/test_reports/iteration_15.json`.

- ✅ **Objetivo P0 (Phase 11) — COMPLETADO:** Live Match Lifecycle Fix (anti-stale live)
  - ✅ LivePage muestra **solo** partidos realmente activos, con estado/minuto real, freshness y expiración automática.
  - ✅ Se elimina el bug crítico de cards “90’” stale / partidos terminados.
  - ✅ Testing completo: `/app/test_reports/iteration_16.json`.

- ✅ **Objetivo P1 (Phase P2A) — COMPLETADO:** StatsBomb-Inspired Under 3.5/2.5 Model
  - ✅ Modelo Poisson (λ_home/λ_away/λ_total) + shrinkage bayesiano + P(Under) por CDF Poisson.
  - ✅ Integración directa en Phase 9 (Protected Alternative Market Scan).
  - ✅ UI muestra λ_total, P(Under), confidence y explicaciones.

- ✅ **Objetivo P2 (Phase P2B) — COMPLETADO:** Provenance visible por match (UI)
  - ✅ Badge “Fuente: API-Sports/ESPN/Flashscore…” visible en LivePage y MatchCard.
  - ✅ Propagación `_provenance` match→picks desde backend.

- ✅ **Correcciones críticas (P1/P2) — COMPLETADAS**
  - ✅ P1 BSON: normalizador global de keys para Mongo (`documents must have only string keys, key was 1`).
  - ✅ P2 aislamiento de estado por deporte: evita “bleed” entre tabs (picks de fútbol en basket/baseball).

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
  - `data_ingestion.py`: priorización ligas + enriquecimiento (evolucionó en Phase 8.1)
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

#### 4.3 P1 — Custom Saved Filter Views (Backend/MongoDB)
✅ **Estado: DONE (feature 100% operativa y testeada)**

---

### Phase 5 — Multi-sport Pending Picks + Big Five Live Filter + Explicit Team Names (P0)
✅ **Estado: COMPLETADO**

---

### Phase 6 — MLB Intelligence Engine + Universal Market Guardrail (P0/P1)
✅ **Estado: COMPLETADO (Fase 1)**

---

### Phase 8 — Football Search & Selection Engine (P0)
✅ **Estado: COMPLETADO**

---

### Phase 8.1 — Active Priority Fixture Discovery (P0)
✅ **Estado: COMPLETADO**

---

### Phase 9 — Protected Alternative Market Scan (P0)
✅ **Estado: COMPLETADO**

---

### Phase 10 — Live & Alternative Market Re-Evaluation Engine (P0/P1)
✅ **Estado: COMPLETADO**

---

### Phase 11 — Live Match Lifecycle Fix (P0)
✅ **Estado: COMPLETADO**

**Objetivo:** LIVE debe mostrar solo partidos realmente activos, con frescura y expiración automática; sin “90’” zombie.

Implementado:
- ✅ Backend: `/app/backend/services/live_lifecycle.py`
  - `is_match_live()` / `is_match_expired()`
  - `compute_live_state()` (LIVE_ACTIVE/LIVE_LATE/GARBAGE_TIME/HT/LIVE_STALE)
  - `compute_freshness()` (0–100 → DATOS_FRESCOS/RETRASADOS/LIVE_STALE)
  - `sweep_expired_live()` (auto-expira y archiva a `archived_live_matches`)
- ✅ Backend: `GET /api/matches/live`
  - filtro estricto + `_live_state` + `_freshness` por match
  - `archived_count` + `cache_ttl_sec`
- ✅ Backend: `POST /api/live/reevaluate`
  - devuelve **409** si el match no está activo (payload con `live_state`)
- ✅ Frontend:
  - `/app/frontend/src/lib/liveValidation.js` (mirror + TTL por deporte)
  - `LiveStateBadge` + `LiveFreshnessBadge`
  - LivePage:
    - primer load rápido con `refresh=false`, polling con TTL por deporte
    - heartbeat ticker 30s para ocultar stale sin esperar refetch
    - LiveReevalPanel solo en LIVE_ACTIVE/LIVE_LATE/HT
    - warning para GARBAGE_TIME
    - sección colapsable “Live archivados”
- ✅ Logging requerido:
  - `[LIVE_MATCH_VALIDATION]` / `[LIVE_MATCH_EXPIRED]` / `[LIVE_TICKER_EXPIRY]`
- ✅ Testing agent: `/app/test_reports/iteration_16.json` (backend 100% / frontend 100%)

---

### Phase P2A — StatsBomb-Inspired Under 3.5/2.5 Model (P1)
✅ **Estado: COMPLETADO**

**Objetivo:** mejorar Under 3.5/2.5 usando una librería de features “StatsBomb-inspired” pero calculada con datos de **últimos 10 partidos / temporada en curso** vía API-Sports.

Implementado:
- ✅ Backend: `/app/backend/services/statsbomb_features.py`
  - modelo Poisson (λ_home/λ_away/λ_total)
  - shrinkage bayesiano hacia prior neutral (1.35)
  - P(Under 2.5/3.5) vía CDF Poisson
  - confidence 0–100 + explanations
- ✅ Backend: `/app/backend/services/api_football.py`
  - `fixtures_last_n(team_id, n=10, season=..., cache 12h)`
- ✅ Backend: `/app/backend/services/normalizer.py`
  - `normalize_recent_fixtures()` (distribución de goles + under hit rates)
  - `season_priors` (clean_sheet_rate / failed_to_score_rate)
- ✅ Backend: `/app/backend/services/data_ingestion.py`
  - `_enrich_football` hidrata `recent_fixtures` para home/away cuando deep=true
- ✅ Backend: `/app/backend/services/under_market_scan.py`
  - usa Poisson como `estimated_probability_under{25,35}`
  - añade `xg_model` subscore ponderado por confidence
  - retorna `statsbomb_features` + `estimated_probability_source=statsbomb_poisson`
- ✅ Frontend: `/app/frontend/src/components/ProtectedMarketBadge.jsx`
  - pill compacto con λ_total y P(Under)
  - bloque expandido con λ, probabilidades, confidence y explanations

---

### Phase P2B — Provenance UI (P2)
✅ **Estado: COMPLETADO**

**Objetivo:** hacer visible la fuente dominante por match (API-Sports vs ESPN/Flashscore) en UI.

Implementado:
- ✅ Backend: `/app/backend/services/provenance.py` (ya existía)
- ✅ Backend: `server.py` propaga `_provenance` match→picks
- ✅ Frontend:
  - `ProvenanceBadge` integrado en `MatchCard.jsx`
  - `ProvenanceBadge` integrado en `LivePage.jsx`

---

## 3) Next Actions (inmediatas)

### P1 — Extender Live Re-Evaluation a Basketball y Baseball
- Replicar heurística live por deporte (puntos/carreras, tiempo, momentum).
- Definir mercados soportados por deporte (ej. Totales/Spreads/Run line).
- Añadir UI gating por deporte (o panel multi-deporte con mercados válidos).
- Testing end-to-end por deporte.

### P2 — Proxy residencial para Sofascore (opcional, requiere credenciales) — **BLOCKED**
- Integrar proxy residencial en Crawlee/Playwright.
- Añadir variables `.env` (host/usuario/pass o API key proveedor).
- Re-test Sofascore scheduled-events.

### P2 — Stats segmentados por `sport`
- ROI/Winrate por deporte y por mercado en `/api/stats/dashboard` + UI.

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
- ✅ **Saved Filter Views (P1):** persistencia en MongoDB (10 max con evicción) + UX + testing end-to-end.
- ✅ **Phase 8 + 8.1 (P0):** Tiering + hard gate + discovery dinámico funcionando.
- ✅ **Phase 9 (P0):** Fallback protegido Under 3.5/2.5 + badge + metadata.
- ✅ **Phase 10 (P0/P1):** Re-eval live con odds manual y edge calculado.
- ✅ **Phase 11 (P0):** LIVE solo muestra matches realmente activos; no stale 90’; freshness/archiving operativos.
- ✅ **Phase P2A (P1):** Under model StatsBomb-inspired mejora estimación de probabilidad (Poisson + shrinkage) + UI transparente.
- ✅ **Phase P2B (P2):** Provenance visible por match (badge) en Live + MatchCard.
- ✅ **Bugs P1/P2 resueltos:** sin errores BSON por keys no string; sin contaminación cross-sport.
- 🔁 **Operativo:** créditos LLM sostenibles para que análisis siga disponible.
