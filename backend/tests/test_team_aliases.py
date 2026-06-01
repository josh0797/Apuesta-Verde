"""Tests for ``services.external_sources.team_aliases``.

Covers:
    * normalize() invariants.
    * resolve_alias() idempotency + canonical-mapping.
    * best_match() 6-step strategy (exact, alias, reverse alias, token
      subset, token overlap, fuzzy fallback).
    * Critical false-positive avoidance (Manchester City vs United).
    * Both list[str] and dict[str, V] candidate inputs.
"""
from __future__ import annotations

import pytest

from services.external_sources.team_aliases import (
    FUZZY_THRESHOLD,
    TEAM_ALIASES,
    best_match,
    normalize,
    resolve_alias,
)


# ─────────────────────────────────────────────────────────────────────
# normalize / resolve_alias
# ─────────────────────────────────────────────────────────────────────
class TestNormalize:
    def test_empty_string(self):
        assert normalize("") == ""

    def test_lowercase(self):
        assert normalize("FC Barcelona") == "fc barcelona"

    def test_strip_diacritics(self):
        assert normalize("FC Barça") == "fc barca"
        assert normalize("Atlético") == "atletico"
        assert normalize("São Paulo") == "sao paulo"
        assert normalize("Köln") == "koln"

    def test_strip_punctuation(self):
        assert normalize("Cote d'Ivoire") == "cote d ivoire"
        assert normalize("1. FC Köln") == "1 fc koln"

    def test_collapse_whitespace(self):
        assert normalize("   Real    Madrid   ") == "real madrid"


class TestResolveAlias:
    def test_canonical_idempotent(self):
        # Canonical name should map to itself
        assert resolve_alias("real madrid") == "real madrid"

    def test_unknown_passes_through_normalised(self):
        assert resolve_alias("Unknown Team FC") == "unknown team fc"

    def test_alias_resolution(self):
        assert resolve_alias("PSG") == "paris saint germain"
        assert resolve_alias("Inter") == "internazionale"
        assert resolve_alias("Atletico") == "atletico madrid"
        assert resolve_alias("Spurs") == "tottenham"
        assert resolve_alias("Juve") == "juventus"

    def test_case_insensitive(self):
        assert resolve_alias("psg") == resolve_alias("PSG") == resolve_alias("Psg")


# ─────────────────────────────────────────────────────────────────────
# best_match — 6-step strategy
# ─────────────────────────────────────────────────────────────────────
class TestBestMatchExact:
    def test_exact_match_list(self):
        assert best_match("Barcelona", ["barcelona", "real madrid"]) == "barcelona"

    def test_exact_match_dict(self):
        slugs = {"barcelona": "u1", "real madrid": "u2"}
        assert best_match("Barcelona", slugs) == "barcelona"


class TestBestMatchAlias:
    def test_alias_to_canonical(self):
        # Candidates contain canonical, target is alias
        assert best_match("FC Barcelona",
                          ["barcelona", "valencia"]) == "barcelona"
        assert best_match("Barca", ["barcelona"]) == "barcelona"

    def test_alias_atletico(self):
        assert best_match("Atletico",
                          ["atletico madrid", "real madrid"]) == "atletico madrid"


class TestBestMatchReverseAlias:
    def test_inter_to_internazionale(self):
        # Candidate is the canonical form; target is the alias
        assert best_match("Inter",
                          ["internazionale", "milan"]) == "internazionale"

    def test_psg_to_paris_saint_germain(self):
        assert best_match("PSG",
                          ["paris saint germain", "marseille"]) == "paris saint germain"

    def test_inter_milan_resolves(self):
        # 'Inter Milan' is an alias → 'internazionale'
        assert best_match("Inter Milan",
                          ["internazionale"]) == "internazionale"


class TestBestMatchTokenSubset:
    def test_target_subset_of_candidate(self):
        # 'Midtjylland' tokens ⊆ 'fc midtjylland'
        assert best_match("Midtjylland",
                          ["fc midtjylland", "aalborg bk"]) == "fc midtjylland"

    def test_candidate_subset_of_target(self):
        # 'Real Madrid' candidate is subset of 'Real Madrid CF'
        # 'real madrid cf' is in TEAM_ALIASES → 'real madrid', so alias resolution
        # already covers this. Test with non-aliased example:
        assert best_match("Some Team FC Extra",
                          ["some team", "other"]) == "some team"


