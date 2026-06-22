"""Sprint-F98 · StatsBomb → F74 envelope adapter (pure).

StatsBomb is **cache-first / background-only** in our policy: this
adapter only consumes already-fetched StatsBomb payloads (typically
the per-team features blob produced by ``services.statsbomb_features``
or cached from the public open-data repo).

The adapter never triggers IO; it just converts the cached payload
into our F98 envelope shape.

Expected raw shape::

    raw = {
        "match_id":       "abc",
        "home_features":  {"xg_for_l5": ..., "xg_against_l5": ...,
                            "shots_for_l5": ..., "shots_on_target_l5": ...,
                            "passes_completed_l5": ..., "sample": 5},
        "away_features":  {...},
        "sample_size":    5,
    }

If only one side's features are present, the envelope flags MAPPING_PARTIAL.
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

SOURCE = "statsbomb"

_SUPPORTED_KEYS = (
    "xg_for_l5", "xg_against_l5",
    "shots_for_l5", "shots_on_target_l5",
    "possession_avg_l5",
    "passes_completed_l5", "pass_accuracy_l5",
)


def _from_features(env: dict, side: str, blob: Any, sample_size: int) -> bool:
    if not isinstance(blob, dict):
        return False
    wrote_any = False
    for k in _SUPPORTED_KEYS:
        v = _safe_float(blob.get(k))
        if v is None:
            continue
        # The pass_* keys aren't in our standard envelope feature set
        # (yet) — we surface them under their own paths so the
        # cascade selector can pick them later.
        if k in ("passes_completed_l5", "pass_accuracy_l5"):
            # keep ad-hoc; envelope tolerates arbitrary metrics under
            # home/away (only weights drive completeness score).
            env[side][k] = v
            env["field_provenance"][f"{side}.{k}"] = {
                "source": SOURCE, "sample_size": sample_size,
                "reason_codes": ["MAPPING_OK"],
            }
        else:
            set_field(env, f"{side}.{k}", v, sample_size=sample_size)
        wrote_any = True
    return wrote_any


def adapt_statsbomb_to_f74(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
    if not raw:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([str(k) for k in raw.keys()])
    if raw.get("match_id") is not None:
        env["sources"]["match_id"] = raw.get("match_id")

    # Determine sample size with fallback per side.
    overall_sample = _safe_int(raw.get("sample_size")) or 0
    hf = raw.get("home_features")
    af = raw.get("away_features")
    if not isinstance(hf, dict):
        hf = {}
    if not isinstance(af, dict):
        af = {}
    home_sample = _safe_int(hf.get("sample")) or overall_sample
    away_sample = _safe_int(af.get("sample")) or overall_sample

    wrote_home = _from_features(env, "home", hf, home_sample)
    wrote_away = _from_features(env, "away", af, away_sample)

    codes: list[str] = []
    min_required_sample = 3
    if wrote_home and home_sample and home_sample < min_required_sample:
        codes.append(RC_SAMPLE_TOO_SMALL)
    if wrote_away and away_sample and away_sample < min_required_sample:
        codes.append(RC_SAMPLE_TOO_SMALL)

    if wrote_home and wrote_away:
        codes.append(RC_MAPPING_OK)
    elif wrote_home or wrote_away:
        codes.append(RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
