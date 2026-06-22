"""Sprint-F98 · F74 Canonical enrichment builder.

This module is the **single entry point** consumers should call to
materialise the F74 canonical ``football_data_enrichment`` block from a
match document.

Flow:
  1. Run all adapters that have raw data on the match doc:
       legacy_match_doc     (always; the bridge for current writers)
       thesportsdb          (if `_thesportsdb_raw` is attached)
       sofascore            (if `_sofascore_raw`     is attached)
       thestatsapi          (if `_thestatsapi_raw`   is attached
                              OR legacy ``_thestatsapi_enrichment``)
       statsbomb            (cache-first; if `_statsbomb_raw` cached)
       fbref                (cache-first; if `_fbref_raw` cached)
  2. Run the cascade merge with the user-binding rankings.
  3. Project the merged envelope into the F74 canonical shape so
     downstream consumers (editorial, market_selection, ...) can read
     a single stable schema.
  4. Attach the migration telemetry block:
       schema_migration = {
         "canonical_schema": "F74",
         "read_source":      "F74",
         "legacy_fallback_used": <bool>,
         "legacy_consumers_detected": [...],
       }

The function is **pure** and **fail-soft**: every step is wrapped, and
the worst-case output is a valid F74 dict with ``data_quality=THIN``
+ explicit reason codes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.adapters import (
    adapt_fbref_to_f74,
    adapt_sofascore_to_f74,
    adapt_statsbomb_to_f74,
    adapt_thesportsdb_to_f74,
    adapt_thestatsapi_to_f74,
)
from services.adapters.legacy_match_adapter import adapt_legacy_match_to_f74
from services.football_source_cascade import cascade_merge_envelopes

log = logging.getLogger(__name__)

BUILDER_SCHEMA_VERSION = "F98-BUILDER-1"


def _team_names(match: dict) -> tuple[str, str]:
    """Extract home/away names defensively."""
    if not isinstance(match, dict):
        return "", ""
    home_t = match.get("home_team") if isinstance(match.get("home_team"), dict) else {}
    away_t = match.get("away_team") if isinstance(match.get("away_team"), dict) else {}
    home_n = (home_t or {}).get("name") or match.get("home_team_name") or ""
    away_n = (away_t or {}).get("name") or match.get("away_team_name") or ""
    # Flat-string forms ("Argentina") also OK.
    if not home_n and isinstance(match.get("home_team"), str):
        home_n = match["home_team"]
    if not away_n and isinstance(match.get("away_team"), str):
        away_n = match["away_team"]
    return str(home_n), str(away_n)


def _safe_call(label: str, fn, *args, **kwargs):
    """Wrap an adapter call; never raise."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("[f74_builder] adapter %s failed: %s", label, exc)
        return None


def _project_to_f74(merged: dict, *, home_name: str, away_name: str,
                     legacy_fallback_used: bool,
                     legacy_consumers: list[str]) -> dict:
    """Project a cascade-merged envelope into the F74 canonical block.

    The shape matches the user-binding spec::

        {
          "football_data_enrichment": {
            "schema_version":  "F74",
            "available":       bool,
            "home":            {flat metrics},
            "away":            {flat metrics},
            "official_friendly_split": {},
            "h2h":             {...},
            "odds":            {...},
            "sources":         {...},
            "field_provenance":{...},
            "data_quality":    str,
            "data_completeness_score": int,
            "reason_codes":    [...],
            "schema_migration": {...}
          }
        }
    """
    if not isinstance(merged, dict):
        merged = {}
    payload = {
        "schema_version":          "F74",
        "schema_version_builder":  BUILDER_SCHEMA_VERSION,
        "available":               bool(merged.get("available", False)),
        "teams": {
            "home": {"name": home_name, "id": None},
            "away": {"name": away_name, "id": None},
        },
        "home":                    dict(merged.get("home") or {}),
        "away":                    dict(merged.get("away") or {}),
        # Reserved for F84.* lineups + standings (kept here so the
        # schema remains stable when those are filled in later).
        "official_friendly_split": {},
        "h2h":                     dict(merged.get("h2h") or {}),
        "odds":                    dict(merged.get("odds") or {}),
        "sources":                 dict(merged.get("sources") or {}),
        "field_provenance":        dict(merged.get("field_provenance") or {}),
        "sample_sizes":            dict(merged.get("sample_sizes") or {}),
        "data_quality":            merged.get("data_quality") or "THIN",
        "data_completeness_score": int(merged.get("data_completeness_score") or 0),
        "reason_codes":            list(merged.get("reason_codes") or []),
        "fetched_at":              datetime.now(timezone.utc).isoformat(),
        "schema_migration": {
            "canonical_schema":         "F74",
            "read_source":              "F74",
            "legacy_fallback_used":     bool(legacy_fallback_used),
            "legacy_consumers_detected": list(legacy_consumers or []),
        },
    }
    return payload


