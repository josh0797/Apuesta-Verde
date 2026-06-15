"""Phase F92 — MLB Quality Contact Matchup signals applier.

Pure-Python integration layer between
:mod:`services.mlb_quality_contact_matchup` (computes the QCM block)
and the discovery / fragility / ranking layers that act on it. Mirrors
the F86.1 ``football_h2h_scoring_applier`` design: deltas, clamps,
polarity guards and env override.

Rules (per F92 user spec):
  * **Under penalty** when ``contact_mismatch_score`` is high.
      - Full-game Under: penalise whenever the mismatch crosses
        :data:`_DEFAULT_DELTAS["UNDER_FULL_GAME_THRESHOLD"]`.
      - F5 Under: penalise **only** when ``TOP_ORDER_THREAT`` is active
        (top-order sees more at-bats in the first 5 innings).
  * **Over boost** when ``CONTACT_EXPLOSION_POTENTIAL`` is active —
    that is, when ALL three conditions hold:
      - ``PITCHER_BARREL_REGRESSION_RISK`` signal present.
      - ``ERA_UNDERSTATES_DAMAGE`` signal present.
      - ``MATCHUP_CONTACT_ADVANTAGE`` (lineup_contact_quality high).
  * **Hard veto** is **NOT** applied here — only soft warnings + numeric
    deltas. Veto belongs to ``mlb_under_veto_layer``.
  * Clamps:
      - Single-candidate delta in ``[-MAX_UNDER_PENALTY, +MAX_OVER_BOOST]``.
      - Polarity guard: never apply both penalty and boost to the same
        candidate.

Output: a small structured dict applied in-place to the candidate,
plus an audit echo into ``candidate.score_breakdown.qcm_contact``.

Module is pure: no Mongo, no HTTP. Tested standalone.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

log = logging.getLogger("mlb.qcm_signals_applier")

# ─────────────────────────────────────────────────────────────────────
# Defaults (env-overridable via QCM_APPLIER_DELTAS JSON)
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_DELTAS: dict[str, float] = {
    "UNDER_FULL_GAME_THRESHOLD": 70.0,   # contact_mismatch_score ≥ 70 → trigger
    "UNDER_FULL_GAME_PENALTY":   -4,     # base penalty for full game Unders
    "UNDER_F5_PENALTY":          -3,     # smaller penalty for F5 Under (TOP_ORDER required)
    "UNDER_SEVERE_BONUS":        -2,     # extra when regression_risk == SEVERE
    "OVER_BASE_BOOST":           +3,     # base boost when CONTACT_EXPLOSION_POTENTIAL
    "OVER_TEAM_TOTAL_BOOST":     +4,     # team total markets get a bit more
    "MAX_UNDER_PENALTY":         -6,     # clamp (most negative allowed)
    "MAX_OVER_BOOST":            +5,     # clamp (most positive allowed)
    "HARD_VETO_MISMATCH":        85.0,   # used by veto layer (not by this applier)
}


def get_active_qcm_deltas() -> dict[str, float]:
    """Read ``QCM_APPLIER_DELTAS`` env JSON at call time (F86.1 pattern)."""
    raw = (os.environ.get("QCM_APPLIER_DELTAS") or "").strip()
    base = dict(_DEFAULT_DELTAS)
    if not raw:
        return base
    try:
        override = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.debug("[qcm_applier] override parse failed: %s — using defaults", exc)
        return base
    if not isinstance(override, dict):
        return base
    for k, v in override.items():
        if k not in base:
            log.debug("[qcm_applier] ignoring unknown key=%s", k)
            continue
        try:
            base[k] = float(v)
        except (TypeError, ValueError):
            log.debug("[qcm_applier] non-numeric override key=%s value=%r", k, v)
    return base


# ─────────────────────────────────────────────────────────────────────
# Reason codes / signals
# ─────────────────────────────────────────────────────────────────────
SIGNAL_UNDER_CONTACT_RISK         = "UNDER_CONTACT_RISK"
SIGNAL_CONTACT_EXPLOSION_POTENTIAL = "CONTACT_EXPLOSION_POTENTIAL"
RC_QCM_NO_DATA                    = "QCM_NO_DATA"
RC_QCM_NOT_APPLICABLE             = "QCM_NOT_APPLICABLE_MARKET"
RC_QCM_POLARITY_CONFLICT          = "QCM_POLARITY_CONFLICT"
RC_QCM_CLAMPED                    = "QCM_DELTA_CLAMPED"
RC_QCM_VETO_TRIGGERED             = "QCM_HARD_VETO_TRIGGERED"


# ─────────────────────────────────────────────────────────────────────
# Market parsing
# ─────────────────────────────────────────────────────────────────────
def _market_classification(market: Any) -> dict:
    """Detect whether a market is an Under/Over and Full-Game / F5.

    Returns ``{"side": "under"|"over"|None,
                "period": "full_game"|"f5"|None,
                "is_team_total": bool}``.
    """
    out = {"side": None, "period": None, "is_team_total": False}
    if not isinstance(market, str):
        return out
    s = market.upper().replace("-", "_").replace(" ", "_")
    if "UNDER" in s:
        out["side"] = "under"
    elif "OVER" in s:
        out["side"] = "over"
    # Period detection — common naming conventions: ``F5``, ``1H``,
    # ``FIRST_5``, ``FIRST_INNINGS_5``.
    if any(tok in s for tok in ("F5", "1H", "FIRST_5", "FIRST5", "FIRST_INNINGS_5")):
        out["period"] = "f5"
    elif out["side"]:
        out["period"] = "full_game"
    # Team total detection.
    if "TEAM_TOTAL" in s or "TT" in s.split("_"):
        out["is_team_total"] = True
    return out


def _has_signal(qcm_block: dict, signal: str) -> bool:
    if not isinstance(qcm_block, dict):
        return False
    return signal in (qcm_block.get("signals") or [])


# ─────────────────────────────────────────────────────────────────────
# Candidate accessors (dict OR object)
# ─────────────────────────────────────────────────────────────────────
def _get(c: Any, key: str, default: Any = None) -> Any:
    if isinstance(c, dict):
        return c.get(key, default)
    return getattr(c, key, default)


def _set(c: Any, key: str, value: Any) -> None:
    if isinstance(c, dict):
        c[key] = value
    else:
        try:
            setattr(c, key, value)
        except Exception:  # noqa: BLE001
            pass


def _get_confidence(c: Any) -> int:
    v = _get(c, "confidence_score")
    if v is None:
        v = _get(c, "confidence", 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _append_signal(c: Any, sig: str) -> None:
    sigs = _get(c, "signals")
    if not isinstance(sigs, list):
        sigs = []
        _set(c, "signals", sigs)
    if sig not in sigs:
        sigs.append(sig)


def _append_reason(c: Any, rc: str) -> None:
    rcs = _get(c, "reason_codes")
    if not isinstance(rcs, list):
        rcs = []
        _set(c, "reason_codes", rcs)
    if rc not in rcs:
        rcs.append(rc)


def _set_score_breakdown_entry(c: Any, key: str, value: Any) -> None:
    sb = _get(c, "score_breakdown")
    if not isinstance(sb, dict):
        sb = {}
        _set(c, "score_breakdown", sb)
    sb[key] = value


# ─────────────────────────────────────────────────────────────────────
# Core logic
# ─────────────────────────────────────────────────────────────────────
def _qcm_is_usable(qcm_block: Any) -> bool:
    if not isinstance(qcm_block, dict):
        return False
    if not qcm_block.get("available"):
        return False
    # Must have at least lineup_contact_quality and contact_mismatch_score.
    return (qcm_block.get("contact_mismatch_score") is not None
            and qcm_block.get("lineup_contact_quality") is not None)


def _contact_explosion_active(qcm_block: dict) -> bool:
    """Per spec: explicit composite — barrel risk + ERA understates +
    lineup contact advantage *all* present. The QCM module already
    emits the individual sub-signals; we compose them here.
    """
    sigs = set(qcm_block.get("signals") or [])
    return (
        "PITCHER_BARREL_REGRESSION_RISK" in sigs
        and "ERA_UNDERSTATES_DAMAGE"      in sigs
        and "MATCHUP_CONTACT_ADVANTAGE"   in sigs
    )


def _under_contact_risk_active(qcm_block: dict, *, deltas: dict) -> bool:
    mismatch = qcm_block.get("contact_mismatch_score")
    if not isinstance(mismatch, (int, float)):
        return False
    return mismatch >= deltas["UNDER_FULL_GAME_THRESHOLD"]


def _hard_veto_active(qcm_block: dict, *, deltas: dict) -> bool:
    """Used by ``mlb_under_veto_layer``. NOT applied here; surfaced for
    the veto layer's consumption."""
    mismatch = qcm_block.get("contact_mismatch_score")
    if not isinstance(mismatch, (int, float)):
        return False
    sigs = set(qcm_block.get("signals") or [])
    return (
        mismatch >= deltas["HARD_VETO_MISMATCH"]
        and qcm_block.get("regression_risk") == "SEVERE_REGRESSION_RISK"
        and "TOP_ORDER_THREAT" in sigs
    )


