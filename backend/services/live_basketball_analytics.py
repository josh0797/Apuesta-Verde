"""Basketball live analytics — counterpart to `live_xg_proxy.py` for hoops.

Football's xG/xT/pressure proxies don't translate to basketball: there
are no shots-in-box or dangerous-attacks vocabulary, possessions move
ten times faster, and a 15-point lead in Q4 is a fundamentally different
situation than a 2-goal lead at minute 80.

This module produces a parallel-shape `compute_live_analysis()` payload
so the UI's `LiveCopilotCard` and `LiveAnalysisStrip` can render the
same components without sport-aware branching. It uses ONLY data that
API-Sports basketball returns by default (status, score, optional
quarter timer) — no per-quarter shot maps required.

Public API
----------
    compute_live_analysis(match)      → dict
    detect_blowout_trap(...)          → dict | None
    parse_basketball_minute(status)   → tuple[int, float]  (game minute, fraction_remaining)
"""
from __future__ import annotations

from typing import Optional


# A regulation NBA / FIBA game is 4 × 10 (FIBA) or 4 × 12 (NBA). API-Sports
# basketball most often serves NBA so we default to 12-min quarters and
# fall back gracefully. The "game_minute" we compute is for projection
# math only — the UI shows the raw "Q3 04:22" label.
QUARTER_MIN_NBA = 12.0
REG_GAME_MIN_NBA = QUARTER_MIN_NBA * 4
QUARTER_MIN_FIBA = 10.0


def _parse_clock(timer: str | None) -> Optional[float]:
    """Parse a basketball clock string ('mm:ss' / 'mm:ss.t') → minutes remaining
    in the current period, as a float. Returns None on failure."""
    if not timer or not isinstance(timer, str):
        return None
    s = timer.strip().split(".")[0]
    if ":" in s:
        try:
            m, sec = s.split(":")
            return float(m) + float(sec) / 60.0
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None


def parse_basketball_minute(status_short: str | None, timer: str | None,
                            quarter_min: float = QUARTER_MIN_NBA) -> tuple[Optional[int], float]:
    """Translate (status, clock) into (game_minute_elapsed, fraction_remaining 0..1).

    Examples (NBA 12-min quarters):
      ('Q1', '08:30')  → (4, 0.93)            # 3.5 min played, 44.5 left → 0.93
      ('Q3', '04:00')  → (32, 0.33)           # Q3 + 8 min of Q3 done
      ('Q4', '01:18')  → (47, 0.027)
      ('OT', '02:00')  → (50, 0.0+OT)         # past regulation
      ('HT', None)     → (24, 0.5)
    """
    if not status_short:
        return None, 0.5
    s = status_short.upper()
    rem_in_period = _parse_clock(timer)
    if s == "HT":
        return int(quarter_min * 2), 0.5
    if s == "BT":  # Between periods
        return int(quarter_min * 2), 0.5
    if s.startswith("Q"):
        try:
            q = int(s[1])
        except ValueError:
            return None, 0.5
        played_quarters = q - 1
        if rem_in_period is None:
            rem_in_period = quarter_min / 2.0  # midpoint assumption
        played_this_quarter = quarter_min - rem_in_period
        elapsed = played_quarters * quarter_min + played_this_quarter
        total_remaining = max(0.0, (4 * quarter_min) - elapsed)
        return int(elapsed), max(0.0, min(1.0, total_remaining / (4 * quarter_min)))
    if s == "OT":
        # Anywhere in OT: regulation done.
        return int(4 * quarter_min), 0.0
    return None, 0.5


