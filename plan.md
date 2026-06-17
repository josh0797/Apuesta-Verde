# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
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
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

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

### Fase 5 — UI Wiring (P2)
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase SPRINT A — Draw Potential (piloto retrospectivo) (COMPLETED ✅)

### Objetivo
Validar un módulo puro `compute_draw_potential()` (fail-soft, explicable) antes de invertir en infraestructura completa.

### Implementación realizada
- ✅ `services/football_draw_potential.py` (módulo puro, reason codes, labeler)
- ✅ Backtest piloto retrospectivo (sin fugas de futuro)

### Resultado
- ✅ Señal inicial verificada; listo para integración en backtest framework.

---

## Phase SPRINT B — Football Learning Dataset + Loops + UI (COMPLETED ✅)

### Objetivo
Infraestructura para snapshots pre/post partido, cascada de scraping pre-match, loops de aprendizaje y panel UI.

### Implementación realizada
- ✅ Colección `football_match_learning_snapshots`
- ✅ APScheduler jobs (pre-match snapshots)
- ✅ Cascada TheStatsAPI / CONCACAF / CAF hydration
- ✅ 4 learning loops + DC/NB calibration
- ✅ UI: `LearningSnapshotPanel.jsx`

### Estado
- ✅ Suites verdes y sin regresiones.

---

## Phase SPRINT D — Framework Backtest Histórico Point-in-Time (COMPLETED ✅)

### Objetivo
Crear un motor de backtest riguroso con disciplina point-in-time (sin leakage) y walk-forward.

### Implementación realizada
- ✅ `services/football_historical_ingestor.py` (backtest PIT + `build_point_in_time_features`)
- ✅ `services/football_backtest_engine.py`
- ✅ `services/football_backtest_metrics.py`
- ✅ `scripts/run_backtest.py`
- ✅ Ejecución validada en Premier League 23/24

---

## Phase SPRINT D2 — Backtest histórico en torneos nacionales (WC2022 + Euro2024) — COMPLETADO ✅

### Objetivo
Validar si el módulo **Draw Potential** mejora en torneos nacionales manteniendo disciplina point-in-time.

### Estado
- ✅ Implementado: parser openfootball + standings PIT + `TOURNAMENT_CONTEXT_SCORE` + modo no-market + reportes.

---

## Phase SPRINT D3 — Backtest National Tournaments: OVER 1.5 + DOUBLE CHANCE (calibration-only) — COMPLETADO ✅

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT D5 — Multi-league + multi-tournament DRAW + cohortes (observe_only) — EN PROGRESO 🟡 (P0)

### Nota de estado
- Este bloque se mantiene como “D5” en el plan histórico.
- La ejecución y comparativa nueva (Sprint D7) reutiliza componentes de D5 (cohortes + comparativa) pero agrega The Odds API **historical** y cap de créditos.

---

## Phase SPRINT D6 — Probar que el Walk-Forward Calibrator NO es un no-op — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1 — Live Odds Monitor (Base) + persistencia `odds_snapshots` (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1-d — Hook automático (Scheduler) para Market Identity Resolver — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.1.1-f — 365Scores “Tendencias Top” (reemplazo SportyTrader editorial) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — COMPLETADO ✅ (P0)

### Estado
- ✅ COMPLETADO — sin cambios.

---

# Phase SPRINT D7 — Backtest comparativo DRAW (Ligas vs Selecciones) + Post-mortem & Remediación — EN PROGRESO 🟡 (P1)

## Contexto
- Se ejecutó `scripts/run_backtest_d7_comparative.py` con cap estricto de créditos.
- El sondeo histórico (WC2022, 2022-11-27) devolvió eventos (p.ej. 24), pero el reporte final resultó con 0 picks/0 matches domésticos y bloque nacional no disponible.

## Hallazgos (post-mortem)
- **BUG #1 (crítico, confirmado):** el orquestador pasa la **ruta** del CSV a `parse_football_data_csv`, pero el parser espera el **texto del CSV**. Resultado: `n_matches=0` en todas las ligas.
- **BUG #2 (crítico):** falta el directorio `/app/data/openfootball/` (y por ende los JSON de ground truth). Resultado: el bloque nacional gasta créditos (cobertura existe) pero no puede liquidar → `GROUND_TRUTH_MISSING`.
- **BUG #3 (visibilidad):** el orquestador hardcodea `UNAVAILABLE_NO_COVERAGE` en lugar de propagar `reason_codes` reales, ocultando `MAX_CREDITS_REACHED` u otros.

