# Plan — Phases F58–F99 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F99.5 completadas / ✅ P0 Auditoría Drift Producción (8 fases) / ✅ **P0 Post-Auditoría: fix real de `api_sports` en pipeline_meta + clasificación de descartes**.
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
- **H2H rico**: dejar de mostrar “se identifican N enfrentamientos…” y renderizar resultados concretos + señales.
- **Córners con fuente secundaria real**: ingestión de stats de córners usando **365Scores** como fallback (a través de scrape.do) y persistencia consistente.
- **Recomendación conservadora de córners**: no recomendar si `corners.available=false` o si solo hay córners actuales sin tendencia.

### Objetivos nuevos / extendidos (F82.1) — Protección de timeouts (crítico)
- Separar enriquecimiento en:
  - **FAST tier obligatorio (inline)**: H2H desde `h2h_recent` + corners desde datos presentes. **Cero HTTP externo**.
  - **EXTERNAL tier opcional**: 365Scores (scrape.do + resolver IDs). **Nunca inline por defecto**.
- Añadir feature flags + timeouts duros para proteger el job principal.

### Objetivos nuevos / extendidos (F83) — Intervención manual de mercado + cuota
- Cuando haya `REQUIRES_MARKET_IDENTIFICATION`, habilitar intervención manual (cuota manual, selector de mercado, recalcular).
- Backend con endpoint POST para reprice + endpoint GET con catálogo de mercados.

### Objetivos nuevos / extendidos (F83.2 / Bloque E) — xG reciente L1/L5/L15 desde shotmap (TheStatsAPI)
- Calcular promedios xG no-penal (a favor / en contra) L1/L5/L15 por equipo usando shotmap TheStatsAPI.
- Arquitectura **background-first** con cache + timeouts.
- Señales contextuales (nunca pick-binding) + señales de cobertura/muestra parcial.

### Objetivos nuevos / extendidos (P4.1) — Estabilidad de tests UI (LiveReevalPanel)
- Mantener suite FE estable (alinear tests con copy y flujos reales).

### Objetivos nuevos / extendidos (F84) — Migración estructural API-Sports → TheStatsAPI (prioridad-inversa)
- Migrar bloques estructurales fútbol a TheStatsAPI como primaria, manteniendo API-Sports como fallback:
  - F84.a Team Stats ✅
  - F84.b H2H ✅
  - F84.e Odds + line movement ✅
- Flags + auditoría `_provenance_*`.

### Objetivos nuevos / extendidos (F85) — Public xG Enrichment (FBref + Forebet vía scrape.do)
- Scraping fail-soft y background-first con endpoints run-now/background/status.
- UI panel para disparo y render.
- Phase 2: resolver FBref search-page + fuzzy matching ✅.

### Objetivos nuevos / extendidos (F86) — H2H Decision Policy (puro Python)
- Definir cuándo H2H puede influir en scoring vs. cuándo es solo narrativo.
- Output: `h2h_context` enriquecido + `h2h_decision` (points_by_market + signals).

### Objetivos nuevos / extendidos (F87) — Cableado quirúrgico en `_enrich_football`
- Integrar H2H decision + xG recent averages (background) sin bloquear el camino crítico.

### Objetivos nuevos / extendidos (F88 / Sprint F86.2) — Editorial Consumer
- Editorial output y UI consumen `h2h_decision` + `xg_recent_averages`.
- Scoring aplica bump H2H al mercado (clamp +8 + guards).

### Objetivos nuevos / extendidos (F89 / Sprint F86.1) — Calibración H2H rules + guards explícitas
- Recalibrar `H2H_POINT_RULES` contra baselines típicas (más robusto).
- Introducir `get_active_rules()` con override por env (JSON) leído en tiempo de llamada.
- Agregar polarity guard explícito (OVER/UNDER por línea + BTTS YES/NO) con auditoría.
- Agregar sample guard por regla (`min_sample`) + señal `LOW_SAMPLE_H2H_SIGNAL`.
- Agregar DNB overlap guard suave (HOME_DNB + AWAY_DNB no es hard-conflict).
- Agregar cap agregado de puntos H2H (`MAX_H2H_POINTS_TOTAL=8`).
- Mantener back-compat con consumers/editorial UI.

