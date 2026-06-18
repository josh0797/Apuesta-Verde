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

# Phase SPRINT D7 — Backtest comparativo DRAW (Ligas vs Selecciones) + Post-mortem & Remediación — COMPLETADO ✅ (P1)

## Contexto
- Se ejecutó `scripts/run_backtest_d7_comparative.py` con cap estricto de créditos.
- El sondeo histórico (WC2022, 2022-11-27) devolvió eventos (p.ej. 24).
- El reporte inicial resultó con 0 picks/0 matches domésticos y bloque nacional no disponible.

## Hallazgos (post-mortem) — RESUELTOS ✅
- **BUG #1 (crítico, confirmado):** el orquestador pasaba la **ruta** del CSV a `parse_football_data_csv`, pero el parser espera el **texto del CSV**. Resultado: `n_matches=0` en todas las ligas.
- **BUG #2 (crítico):** faltaba `/app/data/openfootball/` (y JSON de ground truth). Resultado: el bloque nacional gastaba créditos (cobertura existía) pero no podía liquidar → `GROUND_TRUTH_MISSING`.
- **BUG #3 (visibilidad):** el orquestador hardcodeaba `UNAVAILABLE_NO_COVERAGE` en lugar de propagar `reason_codes` reales (`MAX_CREDITS_REACHED`, etc.).

## Fase A — Fix del orquestador (0 créditos) — COMPLETADO ✅
- ✅ A1: leer **contenido** del CSV (`Path.read_text()`) antes del parser.
- ✅ A2: propagar `reason_codes`/`reason_code` reales en el bloque nacional.
- ✅ A3: logging visible por liga/torneo (n_matches, n_picks, créditos, motivo).
- ✅ A4: tests de regresión (parser texto vs ruta; parse_empty; skip).
- ✅ A5: flags CLI `--min-edge-pp`, `--skip-national`, `--out`.

## Fase B — Ground truth openfootball (0 créditos) — COMPLETADO ✅ (parcial)
- ✅ Creado `/app/data/openfootball/`.
- ✅ Descargados:
  - WC 2022 → `/app/data/openfootball/wc2022.json` (64 partidos)
  - Euro 2024 → `/app/data/openfootball/euro2024.json` (51 partidos)
- ⏳ Copa América 2024/2021: aplazado (repo openfootball provee `copa.txt` y requeriría un parser distinto).

## Fase C — Re-ejecución doméstica (0 créditos) — COMPLETADO ✅
- ✅ C1: corrida `min_edge_pp=4.0` → `/app/backtest_d7_domestic_edge4.json`.
- ✅ C2: corrida `min_edge_pp=3.0` → `/app/backtest_d7_domestic_edge3.json`.

**Nota:** inicialmente ambas corridas daban resultados idénticos por un gate aguas arriba (ver Sprint D7-E). Tras parametrización, el flag deja de ser inerte.

## Fase D — Validación — COMPLETADO ✅
- ✅ `pytest` backend completo (incluyendo nuevos tests) sin regresiones.

---

# Phase SPRINT D7-E — Threshold parametrization + honest sweep + multi-season sanity check (DRAW) — COMPLETADO ✅ (P1)

## Contexto
Se detectó un bug de diseño: `--min-edge-pp` del CLI era **inoperante** porque el threshold real (`EDGE_VALUE_THRESHOLD_PP=4.0`) estaba hardcodeado dentro de `football_draw_potential.py` y el motor filtraba por `label in (VALUE_DRAW, STRONG_VALUE)`, descartando picks con edge ∈ [3,4)pp “en silencio”.

## Fase E1–E3 — Parametrización end-to-end (0 créditos) — COMPLETADO ✅
- ✅ `compute_draw_potential` ahora acepta:
  - `value_threshold_pp` (opt-in; default preserva legacy 4.0)
  - `strong_threshold_pp` (opt-in; default preserva legacy 8.0)
  - Auditoría en `debug`: `value_threshold_pp_effective`, `strong_threshold_pp_effective`.
