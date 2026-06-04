"""Tests for the Football Moneyball Intelligence Layer + Pattern Memory.

Coverage:
  * Warehouse: fail-soft when db is None, idempotent upserts on a fake DB.
  * Goal pressure profile: tier classification, combined match context,
    early-goal flags, live override, derive_goal_pressure_impact.
  * Snapshot builder: pregame + live + full digest.
  * Pattern memory: derive_pattern_keys + sample-size gates.
  * Market selection: protected Under 3.5 over Under 2.5, manual-odds bucket,
    league-quality gate, pattern-memory hint.
  * Feedback loop: outcome interpretation + persistence on fake DB.
  * Pattern matcher facade: attach_football_intelligence_to_payload +
    compare_live_vs_pregame.

The fake DB is a minimal in-memory async double sufficient for the
warehouse coroutines.
"""

from __future__ import annotations

import asyncio
import pytest

from services.football_moneyball import (
    # Pressure
    HIGH_PRESSURE, MODERATE_PRESSURE, LOW_PRESSURE,
    NEUTRAL_PRESSURE, UNAVAILABLE,
    calculate_team_goal_pressure,
    calculate_match_goal_pressure_context,
    derive_goal_pressure_impact,
    # Snapshot
    build_pregame_snapshot, build_live_snapshot,
    build_full_intelligence_snapshot,
    # Pattern memory
    derive_pattern_keys,
    # Market selection
    select_football_market,
    # Feedback
    record_football_pick_outcome,
    # Pattern matcher facade
    attach_football_intelligence_to_payload,
    compare_live_vs_pregame,
    # Warehouse
    persist_match_intelligence_snapshot,
    persist_football_market_result,
    lookup_pattern_match,
    summarize_pattern_memory,
    ensure_football_indexes,
)
from services.football_moneyball.football_goal_pressure_profile import (
    RC_HIGH_PRESSURE_PROFILE, RC_LOW_PRESSURE_CONTROLLED,
    RC_EARLY_GOAL_RISK, RC_LIVE_PRESSURE_ACCELERATION,
    RC_UNDER_PICK_HIGH_PRESSURE, RC_UNDER_PICK_MODERATE_PRESSURE,
)
from services.football_moneyball.football_market_selection import (
    MKT_UNDER_35, MKT_DOUBLE_CHANCE_1X, MKT_WATCHLIST,
    RC_UNDER_3_5_PREFERRED_OVER_2_5,
    RC_DOUBLE_CHANCE_SAFER_THAN_ML,
    RC_MANUAL_ODDS_REVIEW_REQUIRED,
    RC_LEAGUE_QUALITY_LOW,
    RC_PATTERN_MEMORY_PREFERRED_MARKET,
)
from services.football_moneyball.football_pattern_matcher import (
    RC_LIVE_TIER_ESCALATED, RC_LIVE_KEEP_MARKET, RC_LIVE_AVOID_MARKET,
)


# ─────────────────────────────────────────────────────────────────────
# Fake async DB double
# ─────────────────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_key = None
        self._sort_dir = 1
        self._limit = None

    def sort(self, key, direction=1):
        # Support both .sort("key", 1) and .sort([("k1", 1), ("k2", 1)])
        if isinstance(key, list):
            # Multi-key sort: sort by first key for the fake; good enough.
            if key:
                self._sort_key = key[0][0]
                self._sort_dir = key[0][1]
        else:
            self._sort_key = key
            self._sort_dir = direction
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def __aiter__(self):
        items = list(self._docs)
        if self._sort_key:
            items.sort(
                key=lambda d: (d.get(self._sort_key) is None, d.get(self._sort_key)),
                reverse=self._sort_dir == -1,
            )
        if self._limit is not None:
            items = items[: self._limit]
        async def _gen():
            for x in items:
                yield x
        return _gen()


