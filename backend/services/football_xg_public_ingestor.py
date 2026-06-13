"""Phase F85.3 — Public xG enrichment orchestrator (FBref + Forebet).

Resolves both team URLs via :mod:`fbref_client.resolve_fbref_team_url`,
pulls recent match logs for each side, runs them through the normaliser
to produce L1/L5/L15 averages, optionally enriches with the Forebet
match-detail context, derives signals and returns a single payload to
persist under ``match_doc.xg_public_enrichment``.

Fail-soft — every external miss returns a dict with ``available=False``
plus reason codes; partial successes are allowed (e.g. FBref OK +
Forebet down).

Neither the run-now nor background flow is allowed to block the main
picks generator: callers MUST schedule this from the explicit
enrichment endpoints (F85.4) and never from the inline pipeline,
unless ``ENABLE_INLINE_PUBLIC_XG_SCRAPING=true``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .external_sources import fbref_client as fb
from .external_sources import forebet_client as fcli
from . import football_xg_public_normalizer as nrm
from . import football_xg_public_signals as sig

log = logging.getLogger("football_xg_public_ingestor")

RC_TIMEOUT          = "PUBLIC_XG_SCRAPER_TIMEOUT"
RC_BUILD_FAILED     = "PUBLIC_XG_BUILD_FAILED"
RC_NO_TEAMS         = "PUBLIC_XG_NO_TEAMS"
RC_FBREF_AVAILABLE  = "FBREF_XG_RECENT_AVERAGES_AVAILABLE"
RC_FOREBET_AVAILABLE = "FOREBET_CONTEXT_AVAILABLE"

DEFAULT_RUN_TIMEOUT = float(os.environ.get("PUBLIC_XG_SCRAPER_TIMEOUT_SECONDS", "8"))


def _enable_inline_scraping() -> bool:
    raw = (os.environ.get("ENABLE_INLINE_PUBLIC_XG_SCRAPING") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _enable_background_scraping() -> bool:
    raw = (os.environ.get("ENABLE_BACKGROUND_PUBLIC_XG_SCRAPING") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _extract_team_name(side: dict | None) -> Optional[str]:
    if not isinstance(side, dict):
        return None
    return (side.get("name")
            or side.get("team")
            or side.get("team_name")
            or None)


def _classify_quality(snapshot: dict, forebet: dict | None) -> str:
    """Return one of USABLE / PARTIAL / UNAVAILABLE."""
    has_xg = bool(snapshot and snapshot.get("available"))
    has_fb = bool(forebet and forebet.get("available"))
    if not has_xg and not has_fb:
        return "UNAVAILABLE"
    if has_xg and not snapshot.get("partial") and has_fb:
        return "USABLE"
    if has_xg and snapshot.get("partial"):
        return "PARTIAL"
    if has_xg or has_fb:
        return "PARTIAL"
    return "UNAVAILABLE"


async def _do_fetch(
    match_doc: dict,
    *,
    forebet_url: Optional[str],
    client: httpx.AsyncClient,
    db,
) -> dict:
    home = match_doc.get("home_team") or {}
    away = match_doc.get("away_team") or {}
    home_name = _extract_team_name(home)
    away_name = _extract_team_name(away)

    if not home_name and not away_name:
        return {
            "available":   False,
            "reason_codes": [RC_NO_TEAMS],
            "data_quality": "UNAVAILABLE",
            "source_priority": ["fbref", "forebet"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # 1. Resolve FBref URLs in parallel.
    home_url_task = fb.resolve_fbref_team_url(client, home_name, db=db) if home_name else None
    away_url_task = fb.resolve_fbref_team_url(client, away_name, db=db) if away_name else None
    home_url_res, away_url_res = await asyncio.gather(
        home_url_task or asyncio.sleep(0, result={}),
        away_url_task or asyncio.sleep(0, result={}),
    )

    home_url = (home_url_res or {}).get("url")
    away_url = (away_url_res or {}).get("url")

    # 2. Pull match logs in parallel.
    async def _logs(url: Optional[str]) -> dict:
        if not url:
            return {"available": False, "reason_codes": [fb.RC_TEAM_URL_MISSING]}
        return await fb.fetch_fbref_team_match_logs(client, url, limit=15)

    home_logs_res, away_logs_res = await asyncio.gather(_logs(home_url), _logs(away_url))

    home_logs = (home_logs_res or {}).get("logs") or []
    away_logs = (away_logs_res or {}).get("logs") or []

    # 3. Normalise to canonical xG_recent_averages shape.
    snapshot = nrm.compute_fbref_xg_recent_averages(
        home_logs, away_logs,
        home_team_name=home_name, away_team_name=away_name,
    )

    # 4. Optional Forebet context.
    forebet_ctx: dict | None = None
    if forebet_url:
        try:
            forebet_ctx = await fcli.fetch_forebet_match_context(client, forebet_url)
        except Exception as exc:  # noqa: BLE001
            log.debug("[public_xg] forebet fetch error: %s", exc)
            forebet_ctx = {"available": False, "reason_codes": [fcli.RC_UNAVAILABLE]}

    # 5. Derive signals.
    signals = sig.derive_public_xg_signals(snapshot, forebet_ctx)

    # 6. Reason-code aggregation.
    reason_codes: list[str] = []
    if snapshot.get("available"):
        reason_codes.append(RC_FBREF_AVAILABLE)
    reason_codes.extend(snapshot.get("reason_codes") or [])
    if forebet_ctx and forebet_ctx.get("available"):
        reason_codes.append(RC_FOREBET_AVAILABLE)
    elif forebet_ctx:
        reason_codes.extend(forebet_ctx.get("reason_codes") or [])

    payload = {
        "available":       bool(snapshot.get("available") or (forebet_ctx and forebet_ctx.get("available"))),
        "source_priority": ["thestatsapi", "fbref", "forebet"],
        "xg_recent_averages": snapshot,
        "forebet_context":    forebet_ctx,
        "signals":         signals.get("signals", []),
        "explanations":    signals.get("explanations", {}),
        "metrics":         signals.get("metrics", {}),
        "data_quality":    _classify_quality(snapshot, forebet_ctx),
        "reason_codes":    sorted(set(reason_codes)),
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
    }
    return payload


async def enrich_public_xg_context(
    client: Optional[httpx.AsyncClient],
    db,
    match_doc: dict,
    *,
    forebet_url: Optional[str] = None,
    timeout_s: float = DEFAULT_RUN_TIMEOUT,
) -> dict:
    """Top-level orchestrator. Returns the full enrichment payload or a
    TIMEOUT-shaped dict when the work cannot finish under
    ``timeout_s``."""
    if not isinstance(match_doc, dict):
        return {
            "available": False, "data_quality": "UNAVAILABLE",
            "reason_codes": [RC_BUILD_FAILED],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    owned_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
        owned_client = True

    try:
        return await asyncio.wait_for(
            _do_fetch(match_doc, forebet_url=forebet_url, client=client, db=db),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        log.warning("[public_xg] match=%s status=TIMEOUT timeout=%s",
                    match_doc.get("match_id"), timeout_s)
        return {
            "available": False,
            "status":    "TIMEOUT",
            "data_quality": "UNAVAILABLE",
            "reason_codes": [RC_TIMEOUT],
            "message": ("El enriquecimiento externo tardó demasiado. "
                         "El análisis principal no fue afectado."),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("[public_xg] build failed: %s", exc)
        return {
            "available": False,
            "data_quality": "UNAVAILABLE",
            "reason_codes": [RC_BUILD_FAILED],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if owned_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "enrich_public_xg_context",
    "_enable_inline_scraping", "_enable_background_scraping",
    "RC_TIMEOUT", "RC_BUILD_FAILED", "RC_NO_TEAMS",
    "RC_FBREF_AVAILABLE", "RC_FOREBET_AVAILABLE",
    "DEFAULT_RUN_TIMEOUT",
]
