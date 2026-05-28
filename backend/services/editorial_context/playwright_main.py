"""Standalone Playwright entry point used by `playwright_runner.py`.

Mirrors `editorial_spider_main.py` but for JS-rendered sources. Reads the
request payload from `--input`, writes raw items to `--output`.

Usage (manual):
    python -m services.editorial_context.playwright_main \\
        --input  /tmp/pw_in.json \\
        --output /tmp/pw_out.json

Input JSON shape: see `playwright_runner.run_playwright`.

The entrypoint is fail-soft — it ALWAYS writes the output file, even when
empty, so the caller can read it without race-condition guards.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
log = logging.getLogger("playwright_main")


async def _run(input_path: str, output_path: str) -> int:
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        log.error("failed to read input: %s", exc)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        return 1

    items: list[dict] = []
    try:
        from .playwright_fetcher import fetch_with_playwright
        items = await fetch_with_playwright(
            matches=payload.get("matches") or [],
            sources=payload.get("sources") or [],
            timeout_sec=float(payload.get("timeout_sec") or 30.0),
            user_agent=payload.get("user_agent"),
        )
    except Exception as exc:
        log.warning("playwright fetch failed: %s", exc)
        items = []

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        log.info("[PLAYWRIGHT_EDITORIAL_DONE] reason=finished items=%d output=%s",
                 len(items), output_path)
    except Exception as exc:
        log.error("failed to write output: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args.input, args.output)))
