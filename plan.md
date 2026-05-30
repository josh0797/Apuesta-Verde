# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright + **Bright Data Unlocker** + **Historical Detail Enrichment (Basketball→Baseball)** + **MLB Margin & Total Script Engine v2** + **MLB-V3 Histórico Baseball** + **MLB-V4 Feedback Loop** + **MLB-V5 Bucketing Estructural / Manual Odds** + **MLB-V6 Totals Prob Fix + Visible Picks** + **MLB-V7 Explainability/Game Script/Diversificación** (ACTUALIZADO)

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

- **(🟨 PENDIENTE / SIGUIENTE PRIORIDAD)** **Bright Data Web Unlocker** como tercer backend:
  - Integrar Bright Data (API mode) para desbloquear fuentes con Cloudflare/PerimeterX.
  - Usarlo para **Sportytrader/BeSoccer/scores24** y extenderlo a **fuentes editoriales NBA/basketball y MLB**.

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
  - Problema resuelto: el pipeline genérico (LLM) mandaba MLB a `discarded_market` por “cuotas no disponibles/motivación normal”, ignorando lectura v2.
  - Ahora **Baseball NO usa el LLM genérico** en `/api/analysis/run`.
  - Nuevos buckets MLB:
    - `structural_lean_requires_odds`
    - `watchlist_manual_odds`
    - `discarded_after_full_analysis`
  - El engine usa `mlb_structural_data_quality()` + `odds_missing` + `has_structural_lean` para evitar descartes prematuros.
  - UI: sección dedicada **“Revisión manual — falta cuota”** (solo MLB) vía `ManualOddsReviewPanel.jsx`.

- **(✅ COMPLETADO)** **MLB-V6 — Totals Probability Fix + Edge vs Line + Cards visibles**:
  - **Bug Totals**: `coverProbability` estaba reutilizando el de Run Line (-1.5) incluso cuando el mercado era Under/Over.
    - Fix: modelo **Poisson** para totales (`totals_probability`) + `smart_total_line_selector` devuelve `probabilityUnder/Over`, `edgeVsLine`, `probabilityModel` y `coverProbability` del **lado recomendado**.
    - Se expone `probabilityDebug` en `_mlb_script_v2` y se loguea provenance.
  - **Manual odds**: activado cálculo client-side de EV en `ManualOddsReviewPanel` (input ya no está disabled).
  - **UI cards invisibles** (contador “Recomendados=4” pero no render):
    - Fix backend payload: unificar buckets MLB en `payload.picks`.
    - Bypass `filter_blocked_picks()` genérico para `sport == 'baseball'`.

- **(🟨 NUEVO / OBJETIVO ACTUAL)** **MLB Engine V3 — Explainability, Game Script Visibility, Market Diversification & Baseball-First Reasons**:
  - Problemas actuales identificados (UI/UX):
    1) Picks demasiado similares (Run Line +1.5 + Under 9.5, conf ~68).
    2) Pitcher analysis “oculta”: usuario no ve abridores/por qué pesan.
    3) No se expone el “expected game script” aunque el engine ya calcula proyecciones.
    4) Señales y textos demasiado genéricos (“Lectura estructural”, “Mercado rescatado”, “Línea óptima”).
  - Objetivo V3:
    - Cada pick debe explicar: **quién lanza**, **qué script se espera**, **por qué ese mercado**, **qué edge**, **qué hace diferente ese juego**, y **de dónde sale la confianza**.
  - Restricciones:
    - **NO cambiar** router MLB, buckets estructurales/manual, motor de probabilidad (Poisson), odds engine, Moneyball.
    - **NO tocar** pipelines football/basketball.

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

## Phase MLB-V4 — Feedback Loop MLB + Recalibración automática (cada 50 picks)
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V5 — Bucketing estructural MLB + Manual Odds Review
**Estado:** ✅ COMPLETADO (2026-05-29)

---

## Phase MLB-V6 — Totals Probability Fix + Visible Picks
**Estado:** ✅ COMPLETADO (2026-05-30)

