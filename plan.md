# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks + Over Discovery** + **MLB-V7 Explainability/Game Script/Diversificación** + **MLB Under Confidence Floor (P0)** + **F6C Auto-Settle (P1)** (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (`code/label/severity/explanation`) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Hardening de pipeline: evitar bloqueos en `stage=enriching` con timeouts + degradación elegante.

- **(✅ COMPLETADO)** Robustez multi-deporte en LIVE:
  - Detectar correctamente partidos LIVE en **basketball/baseball**.
  - Evitar “zombies LIVE” en fútbol.
  - Firewall de vocabulario para impedir **fugas de terminología**.

- **(✅ COMPLETADO)** Enriquecimiento histórico fútbol (últimos 15): mejorar explicabilidad y señales para rescate Under.

- **(✅ COMPLETADO)** **P3 — Editorial Context Engine (Scrapy)**:
  - Capa opcional y **fail-soft** de enriquecimiento editorial profundo **solo para fútbol** y **solo para matches shortlisteados**.
  - Separación **dato vs opinión** (heurístico regex) + interpretación Moneyball.
  - UI: bloque “Contexto editorial”.

- **(✅ COMPLETADO / VALIDADO EN VIVO)** Tuning de selectores + fuentes nuevas:
  - **AS.com** y **Marca** server-rendered.
  - Añadido filtrado fino para Marca (evitar `mercado-fichajes` / `-directo.html`).
  - Spider con dedupe por URL y soporte de exclusión por patrón.

- **(✅ COMPLETADO)** **P4 — Playwright** para fuentes JS-heavy:
  - Subprocess + stealth + dispatch paralelo Scrapy/Playwright.
  - Detecta challenges anti-bot y degrada sin romper análisis.

- **(🟨 PENDIENTE / BLOQUEADO)** **Bright Data Web Unlocker** como tercer backend:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball y MLB**.
  - **Bloqueo actual:** faltan credenciales del usuario (`BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE`).

- **(✅ COMPLETADO)** **Historical Detail Enrichment (Baseball)**:
  - Antes de analizar/descartar MLB, enriquecer con histórico profundo (últimos 15) y generar perfiles por equipo + combinado.
  - Añadir `baseballRunsRescueLayer(match)` y **trap signals históricas**.

- **(🟨 PENDIENTE)** **Historical Detail Enrichment (Basketball)**:
  - Antes de analizar/descartar basketball, enriquecer con histórico profundo y generar perfiles.
  - Añadir `basketballTotalPointsRescueLayer(match)`.
  - UI: sección “Historial profundo”.

- **(✅ COMPLETADO)** **MLB Margin & Total Script Engine v2 (solo Baseball)**:
  - Engine especializado en guion MLB:
    - Predecir **margen de victoria** (Run Line -1.5 favoritos dominantes)
    - Seleccionar líneas **Over/Under más protegidas** (6.5/7.5/8/8.5/9 y equivalentes)
    - Análisis **pitcher-first** con gate de pitchers confirmados
    - Parlays **MLB-only** con validación de correlación positiva
  - Restricción crítica: **NO tocar basketball/football** (backend y UI).

- **(✅ COMPLETADO)** **MLB Feedback Loop (P2)**:
  - Guardar outcomes reales por pick: `result/outcome`, `margin`, `totalRuns`, `runLineCovered`, `overHit`.
  - Guardar snapshot v2: `expectedRuns`, `marginProjection`, `coverProbability`, `lineSelected`.
  - Recalibración automática cada 50 picks settled → persiste pesos en DB.

- **(✅ COMPLETADO)** **MLB-V5 — Bucketing estructural MLB + Manual Odds Review**:
  - Baseball NO usa el LLM genérico en `/api/analysis/run`.
  - Nuevos buckets MLB:
    - `structural_lean_requires_odds`
    - `watchlist_manual_odds`
    - `discarded_after_full_analysis`
  - UI: sección **“Revisión manual — falta cuota”** (solo MLB) vía `ManualOddsReviewPanel.jsx`.

- **(✅ COMPLETADO)** **MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery / Market Audit (V6 UI + Backend)**:
  - Fix Totals (Poisson) + UI Edge vs Línea + picks visibles.
  - **Over Discovery Engine (V6)** para eliminar sesgo hacia Unders:
    - Offensive Explosion Score (0–100)
    - Offensive Script badge
    - Over Survival score
    - Market competition Under vs Over + swap cuando Over domina
    - Daily Market Audit endpoint

- **(✅ COMPLETADO)** **MLB-V4 Live Intelligence**:
  - Volatility detection + script breaks monitoring + cashout intelligence.
  - Restricción: solo aplica a matches que pasaron el filtro pregame.

- **(✅ COMPLETADO)** **F6A/F6B Bullpen Risk & Storage**:
  - Downgrade Full Game Unders a F5 Under si bullpens son riesgosos.
  - Storage post-match de script breaks.

- **(✅ COMPLETADO)** **MLB-V5 Script Survival & Fragility**:
  - Survival score 0–100 + fragility score 0–100 con clasificación de estabilidad.
  - UI: summary + detail panels.

