"""Sprint Corner-1+2 · paso final — Backtest probabilístico walk-forward.

Implementa:

  * Calibración walk-forward de β (corner_diff_model) por OLS sobre el
    `corner_diff` real.
  * Calibración walk-forward de (a, b) (corner_most_model sigmoid) por
    maximum likelihood (gradient descent en numpy puro).
  * Calibración de tie buckets por frecuencia empírica.
  * Calibración de bucket_stats para Asian Corners.
  * Backtest probabilístico sobre el split temporal definido por el
    usuario (21/22 train → 22/23 test, 21/22+22/23 train → 23/24 test).
  * Métricas: Brier, Log Loss, calibration (n_bins=10), hit rate global y
    por liga, fair odds vs (opcional) book odds.

Sin sklearn/scipy — todo numpy puro.

CRITICAL: este backtest es **PROBABILÍSTICO**, no financiero. ROI solo se
reporta cuando hay book_odds reales en el input.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from .corner_diff_model import (
    DEFAULT_COEFFICIENTS,
    DOMINANT_FAVORITE_THRESHOLD,
    compute_expected_corner_diff,
)
from .corner_most_model import (
    DEFAULT_SIGMOID_A,
    DEFAULT_SIGMOID_B,
    predict_most_corners,
)
from .corner_diff_distribution import (
    DEFAULT_BUCKETS,
    _bucket_for_value,
    build_corner_diff_distribution,
    build_asian_corner_markets,
    fit_bucket_distributions,
)
from .skellam_corner_model import (  # noqa: F401
    calibrate_skellam_lambdas,
    predict_skellam_corner_diff,
    skellam_most_corners,
    skellam_to_asian_corners,
)


REASON_REAL_ODDS_NA = "REAL_ODDS_NOT_AVAILABLE"


def _pick_side_from_probs(most: dict[str, Any]) -> str:
    """Helper para Skellam: replica las reglas NO_BET de corner_most_model."""
    p_home = float(most["home_most_corners_prob"])
    p_away = float(most["away_most_corners_prob"])
    if max(p_home, p_away) < 0.58:
        return "NO_BET"
    return "HOME" if p_home > p_away else "AWAY"


# ============================================================
# OLS calibration of corner_diff_model
# ============================================================

def calibrate_diff_model(train_rows: list[dict]) -> dict[str, float]:
    """Calibra los β del corner_diff_model usando OLS sobre el target
    `corner_diff = home_corners - away_corners`.

    Necesita en cada row:
      * Features: home_implied_prob, away_implied_prob,
        home_corners_for_L15, away_corners_for_L15,
        home_corners_against_L15, away_corners_against_L15,
        home_deep_allowed_L15, away_deep_allowed_L15
      * Target: home_corners, away_corners
    """
    X_rows = []
    y_rows = []
    for r in train_rows:
        feats = _row_to_features(r)
        if feats is None:
            continue
        # Construir vector en orden: [1 (intercept), ip_diff, cf_diff, ca_diff,
        # da_diff, dom_signal, vs_diff]
        # vs_diff es opcional, lo usamos solo si está
        x = [
            1.0,
            feats["ip_diff"],
            feats["cf_diff"],
            feats["ca_diff"],
            feats["da_diff"] if feats["da_diff"] is not None else 0.0,
            feats["dom_signal"],
            feats["vs_diff"] if feats["vs_diff"] is not None else 0.0,
        ]
        X_rows.append(x)
        y_rows.append(float(r["home_corners"] - r["away_corners"]))
    if len(X_rows) < 50:
        return dict(DEFAULT_COEFFICIENTS)
    X = np.array(X_rows, dtype=float)
    y = np.array(y_rows, dtype=float)
    # OLS: β = (X'X)^-1 X' y
    XtX = X.T @ X
    try:
        beta = np.linalg.solve(XtX, X.T @ y)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(XtX) @ (X.T @ y)
    return {
        "intercept":                  float(beta[0]),
        "implied_prob_diff":          float(beta[1]),
        "corners_for_diff_L15":       float(beta[2]),
        "corners_against_diff_L15":   float(beta[3]),
        "deep_allowed_diff_L15":      float(beta[4]),
        "dominant_favorite_signal":   float(beta[5]),
        "venue_corner_split_diff":    float(beta[6]),
    }


def _row_to_features(r: dict) -> Optional[dict]:
    """Extrae las features comunes a calibración y predicción. Returns
    None si critical features están ausentes."""
    iph = r.get("home_implied_prob") or r.get("implied_prob_home")
    ipa = r.get("away_implied_prob") or r.get("implied_prob_away")
    hcf = r.get("home_corners_for_L15")
    acf = r.get("away_corners_for_L15")
    hca = r.get("home_corners_against_L15")
    aca = r.get("away_corners_against_L15")
    if any(v is None for v in (iph, ipa, hcf, acf, hca, aca)):
        return None
    ip_diff = iph - ipa
    cf_diff = hcf - acf
    ca_diff = aca - hca  # invertido (away concede más → más corner home)
    hda = r.get("home_deep_allowed_L15")
    ada = r.get("away_deep_allowed_L15")
    da_diff = (ada - hda) if (hda is not None and ada is not None) else None
    hv = r.get("home_venue_corner_split")
    av = r.get("away_venue_corner_split")
    vs_diff = (hv - av) if (hv is not None and av is not None) else None
    # Dominant favorite signal
    max_p = max(iph, ipa)
    if max_p >= DOMINANT_FAVORITE_THRESHOLD:
        dom_signal = 1 if iph > ipa else -1
    else:
        dom_signal = 0
    return {
        "ip_diff":    ip_diff,
        "cf_diff":    cf_diff,
        "ca_diff":    ca_diff,
        "da_diff":    da_diff,
        "dom_signal": dom_signal,
        "vs_diff":    vs_diff,
    }


# ============================================================
# Sigmoid calibration (Maximum Likelihood) — numpy puro
# ============================================================

def calibrate_most_corners_sigmoid(
    train_rows: list[dict],
    coefficients: dict[str, float],
    *,
    lr: float = 0.05,
    n_iter: int = 800,
) -> tuple[float, float]:
    """Calibra (a, b) del modelo Most Corners por gradient descent sobre
    log-likelihood. Solo cuenta partidos NO empate en córners.

    Modelo: p_home_no_tie = sigmoid(a*x + b)
    Target: y = 1 si home_corners > away_corners, 0 si away > home.
    Empates excluidos (porque entran al modelo de tie).
    """
    xs = []
    ys = []
    for r in train_rows:
        hc = r.get("home_corners")
        ac = r.get("away_corners")
        if hc is None or ac is None or hc == ac:
            continue
        # Predict expected_corner_diff con los β calibrados
        ctx = dict(r)
        diff_result = compute_expected_corner_diff(ctx, coefficients=coefficients)
        xs.append(float(diff_result["expected_corner_diff_raw"]))  # usar raw (sin cap)
        ys.append(1.0 if hc > ac else 0.0)
    if len(xs) < 50:
        return DEFAULT_SIGMOID_A, DEFAULT_SIGMOID_B
    X = np.array(xs)
    Y = np.array(ys)
    a, b = DEFAULT_SIGMOID_A, DEFAULT_SIGMOID_B
    # ML con descenso de gradiente
    n = len(X)
    for _ in range(n_iter):
        z = a * X + b
        p = 1.0 / (1.0 + np.exp(-z))
        # gradiente: dL/da = sum((y-p) * x) / n ; dL/db = sum(y-p) / n
        err = Y - p
        ga = float((err * X).sum()) / n
        gb = float(err.sum()) / n
        a += lr * ga
        b += lr * gb
    return float(a), float(b)


def calibrate_tie_buckets(
    train_rows: list[dict],
    coefficients: dict[str, float],
    *,
    bucket_edges: list[float] = (0.5, 1.5, 2.5, 3.5, 5.5),
) -> list[tuple[float, float]]:
    """Calibra la frecuencia empírica de empates en córners por bucket de
    |expected_corner_diff|.
    """
    counts: dict[int, list[int]] = {i: [] for i in range(len(bucket_edges) + 1)}
    for r in train_rows:
        hc = r.get("home_corners")
        ac = r.get("away_corners")
        if hc is None or ac is None:
            continue
        ctx = dict(r)
        diff_result = compute_expected_corner_diff(ctx, coefficients=coefficients)
        absed = abs(float(diff_result["expected_corner_diff"]))
        is_tie = 1 if hc == ac else 0
        # Localizar bucket
        idx = len(bucket_edges)
        for i, edge in enumerate(bucket_edges):
            if absed <= edge:
                idx = i
                break
        counts[idx].append(is_tie)
    # Build buckets list
    out = []
    edges = list(bucket_edges) + [99.0]
    for i, edge in enumerate(edges):
        lst = counts[i]
        if not lst:
            # Sin sample → default razonable (decrece con edge)
            p_tie = max(0.04, 0.18 - 0.025 * i)
        else:
            p_tie = sum(lst) / len(lst)
        out.append((edge, round(p_tie, 4)))
    return out


# ============================================================
# Walk-forward backtest
# ============================================================

def run_corner_backtest(
    rows: list[dict],
    *,
    walk_forward: list[tuple[list[str], list[str]]] = None,
    odds_lookup: Optional[dict[str, dict]] = None,
    include_skellam: bool = False,
) -> dict[str, Any]:
    """Backtest walk-forward del motor de córners.

    Args:
        rows: lista de partidos enriquecidos (con features + targets).
              Cada row debe tener: `season`, `date`, `league`,
              `home_corners`, `away_corners`, + features prematch.
        walk_forward: lista de (train_seasons, test_seasons). Default:
              [(["2122"], ["2223"]), (["2122","2223"], ["2324"])].
        odds_lookup: opcional. dict {match_id → {market_id: book_odds}}
                     para mercados reales (Asian Corners).

    Returns:
        dict con métricas globales + por liga + por fold + warnings.
    """
    if walk_forward is None:
        walk_forward = [
            (["2122"], ["2223"]),
            (["2122", "2223"], ["2324"]),
        ]
    real_odds_available = odds_lookup is not None and len(odds_lookup) > 0
    warnings: list[str] = []
    if not real_odds_available:
        warnings.append(REASON_REAL_ODDS_NA)

    # Bucket rows by season for fast slicing
    rows_by_season: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        s = str(r.get("season", ""))
        if s:
            rows_by_season[s].append(r)

    fold_results = []
    all_predictions: list[dict] = []   # for global aggregation
    all_skellam_predictions: list[dict] = []

    for fold_idx, (train_seasons, test_seasons) in enumerate(walk_forward):
        train = [r for s in train_seasons for r in rows_by_season.get(s, [])]
        test  = [r for s in test_seasons  for r in rows_by_season.get(s, [])]
        if not train or not test:
            continue

        coefs = calibrate_diff_model(train)
        a, b  = calibrate_most_corners_sigmoid(train, coefs)
        tie_buckets = calibrate_tie_buckets(train, coefs)
        # Bucket stats requieren primero correr el modelo en train para
        # calcular expected_corner_diff de cada row.
        train_rows_with_ed = []
        for r in train:
            diff_result = compute_expected_corner_diff(r, coefficients=coefs)
            train_rows_with_ed.append({
                "expected_corner_diff": diff_result["expected_corner_diff"],
                "home_corners": r.get("home_corners"),
                "away_corners": r.get("away_corners"),
            })
        bucket_stats = fit_bucket_distributions(train_rows_with_ed)

        # Skellam calibration (opcional)
        if include_skellam:
            skellam_coefs_h, skellam_coefs_a = calibrate_skellam_lambdas(train)
        else:
            skellam_coefs_h, skellam_coefs_a = None, None

        # Predict in test
        fold_preds: list[dict] = []
        skellam_preds: list[dict] = []
        for r in test:
            hc = r.get("home_corners")
            ac = r.get("away_corners")
            if hc is None or ac is None:
                continue
            pred = predict_most_corners(r,
                                          sigmoid_a=a, sigmoid_b=b,
                                          tie_buckets=tie_buckets,
                                          diff_coefficients=coefs)
            # Asian markets (lineal)
            dist = build_corner_diff_distribution({"expected_corner_diff":
                                                     pred["expected_corner_diff"]},
                                                    bucket_stats=bucket_stats)
            book_odds = (odds_lookup or {}).get(r.get("match_id"))
            asian = build_asian_corner_markets(dist, book_odds=book_odds,
                                                real_odds_available=real_odds_available)
            entry = {
                "match_id":         r.get("match_id"),
                "date":             r.get("date"),
                "league":           r.get("league"),
                "season":           r.get("season"),
                "home_corners":     hc,
                "away_corners":     ac,
                "actual_diff":      hc - ac,
                "actual_winner":    "home" if hc > ac else ("away" if ac > hc else "tie"),
                "prediction":       pred,
                "distribution":     dist,
                "asian_markets":    asian,
            }
            fold_preds.append(entry)

            # Skellam parallel prediction (si está habilitado)
            if include_skellam:
                sk = predict_skellam_corner_diff(r,
                                                   coefs_home=skellam_coefs_h,
                                                   coefs_away=skellam_coefs_a)
                sk_most = skellam_most_corners(sk)
                sk_asian = skellam_to_asian_corners(sk, book_odds=book_odds,
                                                     real_odds_available=real_odds_available)
                skellam_preds.append({
                    "match_id":      r.get("match_id"),
                    "date":          r.get("date"),
                    "league":        r.get("league"),
                    "season":        r.get("season"),
                    "home_corners":  hc,
                    "away_corners":  ac,
                    "actual_diff":   hc - ac,
                    "actual_winner": "home" if hc > ac else ("away" if ac > hc else "tie"),
                    "prediction":    {**sk_most,
                                        "recommended_side": _pick_side_from_probs(sk_most),
                                        "lambda_h": sk["lambda_h"],
                                        "lambda_a": sk["lambda_a"]},
                    "asian_markets": sk_asian,
                })

        fold_metrics = _compute_fold_metrics(fold_preds)
        fold_metrics["fold_idx"]      = fold_idx
        fold_metrics["train_seasons"] = train_seasons
        fold_metrics["test_seasons"]  = test_seasons
        fold_metrics["calibrated_coefficients"] = coefs
        fold_metrics["calibrated_sigmoid"]      = {"a": a, "b": b}
        fold_metrics["calibrated_tie_buckets"]  = tie_buckets
        if include_skellam:
            fold_metrics["skellam_metrics"] = _compute_fold_metrics(skellam_preds)
            fold_metrics["skellam_coefs_home"] = skellam_coefs_h
            fold_metrics["skellam_coefs_away"] = skellam_coefs_a

        fold_results.append(fold_metrics)
        all_predictions.extend(fold_preds)
        if include_skellam:
            all_skellam_predictions.extend(skellam_preds)

    global_metrics = _compute_fold_metrics(all_predictions)
    by_league = _compute_metrics_by_league(all_predictions)
    asian_metrics = _compute_asian_metrics(all_predictions, real_odds_available)

    result = {
        "real_odds_available": real_odds_available,
        "warnings":            warnings,
        "n_total_predictions": len(all_predictions),
        "fold_results":        fold_results,
        "global_metrics":      global_metrics,
        "by_league":           by_league,
        "asian_metrics":       asian_metrics,
    }
    if include_skellam:
        result["skellam_global_metrics"] = _compute_fold_metrics(all_skellam_predictions)
        result["skellam_by_league"]      = _compute_metrics_by_league(all_skellam_predictions)
        result["skellam_asian_metrics"]  = _compute_asian_metrics(
            all_skellam_predictions, real_odds_available)
    return result


# ============================================================
# Metrics
# ============================================================

def _compute_fold_metrics(preds: list[dict]) -> dict[str, Any]:
    n = len(preds)
    if n == 0:
        return {"n": 0}
    brier_acc, ll_acc, hit_acc, decided_acc = 0.0, 0.0, 0, 0
    # Calibration bins (probability home_most predicted vs realized)
    bins = [{"n": 0, "p_sum": 0.0, "y_sum": 0} for _ in range(10)]
    # Bet decision tracking
    n_bet_decisions = 0
    n_bet_correct = 0

    for p in preds:
        actual = p["actual_winner"]
        pred_p_home = float(p["prediction"]["home_most_corners_prob"])
        pred_p_away = float(p["prediction"]["away_most_corners_prob"])
        pred_p_tie  = float(p["prediction"]["tie_corners_prob"])
        # Brier: sum of squared errors for the 3-way distribution
        y_home = 1.0 if actual == "home" else 0.0
        y_away = 1.0 if actual == "away" else 0.0
        y_tie  = 1.0 if actual == "tie"  else 0.0
        brier = ((pred_p_home - y_home) ** 2
                 + (pred_p_away - y_away) ** 2
                 + (pred_p_tie  - y_tie)  ** 2)
        brier_acc += brier
        # Log Loss (focused on the actual outcome's probability)
        if actual == "home":
            true_p = pred_p_home
        elif actual == "away":
            true_p = pred_p_away
        else:
            true_p = pred_p_tie
        true_p = max(1e-9, min(1.0 - 1e-9, true_p))
        ll_acc += -math.log(true_p)

        # Hit rate among DECIDED matches (excluding ties) — what model
        # would have predicted ignoring tie probability
        if actual != "tie":
            decided_acc += 1
            argmax_side = "home" if pred_p_home > pred_p_away else "away"
            if argmax_side == actual:
                hit_acc += 1
        # Calibration on P(home_most)
        idx = min(9, int(pred_p_home * 10))
        bins[idx]["n"] += 1
        bins[idx]["p_sum"] += pred_p_home
        bins[idx]["y_sum"] += int(actual == "home")

        # Bet decision tracking
        if p["prediction"]["recommended_side"] in ("HOME", "AWAY"):
            n_bet_decisions += 1
            if p["prediction"]["recommended_side"].lower() == actual:
                n_bet_correct += 1

    calibration = []
    for i, bn in enumerate(bins):
        if bn["n"] == 0:
            calibration.append({"bin": f"{i*0.1:.1f}-{(i+1)*0.1:.1f}",
                                  "n": 0, "predicted_p": None, "observed_p": None})
        else:
            calibration.append({
                "bin": f"{i*0.1:.1f}-{(i+1)*0.1:.1f}",
                "n":   bn["n"],
                "predicted_p": round(bn["p_sum"] / bn["n"], 4),
                "observed_p":  round(bn["y_sum"] / bn["n"], 4),
            })

    return {
        "n":                 n,
        "n_decided":         decided_acc,
        "brier_score":       round(brier_acc / n, 4),
        "log_loss":          round(ll_acc / n, 4),
        "hit_rate_decided":  round(hit_acc / decided_acc, 4) if decided_acc else None,
        "calibration_bins":  calibration,
        "n_bet_decisions":   n_bet_decisions,
        "n_bet_correct":     n_bet_correct,
        "bet_hit_rate":      round(n_bet_correct / n_bet_decisions, 4) if n_bet_decisions else None,
    }


def _compute_metrics_by_league(preds: list[dict]) -> dict[str, dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        by[p.get("league") or "UNKNOWN"].append(p)
    out = {}
    for lg, lst in by.items():
        m = _compute_fold_metrics(lst)
        out[lg] = m
    return out


def _compute_asian_metrics(preds: list[dict], real_odds_available: bool) -> dict[str, Any]:
    """Métricas probabilísticas para Asian Corner markets agregadas por línea."""
    by_market: dict[str, dict] = defaultdict(lambda: {"n": 0, "win_count": 0,
                                                         "push_count": 0,
                                                         "lose_count": 0,
                                                         "prob_sum": 0.0})
    for p in preds:
        actual_diff = p.get("actual_diff")
        if actual_diff is None:
            continue
        for market in p.get("asian_markets", []):
            line = market["line"]
            side = market["side"]
            mkey = market["market"]
            # Did the market WIN?
            if side == "HOME":
                # Home gana si actual_diff > line; push si == line (entera)
                if line == int(line):
                    if actual_diff > line:
                        outcome = "win"
                    elif actual_diff == line:
                        outcome = "push"
                    else:
                        outcome = "lose"
                else:
                    outcome = "win" if actual_diff > line else "lose"
            else:  # AWAY
                if line == int(line):
                    if -actual_diff > line:
                        outcome = "win"
                    elif -actual_diff == line:
                        outcome = "push"
                    else:
                        outcome = "lose"
                else:
                    outcome = "win" if -actual_diff > line else "lose"

            by_market[mkey]["n"] += 1
            by_market[mkey][f"{outcome}_count"] += 1
            by_market[mkey]["prob_sum"] += float(market["prob_win"])

    out = {}
    for mkey, st in by_market.items():
        n = st["n"]
        if n == 0:
            continue
        observed_win_rate = st["win_count"] / n
        avg_predicted = st["prob_sum"] / n
        # Brier per market (only on win/lose, ignore push for simplicity)
        out[mkey] = {
            "n":                 n,
            "n_win":             st["win_count"],
            "n_push":            st["push_count"],
            "n_lose":            st["lose_count"],
            "observed_win_rate": round(observed_win_rate, 4),
            "avg_predicted_win": round(avg_predicted, 4),
            "calibration_gap":   round(avg_predicted - observed_win_rate, 4),
        }
    return out
