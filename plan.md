# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA** + ✅ **F94.3 (Live Enrichment Persistence Audit) COMPLETADA** + ✅ **BUGFIX (Football “mismo momio” odds hallucination guard) COMPLETADO** + ✅ **SPRINT A (Draw Potential piloto retrospectivo) COMPLETADO** + ✅ **SPRINT B (Learning snapshots + loops + UI + scheduler) COMPLETADO** + ✅ **SPRINT D (Backtest histórico point-in-time; PL 23/24) COMPLETADO** + ✅ **SPRINT D2 (WC2022 + Euro2024 backtest nacional + Tournament Context) COMPLETADO** + ✅ **SPRINT D3 (Protected Markets: OVER 1.5 + Double Chance) COMPLETADO (P0)** + ✅ **SPRINT D4 (ROI honesto + significancia + walk-forward auditable) COMPLETADO (P0)** + 🟡 **SPRINT D5 (Multi-league + multi-tournament DRAW + cohortes) EN PROGRESO (P0)** + ✅ **SPRINT E.1 (Live Odds Monitor Base — Observe-only) COMPLETADO (P0)** + ✅ **SPRINT E.1.1 (Resolver identidad de mercado por The Odds API) COMPLETADO (P0)** + 🟡 **REFACTOR-1 (data_ingestion top-2) EN PROGRESO (paso 1/3 completado)** + ⏳ **SPRINT E.2 (Odds Value Detector + Alerts) PENDIENTE (P0)** + ⏳ **SPRINT E.3 (UI Odds Alerts + Comparador Manual) PENDIENTE (P0)** + ⏳ **F84.c/F84.d (Lineups + Standings) PENDIENTE (P1)**.

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
  - `scripts/run_backtest_protected_markets.py`.

### Reportes generados
- ✅ `/app/backtest_worldcup2022_over15.md/.json`
- ✅ `/app/backtest_euro2024_over15.md/.json`
- ✅ `/app/backtest_worldcup2022_double_chance.md/.json`
- ✅ `/app/backtest_euro2024_double_chance.md/.json`
- ✅ `/app/backtest_protected_markets_summary.md`

### Tests
- ✅ Suite backend (post D3): **3443 passing tests**, 0 regresiones.

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)

### Objetivo
Cerrar gaps de honestidad estadística:
1. ROI real con odds históricas reales (no solo hit-rate)
2. Bootstrap CI + significancia (`is_roi_significant = ci_low > 0`)
3. Walk-forward auditable (probar que no usa el futuro)
4. Warnings explícitos (small sample, closing odds, no odds)

### Entregado
- ✅ Parser football-data.co.uk: opening/closing odds + warnings.
- ✅ Cliente The Odds API historical snapshots + caching.
- ✅ Métricas ROI: `roi_ci_low/high`, `sample_status`, `warnings`.
- ✅ `_calibration_audit` en engine + tests no-leakage.
- ✅ Backtest real EPL 24/25:
  - Opening: N=120, ROI=+18.11%, CI cruza 0 → NO significativo.
  - Closing: ROI=+13.28% + warning closing.
- ✅ Suite backend (post D4): **3475 passing tests**, 0 regresiones.

---

## Phase SPRINT D5 — Multi-league + multi-tournament DRAW + cohortes (observe_only) — EN PROGRESO 🟡 (P0)

### Objetivo
1) **Ampliar backtest DRAW** a 5 ligas europeas 2024/25 usando **opening odds** cuando existan:
- Premier League (E0)
- La Liga (SP1)
- Serie A (I1)
- Bundesliga (D1)
- Ligue 1 (F1)

2) Ejecutar backtest DRAW en torneos de selecciones:
- **World Cup 2018**
- **World Cup 2022**
- **Euro 2024** (ya parseado)

3) Reportar separado:
- `domestic_leagues_summary`
- `national_tournaments_summary`
- `combined_comparison`

4) Buscar patrones “España vs Cabo Verde” (arquetipo):
- favorito dominante
- underdog defensivo
- empate con cuota inflada
- fase de grupos
- bajo ritmo esperado
- **draw_probability_model − market_implied ≥ 8pp**

5) Identificar cohortes:
- `DOMINANT_FAVORITE_DRAW_VALUE`
- `TOURNAMENT_GROUP_STAGE_DRAW_VALUE`
- `LOW_GOAL_UNDERDOG_BLOCK`
- `TAIL_EDGE_OVERCONFIDENCE_15PP_PLUS`

6) Mantener **observe_only**:
- No activar picks reales
- No tocar producción

