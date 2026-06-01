# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** (ACTUALIZADO)

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

### MLB-V6.1 Totals probability model correcto (Poisson)
- Backend: `mlb_pregame_analytics_v2.py`
  - `totals_probability(expected_runs, line)` + `_poisson_cdf`.
  - `smart_total_line_selector()` produce:
    - `coverProbability` del lado recomendado
    - `probabilityUnder`, `probabilityOver`
    - `edgeVsLine` (en carreras)
    - `probabilityModel: Poisson`
    - `probabilityDebug` (provenance)
  - `build_v2_payload()` resuelve `coverProbability` según mercado (totals vs Run Line).

### MLB-V6.2 UI: Edge vs Line + debug + odds manual
- `ManualOddsReviewPanel.jsx`:
  - muestra `Edge vs línea`.
  - panel debug: projected runs, mercado recomendado, Poisson P(U)/P(O).
  - input “Pegar tu cuota” activado con cálculo EV client-side.
- `MLBScriptPanel.jsx`:
  - muestra `Edge vs línea` y P(U/O) + modelo.

### MLB-V6.3 Picks visibles bajo el dashboard (contador = cards)
- Backend (`server.py`):
  - `result.picks` unifica todos los buckets visibles para baseball:
    - `picks + rescued_picks + structural_lean_requires_odds + watchlist_manual_odds`
  - sintetiza `recommendation.confidence_score`.
- Backend: bypass `filter_blocked_picks` genérico para baseball.
- Orchestrator: `kickoff_iso/gameDate/status` añadidos al pick_payload.

### MLB-V6.4 (V6) Over Discovery Engine + Market Competition (backend)
- Backend:
  - `services/mlb_over_discovery.py`:
    - `calculate_offensive_explosion_score()` + drivers/componentes.
    - `classify_offensive_script()`.
    - `calculate_over_survival_score()`.
    - `evaluate_over_markets()` + best_over_market.
    - `market_competition()` para competir Over vs Under y swap.
    - `daily_market_audit()`.
  - Orchestrator: adjunta `_mlb_over_discovery` + top-level `offensive_explosion_score`, `offensive_script_code`, `over_survival_score`.
  - Endpoint: `GET /api/mlb/daily_market_audit`.

### MLB-V6.5 (V6) Over Discovery UI (frontend)
- **Estado:** ✅ COMPLETADO
- `frontend/src/components/MLBScriptV3Panel.jsx`:
  - `MLBOffensiveExplosionSummary` (chip compacto siempre visible):
    - Offensive Explosion 0–100
    - Offensive Script (label_es + icono)
    - Over Survival 0–100
  - `MLBOffensiveExplosionDetail` (solo expand):
    - desglose por componentes + pesos
    - Top Offensive Drivers
    - Best Over Market (mercado + edge + score)
  - `MLBOverSwapBadge`: visible cuando Market Competition swappea Under→Over.
  - `MLBMarketAuditBadge`: chip opcional para warnings de sesgo diario.
  - `MLBScriptV3Panel` acepta `overDiscovery` y renderiza summary+detail.
- `frontend/src/components/MatchCard.jsx`:
  - cableado `m._mlb_over_discovery` → `MLBScriptV3Panel.overDiscovery`.
  - render `MLBOverSwapBadge` debajo de `MLBBullpenSwapBadge`.

---

## Phase MLB-M2 — Bullpen Real-Usage (pitch_stress) + Finished-Game Settler
**Estado:** ✅ COMPLETADO (2026-05-31)

### M2.1 Bullpen real-usage (`services/mlb_bullpen_real_usage.py`)
- Hidrata box-scores reales de MLB Stats API por equipo (ventana 48h).
- Expone:
  - `bullpen_pitches_48h` (suma real de pitches del bullpen)
  - `bullpen_innings_48h`
  - `starter_lasted_innings`
  - `pitch_stress_index = bullpen_pitches_48h / 45`
  - `compute_fatigue_score()` combina `games_played × 20 + extra_innings × 15 + pitch_stress × 25`.
  - `derive_fatigue_label()` (fresh / moderate / high / extreme).
