# Plan — Phases F58–F94.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F70 completadas.
> **Estado actual (resumen):** ✅ F58–F70 + F74 (+post v2/v2.5) + F82/F82.1/F82.1-adjust + F83/F83.1/F83.2 + P2 + F82.2 + P4.1 + F84.a/b/e + F85 (+Phase 2) + F86/F87/F88 (Sprint F86.2) + F89 (Sprint F86.1) + F90 (Sprint F83-update) + F91 (MLB QCM Engine puro) + F92 (MLB QCM Applier + Wiring) + F93 (Corners cascade) + Bugfix Upcoming Filter + Fixture Hard Gate + Pipeline Debug Instrumentation + ✅ **F87 (Football fixture discovery cascade) COMPLETADA** + ✅ **F87.1 (Fixture Discovery Contract Fix + Visible Audit + Parte 1.5 upstream audit) COMPLETADA** + ✅ **MLB-F93 (Manual Odds Override Reprice + UI Refresh) COMPLETADA** + ✅ **MLB-F93.1 (Manual Odds Reprice Context Pass-through + Authenticated Debug) COMPLETADA** + ✅ **F94 (Restaurar visibilidad de fixtures, descartados y live exóticos — Live + Dashboard) COMPLETADA** + ✅ **F94.2 (FIFA World Cup Live detection + TheStatsAPI diagnostics) COMPLETADA** + ✅ **F94.3 (Live Enrichment Persistence Audit) COMPLETADA** + 🟡 **REFACTOR-1 (data_ingestion top-2) EN PROGRESO (paso 1/3 completado)** + ✅ **FIX-1 (xG TheStatsAPI normalisation) COMPLETADO** + ✅ **FIX-2 (Corners TheStatsAPI normalisation) COMPLETADO** + ✅ **FIX-3 (Tail Fragility polarity guard) COMPLETADO** + ⏳ **F84.c/F84.d (Lineups + Standings) PENDIENTE (P1)**.

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
**(COMPLETADO)** — sin cambios (ver sección existente).

---

# Phase F94 — Restaurar visibilidad de fixtures, descartados y live exóticos (COMPLETED ✅)
**(COMPLETADO)** — sin cambios (ver sección existente).

---

# Phase F94.2 — FIFA World Cup Live Detection + TheStatsAPI Diagnostics (COMPLETED ✅)
**(COMPLETADO)** — sin cambios (ver sección existente).

---

# Phase F94.3 — Live Enrichment Persistence Audit (LIVE_ENRICHMENT_DROPPED_FIXTURES) (COMPLETED ✅)

## Objetivo
Prevenir regresiones silenciosas tipo **"Live fixture discovery != live fixture persistence"** mediante una regla explícita:

> Si `discovery_count > 0` pero `persisted_live_count == 0` → emitir error técnico `LIVE_ENRICHMENT_DROPPED_FIXTURES`.

## Implementación realizada
### Backend
- ✅ Nuevo módulo `services/live_enrichment_audit.py` con función pura `evaluate_enrichment_drop`.
- ✅ `services/football_live_visibility.py` ahora incluye en `live_debug`:
  - `persisted_live_count`
  - `enrichment_dropped_all_fixtures`
  - `enrichment_error_code`
  - `enrichment_error_message`
- ✅ Fail-soft total: si el conteo Mongo falla, el endpoint no rompe y superficie mensaje técnico.

### Frontend
- ✅ `FootballLiveVisibilityStrip.jsx` renderiza banner rojo de severidad alta con:
  - code `LIVE_ENRICHMENT_DROPPED_FIXTURES`
  - discovery vs persisted
  - hint actionable

## Tests
- ✅ Backend: `tests/test_f94_3_live_enrichment_audit.py` (14 tests).
- ✅ Frontend: extensión de `FootballLiveVisibilityStrip.test.jsx` (banner + contract).

---

# Phase REFACTOR-1 — Refactor quirúrgico `data_ingestion.py` (solo top-2 componentes) — EN PROGRESO 🟡

## Objetivo
Reducir complejidad y riesgo de regresiones en el pipeline de ingesta sin cambiar comportamiento.

## Componentes objetivo (por tamaño aproximado)
1. `_enrich_football` (≈ 458 LOC)
2. `ingest_upcoming` (≈ 274 LOC)

## Reglas estrictas
- **Solo refactorizar estos 2 componentes** (instrucción explícita del usuario).
- Mantener firmas públicas **exactas**: `ingest_upcoming`, `_enrich_football`.
- Cero cambios en endpoints / contratos JSON.
- Extraer helpers cohesivos y puros, sin alterar side-effects.
- Ejecutar `pytest` tras cada extracción (y `yarn craco test` si hay cambios FE).

