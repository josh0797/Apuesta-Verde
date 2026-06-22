"""F94.x REFACTOR — Football odds cascade helper.

Extracted from ``_enrich_football`` (data_ingestion.py).
Strict behavioural parity: same provider order, same source labels,
same log lines, same `_odds_source` stamp on the snapshot.

Cascade (post Sprint-D9 + F84.e):
  1. TheStatsAPI primary (with line_movement / opening odds).
  2. API-Sports fallback behind the ``ENABLE_API_SPORTS_FALLBACK`` flag.
     (No-op post-F99.2 because ``api_football.py`` is a fail-closed stub,
     but kept for parity with the MLB/NBA paths.)
  3. TheStatsAPI late-retry (only if step 2 returned no odds AND step 1
     hadn't already succeeded). Mirrors the v2.5 resilience behaviour.
  4. **Sprint-D9 cascade** — ``fetch_direct_match_odds_cascade``
     (TheOddsAPI primary + OddsPortal fallback). Activated when steps
     1–3 returned no odds.  Replaces the legacy "Sportytrader" path that
     was deprecated by Bright Data blocking.
     Feature flag: ``ENABLE_D9_ODDS_CASCADE_IN_INGEST`` (default: true).
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


def _d9_cascade_enabled() -> bool:
    """Whether to attempt the Sprint-D9 odds cascade (TheOddsAPI+OddsPortal)
    when steps 1–3 of the ingestion cascade returned no odds."""
    import os
    flag = os.environ.get("ENABLE_D9_ODDS_CASCADE_IN_INGEST", "true").strip().lower()
    return flag not in {"false", "0", "no", "off"}


def _wrap_d9_result_as_api_sports_shape(
    d9_payload: dict, *, fid, home_name: Optional[str], away_name: Optional[str]
) -> tuple[Any, dict]:
    """Convert the Sprint-D9 cascade output into the API-Sports v3 odds shape
    expected by downstream consumers (``normalize_odds`` / odds_snapshots).

    The D9 output for H2H is a flat dict::

        {
            "available": True,
            "source": "the_odds_api" | "oddsportal",
            "home_odds": float, "draw_odds": float, "away_odds": float,
            "implied_probs": {...},
            "fetched_at": iso,
            "reason_codes": [...],
        }

    Downstream expects an api_sports-shaped odds_resp + normalized payload.
    We synthesize a minimal but valid bookmaker block so ``normalize_odds``
    produces a snapshot with ``available=True`` and bookmakers populated.
    """
    src = d9_payload.get("source") or "odds_cascade"
    bookmaker_name = {
        "the_odds_api": "TheOddsAPI (avg)",
        "oddsportal":   "OddsPortal (avg)",
    }.get(src, f"D9-{src}")

    h = d9_payload.get("home_odds")
    d = d9_payload.get("draw_odds")
    a = d9_payload.get("away_odds")

    # Build an api-sports v3 odds entry: list with one fixture envelope
    # containing one bookmaker that exposes a single H2H (1X2) bet.
    odds_resp = [{
        "fixture": {"id": fid},
        "bookmakers": [{
            "id":   9999,
            "name": bookmaker_name,
            "bets": [{
                "id":   1,
                "name": "Match Winner",
                "values": [
                    {"value": "Home", "odd": str(h) if h else None},
                    {"value": "Draw", "odd": str(d) if d else None},
                    {"value": "Away", "odd": str(a) if a else None},
                ],
            }],
        }],
    }]

    norm_odds = nz.normalize_odds(odds_resp) if isinstance(odds_resp, list) else {}
    if isinstance(norm_odds, dict):
        # Stamp provenance so the UI/audit can show the real source.
        norm_odds["_odds_provider"] = src
        norm_odds["_odds_cascade_used"] = "sprint_d9"
        norm_odds["_d9_reason_codes"] = d9_payload.get("reason_codes") or []
        # Expose names in case downstream uses them for hint matching.
        if home_name:
            norm_odds.setdefault("_d9_home_name_used", home_name)
        if away_name:
            norm_odds.setdefault("_d9_away_name_used", away_name)

    return odds_resp, (norm_odds or {})


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
      * ``odds_source``: one of ``thestatsapi``, ``api_sports_fallback``,
        ``thestatsapi_late``, ``odds_cascade_theoddsapi``,
        ``odds_cascade_oddsportal``, ``no_odds``.

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

    # ─── Sprint-D9-followup-2 (Jun-2026) cascade FACHADA ────────────────
    # Reemplaza el wiring D9 viejo. La fachada ``fetch_football_odds`` ya
    # contiene el cascade completo: Oddspedia → TheStatsAPI → SofaScore →
    # OddsPortal → manual.  Solo invocamos esta nueva fachada si los
    # pasos 1-3 (TheStatsAPI primary + api_sports stub + TheStatsAPI
    # late) fallaron — para no duplicar trabajo cuando TheStatsAPI ya
    # entregó cuotas.  Cuando devuelve NO_ODDS_AVAILABLE, stamps
    # ``norm_odds._odds_status = "NO_ODDS_AVAILABLE"`` para que el
    # market_trace haga el override prioritario downstream.
    if (not isinstance(norm_odds, dict) or not norm_odds.get("available")) and _d9_cascade_enabled():
        log.info(
            "[sprint-d9] fixture=%s entering D9 cascade (current source=%s)",
            fid, odds_source,
        )
        try:
            from .. import football_odds_aggregator as _agg
            d9_result = await _agg.fetch_football_odds(
                fx_raw,
                source_ids={},
                client=client,
                db=db,
            )
        except Exception as exc:
            log.debug("[sprint-d9] facade failed for %s: %s", fid, exc)
            d9_result = None

        if isinstance(d9_result, dict) and d9_result.get("available"):
            src = d9_result.get("source") or "unknown"
            mkts = d9_result.get("markets") or {}
            h2h = mkts.get("h2h") or {}
            home_odd = h2h.get("home")
            draw_odd = h2h.get("draw")
            away_odd = h2h.get("away")
            if home_odd and draw_odd and away_odd:
                # Empaquetar en shape api-sports para downstream parity.
                d9_payload = {
                    "available": True,
                    "source": src,
                    "home_odds": home_odd,
                    "draw_odds": draw_odd,
                    "away_odds": away_odd,
                    "implied_probs": {},
                    "fetched_at": d9_result.get("snapshot_at"),
                    "reason_codes": d9_result.get("reason_codes") or [],
                    "markets": mkts,
                }
                home_name = (home or {}).get("name") or ""
                away_name = (away or {}).get("name") or ""
                d9_resp, d9_norm = _wrap_d9_result_as_api_sports_shape(
                    d9_payload, fid=fid,
                    home_name=home_name, away_name=away_name,
                )
                if d9_norm.get("available"):
                    odds_resp = d9_resp
                    norm_odds = d9_norm
                    odds_source = f"odds_cascade_{src}"
                    log.info(
                        "[sprint-d9] fixture=%s odds rescued via fachada source=%s (h=%s d=%s a=%s)",
                        fid, src, home_odd, draw_odd, away_odd,
                    )
        else:
            # Fachada confirmó NO_ODDS_AVAILABLE: propagar el estado para
            # que el market_trace lo respete (no emitirá MARKET_IDENTITY_MISSING).
            log.info(
                "[sprint-d9] fixture=%s NOT rescued (home=%r away=%r league=%r) "
                "reason_codes=%s",
                fid,
                (home or {}).get("name"), (away or {}).get("name"),
                league_name,
                (d9_result or {}).get("reason_codes"),
            )
            if not isinstance(norm_odds, dict):
                norm_odds = {"available": False}
            norm_odds["_odds_status"] = "NO_ODDS_AVAILABLE"
            norm_odds["state"] = "NO_ODDS_AVAILABLE"
            norm_odds["_no_odds_reason_codes"] = (
                (d9_result or {}).get("reason_codes") or []
            )

    # Stamp source on the normalised payload for downstream auditing.
    if isinstance(norm_odds, dict):
        norm_odds["_odds_source"] = odds_source

    return (odds_resp, norm_odds, odds_source)


__all__ = ["fetch_football_odds_with_fallback"]
