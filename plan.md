# Plan — Phases F58–F88 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).
> **Estado actual (resumen):** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA** + ✅ **P4.1 (LiveReevalPanel.test.jsx 3 preexistentes) COMPLETADA** + ✅ **F84.a (team_stats prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.b (H2H prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.e (odds prioridad-inversa a TheStatsAPI + line movement) COMPLETADA** + ✅ **F85 (Public xG — FBref + Forebet vía scrape.do) COMPLETADA** + ✅ **F85 Phase 2 (FBref search-page resolver + fuzzy matching) COMPLETADA** + ✅ **F86 (H2H Decision Policy puro en Python) COMPLETADA** + ✅ **F87 (Cableado quirúrgico en `_enrich_football`: H2H decision + xG-recent background dispatch) COMPLETADA** + ✅ **F88 (F86.2 — Editorial Consumer: h2h_decision + xg_recent_averages + UI) COMPLETADA**.
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
  - **F84.a**: `team_stats/season_aggregates` (forma, GF/GA season, etc.) ✅
  - **F84.b**: `h2h` (head-to-head) ✅
  - **F84.e**: `odds` (promover TheStatsAPI a primaria) ✅
- Introducir flag:
  - `ENABLE_API_SPORTS_FALLBACK` (default `true`) para modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only” en staging.
- Auditoría runtime:
  - Persistir `_provenance_*` en match_doc para saber qué proveedor sirvió cada sección (`_provenance_team_stats`, `_provenance_h2h`, `_provenance_odds`).

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Construir una capa pública de scraping/ingesta para fútbol, estilo “FootyStats premium”, usando:
  - **FBref** como fuente **primaria** de xG/npxG/xGA/npxGA (por logs recientes de equipo).
  - **Forebet** como **contexto** (predicción, score proyectado, probs 1X2, hints O/U, texto), **nunca como fuente de xG**.
- Persistir resultados de forma **fail-soft**, con **timeouts** y arquitectura **run-now/background** para que **no bloquee** el generador principal.
- Exponer UI para disparar enriquecimiento y renderizar datos parciales sin ocultar otros bloques.
- **F85 Phase 2:** Resolver URLs FBref vía search-page + fuzzy matching cuando static mapping/Mongo miss ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Implementar un módulo de decisión para H2H **sin I/O**, que defina estrictamente:
  - Cuándo H2H es **decisivo** para puntuar mercados (requiere `MIN_DECISION_SAMPLE=4` en historia reciente).
  - Cuándo H2H es **solo narrativo** (contexto + warnings visibles en UI).
- Output:
  - `h2h_context` enriquecido con `warnings`, `recent_within_1y`, `decision_useful`.
  - `h2h_decision` con `applied`, `points_by_market` y `signals`.

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football` (data_ingestion)
- Integrar **sin refactor grande** (cambio quirúrgico) dos comportamientos en el pipeline:
  1) **H2H Decision Policy** inmediatamente tras construir el contexto.
  2) **xG recent averages (L1/L5/L15) en background** (fire-and-forget) para evitar latencias P95.
- Garantía central:
  - **No bloquear** el camino crítico.
  - **Fail-soft total** (no propaga excepciones al caller).
  - Persistencia consistente en Mongo + mutación del `match_doc` en memoria.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer para `h2h_decision` + `xg_recent_averages`
- Consumir los campos del ingestor (F85+F86+F87):
  - `match_doc["h2h_context"]` (enriquecido con warnings/decision_useful)
  - `match_doc["h2h_decision"]` (nuevo: `points_by_market` + `signals`)
  - `match_doc["xg_recent_averages"]` (dispatch en background, puede ser PENDING)
- Objetivos del sprint:
  1) Editorial renderiza partidos H2H uno por uno cuando la muestra es pequeña, y agregados/rates cuando es decision_useful.
  2) Motor de scoring suma puntos de `h2h_decision.points_by_market` a `confidence_score` por mercado afectado, sin doble conteo; clamp +8 y polarity guard.
  3) Editorial respeta estados `PENDING|SUCCESS|TIMEOUT|UNAVAILABLE` de xG recent averages y renderiza L1/L5/L15 cuando SUCCESS.

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

## Estado: ✅ F84.a COMPLETADA + ✅ F84.b COMPLETADA + ✅ F84.e COMPLETADA

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

## F84.e — odds → primaria TheStatsAPI + line movement (COMPLETED ✅)

### Objetivo
Invertir prioridad de odds: antes API-Sports primario + TheStatsAPI fallback. Ahora:
- TheStatsAPI **primaria** (preserva `opening` → line movement)
- API-Sports como fallback bajo `ENABLE_API_SPORTS_FALLBACK` (default true)

### Implementación ejecutada
- ✅ **NEW** `backend/services/external_sources/thestatsapi_odds_adapter.py`
  - `fetch_odds_api_sports_shape(...) -> (odds_resp, norm_odds, match_id)`
  - Usa raw id cuando está disponible; resuelve por names+date+competition si falta.
  - Normaliza a shape API-Sports mediante `thestatsapi_normalizer.normalize_thestatsapi_odds_to_apisports_shape`.
  - Preserva `_opening_odds` en `norm_odds`.
  - Fail-soft (nunca raise): retorna `(None, None, None)` en miss.
- ✅ **MOD** `backend/services/data_ingestion.py`
  - Odds branch reescrito: TheStatsAPI → si miss AND flag → API-Sports → si miss → late retry TheStatsAPI.
  - `odds_source`: `thestatsapi | api_sports_fallback | thestatsapi_late | no_odds`.
  - `_provenance_odds` añadido al match_doc.

### Tests
- ✅ `backend/tests/test_f84_e_odds_adapter.py` — **17 tests**.

### Validación
- ✅ Backend `pytest` combinado (con F85 Phase 2): **2521 passed, 2 skipped, 0 fallos**.

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
- Persistencia en `xg_public_enrichment`.

---

## F85.1 — `fbref_client.py` (COMPLETED ✅)

### Backend (NEW)
- `services/external_sources/fbref_client.py`
  - `resolve_fbref_team_url(client, team_name, *, db=None)`:
    - Tabla estática inicial con 10 selecciones + aliases.
    - Fallback a Mongo `external_team_mappings`.
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
- `services/football_xg_public_signals.py`
- `services/football_xg_public_ingestor.py` (timeout-safe, fail-soft, data_quality, flags)

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

## F85 Phase 2 — FBref search-page resolver + fuzzy matching (COMPLETED ✅)

### Objetivo
Cuando el resolver no encuentra una URL en el static mapping ni en Mongo cache:
- scrape de la search-page de FBref (`/en/search/search.fcgi?search=...`)
- fuzzy matching para aceptar el mejor candidato
- persistir el hit en `external_team_mappings` para evitar llamadas futuras

### Implementación ejecutada (MOD fbref_client.py)
- Nuevos helpers:
  - `_fuzzy_similarity(a, b)` usando `SequenceMatcher` sobre nombres normalizados.
  - `_parse_fbref_search_results(html)` para extraer `search-item` → `/en/squads/...`.
  - `_search_fbref_for_team(client, query)` con `follow_redirects=False` y soporte 302 single-hit.
  - `_best_fuzzy_hit(candidates, query, threshold=0.78)` (no muta inputs).
  - `_persist_search_hit_to_mongo(db, ...)` upsert fail-soft (`discovered_via='search_fuzzy'`, `fuzzy_score`, `aliases_norm`).
- Integración en `resolve_fbref_team_url`:
  - Tier 1: `static_mapping`
  - Tier 2: `mongo_mapping`
  - Tier 3: `search_fuzzy` (solo si `client is not None` — evita I/O cuando `client=None`)
- Config:
  - `FBREF_SEARCH_FUZZY_THRESHOLD` (default 0.78)

### Tests
- ✅ `backend/tests/test_f85_phase2_search_resolver.py` — **27 tests**.

---

# Phase F86 — H2H Decision Policy (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Implementar una política de decisión **pura** (sin I/O) para definir cuándo H2H:
- suma puntos a mercados (cuando `MIN_DECISION_SAMPLE=4` en historia reciente), o
- queda como **contexto narrativo** (warnings + `decision_useful=false`).

## Implementación ejecutada
- ✅ `backend/services/football_h2h_decision_policy.py`
  - `classify_h2h_context(h2h_context, h2h_recent)` → enriquece warnings/reason_codes y define `decision_useful`.
  - `apply_h2h_decision_points(classified, home_name, away_name)` → `points_by_market`, `signals`, `rates`, `applied`.
  - `build_h2h_decision(match_doc)` → wrapper `(classified, decision)`.

## Tests
- ✅ `backend/tests/test_f86_h2h_decision_policy.py`

---

# Phase F87 — Cableado quirúrgico H2H Decision + xG-recent background dispatch en `_enrich_football` (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Motivación
- El cálculo de xG reciente (L1/L5/L15) puede requerir **hasta 30 HTTP calls** (15 por lado) al endpoint shotmap.
- Para proteger el P95 del ingestor y evitar 504/latencia, el cómputo se despacha **en background**.
- El H2H rico (F82) era informativo, pero faltaba el **gating decisional** (F86) para scoring.

## Implementación ejecutada (MOD: `backend/services/data_ingestion.py`)

### A) Helpers nuevos (antes de `_enrich_football`)
- ✅ `_ensure_thestatsapi_recent_match_ids(match_doc, fid)`
  - Pobla `home_team.thestatsapi_recent_match_ids` y `away_team.thestatsapi_recent_match_ids` si faltan.
  - Fuente: `services.external_sources.thestatsapi_client.fetch_recent_match_ids(team_id, n=15)`.
  - Fail-soft total (nunca raise).
- ✅ `_schedule_xg_recent_background(match_doc, fid, db)`
  - Ejecuta `compute_xg_recent_averages(match_doc)` bajo `asyncio.wait_for(..., timeout=30s)`.
  - Persiste en Mongo: `db.matches.update_one({"match_id": fid}, {"$set": {"xg_recent_averages": result}})`.
  - Muta `match_doc["xg_recent_averages"] = result` en memoria.
  - Estados:
    - `SUCCESS` cuando `available=true`
    - `UNAVAILABLE` cuando `available=false`
    - `TIMEOUT` con reason `XG_RECENT_BACKGROUND_TIMEOUT`
  - Fail-soft total.

### B) Bloque H2H Decision Policy (después de `build_h2h_context`)
- ✅ `classified, decision = build_h2h_decision(match_doc)`
- ✅ Persistencia:
  - `match_doc["h2h_context"]  = classified` (incluye `warnings`, `recent_within_1y`, `decision_useful`, etc.)
  - `match_doc["h2h_decision"] = decision` (`points_by_market`, `signals`, `applied`, etc.)

### C) Dispatch xG recent averages en background (justo después)
- ✅ Inicialización no bloqueante:
  - `match_doc.setdefault("xg_recent_averages", {"available": False, "status": "PENDING_BACKGROUND_ENRICHMENT", "reason_codes": ["XG_RECENT_BACKGROUND_DEFERRED"]})`
- ✅ Fire-and-forget:
  - `asyncio.create_task(_schedule_xg_recent_background(match_doc, fid, db))`

## Contrato para el consumer (editorial / scoring engine)
- UI H2H:
  - `h2h_context.warnings`
  - `h2h_context.recent_within_1y`
  - `h2h_context.decision_useful` (badge informativo vs decisivo)
- Scoring:
  - Si `h2h_decision.applied` entonces sumar `points_by_market` y anexar `signals`.

## Validación
- ✅ Lint Python: sin issues bloqueantes.
- ✅ Smoke import: OK.
- ✅ `pytest` selectivo (F83.2/F85/F86): **109 passed**.
- ✅ Tests relevantes de ingesta (F74 + Batch3): **41 passed**.
- ✅ Suite completa backend: **2573 passed, 2 skipped, 0 fallos** (169s) — **zero-regression confirmada**.

---

# Phase F88 — Sprint F86.2: Editorial Consumer para H2H decision + xG recent averages (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Problema
El ingestor ya producía:
- `match_doc["h2h_context"]` (con warnings/decision_useful)
- `match_doc["h2h_decision"]` (points_by_market + signals)
- `match_doc["xg_recent_averages"]` (background con estado PENDING/SUCCESS/TIMEOUT)

Pero el editorial interno y la UI seguían:
- mostrando checks genéricos (p. ej. "✓ H2H reciente cargado"),
- hardcodeando textos de muestra limitada,
- marcando "⚠ xG no normalizado" incluso cuando el background ya había completado.

## Implementación ejecutada

### Backend
1) ✅ **NEW** `backend/services/football_h2h_scoring_applier.py`
   - `_market_to_h2h_key(market_label)` / alias `market_to_h2h_key`:
     mapea labels internos y strings (OVER_2_5, OVER_2_5_GOALS, Over 2.5 goles,
     BTTS_NO_GOALS, HOME_NO_LOSE, etc.) al naming canónico (OVER_2_5, BTTS_NO,
     HOME_DNB, …).
   - `apply_h2h_points_to_candidate(candidate, h2h_decision)`:
     - Polarity guard `_enforce_polarity`.
     - Clamp ±`MAX_H2H_DELTA` (=8).
     - Mutación in-place: `confidence_score += delta`, señales y
       `score_breakdown["h2h_pattern"] = delta`.
     - Compatible con candidates tipo dict u objeto.
     - Logging: `[h2h_scoring] market=%s delta=%+d signals=%s ...`.

2) ✅ **MOD** `backend/services/football_editorial_prediction.py`
   - **NEW** `_build_h2h_block(match_doc)`:
     - Consume `h2h_context`, `h2h_decision`, `h2h_recent`.
     - Genera `matches_detail` SIEMPRE: `{date, home, away, score, is_recent, result_for_home}`.
     - `rates` solo si `decision_useful=True`.
     - `applied_signals` desde `h2h_decision.signals`.
     - Narrativa ES con templates del spec.
   - **NEW** `_build_xg_block(match_doc)`:
     - `PENDING_BACKGROUND_ENRICHMENT` → `status=PENDING` + mensaje "refresca en 10s".
     - `TIMEOUT/UNAVAILABLE` → `status=UNAVAILABLE` + missing_reason.
     - `SUCCESS` → tabla L1/L5/L15 + `derive_xg_signals` (signals + explanations).
   - Wrapper extendido de `generate_football_editorial_prediction`:
     - Inyecta `out["h2h_block"]` y `out["xg_block"]`.
     - Aplica H2H scoring al `best_protected_market` (back-compat con `confidence`).
     - Añade reason codes:
       - `H2H_SCORING_APPLIED_TO_BEST_PROTECTED_MARKET`
       - `H2H_SCORING_CLAMPED_AT_MAX_DELTA`
       - `H2H_SCORING_POLARITY_CONFLICT`
   - Housekeeping: arregladas 6 ocurrencias E701 preexistentes para mantener lint-clean.

### Frontend
3) ✅ **NEW** `frontend/src/components/H2HBlockPanel.jsx`
   - Badge “Decisivo” vs “Solo contexto”.
   - Narrative + warning banner.
   - Chips verdes “+N MARKET” cuando `decision_useful=True`.
   - Rates table para rates clave.
   - `matches_detail` siempre (colapsable cuando decisivo y >3).

4) ✅ **NEW** `frontend/src/components/XGBlockPanel.jsx`
   - `PENDING` → spinner + texto.
   - `UNAVAILABLE/TIMEOUT` → warning ámbar.
   - `SUCCESS` → tabla 2×3 (Home/Away × L1/L5/L15) + chips de signals.

5) ✅ **MOD** `frontend/src/components/EditorialPredictionPanel.jsx`
   - Si `editorial.h2h_block` existe: renderiza `H2HBlockPanel`.
   - Fallback conservador a `H2HContextPanel` para zero-regression.
   - Si `editorial.xg_block` existe: renderiza `XGBlockPanel`.

### Tests
6) ✅ **NEW** `backend/tests/test_f86_2_editorial_consumer.py` — 19 tests
   - Cubre: thin sample rendering, scoring apply, clamp +8, polarity guard,
     PENDING xG no bloquea, SUCCESS xG render + signals, mapping markets,
     y bump end-to-end sobre best_protected_market.

## Validación
- ✅ Ruff: limpio.
- ✅ ESLint: limpio.
- ✅ esbuild (smoke build del panel): OK.
- ✅ `pytest tests/test_f86_2_editorial_consumer.py`: 19/19 PASS.
- ✅ pytest selectivo (F86.2 + F86 + F83.2 + F69 + F74 + F82.1 + F85.3 + F85.4): 173/173 PASS.
- ✅ Suite completa backend: **2592 passed, 2 skipped, 0 fallos**.
- ✅ Suite completa frontend (`craco test`): **125 passed, 0 fallos**.

---

## 3) Pendientes y siguientes pasos (post-F88)

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Futuras mejoras recomendadas
- Backtest F86 (≥ 30 picks con H2H aplicado) para recalibrar `MAX_H2H_DELTA` y thresholds.
- Para FBref Phase 2: ampliar heurísticas (country/team_type) para equipos UCL/UEL.
- Para odds: agregar comparación de `bookmakers_count` TSA vs APS como métrica de calidad.
- Para `data_ingestion.py` y `server.py`: refactor incremental (pero **evitar extracción excesiva** para no romper; solo cambios quirúrgicos cuando sean necesarios).

---

## 6) Validación esperada (estado actual)

- Flags:
  - `ENABLE_API_SPORTS_FALLBACK=true` mantiene modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only”.
  - `ENABLE_INLINE_PUBLIC_XG_SCRAPING=false` mantiene scraping fuera del camino crítico.
- Auditoría runtime:
  - `match_doc._provenance_team_stats`, `match_doc._provenance_h2h`, `match_doc._provenance_odds` presentes.
  - `match_doc.xg_public_enrichment` persistido al ejecutar run-now/background.
  - `match_doc.h2h_context` + `match_doc.h2h_decision` presentes tras ingesta (F87).
  - `match_doc.xg_recent_averages.status` puede ser:
    - `PENDING_BACKGROUND_ENRICHMENT` al finalizar `_enrich_football` (F87)
    - `SUCCESS | UNAVAILABLE | TIMEOUT` al completar el background job.
  - Editorial output ahora incluye:
    - `editorial.h2h_block` (shape consumer-grade)
    - `editorial.xg_block` (status normalizado + tabla L1/L5/L15)
  - `best_protected_market.confidence_score` puede incorporar bump H2H con clamp+polarity.
- No regresiones:
  - Backend `pytest` verde (actual: **2592 passed, 2 skipped**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → se cae a API-Sports o queda vacío.
  - Si FBref/Forebet fallan → el análisis principal no se bloquea y la UI muestra datos parciales.
  - Si xG recent averages falla/timeout → el análisis principal no se bloquea; la UI deja de mostrar “PENDING” al persistir `UNAVAILABLE/TIMEOUT`.
