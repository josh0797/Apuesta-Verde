"""Sprint-F98 · Phase 2 — adapter layer tests (per-source + envelope).

Acceptance criteria:
  1. Every adapter is fail-soft (None / {} / garbage → available=False).
  2. Every adapter is pure (no async, no IO required).
  3. Envelope schema invariants (keys, version, types).
  4. set_field() records provenance + sample_size + reason codes.
  5. compute_data_quality() bucketing.
  6. Per-source:
     - TheSportsDB: identity + recent fixtures + h2h.
     - SofaScore:   form + shots + possession + corners + h2h + odds.
     - TheStatsAPI: xG from team_stats season-avg + form + h2h + odds.
     - StatsBomb:   xG + shots from cached features.
     - FBref:       xG + shots + possession from cached stats.
  7. None values produce FIELD_NULL provenance — NEVER leak into home/away
     dicts.
  8. NEVER raise on malformed sub-dicts.
"""
from __future__ import annotations

import pytest

from services.adapters import (
    ENVELOPE_SCHEMA_VERSION,
    adapt_fbref_to_f74,
    adapt_sofascore_to_f74,
    adapt_statsbomb_to_f74,
    adapt_thesportsdb_to_f74,
    adapt_thestatsapi_to_f74,
    compute_data_quality,
    new_envelope,
    set_field,
)
from services.adapters._envelope import (
    DQ_LIMITED,
    DQ_STRONG,
    DQ_THIN,
    DQ_USABLE,
    RC_FIELD_NULL,
    RC_MAPPING_OK,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    finalize_envelope,
)


