"""Sprint D9.3-A · mlb_active_series_analyzer hotfix tests.

These tests cover the regression that produced phantom "G1: Team 0 - 0
Team" entries in the UI's "Contexto de serie activa" badge, contaminating
the projected expected runs via `apply_series_degradation`.

Covered cases
-------------
* Strict score parsing — `path.get("home")` returning None / missing key
  must yield `series_state=ACTIVE_SERIES_SCORE_MISSING`, NOT a 0-0 game.
* Suspicious 0-0 in MLB — excluded unless `score_confirmed=True`.
* Non-final statuses (postponed, suspended, in progress) — excluded.
* Happy path — valid completed games produce CONFIRMED state with
  correct line-aware over/under counts.
* Back-compat — supports both `final_score` and `live_stats.score` shapes.
* `LIMITED_SAMPLE_SERIES_SIGNAL` reason code surfaces when n<3.
* Suspicious 0-0 with explicit `score_confirmed=True` IS accepted.
"""
from __future__ import annotations

import pytest

from services.mlb_active_series_analyzer import (
    get_active_series_context,
    SERIES_STATE_CONFIRMED,
    SERIES_STATE_NO_COMPLETED,
    SERIES_STATE_SCORE_MISSING,
    SERIES_STATE_UNRESOLVED,
)


# ─── Mock Mongo plumbing ───────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, docs):
        self._docs = list(docs)
    def limit(self, _n):
        return self
    def __aiter__(self):
        return self._aiter()
    async def _aiter(self):
        for d in self._docs:
            yield d


class _FakeColl:
    def __init__(self, docs):
        self._docs = list(docs)
    def find(self, _query):
        return _AsyncCursor(self._docs)


class _FakeDB:
    """Dict-style DB that returns fake collections."""
    def __init__(self, mapping):
        # mapping: {coll_name: [docs]}
        self._mapping = {k: _FakeColl(v) for k, v in mapping.items()}
    def __getitem__(self, name):
        return self._mapping.get(name, _FakeColl([]))


# ─── Fixtures (Texas Rangers @ Minnesota Twins on 2026-XX-XX) ──────────
HOME = "Texas Rangers"
AWAY = "Minnesota Twins"
TARGET_DATE = "2026-09-21"
PREV_DAY     = "2026-09-20T19:05:00"
PREV_DAY_2   = "2026-09-19T19:05:00"


def _doc(home, away, kickoff, *, final_score=None, status=None,
          live_stats=None, score=None, score_confirmed=None):
    d = {
        "sport": "baseball",
        "home_team": home,
        "away_team": away,
        "kickoff_iso": kickoff,
    }
    if final_score is not None:
        d["final_score"] = final_score
    if score is not None:
        d["score"] = score
    if status is not None:
        d["status"] = status
    if live_stats is not None:
        d["live_stats"] = live_stats
    if score_confirmed is not None:
        d["score_confirmed"] = score_confirmed
    return d


