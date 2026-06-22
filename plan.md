# Plan — Phases F58–F99 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F98.1 completadas / F99 en curso.
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

### Objetivos nuevos / extendidos (F90 / Sprint F86.2) — Corners cascade con diagnóstico estructurado (Scrape.do)
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
  - **Bloque 3 (§5-§6):** reglas hard de Under + UI “Distribución y colas” ✅

### Objetivos nuevos / extendidos (D9.2-C) — Residual Model con xG real (Bonferroni estricto)
- Fortalecer el backtest residual para evitar falsos positivos por múltiples comparaciones:
  - Clasificador puro y testeable ✅
  - Corrección Bonferroni estricta ✅
  - Auditoría explícita de umbral ajustado y resultados por métrica ✅

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivos nuevos / extendidos (F95) — Football Post-Match Settlement Hotfix (P0)
(…sin cambios; ver bitácora inferior.)

### Objetivos nuevos / extendidos (F96) — Football: Settler corners + TheSportsDB experimental + ingest fallback (P1)
(…sin cambios; ver bitácora inferior.)

### Objetivos nuevos / extendidos (F97) — NIVEL 3 Bloque 3 (§5-§6): Under hard rules + UI “Distribución y colas” (P1)
(…sin cambios; ver bitácora inferior.)

### Objetivo nuevo (Sprint-F98) — **Cross-Source Identity + F74 Canonical Adapters (P0)**
**Meta:** eliminar falsos `data_quality: THIN` cuando el motor **sí tiene datos**, pero están guardados en forma distinta (anidada/legacy) vs lo que consumen editorial/selección de mercado.

(…sin cambios en esta sección; ver entrada Sprint-F98 abajo en bitácora.)

### Objetivo nuevo (F99) — **SofaScore Wiring + eliminación definitiva de API-Sports en fútbol (P0)**
**Meta global:**
- Eliminar API-Sports del pipeline de fútbol.
- Base de fixtures: **TheSportsDB**.
- Stats primarios: **SofaScore**.
- Fallback/odds: **TheStatsAPI**.
- Reusar F98/F98.1 (identity, adapters, cascade, builder). **No reconstruir capas existentes: solo cablear.**
- **F74 es el único schema canónico** hacia editorial/market selection/UI.

**Requisitos de comportamiento (binding):**
- **Fail-soft granular por campo**: si falta xG en SofaScore pero hay tiros/posesión, se selecciona SofaScore para esos campos y se cae a TheStatsAPI solo para xG.
- **Fail-soft con telemetría estructurada** (sin logs ruidosos):
  - registrar intentos por fuente en `source_trace`/`sources` (según la estructura existente del envelope/F74), sin HTML/payloads completos ni errores sensibles.
  - logs **DEBUG** para fallos esperados (blocked/timeout/schema drift); **WARNING** solo para problemas sistémicos (ej. fallos consecutivos/circuit breaker).
- **Prohibido** filtrar payload crudo de SofaScore al editorial/market/UI:
  - Flujo obligatorio: SofaScore raw → normalizador/wrapper → adapter F98 → cascade → builder → F74 → editorial.

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

## Phase Sprint Corner-2 — Datos ricos (Understat) — ✅ COMPLETADA (P0)
(Sin cambios; ver secciones anteriores.)

---

## Phase F99 — SofaScore Wiring + eliminación definitiva de API-Sports (Football) — EN PROGRESO 🟡 (P0)

### F99 · Prioridad 1 — Wire SofaScore a F74 (sin reconstruir módulos)
**Estado:** 🟡 EN PROGRESO (investigación realizada; implementación pendiente).

**Objetivo:**
- Que `football_enrichment_builder.py` reciba `_sofascore_raw` con un wrapper consistente y que el adapter existente `adapt_sofascore_to_f74(...)` produzca métricas ricas para que el cascade seleccione SofaScore como primario por campo.

**Trabajo a realizar (cableado mínimo):**
1) **Extender `services/external_sources/sofascore.py` (reusar scraper actual, no paralelo):**
   - Exponer funciones nuevas (o equivalentes) para el pipeline F99:
     - `resolve_sofascore_event(...)` (resolver event_id con búsqueda).
     - `fetch_sofascore_match_context(...)` (construir wrapper raw canónico para el adapter).
   - **Wrapper raw canónico esperado por `sofascore_adapter`:**
     - `{"event_id": <int>, "home_form": [...], "away_form": [...], "h2h": [...], "odds": {...}}`
   - Fail-soft + telemetría:
     - no lanzar excepción hacia arriba.
     - no guardar HTML/JSON crudo en match doc.
     - degradar a `None` si bloqueado/timeouts/schema drift.

