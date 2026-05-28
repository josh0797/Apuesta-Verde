# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening + P3 Editorial Context + P4 Playwright (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (`code/label/severity/explanation`) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Hardening de pipeline: evitar bloqueos en `stage=enriching` (Understat/externos) con timeouts + degradación elegante.

- **(✅ COMPLETADO)** Robustez multi-deporte en LIVE:
  - Detectar correctamente partidos LIVE en **basketball/baseball** (API-Sports v1 no soporta `live=all`).
  - Evitar “zombies LIVE” en fútbol (partidos terminados mostrados como LIVE).
  - Firewall de vocabulario para impedir **fugas de terminología** entre deportes.

- **(✅ COMPLETADO)** Enriquecimiento histórico fútbol (últimos 15): mejorar explicabilidad y señales para rescate Under (perfil histórico).

- **(✅ COMPLETADO)** **P3 — Editorial Context Engine (Scrapy)**:
  - Añadir una capa opcional y **fail-soft** de enriquecimiento editorial profundo (previas, predicciones y contexto) **solo para fútbol** y **solo para matches shortlisteados**.
  - No reemplazar Crawlee ni scrapers actuales: Scrapy **complementa** el stack existente.
  - Separar **dato vs opinión** (heurístico por regex) y adjuntar interpretación de Moneyball (PUBLIC_NARRATIVE_RISK, alineación, warnings).
  - Exponer en UI el bloque “Contexto editorial” en el detalle del partido.

- **(✅ COMPLETADO)** **P3 — Tuning de selectores + 3 fuentes adicionales**:
  - Expandir cobertura editorial y reducir “ruido” de anchors en fuentes de noticias.
  - Añadir AS.com y Marca.com como fuentes server-rendered de alta cobertura.
  - Ajuste de selectores + filtros por patrón de URL (`article_url_patterns`) para evitar anchors irrelevantes.

- **(✅ COMPLETADO)** **P4 — Playwright para fuentes JS-heavy (scores24.live + futuras)**:
  - Añadir Playwright como backend editorial **paralelo** a Scrapy.
  - Dispatch por fuente usando `requires_js`:
    - server-rendered → Scrapy
    - JS-rendered → Playwright
  - Ejecutar ambos backends **en paralelo** (no se bloquean).
  - Fail-soft ante Cloudflare/anti-bot (“Un momento…”): no romper análisis.
  - Habilitar scores24.live como fuente JS-rendered (requiere proxy residencial para desbloquear en datacenter).

---

## 2) Implementation Steps

### Phase 1 — Core POC (aislado) para el flujo “tolerancia + decisión contextual + señales trampa”
**Estado:** ✅ COMPLETADO

**Core probado**: dado (market, edge, confidence, fragility, trapSignals) → clasificación correcta + payload estructurado.

**User stories (POC) — completadas**
1. `Under 3.5` con edge ligeramente negativo, conf alta y frag baja ⇒ `PROTECTED_ACCEPTABLE`.
2. `Moneyline favorito` con edge negativo ⇒ `NO_BET_VALUE`.
3. Edge positivo con fragility alta ⇒ `FRAGILE_EDGE`.
4. Visualización de lista de trapSignals estructuradas.
5. `trapSignals>=3` ⇒ `MARKET_TRAP` salvo protected con frag<30.

**Entregables (Phase 1)**
1. ✅ `/app/backend/services/market_tolerance.py`
2. ✅ `/app/backend/services/moneyball_layer.py` (refactor + catálogo trap + clasificación contextual)
3. ✅ Validación POC sintética: **7/7 assertions passed**

---

### Phase 2 — V1 App Development (backend + wiring de rescate)
**Estado:** ✅ COMPLETADO

**User stories (V1) — completadas**
1. Separación de buckets: Recomendados / Protegidos aceptables / Watchlist / Rescatados / Descartados.
2. Descartes con explicación humana + señales trampa detalladas.
3. FragilityScore visible y usable como guardrail.
4. Motor intenta rescate antes de descartar.
5. Empty state con desglose por bucket.

