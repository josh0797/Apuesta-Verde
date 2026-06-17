"""Sprint E.2 · Tests for the odds value detector + alerts persistence.

Pure helpers are covered first, then the async persistence layer with
the same FakeDB pattern used elsewhere in the suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import odds_value_detector as ovd
from services import odds_alerts as oa


# ════════════════════════════════════════════════════════════════════════
# Helpers — synthesize odds_snapshots-shaped docs
# ════════════════════════════════════════════════════════════════════════
def _snap(*, match_id: str, market: str, bookmaker: str,
          outcomes: list[dict], fetched_at=None) -> dict:
    return {
        "snapshot_id":     f"{match_id}-{market}-{bookmaker}",
        "match_id":        match_id,
        "sport_key":       "soccer_epl",
        "event_id":        f"evt-{match_id}",
        "bookmaker_key":   bookmaker,
        "bookmaker_title": bookmaker.title(),
        "market":          market,
        "outcomes":        outcomes,
        "fetched_at":      fetched_at or datetime.now(timezone.utc),
        "snapshot_at":     fetched_at or datetime.now(timezone.utc),
        "source":          "live_odds_monitor_v1",
    }


# ════════════════════════════════════════════════════════════════════════
# Pure detector — primitives
# ════════════════════════════════════════════════════════════════════════
class TestPrimitives:
    def test_safe_price_to_implied(self):
        assert ovd._safe_price_to_implied(2.0) == 0.5
        assert ovd._safe_price_to_implied(1.0) is None
        assert ovd._safe_price_to_implied(0.5) is None
        assert ovd._safe_price_to_implied(None) is None
        assert ovd._safe_price_to_implied("bad") is None

    def test_severity_ladder(self):
        assert ovd._severity_from_value(1.0, low=1.0, high=3.0) == "MEDIUM"
        assert ovd._severity_from_value(3.0, low=1.0, high=3.0) == "HIGH"
        assert ovd._severity_from_value(0.5, low=1.0, high=3.0) == "LOW"


# ════════════════════════════════════════════════════════════════════════
# Pure detector — index_latest_snapshots
# ════════════════════════════════════════════════════════════════════════
class TestIndexLatestSnapshots:
    def test_keeps_only_most_recent_per_book(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=10)
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.10}],
                  fetched_at=old),
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.20}],
                  fetched_at=now),
        ]
        idx = ovd.index_latest_snapshots(snaps)
        key = ("m1", "h2h", "Home", None)
        assert idx[key]["pinnacle"]["price"] == 2.20

    def test_skips_malformed(self):
        snaps = [
            None,                  # not dict
            {"market": "h2h"},     # no match_id
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                   outcomes=[{"name": "Home", "price": 2.0}]),
        ]
        idx = ovd.index_latest_snapshots(snaps)
        assert len(idx) == 1


# ════════════════════════════════════════════════════════════════════════
# Pure detector — OUTLIER + DISPERSION
# ════════════════════════════════════════════════════════════════════════
class TestOutlierAndDispersion:
    def test_no_signal_with_too_few_books(self):
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker=b,
                  outcomes=[{"name": "Home", "price": 2.0}])
            for b in ("a", "b")
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_outlier_and_dispersion_signals(indexed=idx,
                                                          min_books=3)
        assert sigs == []

    def test_outlier_flagged_when_one_book_far_from_consensus(self):
        # Pinnacle is the outlier: implies probability is way off.
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="bet365",
                  outcomes=[{"name": "Home", "price": 2.00}]),  # 50%
            _snap(match_id="m1", market="h2h", bookmaker="bwin",
                  outcomes=[{"name": "Home", "price": 2.05}]),  # 48.8%
            _snap(match_id="m1", market="h2h", bookmaker="williamhill",
                  outcomes=[{"name": "Home", "price": 2.02}]),  # 49.5%
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 3.50}]),  # 28.6% ← outlier
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_outlier_and_dispersion_signals(
            indexed=idx, min_books=3,
            outlier_z=2.0, dispersion_pp=99.0,    # disable dispersion
        )
        outliers = [s for s in sigs if s["signal_type"] == "OUTLIER"]
        assert outliers
        assert outliers[0]["bookmaker_key"] == "pinnacle"
        assert outliers[0]["z_score"] != 0
        assert outliers[0]["severity"] in ("MEDIUM", "HIGH")

    def test_dispersion_signal(self):
        # Wide spread: 50% (Pinnacle) vs 35.7% (bet365).
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.00}]),
            _snap(match_id="m1", market="h2h", bookmaker="bet365",
                  outcomes=[{"name": "Home", "price": 2.80}]),
            _snap(match_id="m1", market="h2h", bookmaker="bwin",
                  outcomes=[{"name": "Home", "price": 2.40}]),
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_outlier_and_dispersion_signals(
            indexed=idx, min_books=3, outlier_z=99.0,  # disable outlier
            dispersion_pp=4.0,
        )
        disp = [s for s in sigs if s["signal_type"] == "DISPERSION"]
        assert disp
        assert disp[0]["dispersion_pp"] >= 4.0
        assert disp[0]["n_books"] == 3


# ════════════════════════════════════════════════════════════════════════
# Pure detector — EDGE_VS_MODEL
# ════════════════════════════════════════════════════════════════════════
class TestEdgeVsModel:
    def test_emits_signal_when_model_beats_consensus(self):
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Arsenal", "price": 2.50}]),  # 40%
            _snap(match_id="m1", market="h2h", bookmaker="bet365",
                  outcomes=[{"name": "Arsenal", "price": 2.45}]),  # 40.8%
            _snap(match_id="m1", market="h2h", bookmaker="bwin",
                  outcomes=[{"name": "Arsenal", "price": 2.55}]),  # 39.2%
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_edge_signals(
            indexed=idx,
            model_probs={("m1", "h2h", "Arsenal", None): 0.55},  # model says 55%
            min_edge_pp=5.0, min_books=3,
        )
        assert len(sigs) == 1
        s = sigs[0]
        assert s["signal_type"] == "EDGE_VS_MODEL"
        assert s["edge_pp"] >= 5.0
        assert s["best_bookmaker"] is not None

    def test_no_signal_when_model_below_implied(self):
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker=b,
                  outcomes=[{"name": "Arsenal", "price": 1.50}])
            for b in ("a", "b", "c")
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_edge_signals(
            indexed=idx,
            model_probs={("m1", "h2h", "Arsenal", None): 0.50},
        )
        assert sigs == []

    def test_skipped_when_no_model_prob(self):
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker=b,
                  outcomes=[{"name": "Arsenal", "price": 2.50}])
            for b in ("a", "b", "c")
        ]
        idx = ovd.index_latest_snapshots(snaps)
        sigs = ovd.detect_edge_signals(indexed=idx, model_probs={})
        assert sigs == []


# ════════════════════════════════════════════════════════════════════════
# Pure detector — FAST_MOVE
# ════════════════════════════════════════════════════════════════════════
class TestFastMove:
    def test_fast_move_detected(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=8)
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.50}],
                  fetched_at=old),                   # 40%
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.00}],
                  fetched_at=now),                   # 50% → +10pp move
        ]
        sigs = ovd.detect_fast_move_signals(
            snapshots_by_match={"m1": snaps},
            window_seconds=600, fast_move_pp=4.0,
        )
        assert len(sigs) == 1
        assert sigs[0]["signal_type"] == "FAST_MOVE"
        assert sigs[0]["delta_pp"] >= 4.0
        assert sigs[0]["bookmaker_key"] == "pinnacle"

    def test_no_signal_when_move_below_threshold(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=5)
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.00}],
                  fetched_at=old),
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.01}],
                  fetched_at=now),
        ]
        sigs = ovd.detect_fast_move_signals(
            snapshots_by_match={"m1": snaps},
            window_seconds=600, fast_move_pp=4.0,
        )
        assert sigs == []


# ════════════════════════════════════════════════════════════════════════
# Pure detector — detect_all_signals
# ════════════════════════════════════════════════════════════════════════
class TestDetectAllSignals:
    def test_emits_at_least_dispersion_for_wide_spread(self):
        snaps = [
            _snap(match_id="m1", market="h2h", bookmaker="pinnacle",
                  outcomes=[{"name": "Home", "price": 2.00}]),
            _snap(match_id="m1", market="h2h", bookmaker="bet365",
                  outcomes=[{"name": "Home", "price": 2.80}]),
            _snap(match_id="m1", market="h2h", bookmaker="bwin",
                  outcomes=[{"name": "Home", "price": 2.40}]),
        ]
        out = ovd.detect_all_signals(snapshots=snaps)
        assert out["stats"]["outcomes"] >= 1
        assert any(s["signal_type"] == "DISPERSION" for s in out["signals"])


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
    def __init__(self):
        self.docs: list[dict] = []

    def find(self, query=None, projection=None, sort=None, limit=None):
        q = query or {}

        def _match(d):
            for k, v in q.items():
                if isinstance(v, dict) and "$gte" in v:
                    cur = d.get(k)
                    if cur is None:
                        return False
                    try:
                        if cur < v["$gte"]:
                            return False
                    except TypeError:
                        return False
                else:
                    if d.get(k) != v:
                        return False
            return True

        return _AsyncCursor([d for d in self.docs if _match(d)])

    async def find_one(self, query=None, sort=None, **kwargs):
        async for d in self.find(query or {}):
            return d
        return None

    async def insert_one(self, doc):
        self.docs.append(doc)

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set") or {})
                for k, inc in (update.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + inc
                return type("R", (), {"matched_count": 1,
                                       "modified_count": 1,
                                       "upserted_id": None})()
        return type("R", (), {"matched_count": 0,
                               "modified_count": 0,
                               "upserted_id": None})()


class FakeDB:
    def __init__(self):
        self.odds_alerts = FakeCollection()


# ════════════════════════════════════════════════════════════════════════
# Persistence layer (services.odds_alerts)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_persist_signals_inserts_new_alerts():
    db = FakeDB()
    sigs = [
        {"signal_type": "OUTLIER", "match_id": "m1", "market": "h2h",
         "outcome_name": "Home", "outcome_point": None,
         "bookmaker_key": "pinnacle", "severity": "HIGH"},
        {"signal_type": "DISPERSION", "match_id": "m1", "market": "h2h",
         "outcome_name": "Home", "outcome_point": None,
         "severity": "MEDIUM"},
    ]
    r = await oa.persist_signals(db, signals=sigs)
    assert r["inserted"] == 2
    assert r["deduped"] == 0
    assert len(db.odds_alerts.docs) == 2
    assert all(d["acked"] is False for d in db.odds_alerts.docs)


@pytest.mark.asyncio
async def test_persist_signals_dedupes_same_fingerprint():
    db = FakeDB()
    sig = {
        "signal_type": "OUTLIER", "match_id": "m1", "market": "h2h",
        "outcome_name": "Home", "outcome_point": None,
        "bookmaker_key": "pinnacle", "severity": "HIGH",
    }
    r1 = await oa.persist_signals(db, signals=[sig])
    r2 = await oa.persist_signals(db, signals=[sig])
    assert r1["inserted"] == 1 and r1["deduped"] == 0
    assert r2["inserted"] == 0 and r2["deduped"] == 1
    assert len(db.odds_alerts.docs) == 1
    assert db.odds_alerts.docs[0]["occurrences"] == 2


@pytest.mark.asyncio
async def test_list_alerts_filters():
    db = FakeDB()
    await oa.persist_signals(db, signals=[
        {"signal_type": "OUTLIER", "match_id": "m1", "market": "h2h",
         "outcome_name": "Home", "severity": "HIGH",
         "bookmaker_key": "pinnacle"},
        {"signal_type": "DISPERSION", "match_id": "m2", "market": "totals",
         "outcome_name": "Over", "outcome_point": 2.5, "severity": "LOW"},
    ])
    only_m1 = await oa.list_alerts(db, match_id="m1", since_hours=0)
    assert len(only_m1) == 1
    assert only_m1[0]["match_id"] == "m1"

    only_disp = await oa.list_alerts(db, signal_type="DISPERSION",
                                       since_hours=0)
    assert len(only_disp) == 1
    assert only_disp[0]["signal_type"] == "DISPERSION"


@pytest.mark.asyncio
async def test_ack_alert_marks_acked():
    db = FakeDB()
    await oa.persist_signals(db, signals=[{
        "signal_type": "OUTLIER", "match_id": "m1", "market": "h2h",
        "outcome_name": "Home", "severity": "HIGH",
        "bookmaker_key": "pinnacle",
    }])
    alert_id = db.odds_alerts.docs[0]["alert_id"]
    r = await oa.ack_alert(db, alert_id=alert_id, acked_by="qa@team.com")
    assert r["ok"] is True
    assert db.odds_alerts.docs[0]["acked"] is True
    assert db.odds_alerts.docs[0]["acked_by"] == "qa@team.com"


@pytest.mark.asyncio
async def test_ack_alert_missing_id_returns_error():
    db = FakeDB()
    r = await oa.ack_alert(db, alert_id="")
    assert r["ok"] is False
    assert r["reason_code"] == "ALERT_ID_REQUIRED"


@pytest.mark.asyncio
async def test_persist_signals_ignores_malformed():
    db = FakeDB()
    r = await oa.persist_signals(db, signals=[None, "string", {}])
    # ``{}`` is technically valid (will just have None fields) — it
    # still counts as inserted. The other two are errors.
    assert r["errors"] == 2
    assert r["inserted"] == 1
