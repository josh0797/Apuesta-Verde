# Plan — Phases F58–F91 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade: TheStatsAPI → API-Football → ESPN → Sofascore PW → scrape.do + Unknown Bucket + MLB Isolation) COMPLETADA**.
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
- ✅ Cero regresiones (capa inicial — luego endurecida por el Hard Fixture Gate).

---

# Fixture Time/Status Hard Gate (PREMATCH_BUFFER_MINUTES) (COMPLETED ✅)

## Spec usuario
- `FINAL_STATUSES = {FT, AET, PEN, CANC, PST, ABD, AWD, WO, FINAL, FINISHED, COMPLETED, ...}` deben **bloquearse**.
- Aplicar `start_time > now + PREMATCH_BUFFER_MINUTES` (default **10 min**, env override).
- Barrera dura **antes de** market identity → SportyTrader → odds → fragility → ranking → picks[].
- Payload de descarte estructurado: `{discard_reason, stage, status, start_time, now, match_id, home, away}`.

## Implementación
1) **NEW** `backend/services/fixture_time_status_gate.py`
- `FINAL_STATUSES` + `LIVE_STATUSES` (frozensets canónicos).
- `RC_ALREADY_FINISHED`, `RC_ALREADY_STARTED`, `RC_KICKOFF_TOO_SOON`, `RC_KICKOFF_TIME_MISSING`, `RC_INVALID_INPUT`.
- `get_prematch_buffer_minutes()` — lee `PREMATCH_BUFFER_MINUTES` env (default 10, clamp ≥ 0).
- `check_fixture_gate(doc, *, now=None, buffer_minutes=None) → dict`:
  1. Terminal status guard (status_short / status / fixture.status.short / abstract MLB).
  2. Score safety net (kickoff pasado + scores numéricos → finalizado).
  3. Live status guard (`1H`, `HT`, `2H`, `LIVE`, `IN_PLAY`, etc.).
  4. Kickoff time guard: `start_time > now + buffer_minutes`.
- `filter_fixtures_through_gate(matches, *, now, buffer_minutes, audit_sink)` — list-level con audit trail JSON-serializable.

2) **MOD** `backend/server.py`
- `_is_match_upcoming` y `_filter_upcoming_candidates` delegan al gate (mantienen firma legacy; `grace_seconds` es no-op).
- Barrera dura insertada **antes** del football_quality filter / market identity / SportyTrader / odds.
- `pipeline_meta["fixture_gate"]` con `{stage, buffer_minutes, before, kept, dropped, audit[]}`.

3) **NEW** `backend/tests/test_fixture_time_status_gate.py` — **39 tests**.

## Validación
- ✅ 39/39 tests focales del gate + 51/51 tests del wrapper legacy.
- ✅ Suite backend completa **2782 → estabilizada**.

---

# Pipeline Debug Instrumentation + /api/diagnostics/api-health (COMPLETED ✅)

## Spec usuario
- Counters por etapa: `provider_response_count`, `raw_fixtures_count`, `after_sport_filter_count`, `after_date_window_count`, `after_priority_league_filter_count`, `after_status_filter_count`, `after_market_filter_count`, `analysis_candidates_count`, `failure_stage`.
- Health-check de proveedores con shape `{provider, request_sent, response_received, http_status, fixtures_returned, response_time_ms, error, status}`.
- Endpoint `/api/diagnostics/api-health` con `api_health` + `summary`.
- Mensaje claro cuando `provider_response_count=0` ("No se recibieron partidos desde el proveedor. Revisa provider, fecha, deporte o caché.").
- Determinar con evidencia la **primera etapa** donde el conteo cae a cero.

## Implementación

### Backend
1) **NEW** `backend/services/pipeline_debug.py`
- `PipelineDebug` dataclass con `record(stage, count, note)` + audit trail.
- `ORDERED_STAGES`: tupla canónica con las 8 etapas en orden spec.
- `failure_stage` (property): primera etapa con count==0 **O** primera etapa no-registrada con downstream zero (detecta saltos silenciosos).
- `failure_message`: mensaje user-facing en español por etapa.
- `to_dict()`: JSON shape spec (`*_count` keys + `failure_stage` + `failure_message` + `stages[]`).
- `empty_debug_payload()`: helper para paths que bail-out early.

2) **NEW** `backend/services/api_health_check.py`
- Probes individuales: `_probe_api_sports`, `_probe_thestatsapi`, `_probe_sportytrader`, `_probe_totalcorner`, `_probe_footystats`.
- Cada probe: HTTP real con timeout (API-Sports/TheStatsAPI) o check de breaker (scrape.do providers).
- `check_all_providers(*, timeout_s, only, probes)`: corre concurrente con `asyncio.gather` + per-probe timeout. Nunca raise.
- Status: `OK`, `DEGRADED` (responde pero 0 fixtures), `DOWN` (HTTP error/timeout/exception), `DISABLED` (key missing).

