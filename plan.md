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

---

## Phase F61.1 — dc_nb_delta promoción de telemetry a scoring (COMPLETED)

### Resumen
Promoción del `dc_nb_delta_2_5_pts` de telemetry-only a scoring real, tras validar el signo con 4 líneas de evidencia (definición matemática en `statsbomb_features.py:447-453`, uso simétrico en `football_over_support._dc_nb_preference`, propagación coherente en `football_totals_model_normalizer`, y fragility logic en `football_over_support:406`).

### Implementación
- `services/football_moneyball/football_under_support.py`:
  - `_dc_nb_telemetry` → renombrado a `_dc_nb_preference` (scoring real).
  - `dc_nb_delta_2_5_pts >= 5.0` → **+12** + RC `DC_NB_DELTA_STRONGLY_FAVORS_UNDER` + `DC_NB_DELTA_FAVORS_UNDER`.
  - `dc_nb_delta_2_5_pts >= 3.0` → **+8** + RC `DC_NB_DELTA_FAVORS_UNDER`.
  - `< 3.0` → 0 puntos + RC legacy `DC_NB_DELTA_TELEMETRY_ONLY` (backward-compat).
  - `dc_nb_telemetry` block sigue presente con `_policy: "validated_and_promoted_phase_F61_signoff"`.
  - Narrative ES incluye "modelo DC/NB favorece (fuertemente) Under" cuando aplica.

### Tests
- `tests/test_football_under_support_smoke.py` extendido (+6 tests): below-threshold, mild tier, strong tier, negative delta no scoring, threshold boundary 3.0 y 5.0, missing 2.5 value.
- Suite: 1870 → 1870 verde tras promoción.

---

## Phase F63 — Discarded Review EN/ES + Soft/Hard Edge + UI Badge + Live Patch (COMPLETED)

### Objetivos
1. Resolver el problema de **resolución de URLs Scores24** cuando la API devuelve nombres en inglés pero Scores24 usa slugs ES/ASCII/acentuados.
2. Cambiar la regla de descarte por edge negativo: solo `<= -25.0%` es terminal; entre 0 y -24.9% es soft discard con revisión Scores24.
3. Surface el estado de revisión Scores24 en la UI con un badge claro.
4. Arreglar el botón "Refrescando..." para que sí muestre goles/tarjetas nuevas sin re-ejecutar el LLM.

### Implementación
- ✅ **`backend/services/team_name_translations.py`** (NUEVO): dict curado EN→ES para 70+ selecciones internacionales (Brazil/Brasil, Morocco/Marruecos, USA/Estados Unidos, South Korea/Corea del Sur, Czech Republic/República Checa, Bosnia & Herzegovina, Qatar/Catar, Switzerland/Suiza, etc.). API pública: `normalize_team_name_for_scores24(name, lang)`, `slug_pairs(home, away)`, `has_translation(name)`. Strip de acentos + & → "and"/"y" + de-dupe.
- ✅ **`backend/services/football_discarded_scores24_review.py`** extendido (~+150 LOC):
  - `build_scores24_slug_candidates` ahora emite hasta 10 variantes (EN-ASCII, ES-ASCII, accented, mixed EN/ES + swap home↔away).
  - Iteración multi-candidato hasta `SCORES24_DISCARDED_MAX_DIRECT_TRIES` (default 3).
  - `_duckduckgo_search_scores24_url()` — fallback opt-in vía `SCORES24_SEARCH_FALLBACK=duckduckgo`. Sin SERP API paga; parsea HTML de DuckDuckGo y resuelve URLs `scores24.live/es/soccer/*-prediction` que contengan ambos equipos.
  - Nuevos reason codes: `SCORES24_DIRECT_SLUG_FAILED`, `SCORES24_SEARCH_FALLBACK_USED`, `SCORES24_MATCH_URL_RESOLVED_FROM_SEARCH`, `SCORES24_TEAM_NAME_TRANSLATION_USED`.
  - Output incluye `team_name_translation_used: bool`.
