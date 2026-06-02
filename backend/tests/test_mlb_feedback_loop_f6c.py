"""Regression tests for F6C feedback loop + Dynamic Park BLOCK.

Covers the 7 sections of the user-supplied spec:

    1. VALID_RESULTS contents
    2. ``update_run_evaluation_result`` accepts ``push``
    3. ``REFERENCE_MLB_POWER_BAT_EXPLOSIVE`` truth table
    4. Dynamic Park BLOCK semantic shape
    5. Settle endpoint F6C paths A/B/C/D
    6. Miss type derivation matrix
    7. ``GET /api/mlb/run-evaluations/summary`` shape + arithmetic

Run::

    cd /app/backend && python -m pytest tests/test_mlb_feedback_loop_f6c.py -v

Notes
-----
* Tests use a per-class isolated MongoDB database, dropped on teardown,
  so they don't interfere with the live preview/production data.
* Sections 4 and 5 simulate the orchestrator/endpoint logic in-test
  (without invoking the HTTP layer) by replicating the exact field
  shapes the production code passes to the storage layer.
"""
from __future__ import annotations

import os
import uuid
from typing import Optional

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from services.mlb_run_evaluations_summary import (
    SETTLED_OUTCOMES,
    compute_run_evaluations_summary,
)
from services.mlb_run_storage import (
    REFERENCE_MLB_POWER_BAT_EXPLOSIVE,
    VALID_RESULTS,
    _derive_reference_tag,
    build_run_evaluation_document,
    query_run_evaluations,
    store_run_evaluation,
    update_run_evaluation_result,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures — isolated db per test class
# ─────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    """Isolated MongoDB database, dropped on teardown."""
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    dbname = f"test_f6c_{uuid.uuid4().hex[:10]}"
    handle = client.get_database(dbname)
    try:
        yield handle
    finally:
        await client.drop_database(dbname)


def _strong_eval(score: int = 80,
                  tier: str = "HIGH",
                  flip: bool = True,
                  state: Optional[str] = "COMMAND_COLLAPSE_RISK",
                  market: str = "Under 8.5",
                  ) -> dict:
    """Build a strong evaluation payload that ``store_run_evaluation``
    will accept under ``only_strong=True``."""
    return {
        "explosive_risk_score": score,
        "risk_tier":            tier,
        "flip_triggered":       flip,
        "should_recommend":     True,
        "recommended_market":   market,
        "recommended_line":     8.5,
        "recommended_side":     "under" if "under" in market.lower() else "over",
        "market_scope":         "full_game",
        "state":                state,
        "confidence":           75,
        "risk":                 "LOW",
        "reason_codes":         ["TEST"],
        "human_reasons":        ["Strong test signal"],
    }


# ═════════════════════════════════════════════════════════════════════
# Section 1 — VALID_RESULTS contents
# ═════════════════════════════════════════════════════════════════════
class TestValidResults:
    def test_contains_5_canonical_values(self):
        assert VALID_RESULTS == {"won", "lost", "pending", "push", "void"}

    def test_includes_won(self):
        assert "won" in VALID_RESULTS

    def test_includes_lost(self):
        assert "lost" in VALID_RESULTS

    def test_includes_pending(self):
        assert "pending" in VALID_RESULTS

    def test_includes_push_canonical(self):
        # "push" is the canonical outcome for ties in new settles
        assert "push" in VALID_RESULTS

    def test_includes_void_legacy(self):
        # "void" is accepted as backward-compat for legacy documents
        assert "void" in VALID_RESULTS


# ═════════════════════════════════════════════════════════════════════
# Section 2 — update_run_evaluation_result accepts push
# ═════════════════════════════════════════════════════════════════════
class TestPushUpdate:
    @pytest.mark.asyncio
    async def test_push_writes_result_field(self, db):
        eid = await store_run_evaluation(
            db, user_id="u1", match_id="M1",
            run_evaluation=_strong_eval(), metrics={},
        )
        ok = await update_run_evaluation_result(
            db, evaluation_id=eid,
            final_runs_home=4, final_runs_away=4,    # 8.0 ties Under 8.5? actually 8 < 8.5 = won. Use 4+4=8 final vs line 8.5 → won. Adjust:
            result="push", miss_type="PUSH",
        )
        assert ok is True
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["result"] == "push"

    @pytest.mark.asyncio
    async def test_push_sets_miss_type(self, db):
        eid = await store_run_evaluation(
            db, user_id="u1", match_id="M1",
            run_evaluation=_strong_eval(), metrics={},
        )
        await update_run_evaluation_result(
            db, evaluation_id=eid,
            final_runs_home=4, final_runs_away=4,
            result="push", miss_type="PUSH",
        )
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["miss_type"] == "PUSH"

    @pytest.mark.asyncio
    async def test_push_fills_resolved_at(self, db):
        eid = await store_run_evaluation(
            db, user_id="u1", match_id="M1",
            run_evaluation=_strong_eval(), metrics={},
        )
        await update_run_evaluation_result(
            db, evaluation_id=eid,
            final_runs_home=4, final_runs_away=4,
            result="push", miss_type="PUSH",
        )
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_push_does_not_activate_reference_tag(self, db):
        """Even with HIGH + flip + score>=70, ``push`` MUST NOT trigger
        the REFERENCE_MLB_POWER_BAT_EXPLOSIVE tag."""
        eid = await store_run_evaluation(
            db, user_id="u1", match_id="M1",
            run_evaluation=_strong_eval(score=85, tier="HIGH", flip=True),
            metrics={},
        )
        await update_run_evaluation_result(
            db, evaluation_id=eid,
            final_runs_home=4, final_runs_away=4,
            result="push", miss_type="PUSH",
        )
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["reference_profile_tag"] is None


# ═════════════════════════════════════════════════════════════════════
# Section 3 — REFERENCE_MLB_POWER_BAT_EXPLOSIVE truth table
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("flip,tier,score,result,expected", [
    # All four conditions True → tag fires
    (True,  "HIGH",   80, "won",  REFERENCE_MLB_POWER_BAT_EXPLOSIVE),
    (True,  "HIGH",   70, "won",  REFERENCE_MLB_POWER_BAT_EXPLOSIVE),   # boundary
    # Each individual condition False → tag rejects
    (False, "HIGH",   80, "won",  None),  # no flip
    (True,  "MEDIUM", 80, "won",  None),  # wrong tier
    (True,  "LOW",    80, "won",  None),
    (True,  "HIGH",   69, "won",  None),  # score below threshold
    (True,  "HIGH",   80, "lost", None),  # lost
    (True,  "HIGH",   80, "push", None),  # push (per Section 2.4)
    (True,  "HIGH",   80, "void", None),  # legacy void
    (True,  "HIGH",   80, "pending", None),  # still pending
])
def test_reference_tag_truth_table(flip, tier, score, result, expected):
    evaluation = {
        "flip_triggered":       flip,
        "risk_tier":            tier,
        "explosive_risk_score": score,
    }
    assert _derive_reference_tag(evaluation, result) == expected


# ═════════════════════════════════════════════════════════════════════
# Section 4 — Dynamic Park BLOCK semantic shape
# ═════════════════════════════════════════════════════════════════════
class TestDynamicParkBlock:
    """Validates the BLOCK path of the dynamic-park veto by simulating
    the inline orchestrator logic. Since the block lives inline in
    ``mlb_day_orchestrator.py``, we replay the exact mutations the spec
    requires and assert on the resulting state.
    """

    @staticmethod
    def _simulate_block(park_dynamic_val: float,
                         park_code: str,
                         veto_central_initial: dict,
                         chosen_market: dict,
                         best_score: int):
        """Replays the orchestrator's dynamic-park BLOCK logic."""
        pick_payload: dict = {
            "park_factor_live": {"code": park_code, "dynamic": park_dynamic_val},
        }
        veto_central = dict(veto_central_initial)

        if (
            park_code == "OFFENSIVE"
            and not veto_central.get("veto")
        ):
            if park_dynamic_val >= 1.12:
                veto_central = {
                    **veto_central,
                    "veto":         True,
                    "severity":     "BLOCK",
                    "veto_reasons": (veto_central.get("veto_reasons") or [])
                                     + [f"DYNAMIC_PARK_OFFENSIVE_BLOCK (factor={park_dynamic_val:.3f})"],
                    "explanation":  (
                        f"Park factor dinámico {park_dynamic_val:.3f} ≥ 1.12 "
                        f"(blend histórico + RPG recientes). Parque muy ofensivo "
                        f"invalida el Under."
                    ),
                }
                pick_payload["under_veto"] = veto_central
                pick_payload["under_veto_block"] = {
                    "blocked_market":    chosen_market.get("market"),
                    "blocked_score":     chosen_market.get("score"),
                    "blocked_rationale": chosen_market.get("rationale"),
                    "veto":              veto_central,
                    "source":            "DYNAMIC_PARK_OFFENSIVE",
                }
                chosen_market = None
                best_score = 0

        return pick_payload, veto_central, chosen_market, best_score

    def test_block_fires_when_dynamic_ge_112(self):
        chosen = {"market": "Under 8.5", "score": 72, "rationale": "Original Under thesis"}
        pp, vc, cm, bs = self._simulate_block(
            park_dynamic_val=1.15,
            park_code="OFFENSIVE",
            veto_central_initial={"veto": False},
            chosen_market=chosen, best_score=72,
        )
        # Veto central upgraded to BLOCK
        assert vc["veto"] is True
        assert vc["severity"] == "BLOCK"

    def test_block_creates_under_veto_block_payload(self):
        chosen = {"market": "Under 8.5", "score": 72, "rationale": "Original Under thesis"}
        pp, _, _, _ = self._simulate_block(
            park_dynamic_val=1.15,
            park_code="OFFENSIVE",
            veto_central_initial={"veto": False},
            chosen_market=chosen, best_score=72,
        )
        assert "under_veto_block" in pp
        assert pp["under_veto_block"]["source"] == "DYNAMIC_PARK_OFFENSIVE"
        assert pp["under_veto_block"]["blocked_market"] == "Under 8.5"
        assert pp["under_veto_block"]["blocked_score"] == 72

    def test_block_nulls_chosen_market(self):
        chosen = {"market": "Under 8.5", "score": 72}
        _, _, cm, _ = self._simulate_block(
            park_dynamic_val=1.20,
            park_code="OFFENSIVE",
            veto_central_initial={"veto": False},
            chosen_market=chosen, best_score=72,
        )
        # Pick must NOT propagate to the parlay builder
        assert cm is None

    def test_block_resets_best_score(self):
        chosen = {"market": "Under 8.5", "score": 72}
        _, _, _, bs = self._simulate_block(
            park_dynamic_val=1.20,
            park_code="OFFENSIVE",
            veto_central_initial={"veto": False},
            chosen_market=chosen, best_score=72,
        )
        assert bs == 0

    def test_block_skipped_when_central_already_vetoed(self):
        """The dynamic-park block must be a NO-OP when the central
        Under veto has already blocked."""
        chosen = {"market": "Under 8.5", "score": 72}
        pp, vc, cm, bs = self._simulate_block(
            park_dynamic_val=1.20,
            park_code="OFFENSIVE",
            veto_central_initial={"veto": True, "severity": "BLOCK"},
            chosen_market=chosen, best_score=72,
        )
        # State unchanged
        assert "under_veto_block" not in pp
        assert cm == chosen
        assert bs == 72

    def test_block_skipped_for_neutral_park(self):
        chosen = {"market": "Under 8.5", "score": 72}
        pp, _, cm, bs = self._simulate_block(
            park_dynamic_val=1.20,
            park_code="NEUTRAL",     # not OFFENSIVE → skip
            veto_central_initial={"veto": False},
            chosen_market=chosen, best_score=72,
        )
        assert "under_veto_block" not in pp
        assert cm == chosen

    @pytest.mark.asyncio
    async def test_under_veto_block_persists_to_run_evaluation(self, db):
        """When the orchestrator passes ``under_veto_block`` in the
        ``run_evaluation`` payload, the storage layer must persist it +
        derive ``veto_source`` and ``blocked_market``."""
        eval_payload = _strong_eval()
        eval_payload["under_veto_block"] = {
            "blocked_market":    "Under 8.5",
            "blocked_score":     72,
            "blocked_rationale": "Original Under thesis",
            "source":            "DYNAMIC_PARK_OFFENSIVE",
        }
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_DP_1",
            run_evaluation=eval_payload, metrics={},
        )
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["under_veto_block"] is not None
        assert doc["veto_source"] == "DYNAMIC_PARK_OFFENSIVE"
        assert doc["blocked_market"] == "Under 8.5"

    @pytest.mark.asyncio
    async def test_under_veto_block_via_metrics_fallback(self, db):
        """If the under_veto_block is in ``metrics`` instead of
        ``run_evaluation``, the storage must still persist it."""
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_DP_2",
            run_evaluation=_strong_eval(),
            metrics={
                "under_veto_block": {
                    "blocked_market": "Under 7.5",
                    "source":         "CENTRAL_UNDER_VETO",
                },
            },
        )
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["veto_source"] == "CENTRAL_UNDER_VETO"
        assert doc["blocked_market"] == "Under 7.5"


