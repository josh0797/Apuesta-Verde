/**
 * appMetadata — Identidad del bundle frontend
 * =============================================
 *
 * Fase 2 de la **Auditoría de Drift de Producción (P0)**.
 *
 * Centraliza la lectura de las variables de versión expuestas por el build
 * system.  El resto del frontend **debe** importar desde aquí y no leer
 * `process.env.REACT_APP_*` directamente, para garantizar una única fuente
 * de verdad.
 *
 * Cascada de lectura (CRA expone solo variables con prefijo REACT_APP_):
 *
 *   1. REACT_APP_COMMIT_SHA  → COMMIT_SHA
 *   2. REACT_APP_APP_VERSION → APP_VERSION
 *   3. REACT_APP_BUILD_TIME  → BUILD_TIME
 *
 * Si una variable no fue inyectada en build-time, su valor será "unknown"
 * y el frontend no debe romperse.
 */

const UNKNOWN = "unknown";

function readEnv(name) {
  // eslint-disable-next-line no-undef
  const raw = typeof process !== "undefined" && process.env ? process.env[name] : undefined;
  if (raw === undefined || raw === null) return UNKNOWN;
  const val = String(raw).trim();
  return val.length > 0 ? val : UNKNOWN;
}

/** SHA corto seguro para logs/headers (no rompe si llega "unknown"). */
function shortSha(sha, length = 7) {
  if (!sha || sha === UNKNOWN) return UNKNOWN;
  const safe = String(sha).replace(/[^A-Za-z0-9]/g, "");
  return safe.slice(0, length) || UNKNOWN;
}

export const APP_VERSION = readEnv("REACT_APP_APP_VERSION");
export const COMMIT_SHA = readEnv("REACT_APP_COMMIT_SHA");
export const COMMIT_SHA_SHORT = shortSha(COMMIT_SHA);
export const BUILD_TIME = readEnv("REACT_APP_BUILD_TIME");

/** Tag opaco usado para detectar drift de bundle vs backend en Producción. */
export const FRONTEND_BUILD_TAG = `${COMMIT_SHA_SHORT}@${BUILD_TIME}`;

export const APP_METADATA = Object.freeze({
  appVersion: APP_VERSION,
  commitSha: COMMIT_SHA,
  commitShaShort: COMMIT_SHA_SHORT,
  buildTime: BUILD_TIME,
  buildTag: FRONTEND_BUILD_TAG,
  auditPhase: "F99-P0-PRODUCTION-DRIFT-AUDIT",
});

let _boot_logged = false;

/**
 * Log idempotente de identidad al boot.  Llamar una sola vez desde index.js.
 * Mantiene el formato estable para que el usuario pueda hacer grep en
 * DevTools/Producción.
 */
export function logAppMetadataOnce() {
  if (_boot_logged) return;
  _boot_logged = true;
  // eslint-disable-next-line no-console
  console.info(
    "[app-version] commit_sha=%s build_time=%s app_version=%s",
    COMMIT_SHA,
    BUILD_TIME,
    APP_VERSION,
  );
}

export default APP_METADATA;
