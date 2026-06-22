"""Sprint-F98 · FBref → F74 envelope adapter (pure).

FBref is **cache-first / background-only** per our policy. This
adapter only converts already-fetched FBref payloads (from
``services.external_sources.fbref_client`` or the warehouse) into the
F98 envelope. It does NOT trigger IO.

Expected raw shape (canonical wrapper)::

    raw = {
        "match_id":       "abc",
        "home_stats":     {"xg_for_l5": float, "xg_against_l5": float,
                            "shots_for_l5": ..., "possession_avg_l5": ...,
                            "sample": 5},
        "away_stats":     {...},
    }
"""
from __future__ import annotations

from typing import Any

from services.adapters._envelope import (
    RC_MAPPING_OK,
    RC_MAPPING_PARTIAL,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    RC_SAMPLE_TOO_SMALL,
    _safe_float,
    _safe_int,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE = "fbref"

_SUPPORTED_KEYS = (
    "xg_for_l5", "xg_against_l5",
    "shots_for_l5", "shots_on_target_l5",
    "possession_avg_l5",
    "goals_scored_l5", "goals_conceded_l5",
)


def _from_stats(env: dict, side: str, blob: Any, sample_size: int) -> bool:
    if not isinstance(blob, dict):
        return False
    wrote_any = False
    for k in _SUPPORTED_KEYS:
        v = _safe_float(blob.get(k))
        if v is None:
            continue
        set_field(env, f"{side}.{k}", v, sample_size=sample_size)
        wrote_any = True
    return wrote_any


def adapt_fbref_to_f74(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
    if not raw:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([str(k) for k in raw.keys()])
    if raw.get("match_id") is not None:
        env["sources"]["match_id"] = raw.get("match_id")

    home_blob = raw.get("home_stats") or raw.get("home_features") or {}
    away_blob = raw.get("away_stats") or raw.get("away_features") or {}
    if not isinstance(home_blob, dict):
        home_blob = {}
    if not isinstance(away_blob, dict):
        away_blob = {}

    home_sample = _safe_int(home_blob.get("sample")) or 0
    away_sample = _safe_int(away_blob.get("sample")) or 0

    wrote_home = _from_stats(env, "home", home_blob, home_sample)
    wrote_away = _from_stats(env, "away", away_blob, away_sample)

    codes: list[str] = []
    if wrote_home and home_sample and home_sample < 3:
        codes.append(RC_SAMPLE_TOO_SMALL)
    if wrote_away and away_sample and away_sample < 3:
        codes.append(RC_SAMPLE_TOO_SMALL)

    if wrote_home and wrote_away:
        codes.append(RC_MAPPING_OK)
    elif wrote_home or wrote_away:
        codes.append(RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
