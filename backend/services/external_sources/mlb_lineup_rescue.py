"""MLB lineup/pitcher rescue layer.

Before discarding an MLB game for "datos incompletos sobre el pitcher",
this module fans out to the four MLB-specific external scrapers
(`rotowire_mlb`, `mlb_official_lineups`, `fantasypros_mlb`, `espn_mlb`),
merges their results, and tries to recover the missing pitcher name(s).

Public entrypoint
-----------------
`rescue_mlb_pitchers(date_str, games)` where `games` is a list of dicts
each containing at minimum `home_team`, `away_team`, and (optionally)
`home_probable_id` / `home_probable_name` / etc.

Returns a parallel list (same order as input) of rescue payloads:

    {
        "match_key":           "<away_lower>@<home_lower>",
        "home_pitcher_name":   str | None,
        "away_pitcher_name":   str | None,
        "home_pitcher_source": str | None,   # which scraper confirmed it
        "away_pitcher_source": str | None,
        "lineup_status":       "confirmed" | "projected" | "missing",
        "sources_consulted":   [ { source, status, url, data_types, ... } ],
        "data_quality":        { lineup_quality, pitcher_quality, overall },
        "signals":             [ <signal dict> ],   # ready to merge upstream
        "conflict":            bool,
    }

The scrapers are called ONCE per `date_str`, cached for the duration of a
single rescue call so we don't hammer them for every game.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import rotowire_mlb, mlb_official_lineups, fantasypros_mlb, espn_mlb
from . import rotogrinders_mlb, fantasyalarm_mlb

log = logging.getLogger("external_sources.mlb_lineup_rescue")

ALL_SCRAPERS = (
    ("rotowire_mlb_lineups",     rotowire_mlb,           "primary"),
    ("mlb_official_lineups",     mlb_official_lineups,   "ground_truth"),
    ("fantasypros_mlb_lineups",  fantasypros_mlb,        "secondary"),
    ("espn_mlb_scoreboard",      espn_mlb,               "secondary"),
    ("rotogrinders_mlb_lineups", rotogrinders_mlb,       "tertiary"),
    ("fantasyalarm_mlb_lineups", fantasyalarm_mlb,       "tertiary"),
)

# Per-scraper hard timeout so a slow site can't stall the rescue.
PER_SCRAPER_TIMEOUT = 8.0


def _norm_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


def _match_key(home: str, away: str) -> str:
    return f"{_norm_team(away)}@{_norm_team(home)}"


def _fuzzy_team_match(target_key: str, available: dict) -> Optional[dict]:
    """Loose team-name match. Tries exact first, then any matchup whose
    home or away token contains the target's home or away token.
    Order-tolerant: also tries the reversed (away↔home) key in case a
    scraper inverted the matchup direction.
    """
    if not available:
        return None
    if target_key in available:
        return available[target_key]
    away_part, _, home_part = target_key.partition("@")
    reversed_key = f"{home_part}@{away_part}"
    if reversed_key in available:
        # The matchup exists with swapped sides — return a SHALLOW COPY
        # with home/away pitchers swapped so downstream sees correct sides.
        v = dict(available[reversed_key])
        v["home_pitcher_name"], v["away_pitcher_name"] = (
            v.get("away_pitcher_name"), v.get("home_pitcher_name"),
        )
        v["home_batting_order"], v["away_batting_order"] = (
            v.get("away_batting_order") or [], v.get("home_batting_order") or [],
        )
        v["home_team"], v["away_team"] = v.get("away_team"), v.get("home_team")
        return v
    away_tokens = set(away_part.split())
    home_tokens = set(home_part.split())
    # Look for a matchup where BOTH last-word tokens match in the normal
    # direction OR in the reversed direction.
    for k, v in available.items():
        k_away, _, k_home = k.partition("@")
        k_away_tokens = set(k_away.split())
        k_home_tokens = set(k_home.split())
        if (away_tokens & k_away_tokens) and (home_tokens & k_home_tokens):
            return v
        if (away_tokens & k_home_tokens) and (home_tokens & k_away_tokens):
            # Reversed direction → swap pitcher sides.
            v2 = dict(v)
            v2["home_pitcher_name"], v2["away_pitcher_name"] = (
                v2.get("away_pitcher_name"), v2.get("home_pitcher_name"),
            )
            v2["home_team"], v2["away_team"] = v2.get("away_team"), v2.get("home_team")
            return v2
    return None


def _compute_data_quality(rescue: dict) -> dict:
    """Score 0-100. Per the spec:
       pitchers confirmed: +35  / lineups confirmed: +30 / projected: +15
       injuries available: +10  / weather: +10           / bullpen/pitcher_stats: +15
    Here we only have pitcher + lineup info from the scrapers.
    """
    score = 0
    breakdown = {}
    pitcher_quality = 0
    lineup_quality  = 0
    if rescue.get("home_pitcher_name") and rescue.get("away_pitcher_name"):
        score += 35
        pitcher_quality = 100
        breakdown["pitchers"] = "+35 (both confirmed)"
    elif rescue.get("home_pitcher_name") or rescue.get("away_pitcher_name"):
        score += 18
        pitcher_quality = 50
        breakdown["pitchers"] = "+18 (one of two)"
    lineup_status = rescue.get("lineup_status")
    if lineup_status == "confirmed":
        score += 30
        lineup_quality = 100
        breakdown["lineups"] = "+30 (confirmed)"
    elif lineup_status == "projected":
        score += 15
        lineup_quality = 60
        breakdown["lineups"] = "+15 (projected)"
    overall = max(0, min(100, score))
    return {
        "pitcher_quality": pitcher_quality,
        "lineup_quality":  lineup_quality,
        "injury_quality":  0,    # not yet sourced from these scrapers
        "weather_quality": 0,
        "overall":         overall,
        "breakdown":       breakdown,
    }


async def _safe_fetch(fetch_coro, name: str, url_hint: str = "") -> dict:
    try:
        return await asyncio.wait_for(fetch_coro, timeout=PER_SCRAPER_TIMEOUT)
    except asyncio.TimeoutError:
        log.debug("rescue: %s timed out", name)
        return {"matchups": {}, "sources_consulted": [{
            "source": name, "status": "failed", "url": url_hint,
            "data_types": [], "reason": "timeout",
        }]}
    except Exception as exc:
        log.debug("rescue: %s crashed: %s", name, exc)
        return {"matchups": {}, "sources_consulted": [{
            "source": name, "status": "failed", "url": url_hint,
            "data_types": [], "reason": f"crash:{exc}"[:80],
        }]}


async def rescue_mlb_pitchers(date_str: str, games: list[dict]) -> list[dict]:
    """Run all MLB external scrapers in parallel for the given date and
    try to recover pitcher info for each game.

    Returns a list of rescue payloads aligned with `games` by index.
    Always returns a list of the same length; failed lookups have all
    pitcher fields ``None`` and a populated `sources_consulted`.
    """
    if not games:
        return []

    # Run every scraper once for the whole date.
    bundles = await asyncio.gather(
        _safe_fetch(rotowire_mlb.fetch_lineups(date_str),     "rotowire_mlb_lineups",   rotowire_mlb.URL),
        _safe_fetch(mlb_official_lineups.fetch_lineups(date_str), "mlb_official_lineups", "https://www.mlb.com/starting-lineups/"),
        _safe_fetch(fantasypros_mlb.fetch_lineups(date_str),  "fantasypros_mlb_lineups", fantasypros_mlb.URL),
        _safe_fetch(espn_mlb.fetch_lineups(date_str),         "espn_mlb_scoreboard",     "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"),
        _safe_fetch(rotogrinders_mlb.fetch_lineups(date_str), "rotogrinders_mlb_lineups", rotogrinders_mlb.URL),
        _safe_fetch(fantasyalarm_mlb.fetch_lineups(date_str), "fantasyalarm_mlb_lineups", fantasyalarm_mlb.URL),
    )
    # Merge sources_consulted lists for per-game telemetry.
    all_sources: list[dict] = []
    for b in bundles:
        all_sources.extend(b.get("sources_consulted") or [])

    # Now for each requested game, look it up across all bundles. Priority
    # order: mlb_official > rotowire > fantasypros > espn > rotogrinders
    # > fantasyalarm. Higher-confidence sources win.
    scrapers_in_priority = [
        ("mlb_official_lineups",     bundles[1]),
        ("rotowire_mlb_lineups",     bundles[0]),
        ("fantasypros_mlb_lineups",  bundles[2]),
        ("espn_mlb_scoreboard",      bundles[3]),
        ("rotogrinders_mlb_lineups", bundles[4]),
        ("fantasyalarm_mlb_lineups", bundles[5]),
    ]

    results: list[dict] = []
    for g in games:
        home = g.get("home_team") or g.get("home_team_name") or ""
        if isinstance(home, dict):
            home = home.get("name") or ""
        away = g.get("away_team") or g.get("away_team_name") or ""
        if isinstance(away, dict):
            away = away.get("name") or ""
        key = _match_key(home, away)

        # Already-known pitcher names from the caller take precedence.
        home_pitcher = g.get("home_probable_name") or (g.get("home_probable") or {}).get("name")
        away_pitcher = g.get("away_probable_name") or (g.get("away_probable") or {}).get("name")
        home_source  = "mlb_stats_api" if home_pitcher else None
        away_source  = "mlb_stats_api" if away_pitcher else None
        lineup_status = "missing"

        # Walk scrapers in priority order; first hit wins per side.
        all_pitcher_names: dict[str, list[str]] = {"home": [], "away": []}
        for src_name, bundle in scrapers_in_priority:
            data = _fuzzy_team_match(key, bundle.get("matchups") or {})
            if not data:
                continue
            if data.get("home_pitcher_name"):
                all_pitcher_names["home"].append(data["home_pitcher_name"])
                if not home_pitcher:
                    home_pitcher = data["home_pitcher_name"]
                    home_source  = src_name
            if data.get("away_pitcher_name"):
                all_pitcher_names["away"].append(data["away_pitcher_name"])
                if not away_pitcher:
                    away_pitcher = data["away_pitcher_name"]
                    away_source  = src_name
            # Status comes from the most authoritative source that has a status.
            if lineup_status == "missing" and data.get("status"):
                lineup_status = data["status"]

        # Detect conflicts (different sources naming different pitchers for
        # the same team — usually means a late scratch was already applied
        # by one source but not the other).
        def _conflicting(names: list[str]) -> bool:
            if len(names) < 2:
                return False
            normalized = {n.lower().strip() for n in names if n}
            return len(normalized) > 1

        conflict = _conflicting(all_pitcher_names["home"]) or _conflicting(all_pitcher_names["away"])

        rescue = {
            "match_key":           key,
            "home_team":           home,
            "away_team":           away,
            "home_pitcher_name":   home_pitcher,
            "away_pitcher_name":   away_pitcher,
            "home_pitcher_source": home_source,
            "away_pitcher_source": away_source,
            "lineup_status":       lineup_status,
            "sources_consulted":   list(all_sources),
            "conflict":            conflict,
        }
        rescue["data_quality"] = _compute_data_quality(rescue)
        rescue["signals"] = _build_signals(rescue)
        results.append(rescue)
    return results


def _build_signals(rescue: dict) -> list[dict]:
    """Build the editorial_context signals from the rescue outcome."""
    from ..signal_catalog import make_signal as _mk

    out: list[dict] = []
    sources = rescue.get("sources_consulted") or []
    successes = [s for s in sources if (s.get("status") in ("success", "partial"))]

    # EXTERNAL_SOURCE_USED — once for the rescue overall
    if successes:
        sig = _mk("EXTERNAL_SOURCE_USED", sport="baseball")
        if sig:
            # Surface the first successful source URL so the user can click.
            top = successes[0]
            sig["source"]     = top.get("source")
            sig["source_url"] = top.get("url")
            sig["extra_explanation"] = (
                f"Se consultaron {len(sources)} fuentes externas; "
                f"{len(successes)} con éxito ({', '.join(s.get('source','?') for s in successes[:3])})."
            )
            out.append(sig)

    # PITCHER_CONFIRMED_EXTERNAL — when at least one side was rescued from external
    if (rescue.get("home_pitcher_source") and rescue["home_pitcher_source"] != "mlb_stats_api") or \
       (rescue.get("away_pitcher_source") and rescue["away_pitcher_source"] != "mlb_stats_api"):
        sig = _mk("PITCHER_CONFIRMED_EXTERNAL", sport="baseball")
        if sig:
            src = rescue.get("home_pitcher_source") or rescue.get("away_pitcher_source")
            top = next((s for s in successes if s.get("source") == src), None)
            sig["source"]     = src
            sig["source_url"] = top.get("url") if top else None
            sig["extra_explanation"] = (
                f"Pitcher recuperado vía {src}: home={rescue.get('home_pitcher_name')!r} · "
                f"away={rescue.get('away_pitcher_name')!r}"
            )
            out.append(sig)

    # LINEUP_PROJECTED_EXTERNAL / LINEUP_CONFIRMED_EXTERNAL
    if rescue.get("lineup_status") == "projected":
        sig = _mk("LINEUP_PROJECTED_EXTERNAL", sport="baseball")
        if sig:
            out.append(sig)
    elif rescue.get("lineup_status") == "confirmed":
        sig = _mk("LINEUP_CONFIRMED_EXTERNAL", sport="baseball")
        if sig:
            out.append(sig)

    # DATA_INCOMPLETE_AFTER_ALL_SOURCES — when none of the scrapers could
    # recover and we still don't have both pitchers.
    if not (rescue.get("home_pitcher_name") and rescue.get("away_pitcher_name")):
        sig = _mk("DATA_INCOMPLETE_AFTER_ALL_SOURCES", sport="baseball")
        if sig:
            attempted = ", ".join(s.get("source", "?") for s in sources)
            sig["extra_explanation"] = (
                f"Pitcher faltante tras consultar: {attempted or '—'}."
            )
            out.append(sig)

    # SOURCE_CONFLICT
    if rescue.get("conflict"):
        sig = _mk("SOURCE_CONFLICT", sport="baseball")
        if sig:
            out.append(sig)

    return out


__all__ = ["rescue_mlb_pitchers", "ALL_SCRAPERS"]
