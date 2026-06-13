"""Phase F85.3 — Public xG normaliser.

Converts raw FBref match-log lists (and optionally Forebet context) into
the canonical ``xg_recent_averages`` shape that the rest of the engine
already consumes (sibling of
:mod:`services.football_xg_recent_averages` which talks to TheStatsAPI).

Deliberately pure (no I/O, no clients) so it can be unit-tested in
isolation. The orchestrator is in
:mod:`services.football_xg_public_ingestor`.

Rules
-----
* Never invent xG — if a log row lacks ``xg_for`` we skip it. If a
  window has zero usable rows, we emit ``None`` for that window (not 0).
* Sample sizes report the REAL count of usable rows.
* ``partial=True`` whenever any side’s ``l5`` or ``l15`` window covers
  fewer than the target window size OR one side has no usable rows at
  all.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_xg_public_normalizer")

RC_AVAILABLE             = "FBREF_XG_RECENT_AVERAGES_AVAILABLE"
RC_L5_AVAILABLE          = "FBREF_XG_L5_AVAILABLE"
RC_L15_AVAILABLE         = "FBREF_XG_L15_AVAILABLE"
RC_L5_MISSING            = "FBREF_XG_L5_MISSING"
RC_L15_MISSING           = "FBREF_XG_L15_MISSING"
RC_NOT_AVAILABLE         = "FBREF_XG_NOT_AVAILABLE"
RC_PARTIAL               = "FBREF_XG_PARTIAL_SAMPLE"

_L1, _L5, _L15 = 1, 5, 15


def _avg(values: list[float | None]) -> Optional[float]:
    cleaned = [v for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned), 3)


def _build_window(logs: list[dict], n: int) -> Optional[dict]:
    """Aggregate the most-recent ``n`` log rows (logs are already
    newest-first). Returns ``None`` when no usable xG row exists in the
    window."""
    if not logs:
        return None
    # Defensive: only dict rows are usable; strings / ints / etc. get
    # filtered out so a garbage-shaped argument can never raise.
    slice_ = [r for r in logs[:n] if isinstance(r, dict)]
    if not slice_:
        return None
    xg_for     = [r.get("xg_for")        for r in slice_ if r.get("xg_for")        is not None]
    xg_against = [r.get("xg_against")    for r in slice_ if r.get("xg_against")    is not None]
    npxg_for     = [r.get("npxg_for")     for r in slice_ if r.get("npxg_for")     is not None]
    npxg_against = [r.get("npxg_against") for r in slice_ if r.get("npxg_against") is not None]
    if not xg_for and not xg_against:
        return None
    out: dict[str, Any] = {
        "xg_for_avg":     _avg(xg_for),
        "xg_against_avg": _avg(xg_against),
        "sample_size":    len([r for r in slice_ if r.get("xg_for") is not None
                                                   or r.get("xg_against") is not None]),
    }
    if npxg_for:
        out["npxg_for_avg"] = _avg(npxg_for)
    if npxg_against:
        out["npxg_against_avg"] = _avg(npxg_against)
    return out


def _side_payload(team_name: Optional[str], logs: list[dict]) -> dict:
    return {
        "team": team_name,
        "l1":  _build_window(logs, _L1),
        "l5":  _build_window(logs, _L5),
        "l15": _build_window(logs, _L15),
    }


def _combined(home: dict, away: dict, window_key: str, field: str) -> Optional[float]:
    h = (home.get(window_key) or {}).get(field)
    a = (away.get(window_key) or {}).get(field)
    if h is None or a is None:
        return None
    return round(h + a, 3)


def compute_fbref_xg_recent_averages(
    home_logs: list[dict],
    away_logs: list[dict],
    *,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
) -> dict:
    """Return the canonical ``xg_recent_averages`` snapshot from FBref logs.

    Contract:
      * ``available=False`` when BOTH sides lack usable xG.
      * ``partial=True`` when any side’s ``l5`` / ``l15`` is missing OR
        when a window’s sample size is below the target.
      * Never invents data; never raises.
    """
    home_logs = [r for r in (home_logs or []) if isinstance(r, dict)]
    away_logs = [r for r in (away_logs or []) if isinstance(r, dict)]

    home_block = _side_payload(home_team_name, home_logs)
    away_block = _side_payload(away_team_name, away_logs)

    home_has_any = any(home_block.get(k) for k in ("l1", "l5", "l15"))
    away_has_any = any(away_block.get(k) for k in ("l1", "l5", "l15"))

    if not home_has_any and not away_has_any:
        return {
            "available":    False,
            "source":       "fbref",
            "home":         home_block,
            "away":         away_block,
            "reason_codes": [RC_NOT_AVAILABLE],
        }

    def _both_have(key: str) -> bool:
        return bool(home_block.get(key)) and bool(away_block.get(key))

    full_l5  = _both_have("l5")
    full_l15 = _both_have("l15")

    # Sample size proxy for partial detection: a window with sample
    # smaller than the target window length counts as partial.
    def _under_target(side: dict, key: str, n: int) -> bool:
        w = side.get(key)
        if not isinstance(w, dict):
            return True
        size = w.get("sample_size")
        return size is None or size < n

    partial = (
        not (full_l5 and full_l15)
        or _under_target(home_block, "l5",  _L5)
        or _under_target(away_block, "l5",  _L5)
        or _under_target(home_block, "l15", _L15)
        or _under_target(away_block, "l15", _L15)
    )

    derived = {
        "combined_l5_xg_for":   _combined(home_block, away_block, "l5",  "xg_for_avg"),
        "combined_l15_xg_for":  _combined(home_block, away_block, "l15", "xg_for_avg"),
        "combined_l5_xga":      _combined(home_block, away_block, "l5",  "xg_against_avg"),
        "combined_l15_xga":     _combined(home_block, away_block, "l15", "xg_against_avg"),
    }

    reason_codes = [RC_AVAILABLE]
    if full_l5:
        reason_codes.append(RC_L5_AVAILABLE)
    else:
        reason_codes.append(RC_L5_MISSING)
    if full_l15:
        reason_codes.append(RC_L15_AVAILABLE)
    else:
        reason_codes.append(RC_L15_MISSING)
    if partial:
        reason_codes.append(RC_PARTIAL)

    return {
        "available":    True,
        "source":       "fbref",
        "partial":      partial,
        "home":         home_block,
        "away":         away_block,
        "derived":      derived,
        "reason_codes": reason_codes,
    }


__all__ = [
    "compute_fbref_xg_recent_averages",
    "RC_AVAILABLE", "RC_L5_AVAILABLE", "RC_L15_AVAILABLE",
    "RC_L5_MISSING", "RC_L15_MISSING", "RC_NOT_AVAILABLE", "RC_PARTIAL",
]
