"""Sprint-F99.3 · Editorial payload adapter (puro, F74→editorial-ready).

Guardas binding del usuario:

  1. Editorial adapter no ejecuta el builder ni consulta db.
  2. No consume payloads crudos.
  3. Odds y market identity quedan fuera del payload editorial.
  4. Feature flag permite rollout con fallback legacy.
  5. Uso del fallback legacy queda registrado.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.football_editorial_payload_adapter import (
    F99_ADAPTER_SCHEMA_VERSION,
    F99_FLAG_ENV_VAR,
    RC_F99_F74_ADAPTER_USED,
    RC_F99_LEGACY_FALLBACK_USED,
    RC_F99_PAYLOAD_INCOMPLETE,
    build_editorial_ready_match_payload_v2,
    is_f99_editorial_adapter_enabled,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Pureza — no IO ni builder
# ─────────────────────────────────────────────────────────────────────
def test_adapter_is_pure_does_not_execute_builder():
    """El adapter NO debe importar ni invocar ``build_football_data_enrichment``."""
    with patch(
        "services.football_enrichment_builder.build_football_data_enrichment",
        side_effect=AssertionError(
            "El adapter F99.3 NO debe ejecutar el builder."
        ),
    ):
        match = {
            "home_team": {"name": "Arsenal"},
            "away_team": {"name": "Liverpool"},
            "football_data_enrichment": {
                "available": True,
                "home": {"xg_for_l5": 1.5, "goals_scored_l5": 2.1, "corners_for_l5": 5.4},
                "away": {"xg_for_l5": 1.3, "goals_scored_l5": 1.8, "corners_for_l5": 4.9},
                "h2h": {"sample": 5},
            },
        }
        payload = build_editorial_ready_match_payload_v2(match)
        assert payload["_meta"]["adapter_path_used"] == "F99_F74"


def test_adapter_does_not_mutate_match():
    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {"xg_for_l5": 1.0},
            "away": {"xg_for_l5": 1.2},
        },
    }
    snapshot = dict(match)
    snapshot_dq = dict(match["football_data_enrichment"])
    _ = build_editorial_ready_match_payload_v2(match)
    assert match == snapshot
    assert match["football_data_enrichment"] == snapshot_dq


def test_adapter_does_not_consume_raw_payloads():
    """Cualquier ``_sofascore_raw`` / ``_thestatsapi_raw`` / raw HTML
    en el match doc debe ser IGNORADO. El adapter solo lee F74 + legacy planos.
    """
    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "_sofascore_raw":     {"event_id": 1, "home_form": []},
        "_thestatsapi_raw":   {"some": "raw"},
        "_corners_offline_seed_raw": {"home": None, "away": None},
        # NO F74 attached intentionally.
    }
    payload = build_editorial_ready_match_payload_v2(match)
    # No raw payloads in output.
    text = repr(payload)
    assert "_sofascore_raw"   not in payload
    assert "_thestatsapi_raw" not in payload
    assert "_corners_offline_seed_raw" not in payload
    assert "home_form" not in text


# ─────────────────────────────────────────────────────────────────────
# 2. Sin odds / market identity en el payload editorial
# ─────────────────────────────────────────────────────────────────────
def test_payload_never_contains_odds_or_market_identity():
    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "odds": {"match_winner": {"home": 1.85, "away": 2.10, "draw": 3.40}},
        "market_evaluated":     "Over 2.5",
        "market_identity_key":  "OVER_2_5",
        "edge":                  0.045,
        "expected_value":        0.123,
        "implied_probability":   0.54,
        "football_data_enrichment": {
            "available": True,
            "home": {
                "xg_for_l5": 1.5,
                "odds":            "leaked?",        # adversarial
                "evaluated_market": "MUST_NOT_LEAK",
            },
            "away": {"xg_for_l5": 1.3},
        },
    }
    payload = build_editorial_ready_match_payload_v2(match)
    for forbidden in (
        "odds", "evaluated_market", "market_identity_key", "market_evaluated",
        "edge", "ev", "expected_value", "implied_probability", "estimated_probability",
        "market_trap", "market_trap_score",
    ):
        assert forbidden not in payload, f"payload editorial leak: {forbidden}"
        assert forbidden not in payload["home"], f"home leak: {forbidden}"
        assert forbidden not in payload["away"], f"away leak: {forbidden}"

    # ``odds_available`` IS allowed (descriptive metadata).
    assert payload["_meta"]["odds_available"] is True


def test_missing_odds_does_not_lower_data_quality():
    """Binding guard #9: la ausencia de odds NO afecta data_quality."""
    enrichment = {
        "available": True,
        "home": {"xg_for_l5": 1.4, "goals_scored_l5": 2.0, "corners_for_l5": 5.3,
                  "btts_rate_l15": 0.6, "clean_sheets_l15": 0.4},
        "away": {"xg_for_l5": 1.2, "goals_scored_l5": 1.7, "corners_for_l5": 4.6,
                  "btts_rate_l15": 0.5, "clean_sheets_l15": 0.3},
    }
    # Match sin odds.
    match_no_odds  = {"home_team": {"name": "A"}, "away_team": {"name": "B"},
                       "football_data_enrichment": enrichment}
    # Match con odds.
    match_with_odds = {"home_team": {"name": "A"}, "away_team": {"name": "B"},
                        "odds": {"match_winner": {"home": 1.85}},
                        "football_data_enrichment": enrichment}
    p1 = build_editorial_ready_match_payload_v2(match_no_odds)
    p2 = build_editorial_ready_match_payload_v2(match_with_odds)
    assert p1["data_quality"] == p2["data_quality"]
    assert p1["data_quality"] in ("STRONG", "USABLE")