- **(✅ COMPLETADO — NUEVO P0)** **MLB Under Confidence Floor (Moneyball guardrail)**:
  - Problema: picks MLB Under con `confidence_score` en el rango 50–74 podían pasar como recomendación activa cuando hay odds y edge positivo.
  - Solución: en `services/moneyball_layer.py::analyze_pick`, **pre-guardia específica sport+market**:
    - Solo para `sport == "baseball"`, **market Under (no team total, no NRFI)**.
    - Solo cuando `edge is not None` (hay odds → edge calculable).
    - Si `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75, env-tunable) → degrada a `WATCHLIST`.
    - Marca el pick con `pick["_conf_floor_demoted"] = True`.

- **(✅ COMPLETADO — NUEVO)** **UI/summary: bucket de democión por floor**:
  - `server.py` expone `summary.conf_floor_demoted` con picks del bucket `watchlist_manual_odds` que incluyen `_conf_floor_demoted=True`.

- **(✅ COMPLETADO — NUEVO P1)** **F6C Auto-Settle MLB (sin intervención del usuario)**:
  - Nuevo módulo `services/mlb_results_settler.py`:
    - `_resolve_result()` para mercados determinísticos con final-score (Over/Under full-game, team totals).
    - `auto_settle_pending_evaluations()` barre `mlb_run_evaluations` pending, busca `matches.final_score` y llama `update_run_evaluation_result`.
  - Wiring APScheduler en `services/scheduler.py`:
    - Job `_job_auto_settle_mlb_evaluations` cada **20 min**, offset respecto a `settle_finished_baseball` (15 min).
  - Cierra el loop F6C automáticamente para `mlb_run_evaluations` cuando existe `final_score`.

---

## 2) Implementation Steps

### Phase 1 — Core POC (aislado) para el flujo “tolerancia + decisión contextual + señales trampa”
**Estado:** ✅ COMPLETADO

---

### Phase 2 — V1 App Development (backend + wiring de rescate)
**Estado:** ✅ COMPLETADO

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

---

### Phase 4 — P0 LIVE Hardening + P2 Historical Profile (fútbol)
**Estado:** ✅ COMPLETADO

---

### Phase 5 — P3 Editorial Context Engine (Scrapy) — MVP
**Estado:** ✅ COMPLETADO

---

### Phase 6 — P3 Selector Tuning + New Sources (AS.com, Marca) + limpieza de falsos positivos
**Estado:** ✅ COMPLETADO (validación real 2026-05-28)

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy)
**Estado:** ✅ COMPLETADO (infra lista; desbloqueo real requiere unlocking)

---

## Phase G3 — Critical Bug Fixes (Defense-in-Depth Time Filter + Pitcher Quality Rewrite)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G2 — Baseball Savant + Parlay Correlation Validator
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G1 — MLB Pre-game Analytics Engine
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase G4 — Multi-Source Sports Scrapers Wiring (MLB + Basketball)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V2 — MLB Margin & Total Script Engine v2
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V3 — Baseball Historical Detail Enrichment + baseballRunsRescueLayer
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V4 — Live Intelligence (Volatilidad / Script Breaks / Cashout) + endpoint reevaluate
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V5 — Script Survival + Fragility + UI Panels
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-F6A/F6B — Bullpen Risk Selector + Storage Script Breaks
**Estado:** ✅ COMPLETADO (2026-05-30)

---

## Phase MLB-V6 — Totals Probability Fix + Visible Picks + Over Discovery Engine + Daily Market Audit
**Estado:** ✅ COMPLETADO (2026-05-30)

(Se mantiene la documentación V6.1–V6.5 sin cambios.)

---

## Phase MLB-M2 — Bullpen Real-Usage (pitch_stress) + Finished-Game Settler
**Estado:** ✅ COMPLETADO (2026-05-31)

(Se mantiene la documentación M2.1–M2.5 sin cambios.)

---

## Phase GAPS-4 — LiveMarketStateValidator + LivePreMatchComparisonLayer + Under-Loss Anti-Pattern Library
**Estado:** ✅ COMPLETADO (2026-05-31)

---

## Phase GAPS-5 — Under Veto Power-Bat + Bullpen Pitch-Stress + Learning Cases MLB
**Estado:** ✅ COMPLETADO (2026-05-31)

---

## Phase RECAL — Lightweight Recalibration + Feedback APScheduler + Bright Data Health
**Estado:** ✅ COMPLETADO (2026-05-31)

### RECAL.3 Feedback-loop recalibración automática (P2)
- **Estado:** ✅ COMPLETADO
- `FEEDBACK_BATCH_SIZE: 50 → 40` en `services/mlb_feedback_loop.py`.
- Job APScheduler `recompute_feedback_weights` (cada 30 min) en `services/scheduler.py`.

### RECAL.5 (NUEVO) F6C Auto-Settle de evaluaciones pending
- **Estado:** ✅ COMPLETADO (2026-06-02)
- `services/mlb_results_settler.py`:
  - `_resolve_result()` (Over/Under full-game + team totals; skip determinístico para F5/NRFI/inning).
  - `auto_settle_pending_evaluations()`.
- `services/scheduler.py`:
  - Job `_job_auto_settle_mlb_evaluations` cada 20 min.

---

## Phase MLB-FIX2 + MLB-TS1 (Batch 3) — Bugfix MLB lookup + Batch 3 enrichment/sources/UI
**Estado:** ✅ COMPLETADO (2026-06-03)

### FIX 2a — `mlb_live_state.py` boxscore fetch
- Añadido `_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"`.
- `_extract_box_score(payload)`: convierte el shape MLB Stats API a la forma plana que `live_pre_match_comparison.py` ya leía:
  `{hits, walks, home_runs, errors, strikeouts, pitches_home, pitches_away, at_bats, left_on_base}` (cada uno como `{home, away}` cuando aplica).
  - Drop graceful de bloques fully-None.
  - Coerce strings → int (MLB API ocasionalmente envía counters como strings).
- `fetch_live_state` ahora ejecuta los 3 endpoints en paralelo (`linescore` + `schedule` + `boxscore`) vía `asyncio.gather(..., return_exceptions=True)` — el boxscore que falle no rompe el snap.
- `fetch_and_persist_live_state` persiste `live_stats.box_score` con el shape correcto.

### FIX 2b — `server.py` pregame pick lookup robusto
- En el bloque `script_comparison` de `match_detail`, la comparación previa
  `str(p.get("match_id")) == str(doc.get("match_id"))` fallaba cuando los
  picks usan `game_pk` (MLB Stats API) y el doc usa otro identificador.
- Nuevo helper inline construye dos sets de candidates por doc y por pick (incluyendo `match_id`, `game_pk`, `live_stats.game_pk`, int/str variants) y matchea por intersección.
- Adicionalmente, propaga `live.game_pk` → `doc.game_pk` cuando el live snapshot lo trae, para que la búsqueda sea idempotente sin requerir re-fetch.

### MLB-TS1 Batch 3.1 — Pre-match enrichment via TheStatsAPI
- `thestatsapi_client.py`: 3 endpoints nuevos:
  - `fetch_match_details(client, id, sport)` — sport-aware (`/football/matches`, `/basketball/matches`, `/baseball/games`).
  - `fetch_team_stats(client, team_id, sport, season, competition_id)`.
  - `fetch_player_stats(client, player_id, sport, season)`.
- `services/external_sources/thestatsapi_enrichment.py` (nuevo):
  - `enrich_pre_match(client, db, sport, match_raw_id, home_team_id, away_team_id, season, competition_id)`:
    - 3 calls en paralelo (`match_details` + `home_team_stats` + `away_team_stats`).
    - Cache integrado (`_CACHE_TEAM_STATS=6h`, `_CACHE_MATCH_DETAILS=2h`, `_CACHE_PLAYER_STATS=6h`).
    - Top-N (cap 5) player_stats per side si hay lineup en `match_details`.
    - Devuelve `{}` si nada útil llegó (consumers usan `if match_doc.get("_thestatsapi_enrichment")`).
- Cableado en `data_ingestion._enrich_football`: cuando el fixture tiene `_thestatsapi_raw_id` AND no es live → llama `enrich_pre_match()` y guarda en `match_doc._thestatsapi_enrichment`.

### MLB-TS1 Batch 3.2 — Sources consulted en summary
- En `server.py` antes del `insert_one`, copia `pipeline_meta.external_sources_consulted` → `result.summary.external_sources_consulted` + `external_sources_labels` (flat list ordenada).

### MLB-TS1 Batch 3.3 — UI: botón "Analizar en vivo"
- En `DashboardPage.jsx`, nuevo handler `analyzeLive()` que llama `POST /api/analysis/run` con `live_only: true`.
- Botón rose-tinted con icono `Activity` (todos los deportes). `data-testid="analyze-live-button"`. Tooltip i18n.
- Suite de 3 botones de fútbol completa: "Refrescar partidos" (cyan) · "Selecciones nacionales" (amber) · "Analizar en vivo" (rose).

### MLB-TS1 Batch 3.4 — UI: Empty state listando fuentes consultadas
- `EmptyStateNoValue.jsx` ahora lee `summary.external_sources_consulted` + `external_sources_labels`.
- Renderiza un bloque "FUENTES CONSULTADAS" con chips cyan, cada uno con `data-testid="empty-state-source-chip-{i}"`.
- Mensaje explicativo: "Cruzamos estas fuentes; ningún partido superó el listón del analista hoy."

### Validación
- **508 tests PASS** (497 previos + 14 nuevos MLB-FIX2 + 11 Batch 3 = 25 nuevos).
  - `tests/test_mlb_live_boxscore_and_pregame_lookup.py` — 14 tests (boxscore parsing, fetch_live_state paralelo, pregame lookup robustness con baseball vs football).
  - `tests/test_thestatsapi_batch3_enrichment.py` — 11 tests (client endpoints, enrich_pre_match aggregator, cache hit, partial failure, sources propagation).
- ESLint + esbuild limpio en `MatchCard.jsx`, `EmptyStateNoValue.jsx`, `DashboardPage.jsx`.
- Backend reiniciado limpio.
- **Validación en UI live (screenshot)**:
  - 3 botones visibles con `data-testid` correctos.
  - Empty state renderizando engine reasoning + sources block (cuando hay datos).

### Despliegue
Cambios en **PREVIEW**. Producción requiere redeploy del usuario.

---


## Phase MLB-TS1 (Batch 2) — National-team detector + stats enrichment + multi-source badge
**Estado:** ✅ COMPLETADO (2026-06-03)

