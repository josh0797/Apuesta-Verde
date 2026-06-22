# DEPLOYMENT AUDIT — Protocolo de validación Producción vs Preview

**Auditoría:** F99-P0 Production Drift Audit (8 fases)
**Estado del repo:** Preview con todas las herramientas de auditoría
implementadas y verdes.
**Producción:** fuera del alcance del agente.  Este documento te entrega los
**comandos exactos** para que tú confirmes (o descartes) el drift.

---

## 0. Resumen de qué se construyó

| Fase | Entregable | Tests |
|---|---|---|
| 1 | `GET /api/debug/version` | 18/18 ✅ |
| 2 | Badge `data-testid="app-version-badge"` + `console.info` boot | 8/8 ✅ |
| 5 | Inventario clasificado en `/app/AUDIT_LEGACY_INVENTORY.md` | n/a |
| 3 | Middleware `X-Backend-Version` + `_meta.backend_version` | 6/6 ✅ |
| 4 | `GET /api/debug/sources` (REGISTERED/ENABLED/DISABLED/UNAVAILABLE) | 12/12 ✅ |
| 6 | Catch-all `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT` (prohíbe `unknown`) | 17/17 ✅ |
| 7 | Cache busting quirúrgico (no global) — middleware + `noStoreConfig()` | 5/5 BE + 4/4 FE ✅ |

Total nuevo: **70 tests P0 verdes** + 4 tests FE.

**Total tests F99 (incluyendo histórico):** 156 verdes en `tests/test_f99_*.py`.

---

## 1. Inventario de URLs

| Entorno | URL base |
|---|---|
| **Preview** | `https://low-volatility-plays.preview.emergentagent.com` |
| **Producción** | `https://low-volatility-plays.emergent.host` |

Reemplaza `${BASE}` por la URL del entorno que quieras auditar.

---

## 2. Protocolo de validación post-deploy

> Ejecuta este protocolo **inmediatamente después** de cada despliegue a
> Producción.  El objetivo es confirmar que el bundle en runtime coincide
> con el commit que esperas.

### 2.1 — Endpoint de identidad del backend

```bash
# Preview
curl -s https://low-volatility-plays.preview.emergentagent.com/api/debug/version \
  | python3 -m json.tool

# Producción
curl -s https://low-volatility-plays.emergent.host/api/debug/version \
  | python3 -m json.tool
```

**Qué debes ver (ejemplo del estado Preview actual):**

```json
{
    "service": "value-bet-intelligence-backend",
    "environment": "unknown",
    "git_sha": "786c998bd171d9489722ca8ff5787b746dc0fe75",
    "git_sha_short": "786c998",
    "build_timestamp": "2026-06-22T10:11:35+00:00",
    "metadata_source": "git",
    "metadata_source_detail": {
        "git_sha": "git",
        "build_timestamp": "git"
    },
    "python_version": "3.11.15",
    "module_hashes": {
        "backend/server.py": "<sha256>",
        "backend/services/api_football.py": "<sha256>",
        ...
    },
    "audit_phase": "F99-P0-PRODUCTION-DRIFT-AUDIT"
}
```

**Diagnóstico:**

| Caso | Significado |
|---|---|
| `git_sha` en Producción ≠ `git_sha` en Preview | **DRIFT confirmado**: Producción está ejecutando un commit anterior. |
| `git_sha` igual pero `module_hashes` diferentes | **Inconsistencia**: misma rama pero archivos críticos divergen (improbable, indica corruption en imagen). |
| `git_sha = "unknown"` | El runtime no pudo determinar la versión: revisar si la imagen incluye `.git` o setear `GIT_SHA` en deploy pipeline. |
| `build_timestamp = "unknown"` igual que arriba pero solo para timestamp | Mismo diagnóstico. Recomendado: setear `BUILD_TIMESTAMP` en CI. |

**Header esperado:**
```
X-Backend-Version: 786c998
Cache-Control: no-store, max-age=0
```

### 2.2 — Cabecera `X-Backend-Version` en TODA respuesta

```bash
curl -sI https://low-volatility-plays.emergent.host/api/admin/rescue-audit/summary \
  | grep -i 'x-backend-version'
```

Debe devolver el mismo SHA corto que `/api/debug/version`.  Si difiere,
hay **dos procesos backend distintos** sirviendo tráfico (load balancer
sin alinear).

### 2.3 — Endpoint de fuentes activas

```bash
curl -s https://low-volatility-plays.emergent.host/api/debug/sources \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['summary'], indent=2)); [print(f\"  {s['name']:20s} → {s['status']}\") for s in d['sources']]"
```