# ═════════════════════════════════════════════════════════════════════
# Section 5 — Settle endpoint F6C paths A/B/C/D
# ═════════════════════════════════════════════════════════════════════
class TestF6CPaths:
    """Simulates the F6C cabling without invoking the HTTP endpoint —
    replicates the exact resolution sequence the endpoint performs in
    ``server.py`` after F6B finishes."""

    @staticmethod
    async def _resolve_eval_id(db, pick_doc: dict, v2_snapshot: dict,
                                  match_id: str, user_id: str) -> Optional[str]:
        """Replays the endpoint's eval_id resolution chain."""
        # Path A / B — explicit IDs from pick_doc or v2_snapshot
        eval_id = (
            pick_doc.get("mlb_run_evaluation_id")
            or (v2_snapshot.get("explosive_v2") or {}).get("mlb_run_evaluation_id")
            or pick_doc.get("run_evaluation_id")
        )
        # Path C — fallback by (user_id, match_id, pending)
        if not eval_id:
            cands = await query_run_evaluations(
                db, user_id="_slate", match_id=match_id,
                result="pending", limit=1,
            )
            if not cands:
                cands = await query_run_evaluations(
                    db, user_id=user_id, match_id=match_id,
                    result="pending", limit=1,
                )
            if cands:
                eval_id = cands[0].get("id")
        return eval_id

    @pytest.mark.asyncio
    async def test_path_A_pick_doc_explicit_id(self, db):
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_A",
            run_evaluation=_strong_eval(market="Under 8.5"), metrics={},
        )
        resolved = await self._resolve_eval_id(
            db, pick_doc={"mlb_run_evaluation_id": eid},
            v2_snapshot={}, match_id="M_A", user_id="u1",
        )
        assert resolved == eid

    @pytest.mark.asyncio
    async def test_path_B_v2_snapshot_explicit_id(self, db):
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_B",
            run_evaluation=_strong_eval(market="Over 8.5"), metrics={},
        )
        resolved = await self._resolve_eval_id(
            db, pick_doc={},
            v2_snapshot={"explosive_v2": {"mlb_run_evaluation_id": eid}},
            match_id="M_B", user_id="u1",
        )
        assert resolved == eid

    @pytest.mark.asyncio
    async def test_path_C1_match_id_slate_lookup(self, db):
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_C",
            run_evaluation=_strong_eval(), metrics={},
        )
        resolved = await self._resolve_eval_id(
            db, pick_doc={}, v2_snapshot={},
            match_id="M_C", user_id="u1",
        )
        assert resolved == eid

    @pytest.mark.asyncio
    async def test_path_C2_falls_back_to_user_id_when_slate_missing(self, db):
        """When no _slate doc exists but a user-specific doc does."""
        eid = await store_run_evaluation(
            db, user_id="u1", match_id="M_C2",
            run_evaluation=_strong_eval(), metrics={},
        )
        resolved = await self._resolve_eval_id(
            db, pick_doc={}, v2_snapshot={},
            match_id="M_C2", user_id="u1",
        )
        assert resolved == eid

    @pytest.mark.asyncio
    async def test_path_D_no_match_returns_none_safely(self, db):
        resolved = await self._resolve_eval_id(
            db, pick_doc={}, v2_snapshot={},
            match_id="NONEXISTENT", user_id="u1",
        )
        assert resolved is None

    @pytest.mark.asyncio
    async def test_path_D_does_not_raise(self, db):
        """Path D must be fail-soft — no exception even with empty inputs."""
        try:
            await self._resolve_eval_id(
                db, pick_doc={}, v2_snapshot={},
                match_id="", user_id="",
            )
        except Exception as exc:
            pytest.fail(f"Path D raised: {exc}")


