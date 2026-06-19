"""Sprint D10 wiring · tests del helper football_total_signal_inputs +
integración con enrich_football_match.

Verifica que:
  * Sin lambdas, devuelve None (UI no renderiza panel).
  * Con lambda_home / lambda_away en el match, construye el payload.
  * Acepta el fallback de home_xg / away_xg como lambdas si no hay
    lambdas explícitas.
  * Normaliza H2H, recent matches y xG blocks.
  * `enrich_football_match` adjunta `football_total_signal_preview_inputs`.
"""
from __future__ import annotations

from services.football_total_signal_inputs import (
    build_football_total_signal_preview_inputs,
)
from services.football_quality import enrich_football_match


# ─── Pure helper ────────────────────────────────────────────────────────
def test_returns_none_without_lambdas():
    assert build_football_total_signal_preview_inputs(None) is None
    assert build_football_total_signal_preview_inputs({}) is None
    assert build_football_total_signal_preview_inputs({"home_team": "X"}) is None


def test_builds_payload_from_explicit_lambdas():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.55, "lambda_away": 0.93,
    })
    assert out is not None
    assert out["base_lambda_home"] == 1.55
    assert out["base_lambda_away"] == 0.93
    assert out["base_expected_goals"] == 2.48


def test_fallback_to_home_away_xg_when_no_lambdas():
    out = build_football_total_signal_preview_inputs({
        "home_xg": 1.2, "away_xg": 1.1,
    })
    assert out is not None
    assert out["base_lambda_home"] == 1.2
    assert out["base_lambda_away"] == 1.1


def test_normalises_h2h_with_status_default_final():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.0, "lambda_away": 1.0,
        "h2h_games": [
            {"home_goals": 2, "away_goals": 1},
            {"home_score": 3, "away_score": 3},
        ],
    })
    assert out is not None
    assert out["recent_h2h_games"] is not None
    assert len(out["recent_h2h_games"]) == 2
    assert all(g["status"] == "FINAL" for g in out["recent_h2h_games"])


def test_extracts_team_recent_with_opponent_strength_default():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.5, "lambda_away": 1.0,
        "home_recent_matches": [
            {"goals_scored": 2, "goals_conceded": 1},
            {"goals_for": 3, "goals_against": 0, "opponent_strength": "weak"},
        ],
    })
    assert out["home_recent_matches"] is not None
    assert out["home_recent_matches"][0]["opponent_strength"] == "average"
    assert out["home_recent_matches"][1]["opponent_strength"] == "weak"
    # Normaliza goals_for/against → goals_scored/conceded.
    assert out["home_recent_matches"][1]["goals_scored"] == 3
    assert out["home_recent_matches"][1]["goals_conceded"] == 0


def test_caps_recent_matches_at_5():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.0, "lambda_away": 1.0,
        "home_recent_matches": [{"goals_scored": 1, "goals_conceded": 1}] * 10,
    })
    assert len(out["home_recent_matches"]) == 5


def test_xg_block_with_l5_and_l15():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.4, "lambda_away": 1.0,
        "home_xg_recent": {
            "xg_for_l5": 1.5, "xg_against_l5": 1.0,
            "xg_for_l15": 1.3, "xg_against_l15": 1.1,
            "matches_available": 15,
        },
    })
    assert out["home_xg_recent"] is not None
    assert out["home_xg_recent"]["xg_for_l5"] == 1.5
    assert out["home_xg_recent"]["matches_available"] == 15


def test_xg_block_none_when_no_data():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.0, "lambda_away": 1.0,
    })
    assert out["home_xg_recent"] is None
    assert out["away_xg_recent"] is None


def test_xg_block_accepts_alternative_keys():
    """Acepta `xg_l5_mean` (output del cliente real xG D9.2-B)."""
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.0, "lambda_away": 1.0,
        "home_xg_recent": {
            "xg_l5_mean": 1.4,
            "xg_l15_mean": 1.2,
            "matches_available": 10,
        },
    })
    assert out["home_xg_recent"] is not None
    assert out["home_xg_recent"]["xg_for_l5"] == 1.4


def test_payload_includes_match_context():
    out = build_football_total_signal_preview_inputs({
        "lambda_home": 1.5, "lambda_away": 1.0,
        "competition_id": 42, "competition_type": "LEAGUE",
        "is_friendly": False,
    })
    ctx = out["current_match_context"]
    assert ctx["competition_id"] == 42
    assert ctx["competition_type"] == "LEAGUE"
    assert ctx["is_friendly"] is False


# ─── Integration with enrich_football_match ─────────────────────────────
def test_enrich_football_match_attaches_preview_inputs_when_lambdas_present():
    """El partido enriquecido debe llevar el campo
    `football_total_signal_preview_inputs` para que la UI lo
    consuma."""
    match = {
        "sport": "football",
        "lambda_home": 1.55, "lambda_away": 0.93,
        "home_team": {"name": "A"}, "away_team": {"name": "B"},
        "h2h_games": [{"home_goals": 2, "away_goals": 1, "status": "FINAL"}],
    }
    enriched = enrich_football_match(match)
    assert "football_total_signal_preview_inputs" in enriched
    preview = enriched["football_total_signal_preview_inputs"]
    assert preview["base_lambda_home"] == 1.55


def test_enrich_football_match_without_lambdas_does_not_attach():
    match = {"sport": "football", "home_team": {"name": "A"}, "away_team": {"name": "B"}}
    enriched = enrich_football_match(match)
    # Sin lambdas, el campo no debe estar (UI no renderiza panel D10).
    assert "football_total_signal_preview_inputs" not in enriched


def test_enrich_football_match_idempotent():
    """Llamadas repetidas no deben duplicar trabajo ni corromper la
    estructura."""
    match = {
        "sport": "football",
        "lambda_home": 1.2, "lambda_away": 1.0,
    }
    enriched1 = enrich_football_match(match)
    pi1 = enriched1["football_total_signal_preview_inputs"]
    enriched2 = enrich_football_match(enriched1)
    pi2 = enriched2["football_total_signal_preview_inputs"]
    assert pi1 == pi2


def test_enrich_football_match_skips_non_football_sport():
    """MLB matches no deben tocarse."""
    match = {"sport": "baseball", "home_team": "Yankees", "away_team": "Red Sox"}
    enriched = enrich_football_match(match)
    assert "football_total_signal_preview_inputs" not in enriched