### Alcance/Notas de datos
- **Ligas domésticas:** `football-data.co.uk` con odds de apertura (preferir B365H/D/A o PSH/PSD/PSA; si solo closing, marcar warning y permitir). Aquí sí se reporta **ROI + CI**.
- **Torneos nacionales:** openfootball JSON no trae odds → modo `no_market` con **Brier + calibration + hit-rate**.

### Subtareas
- **D5.1** — Descargar y parsear los CSV 24/25 para las 5 ligas (E0/SP1/I1/D1/F1).
- **D5.2** — Descargar y parsear openfootball: WC2018, WC2022, Euro2024.
- **D5.3** — Implementar `services/football_cohort_detector.py`:
  - Features: favorito dominante (ELO delta), underdog defensivo (xG proxy bajo + GA bajo), group_stage flag, low-goal environment proxy.
  - Clasificación por cohorte (puede asignar múltiples tags).
  - Extraer ejemplos top-N por edge (≥8pp) y por edge tail (≥15pp).
- **D5.4** — Script runner `scripts/run_backtest_multi_league.py`:
  - Corre DRAW en 5 ligas con opening odds.
  - Produce por liga `.json/.md` y un `domestic_leagues_summary.md`.
- **D5.5** — Script runner `scripts/run_backtest_tournaments_d5.py`:
  - Corre DRAW en WC2018/WC2022/Euro2024 en modo no-market.
  - Produce por torneo `.json/.md` y `national_tournaments_summary.md`.
- **D5.6** — `combined_comparison.md`:
  - Comparar domestic vs national: calibración, colas de edge, cohorts.
  - Responder si Draw Potential funciona mejor en torneos de selecciones.
- **D5.7** — Tests nuevos + 0 regresiones.

### Criterios de aceptación
- Reportes generados y versionados:
  - `/app/domestic_leagues_summary.md`
  - `/app/national_tournaments_summary.md`
  - `/app/combined_comparison.md`
- Cohortes detectadas con ejemplos:
  - Top 10 por cohorte
  - Falsos positivos/negativos por cohorte
- Flags honestos:
  - `sample_status` + `warnings` en ligas
  - `INSUFFICIENT_SAMPLE_DO_NOT_TRUST` en torneos si aplica

---

## Phase SPRINT E.1 — Live Odds Monitor (Base) + persistencia `odds_snapshots` (observe_only) — COMPLETADO ✅ (P0)

### Contexto y restricción crítica
- **observe_only estricto**: no se implementa ni dispara ningún flujo de apuestas.
- **Fail-soft**: cualquier fallo de API/red/parsing → log + continuar; el scheduler no debe caerse.
- **Rate-limit safety**: respetar cuota de The Odds API.
- **No polling global por deporte**: el universo es **solo** los partidos **visibles/recomendados** en el último payload que llega a la UI.

### Decisiones confirmadas (implementadas)
- **TTL**: **SIN TTL** en `odds_snapshots` (conservar todo para histórico/backtesting).
- **Deportes** (E.1): solo soccer + Mundial (scope: WC + WCQ) + Champions/Europa.
- **Polling default**: `LIVE_ODDS_REFRESH_SECONDS=240` (configurable por ENV).
- **Universo**: visible/recommended matches from latest pick_run payload.
  - Fuente primaria: `db.pick_runs`.
  - Fallback: `db.picks`.
- **Mapeo `match_id → The Odds API event_id`**: cache persistente en `odds_event_id_mappings`.
  - Si no hay `event_id`: reason `ODDS_EVENT_ID_MISSING`, no bloquear.
- **Colección `odds_snapshots`**: reutilizada con discriminador `source="live_odds_monitor_v1"`.

### Entregables (verificados)
1) **Cliente The Odds API (live/current)**
   - ✅ `services/external_sources/the_odds_api_client.py` extendido con:
     - `fetch_events()`
     - `fetch_current_odds()`
     - `_extract_quota_headers()`
   - ✅ Fail-soft (nunca levanta, devuelve `None` en fallos).

2) **Servicio `services/live_odds_monitor.py`**
   - ✅ Creado con:
     - `extract_visible_universe` (puro)
     - `collect_visible_universe`
     - `find_event_in_list` (puro; fuzzy por substring + token-overlap)
     - `resolve_event_id` (usa cache `odds_event_id_mappings`)
     - `event_payload_to_snapshots` (puro)
     - `persist_snapshots`, `run_cycle`, `register_jobs`
     - `get_config`, `get_status`
   - ✅ Restricción de universo: no se consulta global por deporte.

