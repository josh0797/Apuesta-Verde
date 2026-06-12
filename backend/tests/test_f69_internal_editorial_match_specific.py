"""Phase F69 — Internal editorial must be match-specific.

Mandatory acceptance tests (8):
  1. Qatar vs Switzerland y Brazil vs Morocco no deben generar exactamente
     el mismo editorial.
  2. El texto final no debe contener "Home" ni "Away" como placeholders.
  3. Si data_quality = THIN, no debe generar marcador probable "1-1".
  4. Si no hay datos suficientes, debe mostrar
     "No disponible con suficiente confianza".
  5. Cache / lookup por match_id (engine es idempotente por payload).
  6. Partido descartado por market_trap debe mencionar cuota,
     probabilidad implícita, probabilidad estimada y edge.
  7. Si dos editoriales son casi idénticos, activar
     ``INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED``.
  8. Scores24 no encontrado no debe forzar análisis editorial completo
     (THIN se respeta).
"""
from __future__ import annotations

import re

import pytest

from services.football_editorial_prediction import (
    detect_duplicate_internal_editorials,
    generate_football_editorial_prediction,
)


def _build_thin_entry(label: str, mid: int, odds: float = 1.25,
                      prob_est: float = 0.637, prob_imp: float = 0.80,
                      edge: float = -0.163,
                      reason: str = "Señales de trampa detectadas; el mercado parece engañar.",
                      ) -> dict:
    return {
        "match_label":           label,
        "match_id":              mid,
        "odds":                  odds,
        "estimated_probability": prob_est,
        "implied_probability":   prob_imp,
        "edge":                  edge,
        "fragility_score":       12,
        "reason":                reason,
        "market_evaluated":      "1X2 - moneyline",
    }


# ─────────────────────────────────────────────────────────────────────
# T1 — Two different matches must NOT produce identical editorials.
# ─────────────────────────────────────────────────────────────────────
def test_t1_two_matches_not_identical():
    qat = _build_thin_entry("Qatar vs Switzerland", 1)
    bra = _build_thin_entry("Brazil vs Morocco", 2, odds=1.30, prob_est=0.62,
                            prob_imp=0.77, edge=-0.15, reason="Mercado frágil")
    e1 = generate_football_editorial_prediction(qat)
    e2 = generate_football_editorial_prediction(bra)

    # The discard_reason_narrative MUST differ (different teams, cuota, edge).
    n1 = (e1["editorial_sections"]["discard_reason_narrative"] or {}).get("text", "")
    n2 = (e2["editorial_sections"]["discard_reason_narrative"] or {}).get("text", "")
    assert n1, "Qatar match must produce a discard narrative"
    assert n2, "Brazil match must produce a discard narrative"
    assert n1 != n2, "Editorials must be match-specific (different text)"
    assert "Qatar" in n1 and "Switzerland" in n1
    assert "Brazil" in n2 and "Morocco" in n2


# ─────────────────────────────────────────────────────────────────────
# T2 — Output must not contain "Home" or "Away" placeholders.
# ─────────────────────────────────────────────────────────────────────
def test_t2_no_home_away_placeholders():
    entry = _build_thin_entry("Qatar vs Switzerland", 99)
    ed = generate_football_editorial_prediction(entry)

    def _collect_text(d):
        out = []
        if isinstance(d, dict):
            for v in d.values():
                out.extend(_collect_text(v))
        elif isinstance(d, list):
            for it in d:
                out.extend(_collect_text(it))
        elif isinstance(d, str):
            out.append(d)
        return out

    all_text = " ".join(_collect_text(ed))
    # Match standalone Home / Away words (not e.g. "homemade").
    assert not re.search(r"\bHome\b", all_text), \
        f"'Home' placeholder leaked: {all_text[:300]}"
    assert not re.search(r"\bAway\b", all_text), \
        f"'Away' placeholder leaked: {all_text[:300]}"


# ─────────────────────────────────────────────────────────────────────
# T3 — THIN data_quality must NOT emit "1-1" probable_score.
# ─────────────────────────────────────────────────────────────────────
def test_t3_thin_does_not_emit_fake_scoreline():
    entry = _build_thin_entry("Qatar vs Switzerland", 3)
    ed = generate_football_editorial_prediction(entry)
    assert ed["data_quality"] == "THIN"
    score_sec = ed["editorial_sections"]["probable_score"]
    assert score_sec["available"] is False
    assert score_sec.get("score") is None
    assert "1-1" not in (score_sec.get("text") or "")


# ─────────────────────────────────────────────────────────────────────
# T4 — Honest "No disponible con suficiente confianza" when data is thin.
# ─────────────────────────────────────────────────────────────────────
def test_t4_honest_unavailable_text():
    entry = _build_thin_entry("Qatar vs Switzerland", 4)
    ed = generate_football_editorial_prediction(entry)
    score_sec = ed["editorial_sections"]["probable_score"]
    assert "No disponible con suficiente confianza" in (score_sec.get("text") or "")


