"""Sprint-D · Football Backtest Engine.

Walks chronologically through historical matches, calling
``compute_draw_potential`` on **point-in-time-only** features, places
simulated bets when the verdict label is VALUE / STRONG_VALUE, settles
against the real outcome, and returns a per-pick log + the running
bankroll curve.

Anti-leakage invariants enforced here
-------------------------------------
* ELO and goal-history are updated AFTER the prediction has been
  locked in (handled inside the historical ingestor).
* Calibration is **walk-forward only**: at step ``i`` the calibrator
  may use ONLY picks settled at steps ``0..i-1``.
* Closing odds are NOT used; only the opening B365 draw odd which is
  available before kickoff.
* The engine never reads ``matches[i]`` outcome fields when building
  features for prediction.

Sprint-D3 — Multi-market support
--------------------------------
The engine now supports the following markets in ``no_market`` mode:
  * ``"DRAW"``          (original Sprint-D / D2)
  * ``"OVER_1_5"``      (Dixon-Coles bivariate)
  * ``"DOUBLE_CHANCE_HD"`` (Home or Draw, ELO 1X2 + Draw Potential)
  * ``"DOUBLE_CHANCE_AD"`` (Away or Draw)
  * ``"DOUBLE_CHANCE_HA"`` (Home or Away, = 1 − P(Draw))

The market-aware (with-odds) mode remains ``"DRAW"``-only for now.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .football_draw_potential import (
    compute_draw_potential,
    LABEL_VALUE_DRAW, LABEL_STRONG_VALUE,
    LABEL_FAIR_DRAW, LABEL_NO_VALUE,
)
from .football_over15_potential import compute_over15_potential
from .football_score_grid_potential import compute_score_grid_potential
from .football_double_chance_potential import compute_double_chance_potential
from .football_historical_ingestor import build_point_in_time_features

log = logging.getLogger("football_backtest_engine")

# Recalibration cadence — every N picks, refit the calibrator from
# settled picks so far. The fitter sees ONLY settled past data, never
# the current pick.
DEFAULT_RECAL_EVERY = 25

# Sprint-D2 · No-market label thresholds for DRAW (back-compat).
NO_MARKET_STRONG_VALUE_PP = 32.0
NO_MARKET_VALUE_PP        = 28.0
NO_MARKET_FAIR_PP         = 24.0


# ─────────────────────────────────────────────────────────────────────
# Sprint-D3 · Market specifications
# ─────────────────────────────────────────────────────────────────────
# Each spec describes how to:
#   1) predict for the market (predictor + extractor)
#   2) settle the ground-truth from a match row (hit_fn)
#   3) assign no-market labels from absolute prob thresholds
#
# The thresholds below are **initial conservative defaults** based on
# football literature baselines. They will be refined empirically in
# Sprint-D3 step D3.5 using the WC22 + Euro24 data.

# Default thresholds (in percentage points).
#
# Sprint-D3 D3.5 · Calibrated against combined WC22 + Euro24 data
# (n=87 predictions per market). For each market we report the
# observed base-rate / avg-pred and pick thresholds where the
# threshold-sweep hit-rate beats base-rate **with at least 20 fires**.
#
# Empirical findings (combined WC22+Euro24):
#   * OVER_1_5     : base_rate=0.747, avg_pred=0.741 — well calibrated
#                    in aggregate. hit_rate climbs monotonically with
#                    threshold (0.733 @ 50pp → 0.821 @ 85pp).
#   * DC_HD        : base_rate=0.701, avg_pred=0.745 — model
#                    **over-predicts** by ~4.4pp. Discrimination is
#                    poor above 65pp; hit_rate FLATLINES then DROPS.
#   * DC_AD        : base_rate=0.598, avg_pred=0.551 — model
#                    **under-predicts** by ~4.7pp. Top of distribution
#                    only reaches 61pp.
#   * DC_HA        : base_rate=0.701, avg_pred=0.704 — well calibrated.
#                    Hit-rate climbs to 0.778 @ 70pp threshold.
NO_MARKET_THRESHOLDS: dict[str, dict[str, float]] = {
    "DRAW": {
        # Sprint-D2 thresholds, kept as-is for back-compat.
        "STRONG": 32.0, "VALUE": 28.0, "FAIR": 24.0,
        "DEFAULT_FIRING": 28.0,
    },
    "OVER_1_5": {
        # Sweet spot 60→75→85 (n=68, 53, 28 fires; hit 0.75, 0.77, 0.82).
        "STRONG": 85.0, "VALUE": 75.0, "FAIR": 60.0,
        "DEFAULT_FIRING": 75.0,
    },
    "OVER_2_5": {
        # Base rate in top-5 leagues ≈ 0.52 (typical seasons). Defensive
        # defaults; engine uses min_edge_pp gating in market-aware mode.
        "STRONG": 70.0, "VALUE": 60.0, "FAIR": 50.0,
        "DEFAULT_FIRING": 60.0,
    },
    "UNDER_2_5": {
        # Complement of above; thresholds reflect the conservative
        # "model believes the match will be tight" zone.
        "STRONG": 60.0, "VALUE": 50.0, "FAIR": 40.0,
        "DEFAULT_FIRING": 50.0,
    },
    "DOUBLE_CHANCE_HD": {
        # Model is over-confident above 70pp → cap STRONG at 75.
        "STRONG": 75.0, "VALUE": 70.0, "FAIR": 65.0,
        "DEFAULT_FIRING": 70.0,
    },
    "DOUBLE_CHANCE_AD": {
        # Model under-predicts; scale shifted ~10pp lower than other
        # DC variants. n=49 @ thr=55pp (hit 0.612).
        "STRONG": 60.0, "VALUE": 55.0, "FAIR": 50.0,
        "DEFAULT_FIRING": 55.0,
    },
    "DOUBLE_CHANCE_HA": {
        # Sweet spot 65→70→75 (hit 0.72, 0.78, 0.77).
        "STRONG": 75.0, "VALUE": 70.0, "FAIR": 65.0,
        "DEFAULT_FIRING": 70.0,
    },
}


# Generic label set used for non-DRAW markets in no-market mode.
LABEL_STRONG_VALUE_GENERIC = "STRONG_VALUE"
LABEL_VALUE_GENERIC        = "VALUE_CANDIDATE"
LABEL_FAIR_GENERIC         = "FAIR_NO_EDGE"
LABEL_NO_VALUE_GENERIC     = "NO_VALUE"


def _hit_draw(m: dict) -> bool:
    return m.get("ftr") == "D"


def _hit_over15(m: dict) -> bool:
    return (int(m.get("fthg", 0)) + int(m.get("ftag", 0))) >= 2


def _hit_over25(m: dict) -> bool:
    return (int(m.get("fthg", 0)) + int(m.get("ftag", 0))) >= 3


def _hit_under25(m: dict) -> bool:
    return (int(m.get("fthg", 0)) + int(m.get("ftag", 0))) <= 2


def _hit_hd(m: dict) -> bool:
    return int(m.get("fthg", 0)) >= int(m.get("ftag", 0))


def _hit_ad(m: dict) -> bool:
    return int(m.get("ftag", 0)) >= int(m.get("fthg", 0))


def _hit_ha(m: dict) -> bool:
    return int(m.get("fthg", 0)) != int(m.get("ftag", 0))


def _predict_draw(features: dict, *,
                    value_threshold_pp: Optional[float] = None,
                    strong_threshold_pp: Optional[float] = None) -> dict:
    pred_kwargs = {k: v for k, v in features.items() if k != "_audit"}
    # Filter out Sprint-D3 extra keys not accepted by compute_draw_potential.
    for extra in ("goal_avg_for_home", "goal_avg_for_away",
                  "goal_avg_against_home", "goal_avg_against_away",
                  # Sprint-D7-F · per-market implied probs are routed
                  # by the engine, not consumed by the draw module.
                  "market_implied_over25_prob",
                  "market_implied_under25_prob",
                  # Sprint-D9.1 · goal-minus-xG_proxy overperformance
                  # is consumed by the residual model only.
                  "goal_minus_xg_home_l15",
                  "goal_minus_xg_away_l15"):
        pred_kwargs.pop(extra, None)
    # Sprint D7-E: propagate threshold overrides only if provided.
    if value_threshold_pp is not None:
        pred_kwargs["value_threshold_pp"] = value_threshold_pp
    if strong_threshold_pp is not None:
        pred_kwargs["strong_threshold_pp"] = strong_threshold_pp
    return compute_draw_potential(**pred_kwargs)


def _predict_over15(features: dict) -> dict:
    return compute_over15_potential(
        xg_home_l5=features.get("xg_home_l5"),
        xg_away_l5=features.get("xg_away_l5"),
        goal_avg_for_home=features.get("goal_avg_for_home"),
        goal_avg_for_away=features.get("goal_avg_for_away"),
        goal_avg_against_home=features.get("goal_avg_against_home"),
        goal_avg_against_away=features.get("goal_avg_against_away"),
        is_group_stage=features.get("is_group_stage", False),
    )


def _predict_score_grid(features: dict) -> dict:
    """Sprint-D7-F · Full Dixon-Coles score grid potential.

    Used by BOTH _predict_over25 and _predict_under25 so that they
    share the exact same grid (and therefore the same τ-correction
    applied to low-score cells), while each downstream consumer reads
    its OWN cell-sum from the verdict — not the complement.
    """
    return compute_score_grid_potential(
        xg_home_l5=features.get("xg_home_l5"),
        xg_away_l5=features.get("xg_away_l5"),
        goal_avg_for_home=features.get("goal_avg_for_home"),
        goal_avg_for_away=features.get("goal_avg_for_away"),
        goal_avg_against_home=features.get("goal_avg_against_home"),
        goal_avg_against_away=features.get("goal_avg_against_away"),
    )


def _predict_over25(features: dict) -> dict:
    return _predict_score_grid(features)


def _predict_under25(features: dict) -> dict:
    return _predict_score_grid(features)


def _predict_double_chance(features: dict) -> dict:
    """Helper that computes a 1X2 + DC payload; the engine picks
    the relevant DC variant downstream."""
    draw_v = _predict_draw(features)
    dc = compute_double_chance_potential(
        elo_home=features.get("elo_home"),
        elo_away=features.get("elo_away"),
        draw_probability_pct=draw_v.get("draw_probability"),
    )
    # Stitch the draw-potential audit so reason codes are accessible.
    dc["_draw_audit"] = {
        "draw_probability":   draw_v.get("draw_probability"),
        "draw_reason_codes":  draw_v.get("reason_codes"),
    }
    return dc


def _extract_prob_pct(verdict: dict, market: str) -> Optional[float]:
    if market == "DRAW":
        return verdict.get("draw_probability")
    if market == "OVER_1_5":
        return verdict.get("over15_probability")
    if market == "OVER_2_5":
        return verdict.get("over25_probability")
    if market == "UNDER_2_5":
        return verdict.get("under25_probability")
    if market == "DOUBLE_CHANCE_HD":
        return verdict.get("p_home_or_draw_pct")
    if market == "DOUBLE_CHANCE_AD":
        return verdict.get("p_away_or_draw_pct")
    if market == "DOUBLE_CHANCE_HA":
        return verdict.get("p_home_or_away_pct")
    return None


def _store_prob_pct(verdict: dict, market: str, pct: float) -> None:
    """Apply a calibrated probability back into the verdict dict (the
    same field we just read with ``_extract_prob_pct``)."""
    if market == "DRAW":
        verdict["draw_probability"] = round(pct, 1)
    elif market == "OVER_1_5":
        verdict["over15_probability"] = round(pct, 1)
    elif market == "OVER_2_5":
        verdict["over25_probability"] = round(pct, 2)
    elif market == "UNDER_2_5":
        verdict["under25_probability"] = round(pct, 2)
    elif market == "DOUBLE_CHANCE_HD":
        verdict["p_home_or_draw_pct"] = round(pct, 2)
    elif market == "DOUBLE_CHANCE_AD":
        verdict["p_away_or_draw_pct"] = round(pct, 2)
    elif market == "DOUBLE_CHANCE_HA":
        verdict["p_home_or_away_pct"] = round(pct, 2)


# Sprint-D7-F · Market-aware adapters: which row field carries the
# canonical odd, which feature carries the market-implied probability,
# and which markets fully support market-aware (with-odds) backtests.
_MARKET_ODD_FIELD: dict[str, str] = {
    "DRAW":      "odd_draw",
    "OVER_2_5":  "odd_over25",
    "UNDER_2_5": "odd_under25",
}
_MARKET_IMPLIED_FEATURE: dict[str, str] = {
    "DRAW":      "market_implied_draw_prob",
    "OVER_2_5":  "market_implied_over25_prob",
    "UNDER_2_5": "market_implied_under25_prob",
}
MARKET_AWARE_SUPPORTED: tuple[str, ...] = ("DRAW", "OVER_2_5", "UNDER_2_5")


def _relabel_for_market(verdict: dict, market: str) -> dict:
    """Assign label from absolute probability thresholds (no-market)."""
    pp = _extract_prob_pct(verdict, market)
    if pp is None:
        return verdict
    th = NO_MARKET_THRESHOLDS.get(market, NO_MARKET_THRESHOLDS["DRAW"])
    if market == "DRAW":
        # Preserve back-compat label set.
        if pp >= th["STRONG"]:
            verdict["label"] = LABEL_STRONG_VALUE
        elif pp >= th["VALUE"]:
            verdict["label"] = LABEL_VALUE_DRAW
        elif pp >= th["FAIR"]:
            verdict["label"] = LABEL_FAIR_DRAW
        else:
            verdict["label"] = LABEL_NO_VALUE
    else:
        if pp >= th["STRONG"]:
            verdict["label"] = LABEL_STRONG_VALUE_GENERIC
        elif pp >= th["VALUE"]:
            verdict["label"] = LABEL_VALUE_GENERIC
        elif pp >= th["FAIR"]:
            verdict["label"] = LABEL_FAIR_GENERIC
        else:
            verdict["label"] = LABEL_NO_VALUE_GENERIC
    return verdict


# Market specs: (predictor, hit_fn).
_MARKET_SPECS: dict[str, dict] = {
    "DRAW":              {"predictor": _predict_draw,          "hit_fn": _hit_draw},
    "OVER_1_5":          {"predictor": _predict_over15,        "hit_fn": _hit_over15},
    "OVER_2_5":          {"predictor": _predict_over25,        "hit_fn": _hit_over25},
    "UNDER_2_5":         {"predictor": _predict_under25,       "hit_fn": _hit_under25},
    "DOUBLE_CHANCE_HD":  {"predictor": _predict_double_chance, "hit_fn": _hit_hd},
    "DOUBLE_CHANCE_AD":  {"predictor": _predict_double_chance, "hit_fn": _hit_ad},
    "DOUBLE_CHANCE_HA":  {"predictor": _predict_double_chance, "hit_fn": _hit_ha},
}

SUPPORTED_MARKETS: tuple[str, ...] = tuple(_MARKET_SPECS.keys())


def _relabel_no_market(verdict: dict) -> dict:
    """Sprint-D2 · Back-compat alias (DRAW market). Sprint-D3 introduces
    ``_relabel_for_market`` for multi-market support."""
    return _relabel_for_market(verdict, "DRAW")


def _isotonic_like_calibrator(history: list[tuple[float, int]]):
    """Return a *very* lightweight monotonic calibrator from
    (predicted_prob, outcome) pairs. Sufficient for backtests; the
    production path uses the dedicated ``football_draw_potential_calibrator``
    when wired.

    The fit is a per-decile mean: at predict time we look up the bucket
    of the predicted probability and return the empirical hit rate.
    Falls back to identity when buckets are sparse.
    """
    if not history:
        return lambda p: p
    buckets: list[list[float]] = [[] for _ in range(10)]
    for p, y in history:
        idx = max(0, min(9, int(p * 10)))
        buckets[idx].append(float(y))
    bucket_means = [
        (sum(b) / len(b)) if b else None for b in buckets
    ]
    # Build a monotonic shape by averaging with neighbours where empty.
    for i, m in enumerate(bucket_means):
        if m is None:
            # Look left/right for nearest filled buckets.
            left  = next((bucket_means[k] for k in range(i - 1, -1, -1)
                          if bucket_means[k] is not None), None)
            right = next((bucket_means[k] for k in range(i + 1, 10)
                          if bucket_means[k] is not None), None)
            if left is not None and right is not None:
                bucket_means[i] = (left + right) / 2.0
            elif left is not None:
                bucket_means[i] = left
            elif right is not None:
                bucket_means[i] = right
    return lambda p: float(bucket_means[max(0, min(9, int(float(p) * 10)))]
                            if bucket_means[max(0, min(9, int(float(p) * 10)))] is not None
                            else p)


def _kelly_fraction(p: float, decimal_odd: float) -> float:
    """Kelly criterion fractional stake (clamped to [0, 0.10])."""
    b = decimal_odd - 1.0
    if b <= 0:
        return 0.0
    f = (p * (b + 1) - 1) / b
    return max(0.0, min(0.10, f))


# Sprint-D6 · Probability clamps for the (post-shrinkage) calibrator.
# Without clamps a noisy small-sample empirical rate could push the
# calibrated probability outside ``(0, 1)`` after rounding.
PROB_MIN: float = 0.001
PROB_MAX: float = 0.999

# Sprint-D6 · Default Bayesian shrinkage K. ``None`` = legacy behaviour
# (pure isotonic, no shrinkage). Tests/operators pass an int K to
# enable the additional shrinkage layer.
DEFAULT_SHRINKAGE_K: Optional[int] = None


def _shrinkage_weight(n: int, K: int) -> float:
    """Bayesian shrinkage weight ``w = n / (n + K)``. Returns 0 for an
    empty sample, monotonically growing toward 1 as ``n`` increases."""
    if n <= 0 or K <= 0:
        return 0.0
    return float(n) / (float(n) + float(K))


def _empirical_observed_rate(history: list[tuple[float, int]]) -> Optional[float]:
    """Global empirical hit rate over the full calibration history."""
    if not history:
        return None
    return sum(int(y) for _, y in history) / float(len(history))


def run_backtest(
    matches_sorted: list[dict],
    *,
    market: str = "DRAW",
    min_edge_pp: float = 4.0,
    use_calibration: bool = False,
    walk_forward: bool = True,
    stake: str = "flat",
    stake_unit: float = 1.0,
    recal_every: int = DEFAULT_RECAL_EVERY,
    no_market: bool = False,
    min_pred_prob_pp: float = 30.0,
    min_history_per_team: Optional[int] = None,
    # ── Sprint-D6 additions (opt-in; defaults preserve legacy) ────────
    shrinkage_K: Optional[int] = DEFAULT_SHRINKAGE_K,
    predictor_override: Optional[Callable[[dict], dict]] = None,
) -> dict:
    """Run the backtest. Returns a dict with picks + summary.

    Parameters
    ----------
    matches_sorted
        Output of ``parse_football_data_csv`` or
        ``parse_openfootball_json`` (sorted ascending by date).
    market
        Currently only ``"DRAW"`` is implemented (Sprint-D MVP).
    min_edge_pp
        Minimum predicted-vs-implied edge (in **percentage points**)
        required to fire a pick. Default 4pp matches the VALUE
        threshold in ``football_draw_potential``.
    use_calibration
        When True, every ``recal_every`` picks the calibrator refits
        on PAST settled picks and is applied to subsequent
        predictions.
    walk_forward
        When True (default), calibration uses only past picks.
    stake
        ``"flat"`` or ``"kelly_fractional"``.
    no_market
        Sprint-D2 mode for datasets WITHOUT odds (e.g. openfootball
        WC2022 / Euro2024). In this mode:
          * picks fire on ``draw_probability >= min_pred_prob_pp``
            instead of edge-vs-market.
          * stake/PnL/ROI are not computed (set to 0); the engine
            focuses on calibration + hit-rate of the label.
          * every match with sufficient history gets a *prediction*
            row in ``predictions`` (so calibration curve covers the
            full sample), but only those firing the threshold appear
            in ``picks``.
    min_pred_prob_pp
        Minimum predicted draw probability (in pp) for a pick to fire
        in ``no_market`` mode. Default 30pp ≈ "above league baseline".
    """
    if market not in SUPPORTED_MARKETS:
        raise NotImplementedError(
            f"Unsupported market={market!r}. "
            f"Supported: {SUPPORTED_MARKETS}"
        )
    # Market-aware (with-odds) mode now supports DRAW, OVER_2_5 and
    # UNDER_2_5. Other markets still fall back to no_market=True.
    if not no_market and market not in MARKET_AWARE_SUPPORTED:
        raise NotImplementedError(
            f"Market-aware mode (no_market=False) supports "
            f"{MARKET_AWARE_SUPPORTED!r}. Got market={market!r}. "
            f"Use no_market=True for calibration-only backtests on "
            f"{market!r}."
        )

    spec = _MARKET_SPECS[market]
    base_predictor: Callable[[dict], dict] = (predictor_override
                                                if predictor_override is not None
                                                else spec["predictor"])
    # Sprint D7-E: derive an effective "strong" threshold so the
    # downstream label re-derivation stays consistent.
    # Default: keep legacy 8.0 unless caller overrode min_edge_pp above
    # it, in which case strong = 2 × min_edge_pp (capped at 12.0).
    if min_edge_pp > 8.0:
        _effective_strong_pp = min(12.0, 2.0 * min_edge_pp)
    else:
        _effective_strong_pp = 8.0
    # Wrap the DRAW predictor so it receives the threshold overrides
    # only when relevant (other markets ignore them).
    if market == "DRAW" and predictor_override is None:
        def predictor(features: dict) -> dict:   # noqa: E306
            return _predict_draw(
                features,
                value_threshold_pp=min_edge_pp,
                strong_threshold_pp=_effective_strong_pp,
            )
    else:
        predictor = base_predictor
    hit_fn:    Callable[[dict], bool] = spec["hit_fn"]
    # Default firing threshold derives from market spec when caller
    # did not override.
    if min_pred_prob_pp == 30.0 and market != "DRAW":
        # The 30.0 sentinel means "caller used the back-compat default";
        # switch to the market-appropriate threshold.
        min_pred_prob_pp = NO_MARKET_THRESHOLDS[market]["DEFAULT_FIRING"]

    picks: list[dict] = []
    predictions: list[dict] = []
    calib_history: list[tuple[float, int]] = []   # (pred_prob, hit) settled so far
    calibrator = (lambda p: p)
    skipped:  list[dict] = []

    # Default min_history_per_team:
    #   * League datasets (market mode) → 3 (default, matches Sprint-D
    #     behaviour for back-compat).
    #   * Tournament datasets (no_market mode) → 1, because each team
    #     plays at most 3 group games + knockouts; requiring 3 prior
    #     would discard the entire group stage.
    if min_history_per_team is None:
        min_history_per_team = 1 if no_market else 3

    for i, m in enumerate(matches_sorted):
        try:
            features = build_point_in_time_features(matches_sorted, i)
        except Exception as exc:   # noqa: BLE001
            skipped.append({"index": i, "reason": f"ingest_error:{exc}"})
            continue

        # Need at least N prior matches per team for any signal.
        if (features["_audit"]["home_hist_n"] < min_history_per_team
                or features["_audit"]["away_hist_n"] < min_history_per_team):
            skipped.append({"index": i, "reason": "insufficient_history"})
            continue
        if not no_market:
            implied_feat = _MARKET_IMPLIED_FEATURE.get(market)
            if implied_feat is None or features.get(implied_feat) is None:
                reason = ("no_draw_odd"
                          if market == "DRAW"
                          else f"no_odd_for_{market.lower()}")
                skipped.append({"index": i, "reason": reason})
                continue

        # Sprint-D4 · Walk-forward calibration audit.
        # Compute the strict-prior calibration window and the audit
        # block PRIOR to the prediction, so it reflects exactly what
        # the calibrator was allowed to see at decision-time.
        target_date = m["date"]
        max_calib_date = None
        for j in range(i - 1, -1, -1):
            if matches_sorted[j]["date"] < target_date:
                max_calib_date = matches_sorted[j]["date"]
                break
        calibration_audit = {
            "n_calib_matches":      i,
            "n_calib_picks_seen":   len(calib_history),
            "max_calib_date":       (max_calib_date.isoformat()
                                      if max_calib_date else None),
            "target_date":          target_date.isoformat(),
            "walk_forward":         bool(walk_forward),
            "use_calibration":      bool(use_calibration),
            "leakage_check_passed": (max_calib_date is None
                                      or max_calib_date < target_date),
        }

        # Predict for the configured market.
        verdict = predictor(features)

        # Sprint-D7-F · For market-aware non-DRAW markets, derive
        # ``market_implied`` / ``edge`` / ``label`` from the pre-calib
        # probability so the downstream gate and metrics machinery
        # work uniformly. DRAW retains its own legacy path inside
        # ``compute_draw_potential``.
        if (not no_market and market in MARKET_AWARE_SUPPORTED
                and market != "DRAW"):
            implied_feat = _MARKET_IMPLIED_FEATURE[market]
            implied_prob = features.get(implied_feat)
            prob_pct_pre = _extract_prob_pct(verdict, market)
            if implied_prob is not None and prob_pct_pre is not None:
                market_pct = implied_prob * 100.0
                edge_pre   = round(prob_pct_pre - market_pct, 2)
                verdict["market_implied"] = round(market_pct, 2)
                verdict["edge"]           = edge_pre
                if edge_pre >= _effective_strong_pp:
                    verdict["label"] = LABEL_STRONG_VALUE_GENERIC
                elif edge_pre >= min_edge_pp:
                    verdict["label"] = LABEL_VALUE_GENERIC
                elif edge_pre >= 0:
                    verdict["label"] = LABEL_FAIR_GENERIC
                else:
                    verdict["label"] = LABEL_NO_VALUE_GENERIC

        # No-market mode: reassign label using absolute-probability
        # thresholds (since edge-vs-market is undefined here).
        if no_market:
            verdict = _relabel_for_market(verdict, market)

        # Apply walk-forward calibration if enabled.
        prob_pct = _extract_prob_pct(verdict, market)
        if use_calibration and prob_pct is not None and calib_history:
            try:
                # Capa 1 (legacy): isotonic-like per-decile mean.
                base_prob       = float(prob_pct) / 100.0
                iso_calibrated  = float(calibrator(base_prob))
                calibrated_prob = iso_calibrated

                # Capa 2 (Sprint-D6 · opt-in): Bayesian shrinkage.
                # Mezcla ``base`` con ``iso`` según ``w = n / (n + K)``.
                # Con poca muestra (w≈0) la predicción se mantiene
                # cerca del base prior; con mucha muestra (w→1) se
                # acerca al estimador empírico isotonic.
                shrinkage_w   = None
                observed_rate = _empirical_observed_rate(calib_history)
                if shrinkage_K is not None and shrinkage_K > 0:
                    shrinkage_w = _shrinkage_weight(
                        len(calib_history), shrinkage_K,
                    )
                    calibrated_prob = (shrinkage_w * iso_calibrated
                                        + (1.0 - shrinkage_w) * base_prob)

                # Clamp final probability to (PROB_MIN, PROB_MAX).
                clamped_prob = max(PROB_MIN, min(PROB_MAX, calibrated_prob))

                # Audit: surface every input/output of the calibrator
                # so Sprint-D6 tests can prove the layer is non-trivial.
                calibration_audit["shrinkage_K"]      = shrinkage_K
                calibration_audit["calib_weight"]     = (
                    round(shrinkage_w, 6) if shrinkage_w is not None else None
                )
                calibration_audit["base_prob"]        = round(base_prob, 6)
                calibration_audit["iso_calibrated"]   = round(iso_calibrated, 6)
                calibration_audit["calibrated_prob"]  = round(clamped_prob, 6)
                calibration_audit["observed_rate"]    = (
                    round(observed_rate, 6) if observed_rate is not None else None
                )
                calibration_audit["clamped"]          = (
                    abs(clamped_prob - calibrated_prob) > 1e-12
                )

                calibrated_pct = clamped_prob * 100.0
                _store_prob_pct(verdict, market, calibrated_pct)
                # For DRAW market we keep the legacy edge re-derivation.
                if market == "DRAW":
                    market_pct = verdict.get("market_implied")
                    if market_pct is not None:
                        new_edge = round(calibrated_pct - market_pct, 1)
                        verdict["edge"]             = new_edge
                        # Sprint D7-E: use the same thresholds applied
                        # to the pre-calibration label (NOT hardcoded
                        # 4.0/8.0).
                        if new_edge >= _effective_strong_pp:
                            verdict["label"] = LABEL_STRONG_VALUE
                        elif new_edge >= min_edge_pp:
                            verdict["label"] = LABEL_VALUE_DRAW
                        elif new_edge >= 0:
                            verdict["label"] = "FAIR_DRAW_NO_EDGE"
                        else:
                            verdict["label"] = "NO_DRAW_VALUE"
                    elif no_market:
                        verdict = _relabel_for_market(verdict, market)
                elif no_market:
                    verdict = _relabel_for_market(verdict, market)
            except Exception as exc:  # noqa: BLE001
                log.debug("calibration failed: %s", exc)

        # Decision: fire a pick.
        label = verdict.get("label")
        edge  = verdict.get("edge")
        prob_pct_final = _extract_prob_pct(verdict, market)
        hit = hit_fn(m)
        if no_market:
            # Fire on predicted probability threshold (no odds available).
            fires = (prob_pct_final is not None
                     and prob_pct_final >= min_pred_prob_pp)
        else:
            # Sprint-D7-F · gate now accepts both legacy DRAW labels and
            # the GENERIC labels used by other market-aware markets.
            fires = (
                label in (LABEL_VALUE_DRAW, LABEL_STRONG_VALUE,
                            LABEL_VALUE_GENERIC, LABEL_STRONG_VALUE_GENERIC)
                and edge is not None and edge >= min_edge_pp
            )

        # Always record a "prediction" row so the calibration curve
        # covers the full sample. Sprint-D7-G: emit in BOTH no_market
        # and market-aware modes (previously only no_market). Records
        # carry all the fields required by the calibration diagnostics
        # module (model prob, raw and de-vigged market implied for the
        # ACTIVE side, plus opening / closing odds for CLV).
        if prob_pct_final is not None:
            # Resolve the canonical odd field for this market and
            # extract opening / closing odds when available.
            odd_field = _MARKET_ODD_FIELD.get(market)
            odd_open = odd_close = odd_canonical = None
            if odd_field:
                odd_canonical = m.get(odd_field)
                odd_open      = m.get(odd_field + "_open") or m.get(odd_field)
                odd_close     = m.get(odd_field + "_close")
            # Raw market implied for this side (1 / odd_open).
            mk_raw_open   = (1.0 / odd_open)  if (odd_open  and odd_open  > 1.0) else None
            mk_raw_close  = (1.0 / odd_close) if (odd_close and odd_close > 1.0) else None
            # 2-way de-vig for OVER/UNDER markets.
            mk_devig_open  = None
            mk_devig_close = None
            if market in ("OVER_2_5", "UNDER_2_5"):
                # The opposite-side odd to compute the 2-way overround.
                opp_field = ("odd_under25" if market == "OVER_2_5"
                              else "odd_over25")
                oo_open  = m.get(opp_field + "_open") or m.get(opp_field)
                oo_close = m.get(opp_field + "_close")
                if (odd_open and odd_open > 1.0
                        and oo_open and oo_open > 1.0):
                    p_self  = 1.0 / odd_open
                    p_other = 1.0 / oo_open
                    mk_devig_open = p_self / (p_self + p_other)
                if (odd_close and odd_close > 1.0
                        and oo_close and oo_close > 1.0):
                    p_self  = 1.0 / odd_close
                    p_other = 1.0 / oo_close
                    mk_devig_close = p_self / (p_self + p_other)
            elif market == "DRAW":
                # 3-way de-vig using home / draw / away odds.
                oh_o = m.get("odd_home_open") or m.get("odd_home")
                od_o = m.get("odd_draw_open") or m.get("odd_draw")
                oa_o = m.get("odd_away_open") or m.get("odd_away")
                if (oh_o and od_o and oa_o
                        and min(oh_o, od_o, oa_o) > 1.0):
                    p_h = 1.0 / oh_o; p_d = 1.0 / od_o; p_a = 1.0 / oa_o
                    ovr = p_h + p_d + p_a
                    mk_devig_open = p_d / ovr if ovr > 0 else None
                oh_c = m.get("odd_home_close")
                od_c = m.get("odd_draw_close")
                oa_c = m.get("odd_away_close")
                if (oh_c and od_c and oa_c
                        and min(oh_c, od_c, oa_c) > 1.0):
                    p_h = 1.0 / oh_c; p_d = 1.0 / od_c; p_a = 1.0 / oa_c
                    ovr = p_h + p_d + p_a
                    mk_devig_close = p_d / ovr if ovr > 0 else None
            predictions.append({
                "date":           m["date"].isoformat(),
                "competition":    m.get("competition") or "",
                "home":           m["home_team"],
                "away":           m["away_team"],
                "market":         market,
                "predicted_prob": round(prob_pct_final / 100.0, 6),
                "label":          label,
                "edge_pp":        edge,
                "tournament_phase": m.get("tournament_phase"),
                "matchday":         m.get("matchday"),
                "group_label":      m.get("group_label"),
                "is_group_stage":   bool(m.get("is_group_stage", False)),
                "hit":            hit,
                "actual_score":   f"{m['fthg']}-{m['ftag']}",
                "fired":          bool(fires),
                # Sprint-D7-G fields used by calibration diagnostics.
                "odd":            odd_canonical,
                "odd_open":       odd_open,
                "odd_close":      odd_close,
                "market_implied_raw":         (round(mk_raw_open, 6)
                                                  if mk_raw_open is not None else None),
                "market_implied_raw_close":   (round(mk_raw_close, 6)
                                                  if mk_raw_close is not None else None),
                "market_implied_devig":       (round(mk_devig_open, 6)
                                                  if mk_devig_open is not None else None),
                "market_implied_devig_close": (round(mk_devig_close, 6)
                                                  if mk_devig_close is not None else None),
                "tournament_context_score":
                    features.get("tournament_context_score"),
                "_calibration_audit": calibration_audit,
            })

        if fires:
            if no_market:
                # No odds → no PnL/stake/ROI. Record a "pick" row for
                # hit-rate by label.
                feat_snapshot = {
                    "elo_home":              features.get("elo_home"),
                    "elo_away":              features.get("elo_away"),
                    "xg_home_l5":            features.get("xg_home_l5"),
                    "xg_away_l5":            features.get("xg_away_l5"),
                    "goal_avg_against_home": features.get("goal_avg_against_home"),
                    "goal_avg_against_away": features.get("goal_avg_against_away"),
                    "tournament_context_score": features.get("tournament_context_score"),
                }
                picks.append({
                    "date":           m["date"].isoformat(),
                    "competition":    m.get("competition") or "",
                    "home":           m["home_team"],
                    "away":           m["away_team"],
                    "market":         market,
                    "odd_draw":       None,
                    "predicted_prob": round(prob_pct_final / 100.0, 4),
                    "market_prob":    None,
                    "edge_pp":        None,
                    "label":          label,
                    "stake":          0.0,
                    "hit":            hit,
                    "pnl":            0.0,
                    "actual_score":   f"{m['fthg']}-{m['ftag']}",
                    "tournament_phase": m.get("tournament_phase"),
                    "matchday":         m.get("matchday"),
                    "group_label":      m.get("group_label"),
                    "is_group_stage":   bool(m.get("is_group_stage", False)),
                    "tournament_context_score":
                        features.get("tournament_context_score"),
                    "_features":      feat_snapshot,
                    "_calibration_audit": calibration_audit,
                })
            else:
                # Sprint-D7-F · Resolve the canonical odd field for
                # the active market (DRAW → odd_draw, OVER_2_5 →
                # odd_over25, etc.).
                odd_field = _MARKET_ODD_FIELD.get(market, "odd_draw")
                odd = m.get(odd_field)
                if odd is None or odd <= 1.0:
                    # Defensive: skip if the canonical odd is missing
                    # or degenerate (1.0 implies arb-killer; never
                    # happens in practice but we guard anyway).
                    continue
                prob_used = prob_pct_final / 100.0
                if stake == "kelly_fractional":
                    f = _kelly_fraction(prob_used, odd)
                    stake_amt = stake_unit * f
                else:
                    stake_amt = stake_unit
                if stake_amt > 0:
                    pnl = (odd - 1.0) * stake_amt if hit else -stake_amt
                    # Sprint-D4 — propagate per-row warnings from the
                    # ingestor (e.g. ODDS_ARE_CLOSING_BACKTEST_OPTIMISTIC).
                    row_warnings = list(m.get("warnings") or [])
                    # Sprint-D5 — attach a minimal feature snapshot for
                    # downstream cohort detection.
                    feat_snapshot = {
                        "elo_home":              features.get("elo_home"),
                        "elo_away":              features.get("elo_away"),
                        "xg_home_l5":            features.get("xg_home_l5"),
                        "xg_away_l5":            features.get("xg_away_l5"),
                        "goal_avg_against_home": features.get("goal_avg_against_home"),
                        "goal_avg_against_away": features.get("goal_avg_against_away"),
                        "tournament_context_score": features.get("tournament_context_score"),
                    }
                    pick_row = {
                        "date":           m["date"].isoformat(),
                        "competition":    m.get("competition") or "",
                        "home":           m["home_team"],
                        "away":           m["away_team"],
                        "market":         market,
                        "odd":            odd,
                        # Back-compat: legacy DRAW callers still expect
                        # the ``odd_draw`` field on every pick row.
                        "odd_draw":       (odd if market == "DRAW" else None),
                        "odds_type":      m.get("odds_type"),
                        "predicted_prob": round(prob_used, 4),
                        "market_prob":    round(verdict["market_implied"] / 100.0, 4),
                        "edge_pp":        edge,
                        "label":          label,
                        "stake":          round(stake_amt, 4),
                        "hit":            hit,
                        "pnl":            round(pnl, 4),
                        "actual_score":   f"{m['fthg']}-{m['ftag']}",
                        "warnings":       row_warnings,
                        "_features":      feat_snapshot,
                        "_calibration_audit": calibration_audit,
                    }
                    picks.append(pick_row)

        # Update calibrator history AFTER the pick is recorded.
        # This is the anti-leakage step: future picks may see THIS
        # pick's outcome, but the current pick used calibrator fitted
        # only on STRICTLY past picks.
        if prob_pct_final is not None:
            calib_history.append((prob_pct_final / 100.0,
                                   1 if hit else 0))
            if (use_calibration and walk_forward
                    and (len(calib_history) % recal_every == 0)):
                calibrator = _isotonic_like_calibrator(calib_history)

    return {
        "market":           market,
        "min_edge_pp":      min_edge_pp,
        "stake_mode":       stake,
        "use_calibration":  use_calibration,
        "walk_forward":     walk_forward,
        "no_market":        no_market,
        "min_pred_prob_pp": min_pred_prob_pp,
        "picks":            picks,
        "predictions":      predictions,
        "skipped":          skipped,
        "n_matches_total":  len(matches_sorted),
    }


__all__ = [
    "run_backtest",
    "_isotonic_like_calibrator",
    "_kelly_fraction",
    "_relabel_no_market",
    "_relabel_for_market",
    "_extract_prob_pct",
    "_store_prob_pct",
    "_predict_draw", "_predict_over15", "_predict_double_chance",
    "_hit_draw", "_hit_over15", "_hit_hd", "_hit_ad", "_hit_ha",
    "SUPPORTED_MARKETS",
    "NO_MARKET_THRESHOLDS",
    "NO_MARKET_STRONG_VALUE_PP",
    "NO_MARKET_VALUE_PP",
    "NO_MARKET_FAIR_PP",
    "LABEL_STRONG_VALUE_GENERIC",
    "LABEL_VALUE_GENERIC",
    "LABEL_FAIR_GENERIC",
    "LABEL_NO_VALUE_GENERIC",
    "DEFAULT_RECAL_EVERY",
]
