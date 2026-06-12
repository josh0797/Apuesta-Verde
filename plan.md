# Plan — Phases F58–F74 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA**.  
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
- F74-post v2.5: **line movement desde día 1** usando `opening` TheStatsAPI + `last_seen` (sin necesidad de snapshots históricos).

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
- ✅ `football_data_enrichment.py`:
  - acepta datos desde `_thestatsapi_enrichment`, `thestatsapi_snapshot`, `external_context.thestatsapi`, API-Sports (si trae stats útiles), y Forebet (contexto externo);
  - devuelve **un schema único**;
  - incluye `estimated_probabilities` por `market_identity_key` **solo si `data_quality != THIN`**.
- ✅ Probabilidades:
  - **Dixon-Coles** si hay datos suficientes (xG por equipo).
  - **Poisson simple** fallback.
  - Heurística logística: **observe-only**.

## Implementación ejecutada (F74-1 … F74-7)
- ✅ F74-1 … F74-7 (ver historial en este mismo archivo).

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Reducir falsos positivos de:
- `REQUIRES_MARKET_IDENTIFICATION`
- “Análisis interno no disponible / THIN”

…cuando los datos sí existen pero están anidados/fragmentados.

## Implementación ejecutada — 9 cambios
- ✅ Cambio 1–9 (adapter editorial + normalizer compat + resolver por odds + aliases rescue + UI debug collapsible + match-id por nombres (client legacy) + pipeline bucketing).

## Testing
- ✅ `backend/tests/test_f74_post_fixes.py` (19 tests)
- ✅ Suite global (en ese punto): **2125 passed**

---

# Phase F74-post v2 — TheStatsAPI Odds Fallback Wiring (5 cambios) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Cablear TheStatsAPI como **fallback de odds** en la ingesta de fútbol:
- API-Sports sigue siendo **primario**.
- Cuando API-Sports devuelve odds vacías/inútiles, TheStatsAPI rescata odds.
- Preservar `opening` + `last_seen` por selección para habilitar line movement sin snapshots históricos.

## Implementación ejecutada — 5 cambios

### ✅ Cambio 1 — `services/external_sources/thestatsapi_client.py`
- Agregado: `odds_for_fixture(client, thestatsapi_match_id)`
  - Endpoint: `GET /football/matches/{match_id}/odds` (Bearer)
  - Fail-soft, usa `_request` (rate-limit + retries).
- Agregado: `resolve_thestatsapi_match_id_by_names(client, *, home, away, date, competition=None)`
  - Lista `GET /football/matches?date_from=DAY&date_to=DAY`
  - Matching por nombres normalizados (acentos-insensitive)
  - Fail-soft.

### ✅ Cambio 2 — `services/external_sources/thestatsapi_normalizer.py`
- Agregado: `normalize_thestatsapi_odds_to_apisports_shape(data)`
  - Traduce payload nested (bookmaker→markets→selections{opening,last_seen}) a shape API-Sports.
  - Usa `last_seen` como cuota actual.
  - Preserva opening en `_opening_odds` (key: `bookmaker|market|value`).
  - Cubre: `match_odds`, `btts`, `total_goals`, `match_corners`, `asian_handicap`.
- Helpers añadidos:
  - `_ts_extract_last_opening`
  - `_ts_line_key_to_label` (`over_2_5` → `2.5`).

### ✅ Cambio 3 — `services/data_ingestion.py` (`_enrich_football`)
- Bloque odds reescrito:
  - API-Sports primario → `nz.normalize_odds(odds_resp)`.
  - Si no hay odds útiles → fallback a TheStatsAPI:
    - si no hay `_thestatsapi_raw_id`, resolver match-id por nombres+fecha+liga.
    - `odds_for_fixture` → normalización a shape API-Sports → `nz.normalize_odds(...)`.
  - Propaga `_opening_odds` al `norm_odds`.
  - Stamp `_odds_source`: `api_sports | thestatsapi_fallback | api_sports_empty | no_odds`.
- Fix: eliminada línea duplicada `norm_odds = nz.normalize_odds(odds_resp)` que sobrescribía el fallback.