### Decisiones de diseño (alineadas con usuario)
- **Detector nat-team**: lista FIFA completa (~210 países) + keywords de competición (EN+ES) + alias ES↔EN (~40 países frecuentes).
- **xG/stats enrichment**: sólo cuando el fixture tiene `_thestatsapi_raw_id` Y stats de API-Sports vacíos. Merge: primary gana en valores no-nulos; secondary llena los huecos.
- **Tests**: 42 nuevos, todos con `httpx.MockTransport`. Caso real "Bélgica vs Croacia" validado.

### MLB-TS1.8 `services/external_sources/national_team_detector.py` (nuevo)
- `FIFA_NATIONAL_TEAMS` (~210 países, lowercase canonical en EN).
- `COUNTRY_ALIASES` (~70 entradas ES/PT/EN variantes → canonical EN).
- `INTERNATIONAL_COMP_KEYWORDS` (EN+ES: World Cup, Euro, Nations League, Copa America, Gold Cup, AFCON, Asian Cup, Friendly, Qualifying, Eliminatorias, Amistoso, Selección, etc.).
- `INTERNATIONAL_REGIONS` ({World, International, Europe, South America, North America, Africa, Asia, Oceania}).
- `normalize_country_name()` — accent-strip + lowercase + alias resolution.
- `is_national_team_name(name)` — checks normalized form against FIFA set.
- `is_international_competition(name, country)` — substring keywords + region check.
- `is_national_team_match(home, away, league_name, league_country)` — true si comp es internacional **o** ambos teams son países (precision-focused).
- `country_canonical(name)` — devuelve EN canonical si es FIFA nation, sino None (para dedupe).

### MLB-TS1.9 Auto-detección en `thestatsapi_normalizer.normalize_match`
- Tras inferir `is_national`/`is_intl` desde flags explícitos del payload, fallback al `ntd.is_national_team_match()` para fixtures de `/football/matches` que no traen `is_national_team`.
- Resultado: "Belgium vs Croatia" en "UEFA Nations League" → `_is_national_team=True, _is_international=True`.

### MLB-TS1.10 Aggregator: country-aware dedupe (Bélgica ↔ Belgium)
- `_normalize_team()` ahora intenta primero `ntd.country_canonical()` antes del normalizador genérico de clubes.
- Resultado: si API-Sports devuelve "Bélgica vs Croacia" y TheStatsAPI "Belgium vs Croatia", ambos colapsan a `(belgium, croatia)` y el dedupe los unifica.
- Merge propaga `_thestatsapi_raw_id` al primary (campo dedicado, NO sobrescribe `_external_source_id` del primary) para que `_enrich_football` lo pueda usar luego.

### MLB-TS1.11 Allowlist extendido en `data_ingestion.ingest_live`
- Nuevo paso 3 tras checks 1 (club tier) y 2 (API-Sports nat-team league_id):
  - Si `_is_national_team=True` (del aggregator) o `ntd.is_national_team_match()` devuelve true → asigna synthetic Tier-2 priority 72 con flag `_detector_source: "national_team_detector"`.
- Log diferencia entre `api_sports_nat_team` (paso 2) y `thestatsapi/detector_nat_team` (paso 3).
- **Resultado en vivo (validado)**: "Côte d'Ivoire U20 vs Venezuela U20" (Tournoi Maurice Revello, country=World) ahora atraviesa el filtro vía el paso 3 (antes el league_id no estaba en `NATIONAL_TEAM_LEAGUES`).

### MLB-TS1.12 Match-stats normalizer (xG / shots / possession)
- `thestatsapi_normalizer.normalize_match_stats()`:
  - Soporta 3 layouts: flat (`home`/`away`), team-keyed (`home_team_stats`), data-wrapped.
  - `_STAT_FIELD_MAP` mapea 24 nombres de stats TheStatsAPI → labels API-Sports (`xg` → `expected_goals`, `shots_total` → `Total Shots`, `possession` → `Ball Possession`, etc.).
  - `_format_stat_value()` convierte possession a string `"55%"` (API-Sports compat), acepta tanto 0..1 como 0..100.
  - Devuelve None si no hay stats útiles (callers preservan su payload original).
- `merge_live_stats(primary, secondary)`: primary gana en valores no-nulos, secondary llena huecos. Anota `_sources` con la lista combinada.

### MLB-TS1.13 Cableado en `_enrich_football`
- Tras el fallback de `af.fixture_statistics`, si el fixture tiene `_thestatsapi_raw_id`, está live, y los stats siguen vacíos:
  - Llama `ts_client.fetch_match_stats(client, ts_raw_id)`.
  - Normaliza con `ts_norm.normalize_match_stats()`.
  - Mergea con `merge_live_stats()`.
- Fail-soft: cualquier excepción no rompe la enrichment.
- Log: `[thestatsapi_stats] enriched fixture {fid} with xG/shots from TheStatsAPI`.

### MLB-TS1.14 UI: badge "Multi-fuente" (Fuentes: API-Sports + TheStatsAPI)
- Cyan-tinted pill en `MatchCard.jsx` cuando `external_sources_covered.length >= 2`.
- Tooltip muestra la lista completa de fuentes ("Fuentes: API-Sports + TheStatsAPI").
- `data-testid="pick-multisource-badge-{match_id}"`.

### Validación
- **483 tests PASS** (441 previos + 42 nuevos en `test_thestatsapi_batch2_nat_teams.py`).
- Cobertura:
  - `normalize_country_name` (accents, lowercase, aliases).
  - `is_national_team_name` (Bélgica/Belgium, Croacia/Croatia, Argentina, São Tomé, rejects clubs).
  - `is_international_competition` (15 casos parametrizados EN+ES + region check).
  - `is_national_team_match` combined decision (4 escenarios: ambos países, comp intl, clubs en non-intl, friendly-name collision).
  - Normalizer auto-flagging (Belgium vs Croatia en Nations League).
  - Aggregator dedupe (`Bélgica` vs `Belgium`, `Alemania` vs `Germany`, no-collapse clubs).
  - Match-stats normalizer (3 layouts, unknown fields skipped, empty returns None).
  - `merge_live_stats` (primary wins on overlap, None handling, sources marker).
  - Client `fetch_match_stats` (data wrapper + 404).
- ESLint + esbuild limpio en `MatchCard.jsx`.
- Backend reiniciado limpio.
- **Validación en vivo con API real**:
  - Aggregator merged 24 fixtures con `dropped_dupes=1` (Gibraltar vs British Virgin Islands).
  - `_thestatsapi_raw_id=mt_377873051` correctamente grafted al primary.
  - "Côte d'Ivoire U20 vs Venezuela U20" detectado como nat-team vía detector keywords (country=World).

### Despliegue
Cambios en **PREVIEW**. Para producción se requiere **redeploy** del usuario.

---


## Phase MLB-TS1 — TheStatsAPI Integration (Football national teams + internacionales)
**Estado:** ✅ COMPLETADO (2026-06-03)

### Decisiones de diseño (alineadas con usuario)
- TheStatsAPI actúa como **provider aditivo + fallback** para fútbol. No reemplaza API-Sports.
- API key en `/app/backend/.env` (`THESTATSAPI_KEY`, `THESTATSAPI_BASE_URL`, `ENABLE_THE_STATS_API=true`).
- Fail-soft total: cualquier 4xx/5xx/timeout/llave faltante → orchestrator sigue con API-Sports.
- Tests usan `httpx.MockTransport` — CI no llama nunca a la API real.

### MLB-TS1.1 `services/external_sources/thestatsapi_client.py` (nuevo)
- `httpx.AsyncClient` con rate-limit interno (60 req/min default vía `THESTATSAPI_RATE_LIMIT`).
- Retry idempotente (1) en 429/5xx con backoff (0.6s × attempt). Sin retry en 4xx no-auth.
- `is_enabled()` requiere flag ON **y** key presente.
- 4 endpoints públicos: `fetch_competitions`, `fetch_live_matches`, `fetch_fixtures`, `fetch_match_stats`.
- `health_check()` para el endpoint de diagnóstico.
- Helper `_extract_list()` para parsear los 4 wrappers conocidos (`matches`, `data`, `response`, `results`).

