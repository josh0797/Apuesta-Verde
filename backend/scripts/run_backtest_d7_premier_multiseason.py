"""Sprint-D7-E · Multi-season Premier League sanity check (DRAW market).

Corre el backtest sobre las últimas 4 temporadas de Premier League
(21/22, 22/23, 23/24, 24/25) con un panel de thresholds para
determinar si la señal observada en 24/25 (+27.96% ROI a edge=4pp)
es robusta o pertenece al ruido.

Hipótesis nula: si la señal es ruido, el ROI medio across temporadas
colapsa cerca de cero y la varianza inter-temporada es grande.

Output: tabla `season × threshold` con ROI, n_picks, hit_rate, plus
ROI promedio y desviación inter-temporada.

Offline, 0 créditos, ``observe_only``.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                  # noqa: E402
from services.football_backtest_metrics import compute_backtest_metrics      # noqa: E402
from services.football_historical_ingestor import parse_football_data_csv    # noqa: E402

log = logging.getLogger("d7_premier_multiseason")

SEASONS = [
    ("E0_2122.csv", "2021-22"),
    ("E0_2223.csv", "2022-23"),
    ("E0_2324.csv", "2023-24"),
    ("E0_2425.csv", "2024-25"),
]
CSV_DIR = Path("/app/data/football_data_co_uk")
DEFAULT_THRESHOLDS = (2.0, 3.0, 4.0, 5.0, 6.0)


def run_one(matches: list[dict], threshold_pp: float) -> dict:
    bt = run_backtest(
        matches, market="DRAW", no_market=False,
        use_calibration=True, walk_forward=True,
        shrinkage_K=50, min_pred_prob_pp=8.0,
        min_edge_pp=threshold_pp,
    )
    m = compute_backtest_metrics(bt)
    return {
        "n_matches":   len(matches),
        "n_picks":     len(bt.get("picks", [])),
        "n_bets":      m.get("n_bets"),
        "roi":         m.get("roi"),
        "hit_rate":    m.get("hit_rate"),
        "roi_ci_low":  m.get("roi_ci_low"),
        "roi_ci_high": m.get("roi_ci_high"),
        "sample_status": m.get("sample_status"),
        "is_roi_significant": m.get("is_roi_significant"),
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sprint-D7-E Premier multi-season (offline)")
    p.add_argument("--thresholds", type=str,
                    default=",".join(str(t) for t in DEFAULT_THRESHOLDS))
    p.add_argument("--out", default="/app/backtest_d7_premier_multiseason.json")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    thresholds = tuple(float(x.strip()) for x in args.thresholds.split(",")
                        if x.strip())

    # Load all seasons.
    seasons_data: dict[str, list[dict]] = {}
    for fn, label in SEASONS:
        path = CSV_DIR / fn
        if not path.exists():
            log.warning("Missing CSV %s — skipping", path); continue
        matches = parse_football_data_csv(path.read_text(),
                                            competition=f"premier_{label}")
        seasons_data[label] = matches
        log.info("[load] %s n_matches=%d", label, len(matches))

    # Build sweep.
    table: dict[str, dict[float, dict]] = {}
    for season, matches in seasons_data.items():
        table[season] = {}
        for t in thresholds:
            r = run_one(matches, t)
            table[season][t] = r
            log.info("[%s @ %.1fpp] n_picks=%d roi=%s hit=%s sig=%s",
                      season, t, r["n_picks"], r["roi"],
                      r["hit_rate"], r["is_roi_significant"])

    # Aggregate per threshold across seasons (unweighted ROI mean +
    # weighted by n_bets).
    summary_per_threshold: list[dict] = []
    for t in thresholds:
        rois = [table[s][t]["roi"] for s in table
                 if table[s][t]["roi"] is not None]
        hits = [table[s][t]["hit_rate"] for s in table
                 if table[s][t]["hit_rate"] is not None]
        nbets = [(table[s][t]["n_bets"] or 0) for s in table]
        weighted_roi = None
        if sum(nbets) > 0:
            weighted_roi = sum(
                (table[s][t]["roi"] or 0) * (table[s][t]["n_bets"] or 0)
                for s in table
            ) / sum(nbets)
        summary_per_threshold.append({
            "threshold_pp":          t,
            "roi_mean_across_seasons": (statistics.mean(rois)
                                          if rois else None),
            "roi_stdev_across_seasons": (statistics.stdev(rois)
                                           if len(rois) > 1 else None),
            "roi_weighted_across_seasons": weighted_roi,
            "hit_rate_mean": (statistics.mean(hits) if hits else None),
            "n_seasons_with_picks": len(rois),
            "total_n_bets":  sum(nbets),
        })

    report = {
        "seasons":              list(seasons_data.keys()),
        "thresholds":           list(thresholds),
        "per_season_threshold": table,
        "summary_per_threshold": summary_per_threshold,
        "observe_only":         True,
    }
    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    log.info("Multi-season Premier report written → %s", args.out)

    # Pretty-print.
    print()
    print(f"{'Premier League · DRAW · 4 seasons (2021-22 … 2024-25)':^96}")
    print("=" * 96)
    hdr = "{:>10} | ".format("Season")
    for t in thresholds:
        hdr += "{:>11}".format(f"edge≥{t:.1f}pp")
    print(hdr)
    print("-" * 96)
    for season in table:
        row = "{:>10} | ".format(season)
        for t in thresholds:
            roi = table[season][t]["roi"]
            row += "{:>11}".format("{:+.2%}".format(roi)
                                       if roi is not None else "n/a")
        print(row)
    print("-" * 96)
    # Summary row.
    row_avg  = "{:>10} | ".format("MEAN")
    row_std  = "{:>10} | ".format("STDEV")
    row_wgt  = "{:>10} | ".format("WEIGHTED")
    for s in summary_per_threshold:
        m = s["roi_mean_across_seasons"]
        st = s["roi_stdev_across_seasons"]
        wt = s["roi_weighted_across_seasons"]
        row_avg += "{:>11}".format("{:+.2%}".format(m) if m is not None else "n/a")
        row_std += "{:>11}".format("{:.2%}".format(st) if st is not None else "n/a")
        row_wgt += "{:>11}".format("{:+.2%}".format(wt) if wt is not None else "n/a")
    print(row_avg)
    print(row_std)
    print(row_wgt)
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
