"""TheStatsAPI HTTP client — fail-soft, rate-limited, async.

This is an ADDITIVE provider that complements API-Sports for football.
It is designed to fill the gap on **national-team / international**
fixtures that API-Sports occasionally fails to return.

Key design constraints (per user spec):
  1. **Fail-soft**: any timeout / 4xx / 5xx / missing key MUST never
     raise to the caller. The caller receives `[]` / `{}` and continues
     with the primary provider (API-Sports).
  2. **Disabled by default safety**: if `THESTATSAPI_KEY` is missing or
     `ENABLE_THE_STATS_API=false`, every call short-circuits to a no-op.
  3. **Rate-limit aware**: small in-process token bucket (default 60 req/min)
     so we never trip the provider's quota and degrade gracefully.
  4. **Tests use mocks**: this module never makes a real HTTP call during
     `pytest`; the `httpx.AsyncClient` is injected so tests pass a mock
     transport. CI must work even with `THESTATSAPI_KEY` unset.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ── Config (env, lazy) ───────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.thestatsapi.com/api"
DEFAULT_TIMEOUT_SEC = 8.0
DEFAULT_RETRIES = 1  # one retry on 429/5xx; never on 4xx
DEFAULT_BACKOFF_SEC = 0.6


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def get_api_key() -> str | None:
    """Read the API key fresh from env on every call so tests can monkeypatch."""
    key = (os.environ.get("THESTATSAPI_KEY") or "").strip()
    return key or None


def get_base_url() -> str:
    return (os.environ.get("THESTATSAPI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def is_enabled() -> bool:
    """Return True only if explicitly enabled AND a key is configured."""
    if not _env_flag("ENABLE_THE_STATS_API", default=False):
        return False
    return bool(get_api_key())


# ── In-process rate limiter (60 req/min default) ─────────────────────
class _RateLimiter:
    def __init__(self, max_calls: int = 60, period_sec: int = 60):
        self.max_calls = max_calls
        self.period = period_sec
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < self.period]
            if len(self._calls) >= self.max_calls:
                wait = self.period - (now - self._calls[0]) + 0.05
                if wait > 0:
                    log.info("[thestatsapi] rate limit reached, sleeping %.2fs", wait)
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls if now - t < self.period]
            self._calls.append(time.monotonic())


_LIMITER = _RateLimiter(
    max_calls=int(os.environ.get("THESTATSAPI_RATE_LIMIT", "60") or 60),
    period_sec=60,
)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    params: dict | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    retries: int = DEFAULT_RETRIES,
) -> dict[str, Any]:
    """Low-level request with retry/backoff. Always fail-soft.

    Returns ``{}`` on any error so callers can safely chain `.get(...)`.
    """
    if not is_enabled():
        log.debug("[thestatsapi] disabled (missing key or flag off)")
        return {}

    key = get_api_key()
    if not key:
        return {}

    base = get_base_url()
    url = f"{base}{path}" if path.startswith("/") else f"{base}/{path}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "betting-engine/1.0 (+thestatsapi)",
    }

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            await _LIMITER.acquire()
            resp = await client.request(method, url, params=params, headers=headers, timeout=timeout)
            status = resp.status_code
            if status == 200:
                try:
                    return resp.json() or {}
                except Exception as exc:
                    log.warning("[thestatsapi] JSON decode failed (%s): %s", path, exc)
                    return {}
            if status in (401, 403):
                # Auth error — log once and short-circuit future calls? we just fail-soft
                log.warning("[thestatsapi] auth error %s on %s — check THESTATSAPI_KEY", status, path)
                return {}
            if status == 404:
                log.debug("[thestatsapi] 404 %s", path)
                return {}
            if status == 429 or 500 <= status < 600:
                # retryable
                log.info("[thestatsapi] %s on %s (attempt %d/%d)", status, path, attempt + 1, retries + 1)
                if attempt < retries:
                    await asyncio.sleep(DEFAULT_BACKOFF_SEC * (attempt + 1))
                    continue
                return {}
            # Other 4xx
            log.warning("[thestatsapi] unexpected status %s on %s", status, path)
            return {}
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPError) as exc:
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(DEFAULT_BACKOFF_SEC * (attempt + 1))
                continue
        except Exception as exc:  # noqa: BLE001 — defensive: never propagate
            last_exc = exc
            break
    if last_exc is not None:
        log.warning("[thestatsapi] %s %s failed after retries: %s", method, path, last_exc)
    return {}


# ── Public surface (high-level endpoints) ────────────────────────────
# These methods accept an injected `client` so tests can pass a
# `httpx.AsyncClient(transport=httpx.MockTransport(...))`.

async def fetch_competitions(client: httpx.AsyncClient) -> list[dict]:
    """Return the full competitions list (cached upstream by the caller)."""
    data = await _request(client, "GET", "/football/competitions")
    if not isinstance(data, dict):
        return []
    # TheStatsAPI typically wraps results under `data` or `competitions`.
    return _extract_list(data, candidate_keys=("competitions", "data", "response", "results"))


async def fetch_live_matches(client: httpx.AsyncClient) -> list[dict]:
    """Return matches currently in-progress."""
    data = await _request(client, "GET", "/football/matches", params={"status": "live"})
    if not isinstance(data, dict):
        return []
    return _extract_list(data, candidate_keys=("matches", "data", "response", "results"))


async def fetch_fixtures(
    client: httpx.AsyncClient,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    competition_id: int | str | None = None,
) -> list[dict]:
    """Return upcoming/scheduled fixtures in a date window."""
    params: dict[str, Any] = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    if competition_id is not None:
        params["competition_id"] = competition_id
    data = await _request(client, "GET", "/football/matches", params=params or None)
    if not isinstance(data, dict):
        return []
    return _extract_list(data, candidate_keys=("matches", "data", "response", "results"))


async def fetch_match_stats(client: httpx.AsyncClient, match_id: int | str) -> dict:
    """Return live/final per-match statistics (xG, shots, possession, etc.)."""
    data = await _request(client, "GET", f"/football/matches/{match_id}/stats")
    if not isinstance(data, dict):
        return {}
    # Some payloads return `{"data": {...stats...}}`
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


async def health_check(client: httpx.AsyncClient) -> dict:
    """Lightweight ping for the `/api/debug/thestatsapi/health` endpoint.

    Returns a dict with `enabled`, `reachable`, `competitions_count`,
    and (on error) a `reason`. Never raises.
    """
    if not is_enabled():
        return {
            "enabled": False,
            "reachable": False,
            "reason": "disabled or missing THESTATSAPI_KEY",
        }
    try:
        comps = await fetch_competitions(client)
        return {
            "enabled": True,
            "reachable": True,
            "competitions_count": len(comps),
            "base_url": get_base_url(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "reachable": False,
            "reason": str(exc),
            "base_url": get_base_url(),
        }


# ── Helpers ──────────────────────────────────────────────────────────
def _extract_list(payload: dict, candidate_keys: tuple[str, ...]) -> list[dict]:
    """TheStatsAPI is consistent within each endpoint but uses different
    keys for the array wrapper. Try each candidate; fall back to first
    list-valued key, finally to empty list.
    """
    if not isinstance(payload, dict):
        return []
    for k in candidate_keys:
        v = payload.get(k)
        if isinstance(v, list):
            return v
    # Some endpoints return the bare list as the top-level value of `data`
    data = payload.get("data")
    if isinstance(data, list):
        return data
    # Last resort: scan all top-level list values
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []
