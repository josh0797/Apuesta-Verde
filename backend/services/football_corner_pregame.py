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
    """Coalesce the smaller of the two sample sizes into a quality label."""
    n = min(
        home_profile.get("sample_size") or 0,
        away_profile.get("sample_size") or 0,
    )
    if n >= 8:
        return "strong"
    if n >= 5:
        return "usable"
    if n >= 3:
        return "thin"
    return "insufficient"


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
    }
    return match