2) **Cableado en el punto de ingesta/enriquecimiento (sin reescribir builder):**
   - Adjuntar el wrapper a `match["_sofascore_raw"]` **solo** cuando exista y sea seguro.
   - Asegurar que el builder ya existente lo consuma (ya lo hace) y que el output canónico siga siendo:
     - `match["football_data_enrichment"]` (F74)

3) **Telemetría estructurada (sin logs ruidosos):**
   - Registrar en el envelope/F74 `sources`/`field_provenance` el uso o fallback.
   - Añadir/propagar un bloque de trazas por fuente (si ya existe en el pipeline actual) con estados como:
     - `NO_DATA | PARTIAL | USABLE | RICH`
   - Reglas:
     - DEBUG para `BLOCKED/timeout/schema`.
     - WARNING solo para problemas sistémicos (fallos consecutivos / breaker).

4) **Regla clave:**
   - No aplicar umbral global (xG+tiros+posesión). Selección **granular por métrica** via cascade.


### F99 · Prioridad 2 — Ajustar rankings de cascada (binding del usuario)
**Estado:** ⏳ NO INICIADO.

**Archivo:** `services/football_source_cascade.py`

**Cambios solicitados:**
- Ajustar rankings (sin tocar lógica del cascade, solo rankings):
  - xG / xGA (L5): **SofaScore → TheStatsAPI → seed offline → caches** (respetando F98; se implementa vía ranking por métrica usando providers disponibles).
  - Tiros / SOT (L5): **SofaScore → TheStatsAPI → caches**.
  - Córners (L5): **offline_seed → SofaScore → TheStatsAPI → TheSportsDB (si tiene dato válido) → seed_partial**.

**Restricciones explícitas (de este turno):**
- NO modificar cascade D9 `fetch_team_corners_history_v2`.
- NO modificar `promote_online_matches_to_seed`.
- NO cambiar endpoints ni UI de córners.
- NO crear/extender todavía el odds aggregator.


### F99 · P0 transversal — Eliminación definitiva de API-Sports en fútbol
**Estado:** 🟡 EN PROGRESO.

**Objetivo:**
- Remover rutas residuales de `api_football`/`api_sports` en el pipeline de fútbol, especialmente en `services/data_ingestion.py`, manteniendo intactos otros deportes.

**Acciones:**
- Auditar `services/data_ingestion.py` y cualquier ruta football que aún haga fallback a API-Sports.
- Reemplazar con:
  - fixture base: TheSportsDB
  - stats/odds fallback: TheStatsAPI
- Mantener compatibilidad legacy donde ya está, pero sin nuevas dependencias a API-Sports en fútbol.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ Sprint-F98 — Cross-Source Identity + F74 Canonical Adapters (COMPLETADO)
- ✅ Sprint-F98.1 — Hidratación upstream selecciones (TheSportsDB) (COMPLETADO)
- 🟡 **F99 Prioridad 1:** cablear SofaScore para alimentar `_sofascore_raw` y activar selección primaria por campo en F74.
- ⏳ **F99 Prioridad 2:** ajustar rankings de cascada (incluye **córners** con orden binding).
- 🟡 **F99 P0:** eliminación completa de API-Sports en rutas football (purgar imports/fallbacks residuales).

### Pendientes P1 (próximo)
- ⏳ F99: actualizar `services/football_editorial_payload_adapter.py` (si hiciera falta para campos nuevos, sin payload crudo).
- ⏳ F99: L5/L15 Recent Form Extender (consolidar seeds + TheSportsDB + SofaScore + TheStatsAPI).
- ⏳ F99: Corner cascade modification + Idempotency promotion (fuera del scope de este turno).
- ⏳ F99: Odds aggregator (`services/football_odds_aggregator.py`) (próxima fase; **no ahora**).

### Pendientes P2
- ⏳ Background cache setup para StatsBomb/FBref.

---

## 4) Cierres recientes (bitácora)

### ✅ Sprint-D9-UI-Parity (iteration 10) — UI vs Backend Market Discrepancy: RESUELTO
(Sin cambios; ya documentado.)

### ✅ Sprint-F98 — Cross-Source Identity + F74 Canonical Adapters: COMPLETADO
(Sin cambios; ya documentado.)

### ✅ Sprint-F98.1 — Hidratación upstream para selecciones nacionales (TheSportsDB eventslast)
(Sin cambios; ya documentado.)

