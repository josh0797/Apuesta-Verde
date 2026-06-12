# Plan — Phases F58–F74 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** (implementación + tests).  
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
**(COMPLETADO)** — ver historial en este mismo archivo (sin cambios).

---

## Phase F70 — Reemplazo externo (Sportytrader / Forebet) (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo (sin cambios).

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

### ✅ F74-1 — Recalibración PROTECTED: mover Over 1.5 a PROTECTED
- Archivo: `backend/services/market_tolerance.py`
- Cambio: `Over 1.5 / Más de 1.5` reclasificado como **PROTECTED**.

### ✅ F74-2 — Floors granulares: `get_protected_floor(...)`
- Archivo: `backend/services/market_tolerance.py`
- Función nueva: `get_protected_floor(market, selection, *, market_identity=None)`
- Aplica floors granulares por familia/side/line, usando `market_identity` si está disponible.

### ✅ F74-3 — Floors completos: `resolve_edge_floors(...)`
- Archivo: `backend/services/market_tolerance.py`
- Función nueva: `resolve_edge_floors(category, *, market=None, selection=None, market_identity=None)`
- Devuelve `negative_edge_floor` y `watchlist_floor` efectivos.

### ✅ F74-4 — Integración en `moneyball_layer.py` (rama PROTECTED) + guard UNKNOWN
- Archivo: `backend/services/moneyball_layer.py`
- Cambios:
  - `classify_pick(...)` acepta `market`, `selection`, `market_identity`.
  - Si `market_identity` es UNKNOWN/inválida → `classification=MARKET_IDENTITY_MISSING`, `state=REQUIRES_MARKET_IDENTIFICATION`.
  - Rama PROTECTED usa `market_tolerance.resolve_edge_floors(...)`.

### ✅ F74-5 — Schema canónico unificado: `football_data_enrichment.py`
- Archivo nuevo: `backend/services/football_data_enrichment.py`
- Normalizador canónico + helper `attach_estimated_probability(...)` con gates (THIN/UNKNOWN/requires MI).

### ✅ F74-6 — Enriquecimiento TheStatsAPI: `thestatsapi_football_enrichment.py`
- Archivo nuevo: `backend/services/external_sources/thestatsapi_football_enrichment.py`
- Dixon-Coles/Poisson/Logística observe-only → `estimated_probabilities`.

### ✅ F74-7 — Tests (pytest) + suite verde
- Archivo nuevo: `backend/tests/test_f74_unified_football_enrichment.py` (30 tests)
- Suite global (en ese punto): ✅ **2106 passed**

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Objetivo (post-F74)
Reducir falsos positivos de:
- `REQUIRES_MARKET_IDENTIFICATION`
- “Análisis interno no disponible / THIN”

…cuando los datos sí existen pero están anidados/fragmentados.

Se implementa un orden de pipeline práctico sin migrar todo a TheStatsAPI:
- API-Sports sigue como base de fixtures/odds
- TheStatsAPI enriquece stats y alimenta normalización/odds

## Implementación ejecutada — 9 cambios

### ✅ Cambio 1 + 6 — Adaptador para editorial interno (nuevo)
- Archivo nuevo: `backend/services/football_editorial_payload_adapter.py`
- Función: `build_editorial_ready_match_payload(match) -> dict`
- Lee y aplana:
  - `home_team.context.recent_fixtures`, `away_team.context.recent_fixtures`
  - `home_team.context.seasonal_form`, `away_team.context.seasonal_form`
  - `live_stats`, `_thestatsapi_enrichment`, `thestatsapi_snapshot`, `football_data_enrichment`
  - `h2h_recent`, `odds_snapshots`
- Produce campos planos tipo:
  - `home_xg`, `away_xg`
  - `home_goals_scored_l5`, `away_goals_scored_l5`, `home_goals_allowed_l5`, etc.
  - `home_btts_rate_l15`, `home_clean_sheet_rate_l15`, etc.
- Eleva `data_quality` cuando hay señales de forma reciente (spec del usuario).
- Incluye `internal_analysis_debug` para UI (missing list + flags + reason_codes).

### ✅ Cambio 2 — Normalizador único TheStatsAPI (adapter/compat)
- Archivo nuevo: `backend/services/football_data_enrichment_normalizer.py`
- Función: `normalize_football_data_enrichment(match) -> dict`
- Regla confirmada: **F74 `football_data_enrichment.py` es el schema canónico interno**.
- Este normalizer:
  - lee `_thestatsapi_enrichment` / `thestatsapi_snapshot` / `football_data_enrichment` / `live_stats`
  - produce un payload canónico **F74 extendido** (`team_stats`, `corners`, `official_friendly_split`)
  - persiste **el mismo objeto** en:
    - `match["football_data_enrichment"]`
    - `match["thestatsapi_snapshot"]`
  - evita volver a fragmentar el schema entre capas legacy.

