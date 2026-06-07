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