## Progreso actual
- ✅ Paso 1/3 completado: extracción de **odds cascade** a helper:
  - `services/_ingestion_helpers/football_odds_cascade.py`
  - Integrado en `data_ingestion._enrich_football`.
  - Validado con tests (subset + suite completa).

## Pendiente (pasos restantes)
- ⏳ Paso 2/3: extraer **deep enrichment** (team stats + h2h + injuries + recent fixtures) sin cambios de comportamiento.
- ⏳ Paso 3/3: extraer **live stats hydration** (API-Sports fixture_statistics + merge TheStatsAPI match_stats) sin cambios de comportamiento.
- ⏳ Refactor `ingest_upcoming` (2º componente más grande) con misma política.

---

# Phase FIX-3 — Tail Fragility polarity guard (COMPLETED ✅)

## Problema
Contradicción interna en MLB: la UI mostraba simultáneamente:
- “Riesgo de cola explosiva: Alta”
- “Tail Fragility: Bajo 15/100”

**Causa raíz:**
- `mlb_expected_runs_distribution._tail_bucket_from` clasifica con thresholds directos (ej. `p_ge_12 > 0.22` → HIGH).
- `mlb_tail_fragility._explosive_tail_score` usa blend ponderado (W_P12/W_P14/W_P16/W_P18) → con 31%/14%/5%/2% devolvía score=15 → LOW.

## Implementación
- ✅ `services/mlb_tail_fragility.py`:
  - Nuevo reason code: `TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL`.
  - Thresholds: `p_ge_12 >= 0.25 OR p_ge_14 >= 0.10 OR external_bucket in (HIGH, EXTREME)`.
  - Polarity guard: si se cumplen thresholds y el bucket interno es LOW → elevar a MEDIUM y `score=max(score, 40)`.
  - Narrative ES enriquecida explicando la escalación.
- ✅ `frontend/src/components/TailRiskPanel.jsx`:
  - Banner ámbar visible solo cuando aparece el reason code.
  - Mensaje: “Tail Fragility escalado porque la distribución asigna alta probabilidad a escenarios de 12+ / 14+ carreras.”

## Tests
- ✅ Backend: `tests/test_fix3_tail_fragility_polarity_guard.py` (10 tests).
- ✅ Frontend: `src/components/__tests__/TailRiskPanel.escalation.test.jsx` (4 tests).

---

# Phase FIX-1 — xG TheStatsAPI normalisation (COMPLETED ✅)

## Problema
El xG TheStatsAPI nunca se normalizaba. Causa raíz dual:
1. `thestatsapi_client.fetch_recent_match_ids` **no existía** → `_ensure_thestatsapi_recent_match_ids` fallaba con `AttributeError` silencioso.
2. `fetch_shotmap_xg` no leía `home_team_id`/`away_team_id` desde `payload.event` (shape real), rompiendo el sumador por equipo.

## Implementación
- ✅ `services/external_sources/thestatsapi_client.py`:
  - Nueva función `fetch_recent_match_ids(team_id, *, n=15, status='finished', sport='football')`.
  - Endpoint: `GET /football/matches?team_id={tm_X}&status=finished&limit={n}`.
  - Fail-soft (devuelve `[]` en error/disabled).
- ✅ `services/external_sources/thestatsapi_shotmap_client.py`:
  - `fetch_shotmap_xg` ahora lee team_ids desde `payload.event.home_team_id` / `payload.event.away_team_id` como fallback adicional.

## Validación E2E live
- ✅ Iran (tm_65309) recent IDs reales: `['mt_986264843', ...]`.
- ✅ Iran vs NZ shotmap real: `home_np_xg=1.49, away_np_xg=1.239`.
- ✅ `compute_xg_recent_averages`: `available=True` (L1/L5 coherentes).

## Tests
- ✅ Backend: `tests/test_fix1_thestatsapi_xg_normalization.py` (11 tests).

---

# Phase FIX-2 — Corners TheStatsAPI normalisation (COMPLETED ✅)

## Problema
Los córneres no se importaban en ningún partido cuando venían de TheStatsAPI.

**Causa raíz:** TheStatsAPI devuelve stats con shape pivot-by-stat anidado:

```json
{"data": {"overview": {"corner_kicks": {"all": {"home": 4, "away": 1}}}}}
```

pero `normalize_match_stats` solo soportaba shape plano (`home/away`).

## Implementación
- ✅ `services/external_sources/thestatsapi_normalizer.py`:
  - Nuevo helper `_split_overview_to_team_blobs(overview)`.
  - `normalize_match_stats` detecta el shape `overview` (con o sin wrapper `data`) primero.
  - Preserva back-compat con flat/team-keyed.
- ✅ `services/football_corners_provider.py`:
  - `_extract_thestatsapi_corners` ahora puede leer corners desde `live_stats.home_stats['Corner Kicks']` cuando `_source=='thestatsapi'` o `'thestatsapi' in _sources`.

