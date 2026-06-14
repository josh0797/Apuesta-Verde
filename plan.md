# Plan — Phases F58–F90 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).
> **Estado actual (resumen):** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA** + ✅ **P4.1 (LiveReevalPanel.test.jsx 3 preexistentes) COMPLETADA** + ✅ **F84.a (team_stats prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.b (H2H prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.e (odds prioridad-inversa a TheStatsAPI + line movement) COMPLETADA** + ✅ **F85 (Public xG — FBref + Forebet vía scrape.do) COMPLETADA** + ✅ **F85 Phase 2 (FBref search-page resolver + fuzzy matching) COMPLETADA** + ✅ **F86 (H2H Decision Policy puro en Python) COMPLETADA** + ✅ **F87 (Cableado quirúrgico en `_enrich_football`: H2H decision + xG-recent background dispatch) COMPLETADA** + ✅ **F88 (F86.2 — Editorial Consumer: h2h_decision + xg_recent_averages + UI) COMPLETADA** + ✅ **F89 (Sprint F86.1 — Calibración H2H rules + explicit polarity/sample/cap guards) COMPLETADA** + ✅ **F90 (Sprint F83-update — Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order) COMPLETADA**.
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
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales.
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia.

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners desde datos presentes. **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual (cuota manual, selector de mercado, recalcular).
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular promedios xG no-penal (a favor / en contra) L1/L5/L15 por equipo usando shotmap TheStatsAPI.
- Arquitectura **background-first** con cache + timeouts.
- Señales contextuales (nunca pick-binding) + señales de cobertura/muestra parcial.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable (alinear tests con copy y flujos reales).

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - F84.a Team Stats ✅
  - F84.b H2H ✅
  - F84.e Odds + line movement ✅
- Flags + auditoría `_provenance_*`.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Scraping fail-soft y background-first con endpoints run-now/background/status.
- UI panel para disparo y render.
- Phase 2: resolver FBref search-page + fuzzy matching ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Definir cuándo H2H puede influir en scoring vs. cuándo es solo narrativo.
- Output: `h2h_context` enriquecido + `h2h_decision` (points_by_market + signals).

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football`
- Integrar H2H decision + xG recent averages (background) sin bloquear el camino crítico.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer
- Editorial output y UI consumen `h2h_decision` + `xg_recent_averages`.
- Scoring aplica bump H2H al mercado (clamp +8 + guards).

### Objetivos nuevos / extendidos (F89 / Sprint F86.1) — Calibración H2H rules + guards explícitas
- Recalibrar `H2H_POINT_RULES` contra baselines típicas (más robusto).
- Introducir `get_active_rules()` con override por env (JSON) leído en tiempo de llamada.
- Agregar polarity guard explícito (OVER/UNDER por línea + BTTS YES/NO) con auditoría.
- Agregar sample guard por regla (`min_sample`) + señal `LOW_SAMPLE_H2H_SIGNAL`.
- Agregar DNB overlap guard suave (HOME_DNB + AWAY_DNB no es hard-conflict).
- Agregar cap agregado de puntos H2H (`MAX_H2H_POINTS_TOTAL=8`).
- Mantener back-compat con consumers/editorial UI.

### Objetivos nuevos / extendidos (F90 / Sprint F83-update) — Corners cascade con diagnóstico estructurado (Scrape.do)
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según:
  - proveedor
  - etapa (`ID_RESOLUTION`, `FETCH_STATS`, `FETCH_PAGE`, `PARSE_HTML`, `NORMALIZE`, etc.)
  - `reason_code` (token ausente, breaker abierto, HTTP 403/429/503, HTML sin stats, stats sin córners, etc.)
- Exponer un endpoint de diagnóstico:
  - `GET /api/football/corners/debug?match_id=...`
- Añadir UI para debug:
  - botón **"Ver debug de córners"**
  - dialog con cascade order usado, estado scrape.do (token+breaker) y providers_checked.
- Mantener el order por defecto de F82.2 (TSA→APS→365) y habilitar un order alternativo bajo flag:
  - `ENABLE_F83_CASCADE_ORDER=true` → APS→365→TSA

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
**(COMPLETADO)** — sin cambios.

---

# Phase P4.1 — Fix tests preexistentes LiveReevalPanel (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F84 — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F85 — Public xG Enrichment (FBref + Forebet vía scrape.do) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F86 — H2H Decision Policy (COMPLETED ✅)
**(COMPLETADO)** — ver fases F87/F88/F89 para wiring y calibración.

---

# Phase F87 — Cableado quirúrgico H2H Decision + xG-recent background dispatch (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F88 — Sprint F86.2: Editorial Consumer para H2H decision + xG recent averages (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F89 — Sprint F86.1: Calibración H2H_POINT_RULES + explicit polarity guard + sample guard (COMPLETED ✅)
**(COMPLETADO)** — sin cambios (ver sección histórica F89 en este archivo).

---

# Phase F90 — Sprint F83-update: Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Motivación
- `scrape_do_client.fetch_via_scrapedo` devolvía solo `HTML|None`, por lo que era imposible saber por qué fallaba la carga.
- La UI mostraba un mensaje genérico (“Falló la carga de córners”) sin guiar el debug.
- Se necesitaba:
  - **reason_code** + **stage** + **message_user/message_debug**
  - endpoint de diagnóstico
  - UI de debug
  - orden de cascada alternativo bajo flag sin romper el default F82.2.

## Implementación ejecutada

### Backend
1) **MOD** `backend/services/scrape_do_client.py`
- ✅ **NEW** `fetch_via_scrapedo_result(target_url, *, timeout, render, geo)` → dict diagnóstico:
  - `ok`, `html`, `status_code`, `target_url`, `provider`, `reason_code`, `message_debug`, `fetched_at`.
- ✅ Reason codes implementados:
  - `SCRAPEDO_TOKEN_MISSING`, `SCRAPEDO_BREAKER_OPEN`, `SCRAPEDO_HTTP_ERROR`,
    `SCRAPEDO_TIMEOUT`, `SCRAPEDO_EMPTY_BODY`, `SCRAPEDO_EXCEPTION`.
- ✅ `fetch_via_scrapedo` preservado como wrapper legacy:
  - `ok=True` → retorna `html`
  - `ok=False` → retorna `None`

2) **NEW** `backend/services/external_sources/score365_scrapedo_client.py`
- ✅ `extract_365scores_ids(match_doc)`:
  - cascade 5 pasos (external_ids, url, match_url, external_urls, pick/ui payload)
  - output: `{game_id, matchup_id, match_url, available, resolved_from}`
- ✅ `fetch_365scores_match_page(..., match_url)`:
  - usa `fetch_via_scrapedo_result(timeout=60, render=True, geo="mx")`
  - retorna dict con `stage`, `reason_code`, `message_user`, `message_debug`.
- ✅ `fetch_365scores_game_stats(..., game_id, matchup_id)`:
  - usa endpoint JSON vía scrape.do + parse JSON
  - reason `SCORE365_JSON_PARSE_FAILED` si no parsea
- ✅ `parse_365scores_corners_from_html(html)`:
  - extrae `__NEXT_DATA__` / `__INITIAL_STATE__`
  - DFS heurístico para encontrar `statistics|gameStats|stats|matchStats`
  - aliases multi-idioma (Corners, Corner Kicks, Córners, Tiros de esquina, Escanteios, …)
- ✅ `normalize_365scores_corners(raw)` idempotente con shape:
  - `{available, source:"365scores_scrapedo", provider:"365scores", transport:"scrape_do", home, away, total_corners, raw_stat_names, confidence:"USABLE", reason_codes:["CORNERS_FROM_365SCORES_SCRAPEDO"], fetched_at}`
- ✅ HTTP 403/429/503 mapeado a `SCORE365_BLOCKED_OR_FORBIDDEN`.

3) **MOD** `backend/services/football_corners_provider.py`
- ✅ **NEW flag** `ENABLE_F83_CASCADE_ORDER`:
  - default (F82.2): **TheStatsAPI → API-Sports → 365Scores**
  - F83 ON: **API-Sports → 365Scores → TheStatsAPI**
- ✅ **NEW** `debug_corners_cascade(match_doc, *, allow_external=True)`:
  - output: `{match_id, home, away, cascade_order_used, flag_enabled, scrapedo{enabled, breaker_status}, providers_checked[], winner, final{...}}`
  - `providers_checked` en orden real usado con `provider`, `transport`, `stage`, `reason_code`, `message_user`, `message_debug`, `retryable`.
- ✅ **NEW** `enrich_match_corners_f83(...)`:
  - ejecuta `debug_corners_cascade` y persiste winner/failure con diagnóstico en:
    - `match_doc.corners_snapshot`
    - `match_doc.football_data_enrichment.corners`
    - `match_doc.thestatsapi_snapshot.corners` (si existe)
- ✅ Fail-soft total (nunca rompe el análisis).

4) **MOD** `backend/server.py`
- ✅ **NEW** endpoint `GET /api/football/corners/debug?match_id=...`
  - carga match_doc desde `analyst_runs` vía `_load_match_doc_for_corners`
  - retorna diagnóstico estructurado
  - `match_doc_found=false` si no existe el match (igual corre cascade para diagnóstico)

### Frontend
5) **NEW** `frontend/src/components/CornersDebugDialog.jsx`
- ✅ Consume `GET /api/football/corners/debug?match_id=`.
- ✅ Renderiza:
  - `cascade_order_used` + `flag_enabled`
  - scrape.do health (`enabled`, `open_hosts`)
  - providers_checked con stage/reason/message_user/debug
  - final outcome + winner
- ✅ Botón “Reejecutar”.

6) **MOD** `frontend/src/components/CornersEnrichmentButton.jsx`
- ✅ Mapping `reason_code → mensaje específico` (ES/EN) para:
  - `SCORE365_ID_MISSING`, `SCRAPEDO_*`, `SCORE365_*`, `NO_CORNERS_PROVIDER_AVAILABLE`, `MATCH_NOT_FOUND`.
- ✅ En error:
  - muestra mensaje específico + `<code>` con el reason_code
  - botón **“Ver debug de córners”** abre `CornersDebugDialog`.

### Tests
7) **NEW** `backend/tests/test_f83_update_corners_debug.py`
- ✅ 29 tests cubriendo:
  - `fetch_via_scrapedo_result` reason codes + wrapper legacy
  - ID resolution cascade
  - parser HTML (__NEXT_DATA__ + alias multi-idioma)
  - cascade order default vs flag
  - debug endpoint shape

## Validación
- ✅ Ruff: limpio.
- ✅ Tests F83-update: **29/29 PASS**.
- ✅ Tests focales corners (F82.1/F82.2/F82-h2h/F83.1/F83.2/F83-update): **124/124 PASS**.
- ✅ Suite completa backend: **2635 passed, 2 skipped, 0 fallos** (2606 → 2635).
- ✅ Suite completa frontend: **125 passed, 12 suites, 0 fallos**.
- ✅ Smoke runtime: `curl /api/football/corners/debug?match_id=test_smoke` devuelve shape esperado.

## Compatibilidad
- `fetch_via_scrapedo` legacy preservado (no rompe callers existentes).
- `score365_client.py` legacy NO modificado.
- `enrich_match_corners*` legacy preservados; el nuevo flujo vive en helpers F83-update.
- Default F82.2 cascade order preservado; F83 order disponible bajo flag.

---

## 3) Pendientes y siguientes pasos (post-F90)

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Futuras mejoras recomendadas
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds, `MAX_H2H_POINTS_TOTAL` y el cap DNB.
- Implementar calibrador offline cuando exista una fuente estable (p.ej. `football_market_results`) + endpoint opcional.
- Para FBref Phase 2: ampliar heurísticas (country/team_type) para equipos UCL/UEL.
- Para odds: comparar `bookmakers_count` TSA vs APS como métrica de calidad.

---

## 6) Validación esperada (estado actual)

- Flags:
  - `ENABLE_API_SPORTS_FALLBACK=true` mantiene modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only”.
  - `ENABLE_INLINE_PUBLIC_XG_SCRAPING=false` mantiene scraping fuera del camino crítico.
  - `H2H_POINT_RULES_OVERRIDE` permite override JSON en runtime (solo en policy; tests usan monkeypatch).
  - `ENABLE_F83_CASCADE_ORDER=true` (opcional) invierte corners cascade a **APS → 365Scores → TSA**.
- Auditoría runtime:
  - `match_doc._provenance_team_stats`, `_provenance_h2h`, `_provenance_odds` presentes.
  - `match_doc.h2h_context` + `match_doc.h2h_decision` presentes tras ingesta (F87).
  - `match_doc.xg_recent_averages.status`: `PENDING_BACKGROUND_ENRICHMENT → SUCCESS|UNAVAILABLE|TIMEOUT`.
  - Editorial output incluye:
    - `editorial.h2h_block` (consumer-grade)
    - `editorial.xg_block` (PENDING/SUCCESS/TIMEOUT/UNAVAILABLE + tabla L1/L5/L15)
  - `best_protected_market.confidence_score` puede incorporar bump H2H con clamp+polarity.
  - Corners debug:
    - `GET /api/football/corners/debug?match_id=...` expone `cascade_order_used`, `flag_enabled`, `providers_checked` y `winner`.
- No regresiones:
  - Backend `pytest` verde (actual: **2635 passed, 2 skipped**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → fallback API-Sports o bloque vacío.
  - Si scraping FBref/Forebet falla → no bloquea; UI muestra parcial.
  - Si xG recent averages falla/timeout → no bloquea; UI informa estado.
  - Si corners fallan → nunca rompe el análisis; UI informa reason_code y ofrece debug.