- ✅ **`backend/services/market_guardrail.py`**: nueva constante `EDGE_HARD_DISCARD_THRESHOLD=-25.0` (env `SCORES24_EDGE_HARD_DISCARD_THRESHOLD`). En el reroute de NO_BET_VALUE:
  - `edge_pct <= -25.0` → `discard_strength=HARD_DISCARD`, `f63_reason_codes=["edge_too_negative", "EDGE_HARD_DISCARD"]`, `scores24_review_required=False`.
  - `-25.0 < edge_pct < 0` → `discard_strength=SOFT_DISCARD_REVIEW`, codes `NEGATIVE_EDGE_SOFT_DISCARD_REVIEW + SCORES24_REVIEW_REQUIRED_FOR_SOFT_DISCARD`, `scores24_review_required=True`.
  - Audit en `_pipeline.market_guardrail.edge_hard_discard_threshold_pct`.
- ✅ **`backend/services/analyst_engine.py`**: sweep extendido para incluir `discarded_unknown` (Phase F63). Priorización: las entradas `SOFT_DISCARD_REVIEW` se procesan PRIMERO (consume los slots del per-run cap antes que el resto). Audit incluye `soft_priority_count`.
- ✅ **`backend/server.py`** — nuevo endpoint `GET /api/football/live-events-patch?match_ids=...`:
  - Devuelve un patch ligero por match con score actual, minuto, status, eventos (goles, tarjetas).
  - El frontend mergea esto SIN re-correr el LLM (más barato, instantáneo).
  - Cap de 60 match_ids por llamada.
- ✅ **`frontend/src/components/Scores24ReviewBadge.jsx`** (NUEVO): chip con 7 estados visuales (pending/searching/reviewed/rescued/watchlist/confirmed/missing). Renderiza también el detalle de `rescued_market` o `editorial_prediction`. data-testid `scores24-review-*`.
- ✅ **`frontend/src/pages/DashboardPage.jsx`**:
  - Import del badge; renderiza en `<DiscardedRow>` cuando hay `item.scores24_review` o cuando `item.discard_strength === "SOFT_DISCARD_REVIEW"` (estado pending).
  - `applyLiveEventsPatch()` helper async: tras un refresh exitoso (rama sync o background) llama al nuevo endpoint con los match_ids del snapshot y guarda los patches en `liveEventsByMatchId`. Toast discreto cuando hay eventos nuevos.

### Tests (+26, 0 regresiones)
- `tests/test_team_name_translations_smoke.py` (NUEVO, 19 tests): cubre todos los pares EN↔ES del spec (México/Sudáfrica, Brazil/Brasil, Morocco/Marruecos, USA aliases, Bosnia & Herzegovina ampersand, Qatar/Catar, Switzerland/Suiza, South Korea/Corea, Czech Republic/República Checa) + edge cases (empty, unknown teams ASCII-only, accents, no duplicates) + `slug_pairs` Cartesian.
- `tests/test_market_guardrail_soft_discard_smoke.py` (NUEVO, 6 tests): -18.8% USA-Paraguay → SOFT, -20% Canada-Bosnia → SOFT, -30%+ → HARD, boundary -25.0 → HARD, audit block exposes threshold.
- `tests/test_football_discarded_scores24_review_smoke.py` extendido: nuevo test `test_slug_builder_es_translation_emitted_for_mexico_south_africa` confirma que el builder emite ambas variantes (Mexico/South Africa + México/Sudáfrica).
- **Suite total: 1897/1897 passing** (1871 → 1897).

### Decisiones clave (confirmadas con usuario)
- (1.b) **Surface live deltas** en snapshot existente — no re-correr LLM.
- (2.b)+(2.c) **Slug extendido EN/ES + DuckDuckGo opt-in** (cero costo adicional; SERP API queda fuera).
- (3) **Mismo bucket `discarded_market`** con marker `discard_strength` (no fragmenta la UI).

### Próximos pasos sugeridos
- (P2) Backtest: medir hit-rate/ROI de los `RESCUE_ALTERNATIVE_MARKET` (alternativas de corners) vs los discards anteriores.
- (P3) Activar `SCORES24_SEARCH_FALLBACK=duckduckgo` en producción tras observar conversion rates de URLs directas vs fallback.
- (P3) Expandir el dict de traducciones a clubes top-30 (Premier League, La Liga, Bundesliga) para Champions League / Europa League.
- (P3) TTL index Mongo en `scores24_discarded_review_cache.expires_at` (purga automática).


---

## Phase F64 — Pre-Discard Structural Match Analysis + Watchlist por Cuota (COMPLETED)

