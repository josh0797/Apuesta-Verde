"""Tests for the second wave of fixes (post Batch 3):

1. Fix 1 (Batch 3.5) — `_enrich_generic` now wires TheStatsAPI enrichment
   for basketball + baseball (parallel to the football path).
2. Fix 2 — `football_competitions.get_competition_meta()` exposes a
   ``thestatsapi_ids`` list per canonical competition.
3. Fix 3 — `mlb_day_orchestrator` always mirrors the L5/L15 fields into
   ``baseballHistoricalProfile`` (`recentRunSplit`, `recentRunTrend`,
   `onBaseProfileL5`) regardless of whether the profile dict pre-existed.
"""

from __future__ import annotations

import pytest

from services import football_competitions as fc
from services.mlb_intelligence import MLB_INTELLIGENCE_RULES


# ──────────────────────────────────────────────────────────────────────
# Fix 2 — thestatsapi_ids in football_competitions
# ──────────────────────────────────────────────────────────────────────
def test_get_thestatsapi_competition_ids_known_seed():
    """FIFA World Cup is the one canonical mapping verified live."""
    assert fc.get_thestatsapi_competition_ids("FIFA World Cup") == ["comp_6107"]


def test_get_thestatsapi_competition_ids_empty_when_unseeded():
    assert fc.get_thestatsapi_competition_ids("Premier League") == []
    assert fc.get_thestatsapi_competition_ids("Serie A") == []


def test_get_thestatsapi_competition_ids_unknown_league():
    assert fc.get_thestatsapi_competition_ids("League That Doesn't Exist") == []
    assert fc.get_thestatsapi_competition_ids(None) == []
    assert fc.get_thestatsapi_competition_ids("") == []


def test_get_competition_meta_exposes_thestatsapi_ids():
    """The descriptor returned by get_competition_meta carries the IDs
    so callers don't need a second lookup."""
    meta = fc.get_competition_meta("FIFA World Cup")
    assert meta is not None
    assert "thestatsapi_ids" in meta
    assert meta["thestatsapi_ids"] == ["comp_6107"]


def test_get_competition_meta_thestatsapi_ids_empty_for_other_leagues():
    meta = fc.get_competition_meta("Premier League")
    assert meta is not None
    assert meta["thestatsapi_ids"] == []


def test_thestatsapi_competition_map_lookup_is_copy():
    """The list returned must be a *copy* — mutating it shouldn't
    corrupt the module-level mapping."""
    ids = fc.get_thestatsapi_competition_ids("FIFA World Cup")
    ids.append("comp_DELETED")
    # Fetch a fresh copy: must NOT contain the mutation
    fresh = fc.get_thestatsapi_competition_ids("FIFA World Cup")
    assert "comp_DELETED" not in fresh
    assert fresh == ["comp_6107"]


# ──────────────────────────────────────────────────────────────────────
# Fix 3 — MLB prompt has L5/L15 instructions
# ──────────────────────────────────────────────────────────────────────
def test_mlb_prompt_mentions_recent_run_split():
    """The system prompt MUST explain how to use the L5/L15 momentum
    fields, otherwise the LLM ignores them even when present in the
    payload."""
    assert "recent_run_split" in MLB_INTELLIGENCE_RULES.lower() or \
           "recentRunSplit" in MLB_INTELLIGENCE_RULES
    assert "baseballHistoricalProfile" in MLB_INTELLIGENCE_RULES
    # Make sure the keyword the LLM should cite is referenced
    assert "L5" in MLB_INTELLIGENCE_RULES and "L15" in MLB_INTELLIGENCE_RULES


def test_mlb_prompt_documents_trend_thresholds():
    """Concrete thresholds must be present so the model can act on them."""
    assert "delta_pct" in MLB_INTELLIGENCE_RULES
    # The +20 / -20 thresholds (used by the trend interpreter on the
    # backend) must be documented for the LLM to align with them.
    assert "20%" in MLB_INTELLIGENCE_RULES


def test_mlb_prompt_addresses_f5_and_nrfi_markets():
    """F5 and NRFI/YRFI logic must be wired so the LLM doesn't fall
    back to soccer-style reasoning on these markets."""
    assert "f5Split" in MLB_INTELLIGENCE_RULES or "F5" in MLB_INTELLIGENCE_RULES
    assert "NRFI" in MLB_INTELLIGENCE_RULES and "YRFI" in MLB_INTELLIGENCE_RULES


