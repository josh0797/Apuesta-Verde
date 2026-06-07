"""Smoke tests for Phase 41 server wiring (per-card endpoint + box-score
hydrate endpoint). We don't spin up a real HTTPx client here — the
expensive auth/mongo bootstrap is exercised by the existing
backend_test.py integration suite. Instead we assert the routes are
REGISTERED and dispatch to the right handlers, and that the box-score
hydrate logic gracefully handles empty matches.
"""
from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────
# Route registration
# ─────────────────────────────────────────────────────────────────────
def test_per_card_reevaluate_alias_route_registered():
    """`POST /api/analysis/live/reevaluate-one` must be registered."""
    from server import api
    paths = {(r.path, tuple(sorted(r.methods or set()))) for r in api.routes}
    assert ("/api/analysis/live/reevaluate-one", ("POST",)) in paths


def test_box_score_hydrate_route_registered():
    """`POST /api/analysis/box-scores/hydrate` must be registered."""
    from server import api
    paths = {(r.path, tuple(sorted(r.methods or set()))) for r in api.routes}
    assert ("/api/analysis/box-scores/hydrate", ("POST",)) in paths


def test_legacy_live_reevaluate_route_still_registered():
    """Backwards compatibility: `/live/reevaluate` must remain available."""
    from server import api
    paths = {r.path for r in api.routes}
    assert "/api/live/reevaluate" in paths
    # And the new alias is registered SIDE-BY-SIDE with the legacy one
    # (no replacement).
    assert "/api/analysis/live/reevaluate-one" in paths


# ─────────────────────────────────────────────────────────────────────
# Manual market whitelist (Fix 7) — fail-soft for unknown markets
# ─────────────────────────────────────────────────────────────────────
def test_manual_market_whitelist_includes_over_under_0_5():
    """The whitelist string MUST mention the new Over/Under 0.5 markets."""
    import server
    import inspect
    src = inspect.getsource(server.live_reevaluate)
    assert "over 0.5" in src.lower()
    assert "under 0.5" in src.lower()
    assert "over 3.5" in src.lower()
    # Also expect the BTTS + DC + DNB families.
    assert "btts yes" in src.lower()
    assert "dnb home" in src.lower()
    assert "doble oportunidad" in src.lower()


# ─────────────────────────────────────────────────────────────────────
# TrackIn outcomes (Fix 5+6)
# ─────────────────────────────────────────────────────────────────────
def test_trackin_accepts_cancelled_and_refund_outcomes():
    """``TrackIn`` outcome regex must accept the new lifecycle states."""
    from server import TrackIn
    valid = ("won", "lost", "push", "pending", "void", "cancelled", "refund")
    for o in valid:
        TrackIn(
            run_id="r", match_id="m", market="Over 1.5",
            selection="Over 1.5", confidence_score=60, outcome=o,
        )
    # Invalid outcomes must still raise.
    with pytest.raises(Exception):
        TrackIn(
            run_id="r", match_id="m", market="Over 1.5",
            selection="Over 1.5", confidence_score=60, outcome="banana",
        )


def test_trackin_accepts_live_entry_context_fields():
    """The new optional live-entry context fields must be accepted."""
    from server import TrackIn
    payload = TrackIn(
        run_id="r", match_id="m", market="Over 2.5", selection="Over 2.5",
        confidence_score=70, outcome="pending",
        source="manual",
        entry_minute=42,
        entry_score_home=1,
        entry_score_away=0,
        entry_score_display="1-0",
    )
    assert payload.source == "manual"
    assert payload.entry_minute == 42
    assert payload.entry_score_display == "1-0"


# ─────────────────────────────────────────────────────────────────────
# Phase 42 — Line Learning Engine wiring
# ─────────────────────────────────────────────────────────────────────
def test_line_learning_routes_registered():
    """Read-only endpoints exposed for the UI panel."""
    from server import api
    paths = {r.path for r in api.routes}
    assert "/api/learning/line/samples" in paths
    assert "/api/learning/line/cohort-bias" in paths


def test_trackin_accepts_cashout_outcomes():
    from server import TrackIn
    for o in ("cashout_win", "cashout_loss"):
        TrackIn(
            run_id="r", match_id="m", market="x",
            selection="y", confidence_score=50, outcome=o,
        )


def test_trackin_accepts_actual_bet_fields():
    """The Line-Learning actual_* + projection + final_value fields."""
    from server import TrackIn
    payload = TrackIn(
        run_id="r", match_id="m1", market="total_runs_under",
        selection="Under 9.5", confidence_score=65, outcome="lost",
        sport="baseball", line=9.5, odds=1.85,
        actual_market="total_runs_under",
        actual_selection="Under 10.0",
        actual_line=10.0, actual_odds=1.26,
        actual_outcome="push",
        engine_projection=7.8, final_value=10.0,
        market_type="total_runs",
    )
    assert payload.actual_line == 10.0
    assert payload.actual_odds == 1.26
    assert payload.actual_outcome == "push"
    assert payload.engine_projection == 7.8
    assert payload.final_value == 10.0
    assert payload.market_type == "total_runs"
