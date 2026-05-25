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

    # ── Season-level Under priors (used by statsbomb_features) ──────────
    # We pull a few additional counts straight from API-Sports'
    # /teams/statistics response so the Poisson model + Under scan can
    # work even when /fixtures?last=N hasn't been fetched yet.
    if stats:
        fixtures = (stats.get("fixtures") or {})
        played_total = ((fixtures.get("played") or {}).get("total")) or 0
        wins_total = ((fixtures.get("wins") or {}).get("total")) or 0
        draws_total = ((fixtures.get("draws") or {}).get("total")) or 0
        loses_total = ((fixtures.get("loses") or {}).get("total")) or 0
        clean_sheet_total = ((stats.get("clean_sheet") or {}).get("total")) or 0
        failed_total = ((stats.get("failed_to_score") or {}).get("total")) or 0
        try:
            ctx["season_priors"] = {
                "played":           int(played_total),
                "wins":              int(wins_total),
                "draws":             int(draws_total),
                "loses":             int(loses_total),
                "clean_sheet":      int(clean_sheet_total),
                "failed_to_score":  int(failed_total),
                "clean_sheet_rate":     round(int(clean_sheet_total) / int(played_total), 3) if played_total else None,
                "failed_to_score_rate": round(int(failed_total) / int(played_total), 3) if played_total else None,
            }
        except Exception:
            ctx["season_priors"] = None
    return ctx


def normalize_recent_fixtures(fixtures: list[dict], team_id: int, *, n: int = 10) -> dict:
    """Compress a team's last-N fixtures into a compact goal-distribution
    payload for the Poisson model in `statsbomb_features.py`.

    Args:
        fixtures: raw /fixtures?team={id}&last={n} response items.
        team_id:  the team we're profiling. Needed to know whether they
                  played home or away in each fixture.
        n:        cap on how many fixtures to consider.

    Returns:
        {
          "played":    int,       # number of finished fixtures we used
          "totals":    [int, ...] # total goals per match (newest first)
          "gf":        [int, ...] # goals FOR (this team)
          "ga":        [int, ...] # goals AGAINST (this team)
          "venues":    [str, ...] # 'home' | 'away' per match
          "clean_sheets":    int,
          "failed_to_score": int,
          "btts":            int,         # both teams scored count
          "under_3_5_count": int,
          "under_2_5_count": int,
          "gf_avg":     float | None,
          "ga_avg":     float | None,
          "gf_avg_home":  float | None,
          "gf_avg_away":  float | None,
          "ga_avg_home":  float | None,
          "ga_avg_away":  float | None,
          "total_avg":  float | None,
          "total_std":  float | None,
        }
    """
    out: dict[str, Any] = {
        "played": 0,
        "totals": [],
        "gf": [],
        "ga": [],
        "venues": [],
        "clean_sheets": 0,
        "failed_to_score": 0,
        "btts": 0,
        "under_3_5_count": 0,
        "under_2_5_count": 0,
        "gf_avg": None,
        "ga_avg": None,
        "gf_avg_home": None,
        "gf_avg_away": None,
        "ga_avg_home": None,
        "ga_avg_away": None,
        "total_avg": None,
        "total_std": None,
    }
    if not fixtures:
        return out

    gf_home, gf_away, ga_home, ga_away = [], [], [], []
    for f in fixtures[:n]:
        try:
            status = ((f.get("fixture") or {}).get("status") or {}).get("short")
            if status not in ("FT", "AET", "PEN"):
                continue  # only count completed matches
            teams = f.get("teams") or {}
            goals = f.get("goals") or {}
            hg = goals.get("home")
            ag = goals.get("away")
            if hg is None or ag is None:
                continue
            hg, ag = int(hg), int(ag)
            home_id = (teams.get("home") or {}).get("id")
            is_home = (home_id == team_id)
            gf = hg if is_home else ag
            ga = ag if is_home else hg
            total = hg + ag
            out["gf"].append(gf)
            out["ga"].append(ga)
            out["totals"].append(total)
            out["venues"].append("home" if is_home else "away")
            if ga == 0:
                out["clean_sheets"] += 1
            if gf == 0:
                out["failed_to_score"] += 1
            if hg > 0 and ag > 0:
                out["btts"] += 1
            if total < 3.5:
                out["under_3_5_count"] += 1
            if total < 2.5:
                out["under_2_5_count"] += 1
            if is_home:
                gf_home.append(gf)
                ga_home.append(ga)
            else:
                gf_away.append(gf)
                ga_away.append(ga)
        except Exception:
            continue

    n_eff = len(out["totals"])
    out["played"] = n_eff
    if n_eff:
        out["gf_avg"] = round(sum(out["gf"]) / n_eff, 3)
        out["ga_avg"] = round(sum(out["ga"]) / n_eff, 3)
        out["total_avg"] = round(sum(out["totals"]) / n_eff, 3)
        mean = out["total_avg"]
        variance = sum((t - mean) ** 2 for t in out["totals"]) / n_eff
        out["total_std"] = round(variance ** 0.5, 3)
    if gf_home:
        out["gf_avg_home"] = round(sum(gf_home) / len(gf_home), 3)
    if gf_away:
        out["gf_avg_away"] = round(sum(gf_away) / len(gf_away), 3)
    if ga_home:
        out["ga_avg_home"] = round(sum(ga_home) / len(ga_home), 3)
    if ga_away:
        out["ga_avg_away"] = round(sum(ga_away) / len(ga_away), 3)
    return out