- ✅ `football_backtest_engine`:
  - `_predict_draw` acepta thresholds.
  - `run_backtest` propaga `min_edge_pp` como threshold del label.
  - Deriva `_effective_strong_pp = min(12.0, 2*min_edge_pp) si min_edge_pp>8; si no, 8.0`.
  - Elimina hardcodes 4.0/8.0 en el re-label post-calibración.
- ✅ Tests nuevos:
  - override cambia label
  - bajar `min_edge_pp` aumenta picks
  - back-compat: Premier 24/25 mantiene 149 picks a 4pp.

## Fase E4 — Barrido honesto de thresholds (5 ligas, 24/25) — COMPLETADO ✅
Script: `scripts/run_backtest_d7_threshold_sweep.py`.

Resultado (agregado 5 ligas, weighted por n_bets):

| edge_pp | n_bets | w_ROI | spread inter-liga (roi_max - roi_min) |
|---:|---:|---:|---:|
| 2.0 | 596 | -2.9% | 61.6 pp |
| 3.0 | 510 | -3.9% | 73.8 pp |
| 4.0 | 419 | -1.4% | 87.3 pp |
| 5.0 | 353 | -8.2% | 91.6 pp |
| 6.0 | 279 | -2.5% | 81.7 pp |
| 8.0 | 187 | -10.3% | 99.5 pp |

Observaciones:
- ROI agregado **siempre negativo** y errático (no robusto al threshold).
- Spread inter-liga aumenta al elevar el threshold → firma de ruido.
- Hit-rate decae con threshold (≈21.1% → ≈16.0%).

## Fase E5–E6 — Multi-season Premier League (4 temporadas) — COMPLETADO ✅
- ✅ Descargados CSV gratuitos:
  - `/app/data/football_data_co_uk/E0_2122.csv`
  - `/app/data/football_data_co_uk/E0_2223.csv`
  - `/app/data/football_data_co_uk/E0_2324.csv`
  - (ya existía) `/app/data/football_data_co_uk/E0_2425.csv`
- ✅ Script: `scripts/run_backtest_d7_premier_multiseason.py`.

Tabla clave (edge≥4pp):

| Season | edge≥4pp ROI |
|---|---:|
| 2021-22 | -6.69% |
| 2022-23 | -6.76% |
| 2023-24 | -20.97% |
| 2024-25 | +27.96% |
| **MEAN** | **-1.61%** |
| **STDEV** | **20.83%** |

Conclusión: el +27.96% 24/25 fue un outlier; el promedio colapsa cerca de 0 con gran varianza inter-temporada.

## Fase E7 — Validación — COMPLETADO ✅
- ✅ `pytest` backend completo: **3632 passing**, 2 skipped, 0 regresiones.

## Veredicto científico (D7-E)
**No hay evidencia de edge real** en el módulo Draw Potential para mercado DRAW en ligas domésticas bajo este horizonte.

---

# Phase SPRINT D7-F — OVER_2_5 / UNDER_2_5 con la misma disciplina (D7-E) — COMPLETADO ✅ (P1)

## Objetivo
Aplicar la misma disciplina de validación que en D7-E (parametrización end-to-end, barrido de thresholds multi-liga, multi-season) a los mercados **OVER_2_5** y **UNDER_2_5**, manteniendo:
- point-in-time correctness,
- fail-soft,
- `observe_only`,
- 0 regresiones en la suite.

## Trabajo realizado (todo offline, 0 créditos) — COMPLETADO ✅

### F1 — Predictor `compute_score_grid_potential` (Dixon-Coles bivariate score grid 9×9)
- ✅ Nuevo módulo: `services/football_score_grid_potential.py`.
- ✅ Grid 0..8 goles por equipo (masa cubierta típica 99.78–99.99%).
- ✅ OVER_2_5 y UNDER_2_5 calculados **sumando SUS celdas** (no como complemento) para preservar la τ-correction asimétrica en 0–0 / 0–1 / 1–0 / 1–1.
- ✅ BTTS_YES/NO también disponibles en el módulo (pero aplazado en backtests por falta de cuotas históricas gratis en football-data.co.uk).

### F2 — Parser + PIT features
- ✅ `parse_football_data_csv` extendido para:
  - Opening: `B365>2.5` / `B365<2.5`
  - Closing: `B365C>2.5` / `B365C<2.5`
  - Fallback cascade: B365 → Pinnacle (P/PC) → Avg/AvgC → BbAv.
  - Nuevos campos: `odd_over25`, `odd_under25`, `*_open`, `*_close`.
