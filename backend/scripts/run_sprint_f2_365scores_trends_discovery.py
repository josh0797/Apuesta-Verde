"""Sprint F.2 — 365Scores Top Trends endpoint discovery (timeboxed).

Runs Chromium headless against the public match page of Mexico vs
South Korea (game_id=4627854, FIFA World Cup 2026 group stage),
intercepts every fetch/XHR request and every response body, and saves
a structured discovery report into
``/app/diagnostics/sprint_f2_365scores_trends_discovery.json``.

Heuristics
----------
* Capture every request whose URL or POST body contains any of the
  signal tokens: ``4627854``, ``5106``, ``2383``, ``trend``,
  ``insight``, ``pre-game``, ``preGame``, ``topTrend``, ``tendencia``.
* Capture every response body of those signal requests (truncated to
  8 KB per body).
* Walk ``__NEXT_DATA__`` and any ``window.__INITIAL_STATE__`` blob in
  the rendered HTML for keys matching the same signal tokens.
* Strict timebox: ``--timeout-ms`` (default 75s) after which we close
  the browser regardless of completion.

The script is **read-only** and never writes scraped trends into
Mongo. Discovery is purely diagnostic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

log = logging.getLogger("f2_discovery")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)

DEFAULT_URL = (
    "https://www.365scores.com/football/match/"
    "fifa-world-cup-5930/mexico-south-korea-2383-5106-5930"
)

SIGNAL_TOKENS = (
    "4627854",
    "5106",
    "2383",
    "trend",
    "Trend",
    "insight",
    "Insight",
    "pre-game",
    "preGame",
    "PreGame",
    "tendencia",
    "Tendencia",
    "topTrend",
    "TopTrend",
)

# Regex for embedded JSON blobs.
RX_NEXT_DATA = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(?P<json>.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
RX_INITIAL_STATE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(?P<json>\{.*?\})\s*[;\n]",
    re.DOTALL,
)


def _matches_signal(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    return [tok for tok in SIGNAL_TOKENS if tok in text]


def _truncate(s: str, n: int = 8192) -> str:
    if not isinstance(s, str):
        return ""
    return s if len(s) <= n else (s[:n] + f"\n…[truncated {len(s)-n} chars]")


async def _walk_json_for_signals(node: Any, path: str = "$", depth: int = 0,
                                   limit: int = 25) -> list[dict]:
    """DFS over a JSON document collecting paths whose key contains a
    signal token."""
    out: list[dict] = []
    if depth > limit:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            hits = _matches_signal(k)
            if hits:
                preview = ""
                if isinstance(v, (dict, list)):
                    preview = _truncate(json.dumps(v, ensure_ascii=False), 1024)
                else:
                    preview = _truncate(str(v), 1024)
                out.append({"path": f"{path}.{k}", "hits": hits,
                             "value_preview": preview,
                             "value_type": type(v).__name__})
            out.extend(await _walk_json_for_signals(v, f"{path}.{k}",
                                                     depth + 1, limit))
    elif isinstance(node, list):
        for i, item in enumerate(node[:50]):
            out.extend(await _walk_json_for_signals(item, f"{path}[{i}]",
                                                     depth + 1, limit))
    return out


async def discover(match_url: str, *, timeout_ms: int,
                    out_path: Path) -> dict:
    captured_requests: list[dict] = []
    captured_responses: list[dict] = []
    embedded_signals: list[dict] = []
    final_status: dict[str, Any] = {}

    async with async_playwright() as pw:
        # We use chromium (not headless_shell only) to be safe with
        # JS-heavy SPAs. The container ships chromium_headless_shell.
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="es-MX",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # ── REQUEST listener ───────────────────────────────────────────
        def on_request(req):  # noqa: ANN001
            url = req.url
            post_data = req.post_data or ""
            hits_url = _matches_signal(url)
            hits_body = _matches_signal(post_data) if post_data else []
            if hits_url or hits_body:
                captured_requests.append({
                    "url":             url,
                    "method":          req.method,
                    "resource_type":   req.resource_type,
                    "headers":         {k: v for k, v in req.headers.items()
                                         if k.lower() in (
                                             "accept", "accept-language",
                                             "content-type", "origin",
                                             "referer", "x-app-id",
                                             "x-language-id")},
                    "post_data":       _truncate(post_data, 2048) if post_data else None,
                    "signal_tokens_url":  hits_url,
                    "signal_tokens_body": hits_body,
                    "timestamp":       datetime.now(timezone.utc).isoformat(),
                })

        page.on("request", on_request)

        # ── RESPONSE listener (async via task) ────────────────────────
        pending_resp_tasks: list[asyncio.Task] = []

        async def _capture_response(resp):  # noqa: ANN001
            try:
                url = resp.url
                hits = _matches_signal(url)
                if not hits:
                    return
                ct = (resp.headers.get("content-type") or "").lower()
                status = resp.status
                # Only attempt to read body when it's JSON/text/HTML and
                # status looks success-ish (or 304).
                body_snippet = None
                json_signal_paths: list[dict] = []
                if status < 400 and any(t in ct for t in ("json", "text",
                                                            "html", "javascript")):
                    try:
                        text = await resp.text()
                        body_snippet = _truncate(text, 4096)
                        # Try parse JSON and walk for signal keys.
                        if "json" in ct:
                            try:
                                parsed = json.loads(text)
                                json_signal_paths = await _walk_json_for_signals(parsed)
                            except (ValueError, TypeError):
                                pass
                    except Exception as exc:  # noqa: BLE001
                        body_snippet = f"<<read failed: {exc}>>"
                captured_responses.append({
                    "url":                url,
                    "status":             status,
                    "content_type":       ct,
                    "signal_tokens_url":  hits,
                    "body_snippet":       body_snippet,
                    "json_signal_paths":  json_signal_paths[:30],
                    "timestamp":          datetime.now(timezone.utc).isoformat(),
                })
            except Exception as exc:  # noqa: BLE001
                log.warning("response capture exception: %s", exc)

        def on_response(resp):  # noqa: ANN001
            task = asyncio.create_task(_capture_response(resp))
            pending_resp_tasks.append(task)

        page.on("response", on_response)

        # ── Navigate ──────────────────────────────────────────────────
        try:
            response = await page.goto(
                match_url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            final_status["initial_status"] = response.status if response else None
        except Exception as exc:  # noqa: BLE001
            final_status["initial_status"] = None
            final_status["nav_error"] = str(exc)
            log.warning("page.goto raised: %s", exc)

        # Give the SPA time to make its XHRs.
        try:
            await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 30000))
        except Exception as exc:  # noqa: BLE001
            log.info("networkidle wait timed out: %s", exc)

        # Try to scroll a bit — some trends are lazy-loaded.
        try:
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(2)
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(2)
        except Exception:  # noqa: BLE001
            pass

        # Wait for any final XHRs.
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending_resp_tasks, return_exceptions=True),
                timeout=20.0,
            )
        except Exception:  # noqa: BLE001
            pass

        # ── Pull __NEXT_DATA__ / __INITIAL_STATE__ ─────────────────────
        try:
            html = await page.content()
            final_status["html_length"] = len(html)
            # Quick token presence check on raw HTML.
            html_hits = _matches_signal(html)
            final_status["html_signal_tokens_present"] = sorted(set(html_hits))

            m_next = RX_NEXT_DATA.search(html)
            if m_next:
                try:
                    next_data = json.loads(m_next.group("json"))
                    paths = await _walk_json_for_signals(next_data,
                                                          path="$.__NEXT_DATA__")
                    embedded_signals.append({
                        "source":     "__NEXT_DATA__",
                        "found_keys": [p["path"] for p in paths][:50],
                        "details":    paths[:30],
                    })
                except (ValueError, TypeError) as exc:
                    embedded_signals.append({"source": "__NEXT_DATA__",
                                              "parse_error": str(exc)})
            else:
                embedded_signals.append({"source": "__NEXT_DATA__",
                                          "found": False})

            m_init = RX_INITIAL_STATE.search(html)
            if m_init:
                try:
                    init_state = json.loads(m_init.group("json"))
                    paths = await _walk_json_for_signals(
                        init_state, path="$.__INITIAL_STATE__",
                    )
                    embedded_signals.append({
                        "source":     "__INITIAL_STATE__",
                        "found_keys": [p["path"] for p in paths][:50],
                        "details":    paths[:30],
                    })
                except (ValueError, TypeError) as exc:
                    embedded_signals.append({"source": "__INITIAL_STATE__",
                                              "parse_error": str(exc)})
            else:
                embedded_signals.append({"source": "__INITIAL_STATE__",
                                          "found": False})
        except Exception as exc:  # noqa: BLE001
            final_status["html_capture_error"] = str(exc)

        await context.close()
        await browser.close()

    # ── Build the discovery report ────────────────────────────────────
    report = {
        "engine":            "sprint_f2_365scores_top_trends_discovery",
        "target_url":        match_url,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "signal_tokens":     list(SIGNAL_TOKENS),
        "final_status":      final_status,
        "captured_requests": captured_requests,
        "captured_responses": captured_responses,
        "embedded_signals":  embedded_signals,
        "summary": {
            "n_requests_captured":  len(captured_requests),
            "n_responses_captured": len(captured_responses),
            "n_embedded_signals":   sum(
                1 for e in embedded_signals
                if e.get("found_keys")
            ),
            "endpoint_candidates": sorted({
                r["url"].split("?")[0]
                for r in captured_requests
                if "trend" in r["url"].lower()
                or "insight" in r["url"].lower()
                or "topTrend" in r["url"]
                or "preGame" in r["url"]
            }),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                    default=str))
    log.info("Discovery report saved to %s", out_path)
    log.info("Requests captured: %d, Responses with signal: %d, "
             "Embedded signal sources: %d",
             len(captured_requests), len(captured_responses),
             report["summary"]["n_embedded_signals"])
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout-ms", type=int, default=75000)
    parser.add_argument(
        "--out",
        default="/app/diagnostics/sprint_f2_365scores_trends_discovery.json",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    report = asyncio.run(discover(args.url, timeout_ms=args.timeout_ms,
                                    out_path=out_path))
    cands = report["summary"]["endpoint_candidates"]
    if cands:
        print("\nENDPOINT CANDIDATES:")
        for c in cands:
            print(f"  - {c}")
    else:
        print("\nNo trend/insight endpoint candidates captured.")
    sys.exit(0 if cands else 0)  # never error out; report is the deliverable


if __name__ == "__main__":
    main()
