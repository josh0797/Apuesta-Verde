"""Sprint Corner-2 · paso 1 — Distribución del Corner Difference + Asian Corner markets.

Construye la distribución empírica de ``corner_diff = home_corners - away_corners``
condicionada por bucket de ``expected_corner_diff``. Las probabilidades se
estiman desde un *training set* histórico (point-in-time) que se le pasa
al módulo de calibración.

Buckets del brief:

    diff <= -4
    -4 < diff <= -2
    -2 < diff <= -1
    -1 < diff < 1
    1 <= diff < 2
    2 <= diff < 4
    diff >= 4

Para cada bucket calculamos empíricamente:

    P(home_diff > k)  para k ∈ {0, 1, 2, 3}
    P(away_diff > k)  para k ∈ {0, 1, 2, 3}
    P(home_diff == k) para k ∈ {0, 1, 2, 3}   (para push en líneas enteras)

Asian markets generados:

    HOME -0.5, -1.0, -1.5, -2.0, -2.5, -3.0, -3.5
    AWAY -0.5, -1.0, -1.5, -2.0, -2.5, -3.0, -3.5

Para cada uno:
    prob_win, prob_push, prob_lose, fair_odds, ev, recommendation.

NO se inventan cuotas. Si el caller no pasa `book_odds`, ``book_odds=None``
y ``ev=None`` (y `recommendation` es WATCH/NO_BET, nunca BET).
"""
from __future__ import annotations

from typing import Any, Optional

# Buckets del brief (open, close): (low_exclusive, high_inclusive, label)
DEFAULT_BUCKETS = [
    (-99.0, -4.0,   "diff <= -4"),
    (-4.0,  -2.0,   "-4 < diff <= -2"),
    (-2.0,  -1.0,   "-2 < diff <= -1"),
    (-1.0,   1.0,   "-1 < diff < 1"),    # open en ambos lados
    ( 1.0,   2.0,   "1 <= diff < 2"),
    ( 2.0,   4.0,   "2 <= diff < 4"),
    ( 4.0,   99.0,  "diff >= 4"),
]

# Minimum sample size per bucket for confidence
MIN_SAMPLE_PER_BUCKET = 30

# Asian lines del brief
ASIAN_LINES = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]

# Recommendation rules (del brief)
BET_EV_THRESHOLD = 0.03
WATCH_EV_LOW     = 0.0
MIN_CONFIDENCE_FOR_BET = 60.0

REASON_REAL_ODDS_NA       = "ASIAN_CORNERS_REAL_ODDS_NOT_AVAILABLE"
REASON_LOW_SAMPLE         = "ASIAN_CORNERS_LOW_BUCKET_SAMPLE"
REASON_LOW_CONFIDENCE     = "ASIAN_CORNERS_LOW_CONFIDENCE"


# ============================================================
# Bucket helpers
# ============================================================

def _bucket_for_value(v: float, buckets: list[tuple] = None) -> int:
    """Retorna el índice del bucket donde cae `v`. Buckets parser:
       bucket_idx 0..6 según DEFAULT_BUCKETS.
    """
    if buckets is None:
        buckets = DEFAULT_BUCKETS
    for i, (lo, hi, _label) in enumerate(buckets):
        # El bucket central usa < en ambos lados, los demás (lo, hi] o [lo, hi)
        if i == 3:  # -1 < diff < 1
            if lo < v < hi:
                return i
        elif lo < v <= hi:
            return i
    return len(buckets) - 1


def fit_bucket_distributions(
    rows: list[dict],
    *,
    buckets: list[tuple] = None,
) -> dict[int, dict]:
    """Calcula empíricamente las probabilidades por bucket desde una
    lista de filas históricas. Cada fila debe tener:
        expected_corner_diff (float)
        home_corners (int)
        away_corners (int)

    Retorna {bucket_idx: {label, n, mean, std, p_home_gt_k, ...}}.
    """
    if buckets is None:
        buckets = DEFAULT_BUCKETS

    # Bucketize
    bucket_data: dict[int, list[int]] = {i: [] for i in range(len(buckets))}
    for r in rows:
        ed = r.get("expected_corner_diff")
        hc = r.get("home_corners")
        ac = r.get("away_corners")
        if ed is None or hc is None or ac is None:
            continue
        idx = _bucket_for_value(float(ed), buckets)
        bucket_data[idx].append(int(hc) - int(ac))

    out: dict[int, dict] = {}
    for idx, (_lo, _hi, label) in enumerate(buckets):
        diffs = bucket_data[idx]
        n = len(diffs)
        entry = {
            "bucket_idx": idx,
            "label":      label,
            "n":          n,
        }
        if n == 0:
            out[idx] = entry
            continue

        # mean & std
        mean = sum(diffs) / n
        var  = sum((d - mean) ** 2 for d in diffs) / n
        std  = var ** 0.5
        entry["mean"] = round(mean, 4)
        entry["std"]  = round(std, 4)

        # Tail probabilities
        for k in (0, 1, 2, 3):
            entry[f"p_home_gt_{k}"] = round(sum(1 for d in diffs if d > k) / n, 4)
            entry[f"p_away_gt_{k}"] = round(sum(1 for d in diffs if -d > k) / n, 4)
            # Push probability (P(diff == k))
            entry[f"p_diff_eq_pos_{k}"] = round(sum(1 for d in diffs if d == k) / n, 4)
            entry[f"p_diff_eq_neg_{k}"] = round(sum(1 for d in diffs if d == -k) / n, 4)

        out[idx] = entry

    return out


