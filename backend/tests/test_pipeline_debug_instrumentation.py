"""Pipeline debug instrumentation tests.

Covers the per-stage funnel counters required by the user spec:

    provider_response_count
    raw_fixtures_count
    after_sport_filter_count
    after_date_window_count
    after_priority_league_filter_count
    after_status_filter_count
    after_market_filter_count
    analysis_candidates_count
    failure_stage

Plus the contract:
  * ``failure_stage`` points to the FIRST stage that dropped to 0.
  * Missing stages stay ``None`` (so the UI can distinguish "never
    reached" from "reached and zeroed out").
  * ``empty_debug_payload()`` returns a fully-populated zeroed payload
    for paths that bail out early.
"""
from __future__ import annotations

import pytest

from services.pipeline_debug import (
    ORDERED_STAGES,
    PipelineDebug,
    STAGE_AFTER_DATE_WINDOW,
    STAGE_AFTER_MARKET_FILTER,
    STAGE_AFTER_PRIORITY_LEAGUE,
    STAGE_AFTER_SPORT_FILTER,
    STAGE_AFTER_STATUS_FILTER,
    STAGE_ANALYSIS_CANDIDATES,
    STAGE_PROVIDER_RESPONSE,
    STAGE_RAW_FIXTURES,
    empty_debug_payload,
)


# =====================================================================
# Ordered-stage contract
# =====================================================================
class TestOrderedStages:
    def test_required_stage_keys_match_spec(self):
        # Each user-required stage MUST be in ORDERED_STAGES, in the
        # spec order (provider → raw → sport → date → league → status →
        # market → analysis).
        assert ORDERED_STAGES == (
            "provider_response",
            "raw_fixtures",
            "after_sport_filter",
            "after_date_window",
            "after_priority_league_filter",
            "after_status_filter",
            "after_market_filter",
            "analysis_candidates",
        )


# =====================================================================
# Record / serialise
# =====================================================================
class TestRecordAndSerialise:
    def test_each_stage_serialises_with_correct_key(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 42)
        dbg.record(STAGE_RAW_FIXTURES, 42)
        dbg.record(STAGE_AFTER_SPORT_FILTER, 42)
        dbg.record(STAGE_AFTER_DATE_WINDOW, 38)
        dbg.record(STAGE_AFTER_PRIORITY_LEAGUE, 12)
        dbg.record(STAGE_AFTER_STATUS_FILTER, 10)
        dbg.record(STAGE_AFTER_MARKET_FILTER, 8)
        dbg.record(STAGE_ANALYSIS_CANDIDATES, 8)
        out = dbg.to_dict()
        # Every required ``*_count`` key is present and integer.
        for key in (
            "provider_response_count",
            "raw_fixtures_count",
            "after_sport_filter_count",
            "after_date_window_count",
            "after_priority_league_filter_count",
            "after_status_filter_count",
            "after_market_filter_count",
            "analysis_candidates_count",
        ):
            assert key in out, f"Missing required key: {key}"
            assert isinstance(out[key], int), f"{key} must be int"
        # No funnel failure when every stage stayed > 0.
        assert out["failure_stage"] is None
        assert out["failure_message"] is None

    def test_negative_count_coerced_to_zero(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, -5)
        assert dbg.to_dict()["provider_response_count"] == 0

    def test_non_int_count_coerced_to_zero(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_RAW_FIXTURES, "not-a-number")
        assert dbg.to_dict()["raw_fixtures_count"] == 0

    def test_missing_stage_serialises_as_none(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 12)
        out = dbg.to_dict()
        assert out["provider_response_count"] == 12
        # All other stages were never recorded → ``None`` (NOT 0).
        assert out["raw_fixtures_count"] is None
        assert out["analysis_candidates_count"] is None
        # And the funnel does NOT flag a failure for un-reached stages.
        assert out["failure_stage"] is None


