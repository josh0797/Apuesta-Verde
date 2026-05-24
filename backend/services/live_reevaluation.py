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
    """Compute a Live Re-Evaluation result for a football match.

    Args:
        match: hydrated match doc with `live_stats` (minute, score) and
            `odds_snapshots` (pre-match) and optionally `h2h_recent`.
        manual_odds: user-pasted decimal odds from their bookie for the
            market they're considering. WHEN PRESENT this is treated as the
            authoritative implied probability.
        manual_market: short label of the manual market — e.g.
            "Under 2.5", "Under 3.5", "Over 1.5", "Resultado Final: home",
            "Doble Oportunidad: 1X". The label drives WHICH probability
            estimate we compare against.
        expected_goals_total: prior on final-game xG total. Football default 2.5.

    Returns:
        {
            "match_id": ...,
            "live_state": "...",
            "recommended_action": "BET" | "WATCH" | "HOLD" | "CASH_OUT" | "PASS",
            "market": str,
            "selection": str,
            "estimated_probability": float,
            "implied_probability": float,
            "edge": float,
            "edge_pct": float,
            "confidence": int 0-100,
            "risk_level": "LOW" | "MEDIUM" | "HIGH",
            "reason": str,
            "live_snapshot": {minute, score, momentum, momentum_side},
            "manual_odds_used": bool,
            "computed_at": iso8601,
        }
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
    }
