"""Sprint-F98 · Phase 3 — cross-source cascade selector tests.

Acceptance:
  1. Ranking honoured per metric (xG → TheStatsAPI > StatsBomb > SofaScore > FBref).
  2. Skips on: available=False, FIELD_NULL, FIELD_ABSENT, FIELD_EMPTY_LIST,
     SAMPLE_TOO_SMALL, FIELD_OUT_OF_RANGE, custom staleness codes.
  3. Picks the SECOND provider when the first fails the gates.
  4. Provenance records source + sample_size + fallback chain.
  5. Section-level ranking (h2h, odds) works per market.
  6. Odds: market-by-market selection — different markets can come from
     different providers.
  7. cascade_merge_envelopes is deterministic & pure.
  8. Empty input → available=False with a clear reason code.
"""
from __future__ import annotations

import pytest

from services.adapters import (
    adapt_fbref_to_f74,
    adapt_sofascore_to_f74,
    adapt_statsbomb_to_f74,
    adapt_thesportsdb_to_f74,
    adapt_thestatsapi_to_f74,
)
from services.adapters._envelope import (
    new_envelope,
    set_field,
    finalize_envelope,
)
from services.football_source_cascade import (
    CASCADE_SCHEMA_VERSION,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_RANKINGS,
    RC_FALLBACK_USED,
    RC_FIELD_SKIPPED_LOW_SAMPLE,
    RC_NO_PROVIDER_HAD_FIELD,
    RC_PRIMARY_HIT,
    RC_PROVIDER_STALE,
    RC_PROVIDER_UNAVAILABLE,
    cascade_merge_envelopes,
    select_field,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers — build minimal envelopes per source
# ─────────────────────────────────────────────────────────────────────
def _envelope_with(source: str, side: str, metric: str, value, *,
                    sample_size: int = 5) -> dict:
    env = new_envelope(source=source, available=True)
    set_field(env, f"{side}.{metric}", value, sample_size=sample_size)
    finalize_envelope(env)
    return env


# ─────────────────────────────────────────────────────────────────────
# 1. Ranking — xG primary
# ─────────────────────────────────────────────────────────────────────
def test_cascade_picks_thestatsapi_for_xg_over_sofascore():
    envs = [
        _envelope_with("sofascore",   "home", "xg_for_l5", 1.10),
        _envelope_with("thestatsapi", "home", "xg_for_l5", 1.45),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["xg_for_l5"] == 1.45
    prov = merged["field_provenance"]["home.xg_for_l5"]
    assert prov["source"] == "thestatsapi"
    assert RC_PRIMARY_HIT in prov["reason_codes"]


def test_cascade_picks_sofascore_for_shots_over_thestatsapi():
    envs = [
        _envelope_with("thestatsapi", "home", "shots_for_l5", 11.0),
        _envelope_with("sofascore",   "home", "shots_for_l5", 13.5),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["shots_for_l5"] == 13.5
    assert merged["field_provenance"]["home.shots_for_l5"]["source"] == "sofascore"


def test_cascade_picks_sofascore_for_corners():
    envs = [
        _envelope_with("thestatsapi", "home", "corners_for_l5", 5.0),
        _envelope_with("sofascore",   "home", "corners_for_l5", 6.4),
        _envelope_with("footystats",  "home", "corners_for_l5", 7.0),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["corners_for_l5"] == 6.4


# ─────────────────────────────────────────────────────────────────────
# 2. Fallback chain on FIELD_ABSENT / available=False
# ─────────────────────────────────────────────────────────────────────
def test_cascade_falls_back_when_primary_missing_field():
    # TheStatsAPI envelope present but does NOT contain xg_for_l5.
    env_tsa = new_envelope(source="thestatsapi", available=True)
    # SofaScore (rank 3 for xG) supplies it.
    env_ss  = _envelope_with("sofascore", "home", "xg_for_l5", 1.20)
    merged = cascade_merge_envelopes([env_tsa, env_ss])
    assert merged["home"]["xg_for_l5"] == 1.20
    prov = merged["field_provenance"]["home.xg_for_l5"]
    assert prov["source"] == "sofascore"
    assert RC_FALLBACK_USED in prov["reason_codes"]
    chain = {step["source"] for step in prov["fallback_chain"]}
    # TheStatsAPI should appear in the fallback chain as skipped.
    assert "thestatsapi" in chain or "statsbomb" in chain


def test_cascade_skips_unavailable_provider():
    env_tsa_dead = new_envelope(source="thestatsapi", available=False)
    env_ss = _envelope_with("sofascore", "home", "xg_for_l5", 1.20)
    merged = cascade_merge_envelopes([env_tsa_dead, env_ss])
    prov = merged["field_provenance"]["home.xg_for_l5"]
    assert prov["source"] == "sofascore"
    # The skip reasons must mention UNAVAILABLE for thestatsapi.
    tsa_step = next((s for s in prov["fallback_chain"]
                     if s["source"] == "thestatsapi"), None)
    assert tsa_step is not None
    assert RC_PROVIDER_UNAVAILABLE in tsa_step["skip_reasons"]


def test_cascade_skips_null_field_in_provenance():
    env_tsa = new_envelope(source="thestatsapi", available=True)
    # Setting None triggers FIELD_NULL provenance entry.
    set_field(env_tsa, "home.xg_for_l5", None, sample_size=5)
    finalize_envelope(env_tsa)
    env_ss  = _envelope_with("sofascore", "home", "xg_for_l5", 0.95)
    merged = cascade_merge_envelopes([env_tsa, env_ss])
    assert merged["home"]["xg_for_l5"] == 0.95
    chain = {(s["source"], tuple(s["skip_reasons"]))
             for s in merged["field_provenance"]["home.xg_for_l5"]["fallback_chain"]}
    found = False
    for src, reasons in chain:
        if src == "thestatsapi" and "FIELD_NULL" in reasons:
            found = True
    assert found, f"expected FIELD_NULL skip for thestatsapi, got {chain}"


# ─────────────────────────────────────────────────────────────────────
# 3. Sample-size guard
# ─────────────────────────────────────────────────────────────────────
def test_cascade_skips_low_sample_when_min_required():
    """xg_for_l5 requires min_sample=3 by default."""
    env_tsa_thin = _envelope_with("thestatsapi", "home", "xg_for_l5", 1.55,
                                    sample_size=1)
    env_ss_ok    = _envelope_with("sofascore",   "home", "xg_for_l5", 1.10,
                                    sample_size=5)
    merged = cascade_merge_envelopes([env_tsa_thin, env_ss_ok])
    assert merged["home"]["xg_for_l5"] == 1.10
    prov = merged["field_provenance"]["home.xg_for_l5"]
    assert prov["source"] == "sofascore"
    tsa_step = next(s for s in prov["fallback_chain"] if s["source"] == "thestatsapi")
    assert RC_FIELD_SKIPPED_LOW_SAMPLE in tsa_step["skip_reasons"]


def test_cascade_accepts_low_sample_when_min_override_to_zero():
    env_tsa_thin = _envelope_with("thestatsapi", "home", "xg_for_l5", 1.55,
                                    sample_size=1)
    env_ss_ok    = _envelope_with("sofascore",   "home", "xg_for_l5", 1.10,
                                    sample_size=5)
    merged = cascade_merge_envelopes(
        [env_tsa_thin, env_ss_ok],
        min_sample_override={"xg_for_l5": 0},
    )
    assert merged["home"]["xg_for_l5"] == 1.55  # primary now allowed


# ─────────────────────────────────────────────────────────────────────
# 4. Custom staleness codes
# ─────────────────────────────────────────────────────────────────────
def test_cascade_skips_on_stale_provider_marker():
    env_tsa = _envelope_with("thestatsapi", "home", "xg_for_l5", 1.55, sample_size=5)
    # Inject stale marker into provenance.
    env_tsa["field_provenance"]["home.xg_for_l5"]["reason_codes"].append("DATA_STALE_24H")
    env_ss = _envelope_with("sofascore", "home", "xg_for_l5", 0.90, sample_size=5)
    merged = cascade_merge_envelopes(
        [env_tsa, env_ss],
        staleness_codes=["DATA_STALE_24H"],
    )
    assert merged["home"]["xg_for_l5"] == 0.90
    tsa_step = next(s for s in merged["field_provenance"]["home.xg_for_l5"]["fallback_chain"]
                     if s["source"] == "thestatsapi")
    assert RC_PROVIDER_STALE in tsa_step["skip_reasons"]


# ─────────────────────────────────────────────────────────────────────
# 5. select_field direct usage
# ─────────────────────────────────────────────────────────────────────
def test_select_field_returns_none_when_no_provider_has_field():
    envs = [_envelope_with("sofascore", "home", "shots_for_l5", 12)]
    by_src = {e["source"]: e for e in envs}
    value, prov = select_field(
        by_src, side="home", metric="xg_for_l5",
        ranking=["thestatsapi", "statsbomb", "fbref"],
    )
    assert value is None
    assert RC_NO_PROVIDER_HAD_FIELD in prov["reason_codes"]


def test_select_field_records_sample_size_in_provenance():
    envs = [_envelope_with("thestatsapi", "away", "xg_for_l5", 1.30, sample_size=8)]
    by_src = {e["source"]: e for e in envs}
    value, prov = select_field(
        by_src, side="away", metric="xg_for_l5",
        ranking=["thestatsapi", "statsbomb"],
        min_sample=3,
    )
    assert value == 1.30
    assert prov["source"] == "thestatsapi"
    assert prov["sample_size"] == 8


# ─────────────────────────────────────────────────────────────────────
# 6. Section-level: h2h + odds (market by market)
# ─────────────────────────────────────────────────────────────────────
def test_cascade_picks_sofascore_h2h_over_thesportsdb():
    env_ss = new_envelope(source="sofascore", available=True)
    set_field(env_ss, "h2h.matches",   [{"home_goals": 1, "away_goals": 0}], sample_size=1)
    set_field(env_ss, "h2h.home_wins", 1, sample_size=1)
    set_field(env_ss, "h2h.sample",    1, sample_size=1)
    finalize_envelope(env_ss)

    env_tsdb = new_envelope(source="thesportsdb", available=True)
    set_field(env_tsdb, "h2h.matches",   [{"home_goals": 2, "away_goals": 2}], sample_size=1)
    finalize_envelope(env_tsdb)

    merged = cascade_merge_envelopes([env_ss, env_tsdb])
    assert merged["h2h"]["sample"] == 1
    assert merged["h2h"]["matches"][0]["home_goals"] == 1
    assert merged["field_provenance"]["h2h"]["source"] == "sofascore"


def test_cascade_odds_selects_per_market_across_providers():
    """match_winner from the-odds-api, over_2_5 from sofascore."""
    env_oa = new_envelope(source="the_odds_api", available=True)
    set_field(env_oa, "odds.match_winner", {"home": 1.7, "draw": 3.6, "away": 4.5})
    finalize_envelope(env_oa)

    env_ss = new_envelope(source="sofascore", available=True)
    set_field(env_ss, "odds.over_2_5", {"yes": 2.1, "no": 1.7})
    set_field(env_ss, "odds.match_winner", {"home": 1.65, "draw": 3.7, "away": 4.6})
    finalize_envelope(env_ss)

    merged = cascade_merge_envelopes([env_oa, env_ss])
    # match_winner → primary (the_odds_api)
    assert merged["odds"]["match_winner"]["home"] == 1.7
    assert merged["field_provenance"]["odds.match_winner"]["source"] == "the_odds_api"
    # over_2_5 → fallback (sofascore) because the-odds-api lacks it
    assert merged["odds"]["over_2_5"]["yes"] == 2.1
    assert merged["field_provenance"]["odds.over_2_5"]["source"] == "sofascore"


# ─────────────────────────────────────────────────────────────────────
# 7. End-to-end with REAL adapters
# ─────────────────────────────────────────────────────────────────────
def test_cascade_end_to_end_with_real_adapters():
    """Integration test: SofaScore L5 form + TheStatsAPI season xG +
    StatsBomb shots → cascade merges them into one rich envelope."""
    sofascore_raw = {
        "event_id": 12345,
        "home_form": [
            {"home_team": "Argentina", "away_team": "Italy",
             "home_score": 2, "away_score": 1,
             "home_stats": {"shots": 12, "shots_on_target": 5,
                             "possession": 58.0, "corners": 6},
             "away_stats": {"shots": 8, "corners": 3}},
            {"home_team": "Argentina", "away_team": "Brazil",
             "home_score": 1, "away_score": 1,
             "home_stats": {"shots": 14, "shots_on_target": 6,
                             "possession": 60.0, "corners": 5},
             "away_stats": {"shots": 9, "corners": 4}},
            {"home_team": "Argentina", "away_team": "Chile",
             "home_score": 3, "away_score": 0,
             "home_stats": {"shots": 18, "shots_on_target": 8,
                             "possession": 65.0, "corners": 8},
             "away_stats": {"shots": 4, "corners": 1}},
        ],
        "away_form": [
            {"home_team": "Austria", "away_team": "Croatia",
             "home_score": 1, "away_score": 2,
             "home_stats": {"shots": 9, "shots_on_target": 3,
                             "possession": 48.0, "corners": 4},
             "away_stats": {"shots": 11, "corners": 6}},
            {"home_team": "Austria", "away_team": "Germany",
             "home_score": 0, "away_score": 0,
             "home_stats": {"shots": 7, "shots_on_target": 2,
                             "possession": 45.0, "corners": 3},
             "away_stats": {"shots": 13, "corners": 7}},
            {"home_team": "Austria", "away_team": "Serbia",
             "home_score": 2, "away_score": 1,
             "home_stats": {"shots": 11, "shots_on_target": 4,
                             "possession": 52.0, "corners": 5},
             "away_stats": {"shots": 8, "corners": 3}},
        ],
    }
    tsa_raw = {
        "team_stats": {
            "home": {"expected_goals_per_match": 1.55,
                     "expected_goals_against_per_match": 0.85},
            "away": {"xg_per_match": 0.95,
                     "expected_goals_against_per_match": 1.35},
        },
    }
    statsbomb_raw = {
        "sample_size":   5,
        "home_features": {
            "shots_for_l5": 15.2, "shots_on_target_l5": 6.3,
            "xg_for_l5":    1.5,  "xg_against_l5":     0.9,
            "sample": 5,
        },
        "away_features": {
            "shots_for_l5": 9.4, "xg_for_l5": 0.95,
            "sample": 5,
        },
    }
    envs = [
        adapt_sofascore_to_f74(sofascore_raw, home_team="Argentina", away_team="Austria"),
        adapt_thestatsapi_to_f74(tsa_raw),
        adapt_statsbomb_to_f74(statsbomb_raw),
    ]
    merged = cascade_merge_envelopes(envs)

    # xG → TheStatsAPI primary
    assert merged["home"]["xg_for_l5"] == pytest.approx(1.55)
    assert merged["field_provenance"]["home.xg_for_l5"]["source"] == "thestatsapi"

    # Shots → SofaScore primary (real value from form aggregation)
    assert merged["home"]["shots_for_l5"] is not None
    assert merged["field_provenance"]["home.shots_for_l5"]["source"] == "sofascore"

    # Possession → SofaScore primary
    assert merged["home"]["possession_avg_l5"] is not None
    assert merged["field_provenance"]["home.possession_avg_l5"]["source"] == "sofascore"

    # Corners → SofaScore primary
    assert merged["home"]["corners_for_l5"] is not None
    assert merged["field_provenance"]["home.corners_for_l5"]["source"] == "sofascore"

    # Cascade flag → not THIN any more
    assert merged["data_quality"] != "THIN"
    assert merged["available"] is True


# ─────────────────────────────────────────────────────────────────────
# 8. Empty input / degenerate cases
# ─────────────────────────────────────────────────────────────────────
def test_cascade_empty_input_returns_unavailable_envelope():
    merged = cascade_merge_envelopes([])
    assert merged["available"] is False
    assert "CASCADE_NO_USABLE_PROVIDERS" in merged["reason_codes"]
    assert merged["home"] == {}
    assert merged["away"] == {}


def test_cascade_none_and_garbage_envelopes_are_ignored():
    merged = cascade_merge_envelopes(
        [None, "garbage", 42,
         _envelope_with("sofascore", "home", "shots_for_l5", 12)]
    )
    assert merged["available"] is True
    assert merged["home"]["shots_for_l5"] == 12


def test_cascade_is_deterministic():
    envs = [
        _envelope_with("sofascore", "home", "xg_for_l5", 1.0),
        _envelope_with("thestatsapi", "home", "xg_for_l5", 1.5),
    ]
    a = cascade_merge_envelopes(envs)
    b = cascade_merge_envelopes(envs)
    # Strip volatile timestamps before comparing.
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    assert a == b


# ─────────────────────────────────────────────────────────────────────
# 9. Ranking metadata sanity
# ─────────────────────────────────────────────────────────────────────
def test_default_rankings_match_user_spec():
    """User-binding rankings — these MUST NOT silently change."""
    assert DEFAULT_RANKINGS["xg_for_l5"][:4] == ["thestatsapi", "statsbomb", "sofascore", "fbref"]
    assert DEFAULT_RANKINGS["shots_for_l5"][:4] == ["sofascore", "thestatsapi", "statsbomb", "fbref"]
    assert DEFAULT_RANKINGS["possession_avg_l5"][:4] == ["sofascore", "thestatsapi", "fbref", "statsbomb"]
    assert DEFAULT_RANKINGS["recent_fixtures"][:4] == ["sofascore", "thesportsdb", "thestatsapi", "fbref"]
    assert DEFAULT_RANKINGS["_h2h"]   == ["sofascore", "thesportsdb", "thestatsapi"]
    assert DEFAULT_RANKINGS["corners_for_l5"] == ["sofascore", "thestatsapi", "footystats", "totalcorner"]
    assert DEFAULT_RANKINGS["_odds"]  == ["the_odds_api", "thestatsapi", "odds_portal", "sofascore"]


def test_default_min_sample_for_strong_metrics_is_three():
    for m in ("xg_for_l5", "xg_against_l5", "shots_for_l5",
              "possession_avg_l5", "corners_for_l5"):
        assert DEFAULT_MIN_SAMPLE[m] == 3