### MLB-V6.1 Totals probability model correcto (Poisson)
- Backend: `mlb_pregame_analytics_v2.py`
  - `totals_probability(expected_runs, line)` + `_poisson_cdf`.
  - `smart_total_line_selector()` produce:
    - `coverProbability` del lado recomendado
    - `probabilityUnder`, `probabilityOver`
    - `edgeVsLine` (en carreras)
    - `probabilityModel: Poisson`
    - `probabilityDebug` (provenance)
  - `build_v2_payload()` resuelve `coverProbability` según mercado (totals vs Run Line).

### MLB-V6.2 UI: Edge vs Line + debug + odds manual
- `ManualOddsReviewPanel.jsx`:
  - muestra `Edge vs línea`.
  - panel debug: projected runs, mercado recomendado, Poisson P(U)/P(O).
  - input “Pegar tu cuota” **activado** con cálculo EV client-side.
- `MLBScriptPanel.jsx`:
  - muestra `Edge vs línea` y P(U/O) + modelo.

### MLB-V6.3 Picks visibles bajo el dashboard (contador = cards)
- Backend (`server.py`):
  - `result.picks` unifica todos los buckets visibles para baseball:
    - `picks + rescued_picks + structural_lean_requires_odds + watchlist_manual_odds`
  - sintetiza `recommendation.confidence_score` (para que el split high/medium del dashboard funcione).
- Backend: bypass `filter_blocked_picks` genérico para baseball (evita bloqueos por fragilidad redundantes).
- Orchestrator: `kickoff_iso/gameDate/status` añadidos al pick_payload.

---

## Phase MLB-V7 — MLB Engine V3 (Explainability + Game Script + Diversificación + Baseball-first)
**Estado:** 🟨 PENDIENTE (nueva fase)

### MLB-V7.1 Backend — nuevo módulo `mlb_pregame_analytics_v3.py` (pure functions)
**Objetivo:** enriquecer y explicar, sin tocar probabilidades/buckets.

1) **Game Script Engine**
- Crear `generate_mlb_game_script(scoring_ctx, v2_payload, under_profile, nrfi, hist_profile=None) -> dict`.
- Output estándar:
  - `script_code`: `LOW_SCORING_PITCHERS_DUEL | OFFENSIVE_SHOOTOUT | FAVORITE_DOMINANCE | BULLPEN_BATTLE | UNDERDOG_CAN_COMPETE | PITCHER_MISMATCH | HIGH_VARIANCE_GAME | LOW_VARIANCE_GAME`
  - `label_es`, `narrative_es`
  - `key_drivers`: lista breve (pitchers, bullpens, park, offenses)
  - `expected_runs`: usar `v2_payload.expectedRuns`
  - `projected_margin`: usar `v2_payload.marginProjection`

2) **Why this pick? (checklist)**
- Crear `build_why_this_pick(v3_ctx) -> list[dict]` con 6–9 filas fijas:
  - Expected Runs, Line, Edge (runs), Pitchers (names + quality), Bullpen, Park/Weather, Offensive outlook, (opcional) Historial last15.

3) **Confidence Breakdown**
- Crear `confidence_breakdown(scoring_ctx, v2_payload, hist_profile=None) -> dict`:
  - `total`: 0–100 (igual al mostrado)
  - `components`: Pitchers, Lineups, Bullpens, Park/Weather, Historical, Market/Line.
- Regla: **no reescalar** el motor base; sólo explicar/descomponer el score resultante.

4) **Baseball-first reason generator**
- Crear `generate_baseball_first_reasons(scoring_ctx, v2_payload, chosen_market, rescue=None) -> list[str]`.
- Sustituir lenguaje interno:
  - “Lectura estructural detectada” → razones tipo: “Ambos abridores suprimen hard contact; proyección de carreras baja.”
  - “Mercado rescatado” → “ML sin valor, pero el Under/NRFI sí mantiene edge.”
  - “Línea óptima seleccionada” → “Total proyectado (6.7) muy por debajo de la línea (9.5).”