- ✅ `build_point_in_time_features` ahora expone:
  - `market_implied_over25_prob = 1/odd_over25`
  - `market_implied_under25_prob = 1/odd_under25`
  - Nota: NO se deriva under = 1−over; se preserva overround asimétrico.

### F3 — Engine market-aware generalizado
- ✅ `MARKET_AWARE_SUPPORTED = ("DRAW", "OVER_2_5", "UNDER_2_5")`.
- ✅ Nuevos predictors/hit_fns:
  - `_predict_over25`, `_predict_under25`
  - `_hit_over25`, `_hit_under25`
- ✅ `_extract_prob_pct` y `_store_prob_pct` soportan los nuevos mercados.
- ✅ Inyección de `market_implied` / `edge` / `label` (GENERIC) en el verdict **pre y post calibración**.
- ✅ Gate de fire generalizado: acepta `LABEL_VALUE_GENERIC` / `LABEL_STRONG_VALUE_GENERIC` además de los labels legacy de DRAW.
- ✅ Pick rows en modo con-odds ahora exponen:
  - `odd` (canónico por mercado)
  - `odd_draw` se preserva solo por back-compat cuando market=DRAW.
- ✅ `compute_backtest_metrics` generalizado para usar `odd` (fallback a `odd_draw`).

### F4 — Scripts
- ✅ `scripts/run_backtest_d7_threshold_sweep.py` ahora acepta `--market {DRAW, OVER_2_5, UNDER_2_5}` y escribe por defecto `..._\<market\>.json`.
- ✅ `scripts/run_backtest_d7_premier_multiseason.py` ahora acepta `--market` y escribe por defecto `..._\<market\>.json`.

### F5 — Tests
- ✅ Nuevo archivo: `tests/test_sprint_d7_phaseF_score_grid_markets.py` (16 tests).
- Incluye:
  - sanity del grid (rangos, suma≈100%, monotónico con xG, fallback sin inputs),
  - regresión explícita: `test_under25_is_NOT_complement_of_over25`,
  - parser (opening/closing/fallback),
  - engine (market-aware O/U 2.5),
  - back-compat DRAW (149 picks @ 4pp, ROI=+27.96%).

### F6 — Ejecución de barridos (artefactos)

**Threshold sweep · top-5 ligas · 2024/25**

| edge_pp | OVER_2_5 w_ROI | UNDER_2_5 w_ROI |
|---:|---:|---:|
| 2.0 | -9.2% | -12.2% |
| 3.0 | -9.3% | -13.4% |
| 4.0 | -9.4% | -14.4% |
| 5.0 | -9.6% | -15.2% |
| 6.0 | -9.2% | -15.3% |
| 8.0 | -8.1% | -14.9% |

**Multi-season Premier (4 temporadas) — ROI @ edge=4pp**

- OVER_2_5: 21/22=-5.10%, 22/23=-11.35%, 23/24=+1.88%, 24/25=-7.50% → MEAN=-5.52% STDEV=5.56%.
- UNDER_2_5: 21/22=-13.43%, 22/23=-3.90%, 23/24=-14.13%, 24/25=-11.57% → MEAN=-10.76% STDEV=4.70%.

**Artefactos (D7-F):**
- `/app/backtest_d7_threshold_sweep_over_2_5.json`
- `/app/backtest_d7_threshold_sweep_under_2_5.json`
- `/app/backtest_d7_premier_multiseason_over_2_5.json`
- `/app/backtest_d7_premier_multiseason_under_2_5.json`

### F7 — Validación
- ✅ `pytest` backend completo: **3650 passing**, 2 skipped, 0 regresiones.

## Veredicto científico (D7-F)
- A diferencia de DRAW (ruido errático), OVER_2_5 y UNDER_2_5 muestran un **sesgo sistemático estable**:
  - ROI agregado y cross-temporada siempre negativo.
  - Bastante estable vs threshold (no hay “corte mágico”).
  - Stdev moderado (~5–7%) comparado con DRAW (~21%).
