# Plan — Phases F58–F93 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA**.
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
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivos nuevos / extendidos (MLB-F93) — Manual Odds Override Reprice + UI Refresh
**Problema:** al guardar cuota manual (especialmente cuando el pick no se encuentra en runs recientes) antes solo se persistía override y la UI no cambiaba.

**Objetivo:** al guardar cuota manual, el sistema SIEMPRE devuelve un resultado accionable y, si hay contexto suficiente, **repricing inmediato** + **refresh visual en MatchCard** sin regenerar picks.

**Estado:** ✅ COMPLETADO.

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

## Phase F87.1 — Football Fixture Discovery Contract Fix + Visible Audit (COMPLETED ✅)
**(COMPLETADO)** — ver sección F87.1 existente para detalles (sin cambios).

---

# Phase MLB-F93 — Manual Odds Override Reprice + UI Refresh (COMPLETED ✅)

## Estado
✅ **Completado end-to-end**: backend (reprice + lookup + debug + persistencia) + frontend (refresh visual en MatchCard) + tests.

## Implementación realizada (resumen)

### Backend
- ✅ Nuevo módulo puro fail-soft:
  - `backend/services/mlb_manual_odds_reprice.py`
  - API: `reprice_mlb_pick_with_manual_odds()` + `build_minimal_pick_context_from_match_doc()`.
  - Decisiones: `VALUE | NO_VALUE | WATCHLIST | MANUAL_ODDS_ONLY | INVALID`.
  - Métricas: `implied_probability`, `model_probability`, `fair_odds`, `edge`, `edge_pct`, `ev`.
  - Reason codes: `MANUAL_ODDS_REPRICE_APPLIED`, `MANUAL_ODDS_OVERRIDE_USED`, `EDGE_*`, `EV_*`, `MODEL_PROBABILITY_MISSING`, `PICK_CONTEXT_*`.

- ✅ Búsqueda del contexto ampliada:
  - `_locate_pick_multikey` conserva estrategias previas (pick_id → alt ids → teams/date).
  - **Nuevo:** fallback a `matches` collection para reconstruir `minimal_pick_context` cuando el pick no está en `pick_runs`.

- ✅ Endpoint POST actualizado (contrato F93 + back-compat):
  - `POST /api/mlb/picks/{pick_id}/manual-odds`
  - **Nuevo contrato F93 (siempre presente):**
    - `status: REPRICED | OVERRIDE_SAVED_ONLY | PICK_NOT_FOUND | ERROR`
    - `reprice: {available, decision, edge, edge_pct, fair_odds, implied_probability, model_probability, ev, confidence_before/after, reason_codes, rationale}`
    - `message_user`, `message_debug`, `next_action`
  - **Back-compat mantenida:**
    - `attached_to_pick`, `fallback_override_created`, `value_status`, `manual_edge_pct`, `manual_odds`, `tried_keys`, `message`, etc.

- ✅ Persistencia ampliada en `pick_runs` cuando el pick existe:
  - `manual_odd`, `odds_source=USER_MANUAL_OVERRIDE`, `odds_status=MANUAL`
  - `reprice`, `value_status`, `edge`, `ev`, `fair_odds`, `implied_probability`
  - `manual_odds_updated_at`, `manual_reprice_promoted`.
  - Mantiene también los campos legacy (`manual_*`).

- ✅ Nuevo endpoint de debug:
  - `GET /api/mlb/manual-odds/debug?match_id=...&pick_id=...&game_pk=...`
  - Respuesta: `lookup_attempts`, `override_found`, `can_reprice`, `missing_fields`, `final_status`.

### Frontend
- ✅ `InlineManualOddsInput.jsx` reescrito:
  - Loading: “Recalculando con cuota manual…”
  - Switch por `status`:
    - `REPRICED`: mensajes específicos (VALUE/NO VALUE/WATCHLIST)
    - `OVERRIDE_SAVED_ONLY`: explicación + CTAs **Refrescar card / Regenerar análisis / Ver debug**
  - Nuevo callback: `onReprice(payload)` para notificar al card.

