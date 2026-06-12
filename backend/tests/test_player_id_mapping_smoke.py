"""Phase F68 — player_id_mapping smoke tests (pure scorer)."""
from __future__ import annotations

import pytest

from services.player_id_mapping import (
    _cache_key, _candidate_score, _normalise,
)


# ─────────────────────────────────────────────────────────────────────
# Normalisation
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Kylian Mbappé",      "kylian mbappe"),
    ("Karim Benzema",       "karim benzema"),
    ("João Félix",           "joao felix"),
    ("  Vinícius   Júnior ","vinicius junior"),
    ("Muñoz",               "munoz"),
    (None,                  ""),
    ("",                    ""),
])
def test_normalise_handles_accents_and_whitespace(raw, expected) -> None:
    assert _normalise(raw) == expected


# ─────────────────────────────────────────────────────────────────────
# Cache key
# ─────────────────────────────────────────────────────────────────────
def test_cache_key_with_and_without_team_hint() -> None:
    assert _cache_key("Kylian Mbappé", None)         == "kylian mbappe"
    assert _cache_key("Kylian Mbappé", "Real Madrid") == "kylian mbappe|real madrid"
    # Same name, different team → different cache key.
    assert _cache_key("Saka", "Arsenal") != _cache_key("Saka", "Tottenham")


# ─────────────────────────────────────────────────────────────────────
# Candidate scoring
# ─────────────────────────────────────────────────────────────────────
KYLIAN = {
    "id": "pl_57255528", "name": "Kylian Mbappé",
    "first_name": "Kylian", "last_name": "Mbappé",
    "current_team": {"id": "tm_999", "name": "Real Madrid"},
}
ETHAN = {
    "id": "pl_83067439", "name": "Ethan Mbappé",
    "first_name": "Ethan", "last_name": "Mbappé",
    "current_team": {"id": "tm_58884", "name": "Lille"},
}


def test_candidate_score_exact_full_name_wins() -> None:
    s_kylian = _candidate_score("Kylian Mbappé", KYLIAN, team_hint=None)
    s_ethan  = _candidate_score("Kylian Mbappé", ETHAN,  team_hint=None)
    assert s_kylian > s_ethan
    # Full-name match unlocks the highest bracket (≥ 200).
    assert s_kylian >= 200


def test_candidate_score_team_hint_breaks_tie_on_lastname() -> None:
    s_kylian = _candidate_score("Mbappé", KYLIAN, team_hint="Real Madrid")
    s_ethan  = _candidate_score("Mbappé", ETHAN,  team_hint="Real Madrid")
    assert s_kylian > s_ethan, "team_hint must disambiguate on shared last name"


def test_candidate_score_zero_when_no_overlap() -> None:
    other = {"id": "pl_1", "name": "Karim Benzema",
             "first_name": "Karim", "last_name": "Benzema"}
    assert _candidate_score("Lionel Messi", other, team_hint=None) == 0


def test_candidate_score_handles_missing_fields() -> None:
    broken = {"id": "pl_x"}    # no name fields at all
    assert _candidate_score("X", broken, team_hint=None) == 0


def test_candidate_score_empty_query() -> None:
    assert _candidate_score("", KYLIAN, team_hint=None) == 0
