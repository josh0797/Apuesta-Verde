# AUDIT LEGACY INVENTORY — Fase 5 (Auditoría Drift Producción · P0)

**Generado:** 2026-06-22
**Auditoría:** F99-P0-PRODUCTION-DRIFT-AUDIT
**Alcance:** identificar referencias a strings legacy (`SPORTYTRADER`,
`api_sports`, `Watchlist descartado`, `unknown` en contextos de descarte)
en el código actual de Preview.

> **Importante:** Esta fase **no borra nada**.  Es un inventario clasificado.
> Las acciones se ejecutarán en fases posteriores (Fase 6 para reason_codes,
> Fase 7 para cache, y limpiezas quirúrgicas validadas con el usuario).

---

## 1. Resumen ejecutivo

| Patrón | Coincidencias totales | Archivos involucrados |
| --- | --- | --- |
| `SPORTYTRADER` / `SportyTrader` / `sportytrader` | 234 líneas | 41 archivos |
| `api_sports` / `API-Sports` / `api-sports.io` | 891 líneas | ~60 archivos |
| `Watchlist descartado` | 12 líneas | **solo** 2 archivos `diagnostics/output/*.json` (artefactos) |
| `unknown` en contexto de descarte | múltiples | concentrado en `football_market_trace.py` + tests |

### Conclusiones preliminares de la Fase 5

1. **`SPORTYTRADER` NO es un texto "fósil olvidado": es un proveedor real
   integrado.**  Su mención no es legacy en el sentido estricto; sí lo es
   el **placeholder de UI** `SPORTYTRADER_NOT_FOUND` que se emite cuando
   el enrich externo falla y la UI lo renderiza como `"Sportytrader no
   encontrado"`.  Esa es una **ruta de fallback activa y alcanzable**.

2. **`api_sports` ya está purgado en el path activo de fútbol** (F99.2:
   `api_football.py` es un stub fail-closed).  Sin embargo:
   - **MLB y NBA** todavía referencian `api_sports`/`API-Sports` en
     `AnalysisProgressModal.jsx` y `data_ingestion.py` — esto está
     **dentro de scope** (F99 era fútbol-only).
   - El campo `meta.primary_source = 'api_sports'` se sigue mostrando
     **en UI de MLB** como default literal (`AnalysisProgressModal.jsx:483,502`).
   - El frontend MLB sigue leyendo `pipelineMeta.api_sports_games_found`.
   - Si Producción muestra `source: api_sports` **en un partido de
     fútbol**, el problema NO está en el código actual: está en el
     **bundle/imagen desplegada en Producción** (commit anterior a F99.2).

3. **`"Watchlist descartado por unknown"` se genera dinámicamente** en
   `football_market_trace.py:559–560`:
   ```python
   return f"{sel}{code_str} descartado por {tag}"
   ```
   donde `tag = rejection_code.replace("_", " ").lower()` cuando
   `rejection_code` no matchea ninguno de los casos conocidos
   (línea 555–556).  Si `rejection_code = "UNKNOWN"` (default de
   línea 533), el header termina como `"… descartado por unknown"`.
   **Esto será atacado por la Fase 6** (prohibir `unknown` como
   reason_code y emitir `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`).

4. **El string literal `"Watchlist descartado"` no aparece en código fuente**:
   solo aparece en JSONs de `diagnostics/output/` (artefactos de prueba).
   Esto **confirma** que el texto observado en Producción se genera
   en runtime con la plantilla anterior.

---

## 2. Clasificación detallada

### 2.1 `SPORTYTRADER` — clasificación por archivo

> Leyenda:
> - 🔴 **ACTIVE_PROD**: código activo en el path de producción.
> - 🟠 **REACHABLE_FALLBACK**: fallback alcanzable bajo cierta condición.
> - 🟡 **UI_TEXT**: solo texto de presentación.
> - 🔵 **TEST_OR_FIXTURE**: test, fixture, snapshot.
> - ⚪ **HISTORICAL_OR_DOC**: docs, historic, scripts diagnóstico.
> - ⚫ **GENERATED**: archivo generado/bundle.