class _FakeColl:
    def __init__(self):
        self.docs: list[dict] = []

    async def find_one(self, q, sort=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(d)
        return None

    def find(self, q, projection=None):
        out = []
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False; break
                elif isinstance(v, dict) and "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        ok = False; break
                elif isinstance(v, dict) and "$gte" in v:
                    val = d.get(k)
                    if val is None or val < v["$gte"]:
                        ok = False; break
                else:
                    if d.get(k) != v:
                        ok = False; break
            if ok:
                out.append(dict(d))
        return _FakeCursor(out)

    async def replace_one(self, q, payload, upsert=False):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in q.items()):
                self.docs[i] = dict(payload)
                return
        if upsert:
            self.docs.append(dict(payload))

    async def insert_one(self, payload):
        self.docs.append(dict(payload))

    async def create_index(self, *_args, **_kwargs):
        return "ok"


class _FakeDB:
    def __init__(self):
        self._colls: dict[str, _FakeColl] = {}

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = _FakeColl()
        return self._colls[name]

    def __getattr__(self, name):
        # Support `db.football_market_results` attribute access used by
        # the calibration module (mirrors Motor's collection access).
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__getitem__(name)


@pytest.fixture
def fake_db():
    return _FakeDB()


# ═════════════════════════════════════════════════════════════════════
# Warehouse fail-soft + indexes
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_warehouse_failsoft_when_db_is_none():
    assert (await ensure_football_indexes(None)) == {"available": False, "reason": "db_is_none"}
    assert (await persist_match_intelligence_snapshot(None, match_id="m1", snapshot={"a": 1})) is False
    assert (await persist_football_market_result(None, match_id="m1", user_id="u", market="x")) is False
    s = await lookup_pattern_match(None, ["X"])
    assert s["sample_size"] == 0
    assert s["confidence_adjustment"] == 0.0
    summary = await summarize_pattern_memory(None)
    assert summary["available"] is False


@pytest.mark.asyncio
async def test_ensure_indexes_creates_all(fake_db):
    res = await ensure_football_indexes(fake_db)
    assert res["available"] is True
    assert res["errors"] == []
    # 16 indexes total (per warehouse module).
    assert len(res["created"]) >= 16


@pytest.mark.asyncio
async def test_persist_snapshot_idempotent(fake_db):
    ok1 = await persist_match_intelligence_snapshot(
        fake_db, match_id="m1", snapshot={"v": 1}, day="2025-01-01",
        league="L", selected_market="Under 3.5", pattern_keys=["A"],
    )
    ok2 = await persist_match_intelligence_snapshot(
        fake_db, match_id="m1", snapshot={"v": 2}, day="2025-01-01",
        league="L", selected_market="Under 3.5", pattern_keys=["A"],
    )
    assert ok1 and ok2
    coll = fake_db["football_match_intelligence_snapshots"]
    assert len(coll.docs) == 1  # idempotent
    assert coll.docs[0]["snapshot"]["v"] == 2


# ═════════════════════════════════════════════════════════════════════
# Goal pressure profile
# ═════════════════════════════════════════════════════════════════════
def _team_ctx(**kwargs):
    base = {
        "goals_for_avg": 1.20,
        "goals_against_avg": 1.20,
        "recent_fixtures": {
            "under_2_5_rate": 0.50,
            "under_3_5_rate": 0.70,
            "btts_rate": 0.50,
            "clean_sheet_rate": 0.30,
        },
        "seasonal_form": {
            "early_goal_profile": {"early_goal_pct": 0.15},
        },
    }
    rf = base["recent_fixtures"]
    sf = base["seasonal_form"]
    for k, v in kwargs.items():
        if k in rf:
            rf[k] = v
        elif k in sf or k == "early_goal_pct":
            sf.setdefault("early_goal_profile", {})["early_goal_pct"] = v
        else:
            base[k] = v
    return {"context": base}


def test_pressure_unavailable_when_no_signals():
    r = calculate_team_goal_pressure({"context": {}})
    assert r["pressure_tier"] == UNAVAILABLE
    assert r["available"] is False


