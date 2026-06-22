"""API-Football v3 async client — **DEPRECATED STUB (F99.2)**.

This module used to be the active legacy client for api-sports.io. After
Phase F99 it is **completely decommissioned** from the football pipeline.
The file is kept on disk only for **import compatibility** during the
transition (some external scripts may still reference the symbols). It:

  * does **not** perform any outbound HTTP IO,
  * does **not** read or use the ``API_FOOTBALL_KEY`` env var at runtime,
  * does **not** participate in any active enrichment or fallback path,
  * does **not** write to ``enrichment_audit`` provenance,
  * is **not** imported by the active football pipeline anymore.

Public symbols return fail-closed values (``[]`` / ``None`` / ``{}``).
A stub counter (``DEPRECATED_STUB_USAGE_COUNTERS``) is incremented every
time a deprecated function is reached, and a single info log is emitted
once per process to flag pending tech debt.

Removal plan (post F99.2):
  1. Confirm zero call-sites via ``grep -rn "api_football\\|from . import .*api_football" services/``.
  2. Delete the file in a follow-up phase once all referencing tests are
     migrated / removed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

log = logging.getLogger("api_football")

# F99.2 — kept for backwards compatibility, but the runtime never reads
# the API key. It is left here so legacy tests that monkeypatch the
# value don't break.
API_KEY = os.environ.get("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

# F99 — kill switch for the API-Sports client.
DISABLE_FLAG_ENV_VAR = "DISABLE_API_FOOTBALL"

# F99.2 — stub usage telemetry. The counter is incremented from ``_get``
# whenever a function is reached so a downstream observer (tests, audit
# tools) can detect lingering call-sites. We also expose a reason code
# so the trace block of the football pipeline can declare the legacy
# touch when it happens.
DEPRECATED_STUB_REASON_CODE = "API_FOOTBALL_DEPRECATED_STUB_USED"
DEPRECATED_STUB_USAGE_COUNTERS: dict[str, int] = {
    "_get":                        0,
    "fixtures_by_date":            0,
    "fixtures_next_48h":           0,
    "fixtures_live":               0,
    "fixtures_by_league_window":   0,
    "fixture_by_id":               0,
    "odds_for_fixture":            0,
    "team_statistics":             0,
    "standings":                   0,
    "head_to_head":                0,
    "injuries":                    0,
    "fixture_statistics":          0,
    "team_corner_form":            0,
    "fixtures_last_n":             0,
}
_DEPRECATION_NOTICE_EMITTED = False


def _bump_stub_counter(label: str) -> None:
    """Internal helper: count a deprecated-stub touch + one-shot info log."""
    global _DEPRECATION_NOTICE_EMITTED
    try:
        DEPRECATED_STUB_USAGE_COUNTERS[label] = (
            DEPRECATED_STUB_USAGE_COUNTERS.get(label, 0) + 1
        )
    except Exception:  # noqa: BLE001
        pass
    if not _DEPRECATION_NOTICE_EMITTED:
        log.info(
            "[F99.2] api_football is decommissioned; reached deprecated stub %s. "
            "Migrate caller to TheSportsDB / SofaScore / TheStatsAPI.",
            label,
        )
        _DEPRECATION_NOTICE_EMITTED = True


def is_disabled() -> bool:
    """True when the kill switch is on (default in F99.2).

    The flag remains for backwards compatibility but in F99.2 the stub
    is **always** fail-closed regardless of the flag. We still expose
    ``is_disabled()`` so caller-side flag-checks behave as before.
    """
    raw = os.environ.get(DISABLE_FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


PROXY_SEASON = 2024  # Free plan limit (kept for legacy callers).

# Cache TTLs
ODDS_TTL_MIN = 30
CONTEXT_TTL_HOURS = 6

# Global rate limiter ~8 req/min (safe under 10 req/min hard cap)
class _RateLimiter:
    def __init__(self, max_calls: int = 8, period_sec: int = 60):
        self.max_calls = max_calls
        self.period = period_sec
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # drop calls outside window
            self._calls = [t for t in self._calls if now - t < self.period]
            if len(self._calls) >= self.max_calls:
                wait = self.period - (now - self._calls[0]) + 0.05
                if wait > 0:
                    log.info("Rate limit: sleeping %.2fs", wait)
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls if now - t < self.period]
            self._calls.append(time.monotonic())


_LIMITER = _RateLimiter(max_calls=8, period_sec=60)


class APIFootballError(Exception):
    pass


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    # F99.2 — DEPRECATED STUB. Always short-circuit: zero IO, fail-closed.
    # The legacy kill switch ``DISABLE_API_FOOTBALL`` is no longer required
    # because the stub now ALWAYS returns the empty response envelope.
    _bump_stub_counter("_get")
    return {
        "response":      [],
        "errors":        {},
        "_f99_disabled": True,
        "_f99_deprecated_stub": True,
        "_reason_code":  DEPRECATED_STUB_REASON_CODE,
    }


# ── Cache helpers (kept read-only for audit / backfill — NEVER written) ────
def _cache_fresh(doc: dict | None, ttl_minutes: int) -> bool:
    if not doc:
        return False
    ts = doc.get("_cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    return (datetime.now(timezone.utc) - cached) < timedelta(minutes=ttl_minutes)


async def _cache_get(db, collection: str, key: dict, ttl_minutes: int) -> dict | None:
    """READ-ONLY in F99.2 — kept for audit tooling that might still query the
    historical fb_* / cache_* collections. The stub itself never reaches
    here at runtime because ``_get`` returns immediately."""
    if db is None:
        return None
    doc = await db[collection].find_one(key)
    if _cache_fresh(doc, ttl_minutes):
        return doc.get("data")
    return None


async def _cache_set(db, collection: str, key: dict, data: Any) -> None:
    """F99.2 — **NO-OP**. We no longer write new entries into the legacy
    API-Sports caches. The function is kept so any rogue caller (we
    expect none after the data_ingestion purge) does not raise.
    """
    # Deliberately do nothing. The legacy caches stay frozen as historical
    # snapshots; we never extend them with new records.
    return None


# ── Public endpoints (with optional db cache) ────────────────────────────────
async def fixtures_by_date(client: httpx.AsyncClient, date_iso: str) -> list[dict]:
    _bump_stub_counter("fixtures_by_date")
    data = await _get(client, "/fixtures", {"date": date_iso})
    return data.get("response", []) or []


async def fixtures_next_48h(client: httpx.AsyncClient) -> list[dict]:
    _bump_stub_counter("fixtures_next_48h")
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for d in (today, tomorrow):
        out.extend(await fixtures_by_date(client, d.isoformat()))
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=48)
    res = []
    for f in out:
        try:
            ts = f["fixture"]["timestamp"]
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            status = f["fixture"]["status"]["short"]
            if status in ("NS", "TBD") and now - timedelta(minutes=10) <= dt <= cutoff:
                res.append(f)
        except Exception:
            pass
    return res


async def fixtures_live(client: httpx.AsyncClient) -> list[dict]:
    _bump_stub_counter("fixtures_live")
    data = await _get(client, "/fixtures", {"live": "all"})
    return data.get("response", []) or []


async def fixtures_by_league_window(
    client: httpx.AsyncClient,
    league_id: int,
    season: int,
    *,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """[F99.2 STUB] Always returns ``[]`` — API-Sports is decommissioned."""
    _bump_stub_counter("fixtures_by_league_window")
    data = await _get(client, "/fixtures", {
        "league": league_id,
        "season": season,
        "from": from_date,
        "to": to_date,
    })
    return data.get("response", []) or []


async def fixture_by_id(client: httpx.AsyncClient, fixture_id: int) -> dict | None:
    _bump_stub_counter("fixture_by_id")
    data = await _get(client, "/fixtures", {"id": fixture_id})
    resp = data.get("response", []) or []
    return resp[0] if resp else None


async def odds_for_fixture(client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    _bump_stub_counter("odds_for_fixture")
    key = {"fixture_id": fixture_id}
    cached = await _cache_get(db, "cache_odds", key, ODDS_TTL_MIN)
    if cached is not None:
        return cached
    data = await _get(client, "/odds", {"fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_odds", key, resp)
    return resp


async def team_statistics(client: httpx.AsyncClient, team_id: int, league_id: int, season: int = PROXY_SEASON, db=None) -> dict:
    _bump_stub_counter("team_statistics")
    key = {"team_id": team_id, "league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_team_stats", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/teams/statistics", {"team": team_id, "league": league_id, "season": season})
    resp = data.get("response") or {}
    await _cache_set(db, "cache_team_stats", key, resp)
    return resp


async def standings(client: httpx.AsyncClient, league_id: int, season: int = PROXY_SEASON, db=None) -> list[dict]:
    _bump_stub_counter("standings")
    key = {"league_id": league_id, "season": season}
    cached = await _cache_get(db, "cache_standings", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/standings", {"league": league_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_standings", key, resp)
    return resp


async def head_to_head(client: httpx.AsyncClient, home_id: int, away_id: int, limit: int = 5, db=None) -> list[dict]:
    _bump_stub_counter("head_to_head")
    key = {"h2h_key": f"{home_id}-{away_id}"}
    cached = await _cache_get(db, "cache_h2h", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached[:limit]
    data = await _get(client, "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}"})
    items = data.get("response", []) or []
    items.sort(key=lambda f: f.get("fixture", {}).get("timestamp", 0), reverse=True)
    await _cache_set(db, "cache_h2h", key, items)
    return items[:limit]


async def injuries(client: httpx.AsyncClient, team_id: int, season: int = PROXY_SEASON, db=None) -> list[dict]:
    _bump_stub_counter("injuries")
    key = {"team_id": team_id, "season": season}
    cached = await _cache_get(db, "cache_injuries", key, CONTEXT_TTL_HOURS * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/injuries", {"team": team_id, "season": season})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_injuries", key, resp)
    return resp


async def fixture_statistics(client: httpx.AsyncClient, fixture_id: int, db=None) -> list[dict]:
    """[F99.2 STUB] Per-team statistics for a finished fixture. Always returns ``[]``."""
    _bump_stub_counter("fixture_statistics")
    key = {"fixture_id": int(fixture_id)}
    cached = await _cache_get(db, "cache_fixture_stats", key, 7 * 24 * 60)
    if cached is not None:
        return cached
    data = await _get(client, "/fixtures/statistics", {"fixture": fixture_id})
    resp = data.get("response", []) or []
    await _cache_set(db, "cache_fixture_stats", key, resp)
    return resp


def _corners_from_fixture_stats(stats_resp: list[dict], team_id: int) -> tuple[int, int] | None:
    """Extract (corners_for, corners_against) from a /fixtures/statistics response.

    Returns None if the corner statistic is missing for either team.
    Stats response shape:
      [{"team":{"id":X,"name":...}, "statistics":[{"type":"Corner Kicks","value":7}, ...]},
       {"team":{"id":Y,"name":...}, "statistics":[...]}]
    """
    if not stats_resp or not isinstance(stats_resp, list) or len(stats_resp) < 2:
        return None
    team_id = int(team_id)
    my_corners: Optional[int] = None
    opp_corners: Optional[int] = None
    for block in stats_resp:
        t_id = ((block.get("team") or {}).get("id"))
        stats_arr = block.get("statistics") or []
        cv = None
        for s in stats_arr:
            if (s.get("type") or "").strip().lower() in ("corner kicks", "corners"):
                v = s.get("value")
                if v is None:
                    continue
                try:
                    cv = int(v)
                except (TypeError, ValueError):
                    try:
                        cv = int(float(v))
                    except (TypeError, ValueError):
                        cv = None
                break
        if cv is None:
            continue
        if t_id == team_id:
            my_corners = cv
        else:
            opp_corners = cv
    if my_corners is None or opp_corners is None:
        return None
    return (my_corners, opp_corners)


async def team_corner_form(
    client: httpx.AsyncClient,
    team_id: int,
    *,
    n: int = 5,
    season: int = PROXY_SEASON,
    db=None,
) -> dict:
    """[F99.2 STUB] Corner kicks form. Returns an empty form dict (sample_size=0)."""
    _bump_stub_counter("team_corner_form")
    n = max(1, min(int(n or 5), 10))
    key = {"team_id": int(team_id), "season": season, "n": n, "kind": "corner_form"}
    cached = await _cache_get(db, "cache_team_corner_form", key, 12 * 60)
    if cached is not None:
        return cached

    fixtures = await fixtures_last_n(client, team_id, n=n, season=season, db=db)

    per_match: list[dict] = []
    for fx in (fixtures or [])[:n]:
        fx_id = (fx.get("fixture") or {}).get("id")
        # Skip non-finished games (no corner stats available yet)
        short = ((fx.get("fixture") or {}).get("status") or {}).get("short")
        if short not in ("FT", "AET", "PEN"):
            continue
        if not fx_id:
            continue
        try:
            stats = await fixture_statistics(client, fx_id, db=db)
        except Exception:
            continue
        corners = _corners_from_fixture_stats(stats, team_id)
        if not corners:
            continue
        c_for, c_against = corners
        per_match.append({
            "fixture_id":      int(fx_id),
            "date":             (fx.get("fixture") or {}).get("date"),
            "corners_for":      c_for,
            "corners_against":  c_against,
        })

    sample = len(per_match)
    if sample == 0:
        result = {
            "team_id":      int(team_id),
            "sample_size":  0,
            "avg_for":      None,
            "avg_against":  None,
            "avg_total":    None,
            "per_match":    [],
            "missing_data": True,
        }
    else:
        avg_for     = sum(m["corners_for"]     for m in per_match) / sample
        avg_against = sum(m["corners_against"] for m in per_match) / sample
        result = {
            "team_id":      int(team_id),
            "sample_size":  sample,
            "avg_for":      round(avg_for,     2),
            "avg_against":  round(avg_against, 2),
            "avg_total":    round(avg_for + avg_against, 2),
            "per_match":    per_match,
            "missing_data": sample < 3,
        }
    await _cache_set(db, "cache_team_corner_form", key, result)
    return result


async def fixtures_last_n(
    client: httpx.AsyncClient,
    team_id: int,
    *,
    n: int = 10,
    season: int | None = PROXY_SEASON,
    db=None,
    include_all_competitions: bool = False,
) -> list[dict]:
    """[F99.2 STUB] Returns ``[]`` — recent fixtures must come from
    TheSportsDB / SofaScore / TheStatsAPI now."""
    _bump_stub_counter("fixtures_last_n")
    n = max(1, min(int(n or 10), 20))
    # Sprint-D9.2 Block A — cross-tournament fan-in for national teams.
    use_global = bool(include_all_competitions) or season is None
    kind = "last_n_global" if use_global else "last_n"
    key = {"team_id": team_id, "season": (None if use_global else season),
            "kind": kind}
    cached = await _cache_get(db, "cache_team_recent_fixtures", key, 12 * 60)
    if cached is not None:
        return cached[:n]
    params: dict[str, Any] = {"team": team_id, "last": n}
    if not use_global:
        params["season"] = season
    data = await _get(client, "/fixtures", params)
    resp = data.get("response", []) or []
    # API-Sports returns chronological; keep newest-first.
    resp.sort(key=lambda f: ((f.get("fixture") or {}).get("timestamp") or 0), reverse=True)
    await _cache_set(db, "cache_team_recent_fixtures", key, resp)
    return resp[:n]
