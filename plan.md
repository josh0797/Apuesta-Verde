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
  - Regla:
    - Solo para `sport == "baseball"`, **market Under (no team total, no NRFI)**.
    - Solo cuando `edge is not None`.
    - Si `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75) → degrada a `WATCHLIST`.
    - Marca el pick con `pick["_conf_floor_demoted"] = True`.

- **(✅ COMPLETADO — NUEVO)** **UI/summary: bucket de democión por floor**:
  - `server.py` expone `summary.conf_floor_demoted`.

- **(✅ COMPLETADO — NUEVO P1)** **F6C Auto-Settle MLB (sin intervención del usuario)**:
  - Nuevo módulo `services/mlb_results_settler.py` + wiring APScheduler.

- **(🟨 NUEVO P0)** **MLB Statcast como “capa de confirmación/riesgo” (Phase 9/10)**:
  - Statcast NO debe ser motor principal; ajustes ponderados por `data_quality`.
  - Guardar metadata: `raw_adjustment` y `weighted_adjustment`.
  - Añadir reason codes nuevos al pick payload para explicabilidad.

- **(🟨 NUEVO P0)** **MLB Offensive Pressure Base (Objetivo 2)**:
  - Detectar “Under frágil” cuando hay **muchos hits pero pocas carreras**.
  - Basado en `baseballHistoricalProfile.recentRunSplit` + (cuando exista) live hits.

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
**Estado:** ✅ FASE CORE COMPLETADA (2026-06-03). **Fase 9/10 pendientes**, Fase 11/13 pendientes.

### Fix 2 — Batch B: MLB Statcast Adapter (Fases 1-8 + 12 + 14)
**Estado:** ✅ COMPLETADO
- Snapshot persistido en `pick_payload["advanced_stats_snapshot"]`.
- Fuentes y cache hit/miss reportados en `pipeline_meta.external_sources.mlb_advanced_stats`.

### Phase 9 — Deep integration en scorers (Statcast → ajustes ponderados)
**Estado:** 🟨 PENDIENTE (P0)

**Contexto actual del repo:**
- Ya existe `services/mlb_advanced_stats_helpers.py` con:
  - `extract_mlb_advanced_context()`
  - `pitcher_quality_advanced_adjustment()`
  - `over_under_advanced_adjustment()`
  - `fragility_advanced_adjustment()`
  - `starter_under_advanced_adjustment()`
  - `compute_all_advanced_adjustments()`

**Objetivo técnico:** aplicar Statcast como capa de confirmación/riesgo sobre:
- `pitcher_quality` (impacto en calidad de abridores)
- `over_under` (apoyo a Over/Under)
- `fragility` (fragilidad global)
- `starter_under` (perfil Under de abridores)

**Regla de ponderación (especificación del usuario):**
- `data_quality == "strong"` → **60%** del adjustment
- `data_quality in ("partial", "thin")` → **35%**
- `data_quality == "missing"` → **0%**

**Metadatos obligatorios por pick:**
- `pick_payload["advanced_adjustments"] = { ... }` (salida de `compute_all_advanced_adjustments`)
- Dentro:
  - `raw_adjustment` (por sub-bloque)
  - `weighted_adjustment` (aplicado al score/conf)
  - `weight_factor_used`

**Cambios concretos (backend):**
1. `services/mlb_day_orchestrator.py`
   - Tras construir `advanced_stats_snapshot`, ejecutar `compute_all_advanced_adjustments(pick_payload)`.
   - Aplicar los ajustes ponderados de forma **conservadora** a:
     - `recommendation.confidence_score` (principal)
     - y/o campos auxiliares como `fragility.score` / `script_survival` cuando aplique.
   - Propagar `reason_codes` nuevos al `pick_payload.reason_codes`.
   - Fail-soft total: si falta snapshot, no cambia nada.

2. Integración en módulos “standalone” (si existen funciones separadas en el repo):
   - Si la arquitectura actual no expone `pitcher_quality_score.py` etc., mantener la integración en orchestrator usando helpers.
   - Si existen bloques equivalentes en `mlb_pregame_analytics*.py` o `mlb_pregame_analytics_v3.py`, ajustar ahí de forma additive (sin romper API).

**Criterios de éxito Phase 9:**
- Ningún pick cambia drásticamente por Statcast (cap + ponderación).
- `advanced_stats_reason_codes` aparecen y son auditables.
- Si `data_quality=missing` → impacto 0.

---

### Phase 10 — Statcast en `mlb_explosive_inning_engine.py`
**Estado:** 🟨 PENDIENTE (P0)

**Objetivo:** mejorar la presión explosiva con señales pregame de contacto duro:
- Reforzar/penalizar la presión cuando Statcast indica:
  - alto barrel/hard-hit (riesgo de inning explosivo)
  - xwOBA elevada (warning)
  - perfil Under (low hard-contact) como amortiguador

**Diseño (fail-soft + pure functions):**
- Extender `evaluate_explosive_inning(metrics)` para aceptar opcionalmente:
  - `advanced_stats_snapshot` o `advanced_ctx` (preferible pasar snapshot completo)
- Agregar reason codes (canónicos ya definidos en `mlb_advanced_stats_helpers.py`):
  - `STATCAST_HARD_CONTACT_SUPPORT`, `BARREL_RISK_ELEVATED`, `PITCHER_XWOBA_WARNING`, etc.
- Ajuste pequeño y capado (ej. ±0..8 puntos) sobre `pressure_score`.

**Implementación sugerida:**
- Nuevo sub-detector interno en `mlb_explosive_inning_engine.py`:
  - `_detect_statcast_contact_context(metrics) -> (pts, reason_codes, human_reasons, flags)`
- Consumir:
  - `pitcher.barrel_pct_allowed`, `pitcher.hard_hit_pct_allowed`, `pitcher.xwoba_allowed`
  - `team.team_barrel_pct`, `team.team_xwoba`
- Integrar en `contribs` y en `reason_codes`.

**Criterios de éxito Phase 10:**
- Engine sigue siendo 100% puro (sin IO) y fail-soft.
- Cuando el snapshot no existe → no cambia outputs.

---

## Objetivo 2 — `services/mlb_pressure_base.py` (Presión ofensiva base)
**Estado:** 🟨 PENDIENTE (P0)

**Motivación:** evitar recomendación Under “controlada” cuando en realidad hay:
- alto volumen de hits + bajo run conversion (señal de “ticking time bomb”).

**Inputs (requisito del usuario):**
- Reusar `baseballHistoricalProfile.recentRunSplit` (fail-soft).
- Considerar `combined_hits` y (si existe) live hits.

**Clasificación (umbrales finales del usuario):**
- `HIGH_PRESSURE` si `hits_avg_L5 >= 9.0` y `runs_avg_L5 <= 3.5`
- `MODERATE_PRESSURE` si `hits_avg_L5 >= 8.0` y `runs_avg_L5 <= 4.0`
- `LOW_PRESSURE` si `hits_avg_L5 <= 6.5` y `runs_avg_L5 <= 3.5`
- si no cumple → `NEUTRAL_PRESSURE`

**API del módulo:**
- `calculate_team_pressure_base(recent_run_split_side: dict, *, live_hits: int | None = None) -> dict`
- `calculate_match_pressure_context(pick_payload_or_match_doc: dict) -> dict`

**Salida canónica:**
```python
{
  "available": bool,
  "home": {"pressure_tier": str, "score": int, "inputs": {...}, "reasons": [...]},
  "away": { ... },
  "combined": {"pressure_tier": str, "score": int, "flags": {...}, "reasons": [...]},
}
```

**Integración:**
1. `services/mlb_day_orchestrator.py`
   - Después de `baseballHistoricalProfile` + `recentRunSplit` mirror, calcular `pressure_base` y adjuntar:
     - `pick_payload["pressure_base"] = ...`
   - Impacto sugerido:
     - Si pick es Under y `combined.pressure_tier == HIGH_PRESSURE` → subir fragility / bajar confidence.
     - Si `LOW_PRESSURE` → leve reducción de fragilidad.

2. Downstream scorers (cuando exista bloque dedicado):
   - `mlb_fragility_score` / `script_survival` / `live analysis` consumen `pressure_base` como “riesgo de conversión tardía”.

**Criterios de éxito Objetivo 2:**
- Detecta escenarios hits altos / runs bajos y los marca.
- Fail-soft: si no hay `recentRunSplit`, retorna `available=False`.

---

## 3) Next Actions

### A) Implementación P0 inmediata — Phase 9 + Phase 10 + Objetivo 2
**Estado:** 🟨 PENDIENTE (nuevo top priority)
1) Phase 9 (orchestrator):
   - Aplicar `compute_all_advanced_adjustments`.
   - Ponderación por `data_quality` (60/35/0).
   - Guardar `raw_adjustment` + `weighted_adjustment`.

2) Phase 10 (explosive inning):
   - Añadir detector statcast-contact.
   - Nuevos reason codes / human reasons.

3) Objetivo 2 (pressure_base):
   - Crear módulo + wiring en orchestrator.
   - Ajustes conservadores a fragility/conf cuando Under.

### B) Testing (obligatorio)
**Estado:** 🟨 PENDIENTE (actualizado)
1) `pytest backend/tests/` (suite completa; mocks para cualquier IO).
2) Ejecutar `testing_agent` (backend) para validación de regresión y sanity de pipeline.

### C) Bright Data Unlocker (P0 bloqueado)
(Sin cambios)

### D) Basketball Historical Detail (P1)
(Sin cambios)

### E) Fix 2C (P2) — Persistencia live como async
(Sin cambios)

### F) Football deep-live parity (P3)
(Sin cambios)

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

- **MLB Statcast Deep Integration (Phase 9/10) — NUEVO**
  - Statcast actúa como **capa de confirmación/riesgo** (no motor principal).
  - Ajustes ponderados por `data_quality`:
    - strong=60%, partial/thin=35%, missing=0%.
  - Se guardan `raw_adjustment` y `weighted_adjustment` por pick.
  - Nuevos reason codes visibles (auditoría + explicabilidad).
  - Cero crash si faltan datos / provider falló.

- **MLB Offensive Pressure Base (Objetivo 2) — NUEVO**
  - `pressure_base` presente cuando hay `recentRunSplit`.
  - Clasificación correcta:
    - HIGH/MODERATE/LOW/NEUTRAL según umbrales.
  - Under picks “muchos hits / pocas carreras” aumentan fragility y se degradan con prudencia.

- **MLB Under Confidence Floor (✅ cumplido)**
  - Un pick MLB Under no puede quedar recomendado si `confidence_score < 75` con odds.

- **F6C Auto-Settle (✅ cumplido)**
  - Evaluaciones pending se resuelven automáticamente cuando hay `final_score`.

### Testing status
- Suite histórica: 550+ tests PASS previo.
- **Nuevo objetivo:** mantener 0 regresiones tras Phase 9/10/Objetivo 2 con `pytest` + `testing_agent` backend.