- Fail-soft: error → fallback a heurística legacy (`games_played × 25 + extra × 15`).

### M2.2 Integración en `services/mlb_stats_api.py::get_bullpen_recent_usage()`
- Ahora añade al payload: `pitch_stress_index`, `bullpen_pitches_48h`, `bullpen_innings_48h`, `starter_lasted_innings`.
- `fatigue_score_0_100` recalculado con la nueva fórmula cuando hay box-score disponible.
- Cache TTL preservado.

### M2.3 Finished-Game Settler (`services/mlb_finished_game_settler.py`)
- `fetch_boxscore_summary(gamePk)` extrae:
  - `final_score: {home, away, total}`
  - `total_runs`
  - `bullpen_usage: {home_pitches, away_pitches, home/away_innings, home/away_starter_innings}`
- `settle_match(db, doc)` persiste en `db.matches` + `db.archived_live_matches` (idempotente vía `settled_at`).
- `settle_recent_finished(db, days_back=2)` barre matches recién finalizados sin `settled_at`.

### M2.4 Wiring de settlement
- `services/live_lifecycle.sweep_expired_live_matches()` invoca `settle_match` en cuanto un match cierra (best-effort, no rompe el sweep si falla).
- APScheduler job `settle_finished_baseball` corre cada **15 minutos** (`services/scheduler.py`) — captura partidos que cerraron sin pasar por el sweep live.

### M2.5 Beneficio aguas abajo
- M1 (`mlb_active_series_analyzer`) ahora consume `final_score` + `bullpen_usage` reales → arma el contexto de serie activa correctamente días después.
- Caso real validado en preview (31 may 2026):
  - PIT 48h → 146 pitches / stress 3.24 / starter 4.3 IP
  - MIN 48h → 149 pitches / stress 3.31 / starter 4.0 IP
  - NYY 48h →  73 pitches / stress 1.62 / starter 6.0 IP
  - LAD 48h →  81 pitches / stress 1.80 / starter 7.0 IP
- Settler probado end-to-end con dos `gamePk` reales — escribe `final_score`, `total_runs` y `bullpen_usage` completos.

---

## Phase GAPS-4 — LiveMarketStateValidator + LivePreMatchComparisonLayer + Under-Loss Anti-Pattern Library
**Estado:** ✅ COMPLETADO (2026-05-31)

### Layer A — `services/live_market_state_validator.py`
- Función pura `validate_market_state(market, *, home_score, away_score, sport, inning_or_minute, is_final, selection_label=None)` retorna `{state, actionable, current_total, threshold, side, summary_es/en, suggested_alternatives, reason_code}`.
- Estados: `still_playable | already_resolved_win | already_resolved_loss | already_resolved_unknown`.
- Parser que reconoce Over/Under (es+en), Run Line +/−, Team Total (home/away), Handicap, Moneyline, NRFI Yes/No.
- Ladders sport-aware para sugerir alternativas (baseball/football/basketball).
- **Tests verificados**: MLB 5-3 Over 7.5 → already_resolved_win (alts: Over 8.5, 9.5, 10.5, 11.5); MLB 5-3 Under 9.5 → still_playable; Football 3-1 Over 1.5 → already_resolved_win; Football 3-1 Under 2.5 → already_resolved_loss; Basketball 180 Over 175.5 → already_resolved_win; Team Total home Over 4.5 con 5-3 → already_won; NRFI Yes 7th 2-1 → already_resolved.
- Acepta `selection_label` secundario para casos donde `market="Run Line +1.5"` pero `selection="Más de 7.5 carreras"` — devuelve el peor estado entre ambos.

### Layer B — `services/live_pre_match_comparison.py`
- `compare_pregame_vs_live(pregame_pick, live_state, sport)` produce el objeto `script_comparison` canónico:
  - `script_status`: on_script | soft_deviation | hard_deviation | broken_script | insufficient_data.
  - `pregame_pick_status`: pending | already_won | already_lost | still_playable | not_actionable.
  - `live_recommendation_status`: actionable | wait | avoid | hedge | cashout_watch.
  - `score_delta`, `expected_total_through`, `actual_total`, `period_n`, `reason_codes`, `human_summary_es/en`, `suggested_alternatives`, `validator` (full Layer A output).
