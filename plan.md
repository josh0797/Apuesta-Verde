# Plan — Phase F58 (Football L5 vs L15 Profile Cross + Player Props Discovery)

> **Nota:** Este plan se mantiene como bitácora completa de las Phases **F58–F70**.  
> **Estado actual:** ✅ **F69 COMPLETADA** + ✅ **F70 COMPLETADA** (scraping externo vía **scrape.do** + UI de enriquecimiento).  
> **Estado de tests:** ✅ **2041 tests passing**, 0 regresiones.  
> **Estado runtime:** Backend reiniciado OK, frontend compila OK.

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
6. Sea **fail-soft honesto**: si no hay datos suficientes, mostrar “sin lectura suficiente”.
7. Use contexto histórico cuando esté disponible.
8. Exponga **data_quality** y **reason_codes**.

✅ **Resultado:** F69 completada con **8/8 tests nuevos** y suite global verde.

### Objetivo (ya resuelto) — Phase F70
Construir un reemplazo “externo” de contexto tipo Scores24/ScoreLive usando:
- **Sportytrader** (página de pronóstico + últimos resultados + stats agregadas), y
- **Forebet** (predicción 1X2, marcador, goles esperados),

…con arquitectura **fail-soft**, **caching TTL**, endpoints REST y UI de enriquecimiento.

✅ **Resultado:** F70 completada usando **scrape.do** (nuevo proveedor anti-bot). Incluye parsers, proveedor orquestador, caché Mongo TTL, endpoints y componente UI.

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
Cuando aparecía “SCORES24: NO ENCONTRADO”, el panel de “Análisis editorial interno” renderizaba un texto repetido (p.ej. neutral + “1-1”) para múltiples partidos.

## Causa raíz (confirmada)
1. `attach_alternatives_to_summary()` se invocaba sin `match_lookup`, por lo que el motor editorial recibía un **entry desnudo**.
2. Sin xG, `compute_scoreline_grid()` caía a heurística `NEUTRAL` → típico “1-1”.
3. No se usaban `reason/odds/implied/estimated/edge` para contextualizar el descarte.
4. `_team_name()` devolvía “Home/Away”.

## Implementación ejecutada (C1–C8)
- **C1 (match_lookup + enrichment):** `analyst_engine` construye `match_lookup` desde `matches_payload` y lo pasa a `attach_alternatives_to_summary`. En `possible_alternative_markets` se mezcla payload hidratado + campos de descarte (odds/edge/etc.).
- **C2 (nombres):** `_team_name()` resuelve nombres desde dict/string/campos planos o parsea `match_label`; fallback español “equipo local/visitante”.
- **C3 (no scoreline inventado):** gating fuerte: si `data_quality` es THIN/LIMITED y no hay xG → `probable_score.available=false` y texto **“No disponible con suficiente confianza.”**
- **C4 (data_quality + audit flags):** salida incluye `data_quality` y `internal_editorial_analysis` con `available`, `match_specific`, `is_generic_fallback`, `reason_codes`.
- **C5 (THIN honesto):** corners/goals/score se bloquean en THIN con mensajes honestos (fail-soft).
- **C6 (descartes con números):** se agrega `discard_reason_narrative` citando cuota/prob impl/prob est/edge/fragility.
- **C7 (anti-duplicado):** `detect_duplicate_internal_editorials()` marca `INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED` si similitud ≥85%.
- **C8 (UI):**
  - Píldora “Calidad: THIN/LIMITED/USABLE/STRONG”.
  - Oculta editorial si `is_generic_fallback=true` y muestra estado honesto.
  - Etiqueta UI “Scores24: …” → “Sportytrader: …” (solo label).
  - Sección visible “Motivo del descarte” (discard_reason_narrative).

## Tests (8) + estado
- ✅ 8 tests nuevos `test_f69_internal_editorial_match_specific.py`.
- ✅ Suite global: **2041 passed**.

---

