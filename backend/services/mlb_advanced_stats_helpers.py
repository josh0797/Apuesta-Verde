"""MLB Advanced-Stats Helpers (Fase 9 + 10).

This module is the glue between the **Statcast adapter** (Batch B,
`mlb_statcast_adapter`) and the **existing scorers** (`pitcher_quality_score`,
`over_under_predictor`, `mlb_fragility_score`,
`mlb_starter_lineup_under_profile`, `mlb_explosive_inning_engine`).

Design contract (NON-NEGOTIABLE):
  * **Fail-soft**: missing snapshot → every helper returns
    ``{"applied": False, "adjustment": 0, "reason_codes": []}`` so callers
    can blindly add the adjustment without branching.
  * **Capped**: every individual adjustment is clamped to ±15 score points
    (no single Statcast field can flip a pick by itself).
  * **Explainable**: every adjustment carries a list of reason codes
    that flow into the pick payload so the LLM and UI can cite them.
  * **Moneyball-aligned**: bias is conservative — boost ONLY when
    multiple Statcast signals converge; downgrade quickly when ERA
    looks better than xERA.

Reason codes (also referenced by the explosive_inning engine, Fase 10):
  STATCAST_HARD_CONTACT_SUPPORT
  BARREL_RISK_ELEVATED
  PITCHER_XWOBA_WARNING
  XERA_UNDERPERFORMS_ERA
  POWER_BAT_STATCAST_SUPPORT
  STATCAST_UNDER_SUPPORT
  STATCAST_OVER_SUPPORT
  XERA_TOTAL_RISK
  HARD_CONTACT_TOTAL_RISK
  ADVANCED_STATS_REDUCE_FRAGILITY
  ADVANCED_STATS_INCREASE_FRAGILITY
  ERA_UNDERSTATES_RISK
  LOW_HARD_CONTACT_ENVIRONMENT
  ADVANCED_STARTER_UNDER_SUPPORT
  ADVANCED_STARTER_UNDER_WARNING
  POWER_CONTACT_VETO
  SCRIPT_SURVIVAL_ADVANCED_BOOST
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Reason codes (canonical names — consumers should import from here)
# ─────────────────────────────────────────────────────────────────────
RC_HARD_CONTACT_SUPPORT       = "STATCAST_HARD_CONTACT_SUPPORT"
RC_BARREL_RISK_ELEVATED       = "BARREL_RISK_ELEVATED"
RC_PITCHER_XWOBA_WARNING      = "PITCHER_XWOBA_WARNING"
RC_XERA_UNDERPERFORMS_ERA     = "XERA_UNDERPERFORMS_ERA"
RC_POWER_BAT_STATCAST_SUPPORT = "POWER_BAT_STATCAST_SUPPORT"
RC_STATCAST_UNDER_SUPPORT     = "STATCAST_UNDER_SUPPORT"
RC_STATCAST_OVER_SUPPORT      = "STATCAST_OVER_SUPPORT"
RC_XERA_TOTAL_RISK            = "XERA_TOTAL_RISK"
RC_HARD_CONTACT_TOTAL_RISK    = "HARD_CONTACT_TOTAL_RISK"
RC_ADV_REDUCE_FRAGILITY       = "ADVANCED_STATS_REDUCE_FRAGILITY"
RC_ADV_INCREASE_FRAGILITY     = "ADVANCED_STATS_INCREASE_FRAGILITY"
RC_ERA_UNDERSTATES_RISK       = "ERA_UNDERSTATES_RISK"
RC_LOW_HARD_CONTACT_ENV       = "LOW_HARD_CONTACT_ENVIRONMENT"
RC_ADV_STARTER_UNDER_SUPPORT  = "ADVANCED_STARTER_UNDER_SUPPORT"
RC_ADV_STARTER_UNDER_WARNING  = "ADVANCED_STARTER_UNDER_WARNING"
RC_POWER_CONTACT_VETO         = "POWER_CONTACT_VETO"
RC_SCRIPT_SURVIVAL_ADV_BOOST  = "SCRIPT_SURVIVAL_ADVANCED_BOOST"


_PT_CAP = 15.0   # max ± points per scoring helper


# ─────────────────────────────────────────────────────────────────────
# Helper 1 — safe context extraction
# ─────────────────────────────────────────────────────────────────────
def extract_mlb_advanced_context(payload: dict | None) -> dict:
    """Return a normalized view of the advanced_stats_snapshot.

    Accepts either a full ``pick_payload`` / ``match_doc`` (looks for
    the ``advanced_stats_snapshot`` key) or the snapshot itself.

    Always returns the same shape — missing keys → empty dicts.
    """
    if not isinstance(payload, dict):
        payload = {}
    snap = payload.get("advanced_stats_snapshot") or payload

    def _block(name: str) -> dict:
        b = snap.get(name)
        return b if isinstance(b, dict) else {}

    hp = _block("home_pitcher_advanced")
    ap = _block("away_pitcher_advanced")
    ht = _block("home_team_advanced")
    at = _block("away_team_advanced")

    # Aggregate availability + quality flags
    blocks = [hp, ap, ht, at]
    any_available = any(b.get("available") for b in blocks)
    qualities = [b.get("data_quality") for b in blocks if b.get("data_quality")]
    if not qualities:
        data_quality = "missing"
    elif all(q == "strong" for q in qualities):
        data_quality = "strong"
    elif any(q in ("strong", "partial") for q in qualities):
        data_quality = "partial"
    else:
        data_quality = "missing"

    # Source labels for transparency
    sources_seen: set[str] = set()
    field_sources_merged: dict[str, str] = {}
    source_status_merged: dict[str, Any] = {}
    for b in blocks:
        for s in (b.get("sources_consulted") or []):
            if isinstance(s, str):
                sources_seen.add(s)
        for k, v in (b.get("field_sources") or {}).items():
            field_sources_merged.setdefault(k, v)
        for k, v in (b.get("source_status") or {}).items():
            source_status_merged.setdefault(k, v)

    return {
        "available":           any_available,
        "data_quality":        data_quality,
        "home_pitcher_advanced": hp,
        "away_pitcher_advanced": ap,
        "home_team_advanced":    ht,
        "away_team_advanced":    at,
        "sources_consulted":   sorted(sources_seen),
        "field_sources":       field_sources_merged,
        "source_status":       source_status_merged,
    }


def _pitcher_block(adv: dict) -> dict:
    """Safely pluck the pitcher section, regardless of nested wrappers."""
    if not isinstance(adv, dict):
        return {}
    p = adv.get("pitcher")
    return p if isinstance(p, dict) else {}


def _team_block(adv: dict) -> dict:
    if not isinstance(adv, dict):
        return {}
    t = adv.get("team")
    return t if isinstance(t, dict) else {}


def _clamp(v: float, lo: float = -_PT_CAP, hi: float = _PT_CAP) -> float:
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# Helper 2 — pitcher_quality_score adjustment
# ─────────────────────────────────────────────────────────────────────
def pitcher_quality_advanced_adjustment(pitcher_advanced: dict | None) -> dict:
    """Return ``{"adjustment": float, "reason_codes": [str], "applied": bool}``.

    Adjustments (capped at ±15 total):
      * xERA worse than ERA by ≥0.5 run → -6 + ``XERA_UNDERPERFORMS_ERA``
      * xwOBA allowed ≥ 0.340 → -4 + ``PITCHER_XWOBA_WARNING``
      * barrel_pct_allowed ≥ 9% OR hard_hit_pct_allowed ≥ 42% → -4 + ``BARREL_RISK_ELEVATED``
      * whiff_pct ≥ 28% AND chase_pct ≥ 32% → +3 (strong K profile)
      * Strong K/BB profile (K% ≥ 26% AND BB% ≤ 7%) → +3
      * Low hard-hit + low barrel allowed → +3 + ``STATCAST_HARD_CONTACT_SUPPORT``
    """
    p = _pitcher_block(pitcher_advanced)
    if not p:
        return {"applied": False, "adjustment": 0.0, "reason_codes": []}

    rcs: list[str] = []
    adj = 0.0

    era    = p.get("era")
    xera   = p.get("xera")
    xwoba  = p.get("xwoba_allowed")
    barrel = p.get("barrel_pct_allowed")
    hard   = p.get("hard_hit_pct_allowed")
    whiff  = p.get("whiff_pct")
    chase  = p.get("chase_pct")
    k_pct  = p.get("k_pct")
    bb_pct = p.get("bb_pct")

    if era is not None and xera is not None and (xera - era) >= 0.5:
        adj -= 6.0
        rcs.append(RC_XERA_UNDERPERFORMS_ERA)
    if xwoba is not None and xwoba >= 0.340:
        adj -= 4.0
        rcs.append(RC_PITCHER_XWOBA_WARNING)
    if (barrel is not None and barrel >= 9.0) or (hard is not None and hard >= 42.0):
        adj -= 4.0
        if RC_BARREL_RISK_ELEVATED not in rcs:
            rcs.append(RC_BARREL_RISK_ELEVATED)
    if whiff is not None and chase is not None and whiff >= 28.0 and chase >= 32.0:
        adj += 3.0
    if k_pct is not None and bb_pct is not None and k_pct >= 26.0 and bb_pct <= 7.0:
        adj += 3.0
    if (barrel is not None and barrel <= 6.0) and (hard is not None and hard <= 32.0):
        adj += 3.0
        rcs.append(RC_HARD_CONTACT_SUPPORT)

    return {"applied": True, "adjustment": _clamp(adj), "reason_codes": rcs}


# ─────────────────────────────────────────────────────────────────────
# Helper 3 — over_under_predictor adjustment
# ─────────────────────────────────────────────────────────────────────
def over_under_advanced_adjustment(
    home_pitcher_advanced: dict | None,
    away_pitcher_advanced: dict | None,
    home_team_advanced: dict | None,
    away_team_advanced: dict | None,
) -> dict:
    """Return adjustment for Over/Under confidence.

    Sign convention: **positive = supports OVER**, negative = supports UNDER.

    Aggregates:
      * Both pitchers low barrel_allowed + low hard_hit_allowed → -5 (UNDER)
      * Both teams low team_barrel_pct + low team_hard_hit_pct → -3 (UNDER)
      * Both team_xwoba ≥ 0.330 → +5 (OVER, but capped)
      * Either pitcher xERA-ERA ≥ 0.5 → +3 (Under fragility) → ``XERA_TOTAL_RISK``
      * Either pitcher hard_hit_allowed ≥ 42% AND team opposing barrel ≥ 8% → +3 → ``HARD_CONTACT_TOTAL_RISK``
    """
    rcs: list[str] = []
    adj = 0.0

    h_p = _pitcher_block(home_pitcher_advanced)
    a_p = _pitcher_block(away_pitcher_advanced)
    h_t = _team_block(home_team_advanced)
    a_t = _team_block(away_team_advanced)

    if not any((h_p, a_p, h_t, a_t)):
        return {"applied": False, "adjustment": 0.0, "reason_codes": []}

    # UNDER support: both pitchers suppressing hard contact
    def _both_low(pa: dict, pb: dict) -> bool:
        for src in (pa, pb):
            bar = src.get("barrel_pct_allowed")
            har = src.get("hard_hit_pct_allowed")
            if not (bar is not None and bar <= 7.0):
                return False
            if not (har is not None and har <= 35.0):
                return False
        return True

    if _both_low(h_p, a_p):
        adj -= 5.0
        rcs.append(RC_STATCAST_UNDER_SUPPORT)

    # UNDER support: both teams low offensive contact profile
    if (h_t.get("team_barrel_pct") is not None and h_t["team_barrel_pct"] <= 6.5 and
            a_t.get("team_barrel_pct") is not None and a_t["team_barrel_pct"] <= 6.5 and
            h_t.get("team_hard_hit_pct") is not None and h_t["team_hard_hit_pct"] <= 35.0 and
            a_t.get("team_hard_hit_pct") is not None and a_t["team_hard_hit_pct"] <= 35.0):
        adj -= 3.0
        rcs.append(RC_LOW_HARD_CONTACT_ENV)

    # OVER support: both teams elevated xwOBA
    if (h_t.get("team_xwoba") is not None and h_t["team_xwoba"] >= 0.330 and
            a_t.get("team_xwoba") is not None and a_t["team_xwoba"] >= 0.330):
        adj += 5.0
        rcs.append(RC_STATCAST_OVER_SUPPORT)

    # XERA risk (either pitcher's ERA understates true skill)
    for pa in (h_p, a_p):
        if pa.get("era") is not None and pa.get("xera") is not None:
            if (pa["xera"] - pa["era"]) >= 0.5:
                adj += 3.0
                if RC_XERA_TOTAL_RISK not in rcs:
                    rcs.append(RC_XERA_TOTAL_RISK)
                break

    # Hard-contact total risk
    for pitcher_blk, opp_team_blk in ((h_p, a_t), (a_p, h_t)):
        ha = pitcher_blk.get("hard_hit_pct_allowed")
        bar = opp_team_blk.get("team_barrel_pct")
        if ha is not None and ha >= 42.0 and bar is not None and bar >= 8.0:
            adj += 3.0
            rcs.append(RC_HARD_CONTACT_TOTAL_RISK)
            break

    return {"applied": True, "adjustment": _clamp(adj), "reason_codes": rcs}


# ─────────────────────────────────────────────────────────────────────
# Helper 4 — mlb_fragility_score adjustment
# ─────────────────────────────────────────────────────────────────────
def fragility_advanced_adjustment(advanced_ctx: dict | None) -> dict:
    """Return ``{"adjustment", "reason_codes", "applied"}``.

    Positive = MORE fragile (downgrade pick confidence).
    Negative = LESS fragile (slight boost).
    """
    if not isinstance(advanced_ctx, dict) or not advanced_ctx.get("available"):
        return {"applied": False, "adjustment": 0.0, "reason_codes": []}

    rcs: list[str] = []
    adj = 0.0

    h_p = _pitcher_block(advanced_ctx.get("home_pitcher_advanced"))
    a_p = _pitcher_block(advanced_ctx.get("away_pitcher_advanced"))
    h_t = _team_block(advanced_ctx.get("home_team_advanced"))
    a_t = _team_block(advanced_ctx.get("away_team_advanced"))

    # Increase fragility: xERA under-estimates ERA risk
    for pa in (h_p, a_p):
        if pa.get("era") is not None and pa.get("xera") is not None:
            if (pa["xera"] - pa["era"]) >= 0.7:
                adj += 4.0
                if RC_ERA_UNDERSTATES_RISK not in rcs:
                    rcs.append(RC_ERA_UNDERSTATES_RISK)
                break

    # Increase fragility: high xwOBA allowed
    for pa in (h_p, a_p):
        if pa.get("xwoba_allowed") is not None and pa["xwoba_allowed"] >= 0.345:
            adj += 3.0
            if RC_PITCHER_XWOBA_WARNING not in rcs:
                rcs.append(RC_PITCHER_XWOBA_WARNING)
            break

    # Increase fragility: high power profile on either team
    for tm in (h_t, a_t):
        bar = tm.get("team_barrel_pct")
        if bar is not None and bar >= 9.0:
            adj += 3.0
            if RC_ADV_INCREASE_FRAGILITY not in rcs:
                rcs.append(RC_ADV_INCREASE_FRAGILITY)
            break

    # Reduce fragility: both pitchers strong + low team contact
    both_pitchers_strong = all(
        pa.get("xwoba_allowed") is not None and pa["xwoba_allowed"] <= 0.310
        for pa in (h_p, a_p)
    )
    both_teams_quiet = (
        h_t.get("team_barrel_pct") is not None and h_t["team_barrel_pct"] <= 6.5 and
        a_t.get("team_barrel_pct") is not None and a_t["team_barrel_pct"] <= 6.5
    )
    if both_pitchers_strong and both_teams_quiet:
        adj -= 4.0
        rcs.append(RC_ADV_REDUCE_FRAGILITY)

    return {"applied": True, "adjustment": _clamp(adj, lo=-10, hi=12), "reason_codes": rcs}


# ─────────────────────────────────────────────────────────────────────
# Helper 5 — mlb_starter_lineup_under_profile adjustment
# ─────────────────────────────────────────────────────────────────────
def starter_under_advanced_adjustment(advanced_ctx: dict | None) -> dict:
    """Return script-survival adjustment for Under markets.

    Positive = supports Under (boost script survival).
    Negative = warns Under (downgrade script survival).
    """
    if not isinstance(advanced_ctx, dict) or not advanced_ctx.get("available"):
        return {"applied": False, "adjustment": 0.0, "reason_codes": []}

    rcs: list[str] = []
    adj = 0.0

    h_p = _pitcher_block(advanced_ctx.get("home_pitcher_advanced"))
    a_p = _pitcher_block(advanced_ctx.get("away_pitcher_advanced"))
    h_t = _team_block(advanced_ctx.get("home_team_advanced"))
    a_t = _team_block(advanced_ctx.get("away_team_advanced"))

    # SUPPORT Under: both starters with strong xERA & low contact allowed
    pitchers_strong = all(
        (pa.get("xera") is not None and pa["xera"] <= 3.50) and
        (pa.get("xwoba_allowed") is None or pa["xwoba_allowed"] <= 0.310) and
        (pa.get("barrel_pct_allowed") is None or pa["barrel_pct_allowed"] <= 7.0)
        for pa in (h_p, a_p)
    )
    if pitchers_strong:
        adj += 6.0
        rcs.append(RC_ADV_STARTER_UNDER_SUPPORT)
        rcs.append(RC_SCRIPT_SURVIVAL_ADV_BOOST)

    # WARN Under: at least one starter with ERA-xERA divergence (lucky)
    for pa in (h_p, a_p):
        if pa.get("era") is not None and pa.get("xera") is not None:
            if (pa["xera"] - pa["era"]) >= 0.6:
                adj -= 5.0
                rcs.append(RC_ADV_STARTER_UNDER_WARNING)
                break

    # VETO: either team with power-contact profile (barrel ≥ 9% AND xwOBA ≥ 0.330)
    for tm in (h_t, a_t):
        bar = tm.get("team_barrel_pct")
        xw  = tm.get("team_xwoba")
        if bar is not None and xw is not None and bar >= 9.0 and xw >= 0.330:
            adj -= 4.0
            rcs.append(RC_POWER_CONTACT_VETO)
            break

    return {"applied": True, "adjustment": _clamp(adj, lo=-12, hi=10), "reason_codes": rcs}


# ─────────────────────────────────────────────────────────────────────
# Top-level summariser — used by orchestrator post-processing
# ─────────────────────────────────────────────────────────────────────
def compute_all_advanced_adjustments(pick_payload_or_match_doc: dict) -> dict:
    """Run every adjustment helper and pack the result into a single dict
    that the orchestrator can attach to the pick payload.

    Output::

        {
            "advanced_stats_used":          bool,
            "advanced_stats_data_quality":  "strong" | "partial" | "missing",
            "advanced_stats_reason_codes":  [str, ...],
            "advanced_stats_adjustment_summary": {
                "home_pitcher_quality":  {...},
                "away_pitcher_quality":  {...},
                "over_under":            {...},
                "fragility":             {...},
                "starter_under":         {...},
            },
            "advanced_stats_sources_consulted": [...],
        }
    """
    ctx = extract_mlb_advanced_context(pick_payload_or_match_doc)
    home_p = ctx.get("home_pitcher_advanced") or {}
    away_p = ctx.get("away_pitcher_advanced") or {}
    home_t = ctx.get("home_team_advanced") or {}
    away_t = ctx.get("away_team_advanced") or {}

    home_pq = pitcher_quality_advanced_adjustment(home_p)
    away_pq = pitcher_quality_advanced_adjustment(away_p)
    ou      = over_under_advanced_adjustment(home_p, away_p, home_t, away_t)
    fr      = fragility_advanced_adjustment(ctx)
    su      = starter_under_advanced_adjustment(ctx)

    all_rcs: list[str] = []
    for block in (home_pq, away_pq, ou, fr, su):
        for rc in block.get("reason_codes") or []:
            if rc not in all_rcs:
                all_rcs.append(rc)

    used = ctx["available"] and any(
        b.get("applied") and (b.get("reason_codes") or b.get("adjustment"))
        for b in (home_pq, away_pq, ou, fr, su)
    )

    return {
        "advanced_stats_used":         used,
        "advanced_stats_data_quality": ctx["data_quality"],
        "advanced_stats_reason_codes": all_rcs,
        "advanced_stats_adjustment_summary": {
            "home_pitcher_quality":  home_pq,
            "away_pitcher_quality":  away_pq,
            "over_under":            ou,
            "fragility":             fr,
            "starter_under":         su,
        },
        "advanced_stats_sources_consulted": ctx.get("sources_consulted") or [],
    }
