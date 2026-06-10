"""
services.pick_divergence_analysis
=================================

Pure-Python module that compares the **engine's recommendation** with the
**user's actual bet**, and computes ALL four answers:

    1.  engine_result   — would the engine have won/lost/pushed?
    2.  user_result     — what is the user's real outcome?
    3.  delta           — qualitative tag (USER_PROTECTED_LINE / USER_AGGRESSIVE_LINE / NONE)
    4.  line_difference — numeric line gap (in carreras / goles)

Critical design rules
---------------------
*   Never raise. Any malformed input returns ``{"available": False, ...}``.
*   Never overwrite the engine's pick. The engine_pick is the source of
    truth for engine accuracy; user_pick is a SEPARATE field.
*   Auto-settles the engine pick using the OFFICIAL final score so we
    can measure pure engine accuracy even when the user diverged.

Supported markets
-----------------
MLB:
*   ``total_runs``         — UNDER/OVER 8.5 .. 11.5  (also 9, 10 push lines)
*   ``f5_total_runs``      — UNDER/OVER 4.5 / 5  (first-5 inning total)
*   ``run_line``           — RL ±1.5 (and ±2.5)
*   ``moneyline``          — ML home/away

Football:
*   ``total_goals``        — Over/Under 0.5 .. 4.5  (also 3 push lines)
*   ``btts``               — Both teams to score: YES/NO
*   ``double_chance``      — 1X / 12 / X2
*   ``moneyline_1x2``      — Home / Draw / Away
*   ``handicap``           — Asian/Euro ±0.5 .. ±2.5

The module is intentionally I/O-free; the caller persists results.
"""
from __future__ import annotations

import re
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
RESULT_WIN     = "WIN"
RESULT_LOSS    = "LOSS"
RESULT_PUSH    = "PUSH"
RESULT_PENDING = "PENDING"
RESULT_VOID    = "VOID"

DELTA_NONE                   = "NONE"
DELTA_USER_PROTECTED_LINE    = "USER_PROTECTED_LINE"     # user bought more cushion
DELTA_USER_AGGRESSIVE_LINE   = "USER_AGGRESSIVE_LINE"    # user shortened the line
DELTA_DIFFERENT_MARKET       = "DIFFERENT_MARKET"        # user picked a different market entirely
DELTA_OPPOSITE_SIDE          = "OPPOSITE_SIDE"           # under vs over, home vs away

LINE_DIR_MORE_PROTECTED = "MORE_PROTECTED"
LINE_DIR_LESS_PROTECTED = "LESS_PROTECTED"
LINE_DIR_SAME           = "SAME"

# Side tokens we recognise.
_SIDE_UNDER = "UNDER"
_SIDE_OVER  = "OVER"
_SIDE_HOME  = "HOME"
_SIDE_AWAY  = "AWAY"
_SIDE_DRAW  = "DRAW"
_SIDE_YES   = "YES"
_SIDE_NO    = "NO"


