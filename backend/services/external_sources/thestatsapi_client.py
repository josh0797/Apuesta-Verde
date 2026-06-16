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


async def fetch_recent_matches(
    team_id: int | str,
    *,
    n: int = 15,
    client: httpx.AsyncClient | None = None,
    status: str = "finished",
    sport: str = "football",
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> list[dict]:
    """FIX-4 — Like :func:`fetch_recent_match_ids` but returns the FULL
    match dicts so callers can read ``home_team_id`` / ``away_team_id``
    without an extra round-trip.

    Returned dict items have at least ``id`` and (when the provider
    exposes them) ``home_team_id`` / ``away_team_id``, plus any other
    keys the upstream endpoint returns.

    Fail-soft: returns ``[]`` on any error.
    """
    if not team_id or not is_enabled():
        return []
    sport = (sport or "football").lower()
    base_path = f"/{sport}/matches" if sport == "football" else "/football/matches"
    params: dict[str, Any] = {"team_id": str(team_id), "limit": int(n) if n and int(n) > 0 else 15}
    if status:
        params["status"] = status

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        payload = await _request(client, "GET", base_path, params=params, timeout=timeout)
    except Exception as exc:
        log.debug("[thestatsapi] fetch_recent_matches failed team=%s: %s", team_id, exc)
        payload = {}
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:
                pass
    if not isinstance(payload, dict):
        return []
    matches = _extract_list(payload, candidate_keys=("data", "matches", "response", "results"))
    out: list[dict] = []
    for m in matches or []:
        if isinstance(m, dict) and (m.get("id") or m.get("match_id")):
            out.append(m)
        if len(out) >= int(n):
            break
    return out


async def fetch_recent_match_ids(
    team_id: int | str,
    *,
    n: int = 15,
    client: httpx.AsyncClient | None = None,
    status: str = "finished",
    sport: str = "football",
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> list[str]:
    """FIX-1 — Return the ``n`` most recent TheStatsAPI match IDs for a team.

    Required by :func:`football_xg_recent_averages.compute_xg_recent_averages`
    so the L1/L5/L15 xG averages can normalise (previously this helper
    was missing, raising ``AttributeError`` at call site → IDs were
    never populated → shotmap had nothing to query → xG never
    normalised in any match).

    Endpoint::

        GET /football/matches?team_id={tm_XXX}&status=finished&limit={n}

    Returns a flat list of match IDs (strings, ``mt_XXX`` format).
    Fail-soft: returns ``[]`` on any error (HTTP / parse / disabled).

    Notes
    -----
    * ``team_id`` MUST be the TheStatsAPI native ID (``tm_XXX``) — NOT
      the API-Sports numeric id. The caller is responsible for passing
      the right one (look at ``home_team._thestatsapi_id`` /
      ``away_team._thestatsapi_id``).
    * If ``is_enabled()`` is false (disabled flag or missing key) we
      short-circuit to ``[]`` without making any HTTP call.
    """
    if not team_id:
        return []
    if not is_enabled():
        return []

    sport = (sport or "football").lower()
    base_path = f"/{sport}/matches" if sport == "football" else "/football/matches"
    params: dict[str, Any] = {
        "team_id": str(team_id),
        "limit":   int(n) if n and int(n) > 0 else 15,
    }
    if status:
        params["status"] = status

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        payload = await _request(
            client, "GET", base_path, params=params, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        log.debug("[thestatsapi] fetch_recent_match_ids failed team=%s: %s",
                  team_id, exc)
        payload = {}
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    if not isinstance(payload, dict):
        return []
    matches = _extract_list(payload, candidate_keys=("data", "matches", "response", "results"))
    out: list[str] = []
    for m in matches or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("match_id")
        if mid:
            out.append(str(mid))
        if len(out) >= int(n):
            break
    return out


async def fetch_match_details(
    client: httpx.AsyncClient,
    match_id: int | str,
    *,
    sport: str = "football",
) -> dict:
    """Return rich match-level details (lineups, venue, referees, ...).

    Used by Batch 3 pre-match enrichment. Sport-aware path:
      * football   → ``/football/matches/{id}``
      * basketball → ``/basketball/matches/{id}``
      * baseball   → ``/baseball/games/{id}``
    """
    sport = (sport or "football").lower()
    if sport in {"basketball", "baseball"}:
        prefix = f"/{sport}/matches" if sport == "basketball" else "/baseball/games"
    else:
        prefix = "/football/matches"
    data = await _request(client, "GET", f"{prefix}/{match_id}")
    if not isinstance(data, dict):
        return {}
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


async def fetch_team_stats(
    client: httpx.AsyncClient,
    team_id: int | str,
    *,
    sport: str = "football",
    season: int | str | None = None,
    competition_id: int | str | None = None,
) -> dict:
    """Return aggregate season stats for a team (form, xG/match, etc.)."""
    sport = (sport or "football").lower()
    params: dict[str, Any] = {}
    if season is not None:
        params["season"] = season
    if competition_id is not None:
        params["competition_id"] = competition_id
    data = await _request(client, "GET", f"/{sport}/teams/{team_id}/stats", params=params or None)
    if not isinstance(data, dict):
        return {}
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


async def fetch_player_stats(
    client: httpx.AsyncClient,
    player_id: int | str,
    *,
    sport: str = "football",
    season: int | str | None = None,
) -> dict:
    """Return per-player season stats (goals, xG, minutes, etc.)."""
    sport = (sport or "football").lower()
    params: dict[str, Any] = {"season": season} if season is not None else None
    data = await _request(client, "GET", f"/{sport}/players/{player_id}/stats", params=params)
    if not isinstance(data, dict):
        return {}
    if "data" in data and isinstance(data["data"], dict):
        return data["data"]
    return data


# ─────────────────────────────────────────────────────────────────────
# Phase F74-post v2 — Odds fallback (TheStatsAPI → API-Sports shape)
# ─────────────────────────────────────────────────────────────────────
async def odds_for_fixture(
    client: httpx.AsyncClient,
    thestatsapi_match_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Fetch odds for a fixture from TheStatsAPI.

    Endpoint: ``GET /api/football/matches/{match_id}/odds`` (Bearer auth).

    Returns the raw ``data`` sub-dict from the response::

        {"match_id": "mt_14502", "bookmakers": [{...}, ...]}

    Fail-soft: returns ``{}`` on any error (HTTP, parse, timeout, disabled).
    The ``match_id`` format is "mt_XXXXX" — pass it as-is, do not strip
    the prefix.
    """
    if not thestatsapi_match_id:
        return {}
    payload = await _request(
        client, "GET",
        f"/football/matches/{thestatsapi_match_id}/odds",
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────────────
# Phase F74-post v2 — Resolver match-id por nombres + fecha (fallback)
# ─────────────────────────────────────────────────────────────────────
def _normalize_team_name_for_search(name: str) -> str:
    """lower + strip-accents."""
    if not isinstance(name, str):
        return ""
    import unicodedata as _u
    nf = _u.normalize("NFD", name)
    return "".join(c for c in nf if _u.category(c) != "Mn").lower().strip()


async def resolve_thestatsapi_match_id_by_names(
    client: httpx.AsyncClient,
    *,
    home: str,
    away: str,
    date: str,
    competition: str | None = None,
) -> str | None:
    """Resolve TheStatsAPI ``match_id`` by listing fixtures of the day
    and matching by normalised team names.

    Used by ``_enrich_football`` when the API-Sports fixture has no
    ``_thestatsapi_raw_id`` (no Batch-3 mapping).

    Args:
        client: shared ``httpx.AsyncClient`` (injected from caller).
        home, away: API-Sports team names.
        date: ISO-8601 (any datetime parseable) — we only use the date.
        competition: optional name to filter ambiguous cross-competition matches.

    Returns:
        ``"mt_XXXXX"`` if a unique match is found, else ``None``.
    """
    if not (home and away and date):
        return None
    if not is_enabled():
        return None
    # Date window of 1 day (we accept "2026-04-19" or "2026-04-19T20:00:00Z").
    day = str(date)[:10]
    matches = await fetch_fixtures(client, date_from=day, date_to=day)
    if not matches:
        return None
    h_norm = _normalize_team_name_for_search(home)
    a_norm = _normalize_team_name_for_search(away)
    c_norm = _normalize_team_name_for_search(competition or "")
    for entry in matches:
        if not isinstance(entry, dict):
            continue
        ht = entry.get("home_team") or entry.get("home") or {}
        at = entry.get("away_team") or entry.get("away") or {}
        ht_name = (ht.get("name") if isinstance(ht, dict) else None) or entry.get("home_team_name") or ""
        at_name = (at.get("name") if isinstance(at, dict) else None) or entry.get("away_team_name") or ""
        ht_norm = _normalize_team_name_for_search(ht_name)
        at_norm = _normalize_team_name_for_search(at_name)
        match_ok = (
            (ht_norm == h_norm and at_norm == a_norm)
            or (h_norm and ht_norm and (h_norm in ht_norm or ht_norm in h_norm)
                 and a_norm and at_norm
                 and (a_norm in at_norm or at_norm in a_norm))
        )
        if not match_ok:
            continue
        if c_norm:
            comp = entry.get("competition") or {}
            comp_name = (comp.get("name") if isinstance(comp, dict) else None) or entry.get("competition_name") or ""
            comp_norm = _normalize_team_name_for_search(comp_name)
            if comp_norm and c_norm not in comp_norm:
                continue
        match_id = entry.get("id") or entry.get("match_id") or entry.get("_id")
        if match_id:
            return str(match_id)
    log.info("[thestatsapi] match-id not found for %s vs %s @%s", h_norm, a_norm, day)
    return None


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
