"""Parlay Correlation Validator — analyses pick combinations for hidden
positive/negative correlations and concentration risk.

A good parlay isn't just "top N picks by individual score". It's a
combination where:
  • Picks don't repeat the same risk (same pitcher, same weather,
    same stadium).
  • Picks don't contradict each other (Run Line -1.5 + Under tight).
  • Picks ideally reinforce each other (Run Line favorite + Over).

Public API
==========
    correlation_validator(picks)               # pure function, no I/O
    parlay_builder(picks, *, max_size=4, db=None)  # picks the best combo
                                                   # of size 2..max_size

Pick contract (each item in `picks` should at least carry):
    {
        "game_pk":     int | str,           # MLB game id
        "match_label": str,                 # "Yankees vs Red Sox"
        "home_team":   str,
        "away_team":   str,
        "venue":       str,
        "weather_tags":list[str],           # e.g. ["WIND_OUT_OVER"]
        "pitcher_home_id": int | None,
        "pitcher_away_id": int | None,
        "recommendation": { "market": str, "selection": str, "side": str,
                            "score": int, "confidence": int },
        "scores":      { "pitcher_edge": int, "fragility": int, ... },
        "editorial_context_signals": list[dict],
    }

The `recommendation.market` strings the validator understands (case-
insensitive substring match):
    "Run Line", "Moneyline", "Total Runs", "Over", "Under",
    "Team Total Over", "Team Total Under", "F5", "First 5",
    "NRFI", "YRFI", "Spread", "+1.5", "-1.5".
"""
from __future__ import annotations

import itertools
import logging
import re
from typing import Any, Optional

log = logging.getLogger("parlay_correlation_validator")

# ════════════════════════════════════════════════════════════════════════════
# MARKET CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════

# Side hints — which "narrative" a pick belongs to. Used by the matrix.
_SIDE_OVER     = "OVER"
_SIDE_UNDER    = "UNDER"
_SIDE_HOME     = "HOME"
_SIDE_AWAY     = "AWAY"
_SIDE_NEUTRAL  = "NEUTRAL"


def _market_kind(market: str) -> str:
    """Normalize a market string to a stable kind token."""
    m = (market or "").lower()
    if "nrfi" in m:                       return "NRFI"
    if "yrfi" in m:                       return "YRFI"
    if "team total over"  in m:           return "TEAM_TOTAL_OVER"
    if "team total under" in m:           return "TEAM_TOTAL_UNDER"
    if re.search(r"\bf5\b|first 5", m):
        if "under" in m: return "F5_UNDER"
        if "over"  in m: return "F5_OVER"
        if "ml" in m or "moneyline" in m: return "F5_ML"
        if "run line" in m or "spread" in m: return "F5_RL"
        return "F5_OTHER"
    if "run line" in m or "spread" in m or "-1.5" in m or "+1.5" in m:
        return "RUN_LINE"
    if "moneyline" in m or "money line" in m or "ml" == m.strip():
        return "MONEYLINE"
    if "over" in m:  return "OVER"
    if "under" in m: return "UNDER"
    return "OTHER"


def _market_side(market_kind: str, selection: str = "") -> str:
    """Map a market_kind + selection to a side."""
    sel = (selection or "").lower()
    if market_kind in ("OVER", "F5_OVER", "TEAM_TOTAL_OVER", "YRFI"):
        return _SIDE_OVER
    if market_kind in ("UNDER", "F5_UNDER", "TEAM_TOTAL_UNDER", "NRFI"):
        return _SIDE_UNDER
    if market_kind in ("RUN_LINE", "F5_RL", "MONEYLINE", "F5_ML"):
        if "+1.5" in sel or "underdog" in sel: return _SIDE_AWAY  # generic
        return _SIDE_HOME if "home" in sel else _SIDE_NEUTRAL
    return _SIDE_NEUTRAL


