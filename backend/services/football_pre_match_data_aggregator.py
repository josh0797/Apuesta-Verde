"""Sprint-B · B1.c — Football Pre-Match Data Aggregator
========================================================

Fetches and merges pre-match data from a **cascade** of providers,
producing inputs ready for the learning-snapshot pre_match_inputs
block plus a source-audit trail.

**Provider order (user-confirmed):**
  1. **TheStatsAPI**     — primary (richer xG + corners)
  2. **API-Sports**      — secondary fallback
  3. **Scrape.do**       — last-resort public scraping (365scores, ESPN)

Design
------
* Each adapter is a callable returning ``(data: dict, status: str)``
  where status ∈ {COMPLETE, PARTIAL, FAILED}.
* The aggregator iterates adapters in priority order. Missing keys
  trigger the next adapter. Adapters never raise.
* The final ``data`` is the **merged** dict (later adapters fill
  missing keys, never overwrite filled ones).
* The audit trail captures every adapter that ran (success or fail).

This module is the **wiring layer**. The actual HTTP fetching lives
in existing clients (``thestatsapi_shotmap_client``, ``api_football``,
etc.). Sprint-B keeps the adapters as thin pure wrappers so they're
fully testable with monkey-patched fetch functions.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from .football_learning_snapshot_schema import (
    SCRAPE_COMPLETE,
    SCRAPE_PARTIAL,
    SCRAPE_FAILED,
    stamp_source_audit_entry,
    build_empty_pre_match_inputs,
    build_empty_source_audit,
)

log = logging.getLogger("football_pre_match_data_aggregator")

SRC_THESTATSAPI       = "thestatsapi"
SRC_API_SPORTS        = "api_sports"
SRC_SCRAPE_DO         = "scrape_do"
SRC_CONCACAF_CAF_HYDR = "concacaf_caf_hydrator"

# Keys aggregator considers "core" — if any of these is still missing
# after all adapters ran, the aggregator status drops to PARTIAL.
_CORE_FIELDS = (
    "home_xg_l5", "away_xg_l5",
    "home_corners_l5", "away_corners_l5",
    "btts_probability", "over25_probability",
)


# Type alias for an adapter:
#   async def adapter(home_team, away_team, **ctx) -> (data_dict, status_str)
AdapterFn = Callable[..., Awaitable[tuple[dict, str]]]


def _merge_inputs(target: dict, incoming: dict) -> list[str]:
    """Fold ``incoming`` into ``target`` without overwriting non-None
    keys. Returns the list of keys that the incoming actually filled."""
    if not isinstance(incoming, dict):
        return []
    filled: list[str] = []
    for k, v in incoming.items():
        if v is None:
            continue
        if k == "market_odds" and isinstance(v, dict):
            cur = target.get("market_odds") or {}
            for ok, ov in v.items():
                if ov is None or ok not in cur:
                    continue
                if cur.get(ok) is None:
                    cur[ok] = ov
                    filled.append(f"market_odds.{ok}")
            target["market_odds"] = cur
        elif k in target and target.get(k) is None:
            target[k] = v
            filled.append(k)
    return filled


def _is_complete(target: dict) -> bool:
    return all(target.get(k) is not None for k in _CORE_FIELDS)


async def gather_pre_match_data(
    *,
    home_team: str,
    away_team: str,
    match_id: int | str,
    adapters: Optional[list[tuple[str, AdapterFn]]] = None,
    context: Optional[dict] = None,
) -> dict:
    """Run the cascade and return ``{inputs, source_audit, status}``.

    Parameters
    ----------
    home_team, away_team, match_id
        Identifiers used by the adapters.
    adapters
        Optional override of the adapter chain. **Tests use this** to
        inject fakes. When ``None``, the default production cascade is
        loaded lazily (TheStatsAPI → API-Sports → Scrape.do).
    context
        Arbitrary key-value pairs forwarded to each adapter (db, hints).
    """
    context = dict(context or {})
    inputs = build_empty_pre_match_inputs()
    audit  = build_empty_source_audit()

    if adapters is None:
        adapters = _default_adapter_chain()

    for source_name, adapter in adapters:
        if _is_complete(inputs):
            break  # short-circuit when core fields are all populated
        try:
            data, status = await adapter(
                home_team=home_team, away_team=away_team,
                match_id=match_id, **context,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[%s] adapter raised: %s", source_name, exc)
            stamp_source_audit_entry(
                audit, bucket="pre_match_sources",
                source=source_name, status=SCRAPE_FAILED, error=str(exc),
            )
            continue
        filled = _merge_inputs(inputs, data)
        stamp_source_audit_entry(
            audit, bucket="pre_match_sources",
            source=source_name, status=status,
            fields_filled=filled,
        )

    # Final aggregator status: COMPLETE iff all core fields filled.
    audit["scrape_status"] = (
        SCRAPE_COMPLETE if _is_complete(inputs) else SCRAPE_PARTIAL
    )
    return {
        "inputs":       inputs,
        "source_audit": audit,
        "status":       audit["scrape_status"],
    }


# ─────────────────────────────────────────────────────────────────────
# Default production adapters — imported lazily so tests don't pay
# the import cost.
# ─────────────────────────────────────────────────────────────────────
async def _adapter_thestatsapi(home_team, away_team, match_id, **ctx):
    """Thin wrapper around the real TheStatsAPI pre-match summary
    fetcher (Sprint-B Fix 2). Returns ``(data, status)``.
    """
    try:
        from .external_sources.thestatsapi_pre_match_summary import (
            fetch_match_pre_match_summary,
        )
    except Exception:
        return {}, SCRAPE_FAILED
    try:
        raw = await fetch_match_pre_match_summary(
            home_team=home_team, away_team=away_team, match_id=match_id,
            home_team_id=ctx.get("home_team_id"),
            away_team_id=ctx.get("away_team_id"),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("thestatsapi adapter failed: %s", exc)
        return {}, SCRAPE_FAILED
    if not isinstance(raw, dict) or not raw:
        return {}, SCRAPE_FAILED
    # Determine status from how many core fields were populated.
    has_xg     = (raw.get("home_xg_l5") is not None
                   and raw.get("away_xg_l5") is not None)
    has_corners = (raw.get("home_corners_l5") is not None
                    and raw.get("away_corners_l5") is not None)
    status = (SCRAPE_COMPLETE if (has_xg and has_corners) else SCRAPE_PARTIAL)
    return raw, status


async def _adapter_api_sports(home_team, away_team, match_id, **ctx):
    """Wrapper around api_football client for goal/corner statistics."""
    try:
        from . import api_football  # type: ignore
    except Exception:
        return {}, SCRAPE_FAILED
    try:
        # Best-effort — the actual API-Sports client exposes many calls.
        # Sprint-B uses a placeholder; the real wiring is added when
        # the credentials/endpoints are confirmed end-to-end.
        get_fn = getattr(api_football, "get_pre_match_summary", None)
        if not callable(get_fn):
            return {}, SCRAPE_FAILED
        raw = await get_fn(match_id=match_id)  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        log.debug("api_sports adapter failed: %s", exc)
        return {}, SCRAPE_FAILED
    if not isinstance(raw, dict):
        return {}, SCRAPE_FAILED
    return {
        "home_xg_l5":       raw.get("home_xg_l5"),
        "away_xg_l5":       raw.get("away_xg_l5"),
        "home_corners_l5":  raw.get("home_corners_l5"),
        "away_corners_l5":  raw.get("away_corners_l5"),
        "home_corners_l15": raw.get("home_corners_l15"),
        "away_corners_l15": raw.get("away_corners_l15"),
        "btts_probability":   raw.get("btts_probability"),
        "over25_probability": raw.get("over25_probability"),
        "draw_probability":   raw.get("draw_probability"),
        "market_odds":        raw.get("market_odds") or {},
    }, SCRAPE_PARTIAL  # api_sports rarely returns all fields


async def _adapter_scrape_do(home_team, away_team, match_id, **ctx):
    """Last-resort public scraping (365scores / ESPN via Scrape.do).

    Sprint-B leaves this stubbed because it requires per-source
    HTML/JSON parsers; B2's corners learning loop will validate the
    cascade end-to-end with the existing scrape.do client.
    """
    return {}, SCRAPE_FAILED


def _default_adapter_chain() -> list[tuple[str, AdapterFn]]:
    """Returns the production cascade. Test code can override by
    passing a custom ``adapters`` list to ``gather_pre_match_data``.

    Fix-3 (Sprint-B) appends the CONCACAF/CAF qualifier-proxy adapter
    as a LAST resort so WC debutants (Cabo Verde, Curazao, Jordan)
    receive at least confederation-median priors when every upstream
    source has failed.
    """
    # Lazy import to keep tests cheap.
    try:
        from .football_concacaf_caf_hydrator import (
            adapter_concacaf_caf_hydrator,
        )
    except Exception:
        adapter_concacaf_caf_hydrator = None  # type: ignore[assignment]
    chain: list[tuple[str, AdapterFn]] = [
        (SRC_THESTATSAPI, _adapter_thestatsapi),
        (SRC_API_SPORTS,  _adapter_api_sports),
        (SRC_SCRAPE_DO,   _adapter_scrape_do),
    ]
    if adapter_concacaf_caf_hydrator is not None:
        chain.append((SRC_CONCACAF_CAF_HYDR, adapter_concacaf_caf_hydrator))
    return chain


__all__ = [
    "SRC_THESTATSAPI", "SRC_API_SPORTS", "SRC_SCRAPE_DO",
    "gather_pre_match_data",
    "_merge_inputs",            # exported for tests
    "_is_complete",             # exported for tests
    "_default_adapter_chain",   # exported for tests
]
