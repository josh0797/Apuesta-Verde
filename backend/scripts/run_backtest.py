#!/usr/bin/env python3
"""Sprint-D · CLI runner for the football DRAW backtest framework.

Example:
  python scripts/run_backtest.py \
      --csv-url https://www.football-data.co.uk/mmz4281/2324/E0.csv \
      --competition "Premier League 2023-24" \
      --market DRAW \
      --walk-forward --use-calibration \
      --min-edge 4.0 --stake flat \
      --report-out /app/backtest_pl_2324_draw.json
"""
import argparse, asyncio, json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.football_historical_ingestor import (
    parse_football_data_csv, fetch_football_data_csv,
)
from services.football_backtest_engine import run_backtest
from services.football_backtest_metrics import compute_backtest_metrics


async def _load_csv(args) -> str:
    if args.csv_path:
        with open(args.csv_path, "r", encoding="utf-8") as fh:
            return fh.read()
    if args.csv_url:
        return await fetch_football_data_csv(args.csv_url)
    raise SystemExit("--csv-path or --csv-url required")


def _render_markdown(report: dict) -> str:
    lines = []
    add = lines.append
    add("# Football DRAW Backtest Report\n")
    add(f"Generated: {datetime.utcnow().isoformat()}Z\n")
    add("## Configuration\n")
    for k in ("market", "min_edge_pp", "stake_mode", "use_calibration",
              "walk_forward"):
        add(f"- `{k}` = `{report.get(k)}`")
    add("\n## Core results\n")
    for k in ("n_matches_total", "n_bets", "n_won", "n_lost", "hit_rate",
              "total_staked", "total_returned", "net_pnl", "roi",
              "yield_per_bet", "max_drawdown", "sharpe_like",
              "avg_edge_predicted_pp", "avg_edge_realised_pp",
              "roi_ci_lo", "roi_ci_hi", "is_significant",
              "calibration_label", "small_sample_flag",
              "small_sample_warning"):
        add(f"- **{k}**: `{report.get(k)}`")
    add("\n## Reliability curve (predicted vs actual)\n")
    add("| Bucket | n | predicted_avg | actual_avg |")
    add("|---|---|---|---|")
    for b in report.get("reliability_curve", []):
        add(f"| {b['bucket']} | {b['n']} | {b['predicted_avg']} | {b['actual_avg']} |")
    add("\n## Breakdown by edge bucket\n")
    add("| Bucket | n | won | roi | hit_rate |")
    add("|---|---|---|---|---|")
    for k, v in (report.get("breakdown_by_edge_bucket") or {}).items():
        add(f"| {k} | {v['n']} | {v['won']} | {v['roi']} | {v['hit_rate']} |")
    add("\n## Breakdown by tier\n")
    add("| Tier | n | won | roi | hit_rate |")
    add("|---|---|---|---|---|")
    for k, v in (report.get("breakdown_by_tier") or {}).items():
        add(f"| {k} | {v['n']} | {v['won']} | {v['roi']} | {v['hit_rate']} |")
    add("\n## Verdict\n")
    if report.get("small_sample_flag"):
        add("\u26a0️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 picks.")
    elif report.get("is_significant") is True and (report.get("roi") or 0) > 0:
        add("✅ **APTO PARA PRODUCCIÓN (observe-only) — ROI positivo "
            "estadísticamente significativo.**")
    elif report.get("is_significant") is False:
        add("⚠️  **REQUIERE MÁS DATOS** — CI cruza 0, no significativo.")
    else:
        add("❌ **NO APTO** — ROI negativo o no significativo.")
    return "\n".join(lines) + "\n"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv-path")
    p.add_argument("--csv-url")
    p.add_argument("--competition", default="")
    p.add_argument("--market", default="DRAW")
    p.add_argument("--min-edge", type=float, default=4.0)
    p.add_argument("--stake", default="flat",
                    choices=["flat", "kelly_fractional"])
    p.add_argument("--use-calibration", action="store_true")
    p.add_argument("--walk-forward", action="store_true")
    p.add_argument("--report-out", default="/app/backtest_report.json")
    p.add_argument("--md-out",     default="/app/backtest_report.md")
    args = p.parse_args()

    csv_text = await _load_csv(args)
    matches  = parse_football_data_csv(csv_text, competition=args.competition)
    result   = run_backtest(
        matches, market=args.market, min_edge_pp=args.min_edge,
        use_calibration=args.use_calibration,
        walk_forward=args.walk_forward, stake=args.stake,
    )
    metrics = compute_backtest_metrics(result)
    metrics["_picks_sample"] = result["picks"][:30]   # first 30 for audit
    metrics["_n_picks_dumped"] = min(30, len(result["picks"]))

    os.makedirs(os.path.dirname(args.report_out), exist_ok=True)
    with open(args.report_out, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2, default=str)
    with open(args.md_out, "w", encoding="utf-8") as fh:
        fh.write(_render_markdown(metrics))
    print(f"✓ Wrote {args.report_out} and {args.md_out}")
    print(f"   n_matches={result['n_matches_total']} n_picks={metrics['n_bets']} "
          f"roi={metrics['roi']} hit_rate={metrics['hit_rate']} "
          f"ci=[{metrics['roi_ci_lo']},{metrics['roi_ci_hi']}] "
          f"sig={metrics['is_significant']}")


if __name__ == "__main__":
    asyncio.run(main())
