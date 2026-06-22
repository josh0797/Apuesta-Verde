"""Sprint-F98 · Phase 3 — Cross-source cascade selector for football.

Given a list of F98 envelopes (one per provider), produce a single
**merged envelope** following the user-specified ranking per metric:

  * Fixture/identity:       TheSportsDB > TheStatsAPI > SofaScore
  * Result/events:          TheSportsDB > TheStatsAPI > SofaScore
  * xG / xGA:               TheStatsAPI > StatsBomb > SofaScore > FBref > proxy
  * Shots / SOT:            SofaScore > TheStatsAPI > StatsBomb > FBref
  * Possession / passes:    SofaScore > TheStatsAPI > FBref > StatsBomb
  * Form (recent fixtures): SofaScore > TheSportsDB > TheStatsAPI > FBref > warehouse
  * H2H:                    SofaScore > TheSportsDB > TheStatsAPI
  * Corners:                SofaScore > TheStatsAPI > FootyStats > TotalCorner
  * Odds:                   the-odds-api > TheStatsAPI > OddsPortal > SofaScore

Granular fail-soft rules (per the user's binding spec):
A provider is **skipped** for a given metric when any of:
  * HTTP error / timeout / CAPTCHA / blocked  (adapter signals "available=False")
  * Unexpected schema                          (RC_SCHEMA_MISMATCH in provenance)
  * Empty response                             (RC_RAW_EMPTY / RC_NO_USABLE_FIELDS)
  * Field absent                                (no entry in envelope.home/away/...)
  * Field is null                               (RC_FIELD_NULL in provenance)
  * Sample size insufficient (< 3)             (RC_SAMPLE_TOO_SMALL OR
                                                 sample_size < min_sample for that
                                                 metric)
  * Out-of-range value                          (RC_FIELD_OUT_OF_RANGE)
  * Stale data                                  (caller-provided ``staleness_codes``)

The cascade is **deterministic** and **pure**: given the same input,
the output is byte-for-byte stable.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from services.adapters._envelope import (
    DQ_THIN,
    ENVELOPE_SCHEMA_VERSION,
    RC_FIELD_NULL,
    RC_FIELD_OUT_OF_RANGE,
    RC_MAPPING_OK,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    RC_SAMPLE_TOO_SMALL,
    RC_SCHEMA_MISMATCH,
    compute_data_quality,
    new_envelope,
    set_field,
)

log = logging.getLogger(__name__)

CASCADE_SCHEMA_VERSION = "F98-CASCADE-1"

# Reason codes (greppable, stable).
RC_FALLBACK_USED          = "CASCADE_FALLBACK_USED"
RC_FIELD_SKIPPED_LOW_SAMPLE = "CASCADE_FIELD_SKIPPED_LOW_SAMPLE"
RC_PROVIDER_UNAVAILABLE   = "CASCADE_PROVIDER_UNAVAILABLE"
RC_PROVIDER_STALE         = "CASCADE_PROVIDER_STALE"
RC_NO_PROVIDER_HAD_FIELD  = "CASCADE_NO_PROVIDER_HAD_FIELD"
RC_PRIMARY_HIT            = "CASCADE_PRIMARY_HIT"

# Default ranking (user-binding).
DEFAULT_RANKINGS: dict[str, list[str]] = {
    # Identity / fixture / result are usually outside the envelope's
    # home/away — they come from sources.event_id etc. We still expose
    # the ranking to let the caller pick the right provider for ids.
    "_identity":              ["thesportsdb", "thestatsapi", "sofascore"],
    "_result":                ["thesportsdb", "thestatsapi", "sofascore"],

    # Per-metric rankings (binding contract).
    "xg_for_l5":              ["thestatsapi", "statsbomb", "sofascore", "fbref"],
    "xg_against_l5":          ["thestatsapi", "statsbomb", "sofascore", "fbref"],
    "shots_for_l5":           ["sofascore", "thestatsapi", "statsbomb", "fbref"],
    "shots_on_target_l5":     ["sofascore", "thestatsapi", "statsbomb", "fbref"],
    "possession_avg_l5":      ["sofascore", "thestatsapi", "fbref", "statsbomb"],
    "passes_completed_l5":    ["sofascore", "thestatsapi", "fbref", "statsbomb"],
    "pass_accuracy_l5":       ["sofascore", "thestatsapi", "fbref", "statsbomb"],

    # Form / recent fixtures
    "recent_fixtures":        ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],
    "form_string_l5":         ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],
    "goals_scored_l5":        ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],
    "goals_conceded_l5":      ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],
    "btts_rate_l5":           ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],
    "clean_sheets_l5":        ["sofascore", "thesportsdb", "thestatsapi", "fbref", "warehouse"],

    # Corners
    "corners_for_l5":         ["sofascore", "thestatsapi", "footystats", "totalcorner"],
    "corners_against_l5":     ["sofascore", "thestatsapi", "footystats", "totalcorner"],

    # H2H is a section, not a per-metric path; we apply the ranking to
    # `h2h.matches` (whichever provider has the richest h2h block wins).
    "_h2h":                   ["sofascore", "thesportsdb", "thestatsapi"],

    # Odds (sections under `odds.*`). The ranking is applied per market.
    "_odds":                  ["the_odds_api", "thestatsapi", "odds_portal", "sofascore"],
}

# Minimum sample size per metric. Metrics not listed default to 1
# (i.e. any non-null sample is acceptable).
DEFAULT_MIN_SAMPLE: dict[str, int] = {
    "xg_for_l5":            3,
    "xg_against_l5":        3,
    "shots_for_l5":         3,
    "shots_on_target_l5":   3,
    "possession_avg_l5":    3,
    "passes_completed_l5":  3,
    "pass_accuracy_l5":     3,
    "corners_for_l5":       3,
    "corners_against_l5":   3,
    "goals_scored_l5":      3,
    "goals_conceded_l5":    3,
    "btts_rate_l5":         3,
    "clean_sheets_l5":      3,
    "recent_fixtures":      1,   # any sample beats nothing
    "form_string_l5":       3,
}


def _envelope_by_source(envelopes: Iterable[dict]) -> dict[str, dict]:
    """Index envelopes by ``source`` (last writer wins)."""
    out: dict[str, dict] = {}
    for env in envelopes or []:
        if not isinstance(env, dict):
            continue
        src = env.get("source")
        if not src:
            continue
        out[str(src)] = env
    return out


def _provider_field_is_skippable(env: dict, path: str,
                                   *, min_sample: int,
                                   staleness_codes: Iterable[str]) -> tuple[bool, list[str]]:
    """Return (skip?, reasons) for a (provider, path) pair.

    Centralised gate so the cascade applies the same rules everywhere.
    """
    reasons: list[str] = []
    if not isinstance(env, dict):
        return True, [RC_SCHEMA_MISMATCH]
    if env.get("available") is False:
        reasons.append(RC_PROVIDER_UNAVAILABLE)
        for rc in env.get("reason_codes") or []:
            if rc in (RC_RAW_EMPTY, RC_RAW_NOT_DICT, RC_NO_USABLE_FIELDS,
                       RC_SCHEMA_MISMATCH):
                reasons.append(rc)
        return True, reasons

    # Path-level provenance is the source of truth for skip decisions.
    prov = (env.get("field_provenance") or {}).get(path) or {}
    rc_list = prov.get("reason_codes") or []
    if RC_FIELD_NULL in rc_list:
        return True, [RC_FIELD_NULL]
    if RC_FIELD_OUT_OF_RANGE in rc_list:
        return True, [RC_FIELD_OUT_OF_RANGE]
    for st in staleness_codes or []:
        if st in rc_list:
            return True, [RC_PROVIDER_STALE]

    # Read the actual value to confirm presence.
    if "." in path:
        section, metric = path.split(".", 1)
    else:
        return True, [RC_SCHEMA_MISMATCH]
    sec = env.get(section) or {}
    if not isinstance(sec, dict):
        return True, [RC_SCHEMA_MISMATCH]
    if metric not in sec:
        return True, ["FIELD_ABSENT"]
    value = sec.get(metric)
    if value is None:
        return True, [RC_FIELD_NULL]
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return True, ["FIELD_EMPTY_LIST"]

    # Sample-size guard.
    sample = prov.get("sample_size")
    if sample is None:
        sample = (env.get("sample_sizes") or {}).get(path)
    if isinstance(sample, (int, float)) and min_sample > 0 and sample < min_sample:
        return True, [RC_FIELD_SKIPPED_LOW_SAMPLE]
    if RC_SAMPLE_TOO_SMALL in rc_list and min_sample > 0:
        return True, [RC_FIELD_SKIPPED_LOW_SAMPLE]
    return False, []


def select_field(envelopes_by_src: dict[str, dict],
                  *,
                  side: str,
                  metric: str,
                  ranking: Optional[list[str]] = None,
                  min_sample: Optional[int] = None,
                  staleness_codes: Optional[Iterable[str]] = None,
                  ) -> tuple[Any, dict]:
    """Return ``(value, provenance)`` for ``home.{metric}`` or ``away.{metric}``.

    Resolution honours the ranking until a provider passes the
    skippable-gates above. Returns ``(None, {...with reason codes...})``
    when no provider has a usable value.
    """
    path = f"{side}.{metric}"
    ranking = ranking or DEFAULT_RANKINGS.get(metric) or []
    min_sample = (min_sample if min_sample is not None
                  else DEFAULT_MIN_SAMPLE.get(metric, 1))
    staleness_codes = list(staleness_codes or [])
    tried: list[dict] = []

    for source_name in ranking:
        env = envelopes_by_src.get(source_name)
        if env is None:
            tried.append({"source": source_name, "skip_reasons": ["PROVIDER_NOT_PRESENT"]})
            continue
        skip, reasons = _provider_field_is_skippable(
            env, path, min_sample=min_sample,
            staleness_codes=staleness_codes,
        )
        if skip:
            tried.append({"source": source_name, "skip_reasons": reasons})
            continue
        # Hit!
        section = env.get(side) or {}
        value = section.get(metric)
        prov = (env.get("field_provenance") or {}).get(path) or {}
        sample = prov.get("sample_size")
        rcs = [RC_PRIMARY_HIT if source_name == ranking[0] else RC_FALLBACK_USED]
        return value, {
            "source":        source_name,
            "sample_size":   sample,
            "reason_codes":  rcs,
            "fallback_chain": tried,
        }

    return None, {
        "source":        None,
        "sample_size":   None,
        "reason_codes":  [RC_NO_PROVIDER_HAD_FIELD],
        "fallback_chain": tried,
    }


def _select_section(envelopes_by_src: dict[str, dict],
                     *,
                     ranking: list[str],
                     section: str) -> tuple[dict, dict]:
    """Pick the first provider whose section (``h2h`` or ``odds``) is
    non-empty. Returns ``(section_dict, provenance)``."""
    tried: list[dict] = []
    for source_name in ranking:
        env = envelopes_by_src.get(source_name)
        if env is None:
            tried.append({"source": source_name, "skip_reasons": ["PROVIDER_NOT_PRESENT"]})
            continue
        if env.get("available") is False:
            tried.append({"source": source_name, "skip_reasons": [RC_PROVIDER_UNAVAILABLE]})
            continue
        sec = env.get(section) or {}
        if not isinstance(sec, dict) or not sec:
            tried.append({"source": source_name, "skip_reasons": ["SECTION_EMPTY"]})
            continue
        rcs = [RC_PRIMARY_HIT if source_name == ranking[0] else RC_FALLBACK_USED]
        return dict(sec), {
            "source":         source_name,
            "reason_codes":   rcs,
            "fallback_chain": tried,
        }
    return {}, {
        "source":        None,
        "reason_codes":  [RC_NO_PROVIDER_HAD_FIELD],
        "fallback_chain": tried,
    }


# ─────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────
def cascade_merge_envelopes(
    envelopes: Iterable[dict],
    *,
    rankings_override: Optional[dict[str, list[str]]] = None,
    min_sample_override: Optional[dict[str, int]] = None,
    staleness_codes: Optional[Iterable[str]] = None,
) -> dict:
    """Merge a list of adapter envelopes into a single F98 envelope.

    Parameters
    ----------
    envelopes:
        Iterable of dicts produced by ``adapt_*_to_f74``.
    rankings_override:
        Optional ``{metric_name: [provider, ...]}`` partial override.
    min_sample_override:
        Optional ``{metric_name: int}`` partial override.
    staleness_codes:
        Reason codes that, if present in a provider's field_provenance,
        cause that provider to be skipped for that field.

    Returns
    -------
    dict
        A new envelope with ``source = "cascade"``, ``home``/``away``
        populated from the highest-ranked available provider per
        metric, ``h2h`` / ``odds`` populated from the section ranking,
        and per-field provenance recording the chosen source and the
        fallback chain that was traversed.
    """
    envs = list(envelopes or [])
    by_src = _envelope_by_source(envs)
    rankings = dict(DEFAULT_RANKINGS)
    if rankings_override:
        rankings.update(rankings_override)
    min_samples = dict(DEFAULT_MIN_SAMPLE)
    if min_sample_override:
        min_samples.update(min_sample_override)
    staleness_codes = list(staleness_codes or [])

    merged = new_envelope(source="cascade", available=True)
    merged["sources"]["providers_considered"] = sorted(by_src.keys())
    merged["sources"]["providers_used"]       = []
    merged["schema_version_cascade"]          = CASCADE_SCHEMA_VERSION

    used_sources: set[str] = set()

    # Iterate over the union of all metrics the providers produced.
    seen_metrics: set[str] = set()
    for env in envs:
        if not isinstance(env, dict):
            continue
        for side in ("home", "away"):
            sec = env.get(side) or {}
            for metric in (sec or {}).keys():
                seen_metrics.add(metric)

    # For each (side, metric), apply the cascade.
    for side in ("home", "away"):
        for metric in sorted(seen_metrics):
            ranking = rankings.get(metric)
            if not ranking:
                # Unknown metric → fall through with a permissive default
                # (use whichever provider supplied the value first).
                ranking = sorted(by_src.keys())
            min_s = min_samples.get(metric, 1)
            value, prov = select_field(
                by_src, side=side, metric=metric,
                ranking=ranking, min_sample=min_s,
                staleness_codes=staleness_codes,
            )
            if value is not None:
                merged[side][metric] = value
                merged["field_provenance"][f"{side}.{metric}"] = prov
                if prov.get("sample_size") is not None:
                    merged["sample_sizes"][f"{side}.{metric}"] = prov["sample_size"]
                if prov.get("source"):
                    used_sources.add(prov["source"])

    # H2H section
    h2h, h2h_prov = _select_section(by_src, ranking=rankings["_h2h"], section="h2h")
    if h2h:
        merged["h2h"] = h2h
        merged["field_provenance"]["h2h"] = h2h_prov
        if h2h_prov.get("source"):
            used_sources.add(h2h_prov["source"])

    # Odds section — per market.
    # Build {market: [(source, payload)]}
    odds_rank = rankings["_odds"]
    market_to_choice: dict[str, tuple[str, Any]] = {}
    for source_name in odds_rank:
        env = by_src.get(source_name)
        if not isinstance(env, dict) or env.get("available") is False:
            continue
        for market, payload in (env.get("odds") or {}).items():
            if market in market_to_choice:
                continue
            if payload is None:
                continue
            market_to_choice[market] = (source_name, payload)
    for market, (source_name, payload) in market_to_choice.items():
        merged["odds"][market] = payload
        is_primary = (source_name == odds_rank[0])
        merged["field_provenance"][f"odds.{market}"] = {
            "source": source_name,
            "reason_codes": [RC_PRIMARY_HIT if is_primary else RC_FALLBACK_USED],
        }
        used_sources.add(source_name)

    merged["sources"]["providers_used"] = sorted(used_sources)

    # Final data quality on merged envelope.
    dq, score = compute_data_quality(merged)
    merged["data_quality"]            = dq
    merged["data_completeness_score"] = score

    if not used_sources:
        merged["available"] = False
        merged["reason_codes"].append("CASCADE_NO_USABLE_PROVIDERS")
    else:
        merged["reason_codes"].append("CASCADE_MERGED_OK")
    return merged


__all__ = [
    "CASCADE_SCHEMA_VERSION",
    "DEFAULT_RANKINGS",
    "DEFAULT_MIN_SAMPLE",
    "RC_FALLBACK_USED",
    "RC_FIELD_SKIPPED_LOW_SAMPLE",
    "RC_PROVIDER_UNAVAILABLE",
    "RC_PROVIDER_STALE",
    "RC_NO_PROVIDER_HAD_FIELD",
    "RC_PRIMARY_HIT",
    "select_field",
    "cascade_merge_envelopes",
]
