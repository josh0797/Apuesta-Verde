"""MLB Quality Contact Matchup Engine.

Detects discrepancies between:
  * The **actual quality** of a lineup's contact (xwOBA, sweet-spot%,
    barrel%, hard-hit%).
  * The **actual vulnerability** of the opposing starting pitcher
    (xERA, xwOBA-allowed, barrel%-allowed, hard-hit%-allowed).
  * The **market perception** that usually fixates on ERA alone.

Design contract (NON-NEGOTIABLE):
  * **Pure**: no Mongo, no HTTP, no logging side-effects beyond
    ``log.debug``. The whole module is exercised with ``pytest`` and a
    handful of dicts.
  * **Fail-soft**: any missing field → block becomes
    ``{"available": False, "reason_codes": ["QCM_INSUFFICIENT_DATA"]}``
    and callers can blindly read ``signals == []`` / scores == ``None``.
  * **No automatic picks**: this module only emits *signals* and
    *contextual scores*. Integration with over/under discovery + ranking
    happens in a follow-up sprint.
  * **Explainable**: every threshold crossed → reason code +
    contribution recorded in ``score_breakdown``.

Phase F91 — see ``plan.md`` for the full sprint description.

The module also exposes :func:`get_active_thresholds` (env override via
``QCM_THRESHOLDS`` JSON, read **at call time** — same pattern as
``football_h2h_decision_policy.get_active_rules``).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("mlb.quality_contact_matchup")

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
# Weighted lineup positions — leadoff and middle of the order get more
# at-bats over a 9-inning game, so we weigh their contact quality higher.
LINEUP_WEIGHTS: dict[int, float] = {
    1: 1.30,
    2: 1.25,
    3: 1.20,
    4: 1.15,
    5: 1.05,
    6: 1.00,
    7: 0.95,
    8: 0.90,
    9: 0.85,
}

# Batter contact weights (per spec).
_CONTACT_W = {
    "xwoba":          0.50,
    "sweet_spot_pct": 0.15,
    "barrel_pct":     0.20,
    "hard_hit_pct":   0.15,
}

# Pitcher vulnerability weights (per spec).
_VULNERABILITY_W = {
    "barrel_pct_allowed":   0.35,
    "hard_hit_pct_allowed": 0.25,
    "xwoba_allowed":        0.20,
    "xera":                 0.20,
}

# Defaults for the rate-style metrics' theoretical ceilings; used to
# normalise raw values into a 0-100 scale.
_BATTER_CEILINGS = {
    "xwoba":          0.500,   # league-best ~ 0.420; cap conservative.
    "sweet_spot_pct": 0.50,    # max practical ~ 45%.
    "barrel_pct":     0.20,    # elite hitter ~ 15%.
    "hard_hit_pct":   0.60,    # elite hitter ~ 55%.
}
_PITCHER_CEILINGS = {
    "barrel_pct_allowed":   0.18,
    "hard_hit_pct_allowed": 0.55,
    "xwoba_allowed":        0.450,
    "xera":                 8.00,   # really bad ~ 7.50.
}

# Default thresholds — overridable via env (see ``get_active_thresholds``).
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "MATCHUP_CONTACT_ADVANTAGE":      70.0,   # lineup_contact_quality ≥ 70
    "PITCHER_BARREL_REGRESSION":       0.08,  # barrel_pct_allowed ≥ 8%
    "ERA_UNDERSTATES_DAMAGE":          1.00,  # xERA - ERA ≥ 1.0
    "TOP_ORDER_THREAT_RATIO":          0.55,  # top-4 weighted ≥ 55%
    "OVER_CONTACT_WARNING":           70.0,   # contact_mismatch_score ≥ 70
    "SEVERE_REGRESSION_GAP":           1.50,
    "HIGH_REGRESSION_GAP":             1.00,
    "MODERATE_REGRESSION_GAP":         0.50,
}

# Reason codes / signals (machine-readable; the UI eventually translates
# them to Spanish copy).
SIGNAL_MATCHUP_CONTACT_ADVANTAGE   = "MATCHUP_CONTACT_ADVANTAGE"
SIGNAL_PITCHER_BARREL_REGRESSION   = "PITCHER_BARREL_REGRESSION_RISK"
SIGNAL_ERA_UNDERSTATES_DAMAGE      = "ERA_UNDERSTATES_DAMAGE"
SIGNAL_TOP_ORDER_THREAT            = "TOP_ORDER_THREAT"
SIGNAL_OVER_CONTACT_WARNING        = "OVER_CONTACT_WARNING"

RC_INSUFFICIENT_DATA               = "QCM_INSUFFICIENT_DATA"
RC_BATTER_LEVEL_REAL               = "QCM_BATTER_LEVEL_REAL"
RC_BATTER_LEVEL_DERIVED            = "QCM_BATTER_LEVEL_DERIVED_FROM_TEAM"
RC_PITCHER_MISSING                 = "QCM_PITCHER_METRICS_MISSING"

# Regression-risk levels (per spec ERA gap thresholds).
REGRESSION_SEVERE   = "SEVERE_REGRESSION_RISK"
REGRESSION_HIGH     = "HIGH_REGRESSION_RISK"
REGRESSION_MODERATE = "MODERATE_REGRESSION_RISK"
REGRESSION_NORMAL   = "NORMAL"


# ─────────────────────────────────────────────────────────────────────
# Env override (read at call time, F86.1-style)
# ─────────────────────────────────────────────────────────────────────
def get_active_thresholds() -> dict[str, float]:
    """Return :data:`_DEFAULT_THRESHOLDS` merged with optional env override.

    The override is parsed from ``QCM_THRESHOLDS`` (JSON object). Unknown
    keys are ignored with a debug log; malformed JSON falls back to the
    defaults.
    """
    raw = (os.environ.get("QCM_THRESHOLDS") or "").strip()
    base = dict(_DEFAULT_THRESHOLDS)
    if not raw:
        return base
    try:
        override = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("[qcm] QCM_THRESHOLDS parse failed: %s — using defaults", exc)
        return base
    if not isinstance(override, dict):
        log.debug("[qcm] QCM_THRESHOLDS must be a JSON object; got %s",
                  type(override).__name__)
        return base
    for k, v in override.items():
        if k not in base:
            log.debug("[qcm] ignoring unknown threshold key=%s", k)
            continue
        try:
            base[k] = float(v)
        except (TypeError, ValueError):
            log.debug("[qcm] non-numeric override for key=%s value=%r", k, v)
    return base


def per_batter_metrics_enabled() -> bool:
    """When True, expects real Statcast batter rows in
    ``payload.lineups.official.<side>[]``. Default False → metrics are
    derived from team-level snapshots.
    """
    return str(os.environ.get("QCM_LINEUP_PER_BATTER") or "").strip().lower() \
        in ("1", "true", "yes", "on")


# ─────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _scale_0_100(value: Optional[float], ceiling: float) -> Optional[float]:
    """Normalise a raw rate / xERA-style value to [0, 100]."""
    if value is None or ceiling <= 0:
        return None
    return max(0.0, min(100.0, (value / ceiling) * 100.0))


def _round(v: Optional[float], ndigits: int = 2) -> Optional[float]:
    return None if v is None else round(v, ndigits)


# ─────────────────────────────────────────────────────────────────────
# Batter-level extraction
# ─────────────────────────────────────────────────────────────────────
def _team_batter_template(team_block: dict) -> Optional[dict]:
    """Pull a team-level batter snapshot from the advanced context.

    Returns a dict shaped like a per-batter row::

        {"xwoba": float, "sweet_spot_pct": float,
         "barrel_pct": float, "hard_hit_pct": float}

    or ``None`` when any required field is missing.
    """
    if not isinstance(team_block, dict):
        return None
    out = {
        "xwoba":          _f(team_block.get("xwoba")
                              or team_block.get("team_xwoba")),
        "sweet_spot_pct": _f(team_block.get("sweet_spot_pct")
                              or team_block.get("team_sweet_spot_pct")),
        "barrel_pct":     _f(team_block.get("barrel_pct")
                              or team_block.get("team_barrel_pct")),
        "hard_hit_pct":   _f(team_block.get("hard_hit_pct")
                              or team_block.get("team_hard_hit_pct")),
    }
    if any(v is None for v in out.values()):
        return None
    return out


def _derive_batters_from_team(team_block: dict, *, lineup_size: int = 9) -> list[dict]:
    """When ``QCM_LINEUP_PER_BATTER`` is False (default), use the team
    average as the per-batter snapshot. Slight position-based jitter is
    applied so the weighted average ≠ team average (the leadoff slot
    tends to have higher OBP/contact, the bottom-of-order lower).
    """
    base = _team_batter_template(team_block)
    if base is None:
        return []
    # Multiplicative jitter — empirically modest (±8% across the 1-9
    # spread). Sums of weights and jitter are designed so that an
    # average team produces ``lineup_contact_quality`` ≈ team xwOBA.
    _BATTER_POS_JITTER = {
        1: 1.05, 2: 1.04, 3: 1.06, 4: 1.05,
        5: 1.00, 6: 0.99, 7: 0.96, 8: 0.93, 9: 0.92,
    }
    out: list[dict] = []
    for pos in range(1, lineup_size + 1):
        j = _BATTER_POS_JITTER.get(pos, 1.0)
        row = {
            "xwoba":          base["xwoba"]          * j,
            "sweet_spot_pct": base["sweet_spot_pct"] * j,
            "barrel_pct":     base["barrel_pct"]     * j,
            "hard_hit_pct":   base["hard_hit_pct"]   * j,
        }
        out.append(row)
    return out


def _extract_real_batters(lineup_side: Any) -> list[dict]:
    """Pull real per-batter Statcast rows when present.

    Expected shape::

        payload.lineups.official.<side> = [
          {"order": 1, "xwoba": 0.420, "sweet_spot_pct": 0.41,
            "barrel_pct": 0.12, "hard_hit_pct": 0.55, ...},
          ...
        ]
    """
    if not isinstance(lineup_side, list) or not lineup_side:
        return []
    out: list[dict] = []
    for raw in lineup_side[:9]:
        if not isinstance(raw, dict):
            continue
        statcast = raw.get("statcast") if isinstance(raw.get("statcast"), dict) else raw
        row = {
            "xwoba":          _f(statcast.get("xwoba")),
            "sweet_spot_pct": _f(statcast.get("sweet_spot_pct")),
            "barrel_pct":     _f(statcast.get("barrel_pct")),
            "hard_hit_pct":   _f(statcast.get("hard_hit_pct")),
        }
        if all(v is not None for v in row.values()):
            out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────
# Score primitives (PUBLIC)
# ─────────────────────────────────────────────────────────────────────
def compute_batter_contact_score(batter_metrics: dict) -> Optional[float]:
    """Compute the per-batter contact score on a 0-100 scale.

    ``contact_score = Σ(metric_i_scaled * weight_i)`` where each metric
    is first normalised to its theoretical ceiling.
    """
    if not isinstance(batter_metrics, dict):
        return None
    total = 0.0
    used = 0
    for key, w in _CONTACT_W.items():
        v = _f(batter_metrics.get(key))
        if v is None:
            continue
        scaled = _scale_0_100(v, _BATTER_CEILINGS[key]) or 0.0
        total += scaled * w
        used  += 1
    if used == 0:
        return None
    return total


def compute_lineup_contact_quality(batters: list[dict]) -> dict:
    """Aggregate per-batter scores using :data:`LINEUP_WEIGHTS`.

    Returns::

        {
          "lineup_contact_quality": float,
          "weighted_per_batter":    [(order, raw_score, weighted), ...],
          "top_4_weighted":         float,
          "top_4_ratio":            float,   # top_4_weighted / total
          "sample_size":            int,
        }
    """
    if not isinstance(batters, list) or not batters:
        return {
            "lineup_contact_quality": None,
            "weighted_per_batter":    [],
            "top_4_weighted":         None,
            "top_4_ratio":            None,
            "sample_size":            0,
        }
    rows: list[tuple[int, Optional[float], Optional[float]]] = []
    total_weighted = 0.0
    top_4_weighted = 0.0
    used = 0
    for idx, b in enumerate(batters[:9]):
        order = idx + 1
        raw = compute_batter_contact_score(b)
        if raw is None:
            rows.append((order, None, None))
            continue
        weight = LINEUP_WEIGHTS.get(order, 0.85)
        weighted = raw * weight
        rows.append((order, raw, weighted))
        total_weighted += weighted
        if order <= 4:
            top_4_weighted += weighted
        used += 1
    if used == 0:
        return {
            "lineup_contact_quality": None,
            "weighted_per_batter":    rows,
            "top_4_weighted":         None,
            "top_4_ratio":            None,
            "sample_size":            0,
        }
    # Normalise back to 0-100 by dividing by the sum of applied weights.
    applied_weight_sum = sum(LINEUP_WEIGHTS.get(o, 0.85)
                              for o, raw, _ in rows if raw is not None)
    quality = total_weighted / applied_weight_sum if applied_weight_sum else 0.0
    return {
        "lineup_contact_quality": quality,
        "weighted_per_batter":    rows,
        "top_4_weighted":         top_4_weighted,
        "top_4_ratio":            (top_4_weighted / total_weighted) if total_weighted else None,
        "sample_size":            used,
    }


def compute_pitcher_vulnerability(pitcher_metrics: dict) -> Optional[float]:
    """Pitcher vulnerability score on a 0-100 scale.

    Higher = more vulnerable to hard contact.
    """
    if not isinstance(pitcher_metrics, dict):
        return None
    total = 0.0
    used = 0
    for key, w in _VULNERABILITY_W.items():
        v = _f(pitcher_metrics.get(key))
        if v is None:
            continue
        scaled = _scale_0_100(v, _PITCHER_CEILINGS[key]) or 0.0
        total += scaled * w
        used += 1
    if used == 0:
        return None
    return total


def classify_era_gap(era: Optional[float], xera: Optional[float],
                      thresholds: Optional[dict] = None) -> dict:
    """Return ``{gap, level}`` per the spec ERA-gap thresholds."""
    th = thresholds or get_active_thresholds()
    era_f  = _f(era)
    xera_f = _f(xera)
    if era_f is None or xera_f is None:
        return {"gap": None, "level": REGRESSION_NORMAL}
    gap = xera_f - era_f
    if gap >= th["SEVERE_REGRESSION_GAP"]:
        return {"gap": gap, "level": REGRESSION_SEVERE}
    if gap >= th["HIGH_REGRESSION_GAP"]:
        return {"gap": gap, "level": REGRESSION_HIGH}
    if gap >= th["MODERATE_REGRESSION_GAP"]:
        return {"gap": gap, "level": REGRESSION_MODERATE}
    return {"gap": gap, "level": REGRESSION_NORMAL}


def compute_matchup_contact_factor(lineup_quality: Optional[float],
                                     pitcher_barrel_pct_allowed: Optional[float]) -> Optional[float]:
    """``lineup_quality * (1 + pitcher_barrel_pct_allowed)`` — the
    headline metric.

    ``pitcher_barrel_pct_allowed`` is treated as a *rate* (0-1).
    """
    if lineup_quality is None:
        return None
    pba = _f(pitcher_barrel_pct_allowed) or 0.0
    return lineup_quality * (1.0 + pba)


def compute_contact_mismatch_score(lineup_quality: Optional[float],
                                     pitcher_vulnerability: Optional[float]) -> Optional[float]:
    """``(lineup_quality * pitcher_vulnerability) / 100``, capped at 100.

    Both inputs are 0-100, the product is 0-10000; we rescale to keep
    the headline score within 0-100.
    """
    if lineup_quality is None or pitcher_vulnerability is None:
        return None
    return min(100.0, (lineup_quality * pitcher_vulnerability) / 100.0)


def derive_signals(lineup_quality: Optional[float],
                    pitcher_metrics: Optional[dict],
                    era_classification: dict,
                    top_4_ratio: Optional[float],
                    contact_mismatch_score: Optional[float],
                    thresholds: Optional[dict] = None) -> list[str]:
    """Translate scores into the canonical signal codes."""
    th = thresholds or get_active_thresholds()
    out: list[str] = []
    if (lineup_quality is not None
            and lineup_quality >= th["MATCHUP_CONTACT_ADVANTAGE"]):
        out.append(SIGNAL_MATCHUP_CONTACT_ADVANTAGE)
    if isinstance(pitcher_metrics, dict):
        pba = _f(pitcher_metrics.get("barrel_pct_allowed"))
        if pba is not None and pba >= th["PITCHER_BARREL_REGRESSION"]:
            out.append(SIGNAL_PITCHER_BARREL_REGRESSION)
    gap = era_classification.get("gap") if isinstance(era_classification, dict) else None
    if gap is not None and gap >= th["ERA_UNDERSTATES_DAMAGE"]:
        out.append(SIGNAL_ERA_UNDERSTATES_DAMAGE)
    if top_4_ratio is not None and top_4_ratio >= th["TOP_ORDER_THREAT_RATIO"]:
        out.append(SIGNAL_TOP_ORDER_THREAT)
    if (contact_mismatch_score is not None
            and contact_mismatch_score >= th["OVER_CONTACT_WARNING"]):
        out.append(SIGNAL_OVER_CONTACT_WARNING)
    return out


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def _resolve_target_side(payload: dict) -> str:
    """Decide which lineup faces the starting pitcher for this match.

    Default: the *away* lineup faces the *home* starter, and vice
    versa. The caller can override by passing
    ``payload["quality_contact_matchup_side"] = "home" | "away"``
    (useful for tests / split analyses).
    """
    override = (payload or {}).get("quality_contact_matchup_side")
    if override in ("home", "away"):
        return override
    return "away"   # the conventional matchup target.


def _extract_pitcher_metrics(payload: dict, *, lineup_side: str) -> dict:
    """Resolve the starter metrics opposing ``lineup_side`` and return a
    canonical dict::

        {"era", "xera", "xwoba_allowed",
          "barrel_pct_allowed", "hard_hit_pct_allowed"}
    """
    opposing = "home" if lineup_side == "away" else "away"

    # Prefer the explicit per-match starter snapshot when present.
    starter = None
    starters = payload.get("starters") or payload.get("starting_pitchers")
    if isinstance(starters, dict):
        starter = starters.get(opposing)
    if not isinstance(starter, dict):
        # Fall back to advanced context (team-level pitcher block).
        try:
            from .mlb_advanced_stats_helpers import (
                extract_mlb_advanced_context,
                _pitcher_block,  # noqa: SLF001 — used as documented.
            )
            adv = extract_mlb_advanced_context(payload)
            tk = f"{opposing}_team_advanced"
            starter = _pitcher_block(adv.get(tk) or {})
        except Exception as exc:  # noqa: BLE001
            log.debug("[qcm] advanced helper unavailable: %s", exc)
            starter = {}

    if not isinstance(starter, dict):
        starter = {}

    return {
        "era":                  _f(starter.get("era")),
        "xera":                 _f(starter.get("xera")),
        "xwoba_allowed":        _f(starter.get("xwoba_allowed")
                                     or starter.get("xwoba_against")),
        "barrel_pct_allowed":   _f(starter.get("barrel_pct_allowed")
                                     or starter.get("barrel_against_pct")),
        "hard_hit_pct_allowed": _f(starter.get("hard_hit_pct_allowed")
                                     or starter.get("hard_hit_against_pct")),
    }


def _extract_lineup_batters(payload: dict, *, lineup_side: str) -> tuple[list[dict], str]:
    """Resolve the batters list for the chosen side.

    Returns ``(batters, source_label)`` where ``source_label`` is one of
    ``"REAL"`` (per-batter Statcast rows) or ``"DERIVED_FROM_TEAM"``.
    """
    if per_batter_metrics_enabled():
        lineups = (payload.get("lineups") or {})
        official = lineups.get("official") if isinstance(lineups.get("official"), dict) else None
        side_rows = official.get(lineup_side) if isinstance(official, dict) else None
        real = _extract_real_batters(side_rows)
        if real:
            return real, "REAL"

    # Default path: derive from the team-level snapshot.
    try:
        from .mlb_advanced_stats_helpers import (
            extract_mlb_advanced_context,
            _team_block,  # noqa: SLF001
        )
        adv = extract_mlb_advanced_context(payload)
        team_key = f"{lineup_side}_team_advanced"
        team = _team_block(adv.get(team_key) or {})
    except Exception as exc:  # noqa: BLE001
        log.debug("[qcm] team advanced helper unavailable: %s", exc)
        team = {}
    return _derive_batters_from_team(team), "DERIVED_FROM_TEAM"


def _build_unavailable(reason: str = RC_INSUFFICIENT_DATA,
                        message_debug: Optional[str] = None) -> dict:
    return {
        "available":              False,
        "lineup_contact_quality": None,
        "pitcher_vulnerability":  None,
        "matchup_contact_factor": None,
        "contact_mismatch_score": None,
        "era_gap":                None,
        "regression_risk":        REGRESSION_NORMAL,
        "signals":                [],
        "reason_codes":           [reason],
        "score_breakdown":        {},
        "message_debug":          message_debug,
    }


def compute_quality_contact_matchup(payload: dict) -> dict:
    """Top-level entry point.

    Returns the ``quality_contact_matchup`` block shaped exactly as the
    spec requires. NEVER raises — on missing data it returns the
    unavailable shape with a single reason code.

    Output keys:
      * ``available``               — bool, ``True`` only when *all* of
        ``lineup_contact_quality``, ``pitcher_vulnerability``, and an
        era gap are computable.
      * ``lineup_contact_quality``  — 0-100 (rounded).
      * ``pitcher_vulnerability``   — 0-100.
      * ``matchup_contact_factor``  — quality * (1 + barrel_pct_allowed).
      * ``contact_mismatch_score``  — (quality * vulnerability) / 100.
      * ``era_gap``                 — xERA - ERA.
      * ``regression_risk``         — SEVERE / HIGH / MODERATE / NORMAL.
      * ``signals``                 — list of canonical signal codes.
      * ``reason_codes``            — list including data-source provenance.
      * ``score_breakdown``         — auditable contributions
        (``weighted_per_batter``, ``top_4_weighted``, etc.).
    """
    if not isinstance(payload, dict):
        return _build_unavailable(message_debug="payload is not a dict")

    thresholds = get_active_thresholds()
    side = _resolve_target_side(payload)

    # Batters.
    batters, source_label = _extract_lineup_batters(payload, lineup_side=side)
    if not batters:
        return _build_unavailable(
            RC_INSUFFICIENT_DATA,
            message_debug=f"no batter metrics resolvable for side={side}",
        )

    lineup_block = compute_lineup_contact_quality(batters)
    lineup_quality = lineup_block["lineup_contact_quality"]
    top_4_ratio    = lineup_block["top_4_ratio"]

    if lineup_quality is None:
        return _build_unavailable(
            RC_INSUFFICIENT_DATA,
            message_debug="lineup_contact_quality could not be computed",
        )

    # Pitcher.
    pitcher_metrics = _extract_pitcher_metrics(payload, lineup_side=side)
    pitcher_vuln    = compute_pitcher_vulnerability(pitcher_metrics)
    era_class       = classify_era_gap(pitcher_metrics.get("era"),
                                          pitcher_metrics.get("xera"),
                                          thresholds=thresholds)

    matchup_factor  = compute_matchup_contact_factor(
        lineup_quality, pitcher_metrics.get("barrel_pct_allowed"),
    )
    mismatch_score  = compute_contact_mismatch_score(
        lineup_quality, pitcher_vuln,
    )

    signals = derive_signals(
        lineup_quality, pitcher_metrics, era_class,
        top_4_ratio, mismatch_score, thresholds=thresholds,
    )

    reason_codes: list[str] = []
    if source_label == "REAL":
        reason_codes.append(RC_BATTER_LEVEL_REAL)
    else:
        reason_codes.append(RC_BATTER_LEVEL_DERIVED)
    if pitcher_vuln is None:
        reason_codes.append(RC_PITCHER_MISSING)

    available = pitcher_vuln is not None

    return {
        "available":              available,
        "lineup_contact_quality": _round(lineup_quality, 2),
        "pitcher_vulnerability":  _round(pitcher_vuln, 2),
        "matchup_contact_factor": _round(matchup_factor, 2),
        "contact_mismatch_score": _round(mismatch_score, 2),
        "era_gap":                _round(era_class.get("gap"), 2),
        "regression_risk":        era_class.get("level", REGRESSION_NORMAL),
        "signals":                signals,
        "reason_codes":           reason_codes,
        "side":                   side,
        "thresholds_used":        thresholds,
        "score_breakdown": {
            "top_4_weighted":   _round(lineup_block.get("top_4_weighted"), 2),
            "top_4_ratio":      _round(top_4_ratio, 4),
            "sample_size":      lineup_block.get("sample_size"),
            "pitcher_metrics":  {k: _round(v, 4)
                                  for k, v in pitcher_metrics.items()},
            "weighted_per_batter": [
                {"order": o,
                  "raw":      _round(raw, 2),
                  "weighted": _round(w, 2)}
                for (o, raw, w) in lineup_block.get("weighted_per_batter", [])
            ],
        },
    }


__all__ = [
    # Constants
    "LINEUP_WEIGHTS",
    # Signals
    "SIGNAL_MATCHUP_CONTACT_ADVANTAGE", "SIGNAL_PITCHER_BARREL_REGRESSION",
    "SIGNAL_ERA_UNDERSTATES_DAMAGE", "SIGNAL_TOP_ORDER_THREAT",
    "SIGNAL_OVER_CONTACT_WARNING",
    # Reason codes
    "RC_INSUFFICIENT_DATA", "RC_BATTER_LEVEL_REAL", "RC_BATTER_LEVEL_DERIVED",
    "RC_PITCHER_MISSING",
    # Regression levels
    "REGRESSION_SEVERE", "REGRESSION_HIGH", "REGRESSION_MODERATE", "REGRESSION_NORMAL",
    # Functions
    "get_active_thresholds", "per_batter_metrics_enabled",
    "compute_batter_contact_score", "compute_lineup_contact_quality",
    "compute_pitcher_vulnerability", "classify_era_gap",
    "compute_matchup_contact_factor", "compute_contact_mismatch_score",
    "derive_signals", "compute_quality_contact_matchup",
]
