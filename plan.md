# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence + Football Moneyball + Football DC/NB Calibration + Live Recommendation History + Over Support Market Selection + RTL Tests (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

### Objetivos en curso (Injury Intelligence Layer)
- Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
- Arquitectura: **fail-soft**, multi-source, cache-aware, sport-specific, explicable, conservadora.
- Entregar un bloque `injury_intelligence` en el payload que ajuste (conservadoramente) confidence/fragility/market warnings **sin forzar picks**.
- UI: `InjuryIntelligencePanel` para football/basketball (no MLB por ahora) mostrando bajas clave, severidad, impacto y freshness.

### Objetivos completados (Football Moneyball Intelligence Layer + Pattern Memory)
- ✅ Convertir el motor de fútbol de “análisis por partido” a un sistema tipo **Moneyball histórico** con:
  - snapshots pregame/live
  - perfiles diarios por equipo (cache)
  - pattern memory conservadora
  - selección de mercado protegida y feedback post-settle
- ✅ Replicar **fielmente la arquitectura MLB** (warehouse + pressure/profile + snapshot + pattern memory + market selection + feedback), pero con señales **football-specific**.
- ✅ **Fail-soft estricto**: si falla DB o faltan señales → fallback a análisis base actual (sin romper picks ni UI).
- ✅ No tocar ni romper MLB ni Basketball (código aislado por módulos y gating por `sport`).
- ✅ UI mínima viable en MatchCard para football: paneles de inteligencia, pattern memory, y live vs pregame.

### Objetivos completados (Football Totals Calibration: Dixon-Coles + NB condicional)
- ✅ Modelo robusto para totales football:
  - Matriz bivariada `P(home=i, away=j)` truncada y renormalizada.
  - Dixon–Coles tau aplicado a low-score con clamp ρ∈[-0.20, 0.0].
  - NB condicional por lado con ratio clamp [1.0, 2.0], por defecto inert (1.0).
- ✅ Telemetría completa en `compute_match_features`.
- ✅ Calibración `global-antes-de-bucket` (n<100 defaults; buckets OBSERVE_ONLY hasta n≥100).
- ✅ Endpoint `GET /api/football/totals-calibration/summary?days=90`.
- ✅ Persistencia extendida en `football_market_results`.

### Objetivos completados (UI Football DC/NB + Over Support)
- ✅ `FootballDcNbPanels.jsx`:
  - `FootballTotalsModelPanel` (Poisson vs DC/NB, ρ, NB ratio, deltas, modo defaults/empirical).
  - `FootballOverSupportPanel` (Over 1.5/2.5 support, presión 0–30, fragilidad, reason codes).
- ✅ Integración en `MatchCard.jsx` (gated por `sport === 'football'`, fail-soft por `available`).

### Objetivo completado (P0): Live Recommendation History / Timeline
- ✅ Historial/auditoría de recomendaciones live:
  - autosave con dedupe (solo cambios reales)
  - manual entry (sin requerir match doc real)
  - auto-settle MVP (BTTS + Over/Under)
  - endpoints con filtros completos
  - UI timeline + formulario manual
  - fail-soft end-to-end

### Objetivo nuevo (P1): Over Support Market Selection + Frontend RTL Tests
- Integrar `football_over_support` en `football_market_selection.py` como señal **de soporte** para mercados Over, manteniendo **protected-market-first**.
- Permitir **Over 1.5** como mercado protegido condicional.
- Permitir **Over 2.5** solo en escenarios de soporte extremo y baja fragilidad, con gates por DC/NB y lesiones.
- Bloquear recomendaciones de **líneas muertas** (ya cumplidas) para entradas live.
- Añadir suite **frontend RTL** para timeline live y paneles football (incluye gating por deporte).
- Añadir tests backend pytest para selección de mercado (Over Support integration).

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC (aislado, obligatorio) ✅ COMPLETADO
**Core probado:** “Game → pipeline Moneyball → `market_selection` final → payload persistible + live/pregame linkage por `game_pk` (fail-soft).”

---

### Phase 2 — V1 Backend Development (Moneyball alignment) ✅ COMPLETADO
(MLB pipeline Moneyball, summary + editorial mapper)

---

### Phase 3 — V1 Frontend Development (UI Moneyball) ✅ COMPLETADO
(MatchCard panels + dashboard buckets + live analysis)

---

### Phase 4 — Comprehensive Testing & Regression ✅ COMPLETADO
- ✅ Suite backend sin regresiones.

> **Estado tests (actual):** ✅ `pytest tests/` **1043 passing**.

---

## 3) Injury Intelligence Layer — Basketball (Phase 1) (EN CURSO)

### Phase 5 — Injury Intelligence (Basketball) — Backend (pendiente)
### Phase 6 — Injury Intelligence (Basketball) — Frontend/UI (pendiente)
### Phase 7 — Tests (Basketball Injury Intelligence) (pendiente)

