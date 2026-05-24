"""Protected Alternative Market Scan — Phase 9.

When the LLM analyst finds no value in 1X2 / Doble Oportunidad on a Tier 1/2
priority match, this module is the second look that decides if there's value
hiding in a PROTECTED goal-line market instead — Under 3.5 / Under 2.5 — and
in combo Doble Oportunidad + Under tickets when both legs are available.

Rationale (Alavés vs Rayo Vallecano case study):
    H2H last 5 (real): 1-0, 1-0, 0-2, 1-0, 2-0
    All five matches ≤ 2 goals. Under 2.5 hits 100%. Under 3.5 hits 100%
    with much lower risk per unit returned. The direct 1X2 market however
    showed no edge (favourite already ~2.10), so the engine was dropping the
    match before checking goal-line totals. This module fixes that.

Public API:
    compute_under_profile_score(match, line=3.5) → dict
    scan_protected_alternatives(match)           → dict | None
    explain_under35_vs_under25(profile)          → list[str]
    eligible_for_alternative_scan(match)         → bool

All money math (implied probability, edge, Kelly) is reused from
moneyball_layer to keep "edge = est_prob − implied" the single source of
truth. We never recommend "porque suena seguro" — every recommendation must
pass the Moneyball gate too (caller's responsibility, see analyst_engine).
"""
from __future__ import annotations

import math
from typing import Optional


# ────────────────────────────────────────────────────────────────────────────
# Helpers — odds extraction
# ────────────────────────────────────────────────────────────────────────────

def _latest_snapshot(match: dict) -> Optional[dict]:
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    return snaps[-1] if isinstance(snaps[-1], dict) else None


def _markets(match: dict) -> dict:
    snap = _latest_snapshot(match)
    return (snap or {}).get("markets") or {}


def _consensus_line(rows: list[dict], key: str) -> Optional[float]:
    """Median across bookmakers for a single line label (e.g. "Under 3.5")."""
    vals = []
    for r in rows or []:
        v = (r.get("lines") or {}).get(key)
        if isinstance(v, (int, float)) and v > 1.01:
            vals.append(float(v))
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def best_under_line(match: dict, line_label: str) -> Optional[float]:
    """Best (highest) Under N.5 odds across bookmakers.

    `line_label` is the human label API-Sports uses, e.g. `"Under 3.5"` or
    `"Under 2.5"`. We return the HIGHEST quote so the user always sees the
    most-paying book; the bettor will look for that one specifically.
    """
    rows = _markets(match).get("Over/Under") or []
    best = None
    for r in rows:
        v = (r.get("lines") or {}).get(line_label)
        if isinstance(v, (int, float)) and v > 1.01:
            best = max(best, float(v)) if best else float(v)
    return best


def double_chance_odds(match: dict) -> dict:
    """Return median 1X / 12 / X2 quotes (or None per leg)."""
    rows = _markets(match).get("Double Chance") or []
    out: dict[str, Optional[float]] = {"Home/Draw": None, "Home/Away": None, "Draw/Away": None}
    for key in out:
        vals = []
        for r in rows:
            v = r.get(key)
            if isinstance(v, (int, float)) and v > 1.01:
                vals.append(float(v))
        if vals:
            vals.sort()
            n = len(vals)
            out[key] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return out


# ────────────────────────────────────────────────────────────────────────────
# H2H trend analysis
# ────────────────────────────────────────────────────────────────────────────

def _extract_h2h_totals(match: dict, limit: int = 10) -> list[int]:
    """Total goals per H2H match, newest first, capped at `limit`."""
    h2h = match.get("h2h_recent") or []
    totals = []
    for game in h2h[:limit]:
        hg = game.get("home_goals")
        ag = game.get("away_goals")
        # Some upstream feeds use {"goals": {"home", "away"}} nested.
        if hg is None or ag is None:
            goals = game.get("goals") or {}
            hg = goals.get("home")
            ag = goals.get("away")
        if isinstance(hg, (int, float)) and isinstance(ag, (int, float)):
            totals.append(int(hg) + int(ag))
    return totals


def _h2h_under_rate(totals: list[int], threshold: float) -> float:
    """Fraction of H2H matches whose total goals < threshold."""
    if not totals:
        return 0.0
    hits = sum(1 for t in totals if t < threshold)
    return hits / len(totals)