### Objetivos nuevos / extendidos (F90 / Sprint F86.2) — Corners cascade con diagnóstico estructurado (Scrape.do)
- Eliminar el mensaje genérico **"Falló la carga de córners"** y reemplazarlo por mensajes específicos según proveedor/etapa/reason_code.
- Exponer endpoint: `GET /api/football/corners/debug?match_id=...`
- Añadir UI debug de córners.

### Objetivos nuevos / extendidos (F91) — MLB Quality Contact Matchup Engine (módulo puro)
- Detectar discrepancias entre calidad de contacto ofensivo vs vulnerabilidad del abridor vs percepción por ERA.
- **No generar picks automáticos**: solo output explicable con señales.

### Objetivos nuevos / extendidos (D13) — MLB Matchup Familiarity Overlay (secundario)
- Implementar una capa contextual MLB basada en enfrentamientos recientes (preferencia: últimos 15 días) que:
  - NO sea pick principal.
  - Sea puro/fail-soft, auditable.
  - **D13.1:** Totales (O/U) ✅
  - **D13.2:** extender a Moneyline + Runline y **permitir impacto en scoring real** con límites y veto ✅

### Objetivos nuevos / extendidos (NIVEL 3) — MLB Totals: Distribution Mixing + Tail Calibration + Threshold Models
- Agregar una capa compatible/auditable que NO reemplace el sistema actual, pero mejore:
  - probas O/U por umbral (7.5/8.5/…)
  - juegos de alta varianza (colas)
- **Bloques (estado actual):**
  - **Bloque 1 (Mixer):** mezcla Poisson/NB dinámica ✅
  - **Bloque 2 (§1-§4):** fórmula de pesos + tail calibration + threshold model + blender **(ACTIVO)** ✅
  - **Bloque 3 (§5-§6):** reglas hard de Under + UI “Distribución y colas” ✅

### Objetivos nuevos / extendidos (D9.2-C) — Residual Model con xG real (Bonferroni estricto)
- Fortalecer el backtest residual para evitar falsos positivos por múltiples comparaciones:
  - Clasificador puro y testeable ✅
  - Corrección Bonferroni estricta ✅
  - Auditoría explícita de umbral ajustado y resultados por métrica ✅

### Objetivos nuevos / extendidos (F87.1) — Fixture Discovery Contract Fix + Visible Audit (con Parte 1.5 upstream)
**Objetivo global:** eliminar “pérdidas invisibles” de fixtures y permitir diagnóstico end-to-end.
**Estado:** ✅ COMPLETADO.

### Objetivo nuevo (F99) — **SofaScore Wiring + eliminación definitiva de API-Sports en fútbol (P0)**
- **Estado:** ✅ COMPLETADO (F99.1–F99.5 + P99.6).
- **Baseline tests (histórico):** 4946 passed, 11 skipped, 0 warnings.

### Objetivo nuevo P0 (CRISIS) — **Auditoría Drift Producción (8 fases)**
**Motivación:** Preview/local correctos (ej. categorización `medium_confidence`), pero Producción ejecuta comportamiento obsoleto (descartes por “mercado frágil”, referencias a `api_sports`, mensajes legados como “SPORTYTRADER NO ENCONTRADO”, “Watchlist descartado por unknown”).

**Objetivos de la auditoría:**
- Demostrar con evidencia objetiva **qué versión** (backend + frontend) está corriendo realmente en Producción.
- Detectar si el problema es:
  - bundle frontend antiguo,
  - imagen backend antigua,
  - ruta/worker legacy desplegado,
  - caché persistente/CDN/service worker,
  - o aún existe código legacy alcanzable.
- Preparar herramientas para que el usuario despliegue y valide en Producción (Producción fuera de alcance del agente).