# ──────────────────────────────────────────────────────────────────────────────
# Public API — pick parsing
# ──────────────────────────────────────────────────────────────────────────────
def parse_pick(
    *,
    market: Optional[str] = None,
    selection: Optional[str] = None,
    line: Optional[float] = None,
    raw: Optional[str] = None,
) -> dict:
    """Normalise a pick into a structured shape.

    Accepts ANY combination of (market, selection, line) or a single
    ``raw`` string like ``"UNDER 9.5"`` / ``"F5 Under 5"`` / ``"ML Phillies"``.

    Returns
    -------
    dict with keys::

        {
            "available":     bool,
            "market_type":   str | None,   # total_runs | f5_total_runs | run_line | moneyline | total_goals | btts | ...
            "side":          str | None,   # UNDER / OVER / HOME / AWAY / DRAW / YES / NO
            "line":          float | None, # 9.5 / -1.5 / 2.5 ...
            "team":          str | None,   # for ML/RL on football/MLB
            "raw":           str,
        }
    """
    src = (raw or f"{selection or ''} {market or ''}").strip()
    if not src and line is None:
        return {"available": False, "reason": "empty_pick", "raw": ""}

    canonical = src.upper().replace("MENOS DE", "UNDER").replace("MÁS DE", "OVER").replace("MAS DE", "OVER")
    canonical = canonical.replace("PRIMEROS 5", "F5").replace("PRIMERA MITAD", "F5")
    # Normalise common underscores so token boundaries (\b) match.
    canonical = canonical.replace("_", " ").replace("/", " ")
    # Tag football 1x2 from the market hint up-front so later branches see it.
    if (market or "").strip().lower().startswith("1x2"):
        canonical = canonical + " 1X2_TAG"

    out: dict = {
        "available":   True,
        "market_type": None,
        "side":        None,
        "line":        None,
        "team":        None,
        "raw":         src,
    }

    # ── First-5 (MLB) detector ──────────────────────────────────────
    is_f5 = bool(re.search(r"\b(F5|FIRST\s*5|1ST\s*5|HALF\s*1)\b", canonical))

    # ── Side detector ───────────────────────────────────────────────
    if re.search(r"\bUNDER\b|\bMENOS\b", canonical):
        out["side"] = _SIDE_UNDER
    elif re.search(r"\bOVER\b|\bMÁS\b|\bMAS\b", canonical):
        out["side"] = _SIDE_OVER
    elif re.search(r"\bDRAW\b|EMPATE", canonical):
        out["side"] = _SIDE_DRAW
    elif re.search(r"\bBTTS\s*(?:YES|SI|SÍ)\b|AMBOS\s*MARCAN\s*(?:SI|SÍ|YES)?", canonical):
        out["side"] = _SIDE_YES
        out["market_type"] = "btts"
    elif re.search(r"\bBTTS\s*NO\b|AMBOS\s*MARCAN\s*NO", canonical):
        out["side"] = _SIDE_NO
        out["market_type"] = "btts"

    # ── Line detector ───────────────────────────────────────────────
    # Pick the FIRST decimal (handles "F5 UNDER 4.5" and "-1.5", "+2.5").
    # IMPORTANT: when the pick contains F5 / 1ST 5 / HALF 1 we must NOT
    # capture the marker digit ("5") as the line — strip those tokens
    # before scanning for a number.
    canonical_for_line = canonical
    if is_f5:
        canonical_for_line = re.sub(
            r"\bF5\b|FIRST\s*5|1ST\s*5|HALF\s*1", "", canonical_for_line,
        )
    # Prefer an explicit decimal (X.Y) over a bare integer.
    line_match = re.search(r"[-+]?\d+[.,]\d+", canonical_for_line)
    if not line_match:
        line_match = re.search(r"[-+]?\d+", canonical_for_line)
    if line_match:
        try:
            out["line"] = float(line_match.group(0).replace(",", "."))
        except ValueError:
            pass
    if line is not None and out["line"] is None:
        try:
            out["line"] = float(line)
        except (TypeError, ValueError):
            pass

    # ── Market type ─────────────────────────────────────────────────
    mkt_lower = (market or "").lower()

    if is_f5:
        out["market_type"] = "f5_total_runs"
    elif re.search(r"\bRUN\s*LINE\b|\bRL\b", canonical) or "run_line" in mkt_lower or "runline" in mkt_lower:
        out["market_type"] = "run_line"
        # RL is implicitly ±1.5 unless line says otherwise.
        if out["line"] is None:
            out["line"] = 1.5
        # The HOME/AWAY side is captured below; if neither caller-provided
        # selection nor canonical helps, leave team/side as None.
        if out["side"] is None:
            sel_upper = (selection or "").upper()
            if "HOME" in sel_upper or "LOCAL" in sel_upper:
                out["side"] = _SIDE_HOME
            elif "AWAY" in sel_upper or "VISITANTE" in sel_upper:
                out["side"] = _SIDE_AWAY
    elif "1X2" in canonical or (out["side"] == _SIDE_DRAW):
        out["market_type"] = "moneyline_1x2"
        # Resolve HOME/AWAY from selection when raw text is sparse.
        if out["side"] is None:
            sel_upper = (selection or "").upper()
            if "HOME" in sel_upper or "LOCAL" in sel_upper:
                out["side"] = _SIDE_HOME
            elif "AWAY" in sel_upper or "VISITANTE" in sel_upper:
                out["side"] = _SIDE_AWAY
    elif re.search(r"\bML\b|MONEYLINE|MONEY\s*LINE", canonical) or "moneyline" in mkt_lower:
        # Football vs MLB: detect by presence of DRAW or by hint.
        if mkt_lower.startswith("1x2") or out["side"] == _SIDE_DRAW:
            out["market_type"] = "moneyline_1x2"
        else:
            out["market_type"] = "moneyline"
        # Extract team name (everything that isn't UNDER/OVER/ML/DRAW/etc).
        team = re.sub(
            r"\b(ML|MONEYLINE|MONEY\s*LINE|UNDER|OVER|F5|FIRST\s*5|DRAW|HOME|AWAY|LOCAL|VISITANTE)\b",
            "", canonical,
        ).strip(" .-+0123456789")
        if team:
            out["team"] = team
    elif out["market_type"] == "btts":
        pass
    elif re.search(r"DOUBLE\s*CHANCE|DC\b|DOBLE\s*OPORTUNIDAD", canonical) or "double_chance" in mkt_lower:
        out["market_type"] = "double_chance"
        # The "side" is the kept combination — extract from canonical.
        for tag in ("1X", "12", "X2"):
            if tag in canonical:
                out["side"] = tag
                break
    elif re.search(r"HANDICAP|SPREAD\b|ASIAN", canonical) or "handicap" in mkt_lower:
        out["market_type"] = "handicap"
    elif out["side"] in (_SIDE_UNDER, _SIDE_OVER):
        # Plain totals — disambiguate MLB vs football by the line (MLB
        # totals live in 6–13 range; football totals 0.5–5.5).
        if out["line"] is not None and out["line"] <= 5.5:
            out["market_type"] = "total_goals"
        else:
            out["market_type"] = "total_runs"

    # Sanity: if we still have nothing useful, fail-soft.
    if out["market_type"] is None and out["side"] is None:
        return {"available": False, "reason": "unrecognised", "raw": src}

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Public API — settle a pick against a final score
# ──────────────────────────────────────────────────────────────────────────────
def settle_pick_against_score(
    *,
    pick: dict,
    final_home: Optional[float] = None,
    final_away: Optional[float] = None,
    f5_home: Optional[float] = None,
    f5_away: Optional[float] = None,
    btts_home_scored: Optional[bool] = None,
    btts_away_scored: Optional[bool] = None,
) -> dict:
    """Compute WIN/LOSS/PUSH for a parsed pick against the official final
    score. Never raises.

    Required final_score fields depend on market_type. The caller passes
    only what makes sense (e.g. F5 totals need ``f5_home/f5_away``).
    """
    if not isinstance(pick, dict) or not pick.get("available"):
        return {"result": RESULT_PENDING, "reason": "pick_unparseable"}

    mkt  = pick.get("market_type")
    side = pick.get("side")
    line = pick.get("line")

    def _safe_sum(a, b) -> Optional[float]:
        try:
            return float(a) + float(b)
        except (TypeError, ValueError):
            return None

    # ── Totals (runs/goals) ─────────────────────────────────────────
    if mkt in ("total_runs", "total_goals") and side in (_SIDE_UNDER, _SIDE_OVER) and line is not None:
        total = _safe_sum(final_home, final_away)
        if total is None:
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "bad_line"}
        if abs(total - line_f) < 1e-9:
            return {"result": RESULT_PUSH, "total": total, "line": line_f}
        if side == _SIDE_UNDER:
            return {
                "result": RESULT_WIN if total < line_f else RESULT_LOSS,
                "total":  total, "line": line_f,
            }
        # OVER
        return {
            "result": RESULT_WIN if total > line_f else RESULT_LOSS,
            "total":  total, "line": line_f,
        }

    # ── F5 Totals ───────────────────────────────────────────────────
    if mkt == "f5_total_runs" and side in (_SIDE_UNDER, _SIDE_OVER) and line is not None:
        total = _safe_sum(f5_home, f5_away)
        if total is None:
            return {"result": RESULT_PENDING, "reason": "missing_f5_score"}
        try:
            line_f = float(line)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "bad_line"}
        if abs(total - line_f) < 1e-9:
            return {"result": RESULT_PUSH, "total": total, "line": line_f}
        if side == _SIDE_UNDER:
            return {
                "result": RESULT_WIN if total < line_f else RESULT_LOSS,
                "total":  total, "line": line_f,
            }
        return {
            "result": RESULT_WIN if total > line_f else RESULT_LOSS,
            "total":  total, "line": line_f,
        }

    # ── BTTS ────────────────────────────────────────────────────────
    if mkt == "btts" and side in (_SIDE_YES, _SIDE_NO):
        if btts_home_scored is None:
            try:
                btts_home_scored = float(final_home) > 0
            except (TypeError, ValueError):
                pass
        if btts_away_scored is None:
            try:
                btts_away_scored = float(final_away) > 0
            except (TypeError, ValueError):
                pass
        if btts_home_scored is None or btts_away_scored is None:
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        both_scored = bool(btts_home_scored) and bool(btts_away_scored)
        if side == _SIDE_YES:
            return {"result": RESULT_WIN if both_scored else RESULT_LOSS}
        return {"result": RESULT_LOSS if both_scored else RESULT_WIN}

    # ── Moneyline (binary — MLB or football no-draw) ────────────────
    if mkt == "moneyline":
        try:
            h = float(final_home); a = float(final_away)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        if h == a:
            return {"result": RESULT_PUSH, "reason": "tie_in_binary_ml"}
        winner_home = h > a
        if side == _SIDE_HOME or (pick.get("team") and "HOME" in (pick.get("team") or "").upper()):
            return {"result": RESULT_WIN if winner_home else RESULT_LOSS}
        if side == _SIDE_AWAY or (pick.get("team") and "AWAY" in (pick.get("team") or "").upper()):
            return {"result": RESULT_LOSS if winner_home else RESULT_WIN}
        # Without a clear side we can't settle.
        return {"result": RESULT_PENDING, "reason": "ambiguous_team"}

    # ── 1X2 (football moneyline) ────────────────────────────────────
    if mkt == "moneyline_1x2":
        try:
            h = float(final_home); a = float(final_away)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        if side == _SIDE_DRAW:
            return {"result": RESULT_WIN if h == a else RESULT_LOSS}
        if side == _SIDE_HOME:
            return {"result": RESULT_WIN if h > a else RESULT_LOSS}
        if side == _SIDE_AWAY:
            return {"result": RESULT_WIN if a > h else RESULT_LOSS}
        return {"result": RESULT_PENDING, "reason": "ambiguous_side"}

    # ── Double Chance ───────────────────────────────────────────────
    if mkt == "double_chance" and side in ("1X", "12", "X2"):
        try:
            h = float(final_home); a = float(final_away)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        winner = "1" if h > a else ("2" if a > h else "X")
        kept = side  # "1X" includes outcomes 1 or X
        return {"result": RESULT_WIN if winner in kept else RESULT_LOSS}

    # ── Run Line (MLB ±1.5) ─────────────────────────────────────────
    if mkt == "run_line" and line is not None:
        try:
            h = float(final_home); a = float(final_away); ln = float(line)
        except (TypeError, ValueError):
            return {"result": RESULT_PENDING, "reason": "missing_final_score"}
        # Side semantics: side==HOME → bet HOME, line is the spread on HOME
        # (negative=lay points). We rely on pick.team or pick.side to pick the side.
        if side == _SIDE_HOME or "HOME" in (pick.get("team") or "").upper():
            margin = h - a
        elif side == _SIDE_AWAY or "AWAY" in (pick.get("team") or "").upper():
            margin = a - h
        else:
            return {"result": RESULT_PENDING, "reason": "ambiguous_team"}
        # +1.5 line wins if margin > -1.5 (i.e. margin + 1.5 > 0).
        net = margin + ln
        if abs(net) < 1e-9:
            return {"result": RESULT_PUSH, "margin": margin, "line": ln}
        return {"result": RESULT_WIN if net > 0 else RESULT_LOSS,
                "margin": margin, "line": ln}

    # Fallback — unknown market.
    return {"result": RESULT_PENDING, "reason": "unsupported_market"}