def test_pressure_high_profile_classification():
    t = _team_ctx(
        goals_for_avg=1.90, goals_against_avg=1.40,
        under_2_5_rate=0.30, btts_rate=0.70,
    )
    r = calculate_team_goal_pressure(t)
    assert r["pressure_tier"] == HIGH_PRESSURE
    assert RC_HIGH_PRESSURE_PROFILE in r["reasons"]


def test_pressure_low_controlled_profile():
    t = _team_ctx(
        goals_for_avg=0.90, goals_against_avg=0.85,
        under_2_5_rate=0.75, clean_sheet_rate=0.50,
    )
    r = calculate_team_goal_pressure(t)
    assert r["pressure_tier"] == LOW_PRESSURE
    assert RC_LOW_PRESSURE_CONTROLLED in r["reasons"]


def test_pressure_early_goal_flag_demotes_low_to_neutral():
    t = _team_ctx(
        goals_for_avg=0.90, goals_against_avg=0.85,
        under_2_5_rate=0.75, clean_sheet_rate=0.50,
        early_goal_pct=0.35,
    )
    r = calculate_team_goal_pressure(t)
    assert RC_EARLY_GOAL_RISK in r["reasons"]
    # Demoted away from pure LOW
    assert r["pressure_tier"] in (NEUTRAL_PRESSURE, MODERATE_PRESSURE)


def test_pressure_live_override_escalates_tier():
    t = _team_ctx()
    r = calculate_team_goal_pressure(t, live_shots_on_goal=7)
    assert RC_LIVE_PRESSURE_ACCELERATION in r["reasons"]
    assert r["pressure_tier"] != UNAVAILABLE


def test_match_combined_high_when_both_high():
    home = _team_ctx(goals_for_avg=1.9, under_2_5_rate=0.3, btts_rate=0.7)
    away = _team_ctx(goals_for_avg=1.85, under_2_5_rate=0.3, btts_rate=0.7)
    match = {"home_team": home, "away_team": away}
    ctx = calculate_match_goal_pressure_context(match)
    assert ctx["available"] is True
    assert ctx["combined"]["pressure_tier"] == HIGH_PRESSURE
    assert ctx["combined"]["flags"]["both_teams_high"] is True


def test_match_combined_low_when_both_low():
    home = _team_ctx(goals_for_avg=0.9, goals_against_avg=0.9, under_2_5_rate=0.75, clean_sheet_rate=0.40)
    away = _team_ctx(goals_for_avg=0.95, goals_against_avg=0.95, under_2_5_rate=0.72, clean_sheet_rate=0.40)
    ctx = calculate_match_goal_pressure_context({"home_team": home, "away_team": away})
    assert ctx["combined"]["pressure_tier"] == LOW_PRESSURE
    assert ctx["combined"]["flags"]["both_teams_low"] is True


def test_derive_pressure_impact_under_high():
    ctx = {
        "available": True,
        "combined": {"pressure_tier": HIGH_PRESSURE,
                       "flags": {"both_teams_high": True}},
    }
    out = derive_goal_pressure_impact(ctx, pick_market="Under 2.5")
    assert out["applied"] is True
    assert out["fragility_delta"] > 0
    assert out["confidence_delta"] < 0
    assert RC_UNDER_PICK_HIGH_PRESSURE in out["reason_codes"]


def test_derive_pressure_impact_under_moderate():
    ctx = {
        "available": True,
        "combined": {"pressure_tier": MODERATE_PRESSURE,
                       "flags": {"any_team_high": True}},
    }
    out = derive_goal_pressure_impact(ctx, pick_market="Under 2.5")
    assert out["applied"] is True
    assert RC_UNDER_PICK_MODERATE_PRESSURE in out["reason_codes"]


def test_derive_pressure_impact_unavailable_inert():
    out = derive_goal_pressure_impact(None, pick_market="Under 2.5")
    assert out == {"applied": False, "fragility_delta": 0,
                   "confidence_delta": 0, "reason_codes": []}