- Diagnóstico probable: el modelo DC plano (tunables globales `HOME_ADV_LAMBDA=0.20`, `RHO=-0.13`, límites de λ) está **miscalibrado estructuralmente** vs mercado; el calibrador walk-forward + shrinkage no corrige el sesgo → la causa parece ser el modelo base/parametrización.
- Ningún sub-resultado cruza significancia estadística.
- **BTTS aplazado** (no hay cuotas históricas gratis en football-data.co.uk; reactivarlo requeriría otra fuente o gasto de créditos).

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
- ✅ (COMPLETADO) Validación de mercados con disciplina D7-E:
  - DRAW (D7-E) refutado como edge real.
  - OVER_2_5 / UNDER_2_5 (D7-F) refutados como edge (sesgo negativo estable).
- ⏳ **BTTS aplazado** (sin cuotas históricas gratis). Solo reactivar con nueva tesis + fuente/caching.
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Nuevas hipótesis de señal para O/U 2.5:
  - features adicionales (lineups, std de xG, matchup/estilos, fatiga),
  - calibración por liga (pero con guardrails anti-overfitting),
  - o pivotear a otro mercado/línea.

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - `observe_only` en SPRINT D/E/F (sin apuestas automáticas).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **E.1**: polling limitado al universo visible de UI.
  - **D6**: shrinkage es opt-in (`shrinkage_K=None` preserva legacy).
  - **Observe-only**: no implementar apuestas automáticas.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - (D7) Flags CLI ya implementados:
    - `--min-edge-pp`
    - `--skip-national`
    - `--out`
  - (D7-E/F scripts):
    - `scripts/run_backtest_d7_threshold_sweep.py --market {DRAW,OVER_2_5,UNDER_2_5}`
    - `scripts/run_backtest_d7_premier_multiseason.py --market {DRAW,OVER_2_5,UNDER_2_5}`

---

## SPRINT F — Ingesta de Tendencias Top desde 365Scores (NUEVO)

**Objetivo**: ingestar las "Tendencias Top" de 365Scores vía Scrape.do para los partidos visibles en la UI que pasaron filtros del engine, y **reemplazar** el bloque UI "Revisión manual — alternativas posibles" por "Tendencias Top — 365Scores". Las tendencias son evidencia contextual `observe_only`: no se convierten en picks automáticos ni modifican el edge.

### Sub-fases

- ✅ **F.1 — Identity Resolver** (COMPLETADO — criterio de salida `F1_IDENTITY_RESOLVER_READY`):
  - Nuevo módulo: `services/external_sources/three65scores_identity_resolver.py`.
  - Cascada: (1) cache Mongo `football_365scores_identities`; (2) parsing URL conocida (`game_id`/`matchup_id`); (3) búsqueda por equipos + competición + commence_time (±6h configurable).
  - **Validación crítica**: cada `team_id` recibido debe validarse contra el nombre del equipo que devuelve el payload de 365Scores. No asumir que el orden home/away del slug es correcto.
  - Aliases extendidos: Mexico↔México, South Korea↔Corea del Sur↔Korea Republic, Congo DR↔RD Congo↔DR Congo, Ivory Coast↔Côte d'Ivoire↔Costa de Marfil, USA↔EEUU, North Macedonia↔Macedonia del Norte, Bosnia & Herzegovina↔Bosnia y Herzegovina.
  - Estados: `RESOLVED` | `AMBIGUOUS` | `NOT_FOUND` | `SOURCE_UNAVAILABLE` | `INVALID_TEAM_MAPPING`.
  - Confidence: `HIGH` | `MEDIUM` | `LOW`.
  - Persistencia: colección `football_365scores_identities` con índices `unique(internal_match_id)`, `unique(game_id)`, `(home_team_id, away_team_id, commence_time)`.
  - Fixture de pruebas: México vs Corea del Sur, `game_id=4627854`, `competition_id=5930`, `home_team_id=5106`, `away_team_id=2383`, kickoff 2026-06-17.
  - Tests: aliases, ±6h tolerance, INVALID_TEAM_MAPPING (slot home/away invertido), AMBIGUOUS (dos candidatos), cache hit, fail-soft cuando fuente cae.
  - Criterio de salida: `F1_IDENTITY_RESOLVER_READY` (tests verdes + 3677 previos sin regresión).

