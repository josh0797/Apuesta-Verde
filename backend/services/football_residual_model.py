"""Sprint-D9.1 · Offset logistic regression — residual model on top of
the de-vigged market prior.

Trains and predicts under the relationship::

    logit(p_final) = logit(p_market_devig)  +  (w · x + b)
                     └──── fixed offset ────┘  └─ trainable corrector ─┘

The model does **not** try to reconstruct probabilities from scratch:
the de-vigged market opening price is the strong prior, the regression
only learns the residual correction.

Pure Python — no numpy / scipy dependency. Suitable for the ~380
matches/season scale we currently work with. Convergence is checked
against ``rel_tol`` so the cap on ``n_iter`` is defensive only.

Public API
----------
* ``standardise_features(rows, feature_names)`` → ``(X_std, mean, std)``
* ``fit_residual_model(X, y, offset, ...)``    → ``ResidualModel``
* ``ResidualModel.predict(x_std, offset)``     → ``p_final``
* ``ResidualModel.predict_batch(X_std, offset)``

Coefficient + scaler are JSON-serialisable for caching.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Sequence, Optional


# ─── Numerical helpers ────────────────────────────────────────────────
_EPS    = 1e-9
_CLIP_P = (1e-6, 1 - 1e-6)


def _sigmoid(z: float) -> float:
    # Numerically stable sigmoid.
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _logit(p: float) -> float:
    p = max(_CLIP_P[0], min(_CLIP_P[1], p))
    return math.log(p / (1.0 - p))


# ─── Standardisation ──────────────────────────────────────────────────
def standardise_features(rows: Sequence[Sequence[Optional[float]]],
                           feature_names: Sequence[str],
                           ) -> tuple[list[list[float]], list[float], list[float]]:
    """Per-column z-standardisation with mean-imputation of missing
    values. Returns the standardised matrix plus the (mean, std) used,
    so the same transformation can be applied at predict time.
    """
    d = len(feature_names)
    n = len(rows)
    means = [0.0] * d
    counts = [0]   * d
    # First pass: means with NaN/None skipped.
    for r in rows:
        for k in range(d):
            v = r[k]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                means[k] += float(v); counts[k] += 1
    for k in range(d):
        means[k] = (means[k] / counts[k]) if counts[k] > 0 else 0.0
    # Second pass: stdev.
    sq = [0.0] * d
    for r in rows:
        for k in range(d):
            v = r[k]
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                sq[k] += (float(v) - means[k]) ** 2
    stds = [
        (math.sqrt(sq[k] / counts[k]) if counts[k] > 0 else 1.0) or 1.0
        for k in range(d)
    ]
    # Third pass: build the standardised matrix.
    X: list[list[float]] = []
    for r in rows:
        row = [0.0] * d
        for k in range(d):
            v = r[k]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                row[k] = 0.0          # mean-imputation in standardised space
            else:
                row[k] = (float(v) - means[k]) / stds[k]
        X.append(row)
    return X, means, stds


def standardise_one(row: Sequence[Optional[float]],
                      means: Sequence[float],
                      stds: Sequence[float]) -> list[float]:
    out: list[float] = []
    for k, v in enumerate(row):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(0.0)
        else:
            s = stds[k] if stds[k] != 0 else 1.0
            out.append((float(v) - means[k]) / s)
    return out


# ─── Model dataclass ──────────────────────────────────────────────────
@dataclass
class ResidualModel:
    """Frozen weights + scaler. JSON-serialisable via ``asdict``."""
    feature_names:  list[str]
    weights:        list[float]
    bias:           float
    feature_means:  list[float]
    feature_stds:   list[float]
    n_train:        int = 0
    n_iter_used:    int = 0
    final_loss:     float = 0.0
    lambda_l2:      float = 1.0
    converged:      bool  = False
    # Sanity metrics on the training sample (NOT to be confused with
    # holdout — caller is responsible for that).
    train_brier_model:   float = 0.0
    train_brier_market:  float = 0.0
    train_logloss_model: float = 0.0
    train_logloss_market: float = 0.0
    reason_codes:   list[str] = field(default_factory=list)

    def predict(self, raw_row: Sequence[Optional[float]],
                  market_devig_prob: float) -> float:
        """``raw_row`` must follow ``feature_names``. ``market_devig_prob``
        is the prior probability that becomes the logit offset."""
        x = standardise_one(raw_row, self.feature_means, self.feature_stds)
        z = _logit(market_devig_prob) + self.bias \
              + sum(self.weights[k] * x[k] for k in range(len(self.weights)))
        return max(_CLIP_P[0], min(_CLIP_P[1], _sigmoid(z)))

    def predict_batch(self,
                        raw_rows: Sequence[Sequence[Optional[float]]],
                        market_devig_probs: Sequence[float]
                        ) -> list[float]:
        return [self.predict(r, p) for r, p in zip(raw_rows, market_devig_probs)]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ResidualModel":
        return cls(**d)


# ─── Training loop ────────────────────────────────────────────────────
def fit_residual_model(
    raw_rows:        Sequence[Sequence[Optional[float]]],
    targets:         Sequence[int],
    market_devig:    Sequence[float],
    feature_names:   Sequence[str],
    *,
    lambda_l2:       float = 1.0,
    lr:              float = 0.10,
    n_iter:          int   = 800,
    rel_tol:         float = 1e-5,
    verbose:         bool  = False,
) -> ResidualModel:
    """Train an offset logistic regression that minimises::

        L(w, b) = − (1/n) Σ [ y_i log p_i + (1 − y_i) log(1 − p_i) ]
                  + (λ/2) ||w||²

    where ``p_i = σ(logit(market_devig_i) + w·x_i + b)``.

    Gradient descent with L2 reg on ``w`` only — never on the offset
    nor on the bias (the offset is fixed; the bias is a global shift).
    Converges when relative drop in loss < ``rel_tol`` for 5 consecutive
    iterations.
    """
    if len(raw_rows) != len(targets) or len(targets) != len(market_devig):
        raise ValueError("raw_rows, targets and market_devig must align")
    if not raw_rows:
        raise ValueError("Empty training set")
    feature_names = list(feature_names)
    X, means, stds = standardise_features(raw_rows, feature_names)
    n = len(X); d = len(feature_names)

    offsets = [_logit(p) for p in market_devig]
    ys      = [int(bool(y)) for y in targets]

    w = [0.0] * d
    b = 0.0

    prev_loss = float("inf")
    converged = False
    n_stable  = 0
    for it in range(n_iter):
        # Forward pass + gradients.
        grad_w = [0.0] * d
        grad_b = 0.0
        loss   = 0.0
        for i in range(n):
            z = offsets[i] + b + sum(w[k] * X[i][k] for k in range(d))
            p = _sigmoid(z)
            p = max(_CLIP_P[0], min(_CLIP_P[1], p))
            loss += -(ys[i] * math.log(p) + (1 - ys[i]) * math.log(1 - p))
            err  = (p - ys[i])
            grad_b += err
            for k in range(d):
                grad_w[k] += err * X[i][k]
        loss /= n
        grad_b /= n
        for k in range(d):
            grad_w[k] = grad_w[k] / n + lambda_l2 * w[k]
        # L2 penalty into loss for monitoring (not part of grad of b).
        loss += 0.5 * lambda_l2 * sum(wk * wk for wk in w)

        # Update.
        for k in range(d):
            w[k] -= lr * grad_w[k]
        b -= lr * grad_b

        if verbose and (it % 50 == 0 or it == n_iter - 1):
            print(f"[it {it:4d}] loss={loss:.6f}")
        # Convergence check.
        if prev_loss != float("inf"):
            rel = abs(prev_loss - loss) / max(abs(prev_loss), _EPS)
            n_stable = (n_stable + 1) if rel < rel_tol else 0
            if n_stable >= 5:
                converged = True
                break
        prev_loss = loss

    # Final training-sample metrics.
    p_model = []
    p_mkt   = []
    for i in range(n):
        z = offsets[i] + b + sum(w[k] * X[i][k] for k in range(d))
        p_model.append(max(_CLIP_P[0], min(_CLIP_P[1], _sigmoid(z))))
        p_mkt.append(max(_CLIP_P[0], min(_CLIP_P[1], market_devig[i])))
    brier_m = sum((p - y) ** 2 for p, y in zip(p_model, ys)) / n
    brier_d = sum((p - y) ** 2 for p, y in zip(p_mkt,  ys)) / n
    ll_m = sum(-(y * math.log(p) + (1 - y) * math.log(1 - p))
                  for p, y in zip(p_model, ys)) / n
    ll_d = sum(-(y * math.log(p) + (1 - y) * math.log(1 - p))
                  for p, y in zip(p_mkt,   ys)) / n

    reason_codes = ["RESIDUAL_MODEL_FIT_OK"]
    if not converged:
        reason_codes.append("RESIDUAL_MODEL_HIT_MAX_ITER")
    return ResidualModel(
        feature_names=feature_names,
        weights=w, bias=b,
        feature_means=means, feature_stds=stds,
        n_train=n, n_iter_used=(it + 1),
        final_loss=loss, lambda_l2=lambda_l2,
        converged=converged,
        train_brier_model=brier_m, train_brier_market=brier_d,
        train_logloss_model=ll_m, train_logloss_market=ll_d,
        reason_codes=reason_codes,
    )


# ─── Identity model (offset only) ─────────────────────────────────────
def identity_model(feature_names: Sequence[str]) -> ResidualModel:
    """A trivial model with all weights at zero — predicts exactly the
    market prior. Useful as a baseline / fallback when training is
    unsafe (e.g. too few samples)."""
    d = len(feature_names)
    return ResidualModel(
        feature_names=list(feature_names),
        weights=[0.0] * d, bias=0.0,
        feature_means=[0.0] * d, feature_stds=[1.0] * d,
        n_train=0, n_iter_used=0, final_loss=0.0,
        lambda_l2=0.0, converged=True,
        reason_codes=["RESIDUAL_MODEL_IDENTITY_FALLBACK"],
    )


__all__ = [
    "ResidualModel",
    "fit_residual_model",
    "identity_model",
    "standardise_features",
    "standardise_one",
]