# ═════════════════════════════════════════════════════════════════════
# Snapshot builder
# ═════════════════════════════════════════════════════════════════════
def test_build_pregame_snapshot_minimal():
    snap = build_pregame_snapshot({
        "match_id": "m1",
        "home_team": {"id": 1, "name": "H", "context": {}},
        "away_team": {"id": 2, "name": "A", "context": {}},
    })
    assert snap["available"] is True
    assert snap["match_id"] == "m1"
    assert "goal_pressure_profile" in snap
    assert snap["odds_digest"]["available"] is False


def test_build_pregame_snapshot_invalid_input():
    snap = build_pregame_snapshot(None)
    assert snap["available"] is False


def test_build_full_intelligence_snapshot_with_live():
    snap = build_full_intelligence_snapshot(
        {
            "match_id": "m2",
            "home_team": {"id": 1, "name": "H"},
            "away_team": {"id": 2, "name": "A"},
            "live_stats": {"minute": 30, "goals_home": 0, "goals_away": 0},
        },
        selected_market="Under 3.5",
        pattern_keys=["LOW_PRESSURE"],
        pick_payload={"recommendation": {"market": "Under 2.5"}},
    )
    assert snap["version"].startswith("football_moneyball.snapshot.")
    assert snap["live"] is not None
    assert snap["pattern_keys"] == ["LOW_PRESSURE"]
    assert snap["selected_market"] == "Under 3.5"


# ═════════════════════════════════════════════════════════════════════
# Pattern memory
# ═════════════════════════════════════════════════════════════════════
def test_derive_pattern_keys_low_pressure_under_profile():
    match = {
        "home_team": _team_ctx(goals_for_avg=0.9, goals_against_avg=0.9,
                                under_2_5_rate=0.75, under_3_5_rate=0.85,
                                clean_sheet_rate=0.40, btts_rate=0.35),
        "away_team": _team_ctx(goals_for_avg=0.95, goals_against_avg=0.95,
                                under_2_5_rate=0.72, under_3_5_rate=0.80,
                                clean_sheet_rate=0.40, btts_rate=0.35),
    }
    snap = build_pregame_snapshot(match)
    keys = derive_pattern_keys({"pregame": snap})
    assert "BOTH_TEAMS_LOW_PRESSURE_UNDER_PROFILE" in keys
    assert "PROTECTED_UNDER_3_5_OVER_UNDER_2_5" in keys


def test_derive_pattern_keys_high_pressure_btts():
    match = {
        "home_team": _team_ctx(goals_for_avg=1.9, under_2_5_rate=0.3, btts_rate=0.70),
        "away_team": _team_ctx(goals_for_avg=1.85, under_2_5_rate=0.3, btts_rate=0.70),
    }
    snap = build_pregame_snapshot(match)
    keys = derive_pattern_keys({"pregame": snap})
    assert "HIGH_PRESSURE_BOTH_SIDES" in keys
    assert "BTTS_PROFILE_STRONG" in keys


def test_derive_pattern_keys_empty_on_missing():
    assert derive_pattern_keys(None) == []
    assert derive_pattern_keys({}) == []


@pytest.mark.asyncio
async def test_pattern_memory_low_sample_no_adjustment(fake_db):
    await fake_db["football_pattern_memory"].insert_one({
        "pattern_key": "X", "sample_size": 5, "wins": 3,
        "hit_rate": 0.6, "roi": 0.10, "enabled": True,
    })
    s = await lookup_pattern_match(fake_db, ["X"])
    assert s["sample_size"] == 5
    assert s["confidence_adjustment"] == 0.0
    assert s["warning"]


@pytest.mark.asyncio
async def test_pattern_memory_strong_positive_roi(fake_db):
    await fake_db["football_pattern_memory"].insert_one({
        "pattern_key": "Y", "sample_size": 60, "wins": 39,
        "hit_rate": 0.65, "roi": 0.20, "enabled": True,
        "best_market": "Under 3.5",
    })
    s = await lookup_pattern_match(fake_db, ["Y"])
    assert s["sample_size"] == 60
    assert s["confidence_adjustment"] > 0
    assert s["best_market"] == "Under 3.5"