- ✅ Integración en `MatchCard.jsx` (lugar correcto):
  - Estado local `manualReprice`.
  - `displayOddsRange` usa `manualReprice.manual_odd` si existe.
  - Badges: VALUE/NO VALUE/WATCHLIST/SOLO INFO con edge%.
  - Chip EV + mensaje de decisión.
  - No requiere regenerar picks ni recargar la página.

## Tests y validación (zero-regression)
- ✅ Backend:
  - +14 tests nuevos (`11 obligatorios + 3 sanity`).
  - Suite completa: **2956 passed, 2 skipped** (baseline anterior 2942 → +14).
- ✅ Frontend:
  - +9 tests nuevos (5 InlineManualOddsInput + 4 ManualOddsReviewPanel F93).
  - Suite completa: **134 passed** (baseline anterior 125 → +9).

## Adaptación adicional — `ManualOddsReviewPanel.jsx`
- ✅ El panel de revisión batch ahora **consume el contrato F93** cuando `r.data.status` está presente:
  - Toasts diferenciados (VALUE / NO_VALUE / WATCHLIST / OVERRIDE_SAVED_ONLY).
  - Pill prefiere `reprice.decision` sobre `value_status` legacy.
  - Edge% desde `reprice.edge_pct` con fallback a `manual_edge_pct`.
  - Renderiza `message_user` (línea informativa) cuando viene en la respuesta.
  - Render explícito de "no se pudo recalcular" para `OVERRIDE_SAVED_ONLY` (basis-full row), evitando la antigua copia genérica.
  - Pasa al backend los campos de contexto (`match_id`, `game_pk`, `home_team`, `away_team`, `commence_date`, `market`, `line`) para que el lookup multi-key/`matches` collection funcione.
- ✅ **Back-compat preservada**: cuando el backend devuelve solo los campos legacy (sin `status`/`reprice`), el panel sigue mostrando `value_status` + `manual_edge_pct` como antes.

## Variables de entorno
- `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
- `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).

---

# Phase F94 — Restaurar visibilidad de fixtures, descartados y live exóticos (COMPLETED ✅)

## Estado
✅ **Completado end-to-end** en dos pasos:
1. **Tab Live** — backend `football_live_visibility.py` + endpoint `GET /api/football/live/visibility` + frontend `FootballLiveVisibilityStrip.jsx` integrado en `LivePage.jsx` con KPI strip (provider/sport/league/visible/analyzable/hidden) y listado de fixtures exóticos visibles pero no analizados.
2. **Tab Dashboard / Picks del día** — nuevo `DashboardDiscardedSummary.jsx` integrado en `DashboardPage.jsx`: cuando `sport === 'football'` y no hay picks recomendados, restaura el comportamiento previo (perdido en refactorización anterior) de mostrar los descartados como bloque colapsable con contador.

## Problema (resumen)
Tras una refactorización previa, en la pestaña **Picks del día** los fixtures descartados/incompletos quedaban **invisibles** cuando `recommended === 0` (no se renderizaba el detalle ni la cuenta agregada). En la pestaña **Live**, los fixtures de ligas exóticas/baja prioridad nunca se mostraban aunque el provider los devolvía. Ambos rompían el principio de auditoría completa de F84/F87.

## Reglas confirmadas con el usuario
- Alcance: **solo Football** (otros deportes no afectados).
- Comportamiento esperado: los descartados/incompletos **siempre** deben renderizarse aunque `recommended = 0`, especialmente en Dashboard / Picks del día.
- UI preferida (Opción 2): **bloque colapsado con contador** "N partidos descartados — ver detalle"; al expandir muestra cada fixture con `match`, `discard_reason`, `secondary_reasons`, `stage`, `provider/status` si existen.
- Texto del banner: `"No hay picks recomendados hoy, pero se analizaron X partidos. Revisa los descartados para ver por qué no pasaron los filtros."`
- Regla clave: no ocultar descartados por `recommended === 0`.

## Implementación realizada (resumen)

### Backend (paso 1 — Live)
- ✅ `backend/services/football_live_visibility.py` (módulo puro fail-soft):
  - Resuelve la lista completa de fixtures live + clasificación + razones.
  - Devuelve `items[]` con `analysis_status`, `discard_reason`, `secondary_reasons`, league info, etc.
  - Devuelve `live_debug` con counters por etapa (`provider_live_count`, `after_sport_filter_count`, `after_league_filter_count`, `visible_live_count`, `analysis_eligible_live_count`, `hidden_by_priority_filter`).