def _pick_meta(p: dict) -> dict:
    rec = p.get("recommendation") or {}
    market_str = str(rec.get("market") or p.get("market") or "")
    selection  = str(rec.get("selection") or p.get("selection") or "")
    kind       = _market_kind(market_str)
    side       = _market_side(kind, selection)
    score      = int(rec.get("score") or p.get("score") or
                     (p.get("scores") or {}).get(kind.lower(), 0) or 60)
    return {
        "game_pk":   p.get("game_pk") or p.get("match_id") or p.get("id"),
        "market":    market_str,
        "kind":      kind,
        "side":      side,
        "selection": selection,
        "score":     score,
        "label":     p.get("match_label") or
                     f"{p.get('away_team','?')} @ {p.get('home_team','?')}",
        "venue":     p.get("venue") or "",
        "weather":   list(p.get("weather_tags") or []),
        "pitchers":  {p.get("pitcher_home_id"), p.get("pitcher_away_id")} - {None},
        "home_team": p.get("home_team") or "",
        "away_team": p.get("away_team") or "",
    }


# ════════════════════════════════════════════════════════════════════════════
# CORRELATION RULES (sport-aware — currently MLB-only by design)
# ════════════════════════════════════════════════════════════════════════════

def _same_game(a: dict, b: dict) -> bool:
    return bool(a["game_pk"]) and a["game_pk"] == b["game_pk"]


def _same_venue(a: dict, b: dict) -> bool:
    return bool(a["venue"]) and a["venue"] == b["venue"]


def _share_pitcher(a: dict, b: dict) -> bool:
    return bool(a["pitchers"] & b["pitchers"])


def _share_team(a: dict, b: dict) -> bool:
    teams_a = {a["home_team"], a["away_team"]} - {""}
    teams_b = {b["home_team"], b["away_team"]} - {""}
    return bool(teams_a & teams_b)


def _share_weather(a: dict, b: dict) -> bool:
    return bool(set(a["weather"]) & set(b["weather"]))


def _classify_pair(a: dict, b: dict) -> Optional[dict]:
    """Return a positive/negative correlation dict for the pair, or None."""
    # Same-game heuristics first — strongest signal.
    if _same_game(a, b):
        # Run Line favorite + Over of same game.
        if a["kind"] == "RUN_LINE" and b["kind"] in ("OVER", "F5_OVER") \
           or b["kind"] == "RUN_LINE" and a["kind"] in ("OVER", "F5_OVER"):
            return _pos(a, b, 12,
                "El Run Line del favorito se beneficia de alta producción "
                "ofensiva, lo cual también favorece el Over.")
        # Run Line favorite + Team Total Over of the favorite.
        if (a["kind"], b["kind"]) in (("RUN_LINE", "TEAM_TOTAL_OVER"),
                                      ("TEAM_TOTAL_OVER", "RUN_LINE")):
            return _pos(a, b, 10,
                "Si el favorito anota mucho, aumenta posibilidad de cubrir -1.5.")
        # NRFI + Under (same game).
        if {a["kind"], b["kind"]} == {"NRFI", "UNDER"}:
            return _pos(a, b, 5,
                "NRFI + Under: si el partido empieza cerrado, perfil bajo de carreras.")
        # NRFI + F5 Under.
        if {a["kind"], b["kind"]} == {"NRFI", "F5_UNDER"}:
            return _pos(a, b, 8,
                "NRFI + F5 Under: ambos exigen pocas carreras en innings tempranos.")
        # ── NEGATIVE ──
        # Run Line favorite + Under tight (line ≤ 8.0). We approximate "tight"
        # by checking that selection mentions "Under 7" or "Under 8" but not 9+.
        if a["kind"] == "RUN_LINE" and b["kind"] == "UNDER":
            if re.search(r"\b(under)\s*([5-8](?:\.5)?)\b", b["selection"], re.I):
                return _neg(a, b, -12,
                    "El Run Line necesita margen ofensivo, pero Under bajo "
                    "limita el rango de anotación.")
        if b["kind"] == "RUN_LINE" and a["kind"] == "UNDER":
            if re.search(r"\b(under)\s*([5-8](?:\.5)?)\b", a["selection"], re.I):
                return _neg(a, b, -12,
                    "El Run Line necesita margen ofensivo, pero Under bajo "
                    "limita el rango de anotación.")
        # Run Line favorite + Team Total Under of the FAVORITE = direct contradiction
        if (a["kind"], b["kind"]) in (("RUN_LINE", "TEAM_TOTAL_UNDER"),
                                      ("TEAM_TOTAL_UNDER", "RUN_LINE")):
            return _neg(a, b, -20,
                "Run Line favorito + Team Total Under del mismo equipo: "
                "contradicción directa.")
        # YRFI + Under tight.
        if {a["kind"], b["kind"]} == {"YRFI", "UNDER"}:
            return _neg(a, b, -8,
                "YRFI exige carrera en 1er inning; Under tight pierde colchón.")
        # 2 picks same game without a known correlation → mild positive (linked
        # narrative) but flagged as concentration risk by the caller.
        return _pos(a, b, 3,
            "Mismo juego — narrativa común; revisa concentración.")

    # Different games — softer correlations.
    if _share_pitcher(a, b):
        # Two picks tied to the same pitcher — concentration risk.
        return _neg(a, b, -6,
            "Ambos picks dependen del mismo pitcher.")
    if _same_venue(a, b):
        return _neg(a, b, -5,
            "Mismo estadio — riesgo repetido por parque/clima.")
    if _share_team(a, b):
        return _neg(a, b, -4,
            "Mismo equipo en ambos picks — exposición concentrada.")
    if _share_weather(a, b):
        # Two Overs both depending on wind: penalize.
        if a["side"] == b["side"] == _SIDE_OVER:
            return _neg(a, b, -7,
                "Ambos Overs dependen del mismo viento — riesgo correlacionado.")
        if a["side"] == b["side"] == _SIDE_UNDER:
            return _neg(a, b, -5,
                "Ambos Unders dependen del mismo clima — riesgo correlacionado.")
    # Both Overs / both Unders across the slate (different games) — soft penalty
    # only applied via concentration check, not pairwise.
    return None


