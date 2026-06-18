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
- ✅ `scripts/run_backtest_d7_threshold_sweep.py` ahora acepta `--market {DRAW, OVER_2_5, UNDER_2_5}` y escribe por defecto `..._<market>.json`.
- ✅ `scripts/run_backtest_d7_premier_multiseason.py` ahora acepta `--market` y escribe por defecto `..._<market>.json`.

### F5 — Tests
- ✅ Nuevo archivo: `tests/test_sprint_d7_phaseF_score_grid_markets.py` (16 tests).

### F6 — Ejecución de barridos (artefactos)
Artefactos (D7-F):
- `/app/backtest_d7_threshold_sweep_over_2_5.json`
- `/app/backtest_d7_threshold_sweep_under_2_5.json`
- `/app/backtest_d7_premier_multiseason_over_2_5.json`
- `/app/backtest_d7_premier_multiseason_under_2_5.json`

### F7 — Validación
- ✅ `pytest` backend completo: **3650 passing**, 2 skipped, 0 regresiones.

## Veredicto científico (D7-F)
- OVER_2_5 y UNDER_2_5: sesgo negativo estable; sin edge demostrable.

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
- 🟡 **SPRINT D9.3-A** (hotfix MLB): corregir “Contexto de serie activa” (impacta proyección y UI).

### Pendientes P1
- 🟡 **SPRINT D9.2 — Block C** Residual Model con xG real (después de D9.3).
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.
- ⏳ **D8 Fase 2** — selecciones (DRAW + cohorte favorito-dominante) con MAX_CREDITS=2500 (bloqueado por ground truth Copa América 2024).

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
  - `observe_only` (sin apuestas automáticas).
  - Backend: ejecutar `pytest` completo tras cambios.

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **Observe-only**: no implementar apuestas automáticas.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.

---

## SPRINT F — Ingesta de Tendencias Top desde 365Scores — COMPLETADO ✅

Sin cambios (ver bitácora previa).

---

## SPRINT D8 — UNDER_3_5 (ligas) + DRAW/cohorte (selecciones)

### D8 Fase 1 — UNDER_3_5 / OVER_3_5 en ligas (CERRADA)
Sin cambios (ver bitácora previa).

---

## SPRINT D9.2 — Block 0 + A + B (COMPLETADO ✅)

Sin cambios (Block 0: UI manual odds; Block A: corners friendlies; Block B: xG real cascada + cache + features).

---

## SPRINT D9.3 — Active Series Context Fix + Expansion (P0 hotfix)

### Contexto / Bug visible en producción
Captura del usuario (Texas Rangers @ Minnesota Twins, Joe Ryan vs Jack Leiter):
- "Familiaridad de serie: media (56/100) — 3d:2, 5d:2, 15d:2" → **correcto**.
- "Contexto de serie activa (G2): G1 Texas Rangers 0 - 0 Minnesota Twins = 0 carreras. Promedio: 0.0 carreras · Over rate: 0%" → **bug**.
- "Segundo juego: ER ajustado 7.2 → 7.9 (+0.7)" → **consecuencia del bug** (contamina proyección).

### Causa raíz (confirmada en código)
`services/mlb_active_series_analyzer.py`:
- `_extract_runs` usa `path.get("home", 0)` / `path.get("away", 0)` y similares; cuando faltan keys, produce **0** y lo interpreta como score válido.
- No hay filtro estricto de `status` final.
- Resultado: `runs_list` incluye un juego fantasma 0 carreras → `next_game_number=2` → `apply_series_degradation` suma +0.4..+0.8 ER → proyección contaminada.

### Plan en fases
- **D9.3-A (hotfix, prioridad P0):** validación estricta de marcadores válidos + estados + UI honesta.
- **D9.3-B (señal matemática):** weighted runs, shrinkage, CV, over/under line-aware.
- **D9.3-C (interacciones):** slope, pitching/bullpen delta, anti-double-counting con familiaridad H2H.

### Sub-fase D9.3-A — Validación + estados + UI honesta (CERRADO ✅ 2026-06-18)

#### Resultado de cierre
- ✅ `mlb_active_series_analyzer.py` reescrito:
  - `_parse_int_strict` y `_read_scores_strict` eliminan defaults a 0 → keys faltantes ahora producen `None`.
  - `_doc_status` + `_is_status_final` clasifican explícitamente `FINAL/COMPLETED/GAME_OVER/...` vs `POSTPONED/SUSPENDED/LIVE/...`.
  - Guard MLB 0-0: excluye partidos 0-0 a menos que `score_confirmed=True`.
  - Estados `series_state` expuestos: `ACTIVE_SERIES_CONFIRMED`, `ACTIVE_SERIES_NO_COMPLETED_GAMES`, `ACTIVE_SERIES_SCORE_MISSING`, `ACTIVE_SERIES_UNRESOLVED`.
  - Auditoría `excluded_docs[]` por partido descartado (con `reason`, `status`, scores).
  - `reason_codes` siempre presentes (incl. `LIMITED_SAMPLE_SERIES_SIGNAL` cuando n<3).
  - Conteo line-aware: `over_count`, `under_count`, `push_count` + `reference_line`. `over_rate` mantenido por back-compat.
- ✅ `frontend/MLBScriptPanel.jsx` actualizado:
  - Estado degradado: "La serie actual todavía no tiene partidos finalizados." (sin promedio falso ni Over rate 0%).
  - Estado confirmado: "Promedio · Partidos válidos: N · Over {line}: X de N · Under {line}: Y de N".
  - Badge "Muestra limitada — señal contextual, no concluyente" cuando n<3.
  - `data-testid` adicionales para QA: `-series-state-badge`, `-series-empty-message`, `-series-line-counts`, `-series-limited-sample`, `-series-avg`, `-series-games-list`, `-series-game-{n}`.
