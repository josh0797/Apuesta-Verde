# Plan — Phases F58–F91 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + ✅ **F93 (Corners cascade TSA→APS→TotalCorner→365→FootyStats vía scrape.do) COMPLETADA** + ✅ **Bugfix (upcoming filter elimina FT/PST/CANC/AET/PEN) COMPLETADA**.
>
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
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según:
  - proveedor
  - etapa (`ID_RESOLUTION`, `FETCH_STATS`, `FETCH_PAGE`, `PARSE_HTML`, `NORMALIZE`, etc.)
  - `reason_code` (token ausente, breaker abierto, HTTP 403/429/503, HTML sin stats, stats sin córners, etc.)
- Exponer un endpoint de diagnóstico:
  - `GET /api/football/corners/debug?match_id=...`
- Añadir UI para debug:
  - botón **"Ver debug de córners"**
  - dialog con cascade order usado, estado scrape.do (token+breaker) y providers_checked.
- Mantener el order por defecto de F82.2 (TSA→APS→365) y habilitar un order alternativo bajo flag:
  - `ENABLE_F83_CASCADE_ORDER=true` → APS→365→TSA

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre:
  - calidad real del contacto ofensivo (xwOBA, sweet-spot%, barrel%, hard-hit%)
  - vulnerabilidad del abridor (xERA, xwOBA allowed, barrel% allowed, hard-hit% allowed)
  - percepción pública basada en ERA
- **No generar picks automáticos**: solo output explicable con señales.
- Entregar un **módulo puro** con:
  - lineup_contact_quality (ponderado por orden al bate)
  - pitcher_vulnerability (0–100)
  - matchup_contact_factor y contact_mismatch_score
  - detector de regresión (xERA − ERA)
  - señales: `MATCHUP_CONTACT_ADVANTAGE`, `PITCHER_BARREL_REGRESSION_RISK`, `ERA_UNDERSTATES_DAMAGE`, `TOP_ORDER_THREAT`, `OVER_CONTACT_WARNING`
- Datos por bateador:
  - por defecto **derivados** desde team-level
  - flag `QCM_LINEUP_PER_BATTER=true` para consumir per-batter real en el futuro
- Thresholds:
  - defaults hardcoded + override por env `QCM_THRESHOLDS` JSON