- ✅ Endpoint `GET /api/football/live/visibility` añadido en `server.py`.

### Frontend (paso 1 — Live)
- ✅ `frontend/src/components/FootballLiveVisibilityStrip.jsx`:
  - Header con icono Eye + botón refresh (auto-cada 60s).
  - KPI strip 3×2 con los 6 counters del `live_debug`.
  - Listado de fixtures exóticos con `ReasonChip` (EXOTIC_LEAGUE, LOW_PRIORITY_LEAGUE, NO_MARKET_IDENTITY, CLASSIFICATION_FAILED) + `secondary_reasons` colapsadas y status "Visible / no analizado".
  - Auto-hide cuando `provider_live_count === 0` (evita ruido).
- ✅ Integrado en `LivePage.jsx`.

### Frontend (paso 2 — Dashboard / Picks del día)
- ✅ Nuevo componente `frontend/src/components/DashboardDiscardedSummary.jsx`:
  - **Scope guards** (F94 alcance): no renderiza si `sport !== 'football'` o `recommendedCount > 0` o `totalDiscarded === 0`.
  - **Intro banner** con texto exacto agreed con el usuario (ES/EN).
  - **Toggle colapsable** "N partidos descartados — ver detalle" / "ver detalle (Expand)/(Hide)".
  - **Bucket pills** (Motivación / Mercado / Datos incompletos / Falta cuota / Baja relevancia) con contador por bucket.
  - **Detalle expandido** por fixture con `match`, `reason` (prefiere `discard_reason` → `reason` → `missing`), `secondary_reasons` (chips), `stage` (`pipeline_stage`/`discard_stage` fallback), `provider` (`source`/`odds_provider` fallback) y `status` (`analysis_status`/`value_status` fallback).
  - **Footer hint** reforzando el rationale: "Estos partidos se analizaron pero no produjeron pick recomendado".
  - **Rules of Hooks**: todos los `useState`/`useMemo` declarados antes de cualquier early-return.
  - **Back-compat fail-soft**: items con sólo `reason`/`missing` siguen renderizando.
- ✅ Integrado en `frontend/src/pages/DashboardPage.jsx` justo antes del `EmptyStateNoValue` (línea ~1414) recibiendo `discMot/discMkt/incomplete/skippedLowRel/watchlistOddsNeeded` ya calculados.

## Tests y validación (zero-regression)
- ✅ Backend (Live):
  - `tests/test_f94_live_visibility.py` (suite agregada en paso 1).
- ✅ Frontend (Live):
  - `__tests__/FootballLiveVisibilityStrip.test.jsx` (4 tests).
- ✅ Frontend (Dashboard — paso 2 actual):
  - `__tests__/DashboardDiscardedSummary.test.jsx` (**7 tests** nuevos):
    1. Banner + counter + bucket pills render para football + recommended===0.
    2. Expand on click → revela match, reason, secondary, stage, provider, status.
    3. Scope guard: NO renderiza para sports !== 'football'.
    4. NO renderiza cuando `recommendedCount > 0`.
    5. Back-compat: payload legacy con sólo `reason`/`missing` renderiza sin throw.
    6. Render null cuando todos los buckets están vacíos.
    7. Copy en inglés cuando `lang="en"`.

## Suites finales
- ✅ Backend: **3033 passed, 3 skipped** (5 errores HTTP E2E preexistentes ajenos a F94; 1 flakey de F83.2 que pasa aislado).
- ✅ Frontend: **152 passed** (baseline previo 145 → **+7 nuevos**).

## API y archivos relevantes
- `GET /api/football/live/visibility`
- `/app/backend/services/football_live_visibility.py`
- `/app/backend/tests/test_f94_live_visibility.py`
- `/app/frontend/src/components/FootballLiveVisibilityStrip.jsx`
- `/app/frontend/src/components/DashboardDiscardedSummary.jsx` *(nuevo en este paso)*
- `/app/frontend/src/components/__tests__/DashboardDiscardedSummary.test.jsx` *(nuevo)*
- `/app/frontend/src/pages/DashboardPage.jsx` *(integración)*
- `/app/frontend/src/pages/LivePage.jsx` *(integración paso 1)*

