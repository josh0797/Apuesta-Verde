"""Line Learning Analytics — Feature 8 (Phase 43).

Aggregates the persisted ``line_learning_samples`` collection into the
dashboard metrics the user asked for:

  * push_rate
  * near_miss_rate
  * half_run_loss_rate
  * protected_line_success_rate
  * aggressive_line_success_rate
  * per_line_success      : { "9.5": 0.61, "10.0": 0.73, ... }
  * insights              : list[str] (Spanish)

All async helpers are fail-soft — return an empty payload on DB error.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

log = logging.getLogger("line_learning_analytics")

ENGINE_VERSION = "line_learning_analytics.1"
COLLECTION = "line_learning_samples"


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


async def compute_analytics(
    db,
    *,
    user_id: str,
    sport:       Optional[str] = None,
    market_type: Optional[str] = None,
) -> dict:
    """Aggregate samples into dashboard-ready metrics + insights."""
    if db is None:
        return _empty()
    q: dict = {"user_id": user_id}
    if sport:
        q["sport"] = sport.lower()
    if market_type:
        q["market_type"] = market_type
    try:
        cursor = db[COLLECTION].find(q)
        samples = [r async for r in cursor]
    except Exception as exc:
        log.debug("compute_analytics db read failed: %s", exc)
        return _empty()

    total = len(samples)
    if total == 0:
        return _empty()

    buckets = {"EXACT_HIT": 0, "SAFE_LINE_HIT": 0, "NEAR_MISS": 0,
               "PUSH_SAVED": 0, "AGGRESSIVE_LINE_MISS": 0, "PROFILE_WRONG": 0}
    half_run_losses = 0
    protected_success = 0
    protected_total = 0
    aggressive_success = 0
    aggressive_total = 0
    per_line_hits: dict[str, int] = defaultdict(int)
    per_line_total: dict[str, int] = defaultdict(int)

    for s in samples:
        cls = (s.get("classification") or "").upper()
        if cls in buckets:
            buckets[cls] += 1
        reasons = s.get("reason_codes") or []
        if "LOST_BY_HALF_RUN" in reasons:
            half_run_losses += 1
        engine_line = _safe_float((s.get("engine") or {}).get("line"))
        user_line   = _safe_float((s.get("user_actual") or {}).get("line"))
        engine_oc   = (s.get("engine")      or {}).get("outcome")
        user_oc     = (s.get("user_actual") or {}).get("outcome")
        # Per-line success of the ENGINE recommendation.
        if engine_line is not None and engine_oc:
            key = f"{engine_line}"
            per_line_total[key] += 1
            if str(engine_oc).lower() in ("won", "win", "hit", "cashout_win"):
                per_line_hits[key] += 1
        # Protected line success = the user's line outperformed the engine's.
        protected = (s.get("user_more_protected"))
        if protected is True:
            protected_total += 1
            if str(user_oc or "").lower() in ("won", "win", "hit", "push", "void", "cashout_win"):
                protected_success += 1
        elif protected is False:
            aggressive_total += 1
            if str(user_oc or "").lower() in ("won", "win", "hit", "cashout_win"):
                aggressive_success += 1

    metrics = {
        "sample_size":               total,
        "push_rate":                 round(buckets["PUSH_SAVED"] / total, 4),
        "near_miss_rate":            round(buckets["NEAR_MISS"]  / total, 4),
        "half_run_loss_rate":        round(half_run_losses        / total, 4),
        "aggressive_line_miss_rate": round(buckets["AGGRESSIVE_LINE_MISS"] / total, 4),
        "safe_line_hit_rate":        round(buckets["SAFE_LINE_HIT"]        / total, 4),
        "exact_hit_rate":            round(buckets["EXACT_HIT"]            / total, 4),
        "profile_wrong_rate":        round(buckets["PROFILE_WRONG"]        / total, 4),
        "protected_line_success_rate": (
            round(protected_success / protected_total, 4) if protected_total else None
        ),
        "aggressive_line_success_rate": (
            round(aggressive_success / aggressive_total, 4) if aggressive_total else None
        ),
        "protected_line_n":   protected_total,
        "aggressive_line_n":  aggressive_total,
    }
    per_line_success = {
        ln: round(per_line_hits[ln] / per_line_total[ln], 4)
        for ln in per_line_total if per_line_total[ln] > 0
    }

    return {
        "engine_version": ENGINE_VERSION,
        "scope": {"user_id": user_id, "sport": sport, "market_type": market_type},
        "metrics": metrics,
        "per_line_success": per_line_success,
        "per_line_sample_size": dict(per_line_total),
        "insights": _build_insights(metrics, per_line_success, market_type),
    }


def _empty() -> dict:
    return {
        "engine_version": ENGINE_VERSION,
        "scope": {},
        "metrics": {"sample_size": 0},
        "per_line_success": {},
        "per_line_sample_size": {},
        "insights": [],
    }


def _build_insights(
    metrics: dict,
    per_line_success: dict[str, float],
    market_type: Optional[str],
) -> list[str]:
    """Generate human-readable Spanish insights."""
    insights: list[str] = []
    n = metrics.get("sample_size", 0)
    if n < 10:
        insights.append(
            f"Pocas muestras todavía ({n}). Las recomendaciones serán "
            "más confiables al pasar 30 muestras por mercado."
        )
        return insights

    # Aggressive vs Protected.
    prot = metrics.get("protected_line_success_rate")
    aggr = metrics.get("aggressive_line_success_rate")
    if prot is not None and aggr is not None and prot - aggr >= 0.10:
        insights.append(
            f"Las líneas protegidas rinden {(prot - aggr):.0%} mejor que las agresivas "
            f"({prot:.0%} vs {aggr:.0%}). Considera elegir el modo Protegido."
        )
    if metrics.get("aggressive_line_miss_rate", 0) >= 0.25:
        insights.append(
            f"El {metrics['aggressive_line_miss_rate']:.0%} de los picks termina en línea "
            "demasiado agresiva. Posible sesgo del engine hacia líneas tight."
        )
    if metrics.get("push_rate", 0) >= 0.10:
        insights.append(
            f"Push frecuente ({metrics['push_rate']:.0%}). Tomar la siguiente línea protegida "
            "convierte estos pushes en pérdidas evitadas o ganancias."
        )
    if metrics.get("half_run_loss_rate", 0) >= 0.15:
        market = market_type or "este mercado"
        insights.append(
            f"Half-run/half-goal losses {metrics['half_run_loss_rate']:.0%} en {market}: "
            "el engine tiende a ser ~0.5 unidades muy agresivo."
        )

    # Per-line: detect monotonic pattern (e.g. protected > value).
    if len(per_line_success) >= 2:
        sorted_lines = sorted(per_line_success.items(), key=lambda kv: float(kv[0]))
        if all(per_line_success[k] >= 0.0 for k, _ in sorted_lines):
            best = max(sorted_lines, key=lambda kv: kv[1])
            insights.append(
                f"Tu mejor línea histórica: {best[0]} ({best[1]:.0%} de éxito)."
            )

    if metrics.get("profile_wrong_rate", 0) >= 0.20:
        insights.append(
            f"El engine falla la lectura del juego en el {metrics['profile_wrong_rate']:.0%} "
            "de los casos. Revisar inputs (xG, pace, lineups) antes de apostar."
        )

    if not insights:
        insights.append("Comportamiento balanceado: sin sesgo claro detectado por ahora.")
    return insights


__all__ = [
    "ENGINE_VERSION",
    "COLLECTION",
    "compute_analytics",
]
