# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests + Game Openness + Unilateral Dominance + Corner Settlement + Pattern Memory Voids + Basketball Possessions/Four Factors + Live Reeval UX + **MLB Offensive Injury Impact** + **Engine vs User Pick Divergence** + **MLB Tail Fragility Engine** (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

---

### Objetivo (MLB): Offensive Injury Impact Score ✅ COMPLETADO (end-to-end)
**Problema:** el motor trataba todas las lesiones igual (“52 jugadores lesionados”).

**Solución implementada:** medir si los lesionados son realmente **bates importantes** (top-5 ofensivo) y cuantificar el daño.

- ✅ `services/mlb_offensive_injury_impact.py`
  - Score 0–100 por equipo (`offensive_injury_score`) basado en ranking top-5 por score compuesto:
    - OPS / wRC+ (35%)
    - Runs + RBI (25%)
    - HR + XBH (20%)
    - OBP (10%)
    - PA / volumen (10%)
  - Buckets: `LOW` / `MEDIUM` / `HIGH`.
  - Reason codes explícitos (incluye señales de Under cuando ambos equipos están depletados).
  - Reglas duras:
    - Pitchers y lesiones de banca **no penalizan**.
    - Two-way players tipo Ohtani: `P/DH` con `PA ≥ 50` cuenta como ofensivo.
    - Fail-soft cuando hay datos insuficientes (pool < 5 ofensivos): `{available: False, ...}`.
  - Ajustes pipeline: `apply_impact_to_pipeline` devuelve multiplicadores con cap de supresión `0.85×`.
  - **Nunca auto-flip** de polaridad de mercado (solo supresión y narrativa).

- ✅ Tests
  - `tests/test_mlb_offensive_injury_impact.py`: **19/19 passing**.

- ✅ Integración backend
  - `services/mlb_stats_api.py`: `hydrate_team_offensive_roster()` (cache 6h; roster activo + stats de bateo).
  - `services/mlb_day_orchestrator.py`:
    - Hidrata roster ofensivo en paralelo con IL.
    - Calcula `compute_offensive_injury_impact`.
    - Persiste en `pick_payload['offensive_injury_impact']` y `pipeline_meta['offensive_injury_impact']`.
    - Aplica supresión al `_mean_eff` antes de `compute_expected_runs_distribution` usando el promedio de multipliers home/away.
    - No toca el pick principal: observe-only, supresión de runs/λ/traffic.

- ✅ Integración frontend
  - `components/OffensiveInjuryImpactPanel.jsx`: panel colapsable estilo `TailRiskPanel`.
    - Colores: `LOW=emerald`, `MEDIUM=amber`, `HIGH=rose/destructive`.
    - Header: pills por equipo con bucket + missing_count.
    - Contenido: narrativa ES, top bates ausentes (OPS/HR), runs/game perdidos estimados, badge “Apoyo al Under” cuando aplica.
    - Fail-soft: no renderiza si `available:false` o sin datos relevantes.
  - `components/MatchCard.jsx`: panel cableado después de `TailRiskPanel` (gated por MLB).

**Notas:**
- Picks previos sin `offensive_injury_impact` → panel oculto (compatibilidad backward).
- Warning F821 `traffic_score_payload` en `mlb_day_orchestrator.py` es preexistente/latente (no introducido por esta feature).

---

### Objetivo (MLB): `hydrate_team_offensive_roster` fail-soft interno ✅ COMPLETADO
**Problema:** el contrato decía “nunca levantar excepción”, pero el primer draft dependía del `try/except` del orchestrator.

**Solución implementada (defensa en profundidad):**
- ✅ `services/mlb_stats_api.py::hydrate_team_offensive_roster()` reescrito como fail-soft de extremo a extremo:
  - `db=None` → bypass cache; warm fetch directo.
  - cache_get exceptions → ignoradas, continúa.
  - cache_put exceptions → ignoradas, retorna payload igualmente.
  - HTTP/JSON/parse failures → retorna payload seguro `{available: False, reason: ..., players: []}`.
  - parse-per-player con `try/except` defensivo.
  - debug logs con contexto (team_id, error).
- ✅ Tests nuevos:
  - `tests/test_hydrate_team_offensive_roster_failsoft.py`: **8/8 passing**.

---

### Objetivo (Fix 1 + Fix 2): Validar apuesta real del usuario + Comparador Engine vs User ✅ COMPLETADO
**Problema:** al liquidar un pick, el sistema asumía automáticamente que el usuario apostó exactamente la recomendación del engine.

**Objetivo:** separar completamente:
- **LO QUE RECOMENDÓ EL ENGINE** (engine_accuracy)
- **LO QUE REALMENTE APOSTÓ EL USUARIO** (user_accuracy)

**Scope confirmado:**
- ✅ Deportes: **MLB + Fútbol**.
- ✅ Modal obligatorio en dos momentos: **pre-bet (Track In live)** y **settlement**.
- ✅ Backfill retroactivo desde Historial.
- ✅ Dashboard dedicado: `/dashboard/calibration`.
- ✅ Liquidación dual SIEMPRE: el engine_pick se auto-liquida con el score oficial (métricas puras de Engine Accuracy).