## Validación E2E live
- ✅ `mt_986264843` Iran vs NZ → normalizer extrae:
  - corners: 4–1
  - xG: 1.49–1.24
  - possession: 48%–52%
  - shots on goal: 4

## Tests
- ✅ Backend: `tests/test_fix2_thestatsapi_corners_normalizer.py` (8 tests).

---

# Phase F84.c / F84.d — Lineups + Standings (P1) — PENDIENTE ⏳

## Objetivo
Añadir cobertura de:
- **F84.c Lineups**: XI inicial + banca (+ injuries breve si la fuente lo permite).
- **F84.d Standings**: tabla de posiciones filtrable por liga.

## Principios
- Fail-soft: nunca bloquear el análisis/picks; devolver `AVAILABLE | PENDING | UNAVAILABLE` con razones.
- Back-compat: no romper consumers existentes; estos endpoints son aditivos.

## Backend
### Nuevos adaptadores (TheStatsAPI)
- `services/external_sources/thestatsapi_lineups.py` (nuevo)
- `services/external_sources/thestatsapi_standings.py` (nuevo)

### Endpoints
- `GET /api/football/match/{match_id}/lineups`
- `GET /api/football/league/{league_id}/standings`

### Contratos (borrador)
- Lineups:
  - `status: AVAILABLE|PENDING|UNAVAILABLE`
  - `reason_code` + `reason_detail`
  - `home`, `away`:
    - `starting_xi[]`, `bench[]`, `coach` (si existe), `injuries[]` (si existe)
  - `source: thestatsapi|api_sports|none`
- Standings:
  - `status: AVAILABLE|PENDING|UNAVAILABLE`
  - `league_id`, `league_name`, `season`
  - `table[]`: `{rank, team, played, won, draw, lost, gf, ga, gd, points, form?}`
  - `source` + `provenance`

## Frontend
- `LineupsPanel.jsx`:
  - Render XI + banca
  - Indicadores de missing data (PENDING/UNAVAILABLE)
- `StandingsPanel.jsx`:
  - Tabla compacta, resaltado de equipos del partido
- Integración:
  - `MatchDetailPage.jsx` (preferido) y/o sección expandible desde la card.

## Tests
- Backend:
  - unit tests de normalización del payload por adaptador
  - endpoint tests con mocking de respuestas HTTP
- Frontend:
  - render states (AVAILABLE/PENDING/UNAVAILABLE)
  - snapshot/queries para filas de tabla y secciones de XI

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- Ninguno (F94.3 + FIX-1/2/3 cerrados, suites verdes).

### Pendientes P1
- 🟡 **REFACTOR-1**: completar pasos 2/3 y 3/3 + refactor `ingest_upcoming`.
- ⏳ **F84.c/F84.d**: Lineups + Standings (TheStatsAPI) + UI + tests.

### Pendientes P2
- ⏳ Expandir `team_name_translations.py` para clubes UCL/UEL.
- ⏳ (Futuro sprint) extender fallback TheStatsAPI a competiciones no-Mundial.

### Futuras mejoras recomendadas (global)
- Backtest de la calibración F86.1 con ≥ 30 picks reales con H2H aplicado para ajustar thresholds.
- Implementar calibrador offline cuando exista una fuente estable.

---

## 6) Validación esperada (estado actual)

- Suites actuales (post F94.3 + FIX-1/2/3):
  - Backend: **3050 passed, 2 skipped**.
    - Incremento vs baseline 3007: **+43 tests acumulados**
      - F94.3: +14
      - FIX-3: +10
      - FIX-1: +11
      - FIX-2: +8
  - Frontend: **166 passed**.
    - Incremento vs baseline 158: **+8 tests acumulados**
      - F94.3: +4
      - FIX-3: +4

- Reglas:
  - Cero regresión post-cada cambio lógico.
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft y back-compat.

---

## Reglas operacionales + flags

- Reglas de operación:
  - Siempre usar `yarn` (no `npm`).
  - Arquitectura fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Mantener back-compat en contratos de respuesta cuando el FE dependa de fields legacy.
  - `Discovery != persistence` debe surfacearse como error técnico `LIVE_ENRICHMENT_DROPPED_FIXTURES`.

- Flags / env relevantes:
  - ✅ **F87.1:** `DISCOVERY_DROPPED_SAMPLE_CAP` (default `3`).
  - ✅ **MLB-F93:** `MLB_MANUAL_VALUE_EDGE_THRESHOLD` (default `0.03`).
  - ✅ **MLB-F93:** `MLB_MANUAL_WATCHLIST_TOLERANCE` (default `0.02`).
  - ✅ **F94.2 / TheStatsAPI:** `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY` configurado.
