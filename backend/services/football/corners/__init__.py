"""Corner engine — Sprint Corner-1 + Corner-2 (Fase A).

Módulos:
    corner_diff_model       — estima `expected_corner_diff` (caps, drivers).
    corner_most_model       — clasificador binario H/A para mercado Most Corners.
    corner_diff_distribution — distribución empírica por buckets + Asian Corners.
    corner_backtest         — backtest probabilístico walk-forward (Brier, LogLoss, calibration).

Diseño:
    * Cero dependencias externas además de numpy/pandas (puro Python).
    * Cero acceso a red — los modelos consumen `context` dicts ya construidos.
    * `expected_corner_diff` clamped a ±5.5 por reglas operativas.
    * Fail-soft: si faltan inputs, devuelve `data_quality="LOW"` y
      `reason_codes` apropiados — nunca crashea.
    * Feature flags listos para integración (ENABLE_CORNER_MOST_MODEL,
      ENABLE_ASIAN_CORNERS_MODEL).
"""

from .corner_diff_model import compute_expected_corner_diff
from .corner_most_model import predict_most_corners
from .corner_diff_distribution import build_corner_diff_distribution, build_asian_corner_markets
from .corner_backtest import run_corner_backtest
from .skellam_corner_model import (
    predict_skellam_corner_diff,
    calibrate_skellam_lambdas,
    skellam_most_corners,
    skellam_to_asian_corners,
)

__all__ = [
    "compute_expected_corner_diff",
    "predict_most_corners",
    "build_corner_diff_distribution",
    "build_asian_corner_markets",
    "run_corner_backtest",
    "predict_skellam_corner_diff",
    "calibrate_skellam_lambdas",
    "skellam_most_corners",
    "skellam_to_asian_corners",
]