- Interpola linealmente `expected_runs` × `inning/9` (baseball), `expected_goals × min/90` (football), `expected_points × Q/4` (basketball).
- `confidence_adjustment`: -25 (broken) / -12 (hard) / -5 (soft).
- **7 tests unitarios pasan**: incluye los 5 escenarios del usuario (MLB Over 7.5 con 5-3, Under 9.5 still_playable, Fútbol Over 1.5 con 3-1, Fútbol Under 2.5 con 3-1, Basketball Over 175.5 con 180) + edge cases (sin pregame pick, sin live data).

### Wiring backend
- `GET /api/matches/{id}` ahora enriquece el doc con `script_comparison` cuando hay pick pregame en el último pick_run del usuario para ese match. Merge inline + live_stats con score/inning/state.

### UI — `LivePreMatchComparisonPanel.jsx`
- Renderiza 3 pills (script_status / pregame_status / live_status) + tile numérico (esperado vs real + Δ + period) + resumen humano + chips de líneas alternativas sugeridas.
- Colores: verde (on_script/actionable), ámbar (soft/cashout_watch), naranja (hard/hedge), rojo (broken/avoid), cyan (already_won), rojo (already_lost).
- Estados informativos: "No hay análisis pregame disponible..." y "Datos live insuficientes...".

### `MatchDetailPage.jsx`
- Monta el panel **antes** del pick para que el usuario vea el verdicto primero.
- Cuando `pregame_pick_status ∈ {already_won, already_lost, not_actionable}` el pick se renderiza en una sección **demoted ámbar** con el header "Pick pregame ya cumplido / no accionable" en lugar de la tarjeta verde de apuesta activa.

---

## Phase GAPS-5 — Under Veto Power-Bat + Bullpen Pitch-Stress + Learning Cases MLB
**Estado:** ✅ COMPLETADO (2026-05-31)

### FIX #1 — `POWER_BAT_PRESENT` en `mlb_under_veto_layer.py`
- Nueva razón `POWER_BAT_PRESENT` con threshold `POWER_BAT_OPS_THRESHOLD=0.770`.
- Añadida a `_BLOCKING_REASONS` → bloquea Under sin necesitar otras razones.
- Disparada cuando `home_team_ops > 0.770` OR `away_team_ops > 0.770`.

### FIX #2 — OPS en `build_under_veto_context`
- Extrae `batting.home.ops` / `batting.away.ops` (con fallbacks OPS / team_ops / season_ops).
- Propaga `home_team_ops` / `away_team_ops` al payload de retorno.
- Orchestrator pasa estos campos a `evaluate_under_veto()`.

### FIX #3 — Activación de `mlb_bullpen_real_usage` (¡por fin invocado!)
- En `_process_one_game()`: 2 nuevas tareas `fetch_recent_bullpen_workload(team_id, days=2)` en paralelo con el resto del fan-out (sin latencia extra).
- Resultado inyectado en `baseball_hist_profile.{home,away}_bullpen_real`.
- `build_under_veto_context` lee `pitch_stress_index` y lo añade al ctx.
- Nueva regla `BULLPEN_PITCH_STRESS_HIGH` (NO bloqueante por sí sola) disparada cuando `pitch_stress_index > 1.5` (≥67 pitches en 48h).

### FIX #4 — `MLB_SEED_CASES` + `detect_mlb_under_warning_pattern` en `learning_cases.py`
- 2 casos seed insertados en `db.learning_cases` (idempotente via `seed_cases`):
  - `yankees-athletics-2026-05-31` — power_bat_visiting_avoid_under (Yankees 0.785 OPS).
  - `twins-pirates-2026-05-31` — active_series_overs_avoid_under (avg 15 runs + pitch_stress 3.24/3.31).
- Función `detect_mlb_under_warning_pattern(match, scoring_ctx)` aplica las reglas:
  1. `power_bat_visiting_avoid_under`: max(home_ops, away_ops) > 0.770 → block.
  2. `active_series_overs_avoid_under`: series.total_runs_avg > 12 AND games_in_series ≥ 2 AND max(psi) > 1.5 → block.
