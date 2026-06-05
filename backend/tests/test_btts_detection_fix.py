"""Tests for the BTTS detection bug fix (Mexico vs Serbia case).

The engine emits a live recommendation whose top-level ``market`` is a
status label ("momentum local") while the offensive market only appears
in narrative fields (``interpreter.reason`` / ``interpreter.why``). This
suite verifies that:

  1. ``normalize_live_market_label`` detects BTTS / Over / Under from
     any free-form text.
  2. ``persist_live_recommendation_event`` registers the event even when
     the market is hidden in the reason, and stores a canonical
     ``recommendation.normalized_market``.
  3. ``settle_live_event_from_score`` consumes the normalized label
     deterministically (BTTS YES @ 1-1 → hit).
  4. ``settle_open_live_events_for_match`` settles all open events for
     a given match against the current score.
  5. A new live recommendation after a HIT does NOT mark the prior
     event as superseded (it keeps status=hit and only adds
     superseded_by_event_id).
"""

from __future__ import annotations

import pytest

from services.live_recommendation_history import (
    normalize_live_market_label,
    settle_live_event_from_score,
    persist_live_recommendation_event,
    settle_open_live_events_for_match,
    NORM_BTTS_YES,
    NORM_BTTS_NO,
    NORM_OVER_15,
    NORM_OVER_25,
    NORM_OVER_35,
    NORM_UNDER_25,
    NORM_UNDER_35,
    COLLECTION,
)
from tests.test_football_moneyball import _FakeDB  # type: ignore
from tests.test_live_recommendation_history import fake_db  # noqa: F401  (fixture)


# ─────────────────────────────────────────────────────────────────────
# normalize_live_market_label
# ─────────────────────────────────────────────────────────────────────
def test_normalize_btts_ambos_marcan_label():
    assert normalize_live_market_label("BTTS (Ambos marcan)") == NORM_BTTS_YES


def test_normalize_ambos_equipos_marcan():
    assert normalize_live_market_label(None, None, None, "Ambos equipos marcan") == NORM_BTTS_YES


def test_normalize_bare_btts_token_is_btts_yes():
    # Engine sometimes emits just "BTTS" without a Yes/Sí qualifier.
    assert normalize_live_market_label("BTTS") == NORM_BTTS_YES


def test_normalize_btts_no_takes_priority_over_yes():
    assert normalize_live_market_label("BTTS NO") == NORM_BTTS_NO
    assert normalize_live_market_label("Ambos equipos no marcan") == NORM_BTTS_NO


def test_normalize_both_teams_to_score_english():
    assert normalize_live_market_label("Both teams to score") == NORM_BTTS_YES


def test_normalize_over_2_5_from_narrative_only():
    out = normalize_live_market_label(
        market="momentum local",
        selection=None,
        title="momentum local",
        # narrative carries the actual offensive market.
        extra="El ritmo ofensivo apoya Over 2.5 con tiempo suficiente.",
    ) if False else normalize_live_market_label(
        "momentum local", None, "momentum local",
        "El ritmo ofensivo apoya Over 2.5 con tiempo suficiente.",
    )
    assert out == NORM_OVER_25


def test_normalize_over_3_5_when_label_says_more_than():
    assert normalize_live_market_label("Más de 3.5 goles") == NORM_OVER_35


def test_normalize_under_3_5_spanish():
    assert normalize_live_market_label("Menos de 3.5") == NORM_UNDER_35


def test_normalize_picks_largest_line_first_when_ambiguous():
    # If both 2.5 and 0.5 appear, prefer the more specific 2.5 mention.
    assert normalize_live_market_label("Over 2.5 (no Over 0.5)") == NORM_OVER_25


def test_normalize_none_when_no_supported_market():
    assert normalize_live_market_label("Moneyline Home") is None
    assert normalize_live_market_label(None) is None
    assert normalize_live_market_label("") is None


