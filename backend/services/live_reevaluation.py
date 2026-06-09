"""Live Re-Evaluation Engine — Phase 10 (football).

A match dropped pre-match for "no value in 1X2/DC" can become live-valuable
when the scoreboard, possession or momentum shift. This module decides if
the current live state opens a window worth a stake, using either:

  • PRE-MATCH odds as approximation (free path — `e1` in product spec), OR
  • USER-PROVIDED `manual_odds` from their bookie (gold path — `e2`)

The latter is the cheap-but-precise wedge: ESPN gives us live score + minute
for free, the user pastes the cuota they actually see, we compute the edge
with mathematical precision.

Decision states (caller surfaces them verbatim to the UI):
    NO_LIVE_VALUE          → edge ≤ 0; do nothing
    WATCHLIST              → small edge or thin sample; keep watching
    LIVE_VALUE_WINDOW      → clean edge ≥ threshold; bet now
    MOMENTUM_SHIFT         → momentum strongly against current line; act fast
    MARKET_OVERREACTION    → line moved past fair value after goal/red card
    CASH_OUT_RECOMMENDED   → pre-match pick still alive but live equity rich
    HOLD_RECOMMENDED       → keep the pick, current odds undervalue it

Pure-Python module; no IO. The HTTP endpoint in server.py is the only
caller that does ESPN refresh + DB writes.
"""
from __future__ import annotations

import math
from typing import Optional


EDGE_VALUE_WINDOW = 0.04   # 4% edge → LIVE_VALUE_WINDOW
EDGE_WATCHLIST    = 0.015  # 1.5% edge → WATCHLIST
EDGE_OVERREACTION = 0.08   # 8% edge after a momentum-defining event


# ─── Probability adjustments ────────────────────────────────────────────────

def _remaining_share(minute: Optional[int], total: int = 90) -> float:
    """Fraction of regulation time remaining (0..1). Clamped at 0.02 to avoid
    division-by-zero artefacts during stoppage time. 90 minutes is the
    football regulation; the caller can pass a different `total` for OT etc.
    """
    if minute is None:
        return 1.0
    m = max(0, min(total, int(minute)))
    return max(0.02, (total - m) / total)


def _poisson_under_remaining(current_total: int, line: float, remaining_share: float, expected_goals_total: float) -> float:
    """Probability that final total goals < `line` given goals already scored.

    Uses a simple Poisson with rate scaled by remaining share. `expected_goals_total`
    is the pre-match xG total (~2.5 default for football). The probability of K
    additional goals follows Poisson(λ = remaining_share × expected_goals_total).

    Math: Under 2.5 wins iff final_total ≤ 2 ⇔ additional ≤ 2 - current_total.
    """
    lam = max(0.05, remaining_share * expected_goals_total)
    max_additional = math.floor(line) - current_total  # we allow up to this many more goals
    if max_additional < 0:
        return 0.0  # already exceeded the line
    cum = 0.0
    for k in range(0, max_additional + 1):
        cum += (lam ** k) * math.exp(-lam) / math.factorial(k)
    return min(1.0, max(0.0, cum))


def _poisson_over_remaining(current_total: int, line: float, remaining_share: float, expected_goals_total: float) -> float:
    return 1.0 - _poisson_under_remaining(current_total, line, remaining_share, expected_goals_total)


# ─── Momentum & event detection ─────────────────────────────────────────────

def _momentum_score(home_stats: dict, away_stats: dict, score_diff: int) -> int:
    """0-100 momentum favouring the trailing/equal side.

    Negative score means visiting team has momentum; positive means home.
    UPGRADED (P3): now uses `live_xg_proxy` (kloppy/socceraction/soccer_xg
    inspired) so shots-in-box, blocked shots, corners, and dangerous attacks
    all enter the momentum equation — not just SOT + dangerous + possession.
    """
    from . import live_xg_proxy as lxp
    home_side = lxp.extract_side(home_stats)
    away_side = lxp.extract_side(away_stats)
    # threat_index already blends possession + dangerous + attacks + corners + SOT
    # and xg_live captures shot-quality realised so far. Combine both.
    h_idx = home_side.threat_index + home_side.xg_live * 25.0
    a_idx = away_side.threat_index + away_side.xg_live * 25.0
    total = max(1.0, h_idx + a_idx)
    delta = h_idx - a_idx
    score = min(100.0, abs(delta) / total * 100.0)
    sign = 1 if delta > 0 else -1 if delta < 0 else 0
    return int(round(score)) * sign


# ─── Main API ───────────────────────────────────────────────────────────────

def reevaluate_match(
    match: dict,
    *,
    manual_odds: Optional[float] = None,
    manual_market: Optional[str] = None,
    expected_goals_total: float = 2.5,
) -> dict:
    """Compute a Live Re-Evaluation result for a football OR basketball match.

    Football (default) uses the Poisson/xG model. Basketball dispatches
    to `_reevaluate_basketball()` which uses pace + projected total +
    blowout-trap detection instead of xG.

    Args:
        match: hydrated match doc with `live_stats` (minute, score) and
            `odds_snapshots` (pre-match) and optionally `h2h_recent`.
        manual_odds: user-pasted decimal odds from their bookie for the
            market they're considering. WHEN PRESENT this is treated as the
            authoritative implied probability.
        manual_market: short label of the manual market. Football examples:
            "Under 2.5", "Over 1.5", "Resultado Final: home". Basketball
            examples: "Money Line: home", "Total: Over 215.5", "Spread: home -4.5".
        expected_goals_total: prior on final-game xG total. Football default 2.5.

    Returns:
        Sport-specific result (same envelope).
    """
    sport = (match.get("sport") or "football").lower()
    if sport == "basketball":
        return _reevaluate_basketball(match, manual_odds=manual_odds, manual_market=manual_market)
    if sport == "baseball":
        return _reevaluate_baseball(match, manual_odds=manual_odds, manual_market=manual_market)
    return _reevaluate_football(match, manual_odds=manual_odds, manual_market=manual_market,
                                 expected_goals_total=expected_goals_total)