- ✅ **F.2 — Descubrimiento + Cliente Top Trends** (COMPLETADO — criterio de salida `F2_TRENDS_CONTRACT_STABLE`):
  - Discovery timeboxed: máximo 1 ciclo técnico.
  - Estrategia híbrida: cargar página pública, interceptar `fetch/XHR` con headless, buscar requests con `4627854/5106/2383/trend/insight/pre-game/top trends`, revisar JSON embebido (`__NEXT_DATA__`/initial state), bundles, requests existentes en `score365_*_client.py`.
  - Si falla → entregar reporte de discovery (rutas/status/content-type/schema-fragment/causa de bloqueo) y marcar `BLOCKED_ENDPOINT_NOT_IDENTIFIED`. **No inventar endpoints**, no implementar parser por supuestos.
  - Si requiere autenticación privada/captcha → reason `365SCORES_TRENDS_SOURCE_RESTRICTED`.
  - Criterio de salida: `F2_TRENDS_CONTRACT_STABLE` (endpoint + método + params + schema real + fixture JSON anonimizado + parser + cache + tests sin red).

- ✅ **F.3 — UI** (COMPLETADO — criterio de salida `F3_UI_TOP_TRENDS_INTEGRATED`):
  - Reemplazar `Revisión manual — alternativas posibles` por `Tendencias Top — 365Scores` en el componente correspondiente (`MarketIdentityResolverPanel.jsx` u otro consumidor de revisión manual). No mostrar ambos paneles, no fabricar tendencias simuladas cuando la fuente caiga.
  - Mantener `observe_only` siempre.
  - Criterio de salida: `F3_UI_TOP_TRENDS_INTEGRATED`.

### Reglas operacionales del Sprint F
- Cero regresión: 3677 tests pre-Sprint F deben seguir pasando.
- Resolver es fail-soft: nunca raise, siempre dict con `status`/`confidence`/`reason_code`.
- Los IDs del caso de prueba (5106/2383/4627854/5930) sólo aparecen en fixtures, **nunca hardcodeados** en lógica productiva.
- Si Scrape.do/365Scores responde con bloqueo → marcar `SOURCE_UNAVAILABLE` y degradar limpio.

### F.1 — Resultado de cierre (2026-06-17)

- **Status**: `F1_IDENTITY_RESOLVER_READY` ✅
- **Módulo nuevo**: `services/external_sources/three65scores_identity_resolver.py` (lint clean).
- **Tests nuevos**: `tests/test_sprint_f1_three65scores_identity_resolver.py` — 29 tests verdes.
- **Suite total**: 3706 passed / 2 skipped (3677 previos + 29 nuevos), 0 regresiones.
- **Persistencia**: índices `ix_internal_match_id (unique)`, `ix_game_id (unique partial $gt:0)`, `ix_teams_commence` confirmados creados al startup.
- **Resolver expone**:
  - `resolve_match_identity(internal_match_id, home_team, away_team, commence_time, competition_id?, match_url?, tolerance_hours=6, db?, persist=True, games_fetcher?, game_detail_fetcher?, force_refresh=False)`.
  - Helpers públicos: `normalize_team_name`, `build_team_alias_set`, `validate_team_mapping`, `ensure_indexes`.
- **Garantías clave**:
  - Validación obligatoria `team_id ↔ nombre`. Si 365Scores devuelve [home, away] invertido, el resolver detecta el swap y persiste los IDs ALINEADOS al canónico (con `mapping_reason=F1_TEAM_MAPPING_SWAPPED` para auditoría). Si ninguno coincide → `INVALID_TEAM_MAPPING`.
  - `competition_id` actúa como guard duro: descarta candidatos con otra competición conocida.
  - Tolerancia `±6h` configurable.
  - Cache Mongo: cache-hit → `RC_FROM_MONGO_CACHE` y no llama a la fuente.
  - `force_refresh=True` salta la cache (útil para re-resoluciones).
  - Fail-soft: nunca lanza excepción.
