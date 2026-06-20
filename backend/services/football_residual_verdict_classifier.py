"""
Sprint D9.2 — Block C · Residual Backtest Verdict Classifier (pure).

Extrae la lógica del clasificador del backtest residual D9.1 a un módulo
puro, testeable, sin dependencias externas. Añade soporte para
**corrección de Bonferroni estricta** para evitar falsos positivos
cuando se evalúan múltiples combinaciones (scope × market × metric)
en un solo run.

Spec
----
* Cada combinación (scope, market) prueba simultáneamente dos métricas:
    - delta-Brier residual vs market_devig
    - delta-LogLoss residual vs market_devig
  ⇒ 2 hipótesis por combinación.
* Cuando se ejecuta el sweep completo (varios markets × varios scopes),
  el número total de pruebas es:
    m_total = n_combinations × 2
* Bonferroni: el α por test es α/m_total. El umbral del bootstrap (que
  reporta `p_below_zero` ≈ P(diff < 0)) debe ser:
    cutoff = 1 - α/m_total
* SIN Bonferroni el script usaba cutoff = 0.95 (α=0.05 unilateral).
* Si tanto Brier como LogLoss superan el cutoff ajustado ⇒ verdict
    RESIDUAL_BEATS_MARKET_OUT_OF_SAMPLE (Bonferroni-significant).
* Si pasan al cutoff naïve 0.95 pero NO al Bonferroni ⇒ se anota
    BONFERRONI_NOT_SIGNIFICANT como tag adicional para auditoría.
* Mantiene las heurísticas anteriores: calibración / overfit / no-signal.

Diseño defensivo
----------------
* Pure: no I/O, no globals, no time. Determinístico.
* Fail-soft: ante inputs malformados devuelve tag genérico
  `INSUFFICIENT_DIAGNOSTICS` en lugar de levantar excepción.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────
TAG_RESIDUAL_BEATS_MARKET    = "RESIDUAL_BEATS_MARKET_OUT_OF_SAMPLE"
TAG_CALIBRATION_ONLY         = "RESIDUAL_IMPROVES_CALIBRATION_ONLY"
TAG_NO_INCREMENTAL_SIGNAL    = "NO_INCREMENTAL_SIGNAL_WITH_CURRENT_FEATURES"
TAG_RESIDUAL_OVERFIT         = "RESIDUAL_OVERFIT_DETECTED"
TAG_BONFERRONI_NOT_SIG       = "BONFERRONI_NOT_SIGNIFICANT"
TAG_INSUFFICIENT_DIAG        = "INSUFFICIENT_DIAGNOSTICS"

DEFAULT_ALPHA = 0.05  # familywise type-I error.


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_get(d: Any, *keys: str) -> Optional[Any]:
    """Navigate nested dicts safely. Returns None on any failure."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def compute_bonferroni_cutoff(
    *,
    alpha: float = DEFAULT_ALPHA,
    m_tests: int = 1,
) -> Dict[str, float]:
    """Compute the Bonferroni-adjusted decision cutoff for a one-sided
    bootstrap test where the test statistic is `p_below_zero`
    (probability mass of the bootstrap distribution below zero).

    Args
    ----
    alpha    : familywise type-I error rate (default 0.05).
    m_tests  : total number of simultaneous hypotheses being tested.

    Returns
    -------
    dict with:
        alpha           : input familywise alpha
        m_tests         : input m
        alpha_adjusted  : alpha / m
        cutoff          : 1 - alpha_adjusted  (one-sided)
    """
    if m_tests < 1:
        m_tests = 1
    alpha_adj = float(alpha) / float(m_tests)
    cutoff = 1.0 - alpha_adj
    return {
        "alpha":          float(alpha),
        "m_tests":        int(m_tests),
        "alpha_adjusted": float(alpha_adj),
        "cutoff":         float(cutoff),
    }


def _detect_overfit(
    *,
    train_audit: Any,
    br_r: Optional[float],
    br_d: Optional[float],
    delta_train_threshold: float = -0.01,
    delta_holdout_gap_threshold: float = 0.005,
) -> bool:
    """Overfit if the model is materially better than market on TRAIN
    but materially WORSE on HOLDOUT.

    Args
    ----
    train_audit : dict-of-dicts or dict with `pooled` leaf.
    br_r        : holdout Brier (residual model).
    br_d        : holdout Brier (market_devig baseline).
    """
    if not isinstance(train_audit, dict):
        return False
    if br_r is None or br_d is None:
        return False
    if "pooled" in train_audit:
        leaves = [train_audit["pooled"]]
    else:
        leaves = list(train_audit.values())
    for leaf in leaves:
        if not isinstance(leaf, dict):
            continue
        dt = leaf.get("delta_train_brier")
        if (
            dt is not None
            and dt < delta_train_threshold
            and (br_r - br_d) > delta_holdout_gap_threshold
        ):
            return True
    return False