### MLB-TS1.2 `services/external_sources/thestatsapi_normalizer.py` (nuevo)
- Convierte payloads TheStatsAPI → shape API-Sports (`fixture/league/teams/goals`).
- IDs string (`mt_370102627`, `tm_28025`, `comp_6107`) → namespaced int (`900_000_000 + N`):
  - Prefijo alpha stripeado y parseado.
  - Strings sin dígitos → hash determinístico blake2b 4-byte (idempotente para dedupe).
  - Sin colisión con IDs de API-Sports (rango 1..~1.5M).
- Status map: 14 variantes TheStatsAPI → códigos cortos API-Sports (`NS/1H/HT/2H/FT/ET/P`).
- Soporta `utc_date`, `utcDate`, `date`, `kickoff`, unix int/float (ms o s).
- `build_competitions_index(raw_list)` → `{raw_id: meta}` para enriquecer matches que sólo traen `competition_id`.
- Drop graceful de payloads malformados (sin teams, sin id, sin kickoff).

### MLB-TS1.3 `services/external_sources/thestatsapi_cache.py` (nuevo)
- Colección `external_source_cache` con clave `(source, endpoint, key)`.
- TTLs configurados (per spec usuario): `competitions=24h`, `live_matches=40s`, `fixtures=5min`, `match_stats=3min`.
- `cache_get/set/clear` totalmente fail-soft (db=None ok, write fail ok).

### MLB-TS1.4 `services/football_live_aggregator.py` (nuevo)
- `fetch_live_football_fixtures(client, db)`:
  - Lanza en paralelo API-Sports + TheStatsAPI (`asyncio.gather`).
  - Si TheStatsAPI deshabilitado o falla → behavior idéntico al baseline `af.fixtures_live(client)`.
  - Si API-Sports falla → retorna sólo los de TheStatsAPI (mejor que nada).
- `merge_and_deduplicate(primary, secondary)`:
  - Dedupe key: `(normalized_home, normalized_away)` + ventana de 60min en kickoff_ts.
  - Normalización de nombres: strip accents + remove suffixes (FC, CF, AC, SC, etc.) + lowercase.
  - Primary gana; al detectar duplicado, mergea `_external_sources_covered` (`["api_sports", "thestatsapi"]`).
- Precarga competitions index en paralelo a live_matches (1 round-trip) para enriquecer league.name + is_international.

### MLB-TS1.5 Cableado en pipeline
- `data_ingestion.ingest_live(sport="football")` → llama al aggregator en lugar de `af.fixtures_live` directo.
- `_enrich_football` ahora propaga `external_source`, `external_sources_covered`, `is_national_team`, `is_international` al `match_doc`.
- `services/provenance.py::SOURCE_LABELS` añade `thestatsapi → "TheStatsAPI"`.
- `server.py` propaga estos campos en el bucle de candidates→picks (junto a `_provenance` y `_football_quality`).

### MLB-TS1.6 Endpoint de diagnóstico
- `GET /api/debug/thestatsapi/health?probe=true` (autenticado):
  - `enabled`, `key_present`, `base_url`, `now`.
  - Si `probe=true` y enabled → `reachable`, `competitions_count`.
  - Nunca devuelve la API key.
- **Validado en vivo**: response `{"enabled": true, "reachable": true, "competitions_count": 20}`.

### MLB-TS1.7 UI badges en `MatchCard.jsx`
- Badge violeta **"TheStatsAPI"** (sólo fútbol) cuando `external_source === "thestatsapi"` o lista `external_sources_covered` lo incluye.
- Badge ámbar **"Selecciones"** (sólo fútbol) cuando `is_national_team` o `is_international`.
- Tooltips i18n EN/ES.
- `data-testid="pick-thestatsapi-badge-{match_id}"`, `data-testid="pick-national-team-badge-{match_id}"`.

### Validación
- **441 tests PASS** (398 previos + 43 nuevos en `test_thestatsapi_integration.py`).
- Cobertura:
  - Client env flags (enabled/disabled/missing key).
  - `fetch_competitions` parsing de 2 wrappers + 404 + 500 con retry + 401 sin retry + disabled short-circuit.
  - `fetch_live_matches` con params.
  - `health_check` enabled/disabled.
  - Normalizer 11 casos: shape completo, missing team/id/kickoff, unix timestamps, bulk skip invalid, `_ns_id` (int/str/prefixed/non-numeric/empty), real string-ID payload, competitions_index enrichment.
  - Cache 5 casos: fresh, stale (TTL), unknown endpoint, db=None fail-soft, endpoint-scoped clear.
  - Aggregator 11 casos: dedupe (close ts + accents/suffixes), distinct matches, far kickoffs, empty inputs, only secondary, disabled skip, secondary failure, primary failure, both merge.
- ESLint + esbuild limpio en `MatchCard.jsx`.
- Backend reinició limpio (APScheduler con todos los jobs).
- **Validación en vivo (API real, no mock)**:
  - `competitions_count: 20`.
  - `fetch_live_football_fixtures` → primary=26, secondary=2, **dropped_dupes=1**, **secondary_added=1** (Renaissance Zemamra vs Union Sportive Yacoub El Mansour — partido marroquí que API-Sports no devolvía).

### Despliegue
Cambios en **PREVIEW**. Para `https://low-volatility-plays.emergent.host` se necesita **redeploy** del usuario.

### Pendiente para próximas iteraciones
- **Batch B (P1)**: `mlb_statcast_adapter` (Bright Data + pybaseball fallback) para xERA/xwOBA/barrel_pct.
- **Batch C (P2)**: `football_territorial_intelligence` (xT proxy).
- **TheStatsAPI extensiones**: integrar `fetch_match_stats` en `_enrich_football` (xG, posesión, shots) para nat-teams donde API-Sports no devuelve stats.

---


## Phase MLB-FP6 — Batch A: Odds Value Engine + Ghost-Edge L5/L15
**Estado:** ✅ COMPLETADO (2026-06-03)

### Auditoría previa
- `moneyball_layer.py` ya tenía `implied_probability` + `compute_expected_value` + `classify_pick`.
- `market_guardrail.py` ya tenía `evaluate_pick` + `estimated_probability_from_confidence`.
- `mlb_script_conflict.py` ya tenía `parse_manual_odds` + `calculate_manual_edge`.
- **Faltaba**: consolidar todo en un value-layer puro + agregar `detect_line_movement` + `compare_bookmaker_odds` + `evaluate_market` con `market_status`.

### MLB-FP6.1 `services/odds_value_engine.py` (nuevo)
- `normalize_decimal_odds()` — acepta decimal (`1.85`/`1,85`), American (`-110`/`+150`), fractional (`9/4`).
- `parse_midpoint_odds()` — extrae midpoint de rangos `"1.80-1.95"` o `"1,80 / 1,95"`.
- `implied_probability()` — 1/odds.
- `calculate_edge()` — verdict `VALUE`/`FAIR_VALUE`/`NO_VALUE`/`UNKNOWN` con threshold ±3%.
- `calculate_expected_value()` — EV unit-stake + ROI%.
- `detect_line_movement()` — line+odds deltas, `direction` (toward_over/under/favorite/underdog/stable), `steam_detected` heurístico.
- `compare_bookmaker_odds()` — pick best price + spread% + avg/median.
- `evaluate_market()` — payload canónico unificado con `market_status`: `priced` / `manual_odds_required` / `no_odds`.

### MLB-FP6.2 Ghost-edge L5/L15 en `mlb_real_stats_verifier.py`
- Nuevos kwargs: `recent_run_split`, `on_base_profile`, `f5_split`.
- 4 nuevos checks de ghost-edge:
  - `GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING` — L5 ≥ ER+2.5 con pick Under → +18 penalty.
  - `GHOST_EDGE_OVER_VS_L5_LOW_SCORING` — L5 ≤ ER−2.5 con pick Over → +14 penalty.
  - `RECENT_RUN_TREND_CONTRADICTS_UNDER/OVER` — L5 vs L15 contradice el pick por ≥2.0 → +8.
  - `GHOST_EDGE_F5_UNDER_VS_L5` — F5 markets con divergencia ≥1.2 runs → +12.
  - `GHOST_EDGE_RISING_ON_BASE_VS_UNDER` — TOB Δ ≥ +2.5 vs Under → +10.
