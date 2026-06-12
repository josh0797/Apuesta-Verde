"""Football BTTS Live Guard — Fix 2.

Real-time guardrail that **blocks BTTS recommendations** when live evidence
contradicts the bilateral-threat requirement that BTTS implies. This is a
companion to the existing "game-openness" guard but focused specifically on
BTTS markets.

The trigger case (Mexico 1-0 South Africa, min 59, South Africa with red,
xG 1.52 vs 0.43, shots 14 vs 3, box-shots 7 vs 1) is the canonical
regression: the engine recommended "BTTS (Ambos marcan)" off the back of
local momentum, ignoring that the *visiting* side wasn't generating
sufficient bilateral threat.

Design
------
* **Pure module, no I/O. Fail-soft.**
* `guard_btts_live_recommendation(...)` is the only entry. It returns
  ``btts_allowed: bool`` plus replacement market + reason codes + a
  Spanish narrative for the UI.
* Three orthogonal block reasons (any one is sufficient):
    1. Low bilateral threat — both sides must meet minimum offensive
       volume **or** finishing-quality thresholds.
    2. Red-card + low threat — the punished side stops generating real
       chances → cannot complete BTTS.
    3. Unilateral dominance — one side's xG / shots / box-shots
       outweigh the other by configurable ratios.
* Replacement logic — when BTTS is blocked AND score is 1-0/0-1 AND
  minute ≤ 75 → recommend ``OVER_1_5``. When minute is too late or the
  Over 1.5 line has likely lost value → ``WATCHLIST`` (no auto-pick).
* `infer_team_strength_from_odds(...)` — bookmaker-derived favorite /
  underdog classification (HIGH / MEDIUM / LOW) used by Rule 2B.

Reason codes (added to Phase F58)
---------------------------------
* ``BTTS_BLOCKED_LOW_BILATERAL_THREAT``
* ``BTTS_BLOCKED_RED_CARD_LOW_THREAT``
* ``BTTS_BLOCKED_UNILATERAL_DOMINANCE``
* ``BTTS_REPLACED_WITH_OVER_1_5``
* ``UNILATERAL_MOMENTUM_NOT_BTTS``
* ``RED_CARD_TEAM_ATTACK_SUPPRESSED``
* ``BTTS_REPLACED_WITH_WATCHLIST``
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_btts_live_guard")

ENGINE_VERSION = "football_btts_live_guard.v1"

# ── Bilateral-threat thresholds (per-side minimums for BTTS) ─────────
BTTS_MIN_XG_PER_SIDE        = 0.60
BTTS_MIN_SHOTS_PER_SIDE     = 5
BTTS_MIN_SOT_PER_SIDE       = 2
BTTS_MIN_BOX_SHOTS_PER_SIDE = 2

# ── Red-card severity gate ──────────────────────────────────────────
RED_CARD_LOW_THREAT_XG          = 0.60
RED_CARD_LOW_THREAT_SHOTS       = 5
RED_CARD_LOW_THREAT_BOX_SHOTS   = 1  # ≤1 box-shot ⇒ attack neutralised

# ── Unilateral dominance ratios ─────────────────────────────────────
UNILATERAL_XG_RATIO        = 2.5
UNILATERAL_SHOTS_RATIO     = 2.0
UNILATERAL_BOX_SHOTS_RATIO = 3.0

# ── Replacement market gating ───────────────────────────────────────
REPLACEMENT_MAX_MINUTE = 75   # after this, Over 1.5 loses value vs odds
LATE_GAME_MINUTE       = 83   # >= this → WATCHLIST instead

# ── Risk classification thresholds ──────────────────────────────────
RISK_HIGH_GAP   = 0.50
RISK_MEDIUM_GAP = 0.25


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    f = _safe(v)
    return int(f) if f is not None else None


def _both_have_xg_threat(home_xg, away_xg, home_shots, away_shots) -> bool:
    h_xg = _safe(home_xg); a_xg = _safe(away_xg)
    h_sh = _safe(home_shots); a_sh = _safe(away_shots)
    if h_xg is None or a_xg is None or h_sh is None or a_sh is None:
        return False
    return (
        h_xg >= BTTS_MIN_XG_PER_SIDE
        and a_xg >= BTTS_MIN_XG_PER_SIDE
        and h_sh >= BTTS_MIN_SHOTS_PER_SIDE
        and a_sh >= BTTS_MIN_SHOTS_PER_SIDE
    )


def _both_have_quality_threat(home_sot, away_sot, home_box, away_box) -> bool:
    h_sot = _safe(home_sot); a_sot = _safe(away_sot)
    h_box = _safe(home_box); a_box = _safe(away_box)
    if h_sot is None or a_sot is None or h_box is None or a_box is None:
        return False
    return (
        h_sot >= BTTS_MIN_SOT_PER_SIDE
        and a_sot >= BTTS_MIN_SOT_PER_SIDE
        and h_box >= BTTS_MIN_BOX_SHOTS_PER_SIDE
        and a_box >= BTTS_MIN_BOX_SHOTS_PER_SIDE
    )


def _team_threat_collapse(*, xg, shots, box_shots) -> bool:
    """Return True if this team is **not** generating real chances."""
    xg_v = _safe(xg)
    sh_v = _safe(shots)
    bx_v = _safe(box_shots)
    if xg_v is None and sh_v is None and bx_v is None:
        # Cannot judge → treat as collapsed (conservative for BTTS).
        return True
    low_xg    = (xg_v is not None and xg_v < RED_CARD_LOW_THREAT_XG)
    low_shots = (sh_v is not None and sh_v < RED_CARD_LOW_THREAT_SHOTS)
    low_box   = (bx_v is not None and bx_v <= RED_CARD_LOW_THREAT_BOX_SHOTS)
    # Any TWO of the three negatives ⇒ collapsed.
    negatives = sum([low_xg, low_shots, low_box])
    return negatives >= 2


def _is_unilateral_dominance(
    *, home_xg, away_xg, home_shots, away_shots,
    home_box, away_box,
) -> Optional[dict]:
    """Return dict with dominant_side + ratios when truly unilateral, else None."""
    h_xg = _safe(home_xg); a_xg = _safe(away_xg)
    h_sh = _safe(home_shots); a_sh = _safe(away_shots)
    h_bx = _safe(home_box); a_bx = _safe(away_box)
    if any(v is None for v in (h_xg, a_xg, h_sh, a_sh, h_bx, a_bx)):
        return None

    # Determine which side is stronger.
    if h_xg + h_sh + h_bx >= a_xg + a_sh + a_bx:
        strong = {"side": "home", "xg": h_xg, "shots": h_sh, "box": h_bx}
        weak   = {"side": "away", "xg": a_xg, "shots": a_sh, "box": a_bx}
    else:
        strong = {"side": "away", "xg": a_xg, "shots": a_sh, "box": a_bx}
        weak   = {"side": "home", "xg": h_xg, "shots": h_sh, "box": h_bx}

    xg_ratio  = strong["xg"]  / max(weak["xg"],  0.10)
    sh_ratio  = strong["shots"] / max(weak["shots"], 1.0)
    bx_ratio  = strong["box"] / max(weak["box"], 1.0)
    if (xg_ratio >= UNILATERAL_XG_RATIO
            and sh_ratio >= UNILATERAL_SHOTS_RATIO
            and bx_ratio >= UNILATERAL_BOX_SHOTS_RATIO):
        return {
            "dominant_side": strong["side"],
            "xg_ratio":      round(xg_ratio, 2),
            "shots_ratio":   round(sh_ratio, 2),
            "box_ratio":     round(bx_ratio, 2),
        }
    return None


def _risk_from_evidence(*, blocked_reasons: list[str], has_red_card: bool,
                       dominance: Optional[dict]) -> str:
    """Risk classification of the blocked recommendation."""
    if "BTTS_BLOCKED_UNILATERAL_DOMINANCE" in blocked_reasons and has_red_card:
        return "HIGH"
    if dominance and dominance.get("xg_ratio", 0) >= 3.5:
        return "HIGH"
    if len(blocked_reasons) >= 2:
        return "MEDIUM"
    return "LOW"


# ─────────────────────────────────────────────────────────────────────
# Public: infer_team_strength_from_odds
# ─────────────────────────────────────────────────────────────────────
def infer_team_strength_from_odds(
    *,
    home_team: str,
    away_team: str,
    home_ml_odds: Optional[float] = None,
    draw_odds:   Optional[float] = None,
    away_ml_odds: Optional[float] = None,
    home_implied_prob: Optional[float] = None,
    away_implied_prob: Optional[float] = None,
) -> dict:
    """Classify favorite vs underdog from 1X2 odds or implied probabilities.

    Either ``*_ml_odds`` (decimal) or ``*_implied_prob`` (0..1) is enough.
    When the inputs are insufficient → returns
    ``{available: False, _reason: "insufficient_inputs"}``.
    """
    h_imp = _safe(home_implied_prob)
    a_imp = _safe(away_implied_prob)

    # Derive implied probabilities from decimal odds if not provided.
    if h_imp is None and home_ml_odds is not None:
        ho = _safe(home_ml_odds)
        if ho and ho > 0:
            h_imp = 1.0 / ho
    if a_imp is None and away_ml_odds is not None:
        ao = _safe(away_ml_odds)
        if ao and ao > 0:
            a_imp = 1.0 / ao

    # Normalise against draw to remove vig (only when draw_odds present).
    do = _safe(draw_odds)
    if h_imp is not None and a_imp is not None and do and do > 0:
        total = h_imp + a_imp + (1.0 / do)
        if total > 0:
            h_imp = h_imp / total
            a_imp = a_imp / total

    if h_imp is None or a_imp is None:
        return {
            "available":   False,
            "_reason":     "insufficient_inputs",
            "favorite_team":  None,
            "underdog_team":  None,
        }

    favorite, underdog = (home_team, away_team) if h_imp >= a_imp else (away_team, home_team)
    fav_p = max(h_imp, a_imp)
    udg_p = min(h_imp, a_imp)
    gap = fav_p - udg_p

    if gap >= 0.35:
        conf = "HIGH"
    elif gap >= 0.20:
        conf = "MEDIUM"
    else:
        conf = "LOW"

    return {
        "available":              True,
        "favorite_team":          favorite,
        "underdog_team":          underdog,
        "favorite_implied_prob":  round(fav_p, 4),
        "underdog_implied_prob":  round(udg_p, 4),
        "strength_gap":           round(gap, 4),
        "favorite_confidence":    conf,
        "source":                 "bookmaker_odds",
    }


# ─────────────────────────────────────────────────────────────────────
# Public: guard_btts_live_recommendation
# ─────────────────────────────────────────────────────────────────────
def _is_btts_market(market: Optional[str]) -> bool:
    if not market:
        return False
    s = str(market).lower()
    return any(token in s for token in (
        "btts", "ambos marcan", "ambos equipos marcan", "both teams to score",
    ))


def _build_narrative(
    *, home_team, away_team, dominant_side, dominance,
    has_red_card_team, replacement_market,
) -> str:
    parts: list[str] = []
    dom_name = home_team if dominant_side == "home" else away_team if dominant_side == "away" else None
    weak_name = away_team if dominant_side == "home" else home_team if dominant_side == "away" else None

    if dominance and dom_name and weak_name:
        parts.append(
            f"Aunque {dom_name} tiene momentum ofensivo, la amenaza es unilateral."
        )
    if has_red_card_team:
        parts.append(
            f"{has_red_card_team} tiene un expulsado y genera poco volumen ofensivo, "
            f"por lo que BTTS no está soportado."
        )
    if not parts:
        parts.append("La amenaza ofensiva no es bilateral, BTTS no está soportado.")
    if replacement_market == "OVER_1_5":
        parts.append("La alternativa más coherente es Over 1.5 goles.")
    elif replacement_market == "WATCHLIST":
        parts.append("El partido entra en watchlist: el tiempo restante no justifica una entrada alternativa.")
    return " ".join(parts)


def guard_btts_live_recommendation(
    *,
    current_market: Optional[str],
    minute: int,
    score_home: int,
    score_away: int,
    home_team: str = "Local",
    away_team: str = "Visitante",
    home_red_cards: int = 0,
    away_red_cards: int = 0,
    home_xg: Optional[float] = None,
    away_xg: Optional[float] = None,
    home_shots: Optional[int] = None,
    away_shots: Optional[int] = None,
    home_sot: Optional[int] = None,
    away_sot: Optional[int] = None,
    home_box_shots: Optional[int] = None,
    away_box_shots: Optional[int] = None,
    home_corners: Optional[int] = None,  # noqa: ARG001 reserved for future use
    away_corners: Optional[int] = None,  # noqa: ARG001
    home_dangerous_attacks: Optional[int] = None,  # noqa: ARG001
    away_dangerous_attacks: Optional[int] = None,  # noqa: ARG001
    team_strength: Optional[dict] = None,
) -> dict:
    """Evaluate whether BTTS is a safe live recommendation.

    Returns a dict (always — fail-soft) with the following keys::

        {
          "engine_version":     str,
          "btts_allowed":       bool,
          "blocked_market":     "BTTS" | None,
          "replacement_market": "OVER_1_5" | "WATCHLIST" | None,
          "replacement_label":  "Más de 1.5 goles" | "Watchlist" | None,
          "risk":               "LOW" | "MEDIUM" | "HIGH",
          "reason_codes":       [str, ...],
          "narrative_es":       str,
          "evidence":           {...summary of inputs used...},
        }

    If ``current_market`` is **not** a BTTS market, the guard returns
    ``btts_allowed=True`` and no-ops (the caller's market is untouched).
    """
    base_evidence = {
        "minute":           minute,
        "score":            f"{score_home}-{score_away}",
        "home_team":        home_team,
        "away_team":        away_team,
        "home_red_cards":   home_red_cards,
        "away_red_cards":   away_red_cards,
        "home_xg":          _safe(home_xg),
        "away_xg":          _safe(away_xg),
        "home_shots":       _safe_int(home_shots),
        "away_shots":       _safe_int(away_shots),
        "home_sot":         _safe_int(home_sot),
        "away_sot":         _safe_int(away_sot),
        "home_box_shots":   _safe_int(home_box_shots),
        "away_box_shots":   _safe_int(away_box_shots),
        "team_strength":    team_strength,
    }

    # Fast path: market is not BTTS — guard no-ops.
    if not _is_btts_market(current_market):
        return {
            "engine_version":     ENGINE_VERSION,
            "btts_allowed":       True,
            "blocked_market":     None,
            "replacement_market": None,
            "replacement_label":  None,
            "risk":               "LOW",
            "reason_codes":       [],
            "narrative_es":       "",
            "evidence":           base_evidence,
            "_skipped":           "non_btts_market",
        }

    reason_codes: list[str] = []
    has_red_h = (home_red_cards or 0) > 0
    has_red_a = (away_red_cards or 0) > 0
    red_card_team_label: Optional[str] = None

    # ── 1. Bilateral-threat gate (any of the two condition sets must hold).
    has_bilateral = (
        _both_have_xg_threat(home_xg, away_xg, home_shots, away_shots)
        or _both_have_quality_threat(home_sot, away_sot, home_box_shots, away_box_shots)
    )
    if not has_bilateral:
        reason_codes.append("BTTS_BLOCKED_LOW_BILATERAL_THREAT")

    # ── 2. Red-card + low threat on the punished side.
    if has_red_h and _team_threat_collapse(
            xg=home_xg, shots=home_shots, box_shots=home_box_shots):
        reason_codes.append("BTTS_BLOCKED_RED_CARD_LOW_THREAT")
        reason_codes.append("RED_CARD_TEAM_ATTACK_SUPPRESSED")
        red_card_team_label = home_team
    if has_red_a and _team_threat_collapse(
            xg=away_xg, shots=away_shots, box_shots=away_box_shots):
        reason_codes.append("BTTS_BLOCKED_RED_CARD_LOW_THREAT")
        reason_codes.append("RED_CARD_TEAM_ATTACK_SUPPRESSED")
        red_card_team_label = away_team

    # ── 3. Unilateral dominance gate.
    dominance = _is_unilateral_dominance(
        home_xg=home_xg, away_xg=away_xg,
        home_shots=home_shots, away_shots=away_shots,
        home_box=home_box_shots, away_box=away_box_shots,
    )
    if dominance:
        reason_codes.append("BTTS_BLOCKED_UNILATERAL_DOMINANCE")
        reason_codes.append("UNILATERAL_MOMENTUM_NOT_BTTS")

    # ── De-dupe but keep order
    seen = set()
    reason_codes = [r for r in reason_codes if not (r in seen or seen.add(r))]

    # ── Verdict
    btts_allowed = not reason_codes
    if btts_allowed:
        return {
            "engine_version":     ENGINE_VERSION,
            "btts_allowed":       True,
            "blocked_market":     None,
            "replacement_market": None,
            "replacement_label":  None,
            "risk":               "LOW",
            "reason_codes":       [],
            "narrative_es":       "BTTS soportado por amenaza bilateral suficiente.",
            "evidence":           base_evidence,
            "dominance":          None,
        }

    # ── Replacement market.
    score_is_one_zero = (score_home, score_away) in ((1, 0), (0, 1))
    replacement_market: Optional[str] = None
    replacement_label:  Optional[str] = None
    if minute >= LATE_GAME_MINUTE:
        replacement_market = "WATCHLIST"
        replacement_label  = "Watchlist"
        reason_codes.append("BTTS_REPLACED_WITH_WATCHLIST")
    elif score_is_one_zero and minute <= REPLACEMENT_MAX_MINUTE:
        replacement_market = "OVER_1_5"
        replacement_label  = "Más de 1.5 goles"
        reason_codes.append("BTTS_REPLACED_WITH_OVER_1_5")
    else:
        # Marcador 0-0 a >75' o 2+ goles ya en el partido — no auto-pick.
        replacement_market = "WATCHLIST"
        replacement_label  = "Watchlist"
        reason_codes.append("BTTS_REPLACED_WITH_WATCHLIST")

    dom_side = (dominance or {}).get("dominant_side")
    risk = _risk_from_evidence(
        blocked_reasons=reason_codes,
        has_red_card=(has_red_h or has_red_a),
        dominance=dominance,
    )
    narrative = _build_narrative(
        home_team=home_team, away_team=away_team,
        dominant_side=dom_side, dominance=dominance,
        has_red_card_team=red_card_team_label,
        replacement_market=replacement_market,
    )

    return {
        "engine_version":     ENGINE_VERSION,
        "btts_allowed":       False,
        "blocked_market":     "BTTS",
        "replacement_market": replacement_market,
        "replacement_label":  replacement_label,
        "risk":               risk,
        "reason_codes":       reason_codes,
        "narrative_es":       narrative,
        "evidence":           base_evidence,
        "dominance":          dominance,
    }


__all__ = [
    "ENGINE_VERSION",
    "BTTS_MIN_XG_PER_SIDE",
    "BTTS_MIN_SHOTS_PER_SIDE",
    "RED_CARD_LOW_THREAT_XG",
    "RED_CARD_LOW_THREAT_SHOTS",
    "RED_CARD_LOW_THREAT_BOX_SHOTS",
    "UNILATERAL_XG_RATIO",
    "UNILATERAL_SHOTS_RATIO",
    "UNILATERAL_BOX_SHOTS_RATIO",
    "REPLACEMENT_MAX_MINUTE",
    "LATE_GAME_MINUTE",
    "guard_btts_live_recommendation",
    "infer_team_strength_from_odds",
]