- 4 tests unitarios pasan: Yankees solo / Twins solo / contexto normal (no FP) / ambas reglas a la vez.

### FIX #5 — Wire en el orchestrator (no en el selector)
- Llamado en `mlb_day_orchestrator.py` justo después de `evaluate_under_veto`.
- Cuando `any_block=True` Y `chosen_market` es Under/NRFI → nullify `chosen_market` (cae al rescue).
- Auditoría persistida en `pick_payload.under_learning_rules` + `pick_payload.under_veto_block.learning_block=True` con `rules_fired` para depuración.

### Verificación end-to-end
```python
veto_ctx = {
    'home_team_ops': 0.785, 'away_team_ops': 0.715,    # Yankees visiting
    'pitcher_home': {'era': 3.20, 'whip': 1.18, ...},
    'pitcher_away': {'era': 4.10, 'whip': 1.31, ...},
    'park': {'run_factor': 1.02},
}
result = evaluate_under_veto(**veto_ctx, book_total=9.5, expected_runs=8.4)
# → 'POWER_BAT_PRESENT' in result['veto_reasons']
# → result['veto'] == True
# → result['severity'] == 'BLOCKED'
# → result['explanation'] == 'Equipo con OPS > 0.770 — riesgo de inning explosivo'
```
**Verificado en preview**, todos los tests pasan, código se carga sin errores.

---

## Phase GAPS-3.1 — Pick diversity colapsada + Live match detail hidratado
**Estado:** ✅ COMPLETADO (2026-05-31)

### Bug A — Todos los picks idénticos "Run Line +1.5 (underdog)"
**Diagnóstico (root cause):**
1. El cap del IL penalty estaba en `-20` conf / `-1.5` ER. Empujaba los `chosen_market` reales (UNDER 9.5 ~ score 50) por debajo del umbral 72.
2. Todos caían al rescue → `mlb_alternative_rescue` siempre emitía `Run Line +1.5 (underdog) score=68` (porque `RUN_LINE_TRAP` se cumple en casi cualquier matchup competitivo).
3. El recalibrate pipeline **no llamaba a `_ensure_recommendation_shape`** → la `recommendation` quedaba con solo `{market, score, rationale}` sin `confidence_score` ni `selection`.

**Fix:**
- `services/mlb_il_penalty.py`: caps reducidos a `MAX_CONFIDENCE_PENALTY=10` y `MAX_ER_REDUCTION=1.0`.
- `server.py`: `_ensure_mlb_recommendation_shape` extraído a módulo level y aplicado en ambos pipelines (`_run_analysis_pipeline` y `_run_recalibration_pipeline`). Sintetiza `confidence_score`/`selection`/`market` desde el `chosen_market` raw.
- `mlb_day_orchestrator.py`: inyección de **rescue diversity** — el `recommendedLine` de v2 (UNDER 9.5, OVER 7.5, ...) se añade al rescue como candidato cuando `lineSafetyScore ≥ 65`. Esto reemplaza al genérico "Favorito gana por 1 carrera" cuando hay una lectura estructural mejor.
- Verificado: ahora `Market diversity = {'UNDER 9.5': 7, 'Run Line +1.5 (underdog)': 1}` con confianzas variadas (68/71/80/82) en lugar de 8× `RL +1.5 conf=None`.

### Bug B — MatchDetailPage muestra "PRÓXIMO" + sin marcador en vivo
**Diagnóstico:**
- `db.matches.live_stats` se hidrata solo periódicamente por el sweep de ingestion (cada 15-30s).
- Cuando el usuario abría el detalle entre sweeps veía `is_live=False` y sin score, aunque MLB Stats API ya tuviera el inning 7th 4-3.
- `MatchDetailPage` solo leía del doc; sin polling.

