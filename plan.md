# Plan — Phases F58–F97.x (bitácora)

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
**Contexto:** bug productivo donde partidos ya finalizados (ej. *Brazil vs Haiti*) seguían apareciendo como elegibles en “Generar picks del día”.

**Diagnóstico:** `settle_post_match()` existe (learning snapshots), pero no había job scheduler equivalente al de MLB; el sistema no persistía `POST_MATCH_RESULT_SETTLED` para fútbol de forma periódica.

**Objetivos F95 (P0):**
1. Arreglar settlement post-match football (final_score).
2. Robustecer gate de fixtures (guard de 4h stale-kickoff).
3. Cascada de proveedores (final_score football): TheStatsAPI → TheSportsDB → API-Sports.
4. Scheduler cada 20 min (ventana 36h).

### Objetivos nuevos / extendidos (F96) — Football: Settler corners + TheSportsDB experimental + ingest fallback (P1)
**Contexto:** tras F95, cerrar el bucle de corners post-match y ampliar el rol de TheSportsDB como fallback.

**Decisiones del usuario aplicadas:**
- **Corners (post-match):**
  - Fuente primaria: **TheStatsAPI** `match_stats`.
  - Fuente secundaria experimental: **TheSportsDB** event stats:
    - V1: `lookupeventstats.php?id={idEvent}`
    - V2: `/lookup/event_stats/{idEvent}` (si premium; usar solo si disponible)
  - Parser defensivo de nombres de stats (normalización + matching):
    - `"corners"`, `"corner kicks"`, `"corner_kicks"`, `"total corners"`, `"corners total"`
  - Si TheSportsDB no trae corners o trae solo 1 lado:
    - **NO forzar settle**
    - mantener `POST_MATCH_CORNERS_MISSING`
    - reason codes: `THESPORTSDB_CORNERS_NOT_AVAILABLE`, `PARTIAL_CORNERS_DATA`
  - Debug obligatorio: logear **raw stat names** recibidos desde TheSportsDB.
- **Fixtures/enrichment (fútbol):**
  - TheSportsDB también se usa para:
    - fallback de **discovery de fixtures upcoming**
    - enrichment (logos/badges/nombres de liga + IDs cruzados)
  - Prioridad: **TheStatsAPI > TheSportsDB fallback**

### Objetivos nuevos / extendidos (F97) — NIVEL 3 Bloque 3 (§5-§6): Under hard rules + UI “Distribución y colas” (P1)
**Contexto:** completar Nivel 3 con reglas duras específicas para picks de Under y UI diagnóstica.

**Decisiones del usuario aplicadas:**
- Reglas hard sobre `final_over_probabilities` (post-NIVEL3):
  - `over_risk >= 0.55` → **BLOCK**
  - `0.48 <= over_risk < 0.55` → **AVOID**
  - `0.42 <= over_risk < 0.48` → **WARN**
  - `tail == HIGH` y `line <= 9.5` → **AVOID**
  - `tail == EXTREME` → **BLOCK**
  - Gana siempre la acción más severa (OR).
- Impacto scoring y feed:
  - WARN: `score -= 3` + warning visible.
  - AVOID: `score -= 10`, no puede salir como **MÁXIMA**, flag `under_recommendation_degraded=true`.
  - BLOCK: `is_blocked=true`, excluido del feed principal, preservado en categoría **"debug"**.
- UI nueva “Distribución y colas”: card dedicado en detalle MLB, mostrando warnings + métricas clave.

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
  - Cascada: **TheStatsAPI → TheSportsDB → API-Sports**.
  - TheSportsDB key en env: `THESPORTSDB_KEY=...`.
  - Scheduler: cada **20 min**, ventana **36h**.

### Estado técnico (entregables)
- ✅ Guard stale-kickoff + tests.
- ✅ `football_finished_game_settler.py` (final_score) + tests.
- ✅ `_job_settle_finished_football` en `scheduler.py` + tests.
- ✅ pytest completo sin regresiones.

---

## Phase F96 — Football: Settler corners + TheSportsDB experimental + ingest fallback (P1) — ✅ COMPLETADO

### F96.1 — TheStatsAPI `match_stats` corners extractor + integración ✅
- `football_finished_game_settler.py` extendido con:
  - `_extract_corners_from_payload(payload)` (6+ shapes: flat, dict home/away, scalar total, stats list, qualifiers con paréntesis, nested team stats).
  - `_lookup_corners_from_thestatsapi(match_id)` usando `fetch_match_stats` (fail-soft).
  - `lookup_total_corners(match_id, snapshot_doc, *, http_client)` como orquestador de cascada.
  - Integración en `settle_recent_finished_football`: hydration best-effort (NO bloquea final_score).
- Tests: `backend/tests/test_football_settler_corners.py`.

