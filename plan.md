# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA** + ✅ **F94.3 (Live Enrichment Persistence Audit) COMPLETADA** + ✅ **BUGFIX (Football “mismo momio” odds hallucination guard) COMPLETADO** + ✅ **SPRINT A (Draw Potential piloto retrospectivo) COMPLETADO** + ✅ **SPRINT B (Learning snapshots + loops + UI + scheduler) COMPLETADO** + ✅ **SPRINT D (Backtest histórico point-in-time; PL 23/24) COMPLETADO** + ✅ **SPRINT D2 (WC2022 + Euro2024 backtest nacional + Tournament Context) COMPLETADO** + ✅ **SPRINT D3 (Protected Markets: OVER 1.5 + Double Chance) COMPLETADO (P0)** + ✅ **SPRINT D4 (ROI honesto + significancia + walk-forward auditable) COMPLETADO (P0)** + 🟡 **SPRINT D5 (Multi-league + multi-tournament DRAW + cohortes) EN PROGRESO (P0)** + ✅ **SPRINT D6 (Walk-forward calibrator activo; no no-op) COMPLETADO (P0)** + ✅ **SPRINT E.1 (Live Odds Monitor Base — Observe-only) COMPLETADO (P0)** + ✅ **SPRINT E.1.1 (Resolver identidad de mercado por The Odds API) COMPLETADO (P0)** + ✅ **SPRINT E.1.1-d (Hook automático Scheduler) COMPLETADO (P0)** + ✅ **SPRINT E.1.1-f (365Scores Tendencias Top; reemplazo SportyTrader editorial) COMPLETADO (P0)** + 🟡 **REFACTOR-1 (data_ingestion top-2) EN PROGRESO (paso 1/3 completado)** + ✅ **SPRINT E.2 (Odds Value Detector + Alerts — backend) COMPLETADO (P0)** + ✅ **SPRINT E.3 (UI Odds Alerts + Resolver AMBIGUOUS + Comparador manual) COMPLETADO (P0)** + ⏳ **F84.c/F84.d (Lineups + Standings) PENDIENTE (P1)**.

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

## Phase SPRINT D6 — Probar que el Walk-Forward Calibrator NO es un no-op — COMPLETADO ✅ (P0)

### Problema
El test previo de walk-forward calibrator tenía una aserción trivial y el fixture chico no mostraba efecto de calibración.

### Objetivo
Demostrar con un test determinista que, con suficiente muestra (≥100 picks) y shrinkage bayesiano (K=50), el calibrador **mueve** la predicción hacia la frecuencia observada.

### Implementación realizada (backend)
- ✅ `services/football_backtest_engine.py` extendido **sin cambiar comportamiento legacy por defecto**:
  - Nuevas constantes: `PROB_MIN=0.001`, `PROB_MAX=0.999`, `DEFAULT_SHRINKAGE_K=None`.
  - Helpers nuevos:
    - `_shrinkage_weight(n, K) = n/(n+K)`.
    - `_empirical_observed_rate(history)`.
  - Nuevos parámetros en `run_backtest`:
    - `shrinkage_K: Optional[int] = None` (**opt-in**) — cuando `None`, no hay shrinkage (legacy).
    - `predictor_override: Optional[Callable] = None` — permite fijar probabilidad base en tests.
  - Capa adicional de calibración (solo si `shrinkage_K > 0`):
    - `final = w*iso + (1-w)*base`, con `w=n/(n+K)`.
  - Clamp final: `final ∈ [PROB_MIN, PROB_MAX]`.
  - `_calibration_audit` ampliado:
    - `shrinkage_K`, `calib_weight`, `base_prob`, `iso_calibrated`, `calibrated_prob`, `observed_rate`, `clamped`.

