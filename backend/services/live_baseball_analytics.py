"""Baseball live analytics — counterpart to live_basketball_analytics.py.

Produces a parallel-shape compute_live_analysis() payload compatible with
LiveCopilotCard and LiveAnalysisStrip. Uses only data available from
API-Sports baseball free tier: innings, runs, hits, errors.

Key concepts that replace football's xG/pressure:
 - run_rate:          carreras por inning (pace ofensivo)
 - threat_score:      proxy de amenaza = hits + errors_opponent
 - blowout_trap:      ventaja >= 5 carreras en inning 8 o 9 con cuota baja
 - innings_remaining: equivalente a minutos restantes
"""
from __future__ import annotations
from typing import Optional

REG_INNINGS = 9


def _parse_inning(status_short: str | None) -> Optional[int]:
    """Parse API-Sports baseball status into inning number (1-9+).

    API-Sports baseball status codes: "IN1".."IN9", "IN" (live generic),
    "LIVE", "NS" (not started), "FT" (finished).
    """
    if not status_short:
        return None
    s = status_short.upper()
    if s.startswith("IN") and len(s) > 2:
        try:
            return int(s[2:])
        except ValueError:
            return None
    if s in ("IN", "LIVE"):
        return 5  # midpoint assumption when no inning number available
    return None


def _fraction_remaining(inning: Optional[int]) -> float:
    """Fraction of game remaining (0..1) given current inning."""
    if inning is None:
        return 0.5
    return max(0.0, min(1.0, (REG_INNINGS - inning) / REG_INNINGS))


def detect_blowout_trap(
    *,
    inning: Optional[int],
    home_score: int,
    away_score: int,
    decimal_odds_for_leader: Optional[float],
) -> Optional[dict]:
    """Baseball blowout trap: ventaja >= 5 carreras en inning 8+ con cuota <= 1.20.

    A 5-run lead in the 8th/9th is statistically a ~97% win probability —
    but if the closer is unavailable or a bullpen meltdown is in progress,
    the cuota baja del líder tiene cero valor real.

    Trigger conditions (ALL must hold):
      1. Inning >= 8.
      2. Absolute run lead >= 5.
      3. Decimal odds for leader <= 1.20 (already priced as locked).
    """
    if inning is None or inning < 8:
        return None
    lead = abs(home_score - away_score)
    if lead < 5:
        return None
    if decimal_odds_for_leader is None or decimal_odds_for_leader > 1.20:
        return None
    leader   = "home" if home_score > away_score else "away"
    trailing = "away" if leader == "home" else "home"
    return {
        "triggered": True,
        "leader_side": leader,
        "trailing_side": trailing,
        "lead": lead,
        "inning": inning,
        "decimal_odds_for_leader": decimal_odds_for_leader,
        "reason_es": (
            f"TRAMPA: Inning {inning} con ventaja {lead} carreras, "
            f"cuota {decimal_odds_for_leader:.2f}. "
            f"Posible meltdown del bullpen — no apostar al favorito."
        ),
        "reason_en": (
            f"TRAP: Inning {inning}, {lead}-run lead, "
            f"odds {decimal_odds_for_leader:.2f}. "
            f"Possible bullpen meltdown — do not bet the leader."
        ),
    }


def _best_winner_odds(match: dict) -> dict:
    """Extract best Moneyline odds for home/away from odds_snapshots."""
    snaps   = match.get("odds_snapshots") or []
    if not snaps:
        return {}
    markets = (snaps[-1] or {}).get("markets") or {}
    rows    = (
        markets.get("Moneyline") or
        markets.get("Home/Away") or
        markets.get("Match Winner") or []
    )
    out: dict[str, Optional[float]] = {"home": None, "away": None}
    for r in rows:
        for k in out:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 1.01:
                if out[k] is None or v > out[k]:
                    out[k] = float(v)
    return {k: v for k, v in out.items() if v is not None}


