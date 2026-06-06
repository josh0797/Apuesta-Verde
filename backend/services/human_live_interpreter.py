"""Human Live Interpreter — turns raw live numbers into a coach's voice.

The rest of the engine produces accurate metrics (xG live, threat index,
pressure rate, edge, freshness, trap detection). What it does NOT do is
*translate* those into the language a bettor actually wants:

  • a single big-picture recommendation
  • a confidence number they can trust at a glance
  • a clear action verb (apostar / esperar / pasar / cash-out)
  • a "¿por qué?" with 2-3 plain-Spanish sentences
  • a protected-market hint when the direct line has no value
  • a trap warning when the market is mispricing the leader

This module is that translator. It is a PURE function (no IO, no LLM call
on the hot path) on top of the artefacts already produced by:

  • services.live_xg_proxy     (compute_live_analysis)
  • services.live_reevaluation (reevaluate_match)
  • services.under_market_scan (scan_protected_alternatives)

Output is shaped to drop straight into the UI's `LiveCopilotCard`:

    {
      "title":            str,        # rich, human title (replaces BALANCEADO)
      "subtitle":         str,        # one sentence narration
      "mood":             "trap"|"value"|"watch"|"neutral"|"insufficient",
      "icon":             str,        # emoji shortcut for the UI
      "action":           "BET_NOW"|"WAIT"|"WATCHLIST"|"NO_BET"|"CASH_OUT"|"LOW_CONFIDENCE",
      "action_label":     str,        # "APOSTAR AHORA", "ESPERAR", ...
      "recommendation":   str,        # "✅ UNDER 3.5 GOLES" or "⛔ NO BET" etc.
      "suggested_market": str | None, # "Under 3.5" / "Doble Oportunidad 1X" / etc.
      "confidence":       int 0-100,
      "risk":             "LOW"|"MEDIUM"|"HIGH",
      "urgency":          "low"|"medium"|"high",
      "why":              list[str],  # plain Spanish bullets
      "narration":        str,        # one-paragraph spoken-style "Razón:"
      "trap":             dict|None,  # echo of detect_late_lead_trap()
      "_source":          "human_live_interpreter_v1",
    }
"""
from __future__ import annotations

from typing import Optional


# ─── Helpers ────────────────────────────────────────────────────────────────

def _safe(d: dict | None, *path, default=None):
    cur = d or {}
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _team_name(match: dict, side: str) -> str:
    name = _safe(match, f"{side}_team", "name") or (side == "home" and "Local" or "Visitante")
    return str(name)


def _build_scoreboard_context(
    *, home_name: str, away_name: str,
    h_score: int, a_score: int,
    minute: Optional[int],
    strength: float,
    verdict_label: str,
) -> dict:
    """Build a structured scoreboard context block.

    The UI uses this block to render badges ("Ventaja clara", "Control
    por marcador", "Marcador pesa más que métricas") without having to
    re-implement the scoreline classifier.

    `scoreState` taxonomy (machine-readable, exported to the UI):
      • LEVEL_SCORE              — diff == 0
      • ONE_GOAL_LEAD            — diff == 1
      • CLEAR_LEAD               — diff == 2 (cualquier minuto)
      • BLOWOUT                  — diff >= 3
      • LATE_PROTECTED_LEAD      — diff == 1 con min >= 70
      • CONTROLLED_LEAD          — diff >= 2 con min >= 70

    `dynamicState` further qualifies CLEAR_LEAD / BLOWOUT:
      • DOMINANT_LEAD                       — líder también domina stats
      • STATISTICAL_BALANCE_WITH_CLEAR_LEAD — líder no domina stats pero
                                              tiene ventaja en marcador
      • SCOREBOARD_TRAP                     — líder gana pero stats lo
                                              contradicen (trap señal)
      • SCOREBOARD_CONTROL                  — ventaja consolidada tarde
      • LEADING_WITHOUT_DOMINANCE           — ventaja de 1 sin dominio
      • TRAILING_BUT_PRESSING               — perdedor empujando fuerte
    """
    diff   = h_score - a_score
    m      = minute or 0
    abs_d  = abs(diff)
    leader   = home_name if diff > 0 else (away_name if diff < 0 else None)
    trailing = away_name if diff > 0 else (home_name if diff < 0 else None)

    # 1. Base state
    if abs_d == 0:
        score_state = "LEVEL_SCORE"
    elif abs_d >= 3:
        score_state = "BLOWOUT"
    elif abs_d == 2:
        score_state = "CONTROLLED_LEAD" if m >= 70 else "CLEAR_LEAD"
    else:  # abs_d == 1
        score_state = "LATE_PROTECTED_LEAD" if m >= 70 else "ONE_GOAL_LEAD"

    # 2. Dynamic qualifier on top of base state
    dyn: Optional[str] = None
    if score_state in ("CLEAR_LEAD", "BLOWOUT", "CONTROLLED_LEAD"):
        if verdict_label == "TRAP_LATE_LEAD":
            dyn = "SCOREBOARD_TRAP"
        elif strength >= 0.18:
            # Strong direction → check direction matches leader
            dyn = "DOMINANT_LEAD"
        else:
            dyn = "STATISTICAL_BALANCE_WITH_CLEAR_LEAD"
        # Override with SCOREBOARD_CONTROL if we're past 70'
        if score_state == "CONTROLLED_LEAD":
            dyn = dyn or "SCOREBOARD_CONTROL"
    elif score_state in ("ONE_GOAL_LEAD", "LATE_PROTECTED_LEAD"):
        if strength < 0.10:
            dyn = "LEADING_WITHOUT_DOMINANCE"
        elif verdict_label == "TRAP_LATE_LEAD":
            dyn = "SCOREBOARD_TRAP"
    elif score_state == "LEVEL_SCORE":
        if verdict_label == "LIVE_VALUE_PUSH":
            dyn = "TRAILING_BUT_PRESSING"  # no leader but momentum push

    # 3. UI badges — list of (label, severity) pairs (ES)
    badges: list[dict] = []
    if score_state == "BLOWOUT":
        badges.append({"label": "Marcador definido", "severity": "high"})
    elif score_state == "CONTROLLED_LEAD":
        badges.append({"label": "Control por marcador", "severity": "medium"})
    elif score_state == "CLEAR_LEAD":
        badges.append({"label": "Ventaja clara", "severity": "medium"})
    elif score_state == "LATE_PROTECTED_LEAD":
        badges.append({"label": "Líder defendiendo", "severity": "medium"})
    if dyn == "STATISTICAL_BALANCE_WITH_CLEAR_LEAD":
        badges.append({"label": "Marcador pesa más que métricas", "severity": "info"})
    elif dyn == "DOMINANT_LEAD":
        badges.append({"label": "Dominio total", "severity": "info"})
    elif dyn == "SCOREBOARD_TRAP":
        badges.append({"label": "Posible trampa de marcador", "severity": "high"})
    elif dyn == "LEADING_WITHOUT_DOMINANCE":
        badges.append({"label": "Gana sin dominar", "severity": "info"})

    return {
        "scoreState":      score_state,
        "dynamicState":    dyn,
        "leadingTeam":     leader,
        "trailingTeam":    trailing,
        "goalDifference":  diff,
        "minute":          minute,
        "gamePhase":       _game_phase(minute),
        "badges":          badges,
    }


def _game_phase(minute: Optional[int]) -> str:
    m = minute or 0
    if m < 1:
        return "pre_kick"
    if m < 30:
        return "first_third"
    if m < 45:
        return "mid_first_half"
    if m < 50:
        return "half_time"
    if m < 70:
        return "second_half_open"
    if m < 85:
        return "closing_phase"
    return "stoppage"


