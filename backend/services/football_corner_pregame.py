"""Football Corner Pregame — builds `_corner_form` from per-team recent
fixtures BEFORE the rescue pipeline runs.

Why this exists
---------------
The existing `corner_market_layer.find_corner_value()` was wired to fire
during the **live** rescue and during the **post-fetch** path that pulls
each team's last-5 fixtures from API-Sports. It works, but it fails in
two common cases:

  1. API-Sports rate-limit / timeout → `_corner_form` is never set →
     the rescue layer skips the match.
  2. The match doc already carries `recent_fixtures` populated by the
     historical enrichment service → re-fetching is wasted I/O.

`attach_pregame_corner_form()` is a **pure, sync** function that reads
the corner fields out of an already-present `recent_fixtures` list and
materialises a `_corner_form` payload with `mode="pregame"`. The
orchestrator calls it BEFORE `attempt_alternative_market_rescue()` so
the corner market layer always has a profile to work with.

Expected match shape
--------------------
    match["home_team"]["context"]["recent_fixtures"] = [
        {"corners_for": 6, "corners_against": 3, "total_corners": 9},
        ...
    ]
    match["away_team"]["context"]["recent_fixtures"] = [...]

`recent_fixtures` may also be flat as `last_matches`. The function
silently accepts either. Each fixture may provide any combination of
`corners_for`, `corners_against`, `total_corners` — missing fields are
ignored, never raised.

Output (mutates the match)
--------------------------
    match["_corner_form"] = {
        "mode":                    "pregame",
        "home":                    {sample_size, corners_for_avg, ...},
        "away":                    {...},
        "expected_home_corners":   float | None,
        "expected_away_corners":   float | None,
        "expected_total_corners":  float | None,
        "data_quality":            "strong" | "usable" | "thin" | "insufficient",
    }
"""
from __future__ import annotations

from statistics import mean
from typing import Optional


__all__ = [
    "build_team_corner_profile",
    "attach_pregame_corner_form",
    "detect_corner_trap_signals",
    "CORNER_TRAP_REASON_LABELS_ES",
]


def _avg(values: list[float]) -> Optional[float]:
    """Mean of clean floats, rounded to 2 decimals, None if empty."""
    clean: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            clean.append(float(v))
        except (TypeError, ValueError):
            continue
    return round(mean(clean), 2) if clean else None


def _rate(values: list[float], line: float) -> Optional[float]:
    """Empirical Over rate for `line` across `values`. None if no data."""
    if not values:
        return None
    try:
        line_f = float(line)
    except (TypeError, ValueError):
        return None
    hits = sum(1 for v in values if v is not None and float(v) > line_f)
    return round(hits / len(values), 3)


def build_team_corner_profile(team_context: dict) -> dict:
    """Compute corner statistics from a team's recent_fixtures.

    Accepts either ``recent_fixtures`` or ``last_matches`` (alias).
    Each fixture may carry any of ``corners_for``, ``corners_against``,
    ``total_corners``. When ``total_corners`` is absent but both
    ``corners_for`` and ``corners_against`` are present, the sum is used.

    Up to the most recent 10 fixtures are honoured.
    """
    if not isinstance(team_context, dict):
        team_context = {}

    fixtures = (
        team_context.get("recent_fixtures")
        or team_context.get("last_matches")
        or []
    )
    if not isinstance(fixtures, list):
        fixtures = []

    corners_for: list[float] = []
    corners_against: list[float] = []
    total_corners: list[float] = []

    for f in fixtures[:10]:
        if not isinstance(f, dict):
            continue
        cf = f.get("corners_for")
        ca = f.get("corners_against")
        tc = f.get("total_corners")

        if cf is not None:
            try:
                corners_for.append(float(cf))
            except (TypeError, ValueError):
                cf = None
        if ca is not None:
            try:
                corners_against.append(float(ca))
            except (TypeError, ValueError):
                ca = None

        if tc is not None:
            try:
                total_corners.append(float(tc))
            except (TypeError, ValueError):
                # fall through to the cf+ca composition below
                tc = None
        if tc is None and cf is not None and ca is not None:
            try:
                total_corners.append(float(cf) + float(ca))
            except (TypeError, ValueError):
                pass

    return {
        "sample_size":          len(total_corners),
        "corners_for_avg":      _avg(corners_for),
        "corners_against_avg":  _avg(corners_against),
        "total_corners_avg":    _avg(total_corners),
        "over_8_5_rate":        _rate(total_corners, 8.5),
        "over_9_5_rate":        _rate(total_corners, 9.5),
        "over_10_5_rate":       _rate(total_corners, 10.5),
        "raw_recent_totals":    total_corners[:10],
    }