- Scope acordado:
  - **solo módulo + tests + payload fail-soft**
  - NO crear `mlb_under_discovery.py` ni `pick_ranking.py`
  - NO modificar `picks[]` ni ranking aún

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

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase F69 — Fix análisis editorial interno match-specific (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

## Phase F70 — Reemplazo externo (Sportytrader / Forebet) (COMPLETED ✅)
**(COMPLETADO)** — ver historial en este mismo archivo.

---

# Phase F74 — Parcial: Unified Football Enrichment + Protected Floor Recalibration (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post — Resolver ingesta interna, market identity y puente TheStatsAPI/API-Sports (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2 — TheStatsAPI Odds Fallback Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F74-post v2.5 — Opening Odds → Line Movement Wiring (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82 — Rich H2H Context + 365Scores Corners Ingestion (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1 — Non-blocking H2H/Corners Enrichment + Job Timeout Protection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83 — Manual Market Identity + Manual Odds Injection (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.1-adjust — Manual/Background Corners Enrichment Endpoints (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.1-fix — Manual Market Identity match_id isolation + Data Availability Sections (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase P2 — `infer_original_pick_side` (4-source cascade) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F82.2 — Scores24 → 365Scores cross integrator + provider re-order (Backend COMPLETED ✅ / Frontend COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F83.2 (Bloque E) — xG L1/L5/L15 desde TheStatsAPI shotmap (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase P4.1 — Fix tests preexistentes LiveReevalPanel (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F84 — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F85 — Public xG Enrichment (FBref + Forebet vía scrape.do) (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F86 — H2H Decision Policy (COMPLETED ✅)
**(COMPLETADO)** — ver fases F87/F88/F89 para wiring y calibración.

---

# Phase F87 — Cableado quirúrgico H2H Decision + xG-recent background dispatch (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F88 — Sprint F86.2: Editorial Consumer para H2H decision + xG recent averages (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F89 — Sprint F86.1: Calibración H2H_POINT_RULES + explicit polarity guard + sample guard (COMPLETED ✅)
**(COMPLETADO)** — sin cambios.

---

# Phase F90 — Sprint F83-update: Corners cascade con diagnóstico estructurado vía Scrape.do + flag F83 cascade order (COMPLETED ✅)
**(COMPLETADO)** — ver historial F90 en este mismo archivo.

---

# Phase F91 — MLB Quality Contact Matchup Engine (módulo puro) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

(Detalles del engine puro mantenidos arriba; ver `mlb_quality_contact_matchup.py` + 36 tests focales.)

---

# Phase F92 — MLB QCM Signals Applier + Pipeline Wiring (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Decisión de scope (acordada)
- ✅ Aplicar `UNDER_CONTACT_RISK` (penalización pequeña a Unders) y `CONTACT_EXPLOSION_POTENTIAL` (boost moderado a Overs).
- ✅ Wiring vía `seal_pick_payload` (no se duplica orquestación). El applier es puro.
- ✅ Polarity guard, clamps `[MAX_UNDER_PENALTY, MAX_OVER_BOOST]`, severity bonus (`SEVERE_REGRESSION_RISK`).
- ✅ F5 Under sólo si `TOP_ORDER_THREAT` activo.
- ✅ Hard veto NO se aplica en este layer (queda como hint en `qcm_audit.hard_veto_hint` para uso futuro de `mlb_under_veto_layer`).
- ✅ Override por env `QCM_APPLIER_DELTAS` (JSON) leído en tiempo de llamada (patrón F86.1).

## Implementación ejecutada

### Backend
1) **NEW** `backend/services/mlb_qcm_signals_applier.py`
- `apply_qcm_to_candidate(candidate, qcm_block, *, deltas=None, logger=None) → audit dict`.
- `apply_qcm_to_candidates(candidates, qcm_block, *, deltas=None) → list[audit]`.
- `qcm_hard_veto_active(qcm_block) → bool` (consumible por veto layer).
- Constantes públicas:
  - `SIGNAL_UNDER_CONTACT_RISK`, `SIGNAL_CONTACT_EXPLOSION_POTENTIAL`.
  - `RC_QCM_NO_DATA`, `RC_QCM_NOT_APPLICABLE`, `RC_QCM_POLARITY_CONFLICT`, `RC_QCM_CLAMPED`, `RC_QCM_VETO_TRIGGERED`.
- `_market_classification(market) → {side, period, is_team_total}` cubre `OVER`/`UNDER`, `F5/1H/FIRST_5`, `TEAM_TOTAL/TT`.
- `_contact_explosion_active`: requiere `PITCHER_BARREL_REGRESSION_RISK` + `ERA_UNDERSTATES_DAMAGE` + `MATCHUP_CONTACT_ADVANTAGE`.
- `_under_contact_risk_active`: `contact_mismatch_score ≥ UNDER_FULL_GAME_THRESHOLD`.
- Mutación del candidate (in-place):
  - `confidence_score` ajustado por delta, y `confidence` espejado si existía.
  - Append a `signals` / `reason_codes` (no duplicados).
  - `score_breakdown.qcm_contact` con la auditoría (delta, signal, side, period, mismatch_score, regression_risk, clamped, hard_veto_hint).

2) **MOD** `backend/services/mlb_pipeline_payload_contract.py`
- Tras adjuntar `quality_contact_matchup`, ejecuta `_apply_qcm_signals_to_picks(payload)` (fail-soft).
- Expone bloque `qcm_audit` en el payload con:
  - `applied_count`, `hard_veto_hint`, `audits[]` (uno por pick, con `pick_index`).
