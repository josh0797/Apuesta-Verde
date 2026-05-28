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

    # ── HistoricalGoalProfile (P2) ──────────────────────────────────────
    # Extra agregados para alimentar el Protected Market Rescue Layer:
    #   - team_exceeded_2_goals: cuántos partidos el equipo anotó >2 goles
    #   - match_exceeded_2_total / match_exceeded_3_total: total goles >2 / >3
    #   - under_2_5_rate, under_3_5_rate
    #   - failed_to_score_over_2_rate: partidos donde el equipo NO anotó más de 2
    #   - trend_summary: texto humano corto (ES)
    if n_eff:
        team_exceeded_2_goals = sum(1 for gf in out["gf"] if gf > 2)
        team_exceeded_2_rate  = round(team_exceeded_2_goals / n_eff, 3)
        match_exceeded_2_total = sum(1 for t in out["totals"] if t > 2)
        match_exceeded_3_total = sum(1 for t in out["totals"] if t > 3)
        under_2_5_rate = round(out["under_2_5_count"] / n_eff, 3)
        under_3_5_rate = round(out["under_3_5_count"] / n_eff, 3)
        failed_to_score_over_2_rate = round(
            sum(1 for gf in out["gf"] if gf <= 2) / n_eff, 3,
        )

        # Trend summary — narrativa humana corta (ES)
        if team_exceeded_2_rate <= 0.20 and n_eff >= 10:
            trend = (
                f"No ha superado los 2 goles en {n_eff - team_exceeded_2_goals} "
                f"de sus últimos {n_eff} partidos."
            )
        elif under_3_5_rate >= 0.70 and n_eff >= 10:
            trend = (
                f"Under 3.5 se cumplió en {out['under_3_5_count']} de los últimos "
                f"{n_eff} partidos ({int(under_3_5_rate*100)}%)."
            )
        elif team_exceeded_2_rate >= 0.50:
            trend = (
                f"Equipo ofensivo: superó los 2 goles en {team_exceeded_2_goals} "
                f"de sus últimos {n_eff} partidos."
            )
        else:
            trend = (
                f"Promedio de {out['gf_avg']:.2f} goles a favor en últimos "
                f"{n_eff} partidos."
            )

        out["historical_goal_profile"] = {
            "matches_analyzed":              n_eff,
            "goals_for_avg":                 out["gf_avg"],
            "goals_against_avg":             out["ga_avg"],
            "total_goals_avg":               out["total_avg"],
            "under_2_5_rate":                under_2_5_rate,
            "under_3_5_rate":                under_3_5_rate,
            "team_exceeded_2_goals_count":   team_exceeded_2_goals,
            "team_exceeded_2_goals_rate":    team_exceeded_2_rate,
            "match_exceeded_2_total_count":  match_exceeded_2_total,
            "match_exceeded_3_total_count":  match_exceeded_3_total,
            "failed_to_score_over_2_rate":   failed_to_score_over_2_rate,
            "clean_sheet_rate":              round(out["clean_sheets"] / n_eff, 3),
            "failed_to_score_rate":          round(out["failed_to_score"] / n_eff, 3),
            "btts_rate":                     round(out["btts"] / n_eff, 3),
            "trend_summary":                 trend,
        }

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
        # Optional: Understat enrichment (xG / PPDA / shots from the linked
        # historical match). Set via POST /api/understat/link or future
        # automatic linker. When present the LLM treats it as authoritative
        # over the internal Poisson xG proxy.
        "understat": match_doc.get("_understat"),
        # Optional: P3 Editorial Context (Scrapy). Compact subset of fields
        # the LLM should consider (motivation_notes, factual_notes,
        # consensus_market) WITHOUT being allowed to recommend a pick
        # purely because the editorial does. Read-only contextual hint.
        "editorial_context": _compact_editorial_context(match_doc.get("editorial_context")),
    }


