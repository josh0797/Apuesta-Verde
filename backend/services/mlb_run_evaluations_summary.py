"""Calibration view for ``mlb_run_evaluations``.

Aggregates settled evaluations into hit-rate breakdowns useful for
tuning the explosive engine, detecting drift **AND** measuring the
new Moneyball pipeline (Market Selection, Pressure Base, Script
Survival, Fragility, Sabermetrics, Ghost Edges, Pattern Memory,
Manual Odds Review, F5-vs-Full-Game Under outcomes).

Pure async service — no HTTP concerns. The endpoint at
``GET /api/mlb/run-evaluations/summary`` calls
:func:`compute_run_evaluations_summary` directly.

Filtering rules
---------------
* Only documents with ``result in ("won", "lost", "push")`` are counted
  in the headline buckets — ``pending`` is excluded by definition and
  ``void`` is treated as a legacy backward-compat input only (it never
  reaches new settles).
* The default window is 30 days. Callers can override via ``days``.
* The default ``user_id`` is ``"_slate"`` because that is the cohort
  the orchestrator writes pregame evaluations under. Individual user
  IDs can be passed for per-user views.

Sections of the response
------------------------
Legacy (kept for backward compat):
    ``by_risk_tier``, ``by_flip``, ``by_market_scope``, ``by_miss_type``,
    ``high_conservative_won_anyway``, ``reference_profile_activations``,
    ``dynamic_park_blocks``, ``central_under_vetoes``, ``park_blocks_saved``.

Moneyball (new, fail-soft — empty buckets when data missing):
    ``by_market_selected``
    ``by_pressure_environment``
    ``by_script_survival``
    ``by_fragility_tier``
    ``by_sabermetrics_edge``
    ``by_ghost_edge``
    ``f5_vs_full_game_under``
    ``manual_odds_review_outcomes``
    ``pattern_memory_performance``
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .mlb_run_storage import REFERENCE_MLB_POWER_BAT_EXPLOSIVE

log = logging.getLogger("mlb_run_evaluations_summary")

SETTLED_OUTCOMES = ("won", "lost", "push")

# Canonical buckets for Moneyball breakdowns (always emitted, even with 0 docs).
MARKET_SELECTED_BUCKETS = (
    "Moneyline",
    "Run Line -1.5",
    "Run Line +1.5",
    "F5 Under",
    "Full Game Under",
    "F5 Over",
    "Full Game Over",
    "Team Total Over",
    "Team Total Under",
    "NRFI",
    "YRFI",
    "Watchlist",
    "Manual Odds Review",
)

PRESSURE_ENVIRONMENT_BUCKETS = (
    "LOW_PRESSURE",
    "MODERATE_PRESSURE",
    "HIGH_PRESSURE",
    "CHAOTIC_PRESSURE",
)

SCRIPT_SURVIVAL_BUCKETS = (
    "HIGH_SURVIVAL",
    "MEDIUM_SURVIVAL",
    "LOW_SURVIVAL",
)

FRAGILITY_TIER_BUCKETS = (
    "LOW",
    "MEDIUM",
    "HIGH",
)

SABERMETRICS_EDGE_BUCKETS = (
    "OPS_EDGE_HOME",
    "OPS_EDGE_AWAY",
    "FIP_EDGE_HOME",
    "FIP_EDGE_AWAY",
    "WAR_EDGE_HOME",
    "WAR_EDGE_AWAY",
    "NEUTRAL",
)

GHOST_EDGE_BUCKETS = (
    "ERA_UNDERSTATES_RISK",
    "ERA_OVERSTATES_RISK",
    "PITCHER_XWOBA_WARNING",
    "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
    "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────
def _hit_rate_bucket(subset: list[dict]) -> dict:
    """Build a {total, won, lost, push, hit_rate} stats block."""
    total = len(subset)
    if total == 0:
        return {"total": 0, "won": 0, "lost": 0, "push": 0, "hit_rate": None}
    won  = sum(1 for d in subset if d.get("result") == "won")
    lost = sum(1 for d in subset if d.get("result") == "lost")
    push = sum(1 for d in subset if d.get("result") == "push")
    return {
        "total":   total,
        "won":     won,
        "lost":    lost,
        "push":    push,
        "hit_rate": round((won / total) * 100, 2),
    }


def _get_nested(d: dict, *path: str, default: Any = None) -> Any:
    """Safely walk a nested dict."""
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _fragility_tier_from_score(score: Any) -> Optional[str]:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 70:
        return "HIGH"
    if s >= 40:
        return "MEDIUM"
    return "LOW"


def _survival_tier_from_score(score: Any) -> Optional[str]:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s >= 65:
        return "HIGH_SURVIVAL"
    if s >= 40:
        return "MEDIUM_SURVIVAL"
    return "LOW_SURVIVAL"


def _extract_recommended_market(doc: dict) -> Optional[str]:
    """Pull the final recommended market from the eval doc.

    Looks in (priority order):
      1. ``market_selection.recommended_market``
      2. ``recommendation.market``
      3. ``market`` (legacy flat field)
    """
    ms = _get_nested(doc, "market_selection", default={})
    if isinstance(ms, dict) and ms.get("recommended_market"):
        return str(ms["recommended_market"])
    rec_market = _get_nested(doc, "recommendation", "market")
    if rec_market:
        return str(rec_market)
    flat = doc.get("market")
    return str(flat) if flat else None


def _extract_pressure_tier(doc: dict) -> Optional[str]:
    tier = _get_nested(doc, "pressure_base", "combined", "pressure_tier")
    if isinstance(tier, str):
        return tier
    return None


def _extract_script_survival_tier(doc: dict) -> Optional[str]:
    score = (
        _get_nested(doc, "script_survival_score", "score")
        or _get_nested(doc, "_mlb_script_v5", "survival", "score")
        or doc.get("script_survival_score")
        or doc.get("script_survival")
    )
    return _survival_tier_from_score(score)


def _extract_fragility_tier(doc: dict) -> Optional[str]:
    # Prefer the contract block, then flat fields, then nested.
    tier = _get_nested(doc, "fragility_score", "tier")
    if tier in FRAGILITY_TIER_BUCKETS:
        return tier
    score = (
        _get_nested(doc, "fragility_score", "score")
        or _get_nested(doc, "fragility", "score")
        or doc.get("fragility_score")
    )
    return _fragility_tier_from_score(score)


def _extract_sabermetrics_edges(doc: dict) -> Iterable[str]:
    """Return zero or more sabermetrics edge buckets the doc matches."""
    saber = _get_nested(doc, "sabermetrics", default={}) or {}
    if not isinstance(saber, dict) or not saber.get("available"):
        return ()
    edges = saber.get("match_edges") or {}
    if not isinstance(edges, dict):
        return ()
    out: list[str] = []
    # Each edge can be: {"side": "home|away", "score": ..., "tier": ...}
    for stat_name, payload in edges.items():
        if not isinstance(payload, dict):
            continue
        side = (payload.get("side") or "").lower()
        stat_upper = stat_name.upper()
        if side in ("home", "away") and stat_upper in ("OPS", "FIP", "WAR"):
            out.append(f"{stat_upper}_EDGE_{side.upper()}")
    return out or ("NEUTRAL",)


def _extract_ghost_edge_flags(doc: dict) -> Iterable[str]:
    """Return the list of ghost-edge flags fired on this eval doc."""
    ge = doc.get("ghost_edges") or {}
    if isinstance(ge, dict) and ge.get("available"):
        flags = ge.get("flags") or []
        if isinstance(flags, list):
            return [f for f in flags if f in GHOST_EDGE_BUCKETS]
    # Fallback: read raw discrepancies.
    discrepancies = _get_nested(doc, "model_verification", "discrepancies",
                                  default=[]) or []
    flags: list[str] = []
    for d in discrepancies:
        if isinstance(d, dict) and d.get("flag") in GHOST_EDGE_BUCKETS:
            flags.append(d["flag"])
    return flags


def _market_is_under(market: Optional[str]) -> bool:
    if not market:
        return False
    m = market.lower()
    return "under" in m and "team total" not in m


def _market_is_f5(market: Optional[str]) -> bool:
    return bool(market and "f5" in market.lower())


def _market_is_full_game(market: Optional[str]) -> bool:
    return bool(market and "full game" in market.lower())


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def compute_run_evaluations_summary(db,
                                            *,
                                            days: int = 30,
                                            user_id: str = "_slate",
                                            ) -> dict:
    """Compute calibration breakdowns for the last ``days``.

    Args
    ----
    db        : Motor database handle.
    days      : Lookback window. Capped at 365.
    user_id   : Cohort selector. Default ``"_slate"`` (orchestrator
                pregame writes). Pass a user UUID for per-user views.

    Returns
    -------
    dict with both legacy AND Moneyball breakdowns.
    """
    capped_days = max(1, min(365, int(days or 30)))
    cutoff_iso = (datetime.now(timezone.utc)
                  - timedelta(days=capped_days)).isoformat()

    # ── Base settled set ──────────────────────────────────────────
    settled_filter = {
        "user_id":      user_id,
        "sport":        "baseball",
        "generated_at": {"$gte": cutoff_iso},
        "result":       {"$in": list(SETTLED_OUTCOMES)},
    }
    settled_docs = await db.mlb_run_evaluations.find(
        settled_filter, {"_id": 0}
    ).to_list(length=10000)

    overall = _hit_rate_bucket(settled_docs)

    # ── LEGACY breakdowns (unchanged) ─────────────────────────────
    by_risk_tier = {
        tier: _hit_rate_bucket(
            [d for d in settled_docs if d.get("risk_tier") == tier]
        )
        for tier in ("HIGH", "MEDIUM", "LOW")
    }

    by_flip = {
        "flip_true":  _hit_rate_bucket(
            [d for d in settled_docs if d.get("flip_triggered") is True]
        ),
        "flip_false": _hit_rate_bucket(
            [d for d in settled_docs if d.get("flip_triggered") is False]
        ),
    }

    market_scopes = sorted({
        d.get("market_scope") for d in settled_docs if d.get("market_scope")
    })
    by_market_scope = {
        sc: _hit_rate_bucket(
            [d for d in settled_docs if d.get("market_scope") == sc]
        )
        for sc in market_scopes
    }

    miss_types = sorted({
        d.get("miss_type") for d in settled_docs if d.get("miss_type")
    })
    by_miss_type = {
        mt: _hit_rate_bucket(
            [d for d in settled_docs if d.get("miss_type") == mt]
        )
        for mt in miss_types
    }

    high_no_rec = [
        d for d in settled_docs
        if d.get("risk_tier") == "HIGH"
        and not bool(d.get("should_recommend"))
    ]
    high_no_rec_won = sum(1 for d in high_no_rec if d.get("result") == "won")
    high_conservative_won_anyway = {
        "total":    len(high_no_rec),
        "won":      high_no_rec_won,
        "hit_rate": (round((high_no_rec_won / len(high_no_rec)) * 100, 2)
                      if high_no_rec else None),
    }

    reference_activations = sum(
        1 for d in settled_docs
        if d.get("reference_profile_tag") == REFERENCE_MLB_POWER_BAT_EXPLOSIVE
    )

    veto_filter = {
        "user_id":      user_id,
        "sport":        "baseball",
        "generated_at": {"$gte": cutoff_iso},
    }
    veto_docs = await db.mlb_run_evaluations.find(
        veto_filter,
        {"_id": 0, "veto_source": 1, "result": 1, "blocked_market": 1,
         "explosive_risk_score": 1, "risk_tier": 1},
    ).to_list(length=10000)

    dynamic_park_blocks = sum(
        1 for d in veto_docs if d.get("veto_source") == "DYNAMIC_PARK_OFFENSIVE"
    )
    central_under_vetoes = sum(
        1 for d in veto_docs if d.get("veto_source") == "CENTRAL_UNDER_VETO"
    )
    park_blocks_saved = sum(
        1 for d in settled_docs
        if d.get("veto_source") == "DYNAMIC_PARK_OFFENSIVE"
        and d.get("blocked_market")
        and "under" in (d.get("blocked_market") or "").lower()
        and d.get("result") == "lost"
    )

    # ── MONEYBALL BREAKDOWNS ──────────────────────────────────────
    by_market_selected = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if _extract_recommended_market(d) == bucket
        ])
        for bucket in MARKET_SELECTED_BUCKETS
    }

    by_pressure_environment = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if _extract_pressure_tier(d) == bucket
        ])
        for bucket in PRESSURE_ENVIRONMENT_BUCKETS
    }

    by_script_survival = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if _extract_script_survival_tier(d) == bucket
        ])
        for bucket in SCRIPT_SURVIVAL_BUCKETS
    }

    by_fragility_tier = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if _extract_fragility_tier(d) == bucket
        ])
        for bucket in FRAGILITY_TIER_BUCKETS
    }

    by_sabermetrics_edge = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if bucket in list(_extract_sabermetrics_edges(d))
        ])
        for bucket in SABERMETRICS_EDGE_BUCKETS
    }

    by_ghost_edge = {
        bucket: _hit_rate_bucket([
            d for d in settled_docs
            if bucket in list(_extract_ghost_edge_flags(d))
        ])
        for bucket in GHOST_EDGE_BUCKETS
    }

    # ── F5 Under vs Full Game Under reconciliation ────────────────
    f5_vs_full_game_under = _compute_f5_vs_full_game_under(settled_docs)

    # ── Manual odds review outcomes ───────────────────────────────
    manual_odds_review_outcomes = _compute_manual_odds_outcomes(settled_docs)

    # ── Pattern memory performance (from mlb_pattern_memory) ──────
    pattern_memory_performance = await _compute_pattern_memory_performance(db)

    # ── Totals dispersion calibration (NB feedback loop) ──────────
    # Inspect settled docs with an `expected_total` + `actual_total` to
    # estimate the empirical variance/mean ratio. If we have enough
    # samples (≥30), we expose a *suggested* dispersion_ratio so the
    # operator can update MLB_TOTALS_DISPERSION_RATIO with confidence.
    totals_dispersion = _compute_totals_dispersion_calibration(settled_docs)

    return {
        "ok":           True,
        "window_days":  capped_days,
        "user_id":      user_id,
        "evaluated_total":         overall["total"],
        "overall":                 overall,
        # ── Legacy fields (untouched) ──
        "by_risk_tier":            by_risk_tier,
        "by_flip":                 by_flip,
        "by_market_scope":         by_market_scope,
        "by_miss_type":            by_miss_type,
        "high_conservative_won_anyway": high_conservative_won_anyway,
        "reference_profile_activations": reference_activations,
        "dynamic_park_blocks":     dynamic_park_blocks,
        "park_blocks_saved":       park_blocks_saved,
        "central_under_vetoes":    central_under_vetoes,
        "settled_outcomes_filter": list(SETTLED_OUTCOMES),
        # ── Moneyball breakdowns ──
        "by_market_selected":          by_market_selected,
        "by_pressure_environment":     by_pressure_environment,
        "by_script_survival":          by_script_survival,
        "by_fragility_tier":           by_fragility_tier,
        "by_sabermetrics_edge":        by_sabermetrics_edge,
        "by_ghost_edge":               by_ghost_edge,
        "f5_vs_full_game_under":       f5_vs_full_game_under,
        "manual_odds_review_outcomes": manual_odds_review_outcomes,
        "pattern_memory_performance":  pattern_memory_performance,
        "totals_dispersion_calibration": totals_dispersion,
        # Schema version (UI can branch on this)
        "summary_schema_version":  "moneyball.2",
    }


# ─────────────────────────────────────────────────────────────────────
# Sub-computations
# ─────────────────────────────────────────────────────────────────────
def _compute_f5_vs_full_game_under(docs: list[dict]) -> dict:
    """Cross-tabulate F5 Under vs Full Game Under outcomes.

    Buckets:
      * f5_won_full_game_lost  — F5 Under won and Full Game Under lost
        (bullpen broke under) — measured PER GAME via game_pk.
      * full_game_won          — Full Game Under won.
      * bullpen_broke_under    — explicit "BULLPEN_BROKE_UNDER" flag
        present, OR derived (f5 won + full lost).

    The cross-tab is only computed for games where BOTH markets were
    settled (we look up by ``game_pk``). Games with only one of the two
    are excluded.
    """
    out = {
        "f5_won_full_game_lost":   {"total": 0, "examples": []},
        "full_game_won":           {"total": 0},
        "bullpen_broke_under":     {"total": 0},
        "games_with_both_markets": 0,
    }
    by_game: dict[str, dict[str, str]] = {}
    for d in docs:
        market = _extract_recommended_market(d) or d.get("market") or ""
        if not _market_is_under(market):
            continue
        result = d.get("result")
        if result not in ("won", "lost"):
            continue
        game_pk = str(d.get("game_pk") or d.get("match_id") or "") or None
        if not game_pk:
            continue
        slot = by_game.setdefault(game_pk, {})
        if _market_is_f5(market):
            slot["f5"] = result
        elif _market_is_full_game(market):
            slot["full"] = result

    for game_pk, slots in by_game.items():
        f5 = slots.get("f5")
        fg = slots.get("full")
        if f5 and fg:
            out["games_with_both_markets"] += 1
            if f5 == "won" and fg == "lost":
                out["f5_won_full_game_lost"]["total"] += 1
                out["bullpen_broke_under"]["total"] += 1
                exmps = out["f5_won_full_game_lost"]["examples"]
                if len(exmps) < 5:
                    exmps.append(game_pk)
            elif fg == "won":
                out["full_game_won"]["total"] += 1
    return out


def _compute_manual_odds_outcomes(docs: list[dict]) -> dict:
    """How structural-lean / manual-odds picks performed."""
    out = {
        "structural_lean_confirmed":     {"total": 0, "won": 0},
        "structural_lean_failed":        {"total": 0, "lost": 0},
        "no_odds_available_at_settle":   {"total": 0},
        "manual_review_required_total":  0,
    }
    for d in docs:
        mor = d.get("manual_odds_review") or {}
        if isinstance(mor, dict) and mor.get("required"):
            out["manual_review_required_total"] += 1
            res = d.get("result")
            reason = mor.get("reason") or ""
            if "structural_lean" in reason:
                if res == "won":
                    out["structural_lean_confirmed"]["total"] += 1
                    out["structural_lean_confirmed"]["won"]   += 1
                elif res == "lost":
                    out["structural_lean_failed"]["total"] += 1
                    out["structural_lean_failed"]["lost"]  += 1
            if "no_odds" in reason or "no_engine_odds" in reason:
                out["no_odds_available_at_settle"]["total"] += 1
    return out


async def _compute_pattern_memory_performance(db) -> list[dict]:
    """Read the mlb_pattern_memory collection and surface canonical rows.

    Returns at most 50 rows, sorted by sample_size desc. Fail-soft: an
    empty list is returned when the collection doesn't exist or DB is
    None (so the summary endpoint doesn't crash on a fresh install).
    """
    if db is None:
        return []
    try:
        cursor = db["mlb_pattern_memory"].find({}, {"_id": 0})
        docs = await cursor.to_list(length=200)
    except Exception as exc:
        log.debug("mlb_pattern_memory read failed: %s", exc)
        return []
    rows: list[dict] = []
    for d in docs:
        sample_size = int(d.get("sample_size") or 0)
        hit_rate    = d.get("hit_rate")
        roi         = d.get("roi")
        best_market = d.get("best_market")
        # Pick a worst_market from the ledger if it exists.
        worst_market = None
        ledger = d.get("market_ledger") or {}
        if isinstance(ledger, dict):
            worst_score = 1e9
            for mname, m in ledger.items():
                if not isinstance(m, dict):
                    continue
                samples = int(m.get("samples") or 0)
                if samples < 5:
                    continue
                hr = (m.get("wins", 0) / samples) if samples else 0
                stake = float(m.get("stake") or 0.0)
                mroi  = ((float(m.get("payout") or 0.0) - stake) / stake) \
                        if stake > 0 else 0
                score = hr * (1 + max(0.0, mroi))
                if score < worst_score:
                    worst_score = score
                    worst_market = mname
        rows.append({
            "pattern_key":  d.get("pattern_key"),
            "sample_size":  sample_size,
            "hit_rate":     hit_rate,
            "ROI":          roi,
            "best_market":  best_market,
            "worst_market": worst_market,
            "updated_at":   d.get("updated_at"),
        })
    rows.sort(key=lambda r: int(r.get("sample_size") or 0), reverse=True)
    return rows[:50]


# ─────────────────────────────────────────────────────────────────────
# Totals dispersion calibration (Negative-Binomial feedback loop)
# ─────────────────────────────────────────────────────────────────────
def _compute_totals_dispersion_calibration(docs: list[dict]) -> dict:
    """Estimate the empirical variance/mean ratio for MLB total runs.

    Drives the Negative-Binomial model in ``mlb_pregame_analytics_v2``:
    if the empirical ratio drifts away from the configured default
    (``MLB_TOTALS_DISPERSION_RATIO = 1.5``), this block surfaces the
    suggested value so the operator can update the constant or the
    feedback loop can read it dynamically.

    Reads each settled doc for ``expected_total`` (engine projection)
    and ``actual_total`` (final game score sum). When at least 30
    matched pairs exist, we compute:

        ratio = empirical_variance(actual - expected) / mean(expected)

    Returns a fail-soft dict with sample_size, ratio_estimate, and a
    confidence tier so the UI can render it without crashing.
    """
    pairs: list[tuple[float, float]] = []
    for d in docs:
        exp_total = (
            d.get("expected_total")
            or _get_nested(d, "totals_model", "lambda")
            or _get_nested(d, "smart_total_line", "expected_runs")
        )
        actual_total = (
            d.get("actual_total")
            or d.get("final_total_runs")
            or _get_nested(d, "final_score", "total")
        )
        if exp_total is None or actual_total is None:
            continue
        try:
            e = float(exp_total)
            a = float(actual_total)
        except (TypeError, ValueError):
            continue
        if e <= 0 or a < 0:
            continue
        pairs.append((e, a))

    sample_size = len(pairs)
    if sample_size < 30:
        return {
            "available":         False,
            "sample_size":       sample_size,
            "reason":            "insufficient_samples",
            "min_samples_required": 30,
            "current_default":   1.5,
        }

    mean_exp = sum(e for e, _ in pairs) / sample_size
    if mean_exp <= 0:
        return {
            "available":         False,
            "sample_size":       sample_size,
            "reason":            "non_positive_mean",
            "current_default":   1.5,
        }
    residuals = [(a - e) for e, a in pairs]
    mean_res  = sum(residuals) / sample_size
    var_res   = sum((x - mean_res) ** 2 for x in residuals) / max(1, sample_size - 1)
    # The empirical variance of the *actual* totals is what we want,
    # but using the residual variance is a stable proxy that does not
    # depend on the absolute level of expected_total. We add back the
    # variance contributed by the expected_total spread.
    exp_var   = sum((e - mean_exp) ** 2 for e, _ in pairs) / max(1, sample_size - 1)
    total_var = var_res + exp_var
    raw_ratio = total_var / mean_exp if mean_exp > 0 else 1.0

    # Clamp to a realistic empirical range so a noisy small sample never
    # produces wild updates.
    suggested = max(1.0, min(2.5, raw_ratio))
    if sample_size >= 200:
        confidence_tier = "VALIDATED"
    elif sample_size >= 100:
        confidence_tier = "USEFUL"
    else:
        confidence_tier = "LOW_SAMPLE"

    return {
        "available":             True,
        "sample_size":           sample_size,
        "mean_expected_total":   round(mean_exp, 3),
        "empirical_variance":    round(total_var, 3),
        "raw_ratio":             round(raw_ratio, 3),
        "suggested_ratio":       round(suggested, 3),
        "current_default":       1.5,
        "confidence_tier":       confidence_tier,
        "recommendation":        _dispersion_recommendation(suggested),
    }


def _dispersion_recommendation(suggested: float) -> str:
    if 1.4 <= suggested <= 1.6:
        return "default_ok"
    if suggested < 1.4:
        return "tighten_dispersion_lower"
    return "loosen_dispersion_higher"


__all__ = [
    "compute_run_evaluations_summary",
    "SETTLED_OUTCOMES",
    "MARKET_SELECTED_BUCKETS",
    "PRESSURE_ENVIRONMENT_BUCKETS",
    "SCRIPT_SURVIVAL_BUCKETS",
    "FRAGILITY_TIER_BUCKETS",
    "SABERMETRICS_EDGE_BUCKETS",
    "GHOST_EDGE_BUCKETS",
]
