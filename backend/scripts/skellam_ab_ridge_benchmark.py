"""Sprint-D9 · A/B benchmark Skellam: ridge=0.1 (baseline persistido) vs
ridge=0.5 (propuesto).

Pipeline:
  1. Reconstruir features rolling L15 desde all_leagues_enriched_dataset.json.
  2. Train en 2122+2223; eval out-of-sample en 2324.
  3. Para CADA versión calcular sobre TEST:
       - Distribución λ_h, λ_a (min/mean/p95/max + % saturated ≥17)
       - EDCD (expected_corner_diff) mean / p10 / p50 / p90
       - Most Corners binario {home wins vs tie/away}:
           * Brier score
           * Log Loss
           * AUC ROC
           * Hit rate (argmax pred vs ground truth)
  4. Reporta tabla comparativa + recomendación.

Decisión: aceptar ridge=0.5 SOLO si Brier↓ y LogLoss↓ (o ≤+1% diff)
y AUC se mantiene (±0.005). Si degrada → mantener ridge=0.1 + guards.
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/backend")
from services.football.corners.skellam_corner_model import (
    calibrate_skellam_lambdas, predict_skellam_corner_diff, LAMBDA_MAX,
)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _avg(items, n, key, vf=None):
    pool = items if vf is None else [it for it in items if it["venue"] == vf]
    return _mean([it.get(key) for it in pool[-n:]]) if pool else None


def build_pit_features():
    data = json.load(open("/app/data/corners_history/all_leagues_enriched_dataset.json"))
    rows = sorted(data, key=lambda r: (r["date"], r["league_code"]))
    history = defaultdict(list)
    out = []
    for r in rows:
        home, away = r["home_team"], r["away_team"]
        hh, ah = history[home], history[away]
        feat = {
            "match_id": r["match_id"], "date": r["date"], "season": r["season"],
            "league": r["league"], "league_code": r["league_code"],
            "home_team": home, "away_team": away,
            "home_corners": r["home_corners"], "away_corners": r["away_corners"],
            "home_implied_prob": r.get("implied_prob_home"),
            "away_implied_prob": r.get("implied_prob_away"),
            "home_corners_for_L15":     _avg(hh, 15, "corners_for"),
            "home_corners_against_L15": _avg(hh, 15, "corners_against"),
            "away_corners_for_L15":     _avg(ah, 15, "corners_for"),
            "away_corners_against_L15": _avg(ah, 15, "corners_against"),
            "home_deep_allowed_L15":    _avg(hh, 15, "deep_allowed"),
            "away_deep_allowed_L15":    _avg(ah, 15, "deep_allowed"),
            "home_xg_for_L15":          _avg(hh, 15, "xg_for"),
            "away_xg_for_L15":          _avg(ah, 15, "xg_for"),
        }
        out.append(feat)
        history[home].append({"venue": "home", "corners_for": r["home_corners"],
            "corners_against": r["away_corners"], "deep_allowed": r.get("deep_allowed_h"),
            "xg_for": r.get("xg_h")})
        history[away].append({"venue": "away", "corners_for": r["away_corners"],
            "corners_against": r["home_corners"], "deep_allowed": r.get("deep_allowed_a"),
            "xg_for": r.get("xg_a")})
    return out


def _safe_log(p):
    """Log estable para LogLoss."""
    return float(np.log(np.clip(p, 1e-9, 1.0 - 1e-9)))


def _auc(y_true, y_score):
    """AUC ROC implementación pura (Mann-Whitney)."""
    pairs = sorted(zip(y_score, y_true), reverse=True)
    n_pos = sum(y for y in y_true)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    rank_sum = 0.0
    for rank, (_, y) in enumerate(pairs, start=1):
        if y == 1:
            rank_sum += (len(pairs) - rank + 1)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def evaluate(test_rows, coefs_h, coefs_a, label):
    lams_h, lams_a, edcds = [], [], []
    # Most Corners binario: home wins (1) o not (tie/away → 0).
    y_true, y_score = [], []
    most_correct = 0
    most_total = 0
    saturated = 0
    for r in test_rows:
        try:
            pred = predict_skellam_corner_diff(r, coefs_home=coefs_h, coefs_away=coefs_a)
        except Exception:
            continue
        lh, la = pred["lambda_h"], pred["lambda_a"]
        lams_h.append(lh)
        lams_a.append(la)
        if lh >= LAMBDA_MAX or la >= LAMBDA_MAX:
            saturated += 1
        edcds.append(pred["expected_corner_diff"])

        hc, ac = r["home_corners"], r["away_corners"]
        if hc == ac:
            actual_home_wins = 0
        else:
            actual_home_wins = 1 if hc > ac else 0

        # P(home wins corner battle) ≈ skellam P(diff > 0)
        # Aproximación rápida: usar la distribución del modelo:
        most = pred.get("most_corners_probabilities", {})
        p_home = float(most.get("home", 0.0)) if most else max(0.0, min(1.0, 0.5 + pred["expected_corner_diff"] * 0.05))
        p_away = float(most.get("away", 0.0)) if most else max(0.0, min(1.0, 0.5 - pred["expected_corner_diff"] * 0.05))
        p_tie = float(most.get("tie", 0.0)) if most else max(0.0, 1.0 - p_home - p_away)

        y_true.append(actual_home_wins)
        y_score.append(p_home)

        # Hit rate ternario (home/tie/away)
        if hc > ac:
            actual_class = "home"
        elif hc < ac:
            actual_class = "away"
        else:
            actual_class = "tie"
        predicted_class = max(("home", "away", "tie"),
                                key=lambda k: most.get(k, 0.0) if most else (p_home if k == "home" else p_away if k == "away" else p_tie))
        if predicted_class == actual_class:
            most_correct += 1
        most_total += 1

    # Métricas
    n = len(y_true)
    brier = sum((s - y) ** 2 for s, y in zip(y_score, y_true)) / n if n else float("nan")
    logloss = -sum(_safe_log(s) if y == 1 else _safe_log(1 - s)
                    for s, y in zip(y_score, y_true)) / n if n else float("nan")
    auc = _auc(y_true, y_score)
    hit_rate = (most_correct / most_total) if most_total else float("nan")

    return {
        "label":          label,
        "n_test":         n,
        "λ_h_min":        round(min(lams_h), 2),
        "λ_h_mean":       round(float(np.mean(lams_h)), 2),
        "λ_h_p95":        round(float(np.percentile(lams_h, 95)), 2),
        "λ_h_max":        round(max(lams_h), 2),
        "λ_a_min":        round(min(lams_a), 2),
        "λ_a_mean":       round(float(np.mean(lams_a)), 2),
        "λ_a_p95":        round(float(np.percentile(lams_a, 95)), 2),
        "λ_a_max":        round(max(lams_a), 2),
        "saturated_pct":  round(100.0 * saturated / max(n, 1), 3),
        "edcd_mean":      round(float(np.mean(edcds)), 3),
        "edcd_p10":       round(float(np.percentile(edcds, 10)), 3),
        "edcd_p50":       round(float(np.percentile(edcds, 50)), 3),
        "edcd_p90":       round(float(np.percentile(edcds, 90)), 3),
        "brier":          round(brier, 5),
        "log_loss":       round(logloss, 5),
        "auc":            round(auc, 5),
        "most_hit_rate":  round(hit_rate, 5),
    }


def main():
    print("Building PIT features…")
    pit = build_pit_features()
    train = [r for r in pit if r["season"] in ("2122", "2223")]
    test = [r for r in pit if r["season"] == "2324"]
    print(f"  train={len(train)}  test={len(test)}")

    print("\nCalibrating BASELINE (ridge=0.1)…")
    coefs_h_a, coefs_a_a = calibrate_skellam_lambdas(
        train, use_interaction=False, ridge_strength=0.1,
    )
    print("\nCalibrating PROPOSED (ridge=0.5)…")
    coefs_h_b, coefs_a_b = calibrate_skellam_lambdas(
        train, use_interaction=False, ridge_strength=0.5,
    )

    print("\n--- Coefs comparison ---")
    print(f"{'feature':28s}{'A(0.1)':>11s}{'B(0.5)':>11s}{'Δ':>10s}")
    for k in coefs_h_a:
        a, b = coefs_h_a[k], coefs_h_b[k]
        print(f"  HOME {k:22s}{a:>+11.4f}{b:>+11.4f}{b - a:>+10.4f}")
    for k in coefs_a_a:
        a, b = coefs_a_a[k], coefs_a_b[k]
        print(f"  AWAY {k:22s}{a:>+11.4f}{b:>+11.4f}{b - a:>+10.4f}")

    print("\nEvaluating BASELINE…")
    res_a = evaluate(test, coefs_h_a, coefs_a_a, "A (ridge=0.1)")
    print("Evaluating PROPOSED…")
    res_b = evaluate(test, coefs_h_b, coefs_a_b, "B (ridge=0.5)")

    print("\n" + "=" * 78)
    print(f"{'Metric':30s}{'A (ridge=0.1)':>20s}{'B (ridge=0.5)':>20s}")
    print("-" * 78)
    for k in (
        "n_test",
        "λ_h_min", "λ_h_mean", "λ_h_p95", "λ_h_max",
        "λ_a_min", "λ_a_mean", "λ_a_p95", "λ_a_max",
        "saturated_pct",
        "edcd_mean", "edcd_p10", "edcd_p50", "edcd_p90",
        "brier", "log_loss", "auc", "most_hit_rate",
    ):
        a, b = res_a[k], res_b[k]
        delta = (b - a) if (isinstance(a, (int, float)) and isinstance(b, (int, float))) else "—"
        mark = ""
        if k in ("brier", "log_loss") and isinstance(delta, (int, float)):
            mark = " ✓" if delta < 0 else (" ✗" if delta > 0 else " =")
        elif k in ("auc", "most_hit_rate") and isinstance(delta, (int, float)):
            mark = " ✓" if delta > 0 else (" ✗" if delta < 0 else " =")
        elif k == "saturated_pct" and isinstance(delta, (int, float)):
            mark = " ✓" if delta < 0 else (" ✗" if delta > 0 else " =")
        print(f"{k:30s}{a:>20}{b:>20}  Δ={delta}{mark}")
    print("=" * 78)

    # Recomendación
    print("\nDecisión:")
    auc_better = res_b["auc"] >= res_a["auc"] - 0.005
    brier_better = res_b["brier"] <= res_a["brier"] * 1.01
    logloss_better = res_b["log_loss"] <= res_a["log_loss"] * 1.01

    if auc_better and brier_better and logloss_better:
        print("  ✓ ridge=0.5 mantiene o mejora performance → ADOPTAR PROPOSED")
        decision = "ADOPT_RIDGE_0_5"
    else:
        print("  ✗ ridge=0.5 degrada performance → MANTENER BASELINE + guards")
        decision = "KEEP_RIDGE_0_1"

    # Persist a JSON report
    report = {
        "decision":  decision,
        "baseline":  res_a,
        "proposed":  res_b,
        "coefs_a":   {"home": coefs_h_a, "away": coefs_a_a},
        "coefs_b":   {"home": coefs_h_b, "away": coefs_a_b},
    }
    Path("/app/diagnostics/skellam_ab_ridge_comparison.json").write_text(
        json.dumps(report, indent=2),
    )
    print("\nReport: /app/diagnostics/skellam_ab_ridge_comparison.json")


if __name__ == "__main__":
    main()