- **Lo que NO incluye F.1** (queda para F.2): la lógica de scraping vía Scrape.do. F.1 sólo expone los hooks (`games_fetcher`/`game_detail_fetcher`) y el contrato; los adaptadores reales a HTTP se cablearán en F.2 una vez se identifique el endpoint estable.

### Próximo paso

**F.2** — Descubrimiento timeboxed del endpoint Top Trends. Necesita 1 ciclo técnico con browser headless interceptando XHR/fetch y revisión de `__NEXT_DATA__`. Si falla → reporte de discovery + `BLOCKED_ENDPOINT_NOT_IDENTIFIED`.

### F.2 — Resultado de cierre (2026-06-17)

- **Status**: `F2_TRENDS_CONTRACT_STABLE` ✅
- **Endpoint confirmado** (descubierto vía Playwright headless interceptando XHR):
  `GET https://webws.365scores.com/web/trends/?appTypeId=5&langId={1|29}&timezoneName=UTC&userCountryId=333&games={game_id}&topBookmaker=103`
  - HTTP 200, `application/json; charset=utf-8`
  - Confirmado vía Scrape.do (transport productivo) en `scripts/run_sprint_f2_capture_fixture.py`.
- **Schema observado**:
  - Top-level: `trends[]`, `bookmakers[]`, `lastUpdateId`, `ttl`, `sports[]`, `countries[]`, `competitions[]`, `competitors[]`, `games[]`.
  - Por trend: `id`, `lineTypeId`, `text`, `cause`, `betCTA`, `isTop`, `competitorIds[]`, `gameId`, `percentage`, `odds{rate, oldRate, originalRate, trend, bookmakerId}`, `confidenceTrendIds[]`.
  - `lineTypeId` taxonomy observada: `1=ML`, `3=OU_GOALS`, `5=1H_ML`, `7=FIRST_GOAL`, `12=BTTS`. Para valores no mapeados se emite `LINE_TYPE_{id}` (nunca se descarta silenciosamente).
- **Módulo nuevo**: `services/external_sources/three65scores_top_trends_client.py` (lint clean).
  - `fetch_top_trends(game_id, ...)` low-level con transport inyectable y cache Mongo (TTL 30 min default).
  - `fetch_top_trends_for_match(internal_match_id, home_team, away_team, commence_time, ...)` high-level que orquesta F.1 (identity) → F.2 (trends).
  - `normalize_trends_payload(payload)` parser puro (testeable sin Mongo ni red).
  - Confidence heuristic: total≥10 ∧ pct≥0.80 → HIGH; total≥5 ∧ pct≥0.70 → MEDIUM; isTop=True floor MEDIUM; resto LOW.
  - Detección de `team_side` (home/away/both/unknown) vía `competitorIds` cruzados con `home_team_id`/`away_team_id` de F.1. Detección de `scope` (home/away/first_half/all) por texto + line_type.
- **Cache**: colección `football_365scores_top_trends` con `ix_game_language (unique)` + TTL 6h hard ceiling en `fetched_at`. Registrado en `server.py` startup.
- **Fixture capturado**: `tests/fixtures/365scores_top_trends_4627854.json` (10 KB, 12 trends del partido México vs Corea del Sur, incluyendo 2+ `isTop=True`).
- **Tests nuevos**: `tests/test_sprint_f2_top_trends_client.py` — 26 tests verdes (parser puro + payload completo del fixture real + cache hit/miss/stale + force_refresh + filtro `only_top` + langId 1/29 + identity short-circuit + fail-soft).
- **Suite total**: 3732 passed / 2 skipped (3677 base + 29 F.1 + 26 F.2), 0 regresiones.

### Próximo paso — F.3 (UI)

Reemplazar el bloque "Revisión manual — alternativas posibles" por "Tendencias Top — 365Scores" en el componente correspondiente (`MarketIdentityResolverPanel.jsx` u otro consumidor). Endpoint de backend a exponer:

`GET /api/football/365scores/top-trends?internal_match_id=...` (o un payload POST con identity ya pre-resuelta).

Reglas:
- `observe_only`: las tendencias se muestran como evidencia contextual, no modifican picks ni edge.
- Si la fuente cae → mostrar reason code (`F2_TRANSPORT_UNAVAILABLE` / `F2_IDENTITY_NOT_RESOLVED` / `F2_TRENDS_EMPTY`) en la UI; nunca fabricar tendencias.
- No mostrar ambos paneles ("Revisión manual" y "Tendencias Top") a la vez.