def _pos(a: dict, b: dict, impact: int, reason: str) -> dict:
    return {
        "type":   "POSITIVE",
        "picks":  [a["label"] + " · " + a["market"], b["label"] + " · " + b["market"]],
        "reason": reason,
        "impact": int(impact),
    }


def _neg(a: dict, b: dict, impact: int, reason: str) -> dict:
    return {
        "type":   "NEGATIVE",
        "picks":  [a["label"] + " · " + a["market"], b["label"] + " · " + b["market"]],
        "reason": reason,
        "impact": int(impact),
    }


# ════════════════════════════════════════════════════════════════════════════
# CONCENTRATION RISK
# ════════════════════════════════════════════════════════════════════════════
def _concentration_warnings(metas: list[dict]) -> tuple[list[str], int]:
    """Return (warnings, penalty)."""
    warnings: list[str] = []
    penalty = 0

    overs    = sum(1 for m in metas if m["side"] == _SIDE_OVER)
    unders   = sum(1 for m in metas if m["side"] == _SIDE_UNDER)
    if overs >= 3:
        warnings.append(f"{overs} Overs en el parlay — riesgo de día Under generalizado.")
        penalty -= 5
    if unders >= 3:
        warnings.append(f"{unders} Unders en el parlay — riesgo de slugfest global.")
        penalty -= 5

    games   = [m["game_pk"] for m in metas if m["game_pk"]]
    same_game_groups = {}
    for g in games:
        same_game_groups[g] = same_game_groups.get(g, 0) + 1
    repeats = {g: c for g, c in same_game_groups.items() if c >= 2}
    if repeats:
        warnings.append(
            f"Hay {len(repeats)} juego(s) con 2+ picks del mismo encuentro; "
            "validar correlación explícita.")

    venues   = [m["venue"] for m in metas if m["venue"]]
    venue_repeats = {v: venues.count(v) for v in set(venues) if venues.count(v) >= 2}
    if venue_repeats:
        warnings.append(
            f"{len(venue_repeats)} estadio(s) con 2+ picks (clima/parque común). ")
        penalty -= 3

    return warnings, penalty


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════════════
def correlation_validator(picks: list[dict]) -> dict:
    """Analyse a list of MLB picks for correlations + concentration risk.

    Pure function — no I/O. Safe to call from anywhere.
    """
    if not picks or len(picks) < 2:
        return {
            "correlation_score":      100,  # nothing to correlate
            "risk_level":             "LOW",
            "positive_correlations":  [],
            "negative_correlations":  [],
            "warnings":               [],
            "recommended_adjustments": [],
        }

    metas = [_pick_meta(p) for p in picks]
    positive: list[dict] = []
    negative: list[dict] = []

    # Pairwise classification
    total_impact = 0
    for a, b in itertools.combinations(metas, 2):
        c = _classify_pair(a, b)
        if not c:
            continue
        total_impact += c["impact"]
        (positive if c["type"] == "POSITIVE" else negative).append(c)

    warnings, conc_penalty = _concentration_warnings(metas)
    total_impact += conc_penalty

    # Map total_impact (typically -50 .. +30) onto a 0..100 score, centered
    # at 70 (no correlations means a neutral, clean parlay).
    correlation_score = max(0, min(100, 70 + total_impact))
    if correlation_score >= 75:
        risk_level = "LOW"
    elif correlation_score >= 50:
        risk_level = "MEDIUM"
    else:
        risk_level = "HIGH"

    # Recommended adjustments
    adjustments: list[str] = []
    if any(c["impact"] <= -15 for c in negative):
        worst = sorted(negative, key=lambda c: c["impact"])[0]
        adjustments.append(
            f"Considera quitar uno de: {' / '.join(worst['picks'])} (contradicción {worst['impact']}).")
    if risk_level == "HIGH" and len(picks) > 3:
        adjustments.append("Reducir el parlay a 3 picks: la concentración penaliza más de lo que aporta.")
    if not positive and risk_level != "LOW":
        adjustments.append(
            "Ninguna correlación positiva detectada. Considera incluir "
            "un Over del mismo juego donde un favorito tiene Run Line.")

    return {
        "correlation_score":      int(correlation_score),
        "risk_level":             risk_level,
        "positive_correlations":  positive,
        "negative_correlations":  negative,
        "warnings":               warnings,
        "recommended_adjustments": adjustments,
    }


