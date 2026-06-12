"""Phase F67 — match_id mapping smoke tests."""
from __future__ import annotations

import pytest

from services.match_id_mapping import (
    _normalise_team_name,
    is_thestatsapi_id,
)


def test_is_thestatsapi_id_true_for_mt_prefix() -> None:
    assert is_thestatsapi_id("mt_511134637")    is True
    assert is_thestatsapi_id("mt_x")            is True
    assert is_thestatsapi_id("apisports_12345") is False
    assert is_thestatsapi_id("")                is False
    assert is_thestatsapi_id(None)              is False
    assert is_thestatsapi_id(12345)             is False


@pytest.mark.parametrize("raw,expected", [
    ("Real Madrid CF",  "real madrid"),
    ("real madrid",     "real madrid"),
    ("FC Barcelona",    "barcelona"),
    ("Manchester City", "manchester city"),
    ("AC Milan",        "milan"),
    ("AS Roma",         "roma"),
    (" arsenal ",       "arsenal"),
    ("",                ""),
    (None,              ""),
])
def test_normalise_team_name(raw, expected) -> None:
    assert _normalise_team_name(raw) == expected


def test_normalise_collapses_whitespace_and_lowercases() -> None:
    assert _normalise_team_name("  Real    Madrid CF  ") == "real madrid"
