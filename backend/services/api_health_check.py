"""API health check — verifies every data provider used by the
ingestion pipeline so the user can see EXACTLY which provider is
broken when the pre-match dashboard shows zeroes.

Each probe returns the canonical shape::

    {
      "provider":          "API_NAME",
      "request_sent":      bool,
      "response_received": bool,
      "http_status":       int | None,
      "fixtures_returned": int | None,
      "response_time_ms":  int,
      "error":             str | None,
      "status":            "OK" | "DEGRADED" | "DOWN" | "DISABLED" | "SKIPPED"
    }

The aggregator :func:`check_all_providers` runs every probe concurrently
with a per-provider timeout so a single slow provider does not stall the
endpoint. NEVER raises — failures are surfaced as ``status: DOWN``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx

log = logging.getLogger("services.api_health_check")

# ─────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────
DEFAULT_PROBE_TIMEOUT_S = 8.0


# ─────────────────────────────────────────────────────────────────────
# Provider probes
# ─────────────────────────────────────────────────────────────────────
async def _probe_api_sports(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Ping API-Sports status endpoint + count today's football fixtures."""
    key = os.environ.get("API_FOOTBALL_KEY") or os.environ.get("API_SPORTS_KEY")
    if not key:
        return {
            "provider":          "api_sports",
            "request_sent":      False,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  0,
            "error":             "API_FOOTBALL_KEY missing in environment",
            "status":            "DISABLED",
        }

    url = "https://v3.football.api-sports.io/fixtures"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-key": key, "x-rapidapi-host": "v3.football.api-sports.io"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url, params={"date": today}, headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return {
                "provider":          "api_sports",
                "request_sent":      True,
                "response_received": True,
                "http_status":       r.status_code,
                "fixtures_returned": None,
                "response_time_ms":  elapsed_ms,
                "error":             f"HTTP {r.status_code}",
                "status":            "DOWN",
            }
        body = r.json()
        fixtures = body.get("response") or []
        return {
            "provider":          "api_sports",
            "request_sent":      True,
            "response_received": True,
            "http_status":       200,
            "fixtures_returned": len(fixtures),
            "response_time_ms":  elapsed_ms,
            "error":             None,
            "status":            "OK" if fixtures else "DEGRADED",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":          "api_sports",
            "request_sent":      True,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  int((time.perf_counter() - started) * 1000),
            "error":             repr(exc),
            "status":            "DOWN",
        }


