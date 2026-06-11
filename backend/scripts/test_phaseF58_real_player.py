"""Phase F58 — Real player StatMuse parsing smoke test.

Executes ``hydrate_player_stats`` against a small set of well-known
Premier League players and prints the resulting payload so we can
audit:

  * Whether StatMuse returns parseable HTML in this environment.
  * Whether the parser extracts shots / SOT / passes / tackles / minutes.
  * Whether the FBref enrichment fires (requires Bright Data Web Unlocker
    — without it, FBref returns 403 from datacenter IPs).
  * Whether the Understat fallback fires when both fail.
  * Whether the ingestor degrades fail-soft when everything fails.

Findings from the 2026-06 run (current environment, no Bright Data)::

    StatMuse:  200 OK → shots_p90 / sot_p90 / minutes_sample populated.
    FBref:     403 Forbidden (anti-bot blocks direct datacenter GETs).
    Result:    source="statmuse", passes/tackles/fouls/cards/xg = null.

To unlock the full FBref enrichment in production set ``BRIGHTDATA_API_KEY``
and ``BRIGHTDATA_CUSTOMER_ID`` (see ``services/external_sources/base.py``).

Usage::

    cd /app/backend
    python -m scripts.test_phaseF58_real_player

Output is human-readable; exit code is 0 even when sources fail
(intentional — this is a diagnostic, not a regression test).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Make the parent /app/backend importable when the script is invoked
# directly (``python scripts/test_phaseF58_real_player.py``).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("phaseF58_real_player_probe")


PLAYERS = [
    {"name": "Erling Haaland",  "team": "Manchester City",   "league": "EPL"},
    {"name": "Bukayo Saka",     "team": "Arsenal",           "league": "EPL"},
    {"name": "Mohamed Salah",   "team": "Liverpool",         "league": "EPL"},
]


def _short(obj, max_len: int = 500) -> str:
    """Pretty-print + truncate for terminal readability."""
    try:
        s = json.dumps(obj, default=str, indent=2, ensure_ascii=False)
    except Exception:
        s = repr(obj)
    return s if len(s) <= max_len else s[:max_len] + " …<truncated>"


async def probe_one(player: dict) -> dict:
    from services.football_player_stats_ingestor import (
        hydrate_player_stats, cache_clear,
    )
    cache_clear()
    t0 = time.perf_counter()
    try:
        payload = await hydrate_player_stats(
            player_name=player["name"],
            team=player.get("team"),
            league=player.get("league"),
            use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "player":     player["name"],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "exception":  repr(exc),
        }
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "player":             player["name"],
        "team":               player.get("team"),
        "league":             player.get("league"),
        "elapsed_ms":         elapsed_ms,
        "available":          payload.get("available"),
        "source":             payload.get("source"),
        "confidence_penalty": payload.get("confidence_penalty"),
        "minutes_sample":     payload.get("minutes_sample"),
        "stats":              payload.get("stats"),
        "raw_reason":         (payload.get("raw") or {}).get("_reason"),
    }


def _summarise(results: list[dict]) -> dict:
    sources = {}
    available = 0
    failed = 0
    for r in results:
        src = r.get("source") or "exception"
        sources[src] = sources.get(src, 0) + 1
        if r.get("available"):
            available += 1
        else:
            failed += 1
    return {
        "players_total":   len(results),
        "available_count": available,
        "failed_count":    failed,
        "by_source":       sources,
    }


async def main() -> int:
    log.info("Phase F58 real-player probe starting — %d players", len(PLAYERS))
    results = []
    for p in PLAYERS:
        log.info("Probing %s (%s, %s)…", p["name"], p.get("team"), p.get("league"))
        r = await probe_one(p)
        results.append(r)
        log.info("→ result for %s:\n%s", p["name"], _short(r, max_len=1500))

    summary = _summarise(results)
    log.info("=" * 60)
    log.info("SUMMARY:\n%s", _short(summary, max_len=600))
    log.info("=" * 60)

    # Persist results to a JSON file for auditing.
    out_dir = ROOT / "scripts" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "phaseF58_real_player_probe.json"
    out_file.write_text(
        json.dumps(
            {"results": results, "summary": summary},
            default=str, indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info("Wrote %s", out_file)
    # Always return 0 — diagnostic, not a regression test.
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