def _reevaluate_football(
    match: dict,
    *,
    manual_odds: Optional[float] = None,
    manual_market: Optional[str] = None,
    expected_goals_total: float = 2.5,
) -> dict:
    """Original football re-evaluation logic — kept verbatim under a new name
    so basketball dispatch is non-invasive.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    live = match.get("live_stats") or {}
    minute = live.get("minute")
    score = live.get("score") or {}
    home_score = int(score.get("home") or 0)
    away_score = int(score.get("away") or 0)
    current_total = home_score + away_score
    score_diff = home_score - away_score
    home_stats = live.get("home_stats") or {}
    away_stats = live.get("away_stats") or {}
    is_live = bool(live)

    momentum = _momentum_score(home_stats, away_stats, score_diff)
    remaining = _remaining_share(minute)

    # ── P3: Full live analysis (kloppy/socceraction/soccer_xg inspired) ──
    # Computes xG live, threat_index, pressure_rate per side, plus the
    # late-lead trap detector. Attached to the response so the UI can show
    # the full picture even when no live edge is found.
    from . import live_xg_proxy as lxp
    live_analysis = lxp.compute_live_analysis(match)
    trap = live_analysis.get("trap") if isinstance(live_analysis, dict) else None

    # Decide which market we're evaluating.
    market = manual_market or _infer_default_market(current_total, minute, expected_goals_total)
    selection = market

    # Compute estimated probability for the chosen market.
    est_prob, est_basis = _estimate_probability(market, match, current_total, score_diff, remaining, expected_goals_total)

    # ── P4.1 bug-fix: dead-line short-circuit ──────────────────────────
    # If the user picks Under X.5 (or our infer picked it) but `current_total`
    # already meets/exceeds X, the line is mathematically lost. Returning a
    # noisy negative-edge math response is confusing UX; instead surface a
    # crystal-clear "línea muerta" verdict so the UI's copilot card narrates
    # what's actually going on.
    import re as _re_dead
    _m_dead = _re_dead.search(r"under\s*(\d+(?:\.\d+)?)", (market or "").lower())
    if _m_dead:
        _line_dead = float(_m_dead.group(1))
        if current_total >= _line_dead:
            return _build_response(
                match_id=match.get("match_id"),
                live_state="LINE_DEAD",
                recommended_action="PASS",
                market=market, selection=selection,
                estimated_probability=0.0,
                implied_probability=(1.0 / float(manual_odds)) if (manual_odds and manual_odds > 1.01) else None,
                decimal_odds=float(manual_odds) if (manual_odds and manual_odds > 1.01) else None,
                edge=-1.0,
                confidence=0,
                risk_level="HIGH",
                reason=(
                    f"{market} ya no es posible: el marcador actual ({current_total} goles) "
                    f"iguala o supera la línea. Considera otro mercado."
                ),
                live_snapshot={"minute": minute, "score": score, "momentum": momentum, "is_live": is_live},
                manual_odds_used=bool(manual_odds),
                computed_at=now,
                live_analysis=live_analysis,
                trap=trap,
            )

    # Resolve implied probability: manual_odds wins, then live, then pre-match.
    implied_source = "pre_match"
    if manual_odds and manual_odds > 1.01:
        implied = 1.0 / float(manual_odds)
        decimal_odds = float(manual_odds)
        implied_source = "manual"
    else:
        decimal_odds, implied = _best_pre_match_quote(match, market)
        if implied is None:
            return _build_response(
                match_id=match.get("match_id"),
                live_state="NO_LIVE_VALUE",
                recommended_action="PASS",
                market=market, selection=selection,
                estimated_probability=est_prob,
                implied_probability=None,
                decimal_odds=None,
                edge=None,
                confidence=0,
                risk_level="HIGH",
                reason="Sin cuota disponible (ni pre-match ni manual). Ingresa la cuota de tu bookie para reevaluar.",
                live_snapshot={"minute": minute, "score": score, "momentum": momentum, "is_live": is_live},
                manual_odds_used=False,
                computed_at=now,
            )

    edge = est_prob - implied
    # ── Trap gate: when the late-lead-low-odds-pressing-rival rule fires
    # we override the classify() output with a hard PASS / TRAP_DETECTED
    # state, regardless of how positive the math edge looks. The user
    # spec is explicit: "favorito ganando tarde + cuota muy baja + rival
    # presionando = NO APOSTAR".
    trap_overrides_leader_bet = (
        trap and trap.get("triggered")
        and market and (
            market.lower().startswith("resultado final: home") and trap["leader_side"] == "home"
            or market.lower().startswith("resultado final: away") and trap["leader_side"] == "away"
            or market.lower() in ("home", "away") and trap["leader_side"] == market.lower()
        )
    )
    if trap_overrides_leader_bet:
        state, action, risk = "TRAP_DETECTED", "PASS", "HIGH"
        reason = trap["reason_es"]
        confidence = 0
    else:
        state, action, risk, reason = _classify(
            edge=edge, est_prob=est_prob, implied=implied, momentum=momentum,
            minute=minute, remaining=remaining, current_total=current_total,
            market=market, is_live=is_live, manual_odds_used=(implied_source == "manual"),
            est_basis=est_basis,
        )
        confidence = _confidence_score(edge, momentum, remaining, est_basis)
        # When the trap fires for a NON-leader bet (e.g. user is on Over)
        # we keep their action but tag the response so the UI can show a
        # secondary warning.
        if trap and trap.get("triggered"):
            reason = (
                f"{reason}  ⚠ {trap['reason_es']}"
                if reason else trap["reason_es"]
            )

    # ── Interpreter: traduce métricas a voz de entrenador para LiveCopilotCard.
    # Siempre se llama DESPUÉS de clasificar el estado, para que el interpreter
    # reciba el reeval completo (edge, state, recommended_action) y actualice
    # el copilot card con la recomendación basada en la cuota del usuario.
    # ── Game openness: bilateral live-threat for TOTAL markets ──────────
    # The momentum score is directional (who wins). For Over/BTTS we need
    # to know whether BOTH sides are threatening. Computed fail-soft and
    # passed to the interpreter so it can pick the right total line instead
    # of over-reaching to Over 3.5 on one-sided xG (France 1-2 IVC bug).
    openness = None
    unilateral_dominance = None
    try:
        from . import game_openness as _go
        openness = _go.compute_game_openness(
            home_stats, away_stats,
            minute=minute, current_total=current_total,
        )
        # Unilateral-dominance profile: complements bilateral openness
        # for cases where one side crushes the other with defensive
        # collapse signals (e.g. Mexico 5-1 Serbia). The interpreter
        # uses this to surface a *team total* instead of Over 3.5 when
        # openness says is_one_sided=True.
        unilateral_dominance = _go.compute_unilateral_dominance_over_profile(
            home_stats, away_stats,
            match_context={
                "minute":         minute,
                "score_diff":     abs(score_diff),
                "current_total":  current_total,
            },
        )
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").debug("game_openness failed: %s", _exc)
        openness = None
        unilateral_dominance = None

    reeval_for_interpreter = {
        "live_state":           state,
        "recommended_action":   action,
        "market":               market,
        "edge":                 edge,
        "edge_pct":             round((edge or 0) * 100, 2) if edge is not None else None,
        "confidence":           confidence,
        "estimated_probability": round(est_prob or 0, 4),
        "implied_probability":  round(implied or 0, 4) if implied is not None else None,
        "decimal_odds":         decimal_odds,
        "manual_odds_used":     (implied_source == "manual"),
        "reason":               reason,
        "game_openness":        openness,
        "unilateral_dominance": unilateral_dominance,
    }
    try:
        from . import human_live_interpreter as hli
        interpreter = hli.interpret_live(
            match,
            analysis=live_analysis,
            reeval=reeval_for_interpreter,
        )
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").warning("interpret_live failed: %s", _exc)
        interpreter = None

    # ── Football Moneyball — Live vs Pregame comparison (fail-soft) ──
    # Pure (no IO): builds a live snapshot from the current match doc and
    # compares it against the pregame snapshot we already attach in
    # analyst_engine (`football_pregame_snapshot` on the match) or one
    # we build on-the-fly here. Never raises.
    fb_live_vs_pregame = None
    try:
        from .football_moneyball import (
            build_pregame_snapshot as _fb_build_pregame,
            build_live_snapshot as _fb_build_live,
            compare_live_vs_pregame as _fb_compare,
        )
        pregame_snap = (
            match.get("football_pregame_snapshot")
            or _fb_build_pregame(match)
        )
        live_snap = _fb_build_live(match)
        fb_live_vs_pregame = _fb_compare(pregame_snap, live_snap)
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").debug(
            "football_live_vs_pregame failed: %s", _exc,
        )
        fb_live_vs_pregame = None

    # ── Phase 45 — Football Siege Pressure Guard (fail-soft) ─────────
    # Prevent the engine from recommending Under purely because the
    # score is low late in a match where one team is in a siege.
    siege = None
    try:
        from . import football_siege_pressure_guard as fspg
        siege = fspg.evaluate_siege_pressure(
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            home_stats=home_stats,
            away_stats=away_stats,
            market=market,
        )
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").debug(
            "football_siege_pressure_guard failed: %s", _exc,
        )
        siege = None

    siege_blocks_under = bool(
        siege and siege.get("verdict") == "BLOCK_UNDER"
        and (market or "").lower().startswith("under")
    )
    siege_downgrades_under_3 = bool(
        siege and siege.get("verdict") == "DOWNGRADE_UNDER_3_5"
        and (market or "").lower().startswith("under")
    )

    if siege_blocks_under:
        # Hard PASS on Under when the siege guard fires for that market.
        # We preserve the math response inside ``live_analysis`` for
        # transparency but the headline verdict becomes a clear block.
        state, action, risk = "SIEGE_PRESSURE_HIGH", "PASS", "HIGH"
        confidence = 0
        reason = (
            siege.get("ui_message_es")
            or "Asedio sostenido del equipo dominante: el Under es de alto riesgo aquí."
        )
    elif siege_downgrades_under_3 and confidence > 35:
        # Cap confidence; do not flip the verdict — let the user see
        # the original math but with the protective ceiling.
        confidence = 35
        reason = (reason or "") + "  ⚠ Asedio sostenido — confianza limitada."

    return _build_response(
        match_id=match.get("match_id"),
        live_state=state,
        recommended_action=action,
        market=market, selection=selection,
        estimated_probability=est_prob,
        implied_probability=implied,
        decimal_odds=decimal_odds,
        edge=edge,
        confidence=confidence,
        risk_level=risk,
        reason=reason,
        live_snapshot={"minute": minute, "score": score, "momentum": momentum, "is_live": is_live},
        manual_odds_used=(implied_source == "manual"),
        computed_at=now,
        live_analysis=live_analysis,
        trap=trap,
        interpreter=interpreter,
        football_live_vs_pregame=fb_live_vs_pregame,
        game_openness=openness,
        unilateral_dominance=unilateral_dominance,
        siege_pressure=siege,
    )


def _infer_default_market(current_total: int, minute: Optional[int], xg_total: float) -> str:
    """When user hasn't specified, pick the most-informative market for the
    live state. Heuristic: if 0-0 past minute 50, Under 1.5 / Under 2.5 are
    in their value window; if Over 1.5 already hit and minute < 30, Over 2.5;
    otherwise Under 2.5 is a sensible default for value analysis."""
    if minute and minute >= 50 and current_total == 0:
        return "Under 1.5"
    if current_total == 1 and minute and minute < 30:
        return "Over 2.5"
    return "Under 2.5"


def _estimate_probability(
    market: str, match: dict, current_total: int, score_diff: int,
    remaining: float, xg_total: float,
) -> tuple[float, str]:
    """Return (estimated_probability, basis_label).

    basis_label tells the UI which model produced the number — used both for
    transparency and the confidence calculation (Poisson-over-live-state
    deserves higher confidence than h2h-only proxies).
    """
    m = market.strip().lower()
    if m.startswith("under"):
        try:
            line = float(m.replace("under", "").strip())
        except ValueError:
            line = 2.5
        return _poisson_under_remaining(current_total, line, remaining, xg_total), "poisson_live"
    if m.startswith("over"):
        try:
            line = float(m.replace("over", "").strip())
        except ValueError:
            line = 2.5
        return _poisson_over_remaining(current_total, line, remaining, xg_total), "poisson_live"
    # 1X2 / DC: use a logistic of (score_diff + xG residual). Coarse but
    # honest — the user typically uses Under/Over for live anyway.
    pre_p = _pre_match_implied_for_market(match, market) or 0.5
    # Live-adjust: each goal lead is worth ~+15 percentage points; momentum
    # shrinks/grows the lead's value.
    z = pre_p + 0.15 * score_diff + 0.05 * (remaining - 0.5)
    return max(0.02, min(0.98, z)), "logistic_live"


def _pre_match_implied_for_market(match: dict, market: str) -> Optional[float]:
    """Heuristic: pull pre-match implied prob for non-totals markets."""
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    if "1X2" in market.lower() or "resultado" in market.lower():
        rows = markets.get("1X2") or []
        odds = []
        for r in rows:
            v = r.get("home")
            if isinstance(v, (int, float)) and v > 1.01:
                odds.append(float(v))
        if odds:
            return 1.0 / (sum(odds) / len(odds))
    return None


def _best_pre_match_quote(match: dict, market: str) -> tuple[Optional[float], Optional[float]]:
    """Best (highest) decimal odds + implied prob for a given market label.

    Implements just enough to cover Under 1.5/2.5/3.5, Over 1.5/2.5/3.5, and
    1X2 'home'/'draw'/'away'. Returns (None, None) when nothing matches —
    caller then asks the user for manual odds.
    """
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None, None
    markets = (snaps[-1] or {}).get("markets") or {}
    m = market.strip()
    m_low = m.lower()

    if m_low.startswith("under") or m_low.startswith("over"):
        rows = markets.get("Over/Under") or []
        best = None
        for r in rows:
            v = (r.get("lines") or {}).get(m)
            if isinstance(v, (int, float)) and v > 1.01:
                best = max(best, float(v)) if best else float(v)
        if best:
            return best, 1.0 / best
        return None, None

    if "resultado" in m_low or "1x2" in m_low:
        rows = markets.get("1X2") or []
        key = "home"
        if "away" in m_low or "visit" in m_low:
            key = "away"
        elif "draw" in m_low or "empate" in m_low:
            key = "draw"
        vals = []
        for r in rows:
            v = r.get(key)
            if isinstance(v, (int, float)) and v > 1.01:
                vals.append(float(v))
        if vals:
            best = max(vals)
            return best, 1.0 / best
    return None, None


def _classify(
    *, edge: float, est_prob: float, implied: float, momentum: int,
    minute: Optional[int], remaining: float, current_total: int,
    market: str, is_live: bool, manual_odds_used: bool, est_basis: str,
) -> tuple[str, str, str, str]:
    """Return (state, recommended_action, risk_level, reason)."""
    # Not live and no manual override → can't reevaluate meaningfully.
    if not is_live and not manual_odds_used:
        return ("NO_LIVE_VALUE", "PASS", "HIGH",
                "El partido aún no está en vivo. Reevaluar tendrá sentido cuando empiece o si ingresas una cuota manual.")

    # Live with strong negative edge.
    if edge is None or edge <= -EDGE_OVERREACTION:
        return ("NO_LIVE_VALUE", "PASS", "HIGH",
                f"Edge negativo ({(edge or 0)*100:+.1f}%) en {market}. El mercado paga peor que la probabilidad estimada.")

    # Strong positive edge after a momentum-defining moment (goal, red card,
    # last 15min push). Detected via absolute momentum >= 60 + last-third clock.
    last_third = (minute is not None and minute >= 60)
    strong_momentum = abs(momentum) >= 60
    if edge >= EDGE_OVERREACTION and strong_momentum and last_third:
        return ("MARKET_OVERREACTION", "BET", "MEDIUM",
                f"Sobre-reacción del mercado: el live paga {(edge*100):+.1f}% por encima de lo justo tras un cambio de momentum claro.")

    if edge >= EDGE_VALUE_WINDOW:
        if strong_momentum:
            return ("LIVE_VALUE_WINDOW", "BET", "LOW",
                    f"Ventana de valor live confirmada: {edge*100:+.1f}% de edge en {market} y el momentum apoya la tesis.")
        return ("LIVE_VALUE_WINDOW", "BET", "MEDIUM",
                f"Ventana de valor live: edge {edge*100:+.1f}% en {market}. Momentum mixto — stake moderado.")

    if edge >= EDGE_WATCHLIST:
        return ("WATCHLIST", "WATCH", "MEDIUM",
                f"Edge marginal ({edge*100:+.1f}%) en {market}. Esperar mejor línea o confirmación de momentum.")

    # Significant momentum without enough edge → MOMENTUM_SHIFT (informational).
    if strong_momentum and last_third:
        side = "local" if momentum > 0 else "visitante"
        return ("MOMENTUM_SHIFT", "WATCH", "MEDIUM",
                f"Momentum fuerte a favor del {side} en el último tercio, pero la cuota aún no compensa. Vigilar.")

    return ("NO_LIVE_VALUE", "PASS", "MEDIUM",
            f"No hay valor live claro: edge {edge*100:+.1f}% en {market} no supera el umbral mínimo.")


def _confidence_score(edge: Optional[float], momentum: int, remaining: float, basis: str) -> int:
    """0-100. Heavier weight on edge; bonus for poisson-backed estimates."""
    if edge is None:
        return 0
    base = 50 + edge * 600  # +6 points per 1% edge
    base += min(15, abs(momentum) * 0.15)
    base += 5 if basis == "poisson_live" else 0
    # Slight penalty when very little time is left (model has less signal).
    base += (remaining - 0.5) * 10
    return int(max(0, min(100, base)))


def _build_response(**kwargs) -> dict:
    """Single point of response construction so the caller gets a stable shape."""
    edge = kwargs.get("edge")
    return {
        "match_id":            kwargs.get("match_id"),
        "live_state":          kwargs.get("live_state"),
        "recommended_action":  kwargs.get("recommended_action"),
        "market":              kwargs.get("market"),
        "selection":           kwargs.get("selection"),
        "decimal_odds":        kwargs.get("decimal_odds"),
        "estimated_probability": round(kwargs.get("estimated_probability") or 0, 4),
        "implied_probability": round(kwargs.get("implied_probability") or 0, 4) if kwargs.get("implied_probability") is not None else None,
        "edge":                round(edge, 4) if edge is not None else None,
        "edge_pct":            round((edge or 0) * 100, 2) if edge is not None else None,
        "confidence":          kwargs.get("confidence", 0),
        "risk_level":          kwargs.get("risk_level"),
        "reason":              kwargs.get("reason"),
        "live_snapshot":       kwargs.get("live_snapshot") or {},
        "manual_odds_used":    bool(kwargs.get("manual_odds_used")),
        "computed_at":         kwargs.get("computed_at"),
        "live_analysis":       kwargs.get("live_analysis"),
        "trap":                kwargs.get("trap"),
        "interpreter":         kwargs.get("interpreter"),
        "football_live_vs_pregame": kwargs.get("football_live_vs_pregame"),
        "game_openness":       kwargs.get("game_openness"),
        "unilateral_dominance": kwargs.get("unilateral_dominance"),
        # Phase 45 — Football siege guard verdict + Phase 44 baseball
        # bullpen/traffic verdict. Both are observe-only payloads
        # surfaced for the UI; ``None`` when not applicable.
        "siege_pressure":      kwargs.get("siege_pressure"),
        "bullpen_traffic":     kwargs.get("bullpen_traffic"),
    }



# ─── Basketball re-evaluation ──────────────────────────────────────────────

# Sport-specific defaults
BBALL_AVG_PACE_PPM = 4.5          # NBA-ish total points per game minute (combined)
BBALL_REG_GAME_MIN = 48           # 4 × 12 min regulation


def _basket_estimate_probability(market: str, match: dict, h_score: int, a_score: int,
                                 frac_remaining: float, projected_total: float,
                                 pace_pts_per_min: float) -> tuple[float, str]:
    """Estimate the probability the chosen basketball market cashes.

    Markets supported:
      • "Money Line: home" / "Money Line: away"          → side cover probability
      • "Total: Over X.5" / "Total: Under X.5"           → over/under projected_total
      • "Spread: home -X.5" / "Spread: away -X.5"        → ATS cover
    """
    market_l = (market or "").strip().lower()
    cur_lead = h_score - a_score
    cur_total = h_score + a_score

    # ── Money Line (no draw possible — NBA / OT to settle) ─────────────
    if market_l.startswith("money line:") or market_l in ("home", "away"):
        side = "home" if "home" in market_l else "away"
        lead_for_side = cur_lead if side == "home" else -cur_lead
        # Project remaining minutes; one-team-pace ≈ half overall pace
        remaining_min = frac_remaining * BBALL_REG_GAME_MIN
        # Expected points this side scores from now: half the league pace, regressed
        # toward observed share. Without per-side pace, use 50/50 split assumption.
        # Probability of win = Φ((lead_for_side) / σ) — using rough σ ≈ 8 pts.
        import math
        sigma = max(4.0, 8.0 * (frac_remaining ** 0.5))  # variance scales with time left
        z = lead_for_side / sigma
        p = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return max(0.02, min(0.98, p)), "normal_lead_model"

    # ── Total Over/Under ───────────────────────────────────────────────
    if "total" in market_l and ("over" in market_l or "under" in market_l):
        # Parse the line number out of the market string.
        import re
        m = re.search(r"(\d+(?:\.\d+)?)", market)
        if not m:
            return 0.5, "no_line"
        line = float(m.group(1))
        is_over = "over" in market_l
        # Expected final total = current_total + (pace * remaining)
        remaining_min = frac_remaining * BBALL_REG_GAME_MIN
        exp_final = cur_total + (pace_pts_per_min or BBALL_AVG_PACE_PPM) * remaining_min
        # Approximate stdev of remaining points ~ 9 pts for 48-min games.
        import math
        sigma = max(5.0, 9.0 * (frac_remaining ** 0.5) + 2)
        z = (exp_final - line) / sigma
        p_over = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        prob = p_over if is_over else (1 - p_over)
        return max(0.02, min(0.98, prob)), "pace_projection"

    # ── Spread / Handicap ──────────────────────────────────────────────
    if "spread" in market_l or "handicap" in market_l:
        import re
        side = "home" if "home" in market_l else "away"
        m = re.search(r"[-+]?\d+(?:\.\d+)?", market)
        if not m:
            return 0.5, "no_handicap"
        handicap = float(m.group(0))
        # Adjusted lead = (lead_for_side + handicap) — assume points add to that team
        lead_for_side = cur_lead if side == "home" else -cur_lead
        adjusted = lead_for_side + handicap
        # Projected adjusted lead at FT
        import math
        sigma = max(4.0, 8.5 * (frac_remaining ** 0.5))
        z = adjusted / sigma
        p_cover = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return max(0.02, min(0.98, p_cover)), "spread_normal_model"

    # Fallback
    return 0.5, "unknown_market"


def _basket_infer_default_market(h_score: int, a_score: int, projected_total: float) -> str:
    """When the user didn't pick a market, default to projected-total Over X.5."""
    line = round(projected_total / 0.5) * 0.5
    # Half-point so it doesn't push.
    if line == int(line):
        line += 0.5
    return f"Total: Over {line}"


