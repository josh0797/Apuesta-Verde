# plan.md — Market Tolerance + Rescue Layers + UI trampa/fragilidad + LIVE Hardening (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (`code/label/severity/explanation`) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Hardening de pipeline: evitar bloqueos en `stage=enriching` (Understat/externos) con timeouts + degradación elegante.
- **(NUEVO ✅)** Robustez multi-deporte en LIVE:
  - Detectar correctamente partidos LIVE en **basketball/baseball** (API-Sports v1 no soporta `live=all`).
  - Evitar “zombies LIVE” en fútbol (partidos terminados mostrados como LIVE).
  - Firewall de vocabulario para impedir **fugas de terminología** entre deportes.
- **(NUEVO ✅)** Enriquecimiento histórico fútbol (últimos 15): mejorar explicabilidad y señales para rescate Under (perfil histórico).

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
   - `classify_market_tolerance(market, selection, decimal_odds)` → aggressive|balanced|protected|unknown
   - `tolerance_params(category)`
   - Excepción **Moneyline favorito** (cuota corta) ⇒ agresivo
2. ✅ `/app/backend/services/moneyball_layer.py` (refactor)
   - `detect_trap_signals_structured()` devuelve `list[dict]` con `code/label/severity/explanation`
   - `TRAP_CATALOG` con **16 códigos canónicos**
   - `classify_pick()` con reglas contextuales por categoría:
     - AGGRESSIVE: edge<0 ⇒ descartar
     - PROTECTED: tolera hasta -1.5% con fragility<=45, confidence>=68, traps<=1
     - BALANCED: zona watchlist (-1% a +1.5%)
   - Nuevos verdicts: `PROTECTED_ACCEPTABLE`, `WATCHLIST`
   - Back-compat: `market_trap_signals` (strings) se mantiene
   - `apply_moneyball_layer()` crea buckets: `summary.protected_acceptable`, `summary.watchlist`
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
   - `attempt_alternative_market_rescue(match, sport, base_confidence, why_direct_failed, original_pick_side=None)`
   - Guardrails:
     - Solo mercados `PROTECTED` (tolerance model)
     - Por defecto rescate **no-direccional** (totales) para evitar invertir el lado
     - `original_pick_side` habilita rescates direccionales solo si coincide
   - Football: delega a `scan_protected_alternatives` (Poisson + H2H)
   - Basketball/Baseball: candidatos protegidos conservadores
2. ✅ `analyst_engine.py`
   - Añadida **Phase 10 Universal Rescue** sobre `summary.discarded_market`
   - Mueve a `summary.rescued_picks` o `summary.watchlist`
   - Reconciliación final actualizada con conteos: `total_rescued`, `total_watchlist`, `total_protected_acceptable`
3. ✅ Compatibilidad preservada
   - `_market_edge` intacto
   - Endpoints existentes sin cambios
   - `asyncio.wait_for(timeout=3.0)` intacto

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

**Frontend (V1) — entregables**
1. ✅ `/app/frontend/src/pages/DashboardPage.jsx`
   - `DiscardedRow` expandible (▼): trapSignals estructuradas + fragility
   - `RescuedRow` (verde): `whyDirectMarketsFailed` + `whyThisMarketIsSafer`
   - `WatchlistRow` (ámbar)
   - `FragilityChip` (semáforo por score)
   - Nuevas secciones: `rescued`, `protected-acceptable`, `watchlist`
   - Empty state mejorado
2. ✅ Lint / build OK
3. ✅ Screenshots verificados

---

### Phase 4 — P0 LIVE Hardening + P2 Historical Profile
**Estado:** ✅ COMPLETADO

**Objetivo:** restaurar LIVE multi-deporte + evitar matches zombies + eliminar fugas de vocabulario + enriquecer rescate Under con histórico (15).

#### 4.1 P0-1 — LIVE basketball/baseball no detectaba partidos
**Estado:** ✅ COMPLETADO

**Problema:** API-Sports v1 para basketball/baseball no soporta `?live=all` (retorna error `The Live field do not exist.`) ⇒ `ingest_live()` traía 0.

**Cambio implementado**
- ✅ `/app/backend/services/api_sports.py`
  - `fixtures_live(sport)`:
    - Football sigue con `/fixtures?live=all`.
    - Basketball/Baseball ahora hace fetch por fechas: `/games?date=today_utc` + `/games?date=yesterday_utc` y filtra por `status.short` en sets `_LIVE_STATUS_SHORT`.

**Resultado:** `/api/matches/live?sport=basketball|baseball` vuelve a listar partidos LIVE.

#### 4.2 P0-3 — Fútbol mostraba partidos terminados como LIVE (zombies)
**Estado:** ✅ COMPLETADO

**Cambio implementado**
- ✅ `/app/backend/services/live_lifecycle.py`
  - Endgame tightening: `2H` y `minute>=90` con `heartbeat_age>180s` ⇒ se considera stale (“ghost-FT”).
  - Se añade motivo explícito en `compute_live_state()`.
  - Se añadió `BRK` a `LIVE_STATUSES['baseball']`.