---

## 4) Football Moneyball Intelligence Layer + Pattern Memory (P0) ✅ COMPLETADO
(Phases 8–16 completadas; warehouse + snapshots + pattern memory + market selection + feedback)

---

## 5) UI mínima viable (Football Moneyball + DC/NB + Over Support) ✅ COMPLETADA

### Phase 17 — Frontend: MatchCard panels ✅
- ✅ Paneles Moneyball football.
- ✅ Paneles DC/NB Totals + Over Support.

### Phase 18 — Frontend: consumo endpoint summary (opcional)
- (Opcional) Consumir endpoints summary para dashboards agregados.

### Phase 19 — Tests frontend (RTL) (P1)
- ⏳ Ahora se eleva a **P1** y se extiende a timeline + paneles football (ver Phase 33).

---

## 6) Football Totals Calibration — Dixon-Coles + NB Conditional (P0) ✅ COMPLETADO
(Phases 20–26 completadas.)

---

## 7) Live Recommendation History / Timeline (P0) ✅ COMPLETADO

### Phase 27–32 ✅
- ✅ Colección + índices + servicio + endpoints + auto-settle + UI timeline.
- ✅ Backfill France vs Ivory Coast (match_id=1536931) insertado.
- ✅ Testing agent backend 30/30 (100%): `/app/test_reports/iteration_63.json`.

---

## 8) Phase 33 — P1: Football Over Support Market Selection + RTL Tests (NUEVO)

### 33.1 Backend — Integración Over Support en `football_market_selection.py`
**Objetivo:** permitir que Over Support influya **de forma conservadora** en la selección final, sin forzar picks.

#### 33.1.1 Inputs
Leer en `select_football_market`:
```python
football_over_support = match.get("football_over_support") or pick.get("football_over_support") or {}
football_totals_model = match.get("football_totals_model") or pick.get("football_totals_model") or {}
```
(En la práctica actual: `pick_payload` + `pregame_snapshot`; debe ser compatible con ambos.)

#### 33.1.2 Helper “líneas muertas”
Añadir helper puro:
```python
def is_total_line_already_hit(match_or_snapshot, market_label):
    # Over 0.5 hit si total_goals>=1
    # Over 1.5 hit si total_goals>=2
    # Over 2.5 hit si total_goals>=3
    # Over 3.5 hit si total_goals>=4
```
Regla: si ya está hit → no recomendar como nueva entrada; añadir `OVER_LINE_ALREADY_HIT`.

#### 33.1.3 Over 1.5 como mercado protegido condicional
Permitir **Over 1.5** cuando:
- `football_over_support.available == True`
- `over_1_5_support_score >= 70`
- `recommended_over_market == "OVER_1_5"` **o** reason_codes contiene `OVER_1_5_PROTECTED`
- `fragility_score <= 60`
- NO reason `CONTROLLED_MATCH_BLOCKS_OVER`
- odds disponibles y no demasiado bajas
- si odds faltan pero soporte estructural es fuerte → **watchlist_manual_odds** + `MANUAL_ODDS_REVIEW_REQUIRED`

Reason codes:
- `OVER_SUPPORT_CONFIRMED`
- `OVER_1_5_PROTECTED_SELECTED`
- `OVER_SUPPORT_WATCHLIST_ONLY`
- `MANUAL_ODDS_REVIEW_REQUIRED` (ya existe)

#### 33.1.4 Over 2.5 como mercado agresivo
Permitir **Over 2.5** solo si:
- `over_2_5_support_score >= 80`
- `fragility_score <= 45`
- `lambda_total >= 2.85`
- early goal pressure fuerte
- NO `DC_NB_MODEL_PREFERS_UNDER`
- NO `CONTROLLED_MATCH_BLOCKS_OVER`

Si hay soporte pero fragilidad alta:
- degradar a Over 1.5 (si cumple sus gates) o watchlist
- reason `OVER_2_5_FRAGILE`, `OVER_2_5_DOWNGRADED_TO_OVER_1_5`

Reason codes:
- `OVER_2_5_ALLOWED_LOW_FRAGILITY`

#### 33.1.5 Conflicto con DC/NB
Si `football_totals_model.under_3_5.dc_nb >= 0.70` y Over Support sugiere Over:
- **NO recomendar Over 2.5**
- permitir Over 1.5 solo si `support >= 75` y `fragility <= 55`
- reason `DC_NB_UNDER_CONFLICT`

#### 33.1.6 Lesiones
- `TOP_SCORER_OUT_WEAKENS_OVER`:
  - reducir confianza Over
  - bloquear Over 2.5 salvo soporte extremo
  - reason `OVER_BLOCKED_BY_OFFENSIVE_INJURY`
- `INJURY_DEFENSE_WEAKENED_OVER_SUPPORT`:
  - puede reforzar Over 1.5 o Team Total Over
  - nunca forzar Over 2.5 automáticamente

