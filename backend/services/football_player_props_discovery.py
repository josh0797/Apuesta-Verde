"""Football Player Props Discovery (Moneyball) — Phase F58 + Fix 2.

Versión v2 con sistema de scoring compuesto:

* ``player_prop_score`` (0-100) — promedio ponderado de:
    - Role / minutes certainty:  30 %
    - Volume stat (per-90):      30 %
    - Matchup:                   20 %
    - Game script:               15 %
    - Market safety:             5  %

* ``player_prop_fragility`` (0-100) — riesgo del prop.

Moneyball filter
----------------
* Tier 1-2: ``score >= 70`` AND ``fragility <= 45``
* Tier 3:   ``score >= 90`` AND ``fragility <= 35``
* Si no cumple → ``confidence_tier ∈ {"WATCHLIST", "AVOID"}``.

Fail-soft contract: lista vacía siempre válida, sin excepciones.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("football_player_props_discovery")

ENGINE_VERSION = "football_player_props_discovery.v2"

# Markets / Tiers
MARKET_SHOTS_OVER = "SHOTS_OVER"
MARKET_SOT_OVER = "SOT_OVER"
MARKET_PASSES_OVER = "PASSES_OVER"
MARKET_TACKLES_OVER = "TACKLES_OVER"
MARKET_FOULS_OVER = "FOULS_OVER"
MARKET_CARDS_OVER = "CARDS_OVER"
MARKET_TO_SCORE = "PLAYER_TO_SCORE"

TIER_1 = (MARKET_SHOTS_OVER, MARKET_SOT_OVER, MARKET_PASSES_OVER, MARKET_TACKLES_OVER)
TIER_2 = (MARKET_FOULS_OVER, MARKET_CARDS_OVER)
TIER_3 = (MARKET_TO_SCORE,)

TIER_BY_MARKET: dict[str, int] = {m: 1 for m in TIER_1}
TIER_BY_MARKET.update({m: 2 for m in TIER_2})
TIER_BY_MARKET.update({m: 3 for m in TIER_3})

DEFAULT_LINES = {
    MARKET_SHOTS_OVER: 1.5,
    MARKET_SOT_OVER: 0.5,
    MARKET_PASSES_OVER: 39.5,
    MARKET_TACKLES_OVER: 1.5,
    MARKET_FOULS_OVER: 1.5,
    MARKET_CARDS_OVER: 0.5,
    MARKET_TO_SCORE: 0.5,
}
DEFAULT_ODDS = {
    MARKET_SHOTS_OVER: -120,
    MARKET_SOT_OVER: -110,
    MARKET_PASSES_OVER: -115,
    MARKET_TACKLES_OVER: -110,
    MARKET_FOULS_OVER: -120,
    MARKET_CARDS_OVER: +180,
    MARKET_TO_SCORE: +350,
}
STAT_KEY_BY_MARKET = {
    MARKET_SHOTS_OVER: "shots_p90",
    MARKET_SOT_OVER: "sot_p90",
    MARKET_PASSES_OVER: "passes_p90",
    MARKET_TACKLES_OVER: "tackles_p90",
    MARKET_FOULS_OVER: "fouls_p90",
    MARKET_CARDS_OVER: "cards_p90",
    MARKET_TO_SCORE: "xg_p90",
}

# Volume per-90 thresholds (Fix 2 spec)
VOLUME_THRESHOLDS = {
    MARKET_SHOTS_OVER: 2.0,
    MARKET_SOT_OVER: 0.8,
    MARKET_PASSES_OVER: 45.0,
    MARKET_TACKLES_OVER: 1.5,
    MARKET_FOULS_OVER: 1.8,
    MARKET_CARDS_OVER: 0.20,
    MARKET_TO_SCORE: 0.30,
}

# Fragility baselines (Fix 2 spec)
FRAGILITY_BASELINE = {
    MARKET_SHOTS_OVER: 20,
    "SHOTS_OVER_2": 35,
    MARKET_SOT_OVER: 42,
    MARKET_FOULS_OVER: 25,
    MARKET_TACKLES_OVER: 28,
    MARKET_PASSES_OVER: 30,
    MARKET_CARDS_OVER: 45,
    "GK_SAVES_OVER": 35,
    MARKET_TO_SCORE: 65,
    "FIRST_GOALSCORER": 85,
}

# Moneyball filter gates
MONEYBALL_SCORE_GATE = 70
MONEYBALL_FRAGILITY_GATE = 45
TIER3_SCORE_GATE = 90
TIER3_FRAGILITY_GATE = 35
WATCHLIST_SCORE_FLOOR = 55
LONGSHOT_PROB_FLOOR = 0.50

W_ROLE = 30
W_VOLUME = 30
W_MATCHUP = 20
W_SCRIPT = 15
W_SAFETY = 5
DEFAULT_MINUTES_STARTER = 78
DEFAULT_MINUTES_SUB = 22
MATCHUP_MULT_FLOOR = 0.75
MATCHUP_MULT_CEIL = 1.25

SCRIPT_CONTROLLED_FAVORITE = "CONTROLLED_FAVORITE"
SCRIPT_UNILATERAL_DOMINANCE = "UNILATERAL_DOMINANCE"
SCRIPT_BILATERAL_OPENNESS = "BILATERAL_OPENNESS"
SCRIPT_LOW_EVENT_UNDER = "LOW_EVENT_UNDER"
SCRIPT_LATE_SIEGE = "LATE_SIEGE"
SCRIPT_CHAOTIC_MATCH = "CHAOTIC_MATCH"


def _safe(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-mu) * (mu**k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _poisson_p_ge(k_target: int, mu: float, max_k: int = 60) -> float:
    if mu <= 0:
        return 0.0 if k_target > 0 else 1.0
    if k_target <= 0:
        return 1.0
    cum = 0.0
    for k in range(0, k_target):
        cum += _poisson_pmf(k, mu)
        if cum >= 0.99999:
            return max(0.0, 1.0 - cum)
        if k > max_k:
            break
    return max(0.0, min(1.0, 1.0 - cum))


def american_odds_to_implied(odds: int) -> float:
    if odds is None:
        return 0.50
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return 0.50
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def _line_k_target(line: float) -> int:
    if line is None:
        return 1
    if line < 0:
        return 0
    return int(math.ceil(line + 1e-9)) if (line - int(line) > 0) else int(line)


# ── Score components ────────────────────────────────────────────────
def _role_minutes_component(p: dict, reason_codes: list[str]) -> int:
    projected_starter = bool(p.get("projected_starter", True))
    minutes_proj = _safe(p.get("minutes_projection")) or (
        DEFAULT_MINUTES_STARTER if projected_starter else DEFAULT_MINUTES_SUB
    )
    started_l5 = _safe(p.get("started_last_5"))
    position_fits = bool(p.get("position_fits_market", True))
    rotation_risk = bool(p.get("rotation_risk", False))
    injury_returning = bool(p.get("returning_from_injury", False))
    sub_likely = bool(p.get("substitute_likely", False))

    score = 50
    if projected_starter:
        score += 20
    if minutes_proj >= 70:
        score += 15
    if started_l5 is not None and started_l5 >= 4:
        score += 10
    if position_fits:
        score += 5
    if rotation_risk:
        score -= 15
    if injury_returning:
        score -= 15
    if minutes_proj < 60:
        score -= 10
    if sub_likely:
        score -= 20
    score = int(_clamp(score, 0, 100))

    if score >= 70 and not rotation_risk and not sub_likely:
        reason_codes.append("PLAYER_MINUTES_STABLE")
    if rotation_risk or sub_likely or minutes_proj < 60:
        reason_codes.append("PLAYER_ROTATION_RISK")
    if position_fits:
        reason_codes.append("PLAYER_ROLE_FITS_MARKET")
    return score


def _volume_component(
    market: str, stat_p90: Optional[float], reason_codes: list[str]
) -> int:
    if stat_p90 is None or stat_p90 <= 0:
        return 0
    threshold = VOLUME_THRESHOLDS.get(market)
    if threshold is None:
        return 50
    ratio = stat_p90 / threshold
    score = int(_clamp((ratio - 0.5) * 100.0 / 1.1, 0, 100))
    if stat_p90 >= threshold:
        codes_by_market = {
            MARKET_SHOTS_OVER: "HIGH_SHOT_VOLUME",
            MARKET_SOT_OVER: "HIGH_SOT_VOLUME",
            MARKET_FOULS_OVER: "HIGH_FOULS_DRAWN_VOLUME",
            MARKET_PASSES_OVER: "HIGH_PASS_VOLUME",
            MARKET_TACKLES_OVER: "HIGH_TACKLES_VOLUME",
        }
        rc = codes_by_market.get(market)
        if rc:
            reason_codes.append(rc)
    return score


def _matchup_component(
    market: str, ctx: dict, reason_codes: list[str]
) -> tuple[int, float]:
    if not isinstance(ctx, dict):
        ctx = {}
    allows_high_shots = bool(ctx.get("opponent_allows_high_shots"))
    allows_high_sot = bool(ctx.get("opponent_allows_high_sot"))
    commits_many_fouls = bool(ctx.get("opponent_commits_many_fouls"))
    high_possession = bool(ctx.get("opponent_allows_high_possession"))
    forces_saves = bool(ctx.get("opponent_forces_goalkeeper_saves"))
    pace_mult = _clamp(
        _safe(ctx.get("opponent_pace_mult")) or 1.0,
        MATCHUP_MULT_FLOOR,
        MATCHUP_MULT_CEIL,
    )
    card_mult = _clamp(
        _safe(ctx.get("opponent_card_proneness_mult")) or 1.0,
        MATCHUP_MULT_FLOOR,
        MATCHUP_MULT_CEIL,
    )
    press_mult = _clamp(
        _safe(ctx.get("opponent_press_mult")) or 1.0,
        MATCHUP_MULT_FLOOR,
        MATCHUP_MULT_CEIL,
    )

    if market == MARKET_SHOTS_OVER:
        mult = pace_mult
        score = 50 + (20 if allows_high_shots else 0) + int((mult - 1.0) * 100)
        if allows_high_shots:
            reason_codes.append("MATCHUP_ALLOWS_SHOTS")
    elif market == MARKET_SOT_OVER:
        mult = pace_mult
        score = 50 + (25 if allows_high_sot else 0) + int((mult - 1.0) * 100)
        if allows_high_sot:
            reason_codes.append("MATCHUP_ALLOWS_SOT")
    elif market == MARKET_FOULS_OVER:
        mult = card_mult
        score = 50 + (25 if commits_many_fouls else 0)
        if commits_many_fouls:
            reason_codes.append("MATCHUP_ALLOWS_FOULS")
    elif market == MARKET_PASSES_OVER:
        mult = 1.0
        score = 50 + (20 if high_possession else 0)
        if high_possession:
            reason_codes.append("MATCHUP_SUPPORTS_PASSES")
    elif market == MARKET_TACKLES_OVER:
        mult = press_mult
        score = 50 + (15 if pace_mult > 1.05 else 0)
    elif market == MARKET_CARDS_OVER:
        mult = card_mult
        score = 50 + (20 if commits_many_fouls else 0)
    elif market == MARKET_TO_SCORE:
        mult = pace_mult
        score = 50 + (20 if allows_high_sot else 0)
    else:
        mult = 1.0
        score = 50

    if forces_saves and market == "GK_SAVES_OVER":
        reason_codes.append("MATCHUP_FORCES_SAVES")
        score += 15
    score = int(_clamp(score, 0, 100))
    return score, mult


def _script_component(
    market: str, script: Optional[str], reason_codes: list[str]
) -> int:
    if not script:
        return 50
    s = script.upper().strip()
    if s == SCRIPT_CONTROLLED_FAVORITE and market in (
        MARKET_PASSES_OVER,
        MARKET_SHOTS_OVER,
    ):
        reason_codes.append(
            "SCRIPT_SUPPORTS_PASSES"
            if market == MARKET_PASSES_OVER
            else "SCRIPT_SUPPORTS_SHOTS"
        )
        return 80
    if s == SCRIPT_UNILATERAL_DOMINANCE and market in (
        MARKET_SHOTS_OVER,
        MARKET_SOT_OVER,
    ):
        reason_codes.append("SCRIPT_SUPPORTS_SHOTS")
        return 78
    if s == SCRIPT_BILATERAL_OPENNESS and market in (
        MARKET_SHOTS_OVER,
        MARKET_SOT_OVER,
        MARKET_TO_SCORE,
    ):
        reason_codes.append("SCRIPT_SUPPORTS_SHOTS")
        return 72
    if s == SCRIPT_LOW_EVENT_UNDER and market in (
        MARKET_SHOTS_OVER,
        MARKET_SOT_OVER,
        MARKET_TO_SCORE,
    ):
        reason_codes.append("SCRIPT_HURTS_AGGRESSIVE_PROPS")
        return 25
    if s == SCRIPT_LATE_SIEGE and market in (MARKET_SHOTS_OVER, MARKET_SOT_OVER):
        reason_codes.append("SCRIPT_SUPPORTS_SHOTS")
        return 75
    if s == SCRIPT_CHAOTIC_MATCH and market == MARKET_PASSES_OVER:
        return 35
    if s == SCRIPT_CHAOTIC_MATCH and market == "GK_SAVES_OVER":
        reason_codes.append("SCRIPT_SUPPORTS_SAVES")
        return 70
    return 50


def _safety_component(market: str, line: float, prob: float, source: str) -> int:
    score = 50
    if line is not None and line <= 1.0:
        score += 15
    elif line is not None and line >= 3.0:
        score -= 10
    if prob >= 0.70:
        score += 20
    elif prob >= 0.62:
        score += 12
    elif prob < 0.55:
        score -= 10
    if source and "statmuse" in source:
        score += 5
    if source == "understat":
        score -= 5
    if market == MARKET_TO_SCORE:
        score -= 15
    return int(_clamp(score, 0, 100))


def compute_player_prop_score(
    *,
    player: dict,
    market: str,
    stat_p90: Optional[float],
    matchup_context: dict,
    game_script: Optional[str],
    line: float,
    prob: float,
    source: str,
) -> tuple[int, list[str], float]:
    rcs: list[str] = []
    role_s = _role_minutes_component(player, rcs)
    vol_s = _volume_component(market, stat_p90, rcs)
    mu_s, mult = _matchup_component(market, matchup_context, rcs)
    script_s = _script_component(market, game_script, rcs)
    safety_s = _safety_component(market, line, prob, source)
    weighted = (
        role_s * W_ROLE
        + vol_s * W_VOLUME
        + mu_s * W_MATCHUP
        + script_s * W_SCRIPT
        + safety_s * W_SAFETY
    ) / 100.0
    score = int(round(_clamp(weighted, 0.0, 100.0)))
    return score, rcs, mult


def compute_player_prop_fragility(
    *,
    market: str,
    line: float,
    stat_p90: Optional[float],
    minutes_sample: Optional[int],
    minutes_projection: Optional[float],
    matchup_context: dict,
    game_script: Optional[str],
    confidence_penalty: int,
    rotation_risk: bool,
) -> int:
    base = FRAGILITY_BASELINE.get(market, 35)
    fragility = base
    if minutes_projection is not None and minutes_projection < 65:
        fragility += 10
    if rotation_risk:
        fragility += 8
    if minutes_sample is None or minutes_sample < 450:
        fragility += 8
    elif minutes_sample < 900:
        fragility += 4
    threshold = VOLUME_THRESHOLDS.get(market)
    if stat_p90 is not None and threshold and stat_p90 < threshold * 0.8:
        fragility += 10
    if isinstance(matchup_context, dict):
        if market in (MARKET_SHOTS_OVER, MARKET_SOT_OVER) and matchup_context.get(
            "opponent_suppresses_shots"
        ):
            fragility += 8
        if market == MARKET_PASSES_OVER and matchup_context.get(
            "opponent_presses_hard"
        ):
            fragility += 6
    if game_script == SCRIPT_LOW_EVENT_UNDER and market in (
        MARKET_SHOTS_OVER,
        MARKET_SOT_OVER,
        MARKET_TO_SCORE,
    ):
        fragility += 8
    if line is not None and line >= 3.0 and market != MARKET_PASSES_OVER:
        fragility += 8
    fragility += int(confidence_penalty or 0)
    if stat_p90 is not None and threshold and stat_p90 >= threshold * 1.3:
        fragility -= 6
    if line is not None and line <= 0.5:
        fragility -= 5
    if (
        isinstance(matchup_context, dict)
        and matchup_context.get("opponent_allows_high_shots")
        and market in (MARKET_SHOTS_OVER, MARKET_SOT_OVER)
    ):
        fragility -= 4
    return int(_clamp(fragility, 0, 100))


def _confidence_tier(score: int, fragility: int, market: str) -> str:
    tier = TIER_BY_MARKET.get(market, 2)
    if tier == 3:
        if score >= TIER3_SCORE_GATE and fragility <= TIER3_FRAGILITY_GATE:
            return "PREMIUM"
        return "AVOID"
    if score >= 85 and fragility <= 35:
        return "PREMIUM"
    if score >= MONEYBALL_SCORE_GATE and fragility <= MONEYBALL_FRAGILITY_GATE:
        return "VALUE"
    if score >= WATCHLIST_SCORE_FLOOR:
        return "WATCHLIST"
    return "AVOID"


def _passes_moneyball_filter(score: int, fragility: int, market: str) -> bool:
    tier = TIER_BY_MARKET.get(market, 2)
    if tier == 3:
        return score >= TIER3_SCORE_GATE and fragility <= TIER3_FRAGILITY_GATE
    return score >= MONEYBALL_SCORE_GATE and fragility <= MONEYBALL_FRAGILITY_GATE


_MARKET_LABEL_ES = {
    MARKET_SHOTS_OVER: "tiros totales",
    MARKET_SOT_OVER: "tiros al arco",
    MARKET_PASSES_OVER: "pases completados",
    MARKET_TACKLES_OVER: "entradas",
    MARKET_FOULS_OVER: "faltas",
    MARKET_CARDS_OVER: "tarjetas",
    MARKET_TO_SCORE: "anotar gol",
}


def _build_narrative(
    *, player_name: str, market: str, score: int, reason_codes: list[str]
) -> str:
    label = _MARKET_LABEL_ES.get(market, market.lower())
    bullets = []
    if "PLAYER_MINUTES_STABLE" in reason_codes:
        bullets.append("minutos estables")
    if "HIGH_SOT_VOLUME" in reason_codes or "HIGH_SHOT_VOLUME" in reason_codes:
        bullets.append("alto volumen de tiros")
    if "HIGH_PASS_VOLUME" in reason_codes:
        bullets.append("alto volumen de pases")
    if "MATCHUP_ALLOWS_SHOTS" in reason_codes or "MATCHUP_ALLOWS_SOT" in reason_codes:
        bullets.append("rival permite tiros")
    if "SCRIPT_SUPPORTS_SHOTS" in reason_codes:
        bullets.append("guion favorece ataques por banda")
    if "SCRIPT_SUPPORTS_PASSES" in reason_codes:
        bullets.append("guion de control de balón")
    extra = ", ".join(bullets) if bullets else "perfil de volumen sostenido"
    return f"{player_name}: {label} — score {score}/100. {extra}."


StatsFetcher = Callable[..., Awaitable[dict]]


async def discover_player_props(
    *,
    players: list[dict],
    matchup_context: Optional[dict] = None,
    book_lines: Optional[dict] = None,
    book_odds: Optional[dict] = None,
    markets: Optional[tuple[str, ...]] = None,
    stats_fetcher: Optional[StatsFetcher] = None,
    league: Optional[str] = None,
    game_script: Optional[str] = None,
) -> dict:
    """Discover player props. Returns dict with both ``top_player_props``
    (passes Moneyball filter) and ``props`` (all evaluated).
    """
    if not players:
        return {
            "available": False,
            "engine_version": ENGINE_VERSION,
            "top_player_props": [],
            "props": [],
            "skipped": [],
            "summary": {
                "tier_1": 0,
                "tier_2": 0,
                "tier_3": 0,
                "total": 0,
                "top_count": 0,
            },
            "_skipped_reason": "no_players",
        }

    book_lines = {**DEFAULT_LINES, **(book_lines or {})}
    book_odds = {**DEFAULT_ODDS, **(book_odds or {})}
    target_markets = tuple(markets) if markets else (TIER_1 + TIER_2 + TIER_3)
    matchup_context = matchup_context or {}

    if stats_fetcher is None:
        try:
            from services.football_player_stats_ingestor import hydrate_player_stats

            stats_fetcher = hydrate_player_stats
        except Exception as exc:  # noqa: BLE001
            log.warning("ingestor unavailable: %s", exc)
            return {
                "available": False,
                "engine_version": ENGINE_VERSION,
                "top_player_props": [],
                "props": [],
                "skipped": [],
                "summary": {
                    "tier_1": 0,
                    "tier_2": 0,
                    "tier_3": 0,
                    "total": 0,
                    "top_count": 0,
                },
                "_skipped_reason": "ingestor_unavailable",
            }

    props_out: list[dict] = []
    skipped: list[dict] = []

    for p in players:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or p.get("player_name") or "").strip()
        if not name:
            skipped.append({"reason": "missing_name", "player": p})
            continue

        try:
            stats_payload = await stats_fetcher(  # type: ignore[misc]
                player_name=name,
                team=p.get("team"),
                league=league,
            )
        except Exception as exc:  # noqa: BLE001
            skipped.append(
                {"player": name, "reason": "stats_fetcher_error", "detail": str(exc)}
            )
            continue

        if not isinstance(stats_payload, dict) or not stats_payload.get("available"):
            skipped.append(
                {
                    "player": name,
                    "reason": "stats_unavailable",
                    "source": (stats_payload or {}).get("source", "unknown"),
                }
            )
            continue

        stats = stats_payload.get("stats") or {}
        minutes_sample = stats_payload.get("minutes_sample")
        confidence_pen = int(stats_payload.get("confidence_penalty") or 0)
        source = stats_payload.get("source", "unknown")

        expected_minutes = _safe(p.get("expected_minutes"))
        if expected_minutes is None:
            role = (p.get("role") or "starter").lower()
            expected_minutes = (
                DEFAULT_MINUTES_STARTER
                if role.startswith("start")
                else DEFAULT_MINUTES_SUB
            )
        rotation_risk = bool(p.get("rotation_risk", False))
        minutes_projection = _safe(p.get("minutes_projection")) or expected_minutes

        for market in target_markets:
            stat_key = STAT_KEY_BY_MARKET.get(market)
            if not stat_key:
                continue
            stat_p90 = _safe(stats.get(stat_key))
            if stat_p90 is None or stat_p90 <= 0:
                continue

            line = float(book_lines.get(market, 0.5))
            odds = int(book_odds.get(market, -110))

            # First pass: compute mult and probability.
            _, _, mult = compute_player_prop_score(
                player=p,
                market=market,
                stat_p90=stat_p90,
                matchup_context=matchup_context,
                game_script=game_script,
                line=line,
                prob=0.0,
                source=source,
            )
            lam = stat_p90 * (expected_minutes / 90.0) * mult
            if lam <= 0:
                continue
            k_target = _line_k_target(line)
            prob = _poisson_p_ge(k_target, lam)
            implied = american_odds_to_implied(odds)
            edge_pts = (prob - implied) * 100.0

            if prob < LONGSHOT_PROB_FLOOR:
                skipped.append(
                    {
                        "player": name,
                        "market": market,
                        "reason": "BELOW_LONGSHOT_FLOOR",
                        "prob": round(prob, 3),
                    }
                )
                continue

            # Second pass: actual score with prob-aware safety.
            score, score_rcs, _ = compute_player_prop_score(
                player=p,
                market=market,
                stat_p90=stat_p90,
                matchup_context=matchup_context,
                game_script=game_script,
                line=line,
                prob=prob,
                source=source,
            )
            fragility = compute_player_prop_fragility(
                market=market,
                line=line,
                stat_p90=stat_p90,
                minutes_sample=minutes_sample,
                minutes_projection=minutes_projection,
                matchup_context=matchup_context,
                game_script=game_script,
                confidence_penalty=confidence_pen,
                rotation_risk=rotation_risk,
            )
            confidence_tier = _confidence_tier(score, fragility, market)
            tier_label = TIER_BY_MARKET.get(market, 2)
            passes_filter = _passes_moneyball_filter(score, fragility, market)
            narrative = _build_narrative(
                player_name=name,
                market=market,
                score=score,
                reason_codes=score_rcs,
            )

            props_out.append(
                {
                    "player": name,
                    "player_name": name,
                    "team": p.get("team"),
                    "market": market,
                    "tier": tier_label,
                    "line": line,
                    "selection": "OVER",
                    "book_odds_american": odds,
                    "lambda_estimate": round(lam, 3),
                    "model_probability": round(prob, 4),
                    "implied_probability": round(implied, 4),
                    "edge_points": round(edge_pts, 2),
                    "player_prop_score": score,
                    "player_prop_fragility": fragility,
                    "passes_moneyball_filter": passes_filter,
                    "edge_score": score,  # backward compat
                    "fragility": fragility,  # backward compat
                    "confidence_tier": confidence_tier,
                    "minutes_sample": minutes_sample,
                    "expected_minutes": expected_minutes,
                    "matchup_mult": round(mult, 3),
                    "data_source": source,
                    "data_confidence_penalty": confidence_pen,
                    "reason_codes": score_rcs
                    + [
                        f"TIER_{tier_label}",
                        f"SOURCE_{source.upper()}",
                        f"GATE_{confidence_tier}",
                    ],
                    "narrative_es": narrative,
                }
            )

    props_out.sort(
        key=lambda d: (
            -int(d["player_prop_score"]),
            int(d["player_prop_fragility"]),
            int(d["tier"]),
        )
    )
    top_props = [p for p in props_out if p.get("passes_moneyball_filter")]

    summary = {
        "tier_1": sum(1 for p in props_out if p["tier"] == 1),
        "tier_2": sum(1 for p in props_out if p["tier"] == 2),
        "tier_3": sum(1 for p in props_out if p["tier"] == 3),
        "total": len(props_out),
        "top_count": len(top_props),
    }

    return {
        "available": True,
        "engine_version": ENGINE_VERSION,
        "top_player_props": top_props,
        "props": props_out,
        "skipped": skipped,
        "summary": summary,
    }


__all__ = [
    "ENGINE_VERSION",
    "MONEYBALL_SCORE_GATE",
    "MONEYBALL_FRAGILITY_GATE",
    "TIER3_SCORE_GATE",
    "TIER3_FRAGILITY_GATE",
    "LONGSHOT_PROB_FLOOR",
    "TIER_1",
    "TIER_2",
    "TIER_3",
    "MARKET_SHOTS_OVER",
    "MARKET_SOT_OVER",
    "MARKET_PASSES_OVER",
    "MARKET_TACKLES_OVER",
    "MARKET_FOULS_OVER",
    "MARKET_CARDS_OVER",
    "MARKET_TO_SCORE",
    "DEFAULT_LINES",
    "DEFAULT_ODDS",
    "STAT_KEY_BY_MARKET",
    "VOLUME_THRESHOLDS",
    "FRAGILITY_BASELINE",
    "SCRIPT_CONTROLLED_FAVORITE",
    "SCRIPT_UNILATERAL_DOMINANCE",
    "SCRIPT_BILATERAL_OPENNESS",
    "SCRIPT_LOW_EVENT_UNDER",
    "SCRIPT_LATE_SIEGE",
    "SCRIPT_CHAOTIC_MATCH",
    "compute_player_prop_score",
    "compute_player_prop_fragility",
    "discover_player_props",
    "american_odds_to_implied",
]