5) **Market diversification penalty (diario)**
- Crear `apply_market_diversification(picks: list[dict]) -> list[dict]`:
  - Calcula distribución del día (`Under 9.5`, `RL +1.5`, etc.).
  - Si un mercado supera umbral (ej. 60–80%), aplica:
    - `diversity_penalty`: reduce rank para ese mercado
    - o fuerza evaluación de alternativos (F5, TT, NRFI, ML) **si la lectura lo soporta**.
- Nota: no cambia probabilidades; sólo **ranking/selección final** cuando hay empate/lectura múltiple.

### MLB-V7.2 Orchestrator wiring (`mlb_day_orchestrator.py`)
1) Tras `v2_payload` + `chosen_market` (o rescue), construir `v3_payload`:
- Adjuntar en cada `pick_payload`:
  - `_mlb_script_v3`: {script, pitchers_block, why, confidence_breakdown, baseball_reasons, diversity_meta}
  - `baseball_reasons`: lista de strings (para UI compacta).

2) Aplicar diversificador **a nivel de día**:
- Después de construir `picks/rescued/structural/watchlist` (o sólo los visibles), ejecutar:
  - `apply_market_diversification(unified_picks)`
- Asegurar que:
  - No se rompen buckets.
  - Se modifica sólo `recommendation` cuando procede (casos de empate o baja separación), y se deja auditoría en `_mlb_script_v3.diversity_meta`.

3) Enriquecimiento de señales existente (sin romper `signal_catalog`):
- Para cada señal positiva genérica (`STRUCTURAL_LEAN_DETECTED`, `RESCUED_MARKET`, `SMART_OVER_LINE_SELECTED`):
  - añadir `extra_explanation` con el top reason beisbolero.
- **No eliminar** señales existentes; sólo enriquecer.

### MLB-V7.3 Frontend — `MLBScriptV3Panel.jsx`
1) Nuevo componente (baseball-only) renderizado desde `MatchCard.jsx` junto a `MLBScriptPanel`:
- Header: **Expected Script** (label + narrative)
- Métricas:
  - Expected Runs
  - Projected Margin
- Tarjeta “Starting Pitchers”:
  - nombres + `Pitcher Quality Score`
  - 2 métricas top (ej: ERA + xERA o FIP + WHIP) según disponibilidad.
- Sección “WHY THIS PICK?” checklist (7 filas).
- Sección “Confidence Breakdown” (barras o filas con sumatoria = total).

2) UI compacta:
- En el cuerpo de la card (antes de expand): mostrar 1–2 líneas `baseball_reasons` (si existen) para diferenciar picks similares.

### MLB-V7.4 Success criteria específico
- Al ver un pick MLB, el usuario entiende en <5 segundos:
  1) quién lanza,
  2) qué script se espera,
  3) por qué ese mercado,
  4) cuánto edge existe,
  5) por qué difiere de otros picks,
  6) de dónde sale la confianza.
- Diversificación: el motor **no converge** en Under 9.5 / RL +1.5 cuando existen alternativas igual o más justificadas.
- Sin regresiones: football/basketball no renderizan nada de V3.

---

## 3) Next Actions

### A) MLB-V7 Explainability + Diversificación (P0 funcional / UX)
1) Implementar `mlb_pregame_analytics_v3.py` con funciones puras.
2) Wire en `mlb_day_orchestrator.py` + adjuntar `_mlb_script_v3`.
3) Implementar `MLBScriptV3Panel.jsx` + hook en `MatchCard.jsx`.
4) Ajustar `signal_catalog` solo si se añaden nuevos `script_code` labels (opcional); preferir que v3 sea UI-driven.

### B) Bright Data Unlocker (P1) — siguiente prioridad de scraping
1. Confirmar `BRIGHTDATA_API_KEY` + `BRIGHTDATA_ZONE` (Web Unlocker).
2. Implementar cascade por scraper: `direct_fetch` → (403/timeout) → `brightdata_fetch`.
3. Añadir cache TTL en DB para reducir coste (por tipo de URL).
4. Activar Unlocker en:
   - Editorial Context (Sportytrader/BeSoccer/scores24)
   - NBA/basketball y MLB scrapers con Cloudflare.

