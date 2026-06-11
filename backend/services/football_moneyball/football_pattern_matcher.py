"""Football Pattern Matcher — orchestrator-friendly facade.

Provides the high-level entry points used by ``analyst_engine`` and
``live_reevaluation``:

  * ``attach_football_intelligence_to_payload(db, pick_payload, match)``
      End-to-end attach: builds the pregame snapshot, computes pattern
      keys, looks up pattern memory, calls the market selection layer
      and persists the snapshot (fail-soft). Mutates ``pick_payload``.

  * ``compare_live_vs_pregame(pregame_snapshot, live_snapshot)``
      Pure function that returns a structured diff used by the UI's
      ``FootballLiveVsPregamePanel`` (tier shift, fragility change,
      market keep/adjust/avoid recommendation).

Fail-soft everywhere: missing inputs or DB errors yield ``available:False``
blocks instead of raising.
"""

from __future__ import annotations

import logging
from typing import Any

from .football_snapshot_builder import (
    build_full_intelligence_snapshot,
    build_live_snapshot,
    build_pregame_snapshot,
)
from .football_pattern_memory import derive_pattern_keys
from .football_market_selection import select_football_market
from .football_intelligence_warehouse import (
    attach_pattern_match_to_payload,
    persist_match_intelligence_snapshot,
)
from .football_goal_pressure_profile import (
    HIGH_PRESSURE, MODERATE_PRESSURE, LOW_PRESSURE, NEUTRAL_PRESSURE, UNAVAILABLE,
)

log = logging.getLogger("football_moneyball.pattern_matcher")


async def attach_football_intelligence_to_payload(
    db,
    pick_payload: dict | None,
    match: dict | None,
    *,
    persist: bool = True,
) -> dict:
    """End-to-end Moneyball attach for a single football pick.

    Returns a small audit dict for orchestrator logging::

        {
          "available":           bool,
          "pattern_keys":        [str, ...],
          "market_selection_ok": bool,
          "snapshot_persisted":  bool,
        }

    Always safe to call; if anything fails it logs at debug level and
    returns ``available:False``.
    """
    if not isinstance(pick_payload, dict):
        return {"available": False, "reason": "no_pick_payload"}
    if not isinstance(match, dict):
        match = {}

    audit: dict[str, Any] = {
        "available":           True,
        "pattern_keys":        [],
        "market_selection_ok": False,
        "snapshot_persisted":  False,
    }

    # 1. Build the pregame snapshot.
    try:
        pregame = build_pregame_snapshot(match)
        pick_payload["goal_pressure_profile"] = pregame.get("goal_pressure_profile") or {}
        pick_payload["football_pregame_snapshot"] = pregame
    except Exception as exc:
        log.debug("build_pregame_snapshot failed: %s", exc)
        pregame = {"available": False}

    # 2. Compute pattern keys.
    try:
        keys = derive_pattern_keys({
            "pregame": pregame,
            "recommendation": pick_payload.get("recommendation"),
            "_corner_form": match.get("_corner_form"),
            "_form_guard":  match.get("_form_guard"),
            "_football_quality": match.get("_football_quality"),
            "trap_signals": match.get("trap_signals"),
        })
        audit["pattern_keys"] = list(keys)
        pick_payload["football_pattern_keys"] = list(keys)
    except Exception as exc:
        log.debug("derive_pattern_keys failed: %s", exc)
        keys = []

    # 3. Pattern memory lookup → mutate payload.
    try:
        await attach_pattern_match_to_payload(db, pick_payload, keys)
    except Exception as exc:
        log.debug("attach_pattern_match_to_payload failed: %s", exc)

    # 4. Market selection.
    try:
        ms = select_football_market(
            pick_payload,
            pregame_snapshot={"pregame": pregame},
            pattern_match=pick_payload.get("historical_pattern_match"),
        )
        if isinstance(ms, dict):
            pick_payload["market_selection"] = (
                ms.get("market_selection") or ms
            )
            audit["market_selection_ok"] = True
    except Exception as exc:
        log.debug("select_football_market failed: %s", exc)

    # 5. Phase F58 — Football L5 vs L15 Profile Cross (+ optional override).
    # Contextual layer applied AFTER market selection. Cross-classifies
    # both teams' L5 vs L15 profile (goals, xG, shots, SOT, corners) and
    # applies symmetric confidence/fragility deltas:
    #   * Bonus capped at +8 when cross supports the pick side.
    #   * Penalty capped at -12 when it contradicts (non-NEUTRAL).
    #   * For STRONG_UNDER_CROSS / STRONG_OVER_CROSS / CORNERS_OVER_CROSS
    #     with confidence_delta >= STRONG_OVERRIDE_THRESHOLD and the
    #     current pick contradicting, an `override` block is emitted in
    #     ``pick_payload["football_profile_cross_applied"]`` so the UI
    #     and downstream selectors can decide whether to flip the market.
    #   * Visual entry on pattern_alignment.entries (visual_only=True).
    try:
        from services.football_phaseF58_integration import (
            attach_football_profile_cross_to_payload,
        )
        f58_audit = attach_football_profile_cross_to_payload(
            pick_payload, match, allow_override=True,
        )
        audit["football_profile_cross"] = {
            "available":   bool(f58_audit.get("available")),
            "profile":     f58_audit.get("profile"),
            "supports":    f58_audit.get("supports"),
            "interaction": f58_audit.get("interaction"),
            "override":    f58_audit.get("override"),
        }
    except Exception as exc:
        log.debug("football_phaseF58 integration failed: %s", exc)

    # 6. Persist the snapshot (best-effort).
    if persist:
        try:
            full_snap = build_full_intelligence_snapshot(
                match,
                selected_market=(
                    (pick_payload.get("market_selection") or {}).get("recommended_market")
                ),
                pattern_keys=keys,
                pick_payload=pick_payload,
            )
            persisted = await persist_match_intelligence_snapshot(
                db,
                match_id=match.get("match_id") or pick_payload.get("match_id"),
                snapshot=full_snap,
                league=full_snap.get("league"),
                selected_market=full_snap.get("selected_market"),
                pattern_keys=keys,
            )
            audit["snapshot_persisted"] = bool(persisted)
        except Exception as exc:
            log.debug("persist_match_intelligence_snapshot failed: %s", exc)

    return audit