def detect_blowout_trap(
    *,
    period: str | None,
    clock_min_remaining: Optional[float],
    home_score: int,
    away_score: int,
    decimal_odds_for_leader: Optional[float],
    quarter_min: float = QUARTER_MIN_NBA,
) -> Optional[dict]:
    """Basketball-equivalent of the football late-lead trap:
    "favorito ganando con ventaja amplia en Q4 + cuota baja + suplentes/garbage time = NO APOSTAR".

    Trigger conditions (ALL must hold):
      1. Period == "Q4" (or "OT").
      2. Less than 4 minutes remaining in the period.
      3. Absolute lead ≥ 15 points.
      4. Decimal odds for the leader ≤ 1.20 (already priced as locked).
    """
    if not period:
        return None
    p = period.upper()
    if p not in ("Q4", "OT"):
        return None
    if clock_min_remaining is None:
        return None
    if clock_min_remaining > 4.0:
        return None
    lead = abs(home_score - away_score)
    if lead < 15:
        return None
    if decimal_odds_for_leader is None or decimal_odds_for_leader > 1.20:
        return None
    leader = "home" if home_score > away_score else "away"
    trailing = "away" if leader == "home" else "home"
    return {
        "triggered": True,
        "leader_side": leader,
        "trailing_side": trailing,
        "lead": lead,
        "clock_min_remaining": round(clock_min_remaining, 2),
        "decimal_odds_for_leader": decimal_odds_for_leader,
        "reason_es": (
            f"TRAMPA: {p} con menos de {clock_min_remaining:.1f} min, "
            f"ventaja {lead} pts, cuota {decimal_odds_for_leader:.2f}. "
            f"Suplentes en pista, posesiones muertas — no apostar al favorito."
        ),
        "reason_en": (
            f"TRAP: {p} with less than {clock_min_remaining:.1f} min, "
            f"lead {lead} pts, odds {decimal_odds_for_leader:.2f}. "
            f"Bench minutes + garbage time — do not bet the leader."
        ),
    }


# ─── Best odds extractor for basketball ────────────────────────────────────
# Basketball doesn't use 1X2 (no draw). Market keys typically seen on
# API-Sports basketball: "Home/Away" or "Match Winner" + "Total" + "Asian
# Handicap".

def _best_basket_winner_odds(match: dict) -> dict:
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return {}
    markets = (snaps[-1] or {}).get("markets") or {}
    rows = markets.get("Home/Away") or markets.get("Match Winner") or markets.get("1X2") or []
    out: dict[str, Optional[float]] = {"home": None, "away": None}
    for r in rows:
        for k in out:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 1.01:
                if out[k] is None or v > out[k]:
                    out[k] = float(v)
    return {k: v for k, v in out.items() if v is not None}


# ─── Public — compute_live_analysis() (mirrors live_xg_proxy shape) ────────

