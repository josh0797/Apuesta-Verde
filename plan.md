# Plan — Phases F58–F95.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F94.x completadas / en curso según bitácora.
> **Idioma operativo:** Español.

---

## 1) Objetivos

### Objetivos originales (F58)
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (shots/SOT/minutos)
  - FBref (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con override contextual.
- Añadir smoke tests y mantener suite global verde.
- (P2) UI wiring: panel independiente para Cross + Override + Player Props.

### Objetivos nuevos / extendidos (F69–F74)
- Editorial interno específico por partido (no genérico).
- Scrapers externos (Forebet / Sportytrader) como fallback.
- Normalización de identidad de mercado y reconciliación interno vs externo.
- Auditoría de predicciones externas contra fuerza del rival.
- Guardrails para **mercados UNKNOWN** (no edge, no trap, no discard).
- F74: **schema canónico** para enriquecimiento fútbol + probabilidades estimadas.
- F74-post: **adaptadores** para eliminar fragmentación de datos anidados.
- F74-post v2: **fallback de odds con TheStatsAPI** (incluye opening/last_seen → line movement sin snapshots históricos).
- F74-post v2.5: **line movement desde día 1** usando `opening` TheStatsAPI + `last_seen`.

### Objetivos nuevos / extendidos (F82)
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales.
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia.

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners desde datos presentes. **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual (cuota manual, selector de mercado, recalcular).
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular promedios xG no-penal (a favor / en contra) L1/L5/L15 por equipo usando shotmap TheStatsAPI.
- Arquitectura **background-first** con cache + timeouts.
- Señales contextuales (nunca pick-binding) + señales de cobertura/muestra parcial.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable (alinear tests con copy y flujos reales).

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - F84.a Team Stats ✅
  - F84.b H2H ✅
  - F84.e Odds + line movement ✅
- Flags + auditoría `_provenance_*`.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Scraping fail-soft y background-first con endpoints run-now/background/status.
- UI panel para disparo y render.
- Phase 2: resolver FBref search-page + fuzzy matching ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Definir cuándo H2H puede influir en scoring vs. cuándo es solo narrativo.
- Output: `h2h_context` enriquecido + `h2h_decision` (points_by_market + signals).

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football`
- Integrar H2H decision + xG recent averages (background) sin bloquear el camino crítico.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer
- Editorial output y UI consumen `h2h_decision` + `xg_recent_averages`.
- Scoring aplica bump H2H al mercado (clamp +8 + guards).

### Objetivos nuevos / extendidos (F89 / Sprint F86.1) — Calibración H2H rules + guards explícitas
- Recalibrar `H2H_POINT_RULES` contra baselines típicas (más robusto).
- Introducir `get_active_rules()` con override por env (JSON) leído en tiempo de llamada.
- Agregar polarity guard explícito (OVER/UNDER por línea + BTTS YES/NO) con auditoría.
- Agregar sample guard por regla (`min_sample`) + señal `LOW_SAMPLE_H2H_SIGNAL`.
- Agregar DNB overlap guard suave (HOME_DNB + AWAY_DNB no es hard-conflict).
- Agregar cap agregado de puntos H2H (`MAX_H2H_POINTS_TOTAL=8`).
- Mantener back-compat con consumers/editorial UI.

### Objetivos nuevos / extendidos (F90 / Sprint F83-update) — Corners cascade con diagnóstico estructurado (Scrape.do)
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (D13) — MLB Matchup Familiarity Overlay (secundario)
- Implementar una capa contextual MLB basada en enfrentamientos recientes (preferencia: últimos 15 días) que:
  - NO sea pick principal.
  - Sea puro/fail-soft, auditable.
  - **D13.1:** Totales (O/U) ✅
  - **D13.2:** extender a Moneyline + Runline y **permitir impacto en scoring real** con límites y veto ✅

### Objetivos nuevos / extendidos (NIVEL 3) — MLB Totals: Distribution Mixing + Tail Calibration + Threshold Models
- Agregar una capa compatible/auditable que NO reemplace el sistema actual, pero mejore:
  - probas O/U por umbral (7.5/8.5/…)
  - juegos de alta varianza (colas)
- **Bloques (estado actual):**
  - **Bloque 1 (Mixer):** mezcla Poisson/NB dinámica ✅
  - **Bloque 2 (§1-§4):** fórmula de pesos + tail calibration + threshold model + blender **(ACTIVO)** ✅
  - **Bloque 3 (§5-§6):** reglas hard de Under + UI “Distribución y colas” ⏳

### Objetivos nuevos / extendidos (D9.2-C) — Residual Model con xG real (Bonferroni estricto)
- Fortalecer el backtest residual para evitar falsos positivos por múltiples comparaciones:
  - Clasificador puro y testeable ✅
  - Corrección Bonferroni estricta ✅
  - Auditoría explícita de umbral ajustado y resultados por métrica ✅

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivos nuevos / extendidos (F95) — Football Post-Match Settlement Hotfix (P0)
**Contexto:** bug productivo donde partidos ya finalizados (ej. *Brazil vs Haiti*) siguen apareciendo como elegibles en “Generar picks del día”.

**Diagnóstico:** `settle_post_match()` existe (learning snapshots), pero no había job scheduler equivalente al de MLB; el sistema no persistía `POST_MATCH_RESULT_SETTLED` para fútbol de forma periódica.

**Objetivos F95 (P0):**
1. **Arreglar settlement post-match football:** correr settlement periódico que hidrate final_score/corners y escriba `POST_MATCH_RESULT_SETTLED` cuando sea posible.
2. **Robustecer gate de fixtures:** mantener guard de “kickoff_ts > 4h en el pasado” como defensa en profundidad para descartar stale fixtures incluso si el settlement se atrasa.
3. **Nueva cascada de proveedores (final_score football):** **TheStatsAPI → TheSportsDB → API-Sports**.
4. **Scheduler:** `_job_settle_finished_football` cada **20 min**, ventana **36h** hacia atrás.

---

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador
**(COMPLETADO)** — sin cambios.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**(COMPLETADO)** — sin cambios.

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**(COMPLETADO)** — sin cambios.

### Fase 4 — Integración en Football pipeline (override incluido)
**(COMPLETADO)** — sin cambios.

### Fase 5 — UI Wiring (P2)
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase SPRINT A — Draw Potential (piloto retrospectivo) (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT B — Football Learning Dataset + Loops + UI (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT D — Framework Backtest Histórico Point-in-Time (COMPLETED ✅)
(Sin cambios.)

---

## Phase SPRINT D2 — Backtest histórico en torneos nacionales (WC2022 + Euro2024) — COMPLETADO ✅
(Sin cambios.)

---

## Phase SPRINT D3 — Backtest National Tournaments: OVER 1.5 + DOUBLE CHANCE (calibration-only) — COMPLETADO ✅
(Sin cambios.)

---

## Phase SPRINT D4 — ROI honesto + significancia estadística + walk-forward verificado — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT D5 — Multi-league + multi-tournament DRAW + cohortes (observe_only) — EN PROGRESO 🟡 (P0)
(Sin cambios.)

---

## Phase SPRINT D6 — Probar que el Walk-Forward Calibrator NO es un no-op — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1 — Live Odds Monitor (Base) + persistencia `odds_snapshots` (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1 — Resolver Identidad de Mercado por The Odds API (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1-d — Hook automático (Scheduler) para Market Identity Resolver — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.1.1-f — 365Scores “Tendencias Top” (reemplazo SportyTrader editorial) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.2 — Odds Value Detector + Alerts (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

## Phase SPRINT E.3 — UI Odds Alerts + Comparador Manual (observe_only) — COMPLETADO ✅ (P0)
(Sin cambios.)

---

# Phase SPRINT D7 — Backtest comparativo DRAW (Ligas vs Selecciones) + Post-mortem & Remediación — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase SPRINT D7-E — Threshold parametrization + honest sweep + multi-season sanity check (DRAW) — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase SPRINT D7-F — OVER_2_5 / UNDER_2_5 con la misma disciplina (D7-E) — COMPLETADO ✅ (P1)
(Sin cambios.)

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡
(Sin cambios.)

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳
(Sin cambios.)

---

## Phase F95 — Football Post-Match Settlement Hotfix (P0) — ✅ COMPLETADO

### Resumen
- **Problema:** fixtures finalizados se quedaban en el feed de “Generar picks del día”; además no se escribía `POST_MATCH_RESULT_SETTLED`.
- **Root cause:** falta un job periódico para settlement football (sí existía para MLB).
- **Decisiones confirmadas por el usuario:**
  - “TheSportAPI” = **TheStatsAPI**.
  - Cascada de settlement: **TheStatsAPI → TheSportsDB → API-Sports**.
  - TheSportsDB API key: `THESPORTSDB_KEY=5129982501`.
  - Scheduler: cada **20 min**, ventana **36h**.

### F95.1 — Tests del guard 4h en `fixture_time_status_gate.py` ✅
- **Estado:** ✅ COMPLETADO.
- Archivo: `backend/tests/test_fixture_time_status_gate_stale_kickoff.py`.
- **19 tests** validando:
  - `DEFAULT_STALE_KICKOFF_MINUTES = 240`.
  - Override `STALE_KICKOFF_MINUTES` con clamp ≥ 60.
  - Fallbacks ante inputs inválidos / vacíos.
  - Discard correcto para kickoff >4h con/sin status terminal/scores.
  - 2h no se marca como stale (sigue siendo `ALREADY_STARTED`).
  - Kickoff futuro intacto.
  - Símbolos públicos exportados.

### F95.2 — Provider TheSportsDB reutilizado ✅
- Cliente existente `backend/services/external_sources/thesportsdb_client.py` cumple el contrato.
- Env: `THESPORTSDB_KEY=5129982501` ya presente en `backend/.env`.
- Endpoints expuestos:
  - V2 `fetch_livescore("soccer")` con normalización `FINISHED|LIVE|SCHEDULED|UNKNOWN`.
  - V1 `search_teams(name)`.

### F95.3 — Wrapper `football_finished_game_settler.py` ✅
- **Archivo nuevo:** `backend/services/football_finished_game_settler.py`.
- Cascada estricta:
  1) **`_lookup_from_db_matches`** (escenarios reutilizables).
  2) **`_lookup_from_thestatsapi`** (`fetch_match_details` con guard de status terminal).
  3) **`_lookup_from_thesportsdb`** (livescore filtrado por nombres normalizados + ventana ±1 día).
  4) **`_lookup_from_api_sports`** (`fixture_by_id` con guard de status terminal).
- Public API:
  - `lookup_final_score(match_id, snapshot_doc, *, db, http_client, kickoff_dt) → dict`.
  - `settle_recent_finished_football(db, *, hours_back=36, max_matches=50, http_client=None, settle_fn=None) → summary`.
- Reglas:
  - `MIN_AGE_HOURS_DEFAULT = 2.5` (override env `FOOTBALL_SETTLER_MIN_AGE_HOURS`, clamp ≥1.5).
  - Defence-in-depth: filtro Python adicional para `POST_MATCH_RESULT_SETTLED` y `sport=football`.
  - Fail-soft total: ninguna excepción cruza al caller.
  - `source_audit_entries` con stage `football_finished_game_settler`.
- **Reason codes nuevos:** `SETTLER_SCORE_FROM_DB_MATCHES`, `SETTLER_SCORE_FROM_THESTATSAPI`, `SETTLER_SCORE_FROM_THESPORTSDB`, `SETTLER_SCORE_FROM_API_SPORTS`, `SETTLER_NO_FINAL_SCORE_AVAILABLE`.
- **18 tests** en `backend/tests/test_football_finished_game_settler.py`.

### F95.4 — Scheduler job `_job_settle_finished_football` ✅
- **Archivo tocado:** `backend/services/scheduler.py`.
- Job registrado con `IntervalTrigger(minutes=20)`, id `settle_finished_football`, `next_run_time=+4min`.
- Persiste métricas en `_status["last_run"]["settle_finished_football"]`.
- Fail-soft: cualquier excepción se logea + se persiste `{ok: False, error}`.
- **6 tests** en `backend/tests/test_scheduler_football_settler_job.py`.
- **Verificación en logs:** `Scheduler started with jobs: [... 'settle_finished_baseball', 'settle_finished_football', ...]`.

### F95.5 — Validación pytest completa ✅
- Suite backend: **4199 passed / 2 skipped** (vs 4156 antes).
- **+43 tests nuevos** (19 + 18 + 6).
- **0 regresiones**.

### Cambios persistidos
| Archivo | Cambio |
|---|---|
| `backend/services/fixture_time_status_gate.py` | Guard #5 stale-kickoff (240 min) + `get_stale_kickoff_minutes()` (ya presente). |
| `backend/services/football_finished_game_settler.py` | **NUEVO** — wrapper + cascada de 3 fuentes. |
| `backend/services/scheduler.py` | `_job_settle_finished_football` + registro `IntervalTrigger(20 min)`. |
| `backend/tests/test_fixture_time_status_gate_stale_kickoff.py` | **NUEVO** — 19 tests. |
| `backend/tests/test_football_finished_game_settler.py` | **NUEVO** — 18 tests. |
| `backend/tests/test_scheduler_football_settler_job.py` | **NUEVO** — 6 tests. |
| `backend/.env` | `THESPORTSDB_KEY=5129982501` (preexistente, sin tocar). |

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ **F95** completado (settler football + scheduler job + guard 4h tests).
- 🟡 **SPRINT D5** (histórico en curso): cohortes + reportes multi-competición.

### Pendientes P1
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.
- ⏳ **D8 Fase 2** — selecciones (DRAW + cohorte favorito-dominante) con MAX_CREDITS=2500 (bloqueado por ground truth Copa América 2024).
- ⏳ **NIVEL 3 Bloque 3 (§5-§6):** reglas hard de Under + UI “Distribución y colas”.
- ⏳ (Opcional) UI para exponer:
  - `matchup_familiarity_overlay` (impact + snapshots)
  - `run_distribution_mixer` + `tail_calibration` + `threshold_over_model` + `distribution_blender` (comparación vs NB canónico y blend final)

### Pendientes P2
- ⏳ Expandir `team_name_translations.py`.
- ⏳ Nuevas hipótesis de señal para O/U 2.5:
  - features adicionales (lineups, std de xG, matchup/estilos, fatiga),
  - calibración por liga (pero con guardrails anti-overfitting),
  - o pivotear a otro mercado/línea.

---

## 4) Cierres recientes (bitácora)

### ✅ SPRINT D12 — Cierre (NB Recalibration Wiring + UI “Riesgos ocultos del Under”) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ SPRINT D13 — MLB Matchup Familiarity Overlay (D13.1) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ SPRINT D13.2 — Matchup Familiarity Overlay extendido a ML/RL + Active Scoring — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ NIVEL 3 — Bloque 1 · Dynamic Run Distribution Mixer — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ NIVEL 3 — Bloque 2 (§1-§4) · Tail Calibration + Threshold Model + Blender **ACTIVO** — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

### ✅ SPRINT D9.2 Block C — Residual Model con xG real (Bonferroni estricto) — COMPLETADO
(Sin cambios; ver bitácora previa.)

---

## 6) Validación esperada (estado actual)

- Reglas:
  - Cero regresión post-cada cambio.
  - Fail-soft y back-compat.
  - Point-in-time correctness en backtests.
  - Observe-only por defecto; excepciones explícitas:
    - D13.2: scoring activo con clamps + snapshots.
    - NIVEL 3 Bloque 2: ACTIVE writeback a `expected_runs_distribution`.
  - Backend: ejecutar `pytest` completo tras cambios.

**Estado actual de la suite backend (post-F95):** `4199 passed / 2 skipped` (0 regresiones; +43 tests vs F94).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **No tocar** `MONGO_URL` ni `REACT_APP_BACKEND_URL`.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - **Nuevo (F95):** `THESPORTSDB_API_KEY=5129982501`.

---

## SPRINT F — Ingesta de Tendencias Top desde 365Scores — COMPLETADO ✅
(Sin cambios.)

---

## SPRINT D8 — UNDER_3_5 (ligas) + DRAW/cohorte (selecciones)
(Sin cambios.)

---

## SPRINT D9.2 — Block 0 + A + B (COMPLETADO ✅)
(Sin cambios.)

---

## SPRINT D9.3 — Active Series Context Fix + Expansion (P0 hotfix)
(Sin cambios.)
