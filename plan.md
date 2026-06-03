# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** + **MLB Under Confidence Floor (P0)** + **F6C Auto-Settle (P1)** + **MLB Statcast Deep Integration (Phase 9/10) + Offensive Pressure Base (Objetivo 2) + Sabermetrics Layer (Phase 9.6) + Ghost-Edges Statcast (Phase 11) + Market Selection Intelligence (Phase 13.1) + UI Advanced Stats/Sabermetrics (Phase 13.2)** (ACTUALIZADO)

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

- **(✅ COMPLETADO — NUEVO P0)** **MLB Sabermetrics Layer (Phase 9.6 — WAR/OPS/FIP)**:
  - Nuevo módulo `services/mlb_sabermetrics_layer.py`.
  - Calcula perfiles:
    - OPS (OBP+SLG cuando aplique, tiers ELITE/STRONG/AVERAGE/WEAK)
    - FIP (directo, por fórmula con constante configurable, o proxy vía xERA)
    - WAR impact (cuando existe data; fail-soft si no)
  - Produce contexto canónico `pick_payload["sabermetrics"]` con:
    - `match_edges` (ops/fip/war/overall)
    - `adjustments` (pitcher_quality, total_runs, fragility, script_survival, run_line_support)
    - `reason_codes` y `summary`
  - Integración en `mlb_day_orchestrator.py`:
    - Aplica delta ponderado por `data_quality` (60/35/0) a `recommendation.confidence_score`.
    - Guardado de auditoría en `pick_payload["sabermetrics_audit"]`.
  - Guardrail: `weighted_conf_delta` capado a ±15; sabermetría **no** convierte picks débiles en fuertes por sí sola.

- **(✅ COMPLETADO — NUEVO P1)** **Fase 11 — Ghost-Edges con xERA/xwOBA (Verifier)**:
  - `services/mlb_real_stats_verifier.py`:
    - Nuevo kwarg `advanced_stats_snapshot` (backwards compatible).
    - Flags:
      - `ERA_UNDERSTATES_RISK` (ERA muy baja vs xERA alta → riesgo oculto, penaliza Under)
      - `ERA_OVERSTATES_RISK` (ERA alta vs xERA baja → ghost-edge para Over)
      - `PITCHER_XWOBA_WARNING` (xwOBA allowed elevada contra Under)
      - `GHOST_EDGE_HARD_CONTACT_VS_UNDER` (barrel/hard-hit elevada contra Under)
      - `GHOST_EDGE_TEAM_XWOBA_VS_UNDER` (ambos equipos con xwOBA alta contra Under)
    - Cap de `confidence_penalty` actualizado a **55**.
  - `mlb_day_orchestrator.py` pasa `advanced_stats_snapshot` al verifier.

- **(✅ COMPLETADO — NUEVO P1)** **Phase 13.1 — MLB Market Selection Intelligence**:
  - Nuevo módulo `services/mlb_market_selection.py` (pure/fail-soft) con `select_protected_market(pick_payload)`.
  - Selección final del mercado más protegido usando:
    - `pressure_base`, `advanced_adjustments`, `sabermetrics/sabermetrics_audit`, `model_verification.discrepancies` (ghost edges), `pitcher_quality_score`, `fragility`, `script_survival`, `bullpen_risk`, `odds_range`.
  - Guardrails Moneyball:
    - Bloquea Run Line -1.5 si `marginProjection < 2.0` o `runLineCoverProb < 0.50` → prefiere Moneyline.
    - Over sin odds → `Manual Odds Review`.
    - Under con `HIGH_PRESSURE` → swap a F5 Under si abridores sostienen, si no → watchlist.
    - Ghost-edge contra el lado → watchlist o swap a alternativa protegida.
  - Output canónico persistido en `pick_payload["market_selection"]` y reason codes propagados.

- **(✅ COMPLETADO — NUEVO P1)** **Phase 13.2 — UI colapsable “MLB Advanced Stats” + “Sabermetría” + “Selección de mercado”**:
  - Nuevo componente frontend `frontend/src/components/MLBAdvancedStatsPanel.jsx`.
  - Integrado en `MatchCard.jsx` (gated por `sport === 'baseball'`).
  - Muestra:
    - Presión ofensiva (tier + hits/runs L5).
    - Deltas auditados Statcast/Sabermetría.
    - Bloque de selección de mercado (`market_selection`): recomendado, alternativa protegida, por qué, por qué no, reason codes.
    - 4 bloques Statcast: pitchers + teams con métricas y badges de `sources_consulted` y `data_quality`.
    - Bloque Sabermetría: OPS/FIP/WAR por equipo + edges + summary.
  - `data-testid` añadidos para testing y estabilidad.

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
**Estado:** ✅ CORE + Phase 9/10/9.6 + Phase 11 + Phase 13.1/13.2 COMPLETADAS (2026-06-03).

