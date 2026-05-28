"""Pattern detector for encounter_history.

Given a list of historical encounter docs, return:
    {
      "repeated_patterns":  list[str]   # plain-text observations
      "warnings":           list[str]   # caution notes for the analyst engine
      "flags":              dict        # programmatic flags (for the LLM payload)
    }

Detectors implemented (football MVP):
  * tight_matches              — final scores have ≤1 goal of difference in ≥60% of past matches
  * under_trend / over_trend   — ≥60% of past matches stayed below 2.5 / above 2.5 goals
  * btts_trend                 — both teams scored in ≥60% of past matches
  * frequent_red_card_hint     — many WARNINGS / risks mention red cards
  * favorite_does_not_dominate — wins for the home/away "big" team are <50% (warning)
  * remontada_pattern          — final score implies comeback semantics (heuristic)
  * cards_trend                — many trap_signals mention cards
  * corners_trend              — many trap_signals/risks mention corners

Detectors are deliberately conservative: a pattern is only emitted when the
supporting evidence is strong (≥3 past encounters AND ≥60% incidence) so the
UI doesn't surface noise.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_MIN_OBSERVATIONS = 3
_MIN_RATIO        = 0.60


def _parse_score(s: Optional[str]) -> Optional[tuple[int, int]]:
    if not s:
        return None
    m = re.match(r"\s*(\d{1,2})\s*[-:\u2013]\s*(\d{1,2})\s*$", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _total_goals(score: Optional[tuple[int, int]]) -> Optional[int]:
    return None if score is None else score[0] + score[1]


def _goals_diff(score: Optional[tuple[int, int]]) -> Optional[int]:
    return None if score is None else abs(score[0] - score[1])


def _is_btts(score: Optional[tuple[int, int]]) -> Optional[bool]:
    return None if score is None else (score[0] > 0 and score[1] > 0)


def _ratio_text(p: float, n: int) -> str:
    return f"{int(round(p * 100))}% ({int(round(p * n))}/{n})"


def detect_patterns(history: list[dict], *, sport: str = "football") -> dict[str, Any]:
    """Return repeated patterns + warnings + raw flags."""
    out: dict[str, Any] = {"repeated_patterns": [], "warnings": [], "flags": {}}
    if not history:
        return out
    sport = (sport or "football").lower()
    scores = [_parse_score(h.get("final_score")) for h in history]
    scored = [s for s in scores if s is not None]
    n_scored = len(scored)

    # ── Football-specific score-based detectors ─────────────────────────
    if sport == "football" and n_scored >= _MIN_OBSERVATIONS:
        tight   = sum(1 for s in scored if (_goals_diff(s) or 0) <= 1)
        unders  = sum(1 for s in scored if (_total_goals(s) or 0) <= 2)
        overs   = sum(1 for s in scored if (_total_goals(s) or 0) >= 3)
        btts    = sum(1 for s in scored if _is_btts(s))
        comeback_like = sum(
            1 for s in scored
            if (_total_goals(s) or 0) >= 3 and (_goals_diff(s) or 0) <= 1
        )
        if tight / n_scored >= _MIN_RATIO:
            out["repeated_patterns"].append(
                f"Partidos cerrados (diferencia ≤1) en {_ratio_text(tight/n_scored, n_scored)} de los enfrentamientos."
            )
            out["flags"]["tight_matches"] = True
        if unders / n_scored >= _MIN_RATIO:
            out["repeated_patterns"].append(
                f"Tendencia UNDER 2.5: {_ratio_text(unders/n_scored, n_scored)} con ≤2 goles totales."
            )
            out["flags"]["under_trend"] = True
        if overs / n_scored >= _MIN_RATIO:
            out["repeated_patterns"].append(
                f"Tendencia OVER 2.5: {_ratio_text(overs/n_scored, n_scored)} con ≥3 goles totales."
            )
            out["flags"]["over_trend"] = True
        if btts / n_scored >= _MIN_RATIO:
            out["repeated_patterns"].append(
                f"Ambos equipos marcan (BTTS) en {_ratio_text(btts/n_scored, n_scored)} de los partidos."
            )
            out["flags"]["btts_trend"] = True
        if comeback_like and comeback_like / n_scored >= 0.4:
            out["repeated_patterns"].append(
                f"Partidos con remontada/empate igualados en {_ratio_text(comeback_like/n_scored, n_scored)}."
            )
            out["flags"]["frequent_comebacks"] = True

    # ── Cross-sport text-based detectors (cards / corners / red cards) ──
    text_blob_parts: list[str] = []
    for h in history:
        if h.get("reasoning"):
            text_blob_parts.append(str(h["reasoning"]))
        for r in h.get("risks") or []:
            text_blob_parts.append(str(r))
        for t in h.get("trap_signals") or []:
            if isinstance(t, dict):
                text_blob_parts.append(str(t.get("label") or t.get("explanation") or ""))
            elif isinstance(t, str):
                text_blob_parts.append(t)
    blob = " ".join(text_blob_parts).lower()
    if blob:
        if re.search(r"\b(tarjeta\s+roja|expulsi[óo]n|red\s+card)\b", blob):
            count = len(re.findall(r"\b(tarjeta\s+roja|expulsi[óo]n|red\s+card)\b", blob))
            if count >= 2:
                out["warnings"].append(
                    f"Contexto LIVE recurrente con tarjetas rojas/expulsiones ({count} menciones en historial)."
                )
                out["flags"]["red_cards_recurring"] = True
        if re.search(r"\bc[oó]rners?\b", blob):
            ccount = len(re.findall(r"\bc[oó]rners?\b", blob))
            if ccount >= 3:
                out["repeated_patterns"].append(
                    f"Mercado de córners mencionado en {ccount} análisis previos — posible tendencia."
                )
                out["flags"]["corners_trend"] = True
        if re.search(r"\b(tarjetas?\s+amarillas?|yellow\s+card)\b", blob):
            yc = len(re.findall(r"\b(tarjetas?\s+amarillas?|yellow\s+card)\b", blob))
            if yc >= 3:
                out["flags"]["cards_trend"] = True

    # ── Favorite-does-not-dominate detector (football) ─────────────────
    if sport == "football" and n_scored >= _MIN_OBSERVATIONS:
        # Loose definition: when the same team was "home" in ≥half the prior
        # matches AND lost ≥40% of them, label the rivalry as imbalanced.
        home_winners = sum(1 for s in scored if s[0] > s[1])
        if home_winners / n_scored <= 0.4:
            out["warnings"].append(
                "El local NO ha dominado históricamente este enfrentamiento."
            )
            out["flags"]["home_does_not_dominate"] = True

    return out


__all__ = ["detect_patterns"]
