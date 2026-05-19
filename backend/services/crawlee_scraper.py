"""Crawlee-based scrapers with anti-bot fingerprinting.

Replaces the legacy `playwright_scraper.py` with Crawlee for Python's
`PlaywrightCrawler` + `DefaultFingerprintGenerator`. Crawlee gives us:

  - Realistic browser fingerprints (UA, screen, WebGL, fonts, plugins, headers)
  - Session pool with rotating cookies (Cloudflare clearance reused/rotated)
  - Automatic retry on soft-blocks
  - Built-in stealth tweaks (webdriver flag removal, etc.)

Public entry-points (drop-in replacements):
  - sofascore_via_crawlee(date_iso=None) -> list[dict]
  - flashscore_via_crawlee()             -> list[dict]

Notes:
  - Browser launch is expensive (~2s) and Sofascore enforces CF challenges.
  - We aggressively cache via Mongo at the caller layer; this module is just
    the raw fetch.
  - `headless=True` is required (no display server in container).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("crawlee_scraper")

# Use the pre-installed chromium binary path baked into the container.
# Falls back to Playwright's default resolution if the env var is unset.
_BROWSER_PATH = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/pw-browsers"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _BROWSER_PATH)

# Force a writable per-process storage dir so Crawlee doesn't pollute /app
# and survives container restarts without permission issues.
_STORAGE_DIR = os.environ.get("CRAWLEE_STORAGE_DIR") or "/tmp/crawlee_storage"
os.environ.setdefault("CRAWLEE_STORAGE_DIR", _STORAGE_DIR)
os.environ.setdefault("CRAWLEE_PURGE_ON_START", "1")
try:
    os.makedirs(_STORAGE_DIR, exist_ok=True)
except Exception:  # pragma: no cover
    pass

# Lazy guard so the rest of the app still imports if crawlee is missing.
try:
    from crawlee import service_locator
    from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    from crawlee.fingerprint_suite import DefaultFingerprintGenerator, HeaderGeneratorOptions
    from crawlee.storage_clients import MemoryStorageClient
    _CRAWLEE_OK = True
except Exception as e:  # pragma: no cover
    log.warning("crawlee import failed: %s", e)
    _CRAWLEE_OK = False


def _reset_crawlee_state() -> None:
    """Clear Crawlee's global storage cache between runs.

    Crawlee uses a process-wide ServiceLocator with cached storage instances
    (request queue, dataset, key-value store). When multiple crawler runs
    execute in the same Python process (e.g. FastAPI request handlers),
    subsequent runs reuse the drained queue from the previous run and end up
    with `requests_total: 0`. Clearing the cache forces a fresh queue per run.
    """
    if not _CRAWLEE_OK:
        return
    try:
        service_locator.storage_instance_manager.clear_cache()
        # Also swap in a fresh in-memory storage client so persistent state
        # from prior runs (cookies, session pool) does not leak across calls.
        service_locator.set_storage_client(MemoryStorageClient())
    except Exception as e:  # pragma: no cover
        log.debug("crawlee state reset partial: %s", e)


def _build_crawler(*, max_concurrency: int = 1, request_handler_timeout_s: int = 40) -> Any:
    """Construct a stealth-configured PlaywrightCrawler instance.

    Each crawler is single-shot (one URL) — we run, gather, then exit so the
    asyncio task stays small and we don't hold a browser between requests.
    """
    fp_generator = DefaultFingerprintGenerator(
        header_options=HeaderGeneratorOptions(
            browsers=["chrome"],
            operating_systems=["linux", "macos", "windows"],
            locales=["en-US", "en"],
        )
    )

    crawler = PlaywrightCrawler(
        max_requests_per_crawl=1,
        max_request_retries=2,
        max_session_rotations=4,
        request_handler_timeout=timedelta(seconds=request_handler_timeout_s),
        headless=True,
        browser_type="chromium",
        fingerprint_generator=fp_generator,
        # Persist cf_clearance cookies across retries inside the same run.
        use_session_pool=True,
        retry_on_blocked=True,
        # Cloudflare's interstitial returns 403 with a JS challenge. We tell
        # crawlee NOT to treat 403 as a hard session block so that the in-page
        # JS challenge has a chance to set cf_clearance.
        ignore_http_error_status_codes=[403, 429, 503],
        configure_logging=False,
        # Container runs as root → must disable Chromium sandbox.
        browser_launch_options={
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        },
    )
    return crawler


# ── Sofascore ────────────────────────────────────────────────────────────────
async def sofascore_via_crawlee(date_iso: str | None = None) -> list[dict]:
    """Fetch Sofascore scheduled-events JSON behind Cloudflare.

    Strategy:
      1. Warm the session by visiting https://www.sofascore.com/ — this gets
         a `cf_clearance` cookie set on the crawlee session.
      2. Navigate to the JSON endpoint within the same browser context (cookie
         is re-sent automatically). The JSON renders as plain text in <body>.
      3. Parse and shape into our normalized event schema.
    """
    if not _CRAWLEE_OK:
        log.warning("crawlee not available; skipping Sofascore")
        return []

    _reset_crawlee_state()

    if date_iso is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()

    api_url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_iso}"
    home_url = "https://www.sofascore.com/"

    out: list[dict] = []
    captured: dict[str, Any] = {"json": None, "status": None}

    crawler = _build_crawler(request_handler_timeout_s=45)

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        # 1. Warm-up CF cookie on the homepage so the browser has a valid
        #    session for the *.sofascore.com domain.
        try:
            resp = await page.goto(home_url, wait_until="domcontentloaded", timeout=25000)
            if resp and resp.status == 403:
                context.log.info("CF challenge detected on homepage; waiting for clearance…")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)
            else:
                await page.wait_for_timeout(2500)
        except Exception as e:
            context.log.warning(f"warmup failed: {e}")

        # 2. Trigger an in-page navigation to a real match listing — this forces
        #    the SPA to load and issues XHR to api.sofascore.com, which warms a
        #    cf_clearance for the API subdomain too.
        try:
            await page.goto(
                "https://www.sofascore.com/football/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await page.wait_for_timeout(2500)
        except Exception as e:
            context.log.warning(f"football page warmup failed: {e}")

        # 3. Fetch the API endpoint using Playwright's APIRequestContext —
        #    bypasses browser CORS while still re-using all cookies (cf_clearance)
        #    and presenting a real browser TLS+header fingerprint.
        try:
            api_resp = await page.context.request.get(
                api_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.sofascore.com/",
                    "Origin": "https://www.sofascore.com",
                },
                timeout=25000,
            )
            captured["status"] = api_resp.status
            if api_resp.status != 200:
                txt = (await api_resp.text())[:200]
                context.log.warning(f"sofascore api returned {api_resp.status}: {txt}")
                return
            body_text = await api_resp.text()
            if body_text:
                captured["json"] = json.loads(body_text)
        except Exception as e:
            context.log.warning(f"sofascore fetch failed: {e}")

    try:
        await crawler.run([home_url])  # the handler navigates internally
    except Exception as e:
        log.warning("sofascore_via_crawlee run failed: %s", e)
        return out

    data = captured.get("json") or {}
    for ev in (data.get("events") or [])[:120]:
        try:
            status = ev.get("status", {}) or {}
            stype = (status.get("type") or "").lower()
            tournament = ev.get("tournament", {}) or {}
            league_name = tournament.get("name") or ""
            cat_name = ((tournament.get("category") or {}) or {}).get("name", "")
            league = f"{league_name} - {cat_name}".strip(" -") if cat_name else league_name
            ts = ev.get("startTimestamp")
            kickoff_iso = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
            )
            out.append({
                "id": f"sofa-{ev.get('id')}",
                "source": "sofascore_crawlee",
                "league": league,
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
    log.info("sofascore_via_crawlee: %d events (status=%s)", len(out), captured.get("status"))
    return out


# ── Flashscore ───────────────────────────────────────────────────────────────
async def flashscore_via_crawlee() -> list[dict]:
    """Best-effort Flashscore scrape via Crawlee. Extracts visible match rows."""
    if not _CRAWLEE_OK:
        return []

    _reset_crawlee_state()

    out: list[dict] = []
    captured: dict[str, Any] = {"items": []}

    crawler = _build_crawler(request_handler_timeout_s=35)

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        try:
            await page.goto(
                "https://www.flashscore.com/football/",
                wait_until="domcontentloaded",
                timeout=25000,
            )
            await page.wait_for_timeout(3500)
            # Dismiss the OneTrust cookie banner so the JS hydrates fully
            try:
                el = await page.query_selector("#onetrust-accept-btn-handler")
                if el:
                    await el.click()
                    await page.wait_for_timeout(1500)
            except Exception:
                pass
            await page.wait_for_timeout(2500)

            data = await page.evaluate(
                """() => {
                    const items = [];
                    const matches = document.querySelectorAll('.event__match');
                    matches.forEach((m) => {
                        try {
                            const link = m.querySelector('a.eventRowLink');
                            const aria = link ? (link.getAttribute('aria-label') || '') : '';
                            // aria-label is e.g. "Bournemouth - Manchester City"
                            const parts = aria.split(' - ');
                            if (parts.length !== 2) return;
                            const home = parts[0].trim();
                            const away = parts[1].trim();
                            const cls = m.className || '';
                            const isLive = cls.includes('event__match--live');
                            const isFinished = cls.includes('event__match--scheduled') === false && !isLive;
                            // try to find a time / minute label
                            const timeEl = m.querySelector('.event__time, .event__stage');
                            const time = timeEl ? timeEl.textContent.trim() : null;
                            // best-effort scores (only present when started)
                            const homeScoreEl = m.querySelector('.event__score--home, .event__participant--home + .event__score');
                            const awayScoreEl = m.querySelector('.event__score--away');
                            items.push({
                                id: m.id || null,
                                href: link ? link.getAttribute('href') : null,
                                home, away,
                                isLive,
                                time,
                                homeScore: (function(){
                                    if (!homeScoreEl) return null;
                                    const n = parseInt(homeScoreEl.textContent.trim(), 10);
                                    return Number.isFinite(n) ? n : null;
                                })(),
                                awayScore: (function(){
                                    if (!awayScoreEl) return null;
                                    const n = parseInt(awayScoreEl.textContent.trim(), 10);
                                    return Number.isFinite(n) ? n : null;
                                })(),
                            });
                        } catch (e) {}
                    });
                    return items.slice(0, 200);
                }"""
            )
            captured["items"] = data or []
        except Exception as e:
            context.log.warning(f"flashscore fetch failed: {e}")

    try:
        await crawler.run(["https://www.flashscore.com/football/"])
    except Exception as e:
        log.warning("flashscore_via_crawlee run failed: %s", e)
        return out

    now_iso = datetime.now(timezone.utc).isoformat()
    for it in captured.get("items") or []:
        try:
            home = it.get("home")
            away = it.get("away")
            if not home or not away:
                continue
            # Python-side sanitization: convert any NaN/inf scores to None
            def _clean_score(v):
                if v is None:
                    return None
                try:
                    n = int(v)
                    return n if -1 < n < 1000 else None
                except (TypeError, ValueError):
                    return None
            match_id = it.get("id") or f"flash-{abs(hash(home + away)) % 10**9}"
            out.append({
                "id": f"flash-{match_id}",
                "source": "flashscore_crawlee",
                "league": "",
                "kickoff_iso": None,
                "status": it.get("time"),
                "is_live": bool(it.get("isLive")),
                "minute": it.get("time") if it.get("isLive") else None,
                "home_team": {
                    "name": home,
                    "score": _clean_score(it.get("homeScore")),
                },
                "away_team": {
                    "name": away,
                    "score": _clean_score(it.get("awayScore")),
                },
                "match_url": it.get("href"),
                "fetched_at": now_iso,
            })
        except Exception:
            continue
    log.info("flashscore_via_crawlee: %d items", len(out))
    return out


# Convenience smoke test (run: `python -m services.crawlee_scraper`)
if __name__ == "__main__":  # pragma: no cover
    async def _main():
        sofa = await sofascore_via_crawlee()
        print(f"Sofascore: {len(sofa)} events")
        if sofa:
            ev = sofa[0]
            print("  sample:", ev["home_team"]["name"], "vs", ev["away_team"]["name"], "—", ev["league"])
        flash = await flashscore_via_crawlee()
        print(f"Flashscore: {len(flash)} items")

    asyncio.run(_main())