# =====================================================================
# Failure-stage detection
# =====================================================================
class TestFailureStageDetection:
    def test_first_zero_stage_is_the_failure(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 25)
        dbg.record(STAGE_RAW_FIXTURES, 25)
        dbg.record(STAGE_AFTER_SPORT_FILTER, 25)
        dbg.record(STAGE_AFTER_DATE_WINDOW, 0)        # ← first zero
        dbg.record(STAGE_AFTER_PRIORITY_LEAGUE, 0)
        dbg.record(STAGE_AFTER_STATUS_FILTER, 0)
        dbg.record(STAGE_AFTER_MARKET_FILTER, 0)
        dbg.record(STAGE_ANALYSIS_CANDIDATES, 0)
        out = dbg.to_dict()
        assert out["failure_stage"] == "after_date_window"
        assert "ventana de fechas" in (out["failure_message"] or "").lower()

    def test_provider_zero_yields_provider_failure_message(self):
        # Mirrors the exact user spec example: the provider returned nothing.
        dbg = PipelineDebug()
        for stage in ORDERED_STAGES:
            dbg.record(stage, 0)
        out = dbg.to_dict()
        assert out["failure_stage"] == "provider_response"
        # User-facing message must mention provider / fecha / deporte / caché.
        msg = (out["failure_message"] or "").lower()
        assert "provee" in msg or "provider" in msg
        for token in ("provider", "fecha", "deporte", "cach"):
            assert token in msg, f"Failure message must mention {token!r}; got {msg!r}"

    def test_no_failure_when_all_positive(self):
        dbg = PipelineDebug()
        for stage, cnt in zip(ORDERED_STAGES, [50, 50, 50, 38, 12, 10, 8, 8]):
            dbg.record(stage, cnt)
        out = dbg.to_dict()
        assert out["failure_stage"] is None
        assert out["failure_message"] is None

    def test_status_filter_failure(self):
        # Realistic case: provider/raw/date/league all positive,
        # but the fixture gate killed everything.
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 30)
        dbg.record(STAGE_RAW_FIXTURES, 30)
        dbg.record(STAGE_AFTER_SPORT_FILTER, 30)
        dbg.record(STAGE_AFTER_DATE_WINDOW, 22)
        dbg.record(STAGE_AFTER_PRIORITY_LEAGUE, 15)
        dbg.record(STAGE_AFTER_STATUS_FILTER, 0)
        dbg.record(STAGE_AFTER_MARKET_FILTER, 0)
        dbg.record(STAGE_ANALYSIS_CANDIDATES, 0)
        out = dbg.to_dict()
        assert out["failure_stage"] == "after_status_filter"


# =====================================================================
# Audit trail
# =====================================================================
class TestAuditTrail:
    def test_each_record_appends_audit_entry(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 25, note="from priority discovery")
        dbg.record(STAGE_RAW_FIXTURES, 25)
        audit = dbg.to_dict()["stages"]
        assert len(audit) == 2
        assert audit[0]["stage"] == "provider_response"
        assert audit[0]["note"] == "from priority discovery"
        assert audit[1]["stage"] == "raw_fixtures"

    def test_audit_marks_first_occurrence_correctly(self):
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 25)
        dbg.record(STAGE_PROVIDER_RESPONSE, 30)  # overwrite
        audit = dbg.to_dict()["stages"]
        assert audit[0]["first"] is True
        assert audit[1]["first"] is False


# =====================================================================
# empty_debug_payload helper
# =====================================================================
class TestEmptyDebugPayload:
    def test_returns_all_zero_counts(self):
        out = empty_debug_payload()
        for key in (
            "provider_response_count",
            "raw_fixtures_count",
            "after_sport_filter_count",
            "after_date_window_count",
            "after_priority_league_filter_count",
            "after_status_filter_count",
            "after_market_filter_count",
            "analysis_candidates_count",
        ):
            assert out[key] == 0
        # The funnel failure points to the FIRST zero (provider).
        assert out["failure_stage"] == "provider_response"
        assert out["failure_message"]


# =====================================================================
# JSON round-trip — pipeline_meta must serialise cleanly
# =====================================================================
class TestJSONRoundTrip:
    def test_to_dict_is_json_serialisable(self):
        import json
        dbg = PipelineDebug()
        dbg.record(STAGE_PROVIDER_RESPONSE, 25, note="α β γ — niño")
        dbg.record(STAGE_ANALYSIS_CANDIDATES, 0)
        encoded = json.dumps(dbg.to_dict())
        decoded = json.loads(encoded)
        assert decoded["provider_response_count"] == 25
        assert decoded["analysis_candidates_count"] == 0
        assert decoded["failure_stage"] == "raw_fixtures"  # first 0 in order