# ─────────────────────────────────────────────────────────────────────
# settle_live_event_from_score uses normalized_market
# ─────────────────────────────────────────────────────────────────────
def test_settle_uses_normalized_market_when_present():
    ev = {
        "recommendation": {
            "market":            "momentum local",        # status label
            "normalized_market": NORM_BTTS_YES,
        }
    }
    s = settle_live_event_from_score(ev, {"home": 1, "away": 1}, minute=38)
    assert s["result"] == "hit"
    assert "BTTS YES cumplido" in s["settlement_reason"]


def test_settle_falls_back_to_text_scan_when_no_normalized_market():
    # Legacy doc without normalized_market — must still resolve through
    # the free-form scanner.
    ev = {
        "recommendation": {
            "market":    "BTTS (Ambos marcan)",
            "selection": "BTTS",
        }
    }
    s = settle_live_event_from_score(ev, {"home": 1, "away": 1})
    assert s["result"] == "hit"


def test_settle_pending_when_btts_yes_only_one_team_scored():
    ev = {"recommendation": {"normalized_market": NORM_BTTS_YES}}
    s = settle_live_event_from_score(ev, {"home": 1, "away": 0}, minute=34)
    assert s["result"] == "pending"


def test_settle_miss_when_btts_yes_full_time_0_1():
    ev = {"recommendation": {"normalized_market": NORM_BTTS_YES}}
    s = settle_live_event_from_score(ev, {"home": 0, "away": 1}, match_ended=True)
    assert s["result"] == "miss"


# ─────────────────────────────────────────────────────────────────────
# persist_live_recommendation_event with offensive market in narrative
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_persist_event_when_btts_only_in_narrative(fake_db):
    match = {
        "match_id":   "mex-srb-2026",
        "home_team":  {"name": "Mexico"},
        "away_team":  {"name": "Serbia"},
        "league":     {"name": "Friendlies"},
        "live_stats": {"minute": 34, "score": {"home": 0, "away": 1}},
    }
    reeval = {
        # Engine left the top-level market as the status label.
        "market":              None,
        "recommended_action":  "WATCHLIST",
        "live_state":          "LIVE_VALUE_WINDOW",
        "confidence":          66,
        "live_snapshot":       {"minute": 34, "score": {"home": 0, "away": 1}},
    }
    interpreter = {
        # The offensive market shows up ONLY in narrative fields — this is
        # exactly the bug we are fixing.
        "title":     "Momentum local",
        "narrative": "El crecimiento del local apoya BTTS (Ambos marcan) si la cuota lo justifica.",
        "reason":    "El Mexico está creciendo, pero el mercado todavía no ha movido la línea suficiente.",
    }
    doc = await persist_live_recommendation_event(
        fake_db,
        user_id="u1",
        match=match,
        reeval_result=reeval,
        interpreter=interpreter,
        source="engine",
    )
    assert doc is not None, "engine event MUST be persisted when BTTS hides in narrative"
    rec = doc["recommendation"]
    assert rec["normalized_market"] == NORM_BTTS_YES
    # The canonical display label MUST replace "momentum local" so the
    # timeline shows a meaningful market badge.
    assert rec["market"] == "BTTS YES"
    assert "Ambos equipos marcan" in (rec["selection"] or "")