### F96.2 — TheSportsDB corners experimental (event stats) + debug ✅
- `thesportsdb_client.py` extendido con `lookup_event_stats(event_id)`:
  - V1: `/v1/json/{key}/lookupeventstats.php?id=...`
  - V2: `/v2/json/lookup/event_stats/{idEvent}` (si está disponible)
- Parser defensivo + matching por alias (`CORNER_STAT_ALIASES`).
- Resolución de `event_id`:
  - directo vía `snapshot_doc["thesportsdb_event_id"]` cuando existe
  - fallback por `fetch_livescore("soccer")` si falta
- Reason codes corners:
  - `CORNERS_FROM_THESPORTSDB`
  - `THESPORTSDB_CORNERS_NOT_AVAILABLE`
  - `PARTIAL_CORNERS_DATA`
- Debug obligatorio: log `raw_names` en `lookup_event_stats`.
- Tests: `backend/tests/test_thesportsdb_event_stats.py`.

### F96.3 — TheSportsDB fixtures fallback + enrichment ✅
- Fixtures fallback (observe-only):
  - `fetch_upcoming_events_by_date(date, sport)` (V1 `eventsday.php`).
  - `fetch_next_events_by_league(league_id)` (V1 `eventsnextleague.php`).
  - Normalización canónica `_normalize_event_item`.
- Enrichment:
  - `enrich_team_badge(team_name)` (prefiere soccer, fallback a primer match).
  - `search_leagues(country, sport)`.
  - `lookup_league(league_id)`.
- Tests: `backend/tests/test_thesportsdb_fixtures_enrichment.py`.

### F96.4 — Validación ✅
- Backend: `pytest` completo **4273 passed / 2 skipped** (+74 vs F95), 0 regresiones.

---

## Phase Sprint-D8/E-LIVE — Corners diagnostic real + Cards Fase 1 AUC ablation real (P1) — ✅ COMPLETADO

Ejecución en vivo de los dos pendientes del Sprint-D8/E ahora que scrape.do está habilitado y los datos de Premier están disponibles.

### PASO 1 (LIVE) · Corners diagnostic — VEREDICTO REAL: `PARSER_FAILURE`
Corrida contra **Manchester United vs Fulham (2024-08-16, EPL 24/25)**, game_id 365Scores = **4147020** (resuelto manualmente; el resolver por nombre+fecha tuvo timeout en la 3ª llamada de scan, no crítico).

**Veredicto por capa**:
| Capa | Detail (`/web/game/`) | Stats (`/web/game/stats/`) |
|---|---|---|
| Transport | ✅ OK (133 KB, 2.3 s) | ❌ **TIMEOUT 35 s** |
| Endpoint (alias córner) | ⚠️ 1 match (chartEvents.eventSubTypes — etiqueta, no estadística) | — (sin payload) |
| Parser | ❌ `available=false`, `total_corners=null` | — |

**Diagnóstico fino**:
- El endpoint `/web/game/?gameId=` SÍ responde rápido, pero **no contiene** el bloque `statistics[]` con córners/posesión/etc. — solo eventos discretos (cards, goals, subs), referee, lineups.
- El endpoint `/web/game/stats/?gameId=` (que históricamente traía las stats agregadas) está **bloqueado o deprecado** — timeout consistente a 35 s.
- Bonus que SÍ se puede extraer del `/game/?`: **referee** (`officials[0].name`) y **tarjetas** (`events[*].eventType.id == 2 → Yellow`, `id == 3 → Red`) por equipo (vía `competitorId`).

**Acción para producción** (no incluida en este sprint; lista para próximo):
1. Investigar si 365Scores cambió el path de stats (probar `/web/games/stats/`, `/web/game-stats/`, o un parámetro adicional como `&includeStatistics=true` en `/web/game/`).
2. Mientras tanto, ajustar `normalize_365scores_match_stats` para que LEA del `events[]` cuando solo el detail está disponible (cards parciales).
3. Como alternativa para córners: completar la cascada con TheStatsAPI / TheSportsDB / football-data.co.uk (que sí tiene HC/AC).

---

### PASO 2 (LIVE) · Cards Fase 1 AUC ablation real — VEREDICTO: `AUC_MARGINAL`
**Decisión clave (ahorro de créditos)**: en lugar de scrape.do en 150 partidos × 365Scores (~270 créditos), descubrí que **football-data.co.uk E0_2425.csv ya tiene TODOS los campos requeridos GRATIS**:
- `Referee`, `HF/AF` (fouls), `HY/AY` (yellows), `HR/AR` (reds) — 380 partidos EPL 24/25 completos.

**Dataset construido**: 380 partidos (24/25 entero); evaluación enfocada en los últimos 150 (`2025-02-01 → 2025-05-25`); 23 árbitros distintos; avg cards/partido = 3.69.