#### 33.1.7 Live gating
- `LIVE_OVER_CONFIRMED_BY_PRESSURE` habilita Over (1.5/2.5) **solo si la línea no está muerta**.
- Nunca recomendar Over X.5 si ya se cumplió sin marcarlo como “ya ocurrió”.

#### 33.1.8 Output canónico
Alinear el shape actual a:
```json
{
  "market_selection": {
    "recommended_market": "Over 1.5",
    "protected_alternative": "Over 1.5",
    "market_confidence": 0,
    "fragility": 0,
    "requires_manual_odds": false,
    "watchlist": false,
    "why_this_market": "...",
    "why_not_other_markets": [],
    "reason_codes": []
  }
}
```


### 33.2 Backend — Tests pytest (Market Selection)
Añadir tests (≈11 casos):
- Over 1.5 se selecciona cuando support≥70 y fragility≤60.
- Over 2.5 solo cuando support≥80 y fragility≤45.
- Over 2.5 se degrada a Over 1.5 si fragility alta.
- DC/NB Under 3.5 ≥0.70 bloquea Over 2.5.
- Controlled match bloquea Over.
- Top scorer out bloquea/degrada Over.
- Defensive injury refuerza Over 1.5 (sin forzar Over 2.5).
- Score 1-1 bloquea Over 1.5 (línea ya hit).
- Missing odds manda a manual review.
- Missing football_over_support no rompe (fail-soft).
- MLB/Basketball no afectados (gating por sport y/o ausencia de payload).


### 33.3 Frontend — Setup RTL (CRA/Jest)
- Instalar:
  - `@testing-library/react@^16`
  - `@testing-library/jest-dom`
  - `@testing-library/user-event`
- Añadir `src/setupTests.js`:
  - `import '@testing-library/jest-dom';`


### 33.4 Frontend — RTL Tests

#### A) `LiveRecommendationTimeline` (9 casos)
- renderiza evento engine.
- renderiza evento manual.
- muestra HIT correctamente.
- muestra OPEN/WATCHLIST correctamente.
- muestra SUPERSEDED correctamente.
- muestra empty state.
- botón refresh llama endpoint.
- form manual envía payload correcto.
- endpoint fallido no rompe UI.

#### B) `FootballTotalsModelPanel` (10 casos)
- renderiza Poisson Under 3.5.
- renderiza DC/NB Under 3.5.
- muestra delta en puntos (no *100).
- muestra ρ usado.
- muestra NB ratio.
- muestra DEFAULT CALIBRATION.
- muestra EMPIRICAL CALIBRATION.
- muestra NB INERT cuando ratio=1.0.
- muestra NB ACTIVE cuando ratio>1.0.
- no renderiza si `available=false`.

#### C) `FootballOverSupportPanel` (6 casos)
- renderiza Over 1.5 support score.
- renderiza Over 2.5 support score.
- muestra recommended_over_market.
- muestra OBSERVE ONLY si sigue en observe mode (si aplica en payload).
- muestra reason codes.
- no renderiza si `available=false`.

#### D) `MatchCard` integration (4 casos)
- paneles aparecen solo en football.
- paneles no aparecen en MLB.
- paneles no aparecen en Basketball.
- null payload no rompe.


### 33.5 Validación y No-regresión
- Ejecutar:
  - `pytest tests/` (backend)
  - `yarn test` / `craco test` (frontend)
- Confirmar:
  - no cambios en MLB/Basketball
  - fail-soft: ausencia de odds/over_support/DCNB no rompe
  - market selection sigue protected-first

---

## 9) Next Actions (Actualizado)

### Inmediato (P1)
1. Implementar Phase 33.1 (Over Support → Market Selection) + helper líneas muertas.
2. Añadir tests backend Phase 33.2.
3. Instalar RTL stack + setupTests.js (Phase 33.3).
4. Añadir suite RTL (Phase 33.4) + correr `craco test`.

### Posterior (P2)
- Extender settlement a córners/handicap si se desea.
- Retomar Injury Intelligence Basketball (Phase 1).

---

## 10) Success Criteria (Actualizado)

### Football Over Support en Market Selection (P1)
- Over Support participa en market selection **sin forzar picks**.
- Over 1.5 puede salir como **protected** condicional.
- Over 2.5 solo con soporte fuerte y baja fragilidad; degradación/warning cuando aplique.
- Conflicto DC/NB (Under 3.5 alto) bloquea Over 2.5; Over 1.5 solo con gates más estrictos.
- Líneas ya cumplidas nunca se recomiendan como nueva entrada (`OVER_LINE_ALREADY_HIT`).
- Fail-soft total y sin regresión MLB/Basketball.

### Frontend RTL
- Timeline live tiene tests RTL.
- Paneles football (DC/NB + Over Support) tienen tests RTL.
- MatchCard gating por deporte validado.