3) **MongoDB (índices)**
   - ✅ `server.py` startup:
     - `odds_event_id_mappings`: unique `(match_id, sport_key)` + index `event_id`.
     - `odds_snapshots`: index `(source, snapshot_at)` y `(match_id, source, snapshot_at)`.
     - **Sin TTL** para `odds_snapshots`.

4) **Scheduler**
   - ✅ `services/scheduler.py`: wiring del job via `live_odds_monitor.register_jobs(...)`.
   - ✅ Kill-switch: cuando `LIVE_ODDS_ENABLED=false`, log: *"not registering job (disabled)"*.

5) **Endpoints (read-only)**
   - ✅ `GET /api/odds/snapshots/{match_id}` (filtra por `source` por defecto).
   - ✅ `GET /api/odds/monitor/status` (config + status; sin side effects).

6) **Tests**
   - ✅ `tests/test_live_odds_monitor.py` (28 tests).
   - ✅ Suite backend completa (post E.1): **3526 passed, 2 skipped, 0 regresiones**.

### Variables de entorno (flags)
- `LIVE_ODDS_ENABLED=true|false`
- `LIVE_ODDS_REFRESH_SECONDS=240`
- `LIVE_ODDS_SPORTS=...` (default: 11 soccer keys)
- `LIVE_ODDS_MARKETS=h2h,totals`
- `LIVE_ODDS_REGIONS=uk,eu`
- `LIVE_ODDS_LOOKBACK_HOURS=24`
- `LIVE_ODDS_MAX_MATCHES=80`
- `LIVE_ODDS_QUOTA_MIN=50`

### Criterios de aceptación
- ✅ Se guardan snapshots en `odds_snapshots` con `source="live_odds_monitor_v1"`.
- ✅ Existe cache persistente `match_id → event_id`.
- ✅ Polling limitado al universo visible del último run.
- ✅ Endpoints funcionan y son fail-soft.
- ✅ Suite backend verde.

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)

### Problema
Cuando el engine detecta una cuota (ej. `detected_price=1.25`) pero el pick termina como `REQUIRES_MARKET_IDENTIFICATION`, el sistema no podía mapear esa cuota a un mercado concreto (DNB, 1X2, Over/Under, BTTS, hándicap, etc.).

**Nueva regla:** usar **The Odds API** como **resolver principal** de identidad de mercado. SportyTrader queda desactivado como resolver (se conserva el módulo por compatibilidad/posible reuso; su reemplazo editorial se planifica aparte).

### Decisiones confirmadas (implementadas)
- Resolver principal: **The Odds API**.
- Ejecución: **manual retry endpoint** ya implementado; el hook automático queda para la fase E.1.1-d (pendiente).
- Tolerancia + confianza:
  - HIGH `<= 0.02`
  - MEDIUM `<= 0.03`
  - LOW `<= 0.05`
- Markets evaluados (orden):
  1) `h2h`
  2) `draw_no_bet`
  3) `totals`
  4) `alternate_totals`
  5) `spreads`
  6) `alternate_spreads`
  7) `btts`
  8) `team_totals` (best-effort)
- Ambigüedad: **devolver todos los candidatos**, mantener `REQUIRES_MARKET_IDENTIFICATION` y permitir selección manual en UI (parte E.3).
- Persistencia obligatoria: `market_identity_resolutions` (auditoría + reuso cache).

### Entregables (verificados)
1) ✅ `services/market_identity_resolver.py` (nuevo)
   - Ladder HIGH/MEDIUM/LOW con redondeo a 4 decimales (evita ruido float).
   - Mapeo The Odds API → `MANUAL_MARKET_TYPES`:
     - `h2h → MATCH_WINNER (HOME/DRAW/AWAY)`
     - `draw_no_bet → DNB (HOME/AWAY)`
     - `totals/alternate_totals → TOTAL_GOALS (OVER/UNDER, line=point)`
     - `spreads/alternate_spreads → ASIAN_HANDICAP (HOME/AWAY, line=point)`
     - `btts → BTTS (YES/NO)`
     - `team_totals → TOTAL_GOALS` con `scope=team_totals` + `team_hint` (best-effort)
   - `extract_candidates_from_event` (puro): filtra por tolerancia y ordena por `(delta asc, market_priority asc)`.
   - `summarise_candidates` (puro): `RESOLVED | AMBIGUOUS | NOT_FOUND`.
   - `resolve_market_identity` (async):
     - cache en `market_identity_resolutions` (TTL por env, default 6h)
     - reusa `odds_event_id_mappings` vía `live_odds_monitor.resolve_event_id`
     - fail-soft total
   - Reason codes:
     - `MARKET_IDENTITY_RESOLVED_BY_THE_ODDS_API`
     - `MARKET_IDENTITY_AMBIGUOUS_REQUIRES_USER_CHOICE`
     - `ODDS_EVENT_ID_MISSING`
     - `FETCH_CURRENT_ODDS_FAILED`
     - `NO_CANDIDATE_WITHIN_TOLERANCE`
     - `DETECTED_PRICE_INVALID`, etc.

