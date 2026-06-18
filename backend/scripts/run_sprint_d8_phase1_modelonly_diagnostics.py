"""Sprint-D8 Fase 1 · UNDER_3_5 / OVER_3_5 model-only diagnostics.

Honest finding from the discovery: football-data.co.uk does **not**
publish 3.5 over/under odds in their main season CSVs (only 2.5).
The cached dataset therefore cannot support a full
``model_vs_market`` calibration report for the 3.5 line.

This script runs the **predictor + ground-truth** diagnostic anyway,
so we can answer the underlying question the spec asks ("does the
model itself carry any signal at the 3.5 line?") with the data we DO
have:

* Reliability curve of the model probabilities (10 buckets).
* Brier(model vs realised), log-loss, AUC.
* Sharpness (mean predicted prob).
* Hit rate per bucket with Wilson 95% CI.

Output: ``/app/diagnostics/calibration_<market>_<scope>_modelonly.json``.

What it does NOT compute (and why):
  * ``model_vs_market.brier_market_devig`` — needs market odds.
  * ``clv``                                — needs market open + close odds.
  * ``model_vs_market.delta_brier``        — needs market odds.

The verdict is therefore reported as
``MODEL_ONLY_INSUFFICIENT_FOR_EDGE_CHECK`` when AUC ≈ 0.50 — we cannot
prove edge OR refute it without market odds. When AUC > 0.55 the row is
flagged ``MODEL_DISCRIMINATES_BUT_MARKET_UNTESTED`` so a follow-up
sprint can chase an alternate odds source (Pinnacle CSV / The Odds
API historical for 3.5 if exposed).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                  # noqa: E402
from services.football_historical_ingestor import parse_football_data_csv    # noqa: E402

log = logging.getLogger("d8_phase1_modelonly")

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
DEFAULT_MARKETS = ("UNDER_3_5", "OVER_3_5")


# ── Pure helpers ─────────────────────────────────────────────────────
def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + (z**2) / n
    centre = (p + (z**2) / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + (z**2) / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def _log_loss(p: float, y: int, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1.0 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _auc(pairs: list[tuple[float, int]]) -> float:
    """Trapezoidal AUC over a list of (prob, label) pairs."""
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return float("nan")
    # Use rank-based formula (Mann-Whitney U).
    sorted_pairs = sorted(pairs, key=lambda x: x[0])
    ranks: dict[int, float] = {}
    n = len(sorted_pairs)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_pairs[j + 1][0] == sorted_pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[id(sorted_pairs[k])] = avg_rank
        i = j + 1
    sum_pos_ranks = 0.0
    for p, y in sorted_pairs:
        if y == 1:
            sum_pos_ranks += ranks[id((p, y))] if id((p, y)) in ranks else 0
    # Fall back to a simpler O(n²) AUC if id() bookkeeping fails
    # (Python re-creates the tuple each time, so ranks[] may miss).
    wins = ties = 0
    for p_pos in pos:
        for p_neg in neg:
            if p_pos > p_neg:
                wins += 1
            elif p_pos == p_neg:
                ties += 1
    total = len(pos) * len(neg)
    return (wins + 0.5 * ties) / total


def _reliability_curve(probs: list[float], labels: list[int],
                         n_buckets: int = 10) -> list[dict]:
    bins: list[dict] = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        bucket_idx = [k for k, p in enumerate(probs)
                      if (p >= lo and (p < hi or (i == n_buckets - 1 and p <= hi)))]
        if not bucket_idx:
            bins.append({"bucket": [round(lo, 2), round(hi, 2)],
                          "n": 0, "mean_pred": None,
                          "hit_rate": None, "ci": [None, None]})
            continue
        bps = [probs[k] for k in bucket_idx]
        bys = [labels[k] for k in bucket_idx]
        n = len(bys)
        hits = sum(bys)
        lo_ci, hi_ci = _wilson_ci(hits, n)
        bins.append({
            "bucket":     [round(lo, 2), round(hi, 2)],
            "n":          n,
            "mean_pred":  round(sum(bps) / n, 4),
            "hit_rate":   round(hits / n, 4),
            "ci":         [round(lo_ci, 4), round(hi_ci, 4)],
        })
    return bins


def _classify(auc: float, brier_model: float, n: int) -> list[str]:
    tags = []
    if math.isnan(auc):
        return ["INSUFFICIENT_VARIANCE"]
    if auc >= 0.60:
        tags.append("MODEL_DISCRIMINATES_STRONG")
    elif auc >= 0.55:
        tags.append("MODEL_DISCRIMINATES_MODEST")
    elif auc >= 0.52:
        tags.append("MODEL_DISCRIMINATES_WEAK")
    else:
        tags.append("MODEL_DOES_NOT_DISCRIMINATE")
    tags.append("MODEL_ONLY_INSUFFICIENT_FOR_EDGE_CHECK")  # no market odds
    if n < 100:
        tags.append("SMALL_SAMPLE")
    return tags


def _diagnostics(preds: list[dict], market: str) -> dict:
    probs   = [float(p["predicted_prob"]) for p in preds]
    labels  = [int(bool(p["hit"])) for p in preds]
    n       = len(preds)
    if n == 0:
        return {
            "market": market, "n_records": 0,
            "auc_model": None, "brier_model": None, "log_loss_model": None,
            "sharpness": None,
            "reliability_curve": [],
            "verdict_tags": ["NO_PREDICTIONS"],
        }
    brier   = sum(_brier(p, y) for p, y in zip(probs, labels)) / n
    ll      = sum(_log_loss(p, y) for p, y in zip(probs, labels)) / n
    pairs   = list(zip(probs, labels))
    auc     = _auc(pairs)
    sharpness = sum(probs) / n
    curve   = _reliability_curve(probs, labels)
    return {
        "market":             market,
        "n_records":          n,
        "base_rate":          round(sum(labels) / n, 4),
        "auc_model":          round(auc, 4) if not math.isnan(auc) else None,
        "brier_model":        round(brier, 5),
        "log_loss_model":     round(ll, 5),
        "sharpness":          round(sharpness, 4),
        "reliability_curve":  curve,
        "verdict_tags":       _classify(auc, brier, n),
        # Honest constraint flag.
        "market_data_available": False,
        "constraint_reason": (
            "football-data.co.uk does not publish 3.5 over/under odds "
            "in the cached season CSVs. Model-only diagnostics shown."
        ),
    }


def _load_predictions(market: str, files: list[tuple[str, str]]) -> list[dict]:
    """Run no_market mode (no odds needed) and collect all predictions."""
    all_preds: list[dict] = []
    for fn, comp in files:
        path = CSV_DIR / fn
        if not path.exists():
            log.warning("Missing CSV %s — skipping (%s)", fn, comp)
            continue
        matches = parse_football_data_csv(path.read_text(), competition=comp)
        bt = run_backtest(
            matches, market=market, no_market=True,
            use_calibration=True, walk_forward=True,
            shrinkage_K=50, min_pred_prob_pp=0.0,
        )
        for p in bt.get("predictions", []):
            p["competition"] = p.get("competition") or comp
        all_preds.extend(bt.get("predictions", []))
        log.info("[load] market=%s file=%s comp=%s n_pred=%d",
                  market, fn, comp, len(bt.get("predictions", [])))
    return all_preds


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    p.add_argument("--scopes",  default=",".join(SCOPES.keys()))
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
                log.warning("Unknown scope %s — skipping", scope)
                continue
            preds = _load_predictions(market, SCOPES[scope])
            report = _diagnostics(preds, market=market)
            report["scope"] = scope
            fn = f"calibration_{market.lower()}_{scope}_modelonly.json"
            (out_dir / fn).write_text(json.dumps(report, indent=2,
                                                    default=str))
            index.append({
                "market":     market,
                "scope":      scope,
                "n_records":  report["n_records"],
                "base_rate":  report.get("base_rate"),
                "auc":        report.get("auc_model"),
                "brier":      report.get("brier_model"),
                "sharpness":  report.get("sharpness"),
                "verdict":    report["verdict_tags"],
                "file":       fn,
            })
            log.info("[ok ] %s × %s → %s  (n=%d auc=%s verdict=%s)",
                      market, scope, fn, report["n_records"],
                      report.get("auc_model"), report["verdict_tags"])

    print()
    print("=" * 120)
    print(f"{'Market':<10} | {'Scope':<22} | {'n':>5} | {'base':>6} "
          f"| {'AUC':>6} | {'Brier':>8} | {'Sharp':>6} | Verdict")
    print("-" * 120)
    for r in index:
        auc = r['auc']
        br  = r['brier']
        sh  = r['sharpness']
        base = r['base_rate']
        print(
            f"{r['market']:<10} | {r['scope']:<22} | {r['n_records']:>5} | "
            f"{base if base is not None else '   nan':>6} | "
            f"{auc if auc is not None else '   nan':>6} | "
            f"{br  if br  is not None else '  nan   ':>8} | "
            f"{sh  if sh  is not None else '  nan':>6} | "
            f"{','.join(r['verdict'])}"
        )
    print("=" * 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