def _reevaluate_basketball(
    match: dict,
    *,
    manual_odds: Optional[float] = None,
    manual_market: Optional[str] = None,
) -> dict:
    """Basketball-specific Live Re-Eval.

    Uses `services.live_basketball_analytics` for analysis + blowout trap.
    Markets: Money Line, Total (Over/Under), Spread.
    """
    from datetime import datetime, timezone
    from . import live_basketball_analytics as lba
    now = datetime.now(timezone.utc).isoformat()

    analysis = lba.compute_live_analysis(match)
    minute = analysis.get("minute")
    score = analysis.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    pace = (analysis.get("deltas") or {}).get("points_per_min") or BBALL_AVG_PACE_PPM
    projected_total = (analysis.get("deltas") or {}).get("projected_total") or 0.0
    frac_remaining = analysis.get("fraction_remaining") or 0.5
    trap = analysis.get("trap")

    market = manual_market or _basket_infer_default_market(h_score, a_score, projected_total)
    selection = market

    est_prob, est_basis = _basket_estimate_probability(
        market, match, h_score, a_score, frac_remaining, projected_total, pace
    )

    # Implied probability — manual odds win.
    if manual_odds and manual_odds > 1.01:
        implied = 1.0 / float(manual_odds)
        decimal_odds = float(manual_odds)
        implied_source = "manual"
    else:
        decimal_odds, implied = _best_pre_match_quote(match, market)
        implied_source = "pre_match"

    if implied is None:
        return _build_response(
            match_id=match.get("match_id"),
            live_state="NO_LIVE_VALUE",
            recommended_action="PASS",
            market=market, selection=selection,
            estimated_probability=est_prob,
            implied_probability=None,
            decimal_odds=None,
            edge=None,
            confidence=0,
            risk_level="HIGH",
            reason="Sin cuota disponible para basket. Ingresa la cuota de tu bookie.",
            live_snapshot={"minute": minute, "score": score, "is_live": True, "sport": "basketball"},
            manual_odds_used=False,
            computed_at=now,
            live_analysis=analysis,
            trap=trap,
        )

    edge = est_prob - implied
    market_l = market.lower()

    # ── Blowout trap gate (basketball) ─────────────────────────────────
    # If the user is betting on the leader's money line during a Q4
    # blowout with sub-1.20 odds → hard PASS.
    leader_side = "home" if h_score > a_score else "away" if a_score > h_score else None
    trap_overrides = (
        trap and trap.get("triggered") and leader_side
        and "money line" in market_l and leader_side in market_l
    )
    if trap_overrides:
        state, action, risk, reason = "TRAP_DETECTED", "PASS", "HIGH", trap["reason_es"]
        confidence = 0
    else:
        # Lightweight classification (no goals-remaining concept here)
        if edge >= 0.05:
            state, action, risk = "LIVE_VALUE_WINDOW", "BET", "LOW" if edge >= 0.10 else "MEDIUM"
            reason = f"Ventana de valor live basket: edge +{edge*100:.1f}% en {market}."
        elif edge >= 0.02:
            state, action, risk = "LIVE_VALUE_WATCH", "WATCH", "MEDIUM"
            reason = f"Posible valor en {market} (edge +{edge*100:.1f}%). Esperar mejor línea o nueva información."
        elif edge >= -0.02:
            state, action, risk = "LIVE_NEUTRAL", "HOLD", "MEDIUM"
            reason = f"Sin edge claro en {market} (edge {edge*100:.1f}%). Pase recomendado."
        else:
            state, action, risk = "NO_LIVE_VALUE", "PASS", "HIGH"
            reason = f"Cuota sin valor en {market} (edge {edge*100:.1f}%)."
        # Confidence based on time elapsed + magnitude of edge.
        confidence = max(0, min(100, int(50 + (edge * 200) + ((1 - frac_remaining) * 30))))
        if trap and trap.get("triggered"):
            reason = f"{reason}  ⚠ {trap['reason_es']}"

    reeval_for_interpreter = {
        "live_state":           state,
        "recommended_action":   action,
        "market":               market,
        "edge":                 edge,
        "edge_pct":             round((edge or 0) * 100, 2) if edge is not None else None,
        "confidence":           confidence,
        "estimated_probability": round(est_prob or 0, 4),
        "implied_probability":  round(implied or 0, 4) if implied is not None else None,
        "decimal_odds":         decimal_odds,
        "manual_odds_used":     (implied_source == "manual"),
        "reason":               reason,
    }
    try:
        from . import human_live_interpreter as hli
        interpreter = hli.interpret_live(
            match,
            analysis=analysis,
            reeval=reeval_for_interpreter,
        )
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").warning("interpret_live (basketball) failed: %s", _exc)
        interpreter = None

    return _build_response(
        match_id=match.get("match_id"),
        live_state=state,
        recommended_action=action,
        market=market, selection=selection,
        estimated_probability=est_prob,
        implied_probability=implied,
        decimal_odds=decimal_odds,
        edge=edge,
        confidence=confidence,
        risk_level=risk,
        reason=reason,
        live_snapshot={"minute": minute, "score": score, "is_live": True,
                       "sport": "basketball", "projected_total": projected_total,
                       "pace_ppm": round(pace, 2)},
        manual_odds_used=(implied_source == "manual"),
        computed_at=now,
        live_analysis=analysis,
        trap=trap,
        interpreter=interpreter,
    )



