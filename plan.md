# Plan — Phases F58–F83 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + Home/Visitante labels) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + 5 FE tests) COMPLETADA**.  
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

### Estado global (Phases F58–F70)
- ✅ F58 completado (backend + UI + scripts + tests).
- ✅ F60 completado (external context gate + corner cross wiring).
- ✅ F61/F61.1 completado (under support simétrico + promoción dc_nb_delta).
- ✅ F62/F63 completado (Scores24 review + soft/hard discard + UI badge + live patch).
- ✅ F64 completado (structural review + bucket `watchlist_odds_needed` + UI + tests).
- ✅ F65 completado (Bright Data circuit breaker + watchlist backtest + cron + endpoints + tests).
- ✅ F66 completado (Motor Editorial Interno + TheStatsAPI + Dixon-Coles contextual-only + UI panel + tests).
- ✅ F67 completado (guardrails, telemetry `discard_rescue_audit`, H2H cron, match_id mapping, heatmaps lazy + tests).
- ✅ F68 completado (player_id mapping por nombre + endpoint by-name + wiring UI + tests).
- ✅ F69 completada (editorial interno match-specific + anti-duplicado + THIN honesto + tests).
- ✅ F70 completada (scraping externo Forebet + Sportytrader vía scrape.do + caching TTL + UI + tests).

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

## Estado: ✅ COMPLETADA

## Objetivo
1) Unificar el enriquecimiento de datos de fútbol en un **schema canónico** (multi-proveedor) y habilitar `estimated_probabilities` por `market_identity_key` cuando la calidad de datos lo permita.  
2) Recalibrar tolerancias (floors) para mercados **PROTECTED** con reglas granulares por familia/line (DC, DNB, U3.5/U4.5, O1.5).  
3) **Prioridad crítica:** si `market_identity_key` es **UNKNOWN**, **bloquear** cálculo de edge/market-trap/protected-below-floor y rutear a `REQUIRES_MARKET_IDENTIFICATION`.

## Requisitos confirmados (conversación) — cumplidos
- ✅ **Over 1.5** movido a categoría **PROTECTED**.
- ✅ Floors protegidos granulares:
  - **DOUBLE_CHANCE (DC)** → `negative_edge_floor = -0.04`
  - **DNB** → `negative_edge_floor = -0.03`
  - **Under 3.5 / Under 4.5** → `negative_edge_floor = -0.03`
  - **Over 1.5** → `negative_edge_floor = -0.02`
  - Resto de PROTECTED → default `-0.015`
- ✅ `football_data_enrichment.py` canónico:
  - Acepta `_thestatsapi_enrichment`, `thestatsapi_snapshot`, `external_context.thestatsapi`, API-Sports (si hay stats útiles), Forebet.
  - `estimated_probabilities` solo si `data_quality != THIN`.
- ✅ Probabilidades:
  - Dixon-Coles → Poisson fallback → logística observe-only.

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Reducir falsos positivos de:
- `REQUIRES_MARKET_IDENTIFICATION`
- “Análisis interno no disponible / THIN”

…cuando los datos sí existen pero están anidados/fragmentados.

## Implementación ejecutada — 9 cambios
- ✅ Adapter editorial (`football_editorial_payload_adapter.py`) para aplanar recent fixtures + TheStatsAPI + live_stats.
- ✅ Normalizer compat (`football_data_enrichment_normalizer.py`) que persiste un solo payload en:
  - `match['football_data_enrichment']` y `match['thestatsapi_snapshot']` (alias del mismo objeto).
- ✅ Resolver de market identity por odds y bucket AMBIGUOUS.
- ✅ Aliases ES/EN en alternative_rescue.
- ✅ UI debug collapsible “Ver detalle del análisis”.

---

# Phase F74-post v2 — TheStatsAPI Odds Fallback Wiring (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
API-Sports como fuente primaria de odds; si viene vacío/inútil, rescatar con TheStatsAPI:
- Endpoint odds: `/football/matches/{match_id}/odds`.
- Convertir shape nested → API-Sports shape.
- Preservar `opening` para line movement sin snapshots.

## Implementación ejecutada
- ✅ Client `odds_for_fixture`.
- ✅ Normalizer `normalize_thestatsapi_odds_to_apisports_shape` + `_opening_odds`.
- ✅ Wiring en `_enrich_football` + `_odds_source`.
- ✅ Telemetría `[odds_coverage]`.

---

# Phase F74-post v2.5 — Opening Odds → Line Movement Wiring (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Activar line movement desde día 1:
- Leer `_opening_odds` del snapshot.
- Calcular `detect_line_movement(opening_odds, current_odds, market_side)`.
- Inyectar a `pick.key_data.line_movement` antes de moneyball.

## Implementación ejecutada
- ✅ `services/opening_odds_movement.py` (attach + batch enrich)
- ✅ Hook en `analyst_engine.py` antes de `apply_moneyball_layer`.