**Restricciones P0:**
- **Detener** desarrollo de nuevas features F99.7 y F99.8 hasta cerrar la auditoría.
- Endpoints de diagnóstico **no exponen secretos** (tokens/URLs privadas/config completa).
- No hardcodear “Argentina vs Austria”; la auditoría es general.

### Objetivo nuevo P0 (Post-Auditoría) — **Eliminar falsos positivos de `api_sports` y `unknown` en UI/telemetría**
**Motivación:** tras desplegar herramientas P0, el usuario reportó que el panel “Pipeline debug” seguía mostrando `Fuente usada: api_sports` y descartes genéricos.

**Objetivos (post-auditoría):**
- Garantizar que **fútbol** nunca muestre `api_sports` como `source_used/primary_source` (solo MLB/NBA).
- Derivar `source_used/primary_source` de la fuente real ganadora del discovery cascade (F87) y exponer `football_discovery_audit`.
- Evitar que descartes de watchlist caigan a `UNKNOWN` cuando el reason real existe.

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

### Fase 5 — UI Wiring (P2)
**(COMPLETADO)** — sin cambios.

### Fase 6 — Prueba con datos reales (P2)
**(COMPLETADO)** — sin cambios.

### Fase 7 — Smoke tests + verificación final
**(COMPLETADO)** — sin cambios.

---

## Phase P0 — Auditoría Drift Producción (8 fases) — COMPLETADA ✅

> **Regla operativa:** todo se implementa en **Preview**; el usuario despliega a **Producción** y ejecuta comandos de verificación.

### Fase 1 — Endpoint backend `/api/debug/version` (identidad de build)
**Estado:** ✅ COMPLETADO.

**Objetivo:** exponer identidad verificable del backend en runtime.

**Implementación (entregado):**
- `backend/services/debug_metadata.py` (cascada env→git→unknown + `metadata_source` + `module_hashes`).
- `GET /api/debug/version` en `backend/routers/debug_router.py` con `X-Backend-Version` y `Cache-Control: no-store`.

**Tests dirigidos:**
- `backend/tests/test_f99_p0_audit_debug_version.py` (**18 passed**).

---

### Fase 2 — Identidad del Frontend (metadata centralizada + badge DOM)
**Estado:** ✅ COMPLETADO.

**Implementación (entregado):**
- `frontend/src/lib/appMetadata.js`.
- `frontend/src/components/AppVersionBadge.jsx` + montaje en `App.js`.

**Tests FE:**
- `appMetadata.test.js` + `AppVersionBadge.test.js`.

---

### Fase 5 — Inventario de referencias legacy (grep + clasificación)
**Estado:** ✅ COMPLETADO.

**Output:**
- `/app/AUDIT_LEGACY_INVENTORY.md`.

---

### Fase 3 — Trazado end-to-end (backend → cliente)
**Estado:** ✅ COMPLETADO.

**Implementación:**
- Middleware global `X-Backend-Version`.
- `_meta.backend_version` en `/api/analysis/run`.

**Tests:**
- `backend/tests/test_f99_p0_audit_backend_version_meta.py` (**6 passed**).

---

### Fase 4 — Auditoría de fuentes activas `/api/debug/sources`
**Estado:** ✅ COMPLETADO.

**Implementación:**
- `backend/services/debug_sources.py` + `GET /api/debug/sources`.
- Invariante: `api_sports` en fútbol **no puede** ser ENABLED.

**Tests:**
- `backend/tests/test_f99_p0_audit_debug_sources.py` (**12 passed**).

---

### Fase 6 — Logs estructurados de descartes (prohibido `unknown`)
**Estado:** ✅ COMPLETADO (con ampliación post-auditoría).

**Implementación:**
- `football_market_trace.build_discarded_header` no emite `"...unknown"`.
- Catch-all `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT` con log WARNING.

**Tests:**
- `backend/tests/test_f99_p0_audit_discard_reason_code.py` (**25 passed**, ampliado para casos reales de runtime).

---

### Fase 7 — Cache busting del Frontend (quirúrgico)
**Estado:** ✅ COMPLETADO.

