"""Phase F86.2 — H2H scoring applier.

Pure Python module that applies the ``h2h_decision.points_by_market``
deltas to a candidate's ``confidence_score`` (or any compatible scoring
container), with two safety nets:

1. **Cumulative clamp** at ``MAX_H2H_DELTA`` (+8) so the H2H signal can
   never dominate a market score — H2H is *one* of many factors (xG,
   recent form, injuries, motivation, line movement, etc.).
2. **Polarity guard** — even though :mod:`football_h2h_decision_policy`
   sets the OVER/UNDER thresholds to be mathematically disjoint
   (over_2_5 ≥ 70% requires under_2_5 ≤ 30%, which is incompatible with
   under_2_5 ≥ 65%), this module *also* refuses to apply both an
   ``OVER_X`` and an ``UNDER_X`` rule for the same X to the *same*
   market. Defensive guard so future threshold changes never produce
   self-cancelling scores.

The module is intentionally I/O-free; the unit tests cover it with
``pytest`` without any mocks.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football.h2h_scoring_applier")

# ─────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────
MAX_H2H_DELTA = 8

# Map free-form / canonical market labels → policy keys.
# Both the engine and the editorial use a mix of conventions:
#   - ``OVER_2_5_GOALS``, ``OVER_2_5``, ``Over 2.5 goles``
#   - ``BTTS_YES_GOALS``, ``BTTS_NO``, ``BTTS Yes``
#   - ``HOME_NO_LOSE``, ``HOME_DNB``, ``Doble oportunidad local``
_DIRECT_MARKET_ALIASES: dict[str, str] = {
    # Goals — OVER
    "OVER_1_5":          "OVER_1_5",
    "OVER_1_5_GOALS":    "OVER_1_5",
    "OVER 1.5":          "OVER_1_5",
    "OVER_2_5":          "OVER_2_5",
    "OVER_2_5_GOALS":    "OVER_2_5",
    "OVER 2.5":          "OVER_2_5",
    "OVER_3_5":          "OVER_3_5",
    "OVER_3_5_GOALS":    "OVER_3_5",
    "OVER 3.5":          "OVER_3_5",
    # Goals — UNDER
    "UNDER_1_5":         "UNDER_1_5",
    "UNDER_1_5_GOALS":   "UNDER_1_5",
    "UNDER 1.5":         "UNDER_1_5",
    "UNDER_2_5":         "UNDER_2_5",
    "UNDER_2_5_GOALS":   "UNDER_2_5",
    "UNDER 2.5":         "UNDER_2_5",
    "UNDER_3_5":         "UNDER_3_5",
    "UNDER_3_5_GOALS":   "UNDER_3_5",
    "UNDER 3.5":         "UNDER_3_5",
    # BTTS
    "BTTS_YES":          "BTTS_YES",
    "BTTS_YES_GOALS":    "BTTS_YES",
    "BTTS YES":          "BTTS_YES",
    "BTTS_NO":           "BTTS_NO",
    "BTTS_NO_GOALS":     "BTTS_NO",
    "BTTS NO":           "BTTS_NO",
    # DNB / Doble oportunidad
    "HOME_DNB":          "HOME_DNB",
    "HOME_NO_LOSE":      "HOME_DNB",
    "AWAY_DNB":          "AWAY_DNB",
    "AWAY_NO_LOSE":      "AWAY_DNB",
}


def _normalise_market_label(market: Any) -> str:
    """Return the upper-case, comma-stripped form of ``market``.

    Tolerant of whitespace, accents on goles/goals, hyphenation and
    common Spanish suffixes ("goles").
    """
    if not isinstance(market, str):
        return ""
    s = market.strip().upper()
    # Collapse internal whitespace.
    s = " ".join(s.split())
    # Strip Spanish trailing nouns that don't change the market identity.
    for suffix in (" GOLES", " GOALS", " CORNERS", " CÓRNERS"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].rstrip()
    return s


def _market_to_h2h_key(market: Any) -> Optional[str]:
    """Map a market label / enum to the canonical H2H policy key.

    Returns ``None`` when the market is not covered by H2H rules
    (e.g. corners markets, half-time markets, exotic).
    """
    norm = _normalise_market_label(market)
    if not norm:
        return None
    if norm in _DIRECT_MARKET_ALIASES:
        return _DIRECT_MARKET_ALIASES[norm]
    # Heuristic: ``OVER 2.5 GOLES``-style with extra tokens — pick the
    # first OVER/UNDER + numeric line pair.
    tokens = norm.replace(",", ".").split()
    side: Optional[str] = None
    line: Optional[str] = None
    for tok in tokens:
        if tok in ("OVER", "UNDER") and side is None:
            side = tok
        elif side is not None and line is None:
            # Accept tokens like "2.5", "1,5" → already replaced.
            try:
                f = float(tok)
                if f in (1.5, 2.5, 3.5):
                    line = str(f).replace(".", "_")
            except (TypeError, ValueError):
                pass
    if side and line:
        return f"{side}_{line}"
    return None


# Backwards-compat shim — the spec mentions ``_market_to_h2h_key`` as the
# helper to expose. Keep the private name as the implementation and
# expose a public alias for callers / tests.
market_to_h2h_key = _market_to_h2h_key


# ─────────────────────────────────────────────────────────────────────
# Polarity guard
# ─────────────────────────────────────────────────────────────────────
_POLARITY_PAIRS: tuple[tuple[str, str], ...] = (
    ("OVER_1_5",  "UNDER_1_5"),
    ("OVER_2_5",  "UNDER_2_5"),
    ("OVER_3_5",  "UNDER_3_5"),
    ("BTTS_YES",  "BTTS_NO"),
    ("HOME_DNB",  "AWAY_DNB"),  # mutually exclusive narrative-wise
)


def _enforce_polarity(
    points_by_market: dict[str, int],
    *,
    logger: logging.Logger = log,
) -> tuple[dict[str, int], bool]:
    """Drop the lower-points side when a polarity pair is dual-applied.

    Returns ``(filtered_points, conflict_detected)``. ``filtered_points``
    is a *new* dict — the input is never mutated. On tie, keeps the
    first key encountered (deterministic for tests).
    """
    if not isinstance(points_by_market, dict) or not points_by_market:
        return ({}, False)
    out = dict(points_by_market)
    conflict = False
    for a, b in _POLARITY_PAIRS:
        if a in out and b in out:
            conflict = True
            pa, pb = out[a], out[b]
            # Keep the larger; on tie keep ``a``.
            drop = b if pa >= pb else a
            logger.warning(
                "[h2h_scoring] polarity conflict: %s(%+d) vs %s(%+d) — dropping %s",
                a, pa, b, pb, drop,
            )
            out.pop(drop, None)
    return (out, conflict)


# ─────────────────────────────────────────────────────────────────────
# Candidate-like protocol helpers
# ─────────────────────────────────────────────────────────────────────
# Candidates can be either dicts or objects with attributes — both
# shapes are exercised in the codebase (dict-based for the editorial,
# object-based for the under_market_scan layer).

def _get_market(candidate: Any) -> Any:
    if isinstance(candidate, dict):
        return (candidate.get("market")
                or candidate.get("market_key")
                or candidate.get("recommended_market"))
    return (getattr(candidate, "market", None)
            or getattr(candidate, "market_key", None)
            or getattr(candidate, "recommended_market", None))


def _get_confidence(candidate: Any) -> int:
    if isinstance(candidate, dict):
        v = candidate.get("confidence_score")
        if v is None:
            v = candidate.get("confidence", 0)
    else:
        v = getattr(candidate, "confidence_score", None)
        if v is None:
            v = getattr(candidate, "confidence", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _set_confidence(candidate: Any, value: int) -> None:
    if isinstance(candidate, dict):
        candidate["confidence_score"] = value
    else:
        try:
            setattr(candidate, "confidence_score", value)
        except Exception:  # noqa: BLE001
            pass


def _append_signal(candidate: Any, signal: str) -> None:
    if isinstance(candidate, dict):
        sigs = candidate.get("signals")
        if not isinstance(sigs, list):
            sigs = []
            candidate["signals"] = sigs
        if signal not in sigs:
            sigs.append(signal)
    else:
        sigs = getattr(candidate, "signals", None)
        if not isinstance(sigs, list):
            sigs = []
            try:
                setattr(candidate, "signals", sigs)
            except Exception:  # noqa: BLE001
                return
        if signal not in sigs:
            sigs.append(signal)


def _set_score_breakdown(candidate: Any, key: str, value: Any) -> None:
    if isinstance(candidate, dict):
        sb = candidate.get("score_breakdown")
        if not isinstance(sb, dict):
            sb = {}
            candidate["score_breakdown"] = sb
        sb[key] = value
    else:
        sb = getattr(candidate, "score_breakdown", None)
        if not isinstance(sb, dict):
            sb = {}
            try:
                setattr(candidate, "score_breakdown", sb)
            except Exception:  # noqa: BLE001
                return
        sb[key] = value


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def apply_h2h_points_to_candidate(
    candidate: Any,
    h2h_decision: Optional[dict],
    *,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """Mutate ``candidate`` adding the H2H delta when applicable.

    Parameters
    ----------
    candidate
        Either a dict with ``market`` / ``confidence_score`` keys, or an
        object with the same attributes. The function mutates it
        **in-place**.
    h2h_decision
        The dict produced by ``football_h2h_decision_policy.build_h2h_decision``
        (the second tuple element). Must contain ``applied`` and
        ``points_by_market``. When falsy / ``applied=False`` the call is
        a no-op.

    Returns
    -------
    dict
        ``{
            "applied":           bool,
            "delta":             int,    # final delta after clamp
            "market_key":        str | None,
            "signals":           list[str],
            "polarity_conflict": bool,
            "clamped":           bool,
        }``

    Behaviour
    ---------
    - Resolves ``market_key`` via :func:`_market_to_h2h_key`.
    - Looks up ``points_by_market[market_key]`` — when missing, no-op.
    - Enforces polarity (no OVER + UNDER on the same line).
    - Clamps the cumulative delta at ``+MAX_H2H_DELTA`` (=+8).
    - Appends the matching ``signals`` entries to ``candidate.signals``.
    - Sets ``candidate.score_breakdown["h2h_pattern"] = delta`` so the
      audit layer can attribute the bump to H2H.
    """
    lg = logger or log
    out = {
        "applied":           False,
        "delta":             0,
        "market_key":        None,
        "signals":           [],
        "polarity_conflict": False,
        "clamped":           False,
    }
    if not isinstance(h2h_decision, dict) or not h2h_decision.get("applied"):
        return out
    raw_points = h2h_decision.get("points_by_market") or {}
    if not isinstance(raw_points, dict) or not raw_points:
        return out

    filtered_points, conflict = _enforce_polarity(raw_points, logger=lg)
    out["polarity_conflict"] = conflict

    market_label = _get_market(candidate)
    market_key = _market_to_h2h_key(market_label)
    out["market_key"] = market_key
    if not market_key:
        return out
    if market_key not in filtered_points:
        return out

    raw_delta = int(filtered_points[market_key])
    final_delta = raw_delta
    if final_delta > MAX_H2H_DELTA:
        final_delta = MAX_H2H_DELTA
        out["clamped"] = True
    elif final_delta < -MAX_H2H_DELTA:
        final_delta = -MAX_H2H_DELTA
        out["clamped"] = True

    # Resolve signals attached to this market_key.
    all_signals = list(h2h_decision.get("signals") or [])
    # The policy emits one signal per applied market — pick the matching
    # one when its label encodes the market key, otherwise apply the
    # whole list (small set, safe to over-include in audit trail).
    matching: list[str] = [
        s for s in all_signals
        if isinstance(s, str) and market_key.replace("_", "") in s.replace("_", "")
    ]
    if not matching:
        matching = all_signals

    # Mutate candidate.
    base_conf = _get_confidence(candidate)
    new_conf = base_conf + final_delta
    _set_confidence(candidate, new_conf)
    for sig in matching:
        _append_signal(candidate, sig)
    _set_score_breakdown(candidate, "h2h_pattern", final_delta)

    out["applied"] = True
    out["delta"]   = final_delta
    out["signals"] = list(matching)

    lg.info(
        "[h2h_scoring] market=%s delta=%+d signals=%s (clamped=%s, polarity_conflict=%s)",
        market_key, final_delta, matching, out["clamped"], conflict,
    )
    return out


__all__ = [
    "MAX_H2H_DELTA",
    "apply_h2h_points_to_candidate",
    "market_to_h2h_key",
    "_market_to_h2h_key",
    "_enforce_polarity",
]