# ─────────────────────────────────────────────────────────────────────
# T5 — Engine is idempotent / match-specific by payload (no shared cache).
# ─────────────────────────────────────────────────────────────────────
def test_t5_engine_idempotent_per_match():
    qat1 = _build_thin_entry("Qatar vs Switzerland", 7)
    qat2 = _build_thin_entry("Qatar vs Switzerland", 7)
    ed1  = generate_football_editorial_prediction(qat1)
    ed2  = generate_football_editorial_prediction(qat2)
    # Identical payload → identical engine output.
    assert ed1["editorial_sections"]["discard_reason_narrative"]["text"] == \
           ed2["editorial_sections"]["discard_reason_narrative"]["text"]

    # Different match_id → different (non-shared) outputs allowed.
    bra = _build_thin_entry("Brazil vs Morocco", 8, odds=1.5, edge=-0.05,
                            reason="Mercado frágil")
    ed_bra = generate_football_editorial_prediction(bra)
    assert (ed_bra["editorial_sections"]["discard_reason_narrative"]["text"] !=
            ed1["editorial_sections"]["discard_reason_narrative"]["text"])


# ─────────────────────────────────────────────────────────────────────
# T6 — market_trap discard must cite cuota / prob impl / prob est / edge.
# ─────────────────────────────────────────────────────────────────────
def test_t6_market_trap_cites_full_numbers():
    entry = _build_thin_entry("Qatar vs Switzerland", 6,
                              odds=1.25, prob_est=0.637, prob_imp=0.80,
                              edge=-0.163)
    ed = generate_football_editorial_prediction(entry)
    narr = (ed["editorial_sections"]["discard_reason_narrative"] or {}).get("text", "")
    assert "1.25" in narr, f"Expected odds 1.25 in narrative: {narr}"
    # Probability rendered as either 80.0% or 0.8% (engine handles both).
    assert ("80.0%" in narr) or ("80%" in narr) or ("0.80" in narr), \
        f"Expected implied probability in narrative: {narr}"
    assert ("63.7%" in narr) or ("63" in narr), \
        f"Expected estimated probability in narrative: {narr}"
    assert ("-16.3%" in narr) or ("-16%" in narr) or ("Edge" in narr), \
        f"Expected edge in narrative: {narr}"
    codes = ed["reason_codes"]
    assert "MARKET_TRAP_NARRATIVE_INJECTED" in codes
    assert "DISCARD_REASON_NARRATIVE_GENERATED" in codes


# ─────────────────────────────────────────────────────────────────────
# T7 — Two near-identical editorials trigger duplicate detection.
# ─────────────────────────────────────────────────────────────────────
def test_t7_duplicate_template_detection():
    qat = _build_thin_entry("Qatar vs Switzerland", 10)
    bra = _build_thin_entry("Brazil vs Morocco", 11)
    # Same reason / odds / edge → narratives nearly identical (only team
    # names + numbers vary). The detector strips team names and digits.
    e1 = generate_football_editorial_prediction(qat)
    e2 = generate_football_editorial_prediction(bra)
    summary = {"discarded_market": [
        {"match_id": 10, "editorial_prediction": e1},
        {"match_id": 11, "editorial_prediction": e2},
    ]}
    flagged = detect_duplicate_internal_editorials(summary)
    assert flagged == 2
    assert e1["internal_editorial_analysis"]["is_generic_fallback"] is True
    assert e2["internal_editorial_analysis"]["is_generic_fallback"] is True
    audit_codes = e1["internal_editorial_analysis"].get("reason_codes") or []
    assert "INTERNAL_EDITORIAL_DUPLICATE_TEMPLATE_DETECTED" in audit_codes


# ─────────────────────────────────────────────────────────────────────
# T8 — Sportytrader / Scores24 missing must NOT trigger a forced full
#       editorial. THIN classification persists; sections stay honest.
# ─────────────────────────────────────────────────────────────────────
def test_t8_scores24_missing_does_not_force_full_editorial():
    # Simulate the exact shape produced by the orchestrator when the
    # external review fails (URL not resolved / fetch failed) — we just
    # have a discard entry with no stats and a "Scores24 not found"
    # context.
    entry = _build_thin_entry("Qatar vs Switzerland", 8)
    entry["scores24_review"] = {
        "available":    False,
        "decision":     None,
        "reason_codes": ["SCORES24_URL_NOT_RESOLVED"],
    }
    ed = generate_football_editorial_prediction(entry)
    # data_quality must remain THIN — Scores24 "not found" doesn't add
    # any new data source.
    assert ed["data_quality"] == "THIN"
    # All sections (except discard_reason_narrative) must be unavailable.
    for k in ("corners_prediction", "goals_prediction", "probable_score"):
        assert ed["editorial_sections"][k]["available"] is False, \
            f"section {k} should be unavailable for THIN data"
