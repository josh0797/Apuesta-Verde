# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** + **MLB Under Confidence Floor (P0)** + **F6C Auto-Settle (P1)** (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (`code/label/severity/explanation`) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Hardening de pipeline: evitar bloqueos en `stage=enriching` con timeouts + degradación elegante.

- **(✅ COMPLETADO)** Robustez multi-deporte en LIVE:
  - Detectar correctamente partidos LIVE en **basketball/baseball**.
  - Evitar “zombies LIVE” en fútbol.
  - Firewall de vocabulario para impedir **fugas de terminología**.

- **(✅ COMPLETADO)** Enriquecimiento histórico fútbol (últimos 15): mejorar explicabilidad y señales para rescate Under.

- **(✅ COMPLETADO)** **P3 — Editorial Context Engine (Scrapy)**:
  - Capa opcional y **fail-soft** de enriquecimiento editorial profundo **solo para fútbol** y **solo para matches shortlisteados**.
  - Separación **dato vs opinión** (heurístico regex) + interpretación Moneyball.
  - UI: bloque “Contexto editorial”.

- **(✅ COMPLETADO / VALIDADO EN VIVO)** Tuning de selectores + fuentes nuevas:
  - **AS.com** y **Marca** server-rendered.
  - Añadido filtrado fino para Marca (evitar `mercado-fichajes` / `-directo.html`).
  - Spider con dedupe por URL y soporte de exclusión por patrón.

- **(✅ COMPLETADO)** **P4 — Playwright** para fuentes JS-heavy:
  - Subprocess + stealth + dispatch paralelo Scrapy/Playwright.
  - Detecta challenges anti-bot y degrada sin romper análisis.

- **(🟨 PENDIENTE / BLOQUEADO)** **Bright Data Web Unlocker** como tercer backend:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball y MLB**.
  - **Bloqueo actual:** faltan credenciales del usuario (`BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`).

- **(✅ COMPLETADO)** **Historical Detail Enrichment (Baseball)**:
  - Antes de analizar/descartar MLB, enriquecer con histórico profundo (últimos 15) y generar perfiles por equipo + combinado.
  - Añadir `baseballRunsRescueLayer(match)` y **trap signals históricas**.

- **(🟨 PENDIENTE)** **Historical Detail Enrichment (Basketball)**:
  - Antes de analizar/descartar basketball, enriquecer con histórico profundo y generar perfiles.
  - Añadir `basketballTotalPointsRescueLayer(match)`.
  - UI: sección “Historial profundo”.

- **(✅ COMPLETADO)** **MLB Margin & Total Script Engine v2 (solo Baseball)**:
  - Engine especializado en guion MLB:
    - Predecir **margen de victoria** (Run Line -1.5 favoritos dominantes)
    - Seleccionar líneas **Over/Under más protegidas** (6.5/7.5/8/8.5/9 y equivalentes)
    - Análisis **pitcher-first** con gate de pitchers confirmados
    - Parlays **MLB-only** con validación de correlación positiva
  - Restricción crítica: **NO tocar basketball/football** (backend y UI).

- **(✅ COMPLETADO)** **MLB Feedback Loop (P2)**:
  - Guardar outcomes reales por pick: `result/outcome`, `margin`, `totalRuns`, `runLineCovered`, `overHit`.
  - Guardar snapshot v2: `expectedRuns`, `marginProjection`, `coverProbability`, `lineSelected`.
  - Recalibración automática cada 50 picks settled → persiste pesos en DB.

- **(✅ COMPLETADO)** **MLB-V5 — Bucketing estructural MLB + Manual Odds Review**:
  - Baseball NO usa el LLM genérico en `/api/analysis/run`.
  - Nuevos buckets MLB:
    - `structural_lean_requires_odds`
    - `watchlist_manual_odds`
    - `discarded_after_full_analysis`
  - UI: sección **“Revisión manual — falta cuota”** (solo MLB) vía `ManualOddsReviewPanel.jsx`.

- **(✅ COMPLETADO)** **MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery / Market Audit (V6 UI + Backend)**:
  - Fix Totals (Poisson) + UI Edge vs Línea + picks visibles.
  - **Over Discovery Engine (V6)** para eliminar sesgo hacia Unders:
    - Offensive Explosion Score (0–100)
    - Offensive Script badge
    - Over Survival score
    - Market competition Under vs Over + swap cuando Over domina
    - Daily Market Audit endpoint