# ─────────────────────────────────────────────────────────────────────
# 3. Reason codes F99.3
# ─────────────────────────────────────────────────────────────────────
def test_reason_code_f74_adapter_used():
    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {"xg_for_l5": 1.5},
            "away": {"xg_for_l5": 1.3},
        },
    }
    p = build_editorial_ready_match_payload_v2(match)
    assert RC_F99_F74_ADAPTER_USED in p["reason_codes"]
    assert p["_meta"]["adapter_path_used"] == "F99_F74"


def test_reason_code_legacy_fallback_used_when_no_f74():
    match = {
        "home_team": {"name": "A", "xg_for_l5": 1.2, "btts_rate_l15": 0.6},
        "away_team": {"name": "B", "xg_for_l5": 1.0, "btts_rate_l15": 0.4},
    }
    p = build_editorial_ready_match_payload_v2(match)
    assert RC_F99_LEGACY_FALLBACK_USED in p["reason_codes"]
    assert RC_F99_F74_ADAPTER_USED     not in p["reason_codes"]
    assert p["_meta"]["adapter_path_used"] == "F99_LEGACY"
    # Legacy values landed in the projection.
    assert p["home"]["xg_for_l5"] == 1.2


def test_reason_code_payload_incomplete():
    """Match con nombres pero sin datos en absoluto."""
    match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    p = build_editorial_ready_match_payload_v2(match)
    assert RC_F99_PAYLOAD_INCOMPLETE in p["reason_codes"]
    assert p["_meta"]["adapter_path_used"] == "F99_NONE"


