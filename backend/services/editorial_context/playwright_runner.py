"""Subprocess runner for the Playwright-based editorial fetcher (P4).

Mirrors `scrapy_runner.py` so callers can swap backends transparently.
  * Writes input JSON to a tempfile
  * Invokes `python -m services.editorial_context.playwright_main`
  * Reads output JSON file the subprocess wrote (fail-soft)
  * NEVER raises — returns `[]` on any error so the analyst engine can
    keep going.

The subprocess approach gives us:
  * Isolation: chromium crashes don't take down FastAPI
  * Predictable resource usage: process dies after each run
  * Compatibility: the same import-safe entrypoint works whether or not
    Playwright is installed in the parent (it just returns []).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from typing import Any, Optional

log = logging.getLogger("editorial.playwright_runner")

_PYTHON = sys.executable or "python"
_MODULE = "services.editorial_context.playwright_main"

# Default location used inside the container. The subprocess will only inherit
# this env var if the parent process is already setting it; we set a known
# default to avoid "Executable doesn't exist" errors when the path differs.
_DEFAULT_PW_BROWSERS_PATH = "/pw-browsers"


async def run_playwright(
    matches: list[dict],
    sources: list[dict],
    *,
    timeout_sec: float = 30.0,
    user_agent: Optional[str] = None,
    backend_root: str = "/app/backend",
) -> list[dict]:
    """Run the Playwright fetcher over a list of matches in a subprocess.

    Args:
        matches:     list of {sport, home, away, league, kickoff_iso, match_id}
        sources:     registry entries with `requires_js: True`
        timeout_sec: overall subprocess wall-clock cap
        user_agent:  optional override
        backend_root: cwd for the subprocess so the module import resolves

    Returns:
        list of raw item dicts (same shape as scrapy_runner output).
    """
    if not matches or not sources:
        return []
    js_sources = [s for s in sources if s.get("requires_js") and s.get("enabled")]
    if not js_sources:
        return []

    payload: dict[str, Any] = {
        "matches":     matches,
        "sources":     js_sources,
        "timeout_sec": float(timeout_sec),
    }
    if user_agent:
        payload["user_agent"] = user_agent

    in_fd, in_path = tempfile.mkstemp(suffix="_pw_in.json", prefix="edctx_")
    os.close(in_fd)
    out_fd, out_path = tempfile.mkstemp(suffix="_pw_out.json", prefix="edctx_")
    os.close(out_fd)

    env = dict(os.environ)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", _DEFAULT_PW_BROWSERS_PATH)

    try:
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        log.info(
            "[PLAYWRIGHT_EDITORIAL_START] matches=%d js_sources=%d timeout=%.0fs proxy=%s",
            len(matches), len(js_sources), timeout_sec,
            bool(env.get("PLAYWRIGHT_PROXY")),
        )

        proc = await asyncio.create_subprocess_exec(
            _PYTHON, "-m", _MODULE,
            "--input",  in_path,
            "--output", out_path,
            cwd=backend_root,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec + 10.0,
            )
        except asyncio.TimeoutError:
            log.warning(
                "[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] subprocess timeout (%.1fs) — killing",
                timeout_sec,
            )
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            stderr = b"timeout"

        try:
            with open(out_path, "r", encoding="utf-8") as f:
                items = json.load(f) or []
        except Exception:
            items = []

        if proc.returncode and proc.returncode != 0:
            log.warning(
                "[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] subprocess rc=%s stderr=%s",
                proc.returncode, (stderr or b"").decode(errors="ignore")[:500],
            )
        log.info("[PLAYWRIGHT_EDITORIAL_DONE] items=%d", len(items))
        return items
    except Exception as exc:
        log.warning("[PLAYWRIGHT_EDITORIAL_SOURCE_FAILED] runner exception: %s", exc)
        return []
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass


__all__ = ["run_playwright"]