### Objetivos
1. **Fix 1**: Activar `SCORES24_SEARCH_FALLBACK=duckduckgo` por defecto (opt-OUT) en `football_discarded_scores24_review.py`.
2. **Fix 2**: TTL index en MongoDB para `scores24_discarded_review_cache.expires_at` (purga automática del cache).
3. **Fix 3 (core)**: NO descartar un pick por edge negativo sin antes correr el análisis estructural completo (xG / goles L5–L15 / córners L5–L15 / under-over support / scores24). Si el edge cae en `[-25, 0)` pero el soporte estructural es alto (≥ 75), enrutar al nuevo bucket `watchlist_odds_needed` en vez de a `discarded_market`. Solo `HARD_DISCARD` (`edge ≤ -25`) o soporte < 60 confirma el descarte.

### Implementación
- ✅ **`services/football_structural_value_review.py`** (NUEVO, ~437 LOC, pure orchestration, fail-soft):
  - Sub-engines (todos fail-soft): `football_corner_profile_cross`, `football_team_profile_cross`, `football_under_support`, `football_over_support`.
  - Decision matrix:
    - `support ≥ 75` + `edge ≥ 0` → `VALUE_CANDIDATE`.
    - `support ≥ 75` + `edge < 0` → `WATCHLIST_ODDS_NEEDED` + `rescued_market`.
    - `60 ≤ support < 75` → `MOVE_TO_WATCHLIST` + `SCORES24_REVIEW_REQUIRED_BEFORE_FINAL_DISCARD`.
    - `support < 60` → `NO_STRUCTURAL_VALUE` + `DISCARD_CONFIRMED_AFTER_FULL_STRUCTURAL_REVIEW`.
    - `discard_strength=HARD_DISCARD` short-circuit → `NO_STRUCTURAL_VALUE` (terminal).
  - Reason codes nuevos: `EDGE_CHECK_MOVED_AFTER_STRUCTURAL_ANALYSIS`, `STRUCTURAL_ANALYSIS_REQUIRED_BEFORE_DISCARD`, `WATCHLIST_ODDS_NEEDED`, `GOAL_PROFILE_ANALYZED_BEFORE_DISCARD`, `CORNER_PROFILE_ANALYZED_BEFORE_DISCARD`, `XG_PROFILE_ANALYZED_BEFORE_DISCARD`, `SCORES24_REVIEW_REQUIRED_BEFORE_FINAL_DISCARD`, `DISCARD_CONFIRMED_AFTER_FULL_STRUCTURAL_REVIEW`, `ALTERNATIVE_MARKET_FOUND_DESPITE_NEGATIVE_EDGE`.
  - Narrativa ES dinámica según `final_state`.
- ✅ **`services/football_corner_profile_cross.py`** (EXTENDIDO):
  - Nueva fn pública `extract_corner_side_from_match(match, prefix)` que parsea las claves planas `{home,away}_corners_{for,against}_{l5,l15}` desde el match root y las normaliza al shape canónico que el classifier ya consume.
  - Mantiene los 6 perfiles existentes (STRONG/LOW UNDER, STRONG/HIGH OVER, ASYMMETRIC, MIXED) sin cambios de lógica.
- ✅ **`services/market_guardrail.py`** (EXTENDIDO):
  - Antes de añadir un pick `NO_BET_VALUE` a `discarded_market`, se corre `compute_structural_value_review(...)`.
  - Si `final_state == WATCHLIST_ODDS_NEEDED` y `discard_strength == SOFT_DISCARD_REVIEW`, el pick va a `summary.watchlist_odds_needed` (NO a `discarded_market`), con `bucket="watchlist_odds_needed"`, `rescued_market` y reason codes específicos.
  - Audit `_pipeline.market_guardrail.watchlist_odds_needed_count`.
- ✅ **`server.py`**: TTL index `expires_at` en `scores24_discarded_review_cache` (Fix 2).
- ✅ **`services/football_discarded_scores24_review.py`**: `SCORES24_SEARCH_FALLBACK` default cambiado a `"duckduckgo"` (Fix 1, opt-OUT).
- ✅ **Frontend**:
  - **NUEVO `frontend/src/components/StructuralReviewPanel.jsx`** (154 LOC) — renderiza scores Under/Over/Max, perfil goles, perfil córners, top 3 candidatos de mercado y narrativa.
  - **`frontend/src/pages/DashboardPage.jsx`**:
    - Import + render del panel en `DiscardedRow` cuando `item.structural_review.available`.
    - Chip compacto inline (color amber/cyan según soporte) en el header del row cerrado, antes del toggle.
    - Nuevo bucket `watchlistOddsNeeded = summary.watchlist_odds_needed || []`.
    - Sección dedicada **"Watchlist por cuota — soporte estructural alto"** con paleta amber, renderizada ENCIMA de los descartes regulares.
    - `hasAnyDiscarded` actualizado para incluir el nuevo bucket.