def test_f74_then_legacy_topup():
    """Si F74 cubre algunas métricas pero faltan otras, legacy hace top-up
    SIN sobre-escribir lo que F74 ya colocó."""
    match = {
        "home_team": {"name": "A", "btts_rate_l15": 0.65,  # legacy-only key
                       "xg_for_l5": 99.0,                   # tries to override F74
                       },
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {"xg_for_l5": 1.5},  # F74 value
            "away": {"xg_for_l5": 1.3},
        },
    }
    p = build_editorial_ready_match_payload_v2(match)
    assert p["home"]["xg_for_l5"]   == 1.5   # F74 wins
    assert p["home"]["btts_rate_l15"] == 0.65  # legacy top-up
    assert RC_F99_F74_ADAPTER_USED      in p["reason_codes"]
    assert RC_F99_LEGACY_FALLBACK_USED  in p["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 4. Feature flag
# ─────────────────────────────────────────────────────────────────────
def test_feature_flag_helper(monkeypatch):
    monkeypatch.delenv(F99_FLAG_ENV_VAR, raising=False)
    assert is_f99_editorial_adapter_enabled() is False
    monkeypatch.setenv(F99_FLAG_ENV_VAR, "true")
    assert is_f99_editorial_adapter_enabled() is True
    monkeypatch.setenv(F99_FLAG_ENV_VAR, "0")
    assert is_f99_editorial_adapter_enabled() is False


def test_data_completeness_routes_through_adapter_when_flag_on(monkeypatch):
    """When ``ENABLE_F99_EDITORIAL_F74_ADAPTER`` is on, the editorial
    ``_data_completeness`` should emit the F99 adapter reason codes."""
    from services.football_editorial_prediction import _data_completeness

    monkeypatch.setenv(F99_FLAG_ENV_VAR, "true")

    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {"xg_for_l5": 1.5, "goals_scored_l5": 2.0, "corners_for_l5": 5.0,
                      "btts_rate_l15": 0.6, "clean_sheets_l15": 0.4},
            "away": {"xg_for_l5": 1.3, "goals_scored_l5": 1.7, "corners_for_l5": 4.7,
                      "btts_rate_l15": 0.5},
            "h2h":  {"sample": 5},
        },
    }
    completeness = _data_completeness(match)
    assert completeness["f99_adapter_used"] is True
    assert RC_F99_F74_ADAPTER_USED in completeness["f99_editorial_reason_codes"]
    assert completeness["data_quality"] in ("USABLE", "STRONG")


def test_data_completeness_legacy_when_flag_off(monkeypatch):
    """When the flag is off, the F99 adapter is NOT invoked."""
    from services.football_editorial_prediction import _data_completeness
    monkeypatch.delenv(F99_FLAG_ENV_VAR, raising=False)

    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {"xg_for_l5": 1.5},
            "away": {"xg_for_l5": 1.3},
        },
    }
    completeness = _data_completeness(match)
    assert completeness["f99_adapter_used"] is False
    assert completeness["f99_editorial_reason_codes"] == []


# ─────────────────────────────────────────────────────────────────────
# 5. Whitelist estricto — solo whitelisted side metrics fluyen
# ─────────────────────────────────────────────────────────────────────
def test_unknown_keys_in_f74_side_block_are_filtered():
    match = {
        "home_team": {"name": "A"},
        "away_team": {"name": "B"},
        "football_data_enrichment": {
            "available": True,
            "home": {
                "xg_for_l5":           1.5,
                "experimental_metric": "leak?",
                "raw_response":        {"big": "blob"},
            },
            "away": {"xg_for_l5": 1.3},
        },
    }
    p = build_editorial_ready_match_payload_v2(match)
    assert p["home"]["xg_for_l5"]            == 1.5
    assert "experimental_metric"             not in p["home"]
    assert "raw_response"                    not in p["home"]


def test_schema_version_emitted():
    p = build_editorial_ready_match_payload_v2({})
    assert p["schema_version"] == F99_ADAPTER_SCHEMA_VERSION


def test_invalid_match_input_returns_safe_payload():
    p = build_editorial_ready_match_payload_v2(None)
    assert p["data_quality"] == "THIN"
    assert RC_F99_PAYLOAD_INCOMPLETE in p["reason_codes"]
    assert p["home"] == {} and p["away"] == {}
    assert p["_meta"]["odds_available"] is False
