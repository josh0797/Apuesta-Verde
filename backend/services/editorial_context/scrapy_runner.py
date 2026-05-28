"""Subprocess runner for the Scrapy editorial spider.

The runner is fail-soft by design:
    • returns an empty list on any failure (process error, timeout, JSON parse)
    • NEVER raises
    • logs warnings/errors using the [SCRAPY_EDITORIAL_*] log convention

Design:
    • We write input to a tempfile, invoke `python -m services.editorial_context.
      editorial_spider_main` as a child process, read the output tempfile.
    • Subprocess isolation guarantees Twisted reactor doesn't touch FastAPI's
      asyncio loop.
    • Configurable timeout. Default 25s for an analysis run; the caller
      should also pass a tighter `per_match_timeout` if running in parallel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from typing import Any, Optional

log = logging.getLogger("editorial.scrapy_runner")

_PYTHON = sys.executable or "python"
_MODULE = "services.editorial_context.editorial_spider_main"


async def run_scrapy(
    matches: list[dict],
    sources: list[dict],
    *,
    timeout_sec: float = 25.0,
    user_agent: Optional[str] = None,
    backend_root: str = "/app/backend",
) -> list[dict]:
    """Run the Scrapy spider over a list of matches.

    Args:
        matches: list of {sport, home, away, league, kickoff_iso, match_id}
        sources: registry entries (full dicts, not just names)
        timeout_sec: overall subprocess wall-clock cap.
        user_agent: optional override.
        backend_root: cwd for the subprocess (so the module import resolves).

    Returns:
        list of raw item dicts (see editorial_spider_main for the shape).
        Empty list on any failure.
    """
    if not matches or not sources:
        return []

    payload: dict[str, Any] = {
        "matches":     matches,
        "sources":     sources,
        "timeout_sec": float(timeout_sec),
    }
    if user_agent:
        payload["user_agent"] = user_agent

    in_fd, in_path = tempfile.mkstemp(suffix="_edctx_in.json", prefix="edctx_")
    os.close(in_fd)
    out_fd, out_path = tempfile.mkstemp(suffix="_edctx_out.json", prefix="edctx_")
    os.close(out_fd)

    try:
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        log.info("[SCRAPY_EDITORIAL_START] matches=%d sources=%d timeout=%.0fs",
                 len(matches), len(sources), timeout_sec)

        proc = await asyncio.create_subprocess_exec(
            _PYTHON, "-m", _MODULE,
            "--input", in_path,
            "--output", out_path,
            cwd=backend_root,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec + 5.0,    # hard wall-clock
            )
        except asyncio.TimeoutError:
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] subprocess timeout (%.1fs) — killing",
                        timeout_sec)
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            stderr = b"timeout"

        # Try to read whatever output was written (spider persists in `closed`).
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                items = json.load(f) or []
        except Exception:
            items = []

        if proc.returncode and proc.returncode != 0:
            log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] subprocess rc=%s stderr=%s",
                        proc.returncode, (stderr or b"").decode(errors="ignore")[:500])

        log.info("[SCRAPY_EDITORIAL_DONE] items=%d", len(items))
        return items
    except Exception as exc:
        log.warning("[SCRAPY_EDITORIAL_SOURCE_FAILED] runner exception: %s", exc)
        return []
    finally:
        for p in (in_path, out_path):
            try:
                os.unlink(p)
            except Exception:
                pass


__all__ = ["run_scrapy"]
