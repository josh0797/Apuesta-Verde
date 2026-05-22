"""Recent-form guard — turns the LLM's textual `form_last_5` (WDLLW) into
a structured signal so the engine never silently recommends a team that's on
a bad streak without an explicit justification.

This was added after the user reported that the engine recommended Rapid Wien
as the winner of a match where Rapid Wien arrived from a string of losses.
The LLM has the data in the payload but sometimes under-weights it.

Public API:
    analyze_team_form(form_str)            → structured dict
    form_red_flag(home_form, away_form,
                  pick_side, pick_market)  → dict|None

Form string format (consumed verbatim from API-Football enrichment):
    Each char is one of W (win) / D (draw) / L (loss).
    Chronological order: oldest first → newest last (right-most = most recent).
    Examples: "WWDLL", "LLLLL", "WDWDW".
"""
from __future__ import annotations

import re
from typing import Optional

_VALID_CHARS = re.compile(r"[WDL]")


def _clean(form_str: Optional[str]) -> str:
    if not form_str:
        return ""
    return "".join(c for c in form_str.upper() if c in ("W", "D", "L"))


def analyze_team_form(form_str: Optional[str]) -> dict:
    """Return a structured analysis of a recent-form string.

    Output keys:
      raw          str   the cleaned chronological form (e.g. "WLLLD")
      n            int   how many samples we actually have (0–5)
      wins         int
      draws        int
      losses       int
      win_rate     float 0..1 over the available sample (None if n=0)
      current_streak  {"kind": "win"|"loss"|"draw"|"mixed"|"none", "length": int}
          — streak from the MOST RECENT match backwards. "mixed" means the
            most recent result is not consistent (e.g. "WLW" → length 1).
      form_score   int   -100..+100 weighted score (recent results matter more)
      red_flag     bool  True iff form_score <= -40 OR current loss streak >= 3
      label_es     str   short Spanish human label
      label_en     str   short English human label
    """
    raw = _clean(form_str)
    n = len(raw)
    if n == 0:
        return {
            "raw": "", "n": 0,
            "wins": 0, "draws": 0, "losses": 0,
            "win_rate": None,
            "current_streak": {"kind": "none", "length": 0},
            "form_score": 0,
            "red_flag": False,
            "label_es": "sin datos",
            "label_en": "no data",
        }
    wins   = raw.count("W")
    draws  = raw.count("D")
    losses = raw.count("L")
    win_rate = wins / n

    # ── Weighted form score ────────────────────────────────────────────
    # Most recent result has weight 5, then 4/3/2/1 going back.
    # W = +1, D = 0, L = -1.  Normalized to [-100, +100].
    weights = list(range(n, 0, -1))  # [n, n-1, …, 1] for newest→oldest
    # Iterate newest first.
    newest_first = raw[::-1]
    score_num = 0.0
    score_den = 0.0
    for ch, w in zip(newest_first, weights):
        val = {"W": 1, "D": 0, "L": -1}[ch]
        score_num += val * w
        score_den += w
    form_score = round((score_num / score_den) * 100) if score_den else 0

    # ── Current streak (from newest result backwards) ──────────────────
    streak_kind_char = newest_first[0]
    streak_len = 1
    for ch in newest_first[1:]:
        if ch == streak_kind_char:
            streak_len += 1
        else:
            break
    kind_map = {"W": "win", "L": "loss", "D": "draw"}
    current_streak = {"kind": kind_map[streak_kind_char], "length": streak_len}

    # ── Red flag heuristic ─────────────────────────────────────────────
    red_flag = form_score <= -40 or (current_streak["kind"] == "loss" and current_streak["length"] >= 3)

    label_es = _label(form_score, current_streak, "es")
    label_en = _label(form_score, current_streak, "en")

    return {
        "raw": raw, "n": n,
        "wins": wins, "draws": draws, "losses": losses,
        "win_rate": round(win_rate, 3),
        "current_streak": current_streak,
        "form_score": form_score,
        "red_flag": red_flag,
        "label_es": label_es,
        "label_en": label_en,
    }


def _label(form_score: int, streak: dict, lang: str) -> str:
    if streak["kind"] == "loss" and streak["length"] >= 4:
        return "racha muy mala" if lang == "es" else "very bad streak"
    if streak["kind"] == "loss" and streak["length"] >= 3:
        return "racha negativa" if lang == "es" else "losing streak"
    if streak["kind"] == "win" and streak["length"] >= 4:
        return "racha muy buena" if lang == "es" else "hot streak"
    if streak["kind"] == "win" and streak["length"] >= 3:
        return "racha positiva" if lang == "es" else "winning streak"
    if form_score >= 40:
        return "buena forma" if lang == "es" else "good form"
    if form_score <= -40:
        return "forma mala" if lang == "es" else "poor form"
    return "forma irregular" if lang == "es" else "mixed form"


