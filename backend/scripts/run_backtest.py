#!/usr/bin/env python3
"""Sprint-D · CLI runner for the football DRAW backtest framework.

Modes
-----
1) Market-aware (Sprint-D, default) — football-data.co.uk CSV with odds:
    python scripts/run_backtest.py \
        --csv-url https://www.football-data.co.uk/mmz4281/2324/E0.csv \
        --competition "Premier League 2023-24" \
        --market DRAW --walk-forward --use-calibration \
        --min-edge 4.0 --stake flat \
        --report-out /app/backtest_pl_2324_draw.json \
        --md-out /app/backtest_pl_2324_draw.md

2) No-market (Sprint-D2) — openfootball JSON (WC2022 / Euro2024):
    python scripts/run_backtest.py \
        --openfootball-path /tmp/worldcup.json \
        --competition "World Cup 2022" \
        --no-market --use-calibration --walk-forward \
        --min-pred-prob-pp 28.0 \
        --report-out /app/backtest_worldcup2022_draw.json \
        --md-out /app/backtest_worldcup2022_draw.md
"""
import argparse, asyncio, json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.football_historical_ingestor import (
    parse_football_data_csv, fetch_football_data_csv,
    parse_openfootball_json,
)
from services.football_backtest_engine import run_backtest
from services.football_backtest_metrics import compute_backtest_metrics


async def _load_matches(args) -> list[dict]:
    """Load matches from one of the supported sources."""
    if args.openfootball_path:
        with open(args.openfootball_path, "r", encoding="utf-8") as fh:
            data = fh.read()
        return parse_openfootball_json(data, competition=args.competition)
    if args.csv_path:
        with open(args.csv_path, "r", encoding="utf-8") as fh:
            csv_text = fh.read()
        return parse_football_data_csv(csv_text, competition=args.competition)
    if args.csv_url:
        csv_text = await fetch_football_data_csv(args.csv_url)
        return parse_football_data_csv(csv_text, competition=args.competition)
    raise SystemExit(
        "One of --openfootball-path, --csv-path, or --csv-url required."
    )


# ─────────────────────────────────────────────────────────────────────
# Market-aware Markdown rendering (Sprint-D)
# ─────────────────────────────────────────────────────────────────────
def _render_markdown_market(report: dict) -> str:
    lines: list[str] = []
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
              "roi_ci_low", "roi_ci_high",
              "is_significant", "is_roi_significant",
              "calibration_label",
              "sample_status", "small_sample_flag",
              "small_sample_warning"):
        add(f"- **{k}**: `{report.get(k)}`")
    # Sprint-D4 — emit warnings block.
    warnings = report.get("warnings") or []
    if warnings:
        add("\n## ⚠️  Warnings\n")
        for w in warnings:
            add(f"- `{w}`")
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
    sample_status = report.get("sample_status")
    if sample_status == "INSUFFICIENT_SAMPLE_DO_NOT_TRUST":
        add("\u26a0️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 picks.")
    elif sample_status == "SMALL_SAMPLE_CAUTION":
        add("🟡 **SMALL_SAMPLE_CAUTION** — between 50 and 200 picks; CI"
            " interpretation should remain qualitative.")
    if report.get("is_roi_significant") is True and (report.get("roi") or 0) > 0:
        add("✅ **APTO PARA PRODUCCIÓN (observe-only) — ROI positivo"
            " estadísticamente significativo (CI 95% excluye 0).**")
    elif report.get("is_roi_significant") is False and (report.get("roi") or 0) > 0:
        add("🟡 **ROI POSITIVO PERO NO SIGNIFICATIVO** — CI cruza 0;"
            " resultado nominalmente positivo no respaldado por la muestra.")
    elif report.get("is_significant") is False:
        add("⚠️  **REQUIERE MÁS DATOS** — CI cruza 0, no significativo.")
    else:
        add("❌ **NO APTO** — ROI negativo o no significativo.")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────