3) **MOD** `backend/server.py`
- `_run_analysis_pipeline` instrumentado en **5 puntos** (provider response → raw + sport + date → priority league → status → market → analysis).
- `pipeline_meta["pipeline_debug"]` + `pipeline_debug_failure_stage` + `pipeline_debug_failure_message` en top-level.
- Log `WARNING` cuando funnel cae a 0.

4) **NEW endpoint** `GET /api/diagnostics/api-health`
- Query params: `timeout_s` (default 8s), `providers` (whitelist).
- Respuesta: `{ok, checked_at, timeout_s, api_health: {…}, summary: {ok, degraded, down, disabled, skipped, total}}`.
- Nunca 500: errores se convierten a `status: DOWN` con `error` poblado.

### Tests
5) **NEW** `backend/tests/test_pipeline_debug_instrumentation.py` — **15 tests**.
6) **NEW** `backend/tests/test_api_health_check.py` — **14 tests**.

## Validación
- ✅ 15+14 = **29/29 tests focales** verdes.
- ✅ Suite backend completa: **2869 passed, 2 skipped, 0 failed** (subimos +87 vs 2782).
- ✅ Suite frontend: **125/125**.
- ✅ Backend re-arranca limpio.
- ✅ Endpoint validado en preview vía Python directo: 5 providers DISABLED (esperado: no hay keys en preview).

## Cómo usar en producción

### 1. Frontend — ver el funnel
Cuando "Generar picks del día" devuelve 0/0/0/0, el frontend recibe `pipeline_meta.pipeline_debug` en la respuesta del job (`/api/analysis/jobs/{job_id}`). `failure_stage` indica EXACTAMENTE en qué etapa cayó a cero:

```json
{
  "pipeline_meta": {
    "pipeline_debug": {
      "provider_response_count": 0,
      "raw_fixtures_count": 0,
      "after_sport_filter_count": 0,
      "after_date_window_count": 0,
      "after_priority_league_filter_count": 0,
      "after_status_filter_count": 0,
      "after_market_filter_count": 0,
      "analysis_candidates_count": 0,
      "failure_stage": "provider_response",
      "failure_message": "No se recibieron partidos desde el proveedor. Revisa provider, fecha, deporte o caché."
    }
  }
}
```

### 2. Endpoint diagnóstico
`GET /api/diagnostics/api-health?timeout_s=8` retorna snapshot de los 5 proveedores con `status`, `fixtures_returned`, `http_status`, `response_time_ms`, `error`. Útil desde panel admin / Postman.

### 3. Redeploy producción
- Toda esta instrumentación + el Hard Fixture Gate está **solo en PREVIEW**. Producción (`low-volatility-plays.emergent.host`) verá los cambios sólo después de **redesplegar**.
- En producción verificar variables: `API_FOOTBALL_KEY`, `THESTATSAPI_KEY`, `SCRAPEDO_TOKEN`, `PREMATCH_BUFFER_MINUTES` (opcional, default 10).

---

# F87 — Football Fixture Discovery Cascade (COMPLETED ✅)

## Spec del usuario
Tres sub-features + guardia de aislamiento MLB:

### F87.a — TheStatsAPI primary
TheStatsAPI como **fuente primaria** del fixture-discovery (espejo de F84.e/F84.a/F84.b para odds/h2h/team_stats).

### F87.b — Sofascore (playwright) + scrape.do
Sofascore via headless browser y vía scrape.do JSON como **tercer y cuarto fallback** de descubrimiento.

### F87.c — Unknown Competition Bucket
Bucket inclusivo `tier=unknown, priority=10` para ligas no registradas que NO estén en el blocklist (reserves, U13-U17, friendly clubs, regional ≥ div 3).

### Guardia de aislamiento MLB/Football
- `_discover_football_fixtures` no importa ni ejecuta NINGÚN módulo MLB.
- `seal_pick_payload` es **no-op** cuando `payload["sport"]` ≠ MLB/baseball.

## Implementación ejecutada

