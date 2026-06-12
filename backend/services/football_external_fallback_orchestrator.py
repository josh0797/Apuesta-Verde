"""Phase F71 — Football external fallback orchestrator.

Cascades external editorial sources when the internal editorial engine
is too thin to provide a confident reading, AND reconciles internal vs
external outputs so the UI never shows a contradictory pair (e.g.
internal says "1-1 heuristic" while Forebet's algorithm says "3-1").

Cascade (per F71 spec, restricted to Forebet + TheStatsAPI + Sportytrader)
==========================================================================
1.  Result / 1X2 / Predicted score / Over-Under / BTTS
    → Forebet (primary)   ─ via ``external_editorial_provider``
    → TheStatsAPI (secondary, when an upstream cache has odds rows)

2.  Corners
    → TheStatsAPI (when available)
    → Else internal L5/L15 (already produced by editorial engine).

3.  Odds / market trap validation
    → TheStatsAPI bookmaker rows (when present in the match payload).

The orchestrator is **fail-soft** end-to-end: any sub-call returning
``available=False`` is silently skipped. The reconciliation step never
RAISES; it only DECORATES the internal editorial dict in place with
suppression / override flags.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from services.market_identity import normalize_market_identity

log = logging.getLogger("external_fallback_orchestrator")


# ─────────────────────────────────────────────────────────────────────
# Reconciliation
# ─────────────────────────────────────────────────────────────────────
def reconcile_internal_vs_external_analysis(internal: dict,
                                              external: dict) -> dict:
    """Reconcile internal editorial dict with external context.

    Rules (per F71 spec):
      * If Forebet has both ``predicted_score`` AND ``expected_goals`` /
        ``goals_avg``, override the probable_score (and SUPPRESS the
        contradictory internal "1-1 heuristic").
      * If external expected_goals ≥ 2.8 → annotate "lean Over".
      * If external expected_goals ≤ 2.1 → annotate "lean Under".
      * If odds_validation marks ``is_market_trap`` → preserve discard;
        flag confidence demotion.

    Returns the mutated ``internal`` dict (also returned) plus an audit
    block injected at ``internal["external_reconciliation"]``.
    """
    if not isinstance(internal, dict):
        return internal
    if not isinstance(external, dict) or not external.get("available"):
        return internal

    forebet = external.get("forebet") or {}
    sporty  = external.get("sportytrader") or {}

    audit: dict[str, Any] = {
        "applied":           False,
        "actions":           [],
        "reason_codes":      [],
        "sources_used":      [],
    }
    if forebet and forebet.get("forebet_pct_1") is not None:
        audit["sources_used"].append("forebet")
    if sporty.get("available"):
        audit["sources_used"].append("sportytrader")

    secs = internal.setdefault("editorial_sections", {})
    score_sec = secs.get("probable_score") or {}

    fb_score = forebet.get("predicted_score")
    fb_xg    = forebet.get("expected_goals") or forebet.get("goals_avg")

    # ── Rule 1: probable_score override when Forebet has BOTH score + xG.
    if fb_score and fb_xg is not None:
        internal_score = score_sec.get("score")
        # If internal score is missing OR contradicts Forebet, prefer
        # Forebet's algorithmic call.
        if not internal_score or _scores_contradict(internal_score, fb_score):
            audit["actions"].append({
                "type":             "PROBABLE_SCORE_OVERRIDE",
                "from":             internal_score,
                "to":               fb_score,
                "source":           "forebet",
                "expected_goals":   fb_xg,
            })
            audit["reason_codes"].append(
                "INTERNAL_PROBABLE_SCORE_SUPPRESSED_BY_FOREBET"
            )
            audit["applied"] = True
            # Suppress the contradictory internal score AND surface the
            # external one in a new field so the UI can render it.
            score_sec["available"]            = True
            score_sec["score"]                = fb_score
            score_sec["method"]               = "FOREBET_ALGORITHMIC"
            score_sec["is_contextual_only"]   = True
            score_sec["text"]                 = (
                f"Marcador algorítmico de Forebet: {fb_score} "
                f"(goles esperados {float(fb_xg):.2f})."
            )
            score_sec["external_override"]    = True
            score_sec["reason_codes"]         = (score_sec.get("reason_codes") or []) + [
                "PROBABLE_SCORE_OVERRIDDEN_BY_EXTERNAL_FOREBET"
            ]
            secs["probable_score"] = score_sec
        elif internal_score == fb_score:
            audit["reason_codes"].append("PROBABLE_SCORE_INTERNAL_AGREES_WITH_FOREBET")

    # ── Rule 2: Over/Under tilt from expected_goals.
    if fb_xg is not None:
        try:
            xg = float(fb_xg)
        except Exception:  # noqa: BLE001
            xg = None
        if xg is not None:
            goals_sec = secs.get("goals_prediction") or {}
            if xg >= 2.8:
                audit["actions"].append({
                    "type":   "OU_TILT",
                    "to":     "OVER",
                    "xg":     xg,
                })
                audit["reason_codes"].append("EXTERNAL_TILT_OVER_FROM_FOREBET_XG")
                goals_sec["external_tilt"] = "OVER"
                goals_sec["external_tilt_reason"] = (
                    f"Forebet estima {xg:.2f} goles → inclinación Over"
                )
            elif xg <= 2.1:
                audit["actions"].append({
                    "type":   "OU_TILT",
                    "to":     "UNDER",
                    "xg":     xg,
                })
                audit["reason_codes"].append("EXTERNAL_TILT_UNDER_FROM_FOREBET_XG")
                goals_sec["external_tilt"] = "UNDER"
                goals_sec["external_tilt_reason"] = (
                    f"Forebet estima {xg:.2f} goles → inclinación Under"
                )
            if "external_tilt" in goals_sec:
                secs["goals_prediction"] = goals_sec
                audit["applied"] = True

    # ── Rule 3: market trap propagation (NOOP unless odds_validation
    # came in with the external context).
    odds_val = external.get("odds_validation") or {}
    if odds_val.get("is_market_trap"):
        audit["reason_codes"].append("MARKET_TRAP_CONFIRMED_BY_EXTERNAL")
        # Demote confidence on any "OK" market in the internal editorial.
        for k in ("corners_prediction", "goals_prediction"):
            sec = secs.get(k) or {}
            if sec.get("status") == "OK" and sec.get("confidence", 0) > 0:
                sec["confidence"] = max(0, int(sec.get("confidence", 0)) - 25)
                sec["reason_codes"] = (sec.get("reason_codes") or []) + [
                    "CONFIDENCE_DEMOTED_BY_EXTERNAL_TRAP"
                ]
                audit["applied"] = True

    internal["external_reconciliation"] = audit
    # Bubble up to top-level reason_codes for telemetry.
    top_codes = internal.get("reason_codes") or []
    for c in audit["reason_codes"]:
        if c not in top_codes:
            top_codes.append(c)
    internal["reason_codes"] = top_codes
    return internal


def _scores_contradict(a: str, b: str) -> bool:
    """Two scoreline strings contradict when their winners differ.

    "1-1" vs "3-1" → contradict (draw vs home win)
    "1-0" vs "2-0" → DO NOT contradict (both home wins)
    "1-1" vs "0-0" → DO NOT contradict (both draws)
    """
    if not a or not b:
        return False
    def _w(s: str) -> Optional[str]:
        parts = s.replace("–", "-").replace("—", "-").split("-")
        if len(parts) != 2:
            return None
        try:
            h, w = int(parts[0]), int(parts[1])
        except Exception:  # noqa: BLE001
            return None
        if h > w:  return "HOME"
        if h < w:  return "AWAY"
        return "DRAW"
    wa, wb = _w(a), _w(b)
    if wa is None or wb is None:
        return False
    return wa != wb


# ─────────────────────────────────────────────────────────────────────
# Cascade builder
# ─────────────────────────────────────────────────────────────────────
async def build_external_fallback_context(match_payload: dict,
                                            *, db: Any = None,
                                            force: bool = False) -> dict:
    """Build the cascaded external context for a single match.

    Sequence:
      1. Try the existing ``external_editorial_provider`` (Forebet +
         Sportytrader). This already implements cache + scrape.do.
      2. Pull TheStatsAPI snapshot (if a hydrated payload exists in
         ``match_payload`` under ``thestatsapi_snapshot``).
      3. Synthesise an ``odds_validation`` block when both market_evaluated
         and odds are present in the entry.
    """
    if not isinstance(match_payload, dict):
        return {"available": False, "reason_codes": ["EXTERNAL_PAYLOAD_INVALID"]}

    out: dict[str, Any] = {
        "available":      False,
        "sources_used":   [],
        "reason_codes":   ["EXTERNAL_FALLBACK_ATTEMPTED"],
    }

    # Step 1 — Forebet + Sportytrader (via the existing provider).
    try:
        from services.external_editorial_provider import (
            fetch_external_editorial_for_match,
        )
        ext = await fetch_external_editorial_for_match(match_payload)
        if ext.get("available"):
            out["available"] = True
            for k in ("home_team", "away_team", "forebet", "sportytrader"):
                if k in ext:
                    out[k] = ext[k]
            for c in (ext.get("reason_codes") or []):
                if c not in out["reason_codes"]:
                    out["reason_codes"].append(c)
            if (out.get("forebet") or {}).get("forebet_pct_1") is not None:
                out["sources_used"].append("forebet")
            if (out.get("sportytrader") or {}).get("available"):
                out["sources_used"].append("sportytrader")
    except Exception as exc:  # noqa: BLE001
        log.warning("[F71_ORCHESTRATOR] external provider failed: %s", exc)
        out["reason_codes"].append("EXTERNAL_PROVIDER_ERROR")

    # Step 2 — TheStatsAPI corners (when an upstream cache exists).
    snap = match_payload.get("thestatsapi_snapshot") or {}
    if isinstance(snap, dict) and snap.get("corners"):
        out["thestatsapi_corners"] = snap.get("corners")
        out["sources_used"].append("thestatsapi")
        out["available"] = True
        out["reason_codes"].append("THESTATSAPI_CORNERS_AVAILABLE")

    # Step 3 — Odds validation skeleton from entry-level numbers.
    odds         = match_payload.get("odds")
    prob_est     = match_payload.get("estimated_probability")
    prob_imp     = match_payload.get("implied_probability")
    edge         = match_payload.get("edge")
    market_eval  = match_payload.get("market_evaluated")
    if any(v is not None for v in (odds, prob_est, prob_imp, edge)):
        identity = normalize_market_identity(
            {"market": market_eval, "side": match_payload.get("side"),
             "line":   match_payload.get("line")},
            home_name=_team_name(match_payload, "home"),
            away_name=_team_name(match_payload, "away"),
        )
        is_trap = False
        reason = None
        if edge is not None:
            edge_pct = float(edge) if abs(float(edge)) > 1 else float(edge) * 100
            if edge_pct <= -10:
                is_trap = True
                reason = (
                    f"Cuota {odds} con edge {edge_pct:+.1f}% — la "
                    "cuota está por debajo del valor implícito que el "
                    "modelo estima."
                )
        out["odds_validation"] = {
            "available":         True,
            "source":            "internal_pricing",
            "market_identity":   identity,
            "odds":              odds,
            "estimated_probability": prob_est,
            "implied_probability":   prob_imp,
            "edge":              edge,
            "is_market_trap":    is_trap,
            "reason":            reason,
        }
        out["reason_codes"].append("ODDS_VALIDATION_GENERATED")

    if not out["sources_used"] and not out.get("odds_validation"):
        out["available"] = False
        out["reason_codes"].append("NO_EXTERNAL_SOURCES_AVAILABLE")
    return out


def _team_name(match: dict, side: str) -> str:
    key = "home_team" if side == "home" else "away_team"
    val = match.get(key)
    if isinstance(val, dict):
        return val.get("name") or val.get("label") or ""
    if isinstance(val, str):
        return val
    flat = match.get("home_team_name" if side == "home" else "away_team_name")
    if isinstance(flat, str):
        return flat
    return ""


__all__ = [
    "build_external_fallback_context",
    "reconcile_internal_vs_external_analysis",
]