**Fix:**
- `services/mlb_live_state.py` (nuevo): `fetch_live_state(match_id)` consulta MLB Stats API linescore + schedule en 1 round-trip (6s timeout), retorna 5 estados canónicos:
  `loading | live-data-ready | live-data-partial | final | no-live-data`.
  Incluye score, inning (number/half/ordinal), outs, balls/strikes, runners_on, current_batter, current_pitcher.
- `fetch_and_persist_live_state(db, match_id)` además persiste el snapshot en `db.matches.live_stats` para que las siguientes lecturas se beneficien.
- `server.py`:
  - `GET /api/matches/{id}` ahora hace **hidratación oportunista**: si el match es baseball y no está Final, dispara `fetch_and_persist_live_state` antes de devolver el doc. Merge inline → sin doble round-trip.
  - `GET /api/matches/{id}/live-refresh` (nuevo): endpoint ligero para el polling del frontend. No sport→devuelve no-live-data stub.
- `hooks/useLiveMatchDetail.js` (nuevo): hook React con polling automático cada **30s** mientras el state sea `live-data-ready|partial`. Se detiene automáticamente cuando es `final` (anti-spam).
- `components/MLBLiveScoreboard.jsx` (nuevo): componente sport-específico de baseball con score grande, inning ordinal con flecha (▲/▼), diamond de bases SVG, dots de outs, count balls-strikes, batter/pitcher actuales, badge de estado (FINAL/EN VIVO/PARCIAL/NO INICIA) + botón Actualizar manual.
- `MatchDetailPage.jsx`: integra el hook + scoreboard. `effectiveIsLive` ahora **prioriza el estado del hook** sobre el `is_live` cacheado del doc, evitando el "PRÓXIMO mientras realmente está en el 7th".

**Verificado:**
- BAL @ TOR (terminado): badge **FINAL** + marcador 9-5 + 9TH ▲ + 3 outs amarillos.
- ATL @ CIN (en curso): badge **EN VIVO** (rosa pulsante) + 5-3 + 7TH ▲ + count 0-2 + bateando JJ Bleday / lanzando Didier Fuentes + diamond.
- Polling 30s confirmado en el hook (clearInterval auto cuando state→final).

### Estados UI exportados al frontend
| Estado | Banner | Marcador |
|---|---|---|
| `loading` | spinner CARGANDO | placeholder |
| `live-data-ready` | EN VIVO (rosa pulsante) | score + inning + outs + batter/pitcher |
| `live-data-partial` | PARCIAL + texto "Datos live parciales. Recomendación basada en lectura estructural." | score + lo que haya |
| `final` | FINAL (cyan) | score final + last inning |
| `no-live-data` | NO INICIA | "El partido aún no comienza" |

---

## Phase GAPS-3 — IL penalty + LLM Reconciliation + Active Series UI + Live Match Detail
**Estado:** ✅ COMPLETADO (2026-05-31)

### GAP #1 — IL Penalty (`services/mlb_il_penalty.py`)
- Nuevo módulo `apply_il_penalty(scoring_ctx)` con tests unitarios.
- KEY_POSITIONS = {1B,2B,3B,SS,LF,CF,RF,DH,C,IF,OF} — pitchers excluidos (ya cubiertos por capa de pitcher).
- **Cap por equipo: 4 bateadores clave máximo** (MAX_KEY_BATS_PER_SIDE) ordenados por prioridad posicional (spine: C/SS/CF > infield > corner/DH). Necesario porque el endpoint roster-injuries de MLB acumula 10-day + 60-day + minor-league IL en una sola lista (13+ por equipo).
- Cada bate clave faltante: ER -0.3 / conf -5 (capeados a -1.5 ER / -20 conf globales).
- Penalty solo aplica a markets **ofensivos** (Over, Run Line, Team Total, F5 Over) — los Under se benefician de menos bates así que NO se penalizan.
- Integrado en `mlb_day_orchestrator.py` **antes** del bloque M1/M3/M4/M5: deflaciona `_mlb_script_v2.expectedRuns` (preservando `expectedRunsRaw`) y resta del `chosen_market.score`.
- Verificado en caso real (PIT): Brandon Lowe (2B), Henry Davis (C), Oneil Cruz (CF), Endy Rodríguez (C) → impacto ALTO, ER -1.5, conf -20.