---

# Phase F82 — Rich H2H Context + 365Scores Corners Ingestion (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
1) Evitar H2H genérico: renderizar resultados concretos + métricas.
2) Ingestar córners vía 365Scores (scrape.do) y persistir al schema canónico.
3) Ajustar recomendador de córners con gates conservadores.

## Implementación ejecutada

### ✅ 1) Rich H2H builder
- **NEW** `services/football_h2h_context_builder.py`
  - `build_h2h_context(match_doc)` → payload con:
    - `matches[].result` (ej. “USA 1-0 Paraguay”)
    - `summary` (avg_goals, under_3_5_rate, btts_rate, over_2_5_rate, home_unbeaten_rate)
    - `editorial_text` compacto
    - `sample_quality` (STRONG/USABLE/LIMITED/NONE)
    - downgrade por amistosos / muy antiguos.

### ✅ 2) Integración en ingesta
- **MOD** `services/data_ingestion.py`
  - Añade `match_doc['h2h_context'] = build_h2h_context(match_doc)` con logs:
    - `[h2h_context] fixture=... sample=... avg_goals=... under35=... btts=...`
  - Ejecuta corners provider (ver abajo).

### ✅ 3) UI H2H (panel independiente)
- **NEW** `frontend/src/components/H2HContextPanel.jsx`
  - Lista: fecha + result + badge “amistoso”
  - Métricas: avg goles, Under 3.5 %, BTTS %, Over 2.5 %
- **MOD** `EditorialPredictionPanel.jsx`
  - Importa y renderiza `<H2HContextPanel context={editorial.h2h_context} />`.

### ✅ 4) 365Scores corners (Fase 1 + Fase 2)
- **NEW** `services/external_sources/score365_client.py`
  - fetch JSON vía **scrape.do**:
    - `fetch_game_stats`, `fetch_game_data`
  - `normalize_365scores_match_stats(raw)` extrae corners/shots/SOT/possession/cards con aliases ES/EN/PT.
  - ID resolvers:
    - Fase 1: `resolve_game_id_from_match_doc` (external_ids o URL)
    - Fase 2: `resolve_game_id_by_date_and_names` (lista games del día ±1 con alias map: USA↔United States, Bosnia & Herzegovina, etc.).

### ✅ 5) Corners provider cascade + persistencia
- **NEW** `services/football_corners_provider.py`
  - Cascade (en versión F82 original): API-Sports → 365Scores → TheStatsAPI → none
  - Persistencia:
    - `match_doc['football_data_enrichment']['corners']`
    - `match_doc['thestatsapi_snapshot']['corners']` (si existe)
    - `match_doc['corners_snapshot']`
  - Logs:
    - `[corners_provider] fixture=... source=... total=... home=... away=...`

### ✅ 6) Normalizer corners ingestion
- **MOD** `services/football_data_enrichment_normalizer.py`
  - `_extract_corners_block()` ahora acepta:
    - `corners_snapshot`, `match_corners`, `current_match_corners`
    - derivación desde `live_stats.home_stats/away_stats` (Corner Kicks / Córners / Tiros de esquina).

### ✅ 7) Recomendador de córners (conservador)
- **MOD** `services/corner_market_layer.py`
  - Gate: respeta `football_data_enrichment.corners.available`/`corners_snapshot.available`.
  - Abort si presión asimétrica fuerte y NO hay corners provider.
  - Mantiene compat con el modo pregame protegido existente.

## Testing
- ✅ Tests nuevos: `backend/tests/test_f82_h2h_and_corners.py`
- ✅ Suite global se mantuvo verde.

---

# Phase F82.1 — Non-blocking H2H/Corners Enrichment + Job Timeout Protection (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Problema
El job principal de picks de fútbol se quedaba en “LLM analyzing…” y terminaba en gateway timeout por llamadas inline a 365Scores/scrape.do.

## Fix aplicado (arquitectura en 2 tiers)
### FAST tier (inline, obligatorio, 0 HTTP)
- ✅ `enrich_match_corners_fast()`
  - Solo API-Sports/live_stats + corners en snapshot TheStatsAPI ya presente en `match_doc`.
  - No realiza HTTP.

### EXTERNAL tier (opt-in, con timeout)
- ✅ `enrich_match_corners_external()`
  - 365Scores vía scrape.do.
  - Envoltorio con `asyncio.wait_for`.

## Feature flags + timeouts (env)
- ✅ `ENABLE_INLINE_365SCORES_CORNERS=False` (default)
- ✅ `ENABLE_BACKGROUND_365SCORES_CORNERS=True`
- ✅ `FOOTBALL_CORNERS_FAST_TIMEOUT_MS=1200`
- ✅ `FOOTBALL_365SCORES_TIMEOUT_MS=3500`

## Cambios de integración
- ✅ `data_ingestion.py` ahora usa **solo** `enrich_match_corners_fast()`.

