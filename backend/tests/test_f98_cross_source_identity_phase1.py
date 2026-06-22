"""Sprint-F98 · Phase 1 — football_cross_source_identity tests.

Acceptance criteria:
  1. Canonical id is deterministic from (date, home, away).
  2. National-team aliases (Egipto→Egypt, Países Bajos→Netherlands).
  3. Date drift > 6h is rejected; ≤ 6h is accepted.
  4. NEVER match by names alone when date is missing on either side
     (`events_refer_to_same_match` must return (False, [...])).
  5. Kickoff parsing handles ISO 'Z' suffix, '+02:00', unix epochs,
     and TheSportsDB-style nested dicts.
  6. Missing teams or kickoff → confidence=UNRESOLVED.
  7. Pre-resolved provider ids (in base_match) are honoured without
     calling external clients.
  8. When TheStatsAPI/SofaScore are not available, fail-soft with
     PROVIDER_LOOKUP_FAILED / PROVIDER_DISABLED reason codes.
  9. Resolver never raises — always returns a well-formed dict.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.football_cross_source_identity import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_UNRESOLVED,
    DATE_TOLERANCE_HOURS,
    RC_BASE_EVENT_USED,
    RC_DATE_DRIFT_TOO_LARGE,
    RC_KICKOFF_MISSING,
    RC_NAMES_ONLY_MATCH_REJECTED,
    RC_NATIONAL_TEAM_ALIAS_APPLIED,
    RC_NO_BASE_EVENT,
    RC_TEAMS_MISSING,
    SCHEMA_VERSION,
    _build_canonical_id,
    _kickoff_close_enough,
    _names_match,
    _normalize_team_name,
    _parse_kickoff,
    events_refer_to_same_match,
    resolve_football_match_sources,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Canonical id determinism
# ─────────────────────────────────────────────────────────────────────
def test_canonical_id_is_deterministic_from_date_and_teams():
    dt = datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc)
    a = _build_canonical_id(kickoff_utc=dt, home_norm="qatar", away_norm="switzerland")
    b = _build_canonical_id(kickoff_utc=dt, home_norm="qatar", away_norm="switzerland")
    assert a == b == "football:2026-06-13:qatar:switzerland"


def test_canonical_id_handles_missing_kickoff_gracefully():
    cid = _build_canonical_id(kickoff_utc=None, home_norm="france", away_norm="iraq")
    assert cid == "football:unknown:france:iraq"


# ─────────────────────────────────────────────────────────────────────
# 2. National-team alias normalisation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Egipto",         "egypt"),
    ("EGIPTO",         "egypt"),
    ("Bélgica",        "belgium"),
    ("países bajos",   "netherlands"),
    ("Países Bajos",   "netherlands"),
    ("Brasil",         "brazil"),
    ("CROACIA",        "croatia"),
    ("USA",            "united states"),
    ("Czechia",        "czech republic"),
    ("Argentina",      "argentina"),
    ("New Zealand",    "new zealand"),
])
def test_normalize_team_name_resolves_aliases(raw, expected):
    assert _normalize_team_name(raw) == expected


def test_normalize_team_name_handles_none_and_empty():
    assert _normalize_team_name(None) == ""
    assert _normalize_team_name("") == ""
    assert _normalize_team_name("   ") == ""


def test_normalize_team_name_strips_accents_when_no_alias():
    # "São Paulo" is a club, not in COUNTRY_ALIASES → fallback to
    # plain normalisation.
    assert _normalize_team_name("São Paulo") == "sao paulo"


# ─────────────────────────────────────────────────────────────────────
# 3. Date drift tolerance
# ─────────────────────────────────────────────────────────────────────
def test_kickoff_close_enough_accepts_within_tolerance():
    base = datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc)
    assert _kickoff_close_enough(base, base + timedelta(hours=5))
    assert _kickoff_close_enough(base, base - timedelta(hours=5, minutes=59))
    assert _kickoff_close_enough(base, base + timedelta(hours=DATE_TOLERANCE_HOURS))


def test_kickoff_close_enough_rejects_outside_tolerance():
    base = datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc)
    assert not _kickoff_close_enough(base, base + timedelta(hours=7))
    assert not _kickoff_close_enough(base, base + timedelta(hours=24))


def test_kickoff_close_enough_rejects_when_either_missing():
    base = datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc)
    assert not _kickoff_close_enough(None, base)
    assert not _kickoff_close_enough(base, None)
    assert not _kickoff_close_enough(None, None)


# ─────────────────────────────────────────────────────────────────────
# 4. HARD RULE — never match by names alone when date is missing
# ─────────────────────────────────────────────────────────────────────
def test_events_refer_to_same_match_rejects_names_only_match():
    """Critical safety: even if both names match exactly, missing
    kickoff on either side must NOT yield a positive match."""
    a = {"home_team": "Egipto", "away_team": "Nueva Zelanda",
         "kickoff_utc": "2026-06-13T19:00:00Z"}
    b = {"home_team": "Egypt",  "away_team": "New Zealand"}  # no date!
    same, codes = events_refer_to_same_match(a, b)
    assert same is False
    assert RC_NAMES_ONLY_MATCH_REJECTED in codes


def test_events_refer_to_same_match_accepts_aliased_names_with_close_date():
    a = {"home_team": "Egipto", "away_team": "Nueva Zelanda",
         "kickoff_utc": "2026-06-13T19:00:00Z"}
    b = {"home_team": "Egypt",  "away_team": "New Zealand",
         "kickoff_utc": "2026-06-13T20:30:00Z"}
    same, _ = events_refer_to_same_match(a, b)
    assert same is True


def test_events_refer_to_same_match_rejects_large_date_drift():
    a = {"home_team": "France", "away_team": "Iraq",
         "kickoff_utc": "2026-06-13T19:00:00Z"}
    b = {"home_team": "France", "away_team": "Iraq",
         "kickoff_utc": "2026-06-14T19:00:00Z"}  # 24h drift
    same, codes = events_refer_to_same_match(a, b)
    assert same is False
    assert RC_DATE_DRIFT_TOO_LARGE in codes


def test_events_refer_to_same_match_rejects_different_teams_same_date():
    a = {"home_team": "Argentina", "away_team": "Austria",
         "kickoff_utc": "2026-06-13T19:00:00Z"}
    b = {"home_team": "Argentina", "away_team": "Germany",
         "kickoff_utc": "2026-06-13T19:00:00Z"}
    same, _ = events_refer_to_same_match(a, b)
    assert same is False


# ─────────────────────────────────────────────────────────────────────
# 5. Kickoff parsing covers all common formats
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected_iso", [
    ("2026-06-13T19:00:00Z",       "2026-06-13T19:00:00+00:00"),
    ("2026-06-13T19:00:00+00:00",  "2026-06-13T19:00:00+00:00"),
    ("2026-06-13T21:00:00+02:00",  "2026-06-13T19:00:00+00:00"),
    ("2026-06-13",                  "2026-06-13T00:00:00+00:00"),
    (1781467200,                    None),    # 2026-06-12 → just check non-None
])
def test_parse_kickoff_iso_and_epoch_forms(raw, expected_iso):
    dt = _parse_kickoff(raw)
    assert dt is not None
    if expected_iso:
        assert dt.isoformat() == expected_iso


def test_parse_kickoff_nested_dict_thesportsdb_style():
    raw = {"iso": "2026-06-13T19:00:00Z"}
    dt = _parse_kickoff(raw)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 13


def test_parse_kickoff_returns_none_for_garbage():
    assert _parse_kickoff(None) is None
    assert _parse_kickoff("") is None
    assert _parse_kickoff("not-a-date") is None
    assert _parse_kickoff({"foo": "bar"}) is None


def test_parse_kickoff_naive_datetime_assumed_utc():
    naive = datetime(2026, 6, 13, 19, 0)
    dt = _parse_kickoff(naive)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# 6. Confidence buckets
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_returns_unresolved_when_kickoff_missing():
    base = {
        "home_team": {"name": "Argentina"},
        "away_team": {"name": "Austria"},
        # No date at all
        "_thesportsdb_event_id": "12345",
    }
    out = await resolve_football_match_sources(base, client=None, db=None)
    assert out["confidence"] == CONFIDENCE_UNRESOLVED
    assert RC_KICKOFF_MISSING in out["reason_codes"]
    assert out["thesportsdb_event_id"] == "12345"  # still surfaced


@pytest.mark.asyncio
async def test_resolver_returns_unresolved_when_team_missing():
    base = {
        "home_team": {"name": "Argentina"},
        # No away team
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "12345",
    }
    out = await resolve_football_match_sources(base, client=None, db=None)
    assert out["confidence"] == CONFIDENCE_UNRESOLVED
    assert RC_TEAMS_MISSING in out["reason_codes"]


@pytest.mark.asyncio
async def test_resolver_low_confidence_when_no_provider_ids():
    """Both teams + kickoff present but zero provider ids resolved."""
    base = {
        "home_team": {"name": "Argentina"},
        "away_team": {"name": "Austria"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
    }
    # Patch external resolvers to return None.
    with patch(
        "services.thestatsapi_client.is_enabled", return_value=False,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    assert out["confidence"] == CONFIDENCE_LOW
    assert RC_NO_BASE_EVENT in out["reason_codes"]


@pytest.mark.asyncio
async def test_resolver_medium_confidence_with_pre_resolved_base_event():
    base = {
        "home_team": {"name": "Argentina"},
        "away_team": {"name": "Austria"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "tsdb-12345",
    }
    with patch(
        "services.thestatsapi_client.is_enabled", return_value=False,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    assert out["confidence"] == CONFIDENCE_MEDIUM
    assert out["thesportsdb_event_id"] == "tsdb-12345"
    assert RC_BASE_EVENT_USED in out["reason_codes"]


@pytest.mark.asyncio
async def test_resolver_high_confidence_with_two_provider_ids():
    base = {
        "home_team": {"name": "Argentina"},
        "away_team": {"name": "Austria"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "tsdb-12345",
        "_sofascore_event_id":   "ss-98765",
    }
    # No external calls needed (both ids pre-resolved). Patch out the
    # downstream resolvers anyway to be safe.
    with patch(
        "services.thestatsapi_client.is_enabled", return_value=False,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    assert out["confidence"] == CONFIDENCE_HIGH
    assert out["thesportsdb_event_id"] == "tsdb-12345"
    assert out["sofascore_event_id"]   == "ss-98765"


# ─────────────────────────────────────────────────────────────────────
# 7. National-team alias telemetry
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_emits_national_team_alias_flag():
    base = {
        "home_team": {"name": "Egipto"},
        "away_team": {"name": "Nueva Zelanda"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "tsdb-egy-nzl",
    }
    with patch(
        "services.thestatsapi_client.is_enabled", return_value=False,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    assert RC_NATIONAL_TEAM_ALIAS_APPLIED in out["reason_codes"]
    # Canonical id uses normalised names
    assert "egypt" in out["canonical_match_id"]
    assert "new_zealand" in out["canonical_match_id"]


# ─────────────────────────────────────────────────────────────────────
# 8. Fail-soft when external lookups raise
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_fail_soft_when_thestatsapi_raises():
    base = {
        "home_team": {"name": "France"},
        "away_team": {"name": "Iraq"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "tsdb-fra-irq",
    }

    async def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    with patch(
        "services.thestatsapi_client.is_enabled", return_value=True,
    ), patch(
        "services.thestatsapi_client.resolve_thestatsapi_match_id_by_names",
        side_effect=_boom,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    # No exception, but a reason code surfaces.
    failed = [c for c in out["reason_codes"]
              if c.startswith("PROVIDER_LOOKUP_FAILED")]
    assert failed, f"expected PROVIDER_LOOKUP_FAILED:* code, got {out['reason_codes']}"


@pytest.mark.asyncio
async def test_resolver_never_raises_for_garbage_input():
    out = await resolve_football_match_sources(
        {"this": "is", "garbage": True}, client=None, db=None,
    )
    assert isinstance(out, dict)
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["confidence"] == CONFIDENCE_UNRESOLVED


@pytest.mark.asyncio
async def test_resolver_returns_well_formed_dict_for_non_dict_base():
    out = await resolve_football_match_sources(None, client=None, db=None)  # type: ignore[arg-type]
    assert out["confidence"] == CONFIDENCE_UNRESOLVED
    assert "BASE_MATCH_NOT_A_DICT" in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 9. Schema invariants
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_output_schema_invariants():
    base = {
        "home_team": {"name": "Argentina"},
        "away_team": {"name": "Austria"},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "_thesportsdb_event_id": "tsdb-arg-aut",
    }
    with patch(
        "services.thestatsapi_client.is_enabled", return_value=False,
    ), patch(
        "services.external_sources.sofascore._resolve_event_id",
        return_value=None,
    ):
        out = await resolve_football_match_sources(base, client=None, db=None)
    for key in (
        "canonical_match_id", "thesportsdb_event_id", "sofascore_event_id",
        "thestatsapi_match_id", "confidence", "matched_by", "reason_codes",
        "resolved_at", "schema_version",
    ):
        assert key in out, f"missing required key {key!r}"
    assert out["schema_version"] == SCHEMA_VERSION
    assert isinstance(out["matched_by"], list)
    assert isinstance(out["reason_codes"], list)


# ─────────────────────────────────────────────────────────────────────
# 10. _names_match helper
# ─────────────────────────────────────────────────────────────────────
def test_names_match_helper():
    assert _names_match("argentina", "austria", "argentina", "austria")
    assert not _names_match("argentina", "austria", "argentina", "germany")
    assert not _names_match("", "austria", "argentina", "austria")
    assert not _names_match("argentina", "", "argentina", "austria")
