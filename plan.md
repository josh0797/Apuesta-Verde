# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA** + ✅ **F94.3 (Live Enrichment Persistence Audit) COMPLETADA** + ✅ **BUGFIX (Football “mismo momio” odds hallucination guard) COMPLETADO** + ✅ **SPRINT A (Draw Potential piloto retrospectivo) COMPLETADO** + ✅ **SPRINT B (Learning snapshots + loops + UI + scheduler) COMPLETADO** + ✅ **SPRINT D (Backtest histórico point-in-time; PL 23/24) COMPLETADO** + ✅ **SPRINT D2 (WC2022 + Euro2024 backtest nacional + Tournament Context) COMPLETADO** + 🟡 **REFACTOR-1 (data_ingestion top-2) EN PROGRESO (paso 1/3 completado)** + ⏳ **F84.c/F84.d (Lineups + Standings) PENDIENTE (P1)**.

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
- ✅ `services/football_historical_ingestor.py` (CSV football-data.co.uk + `build_point_in_time_features`)
- ✅ `services/football_backtest_engine.py`
- ✅ `services/football_backtest_metrics.py`
- ✅ `scripts/run_backtest.py`
- ✅ Ejecución validada en Premier League 23/24

---

## Phase SPRINT D2 — Backtest histórico en torneos nacionales (WC2022 + Euro2024) — COMPLETADO ✅

### Objetivo
Validar si el módulo **Draw Potential** mejora en torneos nacionales (donde incentivos cooperativos y contextos de grupo son más fuertes) manteniendo disciplina point-in-time.

### Decisiones confirmadas por el usuario (fijas)
1) **`TOURNAMENT_CONTEXT_SCORE`**
- Doble:
  - Escalar **0.0–1.0** (auditoría)
  - + **booster suave** conservador (máximo +2pp a +3pp)
- Booster:
  - Se activa cuando `score >= 0.6`
  - Escala lineal: `0.6 → +2pp`, `1.0 → +3pp` (clamp)

2) **Métricas (sin odds)**
- Reportar:
  - **Brier Score**
  - **Log-loss**
  - **Calibration curve**
  - **Hit-rate** del label `VALUE_DRAW_CANDIDATE` (y `STRONG_VALUE_DRAW`)
- **No** reportar ROI/yield (openfootball no incluye odds)

3) **Cobertura de fases**
- Incluir **todo el torneo** y desglosar:
  - **Group Stage**
  - **Knockout**
  - **Combined**

4) **Regla crítica**
- **Point-in-time estricto**: `TOURNAMENT_CONTEXT_SCORE` usa únicamente partidos **anteriores** del mismo torneo/grupo (sin futuro; sin cruces de fixtures simultáneos).

---

### Implementación realizada (SPRINT D2)

#### D2.1 — Parser openfootball JSON → schema canónico (ingestor) ✅
**Archivo:** `services/football_historical_ingestor.py`
- ✅ `parse_openfootball_json(data, competition=...) -> list[dict]`
- ✅ Clasificación de fase desde `round`:
  - Group: `Matchday N`/`Group X`
  - Knockout: `Round of 16`, `Quarter(-final)`, `Semi(-final)`, `Final`, etc.
- ✅ `odd_* = None` (no market)

#### D2.2 — Standings point-in-time por grupo ✅
**Archivo:** `services/football_historical_ingestor.py`
- ✅ `compute_group_standings_pit(matches_sorted, target_index, ...)` con anti-leakage estricto (`m.date < target_date`, misma `competition` y `group_label`).
- ✅ Derivación de **group matchday real** desde PIT standings: `group_matchday = max(played_home, played_away)+1` (no depender del `Matchday` global del JSON openfootball).

#### D2.3 — `TOURNAMENT_CONTEXT_SCORE` + booster conservador ✅
**Archivos:**
- `services/football_tournament_context.py` (nuevo)
- `services/football_draw_potential.py` (extendido)

- ✅ `compute_tournament_context_score()` → `{score_0_1, boost_pp, reason_codes, audit}`
- ✅ Booster aplicado en `compute_draw_potential(tournament_context_score=...)` (+2..+3pp máx, solo si score ≥ 0.6)

