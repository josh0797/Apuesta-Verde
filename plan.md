# Plan — Phases F58–F74 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** (implementación + tests).  
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
  - Para PROTECTED: `watchlist_floor` se ajusta a “un escalón” por debajo del floor granular, conservando el gap del default protegido.

### ✅ F74-4 — Integración en `moneyball_layer.py` (rama PROTECTED) + guard UNKNOWN
- Archivo: `backend/services/moneyball_layer.py`
- Cambios:
  - `classify_pick(...)` ahora acepta `market`, `selection`, `market_identity`.
  - Si `market_identity` es UNKNOWN/inválida → retorna:
    - `classification = MARKET_IDENTITY_MISSING`
    - `state = REQUIRES_MARKET_IDENTIFICATION`
    - `reason_codes` con `EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET`
  - Rama PROTECTED usa `market_tolerance.resolve_edge_floors(...)` (floors granulares).

### ✅ F74-5 — Schema canónico unificado: `football_data_enrichment.py`
- Archivo nuevo: `backend/services/football_data_enrichment.py`
- Entregables:
  - `normalize_football_enrichment(match_doc, *, market_identity=None, extra_sources=None)`
    - Unifica fuentes: `_thestatsapi_enrichment`, `thestatsapi_snapshot`, `external_context.thestatsapi`, API-Sports (best-effort), Forebet.
    - Produce: `data_quality`, `reason_codes`, `providers_used`, `teams`, `xg`, `external_context.forebet`, `requires_market_identity`.
  - `attach_estimated_probability(canonical, identity_key, ...)`
    - Gate: bloquea si `data_quality == THIN` o `identity_key` UNKNOWN o `requires_market_identity=True`.

### ✅ F74-6 — Enriquecimiento TheStatsAPI: `thestatsapi_football_enrichment.py`
- Archivo nuevo: `backend/services/external_sources/thestatsapi_football_enrichment.py`
- Entregables:
  - `enrich_football_match_with_thestatsapi(match_doc, *, canonical=None, market_identity=None, prefer_dixon_coles=True)`
  - Cálculo de probabilidades por `market_identity_key` desde grid:
    - Tier 1: Dixon-Coles (xG home/away)
    - Tier 2: Poisson (DC deshabilitado)
    - Tier 3: logística observe-only (solo Forebet 1X2) con `quality="OBSERVE_ONLY"`

### ✅ F74-7 — Tests (pytest) + suite verde
- Archivo nuevo: `backend/tests/test_f74_unified_football_enrichment.py` (30 tests)
- Estado suite global:
  - ✅ **2106 passed** (sin regresiones)

---

## 3) Pendientes y siguientes pasos (post-F74 parcial)

### Pendientes no bloqueantes
- (P1) **Alternative rescue**: inferir dirección original en `alternative_rescue.py` (evitar rescates direccionales si es desconocido).
- (P2) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (P3) Migrar más lógica estructural de API-Sports a TheStatsAPI tras validar estabilidad.

### Validación del usuario (post-F74 parcial)
- Verificar en UI/JSON:
  - Mercados con `market_identity_key` UNKNOWN aparecen como `REQUIRES_MARKET_IDENTIFICATION` (sin edge/trap).
  - Over 1.5 se trate como PROTECTED.
  - `estimated_probabilities` solo se llena cuando `data_quality` no es THIN.