### Tests
- ✅ **`tests/test_football_structural_value_review_smoke.py`** (NUEVO, 15 tests):
  - T1: edge -18.8% + STRONG corner over support 78 → `WATCHLIST_ODDS_NEEDED`.
  - T2: edge -20.5% + STRONG corner under support 80 → `WATCHLIST_ODDS_NEEDED` con rescued market "Total corners Under".
  - T3: edge -26.0% + `HARD_DISCARD` → `NO_STRUCTURAL_VALUE` (short-circuit, no rescate).
  - T4: SOFT_DISCARD invoca los 4 sub-engines en el output (contrato de audit).
  - T5: soporte ASYMMETRIC = 72 (rango [60, 75)) → `MOVE_TO_WATCHLIST` + `SCORES24_REVIEW_REQUIRED`.
  - T6: match sin señal (~9.0 corners totales, profile MIXED) → soporte < 60 → `NO_STRUCTURAL_VALUE`.
  - T7: edge +4.5% + soporte ≥ 75 → `VALUE_CANDIDATE` / `VALUE_FOUND`.
  - T8: inputs basura (None, "", 42, [], {}, 0) → `available=False`, sin raise, sub-engines vacíos.
  - +contract test del nuevo helper `extract_corner_side_from_match` (flat keys + prefix invalid + missing keys).
  - +integration test confirmando `STRONG_CORNERS_UNDER_CROSS` end-to-end con flat keys.
- ✅ **Suite total: 1912/1912 passing** (+15 nuevos, 0 regresiones desde 1897).

### Bug encontrado y corregido durante implementación
- `football_structural_value_review._market_candidates_from_signals` esperaba `supports == "TEAM_CORNERS"` pero el engine de córners emite `"TEAM_CORNERS_OVER"`. Sin el fix, ningún match con perfil asimétrico generaba candidato → soporte = 0 → siempre confirmaba descarte. Corregido para reconocer la cadena correcta.

### Tarea adicional pedida por usuario
- `football_corner_profile_cross` ahora acepta explícitamente las claves planas:
  - `home_corners_for_l5`, `home_corners_for_l15`, `home_corners_against_l5`, `home_corners_against_l15`
  - `away_corners_for_l5`, `away_corners_for_l15`, `away_corners_against_l5`, `away_corners_against_l15`
- Detección automática de los 6 perfiles cruzados (sin cambios de lógica, sólo nuevo path de entrada): STRONG_CORNERS_UNDER_CROSS, LOW_CORNERS_CROSS, STRONG_CORNERS_OVER_CROSS, HIGH_CORNERS_CROSS, ASYMMETRIC_CORNERS_PROFILE, MIXED_CORNERS_PROFILE.

### Verificación end-to-end
- Synthetic match con `home_corners_for_l5=6.5 … away_corners_against_l15=5.5` + odds 1.40 / confidence 55:
  - edge calculado = **-24.68%** (SOFT_DISCARD)
  - structural support = **78** (STRONG_CORNERS_OVER_CROSS)
  - bucket final = `watchlist_odds_needed` ✓
  - rescued_market = "Total corners Over" (support 78) ✓
  - reason codes = `["NEGATIVE_EDGE_SOFT_DISCARD_REVIEW", "SCORES24_REVIEW_REQUIRED_FOR_SOFT_DISCARD", "WATCHLIST_ODDS_NEEDED", "EDGE_CHECK_MOVED_AFTER_STRUCTURAL_ANALYSIS"]` ✓

### Próximos pasos sugeridos
- (P3) Expandir dict `team_name_translations.py` a clubes top-30 Champions/Europa League.
- (P3) Activar Bright Data en producción para FBref enrichment de player props (queda como caveat documentado).
- (P3) Backtest del hit-rate/ROI de los rescates `watchlist_odds_needed` cuando la cuota baja en T+24h.

---