## Reason codes nuevos
- ✅ `SCORE365_SKIPPED_INLINE_FLAG_DISABLED`
- ✅ `SCORE365_FETCH_TIMEOUT`

## Testing
- ✅ Nuevos tests: `backend/tests/test_f82_1_and_f83.py` (parte F82.1)
- ✅ Suite global: **2223 passed**, 2 skipped, 0 regresiones.

---

# Phase F83 — Manual Market Identity + Manual Odds Injection (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Cuando el engine detecta una cuota pero el mercado es UNKNOWN/AMBIGUOUS, la UI puede:
- elegir market family + selección + línea
- inyectar cuota manual
- recalcular edge/veredicto de forma aislada (sin pisar la cuota original)

## Implementación ejecutada
### Backend
- ✅ **NEW** `services/manual_market_identity.py`
  - `MANUAL_MARKET_TYPES` (8 familias)
  - `MARKET_OPTIONS` (selecciones + líneas permitidas)
  - `validate_manual_payload()` (validación estricta)
  - `recalculate_with_manual_market()` (manual_edge, implied/model prob, fragilidad/confianza heredada si existe)
  - Preserva siempre `detected_odd` separado de `manual_odd`.

- ✅ **NEW** endpoints:
  - `GET /api/football/manual-market-options`
  - `POST /api/football/manual-market-reprice`

### Frontend
- ✅ **NEW** `frontend/src/components/ManualMarketIdentityPanel.jsx`
  - Cuota detectada + input de cuota manual
  - Selects para mercado/selección/línea
  - Botón “Recalcular con mercado manual”
  - Render de resultado (`manual_edge`, fragilidad, confianza, veredicto, warnings)
  - Acepta `candidateMarkets` (del resolver de identidad por odds) para sugerir opciones.

## Testing
- ✅ Nuevos tests: `backend/tests/test_f82_1_and_f83.py` (parte F83)
- ✅ Endpoints verificados por `curl` (200 OK payload válido, 422 inválido).

---

## 3) Pendientes y siguientes pasos (post-F83)

---

# Phase F82.1-adjust — Manual/Background Corners Enrichment Endpoints (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Problema
La Phase F82.1 desactivó por completo 365Scores de la ingesta inline para evitar gateway timeouts. Eso protegió al generador de picks, pero perdimos acceso a datos valiosos de córners de 365Scores. Necesitábamos restaurar acceso vía endpoints manuales/background sin reintroducir el riesgo de timeout.

## Decisiones confirmadas (con el usuario)
- **a)** Timeout duro de **8 s** para `run-now`. Si excede → `SCORE365_TIMEOUT`. ✅
- **b)** Cola **in-memory** (`asyncio.create_task` + dict de estado) — sin Redis/Celery por ahora. ✅
- **c)** Endpoint `GET /status/{match_id}` para polling. Flujo UI: `/run-now` (sincrónico) → si TIMEOUT → fallback `/background` + polling `/status/{match_id}`. ✅
- **d)** P1/P2 se abordan después de cerrar F82.1-adjust. 🔄 PENDIENTE
- **e)** Criterio de éxito: cero regresiones en pytest (2223→2230) + esbuild verde. ✅

## Implementación ejecutada
### Backend
- ✅ **MOD** `services/football_corners_provider.py`
  - Añadidos `RC_DEFERRED = 'CORNERS_EXTERNAL_ENRICHMENT_DEFERRED'` y `STATUS_PENDING_BG = 'PENDING_BACKGROUND_ENRICHMENT'`.
  - Cuando `allow_external=False` y `is_background_365scores_enabled()=True`, el payload se marca con `status: PENDING_BACKGROUND_ENRICHMENT` + `reason_codes: [..., CORNERS_EXTERNAL_ENRICHMENT_DEFERRED]`.

- ✅ **NEW** endpoints en `server.py`:
  - `POST /api/football/corners-enrichment/run-now`
    - Body: `{ match_id }`. Carga match_doc desde `analyst_runs`, llama `enrich_match_corners_external` con `asyncio.wait_for(timeout=8.0)`.
    - Persiste corners_snapshot en `analyst_runs.picks` o `summary.*` (best-effort).
    - Retorna `status: SUCCESS | UNAVAILABLE | TIMEOUT | MATCH_NOT_FOUND | ERROR`.
  - `POST /api/football/corners-enrichment/background`
    - Body: `{ match_id }`. Registra job en `_CORNERS_BG_JOBS` (in-memory dict), dispara `asyncio.create_task`, retorna `{ status: QUEUED }`.
    - Idempotente: re-encolar mientras hay job activo → `ALREADY_QUEUED`.
  - `GET /api/football/corners-enrichment/status/{match_id}`
    - Lee `_CORNERS_BG_JOBS` y devuelve `{ status, result, started_at, finished_at }` o `NOT_FOUND`.

