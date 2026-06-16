"""F94.2 — TheStatsAPI structured diagnostics.

This module exposes lightweight probes that interrogate TheStatsAPI's
fixtures + live endpoints and return a **structured** diagnostic block
suitable for the Discovery Debug Sheet and the Live Visibility Strip.

The existing :func:`services.external_sources.thestatsapi_client._request`
helper is intentionally fail-soft and returns ``{}`` on every error,
which makes it impossible to surface *why* an adapter returned empty.
This module re-implements just enough of the request envelope to also
capture::

    {
      "provider":            "thestatsapi",
      "status":              "OK|EMPTY|HTTP_ERROR|AUTH_ERROR|TIMEOUT|DISABLED|EXCEPTION",
      "endpoint":            "/football/matches?..."  (with normalised query),
      "http_status":         200 | 4xx | 5xx | None,
      "raw_count":           int,
      "reason":              short machine-readable code,
      "sample_payload_keys": list[str],   # top-level keys of the response
      "request_id":          str | None,  # x-request-id header if present
      "elapsed_ms":          int | None,
    }

The probes never raise. They are safe to call from any endpoint /
background task.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import thestatsapi_client as tc

log = logging.getLogger("services.thestatsapi_diagnostics")

DEFAULT_TIMEOUT_S = 8.0


def _build_endpoint_str(path: str, params: dict | None) -> str:
    if not params:
        return path
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    return f"{path}?{qs}" if qs else path


def _sample_keys(payload: Any) -> list[str]:
    """Return up to 20 top-level keys of a dict payload, or descriptive
    metadata for non-dict shapes."""
    if isinstance(payload, dict):
        return sorted(list(payload.keys()))[:20]
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return [f"<list[{len(payload)}] of dict; first.keys={sorted(payload[0].keys())[:10]}>"]
        return [f"<list[{len(payload)}] of {type(payload[0]).__name__ if payload else 'empty'}>"]
    return [f"<{type(payload).__name__}>"]


def _count_items(payload: Any) -> int:
    """Best-effort count of how many fixture-like rows the payload carries."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        # TheStatsAPI typically wraps under one of these keys.
        for k in ("matches", "data", "response", "results", "fixtures"):
            v = payload.get(k)
            if isinstance(v, list):
                return len(v)
    return 0


async def _probe_request(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Execute a single request and return a structured diagnostic.

    Never raises; reflects the actual HTTP outcome in ``status`` +
    ``http_status`` so the caller can decide what to surface.
    """
    diag: dict[str, Any] = {
        "provider":            "thestatsapi",
        "status":              "UNKNOWN",
        "endpoint":            _build_endpoint_str(path, params),
        "http_status":         None,
        "raw_count":           0,
        "reason":              "",
        "sample_payload_keys": [],
        "request_id":          None,
        "elapsed_ms":          None,
    }

    if not tc.is_enabled():
        diag.update(status="DISABLED",
                    reason="ENABLE_THE_STATS_API_OFF_OR_KEY_MISSING")
        return diag

    key = tc.get_api_key()
    if not key:
        diag.update(status="DISABLED", reason="THESTATSAPI_KEY_MISSING")
        return diag

    base = tc.get_base_url()
    url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept":        "application/json",
        "User-Agent":    "betting-engine/1.0 (+thestatsapi-diag)",
    }

    t0 = time.monotonic()
    try:
        resp = await client.request("GET", url, params=params,
                                     headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        diag.update(status="TIMEOUT", reason="REQUEST_TIMEOUT",
                    elapsed_ms=int((time.monotonic() - t0) * 1000))
        return diag
    except httpx.HTTPError as exc:
        diag.update(status="HTTP_ERROR", reason=f"HTTPX_{type(exc).__name__}",
                    elapsed_ms=int((time.monotonic() - t0) * 1000))
        return diag
    except Exception as exc:
        diag.update(status="EXCEPTION", reason=f"UNEXPECTED_{type(exc).__name__}",
                    elapsed_ms=int((time.monotonic() - t0) * 1000))
        return diag

    diag["elapsed_ms"]  = int((time.monotonic() - t0) * 1000)
    diag["http_status"] = resp.status_code
    diag["request_id"]  = resp.headers.get("x-request-id") or resp.headers.get("x-trace-id")

    if resp.status_code in (401, 403):
        diag.update(status="AUTH_ERROR", reason=f"HTTP_{resp.status_code}_AUTH")
        return diag
    if resp.status_code == 404:
        diag.update(status="HTTP_ERROR", reason="HTTP_404_NOT_FOUND")
        return diag
    if resp.status_code >= 400:
        diag.update(status="HTTP_ERROR", reason=f"HTTP_{resp.status_code}")
        return diag

    try:
        payload = resp.json()
    except Exception as exc:
        diag.update(status="EXCEPTION",
                    reason=f"JSON_DECODE_FAILED_{type(exc).__name__}")
        return diag

    diag["sample_payload_keys"] = _sample_keys(payload)
    diag["raw_count"]           = _count_items(payload)

    if diag["raw_count"] == 0:
        diag.update(status="EMPTY", reason="ADAPTER_RETURNED_EMPTY")
    else:
        diag.update(status="OK", reason="OK")
    return diag


async def probe_fixtures_endpoint(
    client: httpx.AsyncClient,
    *,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
) -> dict[str, Any]:
    """Probe ``GET /football/matches?date_from=...&date_to=...``.

    Mirrors the parameters used by
    :func:`thestatsapi_fixtures_adapter.fetch_fixtures_next_48h` so the
    diagnostic faithfully reflects what discovery sees in production.
    """
    today = datetime.now(timezone.utc).date()
    df = date_from or today.isoformat()
    dt = date_to   or (today + timedelta(days=2)).isoformat()
    params = {"date_from": df, "date_to": dt}
    return await _probe_request(client, "/football/matches", params=params)


async def probe_live_endpoint(client: httpx.AsyncClient) -> dict[str, Any]:
    """Probe ``GET /football/matches?status=live``."""
    return await _probe_request(client, "/football/matches",
                                 params={"status": "live"})


async def probe_all(client: httpx.AsyncClient) -> dict[str, Any]:
    """Run both probes (fixtures + live) and return both diagnostics.

    The shape is ``{"fixtures": diag, "live": diag, "base_url": str,
    "enabled": bool}``. Useful for a one-shot ``/api/debug/thestatsapi/probe``
    endpoint.
    """
    fixtures = await probe_fixtures_endpoint(client)
    live     = await probe_live_endpoint(client)
    return {
        "fixtures":  fixtures,
        "live":      live,
        "base_url":  tc.get_base_url(),
        "enabled":   tc.is_enabled(),
        "key_set":   bool(tc.get_api_key()),
    }


__all__ = [
    "probe_fixtures_endpoint",
    "probe_live_endpoint",
    "probe_all",
    "DEFAULT_TIMEOUT_S",
]