**Estado esperado (Preview actual):**

```
{
  "total": 12,
  "by_status": {"DISABLED": 1, "ENABLED": 9, "UNAVAILABLE": 0, "REGISTERED": 2}
}
  api_sports           → DISABLED          ← invariante crítica
  sofascore            → ENABLED
  thestatsapi          → ENABLED
  theoddsapi           → ENABLED
  sportytrader         → ENABLED
  thesportsdb          → ENABLED
  forebet              → ENABLED
  mlb_stats_api        → ENABLED
  espn                 → ENABLED
  statsbomb            → REGISTERED        ← F99.8 pendiente
  fbref                → REGISTERED        ← F99.8 pendiente
  score365             → ENABLED
```

**Si Producción muestra `api_sports → ENABLED`:** el deploy es anterior
a F99.2 y debe rehacerse.

**Si SofaScore aparece `UNAVAILABLE`:** el módulo
`services.external_sources.sofascore` no está empaquetado en la imagen
de Producción → bug de build.

### 2.4 — `_meta.backend_version` en el payload de `/api/analysis/run`

(Requiere autenticación; ejecuta logueado desde la consola del navegador.)

```js
// En DevTools → Console, después de loguearte:
fetch('/api/analysis/run', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${localStorage.getItem('vbi_token')}`,
    'Cache-Control': 'no-store',
  },
  body: JSON.stringify({
    sport: 'football', refresh: true, include_live: false,
    max_matches: 1, background: false,
  }),
}).then(r => r.json()).then(d => console.log(d._meta));
```

**Debe imprimir:**

```js
{
  backend_version: {
    git_sha: "786c998...",
    git_sha_short: "786c998",
    build_timestamp: "2026-06-22T10:11:35+00:00",
    metadata_source: { git_sha: "git", build_timestamp: "git" },
    audit_phase: "F99-P0-PRODUCTION-DRIFT-AUDIT"
  }
}
```

### 2.5 — Identidad del Frontend (bundle activo)

En el navegador apuntando a Producción:

```js
// DevTools → Console: el log de boot debe aparecer una sola vez.
// Buscar la línea:
//   [app-version] commit_sha=<sha> build_time=<iso> app_version=<x.y.z>

