# plan.md — Market Tolerance + Rescue Layer + UI de trampa/fragilidad

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED**, y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (código/etiqueta/severidad/explicación) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido.
- Mantener compatibilidad: endpoints existentes, `_market_edge` y `reason_es`. No tocar `asyncio.wait_for(timeout=3.0)`.

## 2) Implementation Steps

### Phase 1 — Core POC (aislado) para el flujo “tolerancia + decisión contextual + señales trampa”
**Core a probar**: dado (market, edge, confidence, fragility, trapSignals) → clasificación correcta + payload estructurado.

**User stories (POC)**
1. Como analista, quiero que `Under 3.5` con edge -1.1%, conf 72, frag 32 sea `PROTECTED_ACCEPTABLE`.
2. Como analista, quiero que `Moneyline favorito` con edge -1.1% sea `NO_BET_VALUE`.
3. Como analista, quiero que edge positivo con fragility>70 sea `FRAGILE_EDGE`.
4. Como analista, quiero ver una lista de trapSignals estructuradas (no solo conteo).
5. Como analista, quiero que `trapSignals>=3` fuerce `MARKET_TRAP` salvo protected con frag<30.

**Steps**
1. Websearch breve: mejores prácticas para “risk scoring + thresholding” (solo para validar reglas simples y naming).
2. Crear `/app/backend/services/market_tolerance.py`:
   - `classify_market_tolerance(market_name)` → aggressive|balanced|protected.
   - `tolerance_params(category)` → min_edge, negative_edge_floor, etc.
3. Refactor POC en `/app/backend/services/moneyball_layer.py` (sin tocar integración UI todavía):
   - Añadir `contextual_edge_decision(...)` que use tolerance model + reglas trap/fragility.
   - Añadir `detect_trap_signals_structured(...)` y devolver también strings legacy.
4. Script de prueba aislado: `/app/backend/tests/test_market_tolerance_poc.py` (pytest o python -c) con 8–12 casos.
5. Criterio “no avanzar”: todos los casos del POC pasan y no rompe `apply_moneyball_layer`.

### Phase 2 — V1 App Development (backend + frontend mínimo viable)

**User stories (V1)**
1. Como usuario, quiero ver secciones separadas: Recomendados / Protegidos aceptables / Watchlist / Rescatados / Descartados.
2. Como usuario, quiero que un descarte explique: “mercado directo sin valor” + lista de señales trampa.
3. Como usuario, quiero ver fragilityScore y su etiqueta (baja/moderada/alta/muy alta) en cada fila.
4. Como usuario, quiero que el motor intente rescatar mercados protegidos antes de descartar.
5. Como usuario, quiero que el empty state diga cuántos items hay en watchlist/protected acceptable aunque no haya picks.

**Backend (V1)**
1. Crear `/app/backend/services/alternative_rescue.py`:
   - `attempt_alternative_market_rescue(match, sport, failed_direct_markets)`.
   - MVP: usar odds disponibles en `odds_snapshots[-1]` + heurística simple (no LLM) para proponer 1–2 mercados protegidos y pasar por `contextual_edge_decision`.
2. Refactor `moneyball_layer.py`:
   - Extender `classify_pick()` → soportar `PROTECTED_ACCEPTABLE` y `WATCHLIST`.
   - Mantener `_market_edge` (shape) y agregar en `_moneyball`: `trap_signals_structured` + `tolerance_used`.
   - Back-compat: mantener `market_trap_signals` (strings).
3. Integrar en `analyst_engine.py`:
   - Tras `apply_moneyball_layer`, mover picks a buckets por `classification`:
     - `picks` (VALUE/STRONG/UNDERVALUED/LIVE)
     - `protected_acceptable`
     - `watchlist`
     - `rescued_picks` (RESCUED_PROTECTED_MARKET)
   - Ejecutar `attempt_alternative_market_rescue` sobre descartados (no solo Tier 1/2).
   - Actualizar `summary` con conteos nuevos sin romper `total_*`.
4. No tocar: timeout `asyncio.wait_for` en `server.py`, `reason_es`, endpoints.

**Frontend (V1)**
1. `DashboardPage.jsx`:
   - Nuevas secciones renderizando `protected_acceptable`, `watchlist`, `rescued_picks`.
   - Empty state: “0 picks fuertes, pero X en watchlist / Y protected acceptable”.
2. `DiscardedRow` expandido:
   - Si existe `_moneyball.trap_signals_structured`, renderizar lista (label + explanation) + severidad.
   - Mensaje humano: “Descartado: mercado directo sin valor. Se detectaron N señales trampa.”

**Testing (V1)**
- Ejecutar testing agent backend: endpoints `/api/analysis/run`, `/api/picks/today` y validación de buckets.
- Prueba manual UI: generar picks y verificar secciones/empty state.

### Phase 3 — Hardening + Guardrails (producción)

**User stories (Hardening)**
1. Como usuario, no quiero picks “por actividad”: si no hay valor, el sistema debe dejarlo claro.
2. Como usuario, quiero que mercados agresivos nunca acepten edge negativo.
3. Como usuario, quiero que protected acceptable solo aparezca con conf/fragility/traps dentro de límites.
4. Como usuario, quiero que el rescate explique por qué es más seguro que los directos.
5. Como usuario, quiero consistencia multi-sport (basket/baseball) en etiquetas y buckets.

**Steps**
1. Expandir catálogo de markets y mapeos (sin sobre-ingeniería): aliases por deporte.
2. Mejorar `attempt_alternative_market_rescue`: registrar `whyDirectMarketsFailed` + `whyThisMarketIsSafer`.
3. Ajustar severidades y códigos trap; asegurar mínimo 14 señales definidas.
4. Añadir métricas en `_pipeline`: counts por bucket + ratio de rescates.
5. Testing agent end-to-end + regresión (live reevaluate, understat endpoints, etc.).

## 3) Next Actions
1. Implementar Phase 1 (POC): `market_tolerance.py` + `contextual_edge_decision` + `trap_signals_structured` + tests aislados.
2. Si POC pasa: implementar Phase 2 (V1) con rescue layer + buckets + UI.
3. Cerrar con testing agent y screenshots.

## 4) Success Criteria
- En datasets típicos, aparecen resultados en **Protected acceptable** y/o **Watchlist** cuando corresponda (sin inventar valor).
- `Moneyline favorito` con edge negativo sigue siendo `NO_BET_VALUE`.
- Descartados muestran lista de señales trampa (no solo conteo) + explicación humana.
- No regresiones: endpoints existentes siguen respondiendo; `_market_edge` no cambia; `asyncio.wait_for` intacto; narrativas `reason_es` intactas.
