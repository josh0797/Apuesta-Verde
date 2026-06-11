"""Football Player Props Discovery (Moneyball) — Phase F58.

Filosofía Moneyball
-------------------
Buscamos props **aburridos pero repetibles** (alto volumen / baja
fragilidad) y descartamos longshots populares. Estructura por tiers:

* **Tier 1 — Low Fragility (high volume rates)**::
      SHOTS_OVER, SOT_OVER, PASSES_OVER, TACKLES_OVER

* **Tier 2 — Moderate Fragility**::
      FOULS_OVER, CARDS_OVER

* **Tier 3 — High Fragility (only if elite edge)**::
      PLAYER_TO_SCORE  ← gate duro: ``edge_score ≥ 90`` y
                          ``fragility ≤ 35`` (configurable).

Cómo se computa la probabilidad
-------------------------------
Modelado Poisson sobre el conteo esperado en el partido::

    minutes_expected = minutes_p_game OR DEFAULT_MINUTES
    lambda           = stat_p90 * (minutes_expected / 90.0) * matchup_mult
    prob_over_line   = P(X >= ceil(line + 0.5))   # ≥ line+1 events

Para "PLAYER_TO_SCORE" usamos ``xg_p90`` como ``λ_goals_per_90`` con
``minutes_expected`` y la prob es ``P(X >= 1)``.

Gates Moneyball
---------------
* ``MONEYBALL_MIN_PROB    = 0.55``
* ``MONEYBALL_MIN_EDGE    = 4.0``  (edge_points = (prob − implied) × 100)
* ``LONGSHOT_PROB_FLOOR   = 0.50`` (rechaza props con prob por debajo de 0.50)
* Tier 3 ``PLAYER_TO_SCORE``:
    - ``edge_score >= TIER3_MIN_EDGE_SCORE`` (90)
    - ``fragility  <= TIER3_MAX_FRAGILITY`` (35)

Fail-soft
---------
* Si ``hydrate_player_stats`` retorna ``available=False`` o stats vacías
  → skip jugador (no rompe).
* Si una métrica para Tier 1 falta → ese mercado se salta para el jugador.
* Lista vacía siempre es válida.

Integración
-----------
El caller (orchestrator) llama a ``discover_player_props(...)`` con la
lista de jugadores titulares (``home`` + ``away``), el ingestor por
defecto se usa o puede inyectarse otro vía ``stats_fetcher``.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("football_player_props_discovery")

ENGINE_VERSION = "football_player_props_discovery.v1"


# ─────────────────────────────────────────────────────────────────────
# Markets
# ─────────────────────────────────────────────────────────────────────
MARKET_SHOTS_OVER   = "SHOTS_OVER"
MARKET_SOT_OVER     = "SOT_OVER"
MARKET_PASSES_OVER  = "PASSES_OVER"
MARKET_TACKLES_OVER = "TACKLES_OVER"
MARKET_FOULS_OVER   = "FOULS_OVER"
MARKET_CARDS_OVER   = "CARDS_OVER"
MARKET_TO_SCORE     = "PLAYER_TO_SCORE"

TIER_1 = (MARKET_SHOTS_OVER, MARKET_SOT_OVER, MARKET_PASSES_OVER, MARKET_TACKLES_OVER)
TIER_2 = (MARKET_FOULS_OVER, MARKET_CARDS_OVER)
TIER_3 = (MARKET_TO_SCORE,)

TIER_BY_MARKET = {m: 1 for m in TIER_1}
TIER_BY_MARKET.update({m: 2 for m in TIER_2})
TIER_BY_MARKET.update({m: 3 for m in TIER_3})

# Default book lines (cuando el caller no provee líneas reales)
DEFAULT_LINES = {
    MARKET_SHOTS_OVER:   1.5,
    MARKET_SOT_OVER:     0.5,
    MARKET_PASSES_OVER:  39.5,
    MARKET_TACKLES_OVER: 1.5,
    MARKET_FOULS_OVER:   1.5,
    MARKET_CARDS_OVER:   0.5,
    MARKET_TO_SCORE:     0.5,
}

# Default American odds (anchors realistas)
DEFAULT_ODDS = {
    MARKET_SHOTS_OVER:   -120,
    MARKET_SOT_OVER:     -110,
    MARKET_PASSES_OVER:  -115,
    MARKET_TACKLES_OVER: -110,
    MARKET_FOULS_OVER:   -120,
    MARKET_CARDS_OVER:   +180,
    MARKET_TO_SCORE:     +350,
}

# Stat key per market (en el dict de stats per-90 del ingestor)
STAT_KEY_BY_MARKET = {
    MARKET_SHOTS_OVER:   "shots_p90",
    MARKET_SOT_OVER:     "sot_p90",
    MARKET_PASSES_OVER:  "passes_p90",
    MARKET_TACKLES_OVER: "tackles_p90",
    MARKET_FOULS_OVER:   "fouls_p90",
    MARKET_CARDS_OVER:   "cards_p90",
    MARKET_TO_SCORE:     "xg_p90",
}

# Moneyball gates
MONEYBALL_MIN_PROB   = 0.55
MONEYBALL_MIN_EDGE   = 4.0   # edge_points
LONGSHOT_PROB_FLOOR  = 0.50

# Tier 3 gate (PLAYER_TO_SCORE)
TIER3_MIN_EDGE_SCORE = 90
TIER3_MAX_FRAGILITY  = 35

# Minutes assumptions
DEFAULT_MINUTES_STARTER = 78    # titular promedio que sale antes del final
DEFAULT_MINUTES_SUB     = 22

# Matchup multiplier window (proteger contra outliers)
MATCHUP_MULT_FLOOR = 0.75
MATCHUP_MULT_CEIL  = 1.25

# Edge score tuning
EDGE_SCORE_MAX = 100


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
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
        return math.exp(-mu) * (mu ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _poisson_p_ge(k_target: int, mu: float, max_k: int = 60) -> float:
    """Return P(X >= k_target) for Poisson(mu)."""
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
    """Convert American odds to implied probability (0..1)."""
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
    """Convierte la línea (e.g. 1.5) en el k_target para P(X >= k).

    Para una línea X.5 → k_target = ceil(X.5) = X+1.
    Para una línea entera N → tratamos como X.5 = N-0.5 ⇒ k_target = N.
    """
    if line is None:
        return 1
    if line < 0:
        return 0
    return int(math.ceil(line + 1e-9)) if (line - int(line) > 0) else int(line)


# ─────────────────────────────────────────────────────────────────────
# Core scoring
# ─────────────────────────────────────────────────────────────────────
def _compute_edge_score(prob: float, edge_pts: float) -> int:
    """Score 0..100 que mezcla probabilidad pura y edge contra implied.

    Diseño:
      * 60% peso a la probabilidad (escalada 0.50..0.85 → 0..60)
      * 40% peso al edge_pts (escalado 0..20 → 0..40)
    """
    prob_norm = _clamp((prob - 0.50) / 0.35, 0.0, 1.0)  # 0.50→0, 0.85→1.0
    edge_norm = _clamp(edge_pts / 20.0, 0.0, 1.0)
    raw = prob_norm * 60.0 + edge_norm * 40.0
    return int(round(_clamp(raw, 0.0, float(EDGE_SCORE_MAX))))


def _compute_fragility(
    *,
    market: str,
    prob: float,
    minutes_sample: Optional[int],
    matchup_mult: float,
    confidence_penalty: int,
) -> int:
    """Fragility 0..100 (más alto = menos confianza).

    * Tier base:
        Tier 1 → 20
        Tier 2 → 35
        Tier 3 → 55
    * Penalty si minutes_sample bajo (<450 minutos).
    * Penalty por matchup_mult lejos de 1.0.
    * Penalty del ingestor (fallback / muestra escasa).
    * Bonus (resta) si prob > 0.65.
    """
    tier = TIER_BY_MARKET.get(market, 2)
    base = {1: 20, 2: 35, 3: 55}.get(tier, 35)

    sample_pen = 0
    if minutes_sample is None or minutes_sample < 450:
        sample_pen = 10
    elif minutes_sample < 900:
        sample_pen = 5

    mu_pen = int(round(abs(matchup_mult - 1.0) * 30.0))
    mu_pen = min(mu_pen, 12)

    prob_bonus = 0
    if prob >= 0.70:
        prob_bonus = -8
    elif prob >= 0.65:
        prob_bonus = -4

    total = base + sample_pen + mu_pen + int(confidence_penalty or 0) + prob_bonus
    return int(_clamp(total, 0.0, 100.0))


def _confidence_tier(edge_score: int, fragility: int) -> str:
    if edge_score >= 80 and fragility <= 30:
        return "PREMIUM"
    if edge_score >= 65 and fragility <= 45:
        return "VALUE"
    if edge_score >= 50:
        return "LEAN"
    return "WATCH"


def _narrative_es(
    *,
    player_name: str,
    market: str,
    line: float,
    selection: str,
    lambda_estimate: float,
    prob: float,
    edge_pts: float,
) -> str:
    market_label = {
        MARKET_SHOTS_OVER:   "tiros totales",
        MARKET_SOT_OVER:     "tiros al arco",
        MARKET_PASSES_OVER:  "pases completados",
        MARKET_TACKLES_OVER: "entradas",
        MARKET_FOULS_OVER:   "faltas cometidas",
        MARKET_CARDS_OVER:   "tarjetas",
        MARKET_TO_SCORE:     "anotar gol",
    }.get(market, market.lower())
    return (
        f"{player_name}: modelo proyecta λ≈{lambda_estimate:.2f} para "
        f"{market_label} sobre línea {line:g} ({selection}). "
        f"Prob {prob*100:.1f}% / edge +{edge_pts:.1f}pts."
    )


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
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
) -> dict:
    """Genera la lista de props recomendados.

    Parameters
    ----------
    players
        Lista de dicts con al menos ``name`` (str). Opcional:
        ``team``, ``role`` (``"starter"``/``"sub"``), ``expected_minutes``.
    matchup_context
        Dict opcional con ``opponent_pace_mult`` (∈[0.75, 1.25]),
        ``opponent_card_proneness_mult``, etc. Se usa para el ``matchup_mult``.
    book_lines / book_odds
        Mapping market → línea / American odds. Si no se provee, usa
        ``DEFAULT_LINES`` / ``DEFAULT_ODDS``.
    markets
        Tuple de mercados a evaluar. Por defecto Tier 1 + Tier 2 + Tier 3.
    stats_fetcher
        Awaitable que retorna stats per-90 con el shape de
        :func:`services.football_player_stats_ingestor.hydrate_player_stats`.
        Por defecto se importa el ingestor real (fail-soft).
    league
        Liga del partido — se pasa al ingestor para mejorar accuracy.

    Returns
    -------
    Dict con::

        {
            "available":          bool,
            "engine_version":     str,
            "props":              [dict, ...],   # ordenado por edge_score desc
            "skipped":            [dict, ...],   # auditoría
            "summary":            {tier_1: int, tier_2: int, tier_3: int, total: int},
        }
    """
    if not players:
        return {
            "available":      False,
            "engine_version": ENGINE_VERSION,
            "props":          [],
            "skipped":        [],
            "summary":        {"tier_1": 0, "tier_2": 0, "tier_3": 0, "total": 0},
            "_skipped_reason": "no_players",
        }

    book_lines = {**DEFAULT_LINES, **(book_lines or {})}
    book_odds  = {**DEFAULT_ODDS,  **(book_odds  or {})}
    target_markets = tuple(markets) if markets else (TIER_1 + TIER_2 + TIER_3)

    # Resolver stats_fetcher por defecto.
    if stats_fetcher is None:
        try:
            from services.football_player_stats_ingestor import hydrate_player_stats
            stats_fetcher = hydrate_player_stats
        except Exception as exc:  # noqa: BLE001
            log.warning("ingestor unavailable, returning empty: %s", exc)
            return {
                "available":      False,
                "engine_version": ENGINE_VERSION,
                "props":          [],
                "skipped":        [],
                "summary":        {"tier_1": 0, "tier_2": 0, "tier_3": 0, "total": 0},
                "_skipped_reason": "ingestor_unavailable",
            }

    # Matchup multipliers
    ctx = matchup_context or {}
    pace_mult        = _clamp(_safe(ctx.get("opponent_pace_mult")) or 1.0, MATCHUP_MULT_FLOOR, MATCHUP_MULT_CEIL)
    card_mult        = _clamp(_safe(ctx.get("opponent_card_proneness_mult")) or 1.0, MATCHUP_MULT_FLOOR, MATCHUP_MULT_CEIL)
    defensive_mult   = _clamp(_safe(ctx.get("opponent_press_mult")) or 1.0, MATCHUP_MULT_FLOOR, MATCHUP_MULT_CEIL)

    def _market_mult(m: str) -> float:
        if m in (MARKET_SHOTS_OVER, MARKET_SOT_OVER, MARKET_TO_SCORE):
            return pace_mult
        if m == MARKET_CARDS_OVER:
            return card_mult
        if m == MARKET_TACKLES_OVER:
            return defensive_mult
        return 1.0

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
            log.debug("stats_fetcher failed for %s: %s", name, exc)
            skipped.append({"player": name, "reason": "stats_fetcher_error", "detail": str(exc)})
            continue

        if not isinstance(stats_payload, dict) or not stats_payload.get("available"):
            skipped.append({
                "player": name,
                "reason": "stats_unavailable",
                "source": (stats_payload or {}).get("source", "unknown"),
            })
            continue

        stats          = stats_payload.get("stats") or {}
        minutes_sample = stats_payload.get("minutes_sample")
        confidence_pen = int(stats_payload.get("confidence_penalty") or 0)
        source         = stats_payload.get("source", "unknown")

        # Estimar minutos esperados del partido.
        expected_minutes = _safe(p.get("expected_minutes"))
        if expected_minutes is None:
            role = (p.get("role") or "starter").lower()
            expected_minutes = (
                DEFAULT_MINUTES_STARTER if role.startswith("start") else DEFAULT_MINUTES_SUB
            )

        for market in target_markets:
            stat_key = STAT_KEY_BY_MARKET.get(market)
            if not stat_key:
                continue
            stat_p90 = _safe(stats.get(stat_key))
            if stat_p90 is None or stat_p90 <= 0:
                continue

            line = float(book_lines.get(market, 0.5))
            odds = int(book_odds.get(market, -110))
            mult = _market_mult(market)
            lam = stat_p90 * (expected_minutes / 90.0) * mult
            if lam <= 0:
                continue

            k_target = _line_k_target(line)
            prob = _poisson_p_ge(k_target, lam)
            implied = american_odds_to_implied(odds)
            edge_pts = (prob - implied) * 100.0

            # Hard Moneyball gates
            if prob < LONGSHOT_PROB_FLOOR:
                skipped.append({"player": name, "market": market, "reason": "BELOW_LONGSHOT_FLOOR",
                                "prob": round(prob, 3)})
                continue
            if prob < MONEYBALL_MIN_PROB:
                skipped.append({"player": name, "market": market, "reason": "BELOW_MIN_PROB",
                                "prob": round(prob, 3)})
                continue
            if edge_pts < MONEYBALL_MIN_EDGE:
                skipped.append({"player": name, "market": market, "reason": "BELOW_MIN_EDGE",
                                "edge": round(edge_pts, 2)})
                continue

            edge_score = _compute_edge_score(prob, edge_pts)
            fragility = _compute_fragility(
                market=market, prob=prob,
                minutes_sample=minutes_sample,
                matchup_mult=mult,
                confidence_penalty=confidence_pen,
            )

            # Tier 3 hard gate
            if market == MARKET_TO_SCORE:
                if edge_score < TIER3_MIN_EDGE_SCORE or fragility > TIER3_MAX_FRAGILITY:
                    skipped.append({
                        "player": name, "market": market,
                        "reason": "TIER3_GATE_NOT_MET",
                        "edge_score": edge_score, "fragility": fragility,
                    })
                    continue

            tier_label = TIER_BY_MARKET.get(market, 2)
            confidence_tier = _confidence_tier(edge_score, fragility)

            props_out.append({
                "player_name":       name,
                "team":              p.get("team"),
                "market":            market,
                "tier":              tier_label,
                "line":              line,
                "selection":         "OVER",
                "book_odds_american": odds,
                "lambda_estimate":   round(lam, 3),
                "model_probability": round(prob, 4),
                "implied_probability": round(implied, 4),
                "edge_points":       round(edge_pts, 2),
                "edge_score":        edge_score,
                "fragility":         fragility,
                "confidence_tier":   confidence_tier,
                "minutes_sample":    minutes_sample,
                "expected_minutes":  expected_minutes,
                "matchup_mult":      round(mult, 3),
                "data_source":       source,
                "data_confidence_penalty": confidence_pen,
                "reason_codes":      [
                    f"MONEYBALL_{confidence_tier}",
                    f"TIER_{tier_label}",
                    f"SOURCE_{source.upper()}",
                ],
                "narrative_es":      _narrative_es(
                    player_name=name, market=market, line=line,
                    selection="OVER", lambda_estimate=lam,
                    prob=prob, edge_pts=edge_pts,
                ),
            })

    # Orden final: por edge_score desc → menor fragilidad → tier asc
    props_out.sort(
        key=lambda d: (-int(d["edge_score"]), int(d["fragility"]), int(d["tier"]))
    )

    summary = {
        "tier_1": sum(1 for p in props_out if p["tier"] == 1),
        "tier_2": sum(1 for p in props_out if p["tier"] == 2),
        "tier_3": sum(1 for p in props_out if p["tier"] == 3),
        "total":  len(props_out),
    }

    return {
        "available":      True,
        "engine_version": ENGINE_VERSION,
        "props":          props_out,
        "skipped":        skipped,
        "summary":        summary,
    }


__all__ = [
    "ENGINE_VERSION",
    "MONEYBALL_MIN_PROB",
    "MONEYBALL_MIN_EDGE",
    "LONGSHOT_PROB_FLOOR",
    "TIER3_MIN_EDGE_SCORE",
    "TIER3_MAX_FRAGILITY",
    "MARKET_SHOTS_OVER", "MARKET_SOT_OVER", "MARKET_PASSES_OVER",
    "MARKET_TACKLES_OVER", "MARKET_FOULS_OVER", "MARKET_CARDS_OVER",
    "MARKET_TO_SCORE",
    "TIER_1", "TIER_2", "TIER_3",
    "DEFAULT_LINES", "DEFAULT_ODDS",
    "STAT_KEY_BY_MARKET",
    "discover_player_props",
    "american_odds_to_implied",
]