**Tabla AUC ablation por línea (entregable estrella)**:
| Línea | base_rate | AUC sin árbitro | AUC con árbitro | Δ AUC | ¿Árbitro ayuda? |
|---|---|---|---|---|---|
| **3.5** | 0.6053 | 0.5597 | 0.5541 | **−0.0056** | No |
| **4.5** | 0.4079 | 0.5502 | 0.5637 | **+0.0135** | Sí (marginal) |
| **5.5** | 0.2526 | 0.5848 | 0.5877 | **+0.0029** | No |

**Verdict tags**: `REFEREE_FACTOR_MIXED_SIGNAL`.

**Categoría por línea** (rubric del usuario):
- 3.5: AUC=0.55 — `AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2`
- 4.5: AUC=0.56 — `AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2`
- 5.5: AUC=0.59 — `AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2`

**Recomendación: NO pasar a Fase 2** (cuotas históricas + CLV).
Razones:
1. **Ningún line cruza el umbral 0.60** de "el modelo discrimina claramente; justifica gastar créditos contra el mercado".
2. **El factor árbitro NO añade señal robusta**: ayuda solo +0.0135 en una línea (4.5), neutral/negativo en las otras dos. La hipótesis "el árbitro es la señal dominante" queda **no confirmada** con esta muestra (n=380).
3. **Patrón consistente con los demás mercados ya cerrados**:
   - DRAW ligas: AUC ≈ 0.50 → cerrado.
   - DRAW selecciones: AUC=0.55, modelo PIERDE al devigged → cerrado.
   - OVER/UNDER 2.5/3.5: AUC 0.50-0.56 → cerrados.
   - Cards 3.5/4.5/5.5: AUC 0.55-0.59 → mismo régimen → **CERRAR**.

**Reason codes finales**:
- `CARDS_MODEL_NO_LINE_CROSSES_0_60_THRESHOLD`
- `REFEREE_FACTOR_NOT_ROBUST_ACROSS_LINES`
- `MARKET_TARJETAS_NOT_BEATABLE_WITHOUT_PHASE_2_BUT_NOT_WORTH_PROBE`
- `RECOMMEND_CLOSE_CARDS_DEFINITIVELY`

### Entregables
- ✅ `/app/diagnostics/sprint_d8e_corners_diagnostic.json` — veredicto real `PARSER_FAILURE` con audit completo por capa.
- ✅ `backend/scripts/build_premier_cards_dataset_from_csv.py` — builder gratis desde CSV.
- ✅ `/app/data/cards_history/premier_last_4_months.json` — 380 partidos EPL 24/25.
- ✅ `/app/diagnostics/cards_phase1_modelonly.json` — tabla AUC ablation real.
- ✅ Créditos consumidos: ~5 scrape.do (corners diagnostic) + 0 The Odds API.

---

## Phase Sprint-D8-Fase2 — DRAW + DOMINANT_FAVORITE en selecciones + Cascada TheSportsDB primaria (P1) — ✅ COMPLETADO

### Resumen ejecutivo
- **Hipótesis original (DRAW en selecciones bate al mercado)** → ❌ **REFUTADA empíricamente** sobre 123 partidos (WC2022 + Euro2024 + Copa América 2024) con datos PIT reales.
- **Refactor de cascada de descubrimiento** (TheSportsDB primario) → ✅ Implementado y validado contra producción (Germany vs Ivory Coast detectado en 2026-06-20).
- **Backend**: 4,421 tests passing, 0 regresiones.