- Preserva orden de `picks[]` y nunca añade/quita picks.
- Coerción QCM se hace ANTES del coerce del advanced snapshot para evitar overwrite del legacy `*_team_advanced` (mantiene la regla F91).

### Tests
3) **NEW** `backend/tests/test_f92_qcm_signals_applier.py` (24 tests).
4) **MOD** `backend/tests/test_mlb_quality_contact_matchup.py` — el test de wiring fue actualizado para reflejar que ahora F92 muta picks intencionalmente con auditoría completa en `qcm_audit` (preservando `market`, longitud y orden).

## Validación
- ✅ Tests focales F92: **24/24 PASS**.
- ✅ Tests focales F91+wiring: **39/39 PASS** (63 tests combinados QCM verdes).
- ✅ Suite completa backend: **2698 passed, 2 skipped, 0 failed** en 176s.
- ✅ Suite completa frontend: **125 passed / 12 suites** en 19s.
- ✅ Lint Ruff limpio.
- ✅ Cero regresiones (subimos de 2671 → 2698 backend).

## Flags / env
- `QCM_APPLIER_DELTAS='{"UNDER_FULL_GAME_THRESHOLD": 70.0, "UNDER_FULL_GAME_PENALTY": -4, ...}'` permite override JSON en runtime.

---

# Phase F93 — Corners cascade migration (TotalCorner + FootyStats vía scrape.do) (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Decisión de scope (acordada)
- ✅ Despriorizar 365Scores a posición 4 de 5.
- ✅ Nueva cascada por defecto: **TheStatsAPI → API-Sports → TotalCorner → 365Scores → FootyStats**.
- ✅ Tanto TotalCorner como FootyStats vía `services.scrape_do_client` (sin nuevas API keys).
- ✅ Fail-soft estricto: cada proveedor expone `reason_code` granular + `message_user` + `message_debug`.
- ✅ Compatibilidad hacia atrás: la cascada F82.2 (3 pasos) y F83 (3 pasos en orden alternativo) siguen accesibles bajo flags.

## Implementación ejecutada

### Backend
1) **NEW** `backend/services/external_sources/totalcorner_scrapedo_client.py`
- Resolver de URL: `external_ids.totalcorner.match_url` → `match_id` (URL canónica) → campos legacy.
- `fetch_totalcorner_match_page(url, timeout_s, render=True)` vía `fetch_via_scrapedo_result`.
- Parser HTML robusto (regex `<tr><th>label</th><td>home</td><td>away</td></tr>`) con aliases multilingües: `corners`, `corner kicks`, `tiros de esquina`, `córner`, `escanteios`.
- Reason codes propios: `TOTALCORNER_URL_MISSING`, `_STATS_EMPTY`, `_CORNERS_NOT_FOUND`, `_BLOCKED_OR_FORBIDDEN`, `_HTML_PARSE_FAILED`, `CORNERS_FROM_TOTALCORNER_SCRAPEDO`.

2) **NEW** `backend/services/external_sources/footystats_scrapedo_client.py`
- Resolver de URL: `external_ids.footystats.match_url` → `slug` (URL canónica) → legacy.
- `fetch_footystats_match_page` vía `fetch_via_scrapedo_result`.
- Parser HTML con 3 patrones complementarios:
  - `data-stat="corners"` (estructura limpia).
  - Bloque label-HOME-AWAY (`<div>5 Corners 4</div>`).
  - Loose triplet "label THEN two numbers" (último recurso).
- Reason codes propios: `FOOTYSTATS_URL_MISSING`, `_STATS_EMPTY`, `_CORNERS_NOT_FOUND`, `_BLOCKED_OR_FORBIDDEN`, `_HTML_PARSE_FAILED`, `CORNERS_FROM_FOOTYSTATS_SCRAPEDO`.

3) **MOD** `backend/services/football_corners_provider.py`
- Nuevos probes:
  - `_f93_check_totalcorner(match_doc, *, timeout_s)` — resuelve URL, fetch via scrape.do, parse HTML.
  - `_f93_check_footystats(match_doc, *, timeout_s)` — idem.
