"""MLB Pre-game Analytics Engine **v3 — Explainability & Game Script**.

This module is a *pure explainability layer* on top of v1+v2. It NEVER
touches the probability engine, the odds engine, the structural buckets,
the MLB router or the Moneyball guardrail. It only enriches each pick
with:

  1. ``generate_mlb_game_script(...)`` — narrative classifier:
        LOW_SCORING_PITCHERS_DUEL | OFFENSIVE_SHOOTOUT | FAVORITE_DOMINANCE
        | BULLPEN_BATTLE | UNDERDOG_CAN_COMPETE | PITCHER_MISMATCH
        | HIGH_VARIANCE_GAME | LOW_VARIANCE_GAME

  2. ``build_pitcher_block(...)`` — pitcher visibility (names + Quality
     Scores + top 2 ERA/xERA/FIP/WHIP rows for each side).

  3. ``build_why_this_pick(...)`` — 7-row checklist (Expected Runs, Line,
     Edge, Pitchers, Bullpen Stability, Park Factor, Offensive Outlook).

  4. ``build_confidence_breakdown(...)`` — decomposes the engine's final
     confidence number into Pitchers / Lineups / Bullpens / Park-Weather
     / Historical Matchup components. **Sum equals the displayed score.**

  5. ``generate_baseball_first_reasons(...)`` — replaces the generic
     "Lectura estructural detectada / Mercado rescatado / Línea óptima"
     with baseball-flavoured sentences anchored on the matchup data.

  6. ``apply_market_diversification(picks)`` — penalises the day's
     dominant market when alternative reads exist. Pure ranking helper:
     it does NOT mutate probability fields.

All functions are pure (dict in → dict out / list in → list out).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("mlb_pregame_analytics_v3")


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


def _round1(v: Any) -> float:
    return round(_f(v), 1)


# ════════════════════════════════════════════════════════════════════════════
# 1. GAME SCRIPT ENGINE
# ════════════════════════════════════════════════════════════════════════════
SCRIPT_LABELS_ES: dict[str, str] = {
    "LOW_SCORING_PITCHERS_DUEL": "Duelo de pitchers (poco scoring)",
    "OFFENSIVE_SHOOTOUT":        "Tiroteo ofensivo",
    "FAVORITE_DOMINANCE":        "Dominio del favorito",
    "BULLPEN_BATTLE":            "Batalla de bullpens",
    "UNDERDOG_CAN_COMPETE":      "Underdog competitivo",
    "PITCHER_MISMATCH":          "Mismatch de abridores",
    "HIGH_VARIANCE_GAME":        "Juego de alta varianza",
    "LOW_VARIANCE_GAME":         "Juego de baja varianza",
}


def generate_mlb_game_script(
    scoring_ctx: dict,
    v2_payload: dict,
    *,
    under_profile: Optional[dict] = None,
    nrfi: Optional[dict] = None,
    hist_profile: Optional[dict] = None,
) -> dict:
    """Classify the expected game script and produce a human narrative.

    Returns
    -------
    {
        "script_code": str,
        "label_es":    str,
        "narrative_es": str,
        "key_drivers": [str, ...],
        "expected_runs":    float | None,
        "projected_margin": float | None,
        "variance":         "LOW" | "MEDIUM" | "HIGH",
    }
    """
    scoring_ctx  = scoring_ctx  or {}
    v2_payload   = v2_payload   or {}
    under_profile = under_profile or (scoring_ctx.get("under_profile") or {})
    nrfi          = nrfi          or {}

    h_q  = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q  = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    park = scoring_ctx.get("park") or {}
    park_mult = _f(park.get("park_runs_mult"), 1.0)
    weather   = _f(park.get("weather_score"), 50)

    fav_bp_era_7d = _f(scoring_ctx.get("favorite_bullpen_era_7d"))
    und_bp_era_7d = _f(scoring_ctx.get("underdog_bullpen_era_7d"))

    expected_runs = v2_payload.get("expectedRuns") or v2_payload.get("expected_runs")
    margin_proj   = v2_payload.get("marginProjection")
    er_f  = _f(expected_runs, 9.0)
    mar_f = _f(margin_proj, 0.0)

    pcen = v2_payload.get("pitcherCentered") or {}
    mismatch  = bool(pcen.get("mismatch") or False)
    strong_edge = bool(pcen.get("strongEdge") or False)
    avg_off   = (off_h + off_a) / 2.0
    avg_pq    = (h_q + a_q) / 2.0

    drivers: list[str] = []
    if avg_pq >= 65:
        drivers.append("ambos abridores sólidos")
    if avg_pq <= 40:
        drivers.append("calidad combinada de abridores baja")
    if avg_off >= 60:
        drivers.append("ofensivas en buen momento")
    if avg_off <= 40:
        drivers.append("ofensivas con bajo OPS")
    if park_mult >= 1.07:
        drivers.append(f"parque hitter-friendly (×{park_mult:.2f})")
    elif park_mult <= 0.95:
        drivers.append(f"parque pitcher-friendly (×{park_mult:.2f})")
    if weather >= 70:
        drivers.append("clima caliente/viento a favor")
    elif weather <= 30:
        drivers.append("clima frío/viento en contra")
    if max(fav_bp_era_7d, und_bp_era_7d) >= 4.75:
        drivers.append("al menos un bullpen vulnerable")
    if mismatch:
        drivers.append("mismatch claro entre abridores")
    if strong_edge:
        drivers.append("ventaja fuerte de abridor")

    # Classification — strict order, first match wins.
    code: str
    if mismatch and mar_f >= 1.6:
        code = "PITCHER_MISMATCH"
    elif mar_f >= 1.8 and avg_pq >= 55:
        code = "FAVORITE_DOMINANCE"
    elif er_f <= 7.6 and avg_pq >= 60:
        code = "LOW_SCORING_PITCHERS_DUEL"
    elif er_f >= 9.6 and avg_off >= 55:
        code = "OFFENSIVE_SHOOTOUT"
    elif max(fav_bp_era_7d, und_bp_era_7d) >= 4.50 and 7.5 <= er_f <= 9.5:
        code = "BULLPEN_BATTLE"
    elif mar_f <= 1.0 and avg_pq >= 55:
        code = "UNDERDOG_CAN_COMPETE"
    elif er_f >= 9.0 and avg_pq <= 45:
        code = "HIGH_VARIANCE_GAME"
    else:
        code = "LOW_VARIANCE_GAME"

    # Variance — informal proxy for how "spread out" outcomes can be.
    if code in ("OFFENSIVE_SHOOTOUT", "BULLPEN_BATTLE", "HIGH_VARIANCE_GAME"):
        variance = "HIGH"
    elif code in ("LOW_SCORING_PITCHERS_DUEL", "LOW_VARIANCE_GAME"):
        variance = "LOW"
    else:
        variance = "MEDIUM"

    narrative = _build_script_narrative(code, er_f, mar_f, drivers,
                                         h_q, a_q, park_mult, weather,
                                         fav_bp_era_7d, und_bp_era_7d)

    return {
        "script_code":      code,
        "label_es":         SCRIPT_LABELS_ES.get(code, code),
        "narrative_es":     narrative,
        "key_drivers":      drivers,
        "expected_runs":    None if expected_runs is None else round(er_f, 1),
        "projected_margin": None if margin_proj is None else round(mar_f, 2),
        "variance":         variance,
    }


def _build_script_narrative(
    code: str, er: float, margin: float, drivers: list[str],
    h_q: float, a_q: float, park_mult: float, weather: float,
    fav_bp_era_7d: float, und_bp_era_7d: float,
) -> str:
    """Produce a 1-2 sentence narrative in Spanish for each script code."""
    if code == "LOW_SCORING_PITCHERS_DUEL":
        return (
            f"Juego pitcher-friendly esperado. Ambos abridores proyectan "
            f"ritmo de carreras bajo (combined quality {(h_q + a_q)/2:.0f}/100). "
            f"Expected runs ≈ {er:.1f}. Oportunidades de scoring limitadas."
        )
    if code == "OFFENSIVE_SHOOTOUT":
        return (
            f"Tiroteo ofensivo probable: expected runs ≈ {er:.1f}. "
            f"Calidad de abridores combinada ({(h_q + a_q)/2:.0f}/100) por debajo "
            f"de las dos ofensivas en forma. Park/clima refuerzan el Over."
        )
    if code == "FAVORITE_DOMINANCE":
        return (
            f"Modelo proyecta dominio del favorito (margen ≈ +{margin:.1f}). "
            f"Pitching + ofensiva del favorito superan al rival; "
            f"Run Line -1.5 con respaldo estructural."
        )
    if code == "BULLPEN_BATTLE":
        worst = max(fav_bp_era_7d, und_bp_era_7d)
        return (
            f"Batalla de bullpens esperada: expected runs ≈ {er:.1f} con al "
            f"menos un bullpen vulnerable (ERA 7d ≈ {worst:.2f}). "
            f"Innings medios/altos vulnerables; Overs/Team Totals viables."
        )
    if code == "UNDERDOG_CAN_COMPETE":
        return (
            f"Underdog competitivo: margen proyectado bajo ({margin:+.1f}) y "
            f"calidad de abridores pareja ({(h_q + a_q)/2:.0f}/100). "
            f"Run Line +1.5 atractivo; ML del favorito sin gran edge."
        )
    if code == "PITCHER_MISMATCH":
        edge_side = "home" if h_q > a_q else "away"
        return (
            f"Mismatch claro de abridores a favor de {edge_side} "
            f"({max(h_q, a_q):.0f} vs {min(h_q, a_q):.0f}). "
            f"Ventaja explotable en Run Line y Team Total Under del rival."
        )
    if code == "HIGH_VARIANCE_GAME":
        return (
            f"Juego de alta varianza: expected runs ≈ {er:.1f} pero la "
            f"calidad de abridores combinada es baja ({(h_q + a_q)/2:.0f}). "
            f"Picks frágiles; preferir líneas protegidas."
        )
    # LOW_VARIANCE_GAME / fallback
    return (
        f"Juego de baja varianza: expected runs ≈ {er:.1f}, margen ≈ {margin:+.1f}. "
        f"Sin un driver dominante; preferir mercados protegidos (líneas safe)."
    )


# ════════════════════════════════════════════════════════════════════════════
# 2. PITCHER VISIBILITY BLOCK
# ════════════════════════════════════════════════════════════════════════════
def build_pitcher_block(scoring_ctx: dict) -> dict:
    """Build the Starting Pitchers visualisation block.

    Returns
    -------
    {
      "home": {"name", "qualityScore", "team", "primary_stats": [{label, value}, ...]},
      "away": {"name", "qualityScore", "team", "primary_stats": [...]},
      "bothConfirmed": bool,
    }
    """
    home_p = scoring_ctx.get("home_pitcher_stats") or {}
    away_p = scoring_ctx.get("away_pitcher_stats") or {}
    home_team = (scoring_ctx.get("home_team") or {}).get("team_name") or scoring_ctx.get("home_team_name")
    away_team = (scoring_ctx.get("away_team") or {}).get("team_name") or scoring_ctx.get("away_team_name")
    h_q = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)

    def _primary(p: dict) -> list[dict]:
        """Pick the top 2 stats actually available, preferring xERA/FIP over ERA."""
        candidates: list[tuple[str, str, Any, str]] = [
            ("xERA", "xera",   p.get("xera"),   "{:.2f}"),
            ("FIP",  "fip",    p.get("fip"),    "{:.2f}"),
            ("ERA",  "era",    p.get("era"),    "{:.2f}"),
            ("WHIP", "whip",   p.get("whip"),   "{:.2f}"),
            ("K/9",  "k9",     p.get("k_per_9") or p.get("k9"), "{:.1f}"),
            ("BB/9", "bb9",    p.get("bb_per_9") or p.get("bb9"), "{:.1f}"),
        ]
        out: list[dict] = []
        for label, key, val, fmt in candidates:
            if val is None:
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            out.append({"label": label, "key": key, "value": fmt.format(fv)})
            if len(out) >= 2:
                break
        return out

    return {
        "home": {
            "name":           home_p.get("name") or scoring_ctx.get("home_pitcher_name"),
            "team":           home_team,
            "qualityScore":   round(h_q, 0),
            "primary_stats":  _primary(home_p),
            "throws":         home_p.get("throws_hand") or home_p.get("throws"),
        },
        "away": {
            "name":           away_p.get("name") or scoring_ctx.get("away_pitcher_name"),
            "team":           away_team,
            "qualityScore":   round(a_q, 0),
            "primary_stats":  _primary(away_p),
            "throws":         away_p.get("throws_hand") or away_p.get("throws"),
        },
        "bothConfirmed":     bool(home_p.get("name") and away_p.get("name")),
        "edgeSide":          "home" if h_q > a_q else ("away" if a_q > h_q else "tie"),
        "qualityDiff":       round(abs(h_q - a_q), 0),
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. WHY THIS PICK? CHECKLIST
# ════════════════════════════════════════════════════════════════════════════
def build_why_this_pick(
    scoring_ctx: dict,
    v2_payload: dict,
    chosen_market: Optional[dict] = None,
    pitcher_block: Optional[dict] = None,
    hist_profile: Optional[dict] = None,
) -> list[dict]:
    """Return the 6–9 row checklist explaining why this pick was chosen.

    Each row::
        {"label": str, "value": str, "tone": "positive"|"neutral"|"negative", "key": str}
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    rows: list[dict] = []

    er = v2_payload.get("expectedRuns") or v2_payload.get("expected_runs")
    rl = v2_payload.get("recommendedLine")
    edge_v = v2_payload.get("edgeVsLine")
    margin = v2_payload.get("marginProjection")
    park   = scoring_ctx.get("park") or {}
    park_mult = _f(park.get("park_runs_mult"), 1.0)
    weather   = _f(park.get("weather_score"), 50)
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    bp_score = _f((scoring_ctx.get("bullpen") or {}).get("score", 60), 60)
    fav_bp_era_7d = _f(scoring_ctx.get("favorite_bullpen_era_7d"))

    pitcher_block = pitcher_block or build_pitcher_block(scoring_ctx)

    # Expected Runs
    rows.append({
        "key":   "expected_runs",
        "label": "Expected Runs",
        "value": f"{_f(er, 0):.1f}" if er is not None else "—",
        "tone":  "neutral",
    })

    # Recommended Line
    if rl:
        rows.append({
            "key":   "line",
            "label": "Línea",
            "value": str(rl),
            "tone":  "neutral",
        })
    elif chosen_market and chosen_market.get("market"):
        rows.append({
            "key":   "line",
            "label": "Mercado",
            "value": str(chosen_market.get("market")),
            "tone":  "neutral",
        })

    # Edge vs line (or margin projection for Run Line)
    if edge_v is not None:
        rows.append({
            "key":   "edge",
            "label": "Edge",
            "value": f"{_f(edge_v):+.1f} carreras",
            "tone":  "positive" if abs(_f(edge_v)) >= 1.5 else "neutral",
        })
    elif margin is not None:
        rows.append({
            "key":   "edge",
            "label": "Margen proyectado",
            "value": f"{_f(margin):+.1f}",
            "tone":  "positive" if _f(margin) >= 1.8 else "neutral",
        })

    # Starting pitchers (always)
    h_name = pitcher_block.get("home", {}).get("name") or "—"
    a_name = pitcher_block.get("away", {}).get("name") or "—"
    h_q    = pitcher_block.get("home", {}).get("qualityScore") or 0
    a_q    = pitcher_block.get("away", {}).get("qualityScore") or 0
    rows.append({
        "key":   "pitchers",
        "label": "Abridores",
        "value": f"{a_name} ({a_q:.0f}) vs {h_name} ({h_q:.0f})",
        "tone":  "positive" if max(h_q, a_q) >= 65 else "neutral",
    })

    # Bullpen Stability
    bp_tone = "positive" if bp_score >= 65 and fav_bp_era_7d <= 4.20 else (
        "negative" if (bp_score < 50 or fav_bp_era_7d >= 4.75) else "neutral"
    )
    bp_label = (
        "Sobre el promedio" if bp_score >= 65 else
        "Promedio" if bp_score >= 45 else
        "Vulnerable"
    )
    rows.append({
        "key":   "bullpen",
        "label": "Estabilidad de bullpen",
        "value": bp_label + (f" (ERA7d {fav_bp_era_7d:.2f})" if fav_bp_era_7d else ""),
        "tone":  bp_tone,
    })

    # Park Factor
    park_label = (
        "Hitter-friendly" if park_mult >= 1.05 else
        "Pitcher-friendly" if park_mult <= 0.95 else
        "Neutral"
    )
    rows.append({
        "key":   "park",
        "label": "Park factor",
        "value": f"{park_label} (×{park_mult:.2f})",
        "tone":  "neutral",
    })

    # Weather (only when non-trivial)
    if weather >= 65 or weather <= 35:
        rows.append({
            "key":   "weather",
            "label": "Clima",
            "value": "Favorece Over" if weather >= 65 else "Favorece Under",
            "tone":  "neutral",
        })

    # Offensive outlook
    avg_off = (off_h + off_a) / 2.0
    off_label = (
        "Sobre el promedio" if avg_off >= 60 else
        "Promedio" if avg_off >= 45 else
        "Bajo el promedio"
    )
    rows.append({
        "key":   "offense",
        "label": "Outlook ofensivo",
        "value": off_label,
        "tone":  "neutral",
    })

    # Historical (optional)
    if hist_profile and hist_profile.get("available"):
        last_n = hist_profile.get("last_n_games") or hist_profile.get("lookback") or 15
        rows.append({
            "key":   "historical",
            "label": f"Historial últimos {last_n}",
            "value": "Disponible",
            "tone":  "positive",
        })

    return rows