def _scoreline_context(diff: int, h_score: int, a_score: int, minute: Optional[int]) -> str:
    """Classify the match state based on scoreline + time.

    Returns a context key that takes priority over pace in title/narration:
      'blowout'        — diff >= 3 (partido sentenciado)
      'commanding'     — diff == 2 con min >= 60 (ventaja consolidada)
      'clear_lead'     — diff == 2 con min < 60 (ventaja clara, todavía jugable)
      'late_lead'      — diff == 1 con min >= 75 (líder defendiendo)
      'one_goal_early' — diff == 1 con min < 60 (partido vivo)
      'level'          — diff == 0 (empatado)

    NOTE (P1 fix 2026-05-28): se añadió `clear_lead` para diff == 2 antes
    de la hora 60. Antes este caso caía en `level` y el copilot decía
    "ningún equipo domina claramente" pese a un 0-2 al descanso, que es
    una contradicción evidente con el marcador.
    """
    m = minute or 0
    abs_diff = abs(diff)
    if abs_diff >= 3:
        return "blowout"
    if abs_diff == 2 and m >= 60:
        return "commanding"
    if abs_diff == 2:
        return "clear_lead"
    if abs_diff == 1 and m >= 75:
        return "late_lead"
    if abs_diff == 1:
        return "one_goal_early"
    return "level"


def _offensive_market_suggestion(
    h_score: int, a_score: int, minute: Optional[int], pace: str
) -> Optional[str]:
    """Suggest the best OFFENSIVE market given the live state.

    Returns a market string or None if no offensive market makes sense.

    Logic:
      • Si ambos equipos han marcado → BTTS ya ocurrió → sugerir Over de goles
        adicionales o nada (no tiene sentido sugerir BTTS de nuevo).
      • Si ningún equipo ha marcado y el ritmo es abierto → Over 1.5 (alta prob).
      • Si el marcador es 1-0 o 0-1 con ritmo abierto → BTTS (el que no marcó
        puede empatar) o Over 2.5 dependiendo del tiempo.
      • Si el marcador ya tiene 2+ goles totales y hay tiempo → Over 3.5.
      • Si queda poco tiempo (>75) → evitar Over (demasiado riesgo temporal).
    """
    m = minute or 0
    total = h_score + a_score
    both_scored = h_score > 0 and a_score > 0

    # Tarde en el partido — los Over son de alto riesgo temporal
    if m >= 75:
        return None

    # Partido abierto (ambos marcaron) → Over de siguiente gol
    if both_scored:
        if total <= 2 and m < 65:
            return "Over 3.5"
        if total == 1 and m < 50:
            return "Over 2.5"
        return None  # demasiados goles o poco tiempo

    # Solo un equipo marcó → BTTS o Over 2.5 según ritmo y tiempo
    if total == 1:
        if pace == "abierto" and m < 60:
            return "BTTS (Ambos marcan)"
        if pace in ("abierto", "medio") and m < 70:
            return "Over 2.5"
        return None

    # Nadie ha marcado → Over 1.5 si el ritmo lo justifica
    if total == 0:
        if pace == "abierto":
            return "Over 1.5"
        if pace == "medio" and m < 55:
            return "Over 1.5"
        return None

    return None


def _numerical_state(analysis: dict) -> dict:
    """Extract numerical advantage state from live analysis incidents.

    Returns:
      {
        "has_incident":       bool,   # True si hay al menos una roja
        "advantage":          "home"|"away"|"none",
        "diff":               int,    # jugadores de diferencia (0, 1, 2...)
        "short_side":         "home"|"away"|None,  # el que tiene menos
        "short_side_reds":    int,
        "red_cards":          list,   # lista completa de rojas
        "first_red_minute":   int|None,
        "minutes_short":      int|None, # cuántos minutos lleva en inferioridad
      }
    """
    if not isinstance(analysis, dict):
        analysis = {}
    incidents = analysis.get("incidents") or {}
    # incidents puede venir directo del analysis o anidado en live_stats
    if not incidents:
        live = analysis.get("live_stats") or {}
        incidents = live.get("incidents") or {}

    red_cards      = incidents.get("red_cards") or []
    advantage      = incidents.get("numerical_advantage") or "none"
    diff           = incidents.get("numerical_diff") or 0
    home_reds      = incidents.get("home_reds") or 0
    away_reds      = incidents.get("away_reds") or 0
    has_incident   = bool(red_cards)

    short_side = None
    short_reds = 0
    if advantage == "home":       # home tiene ventaja → away está corto
        short_side = "away"
        short_reds = away_reds
    elif advantage == "away":     # away tiene ventaja → home está corto
        short_side = "home"
        short_reds = home_reds

    first_red_minute = None
    if red_cards:
        minutes = [r["minute"] for r in red_cards if r.get("minute") is not None]
        if minutes:
            first_red_minute = min(minutes)

    minutes_short = None
    if first_red_minute is not None and analysis.get("minute") is not None:
        minutes_short = max(0, int(analysis.get("minute") or 0) - first_red_minute)

    return {
        "has_incident":     has_incident,
        "advantage":        advantage,
        "diff":             diff,
        "short_side":       short_side,
        "short_side_reds":  short_reds,
        "red_cards":        red_cards,
        "first_red_minute": first_red_minute,
        "minutes_short":    minutes_short,
    }


def _pace_label(home: dict, away: dict) -> str:
    """Tactical pace given current xG + shots + dangerous attacks."""
    xg = (home.get("xg_live") or 0) + (away.get("xg_live") or 0)
    shots = (home.get("shots") or 0) + (away.get("shots") or 0)
    if xg < 0.6 and shots < 8:
        return "lento_tactico"
    if xg < 1.2 and shots < 14:
        return "medio"
    return "abierto"


def _direction(home: dict, away: dict) -> tuple[str, float]:
    """Returns (side, strength 0..1) where side ∈ {'home','away','none'}.

    Combines normalized xG delta, pressure delta and threat delta.
    """
    xg_h, xg_a = float(home.get("xg_live") or 0), float(away.get("xg_live") or 0)
    pr_h, pr_a = float(home.get("pressure_rate") or 0), float(away.get("pressure_rate") or 0)
    th_h, th_a = float(home.get("threat_index") or 0), float(away.get("threat_index") or 0)
    # Normalise each delta into -1..+1.
    def _norm(a, b):
        t = abs(a) + abs(b)
        return ((a - b) / t) if t > 0.0001 else 0.0
    score = (_norm(xg_h, xg_a) * 0.45) + (_norm(pr_h, pr_a) * 0.30) + (_norm(th_h, th_a) * 0.25)
    if score >= 0.18:
        return "home", min(1.0, abs(score) * 2.0)
    if score <= -0.18:
        return "away", min(1.0, abs(score) * 2.0)
    return "none", abs(score)


# ─── Public API ─────────────────────────────────────────────────────────────