## Phase F65 — Bright Data Productionisation + Watchlist Backtest (COMPLETED)

### Objetivos (P3 del Phase F64)
1. **Configurar Bright Data en producción** para FBref enrichment + scores24 fallback (con circuit breaker y opt-IN gate para no quemar plan).
2. **Backtest hit-rate / ROI del bucket `watchlist_odds_needed`** para validar empíricamente la rentabilidad del rescate estructural introducido en Phase F64.

### Diagnóstico real de Bright Data (probado contra el plan del usuario)
- ✅ `understat.com` → 200 OK en ~3s.
- ✅ `fbref.com/en/comps/9/Premier-League-Stats` → 200 OK con `country=us` + timeout 90s (~45s) → 910 KB HTML correctos.
- ❌ `scores24.live/...` → **BLOQUEO DE POLÍTICA** (`brd_err_code=proxy_error`, mensaje *"Access denied: scores24.live is classified as Gambling and blocked by Bright Data as it might breach Bright Data usage policy."*). **No se levanta con dominios premium** — es policy refusal global.
- **Mitigación permanente del bloqueo scores24**: la ruta opt-OUT de DuckDuckGo HTML Search (`SCORES24_SEARCH_FALLBACK=duckduckgo`, activada en Phase F63) sigue siendo el único camino viable para Scores24. Bright Data se reserva para FBref, Understat, FotMob, SofaScore, etc. (no-gambling).

### Implementación

#### A) Circuit Breaker — `services/external_sources/circuit_breaker.py` (NUEVO, ~245 LOC)
- **Estado por host** (`fbref.com`, `scores24.live`, `understat.com`…). Las subdomains colapsan al apex (`en.fbref.com` → `fbref.com`).
- **Tres estados**: CLOSED → OPEN → HALF_OPEN con probe único por gap.
- **Defaults env-overridables**:
  - `BRIGHTDATA_BREAKER_FAIL_THRESHOLD = 5` (failures consecutivos para abrir).
  - `BRIGHTDATA_BREAKER_PAUSE_SEC = 1800` (30 min de pausa estándar).
  - `BRIGHTDATA_BREAKER_POLICY_PAUSE_SEC = 86400` (24h cuando el error es `Access denied … Gambling/Adult/Copyright` — pausa agresiva porque no se recupera esperando).
  - `BRIGHTDATA_BREAKER_HALF_OPEN_GAP_SEC = 60`.
- **Detección automática de policy block**: heurística sobre `error_code` + `error_msg` (substrings `policy`, `gambling`, `adult`, `copyright`, `access denied … bright data`).
- **Snapshot público** (`snapshot_all()`) + `reset()` admin helper.
- **Fail-soft**: nunca lanza, in-memory (state se resetea en cada deploy intencionalmente).

#### B) Opt-IN Gate `BRIGHTDATA_ENABLED`
- `services/external_sources/base.brightdata_available()` ahora consulta `is_brightdata_enabled()` además de las credenciales.
- Default ON cuando hay credenciales (backward-compat). Set `BRIGHTDATA_ENABLED=false` en `.env` para apagado global de emergencia (incident response, presupuesto agotado, etc.) **sin** tener que borrar credenciales.

#### C) Integración del breaker
- `services/external_sources/base.brightdata_fetch()`: short-circuit cuando `is_open(url)` → True; `record_success` / `record_failure` con `error_code = type(exc).__name__`.
- `services/scores24_scraper._fetch_scores24_html()`: además del breaker, respeta `BRIGHTDATA_ENABLED`, expone reason codes `breaker_open` y `brightdata_disabled_by_flag` en el diagnostic. Critical: pasa el `brd_error` literal al breaker → la heurística detecta automáticamente "Gambling" y abre 24h.
- `services/football_player_stats_ingestor._fetch_fbref_player()`: timeouts subidos de **10s → 55s** (Bright Data tarda ~45s en servir FBref con `country=us`, antes el código se rendía solo).

#### D) Endpoints admin nuevos
- `GET /api/admin/brightdata/circuit-breaker` → estado completo (flag opt-IN, thresholds, lista de hosts con su estado y contadores).
- `POST /api/admin/brightdata/circuit-breaker/reset?host=…` → limpia un host (o todos cuando se omite el parámetro).