**Backend (V1) — entregables**
1. ✅ `/app/backend/services/alternative_rescue.py`
2. ✅ `analyst_engine.py` (Phase 10 Universal Rescue)
3. ✅ Compatibilidad preservada

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

**Frontend (V1) — entregables**
1. ✅ `/app/frontend/src/pages/DashboardPage.jsx`
   - DiscardedRow expandible, RescuedRow, WatchlistRow, FragilityChip, nuevas secciones.
2. ✅ Lint / build OK
3. ✅ Screenshots verificados

---

### Phase 4 — P0 LIVE Hardening + P2 Historical Profile
**Estado:** ✅ COMPLETADO

**Objetivo:** restaurar LIVE multi-deporte + evitar matches zombies + eliminar fugas de vocabulario + enriquecer rescate Under con histórico (15).

#### 4.1 P0-1 — LIVE basketball/baseball no detectaba partidos
**Estado:** ✅ COMPLETADO
- ✅ `/app/backend/services/api_sports.py`
  - `fixtures_live(sport)`:
    - Football: `/fixtures?live=all`
    - Basketball/Baseball: `/games?date=today_utc` + `/games?date=yesterday_utc` + filtro por `status.short`

#### 4.2 P0-3 — Fútbol mostraba partidos terminados como LIVE (zombies)
**Estado:** ✅ COMPLETADO
- ✅ `/app/backend/services/live_lifecycle.py`
  - `2H` y `minute>=90` con `heartbeat_age>180s` ⇒ stale (“ghost-FT”).
  - Motivo explícito en `compute_live_state()`.
  - Añadido `BRK` a `LIVE_STATUSES['baseball']`.

#### 4.3 P0-2 — Sport Routing & Terminology Leakage
**Estado:** ✅ COMPLETADO
- ✅ `/app/backend/services/sport_vocab_guard.py` (NUEVO)
  - Firewall por deporte: reroute a `discarded_market` con `SPORT_VOCAB_LEAK`.
- ✅ Integración:
  - `analyst_engine.py` Phase 11
  - `server.py` `/api/matches/live` defensa final

#### 4.4 P2-1 — Enriquecimiento histórico fútbol (últimos 15)
**Estado:** ✅ COMPLETADO
- ✅ `data_ingestion._enrich_football(deep=True)` + `fixtures_last_n(n=15)`
- ✅ `normalizer.normalize_recent_fixtures()` crea `historical_goal_profile`
- ✅ `under_market_scan.py` usa `historical_goal_profile` para boost y reasons
- ✅ `alternative_rescue.py` expone `historical_profile` en rescate

#### 4.5 Testing
**Estado:** ✅ COMPLETADO
- ✅ Reporte: `/app/test_reports/iteration_24.json`

---

### Phase 5 — P3 Editorial Context Engine (Scrapy) — MVP
**Estado:** ✅ COMPLETADO

**Propósito:** añadir contexto editorial profundo (motivación real, objetivos, bajas, rotaciones, predicción editorial, riesgos) como capa P3 opcional **solo para fútbol** y **solo para matches shortlisteados**.

**Arquitectura implementada**
- ✅ Nuevo módulo: `/app/backend/services/editorial_context/`
  - `match_key.py`: `canonical_match_key()` + normalización de equipos
  - `editorial_source_registry.py`: registry declarativo de fuentes
  - `editorial_signal_mapper.py`: clasificador heurístico (regex) + extractores (score/market)
  - `editorial_normalizer.py`: normalización + scoring
    - `freshness_score` (24h/48h/72h)
    - `reliability_score` (baseline por fuente + bonus)
    - `narrative_bias_score` (detección hype)
    - `build_consensus()` (consenso por match)
  - `editorial_spider_main.py`: Scrapy spider entrypoint (crawler process)
  - `scrapy_runner.py`: ejecución **subprocess** (Twisted aislado, timeout, fail-soft)
  - `editorial_context_service.py`:
    - cache MongoDB (`editorial_context_signals`)
    - TTL lógico 6h por match_key
    - feature flag `EDITORIAL_CONTEXT_ENABLED`
  - `moneyball_interpretation.py`: “How Moneyball interprets editorial context”