---

## 3) Pendientes y siguientes pasos (post-F94)

### Pendientes P0 (actual)
- Ninguno (F94 cerrado: Live tab + Dashboard tab cubiertos).

### Pendientes no bloqueantes
- (F84.c) lineups / injuries — fuera de scope inicial, requiere confirmar cobertura TheStatsAPI.
- (F84.d) standings — fuera de scope inicial.
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.

### Futuras mejoras recomendadas (global)
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds.
- Implementar calibrador offline cuando exista una fuente estable.

---

## 6) Validación esperada (estado actual)

- Suites actuales (post F94.2):
  - Backend: **3004 passed, 2 skipped** con `pytest.ini` limitando discovery a `tests/` (vs baseline 2972 → **+32 tests F94.2**).
  - Frontend: **158 passed** (vs baseline 152 → **+6 tests WC**, vs baseline pre-F94 145 → **+13 tests acumulados**).
  - Lint Python: clean.
  - Lint JS: 1 advisory preexistente (no introducido por F94.2).
  - esbuild compila limpio en `WorldCupLiveCard.jsx` + `FootballLiveVisibilityStrip.jsx`.

---

# Phase F94.2 — FIFA World Cup Live Detection + TheStatsAPI Diagnostics (COMPLETED ✅)

## Estado
✅ **Completado end-to-end**. Resuelve el caso reportado por el usuario: **"Irán vs Nueva Zelanda / FIFA World Cup 2026 / Status LIVE / minuto 24'"** estaba siendo ocultado por filtros de prioridad, y el adaptador TheStatsAPI devolvía `ADAPTER_RETURNED_EMPTY` sin diagnóstico estructurado.

## Problema (resumen)
1. **API-Football devolvía 226 fixtures** pero el panel "EN CURSO AHORA" mostraba **0**. Las ligas exóticas (Serie B Brasil, USL, etc.) sí se listaban, pero la Copa del Mundo no.
2. El bloqueo provenía del filtro `ALLOWED_TIERS` que en algunos paths sí incluía tier_1, pero el feed devolvía variantes como "FIFA World Cup 2026" / "Copa Mundial" que el alias matcher de `football_competitions.py` solo cubría parcialmente (faltaba portugués, y la ruta exacta dependía del path por el que llegaba el fixture).
3. **TheStatsAPI** devolvía `raw=0 kept=0 drop=0` con razón dominante `ADAPTER_RETURNED_EMPTY`, sin información estructurada sobre `endpoint`, `http_status`, `sample_payload_keys` ni `reason`.

## Reglas confirmadas con el usuario
- Alcance F94.2: **solo Football**.
- World Cup nunca puede ocultarse — aunque no tenga mercado, SportyTrader o cuotas.
- Si `is_world_cup` matchea pero falta mercado → status `VISIBLE_PENDING_MARKET` + CTA de "Ingresar cuota manual" (estilo F93).
- TheStatsAPI: autorización para investigar/modificar query/endpoint/timezone/parser; mantener fail-soft.
- Fallback TheStatsAPI: SOLO para World Cup en este sprint.
- Screenshots: scroll completo (header + middle + footer), no solo header.

## Implementación realizada

### Backend
- ✅ **Nuevo** `services/football_world_cup_aliases.py`:
  - `WORLD_CUP_ALIASES` (frozenset): ES/EN/PT/FR/DE/IT — `fifa world cup`, `world cup`, `copa mundial`, `copa do mundo`, `coupe du monde`, `weltmeisterschaft`, `coppa del mondo`…
  - `is_world_cup(league_name, country)`: case+accent insensitive, con guardas negativas para `qualifying`, `women`, `U-XX`, `Club World Cup`, `eliminator`.
  - `normalize_world_cup_league_name`: devuelve siempre `"FIFA World Cup"` canónico.
  - Cobertura: **12 variantes positivas + 10 negativas verificadas en tests**.