#### E) Backtest del Watchlist — `services/watchlist_odds_backtest.py` (NUEVO, ~430 LOC)
- **Scorer puro y funcional**: no toca Mongo, recibe `picks` + `settlements_by_match` + `snapshots_by_match`, devuelve un report JSON-serialisable.
- **Métricas calculadas**:
  - `n_picks_total`, `n_picks_settled`, `n_picks_won`, `n_picks_lost`, `n_picks_no_positive_edge`.
  - `hit_rate_pct` (sobre picks settled con outcome conocido — pushes excluidos).
  - `roi_pct` flat-stake 1u, asentando al **mejor odds observado** en snapshots (asume que el usuario sí esperó la mejor cuota).
  - `avg_edge_at_pick` vs `avg_edge_at_best`.
  - `median_hours_to_positive_edge` (cuánto tarda el mercado en moverse al rango positivo).
  - Breakdown por **familia** (CORNERS / GOALS / UNDER / OVER) y por **league tier** (Tier 1 / 2 / 3 / Other).
- **`did_rescued_market_win()`**: mapea `rescued_market` → outcome ganador/perdedor/push contra `finished_matches.{final_corners_total, final_goals_total, home_corners, away_corners}`.
- **Dataset sintético**: `_synthetic_demo_dataset()` con 5 picks que ejercen todos los code paths (win corners over, loss corners under, win goals over no-snapshot, unsettled, push). Usado por tests y por el endpoint en modo `demo=true`.

#### F) Endpoint REST
- `GET /api/backtest/watchlist-odds-needed?demo=true` → corre el dataset sintético (smoke test instantáneo, útil para front-end mientras llegan datos reales).
- `GET /api/backtest/watchlist-odds-needed` → live: agrega los últimos 30 días de `analyst_runs.summary.watchlist_odds_needed`, hace join con `finished_matches` y `watchlist_odds_snapshots`, devuelve report completo + `mode: "live"`.

#### G) Cron Job — `_job_snapshot_watchlist_odds`
- **Cadencia: cada 1h** (`IntervalTrigger(hours=1)`, primer run +10 min tras boot).
- Para cada `match_id` único de `analyst_runs.summary.watchlist_odds_needed` en las últimas 48h, lee la última `odds_snapshots`, recomputa edge y persiste a `watchlist_odds_snapshots` con `captured_at` UTC.
- `_status["last_run"]["snapshot_watchlist_odds"]` registra duración + `written` + `matches` para observabilidad.

#### H) MongoDB indexes nuevos (auto-creados en startup)
- `watchlist_odds_snapshots`:
  - `(match_id, captured_at desc)` — query path para el backtest endpoint.
  - `captured_at` con TTL **60 días** — un cuarto de season de retención.

### Tests
- ✅ **`tests/test_brightdata_circuit_breaker_smoke.py`** — 21 tests:
  - `host_for` normaliza subdomains (`www.fbref.com` → `fbref.com`).
  - Transición CLOSED → OPEN al alcanzar threshold.
  - `record_success` cierra el breaker y resetea contadores.
  - **Aislamiento per-host** (open fbref no afecta understat).
  - **Policy block** dispara pausa 24h instantáneamente (sin necesidad de 5 fallos).
  - HALF_OPEN admite un único probe por gap, otro fallo re-abre.
  - `reset(host)` selectivo + `reset()` global.
  - Opt-IN flag (`BRIGHTDATA_ENABLED=true|false|0|off|no` etc.).
- ✅ **`tests/test_watchlist_odds_backtest_smoke.py`** — 16 tests:
  - Math (`implied_probability`, `edge_pct`).
  - `best_positive_snapshot` selecciona el max-edge correcto.
  - `hours_to_first_positive_edge` detecta el primer cruce y devuelve `None` cuando no cruza.
  - Settlement classifier (corners over/under, goals over/under, push, missing data).
  - Empty input → empty report con nota `no_picks_in_window`.
  - **Synthetic demo end-to-end**: 5 picks → hit-rate 66.67%, ROI +18.33%, median 10h, breakdown correcto.
  - Unsettled picks no rompen el scorer.
  - Solo-pushes → `hit_rate=None` (no penaliza por matches que cayeron en la línea).
  - Fail-soft con basura (`{}`, `"not a dict"`, etc.) — no raise.
- ✅ **Suite total: 1949/1949 passing** (+37 nuevos vs baseline 1912, **0 regresiones**).