def compute_live_analysis(match: dict) -> dict:
    """Build the basketball live-analysis payload (same shape as football's).

    The `home`/`away` blocks expose the most useful basketball metrics
    we can compute from API-Sports' free-tier data:

      • points          (current score)
      • points_per_min  (pace — used by Total Points projection)
      • lead_pts        (signed, + for home)
      • projected_total (extrapolated final total points)

    The verdict labels are reused unchanged so the UI doesn't need to
    branch on sport.
    """
    live = match.get("live_stats") or {}
    status = live.get("status") or ""
    timer = live.get("minute")  # API-Sports stuffs the clock-string in here
    # If "minute" is actually a status long-name, prefer status_short.
    score = live.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)

    # Try NBA quarters first; if total points are tiny we'll fall back to FIBA.
    elapsed, frac_remaining = parse_basketball_minute(status, timer if isinstance(timer, str) else None)
    if elapsed is None:
        # No clock data — emit insufficient.
        return _insufficient(match, h_score, a_score, status)

    total_pts = h_score + a_score
    points_per_min = total_pts / max(1, elapsed) if elapsed else 0.0
    projected_total = points_per_min * (4 * QUARTER_MIN_NBA)

    # Clock remaining in this period (for blowout detection).
    clock_rem = _parse_clock(timer if isinstance(timer, str) else None)

    home_block = {
        "points":         h_score,
        "points_per_min": round(points_per_min / 2.0, 2) if total_pts else 0.0,  # half of pace
        "lead_pts":       h_score - a_score,
        "projected_total": round(projected_total, 1),
        # Padded zero-valued analogs of the football block so the UI's
        # generic grid doesn't blow up if it tries to read them.
        "xg_live": 0.0, "threat_index": 0.0, "pressure_rate": 0.0,
        "shots": 0, "shots_on_target": 0, "shots_in_box": 0,
        "possession": 0, "corners": 0, "dangerous": 0, "attacks": 0,
    }
    away_block = {**home_block, "points": a_score, "lead_pts": a_score - h_score}
    away_block["points_per_min"] = home_block["points_per_min"]

    # Trap?
    leader_odds = _best_basket_winner_odds(match).get(
        "home" if h_score > a_score else "away" if a_score > h_score else None
    )
    trap = detect_blowout_trap(
        period=status,
        clock_min_remaining=clock_rem,
        home_score=h_score, away_score=a_score,
        decimal_odds_for_leader=leader_odds,
    )

    # Verdict
    lead = h_score - a_score
    if trap and trap.get("triggered"):
        verdict = {
            "label": "TRAP_LATE_LEAD",
            "side":  trap["leader_side"],
            "reason_es": trap["reason_es"],
            "reason_en": trap["reason_en"],
        }
    elif status in ("Q1",) and total_pts < 8:
        verdict = {
            "label": "INSUFFICIENT_DATA",
            "side":  None,
            "reason_es": "Muy pronto en el partido — esperar al menos al segundo cuarto.",
            "reason_en": "Too early — wait until at least the second quarter.",
        }
    elif abs(lead) >= 10 and status in ("Q3", "Q4") and projected_total >= 200:
        # Likely high-pace blowout — Over Total is the directional read.
        side = "home" if lead > 0 else "away"
        verdict = {
            "label": "LIVE_VALUE_PUSH",
            "side":  side,
            "reason_es": (
                f"Pace alto ({points_per_min:.1f} pts/min). Proyección total "
                f"~{projected_total:.0f} pts; revisar Over total."
            ),
            "reason_en": (
                f"High pace ({points_per_min:.1f} pts/min). Projected total "
                f"~{projected_total:.0f}; consider Over total."
            ),
        }
    elif abs(lead) <= 5:
        verdict = {
            "label": "BALANCED",
            "side":  None,
            "reason_es": "Partido parejo en el marcador; sin señal clara de dominio.",
            "reason_en": "Tight game; no clear dominance signal.",
        }
    else:
        side = "home" if lead > 0 else "away"
        verdict = {
            "label": "BALANCED",
            "side":  side,
            "reason_es": "Ventaja moderada; revisar línea live y rotaciones.",
            "reason_en": "Moderate edge; check live line and rotations.",
        }

    return {
        "minute": elapsed,
        "score":  {"home": h_score, "away": a_score},
        "home":   home_block,
        "away":   away_block,
        "deltas": {
            "lead":            lead,
            "projected_total": projected_total,
            "points_per_min":  points_per_min,
        },
        "leader_odds":  leader_odds,
        "favorite_side": ("home" if lead > 0 else "away" if lead < 0 else None),
        "trap":   trap,
        "verdict": verdict,
        "clock_min_remaining": clock_rem,
        "fraction_remaining":  round(frac_remaining, 3),
        "_source": "live_basketball_analytics_v1",
        "_sport":  "basketball",
    }


def _insufficient(match: dict, h_score: int, a_score: int, status: str) -> dict:
    """Fallback when we don't have enough data to model the game."""
    return {
        "minute": None,
        "score":  {"home": h_score, "away": a_score},
        "home":   {"points": h_score, "lead_pts": h_score - a_score,
                   "points_per_min": 0.0, "projected_total": 0.0,
                   "xg_live": 0.0, "threat_index": 0.0, "pressure_rate": 0.0,
                   "shots": 0, "shots_on_target": 0, "shots_in_box": 0,
                   "possession": 0, "corners": 0, "dangerous": 0, "attacks": 0},
        "away":   {"points": a_score, "lead_pts": a_score - h_score,
                   "points_per_min": 0.0, "projected_total": 0.0,
                   "xg_live": 0.0, "threat_index": 0.0, "pressure_rate": 0.0,
                   "shots": 0, "shots_on_target": 0, "shots_in_box": 0,
                   "possession": 0, "corners": 0, "dangerous": 0, "attacks": 0},
        "deltas": {"lead": h_score - a_score, "projected_total": 0.0, "points_per_min": 0.0},
        "leader_odds": None,
        "favorite_side": None,
        "trap": None,
        "verdict": {
            "label": "INSUFFICIENT_DATA",
            "side":  None,
            "reason_es": "Sin datos suficientes para emitir veredicto basket.",
            "reason_en": "Not enough basketball data to emit a verdict.",
        },
        "clock_min_remaining": None,
        "fraction_remaining":  0.5,
        "_source": "live_basketball_analytics_v1",
        "_sport":  "basketball",
    }


__all__ = [
    "compute_live_analysis",
    "detect_blowout_trap",
    "parse_basketball_minute",
]
