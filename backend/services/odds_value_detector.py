"""Sprint E.2 · Odds Value Detector.

Pure analytical layer that consumes the snapshots written by
:mod:`services.live_odds_monitor` (collection ``odds_snapshots`` with
``source="live_odds_monitor_v1"``) and surfaces four families of signals
**without ever placing a bet** (strict ``observe_only`` mandate):

* **OUTLIER**        — a bookmaker's price is far from the consensus
  for the same (match, market, outcome). Detected via robust median +
  MAD z-score on implied probabilities.
* **EDGE_VS_MODEL**  — the model's estimated probability beats the
  consensus implied probability by ≥ ``min_edge_pp`` percentage points.
  Requires a ``model_prob`` from the caller.
* **FAST_MOVE**      — implied probability of an outcome shifted by
  ≥ ``fast_move_pp`` over the last ``fast_move_window_seconds``.
* **DISPERSION**     — the spread (max-min) between bookmakers' implied
  probabilities for the same outcome is ≥ ``dispersion_pp``.

This module is **pure** (no Mongo / no HTTP). The persistence layer
:mod:`services.odds_alerts` is the one that talks to ``odds_alerts``.

Strict invariants
-----------------
* observe_only — never proposes a bet, never mutates picks.
* Fail-soft — malformed snapshots are skipped; never raises.
* Deterministic — same input → same signals.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional


# ─── Constants & defaults ─────────────────────────────────────────────
DEFAULT_MIN_BOOKS_FOR_CONSENSUS: int = 3
DEFAULT_OUTLIER_Z:               float = 3.0
DEFAULT_MIN_EDGE_PP:             float = 5.0     # 5 percentage points
DEFAULT_DISPERSION_PP:           float = 6.0
DEFAULT_FAST_MOVE_PP:            float = 4.0
DEFAULT_FAST_MOVE_WINDOW_SEC:    int   = 600     # 10 min

SIG_OUTLIER:        str = "OUTLIER"
SIG_EDGE_VS_MODEL:  str = "EDGE_VS_MODEL"
SIG_FAST_MOVE:      str = "FAST_MOVE"
SIG_DISPERSION:     str = "DISPERSION"

SEVERITY_LOW:    str = "LOW"
SEVERITY_MEDIUM: str = "MEDIUM"
SEVERITY_HIGH:   str = "HIGH"


# ─── Helpers ──────────────────────────────────────────────────────────
def _safe_price_to_implied(price: Any) -> Optional[float]:
    """Convert a decimal price (e.g. 1.85) to an implied probability
    in ``[0.0, 1.0]``. ``None`` when the price is missing/invalid."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 1.0 or not math.isfinite(p):
        return None
    return 1.0 / p


def _median_abs_dev(values: list[float], *, med: float) -> float:
    """Robust median absolute deviation (MAD) of ``values``.

    Uses the consistency constant ``1.4826`` to make MAD a
    Gaussian-equivalent SD estimator.
    """
    if len(values) < 2:
        return 0.0
    deviations = [abs(v - med) for v in values]
    mad = statistics.median(deviations)
    return mad * 1.4826


def _outcome_key(outcome: dict) -> Optional[tuple]:
    """Return a stable identifier for an outcome inside a (market)
    context. ``None`` if the outcome is malformed."""
    if not isinstance(outcome, dict):
        return None
    name = outcome.get("name")
    if not name:
        return None
    point = outcome.get("point")
    return (name, point)


def _severity_from_value(value: float, low: float, high: float) -> str:
    """Three-tier severity from a magnitude. ``high`` thresholds are
    inclusive."""
    av = abs(value)
    if av >= high:
        return SEVERITY_HIGH
    if av >= low:
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


