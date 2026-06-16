"""Phase F94.4 — Tests for the LLM-hallucinated odds guard.

These tests reproduce the **user-reported bug**:
    Two completely unrelated football matches (France vs Senegal and
    Austria vs Jordan) both displayed ``Cuota detectada: 1.275`` because
    the LLM hallucinated the same ``odds_range`` placeholder when no
    real ``odds_snapshots`` existed for either match.

The guard ``suppress_llm_hallucinated_odds`` is the fix: scrub
``recommendation.odds_range`` (and any legacy top-level odds field) for
every pick whose underlying match lacks real upstream odds, stamping a
``LLM_ODDS_HALLUCINATION_SUPPRESSED_NO_UPSTREAM_ODDS`` reason code.
"""
from __future__ import annotations

import pytest

from services.football_llm_odds_hallucination_guard import (
    REASON_CODE,
    suppress_llm_hallucinated_odds,
    _match_has_real_odds,
    _build_match_index,
)
from services.football_market_trace import build_market_trace


# ─────────────────────────────────────────────────────────────────────
# 1. _match_has_real_odds — happy / sad paths
# ─────────────────────────────────────────────────────────────────────
class TestMatchHasRealOdds:
    def test_empty_snapshots_is_false(self):
        assert _match_has_real_odds({"match_id": 1}) is False
        assert _match_has_real_odds({"match_id": 1, "odds_snapshots": []}) is False
        assert _match_has_real_odds({"match_id": 1, "odds_snapshots": None}) is False

    def test_snapshots_with_empty_markets_is_false(self):
        m = {"match_id": 1, "odds_snapshots": [{"markets": {}}]}
        assert _match_has_real_odds(m) is False

    def test_snapshots_with_no_markets_key_is_false(self):
        m = {"match_id": 1, "odds_snapshots": [{"snapshot_at": "x"}]}
        assert _match_has_real_odds(m) is False

    def test_non_dict_input_is_false(self):
        assert _match_has_real_odds(None) is False
        assert _match_has_real_odds([]) is False
        assert _match_has_real_odds("string") is False

    def test_dict_markets_with_entries_is_true(self):
        m = {"match_id": 1,
             "odds_snapshots": [{"markets": {"Match Winner": [{"home": 2.1}]}}]}
        assert _match_has_real_odds(m) is True

    def test_list_markets_is_true_when_non_empty(self):
        m = {"match_id": 1, "odds_snapshots": [{"markets": [{"home": 2.1}]}]}
        assert _match_has_real_odds(m) is True

    def test_only_one_usable_snapshot_required(self):
        m = {"match_id": 1, "odds_snapshots": [
            {"markets": {}},
            {"markets": {"Match Winner": [{"home": 2.1}]}},
        ]}
        assert _match_has_real_odds(m) is True


# ─────────────────────────────────────────────────────────────────────
# 2. _build_match_index — tolerant lookup keys
# ─────────────────────────────────────────────────────────────────────
class TestBuildMatchIndex:
    def test_indexes_by_str_and_int_when_id_is_int(self):
        idx = _build_match_index([{"match_id": 1539000}])
        assert 1539000 in idx
        assert "1539000" in idx

    def test_indexes_by_int_when_id_is_digit_string(self):
        idx = _build_match_index([{"match_id": "1539000"}])
        assert "1539000" in idx
        assert 1539000 in idx

    def test_ignores_non_dict_and_missing_ids(self):
        idx = _build_match_index([{"match_id": 1}, None, {"foo": "bar"}, "x"])
        assert 1 in idx
        # Non-numeric / missing did not pollute the index
        assert "x" not in idx


