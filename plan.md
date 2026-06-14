# Plan — Phases F58–F91 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA** + ✅ **P4.1 (LiveReevalPanel.test.jsx 3 preexistentes) COMPLETADA** + ✅ **F84.a (team_stats prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.b (H2H prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.e (odds prioridad-inversa a TheStatsAPI + line movement) COMPLETADA** + ✅ **F85 (Public xG — FBref + Forebet vía scrape.do) COMPLETADA** + ✅ **F85 Phase 2 (FBref search-page resolver + fuzzy matching) COMPLETADA** + ✅ **F86 (H2H Decision Policy puro en Python) COMPLETADA** + ✅ **F87 (Cableado quirúrgico en `_enrich_football`: H2H decision + xG-recent background dispatch) COMPLETADA** + ✅ **F88 (F86.2 — Editorial Consumer: h2h_decision + xg_recent_averages + UI) COMPLETADA** + ✅ **F89 (Sprint F86.1 — Calibración H2H rules + explicit polarity/sample/cap guards) COMPLETADA** + ✅ **F90 (Sprint F83-update — Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order) COMPLETADA** + ✅ **F91 (MLB Quality Contact Matchup Engine — módulo puro + tests) COMPLETADA**.
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

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre:
  - calidad real del contacto ofensivo (xwOBA, sweet-spot%, barrel%, hard-hit%)
  - vulnerabilidad del abridor (xERA, xwOBA allowed, barrel% allowed, hard-hit% allowed)
  - percepción pública basada en ERA
- **No generar picks automáticos**: solo output explicable con señales.
- Entregar un **módulo puro** con:
  - lineup_contact_quality (ponderado por orden al bate)
  - pitcher_vulnerability (0–100)
  - matchup_contact_factor y contact_mismatch_score
  - detector de regresión (xERA − ERA)
  - señales: `MATCHUP_CONTACT_ADVANTAGE`, `PITCHER_BARREL_REGRESSION_RISK`, `ERA_UNDERSTATES_DAMAGE`, `TOP_ORDER_THREAT`, `OVER_CONTACT_WARNING`
- Datos por bateador:
  - por defecto **derivados** desde team-level
  - flag `QCM_LINEUP_PER_BATTER=true` para consumir per-batter real en el futuro
- Thresholds:
  - defaults hardcoded + override por env `QCM_THRESHOLDS` JSON
- Scope acordado:
  - **solo módulo + tests + payload fail-soft**
  - NO crear `mlb_under_discovery.py` ni `pick_ranking.py`
  - NO modificar `picks[]` ni ranking aún

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
**(COMPLETADO)** — sin cambios.

---

# Phase F90 — Sprint F83-update: Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order (COMPLETED ✅)
**(COMPLETADO)** — ver historial F90 en este mismo archivo.

---

# Phase F91 — MLB Quality Contact Matchup Engine (módulo puro) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Decisión de scope (acordada)
- ✅ Solo módulo puro + tests + output fail-soft.
- ✅ NO crear `mlb_under_discovery.py` ni `pick_ranking.py` (no existen en este repo).
- ✅ NO modificar `picks[]` ni ranking.
- ✅ Métricas por bateador derivadas por defecto; flag `QCM_LINEUP_PER_BATTER=true` para real per-batter en el futuro.
- ✅ Thresholds hardcoded + override por env `QCM_THRESHOLDS` (JSON).

## Implementación ejecutada

### Backend
1) **NEW** `backend/services/mlb_quality_contact_matchup.py`
- Implementa el engine completo como módulo puro:
  - **Weighted Lineup Quality Score** con `LINEUP_WEIGHTS`.
  - **Offensive Contact Quality Score** (xwOBA/sweet-spot/barrel/hard-hit) con escalado 0–100.
  - **Pitcher Vulnerability Score** con escalado 0–100.
  - **ERA Regression Detector** (`era_gap = xERA − ERA`) con niveles:
    - `SEVERE_REGRESSION_RISK` (≥ 1.50)
    - `HIGH_REGRESSION_RISK` (≥ 1.00)
    - `MODERATE_REGRESSION_RISK` (≥ 0.50)
    - `NORMAL`
  - **Matchup Contact Factor**: `lineup_quality * (1 + barrel_pct_allowed)`.
  - **Contact Mismatch Score**: `(lineup_quality * pitcher_vulnerability)/100` (0–100).
  - **Signals**:
    - `MATCHUP_CONTACT_ADVANTAGE`
    - `PITCHER_BARREL_REGRESSION_RISK`
    - `ERA_UNDERSTATES_DAMAGE`
    - `TOP_ORDER_THREAT`
    - `OVER_CONTACT_WARNING`