| Archivo | Líneas | Clase | Ruta de ejecución probable | Acción recomendada |
|---|---|---|---|---|
| `backend/services/sportytrader_scraper.py` | 16 | 🔴 ACTIVE_PROD | Scraper de proveedor SportyTrader (`scrape.do`). Usado por `football_external_fallback_orchestrator`. | **Conservar.** Es un proveedor real, no legacy. Validar que no se considera autoritativo cuando devuelve fallo. |
| `backend/services/external_editorial_provider.py` | 29 | 🔴 ACTIVE_PROD | Editorial externo: SportyTrader como **fuente primaria** alternativa. | **Conservar.** Asegurar logging del proveedor real elegido (Fase 3). |
| `backend/services/football_live_visibility.py` | 10 | 🟠 REACHABLE_FALLBACK | Emite `secondary_reasons.append("SPORTYTRADER_NOT_FOUND")` cuando `enriched_external_editorial` no encuentra editorial.  Usado por filtros World Cup live. | **Conservar pero auditar**: este flag es la fuente del texto visible en Producción.  Verificar Fase 6. |
| `backend/services/football_external_fallback_orchestrator.py` | 16 | 🔴 ACTIVE_PROD | Orquestador de fallbacks externos: SportyTrader es uno de los proveedores cascada. | **Conservar.** |
| `backend/services/editorial_context/editorial_source_registry.py` | 6 | 🔴 ACTIVE_PROD | Registry de fuentes editoriales. Define identidad `sportytrader`. | **Conservar.** Aprovechar para Fase 4 (debug/sources). |
| `backend/services/editorial_context/editorial_normalizer.py` | n/a | 🔴 ACTIVE_PROD | Normaliza la salida del scraper SportyTrader. | **Conservar.** |
| `backend/services/editorial_context/editorial_spider_main.py` | n/a | 🔴 ACTIVE_PROD | Spider/scheduler. | **Conservar.** |
| `backend/services/fallback_scraper.py` | 10 | 🟠 REACHABLE_FALLBACK | Cliente HTTP genérico utilizado por SportyTrader/Forebet/365Scores. | **Conservar.** |
| `backend/services/fixture_time_status_gate.py` | n/a | 🟠 REACHABLE_FALLBACK | Lógica de visibilidad por estado del partido; consulta SportyTrader como señal. | **Conservar.** |
| `backend/services/external_sources/odds_cascade.py` | n/a | 🟠 REACHABLE_FALLBACK | Cascada de odds con SportyTrader. | **Conservar.** |
| `backend/services/external_sources/odds_portal_client.py` | n/a | 🟠 REACHABLE_FALLBACK | Cliente de odds; menciona SportyTrader como peer. | **Conservar.** |
| `backend/services/external_sources/score365_trends_client.py` | n/a | 🟠 REACHABLE_FALLBACK | Cliente trends 365Scores; menciona SportyTrader en docs. | **Conservar.** |
| `backend/services/football_llm_odds_hallucination_guard.py` | n/a | 🟠 REACHABLE_FALLBACK | Guarda contra alucinaciones LLM; trace SportyTrader. | **Conservar.** |
| `backend/services/provenance.py` | n/a | 🔴 ACTIVE_PROD | Provenance helpers; incluye SportyTrader como source. | **Conservar.** |
| `backend/services/scrape_do_client.py` | n/a | 🔴 ACTIVE_PROD | Cliente scrape.do compartido por SportyTrader/Forebet/365Scores. | **Conservar.** |
| `backend/services/api_health_check.py` | 6 | 🔴 ACTIVE_PROD | Health check de fuentes; SportyTrader incluida. | **Conservar.** Útil para Fase 4. |
| `backend/server.py` | 6 | 🔴 ACTIVE_PROD | Endpoints que exponen estado SportyTrader (`api_health_check`, etc). | **Conservar.** |
| `frontend/src/components/EditorialPredictionPanel.jsx:212` | 4 | 🟡 UI_TEXT | Texto literal: `'Fallback interno — Sportytrader no encontrado · generado desde tu modelo L5/L15'`. | **Mantener como UI text legítimo de fallback.** Es un mensaje informativo al usuario, no un bug. |
| `frontend/src/components/ExternalEditorialPanel.jsx` | 15 | 🟡 UI_TEXT | Panel de editorial externo con métricas SportyTrader. | **Conservar.** |
| `frontend/src/components/Scores24ReviewBadge.jsx:38` | 14 | 🟡 UI_TEXT | Mapping: `missing: 'Sportytrader no encontrado'`. | **Conservar** — texto correcto cuando no hay editorial. |
| `frontend/src/components/ProvenancePanel.jsx` | n/a | 🟡 UI_TEXT | Render del proveedor por nombre. | **Conservar.** |
| `frontend/src/components/WorldCupLiveCard.jsx` | n/a | 🟡 UI_TEXT | Renderiza badges incluyendo `SPORTYTRADER_NOT_FOUND` cuando es `secondary_reason`. | **Conservar (UI legítima).** El reason_code es válido. |
| `backend/tests/test_f70_external_editorial.py` | 21 | 🔵 TEST_OR_FIXTURE | Tests externos editorial. | **No tocar.** |
| `backend/tests/test_f71_market_identity_and_reconciliation.py` | 6 | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_f72_forebet_audit_and_opponent_strength.py` | 2 | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_f94_2_world_cup_visibility.py` | n/a | 🔵 TEST_OR_FIXTURE | Asserts `SPORTYTRADER_NOT_FOUND` como reason_code. | **No tocar.** |
| `backend/tests/test_f94_live_visibility.py` | 4 | 🔵 TEST_OR_FIXTURE | Idem. | **No tocar.** |
| `backend/tests/test_fixture_time_status_gate.py` | 10 | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_d9_odds_cascade_iteration4.py` | 9 | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_d9_market_trace_ui_parity_iteration10.py` | n/a | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_market_identity_auto_resolver_and_trends.py` | 8 | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_api_health_check.py` | n/a | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/tests/test_f69_internal_editorial_match_specific.py` | n/a | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `frontend/src/components/__tests__/WorldCupLiveCard.test.jsx` | n/a | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `backend/test_p3_selector_tuning.py` | 4 | 🔵 TEST_OR_FIXTURE | Script de tuning legacy en `backend/` (no en `tests/`). | **No tocar** — no es ejecutado en CI. |
| `diagnostics/football_e2e_recommendation_trace.py:143,156` | 4 | ⚪ HISTORICAL_OR_DOC | Script diagnóstico que documenta el reason_code `SPORTYTRADER_NOT_FOUND_REFERENCED`. | **Conservar como diagnóstico.** |
| `test_reports/iteration_2.json`, `iteration_25.json`, `iteration_26.json`, `iteration_27.json` | 8 | ⚪ HISTORICAL_OR_DOC | Reportes de tests pasados. | **No tocar.** |
| `test_reports/p3_editorial_context_test_report.md` | 3 | ⚪ HISTORICAL_OR_DOC | Reporte legacy. | **No tocar.** |
| `tests/backend_test_p4_playwright.py` | n/a | 🔵 TEST_OR_FIXTURE | | **No tocar.** |
| `plan.md` | 3 | ⚪ HISTORICAL_OR_DOC | Este propio plan. | **No tocar.** |
| `.emergent/emergent_todos.json` | n/a | ⚫ GENERATED | Generado por la plataforma. | **No tocar.** |

**Conclusión sub-bloque 1:** No hay código activo de fútbol que "olvide"
limpiar SportyTrader.  Las apariciones son legítimas (proveedor real + UI
de fallback).  El texto `"SPORTYTRADER NO ENCONTRADO"` observado en
Producción es **producido por `football_live_visibility.py` + UI**, y es
correcto **solo si el proveedor falló**.

---

### 2.2 `api_sports` — clasificación

| Archivo | Líneas | Clase | Notas |
|---|---|---|---|
| `backend/services/api_football.py` (todo el archivo) | n/a | 🔴 ACTIVE_PROD (STUB) | F99.2: fail-closed; sin IO.  Conservado solo por compatibilidad de imports. |
| `backend/services/data_ingestion.py` | 26 | 🔴 ACTIVE_PROD (NON-FOOTBALL) | Importa stub para MLB/NBA/legacy. Confirmar que **fútbol no llama** funciones del stub. |
| `backend/server.py` | 24 | 🔴 ACTIVE_PROD | Endpoints históricos + meta. Mayoría son MLB/NBA o documentación. |
| `backend/services/football_corners_provider.py` | 18 | 🟠 REACHABLE_FALLBACK | Provider corners. Tras F99 esto debería estar inerte para fútbol; verificar runtime con `/api/debug/sources` (Fase 4). |
| `backend/services/box_score_providers/baseball.py`, `basketball.py`, `common.py` | 39 | 🔴 ACTIVE_PROD (NON-FOOTBALL) | MLB/NBA box scores — fuera del alcance F99. |
| `backend/services/football_corners_history.py` | 13 | 🟠 REACHABLE_FALLBACK | Llama `api_football.team_corner_form` (stub). Devolverá `[]`/`None`. |
| `backend/services/football_finished_game_settler.py` | 12 | 🟠 REACHABLE_FALLBACK | Settler usa stub; al estar el cliente fail-closed no impacta. |
| `backend/services/api_health_check.py` | 10 | 🔴 ACTIVE_PROD | Health check de proveedores; expone `api_sports` status. |
| `backend/services/injury_intelligence/injury_sources.py` | 8 | 🟠 REACHABLE_FALLBACK | Multi-sport injuries; api-sports como uno más. |
| `backend/services/external_sources/thestatsapi_*` | 22 | 🟠 REACHABLE_FALLBACK | Adaptadores con menciones documentales a api-sports como punto de comparación. |
| `backend/services/_ingestion_helpers/football_odds_cascade.py` | 8 | 🟠 REACHABLE_FALLBACK | Importa stub; en práctica no debería invocar IO. |
| `frontend/src/components/AnalysisProgressModal.jsx` | 7 | 🟡 UI_TEXT | **Mensaje activo en UI** para MLB/NBA: `"API-Sports no encontró juegos…"`. **Fuera de scope F99 (fútbol).** Si Producción muestra esto para fútbol, indica drift. |
| `backend_mlb_fallback_test.py`, `backend/test_phase21_*` | 8 | 🔵 TEST_OR_FIXTURE | | |
| `plan.md`, `test_reports/**`, `*.md` | n/a | ⚪ HISTORICAL_OR_DOC | | |
| (resto de tests `tests/test_*.py`) | n/a | 🔵 TEST_OR_FIXTURE | | |

**Conclusión sub-bloque 2:**
- En **fútbol**, ninguna ruta activa hace IO real a api-sports (gracias
  a F99.2).  Las menciones son imports del stub fail-closed o comentarios.
- En **MLB/NBA**, `api_sports` sigue siendo un proveedor real (esperado).
- Si Producción muestra `source: api_sports` en un partido de fútbol,
  el problema **no está en el código actual**; está en el bundle/imagen
  desplegada (commit anterior a F99.2).

---

### 2.3 `Watchlist descartado por unknown` — análisis de origen

| Origen | Tipo | Acción |
|---|---|---|
| `backend/services/football_market_trace.py:559–560` (`f"{sel}{code_str} descartado por {tag}"`) | 🔴 ACTIVE_PROD | **Fuente generativa.** Cuando `rejection_code` no matchea ningún `elif`, el fallback `tag = rejection_code.replace("_", " ").lower()` produce `"unknown"`. |
| `backend/services/football_market_trace.py:533` (`rejection_code = t.get("rejection_code") or "UNKNOWN"`) | 🔴 ACTIVE_PROD | **Fuente del valor `UNKNOWN`.** Default cuando el trace no tiene un código explícito. |
| `diagnostics/output/football_analysis_run_raw.json`, `football_ui_feed_raw.json` | ⚫ GENERATED | Artefactos: confirman que el bug existe **al menos** en estos snapshots históricos. No son código fuente. |
| `backend/services/analyst_engine.py:1194` (`"…Descartado por mercado frágil / cuotas / volatilidad."`) | 🔴 ACTIVE_PROD | String legítimo (no genera `unknown`). |

**Acción Fase 6 (planificada):**
- Sustituir el fallback `tag = rejection_code.replace("_", " ").lower()`
  por un mapeo explícito de códigos conocidos + emisión de
  `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT` con contexto cuando el código
  recibido **no pertenece** al catálogo válido.
- Loguear `reason_code` y `selection` en estructura para rastreo.

---

### 2.4 Otros usos de `unknown` (NO eliminar globalmente)

| Tipo de uso | Acción |
|---|---|
| Default semántico en debug (`metadata_source: "unknown"` en `/api/debug/version`) | 🟢 **Mantener.** Es el contrato. |
| Defaults en payloads (`market: "UNKNOWN"`, `code: "UNKNOWN"`) | 🟢 **Mantener.** Forma parte del schema F87. |
| Logs de info (`league_id=unknown`) | 🟢 **Mantener.** |
| Fallback de `rejection_code` en `football_market_trace.py` | 🔴 **Cambiar en Fase 6.** |

---

## 3. Hipótesis sobre el drift de Producción

Dado que el código de Preview:

- ya tiene `api_football.py` como stub fail-closed (F99.2),
- ya wirea SofaScore en odds aggregator (F99.5),
- ya entrega `medium_confidence` correctamente,

…y Producción muestra:

- `source: api_sports`,
- `"Watchlist descartado por unknown"`,
- `"SPORTYTRADER NO ENCONTRADO"` sin contexto,

**la hipótesis dominante** es:

1. **Bundle backend obsoleto** en la imagen de Producción (commit anterior
   a F99.2 / F99.5).  ⇒ El endpoint `/api/debug/version` (Fase 1)
   responderá con un `git_sha` distinto al de Preview, confirmándolo.
2. **Bundle frontend obsoleto** en CDN o cache del cliente.  ⇒ El badge
   `data-testid="app-version-badge"` (Fase 2) revelará distinto
   `commit_sha` y/o `build_time` que el deploy actual.

**El protocolo de validación en Fase 8** dará al usuario los comandos
exactos para confirmar (o descartar) cada hipótesis.

---

## 4. Inventario de archivos clave para fases siguientes

- Fase 3 (X-Backend-Version + `_meta.backend_version`): integrar en
  `server.py` en el endpoint principal `POST /api/analysis/run`.
- Fase 4 (`/api/debug/sources`): registro de proveedores se concentra en
  `editorial_source_registry.py` y `api_health_check.py`.  La fuente
  principal a marcar como `DISABLED` es `api_sports` (en el contexto
  fútbol) — no se elimina, pero **debe reportarse explícitamente como
  DISABLED**.
- Fase 6 (reason_code): `football_market_trace.py:523–560` es el punto
  de cambio único.
- Fase 7 (cache busting): revisar `frontend/src/lib/api.js` y consumidores
  React Query.

---

## 5. Hallazgos que NO ameritan cambio

- 🟡 UI: `"Sportytrader no encontrado"`, `"API-Sports no encontró juegos"`
  son mensajes legítimos para el usuario; no son texto fósil.  Lo
  problemático es que aparezcan **cuando la fuente real era SofaScore**,
  no SportyTrader.  Esto se detectará con `X-Backend-Version` (Fase 3) y
  `_provenance_*` en los traces.

- 🔵 Tests/fixtures: 60+ ocurrencias.  **No se tocan.**

- ⚪ Archivos históricos/diagnostics: se conservan.

---

**Fin del inventario.**