def _parse_match_events(events: list[dict], home_id: int | None) -> dict:
    """Parse API-Sports events[] into a structured incidents summary.

    Returns:
      {
        "red_cards": [
          {"minute": int, "team": "home"|"away", "player": str, "detail": str},
          ...
        ],
        "yellow_cards": [
          {"minute": int, "team": "home"|"away", "player": str},
          ...
        ],
        "goals": [
          {"minute": int, "team": "home"|"away", "player": str, "detail": str},
          ...
        ],
        "home_players": int,   # current players on field (11 - red cards home)
        "away_players": int,   # current players on field (11 - red cards away)
        "home_reds": int,
        "away_reds": int,
        "home_yellows": int,
        "away_yellows": int,
        "numerical_advantage": "home"|"away"|"none",  # who has more players
        "numerical_diff": int,  # absolute difference in player count
      }
    """
    red_cards: list[dict] = []
    yellow_cards: list[dict] = []
    goals: list[dict] = []

    for ev in (events or []):
        ev_type   = (ev.get("type")   or "").strip()
        ev_detail = (ev.get("detail") or "").strip()
        ev_time   = ev.get("time") or {}
        minute    = ev_time.get("elapsed")
        team_id   = (ev.get("team") or {}).get("id")
        team_side = "home" if team_id == home_id else "away"
        player    = (ev.get("player") or {}).get("name") or "Desconocido"

        if ev_type == "Card":
            if ev_detail in ("Red Card", "Second Yellow card"):
                red_cards.append({
                    "minute":  minute,
                    "team":    team_side,
                    "player":  player,
                    "detail":  ev_detail,
                })
            elif ev_detail == "Yellow Card":
                yellow_cards.append({
                    "minute": minute,
                    "team":   team_side,
                    "player": player,
                })
        elif ev_type == "Goal" and ev_detail not in ("Missed Penalty",):
            goals.append({
                "minute": minute,
                "team":   team_side,
                "player": player,
                "detail": ev_detail,
            })

    home_reds    = sum(1 for r in red_cards if r["team"] == "home")
    away_reds    = sum(1 for r in red_cards if r["team"] == "away")
    home_yellows = sum(1 for y in yellow_cards if y["team"] == "home")
    away_yellows = sum(1 for y in yellow_cards if y["team"] == "away")
    home_players = max(1, 11 - home_reds)
    away_players = max(1, 11 - away_reds)

    if home_players > away_players:
        numerical_advantage = "home"
    elif away_players > home_players:
        numerical_advantage = "away"
    else:
        numerical_advantage = "none"

    return {
        "red_cards":            red_cards,
        "yellow_cards":         yellow_cards,
        "goals":                goals,
        "home_players":         home_players,
        "away_players":         away_players,
        "home_reds":            home_reds,
        "away_reds":            away_reds,
        "home_yellows":         home_yellows,
        "away_yellows":         away_yellows,
        "numerical_advantage":  numerical_advantage,
        "numerical_diff":       abs(home_players - away_players),
    }