**Integración en el pipeline**
- ✅ `analyst_engine.py`
  - Stage 1.6: `fetch_editorial_context_bulk()` sobre shortlist (máx 8) → adjunta `match.editorial_context`
  - Phase 12: adjunta `_editorial_context` + `_editorial_interpretation` a:
    - picks kept
    - `summary.discarded_market` (warnings de narrativa)
- ✅ `normalizer.summarize_match_for_llm()`
  - añade `editorial_context` compacto al payload LLM (solo campos esenciales, sin inflar tokens)

**UI**
- ✅ Nuevo componente: `/app/frontend/src/components/EditorialContextPanel.jsx`
- ✅ Integrado en `/app/frontend/src/components/MatchCard.jsx`
  - bloque colapsable “Contexto editorial”
  - muestra fuentes, mercado consenso, notas de motivación/factual, bajas, riesgos
  - muestra “Lectura del motor” con flags `PUBLIC_NARRATIVE_RISK` / sesgo

**No reemplazar stack actual (cumplido)**
- Crawlee y scrapers existentes permanecen intactos.
- Scrapy es P3 complementario; si falla o devuelve 0 items:
  - no rompe análisis
  - retorna `available=false`
  - logs `[SCRAPY_EDITORIAL_*]`

**Testing**
- ✅ Reporte: `/app/test_reports/iteration_25.json` — **14/14 tests passed**

---

### Phase 6 — P3 Selector Tuning + 3 New Sources (AS.com, Marca + limpieza de falsos positivos)
**Estado:** ✅ COMPLETADO

**Objetivo:** mejorar cobertura editorial real en producción y disminuir ruido de anchors.

**Cambios realizados**
1. ✅ **Inspección de HTML real (2026-05-28)** y ajuste de selectores.
2. ✅ `editorial_source_registry.py` expandido (y priorizado):
   - **AS.com** (`as_com`, prioridad 1) — alta cobertura, tip principal, cuotas.
   - Sportytrader ES (`sportytrader_es`, prioridad 2)
   - BeSoccer ES (`besoccer_es`, prioridad 3)
   - **Marca.com** (`marca_com`, prioridad 4) — contexto/lesiones/alineaciones.
3. ✅ `editorial_spider_main.py` mejorado:
   - Soporte Scrapy 2.13+: `async def start()`.
   - `article_url_patterns` para filtrar anchors irrelevantes.
   - Headers endurecidos + cookies habilitadas.
4. ✅ `editorial_signal_mapper.py` afinado:
   - Evita falso positivo “Más de 10 partidos” (Over/Under requiere `.5` y unidad cuando aplica).
   - Añadidos patrones 1X2/Victoria (“Tip principal: victoria de …”).

**Resultados verificados**
- ✅ Scrapy captura correctamente artículos de AS.com con cuerpo ~4.7–4.9k caracteres.
- ✅ Consenso detectado en producción:
  - `Victoria local (1X2)`
  - `Menos de 2.5`
- ✅ E2E timing: ~15s para 3 matches.

**Testing**
- ✅ Reporte: `/app/test_reports/iteration_26.json` — **13/13 tests passed**

---

### Phase 7 — P4 Playwright Integration (fuentes JS-heavy) + scores24.live
**Estado:** ✅ COMPLETADO (infra lista; desbloqueo en prod requiere proxy residencial)

**Objetivo:** habilitar fuentes editoriales renderizadas con JavaScript sin reemplazar Scrapy.

**Entregables**
1. ✅ **Backend Playwright (subprocess, fail-soft)**
   - ✅ `/app/backend/services/editorial_context/playwright_fetcher.py`
     - navegador stealth + bloqueo de assets
     - detección de challenge (“Un momento…”) y salida limpia
     - soporte proxy por env `PLAYWRIGHT_PROXY`
   - ✅ `/app/backend/services/editorial_context/playwright_runner.py`
     - runner subprocess fail-soft con `PLAYWRIGHT_BROWSERS_PATH=/pw-browsers`
   - ✅ `/app/backend/services/editorial_context/playwright_main.py`
     - entrypoint del subprocess (I/O JSON)