async def _probe_thestatsapi(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    """Ping TheStatsAPI fixtures endpoint."""
    key = os.environ.get("THESTATSAPI_KEY") or os.environ.get("THE_STATS_API_KEY")
    if not key:
        return {
            "provider":          "thestatsapi",
            "request_sent":      False,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  0,
            "error":             "THESTATSAPI_KEY missing in environment",
            "status":            "DISABLED",
        }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = "https://api.thestatsapi.io/v1/football/fixtures"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(
                url,
                params={"date": today},
                headers={"Authorization": f"Bearer {key}"},
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if r.status_code not in (200, 201):
            return {
                "provider":          "thestatsapi",
                "request_sent":      True,
                "response_received": True,
                "http_status":       r.status_code,
                "fixtures_returned": None,
                "response_time_ms":  elapsed_ms,
                "error":             f"HTTP {r.status_code}",
                "status":            "DOWN",
            }
        body = r.json()
        # TheStatsAPI uses either ``data`` or ``fixtures`` depending on tier.
        items = body.get("data") or body.get("fixtures") or body.get("response") or []
        count = len(items) if isinstance(items, list) else 0
        return {
            "provider":          "thestatsapi",
            "request_sent":      True,
            "response_received": True,
            "http_status":       r.status_code,
            "fixtures_returned": count,
            "response_time_ms":  elapsed_ms,
            "error":             None,
            "status":            "OK" if count else "DEGRADED",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":          "thestatsapi",
            "request_sent":      True,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  int((time.perf_counter() - started) * 1000),
            "error":             repr(exc),
            "status":            "DOWN",
        }


async def _probe_scrapedo_provider(name: str, sample_url: str,
                                    *, timeout_s: float = DEFAULT_PROBE_TIMEOUT_S
                                    ) -> dict:
    """Generic probe for any scrape.do-backed provider (sportytrader,
    totalcorner, footystats, forebet, fbref, 365scores).

    The probe simply verifies that scrape.do has a valid token and that
    the breaker is closed — it intentionally avoids a real HTTP request
    so we don't burn quota on every health check.
    """
    try:
        from .scrape_do_client import (
            breaker_status, is_enabled,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":          name,
            "request_sent":      False,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  0,
            "error":             f"scrape_do_client import failed: {exc!r}",
            "status":            "DOWN",
        }

    enabled = is_enabled()
    breaker = breaker_status() if enabled else {}
    if not enabled:
        return {
            "provider":          name,
            "request_sent":      False,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  0,
            "error":             "SCRAPEDO_TOKEN missing — scrape.do disabled",
            "status":            "DISABLED",
        }
    breaker_open = breaker.get("open") is True if isinstance(breaker, dict) else False
    return {
        "provider":          name,
        "request_sent":      False,
        "response_received": True,
        "http_status":       None,
        "fixtures_returned": None,
        "response_time_ms":  0,
        "error":             "scrape.do breaker is OPEN" if breaker_open else None,
        "status":            "DOWN" if breaker_open else "OK",
        "breaker":           breaker,
        "sample_url":        sample_url,
    }


async def _probe_sportytrader(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    return await _probe_scrapedo_provider(
        "sportytrader",
        sample_url="https://www.sportytrader.com/en/odds/football/",
        timeout_s=timeout_s,
    )


async def _probe_totalcorner(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    return await _probe_scrapedo_provider(
        "totalcorner",
        sample_url="https://www.totalcorner.com/",
        timeout_s=timeout_s,
    )


async def _probe_footystats(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> dict:
    return await _probe_scrapedo_provider(
        "footystats",
        sample_url="https://footystats.org/",
        timeout_s=timeout_s,
    )


# Map of provider id → probe coroutine. Exposed so tests / callers can
# selectively pick which providers to check.
DEFAULT_PROBES: dict[str, Callable[..., Awaitable[dict]]] = {
    "api_sports":   _probe_api_sports,
    "thestatsapi":  _probe_thestatsapi,
    "sportytrader": _probe_sportytrader,
    "totalcorner":  _probe_totalcorner,
    "footystats":   _probe_footystats,
}


# ─────────────────────────────────────────────────────────────────────
# Aggregator
# ─────────────────────────────────────────────────────────────────────
async def _run_with_timeout(
    name: str,
    coro: Awaitable[dict],
    *,
    timeout_s: float,
) -> dict:
    """Run a single probe with a hard timeout. Fail-soft: returns a
    structured DOWN dict instead of raising."""
    started = time.perf_counter()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        return {
            "provider":          name,
            "request_sent":      True,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  int((time.perf_counter() - started) * 1000),
            "error":             f"Probe timed out after {timeout_s:.0f}s",
            "status":            "DOWN",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider":          name,
            "request_sent":      True,
            "response_received": False,
            "http_status":       None,
            "fixtures_returned": None,
            "response_time_ms":  int((time.perf_counter() - started) * 1000),
            "error":             repr(exc),
            "status":            "DOWN",
        }


async def check_all_providers(
    *,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    only: Optional[list[str]] = None,
    probes: Optional[dict[str, Callable[..., Awaitable[dict]]]] = None,
) -> dict:
    """Run every provider probe concurrently and return the canonical
    health snapshot::

        {
          "checked_at":    "<iso>",
          "timeout_s":     8.0,
          "api_health":    {provider: {...}, ...},
          "summary": {"ok": 3, "degraded": 1, "down": 1, "disabled": 0,
                      "total": 5}
        }
    """
    probe_map = probes or DEFAULT_PROBES
    if only:
        probe_map = {k: v for k, v in probe_map.items() if k in only}

    started_at = datetime.now(timezone.utc).isoformat()

    coros = [
        _run_with_timeout(name, fn(timeout_s=timeout_s), timeout_s=timeout_s + 0.5)
        for name, fn in probe_map.items()
    ]
    results = await asyncio.gather(*coros, return_exceptions=False)

    api_health: dict[str, dict] = {}
    summary: dict[str, int] = {"ok": 0, "degraded": 0, "down": 0,
                                "disabled": 0, "skipped": 0, "total": 0}
    for r in results:
        prov = r.get("provider", "?")
        api_health[prov] = r
        summary["total"] += 1
        st = (r.get("status") or "").upper()
        if st == "OK":
            summary["ok"] += 1
        elif st == "DEGRADED":
            summary["degraded"] += 1
        elif st == "DOWN":
            summary["down"] += 1
        elif st == "DISABLED":
            summary["disabled"] += 1
        else:
            summary["skipped"] += 1

    return {
        "checked_at":  started_at,
        "timeout_s":   timeout_s,
        "api_health":  api_health,
        "summary":     summary,
    }


__all__ = [
    "DEFAULT_PROBE_TIMEOUT_S",
    "DEFAULT_PROBES",
    "check_all_providers",
    # Individual probes (exposed so the orchestrator can run a single
    # provider check inline without paying the full health-check cost).
    "_probe_api_sports",
    "_probe_thestatsapi",
    "_probe_sportytrader",
    "_probe_totalcorner",
    "_probe_footystats",
]
