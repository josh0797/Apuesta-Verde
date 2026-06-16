"""FIX-4 — Corners Profile (pure module).

Pre-match corners must be derived from each team's *historical* match
log, NOT from the current fixture (which hasn't been played yet). This
module owns the deterministic, side-effect-free computation that turns
two per-team corner-history vectors into:

  * L1 / L5 / L15 averages (corners for + against),
  * a Corner Momentum Score (5 states),
  * an Expected Corners projection,
  * Combined L5 / L15 averages,
  * Over/Under line projections.

The I/O — fetching the history vectors from TheStatsAPI / API-Sports
with a 24h cache — lives in :mod:`services.football_corners_history`.

Inputs (per team)
-----------------
Each ``team_history`` is a list of dicts, newest-first::

    [
      {"match_id": "mt_X1", "kickoff_iso": "...",
       "corners_for": 8, "corners_against": 5},
      {"match_id": "mt_X2", ..., "corners_for": 6, "corners_against": 3},
      ...
    ]

Missing samples (``None``) are skipped — they don't sink an L5/L15.

Reason codes (canonical, stable)
--------------------------------
* ``CORNERS_PROFILE_OK``
* ``CORNERS_HISTORY_INSUFFICIENT_SAMPLE``
* ``CORNERS_TEAM_HISTORY_NOT_FOUND``
* ``CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED``

Status enum
-----------
* ``OK``                — both teams have ≥ ``min_sample`` samples.
* ``PARTIAL``           — at least one team has < ``min_sample`` but > 0.
* ``UNAVAILABLE``       — at least one team has 0 samples or not found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────
#   Reason codes
# ─────────────────────────────────────────────────────────────────────
RC_CORNERS_PROFILE_OK = "CORNERS_PROFILE_OK"
RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE = "CORNERS_HISTORY_INSUFFICIENT_SAMPLE"
RC_CORNERS_TEAM_HISTORY_NOT_FOUND = "CORNERS_TEAM_HISTORY_NOT_FOUND"
RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED = (
    "CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED"
)

# Momentum states.
MOMENTUM_BULLISH_STRONG          = "BULLISH_STRONG"            # 🔥 L1 > L5 > L15
MOMENTUM_BULLISH_STABLE          = "BULLISH_STABLE"            # 📈 L1 ≈ L5, L5 > L15
MOMENTUM_BULLISH_LOSING_MOMENTUM = "BULLISH_LOSING_MOMENTUM"   # ⚠️ L1 < L5, L5 > L15
MOMENTUM_BEARISH                 = "BEARISH"                   # 📉 L5 < L15
MOMENTUM_NEUTRAL                 = "NEUTRAL"                   # plano / muestra mínima

# Profile statuses.
STATUS_OK          = "OK"
STATUS_PARTIAL     = "PARTIAL"
STATUS_UNAVAILABLE = "UNAVAILABLE"


# ─────────────────────────────────────────────────────────────────────
#   Helpers
# ─────────────────────────────────────────────────────────────────────

def _safe_avg(values: list[float | int | None]) -> Optional[float]:
    """Return the mean of non-``None`` values rounded to 2 decimals."""
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _safe_int_or_none(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trend_pct(l5: Optional[float], l15: Optional[float]) -> Optional[float]:
    """Percent change L5 vs L15, rounded to 1 decimal. ``None`` on bad input."""
    if l5 is None or l15 is None or l15 == 0:
        return None
    return round(((l5 - l15) / l15) * 100, 1)


# ─────────────────────────────────────────────────────────────────────
#   Momentum classifier
# ─────────────────────────────────────────────────────────────────────

# Tolerance band for "L1 ≈ L5" detection (corners are discrete; 1 corner
# of slack avoids false "losing-momentum" trips).
_MOMENTUM_TOLERANCE = 1.0


def compute_corner_momentum(
    l1: Optional[float],
    l5: Optional[float],
    l15: Optional[float],
) -> dict[str, Any]:
    """Classify the corner momentum into one of the 5 canonical states.

    Returns a dict::

        {
          "state":       <MOMENTUM_*>,
          "label_es":    str,
          "label_en":    str,
          "trend_delta": float | None,
          "trend_pct":   float | None,
        }

    Rules (cascade, first match wins):
      * Any of (l1, l5, l15) is None  → NEUTRAL (insufficient signal).
      * l5 < l15 (strict)             → BEARISH.
      * l1 > l5 + tol  AND l5 > l15   → BULLISH_STRONG.
      * |l1 - l5| <= tol AND l5 > l15 → BULLISH_STABLE.
      * l1 + tol < l5  AND l5 > l15   → BULLISH_LOSING_MOMENTUM.
      * Otherwise                      → NEUTRAL.
    """
    trend_delta = None
    trend_pct = None
    if l5 is not None and l15 is not None:
        trend_delta = round(l5 - l15, 2)
        trend_pct = _trend_pct(l5, l15)

    if l1 is None or l5 is None or l15 is None:
        return {
            "state":       MOMENTUM_NEUTRAL,
            "label_es":    "Sin muestra suficiente",
            "label_en":    "Insufficient sample",
            "trend_delta": trend_delta,
            "trend_pct":   trend_pct,
        }

    tol = _MOMENTUM_TOLERANCE
    if l5 < l15:
        state = MOMENTUM_BEARISH
        es = "Tendencia bajista"
        en = "Bearish trend"
    elif l5 > l15:
        if l1 > l5 + tol:
            state = MOMENTUM_BULLISH_STRONG
            es = "Alcista fuerte"
            en = "Strongly bullish"
        elif abs(l1 - l5) <= tol:
            state = MOMENTUM_BULLISH_STABLE
            es = "Alcista estable"
            en = "Steadily bullish"
        else:  # l1 + tol < l5
            state = MOMENTUM_BULLISH_LOSING_MOMENTUM
            es = "Alcista perdiendo fuerza"
            en = "Bullish but cooling"
    else:
        # l5 == l15 → effectively neutral.
        state = MOMENTUM_NEUTRAL
        es = "Tendencia neutra"
        en = "Flat trend"

    return {
        "state":       state,
        "label_es":    es,
        "label_en":    en,
        "trend_delta": trend_delta,
        "trend_pct":   trend_pct,
    }


# ─────────────────────────────────────────────────────────────────────
#   Expected corners
# ─────────────────────────────────────────────────────────────────────

def compute_expected_corners(
    *,
    home_for: Optional[float],
    home_against: Optional[float],
    away_for: Optional[float],
    away_against: Optional[float],
) -> Optional[float]:
    """Symmetric Expected Corners projection.

    Formula (per user spec)::

        EC = ((home_for + away_against) / 2 + (away_for + home_against) / 2) / 1

    The two halves average "the home team's offensive output blended
    with the away team's defensive allowance" and the symmetric
    counterpart. We DO NOT divide by 2 a second time — the spec sums
    the two halves.

    Returns ``None`` if any input is missing (no half-blind output).
    """
    if any(x is None for x in (home_for, home_against, away_for, away_against)):
        return None
    home_blend = (home_for + away_against) / 2.0   # type: ignore[operator]
    away_blend = (away_for + home_against) / 2.0   # type: ignore[operator]
    return round(home_blend + away_blend, 2)


# ─────────────────────────────────────────────────────────────────────
#   Line projections
# ─────────────────────────────────────────────────────────────────────

# Bands per spec: edge >= 1.5 favourable, |edge| < 0.75 neutral,
# edge <= -1.0 risky.
def _project_line(expected: Optional[float], line: float) -> str:
    if expected is None:
        return "UNKNOWN"
    edge = expected - line
    if edge >= 1.5:
        return "FAVORABLE"
    if edge <= -1.0:
        return "RISKY"
    return "NEUTRAL"


# ─────────────────────────────────────────────────────────────────────
#   Per-team profile
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TeamCornersProfile:
    """Per-team corners profile derived from the history vector."""
    team_id: Optional[str | int] = None
    team_name: Optional[str] = None
    sample_size: int = 0
    last_match_corners: Optional[int] = None
    l5_avg_corners_for: Optional[float] = None
    l5_avg_corners_against: Optional[float] = None
    l15_avg_corners_for: Optional[float] = None
    l15_avg_corners_against: Optional[float] = None
    l1_corners_for: Optional[int] = None
    l1_corners_against: Optional[int] = None
    momentum: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "team":                     self.team_name,
            "team_id":                  self.team_id,
            "sample_size":              self.sample_size,
            "last_match_corners":       self.last_match_corners,
            "l1_corners_for":           self.l1_corners_for,
            "l1_corners_against":       self.l1_corners_against,
            "l5_avg_corners_for":       self.l5_avg_corners_for,
            "l5_avg_corners_against":   self.l5_avg_corners_against,
            "l15_avg_corners_for":      self.l15_avg_corners_for,
            "l15_avg_corners_against":  self.l15_avg_corners_against,
            "momentum":                 self.momentum,
            "reason_codes":             list(self.reason_codes),
        }
        return d


def build_team_profile(
    *,
    team_id: Optional[str | int],
    team_name: Optional[str],
    history: list[dict] | None,
    min_sample: int = 5,
) -> TeamCornersProfile:
    """Compute a :class:`TeamCornersProfile` from a raw history vector.

    ``history`` is expected newest-first. Items without
    ``corners_for`` or ``corners_against`` are still counted in
    ``sample_size`` only if at least one of the two values is present.
    """
    profile = TeamCornersProfile(team_id=team_id, team_name=team_name)
    # Always populate ``momentum`` with a stable schema so consumers
    # (FE/tests) can read ``momentum.state`` unconditionally.
    profile.momentum = compute_corner_momentum(l1=None, l5=None, l15=None)
    if not isinstance(history, list) or not history:
        profile.reason_codes.append(RC_CORNERS_TEAM_HISTORY_NOT_FOUND)
        return profile

    # Normalise + keep newest-first.
    norm: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        cf = _safe_int_or_none(item.get("corners_for"))
        ca = _safe_int_or_none(item.get("corners_against"))
        if cf is None and ca is None:
            continue
        norm.append({"corners_for": cf, "corners_against": ca,
                     "match_id": item.get("match_id")})
    profile.sample_size = len(norm)
    if not norm:
        profile.reason_codes.append(RC_CORNERS_TEAM_HISTORY_NOT_FOUND)
        return profile

    # L1 — most recent match.
    last = norm[0]
    profile.l1_corners_for = last.get("corners_for")
    profile.l1_corners_against = last.get("corners_against")
    profile.last_match_corners = (
        last.get("corners_for")
        if last.get("corners_for") is not None
        else None
    )

    # L5 / L15 averages.
    profile.l5_avg_corners_for = _safe_avg([x.get("corners_for") for x in norm[:5]])
    profile.l5_avg_corners_against = _safe_avg([x.get("corners_against") for x in norm[:5]])
    profile.l15_avg_corners_for = _safe_avg([x.get("corners_for") for x in norm[:15]])
    profile.l15_avg_corners_against = _safe_avg([x.get("corners_against") for x in norm[:15]])

    # Momentum (drives off corners-for).
    profile.momentum = compute_corner_momentum(
        l1=profile.l1_corners_for,
        l5=profile.l5_avg_corners_for,
        l15=profile.l15_avg_corners_for,
    )

    if profile.sample_size < min_sample:
        profile.reason_codes.append(RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE)
    return profile


# ─────────────────────────────────────────────────────────────────────
#   Full profile builder (both teams + combined)
# ─────────────────────────────────────────────────────────────────────

def build_corners_profile(
    *,
    home_team_id: Optional[str | int],
    home_team_name: Optional[str],
    home_history: list[dict] | None,
    away_team_id: Optional[str | int],
    away_team_name: Optional[str],
    away_history: list[dict] | None,
    is_pre_match: bool = True,
    current_fixture_corners_available: bool = False,
    min_sample: int = 5,
    line_grid: tuple[float, ...] = (8.5, 9.5, 10.5, 11.5),
    provider: str = "thestatsapi",
) -> dict[str, Any]:
    """Build the full ``corners_profile`` payload.

    Returns the canonical contract::

        {
          "status":   "OK" | "PARTIAL" | "UNAVAILABLE",
          "provider": str,
          "is_pre_match": bool,
          "current_fixture_corners_available": bool,
          "home": {...team_profile_dict},
          "away": {...team_profile_dict},
          "combined_l5_avg": float | None,
          "combined_l15_avg": float | None,
          "expected_corners": float | None,
          "line_projections": {"over_8.5": "...", ...},
          "picks_blocked": bool,
          "reason_codes": [...]
        }
    """
    home = build_team_profile(
        team_id=home_team_id, team_name=home_team_name,
        history=home_history, min_sample=min_sample,
    )
    away = build_team_profile(
        team_id=away_team_id, team_name=away_team_name,
        history=away_history, min_sample=min_sample,
    )

    # Combined averages: sum of for-averages of both teams (the "match
    # total" projection through the lens of historical means).
    def _sum_or_none(*xs):
        if any(x is None for x in xs):
            return None
        return round(sum(xs), 2)

    combined_l5 = _sum_or_none(home.l5_avg_corners_for, away.l5_avg_corners_for)
    combined_l15 = _sum_or_none(home.l15_avg_corners_for, away.l15_avg_corners_for)

    expected = compute_expected_corners(
        home_for=home.l5_avg_corners_for,
        home_against=home.l5_avg_corners_against,
        away_for=away.l5_avg_corners_for,
        away_against=away.l5_avg_corners_against,
    )

    line_projections: dict[str, str] = {}
    for line in line_grid:
        key = f"over_{str(line).replace('.', '_')}"
        line_projections[key] = _project_line(expected, line)

    # Status.
    if home.sample_size == 0 or away.sample_size == 0:
        status = STATUS_UNAVAILABLE
    elif home.sample_size < min_sample or away.sample_size < min_sample:
        status = STATUS_PARTIAL
    else:
        status = STATUS_OK

    # Aggregate reason codes.
    rc: list[str] = []
    if status == STATUS_OK:
        rc.append(RC_CORNERS_PROFILE_OK)
    rc.extend(home.reason_codes)
    rc.extend(away.reason_codes)
    if is_pre_match and not current_fixture_corners_available:
        # Surface the "no current-fixture corners — expected" signal
        # so downstream layers stop treating it as a hard error.
        rc.append(RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED)
    # De-dup preserving order.
    seen: set[str] = set()
    rc_dedup: list[str] = []
    for code in rc:
        if code not in seen:
            seen.add(code)
            rc_dedup.append(code)

    # Picks of the corners sub-market are blocked when either side
    # falls short of the minimum sample. The main pick is NOT blocked
    # (per spec — that's a sub-market guard).
    picks_blocked = (
        home.sample_size < min_sample
        or away.sample_size < min_sample
    )

    return {
        "status":   status,
        "provider": provider,
        "is_pre_match": bool(is_pre_match),
        "current_fixture_corners_available": bool(current_fixture_corners_available),
        "home": home.to_dict(),
        "away": away.to_dict(),
        "combined_l5_avg":  combined_l5,
        "combined_l15_avg": combined_l15,
        "expected_corners": expected,
        "line_projections": line_projections,
        "picks_blocked":    picks_blocked,
        "reason_codes":     rc_dedup,
    }


__all__ = [
    # reason codes
    "RC_CORNERS_PROFILE_OK",
    "RC_CORNERS_HISTORY_INSUFFICIENT_SAMPLE",
    "RC_CORNERS_TEAM_HISTORY_NOT_FOUND",
    "RC_CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED",
    # momentum
    "MOMENTUM_BULLISH_STRONG", "MOMENTUM_BULLISH_STABLE",
    "MOMENTUM_BULLISH_LOSING_MOMENTUM", "MOMENTUM_BEARISH", "MOMENTUM_NEUTRAL",
    # statuses
    "STATUS_OK", "STATUS_PARTIAL", "STATUS_UNAVAILABLE",
    # API
    "TeamCornersProfile",
    "compute_corner_momentum",
    "compute_expected_corners",
    "build_team_profile",
    "build_corners_profile",
]