2. ✅ **Dispatcher dual-backend en paralelo**
   - ✅ `/app/backend/services/editorial_context/editorial_context_service.py`
     - ejecuta **Scrapy + Playwright en paralelo** via `asyncio.gather`
     - unifica items crudos antes de normalizar

3. ✅ **Registry extendido para dispatch**
   - ✅ `/app/backend/services/editorial_context/editorial_source_registry.py`
     - `enabled_sources(include_js=True|False)`
     - helpers `server_rendered_sources()` + `js_rendered_sources()`
     - `scores24_live` ahora `enabled=true`, `requires_js=true`

4. ✅ **Operación en entorno actual**
   - Chromium instalado en `/pw-browsers`.
   - scores24.live está **bloqueado por Cloudflare** desde IPs de datacenter:
     - comportamiento esperado: Playwright devuelve 0 items y el pipeline sigue.
     - para activarlo en producción: **configurar proxy residencial**
       `PLAYWRIGHT_PROXY=http://user:pass@residential-host:port`.

**Testing**
- ✅ Reporte: `/app/test_reports/iteration_27.json` — **12/12 tests passed**

---

## 3) Next Actions

### A) Hardening de enrichment (P1)
**Motivo:** se observó que una generación puede quedarse en `stage=enriching` (scraping/Understat/editorial).
1. Timeouts agresivos + fallback en enrichment Understat (2–4s).
2. Telemetría: tiempos por etapa + ratio de fallos.
3. Job progress reliability: `/api/analysis/jobs/{job_id}` status monotónico.

### B) Refinamiento Editorial (P1/P2)
1. Mejorar cobertura de scraping:
   - Añadir index URLs por fuente cuando cambien estructura.
   - Ajustar selectores sin tocar spider.
2. Mejorar `sourceReliabilityScore` con histórico interno (accuracy tracking).
3. Añadir `contradiction_flags` más ricos:
   - contradicción motivación vs standings
   - contradicción forma reciente vs narrativa
4. Persistencia avanzada:
   - TTL real por tipo (pre-match 7 días vs live 24h) si se amplía a live.

### C) Proxies residenciales (P2)
- **Bloqueado por credenciales**: necesarias para habilitar scores24.live en P4 y mejorar fallback de Sofascore.

---

## 4) Success Criteria
- Aparición de resultados en **Protected acceptable** y/o **Watchlist** cuando corresponde (sin inventar valor).
- `Moneyline favorito` con edge negativo sigue siendo `NO_BET_VALUE`.
- Descartados muestran: mensaje humano + señales trampa + fragility.
- Rescatados muestran: por qué falló el directo + por qué el protegido es más seguro.

- LIVE multi-deporte estable:
  - Basketball/Baseball: LIVE detectado sin depender de `live=all`.
  - Fútbol: no se muestran FT/90’ zombies; sweeper archiva.
  - Firewall vocabulario: no hay “goles/córners” fuera de fútbol.

- Editorial Context (P3/P4):
  - Se adjunta `editorial_context` a matches shortlisteados cuando hay contenido.
  - `available=false` y pipeline intacto cuando Scrapy/Playwright no encuentra señales.
  - UI muestra “Contexto editorial” con fuentes/argumentos/riesgos.
  - Moneyball nunca recomienda “a ciegas”: si editorial sugiere mercado pero Moneyball no ve edge → `PUBLIC_NARRATIVE_RISK`.
  - Fuentes server-rendered (AS/Marca/Sportytrader/BeSoccer) continúan aportando señales.
  - Fuentes JS-heavy pueden activarse vía Playwright; si Cloudflare bloquea, el sistema degrada elegantemente.

- No regresiones:
  - endpoints existentes responden
  - `_market_edge` no cambia
  - `asyncio.wait_for(timeout=3.0)` intacto
  - narrativa ES intacta

- Hardening:
  - ningún job queda colgado en `enriching`; si falla Understat, Scrapy o Playwright, el pipeline termina con degradación elegante.
