# Plan — Phases F58–F85 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).
> **Estado actual (resumen):** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA** + ✅ **P4.1 (LiveReevalPanel.test.jsx 3 preexistentes) COMPLETADA** + ✅ **F84.a (team_stats prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.b (H2H prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F85 (Public xG — FBref + Forebet vía scrape.do) COMPLETADA**.
>
> **Idioma operativo:** Español.

---

## 1) Objetivos

### Objetivos originales (F58)
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (shots/SOT/minutos)
  - FBref (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con override contextual.
- Añadir smoke tests y mantener suite global verde.
- (P2) UI wiring: panel independiente para Cross + Override + Player Props.

### Objetivos nuevos / extendidos (F69–F74)
- Editorial interno específico por partido (no genérico).
- Scrapers externos (Forebet / Sportytrader) como fallback.
- Normalización de identidad de mercado y reconciliación interno vs externo.
- Auditoría de predicciones externas contra fuerza del rival.
- Guardrails para **mercados UNKNOWN** (no edge, no trap, no discard).
- F74: **schema canónico** para enriquecimiento fútbol + probabilidades estimadas.
- F74-post: **adaptadores** para eliminar fragmentación de datos anidados.
- F74-post v2: **fallback de odds con TheStatsAPI** (incluye opening/last_seen → line movement sin snapshots históricos).
- F74-post v2.5: **line movement desde día 1** usando `opening` TheStatsAPI + `last_seen`.

### Objetivos nuevos / extendidos (F82)
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales (Under 3.5, BTTS, promedio de goles).
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente en `football_data_enrichment.corners`.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia; permitir *watchlist* bajo condiciones live (extensión futura si hace falta).

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners solo desde datos ya presentes (API-Sports/live_stats o snapshot TheStatsAPI). **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs por fecha/nombres). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal de picks.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual:
  - cuota detectada
  - cuota manual editable
  - selector de mercado / selección / línea
  - botón “Recalcular con mercado manual”
  - resultado recalculado: edge, fragilidad, confianza, veredicto
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular **promedios xG no-penal** (a favor / en contra) para **L1/L5/L15** por equipo usando `GET /football/matches/{match_id}/shotmap`.
- Arquitectura **background-first** con cache + timeouts cortos:
  - Nunca bloquear el generador principal de picks.
  - Si falla shotmap: **no inventar datos** → render parcial con reason_codes.
- Señales analíticas (contextuales):
  - `LOW_RECENT_XG_PROFILE`, `DEFENSIVE_XG_SUPPRESSION`, `XG_FORM_SHIFT`, `XG_APOYA_UNDER`, `XG_APOYA_OVER`.
- Señales de **muestra parcial** (gobernanza editorial):
  - `XG_PARTIAL_SAMPLE`, `XG_L1_ONLY`, `XG_L5_AVAILABLE_L15_MISSING`, `XG_L15_AVAILABLE_L5_MISSING`, `XG_RECENT_SAMPLE_INSUFFICIENT`.
  - Regla clave: **si solo hay L1** (o no hay L5/L15 para ambos lados), el sistema no debe usar xG como confirmación fuerte para Over/Under.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable: alinear tests con la copy/comportamiento real del toast y el flujo de confirmación del EnginePickConfirmModal.

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales para fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - **F84.a**: `team_stats/season_aggregates` (forma, GF/GA season, etc.)
  - **F84.b**: `h2h` (head-to-head)
  - **F84.e (pendiente)**: `odds` (promover TheStatsAPI a primaria)
- Introducir flag:
  - `ENABLE_API_SPORTS_FALLBACK` (default `true`) para modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only” en staging.
- Auditoría runtime:
  - Persistir `_provenance_*` en match_doc para saber qué proveedor sirvió cada sección.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Construir una capa pública de scraping/ingesta para fútbol, estilo “FootyStats premium”, usando:
  - **FBref** como fuente **primaria** de xG/npxG/xGA/npxGA (por logs recientes de equipo).
  - **Forebet** como **contexto** (predicción, score proyectado, probs 1X2, hints O/U, texto), **nunca como fuente de xG**.
- Persistir resultados de forma **fail-soft**, con **timeouts** y arquitectura **run-now/background** para que **no bloquee** el generador principal.
- Exponer UI para disparar enriquecimiento y renderizar datos parciales sin ocultar otros bloques.

---

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador
**(COMPLETADO)** — sin cambios.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**(COMPLETADO)** — sin cambios.

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**(COMPLETADO)** — sin cambios.

### Fase 4 — Integración en Football pipeline (override incluido)
**(COMPLETADO)** — sin cambios.

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase F69 — Fix análisis editorial interno match-specific (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

## Phase F70 — Reemplazo externo (Sportytrader / Forebet) (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

# Phase F74 — Parcial: Unified Football Enrichment + Protected Floor Recalibration (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2 — TheStatsAPI Odds Fallback Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2.5 — Opening Odds → Line Movement Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82 — Rich H2H Context + 365Scores Corners Ingestion (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1 — Non-blocking H2H/Corners Enrichment + Job Timeout Protection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83 — Manual Market Identity + Manual Odds Injection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1-adjust — Manual/Background Corners Enrichment Endpoints (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.1-fix — Manual Market Identity match_id isolation + Data Availability Sections (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase P2 — `infer_original_pick_side` (4-source cascade) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.2 — Scores24 → 365Scores cross integrator + provider re-order (Backend COMPLETED ✅ / Frontend COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.2 (Bloque E) — xG L1/L5/L15 desde TheStatsAPI shotmap (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Implementación ejecutada

### Backend
- ✅ `services/external_sources/thestatsapi_shotmap_client.py`
- ✅ `services/football_xg_recent_averages.py`
- ✅ `services/football_xg_signals.py` (señales parciales + guardrail L1-only)
- ✅ `server.py` (endpoints `/api/football/xg-recent-averages/*`)

### Frontend
- ✅ `frontend/src/components/XGRecentAveragesPanel.jsx`
- ✅ `frontend/src/components/MatchIntelligencePanel.jsx` (wiring verificado)

### Testing
- ✅ `backend/tests/test_f83_2_xg_recent_averages.py` — 30 tests
- ✅ `frontend/src/components/__tests__/XGRecentAveragesPanel.test.jsx` — 12 tests

## Validación
- ✅ Backend `pytest`: **2313 passed, 2 skipped, 0 fallos** (antes: 2283).
- ✅ Frontend: `craco test` panel: **12/12 pasan**.

---

# Phase P4.1 — Fix tests preexistentes LiveReevalPanel (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Implementación ejecutada
- ✅ Timeout manual esperado: `45000 → 15000`.
- ✅ Copy del toast de timeout manual: valida `/no se pudo recalcular la cuota/i` y `/tus datos siguen ingresados/i`.
- ✅ Flujo engine-source WON: el test confirma `EnginePickConfirmModal` (`engine-pick-confirm-yes`) antes de validar el POST `/picks/track`.

## Validación
- ✅ Frontend `craco test`: **112/112 tests pasan**, 0 regresiones.

---

# Phase F84 — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)

## Estado: ✅ F84.a COMPLETADA + ✅ F84.b COMPLETADA (F84.e pendiente)

## Documento de mapeo
- ✅ `/app/docs/F84_thestatsapi_migration_mapping.md`

## Estrategia (confirmada)
- TheStatsAPI primaria.
- API-Sports fallback con `ENABLE_API_SPORTS_FALLBACK` (default `true`).
- Fail-soft estricto.
- Auditoría runtime: `_provenance_*`.

## F84.a — team_stats / season_aggregates (COMPLETED ✅)
- ✅ **NEW** `backend/services/external_sources/thestatsapi_team_stats_adapter.py`
- ✅ **MOD** `backend/services/data_ingestion.py` (inversión de prioridad + `_provenance_team_stats`).
- ✅ **NEW** `backend/tests/test_f84_a_team_stats_adapter.py` — 27 tests.
- ✅ Validación: **2340 passed, 2 skipped, 0 fallos**.

## F84.b — head-to-head (H2H) (COMPLETED ✅)
- ✅ **NEW** `backend/services/external_sources/thestatsapi_h2h_adapter.py`
- ✅ **MOD** `backend/services/data_ingestion.py` (inversión de prioridad + `_provenance_h2h`).
- ✅ **NEW** `backend/tests/test_f84_b_h2h_adapter.py` — 26 tests.
- ✅ Validación: **2366 passed, 2 skipped, 0 fallos**.

---

# Phase F85 — Public xG Enrichment (FBref + Forebet vía scrape.do) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Construir una capa pública de scraping/ingesta similar a una versión premium de FootyStats usando:
- **FBref** como fuente primaria de xG/npxG/xGA/npxGA (por logs de equipo).
- **Forebet** como contexto (predicción, score, probs, hints) — **nunca** como xG.

Arquitectura:
- **Background-first** + **run-now** explícito.
- Timeouts duros.
- Fail-soft total.
- Persistencia en `xg_public_enrichment` y mirror a `xg_recent_averages` cuando aplica.

---

## F85.1 — `fbref_client.py` (COMPLETED ✅)

### Backend (NEW)
- `services/external_sources/fbref_client.py`
  - `resolve_fbref_team_url(client, team_name, *, db=None)`:
    - Tabla estática inicial con 10 selecciones (USA, Paraguay, Mexico, Brazil, Argentina, Uruguay, Germany, France, Spain, England) + aliases.
    - Fallback a Mongo `external_team_mappings`.
    - Normalización agresiva (lower, sin acentos, drop suffixes, `&`→`and`).
  - `fetch_fbref_team_match_logs(client, team_url, *, limit=15)`:
    - Fetch dual: scrape.do en prod / httpx client en tests.
    - Extrae tablas dentro de comentarios `<!-- -->`.
    - Parse por headers `data-stat` con coerción robusta y **sin inventar datos**.

### Tests
- ✅ `backend/tests/test_f85_1_fbref_client.py` — **47 tests**.

---

## F85.2 — `forebet_client.py` (match-detail) (COMPLETED ✅)

### Backend (NEW)
- `services/external_sources/forebet_client.py`
  - `parse_forebet_match_html(...)`:
    - Teams, score proyectado, pick 1X2, probabilidades, hints O/U, avg goals, summary.
    - Marca unavailable si no detecta payload.
  - `fetch_forebet_match_context(client, url)`:
    - Valida host.
    - Fetch dual: scrape.do / httpx.

### Tests
- ✅ `backend/tests/test_f85_2_forebet_client.py` — **14 tests**.

---

## F85.3 — Normalizer + Signals + Ingestor (COMPLETED ✅)

### Backend (NEW)
- `services/football_xg_public_normalizer.py`
  - `compute_fbref_xg_recent_averages(...)` (L1/L5/L15, npxG opcional, partial, derived, skip None).
- `services/football_xg_public_signals.py`
  - `derive_public_xg_signals(xg, forebet)` (UNDER/OVER, suppression, form shift, forebet conflict/confirm, partial).
- `services/football_xg_public_ingestor.py`
  - `enrich_public_xg_context(client, db, match_doc, *, forebet_url, timeout_s=8)`
    - resolve + fetch en paralelo, wait_for con TIMEOUT payload, data_quality, reason_codes agregados.
  - Flags:
    - `ENABLE_INLINE_PUBLIC_XG_SCRAPING` (default false)
    - `ENABLE_BACKGROUND_PUBLIC_XG_SCRAPING` (default true)
    - `PUBLIC_XG_SCRAPER_TIMEOUT_SECONDS` (default 8)

### Tests
- ✅ `backend/tests/test_f85_3_public_xg.py` — **34 tests**.

---

## F85.4 — Endpoints `/api/football/public-xg-enrichment/*` (COMPLETED ✅)

### Backend (MOD)
- `server.py`
  - `PublicXGEnrichmentRequest` (match_id + sources + forebet_url + force)
  - `_persist_public_xg_to_run(match_id, payload)`
  - `_do_public_xg_fetch(match_id, *, forebet_url, sources)`
  - `_run_background_public_xg_job(...)`
  - Endpoints:
    - `POST /api/football/public-xg-enrichment/run-now`
    - `POST /api/football/public-xg-enrichment/background`
    - `GET  /api/football/public-xg-enrichment/status/{match_id}`

### Tests
- ✅ `backend/tests/test_f85_4_public_xg_endpoints.py` — **16 tests**.

---

## F85.5 — Frontend: Panel + wiring (COMPLETED ✅)

### Frontend (NEW)
- `frontend/src/components/PublicXGPanel.jsx`
- `frontend/src/components/PublicXGEnrichmentButton.jsx` (alias/re-export)
- `frontend/src/components/MatchIntelligencePanel.jsx` (render después de `XGRecentAveragesPanel`)

### Tests
- ✅ `frontend/src/components/__tests__/PublicXGPanel.test.jsx` — **13 tests**.

---

## Validación final F85
- ✅ Backend `pytest`: **2477 passed, 2 skipped, 0 fallos**.
- ✅ Frontend `craco test`: **125 passed, 0 fallos**.
- ✅ esbuild: componentes compilan limpio.

---

## 3) Pendientes y siguientes pasos (post-F85)

### Pendientes no bloqueantes
- (F84.e) **Promover odds a primaria** con TheStatsAPI (mantener fallback API-Sports con `ENABLE_API_SPORTS_FALLBACK`).
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (F85 Phase 2) Resolver URLs FBref vía search-page + fuzzy matching (ahora MVP es mapping + Mongo).

### Fuera de scope inicial (documentado)
- (F84.c) lineups / injuries (TheStatsAPI coverage no confirmada todavía).
- (F84.d) standings.

---

## 6) Validación esperada (estado actual)

- Flags:
  - `ENABLE_API_SPORTS_FALLBACK=true` mantiene modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only”.
  - `ENABLE_INLINE_PUBLIC_XG_SCRAPING=false` mantiene scraping fuera del camino crítico.
- Auditoría runtime:
  - `match_doc._provenance_team_stats` y `match_doc._provenance_h2h` presentes.
  - `match_doc.xg_public_enrichment` persistido al ejecutar run-now/background.
- No regresiones:
  - Backend `pytest` verde (actual: **2477 passed**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → se cae a API-Sports o queda vacío.
  - Si FBref/Forebet fallan → el análisis principal no se bloquea y el UI muestra datos parciales.
