"""Baseball Runs Rescue Layer.

Mirrors `basketball_pace_layer.find_basketball_pace_value(...)`. When the
direct-Moneyline / Run-Line market doesn't show value, we use the deep
historical profile (last 10–15 games per team + bullpen + starter) to
project the total runs and discover a tradable alternative market:

  • Total Runs Over / Under
  • Team Total Over / Under
  • Run Line +1.5 (underdog protected line)
  • F5 Moneyline / F5 Total Runs

Output shape matches the rest of the rescue layers: a dict that
`alternative_rescue.attempt_alternative_market_rescue()` can return
directly, with these fields:

    {
        "rescue_market":              "Total Runs Over 8.5",
        "rescue_selection":           "OVER 8.5",
        "rescue_reason":              <string ES>,
        "rescue_confidence":          0–100,
        "fragility_score":            0–100 (higher = less safe),
        "trap_signals_structured":    [<from baseball_trap_signals>],
        "metrics": {
            "projectedTotalRuns": ..., "leagueAvgRunsUsed": ...,
            "lean": "OVER"|"UNDER", "bookmaker_total_line": ...,
            "f5Lean": "OVER"|"UNDER",
        },
        "_engine": "baseball-runs-rescue.1",
    }

The function is fail-soft: returns None whenever there's not enough
signal to recommend anything.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("rescue.baseball_runs")

ENGINE_VERSION = "baseball-runs-rescue.1"
MIN_PROJECTION_MARGIN = 0.6       # runs above/below line needed to recommend
MIN_PROJECTION_MARGIN_HIGH = 1.0  # bigger margin for HIGH confidence
COLD_OFFENSE_THRESHOLD = 0.55     # % of last games below team-total → cold
HOT_OFFENSE_THRESHOLD  = 0.65     # % of last games above team-total → hot


def _extract_book_total(match: dict) -> Optional[float]:
    """Pull current bookmaker total line from odds snapshots if available."""
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    # Common keys for baseball total
    for k in ("Total Runs", "Total", "Over/Under", "Runs Over Under"):
        rows = markets.get(k) or []
        for r in rows or []:
            line_str = r.get("line")
            if isinstance(line_str, (int, float)):
                return float(line_str)
            # selections may carry the line inside the key, e.g. "Over 8.5"
            for sel in (r.get("lines") or {}).keys():
                if isinstance(sel, str):
                    import re as _re
                    m = _re.search(r"(\d+(?:\.\d+)?)", sel)
                    if m:
                        return float(m.group(1))
    return None


def _build_candidates(
    profile: dict,
    *,
    book_total: Optional[float],
    league_avg: float,
) -> list[dict]:
    """Generate alternative-market candidates from the historical profile.

    Returns a sorted list (by confidence desc). Each candidate has the
    same shape as the final rescue output.
    """
    combined = profile.get("combined") or {}
    proj      = combined.get("projectedTotalRuns") or league_avg
    lean      = combined.get("overUnderLean") or "NEUTRAL"
    f5_lean   = combined.get("f5Lean") or "NEUTRAL"
    f5_proj   = combined.get("f5ProjectedRuns")
    fit       = int(combined.get("marketFitScore") or 0)
    base_frag = int(combined.get("fragilityScore") or 0)

    candidates: list[dict] = []

    # ── Candidate 1: Total Runs Over / Under vs book line ────────────────
    if book_total and lean != "NEUTRAL":
        delta = proj - book_total
        if lean == "OVER" and delta >= MIN_PROJECTION_MARGIN:
            conf = min(95, 45 + int(fit * 0.35) + min(20, int(delta * 12)))
            candidates.append({
                "rescue_market":     f"Total Runs Over {book_total:.1f}",
                "rescue_selection":  f"OVER {book_total:.1f}",
                "rescue_confidence": conf,
                "rescue_reason":     (
                    f"Proyección del motor ({proj:.1f}) está {abs(delta):.1f} carreras por encima "
                    f"de la línea ({book_total:.1f}). Bullpens y ofensivas recientes apoyan el Over."
                ),
                "fragility_score":   base_frag,
                "metrics":           {
                    "projectedTotalRuns": proj,
                    "leagueAvgRunsUsed":  league_avg,
                    "lean":               lean,
                    "bookmaker_total_line": book_total,
                    "f5Lean":             f5_lean,
                },
            })
        elif lean == "UNDER" and -delta >= MIN_PROJECTION_MARGIN:
            conf = min(95, 45 + int(fit * 0.35) + min(20, int(-delta * 12)))
            candidates.append({
                "rescue_market":     f"Total Runs Under {book_total:.1f}",
                "rescue_selection":  f"UNDER {book_total:.1f}",
                "rescue_confidence": conf,
                "rescue_reason":     (
                    f"Proyección del motor ({proj:.1f}) está {abs(delta):.1f} carreras por debajo "
                    f"de la línea ({book_total:.1f}). Abridores y dinámica defensiva apoyan el Under."
                ),
                "fragility_score":   base_frag,
                "metrics":           {
                    "projectedTotalRuns": proj,
                    "leagueAvgRunsUsed":  league_avg,
                    "lean":               lean,
                    "bookmaker_total_line": book_total,
                    "f5Lean":             f5_lean,
                },
            })

    # ── Candidate 2: F5 Total Runs (independent of full-game) ────────────
    if f5_proj and f5_lean != "NEUTRAL":
        # The standard MLB F5 line is roughly league_avg * 0.55
        f5_baseline = league_avg * 0.55
        f5_delta = f5_proj - f5_baseline
        if f5_lean == "OVER" and f5_delta >= 0.5:
            candidates.append({
                "rescue_market":     "F5 Total Runs Over",
                "rescue_selection":  f"F5 OVER ~{f5_baseline:.1f}",
                "rescue_confidence": min(85, 40 + int(fit * 0.3)),
                "rescue_reason":     (
                    f"Proyección F5 ({f5_proj:.1f}) por encima de la baseline ({f5_baseline:.1f}). "
                    f"Históricamente ambos equipos producen en innings tempranos."
                ),
                "fragility_score":   base_frag + 5,
                "metrics":           {
                    "f5ProjectedRuns":      f5_proj,
                    "f5BaselineRuns":       f5_baseline,
                    "lean":                 f5_lean,
                },
            })
        elif f5_lean == "UNDER" and -f5_delta >= 0.5:
            candidates.append({
                "rescue_market":     "F5 Total Runs Under",
                "rescue_selection":  f"F5 UNDER ~{f5_baseline:.1f}",
                "rescue_confidence": min(85, 40 + int(fit * 0.3)),
                "rescue_reason":     (
                    f"Proyección F5 ({f5_proj:.1f}) por debajo de la baseline ({f5_baseline:.1f}). "
                    f"Abridores históricamente contienen los primeros innings."
                ),
                "fragility_score":   base_frag + 5,
                "metrics":           {
                    "f5ProjectedRuns":      f5_proj,
                    "f5BaselineRuns":       f5_baseline,
                    "lean":                 f5_lean,
                },
            })

    # ── Candidate 3: Run Line +1.5 (underdog protected) ──────────────────
    # If one team's runsForAvg + opp.runsAgainstAvg shows a tight
    # projection (diff < 2.5 runs), Run Line +1.5 on the projected loser
    # is a protected play.
    proj_home = combined.get("projectedHomeRuns")
    proj_away = combined.get("projectedAwayRuns")
    if isinstance(proj_home, (int, float)) and isinstance(proj_away, (int, float)):
        diff = abs(proj_home - proj_away)
        if 0.8 <= diff <= 2.2:
            underdog = "away" if proj_home > proj_away else "home"
            ud_name  = "Visitante" if underdog == "away" else "Local"
            candidates.append({
                "rescue_market":     f"Run Line +1.5 — {ud_name}",
                "rescue_selection":  f"{ud_name} +1.5",
                "rescue_confidence": min(80, 35 + int(fit * 0.4)),
                "rescue_reason":     (
                    f"Proyección ajustada (diferencia {diff:.1f} carreras). "
                    f"El {ud_name.lower()} compite sin tener que ganar — Run Line +1.5 protege la entrada."
                ),
                "fragility_score":   base_frag + 10,
                "metrics":           {
                    "projectedHomeRuns": proj_home,
                    "projectedAwayRuns": proj_away,
                    "projectionDelta":   round(diff, 2),
                },
            })

    # ── Candidate 4: Team Total Under for cold offenses ──────────────────
    for side_key, name in (("home", "Local"), ("away", "Visitante")):
        block = profile.get(side_key) or {}
        failed = block.get("failedToReachTeamTotalRate") or 0
        n = block.get("gamesAnalyzed") or 0
        if failed >= COLD_OFFENSE_THRESHOLD and n >= 8:
            thr = block.get("teamTotalThreshold", 4.5)
            n_fail = int(round(failed * n))
            candidates.append({
                "rescue_market":     f"Team Total Under {thr:.1f} — {name}",
                "rescue_selection":  f"{name} UNDER {thr:.1f}",
                "rescue_confidence": min(75, 30 + int(failed * 60)),
                "rescue_reason":     (
                    f"{name} no superó {thr:.1f} carreras en {n_fail} de sus últimos {n} partidos "
                    f"({int(failed*100)}%). Patrón consistente de ofensiva fría."
                ),
                "fragility_score":   base_frag + 12,
                "metrics":           {
                    "teamTotalThreshold": thr,
                    "failedRate":         failed,
                },
            })

    # Sort: highest confidence first, then lowest fragility
    candidates.sort(
        key=lambda c: (-(c.get("rescue_confidence") or 0), c.get("fragility_score") or 100),
    )
    return candidates


def find_baseball_runs_value(
    match: dict,
    *,
    why_direct_failed: Optional[str] = None,
) -> Optional[dict]:
    """Entry point used by `alternative_rescue.attempt_alternative_market_rescue`.

    Returns the best candidate when the historical profile is available,
    otherwise None.
    """
    profile = match.get("baseballHistoricalProfile") or {}
    if not profile or not profile.get("available"):
        return None

    combined   = profile.get("combined") or {}
    league_avg = combined.get("leagueAvgRunsUsed") or 9.0
    book_total = _extract_book_total(match)

    cands = _build_candidates(profile, book_total=book_total, league_avg=league_avg)
    if not cands:
        return None

    best = cands[0]
    best["_engine"]            = ENGINE_VERSION
    best["why_direct_failed"]  = why_direct_failed or ""
    # Preserve raw profile + the rest of the candidate stack for the UI
    best["baseballHistoricalProfile"] = profile
    best["alternative_candidates"]    = cands[1:4]
    return best


__all__ = [
    "find_baseball_runs_value",
    "ENGINE_VERSION",
]