**Implementación:**
- `frontend/src/lib/api.js`: `noStoreConfig()`.
- Backend middleware: `Cache-Control: no-store` solo en endpoints dinámicos.

**Tests:**
- `backend/tests/test_f99_p0_audit_cache_busting.py` (**5 passed**).
- `frontend/src/lib/__tests__/noStoreConfig.test.js` (**4 passed**).

---

### Fase 8 — Checklist de despliegue + validación en Producción (documentación)
**Estado:** ✅ COMPLETADO.

**Entregable:**
- `/app/DEPLOYMENT_AUDIT.md`.

---

## Phase P0 — Post-Auditoría (Fix real de `api_sports` en fútbol + clasificación)

### Estado
✅ COMPLETADO en Preview (pendiente de redeploy del usuario a Producción).

### Diagnóstico post-feedback (causas raíz)
1. `backend/server.py` tenía defaults hardcoded:
   - `pipeline_meta["source_used"] = "api_sports"`
   - `pipeline_meta["primary_source"] = "api_sports"`
   Esto forzaba a la UI a mostrar `api_sports` aunque el discovery cascade F87 estuviera usando `thesportsdb`/`thestatsapi`/`espn`/`sofascore`.
2. Frontend tenía defaults literales `|| 'api_sports'` en `AnalysisProgressModal.jsx`.
3. `football_market_trace._derive_rejection_code` no reconocía reasons reales y caía en `UNKNOWN`.

### Cambios implementados
**Backend — `backend/server.py`:**
- Default inicial: `source_used = "unknown"` (no `api_sports`).
- `pipeline_meta.primary_source/source_used` para fútbol se resuelven desde `ingestion.get_last_football_discovery_audit().primary_winner`.
- Nuevo bloque `pipeline_meta.football_discovery_audit` con:
  - `primary_winner`, `sources_called`, `counts_normalised`, `merged`, `total`.
- Mensaje de timeout de ingestión ya no dice “API-Sports lenta” (ahora genérico).

**Frontend — `frontend/src/components/AnalysisProgressModal.jsx`:**
- Defaults cambiados a `unknown`.
- Render de `Fuente primaria` también para fútbol cuando exista `meta.primary_source`.

**Backend — `backend/services/football_market_trace.py`:**
- Nuevas reglas de `_REJECTION_CODE_RULES` para reasons reales:
  - `ODDS_NOT_ATTRACTIVE`
  - `COMPETITIVE_CONTEXT_NORMAL`
  - `WATCHLIST_INSUFFICIENT_SUPPORT`
  - `MARKET_IDENTITY_UNRESOLVED`
- `build_market_trace`: detección del patrón `market_label == "Watchlist"` con promoción a `WATCHLIST_ONLY`/`WATCHLIST_INSUFFICIENT_SUPPORT`.
- `build_discarded_header` y `_humanize_rejection_reason`: tags/mensajes humanos para los nuevos códigos.

### Evidencia / Validación
- Validación API (local Preview):
  - `pipeline_meta.primary_source = "thesportsdb"`
  - `pipeline_meta.source_used = "thesportsdb"`
  - `football_discovery_audit.total = 100`
  - descartes con `rejection_code` específicos (`ODDS_NOT_ATTRACTIVE`, `MARKET_IDENTITY_MISSING`).
- Validación visual (Preview):
  - Screenshot full-page con:
    - “Fuente usada: thesportsdb”
    - “Watchlist descartado por cuotas no atractivas”
    - “Watchlist descartado por mercado no identificado”.

### Tests
- `test_f99_p0_audit_discard_reason_code.py` ampliado: **25 passed**.
- Regresión focalizada por archivos críticos: verde.

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ Herramientas de auditoría + fix post-auditoría completados en Preview.
- ⬜ **Acción del usuario (fuera del repo):** redeploy a Producción para propagar el fix.

### Features suspendidas durante la auditoría
- ⏸️ **F99.7** Wire odds aggregator (SUSPENDIDO hasta validar Producción).
- ⏸️ **F99.8** Background cache StatsBomb/FBref (SUSPENDIDO hasta validar Producción).