#### Backend ✅
- ✅ Nuevo módulo fail-soft: `services/pick_divergence_analysis.py` (puro Python)
  - `parse_pick` (MLB + fútbol, ES/EN)
  - `settle_pick_against_score`
  - `compute_divergence` (`NONE/PROTECTED/AGGRESSIVE/DIFFERENT_MARKET/OPPOSITE_SIDE` + `line_difference`)
  - `evaluate_engine_vs_user`
- ✅ Inyección en `POST /api/picks/track` (`track_pick`):
  - Persiste `divergence` + `engine_result` + `user_result`.
  - Si faltan `actual_*` → asume followed_engine=true (fail-soft).
  - **Nunca sobrescribe** `engine_recommendation`.
- ✅ Endpoints:
  - `GET /api/calibration/summary`
  - `GET /api/calibration/divergences`
  - `PATCH /api/picks/{pick_uid}/user-bet`
- ✅ Tests:
  - `tests/test_pick_divergence_analysis.py`: **43/43 passing**.

#### Frontend ✅
- ✅ `components/EnginePickConfirmModal.jsx` (Sí/No + formulario mercado/lado/línea/cuota con normalización decimal/americana).
- ✅ Integración en `LiveReevalPanel.jsx` (engine-source settlement) sin set-state-in-effect (reset via `key`).
- ✅ `components/UserBetBackfillModal.jsx` (botón lápiz desktop/mobile en Historial).
- ✅ `pages/CalibrationPage.jsx` + ruta `/dashboard/calibration`.
- ✅ `AppHeader.jsx` tab “Calibración” (`data-testid='nav-calibration'`).

#### Testing agent ✅
- ✅ `iteration_71.json` verde.

---

### Objetivo (MLB): Phase 55 — Tail Fragility Engine ✅ COMPLETADO (end-to-end)
**Problema:** fragility tenía drivers estructurales (bullpen, lambda tarde, breakdown, etc.) pero no capturaba explícitamente la probabilidad de eventos extremos (colas explosivas) cuando dos juegos comparten la misma media (ER).

**Objetivo:** distinguir escenarios con misma media (ER=8.0) pero distinto riesgo de blow-up tardío, y reflejarlo en fragility/confidence **sin** tocar `expected_runs`, `run_distribution` ni la polaridad Over/Under.

#### Backend ✅
- ✅ Extendido `services/mlb_expected_runs_distribution.py::compute_tail_risk()`:
  - Incluye `p_ge_18` (P(total_runs ≥ 18)) junto con `p_ge_12/14/16`.
  - **No recalcula** distribución; consume CDF existente.

- ✅ Nuevo módulo `services/mlb_tail_fragility.py` (puro Python, fail-soft):
  - `compute_tail_fragility(tail_risk_payload, bullpen_fatigue_high, defensive_breakdown_bucket, series_familiarity_bucket, starter_era, starter_whip, market_side)`
  - Probabilidades: `p_ge_12`, `p_ge_14`, `p_ge_16`, `p_ge_18`.
  - **Explosive Tail Score (0-100):**
    - `tail = p12·0.30 + p14·0.30 + p16·0.25 + p18·0.15`
    - `explosive_tail_score = round(tail * 100)`
  - Buckets: `LOW [0-24]`, `MEDIUM [25-49]`, `HIGH [50-74]`, `EXTREME [75+]`.
  - Base adjustment: `LOW=0`, `MEDIUM=+5`, `HIGH=+10`, `EXTREME=+15`.
  - Interaction modifiers (solo si bucket ≥ HIGH):
    - Bullpen fatigado +5 (`TAIL_BULLPEN_INTERACTION`)
    - Defensive breakdown ≥ MEDIUM +4 (`TAIL_DEFENSE_INTERACTION`)
    - Series familiarity ≥ MEDIUM +3 (`TAIL_SERIES_INTERACTION`)
    - Abridor vulnerable (ERA>4.50 o WHIP>1.35) +5 (`TAIL_STARTER_INTERACTION`)
  - **Cap total +20** (base + interactions) con `TAIL_FRAGILITY_CAP_HIT`.
  - Helper `apply_to_fragility()` para sumar delta y clamp `[0,100]`.
  - Restricciones honradas: **no** modifica ER, distribución ni polaridad.

- ✅ Reemplazo sin doble conteo en calibrator:
  - `services/mlb_fragility_calibrator.py::calibrate_fragility` acepta `tail_fragility`.
  - Si `tail_fragility.available=True` → usa `total_adjustment` + reason codes Phase 55.
  - Si NO se provee → fallback a lógica legacy con `p_ge_12` (backward compat).