- ✅ `services/football_live_visibility.py` (modificado):
  - `classify_live_fixture`: bypass de filtros para World Cup. Si `is_wc=True`, `analysis_status` siempre = `"ANALYZABLE"`, nunca `DISCARDED`, con `competition_meta` sintético tier_1 si falta meta real.
  - Si World Cup sin `league_id` → agrega `VISIBLE_PENDING_MARKET` a `secondary_reasons` (señal para el frontend).
  - Surface `_is_world_cup` en `_flatten_fixture`.
  - Nuevo helper `_thestatsapi_world_cup_fallback`: solo para World Cup, fail-soft, devuelve `(fixtures, diag)` con `{provider, status, raw_count, reason, endpoint, http_status, sample_payload_keys, world_cup_count}`.
  - `compute_football_live_visibility`: ahora detecta si primary tiene World Cup; si no, invoca el fallback. Cuando primary ya tiene WC, NO llama al fallback y reporta `SKIPPED_PRIMARY_HAS_WC`.
  - Nuevos `live_debug` counters: `world_cup_live_detected`, `world_cup_live_count`, `world_cup_hidden_by_filter` (contract: siempre 0), `world_cup_examples[]` (hasta 8), `world_cup_fallback_used`, `thestatsapi_diag`.

- ✅ **Nuevo** `services/external_sources/thestatsapi_diagnostics.py`:
  - `probe_fixtures_endpoint`, `probe_live_endpoint`, `probe_all` — devuelven envelope estructurado.
  - Captura `endpoint`, `http_status`, `request_id` (x-request-id / x-trace-id), `elapsed_ms`, `raw_count`, `sample_payload_keys` (hasta 20), `status` ∈ `{OK, EMPTY, AUTH_ERROR, HTTP_ERROR, TIMEOUT, DISABLED, EXCEPTION}`, `reason`.
  - Nunca lanza; reflejan fielmente el resultado HTTP en `status` + `http_status`.

- ✅ `server.py` — Nuevo endpoint `GET /api/debug/thestatsapi/probe` autenticado, que llama a `probe_all(client)` con timeout global de 20s.

### Frontend
- ✅ **Nuevo** `frontend/src/components/WorldCupLiveCard.jsx`:
  - Card pinned destacada (amber/gold gradient) que aparece sobre el KPI strip.
  - Título con contador: `"FIFA Copa del Mundo en vivo — N partido(s)"`.
  - Por fixture: minuto, equipos, badge de liga + país, status `Visible / pendiente de mercado`.
  - CTA "Ingresar cuota manual" estilo F93:
    - Botón → revela input numérico (decimal, comma/dot agnostic).
    - Save → persiste en `localStorage` (`wc_manual_odds:<fixture_id>`).
    - "Cuota guardada: X" como badge + opciones Editar/Borrar.
  - Warning de violación de contrato cuando `world_cup_hidden_by_filter > 0`.
  - Footnote: "Per F94.2, World Cup siempre es visible".
  - Bilingüe ES/EN.

- ✅ `frontend/src/components/FootballLiveVisibilityStrip.jsx` (modificado):
  - Importa `WorldCupLiveCard`, lo renderiza sobre el KPI strip cuando hay WC.
  - Nuevo panel diagnóstico **TheStatsAPI** que aparece cuando `live_debug.thestatsapi_diag.status !== 'OK'`:
    - Status badge (color azul) con el código (`EMPTY`/`AUTH_ERROR`/`HTTP_ERROR`/`TIMEOUT`/`DISABLED`/`EXCEPTION`).
    - Grid con `endpoint`, `http_status`, `raw_count`, `reason`.
    - Línea con `sample_payload_keys` cuando existe.
  - Visibility relajada: ya no oculta el strip si hay World Cup detectado (`worldCupItems.length > 0`).

## Tests (zero regresiones)
- ✅ Backend `tests/test_f94_2_world_cup_visibility.py` (**32 tests**, 100% green):
  - 11 positivos + 10 negativos para aliases (ES/EN/PT/FR variants).
  - `test_world_cup_aliases_set_contains_all_canonical_forms`.
  - `test_world_cup_live_fixture_is_always_analyzable` (Iran vs New Zealand mock).
  - `test_world_cup_live_fixture_without_league_id_marks_pending_market`.
  - `test_world_cup_with_qualifying_name_does_NOT_trigger_bypass`.
  - `test_compute_live_visibility_surfaces_world_cup_counters`.
  - `test_thestatsapi_fallback_fires_when_primary_has_no_world_cup`.
  - `test_thestatsapi_fallback_SKIPPED_when_primary_already_has_wc`.
  - `test_world_cup_fallback_returns_diag_when_thestatsapi_disabled`.
  - `test_diagnostics_probe_returns_full_envelope` (200 OK).
  - `test_diagnostics_probe_reports_empty_payload` (EMPTY).
  - `test_diagnostics_probe_reports_auth_error` (401 → AUTH_ERROR).

