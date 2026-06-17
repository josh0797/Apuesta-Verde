# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA** + ✅ **F94.3 (Live Enrichment Persistence Audit) COMPLETADA** + ✅ **BUGFIX (Football “mismo momio” odds hallucination guard) COMPLETADO** + ✅ **SPRINT A (Draw Potential piloto retrospectivo) COMPLETADO** + ✅ **SPRINT B (Learning snapshots + loops + UI + scheduler) COMPLETADO** + ✅ **SPRINT D (Backtest histórico point-in-time; PL 23/24) COMPLETADO** + ✅ **SPRINT D2 (WC2022 + Euro2024 backtest nacional + Tournament Context) COMPLETADO** + ✅ **SPRINT D3 (Protected Markets: OVER 1.5 + Double Chance) COMPLETADO (P0)** + ✅ **SPRINT D4 (ROI honesto + significancia + walk-forward auditable) COMPLETADO (P0)** + 🟡 **REFACTOR-1 (data_ingestion top-2) EN PROGRESO (paso 1/3 completado)** + ⏳ **F84.c/F84.d (Lineups + Standings) PENDIENTE (P1)**.

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
- ✅ Reportes:
  - `/app/backtest_worldcup2022_draw.md/.json`
  - `/app/backtest_euro2024_draw.md/.json`
  - `/app/backtest_national_tournaments_summary.md`
- ✅ Suite backend (post D2): **3350 tests passing**, 0 regresiones.

---

## Phase SPRINT D3 — Backtest National Tournaments: OVER 1.5 + DOUBLE CHANCE (calibration-only) — COMPLETADO ✅

### Objetivo
Responder: **¿los mercados protegidos OVER 1.5 y DOUBLE CHANCE están mejor calibrados que DRAW en torneos de selecciones?**

### Alcance y restricciones
- **Modo:** `observe_only` + `calibration_only`.
- **NO ROI** (openfootball no trae odds confiables).
- **NO tocar producción ni ranking real.**
- **Point-in-time estricto:** `feature_date < match_date`.
- **Desglose obligatorio:** WC2022 / Euro2024 × (Group Stage / Knockout / Combined).

### Implementación realizada
- ✅ `services/football_over15_potential.py`:
  - Dixon-Coles bivariate Poisson con corrección tau y `rho=-0.13` (fail-soft).
- ✅ `services/football_double_chance_potential.py`:
  - ELO 1X2 + reutiliza `P(D)` desde `compute_draw_potential`.
  - Identidades matemáticas garantizadas (sum-to-one, HD/AD/HA).
- ✅ `services/football_backtest_engine.py`:
  - Multi-mercado en modo `no_market`: `DRAW`, `OVER_1_5`, `DOUBLE_CHANCE_HD/AD/HA`.
  - Thresholds por mercado en `NO_MARKET_THRESHOLDS` con ajuste empírico (D3.5).
- ✅ `services/football_backtest_metrics.py`:
  - `reliability_by_bucket` + `false_positive_examples` + `false_negative_examples`.
- ✅ Script runner:
  - `scripts/run_backtest_protected_markets.py` (genera reportes automáticamente).

### Exploración y thresholds (D3.5)
- ✅ Thresholds calibrados sobre muestra combinada WC22+Euro24 (n=87 predicciones por mercado), con “sweet spots” por tasa de acierto y tamaño mínimo de fired.

### Reportes generados
- ✅ `/app/backtest_worldcup2022_over15.md/.json`
- ✅ `/app/backtest_euro2024_over15.md/.json`
- ✅ `/app/backtest_worldcup2022_double_chance.md/.json`
- ✅ `/app/backtest_euro2024_double_chance.md/.json`
- ✅ `/app/backtest_protected_markets_summary.md`

### Conclusión
- ✅ **Euro 2024**: mercados protegidos (especialmente DC_HD/DC_AD) muestran calibración (Brier) **mejor que DRAW**.
- ✅ **WC 2022**: torneo atípicamente decisivo; el edge de calibración de mercados protegidos se reduce o invierte.

### Tests
- ✅ **93 tests nuevos** Sprint D3.
- ✅ Suite backend (post D3): **3443 passing tests**, 2 skipped, 0 regresiones.

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)

### Contexto (gaps detectados)
- **GAP 1:** El modo calibration-only reporta hit-rate/Brier, pero faltaba **ROI real** cuando hay odds.
- **GAP 2:** `walk_forward` existía pero faltaba un test/auditoría que pruebe anti-leakage de calibración.

### Objetivo
Cerrar gaps de honestidad estadística:
1. ROI real con odds históricas reales (no solo hit-rate)
2. Bootstrap CI + significancia
3. Walk-forward auditable (probar que no usa el futuro)
4. Warnings explícitos (small sample, closing odds, no odds)

### Entregado (D4.1–D4.8)

**D4.1 — Parser football-data.co.uk con odds** ✅
- Extendido `parse_football_data_csv`/`parse_footballdata_csv`:
  - `odds_type`: `OPENING|CLOSING|MIXED|NONE`
  - odds opening (`B365H/D/A`, `PSH/PSD/PSA`) y closing (`B365CH/CD/CA`, `PSCH/PSCD/PSCA`)
  - warning por fila: `ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC` cuando aplica
  - fail-soft: fila sin odds no se descarta