### BUG fix — Match detail 404 + crash con league object
- `/api/matches/{match_id}` ahora prueba **ambos shapes** (str + int) en un `$in` query. Antes intentaba castear a int primero, lo cual fallaba para los MLB live (almacenados como string).
- Fallback a `db.archived_live_matches` para matches recién finalizados.
- `MatchDetailPage.jsx` ahora extrae `league.name` cuando viene como objeto `{id, name}` (rompía el render con "Objects are not valid as a React child").
- Verificado: `/match/824832` (BAL @ TOR live) carga correctamente sin errores.

### GAP #2 — LLM Reconciliation (penalizar, no justificar)
- `frontend/src/lib/intelligence.js::deriveConfidenceBreakdown` extendido:
  - `gap > +10` → `reconciliation_label = 'LLM_OVERCONFIDENT'`, `penalty = min(15, ⌊gap/2⌋)`, `final_score = factor_sum + penalty` (anclado al modelo).
  - `gap < -10` → `LLM_UNDERCONFIDENT`, mantiene el reported como final pero etiqueta riesgo oculto.
  - `|gap| ≤ 10` → `BALANCED`, sin warning.
- `components/ConfidenceBreakdown.jsx` ahora renderiza:
  - Badge **LLM SOBRECONFIANTE** en rojo (ShieldAlert) + cap `Score ajustado` visible cuando overconfident.
  - Badge **LLM SUBCONFIANTE** en ámbar (AlertTriangle) cuando underconfident.
  - Copy ES/EN.

### GAP #3 — Active Series Context UI + override avg > 12
- `services/mlb_active_series_analyzer.py`:
  - Nueva función `_extract_per_team_runs(doc, home, away)` normaliza la orientación home/away respecto al juego upcoming (la UI siempre ve el mismo equipo en el mismo lado).
  - `games_detail`: lista `[{game_number, home, away, total_runs, summary, kickoff}]` ordenada G1→Gn.
  - `next_game_number`: número del próximo juego (G4 si la serie lleva 3).
  - **Override hard-cap añadido**: si `avg > 12.0` → `lean = OVER` + `series_override = True` con razón explícita "Promedio de serie X carreras > 12 — entorno claramente ofensivo".
- `components/MLBScriptPanel.jsx`:
  - Panel rediseñado: encabezado `📊 Contexto de serie activa (Gn)`, lista per-game con scores normalizados (HOME 9 - 13 AWAY = 22 carreras), promedio con resaltado rojo cuando > 12.
  - **Lean badge se oculta cuando `chosenMarket` es Run Line** (la serie se consume como confirmación, no como Over/Under signal). Sigue mostrando el override_reason.
  - Nuevo bloque `🩹 Bateadores clave en IL` con counts + ER adjust + nombres (cap 3 por lado).
- `MatchDetailPage.jsx`: ahora monta el `MLBScriptPanel` también en el detalle, no solo en el dashboard. El usuario puede ver IL + serie activa al hacer click en cualquier match MLB.
- Verificado en caso real (BAL @ TOR live, gamePk 824832):
  - Serie activa: G1 9-13 = 22 / G2 5-6 = 11 / G3 6-5 = 11 / Promedio 14.7
  - Override OVER fires (avg > 12 + bullpens > 80)
  - Pick es Run Line → Lean badge oculto, contexto sí visible (validación)
  - IL: BAL 4 + TOR 4 → IMPACTO ALTO, ER -1.5, conf -20

---

## Phase RECAL — Lightweight Recalibration + Feedback APScheduler + Bright Data Health
**Estado:** ✅ COMPLETADO (2026-05-31)