def normalize_live_stats(fixture: dict) -> dict | None:
    fx = fixture.get("fixture", {})
    status = fx.get("status", {})
    short = status.get("short")
    if short not in LIVE_STATUSES:
        return None
    goals = fixture.get("goals", {}) or {}
    statistics = fixture.get("statistics", []) or []
    events = fixture.get("events", []) or []
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
    incidents = _parse_match_events(events, home_id)
    return {
        "minute":    status.get("elapsed"),
        "status":    short,
        "score":     {"home": goals.get("home"), "away": goals.get("away")},
        "home_stats": home_stats,
        "away_stats": away_stats,
        "incidents": incidents,
        "fetched_at": now_iso(),
    }


def summarize_match_for_llm(match_doc: dict) -> dict:
    """Strip a normalized match doc to a compact payload for the LLM.

    Now ALSO computes a competition_stage / pressure_state / is_final block via
    services.match_stage_detector. This block is fed to BOTH the pre-filter
    (Stage 1) and the deep analyst (Stage 2) so motivation classification is
    stage-aware before standings-based heuristics kick in.
    """
    # Local import to avoid an import cycle at module load time.
    from . import match_stage_detector as msd

    stage_info = msd.detect_match_stage(match_doc)
    return {
        "match_id": match_doc.get("match_id"),
        "sport": match_doc.get("sport", "football"),
        "league": match_doc.get("league"),
        "league_id": match_doc.get("league_id"),
        "season": match_doc.get("season"),
        "round": match_doc.get("round"),
        "kickoff_iso": match_doc.get("kickoff_iso"),
        "is_live": match_doc.get("is_live"),
        "venue": match_doc.get("venue"),
        "home_team": match_doc.get("home_team"),
        "away_team": match_doc.get("away_team"),
        "odds_snapshots": match_doc.get("odds_snapshots", [])[-2:],
        "live_stats": match_doc.get("live_stats"),
        "h2h_recent": match_doc.get("h2h_recent", []),
        # Competition metadata (set by data_ingestion via football_competitions)
        "competition_canonical_name": match_doc.get("competition_canonical_name"),
        "competition_tier": match_doc.get("competition_tier"),
        "competition_type": match_doc.get("competition_type"),
        "competition_region": match_doc.get("competition_region"),
        # Stage / importance block — drives the COMPETITION_STAGE_OVERRIDE rules
        "competition_stage": stage_info["competition_stage"],
        "match_importance": stage_info["match_importance"],
        "is_knockout": stage_info["is_knockout"],
        "is_final": stage_info["is_final"],
        "is_two_legged_tie": stage_info["is_two_legged_tie"],
        "leg": stage_info["leg"],
        "aggregate_score": stage_info["aggregate_score"],
        "pressure_state": stage_info["pressure_state"],
        # Optional: external injury/team-news snippets (only attached when the
        # injury_sources hook is enabled; otherwise the analyst engine adds
        # nothing here and the LLM treats it as missing context).
        "team_news_snippets": match_doc.get("team_news_snippets"),
    }


# ── Multi-sport helpers (basketball / baseball) ──────────────────────────────
def normalize_odds_generic(odds_response: list[dict], sport: str) -> dict:
    """Normalize odds for basketball or baseball.

    API-Sports basketball/baseball share a similar bookmaker→bets→values structure
    but use different market names (e.g. "Home/Away" for moneyline, "Asian Handicap",
    "Over/Under"). We collect what's available and bucket into a generic schema.
    """
    if not odds_response:
        return {"available": False, "snapshot_at": now_iso(), "bookmakers": [], "markets": {}}
    item = odds_response[0]
    bookmakers_data = item.get("bookmakers", []) or []
    markets: dict[str, list[dict]] = {
        "Moneyline": [],     # Home / Away
        "Spread": [],        # Point spread / Run Line
        "Total": [],         # Total Points / Total Runs
    }
    bm_names = []
    for bm in bookmakers_data:
        bm_name = bm.get("name", "Unknown")
        bm_names.append(bm_name)
        for bet in bm.get("bets", []) or []:
            bname = (bet.get("name") or "").strip().lower()
            values = bet.get("values", []) or []
            # Moneyline (Home/Away)
            if bname in ("home/away", "match winner", "moneyline", "winner"):
                row = {"bookmaker": bm_name}
                for v in values:
                    val = (v.get("value") or "").lower()
                    try:
                        odd = float(v.get("odd", 0))
                    except Exception:
                        odd = 0.0
                    if "home" in val or val in ("1", "local"):
                        row["home"] = odd
                    elif "away" in val or val in ("2", "visitor", "visiting"):
                        row["away"] = odd
                markets["Moneyline"].append(row)
            # Spread (point spread / run line)
            elif bname in ("asian handicap", "spread", "run line", "handicap"):
                row = {"bookmaker": bm_name, "lines": []}
                for v in values:
                    try:
                        row["lines"].append({"value": v.get("value"), "odd": float(v.get("odd", 0))})
                    except Exception:
                        pass
                markets["Spread"].append(row)
            # Total
            elif bname in ("over/under", "total", "total points", "total runs"):
                row = {"bookmaker": bm_name, "lines": {}}
                for v in values:
                    try:
                        row["lines"][v.get("value")] = float(v.get("odd", 0))
                    except Exception:
                        pass
                markets["Total"].append(row)
    return {
        "available": bool(bm_names),
        "snapshot_at": now_iso(),
        "bookmakers": bm_names,
        "markets": markets,
    }