_HOME_TOKENS = re.compile(r"\b(home|local|1|h|casa)\b", re.IGNORECASE)
_AWAY_TOKENS = re.compile(r"\b(away|visitor|visitante|2|v|a|road)\b", re.IGNORECASE)
_DRAW_TOKENS = re.compile(r"\b(draw|empate|x|tie)\b", re.IGNORECASE)


def _selection_targets(selection: str, market: str, home_name: str = "", away_name: str = "") -> dict:
    """Detect which side(s) a pick endorses.

    Returns dict with keys home: bool, away: bool, draw: bool.
    For Double Chance picks both sides may be True (covered).
    For Over/Under / totals, NONE of the team sides applies.
    """
    s = (selection or "").strip()
    m = (market or "").lower()
    out = {"home": False, "away": False, "draw": False}
    if not s:
        return out
    # Totals/spreads do not endorse a winner side
    if any(k in m for k in ("under", "over", "total")):
        return out
    short = s.replace(" ", "").upper()
    if short in ("1X", "X1"): return {"home": True, "draw": True, "away": False}
    if short in ("X2", "2X"): return {"home": False, "draw": True, "away": True}
    if short == "12":         return {"home": True, "away": True, "draw": False}
    if short == "1":          return {"home": True, "away": False, "draw": False}
    if short == "2":          return {"home": False, "away": True, "draw": False}
    if short == "X":          return {"home": False, "away": False, "draw": True}

    # Tokenize with separators / or or o ,
    parts = re.split(r"\s*(?:\/|\s+or\s+|\s+o\s+|,)\s*", s, flags=re.IGNORECASE)
    for tok in parts:
        if _DRAW_TOKENS.search(tok):
            out["draw"] = True
        if _HOME_TOKENS.search(tok) or (home_name and home_name.lower() in tok.lower()):
            out["home"] = True
        if _AWAY_TOKENS.search(tok) or (away_name and away_name.lower() in tok.lower()):
            out["away"] = True
    return out


def form_red_flag(
    home_form: Optional[str],
    away_form: Optional[str],
    selection: str,
    market: str,
    home_name: str = "",
    away_name: str = "",
) -> Optional[dict]:
    """Return a red-flag report when a pick endorses a team on a bad streak.

    Returns None when nothing is suspicious. Otherwise a dict:
        {
          "side": "home"|"away",
          "team_name": str,
          "streak": {"kind": "loss", "length": n},
          "form_score": int,
          "raw_form": str,
          "severity": "warn"|"critical",
          "reason_es": str,
          "reason_en": str,
          "suggested_action": "penalize_confidence"|"reroute_to_market_discard",
        }

    Severity rules:
      • critical → endorsed side has loss streak ≥ 4 OR form_score ≤ -60
      • warn     → endorsed side has loss streak ≥ 3 OR form_score ≤ -40
    The endorsed side is detected via _selection_targets(). For Double Chance
    picks that cover the bad side BUT also cover draw/opposite, we still warn
    but mark severity "warn" (not critical) because the DC mitigates risk.
    """
    targets = _selection_targets(selection, market, home_name, away_name)
    if not (targets["home"] or targets["away"]):
        return None

    home_info = analyze_team_form(home_form)
    away_info = analyze_team_form(away_form)

    def _evaluate(side_key: str, team: str, info: dict) -> Optional[dict]:
        if not targets[side_key]:
            return None
        if not info["red_flag"]:
            return None
        streak = info["current_streak"]
        critical = (
            (streak["kind"] == "loss" and streak["length"] >= 4)
            or info["form_score"] <= -60
        )
        # Double-chance picks (covering an alternative outcome) are softened.
        is_dc = (targets["home"] + targets["away"] + targets["draw"]) >= 2
        severity = "critical" if (critical and not is_dc) else "warn"
        reason_es = (
            f"{team} llega con {info['label_es']} ("
            f"últimos {info['n']}: {info['raw']}, score {info['form_score']}). "
            f"La recomendación del lado {side_key} contradice la forma reciente."
        )
        reason_en = (
            f"{team} arrives in {info['label_en']} ("
            f"last {info['n']}: {info['raw']}, score {info['form_score']}). "
            f"Endorsing the {side_key} side contradicts the recent form signal."
        )
        return {
            "side": side_key,
            "team_name": team,
            "streak": streak,
            "form_score": info["form_score"],
            "raw_form": info["raw"],
            "severity": severity,
            "reason_es": reason_es,
            "reason_en": reason_en,
            "suggested_action": (
                "reroute_to_market_discard" if severity == "critical"
                else "penalize_confidence"
            ),
        }

    return (
        _evaluate("home", home_name or "Local", home_info)
        or _evaluate("away", away_name or "Visitante", away_info)
    )
