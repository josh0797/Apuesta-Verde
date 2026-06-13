# Plan — Phases F58–F83 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.  
> **Estado histórico:** ✅ F58–F70 completadas (ver secciones abajo).  
> **Estado actual:** ✅ **F74 (parcial) COMPLETADA** + ✅ **F74-post (9 cambios) COMPLETADA** + ✅ **F74-post v2 (TheStatsAPI Odds Fallback Wiring) COMPLETADA** + ✅ **F74-post v2.5 (Opening Odds → Line Movement Wiring) COMPLETADA** + ✅ **F82 (Rich H2H Context + 365Scores Corners) COMPLETADA** + ✅ **F82.1 (Non-blocking Enrichment + Timeout Protection) COMPLETADA** + ✅ **F83 (Manual Market Identity + Manual Odds Injection) COMPLETADA** + ✅ **F82.1-adjust (Manual/Background Corners Enrichment Endpoints) COMPLETADA** + ✅ **F83.1 (Pantalla-negra fix + match_id robust + odd isolation + data availability sections) COMPLETADA** + ✅ **P2 (infer_original_pick_side 4-source cascade) COMPLETADA** + ✅ **F82.2 backend (Scores24 deprecated, 365Scores cross integrator, provider re-order, persistence) COMPLETADA** + ✅ **F82.2 frontend (CornersEnrichmentButton wiring + Scores24 label removal + FE tests) COMPLETADA** + ✅ **F83.2 / Bloque E (xG L1/L5/L15 desde shotmap TheStatsAPI + UI + tests) COMPLETADA**.  
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
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales (Under 3.5, BTTS, promedio de goles).
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente en `football_data_enrichment.corners`.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia; permitir *watchlist* bajo condiciones live (extensión futura si hace falta).

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners solo desde datos ya presentes (API-Sports/live_stats o snapshot TheStatsAPI). **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs por fecha/nombres). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal de picks.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual:
  - cuota detectada
  - cuota manual editable
  - selector de mercado / selección / línea
  - botón “Recalcular con mercado manual”
  - resultado recalculado: edge, fragilidad, confianza, veredicto
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular **promedios xG no-penal** (a favor / en contra) para **L1/L5/L15** por equipo usando `GET /football/matches/{match_id}/shotmap`.
- Arquitectura **background-first** con cache + timeouts cortos:
  - Nunca bloquear el generador principal de picks.
  - Si falla shotmap: **no inventar datos** → render parcial con reason_codes.
- Señales analíticas (contextuales):
  - `LOW_RECENT_XG_PROFILE`, `DEFENSIVE_XG_SUPPRESSION`, `XG_FORM_SHIFT`, `XG_APOYA_UNDER`, `XG_APOYA_OVER`.
- Señales de **muestra parcial** (NUEVO, para gobernanza editorial):
  - `XG_PARTIAL_SAMPLE`, `XG_L1_ONLY`, `XG_L5_AVAILABLE_L15_MISSING`, `XG_L15_AVAILABLE_L5_MISSING`, `XG_RECENT_SAMPLE_INSUFFICIENT`.
  - Regla clave: **si solo hay L1** (o no hay L5/L15 para ambos lados), el sistema no debe usar xG como confirmación fuerte para Over/Under.

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

## Estado: ✅ COMPLETADA

## Problema
Necesitábamos calcular y exponer **xG reciente** (L1/L5/L15) desde TheStatsAPI shotmap **sin bloquear** el pipeline principal (evitar 504/timeouts), y mostrando datos parciales cuando estén disponibles.

## Implementación ejecutada

### Backend
- ✅ **NEW** `services/external_sources/thestatsapi_shotmap_client.py`
  - Wrapper de `GET /football/matches/{match_id}/shotmap`.
  - Orden de resolución:
    1) `np_xg_summary.stored` (preferido)
    2) `np_xg_summary.live`
    3) fallback: suma de `data[].expected_goals` por `team_id` saltando penales (`is_penalty=True`).
  - Fail-soft: nunca raise, retorna `available=False` con reason_codes.
  - Sanity cap en xG (rechaza valores fuera de rango / NaN).

- ✅ **NEW** `services/football_xg_recent_averages.py`
  - `compute_xg_recent_averages(match_doc, timeout_s=6, use_cache=True)`
  - Extrae ids recientes por equipo desde:
    - `team.context.recent_fixtures[].match_id`
    - `team.recent_fixtures[].match_id|id`
    - `team.thestatsapi_recent_match_ids[]`
  - Concurrencia controlada (semáforo) y máximo 15 fixtures por lado.
  - Devuelve payload con:
    - `available`, `partial`, `source`, `home`, `away`, `reason_codes`.
  - Cache in-memory TTL 6h (anti-spam del botón refrescar).