# ─── Pure: index latest snapshots by (match, market, bookmaker, outcome)
def index_latest_snapshots(
    snapshots: Iterable[dict],
) -> dict:
    """Pure transform. Returns::

        {
          (match_id, market, outcome_name, outcome_point):
            { bookmaker_key: {"price": float, "implied": float,
                              "fetched_at": <dt|str>,
                              "snapshot": <original>} }
        }

    When a bookmaker emits more than one snapshot for the same outcome
    we keep the most recent (largest ``fetched_at``).
    """
    out: dict[tuple, dict[str, dict]] = {}
    for s in snapshots or []:
        if not isinstance(s, dict):
            continue
        mid     = s.get("match_id")
        market  = s.get("market")
        bm      = s.get("bookmaker_key") or s.get("bookmaker_title")
        if not mid or not market or not bm:
            continue
        outcomes = s.get("outcomes") or []
        if not isinstance(outcomes, list):
            continue
        fa = s.get("fetched_at") or s.get("snapshot_at")
        for o in outcomes:
            ok = _outcome_key(o)
            if ok is None:
                continue
            implied = _safe_price_to_implied(o.get("price"))
            if implied is None:
                continue
            try:
                price = float(o.get("price"))
            except (TypeError, ValueError):
                continue
            key = (str(mid), str(market), ok[0], ok[1])
            row = {"price": price, "implied": implied,
                   "fetched_at": fa, "snapshot": s,
                   "outcome": o}
            bucket = out.setdefault(key, {})
            cur = bucket.get(bm)
            if cur is None or _is_after(fa, cur.get("fetched_at")):
                bucket[bm] = row
    return out


def _is_after(a, b) -> bool:
    """Compare two datetime-ish values fail-softly."""
    if a is None:
        return False
    if b is None:
        return True
    try:
        if hasattr(a, "timestamp") and hasattr(b, "timestamp"):
            return a > b
        # Fallback: ISO strings.
        return str(a) > str(b)
    except Exception:  # noqa: BLE001
        return False


# ─── Pure: detect OUTLIER + DISPERSION over a single outcome bucket ───
def _consensus_for_outcome(
    by_book: dict[str, dict],
    *,
    min_books: int = DEFAULT_MIN_BOOKS_FOR_CONSENSUS,
) -> Optional[dict]:
    """Return consensus stats ``{n, median_implied, mad_implied,
    min_implied, max_implied, dispersion_pp}`` or ``None`` when not
    enough books are present."""
    implieds = [row["implied"] for row in by_book.values()
                if row.get("implied") is not None]
    if len(implieds) < min_books:
        return None
    med = statistics.median(implieds)
    mad = _median_abs_dev(implieds, med=med)
    return {
        "n":              len(implieds),
        "median_implied": med,
        "mad_implied":    mad,
        "min_implied":    min(implieds),
        "max_implied":    max(implieds),
        "dispersion_pp":  (max(implieds) - min(implieds)) * 100.0,
    }


def detect_outlier_and_dispersion_signals(
    *,
    indexed: dict,
    min_books: int = DEFAULT_MIN_BOOKS_FOR_CONSENSUS,
    outlier_z: float = DEFAULT_OUTLIER_Z,
    dispersion_pp: float = DEFAULT_DISPERSION_PP,
) -> list[dict]:
    """Emit OUTLIER + DISPERSION signals from an indexed snapshot map
    (output of :func:`index_latest_snapshots`).
    """
    signals: list[dict] = []
    for (mid, market, name, point), by_book in indexed.items():
        consensus = _consensus_for_outcome(by_book, min_books=min_books)
        if consensus is None:
            continue

        # DISPERSION (per outcome).
        if consensus["dispersion_pp"] >= dispersion_pp:
            signals.append({
                "signal_type":   SIG_DISPERSION,
                "match_id":      mid,
                "market":        market,
                "outcome_name":  name,
                "outcome_point": point,
                "n_books":       consensus["n"],
                "min_implied":   round(consensus["min_implied"], 4),
                "max_implied":   round(consensus["max_implied"], 4),
                "median_implied": round(consensus["median_implied"], 4),
                "dispersion_pp": round(consensus["dispersion_pp"], 2),
                "severity":      _severity_from_value(
                    consensus["dispersion_pp"],
                    low=dispersion_pp, high=dispersion_pp + 4.0,
                ),
                "reason_code":   "DISPERSION_BETWEEN_BOOKMAKERS",
            })

        # OUTLIER per bookmaker (compared to consensus).
        med = consensus["median_implied"]
        mad = consensus["mad_implied"]
        if mad <= 0:
            continue   # all books identical → nothing to flag.
        for bm_key, row in by_book.items():
            imp = row["implied"]
            z = (imp - med) / mad
            if abs(z) >= outlier_z:
                signals.append({
                    "signal_type":     SIG_OUTLIER,
                    "match_id":        mid,
                    "market":          market,
                    "outcome_name":    name,
                    "outcome_point":   point,
                    "bookmaker_key":   bm_key,
                    "bookmaker_implied": round(imp, 4),
                    "bookmaker_price":   row["price"],
                    "consensus_implied": round(med, 4),
                    "mad_implied":       round(mad, 4),
                    "z_score":          round(z, 2),
                    "n_books":          consensus["n"],
                    "severity":         _severity_from_value(
                        abs(z), low=outlier_z,
                        high=outlier_z + 2.0,
                    ),
                    "reason_code":      ("OUTLIER_LOW_IMPLIED_VS_CONSENSUS"
                                          if z < 0 else
                                          "OUTLIER_HIGH_IMPLIED_VS_CONSENSUS"),
                })
    return signals