## Objetivo de D7 (actualizado)
1) Reparar el pipeline D7 para que produzca métricas reales (domestic y, si hay ground truth, national) sin romper suites.
2) Re-ejecutar bloque doméstico **dos veces** (sin créditos): `min_edge_pp=4.0` y `min_edge_pp=3.0`.
3) Mantener `observe_only` y disciplina anti-overfitting (cohortes solo con features pre-match).

## Fase A — Fix del orquestador (0 créditos) — EN PROGRESO
- **A1 (BUG #1):** leer el **contenido** del CSV antes de llamar a `parse_football_data_csv`.
  - Implementación: `csv_text = Path(csv_path).read_text()`.
- **A2 (BUG #3):** propagar `reason_codes` / `reason_code` reales del cliente histórico en el reporte por torneo.
  - Distinguir claramente:
    - `MAX_CREDITS_REACHED`
    - `GROUND_TRUTH_MISSING`
    - `UNAVAILABLE_NO_COVERAGE`
    - `HTTP_ERROR`
- **A3:** logging visible por liga/torneo:
  - `n_matches`, `n_picks`, `credits_used_delta`, `aborted`, y motivo final.
- **A4:** tests de regresión:
  - Añadir test que verifique que el orquestador alimenta `parse_football_data_csv` con texto (no ruta) y que `n_matches > 0` usando los CSV cacheados.
- **A5:** flags CLI para ejecuciones controladas:
  - `--min-edge-pp` (float)
  - `--skip-national` (bool)
  - `--out` (path)

## Fase B — Ground truth openfootball (0 créditos) — PENDIENTE
- Crear `/app/data/openfootball/`.
- Descargar/instalar JSONs desde **openfootball/football.json**:
  - WC 2022 → `/app/data/openfootball/wc2022.json`
  - Euro 2024 → `/app/data/openfootball/euro2024.json`
  - Copa América 2024 → `/app/data/openfootball/copa2024.json`
  - Copa América 2021 → `/app/data/openfootball/copa2021.json`
- Sanity checks:
  - `parse_openfootball_json(file) → n_matches > 0` por archivo.

## Fase C — Re-ejecución doméstica (0 créditos) — PENDIENTE (confirmado por el usuario)
- **C1 (edge 4pp):**
  - `python -m scripts.run_backtest_d7_comparative --skip-national --min-edge-pp 4.0 --out /app/backtest_d7_domestic_edge4.json`
- **C2 (edge 3pp):**
  - `python -m scripts.run_backtest_d7_comparative --skip-national --min-edge-pp 3.0 --out /app/backtest_d7_domestic_edge3.json`
- Comparar por liga:
  - `n_matches`, `n_picks`, `roi`, `roi_ci_low/high`, `hit_rate`, `sample_status`, `warnings`.

## Fase D — Validación — PENDIENTE
- **D1:** `pytest` backend completo (mantener suite verde sin regresiones).
- **D2:** resumen ejecutivo para el usuario con comparación lado a lado (edge4 vs edge3) y recomendaciones de parámetros para una futura corrida nacional.

## Fase E — Próximos pasos (solo con OK del usuario)
- Re-correr bloque nacional una vez que exista ground truth.
- Evaluar caching para reutilizar eventos/odds ya pagados (si hay artefactos persistidos), evitando re-gasto de créditos.

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Progreso actual
- ✅ Paso 1/3 completado: extracción de odds cascade a `services/_ingestion_helpers/football_odds_cascade.py`.

## Pendiente
- ⏳ Paso 2/3: extraer deep enrichment.
- ⏳ Paso 3/3: extraer live stats hydration.
- ⏳ Refactor `ingest_upcoming`.

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳

## Plan
- Lineups: TheStatsAPI primary, API-Sports fallback.
- Standings: TheStatsAPI primary, API-Sports fallback.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- 🟡 **SPRINT D5** (histórico en curso): cohortes + reportes multi-competición.

### Pendientes P1
- 🟡 **SPRINT D7 Remediación** (este documento): fixes + descarga openfootball + reruns domésticos edge4/edge3.
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Expandir backtest framework a otros mercados (BTTS / Over 2.5 / Corners) tras validar DRAW.

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - `observe_only` en SPRINT D/E (sin apuestas automáticas).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **E.1**: polling limitado al universo visible de UI.
  - **D6**: shrinkage es opt-in (`shrinkage_K=None` preserva legacy).

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - (D7) Nuevos flags previstos:
    - `--min-edge-pp`
    - `--skip-national`
    - `--out`