2) ✅ MongoDB (índices)
   - `market_identity_resolutions`:
     - index `(match_id, detected_price, resolved_at desc)`
     - index `resolved_at`
     - index `event_id`

3) ✅ Endpoints REST
   - `POST /api/football/market-identity/resolve` (manual retry)
   - `GET  /api/football/market-identity/history/{match_id}` (audit trail)

4) ✅ Validación real
   - Probado end-to-end con un caso real (Portugal vs Congo DR):
     - `event_id` resuelto con The Odds API
     - respuesta fail-soft si odds no disponibles
     - persistencia del intento en `market_identity_resolutions`

5) ✅ Tests
   - `tests/test_market_identity_resolver.py`: 28 tests (puros + async + cache + ambigüedad + fail-soft).

6) ✅ Suite backend
   - **3554 passed, 2 skipped, 0 regresiones** (antes 3526).

### Pendientes (Sprint E.1.1 — fase 2)
- ⏳ **E.1.1-d Hook automático**: integrar el resolver en `_enrich_football`/pipeline cuando aparezca `REQUIRES_MARKET_IDENTIFICATION`.
  - Requisito: no bloquear el camino crítico; debe ser fail-soft y preferiblemente background-first.
- ⏳ **E.1.1-f Reemplazo editorial SportyTrader → 365Scores**:
  - Esto pertenece a `football_external_fallback_orchestrator.py` (editorial), no al market resolver.
- ⏳ **UI (E.3)**: render de candidatos cuando `resolution_status=AMBIGUOUS` + selección por el usuario.

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — PENDIENTE ⏳ (P0)

### Objetivo
Usar los snapshots live para detectar oportunidades/anomalías **sin apostar**:
- Outliers (cuotas erróneas)
- Edge vs probabilidad del engine
- Movimientos rápidos de línea
- Dispersión entre bookmakers

### Entregables propuestos
1) `services/odds_value_detector.py`
   - Input: odds_snapshots + prob del engine
   - Output: lista de señales por match/market/book
   - Reglas:
     - **Edge**: `model_prob - implied_prob >= threshold_pp`
     - **Outlier**: comparación contra consenso (median) + z-score robusto
     - **Fast move**: delta odds / implied en ventana corta (requiere últimas N snapshots)
     - **Dispersion**: rango/varianza entre books para mismo market
   - Fail-soft y explainable con reason_codes.

2) `services/odds_alerts.py`
   - Persistir en `odds_alerts` con:
     - `match_id`, `event_id`, `market`, `bookmaker`, `signal_type`, `severity`,
       `model_prob`, `implied_prob`, `edge_pp`, `created_at`, `snapshot_refs`, etc.
   - Dedupe: evitar spam (ventana + fingerprint).

3) Wiring scheduler (opcional en E.2)
   - Job que corre después de cada ciclo de live odds.

4) Tests
   - Unit tests (módulos puros) + integración con FakeDB.

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — PENDIENTE ⏳ (P0)

### Objetivo
- UI para listar/filtrar alertas (`odds_alerts`).
- Panel comparador manual: user introduce cuotas y el sistema calcula implied/edge vs modelo.

### Extensión por E.1.1
- Render de `AMBIGUOUS` candidates del resolver de identidad de mercado.
- UI para elegir candidato → setear mercado/selección/linea sugeridos.
- Botón manual de retry: usar `POST /api/football/market-identity/resolve`.

### Entregables propuestos
- Frontend: panel de alertas + panel comparador + selector de candidatos.
- Backend: endpoints de consulta/ack de alertas, y endpoint de cálculo manual.

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Componentes objetivo (por tamaño aproximado)
1. `_enrich_football` (≈ 458 LOC)
2. `ingest_upcoming` (≈ 274 LOC)

## Progreso actual
- ✅ Paso 1/3 completado: extracción de **odds cascade** a helper:
  - `services/_ingestion_helpers/football_odds_cascade.py`
  - Integrado en `data_ingestion._enrich_football`.

## Pendiente
- ⏳ Paso 2/3: extraer **deep enrichment** (team stats + h2h + injuries + recent fixtures) sin cambios de comportamiento.
- ⏳ Paso 3/3: extraer **live stats hydration** (API-Sports fixture_statistics + merge TheStatsAPI match_stats) sin cambios de comportamiento.
- ⏳ Refactor `ingest_upcoming`.

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE (con cambio de arquitectura) ⏳