def _interpret_baseball_live(match: dict, analysis: dict) -> dict:
    """MLB-language copilot payload — speaks runs/hits/innings, never goals.

    Drives from the output of `live_baseball_analytics.compute_live_analysis`
    which already exposes inning, score, run_rate, threat_score and verdict.
    """
    home = _team_name(match, "home")
    away = _team_name(match, "away")
    inning = analysis.get("inning") or analysis.get("minute")
    inning_half = analysis.get("inning_half")  # 'top' / 'bottom' / None
    score = analysis.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    diff = h_score - a_score
    verdict = (analysis.get("verdict") or {})
    trap = analysis.get("trap") or {}
    leader_odds = analysis.get("leader_odds")
    proj_total = (analysis.get("deltas") or {}).get("projected_total")

    # Score readout in MLB terms
    leader = home if h_score > a_score else (away if a_score > h_score else None)
    half_label = "Top" if inning_half == "top" else ("Bottom" if inning_half == "bottom" else "")
    inning_str = f"{half_label} {inning}".strip() if inning else "—"

    why: list[str] = []
    risks: list[str] = []
    market_suggestion: dict | None = None
    action = "WAIT"
    action_label = "ESPERAR"

    # Trap detection in MLB context
    if trap.get("type") == "LATE_LEAD_TRAP":
        action = "AVOID_LEADER_ML"
        action_label = "EVITAR ML DEL LÍDER"
        why.append(
            f"{leader} lidera {abs(diff)} carrera{'s' if abs(diff) != 1 else ''} en el "
            f"{inning_str} con cuota {leader_odds:.2f}: el mercado ya descuenta el resultado."
            if leader and leader_odds else
            f"Líder con ventaja en el {inning_str} a cuota muy corta — sin EV en el ML."
        )
        risks.append("Un rally del rival en innings finales puede romper la línea sin avisar.")
    elif diff != 0 and inning and inning >= 7 and abs(diff) >= 4:
        action = "TOTAL_UNDER_REMAINING"
        action_label = "CONSIDERAR TOTAL UNDER RESTANTE"
        why.append(
            f"Ventaja amplia ({abs(diff)} carreras) en el {inning_str}: el bullpen suele "
            f"administrar y el ritmo ofensivo baja."
        )
        if proj_total:
            market_suggestion = {
                "market":   "Total Runs Under",
                "selection": f"Bajo proyección {proj_total:.1f}",
                "reason":   f"Run rate del partido proyecta {proj_total:.1f} carreras totales.",
            }
    elif diff == 0 and inning and inning >= 6:
        action = "WATCH"
        action_label = "MONITOREAR"
        why.append(f"Partido empatado en el {inning_str} — alta volatilidad de bullpen.")
        risks.append("Cualquier base por bolas puede desencadenar el rally decisivo.")
    else:
        why.append(f"Marcador {h_score}-{a_score} en el {inning_str}. Run rate combinado "
                   f"{(analysis.get('deltas') or {}).get('run_rate_combined') or 0:.2f}/inning.")

    # Pace context
    if proj_total:
        why.append(f"Proyección total del partido: {proj_total:.1f} carreras.")

    title = (
        "Trampa de marcador (bullpen)" if action == "AVOID_LEADER_ML" else
        "Total restante bajo presión"   if action == "TOTAL_UNDER_REMAINING" else
        "Partido en zona de bullpen"    if action == "WATCH" else
        "Partido en desarrollo"
    )
    return {
        "sport":          "baseball",
        "title":          title,
        "mood":           "neutral" if action == "WAIT" else ("danger" if action == "AVOID_LEADER_ML" else "watch"),
        "verdict":        verdict.get("label") or "BALANCED",
        "action":         action,
        "action_label":   action_label,
        "score_summary":  f"{home} {h_score} — {a_score} {away} ({inning_str})",
        "why":            why,
        "risks":          risks,
        "market_suggestion": market_suggestion,
        "trap":           trap if trap else None,
        "_source":        "human_live_interpreter_baseball_v1",
    }


def _interpret_basketball_live(match: dict, analysis: dict) -> dict:
    """Basketball-language copilot payload — speaks pace/points/quarter."""
    home = _team_name(match, "home")
    away = _team_name(match, "away")
    score = analysis.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    diff = h_score - a_score
    period = analysis.get("period") or analysis.get("quarter") or analysis.get("status")
    verdict = (analysis.get("verdict") or {})
    trap = analysis.get("trap") or {}
    proj_total = (analysis.get("deltas") or {}).get("projected_total")
    pace_combined = (analysis.get("deltas") or {}).get("pace_combined")

    period_str = str(period) if period else "—"
    leader = home if h_score > a_score else (away if a_score > h_score else None)

    why: list[str] = []
    risks: list[str] = []
    market_suggestion: dict | None = None
    action = "WAIT"
    action_label = "ESPERAR"

    if trap.get("type") == "BLOWOUT_TRAP":
        action = "AVOID_LEADER_ML"
        action_label = "EVITAR ML DEL LÍDER"
        why.append(
            f"{leader} domina {abs(diff)} puntos en el {period_str}: garbage time inminente."
        )
        risks.append("Sustituciones masivas reducen pace; cualquier triple recorta sin reflejar dominio real.")
    elif abs(diff) >= 12 and period_str in ("Q4", "OT"):
        action = "TOTAL_UNDER_REMAINING"
        action_label = "TOTAL UNDER RESTANTE"
        why.append(f"Diferencia de {abs(diff)} en {period_str}: pace cae con cierre administrado.")
        if proj_total:
            market_suggestion = {
                "market": "Total Points Under",
                "selection": f"Bajo proyección {proj_total:.0f}",
                "reason":   f"Proyección actual del partido: {proj_total:.0f} puntos.",
            }
    elif abs(diff) <= 4 and period_str in ("Q3", "Q4"):
        action = "WATCH"
        action_label = "MONITOREAR"
        why.append(f"Partido cerrado ({h_score}-{a_score}) en {period_str} — momentum decide.")
    else:
        why.append(f"Marcador {h_score}-{a_score} en {period_str}.")

    if pace_combined:
        why.append(f"Pace combinado: {pace_combined:.1f} pos/48min.")
    if proj_total:
        why.append(f"Proyección total: {proj_total:.0f} puntos.")

    title = (
        "Trampa de paliza (garbage time)"    if action == "AVOID_LEADER_ML" else
        "Total restante bajo presión"         if action == "TOTAL_UNDER_REMAINING" else
        "Partido cerrado en cuarto decisivo"  if action == "WATCH" else
        "Partido en desarrollo"
    )
    return {
        "sport":          "basketball",
        "title":          title,
        "mood":           "neutral" if action == "WAIT" else ("danger" if action == "AVOID_LEADER_ML" else "watch"),
        "verdict":        verdict.get("label") or "BALANCED",
        "action":         action,
        "action_label":   action_label,
        "score_summary":  f"{home} {h_score} — {a_score} {away} ({period_str})",
        "why":            why,
        "risks":          risks,
        "market_suggestion": market_suggestion,
        "trap":           trap if trap else None,
        "_source":        "human_live_interpreter_basketball_v1",
    }