### Fix 2 — Batch B: MLB Statcast Adapter (Fases 1-8 + 12 + 14)
**Estado:** ✅ COMPLETADO
- Snapshot persistido en `pick_payload["advanced_stats_snapshot"]`.
- Fuentes y cache hit/miss reportados en `pipeline_meta.external_sources.mlb_advanced_stats`.

### Phase 9 — Deep integration en scorers (Statcast → ajustes ponderados)
**Estado:** ✅ COMPLETADO (P0)

### Phase 10 — Statcast en `mlb_explosive_inning_engine.py`
**Estado:** ✅ COMPLETADO (P0)

### Phase 9.6 — MLB Sabermetrics Layer (WAR/OPS/FIP)
**Estado:** ✅ COMPLETADO (P0)

### Phase 11 — Real Stats Verifier (Ghost-Edges con xERA/xwOBA)
**Estado:** ✅ COMPLETADO (P1)

### Phase 13.1 — MLB Market Selection Intelligence
**Estado:** ✅ COMPLETADO (P1)

### Phase 13.2 — UI “MLB Advanced Stats” + “Sabermetría” + “Selección de mercado”
**Estado:** ✅ COMPLETADO (P1)

---

## Objetivo 2 — `services/mlb_pressure_base.py` (Presión ofensiva base)
**Estado:** ✅ COMPLETADO (P0)

---

## 3) Next Actions

### A) Bright Data Unlocker (P0 bloqueado)
**Estado:** 🟨 PENDIENTE / BLOQUEADO
- Requiere `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE`.

### B) Basketball Historical Detail (P1)
**Estado:** 🟨 PENDIENTE
- Implementar perfil histórico y rescue layer equivalentes a MLB.

### C) Fix 2C (P2) — Persistencia live como async
**Estado:** 🟨 PENDIENTE

### D) Football deep-live parity (P3)
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
  - Ajustes ponderados por `data_quality` (60/35/0), capados y explicables.
  - `mlb_explosive_inning_engine` incorpora `statcast_contact` (±8) sin IO.

- **MLB Offensive Pressure Base (Objetivo 2) — ✅ cumplido**
  - Under frágil cuando hay muchos hits y pocas carreras.
  - Considera hits live y expone reason codes.

- **MLB Sabermetrics Layer (Phase 9.6) — ✅ cumplido**
  - WAR/OPS/FIP influyen de forma conservadora, explicable y fail-soft.
  - No “fuerzan” Over/RunLine sin confirmación adicional.
  - Auditoría presente en `sabermetrics_audit`.

- **Fase 11 Ghost-Edges Statcast — ✅ cumplido**
  - `mlb_real_stats_verifier` detecta discrepancias xERA/xwOBA y penaliza picks conflictivos.
  - Cap de penalty actualizado a 55.

- **Fase 13.1 Market Selection Intelligence — ✅ cumplido**
  - El engine no solo predice: selecciona mercado protegido basado en presión/ghost-edges/odds.
  - Picks agresivos quedan bloqueados cuando no hay soporte estructural.
  - Salida explicable: por qué este mercado y por qué no otros.

- **Fase 13.2 UI MLB Advanced Stats/Sabermetría/Selección — ✅ cumplido**
  - Panel colapsable MLB con badges de fuentes y calidad.
  - Fail-soft: si no hay datos en el pick, el panel no aparece.
  - Aislamiento: football/basketball no cambian.

- **MLB Under Confidence Floor — ✅ cumplido**
  - Un pick MLB Under no puede quedar recomendado si `confidence_score < 75` con odds.

- **F6C Auto-Settle — ✅ cumplido**
  - Evaluaciones pending se resuelven automáticamente cuando hay `final_score`.

### Testing status
- **Suite actual:** 678 tests PASS.
- **Validación adicional:** `testing_agent_v3` backend+frontend OK (endpoints OK, boot limpio, fail-soft confirmado, UI MLB colapsable verificada).