"""Sprint-D7-G · Pre-compute calibration diagnostic reports.

Generates JSON reports for each (market, scope) pair so the panel UI
can render them without re-running the backtest at request time.

Default scopes:
  * ``premier_2425``       — Premier League 2024/25 only.
  * ``top5_2425``          — Top-5 European leagues 2024/25.
  * ``premier_multiseason`` — Premier 21/22 … 24/25.

Default markets: ``OVER_2_5``, ``UNDER_2_5``, ``DRAW``.

Output: ``/app/diagnostics/calibration_<market>_<scope>.json``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                  # noqa: E402
from services.football_calibration_diagnostics import (                     # noqa: E402
    compute_calibration_diagnostics, classify_model_quality,
)
from services.football_historical_ingestor import parse_football_data_csv    # noqa: E402

log = logging.getLogger("d7_calibration_diagnostics")

CSV_DIR = Path("/app/data/football_data_co_uk")
OUT_DIR = Path("/app/diagnostics")

SCOPES: dict[str, list[tuple[str, str]]] = {
    "premier_2425":         [("E0_2425.csv",  "premier_24-25")],
    "top5_2425":            [
        ("E0_2425.csv",  "premier"),
        ("SP1_2425.csv", "la_liga"),
        ("I1_2425.csv",  "serie_a"),
        ("D1_2425.csv",  "bundesliga"),
        ("F1_2425.csv",  "ligue_1"),
    ],
    "premier_multiseason":  [
        ("E0_2122.csv",  "premier_21-22"),
        ("E0_2223.csv",  "premier_22-23"),
        ("E0_2324.csv",  "premier_23-24"),
        ("E0_2425.csv",  "premier_24-25"),
    ],
}
DEFAULT_MARKETS = ("OVER_2_5", "UNDER_2_5", "DRAW")


def _load_predictions(market: str, files: list[tuple[str, str]]) -> list[dict]:
    """Run walk-forward calibrated backtest with no edge gate so we
    collect ALL predictions (not just picks)."""
    all_preds: list[dict] = []
    for fn, comp in files:
        path = CSV_DIR / fn
        if not path.exists():
            log.warning("Missing CSV %s — skipping (%s)", fn, comp)
            continue
        matches = parse_football_data_csv(path.read_text(), competition=comp)
        bt = run_backtest(
            matches, market=market, no_market=False,
            use_calibration=True, walk_forward=True,
            shrinkage_K=50, min_edge_pp=0.0, min_pred_prob_pp=0.0,
        )
        # Annotate competition for traceability.
        for p in bt.get("predictions", []):
            p["competition"] = p.get("competition") or comp
        all_preds.extend(bt.get("predictions", []))
        log.info("[load] market=%s file=%s comp=%s n_pred=%d",
                  market, fn, comp, len(bt.get("predictions", [])))
    return all_preds


def main() -> int:
    p = argparse.ArgumentParser(
        description="Pre-compute calibration diagnostics (offline)")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS),
                    help="Coma-separados (default OVER_2_5,UNDER_2_5,DRAW)")
    p.add_argument("--scopes", default=",".join(SCOPES.keys()),
                    help=f"Coma-separados (default todos: {list(SCOPES)})")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    scopes  = [s.strip() for s in args.scopes.split(",")  if s.strip()]

    index: list[dict] = []
    for market in markets:
        for scope in scopes:
            if scope not in SCOPES:
                log.warning("Unknown scope %s — skipping", scope); continue
            preds = _load_predictions(market, SCOPES[scope])
            report = compute_calibration_diagnostics(
                preds, market=market, n_buckets=10,
            )
            report["scope"]   = scope
            report["verdict"] = classify_model_quality(report)
            fn = f"calibration_{market.lower()}_{scope}.json"
            (out_dir / fn).write_text(json.dumps(report, indent=2,
                                                    default=str))
            index.append({
                "market":     market,
                "scope":      scope,
                "n_records":  report["meta"]["n_records"],
                "auc":        report["discrimination"]["auc_model"],
                "brier_model":        report["model_vs_market"]["brier_model"],
                "brier_market_devig": report["model_vs_market"]["brier_market_devig"],
                "verdict":    report["verdict"]["tags"],
                "file":       fn,
            })
            log.info("[ok ] %s × %s → %s  (n=%d verdict=%s)",
                      market, scope, fn,
                      report["meta"]["n_records"],
                      report["verdict"]["tags"])

    (out_dir / "_index.json").write_text(json.dumps(index, indent=2,
                                                       default=str))
    log.info("Index written → %s/_index.json", out_dir)

    # Console table.
    print()
    print("=" * 110)
    print(f"{'Market':<10} | {'Scope':<22} | {'n':>5} | "
          f"{'AUC':>6} | {'Brier_m':>8} | {'Brier_d':>8} | {'Verdict'}")
    print("-" * 110)
    for r in index:
        auc = r['auc']
        print(f"{r['market']:<10} | {r['scope']:<22} | {r['n_records']:>5} | "
              f"{auc:>6.3f} | {r['brier_model']:>8.4f} | "
              f"{r['brier_market_devig']:>8.4f} | "
              f"{','.join(r['verdict'])}")
    print("=" * 110)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
