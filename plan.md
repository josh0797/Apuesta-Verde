# Plan — Phases F58–F89 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).
> **Estado actual (resumen):** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA** + ✅ **P4.1 (LiveReevalPanel.test.jsx 3 preexistentes) COMPLETADA** + ✅ **F84.a (team_stats prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.b (H2H prioridad-inversa a TheStatsAPI) COMPLETADA** + ✅ **F84.e (odds prioridad-inversa a TheStatsAPI + line movement) COMPLETADA** + ✅ **F85 (Public xG — FBref + Forebet vía scrape.do) COMPLETADA** + ✅ **F85 Phase 2 (FBref search-page resolver + fuzzy matching) COMPLETADA** + ✅ **F86 (H2H Decision Policy puro en Python) COMPLETADA** + ✅ **F87 (Cableado quirúrgico en `_enrich_football`: H2H decision + xG-recent background dispatch) COMPLETADA** + ✅ **F88 (F86.2 — Editorial Consumer: h2h_decision + xg_recent_averages + UI) COMPLETADA** + ✅ **F89 (Sprint F86.1 — Calibración H2H rules + explicit polarity/sample/cap guards) COMPLETADA**.
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

## Estado: ✅ COMPLETADA

## Motivación
- Los thresholds previos eran “disjuntos” por accidente (seguridad emergente), no por diseño.
- Los mínimos estaban demasiado cerca de baselines típicas → señales débiles.
- Faltaban guards explícitos (polarity, DNB overlap, sample guard per-rule, cap agregado) y override por env para backtesting.

## Implementación ejecutada (3 archivos)

### 1) **REWRITE** `backend/services/football_h2h_decision_policy.py`
- ✅ Tabla `H2H_POINT_RULES` recalibrada con:
  - `min_rate` más estricto
  - `baseline` (DOCUMENTACIÓN, no afecta la lógica)
  - `min_sample` (EFECTIVO)
  - `label` con sufijo `_STRONG` (p.ej. `H2H_OVER_2_5_STRONG`, `H2H_BTTS_NO_STRONG`, `H2H_HOME_DNB_STRONG`).
- ✅ `get_active_rules()`:
  - Lee `H2H_POINT_RULES_OVERRIDE` en **tiempo de llamada**.
  - JSON merge por mercado, fail-soft; warnings si mercado desconocido / override inválido.
- ✅ `POLARITY_PAIRS` explícito:
  - (OVER/UNDER 1.5, 2.5, 3.5) + (BTTS_YES/BTTS_NO).
  - HOME_DNB vs AWAY_DNB se excluye del hard-conflict.
- ✅ `apply_polarity_guard(out, market_to_rate, active_rules)`:
  - Gana el mayor rate; tie → mayor points; tie → drop ambos + `H2H_POLARITY_UNRESOLVED`.
  - Expone `polarity_conflicts[]` y reason code `H2H_POLARITY_GUARD_TRIGGERED`.
  - Log warning `[h2h_polarity_guard]`.
- ✅ `apply_dnb_overlap_guard(out)` soft:
  - HOME_DNB + AWAY_DNB → `H2H_DNB_OVERLAP_DRAW_HEAVY` + `soft_conflicts[]`.
  - Cap combinado DNB a 4 (ajuste proporcional; `soft_conflict_adjustments`).
- ✅ `apply_total_cap(out)`:
  - Cap agregado `MAX_H2H_POINTS_TOTAL=8`.
  - Preserva signals; solo clamp numérico → `h2h_points_uncapped`, `h2h_points_total`, reason `H2H_POINTS_CAPPED`.
- ✅ `apply_h2h_decision_points(...)` ahora integra:
  1) Sample guard per-rule (si `sample_size < rule.min_sample` → puntos a la mitad + `LOW_SAMPLE_H2H_SIGNAL`).
  2) Polarity guard hard.
  3) DNB overlap guard soft.
  4) Cap agregado.

### 2) **MOD** `backend/services/football_editorial_prediction.py`
- ✅ `_H2H_SIGNAL_TRANSLATIONS_ES` y `_H2H_SIGNAL_TO_RATE_KEY` ahora reconocen **ambos** sets:
  - legacy F86 (`H2H_PROFILE_*`, `H2H_HOME_DOMINANT`, etc.)
  - calibrados F86.1 (`H2H_*_STRONG`).

### 3) **MOD** `backend/tests/test_f86_h2h_decision_policy.py`
- ✅ 6 tests existentes migrados a labels `_STRONG`.
- ✅ +14 tests nuevos (cubre los 8 del spec + extras de estabilidad):
  - thresholds ≥ baseline+5pp
  - env override merge / invalid JSON fallback / unknown market warning / copy semantics
  - polarity guard (unresolved tie + drops loser con rates distintos)
  - baseline doc-only
  - low sample: puntos halved + `LOW_SAMPLE_H2H_SIGNAL`
  - DNB overlap soft (no hard polarity) + cap combinado
  - cap total `MAX_H2H_POINTS_TOTAL`

## Validación
- ✅ Ruff: limpio.
- ✅ F86 tests: **43/43 PASS**.
- ✅ Tests focales (F86 + F86.2 + F87 + F74 + F69 + F83.2): **141/141 PASS**.
- ✅ Suite completa backend: **2606 passed, 2 skipped, 0 fallos**.
- ✅ Suite completa frontend: **125 passed, 0 fallos**.

## Compatibilidad
- Back-compat garantizada:
  - Editorial consumer F86.2 entiende labels legacy y STRONG.
  - Scoring applier F86.2 no depende de labels (market mapping) y su matching por substring funciona con `_STRONG`.

## Diferidos / fuera de scope
- Calibrador offline opcional (`football_h2h_threshold_calibrator.py`) diferido por falta de fuente `football_market_results` en el proyecto.

---

## 3) Pendientes y siguientes pasos (post-F89)

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
- Auditoría runtime:
  - `match_doc._provenance_team_stats`, `_provenance_h2h`, `_provenance_odds` presentes.
  - `match_doc.h2h_context` + `match_doc.h2h_decision` presentes tras ingesta (F87).
  - `match_doc.xg_recent_averages.status`: `PENDING_BACKGROUND_ENRICHMENT → SUCCESS|UNAVAILABLE|TIMEOUT`.
  - Editorial output incluye:
    - `editorial.h2h_block` (consumer-grade)
    - `editorial.xg_block` (PENDING/SUCCESS/TIMEOUT/UNAVAILABLE + tabla L1/L5/L15)
  - `best_protected_market.confidence_score` puede incorporar bump H2H con clamp+polarity.
- No regresiones:
  - Backend `pytest` verde (actual: **2606 passed, 2 skipped**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → fallback API-Sports o bloque vacío.
  - Si scraping FBref/Forebet falla → no bloquea; UI muestra parcial.
  - Si xG recent averages falla/timeout → no bloquea; UI informa estado.