# ─── Baseball re-evaluation ───────────────────────────────────────────────

BASEBALL_AVG_RUN_RATE = 0.98   # ~8.9 runs/game combinado MLB ÷ 9 innings
BASEBALL_REG_INNINGS  = 9


def _baseball_estimate_probability(
    market: str,
    h_score: int,
    a_score: int,
    frac_remaining: float,
    projected_total: float,
    run_rate: float,
) -> tuple[float, str]:
    """Estimate probability for baseball markets.

    Markets supported:
      • "Money Line: home" / "Money Line: away"
      • "Total: Over X.5"  / "Total: Under X.5"
      • "Run Line: home -1.5" / "Run Line: away +1.5"

    Returns (estimated_probability, basis_label).
    """
    import math
    import re as _re
    market_l  = (market or "").strip().lower()
    cur_lead  = h_score - a_score
    cur_total = h_score + a_score

    # ── Money Line (no draw in baseball — extra innings to settle) ──────
    if market_l.startswith("money line:") or market_l in ("home", "away"):
        side          = "home" if "home" in market_l else "away"
        lead_for_side = cur_lead if side == "home" else -cur_lead
        # σ scales with innings remaining: less time = tighter distribution.
        sigma = max(1.5, 3.5 * (frac_remaining ** 0.5))
        z     = lead_for_side / sigma
        p     = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return max(0.02, min(0.98, p)), "normal_lead_model"

    # ── Total Over / Under runs ─────────────────────────────────────────
    if "total" in market_l and ("over" in market_l or "under" in market_l):
        m = _re.search(r"(\d+(?:\.\d+)?)", market)
        if not m:
            return 0.5, "no_line"
        line    = float(m.group(1))
        is_over = "over" in market_l
        remaining_innings = frac_remaining * BASEBALL_REG_INNINGS
        exp_final = cur_total + (run_rate or BASEBALL_AVG_RUN_RATE) * remaining_innings
        sigma = max(1.5, 3.0 * (frac_remaining ** 0.5) + 1.0)
        z     = (exp_final - line) / sigma
        p_over = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        prob   = p_over if is_over else (1 - p_over)
        return max(0.02, min(0.98, prob)), "run_rate_projection"

    # ── Run Line (-1.5 / +1.5) ─────────────────────────────────────────
    if "run line" in market_l:
        side = "home" if "home" in market_l else "away"
        m    = _re.search(r"[-+]?\d+(?:\.\d+)?", market)
        handicap      = float(m.group(0)) if m else -1.5
        lead_for_side = cur_lead if side == "home" else -cur_lead
        adjusted      = lead_for_side + handicap
        sigma   = max(1.5, 3.5 * (frac_remaining ** 0.5))
        z       = adjusted / sigma
        p_cover = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return max(0.02, min(0.98, p_cover)), "run_line_model"

    # Fallback — unknown market
    return 0.5, "unknown_market"


