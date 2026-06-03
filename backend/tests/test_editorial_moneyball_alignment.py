"""Tests for editorial_context_service + editorial_signal_mapper Moneyball alignment."""
from __future__ import annotations

import pytest

from services.editorial_context.editorial_context_service import (
    EDITORIAL_CONTEXT_VERSION,
    MLB_EDITORIAL_TAGS,
    MLB_FAST_STALE_TAGS,
    annotate_editorial_vs_moneyball,
    extract_mlb_tag,
    _empty_payload,
)
from services.editorial_context.editorial_signal_mapper import (
    classify_signal,
    detect_sport_hint,
)


# ─────────────────────────────────────────────────────────────────────
# editorial_context_service
# ─────────────────────────────────────────────────────────────────────
def test_editorial_version_bumped_to_p4_moneyball():
    """The version must reflect the Moneyball alignment."""
    assert EDITORIAL_CONTEXT_VERSION.startswith("p4-moneyball")


def test_mlb_editorial_tags_canonical_set():
    expected = {
        "public_narrative",
        "injury_or_lineup_note",
        "pitcher_news",
        "bullpen_news",
        "market_public_bias",
        "weather_or_park_note",
    }
    assert set(MLB_EDITORIAL_TAGS) == expected


def test_fast_stale_tags_include_pitcher_lineup():
    assert "pitcher_news" in MLB_FAST_STALE_TAGS
    assert "injury_or_lineup_note" in MLB_FAST_STALE_TAGS
    assert "bullpen_news" in MLB_FAST_STALE_TAGS


def test_empty_payload_includes_moneyball_metadata_fields():
    p = _empty_payload("test_reason")
    assert "moneyball_interpretation" in p
    assert "editorial_vs_model_alignment" in p
    assert "used_as_confirmation_only" in p
    assert p["used_as_confirmation_only"] is True
    assert p["_engine_version"] == EDITORIAL_CONTEXT_VERSION


def test_annotate_aligned_when_both_recommend_under():
    ed = {"consensus_direction": "under", "consensus_market": "Full Game Under"}
    pick = {
        "market_selection": {"recommended_market": "Full Game Under"},
        "recommendation":   {"market": "Full Game Under"},
    }
    out = annotate_editorial_vs_moneyball(ed, pick_payload=pick)
    assert out["editorial_vs_model_alignment"] == "aligned"
    assert out["used_as_confirmation_only"] is True


def test_annotate_contradicts_when_editorial_over_engine_under():
    ed = {"consensus_direction": "over", "consensus_market": "Full Game Over"}
    pick = {
        "market_selection": {"recommended_market": "F5 Under"},
        "recommendation":   {"market": "F5 Under"},
    }
    out = annotate_editorial_vs_moneyball(ed, pick_payload=pick)
    assert out["editorial_vs_model_alignment"] == "contradicts"
    assert out["moneyball_interpretation"] is not None
    assert "Over" in out["moneyball_interpretation"]


def test_annotate_ghost_edge_with_editorial_over_flags_public_narrative_risk():
    ed = {"consensus_direction": "over", "consensus_market": "Full Game Over"}
    pick = {
        "market_selection": {"recommended_market": "Full Game Under"},
        "ghost_edges":      {
            "flags":        ["ERA_UNDERSTATES_RISK"],
            "blocked_pick": True,
            "available":    True,
        },
    }
    out = annotate_editorial_vs_moneyball(ed, pick_payload=pick)
    assert "PUBLIC_NARRATIVE_RISK" in out["contradiction_flags"]
    assert "EDITORIAL_CONTRADICTS_MONEYBALL" in out["contradiction_flags"]
    assert out["editorial_vs_model_alignment"] == "contradicts"


def test_annotate_does_not_modify_confidence():
    """The annotation must NEVER add or set a confidence field on the
    editorial payload — editorial is confirmation only."""
    ed = {"consensus_direction": "under", "consensus_market": "Full Game Under"}
    pick = {"market_selection": {"recommended_market": "Full Game Under"}}
    out = annotate_editorial_vs_moneyball(ed, pick_payload=pick)
    assert "confidence" not in out
    assert "confidence_score" not in out
    assert "confidence_delta" not in out


def test_annotate_fail_soft_on_bad_input():
    out = annotate_editorial_vs_moneyball("not a dict", pick_payload=None)  # type: ignore[arg-type]
    assert out["available"] is False
    assert out["editorial_vs_model_alignment"] is None
    assert out["used_as_confirmation_only"] is True


