import React from "react";
import {
  APP_VERSION,
  COMMIT_SHA,
  COMMIT_SHA_SHORT,
  BUILD_TIME,
} from "@/lib/appMetadata";

/**
 * AppVersionBadge
 * ================
 *
 * Fase 2 de la Auditoría de Drift de Producción.
 *
 * Componente visualmente oculto pero presente en el DOM con
 * ``data-testid="app-version-badge"``.  Sirve para que QA/usuarios y tests
 * automatizados puedan inspeccionar qué bundle exacto se está ejecutando
 * en Producción sin tener que abrir la consola.
 *
 * Reglas:
 *  - **Sin secretos**: solo expone commit_sha, build_time y app_version.
 *  - Accesible para tests pero no visible para el usuario final (sr-only).
 *  - Se renderiza una sola vez por mount (no se monta condicionalmente).
 */
export const AppVersionBadge = () => {
  const payload = `commit_sha=${COMMIT_SHA} build_time=${BUILD_TIME} app_version=${APP_VERSION}`;

  return (
    <div
      data-testid="app-version-badge"
      data-commit-sha={COMMIT_SHA}
      data-commit-sha-short={COMMIT_SHA_SHORT}
      data-build-time={BUILD_TIME}
      data-app-version={APP_VERSION}
      data-audit-phase="F99-P0-PRODUCTION-DRIFT-AUDIT"
      aria-hidden="true"
      className="sr-only"
      style={{
        position: "absolute",
        width: 1,
        height: 1,
        padding: 0,
        margin: -1,
        overflow: "hidden",
        clip: "rect(0, 0, 0, 0)",
        whiteSpace: "nowrap",
        border: 0,
      }}
    >
      {payload}
    </div>
  );
};

export default AppVersionBadge;