- **(✅ COMPLETADO)** **MLB-V4 Live Intelligence**:
  - Volatility detection + script breaks monitoring + cashout intelligence.
  - Restricción: solo aplica a matches que pasaron el filtro pregame.

- **(✅ COMPLETADO)** **F6A/F6B Bullpen Risk & Storage**:
  - Downgrade Full Game Unders a F5 Under si bullpens son riesgosos.
  - Storage post-match de script breaks.

- **(✅ COMPLETADO)** **MLB-V5 Script Survival & Fragility**:
  - Survival score 0–100 + fragility score 0–100 con clasificación de estabilidad.
  - UI: summary + detail panels.

- **(✅ COMPLETADO — NUEVO P0)** **MLB Under Confidence Floor (Moneyball guardrail)**:
  - Problema: picks MLB Under con `confidence_score` en el rango 50–74 podían pasar como recomendación activa cuando hay odds y edge positivo.
  - Solución: en `services/moneyball_layer.py::analyze_pick`, **pre-guardia específica sport+market**:
    - Solo para `sport == "baseball"`, **market Under (no team total, no NRFI)**.
    - Solo cuando `edge is not None` (hay odds → edge calculable).
    - Si `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75, env-tunable) → degrada a `WATCHLIST`.
    - Marca el pick con `pick["_conf_floor_demoted"] = True`.

- **(✅ COMPLETADO — NUEVO)** **UI/summary: bucket de democión por floor**:
  - `server.py` expone `summary.conf_floor_demoted` con picks del bucket `watchlist_manual_odds` que incluyen `_conf_floor_demoted=True`.

- **(✅ COMPLETADO — NUEVO P1)** **F6C Auto-Settle MLB (sin intervención del usuario)**:
  - Nuevo módulo `services/mlb_results_settler.py`:
    - `_resolve_result()` para mercados determinísticos con final-score (Over/Under full-game, team totals).
    - `auto_settle_pending_evaluations()` barre `mlb_run_evaluations` pending, busca `matches.final_score` y llama `update_run_evaluation_result`.
  - Wiring APScheduler en `services/scheduler.py`:
    - Job `_job_auto_settle_mlb_evaluations` cada **20 min**, offset respecto a `settle_finished_baseball` (15 min).
  - Cierra el loop F6C automáticamente para `mlb_run_evaluations` cuando existe `final_score`.

---

## 2) Implementation Steps

### Phase 1 — Core POC (aislado) para el flujo “tolerancia + decisión contextual + señales trampa”
**Estado:** ✅ COMPLETADO

---

### Phase 2 — V1 App Development (backend + wiring de rescate)
**Estado:** ✅ COMPLETADO

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

---

### Phase 4 — P0 LIVE Hardening + P2 Historical Profile (fútbol)
**Estado:** ✅ COMPLETADO

---

### Phase 5 — P3 Editorial Context Engine (Scrapy) — MVP
**Estado:** ✅ COMPLETADO

---

### Phase 6 — P3 Selector Tuning + New Sources (AS.com, Marca) + limpieza de falsos positivos
**Estado:** ✅ COMPLETADO (validación real 2026-05-28)

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy)
**Estado:** ✅ COMPLETADO (infra lista; desbloqueo real requiere unlocking)

---

## Phase G3 — Critical Bug Fixes (Defense-in-Depth Time Filter + Pitcher Quality Rewrite)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G2 — Baseball Savant + Parlay Correlation Validator
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G4 — Multi-Source Sports Scrapers Wiring (MLB + Basketball)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V2 — MLB Margin & Total Script Engine v2
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V3 — Baseball Historical Detail Enrichment + baseballRunsRescueLayer
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V4 — Live Intelligence (Volatilidad / Script Breaks / Cashout) + endpoint reevaluate
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V5 — Script Survival + Fragility + UI Panels
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-F6A/F6B — Bullpen Risk Selector + Storage Script Breaks
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery Engine + Daily Market Audit
**Estado:** ✅ COMPLETADO (2026-05-30)

(Se mantiene la documentación V6.1–V6.5 sin cambios.)

---

## Phase MLB-M2 — Bullpen Real-Usage (pitch_stress) + Finished-Game Settler
**Estado:** ✅ COMPLETADO (2026-05-31)

(Se mantiene la documentación M2.1–M2.5 sin cambios.)

---

## Phase GAPS-4 — LiveMarketStateValidator + LivePreMatchComparisonLayer + Under-Loss Anti-Pattern Library
**Estado:** ✅ COMPLETADO (2026-05-31)

---

## Phase GAPS-5 — Under Veto Power-Bat + Bullpen Pitch-Stress + Learning Cases MLB
**Estado:** ✅ COMPLETADO (2026-05-31)

---

## Phase RECAL — Lightweight Recalibration + Feedback APScheduler + Bright Data Health
**Estado:** ✅ COMPLETADO (2026-05-31)

### RECAL.3 Feedback-loop recalibración automática (P2)
- **Estado:** ✅ COMPLETADO
- `FEEDBACK_BATCH_SIZE: 50 → 40` en `services/mlb_feedback_loop.py`.
- Job APScheduler `recompute_feedback_weights` (cada 30 min) en `services/scheduler.py`.

### RECAL.5 (NUEVO) F6C Auto-Settle de evaluaciones pending
- **Estado:** ✅ COMPLETADO (2026-06-02)
- `services/mlb_results_settler.py`:
  - `_resolve_result()` (Over/Under full-game + team totals; skip determinístico para F5/NRFI/inning).
  - `auto_settle_pending_evaluations()`.
- `services/scheduler.py`:
  - Job `_job_auto_settle_mlb_evaluations` cada 20 min.

---

## Phase MLB-FP4 — Recent Form v2 (schedule+boxscore) + Trend Interpreter
**Estado:** ✅ COMPLETADO (2026-06-03)

### MLB-FP4.1 Root-cause fix: L5 = L15 (Δ=0.0)
- **Bug confirmado por screenshot del usuario**: el panel "Tendencia carreras 5 vs 15" mostraba valores idénticos para L5 y L15 (4.3/4.3, 12.0/12.0, etc.) porque el endpoint `/teams/{id}/stats?stats=lastXGames&limit=N` IGNORA el parámetro `limit` y devuelve el mismo agregado season-to-date para cualquier N.
- **Fix**: rescritura completa de `services/mlb_recent_form_split.py` con `/schedule` + `/boxscore`:
  1. `GET /api/v1/schedule?sportId=1&teamId={teamId}&startDate=-35d&endDate=today&gameType=R` → partidos FINAL.
  2. Para cada `gamePk` (top-15 más recientes) → `GET /api/v1/game/{gamePk}/boxscore` → batting line del equipo.
  3. Aggregate L15 y L5 separadamente; Δ_5_vs_15 calculado correctamente.
- Cache de 12h (`_SCHEDULE_CACHE` + `_BOX_CACHE`), fail-soft total, paralelo con `asyncio.gather`.

### MLB-FP4.2 Trend Interpreter — `services/mlb_trend_interpreter.py` (nuevo)
- Capa de interpretación que consume `recent_run_split` + `on_base_profile` y produce señales accionables.
- Reglas: TOB Δ ≥ +2.0 → strong_rising (over +16, explosive +12); ≥+1.5 → moderate (+10); ≤-2.0 → strong_declining (under +16); HR rising → over +4 + explosive +4.
- Decisión `SUPPORTS_OVER / SUPPORTS_UNDER / MIXED / NEUTRAL` por diff ≥ 8.
- Ajustes por mercado: Under + SUPPORTS_UNDER → +6/+6; Under + SUPPORTS_OVER → -12/-12; Mixed → -4/-4.
- Runline +1.5 con lógica separada underdog vs favorite (UNDERDOG_OFFENSE_CAN_COMPETE, FAVORITE_OFFENSE_SURGING_AGAINST_RUNLINE, FAVORITE_POWER_SPIKE_RUNLINE_RISK).
- Clamps: score ∈ [-15, +12], confidence ∈ [-12, +6].
- Outputs incluyen `human_summary`, `human_explanations` (ES), `decision_notes`, `mixed_signals`, `impact_on_final_pick`, `per_side` breakdown.

### MLB-FP4.3 Integración orchestrator
- Tras computar `recent_form_payload`, se llama a `combine_trend_signals()` y se aplica:
  - `pick_payload["trend_interpretation"]` (payload completo para UI + audit).
  - `recommendation.confidence_score` += `confidence_adjustment` (clamped 0-100).
  - `recommendation.confidence_trend_adjustment` (auditoría).
  - `reason_codes` extendidos con los del interpreter.
  - `underdog_side` derivado desde moneyline odds (home_ml > away_ml → home underdog).

### MLB-FP4.4 UI — `TrendInterpretationBlock` (nuevo subcomponente)
- Render justo debajo del bloque L5 vs L15 en `HistoricalProfilePanel.jsx`.
- Decision chip (emerald/sky/amber/slate), human summary, grid 2× Δ Score + Δ Confianza con signo.
- Barras de Apoyo Over / Apoyo Under (0-16).
- Chip riesgo explosivo cuando `explosive_risk_boost > 0`.
- Listas de `human_explanations` + `decision_notes`.
- `impact_on_final_pick` italic al pie.

### Validación
- **312 tests PASS** (297 previos + 15 nuevos del interpreter + 8 nuevos del schedule scraper).
- Cobertura: aggregate, scheduling mocked → L5 ≠ L15, empty/error fail-soft, strength bands, market-direction adjustments, mixed signals, runline +1.5, clamps.
- ESLint + esbuild limpio. Backend reiniciado limpio.

### Despliegue
Cambios en **PREVIEW**. Para `https://low-volatility-plays.emergent.host` se necesita **redeploy** del usuario.

