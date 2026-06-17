"""Sprint E.1.1 · Tests for ``services.market_identity_resolver``.

Covers the contract approved with the user:

* Tolerance ladder HIGH/MEDIUM/LOW with thresholds 0.02/0.03/0.05.
* Outcome → MANUAL_MARKET_TYPES mapping for h2h, draw_no_bet, totals
  (incl. ``alternate_totals``), spreads (incl. ``alternate_spreads``),
  btts, team_totals.
* Candidate ranking by ``(delta_asc, market_priority_asc)``.
* Ambiguity detection: when ≥2 distinct market families tie at the
  same confidence, the status must be ``AMBIGUOUS`` with the full
  list surfaced.
* Resolver end-to-end: input validation, cache short-circuit,
  ODDS_EVENT_ID_MISSING fail-soft, API failure fail-soft, persistence.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import market_identity_resolver as mir
from services import live_odds_monitor as lom


# ════════════════════════════════════════════════════════════════════════
# Fake Mongo
# ════════════════════════════════════════════════════════════════════════
class _AsyncCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.docs = self.docs[: int(n)]
        return self

    async def to_list(self, length=None):
        return list(self.docs[: int(length)] if length else self.docs)

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])
        self.inserts: list[dict] = []

    def find(self, query=None, projection=None, sort=None, limit=None):
        q = query or {}

        def _match(d):
            for k, v in q.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        return False
                else:
                    if d.get(k) != v:
                        return False
            return True

        return _AsyncCursor([d for d in self.docs if _match(d)])

    async def find_one(self, query=None, projection=None, sort=None):
        cur = self.find(query or {})
        async for d in cur:
            return d
        return None

    async def insert_one(self, doc):
        self.inserts.append(doc)
        self.docs.append(doc)

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set") or {})
                return type("R", (), {"matched_count": 1,
                                       "modified_count": 1,
                                       "upserted_id": None})()
        if upsert:
            new = {**query, **(update.get("$set") or {})}
            self.docs.append(new)
        return type("R", (), {"matched_count": 0,
                               "modified_count": 0,
                               "upserted_id": None})()

    async def create_index(self, *args, **kwargs):
        pass


class FakeDB:
    def __init__(self):
        self.market_identity_resolutions = FakeCollection()
        self.odds_event_id_mappings      = FakeCollection()

    def __getitem__(self, name):
        if not hasattr(self, name):
            setattr(self, name, FakeCollection())
        return getattr(self, name)


# ════════════════════════════════════════════════════════════════════════
# Pure helpers
# ════════════════════════════════════════════════════════════════════════
class TestConfidenceLadder:
    def test_high(self):
        assert mir._confidence_for_delta(0.00) == "HIGH"
        assert mir._confidence_for_delta(0.02) == "HIGH"

    def test_medium(self):
        assert mir._confidence_for_delta(0.025) == "MEDIUM"
        assert mir._confidence_for_delta(0.03)  == "MEDIUM"

    def test_low(self):
        assert mir._confidence_for_delta(0.04) == "LOW"
        assert mir._confidence_for_delta(0.05) == "LOW"

    def test_out_of_band(self):
        assert mir._confidence_for_delta(0.051) is None
        assert mir._confidence_for_delta(0.10) is None


class TestOutcomeMapping:
    HOME = "Portugal"
    AWAY = "Congo DR"

    def test_h2h_home(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="h2h",
            outcome={"name": "Portugal", "price": 1.25},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "MATCH_WINNER", "selection": "HOME",
                     "line": None}

    def test_h2h_draw(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="h2h", outcome={"name": "Draw", "price": 4.5},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m["market_type"] == "MATCH_WINNER" and m["selection"] == "DRAW"

    def test_dnb_away(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="draw_no_bet",
            outcome={"name": "Congo DR", "price": 5.0},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "DNB", "selection": "AWAY", "line": None}

    def test_totals_over(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="totals",
            outcome={"name": "Over", "price": 1.85, "point": 2.5},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "TOTAL_GOALS", "selection": "OVER",
                     "line": 2.5}

    def test_alternate_totals_under(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="alternate_totals",
            outcome={"name": "Under", "price": 1.25, "point": 4.5},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "TOTAL_GOALS", "selection": "UNDER",
                     "line": 4.5}

    def test_spreads_home(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="spreads",
            outcome={"name": "Portugal", "price": 1.95, "point": -1.5},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "ASIAN_HANDICAP", "selection": "HOME",
                     "line": -1.5}

    def test_btts_yes(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="btts", outcome={"name": "Yes", "price": 2.20},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m == {"market_type": "BTTS", "selection": "YES", "line": None}

    def test_team_totals(self):
        m = mir._map_outcome_to_manual_identity(
            api_market="team_totals",
            outcome={"name": "Portugal Over", "price": 1.50, "point": 1.5},
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert m["market_type"] == "TOTAL_GOALS"
        assert m["selection"] == "OVER"
        assert m["scope"] == "team_totals"
        assert m["team_hint"] == "Portugal Over"

    def test_unknown_market_returns_none(self):
        assert mir._map_outcome_to_manual_identity(
            api_market="player_props",
            outcome={"name": "Anything", "price": 1.25},
            home_team=self.HOME, away_team=self.AWAY,
        ) is None


class TestExtractCandidates:
    HOME = "Portugal"
    AWAY = "Congo DR"

    def _event(self, *outcomes):
        # Build a minimal event payload with one bookmaker holding the
        # supplied outcomes across the right markets.
        markets: dict[str, list[dict]] = {}
        for mk, o in outcomes:
            markets.setdefault(mk, []).append(o)
        return {
            "id": "evt-1",
            "home_team": self.HOME, "away_team": self.AWAY,
            "bookmakers": [{
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [{"key": mk, "outcomes": ous}
                             for mk, ous in markets.items()],
            }],
        }

    def test_within_low_tolerance_only(self):
        # detected=1.25; api prices 1.24 (HIGH), 1.27 (MEDIUM),
        # 1.29 (LOW), 1.40 (OUT). Should keep only the first three.
        ev = self._event(
            ("totals", {"name": "Over",  "price": 1.24, "point": 2.5}),
            ("h2h",    {"name": "Portugal", "price": 1.27}),
            ("btts",   {"name": "Yes",  "price": 1.29}),
            ("draw_no_bet", {"name": "Portugal", "price": 1.40}),
        )
        cands = mir.extract_candidates_from_event(
            event_payload=ev, detected_price=1.25,
            home_team=self.HOME, away_team=self.AWAY,
        )
        prices = [c["api_price"] for c in cands]
        assert 1.40 not in prices
        # Sorted by delta asc.
        assert cands[0]["confidence"] == "HIGH"
        assert cands[0]["api_price"] == 1.24
        confs = {round(c["delta"], 2): c["confidence"] for c in cands}
        assert confs[0.01] == "HIGH"
        assert confs[0.02] == "HIGH"
        assert confs[0.04] == "LOW"

    def test_market_priority_breaks_ties(self):
        # Two outcomes with the SAME delta but different markets — the
        # earlier in DEFAULT_MARKETS_PRIORITY must come first.
        ev = self._event(
            ("totals",  {"name": "Over", "price": 1.27, "point": 2.5}),
            ("h2h",     {"name": "Portugal", "price": 1.27}),
        )
        cands = mir.extract_candidates_from_event(
            event_payload=ev, detected_price=1.25,
            home_team=self.HOME, away_team=self.AWAY,
        )
        assert cands[0]["api_market"] == "h2h"
        assert cands[1]["api_market"] == "totals"

    def test_empty_event_returns_empty(self):
        assert mir.extract_candidates_from_event(
            event_payload={}, detected_price=1.25,
            home_team=self.HOME, away_team=self.AWAY,
        ) == []

    def test_invalid_detected_price_returns_empty(self):
        assert mir.extract_candidates_from_event(
            event_payload={"bookmakers": []}, detected_price="garbage",
            home_team=self.HOME, away_team=self.AWAY,
        ) == []


class TestSummariseCandidates:
    def _cand(self, market, sel, line, delta, conf="HIGH"):
        return {"resolved_market": market, "resolved_selection": sel,
                "resolved_line": line, "delta": delta, "confidence": conf}

    def test_resolved_single_family(self):
        cands = [
            self._cand("MATCH_WINNER", "HOME", None, 0.01, "HIGH"),
            self._cand("MATCH_WINNER", "HOME", None, 0.02, "HIGH"),  # same family
        ]
        s = mir.summarise_candidates(cands)
        assert s["resolution_status"] == "RESOLVED"
        assert s["best"]["resolved_market"] == "MATCH_WINNER"
        assert s["ambiguous"] == []

    def test_ambiguous_two_families_same_confidence(self):
        cands = [
            self._cand("TOTAL_GOALS", "OVER",  1.5,  0.01, "HIGH"),
            self._cand("BTTS",        "YES",   None, 0.01, "HIGH"),
        ]
        s = mir.summarise_candidates(cands)
        assert s["resolution_status"] == "AMBIGUOUS"
        # Both families are in `ambiguous`.
        markets = {c["resolved_market"] for c in s["ambiguous"]}
        assert markets == {"TOTAL_GOALS", "BTTS"}

    def test_not_ambiguous_when_top_outranks_others(self):
        # Best is HIGH; second-best is LOW → not ambiguous.
        cands = [
            self._cand("DNB", "HOME", None, 0.01, "HIGH"),
            self._cand("BTTS", "NO",  None, 0.04, "LOW"),
        ]
        s = mir.summarise_candidates(cands)
        assert s["resolution_status"] == "RESOLVED"
        assert s["best"]["resolved_market"] == "DNB"

    def test_empty_returns_not_found(self):
        s = mir.summarise_candidates([])
        assert s["resolution_status"] == "NOT_FOUND"
        assert s["best"] is None


# ════════════════════════════════════════════════════════════════════════
# Async resolver
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_resolver_invalid_inputs():
    db = FakeDB()
    out = await mir.resolve_market_identity(
        db, match={"match_id": "m1", "home_team": "A", "away_team": "B"},
        detected_price="abc",
    )
    assert out["resolution_status"] == "INVALID_INPUT"
    assert out["reason_code"] == "DETECTED_PRICE_INVALID"

    out2 = await mir.resolve_market_identity(
        db, match={"match_id": "m1", "home_team": "A", "away_team": "B"},
        detected_price=0.95,
    )
    assert out2["resolution_status"] == "INVALID_INPUT"
    assert out2["reason_code"] == "DETECTED_PRICE_BELOW_MIN"

    out3 = await mir.resolve_market_identity(
        db, match={"match_id": "m1"}, detected_price=1.25,
    )
    assert out3["resolution_status"] == "INVALID_INPUT"
    assert out3["reason_code"] == "MATCH_INFO_INCOMPLETE"


@pytest.mark.asyncio
async def test_resolver_match_not_found_when_no_event(monkeypatch):
    db = FakeDB()

    async def _empty_events(*, sport, **kwargs):
        return {"events": [], "quota": {"remaining": 100}}

    async def _no_current(*args, **kwargs):
        raise AssertionError("fetch_current_odds must NOT be called when "
                              "no event_id was resolved")

    out = await mir.resolve_market_identity(
        db, match={"match_id": "m_unknown",
                    "home_team": "Some Team A",
                    "away_team": "Some Team B"},
        detected_price=1.25,
        sport_keys=["soccer_epl"],
        fetch_events=_empty_events,
        fetch_current_odds=_no_current,
    )
    assert out["resolution_status"] == "MATCH_NOT_FOUND"
    assert out["reason_code"] == "ODDS_EVENT_ID_MISSING"
    # Persistence: the audit row is written even on no-event.
    assert any(d.get("match_id") == "m_unknown"
                for d in db.market_identity_resolutions.docs)


@pytest.mark.asyncio
async def test_resolver_api_unavailable_when_current_fails():
    db = FakeDB()

    async def _events(*, sport, **kwargs):
        return {"events": [
            {"id": "evt-1", "home_team": "Portugal",
             "away_team": "Congo DR"}
        ], "quota": {"remaining": 100}}

    async def _broken_current(*args, **kwargs):
        return None

    out = await mir.resolve_market_identity(
        db, match={"match_id": "m1",
                    "home_team": "Portugal",
                    "away_team": "Congo DR"},
        detected_price=1.25,
        sport_keys=["soccer_fifa_world_cup"],
        fetch_events=_events,
        fetch_current_odds=_broken_current,
    )
    assert out["resolution_status"] == "API_UNAVAILABLE"
    assert out["reason_code"] == "FETCH_CURRENT_ODDS_FAILED"
    assert out["event_id"] == "evt-1"


@pytest.mark.asyncio
async def test_resolver_end_to_end_resolved():
    db = FakeDB()

    async def _events(*, sport, **kwargs):
        return {"events": [
            {"id": "evt-1", "home_team": "Portugal",
             "away_team": "Congo DR"}
        ], "quota": {"remaining": 99}}

    async def _current(*, sport, event_ids, **kwargs):
        return {"events": [{
            "id": "evt-1", "home_team": "Portugal", "away_team": "Congo DR",
            "bookmakers": [{
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [
                    # Detected 1.25 → Over 4.5 @ 1.26 is the closest match.
                    {"key": "alternate_totals", "outcomes": [
                        {"name": "Over", "price": 1.26, "point": 4.5},
                        {"name": "Under", "price": 3.80, "point": 4.5},
                    ]},
                    # h2h is far away, must be filtered out.
                    {"key": "h2h", "outcomes": [
                        {"name": "Portugal", "price": 1.50},
                        {"name": "Draw",     "price": 4.20},
                        {"name": "Congo DR", "price": 6.10},
                    ]},
                ],
            }],
        }], "quota": {"remaining": 98}}

    out = await mir.resolve_market_identity(
        db,
        match={"match_id": "portugal_congo_001",
                "home_team": "Portugal", "away_team": "Congo DR"},
        detected_price=1.25,
        sport_keys=["soccer_fifa_world_cup"],
        fetch_events=_events,
        fetch_current_odds=_current,
    )
    assert out["resolution_status"] == "RESOLVED"
    assert out["reason_code"] == "MARKET_IDENTITY_RESOLVED_BY_THE_ODDS_API"
    assert out["best"]["resolved_market"] == "TOTAL_GOALS"
    assert out["best"]["resolved_selection"] == "OVER"
    assert out["best"]["resolved_line"] == 4.5
    assert out["best"]["confidence"] == "HIGH"
    # Persistence row written.
    assert any(d.get("match_id") == "portugal_congo_001"
                for d in db.market_identity_resolutions.docs)


@pytest.mark.asyncio
async def test_resolver_ambiguous_surfaces_all_candidates():
    db = FakeDB()

    async def _events(*, sport, **kwargs):
        return {"events": [{"id": "e", "home_team": "Portugal",
                            "away_team": "Congo DR"}],
                "quota": {}}

    async def _current(*, sport, event_ids, **kwargs):
        # Two distinct markets at the exact same price → AMBIGUOUS.
        return {"events": [{
            "id": "e", "home_team": "Portugal", "away_team": "Congo DR",
            "bookmakers": [{
                "key": "pinnacle", "title": "Pinnacle",
                "markets": [
                    {"key": "draw_no_bet", "outcomes": [
                        {"name": "Portugal", "price": 1.26}
                    ]},
                    {"key": "alternate_totals", "outcomes": [
                        {"name": "Over", "price": 1.26, "point": 4.5}
                    ]},
                ],
            }],
        }], "quota": {}}

    out = await mir.resolve_market_identity(
        db, match={"match_id": "amb1", "home_team": "Portugal",
                    "away_team": "Congo DR"},
        detected_price=1.25,
        sport_keys=["soccer_fifa_world_cup"],
        fetch_events=_events,
        fetch_current_odds=_current,
    )
    assert out["resolution_status"] == "AMBIGUOUS"
    assert out["reason_code"] == "MARKET_IDENTITY_AMBIGUOUS_REQUIRES_USER_CHOICE"
    families = sorted({c["resolved_market"] for c in out["ambiguous"]})
    assert families == ["DNB", "TOTAL_GOALS"]


@pytest.mark.asyncio
async def test_resolver_cache_short_circuit():
    db = FakeDB()
    # Pre-seed an event_id mapping so we know it has been "seen".
    await db.odds_event_id_mappings.insert_one({
        "match_id": "m1", "event_id": "evt-1",
        "sport_key": "soccer_epl",
    })
    # Seed a previous resolution row.
    await db.market_identity_resolutions.insert_one({
        "match_id":       "m1",
        "detected_price": 1.25,
        "resolution_status": "RESOLVED",
        "best":           {"resolved_market": "TOTAL_GOALS",
                            "resolved_selection": "OVER",
                            "resolved_line": 2.5,
                            "delta": 0.0, "confidence": "HIGH"},
        "resolved_at":    datetime.now(timezone.utc),
    })

    async def _no_events(*args, **kwargs):
        raise AssertionError("must not call fetch_events on cache hit")

    out = await mir.resolve_market_identity(
        db,
        match={"match_id": "m1", "home_team": "A", "away_team": "B"},
        detected_price=1.25,
        sport_keys=["soccer_epl"],
        fetch_events=_no_events,
        fetch_current_odds=_no_events,
    )
    assert out["from_cache"] is True
    assert out["best"]["resolved_market"] == "TOTAL_GOALS"


@pytest.mark.asyncio
async def test_resolver_cache_bypassed_when_use_cache_false():
    db = FakeDB()
    await db.market_identity_resolutions.insert_one({
        "match_id":       "m1",
        "detected_price": 1.25,
        "resolution_status": "RESOLVED",
        "resolved_at":    datetime.now(timezone.utc),
    })

    fetch_calls: list = []

    async def _events(*, sport, **kwargs):
        fetch_calls.append(sport)
        return {"events": [], "quota": {}}

    async def _no_current(*args, **kwargs):
        raise AssertionError("not expected")

    await mir.resolve_market_identity(
        db, match={"match_id": "m1", "home_team": "A", "away_team": "B"},
        detected_price=1.25,
        sport_keys=["soccer_epl"],
        use_cache=False,
        fetch_events=_events,
        fetch_current_odds=_no_current,
    )
    # use_cache=False → it tried to fetch events.
    assert fetch_calls == ["soccer_epl"]