### F.3 — Resultado de cierre (2026-06-18)

- **Status**: `F3_UI_TOP_TRENDS_INTEGRATED` ✅
- **Endpoint backend nuevo**: `POST /api/football/365scores/top-trends`
  - Body: `{internal_match_id, home_team, away_team, commence_time (ISO), competition?, competition_id?, match_url?, language?, only_top?, force_refresh?}`.
  - Orquesta F.1 (identidad) → F.2 (trends) → cache Mongo.
  - Marca `observe_only: True` siempre. Never raises.
  - Live fetchers cableados vía `services/external_sources/three65scores_live_fetchers.py` (Scrape.do como transport; fail-soft con `[]`/`{}` en fallos).
- **Componente UI nuevo**: `frontend/src/components/Top365TrendsPanel.jsx`.
  - Lazy fetch on first expand (no carga si el usuario no abre el panel).
  - Estados: loading (skeleton+spinner), error con `reason_code` legible (`F2_IDENTITY_REQUIRED`, `F2_TRANSPORT_UNAVAILABLE`, `F2_TOP_TRENDS_EMPTY`, etc.), success con lista.
  - Cada trend: badge `isTop`, market chip, team_side chip, scope chip si distinto del team_side, confidence chip (HIGH=emerald / MEDIUM=amber / LOW=slate), texto raw, sample `hits/total`, barra de progreso `percentage`, link al partido en 365Scores.
  - Footer "observe_only" + `game_id` resuelto para auditoría.
  - `data-testid` en todos los elementos interactivos (toggle, refresh, list, row-N-market, row-N-confidence, etc.).
  - Botón refresh fuerza `force_refresh=True`.
- **Reemplazo en Dashboard**: `frontend/src/pages/DashboardPage.jsx` cambia `<PossibleAlternativeMarkets>` por `<Top365TrendsPanel>` cuando `possibleAlts.length > 0`. No se muestran ambos paneles a la vez. No se fabrican tendencias simuladas cuando la fuente cae.
- **Bug pre-existente arreglado**: `MarketIdentityResolverPanel.jsx` (Sprint D10) referenciaba `missing`, `manualPrice`, `setManualPrice` sin definirlos — causaba `Uncaught runtime error` que rompía la sección de discarded rows del Dashboard. Fix: declarar `useMemo` para `missing` (derivado de las props canónicas), state `manualPrice`/`setManualPrice`, y `effectivePrice` que cae al `manualPrice` cuando no hay `detectedOdd`. Lint clean.
- **Validación end-to-end en producción (preview)**:
  - Backend: 3735 passed / 2 skipped (3677 base + 32 F.1 + 26 F.2), 0 regresiones.
  - Endpoint live test (Portugal vs DR Congo, game_id=4697734): identity RESOLVED HIGH confidence, 5 trends ES, cache hit confirmado.
  - UI render real con demo account: panel "Tendencias Top — 365Scores" se renderiza con icono Sparkles + theme cyan correcto. Caso `F2_IDENTITY_REQUIRED` mostrado limpio cuando el item descartado no expone `commence_time` (mensaje claro, no se muestra el bloque antiguo). Sin errores de runtime.
- **Resultado**: Sprint F **COMPLETO** (F.1 + F.2 + F.3). Las tendencias de 365Scores se ingestan, normalizan, cachean y muestran en la UI como evidencia contextual `observe_only`. Sin tocar el engine ni los picks.

### Próximos pasos (post Sprint F)

Sin tareas P0 abiertas. Siguen en pendientes:
- **D9.2 Block 1**: xG real (FBref/Understat) para el Residual Model (P1).
- **REFACTOR-1**: Extraer Steps 2 y 3 fuera de `data_ingestion.py` (P2).
- **F84.c/F84.d**: Lineups + Standings via API-Sports (P1).
- **BTTS market backtest**: aplazado hasta sourcing de cuotas históricas.

---

## SPRINT D8 — UNDER_3_5 (ligas) + DRAW/cohorte (selecciones)

