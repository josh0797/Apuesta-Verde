# Plan — Phase F58 (Football L5 vs L15 Profile Cross + Player Props Discovery)

## 1) Objetivos
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (rápido y estable para shots/SOT/minutos)
  - FBref para enriquecer mercados de volumen (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso para xG/shots/sot cuando aplica
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con un modo más agresivo: **override contextual** cuando el cross sea “muy fuerte”.
- Añadir **smoke tests** (mínimos) y mantener la suite global verde (cero regresiones).
- (P2) **UI wiring**: panel independiente en cards football para visualizar Cross + Override + Player Props.

**Estado actual:**
- ✅ Backend Phase F58 completado (módulos core + integración + tests).
- ✅ UI panel independiente completado y cableado en `MatchCard` (fail-soft).
- ✅ Probe real EPL ejecutado (StatMuse OK; FBref directo 403 sin Bright Data).
- ✅ FBref scraper implementado (con caveat anti-bot) + smart merge.
- ✅ Test suite verde: **1744/1744 passing**.

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador (core frágil)
**Por qué aquí:** el scraping es el punto más propenso a romperse (estructura HTML/anti-bot) y condiciona toda la discovery.

**User stories (POC)**
1. Como caller, quiero pedir stats de un jugador y obtener un dict normalizado aunque falten campos.
2. Como caller, quiero que si StatMuse falla, el sistema intente fallback automáticamente.
3. Como caller, quiero que si todo falla, el ingestor responda fail-soft sin romper el pipeline.
4. Como dev, quiero poder ejecutar un script local que pruebe jugadores reales end-to-end.
5. Como sistema, quiero cachear resultados para no golpear la fuente repetidamente.

**Pasos (COMPLETADO + ACTUALIZADO)**
- ✅ Crear `backend/services/football_player_stats_ingestor.py`.
  - ✅ API: `hydrate_player_stats(*, player_name: str, team: str | None = None, league: str | None = None) -> dict`.
  - ✅ Salida estándar (siempre):
    - `{"available": bool, "source": str, "confidence_penalty": int, "minutes_sample": int|None, "stats": {...}, "raw": {...}}`.
  - ✅ Normaliza métricas: `shots_p90`, `sot_p90`, `passes_p90`, `tackles_p90`, `fouls_p90`, `cards_p90`, `xg_p90`, `minutes_p_game`, `minutes_sample`.
  - ✅ Implementación (cadena final):
    - ✅ **StatMuse scraping** (primario): HTMLParser + cache + fail-soft.
    - ✅ **FBref scraper** (terciario/enriquecimiento):
      - Search → player page → tabla Standard Stats (usa `data-stat` para robustez).
      - Prefer Bright Data cuando esté disponible; `direct_fetch` como último intento.
      - **Caveat**: FBref puede devolver 403 desde IPs de datacenter (sin Bright Data).
    - ✅ **Understat fallback** (último recurso): xG/shots/sot cuando se dispone de helper.
  - ✅ **Smart merge**:
    - Si StatMuse devuelve stats parciales → se intenta FBref y se rellenan `None` sin sobre-escribir valores de StatMuse.
    - Si StatMuse viene completo → NO se consulta FBref (politeness/perf).
    - `source` ∈ {`statmuse`, `fbref`, `understat`, `statmuse+fbref`, `unavailable`}.
  - ✅ Cache in-memory TTL **6h**.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**User stories**
1. Como analista, quiero ver un perfil de cruce L5 vs L15 para entender si el partido cambió de régimen.
2. Como sistema, quiero deltas de confidence/fragility consistentes con el patrón MLB.
3. Como UI, quiero un entry “pattern_alignment” visual para explicar el cross.
4. Como caller, quiero un resultado fail-soft si faltan inputs.
5. Como trader, quiero que en señales muy fuertes se pueda **override** (según reglas).

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_team_profile_cross.py`:
  - ✅ `classify_team_football_profile(...)`.
  - ✅ `compute_combined_football_profile_cross(home, away)` → 7 perfiles.
  - ✅ `apply_profile_cross_to_pick(...)`:
    - Override gating:
      - Solo `STRONG_UNDER_CROSS`, `STRONG_OVER_CROSS`, `CORNERS_OVER_CROSS`.
      - Umbral “muy fuerte”: `confidence_delta >= 10`.
      - Solo si el cross contradice el pick.
  - ✅ `build_pattern_alignment_entry(...)` visual-only (`visual_only=True`).

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**User stories**
1. Como usuario, quiero props “aburridas” (alto volumen) en vez de longshots.
2. Como sistema, quiero rechazar props con prob baja aunque parezcan +EV.
3. Como caller, quiero una lista ordenada por edge_score con metadata completa.
4. Como sistema, quiero degradar a “no props” si faltan stats del jugador.
5. Como usuario, quiero que “Player to score” solo aparezca si es ultra-élite (edge≥90 y fragility≤35).

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_player_props_discovery.py`:
  - ✅ Tier 1: `SHOTS_OVER`, `SOT_OVER`, `PASSES_OVER`, `TACKLES_OVER`.
  - ✅ Tier 2: `FOULS_OVER`, `CARDS_OVER`.
  - ✅ Tier 3: `PLAYER_TO_SCORE` (gate duro: `edge_score>=90` & `fragility<=35`).
  - ✅ Poisson λ y Moneyball gates (`min_prob=0.55`, `min_edge_pts=4.0`, `longshot_floor=0.50`).
  - ✅ Consume `hydrate_player_stats` por defecto (o inyectable).

### Fase 4 — Integración en Football pipeline (override incluido)
**User stories**
1. Como sistema, quiero que el cross pueda modificar/override el pick final cuando sea muy fuerte.
2. Como UI, quiero ver claramente cuándo hubo override y por qué.
3. Como sistema, quiero que fallos de scraping no rompan el refresh de football.
4. Como analista, quiero poder auditar reason_codes y fuentes.
5. Como QA, quiero que los cambios no afecten baseball/basketball.

**Pasos (COMPLETADO)**
- ✅ Crear `backend/services/football_phaseF58_integration.py`:
  - Deriva inputs L5/L15 desde `recent_fixtures`.
  - Adjunta `combined_football_profile_cross` al payload.
  - Aplica deltas simétricos + escribe `football_profile_cross_applied`.
  - Añade entry visual-only en `pattern_alignment.entries`.
  - **No muta** el market final: emite `override` como sugerencia auditable.
- ✅ Cableado en `backend/services/football_moneyball/football_pattern_matcher.py` (paso 5, antes de persist).

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**User stories**
1. Como usuario, quiero visualizar el cross L5 vs L15 y entender qué apoya.
2. Como usuario, quiero ver un aviso claro cuando existe un override sugerido.
3. Como usuario, quiero ver props Moneyball por jugador (tiers) cuando estén disponibles.
4. Como sistema, el panel no debe romper cards sin payload (self-hide).

**Pasos (COMPLETADO)**
- ✅ Crear `frontend/src/components/FootballProfileCrossPropsPanel.jsx` (diseño propio, no replica MLB):
  - Header + sección Cross + sección Override + sección Player Props.
  - Paleta emerald/cyan (sin purple), microtipografía estilo terminal, `data-testid`.
  - Self-hide si no hay datos.
- ✅ Integrar en `frontend/src/components/MatchCard.jsx` para `sport === 'football'`.
- ✅ Verificación de compilación con esbuild.

### Fase 6 — Prueba con datos reales (P2) — validar `hydrate_player_stats`
**User stories**
1. Como operador, quiero confirmar que StatMuse funciona en el entorno.
2. Como operador, quiero saber si FBref está bloqueado por anti-bot y qué requiere.
3. Como sistema, quiero que el chain degrade sin fallar.

**Pasos (COMPLETADO + DOCUMENTADO)**
- ✅ Crear `backend/scripts/test_phaseF58_real_player.py`.
- ✅ Ejecutar probe EPL (Haaland, Saka, Salah):
  - StatMuse **OK** (200) → shots_p90/sot_p90/minutes_sample.
  - FBref **403** sin Bright Data (anti-bot) → se documenta como caveat.
- ✅ Persistir resultados en `backend/scripts/out/phaseF58_real_player_probe.json`.

### Fase 7 — Smoke tests + verificación final
**User stories**
1. Como dev, quiero que `pytest` siga pasando sin regresiones.
2. Como dev, quiero smoke tests que validen contratos y merges.

**Pasos (COMPLETADO + ACTUALIZADO)**
- ✅ Smoke tests existentes F58 (49).
- ✅ Añadidos 11 tests FBref + merge:
  - `tests/test_phaseF58_fbref_scraper_smoke.py`
- ✅ Pytest suite completa: **1744/1744 passing** (0 regresiones).

## 3) Next Actions (orden de ejecución)
**Estado actual: DONE para backend + UI panel + Phase F60 (cost-control gate + corner cross wiring). Próximos pasos recomendados (P2/P3):**
1. (P2) **RTL tests** del nuevo `FootballProfileCrossPropsPanel.jsx`.
2. (P2) **Wiring del payload de props en football pipeline**:
   - Actualmente el panel espera `pick.player_props_discovery`.
   - Falta adjuntar automáticamente `discover_player_props(...)` en el flujo que construye el match/pick payload (por ejemplo en `attach_football_intelligence_to_payload` o en el stage que arma la card).
3. (P3) **Configurar Bright Data en producción** para activar FBref enrichment:
   - Requiere `BRIGHTDATA_API_KEY` + `BRIGHTDATA_CUSTOMER_ID` (según `services/external_sources/base.py`).
4. (P3) **Ampliar StatMuse slugs** para cubrir pases/tackles si StatMuse soporta endpoints equivalentes (reduce dependencia en FBref).
5. (P3) **Backtest del override gating** (tasa de override, hit-rate/ROI) antes de aplicar auto-flip en producción.
6. (P2) **UI surface** del `football_corner_cross_applied` audit + scores24 confirmation chip en MatchCard (visualizar gate verdict + external_confirmation/conflict).

## Phase F60 — External Context Gate + Corner Cross Wiring (COMPLETED)

### Objetivos
- ✅ Controlar el costo de las llamadas a Bright Data Premium (Scores24) con un gate determinístico.
- ✅ Cablear `football_corner_profile_cross` al pipeline principal (`football_pattern_matcher`).
- ✅ Inyectar el `scores24_payload` al cruce de córners cuando el gate lo permita y haya URL del match.

### Implementación
- ✅ **`services/external_context_gate.py`** (399 LOC, pre-existente):
  - **5 reglas allow**: corner candidate, no main value, high priority, layer conflict, edge needs external confirmation.
  - **7 reglas deny**: no candidate, low priority, cache fresh, main value clean, no corner line, mixed profile, late live.
  - Hard-deny rules (cache fresh, late live) cortan antes que cualquier allow.
  - Bug fix: normalización de market strings ("Over 2.5" → "over_2_5") para que el matcher de mercados principales funcione.
- ✅ **`services/football_corner_cross_integration.py`** (NUEVO, fail-soft, async):
  - Step 1: computa el corner cross internal-only (siempre).
  - Step 2: consulta el gate con un payload proyectado.
  - Step 3: si el gate abre + hay URL → llama a `scrape_scores24_match` (con fetcher inyectable para tests).
  - Step 4: re-corre el cross con `scores24_payload` → emite `external_confirmation`/`external_conflict`.
  - Audit completo en `pick_payload["football_corner_cross_applied"]`.
- ✅ **`services/football_moneyball/football_pattern_matcher.py`**:
  - Nuevo step 6 (entre F58 profile cross y persistencia) que llama al integrador.
  - Fail-soft: cualquier error solo se loggea en debug.

### Tests
- ✅ `backend/tests/test_external_context_gate_smoke.py` — 25 tests (5 allow × variantes + 7 deny + edge cases + composites).
- ✅ `backend/tests/test_football_corner_cross_integration_smoke.py` — 8 tests (gate deny, gate allow + scraper OK, no URL, scraper raises, fail-soft inputs, external conflict, premium disabled).
- ✅ Pytest suite: **1827/1827 passing** (+33 nuevos tests, 0 regresiones desde Phase F58).

### Decisiones de diseño (confirmadas con usuario)
- **Siempre correr el corner-cross internal-only**; el gate solo decide el costo de Scores24 (porque el cross interno es gratis).
- Cross results se adjuntan a `pick_payload["combined_football_corner_profile_cross"]` (snake_case) y `pick_payload["footballHistoricalProfile"]["combinedFootballCornerProfileCross"]` (camelCase para UI).
- El payload crudo de Scores24 se stashea en `pick_payload["scores24_corner_payload"]` solo cuando el scraper tuvo éxito.

## 4) Criterios de éxito
- ✅ Ingestor devuelve siempre un dict normalizado; en fallo devuelve `available=False` y no rompe.
- ✅ Cross profile produce uno de los 7 perfiles cuando hay inputs suficientes; si no, `available=False`.
- ✅ Override ocurre **solo** para perfiles permitidos y umbral “muy fuerte” (y solo si contradice).
- ✅ Player props: Tier 1/2 generables con stats; Tier 3 solo si `edge_score≥90` y `fragility≤35`.
- ✅ UI panel se renderiza sin romper cards (self-hide) y respeta design system.
- ✅ `pytest` completo pasa (sin regresiones) y los smoke tests cubren parsing/merge FBref.
---

## Phase F61 — Football Under Support + Cross-Signal Check (COMPLETED)

### Objetivos
- ✅ Crear `football_under_support.py` espejo estructural de `football_over_support.py` (no fuerza picks; aporta una segunda señal estructural al pipeline).
- ✅ Cablearlo al `analyst_engine` para que cada match football quede con AMBOS supports adjuntos simétricamente.
- ✅ Implementar cross-check estructurado en `compute_under_profile_score`: una señal Over fuerte debe penalizar el Under profile; una Under support fuerte debe confirmarlo. Todo expuesto en `cross_signal_check` para auditoría.
- ✅ Documentar el requisito de simetría obligatoria para cuando se cree un `compute_over_profile_score` análogo (TODO explícito en `football_over_support.py`).

### Implementación
- ✅ **`services/football_moneyball/football_under_support.py`** (NUEVO, pure, fail-soft):
  - 6 reason codes positivos + `SIGNAL_MISSING` + `DC_NB_DELTA_TELEMETRY_ONLY`.
  - `MIN_SIGNALS_FLOOR = 3`: si menos señales contribuyen, devuelve `available=False` con `_skipped="insufficient_signals"` (NO devuelve 50 misleading).
  - **`LOW_MOTIVATION_CONTEXT_MILD` conservador**: cap +3 (no +8) y solo aplica si HAY corroboración (cold offenses, low xG, clean sheets, defensa sólida). Sin corroboración → 0 puntos + RC `LOW_MOTIVATION_CONTEXT_MILD_NOT_CORROBORATED`.
  - **`dc_nb_telemetry`**: el delta DC/NB se expone pero NO suma puntos hasta validar el signo. Punto de promoción documentado in-code.
- ✅ **`services/analyst_engine.py`**: nueva sección espejo después del cálculo de over_support, dentro del mismo loop de buckets (picks + descartes + watchlist + protected). Audit en `pipeline_meta.football_totals_model.under_support_attached`.
- ✅ **`services/under_market_scan.py`**: cross-check estructurado en `compute_under_profile_score`:
  - `over_support >= 75` → score −15 + RC `OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE` + `OVER_SUPPORT_STRONG_PENALTY_APPLIED`.
  - `over_support >= 60` → score −8  + RC `OVER_SUPPORT_CONTRADICTS_UNDER_PROFILE`.
  - `under_support >= 70` → score +5 + RC `UNDER_SUPPORT_CONFIRMS_UNDER_PROFILE`.
  - El bloque `cross_signal_check` queda SIEMPRE en el output (incluso cuando no hay supports) con `penalty=0`, `bonus=0`, `reason_codes=[]`.
- ✅ **`services/football_moneyball/football_over_support.py`**: header actualizado con sección "SYMMETRY TODO (Phase F61)" que documenta el contrato espejo que un futuro `compute_over_profile_score` DEBE implementar de fábrica.

### Tests
- ✅ `backend/tests/test_football_under_support_smoke.py` — **20 tests** que cubren los 8 escenarios del spec + variantes:
  1. Empty/None input → `available=False`.
  2. Thin payload → `insufficient_signals`.
  3. Full signals (low-scoring) → `score >= 60`.
  4. Full signals (high-scoring) → `score <= 50`.
  5. Motivación sin corroboración → 0 pts + `NOT_CORROBORATED`.
  6. Motivación con corroboración → +3 pts.
  7. `dc_nb_delta` es telemetry-only (score idéntico con/sin delta).
  8. Cross-check Over-support penalty (75+/-15, 60+/-8, <60/0, unavailable/0).
  9. Cross-check Under-support bonus (70+/+5, <70/0).
  10. Cross-check combinado (Over=80 + Under=72 → net −10).
  11. `cross_signal_check` siempre presente.
  12. Cold weather / attacking injuries bonuses.
- ✅ Pytest suite: **1847/1847 passing** (+20 nuevos, 0 regresiones).

### Política sobre `dc_nb_delta`
Antes de promover esta señal de telemetry a scoring, validar el significado exacto del signo consultando `football_totals_calibration` o `statsbomb_features` directamente. El comentario en `_dc_nb_telemetry` marca el punto de promoción. Hasta entonces, el campo se expone en `dc_nb_telemetry` pero NO suma puntos al score.

### Simetría obligatoria (TODO documentado)
Si en el futuro se crea un `compute_over_profile_score` análogo al `compute_under_profile_score`, DEBE aplicar las reglas espejo contra `football_under_support` y `football_over_support` exactamente como se documentó en el header de `football_over_support.py`. Crear el módulo Over sin cross-check no es opcional — recrearía la asimetría que Phase F61 acaba de remover.

---

## Phase F62 — Discarded Match Scores24 External Review (COMPLETED)

### Objetivos
- ✅ Todo partido de fútbol descartado dispara una revisión externa contra Scores24 (corners + editorial únicamente, ignora ads/telegram/comments/player props).
- ✅ La revisión NO muta el bucket — solo adjunta un `scores24_review` con la decisión (`CONFIRM_DISCARD` | `MOVE_TO_WATCHLIST` | `RESCUE_ALTERNATIVE_MARKET`).
- ✅ Cost-control con cache Mongo (TTL pregame=12h, live=15min, postgame=24h) + quota diaria 40 + cap por run 10.

### Implementación
- ✅ **`backend/services/football_discarded_scores24_review.py`** (NUEVO, ~430 LOC, async, fail-soft):
  - `build_scores24_slug_candidates(match)` — generador determinístico (`m-DD-MM-YYYY-home-away-prediction` + swap). Respeta `scores24_url` explícita si está. Strip de acentos y normalización de fechas (ISO/DD-MM-YYYY/Unix timestamp).
  - `make_run_counter(limit)` + `_RunCounter` — cap por run para `SCORES24_DISCARDED_MAX_PER_RUN=10`.
  - `review_discarded_match_with_scores24(match, db, force, run_counter, discard_reason, scrape_fn)` — entry async principal.
  - Cache Mongo en `scores24_discarded_review_cache` con `expires_at` por status del partido.
  - Quota diaria atómica vía `find_one_and_update` en `scores24_discarded_quota` (rollback si excede).
  - Decision logic: corners → RESCUE, editorial → WATCHLIST, vacío → CONFIRM_DISCARD.
  - Env: `SCORES24_DISCARDED_REVIEW_ENABLED`, `SCORES24_DISCARDED_MAX_PER_RUN`, `SCORES24_DISCARDED_MAX_PER_DAY`, `SCORES24_PREMIUM_ENABLED`, `SCORES24_USE_BROWSER_API`.
- ✅ **`backend/server.py`** — nuevo endpoint REST `POST /api/football/discarded/{match_id}/review?force=false`:
  - Resuelve el match contra el snapshot más reciente.
  - Si no encuentra el match, igual intenta con un payload sintético (slug builder devuelve `URL_NOT_RESOLVED` y se honra fail-soft).
  - Verificado en vivo: `curl POST` devuelve el shape contractual sin romper.
- ✅ **`backend/services/analyst_engine.py`** — Cableado automático (solo football) después de Phase 13.6 market_trace:
  - Itera `discarded_motivation`, `discarded_market`, `incomplete_data`.
  - Respeta `MAX_PER_RUN=10` con `make_run_counter()` global por run.
  - Attacha `scores24_review` a cada entry sin mutar buckets.
  - Audit: `parsed._pipeline.discarded_scores24_review` con `attempted/reviewed/decisions/cap_per_run`.

### Tests
- ✅ `backend/tests/test_football_discarded_scores24_review_smoke.py` — **17 tests**:
  - Slug builder: happy path, acentos/espacios, URL explícita, missing fields, Unix timestamp.
  - Decision logic: RESCUE (corners), WATCHLIST (editorial), CONFIRM_DISCARD (vacío/scraper falla).
  - Env kill-switch (`SCORES24_DISCARDED_REVIEW_ENABLED=false`).
  - URL no resolvible → `SCORES24_URL_NOT_RESOLVED`.
  - Per-run quota → `SCORES24_QUOTA_RUN_EXCEEDED` después del 2do.
  - Cache hit (skip scraper) + force=True bypass.
  - Daily quota Mongo (con `_FakeDB`) → blocked al 3ro con MAX_PER_DAY=2.
  - Shape contract + engine_version.
- ✅ Pytest suite completa: **1864/1864 passing** (+17 nuevos, 0 regresiones).
- ✅ Backend service reiniciado + endpoint live verificado: `POST /api/football/discarded/test-fixture/review` devuelve el JSON contractual.

### Decisiones clave (confirmadas con usuario)
- **(1.c) Ambos** — cableado automático en analyst_engine + endpoint manual REST.
- **(2.a) Solo audit** — los buckets no se mutan; la UI lee `entry.scores24_review` para decidir cómo mostrar el partido (chip RESCATE/WATCHLIST/CONFIRMADO).
- **(3.a) Mongo** para rate limit diario y cache (sobrevive restarts).
- **(4) Solo slug determinístico** — sin SERP API (no se añade nueva integración paga).
- **(5.a) Cache propio Mongo** con TTL diferenciado por status del partido.

### Próximos pasos sugeridos
- (P2) **UI surface** del bloque `scores24_review` en las cards de descartados: chip RESCATE (verde) / WATCHLIST (amarillo) / CONFIRMADO (gris) + tooltip con `rescued_market`.
- (P3) Backtest del valor de los `RESCUE_ALTERNATIVE_MARKET` para validar que las recomendaciones de corners de Scores24 tienen ROI positivo.
- (P3) TTL index nativo en Mongo para `scores24_discarded_review_cache.expires_at` (purga automática).