### ✅ Cambio 4 — Provenance en `match_doc`
- `match_doc["_odds_source"]` + `match_doc["odds_source"]` (alias para UI)
- Si odds provienen de TheStatsAPI fallback:
  - `external_sources_covered` incluye `"thestatsapi"`.

### ✅ Cambio 5 — Telemetría granular en `ingest_upcoming`
- Al final del ciclo de fútbol se loguea:
  - `[odds_coverage] {api_sports, thestatsapi_fallback, api_sports_empty, no_odds, total}`

## Testing
- ✅ 22 tests nuevos: `backend/tests/test_f74_post_thestatsapi_odds_fallback.py`
- ✅ Suite global (en ese punto): **2145 passed**, **2 skipped**, 5 warnings, 0 regresiones.

---

# Phase F74-post v2.5 — Opening Odds → Line Movement Wiring (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo
Activar detección de **line movement desde el día 1** usando el `opening` por selección que TheStatsAPI trae (preservado en `_opening_odds`) sin necesidad de snapshots históricos.

## Implementación ejecutada

### ✅ Nuevo — `services/opening_odds_movement.py`
- `attach_line_movement_from_opening_odds(pick, match_doc)`:
  - lee `match_doc["odds_snapshots"][0]["_opening_odds"]`;
  - resuelve market+selection canónicos con aliases ES/EN (ej.: `Goles totales`→`Goals Over/Under`, `Local`→`Home`, `Sí`→`Yes`, `Más de 2.5`→`Over 2.5`);
  - recupera `opening` por key `bookmaker|market|value` y `current` desde `bookmakers[].bets[].values[]`;
  - llama `detect_line_movement(opening_odds=opening, current_odds=current, market_side=hint)`;
  - muta el pick añadiendo:
    - `pick["_line_movement"]` (payload completo)
    - `pick["key_data"]["line_movement"]` (forma legacy ya consumida por moneyball).
  - fail-soft (inputs malformados / faltantes → no-op).
- `enrich_picks_with_opening_movement(parsed, matches_payload)`:
  - itera `parsed["picks"]` y cruza con `matches_payload` por `match_id`.

### ✅ Edit — `services/analyst_engine.py`
- Justo antes de `_mb.apply_moneyball_layer(...)`:
  - invoca `enrich_picks_with_opening_movement(parsed, matches_payload)`;
  - esto garantiza que moneyball lea el `line_movement_favourable` desde el primer pase.

## Testing
- ✅ 44 tests nuevos: `backend/tests/test_f74_post_opening_odds_movement.py`
- ✅ Suite global: **2189 passed**, **2 skipped**, 0 regresiones (subió de 2145 → 2189).

## Verificación manual
- Match Winner Home → opening 2.10 / current 2.05 → `odds_movement = -0.05`.
- Goles totales Más de 2.5 → opening 1.85 / current 1.87 → `odds_movement = +0.02`.
- Normalización ES/EN confirmada.

---

## 3) Pendientes y siguientes pasos (post-F74)

### Pendientes no bloqueantes (actualizadas)
- (P1) **Alternative rescue**: implementar `alternative_rescue.infer_original_pick_side()`
  - Inferencia: `recommendation.selection` → Forebet → favorito por cuota → edge TheStatsAPI.
  - Bloquear rescates direccionales si la dirección original no es inferible.
- (P2) Bucket UI + acción manual:
  - Exponer bucket para `REQUIRES_MANUAL_MARKET_SELECTION` con `candidate_markets` + input manual de cuota.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (P4) Validar resolver TheStatsAPI por nombres con datos reales y mejorar filtros por `competition` donde aplique.

### Validación esperada (post-F74-post v2.5)
- UI/JSON:
  - `odds_source` visible y consistente (`api_sports` vs `thestatsapi_fallback`).
  - Si API-Sports devuelve odds vacías, TheStatsAPI rescata y `odds_snapshots[0].available == True`.
  - `_opening_odds` poblado cuando venga de TheStatsAPI.
  - `key_data.line_movement` poblado en picks cuando hay opening+current.
  - `[odds_coverage]` en logs detecta regresiones.
  - `internal_analysis_debug` disponible (collapsible) y evita el mensaje genérico sin diagnóstico.