- ✅ Tests: nuevo `backend/tests/test_mlb_active_series_analyzer.py` con **15 tests** cubriendo:
  - Bug regression `final_score={}` → `SCORE_MISSING` (no fabrica G1 0-0).
  - `final_score={"home":None,"away":None}` → `SCORE_MISSING`.
  - Suspicious 0-0 sin confirmar → excluido + `SUSPICIOUS_ZERO_ZERO_EXCLUDED`.
  - 0-0 con `score_confirmed=True` → aceptado.
  - `status=Postponed` → excluido.
  - `status=Live` → excluido.
  - Status missing + scores válidos → soft-final.
  - Happy path 2 juegos confirmados con over/under counts correctos.
  - Reorientación home/away cuando el doc tiene equipos invertidos.
  - `live_stats.score = {home, away}` shape soportado (back-compat).
  - Sin matchup en ventana → `NO_COMPLETED_GAMES`.
  - `db=None` → `UNRESOLVED`.
  - n≥3 → no emite `LIMITED_SAMPLE_SERIES_SIGNAL`.
  - Mix válido + inválido → solo los válidos cuentan, los demás aparecen en `excluded_docs`.
  - High-scoring series → triggers `series_override=True` + `lean=OVER`.
- ✅ Pytest backend completo: **3806 passed / 2 skipped** (3791 base + 15 nuevos), 0 regresiones.
- ✅ Build FE (`esbuild MLBScriptPanel.jsx`) clean, sin errores.

#### Efecto sobre el bug visible
Para el caso Texas Rangers @ Minnesota Twins (Joe Ryan vs Jack Leiter):
- Antes: G1 fantasma 0-0 → `games_in_series=1` → `apply_series_degradation(g=2)` sumaba +0.7 ER (7.2 → 7.9).
- Ahora: el doc con `final_score={}` (o sin scores válidos) cae en `SCORE_MISSING` → `games_in_series=0` → guard `if base_er and series_ctx.get("games_in_series", 0) >= 1` en `mlb_day_orchestrator.py:3273` no activa la degradación → ER permanece intacta.
- UI: muestra "La serie actual todavía no tiene partidos finalizados." en vez de "G1: 0-0, Promedio 0.0, Over rate 0%".

### Sub-fase D9.3-B — Señal matemática weighted + shrinkage + CV (PENDIENTE)
- Implementar `calculate_series_total_signal(current_expected_runs, market_total, active_series_games, recent_h2h_games, starting_pitching_projection, bullpen_projection)`.
- Weighted runs:
  - pesos por recencia: 1.00 / 0.75 / 0.55 / 0.40 / 0.30.
  - peso de juegos de serie activa: 1.0.
  - peso de H2H de series previas: 0.45.
- Shrinkage:
  - `series_reliability = n/(n+3)`.
  - cap de influencia: máx 30% de la proyección.
  - clamp de ajuste: [-1.25, +1.25].
- `series_edge_runs = adjusted_expected_runs - market_total` con bandas interpretables.
- Métricas: mean, median, std, min, max, CV.

### Sub-fase D9.3-C — Interacciones pitching/bullpen + slope + anti-double-counting (PENDIENTE)
- Tendencia:
  - `series_slope = linear_regression_slope(game_number, total_runs)`.
  - Guard: `INSUFFICIENT_SAMPLE_FOR_SERIES_TREND` si n<3.
- Interacciones:
  - `pitching_delta`, `bullpen_delta` y reason codes explicativos.
- Evitar doble conteo:
  - si hay overlap `active_series_games` vs `recent_h2h_games` → `do_not_double_count=True`.

---

## SPRINT D9.2 — Block C: Residual Model con xG real (CONFIRMADO, PENDIENTE)

### Decisión usuario (confirmada)
- Estrategia: **cache-first agresivo** (fetch 1× por equipo+temporada en Mongo `football_team_xg_history`), y **PIT-filter en memoria** por `match_date < target_date`.
- Scope: **top5_2425** únicamente.
- Cobertura parcial: añadir feature `xg_real_available` (0/1).
- Criterio Bonferroni estricto: **AUC > 0.55 ∧ delta_brier < 0 ∧ roi_ci_low > 0**.

### Plan (a ejecutar después de D9.3)
1) Añadir wrapper PIT-safe:
   - Dado `team_xg_history.matches[]` (fecha,xG), filtrar **solo** `dt < match_dt` antes de `compute_xg_features_l15`.
2) Expandir `FEATURE_NAMES` en `backend/scripts/run_d9_residual_backtest.py`:
   - `xg_l15_mean`, `xg_l15_std`, `xg_l15_dispersion`, `xg_real_available`.
3) Modificar `_gather_records`:
   - hidratar xG real por equipo+temporada (cache-first),
   - calcular features PIT por cada match,
   - mantener fail-soft (si no hay xG: features None + flag 0).
4) Reportar coverage en `train_audit`:
   - % de filas con `xg_real_available=1` (train/holdout).
5) Clasificación/veredicto:
   - aplicar criterio Bonferroni (nuevo tag `NO_INCREMENTAL_SIGNAL_WITH_XG_REAL` si falla).
6) Ejecutar backtest:
   - generar `/app/diagnostics/residual_d9_2c_over_2_5_top5_2425.json`.
7) Tests:
   - wrapper PIT-filter (no usa partidos futuros),
   - coverage flag,
   - criterio Bonferroni aplicado correctamente.
