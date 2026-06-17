"""Sprint-B · B1 — Tests for football_match_learning_snapshots.

Covers:
* Schema invariants (UUID ids, UTC datetimes, required key set).
* Snapshot manager CRUD (create / refresh / settle) on an in-memory
  fake Mongo client (no network).
* Pre-match aggregator cascade (TheStatsAPI → API-Sports → Scrape.do)
  with injected adapter fakes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from services.football_learning_snapshot_schema import (
    SNAPSHOT_PRE_MATCH,
    SCRAPE_COMPLETE,
    SCRAPE_PARTIAL,
    SCRAPE_FAILED,
    REQUIRED_PRE_MATCH_KEYS,
    REQUIRED_POST_MATCH_KEYS,
    RC_PRE_MATCH_SNAPSHOT_CREATED,
    RC_PRE_MATCH_SNAPSHOT_REFRESHED,
    RC_POST_MATCH_RESULT_SETTLED,
    RC_POST_MATCH_CORNERS_MISSING,
    new_snapshot_doc,
    build_empty_pre_match_inputs,
    build_empty_post_match_outputs,
    validate_pre_match_completeness,
    validate_post_match_completeness,
    stamp_source_audit_entry,
)
from services.football_learning_snapshot_manager import (
    COLLECTION,
    create_pre_match_snapshot,
    refresh_pre_match_snapshot,
    settle_post_match,
    get_snapshot,
)
from services.football_pre_match_data_aggregator import (
    SRC_THESTATSAPI, SRC_API_SPORTS, SRC_SCRAPE_DO,
    gather_pre_match_data,
    _merge_inputs,
    _is_complete,
)


# ════════════════════════════════════════════════════════════════════
# 1. Schema layer
# ════════════════════════════════════════════════════════════════════
class TestSchema:
    def test_new_snapshot_has_uuid_id(self):
        doc = new_snapshot_doc(match_id=1, home_team="A", away_team="B")
        assert isinstance(doc["_id"], str)
        # Validate UUID format.
        uuid.UUID(doc["_id"])

    def test_new_snapshot_timestamps_are_utc(self):
        doc = new_snapshot_doc(match_id=1, home_team="A", away_team="B")
        assert doc["snapshot_taken_at"].tzinfo == timezone.utc
        assert doc["created_at"].tzinfo == timezone.utc

    def test_new_snapshot_carries_initial_reason_code(self):
        doc = new_snapshot_doc(match_id=1, home_team="A", away_team="B")
        assert RC_PRE_MATCH_SNAPSHOT_CREATED in doc["reason_codes"]

    def test_pre_match_inputs_has_canonical_key_set(self):
        inputs = build_empty_pre_match_inputs()
        for k in REQUIRED_PRE_MATCH_KEYS:
            assert k in inputs, f"missing required key {k}"
        assert "market_odds" in inputs
        # Market odds nested dict.
        assert "over25" in inputs["market_odds"]
        assert "btts_yes" in inputs["market_odds"]

    def test_post_match_outputs_has_canonical_key_set(self):
        outputs = build_empty_post_match_outputs()
        for k in REQUIRED_POST_MATCH_KEYS:
            assert k in outputs

    def test_validate_pre_match_completeness(self):
        is_complete, missing = validate_pre_match_completeness({})
        assert not is_complete
        assert set(missing) == set(REQUIRED_PRE_MATCH_KEYS)

        full = {k: 1.0 for k in REQUIRED_PRE_MATCH_KEYS}
        is_complete, missing = validate_pre_match_completeness(full)
        assert is_complete
        assert missing == []

    def test_stamp_source_audit_entry(self):
        audit = {"pre_match_sources": [], "post_match_sources": [],
                 "scrape_status": "PENDING"}
        stamp_source_audit_entry(audit, bucket="pre_match_sources",
                                  source="thestatsapi", status=SCRAPE_COMPLETE,
                                  fields_filled=["home_xg_l5"])
        assert len(audit["pre_match_sources"]) == 1
        e = audit["pre_match_sources"][0]
        assert e["source"] == "thestatsapi"
        assert e["status"] == SCRAPE_COMPLETE
        assert "fetched_at" in e
        assert e["fields_filled"] == ["home_xg_l5"]

    def test_stamp_source_audit_entry_with_error(self):
        audit = {"pre_match_sources": [], "post_match_sources": [],
                 "scrape_status": "PENDING"}
        stamp_source_audit_entry(audit, bucket="pre_match_sources",
                                  source="api_sports", status=SCRAPE_FAILED,
                                  error="HTTP 500")
        assert audit["pre_match_sources"][0]["error"] == "HTTP 500"


# ════════════════════════════════════════════════════════════════════
# 2. In-memory Mongo fake (just what the manager needs)
# ════════════════════════════════════════════════════════════════════
class _FakeCol:
    def __init__(self):
        self._docs: list[dict] = []
        self._indexes: list[str] = []

    async def create_index(self, _key, **_kwargs):
        self._indexes.append(_kwargs.get("name", "ix"))

    async def insert_one(self, doc):
        # Mimic unique index on match_id.
        if any(d["match_id"] == doc["match_id"] for d in self._docs):
            raise RuntimeError("DuplicateKeyError")
        self._docs.append(dict(doc))

    async def find_one(self, query):
        for d in self._docs:
            ok = all(d.get(k) == v for k, v in query.items())
            if ok:
                return dict(d)
        return None

    async def update_one(self, query, update):
        for i, d in enumerate(self._docs):
            if all(d.get(k) == v for k, v in query.items()):
                self._docs[i] = self._apply_update(dict(d), update)
                return
        raise RuntimeError("no match")

    @staticmethod
    def _apply_update(doc, update):
        for op, payload in update.items():
            if op == "$set":
                for k, v in payload.items():
                    _set_path(doc, k, v)
            elif op == "$push":
                for k, v in payload.items():
                    cur = _get_path(doc, k) or []
                    each = v.get("$each", []) if isinstance(v, dict) else [v]
                    _set_path(doc, k, list(cur) + list(each))
            elif op == "$addToSet":
                for k, v in payload.items():
                    cur = list(_get_path(doc, k) or [])
                    each = v.get("$each", []) if isinstance(v, dict) else [v]
                    for x in each:
                        if x not in cur:
                            cur.append(x)
                    _set_path(doc, k, cur)
        return doc


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _get_path(doc, path):
    parts = path.split(".")
    cur = doc
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


class _FakeDb:
    def __init__(self):
        self._cols: dict[str, _FakeCol] = {}

    def __getitem__(self, name):
        return self._cols.setdefault(name, _FakeCol())


# ════════════════════════════════════════════════════════════════════
# 3. Manager — CRUD against the fake db
# ════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestManager:
    async def test_create_snapshot_persists_and_returns(self):
        db = _FakeDb()
        snap = await create_pre_match_snapshot(
            db,
            match_id=42, home_team="France", away_team="Senegal",
            competition="WC 2026",
        )
        assert snap["match_id"] == 42
        assert snap["home_team"] == "France"
        assert snap["snapshot_type"] == SNAPSHOT_PRE_MATCH

    async def test_create_snapshot_is_idempotent(self):
        db = _FakeDb()
        s1 = await create_pre_match_snapshot(db, match_id=1, home_team="A", away_team="B")
        s2 = await create_pre_match_snapshot(db, match_id=1, home_team="A", away_team="B")
        # Same _id → exact same document returned.
        assert s1["_id"] == s2["_id"]

    async def test_create_with_initial_inputs_merges(self):
        db = _FakeDb()
        snap = await create_pre_match_snapshot(
            db, match_id=1, home_team="A", away_team="B",
            initial_inputs={"home_xg_l5": 2.1, "away_xg_l5": 1.4,
                            "market_odds": {"over25": 1.85}},
        )
        assert snap["pre_match_inputs"]["home_xg_l5"] == 2.1
        assert snap["pre_match_inputs"]["market_odds"]["over25"] == 1.85

    async def test_refresh_merges_without_wiping_previous(self):
        db = _FakeDb()
        await create_pre_match_snapshot(
            db, match_id=1, home_team="A", away_team="B",
            initial_inputs={"home_xg_l5": 2.1},
        )
        updated = await refresh_pre_match_snapshot(
            db, match_id=1,
            refreshed_inputs={"away_xg_l5": 1.4,
                              "lineup_home": "FRA XI"},
        )
        # Old value preserved + new added.
        assert updated["pre_match_inputs"]["home_xg_l5"] == 2.1
        assert updated["pre_match_inputs"]["away_xg_l5"] == 1.4
        assert updated["pre_match_inputs"]["lineup_home"] == "FRA XI"
        # Refresh stamp.
        assert RC_PRE_MATCH_SNAPSHOT_REFRESHED in updated["reason_codes"]
        assert updated["snapshot_refreshed_at"] is not None

    async def test_settle_post_match_derives_hit_booleans(self):
        db = _FakeDb()
        await create_pre_match_snapshot(db, match_id=1, home_team="A", away_team="B")
        result = await settle_post_match(
            db, match_id=1,
            outputs={"home_goals": 3, "away_goals": 1, "total_corners": 11},
        )
        out = result["post_match_outputs"]
        assert out["total_goals"]      == 4
        assert out["final_score"]      == "3-1"
        assert out["draw_hit"]         is False
        assert out["btts_hit"]         is True
        assert out["over25_hit"]       is True
        assert out["over85_corners_hit"] is True
        assert RC_POST_MATCH_RESULT_SETTLED in result["reason_codes"]

    async def test_settle_with_draw(self):
        db = _FakeDb()
        await create_pre_match_snapshot(db, match_id=1, home_team="Spain", away_team="CV")
        result = await settle_post_match(
            db, match_id=1,
            outputs={"home_goals": 0, "away_goals": 0},
        )
        out = result["post_match_outputs"]
        assert out["draw_hit"]   is True
        assert out["btts_hit"]   is False
        assert out["over25_hit"] is False

    async def test_settle_without_corners_stamps_missing_code(self):
        db = _FakeDb()
        await create_pre_match_snapshot(db, match_id=1, home_team="A", away_team="B")
        result = await settle_post_match(
            db, match_id=1,
            outputs={"home_goals": 1, "away_goals": 1},
        )
        assert RC_POST_MATCH_CORNERS_MISSING in result["reason_codes"]

    async def test_settle_fails_when_snapshot_missing(self):
        db = _FakeDb()
        result = await settle_post_match(
            db, match_id=999, outputs={"home_goals": 1, "away_goals": 0},
        )
        assert result.get("available") is False
        assert result.get("error") == "snapshot_not_found"


# ════════════════════════════════════════════════════════════════════
# 4. Aggregator cascade
# ════════════════════════════════════════════════════════════════════
async def _fake_adapter_thestatsapi_full(home_team, away_team, match_id, **ctx):
    return {
        "home_xg_l5": 2.1, "away_xg_l5": 1.4,
        "home_corners_l5": 6.2, "away_corners_l5": 5.1,
        "btts_probability": 0.62, "over25_probability": 0.64,
    }, SCRAPE_COMPLETE


async def _fake_adapter_thestatsapi_partial(home_team, away_team, match_id, **ctx):
    # Only xG, no corners.
    return {
        "home_xg_l5": 2.1, "away_xg_l5": 1.4,
    }, SCRAPE_PARTIAL


async def _fake_adapter_api_sports_corners(home_team, away_team, match_id, **ctx):
    return {
        "home_corners_l5": 6.2, "away_corners_l5": 5.1,
        "btts_probability": 0.62, "over25_probability": 0.64,
    }, SCRAPE_PARTIAL


async def _fake_adapter_fail(home_team, away_team, match_id, **ctx):
    return {}, SCRAPE_FAILED


async def _fake_adapter_raises(home_team, away_team, match_id, **ctx):
    raise RuntimeError("upstream API blew up")


@pytest.mark.asyncio
class TestAggregatorCascade:
    async def test_short_circuits_when_first_adapter_completes(self):
        result = await gather_pre_match_data(
            home_team="France", away_team="Senegal", match_id=1,
            adapters=[
                (SRC_THESTATSAPI, _fake_adapter_thestatsapi_full),
                (SRC_API_SPORTS,  _fake_adapter_api_sports_corners),
            ],
        )
        assert result["status"] == SCRAPE_COMPLETE
        # Only the first adapter ran (short-circuit).
        sources = result["source_audit"]["pre_match_sources"]
        assert len(sources) == 1
        assert sources[0]["source"] == SRC_THESTATSAPI

    async def test_falls_through_when_first_adapter_partial(self):
        result = await gather_pre_match_data(
            home_team="France", away_team="Senegal", match_id=1,
            adapters=[
                (SRC_THESTATSAPI, _fake_adapter_thestatsapi_partial),
                (SRC_API_SPORTS,  _fake_adapter_api_sports_corners),
            ],
        )
        assert result["status"] == SCRAPE_COMPLETE
        sources = result["source_audit"]["pre_match_sources"]
        # Both ran.
        assert {s["source"] for s in sources} == {SRC_THESTATSAPI, SRC_API_SPORTS}
        # Both core sets of fields are now populated.
        i = result["inputs"]
        assert i["home_xg_l5"] == 2.1                # from thestatsapi
        assert i["home_corners_l5"] == 6.2           # from api_sports

    async def test_partial_status_when_all_adapters_partial(self):
        result = await gather_pre_match_data(
            home_team="X", away_team="Y", match_id=1,
            adapters=[
                (SRC_THESTATSAPI, _fake_adapter_thestatsapi_partial),
                (SRC_API_SPORTS,  _fake_adapter_fail),
                (SRC_SCRAPE_DO,   _fake_adapter_fail),
            ],
        )
        assert result["status"] == SCRAPE_PARTIAL

    async def test_raising_adapter_is_audited_not_propagated(self):
        result = await gather_pre_match_data(
            home_team="X", away_team="Y", match_id=1,
            adapters=[
                (SRC_THESTATSAPI, _fake_adapter_raises),
                (SRC_API_SPORTS,  _fake_adapter_thestatsapi_full),
            ],
        )
        # Did not propagate.
        assert result["status"] == SCRAPE_COMPLETE
        sources = result["source_audit"]["pre_match_sources"]
        failed = [s for s in sources if s["source"] == SRC_THESTATSAPI]
        assert failed[0]["status"] == SCRAPE_FAILED
        assert "upstream API blew up" in failed[0].get("error", "")

    async def test_later_adapter_does_not_overwrite_filled_field(self):
        async def _ts(*_a, **_kw):
            return {"home_xg_l5": 2.1}, SCRAPE_PARTIAL

        async def _api(*_a, **_kw):
            # Tries to overwrite with 999 — must be ignored.
            return {"home_xg_l5": 999.0, "away_xg_l5": 1.4,
                    "home_corners_l5": 5.5, "away_corners_l5": 4.0,
                    "btts_probability": 0.6, "over25_probability": 0.55,
                    }, SCRAPE_PARTIAL

        result = await gather_pre_match_data(
            home_team="X", away_team="Y", match_id=1,
            adapters=[(SRC_THESTATSAPI, _ts), (SRC_API_SPORTS, _api)],
        )
        # The 2.1 from the first adapter is preserved.
        assert result["inputs"]["home_xg_l5"] == 2.1
        # The second adapter only filled the previously-empty fields.
        sources = result["source_audit"]["pre_match_sources"]
        api_filled = next(s for s in sources if s["source"] == SRC_API_SPORTS)
        assert "home_xg_l5" not in api_filled["fields_filled"]
        assert "away_xg_l5" in api_filled["fields_filled"]


# ════════════════════════════════════════════════════════════════════
# 5. _merge_inputs helper
# ════════════════════════════════════════════════════════════════════
class TestMergeInputs:
    def test_does_not_overwrite_non_none(self):
        target = build_empty_pre_match_inputs()
        target["home_xg_l5"] = 2.1
        filled = _merge_inputs(target, {"home_xg_l5": 999.0,
                                         "away_xg_l5": 1.4})
        assert target["home_xg_l5"] == 2.1
        assert target["away_xg_l5"] == 1.4
        assert filled == ["away_xg_l5"]

    def test_merges_market_odds_subfield(self):
        target = build_empty_pre_match_inputs()
        filled = _merge_inputs(target, {"market_odds": {"over25": 1.85,
                                                         "btts_yes": 1.95}})
        assert target["market_odds"]["over25"] == 1.85
        assert "market_odds.over25" in filled
        assert "market_odds.btts_yes" in filled

    def test_ignores_unknown_keys(self):
        target = build_empty_pre_match_inputs()
        _merge_inputs(target, {"alien_field": 1.0})
        assert "alien_field" not in target

    def test_is_complete_helper(self):
        target = build_empty_pre_match_inputs()
        assert not _is_complete(target)
        for k in REQUIRED_PRE_MATCH_KEYS:
            target[k] = 1.0
        assert _is_complete(target)