- Penalty cap subido de 35 a 45 (más checks).

### MLB-FP6.3 Cableado en orchestrator
- `verify_model_inputs()` ahora recibe `recent_run_split`/`on_base_profile`/`f5_split` desde el `pick_payload`.
- Tras `moneyball_layer.analyze_pick`, también se attacha `_odds_value` (de `evaluate_market`) en cada pick — additive, no rompe la clasificación moneyball.

### Validación
- **398 tests PASS** (351 previos + 47 nuevos: 35 sobre odds_value_engine y 12 sobre ghost-edge L5/L15).
- Cobertura: 8 formatos de odds parametrizados (decimal/American/fractional/comma), edge VALUE/FAIR/NO_VALUE/UNKNOWN, EV positivo/negativo, line movement con steam, compare_bookmaker con entries inválidos, evaluate_market en 4 escenarios (`priced`, `manual_odds_required`, `no_odds`, best-of-N), 6 escenarios ghost-edge incluyendo cap=45.
- Backend reinició limpio.

### Despliegue
Cambios en **PREVIEW**. Para `https://low-volatility-plays.emergent.host` se necesita **redeploy** del usuario.

### Pendiente para próximas iteraciones
- **Batch B**: `mlb_statcast_adapter` con Bright Data scraper como fuente principal + `pybaseball` como fallback. Output: xERA/xwOBA/hard_hit_pct/barrel_pct/whiff_pct para pitchers y batting.
- **Batch C**: mejorar `live_territorial_control` con xT proxy (xG + shots + posesión + corners + entradas al área) y adapter stub para socceraction.
- **Arbitrage finder**: separado, cuando haya multi-bookmaker data.

---


## Phase MLB-FP5 — StatMuse Fallback + F5 / Team Total / NRFI-YRFI
**Estado:** ✅ COMPLETADO (2026-06-03)

### MLB-FP5.1 StatMuse scraper — `services/statmuse_recent_form.py` (nuevo)
- Wrapper sobre `brightdata_fetch()` (ya configurado en `.env` con `BRIGHTDATA_*`).
- Parser HTML stdlib (`html.parser`) que extrae la tabla ranking de cualquier URL StatMuse del estilo `https://www.statmuse.com/mlb/ask/mlb-team-stats-last-{N}-games`.
- Alias map para columnas: `TEAM → team`, `G/GP → G`, `R/R/G → R`, `H/H/G → H`, `BB → BB`, `HBP → HBP`, `HR/HRs → HR`, `OBP`, `OPS`.
- `_normalise_team_name()` quita rank prefix ("1. Yankees" → "Yankees") y registros parentéticos ("(8-7)").
- `find_team_row(rows, team_name)` con loose token-set matching (Jaccard-ish) para conciliar "NY Yankees" vs "New York Yankees".
- `get_team_recent_form_via_statmuse(team_name)` pulls L5 + L15 y devuelve el mismo shape que la API primaria → drop-in fallback.
- `compare_forms(primary, secondary, threshold_pct=10.0)` produce reporte de discrepancias.
- Cache 12h en memoria. Bright Data como primer fetch; `direct_fetch` como último recurso.

### MLB-FP5.2 Integración fallback + cross-validation en `mlb_recent_form_split.py`
- `get_team_recent_form()` ahora acepta `team_name` opcional.
- Si la API primaria (MLB Stats API) devuelve `{}` → intenta StatMuse y marca `primary_source="statmuse_fallback"`.
- Si devuelve datos → opcionalmente llama StatMuse en paralelo y attacha `cross_validation: {match, issues, source}`.
- Discrepancias > 10% en cualquier métrica headline (runs/hits/walks/HR L5+L15) → log `[STATMUSE_DISCREPANCY] team=X issues=[...]` para auditoría.
- Fail-soft: cualquier excepción del fallback no rompe el pipeline.

### MLB-FP5.3 F5 + first-inning desde `/game/{pk}/linescore`
- `_fetch_boxscore_lines()` ahora pulls `linescore` en paralelo (`asyncio.gather`) sin HTTP extra (mismo cache 12h).
- Extrae `first_inning_runs` (sumando runs del inning 0 para cada side) y `f5_runs` (innings 1-5 sumados por side).
- `_aggregate()` calcula `f5_runs_avg`, `first_inning_runs_avg` y `first_inning_scored_rate` (P(team anotó ≥1 carrera en 1ra)).
- `build_recent_form_payload()` agrega 2 nuevos bloques:
  - **`f5_split`**: per-team L5/L15/Δ + combined trend.
  - **`first_inning_split`**: per-team + combined `yrfi_rate` (vía P(home ∨ away) = 1 - (1-p_h)(1-p_a)) + `nrfi_rate`.

### MLB-FP5.4 Trend Interpreter extendido — `mlb_trend_interpreter.py`
- Nueva función `_detect_market_kind(market)` clasifica entre: `totals_full`, `totals_f5`, `team_total`, `nrfi`, `yrfi`, `runline_plus_15`, `other`.
- `interpret_recent_form()` ahora acepta kw-only: `f5_split`, `first_inning_split`, `team_total_context`.
- 3 evaluators nuevos con reglas dedicadas:
  - **`_evaluate_f5()`**: thresholds calibrados a Δ ±0.8 carreras/5-innings; ajustes ±10/±6 score+conf.
  - **`_evaluate_team_total()`**: solo evalúa la metrica del lado picked (`team_side: "home"|"away"`); usa tob_delta + runs_delta de ese equipo + HR trend.
  - **`_evaluate_nrfi_yrfi()`**: anchor en `yrfi_rate_last_15` (low/high baseline) + detect recent shift L5 vs L15 (±20%).
- Output incluye `market_kind` para que la UI sepa qué chip mostrar.

### MLB-FP5.5 Orchestrator wiring
- Pasa `team_name` a `get_team_recent_form()` (necesario para StatMuse).
- Detecta team_total en el market label (busca tokens del nombre del equipo) → setea `team_total_context: {team_side, force_kind: "team_total"}`.
- Pasa `f5_split` + `first_inning_split` al interpreter.

### MLB-FP5.6 UI — chip `market_kind` en TrendInterpretationBlock
- Header del bloque ahora muestra un mini-chip slate con el tipo: "Total juego" / "F5" / "Total equipo" / "Runline +1.5" / "NRFI" / "YRFI".
- `data-testid="${testId}-market-kind"` para QA.

### Validación
- **351 tests PASS** (312 previos + 39 nuevos en `test_statmuse_and_f5_nrfi.py`).
- Cobertura nueva:
  - StatMuse parser (tabla simple, normalización rank/record, columnas desconocidas, tabla vacía, token-match).
  - `compare_forms` (match, discrepancy ≥ 10%, missing metrics ignorados).
  - `_detect_market_kind` 15 casos parametrizados.
  - F5 split builder (combined delta, missing data).
  - First-inning split (yrfi_rate vía union probability).
  - F5 evaluator (rising/declining × Over/Under).
  - Team-total evaluator (per-side direccional + side-unknown).
  - NRFI/YRFI evaluator (baseline low/high + recent shift + no_data).
  - End-to-end routing (`combine_trend_signals` con `selected_market="NRFI"` produce `market_kind="nrfi"` + reason codes correctos).
- ESLint + esbuild limpio. Backend reinició limpio (APScheduler todos los jobs activos).

### Despliegue
Cambios en **PREVIEW**. Para `https://low-volatility-plays.emergent.host` se necesita **redeploy** del usuario.

---


## Phase MLB-FP4 — Recent Form v2 (schedule+boxscore) + Trend Interpreter
**Estado:** ✅ COMPLETADO (2026-06-03)