# ─── Pure: EDGE_VS_MODEL ──────────────────────────────────────────────
def detect_edge_signals(
    *,
    indexed: dict,
    model_probs: dict,
    min_edge_pp: float = DEFAULT_MIN_EDGE_PP,
    min_books: int = DEFAULT_MIN_BOOKS_FOR_CONSENSUS,
) -> list[dict]:
    """Emit ``EDGE_VS_MODEL`` signals.

    ``model_probs`` maps ``(match_id, market, outcome_name, outcome_point)``
    → ``model_probability`` in ``[0, 1]``. Outcomes without a model
    probability are skipped.
    """
    signals: list[dict] = []
    if not model_probs:
        return signals
    for (mid, market, name, point), by_book in indexed.items():
        mp = model_probs.get((str(mid), str(market), name, point))
        if mp is None:
            mp = model_probs.get((str(mid), str(market), name))
        if mp is None:
            continue
        try:
            mp = float(mp)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= mp <= 1.0):
            continue
        consensus = _consensus_for_outcome(by_book, min_books=min_books)
        if consensus is None:
            continue
        med = consensus["median_implied"]
        edge_pp = (mp - med) * 100.0
        if edge_pp < min_edge_pp:
            continue
        # Best-priced (highest decimal price = lowest implied) book.
        best_bm = None
        best_price = None
        best_implied = None
        for bm_key, row in by_book.items():
            if best_implied is None or row["implied"] < best_implied:
                best_implied = row["implied"]
                best_price   = row["price"]
                best_bm      = bm_key
        signals.append({
            "signal_type":      SIG_EDGE_VS_MODEL,
            "match_id":         mid,
            "market":           market,
            "outcome_name":     name,
            "outcome_point":    point,
            "model_prob":       round(mp, 4),
            "consensus_implied": round(med, 4),
            "edge_pp":          round(edge_pp, 2),
            "best_bookmaker":   best_bm,
            "best_price":       best_price,
            "best_implied":     round(best_implied or 0.0, 4),
            "n_books":          consensus["n"],
            "severity":         _severity_from_value(
                edge_pp, low=min_edge_pp, high=min_edge_pp + 4.0,
            ),
            "reason_code":      "MODEL_BEATS_MARKET_IMPLIED",
        })
    return signals


