"""Sprint Corner-1 · paso 1 — Modelo de **Expected Corner Difference**.

Estima ``expected_corner_diff = E[home_corners - away_corners]`` usando
features prematch.

**Modelo lineal calibrado** (intercept + 6 drivers):

    expected_corner_diff =
        β0
      + β1 * (home_implied_prob - away_implied_prob)           # mercado h2h
      + β2 * (home_corners_for_L15  - away_corners_for_L15)    # L15 historia
      + β3 * (away_corners_against_L15 - home_corners_against_L15)
      + β4 * (away_deep_allowed_L15 - home_deep_allowed_L15)   # rico (Understat)
      + β5 * dominant_favorite_signal                          # ±1 si DOM_FAV, 0 si no
      + β6 * (home_venue_corner_split - away_venue_corner_split)  # filtrado por venue

Los coeficientes β se aprenden por OLS en
``corner_backtest._calibrate_diff_model``.  Mientras no haya
calibración, este módulo expone valores **default** razonables
construidos desde los hallazgos de Fase 1.5:

    - DOMINANT_FAVORITE → corner_diff_mean = +3.82 (favor del fav)
    - Sin favorito dominante → diff ~ 0 con σ ≈ 4

El output está **clamped a ±5.5** por regla operativa del brief.

Outputs:
    dict con:
      * expected_corner_diff   (float)
      * favored_corner_side    "HOME" | "AWAY" | "NONE"
      * confidence             0-100
      * drivers                list[{"name", "contribution", "raw_value"}]
      * missing_fields         list[str]
      * data_quality           "LOW" | "MEDIUM" | "HIGH"
      * reason_codes           list[str]
"""
from __future__ import annotations

from typing import Any, Optional

# Caps operativos del brief
CORNER_DIFF_MIN = -5.5
CORNER_DIFF_MAX = +5.5

# Threshold para identificar DOMINANT_FAVORITE (mismo del Sprint D8 / Fase 1.5)
DOMINANT_FAVORITE_THRESHOLD = 0.65

# Coeficientes default — calibrados aproximadamente desde los hallazgos
# de Fase 1.5 (n=851 dominant favorites → diff promedio +3.82).
# El módulo de backtest puede sobrescribirlos vía `coefficients` arg.
DEFAULT_COEFFICIENTS = {
    "intercept":                       0.0,
    "implied_prob_diff":               4.5,   # peso fuerte: mercado h2h capta la mayor parte
    "corners_for_diff_L15":            0.25,  # pequeño aporte
    "corners_against_diff_L15":        0.25,
    "deep_allowed_diff_L15":           0.005, # PPDA rich: pequeño pero direccional
    "dominant_favorite_signal":        1.5,   # boost extra cuando hay DOM_FAV
    "venue_corner_split_diff":         0.20,
}

# Pesos para confidence: cuántos drivers de calidad están presentes
CONFIDENCE_MAX_DRIVERS = 6