# ════════════════════════════════════════════════════════════════════════════
# 4. CONFIDENCE BREAKDOWN
# ════════════════════════════════════════════════════════════════════════════
def build_confidence_breakdown(
    scoring_ctx: dict,
    v2_payload: dict,
    *,
    chosen_market: Optional[dict] = None,
    hist_profile: Optional[dict] = None,
    displayed_total: Optional[float] = None,
    survival_payload: Optional[dict] = None,
) -> dict:
    """Decompose the displayed confidence into named components.

    Components sum (after normalisation) to ``displayed_total`` so the user
    sees an honest breakdown. This is purely explanatory — it does NOT
    re-score the pick.

    Returns
    -------
    {
        "total":      float (0-100),
        "components": [{label, key, value, weight}, ...],
        "method":     "v3_explainability",
    }
    """
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}

    h_q  = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q  = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    bp   = _f((scoring_ctx.get("bullpen")      or {}).get("score", 60), 60)
    park = scoring_ctx.get("park") or {}
    park_mult = _f(park.get("park_runs_mult"), 1.0)
    weather   = _f(park.get("weather_score"), 50)
    fav_margin_rel = _f(
        (scoring_ctx.get("favorite_margin_profile") or {}).get("marginReliability"),
        0,
    )

    # MLB-V8 — Volatility Penalty: pitchers prone to early blowups reduce
    # confidence. We import lazily to avoid a hard dependency loop.
    volatility_penalty = 0.0
    volatility_meta = None
    try:
        from .mlb_live_intelligence import pitcher_volatility_score
        home_p_stats = scoring_ctx.get("home_pitcher_stats") or {}
        away_p_stats = scoring_ctx.get("away_pitcher_stats") or {}
        vol_h = pitcher_volatility_score(home_p_stats)
        vol_a = pitcher_volatility_score(away_p_stats)
        volatility_penalty = float(vol_h.get("penalty", 0) + vol_a.get("penalty", 0))
        volatility_meta = {
            "home_level":   vol_h.get("level"),
            "away_level":   vol_a.get("level"),
            "home_score":   vol_h.get("score"),
            "away_score":   vol_a.get("score"),
            "home_reasons": vol_h.get("reasons", [])[:2],
            "away_reasons": vol_a.get("reasons", [])[:2],
            "penalty":      volatility_penalty,
        }
    except Exception:
        volatility_penalty = 0.0
        volatility_meta = None

    # Raw component "strength" 0-100 each (heuristic, normalised below).
    pitchers_raw = (h_q + a_q) / 2.0
    lineups_raw  = (off_h + off_a) / 2.0
    bullpens_raw = bp
    park_raw     = min(100.0, max(0.0, 50.0 + (park_mult - 1.0) * 60.0 + (weather - 50) * 0.3))
    historical_raw = (
        fav_margin_rel
        if fav_margin_rel > 0 else
        (60.0 if (hist_profile or {}).get("available") else 0.0)
    )

    # Weights — must sum to 100. Calibrated to MLB importance ranking.
    weights = {
        "pitchers":   0.42,
        "lineups":    0.20,
        "bullpens":   0.16,
        "park":       0.12,
        "historical": 0.10,
    }

    raw_components = {
        "pitchers":   pitchers_raw,
        "lineups":    lineups_raw,
        "bullpens":   bullpens_raw,
        "park":       park_raw,
        "historical": historical_raw,
    }

    # Weighted sum: each component contributes (raw/100) * weight * 100.
    weighted = {k: raw_components[k] * weights[k] for k in weights}
    computed_total = sum(weighted.values())

    # If the orchestrator already exposes a displayed total, rescale so
    # the breakdown sums to it exactly (preserves UI honesty).
    if displayed_total is not None and computed_total > 0:
        scale = float(displayed_total) / computed_total
    else:
        scale = 1.0
    final_components = []
    label_map = {
        "pitchers":   "Pitchers",
        "lineups":    "Lineups",
        "bullpens":   "Bullpens",
        "park":       "Park/Weather",
        "historical": "Historial",
    }
    running_total = 0.0
    for k in ("pitchers", "lineups", "bullpens", "park", "historical"):
        v = round(weighted[k] * scale, 1)
        running_total += v
        final_components.append({
            "key":    k,
            "label":  label_map[k],
            "value":  v,
            "weight": round(weights[k] * 100, 0),
            "raw":    round(raw_components[k], 1),
        })

    # MLB-V8 — Append the Volatility Penalty as a negative-tone component
    # so the UI can render it in red and the user understands why pitcher
    # contribution was discounted. Subtractive: NOT scaled.
    if volatility_penalty > 0:
        final_components.append({
            "key":    "volatility_penalty",
            "label":  "Volatility Penalty",
            "value":  -round(volatility_penalty, 1),
            "weight": 0,
            "raw":    round(volatility_penalty, 1),
            "tone":   "negative",
        })

    # MLB-V10 — Script Survival contribution.
    # Positive when the script has high survival probability (≥ 70).
    # Negative when the script is fragile (< 45 survival).
    survival_contrib = 0.0
    survival_meta = None
    if survival_payload and isinstance(survival_payload, dict):
        survival_contrib = float(survival_payload.get("confidence_contribution") or 0)
        survival_meta = {
            "survival":      survival_payload.get("survival", {}).get("score"),
            "fragility":     survival_payload.get("fragility", {}).get("score"),
            "stability":     survival_payload.get("stability", {}).get("code"),
            "label_es":      survival_payload.get("stability", {}).get("label_es"),
        }
    if survival_contrib != 0:
        final_components.append({
            "key":    "script_survival",
            "label":  "Script Survival",
            "value":  round(survival_contrib, 1),
            "weight": 0,
            "raw":    survival_meta.get("survival") if survival_meta else None,
            "tone":   "positive" if survival_contrib > 0 else "negative",
        })

    final_total = (displayed_total if displayed_total is not None else computed_total) \
                  - volatility_penalty + survival_contrib
    final_total = max(0.0, min(100.0, final_total))

    return {
        "total":               round(final_total, 1),
        "raw_total":           round(displayed_total if displayed_total is not None else computed_total, 1),
        "volatility_penalty":  round(volatility_penalty, 1),
        "volatility_meta":     volatility_meta,
        "script_survival_contribution": round(survival_contrib, 1),
        "script_survival_meta":         survival_meta,
        "components":          final_components,
        "method":              "v3_explainability_v5_survival",
    }


