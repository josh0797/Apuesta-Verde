"""Football Totals Model Normalizer (pure).

Produces the canonical ``football_totals_model`` block consumed by the
``FootballTotalsModelPanel`` UI. Reads DC/NB telemetry from
``compute_match_features`` output (already attached to the match doc by
the statsbomb pipeline) + the calibration mode from
``pipeline_meta.football_totals_calibration``.

Design principles:
  * Pure / fail-soft. Missing inputs → ``{available: False}``.
  * Read-only: never recomputes the probabilities. Just normalises.
  * Football-only. The orchestrator MUST gate by ``sport == 'football'``
    before calling this; if invoked on MLB/Basketball it still
    returns ``available:False`` because the inputs won't be there.

Mode semantics (per user spec):
  * ``unavailable`` — statsbomb features missing or pick is not football.
  * ``empirical``   — calibration summary marks ``global_applies: True``.
  * ``defaults``    — anything else (including n<100).
"""
from __future__ import annotations

from typing import Any

TOTALS_MODEL_SOURCE = "statsbomb_dc_nb_v1"


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _pick_features(pick: dict, match: dict | None) -> dict:
    """Locate the statsbomb features dict on either the pick payload or
    the source match doc."""
    for src in (pick, match or {}):
        if not isinstance(src, dict):
            continue
        for key in ("match_features", "_statsbomb_features"):
            v = src.get(key)
            if isinstance(v, dict) and v.get("p_under_2_5") is not None:
                return v
    return {}


def _resolve_mode(calibration_summary: dict | None) -> tuple[str, int]:
    """Return (mode, sample_size)."""
    if not isinstance(calibration_summary, dict):
        return "defaults", 0
    if not calibration_summary.get("available"):
        return "unavailable", int(calibration_summary.get("sample_size") or 0)
    if calibration_summary.get("global_applies"):
        return "empirical", int(calibration_summary.get("sample_size") or 0)
    return "defaults", int(calibration_summary.get("sample_size") or 0)


def build_football_totals_model(
    pick: dict,
    *,
    match: dict | None = None,
    calibration_summary: dict | None = None,
    league_tier: str | None = None,
    offense_bucket: str | None = None,
) -> dict:
    """Build the canonical ``football_totals_model`` block.

    Returns a dict with the shape::

        {
          "available":              bool,
          "mode":                   "defaults"|"empirical"|"unavailable",
          "rho_used":               float,
          "goals_dispersion_ratio": float,
          "league_tier":            str|None,
          "offense_bucket":         str|None,
          "sample_size":            int,
          "under_2_5":              {"poisson": .., "dc_nb": .., "delta_pts": ..},
          "under_3_5":              {"poisson": .., "dc_nb": .., "delta_pts": ..},
          "totals_model_source":    "statsbomb_dc_nb_v1",
          "reason_codes":           list[str],
          "summary":                str,
        }

    NEVER raises. Missing data → ``available=False``.
    """
    feats = _pick_features(pick, match)
    p_u25_dc_nb = _f(feats.get("p_under_2_5"))
    p_u35_dc_nb = _f(feats.get("p_under_3_5"))
    p_u25_pois  = _f(feats.get("p_under_2_5_poisson"))
    p_u35_pois  = _f(feats.get("p_under_3_5_poisson"))

    has_dc_nb = p_u25_dc_nb is not None and p_u35_dc_nb is not None
    if not has_dc_nb:
        return {
            "available":           False,
            "mode":                "unavailable",
            "reason":              "missing_statsbomb_features",
            "totals_model_source": TOTALS_MODEL_SOURCE,
        }

    mode, sample_size = _resolve_mode(calibration_summary)
    rho = _f(feats.get("dc_rho_used"))
    ratio = _f(feats.get("goals_dispersion_ratio"))

    # Delta pts (already stored by compute_match_features but recompute
    # defensively when missing).
    delta_25 = _f(feats.get("dc_nb_delta_2_5_pts"))
    delta_35 = _f(feats.get("dc_nb_delta_3_5_pts"))
    if delta_25 is None and p_u25_pois is not None:
        delta_25 = round((p_u25_dc_nb - p_u25_pois) * 100, 1)
    if delta_35 is None and p_u35_pois is not None:
        delta_35 = round((p_u35_dc_nb - p_u35_pois) * 100, 1)

    reasons: list[str] = []
    if mode == "empirical":
        reasons.append("EMPIRICAL_CALIBRATION")
    elif mode == "defaults":
        reasons.append("DEFAULT_CALIBRATION")
    if ratio is not None and ratio > 1.0001:
        reasons.append("NB_ACTIVE")
    else:
        reasons.append("NB_INERT")
    if rho is not None and rho < 0.0:
        reasons.append("DC_ACTIVE")

    summary = _build_summary(
        mode=mode, delta_25=delta_25, delta_35=delta_35,
        rho=rho, ratio=ratio, sample_size=sample_size,
    )

    return {
        "available":              True,
        "mode":                   mode,
        "rho_used":               rho,
        "goals_dispersion_ratio": ratio,
        "league_tier":            league_tier or "UNKNOWN_LEAGUE",
        "offense_bucket":         offense_bucket or "MODERATE_OFFENSE",
        "sample_size":            sample_size,
        "under_2_5": {
            "poisson":   p_u25_pois,
            "dc_nb":     p_u25_dc_nb,
            "delta_pts": delta_25,
        },
        "under_3_5": {
            "poisson":   p_u35_pois,
            "dc_nb":     p_u35_dc_nb,
            "delta_pts": delta_35,
        },
        "totals_model_source":    TOTALS_MODEL_SOURCE,
        "reason_codes":           reasons,
        "summary":                summary,
    }


def _build_summary(*, mode, delta_25, delta_35, rho, ratio, sample_size) -> str:
    parts: list[str] = []
    if mode == "empirical":
        parts.append(f"Calibración empírica activa (n={sample_size}).")
    elif mode == "defaults":
        parts.append(
            f"Calibración por defecto (n={sample_size}<100): rho=-0.05, NB ratio=1.0."
        )
    else:
        parts.append("Calibración no disponible.")
    if rho is not None:
        parts.append(f"rho usado: {rho:+.3f}.")
    if ratio is not None:
        parts.append(f"NB ratio: {ratio:.2f}.")
    if delta_35 is not None:
        sign = "+" if delta_35 >= 0 else ""
        parts.append(f"Delta Under 3.5 vs Poisson: {sign}{delta_35:.1f} pts.")
    return " ".join(parts)


__all__ = [
    "build_football_totals_model",
    "TOTALS_MODEL_SOURCE",
]