---


## Phase MLB-FP3 — Live Engine v2 + Recent Form Split BB/HR/Hits
**Estado:** ✅ COMPLETADO (2026-06-03)

### MLB-FP3.1 Live engine — FIN del falso "Datos live insuficientes"
- Archivo: `services/live_pre_match_comparison.py`
- Bug: cuando el partido finalizaba sin `period_n` (juegos FINAL), `_classify_script` devolvía `insufficient_data` aunque el validator hubiera resuelto el pick → la UI bloqueaba toda la información y mostraba "Datos live insuficientes" en lugar de "Pick ya perdió".
- Fix: tras `_classify_script`, si tenemos `actual_total` y el partido es FINAL o validator devolvió `already_resolved_*`, se promueve `script_status="final_settled"`.

### MLB-FP3.2 Nuevo `live_verdict` (chips solicitados por el usuario)
- Función nueva: `_derive_live_verdict()` con lógica para los 7 chips canónicos:
  - `PICK_ALREADY_LOST` / `PICK_ALREADY_WON`
  - `AVOID_UNDER_OR_LOOK_OVER` / `AVOID_OVER_OR_CASHOUT`
  - `MAINTAIN` (on-script + still playable)
  - `CASHOUT` (deviación)
  - `NO_ACTIONABLE` (final sin resolución / sin pregame)
