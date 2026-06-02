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
