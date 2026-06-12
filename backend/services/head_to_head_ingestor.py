"""Phase F67 — Head-to-Head ingestion + retrieval.

* Mongo collection ``head_to_head_matches`` holds one document per
  historical fixture between two teams. Indexed by ``(home_team_norm,
  away_team_norm)`` (symmetric, so a flipped lookup works) and TTL'd at
  365 days (a full season).
* :func:`fetch_h2h_for_match` is what the editorial engine calls — it
  reads up to 5 most-recent rows and returns them in the shape the
  ``_build_head_to_head`` sub-section expects.
* :func:`refresh_h2h_for_upcoming_fixtures` is the cron job. It walks
  the upcoming-fixtures list (the same source the analyst pipeline
  already consumes), queries API-Sports ``/fixtures?h2h={id1}-{id2}&last=5``,
  and upserts rows.

The cron is **opt-IN**: only runs when ``API_SPORTS_KEY`` is set
(``services.external_sources.api_sports`` already handles auth). Without
the key it just no-ops and the editorial 5th section keeps reporting
``INSUFFICIENT_SAMPLE``.

All public coroutines are fail-soft.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("head_to_head_ingestor")

COLLECTION    = "head_to_head_matches"
RETENTION_DAYS = 365
MAX_PER_PAIR  = 5


def _norm(name: Optional[str]) -> str:
    """Re-use the same canonicalisation as the match_id_mapping module."""
    try:
        from services.match_id_mapping import _normalise_team_name
        return _normalise_team_name(name)
    except Exception:  # noqa: BLE001
        return (name or "").strip().lower()


def _pair_key(home: str, away: str) -> str:
    """Symmetric key so 'Brazil-Morocco' and 'Morocco-Brazil' collide."""
    a, b = _norm(home), _norm(away)
    return "|".join(sorted([a, b]))


# ─────────────────────────────────────────────────────────────────────
# Retrieval — used by the editorial engine.
# ─────────────────────────────────────────────────────────────────────
async def fetch_h2h_for_match(
    db, home_team: Optional[str], away_team: Optional[str],
    *, limit: int = MAX_PER_PAIR,
) -> list[dict]:
    """Return up to ``limit`` most-recent H2H rows between two teams.

    Returns ``[]`` when no rows exist or any error occurs.
    """
    if db is None or not (home_team and away_team):
        return []
    try:
        key = _pair_key(home_team, away_team)
        docs = await db[COLLECTION].find(
            {"pair_key": key},
            {"_id": 0, "pair_key": 0, "ingested_at": 0},
        ).sort("utc_date", -1).limit(limit).to_list(length=limit)
        return docs or []
    except Exception as exc:  # noqa: BLE001
        log.debug("[H2H_FETCH_FAIL] %s vs %s: %s", home_team, away_team, exc)
        return []


# ─────────────────────────────────────────────────────────────────────
# Persistence helper.
# ─────────────────────────────────────────────────────────────────────
async def upsert_h2h_row(
    db, *, home_team: str, away_team: str,
    utc_date: str, score: Optional[str] = None,
    competition: Optional[str] = None,
    raw_payload: Optional[dict] = None,
) -> bool:
    if db is None or not (home_team and away_team and utc_date):
        return False
    try:
        key = _pair_key(home_team, away_team)
        await db[COLLECTION].update_one(
            {"pair_key": key, "utc_date": utc_date,
             "home_team": home_team, "away_team": away_team},
            {"$set": {
                "pair_key":    key,
                "home_team":   home_team,
                "away_team":   away_team,
                "utc_date":    utc_date,
                "score":       score,
                "competition": competition,
                "ingested_at": datetime.now(timezone.utc),
                "raw_payload": raw_payload or {},
            }},
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("[H2H_UPSERT_FAIL] %s vs %s @ %s: %s",
                  home_team, away_team, utc_date, exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Cron job (called from services.scheduler).
# ─────────────────────────────────────────────────────────────────────
async def refresh_h2h_for_upcoming_fixtures(db) -> dict:
    """Walk the upcoming fixtures recently analysed and refresh H2H rows.

    Source of upcoming pairs: the most recent 7 days of ``analyst_runs``,
    deduping by (home, away) pair. For each unique pair (limit 200/run)
    call API-Sports ``/fixtures?h2h=id1-id2&last=5`` and persist.

    Returns a small stats dict ``{pairs_checked, rows_written, ...}``.
    """
    started = datetime.now(timezone.utc)
    pairs_checked = 0
    rows_written  = 0
    try:
        # 1) Collect unique pairs from recent analyst runs.
        from datetime import timedelta
        cutoff = started - timedelta(days=7)
        runs = await db.analyst_runs.find(
            {"created_at": {"$gte": cutoff}},
            {"picks": 1, "summary": 1},
        ).to_list(length=200)

        unique_pairs: set[tuple[str, str]] = set()
        for run in runs or []:
            for bucket in (run.get("picks") or []):
                if isinstance(bucket, dict):
                    h = bucket.get("home_team") or (bucket.get("home") or {})
                    a = bucket.get("away_team") or (bucket.get("away") or {})
                    if isinstance(h, dict): h = h.get("name")
                    if isinstance(a, dict): a = a.get("name")
                    if h and a:
                        unique_pairs.add((h, a))
            summary = run.get("summary") or {}
            for bucket_key in ("discarded_market", "discarded_motivation",
                               "incomplete_data", "watchlist_odds_needed"):
                for e in summary.get(bucket_key) or []:
                    if isinstance(e, dict):
                        h = e.get("home_team") or (e.get("home") or {})
                        a = e.get("away_team") or (e.get("away") or {})
                        if isinstance(h, dict): h = h.get("name")
                        if isinstance(a, dict): a = a.get("name")
                        if h and a:
                            unique_pairs.add((h, a))
            if len(unique_pairs) >= 200:
                break

        # 2) For each pair, try API-Sports. The api_sports client may not
        # be present; degrade gracefully.
        try:
            from services.external_sources.api_sports_client import (  # type: ignore
                fetch_h2h as _fetch_h2h_from_apisports,
            )
        except Exception:  # noqa: BLE001
            _fetch_h2h_from_apisports = None

        for home, away in unique_pairs:
            pairs_checked += 1
            if not _fetch_h2h_from_apisports:
                continue
            try:
                rows = await _fetch_h2h_from_apisports(home, away, last=5)
            except Exception as exc:  # noqa: BLE001
                log.debug("[H2H_API_FAIL] %s vs %s: %s", home, away, exc)
                continue
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                if await upsert_h2h_row(
                    db,
                    home_team=r.get("home_team") or home,
                    away_team=r.get("away_team") or away,
                    utc_date=r.get("utc_date") or r.get("date") or "",
                    score=r.get("score") or r.get("final_score"),
                    competition=r.get("competition") or r.get("league"),
                    raw_payload=r,
                ):
                    rows_written += 1

        return {
            "ok":             True,
            "pairs_checked":  pairs_checked,
            "rows_written":   rows_written,
            "duration_s":     (datetime.now(timezone.utc) - started).total_seconds(),
            "started_at":     started.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("[H2H_REFRESH_FAIL] %s", exc)
        return {"ok": False, "_error": str(exc),
                "pairs_checked": pairs_checked, "rows_written": rows_written}


__all__ = [
    "COLLECTION", "RETENTION_DAYS", "MAX_PER_PAIR",
    "fetch_h2h_for_match", "upsert_h2h_row",
    "refresh_h2h_for_upcoming_fixtures",
    "_pair_key", "_norm",
]