# No-market Markdown rendering (Sprint-D2)
# ─────────────────────────────────────────────────────────────────────
def _phase_section(title: str, m: dict) -> list[str]:
    out: list[str] = []
    out.append(f"### {title}")
    out.append(f"- n_predictions: `{m.get('n_predictions')}`")
    out.append(f"- n_draws: `{m.get('n_draws')}`")
    out.append(f"- draw_base_rate: `{m.get('draw_base_rate')}`")
    out.append(f"- brier_score: `{m.get('brier_score')}`  *(lower is better; "
               f"≤0.18 = decent for draw market)*")
    out.append(f"- log_loss: `{m.get('log_loss')}`")
    out.append(f"- calibration_label: `{m.get('calibration_label')}`")
    out.append("")
    out.append("**Calibration curve (predicted vs actual)**")
    out.append("")
    out.append("| Bucket | n | predicted_avg | actual_avg |")
    out.append("|---|---|---|---|")
    for b in m.get("reliability_curve", []):
        out.append(f"| {b['bucket']} | {b['n']} | {b['predicted_avg']} | {b['actual_avg']} |")
    out.append("")
    return out


def _label_table(title: str, label_dict: dict) -> list[str]:
    out: list[str] = []
    out.append(f"### {title}")
    out.append("| Label | n | won | hit_rate |")
    out.append("|---|---|---|---|")
    for k, v in (label_dict or {}).items():
        out.append(f"| {k} | {v['n']} | {v['won']} | {v['hit_rate']} |")
    out.append("")
    return out


