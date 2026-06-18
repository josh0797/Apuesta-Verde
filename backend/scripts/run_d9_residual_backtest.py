"""Sprint-D9.1 · Residual model backtest — MVP.

Trains the offset logistic regression with the ALREADY-EXISTING
features plus the new ``goal_minus_xg_l15`` overperformance, and
benchmarks it against:

  (a) the de-vigged market opening prior — primary baseline.
  (b) the Dixon-Coles + Bayesian-shrunk current production
      predictor — secondary baseline (the model currently in place).

Disciplines enforced
--------------------
* Strict point-in-time walk-forward by chronological cut:
    - Per-season scope (top5_2425): 60% train / 40% holdout per league.
    - Multi-season scope (premier_multiseason): train on 21/22+22/23+
      23/24, evaluate on 24/25.
* No future data leakage (the standardiser fits on train only).
* Per-league training when sample ≥ MIN_PER_LEAGUE_TRAIN; falls back
  to pooled / global model otherwise.
* OVER and UNDER share the SAME canonical model (target y = total
  goals ≥ 3) — P_UNDER = 1 − P_OVER.
* All three probability series are persisted side-by-side:
    p_dc_original  p_market_devig  p_residual_final
* Bootstrap 1000 of the delta-Brier and delta-LogLoss between
  residual and market_devig to test whether the improvement (if any)
  is distinguishable from sampling noise.
* Result classification follows the rubric the user provided:
    RESIDUAL_BEATS_MARKET_OUT_OF_SAMPLE
    RESIDUAL_IMPROVES_CALIBRATION_ONLY
    NO_INCREMENTAL_SIGNAL_WITH_CURRENT_FEATURES
    RESIDUAL_OVERFIT_DETECTED
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                  # noqa: E402
from services.football_calibration_diagnostics import (                     # noqa: E402
    compute_calibration_diagnostics,
)
from services.football_historical_ingestor import (                         # noqa: E402
    parse_football_data_csv, build_point_in_time_features,
)
from services.football_residual_model import (                              # noqa: E402
    fit_residual_model, identity_model,
)

log = logging.getLogger("d9_residual_backtest")

CSV_DIR = Path("/app/data/football_data_co_uk")
OUT_DIR = Path("/app/diagnostics")

# Residual model features (use only ones already point-in-time safe).
FEATURE_NAMES: list[str] = [
    "elo_home", "elo_away",
    "xg_home_l5", "xg_away_l5",
    "goal_avg_against_home", "goal_avg_against_away",
    "goal_minus_xg_home_l15", "goal_minus_xg_away_l15",   # ← new (D9.1)
]
MIN_PER_LEAGUE_TRAIN = 120
SPLIT_RATIO = 0.60       # for single-season per-league scope
LAMBDA_L2_DEFAULT = 2.0


def _gather_records(market: str, files: list[tuple[str, str]]) -> list[dict]:
    """Run the engine in market-aware mode (no edge gate) and pair each
    prediction with its raw feature vector + the new D9.1 feature.

    The engine returns ``predictions[]`` with ``predicted_prob`` (the
    DC original probability) and ``market_implied_devig``. We then
    rebuild the per-row feature vector by re-invoking
    ``build_point_in_time_features`` so we can train the residual
    model on the SAME data the engine saw.
    """
    out: list[dict] = []
    for fn, comp in files:
        path = CSV_DIR / fn
        if not path.exists():
            log.warning("Missing CSV %s — skipping", fn); continue
        matches = parse_football_data_csv(path.read_text(), competition=comp)
        bt = run_backtest(
            matches, market=market, no_market=False,
            use_calibration=True, walk_forward=True,
            shrinkage_K=50, min_edge_pp=0.0, min_pred_prob_pp=0.0,
        )
        preds = bt.get("predictions", [])
        # Build a date → match-index map.
        idx_by_date = {(m["date"].isoformat(),
                         m["home_team"], m["away_team"]): i
                         for i, m in enumerate(matches)}
        for p in preds:
            key = (p["date"], p["home"], p["away"])
            mi = idx_by_date.get(key)
            if mi is None:
                continue
            feats = build_point_in_time_features(matches, mi)
            row = [feats.get(k) for k in FEATURE_NAMES]
            mk_devig = p.get("market_implied_devig")
            if mk_devig is None:
                continue
            out.append({
                "date":             p["date"],
                "competition":      comp,
                "home":             p["home"],
                "away":             p["away"],
                "match_index":      mi,
                "hit":              bool(p["hit"]),
                "p_dc_original":    float(p["predicted_prob"]),
                "p_market_devig":   float(mk_devig),
                "p_market_devig_close":
                    p.get("market_implied_devig_close"),
                "raw_features":     row,
                "odd":              p.get("odd"),
                "odd_open":         p.get("odd_open"),
                "odd_close":        p.get("odd_close"),
                # Echo through the engine's market-implied raw for
                # downstream diagnostics.
                "market_implied_raw":         p.get("market_implied_raw"),
                "market_implied_raw_close":
                    p.get("market_implied_raw_close"),
            })
        log.info("[gather] %s: n=%d", comp, len(preds))
    out.sort(key=lambda r: r["date"])
    return out


def _bootstrap_paired_delta(a: list[float], b: list[float],
                              y: list[int], metric: str, n_boot: int = 1000,
                              seed: int = 17,
                              ) -> dict:
    """Paired bootstrap of (metric(a) − metric(b)) with CI95.
    ``metric`` ∈ ``{"brier", "logloss"}``.
    """
    rng = random.Random(seed)
    n = len(a)
    if n < 30:
        return {"n": n, "ci_low": None, "ci_high": None,
                "p_below_zero": None, "median": None}

    def _score(p, t):
        if metric == "brier":
            return (p - t) ** 2
        p = max(1e-6, min(1 - 1e-6, p))
        return -(t * math.log(p) + (1 - t) * math.log(1 - p))

    diffs_per_idx = [(_score(a[i], y[i]) - _score(b[i], y[i])) for i in range(n)]
    boot: list[float] = []
    for _ in range(n_boot):
        sample_idx = [rng.randint(0, n - 1) for _ in range(n)]
        s = sum(diffs_per_idx[k] for k in sample_idx) / n
        boot.append(s)
    boot.sort()
    lo = boot[int(0.025 * n_boot)]
    hi = boot[int(0.975 * n_boot)]
    med = boot[n_boot // 2]
    p_neg = sum(1 for x in boot if x < 0) / n_boot
    return {"n": n, "n_boot": n_boot,
            "ci_low": round(lo, 6), "ci_high": round(hi, 6),
            "median": round(med, 6), "p_below_zero": round(p_neg, 4)}


def _train_holdout_per_league(records: list[dict], lambda_l2: float
                                 ) -> tuple[list[dict], dict]:
    """Per-league chronological split. Returns predictions on holdout +
    a per-league training audit."""
    out: list[dict] = []
    audit: dict[str, dict] = {}
    by_comp: dict[str, list[dict]] = {}
    for r in records:
        by_comp.setdefault(r["competition"], []).append(r)
    for comp, rs in by_comp.items():
        rs_sorted = sorted(rs, key=lambda r: r["date"])
        n = len(rs_sorted)
        cut = int(n * SPLIT_RATIO)
        train_rs = rs_sorted[:cut]
        hold_rs  = rs_sorted[cut:]
        if cut < MIN_PER_LEAGUE_TRAIN:
            audit[comp] = {
                "n_train": cut, "n_holdout": len(hold_rs),
                "reason_code": "PER_LEAGUE_TRAIN_INSUFFICIENT",
                "model_used": "IDENTITY",
            }
            model = identity_model(FEATURE_NAMES)
        else:
            model = fit_residual_model(
                raw_rows=[r["raw_features"] for r in train_rs],
                targets=[int(r["hit"]) for r in train_rs],
                market_devig=[r["p_market_devig"] for r in train_rs],
                feature_names=FEATURE_NAMES,
                lambda_l2=lambda_l2,
            )
            audit[comp] = {
                "n_train": cut, "n_holdout": len(hold_rs),
                "reason_code": ("CONVERGED" if model.converged
                                  else "HIT_MAX_ITER"),
                "n_iter_used":  model.n_iter_used,
                "final_loss":   round(model.final_loss, 6),
                "weights":      [round(w, 4) for w in model.weights],
                "bias":         round(model.bias, 4),
                "train_brier_model":   round(model.train_brier_model, 6),
                "train_brier_market":  round(model.train_brier_market, 6),
                "delta_train_brier":   round(model.train_brier_model
                                                - model.train_brier_market, 6),
            }
        for r in hold_rs:
            p_final = model.predict(r["raw_features"], r["p_market_devig"])
            out.append({**r, "p_residual_final": p_final})
    return out, audit


def _train_holdout_multiseason(records: list[dict], holdout_season: str,
                                 lambda_l2: float,
                                 ) -> tuple[list[dict], dict]:
    """Train on seasons != holdout_season; evaluate on holdout_season.

    Season is encoded in ``competition`` (e.g. ``premier_21-22``).
    """
    train_rs = [r for r in records if not r["competition"].endswith(holdout_season)]
    hold_rs  = [r for r in records if r["competition"].endswith(holdout_season)]
    audit: dict = {"holdout_season": holdout_season,
                    "n_train": len(train_rs), "n_holdout": len(hold_rs)}
    if len(train_rs) < MIN_PER_LEAGUE_TRAIN:
        model = identity_model(FEATURE_NAMES)
        audit["reason_code"] = "MULTI_SEASON_TRAIN_INSUFFICIENT"
    else:
        model = fit_residual_model(
            raw_rows=[r["raw_features"] for r in train_rs],
            targets=[int(r["hit"]) for r in train_rs],
            market_devig=[r["p_market_devig"] for r in train_rs],
            feature_names=FEATURE_NAMES,
            lambda_l2=lambda_l2,
        )
        audit.update({
            "reason_code":  ("CONVERGED" if model.converged
                              else "HIT_MAX_ITER"),
            "n_iter_used": model.n_iter_used,
            "final_loss":  round(model.final_loss, 6),
            "weights":     [round(w, 4) for w in model.weights],
            "bias":        round(model.bias, 4),
            "train_brier_model":   round(model.train_brier_model, 6),
            "train_brier_market":  round(model.train_brier_market, 6),
            "delta_train_brier":   round(model.train_brier_model
                                            - model.train_brier_market, 6),
        })
    out = [{**r, "p_residual_final":
            model.predict(r["raw_features"], r["p_market_devig"])}
            for r in hold_rs]
    return out, {"pooled": audit}


def _diagnostics_for(preds: list[dict], which: str) -> dict:
    """Build a calibration diagnostics report substituting
    ``predicted_prob`` with the requested probability source."""
    recs = [{"predicted_prob":         p[which],
              "hit":                    p["hit"],
              "market_implied_raw":     p.get("market_implied_raw"),
              "market_implied_devig":   p.get("p_market_devig"),
              "odd_open":               p.get("odd_open"),
              "odd_close":              p.get("odd_close"),
              "market_implied_raw_close":
                  p.get("market_implied_raw_close"),
              "market_implied_devig_close":
                  p.get("p_market_devig_close"),
              "fired":                  False}
             for p in preds]
    return compute_calibration_diagnostics(recs, market=which)


def _classify(diag_resid: dict, diag_market: dict, diag_dc: dict,
                boot_brier: dict, train_audit: dict) -> dict:
    """Apply the rubric requested by the user."""
    tags: list[str] = []
    br_r = diag_resid["model_vs_market"]["brier_model"]
    br_d = diag_resid["model_vs_market"]["brier_market_devig"]
    ll_r = diag_resid["model_vs_market"]["logloss_model"]
    ll_d = diag_resid["model_vs_market"]["logloss_market_devig"]
    cal  = diag_resid["calibration"]
    # Overfit signal: residual model is much better on train than holdout.
    overfit_signal = False
    if isinstance(train_audit, dict):
        # Iterate possible nesting.
        leaves = []
        if "pooled" in train_audit:
            leaves = [train_audit["pooled"]]
        else:
            leaves = list(train_audit.values())
        for leaf in leaves:
            if not isinstance(leaf, dict):
                continue
            dt = leaf.get("delta_train_brier")
            if dt is not None and dt < -0.01 \
                    and br_r is not None and br_d is not None \
                    and (br_r - br_d) > 0.005:
                overfit_signal = True
                break
    # Decide.
    boot_p = boot_brier.get("p_below_zero")
    if (br_r is not None and br_d is not None
            and br_r < br_d
            and ll_r is not None and ll_d is not None and ll_r < ll_d
            and boot_p is not None and boot_p >= 0.95):
        tags.append("RESIDUAL_BEATS_MARKET_OUT_OF_SAMPLE")
    elif (cal.get("slope") is not None
            and 0.85 <= cal["slope"] <= 1.15
            and abs(cal.get("intercept") or 0) < 0.05
            and br_r is not None and br_d is not None
            and br_r >= br_d):
        tags.append("RESIDUAL_IMPROVES_CALIBRATION_ONLY")
    else:
        tags.append("NO_INCREMENTAL_SIGNAL_WITH_CURRENT_FEATURES")
    if overfit_signal:
        tags.append("RESIDUAL_OVERFIT_DETECTED")
    return {"tags": tags}


def _run_one(scope_name: str, files: list[tuple[str, str]],
              market: str, *, lambda_l2: float,
              out_dir: Path, multi_season_holdout: str = None) -> dict:
    log.info("=== scope=%s market=%s lambda=%s ===",
              scope_name, market, lambda_l2)
    records = _gather_records(market, files)
    if not records:
        return {"scope": scope_name, "market": market,
                 "reason_code": "NO_RECORDS"}
    if multi_season_holdout:
        preds, train_audit = _train_holdout_multiseason(
            records, multi_season_holdout, lambda_l2,
        )
    else:
        preds, train_audit = _train_holdout_per_league(records, lambda_l2)
    if not preds:
        return {"scope": scope_name, "market": market,
                 "reason_code": "EMPTY_HOLDOUT"}
    # Diagnostics on the residual model.
    diag_resid  = _diagnostics_for(preds, "p_residual_final")
    diag_market = _diagnostics_for(preds, "p_market_devig")
    diag_dc     = _diagnostics_for(preds, "p_dc_original")
    # Bootstrap the delta-Brier residual vs market.
    boot_brier   = _bootstrap_paired_delta(
        [p["p_residual_final"] for p in preds],
        [p["p_market_devig"]   for p in preds],
        [int(p["hit"]) for p in preds], "brier",
    )
    boot_logloss = _bootstrap_paired_delta(
        [p["p_residual_final"] for p in preds],
        [p["p_market_devig"]   for p in preds],
        [int(p["hit"]) for p in preds], "logloss",
    )
    verdict = _classify(diag_resid, diag_market, diag_dc, boot_brier,
                          train_audit)
    report = {
        "scope":           scope_name,
        "market":          market,
        "n_holdout":       len(preds),
        "lambda_l2":       lambda_l2,
        "feature_names":   FEATURE_NAMES,
        "train_audit":     train_audit,
        "diag_residual":   diag_resid,
        "diag_market_devig": diag_market,
        "diag_dc_original":  diag_dc,
        "bootstrap_brier_resid_vs_market":   boot_brier,
        "bootstrap_logloss_resid_vs_market": boot_logloss,
        "verdict":         verdict,
        "observe_only":    True,
        # Persist the per-row triplet for downstream UI plotting.
        "rows": [
            {"date": p["date"], "competition": p["competition"],
             "home": p["home"], "away": p["away"],
             "hit": p["hit"],
             "p_dc_original":     round(p["p_dc_original"], 6),
             "p_market_devig":    round(p["p_market_devig"], 6),
             "p_residual_final":  round(p["p_residual_final"], 6)}
            for p in preds
        ],
    }
    out_path = out_dir / f"residual_d9_1_{market.lower()}_{scope_name}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    log.info("written → %s (verdict=%s)", out_path, verdict["tags"])
    return report


def main() -> int:
    p = argparse.ArgumentParser(
        description="D9.1 — Residual model backtest (offline)")
    p.add_argument("--markets", default="OVER_2_5",
                    help="Coma-separados (default OVER_2_5).")
    p.add_argument("--lambda-l2", type=float, default=LAMBDA_L2_DEFAULT)
    p.add_argument("--out-dir", default=str(OUT_DIR))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    scopes_single = {
        "top5_2425": [
            ("E0_2425.csv",  "premier"),
            ("SP1_2425.csv", "la_liga"),
            ("I1_2425.csv",  "serie_a"),
            ("D1_2425.csv",  "bundesliga"),
            ("F1_2425.csv",  "ligue_1"),
        ],
    }
    scopes_multi = {
        "premier_multiseason": ([
            ("E0_2122.csv", "premier_21-22"),
            ("E0_2223.csv", "premier_22-23"),
            ("E0_2324.csv", "premier_23-24"),
            ("E0_2425.csv", "premier_24-25"),
        ], "24-25"),
    }

    index: list[dict] = []
    for market in markets:
        for scope, files in scopes_single.items():
            rep = _run_one(scope, files, market,
                              lambda_l2=args.lambda_l2, out_dir=out_dir)
            index.append({"scope": scope, "market": market,
                            "verdict": rep.get("verdict", {}).get("tags")})
        for scope, (files, hold) in scopes_multi.items():
            rep = _run_one(scope, files, market,
                              lambda_l2=args.lambda_l2, out_dir=out_dir,
                              multi_season_holdout=hold)
            index.append({"scope": scope, "market": market,
                            "verdict": rep.get("verdict", {}).get("tags")})
    (out_dir / "_d9_1_index.json").write_text(json.dumps(index, indent=2))
    print("\n=== D9.1 verdicts ===")
    for r in index:
        print(f"  {r['market']:<10} {r['scope']:<22} → {r['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