- Lógica direccional: si pregame era Under y actual > expected con broken_script → `AVOID_UNDER_OR_LOOK_OVER`; si era Over y actual < expected → `AVOID_OVER_OR_CASHOUT`.

### MLB-FP3.3 Payload `live_data` (box-score live)
- El comparator ahora extrae `score_home / score_away / total_runs / hits / walks / home_runs / errors / strikeouts / pitches / inning + half` del `live_state` y los expone bajo `comparison.live_data`. Filtra keys nulos para mantener el payload limpio.

### MLB-FP3.4 Recent form split — HR + deltas explícitos
- Archivo: `services/mlb_recent_form_split.py`
- `_fetch_last_x_games` / `get_team_recent_form` ahora calculan `home_runs_avg_last_5/15`.
- `_ob_block` expone `hits_delta_5_vs_15`, `walks_delta_5_vs_15`, `hbp_delta_5_vs_15`, `home_runs_delta_5_vs_15` para que la UI pueda renderizar tendencias por componente.

### MLB-FP3.5 Frontend — `LivePreMatchComparisonPanel.jsx`
- Eliminado el early-return en `insufficient_data` cuando hay `actual_total` / `pregame_pick_status` accionable / `live_verdict` (criterio `hasUsefulInfo`).
- Añadido pill `final_settled`.
- Nuevo chip "Veredicto live" con los 7 estados + colores específicos (rose para lost, cyan para won, orange para pivot, emerald para maintain, amber para cashout, slate para no-actionable).
- Nueva tabla de box-score live: 4 columnas (Local / Visitante / Total) con filas para carreras, hits, BB, HR, errores, ponches, lanzamientos — render condicional, oculta filas con todos los valores nulos.