def _compact_editorial_context(ec: dict | None) -> dict | None:
    """Strip the editorial context dict to the fields the LLM actually needs.

    Keeps motivation_notes / risks / injury_notes / factual_notes /
    consensus_market / scores; drops the full raw signal list and metadata
    to save token budget.
    """
    if not ec or not isinstance(ec, dict) or not ec.get("available"):
        return None
    return {
        "available":             True,
        "sources_count":         ec.get("sources_count"),
        "sources":               ec.get("sources"),
        "consensus_market":      ec.get("consensus_market"),
        "consensus_direction":   ec.get("consensus_direction"),
        "motivation_notes":      (ec.get("motivation_notes") or [])[:6],
        "factual_notes":         (ec.get("factual_notes") or [])[:6],
        "risks":                 (ec.get("risks") or [])[:5],
        "injury_notes":          (ec.get("injury_notes") or [])[:5],
        "contradiction_flags":   ec.get("contradiction_flags") or [],
        "freshness_score":       ec.get("freshness_score"),
        "reliability_score":     ec.get("reliability_score"),
        "narrative_bias_score":  ec.get("narrative_bias_score"),
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
    """Live stats for basketball/baseball games.

    For baseball, delegates to the specialised `normalize_live_stats_baseball`
    so per-inning breakdowns, hits, errors and inning-half deduction are all
    surfaced for `live_baseball_analytics.compute_live_analysis()`.
    """
    if sport == "baseball":
        return normalize_live_stats_baseball(game)
    status = (game.get("status") or {})
    short = status.get("short")
    LIVE_HOOPS = {"Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT", "LIVE"}
    if short not in LIVE_HOOPS and not (game.get("scores") and short in ("LIVE", None)):
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


# ── Baseball-specific live normalizer ─────────────────────────────────────────
LIVE_BASEBALL = {
    "IN1", "IN2", "IN3", "IN4", "IN5", "IN6", "IN7", "IN8", "IN9",
    "IN10", "IN11", "IN12", "IN13", "IN14", "IN15",
    "IN", "LIVE", "BRK", "BT",
}


def _parse_baseball_inning_number(status_short: str | None, status_long: str | None) -> int | None:
    """Best-effort extraction of the current inning number from API-Sports.

    Priority:
      1. Short code `IN<N>` (most reliable when present).
      2. Long string parsing ("Top 7th", "Bottom of the 3rd", "Inning 5"…).
    """
    if status_short:
        s = status_short.upper()
        if s.startswith("IN") and len(s) > 2 and s[2:].isdigit():
            return int(s[2:])
    if status_long:
        import re as _re
        m = _re.search(r"(\d{1,2})\s*(st|nd|rd|th)?", status_long)
        if m:
            try:
                n = int(m.group(1))
                if 1 <= n <= 20:
                    return n
            except ValueError:
                pass
    return None


def _deduce_inning_half(
    home_innings: dict,
    away_innings: dict,
    current_inning: int | None,
    status_long: str | None,
) -> str | None:
    """Deduce whether we're in the Top or Bottom of the current inning.

    Rules (in order of priority):
      1. If `status_long` mentions "top"/"bottom" explicitly, use that.
      2. Compare per-inning counters for the current inning:
         - away batted but home hasn't → "top"
         - both have batted          → "bottom" (in progress or just ended)
      3. Fallback to None when ambiguous.
    """
    if status_long:
        sl = status_long.lower()
        if "top" in sl:
            return "top"
        if "bot" in sl:
            return "bottom"
        if "mid" in sl:
            return "middle"  # between halves
        if "end" in sl:
            return "end"

    if current_inning is None:
        return None
    key = str(current_inning)
    away_val = (away_innings or {}).get(key)
    home_val = (home_innings or {}).get(key)
    if away_val is not None and home_val is None:
        return "top"
    if away_val is not None and home_val is not None:
        return "bottom"
    return None


def _innings_played_count(home_innings: dict, away_innings: dict) -> int:
    """Count how many innings have been started (at least Top half played).

    Excludes the `extra` synthetic key that API-Sports adds for runs-after-9.
    """
    if not isinstance(home_innings, dict) and not isinstance(away_innings, dict):
        return 0
    keys = set()
    for d in (home_innings or {}, away_innings or {}):
        for k, v in d.items():
            if k == "extra":
                continue
            if v is not None and str(k).isdigit():
                keys.add(int(k))
    return max(keys) if keys else 0


def normalize_live_stats_baseball(game: dict) -> dict | None:
    """Normalize API-Sports baseball game response into a live_stats payload.

    Returns a dict shape compatible with
    `live_baseball_analytics.compute_live_analysis()`:

      {
        "minute": <inning>,                # reused field for UI parity
        "status": "IN5",
        "status_long": "Top of the 5th",
        "score": {"home": int, "away": int},
        "quarter_or_inning": "Inning 5",
        "inning": int|None,
        "inning_half": "top"|"bottom"|"middle"|"end"|None,
        "innings_played": int,
        "is_extra_innings": bool,
        "innings_runs": {
            "home": {"1":0,"2":0,...,"extra":None},
            "away": {"1":0,...},
        },
        "last_inning_runs": {"home": int|None, "away": int|None},
        "home_stats": {"Hits": int, "Errors": int, "Runs": int},
        "away_stats": {"Hits": int, "Errors": int, "Runs": int},
        "fetched_at": iso,
      }

    Returns None when the game has no status or hasn't started.
    """
    if not game:
        return None
    status      = (game.get("status") or {})
    short       = status.get("short")
    long_status = status.get("long") or ""

    # Be lenient: keep payload for live OR recently-finished games (UI shows
    # final-state cards too). Return None only when the game truly hasn't
    # started or has no scores yet.
    scores = game.get("scores") or {}
    home_obj = scores.get("home") or {}
    away_obj = scores.get("away") or {}

    has_any_score = (
        home_obj.get("total") is not None or
        away_obj.get("total") is not None or
        home_obj.get("hits") is not None or
        away_obj.get("hits") is not None
    )
    if short in ("NS", "TBD", "PST", "CANC") and not has_any_score:
        return None

    home_innings = home_obj.get("innings") or {}
    away_innings = away_obj.get("innings") or {}

    inning      = _parse_baseball_inning_number(short, long_status)
    if inning is None:
        # Last resort: use the highest inning that has any score on either side
        inning = _innings_played_count(home_innings, away_innings) or None

    # Finished games never have an active half — clear inning_half to avoid
    # misleading downstream logic.
    if short in ("FT", "AOT", "POST", "CANC", "ABD", "INTR"):
        inning_half = None
    else:
        inning_half = _deduce_inning_half(home_innings, away_innings, inning, long_status)
    innings_played   = _innings_played_count(home_innings, away_innings)
    is_extra_innings = innings_played > 9

    def _int_or_none(v):
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # Last completed inning runs (excluding `extra`).
    last_inning_runs = {"home": None, "away": None}
    if innings_played > 0:
        k = str(innings_played)
        last_inning_runs["home"] = _int_or_none(home_innings.get(k))
        last_inning_runs["away"] = _int_or_none(away_innings.get(k))

    home_total  = _int_or_none(home_obj.get("total"))
    away_total  = _int_or_none(away_obj.get("total"))
    home_hits   = _int_or_none(home_obj.get("hits"))   or 0
    away_hits   = _int_or_none(away_obj.get("hits"))   or 0
    home_errors = _int_or_none(home_obj.get("errors")) or 0
    away_errors = _int_or_none(away_obj.get("errors")) or 0

    return {
        "minute":           inning,            # reused for UI parity
        "status":           short,
        "status_long":      long_status,
        "score":            {"home": home_total, "away": away_total},
        "quarter_or_inning": long_status or (f"Inning {inning}" if inning else None),
        "inning":           inning,
        "inning_half":      inning_half,
        "innings_played":   innings_played,
        "is_extra_innings": is_extra_innings,
        "innings_runs":     {"home": home_innings, "away": away_innings},
        "last_inning_runs": last_inning_runs,
        # Keys named to match what live_baseball_analytics reads from
        # `home_stats[...]` / `away_stats[...]`.
        "home_stats": {
            "Hits":   home_hits,
            "Errors": home_errors,
            "Runs":   home_total if home_total is not None else 0,
        },
        "away_stats": {
            "Hits":   away_hits,
            "Errors": away_errors,
            "Runs":   away_total if away_total is not None else 0,
        },
        "fetched_at": now_iso(),
    }