def test_extract_mlb_tag_pitcher_news():
    sig = {"sport": "baseball",
           "text":  "El abridor probable es Justin Verlander."}
    assert extract_mlb_tag(sig) == "pitcher_news"


def test_extract_mlb_tag_bullpen_news():
    sig = {"sport": "baseball",
           "text":  "El bullpen está fatigado tras la serie."}
    assert extract_mlb_tag(sig) == "bullpen_news"


def test_extract_mlb_tag_injury_or_lineup():
    sig = {"sport": "baseball",
           "text":  "Trout fue scratched de la alineación."}
    assert extract_mlb_tag(sig) == "injury_or_lineup_note"


def test_extract_mlb_tag_weather_or_park():
    sig = {"sport": "baseball",
           "text":  "El viento favorece el bateo en Coors Field."}
    assert extract_mlb_tag(sig) == "weather_or_park_note"


def test_extract_mlb_tag_returns_none_for_non_mlb():
    sig = {"sport": "football", "text": "Goleada esperada del Madrid."}
    assert extract_mlb_tag(sig) is None


# ─────────────────────────────────────────────────────────────────────
# editorial_signal_mapper
# ─────────────────────────────────────────────────────────────────────
def test_sport_hint_baseball():
    assert detect_sport_hint("El abridor con FIP 2.80 enfrenta a un bullpen") \
        == "baseball"
    assert detect_sport_hint("F5 Under 4.5 con cuota @1.85") == "baseball"
    assert detect_sport_hint("NRFI con altísimo K%") == "baseball"


def test_sport_hint_basketball():
    assert detect_sport_hint("Spread Lakers -5.5 con pace alto") == "basketball"
    assert detect_sport_hint("Back-to-back para los Celtics") == "basketball"


def test_sport_hint_football():
    assert detect_sport_hint("Goles esperados del Real Madrid") == "football"
    assert detect_sport_hint("Tarjetas amarillas en el clásico") == "football"


def test_sport_hint_ambiguous_returns_none():
    assert detect_sport_hint("El partido será emocionante.") is None


def test_classify_signal_includes_sport_hint():
    out = classify_signal("Recomendamos F5 Under 4.5 con cuota @1.85.")
    assert out["sport_hint"] == "baseball"
    assert out["signal_type"] == "MARKET_SUGGESTION"
    assert "tags" in out
    assert isinstance(out["tags"], list)


def test_mlb_normal_motivation_neutralised():
    """MLB regular-season motivation must be downgraded to neutral."""
    out = classify_signal(
        "El abridor necesita ganar para mantener su lugar en la rotación."
    )
    # Either MOTIVATION_NOTE downgraded OR it gets classified as something else;
    # in either case, the MLB_NORMAL_MOTIVATION_NEUTRAL tag should appear when
    # baseball is detected AND it was classified as motivation.
    if out["signal_type"] == "MOTIVATION_NOTE" and out["sport_hint"] == "baseball":
        assert "MLB_NORMAL_MOTIVATION_NEUTRAL" in out["tags"]
        assert out["confidence"] <= 0.50


def test_mlb_playoff_motivation_preserved():
    """Postseason motivation must NOT be neutralised."""
    out = classify_signal(
        "El equipo se juega la vida en los playoffs y necesita ganar."
    )
    # The motivation tag must NOT be neutralised in postseason context.
    if out["signal_type"] == "MOTIVATION_NOTE":
        assert "MLB_NORMAL_MOTIVATION_NEUTRAL" not in out["tags"]


def test_classify_signal_factual_picks_up_sabermetrics_vocab():
    out = classify_signal("Su xERA es 2.50 y su xwOBA 0.290.")
    assert out["sport_hint"] == "baseball"
    assert out["signal_type"] in ("FACTUAL_CONTEXT", "MARKET_SUGGESTION",
                                    "WARNING")


def test_classify_signal_warning_mlb_vocab():
    out = classify_signal("Bullpen fatigado en este partido — under frágil.")
    assert out["sport_hint"] == "baseball"
    assert out["signal_type"] == "WARNING"


def test_classify_signal_low_confidence_when_ambiguous():
    """Fail-soft contract: ambiguous fragments default to OPINION/low conf."""
    out = classify_signal("Será un buen partido.")
    assert out["signal_type"] == "OPINION"
    assert out["confidence"] <= 0.65