- ✅ **MOD** `services/football_editorial_payload_adapter.py`, `possible_alternative_markets.py`, `football_structural_value_review.py`
  - Propagación de `corners_snapshot` desde `match_doc` al editorial output (mismo patrón que `h2h_context`).

### Frontend
- ✅ **NEW** `frontend/src/components/CornersRefreshPanel.jsx`
  - Renderiza cuando `corners_snapshot.status == 'PENDING_BACKGROUND_ENRICHMENT'` o `reason_codes` incluye `CORNERS_EXTERNAL_ENRICHMENT_DEFERRED`.
  - Botón **"Actualizar córners con 365Scores"** → POST `/run-now`.
  - 7 estados explícitos: `idle | loading | success | timeout | error | bg_queued | bg_polling`.
  - Fallback automático a `/background` + polling cada 3 s al endpoint `/status/{match_id}`.
  - Traducción humana de reason codes: `SCORE365_FETCH_TIMEOUT`, `SCORE365_ID_MISSING`, `SCORE365_BLOCKED_OR_EMPTY`, etc.
  - Todos los elementos interactivos con `data-testid`.

- ✅ **MOD** `frontend/src/components/EditorialPredictionPanel.jsx`
  - Renderiza `<CornersRefreshPanel matchId={matchId} cornersSnapshot={editorial.corners_snapshot} />` cuando aplique.

## Testing
- ✅ **NEW** `backend/tests/test_f82_1_adjust.py` — 7 tests (5 obligatorios + 2 extras):
  1. `test_main_generator_never_calls_365scores_inline` — fast tier no llama a 365Scores ni una vez.
  2. `test_data_ingestion_uses_fast_wrapper_only` — `data_ingestion._enrich_football` importa solo `enrich_match_corners_fast`.
  3. `test_corners_snapshot_marks_pending_when_empty` — fast tier vacío + background ON → status `PENDING_BACKGROUND_ENRICHMENT` + reason `CORNERS_EXTERNAL_ENRICHMENT_DEFERRED`.
  4. `test_run_now_endpoint_returns_score365_data_when_available` — endpoint `/run-now` devuelve SUCCESS con datos de 365Scores mockeados.
  5. `test_run_now_endpoint_returns_timeout_code_when_slow` — `/run-now` retorna `TIMEOUT` + `SCORE365_FETCH_TIMEOUT` y responde en <2 s aun cuando el proveedor cuelga.
  6. `test_background_endpoint_queues_and_status_returns_result` — `/background` → QUEUED, polling `/status` → SUCCESS tras completarse el job.
  7. `test_status_returns_not_found_for_unknown_match` — `/status` devuelve `NOT_FOUND` para match nunca encolado.
- ✅ **Suite global:** 2230 passed, 2 skipped, 0 regresiones (antes: 2223).
- ✅ **Lint:** Python (provider + tests) y JS (CornersRefreshPanel + EditorialPredictionPanel) limpios.
- ✅ **esbuild:** EditorialPredictionPanel.jsx compila sin errores.

## Endpoints verificados con curl en preview
- `POST /run-now` con match inexistente → `{"status":"MATCH_NOT_FOUND",...}` ✅
- `POST /background` → `{"status":"QUEUED",...}` inmediato ✅
- `GET /status/{match_id}` post-completion → resultado correcto con `started_at`/`finished_at` ✅

---

## 4) Pendientes y siguientes pasos (post-F82.1-adjust)

---

# Phase F83.1 — Pantalla-negra fix + Home/Visitante labels (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Problema
1. El botón "Recalcular con mercado manual" provocaba pantalla en negro cuando la respuesta del backend era un error 422 (FastAPI validation): el frontend hacía `setError(e.response.data.detail)` y `detail` era un ARRAY de objetos. React crasheaba con "Objects are not valid as a React child".
2. Las opciones del dropdown mostraban tokens opacos ("HOME"/"AWAY"/"1X") en vez de los nombres reales de los equipos.

## Implementación
- **MOD** `frontend/src/components/ManualMarketIdentityPanel.jsx`:
  - Añadido helper `normaliseError(raw)` que convierte arrays/objetos de Pydantic a strings legibles antes de guardarlos en state.
  - Defensiva extra: `<span>{typeof error === 'string' ? error : normaliseError(error)}</span>`.
  - Nuevas props `homeName` y `awayName` (opcionales).
  - Helper `renderableSelection(marketType, selection)` muestra labels dinámicas:
    - `MATCH_WINNER / DNB / HANDICAP / ASIAN_HANDICAP`: "México (Local)" / "Colombia (Visitante)" / "Empate".
    - `DOUBLE_CHANCE`: "México o Empate" / "Empate o Colombia" / "México o Colombia".
- **MOD** `frontend/src/components/FootballMarketAuditPanel.jsx`:
  - Extrae `homeName / awayName` del `item.home_team / item.away_team / cardHeader` y los propaga via `FootballMarketTraceDetail` → `ManualMarketIdentityPanel`.

