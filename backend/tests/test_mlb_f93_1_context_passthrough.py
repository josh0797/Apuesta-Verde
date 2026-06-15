"""MLB-F93.1 — Manual Odds Reprice Context Pass-through + Authenticated Debug.

Ten obligatory tests:
  1. reprice from request pick_context
  2. uses _mlb_script_v2.coverProbability
  3. uses probabilityUnder for Under market
  4. uses probabilityOver for Over market
  5. infers line from market
  6. infers line from selection
  7. PICK_CONTEXT_FROM_REQUEST_PAYLOAD reason code present
  8. saved-only when request context has no probability
  9. debug endpoint requires auth (Depends(get_current_user))
 10. debug endpoint requires identifier (422)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from services.mlb_manual_odds_reprice import (
    RC_CONFIDENCE_WEAK_PROXY,
    RC_LINE_INFERRED_FROM_MARKET,
    RC_LINE_INFERRED_FROM_SELECTION,
    RC_MODEL_PROB_MISSING,
    RC_PICK_CONTEXT_FROM_REQUEST,
    RC_REPRICE_APPLIED,
    infer_total_line,
    reprice_mlb_pick_with_manual_odds,
)


class _FakeUpdateResult:
    matched_count = 1
    modified_count = 1


async def _call_endpoint(payload_dict, **patches):
    from server import MlbManualOddsIn, mlb_pick_manual_odds

    payload = MlbManualOddsIn(**payload_dict)
    pick_id = payload_dict.get("_pick_id_url", "missing-pk")

    fake_db = MagicMock()
    fake_db.pick_runs = patches.get("pick_runs") or MagicMock(
        find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
    )
    fake_db.matches = patches.get("matches") or MagicMock(
        find_one=AsyncMock(return_value=None),
    )
    fake_db.mlb_manual_odds_overrides = patches.get("overrides") or MagicMock(
        update_one=AsyncMock(return_value=_FakeUpdateResult()),
        find_one=AsyncMock(return_value=None),
    )

    with patch("server.db", fake_db):
        return await mlb_pick_manual_odds(
            pick_id=pick_id, payload=payload, user={"id": "u1"},
        )


# ─────────────────────────────────────────────────────────────────────
# 1 + 7. Reprices from request pick_context + reason_code present
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_odds_reprices_from_request_pick_context():
    resp = await _call_endpoint({
        "manual_odds":  1.64,
        "market":       "UNDER 9.5",
        "_pick_id_url": "pk-missing",
        "pick_context": {
            "id":       "front-pk",
            "match_id": "m-X",
            "_mlb_script_v2": {"coverProbability": 61.0},
        },
    })
    assert resp["status"] == "REPRICED"
    assert resp["reprice"]["available"] is True
    assert resp["reprice"]["model_probability"] == pytest.approx(0.61, rel=1e-3)
    assert RC_PICK_CONTEXT_FROM_REQUEST in resp["reprice"]["reason_codes"]
    assert RC_REPRICE_APPLIED          in resp["reprice"]["reason_codes"]
    # Attached_to_pick must be False because the run was never found.
    assert resp["attached_to_pick"] is False


# ─────────────────────────────────────────────────────────────────────
# 2. Uses _mlb_script_v2.coverProbability when no top-level prob.
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_uses_mlb_script_v2_cover_probability():
    pick = {"_mlb_script_v2": {"coverProbability": 0.58}}
    out = reprice_mlb_pick_with_manual_odds(pick, 1.85, market="UNDER 9.5")
    assert out["available"] is True
    assert out["model_probability"] == pytest.approx(0.58, rel=1e-3)


# ─────────────────────────────────────────────────────────────────────
# 3. Side-aware: probabilityUnder for Under market.
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_uses_probability_under_for_under_market():
    pick = {"_mlb_script_v2": {
        "probabilityUnder": 0.62, "probabilityOver": 0.38,
    }}
    out = reprice_mlb_pick_with_manual_odds(pick, 1.65, market="UNDER 9.5")
    assert out["model_probability"] == pytest.approx(0.62, rel=1e-3)


# ─────────────────────────────────────────────────────────────────────
# 4. Side-aware: probabilityOver for Over market.
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_uses_probability_over_for_over_market():
    pick = {"expected_runs_distribution": {
        "probability_under": 0.40, "probability_over": 0.60,
    }}
    out = reprice_mlb_pick_with_manual_odds(pick, 1.80, market="OVER 8.5")
    assert out["model_probability"] == pytest.approx(0.60, rel=1e-3)


# ─────────────────────────────────────────────────────────────────────
# 5. Infer line from market.
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_infers_line_from_market():
    line, rcs = infer_total_line(market="UNDER 9.5", selection=None)
    assert line == 9.5
    assert RC_LINE_INFERRED_FROM_MARKET in rcs

    # And the reprice function attaches the reason code.
    out = reprice_mlb_pick_with_manual_odds(
        {"cover_probability": 0.6}, 1.80, market="UNDER 9.5",
    )
    assert out["line"] == 9.5
    assert RC_LINE_INFERRED_FROM_MARKET in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 6. Infer line from selection (Spanish copy).
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_infers_line_from_selection():
    line, rcs = infer_total_line(market=None,
                                  selection="Menos de 8.5 carreras")
    assert line == 8.5
    assert RC_LINE_INFERRED_FROM_SELECTION in rcs


# ─────────────────────────────────────────────────────────────────────
# 8. Saved-only when request pick_context has NO probability.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_odds_saved_only_when_request_context_has_no_probability():
    resp = await _call_endpoint({
        "manual_odds":  1.64,
        "market":       "UNDER 9.5",
        "_pick_id_url": "pk-x",
        "pick_context": {
            "id":       "p",
            "match_id": "m-X",
            # No probability field anywhere. confidence_score only.
            "confidence_score": 82,
        },
    })
    assert resp["status"] == "OVERRIDE_SAVED_ONLY"
    assert resp["reprice"]["available"] is False
    codes = resp["reprice"]["reason_codes"]
    assert RC_MODEL_PROB_MISSING       in codes
    assert RC_PICK_CONTEXT_FROM_REQUEST in codes
    # Weak proxy must be flagged because confidence_score is present.
    assert RC_CONFIDENCE_WEAK_PROXY in codes


# ─────────────────────────────────────────────────────────────────────
# 9. Debug endpoint requires auth — calling without `user` raises.
# ─────────────────────────────────────────────────────────────────────
def test_manual_odds_debug_requires_auth():
    """The endpoint signature uses Depends(get_current_user); when wired
    into FastAPI a missing token returns 401. We assert the dependency
    is present in the route definition."""
    from server import mlb_manual_odds_debug
    import inspect

    sig = inspect.signature(mlb_manual_odds_debug)
    user_param = sig.parameters.get("user")
    assert user_param is not None
    # The default uses fastapi.Depends — surfacing get_current_user.
    default = user_param.default
    assert default is not None
    # Depends wraps a callable named `get_current_user`.
    dep_fn = getattr(default, "dependency", None)
    assert dep_fn is not None
    assert dep_fn.__name__ == "get_current_user"


# ─────────────────────────────────────────────────────────────────────
# 10. Debug endpoint requires identifier (422 without any).
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_odds_debug_requires_identifier():
    from server import mlb_manual_odds_debug

    with pytest.raises(HTTPException) as exc_info:
        await mlb_manual_odds_debug(
            user={"id": "u1"}, match_id=None, pick_id=None, game_pk=None,
        )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["reason_code"] == "DEBUG_IDENTIFIER_REQUIRED"
    assert "match_id" in detail["message_user"]


# ─────────────────────────────────────────────────────────────────────
# Bonus: confidence_score alone does NOT promote to VALUE
# ─────────────────────────────────────────────────────────────────────
def test_confidence_alone_does_not_become_value():
    """Even with a very high confidence_score and a juicy odd, decision
    must stay MANUAL_ODDS_ONLY when no real probability is available."""
    pick = {"confidence_score": 95}
    out = reprice_mlb_pick_with_manual_odds(pick, 2.50, market="UNDER 9.5")
    assert out["decision"] == "MANUAL_ODDS_ONLY"
    assert RC_MODEL_PROB_MISSING    in out["reason_codes"]
    assert RC_CONFIDENCE_WEAK_PROXY in out["reason_codes"]