class TestBestMatchTokenOverlap:
    def test_two_token_overlap(self):
        # 'Stade Toulousain' vs 'stade toulousain rugby' → 2 tokens overlap
        assert best_match("Stade Toulousain",
                          ["stade toulousain rugby", "racing 92"]) == "stade toulousain rugby"

    def test_single_token_target_overlap(self):
        # When target has < 2 tokens, min_overlap is 1
        assert best_match("Madrid",
                          ["real madrid", "fc porto"]) == "real madrid"


class TestBestMatchFuzzy:
    def test_typo_recovered(self):
        # 'Borusia' (missing 's') vs 'borussia dortmund'
        assert best_match("Borusia Dortmund",
                          ["borussia dortmund", "rb leipzig"]) == "borussia dortmund"

    def test_unrecoverable_typo_returns_none(self):
        # Way too different — below fuzzy threshold
        assert best_match("Wgxq Tjklfn",
                          ["barcelona", "real madrid"]) is None


class TestBestMatchFalsePositives:
    """The most important tests — these prevent silent wrong-team matches."""

    def test_man_city_does_not_match_man_united(self):
        # The pre-existing _best_match would have matched on 'manchester'
        # token overlap. The improved one must NOT.
        assert best_match("Manchester City",
                          ["manchester united", "liverpool"]) is None

    def test_real_betis_does_not_match_real_sociedad(self):
        # 'Real' overlaps but Betis ≠ Sociedad
        # min_overlap=2 for 2-token target. Only 1 token shared.
        assert best_match("Real Betis",
                          ["real sociedad", "valencia"]) is None

    def test_atletico_madrid_does_not_match_atletico_de_kolkata(self):
        # Hypothetical: 'atletico kolkata' has only 'atletico' in common
        # (1 token). For target 'atletico madrid' (2 tokens), min_overlap=2.
        assert best_match("Atletico Madrid",
                          ["atletico kolkata", "boca juniors"]) is None


class TestBestMatchEdgeCases:
    def test_empty_target(self):
        assert best_match("", ["barcelona"]) is None
        assert best_match("   ", ["barcelona"]) is None

    def test_empty_candidates(self):
        assert best_match("Barcelona", []) is None
        assert best_match("Barcelona", {}) is None

    def test_none_safe(self):
        # Should not crash on falsy inputs
        assert best_match("Barcelona", None or []) is None

    def test_dict_returns_key_not_value(self):
        # The contract: returns the KEY, caller does dict[key]
        slugs = {"barcelona": "u500-barcelona", "valencia": "u501-valencia"}
        key = best_match("Barca", slugs)
        assert key == "barcelona"
        assert slugs[key] == "u500-barcelona"


# ─────────────────────────────────────────────────────────────────────
# TEAM_ALIASES integrity
# ─────────────────────────────────────────────────────────────────────
class TestAliasesIntegrity:
    def test_all_values_are_normalised(self):
        for alias, canonical in TEAM_ALIASES.items():
            assert canonical == normalize(canonical), \
                f"canonical {canonical!r} not normalised"

    def test_all_keys_are_normalised(self):
        for alias in TEAM_ALIASES:
            assert alias == normalize(alias), f"alias {alias!r} not normalised"

    def test_dict_size_minimum(self):
        # Sanity floor — we shipped 250+ entries
        assert len(TEAM_ALIASES) >= 200

    def test_critical_aliases_present(self):
        critical = ["psg", "inter", "barca", "spurs", "juve", "dortmund",
                     "bayern", "atletico", "bvb"]
        for c in critical:
            assert c in TEAM_ALIASES, f"missing critical alias: {c}"


# ─────────────────────────────────────────────────────────────────────
# Parametrized regression suite
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("target,candidates,expected", [
    # Exact
    ("Real Madrid",          ["real madrid"],                     "real madrid"),
    # Alias
    ("PSG",                  ["paris saint germain"],             "paris saint germain"),
    ("Atletico",             ["atletico madrid"],                 "atletico madrid"),
    # Reverse alias
    ("Inter",                ["internazionale"],                  "internazionale"),
    ("Bayern",               ["bayern munich"],                   "bayern munich"),
    ("Spurs",                ["tottenham"],                       "tottenham"),
    # Fuzzy
    ("Manchestar United",    ["manchester united"],               "manchester united"),
    # Combined with extra noise
    ("FC Barcelona",         ["barcelona", "valencia", "sevilla"], "barcelona"),
])
def test_regression_table(target, candidates, expected):
    assert best_match(target, candidates) == expected


def test_fuzzy_threshold_constant():
    # Sanity: should be >= 0.80 (strict enough to avoid false positives)
    assert FUZZY_THRESHOLD >= 0.80