# ──────────────────────────────────────────────────────────────────────────────
# Public API — divergence analysis
# ──────────────────────────────────────────────────────────────────────────────
def compute_divergence(
    *,
    engine_pick: dict,
    user_pick: Optional[dict],
) -> dict:
    """Compare engine_pick vs user_pick and return the qualitative delta
    + numeric line gap.

    Returns
    -------
    dict::
        {
            "followed_engine":  bool,
            "delta":            DELTA_*,
            "line_difference":  float | None,    # magnitude in points/runs
            "line_direction":   LINE_DIR_*,
            "pick_variation":   float,           # alias for line_difference (signed)
        }
    """
    # If no user pick, assume user followed engine.
    if not user_pick or not user_pick.get("available"):
        return {
            "followed_engine": True,
            "delta":           DELTA_NONE,
            "line_difference": 0.0,
            "line_direction":  LINE_DIR_SAME,
            "pick_variation":  0.0,
        }

    eng_mkt  = engine_pick.get("market_type")
    eng_side = engine_pick.get("side")
    eng_line = engine_pick.get("line")

    usr_mkt  = user_pick.get("market_type")
    usr_side = user_pick.get("side")
    usr_line = user_pick.get("line")

    # Same market, same side, same line → followed.
    same_market = (eng_mkt == usr_mkt)
    same_side   = (eng_side == usr_side)
    same_line   = (
        (eng_line is None and usr_line is None)
        or (eng_line is not None and usr_line is not None
            and abs(float(eng_line) - float(usr_line)) < 1e-9)
    )

    if same_market and same_side and same_line:
        return {
            "followed_engine": True,
            "delta":           DELTA_NONE,
            "line_difference": 0.0,
            "line_direction":  LINE_DIR_SAME,
            "pick_variation":  0.0,
        }

    # Different market entirely.
    if not same_market:
        return {
            "followed_engine": False,
            "delta":           DELTA_DIFFERENT_MARKET,
            "line_difference": None,
            "line_direction":  None,
            "pick_variation":  None,
        }

    # Opposite side on the same market.
    if not same_side:
        return {
            "followed_engine": False,
            "delta":           DELTA_OPPOSITE_SIDE,
            "line_difference": (
                abs(float(eng_line) - float(usr_line))
                if eng_line is not None and usr_line is not None
                else None
            ),
            "line_direction":  None,
            "pick_variation":  None,
        }

    # Same market & side, different line — protection vs aggression.
    try:
        diff = float(usr_line) - float(eng_line)
    except (TypeError, ValueError):
        diff = None

    if diff is None:
        return {
            "followed_engine": False,
            "delta":           DELTA_NONE,
            "line_difference": None,
            "line_direction":  None,
            "pick_variation":  None,
        }

    if eng_side == _SIDE_UNDER:
        # UNDER 9.5 → UNDER 10.0 = more protected (cushion grew)
        protected = diff > 0
    elif eng_side == _SIDE_OVER:
        # OVER 9.5 → OVER 9.0 = more protected (line dropped)
        protected = diff < 0
    else:
        protected = False  # non-totals — ignore direction semantics

    return {
        "followed_engine": False,
        "delta": (DELTA_USER_PROTECTED_LINE if protected
                  else DELTA_USER_AGGRESSIVE_LINE),
        "line_difference": round(abs(diff), 3),
        "line_direction":  LINE_DIR_MORE_PROTECTED if protected else LINE_DIR_LESS_PROTECTED,
        "pick_variation":  round(diff, 3),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Public API — one-shot evaluation
# ──────────────────────────────────────────────────────────────────────────────
def evaluate_engine_vs_user(
    *,
    engine_market: Optional[str]    = None,
    engine_selection: Optional[str] = None,
    engine_line: Optional[float]    = None,
    user_market: Optional[str]      = None,
    user_selection: Optional[str]   = None,
    user_line: Optional[float]      = None,
    final_home: Optional[float]     = None,
    final_away: Optional[float]     = None,
    f5_home: Optional[float]        = None,
    f5_away: Optional[float]        = None,
) -> dict:
    """High-level convenience wrapper: parse both picks, settle them
    independently against the official final score, compute divergence,
    and return a single envelope payload.

    Designed to be called from:
      • track_pick() when the user marks the result.
      • backfill endpoints (PATCH /api/picks/{uid}/user-bet).
      • The /api/calibration/* aggregation pipeline.
    """
    engine = parse_pick(market=engine_market, selection=engine_selection, line=engine_line)
    user   = (
        parse_pick(market=user_market, selection=user_selection, line=user_line)
        if any((user_market, user_selection, user_line is not None))
        else None
    )

    eng_settle = settle_pick_against_score(
        pick=engine,
        final_home=final_home, final_away=final_away,
        f5_home=f5_home, f5_away=f5_away,
    )
    usr_settle = (
        settle_pick_against_score(
            pick=user,
            final_home=final_home, final_away=final_away,
            f5_home=f5_home, f5_away=f5_away,
        ) if user else {"result": eng_settle.get("result", RESULT_PENDING),
                        "reason": "no_user_pick_followed_engine"}
    )

    divergence = compute_divergence(engine_pick=engine, user_pick=user)

    return {
        "available":      engine.get("available", False),
        "engine_pick":    engine,
        "user_pick":      user,
        "engine_result":  eng_settle.get("result"),
        "user_result":    usr_settle.get("result"),
        "followed_engine": divergence["followed_engine"],
        "delta":           divergence["delta"],
        "line_difference": divergence["line_difference"],
        "line_direction":  divergence["line_direction"],
        "pick_variation":  divergence["pick_variation"],
        "_settle_meta": {
            "engine": eng_settle,
            "user":   usr_settle,
        },
    }


# Public re-exports for convenience.
__all__ = [
    "parse_pick",
    "settle_pick_against_score",
    "compute_divergence",
    "evaluate_engine_vs_user",
    "RESULT_WIN", "RESULT_LOSS", "RESULT_PUSH", "RESULT_PENDING", "RESULT_VOID",
    "DELTA_NONE", "DELTA_USER_PROTECTED_LINE", "DELTA_USER_AGGRESSIVE_LINE",
    "DELTA_DIFFERENT_MARKET", "DELTA_OPPOSITE_SIDE",
    "LINE_DIR_MORE_PROTECTED", "LINE_DIR_LESS_PROTECTED", "LINE_DIR_SAME",
]
