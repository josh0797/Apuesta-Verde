"""F6A — MLB Under Market Selector with Bullpen Risk Awareness.

When the pregame engine recommends a **Full Game Under** because the
Under thesis is supported by *strong starters + pitcher-friendly park +
weak offensive outlook*, the recommendation can still **fail in innings
6-9** if one or both bullpens are vulnerable.

This module adds a single pure function that re-ranks the Under markets
considering bullpen risk and returns a (possibly different) selection:

    select_under_market_with_bullpen_risk(...)

Inputs / Outputs / Logic strictly follow the user spec:

- IF recommended_market is Full Game Under AND
   expected_runs <= full_game_line - 2.0 AND
   pitcher_score is strong AND
   park is pitcher-friendly/neutral-positive AND
   bullpen_risk >= MEDIUM
- THEN:
   * Apply penalty to Full Game Under confidence
   * Mark Full Game Under as "bullpen fragile"
   * Evaluate F5 Under first
   * Evaluate protected alternate full-game under line (10.5 / 11.5)
   * Recommend F5 Under if it has acceptable value

Reason codes emitted:
   BULLPEN_RISK_DOWNGRADES_FULL_GAME_UNDER
   STARTER_PARK_SUPPORTS_F5_UNDER

This module does NOT touch:
- MLB router
- base expected runs model
- existing probability engine
- daily pick generation flow

It only proposes a (possibly different) market selection that the
orchestrator may adopt in the final pick payload.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger("mlb_under_market_selector")

# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _extract_line_number(s: Any) -> Optional[float]:
    if s is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


# ════════════════════════════════════════════════════════════════════════════
# Bullpen risk normaliser
# ════════════════════════════════════════════════════════════════════════════
def _normalise_bullpen_risk(bullpen_risk: Any,
                             bullpen_fatigue: Any = None,
                             bullpen_era_7d: Any = None) -> str:
    """Coerce a variety of bullpen risk inputs into LOW / MEDIUM / HIGH.

    Accepts:
      - explicit string: "LOW" | "MEDIUM" | "HIGH" | "low"...
      - numeric 0-100 score (higher = worse)
      - structured dict with 'level' or 'score'
      - ERA-7d numeric (>=4.50 ⇒ HIGH; 4.00-4.49 ⇒ MEDIUM; <4.00 ⇒ LOW)
    """
    # 1) explicit string
    if isinstance(bullpen_risk, str):
        v = bullpen_risk.strip().upper()
        if v in ("LOW", "MEDIUM", "HIGH"):
            return v
    # 2) numeric score
    if isinstance(bullpen_risk, (int, float)):
        if bullpen_risk >= 60:
            return "HIGH"
        if bullpen_risk >= 35:
            return "MEDIUM"
        return "LOW"
    # 3) dict
    if isinstance(bullpen_risk, dict):
        lvl = (bullpen_risk.get("level") or "").upper()
        if lvl in ("LOW", "MEDIUM", "HIGH"):
            return lvl
        score = bullpen_risk.get("score") or bullpen_risk.get("risk_score")
        if score is not None:
            return _normalise_bullpen_risk(score)
    # 4) ERA-7d fallback
    if bullpen_era_7d is not None:
        era = _f(bullpen_era_7d)
        if era >= 4.50:
            return "HIGH"
        if era >= 4.00:
            return "MEDIUM"
        if era > 0:
            return "LOW"
    # 5) fatigue fallback (0-100 score)
    if bullpen_fatigue is not None:
        f = _f(bullpen_fatigue)
        if f >= 60:
            return "HIGH"
        if f >= 35:
            return "MEDIUM"
        return "LOW"
    return "LOW"


def _normalise_park_factor(park_factor: Any) -> str:
    """Coerce park factor into PITCHER_FRIENDLY / NEUTRAL / HITTER_FRIENDLY."""
    if isinstance(park_factor, str):
        v = park_factor.strip().upper()
        if "PITCHER" in v:
            return "PITCHER_FRIENDLY"
        if "HITTER" in v:
            return "HITTER_FRIENDLY"
        return "NEUTRAL"
    if isinstance(park_factor, (int, float)):
        if park_factor <= 0.97:
            return "PITCHER_FRIENDLY"
        if park_factor >= 1.05:
            return "HITTER_FRIENDLY"
        return "NEUTRAL"
    if isinstance(park_factor, dict):
        if "park_runs_mult" in park_factor:
            return _normalise_park_factor(park_factor["park_runs_mult"])
        if "label" in park_factor:
            return _normalise_park_factor(park_factor["label"])
    return "NEUTRAL"


# ════════════════════════════════════════════════════════════════════════════
# CORE: market selector
# ════════════════════════════════════════════════════════════════════════════
def select_under_market_with_bullpen_risk(
    *,
    expected_runs:        Optional[float],
    full_game_total_line: Optional[float],
    f5_total_line:        Optional[float] = None,
    pitcher_score:        Optional[float] = None,
    starter_quality:      Optional[float] = None,
    park_factor:          Any = None,
    bullpen_risk:         Any = None,
    bullpen_fatigue:      Any = None,
    bullpen_era_7d:       Any = None,
    offensive_outlook:    Optional[float] = None,
    available_markets:    Optional[list[dict]] = None,
    current_selection:    Optional[dict] = None,
) -> dict:
    """Re-rank the Under markets given bullpen risk.

    Parameters
    ----------
    expected_runs : float
        Pregame engine's expected runs (Poisson mean).
    full_game_total_line : float
        Current Full Game Under line (e.g. 9.5).
    f5_total_line : float, optional
        Available F5 (innings 1-5) Under line (e.g. 4.5).
    pitcher_score : float
        Aggregate pitcher quality 0-100 (avg of home/away).
    starter_quality : float, optional
        Same as pitcher_score, accepted alias for backwards compat.
    park_factor : str | float | dict
        Anything coercible to PITCHER_FRIENDLY / NEUTRAL / HITTER_FRIENDLY.
    bullpen_risk : str | float | dict
        Anything coercible to LOW / MEDIUM / HIGH.
    bullpen_fatigue, bullpen_era_7d : optional fallbacks for bullpen_risk.
    offensive_outlook : float, optional
        0-100 score (higher = better offenses).
    available_markets : list[dict], optional
        Each item: {"market": "Full Game Under 9.5", "line": 9.5, "score": 75}.
        Used to confirm F5 Under or alternate full-game lines exist.
    current_selection : dict, optional
        The market currently chosen by the orchestrator
        (e.g. {"market": "Total Runs Under", "score": 75, ...}).

    Returns
    -------
    {
        "selected_market":        dict | None,        # the (possibly new) selection
        "rejected_markets":       list[dict],         # markets that were down-ranked
        "bullpen_fragility_warning": bool,
        "confidence_adjustment":  int (subtractive),
        "explanation":            str (es),
        "reason_codes":           list[str],
        "ranking":                list[dict],         # final ordered ranking
        "rule_triggered":         bool,
        "bullpen_risk_level":     "LOW" | "MEDIUM" | "HIGH",
        "park_label":             str,
    }
    """
    # Normalise inputs.
    er         = _f(expected_runs, 0.0)
    fg_line    = _f(full_game_total_line, 0.0)
    f5_line    = f5_total_line  # keep None if missing
    p_score    = _f(pitcher_score if pitcher_score is not None else starter_quality, 50.0)
    park_lbl   = _normalise_park_factor(park_factor)
    bp_lvl     = _normalise_bullpen_risk(bullpen_risk, bullpen_fatigue, bullpen_era_7d)
    _ = _f(offensive_outlook, 50.0)   # reserved for future weighting
    avail      = list(available_markets or [])
    selection  = dict(current_selection or {})

    rejected: list[dict] = []
    reason_codes: list[str] = []
    explanation_parts: list[str] = []
    rule_triggered = False
    bullpen_fragility_warning = False
    confidence_adjustment = 0

    selection_market = (selection.get("market") or "").strip()
    selection_market_lower = selection_market.lower()

    # Detect: are we currently on a Full Game Under?
    is_full_game_under = (
        "under" in selection_market_lower
        and "f5" not in selection_market_lower
        and "first 5" not in selection_market_lower
        and "team total" not in selection_market_lower
        and "nrfi" not in selection_market_lower
    )
    # Allow Full Game Under via current_selection.line if explicitly tagged.
    if not is_full_game_under and selection.get("line") and "under" in selection_market_lower:
        # market without explicit "full game" wording: assume full game
        is_full_game_under = True

    # Pre-conditions for rule.
    edge_runs = fg_line - er if fg_line > 0 else 0
    starter_strong = p_score >= 60
    park_supportive = park_lbl in ("PITCHER_FRIENDLY", "NEUTRAL")
    edge_supports_under = edge_runs >= 2.0

    # Trigger condition.
    rule_should_apply = (
        is_full_game_under
        and edge_supports_under
        and starter_strong
        and park_supportive
        and bp_lvl in ("MEDIUM", "HIGH")
    )

    # ── Build ranking ────────────────────────────────────────────────────
    ranking: list[dict] = []

    # F5 Under candidate. Acceptable when:
    #   - f5_line is provided OR a market in available_markets contains "F5 Under"
    #   - expected_runs implies F5 runs (er * 5/9) below f5_line - 0.5
    f5_market = None
    if f5_line:
        f5_market = {"market": f"F5 Total Runs Under {f5_line}", "line": float(f5_line),
                      "kind": "F5_UNDER"}
    else:
        for m in avail:
            mkt = (m.get("market") or "").lower()
            if "f5" in mkt and "under" in mkt:
                f5_market = {**m, "kind": "F5_UNDER"}
                f5_market.setdefault("line", _extract_line_number(m.get("market")))
                break

    if f5_market and f5_market.get("line"):
        f5_expected = er * 5.0 / 9.0
        f5_edge = f5_market["line"] - f5_expected
        f5_score = 70.0 + min(15.0, max(-15.0, f5_edge * 6.0))
        # F5 is friendlier to bullpen risk by construction.
        ranking.append({
            **f5_market,
            "score":     round(f5_score, 1),
            "expected":  round(f5_expected, 2),
            "edge":      round(f5_edge, 2),
            "category":  "F5_UNDER",
        })

    # Protected Full Game Under (10.5 / 11.5) — only when an alternate line is
    # available and the edge survives.
    protected_alt = None
    for m in avail:
        mkt = (m.get("market") or "").lower()
        ln  = m.get("line") or _extract_line_number(m.get("market"))
        if ln is None:
            continue
        if "under" in mkt and "f5" not in mkt and "team total" not in mkt:
            try:
                ln_f = float(ln)
            except (TypeError, ValueError):
                continue
            if ln_f > fg_line + 0.5:  # alternate higher line
                alt_edge = ln_f - er
                if alt_edge >= 2.5:    # require a healthy buffer
                    protected_alt = {
                        **m,
                        "line":     ln_f,
                        "edge":     round(alt_edge, 2),
                        "score":    65.0 + min(15.0, alt_edge * 4.0),
                        "category": "PROTECTED_FULL_GAME_UNDER",
                    }
                    break
    if protected_alt:
        ranking.append(protected_alt)

    # Standard Full Game Under — penalised when rule triggers.
    fg_score_base = 75.0 if er <= fg_line - 1.5 else 60.0
    fg_penalty = 0
    if bp_lvl == "MEDIUM":
        fg_penalty = 7      # mid of -5..-8
    elif bp_lvl == "HIGH":
        fg_penalty = 12     # mid of -10..-15
    fg_score_final = max(0.0, fg_score_base - (fg_penalty if rule_should_apply else 0))
    fg_market = {
        "market":   f"Full Game Under {fg_line}" if fg_line else "Full Game Under",
        "line":     fg_line,
        "score":    round(fg_score_final, 1),
        "edge":     round(edge_runs, 2),
        "category": "FULL_GAME_UNDER",
        "penalty_applied": fg_penalty if rule_should_apply else 0,
    }
    ranking.append(fg_market)

    # Sort by score desc.
    ranking.sort(key=lambda m: _f(m.get("score"), 0), reverse=True)

    # ── Decision tree ────────────────────────────────────────────────────
    # Strict priority order from the spec:
    #   1) F5 Under (when viable)
    #   2) Protected Full Game Under (alternate higher line)
    #   3) Standard Full Game Under (if confidence survives penalty)
    #   4) No Bet / manual review
    f5_candidate = next((m for m in ranking if m.get("category") == "F5_UNDER"), None)
    f5_viable = bool(
        f5_candidate
        and _f(f5_candidate.get("edge"), 0) >= 0.4   # F5 expected runs at least ~0.4 below line
        and _f(f5_candidate.get("score"), 0) >= 65
    )

    if rule_should_apply:
        rule_triggered = True
        bullpen_fragility_warning = True
        confidence_adjustment = -fg_penalty
        reason_codes.append("BULLPEN_RISK_DOWNGRADES_FULL_GAME_UNDER")
        explanation_parts.append(
            "Full Game Under marcado como bullpen-frágil: la tesis Under está "
            "sostenida por starters + parque, pero el bullpen muestra riesgo "
            f"{bp_lvl.lower()}."
        )
        if f5_viable:
            # PRIORITY 1: F5 Under — covers exactly the innings the thesis is valid for.
            reason_codes.append("STARTER_PARK_SUPPORTS_F5_UNDER")
            explanation_parts.append(
                f"F5 Under {f5_candidate['line']} preferido: cubre exactamente "
                "los innings de los abridores donde la tesis es válida."
            )
            selected = f5_candidate
            rejected.append(fg_market)
            if protected_alt:
                rejected.append(protected_alt)
        elif protected_alt:
            # PRIORITY 2: Protected Full Game Under (alternate higher line).
            explanation_parts.append(
                f"Protected Full Game Under {protected_alt['line']} preferido: "
                "buffer extra de carreras compensa el riesgo de bullpen."
            )
            selected = protected_alt
            rejected.append(fg_market)
        elif fg_score_final >= 65:
            # PRIORITY 3: Keep Full Game Under but with explicit warning.
            explanation_parts.append(
                f"Full Game Under {fg_line} mantenido pero con penalización "
                f"de confianza ({fg_penalty} pts) por bullpen frágil."
            )
            selected = fg_market
        else:
            # PRIORITY 4: No Bet / manual review.
            explanation_parts.append(
                "Edge insuficiente tras penalización de bullpen — preferir "
                "revisión manual o no apostar."
            )
            selected = None
            rejected.append(fg_market)
    else:
        # Rule didn't trigger — keep existing selection.
        if is_full_game_under:
            selected = fg_market
        elif selection:
            selected = selection
        else:
            selected = ranking[0] if ranking else None

    # Build final explanation string.
    if not explanation_parts:
        if is_full_game_under and bp_lvl == "LOW":
            explanation_parts.append(
                "Bullpen sin riesgo elevado — Full Game Under sostenible."
            )
        elif not is_full_game_under:
            explanation_parts.append(
                "Selección actual no es Full Game Under — regla no aplica."
            )
        else:
            explanation_parts.append(
                "Condiciones no cumplidas para activar el downgrade por bullpen."
            )

    explanation = " ".join(explanation_parts).strip()

    return {
        "selected_market":           selected,
        "rejected_markets":          rejected,
        "bullpen_fragility_warning": bullpen_fragility_warning,
        "confidence_adjustment":     confidence_adjustment,
        "explanation":               explanation,
        "reason_codes":              reason_codes,
        "ranking":                   ranking,
        "rule_triggered":            rule_triggered,
        "bullpen_risk_level":        bp_lvl,
        "park_label":                park_lbl,
        "preconditions": {
            "is_full_game_under":  is_full_game_under,
            "edge_supports_under": edge_supports_under,
            "starter_strong":      starter_strong,
            "park_supportive":     park_supportive,
            "expected_runs":       round(er, 2),
            "full_game_line":      fg_line,
            "edge_runs":           round(edge_runs, 2),
            "pitcher_score":       round(p_score, 1),
        },
    }


__all__ = [
    "select_under_market_with_bullpen_risk",
]