def interpret_live(
    match: dict,
    *,
    analysis: dict | None,
    reeval: dict | None = None,
    alt_market: dict | None = None,
) -> dict:
    """Build the copilot-style payload.

    SPORT GATE: dispatch to sport-specific interpreters. The football flow
    (the original body of this function) speaks goles/Moneyline/BTTS. For
    basket/baseball we route to dedicated interpreters that speak the
    correct vocabulary — never returning "goles" or "córners" for non-football.
    """
    sport = (match.get("sport") or analysis.get("_sport") if analysis else None) or "football"
    if sport == "baseball":
        return _interpret_baseball_live(match, analysis or {})
    if sport == "basketball":
        return _interpret_basketball_live(match, analysis or {})

    # ── Football pathway (original logic) ───────────────────────────────
    analysis = analysis or {}
    minute = analysis.get("minute")
    score = analysis.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    home = analysis.get("home") or {}
    away = analysis.get("away") or {}
    trap = analysis.get("trap")
    verdict_label = _safe(analysis, "verdict", "label") or "BALANCED"

    home_name = _team_name(match, "home")
    away_name = _team_name(match, "away")
    diff = h_score - a_score
    pace = _pace_label(home, away)
    direction, strength = _direction(home, away)

    # ── Numerical state (red cards / inferioridad) ──────────────────────
    num = _numerical_state(analysis)
    short_team  = home_name if num["short_side"] == "home" else (
                  away_name if num["short_side"] == "away" else None)
    long_team   = away_name if num["short_side"] == "home" else (
                  home_name if num["short_side"] == "away" else None)

    # ── 1. Decide mood + action ─────────────────────────────────────────
    mood = "neutral"
    action = "WAIT"
    action_label = "ESPERAR MEJOR LÍNEA"
    icon = "⚖️"
    risk = "MEDIUM"
    urgency = "low"
    suggested_market: Optional[str] = None
    recommendation = "ESPERAR — sin señal clara"
    why: list[str] = []
    narration_parts: list[str] = []

    # ── Inferioridad numérica — señal dominante sobre pace y verdict ────
    # Si hay una roja, la recomendación debe reflejar la nueva dinámica
    # antes de analizar cualquier otra señal. Esto previene el bug donde
    # un 0-1 con 10 hombres leía igual que un partido normal.
    if num["has_incident"] and num["advantage"] != "none" and not (
        reeval and reeval.get("edge") is not None
    ):
        adv_team  = long_team   # equipo con más jugadores
        dis_team  = short_team  # equipo con menos jugadores
        n_reds    = num["short_side_reds"]
        min_short = num["minutes_short"]
        min_label = f"{min_short} min" if min_short is not None else "varios minutos"

        why.append(
            f"🟥 {dis_team} juega con {11 - n_reds} jugadores desde el min "
            f"{num['first_red_minute'] or '?'} ({min_label} en inferioridad)."
        )

        # ¿Quién va ganando?
        dis_is_winning = (
            (num["short_side"] == "home" and diff > 0) or
            (num["short_side"] == "away" and diff < 0)
        )
        dis_is_losing = (
            (num["short_side"] == "home" and diff < 0) or
            (num["short_side"] == "away" and diff > 0)
        )

        if dis_is_winning:
            # El equipo corto va ganando → trampa potencial al apostar al líder
            mood, icon = "watch", "⚠️"
            action, action_label = "WATCHLIST", "VIGILAR — LÍDER CON 10"
            recommendation = f"⚠️ {dis_team} gana con 10 — riesgo de remontada"
            risk, urgency = "HIGH", "high"
            why.append(
                f"{adv_team} tiene un jugador más — la presión puede crecer "
                f"y romper el bloque defensivo de {dis_team}."
            )
            why.append(
                "Evitar cuotas bajas al líder. Draw No Bet del favorito numérico "
                f"({adv_team}) puede tener más valor."
            )
            suggested_market = f"Draw No Bet — {adv_team}"
        elif dis_is_losing:
            # El equipo corto va perdiendo → resultado prácticamente decidido
            mood, icon = "neutral", "📊"
            action, action_label = "WAIT", "INFERIORIDAD + PERDIENDO"
            recommendation = f"📊 {dis_team} pierde y tiene 10 — difícil remontada"
            risk, urgency = "LOW", "low"
            why.append(
                f"{dis_team} va perdiendo Y tiene {11 - n_reds} jugadores. "
                f"La remontada es estadísticamente muy improbable."
            )
            # Mercado: Under de goles restantes o resultado al descanso
            cur_total = h_score + a_score
            if cur_total <= 2 and (minute or 0) < 70:
                suggested_market = f"Gana {adv_team} (resultado actual)"
            else:
                suggested_market = None
        else:
            # Empatados con inferioridad → el equipo con más jugadores tiene ventaja
            mood, icon = "value", "🔥"
            action, action_label = "BET_NOW", "APROVECHAR SUPERIORIDAD"
            recommendation = f"🔥 {adv_team} con superioridad numérica — ventaja real"
            risk, urgency = "MEDIUM", "high"
            why.append(
                f"{adv_team} juega con un jugador más — superioridad táctica "
                f"y física que tiende a materializarse en el marcador."
            )
            suggested_market = _offensive_market_suggestion(
                h_score, a_score, minute, pace
            ) or f"Doble Oportunidad — {adv_team}"

        narration_parts.append(
            f"Al minuto {minute or '?'}, {home_name} {h_score}-{a_score} {away_name}. "
            f"{dis_team} lleva {min_label} con {11 - n_reds} jugadores tras la expulsión "
            f"en el min {num['first_red_minute'] or '?'}."
        )

    # First — has the user already pasted a manual odds + reeval ran?
    # If yes the reeval result drives the recommendation.
    if reeval and reeval.get("edge") is not None:
        state = reeval.get("live_state")
        rec_action = (reeval.get("recommended_action") or "WAIT").upper()
        market = reeval.get("market") or "Mercado live"
        edge_pct = float(reeval.get("edge_pct") or 0.0)
        if state == "LINE_DEAD":
            mood, icon = "trap", "⛔"
            action, action_label = "NO_BET", "LÍNEA MUERTA"
            recommendation = f"⛔ {market.upper()} — ya no es posible"
            risk, urgency = "HIGH", "low"
            suggested_market = None
        elif state == "TRAP_DETECTED":
            mood, icon = "trap", "⛔"
            action, action_label = "NO_BET", "NO APOSTAR"
            recommendation = "⛔ NO APOSTAR — trampa de mercado"
            risk, urgency = "HIGH", "high"
            suggested_market = None
        elif rec_action == "BET":
            mood, icon = "value", "✅"
            action, action_label = "BET_NOW", "APOSTAR AHORA"
            recommendation = f"✅ {market.upper()}"
            suggested_market = market
            risk = "LOW" if edge_pct >= 6 else "MEDIUM"
            urgency = "high" if edge_pct >= 6 else "medium"
        elif rec_action == "WATCH":
            mood, icon = "watch", "👀"
            action, action_label = "WATCHLIST", "EN OBSERVACIÓN"
            recommendation = f"👀 {market} — esperar mejor línea"
            suggested_market = market
            risk, urgency = "MEDIUM", "medium"
        elif rec_action == "CASH_OUT":
            mood, icon = "value", "💰"
            action, action_label = "CASH_OUT", "CONSIDERAR CASH-OUT"
            recommendation = "💰 Cash-out recomendado"
            risk, urgency = "LOW", "high"
        else:  # PASS / HOLD
            mood, icon = "neutral", "⛔"
            action, action_label = "NO_BET", "NO APOSTAR"
            recommendation = "⛔ NO BET — sin valor"
            risk, urgency = "MEDIUM", "low"

        # Reuse reeval reason if it already exists (already in Spanish).
        if reeval.get("reason"):
            narration_parts.append(str(reeval["reason"]))

    elif not (num["has_incident"] and num["advantage"] != "none"):
        # ── No manual reeval yet AND no inferioridad numérica ──
        # Use analysis verdict + alt market.
        if verdict_label == "TRAP_LATE_LEAD" or (trap and trap.get("triggered")):
            mood, icon = "trap", "⛔"
            action, action_label = "NO_BET", "NO APOSTAR AL FAVORITO"
            recommendation = "⛔ TRAMPA DETECTADA"
            risk, urgency = "HIGH", "high"
            why.append("El favorito gana, pero las estadísticas no respaldan.")
            why.append("El rival presiona más en los últimos minutos.")
            narration_parts.append(
                _safe(analysis, "verdict", "reason_es")
                or "Trampa de mercado: el favorito tiene marcador a favor pero pierde el partido tácticamente."
            )
        elif verdict_label == "LIVE_VALUE_PUSH":
            side = _safe(analysis, "verdict", "side") or direction
            side_label = "local" if side == "home" else "visitante"
            mood, icon = "value", "🔥"
            action, action_label = "BET_NOW", "EVALUAR VALOR"
            recommendation = f"🔥 EMPUJE {side_label.upper()}"
            # Offensive market suggestion: Over / BTTS según estado del marcador.
            # Usa _offensive_market_suggestion() en lugar de la lógica hardcoded
            # de solo-Over que ignoraba BTTS y el contexto de tiempo.
            suggested_market = _offensive_market_suggestion(
                h_score, a_score, minute, pace
            )
            # Si el marcador ya tiene 3+ goles y queda tiempo, el empuje puede
            # ser interesante para Doble Oportunidad del lado que empuja.
            if suggested_market is None and (h_score + a_score) >= 3:
                push_side = home_name if side == "home" else away_name
                suggested_market = f"Doble Oportunidad — {push_side}"
            risk = "MEDIUM"
            urgency = "high"
            why.append(
                f"{home_name if side=='home' else away_name} genera más xG live "
                f"({home.get('xg_live', 0):.2f} vs {away.get('xg_live', 0):.2f})."
            )
            why.append(
                f"Presión {side_label}: "
                f"{(home if side=='home' else away).get('pressure_rate', 0):.2f}/min "
                f"vs {(away if side=='home' else home).get('pressure_rate', 0):.2f}/min."
            )
            if pace == "abierto":
                why.append("El partido está abierto: muchos tiros y oportunidades.")
            if suggested_market and "BTTS" in suggested_market:
                why.append(
                    "Solo un equipo ha marcado — el rival tiene xG suficiente para empatar."
                )
            if suggested_market and "Over" in suggested_market:
                why.append(
                    f"El ritmo ofensivo apoya {suggested_market} con tiempo suficiente."
                )
        elif verdict_label == "BALANCED":
            # Scoreline context takes priority over pace — a 3-0 must never
            # read as "Ritmo lento, partido táctico" regardless of xG/shots.
            _h = h_score
            _a = a_score
            _diff = _h - _a
            _ctx = _scoreline_context(_diff, _h, _a, minute)
            _leader   = home_name if _diff > 0 else away_name
            _trailing = away_name if _diff > 0 else home_name

            if _ctx == "blowout":
                mood, icon = "neutral", "🔴"
                action, action_label = "NO_BET", "EVITAR — RESULTADO DEFINIDO"
                recommendation = f"🔴 {_leader} sentencia el partido"
                risk, urgency = "HIGH", "low"
                why.append(f"{abs(_diff)} goles de ventaja — el resultado está prácticamente cerrado.")
                why.append("Sin valor live en Moneyline ni Spread del líder.")
            elif _ctx == "commanding":
                mood, icon = "neutral", "📊"
                action, action_label = "WATCHLIST", "VIGILAR UNDER RESTANTE"
                recommendation = f"📊 {_leader} controla con autoridad"
                risk, urgency = "MEDIUM", "low"
                why.append(f"Ventaja de {abs(_diff)} goles desde la hora de juego.")
                why.append("Remontada estadísticamente improbable — Under restante puede tener valor.")
            elif _ctx == "clear_lead":
                # P1 fix: 0-2 al descanso → el marcador YA cambia el partido,
                # aunque las stats estén equilibradas. Nunca decir "partido
                # parejo" cuando hay 2 goles de diferencia.
                mood, icon = "watch", "🎯"
                action, action_label = "WATCHLIST", "VENTAJA CLARA EN MARCADOR"
                recommendation = f"🎯 Ventaja clara — {_leader} gana {h_score}-{a_score}"
                risk, urgency = "MEDIUM", "medium"
                why.append(
                    f"El partido no está igualado: {_leader} tiene ventaja "
                    f"{h_score}-{a_score} a falta de {max(0, 90 - (minute or 0))} min."
                )
                # Distinguir paliza estadística de "stats parejas + marcador claro"
                if strength < 0.18:
                    why.append(
                        f"Las estadísticas son parejas, pero el marcador ya favorece "
                        f"claramente a {_leader}: efectividad por encima del rival."
                    )
                else:
                    why.append(
                        f"{_leader} no solo gana en el marcador, también domina las "
                        f"métricas — control real del partido."
                    )
                why.append(
                    f"{_trailing} tendrá que arriesgar para volver — pueden abrirse espacios."
                )
                # Mercado protegido razonable: Doble Oportunidad del líder o ganador
                # restante / +0.5 si la cuota está castigada.
                suggested_market = (
                    f"Doble Oportunidad — {_leader}"
                    if (minute or 0) < 60
                    else f"Gana {_leader} (resto del partido)"
                )
            elif _ctx == "late_lead":
                mood, icon = "watch", "⏱️"
                action, action_label = "WATCHLIST", "TRAMO FINAL — CUIDADO"
                recommendation = f"⏱️ {_leader} defiende el resultado"
                risk, urgency = "MEDIUM", "medium"
                why.append(f"{_trailing} necesita marcar y queda poco tiempo.")
                why.append("Riesgo de gol desesperado — no entrar al Moneyline del líder a cuota baja.")
            elif pace == "lento_tactico":
                mood, icon = "neutral", "🧊"
                action = "WATCHLIST" if alt_market else "WAIT"
                action_label = "ESPERAR" if not alt_market else "VIGILAR UNDER"
                recommendation = "🧊 Ritmo lento, partido táctico"
                risk, urgency = "LOW", "low"
                why.append("Pocas oportunidades claras, ritmo bajo.")
                why.append("Defensas dominan el partido.")
            elif strength > 0.10 and direction != "none":
                side_label = "local" if direction == "home" else "visitante"
                mood, icon = "watch", "🔥"
                action, action_label = "WATCHLIST", "EN OBSERVACIÓN"
                recommendation = f"🔥 Momentum {side_label}"
                risk, urgency = "MEDIUM", "medium"
                why.append(f"El {side_label} está creciendo en los últimos minutos.")
                why.append("El marcador todavía no refleja ese dominio.")
                # Si el momentum es fuerte y el marcador está abierto,
                # sugerir el mercado ofensivo apropiado.
                _off = _offensive_market_suggestion(h_score, a_score, minute, pace)
                if _off:
                    suggested_market = _off
                    why.append(
                        f"El crecimiento del {side_label} apoya {_off} "
                        f"si la cuota lo justifica."
                    )
            else:
                mood, icon = "neutral", "⚖️"
                action, action_label = "WAIT", "PARTIDO MUY CERRADO"
                recommendation = "⚖️ Partido muy parejo"
                risk, urgency = "MEDIUM", "low"
                why.append("Ningún equipo domina claramente.")
                why.append("Las estadísticas no marcan diferencia suficiente.")
        elif verdict_label == "INSUFFICIENT_DATA":
            mood, icon = "insufficient", "❓"
            action, action_label = "LOW_CONFIDENCE", "DATOS INSUFICIENTES"
            recommendation = "❓ Sin señal — esperar más datos"
            risk, urgency = "HIGH", "low"
            why.append("Faltan estadísticas live para emitir veredicto fiable.")

        # ── Layer in the alt-market suggestion if available ───────────
        # Live-aware: drop alt suggestions whose line is too close to busting
        # given the CURRENT live score (e.g. "Under 2.5" when score is 2-1).
        live_total_now = (analysis.get("score") or {})
        live_total_sum = int(live_total_now.get("home") or 0) + int(live_total_now.get("away") or 0)
        def _is_under_alive(market_label: str, total_sum: int) -> bool:
            """Return False if `Under X.5` is already dead or one goal from death."""
            import re as _re
            if not market_label:
                return False
            m = _re.search(r"under\s*(\d+(?:\.\d+)?)", market_label.lower())
            if not m:
                return True  # not an Under line — let it through
            line_num = float(m.group(1))
            return (line_num - total_sum) >= 1.0

        if alt_market and alt_market.get("state") in ("PROTECTED_MARKET_RECOMMENDED", "UNDER35_WATCHLIST"):
            am = alt_market.get("market") or "Under 3.5"
            am_state = alt_market.get("state")
            if not _is_under_alive(am, live_total_sum):
                # Mathematically (almost) impossible already — do not suggest.
                am = None
                am_state = None
            if am and mood not in ("trap", "value") and am_state == "PROTECTED_MARKET_RECOMMENDED":
                # No trap, no direct value → use protected market as the rec.
                suggested_market = am
                mood = "value" if mood != "trap" else mood
                action = "BET_NOW"
                action_label = f"VALOR EN {am.upper()}"
                recommendation = f"🛡️ {am.upper()} protegido"
                edge_p = float(alt_market.get("edge_pct") or 0.0)
                risk = "LOW" if edge_p >= 4 else "MEDIUM"
                urgency = "medium"
                why.append(f"{am} tiene edge protegido (+{edge_p:.1f}%).")
                if alt_market.get("statsbomb_features"):
                    sb = alt_market["statsbomb_features"]
                    why.append(
                        f"Modelo xG: P({am}) ≈ "
                        f"{sb.get('p_under_3_5' if '3.5' in am else 'p_under_2_5', 0)*100:.0f}% "
                        f"(confianza {sb.get('confidence', 0)}/100)."
                    )
                # Knowledge Base — caso aprendido aplicado a esta línea
                if alt_market.get("applied_learning_rule"):
                    why.append(
                        "📚 Caso aprendido (Pumas-Cruz Azul): partido cerrado + ritmo "
                        "moderado → Under 3.5 protege mejor que Under 2.5."
                    )
            elif am and mood == "neutral":
                # No trap, balanced match, watchlist alt
                suggested_market = am
                why.append(f"{am} podría seguir protegido — vigilar línea.")

    # ── 2. Title + subtitle (replaces "BALANCEADO") ─────────────────────
    title, subtitle = _title_for(
        mood=mood, verdict=verdict_label, pace=pace, direction=direction,
        strength=strength, home_name=home_name, away_name=away_name,
        diff=diff, h_score=h_score, a_score=a_score,
        minute=minute, trap=trap,
    )

    # ── 3. Confidence (0-100) ───────────────────────────────────────────
    # Blend: edge-based (when reeval present), data-density, agreement.
    if reeval and reeval.get("confidence") is not None:
        confidence = int(reeval["confidence"])
    else:
        # base on data density: shots + minute → more confidence
        shots_total = (home.get("shots") or 0) + (away.get("shots") or 0)
        density = min(35, int(shots_total * 1.5))
        time_factor = min(25, int((minute or 0) * 0.35))
        agreement_bonus = 15 if strength > 0.20 else (8 if strength > 0.10 else 0)
        confidence = max(0, min(100, 30 + density + time_factor + agreement_bonus))
        if trap and trap.get("triggered"):
            confidence = max(confidence, 80)  # trap detection is high confidence
        if verdict_label == "INSUFFICIENT_DATA":
            confidence = min(confidence, 25)

    # ── 4. Narration (1-paragraph spoken style) ─────────────────────────
    if not narration_parts:
        narration_parts.append(_compose_narration(
            home_name=home_name, away_name=away_name, h_score=h_score, a_score=a_score,
            minute=minute, mood=mood, direction=direction, strength=strength,
            home=home, away=away, pace=pace, alt_market=alt_market, trap=trap,
        ))
    # Añadir contexto de inferioridad a la narración siempre que haya roja,
    # independientemente de si vino del reeval o del análisis base.
    if num["has_incident"] and num["advantage"] != "none" and short_team:
        n_reds = num["short_side_reds"]
        min_short = num["minutes_short"]
        min_label = f"{min_short} min" if min_short is not None else "varios minutos"
        incident_note = (
            f"{short_team} lleva {min_label} con {11 - n_reds} jugadores "
            f"(expulsión min {num['first_red_minute'] or '?'}). "
            f"La dinámica del partido cambió significativamente."
        )
        # Dedup semántico: si la narración base ya menciona el conteo de
        # jugadores del equipo corto, evitar añadir este note.
        joined = " ".join(narration_parts)
        already_mentions = (
            f"con {11 - n_reds} jugadores" in joined
            or f"{short_team} lleva" in joined
        )
        if not already_mentions:
            narration_parts.append(incident_note)
    narration = " ".join(p for p in narration_parts if p).strip()

    # ── Game-openness guard for TOTAL markets ───────────────────────────
    # The reeval pipeline computes a bilateral live-threat report. If the
    # interpreter is about to recommend an aggressive total (Over 3.5)
    # while only one side is generating xG, the guard either degrades the
    # market to a supported line (Over 2.5 / BTTS) or marks it as not
    # actionable. This is the live-side companion to Phase 33's pregame
    # Over Support layer.
    game_openness = (reeval or {}).get("game_openness") if isinstance(reeval, dict) else None
    unilateral_dominance = (reeval or {}).get("unilateral_dominance") if isinstance(reeval, dict) else None
    if suggested_market:
        # 1) BTTS guard: never recommend BTTS if both teams have already
        #    scored. We use the *current score* as truth — the openness
        #    layer doesn't know who has scored, only who is creating.
        sm_lower = (suggested_market or "").lower()
        is_btts_market = (
            "btts" in sm_lower
            or "ambos marcan" in sm_lower
            or "ambos equipos marcan" in sm_lower
            or "both teams to score" in sm_lower
        )
        try:
            cur_h = int(h_score or 0)
            cur_a = int(a_score or 0)
        except (TypeError, ValueError):
            cur_h, cur_a = 0, 0
        if is_btts_market and cur_h > 0 and cur_a > 0:
            # Already cashed — no new BTTS entry.
            reason_btts = "BTTS ya ocurrió: ambos equipos ya anotaron, mercado cerrado."
            if reason_btts not in why:
                why.insert(0, reason_btts)
            suggested_market = None

        # 2) Strict OVER gates against openness flags. Even if openness
        #    says supports_over_35=False or supports_over_25=False, the
        #    interpreter must NOT surface those markets — UNLESS the
        #    unilateral-dominance profile says one side is crushing the
        #    other with defensive collapse signals (Phase 35 Fix 1.5).
        if game_openness and suggested_market:
            sm_lower = (suggested_market or "").lower()
            is_over_35 = "over 3.5" in sm_lower or "más de 3.5" in sm_lower or "mas de 3.5" in sm_lower
            is_over_25 = "over 2.5" in sm_lower or "más de 2.5" in sm_lower or "mas de 2.5" in sm_lower

            if is_over_35 and not game_openness.get("supports_over_35"):
                # 2a) Unilateral-dominance escape hatch BEFORE we kill the
                # market. If one side is dominating with defensive collapse
                # signals, the Over high is still supported — just via the
                # dominance route. If only dominance (no collapse), degrade
                # to the dominant side's team total instead.
                dom = unilateral_dominance if isinstance(unilateral_dominance, dict) else None
                dom_handled = False
                if dom and dom.get("supports_match_over_high"):
                    # Keep Over 3.5 but switch the reason to dominance + collapse.
                    if dom.get("reason_es") and dom["reason_es"] not in why:
                        why.insert(0, dom["reason_es"])
                    dom_handled = True
                elif dom and dom.get("supports_team_total") and dom.get("dominant_side"):
                    # Degrade to team total of the dominant side.
                    dom_side = dom["dominant_side"]
                    dom_name = home_name if dom_side == "home" else away_name
                    suggested_market = f"Over equipo — {dom_name} (>1.5)"
                    if dom.get("reason_es") and dom["reason_es"] not in why:
                        why.insert(0, dom["reason_es"])
                    dom_handled = True

                if not dom_handled:
                    # No dominance route — fall back to bilateral guard
                    # (degrade to Over 2.5 / BTTS or kill the market).
                    try:
                        from . import game_openness as _go_mod
                        g = _go_mod.guard_total_recommendation(suggested_market, game_openness)
                        if g.get("downgraded") and g.get("market"):
                            if g.get("reason_es") and g["reason_es"] not in why:
                                why.insert(0, g["reason_es"])
                            suggested_market = g["market"]
                        else:
                            if g.get("reason_es") and g["reason_es"] not in why:
                                why.insert(0, g["reason_es"])
                            suggested_market = None
                    except Exception:
                        if game_openness.get("reason_es") and game_openness["reason_es"] not in why:
                            why.insert(0, game_openness["reason_es"])
                        suggested_market = None

            elif is_over_25 and not game_openness.get("supports_over_25"):
                if game_openness.get("reason_es") and game_openness["reason_es"] not in why:
                    why.insert(0, game_openness["reason_es"])
                suggested_market = None

            else:
                # Non-aggressive total or supported — run the regular guard
                # for back-compat (it can still mark as not_actionable).
                try:
                    from . import game_openness as _go_mod
                    guard = _go_mod.guard_total_recommendation(suggested_market, game_openness)
                    if guard.get("downgraded"):
                        suggested_market = guard["market"]
                        reason_text = guard.get("reason_es") or ""
                        if reason_text and reason_text not in why:
                            why.insert(0, reason_text)
                    elif guard.get("not_actionable"):
                        reason_text = guard.get("reason_es") or ""
                        if reason_text and reason_text not in why:
                            why.insert(0, reason_text)
                        suggested_market = None
                except Exception:
                    pass

    # ── Phase 39 / Fix 2 — Friendly Internationals DNB preference ─────
    # Hard rule (always evaluated). When the match is an international
    # friendly with a clear favorite and the Moneyline premium over DNB
    # is too small to justify the extra risk, override the suggested
    # market with the DNB protection. The warehouse-learned pattern
    # (≥60 samples) amplifies or dampens this decision.
    friendly_dnb_decision = None
    try:
        from . import friendly_dnb_rule as _fdnb

        # Pull the pre-match odds (1X2 + DNB) from the match doc. The
        # schema varies slightly across ingestion paths so we try several
        # well-known fields and fall back to None on any miss.
        odds_book = match.get("odds") or {}
        oh = (odds_book.get("home") or odds_book.get("1") or
              odds_book.get("home_odds") or odds_book.get("ml_home"))
        od = (odds_book.get("draw") or odds_book.get("X") or
              odds_book.get("draw_odds") or odds_book.get("ml_draw"))
        oa = (odds_book.get("away") or odds_book.get("2") or
              odds_book.get("away_odds") or odds_book.get("ml_away"))
        dnb_h = (odds_book.get("dnb_home") or odds_book.get("dnb_1") or
                  (odds_book.get("dnb") or {}).get("home")
                  if isinstance(odds_book.get("dnb"), dict) else None)
        dnb_a = (odds_book.get("dnb_away") or odds_book.get("dnb_2") or
                  (odds_book.get("dnb") or {}).get("away")
                  if isinstance(odds_book.get("dnb"), dict) else None)

        # Optional learned pattern from the warehouse — passed via the
        # match doc by analyst_engine when it pre-fetched the row.
        learned = (match.get("learned_patterns") or {}).get(
            _fdnb.PATTERN_NAME
        )

        friendly_dnb_decision = _fdnb.evaluate_friendly_dnb_preference(
            match=match,
            odds_home=oh, odds_draw=od, odds_away=oa,
            odds_dnb_home=dnb_h, odds_dnb_away=dnb_a,
            learned_pattern=learned,
        )

        if friendly_dnb_decision.get("applies"):
            fav = friendly_dnb_decision.get("favorite")
            adv_team = home_name if fav == "home" else away_name
            new_market = f"Draw No Bet — {adv_team}"
            # Only override when the previous suggestion was the raw ML
            # on the favorite — never override Over/Under/BTTS picks.
            sm_lower = (suggested_market or "").lower()
            is_ml_pick = (
                ("moneyline" in sm_lower)
                or ("resultado final" in sm_lower)
                or (sm_lower == "" and action in ("LIVE_ENTRY", "WAIT", "WATCHLIST"))
                or (adv_team and adv_team.lower() in sm_lower
                    and "dnb" not in sm_lower and "no bet" not in sm_lower)
            )
            if is_ml_pick:
                suggested_market = new_market
                # Surface the reason as the TOP why so the UI badge reads it.
                reason_es = friendly_dnb_decision.get("summary") or (
                    f"Amistoso internacional con favorito {adv_team}: "
                    "DNB protege contra rotación y volatilidad táctica."
                )
                if reason_es not in why:
                    why.insert(0, reason_es)
                # Bump confidence slightly — DNB is statistically safer.
                try:
                    confidence = max(int(confidence), 60)
                except Exception:
                    pass
    except Exception as _exc_fdnb:
        log.debug("friendly_dnb_rule failed: %s", _exc_fdnb)

    return {
        "title":            title,
        "subtitle":         subtitle,
        "mood":             mood,
        "icon":             icon,
        "action":           action,
        "action_label":     action_label,
        "recommendation":   recommendation,
        "suggested_market": suggested_market,
        "confidence":       int(confidence),
        "risk":             risk,
        "urgency":          urgency,
        "why":              why[:4],
        "narration":        narration,
        "trap":             trap,
        # ── Expose openness + dominance so the UI can render chips ──
        "game_openness":    game_openness,
        "unilateral_dominance": unilateral_dominance,
        # ── Phase 39 / Fix 2 — Friendly DNB decision (UI badge) ──
        "friendly_dnb":     friendly_dnb_decision,
        # ── P1 fix: structured scoreboard context for the UI badges ──
        # This lets the LiveCopilotCard render badges like "Ventaja clara"
        # / "Control por marcador" without re-deriving the state.
        "scoreboard_context": _build_scoreboard_context(
            home_name=home_name, away_name=away_name,
            h_score=h_score, a_score=a_score, minute=minute,
            strength=strength, verdict_label=verdict_label,
        ),
        "_source":          "human_live_interpreter_v1",
    }