# ─────────────────────────────────────────────────────────────────────
# Auto-settle open events when the score changes
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_settle_open_live_events_for_match_btts_hit(fake_db):
    # Step 1: persist an open BTTS YES event at minute 34, score 0-1.
    match_at_34 = {
        "match_id":   "mex-srb-2026",
        "home_team":  {"name": "Mexico"},
        "away_team":  {"name": "Serbia"},
        "live_stats": {"minute": 34, "score": {"home": 0, "away": 1}},
    }
    interp = {
        "narrative": "El crecimiento del local apoya BTTS (Ambos marcan) si la cuota lo justifica.",
    }
    await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match_at_34,
        reeval_result={
            "live_state": "LIVE_VALUE_WINDOW",
            "recommended_action": "WATCHLIST",
            "confidence": 66,
            "live_snapshot": {"minute": 34, "score": {"home": 0, "away": 1}},
        },
        interpreter=interp,
    )

    # Step 2: minute 38, score moves to 1-1 — BTTS YES is now satisfied.
    match_at_38 = {
        "match_id":   "mex-srb-2026",
        "live_stats": {"minute": 38, "score": {"home": 1, "away": 1}},
    }
    res = await settle_open_live_events_for_match(
        fake_db, sport="football", match=match_at_38, user_id="u1",
    )
    assert res["updated"] >= 1

    # Verify the event is now hit.
    doc = next(
        (d for d in fake_db[COLLECTION].docs if d.get("match_id") == "mex-srb-2026"),
        None,
    )
    assert doc is not None
    assert doc["status"] == "hit"
    assert doc["outcome"]["result"] == "hit"
    assert doc["outcome"]["settled_score"] == "1-1"


# ─────────────────────────────────────────────────────────────────────
# Hit must remain hit when a NEW recommendation is later persisted
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hit_not_marked_superseded_on_new_recommendation(fake_db):
    # Step 1: BTTS YES at minute 34 (open).
    match_at_34 = {
        "match_id":   "mex-srb-2026",
        "live_stats": {"minute": 34, "score": {"home": 0, "away": 1}},
    }
    await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match_at_34,
        reeval_result={
            "live_state": "LIVE_VALUE_WINDOW",
            "recommended_action": "WATCHLIST",
            "live_snapshot": {"minute": 34, "score": {"home": 0, "away": 1}},
        },
        interpreter={
            "narrative": "El crecimiento del local apoya BTTS (Ambos marcan).",
        },
    )
    # Step 2: settle (BTTS YES @ 1-1).
    match_at_38 = {
        "match_id":   "mex-srb-2026",
        "live_stats": {"minute": 38, "score": {"home": 1, "away": 1}},
    }
    await settle_open_live_events_for_match(
        fake_db, sport="football", match=match_at_38, user_id="u1",
    )
    # Step 3: engine emits a NEW recommendation at minute 54 (Over 3.5).
    match_at_54 = {
        "match_id":   "mex-srb-2026",
        "live_stats": {"minute": 54, "score": {"home": 1, "away": 1}},
    }
    new_doc = await persist_live_recommendation_event(
        fake_db, user_id="u1", match=match_at_54,
        reeval_result={
            "live_state": "LIVE_VALUE_WINDOW",
            "recommended_action": "LIVE_ENTRY",
            "market": "Over 3.5",
            "live_snapshot": {"minute": 54, "score": {"home": 1, "away": 1}},
        },
        interpreter={"suggested_market": "Over 3.5"},
    )
    assert new_doc is not None
    # The prior BTTS event MUST remain hit (never superseded).
    btts_doc = next(
        (d for d in fake_db[COLLECTION].docs
         if (d.get("recommendation") or {}).get("normalized_market") == NORM_BTTS_YES),
        None,
    )
    assert btts_doc is not None
    assert btts_doc["status"] == "hit", "HIT must persist after a new recommendation"
    # It can carry superseded_by_event_id as an audit link.
    assert btts_doc.get("superseded_by_event_id") in (None, new_doc["event_id"])


# ─────────────────────────────────────────────────────────────────────
# Fail-soft when DB raises
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_settle_open_events_failsoft_on_db_none():
    res = await settle_open_live_events_for_match(
        None, sport="football", match={"match_id": "x"},
    )
    assert res == {"updated": 0, "errors": []}


@pytest.mark.asyncio
async def test_settle_open_events_failsoft_on_missing_score(fake_db):
    # Match without score → should not crash and update nothing.
    res = await settle_open_live_events_for_match(
        fake_db, sport="football",
        match={"match_id": "x", "live_stats": {"minute": 5}},
    )
    assert res["updated"] == 0