### MLB-FP3.6 Frontend — `MatchDetailPage.jsx`
- El header del bloque "settled" antes era genérico ("Pick pregame ya cumplido / no accionable"). Ahora:
  - `already_won` → "Pick pregame · ya ganó" (cyan)
  - `already_lost` → "Pick pregame · ya perdió" (rose)
  - `not_actionable` → "Pick pregame · no accionable" (amber)

### MLB-FP3.7 Frontend — `HistoricalProfilePanel.jsx`
- `OnBaseTrendCell` ahora renderiza sub-filas para **Hits / BB / HR** con L15 · L5 · Δ debajo del bloque agregado de times-on-base. Δ coloreado por dirección (verde / rojo / gris). Cada fila tiene `data-testid` específico (`-hits`, `-walks`, `-home_runs`) para QA.

### Validación
- 13 tests nuevos en `tests/test_live_verdict_and_form_split.py` → PASS:
  - `_derive_live_verdict()` para 7 escenarios.
  - Caso real Minnesota 6-4 FINAL → final_settled + already_lost + PICK_ALREADY_LOST.
  - Caso real Yankees 4-9 9th → already_lost + PICK_ALREADY_LOST.
  - Recent form split expone HR + deltas calculados correctamente.
  - Umbrales `_classify_run_trend` / `_classify_on_base_trend`.
- Suite total: **289 tests PASS** (276 previos + 13 nuevos).
- ESLint + esbuild OK en `LivePreMatchComparisonPanel.jsx`, `HistoricalProfilePanel.jsx`, `MatchDetailPage.jsx`.
- Backend reiniciado limpio, APScheduler activo.

### Nota de despliegue
- Los cambios viven en **PREVIEW**. Para producción (`https://low-volatility-plays.emergent.host`) requiere **redeploy** del usuario.

---

## Phase MLB-FP2 — Deep Script UI: lean visual + L5/L15 + Manual Odds inline
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-FP2.1 Fix override del lean histórico (root cause del "LEAN OVER CARRERAS" en pick UNDER)
- Archivo: `services/mlb_day_orchestrator.py` (~líneas 1379-1455).
- **Bug**: el `market_lean_classifier.classify_and_validate()` se ejecutaba bien y producía `lean=UNDER` para casos como Detroit @ Rays (ER 7.1 vs línea 9.5), pero el override escribía en `baseballHistoricalProfile["overUnderLean"]` (raíz). La UI (`HistoricalProfilePanel.jsx`) lee `combined.overUnderLean` → el override nunca llegaba al header y se mostraba el heurístico legacy (`projected_total_runs > league_avg` → OVER).
- **Fix**: el override ahora escribe primero en `baseballHistoricalProfile.combined.overUnderLean / overUnderLeanDisplay / overUnderLeanConfidence / overUnderLeanReason / overUnderLeanConsistency`. Conserva mirror en la raíz para consumidores legacy (`baseball_runs_rescue`, `script_conflict`).
- Adicional: el override guarda `combined.historicalLeanLegacy` para auditoría.

### MLB-FP2.2 Mixed Signals payload (señales mixtas)
- Cuando `legacy_lean` ≠ `final_lean`, el orquestador genera `combined.mixedSignals`:
  ```python
  {
    "has_mixed_signals": True,
    "over_signals":      ["HISTORICAL_HEURISTIC_LEAN_OVER", "RISING_RUN_ENVIRONMENT", "RISING_ON_BASE_PRESSURE", ...],
    "under_signals":     ["EXPECTED_RUNS_BELOW_LINE", ...],
    "final_resolution":  "LEAN_UNDER",
    "legacy_lean":       "OVER",
  }
  ```
- UI render: `MixedSignalsBlock` en `HistoricalProfilePanel.jsx` con dos columnas (apuntan a Over / Under) + ribbon de resolución final.

### MLB-FP2.3 Mirror recent_run_split + on_base_profile en `baseballHistoricalProfile`
- Los campos `recent_run_split`, `recent_run_trend`, `on_base_profile` que ya se calculaban en pick_payload ahora también se copian en `baseballHistoricalProfile.recentRunSplit / recentRunTrend / onBaseProfileL5`.
- Permite al panel renderizar el bloque sin tocar la API de la card.