## Verificación
- Reproducido el bug con `curl` (payload incompleto → 422 con `detail` array) — antes crashea React, ahora se renderea como string seguro.
- esbuild + lint frontend verdes.

---

# Phase P2 — `infer_original_pick_side` (4-source cascade) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Problema
`attempt_alternative_market_rescue` ya aceptaba `original_pick_side` pero el call-site en `analyst_engine.py` pasaba `None` siempre. Por lo tanto los rescates direccionales (Doble Op 1X / X2, AH, Run Line ±) nunca se aplicaban — pérdida sistemática de rescates legítimos cuando el lado original era inferible.

## Implementación
- **MOD** `services/alternative_rescue.py` — nueva función pública `infer_original_pick_side(match, entry)` con cascada de 4 fuentes:
  1. `entry.recommendation.selection` (tokens 1X2 / nombres de equipo / prefix spread).
  2. Forebet `predicted_winner` (home/away/draw) o `predicted_score` ("2-1" → home).
  3. Match Winner odds — favorito implícito, gap ≥10% requerido.
  4. TheStatsAPI directional edge (`_market_edge.side` o `home_edge - away_edge ≥ 2 pts`).
  - Retorna `None` cuando ninguna fuente da señal confiable → preserva el comportamiento conservador previo.
- **MOD** `services/analyst_engine.py` — call-site llama `infer_original_pick_side(m, entry)` y pasa el resultado a `attempt_alternative_market_rescue(original_pick_side=...)`.

## Tests
- ✅ **NEW** `backend/tests/test_p2_infer_pick_side.py` — **29 tests** organizados en 5 clases:
  - `TestSource1Recommendation` (6 tests).
  - `TestSource2Forebet` (7 tests).
  - `TestSource3OddsFavourite` (4 tests).
  - `TestSource4TheStatsAPI` (5 tests).
  - `TestCascadeOrdering` (6 tests — prioridad de fuentes + fallback NULL).
  - `TestRescueWiring` (1 test integración — `original_pick_side=None` preserva legacy skip).

---

# Phase F82.2 — Scores24 → 365Scores cross integrator + provider re-order (Backend COMPLETED ✅ / Frontend PENDING 🔄)

## Estado: ✅ BACKEND COMPLETO · 🔄 FRONTEND POSTPUESTO PARA BLOQUE C

## Cambios backend ejecutados
### B1 — Deprecar Scores24 (flag OFF default)
- **MOD** `services/football_corner_cross_integration.py`:
  - Nueva constante `ENABLE_SCORES24_CORNERS_CONFIRMATION = False` (default).
  - Lectura runtime vía env var `ENABLE_SCORES24_CORNERS_CONFIRMATION` (solo `true` la activa).
  - Short-circuit antes del gate: si flag OFF → añade `RC_SCORES24_DISABLED` + `RC_SCRAPER_SKIPPED`, persiste el cross interno y retorna sin tocar Scores24.
  - Tests legacy (`test_football_corner_cross_integration_smoke.py`) ahora activan el flag con `monkeypatch.setenv` para seguir validando el legacy path.

### B2 — Nuevo módulo `football_corner_365_cross_integration.py`
- Función pública `attach_365_corner_confirmation(match_doc, pick_payload=None) → dict`.
- Reglas de confirmación (per spec del usuario):
  - **UNDER**: confirma si `combined_avg_for ≤ 8.5` o `over_9_5_rate ≤ 0.40`; conflicto si `combined_avg_for ≥ 10.0` o `over_9_5_rate ≥ 0.58`.
  - **OVER**: confirma si `combined_avg_for ≥ 9.5` o `over_9_5_rate ≥ 0.55`; conflicto si `combined_avg_for ≤ 8.0` o `over_9_5_rate ≤ 0.38`.
- Reason codes: `365SCORES_CONFIRMS_UNDER/OVER_PROFILE`, `365SCORES_CONFLICTS_UNDER/OVER_PROFILE`, `365SCORES_NEUTRAL_VS_PROFILE`, `NO_CROSS_PROFILE_AVAILABLE`, `NO_365SCORES_CONFIRMATION_AVAILABLE`, `365SCORES_PENDING_BACKGROUND_ENRICHMENT`.
- Persiste `external_source/_confirmation/_conflict/_reason_codes/_snapshot` dentro del cross block, espeja a `footballHistoricalProfile.combinedFootballCornerProfileCross` (camelCase UI) y emite audit en `football_corner_365_cross_applied`.

### B3 — Provider re-order: TheStatsAPI → API-Sports → 365Scores
- **MOD** `services/football_corners_provider.py`:
  - Ahora **TheStatsAPI es el baseline rápido** (cubre más ligas que API-Sports).
  - API-Sports queda como respaldo cuando ya hay `live_stats` en el doc.
  - 365Scores se mantiene fuera del pipeline inline (sigue dentro de `/run-now` y `/background`).