- Nuevo flag: `is_f93_cascade_order_enabled()` con default **True** y override `ENABLE_F93_CASCADE_ORDER=false`.
- Nuevo resolver de orden `_resolve_cascade_order()` (precedencia: F93 → F83 → F82.2):
  - F93 (default): `[thestatsapi, api_sports, totalcorner, 365scores, footystats]`.
  - F83 (legacy, sólo si F93 explícitamente off): `[api_sports, 365scores, thestatsapi]`.
  - F82.2 (sólo si ambos flags off): `[thestatsapi, api_sports, 365scores]`.
- `debug_corners_cascade(...)` ahora:
  - itera la cascada según `_resolve_cascade_order()`.
  - emite `cascade_flag` (`"F93"` | `"F83"` | `"F82.2"`).
  - cada probe respeta `allow_external=False` (no HTTP en modo rápido) emitiendo `*_SKIPPED_INLINE` sin awaits.
  - mantiene `_persist`, `enrich_match_corners_f83`, `score365_timeout_seconds`, `breaker_status` y `is_enabled` para back-compat.
- Nuevos reason codes exportados: `RC_TOTALCORNER`, `RC_FOOTYSTATS`, `RC_TOTALCORNER_EMPTY`, `RC_FOOTYSTATS_EMPTY`.

### Tests
4) **NEW** `backend/tests/test_f93_corners_cascade.py` — **32 tests** que cubren:
- Resolvers TotalCorner / FootyStats (explicit URL, slug/match_id, legacy fields, missing → fail-soft).
- Parser TotalCorner (`<tr><th>Corners</th><td>9</td><td>5</td></tr>`, aliases, sin córners, HTML vacío).
- Parser FootyStats (data-stat, bloque label, loose triplet, HTML vacío).
- Fetch fail-soft: URL vacía, HTTP 403 → mapeado a `*_BLOCKED_OR_FORBIDDEN`, timeout.
- `_resolve_cascade_order()` con 4 escenarios (default, F93 explícito, F93 off → F82.2, F83 only).
- `debug_corners_cascade` end-to-end mocked:
  - TheStatsAPI gana temprano → TC + FS nunca se invocan.
  - TotalCorner gana → 365Scores y FootyStats no se llaman.
  - FootyStats es last-resort → todos los 5 proveedores aparecen en `providers_checked`.
  - `allow_external=False` evita TODOS los HTTP probes (TC, 365, FS skipped en orden).
- Contrato no-raise (resolvers + parsers + cascade con inputs basura).

5) **MOD** `backend/tests/test_f83_update_corners_debug.py` — actualizados 3 tests pre-existentes para reflejar la nueva default F93 + agregado test específico para fallback F82.2 cuando ambos flags están off.

## Validación
- ✅ Tests focales F93: **32/32 PASS**.
- ✅ Tests F83 corners debug (legacy + F93 wiring): **30/30 PASS**.
- ✅ Suite completa backend: **2782 passed, 2 skipped, 0 failed** en 176s.
- ✅ Suite completa frontend: **125 passed / 12 suites** en 6s.
- ✅ Lint Ruff limpio en los 3 archivos nuevos/modificados.
- ✅ Cero regresiones (subimos de 2698 → 2782 backend, +84 nuevos tests).

## Flags / env
- `ENABLE_F93_CASCADE_ORDER=true` (default) — cascada F93 de 5 proveedores.
- `ENABLE_F93_CASCADE_ORDER=false` + `ENABLE_F83_CASCADE_ORDER=true` — cascada legacy F83 (3 proveedores).
- Ambos `false` — cascada legacy F82.2 (3 proveedores).
- `FOOTBALL_365SCORES_TIMEOUT_MS=3500` — aplica también a TotalCorner y FootyStats (timeout compartido vía scrape.do).
- Sin nuevas API keys requeridas (todo el transporte usa `SCRAPEDO_TOKEN`).

---