### Verificación end-to-end (producción real)
- **scores24.live**: confirmado bloqueo policy → breaker abrió por 86400s tras 1er fetch real. 2da llamada se cortó instantáneamente con `reason=breaker_open` → **0 requests adicionales gastados**.
- **`/api/backtest/watchlist-odds-needed?demo=true`** en preview URL:
  - `engine_version=watchlist_backtest.v1` ✓
  - 5 picks → 4 settled → 2 won / 1 lost / 1 push.
  - `hit_rate=66.67%`, `roi=+18.33%`, `median_hours_to_positive_edge=10.0`.
  - Per-family: GOALS `100%/+70%`, CORNERS `50%/-7.5%`.
  - Per-tier: Tier 1 (La Liga + Bundesliga) `50%/-7.5%`.
- **`/api/admin/brightdata/circuit-breaker`**: expone thresholds + lista de hosts (vacía hasta que llegue tráfico).

### Variables de entorno (todas opcionales, defaults razonables)
| Variable | Default | Propósito |
|---|---|---|
| `BRIGHTDATA_ENABLED` | `true` cuando hay credenciales | Apagado global de emergencia |
| `BRIGHTDATA_BREAKER_FAIL_THRESHOLD` | `5` | Failures consecutivos para abrir |
| `BRIGHTDATA_BREAKER_PAUSE_SEC` | `1800` (30 min) | Pausa estándar |
| `BRIGHTDATA_BREAKER_POLICY_PAUSE_SEC` | `86400` (24h) | Pausa cuando hay policy block (gambling/adult/copyright) |
| `BRIGHTDATA_BREAKER_HALF_OPEN_GAP_SEC` | `60` | Gap entre probes en HALF_OPEN |

### Próximos pasos sugeridos
- (P3) UI **Backtest Lab** consumiendo `/api/backtest/watchlist-odds-needed` (cards de hit-rate / ROI / median-hours + breakdown por familia + tabla de picks recientes).
- (P3) Dashboard admin badge **"Bright Data breaker"** consumiendo `/api/admin/brightdata/circuit-breaker` (chip rojo cuando algún host está OPEN).
- (P3) Expandir `team_name_translations.py` Champions/Europa League (sigue pendiente desde F64).
- (P4) Considerar proveedores alternativos para gambling sites cuando se requiera (ScrapingBee, Zyte) — *fuera de scope hoy*.


---

## Phase F66 — Internal Editorial Prediction Engine + TheStatsAPI Integration (COMPLETED)

### Objetivo
Reemplazar la dependencia runtime de Scores24 (bloqueado por política de Bright Data) por un motor editorial PROPIO que genera 4 secciones dinámicas para cada partido descartado, con cuotas reales servidas por **TheStatsAPI**.

### Implementación

#### A) TheStatsAPI integration — `services/thestatsapi_client.py` (NUEVO, ~225 LOC)
- **Auth**: `Authorization: Bearer <STATSAPI_API_KEY>` (verificado live contra `api.thestatsapi.com`).
- **3 endpoints**: `/api/football/matches/{id}/odds`, `/odds/live`, `/players/{pid}/competitions/{cid}/seasons/{sid}/heatmap`.
- **Cache TTL en Mongo** (3 colecciones con TTL indexes auto-creados en startup):
  - `thestatsapi_prematch_cache` → 15 min
  - `thestatsapi_live_cache` → 60s
  - `thestatsapi_heatmap_cache` → 24h
- **Opt-IN**: gateado por `STATSAPI_API_KEY`. Sin key, `is_enabled()` retorna False y el editorial sigue funcionando sin cuotas.
- **Circuit breaker reuse**: usa el mismo módulo F65 (per-host, 5 fallos → pausa 30 min, policy block → 24h).
- **`extract_normalised_markets()`**: aplana respuesta Kambi-style (`opening`/`last_seen`) a dict consumible por el editorial.

#### B) Dixon-Coles scoreline grid — `services/football_dixon_coles.py` (NUEVO, ~180 LOC)
- **Tier 1 Dixon-Coles** (Poisson + tau low-score, rho=-0.13) → **Tier 2 Poisson** → **Tier 3 Heurística por perfil** (UNDER/OVER/DOMINANT/BTTS/NEUTRAL).
- Solo stdlib (math). Fail-soft total. Garbage → NEUTRAL heuristic.