REASON_DOMINANT_FAV          = "DOMINANT_FAVORITE_CORNER_EDGE"
REASON_DEEP_ALLOWED          = "DEEP_ALLOWED_CORNER_EDGE"
REASON_L15_HISTORY           = "L15_CORNER_HISTORY_EDGE"
REASON_VENUE_SPLIT           = "VENUE_CORNER_SPLIT_USED"
REASON_SERIES_FAMILIARITY    = "SERIES_FAMILIARITY_CORNER_EDGE"
REASON_LOW_DATA_QUALITY      = "CORNER_DIFF_LOW_DATA_QUALITY"


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def compute_expected_corner_diff(
    context: dict[str, Any],
    coefficients: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Estima `expected_corner_diff` (home - away) desde features prematch.

    Args:
        context: dict con las features (todas opcionales, fail-soft).
        coefficients: opcional. Si no se pasa, usa DEFAULT_COEFFICIENTS.
                     Espera mismas keys que DEFAULT_COEFFICIENTS.

    Returns:
        dict con expected_corner_diff, favored_corner_side, confidence,
        drivers, missing_fields, data_quality, reason_codes.
    """
    coefs = dict(DEFAULT_COEFFICIENTS)
    if coefficients:
        coefs.update(coefficients)

    missing: list[str] = []
    reason_codes: list[str] = []
    drivers: list[dict] = []

    # -------- 1) implied_prob_diff (h2h market) --------
    iph = _safe_float(context.get("home_implied_prob"))
    ipa = _safe_float(context.get("away_implied_prob"))
    ip_diff = None
    if iph is not None and ipa is not None:
        ip_diff = iph - ipa
    else:
        missing.append("home_implied_prob/away_implied_prob")

    # Dominant favorite signal: +1 (home dom_fav), -1 (away), 0 (none)
    dom_side = context.get("dominant_favorite_side")  # "HOME" | "AWAY" | "NONE" | None
    dom_signal = 0
    if iph is not None and ipa is not None:
        max_p = max(iph, ipa)
        if max_p >= DOMINANT_FAVORITE_THRESHOLD:
            if iph > ipa:
                dom_signal = 1
                dom_side = "HOME"
            else:
                dom_signal = -1
                dom_side = "AWAY"
        else:
            dom_side = "NONE"
    elif dom_side in ("HOME", "AWAY"):
        # Fallback si el caller ya nos dice el lado pero faltan probs
        dom_signal = 1 if dom_side == "HOME" else -1

    # -------- 2) corners_for_diff L15 --------
    hcf15 = _safe_float(context.get("home_corners_for_L15"))
    acf15 = _safe_float(context.get("away_corners_for_L15"))
    cf_diff = None
    if hcf15 is not None and acf15 is not None:
        cf_diff = hcf15 - acf15
    else:
        missing.append("corners_for_L15")

    # -------- 3) corners_against_diff L15 (away allows more → more home corners) --------
    hca15 = _safe_float(context.get("home_corners_against_L15"))
    aca15 = _safe_float(context.get("away_corners_against_L15"))
    ca_diff = None
    if hca15 is not None and aca15 is not None:
        # positivo = away concede más que home → más corners al home
        ca_diff = aca15 - hca15
    else:
        missing.append("corners_against_L15")

    # -------- 4) deep_allowed_diff (Understat) --------
    hda15 = _safe_float(context.get("home_deep_allowed_L15"))
    ada15 = _safe_float(context.get("away_deep_allowed_L15"))
    da_diff = None
    if hda15 is not None and ada15 is not None:
        # positivo = away permite más pases profundos
        da_diff = ada15 - hda15

    # -------- 5) venue_corner_split (filtrado home en local / away en visitante) --------
    hv = _safe_float(context.get("home_venue_corner_split"))
    av = _safe_float(context.get("away_venue_corner_split"))
    vs_diff = None
    if hv is not None and av is not None:
        vs_diff = hv - av

    # -------- 6) series_familiarity_score (opcional) --------
    series = _safe_float(context.get("series_familiarity_score"))

    # ---- Calcular contribuciones ----
    edcd = coefs["intercept"]
    contrib_count = 0
    for name, value, coef_key in [
        ("implied_prob_diff",         ip_diff, "implied_prob_diff"),
        ("corners_for_diff_L15",      cf_diff, "corners_for_diff_L15"),
        ("corners_against_diff_L15",  ca_diff, "corners_against_diff_L15"),
        ("deep_allowed_diff_L15",     da_diff, "deep_allowed_diff_L15"),
        ("venue_corner_split_diff",   vs_diff, "venue_corner_split_diff"),
    ]:
        if value is None:
            continue
        contrib = coefs[coef_key] * value
        edcd += contrib
        drivers.append({
            "name":         name,
            "raw_value":    round(value, 4),
            "coefficient":  round(coefs[coef_key], 4),
            "contribution": round(contrib, 4),
        })
        contrib_count += 1

    # Dominant fav signal: añadir como driver propio
    if dom_signal != 0:
        contrib_dom = coefs["dominant_favorite_signal"] * dom_signal
        edcd += contrib_dom
        drivers.append({
            "name":         "dominant_favorite_signal",
            "raw_value":    dom_signal,
            "coefficient":  round(coefs["dominant_favorite_signal"], 4),
            "contribution": round(contrib_dom, 4),
        })
        contrib_count += 1
        reason_codes.append(REASON_DOMINANT_FAV)

    # Series familiarity (sin coef específico — multiplicador suave)
    if series is not None and abs(series) > 0.1:
        # Aplicar un boost del 10% del diff si series_familiarity está
        # alineado con la dirección del diff actual
        if (series > 0 and edcd > 0) or (series < 0 and edcd < 0):
            boost = edcd * 0.10 * min(1.0, abs(series))
            edcd += boost
            drivers.append({
                "name":         "series_familiarity",
                "raw_value":    round(series, 4),
                "coefficient":  0.10,
                "contribution": round(boost, 4),
            })
            reason_codes.append(REASON_SERIES_FAMILIARITY)

    # ---- Aplicar cap operativo ----
    edcd_capped = _clamp(edcd, CORNER_DIFF_MIN, CORNER_DIFF_MAX)

    # ---- Reason codes restantes ----
    if cf_diff is not None and abs(cf_diff) >= 0.8:
        reason_codes.append(REASON_L15_HISTORY)
    if da_diff is not None and abs(da_diff) >= 1.5:
        reason_codes.append(REASON_DEEP_ALLOWED)
    if vs_diff is not None and abs(vs_diff) >= 0.5:
        reason_codes.append(REASON_VENUE_SPLIT)

    # ---- Favored side ----
    if edcd_capped > 0.5:
        favored_side = "HOME"
    elif edcd_capped < -0.5:
        favored_side = "AWAY"
    else:
        favored_side = "NONE"

    # ---- Data quality + confidence ----
    n_required_for_high = 4  # ip_diff, cf_diff, ca_diff + dom_signal o da_diff
    if contrib_count >= n_required_for_high:
        data_quality = "HIGH"
    elif contrib_count >= 2:
        data_quality = "MEDIUM"
    else:
        data_quality = "LOW"
        reason_codes.append(REASON_LOW_DATA_QUALITY)

    # Confidence: razón entre drivers presentes y máximo, ajustada por magnitud
    base_conf = 100.0 * contrib_count / CONFIDENCE_MAX_DRIVERS
    # Mayor magnitud absoluta → más confianza (modesto)
    magnitude_bonus = 10.0 * min(1.0, abs(edcd_capped) / 3.0)
    if data_quality == "LOW":
        magnitude_bonus = 0.0
        base_conf = min(base_conf, 40.0)
    confidence = round(min(100.0, base_conf + magnitude_bonus), 2)

    # Limpiar reason_codes duplicados preservando orden
    seen = set()
    rc_clean = []
    for rc in reason_codes:
        if rc not in seen:
            seen.add(rc)
            rc_clean.append(rc)

    return {
        "expected_corner_diff": round(edcd_capped, 4),
        "expected_corner_diff_raw": round(edcd, 4),  # antes del cap (debug)
        "favored_corner_side":  favored_side,
        "confidence":           confidence,
        "drivers":              drivers,
        "missing_fields":       missing,
        "data_quality":         data_quality,
        "reason_codes":         rc_clean,
        "dominant_favorite_side": dom_side,
        "is_dominant_favorite":   bool(dom_signal != 0),
    }