# ============================================================
# Distribution builder per match
# ============================================================

def build_corner_diff_distribution(
    context: dict[str, Any],
    *,
    bucket_stats: Optional[dict[int, dict]] = None,
) -> dict[str, Any]:
    """Construye la distribución empírica del corner_diff para un
    partido específico, dado el `expected_corner_diff` precalculado y
    las estadísticas por bucket.

    Si no se pasan bucket_stats, usa una distribución default
    aproximada (señal pobre, alta confianza solo informativa).
    """
    edcd = context.get("expected_corner_diff")
    if edcd is None:
        # Calcular si no viene precalculado
        from .corner_diff_model import compute_expected_corner_diff
        diff_result = compute_expected_corner_diff(context)
        edcd = diff_result["expected_corner_diff"]

    edcd = float(edcd)
    idx = _bucket_for_value(edcd)
    label = DEFAULT_BUCKETS[idx][2]

    reason_codes: list[str] = []
    bucket = bucket_stats.get(idx) if bucket_stats else None

    if not bucket or bucket.get("n", 0) == 0:
        # No empirical data — use a coarse Gaussian-like approximation
        std_default = 4.0
        # P(diff > k) ≈ Normal(edcd, 4)
        prob_h_gt = {k: _gauss_tail(edcd, std_default, k) for k in (0, 1, 2, 3)}
        prob_a_gt = {k: _gauss_tail(-edcd, std_default, k) for k in (0, 1, 2, 3)}
        push_h = {k: 0.06 for k in (0, 1, 2, 3)}  # rough constant
        push_a = {k: 0.06 for k in (0, 1, 2, 3)}
        bucket_n = 0
        diff_std = std_default
        confidence = 25.0
        reason_codes.append(REASON_LOW_SAMPLE)
        distribution_type = "DISCRETE_NORMAL"
    else:
        prob_h_gt = {k: bucket[f"p_home_gt_{k}"] for k in (0, 1, 2, 3)}
        prob_a_gt = {k: bucket[f"p_away_gt_{k}"] for k in (0, 1, 2, 3)}
        push_h = {k: bucket[f"p_diff_eq_pos_{k}"] for k in (0, 1, 2, 3)}
        push_a = {k: bucket[f"p_diff_eq_neg_{k}"] for k in (0, 1, 2, 3)}
        bucket_n = int(bucket["n"])
        diff_std = float(bucket.get("std", 4.0))
        # Confidence: función de bucket sample size y proximidad al centro
        if bucket_n >= 200:
            confidence = 80.0
        elif bucket_n >= 100:
            confidence = 70.0
        elif bucket_n >= 50:
            confidence = 60.0
        elif bucket_n >= MIN_SAMPLE_PER_BUCKET:
            confidence = 50.0
        else:
            confidence = 35.0
            reason_codes.append(REASON_LOW_SAMPLE)
        distribution_type = "EMPIRICAL_BUCKET"

    # Probabilidades del brief para output (líneas .5)
    probabilities = {
        "home_minus_0_5": round(prob_h_gt[0], 4),   # gana home si diff > 0
        "home_minus_1_5": round(prob_h_gt[1], 4),
        "home_minus_2_5": round(prob_h_gt[2], 4),
        "home_minus_3_5": round(prob_h_gt[3], 4),
        "away_minus_0_5": round(prob_a_gt[0], 4),
        "away_minus_1_5": round(prob_a_gt[1], 4),
        "away_minus_2_5": round(prob_a_gt[2], 4),
        "away_minus_3_5": round(prob_a_gt[3], 4),
    }
    # Líneas enteras con push
    for k in (0, 1, 2, 3):
        # Home side: gana si diff > k, push si diff == k
        win_h  = prob_h_gt[k]
        push_h_k = push_h[k]
        lose_h = max(0.0, 1.0 - win_h - push_h_k)
        probabilities[f"home_minus_{k}"] = {
            "win":  round(win_h, 4),
            "push": round(push_h_k, 4),
            "lose": round(lose_h, 4),
        }
        # Away side: gana si -diff > k, push si -diff == k
        win_a  = prob_a_gt[k]
        push_a_k = push_a[k]
        lose_a = max(0.0, 1.0 - win_a - push_a_k)
        probabilities[f"away_minus_{k}"] = {
            "win":  round(win_a, 4),
            "push": round(push_a_k, 4),
            "lose": round(lose_a, 4),
        }

    return {
        "distribution_type":   distribution_type,
        "expected_corner_diff": round(edcd, 4),
        "diff_std":            round(diff_std, 4),
        "bucket_idx":          idx,
        "bucket_label":        label,
        "bucket_sample_size":  bucket_n,
        "probabilities":       probabilities,
        "confidence":          round(confidence, 2),
        "reason_codes":        reason_codes,
        "debug": {
            "prob_h_gt_raw": prob_h_gt,
            "prob_a_gt_raw": prob_a_gt,
            "push_h_raw":    push_h,
            "push_a_raw":    push_a,
        },
    }


