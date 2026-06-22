"""
debug_metadata
==============

Helpers puros (sin IO obligatorio) para construir la metadata de identidad
del backend en runtime. Diseñado para la **Auditoría de Drift de Producción**:

- Determina `git_sha` y `build_timestamp` siguiendo la cascada acordada:
    1. Variables canónicas: ``GIT_SHA`` y ``BUILD_TIMESTAMP``.
    2. Alternativas equivalentes ya presentes en el entorno
       (p. ej. ``COMMIT_SHA``, ``SOURCE_COMMIT``, ``BUILD_TIME``).
    3. ``git rev-parse HEAD`` (+ timestamp del commit) **solo** si existe
       ``.git`` y el binario ``git`` está disponible.
    4. Valor explícito ``"unknown"``.  **Jamás** debe propagar 500.

- Calcula hashes deterministas (SHA-256) de archivos críticos para detectar
  drift de bundle.

- **No** expone secretos: solo metadatos opacos.

Este módulo es puro y testeable.  Las funciones aceptan rutas inyectadas y
``os.environ`` se lee una sola vez al inicio de cada llamada para que los
tests puedan parametrizar el entorno.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Constantes ───────────────────────────────────────────────────────────────

UNKNOWN: str = "unknown"

# Fuente declarada en `metadata_source`.
SOURCE_ENVIRONMENT: str = "environment"
SOURCE_ALTERNATIVE_ENV: str = "alternative_env"
SOURCE_GIT: str = "git"
SOURCE_UNKNOWN: str = "unknown"

# Variables canónicas (prioridad 1).
_CANONICAL_SHA_VAR: str = "GIT_SHA"
_CANONICAL_BUILD_VAR: str = "BUILD_TIMESTAMP"

# Variables alternativas reconocidas (prioridad 2).  Orden = preferencia.
_ALTERNATIVE_SHA_VARS: Tuple[str, ...] = (
    "COMMIT_SHA",
    "SOURCE_COMMIT",
    "VERCEL_GIT_COMMIT_SHA",
    "RAILWAY_GIT_COMMIT_SHA",
    "RENDER_GIT_COMMIT",
    "HEROKU_SLUG_COMMIT",
)
_ALTERNATIVE_BUILD_VARS: Tuple[str, ...] = (
    "BUILD_TIME",
    "BUILD_DATE",
    "DEPLOY_TIMESTAMP",
    "VERCEL_GIT_COMMIT_DATE",
)

# Archivos críticos cuyos hashes exponemos en `module_hashes`.
# Las rutas son **relativas a /app** para mantener output portable.
CRITICAL_MODULES: Tuple[str, ...] = (
    "backend/server.py",
    "backend/services/api_football.py",
    "backend/services/football_odds_aggregator.py",
    "backend/services/football_recent_form_consolidator.py",
    "backend/services/football_offline_seed_hydrator.py",
    "backend/services/football_recent_form_hydrator.py",
)

# Nombre del servicio (constante de aplicación; no es secreto).
SERVICE_NAME: str = "value-bet-intelligence-backend"


# ── Helpers internos ─────────────────────────────────────────────────────────


def _read_env(name: str, environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Lee una variable de entorno con sanitización mínima."""
    src = environ if environ is not None else os.environ
    raw = src.get(name)
    if raw is None:
        return None
    val = raw.strip()
    return val or None


def _detect_environment(environ: Optional[Dict[str, str]] = None) -> str:
    """Devuelve un identificador opaco del entorno (no es secreto)."""
    for var in ("ENVIRONMENT", "APP_ENV", "ENV", "DEPLOY_ENV"):
        v = _read_env(var, environ)
        if v:
            return v.lower()
    return UNKNOWN