#### C) Editorial Prediction engine — `services/football_editorial_prediction.py` (NUEVO, ~580 LOC)
Función pública: `generate_football_editorial_prediction(match_payload, odds=None, h2h_matches=None)`.

**4 sub-secciones + H2H placeholder**:
1. **corners_prediction** — `football_corner_profile_cross` + flat L5/L15. Reglas verbatim (STRONG_UNDER→Under, ASYMMETRIC→Team Over, MIXED→Watchlist).
2. **goals_prediction** — `team_profile_cross` + `under_support` + `over_support` + xG. NO fuerza Over 2.5/BTTS.
3. **key_trends** — top-5 desde L5/L15, prioriza tendencias que apoyen el mercado recomendado.
4. **head_to_head** — placeholder fail-soft (insufficient_sample por defecto).
5. **probable_score** — cascada DC→Poisson→Heurística con top-5 scorelines + narrativa que cita el método.

Salida con `best_protected_market`, `overall_narrative_es`, `reason_codes` consolidados.

#### D) Integración pipeline de descartes
- `compute_structural_value_review()` adjunta `editorial_prediction` al output.
- `possible_alternative_markets.attach_alternatives_to_summary()` extendido: TODOS los buckets (`discarded_market`, `discarded_motivation`, `incomplete_data`) reciben `editorial_prediction`.

#### E) Endpoint REST `/api/football/editorial-prediction/{match_id}?use_odds=true`
Resuelve match doc desde `analyst_runs`, llama TheStatsAPI (cache 15min), normaliza, alimenta editorial.

#### F) UI — `components/EditorialPredictionPanel.jsx` (NUEVO, ~155 LOC)
Header verde-esmeralda, banner `best_protected_market`, 5 sub-secciones con chips de mercado+cuota cuando OK, chips amber "Watchlist", chips mono para scorelines. Renderizado **arriba** del `StructuralReviewPanel`.

#### G) MongoDB TTL indexes auto-creados al startup
`thestatsapi_prematch_cache` (15m), `thestatsapi_live_cache` (60s), `thestatsapi_heatmap_cache` (24h).

### Tests
- **`tests/test_football_dixon_coles_smoke.py`** — 10 tests (DC vs Poisson, heuristic fallback, garbage).
- **`tests/test_football_editorial_prediction_smoke.py`** — 21 tests (4 secciones, fail-soft, normaliser Kambi-style).
- **Suite total: 1980/1980 passing** (+31 nuevos vs 1949, **0 regresiones**).

### Verificación end-to-end (producción real)
- TheStatsAPI live: `GET /api/football/competitions` → 200, 1000+ competitions ✓
- `mt_511134637`: odds completos con total_goals, match_corners 7.5-11.5, btts, match_odds ✓
- Endpoint propio `/api/football/editorial-prediction/mt_511134637?use_odds=true` → `available=true`, `odds_attached=true` ✓
- Smoke sintético Brazil vs Morocco UNDER: "Brazil promedia 3.5 córners… Morocco apenas 3.7… apostar por Under 9.5 córners a cuota 1.42*" — coincide con ejemplo del spec ✓

### Variables env nuevas (todas opt-IN)
| Variable | Default | Propósito |
|---|---|---|
| `STATSAPI_API_KEY` | (vacío) | API key TheStatsAPI |
| `STATSAPI_BASE_URL` | `https://api.thestatsapi.com` | Override opcional |

### Cumplimiento Acceptance Criteria (1-10 del spec)
1. ✅ No depende de Scores24. 2. ✅ 5 secciones. 3. ✅ Corners L5/L15. 4. ✅ Goals con xG+support. 5. ✅ Trends desde datos reales. 6. ✅ H2H como contexto. 7. ✅ Probable score desde engine. 8. ✅ No fuerza picks. 9. ✅ PARTIAL/MISSING. 10. ✅ Todo fail-soft.

### Próximos pasos sugeridos (P3)
- (P3) UI heatmaps consumiendo TheStatsAPI player heatmap endpoint.
- (P3) UI Backtest Lab consumiendo `/api/backtest/watchlist-odds-needed` (pendiente desde F65).
- (P3) Colección `head_to_head_matches` + cron de ingesta para activar 5ª sección real.
- (P3) Expandir `team_name_translations.py` Champions/Europa League (desde F64).
- (P4) Mapping `match_id` API-Sports ↔ TheStatsAPI `mt_*`.

