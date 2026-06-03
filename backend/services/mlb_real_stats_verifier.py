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
    *,
    recent_run_split: Optional[dict] = None,
    on_base_profile:  Optional[dict] = None,
    f5_split:         Optional[dict] = None,
    advanced_stats_snapshot: Optional[dict] = None,
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

    # 4. Ghost-edge detection L5 vs L15 (2026-06 layer).
    # Compares the engine's `expected_runs` projection against the
    # team's *actual* recent run totals. If the recent L5 average is
    # several runs above the projection — and the pick is an Under —
    # this is a textbook ghost-edge case the verifier must flag.
    rrs = recent_run_split or {}
    total_l5  = _num(rrs.get("total_runs_avg_last_5"))
    total_l15 = _num(rrs.get("total_runs_avg_last_15"))
    rec_lc = (recommended_market or "").lower()
    is_under = "under" in rec_lc or "menos de" in rec_lc
    is_over  = "over"  in rec_lc or "más de"   in rec_lc or "mas de" in rec_lc
    if er is not None and total_l5 is not None:
        delta_l5 = round(total_l5 - er, 2)
        if is_under and delta_l5 >= 2.5:
            discrepancies.append({
                "field":  "expected_runs_vs_recent_l5",
                "model":  round(er, 2),
                "real":   total_l5,
                "delta":  delta_l5,
                "flag":   "GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING",
            })
            penalty += 18
        elif is_over and delta_l5 <= -2.5:
            discrepancies.append({
                "field":  "expected_runs_vs_recent_l5",
                "model":  round(er, 2),
                "real":   total_l5,
                "delta":  delta_l5,
                "flag":   "GHOST_EDGE_OVER_VS_L5_LOW_SCORING",
            })
            penalty += 14
    # Cross-check L5 trend vs L15 — if L5 contradicts the pick direction
    # by a wider margin than L15, the pick is fighting recent momentum.
    if total_l5 is not None and total_l15 is not None:
        delta_l5_l15 = round(total_l5 - total_l15, 2)
        if is_under and delta_l5_l15 >= 2.0:
            discrepancies.append({
                "field":  "recent_run_trend",
                "model":  round(total_l15, 2),
                "real":   total_l5,
                "delta":  delta_l5_l15,
                "flag":   "RECENT_RUN_TREND_CONTRADICTS_UNDER",
            })
            penalty += 8
        elif is_over and delta_l5_l15 <= -2.0:
            discrepancies.append({
                "field":  "recent_run_trend",
                "model":  round(total_l15, 2),
                "real":   total_l5,
                "delta":  delta_l5_l15,
                "flag":   "RECENT_RUN_TREND_CONTRADICTS_OVER",
            })
            penalty += 8

    # 5. F5 ghost-edge — same idea but for the first-5-innings market.
    f5c = (f5_split or {}).get("combined") or {}
    f5_l5  = _num(f5c.get("f5_runs_avg_last_5"))
    if er is not None and f5_l5 is not None and ("f5" in rec_lc or "first 5" in rec_lc or "1st 5" in rec_lc):
        # F5 expected runs ≈ er * 0.55 (rough conversion).
        f5_expected = er * 0.55
        f5_delta = round(f5_l5 - f5_expected, 2)
        if is_under and f5_delta >= 1.2:
            discrepancies.append({
                "field":  "f5_expected_vs_l5",
                "model":  round(f5_expected, 2),
                "real":   f5_l5,
                "delta":  f5_delta,
                "flag":   "GHOST_EDGE_F5_UNDER_VS_L5",
            })
            penalty += 12

    # 6. On-base pressure — if recent TOB is rising sharply and the
    # model still projects a low scoring environment, flag.
    obc = (on_base_profile or {}).get("combined") or {}
    tob_delta = _num(obc.get("times_on_base_delta_5_vs_15"))
    if er is not None and tob_delta is not None and tob_delta >= 2.5 and is_under:
        discrepancies.append({
            "field":  "on_base_pressure_trend",
            "model":  round(er, 2),
            "real":   tob_delta,
            "delta":  tob_delta,
            "flag":   "GHOST_EDGE_RISING_ON_BASE_VS_UNDER",
        })
        penalty += 10

    # 7. Statcast ghost-edge — xERA / xwOBA vs ERA / wOBA gap (Phase 11).
    # When ERA "looks" different from xERA, the engine should weight the
    # pick accordingly. ``advanced_stats_snapshot`` carries home_pitcher /
    # away_pitcher blocks with statcast metrics.
    snap = advanced_stats_snapshot or {}
    if isinstance(snap, dict) and snap:
        for side in ("home", "away"):
            block = snap.get(f"{side}_pitcher_advanced") or {}
            pdata = (block.get("pitcher") or {}) if isinstance(block, dict) else {}
            p_era  = _num(pdata.get("era"))
            p_xera = _num(pdata.get("xera"))
            p_xwoba = _num(pdata.get("xwoba_allowed"))
            p_barrel = _num(pdata.get("barrel_pct_allowed"))
            p_hard   = _num(pdata.get("hard_hit_pct_allowed"))

            # 7a. xERA much WORSE than ERA → pitcher running hot/lucky.
            #     ERA looks good (low) but true skill is worse (high xERA).
            #     "ERA UNDERSTATES the real RISK" → ``ERA_UNDERSTATES_RISK``.
            #     Under pick is at risk because skill says more runs are coming.
            if p_era is not None and p_xera is not None:
                era_xera_gap = round(p_era - p_xera, 2)
                if era_xera_gap <= -0.60:
                    discrepancies.append({
                        "field":  f"{side}_pitcher_era_vs_xera",
                        "model":  p_era,
                        "real":   p_xera,
                        "delta":  era_xera_gap,
                        "flag":   "ERA_UNDERSTATES_RISK",
                    })
                    if is_under:
                        penalty += 12
                # 7b. xERA much BETTER than ERA → pitcher running unlucky.
                #     ERA looks bad (high) but xERA says skill is great.
                #     For OVER picks this is a ghost-edge: Over is fighting
                #     underlying skill.
                elif era_xera_gap >= 0.60:
                    discrepancies.append({
                        "field":  f"{side}_pitcher_era_vs_xera",
                        "model":  p_era,
                        "real":   p_xera,
                        "delta":  era_xera_gap,
                        "flag":   "ERA_OVERSTATES_RISK",
                    })
                    if is_over:
                        penalty += 10

            # 7c. xwoba_allowed elevated — explosive contact risk for Under.
            if p_xwoba is not None and p_xwoba >= 0.345 and is_under:
                discrepancies.append({
                    "field":  f"{side}_pitcher_xwoba_allowed",
                    "model":  None,
                    "real":   p_xwoba,
                    "delta":  None,
                    "flag":   "PITCHER_XWOBA_WARNING",
                })
                penalty += 8

            # 7d. Hard contact / barrel elevated — under penalty.
            if (
                (p_barrel is not None and p_barrel >= 9.0)
                or (p_hard is not None and p_hard >= 42.0)
            ) and is_under:
                discrepancies.append({
                    "field":  f"{side}_pitcher_hard_contact",
                    "model":  None,
                    "real":   p_barrel if p_barrel is not None else p_hard,
                    "delta":  None,
                    "flag":   "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
                })
                penalty += 8

        # 7e. Team xwOBA — both teams elevated → over support / under risk.
        h_team = (snap.get("home_team_advanced") or {}).get("team") or {}
        a_team = (snap.get("away_team_advanced") or {}).get("team") or {}
        if isinstance(h_team, dict) and isinstance(a_team, dict):
            h_xw = _num(h_team.get("team_xwoba"))
            a_xw = _num(a_team.get("team_xwoba"))
            if (h_xw is not None and a_xw is not None
                    and h_xw >= 0.330 and a_xw >= 0.330 and is_under):
                discrepancies.append({
                    "field":  "combined_team_xwoba",
                    "model":  None,
                    "real":   round((h_xw + a_xw) / 2.0, 3),
                    "delta":  None,
                    "flag":   "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
                })
                penalty += 8

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
        "confidence_penalty": min(penalty, 55),
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
