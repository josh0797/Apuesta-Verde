"""Basketball matchup-discovery rescue layer.

Before discarding a basketball date for "0 games", or to enrich an
already-ingested basketball day with cross-source telemetry, this
module fans out to the basketball-specific external scrapers
(`sofascore_basketball`, `flashscore_basketball`, plus ESPN scoreboard
when available), merges their results and exposes:

  • a deduplicated `matchups` dict keyed by ``<away_lower>@<home_lower>``
  • a `sources_consulted` list ready to attach to `pipeline_meta`
  • per-game `_external_evidence` payloads aligned with the ones used
    by `mlb_lineup_rescue` so the frontend can render them with the
    same `SourcesConsultedPanel` component.

Public entrypoints
------------------
- ``rescue_basketball_day(date_str)`` → returns the merged matchups +
  sources_consulted telemetry for the given calendar day (no ingestion
  side-effects).
- ``attach_evidence(matches, rescue_payload)`` → mutates each
  basketball match in `matches` adding ``_external_evidence`` so the
  frontend's SourcesConsultedPanel renders it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from . import sofascore_basketball, flashscore_basketball

log = logging.getLogger("external_sources.basketball_rescue")

# Per-scraper hard timeout so a slow site can't stall the rescue.
PER_SCRAPER_TIMEOUT = 8.0


ALL_SCRAPERS = (
    ("sofascore_basketball",  sofascore_basketball,  "primary"),
    ("flashscore_basketball", flashscore_basketball, "secondary"),
)


def _norm_team(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


def _match_key(home: str, away: str) -> str:
    return f"{_norm_team(away)}@{_norm_team(home)}"


def _fuzzy_team_match(target_key: str, available: dict) -> Optional[dict]:
    """Loose team-name match (mirror of mlb_lineup_rescue helper)."""
    if not available:
        return None
    if target_key in available:
        return available[target_key]
    away_part, _, home_part = target_key.partition("@")
    reversed_key = f"{home_part}@{away_part}"
    if reversed_key in available:
        v = dict(available[reversed_key])
        v["home_team"], v["away_team"] = v.get("away_team"), v.get("home_team")
        return v
    away_tokens = set(away_part.split())
    home_tokens = set(home_part.split())
    for k, v in available.items():
        k_away, _, k_home = k.partition("@")
        k_away_tokens = set(k_away.split())
        k_home_tokens = set(k_home.split())
        if (away_tokens & k_away_tokens) and (home_tokens & k_home_tokens):
            return v
        if (away_tokens & k_home_tokens) and (home_tokens & k_away_tokens):
            v2 = dict(v)
            v2["home_team"], v2["away_team"] = v2.get("away_team"), v2.get("home_team")
            return v2
    return None


async def _safe_fetch(fetch_coro, name: str, url_hint: str = "") -> dict:
    try:
        return await asyncio.wait_for(fetch_coro, timeout=PER_SCRAPER_TIMEOUT)
    except asyncio.TimeoutError:
        log.debug("basketball rescue: %s timed out", name)
        return {"matchups": {}, "sources_consulted": [{
            "source": name, "status": "failed", "url": url_hint,
            "data_types": [], "reason": "timeout",
        }]}
    except Exception as exc:
        log.debug("basketball rescue: %s crashed: %s", name, exc)
        return {"matchups": {}, "sources_consulted": [{
            "source": name, "status": "failed", "url": url_hint,
            "data_types": [], "reason": f"crash:{exc}"[:80],
        }]}


async def rescue_basketball_day(date_str: str) -> dict:
    """Fan out to every registered basketball external scraper for the
    given ``YYYY-MM-DD`` date. Returns a merged payload:

        {
          "matchups":          {"<away>@<home>": {…}},
          "sources_consulted": [...],
          "source_priority":   ["sofascore_basketball", "flashscore_basketball"],
        }
    """
    if not date_str:
        return {"matchups": {}, "sources_consulted": [], "source_priority": []}

    # Run scrapers in parallel.
    bundles = await asyncio.gather(
        _safe_fetch(
            sofascore_basketball.fetch_matchups(date_str),
            "sofascore_basketball",
            sofascore_basketball.URL_TEMPLATE.format(date=date_str),
        ),
        _safe_fetch(
            flashscore_basketball.fetch_matchups(date_str),
            "flashscore_basketball",
            flashscore_basketball.URL,
        ),
    )

    sources_consulted: list[dict] = []
    for b in bundles:
        sources_consulted.extend(b.get("sources_consulted") or [])

    # Priority merge: sofascore wins (has kickoff_ts), flashscore enriches gaps.
    merged: dict[str, dict] = {}
    for name, bundle in (("sofascore_basketball", bundles[0]),
                         ("flashscore_basketball", bundles[1])):
        for key, entry in (bundle.get("matchups") or {}).items():
            if key in merged:
                # Append corroborating source to the existing entry.
                merged[key].setdefault("_corroborated_by", []).append(name)
                continue
            entry = dict(entry)
            entry["_primary_source"] = name
            merged[key] = entry

    log.info(
        "basketball rescue %s: %d matchups merged (sofascore=%d, flashscore=%d)",
        date_str, len(merged),
        len(bundles[0].get("matchups") or {}),
        len(bundles[1].get("matchups") or {}),
    )
    return {
        "matchups":          merged,
        "sources_consulted": sources_consulted,
        "source_priority":   [n for n, _, _ in ALL_SCRAPERS],
    }


def attach_evidence(matches: list[dict], rescue_payload: dict) -> int:
    """Attach `_external_evidence` to each basketball match in `matches`,
    looking it up in `rescue_payload['matchups']`. Returns the number of
    matches that received evidence (i.e. were corroborated by at least
    one external source)."""
    if not (matches and rescue_payload):
        return 0
    available = rescue_payload.get("matchups") or {}
    sources_consulted = rescue_payload.get("sources_consulted") or []
    attached = 0
    for m in matches:
        if m.get("sport") and m.get("sport") != "basketball":
            continue
        home = (m.get("home_team") or {}).get("name") if isinstance(m.get("home_team"), dict) else (m.get("home_team") or "")
        away = (m.get("away_team") or {}).get("name") if isinstance(m.get("away_team"), dict) else (m.get("away_team") or "")
        if not (home and away):
            continue
        key = _match_key(home, away)
        evidence_entry = _fuzzy_team_match(key, available)
        if not evidence_entry:
            m.setdefault("_external_evidence", {
                "match_key":         key,
                "found":             False,
                "sources_consulted": sources_consulted,
            })
            continue
        m["_external_evidence"] = {
            "match_key":         key,
            "found":             True,
            "primary_source":    evidence_entry.get("_primary_source"),
            "corroborated_by":   evidence_entry.get("_corroborated_by") or [],
            "league":            evidence_entry.get("league"),
            "kickoff_ts":        evidence_entry.get("kickoff_ts"),
            "sources_consulted": sources_consulted,
        }
        attached += 1
    return attached


__all__ = ["rescue_basketball_day", "attach_evidence", "ALL_SCRAPERS"]
