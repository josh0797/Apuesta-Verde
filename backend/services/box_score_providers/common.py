"""Shared primitives for box-score providers (rate limit, retry, helpers)."""
from __future__ import annotations

import asyncio
import logging
import os
from http import HTTPStatus
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

log = logging.getLogger("box_score_providers")

# ───── Constants / provider names ─────────────────────────────────
PROVIDER_API_SPORTS    = "api_sports"
PROVIDER_BALLDONTLIE   = "balldontlie"
PROVIDER_MLB_STATSAPI  = "mlb_statsapi"

DEFAULT_TIMEOUT_S = 10.0
MAX_RETRIES = 3

# API-Sports shares the SAME key across all sports.
API_SPORTS_KEY = (
    os.environ.get("API_SPORTS_KEY")
    or os.environ.get("API_FOOTBALL_KEY")   # legacy var name in this repo
    or ""
)
API_SPORTS_BASKETBALL_BASE = "https://v1.basketball.api-sports.io"
API_SPORTS_BASEBALL_BASE   = "https://v1.baseball.api-sports.io"
API_SPORTS_HEADERS = {
    "x-apisports-key": API_SPORTS_KEY,
    "accept": "application/json",
}

BALLDONTLIE_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")
BALLDONTLIE_BASE = "https://api.balldontlie.io"

MLB_STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"


class BoxScoreProviderError(Exception):
    """Soft signal that a provider call failed in a recoverable way.

    Callers MUST NOT propagate this — the public top-level wrappers
    swallow it and return ``[]`` (fail-soft contract).
    """


# ───── Safe HTTP helpers ──────────────────────────────────────────
async def robust_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    max_retries: int = MAX_RETRIES,
) -> Optional[httpx.Response]:
    """GET with exponential-backoff retry on 429 / 5xx.

    Returns the final response (which may be non-200) or ``None`` if all
    retries exhausted with a network error.
    """
    attempt = 0
    backoff = 1.0
    while True:
        try:
            response = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as exc:
            if attempt >= max_retries:
                log.debug("robust_get network error after retries: %s", exc)
                return None
            await asyncio.sleep(backoff)
            attempt += 1
            backoff *= 2
            continue

        status = response.status_code
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            if attempt >= max_retries:
                return response
            ra = response.headers.get("Retry-After")
            try:
                wait_s = float(ra) if ra else backoff
            except ValueError:
                wait_s = backoff
            await asyncio.sleep(wait_s)
            attempt += 1
            backoff *= 2
            continue

        if status in (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_EARLY) or 500 <= status < 600:
            if attempt >= max_retries:
                return response
            await asyncio.sleep(backoff)
            attempt += 1
            backoff *= 2
            continue

        return response


def safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except (TypeError, ValueError):
        return default


def keys_lower(d: dict | None) -> dict:
    if not isinstance(d, dict):
        return {}
    return {(k or "").lower(): v for k, v in d.items()}
