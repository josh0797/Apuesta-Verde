"""Multi-source fallback scrapers.

When API-Football fails entirely, we pull basic fixture lists from public sources.
We prioritize sources with clean JSON endpoints / static HTML.

Returned schema (minimal):
  {
    id: str,
    source: str,                # 'espn'|'sofascore'|'sportytrader'|'flashscore'
    league: str,
    kickoff_iso: str,
    is_live: bool,
    minute: int|None,
    home_team: {name, score?},
    away_team: {name, score?},
  }
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from selectolax.parser import HTMLParser

log = logging.getLogger("fallback")

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}


# ── ESPN (still primary fallback — JSON endpoint) ────────────────────────────
async def espn_soccer_scoreboard(client: httpx.AsyncClient) -> list[dict]:
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
    try:
        r = await client.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("ESPN fallback failed: %s", e)
        return []
    out = []
    for ev in (data.get("events") or [])[:60]:
        try:
            comp = (ev.get("competitions") or [{}])[0]
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            status = (comp.get("status") or {}).get("type", {})
            league = (ev.get("league", {}) or {}).get("name") or (comp.get("league") or "")
            out.append({
                "id": f"espn-{ev.get('id')}",
                "source": "espn",
                "league": league,
                "kickoff_iso": ev.get("date"),
                "status": status.get("description"),
                "is_live": status.get("state") in ("in", "live"),
                "minute": (comp.get("status") or {}).get("displayClock"),
                "home_team": {
                    "name": (home.get("team") or {}).get("displayName", "Home"),
                    "score": int(home.get("score", 0) or 0),
                },
                "away_team": {
                    "name": (away.get("team") or {}).get("displayName", "Away"),
                    "score": int(away.get("score", 0) or 0),
                },
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            continue
    return out


# ── Sofascore (unofficial public JSON endpoint) ──────────────────────────────
async def sofascore_today(client: httpx.AsyncClient, date_iso: str | None = None) -> list[dict]:
    """Sofascore public scheduled-events feed.

    The unofficial endpoint  https://api.sofascore.com/api/v1/sport/football/scheduled-events/<YYYY-MM-DD>
    returns a JSON with `events` array.
    """
    if date_iso is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()
    url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_iso}"
    try:
        r = await client.get(url, headers={**UA, "Referer": "https://www.sofascore.com/"}, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("Sofascore fallback failed: %s", e)
        return []
    out = []
    for ev in (data.get("events") or [])[:120]:
        try:
            status = ev.get("status", {})
            stype = (status.get("type") or "").lower()
            tournament = ev.get("tournament", {}) or {}
            league = (tournament.get("name") or "") + (" - " + (tournament.get("category", {}) or {}).get("name", "") if (tournament.get("category") or {}).get("name") else "")
            ts = ev.get("startTimestamp")
            kickoff_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
            out.append({
                "id": f"sofa-{ev.get('id')}",
                "source": "sofascore",
                "league": league.strip(" -"),
                "kickoff_iso": kickoff_iso,
                "status": status.get("description"),
                "is_live": stype == "inprogress",
                "minute": status.get("description"),
                "home_team": {
                    "name": (ev.get("homeTeam") or {}).get("name", "Home"),
                    "score": ((ev.get("homeScore") or {}).get("current")),
                },
                "away_team": {
                    "name": (ev.get("awayTeam") or {}).get("name", "Away"),
                    "score": ((ev.get("awayScore") or {}).get("current")),
                },
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            continue
    return out


# ── SportyTrader (HTML parsing) ──────────────────────────────────────────────
async def sportytrader_today(client: httpx.AsyncClient) -> list[dict]:
    """SportyTrader HTML scoreboard scraper (best-effort).

    HTML is unstable but we extract whatever we can.
    """
    url = "https://www.sportytrader.com/en/football/matches/"
    try:
        r = await client.get(url, headers=UA, timeout=15, follow_redirects=True)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("SportyTrader fallback failed: %s", e)
        return []
    out: list[dict] = []
    try:
        tree = HTMLParser(html)
        # Heuristic: find <a> rows mentioning teams via vs/ separator
        for node in tree.css("a")[:300]:
            txt = (node.text() or "").strip()
            if not txt or len(txt) < 5 or len(txt) > 90:
                continue
            for sep in (" vs ", " - ", " v "):
                if sep in txt:
                    parts = txt.split(sep, 1)
                    if len(parts) == 2 and 2 <= len(parts[0]) <= 40 and 2 <= len(parts[1]) <= 40:
                        out.append({
                            "id": f"sport-{abs(hash(txt))%10**9}",
                            "source": "sportytrader",
                            "league": "",
                            "kickoff_iso": None,
                            "status": None,
                            "is_live": False,
                            "minute": None,
                            "home_team": {"name": parts[0].strip()},
                            "away_team": {"name": parts[1].strip()},
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        })
                        break
            if len(out) >= 30:
                break
    except Exception as e:
        log.warning("SportyTrader parse failed: %s", e)
    return out


# ── Flashscore (very JS-heavy; lightweight HTML scrape) ──────────────────────
async def flashscore_today(client: httpx.AsyncClient) -> list[dict]:
    url = "https://www.flashscore.com/football/"
    try:
        r = await client.get(url, headers=UA, timeout=15, follow_redirects=True)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("Flashscore fallback failed: %s", e)
        return []
    # Flashscore is heavily JS-rendered; static HTML rarely contains scores.
    # We just check if site reachable + return source marker.
    return [{"source": "flashscore", "note": "requires JS rendering; not parsed", "fetched_at": datetime.now(timezone.utc).isoformat()}] if html else []


# ── Aggregator ───────────────────────────────────────────────────────────────
async def aggregate_fallback(client: httpx.AsyncClient, use_playwright: bool = False) -> dict:
    """Run all scrapers in parallel and return aggregated results.

    use_playwright=True enables the heavier browser-based bypass via Crawlee
    (with TLS fingerprinting + session pool). Only enable when the standard
    httpx scrapers return 0 and you need data, since browser launches cost ~3s.

    Notes:
      - Sofascore's API blocks Kubernetes datacenter IPs at the application
        layer (returns JSON 403 even after Cloudflare clearance). Without a
        residential proxy this source will keep failing — kept for parity.
      - Flashscore loads fine via Crawlee (no API gate, just CF Turnstile on
        first hit which the fingerprinted browser passes through).
      - Browser-based scrapers are run SEQUENTIALLY (not concurrently) because
        Crawlee's PlaywrightCrawler uses global per-process configuration —
        concurrent instances interfere with each other's session/storage.
    """
    # Lightweight httpx scrapers always run in parallel
    http_tasks = [
        espn_soccer_scoreboard(client),
        sofascore_today(client),
        sportytrader_today(client),
    ]
    http_results = await asyncio.gather(*http_tasks, return_exceptions=True)

    def _safe(x): return x if isinstance(x, list) else []

    out = {
        "espn": _safe(http_results[0]),
        "sofascore": _safe(http_results[1]),
        "sportytrader": _safe(http_results[2]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if use_playwright:
        try:
            from . import crawlee_scraper as cws
            pw_module = "crawlee"
            # Serial execution avoids Crawlee global-state conflicts
            sofa_pw = await cws.sofascore_via_crawlee()
            flash_pw = await cws.flashscore_via_crawlee()
        except Exception as e:
            log.warning("crawlee import/run failed (%s); falling back to playwright_scraper", e)
            from . import playwright_scraper as pws
            pw_module = "playwright_legacy"
            sofa_pw, flash_pw = await asyncio.gather(
                pws.sofascore_via_playwright(),
                pws.flashscore_via_playwright(),
                return_exceptions=True,
            )

        out["sofascore_crawlee"] = _safe(sofa_pw)
        out["flashscore_crawlee"] = _safe(flash_pw)
        # Legacy aliases for backward compatibility
        out["sofascore_pw"] = out["sofascore_crawlee"]
        out["flashscore_pw"] = out["flashscore_crawlee"]
        out["browser_engine"] = pw_module
    return out