# Bugfix — Upcoming filter rechaza partidos terminados / aplazados / cancelados (COMPLETED ✅)

## Estado: ✅ COMPLETADA

## Reporte del usuario
- "Otra vez está trayendo partidos ya terminados" — Bournemouth vs Manchester City, Ried vs Wolfsberger AC, Genk vs Antwerp, Hapoel Beer Sheva vs Maccabi Tel Aviv aparecían en *Descartados de ligas prioritarias* con badge `Frag 24` y razón "Mercado descartado por market identity missing", aunque ya habían finalizado.

## Causa raíz
- En `server._run_analysis_pipeline` el filtro de candidatos `upcoming` se hacía solo con `kickoff_ts >= now_ts - 600`. Si el documento DB tenía `status_short=FT` pero su `kickoff_ts` quedaba en el futuro o se reusaba para otro fixture, el partido finalizado pasaba al scoring.
- No había guard explícito por `status_short` ni por `status` de larga forma (TheStatsAPI / ESPN / MLB Stats API).

## Implementación ejecutada

### Backend
1) **MOD** `backend/server.py` — nuevo helper centralizado:
- `_TERMINAL_FOOTBALL_STATUSES = {FT, AET, PEN, FT_PEN, PST, CANC, ABD, AWD, WO, SUSP, INT}`.
- `_TERMINAL_GENERIC_STATUSES = {post, final, completed, ended, postponed, cancelled, abandoned, walkover, suspended, "match finished", ...}`.
- `_is_match_upcoming(match_doc, *, now_ts=None, grace_seconds=600) -> bool` con 4 guards independientes:
  1. `kickoff_ts >= now - grace_seconds`.
  2. `status_short` ∉ `_TERMINAL_FOOTBALL_STATUSES` (case-insensitive).
  3. `status` (str o dict-anidado) ∉ `_TERMINAL_GENERIC_STATUSES`.
  4. Safety net: si `kickoff_ts` está en el pasado y hay `home_score`+`away_score` numéricos persistidos (top-level o dentro de `home_team`/`away_team`), el partido se considera terminado.
- `_filter_upcoming_candidates(matches, *, grace_seconds=600)` aplica el guard a una lista y emite un log de auditoría con sample de los drops.
- Reemplazadas **6 ocurrencias** del filtro inline `(c.get("kickoff_ts") or 0) >= now_ts - 600` (en el pipeline + en `/api/matches/upcoming` + en 4 ramas de fallback MLB / ESPN NBA / SofaScore).

### Tests
2) **NEW** `backend/tests/test_upcoming_filter_finished_dropoff.py` — **51 tests** que cubren:
- `_is_match_upcoming` con kickoff futuro/pasado/grace.
- TODOS los `_TERMINAL_FOOTBALL_STATUSES` (parametrizado).
- TODOS los `_TERMINAL_GENERIC_STATUSES` (parametrizado).
- Status dict anidado (caso MLB legacy).
- Documents legacy sin `status_*` con `kickoff_ts` futuro siguen pasando.
- Safety net: `kickoff_ts` pasado + `home_score`/`away_score` numéricos → drop.
- Inputs basura no levantan excepción.
- `_filter_upcoming_candidates`: empty list, mixed list, preserva orden, `grace_seconds` configurable.
- Caso real reportado: drop explícito de Bournemouth-MC, Genk-Antwerp, Hapoel-Maccabi, Ried-Wolfsberger.

## Validación
- ✅ Tests focales: **51/51 PASS**.
- ✅ Suite completa backend: **2782 passed, 2 skipped, 0 failed**.
- ✅ Frontend: **125/125 PASS**.
- ✅ Backend re-arranca limpio (sin errores en `/var/log/supervisor/backend.err.log`).
- ✅ Cero regresiones.

---

## Estado: ✅ COMPLETADA