### Backend
1) **NEW** `backend/services/external_sources/thestatsapi_fixtures_adapter.py`
- `fetch_fixtures_next_48h(client, *, date_iso, timeout_s) → (fixtures, reason_codes)`.
- Reason codes: `THESTATSAPI_FIXTURES_DISABLED|TIMEOUT|EMPTY|SUCCESS|HTTP_ERROR|EXCEPTION`.
- `_normalise_fixture(raw) → dict` produce el shape API-Football exacto (con keys `id`, `fixture.{id,date,timestamp,status}`, `league.{id,name,country,_thestatsapi_id}`, `teams.{home,away}`, `_external_source=thestatsapi`, `_is_national_team`, `_is_international`).
- Detección heurística de internacionales por nombre (`WC|nations league|copa america|...`) + country normalizado.
- Mapping de estados TheStatsAPI → API-Football short codes.
- Fail-soft: nunca raise, `asyncio.wait_for` con timeout dedicado.

2) **NEW** `backend/services/external_sources/sofascore_fixtures_adapter.py`
- `fetch_fixtures_today(date_iso) → list[dict]` envuelve `playwright_scraper.sofascore_via_playwright`.
- `_normalise_sofascore_event(ev, source_tag) → dict | None` normalizador compartido (también usado por scrape.do).
- Soporta dos shapes: playwright (`{id: "sofa-X", league: "Y - Z", ...}`) y raw Sofascore JSON (`{startTimestamp, tournament, homeTeam, status: {type: ...}}`).
- Status mapping: `inprogress → 1H`, `finished → FT`, otros → `NS`.

3) **NEW** `backend/services/external_sources/scrapedo_fixtures_adapter.py`
- `fetch_fixtures_today(date_iso, timeout_s) → list[dict]` invoca `https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date}` vía `scrape_do_client.fetch_via_scrapedo_result(render=False)`.
- Reusa `_normalise_sofascore_event` con `source_tag="scrapedo"`.

4) **MOD** `backend/services/data_ingestion.py`
- Nuevos helpers F87 al top del módulo (antes de `ingest_upcoming`):
  - `_normalize_team_for_dedupe(name)` — lowercase + strip diacríticos + drop `FC|CF|SC|SD|AC|U\d+`.
  - `_fixture_dedupe_key(fx) → (home_norm, away_norm, date_only)`.
  - `_espn_to_apifootball_shape(ev) → dict`.
  - `_merge_fixture_buckets(buckets) → list[dict]` con orden de prioridad `thestatsapi > api_football > espn > sofascore_pw > scrapedo`.
  - `_discover_football_fixtures(client) → (fixtures, audit_dict)`.
- Cascada:
  1. TheStatsAPI primary (≥ `F87_MIN_VIABLE_COUNT=5` corta cascada).
  2. API-Football legacy (≥ 5 corta cascada si TheStatsAPI vacío).
  3. ESPN scoreboard.
  4. Sofascore PW.
  5. Sofascore scrape.do.
  Si nada superó el threshold → merge + dedupe todos los buckets.
- Audit dict expone `sources_called`, `counts_per_src`, `reason_codes`, `primary_winner`, `merged`, `total`, `isolated_from_mlb: true`.
- Log explícito al arrancar: `[F87_discovery] sport=football isolated_from_mlb=true`.
- Cuando `sport == "football"`, reemplaza el `await af.fixtures_next_48h(client)` por `await _discover_football_fixtures(client)`.

5) **MOD** `backend/services/football_competitions.py` — F87.c
- `UNKNOWN_TIER_NAME="unknown"`, `UNKNOWN_TIER_PRIORITY=10` (env override).
- `UNKNOWN_HYDRATE_CAP=3` (cap separado para evitar que partidos unknown coman budget de Tier-1/2/3).
- `_COMPETITION_BLOCKLIST_PATTERNS`: U13-U17, reserves, friendly clubs, youth, women.*reserve, amateur, regional league, division 3-9, tercera/cuarta/quinta división.
- `is_competition_blocklisted(name) → bool`.
- `get_unknown_competition_meta(name) → dict | None` — devuelve meta sintético sólo si NO está blocklisted Y el flag `ENABLE_UNKNOWN_COMPETITION_BUCKET=true`.
- `get_allowed_tiers()` extiende `ALLOWED_TIERS` con `"unknown"` cuando el flag está on.

6) **MOD** `backend/services/data_ingestion.py::ingest_upcoming` — integración F87.c
- Tras los pasos 1) tier allowlist + 2) national-teams, añade paso **3) Unknown bucket** ANTES del discard.
- Después de la hidratación, separa `kept` en `known_subset` + `unknown_subset`, capa el unknown a `UNKNOWN_HYDRATE_CAP=3`, y re-sortea por priority.
- Logs nuevos: cuenta `Unknown:` en el log de tiers + warning con nombres capeados.