# ─── Pure: FAST_MOVE ──────────────────────────────────────────────────
def _parse_dt(x) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, str):
        try:
            d = datetime.fromisoformat(x.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def detect_fast_move_signals(
    *,
    snapshots_by_match: dict,
    window_seconds: int = DEFAULT_FAST_MOVE_WINDOW_SEC,
    fast_move_pp:   float = DEFAULT_FAST_MOVE_PP,
) -> list[dict]:
    """Emit ``FAST_MOVE`` signals.

    ``snapshots_by_match`` maps ``match_id → list_of_snapshots``
    (chronologically ordered or not — we sort internally). We compute,
    per (bookmaker, market, outcome), the implied probability at the
    *now-most-recent* snapshot vs the latest snapshot **outside** the
    fast-move window. If the difference ≥ ``fast_move_pp``, we flag it.
    """
    signals: list[dict] = []
    now = datetime.now(timezone.utc)
    window = timedelta(seconds=window_seconds)
    for mid, snaps in (snapshots_by_match or {}).items():
        if not isinstance(snaps, list) or len(snaps) < 2:
            continue
        # Group by (bookmaker, market, outcome).
        groups: dict[tuple, list[dict]] = {}
        for s in snaps:
            if not isinstance(s, dict):
                continue
            bm     = s.get("bookmaker_key") or s.get("bookmaker_title")
            market = s.get("market")
            fa     = _parse_dt(s.get("fetched_at") or s.get("snapshot_at"))
            if not (bm and market and fa):
                continue
            for o in s.get("outcomes") or []:
                imp = _safe_price_to_implied(o.get("price"))
                if imp is None:
                    continue
                key = (bm, market, o.get("name"), o.get("point"))
                groups.setdefault(key, []).append({
                    "fetched_at": fa, "implied": imp,
                    "price": o.get("price"), "outcome": o,
                })
        for (bm, market, name, point), rows in groups.items():
            rows.sort(key=lambda r: r["fetched_at"])
            current = rows[-1]
            if (now - current["fetched_at"]) > window:
                # The "current" snapshot itself is older than the window
                # — no fresh move to talk about.
                continue
            # Latest snapshot that is OUTSIDE the move window.
            past = None
            for r in reversed(rows[:-1]):
                if (current["fetched_at"] - r["fetched_at"]) >= timedelta(seconds=1):
                    past = r
                    if (current["fetched_at"] - r["fetched_at"]) >= window / 2:
                        break
            if past is None:
                continue
            delta_pp = (current["implied"] - past["implied"]) * 100.0
            if abs(delta_pp) < fast_move_pp:
                continue
            signals.append({
                "signal_type":    SIG_FAST_MOVE,
                "match_id":       str(mid),
                "market":         market,
                "outcome_name":   name,
                "outcome_point":  point,
                "bookmaker_key":  bm,
                "from_implied":   round(past["implied"], 4),
                "to_implied":     round(current["implied"], 4),
                "delta_pp":       round(delta_pp, 2),
                "elapsed_sec":    int(
                    (current["fetched_at"] - past["fetched_at"]).total_seconds()
                ),
                "severity":       _severity_from_value(
                    abs(delta_pp), low=fast_move_pp,
                    high=fast_move_pp + 4.0,
                ),
                "reason_code":   ("FAST_LINE_MOVE_SHARPER"
                                    if delta_pp > 0
                                    else "FAST_LINE_MOVE_DRIFTING"),
            })
    return signals


# ─── Top-level orchestration (still pure) ─────────────────────────────
def detect_all_signals(
    *,
    snapshots: list[dict],
    model_probs: Optional[dict] = None,
    min_books: int = DEFAULT_MIN_BOOKS_FOR_CONSENSUS,
    outlier_z: float = DEFAULT_OUTLIER_Z,
    min_edge_pp: float = DEFAULT_MIN_EDGE_PP,
    dispersion_pp: float = DEFAULT_DISPERSION_PP,
    fast_move_pp: float = DEFAULT_FAST_MOVE_PP,
    fast_move_window_sec: int = DEFAULT_FAST_MOVE_WINDOW_SEC,
) -> dict:
    """One-shot pure runner. Returns ``{"signals": [...], "stats": {...}}``."""
    indexed = index_latest_snapshots(snapshots)

    od = detect_outlier_and_dispersion_signals(
        indexed=indexed, min_books=min_books,
        outlier_z=outlier_z, dispersion_pp=dispersion_pp,
    )
    edge = detect_edge_signals(
        indexed=indexed, model_probs=model_probs or {},
        min_edge_pp=min_edge_pp, min_books=min_books,
    )

    # FAST_MOVE — needs the raw timeline, not the indexed map.
    by_match: dict = {}
    for s in snapshots or []:
        if not isinstance(s, dict):
            continue
        mid = s.get("match_id")
        if not mid:
            continue
        by_match.setdefault(str(mid), []).append(s)
    fast = detect_fast_move_signals(
        snapshots_by_match=by_match,
        window_seconds=fast_move_window_sec,
        fast_move_pp=fast_move_pp,
    )
    return {
        "signals": od + edge + fast,
        "stats": {
            "n_outlier":    sum(1 for s in od   if s["signal_type"] == SIG_OUTLIER),
            "n_dispersion": sum(1 for s in od   if s["signal_type"] == SIG_DISPERSION),
            "n_edge":       len(edge),
            "n_fast_move":  len(fast),
            "outcomes":     len(indexed),
        },
    }


__all__ = [
    "SIG_OUTLIER", "SIG_EDGE_VS_MODEL", "SIG_FAST_MOVE", "SIG_DISPERSION",
    "SEVERITY_LOW", "SEVERITY_MEDIUM", "SEVERITY_HIGH",
    "DEFAULT_MIN_BOOKS_FOR_CONSENSUS", "DEFAULT_OUTLIER_Z",
    "DEFAULT_MIN_EDGE_PP", "DEFAULT_DISPERSION_PP",
    "DEFAULT_FAST_MOVE_PP", "DEFAULT_FAST_MOVE_WINDOW_SEC",
    "index_latest_snapshots",
    "detect_outlier_and_dispersion_signals",
    "detect_edge_signals",
    "detect_fast_move_signals",
    "detect_all_signals",
]