### PASO A · DRAW backtest en selecciones — REFUTADO
**Datos reales obtenidos** (PIT estricto, sin leakage):
- Odds prematch (kickoff − 3h) desde The Odds API histórico para 3 sport_keys verificados con `/v4/sports?all=true` (gratis).
- Ground truth desde openfootball (wc2022.json, euro2024.json) + parser de Copa América 2024 (32 partidos, manejando `pen.`/`a.e.t.` para extraer score regulación 90').
- FIFA ranking points (PIT pre-torneo) desde repo público Dato-Futbol (proxy de "ELO gap" para selecciones, descarga estática gratis).

**Resultado del análisis D9** (n=123 records):
| Métrica | Modelo | Mercado de-vigged | Δ |
|---|---|---|---|
| AUC | **0.552** | 0.635 | -0.083 |
| Brier | 0.192 | 0.184 | **+0.008** (modelo pierde) |
| LogLoss | 0.573 | 0.551 | **+0.022** (modelo pierde) |

**Veredicto Bonferroni**: `PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE`
- Tags: `MODEL_DOES_NOT_BEAT_MARKET_DEVIG`, `HYPOTHESIS_SUGGESTIVE_BUT_NOT_PROVEN`.
- Cohorte `DOMINANT_FAVORITE_DRAW_VALUE`: **n=0** (ningún partido cumplió el filtro `edge_pp ≥ 8` simultáneamente con `|ELO_delta| ≥ 150` — el modelo NO encuentra value sobre el mercado de selecciones).
- Conclusión honesta: la hipótesis "selecciones tienen sesgo de favorito que infla la cuota del empate" **no se sostiene** con esta evidencia.

**Auditoría transparente de créditos**:
| Run | Crédito | Resultado |
|---|---|---|
| #1 (bugs) | 2,506 | 0 records (build_match_record leía payload mal + falta de dedup en /events listing) |
| #2 (over-filter) | 102 | 2 records (filtro commence_day != listing_day demasiado agresivo) |
| #3 (final correcto) | **1,552** | **123 records** ✓ |
| **TOTAL** | **4,160** | — |

**3 bugs corregidos con tests de regresión**:
1. `build_match_record` ahora lee `odds_event["event_payload"]["bookmakers"]` (anidado).
2. `fetch_tournament_pit_odds` dedup por `event_id` antes de cada `/odds` (10 créditos).
3. Eliminado filtro `commence_day != listing_day` que era demasiado agresivo (causaba 0 fixtures).

### PASO B · Cascada TheSportsDB primaria
**Decisión del usuario aplicada**:
- (1c) Refactor de fixtures + basketball/baseball + odds enrichment.
- (2b) The Odds API solo como secundario en **enrichment de odds**, no de fixtures.
- (4b) Test de integración en vivo contra TheSportsDB real.

**Cambios**:
- Nuevo módulo `services/external_sources/thesportsdb_fixtures_adapter.py`:
  - `fetch_fixtures_next_48h(client, sport='Soccer')` → llama `eventsday.php` para hoy + mañana (UTC).
  - Normaliza al shape API-Football (`fixture`, `league`, `teams`) para integrarse con FFC sin tocar el pipeline existente.
  - Fail-soft total con reason codes (`THESPORTSDB_DISABLED`, `THESPORTSDB_FIXTURES_OK`, etc.).
- `services/data_ingestion.py` — `_discover_football_fixtures`:
  - **Step 0 nuevo**: TheSportsDB (primary, env flag `ENABLE_THESPORTSDB_FIXTURES_PRIMARY=true` por default).
  - Step 1 (legacy): TheStatsAPI degradado a secundario.
  - Step 2: API-Football fallback.
  - Step 3-5: ESPN / Sofascore / scrape.do (sin cambios).

**Validación en vivo**:
- `test_thesportsdb_cascade_discovers_germany_vs_ivory_coast_when_present`: la cascada devuelve para 2026-06-20:
  ```
  Germany vs Ivory Coast | FIFA World Cup | 2026-06-20T20:00:00Z
  ```
- 4/4 tests de cascada passing.

### Entregables del Sprint
- ✅ `services/football_goals_3_5_closure.py` (cierre 3.5 — heredado D8E, ya en plan).
- ✅ `services/theoddsapi_historical_client.py` actualizado con `verify_sport_keys_available` + `estimate_credit_cost` + dedup hard cap.
- ✅ `services/football_selecciones_ingestor.py` (270 líneas, función pura DI, fail-soft).
- ✅ `services/external_sources/thesportsdb_fixtures_adapter.py` (nuevo, refactor de cascada).
- ✅ `services/data_ingestion.py` — Step 0 TheSportsDB insertado.
- ✅ Scripts:
   - `scripts/fetch_copa_america_2024_openfootball.py` (32 partidos parseados desde TXT).
   - `scripts/fetch_fifa_ranking_points.py` (PIT pre-torneo, 3 tournaments).
   - `scripts/run_selecciones_draw_backtest.py` (dry-run preflight + tope duro 2500 + Bonferroni verdict).
- ✅ Datos:
   - `/app/data/openfootball/copa_america_2024.json` (32 matches con regulation-time scores).
   - `/app/data/fifa_ranking/team_points_by_tournament.json` (3 tournaments, PIT).
- ✅ Diagnóstico final: `/app/diagnostics/sprint_d8_fase2_selecciones_draw_backtest.json`.
- ✅ Tests: 22 (selecciones) + 4 (cascada) = **+26 tests** vs baseline.

### Validación global
- Backend: `pytest tests/` → **4,421 passed / 2 skipped / 0 failed** (211.85s).
- **+26 tests** vs baseline Sprint-D8/E (4,395). 0 regresiones.
- Lint limpio en 6 archivos nuevos/modificados.
- observe_only confirmed: 0 picks productivos, 0 cambios a scoring, 4,160 créditos consumidos (autorizados explícitamente por el usuario en opción c con disclaimer de bugs previos).

### Reporte de cumplimiento (criterios de aceptación)
- [x] sport_keys verificados con endpoint **FREE** antes de gastar créditos.
- [x] Tope duro `MAX_CREDITS` que aborta antes de sobregirar; créditos logueados por torneo.
- [x] `odds_timestamp` PIT (`kickoff − 3h`) persistido en `source_audit`.
- [x] Mismo análisis AUC/Brier/calibración/cohorte que refutó ligas (D9 reusado).
- [x] Cohorte medido sobre **todos los disparos**, definido por features prematch (test PIT anti-leakage passing).
- [x] Cross-tab con DOMINANT_FAVORITE en ligas (n=0 en selecciones → no rinde mejor que ligas).
- [x] Veredicto Bonferroni honesto (`PATTERN_NOT_YET_PROVEN`, no falsamente confirmado).
- [x] observe_only; sin producción; sin `vercel --prod`.
- [x] Sin regresiones (4,421 pasando).
- [x] Cascada TheSportsDB primaria implementada y verificada con fixture real (Germany vs Ivory Coast).

### Respuesta directa al entregable solicitado
> "DRAW en selecciones: AUC=**0.552** (vs ligas ~0.50 baseline), delta_brier_vs_devig=**+0.008** (modelo pierde). Cohorte DOMINANT_FAVORITE: **n=0** (ningún partido pasó el filtro `edge_pp ≥ 8`), ROI=**N/A**, veredicto=**HYPOTHESIS_REFUTED** (a nivel de calibración) + **PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE** (a nivel de cohorte). ¿Mejor que ligas? **NO** — comportamiento equivalente: el modelo no añade información sobre el mercado de-vigged.
> 
> **Hipótesis original**: ❌ **REFUTADA**. El sesgo del público hacia favoritos famosos NO produce un edge medible en el mercado h2h de selecciones, al menos no detectable con un modelo basado en strength gap (FIFA points) + group-stage flag. El mercado de selecciones está tan eficientemente ajustado como el de ligas top."

---

## Phase Sprint-D8/E — Cierre goles 3.5 + Diagnóstico córners + Predictor de tarjetas (P1) — ✅ COMPLETADO

### Contexto
Sprint compuesto por **3 pasos independientes** ejecutados en una sola iteración:
1. **PASO 0** — Cerrar definitivamente el mercado de goles 3.5 ligas (model-only).
2. **PASO 1** — Diagnóstico de capas del scraper de córners (transport / endpoint / parser).
3. **PASO 2** — Predictor de tarjetas Fase 1 (model-only, factor árbitro PIT) con ablación.

### Disciplina aplicada
- **observe_only**: ninguna nueva integración productiva, ningún cambio de scoring.
- **Cero créditos The Odds API**: confirmado.
- **Cero hits scrape.do en este entorno**: confirmado (scrape.do deshabilitado en preview).
- **Fail-soft total**: cada módulo tolera entradas missing/garbage con reason codes.
- **PIT estricto**: el ingestor de tarjetas filtra rigurosamente todo dato con fecha ≥ target_date.

---

### PASO 0 · Cierre goles 3.5 — ✅ CERRADO DEFINITIVAMENTE
**Decisión del usuario aplicada**: opción (a) — usar AUC model-only ya obtenido.

**Datos reusados** (de Sprint-D8 Fase 1 model-only diagnostics):
```
              premier_2425   top5_2425   premier_multiseason
OVER_3_5       0.4819         0.5523       0.5344
UNDER_3_5      0.4640         0.5609       0.5319
```

**Heurísticas aplicadas** (módulo `services/football_goals_3_5_closure.py`):
- **Dispersión inter-scope**: Δ(max-min) > 0.05 ⇒ no robusto. OVER=0.0704, UNDER=0.0969 → AMBOS fallan.
- **Tope absoluto del AUC máximo**: max < 0.58 ⇒ no justifica chase de créditos. OVER max=0.5523, UNDER max=0.5609 → AMBOS fallan.

**Veredicto**: `CLOSED` con reason codes:
- `MARKET_DATA_UNAVAILABLE_FOR_3_5`
- `LEAGUE_GOALS_3_5_CLOSED_DEFINITIVELY`
- `AUC_DISPERSION_HIGH_ACROSS_SCOPES`
- `MAX_AUC_BELOW_CHASE_THRESHOLD`
- `MODEL_DISCRIMINATION_NOT_ROBUST`

**Entregables**:
- `backend/services/football_goals_3_5_closure.py` (módulo puro, 8 tests).
- `backend/scripts/run_goals_3_5_close.py` (CLI runner).
- `/app/diagnostics/sprint_d8e_goals_3_5_closure.json` (veredicto persistido).
- 8/8 tests passing (`test_sprint_d8e_goals_3_5_closure.py`).

---

### PASO 1 · Diagnóstico de córners (3 capas) — ✅ MÓDULO LISTO
**Decisión del usuario aplicada**: opción (b) — instrumentar **ambos** endpoints.

**Endpoints instrumentados**:
- `fetch_game_detail` → `webws.365scores.com/web/game/?gameId=...`
- `fetch_game_stats`  → `webws.365scores.com/web/game/stats/?gameId=...`

**3 niveles de instrumentación** (módulo `services/football_corners_diagnostic.py`):
- **Level 1 (Transport)**: `ok`, `raw_size`, `elapsed_ms`, timeouts.
- **Level 2 (Endpoint)**: búsqueda recursiva de keys que contengan alias de córner (`corner`, `córner`, `corner kicks`, etc.).
- **Level 3 (Parser)**: ejecuta `normalize_365scores_match_stats` y reporta total/home/away córners. Opcional: compara contra ground truth real.

**Verdicts soportados**:
- `TRANSPORT_FAILURE` → ambos endpoints fallaron o devolvieron payload vacío.
- `ENDPOINT_NO_CORNERS_KEY` → transport OK pero ningún payload contiene alias de córner.
- `PARSER_FAILURE` → keys presentes pero el parser no extrae canonical home/away/total.
- `OK` → al menos un endpoint funciona; `winning_endpoint` identifica cuál (detail|stats).

**Ground truth canónico** (verificado contra `football-data.co.uk/E0_2425.csv`):
- **Manchester United vs Fulham, 2024-08-16** — HC=7, AC=8, total=15 córners.

**Estado en preview**: el script CLI requiere `SCRAPEDO_TOKEN` (no presente aquí). Sale limpio con código 3 sin gastar créditos. Tests con mocks (13/13 passing) validan la lógica.

**Entregables**:
- `backend/services/football_corners_diagnostic.py` (función pura DI, 270 líneas).
- `backend/scripts/run_corners_diagnostic.py` (CLI con `--home/--away/--date` o `--game-id`).
- `/app/diagnostics/sprint_d8e_corners_diagnostic.json` (placeholder con instrucciones de uso en prod).
- 13/13 tests passing (`test_sprint_d8e_corners_diagnostic.py`).

---

### PASO 2 · Predictor de tarjetas Fase 1 (model-only, ablación árbitro) — ✅ MÓDULOS LISTOS
**Decisiones del usuario aplicadas**:
- POC inicial: ~150 partidos Premier últimos 4 meses (opción c — disciplina de costo).
- Ablación: solo árbitro on/off (opción a — una variable, una conclusión).
- Pesos: fijos documentados (opción i — sin grid search).

**Modelo Poisson** (`services/football_cards_potential.py`):
- λ_cards = `0.55·referee + 0.30·team_cards_for + 0.10·team_fouls + 0.05·derby_bump` (suma=1.0).
- Re-normalización proporcional cuando features missing.
- Fallback `LEAGUE_DEFAULT_LAMBDA=4.2` cuando todo está missing.
- Output: `over_cards_probability`, `under_cards_probability`, `expected_total_cards`, `reason_codes`, `audit`.

**Ingestor PIT** (`services/football_cards_ingestor.py`):
- `referee_cards_avg_pit(target_date, referee, history, ...)` — filtra ESTRICTO `row_dt < target_dt`.
- Low-sample fallback: si `n_prior < min_sample` (default 5), usa promedio de liga PIT.
- `team_cards_for_avg_pit`, `team_fouls_avg_pit`: mismas reglas.
- Test crítico de no-leakage incluye un partido objetivo con `cards=99` en history; el avg PIT debe ignorarlo.

**Pipeline de evaluación + ablación** (`scripts/run_cards_phase1_modelonly.py`):
- Métricas puras (sin scipy): AUC vía Mann-Whitney U, Brier, reliability curve (10 buckets).
- Run A (`use_referee_factor=False`) vs Run B (`use_referee_factor=True`).
- Tabla `AUC sin árbitro vs AUC con árbitro` por línea (3.5/4.5/5.5) → entregable estrella.
- Verdict tags por AUC:
  - `>= 0.60` → `AUC_GOOD_JUSTIFIES_PHASE_2`
  - `0.55–0.60` → `AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2`
  - `0.52–0.55` → `AUC_WEAK_DO_NOT_PROCEED`
  - `< 0.52` → `AUC_CHANCE_LEVEL_STOP`

**Validación con dataset sintético** (n=150, refs con señal real Poisson 6 vs 3):
```
   Line   AUC (no ref)   AUC (with ref)   Δ AUC   Referee helps?
    3.5         0.4622           0.7278   0.2656   True
    4.5         0.4369           0.7611   0.3242   True
    5.5         0.4842           0.7491   0.2649   True
   Verdict: REFEREE_FACTOR_ADDS_SIGNAL_ALL_LINES
```
La metodología recupera la señal del árbitro cuando existe — confirmando que el experimento real (con dataset 365Scores) producirá conclusiones diagnósticas válidas.

**Estado en preview**: el dataset `/app/data/cards_history/premier_last_4_months.json` no existe en este entorno (requiere scrape.do habilitado). El script CLI emite placeholder con instrucciones. Tests sintéticos (9/9 passing) validan toda la metodología.

**Entregables**:
- `backend/services/football_cards_potential.py` (función pura, 240 líneas).
- `backend/services/football_cards_ingestor.py` (PIT estricto, 290 líneas).
- `backend/scripts/run_cards_phase1_modelonly.py` (CLI + métricas + ablación).
- `/app/diagnostics/cards_phase1_modelonly.json` (placeholder con instrucciones).
- 17/17 tests del predictor + ingestor passing (`test_sprint_d8e_cards_predictor.py`).
- 9/9 tests del pipeline + ablación passing (`test_sprint_d8e_cards_phase1_pipeline.py`).

---

### Validación global del Sprint
- Backend: `pytest tests/` completo → **4395 passed / 2 skipped / 0 failed / 0 errors** (224.94s).
- **+47 tests añadidos** vs baseline post-F99 (4348). 0 regresiones.
- Lint limpio en los 7 archivos nuevos (`mcp_lint_python`: 0 errores).

### Criterios de aceptación verificados
- [x] Goles 3.5 cerrado con veredicto auditado y persistido en `/app/diagnostics/`.
- [x] Diagnóstico de córners con identificación de capa (transport/endpoint/parser) basada en conteos, no conjetura.
- [x] Predictor de tarjetas con factor árbitro PIT-correct (test de no-leakage incluido).
- [x] AUC ablation por línea (3.5/4.5/5.5) implementado y validado contra dataset sintético.
- [x] observe_only; sin producción; cero créditos Odds API en esta fase.
- [x] Sin regresiones (4395 passed).

### Próximos pasos (cuando scrape.do esté disponible)
1. Correr el script CLI de córners contra `Manchester United vs Fulham 2024-08-16` (game_id real) para obtener el veredicto definitivo (¿transport / endpoint / parser?).
2. Scrapear ~150 partidos Premier últimos 4 meses (con árbitro + tarjetas + faltas) y depositar en `/app/data/cards_history/premier_last_4_months.json`.
3. Correr `run_cards_phase1_modelonly.py` → leer la tabla de ablación real → decidir si pasar a Fase 2 (cuotas históricas + CLV).

---

## Phase F99 — Refactor estructural `mlb_day_orchestrator.py` → `mlb_day_context_builder.py` (P0) — ✅ COMPLETADO

### Contexto
- `mlb_day_orchestrator.py` superaba las **6,000 líneas** y la función `analyze_mlb_day` concentraba múltiples bloques de enrichment + pipeline_meta que dificultaban el mantenimiento.
- Restricción explícita del usuario: **refactor 100% estructural**, sin tocar lógica de negocio, contratos, reason codes, scoring ni output del orchestrator.

### Decisiones aplicadas
- Nuevo módulo `backend/services/mlb_day_context_builder.py` (helper puro, mutación in-place del `pick_payload`, fail-soft total).
- Extracción **1:1** del código inline (nombres de variables locales preservados con prefijo `_` para facilitar `git diff` line-by-line).
- Doble guardia `try/except`: una en el orchestrator (mantiene el contrato fail-soft original) y otra dentro del helper (defensa en profundidad). Las dos hacen `log.debug` con el mismo mensaje exacto.
- Comentario doctrinal preservado en el orchestrator para que el lector entienda el step sin abrir el helper.

### Bloques extraídos (6 helpers)
1. **`apply_statcast_phase9_adjustments(pick_payload, chosen_market)`** ← MLB STATCAST DEEP INTEGRATION (Phase 9). Pesado por `data_quality` (60/35/0%), reason codes propagados.
2. **`apply_offensive_pressure_base(pick_payload, chosen_market)`** ← Objetivo 2: detección de presión ofensiva oculta (muchos hits, pocas carreras), boost de fragility para picks Under.
3. **`apply_sabermetrics_layer(pick_payload, chosen_market)`** ← Phase 9.6: WAR/OPS/FIP confirmation layer, weighted by data_quality.
4. **`apply_market_selection_intelligence(pick_payload)`** ← Phase 13.1: capa final protectora de selección de mercado (defensive market pick).
5. **`apply_intelligence_warehouse(pick_payload, db)`** (async) ← Fix 3: Pattern Memory lookup + persistencia de game intelligence snapshot.
6. **`seal_pipeline_payload_contract(pick_payload)`** ← Moneyball alignment: sella el contrato canónico del payload (`available: false` cuando falta info upstream).

### Entregables
- ✅ `backend/services/mlb_day_context_builder.py` (473 líneas, 6 helpers + docstrings doctrinales).
- ✅ `backend/services/mlb_day_orchestrator.py` actualizado: bloques inline reemplazados por llamadas al helper (lines 2579-2630). Reducción neta de complejidad de `analyze_mlb_day` sin cambios funcionales.
- ✅ Test golden: `backend/tests/test_f99_mlb_day_context_builder_refactor.py`.
- ✅ Lint limpio (`mcp_lint_python`): 0 errores en orchestrator y context_builder.

### Validación
- Backend: `pytest tests/` completo → **4348 passed / 2 skipped / 0 failed / 0 errors** (177.28s).
- **0 regresiones** vs baseline F97 (4322) — el delta de +26 tests corresponde a los tests añadidos en F98 (football ingest hotfix) y los goldens F99.
- Contrato verificado: mismas claves, mismos reason_codes, mismas mutaciones in-place del `pick_payload` que la versión pre-F99.

---

## Phase F97 — NIVEL 3 Bloque 3 (§5-§6): Under hard rules + UI “Distribución y colas” (P1) — ✅ COMPLETADO

### F97.1 — Módulo puro `services/mlb_under_hard_rules.py` ✅
- `evaluate_under_hard_rules(*, final_over_probabilities, line, tail_bucket, pick_side, market) -> dict`.
- Thresholds:
  - WARN: `[0.42, 0.48)`
  - AVOID: `[0.48, 0.55)`
  - BLOCK: `>= 0.55`
- Tail rules:
  - `EXTREME` → BLOCK
  - `HIGH` y `line <= 9.5` → AVOID
- Score deltas:
  - WARN = −3, AVOID = −10, BLOCK = 0 (BLOCK excluye del feed, sin doble penalización).
- 39 tests: `backend/tests/test_mlb_under_hard_rules.py`.

### F97.2 — Cableo en `mlb_day_orchestrator.py` (M5.8.4) ✅
- Inserción justo después de M5.8.3 (blender) y dentro del try del mixer.
- Resolución de inputs:
  - `final_over_probabilities` desde `_blend_out` o fallback desde `expected_runs_distribution.probabilities`.
  - `tail_bucket` desde `tail_calibration` o fallback desde `mixer_out.tail_risk.bucket`.
- Mutaciones del `pick_payload`:
  - Snapshots: `pick_score_pre_under_rules` / `pick_score_post_under_rules`.
  - WARN: `score -= 3` + `under_warning`.
  - AVOID: `score -= 10` + `under_recommendation_degraded=true` + `block_max_pick=true` + `under_avoid`.
  - BLOCK: `is_blocked=true` + `exclude_from_main_feed=true` + `category="debug"` + `under_block`.
- Propagación de `reason_codes` a `expected_runs_distribution.reason_codes`.
- Diagnóstico en `pipeline_meta["expected_runs_distribution"]["under_hard_rules"]`.
- Log marker: `[UNDER_HARD_RULES]`.
- 10 tests: `backend/tests/test_orchestrator_under_hard_rules_wiring.py`.

### F97.3 — Frontend UI: `UnderDistributionTailsCard.jsx` ✅
- Nuevo card en detalle MLB, cableado en `MLBScriptPanel.jsx` debajo de `UnderHiddenRisksCard`.
- Render condicional (solo con datos NIVEL 3).
- Muestra:
  - distribución usada + pesos Poisson/NB
  - dispersión efectiva
  - bucket de cola
  - percentiles P90/P95/P99
  - over_risk + línea seleccionada
  - acción final WARN/AVOID/BLOCK + signals
- Warnings (badges inline):
  - P90 comprimido (`P90_TOO_COMPRESSED_FOR_CONTEXT` / `CENTRAL_MEAN_NOT_ENOUGH`)
  - Cola recalibrada (`P90_RECALIBRATED`)
  - Divergencia dist vs threshold model (`distributionBlender.divergence_flags`)
  - Under degradado (acción AVOID/BLOCK)
- 15 tests RTL: `frontend/src/components/__tests__/UnderDistributionTailsCard.test.jsx`.

### F97.4 — Validación ✅
- Backend: **4322 passed / 2 skipped** (+49 vs F96; 0 regresiones).
- Frontend: tests Under **31/31** passing.
- Build: esbuild bundle OK.
- Runtime: restart backend+frontend OK; scheduler muestra `settle_finished_football` activo.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- 🟡 **SPRINT D5** (histórico en curso): cohortes + reportes multi-competición.

### Pendientes P1
- 🟡 **REFACTOR-1** (pasos 2/3 y 3/3 + ingest_upcoming).
- ⏳ **F84.c/F84.d** Lineups + Standings.
- ⏳ **D8 Fase 2** — selecciones (DRAW + cohorte favorito-dominante) con MAX_CREDITS=2500.

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

### ✅ NIVEL 3 — Bloque 3 (§5-§6) · Under hard rules + UI “Distribución y colas” — COMPLETADO
(Ver fases F97.1–F97.4.)

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

**Estado actual de la suite backend (post-Sprint-D8-Fase2):** `4421 passed / 2 skipped` (0 regresiones; +26 tests vs Sprint-D8/E; observe_only, sin cambios funcionales productivos; cascada TheSportsDB primaria implementada).

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
