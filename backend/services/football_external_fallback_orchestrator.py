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
    # Phase F72 — audit verdict guides what we apply.
    f72_audit         = external.get("forebet_audit") or {}
    direction_audit   = f72_audit.get("forebet_direction_signal") or {}
    scoreline_audit   = f72_audit.get("forebet_scoreline_audit") or {}
    direction_status  = direction_audit.get("status")
    scoreline_status  = scoreline_audit.get("status")
    opponent_audit    = f72_audit.get("opponent_strength_audit") or {}

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
    #   Phase F72 — but ONLY when the F72 scoreline audit allows it
    #   (status == TRUSTED). If status is DEGRADED or BLOCKED, we keep
    #   the internal score but annotate the degradation reason so the
    #   UI can suppress its display.
    if fb_score and fb_xg is not None:
        internal_score = score_sec.get("score")
        forebet_trusted = scoreline_status in (None, "TRUSTED")
        scoreline_blocked = scoreline_status == "BLOCKED_FOR_AGGRESSIVE_MARKETS"
        scoreline_degraded = scoreline_status == "DEGRADED"
        if forebet_trusted and (not internal_score
                                 or _scores_contradict(internal_score, fb_score)):
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
        elif scoreline_blocked or scoreline_degraded:
            # Annotate the internal score with the degradation reason so
            # the UI hides the aggressive scoreline / Over.
            score_sec["external_audit_status"] = scoreline_status
            score_sec["external_audit_text"]   = scoreline_audit.get("text")
            score_sec["external_audit_block_aggressive_overs"] = bool(
                scoreline_audit.get("block_aggressive_overs")
            )
            score_sec["reason_codes"] = (score_sec.get("reason_codes") or []) + [
                f"FOREBET_SCORELINE_{scoreline_status}"
            ]
            audit["actions"].append({
                "type":             "PROBABLE_SCORE_AUDIT_ANNOTATION",
                "scoreline_status": scoreline_status,
                "block_aggressive_overs":
                    bool(scoreline_audit.get("block_aggressive_overs")),
            })
            audit["reason_codes"].append(
                f"FOREBET_SCORELINE_{scoreline_status}_APPLIED"
            )
            audit["applied"] = True
            secs["probable_score"] = score_sec
        elif internal_score == fb_score:
            audit["reason_codes"].append("PROBABLE_SCORE_INTERNAL_AGREES_WITH_FOREBET")

    # ── Rule 2: Over/Under tilt from expected_goals.
    #   Phase F72 — gate by audit verdicts:
    #     * Direction CONFLICTED → skip OVER tilt (Forebet may be wrong).
    #     * Scoreline BLOCKED_FOR_AGGRESSIVE_MARKETS → skip OVER tilt
    #       (we already blocked aggressive overs).
    if fb_xg is not None:
        try:
            xg = float(fb_xg)
        except Exception:  # noqa: BLE001
            xg = None
        # Apply gate.
        skip_over_tilt = (
            direction_status == "CONFLICTED"
            or scoreline_status == "BLOCKED_FOR_AGGRESSIVE_MARKETS"
        )
        if xg is not None and skip_over_tilt and xg >= 2.8:
            audit["reason_codes"].append("EXTERNAL_OVER_TILT_SUPPRESSED_BY_AUDIT")
            xg_for_tilt = None
        else:
            xg_for_tilt = xg
        if xg_for_tilt is not None:
            goals_sec = secs.get("goals_prediction") or {}
            if xg_for_tilt >= 2.8:
                audit["actions"].append({
                    "type":   "OU_TILT",
                    "to":     "OVER",
                    "xg":     xg_for_tilt,
                })
                audit["reason_codes"].append("EXTERNAL_TILT_OVER_FROM_FOREBET_XG")
                goals_sec["external_tilt"] = "OVER"
                goals_sec["external_tilt_reason"] = (
                    f"Forebet estima {xg_for_tilt:.2f} goles → inclinación Over"
                )
            elif xg_for_tilt <= 2.1:
                audit["actions"].append({
                    "type":   "OU_TILT",
                    "to":     "UNDER",
                    "xg":     xg_for_tilt,
                })
                audit["reason_codes"].append("EXTERNAL_TILT_UNDER_FROM_FOREBET_XG")
                goals_sec["external_tilt"] = "UNDER"
                goals_sec["external_tilt_reason"] = (
                    f"Forebet estima {xg_for_tilt:.2f} goles → inclinación Under"
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
    # Phase F72 — surface verdict at top-level so the UI can render it.
    if direction_status:
        audit["direction_status"]  = direction_status
        audit["direction_text"]    = direction_audit.get("text")
        audit["direction_favorite"] = direction_audit.get("favorite")
    if scoreline_status:
        audit["scoreline_status"]  = scoreline_status
        audit["scoreline_text"]    = scoreline_audit.get("text")
        audit["scoreline_block_aggressive_overs"] = bool(
            scoreline_audit.get("block_aggressive_overs")
        )
    if opponent_audit.get("available"):
        audit["opponent_strength_text"] = opponent_audit.get("text")
        audit["goals_inflation_risk"]    = opponent_audit.get("goals_inflation_risk")

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

    # Step 1.5 — Sprint E.1.1-f · 365Scores Top Trends.
    # Replaces SportyTrader as the primary editorial-style fallback.
    # SportyTrader is kept under its key but marked as deprecated +
    # ``replaced_by="score365_trends"`` so legacy consumers that read
    # ``ext.sportytrader`` continue to work without crashing.
    try:
        from services.external_sources.score365_trends_client import (
            fetch_top_trends,
        )
        trends_res = await fetch_top_trends(
            match_payload,
            home_team=match_payload.get("home_team"),
            away_team=match_payload.get("away_team"),
            language="es",
        )
        out["top_trends"] = trends_res
        if trends_res.get("available"):
            out["available"] = True
            out["sources_used"].append("score365_trends")
            out["reason_codes"].append("TOP_TRENDS_FROM_365SCORES")
        else:
            # Persist a clear reason so the UI / debug endpoint can
            # explain why trends are missing.
            rc = trends_res.get("reason_code") or "SCORE365_TOP_TRENDS_NOT_FOUND"
            if rc not in out["reason_codes"]:
                out["reason_codes"].append(rc)

        # Deprecate the legacy SportyTrader block now that trends are
        # the canonical replacement. We do NOT delete the key (to keep
        # downstream consumers fail-soft) but we annotate it.
        legacy_sporty = out.get("sportytrader") or {}
        out["sportytrader"] = {
            **legacy_sporty,
            "available":   False,
            "deprecated":  True,
            "replaced_by": "score365_trends",
            "deprecation_reason": (
                "SportyTrader queda desactivado como fuente editorial; "
                "ahora se usan las Tendencias Top de 365Scores."
            ),
        }
        # Strip the source-used marker if it was added above.
        out["sources_used"] = [s for s in out["sources_used"]
                                if s != "sportytrader"]
    except Exception as exc:  # noqa: BLE001
        log.warning("[F71_ORCHESTRATOR] score365 trends fetch failed: %s", exc)
        out["reason_codes"].append("SCORE365_TOP_TRENDS_ERROR")

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

    # Phase F72 — Forebet audit (favoritism vs scoreline separated,
    # with opponent-strength and official/friendly splits taken into
    # account). Runs only when Forebet returned a fixture so the audit
    # has something to validate against.
    forebet_payload = (out.get("forebet") or {})
    if forebet_payload.get("forebet_pct_1") is not None:
        try:
            from services.football_external_prediction_audit import (
                audit_forebet_prediction_against_match_splits,
            )
            statsapi_snap = match_payload.get("thestatsapi_snapshot") or None
            audit = audit_forebet_prediction_against_match_splits(
                forebet_payload, match_payload, statsapi=statsapi_snap,
            )
            out["forebet_audit"] = audit
            for c in (audit.get("reason_codes") or []):
                if c not in out["reason_codes"]:
                    out["reason_codes"].append(c)
        except Exception as exc:  # noqa: BLE001
            log.warning("[F72_AUDIT] failed: %s", exc)
            out["reason_codes"].append("FOREBET_AUDIT_ERROR")

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