# ════════════════════════════════════════════════════════════════════════════
# PARLAY BUILDER
# ════════════════════════════════════════════════════════════════════════════
def parlay_builder(
    picks: list[dict],
    *,
    max_size: int = 4,
    min_score: int = 60,
) -> dict:
    """Pick the BEST 2..`max_size` combination of `picks` ranked by
    individual score AND correlation_validator quality.

    Strategy
    --------
    1. Sort candidates by individual score (desc), keep top 10.
    2. Enumerate all size 2..max_size subsets (cheap because top 10 →
       C(10,4)=210 max).
    3. Compute combined_score = sum(individual_score) * 0.6
                                + correlation_score * 0.4 * count.
    4. Return the best subset + its correlation report.
    """
    if not picks:
        return {"parlay": [], "validator": correlation_validator([]),
                "combined_score": 0, "size": 0,
                "rejected_count": 0}
    candidates = [p for p in picks
                  if int((p.get("recommendation") or {}).get("score")
                         or p.get("score") or 0) >= min_score]
    rejected_count = len(picks) - len(candidates)
    if not candidates:
        return {"parlay": [], "validator": correlation_validator([]),
                "combined_score": 0, "size": 0,
                "rejected_count": rejected_count}

    # Sort by individual score desc
    candidates.sort(
        key=lambda p: int((p.get("recommendation") or {}).get("score")
                          or p.get("score") or 0),
        reverse=True,
    )
    pool = candidates[:10]

    best: dict = {
        "parlay":          [],
        "validator":       correlation_validator([]),
        "combined_score":  0,
        "size":             0,
        "rejected_count":  rejected_count,
    }
    cap = min(max_size, len(pool))
    for size in range(2, cap + 1):
        for combo in itertools.combinations(pool, size):
            v = correlation_validator(list(combo))
            if v["risk_level"] == "HIGH":
                continue
            sum_indiv = sum(
                int((p.get("recommendation") or {}).get("score")
                    or p.get("score") or 60)
                for p in combo
            )
            combined = sum_indiv * 0.6 + v["correlation_score"] * 0.4 * size
            if combined > best["combined_score"]:
                best = {
                    "parlay":         list(combo),
                    "validator":      v,
                    "combined_score": int(combined),
                    "size":           size,
                    "rejected_count": rejected_count,
                }
    return best


__all__ = ["correlation_validator", "parlay_builder"]