### MLB-FP4.1 Root-cause fix: L5 = L15 (Δ=0.0)
- **Bug confirmado por screenshot del usuario**: el panel "Tendencia carreras 5 vs 15" mostraba valores idénticos para L5 y L15 (4.3/4.3, 12.0/12.0, etc.) porque el endpoint `/teams/{id}/stats?stats=lastXGames&limit=N` IGNORA el parámetro `limit` y devuelve el mismo agregado season-to-date para cualquier N.
- **Fix**: rescritura completa de `services/mlb_recent_form_split.py` con `/schedule` + `/boxscore`:
  1. `GET /api/v1/schedule?sportId=1&teamId={teamId}&startDate=-35d&endDate=today&gameType=R` → partidos FINAL.
  2. Para cada `gamePk` (top-15 más recientes) → `GET /api/v1/game/{gamePk}/boxscore` → batting line del equipo.
  3. Aggregate L15 y L5 separadamente; Δ_5_vs_15 calculado correctamente.
- Cache de 12h (`_SCHEDULE_CACHE` + `_BOX_CACHE`), fail-soft total, paralelo con `asyncio.gather`.

### MLB-FP4.2 Trend Interpreter — `services/mlb_trend_interpreter.py` (nuevo)
- Capa de interpretación que consume `recent_run_split` + `on_base_profile` y produce señales accionables.
- Reglas: TOB Δ ≥ +2.0 → strong_rising (over +16, explosive +12); ≥+1.5 → moderate (+10); ≤-2.0 → strong_declining (under +16); HR rising → over +4 + explosive +4.
- Decisión `SUPPORTS_OVER / SUPPORTS_UNDER / MIXED / NEUTRAL` por diff ≥ 8.
- Ajustes por mercado: Under + SUPPORTS_UNDER → +6/+6; Under + SUPPORTS_OVER → -12/-12; Mixed → -4/-4.
- Runline +1.5 con lógica separada underdog vs favorite (UNDERDOG_OFFENSE_CAN_COMPETE, FAVORITE_OFFENSE_SURGING_AGAINST_RUNLINE, FAVORITE_POWER_SPIKE_RUNLINE_RISK).
- Clamps: score ∈ [-15, +12], confidence ∈ [-12, +6].
- Outputs incluyen `human_summary`, `human_explanations` (ES), `decision_notes`, `mixed_signals`, `impact_on_final_pick`, `per_side` breakdown.

### MLB-FP4.3 Integración orchestrator
- Tras computar `recent_form_payload`, se llama a `combine_trend_signals()` y se aplica:
  - `pick_payload["trend_interpretation"]` (payload completo para UI + audit).
  - `recommendation.confidence_score` += `confidence_adjustment` (clamped 0-100).
  - `recommendation.confidence_trend_adjustment` (auditoría).
  - `reason_codes` extendidos con los del interpreter.
  - `underdog_side` derivado desde moneyline odds (home_ml > away_ml → home underdog).

### MLB-FP4.4 UI — `TrendInterpretationBlock` (nuevo subcomponente)
- Render justo debajo del bloque L5 vs L15 en `HistoricalProfilePanel.jsx`.
- Decision chip (emerald/sky/amber/slate), human summary, grid 2× Δ Score + Δ Confianza con signo.
- Barras de Apoyo Over / Apoyo Under (0-16).
- Chip riesgo explosivo cuando `explosive_risk_boost > 0`.
- Listas de `human_explanations` + `decision_notes`.
- `impact_on_final_pick` italic al pie.

### Validación
- **312 tests PASS** (297 previos + 15 nuevos del interpreter + 8 nuevos del schedule scraper).
- Cobertura: aggregate, scheduling mocked → L5 ≠ L15, empty/error fail-soft, strength bands, market-direction adjustments, mixed signals, runline +1.5, clamps.
- ESLint + esbuild limpio. Backend reiniciado limpio.

### Despliegue
Cambios en **PREVIEW**. Para `https://low-volatility-plays.emergent.host` se necesita **redeploy** del usuario.

---


## Phase MLB-FP3 — Live Engine v2 + Recent Form Split BB/HR/Hits
**Estado:** ✅ COMPLETADO (2026-06-03)

### MLB-FP3.1 Live engine — FIN del falso "Datos live insuficientes"
- Archivo: `services/live_pre_match_comparison.py`
- Bug: cuando el partido finalizaba sin `period_n` (juegos FINAL), `_classify_script` devolvía `insufficient_data` aunque el validator hubiera resuelto el pick → la UI bloqueaba toda la información y mostraba "Datos live insuficientes" en lugar de "Pick ya perdió".
- Fix: tras `_classify_script`, si tenemos `actual_total` y el partido es FINAL o validator devolvió `already_resolved_*`, se promueve `script_status="final_settled"`.

### MLB-FP3.2 Nuevo `live_verdict` (chips solicitados por el usuario)
- Función nueva: `_derive_live_verdict()` con lógica para los 7 chips canónicos:
  - `PICK_ALREADY_LOST` / `PICK_ALREADY_WON`
  - `AVOID_UNDER_OR_LOOK_OVER` / `AVOID_OVER_OR_CASHOUT`
  - `MAINTAIN` (on-script + still playable)
  - `CASHOUT` (deviación)
  - `NO_ACTIONABLE` (final sin resolución / sin pregame)
- Lógica direccional: si pregame era Under y actual > expected con broken_script → `AVOID_UNDER_OR_LOOK_OVER`; si era Over y actual < expected → `AVOID_OVER_OR_CASHOUT`.

### MLB-FP3.3 Payload `live_data` (box-score live)
- El comparator ahora extrae `score_home / score_away / total_runs / hits / walks / home_runs / errors / strikeouts / pitches / inning + half` del `live_state` y los expone bajo `comparison.live_data`. Filtra keys nulos para mantener el payload limpio.

### MLB-FP3.4 Recent form split — HR + deltas explícitos
- Archivo: `services/mlb_recent_form_split.py`
- `_fetch_last_x_games` / `get_team_recent_form` ahora calculan `home_runs_avg_last_5/15`.
- `_ob_block` expone `hits_delta_5_vs_15`, `walks_delta_5_vs_15`, `hbp_delta_5_vs_15`, `home_runs_delta_5_vs_15` para que la UI pueda renderizar tendencias por componente.

### MLB-FP3.5 Frontend — `LivePreMatchComparisonPanel.jsx`
- Eliminado el early-return en `insufficient_data` cuando hay `actual_total` / `pregame_pick_status` accionable / `live_verdict` (criterio `hasUsefulInfo`).
- Añadido pill `final_settled`.
- Nuevo chip "Veredicto live" con los 7 estados + colores específicos (rose para lost, cyan para won, orange para pivot, emerald para maintain, amber para cashout, slate para no-actionable).
- Nueva tabla de box-score live: 4 columnas (Local / Visitante / Total) con filas para carreras, hits, BB, HR, errores, ponches, lanzamientos — render condicional, oculta filas con todos los valores nulos.

### MLB-FP3.6 Frontend — `MatchDetailPage.jsx`
- El header del bloque "settled" antes era genérico ("Pick pregame ya cumplido / no accionable"). Ahora:
  - `already_won` → "Pick pregame · ya ganó" (cyan)
  - `already_lost` → "Pick pregame · ya perdió" (rose)
  - `not_actionable` → "Pick pregame · no accionable" (amber)

### MLB-FP3.7 Frontend — `HistoricalProfilePanel.jsx`
- `OnBaseTrendCell` ahora renderiza sub-filas para **Hits / BB / HR** con L15 · L5 · Δ debajo del bloque agregado de times-on-base. Δ coloreado por dirección (verde / rojo / gris). Cada fila tiene `data-testid` específico (`-hits`, `-walks`, `-home_runs`) para QA.

