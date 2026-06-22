"""Sprint-F98 · Canonical adapter envelope.

Every adapter produces a dict shaped like this::

    {
      "schema_version":        "F98-ENV-1",
      "source":                "sofascore" | "thesportsdb" | ...,
      "available":             True/False,
      "home":                  {<flat metric:value pairs>},
      "away":                  {<flat metric:value pairs>},
      "h2h":                   {...},
      "odds":                  {...},
      "sources":               {"provider": "sofascore", "raw_keys": [...]},
      "field_provenance":      {
          "home.goals_scored_l5":  {"source": "sofascore",
                                    "sample_size": 5,
                                    "reason_codes": ["SOFASCORE_FORM_OK"]},
          ...
      },
      "sample_sizes":          {"home.recent_fixtures": 5, ...},
      "data_quality":          "THIN" | "LIMITED" | "USABLE" | "STRONG",
      "data_completeness_score": 0..100,
      "reason_codes":           ["..."]
    }

The key invariant: **the envelope NEVER raises**. Bad input produces
an ``available=False`` envelope with reason codes.

Flat metric naming (binding contract — consumers depend on this):

  home.goals_scored_l5      / away.goals_scored_l5
  home.goals_conceded_l5    / away.goals_conceded_l5
  home.goals_scored_l15     / away.goals_scored_l15
  home.goals_conceded_l15   / away.goals_conceded_l15
  home.xg_for_l5            / away.xg_for_l5
  home.xg_against_l5        / away.xg_against_l5
  home.xg_for_l15           / away.xg_for_l15
  home.xg_against_l15       / away.xg_against_l15
  home.shots_for_l5         / away.shots_for_l5
  home.shots_on_target_l5   / away.shots_on_target_l5
  home.possession_avg_l5    / away.possession_avg_l5
  home.corners_for_l5       / away.corners_for_l5
  home.corners_against_l5   / away.corners_against_l5
  home.btts_rate_l5         / away.btts_rate_l5
  home.clean_sheets_l5      / away.clean_sheets_l5
  home.recent_fixtures      / away.recent_fixtures   (list[dict])
  home.form_string_l5       / away.form_string_l5    ("WWDLW" …)
  h2h.matches               (list[dict])
  h2h.home_wins / h2h.away_wins / h2h.draws / h2h.sample
  odds.<market>             ({"selection": odds, ...})
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

ENVELOPE_SCHEMA_VERSION = "F98-ENV-1"

DQ_THIN     = "THIN"
DQ_LIMITED  = "LIMITED"
DQ_USABLE   = "USABLE"
DQ_STRONG   = "STRONG"
_DQ_ORDER = {DQ_THIN: 0, DQ_LIMITED: 1, DQ_USABLE: 2, DQ_STRONG: 3}

# Reason codes (per-source we expect to see in the wild).
RC_RAW_EMPTY          = "RAW_EMPTY"
RC_RAW_NOT_DICT       = "RAW_NOT_DICT"
RC_MAPPING_OK         = "MAPPING_OK"
RC_MAPPING_PARTIAL    = "MAPPING_PARTIAL"
RC_NO_USABLE_FIELDS   = "NO_USABLE_FIELDS"
RC_FIELD_NULL         = "FIELD_NULL"
RC_SAMPLE_TOO_SMALL   = "SAMPLE_TOO_SMALL"
RC_FIELD_OUT_OF_RANGE = "FIELD_OUT_OF_RANGE"
RC_SCHEMA_MISMATCH    = "SCHEMA_MISMATCH"


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except Exception:  # noqa: BLE001
            return None


def new_envelope(*, source: str, available: bool = True) -> dict:
    """Create a fresh envelope skeleton."""
    return {
        "schema_version":          ENVELOPE_SCHEMA_VERSION,
        "source":                  source,
        "available":               bool(available),
        "home":                    {},
        "away":                    {},
        "h2h":                     {},
        "odds":                    {},
        "sources":                 {"provider": source, "raw_keys": []},
        "field_provenance":        {},
        "sample_sizes":            {},
        "data_quality":            DQ_THIN,
        "data_completeness_score": 0,
        "reason_codes":            [],
        "generated_at":            datetime.now(timezone.utc).isoformat(),
    }


def set_field(envelope: dict,
               path: str,
               value: Any,
               *,
               sample_size: Optional[int] = None,
               reason_codes: Optional[Iterable[str]] = None,
               ) -> bool:
    """Set ``envelope[section][metric] = value`` and record provenance.

    ``path`` is dotted: ``"home.goals_scored_l5"`` / ``"h2h.home_wins"``
    / ``"odds.over_2_5"`` etc.

    Returns True iff a non-null value was set. None / NaN values are
    silently skipped and recorded as ``FIELD_NULL`` in field_provenance.
    """
    if not isinstance(envelope, dict) or not isinstance(path, str) or "." not in path:
        return False
    section, metric = path.split(".", 1)
    if section not in ("home", "away", "h2h", "odds"):
        return False

    rc = list(reason_codes or [])
    if value is None:
        envelope["field_provenance"][path] = {
            "source":       envelope.get("source"),
            "sample_size":  sample_size,
            "reason_codes": rc + [RC_FIELD_NULL],
        }
        return False

    envelope[section][metric] = value
    envelope["field_provenance"][path] = {
        "source":       envelope.get("source"),
        "sample_size":  sample_size,
        "reason_codes": rc + [RC_MAPPING_OK],
    }
    if sample_size is not None:
        envelope["sample_sizes"][path] = int(sample_size)
    return True


# ── Standard "strong" feature set used to compute completeness score ──
# These are the fields the editorial / market_selection / corner_engine
# realistically consume. Weights add to 100.
_FEATURE_WEIGHTS = {
    "home.recent_fixtures":       8,
    "away.recent_fixtures":       8,
    "home.xg_for_l5":             6,
    "away.xg_for_l5":             6,
    "home.xg_against_l5":         5,
    "away.xg_against_l5":         5,
    "home.goals_scored_l5":       5,
    "away.goals_scored_l5":       5,
    "home.goals_conceded_l5":     4,
    "away.goals_conceded_l5":     4,
    "home.shots_for_l5":          3,
    "away.shots_for_l5":          3,
    "home.shots_on_target_l5":    3,
    "away.shots_on_target_l5":    3,
    "home.possession_avg_l5":     2,
    "away.possession_avg_l5":     2,
    "home.corners_for_l5":        3,
    "away.corners_for_l5":        3,
    "home.btts_rate_l5":          2,
    "away.btts_rate_l5":          2,
    "home.clean_sheets_l5":       2,
    "away.clean_sheets_l5":       2,
    "h2h.matches":                7,
    "h2h.sample":                 1,
    "odds.match_winner":          3,
    "odds.over_2_5":              2,
    "odds.btts":                  2,
}


def compute_data_quality(envelope: dict) -> tuple[str, int]:
    """Return (data_quality_label, completeness_score 0..100).

    Rules:
      THIN     score <  25
      LIMITED  25 <= score < 50
      USABLE   50 <= score < 75
      STRONG   score >= 75
    """
    if not isinstance(envelope, dict):
        return DQ_THIN, 0
    score = 0
    for path, weight in _FEATURE_WEIGHTS.items():
        section, metric = path.split(".", 1)
        sec = envelope.get(section) or {}
        if not isinstance(sec, dict):
            continue
        v = sec.get(metric)
        if v is None:
            continue
        # For list-like fields require at least one entry.
        if isinstance(v, (list, tuple)) and len(v) == 0:
            continue
        score += weight
    if score >= 75:
        label = DQ_STRONG
    elif score >= 50:
        label = DQ_USABLE
    elif score >= 25:
        label = DQ_LIMITED
    else:
        label = DQ_THIN
    return label, int(min(100, score))


def finalize_envelope(envelope: dict, *, extra_codes: Optional[Iterable[str]] = None) -> dict:
    """Stamp final ``data_quality`` + ``data_completeness_score`` and
    return the envelope (mutated in-place). Appends ``extra_codes``
    deduplicated."""
    if not isinstance(envelope, dict):
        return envelope
    dq, score = compute_data_quality(envelope)
    envelope["data_quality"]            = dq
    envelope["data_completeness_score"] = score
    if extra_codes:
        codes = envelope.setdefault("reason_codes", [])
        for c in extra_codes:
            if c and c not in codes:
                codes.append(c)
    return envelope


def envelope_unavailable(*, source: str, reason: str) -> dict:
    """Convenience constructor for "raw was empty/garbage" cases."""
    env = new_envelope(source=source, available=False)
    env["reason_codes"].append(reason)
    env["data_quality"] = DQ_THIN
    env["data_completeness_score"] = 0
    return env


def _last_n(seq: Any, n: int) -> list:
    """Take last ``n`` items from a sequence; ignores non-sequences.

    Treats the *last* element of the list as the most recent match.
    Providers that store oldest-first are normalized by callers.
    """
    if not isinstance(seq, (list, tuple)):
        return []
    return list(seq)[-int(n):] if int(n) > 0 else []


def _safe_mean(values: Iterable[Any]) -> Optional[float]:
    nums = [_safe_float(v) for v in values]
    nums = [v for v in nums if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _normalize_form_letter(result: Any, *, team_side: str, scored: Any, conceded: Any) -> Optional[str]:
    """Return W/D/L for a single match given (scored, conceded).

    Some providers give a raw result letter; if not, derive from goals.
    """
    if isinstance(result, str) and result.upper() in ("W", "D", "L"):
        return result.upper()
    s = _safe_int(scored)
    c = _safe_int(conceded)
    if s is None or c is None:
        return None
    if s > c:
        return "W"
    if s < c:
        return "L"
    return "D"


__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "DQ_THIN", "DQ_LIMITED", "DQ_USABLE", "DQ_STRONG",
    "RC_RAW_EMPTY", "RC_RAW_NOT_DICT", "RC_MAPPING_OK",
    "RC_MAPPING_PARTIAL", "RC_NO_USABLE_FIELDS", "RC_FIELD_NULL",
    "RC_SAMPLE_TOO_SMALL", "RC_FIELD_OUT_OF_RANGE", "RC_SCHEMA_MISMATCH",
    "new_envelope", "set_field", "compute_data_quality",
    "finalize_envelope", "envelope_unavailable",
    "_safe_float", "_safe_int", "_safe_mean",
    "_last_n", "_normalize_form_letter",
]
