"""Phase F67 — Head-to-Head ingestor smoke tests (pure functional)."""
from __future__ import annotations

from services.head_to_head_ingestor import _norm, _pair_key


def test_norm_strips_suffixes_and_lowercases() -> None:
    assert _norm("Real Madrid CF") == "real madrid"
    assert _norm("FC Barcelona")   == "barcelona"


def test_pair_key_is_symmetric() -> None:
    """The collision protocol — Brazil-Morocco and Morocco-Brazil
    MUST yield the same key so a flipped lookup hits the same row."""
    assert _pair_key("Brazil",  "Morocco") == _pair_key("Morocco", "Brazil")
    assert _pair_key("Real Madrid CF", "FC Barcelona") \
        == _pair_key("Barcelona", "Real Madrid")


def test_pair_key_distinguishes_different_pairs() -> None:
    assert _pair_key("Brazil", "Morocco") != _pair_key("Brazil", "France")
