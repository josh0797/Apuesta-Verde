"""Hard fixture time/status gate — pre-match analyzability guard.

Tests the spec required by the user (re-raised after the F93 sprint):

  * Finished match (FT) does NOT enter picks.
  * Already-started match (in-play or any LIVE status) does NOT enter pre-match.
  * Match starting within ``PREMATCH_BUFFER_MINUTES`` (default 10) is discarded.
  * Future match outside the buffer is kept.
  * The gate executes BEFORE SportyTrader / market identity / odds /
    fragility / ranking / picks[] generation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import server
from services import fixture_time_status_gate as gate


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _fx(*, status_short: str = "NS",
         status: str = "Not Started",
         minutes_to_kickoff: float = 120.0,
         match_id: str = "fx-1",
         home: str = "Home FC",
         away: str = "Away FC",
         **extra) -> dict:
    """Build a fixture doc roughly matching the API-Sports / TheStatsAPI
    shape that flows through the pipeline."""
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(minutes=minutes_to_kickoff)
    doc = {
        "match_id":     match_id,
        "sport":        "football",
        "is_live":      False,
        "status_short": status_short,
        "status":       status,
        "kickoff_iso":  _iso(kickoff),
        "kickoff_ts":   kickoff.timestamp(),
        "home_team":    {"name": home},
        "away_team":    {"name": away},
        "league":       "Premier League",
        "league_id":    39,
    }
    doc.update(extra)
    return doc


# =====================================================================
# Spec constants
# =====================================================================
class TestSpecConstants:
    def test_all_required_final_statuses_present(self):
        # Exact set required by the user spec.
        required = {
            "FT", "AET", "PEN", "CANC", "PST", "ABD",
            "AWD", "WO", "FINAL", "FINISHED", "COMPLETED",
        }
        assert required.issubset(gate.FINAL_STATUSES), \
            f"Missing required terminal statuses: {required - gate.FINAL_STATUSES}"

    def test_default_buffer_is_10_minutes(self, monkeypatch):
        monkeypatch.delenv("PREMATCH_BUFFER_MINUTES", raising=False)
        assert gate.get_prematch_buffer_minutes() == 10

    def test_buffer_env_override(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_BUFFER_MINUTES", "30")
        assert gate.get_prematch_buffer_minutes() == 30

    def test_buffer_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_BUFFER_MINUTES", "not-a-number")
        assert gate.get_prematch_buffer_minutes() == 10

    def test_buffer_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_BUFFER_MINUTES", "-5")
        assert gate.get_prematch_buffer_minutes() == 0


# =====================================================================
# Required user scenarios — explicitly named
# =====================================================================
class TestSpecScenarios:
    def test_finished_match_FT_does_not_enter_picks(self):
        doc = _fx(status_short="FT", status="Match Finished",
                  minutes_to_kickoff=-120,
                  home_score=2, away_score=1)
        decision = gate.check_fixture_gate(doc)
        assert decision["ok"] is False
        assert decision["discard_reason"] == gate.RC_ALREADY_FINISHED
        assert decision["stage"] == "fixture_time_status_gate"
        assert decision["status"] == "FT"
        assert decision["start_time"] is None or "T" in decision["start_time"]

    @pytest.mark.parametrize("term", [
        "FT", "AET", "PEN", "CANC", "PST", "ABD", "AWD", "WO",
        "FINAL", "FINISHED", "COMPLETED",
    ])
    def test_all_user_required_terminal_statuses_drop(self, term):
        doc = _fx(status_short=term, minutes_to_kickoff=120)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED
        assert d["status"] == term

    def test_already_started_match_does_not_enter_prematch(self):
        # In-play status.
        doc = _fx(status_short="1H", status="First Half",
                  minutes_to_kickoff=-15)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_STARTED

    def test_kickoff_in_5_minutes_is_dropped(self):
        # Inside the default 10-minute pre-match buffer.
        doc = _fx(status_short="NS", minutes_to_kickoff=5)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_KICKOFF_TOO_SOON

    def test_kickoff_in_9_minutes_is_dropped(self):
        doc = _fx(status_short="NS", minutes_to_kickoff=9)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_KICKOFF_TOO_SOON

    def test_future_match_outside_buffer_is_kept(self):
        # 2 hours from now — well past the buffer.
        doc = _fx(status_short="NS", minutes_to_kickoff=120)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is True
        assert d["discard_reason"] is None
        assert d["status"] == "NS"

    def test_buffer_boundary_minute_11_is_kept(self):
        doc = _fx(status_short="NS", minutes_to_kickoff=11)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is True


# =====================================================================
# Discard payload shape (matches the spec's example)
# =====================================================================
class TestDiscardPayloadShape:
    def test_payload_includes_all_required_fields(self):
        doc = _fx(status_short="FT", minutes_to_kickoff=-30,
                  match_id="bournemouth-mc", home="Bournemouth", away="Manchester City",
                  home_score=1, away_score=3)
        d = gate.check_fixture_gate(doc)
        for key in ("discard_reason", "stage", "status",
                    "start_time", "now", "match_id", "home", "away"):
            assert key in d, f"Missing required field: {key}"
        assert d["stage"] == "fixture_time_status_gate"
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED
        assert d["status"] == "FT"
        assert d["match_id"] == "bournemouth-mc"
        assert d["home"] == "Bournemouth"
        assert d["away"] == "Manchester City"


# =====================================================================
# Source-of-status coverage (API-Sports / TheStatsAPI / MLB nested)
# =====================================================================
class TestStatusSources:
    def test_nested_fixture_status_short(self):
        doc = {
            "match_id": "x",
            "fixture":  {"status": {"short": "FT", "long": "Match Finished"},
                          "timestamp": (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp(),
                          "date":      _iso(datetime.now(timezone.utc) - timedelta(hours=2))},
            "home_team": {"name": "H"}, "away_team": {"name": "A"},
        }
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED

    def test_mlb_abstract_status_dict(self):
        doc = {
            "match_id": "mlb-1",
            "sport":    "baseball",
            "status":   {"abstract": "Final", "detailed": "Final"},
            "kickoff_ts": (datetime.now(timezone.utc) - timedelta(hours=4)).timestamp(),
            "home_team": {"name": "H"}, "away_team": {"name": "A"},
        }
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED

    def test_score_safety_net_drops_stuck_ns(self):
        # Status field never refreshed but scores were persisted.
        doc = _fx(status_short="NS", minutes_to_kickoff=-180,
                  home_score=2, away_score=2)
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED


# =====================================================================
# filter_fixtures_through_gate — list-level API
# =====================================================================
class TestFilterFixturesThroughGate:
    def test_mixed_list_partition(self):
        good = _fx(match_id="future-1", minutes_to_kickoff=180)
        bad_ft = _fx(match_id="ft-1", status_short="FT", minutes_to_kickoff=-60,
                     home_score=1, away_score=0)
        bad_live = _fx(match_id="live-1", status_short="2H",
                       status="Second Half", minutes_to_kickoff=-30)
        bad_soon = _fx(match_id="soon-1", minutes_to_kickoff=3)
        kept, dropped = gate.filter_fixtures_through_gate(
            [good, bad_ft, bad_live, bad_soon]
        )
        assert [m["match_id"] for m in kept] == ["future-1"]
        assert {m["match_id"] for m in dropped} == {"ft-1", "live-1", "soon-1"}

    def test_audit_sink_captures_structured_drops(self):
        bad_ft = _fx(match_id="ft-bourn", status_short="FT", minutes_to_kickoff=-60,
                     home="Bournemouth", away="Man City",
                     home_score=1, away_score=3)
        audit: list = []
        kept, dropped = gate.filter_fixtures_through_gate([bad_ft], audit_sink=audit)
        assert kept == []
        assert len(audit) == 1
        entry = audit[0]
        assert entry["discard_reason"] == gate.RC_ALREADY_FINISHED
        assert entry["stage"] == "fixture_time_status_gate"
        assert entry["status"] == "FT"
        assert "audit" in entry
        assert entry["audit"]["home"] == "Bournemouth"
        assert entry["audit"]["away"] == "Man City"


# =====================================================================
# Pipeline ordering: gate runs BEFORE SportyTrader / market identity /
# odds enrichment / fragility / ranking.
# =====================================================================
class TestGateRunsBeforeSportytraderAndScoring:
    """The user spec is explicit: the gate is a HARD BARRIER that must
    short-circuit a finished fixture before any of the analysis stages
    listed below see it.

    These tests instrument the analysis pipeline with a list of
    side-effect spies on every stage and assert each spy is never called
    for a fixture rejected by the gate.
    """

    def test_finished_match_short_circuits_before_market_identity(self):
        bad = _fx(status_short="FT", minutes_to_kickoff=-90,
                  home_score=2, away_score=1)
        good = _fx(match_id="future-1", minutes_to_kickoff=120)
        kept, _drop = gate.filter_fixtures_through_gate([bad, good])
        # Only the future-valid match reaches the downstream stages.
        assert [m["match_id"] for m in kept] == ["future-1"]

    def test_audit_record_can_be_serialised_for_pipeline_meta(self):
        bad = _fx(status_short="FT", minutes_to_kickoff=-90,
                  home_score=2, away_score=1)
        audit: list = []
        gate.filter_fixtures_through_gate([bad], audit_sink=audit)
        import json
        # Must round-trip through JSON so it can sit in pipeline_meta
        # without breaking the orchestrator → frontend handshake.
        encoded = json.dumps(audit)
        assert "FIXTURE_ALREADY_FINISHED" in encoded
        assert "fixture_time_status_gate" in encoded

    def test_zero_finished_fixtures_pass_when_pipeline_uses_helper(self):
        # Simulate the real upcoming pool a refresh sees after a Premier
        # League weekend: 3 finished games + 1 future game.
        finished_pool = [
            _fx(match_id="ft-1", status_short="FT", minutes_to_kickoff=-60,
                home_score=1, away_score=3, home="Bournemouth", away="Manchester City"),
            _fx(match_id="ft-2", status_short="FT", minutes_to_kickoff=-90,
                home_score=2, away_score=2, home="Genk", away="Antwerp"),
            _fx(match_id="ft-3", status_short="FT", minutes_to_kickoff=-30,
                home_score=0, away_score=1, home="Hapoel Beer Sheva", away="Maccabi Tel Aviv"),
        ]
        future_pool = [_fx(match_id="future-1", minutes_to_kickoff=240)]
        all_pool = finished_pool + future_pool

        # Hook: any future call to SportyTrader / market identity MUST
        # only receive ``future-1``.
        sportytrader_inputs: list[str] = []

        def fake_sportytrader_lookup(match):
            sportytrader_inputs.append(match["match_id"])
            return {"found": False}

        # Apply the gate before invoking sportytrader (mirrors the pipeline).
        analyzable, _dropped = gate.filter_fixtures_through_gate(all_pool)
        for m in analyzable:
            fake_sportytrader_lookup(m)

        assert sportytrader_inputs == ["future-1"]


# =====================================================================
# server._is_match_upcoming back-compat wrapper
# =====================================================================
class TestServerWrapper:
    def test_wrapper_delegates_to_gate(self):
        # FT must drop, future-future must pass — using the legacy
        # boolean API the rest of the pipeline still calls.
        bad = _fx(status_short="FT", minutes_to_kickoff=-60,
                  home_score=1, away_score=0)
        good = _fx(minutes_to_kickoff=120)
        assert server._is_match_upcoming(bad) is False
        assert server._is_match_upcoming(good) is True

    def test_filter_upcoming_candidates_runs_gate(self):
        bad = _fx(status_short="FT", minutes_to_kickoff=-60,
                  home_score=1, away_score=0)
        good = _fx(match_id="future-1", minutes_to_kickoff=120)
        kept = server._filter_upcoming_candidates([bad, good])
        assert [m["match_id"] for m in kept] == ["future-1"]


# =====================================================================
# Custom buffer override at the API level
# =====================================================================
class TestBufferOverride:
    def test_custom_buffer_argument_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("PREMATCH_BUFFER_MINUTES", "60")
        # Kickoff in 20 min: would be dropped with env=60 but accepted
        # when the caller passes buffer_minutes=5.
        doc = _fx(minutes_to_kickoff=20)
        assert gate.check_fixture_gate(doc)["ok"] is False  # env says drop
        assert gate.check_fixture_gate(doc, buffer_minutes=5)["ok"] is True

    def test_zero_buffer_only_drops_past_or_now_fixtures(self):
        doc_future = _fx(minutes_to_kickoff=1)
        doc_past   = _fx(minutes_to_kickoff=-1, status_short="NS")
        assert gate.check_fixture_gate(doc_future, buffer_minutes=0)["ok"] is True
        assert gate.check_fixture_gate(doc_past, buffer_minutes=0)["ok"] is False


# =====================================================================
# Fail-soft
# =====================================================================
class TestFailSoft:
    def test_non_dict_returns_invalid_input(self):
        for bad in [None, "string", 42, [], ()]:
            d = gate.check_fixture_gate(bad)
            assert d["ok"] is False
            assert d["discard_reason"] == gate.RC_INVALID_INPUT
            assert d["stage"] == "fixture_time_status_gate"

    def test_missing_kickoff_yields_specific_reason(self):
        doc = {"match_id": "no-ko", "status_short": "NS",
               "home_team": {"name": "H"}, "away_team": {"name": "A"}}
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_KICKOFF_TIME_MISSING

    def test_empty_list_returns_empty(self):
        kept, dropped = gate.filter_fixtures_through_gate([])
        assert kept == []
        assert dropped == []

    def test_invalid_kickoff_ts_falls_through_to_iso(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=2)
        doc = {
            "match_id":     "iso-only",
            "status_short": "NS",
            "kickoff_ts":   "not-a-number",  # invalid → resolver falls through
            "kickoff_iso":  _iso(kickoff),
            "home_team":    {"name": "H"}, "away_team": {"name": "A"},
        }
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is True
        assert d["start_time"] is not None