### Validación
- 13 tests nuevos en `tests/test_live_verdict_and_form_split.py` → PASS:
  - `_derive_live_verdict()` para 7 escenarios.
  - Caso real Minnesota 6-4 FINAL → final_settled + already_lost + PICK_ALREADY_LOST.
  - Caso real Yankees 4-9 9th → already_lost + PICK_ALREADY_LOST.
  - Recent form split expone HR + deltas calculados correctamente.
  - Umbrales `_classify_run_trend` / `_classify_on_base_trend`.
- Suite total: **289 tests PASS** (276 previos + 13 nuevos).
- ESLint + esbuild OK en `LivePreMatchComparisonPanel.jsx`, `HistoricalProfilePanel.jsx`, `MatchDetailPage.jsx`.
- Backend reiniciado limpio, APScheduler activo.

### Nota de despliegue
- Los cambios viven en **PREVIEW**. Para producción (`https://low-volatility-plays.emergent.host`) requiere **redeploy** del usuario.

---

## Phase MLB-FP2 — Deep Script UI: lean visual + L5/L15 + Manual Odds inline
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-FP2.1 Fix override del lean histórico (root cause del "LEAN OVER CARRERAS" en pick UNDER)
- Archivo: `services/mlb_day_orchestrator.py` (~líneas 1379-1455).
- **Bug**: el `market_lean_classifier.classify_and_validate()` se ejecutaba bien y producía `lean=UNDER` para casos como Detroit @ Rays (ER 7.1 vs línea 9.5), pero el override escribía en `baseballHistoricalProfile["overUnderLean"]` (raíz). La UI (`HistoricalProfilePanel.jsx`) lee `combined.overUnderLean` → el override nunca llegaba al header y se mostraba el heurístico legacy (`projected_total_runs > league_avg` → OVER).
- **Fix**: el override ahora escribe primero en `baseballHistoricalProfile.combined.overUnderLean / overUnderLeanDisplay / overUnderLeanConfidence / overUnderLeanReason / overUnderLeanConsistency`. Conserva mirror en la raíz para consumidores legacy (`baseball_runs_rescue`, `script_conflict`).
- Adicional: el override guarda `combined.historicalLeanLegacy` para auditoría.

### MLB-FP2.2 Mixed Signals payload (señales mixtas)
- Cuando `legacy_lean` ≠ `final_lean`, el orquestador genera `combined.mixedSignals`:
  ```python
  {
    "has_mixed_signals": True,
    "over_signals":      ["HISTORICAL_HEURISTIC_LEAN_OVER", "RISING_RUN_ENVIRONMENT", "RISING_ON_BASE_PRESSURE", ...],
    "under_signals":     ["EXPECTED_RUNS_BELOW_LINE", ...],
    "final_resolution":  "LEAN_UNDER",
    "legacy_lean":       "OVER",
  }
  ```
- UI render: `MixedSignalsBlock` en `HistoricalProfilePanel.jsx` con dos columnas (apuntan a Over / Under) + ribbon de resolución final.

### MLB-FP2.3 Mirror recent_run_split + on_base_profile en `baseballHistoricalProfile`
- Los campos `recent_run_split`, `recent_run_trend`, `on_base_profile` que ya se calculaban en pick_payload ahora también se copian en `baseballHistoricalProfile.recentRunSplit / recentRunTrend / onBaseProfileL5`.
- Permite al panel renderizar el bloque sin tocar la API de la card.

### MLB-FP2.4 UI: bloque L5 vs L15
- Archivo: `frontend/src/components/HistoricalProfilePanel.jsx` (`RecentFormSplitBlock`, `RunTrendCell`, `OnBaseTrendCell`).
- Grid 3 columnas (Local / Visitante / Combinado) para `runs_scored_avg_last_15 / last_5 / delta` con chip de trend (Subiendo / Bajando / Estable) — umbral L5-L15 ≥ ±1.25 carreras.
- Grid 2 columnas (Local / Visitante) para `times_on_base_avg_last_15 / last_5 / delta` con OBP opcional — umbral ±1.0.
- Trends consolidados consumidos directos del backend (`RISING_RUN_ENVIRONMENT` / `RISING_ON_BASE_PRESSURE` / etc.).
- Sólo renderiza cuando al menos un valor L5 está presente; fail-soft si MLB Stats API no responde.

### MLB-FP2.5 UI: input inline "Agregar cuota manual"
- Nuevo componente: `frontend/src/components/InlineManualOddsInput.jsx`.
- Surfacea inside la card `MatchCard.jsx` justo debajo de `Cuota aprox.: —` cuando:
  - `sport === "baseball"` AND `recommendation.odds_range` está vacío.
- POST al endpoint existente `/api/mlb/picks/{pickId}/manual-odds` (acepta `"1.85"` y `"1,85"`).
- Toast en español con `value_status` + edge%.

### Validación
- `pytest backend/tests/` → **276 PASS** (sin regresiones).
- `esbuild` + ESLint sobre `HistoricalProfilePanel.jsx`, `MatchCard.jsx`, `InlineManualOddsInput.jsx` → 0 errors.
- Backend reiniciado limpio (todos los APScheduler jobs activos).
- Smoke screenshot: dashboard carga sin runtime errors.

---

## Phase MLB-FP1 — Final Pick Router + Manual Odds + Momentum (L5 vs L15)
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-FP1.1 Conflict detector
- Archivo: `services/mlb_script_conflict.py`
- `detect_total_script_conflict(chosen_market, deep_script)` con severity ladder (`high` / `medium`).
- Códigos: `UNDER_PICK_CONFLICTS_WITH_OVER_SCRIPT`, `OVER_PICK_CONFLICTS_WITH_UNDER_SCRIPT`, `UNDER_BELOW_PROJECTED_RUNS`, `UNDER_CLOSE_TO_PROJECTED_RUNS`, `OVER_ABOVE_PROJECTED_RUNS`, `F5_OVER_VS_FULLGAME_UNDER`.
- Wireado en `services/mlb_day_orchestrator.py` (fail-soft): inyecta `pick_payload.script_conflict` y degrada/redirige a watchlist en severity `high`.

### MLB-FP1.2 Manual odds helpers
- En el mismo módulo: `parse_manual_odds()` (acepta `"1,85"`/`"1.85"`, guard ≥ 1.01) y `calculate_manual_edge()` (`VALUE` / `FAIR_VALUE` / `NO_VALUE` / `UNKNOWN` / `INVALID`).

### MLB-FP1.3 Endpoint `POST /api/mlb/picks/{pick_id}/manual-odds`
- Archivo: `server.py`
- Lookup en `pick_runs` (buckets `picks`, `rescued`, `structural_lean_requires_odds`, `watchlist_manual_odds`).
- Recalcula edge contra `estimated_probability` (con fallback a `_mlb_script_v2.coverProbability`).
- Persiste `manual_odds*`, `manual_value_status`, `manual_can_recommend`, `manual_rationale`, `manual_odds_submitted_at` en el bucket correcto vía `arrayFilters`.
- Promoción opcional a `RECOMMENDED_MANUAL_ODDS` si `promote_if_value && value_status == VALUE`.

### MLB-FP1.4 Recent-form split (L5 vs L15)
- Archivo: `services/mlb_recent_form_split.py`
- `get_team_recent_form()` consulta MLB Stats API `lastXGames` con caché 12h.
- `build_recent_form_payload()` genera `recent_run_split`, `recent_run_trend` (`RISING_RUN_ENVIRONMENT` / `STABLE` / `DECLINING` / `UNKNOWN`) y `on_base_profile` con sub-tendencias por equipo.
- Integrado en orchestrator (fail-soft, gather paralelo home+away).

### MLB-FP1.5 UI — `ManualOddsReviewPanel.jsx`
- Acepta coma o punto como separador decimal.
- Llama al endpoint con `api.post` y muestra `value_status` + `manual_edge_pct` + toast en español.
- Render del conflict ribbon (`script_conflict`) con severity colors.
- **Bugfix:** `import api from '@/lib/api'` → `import { api } from '@/lib/api'` (el módulo solo exporta named, no default — sin el fix el componente fallaba en runtime).