## Decisión de scope (acordada)
- ✅ Solo módulo puro + tests + output fail-soft.
- ✅ NO crear `mlb_under_discovery.py` ni `pick_ranking.py` (no existen en este repo).
- ✅ NO modificar `picks[]` ni ranking.
- ✅ Métricas por bateador derivadas por defecto; flag `QCM_LINEUP_PER_BATTER=true` para real per-batter en el futuro.
- ✅ Thresholds hardcoded + override por env `QCM_THRESHOLDS` (JSON).

## Implementación ejecutada

### Backend
1) **NEW** `backend/services/mlb_quality_contact_matchup.py`
- Implementa el engine completo como módulo puro:
  - **Weighted Lineup Quality Score** con `LINEUP_WEIGHTS`.
  - **Offensive Contact Quality Score** (xwOBA/sweet-spot/barrel/hard-hit) con escalado 0–100.
  - **Pitcher Vulnerability Score** con escalado 0–100.
  - **ERA Regression Detector** (`era_gap = xERA − ERA`) con niveles:
    - `SEVERE_REGRESSION_RISK` (≥ 1.50)
    - `HIGH_REGRESSION_RISK` (≥ 1.00)
    - `MODERATE_REGRESSION_RISK` (≥ 0.50)
    - `NORMAL`
  - **Matchup Contact Factor**: `lineup_quality * (1 + barrel_pct_allowed)`.
  - **Contact Mismatch Score**: `(lineup_quality * pitcher_vulnerability)/100` (0–100).
  - **Signals**:
    - `MATCHUP_CONTACT_ADVANTAGE`
    - `PITCHER_BARREL_REGRESSION_RISK`
    - `ERA_UNDERSTATES_DAMAGE`
    - `TOP_ORDER_THREAT`
    - `OVER_CONTACT_WARNING`
- **Thresholds**:
  - defaults hardcoded en `_DEFAULT_THRESHOLDS`
  - override por env `QCM_THRESHOLDS` (JSON) leído **en tiempo de llamada** (`get_active_thresholds`).
- **Fuentes de datos (mock/derivado, acordado)**:
  - Reutiliza `mlb_advanced_stats_helpers.extract_mlb_advanced_context()` + `_team_block/_pitcher_block`.
  - Default: deriva 9 filas de bateadores desde snapshot de equipo con jitter posicional.
  - Futuro: con `QCM_LINEUP_PER_BATTER=true` consume `payload.lineups.official.<side>[].statcast`.
- **Fail-soft**:
  - si faltan datos → `available=false`, `reason_codes=["QCM_INSUFFICIENT_DATA"]`, `signals=[]`.
- **Output**:
  - `compute_quality_contact_matchup(payload)` devuelve el bloque `quality_contact_matchup` con:
    - `available`, `lineup_contact_quality`, `pitcher_vulnerability`, `matchup_contact_factor`, `contact_mismatch_score`, `era_gap`, `regression_risk`, `signals`
    - `reason_codes` (provenance: REAL vs DERIVED)
    - `thresholds_used` (auditoría)
    - `score_breakdown` (per-batter weighted + pitcher_metrics)

### Tests
2) **NEW** `backend/tests/test_mlb_quality_contact_matchup.py`
- **36 tests** que cubren:
  - invariantes de pesos `LINEUP_WEIGHTS`
  - scoring primitives (batter score, lineup quality, pitcher vulnerability)
  - clasificación de gap xERA−ERA
  - override de thresholds por env
  - flag `QCM_LINEUP_PER_BATTER`
  - señales (incluye TOP_ORDER_THREAT y OVER_CONTACT_WARNING con override)
  - audit `score_breakdown`
  - garantía de no tocar `picks[]`

## Validación
- ✅ Ruff lint clean.
- ✅ Tests F91: **36/36 PASS**.
- ✅ Suite completa backend: **2671 passed, 2 skipped, 0 failed** (2635 → 2671).
- ✅ Cero regresiones.

---