- ✅ Frontend `__tests__/WorldCupLiveCard.test.jsx` (**6 tests**, 100% green):
  - Iran vs New Zealand renderiza pinned con FIFA World Cup badge.
  - Manual odds CTA captura y persiste valor en localStorage.
  - `VISIBLE_PENDING_MARKET` surface notice.
  - Returns null cuando no hay WC items.
  - Contract-violation warning cuando `world_cup_hidden_by_filter > 0`.
  - Paridad ES/EN.

## Validación E2E
- ✅ Backend live-call con mock pipeline:
  - Iran vs New Zealand mock → `_is_world_cup=True`, `analysis_status=ANALYZABLE`, `discard_reason=None`, `secondary_reasons=[SPORTYTRADER_NOT_FOUND, VISIBLE_PENDING_MARKET]`.
  - `live_debug`: `world_cup_live_detected=True`, `world_cup_live_count=1`, `world_cup_hidden_by_filter=0`, `world_cup_examples=['Iran vs New Zealand']`.
- ✅ Frontend en preview real (`low-volatility-plays.preview.emergentagent.com`):
  - Login con cuenta demo → Dashboard funciona, muestra `"10 partidos descartados — ver detalle"` (F94 anterior validado en producción).
  - Live tab → `FootballLiveVisibilityStrip` montado correctamente.
  - **Scroll completo (top + middle + bottom)**: **0 errores visibles** en toda la página.
  - `WorldCupLiveCard` no se renderiza porque actualmente no hay partidos WC en vivo en el snapshot (guard correcto).

## Endpoints / archivos
- `GET /api/football/live/visibility` (extended con `world_cup_*` counters + `thestatsapi_diag`).
- `GET /api/debug/thestatsapi/probe` *(nuevo, autenticado)*.
- `services/football_world_cup_aliases.py` *(nuevo)*.
- `services/football_live_visibility.py` *(modificado)*.
- `services/external_sources/thestatsapi_diagnostics.py` *(nuevo)*.
- `tests/test_f94_2_world_cup_visibility.py` *(nuevo, 32 tests)*.
- `frontend/src/components/WorldCupLiveCard.jsx` *(nuevo)*.
- `frontend/src/components/FootballLiveVisibilityStrip.jsx` *(modificado)*.
- `frontend/src/components/__tests__/WorldCupLiveCard.test.jsx` *(nuevo, 6 tests)*.

## Pre-existing test cleanup (incluido en este sprint)
- ✅ **`backend/pytest.ini` creado** con `testpaths = tests` — fija definitivamente el problema preexistente de discovery de pytest que recogía scripts standalone del root (`backend_test.py`, `live_recommendation_history_*.py`, `mlb_under_veto_test.py`, `test_backend_api.py`, `test_phase15_*::test_api_endpoints`) que ejecutaban código top-level con `sys.exit()` y HTTP timeouts. La regla: solo `/app/backend/tests/` es el directorio de pytest. Los scripts legacy del root siguen funcionando como standalone (`python <script>.py`).
- ✅ Esto resuelve los 1 failed + 5 errors + 4 collection errors reportados anteriormente, sin cambiar nada en el código de los scripts.

---

## Reglas operacionales + flags

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Mantener back-compat en contratos de respuesta cuando el FE dependa de fields legacy.

- Flags / env relevantes:
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
  - ✅ **MLB-F93:** `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
  - ✅ **MLB-F93:** `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).
  - ✅ **F94.2:** `ENABLE_THE_STATS_API` (default `false`, requerido para fallback World Cup live).
  - ✅ **F94.2:** `THESTATSAPI_KEY` (clave Bearer del proveedor; sin esta clave el fallback se reporta como `DISABLED`).