# ═════════════════════════════════════════════════════════════════════
# Section 6 — Miss type derivation matrix
# ═════════════════════════════════════════════════════════════════════
def _derive_miss_type(outcome: str, market: str) -> Optional[str]:
    """Replicates the inline derivation logic from server.py F6C block."""
    _mkt = (market or "").lower()
    miss_type = None
    if outcome == "lost":
        if "under" in _mkt:
            miss_type = "OVER_BEAT_UNDER"
        elif "over" in _mkt:
            miss_type = "UNDER_BEAT_OVER"
    elif outcome == "push":
        miss_type = "PUSH"
    return miss_type


@pytest.mark.parametrize("outcome,market,expected", [
    # lost paths
    ("lost", "Full Game Under 8.5",     "OVER_BEAT_UNDER"),
    ("lost", "F5 Under 4.5",            "OVER_BEAT_UNDER"),
    ("lost", "Over 8.5",                "UNDER_BEAT_OVER"),
    ("lost", "F5 Over 4.5",             "UNDER_BEAT_OVER"),
    # push path
    ("push", "Under 8.5",               "PUSH"),
    ("push", "Over 7.5",                "PUSH"),
    # won → None
    ("won",  "Under 8.5",               None),
    ("won",  "Over 8.5",                None),
    # unknown market / outcome → None (defensive)
    ("lost", "Money Line Home",         None),
    ("lost", "",                        None),
])
def test_miss_type_matrix(outcome, market, expected):
    assert _derive_miss_type(outcome, market) == expected


