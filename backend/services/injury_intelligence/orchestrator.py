"""Public orchestrator for Injury Intelligence (Phase 1 — Basketball).

Single entry point:

    fetch_basketball_injury_intelligence(
        home_team: dict, away_team: dict, *,
        db=None, force_refresh=False, is_game_day=False,
        player_stats: dict|None = None,
    ) -> dict

Returns the canonical ``injury_intelligence`` payload (see injury_schema).

Behaviour:
  * Tries Mongo cache (TTL: 2h pregame / 30m game-day) unless force_refresh.
  * Fetches per-team injuries in parallel from every enabled source.
  * Merges + normalises records (conservative status on conflicts).
  * Scores per-team and computes match-level edge.
  * Computes ``match_impact``: conservative confidence/fragility adjustments
    + market warnings + reason_codes + ES summary.
  * Stamps source_status and freshness.
  * Fail-soft: any source failure is recorded in source_status; an empty
    fetch returns ``available:false``.

The orchestrator does NOT mutate the engine pick directly — that's the
job of the basketball pipeline which reads the returned payload.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Optional

from .injury_schema import (
    INJURY_SCHEMA_VERSION,
    empty_payload,
    empty_team_block,
)
from .injury_normalizer import (
    merge_player_records,
    compute_freshness,
)
from .basketball_injury_impact import (
    calculate_basketball_injury_impact,
)
from .injury_sources import (
    fetch_api_sports_basketball_injuries,
    fetch_thestatsapi_basketball_injuries,
    fetch_bright_data_basketball_injuries,
)
from .injury_cache import cache_get, cache_set

log = logging.getLogger("injury_intelligence.orchestrator")

# Hard caps from spec — Injury Intelligence is a risk layer, not a motor.
MAX_CONFIDENCE_ADJUSTMENT = 12
MAX_FRAGILITY_ADJUSTMENT  = 15


def _cache_key(home: dict, away: dict) -> str:
    h = (home or {}).get("id") or (home or {}).get("name") or ""
    a = (away or {}).get("id") or (away or {}).get("name") or ""
    raw = f"basketball::{h}::{a}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _team_slug(team: dict) -> str:
    """ESPN/Rotowire URL slug from a team name (very rough — operators tune)."""
    name = (team or {}).get("slug") or (team or {}).get("name") or ""
    return str(name).lower().replace(" ", "-")


async def _fetch_team_injuries(team: dict) -> tuple[list[dict], dict]:
    """Fetch and merge injuries for ONE team across all enabled sources."""
    team_id = (team or {}).get("id")
    season  = (team or {}).get("season") or "2024-2025"
    slug    = _team_slug(team)

    tasks = []
    labels: list[str] = []

    # API-Sports primary.
    if team_id:
        tasks.append(fetch_api_sports_basketball_injuries(team_id=team_id, season=season))
        labels.append("api_sports")
    # TheStatsAPI (optional).
    tasks.append(fetch_thestatsapi_basketball_injuries(team_id=team_id or 0))
    labels.append("thestatsapi")
    # ESPN scrape (optional).
    if slug:
        tasks.append(fetch_bright_data_basketball_injuries(team_slug=slug, source="espn"))
        labels.append("espn")
        tasks.append(fetch_bright_data_basketball_injuries(team_slug=slug, source="rotowire"))
        labels.append("rotowire")

    if not tasks:
        return [], {
            "api_sports":        "skipped",
            "thestatsapi":       "skipped",
            "espn":              "skipped",
            "rotowire":          "skipped",
            "official":          "skipped",
            "editorial_context": "skipped",
        }

    results = await asyncio.gather(*tasks, return_exceptions=True)
    raw_records: list[dict] = []
    source_status: dict[str, str] = {
        "api_sports":        "skipped",
        "thestatsapi":       "skipped",
        "espn":              "skipped",
        "rotowire":          "skipped",
        "official":          "skipped",
        "editorial_context": "skipped",
    }
    for label, r in zip(labels, results):
        if isinstance(r, Exception):
            source_status[label] = "failed"
            continue
        if not isinstance(r, dict):
            source_status[label] = "failed"
            continue
        source_status[label] = r.get("status") or "partial"
        for rec in r.get("records") or []:
            if isinstance(rec, dict):
                raw_records.append(rec)

    merged = merge_player_records(raw_records)
    return merged, source_status


def _compose_team_block(team: dict, injuries: list[dict],
                          player_stats: Optional[dict]) -> dict:
    impact = calculate_basketball_injury_impact(
        team_profile=team or {},
        injuries=injuries,
        player_stats=player_stats,
    )
    block = empty_team_block()
    block["team_name"]               = (team or {}).get("name")
    block["team_id"]                 = (team or {}).get("id")
    block["injuries"]                = injuries
    block["team_injury_impact"]      = impact["team_injury_impact"]
    block["basketball_injury_score"] = impact["basketball_injury_score"]
    return block


def _compute_match_edge(home_block: dict, away_block: dict) -> dict:
    h_adj = home_block["basketball_injury_score"]["team_strength_adjustment"]
    a_adj = away_block["basketball_injury_score"]["team_strength_adjustment"]
    # Edge favours the team that is LESS affected (less negative).
    net_points = h_adj - a_adj          # positive → home is less affected
    edge_side  = "neutral"
    if   net_points > 1:  edge_side = "home"
    elif net_points < -1: edge_side = "away"
    abs_edge = abs(net_points)
    if   abs_edge >= 8: tier = "STRONG"
    elif abs_edge >= 4: tier = "MODERATE"
    else:               tier = "SMALL"

    h_tier = home_block["team_injury_impact"]["impact_tier"]
    a_tier = away_block["team_injury_impact"]["impact_tier"]
    high_volatility = h_tier in ("HIGH", "CRITICAL") and a_tier in ("HIGH", "CRITICAL")

    summary = _build_match_edge_summary(home_block, away_block, edge_side, abs_edge,
                                           high_volatility)

    return {
        "home_total_adjustment": h_adj,
        "away_total_adjustment": a_adj,
        "net_edge":              edge_side,
        "net_edge_points":       abs_edge,
        "edge_tier":             tier,
        "high_volatility":       high_volatility,
        "summary":               summary,
    }


def _build_match_edge_summary(home: dict, away: dict, edge_side: str,
                                points: int, high_vol: bool) -> str:
    home_name = home.get("team_name") or "local"
    away_name = away.get("team_name") or "visitante"
    if high_vol:
        return (f"Ambos equipos llegan muy golpeados por lesiones; "
                f"alta volatilidad, evitar mercados agresivos.")
    if edge_side == "home":
        return (f"Las bajas afectan más al visitante ({away_name}); "
                f"el local ({home_name}) gana ventaja relativa de "
                f"+{points} puntos por disponibilidad.")
    if edge_side == "away":
        return (f"Las bajas afectan más al local ({home_name}); "
                f"el visitante ({away_name}) gana ventaja relativa de "
                f"+{points} puntos por disponibilidad.")
    return "Impacto de lesiones similar entre ambos equipos."


def _compute_match_impact(home_block: dict, away_block: dict,
                            match_edge: dict, freshness: str) -> dict:
    # Freshness weight.
    weight = {
        "fresh":   0.70,
        "partial": 0.40,
        "stale":   0.20,
        "unknown": 0.00,
    }.get(freshness, 0.0)

    # Confidence adjustment uses NET edge magnitude, capped.
    raw_conf_adj = match_edge["net_edge_points"] * 0.6 * weight
    conf_adj = int(round(min(MAX_CONFIDENCE_ADJUSTMENT, raw_conf_adj)))
    # Sign: positive when there's a real edge (the pipeline applies sign
    # based on whether the pick aligns with the edge side; we surface the
    # magnitude only here).

    # Fragility goes up with EITHER team's tier severity.
    h_frag = home_block["basketball_injury_score"]["fragility_adjustment"]
    a_frag = away_block["basketball_injury_score"]["fragility_adjustment"]
    raw_frag = max(h_frag, a_frag)
    frag_adj = int(round(min(MAX_FRAGILITY_ADJUSTMENT, raw_frag * weight)))

    # Market warnings & reason_codes.
    warnings: list[str] = []
    reason_codes: list[str] = []
    for side, block in (("home", home_block), ("away", away_block)):
        tier = block["team_injury_impact"]["impact_tier"]
        rcs  = block["basketball_injury_score"]["reason_codes"]
        if tier in ("HIGH", "CRITICAL"):
            warnings.append(f"AGGRESSIVE_PICKS_BLOCKED_{side.upper()}")
            reason_codes.append(f"INJURY_TIER_{tier}_{side.upper()}")
        if "STARTING_POINT_GUARD_OUT" in rcs:
            warnings.append(f"OFFENSIVE_CREATION_REDUCED_{side.upper()}")
        if "RIM_PROTECTOR_OUT" in rcs or "DEFENSIVE_ANCHOR_OUT" in rcs:
            warnings.append(f"OPPONENT_SCORING_RISK_UP_{('AWAY' if side == 'home' else 'HOME')}")
        if "MINUTES_RESTRICTION_KEY_PLAYER" in rcs:
            warnings.append(f"MINUTES_RESTRICTION_{side.upper()}")
        if "QUESTIONABLE_STAR_RISK" in rcs:
            warnings.append(f"QUESTIONABLE_STAR_{side.upper()}")
            reason_codes.append(f"WATCHLIST_QUESTIONABLE_{side.upper()}")
    if match_edge["high_volatility"]:
        warnings.append("HIGH_INJURY_VOLATILITY_BOTH_SIDES")
        reason_codes.append("HIGH_INJURY_VOLATILITY")
    if freshness == "stale":
        warnings.append("INJURY_DATA_STALE_NO_BIG_BOOST")
        reason_codes.append("DATA_STALE_WARNING")

    return {
        "injury_edge":            match_edge["net_edge"],
        "confidence_adjustment":  conf_adj,
        "fragility_adjustment":   frag_adj,
        "market_warnings":        sorted(set(warnings)),
        "reason_codes":           sorted(set(reason_codes)),
        "summary":                match_edge["summary"],
    }


async def fetch_basketball_injury_intelligence(
    home_team: dict,
    away_team: dict,
    *,
    db: Any = None,
    force_refresh: bool = False,
    is_game_day: bool = False,
    player_stats: Optional[dict] = None,
) -> dict:
    """Public entry point — see module docstring for the contract."""
    if not isinstance(home_team, dict) or not isinstance(away_team, dict):
        return empty_payload(reason="invalid_input")

    cache_key = _cache_key(home_team, away_team)
    if not force_refresh:
        cached = await cache_get(db, cache_key=cache_key, sport="basketball",
                                    is_game_day=is_game_day)
        if cached:
            return cached

    # Parallel fetch per team.
    try:
        (home_injuries, home_status), (away_injuries, away_status) = await asyncio.gather(
            _fetch_team_injuries(home_team),
            _fetch_team_injuries(away_team),
        )
    except Exception as exc:
        log.debug("injury_intelligence fetch error (fail-soft): %s", exc)
        return empty_payload(reason="fetch_error")

    all_injuries = home_injuries + away_injuries
    if not all_injuries:
        payload = empty_payload(reason="no_injuries_reported")
        # Merge per-source status so the UI knows what we tried.
        for k in set(home_status.keys()) | set(away_status.keys()):
            payload["source_status"][k] = _merge_status(
                home_status.get(k, "skipped"),
                away_status.get(k, "skipped"),
            )
        payload["home"]["team_name"] = home_team.get("name")
        payload["home"]["team_id"]   = home_team.get("id")
        payload["away"]["team_name"] = away_team.get("name")
        payload["away"]["team_id"]   = away_team.get("id")
        await cache_set(db, cache_key=cache_key, payload=payload)
        return payload

    home_block = _compose_team_block(home_team, home_injuries, player_stats)
    away_block = _compose_team_block(away_team, away_injuries, player_stats)
    match_edge = _compute_match_edge(home_block, away_block)
    freshness  = compute_freshness(all_injuries, sport="basketball",
                                      is_game_day=is_game_day)
    match_impact = _compute_match_impact(home_block, away_block, match_edge, freshness)

    payload = {
        "available":         True,
        "sport":             "basketball",
        "schema_version":    INJURY_SCHEMA_VERSION,
        "home":              home_block,
        "away":              away_block,
        "match_injury_edge": match_edge,
        "match_impact":      match_impact,
        "source_status":     {
            k: _merge_status(home_status.get(k, "skipped"),
                              away_status.get(k, "skipped"))
            for k in set(home_status.keys()) | set(away_status.keys())
        },
        "freshness":         freshness,
    }
    await cache_set(db, cache_key=cache_key, payload=payload)
    return payload


def _merge_status(h: str, a: str) -> str:
    rank = {"success": 3, "partial": 2, "failed": 1, "skipped": 0}
    return h if rank.get(h, 0) >= rank.get(a, 0) else a


__all__ = ["fetch_basketball_injury_intelligence"]
