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


def _scoreline_context(diff: int, h_score: int, a_score: int, minute: Optional[int]) -> str:
    """Classify the match state based on scoreline + time.

    Returns a context key that takes priority over pace in title/narration:
      'blowout'        — diff >= 3 (partido sentenciado)
      'commanding'     — diff == 2 con min >= 60 (ventaja consolidada)
      'late_lead'      — diff == 1 con min >= 75 (líder defendiendo)
      'one_goal_early' — diff == 1 con min < 60 (partido vivo)
      'level'          — diff == 0 (empatado)
    """
    m = minute or 0
    abs_diff = abs(diff)
    if abs_diff >= 3:
        return "blowout"
    if abs_diff == 2 and m >= 60:
        return "commanding"
    if abs_diff == 1 and m >= 75:
        return "late_lead"
    if abs_diff == 1:
        return "one_goal_early"
    return "level"


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

def interpret_live(
    match: dict,
    *,
    analysis: dict | None,
    reeval: dict | None = None,
    alt_market: dict | None = None,
) -> dict:
    """Build the copilot-style payload.

    Args:
        match: hydrated match doc (has live_stats, odds_snapshots, h2h).
        analysis: output of `live_xg_proxy.compute_live_analysis(match)`.
        reeval: optional output of `live_reevaluation.reevaluate_match(match, ...)`.
        alt_market: optional output of `under_market_scan.scan_protected_alternatives(match)`.

    Returns: dict ready to ship to the UI (`LiveCopilotCard`).
    """
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

    else:
        # ── No manual reeval yet → use analysis verdict + alt market ──
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
            # Live-aware Over suggestion: pick the Over line that still has
            # at least 1 goal of headroom (i.e. live total < line - 0.5).
            cur_total = h_score + a_score
            if cur_total == 0:
                suggested_market = "Over 1.5"
            elif cur_total <= 1:
                suggested_market = "Over 2.5"
            elif cur_total <= 2:
                suggested_market = "Over 3.5"
            else:
                # Match is already high-scoring (3+ goals) — Over 2.5 / 3.5
                # already cashed or one tap away. Drop direct Over suggestion.
                suggested_market = None
            risk = "MEDIUM"
            urgency = "high"
            why.append(
                f"{home_name if side=='home' else away_name} genera más xG live "
                f"({home.get('xg_live',0):.2f} vs {away.get('xg_live',0):.2f})."
            )
            why.append(
                f"Presión {side_label}: {(home if side=='home' else away).get('pressure_rate',0):.2f}/min "
                f"vs {(away if side=='home' else home).get('pressure_rate',0):.2f}/min."
            )
            if pace == "abierto":
                why.append("El partido está abierto: muchos tiros y oportunidades.")
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
        diff=diff, minute=minute, trap=trap,
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
    narration = " ".join(p for p in narration_parts if p).strip()

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
        "_source":          "human_live_interpreter_v1",
    }


# ─── Title / subtitle ───────────────────────────────────────────────────────

def _title_for(*, mood, verdict, pace, direction, strength,
               home_name, away_name, diff, minute, trap) -> tuple[str, str]:
    """Replace cold labels with human framings, in ES.

    Priority order:
      1. Trap detected (market mispricing)
      2. Scoreline context (blowout / commanding / late lead) — ANTES que pace
      3. Value/Push (xG momentum)
      4. Pace (tactical rhythm)
      5. Strength + direction fallback
    """
    leader   = home_name if diff > 0 else away_name
    trailing = away_name if diff > 0 else home_name
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