## Cambio solicitado por el usuario
- Implementar **The Odds API historical como fuente primaria** y **TheStatsAPI como fallback**.

## Nota crítica (técnica)
- **The Odds API solo provee ODDS** (mercados/cuotas). **NO provee lineups ni standings**.
- Por lo tanto, el cambio solicitado aplica a la **capa de odds** (ya existe en D4 como cliente) y NO puede ser “primario” para lineups/standings.

## Plan actualizado (fail-soft)
- **F84.c Lineups**:
  - Primario: **TheStatsAPI** (si ofrece lineups en el endpoint disponible).
  - Fallback: **API-Sports** (si está habilitado y con key).
  - (The Odds API NO aplica).
- **F84.d Standings**:
  - Primario: **TheStatsAPI**.
  - Fallback: **API-Sports**.
  - (The Odds API NO aplica).
- **Odds (ya existente)**:
  - Primario para históricos (backtests): **The Odds API historical snapshots**.
  - Fallback para live/pre-match: **TheStatsAPI odds**.

## Backend
- Nuevos adaptadores:
  - `services/external_sources/thestatsapi_lineups.py`
  - `services/external_sources/thestatsapi_standings.py`
  - `services/external_sources/api_sports_lineups.py` (si no existe)
  - `services/external_sources/api_sports_standings.py` (si no existe)
- Endpoints:
  - `GET /api/football/match/{match_id}/lineups`
  - `GET /api/football/league/{league_id}/standings`

## Frontend
- `LineupsPanel.jsx`
- `StandingsPanel.jsx`

## Tests
- Unit tests de normalización + mocks HTTP
- Render tests FE para AVAILABLE/PENDING/UNAVAILABLE

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- 🟡 **SPRINT D5** (en curso): 5 ligas 24/25 + WC18/WC22/Euro24 + cohortes + 3 reports.
- ⏳ **SPRINT E.2**: Detector de valor + alertas (usa odds_snapshots live).
- ⏳ **SPRINT E.3**: UI de alertas + comparador manual (+ selección candidatos AMBIGUOUS).
- ⏳ **E.1.1 fase 2**: hook automático + SportyTrader→365Scores (editorial).

### Pendientes P1
- 🟡 **REFACTOR-1** pasos 2/3 y 3/3 + `ingest_upcoming`.
- ⏳ **F84.c/F84.d** Lineups + Standings (TheStatsAPI primary, API-Sports fallback).

### Pendientes P2
- ⏳ Expandir `team_name_translations.py` (UCL/UEL).
- ⏳ Expandir backtest framework a otros mercados (BTTS / Over 2.5 / Corners) tras validar DRAW.

---

## 6) Validación esperada (estado actual)

- Suites actuales (post Sprint E.1.1):
  - Backend: **3554 passing tests**, 2 skipped.
  - Frontend: **174 passing tests**.

- Reglas:
  - Cero regresión post-cada cambio lógico.
  - Arquitectura fail-soft y back-compat.
  - Point-in-time correctness: prohibido usar datos futuros en backtests.
  - SPRINT D3/D4/D5/E: `observe_only` (no tocar ranking real; no apuestas automáticas).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta (sin leakage).
  - **E.1**: polling limitado al universo visible de UI (no global por deporte).
  - **E.1.1**: resolver de identidad de mercado principal = The Odds API.

- Flags / env:
  - ✅ `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - ✅ `THE_ODDS_API_KEY=...` (no hardcode en código).
  - ✅ (Sprint E.1) `LIVE_ODDS_ENABLED=true|false`
  - ✅ (Sprint E.1) `LIVE_ODDS_REFRESH_SECONDS=240`
  - ✅ (Sprint E.1) `LIVE_ODDS_SPORTS=...`
  - ✅ (Sprint E.1) `LIVE_ODDS_MARKETS=h2h,totals`
  - ✅ (Sprint E.1) `LIVE_ODDS_REGIONS=uk,eu`
  - ✅ (Sprint E.1) `LIVE_ODDS_LOOKBACK_HOURS=24`
  - ✅ (Sprint E.1) `LIVE_ODDS_MAX_MATCHES=80`
  - ✅ (Sprint E.1) `LIVE_ODDS_QUOTA_MIN=50`
  - ✅ (Sprint E.1.1) `MARKET_RESOLVER_REGIONS=uk,eu,us` (default)
  - ✅ (Sprint E.1.1) `MARKET_RESOLVER_CACHE_TTL=21600` (default 6h)
