"""
LiveMarketStateValidator (Bug fix — Reds 5-3 Over 7.5)
=======================================================

Today the engine happily shows "Más de 7.5 carreras" as the active live
recommendation for Reds 5 - 3 Braves even though the market is already
RESOLVED (5+3 = 8 > 7.5). Same family of bug applies to football
(Over 1.5 with 3-1 score) and basketball (Over 175.5 with 180 points).

This validator is the **single source of truth** for "is this market line
still playable given the current scoreboard?". Every live recommendation
must pass through it before being surfaced as actionable.

Output (canonical shape)::

    {
        "state":             "still_playable" | "already_resolved_win"
                           | "already_resolved_loss",
        "actionable":        True | False,
        "current_total":     8,
        "threshold":         7.5,
        "side":              "over" | "under" | "run_line" | "team_total"
                           | "moneyline" | "unknown",
        "team_target":       None | "home" | "away",
        "summary_es":        "Línea ya superada (8 > 7.5). Buscar línea live.",
        "summary_en":        "Line already passed (8 > 7.5). Look for live line.",
        "suggested_alternatives": ["Over 8.5", "Over 9.5", "Under 10.5"],
        "reason_code":       "OVER_ALREADY_HIT" | "UNDER_ALREADY_BROKEN"
                           | "RL_FAVORITE_COVERED" | "STILL_LIVE",
    }

This module is **sport-agnostic** at the API level — callers pass a
sport literal so the alternative-line generator picks the right ladder.
All math is deterministic and unit-testable (no I/O, no DB).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Sport-aware ladders we walk when the original line is already resolved.
# Order matters — generators iterate top-to-bottom.
_OVER_LADDERS = {
    "baseball":   [7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5],
    "football":   [0.5, 1.5, 2.5, 3.5, 4.5],
    "basketball": [175.5, 180.5, 185.5, 190.5, 195.5, 200.5, 205.5,
                   210.5, 215.5, 220.5, 225.5, 230.5, 235.5],
}
_UNDER_LADDERS = {k: list(reversed(v)) for k, v in _OVER_LADDERS.items()}


# ── Market parsing ──────────────────────────────────────────────────────────

_OVER_RE      = re.compile(r"(over|más de|mas de|m[áa]s de)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_UNDER_RE     = re.compile(r"(under|menos de)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_RL_RE        = re.compile(r"run\s*line\s*([+-]?\d+(?:\.\d+)?)?", re.IGNORECASE)
_HANDICAP_RE  = re.compile(r"(?:handicap|spread)\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_TEAM_TOTAL_RE = re.compile(r"team\s*total\s*(over|under|más de|menos de)?\s*(\d+(?:\.\d+)?)?(?:\s*\((home|away|local|visit\w*)\))?", re.IGNORECASE)


def _parse_market(market_label: Optional[str]) -> dict:
    """Extract `{side, threshold, team_target}` from a market label.

    Returns sensible defaults when nothing matches so callers don't need
    to guard.
    """
    if not market_label:
        return {"side": "unknown", "threshold": None, "team_target": None}
    s = str(market_label).strip()

    m_tt = _TEAM_TOTAL_RE.search(s)
    if m_tt:
        side_word = (m_tt.group(1) or "").lower()
        side = "over" if side_word in ("over", "más de", "mas de") else (
               "under" if side_word in ("under", "menos de") else "team_total")
        try:
            thr = float(m_tt.group(2)) if m_tt.group(2) else None
        except (TypeError, ValueError):
            thr = None
        team_target = None
        team_word = (m_tt.group(3) or "").lower()
        if team_word in ("home", "local"):
            team_target = "home"
        elif team_word in ("away", "visit", "visitante", "visitor"):
            team_target = "away"
        return {"side": "team_total", "threshold": thr, "team_target": team_target, "tt_side": side}

    m_over = _OVER_RE.search(s)
    if m_over:
        try:
            return {"side": "over", "threshold": float(m_over.group(2)), "team_target": None}
        except (TypeError, ValueError):
            pass

    m_under = _UNDER_RE.search(s)
    if m_under:
        try:
            return {"side": "under", "threshold": float(m_under.group(2)), "team_target": None}
        except (TypeError, ValueError):
            pass

    m_rl = _RL_RE.search(s)
    if m_rl:
        try:
            thr = float(m_rl.group(1)) if m_rl.group(1) else 1.5
        except (TypeError, ValueError):
            thr = 1.5
        team_target = None
        sl = s.lower()
        if "underdog" in sl or "+" in (m_rl.group(1) or ""):
            team_target = "underdog"
        elif "favorite" in sl or "favorito" in sl or "-" in (m_rl.group(1) or ""):
            team_target = "favorite"
        return {"side": "run_line", "threshold": thr, "team_target": team_target}

    m_hc = _HANDICAP_RE.search(s)
    if m_hc:
        try:
            return {"side": "handicap", "threshold": float(m_hc.group(1)), "team_target": None}
        except (TypeError, ValueError):
            pass

    sl = s.lower()
    if "moneyline" in sl or "1x2" in sl or "ml " in sl:
        return {"side": "moneyline", "threshold": None, "team_target": None}
    if "nrfi yes" in sl:
        return {"side": "nrfi_yes", "threshold": 0.5, "team_target": None}
    if "nrfi no" in sl or "yrfi" in sl:
        return {"side": "nrfi_no", "threshold": 0.5, "team_target": None}

    return {"side": "unknown", "threshold": None, "team_target": None}


# ── Public API ──────────────────────────────────────────────────────────────

def validate_market_state(
    market_label: Optional[str],
    *,
    home_score: Optional[int],
    away_score: Optional[int],
    sport: str = "baseball",
    inning_or_minute: Optional[Any] = None,
    is_final: bool = False,
    selection_label: Optional[str] = None,
) -> dict:
    """Validate whether `market_label` is still playable given the live score.

    Pure / deterministic / no I/O — easy to unit-test.

    Parameters
    ----------
    market_label : the recommendation's `market` (or `selection`) string.
    selection_label : optional secondary label. Some picks have
        `market="Run Line +1.5 (underdog)"` AND
        `selection="Más de 7.5 carreras"`. In that case the SELECTION is
        what the user is actually betting and must also be validated.
        We pick the worst-of-two states so a settled-over isn't masked
        by a still-playable run-line.
    home_score / away_score : current live scoreboard.
    sport : "baseball" | "football" | "basketball" — controls suggested
        alternative ladders.
    inning_or_minute : optional, only used for the human summary so the
        message reads naturally ("ya en el 7th" / "minuto 60").
    is_final : if True, the game already ended → moneyline / RL are
        evaluated against the FINAL scoreboard.

    Returns the canonical state payload (see module docstring).
    """
    # Validate both labels independently and pick the worst state so the
    # caller always sees a "settled" verdict when ANY part of the pick
    # is resolved.
    primary = _validate_one(market_label, home_score=home_score, away_score=away_score,
                            sport=sport, inning_or_minute=inning_or_minute, is_final=is_final)
    if selection_label and selection_label != market_label:
        secondary = _validate_one(selection_label, home_score=home_score, away_score=away_score,
                                  sport=sport, inning_or_minute=inning_or_minute, is_final=is_final)
        # Resolution priority: already_resolved_loss > already_resolved_win > unknown > still_playable.
        priority = {
            "already_resolved_loss":    0,
            "already_resolved_win":     1,
            "already_resolved_unknown": 2,
            "still_playable":           3,
        }
        if priority.get(secondary.get("state"), 9) < priority.get(primary.get("state"), 9):
            return secondary
    return primary


def _validate_one(
    market_label: Optional[str],
    *,
    home_score: Optional[int],
    away_score: Optional[int],
    sport: str = "baseball",
    inning_or_minute: Optional[Any] = None,
    is_final: bool = False,
) -> dict:
    """Validate a single label. Internal helper for `validate_market_state`."""
    parsed = _parse_market(market_label)
    side = parsed["side"]
    threshold = parsed["threshold"]
    team_target = parsed["team_target"]

    # Coerce scores defensively — None / strings happen.
    try:
        hs = int(home_score) if home_score is not None else None
    except (TypeError, ValueError):
        hs = None
    try:
        as_ = int(away_score) if away_score is not None else None
    except (TypeError, ValueError):
        as_ = None
    have_scores = hs is not None and as_ is not None
    total = (hs + as_) if have_scores else None

    out: dict = {
        "state":            "still_playable",
        "actionable":       True,
        "current_total":    total,
        "threshold":        threshold,
        "side":             side,
        "team_target":      team_target,
        "summary_es":       "Mercado aún jugable.",
        "summary_en":       "Market still playable.",
        "suggested_alternatives": [],
        "reason_code":      "STILL_LIVE",
    }

    # If we have no scoreboard yet → assume still playable (pre-game).
    if not have_scores:
        return out

    # ── OVER ─────────────────────────────────────────────────────────────
    if side == "over" and threshold is not None:
        unit = _unit(sport)
        if total > threshold:
            out.update({
                "state":        "already_resolved_win",
                "actionable":   False,
                "summary_es":   f"Línea Over {threshold} ya superada con {total} {unit}.",
                "summary_en":   f"Over {threshold} already passed with {total} {unit}.",
                "reason_code":  "OVER_ALREADY_HIT",
                "suggested_alternatives": _alt_lines(sport, total, side="over"),
            })
        elif is_final and total <= threshold:
            out.update({
                "state":        "already_resolved_loss",
                "actionable":   False,
                "summary_es":   f"Línea Over {threshold} no alcanzada (terminó {total}).",
                "summary_en":   f"Over {threshold} missed (final {total}).",
                "reason_code":  "OVER_FINAL_MISS",
            })
        return out

    # ── UNDER ────────────────────────────────────────────────────────────
    if side == "under" and threshold is not None:
        unit = _unit(sport)
        if total > threshold:
            # Once the total surpasses the line the Under is dead.
            out.update({
                "state":        "already_resolved_loss",
                "actionable":   False,
                "summary_es":   f"Línea Under {threshold} ya rota con {total} {unit}.",
                "summary_en":   f"Under {threshold} already broken with {total} {unit}.",
                "reason_code":  "UNDER_ALREADY_BROKEN",
                "suggested_alternatives": _alt_lines(sport, total, side="under"),
            })
        elif is_final and total <= threshold:
            out.update({
                "state":        "already_resolved_win",
                "actionable":   False,
                "summary_es":   f"Línea Under {threshold} ganada (terminó {total}).",
                "summary_en":   f"Under {threshold} cashed (final {total}).",
                "reason_code":  "UNDER_FINAL_WIN",
            })
        return out

    # ── TEAM TOTAL ───────────────────────────────────────────────────────
    if side == "team_total" and threshold is not None:
        team_score = hs if team_target == "home" else (as_ if team_target == "away" else None)
        if team_score is not None:
            tt_side = parsed.get("tt_side")
            if tt_side == "over" and team_score > threshold:
                out.update({
                    "state":        "already_resolved_win",
                    "actionable":   False,
                    "current_total": team_score,
                    "summary_es":   f"Team Total Over {threshold} ya superado por el equipo ({team_score}).",
                    "summary_en":   f"Team Total Over {threshold} already covered ({team_score}).",
                    "reason_code":  "TT_OVER_ALREADY_HIT",
                })
            elif tt_side == "under" and team_score > threshold:
                out.update({
                    "state":        "already_resolved_loss",
                    "actionable":   False,
                    "current_total": team_score,
                    "summary_es":   f"Team Total Under {threshold} ya rota por el equipo ({team_score}).",
                    "summary_en":   f"Team Total Under {threshold} already broken ({team_score}).",
                    "reason_code":  "TT_UNDER_ALREADY_BROKEN",
                })
        return out

    # ── RUN LINE / SPREAD ────────────────────────────────────────────────
    # Pregame RL +1.5 (underdog) is still playable as long as the underdog
    # is within +1.5 of the favorite. We can't reliably tell who's the
    # favorite without odds, so we only flag obvious final cases.
    if side in ("run_line", "handicap") and is_final and threshold is not None:
        # Flag completed RL as informational only — accuracy comes from
        # the comparison layer that knows which side was the favorite.
        out.update({
            "summary_es": f"Mercado de spread finalizado — diferencia {abs(hs - as_)}.",
            "summary_en": f"Spread market settled — final delta {abs(hs - as_)}.",
        })
        return out

    # ── NRFI (no run first inning) ───────────────────────────────────────
    # Once we're past the 1st inning OR the 1st-inning scoreboard differs
    # from 0-0, the market resolves. We can't tell the partial 1st-inning
    # score from total alone, so we only invalidate post-1st.
    if side == "nrfi_yes" and inning_or_minute and _inning_num(inning_or_minute) >= 2:
        # Without the 1st-inning split we can't say win/loss with
        # certainty — flag as "settled, not actionable".
        out.update({
            "state":      "already_resolved_unknown" if total > 0 else "already_resolved_win",
            "actionable": False,
            "summary_es": "NRFI 1ra entrada ya jugada.",
            "summary_en": "NRFI: 1st inning already played.",
            "reason_code": "NRFI_SETTLED",
        })
    return out


def _unit(sport: str) -> str:
    return {"baseball": "carreras", "football": "goles", "basketball": "puntos"}.get(sport, "puntos")


def _inning_num(value: Any) -> int:
    """Best-effort inning/minute number extraction."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    # Try regex like "7TH ▲", "Top 5"
    m = re.search(r"(\d+)", str(value))
    return int(m.group(1)) if m else 0


def _alt_lines(sport: str, current_total: int, *, side: str) -> list:
    """Suggest next viable lines above the current total.

    For Over: lines strictly greater than `current_total`.
    For Under: lines safely above `current_total + 2` (cushion).
    Returns a list of human-readable strings, capped at 4.
    """
    if side == "over":
        ladder = _OVER_LADDERS.get(sport, [])
        out = [f"Over {x}" for x in ladder if x > current_total][:4]
        # Always offer a paired under so the user has both sides.
        next_under = next((x for x in _OVER_LADDERS.get(sport, []) if x > current_total + 2), None)
        if next_under:
            out.append(f"Under {next_under + 1}")
        return out[:4]
    if side == "under":
        ladder = _UNDER_LADDERS.get(sport, [])
        return [f"Under {x}" for x in ladder if x > current_total + 2][:4]
    return []


__all__ = ["validate_market_state", "_parse_market"]
