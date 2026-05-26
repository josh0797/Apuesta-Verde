# plan.md — Market Tolerance + Rescue Layer + UI de trampa/fragilidad (ACTUALIZADO)

## 1) Objectives
- Reducir **falsos descartes**: no tratar igual todo edge negativo; permitir **tolerancia contextual** en mercados protegidos.
- Diferenciar de forma consistente: **AGGRESSIVE / BALANCED / PROTECTED** (y UNKNOWN conservador), y resultados: `VALUE_BET`, `PROTECTED_ACCEPTABLE`, `WATCHLIST`, `NO_BET_VALUE`, `MARKET_TRAP`, `FRAGILE_EDGE`.
- Exponer **trapSignals estructuradas** (código/etiqueta/severidad/explicación) y **fragilityScore 0–100** como elementos UI.
- Añadir **rescate de mercados alternativos** antes de descartar un partido (sin inventar valor).
- Mantener compatibilidad: endpoints existentes, `_market_edge`, payloads legacy y narrativa ES. **No tocar** `asyncio.wait_for(timeout=3.0)`.
- Nuevo (Hardening): evitar que el pipeline se bloquee en **stage `enriching`** por enrichment externo (Understat); añadir timeouts y degradación elegante.

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
   - `apply_moneyball_layer()` ahora crea buckets: `summary.protected_acceptable`, `summary.watchlist`
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
     - Por defecto, rescate **no-direccional** (totales) para evitar invertir el lado
     - `original_pick_side` habilita rescates direccionales solo si coincide
   - Football: delega a `scan_protected_alternatives` (Poisson + H2H) para evitar heurísticas pobres
   - Basketball/Baseball: candidatos protegidos conservadores
2. ✅ `analyst_engine.py`
   - Añadida **Phase 10 Universal Rescue** sobre `summary.discarded_market`
   - Mueve a `summary.rescued_picks` o `summary.watchlist`
   - Reconciliación final actualizada:
     - Nuevas keys y conteos: `total_rescued`, `total_watchlist`, `total_protected_acceptable`
3. ✅ Compatibilidad preservada
   - `_market_edge` intacto
   - Endpoints existentes sin cambios
   - `asyncio.wait_for(timeout=3.0)` intacto

---

### Phase 3 — Frontend UI (V1)
**Estado:** ✅ COMPLETADO

**Frontend (V1) — entregables**
1. ✅ `/app/frontend/src/pages/DashboardPage.jsx`
   - `DiscardedRow` expandible (▼):
     - Render de `trap_signals_structured` (badge severidad + label + explanation)
     - Render de factores de fragilidad
     - Mensaje humano: “Descartado: mercado directo sin valor. Se detectaron N señales trampa.”
   - Nuevos componentes:
     - `RescuedRow` (verde): muestra `whyDirectMarketsFailed` + `whyThisMarketIsSafer`
     - `WatchlistRow` (ámbar)
     - `FragilityChip` con semáforo por score
   - Nuevas secciones:
     - `rescued-section`, `protected-acceptable-section`, `watchlist-section`
   - Empty state mejorado: “0 picks fuertes, pero X watchlist / Y rescatados / Z protegidos aceptables”
2. ✅ Lint / build OK
   - `mcp_lint_javascript`: clean
   - `esbuild`: clean
3. ✅ Screenshots verificados
   - Chips de fragilidad y expansión funcionando

---

## 3) Next Actions

### A) Hardening (recomendado, P1)
**Motivo:** Se observó que una generación quedó en `stage=enriching` ~45% (probable enrichment externo — Understat).

1. **Timeouts agresivos y degradación elegante en enrichment**
   - Enrichment Understat: envolver requests con timeout corto (p.ej. 2–4s) y fallback a “sin enrichment”
   - Evitar que `analysis_run` se quede bloqueado por scraping.
2. **Telemetría/observabilidad**
   - Loggear: tiempo por etapa (ingesting/enriching/LLM/moneyball/rescue)
   - Registrar cuántos enriquecimientos fallaron y por qué.
3. **Job progress reliability**
   - Asegurar que `/api/analysis/jobs/{job_id}` refleje `status` correctamente (no `None`), y que avance de progress sea monotónico.

### B) Regeneración de datos para ver buckets nuevos (P1)
**Motivo:** DB contiene runs pre-refactor.

1. Ejecutar un run nuevo desde UI (“Generar picks del día”) o vía API:
   - `POST /api/analysis/run` con body `{sport, max_matches, background:true, force:true}`
2. Confirmar en `/api/picks/today` que `pick_run.payload.summary` contiene:
   - `rescued_picks`, `watchlist`, `protected_acceptable`
   - `total_rescued`, `total_watchlist`, `total_protected_acceptable`

### C) Refinamiento de catálogo y mapeos (P2)
1. Expandir alias de mercados (multi-sport) sin sobre-ingeniería.
2. Ajustar severidades/códigos trap según feedback real.
3. Mejorar `whyThisMarketIsSafer` con ejemplos por deporte.

## 4) Success Criteria
- Aparición de resultados en **Protected acceptable** y/o **Watchlist** cuando corresponde (sin inventar valor).
- `Moneyline favorito` con edge negativo sigue siendo `NO_BET_VALUE` (guardrail).
- Descartados muestran:
  - mensaje humano
  - lista de señales trampa (no solo conteo)
  - fragilityScore + factores
- Rescatados muestran:
  - “Mercado rescatado”
  - por qué falló el directo
  - por qué el protegido es más seguro
- No regresiones:
  - endpoints existentes siguen respondiendo
  - `_market_edge` no cambia
  - `asyncio.wait_for(timeout=3.0)` intacto
  - narrativa ES intacta
- Hardening:
  - Ningún job se queda colgado en `enriching`; si falla Understat, el pipeline termina igual con degradación elegante.