# ─────────────────────────────────────────────────────────────────────
# Envelope skeleton & set_field
# ─────────────────────────────────────────────────────────────────────
def test_new_envelope_has_required_keys():
    env = new_envelope(source="x")
    for k in ("schema_version", "source", "available", "home", "away",
              "h2h", "odds", "sources", "field_provenance",
              "sample_sizes", "data_quality", "data_completeness_score",
              "reason_codes", "generated_at"):
        assert k in env
    assert env["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert env["source"] == "x"
    assert env["data_quality"] == DQ_THIN
    assert env["data_completeness_score"] == 0


def test_set_field_writes_value_and_provenance():
    env = new_envelope(source="src")
    ok = set_field(env, "home.xg_for_l5", 1.4, sample_size=5,
                   reason_codes=["SOFASCORE_FORM_OK"])
    assert ok is True
    assert env["home"]["xg_for_l5"] == 1.4
    prov = env["field_provenance"]["home.xg_for_l5"]
    assert prov["source"] == "src"
    assert prov["sample_size"] == 5
    assert "SOFASCORE_FORM_OK" in prov["reason_codes"]
    assert RC_MAPPING_OK in prov["reason_codes"]
    assert env["sample_sizes"]["home.xg_for_l5"] == 5


def test_set_field_skips_none_and_records_null_provenance():
    env = new_envelope(source="src")
    ok = set_field(env, "home.xg_for_l5", None, sample_size=5)
    assert ok is False
    # No value leaked
    assert "xg_for_l5" not in env["home"]
    # Provenance still recorded with NULL
    assert RC_FIELD_NULL in env["field_provenance"]["home.xg_for_l5"]["reason_codes"]


def test_set_field_rejects_invalid_paths():
    env = new_envelope(source="src")
    assert set_field(env, "garbage", 1.4) is False
    assert set_field(env, "unknown.x", 1.4) is False
    assert set_field(env, "home.x", 1.4) is True   # accepted


# ─────────────────────────────────────────────────────────────────────
# compute_data_quality buckets
# ─────────────────────────────────────────────────────────────────────
def test_compute_data_quality_thin_when_empty():
    env = new_envelope(source="x")
    dq, score = compute_data_quality(env)
    assert dq == DQ_THIN
    assert score == 0


def test_compute_data_quality_strong_with_rich_envelope():
    env = new_envelope(source="x")
    # Force a high-weight feature set
    paths = [
        "home.recent_fixtures", "away.recent_fixtures",
        "home.xg_for_l5", "away.xg_for_l5",
        "home.xg_against_l5", "away.xg_against_l5",
        "home.goals_scored_l5", "away.goals_scored_l5",
        "home.goals_conceded_l5", "away.goals_conceded_l5",
        "home.shots_for_l5", "away.shots_for_l5",
        "home.shots_on_target_l5", "away.shots_on_target_l5",
        "h2h.matches", "h2h.sample",
        "odds.match_winner", "odds.over_2_5",
    ]
    for p in paths:
        # Lists need at least one entry to count.
        if p.endswith("recent_fixtures") or p == "h2h.matches":
            set_field(env, p, [{"date": "2026-01-01", "home_goals": 1, "away_goals": 0}])
        elif p.startswith("odds."):
            set_field(env, p, {"home": 1.8, "away": 4.2, "draw": 3.5})
        else:
            set_field(env, p, 1.5)
    finalize_envelope(env)
    assert env["data_quality"] == DQ_STRONG
    assert env["data_completeness_score"] >= 75


def test_compute_data_quality_limited_with_partial():
    env = new_envelope(source="x")
    # ~30-40 points: a couple of xG entries + recent fixtures one side
    set_field(env, "home.recent_fixtures", [{"x": 1}])
    set_field(env, "home.xg_for_l5", 1.3)
    set_field(env, "home.goals_scored_l5", 1.5)
    finalize_envelope(env)
    assert env["data_quality"] in (DQ_LIMITED, DQ_THIN, DQ_USABLE)


# ─────────────────────────────────────────────────────────────────────
# TheSportsDB adapter
# ─────────────────────────────────────────────────────────────────────
def test_thesportsdb_adapter_fail_soft_on_garbage():
    assert adapt_thesportsdb_to_f74(None)["available"] is False
    assert adapt_thesportsdb_to_f74([])["available"] is False
    assert adapt_thesportsdb_to_f74("garbage")["available"] is False
    assert adapt_thesportsdb_to_f74({})["available"] is False
    assert RC_RAW_NOT_DICT in adapt_thesportsdb_to_f74(None)["reason_codes"]
    assert RC_RAW_EMPTY in adapt_thesportsdb_to_f74({})["reason_codes"]


def test_thesportsdb_adapter_extracts_identity_and_h2h():
    raw = {
        "event": {
            "idEvent":     "12345",
            "idLeague":    "4480",
            "strLeague":   "FIFA World Cup",
            "strTimestamp": "2026-06-13T19:00:00",
        },
        "recent_home": [
            {"strHomeTeam": "Argentina", "strAwayTeam": "Italy",
             "intHomeScore": 2, "intAwayScore": 1, "dateEvent": "2026-05-01"},
            {"strHomeTeam": "Argentina", "strAwayTeam": "Brazil",
             "intHomeScore": 0, "intAwayScore": 0, "dateEvent": "2026-04-12"},
        ],
        "recent_away": [
            {"strHomeTeam": "Austria", "strAwayTeam": "Croatia",
             "intHomeScore": 1, "intAwayScore": 2, "dateEvent": "2026-05-03"},
        ],
        "h2h": [
            {"strHomeTeam": "Argentina", "strAwayTeam": "Austria",
             "intHomeScore": 2, "intAwayScore": 0, "dateEvent": "2024-09-10"},
            {"strHomeTeam": "Austria",   "strAwayTeam": "Argentina",
             "intHomeScore": 0, "intAwayScore": 1, "dateEvent": "2018-11-12"},
        ],
    }
    env = adapt_thesportsdb_to_f74(raw)
    assert env["available"] is True
    assert env["sources"]["event_id"] == "12345"
    assert env["sources"]["league_name"] == "FIFA World Cup"
    # recent fixtures persisted
    assert len(env["home"]["recent_fixtures"]) == 2
    assert env["home"]["goals_scored_l5"] is not None
    assert env["away"]["recent_fixtures"]
    # h2h aggregates
    assert env["h2h"]["sample"] == 2
    assert env["h2h"]["home_wins"] == 1
    assert env["h2h"]["away_wins"] == 1
    assert env["h2h"]["draws"] == 0


def test_thesportsdb_adapter_handles_only_event_no_recent():
    raw = {"event": {"idEvent": "1", "strLeague": "x"}}
    env = adapt_thesportsdb_to_f74(raw)
    # No usable home/away/h2h → available=False
    assert env["available"] is False
    assert RC_NO_USABLE_FIELDS in env["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# SofaScore adapter
# ─────────────────────────────────────────────────────────────────────
def test_sofascore_adapter_fail_soft():
    assert adapt_sofascore_to_f74(None)["available"] is False
    assert adapt_sofascore_to_f74({})["available"] is False
    assert adapt_sofascore_to_f74("garbage")["available"] is False


def test_sofascore_adapter_extracts_rich_form():
    raw = {
        "event_id": 98765,
        "home_form": [
            {"home_team": "Argentina", "away_team": "Italy",
             "home_score": 2, "away_score": 1,
             "home_stats": {"shots": 12, "shots_on_target": 5,
                             "possession": 58.0, "corners": 6, "xg": 1.6},
             "away_stats": {"shots": 8, "corners": 3, "xg": 0.9}},
            {"home_team": "France",   "away_team": "Argentina",
             "home_score": 1, "away_score": 1,
             "home_stats": {"corners": 5}, "away_stats": {"corners": 7}},
        ],
        "away_form": [
            {"home_team": "Austria", "away_team": "Croatia",
             "home_score": 1, "away_score": 2,
             "home_stats": {"shots": 9, "shots_on_target": 3, "corners": 4, "xg": 1.2},
             "away_stats": {"shots": 11, "corners": 6, "xg": 1.4}},
        ],
        "h2h": [
            {"home_team": "Argentina", "away_team": "Austria",
             "home_score": 2, "away_score": 0, "date": "2024-09-10"},
        ],
        "odds": {
            "match_winner": {"home": 1.7, "draw": 3.6, "away": 4.5},
            "over_2_5":      {"yes": 2.1, "no": 1.7},
        },
    }
    env = adapt_sofascore_to_f74(raw, home_team="Argentina", away_team="Austria")
    assert env["available"] is True
    # Home stats present (Argentina was home in match 1, away in match 2)
    assert env["home"]["recent_fixtures"]
    assert env["home"]["shots_for_l5"] is not None
    assert env["home"]["corners_for_l5"] is not None
    # Away (Austria) has 1 fixture
    assert env["away"]["recent_fixtures"]
    assert env["away"]["shots_for_l5"] is not None
    # H2H
    assert env["h2h"]["sample"] == 1
    assert env["h2h"]["home_wins"] == 1
    # Odds passed through
    assert env["odds"]["match_winner"]["home"] == 1.7
    assert env["odds"]["over_2_5"]["yes"]      == 2.1


def test_sofascore_adapter_form_letter_uses_perspective():
    """When Argentina plays as 'away' in a fixture, the form letter
    must reflect Argentina's perspective."""
    raw = {
        "home_form": [
            # Argentina played AS AWAY here and LOST 0-2
            {"home_team": "Brazil", "away_team": "Argentina",
             "home_score": 2, "away_score": 0,
             "home_stats": {"corners": 5}, "away_stats": {"corners": 3}},
        ],
    }
    env = adapt_sofascore_to_f74(raw, home_team="Argentina", away_team="x")
    assert env["home"]["form_string_l5"] == "L"  # not W


# ─────────────────────────────────────────────────────────────────────
# TheStatsAPI adapter
# ─────────────────────────────────────────────────────────────────────
def test_thestatsapi_adapter_fail_soft():
    assert adapt_thestatsapi_to_f74(None)["available"] is False
    assert adapt_thestatsapi_to_f74({})["available"] is False


def test_thestatsapi_adapter_extracts_xg_from_team_stats():
    raw = {
        "match_id":   "abc",
        "team_stats": {
            "home": {"expected_goals_per_match": 1.55, "shots_per_match": 13.4,
                     "shots_on_target_per_match": 4.5, "possession_avg": 56.0,
                     "expected_goals_against_per_match": 0.92},
            "away": {"xg_per_match": 1.10, "shots_per_match": 10.1,
                     "xga": 1.30},
        },
    }
    env = adapt_thestatsapi_to_f74(raw)
    assert env["available"] is True
    assert env["home"]["xg_for_l5"]      == pytest.approx(1.55)
    assert env["home"]["xg_against_l5"]  == pytest.approx(0.92)
    assert env["home"]["shots_for_l5"]   == pytest.approx(13.4)
    assert env["home"]["shots_on_target_l5"] == pytest.approx(4.5)
    assert env["home"]["possession_avg_l5"] == pytest.approx(56.0)
    assert env["away"]["xg_for_l5"]      == pytest.approx(1.10)
    assert env["away"]["xg_against_l5"]  == pytest.approx(1.30)


def test_thestatsapi_adapter_partial_one_side_only():
    raw = {"team_stats": {"home": {"xg_per_match": 1.4}, "away": {}}}
    env = adapt_thestatsapi_to_f74(raw)
    assert env["available"] is True
    assert env["home"].get("xg_for_l5") is not None
    assert not env["away"]  # empty section


# ─────────────────────────────────────────────────────────────────────
# StatsBomb adapter
# ─────────────────────────────────────────────────────────────────────
def test_statsbomb_adapter_fail_soft():
    assert adapt_statsbomb_to_f74(None)["available"] is False
    assert adapt_statsbomb_to_f74({})["available"] is False


def test_statsbomb_adapter_extracts_cached_features():
    raw = {
        "match_id":       "abc",
        "sample_size":    5,
        "home_features":  {
            "xg_for_l5": 1.65, "xg_against_l5": 0.88,
            "shots_for_l5": 14.0, "shots_on_target_l5": 5.2,
            "possession_avg_l5": 57.0,
            "passes_completed_l5": 511.4, "pass_accuracy_l5": 0.86,
            "sample": 5,
        },
        "away_features":  {
            "xg_for_l5": 1.05, "xg_against_l5": 1.25,
            "sample": 5,
        },
    }
    env = adapt_statsbomb_to_f74(raw)
    assert env["available"] is True
    assert env["home"]["xg_for_l5"]      == pytest.approx(1.65)
    assert env["home"]["xg_against_l5"]  == pytest.approx(0.88)
    assert env["home"]["passes_completed_l5"] == pytest.approx(511.4)
    assert env["home"]["pass_accuracy_l5"]    == pytest.approx(0.86)
    assert env["away"]["xg_for_l5"]      == pytest.approx(1.05)
    assert env["sample_sizes"]["home.xg_for_l5"] == 5


def test_statsbomb_adapter_low_sample_warning():
    raw = {
        "sample_size":    1,
        "home_features":  {"xg_for_l5": 1.1, "sample": 1},
        "away_features":  {"xg_for_l5": 0.9, "sample": 1},
    }
    env = adapt_statsbomb_to_f74(raw)
    assert env["available"] is True
    assert "SAMPLE_TOO_SMALL" in env["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# FBref adapter
# ─────────────────────────────────────────────────────────────────────
def test_fbref_adapter_fail_soft():
    assert adapt_fbref_to_f74(None)["available"] is False
    assert adapt_fbref_to_f74({})["available"] is False


def test_fbref_adapter_extracts_cached_stats():
    raw = {
        "match_id":   "abc",
        "home_stats": {
            "xg_for_l5": 1.45, "xg_against_l5": 0.95,
            "shots_for_l5": 12.3, "possession_avg_l5": 54.0,
            "goals_scored_l5": 1.6, "goals_conceded_l5": 0.8,
            "sample": 5,
        },
        "away_stats": {
            "xg_for_l5": 1.10, "sample": 4,
        },
    }
    env = adapt_fbref_to_f74(raw)
    assert env["available"] is True
    assert env["home"]["xg_for_l5"]       == pytest.approx(1.45)
    assert env["home"]["goals_scored_l5"] == pytest.approx(1.6)
    assert env["home"]["possession_avg_l5"] == pytest.approx(54.0)
    assert env["away"]["xg_for_l5"] == pytest.approx(1.10)


# ─────────────────────────────────────────────────────────────────────
# Cross-adapter contract: NEVER raise
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fn", [
    adapt_thesportsdb_to_f74,
    lambda r: adapt_sofascore_to_f74(r, home_team="x", away_team="y"),
    adapt_thestatsapi_to_f74,
    adapt_statsbomb_to_f74,
    adapt_fbref_to_f74,
])
@pytest.mark.parametrize("raw", [
    None, "", [], (), 42, 3.14,
    {"home_features": "not-a-dict"},
    {"team_stats": "garbage"},
    {"event": 123},
    {"home_form": "not-a-list"},
    {"home_form": [None, None, "x", 1]},
    {"home_form": [{"home_score": "not-a-number"}]},
])
def test_adapters_never_raise(fn, raw):
    out = fn(raw)
    assert isinstance(out, dict)
    assert out["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert "available" in out
    assert "reason_codes" in out


# ─────────────────────────────────────────────────────────────────────
# Provenance integrity: every metric in home/away has a provenance entry
# ─────────────────────────────────────────────────────────────────────
def test_every_metric_has_provenance():
    raw = {
        "team_stats": {"home": {"xg_per_match": 1.4}, "away": {"xg_per_match": 1.1}},
    }
    env = adapt_thestatsapi_to_f74(raw)
    for side in ("home", "away"):
        for metric in env[side]:
            assert f"{side}.{metric}" in env["field_provenance"], (
                f"missing provenance for {side}.{metric}"
            )