7) **MOD** `backend/services/mlb_pipeline_payload_contract.py::seal_pick_payload` — guardia
- Si `payload["sport"]` está definido y NO es `mlb|baseball` (case-insensitive), retorna inmediatamente con `qcm_audit = {applied: False, reason: "PAYLOAD_NOT_MLB", sport: <sport>}`.
- Picks NO se mutan, no se agregan bloques MLB-specific.
- Compatibilidad: payload sin `sport` (legacy F91) sigue funcionando exactamente igual (asumido MLB).

### Tests
8) **NEW** `backend/tests/test_f87_fixture_discovery.py` — **34 tests**:
- F87.a: primary wins, empty falls through, disabled skips, adapter normalisation (basic + intl flags + unparseable).
- F87.b: playwright shape + raw JSON shape + in-progress mapping + flags off + scrape.do sin token.
- F87.c: unknown bucket pass, blocklist parametrizado (9 nombres), inclusive competitions parametrizado (5), flag off, get_allowed_tiers.
- Merge: dedupe across sources, normalisation respects FC suffix, priority order respected.
- Fail-soft: broken TheStatsAPI cascade fallback.

9) **NEW** `backend/tests/test_f87_fixture_discovery_isolation.py` — **15 tests**:
- `test_mlb_qcm_import_does_not_affect_football_fixture_discovery`: poisons MLB modules in `sys.modules`, runs discovery, asserts success + isolation flag.
- `test_football_ingest_does_not_call_seal_pick_payload`: spies seal_pick_payload con `AsyncMock(side_effect=AssertionError)`, ejecuta discovery, assert NOT awaited.
- `test_football_payload_skips_qcm`: assert picks intact + qcm_audit.reason=PAYLOAD_NOT_MLB.
- Parametrizados: 4 deportes non-MLB (`basketball`, `tennis`, `hockey`, `nfl`) → skip; 5 variantes MLB (`mlb`, `baseball`, `MLB`, `BaseBall`, `" mlb "`) → procesado normal.
- Missing sport preserves legacy MLB behavior.
- Garbage input no exception.
- Audit dict marca `isolated_from_mlb: true`.

## Variables de entorno nuevas (defaults sanos)
| Variable | Default | Efecto |
|---|---|---|
| `ENABLE_THESTATSAPI_FIXTURES_PRIMARY` | `true` | Activa TheStatsAPI como fuente #1 |
| `ENABLE_API_FOOTBALL_FALLBACK` | `true` | Activa API-Football como #2 |
| `ENABLE_SOFASCORE_PW_FALLBACK` | `true` | Activa Sofascore PW como #4 |
| `ENABLE_SCRAPEDO_FIXTURES_FALLBACK` | `true` | Activa scrape.do como #5 |
| `ENABLE_UNKNOWN_COMPETITION_BUCKET` | `true` | Activa bucket unknown |
| `UNKNOWN_COMPETITION_PRIORITY` | `10` | Priority del bucket unknown |
| `UNKNOWN_COMPETITION_HYDRATE_CAP` | `3` | Cap de unknown en hidratación |
| `F87_MIN_VIABLE_COUNT` | `5` | Umbral para short-circuit |

## Validación
- ✅ Tests focales F87: **49/49 PASS** (34 discovery + 15 isolation).
- ✅ Suite completa backend: **2918 passed, 2 skipped, 0 failed** (subimos de 2869 → 2918, +49 nuevos tests).
- ✅ Suite completa frontend: **125/125 PASS**.
- ✅ Lint Ruff limpio en los 5 archivos nuevos / modificados.
- ✅ Backend re-arranca limpio.
- ✅ **Cero regresiones MLB QCM**: el guardia de aislamiento preserva todo F91/F92.
- ✅ **Cero regresiones football**: el discovery sigue devolviendo el shape API-Football exacto.

## Beneficios esperados en producción
- **Cobertura ampliada**: TheStatsAPI cubre torneos mundiales que API-Football no expone (FIFA Club World Cup, CONMEBOL Libertadores, ligas africanas/asiáticas no top).
- **Resiliencia**: si una fuente falla, la cascada continúa. Solo se descartan partidos si las 5 fuentes vienen vacías.
- **No silent discards**: ligas desconocidas pasan al bucket con priority=10 en vez de desaparecer.
- **Aislamiento**: futuras tocadas a MLB no pueden romper football discovery (guardia probada con `_Exploding` MLB modules).

## Recordatorio producción
- Cambios sólo en preview. Para activar en producción → **redesplegar**.
- En producción asegúrate de tener `THESTATSAPI_KEY` y `SCRAPEDO_TOKEN` configurados (si faltan, los respectivos pasos del cascade reportan `DISABLED` vía `/api/diagnostics/api-health`).

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
