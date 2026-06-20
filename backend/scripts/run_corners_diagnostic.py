"""Sprint-D8/E PASO 1 · CLI runner for football corners diagnostic.

Instrumenta **ambos** endpoints de 365Scores con un game_id real,
imprime el diagnóstico por capas y persiste el JSON en
``/app/diagnostics/sprint_d8e_corners_diagnostic.json``.

Usage:
  # Con game_id directo:
  python scripts/run_corners_diagnostic.py --game-id 4123456 \\
      --gt-home 7 --gt-away 8 --gt-total 15

  # Con resolución por nombres + fecha (vía resolver de 365scores):
  python scripts/run_corners_diagnostic.py \\
      --home "Manchester United" --away "Fulham" --date 2024-08-16 \\
      --gt-home 7 --gt-away 8 --gt-total 15

Si scrape.do no está habilitado en este entorno, el script sale con
un mensaje claro sin gastar créditos.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.external_sources.score365_client import (              # noqa: E402
    fetch_game_stats, normalize_365scores_match_stats,
    resolve_game_id_by_date_and_names,
)
from services.external_sources.three65scores_live_fetchers import (  # noqa: E402
    fetch_game_detail,
)
from services.scrape_do_client import is_enabled                     # noqa: E402
from services.football_corners_diagnostic import (                   # noqa: E402
    diagnose_corners_pipeline,
)

log = logging.getLogger("d8e_paso1_corners_diag")

DEFAULT_OUT = Path("/app/diagnostics/sprint_d8e_corners_diagnostic.json")


async def _resolve_game_id(home: str, away: str, date_iso: str) -> str | None:
    try:
        return await resolve_game_id_by_date_and_names(home, away, date_iso)
    except Exception as exc:  # noqa: BLE001
        log.warning("game_id resolver raised: %s", exc)
        return None


# Wrappers so the diagnostic only receives ``fn(game_id) → Awaitable``.
async def _fetch_detail(game_id: str):
    return await fetch_game_detail(game_id)


async def _fetch_stats(game_id: str):
    # ``fetch_game_stats`` expects (client, game_id); client is unused.
    return await fetch_game_stats(None, game_id)


async def _async_main(args: argparse.Namespace) -> int:
    if not is_enabled():
        print("[corners_diag] scrape.do NOT enabled "
              "(SCRAPEDO_TOKEN missing). Aborting without burning credits.")
        return 3

    game_id = args.game_id
    if not game_id:
        if not (args.home and args.away and args.date):
            print("[corners_diag] need --game-id OR (--home --away --date)")
            return 2
        game_id = await _resolve_game_id(args.home, args.away, args.date)
        if not game_id:
            print(f"[corners_diag] could not resolve game_id "
                  f"for {args.home} vs {args.away} @ {args.date}")
            return 2
        print(f"[corners_diag] resolved game_id={game_id}")

    ground_truth = None
    if any(v is not None for v in (args.gt_home, args.gt_away, args.gt_total)):
        ground_truth = {
            "home_corners":  args.gt_home,
            "away_corners":  args.gt_away,
            "total_corners": args.gt_total,
        }

    out = await diagnose_corners_pipeline(
        game_id,
        fetch_detail=_fetch_detail,
        fetch_stats=_fetch_stats,
        normalizer=normalize_365scores_match_stats,
        ground_truth=ground_truth,
        timeout_s=float(args.timeout),
    )
    out["_meta"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sprint":           "D8E-PASO-1",
        "scrape_do_enabled": True,
    }

    out_path = Path(args.out) if args.out else DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str),
                        encoding="utf-8")

    # Console summary.
    print("=" * 64)
    print("Sprint-D8/E PASO 1 · Corners scraper diagnostic")
    print(f"game_id: {game_id}")
    print("=" * 64)
    for layer in ("transport", "endpoint", "parser"):
        print(f"\n--- LEVEL {layer.upper()} ---")
        for ep in ("detail", "stats"):
            block = out["layers"][layer][ep]
            print(f"  [{ep}] {json.dumps({k: v for k, v in block.items() if k not in ('paths','raw_stat_names')}, default=str)[:300]}")
    print("\n" + "=" * 64)
    print(f"VERDICT:           {out['verdict']}")
    print(f"WINNING ENDPOINT:  {out['winning_endpoint']}")
    print(f"REASON CODES:      {out['reason_codes']}")
    print(f"Output written to: {out_path}")
    print("=" * 64)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-id", help="365Scores numeric game id")
    parser.add_argument("--home", help="Home team name (for resolver)")
    parser.add_argument("--away", help="Away team name (for resolver)")
    parser.add_argument("--date", help="ISO date YYYY-MM-DD (for resolver)")
    parser.add_argument("--gt-home",  type=int, default=None,
                        help="Ground truth home corners")
    parser.add_argument("--gt-away",  type=int, default=None,
                        help="Ground truth away corners")
    parser.add_argument("--gt-total", type=int, default=None,
                        help="Ground truth total corners")
    parser.add_argument("--timeout", default=35,
                        help="Transport timeout in seconds")
    parser.add_argument("--out", default=None,
                        help="Output JSON path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_async_main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
