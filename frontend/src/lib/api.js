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
