"""Sprint-F98 · Phase 4 + 5 — Builder + editorial F74 read-first.

Acceptance:
  1. ``build_football_data_enrichment`` is fail-soft (None / {} → valid F74).
  2. Builder runs the legacy adapter even when no external raws are
     attached, producing a usable F74 envelope FROM the live match doc.
  3. Builder respects cascade rankings when external raws ARE attached.
  4. ``schema_migration`` telemetry is populated:
     - ``canonical_schema = "F74"``
     - ``read_source = "F74"``
     - ``legacy_fallback_used`` true when only legacy adapter contributed
     - ``legacy_consumers_detected`` lists which legacy paths were used
  5. CRITICAL — the editorial ``_data_completeness`` reads F74 first.
     When a match has ``home_team.context.recent_fixtures`` (legacy
     shape), the editorial must no longer return ``data_quality: THIN``.
  6. When F74 IS attached directly, editorial uses it (no rebuild).
  7. Legacy fields still drive completeness when F74 is empty.
"""
from __future__ import annotations

import pytest

from services.adapters.legacy_match_adapter import adapt_legacy_match_to_f74
from services.football_editorial_prediction import _data_completeness
from services.football_enrichment_builder import (
    BUILDER_SCHEMA_VERSION,
    build_football_data_enrichment,
    read_f74_field,
)


# Sample fixture: 5 recent matches for "Argentina" with rich stats.
def _sample_recent_fixtures(team_name: str, team_id: int = 1) -> list[dict]:
    """Build a realistic recent_fixtures list as data_ingestion stores it."""
    return [
        # As HOME, won 2-1
        {"home_team": {"id": team_id, "name": team_name},
         "away_team": {"id": 99, "name": "Italy"},
         "home_goals": 2, "away_goals": 1, "date": "2026-05-01",
         "home_stats": {"shots": 12, "shots_on_target": 5,
                         "possession": 58.0, "corners": 6, "xg": 1.6},
         "away_stats": {"shots": 8, "corners": 3, "xg": 0.9}},
        # As AWAY, drew 1-1
        {"home_team": {"id": 99, "name": "Brazil"},
         "away_team": {"id": team_id, "name": team_name},
         "home_goals": 1, "away_goals": 1, "date": "2026-04-12",
         "home_stats": {"shots": 9, "corners": 5},
         "away_stats": {"shots": 11, "shots_on_target": 4,
                         "possession": 51.0, "corners": 6, "xg": 1.3}},
        # As HOME, won 3-0
        {"home_team": {"id": team_id, "name": team_name},
         "away_team": {"id": 99, "name": "Chile"},
         "home_goals": 3, "away_goals": 0, "date": "2026-03-25",
         "home_stats": {"shots": 18, "shots_on_target": 8,
                         "possession": 65.0, "corners": 8, "xg": 2.4},
         "away_stats": {"shots": 4, "corners": 1, "xg": 0.3}},
        # As AWAY, lost 0-2
        {"home_team": {"id": 99, "name": "Germany"},
         "away_team": {"id": team_id, "name": team_name},
         "home_goals": 2, "away_goals": 0, "date": "2026-03-10",
         "home_stats": {"shots": 14, "corners": 7},
         "away_stats": {"shots": 6, "shots_on_target": 2,
                         "possession": 42.0, "corners": 3, "xg": 0.6}},
        # As HOME, won 1-0
        {"home_team": {"id": team_id, "name": team_name},
         "away_team": {"id": 99, "name": "Mexico"},
         "home_goals": 1, "away_goals": 0, "date": "2026-02-20",
         "home_stats": {"shots": 13, "shots_on_target": 5,
                         "possession": 60.0, "corners": 5, "xg": 1.2},
         "away_stats": {"shots": 7, "corners": 2, "xg": 0.5}},
    ]


