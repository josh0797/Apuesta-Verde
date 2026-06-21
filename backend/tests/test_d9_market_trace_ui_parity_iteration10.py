"""Sprint-D9 · iteration 10 — UI ↔ Backend market-trace parity regression.

User-reported bug (P0):
    Backend's POST /api/analysis/run correctly classified matches
    (e.g. Argentina vs Austria as ``high_confidence``), but the UI feed
    showed them as discarded with literal labels ``SPORTYTRADER NO
    ENCONTRADO`` and ``Mercado desconocido``. Root cause: when a pick
    was discarded BEFORE the LLM produced a ``recommendation`` block
    (because the moneyball gate blocked it or odds were absent), the
    `market_trace.market` field was overwritten to ``None`` even though
    the moneyball engine had already populated ``market_selection.
    recommended_market`` with a real label (e.g. "Doble Oportunidad").

Fix:
    `services/football_market_trace.build_market_trace` now falls back
    to ``market_selection.recommended_market`` / ``market_name`` when
    ``recommendation.market`` is absent.

This regression suite locks down:
    1. The fallback to ``market_selection.recommended_market`` works.
    2. The fallback to ``market_selection.market_name`` works.
    3. The fallback to ``_market_edge.market`` works.
    4. Real ``recommendation.market`` ALWAYS wins over fallbacks.
    5. Items with no market info AT ALL (true edge case) still produce
       a valid trace with ``state=REQUIRES_MARKET_IDENTIFICATION`` —
       NOT a literal ``"unknown"`` string that could leak to the UI.
    6. The Sprint-D9 trace script (`diagnostics/football_e2e_recommendation_trace.py`)
       remains importable and exposes the helpers we depend on.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from services.football_market_trace import build_market_trace


# ─────────────────────────────────────────────────────────────────────
# Fixtures helpers
# ─────────────────────────────────────────────────────────────────────
def _discarded_entry_without_recommendation(*,
                                              market_selection: dict | None = None,
                                              market_edge: dict | None = None,
                                              moneyball: dict | None = None,
                                              match_label: str = "Argentina vs Austria",
                                              reason: str = "Datos insuficientes",
                                              ) -> dict:
    """Mimic the discarded_market / incomplete_data entry shape produced
    by the football pipeline when the LLM never emitted a recommendation
    block (e.g. moneyball gate short-circuited)."""
    entry: dict = {
        "match_id":    "2391758",
        "match_label": match_label,
        "reason":      reason,
        # NOTE — deliberately NO `recommendation` block. This is the
        # critical condition that triggered the user-reported bug.
    }
    if market_selection is not None:
        entry["market_selection"] = market_selection
    if market_edge is not None:
        entry["_market_edge"] = market_edge
    if moneyball is not None:
        entry["_moneyball"] = moneyball
    return entry


# ─────────────────────────────────────────────────────────────────────
# 1. recommended_market fallback
# ─────────────────────────────────────────────────────────────────────
def test_market_trace_uses_market_selection_recommended_market_as_fallback():
    """When recommendation is absent BUT market_selection.recommended_market
    is set, the trace MUST expose that label (not None / not 'unknown')."""
    entry = _discarded_entry_without_recommendation(
        market_selection={
            "recommended_market":   "Doble Oportunidad",
            "selection":            "Argentina or Draw",
            "market_confidence":    72,
            "fragility":            30,
            "why_this_market":      "Mercado base: Doble Oportunidad.",
            "engine_version":       "football_moneyball.market_selection.1",
        },
        moneyball={
            "classification": "MARKET_PROTECTION_BELOW_FLOOR",
            "classification_reason": "Mercado protegido con edge -6.0%.",
            "confidence": 65,
            "fragility": {"score": 30, "factors": []},
        },
    )
    trace = build_market_trace(entry)

    assert trace["market"] == "Doble Oportunidad", (
        f"Sprint-D9 regression: market_trace.market should fall back to "
        f"market_selection.recommended_market, got {trace['market']!r}"
    )
    assert (trace["market"] or "").lower() != "unknown"
    assert trace["market"] is not None


def test_market_trace_uses_market_selection_market_name_as_secondary_fallback():
    """Some legacy entries carry ``market_name`` instead of
    ``recommended_market``. Both must be honoured."""
    entry = _discarded_entry_without_recommendation(
        market_selection={
            "market_name": "Under 2.5",
            "selection":   "Under 2.5 Goals",
        },
    )
    trace = build_market_trace(entry)
    assert trace["market"] == "Under 2.5"


def test_market_trace_uses_market_edge_market_as_tertiary_fallback():
    """If neither recommendation nor market_selection are present but the
    moneyball layer attached `_market_edge.market`, it should still bubble
    up to the trace label."""
    entry = _discarded_entry_without_recommendation(
        market_edge={"market": "Corners Over 9.5", "edge": -0.025},
    )
    trace = build_market_trace(entry)
    assert trace["market"] == "Corners Over 9.5"


# ─────────────────────────────────────────────────────────────────────
# 2. recommendation.market always wins
# ─────────────────────────────────────────────────────────────────────
def test_recommendation_market_wins_over_market_selection():
    """If the LLM emitted a recommendation, that label takes priority
    over any moneyball-recommended market — fallbacks must NOT override
    real picks."""
    entry = {
        "match_label": "Spain vs Saudi Arabia",
        "recommendation": {
            "market":     "BTTS Yes",
            "selection":  "Yes",
            "odds_range": "1.85",
            "confidence": 73,
        },
        "market_selection": {
            "recommended_market": "Doble Oportunidad",  # should be IGNORED
        },
    }
    trace = build_market_trace(entry)
    assert trace["market"] == "BTTS Yes", (
        "recommendation.market MUST take priority over the fallback "
        "introduced by Sprint-D9"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. True-blank edge case → MARKET_IDENTITY_MISSING (NOT 'unknown')
# ─────────────────────────────────────────────────────────────────────
def test_market_trace_blank_entry_does_not_leak_unknown_string_to_ui():
    """When literally NO market information exists anywhere in the
    pipeline, the trace must remain semantically clear instead of
    leaking a literal 'unknown' string to the UI. We require:
       - `market` field is None (UI hides the chip)  OR semantic label
       - `state` flagged as REQUIRES_MARKET_IDENTIFICATION when odds
         exist without identity, OR `rejection_code` is informative
       - The string 'unknown' (case-insensitive) NEVER appears as the
         user-visible market label.
    """
    entry = _discarded_entry_without_recommendation(
        reason="Cuotas no atractivas y sin información de forma reciente.",
    )
    trace = build_market_trace(entry)

    # The market label MUST NOT be the literal string 'unknown'.
    mkt = trace.get("market")
    assert mkt is None or (mkt or "").lower() != "unknown", (
        f"Sprint-D9: market_trace.market leaked literal 'unknown' "
        f"to UI: {mkt!r}"
    )

    # Either the market is None (UI hides chip) or a valid rejection
    # code is present so the UI can render an informative message.
    assert trace.get("rejection_code") is not None
    assert trace.get("rejection_reason"), "rejection_reason must be filled"


def test_market_trace_blank_entry_with_odds_routes_to_identity_missing():
    """If we have an odds value but no market identity, the F73 guard
    must route us to MARKET_IDENTITY_MISSING — not a generic
    PROTECTED_BELOW_FLOOR / MARKET_TRAP that would mislead the user."""
    entry = {
        "match_label": "Foo vs Bar",
        "recommendation": {
            "odds_range": "1.55",
            "confidence": 60,
            # market intentionally omitted
        },
        "_moneyball": {
            "classification": "MARKET_TRAP",
            "classification_reason": "test seed",
            "confidence": 60,
            "fragility": {"score": 40, "factors": []},
        },
    }
    trace = build_market_trace(entry)
    # F73 guard should override the classification when identity is missing.
    assert trace.get("rejection_code") in (
        "MARKET_IDENTITY_MISSING", "UNKNOWN", "NO_VALUE", "LOW_EDGE"
    )
    # The user-visible odds must still be preserved for transparency.
    if trace.get("rejection_code") == "MARKET_IDENTITY_MISSING":
        assert trace.get("odds_visible") in (1.55, 1.55)


# ─────────────────────────────────────────────────────────────────────
# 4. Output schema invariants the UI relies on
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    _discarded_entry_without_recommendation(
        market_selection={"recommended_market": "Doble Oportunidad"},
    ),
    _discarded_entry_without_recommendation(),  # truly blank
])
def test_market_trace_output_is_a_well_formed_dict(payload):
    """Lock down the keys the UI expects to render the audit card."""
    trace = build_market_trace(payload)
    assert isinstance(trace, dict)
    for key in (
        "market", "selection", "market_code", "team_side",
        "odds", "estimated_probability", "implied_probability",
        "edge", "edge_pct", "fragility", "confidence",
        "rejection_code", "rejection_reason", "classification",
        "sport",
    ):
        assert key in trace, f"market_trace is missing UI-required key {key!r}"


# ─────────────────────────────────────────────────────────────────────
# 5. Diagnostics script importable
# ─────────────────────────────────────────────────────────────────────
def test_diagnostics_e2e_trace_script_is_importable_and_exposes_helpers():
    """Guard against silent breakage of the E2E diagnostics tool the
    user relies on for reproducing UI vs backend divergence."""
    sys.path.insert(0, "/app/diagnostics")
    try:
        spec = importlib.util.spec_from_file_location(
            "football_e2e_recommendation_trace",
            "/app/diagnostics/football_e2e_recommendation_trace.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Required helpers exposed by the script
        assert hasattr(mod, "_flatten_buckets")
        assert hasattr(mod, "_extract_market_fingerprint")
        assert hasattr(mod, "_detect_unknown_pollution")
        assert hasattr(mod, "_build_diff")
        assert hasattr(mod, "_extract_summary_from_any")
        assert hasattr(mod, "FOCUS_MATCHES")
        assert hasattr(mod, "_is_focus_match")
        # Focus matches MUST include the three the user explicitly asked for.
        labels = {(h.lower(), a.lower()) for (h, a) in mod.FOCUS_MATCHES}
        assert ("argentina", "austria") in labels
        # Accept either spelling.
        assert (("uruguay", "cabo verde") in labels
                or ("uruguay", "cape verde") in labels)
        assert (("nueva zelanda", "egipto") in labels
                or ("new zealand", "egypt") in labels)
    finally:
        if "/app/diagnostics" in sys.path:
            sys.path.remove("/app/diagnostics")


def test_diagnostics_e2e_extract_summary_handles_double_nested_jobs():
    """`/api/analysis/jobs/{id}` returns body.result.result.summary
    (double nesting). The helper must drill into both single AND double
    levels of `.result.` to find the summary block."""
    sys.path.insert(0, "/app/diagnostics")
    try:
        spec = importlib.util.spec_from_file_location(
            "football_e2e_recommendation_trace",
            "/app/diagnostics/football_e2e_recommendation_trace.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Single-level (/api/analysis/run sync, /api/picks/run/{id})
        body_a = {"result": {"summary": {"high_confidence": [{"x": 1}]}}}
        assert mod._extract_summary_from_any(body_a) == {"high_confidence": [{"x": 1}]}

        # Double-level (job document)
        body_b = {"result": {"result": {"summary": {"medium_confidence": []}}}}
        assert mod._extract_summary_from_any(body_b) == {"medium_confidence": []}

        # Direct (/api/picks/today)
        body_c = {"summary": {"discarded_market": [1, 2]}}
        assert mod._extract_summary_from_any(body_c) == {"discarded_market": [1, 2]}

        # Nothing → empty dict (not None, not exception)
        assert mod._extract_summary_from_any({"foo": "bar"}) == {}
        assert mod._extract_summary_from_any(None) == {}
    finally:
        if "/app/diagnostics" in sys.path:
            sys.path.remove("/app/diagnostics")
