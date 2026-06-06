"""Box-score Providers — cross-provider fail-over for NBA + MLB raw stats.

Phase 40 / Fix 1.

This package fetches *raw per-team per-game* box-score statistics so the
``basketball_possession_layer`` can replace its historical fallback with
real Four-Factors data (eFG%, TOV%, ORB%, FTr) and so the baseball model
can read per-game AB/H/BB/K/SB plus computed OBP/SLG/ISO/BABIP.

Architecture (matches the integration playbook):

  basketball
    primary  → API-Sports   v1.basketball.api-sports.io/games/statistics
    fallback → Balldontlie  api.balldontlie.io/v1/stats?dates[]=...
  baseball
    primary  → API-Sports   v1.baseball.api-sports.io/games/statistics
    fallback → MLB StatsAPI statsapi.mlb.com/api/v1/game/{gamePk}/feed/live

Public surface (all async, all fail-soft — NEVER raise):
    * ``fetch_basketball_team_games(team_id, *, last_n=...)`` → list[dict]
    * ``fetch_baseball_team_games(team_id, *, last_n=...)``   → list[dict]

Each returned dict matches the schema ``basketball_possession_layer``
consumes (``fga, fgm, three_pa, three_pm, fta, ftm, orb, drb, tov,
minutes, pts_for, pts_against, opp_*``) and the corresponding baseball
schema (``ab, h, r, bb, k, sb, obp, slg, iso, k_rate, bb_rate``).

When BOTH providers fail or both return empty, returns an empty list and
the upstream possession layer falls back to the historical proxy.
"""
from .common import (
    BoxScoreProviderError,
    DEFAULT_TIMEOUT_S,
    PROVIDER_API_SPORTS,
    PROVIDER_BALLDONTLIE,
    PROVIDER_MLB_STATSAPI,
)
from .basketball import (
    fetch_basketball_team_games,
    normalize_api_sports_basketball,
    normalize_balldontlie,
)
from .baseball import (
    fetch_baseball_team_games,
    normalize_api_sports_baseball,
    normalize_mlb_statsapi,
)


async def hydrate_match_with_box_scores(
    match: dict,
    *,
    last_n: int = 10,
) -> dict:
    """Attach pre-fetched per-team box-scores to a match dict.

    Mutates ``match`` in place adding::

        match["_box_score_games"] = {
            "home": [<per-game dict>, ...],
            "away": [<per-game dict>, ...],
            "_provider_summary": {"home": "api_sports"|"balldontlie"|..., ...},
        }

    Fail-soft: if both providers fail for a side, the corresponding key
    is an empty list and the downstream possession layer falls back to
    its historical proxy. NEVER raises.

    Returns the same ``match`` dict for convenient chaining.
    """
    if not isinstance(match, dict):
        return match
    sport = (match.get("sport") or "").lower()
    home_id = ((match.get("home_team") or {}).get("id"))
    away_id = ((match.get("away_team") or {}).get("id"))
    try:
        if sport == "basketball" and home_id and away_id:
            home_games = await fetch_basketball_team_games(home_id, last_n=last_n)
            away_games = await fetch_basketball_team_games(away_id, last_n=last_n)
            match["_box_score_games"] = {
                "home": home_games,
                "away": away_games,
                "_provider_summary": {
                    "home": (home_games[0].get("_provider") if home_games else None),
                    "away": (away_games[0].get("_provider") if away_games else None),
                    "sport": "basketball",
                },
            }
        elif sport == "baseball" and home_id and away_id:
            home_games = await fetch_baseball_team_games(home_id, last_n=last_n)
            away_games = await fetch_baseball_team_games(away_id, last_n=last_n)
            match["_box_score_games"] = {
                "home": home_games,
                "away": away_games,
                "_provider_summary": {
                    "home": (home_games[0].get("_provider") if home_games else None),
                    "away": (away_games[0].get("_provider") if away_games else None),
                    "sport": "baseball",
                },
            }
    except Exception:
        # Strict fail-soft contract.
        pass
    return match


__all__ = [
    "BoxScoreProviderError",
    "DEFAULT_TIMEOUT_S",
    "PROVIDER_API_SPORTS",
    "PROVIDER_BALLDONTLIE",
    "PROVIDER_MLB_STATSAPI",
    "fetch_basketball_team_games",
    "fetch_baseball_team_games",
    "normalize_api_sports_basketball",
    "normalize_balldontlie",
    "normalize_api_sports_baseball",
    "normalize_mlb_statsapi",
    "hydrate_match_with_box_scores",
]
