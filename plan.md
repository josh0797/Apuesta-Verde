# MLB Moneyball Alignment — Polish Sprint (plan.md)

## 1) Objectives
- Alinear backend MLB al pipeline Moneyball: **Market Selection como capa final**, módulos legacy solo como contexto.
- Estandarizar `pick_payload` (campos Moneyball + estados `available:false` si faltan) y mantener **fail-soft**.
- Enriquecer métricas de evaluación (`mlb_run_evaluations_summary`) con breakdowns Moneyball sin romper compatibilidad.
- Convertir **editorial** en capa de contexto/confirmación (no motor) + mapper con vocabulario MLB/NBA y `sport_hint`.
- UI: explicar **por qué** se eligió el mercado, **por qué se rechazaron otros**, fragilidad, confirmaciones/bloqueos, manual odds, live vs pregame por `game_pk`.

---

## 2) Implementation Steps (Phases)

### Phase 1 — Core Flow POC (aislado, obligatorio)
**Core a probar:** “Game → pipeline Moneyball → `market_selection` final → payload persistible + live/pregame linkage por `game_pk` (fail-soft).”

User stories:
1. Como operador, quiero ejecutar el pipeline para 1 juego y ver que el mercado final viene de `mlb_market_selection`.
2. Como operador, quiero que si faltan odds el pick vaya a `structural_lean_requires_odds`/`watchlist_manual_odds` (no discard automático).
3. Como operador, quiero que faltantes de Statcast/sabermetrics no rompan el análisis.
4. Como operador, quiero ver `available:false` en capas faltantes para no romper UI.
5. Como operador, quiero que el pick se vincule a `game_pk` para comparación live vs pregame.

Steps:
- Crear script/test aislado (pytest) que:
  - construya un fixture MLB mínimo con `game_pk`.
  - simule 3 escenarios: (a) odds OK, (b) odds missing, (c) advanced stats missing/stale.
  - verifique: `market_selection.recommended_market` existe y es usado como final.
  - verifique presencia/shape de campos Moneyball (o `available:false`).
  - verifique persistencia de snapshot de inteligencia (colección `mlb_game_intelligence_snapshots`).
- (Si aplica) mini-websearch interno de best practices: “fail-soft payload contracts + cached snapshot freshness patterns”.
- No avanzar hasta que POC quede verde.

---

### Phase 2 — V1 Backend Development (Moneyball alignment)

#### 2.1 `mlb_day_orchestrator.py` (refactor de orden y responsabilidades)
User stories:
1. Como usuario, quiero que el pick final siempre pase por Market Selection.
2. Como usuario, quiero que el sistema explique por qué eligió ese mercado y por qué rechazó otros.
3. Como usuario, quiero que si faltan odds se pida revisión manual sin perder el análisis.
4. Como usuario, quiero que el sistema use cache/warehouse antes de recalcular Statcast/sabermetrics.
5. Como analista, quiero ver auditoría de pattern memory y fuentes externas en el payload.

Steps:
- Reordenar pipeline: base/contexto → pressure → sabermetrics → advanced snapshot/statcast → ghost edges → fragility/script survival → pattern memory → **market_selection (final)** → manual odds review.
- Asegurar que legacy modules (run_line/over_under/nrfi/under_profile) **no finalicen** picks; solo llenen contexto de candidatos.
- Estándar de payload:
  - incluir cuando exista: `advanced_stats_snapshot`, `pressure_base`, `sabermetrics_audit`, `ghost_edges`, `fragility_score`, `script_survival_score`, `market_selection`, `historical_pattern_match`, `pattern_memory_audit`, `manual_odds_review`, `pipeline_meta.external_sources`.
  - si falta capa/datos: devolver objeto con `available:false` (y `reason`/`stale` si aplica).
- Odds handling:
  - no mandar a `discarded_market` por missing odds.
  - usar `structural_lean_requires_odds` y/o `watchlist_manual_odds`.
- Warehouse-first:
  - consultar snapshot fresco; si stale/no existe → recalcular + upsert.
  - si snapshot válido → `upsert_team_profile`/`upsert_pitcher_profile`.
  - persistir `mlb_game_intelligence_snapshots` al final.