# ─────────────────────────────────────────────────────────────────────
# 3. Core bug reproduction — France-Senegal + Austria-Jordan
#    Both matches LACK odds_snapshots; the LLM "guessed" the same
#    odds_range="1.25-1.30" for both. Guard must scrub them.
# ─────────────────────────────────────────────────────────────────────
class TestSuppressHallucinatedOdds_BugReproduction:
    @pytest.fixture
    def matches_payload_no_odds(self):
        return [
            {"match_id": 11, "match_label": "France vs Senegal",
             "odds_snapshots": []},
            {"match_id": 22, "match_label": "Austria vs Jordan",
             "odds_snapshots": []},
        ]

    @pytest.fixture
    def parsed_with_hallucinated_odds(self):
        return {
            "picks": [],
            "summary": {
                "requires_market_identity": [
                    {
                        "match_id":  11,
                        "match_label": "France vs Senegal",
                        "recommendation": {
                            "market":      None,
                            "selection":   None,
                            "odds_range":  "1.25-1.30",
                        },
                    },
                    {
                        "match_id":  22,
                        "match_label": "Austria vs Jordan",
                        "recommendation": {
                            "market":      None,
                            "selection":   None,
                            "odds_range":  "1.25-1.30",
                        },
                    },
                ],
            },
        }

    def test_both_entries_get_scrubbed_when_no_upstream_odds(
        self, parsed_with_hallucinated_odds, matches_payload_no_odds,
    ):
        meta = suppress_llm_hallucinated_odds(
            parsed_with_hallucinated_odds, matches_payload_no_odds,
        )
        bucket = parsed_with_hallucinated_odds["summary"]["requires_market_identity"]
        assert all(e["recommendation"]["odds_range"] is None for e in bucket)
        assert meta["available"] is True
        assert meta["suppressed_summary"] == {"requires_market_identity": 2}
        assert meta["matches_no_odds"] == 2
        assert meta["reason_code"] == REASON_CODE

    def test_each_scrubbed_entry_carries_audit_reason_code(
        self, parsed_with_hallucinated_odds, matches_payload_no_odds,
    ):
        suppress_llm_hallucinated_odds(
            parsed_with_hallucinated_odds, matches_payload_no_odds,
        )
        bucket = parsed_with_hallucinated_odds["summary"]["requires_market_identity"]
        for e in bucket:
            assert e["_odds_provenance"]["status"] == "SUPPRESSED_NO_UPSTREAM"
            assert e["_odds_provenance"]["reason_code"] == REASON_CODE
            assert REASON_CODE in e["reason_codes"]

    def test_trace_emits_null_odds_after_guard(
        self, parsed_with_hallucinated_odds, matches_payload_no_odds,
    ):
        """Integration check: post-guard, ``build_market_trace`` must
        return ``odds=None`` for each scrubbed entry — that is what
        actually drives the UI fix."""
        suppress_llm_hallucinated_odds(
            parsed_with_hallucinated_odds, matches_payload_no_odds,
        )
        bucket = parsed_with_hallucinated_odds["summary"]["requires_market_identity"]
        traces = [build_market_trace(e, sport="football") for e in bucket]
        assert all(t["odds"] is None for t in traces), (
            f"Expected odds=None on all entries, got: "
            f"{[t['odds'] for t in traces]}"
        )


# ─────────────────────────────────────────────────────────────────────
# 4. Back-compat: matches WITH real odds remain untouched
# ─────────────────────────────────────────────────────────────────────
class TestBackCompat_WhenRealOddsExist:
    def test_picks_with_real_odds_are_not_scrubbed(self):
        matches = [
            {"match_id": 100,
             "odds_snapshots": [{"markets": {"Match Winner": [{"home": 2.1}]}}]},
        ]
        parsed = {
            "picks": [{
                "match_id": 100,
                "match_label": "Real vs Barcelona",
                "recommendation": {"market": "Match Winner",
                                    "selection": "Real Madrid gana",
                                    "odds_range": "2.05-2.15"},
            }],
        }
        meta = suppress_llm_hallucinated_odds(parsed, matches)
        # NOT scrubbed.
        assert parsed["picks"][0]["recommendation"]["odds_range"] == "2.05-2.15"
        assert meta["suppressed_picks"] == 0
        assert meta["matches_no_odds"] == 0

    def test_mixed_matches_scrub_only_the_no_odds_ones(self):
        matches = [
            {"match_id": 100,
             "odds_snapshots": [{"markets": {"Match Winner": [{"home": 2.1}]}}]},
            {"match_id": 200, "odds_snapshots": []},
        ]
        parsed = {
            "picks": [
                {"match_id": 100, "recommendation": {"odds_range": "2.05-2.15"}},
                {"match_id": 200, "recommendation": {"odds_range": "1.25-1.30"}},
            ],
        }
        meta = suppress_llm_hallucinated_odds(parsed, matches)
        assert parsed["picks"][0]["recommendation"]["odds_range"] == "2.05-2.15"
        assert parsed["picks"][1]["recommendation"]["odds_range"] is None
        assert meta["suppressed_picks"] == 1
        assert meta["matches_no_odds"] == 1