### B4 — Persistencia del cross en analyst_runs
- **MOD** `server.py`:
  - `_persist_corners_snapshot_to_run` ahora acepta `cross_block` y `cross_audit` (kwargs opcionales) y los escribe junto con el `corners_snapshot` en `picks.$.` o `summary.<bucket>.$.`.
  - `_do_external_corners_fetch` (endpoint `/run-now` + worker background) ahora ejecuta `attach_365_corner_confirmation` después del fetch exitoso y devuelve `combined_football_corner_profile_cross` + `football_corner_365_cross_applied` en el response.

### B5 — Tests obligatorios (8/8 ✅)
- **NEW** `backend/tests/test_f82_2_corner_365_cross.py`:
  1. `test_thestatsapi_is_fast_corner_baseline` — TheStatsAPI gana sobre API-Sports.
  2. `test_scores24_not_called_for_corner_confirmation_by_default` — scraper NUNCA invocado con flag OFF.
  3. `test_365scores_confirms_under_corner_cross` — UNDER + métricas bajas → confirms=True.
  4. `test_365scores_conflicts_with_under_corner_cross` — UNDER + métricas altas → conflict=True.
  5. `test_365scores_confirms_over_corner_cross` — OVER + métricas altas → confirms=True.
  6. `test_corner_cross_persists_365_external_confirmation_in_analyst_runs` — persist incluye cross + audit en `$set`.
  7. `test_corner_enrichment_run_now_returns_cross_confirmation` — endpoint `/run-now` devuelve cross + audit en el response.
  8. `test_corner_enrichment_background_updates_cross_confirmation` — `/background` + polling `/status` devuelve el cross actualizado.

### Suite global
- ✅ **2267 passed**, 2 skipped, 0 regresiones (antes: 2230). **+37 nuevos tests** (29 P2 + 8 F82.2).
- ✅ Lint Python (0 blocking) y JS (CornersRefreshPanel, ManualMarketIdentityPanel, FootballMarketAuditPanel) limpios.
- ✅ esbuild de DashboardPage verde.

## Pendiente para Bloque C (sesión futura) — RESUELTO ✅

Ver sección **Phase F82.2-frontend** abajo.

---

# Phase F82.2-frontend — CornersEnrichmentButton wiring + Scores24 label removal (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Implementación
### Frontend
- ✅ **NEW** `frontend/src/components/CornersEnrichmentButton.jsx` (convertido del TSX adjunto del usuario a JSX puro, ~470 líneas):
  - Renderiza CTA "Cargar stats de córners" / "Refrescar" cuando hay snapshot.
  - Flow: click → POST `/football/corners-enrichment/run-now` → si `TIMEOUT` → POST `/background` + polling `/status/{match_id}` cada 3s (máx ~60s).
  - Estados: `idle | loading | polling | done | error`.
  - Render del **`CrossProfileBlock`** L5-vs-L15 con confirmación externa.
  - **Labels Scores24 → 365Scores** (per spec del usuario):
    - `extConfirms`: "365Scores confirma" / "365Scores confirms".
    - `extConflict`: "365Scores contradice" / "365Scores conflicts".
    - `gateBlocked`: "Confirmación externa no disponible" / "External confirmation unavailable".
  - Audit field lee `football_corner_365_cross_applied` (Phase F82.2) con fallback a `football_corner_cross_applied` (legacy).
  - Test-ids: `corners-enrichment-{matchId}`, `corners-enrichment-btn-{matchId}`, `corner-cross-external-confirms/conflicts/unavailable`, `corner-cross-profile`, `corners-summary`.
  - Defensiva: `try/catch` alrededor de `toast.error` y `toast.message` (sonner puede no estar montado en algunos contextos de test).
  - **No hooks tras early return** — el guard `sport !== 'football'` se hace DESPUÉS de los hooks para no romper el contrato de React.

- ✅ **MOD** `frontend/src/components/MatchCard.jsx`:
  - Import + render del botón dentro del bloque `sport === 'football'`, después de `FootballOverSupportPanel` y antes de `LiveRecommendationTimeline`.

- ✅ **MOD** `frontend/src/components/MatchIntelligencePanel.jsx`:
  - Import + render condicional `sport === 'football'` al final del panel detallado.
  - **Sin duplicado**: `MatchCard` aparece en el dashboard listado, `MatchIntelligencePanel` solo en `MatchDetailPage`. Páginas distintas → no se renderean simultáneamente.