def _live_match_doc_legacy() -> dict:
    """Build a match doc shaped exactly as data_ingestion currently
    persists (recent_fixtures nested inside ``home_team.context``)."""
    return {
        "match_id":   "2391758",
        "home_team":  {"id": 1, "name": "Argentina",
                        "context": {
                            "recent_fixtures": _sample_recent_fixtures("Argentina", 1),
                        }},
        "away_team":  {"id": 2, "name": "Austria",
                        "context": {
                            "recent_fixtures": _sample_recent_fixtures("Austria", 2),
                        }},
        "kickoff_utc": "2026-06-13T19:00:00Z",
        "league":      {"name": "FIFA World Cup", "id": "4480"},
        "h2h_recent":  [
            {"home_team": "Argentina", "away_team": "Austria",
             "home_goals": 2, "away_goals": 0, "date": "2018-11-12"},
        ],
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Legacy adapter alone produces a usable envelope
# ─────────────────────────────────────────────────────────────────────
def test_legacy_adapter_aggregates_recent_fixtures():
    match = _live_match_doc_legacy()
    env = adapt_legacy_match_to_f74(match)
    assert env["available"] is True
    # Goals should be computed correctly (Argentina perspective: 2,1,3,0,1 = 1.4 avg)
    assert env["home"]["goals_scored_l5"] == pytest.approx(1.4)
    assert env["away"]["goals_scored_l5"] == pytest.approx(1.4)
    # Rich stats from home_stats / away_stats also aggregated.
    assert env["home"]["shots_for_l5"] is not None
    assert env["home"]["corners_for_l5"] is not None
    assert env["home"]["xg_for_l5"] is not None
    # H2H surfaced.
    assert env["h2h"]["sample"] == 1


def test_legacy_adapter_handles_team_perspective_correctly():
    """When the team played AS AWAY, conceded/scored must reflect them,
    NOT the home side of that fixture."""
    fixtures = [
        # Argentina played AS AWAY, LOST 0-2  → scored 0, conceded 2
        {"home_team": {"id": 99, "name": "Germany"},
         "away_team": {"id": 1,  "name": "Argentina"},
         "home_goals": 2, "away_goals": 0,
         "home_stats": {"corners": 7}, "away_stats": {"corners": 3}},
    ]
    match = {
        "home_team": {"id": 1, "name": "Argentina",
                       "context": {"recent_fixtures": fixtures}},
        "away_team": {"id": 2, "name": "x", "context": {"recent_fixtures": []}},
    }
    env = adapt_legacy_match_to_f74(match)
    # Goals scored = 0, conceded = 2
    assert env["home"]["goals_scored_l5"]   == pytest.approx(0.0)
    assert env["home"]["goals_conceded_l5"] == pytest.approx(2.0)
    # Corners_for taken from Argentina's "away_stats" (3), not the
    # home side (Germany).
    assert env["home"]["corners_for_l5"] == pytest.approx(3.0)


def test_legacy_adapter_fail_soft():
    assert adapt_legacy_match_to_f74(None)["available"] is False
    assert adapt_legacy_match_to_f74({})["available"] is False
    assert adapt_legacy_match_to_f74("garbage")["available"] is False


# ─────────────────────────────────────────────────────────────────────
# 2. Builder end-to-end
# ─────────────────────────────────────────────────────────────────────
def test_builder_returns_well_formed_f74_for_legacy_match():
    payload = build_football_data_enrichment(_live_match_doc_legacy())
    assert payload["schema_version"] == "F74"
    assert payload["schema_version_builder"] == BUILDER_SCHEMA_VERSION
    assert payload["available"] is True
    assert payload["home"]["goals_scored_l5"] is not None
    assert payload["away"]["goals_scored_l5"] is not None
    assert payload["data_quality"] != "THIN"
    # H2H present
    assert payload["h2h"]["sample"] == 1


def test_builder_fail_soft_on_garbage():
    payload = build_football_data_enrichment(None)
    assert payload["schema_version"] == "F74"
    assert payload["available"] is False
    assert payload["data_quality"] == "THIN"


def test_builder_schema_migration_telemetry_flags_legacy_only():
    """When ONLY the legacy adapter contributes data, telemetry must
    signal ``legacy_fallback_used: True``."""
    payload = build_football_data_enrichment(_live_match_doc_legacy())
    sm = payload["schema_migration"]
    assert sm["canonical_schema"] == "F74"
    assert sm["read_source"]      == "F74"
    assert sm["legacy_fallback_used"] is True
    assert "legacy_match_doc" in sm["legacy_consumers_detected"]


def test_builder_uses_external_raws_when_attached():
    """When an external raw is attached, cascade should pick its xG
    over the legacy aggregate."""
    match = _live_match_doc_legacy()
    # Inject TheStatsAPI raw: should win xG over legacy aggregate.
    match["_thestatsapi_raw"] = {
        "team_stats": {
            "home": {"expected_goals_per_match": 1.99},
            "away": {"expected_goals_per_match": 0.55},
        },
    }
    payload = build_football_data_enrichment(match)
    assert payload["home"]["xg_for_l5"] == pytest.approx(1.99)
    assert payload["field_provenance"]["home.xg_for_l5"]["source"] == "thestatsapi"
    # Telemetry: NOT a pure legacy fallback any more.
    assert payload["schema_migration"]["legacy_fallback_used"] is False


def test_builder_read_helper():
    payload = build_football_data_enrichment(_live_match_doc_legacy())
    assert read_f74_field(payload, "home", "goals_scored_l5") is not None
    assert read_f74_field(payload, "home", "non_existent_metric", default="x") == "x"
    assert read_f74_field(None, "home", "x", default=42) == 42


# ─────────────────────────────────────────────────────────────────────
# 3. CRITICAL — editorial reads F74 first and no longer says THIN
# ─────────────────────────────────────────────────────────────────────
def test_editorial_no_longer_thin_for_legacy_match_with_recent_fixtures():
    """REGRESSION GUARD for the user-reported bug:
       Argentina-Austria has recent_fixtures inside home_team.context
       → editorial used to return data_quality=THIN.
       Sprint-F98 must lift the quality to at least LIMITED / USABLE.
    """
    match = _live_match_doc_legacy()
    completeness = _data_completeness(match)
    assert completeness["data_quality"] != "THIN", (
        f"Sprint-F98 regression: editorial still returns THIN for a "
        f"match with recent_fixtures. Got {completeness}"
    )
    # Sanity: at least these signals must now be detected.
    assert completeness["has_goals_history"] is True
    assert completeness["has_xg"]            is True
    assert completeness["has_h2h"]           is True


def test_editorial_reads_pre_attached_f74_without_rebuilding():
    """When the match doc already carries ``football_data_enrichment``
    (F74), the editorial must use it directly (no rebuild)."""
    match = {
        # NOTE: NO home_team.context.recent_fixtures here.
        "home_team": {"id": 1, "name": "Argentina"},
        "away_team": {"id": 2, "name": "Austria"},
        "football_data_enrichment": {
            "schema_version": "F74",
            "available":      True,
            "home": {"goals_scored_l5": 2.0, "xg_for_l5": 1.5,
                     "btts_rate_l5": 0.6,    "corners_for_l5": 5.5,
                     "clean_sheets_l5": 1},
            "away": {"goals_scored_l5": 1.0, "xg_for_l5": 0.9,
                     "btts_rate_l5": 0.4,    "corners_for_l5": 4.2,
                     "clean_sheets_l5": 2},
            "h2h":  {"sample": 3, "home_wins": 2},
            "data_quality": "STRONG",
        },
    }
    completeness = _data_completeness(match)
    assert completeness["data_quality"] != "THIN"
    assert completeness["has_xg"]            is True
    assert completeness["has_h2h"]           is True
    assert completeness["has_corners_l5"]    is True


def test_editorial_falls_back_to_legacy_flat_when_no_f74_and_no_fixtures():
    """When neither F74 nor recent_fixtures are present but legacy flat
    fields ARE present, the editorial should still detect them."""
    match = {
        "home_team": {"id": 1, "name": "Argentina"},
        "away_team": {"id": 2, "name": "Austria"},
        "home_xg":   1.4,
        "away_xg":   1.1,
        "home_corners_for_l5": 5.2,
        "away_corners_for_l5": 4.4,
    }
    completeness = _data_completeness(match)
    assert completeness["has_xg"] is True
    assert completeness["has_corners_l5"] is True
    # Still at least LIMITED (xG + corners → ≥1 stats source).
    assert completeness["data_quality"] != "THIN"


def test_editorial_still_thin_when_truly_empty():
    """A match with NO stats and NO recent_fixtures must still be THIN."""
    match = {
        "home_team": {"id": 1, "name": "Argentina"},
        "away_team": {"id": 2, "name": "Austria"},
    }
    completeness = _data_completeness(match)
    assert completeness["data_quality"] == "THIN"
    assert completeness["has_goals_history"] is False
    assert completeness["has_xg"]            is False


def test_editorial_data_completeness_schema_migration_telemetry_attached():
    """Telemetry block must surface when F74 was consulted."""
    match = _live_match_doc_legacy()
    completeness = _data_completeness(match)
    sm = completeness.get("schema_migration")
    assert isinstance(sm, dict)
    assert sm["canonical_schema"] == "F74"
    assert sm["read_source"]      == "F74"
