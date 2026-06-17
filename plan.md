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

# Phase SPRINT D7-E — Threshold parametrization + honest sweep + multi-season sanity check — COMPLETADO ✅ (P1)

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
Script: `scripts/run_backtest_d7_threshold_sweep.py` → `/app/backtest_d7_threshold_sweep.json`

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
- ✅ Script: `scripts/run_backtest_d7_premier_multiseason.py` → `/app/backtest_d7_premier_multiseason.json`

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
**No hay evidencia de edge real** en el módulo Draw Potential para mercado DRAW en ligas domésticas bajo este horizonte. El comportamiento es consistente con ruido (ROI no robusto al threshold, alta varianza inter-liga e inter-temporada).

## Artefactos (D7 + D7-E)
- `/app/backtest_d7_domestic_edge4.json`
- `/app/backtest_d7_domestic_edge3.json`
- `/app/backtest_d7_threshold_sweep.json`
- `/app/backtest_d7_premier_multiseason.json`
- `/app/data/openfootball/{wc2022,euro2024}.json`

## Próximos pasos sugeridos (pendientes de prioridad del usuario)
1. Pivotear a otros mercados/módulos (OVER_1_5, BTTS, DC) y repetir exactamente la misma disciplina de:
   - barrido de thresholds,
   - checks multi-liga,
   - checks multi-temporada,
   antes de consumir créditos históricos.
2. Mantener bloque nacional D7 aplazado (no gastar créditos) hasta tener una nueva tesis y/o caching de odds ya pagadas.

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
- ⏳ Evaluar pivote de mercado (OVER_1_5 / BTTS / DC) con disciplina D7-E (threshold sweep + multi-season).
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Expandir backtest framework a otros mercados tras validar robustez.

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
  - (D7) Flags CLI ya implementados:
    - `--min-edge-pp`
    - `--skip-national`
    - `--out`