# ──────────────────────────────────────────────────────────────────────
# Bug regression — the canonical case that started this sprint.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_phantom_zero_zero_from_missing_keys_is_excluded():
    """The original bug: `final_score = {}` (no home/away keys) was
    coalesced to 0-0 and counted as a valid game. Must now be excluded
    with `SCORE_MISSING`."""
    db = _FakeDB({
        "matches": [_doc(HOME, AWAY, PREV_DAY, final_score={})],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_SCORE_MISSING
    assert out["games_in_series"] == 0
    assert out["total_runs_avg"] is None
    assert out["over_rate"] is None  # zero observations ≠ zero runs


@pytest.mark.asyncio
async def test_none_scores_excluded_not_coalesced_to_zero():
    """`final_score = {"home": None, "away": None}` must be SCORE_MISSING."""
    db = _FakeDB({
        "matches": [_doc(HOME, AWAY, PREV_DAY,
                          final_score={"home": None, "away": None})],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_SCORE_MISSING
    assert out["games_in_series"] == 0


@pytest.mark.asyncio
async def test_suspicious_zero_zero_excluded_without_confirmation():
    """MLB 0-0 final is extremely rare. Without `score_confirmed=True`,
    must be excluded so the UI doesn't render a phantom G1."""
    db = _FakeDB({
        "finished_games": [_doc(HOME, AWAY, PREV_DAY,
                                  final_score={"home": 0, "away": 0},
                                  status="Final")],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE,
                                            sport="MLB")
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_SCORE_MISSING
    assert "SUSPICIOUS_ZERO_ZERO_EXCLUDED" in out["reason_codes"]
    # Audit trail visible.
    assert any(e["reason"] == "SCORE_SUSPICIOUS_ZERO_ZERO"
                  for e in out["excluded_docs"])


@pytest.mark.asyncio
async def test_suspicious_zero_zero_accepted_when_score_confirmed():
    """When the settlement job set `score_confirmed=True`, even 0-0 is
    a legitimate completed game."""
    db = _FakeDB({
        "finished_games": [_doc(HOME, AWAY, PREV_DAY,
                                  final_score={"home": 0, "away": 0},
                                  status="Final",
                                  score_confirmed=True)],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE,
                                            sport="MLB")
    assert out["available"] is True
    assert out["series_state"] == SERIES_STATE_CONFIRMED
    assert out["games_in_series"] == 1
    assert out["total_runs_avg"] == 0.0
    assert out["over_count"] == 0
    assert out["under_count"] == 1  # 0 < 9.5


# ──────────────────────────────────────────────────────────────────────
# Status validation.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_postponed_status_excluded():
    db = _FakeDB({
        "matches": [_doc(HOME, AWAY, PREV_DAY,
                          final_score={"home": 5, "away": 2},
                          status="Postponed")],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_NO_COMPLETED


@pytest.mark.asyncio
async def test_live_status_excluded():
    db = _FakeDB({
        "matches": [_doc(HOME, AWAY, PREV_DAY,
                          final_score={"home": 5, "away": 2},
                          status="Live")],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_NO_COMPLETED


@pytest.mark.asyncio
async def test_status_field_missing_with_scores_promoted_to_soft_final():
    """Docs without explicit status but WITH valid scores are accepted
    as soft-final (matches the archived_live_matches reality)."""
    db = _FakeDB({
        "archived_live_matches": [_doc(HOME, AWAY, PREV_DAY,
                                         final_score={"home": 5, "away": 2})],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is True
    assert out["series_state"] == SERIES_STATE_CONFIRMED
    assert out["games_in_series"] == 1
    assert out["total_runs_avg"] == 7.0


# ──────────────────────────────────────────────────────────────────────
# Happy path: 2 valid completed games + line-aware counts.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_happy_path_two_games_confirmed_with_line_counts():
    db = _FakeDB({
        "finished_games": [
            _doc(HOME, AWAY, PREV_DAY_2,
                  final_score={"home": 4, "away": 3}, status="Final"),
            _doc(HOME, AWAY, PREV_DAY,
                  final_score={"home": 8, "away": 5}, status="Final"),
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE,
                                            over_under_line=9.5)
    assert out["available"] is True
    assert out["series_state"] == SERIES_STATE_CONFIRMED
    assert out["games_in_series"] == 2
    assert out["total_runs_list"] == [7, 13]
    assert out["total_runs_avg"] == 10.0
    assert out["over_count"]  == 1   # 13 > 9.5
    assert out["under_count"] == 1   # 7  < 9.5
    assert out["push_count"]  == 0
    assert out["reference_line"] == 9.5
    # Per-game breakdown ordered chronologically.
    assert [g["game_number"] for g in out["games_detail"]] == [1, 2]
    assert out["games_detail"][0]["total_runs"] == 7
    assert out["games_detail"][1]["total_runs"] == 13
    # Sample-size reason code.
    assert "LIMITED_SAMPLE_SERIES_SIGNAL" in out["reason_codes"]


@pytest.mark.asyncio
async def test_team_orientation_normalised_when_doc_has_swapped_home_away():
    """Doc records the previous game with TWINS as home. The payload
    must re-orient to the TARGET orientation (RANGERS home)."""
    db = _FakeDB({
        "finished_games": [
            # Twins hosted: Twins 4, Rangers 3. From Rangers's POV that's "home 3 - away 4".
            _doc(AWAY, HOME, PREV_DAY,
                  final_score={"home": 4, "away": 3}, status="Final"),
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    g = out["games_detail"][0]
    assert g["home_team"] == HOME
    assert g["away_team"] == AWAY
    # Re-oriented: rangers had 3 (visitor), twins had 4 (host).
    assert g["home"] == 3
    assert g["away"] == 4
    assert g["total_runs"] == 7


@pytest.mark.asyncio
async def test_live_stats_score_shape_supported():
    """Back-compat: `live_stats.score = {home, away}` works."""
    db = _FakeDB({
        "archived_live_matches": [
            {
                "sport": "baseball",
                "home_team": HOME,
                "away_team": AWAY,
                "kickoff_iso": PREV_DAY,
                "live_stats": {
                    "score": {"home": 6, "away": 4},
                    "status": "Final",
                },
            },
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is True
    assert out["games_in_series"] == 1
    assert out["total_runs_avg"] == 10.0


# ──────────────────────────────────────────────────────────────────────
# Defensive / edge cases.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_no_matchup_in_window_returns_no_completed_state():
    db = _FakeDB({"matches": []})
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_NO_COMPLETED
    assert out["games_in_series"] == 0


@pytest.mark.asyncio
async def test_missing_db_returns_unresolved():
    out = await get_active_series_context(None, HOME, AWAY, TARGET_DATE)
    assert out["available"] is False
    assert out["series_state"] == SERIES_STATE_UNRESOLVED


@pytest.mark.asyncio
async def test_three_or_more_games_does_not_emit_limited_sample():
    db = _FakeDB({
        "finished_games": [
            _doc(HOME, AWAY, "2026-09-18T19:05:00",
                  final_score={"home": 4, "away": 3}, status="Final"),
            _doc(HOME, AWAY, "2026-09-19T19:05:00",
                  final_score={"home": 5, "away": 4}, status="Final"),
            _doc(HOME, AWAY, "2026-09-20T19:05:00",
                  final_score={"home": 6, "away": 5}, status="Final"),
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["games_in_series"] == 3
    assert "LIMITED_SAMPLE_SERIES_SIGNAL" not in out["reason_codes"]


@pytest.mark.asyncio
async def test_partial_validity_keeps_only_valid_games():
    """Mix of valid + suspicious + missing — only valid one survives,
    and excluded_docs records the rest."""
    db = _FakeDB({
        "finished_games": [
            _doc(HOME, AWAY, "2026-09-18T19:05:00",
                  final_score={}),  # SCORE_MISSING
            _doc(HOME, AWAY, "2026-09-19T19:05:00",
                  final_score={"home": 0, "away": 0}, status="Final"),  # suspicious
            _doc(HOME, AWAY, "2026-09-20T19:05:00",
                  final_score={"home": 7, "away": 4}, status="Final"),  # valid
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE)
    assert out["available"] is True
    assert out["games_in_series"] == 1
    assert out["total_runs_avg"] == 11.0
    assert len(out["excluded_docs"]) == 2
    reasons = {e["reason"] for e in out["excluded_docs"]}
    assert reasons == {"SCORE_MISSING", "SCORE_SUSPICIOUS_ZERO_ZERO"}


# ──────────────────────────────────────────────────────────────────────
# Override rules still work on CONFIRMED state.
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_high_scoring_series_triggers_override():
    """avg > 12 → series_override=True + lean=OVER."""
    db = _FakeDB({
        "finished_games": [
            _doc(HOME, AWAY, "2026-09-19T19:05:00",
                  final_score={"home": 9, "away": 8}, status="Final"),
            _doc(HOME, AWAY, "2026-09-20T19:05:00",
                  final_score={"home": 8, "away": 7}, status="Final"),
        ],
    })
    out = await get_active_series_context(db, HOME, AWAY, TARGET_DATE,
                                            over_under_line=9.0,
                                            model_expected_runs=7.0)
    assert out["available"] is True
    assert out["series_override"] is True
    assert out["series_lean"] == "OVER"
    assert "ofensivo" in (out["override_reason"] or "").lower()
