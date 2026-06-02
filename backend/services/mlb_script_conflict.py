"""
MLB Final Pick — Script Conflict Detector
==========================================

Detect when the chosen recommended market disagrees with the deep
historical / script reading. Surfaces an explicit conflict object that
the final pick router can use to:

  * downgrade confidence (or block the pick outright if severity=high)
  * render a visible warning in the UI

Three conflict patterns are detected today:

  1. **Direction conflict** — pick says Under but the deep script labels
     the environment as "Lean Over" (or vice versa).
  2. **Projected runs vs line conflict** — pick is Under N.5 but the
     historical projection ≥ N.5 → the engine is contradicting itself.
  3. **F5 vs full-game directional conflict** — pick is full-game Under
     but F5 projection says Over (rarely fatal, severity=medium).

All detectors are pure functions, side-effect free, and never raise.
"""
from __future__ import annotations

from typing import Any, Optional


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _market_text(chosen_market: Optional[dict]) -> str:
    if not chosen_market:
        return ""
    return str(
        chosen_market.get("market")
        or chosen_market.get("selection")
        or chosen_market.get("recommended_market")
        or ""
    ).lower()


def _lean_text(deep_script: Optional[dict]) -> str:
    if not deep_script:
        return ""
    return str(
        deep_script.get("lean")
        or deep_script.get("overUnderLean")
        or deep_script.get("state")
        or deep_script.get("label")
        or ""
    ).lower()


def _line_from_market(chosen_market: Optional[dict]) -> Optional[float]:
    if not chosen_market:
        return None
    candidates = [
        chosen_market.get("recommended_line"),
        chosen_market.get("line"),
        chosen_market.get("smartTotalsLine"),
    ]
    # Try to extract from market text like "Under 8.5"
    txt = _market_text(chosen_market)
    if txt:
        import re
        m = re.search(r"(\d+(?:\.\d+)?)", txt)
        if m:
            candidates.append(m.group(1))
    for c in candidates:
        v = _safe_float(c)
        if v is not None:
            return v
    return None


def _projected_runs(deep_script: Optional[dict]) -> Optional[float]:
    if not deep_script:
        return None
    return _safe_float(
        deep_script.get("projected_runs")
        or deep_script.get("projection")
        or deep_script.get("projectedRuns")
        or deep_script.get("expectedRuns")
        or deep_script.get("projected_total_runs")
    )


def _f5_projected(deep_script: Optional[dict]) -> Optional[float]:
    if not deep_script:
        return None
    return _safe_float(
        deep_script.get("f5_projected_runs")
        or deep_script.get("f5_projection")
        or deep_script.get("f5ProjectedRuns")
    )


def detect_total_script_conflict(
    chosen_market: Optional[dict],
    deep_script: Optional[dict],
) -> dict:
    """Return ``{"has_conflict": bool, ...}``.

    Never raises. When inputs are missing the function returns a clean
    no-conflict object — callers don't need a try/except wrapper.

    Severity ladder:
        * ``high``   — directional contradiction or line-vs-projection
                       contradiction (the router should NOT confirm the
                       pick without manual review).
        * ``medium`` — secondary contradictions (F5 disagrees with FG,
                       or proyección is close to line within ±0.5).
        * not present when no conflict.
    """
    market_text = _market_text(chosen_market)
    lean_text   = _lean_text(deep_script)
    projected   = _projected_runs(deep_script)
    line        = _line_from_market(chosen_market)
    f5          = _f5_projected(deep_script)

    # 1) Direct directional conflict — over vs under literal mismatch.
    if "under" in market_text and "over" in lean_text:
        return {
            "has_conflict": True,
            "code":         "UNDER_PICK_CONFLICTS_WITH_OVER_SCRIPT",
            "severity":     "high",
            "message":      "El mercado final Under contradice el script profundo Lean Over.",
            "details":      {
                "market_text": market_text,
                "lean_text":   lean_text,
            },
        }
    if "over" in market_text and "under" in lean_text:
        return {
            "has_conflict": True,
            "code":         "OVER_PICK_CONFLICTS_WITH_UNDER_SCRIPT",
            "severity":     "high",
            "message":      "El mercado final Over contradice el script profundo Lean Under.",
            "details":      {
                "market_text": market_text,
                "lean_text":   lean_text,
            },
        }

    # 2) Under chosen but projection ≥ line — engine is self-contradictory.
    if "under" in market_text and projected is not None and line is not None:
        if projected >= line:
            return {
                "has_conflict": True,
                "code":         "UNDER_BELOW_PROJECTED_RUNS",
                "severity":     "high",
                "message":      (
                    f"La proyección de carreras ({projected:.1f}) está "
                    f"≥ a la línea Under seleccionada ({line:.1f})."
                ),
                "details":      {
                    "projected_runs": projected,
                    "selected_line":  line,
                    "gap":            round(projected - line, 2),
                },
            }
        # Soft warning if very close (within 0.5 runs).
        if (line - projected) < 0.5:
            return {
                "has_conflict": True,
                "code":         "UNDER_CLOSE_TO_PROJECTED_RUNS",
                "severity":     "medium",
                "message":      (
                    f"La proyección ({projected:.1f}) está a menos de 0.5 "
                    f"carreras de la línea Under {line:.1f} — colchón ajustado."
                ),
                "details":      {
                    "projected_runs": projected,
                    "selected_line":  line,
                    "gap":            round(projected - line, 2),
                },
            }

    # 3) Over chosen but projection ≤ line by a wide margin.
    if "over" in market_text and projected is not None and line is not None:
        if projected <= line and (line - projected) >= 1.0:
            return {
                "has_conflict": True,
                "code":         "OVER_ABOVE_PROJECTED_RUNS",
                "severity":     "high",
                "message":      (
                    f"La proyección de carreras ({projected:.1f}) está "
                    f"materialmente por debajo de la línea Over ({line:.1f})."
                ),
                "details":      {
                    "projected_runs": projected,
                    "selected_line":  line,
                    "gap":            round(projected - line, 2),
                },
            }

    # 4) F5 disagreement with full-game direction (medium severity).
    if f5 is not None:
        if "under" in market_text and ("f5_over" in lean_text or "f5 over" in str(deep_script or {}).lower()):
            return {
                "has_conflict": True,
                "code":         "F5_OVER_VS_FULLGAME_UNDER",
                "severity":     "medium",
                "message":      (
                    f"El script F5 proyecta {f5:.1f} carreras (Lean Over en F5) "
                    "pero el pick final es Under FG."
                ),
                "details":      {"f5_projected": f5},
            }

    return {"has_conflict": False, "code": None, "severity": None, "message": None}


