"""F94.x REFACTOR — Football odds cascade helper.

Extracted from ``_enrich_football`` (data_ingestion.py).
Strict behavioural parity: same provider order, same source labels,
same log lines, same `_odds_source` stamp on the snapshot.

Cascade (per F84.e):
  1. TheStatsAPI primary (with line_movement / opening odds).
  2. API-Sports fallback behind the ``ENABLE_API_SPORTS_FALLBACK`` flag.
  3. TheStatsAPI late-retry (only if step 2 returned no odds AND step 1
     hadn't already succeeded). Mirrors the v2.5 resilience behaviour.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import httpx

from .. import api_football as af
from .. import normalizer as nz
from ..external_sources import thestatsapi_odds_adapter as _ts_odds

log = logging.getLogger("services.data_ingestion")  # keep parity with caller


def _api_sports_fallback_enabled() -> bool:
    """Mirror of the local helper in data_ingestion.py (kept consistent)."""
    import os
    flag = os.environ.get("ENABLE_API_SPORTS_FALLBACK", "true").strip().lower()
    return flag not in {"false", "0", "no", "off"}


async def fetch_football_odds_with_fallback(
    client: httpx.AsyncClient,
    db,
    fx_raw: dict,
    *,
    fid,
    home: dict,
    away: dict,
    kickoff: Any,
    league_name: Optional[str],
) -> Tuple[Any, dict, str]:
    """Run the odds cascade for a single football fixture.

    Returns ``(odds_resp, norm_odds, odds_source)``:
      * ``odds_resp``: raw API-Sports v3 shape (or TheStatsAPI shape
        already adapted to v3) consumed downstream.
      * ``norm_odds``: ``normalize_odds`` output. Will have
        ``_odds_source`` stamped on it for downstream auditing.
      * ``odds_source``: one of ``thestatsapi``,
        ``api_sports_fallback``, ``thestatsapi_late``, ``no_odds``.

    Never raises (fail-soft).
    """
    odds_source = "no_odds"
    odds_resp: Any = []
    norm_odds: dict = {}

    try:
        ts_shape, ts_norm, ts_mid = await _ts_odds.fetch_odds_api_sports_shape(
            client, fx_raw,
            home_name=home.get("name"), away_name=away.get("name"),
            kickoff=kickoff, league_name=league_name,
        )
    except Exception as exc:
        log.debug("[F84.e] thestatsapi odds adapter failed for %s: %s", fid, exc)
        ts_shape, ts_norm, ts_mid = None, None, None

    if ts_norm and ts_norm.get("available"):
        odds_resp   = ts_shape
        norm_odds   = ts_norm
        odds_source = "thestatsapi"
        log.info(
            "[F84.e] fixture=%s odds primary=TheStatsAPI ts_mid=%s bookmakers=%d",
            fid, ts_mid, len(norm_odds.get("bookmakers") or []),
        )
    elif _api_sports_fallback_enabled():
        try:
            odds_resp = await af.odds_for_fixture(client, fid, db=db)
        except Exception as e:
            log.warning("odds failed for %s: %s", fid, e)
            odds_resp = []
        norm_odds = nz.normalize_odds(odds_resp)
        if norm_odds.get("available"):
            odds_source = "api_sports_fallback"
        else:
            odds_source = "no_odds"
            # Last-resort: TheStatsAPI late retry (resilience).
            if not ts_norm:
                try:
                    ts2_shape, ts2_norm, _ = await _ts_odds.fetch_odds_api_sports_shape(
                        client, fx_raw,
                        home_name=home.get("name"), away_name=away.get("name"),
                        kickoff=kickoff, league_name=league_name,
                    )
                    if ts2_norm and ts2_norm.get("available"):
                        odds_resp = ts2_shape
                        norm_odds = ts2_norm
                        odds_source = "thestatsapi_late"
                except Exception as exc2:
                    log.debug("[F84.e] thestatsapi late retry failed for %s: %s",
                              fid, exc2)
    else:
        # Fallback disabled in TheStatsAPI-only mode.
        odds_source = "no_odds"
        norm_odds = {"available": False}

    # Stamp source on the normalised payload for downstream auditing.
    if isinstance(norm_odds, dict):
        norm_odds["_odds_source"] = odds_source

    return (odds_resp, norm_odds, odds_source)


__all__ = ["fetch_football_odds_with_fallback"]