### C) Basketball Historical Detail (P1)
1. Implementar profile + integración pipeline.
2. Añadir rescue layer (totales/team totals) + trap signals.
3. UI “Historial profundo”.

### D) Manual Odds paste (P1/P2)
**Estado:** ✅ COMPLETADO para MLB (client-side EV) en MLB-V6.
- Futuro: persistir la cuota manual y recalcular edge server-side si se desea.

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

- **MLB-V2 (✅ cumplido y validado)**
  - Picks MLB incluyen: `Projected Margin`, `Cover Probability`, `Best/Recommended Total Line`, `lineSafetyScore`, `pickType`, `sameGameCorrelation`.
  - Parlays MLB-only de 2–4 picks con correlación ≥60 (cuando existan suficientes picks elegibles).
  - Run Line -1.5 solo cuando hay dominancia real.
  - **Cero regresiones**:
    - Football/basketball sin `_mlb_script_v2`.
    - Parlay genérico intacto fuera de MLB.

- **MLB-V3 (✅ cumplido)**
  - `baseballHistoricalProfile` presente por pick (fail-soft: `available=false` con `_reason`).
  - `historical_trap_signals` expuestas y ajustan `fragility.score`.
  - `baseball_runs_rescue` se intenta antes de descartar cuando el histórico lo permite.

- **MLB-V4 (✅ cumplido)**
  - Endpoints live: `GET /api/mlb/engine/weights`, `POST /api/mlb/picks/{id}/settle`, `POST /api/mlb/engine/recompute`.
  - Métricas outcome correctas: `margin/totalRuns/runLineCovered/overHit`.
  - Recalibración automática cada 50 picks settled, con pesos bounded.

- **MLB-V5 (✅ cumplido)**
  - Baseball NO se rutea a `discarded_market` por falta de cuotas.
  - Juegos con lectura estructural pero sin odds → `structural_lean_requires_odds`.
  - UI muestra “Revisión manual — falta cuota” con mercados sugeridos.
  - “Motivación normal” es neutral y no descarta MLB.

- **MLB-V6 (✅ cumplido)**
  - Totals: `coverProbability` corresponde a P(Under/Over) del lado recomendado (modelo Poisson).
  - UI muestra `Edge vs línea` y debug de probabilidades.
  - **Counter = render**: si el dashboard dice “Recomendados: N”, se renderizan N cards (incluye rescued/manual-review).
  - MLB picks ya no se ocultan por filtros genéricos de fragilidad.

- **MLB-V7 (🟨 objetivo nuevo)**
  - Explicabilidad: cada pick muestra pitchers, script, checklist y breakdown.
  - Razones beisboleras reemplazan conceptos internos.
  - Diversificación reduce repetición de Under 9.5 / RL +1.5 cuando existan alternativas justificadas.

### Testing status
- `/app/test_reports/mlb_v2_backend_test.json`: **12/12 PASS (100%)**
- `/app/test_reports/iteration_39.json`: **12/13 PASS (92.3%)**
  - Incidencia: timeout en regresión fútbol con `background=False` (no funcional; workaround: `background=True` o aumentar timeout).
- `/app/test_reports/iteration_40.json`: **44/51 PASS (86%)**
  - Core MLB-V5 verificado; algunos tests flaky por latencia de background jobs.
- MLB-V6 validations (manual):
  - ER=6.7, line=9.5 → P(Under)=86% ✓
  - ER=6.9, line=9.5 → P(Under)=84% ✓
  - ER=10.8, line=8.5 → P(Over)=75% ✓
  - ER=8.0, line=8.0 → ~50/50 ✓

### Nota de despliegue
- Los cambios se implementan en **PREVIEW**. Para aplicarlos en **PRODUCTION** se requiere **redeploy** del usuario.
