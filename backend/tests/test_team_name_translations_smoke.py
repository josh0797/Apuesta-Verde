"""Phase F63 — team_name_translations smoke tests.

Validates the curated EN ↔ ES dictionary and the slug-variant helpers.
Coverage focused on the explicit cases the user listed in the spec.
"""
from __future__ import annotations

import pytest

from services.team_name_translations import (
    ENGINE_VERSION,
    has_translation,
    normalize_team_name_for_scores24,
    slug_pairs,
)


# ─────────────────────────────────────────────────────────────────────
# normalize_team_name_for_scores24 — curated cases from the spec
# ─────────────────────────────────────────────────────────────────────
def test_mexico_emits_es_variants():
    variants = normalize_team_name_for_scores24("Mexico")
    assert variants  # non-empty
    assert "mexico" in variants
    # Accented variant present.
    assert "méxico" in variants


def test_south_africa_emits_sudafrica_variants():
    variants = normalize_team_name_for_scores24("South Africa")
    assert "south-africa" in variants
    assert "sudafrica" in variants
    # Accented variant present.
    assert "sudáfrica" in variants


def test_brazil_emits_brasil_variant():
    variants = normalize_team_name_for_scores24("Brazil")
    assert "brazil" in variants
    assert "brasil" in variants


def test_morocco_emits_marruecos_variant():
    variants = normalize_team_name_for_scores24("Morocco")
    assert "morocco" in variants
    assert "marruecos" in variants


def test_united_states_aliases():
    """API may send 'USA' or 'United States' — both must resolve."""
    for raw in ("USA", "United States"):
        variants = normalize_team_name_for_scores24(raw)
        # ASCII USA must always appear.
        assert "usa" in variants
        # ES translation also present.
        assert "estados-unidos" in variants
        # English long form.
        assert "united-states" in variants


def test_bosnia_herzegovina_handles_ampersand():
    """User listed `Bosnia & Herzegovina` — must NOT have a stray '&' in the slug
    and must include the ES variant."""
    variants = normalize_team_name_for_scores24("Bosnia & Herzegovina")
    # No raw ampersand survives.
    for v in variants:
        assert "&" not in v
    assert "bosnia-herzegovina" in variants
    assert "bosnia-y-herzegovina" in variants


def test_qatar_emits_catar():
    variants = normalize_team_name_for_scores24("Qatar")
    assert "qatar" in variants
    assert "catar" in variants


def test_switzerland_emits_suiza():
    variants = normalize_team_name_for_scores24("Switzerland")
    assert "switzerland" in variants
    assert "suiza" in variants


def test_south_korea_emits_corea_del_sur():
    variants = normalize_team_name_for_scores24("South Korea")
    assert "south-korea" in variants
    assert "corea-del-sur" in variants


def test_czech_republic_emits_es_variants():
    variants = normalize_team_name_for_scores24("Czech Republic")
    assert "czech-republic" in variants
    assert "republica-checa" in variants
    assert "república-checa" in variants


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────
def test_empty_input_returns_empty():
    assert normalize_team_name_for_scores24(None) == []
    assert normalize_team_name_for_scores24("") == []
    assert normalize_team_name_for_scores24("   ") == []


def test_unknown_team_still_emits_ascii_slug():
    """Teams NOT in the dictionary should still get the ASCII-folded slug."""
    v = normalize_team_name_for_scores24("Club Atlético Tigre")
    assert "club-atletico-tigre" in v


def test_accent_only_input_is_handled():
    v = normalize_team_name_for_scores24("São Paulo FC")
    assert "sao-paulo-fc" in v


def test_no_duplicates_in_output():
    """The helper must de-dupe variants — ordering preserved."""
    v = normalize_team_name_for_scores24("Paraguay")
    assert v == list(dict.fromkeys(v))


def test_has_translation_true_for_curated_teams():
    assert has_translation("Mexico") is True
    assert has_translation("Bosnia & Herzegovina") is True
    assert has_translation("South Korea") is True


def test_has_translation_false_for_clubs():
    """Club teams aren't in the curated dictionary."""
    assert has_translation("Real Madrid") is False
    assert has_translation("Boca Juniors") is False
    assert has_translation(None) is False
    assert has_translation("") is False


# ─────────────────────────────────────────────────────────────────────
# slug_pairs — Cartesian-product helper
# ─────────────────────────────────────────────────────────────────────
def test_slug_pairs_mexico_south_africa():
    pairs = slug_pairs("Mexico", "South Africa", max_pairs=4)
    assert pairs
    # Diagonal: EN first.
    assert pairs[0] == ("mexico", "south-africa")
    # ES variant must appear somewhere.
    joined = " ".join(f"{h}--{a}" for h, a in pairs)
    assert "sudafrica" in joined


def test_slug_pairs_respects_max_pairs():
    pairs = slug_pairs("Brazil", "Morocco", max_pairs=3)
    assert len(pairs) <= 3


def test_slug_pairs_handles_missing_input():
    assert slug_pairs(None, "Brazil") == []
    assert slug_pairs("Brazil", None) == []
    assert slug_pairs("", "") == []


def test_engine_version_stable():
    assert ENGINE_VERSION == "team_name_translations.v1"
