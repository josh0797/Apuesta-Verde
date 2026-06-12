# Plan — Phase F58 (Football L5 vs L15 Profile Cross + Player Props Discovery)

> **Nota:** Este plan se mantiene como bitácora completa de las Phases F58–F70.  
> **Estado actual:** ✅ **F69 COMPLETADA** (bug de editorial genérico resuelto).  
> **Nueva fase activa:** **F70 — Integración de fuentes externas (Sportytrader / Forebet) como reemplazo de Scores24/ScoreLive** (**BLOQUEADA por políticas / anti-bot**; ver opciones).

---

## 1) Objetivos

### Objetivos originales (F58)
- Implementar un **cross L5 vs L15** para fútbol (goles, xG, xGA, tiros, SOT, corners) con 7 perfiles y deltas simétricos.
- Añadir **ingestión híbrida** para hidratar stats de jugador usadas por props:
  - StatMuse primario (rápido y estable para shots/SOT/minutos)
  - FBref para enriquecer mercados de volumen (pases/tackles/fouls/cards/xG) cuando sea accesible
  - Understat como último recurso para xG/shots/sot cuando aplica
- Implementar **Player Props Discovery Moneyball** (tiers + gates) con degradación fail-soft.
- Integrar en el flujo football existente con un modo más agresivo: **override contextual** cuando el cross sea “muy fuerte”.
- Añadir **smoke tests** (mínimos) y mantener la suite global verde (cero regresiones).
- (P2) **UI wiring**: panel independiente en cards football para visualizar Cross + Override + Player Props.

### Estado actual global (Phases F58–F68)
- ✅ F58 completado (backend + UI + scripts + tests).
- ✅ F60 completado (external context gate + corner cross wiring).
- ✅ F61/F61.1 completado (under support simétrico + promoción dc_nb_delta).
- ✅ F62/F63 completado (Scores24 review + soft/hard discard + UI badge + live patch).
- ✅ F64 completado (structural review + bucket `watchlist_odds_needed` + UI + 8 tests).
- ✅ F65 completado (Bright Data circuit breaker + watchlist backtest + cron + endpoints + tests).
- ✅ F66 completado (Motor Editorial Interno + TheStatsAPI + Dixon-Coles contextual-only + UI panel + tests).
- ✅ F67 completado (guardrails, telemetry `discard_rescue_audit`, H2H cron, match_id mapping, heatmaps lazy + tests).
- ✅ F68 completado (player_id mapping por nombre + endpoint by-name + wiring UI + tests).

### Objetivo (ya resuelto) — Phase F69
Corregir el generador de análisis editorial interno para que:
1. Sea **match-specific** (no plantilla repetida).
2. **Nombre correctamente equipos** (nunca “Home/Away”).
3. **No invente scoreline** cuando la data sea insuficiente.
4. Evite **texto duplicado** intra-run (anti-duplicado).
5. No use **cache compartida** entre partidos.
6. Sea **fail-soft honesto**: cuando datos insuficientes, mostrar “sin lectura suficiente”.
7. Use contexto histórico cuando esté disponible.
8. Exponga **data_quality** y **reason_codes**.

✅ **Resultado**: F69 completada con 8/8 tests nuevos y suite global verde.

### Objetivos nuevos (Phase F70)
Construir un reemplazo “externo” de contexto tipo Scores24/ScoreLive usando:
- **Sportytrader** (página de pronóstico + últimos resultados + H2H), y
- **Forebet** (predicciones y señales),

…con arquitectura **fail-soft**, telemetría y caching; y con **circuit breaker** para evitar loops de scraping.

⚠️ **Nuevo hallazgo (bloqueador):**
- **Sportytrader**: HTTP directo devuelve 403; y **Bright Data lo bloquea por política “Gambling”** (cabecera `x-brd-error`).
- **Forebet (páginas de predicción)**: también **bloqueado por política “Gambling”** en Bright Data.
- **Forebet HOME PAGE**: ✅ Bright Data devuelve HTML (parseable, ~251KB).
- **DuckDuckGo HTML**: ❌ timeout desde el contenedor (no usable como fallback estable actualmente).

---

## 2) Implementación (fases)

### Fase 1 — POC (Aislamiento): Scraping/ingestión de stats de jugador (core frágil)
**(COMPLETADO)** — sin cambios.

### Fase 2 — V1 App Dev: Football Team Profile Cross (L5 vs L15)
**(COMPLETADO)** — sin cambios.

### Fase 3 — V1 App Dev: Football Player Props Discovery (Moneyball)
**(COMPLETADO)** — sin cambios.

### Fase 4 — Integración en Football pipeline (override incluido)
**(COMPLETADO)** — sin cambios.

### Fase 5 — UI Wiring (P2) — Panel independiente Cross + Override + Player Props
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2) — validar `hydrate_player_stats`
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase F60 — External Context Gate + Corner Cross Wiring (COMPLETED)
**(COMPLETADO)** — sin cambios.

## Phase F61 — Football Under Support + Cross-Signal Check (COMPLETED)
**(COMPLETADO)** — sin cambios.

## Phase F62/F63/F64/F65/F66/F67/F68
**(COMPLETADO)** — sin cambios.

---

# Phase F69 — Fix análisis editorial interno match-specific (COMPLETED ✅)

## Resumen del problema
Cuando aparecía “SCORES24: NO ENCONTRADO”, el panel de “Análisis editorial interno” renderizaba un texto repetido (p.ej. neutral + “1-1”) para múltiples partidos, rompiendo credibilidad.

