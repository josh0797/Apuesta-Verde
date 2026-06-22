import axios from 'axios';

const BASE = process.env.REACT_APP_BACKEND_URL;
if (!BASE) {
  // eslint-disable-next-line no-console
  console.error('REACT_APP_BACKEND_URL is not defined');
}

export const api = axios.create({ baseURL: `${BASE}/api`, timeout: 120000 });

function getToken() { return localStorage.getItem('vbi_token'); }
export function setToken(t) {
  if (t) localStorage.setItem('vbi_token', t);
  else localStorage.removeItem('vbi_token');
}

api.interceptors.request.use((config) => {
  const t = getToken();
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response && err.response.status === 401) {
      setToken(null);
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(err);
  }
);

/**
 * F99-P0 (Fase 7) — Cache busting quirúrgico.
 *
 * Devuelve un fragmento de config axios con headers anti-cache.  Debe
 * usarse **solo** en endpoints dinámicos donde es crítico evitar que un
 * proxy/CDN/cliente sirva un snapshot obsoleto:
 *
 *   - `/api/analysis/run` (y polling de jobs)
 *   - `/api/debug/version`
 *   - `/api/debug/sources`
 *   - cualquier endpoint invocado con `refresh=true`
 *
 * **NO** aplicar globalmente: rompería caches útiles de fixtures, ligas, etc.
 *
 * Uso:
 *
 *   await api.post('/analysis/run', body, noStoreConfig());
 *   await api.get('/debug/version', noStoreConfig());
 *
 * Combina-friendly con otros configs:
 *
 *   await api.get('/foo', { ...noStoreConfig(), params: { ... } });
 */
export function noStoreConfig(extra = {}) {
  const headers = {
    'Cache-Control': 'no-cache, no-store, max-age=0',
    Pragma: 'no-cache',
    ...(extra.headers || {}),
  };
  return { ...extra, headers };
}
