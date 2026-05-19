"""Playwright-based scraper for sites that block server IPs via Cloudflare/JS.

Uses headless Chromium with stealth tweaks (User-Agent override, no automation flag,
realistic Accept-Language, viewport). For aggressive anti-bot like Cloudflare Turnstile
this may still fail; we return empty list and let the caller fall back to other sources.

Public entry-points:
  - sofascore_via_playwright(date_iso=None) -> list[dict]
  - flashscore_via_playwright()           -> list[dict]

NOTE: Browser launch is expensive (~2s) and rate-limited by site CAPTCHA cooldowns.
We lazy-import playwright so the rest of the app loads even if playwright is missing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("playwright_scraper")

UA_REAL = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _new_context(p):
    """Create a stealth-ish browser context."""
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=UA_REAL,
        locale="en-US",
        viewport={"width": 1366, "height": 768},
        java_script_enabled=True,
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    # Strip webdriver flag
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
        "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
    )
    return browser, context


async def sofascore_via_playwright(date_iso: str | None = None, timeout_ms: int = 25000) -> list[dict]:
    """Fetch Sofascore scheduled events for date_iso (defaults to today) via headless browser.

    Strategy: Navigate the homepage first to warm cf_clearance cookie, then navigate
    DIRECTLY to the JSON endpoint (which renders as JSON text in the browser).
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        log.warning("playwright not installed; skipping Sofascore PW")
        return []

    if date_iso is None:
        date_iso = datetime.now(timezone.utc).date().isoformat()

    api_url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{date_iso}"
    out: list[dict] = []
    async with async_playwright() as p:
        browser, context = await _new_context(p)
        try:
            page = await context.new_page()
            # Warm-up: visit homepage so CF issues a clearance cookie
            try:
                await page.goto("https://www.sofascore.com/", wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(2500)
            except Exception as e:
                log.warning("Sofascore PW warmup failed: %s", e)
            # Navigate directly to API JSON
            try:
                resp = await page.goto(api_url, wait_until="domcontentloaded", timeout=timeout_ms)
                if resp and resp.status >= 400:
                    log.warning("Sofascore PW API returned %s", resp.status)
                    return out
                body_text = await page.evaluate("() => document.body && document.body.innerText")
                data = json.loads(body_text) if body_text else {}
            except Exception as e:
                log.warning("Sofascore PW navigation/parse failed: %s", e)
                return out

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
                        "source": "sofascore_pw",
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
        except Exception as e:
            log.warning("sofascore_via_playwright failed: %s", e)
        finally:
            await context.close()
            await browser.close()
    return out


async def flashscore_via_playwright(timeout_ms: int = 18000) -> list[dict]:
    """Best-effort Flashscore scrape via headless browser. Limited data — IDs of matches only."""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return []
    out: list[dict] = []
    async with async_playwright() as p:
        browser, context = await _new_context(p)
        try:
            page = await context.new_page()
            await page.goto("https://www.flashscore.com/football/", wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2500)
            # Flashscore renders matches inside divs with specific classes; capture team names
            data = await page.evaluate(
                """() => {
                    const items = [];
                    document.querySelectorAll('.event__match').forEach((el) => {
                        try {
                            const home = el.querySelector('.event__participant--home');
                            const away = el.querySelector('.event__participant--away');
                            const time = el.querySelector('.event__time');
                            if (home && away) items.push({
                                home: home.textContent.trim(),
                                away: away.textContent.trim(),
                                time: time ? time.textContent.trim() : null,
                            });
                        } catch (e) {}
                    });
                    return items.slice(0, 60);
                }"""
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            for it in data or []:
                out.append({
                    "id": f"flash-{abs(hash(it['home'] + it['away'])) % 10**9}",
                    "source": "flashscore_pw",
                    "league": "",
                    "kickoff_iso": None,
                    "status": it.get("time"),
                    "is_live": False,
                    "minute": None,
                    "home_team": {"name": it["home"]},
                    "away_team": {"name": it["away"]},
                    "fetched_at": now_iso,
                })
        except Exception as e:
            log.warning("flashscore_via_playwright failed: %s", e)
        finally:
            await context.close()
            await browser.close()
    return out