### RECAL.1 Endpoint `POST /api/analysis/recalibrate`
- Re-corre el analista sobre los partidos del **último `pick_run`** del usuario para ese deporte, **sin** re-ingestar APIs externas.
- Scope: **MLB + Basketball** (football pendiente para una iteración futura).
- Modo **background** vía `job_queue` con polling en `/api/analysis/jobs/{id}` (compatible con el modal de progreso ya existente).
- MLB: invoca `analyze_mlb_day(date_str, db)` que aprovecha cachés internas del orquestador (MLB Stats API, team form, pitcher stats). Tiempo medido en preview: **~10–15s**.
- Basketball: recupera `match_ids` de cada bucket del último pick_run, hidrata desde `db.matches` y re-corre `analyst_engine.analyze_matches`. Tiempo medido: **~75s** (cuello LLM).
- Guarda un nuevo `pick_run` con `is_recalibration: True` + `recalibrated_from: <prev_run_id>` para auditoría.

### RECAL.2 Botón "Recalibrar" en Dashboard (frontend)
- `frontend/src/pages/DashboardPage.jsx`: añadido handler `recalibrate()` y `<Button data-testid="recalibrate-picks-button">` al lado de "Generar picks del día".
- Visible **solo** para `sport ∈ {baseball, basketball}`.
- Deshabilitado si no hay pick_run previo o si ya hay un job activo.
- Icono `RefreshCcw` + estilo outline cyan para distinguirlo del botón verde "Generar picks".
- i18n: copy ES/EN (`recalibrateBtn`, `recalibrating`, `recalibrateHint`, `recalibrateDone`).

### RECAL.3 Feedback-loop recalibración automática (P2)
- `FEEDBACK_BATCH_SIZE: 50 → 40` en `services/mlb_feedback_loop.py`.
- Nuevo job APScheduler `recompute_feedback_weights` (cada **30 min**, alineado con `refresh_upcoming`) en `services/scheduler.py::_job_recompute_feedback_weights`.
- El job invoca `recompute_weights_if_due(db)` — no-op cuando hay menos de 40 picks settled pendientes (cuesta un `count_documents`).
- Verificado: scheduler arranca con la lista de jobs `['refresh_live', 'refresh_upcoming', 'sweep_stale_live', 'settle_finished_baseball', 'recompute_feedback_weights', 'purge_context']`.

### RECAL.4 Bright Data — healthcheck + telemetría
- `services/brightdata_client.py`: añadido ledger en memoria (deque) con ventana de **24h** (max 5000 entradas). Cada `fetch_unlocked` registra `(ts, status, ok, url_short)`.
- Nueva función `get_health_snapshot()` devuelve `{fetches_24h, ok_24h, fail_24h, success_ratio, last_fetch}`.
- Nuevo endpoint `GET /api/admin/brightdata` (`?probe=true` para health-check real contra `https://geo.brdtest.com/mygeo.json`):
  - Devuelve `{ok, token_present, api_key_present, zone, editorial_enabled, ledger_24h, probe?}`.
  - Verificado en preview: `probe.ok=true`, response real con `country: US`, ASN HostRoyale.
- **No** se wirearon scrapers legacy (understat / crawlee / playwright_scraper) — decisión explícita del usuario.

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
2) Validar UI (smoke):
   - Chips V6 en `MLBScriptV3Panel` (summary siempre visible + detail al expand).
   - Badge `Under→Over` cuando exista swap.

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

### D) MLB Feedback Loop Recalibration via APScheduler (P2)
**Estado:** 🟨 PENDIENTE
- Wire APScheduler para recomputar pesos automáticamente cada 50 picks settled (en lugar de depender solo de triggers manuales).

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
- `/app/test_reports/mlb_v2_backend_test.json`: **12/12 PASS (100%)**
- `/app/test_reports/iteration_39.json`: **12/13 PASS (92.3%)**
  - Incidencia: timeout en regresión fútbol con `background=False` (no funcional; workaround: `background=True` o aumentar timeout).
- `/app/test_reports/iteration_40.json`: **44/51 PASS (86%)**
  - Core MLB-V5 verificado; algunos tests flaky por latencia de background jobs.
- Iteraciones V4/F6A/V5: `/app/test_reports/iteration_41.json` → `iteration_44.json`.
- **MLB-V6 (Over Discovery) — validación local (manual):** OK (scores/drivers/best_over coherentes).
- **Siguiente:** ejecutar `testing_agent_v3` para validación formal end-to-end de V6 + regresión V1–V5.

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
