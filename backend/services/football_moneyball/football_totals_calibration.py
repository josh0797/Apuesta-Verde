"""Football Totals Calibration — feedback loop for Dixon-Coles rho AND
conditional NB dispersion ratio.

Mirror of ``mlb_run_evaluations_summary`` but football-specific:
  * `rho` controls the four DC low-score cells (joint dependence)
  * `dispersion_ratio` controls the per-side NB widening (high-tail)

Calibration rules (codified, NOT documented only):
  • Global n < 100  → return defaults (rho=-0.05, ratio=1.0).
    Sample is too small to be trusted; both knobs stay at safe defaults.
    The empirical values are still surfaced as `empirical` for visibility
    but `to_apply` mirrors the default and `global_applies` is False.
  • Global n ≥ 100  → apply empirical global values, clamped.
  • Bucket level    → always OBSERVE_ONLY until that bucket has n≥100 of
    its own settled docs. Even when a bucket reaches 100, the
    orchestrator MUST keep applying the global value — bucket-specific
    application is opt-in via the `apply_eligible` flag and intended
    for the next iteration.

Conservative caps (NEVER widen Over by NB alone):
  • rho clamped to [-0.20, 0.0]
  • dispersion_ratio clamped to [1.0, 2.0]

Fail-soft: any error → ``{"available": False, "reason": ...}``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

log = logging.getLogger("football_moneyball.totals_calibration")

# Mirrored from statsbomb_features (kept here to avoid circular import).
DIXON_COLES_RHO_DEFAULT_REF = -0.05
GOALS_DISPERSION_RATIO_DEFAULT_REF = 1.0

# Gates (per user spec, more conservative than initial draft).
_MIN_SAMPLES_GLOBAL_APPLY  = 100   # n<100 → defaults
_MIN_SAMPLES_BUCKET_APPLY  = 100   # bucket eligibility (still observe-only by default)
_RHO_CLAMP   = (-0.20, 0.0)
_RATIO_CLAMP = (1.0, 2.0)

LEAGUE_TIER_BUCKETS = ("TIER1", "TIER2", "TIER3", "UNKNOWN_LEAGUE")
OFFENSE_BUCKETS     = ("LOW_OFFENSE", "MODERATE_OFFENSE", "HIGH_OFFENSE")


def _empirical_rho(docs: list[dict]) -> Optional[float]:
    """Estimate rho from observed vs Poisson-predicted low-score
    frequency. Each settled doc carries final_score and the legacy
    Poisson `p_under_*_poisson` recorded at pick time."""
    low_obs = 0
    n = 0
    for d in docs:
        fs = d.get("final_score") or {}
        h, a = fs.get("home"), fs.get("away")
        if h is None or a is None:
            continue
        try:
            h, a = int(h), int(a)
        except (TypeError, ValueError):
            continue
        n += 1
        # Low-score cells in DC's correction radius.
        if (h, a) in ((0, 0), (1, 0), (0, 1), (1, 1)):
            low_obs += 1
    if n < _MIN_SAMPLES_GLOBAL_APPLY:
        return None
    obs_rate = low_obs / n
    # Heuristic: each 5pp of low-score rate above Poisson baseline
    # (~0.38 for typical lambdas) maps to ~ -0.03 of rho. The mapping is
    # rough — only used as a starting point; the recommendation field
    # tells the operator whether to apply.
    baseline = 0.38
    rho_est = -0.03 * ((obs_rate - baseline) / 0.05)
    return round(max(_RHO_CLAMP[0], min(_RHO_CLAMP[1], rho_est)), 4)


def _empirical_ratio(docs: list[dict]) -> Optional[float]:
    """Estimate variance / mean of TOTAL goals across settled docs."""
    totals: list[int] = []
    for d in docs:
        fs = d.get("final_score") or {}
        h, a = fs.get("home"), fs.get("away")
        if h is None or a is None:
            continue
        try:
            totals.append(int(h) + int(a))
        except (TypeError, ValueError):
            continue
    if len(totals) < _MIN_SAMPLES_GLOBAL_APPLY:
        return None
    mean = sum(totals) / len(totals)
    if mean <= 0:
        return None
    var = sum((t - mean) ** 2 for t in totals) / max(1, len(totals) - 1)
    return round(max(_RATIO_CLAMP[0], min(_RATIO_CLAMP[1], var / mean)), 3)


def _tier_for(n: int) -> str:
    if n >= 200:
        return "VALIDATED"
    if n >= 100:
        return "USEFUL"
    return "LOW_SAMPLE"


def _league_tier_of(doc: dict) -> str:
    return (doc.get("league_tier") or "UNKNOWN_LEAGUE")


def _offense_of(doc: dict) -> str:
    return (doc.get("offense_bucket") or "MODERATE_OFFENSE")


def _bucket_block(subset: list[dict]) -> dict:
    bn = len(subset)
    return {
        "sample_size":          bn,
        "empirical_rho":        _empirical_rho(subset),
        "empirical_ratio":      _empirical_ratio(subset),
        "confidence_tier":      _tier_for(bn),
        "apply_eligible":       bn >= _MIN_SAMPLES_BUCKET_APPLY,
        "mode":                 "OBSERVE_ONLY",  # always observe-only
        "samples_until_apply":  max(0, _MIN_SAMPLES_BUCKET_APPLY - bn),
    }


def _rho_recommendation(rho: Optional[float]) -> str:
    if rho is None:
        return "insufficient_samples"
    if -0.08 <= rho <= -0.02:
        return "default_ok"
    if rho < -0.08:
        return "strengthen_dc_more_negative"
    return "weaken_dc_toward_zero"


def _ratio_recommendation(ratio: Optional[float]) -> str:
    if ratio is None:
        return "insufficient_samples"
    if ratio <= 1.1:
        return "poisson_ok_no_overdispersion"
    return "consider_nb_widening"


async def compute_football_totals_calibration(
    db, *, days: int = 90, user_id: str = "_slate",
) -> dict:
    """Build the calibration summary used by the orchestrator + UI.

    Fail-soft: any error returns ``{"available": False, ...}``.

    Args:
        db:       Motor / async Mongo handle (or None → defaults).
        days:     Lookback window for settled results (default 90).
        user_id:  Cohort key. Use ``_slate`` for live picks, or
                  ``_slate_backtest`` for the backtest runner.
    """
    if db is None:
        return {
            "available": False,
            "reason": "db_unavailable",
            "rho": {
                "current_default": DIXON_COLES_RHO_DEFAULT_REF,
                "empirical":       None,
                "to_apply":        DIXON_COLES_RHO_DEFAULT_REF,
                "recommendation":  "insufficient_samples",
            },
            "dispersion_ratio": {
                "current_default": GOALS_DISPERSION_RATIO_DEFAULT_REF,
                "empirical":       None,
                "to_apply":        GOALS_DISPERSION_RATIO_DEFAULT_REF,
                "recommendation":  "insufficient_samples",
            },
        }
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, min(365, int(days))))
        ).isoformat()
        q: dict[str, Any] = {"settled_at": {"$gte": cutoff}}
        if user_id:
            q["user_id"] = user_id
        # NOTE: football_market_results uses `settled_at` (see warehouse).
        # We also filter `sport` when present (back-compat: older rows may
        # not carry the field).
        cursor = db.football_market_results.find(q, {"_id": 0})
        docs: list[dict] = []
        async for d in cursor:
            docs.append(d)
        n = len(docs)

        global_rho_empirical   = _empirical_rho(docs)
        global_ratio_empirical = _empirical_ratio(docs)

        # ── Codified rule: global needs ≥100 samples to APPLY empirical ──
        # Under that, return defaults. The empirical values are still
        # surfaced for visibility but not marked as "apply".
        global_applies = n >= _MIN_SAMPLES_GLOBAL_APPLY
        rho_to_apply = (
            global_rho_empirical
            if (global_applies and global_rho_empirical is not None)
            else DIXON_COLES_RHO_DEFAULT_REF
        )
        ratio_to_apply = (
            global_ratio_empirical
            if (global_applies and global_ratio_empirical is not None)
            else GOALS_DISPERSION_RATIO_DEFAULT_REF
        )

        # Buckets — always OBSERVE_ONLY until each reaches 100 of its own.
        by_league = {
            b: _bucket_block([d for d in docs if _league_tier_of(d) == b])
            for b in LEAGUE_TIER_BUCKETS
        }
        by_offense = {
            b: _bucket_block([d for d in docs if _offense_of(d) == b])
            for b in OFFENSE_BUCKETS
        }

        eligible: list[str] = []
        for dim_name, dim in (("league", by_league), ("offense", by_offense)):
            for k, v in dim.items():
                if v.get("apply_eligible"):
                    eligible.append(f"{dim_name}.{k}")

        return {
            "available":       True,
            "sample_size":     n,
            "confidence_tier": _tier_for(n),
            "global_applies":  global_applies,
            "rho": {
                "current_default": DIXON_COLES_RHO_DEFAULT_REF,
                "empirical":       global_rho_empirical,
                "to_apply":        rho_to_apply,
                "recommendation":  _rho_recommendation(global_rho_empirical),
            },
            "dispersion_ratio": {
                "current_default": GOALS_DISPERSION_RATIO_DEFAULT_REF,
                "empirical":       global_ratio_empirical,
                "to_apply":        ratio_to_apply,
                "recommendation":  _ratio_recommendation(global_ratio_empirical),
            },
            "by_league_tier":   by_league,
            "by_offense":       by_offense,
            "bucket_application_policy": {
                "mode": "OBSERVE_ONLY",
                "buckets_eligible_for_apply": eligible,
                "min_samples_per_bucket": _MIN_SAMPLES_BUCKET_APPLY,
                "reason": (
                    "Parámetros por bucket calculados pero NO aplicados "
                    "hasta n≥100 propios. Solo rho/ratio globales aplican "
                    "y solo cuando global n≥100."
                ),
            },
            "_schema":       "football_totals_calibration.1",
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "lookback_days": int(days),
            "user_id":       user_id,
        }
    except Exception as exc:
        log.warning("compute_football_totals_calibration failed: %s", exc)
        return {"available": False, "reason": str(exc)[:200]}


# ────────────────────────────────────────────────────────────────────────────
# Public wiring helper (Pieza 5.1) — used by analyst_engine to propagate
# the calibrated rho/ratio onto every football match before
# `compute_match_features` is called.
# ────────────────────────────────────────────────────────────────────────────
def apply_calibration_to_match(match: dict, calibration: dict | None) -> dict:
    """Mutates ``match`` to set ``_dc_rho`` + ``_goals_dispersion_ratio``.

    Pure / fail-soft: if calibration is missing or unavailable, returns
    the match untouched (so `compute_match_features` uses defaults).
    """
    if not isinstance(match, dict):
        return match
    if not isinstance(calibration, dict) or not calibration.get("available"):
        return match
    rho_block = calibration.get("rho") or {}
    ratio_block = calibration.get("dispersion_ratio") or {}
    rho = rho_block.get("to_apply", DIXON_COLES_RHO_DEFAULT_REF)
    ratio = ratio_block.get("to_apply", GOALS_DISPERSION_RATIO_DEFAULT_REF)
    if isinstance(rho, (int, float)):
        match["_dc_rho"] = float(rho)
    if isinstance(ratio, (int, float)):
        match["_goals_dispersion_ratio"] = float(ratio)
    return match


__all__ = [
    "compute_football_totals_calibration",
    "apply_calibration_to_match",
    "DIXON_COLES_RHO_DEFAULT_REF",
    "GOALS_DISPERSION_RATIO_DEFAULT_REF",
    "LEAGUE_TIER_BUCKETS",
    "OFFENSE_BUCKETS",
]