- Garantizar aislamiento: no tocar flujos football/basketball.

#### 2.2 `mlb_run_evaluations_summary.py` (Moneyball breakdowns)
User stories:
1. Como usuario, quiero ver performance por mercado seleccionado.
2. Como usuario, quiero ver cómo cambia el rendimiento por presión/fragilidad/supervivencia.
3. Como usuario, quiero detectar ghost-edges frecuentes y su impacto.
4. Como usuario, quiero comparar F5 under vs full game under y detectar “bullpen broke under”.
5. Como usuario, quiero ver performance por pattern_key con ROI y sample_size.

Steps:
- Agregar campos nuevos (sin borrar legacy):
  - `by_market_selected`, `by_pressure_environment`, `by_script_survival`, `by_fragility_tier`, `by_sabermetrics_edge`, `by_ghost_edge`, `f5_vs_full_game_under`, `manual_odds_review_outcomes`, `pattern_memory_performance`.
- Fail-soft: si no hay datos → totales 0, `hit_rate:null`, listas vacías.
- Mantener contrato del endpoint (compatibilidad).

#### 2.3 `editorial_context_service.py` (contexto, no motor)
User stories:
1. Como usuario, quiero que editorial solo confirme/etiquete contexto.
2. Como usuario, quiero ver warnings si editorial contradice Moneyball.
3. Como usuario, quiero tags MLB consistentes (pitcher/bullpen/weather/bias).
4. Como usuario, quiero metadata de alineación editorial vs modelo.
5. Como usuario, quiero cache con TTL y flag `fast_stale` para pitcher/lineup.

Steps:
- Bump `EDITORIAL_CONTEXT_VERSION` → `p4-moneyball-context.1`.
- MLB tags: `public_narrative`, `injury_or_lineup_note`, `pitcher_news`, `bullpen_news`, `market_public_bias`, `weather_or_park_note`.
- No modificar `confidence` directamente; agregar:
  - `moneyball_interpretation`, `editorial_vs_model_alignment`, `used_as_confirmation_only:true`.
- Contradicción: si editorial sugiere Over y hay ghost-edge/fragility alta → flags `PUBLIC_NARRATIVE_RISK`, `EDITORIAL_CONTRADICTS_MONEYBALL`.
- Cache: TTL 6h general; MLB pitcher/lineup con `fast_stale`/TTL menor.

#### 2.4 `editorial_signal_mapper.py` (vocabulario y `sport_hint`)
User stories:
1. Como usuario, quiero que el mapper detecte MLB sin confundirlo con fútbol.
2. Como usuario, quiero soporte NBA (spread/pace/back-to-back).
3. Como usuario, quiero que “goles/corners/tarjetas” se marque football.
4. Como usuario, quiero que “pitcher/bullpen/carreras/hits” se marque baseball.
5. Como usuario, quiero que si es ambiguo salga OPINION con baja confianza.

Steps:
- Añadir patrones MLB (market + factual + warning) y NBA.
- Añadir `sport_hint` en output.
- Regla motivación MLB: `MLB_NORMAL_MOTIVATION_NEUTRAL`.
- Fail-soft: default OPINION low confidence.

---

### Phase 3 — V1 Frontend Development (UI Moneyball)

#### 3.1 MatchCard + Panels (explicabilidad)
User stories:
1. Como usuario, quiero ver mercado recomendado + alternativa protegida + fragilidad en el header.
2. Como usuario, quiero entender “por qué este mercado” y “por qué no otros”.
3. Como usuario, quiero ver pressure base L5/L15 y ambiente (LOW→CHAOTIC) con alertas.
4. Como usuario, quiero ver sabermetrics (OPS/FIP/WAR) en español, sin saturación.
5. Como usuario, quiero ver ghost-edges y si bloquearon/degradaron el pick.

