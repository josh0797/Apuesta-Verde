"""Sprint-D7-E/F · Barrido honesto de ``min_edge_pp`` (multi-market).

Corre el backtest doméstico (5 ligas, temporada 2024/25) sobre una
lista de thresholds (por defecto ``{2, 3, 4, 5, 6, 8}``) para un
mercado configurable (``DRAW`` por defecto; también ``OVER_2_5``,
``UNDER_2_5``) y produce una tabla agregada con:

* n_picks por liga y por threshold
* ROI por liga y por threshold
* ROI agregado (weighted por ``n_bets``) por threshold
* hit_rate agregado (weighted) por threshold

Objetivo del barrido: determinar si el ROI agregado es **estable** a
través de thresholds — la firma de una señal real — o **errático** —
la firma del ruido.

Este script es **offline**, NO consume créditos de The Odds API y
preserva ``observe_only``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                  # noqa: E402
from services.football_backtest_metrics import compute_backtest_metrics      # noqa: E402
from services.football_historical_ingestor import parse_football_data_csv    # noqa: E402

log = logging.getLogger("d7_threshold_sweep")

LEAGUES_2425 = [
    ("E0",  "premier_league"),
    ("SP1", "la_liga"),
    ("I1",  "serie_a"),
    ("D1",  "bundesliga"),
    ("F1",  "ligue_1"),
]
CSV_CACHE_DIR = Path("/app/data/football_data_co_uk")
DEFAULT_THRESHOLDS = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)


def _weighted_avg(rows: list[dict], key: str) -> float | None:
    num = 0.0
    den = 0
    for r in rows:
        v = r.get(key)
        n = r.get("n_bets") or 0
        if v is None or n <= 0:
            continue
        num += float(v) * n
        den += n
    return (num / den) if den > 0 else None


def run_one_threshold(matches_by_league: dict[str, list[dict]],
                       threshold_pp: float, *,
                       market: str = "DRAW") -> dict:
    per_league: dict[str, dict] = {}
    rows: list[dict] = []
    for label, matches in matches_by_league.items():
        bt = run_backtest(
            matches, market=market, no_market=False,
            use_calibration=True, walk_forward=True,
            shrinkage_K=50, min_pred_prob_pp=8.0,
            min_edge_pp=threshold_pp,
        )
        metrics = compute_backtest_metrics(bt)
        per_league[label] = {
            "n_matches": len(matches),
            "n_picks":   len(bt.get("picks", [])),
            "n_bets":    metrics.get("n_bets"),
            "n_won":     metrics.get("n_won"),
            "roi":       metrics.get("roi"),
            "hit_rate":  metrics.get("hit_rate"),
            "roi_ci_low":  metrics.get("roi_ci_low"),
            "roi_ci_high": metrics.get("roi_ci_high"),
            "sample_status": metrics.get("sample_status"),
        }
        rows.append(metrics)
    aggregate = {
        "n_bets_total":      sum((r.get("n_bets") or 0) for r in rows),
        "weighted_roi":      _weighted_avg(rows, "roi"),
        "weighted_hit_rate": _weighted_avg(rows, "hit_rate"),
    }
    return {"threshold_pp": threshold_pp,
            "market":       market,
            "per_league":  per_league,
            "aggregate":   aggregate}


def load_matches() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for code, label in LEAGUES_2425:
        path = CSV_CACHE_DIR / f"{code}_2425.csv"
        if not path.exists():
            log.warning("Missing CSV for %s (%s) — skipping.", label, path)
            continue
        text = path.read_text()
        matches = parse_football_data_csv(text, competition=label)
        out[label] = matches
        log.info("[load] %s n_matches=%d", label, len(matches))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sprint-D7-E/F barrido de thresholds (offline, observe_only)",
    )
    p.add_argument("--market", default="DRAW",
                    choices=["DRAW", "OVER_2_5", "UNDER_2_5"],
                    help="Mercado a evaluar (default DRAW).")
    p.add_argument("--thresholds", type=str,
                    default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
                    help="Coma-separados (ej: '2,3,4,5,6,8').")
    p.add_argument("--out", default=None,
                    help=("Salida JSON. Si se omite, "
                          "/app/backtest_d7_threshold_sweep_<market>.json"))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    out = args.out or f"/app/backtest_d7_threshold_sweep_{args.market.lower()}.json"
    thresholds = tuple(float(x.strip()) for x in args.thresholds.split(",")
                        if x.strip())
    matches_by_league = load_matches()

    sweep: list[dict] = []
    for t in thresholds:
        log.info("--- market=%s threshold = %.1f pp ---", args.market, t)
        res = run_one_threshold(matches_by_league, t, market=args.market)
        log.info("aggregate: n_bets=%d weighted_roi=%s weighted_hit=%s",
                  res["aggregate"]["n_bets_total"],
                  res["aggregate"]["weighted_roi"],
                  res["aggregate"]["weighted_hit_rate"])
        sweep.append(res)

    report = {
        "market":             args.market,
        "thresholds":         list(thresholds),
        "leagues":            list(matches_by_league.keys()),
        "sweep":              sweep,
        "observe_only":       True,
    }
    Path(out).write_text(json.dumps(report, indent=2, default=str))
    log.info("Threshold sweep written → %s", out)

    # Pretty-print summary table to stdout.
    print()
    print("=" * 92)
    title = f"{args.market} threshold sweep · top-5 leagues 2024/25"
    print(f"{title:^92}")
    print("-" * 92)
    print(f"{'edge_pp':>8} | {'n_bets':>7} | {'w_ROI':>8} | {'w_hit_rate':>11} | "
          f"{'roi_min(per_lg)':>17} | {'roi_max(per_lg)':>17}")
    print("-" * 92)
    for r in sweep:
        agg = r["aggregate"]
        rois = [v.get("roi") for v in r["per_league"].values()
                 if v.get("roi") is not None]
        roi_min = min(rois) if rois else None
        roi_max = max(rois) if rois else None
        def _fmt(x, w=8):
            return ("{:+.3f}".format(x) if isinstance(x, (int, float))
                     else "  n/a   ").rjust(w)
        print(f"{r['threshold_pp']:>8.1f} | {agg['n_bets_total']:>7d} | "
              f"{_fmt(agg['weighted_roi'])} | "
              f"{_fmt(agg['weighted_hit_rate'], 11)} | "
              f"{_fmt(roi_min, 17)} | {_fmt(roi_max, 17)}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