def _team_recent_goals(team_ctx: dict | None) -> tuple[Optional[float], Optional[float]]:
    """Return (goals_for_avg_last5, goals_against_avg_last5) when available."""
    if not team_ctx:
        return None, None
    last5 = team_ctx.get("last_5") or team_ctx.get("form_last_5_detail") or {}
    return last5.get("goals_for_avg"), last5.get("goals_against_avg")


# ────────────────────────────────────────────────────────────────────────────
# Profile scoring  (0-100)
# ────────────────────────────────────────────────────────────────────────────

# Each contributing factor returns its own 0-100 sub-score. The final
# profile score is a weighted average. We expose the weights as a dict so
# future tuning (or A/B testing) can swap without touching the call sites.
PROFILE_WEIGHTS_3_5 = {
    "h2h_under_3_5_rate":    0.30,  # most predictive — repeat fixture pattern
    "h2h_avg_total_goals":   0.18,  # cap-and-clip
    "recent_form_total":     0.18,  # home GF/GA + away GF/GA combined
    "tactical_profile":      0.12,  # placeholder from analyst_engine context (defaults 60)
    "fragility_inverse":     0.12,  # lower fragility ⇒ higher score
    "market_under_trend":    0.10,  # do odds themselves favour Under? (book consensus)
}
PROFILE_WEIGHTS_2_5 = {
    "h2h_under_2_5_rate":    0.35,  # stricter line; H2H matters MORE here
    "h2h_avg_total_goals":   0.20,
    "recent_form_total":     0.18,
    "tactical_profile":      0.10,
    "fragility_inverse":     0.08,
    "market_under_trend":    0.09,
}

# Decision thresholds aligned with the user's spec
TH_RECOMMEND = 70
TH_WATCHLIST = 60
TH_FRAGILITY_MAX = 55     # above this we don't recommend an Under


