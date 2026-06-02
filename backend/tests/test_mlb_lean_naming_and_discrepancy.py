"""Regression tests for the camelCase naming bug + structured discrepancy_details.

Cubre los 2 fixes del usuario:
  FIX #1 — el override del lean_classifier escribe en
           ``baseballHistoricalProfile`` (camelCase) que es la key que el
           frontend realmente lee. Antes escribía en ``historical_profile``
           (snake_case) → el override se perdía y el panel seguía mostrando
           el lean heurístico legacy.
  FIX #2 — script_pick_mismatch_details ahora viaja con los números
           concretos (Script ofensivo, Offensive Explosion, Over Survival,
           Cover Probability vs Margen, Survival vs Fragility, Gap proyec.
           vs línea) para que el panel los renderice junto al ⚠.
"""
from __future__ import annotations

# We test the BUILDING logic directly without invoking the full orchestrator
# pipeline (which requires DB + API-Sports). Both fixes are localized blocks
# of code that we can reproduce in isolation.


# ───────────────────────────────────────────────────────────────────────
# FIX #1 — naming
# ───────────────────────────────────────────────────────────────────────
class TestLeanOverrideWritesCamelCase:
    """Reproduce el override del market_lean_classifier para garantizar que
    la key escrita es ``baseballHistoricalProfile`` (la que el panel lee)."""

    def _apply_override(self, pick_payload: dict, lean_payload: dict) -> dict:
        # Replica exacta del bloque corregido en mlb_day_orchestrator.py
        hist_block = pick_payload.get("baseballHistoricalProfile") or {}
        if hist_block:
            hist_block["overUnderLean"]            = lean_payload["lean"]
            hist_block["overUnderLeanConfidence"]  = lean_payload["confidence"]
            hist_block["overUnderLeanReason"]      = lean_payload["reason"]
            hist_block["overUnderLeanDisplay"]     = lean_payload["display_lean"]
            hist_block["overUnderLeanConsistency"] = lean_payload["consistency"]
            pick_payload["baseballHistoricalProfile"] = hist_block
        return pick_payload

    def test_camel_case_key_is_updated(self):
        pick = {
            "baseballHistoricalProfile": {
                "overUnderLean": "OVER",
                "overUnderLeanDisplay": "LEAN OVER CARRERAS",
                "overUnderLeanConfidence": "high",
                "overUnderLeanReason": "Heuristic projected 10.0 runs",
                "overUnderLeanConsistency": 0.7,
                "projection": 10.0,
            },
        }
        lean = {
            "lean": "UNDER",
            "display_lean": "LEAN UNDER",
            "confidence": "medium",
            "reason": "market_lean_classifier override",
            "consistency": 0.55,
        }
        out = self._apply_override(pick, lean)
        hist = out["baseballHistoricalProfile"]
        assert hist["overUnderLean"] == "UNDER"
        assert hist["overUnderLeanDisplay"] == "LEAN UNDER"
        assert hist["overUnderLeanConfidence"] == "medium"
        assert hist["overUnderLeanReason"] == "market_lean_classifier override"
        # Confirm el legacy snake_case NO se crea (solo escribimos camel).
        assert "historical_profile" not in out

    def test_legacy_snake_case_block_is_ignored(self):
        # Si por algún motivo viene un historical_profile snake_case
        # populado (legacy run), NO debe usarse: solo el camelCase.
        pick = {
            "historical_profile": {"overUnderLean": "OVER"},
            # camelCase está vacío → el override no debe ejecutarse
        }
        lean = {
            "lean": "UNDER", "display_lean": "LEAN UNDER", "confidence": "medium",
            "reason": "r", "consistency": 0.5,
        }
        out = self._apply_override(pick, lean)
        # Snake intacto (no se tocó).
        assert out["historical_profile"]["overUnderLean"] == "OVER"
        # Camel no se creó porque no había hist_block.
        assert "baseballHistoricalProfile" not in out

    def test_pick_without_historical_profile_unchanged(self):
        pick = {"match_id": "abc"}
        lean = {"lean": "OVER", "display_lean": "LEAN OVER", "confidence": "low",
                "reason": "x", "consistency": 0.4}
        out = self._apply_override(pick, lean)
        assert "baseballHistoricalProfile" not in out
        assert "historical_profile" not in out