### Tests (deterministas)
- ✅ `tests/test_sprint_d6_calibration_is_active.py` (12 tests):
  - Dataset sintético `n=150` con intercalado tipo Bresenham para mantener tasa estable temporalmente.
  - Prueba de desplazamiento (≥2pp) con `shrinkage_K=50` cuando `n_seen>100`.
  - Verifica dirección (sube si rate>base; baja si rate<base).
  - Verifica monotonicidad y exactitud de `calib_weight = n/(n+K)`.
  - Verifica early-prior y clamps.
  - Verifica opt-in: `shrinkage_K=None` no cambia el comportamiento.
  - Guard de regresión: exige todos los campos en `_calibration_audit`.

### Suite
- ✅ Suite backend total: **3614 passed, 2 skipped, 0 regresiones**.

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
   - ✅ `services/external_sources/the_odds_api_client.py` extendido con `fetch_events()`, `fetch_current_odds()`, `_extract_quota_headers()`.
2) **Servicio `services/live_odds_monitor.py`**
   - ✅ Universo visible, resolver event_id, persistencia snapshots, scheduler integration.
3) **MongoDB (índices)**
   - ✅ `odds_event_id_mappings`, `odds_snapshots` por source (sin TTL).
4) **Scheduler**
   - ✅ Wire con kill-switch `LIVE_ODDS_ENABLED`.
5) **Endpoints**
   - ✅ `GET /api/odds/snapshots/{match_id}`; `GET /api/odds/monitor/status`.
6) **Tests**
   - ✅ `tests/test_live_odds_monitor.py`.

### Variables de entorno (flags)
- `LIVE_ODDS_ENABLED=true|false`
- `LIVE_ODDS_REFRESH_SECONDS=240`
- `LIVE_ODDS_SPORTS=...`
- `LIVE_ODDS_MARKETS=h2h,totals`
- `LIVE_ODDS_REGIONS=uk,eu`
- `LIVE_ODDS_LOOKBACK_HOURS=24`
- `LIVE_ODDS_MAX_MATCHES=80`
- `LIVE_ODDS_QUOTA_MIN=50`

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)

### Problema
Cuota detectada + `REQUIRES_MARKET_IDENTIFICATION` ⇒ el sistema no conocía el mercado.

### Entregables
- ✅ `services/market_identity_resolver.py` + persistencia `market_identity_resolutions`.
- ✅ Endpoints: `POST /api/football/market-identity/resolve`, `GET /api/football/market-identity/history/{match_id}`.
- ✅ Tests unitarios + suite verde.

---

## Phase SPRINT E.1.1-d — Hook automático (Scheduler) para Market Identity Resolver — COMPLETADO ✅ (P0)

- ✅ `services/market_identity_auto_resolver.py`.
- ✅ Wiring scheduler.
- ✅ ENV: `IDENTITY_RESOLVER_ENABLED`, `IDENTITY_RESOLVER_INTERVAL_SECONDS=180`, `IDENTITY_RESOLVER_MAX_PER_CYCLE=20`, `IDENTITY_RESOLVER_LOOKBACK_HOURS=24`.

---

## Phase SPRINT E.1.1-f — 365Scores “Tendencias Top” (reemplazo SportyTrader editorial) — COMPLETADO ✅ (P0)

- ✅ `services/external_sources/score365_trends_client.py` (parser estructurado).
- ✅ `football_external_fallback_orchestrator.py` reemplaza SportyTrader por `top_trends`.

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — COMPLETADO ✅ (P0)

- ✅ `services/odds_value_detector.py` (puro): OUTLIER/DISPERSION/EDGE_VS_MODEL/FAST_MOVE.
- ✅ `services/odds_alerts.py` (Mongo): dedupe + occurrences + ack.
- ✅ Wire en `live_odds_monitor.run_cycle`.
- ✅ Endpoints: `GET /api/odds/alerts`, `POST /api/odds/alerts/ack`.

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — COMPLETADO ✅ (P0)

### Objetivo
- UI alertas (listar/filtrar/ack) + resolver AMBIGUOUS + comparador manual de odds vs modelo.

### Implementación realizada
1) ✅ Resolver UI (E.1.1)
- ✅ `frontend/src/components/MarketIdentityResolverPanel.jsx`.
- ✅ Integración en `ManualMarketIdentityPanel.jsx` (precarga manual form).