#### D2.4 — Backtest “no-market” + métricas ✅
**Archivos:**
- `services/football_backtest_engine.py`
- `services/football_backtest_metrics.py`

- ✅ `run_backtest(..., no_market=True)`:
  - genera `predictions[]` (para calibración sobre muestra completa)
  - genera `picks[]` solo cuando `draw_probability >= min_pred_prob_pp`
  - **re-labeling** sin odds: thresholds absolutos (FAIR ≥ 24pp, VALUE ≥ 28pp, STRONG ≥ 32pp)
- ✅ Métricas no-market:
  - Brier score, log-loss, curva de calibración, base-rate
  - hit-rate por label
  - desglose Group / Knockout / Combined

#### D2.5 — CLI runner actualizado ✅
**Archivo:** `scripts/run_backtest.py`
- ✅ Soporta `--openfootball-path` y `--no-market`
- ✅ Soporta `--min-pred-prob-pp`
- ✅ Render markdown específico no-market (sin ROI)

#### D2.6 — Ejecución de backtests + reportes ✅
- ✅ World Cup 2022:
  - `/app/backtest_worldcup2022_draw.json`
  - `/app/backtest_worldcup2022_draw.md`
- ✅ Euro 2024:
  - `/app/backtest_euro2024_draw.json`
  - `/app/backtest_euro2024_draw.md`
- ✅ Comparativo:
  - `/app/backtest_national_tournaments_summary.md`

**Resultados clave (resumen):**
- **WC 2022** (Group Stage): base-rate de draw **15.6%** (torneo atípicamente decisivo), `hit_rate_fired` ≈ **14.3%** (muestra pequeña)
- **Euro 2024** (Group Stage): base-rate de draw **54.2%** (torneo excepcionalmente cooperativo), `hit_rate_fired` ≈ **41.2%**, `STRONG_VALUE_DRAW` **70% hit-rate** en grupo
- **Combinado**: 48 picks fired, **33% hit-rate** (sobre baseline histórico ~24%)
- **Conclusión:** hipótesis parcialmente validada (fuerte en Euro24, débil en WC22); requiere más muestra.
- **Verdict operativo:** **NO desplegar** aún; `small_sample_flag=True`.

#### D2.7 — Tests + cero regresiones ✅
- ✅ 79 tests nuevos (Sprint D2):
  - parser openfootball
  - standings PIT anti-leakage
  - tournament context score + booster
  - engine no-market + métricas
- ✅ Suite global: **3350 passing**, 2 skipped, 0 regresiones
- ✅ Frontend: sin cambios (174 passing)

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Componentes objetivo (por tamaño aproximado)
1. `_enrich_football` (≈ 458 LOC)
2. `ingest_upcoming` (≈ 274 LOC)

## Reglas estrictas
- **Solo refactorizar estos 2 componentes** (instrucción explícita del usuario).
- Mantener firmas públicas **exactas**: `ingest_upcoming`, `_enrich_football`.
- Cero cambios en endpoints / contratos JSON.
- Extraer helpers cohesivos y puros, sin alterar side-effects.
- Ejecutar `pytest` tras cada extracción (y `yarn craco test` si hay cambios FE).

## Progreso actual
- ✅ Paso 1/3 completado: extracción de **odds cascade** a helper:
  - `services/_ingestion_helpers/football_odds_cascade.py`
  - Integrado en `data_ingestion._enrich_football`.
  - Validado con tests (subset + suite completa).

## Pendiente (pasos restantes)
- ⏳ Paso 2/3: extraer **deep enrichment** (team stats + h2h + injuries + recent fixtures) sin cambios de comportamiento.
- ⏳ Paso 3/3: extraer **live stats hydration** (API-Sports fixture_statistics + merge TheStatsAPI match_stats) sin cambios de comportamiento.
- ⏳ Refactor `ingest_upcoming` (2º componente más grande) con misma política.

---