def normalize_team_context_generic(stats: dict, standings_resp: list[dict], team_id: int, sport: str) -> dict:
    """Normalize team context for basketball/baseball.

    API-Sports basketball:
      stats.games.wins.all.total, games.loses.all.total, points.{for,against}.average.all
    API-Sports baseball:
      stats.games.wins.all.total, games.loses.all.total, points.{for,against}.average.all
    Standings: league.standings[][] with rank, points/wins/losses.
    """
    ctx: dict[str, Any] = {
        "fetched_at": now_iso(),
        "data_source_season": "2024 (proxy)",
        "form_last_5": "",
        "wins_total": None,
        "losses_total": None,
        "points_for_avg": None,
        "points_against_avg": None,
        "position": None,
        "description": None,
        "motivation_flags": {
            "nothing_to_play_for": False,
            "playoff_race": False,
            "eliminated": False,
        },
    }
    if stats:
        games = stats.get("games") or {}
        try:
            ctx["wins_total"] = (games.get("wins") or {}).get("all", {}).get("total")
        except Exception:
            pass
        try:
            ctx["losses_total"] = (games.get("loses") or {}).get("all", {}).get("total")
        except Exception:
            pass
        points = stats.get("points") or {}
        try:
            pfor = (points.get("for") or {}).get("average", {}).get("all")
            if pfor:
                ctx["points_for_avg"] = float(pfor)
        except Exception:
            pass
        try:
            pag = (points.get("against") or {}).get("average", {}).get("all")
            if pag:
                ctx["points_against_avg"] = float(pag)
        except Exception:
            pass
    # Standings
    try:
        if standings_resp:
            # API-basketball: response is list of group rows
            # API-baseball: response is { league: { standings: [[rows]] } } variant
            rows = []
            for s in standings_resp:
                if isinstance(s, dict) and "league" in s:
                    league = s.get("league") or {}
                    for g in league.get("standings", []) or []:
                        if isinstance(g, list):
                            rows.extend(g)
                        else:
                            rows.append(g)
                elif isinstance(s, list):
                    rows.extend(s)
                else:
                    rows.append(s)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                team = (row.get("team") or {})
                if team.get("id") == team_id:
                    ctx["position"] = row.get("position") or row.get("rank")
                    ctx["description"] = row.get("description") or row.get("form")
                    break
    except Exception:
        pass
    return ctx


def normalize_live_stats_generic(game: dict, sport: str) -> dict | None:
    """Live stats for basketball/baseball games."""
    status = (game.get("status") or {})
    short = status.get("short")
    LIVE_HOOPS = {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT", "LIVE"}
    LIVE_BASEBALL = {"IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "IN8", "IN9", "LIVE", "IN"}
    live_set = LIVE_HOOPS if sport == "basketball" else LIVE_BASEBALL
    if short not in live_set and not (game.get("scores") and short in ("LIVE", None)):
        return None
    scores = game.get("scores") or {}
    return {
        "minute": status.get("timer") or status.get("long"),
        "status": short,
        "score": {
            "home": (scores.get("home") or {}).get("total"),
            "away": (scores.get("away") or {}).get("total"),
        },
        "quarter_or_inning": status.get("long"),
        "fetched_at": now_iso(),
    }
