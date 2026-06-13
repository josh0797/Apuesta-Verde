"""Phase F84.e — TheStatsAPI odds adapter (primary-source promotion).

Tiny façade around the existing TheStatsAPI odds + normaliser plumbing
so the orchestrator (``data_ingestion._enrich_football``) can flip its
priority order — TheStatsAPI primaria, API-Sports fallback — with one
function call.

Returns a 3-tuple ``(odds_resp, norm_odds, resolved_match_id)`` where:
  * ``odds_resp`` is the API-Sports-shaped odds payload (list of one
    fixture dict) so downstream normalisers can keep their existing
    code path.
  * ``norm_odds`` is the already-normalised dict produced by
    :func:`services.normalizer.normalize_odds` with ``_opening_odds``
    preserved (so ``odds_value_engine`` keeps computing line
    movement).
  * ``resolved_match_id`` is the TheStatsAPI match id that served the
    response (audit aid).

All three values are ``None`` on miss. Never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from . import thestatsapi_client as _ts
from . import thestatsapi_normalizer as _ts_norm
from .. import normalizer as nz

log = logging.getLogger("thestatsapi_odds_adapter")

RC_DISABLED        = "RC_THESTATSAPI_DISABLED"
RC_NO_MATCH_ID     = "RC_THESTATSAPI_NO_MATCH_ID"
RC_EMPTY_RESPONSE  = "RC_THESTATSAPI_EMPTY_ODDS"
RC_NORMALISE_FAIL  = "RC_THESTATSAPI_NORMALISE_FAIL"
RC_OK              = "RC_OK"


async def _resolve_match_id(
    client: httpx.AsyncClient,
    fx_raw: dict,
    *,
    home_name: Optional[str],
    away_name: Optional[str],
    kickoff: Any,
    league_name: Optional[str],
) -> Optional[str]:
    """Use the cached raw id when the fixture itself came from TheStatsAPI;
    otherwise resolve by names + date + competition."""
    raw_id = fx_raw.get("_thestatsapi_raw_id")
    if not raw_id and fx_raw.get("_external_source") == "thestatsapi":
        raw_id = fx_raw.get("_external_source_id")
    if raw_id:
        return raw_id
    if not home_name or not away_name:
        return None
    try:
        return await _ts.resolve_thestatsapi_match_id_by_names(
            client, home=home_name, away=away_name,
            date=kickoff, competition=league_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[odds_adapter] match-id resolve failed: %s", exc)
        return None


async def fetch_odds_api_sports_shape(
    client: httpx.AsyncClient,
    fx_raw: dict,
    *,
    home_name: Optional[str] = None,
    away_name: Optional[str] = None,
    kickoff: Any = None,
    league_name: Optional[str] = None,
) -> tuple[Optional[list], Optional[dict], Optional[str]]:
    """Run the full TheStatsAPI odds → API-Sports-shape → normalize
    pipeline. Returns ``(None, None, None)`` on miss."""
    if not _ts.is_enabled():
        log.debug("[%s]", RC_DISABLED)
        return None, None, None

    match_id = await _resolve_match_id(
        client, fx_raw,
        home_name=home_name, away_name=away_name,
        kickoff=kickoff, league_name=league_name,
    )
    if not match_id:
        log.debug("[%s]", RC_NO_MATCH_ID)
        return None, None, None

    try:
        ts_data = await _ts.odds_for_fixture(client, match_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("[odds_adapter] thestatsapi fetch failed: %s", exc)
        return None, None, match_id
    if not ts_data:
        log.debug("[%s] match_id=%s", RC_EMPTY_RESPONSE, match_id)
        return None, None, match_id

    try:
        api_sports_shape = (
            _ts_norm.normalize_thestatsapi_odds_to_apisports_shape(ts_data)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] match_id=%s err=%s", RC_NORMALISE_FAIL, match_id, exc)
        return None, None, match_id
    if not api_sports_shape:
        return None, None, match_id

    try:
        norm = nz.normalize_odds(api_sports_shape)
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] normalize_odds crashed: %s", RC_NORMALISE_FAIL, exc)
        return None, None, match_id

    if not isinstance(norm, dict) or not norm.get("available"):
        return None, None, match_id

    # Preserve opening odds so the value engine can compute line movement.
    try:
        norm["_opening_odds"] = (
            api_sports_shape[0].get("_opening_odds") or {}
        )
    except Exception:  # noqa: BLE001
        norm.setdefault("_opening_odds", {})

    log.debug("[%s] match_id=%s bookmakers=%d",
              RC_OK, match_id, len(norm.get("bookmakers") or []))
    return api_sports_shape, norm, match_id


__all__ = [
    "fetch_odds_api_sports_shape",
    "RC_DISABLED", "RC_NO_MATCH_ID", "RC_EMPTY_RESPONSE",
    "RC_NORMALISE_FAIL", "RC_OK",
]
