"""Regression tests for ``mlb_results_settler._resolve_result`` and the
F6C auto-settle sweep.

Covers:
    * Full-game Over/Under win/lost/push paths
    * team_total_over / team_total_under (home & away)
    * Markets que NO se auto-settlean (F5, NRFI, inning)
    * Outcomes con miss_type correctos
    * Recommended_line desde texto cuando no viene explícito
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from services.mlb_results_settler import (
    _resolve_result,
    auto_settle_pending_evaluations,
)


# ───────────────────────────────────────────────────────────────────────
# _resolve_result — full-game Over / Under
# ───────────────────────────────────────────────────────────────────────
class TestResolveFullGameOverUnder:
    def test_over_wins_when_total_exceeds_line(self):
        out = _resolve_result(
            final_runs_home=6,
            final_runs_away=4,            # total 10
            recommended_market="Over 8.5",
            recommended_side="over",
            recommended_line=8.5,
        )
        assert out["result"] == "won"
        assert out["miss_type"] is None

    def test_under_wins_when_total_below_line(self):
        out = _resolve_result(
            final_runs_home=2,
            final_runs_away=3,            # total 5
            recommended_market="Under 8.5",
            recommended_side="under",
            recommended_line=8.5,
        )
        assert out["result"] == "won"

    def test_under_loses_when_total_above_line(self):
        out = _resolve_result(
            final_runs_home=5,
            final_runs_away=5,            # total 10
            recommended_market="Under 8.5",
            recommended_side="under",
            recommended_line=8.5,
        )
        assert out["result"] == "lost"
        assert out["miss_type"] == "OVER_BEAT_UNDER"

    def test_over_loses_when_total_below_line(self):
        out = _resolve_result(
            final_runs_home=2,
            final_runs_away=2,            # total 4
            recommended_market="Over 8.5",
            recommended_side="over",
            recommended_line=8.5,
        )
        assert out["result"] == "lost"
        assert out["miss_type"] == "UNDER_BEAT_OVER"

    def test_push_on_integer_line_exact_total(self):
        out = _resolve_result(
            final_runs_home=4,
            final_runs_away=4,            # total 8
            recommended_market="Under 8",
            recommended_side="under",
            recommended_line=8.0,
        )
        assert out["result"] == "push"
        assert out["miss_type"] == "PUSH"

    def test_half_line_never_pushes(self):
        out = _resolve_result(
            final_runs_home=4,
            final_runs_away=5,            # total 9
            recommended_market="Under 8.5",
            recommended_side="under",
            recommended_line=8.5,
        )
        assert out["result"] == "lost"
        assert out["miss_type"] == "OVER_BEAT_UNDER"


# ───────────────────────────────────────────────────────────────────────
# _resolve_result — team totals
# ───────────────────────────────────────────────────────────────────────
class TestResolveTeamTotal:
    def test_team_total_over_home_wins(self):
        out = _resolve_result(
            final_runs_home=5,
            final_runs_away=2,
            recommended_market="Home Team Total Over 4.5",
            recommended_side="team_total_over",
            recommended_line=4.5,
        )
        assert out["result"] == "won"

    def test_team_total_under_away_loses(self):
        out = _resolve_result(
            final_runs_home=3,
            final_runs_away=5,
            recommended_market="Away Team Total Under 3.5",
            recommended_side="team_total_under",
            recommended_line=3.5,
        )
        assert out["result"] == "lost"
        assert out["miss_type"] == "OVER_BEAT_UNDER"

    def test_team_total_missing_side_skipped(self):
        out = _resolve_result(
            final_runs_home=5,
            final_runs_away=2,
            recommended_market="Team Total Over 4.5",  # sin home/away
            recommended_side="team_total_over",
            recommended_line=4.5,
        )
        assert out["result"] is None
        assert out["skipped_reason"] == "team_total_missing_home_away_marker"


# ───────────────────────────────────────────────────────────────────────
# _resolve_result — markets que NO se auto-settle
# ───────────────────────────────────────────────────────────────────────
class TestResolveSkippedMarkets:
    @pytest.mark.parametrize("scope,market", [
        ("f5",      "F5 Over 4.5"),
        ("",       "First 5 Innings Under 4.5"),
        ("",       "NRFI"),
        ("",       "YRFI"),
        ("inning", "Inning Explosive Over 1.5"),
    ])
    def test_skipped_inning_dependent_markets(self, scope, market):
        out = _resolve_result(
            final_runs_home=4,
            final_runs_away=4,
            recommended_market=market,
            recommended_side="under",
            recommended_line=4.5,
            market_scope=scope,
        )
        assert out["result"] is None
        assert out["skipped_reason"] is not None

    def test_missing_final_score(self):
        out = _resolve_result(
            final_runs_home=None,
            final_runs_away=4,
            recommended_market="Over 8.5",
            recommended_side="over",
            recommended_line=8.5,
        )
        assert out["skipped_reason"] == "missing_final_score"

    def test_missing_line_falls_back_to_market_text(self):
        # Caso: orquestador no escribió recommended_line, pero
        # "Under 8.5" está en el texto del mercado.
        out = _resolve_result(
            final_runs_home=3,
            final_runs_away=3,            # total 6
            recommended_market="Under 8.5",
            recommended_side="under",
            recommended_line=None,        # falta, debería extraerlo del texto
        )
        assert out["result"] == "won"

    def test_unknown_side_skipped(self):
        out = _resolve_result(
            final_runs_home=4,
            final_runs_away=4,
            recommended_market="moneyline",
            recommended_side="home",
            recommended_line=None,
        )
        assert out["result"] is None
        assert out["skipped_reason"] == "unknown_market_side"


# ───────────────────────────────────────────────────────────────────────
# auto_settle_pending_evaluations — integration con DB fake
# ───────────────────────────────────────────────────────────────────────
class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.updates = []

    def find(self, query, projection=None):
        # Soporte filtro simple result/sport/generated_at
        def _match(d):
            if "sport" in query and d.get("sport") != query["sport"]:
                return False
            if "result" in query and d.get("result") != query["result"]:
                return False
            if "match_id" in query:
                want = query["match_id"]
                if isinstance(want, dict) and "$in" in want:
                    if d.get("match_id") not in want["$in"]:
                        return False
                elif d.get("match_id") != want:
                    return False
            if "final_score" in query and "$exists" in query["final_score"]:
                if (query["final_score"]["$exists"]) != ("final_score" in d):
                    return False
            return True

        matched = [d for d in self.docs if _match(d)]
        return _AsyncCursor(matched)

    async def find_one(self, query, projection=None):
        cursor = self.find(query, projection)
        async for d in cursor:
            return d
        return None

    async def update_one(self, query, update):
        # Solo registra; las acciones reales no son necesarias para el test.
        self.updates.append({"query": query, "update": update})
        return type("R", (), {"matched_count": 1, "modified_count": 1})()


class _AsyncCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def sort(self, *args, **kwargs):
        return self

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeDB:
    def __init__(self):
        self.mlb_run_evaluations = FakeCollection()
        self.matches = FakeCollection()
        self.archived_live_matches = FakeCollection()


@pytest.mark.asyncio
async def test_auto_settle_resolves_over_pending_to_won():
    db = FakeDB()
    now_iso = datetime.now(timezone.utc).isoformat()

    db.mlb_run_evaluations.docs = [{
        "id":                 "eval-001",
        "sport":              "baseball",
        "match_id":           "m-100",
        "result":             "pending",
        "recommended_market": "Over 7.5",
        "recommended_side":   "over",
        "recommended_line":   7.5,
        "market_scope":       "full_game",
        "generated_at":       now_iso,
    }]
    db.matches.docs = [{
        "match_id":     "m-100",
        "final_score":  {"home": 6, "away": 5, "total": 11},
    }]

    stats = await auto_settle_pending_evaluations(db, days_back=7)
    assert stats["scanned"] == 1
    assert stats["settled"] == 1
    assert stats["by_result"]["won"] == 1
    # update_run_evaluation_result se llama dentro → produce 1 update.
    assert len(db.mlb_run_evaluations.updates) >= 1


@pytest.mark.asyncio
async def test_auto_settle_skips_f5_markets():
    db = FakeDB()
    now_iso = datetime.now(timezone.utc).isoformat()

    db.mlb_run_evaluations.docs = [{
        "id":                 "eval-002",
        "sport":              "baseball",
        "match_id":           "m-200",
        "result":             "pending",
        "recommended_market": "F5 Over 4.5",
        "recommended_side":   "over",
        "recommended_line":   4.5,
        "market_scope":       "f5",
        "generated_at":       now_iso,
    }]
    db.matches.docs = [{
        "match_id":     "m-200",
        "final_score":  {"home": 5, "away": 3},
    }]

    stats = await auto_settle_pending_evaluations(db, days_back=7)
    assert stats["skipped"] == 1
    assert stats["settled"] == 0
    assert "f5_requires_inning_data" in stats["by_skip_reason"]


@pytest.mark.asyncio
async def test_auto_settle_no_final_score_yet():
    db = FakeDB()
    now_iso = datetime.now(timezone.utc).isoformat()

    db.mlb_run_evaluations.docs = [{
        "id":                 "eval-003",
        "sport":              "baseball",
        "match_id":           "m-300",
        "result":             "pending",
        "recommended_market": "Under 8.5",
        "recommended_side":   "under",
        "recommended_line":   8.5,
        "market_scope":       "full_game",
        "generated_at":       now_iso,
    }]
    # matches collection vacía → no hay final_score escrito todavía.
    db.matches.docs = []

    stats = await auto_settle_pending_evaluations(db, days_back=7)
    assert stats["no_score"] == 1
    assert stats["settled"] == 0