### Validación
- `pytest backend/tests/` → **276 tests PASS** (regresión completa).
- Smoke endpoint:
  - `POST /manual-odds {"manual_odds":"0,5"}` → 400 `"manual_odds inválida (debe ser ≥ 1.01, acepta '1.85' o '1,85')"`.
  - `POST /manual-odds {"manual_odds":"1,85"}` → 404 `"pick not found in recent runs"` (esperado sin pick real).
- `esbuild` sobre `ManualOddsReviewPanel.jsx` → 0 errors.
- Dashboard carga limpio con demo user (screenshot validado).

---

## Phase MLB-P0 — MLB Under Confidence Floor (Moneyball)
**Estado:** ✅ COMPLETADO (2026-06-02)

### MLB-P0.1 Pre-guardia en Moneyball
- Archivo: `services/moneyball_layer.py`
- Punto: `analyze_pick()` justo antes de `classify_pick()`.
- Regla:
  - `sport == "baseball"`
  - `edge is not None` (odds disponibles)
  - `market` contiene `under`, excluye `team total` y `nrfi`
  - `confidence_score < MLB_UNDER_CONFIDENCE_FLOOR` (default 75)
  - → `WATCHLIST` + razón explícita + `pick["_conf_floor_demoted"] = True`.

### MLB-P0.2 Exposición en summary
- Archivo: `server.py`
- Añade: `summary.conf_floor_demoted` para inspección UI/QA.

---

## Phase MLB-V7 — MLB Engine V3 (Explainability + Game Script + Diversificación + Baseball-first)
**Estado:** 🟨 PENDIENTE (fase futura; V6 ya cubre drivers ofensivos + swap Over/Under)

> Nota: con V4/V5/V6 ya existe explicabilidad fuerte. V7 queda como refactor/iteración de guion v3 (si se desea ampliar más allá del panel actual) y diversificación adicional.

---

## 3) Next Actions

### A) Validación formal MLB-V6 (P0) — Testing Agent v3
**Estado:** 🟨 PENDIENTE (siguiente paso inmediato)
1) Ejecutar `testing_agent_v3`:
   - Pure functions V6 (`mlb_over_discovery.py`).
   - Endpoint `GET /api/mlb/daily_market_audit`.
   - Regresión V1–V5 (orchestrator chain intacta).
   - **Nuevo:** verificar que `MLB_UNDER_CONFIDENCE_FLOOR` demote correctamente a WATCHLIST cuando hay odds.
   - **Nuevo:** verificar bucket `summary.conf_floor_demoted` y flag `_conf_floor_demoted`.
2) Validar UI (smoke):
   - Chips V6 en `MLBScriptV3Panel` (summary siempre visible + detail al expand).
   - Badge `Under→Over` cuando exista swap.
   - **Nuevo:** que el bucket `conf_floor_demoted` sea consumible por el frontend (aunque aún no haya panel dedicado).

### B) Bright Data Unlocker (P0 bloqueado) — siguiente prioridad scraping
1) Confirmar `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE` (Web Unlocker).
2) Implementar cascade por scraper: `direct_fetch` → (403/timeout) → `brightdata_fetch`.
3) Añadir cache TTL en DB para reducir coste (por tipo de URL).
4) Activar Unlocker en:
   - Editorial Context (Sportytrader/BeSoccer/scores24)
   - NBA/basketball y MLB scrapers con Cloudflare.

### C) Basketball Historical Detail (P1)
1) Implementar profile + integración pipeline.
2) Añadir rescue layer (totales/team totals) + trap signals.
3) UI “Historial profundo”.

### D) Fix 2C (P2) — Persistencia live como async
**Estado:** 🟨 PENDIENTE
- Consolidar persistencia dentro de `build_live_intelligence_payload` como llamada `async`.

### E) Football deep-live parity (P3)
**Estado:** 🟨 PENDIENTE
- Aplicar `LivePreMatchComparisonLayer` y lógica live profunda a Football.

---

## 4) Success Criteria
- Market tolerance y rescue layers funcionan sin inventar valor.
- LIVE multi-deporte estable; sin zombies; sin fugas de vocabulario.
- Editorial Context:
  - Scrapy/Playwright/BrightData degradan elegante.
  - Fuentes bloqueadas se desbloquean con Unlocker cuando procede.
  - UI muestra contexto con fuentes y warnings.

- Historical Detail Enrichment:
  - Ningún match (basketball/baseball) prioritario se descarta sin intentar perfil histórico.
  - Se detectan oportunidades en **totales/team totals/F5/run line** con razonamiento humano.
  - Moneyball guardrail siempre manda: sin edge → no recomendación.

- **MLB Under Confidence Floor (✅ nuevo, cumplido)**
  - Un pick MLB Under **no** puede quedar como apuesta recomendada si:
    - hay odds (edge calculable) y `confidence_score < 75`.
  - Debe degradarse a `WATCHLIST` con razón explícita y flag `_conf_floor_demoted=True`.
  - `summary.conf_floor_demoted` expone esos casos para auditoría.

- **F6C Auto-Settle (✅ nuevo, cumplido)**
  - Evaluaciones `mlb_run_evaluations` en `pending` se resuelven automáticamente cuando:
    - `matches.final_score` existe (escrito por `mlb_finished_game_settler`).
  - Deben actualizarse a `won/lost/push` con `resolved_at` y `miss_type` correcto.
  - Markets no determinísticos (F5/NRFI/inning) se dejan `pending` con `auto_settle_skipped_reason` para manual.

- **MLB-V2 (✅ cumplido y validado)**
  - Picks MLB incluyen: `Projected Margin`, `Cover Probability`, `Best/Recommended Total Line`, `lineSafetyScore`, `pickType`, `sameGameCorrelation`.
  - Parlays MLB-only de 2–4 picks con correlación ≥60 (cuando existan suficientes picks elegibles).
  - Run Line -1.5 solo cuando hay dominancia real.
  - Cero regresiones:
    - Football/basketball sin `_mlb_script_v2`.
    - Parlay genérico intacto fuera de MLB.

- **MLB-V3 (✅ cumplido)**
  - `baseballHistoricalProfile` presente por pick (fail-soft: `available=false` con `_reason`).
  - `historical_trap_signals` expuestas y ajustan `fragility.score`.
  - `baseball_runs_rescue` se intenta antes de descartar cuando el histórico lo permite.

- **MLB-V4 (✅ cumplido)**
  - Endpoint live: `POST /api/mlb/live/reevaluate`.
  - Live Intelligence solo para matches que pasaron filtro pregame.
  - UI en Match Detail con panel Live.

- **MLB-V5 (✅ cumplido)**
  - Script Survival 0–100 + Fragility 0–100 visibles en cards.
  - Clasificación de estabilidad (ELITE_STABLE…HIGHLY_FRAGILE) + panel detalle.

- **F6A/F6B (✅ cumplido)**
  - Swap Full Game Under → F5 Under si bullpens riesgosos.
  - Storage de script breaks en DB post-match.

- **MLB-V6 (✅ cumplido)**
  - Totals: `coverProbability` corresponde a P(Under/Over) del lado recomendado (Poisson).
  - UI muestra `Edge vs línea` y debug de probabilidades.
  - Counter = render: el dashboard renderiza todas las cards (incluye rescued/manual-review).
  - Over Discovery:
    - Offensive Explosion Score + script badge + drivers visibles.
    - Over Survival visible.
    - Badge Under→Over cuando Market Competition swap.
  - Endpoint `GET /api/mlb/daily_market_audit` operativo.

### Testing status
- **Suite total MLB (incluye nuevo settler):** 148 tests PASS.
- **Nuevo archivo tests:** `tests/test_mlb_results_settler.py` → 20/20 PASS.
- Smoke tests:
  - Imports OK: `mlb_results_settler`, `moneyball_layer`, `server.py`.
  - Backend reiniciado limpio; scheduler arrancó con job `auto_settle_mlb_evaluations`.

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