### 🟡 F99 — Inicio / confirmación de directivas
- Confirmado: trabajo en **PREVIEW**; el usuario hará redeploy a producción.
- Confirmado: incluir ajuste de **ranking de córners** en `football_source_cascade.py` (sin tocar D9 `fetch_team_corners_history_v2` ni promoción a seed).
- Confirmado: fail-soft con **telemetría estructurada** y sin logs ruidosos.
- Confirmado: **prohibido** payload crudo SofaScore hacia editorial/UI.
- Confirmado: selección granular por campo (sin umbral global).

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

**Estado actual de la suite backend:** `4804 passed / 11 skipped` (0 regresiones).

---

## Reglas operacionales + flags

- Reglas:
  - Siempre usar `yarn` (no `npm`).
  - Fail-soft: no levantar excepción sin convertirla a auditoría/razón.
  - Backtests: disciplina point-in-time estricta.
  - **No tocar** `MONGO_URL` ni `REACT_APP_BACKEND_URL`.
  - F99: cambios solo en **PREVIEW**; sin operaciones destructivas/migraciones en producción en este turno.

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - TheSportsDB: `THESPORTSDB_KEY=...`.
  - Scrape.do: `SCRAPEDO_TOKEN=...` (necesario para SofaScore scraping).

- Sprint-D9-OddsCascade / CornerAutoFallback:
  - `ENABLE_ODDS_CASCADE_FALLBACK=true` (default; OddsPortal vía Scrape.do).
  - `ENABLE_CORNER_AUTO_FALLBACK=false` (opt-in; promociona a Asian Corners cuando edge ≥ 8%).
  - `CORNER_AUTO_FALLBACK_MIN_EDGE_PCT=8.0` (decisión usuario).

- Política Sprint-F98 (vigente):
  - StatsBomb/FBref: cache-first / background-only (no bloquear request principal).
  - Resolver identidad cross-source: persistir `matches.cross_source_ids`.
  - Consumers: leer F74 primero + fallback legacy con telemetría.

- Política Sprint-F98.1 (vigente):
  - `eventslast.php` de TheSportsDB es el fallback oficial para `recent_fixtures` cuando API-Sports no provea datos.
  - Fallback debe correr tanto en `deep=True` como `deep=False`.

- Política F99 (nueva):
  - SofaScore es primario de stats por campo; TheStatsAPI es fallback.
  - Eliminación de API-Sports en fútbol.
  - Telemetría estructurada por fuente (sin payloads ni PII) y logs no ruidosos.

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

---

## FASE F99 — SofaScore Wiring + eliminación funcional de API-Sports — COMPLETADO ✅

### Resumen
Cableado de SofaScore como **fuente estadística primaria** del esquema canónico F74, ajuste de los rankings de cascada según binding del usuario, y purga **funcional** (kill-switch) de API-Sports en el pipeline de fútbol — **sin reconstruir** ningún módulo F98/F98.1.

### Decisiones del usuario (binding aplicado)
- **PREVIEW only**: cambios listos para validación; redeploy a producción a cargo del usuario.
- **Corners ranking**: `offline_seed → SofaScore → TheStatsAPI → TheSportsDB → seed_partial → caches legacy`.
- **Sin tocar**: cascade D9 (`fetch_team_corners_history_v2`), `promote_online_matches_to_seed`, endpoints/UI de córners, odds aggregator (queda para próxima fase).
- **Fallo de SofaScore**: fail-soft con telemetría estructurada, sin logs ruidosos; DEBUG para fallos esperados; WARNING reservado para problemas sistémicos.
- **Sin payload crudo al editorial**: `_sofascore_raw` es ya una versión normalizada por `fetch_sofascore_match_context` (nunca HTML/JSON completos).
- **Umbral granular**: selección de campo por campo (no umbral global); estados descriptivos `NO_DATA | PARTIAL | USABLE | RICH` solo para telemetría.

### Archivos modificados
- `services/external_sources/sofascore.py`
  - Bajados WARNING/INFO a DEBUG para fallos esperados (no logs ruidosos).
  - Añadidas funciones públicas F99:
    - `resolve_sofascore_event(home, away, *, sport, target_date)` — wrapper público sobre `_resolve_event_id`.
    - `fetch_sofascore_match_context(home, away, *, sport, recent_n, h2h_n, enrich_stats, total_timeout_s)` — produce el wrapper canónico `{event_id, home_form, away_form, h2h, odds, _trace}` que consume `adapt_sofascore_to_f74`.
  - Mapeo de stats SofaScore → métricas adapter: `shots_on_target`, `shots`, `possession`, `corners`, `xg`.
  - Timeout total y fail-soft estricto (nunca raise hacia el caller).