# ─── Title / subtitle ───────────────────────────────────────────────────────

def _title_for(*, mood, verdict, pace, direction, strength,
               home_name, away_name, diff, h_score=None, a_score=None,
               minute, trap) -> tuple[str, str]:
    """Replace cold labels with human framings, in ES.

    Priority order:
      1. Trap detected (market mispricing)
      2. Scoreline context (blowout / commanding / clear_lead / late_lead) — ANTES que pace
      3. Value/Push (xG momentum)
      4. Pace (tactical rhythm)
      5. Strength + direction fallback
    """
    leader   = home_name if diff > 0 else away_name
    trailing = away_name if diff > 0 else home_name
    # When real scoreline is not provided, fall back to a diff-only render
    # (preserves backwards compatibility but loses absolute numbers).
    if h_score is None or a_score is None:
        h_score = max(0, (diff if diff > 0 else 0))
        a_score = max(0, (-diff if diff < 0 else 0))
    ctx = _scoreline_context(diff, h_score, a_score, minute)

    # 1. Trap always wins.
    if trap and trap.get("triggered"):
        trap_leader = home_name if trap.get("leader_side") == "home" else away_name
        trap_chaser = away_name if trap.get("leader_side") == "home" else home_name
        return (
            "⚠️ Trampa de mercado",
            f"{trap_leader} gana, pero {trap_chaser} domina las estadísticas y el momentum.",
        )

    # 2. Scoreline context — overrides pace when the result is effectively decided.
    if ctx == "blowout":
        return (
            f"🔴 {leader} sentencia el partido",
            f"Con {abs(diff)} goles de ventaja, el resultado está prácticamente definido.",
        )
    if ctx == "commanding":
        return (
            f"📊 {leader} controla con autoridad",
            f"Ventaja de {abs(diff)} goles desde la hora de juego — difícil de remontar.",
        )
    if ctx == "clear_lead":
        # P1 fix: 0-2 al descanso debe leerse como ventaja clara, no como
        # "partido equilibrado". El marcador pesa más que las métricas.
        return (
            f"🎯 Ventaja clara para {leader}",
            f"{leader} gana {h_score}-{a_score} — el partido no está parejo aunque las "
            f"métricas lo parezcan.",
        )
    if ctx == "late_lead":
        return (
            f"⏱️ {leader} defiende en el tramo final",
            f"{trailing} necesita al menos {abs(diff)} gol{'es' if abs(diff) > 1 else ''} "
            f"con poco tiempo restante.",
        )

    # 3. Value/Push from xG verdict.
    if verdict == "LIVE_VALUE_PUSH" or (mood == "value" and direction != "none"):
        side_team = home_name if direction == "home" else away_name
        return (
            f"🔥 Momentum {side_team}",
            f"{side_team} está creciendo y generando peligro en los últimos minutos.",
        )

    # 4. Tactical pace (only relevant when scoreline is still open).
    if pace == "lento_tactico":
        if ctx == "one_goal_early":
            return (
                f"🧊 {leader} gana en partido cerrado",
                "Ritmo contenido con un gol de diferencia — partido abierto todavía.",
            )
        return (
            "🧊 Ritmo lento",
            "El partido sigue táctico, con pocas oportunidades claras.",
        )
    if pace == "abierto":
        return (
            "🔥 Partido abierto",
            "Mucho ida y vuelta, pocas opciones de Under agresivo.",
        )

    # 5. Strength + direction fallback.
    if diff == 0 and strength < 0.10:
        return (
            "⚖️ Partido muy cerrado",
            "Ningún equipo domina; los datos no marcan diferencia.",
        )
    if diff != 0 and strength < 0.10:
        side_team = home_name if diff > 0 else away_name
        return (
            f"⚖️ {side_team} gana, pero no domina",
            "El marcador no refleja diferencias estadísticas claras.",
        )
    return (
        "⚖️ Partido equilibrado",
        "Sin señal direccional clara todavía.",
    )