# Phase FIX-3 — Tail Fragility polarity guard (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase FIX-1 — xG TheStatsAPI normalisation (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase FIX-2 — Corners TheStatsAPI normalisation (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳

## Objetivo
Añadir cobertura de:
- **F84.c Lineups**: XI inicial + banca (+ injuries breve si la fuente lo permite).
- **F84.d Standings**: tabla de posiciones filtrable por liga.

## Principios
- Fail-soft: nunca bloquear el análisis/picks; devolver `AVAILABLE | PENDING | UNAVAILABLE` con razones.
- Back-compat: no romper consumers existentes; estos endpoints son aditivos.

## Backend
### Nuevos adaptadores (TheStatsAPI)
- `services/external_sources/thestatsapi_lineups.py` (nuevo)
- `services/external_sources/thestatsapi_standings.py` (nuevo)

### Endpoints
- `GET /api/football/match/{match_id}/lineups`
- `GET /api/football/league/{league_id}/standings`

### Contratos (borrador)
- Lineups:
  - `status: AVAILABLE|PENDING|UNAVAILABLE`
  - `reason_code` + `reason_detail`
  - `home`, `away`:
    - `starting_xi[]`, `bench[]`, `coach` (si existe), `injuries[]` (si existe)
  - `source: thestatsapi|api_sports|none`
- Standings:
  - `status: AVAILABLE|PENDING|UNAVAILABLE`
  - `league_id`, `league_name`, `season`
  - `table[]`: `{rank, team, played, won, draw, lost, gf, ga, gd, points, form?}`
  - `source` + `provenance`

## Frontend
- `LineupsPanel.jsx`:
  - Render XI + banca
  - Indicadores de missing data (PENDING/UNAVAILABLE)
- `StandingsPanel.jsx`:
  - Tabla compacta, resaltado de equipos del partido
- Integración:
  - `MatchDetailPage.jsx` (preferido) y/o sección expandible desde la card.

## Tests
- Backend:
  - unit tests de normalización del payload por adaptador
  - endpoint tests con mocking de respuestas HTTP
- Frontend:
  - render states (AVAILABLE/PENDING/UNAVAILABLE)
  - snapshot/queries para filas de tabla y secciones de XI

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ **SPRINT D2** (completado). Próximos P0 derivados:
  - ⏳ Backtest **Copa América 2024** (mismo engine no-market) para aumentar muestra.
  - ⏳ Backtest **AFCON 2024**.
  - ⏳ Sensitivity analysis de `min_pred_prob_pp` (28 → 30 → 32) y comparación por fase.
  - ⏳ Conseguir odds históricas WC22/Euro24 (Pinnacle/Betfair) y re-ejecutar en modo market para ROI real.

### Pendientes P1
- 🟡 **REFACTOR-1**: completar pasos 2/3 y 3/3 + refactor `ingest_upcoming`.
- ⏳ **F84.c/F84.d**: Lineups + Standings (TheStatsAPI) + UI + tests.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py` para clubes UCL/UEL.
- ⏳ Expandir backtest framework a otros mercados (BTTS / Over 2.5 / Corners) tras validar DRAW.

---

## 6) Validación esperada (estado actual)

- Suites actuales (post Sprint D2):
  - Backend: **3350 passing tests**, 2 skipped.
  - Frontend: **174 passing tests**.

- Reglas:
  - Cero regresión post-cada cambio lógico.
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft y back-compat.
  - Point-in-time correctness: prohibido usar datos futuros en backtests.

---

## Reglas operacionales + flags

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Mantener back-compat en contratos de respuesta cuando el FE dependa de fields legacy.
  - `Discovery != persistence` debe surfacearse como error técnico `LIVE_ENRICHMENT_DROPPED_FIXTURES`.
  - **Backtests:** disciplina point-in-time estricta (sin leakage); cualquier feature de contexto de torneo debe depender solo de partidos anteriores.

- Flags / env relevantes:
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
  - ✅ **MLB-F93:** `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
  - ✅ **MLB-F93:** `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).
  - ✅ **F94.2 / TheStatsAPI:** `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY` configurado.