### D8 Fase 1 — UNDER_3_5 / OVER_3_5 en ligas (CERRADA — `WELL_CALIBRATED_BUT_NO_EDGE_DEMONSTRABLE`)

**Pregunta**: ¿la línea 3.5 esconde una señal real o replica el patrón de "modelo bien calibrado pero sin edge sobre devig" ya observado en 2.5?

**Implementación**:
- Predictor `compute_score_grid_potential`: añadidos `over35_probability` y `under35_probability` sumando sus PROPIAS celdas (i+j ≥ 4 y ≤ 3 respectivamente). Audit invariant `p_under35_complement_check < 1e-9` testado.
- Parser `parse_football_data_csv`: añadidas las cascadas `B365>3.5/B365<3.5` + close + Avg fallback. Preserva `prefer_closing`.
- Engine `MARKET_AWARE_SUPPORTED` extendido con `OVER_3_5`/`UNDER_3_5`. Hit functions y NO_MARKET_THRESHOLDS registrados.
- 24 tests nuevos (`tests/test_sprint_d8_phase1_under_3_5.py`) cubriendo: NOT-complement, monotonía, sample size en bucket, parser open/close/fallback, engine integration, back-compat 2.5.
- Suite total: **3761 passed / 2 skipped** (3735 + 26), 0 regresiones.

**Hallazgo crítico durante la ejecución**:
- `football-data.co.uk` **no publica** columnas 3.5 (>3.5 / <3.5) ni en open ni en close ni en Avg. Verificado contra los 8 CSVs cacheados y contra el remoto live. El parser está listo para cuando esos datos lleguen, pero hoy quedan en `None`.
- Resultado: **no es posible calcular `delta_brier_vs_devig` para 3.5 con el dataset actual**. Por honestidad, se ejecuta un diagnóstico *model-only* (predictor vs ground truth) y se compara con el patrón ya conocido de 2.5.

**Resultados model-only** (`/app/diagnostics/calibration_under_3_5_*.json` y `over_3_5_*`):

| Mercado    | Scope                  | n     | base_rate | AUC    | Brier   | Sharpness | Veredicto                                                                    |
|------------|------------------------|-------|-----------|--------|---------|-----------|------------------------------------------------------------------------------|
| UNDER_3_5  | premier_2425           |  370  | 0.6486    | 0.464  | 0.24501 | 0.6640    | MODEL_DOES_NOT_DISCRIMINATE                                                  |
| UNDER_3_5  | top5_2425              | 1704  | 0.6796    | 0.5609 | 0.22926 | 0.6709    | MODEL_DISCRIMINATES_MODEST (weak vs market untestable here)                  |
| UNDER_3_5  | premier_multiseason    | 1480  | 0.6412    | 0.5319 | 0.24839 | 0.6409    | MODEL_DISCRIMINATES_WEAK                                                     |
| OVER_3_5   | premier_2425           |  370  | 0.3514    | 0.4819 | 0.24234 | 0.3355    | MODEL_DOES_NOT_DISCRIMINATE                                                  |
| OVER_3_5   | top5_2425              | 1704  | 0.3204    | 0.5523 | 0.22808 | 0.3300    | MODEL_DISCRIMINATES_MODEST                                                   |
| OVER_3_5   | premier_multiseason    | 1480  | 0.3588    | 0.5344 | 0.24600 | 0.3594    | MODEL_DISCRIMINATES_WEAK                                                     |

**Veredicto cerrado**: AUC entre 0.46 y 0.56 a lo largo de los 6 escenarios — la misma magnitud de discriminación pobre observada en 2.5 (D7-G: AUC 0.503–0.534, brier_modelo > brier_devig en TODOS los scopes). Por analogía estricta: el modelo es a lo sumo "weak/modest" vs ground truth, y el devig de la casa ha probado ser mejor calibrador en cada caso que hemos podido medirlo. **Cierre de mercados de goles en ligas — no se persigue 3.5 más adelante salvo que cambie la fuente de odds**.

Reporte consolidado: `/app/diagnostics/sprint_d8_phase1_summary.json`.

**Siguiente**: D8 Fase 2 (DRAW + cohorte favorito-dominante en selecciones).