# ════════════════════════════════════════════════════════════════════════════
# 5. BASEBALL-FIRST REASON GENERATOR
# ════════════════════════════════════════════════════════════════════════════
def generate_baseball_first_reasons(
    scoring_ctx: dict,
    v2_payload: dict,
    chosen_market: Optional[dict] = None,
    *,
    rescue: Optional[dict] = None,
    pitcher_block: Optional[dict] = None,
    script: Optional[dict] = None,
) -> list[str]:
    """Generate 3–5 baseball-first reason sentences for the recommendation.

    Replaces internal-engine concepts ("Lectura estructural detectada",
    "Mercado rescatado", "Línea óptima") with concrete baseball reasoning.
    """
    out: list[str] = []
    scoring_ctx = scoring_ctx or {}
    v2_payload  = v2_payload  or {}
    pitcher_block = pitcher_block or build_pitcher_block(scoring_ctx)
    script = script or {}

    market = ((chosen_market or {}).get("market") or v2_payload.get("recommendedLine") or "").lower()
    rec_line = v2_payload.get("recommendedLine") or ""
    er = v2_payload.get("expectedRuns") or v2_payload.get("expected_runs")
    margin = v2_payload.get("marginProjection")
    park_mult = _f((scoring_ctx.get("park") or {}).get("park_runs_mult"), 1.0)
    weather   = _f((scoring_ctx.get("park") or {}).get("weather_score"), 50)
    h_q  = _f((scoring_ctx.get("home_pitcher_quality") or {}).get("score", 50), 50)
    a_q  = _f((scoring_ctx.get("away_pitcher_quality") or {}).get("score", 50), 50)
    avg_pq = (h_q + a_q) / 2.0
    off_h = _f((scoring_ctx.get("offense_home") or {}).get("score", 50), 50)
    off_a = _f((scoring_ctx.get("offense_away") or {}).get("score", 50), 50)
    h_name = pitcher_block.get("home", {}).get("name") or "Home SP"
    a_name = pitcher_block.get("away", {}).get("name") or "Away SP"

    is_under = "under" in market or "under" in rec_line.lower()
    is_over  = "over"  in market or "over"  in rec_line.lower()
    is_runline = "run line" in market or "runline" in market

    # 1) Pitcher-anchored reason (always emitted when both confirmed).
    if pitcher_block.get("bothConfirmed"):
        if is_under and avg_pq >= 60:
            out.append(
                f"Ambos abridores suprimen contacto duro ({h_name} y {a_name}, "
                f"calidad combinada {avg_pq:.0f}/100): perfil sólido de Under."
            )
        elif is_runline and abs(h_q - a_q) >= 15:
            edge_side = "local" if h_q > a_q else "visitante"
            out.append(
                f"Mismatch claro de abridores a favor del {edge_side} "
                f"({max(h_q, a_q):.0f} vs {min(h_q, a_q):.0f}): el favorito "
                f"tiene márgenes reales, no sólo ganar por una."
            )
        elif is_over and avg_pq <= 45:
            out.append(
                f"Calidad combinada de abridores baja ({avg_pq:.0f}/100) — "
                f"ambas ofensivas pueden castigar pronto."
            )
        else:
            out.append(
                f"Abridores confirmados ({a_name} vs {h_name}) — el modelo "
                f"se ancla en pitching, no en cuotas."
            )

    # 2) Expected runs vs line reason (replaces "Línea óptima seleccionada").
    if er is not None and rec_line:
        if is_under:
            line_num = _extract_line_number(rec_line)
            if line_num is not None and _f(er) < line_num:
                out.append(
                    f"Carreras proyectadas ({_f(er):.1f}) bien por debajo "
                    f"de la línea seleccionada ({rec_line})."
                )
        elif is_over:
            line_num = _extract_line_number(rec_line)
            if line_num is not None and _f(er) > line_num:
                out.append(
                    f"Carreras proyectadas ({_f(er):.1f}) por encima de "
                    f"la línea ({rec_line}) con buffer de valor."
                )

    # 3) Park / weather reason.
    if is_under and (park_mult <= 0.97 or weather <= 35):
        out.append(
            "Parque y/o clima refuerzan el Under (park-friendly o viento en contra)."
        )
    elif is_over and (park_mult >= 1.05 or weather >= 65):
        out.append(
            f"Parque/clima refuerzan el Over (×{park_mult:.2f}, weather score {weather:.0f})."
        )

    # 4) Offensive outlook reason.
    if is_under and (off_h + off_a) / 2.0 <= 45:
        out.append(
            "Ambas ofensivas con OPS bajo en últimas 15 — poca proyección de explotar carreras."
        )
    elif is_over and (off_h + off_a) / 2.0 >= 60:
        out.append(
            "Ambas ofensivas en forma — el upside ofensivo justifica el Over."
        )

    # 5) Rescue context — replaces "Mercado rescatado".
    if rescue and (rescue.get("candidates") or rescue.get("market")):
        out.append(
            "El ML directo no tenía valor, pero un mercado alternativo "
            "(Under/Team Total/F5) sí mantiene edge basado en el matchup."
        )

    # 6) Margin reason for Run Line picks.
    if is_runline and margin is not None and _f(margin) >= 1.8:
        out.append(
            f"Margen proyectado ≈ {_f(margin):+.1f}: el favorito gana por 2+ con respaldo histórico."
        )

    # If we couldn't generate any baseball reason, fall back to the script narrative.
    if not out and script.get("narrative_es"):
        out.append(script["narrative_es"])

    return out[:5]


