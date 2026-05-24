"""StatsBomb-inspired feature pack for Under 3.5 / Under 2.5 modelling.

This module turns a hydrated match document into a small set of robust,
explainable goal-expectation features modelled on the methodology used by
StatsBomb's public open-data work (xG-based expectation, defensive
solidity, scoring fragility, scoreline distribution). We do NOT use raw
StatsBomb event data — their open dataset is historical and limited to
specific competitions, so it cannot drive picks on this week's Premier
League / La Liga. Instead we apply the StatsBomb FEATURE PHILOSOPHY to
the data we do have access to via API-Sports:

  • Realized goals scored / conceded over the last 10 matches
    (proxy for xG when xG is not exposed).
  • Season-level priors: clean-sheet rate, failed-to-score rate.
  • H2H goal totals (re-used from the existing scan).

The output is plugged into `services/under_market_scan.py` as the
estimated probability for Under 2.5 / Under 3.5 (replacing the bayesian
shrink-toward-implied that the scan does when no model is wired). It is
also exposed under `pick._statsbomb_features` so the UI can show
"Modelo xG: P(Under 2.5)=64%, λ=2.18" next to the Protected Market
badge.

Public API:
    compute_match_features(match)  → dict | None
    poisson_total_under(lambda_total, line)  → float
    explain_features(features, lang='es')  → list[str]

Implementation notes
--------------------
1) Lambda model — bivariate independent Poisson over total goals.
   We start from each team's last-N average goals scored and conceded,
   then *adjust* one side by the OPPONENT's defensive strength so the
   final λ_home / λ_away are not just team-level priors but matchup-
   specific.

       λ_home = 0.6 * home.gf_avg_home + 0.4 * (away.ga_avg_away * home.gf_avg_overall / league_avg)
       λ_away = 0.6 * away.gf_avg_away + 0.4 * (home.ga_avg_home * away.gf_avg_overall / league_avg)

   When venue splits aren't available we fall back to overall avgs.

2) For low-sample teams (played < 5) we shrink toward a neutral prior
   (1.35 goals/team — the historical league-average per side) using
   empirical Bayes weights: w = n / (n + 4). This avoids the model
   exploding on early-season fixtures.

3) The model output `p_under_25` and `p_under_35` is the Poisson CDF
   of TOTAL = X_home + X_away (sum of independent Poissons is Poisson
   with λ = λ_home + λ_away), so we just compute Poisson(λ_total).cdf(2)
   and Poisson(λ_total).cdf(3) respectively.

4) A confidence score 0-100 is also returned. It reflects sample size
   (last-N played count for both teams) plus the *agreement* between
   the model and the H2H Under-rate. The protected scan uses it as a
   gate (high-confidence required to recommend, lower allowed for
   watchlist).
"""
from __future__ import annotations

import math
from typing import Any, Optional


# Soft fallback used when a team has no recorded goal data.
NEUTRAL_PRIOR_LAMBDA = 1.35
LEAGUE_AVG_GOALS_PER_SIDE = 1.35
PRIOR_SHRINK_KAPPA = 4  # Bayesian shrinkage strength


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _team_recent(team_ctx: dict | None) -> dict | None:
    """Pull a team's last-N fixture distribution out of its context.

    The data_ingestion pipeline attaches it as
    `match.home_team.context.recent_fixtures` — see normalizer
    `normalize_recent_fixtures`.
    """
    if not isinstance(team_ctx, dict):
        return None
    rf = team_ctx.get("recent_fixtures")
    if not isinstance(rf, dict):
        return None
    return rf


def _team_priors(team_ctx: dict | None) -> dict | None:
    if not isinstance(team_ctx, dict):
        return None
    sp = team_ctx.get("season_priors")
    return sp if isinstance(sp, dict) else None


def _shrunk_avg(observed: Optional[float], n: int, neutral: float = NEUTRAL_PRIOR_LAMBDA) -> float:
    """Empirical-Bayes shrinkage toward a neutral prior.

    Smaller `n` ⇒ more weight on `neutral`. With `kappa=4` and `n=10`
    we get w=10/14 ≈ 0.71 (mostly observed); with `n=2` we get w=0.33
    (mostly neutral). This stops a team that just played 2 freak
    matches from dominating the lambda.
    """
    if observed is None or observed < 0:
        return neutral
    if n <= 0:
        return neutral
    w = n / (n + PRIOR_SHRINK_KAPPA)
    return w * float(observed) + (1.0 - w) * neutral


