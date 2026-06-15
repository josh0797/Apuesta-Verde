"""Bugfix tests — drop finished / postponed matches from "upcoming" pool.

Re-raised by the user after multiple production reports of finished
matches (e.g. Bournemouth vs Manchester City, Genk vs Antwerp, Hapoel
Beer Sheva vs Maccabi Tel Aviv) leaking into the analysis pipeline.

The fix introduces ``server._is_match_upcoming`` and
``server._filter_upcoming_candidates``: any match with a terminal
``status_short`` (``FT``/``AET``/``PEN``/``PST``/``CANC`` …) or a
terminal long-form ``status`` MUST be dropped, regardless of how its
``kickoff_ts`` was persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server import (
    _filter_upcoming_candidates,
    _is_match_upcoming,
    _TERMINAL_FOOTBALL_STATUSES,
    _TERMINAL_GENERIC_STATUSES,
)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _doc(**kw) -> dict:
    base = {
        "sport":        "football",
        "match_id":     "x",
        "is_live":      False,
        "kickoff_ts":   _now_ts() + 3600,  # +1h future
        "status_short": "NS",
        "status":       "Not Started",
        "home_team":    {"name": "H"},
        "away_team":    {"name": "A"},
    }
    base.update(kw)
    return base


class TestIsMatchUpcoming:
    def test_future_kickoff_with_ns_status_is_upcoming(self):
        assert _is_match_upcoming(_doc()) is True

    def test_past_kickoff_is_dropped(self):
        # Match started 2 hours ago — beyond 10 min grace.
        d = _doc(kickoff_ts=_now_ts() - 7200)
        assert _is_match_upcoming(d) is False

    def test_recently_started_within_grace_is_kept(self):
        # Started 5 minutes ago, status still NS (haven't refreshed yet).
        d = _doc(kickoff_ts=_now_ts() - 300)
        assert _is_match_upcoming(d) is True

    @pytest.mark.parametrize("status_short", sorted(_TERMINAL_FOOTBALL_STATUSES))
    def test_terminal_status_short_is_always_dropped(self, status_short):
        # Even with a future kickoff_ts, a finished match must be filtered.
        d = _doc(kickoff_ts=_now_ts() + 3600, status_short=status_short)
        assert _is_match_upcoming(d) is False, status_short

    @pytest.mark.parametrize("status_short", ["ft", "Ft", "fT"])
    def test_terminal_status_short_case_insensitive(self, status_short):
        d = _doc(status_short=status_short)
        assert _is_match_upcoming(d) is False

    @pytest.mark.parametrize("status_long", sorted(_TERMINAL_GENERIC_STATUSES))
    def test_terminal_long_status_is_dropped(self, status_long):
        # status_short might be empty (TheStatsAPI/ESPN paths) but
        # status long form says "Final" / "Postponed" / "Cancelled" — must drop.
        d = _doc(status_short="", status=status_long)
        assert _is_match_upcoming(d) is False, status_long

    def test_dict_status_with_terminal_value_dropped(self):
        # Some legacy MLB docs nest the status under a dict.
        d = _doc(status_short="", status={"abstract": "Final", "detailed": "Final"})
        assert _is_match_upcoming(d) is False

    def test_legacy_doc_without_status_fields_passes_if_future(self):
        # Backward-compat: pre-status docs only had kickoff_ts.
        d = {
            "sport":      "football",
            "match_id":   "legacy-1",
            "is_live":    False,
            "kickoff_ts": _now_ts() + 1800,
        }
        assert _is_match_upcoming(d) is True

    def test_invalid_kickoff_ts_dropped(self):
        d = _doc(kickoff_ts="not-a-number")
        assert _is_match_upcoming(d) is False

    def test_final_score_persisted_with_past_kickoff_drops(self):
        # Status fields stayed at "NS" by mistake — the home/away scores
        # are the last-line-of-defence signal.
        d = _doc(kickoff_ts=_now_ts() - 7200, status_short="NS",
                  home_score=2, away_score=1)
        assert _is_match_upcoming(d) is False

    def test_final_score_in_team_dict_drops(self):
        d = _doc(
            kickoff_ts=_now_ts() - 7200, status_short="NS",
            home_team={"name": "H", "score": 1},
            away_team={"name": "A", "score": 0},
        )
        assert _is_match_upcoming(d) is False

    def test_non_dict_input_returns_false(self):
        assert _is_match_upcoming(None) is False
        assert _is_match_upcoming("string") is False
        assert _is_match_upcoming(42) is False


class TestFilterUpcomingCandidates:
    def test_empty_input_returns_input(self):
        assert _filter_upcoming_candidates([]) == []

    def test_mixed_list_drops_only_finished(self):
        good1 = _doc(match_id="g1", kickoff_ts=_now_ts() + 3600)
        bad_ft = _doc(match_id="ft", status_short="FT")
        bad_pst = _doc(match_id="pst", status_short="PST")
        good2 = _doc(match_id="g2", kickoff_ts=_now_ts() + 7200)
        bad_old = _doc(match_id="old", kickoff_ts=_now_ts() - 9000)

        kept = _filter_upcoming_candidates([good1, bad_ft, bad_pst, good2, bad_old])
        kept_ids = {k["match_id"] for k in kept}
        assert kept_ids == {"g1", "g2"}

    def test_reported_real_world_finished_matches_are_dropped(self):
        # Matches the user reported as wrongly appearing in
        # "Descartados de ligas prioritarias" while already finished.
        finished_examples = [
            _doc(match_id="bournemouth-mancity",
                 home_team={"name": "Bournemouth"},
                 away_team={"name": "Manchester City"},
                 status_short="FT", kickoff_ts=_now_ts() - 3600,
                 home_score=1, away_score=3),
            _doc(match_id="genk-antwerp",
                 home_team={"name": "Genk"}, away_team={"name": "Antwerp"},
                 status_short="FT", kickoff_ts=_now_ts() - 5400,
                 home_score=2, away_score=2),
            _doc(match_id="hapoel-maccabi",
                 home_team={"name": "Hapoel Beer Sheva"},
                 away_team={"name": "Maccabi Tel Aviv"},
                 status_short="FT", kickoff_ts=_now_ts() - 8000,
                 home_score=0, away_score=1),
            _doc(match_id="ried-wolfsberger",
                 home_team={"name": "Ried"},
                 away_team={"name": "Wolfsberger AC"},
                 status_short="FT", kickoff_ts=_now_ts() - 3600,
                 home_score=1, away_score=2),
        ]
        future_safe = _doc(match_id="future-1", kickoff_ts=_now_ts() + 7200)
        kept = _filter_upcoming_candidates([*finished_examples, future_safe])
        assert kept == [future_safe]

    def test_preserves_input_order(self):
        a = _doc(match_id="a", kickoff_ts=_now_ts() + 1000)
        b = _doc(match_id="b", kickoff_ts=_now_ts() + 2000)
        c = _doc(match_id="c", kickoff_ts=_now_ts() + 3000)
        kept = _filter_upcoming_candidates([a, b, c])
        assert [m["match_id"] for m in kept] == ["a", "b", "c"]

    def test_grace_window_param_is_honoured(self):
        # 5-minute-old kickoff, status NS, grace 60s → should drop.
        d = _doc(kickoff_ts=_now_ts() - 300)
        kept = _filter_upcoming_candidates([d], grace_seconds=60)
        assert kept == []
        # Same doc with default grace (600s) — should be kept.
        kept2 = _filter_upcoming_candidates([d])
        assert kept2 == [d]
