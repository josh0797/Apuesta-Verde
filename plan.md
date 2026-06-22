# Plan — Phases F58–F98.x (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F97 completadas / en curso según bitácora.
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

**Problema específico detectado:**
- `data_ingestion.py` guarda forma reciente en `home_team.context.recent_fixtures` / `away_team.context.recent_fixtures`.
- El editorial y consumidores legacy buscaban campos planos tipo `home_xg`, `home_goals_scored_l5` o `home_team.goals_scored_l5`.
- Resultado: caídas a `data_quality: THIN` aunque haya datos reales.

**Decisiones del usuario (confirmadas):**
- F74 (`services/football_data_enrichment.py`) debe ser la **única fuente canónica** para consumidores.
- Migración gradual y segura: legacy se mantiene como fallback mientras se instrumenta telemetría.
- Resolver identidad cross-source durante ingesta y persistir en `matches.cross_source_ids`.
- StatsBomb y FBref: **cache-first / background-only**, no bloquear request principal.
- Fail-soft granular por campo/métrica: fallback ante error/timeout/captcha/schema inesperado/empty/null/sample insuficiente/stale.

**Entregables (implementados ✅):**
1) `services/football_cross_source_identity.py` ✅
   - `resolve_football_match_sources(base_match, client, db=None) -> dict` (async)
   - Matching: fecha ± 6 horas, home/away normalizados, competición, aliases selecciones.
   - Regla dura: NO unir solo por nombres si fecha no coincide.

2) Adapter layer (puro) en `services/adapters/` ✅
   - Envelope canónico: `services/adapters/_envelope.py` (provenance + sample_size + data_quality score)
   - `adapt_thesportsdb_to_f74(raw) -> dict`
   - `adapt_sofascore_to_f74(raw, *, home_team, away_team) -> dict`
   - `adapt_thestatsapi_to_f74(raw) -> dict`
   - `adapt_statsbomb_to_f74(raw) -> dict` (cache-first)
   - `adapt_fbref_to_f74(raw) -> dict` (cache-first)
   - `adapt_legacy_match_to_f74(match) -> dict` (bridge legacy → envelope)
   - **Fix crítico:** coalesce de scores (0 es válido) para evitar perder fixtures con `away_score=0`.

3) Cascade selector por campo ✅
   - `services/football_source_cascade.py` con rankings binding por métrica
   - Fail-soft granular por campo: salta provider ante unavailable/empty/null/sample insuficiente/stale/schema mismatch
   - Provenance por métrica + fallback chain.

4) Builder canónico F74 ✅
   - `services/football_enrichment_builder.py`: materializa `football_data_enrichment` **en memoria** combinando:
     - adapters por fuente (si existen raws) +
     - legacy bridge adapter +
     - cascade por campo.
   - **Override ranking:** añade `legacy_match_doc` como último fallback para que no sea ignorado.

5) Consumer (editorial) lee F74 primero ✅
   - `services/football_editorial_prediction._data_completeness` actualizado:
     - 1) usa `match["football_data_enrichment"]` si existe
     - 2) si no existe, lo construye via builder (sin IO)
     - 3) legacy flat sigue como último fallback
   - Añade `schema_migration` telemetry al output.

**Validación Sprint-F98:**
- Suite completa: **4778 passed / 11 skipped / 0 failures** (0 regresiones).
- E2E tests parametrizados (Argentina–Austria / Uruguay–Cabo Verde / NZ–Egipto) con match-doc shape de `data_ingestion` ✅

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

## Phase Sprint Corner-2 — Datos ricos (Understat) — **✅ COMPLETADA (P0)**
(Sin cambios; ver secciones anteriores.)

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ Corner Momentum Study (Fase 1) completada.
- ✅ Sprint Corner-2 (Understat) completada.
- ✅ Sprint Corner Fase A (módulos + backtest probabilístico) completada.
- ✅ Sprint Corner Fase B (Skellam + endpoint + UI) completada.
- ✅ Skellam P0 estabilidad/guards/validación avanzada completada.
- ✅ Sprint-D9-UI-Parity (iteration 10) completada.
- ✅ **Sprint-F98 — Cross-Source Identity + F74 Canonical Adapters (COMPLETADO)**
  - Fase 1 ✅, Fase 2 ✅, Fase 3 ✅, Fase 4 ✅, Fase 5 ✅
  - Tests nuevos: **+155** + **+5 E2E focus** ⇒ **+160**
  - Suite: **4778 passed / 11 skipped / 0 failures**

#### ✅ Pendiente P0 (resuelto) — Sprint-F98.1: Hidratación upstream de forma reciente para selecciones
**Hallazgo original:** el endpoint REAL traía `recent_fixtures=[]` y `h2h_recent=[]` para selecciones → editorial real caía a THIN.

**Remediación implementada (Sprint-F98.1) ✅:**
1) **TheSportsDB `eventslast.php` como reemplazo real de API-Sports**
   - Archivo: `services/external_sources/thesportsdb_client.py`
   - Nuevo: `fetch_last_events_by_team(team_id, n=5)` + normalizador `_normalize_event_to_recent_fixture`.

2) **Fallback en `data_ingestion.py` fuera de `if deep:`**
   - Esto asegura que:
     - `deep=True` (upcoming/background)
     - `deep=False` (live ingest)
     ambos hidraten `recent_h_raw`/`recent_a_raw` cuando API-Sports sea vacío.
   - Resolver de idTeam por nombre (`search_teams`) con filtros:
     - `strSport == Soccer`
     - exclusión `U17/U20/U23/Women/Youth`
     - preferencia por ligas nacionales (World Cup / Nations League / Qualifiers)