def _git_available(repo_root: Path) -> bool:
    """True si existe ``.git`` y el binario ``git`` es invocable."""
    if not (repo_root / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "--version"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_head_sha(repo_root: Path) -> Optional[str]:
    """Devuelve el SHA HEAD completo o ``None`` si falla."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.decode("utf-8", errors="ignore").strip()
        return sha or None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_head_commit_timestamp(repo_root: Path) -> Optional[str]:
    """Devuelve el timestamp ISO-8601 del commit HEAD o ``None``."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return None
        ts = result.stdout.decode("utf-8", errors="ignore").strip()
        return ts or None
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256_file(path: Path) -> Optional[str]:
    """SHA-256 hexadecimal del contenido binario del archivo, o ``None``."""
    if not path.exists() or not path.is_file():
        return None
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ── API pública ──────────────────────────────────────────────────────────────


def _default_repo_root() -> Path:
    """Default repo root: /app (parents[2] desde este archivo)."""
    return Path(__file__).resolve().parents[2]


def resolve_git_sha(
    environ: Optional[Dict[str, str]] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[str, str]:
    """
    Devuelve ``(git_sha, metadata_source)`` aplicando la cascada acordada.

    Garantiza que ``git_sha`` nunca sea ``None``: peor caso ``"unknown"`` con
    ``metadata_source == "unknown"``.

    Si ``repo_root`` es ``None``, usa la raíz del repo por defecto (``/app``).
    """
    if repo_root is None:
        repo_root = _default_repo_root()

    # 1) Variable canónica
    canonical = _read_env(_CANONICAL_SHA_VAR, environ)
    if canonical:
        return canonical, SOURCE_ENVIRONMENT

    # 2) Alternativas conocidas
    for var in _ALTERNATIVE_SHA_VARS:
        alt = _read_env(var, environ)
        if alt:
            return alt, SOURCE_ALTERNATIVE_ENV

    # 3) Git CLI (solo si .git disponible y binario funcional)
    if _git_available(repo_root):
        sha = _git_head_sha(repo_root)
        if sha:
            return sha, SOURCE_GIT

    # 4) Fallback explícito
    return UNKNOWN, SOURCE_UNKNOWN


def resolve_build_timestamp(
    environ: Optional[Dict[str, str]] = None,
    repo_root: Optional[Path] = None,
    git_source_already: bool = False,
) -> Tuple[str, str]:
    """
    Devuelve ``(build_timestamp, metadata_source)`` aplicando la cascada.

    Importante: **nunca** usar la hora de arranque del servidor como
    ``build_timestamp``, porque un simple reinicio del contenedor podría
    aparentar un despliegue nuevo.

    ``git_source_already`` es un hint informativo: cuando el SHA ya vino de
    git, preferimos también el timestamp de git si el env no lo provee, para
    mantener coherencia.

    Si ``repo_root`` es ``None``, usa la raíz del repo por defecto (``/app``).
    """
    if repo_root is None:
        repo_root = _default_repo_root()

    canonical = _read_env(_CANONICAL_BUILD_VAR, environ)
    if canonical:
        return canonical, SOURCE_ENVIRONMENT

    for var in _ALTERNATIVE_BUILD_VARS:
        alt = _read_env(var, environ)
        if alt:
            return alt, SOURCE_ALTERNATIVE_ENV

    if _git_available(repo_root):
        ts = _git_head_commit_timestamp(repo_root)
        if ts:
            return ts, SOURCE_GIT

    return UNKNOWN, SOURCE_UNKNOWN


def compute_module_hashes(
    repo_root: Path,
    modules: Iterable[str] = CRITICAL_MODULES,
) -> Dict[str, str]:
    """
    Devuelve ``{ ruta_relativa: sha256_hex or "missing" }``.

    Determinista: el mismo contenido produce el mismo hash.  No expone paths
    absolutos ni metadatos sensibles del sistema de archivos.
    """
    out: Dict[str, str] = {}
    for rel in modules:
        path = repo_root / rel
        digest = _sha256_file(path)
        out[rel] = digest if digest is not None else "missing"
    return out


def short_sha(sha: str, length: int = 7) -> str:
    """Devuelve la versión corta del SHA, segura para headers HTTP."""
    if not sha or sha == UNKNOWN:
        return UNKNOWN
    safe = "".join(c for c in sha if c.isalnum())
    return safe[:length] or UNKNOWN


def build_version_payload(
    repo_root: Optional[Path] = None,
    environ: Optional[Dict[str, str]] = None,
    modules: Iterable[str] = CRITICAL_MODULES,
) -> Dict[str, object]:
    """
    Construye el payload completo de ``/api/debug/version``.

    **Garantía fail-soft**: jamás lanza excepción.  Cualquier fallo interno
    se traduce en un valor ``unknown`` con ``metadata_source`` coherente.
    """
    if repo_root is None:
        # /app/backend/services/debug_metadata.py -> repo root = /app
        repo_root = Path(__file__).resolve().parents[2]

    try:
        git_sha, sha_source = resolve_git_sha(environ=environ, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
        log.warning("[debug_metadata] resolve_git_sha failed: %s", exc)
        git_sha, sha_source = UNKNOWN, SOURCE_UNKNOWN

    try:
        build_ts, ts_source = resolve_build_timestamp(
            environ=environ,
            repo_root=repo_root,
            git_source_already=(sha_source == SOURCE_GIT),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[debug_metadata] resolve_build_timestamp failed: %s", exc)
        build_ts, ts_source = UNKNOWN, SOURCE_UNKNOWN

    try:
        module_hashes = compute_module_hashes(repo_root, modules=modules)
    except Exception as exc:  # noqa: BLE001
        log.warning("[debug_metadata] compute_module_hashes failed: %s", exc)
        module_hashes = {}

    # `metadata_source` describe la cascada combinada (peor caso entre sha y ts)
    if sha_source == ts_source:
        metadata_source = sha_source
    elif SOURCE_UNKNOWN in (sha_source, ts_source):
        metadata_source = SOURCE_UNKNOWN
    else:
        # Mezcla: priorizamos el más explícito.
        rank = {
            SOURCE_ENVIRONMENT: 3,
            SOURCE_ALTERNATIVE_ENV: 2,
            SOURCE_GIT: 1,
            SOURCE_UNKNOWN: 0,
        }
        metadata_source = (
            sha_source if rank.get(sha_source, 0) >= rank.get(ts_source, 0) else ts_source
        )

    return {
        "service": SERVICE_NAME,
        "environment": _detect_environment(environ),
        "git_sha": git_sha,
        "git_sha_short": short_sha(git_sha),
        "build_timestamp": build_ts,
        "metadata_source": metadata_source,
        "metadata_source_detail": {
            "git_sha": sha_source,
            "build_timestamp": ts_source,
        },
        "python_version": platform.python_version(),
        "module_hashes": module_hashes,
        "audit_phase": "F99-P0-PRODUCTION-DRIFT-AUDIT",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


__all__: List[str] = [
    "build_version_payload",
    "resolve_git_sha",
    "resolve_build_timestamp",
    "compute_module_hashes",
    "short_sha",
    "CRITICAL_MODULES",
    "SOURCE_ENVIRONMENT",
    "SOURCE_ALTERNATIVE_ENV",
    "SOURCE_GIT",
    "SOURCE_UNKNOWN",
    "UNKNOWN",
    "SERVICE_NAME",
]