def _corner_data_quality(home_profile: dict, away_profile: dict) -> str:
    """Coalesce the smaller of the two sample sizes into a quality label.

    Thresholds tightened per user feedback: corner stats benefit from a
    larger window because possession patterns + pressing intensity drift
    notably over short stretches. Minimum reliable sample is 7 partidos
    por equipo (≈ one stable form block).
    """
    n = min(
        home_profile.get("sample_size") or 0,
        away_profile.get("sample_size") or 0,
    )
    if n >= 10:
        return "strong"
    if n >= 7:
        return "usable"
    if n >= 4:
        return "thin"
    return "insufficient"


# ════════════════════════════════════════════════════════════════════════════
# TRAP SIGNALS  (corners-specific)
# ════════════════════════════════════════════════════════════════════════════
#
# Catalogue of structural reasons NOT to take a corner pick even when the
# raw average suggests value. Each detector returns a dict
#     {"code": str, "severity": "high"|"medium", "explanation": str}
# or None if the pattern doesn't apply.
#
# These signals are surfaced in `_corner_form["trap_signals"]` so the
# rescue layer (`corner_market_layer._pregame_protected_recommendation`)
# can decline picks even when the projection looks attractive.


CORNER_TRAP_REASON_LABELS_ES = {
    "SLOW_POSSESSION_LOW_DEPTH":   (
        "Ambos equipos con posesión lenta y poca profundidad — "
        "córners proyectados no se materializan."
    ),
    "EARLY_SCORING_FAVOURITE":     (
        "Favorito muy corto que suele marcar temprano y controlar el ritmo — "
        "el partido tiende a abrirse y bajan los córners."
    ),
    "ONE_SIDED_PRESSURE":          (
        "Asimetría marcada en generación de córners — un equipo concentra "
        "casi todos, fragiliza Over si el otro no aparece."
    ),
}