@pytest.mark.asyncio
async def test_pattern_memory_disabled_blocks_adjustment(fake_db):
    await fake_db["football_pattern_memory"].insert_one({
        "pattern_key": "Z", "sample_size": 100, "wins": 70,
        "hit_rate": 0.70, "roi": 0.30, "enabled": False,
    })
    s = await lookup_pattern_match(fake_db, ["Z"])
    assert s["enabled"] is False
    assert s["confidence_adjustment"] == 0.0


# ═════════════════════════════════════════════════════════════════════
# Market selection
# ═════════════════════════════════════════════════════════════════════
def test_market_selection_no_inputs_degrades_safely():
    out = select_football_market({}, pregame_snapshot={}, pattern_match={})
    ms = out["market_selection"]
    assert ms["recommended_market"] == MKT_WATCHLIST
    assert "FOOTBALL_MARKET_SELECTION_NO_INPUTS" in ms["reason_codes"]


def test_market_selection_under_25_to_under_35_when_high_pressure():
    pp = {"recommendation": {"market": "Under 2.5", "confidence_score": 55, "fragility": 50}}
    pressure = {
        "available": True,
        "combined": {"pressure_tier": HIGH_PRESSURE,
                      "flags": {"both_teams_high": True, "any_team_high": True}},
    }
    pre = {"pregame": {"goal_pressure_profile": pressure}}
    out = select_football_market(pp, pregame_snapshot=pre)
    ms = out["market_selection"]
    assert ms["protected_alternative"] == MKT_UNDER_35
    assert RC_UNDER_3_5_PREFERRED_OVER_2_5 in ms["reason_codes"]


def test_market_selection_moneyline_offers_double_chance_when_fragile():
    pp = {"recommendation": {"market": "Moneyline", "confidence_score": 50, "fragility": 65}}
    out = select_football_market(pp, pregame_snapshot={"pregame": {}})
    ms = out["market_selection"]
    assert ms["protected_alternative"] == MKT_DOUBLE_CHANCE_1X
    assert RC_DOUBLE_CHANCE_SAFER_THAN_ML in ms["reason_codes"]


def test_market_selection_league_quality_low_pushes_watchlist():
    pp = {
        "recommendation": {"market": "Under 2.5"},
        "_football_quality": {"classification": "EXOTIC_LEAGUE_WARNING"},
    }
    out = select_football_market(pp, pregame_snapshot={"pregame": {}})
    ms = out["market_selection"]
    assert RC_LEAGUE_QUALITY_LOW in ms["reason_codes"]
    assert ms["watchlist"] is True


def test_market_selection_manual_odds_required_when_no_odds():
    pp = {"recommendation": {"market": "Under 2.5"}}
    out = select_football_market(pp, pregame_snapshot={"pregame": {"odds_digest": {"available": False}}})
    ms = out["market_selection"]
    assert RC_MANUAL_ODDS_REVIEW_REQUIRED in ms["reason_codes"]
    assert ms["requires_manual_odds"] is True


def test_market_selection_pattern_memory_hint_when_strong():
    pp = {
        "recommendation": {"market": "Under 2.5"},
        "odds_snapshots": [{"home": 2, "away": 2, "draw": 3}],
        "historical_pattern_match": {
            "best_historical_market": "BTTS No",
            "sample_size": 80,
            "historical_roi": 0.15,
        },
    }
    out = select_football_market(pp, pregame_snapshot={"pregame": {"odds_digest": {"available": True}}})
    ms = out["market_selection"]
    assert RC_PATTERN_MEMORY_PREFERRED_MARKET in ms["reason_codes"] or ms["protected_alternative"] == "BTTS No"