def build_football_data_enrichment(match: Any) -> dict:
    """Build the F74 canonical enrichment block from a live match doc.

    This is the function consumers should call. It is **pure**, has no
    IO of its own, and returns a self-describing F74 payload.

    Returns
    -------
    dict
        The F74 enrichment payload (see `_project_to_f74`).
    """
    if not isinstance(match, dict):
        return _project_to_f74({}, home_name="", away_name="",
                                  legacy_fallback_used=False,
                                  legacy_consumers=[])

    home_name, away_name = _team_names(match)
    envelopes: list[dict] = []
    legacy_consumers: list[str] = []

    # 1) Legacy bridge — ALWAYS run so we don't regress.
    legacy_env = _safe_call("legacy_match_doc", adapt_legacy_match_to_f74, match)
    if isinstance(legacy_env, dict):
        envelopes.append(legacy_env)
        if legacy_env.get("available"):
            legacy_consumers.append("legacy_match_doc")

    # 2) Per-source raw payloads (when attached by data_ingestion or
    #    by upstream enrichment steps).
    raw_pairs = [
        ("thesportsdb", match.get("_thesportsdb_raw"),
         lambda r: adapt_thesportsdb_to_f74(r)),
        ("sofascore",   match.get("_sofascore_raw"),
         lambda r: adapt_sofascore_to_f74(r, home_team=home_name, away_team=away_name)),
        ("thestatsapi", match.get("_thestatsapi_raw") or match.get("_thestatsapi_enrichment"),
         lambda r: adapt_thestatsapi_to_f74(r)),
        ("statsbomb",   match.get("_statsbomb_raw"),
         lambda r: adapt_statsbomb_to_f74(r)),
        ("fbref",       match.get("_fbref_raw"),
         lambda r: adapt_fbref_to_f74(r)),
    ]
    # F99.1 — corners offline_seed / seed_partial. The same raw payload
    # feeds two adapters that classify sides as full vs partial via
    # sample_size & underlying_source (NOT separate collections).
    _corners_seed_raw = match.get("_corners_offline_seed_raw")
    if _corners_seed_raw is not None:
        try:
            from services.adapters.offline_seed_corners_adapter import (
                adapt_offline_seed_corners_to_f74,
                adapt_seed_partial_corners_to_f74,
            )
            raw_pairs.append((
                "offline_seed", _corners_seed_raw,
                lambda r: adapt_offline_seed_corners_to_f74(
                    r, home_team=home_name, away_team=away_name,
                ),
            ))
            raw_pairs.append((
                "seed_partial", _corners_seed_raw,
                lambda r: adapt_seed_partial_corners_to_f74(
                    r, home_team=home_name, away_team=away_name,
                ),
            ))
        except Exception as exc:  # noqa: BLE001
            log.debug("[f74_builder] corners seed adapters unavailable: %s", exc)

    # F99.4 — recent-form consolidated envelope. Pure adapter; only added
    # when the hydrator has attached the consolidated payload.
    _recent_form_raw = match.get("_recent_form_consolidated_raw")
    if _recent_form_raw is not None:
        try:
            from services.adapters.recent_form_consolidated_adapter import (
                adapt_recent_form_to_f74,
            )
            raw_pairs.append((
                "recent_form_consolidated", _recent_form_raw,
                lambda r: adapt_recent_form_to_f74(
                    r, home_team=home_name, away_team=away_name,
                ),
            ))
        except Exception as exc:  # noqa: BLE001
            log.debug("[f74_builder] recent_form adapter unavailable: %s", exc)

    for label, raw, runner in raw_pairs:
        if raw is None:
            continue
        env = _safe_call(label, runner, raw)
        if isinstance(env, dict):
            envelopes.append(env)

    # 3) Cascade-merge — append legacy_match_doc as the LAST fallback
    # for every ranking. This preserves the user-binding spec (external
    # providers always win when present) while ensuring the bridge
    # adapter doesn't get silently ignored.
    try:
        from services.football_source_cascade import DEFAULT_RANKINGS
        legacy_fallback_override = {
            metric: list(rank) + ["legacy_match_doc"]
            for metric, rank in DEFAULT_RANKINGS.items()
            if "legacy_match_doc" not in rank
        }
        merged = cascade_merge_envelopes(
            envelopes,
            rankings_override=legacy_fallback_override,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("[f74_builder] cascade merge crashed: %s", exc)
        merged = {}

    legacy_fallback_used = (
        # If only legacy_match_doc supplied data (no external sources),
        # we are operating in legacy-fallback mode.
        len([e for e in envelopes
             if e.get("source") not in ("legacy_match_doc",)
             and e.get("available")]) == 0
        and any(e.get("source") == "legacy_match_doc" and e.get("available")
                for e in envelopes)
    )
    return _project_to_f74(
        merged,
        home_name=home_name,
        away_name=away_name,
        legacy_fallback_used=legacy_fallback_used,
        legacy_consumers=legacy_consumers,
    )


def read_f74_field(enrichment: Any,
                    side: str,
                    metric: str,
                    *,
                    default: Any = None) -> Any:
    """Tiny helper consumers can use to look up a single metric.

    Returns ``default`` when the enrichment is missing the field.
    """
    if not isinstance(enrichment, dict):
        return default
    sec = enrichment.get(side)
    if not isinstance(sec, dict):
        return default
    v = sec.get(metric)
    return default if v is None else v


__all__ = [
    "BUILDER_SCHEMA_VERSION",
    "build_football_data_enrichment",
    "read_f74_field",
]