## Tests (5/5 obligatorios + 7 bonus = 12/12 ✅)
- ✅ **NEW** `frontend/src/components/__tests__/CornersEnrichmentButton.test.jsx`:
  - **`renders CornersEnrichmentButton for football match with pending corners`** ✅
  - **`does not render CornersEnrichmentButton for non-football sports`** ✅
  - **`calls run-now endpoint when clicking the update button`** ✅
  - **`shows "365Scores confirma" when cross profile is confirmed`** ✅
  - **`does not show legacy "Scores24" labels for confirmed match`** ✅
  - Bonus: render gating sin match_id, fallback TIMEOUT → background, EN labels, 365Scores contradice, confirmación externa no disponible, sin Scores24 en conflicting match, sin "gate denied" wording legacy.

## Verificación
- ✅ Lint JS: `CornersEnrichmentButton.jsx`, `MatchCard.jsx`, `MatchIntelligencePanel.jsx` — todos limpios.
- ✅ esbuild: `DashboardPage.jsx` + `MatchDetailPage.jsx` compilan sin errores.
- ✅ Frontend test suite: **12/12 nuevos tests pasan**. 3 fallos preexistentes en `LiveReevalPanel.test.jsx` no relacionados con F82.2.
- ✅ Backend suite (sanity): 52/52 tests F82.x + P2 + smoke siguen verdes.
- ✅ Screenshot en preview: login page carga limpio, sin errores de runtime ni de bundle.

---

## 5) Pendientes y siguientes pasos (post-F82.2-frontend)

---

# Phase F83.1-fix — Manual Market Identity match_id isolation + Data Availability Sections (COMPLETED ✅)

## Estado: ✅ COMPLETADA (Bloque D)

## Problemas reportados (en producción)
1. **`body.match_id: Input should be a valid string`** — el frontend enviaba `null`/número y el modelo Pydantic strict `str` rechazaba el payload.
2. **Cuota repetida en todas las cards** — el panel manual mostraba la misma cuota (1.25) para todos los partidos porque el estado del input no se reseteaba entre cards.
3. **Contradicción "xG disponible vs faltante"** — `internal_analysis_debug.thestatsapi_found=True` se mostraba como "✓ TheStatsAPI / xG disponible" mientras `missing=["xG"]` se renderaba como "Faltantes: xG".

## Implementación

### Frontend
- ✅ **NEW** `frontend/src/lib/matchResolver.js`:
  - `resolveMatchId(match, pick, auditRow)` — cascada de 11 fuentes; siempre devuelve string no-blank o `null`. Filtra sentinelas `"undefined"`, `"null"`, `"NaN"`. Loggea diagnóstico cuando devuelve null.
  - `resolveDetectedOdd(match, pick, auditRow)` — 11 fuentes; valida rango `[1.01, 50]`; nunca permite que dos cards compartan accidentalmente la misma cuota.
- ✅ **MOD** `FootballMarketAuditPanel.jsx`:
  - Usa `resolveMatchId(item, item, item)` + `resolveDetectedOdd(item, item, item)` (card-scoped).
  - Pasa `key={mm-${matchId}-${detectedOdd}}` al `ManualMarketIdentityPanel` para forzar remount cuando cambia el contexto del card → reseta `manualOdd` automáticamente.
- ✅ **MOD** `ManualMarketIdentityPanel.jsx`:
  - Guarda contra `matchId === null` antes del submit con mensaje claro.
  - `safeMatchId = String(matchId).trim()` + filtro `"undefined"/"null"`.
  - `detected_odd` siempre enviado como número.

### Backend
- ✅ **MOD** `server.py` — `ManualMarketRepriceRequest`:
  - `match_id: Union[str, int]` con `@field_validator(mode="before")` que coerce a string, strip, y rechaza `null/blank/undefined/NaN` con `ValueError` claro.
  - `Union` y `field_validator` importados.
- ✅ **MOD** `/api/football/manual-market-reprice` endpoint:
  - Reemplazado `db.matches.find_one({"match_id": payload.match_id})` (colección que no existe) por lookup tolerante en `analyst_runs` con variantes: `match_id`, `fixture_id`, `id`; intenta ambos string e int cuando aplica. Busca en `picks` + 4 buckets de summary.
- ✅ **NEW** `services/football_data_availability.py`:
  - `has_xg_available(match)` — solo `True` cuando hay valores numéricos home+away en `football_data_enrichment.xg`, `thestatsapi_snapshot.xg`, `_thestatsapi_enrichment.xg`, o `live_stats.xg_home/xg_away`. **`football_data_enrichment` solo no es suficiente.**
  - `has_thestatsapi_available(match)` — heurística separada de xG.
  - `has_h2h_available(match)`, `has_corners_l5_l15_available(match)`, `has_market_identity_available(match)`, `has_recent_form_available(match)`.
  - `build_data_availability_sections(match) → {sections, available_sections, missing_sections, missing_codes}`:
    - Cuando TheStatsAPI presente pero xG no normalizado → `sections.xg.status = "MISSING_NORMALIZATION"` (nuevo estado explícito).
    - `missing_codes` incluye `XG_NOT_NORMALIZED` distinguible de `XG_MISSING`.
