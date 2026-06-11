"""Phase F58 smoke tests — football integration wiring."""
from __future__ import annotations

import pytest

from services.football_phaseF58_integration import (
    _derive_l5_l15_from_recent,
    _resolve_pick_side,
    attach_football_profile_cross_to_payload,
)


def test_derive_l5_l15_from_dict_recent_fixtures():
    side = {
        "context": {
            "recent_fixtures": {
                "gf": [0, 1, 0, 1, 0, 1, 2, 1, 1, 1, 2, 1, 0, 1, 1],
                "ga": [0, 0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 1, 0],
                "corners": [5, 4, 6, 5, 4, 5, 5, 6, 4, 5, 5, 6, 4, 5, 5],
            }
        }
    }
    out = _derive_l5_l15_from_recent(side)
    assert out["goals_for_l5"] == round((0 + 1 + 0 + 1 + 0) / 5, 3)
    assert out["goals_for_l15"] is not None
    assert out["corners_l5"] is not None


def test_derive_l5_l15_from_list_recent_fixtures():
    side = {
        "context": {
            "recent_fixtures": [
                {"gf": 0, "ga": 1, "shots": 8, "shots_on_target": 2},
                {"gf": 1, "ga": 0, "shots": 12, "shots_on_target": 4},
                {"gf": 0, "ga": 1, "shots": 7, "shots_on_target": 1},
                {"gf": 0, "ga": 2, "shots": 9, "shots_on_target": 3},
                {"gf": 1, "ga": 0, "shots": 11, "shots_on_target": 4},
                {"gf": 1, "ga": 1, "shots": 10, "shots_on_target": 3},
            ]
        }
    }
    out = _derive_l5_l15_from_recent(side)
    assert out["goals_for_l5"] == 0.4
    assert out["shots_l5"] == round((8 + 12 + 7 + 9 + 11) / 5, 3)
    assert out["sot_l5"] == round((2 + 4 + 1 + 3 + 4) / 5, 3)


def test_resolve_pick_side_under_over_btts_corners():
    assert _resolve_pick_side({"market": "OVER_2_5", "selection": "OVER"}) == "OVER"
    assert _resolve_pick_side({"market": "UNDER_3_5"}) == "UNDER"
    assert _resolve_pick_side({"market": "BTTS_YES"}) == "BTTS"
    assert _resolve_pick_side({"market": "CORNERS_OVER_9_5", "selection": "OVER"}) == "CORNERS"
    assert _resolve_pick_side(None) is None
    assert _resolve_pick_side({"market": "MONEYLINE_HOME"}) == "MONEYLINE_HOME"


def test_attach_phaseF58_failsoft_when_no_pick():
    audit = attach_football_profile_cross_to_payload(None, {})
    assert audit["available"] is False
    assert audit["_reason"] == "no_pick_payload"


def test_attach_phaseF58_strong_under_supports_under_pick():
    # Build a fake match with both teams cold+tight.
    match = {
        "home_team": {
            "context": {
                "recent_fixtures": {
                    "gf": [0, 1, 0, 1, 0, 1, 1, 1, 1, 1, 2, 1, 1, 1, 2],
                    "ga": [0, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1],
                }
            }
        },
        "away_team": {
            "context": {
                "recent_fixtures": {
                    "gf": [0, 0, 1, 1, 0, 1, 1, 1, 2, 1, 1, 2, 1, 1, 1],
                    "ga": [1, 0, 1, 1, 0, 1, 1, 1, 1, 2, 1, 1, 1, 1, 2],
                }
            }
        },
    }
    pick_payload = {
        "recommendation": {
            "market":           "UNDER_2_5",
            "selection":        "UNDER",
            "confidence_score": 60.0,
        },
        "fragility": {"score": 40.0},
    }
    audit = attach_football_profile_cross_to_payload(pick_payload, match)
    assert audit["available"] is True
    # The synthetic data should trigger STRONG_UNDER profile.
    assert audit["profile"] == "STRONG_UNDER_CROSS"
    assert audit["supports"] == "UNDER"
    # Pick was UNDER → SUPPORTS_PICK → confidence should have risen.
    assert pick_payload["recommendation"]["confidence_score"] > 60.0
    # Visual entry was added.
    pa = pick_payload.get("pattern_alignment") or {}
    assert any(
        (isinstance(e, dict) and e.get("source") == "football_team_profile_cross")
        for e in pa.get("entries", [])
    )
    # Override should NOT trigger because pick already aligned with cross.
    cra = pick_payload.get("football_profile_cross_applied") or {}
    assert (cra.get("override") is None) or (cra["override"].get("enabled") is False)


def test_attach_phaseF58_emits_override_when_strong_contradicts():
    # Both teams hot offense + leaky defense → STRONG_OVER cross.
    match = {
        "home_team": {
            "context": {
                "recent_fixtures": {
                    "gf": [3, 2, 3, 2, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                    "ga": [2, 3, 2, 3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                }
            }
        },
        "away_team": {
            "context": {
                "recent_fixtures": {
                    "gf": [3, 2, 3, 2, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                    "ga": [2, 3, 2, 3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                }
            }
        },
    }
    # Pick is UNDER but cross supports OVER → override should fire.
    pick_payload = {
        "recommendation": {
            "market":           "UNDER_2_5",
            "selection":        "UNDER",
            "confidence_score": 65.0,
        },
        "fragility": {"score": 40.0},
    }
    audit = attach_football_profile_cross_to_payload(pick_payload, match)
    assert audit["available"] is True
    assert audit["profile"] == "STRONG_OVER_CROSS"
    assert audit["interaction"] == "CONTRADICTS_PICK"
    assert audit["override"] is not None
    assert audit["override"]["enabled"] is True
    assert audit["override"]["recommended_market"] == "OVER_2_5"
    cra = pick_payload.get("football_profile_cross_applied") or {}
    assert cra.get("override", {}).get("enabled") is True


def test_attach_phaseF58_failsoft_when_no_recent_fixtures():
    match = {"home_team": {}, "away_team": {}}
    pick_payload = {
        "recommendation": {
            "market":           "OVER_2_5",
            "selection":        "OVER",
            "confidence_score": 55.0,
        },
    }
    audit = attach_football_profile_cross_to_payload(pick_payload, match)
    assert audit["available"] is False
    # confidence must not have been mutated.
    assert pick_payload["recommendation"]["confidence_score"] == 55.0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
