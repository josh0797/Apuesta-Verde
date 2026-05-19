"""Normalize API-Football raw responses into our 3-layer schema:
  - odds_snapshots (Layer 1)
  - team_context   (Layer 2)
  - live_stats     (Layer 3)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LIVE_STATUSES = {"1H", "2H", "HT", "ET", "P", "LIVE", "BT"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_odds(odds_response: list[dict]) -> dict:
    """Convert API-Football odds (list response) -> single snapshot dict."""
    if not odds_response:
        return {"available": False, "snapshot_at": now_iso(), "bookmakers": [], "markets": {}}
    item = odds_response[0]
    bookmakers_data = item.get("bookmakers", []) or []
    markets: dict[str, list[dict]] = {"1X2": [], "Over/Under": [], "BTTS": [], "Asian Handicap": [], "Double Chance": []}
    bm_names = []
    for bm in bookmakers_data:
        bm_name = bm.get("name", "Unknown")
        bm_names.append(bm_name)
        for bet in bm.get("bets", []) or []:
            bname = (bet.get("name") or "").strip()
            values = bet.get("values", []) or []
            if bname == "Match Winner":
                row = {"bookmaker": bm_name}
                for v in values:
                    val = v.get("value")
                    try:
                        odd = float(v.get("odd", 0))
                    except Exception:
                        odd = 0.0
                    if val == "Home":
                        row["home"] = odd
                    elif val == "Draw":
                        row["draw"] = odd
                    elif val == "Away":
                        row["away"] = odd
                markets["1X2"].append(row)
            elif bname in ("Goals Over/Under", "Over/Under"):
                row = {"bookmaker": bm_name, "lines": {}}
                for v in values:
                    try:
                        row["lines"][v.get("value")] = float(v.get("odd", 0))
                    except Exception:
                        pass
                markets["Over/Under"].append(row)
            elif bname in ("Both Teams Score", "Both Teams To Score"):
                row = {"bookmaker": bm_name}
                for v in values:
                    try:
                        row[(v.get("value") or "").lower()] = float(v.get("odd", 0))
                    except Exception:
                        pass
                markets["BTTS"].append(row)
            elif bname == "Asian Handicap":
                row = {"bookmaker": bm_name, "lines": []}
                for v in values:
                    try:
                        row["lines"].append({"value": v.get("value"), "odd": float(v.get("odd", 0))})
                    except Exception:
                        pass
                markets["Asian Handicap"].append(row)
            elif bname == "Double Chance":
                row = {"bookmaker": bm_name}
                for v in values:
                    try:
                        row[(v.get("value") or "")] = float(v.get("odd", 0))
                    except Exception:
                        pass
                markets["Double Chance"].append(row)
    return {
        "available": True,
        "snapshot_at": now_iso(),
        "bookmakers": bm_names,
        "markets": markets,
    }


def _team_in_standings(standings_resp: list[dict], team_id: int) -> dict:
    info = {"position": None, "points": None, "played": None, "goalsDiff": None, "description": None}
    try:
        if standings_resp:
            league = standings_resp[0].get("league", {})
            for group in league.get("standings", []) or []:
                for row in group:
                    if (row.get("team") or {}).get("id") == team_id:
                        info["position"] = row.get("rank")
                        info["points"] = row.get("points")
                        info["played"] = (row.get("all") or {}).get("played")
                        info["goalsDiff"] = row.get("goalsDiff")
                        info["description"] = row.get("description")
                        return info
    except Exception:
        pass
    return info


def normalize_team_context(stats: dict, standings_resp: list[dict], injuries_list: list[dict], team_id: int) -> dict:
    ctx: dict[str, Any] = {
        "fetched_at": now_iso(),
        "data_source_season": "2024 (proxy)",  # because free plan limit
        "form_last_5": "",
        "goals_for_avg": None,
        "goals_against_avg": None,
        "injuries_count": 0,
        "suspensions_count": 0,
        "position": None,
        "points": None,
        "league_stage": "regular",
        "description": None,
        "motivation_flags": {
            "already_champion": False,
            "relegated": False,
            "nothing_to_play_for": False,
            "in_relegation_zone": False,
            "in_european_zone": False,
        },
    }
    if stats:
        form_str = stats.get("form") or ""
        ctx["form_last_5"] = form_str[-5:] if form_str else ""
        goals = stats.get("goals") or {}
        gf_avg = (goals.get("for") or {}).get("average", {}).get("total")
        ga_avg = (goals.get("against") or {}).get("average", {}).get("total")
        try:
            ctx["goals_for_avg"] = float(gf_avg) if gf_avg else None
        except Exception:
            pass
        try:
            ctx["goals_against_avg"] = float(ga_avg) if ga_avg else None
        except Exception:
            pass
    info = _team_in_standings(standings_resp, team_id)
    ctx["position"] = info["position"]
    ctx["points"] = info["points"]
    ctx["description"] = info["description"]
    desc = (info.get("description") or "").lower()
    if "relegation" in desc:
        ctx["motivation_flags"]["in_relegation_zone"] = True
    if "champions" in desc or "europa" in desc:
        ctx["motivation_flags"]["in_european_zone"] = True
    # Injuries (consider only active/listed)
    ctx["injuries_count"] = len(injuries_list or [])
    return ctx


def normalize_live_stats(fixture: dict) -> dict | None:
    fx = fixture.get("fixture", {})
    status = fx.get("status", {})
    short = status.get("short")
    if short not in LIVE_STATUSES:
        return None
    goals = fixture.get("goals", {}) or {}
    statistics = fixture.get("statistics", []) or []
    home_stats: dict[str, Any] = {}
    away_stats: dict[str, Any] = {}
    home_id = ((fixture.get("teams") or {}).get("home") or {}).get("id")
    for side in statistics:
        team_id = (side.get("team") or {}).get("id")
        bucket = {}
        for s in side.get("statistics", []) or []:
            bucket[s.get("type")] = s.get("value")
        if team_id == home_id:
            home_stats = bucket
        else:
            away_stats = bucket
    return {
        "minute": status.get("elapsed"),
        "status": short,
        "score": {"home": goals.get("home"), "away": goals.get("away")},
        "home_stats": home_stats,
        "away_stats": away_stats,
        "fetched_at": now_iso(),
    }


def summarize_match_for_llm(match_doc: dict) -> dict:
    """Strip a normalized match doc to a compact payload for the LLM."""
    return {
        "match_id": match_doc.get("match_id"),
        "league": match_doc.get("league"),
        "league_id": match_doc.get("league_id"),
        "season": match_doc.get("season"),
        "kickoff_iso": match_doc.get("kickoff_iso"),
        "is_live": match_doc.get("is_live"),
        "venue": match_doc.get("venue"),
        "home_team": match_doc.get("home_team"),
        "away_team": match_doc.get("away_team"),
        "odds_snapshots": match_doc.get("odds_snapshots", [])[-2:],
        "live_stats": match_doc.get("live_stats"),
        "h2h_recent": match_doc.get("h2h_recent", []),
    }