Steps:
- Reorganizar MatchCard MLB con accordions:
  - Header (debug `game_pk`, status, recommended, protected_alt, confidence, fragility tier).
  - Market Selection panel (why/why_not/reason_codes/manual odds chips).
  - Pressure Base panel.
  - Sabermetrics panel.
  - MLB Advanced Stats panel (sources/cache/data_quality + fail-soft).
  - Ghost-Edges panel.
  - Script Survival / Fragility panel.
  - Pattern Memory panel (sample_size badges  <20 / ≥20 / ≥50).
- Asegurar compatibilidad con picks viejos (render con defaults).

#### 3.2 Dashboard buckets + empty states + manual odds
User stories:
1. Como usuario, quiero bucket “Structural Lean Requires Odds”.
2. Como usuario, quiero bucket “Watchlist Manual Odds”.
3. Como usuario, quiero “Discarded after full analysis” con copy Moneyball.
4. Como usuario, quiero “Incomplete Data” con fuentes consultadas y faltantes.
5. Como usuario, quiero pegar odds manuales y ver implied prob/edge/recomendación.

Steps:
- Actualizar DashboardPage y componentes relacionados para nuevos buckets.
- Pulir `EmptyStateNoValue` con mensajes MLB y `SourcesConsultedPanel`.
- Integrar/ajustar `InlineManualOddsInput` + `ManualOddsReviewPanel` para recalcular edge.

#### 3.3 Live Analysis (live vs pregame por `game_pk`)
User stories:
1. Como usuario, quiero comparar live vs pregame por `game_pk`.
2. Como usuario, quiero ver veredicto (mantener/evitar/cashout/esperar/invalidado).
3. Como usuario, quiero warnings live (hits>>runs).
4. Como usuario, no quiero recomendaciones de líneas ya superadas.
5. Como usuario, si no hay pick pregame, quiero mensaje específico (no genérico).

Steps:
- Ajustar paneles live (`LivePreMatchComparisonPanel`/`LiveCopilotCard`) para lookup por `game_pk`.
- Añadir chips live + reglas de “línea ya superada”.

---

### Phase 4 — Comprehensive Testing & Regression
User stories:
1. Como dev, quiero que `pytest` siga ≥733 sin regresiones.
2. Como dev, quiero nuevos tests que cubran Market Selection final y fail-soft.
3. Como dev, quiero tests de summary con breakdowns nuevos vacíos.
4. Como dev, quiero tests editorial (no cambia confidence; contradicción genera warning).
5. Como dev, quiero tests mapper con sport_hint MLB/NBA.

Backend tests:
- `test_mlb_day_orchestrator_market_selection_final` (incluye missing odds + advanced missing + upserts + snapshot persist).
- `test_mlb_summary_new_breakdowns` (fields existen + legacy intact).
- `test_editorial_context_moneyball_alignment`.
- `test_editorial_signal_mapper_mlb_nba`.

Frontend tests (RTL):
- MatchCard render de `market_selection` + manual odds chip + fail-soft panels.
- Live panel: no recomendar línea superada + lookup por `game_pk`.
- Empty state: fuentes consultadas.
- Buckets nuevos.

---

## 3) Next Actions
1. Implementar Phase 1 POC tests (backend) y correr `pytest -k moneyball_orchestrator_poc`.
2. Refactor `mlb_day_orchestrator.py` para “market_selection final” + payload contract `available:false`.
3. Añadir breakdowns a `mlb_run_evaluations_summary.py` manteniendo legacy.
4. Actualizar editorial service + mapper (version bump + sport_hint).
5. UI: reorganizar MatchCard y buckets; luego live comparison por `game_pk`.
6. Ejecutar `pytest` completo + suite frontend.

---

## 4) Success Criteria
- MLB: decisión final siempre via `mlb_market_selection` (legacy no decide).
- Missing odds → `structural_lean_requires_odds`/`watchlist_manual_odds` (no discard automático).
- Payload Moneyball consistente; capas faltantes retornan `available:false` sin romper UI.
- Editorial = confirmación/contexto; contradicciones generan warnings.
- UI explica selección/rechazo, fragilidad, confirmaciones/bloqueos, manual odds, live vs pregame por `game_pk`.
- Tests: backend sin regresiones (≥733) + nuevos tests pasando; frontend tests clave pasando.