def compute_live_analysis(match: dict) -> dict:
    """Build baseball live-analysis payload (same shape as football/basketball).

    home/away blocks expose:
      runs            — carreras actuales
      hits            — hits (si API-Sports los provee en statistics)
      errors          — errores del rival (proxy de amenaza)
      run_rate        — carreras por inning (pace)
      threat_score    — hits + errors_opponent (proxy de xT)
      innings_played  — innings jugados

    The football-shape analog fields (xg_live, threat_index, pressure_rate,
    shots, etc.) are populated so LiveAnalysisStrip renders without crashing.
    """
    live     = match.get("live_stats") or {}
    status   = live.get("status") or ""
    score    = live.get("score") or {}
    h_score  = int(score.get("home") or 0)
    a_score  = int(score.get("away") or 0)

    inning         = _parse_inning(status)
    frac_remaining = _fraction_remaining(inning)
    innings_played = inning or 1

    # Stats from normalize_live_stats home_stats / away_stats buckets.
    # API-Sports baseball statistics keys: "Hits", "Errors" (best-effort).
    home_raw = live.get("home_stats") or {}
    away_raw = live.get("away_stats") or {}

    def _int(d: dict, key: str) -> int:
        try:
            return int(d.get(key) or 0)
        except (ValueError, TypeError):
            return 0

    h_hits   = _int(home_raw, "Hits")
    a_hits   = _int(away_raw, "Hits")
    h_errors = _int(home_raw, "Errors")
    a_errors = _int(away_raw, "Errors")

    h_run_rate        = round(h_score / max(1, innings_played), 3)
    a_run_rate        = round(a_score / max(1, innings_played), 3)
    total_runs        = h_score + a_score
    run_rate_combined = round(total_runs / max(1, innings_played), 3)

    # Projected final total (simple linear extrapolation over 9 innings).
    projected_total = round(run_rate_combined * REG_INNINGS, 1)

    # Threat score: own hits generated + errors forced on the opponent.
    h_threat = h_hits + a_errors
    a_threat = a_hits + h_errors

    # Blowout trap detection.
    winner_odds = _best_winner_odds(match)
    leader_key  = (
        "home" if h_score > a_score else
        "away" if a_score > h_score else None
    )
    leader_odds = winner_odds.get(leader_key) if leader_key else None
    trap = detect_blowout_trap(
        inning=inning,
        home_score=h_score,
        away_score=a_score,
        decimal_odds_for_leader=leader_odds,
    )

    # Verdict classification.
    lead = h_score - a_score
    if trap and trap.get("triggered"):
        verdict = {
            "label":     "TRAP_LATE_LEAD",
            "side":      trap["leader_side"],
            "reason_es": trap["reason_es"],
            "reason_en": trap["reason_en"],
        }
    elif inning is not None and inning <= 2:
        verdict = {
            "label":     "INSUFFICIENT_DATA",
            "side":      None,
            "reason_es": "Muy pronto — esperar al tercer inning para emitir veredicto.",
            "reason_en": "Too early — wait until the 3rd inning.",
        }
    elif abs(lead) >= 4 and inning is not None and inning >= 6:
        side = "home" if lead > 0 else "away"
        verdict = {
            "label": "LIVE_VALUE_PUSH",
            "side":  side,
            "reason_es": (
                f"Ventaja amplia ({abs(lead)} carreras, inning {inning}). "
                f"Proyección total ~{projected_total} carreras — "
                f"revisar Over/Under restante."
            ),
            "reason_en": (
                f"Large lead ({abs(lead)} runs, inning {inning}). "
                f"Projected total ~{projected_total} runs — "
                f"check remaining Over/Under."
            ),
        }
    elif abs(lead) <= 1:
        verdict = {
            "label":     "BALANCED",
            "side":      None,
            "reason_es": "Partido parejo — sin señal clara de dominio.",
            "reason_en": "Close game — no clear dominance signal.",
        }
    else:
        side = "home" if lead > 0 else "away"
        verdict = {
            "label": "BALANCED",
            "side":  side,
            "reason_es": (
                f"Ventaja moderada ({abs(lead)} carreras). "
                f"Revisar línea y bullpen disponible."
            ),
            "reason_en": (
                f"Moderate lead ({abs(lead)} runs). "
                f"Check live line and bullpen availability."
            ),
        }

    # ── Block shapes ────────────────────────────────────────────────────
    # Baseball-native fields + football-shape analogs for UI compatibility.
    home_block = {
        # Baseball-specific
        "runs":           h_score,
        "hits":           h_hits,
        "errors":         h_errors,
        "run_rate":       h_run_rate,
        "threat_score":   h_threat,
        "innings_played": innings_played,
        "projected_total": projected_total,
        # Football-shape analogs (LiveAnalysisStrip reads these)
        "xg_live":        round(h_run_rate, 2),
        "threat_index":   h_threat,
        "pressure_rate":  round(h_run_rate / max(1, innings_played), 2),
        "shots":          h_hits,
        "shots_on_target": h_hits,
        "shots_in_box":   0,
        "possession":     0,
        "corners":        0,
        "dangerous":      h_threat,
        "attacks":        h_hits,
    }
    away_block = {
        "runs":           a_score,
        "hits":           a_hits,
        "errors":         a_errors,
        "run_rate":       a_run_rate,
        "threat_score":   a_threat,
        "innings_played": innings_played,
        "projected_total": projected_total,
        "xg_live":        round(a_run_rate, 2),
        "threat_index":   a_threat,
        "pressure_rate":  round(a_run_rate / max(1, innings_played), 2),
        "shots":          a_hits,
        "shots_on_target": a_hits,
        "shots_in_box":   0,
        "possession":     0,
        "corners":        0,
        "dangerous":      a_threat,
        "attacks":        a_hits,
    }

    return {
        "minute":        inning,       # reutilizamos 'minute' = inning para UI
        "score":         {"home": h_score, "away": a_score},
        "home":          home_block,
        "away":          away_block,
        "deltas": {
            "lead":               lead,
            "projected_total":    projected_total,
            "run_rate_combined":  run_rate_combined,
        },
        "leader_odds":      leader_odds,
        "favorite_side":    leader_key,
        "trap":             trap,
        "verdict":          verdict,
        "fraction_remaining": round(frac_remaining, 3),
        "inning":           inning,
        "_source":          "live_baseball_analytics_v1",
        "_sport":           "baseball",
    }