3) **Script de seed para selecciones (opcional, acelerador de cobertura)**
   - Archivo: `services/football_national_team_seed.py`
   - CLI + batch: `seed_national_team_recent_form()`
   - Colección nueva: `football_team_recent_fixtures_seed`
   - Lista top: `TOP_NATIONAL_TEAMS` (~60–80 selecciones) incluyendo los 3 partidos focales.

**Validación Sprint-F98.1 ✅:**
- Tests nuevos: **+21** (`test_f98_1_upstream_hydration.py`) + **+5** E2E focus.
- Suite completa: **4804 passed / 11 skipped / 0 failures**.
- Logs backend confirman hidratación masiva: ~20 hidrataciones (10 partidos × 2 sides) vía TheSportsDB.
- E2E real tras restart:
  - **Argentina vs Austria** pasó de `incomplete_data` → `medium_confidence` (market `Total Under`, conf=66).
  - Se observan mejoras de calidad (THIN→LIMITED) en múltiples partidos.
  - Algunos partidos siguen en `discarded_market` por `ODDS_MISSING` (válido; no es THIN).

**Estado:** ✅ LISTO para redespliegue.

### Pendientes P1 (próximo)
- ⏳ **Backtest financiero con TheOddsAPI (P1)**
  - Objetivo: 100–150 partidos (muestra controlada por coste de créditos).
  - Mercados: **Asian Corners** y/o “Most Corners”.
  - Condición: marcar explícitamente `REAL_ODDS_NOT_AVAILABLE` cuando falten cuotas.
  - Entregables:
    - reporte ROI/CLV/hit-rate por línea
    - breakdown por liga + por bucket de `dominant_favorite_strength`
    - auditoría de sesgo (solo picks recomendados vs todos)

- 🟡 SPRINT D5 (histórico en curso): cohortes + reportes multi-competición.
- 🟡 REFACTOR-1 (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ F84.c/F84.d Lineups + Standings.

### Pendientes P2
- ⏳ (Acordado) **NO construir aún Total Corners O/U** como motor principal.

---

## 4) Cierres recientes (bitácora)

### ✅ Sprint-D9-UI-Parity (iteration 10) — UI vs Backend Market Discrepancy: RESUELTO
(Sin cambios; ya documentado.)

### ✅ Sprint-F98 — Cross-Source Identity + F74 Canonical Adapters: COMPLETADO
**Archivos clave entregados:**
- `services/football_cross_source_identity.py`
- `services/adapters/_envelope.py` + adapters por fuente
- `services/football_source_cascade.py`
- `services/adapters/legacy_match_adapter.py`
- `services/football_enrichment_builder.py`
- `services/football_editorial_prediction.py` (read-first F74 + telemetría)

**Notas:**
- Fix crítico de coalesce para scores=0 en adapters.
- `legacy_match_doc` agregado como fallback final en rankings dentro del builder.

### ✅ Sprint-F98.1 — Hidratación upstream para selecciones nacionales (TheSportsDB eventslast)
**Archivos clave entregados:**
- `services/external_sources/thesportsdb_client.py`
  - `fetch_last_events_by_team()`
  - `_normalize_event_to_recent_fixture()`
- `services/data_ingestion.py`
  - fallback TheSportsDB aplicado también para `deep=False`
  - resolver idTeam por `search_teams` con filtros anti-juveniles
- `services/football_national_team_seed.py`
  - CLI + batch seeding de selecciones (colección `football_team_recent_fixtures_seed`)
- Tests:
  - `tests/test_f98_1_upstream_hydration.py` (+21)
  - `tests/test_f98_e2e_focus_matches.py` (+5)

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

- Flags / env (principales):
  - `ENABLE_THE_STATS_API=true` + `THESTATSAPI_KEY`.
  - `THE_ODDS_API_KEY=...`.
  - TheSportsDB: `THESPORTSDB_KEY=...`.
  - Corner Engine:
    - `ENABLE_CORNER_MOST_MODEL=true`
    - `ENABLE_ASIAN_CORNERS_MODEL=true`

- Sprint-D9-OddsCascade / CornerAutoFallback:
  - `ENABLE_ODDS_CASCADE_FALLBACK=true` (default; OddsPortal vía Scrape.do).
  - `ENABLE_CORNER_AUTO_FALLBACK=false` (opt-in; promociona a Asian Corners cuando edge ≥ 8%).
  - `CORNER_AUTO_FALLBACK_MIN_EDGE_PCT=8.0` (decisión usuario).
  - `SCRAPEDO_TOKEN=...` (necesario para fetch real de OddsPortal; ausente → cascada degrada fail-soft).

- Política Sprint-F98 (nueva):
  - StatsBomb/FBref: cache-first / background-only (no bloquear request principal).
  - Resolver identidad cross-source: persistir `matches.cross_source_ids`.
  - Consumers: leer F74 primero + fallback legacy con telemetría.

- Política Sprint-F98.1 (nueva):
  - `eventslast.php` de TheSportsDB es el fallback oficial para `recent_fixtures` cuando API-Sports no provea datos.
  - Fallback debe correr tanto en `deep=True` como `deep=False`.

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
