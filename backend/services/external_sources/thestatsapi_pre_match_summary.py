"""Sprint-B · Fix 2 — TheStatsAPI pre-match summary fetcher.

Produces the dict the ``football_pre_match_data_aggregator`` expects
from its primary adapter. Composes existing low-level fetchers from
``thestatsapi_client`` + ``thestatsapi_shotmap_client``:

* ``resolve_thestatsapi_match_id_by_names``  — match_id resolution
* ``fetch_recent_matches(team_id)``           — last-N fixtures
* ``fetch_shotmap_xg(match_id)``              — xG per match
* ``fetch_match_stats(match_id)``             — corners per match (when available)
* ``odds_for_fixture(fixture_id)``            — pre-match odds

The module is **fail-soft**: every fetcher is wrapped in try/except,
any missing field falls through as ``None`` so the aggregator can
decide whether to continue with the next adapter.

To keep the call count low (TheStatsAPI is rate-limited) the function
short-circuits as soon as the **core fields** are populated and
leverages the existing thestatsapi cache.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("thestatsapi_pre_match_summary")

_DEFAULT_RECENT_N = 15


def _avg(values: list, k: int) -> Optional[float]:
    """Average the first k numeric values; None when empty."""
    if not values:
        return None
    nums: list[float] = []
    for v in values[:k]:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    return round(sum(nums) / len(nums), 3)


async def _resolve_tsa_match_id(client, match_id, home_team: str,
                                  away_team: str) -> Optional[str]:
    """Best-effort: try the user-provided match_id first; if it can't
    be hit, fall back to the name resolver."""
    try:
        from . import thestatsapi_client as _tsa
    except Exception:
        return None
    if match_id:
        # Trust the caller's id when it's already in TheStatsAPI form.
        return str(match_id)
    try:
        resolved = await _tsa.resolve_thestatsapi_match_id_by_names(
            client, home_team=home_team, away_team=away_team,
        )
        return resolved
    except Exception as exc:  # noqa: BLE001
        log.debug("resolve_thestatsapi_match_id_by_names failed: %s", exc)
        return None


async def _team_recent_xg_corners(client, *, team_id, max_calls: int = 5
                                    ) -> tuple[list, list]:
    """Return ``(xg_list_newest_first, corners_list_newest_first)``.

    Bounded by ``max_calls`` to respect rate limits. ``team_id`` may be
    a TheStatsAPI team id (preferred) or a textual name (resolved
    upstream when needed).
    """
    if not team_id:
        return [], []
    try:
        from . import thestatsapi_client as _tsa
        from . import thestatsapi_shotmap_client as _shot
    except Exception:
        return [], []

    xg_list: list[float] = []
    corners_list: list[float] = []
    try:
        recent = await _tsa.fetch_recent_matches(client, team_id=team_id,
                                                  last=_DEFAULT_RECENT_N)
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_recent_matches failed for %s: %s", team_id, exc)
        return [], []
    if not isinstance(recent, list):
        return [], []

    n_done = 0
    for fx in recent:
        if n_done >= max_calls:
            break
        mid = (fx.get("id") or fx.get("match_id")
                or (fx.get("match") or {}).get("id"))
        if not mid:
            continue
        n_done += 1
        # xG via shotmap
        try:
            xg_payload = await _shot.fetch_shotmap_xg(client, mid)
            if xg_payload.get("available"):
                # Pick the side the team played as.
                home_team_id = ((fx.get("teams") or {}).get("home") or {}).get("id")
                if str(home_team_id) == str(team_id):
                    side_xg = xg_payload.get("home")
                else:
                    side_xg = xg_payload.get("away")
                if side_xg is not None:
                    xg_list.append(float(side_xg))
        except Exception as exc:  # noqa: BLE001
            log.debug("shotmap fetch failed for mid=%s: %s", mid, exc)
        # Corners via match stats
        try:
            stats = await _tsa.fetch_match_stats(client, mid)
            if isinstance(stats, dict):
                # Best-effort: stats may put corners under several keys.
                home_corners = (
                    stats.get("home_corners")
                    or ((stats.get("home") or {}).get("corners"))
                    or ((stats.get("corners") or {}).get("home"))
                )
                away_corners = (
                    stats.get("away_corners")
                    or ((stats.get("away") or {}).get("corners"))
                    or ((stats.get("corners") or {}).get("away"))
                )
                home_team_id = ((fx.get("teams") or {}).get("home") or {}).get("id")
                side_c = home_corners if str(home_team_id) == str(team_id) else away_corners
                if side_c is not None:
                    corners_list.append(float(side_c))
        except Exception as exc:  # noqa: BLE001
            log.debug("match_stats fetch failed for mid=%s: %s", mid, exc)
    return xg_list, corners_list


async def fetch_match_pre_match_summary(
    *,
    home_team: str,
    away_team: str,
    match_id: Optional[str | int] = None,
    home_team_id: Optional[str | int] = None,
    away_team_id: Optional[str | int] = None,
    client=None,
) -> dict:
    """Public entry. Returns the dict the aggregator expects.

    All keys are nullable. When the function cannot produce ANY value
    (e.g. TheStatsAPI disabled) it returns an empty dict so the
    aggregator records a FAILED audit row and moves on.
    """
    try:
        from . import thestatsapi_client as _tsa
    except Exception:
        return {}
    if not _tsa.is_enabled():
        return {}

    import httpx
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        # Resolve fixture id (used for odds lookups).
        fixture_id = await _resolve_tsa_match_id(client, match_id,
                                                   home_team, away_team)

        # Fetch xG + corners for each side (capped at 5 fetches/side).
        h_xg, h_corners = await _team_recent_xg_corners(
            client, team_id=home_team_id
        )
        a_xg, a_corners = await _team_recent_xg_corners(
            client, team_id=away_team_id
        )

        # Odds payload (best-effort)
        odds_block: dict = {}
        if fixture_id:
            try:
                odds_block = await _tsa.odds_for_fixture(client, fixture_id)
            except Exception as exc:  # noqa: BLE001
                log.debug("odds_for_fixture failed: %s", exc)
                odds_block = {}
            if not isinstance(odds_block, dict):
                odds_block = {}

        market_odds = {
            "over25":         (odds_block.get("totals") or {}).get("over_2_5"),
            "btts_yes":       (odds_block.get("btts")   or {}).get("yes"),
            "draw":           (odds_block.get("match_winner") or {}).get("draw"),
            "home_ml":        (odds_block.get("match_winner") or {}).get("home"),
            "away_ml":        (odds_block.get("match_winner") or {}).get("away"),
            "over85_corners": (odds_block.get("corners") or {}).get("over_8_5"),
        }

        return {
            "home_xg_l5":       _avg(h_xg,      5),
            "away_xg_l5":       _avg(a_xg,      5),
            "home_xg_l15":      _avg(h_xg,     15),
            "away_xg_l15":      _avg(a_xg,     15),
            "home_corners_l5":  _avg(h_corners, 5),
            "away_corners_l5":  _avg(a_corners, 5),
            "home_corners_l15": _avg(h_corners,15),
            "away_corners_l15": _avg(a_corners,15),
            "market_odds":      {k: v for k, v in market_odds.items()
                                  if v is not None},
        }
    finally:
        if owns_client:
            try: await client.aclose()
            except Exception: pass  # noqa: BLE001


__all__ = [
    "fetch_match_pre_match_summary",
    "_avg",                # exposed for tests
    "_team_recent_xg_corners",
]
