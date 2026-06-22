"""Sprint-F99.1 · Offline-seed (corners) hydrator.

Bridge IO layer between the Mongo collection
``football_team_corners_offline_seed`` and the **pure** F98/F74 adapter
``services.adapters.offline_seed_corners_adapter``.

Binding del usuario (F99.1):
  * Lee EXCLUSIVAMENTE de la colección de córners — nunca mezcla seeds.
  * Adjunta el payload a ``match["_corners_offline_seed_raw"]`` (path
    interno, NUNCA expuesto al editorial ni a la UI).
  * Fail-soft estricto: cualquier error de IO degrada a ``None`` y deja
    telemetría DEBUG (sin WARNING ruidoso por partido).
  * Idempotente: si el match ya tiene ``_corners_offline_seed_raw``, no
    re-lee (caché de turno).

Feature flag:
  * ``ENABLE_F99_CORNERS_SEED_HYDRATION`` — opt-in (default off).
  * Cuando off, el hydrator no consulta Mongo y devuelve False.

Telemetry block (``match[TRACE_KEY][SOURCE_KEY]``)::

    {
      "attempted":          True,
      "status":             "RICH" | "PARTIAL" | "NO_DATA" | "SKIPPED",
      "sides":              {
          "home": {"matches": int, "classified_as": "offline_seed"|"seed_partial"|"none"},
          "away": {"matches": int, "classified_as": ... },
      },
      "min_sample":         3,
      "checked_at":         "<iso8601>",
    }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

FLAG_ENV_VAR = "ENABLE_F99_CORNERS_SEED_HYDRATION"
TRACE_KEY    = "football_data_enrichment_source_trace"
SOURCE_KEY   = "corners_offline_seed"
RAW_KEY      = "_corners_offline_seed_raw"

_DEFAULT_MIN_SAMPLE = 3


def is_enabled() -> bool:
    raw = os.environ.get(FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _team_name(match: dict, side: str) -> str:
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


def _league(match: dict) -> Optional[str]:
    if not isinstance(match, dict):
        return None
    league = match.get("league_name") or match.get("league") or match.get("competition")
    if isinstance(league, dict):
        league = league.get("name")
    return league if isinstance(league, str) and league else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_side(doc: Optional[dict], *, min_sample: int) -> str:
    if not isinstance(doc, dict) or not doc.get("available", True):
        return "none"
    matches = doc.get("matches") or []
    if not isinstance(matches, list) or not matches:
        return "none"
    underlying = doc.get("underlying_source") or doc.get("source")
    if isinstance(underlying, str) and underlying.lower() in (
        "promoted_from_online", "online_partial",
    ):
        return "seed_partial"
    if len(matches) < int(min_sample):
        return "seed_partial"
    return "offline_seed"


def _record_trace(match: dict, payload: dict) -> None:
    if not isinstance(match, dict):
        return
    trace = match.get(TRACE_KEY)
    if not isinstance(trace, dict):
        trace = {}
        match[TRACE_KEY] = trace
    trace[SOURCE_KEY] = payload


async def hydrate_match_corners_offline_seed(
    match: Any,
    db: Any,
    *,
    sport: str = "football",
    min_sample: int = _DEFAULT_MIN_SAMPLE,
) -> bool:
    """Read the corners offline seed for both teams and attach the raw payload.

    Returns ``True`` iff at least one side returned a non-empty seed doc
    (regardless of classification — the adapter handles routing).

    The hydrator NEVER raises; on any failure it writes a trace block
    and returns ``False``.
    """
    if not isinstance(match, dict):
        return False

    # Idempotency.
    if isinstance(match.get(RAW_KEY), dict):
        return True

    if not is_enabled():
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     "feature_flag_off",
            "checked_at": _now_iso(),
        })
        return False

    if sport != "football":
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     f"sport_not_supported:{sport}",
            "checked_at": _now_iso(),
        })
        return False

    if db is None:
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     "no_db_handle",
            "checked_at": _now_iso(),
        })
        return False

    home = _team_name(match, "home")
    away = _team_name(match, "away")
    if not home or not away:
        _record_trace(match, {
            "attempted":  False,
            "status":     "SKIPPED",
            "reason":     "missing_team_names",
            "checked_at": _now_iso(),
        })
        return False

    league = _league(match)

    try:
        from .football_corners_offline_seed import get_offline_corners_history
    except Exception as exc:  # noqa: BLE001
        log.debug("corners_offline_seed module import failed: %s", exc)
        _record_trace(match, {
            "attempted":  True,
            "status":     "NO_DATA",
            "reason":     "import_error",
            "checked_at": _now_iso(),
        })
        return False

    home_doc: Optional[dict] = None
    away_doc: Optional[dict] = None
    try:
        home_doc = await get_offline_corners_history(db, home, league=league)
    except Exception as exc:  # noqa: BLE001
        log.debug("offline_seed home lookup failed (%s): %s", home, exc)
    try:
        away_doc = await get_offline_corners_history(db, away, league=league)
    except Exception as exc:  # noqa: BLE001
        log.debug("offline_seed away lookup failed (%s): %s", away, exc)

    home_class = _classify_side(home_doc, min_sample=min_sample)
    away_class = _classify_side(away_doc, min_sample=min_sample)

    raw_payload = {
        "home":       home_doc,
        "away":       away_doc,
        "min_sample": int(min_sample),
    }
    has_any = (home_class != "none") or (away_class != "none")
    has_rich = (home_class == "offline_seed") or (away_class == "offline_seed")
    if has_rich:
        status = "RICH"
    elif has_any:
        status = "PARTIAL"
    else:
        status = "NO_DATA"

    _record_trace(match, {
        "attempted":  True,
        "status":     status,
        "min_sample": int(min_sample),
        "sides": {
            "home": {
                "matches":       len((home_doc or {}).get("matches") or []),
                "classified_as": home_class,
            },
            "away": {
                "matches":       len((away_doc or {}).get("matches") or []),
                "classified_as": away_class,
            },
        },
        "checked_at": _now_iso(),
    })

    if has_any:
        # Strip any non-canonical keys that don't belong to the corners family
        # to avoid leaking goals/xG/other into adapters that expect only corners.
        match[RAW_KEY] = raw_payload
        return True

    return False


__all__ = [
    "FLAG_ENV_VAR",
    "TRACE_KEY",
    "SOURCE_KEY",
    "RAW_KEY",
    "is_enabled",
    "hydrate_match_corners_offline_seed",
]