def _poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) for Poisson(lam). Safe for small k."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except Exception:
        return 0.0


def poisson_total_under(lam: float, line: float) -> float:
    """P(X < `line`) for Poisson(lam) with `line` ∈ {2.5, 3.5, ...}.

    `lam` should already be the TOTAL goals lambda (home + away).
    """
    if lam is None or lam < 0:
        return 0.5
    cap = int(math.floor(line))  # for Under 3.5 → sum k=0..3
    p = 0.0
    for k in range(cap + 1):
        p += _poisson_pmf(k, lam)
    return max(0.0, min(1.0, p))


# ────────────────────────────────────────────────────────────────────────────
# Public — main entry point
# ────────────────────────────────────────────────────────────────────────────

def compute_match_features(match: dict) -> Optional[dict]:
    """Build the StatsBomb-inspired feature pack for one match.

    Returns None when we have neither last-N data nor season averages for
    BOTH teams — without any input there's no information to model with.

    Returns:
        {
          "p_under_2_5":            float 0-1,
          "p_under_3_5":            float 0-1,
          "lambda_home":            float,    # adjusted xG (goals expected)
          "lambda_away":            float,
          "lambda_total":           float,
          "confidence":             int 0-100,
          "sample_size":            {"home": int, "away": int},
          "defensive_solidity":     {"home": float, "away": float},
          "scoring_fragility":      {"home": float, "away": float},
          "recent_under_rate_2_5":  float | None,
          "recent_under_rate_3_5":  float | None,
          "btts_rate_recent":       float | None,
          "components":             {...},   # raw subcomponents for UI/debug
          "explanations":           list[str], # human-readable rationale (es)
        }
    """
    home_ctx = (match.get("home_team") or {}).get("context") or {}
    away_ctx = (match.get("away_team") or {}).get("context") or {}

    h_rec = _team_recent(home_ctx) or {}
    a_rec = _team_recent(away_ctx) or {}
    h_pri = _team_priors(home_ctx) or {}
    a_pri = _team_priors(away_ctx) or {}

    h_n = int(h_rec.get("played") or 0)
    a_n = int(a_rec.get("played") or 0)

    # Need at least *something* from each side. If both buckets are empty
    # we can't build a meaningful matchup-specific lambda — bail out and
    # let the existing H2H heuristic in under_market_scan handle it.
    if h_n == 0 and a_n == 0 and not (h_pri or a_pri):
        return None

    # ── Per-team rates ───────────────────────────────────────────────────
    # Prefer venue-specific avg (home team @ home, away team @ away) when
    # we have ≥ 3 such matches. Fallback to overall.
    def _pick_avg(rec: dict, key_split: str, key_overall: str, n_split_threshold: int = 3) -> Optional[float]:
        v_split = rec.get(key_split)
        # We don't track per-venue n directly; trust split when present.
        if isinstance(v_split, (int, float)):
            return float(v_split)
        v_overall = rec.get(key_overall)
        return float(v_overall) if isinstance(v_overall, (int, float)) else None

    h_gf_obs = _pick_avg(h_rec, "gf_avg_home", "gf_avg")
    h_ga_obs = _pick_avg(h_rec, "ga_avg_home", "ga_avg")
    a_gf_obs = _pick_avg(a_rec, "gf_avg_away", "gf_avg")
    a_ga_obs = _pick_avg(a_rec, "ga_avg_away", "ga_avg")

    # Fallback to team context's goals_for_avg / goals_against_avg
    # (season-level via /teams/statistics) when last-N is missing.
    if h_gf_obs is None:
        h_gf_obs = home_ctx.get("goals_for_avg")
    if h_ga_obs is None:
        h_ga_obs = home_ctx.get("goals_against_avg")
    if a_gf_obs is None:
        a_gf_obs = away_ctx.get("goals_for_avg")
    if a_ga_obs is None:
        a_ga_obs = away_ctx.get("goals_against_avg")

    # Convert each rate to a shrunk value (Bayes prior toward 1.35).
    h_gf = _shrunk_avg(h_gf_obs, h_n)
    h_ga = _shrunk_avg(h_ga_obs, h_n)
    a_gf = _shrunk_avg(a_gf_obs, a_n)
    a_ga = _shrunk_avg(a_ga_obs, a_n)

    # ── Adjusted matchup-specific lambdas ────────────────────────────────
    # Each side's λ is its own offensive rate softly pulled toward the
    # opponent's defensive rate, normalized by league average so the
    # ratio doesn't blow up.
    lam_h = 0.55 * h_gf + 0.45 * (a_ga * (h_gf / max(LEAGUE_AVG_GOALS_PER_SIDE, 0.6)))
    lam_a = 0.55 * a_gf + 0.45 * (h_ga * (a_gf / max(LEAGUE_AVG_GOALS_PER_SIDE, 0.6)))
    # Clamp to reasonable football range (0.3 - 3.8 goals/team)
    lam_h = max(0.30, min(3.8, lam_h))
    lam_a = max(0.30, min(3.8, lam_a))
    lam_total = lam_h + lam_a

    # ── Probabilities ────────────────────────────────────────────────────
    p_under_25 = poisson_total_under(lam_total, 2.5)
    p_under_35 = poisson_total_under(lam_total, 3.5)

    # ── Side-feature scores (for the UI) ─────────────────────────────────
    # Higher = better for Under.
    # Defensive solidity: blend clean-sheet rate (season) + 1-ga_avg/2.0
    def _solidity(pri: dict, ga_avg: Optional[float]) -> float:
        cs_rate = (pri or {}).get("clean_sheet_rate")
        cs = float(cs_rate) if isinstance(cs_rate, (int, float)) else None
        if ga_avg is None:
            return cs if cs is not None else 0.5
        ga_score = max(0.0, min(1.0, 1.0 - (float(ga_avg) / 2.5)))
        if cs is None:
            return ga_score
        return 0.5 * cs + 0.5 * ga_score

    # Scoring fragility = how often the team fails to score (higher = better for Under)
    def _fragility(pri: dict, gf_avg: Optional[float]) -> float:
        fts = (pri or {}).get("failed_to_score_rate")
        fts_v = float(fts) if isinstance(fts, (int, float)) else None
        if gf_avg is None:
            return fts_v if fts_v is not None else 0.5
        gf_score = max(0.0, min(1.0, 1.0 - (float(gf_avg) / 2.5)))
        if fts_v is None:
            return gf_score
        return 0.5 * fts_v + 0.5 * gf_score

    home_solidity = _solidity(h_pri, h_ga_obs)
    away_solidity = _solidity(a_pri, a_ga_obs)
    home_fragility = _fragility(h_pri, h_gf_obs)
    away_fragility = _fragility(a_pri, a_gf_obs)

    # ── Recent Under hit-rates (last-N realised) ─────────────────────────
    def _recent_rate(rec: dict, key: str) -> Optional[float]:
        c = rec.get(key)
        n = int(rec.get("played") or 0)
        if not c or not n:
            return None
        return round(int(c) / n, 3)

    h_u35 = _recent_rate(h_rec, "under_3_5_count")
    a_u35 = _recent_rate(a_rec, "under_3_5_count")
    h_u25 = _recent_rate(h_rec, "under_2_5_count")
    a_u25 = _recent_rate(a_rec, "under_2_5_count")
    recent_u35 = None
    recent_u25 = None
    if h_u35 is not None and a_u35 is not None:
        recent_u35 = round((h_u35 + a_u35) / 2.0, 3)
    elif h_u35 is not None or a_u35 is not None:
        recent_u35 = h_u35 if h_u35 is not None else a_u35
    if h_u25 is not None and a_u25 is not None:
        recent_u25 = round((h_u25 + a_u25) / 2.0, 3)
    elif h_u25 is not None or a_u25 is not None:
        recent_u25 = h_u25 if h_u25 is not None else a_u25

    # BTTS rate is informative for under context (lots of BTTS ⇒ less under)
    btts_rate = None
    if h_n or a_n:
        btts_h = (h_rec.get("btts") or 0) / h_n if h_n else None
        btts_a = (a_rec.get("btts") or 0) / a_n if a_n else None
        vals = [v for v in (btts_h, btts_a) if v is not None]
        if vals:
            btts_rate = round(sum(vals) / len(vals), 3)

    # ── Confidence score 0-100 ───────────────────────────────────────────
    # Base from sample size (min 0, max 70 from samples alone).
    samples_total = h_n + a_n
    base = min(70, int(samples_total * 4))  # 10+10 → 70
    # Bonus: agreement between Poisson and last-N recent rate (max +20)
    agreement_bonus = 0
    if recent_u35 is not None:
        diff = abs(recent_u35 - p_under_35)
        agreement_bonus = max(0, int(round((0.30 - diff) * 67)))  # ~+20 if diff=0
        agreement_bonus = min(20, agreement_bonus)
    # Bonus: low spread of recent goal totals (predictable team) (max +10)
    spread_bonus = 0
    for rec in (h_rec, a_rec):
        std = rec.get("total_std")
        if isinstance(std, (int, float)) and std <= 1.2:
            spread_bonus += 5
    confidence = max(0, min(100, base + agreement_bonus + spread_bonus))

    # ── Explanations (ES) ────────────────────────────────────────────────
    explanations: list[str] = []
    explanations.append(
        f"Modelo Poisson: λ_total = {lam_total:.2f} goles esperados "
        f"({lam_h:.2f} local + {lam_a:.2f} visitante)."
    )
    explanations.append(
        f"P(Under 2.5) = {p_under_25*100:.1f}% · P(Under 3.5) = {p_under_35*100:.1f}%."
    )
    if recent_u35 is not None:
        explanations.append(
            f"Últimos {samples_total} partidos (ambos equipos): "
            f"Under 3.5 hit-rate = {recent_u35*100:.0f}%."
        )
    if (h_pri.get("clean_sheet_rate") or 0) >= 0.40 or (a_pri.get("clean_sheet_rate") or 0) >= 0.40:
        cs_h = (h_pri.get("clean_sheet_rate") or 0) * 100
        cs_a = (a_pri.get("clean_sheet_rate") or 0) * 100
        explanations.append(
            f"Defensa sólida en temporada — clean-sheet rate: local {cs_h:.0f}% / visitante {cs_a:.0f}%."
        )
    if (h_pri.get("failed_to_score_rate") or 0) >= 0.30 or (a_pri.get("failed_to_score_rate") or 0) >= 0.30:
        explanations.append(
            "Al menos uno de los equipos falla en marcar frecuentemente — apuntala Under."
        )
    if btts_rate is not None and btts_rate <= 0.40:
        explanations.append(f"BTTS reciente solo {btts_rate*100:.0f}% — perfil bajo en goles.")

    return {
        "p_under_2_5":           round(p_under_25, 4),
        "p_under_3_5":           round(p_under_35, 4),
        "lambda_home":           round(lam_h, 3),
        "lambda_away":           round(lam_a, 3),
        "lambda_total":          round(lam_total, 3),
        "confidence":            confidence,
        "sample_size":           {"home": h_n, "away": a_n},
        "defensive_solidity":    {
            "home": round(home_solidity, 3),
            "away": round(away_solidity, 3),
        },
        "scoring_fragility":     {
            "home": round(home_fragility, 3),
            "away": round(away_fragility, 3),
        },
        "recent_under_rate_2_5": recent_u25,
        "recent_under_rate_3_5": recent_u35,
        "btts_rate_recent":      btts_rate,
        "components": {
            "home_gf_obs": h_gf_obs, "home_ga_obs": h_ga_obs,
            "away_gf_obs": a_gf_obs, "away_ga_obs": a_ga_obs,
            "home_gf_shrunk": round(h_gf, 3), "home_ga_shrunk": round(h_ga, 3),
            "away_gf_shrunk": round(a_gf, 3), "away_ga_shrunk": round(a_ga, 3),
            "league_avg_per_side": LEAGUE_AVG_GOALS_PER_SIDE,
        },
        "explanations":          explanations,
        "_source":               "statsbomb_inspired_v1",
    }


def explain_features(features: dict, lang: str = "es") -> list[str]:
    """Backwards-compatible accessor; the explanations are already cached."""
    if not features:
        return []
    return list(features.get("explanations") or [])