**D4.2 — Cliente The Odds API historical snapshots + caching** ✅
- Nuevo: `services/external_sources/the_odds_api_client.py`
- Cache local: `/tmp/the_odds_api_cache/`
- Fail-soft (retorna `None` en errores)

**D4.3 — Métricas ROI con CI + sample_status + warnings** ✅
- `football_backtest_metrics.py` ahora retorna:
  - `roi`, `yield_per_bet`, `net_pnl`, `total_staked`, `total_returned`
  - `roi_ci_low/high` (bootstrap) + `is_roi_significant = (roi_ci_low > 0)`
  - `sample_status`: `INSUFFICIENT_SAMPLE_DO_NOT_TRUST | SMALL_SAMPLE_CAUTION | ADEQUATE_SAMPLE`
  - `warnings` canónica (incluye `ROI_NOT_STATISTICALLY_SIGNIFICANT`, `NO_ODDS_HIT_RATE_ONLY`, `ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC`)

**D4.4 — Walk-forward auditable** ✅
- Engine registra por predicción:
  - `_calibration_audit`: `{n_calib_matches, n_calib_picks_seen, max_calib_date, target_date, leakage_check_passed, ...}`
- Invariante probada: `max_calib_date < target_date` para todas las predicciones

**D4.5 — Tests ROI + significancia** ✅
- Nuevo: `tests/test_sprint_d4_roi_significance.py` (25 tests)

**D4.6 — Tests walk-forward (no leakage)** ✅
- Nuevo: `tests/test_sprint_d4_walk_forward.py` (7 tests)

**D4.7 — Backtest real EPL 2024/25 con odds reales (football-data.co.uk)** ✅
- Dataset: 380 partidos.
- Config: DRAW, `min_edge=4pp`, walk-forward, calibración.
- Resultados:
  - **Opening odds:** N=120, ROI=+18.11%, CI95%=[-19.67%, +57.13%] → **NO significativo**, `SMALL_SAMPLE_CAUTION`.
  - **Closing odds:** N=120, ROI=+13.28% (≈5pp menos), CI95%=[-22.58%, +51.23%] → **NO significativo**, warning `ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC`.
  - Hallazgo: edge bucket 15pp+ pierde dinero (sobreconfianza en cola).
- Reportes:
  - `/app/backtest_epl_2425_draw_opening.md/.json`
  - `/app/backtest_epl_2425_draw_closing.json`
  - `/app/backtest_d4_summary.md`

**D4.8 — 0 regresiones** ✅
- Suite backend (post D4): **3475 passing tests**, 2 skipped, 0 regresiones.

### Conclusión D4
El framework ahora cumple la barra de honestidad estadística:
- Siempre reporta CI + sample_status + warnings.
- No declara “apto” si la muestra es chica o el CI cruza 0.
- Closing-odds warning explícito (backtest optimista).

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Componentes objetivo (por tamaño aproximado)
1. `_enrich_football` (≈ 458 LOC)
2. `ingest_upcoming` (≈ 274 LOC)

## Reglas estrictas
- **Solo refactorizar estos 2 componentes**.
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
- ⏳ **Ampliar backtest con odds reales a 5 ligas europeas (2024/25)** para alcanzar `ADEQUATE_SAMPLE` (≥200 picks) y reevaluar significancia.
- ⏳ **Backtest WC2022 con The Odds API historical** (cuando el usuario apruebe el gasto de cuota / quota). *(El cliente ya existe + caching, falta ejecución sistemática por timestamps pre-kickoff).* 

### Pendientes P1
- 🟡 **REFACTOR-1**: completar pasos 2/3 y 3/3 + refactor `ingest_upcoming`.
- ⏳ **F84.c/F84.d**: Lineups + Standings (TheStatsAPI) + UI + tests.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py` para clubes UCL/UEL.
- ⏳ Expandir backtest framework a otros mercados (BTTS / Over 2.5 / Corners) tras validar DRAW.

---

## 6) Validación esperada (estado actual)

- Suites actuales (post Sprint D4):
  - Backend: **3475 passing tests**, 2 skipped.
  - Frontend: **174 passing tests**.

- Reglas:
  - Cero regresión post-cada cambio lógico.
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft y back-compat.
  - Point-in-time correctness: prohibido usar datos futuros en backtests.
  - SPRINT D3/D4: `observe_only` (no tocar ranking real).

---

## Reglas operacionales + flags

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Mantener back-compat en contratos de respuesta cuando el FE dependa de fields legacy.
  - `Discovery != persistence` debe surfacearse como error técnico `LIVE_ENRICHMENT_DROPPED_FIXTURES`.
  - **Backtests:** disciplina point-in-time estricta (sin leakage); cualquier feature de contexto de torneo depende solo de partidos anteriores.

- Flags / env relevantes:
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
  - ✅ **MLB-F93:** `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
  - ✅ **MLB-F93:** `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).
  - ✅ **F94.2 / TheStatsAPI:** `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY` configurado.
  - ✅ **Sprint D4 / The Odds API:** `THE_ODDS_API_KEY=...` (provista por el usuario; almacenar como env, no hardcode).