### MLB-FP2.4 UI: bloque L5 vs L15
- Archivo: `frontend/src/components/HistoricalProfilePanel.jsx` (`RecentFormSplitBlock`, `RunTrendCell`, `OnBaseTrendCell`).
- Grid 3 columnas (Local / Visitante / Combinado) para `runs_scored_avg_last_15 / last_5 / delta` con chip de trend (Subiendo / Bajando / Estable) — umbral L5-L15 ≥ ±1.25 carreras.
- Grid 2 columnas (Local / Visitante) para `times_on_base_avg_last_15 / last_5 / delta` con OBP opcional — umbral ±1.0.
- Trends consolidados consumidos directos del backend (`RISING_RUN_ENVIRONMENT` / `RISING_ON_BASE_PRESSURE` / etc.).
- Sólo renderiza cuando al menos un valor L5 está presente; fail-soft si MLB Stats API no responde.

### MLB-FP2.5 UI: input inline "Agregar cuota manual"
- Nuevo componente: `frontend/src/components/InlineManualOddsInput.jsx`.
- Surfacea inside la card `MatchCard.jsx` justo debajo de `Cuota aprox.: —` cuando:
  - `sport === "baseball"` AND `recommendation.odds_range` está vacío.
- POST al endpoint existente `/api/mlb/picks/{pickId}/manual-odds` (acepta `"1.85"` y `"1,85"`).
- Toast en español con `value_status` + edge%.

### Validación
- `pytest backend/tests/` → **276 PASS** (sin regresiones).
- `esbuild` + ESLint sobre `HistoricalProfilePanel.jsx`, `MatchCard.jsx`, `InlineManualOddsInput.jsx` → 0 errors.
- Backend reiniciado limpio (todos los APScheduler jobs activos).
- Smoke screenshot: dashboard carga sin runtime errors.

---

## Phase MLB-FP1 — Final Pick Router + Manual Odds + Momentum (L5 vs L15)
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-FP1.1 Conflict detector
- Archivo: `services/mlb_script_conflict.py`
- `detect_total_script_conflict(chosen_market, deep_script)` con severity ladder (`high` / `medium`).
- Códigos: `UNDER_PICK_CONFLICTS_WITH_OVER_SCRIPT`, `OVER_PICK_CONFLICTS_WITH_UNDER_SCRIPT`, `UNDER_BELOW_PROJECTED_RUNS`, `UNDER_CLOSE_TO_PROJECTED_RUNS`, `OVER_ABOVE_PROJECTED_RUNS`, `F5_OVER_VS_FULLGAME_UNDER`.
- Wireado en `services/mlb_day_orchestrator.py` (fail-soft): inyecta `pick_payload.script_conflict` y degrada/redirige a watchlist en severity `high`.

### MLB-FP1.2 Manual odds helpers
- En el mismo módulo: `parse_manual_odds()` (acepta `"1,85"`/`"1.85"`, guard ≥ 1.01) y `calculate_manual_edge()` (`VALUE` / `FAIR_VALUE` / `NO_VALUE` / `UNKNOWN` / `INVALID`).

### MLB-FP1.3 Endpoint `POST /api/mlb/picks/{pick_id}/manual-odds`
- Archivo: `server.py`
- Lookup en `pick_runs` (buckets `picks`, `rescued`, `structural_lean_requires_odds`, `watchlist_manual_odds`).
- Recalcula edge contra `estimated_probability` (con fallback a `_mlb_script_v2.coverProbability`).
- Persiste `manual_odds*`, `manual_value_status`, `manual_can_recommend`, `manual_rationale`, `manual_odds_submitted_at` en el bucket correcto vía `arrayFilters`.
- Promoción opcional a `RECOMMENDED_MANUAL_ODDS` si `promote_if_value && value_status == VALUE`.

### MLB-FP1.4 Recent-form split (L5 vs L15)
- Archivo: `services/mlb_recent_form_split.py`
- `get_team_recent_form()` consulta MLB Stats API `lastXGames` con caché 12h.
- `build_recent_form_payload()` genera `recent_run_split`, `recent_run_trend` (`RISING_RUN_ENVIRONMENT` / `STABLE` / `DECLINING` / `UNKNOWN`) y `on_base_profile` con sub-tendencias por equipo.
- Integrado en orchestrator (fail-soft, gather paralelo home+away).