# ─────────────────────────────────────────────────────────────────────
# 5. Robustness — never raises + idempotent
# ─────────────────────────────────────────────────────────────────────
class TestRobustness:
    def test_does_not_raise_on_empty_inputs(self):
        suppress_llm_hallucinated_odds({}, [])
        suppress_llm_hallucinated_odds(None, None)
        suppress_llm_hallucinated_odds({"picks": "not_a_list"}, [])

    def test_idempotent_running_twice_yields_same_state(self):
        matches = [{"match_id": 9, "odds_snapshots": []}]
        parsed = {"summary": {"discarded_market": [
            {"match_id": 9, "recommendation": {"odds_range": "1.40-1.55"}},
        ]}}
        meta1 = suppress_llm_hallucinated_odds(parsed, matches)
        # Second pass MUST NOT re-suppress (already None) but also MUST
        # NOT crash.
        meta2 = suppress_llm_hallucinated_odds(parsed, matches)
        assert meta1["suppressed_summary"] == {"discarded_market": 1}
        # Already cleared → 0 newly-modified entries.
        assert meta2.get("suppressed_summary", {}).get("discarded_market", 0) == 0
        assert (parsed["summary"]["discarded_market"][0]
                ["recommendation"]["odds_range"] is None)

    def test_unknown_match_ids_are_left_alone(self):
        matches = [{"match_id": 1, "odds_snapshots": []}]
        parsed = {"picks": [
            {"match_id": 999, "recommendation": {"odds_range": "1.25-1.30"}},
        ]}
        meta = suppress_llm_hallucinated_odds(parsed, matches)
        # No upstream match for match_id=999 → leave it untouched (safer
        # than mutating an entry we can't verify).
        assert parsed["picks"][0]["recommendation"]["odds_range"] == "1.25-1.30"
        assert meta["suppressed_picks"] == 0

    def test_legacy_top_level_odds_field_is_also_scrubbed(self):
        matches = [{"match_id": 5, "odds_snapshots": []}]
        parsed = {"picks": [
            {"match_id": 5,
             "odds": 1.275,
             "recommendation": {"odds_range": "1.25-1.30"}},
        ]}
        suppress_llm_hallucinated_odds(parsed, matches)
        assert parsed["picks"][0]["recommendation"]["odds_range"] is None
        assert parsed["picks"][0]["odds"] is None


# ─────────────────────────────────────────────────────────────────────
# 6. Anti-leak invariant — two matches with no odds MUST yield
#    distinct (and both None) trace.odds — never the same number.
# ─────────────────────────────────────────────────────────────────────
class TestAntiLeak:
    def test_two_no_odds_matches_never_share_a_fake_value(self):
        matches = [
            {"match_id": 1, "match_label": "A vs B", "odds_snapshots": []},
            {"match_id": 2, "match_label": "C vs D", "odds_snapshots": []},
        ]
        parsed = {"summary": {"requires_market_identity": [
            {"match_id": 1, "match_label": "A vs B",
             "recommendation": {"odds_range": "1.25-1.30"}},
            {"match_id": 2, "match_label": "C vs D",
             "recommendation": {"odds_range": "1.25-1.30"}},
        ]}}
        suppress_llm_hallucinated_odds(parsed, matches)
        bucket = parsed["summary"]["requires_market_identity"]
        traces = [build_market_trace(e, sport="football") for e in bucket]
        # Both odds must be None. NEVER a shared fake 1.275.
        assert traces[0]["odds"] is None
        assert traces[1]["odds"] is None
        # And the shared placeholder value must not appear ANYWHERE.
        for t in traces:
            assert t["odds"] != 1.275
            assert t["odds"] != 1.27