# ─────────────────────────────────────────────────────────────────────
# Live vs Pregame comparison (pure)
# ─────────────────────────────────────────────────────────────────────
_TIER_ORDER = {
    UNAVAILABLE: 0,
    LOW_PRESSURE: 1,
    NEUTRAL_PRESSURE: 2,
    MODERATE_PRESSURE: 3,
    HIGH_PRESSURE: 4,
}

RC_LIVE_TIER_ESCALATED   = "FOOTBALL_LIVE_TIER_ESCALATED"
RC_LIVE_TIER_DEESCALATED = "FOOTBALL_LIVE_TIER_DEESCALATED"
RC_LIVE_PRESSURE_STABLE  = "FOOTBALL_LIVE_PRESSURE_STABLE"
RC_LIVE_NO_DATA          = "FOOTBALL_LIVE_NO_DATA"
RC_LIVE_KEEP_MARKET      = "FOOTBALL_LIVE_KEEP_MARKET"
RC_LIVE_REDUCE_EXPOSURE  = "FOOTBALL_LIVE_REDUCE_EXPOSURE"
RC_LIVE_AVOID_MARKET     = "FOOTBALL_LIVE_AVOID_MARKET"


def compare_live_vs_pregame(
    pregame: dict | None,
    live: dict | None,
) -> dict:
    """Pure structured diff between a pregame and live snapshot.

    Returns::

        {
          "available":      bool,
          "pregame_tier":   str,
          "live_tier":      str,
          "tier_shift":     int (live - pregame on _TIER_ORDER),
          "reason_codes":   [str, ...],
          "market_recommendation": "KEEP" | "REDUCE" | "AVOID",
          "summary_es":     str,
        }
    """
    out: dict[str, Any] = {
        "available":             False,
        "pregame_tier":          UNAVAILABLE,
        "live_tier":             UNAVAILABLE,
        "tier_shift":            0,
        "reason_codes":          [],
        "market_recommendation": "KEEP",
        "summary_es":            "",
    }
    if not isinstance(pregame, dict) or not isinstance(live, dict):
        out["reason_codes"].append(RC_LIVE_NO_DATA)
        out["summary_es"] = "Sin datos suficientes para comparar pregame vs live."
        return out

    pre_p = pregame.get("goal_pressure_profile") or {}
    live_p = live.get("goal_pressure_profile") or {}
    pre_tier = (pre_p.get("combined") or {}).get("pressure_tier") or UNAVAILABLE
    live_tier = (live_p.get("combined") or {}).get("pressure_tier") or UNAVAILABLE

    out["available"] = True
    out["pregame_tier"] = pre_tier
    out["live_tier"] = live_tier
    shift = _TIER_ORDER.get(live_tier, 0) - _TIER_ORDER.get(pre_tier, 0)
    out["tier_shift"] = int(shift)

    if shift > 0:
        out["reason_codes"].append(RC_LIVE_TIER_ESCALATED)
    elif shift < 0:
        out["reason_codes"].append(RC_LIVE_TIER_DEESCALATED)
    else:
        out["reason_codes"].append(RC_LIVE_PRESSURE_STABLE)

    # Decide market recommendation.
    if shift >= 2:
        out["market_recommendation"] = "AVOID"
        out["reason_codes"].append(RC_LIVE_AVOID_MARKET)
        out["summary_es"] = (
            "La presión goleadora aumentó significativamente vs el pregame; "
            "se sugiere evitar el mercado original (especialmente Unders agresivos)."
        )
    elif shift == 1:
        out["market_recommendation"] = "REDUCE"
        out["reason_codes"].append(RC_LIVE_REDUCE_EXPOSURE)
        out["summary_es"] = (
            "La presión goleadora subió un escalón vs pregame; "
            "considerar reducir exposición o migrar a una alternativa protegida."
        )
    elif shift <= -1:
        out["market_recommendation"] = "KEEP"
        out["reason_codes"].append(RC_LIVE_KEEP_MARKET)
        out["summary_es"] = (
            "La presión bajó vs pregame; el mercado original mantiene o mejora su soporte."
        )
    else:
        out["market_recommendation"] = "KEEP"
        out["reason_codes"].append(RC_LIVE_KEEP_MARKET)
        out["summary_es"] = (
            "Presión estable respecto al pregame; el mercado original sigue siendo válido."
        )

    return out


__all__ = [
    "attach_football_intelligence_to_payload",
    "compare_live_vs_pregame",
    "RC_LIVE_TIER_ESCALATED", "RC_LIVE_TIER_DEESCALATED",
    "RC_LIVE_PRESSURE_STABLE", "RC_LIVE_NO_DATA",
    "RC_LIVE_KEEP_MARKET", "RC_LIVE_REDUCE_EXPOSURE", "RC_LIVE_AVOID_MARKET",
]