# ───────────────────────────────────────────────────────────────────────
# FIX #2 — discrepancy_details
# ───────────────────────────────────────────────────────────────────────
class TestDiscrepancyDetails:
    """Verifica que cuando el script ofensivo dice 'altas carreras' pero el
    pick es Under, el orquestador adjunta una lista estructurada con los
    valores que justifican (o contradicen) la decisión."""

    def _build_details(self, *, osm: dict, v2b: dict, v5b: dict) -> list:
        """Replica MINIMA del bloque del orquestador (fix #4 / #2 del user)."""
        _details: list[dict] = []
        off_code = (osm.get("offensive_script") or {}).get("code")
        _details.append({
            "label":          "Script ofensivo",
            "value":          off_code,
            "interpretation": "Proyecta entorno de altas carreras",
            "severity":       "high",
        })
        _oe = (osm.get("offensive_explosion") or {}).get("score")
        if _oe is not None:
            _oe = float(_oe)
            _details.append({
                "label":     "Offensive Explosion",
                "value":     f"{_oe:.0f}/100",
                "interpretation": (
                    "Indicador de capacidad ofensiva por encima del promedio"
                    if _oe >= 55 else "Indicador ofensivo en rango medio"
                ),
                "severity":  "high" if _oe >= 70 else "medium",
            })
        _os = (osm.get("over_survival") or {}).get("score")
        if _os is not None:
            _os = float(_os)
            _details.append({
                "label":          "Over Survival",
                "value":          f"{_os:.0f}/100",
                "interpretation": (
                    "El Over sobrevive a la mayoría de escenarios"
                    if _os >= 70 else "El Over no domina los escenarios"
                ),
                "severity":       "high" if _os >= 70 else "medium",
            })
        cov   = v2b.get("coverProbability")
        margn = v2b.get("marginProjection")
        if cov is not None and margn is not None:
            _cov = float(cov)
            _cover_pct = _cov * 100.0 if _cov <= 1.0 else _cov
            _mgn = float(margn)
            _details.append({
                "label":          "Cover Probability vs Margen",
                "value":          f"{_cover_pct:.1f}% cover · margen {_mgn:+.2f}",
                "interpretation": (
                    "Cover alto pero margen pequeño — el Under cubre por poco"
                    if _cover_pct >= 75 and abs(_mgn) < 1.0
                    else "Cover y margen consistentes con la recomendación"
                ),
                "severity": "medium" if _cover_pct >= 75 and abs(_mgn) < 1.0 else "low",
            })
        sv = (v5b.get("survival") or {}).get("score")
        fr = (v5b.get("fragility") or {}).get("score")
        if sv is not None and fr is not None:
            sv, fr = int(sv), int(fr)
            _details.append({
                "label":          "Survival vs Fragility",
                "value":          f"Survival {sv}/100 · Fragility {fr}/100",
                "interpretation": (
                    "Script frágil — el Under es vulnerable a un inning explosivo"
                    if fr >= 50 or sv <= 55 else "Script estable"
                ),
                "severity":       "high" if fr >= 50 else "medium",
            })
        er = v2b.get("expectedRuns")
        line = v2b.get("smartTotalsLine") or v2b.get("recommendedLine")
        if er is not None and line is not None:
            er, line = float(er), float(line)
            gap = line - er
            _details.append({
                "label":          "Gap proyección ↔ línea",
                "value":          f"ER {er:.2f} vs línea {line:.1f} (gap {gap:+.2f})",
                "interpretation": (
                    "Gap holgado a favor del Under"
                    if gap >= 2.5 else "Gap ajustado — el Under tiene poco colchón"
                ),
                "severity":       "low" if gap >= 2.5 else "high",
            })
        return _details

    def test_full_chicago_white_sox_case(self):
        # Reproduce el caso de la captura del usuario:
        # Chicago White Sox @ Minnesota Twins
        # Script: ofensivo > promedio, Explosion 58/100, Over Surv 82/100,
        # Survival 52/100 Fragility 52/100, ER ~6.3 línea 9.5
        osm = {
            "offensive_script":    {"code": "ABOVE_AVERAGE_SCORING"},
            "offensive_explosion": {"score": 58},
            "over_survival":       {"score": 82},
        }
        v2b = {
            "coverProbability":  0.799,
            "marginProjection":  -0.46,
            "expectedRuns":      6.3,
            "smartTotalsLine":   9.5,
        }
        v5b = {
            "survival":  {"score": 52},
            "fragility": {"score": 52},
        }
        details = self._build_details(osm=osm, v2b=v2b, v5b=v5b)
        # 6 entries esperadas: script, explosion, survival, cover/margen,
        # survival/fragility, gap.
        assert len(details) == 6
        labels = [d["label"] for d in details]
        assert "Script ofensivo" in labels
        assert "Offensive Explosion" in labels
        assert "Over Survival" in labels
        assert "Cover Probability vs Margen" in labels
        assert "Survival vs Fragility" in labels
        assert "Gap proyección ↔ línea" in labels
        # Verifica que el cover% se renderice como %, no como 0.799
        cover_entry = next(d for d in details if d["label"] == "Cover Probability vs Margen")
        assert "79.9% cover" in cover_entry["value"]
        assert "-0.46" in cover_entry["value"]
        # Verifica que el gap sea +3.20 (9.5 - 6.3)
        gap_entry = next(d for d in details if d["label"] == "Gap proyección ↔ línea")
        assert "+3.20" in gap_entry["value"]
        # Gap holgado >= 2.5 → low severity
        assert gap_entry["severity"] == "low"
        # Survival 52 + Fragility 52 → high severity
        sf_entry = next(d for d in details if d["label"] == "Survival vs Fragility")
        assert sf_entry["severity"] == "high"

    def test_skips_missing_metrics(self):
        # Solo el "Script ofensivo" debe aparecer cuando todo lo demás es None.
        osm = {"offensive_script": {"code": "HIGH_SCORING"}}
        details = self._build_details(osm=osm, v2b={}, v5b={})
        assert len(details) == 1
        assert details[0]["label"] == "Script ofensivo"
        assert details[0]["value"] == "HIGH_SCORING"

    def test_cover_pct_normalization(self):
        # Soporta tanto 0-1 como 0-100 en coverProbability.
        osm = {"offensive_script": {"code": "OFFENSIVE_EXPLOSION"}}
        for cov, expected in [(0.85, "85.0%"), (85.0, "85.0%"), (0.5, "50.0%")]:
            details = self._build_details(
                osm=osm,
                v2b={"coverProbability": cov, "marginProjection": -0.3},
                v5b={},
            )
            cover_entry = next(d for d in details if d["label"] == "Cover Probability vs Margen")
            assert expected in cover_entry["value"], f"cov={cov}: got {cover_entry['value']!r}"

    def test_low_cover_tight_margin_flags_medium(self):
        # Cover >=75% + |margen|<1 → severity medium (mensaje de "cubre por poco").
        osm = {"offensive_script": {"code": "HIGH_SCORING"}}
        details = self._build_details(
            osm=osm,
            v2b={"coverProbability": 0.79, "marginProjection": -0.46},
            v5b={},
        )
        cover = next(d for d in details if d["label"] == "Cover Probability vs Margen")
        assert cover["severity"] == "medium"
        assert "cubre por poco" in cover["interpretation"]

    def test_high_fragility_flags_high_severity(self):
        # Fragility >= 50 → severity high + warning de inning explosivo.
        osm = {"offensive_script": {"code": "ABOVE_AVERAGE_SCORING"}}
        details = self._build_details(
            osm=osm,
            v2b={},
            v5b={"survival": {"score": 52}, "fragility": {"score": 55}},
        )
        sf = next(d for d in details if d["label"] == "Survival vs Fragility")
        assert sf["severity"] == "high"
        assert "inning explosivo" in sf["interpretation"].lower()