## 3) Pendientes y siguientes pasos (post-F91)

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Próximos sprints recomendados para MLB (post-F91)
- **F91.1 — Emisión al pipeline MLB**: integrar `quality_contact_matchup` en el payload contract (p.ej. `mlb_pipeline_payload_contract.py` o punto único equivalente del pipeline). *(En F91 solo se entregó el módulo puro + tests).*
- **F91.2 — Señales impactan confianza (sin picks automáticos)**:
  - Under: penalización `UNDER_CONTACT_RISK` en el layer de fragilidad (p.ej. `mlb_under_fragility_calibrator.py`).
  - Over: boost moderado `CONTACT_EXPLOSION_POTENTIAL` en `mlb_over_discovery.py` cuando coincidan: contact_factor alto + barrel risk + regression.
- **F91.3 — UI panel MLB**: renderizar scores + signals + breakdown (narrativo) sin generar picks.
- **F91.4 — Per-batter real**: habilitar `QCM_LINEUP_PER_BATTER=true` cuando `mlb_official_lineups.py` exponga métricas por bateador (Statcast).

### Futuras mejoras recomendadas (global)
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds, `MAX_H2H_POINTS_TOTAL` y el cap DNB.
- Implementar calibrador offline cuando exista una fuente estable (p.ej. `football_market_results`) + endpoint opcional.
- Para FBref Phase 2: ampliar heurísticas (country/team_type) para equipos UCL/UEL.
- Para odds: comparar `bookmakers_count` TSA vs APS como métrica de calidad.

---

## 6) Validación esperada (estado actual)

- Flags:
  - `ENABLE_API_SPORTS_FALLBACK=true` mantiene modo conservador.
  - `ENABLE_API_SPORTS_FALLBACK=false` activa modo “TheStatsAPI-only”.
  - `ENABLE_INLINE_PUBLIC_XG_SCRAPING=false` mantiene scraping fuera del camino crítico.
  - `H2H_POINT_RULES_OVERRIDE` permite override JSON en runtime (solo en policy; tests usan monkeypatch).
  - `ENABLE_F83_CASCADE_ORDER=true` (opcional) invierte corners cascade a **APS → 365Scores → TSA**.
  - `QCM_LINEUP_PER_BATTER=true` (opcional) activa path real per-batter (cuando exista data) — default off.
  - `QCM_THRESHOLDS='{...}'` (opcional) override de thresholds del engine QCM.
- Auditoría runtime:
  - `match_doc._provenance_team_stats`, `_provenance_h2h`, `_provenance_odds` presentes.
  - `match_doc.h2h_context` + `match_doc.h2h_decision` presentes tras ingesta (F87).
  - `match_doc.xg_recent_averages.status`: `PENDING_BACKGROUND_ENRICHMENT → SUCCESS|UNAVAILABLE|TIMEOUT`.
  - Editorial output incluye:
    - `editorial.h2h_block` (consumer-grade)
    - `editorial.xg_block` (PENDING/SUCCESS/TIMEOUT/UNAVAILABLE + tabla L1/L5/L15)
  - `best_protected_market.confidence_score` puede incorporar bump H2H con clamp+polarity.
  - Corners debug:
    - `GET /api/football/corners/debug?match_id=...` expone `cascade_order_used`, `flag_enabled`, `providers_checked` y `winner`.
  - MLB QCM (F91):
    - `services.mlb_quality_contact_matchup.compute_quality_contact_matchup(payload)` produce el bloque `quality_contact_matchup` (aún sin integración al pipeline).
- No regresiones:
  - Backend `pytest` verde (actual: **2671 passed, 2 skipped**).
  - Frontend `craco test` verde (actual: **125 passed**).
- Fail-soft:
  - Si TheStatsAPI falla → fallback API-Sports o bloque vacío.
  - Si scraping FBref/Forebet falla → no bloquea; UI muestra parcial.
  - Si xG recent averages falla/timeout → no bloquea; UI informa estado.
  - Si corners fallan → nunca rompe el análisis; UI informa reason_code y ofrece debug.
  - MLB QCM: si faltan métricas → `available=false` y no bloquea ningún pipeline.