# ═════════════════════════════════════════════════════════════════════
# Feedback loop
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_record_outcome_won_updates_pattern_memory(fake_db):
    res = await record_football_pick_outcome(
        fake_db,
        match_id="m1", user_id="u1",
        market="Under 3.5", selection="Under 3.5",
        odds=1.50, stake=10.0,
        outcome="won",
        pattern_keys=["PROTECTED_UNDER_3_5_OVER_UNDER_2_5"],
    )
    assert res["available"] is True
    assert res["persisted"] is True
    assert res["won"] is True
    assert res["payout"] == pytest.approx(15.0)
    # Pattern memory must have a row now.
    mem = await fake_db["football_pattern_memory"].find_one(
        {"pattern_key": "PROTECTED_UNDER_3_5_OVER_UNDER_2_5"},
    )
    assert mem is not None
    assert mem["sample_size"] == 1
    assert mem["wins"] == 1


@pytest.mark.asyncio
async def test_record_outcome_lost_no_wins(fake_db):
    res = await record_football_pick_outcome(
        fake_db, match_id="m2", user_id="u1",
        market="Moneyline", selection="Home",
        odds=2.0, stake=10.0,
        outcome="lost", pattern_keys=["X"],
    )
    assert res["won"] is False
    mem = await fake_db["football_pattern_memory"].find_one({"pattern_key": "X"})
    assert mem["wins"] == 0
    assert mem["sample_size"] == 1


@pytest.mark.asyncio
async def test_record_outcome_db_none_failsoft():
    res = await record_football_pick_outcome(
        None, match_id="m1", user_id="u1", market="Under 2.5",
    )
    assert res["available"] is False


# ═════════════════════════════════════════════════════════════════════
# Pattern matcher facade
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_attach_football_intelligence_to_payload(fake_db):
    pp = {"recommendation": {"market": "Under 2.5"}}
    match = {
        "match_id": "m1",
        "home_team": _team_ctx(goals_for_avg=1.9, under_2_5_rate=0.3, btts_rate=0.7),
        "away_team": _team_ctx(goals_for_avg=1.85, under_2_5_rate=0.3, btts_rate=0.7),
    }
    audit = await attach_football_intelligence_to_payload(fake_db, pp, match, persist=True)
    assert audit["available"] is True
    assert audit["market_selection_ok"] is True
    assert "goal_pressure_profile" in pp
    assert "market_selection" in pp
    # Snapshot persisted to fake DB.
    assert audit["snapshot_persisted"] is True


@pytest.mark.asyncio
async def test_attach_football_intelligence_failsoft_no_db():
    pp = {"recommendation": {"market": "Under 2.5"}}
    match = {"match_id": "m1", "home_team": {}, "away_team": {}}
    # Should not raise even with None DB.
    audit = await attach_football_intelligence_to_payload(None, pp, match, persist=False)
    assert audit["available"] is True  # pure stages still ran
    assert "goal_pressure_profile" in pp


def test_compare_live_vs_pregame_escalation():
    pre = {"goal_pressure_profile": {
        "available": True,
        "combined": {"pressure_tier": LOW_PRESSURE, "flags": {}},
    }}
    live = {"goal_pressure_profile": {
        "available": True,
        "combined": {"pressure_tier": HIGH_PRESSURE, "flags": {}},
    }}
    diff = compare_live_vs_pregame(pre, live)
    assert diff["available"] is True
    assert diff["market_recommendation"] == "AVOID"
    assert RC_LIVE_TIER_ESCALATED in diff["reason_codes"]
    assert RC_LIVE_AVOID_MARKET in diff["reason_codes"]


def test_compare_live_vs_pregame_stable_keep():
    pre = {"goal_pressure_profile": {"available": True,
                                       "combined": {"pressure_tier": NEUTRAL_PRESSURE, "flags": {}}}}
    live = {"goal_pressure_profile": {"available": True,
                                        "combined": {"pressure_tier": NEUTRAL_PRESSURE, "flags": {}}}}
    diff = compare_live_vs_pregame(pre, live)
    assert diff["market_recommendation"] == "KEEP"
    assert RC_LIVE_KEEP_MARKET in diff["reason_codes"]


def test_compare_live_vs_pregame_missing_data():
    diff = compare_live_vs_pregame(None, None)
    assert diff["available"] is False
    assert "FOOTBALL_LIVE_NO_DATA" in diff["reason_codes"]
