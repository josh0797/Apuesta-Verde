# Plan — Phases F58–F83 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA**.  
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