### MLB-FP1.5 UI — `ManualOddsReviewPanel.jsx`
- Acepta coma o punto como separador decimal.
- Llama al endpoint con `api.post` y muestra `value_status` + `manual_edge_pct` + toast en español.
- Render del conflict ribbon (`script_conflict`) con severity colors.
- **Bugfix:** `import api from '@/lib/api'` → `import { api } from '@/lib/api'` (el módulo solo exporta named, no default — sin el fix el componente fallaba en runtime).

### Validación
- `pytest backend/tests/` → **276 tests PASS** (regresión completa).
- Smoke endpoint:
  - `POST /manual-odds {"manual_odds":"0,5"}` → 400 `"manual_odds inválida (debe ser ≥ 1.01, acepta '1.85' o '1,85')"`.
  - `POST /manual-odds {"manual_odds":"1,85"}` → 404 `"pick not found in recent runs"` (esperado sin pick real).
- `esbuild` sobre `ManualOddsReviewPanel.jsx` → 0 errors.
- Dashboard carga limpio con demo user (screenshot validado).

---

## Phase MLB-P0 — MLB Under Confidence Floor (Moneyball)
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-P0.1 Pre-guardia en Moneyball
- Archivo: `services/moneyball_layer.py`
- Punto: `analyze_pick()` justo antes de `classify_pick()`.
- Regla:
  - `sport == "baseball"`
  - `edge is not None` (odds disponibles)
  - `market` contiene `under`, excluye `team total` y `nrfi`
  - `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75)
  - → `WATCHLIST` + razón explícita + `pick["_conf_floor_demoted"] = True`.

### MLB-P0.2 Exposición en summary
- Archivo: `server.py`
- Añade: `summary.conf_floor_demoted` para inspección UI/QA.

---

## Phase MLB-V7 — MLB Engine V3 (Explainability + Game Script + Diversificación + Baseball-first)
**Estado:** 🟨 PENDIENTE (fase futura; V6 ya cubre drivers ofensivos + swap Over/Under)

> Nota: con V4/V5/V6 ya existe explicabilidad fuerte. V7 queda como refactor/iteración de guion v3 (si se desea ampliar más allá del panel actual) y diversificación adicional.

---

## 3) Next Actions

### A) Validación formal MLB-V6 (P0) — Testing Agent v3
**Estado:** 🟨 PENDIENTE (siguiente paso inmediato)
1) Ejecutar `testing_agent_v3`:
   - Pure functions V6 (`mlb_over_discovery.py`).
   - Endpoint `GET /api/mlb/daily_market_audit`.
   - Regresión V1–V5 (orchestrator chain intacta).
   - **Nuevo:** verificar que `MLB_UNDER_CONFIDENCE_FLOOR` demote correctamente a WATCHLIST cuando hay odds.
   - **Nuevo:** verificar bucket `summary.conf_floor_demoted` y flag `_conf_floor_demoted`.
2) Validar UI (smoke):
   - Chips V6 en `MLBScriptV3Panel` (summary siempre visible + detail al expand).
   - Badge `Under→Over` cuando exista swap.
   - **Nuevo:** que el bucket `conf_floor_demoted` sea consumible por el frontend (aunque aún no haya panel dedicado).

### B) Bright Data Unlocker (P0 bloqueado) — siguiente prioridad scraping
1) Confirmar `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE` (Web Unlocker).
2) Implementar cascade por scraper: `direct_fetch` → (403/timeout) → `brightdata_fetch`.
3) Añadir cache TTL en DB para reducir coste (por tipo de URL).
4) Activar Unlocker en:
   - Editorial Context (Sportytrader/BeSoccer/scores24)
   - NBA/basketball y MLB scrapers con Cloudflare.

### C) Basketball Historical Detail (P1)
1) Implementar profile + integración pipeline.
2) Añadir rescue layer (totales/team totals) + trap signals.
3) UI “Historial profundo”.

### D) Fix 2C (P2) — Persistencia live como async
**Estado:** 🟨 PENDIENTE
- Consolidar persistencia dentro de `build_live_intelligence_payload` como llamada `async`.

### E) Football deep-live parity (P3)
**Estado:** 🟨 PENDIENTE
- Aplicar `LivePreMatchComparisonLayer` y lógica live profunda a Football.

---

## 4) Success Criteria
- Market tolerance y rescue layers funcionan sin inventar valor.
- LIVE multi-deporte estable; sin zombies; sin fugas de vocabulario.
- Editorial Context:
  - Scrapy/Playwright/BrightData degradan elegante.
  - Fuentes bloqueadas se desbloquean con Unlocker cuando procede.
  - UI muestra contexto con fuentes y warnings.

- Historical Detail Enrichment:
  - Ningún match (basketball/baseball) prioritario se descarta sin intentar perfil histórico.
  - Se detectan oportunidades en **totales/team totals/F5/run line** con razonamiento humano.
  - Moneyball guardrail siempre manda: sin edge → no recomendación.

- **MLB Under Confidence Floor (✅ nuevo, cumplido)**
  - Un pick MLB Under **no** puede quedar como apuesta recomendada si:
    - hay odds (edge calculable) y `confidence_score < 75`.
  - Debe degradarse a `WATCHLIST` con razón explícita y flag `_conf_floor_demoted=True`.
  - `summary.conf_floor_demoted` expone esos casos para auditoría.

- **F6C Auto-Settle (✅ nuevo, cumplido)**
  - Evaluaciones `mlb_run_evaluations` en `pending` se resuelven automáticamente cuando:
    - `matches.final_score` existe (escrito por `mlb_finished_game_settler`).
  - Deben actualizarse a `won/lost/push` con `resolved_at` y `miss_type` correcto.
  - Markets no determinísticos (F5/NRFI/inning) se dejan `pending` con `auto_settle_skipped_reason` para manual.

- **MLB-V2 (✅ cumplido y validado)**
  - Picks MLB incluyen: `Projected Margin`, `Cover Probability`, `Best/Recommended Total Line`, `lineSafetyScore`, `pickType`, `sameGameCorrelation`.
  - Parlays MLB-only de 2–4 picks con correlación ≥60 (cuando existan suficientes picks elegibles).
  - Run Line -1.5 solo cuando hay dominancia real.
  - Cero regresiones:
    - Football/basketball sin `_mlb_script_v2`.
    - Parlay genérico intacto fuera de MLB.

- **MLB-V3 (✅ cumplido)**
  - `baseballHistoricalProfile` presente por pick (fail-soft: `available=false` con `_reason`).
  - `historical_trap_signals` expuestas y ajustan `fragility.score`.
  - `baseball_runs_rescue` se intenta antes de descartar cuando el histórico lo permite.

- **MLB-V4 (✅ cumplido)**
  - Endpoint live: `POST /api/mlb/live/reevaluate`.
  - Live Intelligence solo para matches que pasaron filtro pregame.
  - UI en Match Detail con panel Live.

- **MLB-V5 (✅ cumplido)**
  - Script Survival 0–100 + Fragility 0–100 visibles en cards.
  - Clasificación de estabilidad (ELITE_STABLE…HIGHLY_FRAGILE) + panel detalle.

- **F6A/F6B (✅ cumplido)**
  - Swap Full Game Under → F5 Under si bullpens riesgosos.
  - Storage de script breaks en DB post-match.

- **MLB-V6 (✅ cumplido)**
  - Totals: `coverProbability` corresponde a P(Under/Over) del lado recomendado (Poisson).
  - UI muestra `Edge vs línea` y debug de probabilidades.
  - Counter = render: el dashboard renderiza todas las cards (incluye rescued/manual-review).
  - Over Discovery:
    - Offensive Explosion Score + script badge + drivers visibles.
    - Over Survival visible.
    - Badge Under→Over cuando Market Competition swap.
  - Endpoint `GET /api/mlb/daily_market_audit` operativo.

### Testing status
- **Suite total MLB (incluye nuevo settler):** 148 tests PASS.
- **Nuevo archivo tests:** `tests/test_mlb_results_settler.py` → 20/20 PASS.
- Smoke tests:
  - Imports OK: `mlb_results_settler`, `moneyball_layer`, `server.py`.
  - Backend reiniciado limpio; scheduler arrancó con job `auto_settle_mlb_evaluations`.

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