- `services/football_sofascore_hydrator.py` **(NUEVO)**
  - Hydrator opt-in (`ENABLE_F99_SOFASCORE_HYDRATION`, default off).
  - Escribe telemetría estructurada en `match["football_data_enrichment_source_trace"]["sofascore"]` con `attempted | status | valid_fields | missing_fields | fallback_triggered | checked_at`.
  - Adjunta `match["_sofascore_raw"]` solo si hay datos usables.
  - Nunca raise hacia el caller.
- `services/data_ingestion.py`
  - Llamada al hydrator inmediatamente después del bloque TheStatsAPI en el path football (no toca otros deportes).
- `services/football_source_cascade.py`
  - **xG / xGA L5**: SofaScore → TheStatsAPI → StatsBomb → FBref.
  - **Tiros / SOT L5**: SofaScore → TheStatsAPI → StatsBomb → FBref (sin cambios; ya cumplía).
  - **Córners**: `offline_seed → sofascore → thestatsapi → thesportsdb → seed_partial → footystats → totalcorner` (declarativo; `offline_seed`/`seed_partial` aún sin envelopes propios — fail-soft `PROVIDER_NOT_PRESENT`).
- `services/api_football.py`
  - Kill switch `DISABLE_API_FOOTBALL`: cuando está activo, `_get` retorna `{"response": [], "errors": {}, "_f99_disabled": True}` (cero IO, cero excepciones). La purga estructural de los ~40 call-sites en `data_ingestion.py` queda para F99.2 (low-risk follow-up).
- `tests/test_f99_sofascore_wiring.py` **(NUEVO)** — 23 tests pasando.
- `tests/test_f98_cascade_selector_phase3.py` — 8 tests actualizados al nuevo binding F99.

### Validación
- **Pytest full**: `4850 passed, 11 skipped, 0 warnings` en 324s (baseline elevada desde 4827 → +23 nuevos tests F99).
- **0 regresiones**, **0 warnings**.
- Lint Python: clean.

### Cobertura de tests F99
1. `resolve_sofascore_event` — fail-soft (empty teams, sport desconocido, scrape.do unavailable) + delegación al resolver interno.
2. `fetch_sofascore_match_context` — fail-soft (event no resuelto, sport no soportado, timeout) + construcción correcta del wrapper para el adapter F98.
3. Hydrator — feature flag off / sport no soportado / team names ausentes / wrapper válido / fetch=None / excepciones inesperadas.
4. Cascade rankings — xG/SOT/Tiros/Córners según spec; selección dinámica y fallback ordenado.
5. API-Sports kill switch — helper `is_disabled`, short-circuit sin IO, comportamiento legacy preservado cuando el flag está off.

### Lo que NO se hizo en este turno (deferido por binding del usuario)
- ❌ **No** se modificó cascade D9 `fetch_team_corners_history_v2`.
- ❌ **No** se modificó `promote_online_matches_to_seed`.
- ❌ **No** se cambiaron endpoints/UI de córners.
- ❌ **No** se extendió/creó odds aggregator (queda para próxima fase).
- ❌ **No** se cambió ranking ni cascade de odds (queda para próxima fase).
- ❌ **No** se removieron los call-sites de `af.*` en `data_ingestion.py` (purga **estructural**) — preferimos kill switch funcional sin riesgo de regresión. Sub-fase F99.2 sugerida.

### Follow-ups sugeridos (próxima fase)
- **F99.1** — Adapters para envelopes `offline_seed` y `seed_partial` (córners). Hoy son `PROVIDER_NOT_PRESENT` → cuando se cableen, el ranking declarativo entrará en efecto sin tocar el cascade.
- **F99.2** — Purga estructural de call-sites `af.*` en `data_ingestion.py` + remoción del import.
- **F99.3** — Editorial payload adapter (`services/football_editorial_payload_adapter.py`) para consumir métricas de `football_data_enrichment` enriquecido con SofaScore.
- **F99.4** — L5/L15 Recent Form Extender (consolidación seed + TheSportsDB + SofaScore + TheStatsAPI).
- **F99.5** — Odds aggregator + ranking/cascade de odds.
- **F99.6** — Background cache para StatsBomb/FBref.

### Activación en producción
1. Desplegar a producción.
2. Set env `ENABLE_F99_SOFASCORE_HYDRATION=true` para activar el hydrator.
3. Set env `DISABLE_API_FOOTBALL=true` para activar el kill switch funcional.
4. Monitorear `match["football_data_enrichment_source_trace"]["sofascore"]` para observabilidad granular por partido.