## Causa raíz (confirmada)
1. `attach_alternatives_to_summary()` se invocaba sin `match_lookup`, por lo que el motor editorial recibía un **entry desnudo**.
2. Sin xG, `compute_scoreline_grid()` caía a heurística `NEUTRAL` → típico “1-1”.
3. No se usaban `reason/odds/implied/estimated/edge` para contextualizar el descarte.
4. `_team_name()` devolvía “Home/Away”.

## Implementación ejecutada (C1–C8)
- **C1 (match_lookup + enrichment):** `analyst_engine` construye `match_lookup` desde `matches_payload` y lo pasa a `attach_alternatives_to_summary`. En `possible_alternative_markets` se mezcla payload hidratado + campos de descarte (odds/edge/etc.).
- **C2 (nombres):** `_team_name()` ahora resuelve `home/away` desde dicts, strings, campos planos, o parsea `match_label`; fallback español “equipo local/visitante”.
- **C3 (no scoreline inventado):** gating fuerte: si `data_quality` es THIN/LIMITED y no hay xG → `probable_score.available=false` y texto “No disponible con suficiente confianza.”
- **C4 (data_quality + audit flags):** salida incluye `data_quality` y `internal_editorial_analysis` con `available`, `match_specific`, `is_generic_fallback`, `reason_codes`.
- **C5 (THIN honesto):** corners/goals/score se bloquean en THIN con mensajes honestos (fail-soft).
- **C6 (descartes con números):** se agrega `discard_reason_narrative` citando cuota/prob impl/prob est/edge/fragility cuando existe.
- **C7 (anti-duplicado):** `detect_duplicate_internal_editorials()` marca `INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED` si similitud ≥85% (normalización + Jaccard tokens).
- **C8 (UI):**
  - Píldora “Calidad: THIN/LIMITED/USABLE/STRONG”.
  - Oculta editorial si `is_generic_fallback=true` y muestra estado honesto.
  - Etiqueta UI “Scores24: …” → “Sportytrader: …” (solo label).
  - Sección visible de “Motivo del descarte” (discard_reason_narrative).

## Tests (8) + estado
- ✅ 8 tests nuevos `test_f69_internal_editorial_match_specific.py`.
- ✅ Suite total: **2033 passed** (0 regresiones).

---

# Phase F70 — Reemplazo externo (Sportytrader / Forebet) para contexto editorial (IN PROGRESS, BLOQUEADA)

## Objetivo
Añadir un “External Editorial Context Provider” que:
- intente resolver URL por partido,
- scrapee “puntos clave”, últimos resultados y H2H,
- alimente la UI (badge + link + extractos),
- mantenga fail-soft (si no hay acceso: explicar y degradar al motor interno).

## Investigación completada (bloqueadores)
- **Sportytrader**:
  - ❌ HTTP 403 directo desde contenedor.
  - ❌ Bright Data bloqueado por política: “classified as Gambling”.
- **Forebet**:
  - ✅ HOME `/es/` accesible vía Bright Data (HTML grande, parseable).
  - ❌ Rutas de predicción como `/es/predicciones-...` bloqueadas por política “Gambling”.
- **DuckDuckGo**:
  - ❌ timeout (no usable para discovery en este entorno).

## Decisión pendiente (opciones)
Dado el bloqueo por políticas, F70 requiere elegir una estrategia:

### Opción A — Implementación limitada “Forebet HOME only” (recomendación operativa si se quiere avanzar ya)
- Scraping únicamente de `https://www.forebet.com/es/` (vía Bright Data, permitido).
- Extraer lista del día + señales básicas (estructura `div.rcnt`, etc.).
- Usar esto como “contexto externo liviano” (sin deep-match pages).
- UI: mostrar “Forebet (resumen)” + link a la home, indicando limitación.

### Opción B — Cambiar proveedor anti-bot (requiere credenciales/contrato)
- Integrar ScraperAPI / ZenRows / (otro) para Sportytrader + Forebet deep pages.
- Mantener el `circuit_breaker` existente y añadir un breaker por proveedor.

### Opción C — Mantener solo Motor Editorial Interno (estado actual estable post-F69)
- No añadir scraping; solo links informativos.
- Ventaja: 0 riesgo de bloqueos y estabilidad máxima.

### Opción D — Solo UI label + link externo (ya parcialmente hecho)
- Mantener badge “Sportytrader no encontrado” pero añadir botón “Abrir en Sportytrader/Forebet” sin scraping.

## Implementación propuesta (una vez elegida opción)
1) **Servicio backend** `external_sources/forebet_scraper.py` (o `sportytrader_scraper.py` si se habilita proveedor):
   - `fetch_*()` usando `external_sources.circuit_breaker.fetch_with_breaker()`.
   - Parseo con `selectolax` y extracción de:
     - fixtures list,
     - predicción 1X2 / marcador sugerido si está en home,
     - señales (ej. “12-1”, etc.).
2) **Caché Mongo TTL** (similar a `head_to_head_matches`):
   - `external_editorial_cache` TTL 24–72h.
3) **Endpoints**:
   - `GET /api/football/external-editorial/{match_id}` (fail-soft).
4) **UI**:
   - badge “External: available/missing/policy_blocked”
   - mostrar fuente + URL + extractos.
5) **Tests**:
   - parser unit tests con HTML fixture local.
   - breaker tests: policy-block → pause 24h.

## Estado
- **Estado actual:** investigación completada; implementación bloqueada por política Bright Data para Sportytrader y rutas profundas de Forebet.
- **Siguiente paso:** elegir opción A/B/C/D para ejecutar.

---

## Próximos pendientes no bloqueantes
- (P3) Expandir diccionario de equipos `team_name_translations.py` (clubs UCL/UEL).
- Evaluar evolución del componente gráfico de heatmaps si se requiere más detalle.