def _to_float_safe(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _detect_slow_possession_low_depth(
    home_context: dict, away_context: dict,
) -> Optional[dict]:
    """High severity when both teams exhibit slow possession AND low
    attacking depth (shots on target per 90 or penalty-area touches).

    Expected context fields (any combination — fail-soft):
      - possession_avg          0-100 (lower = slower)
      - shots_on_target_per_90  float
      - touches_in_box_per_90   float
      - depth_score             0-100 already-normalised flag
    """
    def _slow_low(ctx: dict) -> Optional[bool]:
        if not isinstance(ctx, dict):
            return None
        poss = _to_float_safe(ctx.get("possession_avg"))
        sot  = _to_float_safe(ctx.get("shots_on_target_per_90"))
        box  = _to_float_safe(ctx.get("touches_in_box_per_90"))
        depth = _to_float_safe(ctx.get("depth_score"))
        # Need at least possession + one depth proxy to decide.
        if poss is None:
            return None
        slow = poss <= 48.0
        low_depth = False
        if depth is not None:
            low_depth = depth <= 35.0
        elif sot is not None:
            low_depth = sot <= 3.2
        elif box is not None:
            low_depth = box <= 18.0
        else:
            return None
        return slow and low_depth

    h = _slow_low(home_context)
    a = _slow_low(away_context)
    if h is True and a is True:
        return {
            "code":        "SLOW_POSSESSION_LOW_DEPTH",
            "severity":    "high",
            "explanation": CORNER_TRAP_REASON_LABELS_ES["SLOW_POSSESSION_LOW_DEPTH"],
        }
    return None


def _detect_early_scoring_favourite(match: dict) -> Optional[dict]:
    """High severity when one side is a heavy favourite (decimal odds
    ≤ 1.45) AND has a high rate of scoring before minute 30. The
    moment they go ahead they typically slow the pace down and corner
    volume drops sharply.

    Looks at:
      match["odds"]["1x2"]                 → favourite price
      home_team/away_team ["context"]["early_goal_pct"]   → 0-1 share of
        games where they scored before min 30 (last 10 matches).
    """
    if not isinstance(match, dict):
        return None
    odds = match.get("odds") or {}
    ml = odds.get("1x2") or odds.get("moneyline") or odds.get("h2h") or {}
    if not isinstance(ml, dict):
        return None
    home_odd = _to_float_safe(ml.get("home"))
    away_odd = _to_float_safe(ml.get("away"))

    fav_side: Optional[str] = None
    if home_odd is not None and home_odd <= 1.45:
        fav_side = "home"
    elif away_odd is not None and away_odd <= 1.45:
        fav_side = "away"
    if fav_side is None:
        return None

    fav_team = match.get(f"{fav_side}_team") or {}
    ctx = fav_team.get("context") or {}
    # FIX — prefer the canonical seasonal early-goal profile populated by
    # `analyst_engine._prefetch_early_goal_profiles` (SoccerSTATS + API-Sports
    # derived). Fall back to a flat `early_goal_pct` on the context for
    # backwards compatibility with hand-set fixtures.
    seasonal = ctx.get("seasonal_form") or {}
    early_profile = seasonal.get("early_goal_profile") or {}
    early_pct = _to_float_safe(
        early_profile.get("early_goal_pct")
        if early_profile.get("data_quality") in ("strong", "usable", "thin")
        else None
    )
    if early_pct is None:
        early_pct = _to_float_safe(ctx.get("early_goal_pct"))
    if early_pct is None:
        return None
    if early_pct >= 0.55:
        return {
            "code":        "EARLY_SCORING_FAVOURITE",
            "severity":    "high",
            "explanation": CORNER_TRAP_REASON_LABELS_ES["EARLY_SCORING_FAVOURITE"],
        }
    if early_pct >= 0.40:
        return {
            "code":        "EARLY_SCORING_FAVOURITE",
            "severity":    "medium",
            "explanation": CORNER_TRAP_REASON_LABELS_ES["EARLY_SCORING_FAVOURITE"],
        }
    return None


def _detect_one_sided_pressure(
    home_profile: dict, away_profile: dict,
) -> Optional[dict]:
    """Medium severity when one team's corners_for_avg is more than 2.5x
    the other's. Suggests an Over bet relies almost entirely on one side
    showing up — fragile spot.
    """
    h = _to_float_safe(home_profile.get("corners_for_avg"))
    a = _to_float_safe(away_profile.get("corners_for_avg"))
    if h is None or a is None or h <= 0 or a <= 0:
        return None
    ratio = max(h, a) / min(h, a)
    if ratio >= 2.5:
        return {
            "code":        "ONE_SIDED_PRESSURE",
            "severity":    "medium",
            "explanation": CORNER_TRAP_REASON_LABELS_ES["ONE_SIDED_PRESSURE"],
        }
    return None


def detect_corner_trap_signals(
    match: dict, home_profile: dict, away_profile: dict,
) -> list[dict]:
    """Run all corner trap detectors and return the active ones."""
    signals: list[dict] = []
    home_ctx = (match.get("home_team") or {}).get("context") or {}
    away_ctx = (match.get("away_team") or {}).get("context") or {}

    for detector in (
        lambda: _detect_slow_possession_low_depth(home_ctx, away_ctx),
        lambda: _detect_early_scoring_favourite(match),
        lambda: _detect_one_sided_pressure(home_profile, away_profile),
    ):
        try:
            s = detector()
        except Exception:
            s = None
        if s:
            signals.append(s)
    return signals


def attach_pregame_corner_form(match: dict) -> dict:
    """Mutates ``match["_corner_form"]`` so the corner rescue layer can run
    pre-match (not just live). Idempotent.

    Skips when ``match`` is None or when ``_corner_form`` is already set
    with ``mode="live"`` — the live path is more accurate and must not be
    overwritten.
    """
    if not isinstance(match, dict):
        return match

    existing = match.get("_corner_form") or {}
    # Never clobber the live form — it's authoritative.
    if isinstance(existing, dict) and existing.get("mode") == "live":
        return match

    home = match.get("home_team") or {}
    away = match.get("away_team") or {}

    # Accept context in either shape:
    #   team["context"]["recent_fixtures"]  ← canonical
    #   team["recent_fixtures"]             ← flat fallback
    home_context = home.get("context") or {"recent_fixtures": home.get("recent_fixtures")}
    away_context = away.get("context") or {"recent_fixtures": away.get("recent_fixtures")}

    home_profile = build_team_corner_profile(home_context)
    away_profile = build_team_corner_profile(away_context)

    home_expected: Optional[float] = None
    away_expected: Optional[float] = None
    combined_avg: Optional[float] = None

    if (home_profile.get("corners_for_avg") is not None
            and away_profile.get("corners_against_avg") is not None):
        home_expected = (
            home_profile["corners_for_avg"] + away_profile["corners_against_avg"]
        ) / 2

    if (away_profile.get("corners_for_avg") is not None
            and home_profile.get("corners_against_avg") is not None):
        away_expected = (
            away_profile["corners_for_avg"] + home_profile["corners_against_avg"]
        ) / 2

    if home_expected is not None and away_expected is not None:
        combined_avg = round(home_expected + away_expected, 2)

    # Run trap detection AFTER the profiles are built. Fail-soft.
    try:
        trap_signals = detect_corner_trap_signals(
            match, home_profile, away_profile,
        )
    except Exception:
        trap_signals = []

    match["_corner_form"] = {
        "mode":                    "pregame",
        "home":                    home_profile,
        "away":                    away_profile,
        "expected_home_corners":   (
            round(home_expected, 2) if home_expected is not None else None
        ),
        "expected_away_corners":   (
            round(away_expected, 2) if away_expected is not None else None
        ),
        "expected_total_corners":  combined_avg,
        "data_quality":            _corner_data_quality(home_profile, away_profile),
        "trap_signals":            trap_signals,
    }
    return match