- ✅ **MOD** `services/football_xg_signals.py`
  - Señales existentes: `LOW_RECENT_XG_PROFILE`, `DEFENSIVE_XG_SUPPRESSION`, `XG_FORM_SHIFT`, `XG_APOYA_UNDER`, `XG_APOYA_OVER`.
  - ✅ **NUEVAS señales de muestra parcial / cobertura (F83.2-E5):**
    - `XG_PARTIAL_SAMPLE`
    - `XG_L1_ONLY`
    - `XG_L5_AVAILABLE_L15_MISSING`
    - `XG_L15_AVAILABLE_L5_MISSING`
    - `XG_RECENT_SAMPLE_INSUFFICIENT`
  - Añade `metrics.coverage = {l1_both, l5_both, l15_both}`.
  - Regla editorial crítica validada por tests: **no se emiten `XG_APOYA_OVER/UNDER` cuando solo hay L1** (o cuando L5/L15 no cubren ambos lados).

- ✅ **MOD** `server.py`
  - Endpoints xG (prefijo `/api`):
    - `POST /api/football/xg-recent-averages/run-now` (timeout duro 8s)
    - `POST /api/football/xg-recent-averages/background` (job in-memory)
    - `GET  /api/football/xg-recent-averages/status/{match_id}`
  - Persistencia best-effort de snapshot en `analyst_runs`:
    - `picks.$.xg_recent_averages`
    - `picks.$.football_data_enrichment.xg_recent_averages`

### Frontend
- ✅ `frontend/src/components/XGRecentAveragesPanel.jsx`
  - Panel con CTA manual que llama `/football/xg-recent-averages/run-now`.
  - Render parcial: muestra L1 aunque falten L5/L15; cada fila ausente muestra “no disponible”.
  - Badge bilingüe de muestra parcial (`partialBadge`).
  - ✅ Badge adicional bilingüe para `XG_L1_ONLY` (`xg-recent-l1-only-badge`) para marcar que la muestra es demasiado limitada.
  - Render de señales (`signals[]`) + explicación (`explanations[code]`).

- ✅ `frontend/src/components/MatchIntelligencePanel.jsx`
  - Verificado el wiring: renderiza `XGRecentAveragesPanel` para fútbol después de `CornersEnrichmentButton`.

### Testing
- ✅ **NEW** `backend/tests/test_f83_2_xg_recent_averages.py` — **30 tests**
  - Layer 1: shotmap client (stored/live/fallback + fail-soft).
  - Layer 2: agregación L1/L5/L15, cache, partial, no-ids.
  - Layer 3: señales existentes + nuevas señales parciales (incluye guardrail “L1 only → no Over/Under support”).
  - Layer 4: endpoints/validación (coerción match_id numeric/blank/null, MATCH_NOT_FOUND, wiring señales).

- ✅ **NEW** `frontend/src/components/__tests__/XGRecentAveragesPanel.test.jsx` — **12 tests**
  - Gating, CTA, POST payload, error rendering.
  - Full snapshot, partial snapshot, L1-only badge, i18n smoke.

## Validación
- ✅ Backend `pytest`: **2313 passed, 2 skipped, 0 fallos** (antes: 2283) → **+30 tests Bloque E**.
- ✅ Frontend: `craco test` del panel: **12/12 pasan**.
- ✅ esbuild: `XGRecentAveragesPanel` y `MatchIntelligencePanel` compilan sin errores.
- ✅ Preview: login carga limpio.

---

## 3) Pendientes y siguientes pasos (post-F83.2)

### Pendientes no bloqueantes (actualizadas)
- (P3) Expandir `team_name_translations.py` para clubes UCL/UEL.
- (P4) Migrar enriquecimiento estructural a TheStatsAPI una vez verificada estabilidad.

### Known issue fuera de alcance (preexistente)
- ⚠️ 3 fallos en `frontend/src/components/__tests__/LiveReevalPanel.test.jsx` (copy de toast no coincide con implementación actual). No se tocaron en Bloque E; reproducidos como preexistentes.

---

## 6) Validación esperada (post-F83)
- UI/JSON:
  - `xg_recent_averages.available` y/o `partial=true` visibles en el panel.
  - Si `XG_L1_ONLY` → badge explícito “muestra limitada” + sin conclusión fuerte.
  - Si falta xG: panel debe poder mostrar error y continuar sin romper otras secciones.
- Arquitectura:
  - Ningún endpoint xG bloquea generación de picks (run-now/background explícitos).
- Logs:
  - Debug señaliza reason_codes y status sin exception bubbling.