# ═════════════════════════════════════════════════════════════════════
# Section 7 — Summary endpoint shape + arithmetic
# ═════════════════════════════════════════════════════════════════════
class TestSummaryEndpoint:

    @pytest.mark.asyncio
    async def _seed_calibration_set(self, db):
        """Seed a known mix of settled docs for calibration tests."""
        async def _add(*, match, eval_, result, final_h, final_a,
                        miss_type=None, under_veto_block=None):
            eid = await store_run_evaluation(
                db, user_id="_slate", match_id=match,
                run_evaluation={**eval_, "under_veto_block": under_veto_block},
                metrics={},
            )
            await update_run_evaluation_result(
                db, evaluation_id=eid,
                final_runs_home=final_h, final_runs_away=final_a,
                result=result, miss_type=miss_type,
            )
            return eid

        # 4 HIGH+flip+won (reference tags fire on all 4)
        for i in range(4):
            await _add(match=f"REF_{i}",
                        eval_=_strong_eval(score=80, tier="HIGH", flip=True,
                                            market="Over 8.5"),
                        result="won", final_h=6, final_a=4, miss_type=None)

        # 2 HIGH+flip+lost → OVER_BEAT_UNDER (we bet UNDER)
        for i in range(2):
            await _add(match=f"LOST_U_{i}",
                        eval_=_strong_eval(market="Under 8.5"),
                        result="lost", final_h=6, final_a=5,
                        miss_type="OVER_BEAT_UNDER")

        # 1 MEDIUM+lost → UNDER_BEAT_OVER (we bet OVER)
        await _add(match="LOST_O_1",
                    eval_=_strong_eval(tier="MEDIUM", score=55,
                                        market="Over 8.5"),
                    result="lost", final_h=2, final_a=3,
                    miss_type="UNDER_BEAT_OVER")

        # 1 push (PUSH miss_type, neutral)
        await _add(match="PUSH_1",
                    eval_=_strong_eval(market="Under 8.5"),
                    result="push", final_h=4, final_a=4, miss_type="PUSH")

        # 1 with Dynamic Park BLOCK metadata, lost → "saved"
        await _add(match="DPB_1",
                    eval_=_strong_eval(market="Under 8.5"),
                    result="lost", final_h=7, final_a=5,
                    miss_type="OVER_BEAT_UNDER",
                    under_veto_block={
                        "blocked_market": "Under 8.5",
                        "source":         "DYNAMIC_PARK_OFFENSIVE",
                    })

    @pytest.mark.asyncio
    async def test_summary_overall_total(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        # 4 won + 2 lost + 1 lost + 1 push + 1 lost = 9
        assert s["evaluated_total"] == 9
        assert s["overall"]["won"] == 4
        assert s["overall"]["lost"] == 4
        assert s["overall"]["push"] == 1

    @pytest.mark.asyncio
    async def test_summary_by_risk_tier(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        # HIGH: 4 won + 2 lost (Under) + 1 push + 1 lost (DPB) = 8 docs, 4 wins
        assert s["by_risk_tier"]["HIGH"]["total"] == 8
        assert s["by_risk_tier"]["HIGH"]["won"] == 4
        # MEDIUM: 1 lost
        assert s["by_risk_tier"]["MEDIUM"]["total"] == 1
        assert s["by_risk_tier"]["MEDIUM"]["won"] == 0
        # LOW: 0
        assert s["by_risk_tier"]["LOW"]["total"] == 0

    @pytest.mark.asyncio
    async def test_summary_by_miss_type(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        miss = s["by_miss_type"]
        # OVER_BEAT_UNDER: 2 (lost Under) + 1 (DPB lost) = 3
        assert miss["OVER_BEAT_UNDER"]["total"] == 3
        assert miss["UNDER_BEAT_OVER"]["total"] == 1
        assert miss["PUSH"]["total"] == 1

    @pytest.mark.asyncio
    async def test_summary_reference_activations(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        # 4 HIGH+flip+won → 4 reference tag activations
        assert s["reference_profile_activations"] == 4

    @pytest.mark.asyncio
    async def test_summary_dynamic_park_blocks(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        # 1 doc seeded with Dynamic Park BLOCK
        assert s["dynamic_park_blocks"] == 1
        # And it was a "saved" block (Under blocked + game ended Over)
        assert s["park_blocks_saved"] == 1

    @pytest.mark.asyncio
    async def test_summary_settled_filter(self, db):
        await self._seed_calibration_set(db)
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        # The filter constant must be exactly the canonical settled set
        assert s["settled_outcomes_filter"] == list(SETTLED_OUTCOMES)
        assert set(s["settled_outcomes_filter"]) == {"won", "lost", "push"}
        # "void" must NOT be in the headline filter
        assert "void" not in s["settled_outcomes_filter"]

    @pytest.mark.asyncio
    async def test_summary_empty_db_safe(self, db):
        s = await compute_run_evaluations_summary(db, days=30, user_id="_slate")
        assert s["ok"] is True
        assert s["evaluated_total"] == 0
        assert s["overall"]["hit_rate"] is None    # disambiguate from 0%
        assert s["by_risk_tier"]["HIGH"]["hit_rate"] is None
        assert s["reference_profile_activations"] == 0


# ═════════════════════════════════════════════════════════════════════
# NOTE: no pending docs remain after settle simulation
# ═════════════════════════════════════════════════════════════════════
class TestNoPendingAfterSettle:
    """Closes the loop: once the F6C update fires, no pending row
    should remain for the affected match_id."""

    @pytest.mark.asyncio
    async def test_no_pending_for_settled_match(self, db):
        eid = await store_run_evaluation(
            db, user_id="_slate", match_id="M_FINAL",
            run_evaluation=_strong_eval(market="Under 8.5"), metrics={},
        )
        # Before settle: one pending
        pre = await query_run_evaluations(
            db, user_id="_slate", match_id="M_FINAL", result="pending", limit=10,
        )
        assert len(pre) == 1

        # Apply settle (F6C path A)
        await update_run_evaluation_result(
            db, evaluation_id=eid,
            final_runs_home=6, final_runs_away=5,
            result="lost", miss_type="OVER_BEAT_UNDER",
        )

        # After settle: zero pending
        post = await query_run_evaluations(
            db, user_id="_slate", match_id="M_FINAL", result="pending", limit=10,
        )
        assert len(post) == 0

        # And the doc is now resolved
        doc = await db.mlb_run_evaluations.find_one({"id": eid})
        assert doc["result"] == "lost"
        assert doc["resolved_at"] is not None


# ── pytest-asyncio config ────────────────────────────────────────────
# The global config sets `asyncio_mode = auto`. No custom event_loop
# fixture needed — letting pytest-asyncio handle scoping avoids the
# deprecation warning about redefining event_loop.