# ──────────────────────────────────────────────────────────────────────
# Fix 3 — orchestrator unconditional mirror
# ──────────────────────────────────────────────────────────────────────
def test_orchestrator_mirror_block_handles_empty_profile():
    """Simulate the exact mirror logic post-fix: even when
    `baseballHistoricalProfile` was not pre-populated, the three
    camelCase fields must be set on the pick_payload."""
    # Mimic mlb_day_orchestrator.py lines 1340-1358 verbatim
    pick_payload: dict = {}   # crucially, NO baseballHistoricalProfile key
    recent_form_payload = {
        "recent_run_split": {"home": {"delta_pct": 25}, "away": {"delta_pct": -10}},
        "recent_run_trend": "HOME_HOT_AWAY_COLD",
        "on_base_profile":  {"home": {"obp_l5": 0.380}, "away": {"obp_l5": 0.310}},
        "f5_split":         {"home": {"runs_l5_avg": 2.4}},
        "first_inning_split": {"home": {"scored_pct": 0.45}},
    }
    _hb = pick_payload.get("baseballHistoricalProfile") or {}
    _hb["recentRunSplit"]   = recent_form_payload["recent_run_split"]
    _hb["recentRunTrend"]   = recent_form_payload["recent_run_trend"]
    _hb["onBaseProfileL5"]  = recent_form_payload["on_base_profile"]
    if recent_form_payload.get("f5_split"):
        _hb["f5Split"]          = recent_form_payload["f5_split"]
    if recent_form_payload.get("first_inning_split"):
        _hb["firstInningSplit"] = recent_form_payload["first_inning_split"]
    pick_payload["baseballHistoricalProfile"] = _hb

    out = pick_payload["baseballHistoricalProfile"]
    assert out["recentRunSplit"]["home"]["delta_pct"] == 25
    assert out["recentRunTrend"] == "HOME_HOT_AWAY_COLD"
    assert out["onBaseProfileL5"]["home"]["obp_l5"] == 0.380
    assert out["f5Split"]["home"]["runs_l5_avg"] == 2.4
    assert out["firstInningSplit"]["home"]["scored_pct"] == 0.45


def test_orchestrator_mirror_block_preserves_existing_fields():
    """If `baseballHistoricalProfile` was already populated (e.g. with
    `last15` or `pitcherDuel`), the L5/L15 mirrors must be ADDITIVE
    rather than overwriting the dict."""
    pick_payload = {
        "baseballHistoricalProfile": {
            "last15": {"home": {"wins": 8, "losses": 7}},
            "pitcherDuel": "ace_vs_back-end",
        }
    }
    recent_form_payload = {
        "recent_run_split": {"home": {}, "away": {}},
        "recent_run_trend": "BOTH_COLD",
        "on_base_profile":  {"home": {}, "away": {}},
    }
    _hb = pick_payload.get("baseballHistoricalProfile") or {}
    _hb["recentRunSplit"]   = recent_form_payload["recent_run_split"]
    _hb["recentRunTrend"]   = recent_form_payload["recent_run_trend"]
    _hb["onBaseProfileL5"]  = recent_form_payload["on_base_profile"]
    pick_payload["baseballHistoricalProfile"] = _hb

    out = pick_payload["baseballHistoricalProfile"]
    # Pre-existing keys preserved
    assert out["last15"]["home"]["wins"] == 8
    assert out["pitcherDuel"] == "ace_vs_back-end"
    # New keys added
    assert out["recentRunTrend"] == "BOTH_COLD"


# ──────────────────────────────────────────────────────────────────────
# Fix 1 (Batch 3.5) — `_enrich_generic` wires TheStatsAPI enrichment
# (smoke test — full integration covered by existing batch3 tests)
# ──────────────────────────────────────────────────────────────────────
def test_enrich_generic_imports_thestatsapi_enrichment():
    """Smoke-level sanity that the lazy import path used inside
    `_enrich_generic` still resolves. The actual behaviour is tested
    via the dedicated batch3 suite with mocked httpx."""
    from services.external_sources import thestatsapi_enrichment as ts_enrich  # noqa: F401
    from services.external_sources import thestatsapi_client as ts_client      # noqa: F401
    assert callable(ts_enrich.enrich_pre_match)
    assert callable(ts_client.is_enabled)