### ✅ Cambio 3 — Usar el adaptador antes del análisis editorial
- Archivos:
  - `backend/services/football_structural_value_review.py`
  - `backend/services/possible_alternative_markets.py`
- Cambios:
  - antes de `generate_football_editorial_prediction(...)`:
    1) `normalize_football_data_enrichment(match)`
    2) `build_editorial_ready_match_payload(match)`
  - Se propaga `internal_analysis_debug` al bloque editorial para la UI.

### ✅ Cambio 4 — Resolver market identity desde odds reales
- Archivo nuevo: `backend/services/football_market_identity_resolver.py`
- Función: `resolve_market_identity_for_discarded_entry(match, discarded_entry) -> dict`
- Resuelve desde:
  1) `evaluated_market` / `market_trace`
  2) `recommendation.market + recommendation.selection`
  3) `protected_alternative`
  4) **odds_snapshots** por cuota detectada (tolerancia ±0.01)
- Si hay una sola coincidencia → `state=RESOLVED` + `market_identity_key`.
- Si hay varias → `state=REQUIRES_MANUAL_MARKET_SELECTION` + `candidate_markets`.

### ✅ Cambio 5 — Normalizar alias de mercados en alternative_rescue
- Archivo: `backend/services/alternative_rescue.py`
- Añadido:
  - `OVER_UNDER_ALIASES`, `DOUBLE_CHANCE_ALIASES`
  - `DOUBLE_CHANCE_SELECTION_ALIASES`
  - helper `get_market_rows_by_alias(markets, aliases)` (case/accent-insensitive)
  - `_find_line_odds` ahora soporta:
    - `lines` como dict **y** lista (`[{value, odd}]`)
    - equivalencias EN↔ES (Over↔Más de, Under↔Menos de)

### ✅ Cambio 7 — Mejorar mensaje UI + debug collapsible
- Archivo: `frontend/src/components/EditorialPredictionPanel.jsx`
- Se añade:
  - collapsible **“Ver detalle del análisis”** (siempre disponible)
  - mensajes específicos cuando `internal_analysis_debug` está presente.

### ✅ Cambio 8 — TheStatsAPI mapping cuando no hay raw_id
- Archivo: `backend/services/thestatsapi_client.py`
- Función nueva:
  - `resolve_thestatsapi_match_id_by_names(home_team, away_team, date, competition=None, db=None)`
- Estrategia:
  - lista matches por fecha: `/api/football/matches?date_from=...&date_to=...`
  - matchea por nombres normalizados
  - cachea resultado (positivo o negativo) en `thestatsapi_match_mapping_cache`
  - fail-soft con log `THESTATSAPI_MATCH_MAPPING_NOT_FOUND`

### ✅ Cambio 9 — Orden correcto del pipeline (aplicado en moneyball bucketing)
- Archivo: `backend/services/moneyball_layer.py`
- `apply_moneyball_layer` ahora:
  - antes de rutear a `requires_market_identity`, intenta resolver identity con el resolver.
  - si `RESOLVED` re-evalúa el pick.
  - si `AMBIGUOUS` agrega `candidate_markets` para selección manual.

## Tests (F74-post)
- Archivo nuevo: `backend/tests/test_f74_post_fixes.py` (19 tests)
- Estado suite global:
  - ✅ **2125 passed**, 5 warnings, 0 regresiones.

---

## 3) Pendientes y siguientes pasos (post-F74)

### Pendientes no bloqueantes (actualizadas)
- (P1) **Alternative rescue**: implementar `alternative_rescue.infer_original_pick_side()`
  - Inferencia: `recommendation.selection` → Forebet → favorito por cuota → edge TheStatsAPI
  - Bloquear rescates direccionales si la dirección original no es inferible.
- (P2) Bucket UI + acción manual:
  - Exponer un bucket separado para `REQUIRES_MANUAL_MARKET_SELECTION` con input manual de cuota/mercado.
  - (Backend ya entrega `candidate_markets`, falta wiring UI si el panel no lo consume aún.)
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (P4) Validar resolver TheStatsAPI por nombres con datos reales + ajustar filtros por competition si es necesario.
- (P5) Continuar migración gradual de lógica estructural de API-Sports a TheStatsAPI **solo** tras estabilidad.

### Validación del usuario (post-F74-post)
- Verificar en UI/JSON:
  - Cuando hay `recent_fixtures` anidados, el editorial deja de caer en THIN y aparece `internal_analysis_debug`.
  - Unknown markets:
    - se intenta resolver por odds snapshot
    - si AMBIGUOUS, aparece bucket/manual con `candidate_markets`
    - si UNKNOWN real, cae a `requires_market_identity` sin edge/trap.
  - Alias ES/EN en `alternative_rescue` detectan Over/Under y DC aunque el proveedor no use los nombres exactos.
