# Plan — Phases F58–F99 (bitácora)

> **Nota:** Este plan se mantiene como bitácora completa.
> **Estado histórico:** ✅ F58–F99.5 completadas / ✅ **P0 Auditoría Drift Producción (8 fases) COMPLETADA en Preview**.
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
- Nuevo módulo: `backend/services/debug_metadata.py`.
  - Cascada de `git_sha`/`build_timestamp`:
    1) `GIT_SHA` / `BUILD_TIMESTAMP`
    2) alternativas equivalentes (`COMMIT_SHA`, `SOURCE_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, etc.)
    3) `git rev-parse HEAD` + timestamp del commit **solo si** `.git` y `git` están disponibles
    4) `unknown` (sin 500)
  - Campo `metadata_source` + `metadata_source_detail`.
  - `module_hashes`: SHA-256 determinista de módulos críticos.
- Endpoint: `GET /api/debug/version` en `backend/routers/debug_router.py`.
  - Header: `X-Backend-Version` (sha corto) + `Cache-Control: no-store`.

**Tests dirigidos (entregado):**
- `backend/tests/test_f99_p0_audit_debug_version.py` (**18 passed**).

**Evidencia (Preview):**
- `GET /api/debug/version` devuelve `audit_phase=F99-P0-PRODUCTION-DRIFT-AUDIT` y `git_sha_short=786c998`.

---

### Fase 2 — Identidad del Frontend (metadata centralizada + badge DOM)
**Estado:** ✅ COMPLETADO.

**Objetivo:** demostrar qué bundle frontend está ejecutándose.

**Implementación (entregado):**
- Metadata centralizada: `frontend/src/lib/appMetadata.js`.
  - Variables (CRA): `REACT_APP_APP_VERSION`, `REACT_APP_COMMIT_SHA`, `REACT_APP_BUILD_TIME`.
  - Exporta `APP_METADATA` + `logAppMetadataOnce()`.
- Badge DOM (oculto, siempre presente): `frontend/src/components/AppVersionBadge.jsx`.
  - `data-testid="app-version-badge"` + dataset con `commit_sha`, `build_time`, `app_version`.
- Integración:
  - `frontend/src/index.js`: `logAppMetadataOnce()` al boot.
  - `frontend/src/App.js`: renderiza `<AppVersionBadge />`.

**Tests dirigidos (FE):**
- `frontend/src/lib/__tests__/appMetadata.test.js` (4 tests).
- `frontend/src/components/__tests__/AppVersionBadge.test.js` (4 tests).

**Evidencia (Preview):**
- Badge presente en DOM con `data-audit-phase=F99-P0-PRODUCTION-DRIFT-AUDIT`.
- En Preview actual las vars de build no están inyectadas → valores `unknown` (esperado; se recomienda configurar CI para Producción).

---

### Fase 5 — Inventario de referencias legacy (grep + clasificación)
**Estado:** ✅ COMPLETADO.

**Objetivo:** determinar si textos legacy existen en el código actual o solo provienen de despliegues/cachés antiguas.

**Output entregado:**
- `/app/AUDIT_LEGACY_INVENTORY.md` con:
  - resumen por patrón
  - clasificación por archivo
  - ruta de ejecución probable
  - acción recomendada

**Hallazgos clave:**
- `SPORTYTRADER` aparece mayormente como proveedor real + UI fallback; no es “texto fósil” por sí solo.
- `api_sports` sigue existiendo en ámbitos no-fútbol; en fútbol está fail-closed por stub (F99.2). Si aparece activo en fútbol en Producción → drift.
- “Watchlist descartado por unknown” se originaba en `football_market_trace.py` por fallback de `rejection_code`.

---

### Fase 3 — Trazado end-to-end (backend → cliente)
**Estado:** ✅ COMPLETADO.

**Objetivo:** vincular cada respuesta y ejecución con la versión exacta del backend.

**Implementación (entregado):**
- `backend/server.py`:
  - Middleware global que inyecta `X-Backend-Version` en **todas** las respuestas.
  - Helper `_build_response_meta()`.
  - Inyección de `_meta.backend_version` en respuestas de `/api/analysis/run`.

**Tests dirigidos:**
- `backend/tests/test_f99_p0_audit_backend_version_meta.py` (**6 passed**).

---

### Fase 4 — Auditoría de fuentes activas `/api/debug/sources`
**Estado:** ✅ COMPLETADO.

**Objetivo:** ver en runtime qué proveedores están registrados y si están habilitados.

**Implementación (entregado):**
- Nuevo módulo: `backend/services/debug_sources.py`.
  - Estados: `REGISTERED | ENABLED | DISABLED | UNAVAILABLE`.
  - Invariante crítica aplicada: `api_sports` en fútbol **nunca** puede reportarse como `ENABLED`.
- Endpoint: `GET /api/debug/sources` en `backend/routers/debug_router.py`.
  - `Cache-Control: no-store`.

**Tests dirigidos:**
- `backend/tests/test_f99_p0_audit_debug_sources.py` (**12 passed**).

**Evidencia (Preview):**
- `api_sports: DISABLED`, `9 ENABLED`, `2 REGISTERED`, `0 UNAVAILABLE`.

---

### Fase 6 — Logs estructurados de descartes (prohibido `unknown`)
**Estado:** ✅ COMPLETADO.

**Objetivo:** eliminar descartes con reason_code genérico `unknown` y forzar trazabilidad.

**Implementación (entregado):**
- `backend/services/football_market_trace.py`:
  - `rejection_code` vacío/UNKNOWN → `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`.
  - Tag legible: `motivo no clasificado (revisión pendiente)`.
  - Warning log con contexto estructurado para rastreo.

**Tests dirigidos:**
- `backend/tests/test_f99_p0_audit_discard_reason_code.py` (**17 passed**).
- Regresión parcial: `pytest -k market_trace` (**44 passed**, sin regresión).

---

### Fase 7 — Cache busting del Frontend (quirúrgico)
**Estado:** ✅ COMPLETADO.

**Objetivo:** evitar que caches del cliente oculten despliegues o persistan resultados viejos.

**Implementación (entregado):**
- Frontend (axios):
  - `frontend/src/lib/api.js`: exporta `noStoreConfig()` para aplicar headers anti-cache **solo** en endpoints dinámicos.
  - `frontend/src/pages/DashboardPage.jsx`: aplica `noStoreConfig()` en llamadas a `/analysis/run` con `refresh=true`.
- Backend:
  - `backend/server.py` middleware: aplica `Cache-Control: no-store` **solo** a:
    - `/api/analysis/run`
    - `/api/analysis/jobs*`
    - `/api/debug/*`
    - requests con `?refresh=true`

**Tests:**
- `backend/tests/test_f99_p0_audit_cache_busting.py` (**5 passed**).
- `frontend/src/lib/__tests__/noStoreConfig.test.js` (4 passed).

---

### Fase 8 — Checklist de despliegue + validación en Producción (documentación)
**Estado:** ✅ COMPLETADO.

**Entregable:**
- `/app/DEPLOYMENT_AUDIT.md`:
  - comandos exactos (`curl` + DevTools snippets)
  - comparación SHA frontend vs backend
  - matriz de diagnóstico (drift vs caché vs datos viejos)
  - recomendaciones de variables de CI (`GIT_SHA`, `BUILD_TIMESTAMP`, `REACT_APP_*`)

---

## 3) Pendientes y siguientes pasos

### Pendientes P0 (actual)
- ✅ Auditoría Drift Producción completada en Preview.
- **Acción del usuario (pendiente fuera del repo):** desplegar a Producción y ejecutar el protocolo de `/app/DEPLOYMENT_AUDIT.md`.

### Features suspendidas durante la auditoría
- ⏸️ **F99.7** Wire odds aggregator (SUSPENDIDO hasta validación en Producción).
- ⏸️ **F99.8** Background cache StatsBomb/FBref (SUSPENDIDO hasta validación en Producción).

### Próximos pasos del usuario (operativos)
1. Desplegar a Producción.
2. Ejecutar el protocolo de `DEPLOYMENT_AUDIT.md` (sección 2).
3. Comparar `git_sha`/`build_timestamp` entre Preview y Producción.
4. Si hay drift: alinear pipeline para inyectar `GIT_SHA`/`BUILD_TIMESTAMP` y `REACT_APP_COMMIT_SHA`/`REACT_APP_BUILD_TIME`/`REACT_APP_APP_VERSION`.
5. Con auditoría cerrada en Producción: reanudar F99.7 y luego F99.8.

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
- `backend/services/debug_metadata.py` (Fase 1)
- `backend/routers/debug_router.py` (Fases 1+4)
- `backend/services/debug_sources.py` (Fase 4)
- `backend/tests/test_f99_p0_audit_debug_version.py` (18 tests)
- `backend/tests/test_f99_p0_audit_backend_version_meta.py` (6 tests)
- `backend/tests/test_f99_p0_audit_debug_sources.py` (12 tests)
- `backend/tests/test_f99_p0_audit_discard_reason_code.py` (17 tests)
- `backend/tests/test_f99_p0_audit_cache_busting.py` (5 tests)
- `frontend/src/lib/appMetadata.js` (Fase 2)
- `frontend/src/components/AppVersionBadge.jsx` (Fase 2)
- `frontend/src/lib/__tests__/appMetadata.test.js` (4 tests)
- `frontend/src/components/__tests__/AppVersionBadge.test.js` (4 tests)
- `frontend/src/lib/__tests__/noStoreConfig.test.js` (4 tests)
- `AUDIT_LEGACY_INVENTORY.md` (Fase 5)
- `DEPLOYMENT_AUDIT.md` (Fase 8)

**Archivos modificados:**
- `backend/server.py`: middleware `X-Backend-Version` + no-store quirúrgico + `_build_response_meta()` + include `debug_router`.
- `backend/services/football_market_trace.py`: prohíbe “unknown” en descartes, usa `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`.
- `frontend/src/index.js`: `logAppMetadataOnce()`.
- `frontend/src/App.js`: `<AppVersionBadge />`.
- `frontend/src/lib/api.js`: `noStoreConfig()`.
- `frontend/src/pages/DashboardPage.jsx`: aplica `noStoreConfig()` en `/analysis/run`.

**Invariantes validadas en runtime (Preview):**
- `/api/debug/version`: `git_sha_short=786c998`, `metadata_source=git`.
- `/api/debug/sources`: `api_sports=DISABLED`.
- Header `X-Backend-Version=786c998` en respuestas.
- Badge `data-testid="app-version-badge"` presente en el DOM con `data-audit-phase`.
- Cache-Control `no-store` solo en endpoints dinámicos y en requests con `refresh=true`.

**Regresión focalizada:** `246 passed / 2 skipped / 0 failed` sobre módulos críticos (F99.*, F70-F94, market_trace, odds_cascade, api_health).

---

## 6) Validación esperada (estado actual)

- **Meta P0:** demostrar inequívocamente si Producción está corriendo un commit/bundle viejo, una ruta legacy, o un cache persistente.

### Protocolo de tests (auditoría)
- Tras cada fase: tests dirigidos ✅ (completado).
- Tras Fases 1–5: bloque de integración ✅ (se validó con tests P0 + smoke F99).
- Tras Fases 6–8: suite completa backend + testing agent end-to-end:
  - La suite completa (5000+) excede el timeout de la sesión interactiva, pero se ejecutó:
    - regresión focalizada (246 passed)
    - smoke adicional en Preview (endpoints + badge)

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

**Estado:** ✅ Entregado vía `AUDIT_LEGACY_INVENTORY.md` + `DEPLOYMENT_AUDIT.md` + endpoints `/api/debug/*`.