- ✅ **MOD** `services/football_editorial_payload_adapter.py`:
  - `internal_analysis_debug` ahora incluye `sections`, `available_sections`, `missing_sections`, `missing_codes`.
  - Top-level `data_availability` para consumidores que prefieren no abrir el debug.

### Frontend (render parcial)
- ✅ **MOD** `EditorialPredictionPanel.jsx`:
  - Lee `debugBlock.sections.xg.status` (no más `thestatsapi_found` para xG).
  - 3 estados de xG con render distinto:
    - `AVAILABLE` → "✓ TheStatsAPI / xG disponible".
    - `MISSING_NORMALIZATION` → "✓ TheStatsAPI disponible ⚠ xG no normalizado para este partido" (color ámbar).
    - `MISSING` → "✗ TheStatsAPI / xG no disponible".
  - `Faltantes:` lee de `missing_sections` (consistente con `sections`) en lugar del legacy `missing[]`.
  - Test-ids específicos: `*-row-xg-available`, `*-row-xg-not-normalized`, `*-row-xg-missing`.

## Tests
- ✅ **NEW** `backend/tests/test_f83_1_data_availability.py` — **16 tests**:
  - `TestHasXgAvailable` (6 tests): true cases de FDE/TSA/live_stats, false cuando FDE existe pero sin xG, false con missing side.
  - `TestBuildDataAvailabilitySections` (4 tests): contradicción xG normalization, full availability, market_identity UNKNOWN, h2h_recent list.
  - `TestEditorialAdapterIntegration` (2 tests): sections map se propaga al debug block; top-level data_availability presente.
  - `TestManualMarketRepriceMatchIdCoercion` (4 tests): numeric→string coerción + endpoint OK; null/blank/"undefined" raise ValidationError.
- ✅ **NEW** `frontend/src/lib/__tests__/matchResolver.test.js` — **16 tests**:
  - 9 tests `resolveMatchId`: prioridad, coerción numérica, fallback fixture/id/game_id/fixture.id/live_stats.game_pk/pick/auditRow, sentinelas, nunca throw.
  - 7 tests `resolveDetectedOdd`: orden de cascada, manual_market_identity, rejects out-of-range / non-finite, **regression test: no leak entre cards**.

## Suite global
- ✅ **Backend: 2283 passed**, 2 skipped, 0 regresiones (antes 2267 → **+16 nuevos F83.1**).
- ✅ **Frontend: 97/100 tests verdes** (16 nuevos F83.1 + 81 anteriores). 3 fallos preexistentes en `LiveReevalPanel.test.jsx` no relacionados.
- ✅ Lint Python + JS limpios; esbuild verde para `DashboardPage`.
- ✅ Endpoint validado con `curl`:
  - `match_id: 12345` (numérico) → 200 OK, coerción correcta.
  - `match_id: null` → 422 con `"match_id is required"` (UI ahora muestra string legible).
- ✅ Screenshot en preview: app carga sin errores.

## Bloque E pendiente
**xG L1/L5/L15 desde TheStatsAPI shotmap** + señales analíticas (`LOW_RECENT_XG_PROFILE`, `DEFENSIVE_XG_SUPPRESSION`, `XG_FORM_SHIFT`, `XG_APOYA_UNDER/OVER`). Arquitectura background-first con cache + timeout. NO inline para no bloquear el generador.

---

## 6) Pendientes y siguientes pasos (post-F83.1-fix)

### Pendientes no bloqueantes (actualizadas)
- (P1) **Alternative rescue**: implementar `alternative_rescue.infer_original_pick_side()`
  - Inferencia: `recommendation.selection` → Forebet → favorito por cuota → edge TheStatsAPI.
  - Bloquear rescates direccionales si la dirección original no es inferible.

- (P2) Wiring UI del bucket `REQUIRES_MANUAL_MARKET_SELECTION`
  - Integrar `ManualMarketIdentityPanel` dentro del panel/bucket
    `requires_market_identity` cuando `state==REQUIRES_MANUAL_MARKET_SELECTION`.
  - Permitir input manual de cuota y disparar `POST /api/football/manual-market-reprice`.

- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (P4) Validar resolver TheStatsAPI por nombres con datos reales y mejorar filtros por `competition` donde aplique.
- (P5) Extender corner engine live/watchlist:
  - permitir watchlist solo si hay presión ofensiva alta de ambos equipos + total actual bajo vs ritmo.

### Validación esperada (post-F83)
- UI/JSON:
  - `editorial.h2h_context.matches[]` con resultados concretos (no solo conteo).
  - `football_data_enrichment.corners.available` con fuente y reason_codes claros.
  - `requires_market_identity` bucket con `candidate_markets` y panel manual (F83) disponible.
- Logs:
  - `[h2h_context] ...`
  - `[corners_provider] ...` (fast tier inline) y `SCORE365_*` solo en external tier.
  - `[odds_coverage] ...`