def _clamp(delta: float, deltas: dict) -> tuple[int, bool]:
    """Clamp a numeric delta in the configured min/max range. Returns
    ``(clamped_value, was_clamped)``.
    """
    max_pos = deltas["MAX_OVER_BOOST"]
    max_neg = deltas["MAX_UNDER_PENALTY"]
    if delta > max_pos:
        return (int(round(max_pos)), True)
    if delta < max_neg:
        return (int(round(max_neg)), True)
    return (int(round(delta)), False)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def apply_qcm_to_candidate(
    candidate: Any,
    qcm_block: Any,
    *,
    deltas: Optional[dict] = None,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """Mutate ``candidate`` with the QCM delta when applicable.

    Returns an audit dict::

        {
          "applied":          bool,
          "delta":            int,
          "side":             "under"|"over"|None,
          "period":           "full_game"|"f5"|None,
          "signal":           str | None,        # UNDER_CONTACT_RISK / CONTACT_EXPLOSION_POTENTIAL
          "reason_codes":     list[str],
          "polarity_conflict": bool,
          "clamped":          bool,
          "hard_veto_hint":   bool,              # for the veto layer
        }
    """
    lg = logger or log
    audit: dict = {
        "applied":           False,
        "delta":             0,
        "side":              None,
        "period":            None,
        "signal":            None,
        "reason_codes":      [],
        "polarity_conflict": False,
        "clamped":           False,
        "hard_veto_hint":    False,
    }

    if not _qcm_is_usable(qcm_block):
        audit["reason_codes"].append(RC_QCM_NO_DATA)
        return audit

    deltas = deltas or get_active_qcm_deltas()
    audit["hard_veto_hint"] = _hard_veto_active(qcm_block, deltas=deltas)

    market = _get(candidate, "market") or _get(candidate, "market_key")
    info = _market_classification(market)
    audit["side"]   = info["side"]
    audit["period"] = info["period"]
    if info["side"] is None:
        audit["reason_codes"].append(RC_QCM_NOT_APPLICABLE)
        return audit

    explosion = _contact_explosion_active(qcm_block)
    under_risk = _under_contact_risk_active(qcm_block, deltas=deltas)

    # Polarity guard: never both penalty and boost for the same
    # candidate. With distinct sides (Over vs Under) this can never
    # happen naturally — but if either gets misconfigured we drop both
    # numeric changes and emit RC_QCM_POLARITY_CONFLICT.
    if under_risk and explosion and info["side"] is None:
        audit["polarity_conflict"] = True
        audit["reason_codes"].append(RC_QCM_POLARITY_CONFLICT)
        return audit

    raw_delta = 0
    chosen_signal: Optional[str] = None

    if info["side"] == "under" and under_risk:
        if info["period"] == "full_game":
            raw_delta = deltas["UNDER_FULL_GAME_PENALTY"]
            chosen_signal = SIGNAL_UNDER_CONTACT_RISK
        elif info["period"] == "f5":
            # F5 ONLY when top-order threat is active.
            if "TOP_ORDER_THREAT" in (qcm_block.get("signals") or []):
                raw_delta = deltas["UNDER_F5_PENALTY"]
                chosen_signal = SIGNAL_UNDER_CONTACT_RISK
        # Severity bonus on top of the base penalty.
        if (chosen_signal == SIGNAL_UNDER_CONTACT_RISK
                and qcm_block.get("regression_risk") == "SEVERE_REGRESSION_RISK"):
            raw_delta += deltas["UNDER_SEVERE_BONUS"]
    elif info["side"] == "over" and explosion:
        base = deltas["OVER_BASE_BOOST"]
        if info["is_team_total"]:
            base = deltas["OVER_TEAM_TOTAL_BOOST"]
        raw_delta = base
        chosen_signal = SIGNAL_CONTACT_EXPLOSION_POTENTIAL

    if raw_delta == 0 or chosen_signal is None:
        return audit

    final_delta, was_clamped = _clamp(raw_delta, deltas)
    if was_clamped:
        audit["clamped"] = True
        audit["reason_codes"].append(RC_QCM_CLAMPED)

    # Mutate the candidate.
    base_conf = _get_confidence(candidate)
    new_conf  = base_conf + final_delta
    _set(candidate, "confidence_score", new_conf)
    # Mirror to legacy ``confidence`` key when present.
    if isinstance(candidate, dict) and "confidence" in candidate:
        candidate["confidence"] = new_conf
    _append_signal(candidate, chosen_signal)
    _append_reason(candidate, chosen_signal)
    _set_score_breakdown_entry(candidate, "qcm_contact", {
        "delta":            final_delta,
        "signal":           chosen_signal,
        "side":             info["side"],
        "period":           info["period"],
        "mismatch_score":   qcm_block.get("contact_mismatch_score"),
        "regression_risk":  qcm_block.get("regression_risk"),
        "clamped":          was_clamped,
        "hard_veto_hint":   audit["hard_veto_hint"],
    })

    audit["applied"] = True
    audit["delta"]   = final_delta
    audit["signal"]  = chosen_signal
    audit["reason_codes"].append(chosen_signal)

    lg.info(
        "[qcm_applier] market=%s side=%s period=%s signal=%s delta=%+d "
        "mismatch=%.1f regression=%s hard_veto_hint=%s",
        market, info["side"], info["period"], chosen_signal, final_delta,
        float(qcm_block.get("contact_mismatch_score") or 0.0),
        qcm_block.get("regression_risk"), audit["hard_veto_hint"],
    )
    return audit


def apply_qcm_to_candidates(
    candidates: list[Any],
    qcm_block: Any,
    *,
    deltas: Optional[dict] = None,
) -> list[dict]:
    """Convenience batch helper. Returns the list of audit dicts."""
    if not isinstance(candidates, list):
        return []
    deltas = deltas or get_active_qcm_deltas()
    return [apply_qcm_to_candidate(c, qcm_block, deltas=deltas)
            for c in candidates]


def qcm_hard_veto_active(qcm_block: Any) -> bool:
    """Surfaces the hard-veto condition for ``mlb_under_veto_layer``."""
    if not _qcm_is_usable(qcm_block):
        return False
    return _hard_veto_active(qcm_block, deltas=get_active_qcm_deltas())


__all__ = [
    "SIGNAL_UNDER_CONTACT_RISK",
    "SIGNAL_CONTACT_EXPLOSION_POTENTIAL",
    "RC_QCM_NO_DATA", "RC_QCM_NOT_APPLICABLE",
    "RC_QCM_POLARITY_CONFLICT", "RC_QCM_CLAMPED", "RC_QCM_VETO_TRIGGERED",
    "get_active_qcm_deltas",
    "apply_qcm_to_candidate",
    "apply_qcm_to_candidates",
    "qcm_hard_veto_active",
]