def compute_under_profile_score(match: dict, line: float = 3.5, *, tactical_score: int = 60,
                                fragility_score: int = 50) -> dict:
    """Compute UnderXXProfileScore for `line` ∈ {2.5, 3.5}.

    Args:
        match: hydrated match doc.
        line: goal threshold (2.5 or 3.5).
        tactical_score: 0-100 hint from the LLM/analyst pipeline indicating
            how controlled/tactical the matchup looks. When unknown, pass
            the default 60 (neutral).
        fragility_score: 0-100 estimate of how fragile the ticket is. The
            higher this number, the harder we discount the profile.

    Returns:
        {
            "score": int 0-100,
            "line":  float,
            "state": "UNDER_VALUE_FOUND" | "UNDER_WATCHLIST" | "INSUFFICIENT",
            "reasons": list[str],
            "h2h_under_rate": float,
            "h2h_avg_goals":  float | None,
            "samples_h2h":    int,
        }
    """
    if line not in (2.5, 3.5):
        raise ValueError("line must be 2.5 or 3.5")

    weights = PROFILE_WEIGHTS_3_5 if line == 3.5 else PROFILE_WEIGHTS_2_5
    reasons: list[str] = []

    # 1) H2H goal-line hit rate ───────────────────────────────────────────
    totals = _extract_h2h_totals(match, limit=10)
    h2h_under_rate = _h2h_under_rate(totals, line) if totals else 0.0
    h2h_avg = (sum(totals) / len(totals)) if totals else None
    sub_h2h_rate = h2h_under_rate * 100.0
    if totals:
        if h2h_under_rate >= 0.8:
            reasons.append(
                f"H2H reciente: {int(h2h_under_rate*100)}% terminó bajo {line} goles "
                f"({sum(1 for t in totals if t < line)}/{len(totals)})."
            )
        elif h2h_under_rate >= 0.6:
            reasons.append(
                f"H2H favorece Under {line}: {int(h2h_under_rate*100)}% de los últimos {len(totals)} partidos."
            )
    else:
        reasons.append("Sin H2H suficiente — confianza en perfil reducida.")

    # 2) H2H average total goals (lower ⇒ better Under signal) ────────────
    if h2h_avg is not None:
        # Map: 0 goals → 100, line+1.5 goals → 0  (sigmoid-like clamp)
        spread = max(0.5, (line + 1.5) - 0.0)
        sub_h2h_avg = max(0.0, min(100.0, (1.0 - (h2h_avg / spread)) * 100.0))
        if h2h_avg <= (line - 1.0):
            reasons.append(f"Media de goles H2H ({h2h_avg:.1f}) muy por debajo de {line}.")
    else:
        sub_h2h_avg = 40.0  # mild penalty for missing data

    # 3) Recent form combined GF/GA (last 5 home + away) ──────────────────
    home_gf, home_ga = _team_recent_goals(match.get("home_team", {}).get("context"))
    away_gf, away_ga = _team_recent_goals(match.get("away_team", {}).get("context"))
    samples = [v for v in [home_gf, home_ga, away_gf, away_ga] if v is not None]
    if samples:
        proj_total = sum(samples) / len(samples) * 2  # 2 teams → expected combined goals
        # Map: ≤ line-1 → 100, ≥ line+1 → 0
        sub_form = max(0.0, min(100.0, ((line + 1) - proj_total) / 2 * 100.0))
        if proj_total <= line - 0.5:
            reasons.append(
                f"Forma reciente proyecta ~{proj_total:.1f} goles totales — apuntala Under {line}."
            )
    else:
        sub_form = 50.0  # neutral when no form

    # 4) Tactical profile hint (passed from caller) ───────────────────────
    sub_tactical = max(0, min(100, int(tactical_score)))
    if sub_tactical >= 75:
        reasons.append("Perfil táctico/controlado declarado por el motor.")

    # 5) Fragility inverse ─────────────────────────────────────────────────
    sub_frag = max(0, min(100, 100 - int(fragility_score)))
    if fragility_score >= TH_FRAGILITY_MAX + 20:
        reasons.append(f"⚠️ Fragilidad elevada ({fragility_score}/100) — Under es riesgoso.")

    # 6) Market trend ─ do bookmakers themselves price the Under low? ─────
    under_label = f"Under {line}"
    over_label = f"Over {line}"
    rows = _markets(match).get("Over/Under") or []
    medians = {"under": None, "over": None}
    for label, key in ((under_label, "under"), (over_label, "over")):
        odds_list = []
        for r in rows:
            v = (r.get("lines") or {}).get(label)
            if isinstance(v, (int, float)) and v > 1.01:
                odds_list.append(float(v))
        if odds_list:
            odds_list.sort()
            medians[key] = odds_list[len(odds_list) // 2]
    if medians["under"] and medians["over"]:
        # Implied probabilities (without devigging — good enough as signal).
        p_under = 1.0 / medians["under"]
        p_over = 1.0 / medians["over"]
        # Re-vigor: normalise so the pair sums to 1.
        s = p_under + p_over
        if s > 0:
            p_under, p_over = p_under / s, p_over / s
        sub_market = max(0.0, min(100.0, p_under * 100.0))
        if p_under >= 0.65:
            reasons.append(f"Cuotas implican {int(p_under*100)}% Under {line} — mercado coincide.")
    else:
        sub_market = 50.0

    # Weighted composite ──────────────────────────────────────────────────
    if line == 3.5:
        score = (
            sub_h2h_rate * weights["h2h_under_3_5_rate"] +
            sub_h2h_avg  * weights["h2h_avg_total_goals"] +
            sub_form     * weights["recent_form_total"] +
            sub_tactical * weights["tactical_profile"] +
            sub_frag     * weights["fragility_inverse"] +
            sub_market   * weights["market_under_trend"]
        )
    else:
        score = (
            sub_h2h_rate * weights["h2h_under_2_5_rate"] +
            sub_h2h_avg  * weights["h2h_avg_total_goals"] +
            sub_form     * weights["recent_form_total"] +
            sub_tactical * weights["tactical_profile"] +
            sub_frag     * weights["fragility_inverse"] +
            sub_market   * weights["market_under_trend"]
        )

    score = int(round(max(0.0, min(100.0, score))))
    if score >= TH_RECOMMEND and fragility_score <= TH_FRAGILITY_MAX:
        state = "UNDER_VALUE_FOUND"
    elif score >= TH_WATCHLIST and fragility_score <= TH_FRAGILITY_MAX + 15:
        state = "UNDER_WATCHLIST"
    else:
        state = "INSUFFICIENT"
        if score < TH_WATCHLIST:
            reasons.append(f"Score Under {line} ({score}/100) por debajo del umbral mínimo.")

    return {
        "score": score,
        "line": line,
        "state": state,
        "reasons": reasons,
        "h2h_under_rate": round(h2h_under_rate, 3),
        "h2h_avg_goals": round(h2h_avg, 2) if h2h_avg is not None else None,
        "samples_h2h": len(totals),
        "_sub": {
            "h2h_rate": round(sub_h2h_rate, 1),
            "h2h_avg":  round(sub_h2h_avg, 1),
            "form":     round(sub_form, 1),
            "tactical": sub_tactical,
            "fragility_inv": sub_frag,
            "market":   round(sub_market, 1),
        },
    }


# ────────────────────────────────────────────────────────────────────────────
# Decision: which Under line to prefer + DC combos
# ────────────────────────────────────────────────────────────────────────────

def explain_under35_vs_under25(profile_3_5: dict, profile_2_5: dict) -> list[str]:
    """Generate the "why 3.5 is safer than 2.5" rationale list shown in UI."""
    out: list[str] = []
    s35, s25 = profile_3_5["score"], profile_2_5["score"]
    avg = profile_3_5.get("h2h_avg_goals") or profile_2_5.get("h2h_avg_goals")
    if avg is not None and avg <= 1.5:
        out.append(f"Media H2H de {avg:.1f} goles — escenario 0-0/1-0/2-0 dominante.")
    if s35 - s25 >= 12:
        out.append("Under 3.5 protege escenarios 2-1 y goles tardíos; Under 2.5 es más frágil ante un solo gol extra.")
    elif s25 - s35 >= 8:
        out.append("Under 2.5 con score superior — perfil 0-0/1-0 muy marcado.")
    if profile_3_5.get("h2h_under_rate", 0) >= 0.8:
        out.append(
            f"{int(profile_3_5['h2h_under_rate']*100)}% de los H2H terminaron bajo 3.5: muestra robusta."
        )
    if not out:
        out.append("Ambos Under tienen perfil similar; se prefiere 3.5 por menor varianza.")
    return out


def _select_preferred_under(profile_3_5: dict, profile_2_5: dict) -> tuple[str, dict]:
    """Choose the Under line we want to recommend.

    Per spec:
      • Si modelo espera 2-1 → Under 3.5 (no 2.5).
      • Si modelo espera 0-0/1-0/1-1/2-0 → considera ambos, prefiere mejor score.
      • Si caos → ninguno (caller debe filtrar antes con fragility).
    """
    # Use h2h_avg as proxy for expected score profile.
    avg = profile_3_5.get("h2h_avg_goals")
    s35, s25 = profile_3_5["score"], profile_2_5["score"]
    # If average is "right at the 2.5 line" (2.2-2.8) we lean 3.5 to protect
    # against a 2-1.
    if avg is not None and 2.1 <= avg <= 2.9:
        return ("Under 3.5", profile_3_5)
    # Otherwise pick whichever has the higher score & passes thresholds.
    if s35 >= s25:
        return ("Under 3.5", profile_3_5)
    return ("Under 2.5", profile_2_5)


# ────────────────────────────────────────────────────────────────────────────
# Eligibility gate (called by football_quality / analyst_engine)
# ────────────────────────────────────────────────────────────────────────────

def eligible_for_alternative_scan(match: dict) -> bool:
    """Tier 1/2 + H2H present + Over/Under odds present → can run alt scan.

    Used by football_quality to set `protected_alternative_eligible = True`
    and prevent low-liquidity Tier 1/2 matches from being filtered out
    before the alternative scan gets a chance to run.
    """
    fq = match.get("_football_quality") or {}
    tier = fq.get("tier") or 4
    if tier not in (1, 2):
        return False
    if not (match.get("h2h_recent") or []):
        return False
    ou_rows = _markets(match).get("Over/Under") or []
    if not ou_rows:
        return False
    # At least Under 3.5 or Under 2.5 priced somewhere.
    has_line = any(
        ("Under 3.5" in (r.get("lines") or {})) or ("Under 2.5" in (r.get("lines") or {}))
        for r in ou_rows
    )
    return has_line


# ────────────────────────────────────────────────────────────────────────────
# Main scanner — produces a candidate recommendation (Moneyball must approve)
# ────────────────────────────────────────────────────────────────────────────

def scan_protected_alternatives(
    match: dict,
    *,
    tactical_score: int = 60,
    fragility_score: int = 50,
    estimated_probability_under35: Optional[float] = None,
    estimated_probability_under25: Optional[float] = None,
    edge_threshold: float = 0.03,
) -> Optional[dict]:
    """Find the best PROTECTED alternative market for a match without direct edge.

    Args:
        match: hydrated match doc.
        tactical_score / fragility_score: hints from the LLM/moneyball
            pipeline (0-100 each). Defaults to a neutral 60/50.
        estimated_probability_under35 / _under25: model-estimated probability
            (0-1) the line cashes. When the caller didn't compute it, we
            try the StatsBomb-inspired Poisson model from
            `services/statsbomb_features.py` first, then fall back to
            shrunk H2H Under-rate (Bayesian) as a conservative proxy.
        edge_threshold: minimum edge (estimated_prob − implied_prob) to
            recommend, expressed as a fraction. 0.03 = 3% edge.

    Returns:
        None if no protected market clears the bar, OR:
        {
            "market": "Under 3.5" | "Under 2.5" | "Double Chance + Under 3.5",
            "selection": str,         # what the user actually clicks at the book
            "decimal_odds": float,
            "implied_probability": float,
            "estimated_probability": float,
            "edge": float,
            "profile_score": int,
            "fragility_score": int,
            "state": "PROTECTED_MARKET_RECOMMENDED" | "UNDER35_WATCHLIST",
            "reasons": list[str],
            "why_3_5_safer_than_2_5": list[str],
            "h2h_under_rate": float,
            "h2h_avg_goals": float | None,
            "statsbomb_features": dict | None,  # P2A: Poisson model output
        }
    """
    if not eligible_for_alternative_scan(match):
        return None

    # ── P2A — StatsBomb-inspired feature pack ────────────────────────────
    # When per-team recent fixtures / season priors are present on the
    # match doc, compute the Poisson goal-expectation model and use it
    # as the source of truth for estimated_probability_under{25,35}.
    # Falls back to the legacy Bayesian shrinkage when features can't be
    # computed (e.g. brand-new team with no recorded fixtures).
    sb_features = None
    try:
        from . import statsbomb_features as sbf  # local import to avoid cycles
        sb_features = sbf.compute_match_features(match)
    except Exception:
        sb_features = None
    if sb_features:
        # Caller's explicit estimate still wins (allows manual overrides).
        if estimated_probability_under35 is None:
            estimated_probability_under35 = sb_features.get("p_under_3_5")
        if estimated_probability_under25 is None:
            estimated_probability_under25 = sb_features.get("p_under_2_5")

    profile_3_5 = compute_under_profile_score(
        match, 3.5,
        tactical_score=tactical_score, fragility_score=fragility_score,
    )
    profile_2_5 = compute_under_profile_score(
        match, 2.5,
        tactical_score=tactical_score, fragility_score=fragility_score,
    )

    # When the Poisson model agrees strongly with one Under line we bump
    # that profile_score so the selector below picks it. The bump is
    # capped at +12 to avoid drowning H2H/form signals; we also blend
    # in the model's confidence (0-100) to keep low-sample matches honest.
    if sb_features:
        conf_w = sb_features.get("confidence", 50) / 100.0
        for line_val, profile in ((3.5, profile_3_5), (2.5, profile_2_5)):
            p_key = "p_under_3_5" if line_val == 3.5 else "p_under_2_5"
            p_model = sb_features.get(p_key) or 0.0
            # Convert P(under) into 0-100 sub-score (60% → 60).
            sub_xg = max(0.0, min(100.0, float(p_model) * 100.0))
            # Blend into profile: weight by model confidence (max 12 pts).
            bump = int(round((sub_xg - profile["score"]) * 0.12 * conf_w))
            profile["score"] = int(max(0, min(100, profile["score"] + bump)))
            profile["_sub"]["xg_model"] = round(sub_xg, 1)
            profile["_xg_blend_bump"] = bump
            if p_model >= 0.65 and conf_w >= 0.5:
                profile["reasons"].insert(0,
                    f"Modelo xG estima {p_model*100:.0f}% Under {line_val} "
                    f"(confianza {sb_features['confidence']}/100)."
                )

    # Both INSUFFICIENT → nothing protected to offer.
    if profile_3_5["state"] == "INSUFFICIENT" and profile_2_5["state"] == "INSUFFICIENT":
        return None

    line_label, picked = _select_preferred_under(profile_3_5, profile_2_5)

    # Locate the best (highest) odds for that line label.
    decimal_odds = best_under_line(match, line_label)
    if not decimal_odds or decimal_odds <= 1.01:
        # No market priced → fall back to the other line if it passes.
        alt_label = "Under 2.5" if line_label == "Under 3.5" else "Under 3.5"
        alt_picked = profile_2_5 if line_label == "Under 3.5" else profile_3_5
        alt_odds = best_under_line(match, alt_label)
        if not alt_odds or alt_picked["state"] == "INSUFFICIENT":
            return None
        line_label, picked, decimal_odds = alt_label, alt_picked, alt_odds

    implied = 1.0 / decimal_odds

    # Estimated probability — caller-provided wins. Otherwise use h2h rate
    # shrunk toward implied (conservative empirical Bayes shrinkage).
    line_value = picked["line"]
    caller_estimate = estimated_probability_under35 if line_value == 3.5 else estimated_probability_under25
    if caller_estimate is not None:
        est_prob = float(caller_estimate)
    else:
        h2h_rate = picked["h2h_under_rate"]
        n = picked["samples_h2h"]
        # Shrink toward 0.5 when n is small.
        weight = n / (n + 4) if n else 0.0
        est_prob = h2h_rate * weight + 0.5 * (1 - weight)

    edge = est_prob - implied

    # Try a Doble Oportunidad + Under combo when both legs exist and look
    # complementary (e.g. 1X + Under 3.5 against a chaotic away side).
    combo_candidate = _try_dc_under_combo(match, line_label, decimal_odds, est_prob)

    # Recommend only when:
    #   • UNDER_VALUE_FOUND (profile ≥ 70 AND fragility ≤ 55)
    #   • edge ≥ edge_threshold (Moneyball gating happens AFTER this; we
    #     leave the final NO_BET_VALUE call to the caller, but pre-gate here
    #     so an obviously edge-less Under never enters the candidate list).
    state = picked["state"]
    if state == "UNDER_VALUE_FOUND" and edge >= edge_threshold:
        final_state = "PROTECTED_MARKET_RECOMMENDED"
    elif state == "UNDER_WATCHLIST" and edge >= edge_threshold * 0.5:
        final_state = "UNDER35_WATCHLIST" if line_value == 3.5 else "UNDER25_WATCHLIST"
    else:
        return None

    why_3_vs_2 = explain_under35_vs_under25(profile_3_5, profile_2_5)

    result = {
        "market": line_label,
        "selection": line_label,
        "decimal_odds": round(decimal_odds, 3),
        "implied_probability": round(implied, 4),
        "estimated_probability": round(est_prob, 4),
        "edge": round(edge, 4),
        "edge_pct": round(edge * 100, 2),
        "profile_score": picked["score"],
        "fragility_score": fragility_score,
        "state": final_state,
        "reasons": picked["reasons"],
        "why_3_5_safer_than_2_5": why_3_vs_2,
        "h2h_under_rate": picked["h2h_under_rate"],
        "h2h_avg_goals": picked["h2h_avg_goals"],
        "samples_h2h": picked["samples_h2h"],
        "profile_3_5": profile_3_5,
        "profile_2_5": profile_2_5,
        "statsbomb_features": sb_features,
        "estimated_probability_source": (
            "statsbomb_poisson" if sb_features else "h2h_bayesian_shrink"
        ),
    }
    if combo_candidate:
        result["combo_candidate"] = combo_candidate
    return result


def _try_dc_under_combo(match: dict, under_label: str, under_odds: float, under_prob_est: float) -> Optional[dict]:
    """If the Doble Oportunidad market is available and one leg looks like a
    natural complement to the Under bet, build a combo candidate.

    We choose the DC leg whose implied probability is HIGHEST among the
    three (1X / 12 / X2) — i.e. the "stable" side that's least likely to
    chase the game open.

    Note: this is a CANDIDATE — the actual combo odds depend on the
    bookmaker (most don't expose pre-built DC+Under). It's surfaced in the
    response so the UI can suggest the manual ticket build.
    """
    dc = double_chance_odds(match)
    legs = [(k, v) for k, v in dc.items() if v is not None]
    if not legs:
        return None
    # Stable leg = highest implied prob (i.e. lowest decimal odds).
    legs.sort(key=lambda kv: kv[1])
    leg_name, leg_odds = legs[0]
    leg_implied = 1.0 / leg_odds
    # Multiplicative independence assumption — works as a back-of-envelope
    # estimate but ALWAYS show the user that this is approximate (UI flag).
    combo_odds = round(leg_odds * under_odds, 2)
    combo_implied = leg_implied * (1.0 / under_odds)
    # Estimate the combo probability assuming approximate independence —
    # this is intentionally conservative since DC and Under are positively
    # correlated in low-scoring matchups.
    combo_est = leg_implied * under_prob_est
    combo_edge = combo_est - combo_implied
    return {
        "selection": f"{leg_name} + {under_label}",
        "approximate_decimal_odds": combo_odds,
        "implied_probability": round(combo_implied, 4),
        "estimated_probability": round(combo_est, 4),
        "edge_pct": round(combo_edge * 100, 2),
        "approximation_note": "Cuota combinada estimada como producto de las dos patas — la real depende del bookie.",
    }
