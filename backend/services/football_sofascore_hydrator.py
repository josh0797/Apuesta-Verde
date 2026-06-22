"""Sprint-F99 · SofaScore → F74 hydrator (opt-in, fail-soft, telemetría estructurada).

Este módulo es el **único punto** del pipeline de fútbol que invoca el scraper
SofaScore (``services.external_sources.sofascore``) para producir el wrapper
raw que consume el adapter F98 ``adapt_sofascore_to_f74``.

Reglas binding (F99 / decisiones del usuario):

  1. **No paralelizar/reconstruir** el adapter ni el builder/cascade. Solo
     adjuntamos ``match["_sofascore_raw"]`` y un bloque de telemetría en
     ``match["football_data_enrichment_source_trace"]["sofascore"]`` para
     que el builder/cascade lo consuman tal cual.

  2. **Payload crudo nunca llega al editorial**: el wrapper aquí construido
     es por sí mismo una versión normalizada (el scraper ya descarta HTML/JSON
     completos y devuelve sólo dicts shape-friendly).

  3. **Fail-soft con telemetría sin logs ruidosos**:
       * Errores esperados (BLOCKED/timeout/schema drift) → DEBUG.
       * Estados sistémicos (circuit-breaker, varios fallos consecutivos) →
         WARNING (decisión del caller / próxima fase).
       * source_trace **no almacena** HTML, payloads completos ni mensajes
         con datos sensibles.

  4. **Opt-in por feature flag**: ``ENABLE_F99_SOFASCORE_HYDRATION`` (env).
     Por defecto **deshabilitado** en este turno para preservar la baseline
     de 4804 tests pasando y permitir activación gradual desde producción.

API pública:

  * :func:`is_enabled()` → bool
  * :func:`hydrate_match_sofascore(match, *, sport="football", **kwargs)` →
    ``bool`` (True si se adjuntó ``_sofascore_raw`` con datos usables).

Convención del bloque de telemetría escrito en el match::

    match["football_data_enrichment_source_trace"] = {
        "sofascore": {
            "attempted":         True,
            "status":            "USABLE" | "PARTIAL" | "NO_DATA" | "BLOCKED" | "SKIPPED",
            "valid_fields":      ["home_form", "h2h", ...],
            "missing_fields":    ["odds", ...],
            "fallback_triggered": <bool>,
            "checked_at":        "<iso8601>",
        }
    }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

FLAG_ENV_VAR = "ENABLE_F99_SOFASCORE_HYDRATION"
TRACE_KEY    = "football_data_enrichment_source_trace"
SOURCE_KEY   = "sofascore"


def is_enabled() -> bool:
    """Return whether the SofaScore hydrator is allowed to run.

    Strict opt-in (False unless ``ENABLE_F99_SOFASCORE_HYDRATION=true``).
    """
    raw = os.environ.get(FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _team_name(match: dict, side: str) -> str:
    """Extract the home/away team name, tolerating dict + flat shapes."""
    if not isinstance(match, dict):
        return ""
    block = match.get(f"{side}_team")
    if isinstance(block, dict):
        n = block.get("name")
        if n:
            return str(n)
    if isinstance(block, str):
        return block
    flat = match.get(f"{side}_team_name")
    if flat:
        return str(flat)
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_wrapper(wrapper: Optional[dict]) -> tuple[str, list[str], list[str]]:
    """Classify the wrapper into (status, valid_fields, missing_fields).

    Status semantics (descriptive only — does NOT block per-field cascade):

      * ``NO_DATA``  — wrapper is None / empty / has neither form nor h2h nor odds.
      * ``PARTIAL``  — at least one of home_form / away_form / h2h / odds.
      * ``USABLE``   — both home_form and away_form populated.
      * ``RICH``     — USABLE + h2h + odds (or stats enriched).
    """
    if not isinstance(wrapper, dict) or not wrapper:
        return "NO_DATA", [], ["home_form", "away_form", "h2h", "odds"]

    valid: list[str] = []
    missing: list[str] = []
    for key in ("home_form", "away_form", "h2h", "odds"):
        v = wrapper.get(key)
        if isinstance(v, (list, dict)) and len(v) > 0:
            valid.append(key)
        else:
            missing.append(key)

    has_form = ("home_form" in valid) and ("away_form" in valid)
    has_h2h_or_odds = ("h2h" in valid) or ("odds" in valid)
    stats_enriched = bool((wrapper.get("_trace") or {}).get("stats_enriched"))

    if has_form and has_h2h_or_odds and stats_enriched:
        status = "RICH"
    elif has_form:
        status = "USABLE"
    elif valid:
        status = "PARTIAL"
    else:
        status = "NO_DATA"
    return status, valid, missing


def _record_trace(match: dict, payload: dict) -> None:
    """Idempotent merge of a per-source trace into the match doc."""
    if not isinstance(match, dict):
        return
    trace = match.get(TRACE_KEY)
    if not isinstance(trace, dict):
        trace = {}
        match[TRACE_KEY] = trace
    trace[SOURCE_KEY] = payload


async def hydrate_match_sofascore(
    match: Any,
    *,
    sport: str = "football",
    recent_n: int = 5,
    h2h_n: int = 5,
    enrich_stats: bool = False,
    total_timeout_s: float = 25.0,
) -> bool:
    """Try to attach a SofaScore wrapper raw to ``match["_sofascore_raw"]``.

    Always fail-soft: returns ``False`` on any error / missing data; writes a
    structured trace into ``match[TRACE_KEY]["sofascore"]`` even on failure
    so downstream observability stays consistent.

    Parameters
    ----------
    match:
        Live match document (in-memory).  Hydration is in-place.
    sport:
        Only ``"football"`` triggers wiring in F99.  Other sports are SKIPPED
        (telemetry still recorded).
    recent_n, h2h_n, enrich_stats, total_timeout_s:
        Forwarded to ``fetch_sofascore_match_context``.

    Returns
    -------
    bool
        ``True`` iff a non-empty wrapper was attached.
    """
    if not isinstance(match, dict):
        return False

    # Feature flag gate.
    if not is_enabled():
        _record_trace(match, {
            "attempted":         False,
            "status":            "SKIPPED",
            "valid_fields":      [],
            "missing_fields":    [],
            "fallback_triggered": False,
            "reason":            "feature_flag_off",
            "checked_at":        _now_iso(),
        })
        return False

    # Scope restriction: F99 wires SofaScore only on football.
    if sport != "football":
        _record_trace(match, {
            "attempted":         False,
            "status":            "SKIPPED",
            "valid_fields":      [],
            "missing_fields":    [],
            "fallback_triggered": False,
            "reason":            f"sport_not_supported:{sport}",
            "checked_at":        _now_iso(),
        })
        return False

    home = _team_name(match, "home")
    away = _team_name(match, "away")
    if not home or not away:
        _record_trace(match, {
            "attempted":         False,
            "status":            "SKIPPED",
            "valid_fields":      [],
            "missing_fields":    [],
            "fallback_triggered": False,
            "reason":            "missing_team_names",
            "checked_at":        _now_iso(),
        })
        return False

    # Lazy import — keeps the hydrator import-light for tests that mock the source.
    try:
        from services.external_sources.sofascore import fetch_sofascore_match_context
    except Exception as exc:  # noqa: BLE001
        log.debug("sofascore module import failed: %s", exc)
        _record_trace(match, {
            "attempted":         True,
            "status":            "NO_DATA",
            "valid_fields":      [],
            "missing_fields":    ["home_form", "away_form", "h2h", "odds"],
            "fallback_triggered": True,
            "reason":            "import_error",
            "checked_at":        _now_iso(),
        })
        return False

    try:
        wrapper = await fetch_sofascore_match_context(
            home, away,
            sport=sport,
            recent_n=recent_n,
            h2h_n=h2h_n,
            enrich_stats=enrich_stats,
            total_timeout_s=total_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        # The fetcher is itself fail-soft, but guard against unexpected raises.
        log.debug("fetch_sofascore_match_context raised unexpectedly: %s", exc)
        wrapper = None

    status, valid, missing = _classify_wrapper(wrapper)
    fallback_triggered = (status in ("NO_DATA", "BLOCKED"))

    # Trace is always recorded.
    _record_trace(match, {
        "attempted":         True,
        "status":            status if wrapper is not None else "BLOCKED",
        "valid_fields":      valid,
        "missing_fields":    missing,
        "fallback_triggered": fallback_triggered,
        "checked_at":        _now_iso(),
    })

    # Attach the wrapper only when there is something useable for the adapter.
    if wrapper and any(wrapper.get(k) for k in ("home_form", "away_form", "h2h", "odds")):
        match["_sofascore_raw"] = wrapper
        return True

    return False


__all__ = [
    "FLAG_ENV_VAR",
    "TRACE_KEY",
    "SOURCE_KEY",
    "is_enabled",
    "hydrate_match_sofascore",
]