def _extract_line_number(s: str) -> Optional[float]:
    """Pull a numeric line from strings like 'Under 9.5' or 'OVER 7.0'."""
    if not s:
        return None
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", str(s))
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


# ════════════════════════════════════════════════════════════════════════════
# 6. MARKET DIVERSIFICATION
# ════════════════════════════════════════════════════════════════════════════
def apply_market_diversification(picks: list[dict]) -> list[dict]:
    """Annotate each pick with diversity metadata + propose alt market
    when the day's distribution is overly concentrated.

    Inputs
    ------
    picks : list of pick payloads (each must have at least
            ``recommendation.market`` and ``_mlb_script_v2`` blocks).

    Behaviour
    ---------
    - Builds a histogram of recommended markets across the day.
    - When a single normalised market accounts for ≥60% of picks AND there
      are ≥3 picks total, this function attaches a non-destructive
      ``diversity_meta`` block to each pick:
        {
          "dominant_market":      str,
          "dominant_share":       float (0-1),
          "is_dominant":          bool,
          "alt_suggestions":      [str, ...],
          "diversity_penalty":    int (0-15)   # subtractive ranking hint
        }
    - **Never mutates** probability, coverProbability or buckets.

    Returns the same list (with annotations).
    """
    if not picks:
        return picks

    def _norm_market(m: str) -> str:
        m = (m or "").lower().strip()
        if "run line +1.5" in m or "rl +1.5" in m or ("+1.5" in m and "run" in m):
            return "RUN_LINE_PLUS_1_5"
        if "run line -1.5" in m or "rl -1.5" in m or ("-1.5" in m and "run" in m):
            return "RUN_LINE_MINUS_1_5"
        if "under" in m:
            # bucket by line if present
            import re
            num = re.search(r"under\s*(\d+(?:\.\d+)?)", m)
            if num:
                return f"UNDER_{num.group(1)}"
            return "UNDER_TOTAL"
        if "over" in m:
            import re
            num = re.search(r"over\s*(\d+(?:\.\d+)?)", m)
            if num:
                return f"OVER_{num.group(1)}"
            return "OVER_TOTAL"
        if "f5" in m or "1st 5" in m or "first 5" in m:
            return "F5"
        if "team total" in m:
            return "TEAM_TOTAL"
        if "nrfi" in m:
            return "NRFI"
        if "yrfi" in m:
            return "YRFI"
        if "moneyline" in m or m.endswith(" ml") or m.startswith("ml "):
            return "MONEYLINE"
        return m.upper().replace(" ", "_") or "UNKNOWN"

    histogram: dict[str, int] = {}
    for p in picks:
        market = (p.get("recommendation") or {}).get("market") \
                 or (p.get("_mlb_script_v2") or {}).get("recommendedLine") \
                 or ""
        key = _norm_market(market)
        histogram[key] = histogram.get(key, 0) + 1

    total = len(picks)
    if total < 3 or not histogram:
        # Annotate with neutral diversity meta so the UI can read uniform shape.
        for p in picks:
            p.setdefault("_mlb_script_v3_diversity", {
                "dominant_market": None,
                "dominant_share":  0.0,
                "is_dominant":     False,
                "alt_suggestions": [],
                "diversity_penalty": 0,
            })
        return picks

    dominant_market, dominant_count = max(histogram.items(), key=lambda kv: kv[1])
    dominant_share = dominant_count / float(total)

    if dominant_share < 0.60:
        # Healthy diversification — no penalty.
        for p in picks:
            p["_mlb_script_v3_diversity"] = {
                "dominant_market": dominant_market,
                "dominant_share":  round(dominant_share, 2),
                "is_dominant":     False,
                "alt_suggestions": [],
                "diversity_penalty": 0,
            }
        return picks

    # Dominance detected — annotate every pick in the dominant bucket.
    alt_pool = ["F5 Under", "Team Total Under", "NRFI", "Moneyline", "Run Line +1.5", "Team Total Over"]
    for p in picks:
        market = (p.get("recommendation") or {}).get("market") \
                 or (p.get("_mlb_script_v2") or {}).get("recommendedLine") \
                 or ""
        key = _norm_market(market)
        is_dom = (key == dominant_market)
        penalty = 0
        if is_dom and dominant_share >= 0.80:
            penalty = 12
        elif is_dom:
            penalty = 6
        p["_mlb_script_v3_diversity"] = {
            "dominant_market": dominant_market,
            "dominant_share":  round(dominant_share, 2),
            "is_dominant":     is_dom,
            "alt_suggestions": [a for a in alt_pool if a.upper().replace(" ", "_") != dominant_market][:4] if is_dom else [],
            "diversity_penalty": penalty,
            "note_es": (
                f"{dominant_count}/{total} picks del día están en {dominant_market}. "
                f"Considerar alternativas estructuralmente justificadas."
            ) if is_dom else None,
        }
    return picks


