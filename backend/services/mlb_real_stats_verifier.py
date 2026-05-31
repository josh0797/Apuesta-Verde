"""
MLB Real-Stats Verifier (Module #4)

Checks the engine's `scoring_ctx` inputs against reality before a pick
is finalized. Detects "ghost edge" cases — where the model believes ER
is e.g. 6.9 but the active series has produced 15.0 runs per game.

Returns a confidence penalty in [0, 35] plus a structured discrepancy
list the UI can render.

Pure function — async only because the calling site is async; no I/O.
"""

from __future__ import annotations

from typing import Any, Optional


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def verify_model_inputs(
    db: Any,                       # kept for future async lookups
    scoring_ctx: dict,
    expected_runs: Optional[float],
    recommended_market: Optional[str],
) -> dict:
    discrepancies: list[dict] = []
    penalty = 0
    er = _num(expected_runs)

    # 1. Pitcher ERA real vs model.
    for side in ("home", "away"):
        p_real = (scoring_ctx.get(f"{side}_pitcher") or {}).get("era")
        p_model = scoring_ctx.get(f"model_era_{side}")
        rr, mm = _num(p_real), _num(p_model)
        if rr is not None and mm is not None and abs(rr - mm) > 0.75:
            discrepancies.append({
                "field":  f"{side}_pitcher_era",
                "model":  mm,
                "real":   rr,
                "delta":  round(rr - mm, 2),
            })
            penalty += 8

    # 2. Active-series H2H vs model ER.
    h2h = scoring_ctx.get("active_series_context") or {}
    h2h_avg = _num(h2h.get("total_runs_avg"))
    games_in_series = int(h2h.get("games_in_series") or 0)
    model_vs_reality_delta = None
    if h2h_avg is not None and games_in_series >= 2 and er is not None:
        model_vs_reality_delta = h2h_avg - er
        if model_vs_reality_delta > 3.0:
            discrepancies.append({
                "field":  "expected_runs_vs_h2h",
                "model":  round(er, 2),
                "real":   round(h2h_avg, 2),
                "delta":  round(model_vs_reality_delta, 2),
                "flag":   "MODEL_UNDERESTIMATES_SIGNIFICANTLY",
            })
            penalty += 20
        elif model_vs_reality_delta < -3.0:
            discrepancies.append({
                "field":  "expected_runs_vs_h2h",
                "model":  round(er, 2),
                "real":   round(h2h_avg, 2),
                "delta":  round(model_vs_reality_delta, 2),
                "flag":   "MODEL_OVERESTIMATES_SIGNIFICANTLY",
            })
            penalty += 12

    # 3. Home RPG real vs model.
    home_rpg_model = _num(scoring_ctx.get("home_runs_per_game_model"))
    home_rpg_real  = _num((scoring_ctx.get("home_batting") or {}).get("runs_per_game"))
    if home_rpg_real is not None and home_rpg_model is not None:
        if abs(home_rpg_real - home_rpg_model) > 0.8:
            discrepancies.append({
                "field":  "home_rpg",
                "model":  home_rpg_model,
                "real":   home_rpg_real,
                "delta":  round(home_rpg_real - home_rpg_model, 2),
            })
            penalty += 10

    # Aggregate.
    flag = "OK"
    if model_vs_reality_delta is not None:
        if model_vs_reality_delta > 2.0:
            flag = "UNDERESTIMATE"
        elif model_vs_reality_delta < -2.0:
            flag = "OVERESTIMATE"

    return {
        "inputs_verified":    len(discrepancies) == 0,
        "discrepancies":      discrepancies,
        "confidence_penalty": min(penalty, 35),
        "model_vs_reality": {
            "model_er":       round(er, 2) if er is not None else None,
            "reality_er_h2h": round(h2h_avg, 2) if h2h_avg is not None else None,
            "delta":          round(model_vs_reality_delta, 2)
                              if model_vs_reality_delta is not None else None,
            "flag":           flag,
        },
        "recommended_market": recommended_market,
    }


__all__ = ["verify_model_inputs"]
