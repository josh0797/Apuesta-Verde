"""
services.mlb_player_props_discovery
====================================

Phase 57 — **MLB Player Props Discovery (Moneyball)**.

Pure-Python deterministic engine that surfaces *repeatable, low-fragility,
high-probability* player props from confirmed MLB lineups. Built to find
the boring-but-reliable plays (H+R+RBI 1.5, Total Bases 1.5, Hits 1+,
RBI 1+, Runs 1+) instead of popular longshots (HR/SB props).

Data sources
------------
* **MLB Stats API** (mandatory) — season hitting stats + active roster
  via ``hydrate_team_offensive_roster``.
* **Baseball Savant batter** (optional, fail-soft, cached) — xwOBA,
  xSLG, Barrel%, HardHit%, Exit Velocity. Used as a soft modulator; if
  unavailable, the engine falls back to the season SLG/OBP base.

Modelling
---------
Each market is modelled as a Poisson process with rate ``λ`` derived
from the player's per-game expectation, conditioned on:

* season + recent-form rate (last-15 weighted blend),
* matchup against the opposing pitcher (ERA/WHIP/xERA → multiplier),
* park factor (``park_runs_mult``),
* (optional) Savant skill bump (xwOBA above league average).

The Moneyball filter rejects any prop with:

* ``model_probability < MONEYBALL_MIN_PROBABILITY`` (default 0.55),
* ``edge_points < MONEYBALL_MIN_EDGE_PTS`` (default 4.0),
* anti-longshot: never recommend a prop whose model probability is
  below 0.50 even when the apparent edge is high.

The output never mutates engine state — this is a discovery layer.

Output (per recommended prop)
-----------------------------
::

    {
        "available":          True,
        "engine_version":     ENGINE_VERSION,
        "player_id":          660271,
        "player_name":        "Aaron Judge",
        "team":               "NYY",
        "opponent":           "BAL",
        "game_pk":            776123,
        "market":             "H_R_RBI",
        "line":               1.5,
        "selection":          "OVER",
        "lambda_estimate":    2.10,
        "model_probability":  0.621,
        "implied_probability": 0.524,
        "edge_points":        9.7,
        "edge_score":         62,
        "confidence_tier":    "VALUE",
        "season_basis":       {...},
        "recent_form_basis":  {...},
        "adjustments":        {"pitcher_mult": 1.04, "park_mult": 0.98,
                               "savant_mult": 1.03, "recent_weight": 0.30},
        "data_quality":       "COMPLETE",
        "narrative_es":       "Aaron Judge enfrenta a un abridor con ERA 4.80...",
        "reason_codes":       ["MONEYBALL_VALUE", "DATA_QUALITY_COMPLETE"],
    }
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("mlb_player_props_discovery")

ENGINE_VERSION = "mlb_player_props_discovery.v1"
EASTERN = ZoneInfo("America/New_York")


# ──────────────────────────────────────────────────────────────────────
# Markets
# ──────────────────────────────────────────────────────────────────────
MARKET_H_R_RBI    = "H_R_RBI"
MARKET_TB         = "TOTAL_BASES"
MARKET_HITS_1P    = "HITS_1_PLUS"
MARKET_RBI_1P     = "RBI_1_PLUS"
MARKET_RUNS_1P    = "RUNS_1_PLUS"

ALL_MARKETS = (
    MARKET_H_R_RBI, MARKET_TB,
    MARKET_HITS_1P, MARKET_RBI_1P, MARKET_RUNS_1P,
)

# Default book lines per market (when the caller cannot supply real
# sportsbook lines). Chosen to match what books typically post.
DEFAULT_BOOK_LINES = {
    MARKET_H_R_RBI: 1.5,
    MARKET_TB:      1.5,
    MARKET_HITS_1P: 0.5,
    MARKET_RBI_1P:  0.5,
    MARKET_RUNS_1P: 0.5,
}

# Default book payouts (American odds) per market. Conservative anchors
# used to compute implied probability when no book quote is supplied.
DEFAULT_BOOK_AMERICAN_ODDS = {
    MARKET_H_R_RBI: -110,
    MARKET_TB:      -110,
    # 1+ markets tend to be priced juicier on the OVER (-200 to -260).
    MARKET_HITS_1P: -180,
    MARKET_RBI_1P:  +180,
    MARKET_RUNS_1P: +200,
}

# Moneyball thresholds.
MONEYBALL_MIN_PROBABILITY = 0.55
MONEYBALL_MIN_EDGE_PTS    = 4.0
LONGSHOT_PROB_FLOOR       = 0.50

# Recent-form blend weights.
SEASON_WEIGHT    = 0.70
RECENT_WEIGHT    = 0.30

# League averages (used as fall-backs when a stat is missing).
LEAGUE_AVG_OBP        = 0.318
LEAGUE_AVG_SLG        = 0.405
LEAGUE_AVG_OPS        = LEAGUE_AVG_OBP + LEAGUE_AVG_SLG
LEAGUE_AVG_XWOBA      = 0.320
LEAGUE_AVG_HARD_HIT   = 38.5

# Pitcher multiplier window. We cap pitcher influence to ±25%
# to prevent extreme outliers (e.g. fresh callups) from blowing up λ.
PITCHER_MULT_FLOOR = 0.75
PITCHER_MULT_CEIL  = 1.25

# Park multiplier window.
PARK_MULT_FLOOR = 0.85
PARK_MULT_CEIL  = 1.15

# Plate-appearance assumption when not provided. A top-of-order hitter
# usually sees ~4.4 PAs / 9-inning game; bottom of order ~3.8. Default to 4.1.
DEFAULT_PA_PER_GAME = 4.1

# Per-spot PA expectation. Critical for H+R+RBI / Hits / Runs accuracy —
# top of the order sees materially more PAs than the bottom.
LINEUP_POSITION_PA: dict[int, float] = {
    1: 4.6, 2: 4.5, 3: 4.4, 4: 4.3, 5: 4.1,
    6: 3.9, 7: 3.8, 8: 3.7, 9: 3.6,
}


def _resolve_pa_per_game(
    player: dict, fallback: float = DEFAULT_PA_PER_GAME,
) -> float:
    """Pick a per-game plate appearance estimate.

    Priority:
      1. Explicit ``pa_per_game`` on the player.
      2. ``batting_order`` / ``lineup_position`` (1-9 → table).
      3. Fallback (4.1).
    """
    pa_explicit = _safe_float(player.get("pa_per_game"))
    if pa_explicit is not None and pa_explicit > 0:
        return pa_explicit
    spot = _safe_int(
        player.get("batting_order") or player.get("lineup_position") or 0,
        default=0,
    )
    if spot in LINEUP_POSITION_PA:
        return LINEUP_POSITION_PA[spot]
    return fallback


# Market priority for the per-player "best prop" selector. Lower index
# means higher priority. H+R+RBI is the most repeatable / lowest
# context-dependence; RBI is the most context-fragile.
MARKET_PRIORITY_ORDER: tuple[str, ...] = (
    "H_R_RBI",        # 0 — most stable
    "TOTAL_BASES",    # 1
    "HITS_1_PLUS",    # 2
    "RUNS_1_PLUS",    # 3
    "RBI_1_PLUS",     # 4 — most context-dependent
)


# Player-prop fragility model — observe-only score on a 0-100 scale.
# HIGH fragility flags increase the score (more context dependence,
# more noise). The bucket thresholds align with the rest of the engine.
_FRAGILITY_HR_DEPENDENCY     = 18
_FRAGILITY_RBI_DEPENDENCY    = 14
_FRAGILITY_BOOM_BUST_HITTER  = 16
_FRAGILITY_LOW_AVG_HIGH_ISO  = 12
_FRAGILITY_ELITE_PITCHER     = 22
_FRAGILITY_LOW_LINEUP_SPOT   = 14
_FRAGILITY_DATA_MINIMAL      = 18
_FRAGILITY_DATA_PARTIAL      = 8


# ──────────────────────────────────────────────────────────────────────
# Reason codes
# ──────────────────────────────────────────────────────────────────────
RC_MONEYBALL_VALUE        = "MONEYBALL_VALUE"
RC_MONEYBALL_WATCH        = "MONEYBALL_WATCH"
RC_MONEYBALL_AVOID        = "MONEYBALL_AVOID"
RC_LONGSHOT_REJECTED      = "LONGSHOT_REJECTED"
RC_LOW_PROBABILITY        = "LOW_PROBABILITY"
RC_LOW_EDGE               = "LOW_EDGE"
RC_DATA_QUALITY_COMPLETE  = "DATA_QUALITY_COMPLETE"
RC_DATA_QUALITY_PARTIAL   = "DATA_QUALITY_PARTIAL"
RC_DATA_QUALITY_MINIMAL   = "DATA_QUALITY_MINIMAL"
RC_SAVANT_USED            = "SAVANT_USED"
RC_SAVANT_UNAVAILABLE     = "SAVANT_UNAVAILABLE"
RC_PITCHER_QUALITY_FAVOR  = "PITCHER_QUALITY_FAVOR"   # pitcher is weak
RC_PITCHER_QUALITY_HURTS  = "PITCHER_QUALITY_HURTS"   # pitcher is elite
RC_PARK_FAVOR             = "PARK_FAVORS_HITTER"
RC_PARK_HURTS             = "PARK_HURTS_HITTER"
RC_RECENT_FORM_HOT        = "RECENT_FORM_HOT"
RC_RECENT_FORM_COLD       = "RECENT_FORM_COLD"
RC_POSITION_PITCHER_EXCLUDED = "POSITION_PITCHER_EXCLUDED"

# Player-prop fragility reason codes (only emitted on the fragility
# block — never on the moneyball-tier output).
RC_FRAG_HR_DEPENDENCY     = "FRAG_HR_DEPENDENCY"
RC_FRAG_RBI_DEPENDENCY    = "FRAG_RBI_DEPENDENCY"
RC_FRAG_BOOM_BUST_HITTER  = "FRAG_BOOM_BUST_HITTER"
RC_FRAG_LOW_AVG_HIGH_ISO  = "FRAG_LOW_AVG_HIGH_ISO"
RC_FRAG_ELITE_PITCHER     = "FRAG_ELITE_PITCHER"
RC_FRAG_LOW_LINEUP_SPOT   = "FRAG_LOW_LINEUP_SPOT"
RC_FRAG_DATA_MINIMAL      = "FRAG_DATA_MINIMAL"
RC_FRAG_DATA_PARTIAL      = "FRAG_DATA_PARTIAL"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    try:
        return math.exp(-mu) * (mu ** k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def _poisson_p_ge(k_target: int, mu: float, max_k: int = 30) -> float:
    """Return P(X >= k_target) for Poisson(mu)."""
    if mu <= 0:
        return 0.0 if k_target > 0 else 1.0
    cum = 0.0
    for k in range(0, k_target):
        cum += _poisson_pmf(k, mu)
        if cum >= 0.999:
            return max(0.0, 1.0 - cum)
    return max(0.0, 1.0 - cum)


def american_odds_to_implied(odds: int) -> float:
    """Convert American odds to implied probability (0..1).

    -110  → 0.524    +180 → 0.357
    +200  → 0.333    -200 → 0.667
    """
    if odds is None:
        return 0.50
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return 0.50
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


# ──────────────────────────────────────────────────────────────────────
# Public schema helpers
# ──────────────────────────────────────────────────────────────────────
def _is_offensive_position(pos_abbr: Optional[str]) -> bool:
    """Filter out pitchers from props candidate pool."""
    if not pos_abbr:
        return True   # unknown → keep, let stats decide
    p = pos_abbr.upper().strip()
    if p in {"P", "SP", "RP", "TWP"}:
        return False
    return True


def _pitcher_quality_multiplier(pitcher: dict) -> tuple[float, list[str]]:
    """Translate pitcher stats into a hitter-friendly multiplier on λ.

    Higher ERA/WHIP/xERA → larger multiplier (hitter benefits).
    Returns (multiplier, reason_codes).
    """
    reasons: list[str] = []
    era  = _safe_float(pitcher.get("era"))
    whip = _safe_float(pitcher.get("whip"))
    xera = _safe_float(pitcher.get("xera"))

    parts: list[float] = []
    if era is not None:
        # ERA 3.50 → 1.00 (neutral). ERA 5.00 → +15%. ERA 2.80 → -15%.
        parts.append(_clamp(1.0 + (era - 3.50) * 0.10, PITCHER_MULT_FLOOR, PITCHER_MULT_CEIL))
    if whip is not None:
        # WHIP 1.20 → neutral. 1.40 → +12%. 1.05 → -12%.
        parts.append(_clamp(1.0 + (whip - 1.20) * 0.60, PITCHER_MULT_FLOOR, PITCHER_MULT_CEIL))
    if xera is not None:
        # xERA carries slightly less weight; it already approximates expected.
        parts.append(_clamp(1.0 + (xera - 3.60) * 0.08, PITCHER_MULT_FLOOR, PITCHER_MULT_CEIL))

    if not parts:
        return 1.0, reasons
    mult = _clamp(sum(parts) / len(parts), PITCHER_MULT_FLOOR, PITCHER_MULT_CEIL)
    if mult >= 1.05:
        reasons.append(RC_PITCHER_QUALITY_FAVOR)
    elif mult <= 0.95:
        reasons.append(RC_PITCHER_QUALITY_HURTS)
    return mult, reasons


def _park_multiplier(park_runs_mult: Optional[float]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if park_runs_mult is None:
        return 1.0, reasons
    pm = _clamp(float(park_runs_mult), PARK_MULT_FLOOR, PARK_MULT_CEIL)
    if pm >= 1.05:
        reasons.append(RC_PARK_FAVOR)
    elif pm <= 0.95:
        reasons.append(RC_PARK_HURTS)
    return pm, reasons


def _savant_multiplier(savant: Optional[dict]) -> tuple[float, list[str]]:
    """Soft xwOBA-based modulator. xwOBA above league avg → small bump.

    Capped at ±8% to keep the engine conservative.
    """
    reasons: list[str] = []
    if not isinstance(savant, dict):
        return 1.0, reasons
    xw = _safe_float(savant.get("xwoba"))
    if xw is None:
        return 1.0, reasons
    delta = xw - LEAGUE_AVG_XWOBA
    # 30 pts of xwOBA above league → ~+5%.
    mult = _clamp(1.0 + delta * 1.6, 0.92, 1.08)
    if mult >= 1.03:
        reasons.append(RC_SAVANT_USED)
    return mult, reasons


def _recent_form_multiplier(recent_form: Optional[dict]) -> tuple[float, float, list[str]]:
    """Return (multiplier_on_lambda, recent_weight_used, reasons).

    ``recent_form`` is the optional last-15 split dict.
    """
    reasons: list[str] = []
    if not isinstance(recent_form, dict):
        return 1.0, 0.0, reasons
    ops_recent = _safe_float(recent_form.get("ops"))
    if ops_recent is None:
        return 1.0, 0.0, reasons
    # Compare against league avg OPS (0.723).
    delta = ops_recent - LEAGUE_AVG_OPS
    mult = _clamp(1.0 + delta * 0.40, 0.85, 1.20)
    if mult >= 1.05:
        reasons.append(RC_RECENT_FORM_HOT)
    elif mult <= 0.95:
        reasons.append(RC_RECENT_FORM_COLD)
    return mult, RECENT_WEIGHT, reasons


def _base_rates_per_game(player: dict, pa_per_game: float) -> dict:
    """Compute per-game rate baselines from season stats.

    Falls back to league averages when individual stats are missing.
    """
    obp = _safe_float(player.get("obp")) or LEAGUE_AVG_OBP
    slg = _safe_float(player.get("slg")) or LEAGUE_AVG_SLG
    avg = _safe_float(player.get("avg")) or 0.245

    games_played = _safe_int(player.get("games_played"))
    runs_total   = _safe_int(player.get("runs"))
    rbi_total    = _safe_int(player.get("rbi"))

    # Per-game baselines.
    if games_played > 0:
        runs_per_g = runs_total / games_played
        rbi_per_g  = rbi_total / games_played
    else:
        runs_per_g = pa_per_game * obp * 0.30    # rough heuristic
        rbi_per_g  = pa_per_game * slg * 0.20

    # Hits per PA proxy ≈ batting AVG (more accurate than OBP for hits).
    hits_per_pa = max(0.10, min(0.45, avg))
    hits_per_g  = pa_per_game * hits_per_pa

    # Total bases per PA ≈ SLG (definition is TB/AB but SLG≈TB/AB ≈ TB per PA after BB-correction).
    tb_per_pa = max(0.10, min(0.85, slg))
    tb_per_g  = pa_per_game * tb_per_pa

    return {
        "hits_per_g":  hits_per_g,
        "tb_per_g":    tb_per_g,
        "runs_per_g":  runs_per_g,
        "rbi_per_g":   rbi_per_g,
        "h_r_rbi_per_g": hits_per_g + runs_per_g + rbi_per_g,
        "pa_per_game": pa_per_game,
        "obp":         obp,
        "slg":         slg,
        "avg":         avg,
    }


def _compute_player_prop_fragility(
    *,
    player: dict,
    market: str,
    opposing_pitcher: Optional[dict],
    data_quality: str,
    base_rates: dict,
) -> dict:
    """Pure player-prop fragility score (0-100) — observe-only.

    Higher score = more context-fragile (less repeatable). This is
    intentionally independent from edge_score / confidence_tier so
    the UI can show both axes.
    """
    score = 0
    reasons: list[str] = []

    avg = _safe_float(player.get("avg")) or 0.0
    slg = _safe_float(player.get("slg")) or 0.0
    obp = _safe_float(player.get("obp")) or 0.0
    iso = max(0.0, slg - avg)             # ISO = SLG - AVG
    hr  = _safe_int(player.get("hr") or player.get("home_runs"))
    games = _safe_int(player.get("games_played"))

    # HR dependency — TB market depends on power outcomes (HR ≥ 25 + ISO ≥ .220).
    if market == MARKET_TB and hr and games:
        hr_per_g = hr / games if games > 0 else 0
        if hr_per_g >= 0.18 or iso >= 0.220:
            score += _FRAGILITY_HR_DEPENDENCY
            reasons.append(RC_FRAG_HR_DEPENDENCY)

    # RBI dependency — RBI markets are extremely context-fragile.
    if market == MARKET_RBI_1P:
        score += _FRAGILITY_RBI_DEPENDENCY
        reasons.append(RC_FRAG_RBI_DEPENDENCY)

    # Boom/bust hitter — high SLG, low OBP, suggests volatile output.
    if slg >= 0.470 and obp <= 0.310:
        score += _FRAGILITY_BOOM_BUST_HITTER
        reasons.append(RC_FRAG_BOOM_BUST_HITTER)

    # Low AVG + high ISO — pure power profile, fragile for H/Hits markets.
    if avg <= 0.230 and iso >= 0.200:
        score += _FRAGILITY_LOW_AVG_HIGH_ISO
        reasons.append(RC_FRAG_LOW_AVG_HIGH_ISO)

    # Elite opposing pitcher.
    if isinstance(opposing_pitcher, dict):
        era  = _safe_float(opposing_pitcher.get("era"))
        whip = _safe_float(opposing_pitcher.get("whip"))
        if (era is not None and era <= 3.00) or (whip is not None and whip <= 1.05):
            score += _FRAGILITY_ELITE_PITCHER
            reasons.append(RC_FRAG_ELITE_PITCHER)

    # Low lineup spot (7-9) — fewer PAs + worse run/RBI context.
    spot = _safe_int(
        player.get("batting_order") or player.get("lineup_position") or 0,
    )
    if spot >= 7:
        score += _FRAGILITY_LOW_LINEUP_SPOT
        reasons.append(RC_FRAG_LOW_LINEUP_SPOT)

    # Data quality penalties.
    if data_quality == "MINIMAL":
        score += _FRAGILITY_DATA_MINIMAL
        reasons.append(RC_FRAG_DATA_MINIMAL)
    elif data_quality == "PARTIAL":
        score += _FRAGILITY_DATA_PARTIAL
        reasons.append(RC_FRAG_DATA_PARTIAL)

    score = max(0, min(100, score))
    if score >= 60:
        bucket = "HIGH"
    elif score >= 30:
        bucket = "MEDIUM"
    else:
        bucket = "LOW"

    return {
        "player_prop_fragility": score,
        "fragility_bucket":      bucket,
        "fragility_reasons":     reasons,
    }


# ──────────────────────────────────────────────────────────────────────
# Single-prop prediction
# ──────────────────────────────────────────────────────────────────────
def predict_player_prop(
    *,
    player: dict,
    opposing_pitcher: Optional[dict] = None,
    park_runs_mult: Optional[float] = None,
    recent_form: Optional[dict] = None,
    savant: Optional[dict] = None,
    market: str = MARKET_H_R_RBI,
    line: Optional[float] = None,
    book_american_odds: Optional[int] = None,
    pa_per_game: Optional[float] = None,
    data_quality: Optional[str] = None,
) -> dict:
    """Predict a single player-prop with deterministic Poisson math.

    Inputs are *raw stats only* — this function never reaches out to
    the network. Higher layers (``compute_player_props_for_game``)
    handle the fetching + caching.
    """
    if market not in ALL_MARKETS:
        return {
            "available":  False,
            "reason":     f"unknown_market:{market}",
        }

    # PA estimate — explicit arg wins, else resolve from batting order.
    if pa_per_game is None:
        pa_per_game = _resolve_pa_per_game(player)
    base   = _base_rates_per_game(player, pa_per_game)
    p_mult, p_reasons    = _pitcher_quality_multiplier(opposing_pitcher or {})
    pk_mult, pk_reasons  = _park_multiplier(park_runs_mult)
    sv_mult, sv_reasons  = _savant_multiplier(savant)
    rf_mult, rf_w, rf_reasons = _recent_form_multiplier(recent_form)

    # Combine multipliers: season component × p × pk × sv,
    # then blend with recent component via weighted average.
    base_mult   = p_mult * pk_mult * sv_mult
    recent_mult = base_mult * rf_mult
    blended_mult = (1.0 - rf_w) * base_mult + rf_w * recent_mult

    # Choose λ source based on the market.
    if market == MARKET_H_R_RBI:
        lam_base = base["h_r_rbi_per_g"]
    elif market == MARKET_TB:
        lam_base = base["tb_per_g"]
    elif market == MARKET_HITS_1P:
        lam_base = base["hits_per_g"]
    elif market == MARKET_RBI_1P:
        lam_base = base["rbi_per_g"]
    elif market == MARKET_RUNS_1P:
        lam_base = base["runs_per_g"]
    else:
        lam_base = 0.0

    lam = max(0.0, lam_base * blended_mult)

    # Default line per market if caller didn't supply one.
    if line is None:
        line = DEFAULT_BOOK_LINES.get(market, 0.5)

    # P(X > line) for over. For half lines (typical) → P(X >= ceil(line)).
    k_target = int(math.floor(line)) + 1
    model_prob = _poisson_p_ge(k_target, lam)

    # Implied probability from book odds.
    if book_american_odds is None:
        book_american_odds = DEFAULT_BOOK_AMERICAN_ODDS.get(market, -110)
    implied = american_odds_to_implied(book_american_odds)

    edge_pts = round((model_prob - implied) * 100.0, 2)

    # Confidence tier — Moneyball-style.
    if model_prob < LONGSHOT_PROB_FLOOR:
        tier = "AVOID"
    elif model_prob >= MONEYBALL_MIN_PROBABILITY and edge_pts >= MONEYBALL_MIN_EDGE_PTS:
        tier = "VALUE"
    elif edge_pts >= MONEYBALL_MIN_EDGE_PTS:
        tier = "WATCH"
    else:
        tier = "AVOID"

    reason_codes: list[str] = []
    reason_codes.extend(p_reasons)
    reason_codes.extend(pk_reasons)
    reason_codes.extend(sv_reasons)
    reason_codes.extend(rf_reasons)
    if tier == "VALUE":
        reason_codes.append(RC_MONEYBALL_VALUE)
    elif tier == "WATCH":
        reason_codes.append(RC_MONEYBALL_WATCH)
    else:
        reason_codes.append(RC_MONEYBALL_AVOID)
    if model_prob < LONGSHOT_PROB_FLOOR:
        reason_codes.append(RC_LONGSHOT_REJECTED)
    if model_prob < MONEYBALL_MIN_PROBABILITY:
        reason_codes.append(RC_LOW_PROBABILITY)
    if edge_pts < MONEYBALL_MIN_EDGE_PTS:
        reason_codes.append(RC_LOW_EDGE)
    if not isinstance(savant, dict) or not savant:
        reason_codes.append(RC_SAVANT_UNAVAILABLE)

    edge_score = max(0, min(100, int(round(edge_pts * 4))))   # 0-100 scale

    reason_codes = list(dict.fromkeys(reason_codes))

    # Player-prop fragility (independent from edge_score / tier).
    # Uses a conservative quality label if the caller didn't supply one.
    _dq = data_quality
    if not _dq:
        if isinstance(savant, dict) and savant and not savant.get("_savant_failed"):
            _dq = "COMPLETE" if isinstance(recent_form, dict) and recent_form else "PARTIAL"
        else:
            _dq = "PARTIAL" if isinstance(recent_form, dict) and recent_form else "MINIMAL"
    fragility_block = _compute_player_prop_fragility(
        player=player, market=market,
        opposing_pitcher=opposing_pitcher,
        data_quality=_dq, base_rates=base,
    )

    return {
        "available":           True,
        "engine_version":      ENGINE_VERSION,
        "market":              market,
        "line":                round(line, 2),
        "selection":           "OVER",
        "lambda_estimate":     round(lam, 4),
        "model_probability":   round(model_prob, 4),
        "implied_probability": round(implied, 4),
        "edge_points":         edge_pts,
        "edge_score":          edge_score,
        "confidence_tier":     tier,
        "season_basis": {
            "hits_per_g":     round(base["hits_per_g"], 3),
            "tb_per_g":       round(base["tb_per_g"], 3),
            "runs_per_g":     round(base["runs_per_g"], 3),
            "rbi_per_g":      round(base["rbi_per_g"], 3),
            "h_r_rbi_per_g":  round(base["h_r_rbi_per_g"], 3),
            "obp":            round(base["obp"], 3),
            "slg":            round(base["slg"], 3),
            "avg":            round(base["avg"], 3),
            "pa_per_game":    round(base["pa_per_game"], 2),
        },
        "recent_form_basis": (recent_form or {}),
        "adjustments": {
            "pitcher_mult": round(p_mult, 3),
            "park_mult":    round(pk_mult, 3),
            "savant_mult":  round(sv_mult, 3),
            "recent_mult":  round(rf_mult, 3),
            "recent_weight": round(rf_w, 3),
            "blended_mult": round(blended_mult, 3),
        },
        "book_american_odds":  book_american_odds,
        "reason_codes":        reason_codes,
        # Phase-57 v2: explicit fragility (observe-only).
        "player_prop_fragility": fragility_block["player_prop_fragility"],
        "fragility_bucket":      fragility_block["fragility_bucket"],
        "fragility_reasons":     fragility_block["fragility_reasons"],
        "pa_per_game":           round(pa_per_game, 2),
    }


# ──────────────────────────────────────────────────────────────────────
# Game-level + day-level orchestration
# ──────────────────────────────────────────────────────────────────────
def _data_quality_label(player: dict, recent_form: Optional[dict], savant: Optional[dict]) -> str:
    have_season = (
        _safe_float(player.get("obp")) is not None
        and _safe_float(player.get("slg")) is not None
        and _safe_int(player.get("games_played")) >= 10
    )
    have_recent = isinstance(recent_form, dict) and bool(recent_form)
    have_savant = isinstance(savant, dict) and bool(savant) and not savant.get("_savant_failed")
    if have_season and have_recent and have_savant:
        return "COMPLETE"
    if have_season and (have_recent or have_savant):
        return "PARTIAL"
    return "MINIMAL"


def _build_narrative_es(
    *, player_name: str, market: str, line: float,
    model_prob: float, edge_pts: float, tier: str,
    adjustments: dict, opposing_pitcher_name: Optional[str] = None,
) -> str:
    market_label = {
        MARKET_H_R_RBI:  f"H+R+RBI Over {line}",
        MARKET_TB:       f"Bases Totales Over {line}",
        MARKET_HITS_1P:  "1+ Hits",
        MARKET_RBI_1P:   "1+ RBI",
        MARKET_RUNS_1P:  "1+ Carrera Anotada",
    }.get(market, market)
    head = (
        f"{player_name} — {market_label}: "
        f"prob {round(model_prob * 100, 1)}% / edge {round(edge_pts, 1)} pts ({tier})."
    )
    drivers: list[str] = []
    p_mult = adjustments.get("pitcher_mult", 1.0)
    pk_mult = adjustments.get("park_mult", 1.0)
    sv_mult = adjustments.get("savant_mult", 1.0)
    rf_mult = adjustments.get("recent_mult", 1.0)
    if p_mult and p_mult >= 1.05:
        if opposing_pitcher_name:
            drivers.append(f"abridor débil ({opposing_pitcher_name})")
        else:
            drivers.append("abridor débil")
    elif p_mult and p_mult <= 0.95:
        drivers.append("abridor élite enfrente")
    if pk_mult and pk_mult >= 1.05:
        drivers.append("estadio pro-bateador")
    elif pk_mult and pk_mult <= 0.95:
        drivers.append("estadio pro-pitcher")
    if sv_mult and sv_mult >= 1.03:
        drivers.append("xwOBA sobre la media")
    if rf_mult and rf_mult >= 1.05:
        drivers.append("forma reciente caliente")
    elif rf_mult and rf_mult <= 0.95:
        drivers.append("forma reciente fría")
    if drivers:
        return head + " Drivers: " + " · ".join(drivers) + "."
    return head


async def _enrich_with_savant_failsoft(
    players: list[dict], db: Any = None, season: Optional[int] = None,
    timeout_sec: float = 6.0,
) -> dict:
    """Enrich a list of hitter dicts with Savant batter data in
    parallel. Returns {player_id: savant_dict_or_None}. Fail-soft."""
    out: dict[int, Optional[dict]] = {}
    if not players:
        return out
    try:
        from .baseball_savant_batter import fetch_batter_savant
    except Exception:
        return out

    async def _one(pid: int):
        try:
            return pid, await fetch_batter_savant(pid, season=season, db=db,
                                                   timeout_sec=timeout_sec)
        except Exception:
            return pid, None

    tasks = []
    for p in players:
        pid = p.get("id") or p.get("player_id")
        if pid:
            tasks.append(_one(int(pid)))
    if not tasks:
        return out
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        return out
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        pid, data = r
        out[pid] = data
    return out


def _select_best_market_per_player(predictions: list[dict]) -> Optional[dict]:
    """For each player, keep ONLY the best prop (Moneyball priority).

    Selection rules
    ---------------
    1. Drop any prop in AVOID tier.
    2. Within the remaining set, prefer VALUE > WATCH (tier rank).
    3. Within tier, follow ``MARKET_PRIORITY_ORDER`` — H+R+RBI is
       considered more repeatable than RBI 1+ even when RBI 1+ has a
       slightly higher edge score.
    4. Tie-break by edge_score (descending), then model_probability.
    """
    if not predictions:
        return None
    eligible = [p for p in predictions if p.get("confidence_tier") in ("VALUE", "WATCH")]
    if not eligible:
        return None

    def _market_priority(p: dict) -> int:
        m = p.get("market") or ""
        try:
            return MARKET_PRIORITY_ORDER.index(m)
        except ValueError:
            return len(MARKET_PRIORITY_ORDER)

    eligible.sort(
        key=lambda d: (
            0 if d.get("confidence_tier") == "VALUE" else 1,
            _market_priority(d),
            -float(d.get("edge_score") or 0),
            -float(d.get("model_probability") or 0),
        )
    )
    return eligible[0]


async def compute_player_props_for_game(
    game_pk: int,
    *,
    db: Any = None,
    use_savant: bool = True,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
    home_pitcher: Optional[dict] = None,
    away_pitcher: Optional[dict] = None,
    park_runs_mult: Optional[float] = None,
    season: Optional[int] = None,
    # Optional pre-resolved rosters (used by orchestrator integration
    # to avoid re-fetching).
    home_roster: Optional[list[dict]] = None,
    away_roster: Optional[list[dict]] = None,
) -> dict:
    """Compute Moneyball player props for a single game.

    Fail-soft: returns ``{"available": False, ...}`` when essential
    inputs are missing or roster fetch fails.
    """
    if not (home_team_id or away_team_id or home_roster or away_roster):
        return {
            "available":     False,
            "engine_version": ENGINE_VERSION,
            "reason":         "no_team_inputs",
        }

    season = season or datetime.now(timezone.utc).year

    # ── Resolve rosters fail-soft ───────────────────────────────────
    if home_roster is None and home_team_id:
        try:
            from .mlb_stats_api import hydrate_team_offensive_roster
            payload = await hydrate_team_offensive_roster(db, home_team_id, season=season)
            home_roster = (payload or {}).get("players") if (payload or {}).get("available") else []
        except Exception as exc:
            log.debug("home roster fetch failed: %s", exc)
            home_roster = []

    if away_roster is None and away_team_id:
        try:
            from .mlb_stats_api import hydrate_team_offensive_roster
            payload = await hydrate_team_offensive_roster(db, away_team_id, season=season)
            away_roster = (payload or {}).get("players") if (payload or {}).get("available") else []
        except Exception as exc:
            log.debug("away roster fetch failed: %s", exc)
            away_roster = []

    home_roster = home_roster or []
    away_roster = away_roster or []

    if not home_roster and not away_roster:
        return {
            "available":     False,
            "engine_version": ENGINE_VERSION,
            "reason":         "no_rosters",
        }

    # ── Optional Savant enrichment ──────────────────────────────────
    savant_by_pid: dict[int, Optional[dict]] = {}
    if use_savant:
        all_players = list(home_roster) + list(away_roster)
        savant_by_pid = await _enrich_with_savant_failsoft(
            all_players, db=db, season=season,
        )

    # ── Build per-player predictions ────────────────────────────────
    def _process_roster(roster: list[dict], team_name: Optional[str],
                        opp_pitcher: Optional[dict], opp_team_name: Optional[str]) -> list[dict]:
        out: list[dict] = []
        for player in roster:
            pos = player.get("position")
            if not _is_offensive_position(pos):
                continue
            # Players with <10 games played → MINIMAL data (still allowed
            # but flagged).
            pid = player.get("id")
            savant_data = savant_by_pid.get(int(pid)) if pid else None
            recent_form = player.get("recent_form_last_15") or player.get("recent_form")

            predictions: list[dict] = []
            # Compute data_quality once per player so fragility & narrative align.
            dq_for_player = _data_quality_label(player, recent_form, savant_data)
            for market in ALL_MARKETS:
                pred = predict_player_prop(
                    player=player,
                    opposing_pitcher=opp_pitcher,
                    park_runs_mult=park_runs_mult,
                    recent_form=recent_form,
                    savant=savant_data,
                    market=market,
                    data_quality=dq_for_player,
                )
                if pred.get("available"):
                    predictions.append(pred)

            best = _select_best_market_per_player(predictions)
            if not best:
                continue

            dq = dq_for_player
            if dq == "COMPLETE":
                best.setdefault("reason_codes", []).append(RC_DATA_QUALITY_COMPLETE)
            elif dq == "PARTIAL":
                best.setdefault("reason_codes", []).append(RC_DATA_QUALITY_PARTIAL)
            else:
                best.setdefault("reason_codes", []).append(RC_DATA_QUALITY_MINIMAL)

            best["reason_codes"] = list(dict.fromkeys(best["reason_codes"]))

            best["narrative_es"] = _build_narrative_es(
                player_name=player.get("name") or "?",
                market=best["market"],
                line=best["line"],
                model_prob=best["model_probability"],
                edge_pts=best["edge_points"],
                tier=best["confidence_tier"],
                adjustments=best.get("adjustments") or {},
                opposing_pitcher_name=(opp_pitcher or {}).get("name"),
            )
            out.append({
                "game_pk":     game_pk,
                "team":        team_name,
                "opponent":    opp_team_name,
                "player_id":   pid,
                "player_name": player.get("name"),
                "position":    pos,
                "data_quality": dq,
                **best,
            })
        return out

    home_props = _process_roster(home_roster, home_team_name, away_pitcher, away_team_name)
    away_props = _process_roster(away_roster, away_team_name, home_pitcher, home_team_name)

    all_props = home_props + away_props
    # Sort by descending edge_score / VALUE first.
    all_props.sort(
        key=lambda d: (
            0 if d.get("confidence_tier") == "VALUE" else 1,
            -float(d.get("edge_score") or 0),
        )
    )

    dq_counter = {"COMPLETE": 0, "PARTIAL": 0, "MINIMAL": 0}
    for p in all_props:
        dq_counter[p.get("data_quality", "MINIMAL")] = (
            dq_counter.get(p.get("data_quality", "MINIMAL"), 0) + 1
        )

    return {
        "available":      True,
        "engine_version": ENGINE_VERSION,
        "game_pk":        game_pk,
        "home_team":      home_team_name,
        "away_team":      away_team_name,
        "props":          all_props,
        "props_total":    len(all_props),
        "props_value":    sum(1 for p in all_props if p.get("confidence_tier") == "VALUE"),
        "props_watch":    sum(1 for p in all_props if p.get("confidence_tier") == "WATCH"),
        "data_quality_summary": dq_counter,
        "savant_used":    use_savant,
    }


async def compute_player_props_for_day(
    date_str: Optional[str] = None,
    *,
    db: Any = None,
    use_savant: bool = True,
    max_games: int = 20,
) -> dict:
    """Top-level Moneyball props discovery for an entire MLB day.

    Strategy
    --------
    1. Fetch the day's schedule via ``mlb_stats_api.get_schedule_with_probables``.
    2. For each confirmed game with both probable pitchers known, run
       ``compute_player_props_for_game``.
    3. Aggregate all VALUE/WATCH props for the slate.
    """
    if not date_str:
        date_str = datetime.now(EASTERN).strftime("%Y-%m-%d")

    try:
        from .mlb_stats_api import get_schedule_with_probables
        games = await get_schedule_with_probables(db, date_str)
    except Exception as exc:
        log.debug("schedule fetch failed for player props: %s", exc)
        return {
            "available":     False,
            "engine_version": ENGINE_VERSION,
            "date":           date_str,
            "reason":         "schedule_unavailable",
            "error":          str(exc),
        }

    if not games:
        return {
            "available":      True,
            "engine_version": ENGINE_VERSION,
            "date":            date_str,
            "games_processed": 0,
            "props":           [],
            "props_total":     0,
            "props_value":     0,
            "props_watch":     0,
            "data_quality_summary": {"COMPLETE": 0, "PARTIAL": 0, "MINIMAL": 0},
            "savant_used":     use_savant,
        }

    games = games[:max_games]
    per_game: list[dict] = []
    for g in games:
        gid = g.get("gamePk")
        if not gid:
            continue
        # Skip games without both probable pitchers — props would be
        # essentially un-calibrated.
        if not g.get("home_probable_id") or not g.get("away_probable_id"):
            continue
        try:
            res = await compute_player_props_for_game(
                int(gid),
                db=db, use_savant=use_savant,
                home_team_id=g.get("home_team_id"),
                away_team_id=g.get("away_team_id"),
                home_team_name=g.get("home_team"),
                away_team_name=g.get("away_team"),
                home_pitcher={
                    "name": g.get("home_probable_name"),
                    "id":   g.get("home_probable_id"),
                    "era":  g.get("home_probable_era"),
                    "whip": g.get("home_probable_whip"),
                },
                away_pitcher={
                    "name": g.get("away_probable_name"),
                    "id":   g.get("away_probable_id"),
                    "era":  g.get("away_probable_era"),
                    "whip": g.get("away_probable_whip"),
                },
                park_runs_mult=g.get("park_runs_mult"),
            )
            if res.get("available"):
                per_game.append(res)
        except Exception as exc:
            log.debug("per-game props failed for %s: %s", gid, exc)
            continue

    flat_props: list[dict] = []
    dq_counter = {"COMPLETE": 0, "PARTIAL": 0, "MINIMAL": 0}
    for gp in per_game:
        flat_props.extend(gp.get("props") or [])
        for k, v in (gp.get("data_quality_summary") or {}).items():
            dq_counter[k] = dq_counter.get(k, 0) + int(v or 0)

    flat_props.sort(
        key=lambda d: (
            0 if d.get("confidence_tier") == "VALUE" else 1,
            -float(d.get("edge_score") or 0),
        )
    )

    return {
        "available":      True,
        "engine_version": ENGINE_VERSION,
        "date":            date_str,
        "games_processed": len(per_game),
        "props":           flat_props,
        "props_total":     len(flat_props),
        "props_value":     sum(1 for p in flat_props if p.get("confidence_tier") == "VALUE"),
        "props_watch":     sum(1 for p in flat_props if p.get("confidence_tier") == "WATCH"),
        "data_quality_summary": dq_counter,
        "savant_used":     use_savant,
    }


__all__ = [
    "ENGINE_VERSION",
    "MARKET_H_R_RBI", "MARKET_TB",
    "MARKET_HITS_1P", "MARKET_RBI_1P", "MARKET_RUNS_1P",
    "ALL_MARKETS",
    "DEFAULT_BOOK_LINES", "DEFAULT_BOOK_AMERICAN_ODDS",
    "MONEYBALL_MIN_PROBABILITY", "MONEYBALL_MIN_EDGE_PTS", "LONGSHOT_PROB_FLOOR",
    "LINEUP_POSITION_PA", "MARKET_PRIORITY_ORDER",
    "RC_MONEYBALL_VALUE", "RC_MONEYBALL_WATCH", "RC_MONEYBALL_AVOID",
    "RC_LONGSHOT_REJECTED", "RC_LOW_PROBABILITY", "RC_LOW_EDGE",
    "RC_DATA_QUALITY_COMPLETE", "RC_DATA_QUALITY_PARTIAL", "RC_DATA_QUALITY_MINIMAL",
    "RC_SAVANT_USED", "RC_SAVANT_UNAVAILABLE",
    "RC_PITCHER_QUALITY_FAVOR", "RC_PITCHER_QUALITY_HURTS",
    "RC_PARK_FAVOR", "RC_PARK_HURTS",
    "RC_RECENT_FORM_HOT", "RC_RECENT_FORM_COLD",
    "RC_POSITION_PITCHER_EXCLUDED",
    "RC_FRAG_HR_DEPENDENCY", "RC_FRAG_RBI_DEPENDENCY",
    "RC_FRAG_BOOM_BUST_HITTER", "RC_FRAG_LOW_AVG_HIGH_ISO",
    "RC_FRAG_ELITE_PITCHER", "RC_FRAG_LOW_LINEUP_SPOT",
    "RC_FRAG_DATA_MINIMAL", "RC_FRAG_DATA_PARTIAL",
    "american_odds_to_implied",
    "_resolve_pa_per_game",
    "_compute_player_prop_fragility",
    "predict_player_prop",
    "compute_player_props_for_game",
    "compute_player_props_for_day",
]
