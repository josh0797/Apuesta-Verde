"""Sprint-F99.5 · Tests del Odds Aggregator (multi-market, vista canónica + best prices + movement).

Guardas binding del usuario:

  1. ``odds_cascade.py`` NO se duplica — el aggregator es un módulo
     **separado** que consume el cascade existente via raw quotes.
  2. Provider priority por defecto: the_odds_api → thestatsapi →
     sofascore → oddsportal → manual.
  3. Mercado canónico mantiene consistencia bookmaker + snapshot
     (no se mezcla Over de una casa con Under de otra para vig).
  4. ``best_prices`` por selección es una vista adicional (no se usa
     para vig removal).
  5. Líneas reales conservadas (no se fuerzan 2.5/3.5).
  6. Cero leak a F74 / ``football_data_enrichment``.
  7. Ausencia de odds NO afecta data_quality (eso lo testea P99.6 #6).
  8. Feature flag ``ENABLE_F99_ODDS_AGGREGATOR`` (opt-in).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.football_odds_aggregator import (
    DEFAULT_PROVIDER_PRIORITY,
    MARKET_FAMILIES,
    FLAG_ENV_VAR,
    RC_AGGREGATOR_USED,
    RC_BEST_PRICE_SELECTED,
    RC_FALLBACK_USED,
    RC_MARKET_NORMALIZED,
    RC_MANUAL_REQUIRED,
    RC_MOVEMENT_RECORDED,
    RC_NO_PRIMARY,
    RC_PRIMARY_USED,
    aggregate_match_odds,
    is_enabled,
)


_FIXED_SNAPSHOT_AT = "2024-05-12T15:00:00+00:00"


def _now() -> str:
    return _FIXED_SNAPSHOT_AT


def _earlier(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _q(market, selection, price, *, line=None, bm="Book A", at=None):
    return {
        "market_family": market,
        "selection":     selection,
        "price":         price,
        "line":          line,
        "bookmaker":     bm,
        "snapshot_at":   at or _FIXED_SNAPSHOT_AT,
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Provider priority por defecto = binding
# ─────────────────────────────────────────────────────────────────────
def test_default_provider_priority_matches_binding():
    assert DEFAULT_PROVIDER_PRIORITY == (
        "the_odds_api", "thestatsapi", "sofascore", "oddsportal", "manual",
    )


def test_market_families_includes_binding_scope():
    expected = {
        "MATCH_WINNER", "DOUBLE_CHANCE", "DRAW_NO_BET", "ASIAN_HANDICAP",
        "TOTAL_GOALS", "BOTH_TEAMS_TO_SCORE", "TOTAL_CORNERS",
        "TEAM_CORNERS", "ASIAN_CORNERS", "TOTAL_CARDS",
    }
    assert expected.issubset(set(MARKET_FAMILIES))


def test_feature_flag_helper(monkeypatch):
    monkeypatch.delenv(FLAG_ENV_VAR, raising=False)
    assert is_enabled() is False
    monkeypatch.setenv(FLAG_ENV_VAR, "true")
    assert is_enabled() is True


# ─────────────────────────────────────────────────────────────────────
# 2. Canonical market — consistencia bookmaker + snapshot
# ─────────────────────────────────────────────────────────────────────
def test_canonical_market_winner_picks_primary_when_complete():
    """``the_odds_api`` has a complete H2H line → it MUST win."""
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
        ],
        "thestatsapi": [
            _q("MATCH_WINNER", "HOME", 1.95, bm="TheStatsAPI"),
            _q("MATCH_WINNER", "DRAW", 3.20, bm="TheStatsAPI"),
            _q("MATCH_WINNER", "AWAY", 3.80, bm="TheStatsAPI"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    mw = out["canonical_markets"]["MATCH_WINNER"]
    assert mw["provider"]  == "the_odds_api"
    assert mw["bookmaker"] == "Bet365"
    # All 3 selections from the SAME bookmaker + snapshot.
    sels = mw["selections"]
    assert sels["HOME"]["price"] == 1.85
    assert sels["DRAW"]["price"] == 3.40
    assert sels["AWAY"]["price"] == 4.20
    assert RC_PRIMARY_USED in mw["reason_codes"]
    assert RC_MARKET_NORMALIZED in mw["reason_codes"]


def test_canonical_does_not_mix_books_or_snapshots_within_a_market():
    """Two H2H selections at the SAME provider but DIFFERENT bookmakers
    must NOT be assembled into one canonical market."""
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.30, bm="Pinnacle"),  # different book!
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
            # Complete line via William Hill.
            _q("MATCH_WINNER", "HOME", 1.90, bm="WHill"),
            _q("MATCH_WINNER", "DRAW", 3.20, bm="WHill"),
            _q("MATCH_WINNER", "AWAY", 4.10, bm="WHill"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    mw = out["canonical_markets"]["MATCH_WINNER"]
    # Either Bet365 (incomplete due to Pinnacle gap) or WHill — but NOT a mix.
    assert mw["bookmaker"] == "WHill", (
        "Aggregator violó la guarda de consistencia de bookmaker."
    )
    assert mw["selections"]["HOME"]["price"] == 1.90
    assert mw["selections"]["DRAW"]["price"] == 3.20


def test_canonical_falls_back_to_thestatsapi_when_primary_incomplete():
    """Primary has only HOME — secondary has complete H2H → use secondary."""
    snap = {
        "the_odds_api": [_q("MATCH_WINNER", "HOME", 1.85, bm="Bet365")],
        "thestatsapi": [
            _q("MATCH_WINNER", "HOME", 1.95, bm="TheStatsAPI"),
            _q("MATCH_WINNER", "DRAW", 3.20, bm="TheStatsAPI"),
            _q("MATCH_WINNER", "AWAY", 3.80, bm="TheStatsAPI"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    mw = out["canonical_markets"]["MATCH_WINNER"]
    assert mw["provider"]  == "thestatsapi"
    assert RC_FALLBACK_USED in mw["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 3. Best prices — vista adicional
# ─────────────────────────────────────────────────────────────────────
def test_best_prices_picks_highest_price_per_selection():
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
        ],
        "thestatsapi": [
            _q("MATCH_WINNER", "HOME", 1.95, bm="WHill"),    # mejor HOME
            _q("MATCH_WINNER", "AWAY", 4.50, bm="WHill"),    # mejor AWAY
        ],
        "sofascore": [
            _q("MATCH_WINNER", "DRAW", 3.55, bm="Pinnacle"), # mejor DRAW
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    bp = out["best_prices"]["MATCH_WINNER"]
    assert bp["HOME"]["price"] == 1.95 and bp["HOME"]["bookmaker"] == "WHill"
    assert bp["DRAW"]["price"] == 3.55 and bp["DRAW"]["bookmaker"] == "Pinnacle"
    assert bp["AWAY"]["price"] == 4.50 and bp["AWAY"]["bookmaker"] == "WHill"
    assert RC_BEST_PRICE_SELECTED in out["reason_codes"]


def test_best_prices_do_not_pollute_canonical_overround():
    """Critical binding: the canonical market is computed from the
    primary's SAME-bookmaker line — its overround MUST NOT be affected
    by the best_prices view."""
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
        ],
        "sofascore": [_q("MATCH_WINNER", "HOME", 9.99, bm="Sharp")],  # adversarial
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    mw = out["canonical_markets"]["MATCH_WINNER"]
    expected_or = round(1/1.85 + 1/3.40 + 1/4.20, 4)
    assert abs(mw["overround"] - expected_or) < 1e-3
    # Best prices DO see the 9.99 — but the canonical does NOT.
    assert out["best_prices"]["MATCH_WINNER"]["HOME"]["price"] == 9.99


# ─────────────────────────────────────────────────────────────────────
# 4. Líneas reales conservadas (no 2.5/3.5 forzados)
# ─────────────────────────────────────────────────────────────────────
def test_canonical_preserves_arbitrary_lines_for_totals():
    snap = {
        "the_odds_api": [
            _q("TOTAL_GOALS", "OVER",  1.91, line=2.25, bm="Bet365"),
            _q("TOTAL_GOALS", "UNDER", 1.95, line=2.25, bm="Bet365"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap,
                                requested_markets=["TOTAL_GOALS"])
    m = out["canonical_markets"]["TOTAL_GOALS"]
    assert m["line"] == 2.25
    assert m["selections"]["OVER"]["line"]  == 2.25
    assert m["selections"]["UNDER"]["line"] == 2.25


def test_total_corners_arbitrary_line_preserved():
    snap = {
        "the_odds_api": [
            _q("TOTAL_CORNERS", "OVER",  1.85, line=10.5, bm="Bet365"),
            _q("TOTAL_CORNERS", "UNDER", 1.95, line=10.5, bm="Bet365"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap,
                                requested_markets=["TOTAL_CORNERS"])
    assert out["canonical_markets"]["TOTAL_CORNERS"]["line"] == 10.5


# ─────────────────────────────────────────────────────────────────────
# 5. Movement tracking
# ─────────────────────────────────────────────────────────────────────
def test_movement_records_opening_latest_change():
    snap_current = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.75, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.50, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.40, bm="Bet365"),
        ],
    }
    previous = {
        "canonical_markets": {
            "MATCH_WINNER": {
                "selections": {
                    "HOME": {"price": 1.95},
                    "DRAW": {"price": 3.30},
                    "AWAY": {"price": 4.10},
                },
                "opening_selections": {
                    "HOME": {"price": 2.05},
                    "DRAW": {"price": 3.20},
                    "AWAY": {"price": 4.00},
                },
            },
        },
        "snapshots_count": 3,
    }
    out = aggregate_match_odds(
        "A", "B",
        snapshots_from=snap_current,
        previous_snapshot=previous,
    )
    mov = out["movement"]["MATCH_WINNER"]
    home_mov = mov["movement_per_selection"]["HOME"]
    assert home_mov["opening_price"] == 2.05
    assert home_mov["latest_price"]  == 1.75
    assert home_mov["absolute_change"] == round(1.75 - 2.05, 4)
    assert home_mov["percentage_change"] < 0
    assert mov["snapshots_count"] == 4  # incremented
    assert RC_MOVEMENT_RECORDED in out["reason_codes"]


def test_movement_empty_when_no_previous_snapshot():
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    assert out["movement"] == {}
    assert RC_MOVEMENT_RECORDED not in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 6. Cero leak hacia F74 / football_data_enrichment
# ─────────────────────────────────────────────────────────────────────
def test_output_never_contains_football_data_enrichment_keys():
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="Bet365"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="Bet365"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="Bet365"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    forbidden = {
        "football_data_enrichment", "data_quality", "field_provenance",
        "schema_migration", "home", "away_team",  # only top-level "home"/"away" strings allowed
    }
    # ``home`` and ``away`` ARE allowed (the input team names). Filter them.
    leaked = set(out.keys()) & {"football_data_enrichment", "data_quality",
                                  "field_provenance", "schema_migration",
                                  "evaluated_market", "edge", "ev", "market_trap"}
    assert leaked == set(), f"odds aggregator leaked F74-only keys: {leaked}"


def test_aggregator_does_not_set_data_quality():
    snap = {"the_odds_api": []}
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    assert "data_quality" not in out


# ─────────────────────────────────────────────────────────────────────
# 7. Manual required when all sources empty
# ─────────────────────────────────────────────────────────────────────
def test_all_sources_empty_emits_manual_required():
    out = aggregate_match_odds("A", "B", snapshots_from={})
    assert out["available"] is False
    assert RC_MANUAL_REQUIRED in out["reason_codes"]
    assert any(market.get("provider") is None
                for market in out["canonical_markets"].values()) or \
           out["canonical_markets"] == {}


def test_partial_request_only_returns_requested_markets():
    snap = {
        "the_odds_api": [
            _q("MATCH_WINNER", "HOME", 1.85, bm="B"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="B"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="B"),
            _q("TOTAL_GOALS",  "OVER", 1.91, line=2.5, bm="B"),
            _q("TOTAL_GOALS",  "UNDER", 1.95, line=2.5, bm="B"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap,
                                requested_markets=["MATCH_WINNER"])
    assert "MATCH_WINNER" in out["canonical_markets"]
    assert "TOTAL_GOALS"  not in out["canonical_markets"]


# ─────────────────────────────────────────────────────────────────────
# 8. Fail-soft con inputs malformados
# ─────────────────────────────────────────────────────────────────────
def test_aggregator_handles_malformed_quotes():
    snap = {
        "the_odds_api": [
            None,                                              # bad
            {"bad": "shape"},                                  # missing keys
            {"market_family": "unknown_market", "price": 1.5}, # bad family
            _q("MATCH_WINNER", "HOME", 0.5, bm="B"),           # price < 1 → invalid
            _q("MATCH_WINNER", "HOME", 1.85, bm="B"),
            _q("MATCH_WINNER", "DRAW", 3.40, bm="B"),
            _q("MATCH_WINNER", "AWAY", 4.20, bm="B"),
        ],
    }
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    assert out["available"] is True
    assert out["canonical_markets"]["MATCH_WINNER"]["selections"]["HOME"]["price"] == 1.85


def test_invalid_input_returns_safe_empty_payload():
    out = aggregate_match_odds("", "", snapshots_from=None)
    assert out["available"]            is False
    assert out["canonical_markets"]    == {}
    assert out["best_prices"]          == {}
    assert out["movement"]             == {}
    assert "F99_ODDS_SCHEMA_INVALID"   in out["reason_codes"]


def test_aggregator_used_reason_code_always_present():
    snap = {"the_odds_api": [
        _q("MATCH_WINNER", "HOME", 1.85, bm="B"),
        _q("MATCH_WINNER", "DRAW", 3.40, bm="B"),
        _q("MATCH_WINNER", "AWAY", 4.20, bm="B"),
    ]}
    out = aggregate_match_odds("A", "B", snapshots_from=snap)
    assert RC_AGGREGATOR_USED in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 9. No duplicar odds_cascade.py — el aggregator NO importa fetch_oddsportal_h2h
# ─────────────────────────────────────────────────────────────────────
def test_aggregator_does_not_import_oddsportal_or_theoddsapi_directly():
    """El aggregator NO debe importar adapters de fetch — sólo consume
    raw quotes que el caller le pasa (consistencia con la cascade
    existente)."""
    import services.football_odds_aggregator as agg
    src = open(agg.__file__, encoding="utf-8").read()
    forbidden_imports = (
        "from .external_sources.odds_portal_client",
        "from .external_sources.the_odds_api_client",
        "import services.external_sources.odds_portal_client",
        "import services.external_sources.the_odds_api_client",
    )
    for fi in forbidden_imports:
        assert fi not in src, (
            f"F99.5 aggregator está duplicando lógica de cascade — encontró '{fi}'"
        )


# ─────────────────────────────────────────────────────────────────────
# 10. Reason codes completos
# ─────────────────────────────────────────────────────────────────────
def test_no_primary_reason_code_when_only_partial_quotes():
    """Si solo viene HOME (sin DRAW ni AWAY), no se puede construir
    canónico → ``F99_ODDS_NO_PRIMARY``."""
    snap = {"the_odds_api": [_q("MATCH_WINNER", "HOME", 1.85, bm="B")]}
    out = aggregate_match_odds("A", "B", snapshots_from=snap,
                                requested_markets=["MATCH_WINNER"])
    assert RC_NO_PRIMARY in out["reason_codes"]
    assert out["canonical_markets"]["MATCH_WINNER"]["provider"] is None
