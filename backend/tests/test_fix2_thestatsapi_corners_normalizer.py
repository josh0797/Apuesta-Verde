"""FIX-2 — Tests for the corners provider regression with TheStatsAPI.

Real-world bug: TheStatsAPI returns per-stat split blocks under
``overview.<stat>.all.{home,away}`` (verified live with the
``Iran vs New Zealand`` finished match ``mt_986264843``):

    {
      "data": {
        "overview": {
          "corner_kicks":   {"all": {"home": 4, "away": 1}, ...},
          "expected_goals": {"all": {"home": 1.49, "away": 1.24}, ...},
          ...
        }
      }
    }

The previous ``normalize_match_stats`` only understood the flat shape
``{"home": {...}, "away": {...}}``. Result: corners (and every other
overview stat) silently dropped, ``enrich_match_corners_fast`` never
saw them, the UI showed "no corners" even when the data was present.

Tests cover:
  * Normalizer recognises the new shape with and without the outer
    ``{"data": ...}`` wrapper.
  * Corners surface in ``home_stats['Corner Kicks']`` /
    ``away_stats['Corner Kicks']``.
  * ``_extract_thestatsapi_corners`` reads them from the resulting
    ``live_stats`` block (post-normaliser fallback).
  * Backward compatibility: the legacy flat shape still works.
"""
from __future__ import annotations

from services.external_sources.thestatsapi_normalizer import normalize_match_stats
from services.football_corners_provider import (
    _extract_thestatsapi_corners,
    _extract_apisports_corners,
)


# ── Real Iran vs NZ overview block (excerpt — only the fields we care about) ──
_IRAN_NZ_RAW = {
    "match_id": "mt_986264843",
    "overview": {
        "ball_possession":  {"all": {"home": 48, "away": 52}},
        "expected_goals":   {"all": {"home": 1.49, "away": 1.24}},
        "total_shots":      {"all": {"home": 17, "away": 14}},
        "shots_on_target":  {"all": {"home": 4,  "away": 8}},
        "goalkeeper_saves": {"all": {"home": 6,  "away": 2}},
        "corner_kicks":     {"all": {"home": 4,  "away": 1}},
        "fouls":            {"all": {"home": 10, "away": 8}},
    },
    "score": {"home": 1, "away": 1},
    "minute": 90,
    "status": "finished",
}


def test_normalize_recognises_overview_shape_at_root():
    out = normalize_match_stats(_IRAN_NZ_RAW, fallback_status="finished")
    assert out is not None
    hs, as_ = out["home_stats"], out["away_stats"]
    assert hs["Corner Kicks"] == 4
    assert as_["Corner Kicks"] == 1
    assert hs["expected_goals"] == 1.49
    assert as_["expected_goals"] == 1.24
    # Possession is formatted as percentage string per API-Sports parity.
    assert hs["Ball Possession"] == "48%"
    assert as_["Ball Possession"] == "52%"
    assert out["_source"] == "thestatsapi"


def test_normalize_recognises_overview_shape_wrapped_in_data():
    wrapped = {"data": _IRAN_NZ_RAW}
    out = normalize_match_stats(wrapped, fallback_status="finished")
    assert out is not None
    assert (out["home_stats"]).get("Corner Kicks") == 4
    assert (out["away_stats"]).get("Corner Kicks") == 1


def test_normalize_falls_back_to_flat_shape_for_backward_compat():
    flat = {
        "home": {"corners": 5, "xg": 1.10},
        "away": {"corners": 2, "xg": 0.80},
        "score": {"home": 0, "away": 0},
        "minute": 45,
        "status": "live",
    }
    out = normalize_match_stats(flat, fallback_status="live")
    assert out is not None
    assert (out["home_stats"]).get("Corner Kicks") == 5
    assert (out["away_stats"]).get("Corner Kicks") == 2


def test_normalize_returns_none_when_no_usable_stats():
    out = normalize_match_stats({"unrelated": "data"}, fallback_status=None)
    assert out is None


def test_extract_thestatsapi_corners_reads_post_normaliser_live_stats():
    """When ``live_stats`` carries TheStatsAPI provenance and the
    normalised corners, the corners provider must surface them."""
    live = normalize_match_stats(_IRAN_NZ_RAW, fallback_status="finished")
    assert live is not None
    match_doc = {"live_stats": live}
    found = _extract_thestatsapi_corners(match_doc)
    assert found is not None
    assert found["source"] == "thestatsapi"
    assert found["home"] == 4
    assert found["away"] == 1
    assert found["total"] == 5


def test_extract_thestatsapi_corners_skips_live_stats_without_provenance():
    """Without _source/_sources tagging, the function MUST NOT
    misattribute API-Sports corners to TheStatsAPI."""
    match_doc = {"live_stats": {
        "home_stats": {"Corner Kicks": 3},
        "away_stats": {"Corner Kicks": 4},
        # no _source — pretend it's API-Sports.
    }}
    assert _extract_thestatsapi_corners(match_doc) is None


def test_extract_thestatsapi_corners_via_merged_sources_field():
    """merge_live_stats stamps ``_sources`` (plural) with both
    providers. Either way the corners must be picked up."""
    match_doc = {"live_stats": {
        "home_stats": {"Corner Kicks": 6},
        "away_stats": {"Corner Kicks": 2},
        "_sources": ["api_sports", "thestatsapi"],
    }}
    found = _extract_thestatsapi_corners(match_doc)
    assert found is not None
    assert found["home"] == 6
    assert found["away"] == 2


def test_apisports_corners_still_works_independently():
    """Sanity check: API-Sports corners path remains untouched."""
    match_doc = {"live_stats": {
        "home_stats": {"Corner Kicks": "7"},
        "away_stats": {"Corner Kicks": "3"},
    }}
    found = _extract_apisports_corners(match_doc)
    assert found is not None
    assert found["home"] == 7
    assert found["away"] == 3
    assert found["total"] == 10
    assert found["source"] == "api_sports"
