"""Sprint-F98.1 · Seed national-team xG + corners collections.

Populate ``football_team_xg_offline_seed`` and
``football_team_corners_offline_seed`` for the top international
national teams using TheSportsDB ``eventslast.php`` as the data source.

The script is **idempotent** — running it twice does not duplicate
documents (it upserts by ``team_norm``).

Usage
-----
    # In-process programmatic call (used by tests and scripts):
    >>> from services.football_national_team_seed import (
    ...     seed_national_team_recent_form,
    ...     TOP_NATIONAL_TEAMS,
    ... )
    >>> import asyncio
    >>> asyncio.run(seed_national_team_recent_form(db=db, client=client))

    # CLI entry-point (background job):
    $ python -m services.football_national_team_seed \
        --teams Argentina,Uruguay,New\\ Zealand,Egypt

Design choices
--------------
* **Pure data layer** — no HTTP calls beyond TheSportsDB (already used
  upstream). No new dependencies.
* **Fail-soft** — every team is independent; a single 404 / timeout
  does NOT abort the batch.
* **Cache-friendly** — the upsert uses ``team_norm`` so subsequent
  hydration runs (via ``data_ingestion.py``) read this seed without
  another HTTP round-trip.
* **Auditable** — every doc records ``seeded_at``, ``source``, and a
  ``reason_codes`` list explaining the seed path.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

SEED_SCHEMA_VERSION = "F98.1-SEED-1"
COLLECTION_XG      = "football_team_xg_offline_seed"
COLLECTION_CORNERS = "football_team_corners_offline_seed"
COLLECTION_RECENT  = "football_team_recent_fixtures_seed"

# Top FIFA national teams (we seed Soccer-only). The list intentionally
# covers the 32 World Cup qualifiers PLUS top-tier UEFA/CONMEBOL
# selections so the F98 builder has hydration coverage for every match
# the discovery cascade is likely to surface.
TOP_NATIONAL_TEAMS: list[str] = [
    # CONMEBOL
    "Argentina", "Brazil", "Uruguay", "Colombia", "Ecuador", "Peru",
    "Chile", "Paraguay", "Bolivia", "Venezuela",
    # CONCACAF
    "USA", "Mexico", "Canada", "Costa Rica", "Panama", "Jamaica",
    "Honduras", "Curacao", "El Salvador",
    # CONMEBOL/CAF stragglers + World Cup hosts
    "Cape Verde", "Algeria", "Senegal", "Morocco", "Tunisia", "Egypt",
    "Nigeria", "Cameroon", "Ghana", "South Africa", "Ivory Coast",
    # UEFA
    "France", "Spain", "Germany", "Italy", "England", "Netherlands",
    "Belgium", "Portugal", "Croatia", "Denmark", "Sweden", "Norway",
    "Poland", "Czechia", "Austria", "Switzerland", "Serbia",
    "Bosnia & Herzegovina", "Scotland", "Wales", "Ireland", "Iceland",
    "Turkey", "Greece", "Ukraine",
    # AFC
    "Japan", "South Korea", "Saudi Arabia", "Australia", "Iran",
    "Iraq", "Jordan", "Uzbekistan", "Qatar", "UAE",
    # OFC
    "New Zealand",
]


def _normalize_team_name(name: str) -> str:
    """Lowercase + strip + collapse whitespace. Matches the
    ``team_norm`` column already used by the legacy seed scripts."""
    import unicodedata as _u
    s = "".join(c for c in _u.normalize("NFKD", str(name))
                 if not _u.combining(c))
    return " ".join(s.lower().strip().split())


async def _resolve_national_team_id(name: str, *, client: Any) -> Optional[str]:
    """Resolve a national-team idTeam via TheSportsDB ``search_teams``,
    preferring Soccer + WC/Nations League leagues.

    Filters OUT youth and women's teams (``U17``/``U20``/``U23``/``Women``)
    so a search for ``"Argentina"`` returns the senior national team
    instead of, say, ``"Argentina U20"``.
    """
    from services.external_sources import thesportsdb_client as _tsdb
    if not _tsdb.is_enabled() or not name:
        return None
    try:
        candidates = await _tsdb.search_teams(name, client=client)
    except Exception:
        return None
    soccer = [c for c in candidates or []
               if str(c.get("strSport") or "").lower() == "soccer"]
    if not soccer:
        return None
    # Exclude youth + women teams (the senior team always shares the
    # search prefix but never carries those suffixes).
    exclude_kws = (" u17", " u20", " u23", " women", " youth")
    senior = [
        c for c in soccer
        if not any(kw in (" " + str(c.get("strTeam") or "").lower())
                    for kw in exclude_kws)
    ]
    pool = senior or soccer
    national_kws = ("world cup", "nations league", "national", "qualifiers")
    preferred = [
        c for c in pool
        if any(kw in str(c.get("strLeague") or "").lower()
                for kw in national_kws)
    ]
    pick = (preferred or pool)[0]
    return str(pick.get("idTeam") or "") or None


def _aggregate_recent(events: list[dict], *, perspective_team_id: str) -> dict:
    """Compute xG and corner aggregates from a list of recent events.

    TheSportsDB ``eventslast`` does NOT expose corners or xG directly
    — we therefore only produce the goals-based aggregates here,
    plus the raw fixture list which downstream adapters consume.
    """
    if not events:
        return {"matches_count": 0, "matches": []}
    matches_out: list[dict] = []
    goals_for: list[int] = []
    goals_ag:  list[int] = []
    for raw in events:
        home_id = str((raw.get("teams") or {}).get("home", {}).get("id") or "")
        away_id = str((raw.get("teams") or {}).get("away", {}).get("id") or "")
        is_home = perspective_team_id == home_id
        try:
            h_g = int((raw.get("goals") or {}).get("home"))
            a_g = int((raw.get("goals") or {}).get("away"))
        except (TypeError, ValueError):
            continue
        scored, conceded = (h_g, a_g) if is_home else (a_g, h_g)
        goals_for.append(scored)
        goals_ag.append(conceded)
        matches_out.append({
            "fixture_id":    (raw.get("fixture") or {}).get("id"),
            "date":          (raw.get("fixture") or {}).get("date"),
            "league":        (raw.get("league")  or {}).get("name"),
            "team_scored":   scored,
            "team_conceded": conceded,
            "is_home":       is_home,
        })
    return {
        "matches_count":     len(matches_out),
        "matches":           matches_out,
        "goals_scored_avg":  (sum(goals_for) / len(goals_for)) if goals_for else None,
        "goals_conceded_avg":(sum(goals_ag) / len(goals_ag))   if goals_ag   else None,
    }


async def seed_one_national_team(
    name: str,
    *,
    db: Any,
    client: Any = None,
    skip_if_recent_hours: float = 12.0,
) -> dict:
    """Seed a single national team. Returns an audit dict."""
    from services.external_sources import thesportsdb_client as _tsdb
    if db is None:
        return {"team": name, "status": "error", "reason_codes": ["DB_REQUIRED"]}
    team_norm = _normalize_team_name(name)
    now = datetime.now(timezone.utc)
    audit: dict = {
        "team":          name,
        "team_norm":     team_norm,
        "started_at":    now.isoformat(),
        "reason_codes":  [],
    }

    # Skip-if-recent guard (idempotent freshness gate).
    if skip_if_recent_hours > 0:
        existing = await db[COLLECTION_RECENT].find_one(
            {"team_norm": team_norm},
        )
        if existing:
            seeded_at = existing.get("seeded_at")
            if isinstance(seeded_at, str):
                try:
                    seeded_at_dt = datetime.fromisoformat(seeded_at.rstrip("Z"))
                    if seeded_at_dt.tzinfo is None:
                        seeded_at_dt = seeded_at_dt.replace(tzinfo=timezone.utc)
                    age_h = (now - seeded_at_dt).total_seconds() / 3600
                    if age_h < skip_if_recent_hours:
                        audit["status"] = "skipped_fresh"
                        audit["reason_codes"].append("SKIPPED_FRESH_SEED")
                        audit["age_hours"] = age_h
                        return audit
                except Exception:
                    pass

    # 1) Resolve idTeam
    team_id = await _resolve_national_team_id(name, client=client)
    if not team_id:
        audit["status"] = "error"
        audit["reason_codes"].append("THESPORTSDB_TEAM_NOT_FOUND")
        return audit
    audit["thesportsdb_team_id"] = team_id

    # 2) Fetch last events
    events = await _tsdb.fetch_last_events_by_team(team_id, n=5, client=client)
    if not events:
        audit["status"] = "error"
        audit["reason_codes"].append("THESPORTSDB_NO_EVENTS")
        return audit
    audit["events_count"] = len(events)

    # 3) Aggregate
    agg = _aggregate_recent(events, perspective_team_id=team_id)

    # 4) Upsert into the recent_fixtures seed collection (the one our
    #    adapters consume). The xG / corners collections are NOT
    #    populated here because TheSportsDB doesn't carry those signals
    #    — they remain populated by their dedicated SofaScore /
    #    Understat pipelines.
    seed_doc = {
        "schema_version":   SEED_SCHEMA_VERSION,
        "team_name":        name,
        "team_norm":        team_norm,
        "thesportsdb_team_id": team_id,
        "matches_count":    agg["matches_count"],
        "matches":          agg["matches"],
        "goals_scored_avg": agg.get("goals_scored_avg"),
        "goals_conceded_avg": agg.get("goals_conceded_avg"),
        "events_raw":       events,
        "seeded_at":        now.isoformat(),
        "source":           "thesportsdb.eventslast",
        "reason_codes":     ["SEED_OK"],
    }
    await db[COLLECTION_RECENT].update_one(
        {"team_norm": team_norm},
        {"$set": seed_doc},
        upsert=True,
    )
    audit["status"]        = "ok"
    audit["matches_count"] = agg["matches_count"]
    audit["reason_codes"].append("SEED_PERSISTED")
    return audit


async def seed_national_team_recent_form(
    *,
    db: Any,
    client: Any = None,
    teams: Optional[list[str]] = None,
    concurrency: int = 4,
    skip_if_recent_hours: float = 12.0,
) -> dict:
    """Seed many national teams concurrently (bounded).

    Returns a batch audit dict::

        {
          "seeded": int,
          "skipped": int,
          "errors": int,
          "per_team": [audit_dict, ...],
        }
    """
    teams = list(teams or TOP_NATIONAL_TEAMS)
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _run(t: str) -> dict:
        async with sem:
            try:
                return await seed_one_national_team(
                    t, db=db, client=client,
                    skip_if_recent_hours=skip_if_recent_hours,
                )
            except Exception as exc:  # noqa: BLE001
                return {"team": t, "status": "error",
                          "reason_codes": [f"EXCEPTION:{type(exc).__name__}"]}

    results = await asyncio.gather(*[_run(t) for t in teams])
    seeded  = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped_fresh")
    errors  = sum(1 for r in results if r.get("status") == "error")
    return {
        "seeded":   seeded,
        "skipped":  skipped,
        "errors":   errors,
        "per_team": results,
        "schema_version": SEED_SCHEMA_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────
async def _cli_main(team_csv: Optional[str] = None) -> None:
    """Background-safe CLI: ``python -m services.football_national_team_seed``"""
    from dotenv import load_dotenv
    load_dotenv()
    from motor.motor_asyncio import AsyncIOMotorClient
    import httpx
    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME", "test_database")
    if not mongo_url:
        print("MONGO_URL is required")
        return
    teams = None
    if team_csv:
        teams = [t.strip() for t in team_csv.split(",") if t.strip()]
    client_db = AsyncIOMotorClient(mongo_url)
    db        = client_db[db_name]
    async with httpx.AsyncClient(timeout=12.0) as http_client:
        out = await seed_national_team_recent_form(
            db=db, client=http_client, teams=teams,
        )
    print(f"seeded={out['seeded']}  skipped={out['skipped']}  errors={out['errors']}")
    for r in out["per_team"]:
        if r.get("status") != "ok":
            print(f"  - {r.get('team')!r:20} status={r.get('status')!s:8} "
                  f"reasons={r.get('reason_codes')}")


__all__ = [
    "SEED_SCHEMA_VERSION",
    "COLLECTION_XG", "COLLECTION_CORNERS", "COLLECTION_RECENT",
    "TOP_NATIONAL_TEAMS",
    "seed_one_national_team",
    "seed_national_team_recent_form",
    "_aggregate_recent",
    "_normalize_team_name",
    "_resolve_national_team_id",
]


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(_cli_main(team_csv=arg))