// Inspeccionar el badge embebido en el DOM:
document.querySelector('[data-testid="app-version-badge"]').dataset
```

**Debe imprimir:**
```js
{
  commitSha: "...",
  commitShaShort: "...",
  buildTime: "...",
  appVersion: "...",
  auditPhase: "F99-P0-PRODUCTION-DRIFT-AUDIT"
}
```

**Si los valores son todos `"unknown"`:** el pipeline de build no está
inyectando las variables `REACT_APP_COMMIT_SHA`, `REACT_APP_BUILD_TIME`,
`REACT_APP_APP_VERSION`.  **Requiere configurar el CI** para emitir esos
valores en build-time.

### 2.6 — Coherencia FE/BE (mismo deploy)

Cruza los SHAs:

| Fuente | Cómo obtenerlo |
|---|---|
| Backend SHA | `curl ${BASE}/api/debug/version | jq -r .git_sha` |
| Frontend SHA | desde el badge `data-commit-sha` |

Si **coinciden** → deploy consistente.
Si **difieren** → frontend y backend salieron de commits distintos
(builds desacoplados); validar pipeline.

---

## 3. Variables de entorno recomendadas en el pipeline de deploy

Para que `/api/debug/version` y el badge frontend devuelvan información
real (en vez de derivar de `.git`), exporta en el CI/deploy:

### Backend (FastAPI)

| Variable | Valor sugerido | Notas |
|---|---|---|
| `GIT_SHA` | `${CI_COMMIT_SHA}` (o equivalente) | SHA completo |
| `BUILD_TIMESTAMP` | ISO-8601 del momento del build | **No** uses el momento de boot del contenedor |
| `ENVIRONMENT` | `production` / `preview` / `staging` | identificador opaco |

### Frontend (CRA — variables embebidas en build-time)

| Variable | Valor sugerido |
|---|---|
| `REACT_APP_COMMIT_SHA` | `${CI_COMMIT_SHA}` |
| `REACT_APP_BUILD_TIME` | ISO-8601 del build |
| `REACT_APP_APP_VERSION` | semver o tag del release |

> Si el CI no las setea, el sistema sigue funcionando con `"unknown"`
> (cascada fail-soft).  Solo pierdes capacidad de diagnóstico.

---

## 4. Matriz de diagnóstico de drift

Cuando un usuario reporte comportamiento legacy en Producción (p. ej.
`source: api_sports`, `"Watchlist descartado por unknown"`,
`"SPORTYTRADER NO ENCONTRADO"`), ejecuta el protocolo y mapea contra esta
tabla:

| Síntoma | Endpoint a consultar | Diagnóstico |
|---|---|---|
| `source: api_sports` en un partido de fútbol | `/api/debug/sources` → `api_sports.status` | Si **ENABLED**: deploy es anterior a F99.2. **Si DISABLED**: el partido fue ingestado por un worker anterior al deploy; basta con re-ingestar (refresh=true). |
| `"Watchlist descartado por unknown"` | `/api/debug/version` → comparar SHAs | Si los SHAs difieren entre Preview y Producción: deploy obsoleto. Si coinciden: el match document en DB es viejo; re-ingest con `refresh=true`. |
| `"SPORTYTRADER NO ENCONTRADO"` aparece donde no debería | `/api/debug/sources` → `sportytrader.status` | Si UNAVAILABLE/DISABLED → falla del scraper. Si ENABLED → editorial real no encontró ese partido, es un mensaje legítimo. |
| El frontend muestra UI obsoleta | Badge DOM `data-testid="app-version-badge"` | Si `commit_sha` ≠ deploy actual → CDN/cache del cliente; forzar hard refresh + invalidar CDN. |
| Header `X-Backend-Version` diferente entre dos requests consecutivas | Trampear con `curl` repetido | Hay **dos pods** con builds distintos detrás del load balancer; reiniciar el deployment para alinearlos. |

---

## 5. Cómo invalidar caches sin romper la app

1. **Cliente (browser)**:
   - Hard refresh: `Ctrl+Shift+R` / `Cmd+Shift+R`.
   - DevTools → Application → Clear site data (preserva login, limpia caches).
   - Verificar que el badge cambia de `commit_sha` tras el refresh.

2. **CDN / Cloudflare** (si aplica):
   - Purgar específicamente los paths:
     - `/static/js/*`
     - `/static/css/*`
     - `/index.html`
   - **No** purgar `/api/*` (allí ya forzamos `no-store` desde el server).

3. **Backend**:
   - El cache busting es **automático** para endpoints dinámicos:
     - `/api/analysis/run`
     - `/api/analysis/jobs/*`
     - `/api/debug/*`
     - cualquier endpoint con `?refresh=true`

---

## 6. Comandos de regresión (post-deploy)

Si quieres ejecutar el bloque de tests P0 contra una imagen recién
desplegada (en el container directamente):

```bash
cd /app/backend && python -m pytest tests/test_f99_p0_audit_*.py -v
```

Resultado esperado:

```
============== 58 passed in <1s ==============
```

Y para frontend:

```bash
cd /app/frontend && CI=true npx react-scripts test --watchAll=false \
  --testPathPattern='(AppVersionBadge|appMetadata|noStoreConfig)'
```

Resultado esperado: **12 passed**.

---

## 7. Checklist final (para ti, post-deploy)

- [ ] `GET /api/debug/version` responde 200 con `git_sha` real (no `unknown`).
- [ ] `GET /api/debug/sources` responde 200 con `api_sports.status == "DISABLED"`.
- [ ] Header `X-Backend-Version` aparece en TODA respuesta del backend.
- [ ] Badge `data-testid="app-version-badge"` está en el DOM con `commit_sha` real.
- [ ] El SHA del frontend coincide con el SHA del backend.
- [ ] `POST /api/analysis/run` devuelve `_meta.backend_version` con el SHA correcto.
- [ ] Un partido de fútbol generado tras el deploy **no** muestra `source: api_sports`.
- [ ] Un partido descartado no muestra `"descartado por unknown"`; en su lugar
      muestra un tag específico o `"motivo no clasificado (revisión pendiente)"`
      con un log WARNING `UNCLASSIFIED_DISCARD` en el backend.

---

## 8. Próximos pasos sugeridos

Una vez confirmado/descartado el drift:

1. **Si fue drift**: configurar el pipeline para emitir `GIT_SHA` y
   `BUILD_TIMESTAMP` (sección 3) → así la próxima ocurrencia se detecta
   instantáneamente.
2. **Si no fue drift** sino datos viejos en Mongo: forzar
   `refresh=true` sobre los matches afectados y volver a generar pick_run.
3. **Reanudar F99.7** (Wire del odds aggregator) — actualmente suspendido.
4. **Reanudar F99.8** (StatsBomb/FBref background) — actualmente suspendido.

---

**Documento generado:** 2026-06-22.
**Auditoría:** F99-P0-PRODUCTION-DRIFT-AUDIT.