# Phase F70 — Reemplazo externo (Sportytrader / Forebet) para contexto editorial (COMPLETED ✅)

## Motivación
- Bright Data bloquea por política “Gambling” varias fuentes (Sportytrader, Scores24, Forebet deep pages). Se requiere un proveedor anti-bot que sí permita estos dominios.

## Solución elegida
- ✅ Integración de **scrape.do** como proveedor anti-bot.
- ✅ Parsers deterministas (Selectolax + regex) para Sportytrader y Forebet.
- ✅ Proveedor orquestador con **caché Mongo TTL 24h** y endpoints REST.
- ✅ UI: nuevo panel de **Contexto externo** (Forebet + Sportytrader) cargado de forma lazy.

## Componentes implementados
### Backend
- `services/scrape_do_client.py`
  - Cliente async/sync
  - Circuit breaker in-process (fail-soft)
- `services/sportytrader_scraper.py`
  - Extrae: equipos/competición, `final_prediction`, stats promedio últimos 6, y últimos resultados (lista con scores)
- `services/forebet_scraper.py`
  - Extrae: fixtures, probabilidades 1X2, marcador predicho, goles esperados
  - `find_fixture()` robusto (acentos, guiones, tokens, swap)
- `services/external_editorial_provider.py`
  - `external_editorial_cache` TTL 24h
  - `fetch_forebet_index()` cacheado
  - `fetch_sportytrader_match(url)` cacheado
  - `fetch_external_editorial_for_match(match)` normalizado
- `server.py`
  - Endpoints:
    - `GET /api/football/external-editorial/by-teams?home=...&away=...`
    - `GET /api/football/external-editorial/{match_id}`
  - Startup: ensure indexes para `external_editorial_cache`

### Frontend
- `components/ExternalEditorialPanel.jsx`
  - Panel lazy que llama `.../external-editorial/by-teams`
  - Render:
    - Forebet: 1/X/2 (píldoras) + marcador + goles esperados
    - Sportytrader: link, `final_prediction`, stats promedio, últimos resultados (expandible)
- Wiring:
  - `EditorialPredictionPanel.jsx` incluye `ExternalEditorialPanel`
  - `DashboardPage.jsx` pasa `homeTeamName/awayTeamName` (derivados de `home_team` / `away_team` / `match_label`)

## Verificación end-to-end
- Forebet devuelve predicciones match-specific (ejemplos validados):
  - Qatar vs Switzerland: 19/21/60 → pick 2, score 0-2, goals_avg 1.9
  - Brazil vs Morocco: 59/27/14 → pick 1, score 3-1, goals_avg 3.29
  - Canada vs Bosnia: 41/35/24 → pick 1, score 1-0, goals_avg 1.5

## Tests (8) + estado
- ✅ 8 tests nuevos `test_f70_external_editorial.py`.
- ✅ Suite global: **2041 passed**, 0 regresiones.

---

## 3) Pendientes y siguientes pasos (post-F70)

### Validación del usuario (preview)
- Verificar visualmente en el dashboard:
  - Que el **Análisis editorial interno** ya no se repite entre partidos.
  - Que `data_quality` se muestra y que en THIN no aparece scoreline inventado.
  - Que el panel **Contexto externo** aparece cuando Forebet está disponible.
  - Que Sportytrader muestra link de búsqueda cuando no hay URL exacta.

### Pendientes no bloqueantes
- (P3) Expandir diccionario de equipos `team_name_translations.py` (clubs UCL/UEL).
- (Futuro) Resolver discovery de URLs exactas de Sportytrader por partido (para auto-scrape profundo sin depender de URL manual):
  - Estrategia: endpoint de búsqueda interno (scrape.do) + heurística slug + cache de `sportytrader_match_ids`.
- (Futuro) Si se requiere, integrar el contexto externo en la narrativa del motor editorial (por ahora se muestra en panel separado para mantener pureza y evitar dependencia sincrónica).
