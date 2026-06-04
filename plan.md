# Plataforma — Roadmap de Alineación Moneyball + Injury Intelligence (plan.md)

## 1) Objectives

### Objetivos completados (MLB Moneyball)
- ✅ Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- ✅ Estandarizar `pick_payload` con contrato fail-soft (`available:false` por capa) sin romper UI ni picks viejos.
- ✅ Enriquecer `mlb_run_evaluations_summary` con breakdowns Moneyball, manteniendo compatibilidad legacy.
- ✅ Convertir **editorial** en capa de confirmación/contexto (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- ✅ UI Moneyball: paneles explicables (market selection, ghost-edges, fragility/survival, pattern memory, manual odds, etc.).
- ✅ Live MLB: corregido gating y contradicciones en comparación pregame vs live.

### Objetivos nuevos (Injury Intelligence Layer)
- Implementar **Injury Intelligence Layer** para **Basketball (Phase 1)** y luego Football (Phase 2), sin tocar MLB.
- Arquitectura: **fail-soft**, multi-source, cache-aware, sport-specific, explicable, conservadora.
- Entregar un bloque `injury_intelligence` en el payload que ajuste (conservadoramente) confidence/fragility/market warnings **sin forzar picks**.
- UI: `InjuryIntelligencePanel` para football/basketball (no MLB por ahora) mostrando bajas clave, severidad, impacto y freshness.

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC (aislado, obligatorio) ✅ COMPLETADO
**Core probado:** “Game → pipeline Moneyball → `market_selection` final → payload persistible + live/pregame linkage por `game_pk` (fail-soft).”

Entregables:
- ✅ Contrato de payload Moneyball sellado.
- ✅ Warehouse/source status agregados.

---

### Phase 2 — V1 Backend Development (Moneyball alignment) ✅ COMPLETADO

#### 2.1 `mlb_day_orchestrator.py` (refactor de orden y responsabilidades) ✅
- ✅ `market_selection` como capa final.
- ✅ Buckets `structural_lean_requires_odds` / `watchlist_manual_odds`.
- ✅ Payload contract + `pipeline_meta.external_sources`.

#### 2.2 `mlb_run_evaluations_summary.py` (Moneyball breakdowns) ✅
- ✅ Nuevos breakdowns + `summary_schema_version=moneyball.1`.

#### 2.3 `editorial_context_service.py` (contexto, no motor) ✅
- ✅ `p4-moneyball-context.1` + anotación vs Moneyball.

#### 2.4 `editorial_signal_mapper.py` (vocabulario y `sport_hint`) ✅
- ✅ MLB/NBA vocab + sport discrimination + neutralización de motivación MLB.

---

### Phase 3 — V1 Frontend Development (UI Moneyball) ✅ COMPLETADO

#### 3.1 MatchCard + Panels (explicabilidad) ✅
- ✅ Secciones/paneles Moneyball (Market Selection, Ghost Edges, Fragility/Survival, Pattern Memory, Manual Odds Review, etc.).

#### 3.2 Dashboard buckets + empty states + manual odds ✅
- ✅ Buckets separados (structural lean vs watchlist) + copy MLB.

#### 3.3 Live Analysis (live vs pregame por `game_pk`) ✅
- ✅ Warning hits-pressure, filtro de líneas ya superadas.

---

### Phase 4 — Comprehensive Testing & Regression ✅ COMPLETADO
- ✅ Suite backend sin regresiones.
- ✅ Nuevos tests Moneyball + live polish.

**Estado tests:** ✅ `pytest` 792 passing.

---

## 3) NEW: Injury Intelligence Layer — Basketball (Phase 1)

## Context
El usuario solicita Injury Intelligence para basketball y football. Alcance confirmado:
- **Phase 1 = Basketball backend + UI**
- Phase 2 = Football

Fuentes disponibles:
- **API-Sports + Bright Data scraping (ESPN/Rotowire/Transfermarkt) + TheStatsAPI**

Estrategia roles:
- **Lista hardcodeada de superstars/estrellas por equipo** + apoyo con endpoint player-stats (si existe) / heurística.

### Phase 5 — Injury Intelligence (Basketball) — Backend (NEW)

#### 5.1 Crear package `services/injury_intelligence/`
Crear módulos:
- `services/injury_intelligence/__init__.py`
- `injury_schema.py` (dataclasses/typing + shape canónico)
- `injury_sources.py` (multi-source fetcher; wrappers API-Sports/TheStatsAPI/BrightData)
- `injury_normalizer.py` (normalización de estados + dedupe)
- `injury_impact_model.py` (motor común: caps, pesos, freshness)
- `basketball_injury_impact.py` (scoring NBA)
- `injury_cache.py` (mongo + TTL policies)

Reglas:
- Fail-soft estricto: missing data → `available:false`, no crash.
- Multi-source: si una fuente falla, continuar.
- Conservador: conflictos → estado más severo.
- Provenance por jugador: source/source_url/updated_at/confidence.

#### 5.2 Schema normalizado
Implementar shape común (según prompt) y helpers:
- `normalize_status(...)` → out/doubtful/questionable/probable/day_to_day/suspended/minutes_restriction/rest/unknown
- `compute_freshness(...)` con TTLs:
  - Basketball: 2h pregame, 30min game-day

#### 5.3 Roles NBA
- Crear `nba_star_registry.py` (o dentro de `basketball_injury_impact.py`) con:
  - superstars/stars hardcoded por equipo
  - fallback heurístico cuando no está en lista (minutos/usage/ppg si player-stats existen)

#### 5.4 Fetch multi-source (Basketball)
- API-Sports basketball injuries (si endpoint existe; wire via `_get`/client existente)
- TheStatsAPI injuries si disponible
- Bright Data: scrapers ESPN/Rotowire (fallback)
- Editorial context como complemento (no fuente primaria)

#### 5.5 Impact scorer (Basketball)
Función:
- `calculate_basketball_injury_impact(team_profile, injuries, player_stats=None)`

Output por equipo:
- `basketball_injury_score` con:
  - team_strength/offense/defense/pace adjustments
  - spread/moneyline/total_points adjustments
  - fragility_adjustment
  - reason_codes

Reglas clave:
- Caps: ±12 confidence, +15 fragility
- HIGH/CRITICAL pueden bloquear picks agresivos (spread duro, ML fuerte)
- Questionable/minutes restriction en clave → watchlist/manual review

#### 5.6 Match-level edge
- `match_injury_edge`: home vs away net_edge_points + tier (SMALL/MODERATE/STRONG)
- `high_volatility` si ambos con impacto HIGH/CRITICAL

#### 5.7 Integración con pipeline Basketball
- Inyectar `injury_intelligence` en payload para basketball:
  - no tocar MLB
  - ajustes conservadores a confidence/fragility (si data fresh)
  - warnings para market_selection (no forzar pick)

#### 5.8 Prompt `analyst_engine`
- Añadir reglas: antes de recomendar basketball revisar `injury_intelligence`.

---

### Phase 6 — Injury Intelligence (Basketball) — Frontend/UI (NEW)

#### 6.1 `InjuryIntelligencePanel.jsx`
- Renderiza solo si:
  - `injury_intelligence.available===true` y hay lesiones relevantes, o
  - hay `market_warnings`, o
  - freshness=stale con warnings.

Debe mostrar:
- Jugadores OUT/QUESTIONABLE/minutes restriction
- Rol (superstar/star/starter/rotation/bench)
- Impact tier + score
- Team impact y net edge
- Source badges + freshness
- Market warnings

Integración:
- MatchCard basketball
- Picks detail modal
- Live panel (warnings live/pregame)

#### 6.2 Buckets UI
- Si injury uncertainty alta → mostrar chip “Watchlist por lesiones”

---

### Phase 7 — Tests (Basketball Injury Intelligence) (NEW)

#### 7.1 Backend tests (pytest)
- Normalización de estados
- Conflictos entre fuentes (questionable vs out)
- Superstar out → tier CRITICAL + ajustes
- Minutes restriction key player → warning + fragility
- Multiple starters out → bloquea spread agresivo
- Fresh/partial/stale weights y caps
- Missing data no rompe y no ajusta confidence
- Cross-sport: MLB no afectado

#### 7.2 Frontend tests (RTL)
- Renderiza panel con data
- Ordena jugadores por impacto
- Badges HIGH/CRITICAL/DATA STALE
- Fail-soft si `injury_intelligence` null
- No aparece en MLB

---

## 4) Next Actions (Actualizado)

### Inmediato (Basketball Injury Phase 1)
1. Crear package `services/injury_intelligence/` + schema + normalizer.
2. Implementar multi-source fetcher basketball (API-Sports/TheStatsAPI/BrightData).
3. Implementar rol registry NBA + scoring `calculate_basketball_injury_impact`.
4. Integrar `injury_intelligence` al pipeline basketball (payload + ajustes conservadores).
5. UI: crear `InjuryIntelligencePanel.jsx` e integrarlo en cards basketball.
6. Tests backend + frontend; correr `pytest` (mantener 792+ sin regresiones).

### Posterior
- Phase 2: replicar arquitectura para Football (injury/suspension intelligence).

---

## 5) Success Criteria (Actualizado)

### Moneyball MLB (ya cumplido)
- ✅ Market Selection decide el pick final.
- ✅ Missing odds → buckets manuales, no discard automático.
- ✅ Payload contract fail-soft; editorial confirmación; UI explicable; live sin contradicciones.

### Injury Intelligence Basketball (Phase 1)
- Injury Intelligence **fail-soft**: missing → `available:false` y no altera engine.
- Multi-source con provenance, conflictos conservadores.
- Roles NBA: superstars hardcoded + fallback heurístico.
- Ajustes conservadores con caps (±12 confidence, +15 fragility).
- HIGH/CRITICAL bloquea picks agresivos (sin forzar picks).
- Payload incluye `injury_intelligence` en basketball.
- UI muestra bajas y edge neto en  <5s, no rompe picks viejos.
- Tests pasan: backend sin regresiones (≥792) + nuevos tests injury.