def _baseball_infer_default_market(projected_total: float) -> str:
    """When the user hasn't specified a market, default to the Over/Under
    line closest to the projected final run total."""
    common_lines = [6.5, 7.5, 8.5, 9.5, 10.5]
    line = min(common_lines, key=lambda ln: abs(ln - projected_total))
    return f"Total: Over {line}"


def _reevaluate_baseball(
    match: dict,
    *,
    manual_odds: Optional[float] = None,
    manual_market: Optional[str] = None,
) -> dict:
    """Baseball-specific Live Re-Eval.

    Uses live_baseball_analytics for live analysis + blowout-trap detection.
    Supported markets: Money Line, Total (Over/Under runs), Run Line.
    Follows the exact same structure as _reevaluate_basketball() so the
    shared _build_response() envelope is preserved.
    """
    from datetime import datetime, timezone
    from . import live_baseball_analytics as lba
    now = datetime.now(timezone.utc).isoformat()

    analysis  = lba.compute_live_analysis(match)
    inning    = analysis.get("inning")
    score     = analysis.get("score") or {}
    h_score   = int(score.get("home") or 0)
    a_score   = int(score.get("away") or 0)
    run_rate  = (analysis.get("deltas") or {}).get("run_rate_combined") or BASEBALL_AVG_RUN_RATE
    proj_tot  = (analysis.get("deltas") or {}).get("projected_total") or 0.0
    frac_rem  = analysis.get("fraction_remaining") or 0.5
    trap      = analysis.get("trap")

    market    = manual_market or _baseball_infer_default_market(proj_tot)
    selection = market

    est_prob, est_basis = _baseball_estimate_probability(
        market, h_score, a_score, frac_rem, proj_tot, run_rate,
    )

    # ── Implied probability: manual odds win; fallback to pre-match ─────
    if manual_odds and manual_odds > 1.01:
        implied        = 1.0 / float(manual_odds)
        decimal_odds   = float(manual_odds)
        implied_source = "manual"
    else:
        decimal_odds, implied = _best_pre_match_quote(match, market)
        implied_source = "pre_match"

    if implied is None:
        return _build_response(
            match_id=match.get("match_id"),
            live_state="NO_LIVE_VALUE",
            recommended_action="PASS",
            market=market, selection=selection,
            estimated_probability=est_prob,
            implied_probability=None,
            decimal_odds=None,
            edge=None,
            confidence=0,
            risk_level="HIGH",
            reason=(
                "Sin cuota disponible para béisbol. "
                "Ingresa la cuota de tu bookie para reevaluar."
            ),
            live_snapshot={
                "minute": inning, "score": score,
                "is_live": True, "sport": "baseball",
                "projected_total": proj_tot,
                "run_rate": round(run_rate, 2),
            },
            manual_odds_used=False,
            computed_at=now,
            live_analysis=analysis,
            trap=trap,
        )

    edge     = est_prob - implied
    market_l = market.lower()

    # ── Blowout trap gate ───────────────────────────────────────────────
    # If the user bets the leader's Money Line during a late blowout with
    # sub-1.20 odds → hard PASS regardless of model edge.
    leader_side = (
        "home" if h_score > a_score else
        "away" if a_score > h_score else None
    )
    trap_overrides = (
        trap and trap.get("triggered") and leader_side
        and "money line" in market_l and leader_side in market_l
    )

    if trap_overrides:
        state, action, risk, reason = (
            "TRAP_DETECTED", "PASS", "HIGH", trap["reason_es"]
        )
        confidence = 0
    else:
        if edge >= 0.05:
            state  = "LIVE_VALUE_WINDOW"
            action = "BET"
            risk   = "LOW" if edge >= 0.10 else "MEDIUM"
            reason = f"Ventana de valor béisbol: edge +{edge*100:.1f}% en {market}."
        elif edge >= 0.02:
            state, action, risk = "WATCHLIST", "WATCH", "MEDIUM"
            reason = (
                f"Posible valor en {market} (edge +{edge*100:.1f}%). "
                f"Esperar confirmación de tendencia."
            )
        elif edge >= -0.02:
            state, action, risk = "NO_LIVE_VALUE", "PASS", "MEDIUM"
            reason = f"Sin edge claro en {market} (edge {edge*100:.1f}%)."
        else:
            state, action, risk = "NO_LIVE_VALUE", "PASS", "HIGH"
            reason = f"Cuota sin valor en {market} (edge {edge*100:.1f}%)."

        confidence = max(0, min(100, int(
            50 + (edge * 200) + ((1 - frac_rem) * 30)
        )))
        # Attach secondary trap warning when trap fired but didn't hard-override.
        if trap and trap.get("triggered"):
            reason = f"{reason}  ⚠ {trap['reason_es']}"

    # ── Interpreter: coach-voice payload for LiveCopilotCard ────────────
    reeval_for_interpreter = {
        "live_state":            state,
        "recommended_action":    action,
        "market":                market,
        "edge":                  edge,
        "edge_pct":              round((edge or 0) * 100, 2),
        "confidence":            confidence,
        "estimated_probability": round(est_prob or 0, 4),
        "implied_probability":   round(implied or 0, 4) if implied is not None else None,
        "decimal_odds":          decimal_odds,
        "manual_odds_used":      (implied_source == "manual"),
        "reason":                reason,
    }
    try:
        from . import human_live_interpreter as hli
        interpreter = hli.interpret_live(
            match,
            analysis=analysis,
            reeval=reeval_for_interpreter,
        )
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").warning(
            "interpret_live (baseball) failed: %s", _exc
        )
        interpreter = None

    # ── Phase 44 — Bullpen + Traffic interaction (Phase 46: ACTIVE) ──
    # When the match doc has pre-hydrated bullpen_era_7d_max and
    # traffic_bucket (from the MLB pregame pipeline), surface the
    # bullpen-traffic verdict so the UI can show the "Bullpen risk
    # confirmed by traffic" badge. Fail-soft: never raises. Phase 46
    # promotes this from observe-only to active: when the live verdict
    # is ``penalize_under`` for an Under pick, the engine caps confidence
    # and flips to PASS. Live traffic (RISP/LOB/pitch count) is computed
    # on the fly when the live_stats payload exposes those fields, and
    # softens the verdict when traffic is visibly collapsing.
    bullpen_traffic = None
    live_traffic = None
    try:
        from . import traffic_score as _ts
        mlb_pregame = match.get("mlb_pregame_snapshot") or match.get("mlb_pregame") or {}
        scoring_ctx = match.get("scoring_context") or {}
        bp_max = (
            mlb_pregame.get("bullpen_era_7d_max")
            or match.get("bullpen_era_7d_max")
        )
        if bp_max is None:
            fav = scoring_ctx.get("favorite_bullpen_era_7d")
            und = scoring_ctx.get("underdog_bullpen_era_7d")
            if fav is not None and und is not None:
                try:
                    bp_max = max(float(fav), float(und))
                except (TypeError, ValueError):
                    bp_max = None
        pregame_traffic_bucket = (
            mlb_pregame.get("traffic_bucket")
            or match.get("traffic_bucket")
            or (mlb_pregame.get("traffic_score_obj") or {}).get("traffic_bucket")
        )
        pregame_traffic_score = (
            mlb_pregame.get("traffic_score")
            or match.get("traffic_score")
        )
        is_under_pick = market_l.startswith("under") or "total: under" in market_l

        # ── Compute LIVE traffic score from in-game stats (RISP, LOB,
        #    pitch count, hard contact, exit velocity) when available.
        live_stats = match.get("live_stats") or {}
        h_live = live_stats.get("home_stats") or {}
        a_live = live_stats.get("away_stats") or {}

        # ── Phase 50 — Live Defensive Breakdown (raw in-game events) ──
        # Aggregated across both sides so the live_traffic_score reflects
        # the combined sloppy-fielding pressure. Fail-soft.
        defensive_breakdown_live = None
        try:
            from .mlb_defensive_breakdown_score import compute_defensive_breakdown_score
            defensive_breakdown_live = compute_defensive_breakdown_score(
                mode="live",
                live_errors=int((h_live.get("errors") or 0) + (a_live.get("errors") or 0)),
                live_passed_balls=int((h_live.get("passed_balls") or 0) + (a_live.get("passed_balls") or 0)),
                live_wild_pitches=int((h_live.get("wild_pitches") or 0) + (a_live.get("wild_pitches") or 0)),
                live_stolen_bases=int((h_live.get("stolen_bases") or 0) + (a_live.get("stolen_bases") or 0)),
                live_catcher_mistakes=int((h_live.get("catcher_mistakes") or 0) + (a_live.get("catcher_mistakes") or 0)),
                runners_advanced_on_errors=int((h_live.get("runners_advanced_on_errors") or 0) + (a_live.get("runners_advanced_on_errors") or 0)),
                unearned_runs=int((h_live.get("unearned_runs") or 0) + (a_live.get("unearned_runs") or 0)),
                innings_played=analysis.get("innings_played"),
            )
        except Exception as _exc_db:
            import logging
            logging.getLogger("live_reeval").debug("live defensive_breakdown failed: %s", _exc_db)
            defensive_breakdown_live = None

        if h_live or a_live:
            live_traffic = _ts.compute_live_traffic_score(
                inning=inning,
                innings_played=analysis.get("innings_played"),
                home_live=h_live,
                away_live=a_live,
                pitch_count_home=h_live.get("pitch_count")
                                  or h_live.get("pitches_thrown"),
                pitch_count_away=a_live.get("pitch_count")
                                  or a_live.get("pitches_thrown"),
                pregame_traffic_score=pregame_traffic_score,
                pregame_traffic_bucket=pregame_traffic_bucket,
                is_under_pick=is_under_pick,
                defensive_breakdown_score=(defensive_breakdown_live or {}).get("defensive_breakdown_score"),
            )

        # Use LIVE bucket when it diverges from pregame (rising/collapsing);
        # otherwise default to pregame bucket. This is what "active mode"
        # uses to drive the verdict.
        effective_bucket = pregame_traffic_bucket
        pregame_delta = None
        live_score_val = None
        if live_traffic:
            live_score_val = live_traffic.get("live_traffic_score")
            pregame_delta  = live_traffic.get("pregame_delta")
            # Promote the live bucket only when it diverges meaningfully
            # (delta >= 15) — otherwise the live signal is too noisy to
            # override the pregame composite.
            if pregame_delta is not None and abs(pregame_delta) >= 15:
                effective_bucket = live_traffic.get("live_traffic_bucket")

        if bp_max is not None or effective_bucket is not None:
            bullpen_traffic = _ts.classify_live_bullpen_traffic_interaction(
                bullpen_era_7d_max=float(bp_max) if bp_max is not None else None,
                live_traffic_bucket=effective_bucket,
                live_traffic_score=live_score_val,
                pregame_delta=pregame_delta,
                is_under_pick=is_under_pick,
            )
            if pregame_traffic_score is not None:
                bullpen_traffic["traffic_score"] = pregame_traffic_score
            bullpen_traffic["bullpen_era_7d_max"]    = bp_max
            bullpen_traffic["traffic_bucket"]        = effective_bucket
            bullpen_traffic["pregame_traffic_bucket"] = pregame_traffic_bucket
            bullpen_traffic["live_traffic"]          = live_traffic
            bullpen_traffic["active"]                = True  # Phase 46
            bullpen_traffic["defensive_breakdown"]   = defensive_breakdown_live

            # ── Phase 50 — Combined trifecta explosion-risk warning ──
            try:
                from .mlb_defensive_breakdown_score import classify_combined_explosion_risk
                combined = classify_combined_explosion_risk(
                    bullpen_era_7d_max=float(bp_max) if bp_max is not None else None,
                    live_traffic_bucket=(live_traffic or {}).get("live_traffic_bucket"),
                    defensive_bucket=(defensive_breakdown_live or {}).get("defensive_bucket"),
                    is_under_pick=is_under_pick,
                )
                if combined.get("verdict") == "penalize_under":
                    # Append the trifecta reason code & UI message.
                    bullpen_traffic["reason_codes"] = list(dict.fromkeys(
                        (bullpen_traffic.get("reason_codes") or [])
                        + combined.get("reason_codes", [])
                    ))
                    bullpen_traffic["ui_message_es"] = combined.get("ui_message_es")
                    bullpen_traffic["combined_explosion"] = combined
            except Exception as _exc_comb:
                import logging
                logging.getLogger("live_reeval").debug(
                    "combined explosion classifier failed: %s", _exc_comb,
                )

        # ── ACTIVE: apply verdict to the engine output ────────────────
        if (
            bullpen_traffic
            and bullpen_traffic.get("verdict") == "penalize_under"
            and is_under_pick
        ):
            state, action, risk = "BULLPEN_TRAFFIC_BLOCK", "PASS", "HIGH"
            confidence = 0
            extra_msg = (
                "Riesgo de bullpen confirmado por tráfico ofensivo: "
                "el Under es de alto riesgo en este perfil."
            )
            if live_traffic and pregame_delta is not None and pregame_delta >= 15:
                extra_msg += "  ⚠ Tráfico LIVE en alza vs pregame."
            reason = extra_msg if not reason else f"{reason}  ⚠ {extra_msg}"
        elif (
            bullpen_traffic
            and bullpen_traffic.get("verdict") == "monitor_under"
            and is_under_pick
            and confidence > 40
        ):
            # Live traffic collapsing softened the verdict — don't block,
            # just cap confidence so the user is aware the model is split.
            confidence = 40
            reason = (
                (reason or "")
                + "  ⚠ Bullpen vulnerable pero tráfico LIVE colapsando."
            )
        elif (
            bullpen_traffic
            and bullpen_traffic.get("verdict") == "hold_under"
            and is_under_pick
            and state in ("LIVE_VALUE_WINDOW", "WATCHLIST")
        ):
            # Bullpen vulnerable but isolated — small confidence boost so
            # the user sees the engine corroborating the Under.
            confidence = min(100, (confidence or 0) + 5)
    except Exception as _exc:
        import logging
        logging.getLogger("live_reeval").debug(
            "bullpen_traffic enrichment failed: %s", _exc,
        )
        bullpen_traffic = None
        live_traffic = None

    return _build_response(
        match_id=match.get("match_id"),
        live_state=state,
        recommended_action=action,
        market=market, selection=selection,
        estimated_probability=est_prob,
        implied_probability=implied,
        decimal_odds=decimal_odds,
        edge=edge,
        confidence=confidence,
        risk_level=risk,
        reason=reason,
        live_snapshot={
            "minute":          inning,
            "score":           score,
            "is_live":         True,
            "sport":           "baseball",
            "projected_total": proj_tot,
            "run_rate":        round(run_rate, 2),
        },
        manual_odds_used=(implied_source == "manual"),
        computed_at=now,
        live_analysis=analysis,
        trap=trap,
        interpreter=interpreter,
        bullpen_traffic=bullpen_traffic,
    )