**Resultado:** `/api/matches/live?sport=football` deja de mostrar FT/90’ colgados; se archivan via sweeper.

#### 4.3 P0-2 — Sport Routing & Terminology Leakage
**Estado:** ✅ COMPLETADO

**Problema:** picks/explicaciones de MLB/NBA usando vocabulario de fútbol (“goles”, “córners”, “BTTS”), y riesgo de regresiones futuras.

**Cambio implementado**
- ✅ `/app/backend/services/sport_vocab_guard.py` (NUEVO)
  - Firewall estricto por deporte (regex). Si detecta vocabulario prohibido:
    - Re-rutea picks contaminados a `summary.discarded_market` con razón `SPORT_VOCAB_LEAK`.
    - Añade auditoría `_pipeline.sport_vocab_guard`.
- ✅ Integración en pipeline:
  - `analyst_engine.py` **Phase 11**: aplica firewall a `picks` y también limpia `rescued_picks/watchlist/protected_acceptable`.
  - `server.py` `/api/matches/live`: defensa final que nulifica/etiqueta `_live_interpreter` si detecta fuga.

**Resultado:** imposible (por diseño) que MLB/NBA vuelvan a mostrar “goles/córners”; se descarta o bloquea el payload.

#### 4.4 P2-1 — Enriquecimiento histórico fútbol (últimos 15)
**Estado:** ✅ COMPLETADO

**Cambio implementado**
- ✅ `data_ingestion._enrich_football(deep=True)`
  - Fetch de `fixtures_last_n(n=15)` para home/away.
  - `normalizer.normalize_recent_fixtures()` genera `historical_goal_profile`:
    - `under_3_5_rate`, `under_2_5_rate`, `team_exceeded_2_goals_rate`, `trend_summary`, etc.
- ✅ `under_market_scan.py`: usa `historical_goal_profile` para boost y reasons.
- ✅ `alternative_rescue.py`: expone `historical_profile` (home/away) directamente en el payload de rescate.

**Resultado:** rescates Under muestran explicación más transparente y trazable (tendencia últimos 15).

#### 4.5 Testing
**Estado:** ✅ COMPLETADO
- ✅ Reporte: `/app/test_reports/iteration_24.json`
  - LIVE basketball: detecta 6 (en el momento de test)
  - LIVE baseball: detecta 8 (en el momento de test)
  - Football stale: archiva 35 zombies
  - Firewall vocabulario: unit tests + integración OK

---

## 3) Next Actions

### A) Hardening de enrichment (P1)
**Motivo:** se observó que una generación puede quedarse en `stage=enriching` (scraping/Understat).

1. Timeouts agresivos + fallback en enrichment Understat (2–4s).
2. Telemetría: tiempos por etapa + ratio de fallos.
3. Job progress reliability: `/api/analysis/jobs/{job_id}` status monotónico.

### B) Regeneración de datos (P1)
1. Ejecutar run desde UI o API:
   - `POST /api/analysis/run` con `{sport, max_matches, background:true, force:true}`
2. Validar en `/api/picks/today`:
   - `summary.rescued_picks`, `summary.watchlist`, `summary.protected_acceptable`
   - `total_rescued`, `total_watchlist`, `total_protected_acceptable`
   - `_pipeline.sport_vocab_guard` presente

### C) Refinamiento (P2)
1. Expandir alias de mercados multi-sport sin sobre-ingeniería.
2. Ajustar severidades/códigos trap según feedback real.
3. Mejorar `whyThisMarketIsSafer` con ejemplos por deporte.

### D) Scrapy integration (P2 — pendiente)
- Migrar scraping a Scrapy (según solicitud), manteniendo Understat via XHR JSON como baseline.
- Plan de proxies residenciales para Sofascore fallback (bloqueado por credenciales).

## 4) Success Criteria
- Aparición de resultados en **Protected acceptable** y/o **Watchlist** cuando corresponde (sin inventar valor).
- `Moneyline favorito` con edge negativo sigue siendo `NO_BET_VALUE`.
- Descartados muestran: mensaje humano + señales trampa + fragility.
- Rescatados muestran: por qué falló el directo + por qué el protegido es más seguro.
- LIVE multi-deporte estable:
  - Basketball/Baseball: LIVE detectado sin depender de `live=all`.
  - Fútbol: no se muestran FT/90’ zombies; sweeper archiva.
  - Firewall vocabulario: no hay “goles/córners” fuera de fútbol.
- No regresiones:
  - endpoints existentes responden
  - `_market_edge` no cambia
  - `asyncio.wait_for(timeout=3.0)` intacto
  - narrativa ES intacta
- Hardening:
  - ningún job queda colgado en `enriching`; si falla Understat, el pipeline termina con degradación elegante.