- ✅ Integración orchestrator:
  - `services/mlb_day_orchestrator.py` computa `tail_fragility` después de `tail_risk` y antes del calibrator.
  - Derivación de drivers:
    - `bullpen_fatigue_high`: bucket HIGH/EXTREME o score ≥ 65.
    - `defensive_breakdown_bucket`: score ≥70 HIGH, ≥50 MEDIUM.
    - `series_familiarity_bucket`: desde payload.
    - `starter_era/whip`: “worst-case” del matchup.
  - Persiste:
    - `pick_payload['tail_fragility']`
    - `pipeline_meta['tail_fragility']`
  - Alimenta `calibrate_fragility(..., tail_fragility=...)`.

- ✅ Tests:
  - `tests/test_mlb_tail_fragility.py`: **21/21 passing** (incluye caso Cole vs Cecconi que llega al cap +20).
  - Suite completa backend: **1528/1528 passing** (`iteration_72.json` verde, 0 críticos, 0 regresiones).

#### Frontend ✅
- ✅ Extensión de `components/TailRiskPanel.jsx`:
  - Nueva prop `tailFragility`.
  - ProbRow adicional para `18+ carreras` cuando `p_ge_18` existe.
  - Sub-bloque `TAIL FRAGILITY` debajo de las probabilidades:
    - Bucket (LOW/MEDIUM/HIGH/EXTREME) + `explosive_tail_score`.
    - Desglose de ajuste: Base / Interacciones / Total (con highlight del cap).
    - Badges por interacción activa con delta.
    - Narrativa ES.
  - Colores por bucket: LOW=emerald, MEDIUM=cyan, HIGH=amber, EXTREME=red.
- ✅ `components/MatchCard.jsx` pasa `tailFragility={m.tail_fragility}` y habilita el panel si hay `tail_fragility` aunque `tail_risk` no esté presente.

**Notas UI:**
- Si no hay datos suficientes para `tail_risk`/CDF (fail-soft), `tail_fragility.available=False` y el sub-bloque no renderiza.

---

### Objetivos en curso (Injury Intelligence Layer)
- ⏳ Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
- Arquitectura: **fail-soft**, multi-source, cache-aware, sport-specific, explicable, conservadora.
- Entregar un bloque `injury_intelligence` que ajuste (conservadoramente) confidence/fragility/market warnings **sin forzar picks**.
- UI: `InjuryIntelligencePanel` para football/basketball (no MLB).

---

### Objetivo (Basketball): Possessions + Pace + Efficiency + Four Factors (Fix 1)
- 🎯 Crear capa avanzada basada en posesiones reales y Four Factors.
- Fail-soft estricto con fallback a `basketball_historical`.

---

### Objetivo (Live UX/Timeout): Reevaluación live con cuota manual + mercados 0.5 (Fix 2)
- 🎯 Corregir timeout UI (>20s) al reevaluar con cuota manual.
- 🎯 Aceptar coma/punto en odds.
- 🎯 Añadir Over/Under 0.5 en fútbol.

---

### Objetivos completados (Football Moneyball + DC/NB + Timeline + Over Support + Game Openness + Dominance + Corners + Voids)
- ✅ (Sin cambios respecto al plan previo; se mantiene histórico.)

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC ✅

### Phase 2 — V1 Backend Development ✅

### Phase 3 — V1 Frontend Development ✅

### Phase 4 — Comprehensive Testing & Regression ✅

> **Estado tests (actual):** ✅ Backend `pytest tests/` **1528 passing**.

---

## 3) Injury Intelligence Layer — Basketball (Phase 1) (EN CURSO)

### Phase 5 — Backend (pendiente)
### Phase 6 — Frontend/UI (pendiente)
### Phase 7 — Tests (pendiente)

---

## 4) Fútbol Moneyball + Pattern Memory ✅ (histórico)

---

## 5) Next Actions (Actualizado)

### En curso (prioridad)
- (P1) Injury Intelligence Basketball (Phase 5–7).

### Pendiente / futuro
- (P2) RTL tests para sub-bloque **TAIL FRAGILITY** (render/no render + colores por bucket + cap).
- (P3) Mejorar descubribilidad del botón “Hidratar Four Factors”.
- (P2) Tests end-to-end live → settlement con partidos reales.

---

## 6) Success Criteria (Actualizado)

### Phase 55 — Tail Fragility Engine
- ✅ No recalcula distribución; consume CDF existente.
- ✅ Añade `p_ge_18` a tail_risk.
- ✅ Explosive tail score 0-100 con fórmula de weights.
- ✅ Bucket correcto y base adjustment +0/+5/+10/+15.
- ✅ Interactions solo si bucket ≥ HIGH.
- ✅ Cap total +20 (sin doble conteo).
- ✅ No modifica `expected_runs`, `run_distribution` ni polaridad.
- ✅ UI: TailRiskPanel muestra sub-bloque TAIL FRAGILITY + narrativa.
- ✅ No-regresión: `pytest tests/` verde (1528 passing) + `iteration_72.json` verde.

### Global
- ✅ Fail-soft mantenido.
- ✅ Backend y frontend estables sin regresiones.