### Próximos pasos del usuario (operativos)
1. Redeploy a Producción.
2. Verificar en Producción:
   - `GET /api/debug/version` y `GET /api/debug/sources`.
   - Ejecutar “Selecciones nacionales” y abrir “Pipeline debug”:
     - `Fuente usada` **NO** debe ser `api_sports`.
     - Debe reflejar el ganador real (`thesportsdb`/`thestatsapi`/`espn`/`sofascore_*`).
     - Los descartes no deben caer en “motivo no clasificado” salvo casos realmente auditables.
3. Si vuelve a aparecer `api_sports` en fútbol:
   - Confirmar SHA backend en Producción ≠ Preview (drift) y reiniciar deployment.
4. Una vez validado: reanudar F99.7 y luego F99.8.

---

## 4) Cierres recientes (bitácora)

### ✅ F99.1–F99.5 + P99.6 — COMPLETADO
- F99.1 Adapters offline seed corners ✅
- F99.2 Purga estructural API-Sports ✅
- F99.3 Editorial adapter v2 ✅
- F99.4 Recent Form Extender ✅
- F99.5 Odds Aggregator ✅
- P99.6 Tests críticos ✅
- Baseline histórico: **4946 passed, 11 skipped, 0 warnings**

### ✅ P0 — Auditoría Drift Producción (8 fases) — COMPLETADO
**Artefactos creados:**
- `backend/services/debug_metadata.py`
- `backend/routers/debug_router.py`
- `backend/services/debug_sources.py`
- tests P0 (version/meta/sources/discard/cache)
- FE metadata + badge + tests
- `AUDIT_LEGACY_INVENTORY.md`
- `DEPLOYMENT_AUDIT.md`

### ✅ P0 — Post-Auditoría (Fix real)
**Archivos modificados adicionales:**
- `backend/server.py` (resolver source_used/primary_source por audit F87 + defaults correctos + mensaje timeout genérico)
- `frontend/src/components/AnalysisProgressModal.jsx` (defaults a `unknown` + `Fuente primaria` para fútbol)
- `backend/services/football_market_trace.py` (catálogo de reason_codes real + headers humanos)

---

## 6) Validación esperada (estado actual)

- **Meta P0:** demostrar inequívocamente si Producción está corriendo un commit/bundle viejo, una ruta legacy, un cache persistente, o un default hardcoded.

### Protocolo de tests (auditoría)
- Tras cada fase: tests dirigidos ✅
- Tras Fases 1–5: bloque de integración ✅
- Post-auditoría: pruebas adicionales ✅
  - E2E vía API (`/api/analysis/run` background + polling) validando `pipeline_meta.source_used`.
  - Screenshots full-page en dashboard tras click en “Selecciones nacionales”.

---

## Reglas operacionales + flags

- Reglas:
  - Fail-soft: los endpoints de auditoría no deben levantar 500.
  - **No tocar** `MONGO_URL` ni `REACT_APP_BACKEND_URL`.
  - Producción fuera de alcance del agente: solo herramientas en Preview.
  - Endpoints debug: **sin secretos**, sin config dumps.

- Variables/env (audit) recomendadas para CI:
  - Backend: `GIT_SHA`, `BUILD_TIMESTAMP`, `ENVIRONMENT`.
  - Frontend (CRA): `REACT_APP_APP_VERSION`, `REACT_APP_COMMIT_SHA`, `REACT_APP_BUILD_TIME`.

---

## Evidencia obligatoria al cierre (P0)

Al finalizar cada fase, entregar:
- Archivos modificados.
- Hallazgos.
- Pruebas ejecutadas y resultados.
- Ejemplos reales de respuestas HTTP.
- Riesgos pendientes.
- Comparación Preview/local.
- Comandos exactos para ejecutar tras el despliegue en Producción.

**Estado:** ✅ Entregado vía `AUDIT_LEGACY_INVENTORY.md` + `DEPLOYMENT_AUDIT.md` + endpoints `/api/debug/*` + validación visual full-page.