# ════════════════════════════════════════════════════════════════════════════
# 7. CONVENIENCE: build the full v3 payload for a single pick
# ════════════════════════════════════════════════════════════════════════════
def build_v3_payload(
    scoring_ctx: dict,
    v2_payload: dict,
    *,
    chosen_market: Optional[dict] = None,
    under_profile: Optional[dict] = None,
    nrfi: Optional[dict] = None,
    rescue: Optional[dict] = None,
    hist_profile: Optional[dict] = None,
    displayed_total: Optional[float] = None,
    survival_payload: Optional[dict] = None,
) -> dict:
    """Combine all v3 helpers into a single per-pick payload."""
    script = generate_mlb_game_script(
        scoring_ctx, v2_payload,
        under_profile=under_profile, nrfi=nrfi, hist_profile=hist_profile,
    )
    pitchers = build_pitcher_block(scoring_ctx)
    why = build_why_this_pick(
        scoring_ctx, v2_payload,
        chosen_market=chosen_market, pitcher_block=pitchers, hist_profile=hist_profile,
    )
    breakdown = build_confidence_breakdown(
        scoring_ctx, v2_payload,
        chosen_market=chosen_market, hist_profile=hist_profile,
        displayed_total=displayed_total,
        survival_payload=survival_payload,
    )
    reasons = generate_baseball_first_reasons(
        scoring_ctx, v2_payload, chosen_market,
        rescue=rescue, pitcher_block=pitchers, script=script,
    )
    return {
        "script":               script,
        "pitchers_block":       pitchers,
        "why_this_pick":        why,
        "confidence_breakdown": breakdown,
        "baseball_reasons":     reasons,
        "version":              3,
    }


__all__ = [
    "generate_mlb_game_script",
    "build_pitcher_block",
    "build_why_this_pick",
    "build_confidence_breakdown",
    "generate_baseball_first_reasons",
    "apply_market_diversification",
    "build_v3_payload",
    "SCRIPT_LABELS_ES",
]