# ============================================================
# Asian markets builder
# ============================================================

def build_asian_corner_markets(
    distribution: dict[str, Any],
    *,
    book_odds: Optional[dict[str, float]] = None,
    real_odds_available: bool = False,
) -> list[dict[str, Any]]:
    """Construye la lista de mercados Asian Corners derivados de la
    distribución. Si no hay `book_odds`, emite WATCH/NO_BET y marca
    REAL_ODDS_NOT_AVAILABLE.

    book_odds, si se pasa, debe ser un dict con keys del estilo:
        "HOME_-0.5", "HOME_-1.0", ..., "AWAY_-0.5", ...
    valores = decimal odds del bookmaker.
    """
    if book_odds is None:
        book_odds = {}

    probs = distribution.get("probabilities", {})
    confidence = float(distribution.get("confidence", 0.0))

    markets: list[dict[str, Any]] = []
    base_reason = list(distribution.get("reason_codes", []))
    if not real_odds_available:
        base_reason.append(REASON_REAL_ODDS_NA)

    for side in ("HOME", "AWAY"):
        for line in ASIAN_LINES:
            market_id = f"{side}_-{line}"
            reasons = list(base_reason)

            if line == int(line):  # integer line -> push possible
                key = f"{side.lower()}_minus_{int(line)}"
                trio = probs.get(key, {"win": 0.0, "push": 0.0, "lose": 1.0})
                p_win  = trio["win"]
                p_push = trio["push"]
                p_lose = trio["lose"]
                # Fair odds: (1 - push)/win (porque push devuelve la stake)
                if p_win > 1e-6:
                    fair_odds = (1.0 - p_push) / p_win
                else:
                    fair_odds = None
            else:  # half line -> no push
                key = f"{side.lower()}_minus_{str(line).replace('.', '_')}"
                p_win  = probs.get(key, 0.0)
                p_push = 0.0
                p_lose = max(0.0, 1.0 - p_win)
                if p_win > 1e-6:
                    fair_odds = 1.0 / p_win
                else:
                    fair_odds = None

            # EV calculation if we have book odds
            book_price = book_odds.get(market_id)
            ev = None
            if book_price is not None and book_price > 1.0:
                # ev = p_win * (odds - 1) - p_lose, push devuelve 0 (no afecta)
                ev = round(p_win * (book_price - 1.0) - p_lose, 4)

            # Recommendation
            recommendation = "NO_BET"
            if book_price is None:
                # Sin cuota real: solo informativo
                recommendation = "WATCH" if real_odds_available else "NO_BET"
            elif confidence < MIN_CONFIDENCE_FOR_BET:
                recommendation = "NO_BET"
                reasons.append(REASON_LOW_CONFIDENCE)
            elif ev is not None and ev >= BET_EV_THRESHOLD:
                recommendation = "BET"
            elif ev is not None and ev > WATCH_EV_LOW:
                recommendation = "WATCH"
            else:
                recommendation = "NO_BET"

            # Dedupe reasons
            seen = set()
            rc_clean = []
            for rc in reasons:
                if rc not in seen:
                    seen.add(rc)
                    rc_clean.append(rc)

            markets.append({
                "market":         f"{side}_CORNERS_-{line}",
                "side":           side,
                "line":           line,
                "prob_win":       round(p_win, 4),
                "prob_push":      round(p_push, 4),
                "prob_lose":      round(p_lose, 4),
                "fair_odds":      round(fair_odds, 4) if fair_odds is not None else None,
                "book_odds":      book_price,
                "ev":             ev,
                "recommendation": recommendation,
                "confidence":     round(confidence, 2),
                "reason_codes":   rc_clean,
            })

    return markets


# ============================================================
# Internals
# ============================================================

def _gauss_tail(mean: float, std: float, k: float) -> float:
    """P(X > k) para X ~ Normal(mean, std), sin scipy.
    Aproximación con la CDF Hastings."""
    if std <= 0:
        return 1.0 if mean > k else 0.0
    z = (k - mean) / std
    # Φ(z) Hastings approximation
    t = 1.0 / (1.0 + 0.2316419 * abs(z))
    pdf = 0.3989422804014327 * (2.71828182845904523536 ** (-0.5 * z * z))
    poly = (((1.330274429 * t - 1.821255978) * t + 1.781477937) * t - 0.356563782) * t + 0.319381530
    cdf = 1.0 - pdf * poly * t
    if z < 0:
        cdf = 1.0 - cdf
    # P(X > k) = 1 - CDF
    return 1.0 - cdf