2) ✅ UI de alertas
- ✅ `frontend/src/components/OddsAlertsPanel.jsx`.
- ✅ `frontend/src/pages/OddsAlertsPage.jsx`.
- ✅ Ruta `/odds-alerts` + tab "Alertas".

3) ✅ Comparador manual (Fix 2)
- ✅ `frontend/src/components/MarketComparatorPanel.jsx` (nuevo):
  - Card colapsable.
  - Soporta **todos** los `MANUAL_MARKET_TYPES` via `/api/football/manual-market-options`.
  - `model_prob` precargada desde el pick/trace (si existe) + editable.
  - Filas dinámicas (market/selection/line/bookmaker/odd) + cálculo implied/edge en vivo.
  - observe_only estricto.
- ✅ Integración en `frontend/src/components/FootballMarketAuditPanel.jsx`:
  - En rama sin trace (después de ManualMarketIdentityPanel).
  - En rama principal (después de FootballMarketsCheckedTable).

### Estado de build
- ✅ Frontend compila limpio vía esbuild + lint OK.

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Componentes objetivo
1. `_enrich_football`
2. `ingest_upcoming`

## Progreso actual
- ✅ Paso 1/3 completado: extracción de odds cascade a `services/_ingestion_helpers/football_odds_cascade.py`.

## Pendiente
- ⏳ Paso 2/3: extraer deep enrichment.
- ⏳ Paso 3/3: extraer live stats hydration.
- ⏳ Refactor `ingest_upcoming`.

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳

## Nota
The Odds API no provee lineups/standings; aplica a odds únicamente.

## Plan
- Lineups: TheStatsAPI primary, API-Sports fallback.
- Standings: TheStatsAPI primary, API-Sports fallback.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- 🟡 **SPRINT D5** (en curso): 5 ligas 24/25 + WC18/WC22/Euro24 + cohortes + 3 reports.

### Pendientes P1
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Expandir backtest framework a otros mercados (BTTS / Over 2.5 / Corners) tras validar DRAW.

---

## 6) Validación esperada (estado actual)

- Suites actuales:
  - Backend: **3614 passing tests**, 2 skipped.
  - Frontend: build OK vía esbuild; tests FE deben correrse en pipeline.

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
  - **E.1.1**: resolver principal = The Odds API.
  - **E.1.1-d**: auto-resolver por scheduler cada 3 min.
  - **E.2/E.3**: observe_only.
  - **D6**: shrinkage es opt-in (`shrinkage_K=None` preserva legacy).

- Flags / env:
  - ✅ `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - ✅ `THE_ODDS_API_KEY=...`.

  - ✅ `LIVE_ODDS_ENABLED=true|false`
  - ✅ `LIVE_ODDS_REFRESH_SECONDS=240`
  - ✅ `LIVE_ODDS_SPORTS=...`
  - ✅ `LIVE_ODDS_MARKETS=h2h,totals`
  - ✅ `LIVE_ODDS_REGIONS=uk,eu`
  - ✅ `LIVE_ODDS_LOOKBACK_HOURS=24`
  - ✅ `LIVE_ODDS_MAX_MATCHES=80`
  - ✅ `LIVE_ODDS_QUOTA_MIN=50`

  - ✅ `MARKET_RESOLVER_REGIONS=uk,eu,us`
  - ✅ `MARKET_RESOLVER_CACHE_TTL=21600`

  - ✅ `IDENTITY_RESOLVER_ENABLED` (default false)
  - ✅ `IDENTITY_RESOLVER_INTERVAL_SECONDS=180`
  - ✅ `IDENTITY_RESOLVER_MAX_PER_CYCLE=20`
  - ✅ `IDENTITY_RESOLVER_LOOKBACK_HOURS=24`

  - ✅ `ODDS_ALERTS_DEDUPE_WINDOW=1800`

  - ✅ (Sprint D6) `shrinkage_K` es parámetro de `run_backtest` (opt-in); K recomendado: 50.