- **Thresholds**:
  - defaults hardcoded en `_DEFAULT_THRESHOLDS`
  - override por env `QCM_THRESHOLDS` (JSON) leído **en tiempo de llamada** (`get_active_thresholds`).
- **Fuentes de datos (mock/derivado, acordado)**:
  - Reutiliza `mlb_advanced_stats_helpers.extract_mlb_advanced_context()` + `_team_block/_pitcher_block`.
  - Default: deriva 9 filas de bateadores desde snapshot de equipo con jitter posicional.
  - Futuro: con `QCM_LINEUP_PER_BATTER=true` consume `payload.lineups.official.<side>[].statcast`.
- **Fail-soft**:
  - si faltan datos → `available=false`, `reason_codes=["QCM_INSUFFICIENT_DATA"]`, `signals=[]`.
- **Output**:
  - `compute_quality_contact_matchup(payload)` devuelve el bloque `quality_contact_matchup` con:
    - `available`, `lineup_contact_quality`, `pitcher_vulnerability`, `matchup_contact_factor`, `contact_mismatch_score`, `era_gap`, `regression_risk`, `signals`
    - `reason_codes` (provenance: REAL vs DERIVED)
    - `thresholds_used` (auditoría)
    - `score_breakdown` (per-batter weighted + pitcher_metrics)

### Tests
2) **NEW** `backend/tests/test_mlb_quality_contact_matchup.py`
- **36 tests** que cubren:
  - invariantes de pesos `LINEUP_WEIGHTS`
  - scoring primitives (batter score, lineup quality, pitcher vulnerability)
  - clasificación de gap xERA−ERA
  - override de thresholds por env
  - flag `QCM_LINEUP_PER_BATTER`
  - señales (incluye TOP_ORDER_THREAT y OVER_CONTACT_WARNING con override)
  - audit `score_breakdown`
  - garantía de no tocar `picks[]`

## Validación
- ✅ Ruff lint clean.
- ✅ Tests F91: **36/36 PASS**.
- ✅ Suite completa backend: **2671 passed, 2 skipped, 0 failed** (2635 → 2671).
- ✅ Cero regresiones.

---

## 3) Pendientes y siguientes pasos (post-F91)

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Próximos sprints recomendados para MLB (post-F91)
- **F91.1 — Emisión al pipeline MLB**: integrar `quality_contact_matchup` en el payload contract (p.ej. `mlb_pipeline_payload_contract.py` o punto único equivalente del pipeline). *(En F91 solo se entregó el módulo puro + tests).*
- **F91.2 — Señales impactan confianza (sin picks automáticos)**:
  - Under: penalización `UNDER_CONTACT_RISK` en el layer de fragilidad (p.ej. `mlb_under_fragility_calibrator.py`).
  - Over: boost moderado `CONTACT_EXPLOSION_POTENTIAL` en `mlb_over_discovery.py` cuando coincidan: contact_factor alto + barrel risk + regression.
- **F91.3 — UI panel MLB**: renderizar scores + signals + breakdown (narrativo) sin generar picks.
- **F91.4 — Per-batter real**: habilitar `QCM_LINEUP_PER_BATTER=true` cuando `mlb_official_lineups.py` exponga métricas por bateador (Statcast).

### Futuras mejoras recomendadas (global)
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
  - `QCM_LINEUP_PER_BATTER=true` (opcional) activa path real per-batter (cuando exista data) — default off.
  - `QCM_THRESHOLDS='{...}'` (opcional) override de thresholds del engine QCM.
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
  - MLB QCM (F91):
    - `services.mlb_quality_contact_matchup.compute_quality_contact_matchup(payload)` produce el bloque `quality_contact_matchup` (aún sin integración al pipeline).
- No regresiones:
  - Backend `pytest` verde (actual: **2671 passed, 2 skipped**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → fallback API-Sports o bloque vacío.
  - Si scraping FBref/Forebet falla → no bloquea; UI muestra parcial.
  - Si xG recent averages falla/timeout → no bloquea; UI informa estado.
  - Si corners fallan → nunca rompe el análisis; UI informa reason_code y ofrece debug.
  - MLB QCM: si faltan métricas → `available=false` y no bloquea ningún pipeline.