# ── Manual odds helpers ────────────────────────────────────────────────────
def parse_manual_odds(raw: Any) -> Optional[float]:
    """Accept ``1.85``, ``"1.85"``, ``"1,85"`` (Spanish locale) and
    return a clean float. Returns None on invalid input.

    Hard guard: odds must be ≥ 1.01 (no negative/zero/sub-1 prices —
    those are common UI typos like "0.85").
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
    else:
        s = str(raw).strip().replace(",", ".")
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    if v < 1.01 or v > 1000.0:
        return None
    return round(v, 3)


def calculate_manual_edge(
    *,
    estimated_probability: Optional[float],
    manual_odds: float,
    value_threshold: float = 0.03,
    fair_threshold: float = -0.02,
) -> dict:
    """Given the engine's estimated probability and a manually entered
    odds value, compute the implied probability, edge, and value tier.

    Returns
    -------
    dict with::

        {
            "manual_odds":               float,
            "manual_implied_probability": float,
            "manual_edge":                float (estimated - implied),
            "manual_edge_pct":            float (× 100),
            "value_status":               "VALUE" | "FAIR_VALUE" | "NO_VALUE",
            "can_recommend":              bool,
            "rationale":                  str,
        }
    """
    if manual_odds is None or manual_odds < 1.01:
        return {
            "manual_odds":                None,
            "manual_implied_probability": None,
            "manual_edge":                None,
            "manual_edge_pct":            None,
            "value_status":               "INVALID",
            "can_recommend":              False,
            "rationale":                  "Cuota inválida (debe ser ≥ 1.01).",
        }

    implied = round(1.0 / manual_odds, 4)
    if estimated_probability is None:
        return {
            "manual_odds":                manual_odds,
            "manual_implied_probability": implied,
            "manual_edge":                None,
            "manual_edge_pct":            None,
            "value_status":               "UNKNOWN",
            "can_recommend":              False,
            "rationale":                  (
                "Cuota válida pero el engine no calculó probabilidad "
                "estimada — solo informativo."
            ),
        }

    est = float(estimated_probability)
    if est > 1.0:
        # Caller passed a 0-100 percentage; normalize.
        est = est / 100.0
    edge = round(est - implied, 4)
    edge_pct = round(edge * 100.0, 2)

    if edge >= value_threshold:
        status = "VALUE"
        can_rec = True
        rationale = (
            f"Edge +{edge_pct:.1f}% — probabilidad estimada "
            f"{est*100:.1f}% vs implícita {implied*100:.1f}%."
        )
    elif edge >= fair_threshold:
        status = "FAIR_VALUE"
        can_rec = False
        rationale = (
            f"Edge {edge_pct:+.1f}% — dentro del rango neutral. "
            "No hay valor claro a esta cuota."
        )
    else:
        status = "NO_VALUE"
        can_rec = False
        rationale = (
            f"Edge {edge_pct:+.1f}% — la cuota implica "
            f"{implied*100:.1f}% pero el engine estima solo "
            f"{est*100:.1f}%. NO apostar a este precio."
        )

    return {
        "manual_odds":                manual_odds,
        "manual_implied_probability": implied,
        "manual_edge":                edge,
        "manual_edge_pct":            edge_pct,
        "value_status":               status,
        "can_recommend":              can_rec,
        "rationale":                  rationale,
    }


__all__ = [
    "detect_total_script_conflict",
    "parse_manual_odds",
    "calculate_manual_edge",
]