def _render_markdown_no_market(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Football DRAW Backtest Report (No-Market)")
    add("")
    add(f"Generated: {datetime.utcnow().isoformat()}Z")
    add("")
    add("> ⚠️ This backtest runs on openfootball JSON (no odds available)."
        " ROI / yield are not reported. Metrics focus on model"
        " calibration (Brier + log-loss + reliability curve) and the"
        " hit-rate of fired labels.")
    add("")
    add("## Configuration")
    for k in ("market", "min_pred_prob_pp", "use_calibration",
              "walk_forward"):
        add(f"- `{k}` = `{report.get(k)}`")
    add("")
    add("## Sample size")
    add(f"- n_matches_total:  `{report.get('n_matches_total')}`")
    add(f"- n_predictions:    `{report.get('n_predictions')}`")
    add(f"- n_picks_fired:    `{report.get('n_picks_fired')}`")
    add(f"- n_won:            `{report.get('n_won')}`")
    add(f"- n_lost:           `{report.get('n_lost')}`")
    add(f"- hit_rate_fired:   `{report.get('hit_rate_fired')}`")
    add(f"- small_sample_flag: `{report.get('small_sample_flag')}`")
    add("")
    add("## Quantitative calibration metrics")
    add("")
    lines.extend(_phase_section("Group Stage",
                                report.get("group_stage_metrics") or {}))
    lines.extend(_phase_section("Knockout",
                                report.get("knockout_metrics") or {}))
    lines.extend(_phase_section("Combined",
                                report.get("combined_metrics") or {}))
    add("## Label hit-rate (only fired picks)")
    add("")
    lines.extend(_label_table("Group Stage",
                              report.get("label_hit_rate_group_stage") or {}))
    lines.extend(_label_table("Knockout",
                              report.get("label_hit_rate_knockout") or {}))
    lines.extend(_label_table("Combined",
                              report.get("label_hit_rate_combined") or {}))

    add("## Verdict")
    add("")
    if report.get("small_sample_flag"):
        add("⚠️  **INSUFFICIENT_SAMPLE_DO_NOT_TRUST** — fewer than 50 fired"
            " picks. Treat metrics as qualitative only.")
    else:
        comb = (report.get("combined_metrics") or {}).get("calibration_label")
        hr   = report.get("hit_rate_fired")
        if comb == "WELL_CALIBRATED" and (hr or 0) >= 0.30:
            add("✅ **MODEL CALIBRATED + LABEL HIT-RATE ABOVE BASELINE** —"
                " encouraging signal in national tournaments.")
        elif comb in ("WELL_CALIBRATED", "ACCEPTABLE_CALIBRATION"):
            add("🟡 **MODEL CALIBRATED but label hit-rate vs draw base-rate"
                " inconclusive.**")
        else:
            add("❌ **MISCALIBRATED** — model probabilities do not align"
                " with observed draw rate. Needs re-calibration.")
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
async def main():
    p = argparse.ArgumentParser()
    # Sources (mutually exclusive in practice; precedence:
    # openfootball-path > csv-path > csv-url).
    p.add_argument("--openfootball-path",
                   help="Path to an openfootball JSON file (Sprint-D2).")
    p.add_argument("--csv-path",
                   help="Path to a football-data.co.uk CSV file.")
    p.add_argument("--csv-url",
                   help="URL to a football-data.co.uk CSV file.")
    p.add_argument("--competition", default="")
    # Engine config.
    p.add_argument("--market", default="DRAW")
    p.add_argument("--min-edge", type=float, default=4.0)
    p.add_argument("--min-pred-prob-pp", type=float, default=30.0,
                   help="No-market mode: min predicted draw prob (in pp) "
                        "to fire a pick.")
    p.add_argument("--stake", default="flat",
                    choices=["flat", "kelly_fractional"])
    p.add_argument("--use-calibration", action="store_true")
    p.add_argument("--walk-forward", action="store_true")
    p.add_argument("--no-market", action="store_true",
                   help="Sprint-D2 mode for datasets without odds.")
    # Outputs.
    p.add_argument("--report-out", default="/app/backtest_report.json")
    p.add_argument("--md-out",     default="/app/backtest_report.md")
    args = p.parse_args()

    matches  = await _load_matches(args)
    result   = run_backtest(
        matches, market=args.market, min_edge_pp=args.min_edge,
        use_calibration=args.use_calibration,
        walk_forward=args.walk_forward, stake=args.stake,
        no_market=args.no_market,
        min_pred_prob_pp=args.min_pred_prob_pp,
    )
    metrics = compute_backtest_metrics(result)
    metrics["_picks_sample"] = (result["picks"] or [])[:30]
    metrics["_n_picks_dumped"] = min(30, len(result["picks"] or []))
    if args.no_market:
        metrics["_predictions_sample"] = (result.get("predictions") or [])[:50]
        metrics["_n_predictions_dumped"] = min(
            50, len(result.get("predictions") or [])
        )

    os.makedirs(os.path.dirname(args.report_out) or ".", exist_ok=True)
    with open(args.report_out, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2, default=str)
    with open(args.md_out, "w", encoding="utf-8") as fh:
        if args.no_market:
            fh.write(_render_markdown_no_market(metrics))
        else:
            fh.write(_render_markdown_market(metrics))
    print(f"✓ Wrote {args.report_out} and {args.md_out}")
    if args.no_market:
        print(f"   n_matches={result['n_matches_total']} "
              f"n_preds={metrics['n_predictions']} "
              f"n_picks={metrics['n_picks_fired']} "
              f"hit_rate_fired={metrics['hit_rate_fired']} "
              f"brier_combined={(metrics['combined_metrics'] or {}).get('brier_score')} "
              f"calibration={(metrics['combined_metrics'] or {}).get('calibration_label')}")
    else:
        print(f"   n_matches={result['n_matches_total']} n_picks={metrics['n_bets']} "
              f"roi={metrics['roi']} hit_rate={metrics['hit_rate']} "
              f"ci=[{metrics['roi_ci_lo']},{metrics['roi_ci_hi']}] "
              f"sig={metrics['is_significant']}")


if __name__ == "__main__":
    asyncio.run(main())