def _insufficient(match: dict, h_score: int, a_score: int, status: str) -> dict:
    """Fallback when there is not enough data to model the game."""
    empty_block = {
        "runs": 0, "hits": 0, "errors": 0, "run_rate": 0.0,
        "threat_score": 0, "innings_played": 0, "projected_total": 0.0,
        "xg_live": 0.0, "threat_index": 0, "pressure_rate": 0.0,
        "shots": 0, "shots_on_target": 0, "shots_in_box": 0,
        "possession": 0, "corners": 0, "dangerous": 0, "attacks": 0,
    }
    return {
        "minute": None,
        "score":  {"home": h_score, "away": a_score},
        "home":   {**empty_block, "runs": h_score},
        "away":   {**empty_block, "runs": a_score},
        "deltas": {
            "lead": h_score - a_score,
            "projected_total": 0.0,
            "run_rate_combined": 0.0,
        },
        "leader_odds":       None,
        "favorite_side":     None,
        "trap":              None,
        "verdict": {
            "label":     "INSUFFICIENT_DATA",
            "side":      None,
            "reason_es": "Sin datos suficientes para béisbol.",
            "reason_en": "Not enough baseball data.",
        },
        "fraction_remaining": 0.5,
        "inning":            None,
        "_source":           "live_baseball_analytics_v1",
        "_sport":            "baseball",
    }


__all__ = ["compute_live_analysis", "detect_blowout_trap"]
