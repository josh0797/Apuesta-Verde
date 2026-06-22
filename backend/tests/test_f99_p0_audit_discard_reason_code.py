"""
test_f99_p0_audit_discard_reason_code
=====================================

Fase 6 de la **Auditoría de Drift de Producción (P0)**.

Reglas validadas:

- ``build_discarded_header`` **nunca** produce la cadena
  ``"descartado por unknown"`` aunque ``rejection_code`` sea ``"UNKNOWN"``,
  ``None``, ``""``, etc.
- El código catch-all es ``UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`` (auditable,
  rastreable) y produce el tag legible
  ``"motivo no clasificado (revisión pendiente)"``.
- Los códigos conocidos del catálogo siguen produciendo sus tags
  específicos (regresión).
"""

from __future__ import annotations

import logging

import pytest

from services.football_market_trace import build_discarded_header


# ── Casos donde antes salía "unknown" ───────────────────────────────────────


@pytest.mark.parametrize(
    "raw_code",
    ["UNKNOWN", "unknown", "Unknown", None, "", "None", "null", "  "],
)
def test_unknown_like_rejection_codes_never_emit_unknown(raw_code):
    header = build_discarded_header({
        "selection": "Watchlist",
        "market": "Doble Oportunidad",
        "rejection_code": raw_code,
    })
    assert "descartado por" in header
    assert "unknown" not in header.lower(), (
        f"Header siguió emitiendo 'unknown' para raw_code={raw_code!r}: {header!r}"
    )
    # Debe llevar el tag traducido del catch-all
    assert "motivo no clasificado" in header.lower()


def test_unclassified_code_emits_audit_log(caplog):
    """Debe emitir un log WARNING con contexto para que el usuario lo rastree."""
    with caplog.at_level(logging.WARNING, logger="football_market_trace"):
        build_discarded_header({
            "selection": "PSG",
            "market": "Over 2.5",
            "rejection_code": "UNKNOWN",
            "edge_pct": -3.1,
            "confidence": 41,
        })

    # Al menos un registro WARNING con la clave UNCLASSIFIED_DISCARD.
    matching = [r for r in caplog.records if "UNCLASSIFIED_DISCARD" in r.getMessage()]
    assert matching, "Esperaba un WARNING con 'UNCLASSIFIED_DISCARD' en el contexto."


def test_explicit_unclassified_code_is_supported():
    """El código ``UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`` es first-class."""
    header = build_discarded_header({
        "selection": "PSG",
        "market": "Doble Oportunidad",
        "rejection_code": "UNCLASSIFIED_DISCARD_REQUIRES_AUDIT",
    })
    assert "motivo no clasificado" in header.lower()
    assert "unknown" not in header.lower()


# ── Regresiones — códigos conocidos siguen funcionando ──────────────────────


def test_edge_below_min_still_works():
    header = build_discarded_header({
        "selection": "Madrid",
        "market": "Moneyline",
        "rejection_code": "EDGE_BELOW_MIN",
        "edge_pct": -8.5,
    })
    assert "edge insuficiente" in header.lower()
    assert "-8.5" in header


def test_fragility_high_still_works():
    header = build_discarded_header({
        "selection": "PSG",
        "market": "Over 2.5",
        "rejection_code": "FRAGILITY_TOO_HIGH",
        "fragility": 78,
    })
    assert "fragilidad elevada" in header.lower()
    assert "78" in header


def test_low_odds_no_cushion_still_works():
    header = build_discarded_header({
        "selection": "Bayern",
        "market": "1X",
        "rejection_code": "LOW_ODDS_NO_CUSHION",
        "odds": 1.18,
    })
    assert "cuota baja" in header.lower()


def test_watchlist_only_still_works():
    header = build_discarded_header({
        "selection": "Watchlist",
        "market": "BTTS",
        "rejection_code": "WATCHLIST_ONLY",
        "confidence": 55,
    })
    assert "confianza insuficiente" in header.lower()


def test_market_trap_still_works():
    header = build_discarded_header({
        "selection": "Liverpool",
        "market": "Over 1.5",
        "rejection_code": "MARKET_TRAP",
    })
    assert "trampa" in header.lower()


def test_unrecognized_but_specific_code_does_not_emit_unknown():
    """
    Si un código nuevo aparece (p.ej. 'NOVEL_GUARD_REJECTION') que no tiene
    tag dedicado pero **NO** está vacío, igual NO debe emitir 'unknown'.
    """
    header = build_discarded_header({
        "selection": "Real",
        "market": "Handicap",
        "rejection_code": "NOVEL_GUARD_REJECTION",
    })
    assert "unknown" not in header.lower()
    assert "novel guard rejection" in header.lower()


# ── Garantía de "Watchlist descartado por unknown" eliminado ────────────────


def test_legacy_watchlist_unknown_path_is_dead():
    """
    El caso reportado por el usuario en Producción
    (``"Watchlist descartado por unknown"``) ya no es alcanzable a través
    del helper canónico.
    """
    header = build_discarded_header({
        "selection": "Watchlist",
        "market": None,  # noqa: PIE804 — forzar el path sin market
        "rejection_code": "UNKNOWN",
    })
    assert header.startswith("Watchlist descartado por")
    assert "unknown" not in header.lower()
    assert "motivo no clasificado" in header.lower()
