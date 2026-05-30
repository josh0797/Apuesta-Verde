"""MLB Live Intelligence Engine — v4 (Volatility + Script Break + Cashout).

This module is the **live counterpart** to ``mlb_pregame_analytics_v3.py``.
It NEVER modifies pregame probabilities, the expected runs engine, market
ranking, router buckets or the Moneyball guardrail. It ONLY answers:

  1. ``pitcher_volatility_score(pitcher_stats)`` — LOW / MEDIUM / HIGH
     classifier that penalises pitchers prone to blowups (high Hard Hit %,
     high Barrel %, frequent 4+/5+ runs allowed starts).

  2. ``detect_script_break(pregame_script, live_state)`` — boolean
     detector that compares the original game script against the live
     reality and flags SCRIPT_BROKEN when the game evolves outside the
     pregame projection envelope.

  3. ``reevaluate_live_script(live_state, pregame_payload)`` — produces a
     new live script code chosen from:
        LOW_SCORING_SCRIPT | FAVORITE_DOMINANCE | OFFENSIVE_BREAKOUT
      | BULLPEN_COLLAPSE | CHAOTIC_GAME | UNDER_STILL_HEALTHY
      | UNDER_IN_DANGER | OVER_NOW_FAVORED

  4. ``under_risk_monitor(pregame_pick, live_state)`` — risk score 0-100
     specifically for live Under picks, with verdict ON_TRACK / WATCH /
     UNDER_IN_DANGER / UNDER_BUSTED.

  5. ``cashout_advisor(pregame_pick, live_state, live_script)`` — final
     decision: HOLD / PARTIAL_CASHOUT / FULL_CASHOUT, with a Spanish
     narrative the UI can render verbatim.

  6. ``build_live_intelligence_payload(pregame_pick, live_state)`` —
     convenience aggregator that wraps the five helpers above into a
     single dict the frontend can consume in one shot.

All functions are pure (dict in → dict out). No IO, no DB writes. The
HTTP endpoint in ``server.py`` is the only caller responsible for live
state acquisition (MLB Stats API line-score) and any persistence.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("mlb_live_intelligence")


# ════════════════════════════════════════════════════════════════════════════
# Helpers — safe numeric coercion
# ════════════════════════════════════════════════════════════════════════════
def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════════════════
# 1. PITCHER VOLATILITY SCORE
# ════════════════════════════════════════════════════════════════════════════
VOLATILITY_LEVELS = ("LOW", "MEDIUM", "HIGH")


def pitcher_volatility_score(pitcher_stats: Optional[dict]) -> dict:
    """Classify a pitcher's volatility profile.

    Volatility = tendency to "blow up" (allow 4+/5+ runs early) even when
    season-level stats look fine. A pitcher with ERA 3.20 but 35% Hard
    Hit% and 4 of his last 10 starts allowing 5+ runs is HIGH volatility.

    Inputs (``pitcher_stats`` keys, all optional)::

        era                      float
        xera                     float          # expected ERA
        fip                      float
        whip                     float
        hard_hit_pct             float 0-1 or 0-100
        barrel_pct               float 0-1 or 0-100
        last10_runs_allowed      list[int]      # runs allowed per start
        starts_with_4plus_runs   int
        starts_with_5plus_runs   int
        games_pitched            int

    Returns::

        {
            "level":             "LOW" | "MEDIUM" | "HIGH",
            "score":             0-100,           # higher = more volatile
            "penalty":           int (0-15),      # subtractive penalty
            "reasons":           list[str],
            "components":        {... raw factors ...},
        }
    """
    p = pitcher_stats or {}

    era       = _f(p.get("era"),  3.80)
    xera      = _f(p.get("xera"), era)
    fip       = _f(p.get("fip"),  era)
    whip      = _f(p.get("whip"), 1.30)
    hard_hit  = _f(p.get("hard_hit_pct"), 0.0)
    barrel    = _f(p.get("barrel_pct"),   0.0)

    # Normalise hard_hit / barrel to 0..100 percentage points.
    if 0 < hard_hit < 1.5:
        hard_hit = hard_hit * 100.0
    if 0 < barrel < 1.5:
        barrel = barrel * 100.0

    last10 = p.get("last10_runs_allowed") or []
    if not isinstance(last10, list):
        last10 = []
    last10_n = len(last10)

    # Count blowups within last 10 (or whatever sample is available).
    starts_4plus = _i(p.get("starts_with_4plus_runs"))
    starts_5plus = _i(p.get("starts_with_5plus_runs"))
    if last10:
        try:
            starts_4plus = max(starts_4plus, sum(1 for r in last10 if _i(r) >= 4))
            starts_5plus = max(starts_5plus, sum(1 for r in last10 if _i(r) >= 5))
        except Exception:
            pass

    # Volatility raw score 0..100 (heuristic).
    raw = 25.0   # baseline volatility

    # ERA vs xERA/FIP gap — overperforming pitchers regress to volatility.
    era_gap = max(0.0, xera - era)
    if era_gap >= 0.80:
        raw += 12
    elif era_gap >= 0.40:
        raw += 6
    fip_gap = max(0.0, fip - era)
    if fip_gap >= 0.80:
        raw += 10
    elif fip_gap >= 0.40:
        raw += 5

    # WHIP penalty.
    if whip >= 1.45:
        raw += 12
    elif whip >= 1.30:
        raw += 6

    # Hard Hit % and Barrel %.
    if hard_hit >= 42.0:
        raw += 14
    elif hard_hit >= 38.0:
        raw += 8
    if barrel >= 10.0:
        raw += 14
    elif barrel >= 8.0:
        raw += 8

    # Recent blowups — strongest signal.
    if last10_n >= 5:
        rate_4 = starts_4plus / float(last10_n)
        rate_5 = starts_5plus / float(last10_n)
        if rate_5 >= 0.30:
            raw += 20
        elif rate_5 >= 0.20:
            raw += 12
        if rate_4 >= 0.40:
            raw += 8
    else:
        # Sample too small — but still penalise raw counts.
        if starts_5plus >= 3:
            raw += 14
        elif starts_5plus >= 2:
            raw += 8

    score = max(0.0, min(100.0, raw))

    if score >= 65:
        level = "HIGH"
        penalty = 12
    elif score >= 45:
        level = "MEDIUM"
        penalty = 6
    else:
        level = "LOW"
        penalty = 0

    reasons: list[str] = []
    if era_gap >= 0.40:
        reasons.append(f"xERA ({xera:.2f}) {era_gap:.2f} pts encima de ERA — regresión latente.")
    if fip_gap >= 0.40:
        reasons.append(f"FIP ({fip:.2f}) por encima de ERA — peripherals débiles.")
    if hard_hit >= 38.0:
        reasons.append(f"Hard Hit% alto ({hard_hit:.0f}%) — contacto duro frecuente.")
    if barrel >= 8.0:
        reasons.append(f"Barrel% alto ({barrel:.1f}%) — vulnerable a HR.")
    if whip >= 1.30:
        reasons.append(f"WHIP elevado ({whip:.2f}) — traffic on base.")
    if starts_5plus >= 2:
        reasons.append(f"{starts_5plus} apariciones con 5+ carreras en muestra reciente.")
    if not reasons:
        reasons.append("Sin señales de volatilidad reciente.")

    return {
        "level":      level,
        "score":      round(score, 1),
        "penalty":    penalty,
        "reasons":    reasons,
        "components": {
            "era":             round(era, 2),
            "xera":            round(xera, 2),
            "fip":             round(fip, 2),
            "whip":            round(whip, 2),
            "hard_hit_pct":    round(hard_hit, 1),
            "barrel_pct":      round(barrel, 2),
            "last10_n":        last10_n,
            "starts_4plus":    starts_4plus,
            "starts_5plus":    starts_5plus,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. SCRIPT BREAK DETECTOR
# ════════════════════════════════════════════════════════════════════════════
def detect_script_break(pregame_script: Optional[dict],
                        live_state: Optional[dict]) -> dict:
    """Compare pregame script vs live reality.

    pregame_script
    --------------
    Output of ``mlb_pregame_analytics_v3.generate_mlb_game_script()``::

        {"script_code": str, "expected_runs": float, "projected_margin": float, ...}

    live_state
    ----------
    Live game snapshot (caller normalises MLB Stats API line-score)::

        {
            "current_inning":      int 1-9+,    # 1-based
            "is_top_half":         bool,        # optional
            "home_runs":           int,
            "away_runs":           int,
            "home_starter_runs_allowed": int,
            "away_starter_runs_allowed": int,
            "home_starter_pulled": bool,
            "away_starter_pulled": bool,
            "bullpen_runs_allowed_home": int,
            "bullpen_runs_allowed_away": int,
        }

    Returns
    -------
    {
        "broken":          bool,
        "severity":        "NONE" | "MILD" | "STRONG",
        "reasons":         list[str],
        "current_total":   int,
        "innings_played":  float,   # fractional (Top 6 = 5.5, Bottom 6 = 6.0)
        "expected_total":  float,
        "delta_vs_script": float,
    }
    """
    ps = pregame_script or {}
    ls = live_state or {}

    expected_runs   = _f(ps.get("expected_runs"), 9.0)
    pregame_code    = (ps.get("script_code") or "LOW_VARIANCE_GAME").upper()

    inning = _i(ls.get("current_inning"), 0)
    is_top = bool(ls.get("is_top_half", True))
    home_r = _i(ls.get("home_runs"))
    away_r = _i(ls.get("away_runs"))
    total  = home_r + away_r

    # Innings effectively completed at this snapshot.
    # Top of inning N → (N-1) full innings + 0.5; Bottom of N → (N-1)+1.0
    if inning <= 0:
        innings_played = 0.0
    else:
        innings_played = float(inning - 1) + (0.5 if is_top else 1.0)
    if innings_played <= 0:
        innings_played = 0.5  # avoid zero-division and reflect early action

    # Project final total from current pace.
    if innings_played > 0:
        projected_final = total / innings_played * 9.0
    else:
        projected_final = total * 9.0

    delta_runs = total - (expected_runs * (innings_played / 9.0))
    # delta_runs > 0 ⇒ scoring faster than pregame model expected.

    starter_blowup = (
        _i(ls.get("home_starter_runs_allowed")) >= 5
        or _i(ls.get("away_starter_runs_allowed")) >= 5
    )
    starter_pulled_early = (
        (bool(ls.get("home_starter_pulled")) or bool(ls.get("away_starter_pulled")))
        and inning <= 5
    )

    reasons: list[str] = []

    # Broad heuristics — different scripts break for different reasons.
    broken = False
    severity = "NONE"

    if pregame_code in ("LOW_SCORING_PITCHERS_DUEL", "LOW_VARIANCE_GAME",
                        "FAVORITE_DOMINANCE"):
        # Low-scoring scripts break on early run explosions.
        if total >= 6 and innings_played <= 4.5:
            broken = True
            severity = "STRONG"
            reasons.append(
                f"{total} carreras en {innings_played:.1f} innings — "
                f"ritmo {projected_final:.1f} proyectado contra script bajo scoring."
            )
        elif total >= 4 and innings_played <= 3.0:
            broken = True
            severity = "MILD"
            reasons.append(
                f"{total} carreras antes del 4º — el script de poco scoring está en riesgo."
            )

    if pregame_code in ("OFFENSIVE_SHOOTOUT", "OFFENSIVE_BREAKOUT"):
        # Shootout scripts break when pitching unexpectedly dominates.
        if total <= 1 and innings_played >= 5.0:
            broken = True
            severity = "MILD"
            reasons.append(
                f"Apenas {total} carreras tras {innings_played:.0f} innings — "
                f"el shootout no se materializó."
            )

    if pregame_code == "FAVORITE_DOMINANCE":
        # Dominance scripts break when the underdog leads after 5.
        proj_margin = _f(ps.get("projected_margin"))
        diff = (home_r - away_r) if (ps.get("favorite_side") == "home") else (away_r - home_r)
        if proj_margin >= 1.5 and diff <= -1 and innings_played >= 5.0:
            broken = True
            severity = "STRONG"
            reasons.append(
                f"Favorito perdiendo por {abs(diff)} tras {innings_played:.0f} innings — "
                f"dominancia proyectada no se materializa."
            )

    # Universal break — starter blowup.
    if starter_blowup:
        broken = True
        severity = "STRONG" if severity != "STRONG" else "STRONG"
        reasons.append("Abridor permitió 5+ carreras — script estructural roto.")
    elif starter_pulled_early:
        broken = True
        if severity == "NONE":
            severity = "MILD"
        reasons.append("Abridor salió antes del 6º — bullpen extendido obligado.")

    # Total runs already eclipsed expected.
    if total >= expected_runs and innings_played <= 6.0:
        broken = True
        severity = "STRONG"
        reasons.append(
            f"{total} carreras ya superan el ER pregame de {expected_runs:.1f} con "
            f"{9 - int(innings_played)} innings por jugar."
        )

    if not broken:
        reasons.append("Script pregame intacto — ritmo de carreras coincide con proyección.")

    return {
        "broken":           broken,
        "severity":         severity,
        "reasons":          reasons,
        "current_total":    total,
        "innings_played":   round(innings_played, 1),
        "expected_total":   round(expected_runs, 1),
        "projected_final":  round(projected_final, 1),
        "delta_vs_script":  round(delta_runs, 1),
        "starter_blowup":   starter_blowup,
        "starter_pulled_early": starter_pulled_early,
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. LIVE SCRIPT REEVALUATION
# ════════════════════════════════════════════════════════════════════════════
LIVE_SCRIPTS = (
    "LOW_SCORING_SCRIPT",
    "FAVORITE_DOMINANCE",
    "OFFENSIVE_BREAKOUT",
    "BULLPEN_COLLAPSE",
    "CHAOTIC_GAME",
    "UNDER_STILL_HEALTHY",
    "UNDER_IN_DANGER",
    "OVER_NOW_FAVORED",
)

LIVE_SCRIPT_LABELS_ES = {
    "LOW_SCORING_SCRIPT":  "Script de bajo scoring",
    "FAVORITE_DOMINANCE":  "Dominio del favorito",
    "OFFENSIVE_BREAKOUT":  "Explosión ofensiva",
    "BULLPEN_COLLAPSE":    "Colapso de bullpen",
    "CHAOTIC_GAME":        "Juego caótico",
    "UNDER_STILL_HEALTHY": "Under saludable",
    "UNDER_IN_DANGER":     "Under en peligro",
    "OVER_NOW_FAVORED":    "Over ahora favorecido",
}


def reevaluate_live_script(live_state: Optional[dict],
                            pregame_payload: Optional[dict] = None) -> dict:
    """Produce a live script classification from the current scoreboard.

    Triggers (caller logic) recommended at the end of innings 3, 5, 7.

    Returns
    -------
    {
        "live_script":               str (one of LIVE_SCRIPTS),
        "label_es":                  str,
        "narrative_es":              str,
        "expected_remaining_runs":   float,
        "projected_final_total":     float,
        "run_pace_per_inning":       float,
        "innings_played":            float,
        "innings_remaining":         float,
        "bullpen_pressure":          0-100,
        "win_probability_home":      0-100,
        "win_probability_away":      0-100,
    }
    """
    ls = live_state or {}
    pg = pregame_payload or {}
    pg_script = pg.get("script") or pg.get("game_script") or {}

    inning = _i(ls.get("current_inning"), 0)
    is_top = bool(ls.get("is_top_half", True))
    home_r = _i(ls.get("home_runs"))
    away_r = _i(ls.get("away_runs"))
    total  = home_r + away_r
    expected_runs = _f(pg_script.get("expected_runs"), _f(pg.get("expected_runs"), 9.0))

    if inning <= 0:
        innings_played = 0.5
    else:
        innings_played = float(inning - 1) + (0.5 if is_top else 1.0)
    innings_played = max(0.5, innings_played)
    innings_remaining = max(0.0, 9.0 - innings_played)

    run_pace = total / innings_played  # runs per inning so far

    # Project remaining runs using a blend of current pace + pregame ER pace.
    pg_pace = expected_runs / 9.0
    # If we're early (<3 IP), trust pregame more; late (>6 IP), trust live.
    if innings_played <= 3.0:
        live_weight = 0.35
    elif innings_played <= 6.0:
        live_weight = 0.65
    else:
        live_weight = 0.85
    proj_remaining_pace = live_weight * run_pace + (1.0 - live_weight) * pg_pace
    expected_remaining_runs = round(proj_remaining_pace * innings_remaining, 1)
    projected_final = round(total + expected_remaining_runs, 1)

    # Bullpen pressure proxy — starters out + bullpen runs allowed.
    bp_h = _i(ls.get("bullpen_runs_allowed_home"))
    bp_a = _i(ls.get("bullpen_runs_allowed_away"))
    starter_out = bool(ls.get("home_starter_pulled")) or bool(ls.get("away_starter_pulled"))
    bullpen_pressure = min(100.0, (bp_h + bp_a) * 18.0 + (35.0 if starter_out else 0.0))

    # Simple win-prob estimator from run differential + innings remaining.
    diff = home_r - away_r
    base_home_wp = 50.0 + diff * 12.0 - innings_remaining * 0.5 * diff * 0.0
    # When few innings remain and lead exists, ramp the favourite.
    if innings_remaining <= 3.0 and abs(diff) >= 2:
        base_home_wp = 50.0 + diff * 18.0
    if innings_remaining <= 1.5 and abs(diff) >= 1:
        base_home_wp = 50.0 + diff * 28.0
    wp_home = max(2.0, min(98.0, base_home_wp))
    wp_away = 100.0 - wp_home

    starter_blowup = (
        _i(ls.get("home_starter_runs_allowed")) >= 5
        or _i(ls.get("away_starter_runs_allowed")) >= 5
    )

    # ── Script classification ────────────────────────────────────────────
    code = "LOW_SCORING_SCRIPT"

    # Decide live script using a layered rules engine.
    if starter_blowup and innings_played <= 5.5:
        # Starter blowups within the first 5 innings are bullpen-collapse-like.
        code = "BULLPEN_COLLAPSE"
    elif total >= 10 and innings_played <= 7.0:
        code = "OFFENSIVE_BREAKOUT"
    elif projected_final >= expected_runs + 2.0:
        code = "OVER_NOW_FAVORED"
    elif projected_final >= expected_runs + 0.8 and innings_played >= 4.5:
        code = "UNDER_IN_DANGER"
    elif total <= 2 and innings_played >= 5.0:
        code = "LOW_SCORING_SCRIPT"
    elif abs(diff) >= 4 and innings_played >= 5.0:
        code = "FAVORITE_DOMINANCE"
    elif bullpen_pressure >= 70 and projected_final >= expected_runs + 1.5:
        code = "BULLPEN_COLLAPSE"
    elif total >= 6 and innings_played <= 5.0 and run_pace >= 1.3:
        code = "CHAOTIC_GAME"
    elif projected_final <= expected_runs - 0.8 and innings_played >= 4.0:
        code = "UNDER_STILL_HEALTHY"
    else:
        code = "LOW_SCORING_SCRIPT" if projected_final < expected_runs else "OVER_NOW_FAVORED"

    label = LIVE_SCRIPT_LABELS_ES.get(code, code)
    narrative = _build_live_script_narrative(
        code, total, run_pace, innings_played, projected_final,
        expected_runs, bullpen_pressure, diff,
    )

    return {
        "live_script":             code,
        "label_es":                label,
        "narrative_es":            narrative,
        "current_total":           total,
        "innings_played":          round(innings_played, 1),
        "innings_remaining":       round(innings_remaining, 1),
        "run_pace_per_inning":     round(run_pace, 2),
        "expected_remaining_runs": expected_remaining_runs,
        "projected_final_total":   projected_final,
        "expected_total_pregame":  round(expected_runs, 1),
        "bullpen_pressure":        round(bullpen_pressure, 1),
        "win_probability_home":    round(wp_home, 1),
        "win_probability_away":    round(wp_away, 1),
        "starter_blowup":          starter_blowup,
    }


def _build_live_script_narrative(code, total, run_pace, innings_played,
                                  projected_final, expected_runs,
                                  bullpen_pressure, diff):
    if code == "LOW_SCORING_SCRIPT":
        return (
            f"Apenas {total} carreras en {innings_played:.0f} innings "
            f"(pace {run_pace:.2f}/inn). Proyección final {projected_final:.1f} "
            f"por debajo del ER pregame {expected_runs:.1f}."
        )
    if code == "FAVORITE_DOMINANCE":
        return (
            f"Diferencia de {abs(diff)} carreras con {9 - int(innings_played)} "
            f"innings restantes — el favorito controla el juego."
        )
    if code == "OFFENSIVE_BREAKOUT":
        return (
            f"{total} carreras en {innings_played:.0f} innings. Explosión "
            f"ofensiva en curso; proyección final {projected_final:.1f}."
        )
    if code == "BULLPEN_COLLAPSE":
        return (
            f"Bullpen bajo presión (score {bullpen_pressure:.0f}/100). "
            f"Proyección final {projected_final:.1f} vs ER pregame "
            f"{expected_runs:.1f} — colapso en marcha."
        )
    if code == "CHAOTIC_GAME":
        return (
            f"Juego caótico: pace {run_pace:.2f}/inn con bullpens estresados. "
            f"Resultado final difícil de proyectar."
        )
    if code == "UNDER_STILL_HEALTHY":
        return (
            f"Under saludable: pace {run_pace:.2f}/inn y proyección "
            f"{projected_final:.1f} por debajo de ER pregame {expected_runs:.1f}."
        )
    if code == "UNDER_IN_DANGER":
        return (
            f"Under en peligro: pace {run_pace:.2f}/inn lleva a "
            f"{projected_final:.1f} carreras (ER pregame {expected_runs:.1f})."
        )
    if code == "OVER_NOW_FAVORED":
        return (
            f"Over ahora favorecido: proyección {projected_final:.1f} supera "
            f"el ER pregame {expected_runs:.1f} con margen."
        )
    return f"Script: {code}. Total {total} en {innings_played:.0f} innings."


# ════════════════════════════════════════════════════════════════════════════
# 4. UNDER RISK MONITOR
# ════════════════════════════════════════════════════════════════════════════
def under_risk_monitor(pregame_pick: Optional[dict],
                        live_state: Optional[dict],
                        live_script: Optional[dict] = None) -> dict:
    """Score the live risk of an Under pick (0-100).

    Returns dict with verdict ∈ {ON_TRACK, WATCH, UNDER_IN_DANGER, UNDER_BUSTED}.
    """
    pp = pregame_pick or {}
    ls = live_state or {}
    lsc = live_script or {}

    # Extract the Under line from the pregame pick (best-effort).
    market = ((pp.get("recommendation") or {}).get("market")
              or (pp.get("_mlb_script_v2") or {}).get("recommendedLine")
              or "").lower()
    line = None
    if "under" in market or "over" in market:
        import re
        m = re.search(r"(\d+(?:\.\d+)?)", market)
        if m:
            try:
                line = float(m.group(1))
            except (TypeError, ValueError):
                line = None
    if line is None:
        # Try v2 directly.
        line_field = (pp.get("_mlb_script_v2") or {}).get("recommendedLine") or ""
        if line_field:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)", line_field)
            if m:
                try:
                    line = float(m.group(1))
                except (TypeError, ValueError):
                    line = None

    is_under_pick = "under" in market

    inning = _i(ls.get("current_inning"), 0)
    is_top = bool(ls.get("is_top_half", True))
    home_r = _i(ls.get("home_runs"))
    away_r = _i(ls.get("away_runs"))
    total  = home_r + away_r

    if inning <= 0:
        innings_played = 0.5
    else:
        innings_played = float(inning - 1) + (0.5 if is_top else 1.0)
    innings_played = max(0.5, innings_played)
    innings_remaining = max(0.0, 9.0 - innings_played)

    if not is_under_pick:
        return {
            "verdict":    "NOT_APPLICABLE",
            "is_under_pick": False,
            "risk_score": 0,
            "remaining_margin": None,
            "line":       line,
            "narrative_es": "El pick pregame no es un Under — el monitor de riesgo no aplica.",
        }
    if line is None:
        return {
            "verdict":    "UNKNOWN_LINE",
            "is_under_pick": True,
            "risk_score": 0,
            "remaining_margin": None,
            "line":       None,
            "narrative_es": "No se pudo extraer la línea Under — verifica el pick pregame.",
        }

    # Margin = how many runs can still happen before the Under busts.
    remaining_margin = max(0.0, line - total - 0.5)  # -0.5 to be safe vs half-lines

    # Risk score 0-100.
    if total >= line:
        risk = 100.0
        verdict = "UNDER_BUSTED"
    else:
        # Pace-based risk.
        proj_final = _f(lsc.get("projected_final_total"),
                        total + (total / innings_played) * innings_remaining)
        delta_to_line = proj_final - line
        # Map delta_to_line ∈ [-3, +3] to risk ∈ [10, 90].
        risk = 50.0 + delta_to_line * 13.5
        # Innings remaining factor — early innings = more uncertainty.
        if innings_remaining >= 5.0 and delta_to_line > -1.0:
            risk += 8
        if innings_remaining <= 2.0 and delta_to_line < -0.5:
            risk = max(5.0, risk - 18)
        risk = max(0.0, min(100.0, risk))
        if risk >= 75:
            verdict = "UNDER_IN_DANGER"
        elif risk >= 50:
            verdict = "WATCH"
        else:
            verdict = "ON_TRACK"

    if verdict == "UNDER_BUSTED":
        narrative = f"Under {line} bustado — total {total} ya iguala/supera la línea."
    elif verdict == "UNDER_IN_DANGER":
        narrative = (
            f"Under {line} en peligro: total {total} con {innings_remaining:.0f} "
            f"innings restantes y proyección de {lsc.get('projected_final_total', '—')}."
        )
    elif verdict == "WATCH":
        narrative = f"Under {line} bajo observación — margen remanente {remaining_margin:.1f}."
    else:
        narrative = f"Under {line} en buen camino — margen remanente {remaining_margin:.1f}."

    return {
        "verdict":          verdict,
        "is_under_pick":    True,
        "risk_score":       round(risk, 0),
        "remaining_margin": round(remaining_margin, 1),
        "line":             line,
        "current_total":    total,
        "innings_remaining": round(innings_remaining, 1),
        "narrative_es":     narrative,
    }


# ════════════════════════════════════════════════════════════════════════════
# 5. CASHOUT ADVISOR
# ════════════════════════════════════════════════════════════════════════════
CASHOUT_VERDICTS = ("HOLD", "PARTIAL_CASHOUT", "FULL_CASHOUT")


def cashout_advisor(pregame_pick: Optional[dict],
                     live_state: Optional[dict],
                     live_script: Optional[dict] = None,
                     script_break: Optional[dict] = None,
                     under_risk: Optional[dict] = None) -> dict:
    """Final live decision: HOLD / PARTIAL_CASHOUT / FULL_CASHOUT.

    Inputs are the outputs of the previous helpers. The advisor combines:
      - whether the pregame script is broken
      - the live script classification
      - the under-risk monitor (when applicable)
      - bullpen pressure + innings remaining

    Returns
    -------
    {
        "verdict":      "HOLD" | "PARTIAL_CASHOUT" | "FULL_CASHOUT",
        "confidence":   0-100,
        "reasons":      list[str],
        "narrative_es": str,
    }
    """
    pp  = pregame_pick or {}
    _ls = live_state or {}    # reserved for future cashout logic
    del _ls
    lsc = live_script or {}
    sb  = script_break or {}
    ur  = under_risk or {}

    reasons: list[str] = []
    verdict = "HOLD"
    confidence = 60.0

    market = ((pp.get("recommendation") or {}).get("market") or "").lower()
    is_under_pick = "under" in market
    is_over_pick  = ("over" in market) and not is_under_pick
    is_runline_pick = "run line" in market or "runline" in market

    severity = (sb.get("severity") or "NONE").upper()
    script_broken = bool(sb.get("broken"))

    innings_played = _f(lsc.get("innings_played"),
                         _f(sb.get("innings_played"), 0.5))
    innings_remaining = _f(lsc.get("innings_remaining"), max(0.0, 9.0 - innings_played))

    live_code = (lsc.get("live_script") or "").upper()
    bp_pressure = _f(lsc.get("bullpen_pressure"))
    risk_score = _f(ur.get("risk_score"))

    # ── Decision logic ──────────────────────────────────────────────────
    if is_under_pick:
        if ur.get("verdict") == "UNDER_BUSTED":
            verdict = "FULL_CASHOUT"
            confidence = 95
            reasons.append("Under bustado — el resultado ya no es recuperable.")
        elif ur.get("verdict") == "UNDER_IN_DANGER" or risk_score >= 75:
            verdict = "FULL_CASHOUT"
            confidence = 85
            reasons.append(
                f"Under en peligro (risk {int(risk_score)}%). Proyección final "
                f"{lsc.get('projected_final_total', '—')} supera la línea."
            )
        elif script_broken and severity == "STRONG":
            verdict = "FULL_CASHOUT" if risk_score >= 60 else "PARTIAL_CASHOUT"
            confidence = 78
            reasons.append("Script pregame roto con severidad alta; equity en riesgo.")
        elif script_broken and severity == "MILD":
            verdict = "PARTIAL_CASHOUT"
            confidence = 70
            reasons.append("Script pregame parcialmente quebrado — protege parte del stake.")
        elif live_code in ("OVER_NOW_FAVORED", "OFFENSIVE_BREAKOUT", "BULLPEN_COLLAPSE"):
            verdict = "PARTIAL_CASHOUT"
            confidence = 68
            reasons.append(
                f"Live script ahora es {LIVE_SCRIPT_LABELS_ES.get(live_code, live_code)} "
                f"— el Under pierde tracción."
            )
        elif live_code in ("LOW_SCORING_SCRIPT", "UNDER_STILL_HEALTHY"):
            verdict = "HOLD"
            confidence = 80
            reasons.append("Live script confirma el Under — mantener.")
        else:
            verdict = "HOLD"
            confidence = 62
            reasons.append("Sin señal de salida — el Under sigue defendible.")

    elif is_over_pick:
        # Over picks — the symmetric logic.
        proj_final = _f(lsc.get("projected_final_total"))
        line = ur.get("line")  # reuse line extraction even if monitor wasn't applicable
        if proj_final and line and proj_final - line >= 2.0 and innings_remaining <= 3.0:
            verdict = "PARTIAL_CASHOUT"
            confidence = 70
            reasons.append("Over cómodo; toma parcial para asegurar.")
        elif live_code == "LOW_SCORING_SCRIPT" and innings_played >= 5.0:
            verdict = "FULL_CASHOUT"
            confidence = 80
            reasons.append("Live script bajo scoring — el Over difícilmente entra.")
        elif script_broken and severity == "STRONG":
            verdict = "PARTIAL_CASHOUT"
            confidence = 65
            reasons.append("Script roto fuerte; revisa exposición.")
        else:
            verdict = "HOLD"
            confidence = 60
            reasons.append("Over con tracción todavía — mantener.")

    elif is_runline_pick:
        # Run Line — depends on which side is favoured live.
        if live_code == "FAVORITE_DOMINANCE" and innings_played >= 6.0:
            verdict = "PARTIAL_CASHOUT"
            confidence = 70
            reasons.append("Favorito dominando — asegura ganancia parcial.")
        elif script_broken and severity == "STRONG":
            verdict = "PARTIAL_CASHOUT"
            confidence = 65
            reasons.append("Script roto fuerte sobre el favorito.")
        else:
            verdict = "HOLD"
            confidence = 60
            reasons.append("Run Line aún viva — sostener.")

    else:
        verdict = "HOLD"
        confidence = 55
        reasons.append("Mercado no específico; mantener por defecto.")

    # Bullpen pressure escalator (universal).
    if bp_pressure >= 75 and verdict == "HOLD" and is_under_pick:
        verdict = "PARTIAL_CASHOUT"
        confidence = max(confidence, 68)
        reasons.append("Bullpen colapsando — riesgo crece innings finales.")

    # Build short narrative.
    if verdict == "FULL_CASHOUT":
        narrative = "Cashout completo recomendado: " + reasons[0]
    elif verdict == "PARTIAL_CASHOUT":
        narrative = "Cashout parcial recomendado: " + reasons[0]
    else:
        narrative = "Mantener pick: " + reasons[0]

    return {
        "verdict":      verdict,
        "confidence":   round(confidence, 0),
        "reasons":      reasons,
        "narrative_es": narrative,
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. CONVENIENCE AGGREGATOR
# ════════════════════════════════════════════════════════════════════════════
def build_live_intelligence_payload(pregame_pick: Optional[dict],
                                     live_state: Optional[dict]) -> dict:
    """Aggregate the five helpers into the payload the UI consumes.

    pregame_pick : full pick payload as produced by mlb_day_orchestrator
                   (must include recommendation + _mlb_script_v3 + _mlb_script_v2).
    live_state   : normalised live snapshot (see detect_script_break for shape).

    Returns
    -------
    {
        "version":        4,
        "volatility":     {... per starter ...},
        "script_break":   {...},
        "live_script":    {...},
        "under_risk":     {...},
        "cashout":        {...},
        "live_state_echo":{...},   # echoed for the UI to render
    }
    """
    pp = pregame_pick or {}
    pg_script = (pp.get("_mlb_script_v3") or {}).get("script") or {}

    # 1. Volatility per starter (best-effort lookup).
    v3 = pp.get("_mlb_script_v3") or {}
    pitchers_block = v3.get("pitchers_block") or {}
    home_p_stats = (pp.get("all_components") or {}).get("home_pitcher_stats") \
                   or pp.get("home_pitcher_stats") or {}
    away_p_stats = (pp.get("all_components") or {}).get("away_pitcher_stats") \
                   or pp.get("away_pitcher_stats") or {}
    # Augment with name and quality from v3 pitchers block.
    if pitchers_block.get("home"):
        home_p_stats = {**home_p_stats, "name": pitchers_block["home"].get("name")}
    if pitchers_block.get("away"):
        away_p_stats = {**away_p_stats, "name": pitchers_block["away"].get("name")}

    vol_home = pitcher_volatility_score(home_p_stats)
    vol_away = pitcher_volatility_score(away_p_stats)

    # 2. Script break detection.
    sb = detect_script_break(pg_script, live_state)
    # 3. Live script.
    lsc = reevaluate_live_script(live_state, {
        "script": pg_script,
        "expected_runs": pg_script.get("expected_runs"),
    })
    # 4. Under risk monitor.
    ur = under_risk_monitor(pp, live_state, lsc)
    # 5. Cashout advisor.
    co = cashout_advisor(pp, live_state, lsc, sb, ur)

    return {
        "version":      4,
        "volatility": {
            "home": vol_home,
            "away": vol_away,
            "combined_penalty": vol_home["penalty"] + vol_away["penalty"],
        },
        "script_break":    sb,
        "live_script":     lsc,
        "under_risk":      ur,
        "cashout":         co,
        "live_state_echo": live_state or {},
        "pregame_script":  pg_script,
    }


__all__ = [
    "pitcher_volatility_score",
    "detect_script_break",
    "reevaluate_live_script",
    "under_risk_monitor",
    "cashout_advisor",
    "build_live_intelligence_payload",
    "VOLATILITY_LEVELS",
    "LIVE_SCRIPTS",
    "LIVE_SCRIPT_LABELS_ES",
    "CASHOUT_VERDICTS",
]
