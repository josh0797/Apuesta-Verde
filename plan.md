# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** + **MLB Under Confidence Floor (P0)** + **F6C Auto-Settle (P1)** + **MLB Statcast Deep Integration (Phase 9/10) + Offensive Pressure Base (Objetivo 2)** (ACTUALIZADO)

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

- **(✅ COMPLETADO)** **MLB-V6 — Totals Prob Fix + Visible Picks + Over Discovery / Market Audit (V6 UI + Backend)**:
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

- **(✅ COMPLETADO — P0)** **MLB Under Confidence Floor (Moneyball guardrail)**:
  - Regla:
    - Solo para `sport == "baseball"`, **market Under (no team total, no NRFI)**.
    - Solo cuando `edge is not None`.
    - Si `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75) → degrada a `WATCHLIST`.
    - Marca el pick con `pick["_conf_floor_demoted"] = True`.

- **(✅ COMPLETADO)** **UI/summary: bucket de democión por floor**:
  - `server.py` expone `summary.conf_floor_demoted`.

- **(✅ COMPLETADO — P1)** **F6C Auto-Settle MLB (sin intervención del usuario)**:
  - Nuevo módulo `services/mlb_results_settler.py` + wiring APScheduler.

- **(✅ COMPLETADO — NUEVO P0)** **MLB Statcast como “capa de confirmación/riesgo” (Phase 9/10)**:
  - Ajustes **ponderados** por `data_quality` (Statcast no es motor principal):
    - `strong` → 60%
    - `partial/thin` → 35%
    - `missing` → 0%
  - Persistencia de auditoría: `pick_payload["advanced_adjustments"]` incluye `raw_conf_delta`, `weighted_conf_delta`, `weight_factor_used`, breakdown y reason_codes.
  - Integración live: `mlb_explosive_inning_engine` añade contribución `statcast_contact` (cap ±8) + reason codes.

- **(✅ COMPLETADO — NUEVO P0)** **MLB Offensive Pressure Base (Objetivo 2)**:
  - Nuevo módulo `services/mlb_pressure_base.py`.
  - Detecta Under frágil cuando hay **muchos hits pero pocas carreras**.
  - Basado en `baseballHistoricalProfile.recentRunSplit`/`onBaseProfileL5` (mirror) + (si existe) hits live.
  - Wiring en orchestrator:
    - `pick_payload["pressure_base"]` + `pick_payload["pressure_base_impact"]`
    - Ajustes conservadores sobre `recommendation.confidence_score` y `fragility.score`.

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
**Estado:** ✅ COMPLETADO

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy)
**Estado:** ✅ COMPLETADO

---

## Phase MLB-BatchB — Statcast Adapter (pybaseball + Bright Data + TheStatsAPI)
**Estado:** ✅ CORE + Phase 9/10 COMPLETADAS (2026-06-03). Fase 11/13 pendientes.

### Fix 2 — Batch B: MLB Statcast Adapter (Fases 1-8 + 12 + 14)
**Estado:** ✅ COMPLETADO
- Snapshot persistido en `pick_payload["advanced_stats_snapshot"]`.
- Fuentes y cache hit/miss reportados en `pipeline_meta.external_sources.mlb_advanced_stats`.

### Phase 9 — Deep integration en scorers (Statcast → ajustes ponderados)
**Estado:** ✅ COMPLETADO (P0)

**Implementación real (backend):**
- Archivo: `services/mlb_day_orchestrator.py`
  - Tras persistir `advanced_stats_snapshot`, ejecuta `compute_all_advanced_adjustments(pick_payload)`.
  - Aplica ajuste **ponderado** al `recommendation.confidence_score` según `data_quality`:
    - strong=0.60, partial/thin=0.35, missing=0.0.
  - Guarda auditoría completa en `pick_payload["advanced_adjustments"]`:
    - `data_quality`, `weight_factor_used`, `raw_conf_delta`, `weighted_conf_delta`, `raw_breakdown`, `reason_codes`, `summary`.
  - Propaga reason codes al `pick_payload.reason_codes`.
  - Fail-soft: si no hay snapshot útil, no altera el score.

**Criterios de éxito logrados:**
- Ajustes conservadores (Statcast como confirmación/riesgo, no motor principal).
- Persistencia de metadata para UI/explicabilidad y auditoría.
- 0 crashes si faltan datos.

---

### Phase 10 — Statcast en `mlb_explosive_inning_engine.py`
**Estado:** ✅ COMPLETADO (P0)

**Implementación real:**
- Archivo: `services/mlb_explosive_inning_engine.py`
  - Nuevo detector puro: `_detect_statcast_contact_context(metrics, pitching_side, batting_side)`.
  - Lee `metrics["advanced_stats_snapshot"]` (opcional) y añade contribución `statcast_contact` a `score_contributions`.
  - Ajuste capado a ±8; reason codes añadidos a `reason_codes`.
  - Fail-soft: snapshot ausente → contribución 0 sin alterar outputs.

---

## Objetivo 2 — `services/mlb_pressure_base.py` (Presión ofensiva base)
**Estado:** ✅ COMPLETADO (P0)

**Implementación real:**
- Nuevo archivo: `services/mlb_pressure_base.py`
  - `calculate_team_pressure_base()` y `calculate_match_pressure_context()`.
  - Umbrales:
    - HIGH: hits_L5 ≥ 9.0 y runs_L5 ≤ 3.5
    - MOD:  hits_L5 ≥ 8.0 y runs_L5 ≤ 4.0
    - LOW:  hits_L5 ≤ 6.5 y runs_L5 ≤ 3.5
    - si no cumple → NEUTRAL
  - Considera hits live cuando existen (`RC_LIVE_HIT_ACCELERATION`).
  - Helper: `derive_pressure_impact_for_under_pick()` devuelve deltas conservadores para Under/Over.

**Wiring real:**
- `services/mlb_day_orchestrator.py`:
  - Adjunta `pick_payload["pressure_base"]`.
  - Aplica `pressure_base_impact` a `recommendation.confidence_score` y `fragility.score` (si están presentes).
  - Propaga reason codes.

---

## 3) Next Actions

### A) Iteración MLB Statcast — Fase 11 y Fase 13 (P1)
**Estado:** 🟨 PENDIENTE
1) **Fase 11** — `mlb_real_stats_verifier.py`:
   - Detectar Ghost Edges con discrepancias xERA vs ERA / xwOBA vs wOBA.
   - Flags tipo `ERA_UNDERSTATES_RISK` y payload `pitcher_era_vs_xera`.
2) **Fase 13** — UI:
   - Sección colapsable “MLB Advanced Stats” mostrando los 4 bloques del snapshot.
   - Badges por fuente (`pybaseball` / `thestatsapi` / `brightdata`) y `data_quality`.

### B) Bright Data Unlocker (P0 bloqueado)
**Estado:** 🟨 PENDIENTE / BLOQUEADO
- Requiere `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE`.

### C) Basketball Historical Detail (P1)
**Estado:** 🟨 PENDIENTE
- Implementar perfil histórico y rescue layer equivalentes a MLB.

### D) Fix 2C (P2) — Persistencia live como async
**Estado:** 🟨 PENDIENTE

### E) Football deep-live parity (P3)
**Estado:** 🟨 PENDIENTE

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

- **MLB Statcast Deep Integration (Phase 9/10) — ✅ cumplido**
  - Statcast actúa como **capa de confirmación/riesgo** (no motor principal).
  - Ajustes ponderados por `data_quality` (60/35/0).
  - Se guardan `raw_conf_delta` y `weighted_conf_delta` en `pick_payload["advanced_adjustments"]`.
  - `mlb_explosive_inning_engine` incorpora `statcast_contact` (±8) sin IO.
  - Cero crash si faltan datos / provider falló.

- **MLB Offensive Pressure Base (Objetivo 2) — ✅ cumplido**
  - `pressure_base` presente cuando hay `recentRunSplit/onBaseProfile` (o mirror en `baseballHistoricalProfile`).
  - Clasificación HIGH/MODERATE/LOW/NEUTRAL según umbrales.
  - Under picks con “muchos hits / pocas carreras” aumentan fragility y degradan confidence de forma conservadora.

- **MLB Under Confidence Floor — ✅ cumplido**
  - Un pick MLB Under no puede quedar recomendado si `confidence_score < 75` con odds.

- **F6C Auto-Settle — ✅ cumplido**
  - Evaluaciones pending se resuelven automáticamente cuando hay `final_score`.

### Testing status
- **Suite actual:** 604 tests PASS.
- **Validación adicional:** `testing_agent_v3` backend OK (endpoints OK, boot limpio, fail-soft confirmado).