# ─── Narration ──────────────────────────────────────────────────────────────

def _compose_narration(*, home_name, away_name, h_score, a_score, minute,
                       mood, direction, strength, home, away, pace,
                       alt_market, trap) -> str:
    """One-paragraph 'Razón:' text in coach voice.

    Scoreline context takes priority over pace so a 3-0 match never reads
    the same as a 0-0 one, regardless of xG or shot volume.
    """
    diff = h_score - a_score
    leader   = home_name if diff > 0 else away_name
    trailing = away_name if diff > 0 else home_name
    ctx = _scoreline_context(diff, h_score, a_score, minute)

    parts: list[str] = []
    if minute is not None:
        parts.append(f"Al minuto {minute}, {home_name} {h_score}-{a_score} {away_name}.")

    # Trap always overrides everything else.
    if trap and trap.get("triggered"):
        parts.append(
            "La cuota del favorito ya perdió valor: el rival está más cerca del empate que de defender."
        )
        return " ".join(parts)

    # ── Scoreline-driven narrations (high priority) ──────────────────────
    if ctx == "blowout":
        parts.append(
            f"{leader} ha sentenciado el partido con {abs(diff)} goles de ventaja. "
            f"No hay valor live en apostar al resultado — considera mercados de goles restantes "
            f"o simplemente evita este partido."
        )
        return " ".join(parts)

    if ctx == "commanding":
        parts.append(
            f"{leader} controla con {abs(diff)} goles de ventaja desde la hora de juego. "
            f"La remontada es estadísticamente improbable. "
            f"Under de goles restantes podría tener valor si la cuota lo justifica."
        )
        return " ".join(parts)

    if ctx == "clear_lead":
        # P1 fix (2026-05-28): el marcador 0-2 al descanso es una señal por
        # sí misma, aunque las stats estén equilibradas. Nunca decir "sin
        # señal direccional" cuando hay ventaja de 2 goles.
        parts.append(
            f"{leader} no domina todas las métricas, pero ya tiene una ventaja fuerte "
            f"en el marcador. El partido no está parejo: {trailing} tendrá que arriesgar "
            f"y eso puede generar más espacios para {leader}."
        )
        return " ".join(parts)

    if ctx == "late_lead":
        parts.append(
            f"{leader} gestiona el resultado en el tramo final. "
            f"{trailing} necesita marcar pero el tiempo se agota. "
            f"Riesgo real de gol desesperado del perdedor — evitar Moneyline del líder a cuota baja."
        )
        return " ".join(parts)

    # ── Standard narrations when scoreline is still open ─────────────────
    if mood == "value":
        side_team = home_name if direction == "home" else away_name
        parts.append(
            f"{side_team} está empujando con más xG live "
            f"({(home if direction=='home' else away).get('xg_live', 0):.2f} vs "
            f"{(away if direction=='home' else home).get('xg_live', 0):.2f}) "
            f"y más presión por minuto."
        )
        if alt_market:
            am = alt_market.get("market") or "Under 3.5"
            parts.append(f"Si prefieres una jugada protegida, {am} sigue siendo razonable.")
    elif mood == "watch":
        side_team = home_name if direction == "home" else away_name
        parts.append(
            f"El {side_team} está creciendo, pero el mercado todavía no ha movido "
            f"la línea suficiente para entrar."
        )
    elif mood == "neutral":
        if ctx == "one_goal_early":
            parts.append(
                "Un gol de diferencia con tiempo de sobra — el partido sigue vivo. "
                "Las estadísticas no muestran dominio claro de ningún lado todavía."
            )
        elif pace == "lento_tactico":
            parts.append(
                "Ritmo bajo, defensas dominando. Under es un perfil natural "
                "pero la cuota tiene que justificarlo."
            )
        else:
            parts.append("Sin señal direccional. Datos parejos en ambos lados.")
    elif mood == "insufficient":
        parts.append("Todavía no hay suficientes estadísticas live para emitir veredicto.")

    return " ".join(parts)


__all__ = ["interpret_live"]