def classify_residual_verdict(
    diag_residual: Dict[str, Any],
    diag_market: Dict[str, Any],
    diag_dc: Dict[str, Any],
    boot_brier: Dict[str, Any],
    boot_logloss: Dict[str, Any],
    train_audit: Any,
    *,
    alpha: float = DEFAULT_ALPHA,
    m_tests: int = 1,
) -> Dict[str, Any]:
    """Decide the verdict tags for a single (scope, market) combination.

    Args
    ----
    diag_residual / diag_market / diag_dc : output of
        `football_calibration_diagnostics.compute_calibration_diagnostics`
        for residual, market_devig and DC-original probability sources
        respectively. Each carries a `model_vs_market` block with
        `brier_model`, `brier_market_devig`, `logloss_model`,
        `logloss_market_devig` and a `calibration` block.
    boot_brier   : paired bootstrap of (Brier_resid − Brier_market_devig).
        Must carry `p_below_zero` ∈ [0, 1].
    boot_logloss : same shape but for LogLoss.
    train_audit  : per-league or pooled audit dict from training.
    alpha        : familywise alpha (default 0.05).
    m_tests      : total simultaneous hypotheses (Bonferroni divisor).

    Returns
    -------
    dict {
      "tags":  list[str]   - the verdict tag(s).
      "bonferroni": {
          "alpha":           float,
          "m_tests":         int,
          "alpha_adjusted":  float,
          "cutoff":          float,
          "naive_cutoff":    0.95,
          "brier_p":         float | None,
          "logloss_p":       float | None,
          "brier_passes_bonferroni":    bool,
          "logloss_passes_bonferroni":  bool,
          "brier_passes_naive":         bool,
          "logloss_passes_naive":       bool,
      }
    }
    """
    # ── Extract diagnostics ──────────────────────────────────────────
    br_r = _safe_get(diag_residual, "model_vs_market", "brier_model")
    br_d = _safe_get(diag_residual, "model_vs_market", "brier_market_devig")
    ll_r = _safe_get(diag_residual, "model_vs_market", "logloss_model")
    ll_d = _safe_get(diag_residual, "model_vs_market", "logloss_market_devig")
    cal  = _safe_get(diag_residual, "calibration") or {}

    # ── Bonferroni cutoff ────────────────────────────────────────────
    bonf = compute_bonferroni_cutoff(alpha=alpha, m_tests=m_tests)
    cutoff_bonf = bonf["cutoff"]
    cutoff_naive = 1.0 - float(alpha)  # e.g. 0.95

    brier_p   = boot_brier.get("p_below_zero")   if isinstance(boot_brier, dict)   else None
    logloss_p = boot_logloss.get("p_below_zero") if isinstance(boot_logloss, dict) else None

    brier_pass_bonf = brier_p is not None and brier_p >= cutoff_bonf
    ll_pass_bonf    = logloss_p is not None and logloss_p >= cutoff_bonf
    brier_pass_naive = brier_p is not None and brier_p >= cutoff_naive
    ll_pass_naive    = logloss_p is not None and logloss_p >= cutoff_naive

    tags: List[str] = []

    # ── Fail-safe: no holdout diagnostics at all ──────────────────
    if br_r is None or br_d is None or ll_r is None or ll_d is None:
        tags.append(TAG_INSUFFICIENT_DIAG)
        return {
            "tags": tags,
            "bonferroni": {
                **bonf,
                "naive_cutoff":               cutoff_naive,
                "brier_p":                    brier_p,
                "logloss_p":                  logloss_p,
                "brier_passes_bonferroni":    brier_pass_bonf,
                "logloss_passes_bonferroni":  ll_pass_bonf,
                "brier_passes_naive":         brier_pass_naive,
                "logloss_passes_naive":       ll_pass_naive,
            },
        }

    # ── Detect overfit (independent of significance tier) ────────────
    overfit = _detect_overfit(
        train_audit=train_audit, br_r=br_r, br_d=br_d,
    )

    # ── PRIMARY VERDICT — Bonferroni-strict significance ─────────────
    # Both metrics must dominate AND survive Bonferroni.
    holdout_dominates = (br_r < br_d) and (ll_r < ll_d)
    if holdout_dominates and brier_pass_bonf and ll_pass_bonf:
        tags.append(TAG_RESIDUAL_BEATS_MARKET)
    elif (
        cal.get("slope") is not None
        and 0.85 <= float(cal["slope"]) <= 1.15
        and abs(float(cal.get("intercept") or 0)) < 0.05
        and br_r >= br_d
    ):
        # Calibration-only: residual model is well-calibrated but does
        # NOT beat the market in raw Brier.
        tags.append(TAG_CALIBRATION_ONLY)
    else:
        tags.append(TAG_NO_INCREMENTAL_SIGNAL)

    # ── Annotate Bonferroni-vs-naive disagreement ────────────────────
    # If the naïve test passes but Bonferroni does NOT, flag it. This
    # is the key new audit trail introduced by D9.2-C.
    if (
        (brier_pass_naive or ll_pass_naive)
        and not (brier_pass_bonf and ll_pass_bonf)
        and TAG_RESIDUAL_BEATS_MARKET not in tags
    ):
        tags.append(TAG_BONFERRONI_NOT_SIG)

    if overfit:
        tags.append(TAG_RESIDUAL_OVERFIT)

    return {
        "tags": tags,
        "bonferroni": {
            **bonf,
            "naive_cutoff":               cutoff_naive,
            "brier_p":                    brier_p,
            "logloss_p":                  logloss_p,
            "brier_passes_bonferroni":    brier_pass_bonf,
            "logloss_passes_bonferroni":  ll_pass_bonf,
            "brier_passes_naive":         brier_pass_naive,
            "logloss_passes_naive":       ll_pass_naive,
        },
    }


__all__ = [
    "classify_residual_verdict",
    "compute_bonferroni_cutoff",
    "TAG_RESIDUAL_BEATS_MARKET",
    "TAG_CALIBRATION_ONLY",
    "TAG_NO_INCREMENTAL_SIGNAL",
    "TAG_RESIDUAL_OVERFIT",
    "TAG_BONFERRONI_NOT_SIG",
    "TAG_INSUFFICIENT_DIAG",
    "DEFAULT_ALPHA",
]
