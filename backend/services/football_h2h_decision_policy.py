"""Phase F86 + F86.1 — H2H Decision Policy (calibrated).

This module defines **when** the head-to-head context may influence the
engine's scoring and **how much** weight it carries. It is purely
analytical (no Mongo, no httpx) so it can be exercised with ``pytest``
without any external dependency.

What changed with F86.1
-----------------------
* Recalibrated ``H2H_POINT_RULES`` thresholds against typical baselines
  for football matches; each rule now carries ``baseline`` (documentation
  only) and ``min_sample`` (effective).
* :func:`get_active_rules` allows an env-var driven override
  (``H2H_POINT_RULES_OVERRIDE`` as JSON) and is read at call time so
  ``pytest.monkeypatch`` works without module reload.
* :func:`apply_polarity_guard` makes the OVER/UNDER + BTTS_YES/BTTS_NO
  conflict explicit: if both fire the rule with the higher *rate* wins
  (tie → higher points; second tie → ``H2H_POLARITY_UNRESOLVED``).
* DNB overlap (``HOME_DNB`` + ``AWAY_DNB``) is treated as a **soft**
  conflict — neither side is dropped; we emit
  ``H2H_DNB_OVERLAP_DRAW_HEAVY`` and a ``soft_conflicts`` entry.
* :data:`MAX_H2H_POINTS_TOTAL` caps the *aggregated* H2H influence per
  match so H2H can never dominate scoring (signals are preserved).
* Per-rule ``min_sample`` introduces a halved-points + ``LOW_SAMPLE_H2H_SIGNAL``
  path when the recent sample is below the rule's preferred sample size.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

log = logging.getLogger("football.h2h_decision_policy")

# ─────────────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────────────

# Muestra mínima para que el H2H influya en decisión (gate global).
# Reglas individuales (``min_sample``) pueden requerir MÁS muestra; en ese
# caso emiten ``LOW_SAMPLE_H2H_SIGNAL`` y otorgan puntos a la mitad.
MIN_DECISION_SAMPLE = 4

# Edad máxima en días para considerar un partido "recencia útil".
MAX_RECENT_DAYS = 365

# Aggregated cap on the total H2H influence per match. The signals are
# preserved, but the numeric impact reported in ``h2h_points_total`` is
# clamped so H2H stays as a *secondary* factor.
MAX_H2H_POINTS_TOTAL = 8

# Tabla de reglas de puntos H2H (defaults).
# Cada regla expone:
#   * ``min_rate``   — umbral efectivo para que la señal dispare.
#   * ``points``     — puntos asignados al mercado (≤ 5 por convención).
#   * ``baseline``   — referencia de tasa base esperada (DOCUMENTACIÓN
#                       únicamente; no se usa en la lógica de decisión).
#   * ``min_sample`` — sample recent mínimo recomendado; por debajo se
#                       aplica puntuación parcial + ``LOW_SAMPLE_H2H_SIGNAL``.
#   * ``label``      — código de señal emitido en ``signals``.
H2H_POINT_RULES: dict[str, dict] = {
    "OVER_1_5": {
        "min_rate":   0.90,
        "points":     3,
        "baseline":   0.78,
        "min_sample": 4,
        "label":      "H2H_OVER_1_5_STRONG",
    },
    "UNDER_1_5": {
        "min_rate":   0.50,
        "points":     5,
        "baseline":   0.22,
        "min_sample": 4,
        "label":      "H2H_UNDER_1_5_STRONG",
    },
    "OVER_2_5": {
        "min_rate":   0.75,
        "points":     4,
        "baseline":   0.55,
        "min_sample": 4,
        "label":      "H2H_OVER_2_5_STRONG",
    },
    "UNDER_2_5": {
        "min_rate":   0.70,
        "points":     4,
        "baseline":   0.45,
        "min_sample": 4,
        "label":      "H2H_UNDER_2_5_STRONG",
    },
    "OVER_3_5": {
        "min_rate":   0.65,
        "points":     5,
        "baseline":   0.32,
        "min_sample": 4,
        "label":      "H2H_OVER_3_5_STRONG",
    },
    "UNDER_3_5": {
        "min_rate":   0.80,
        "points":     5,
        "baseline":   0.68,
        "min_sample": 4,
        "label":      "H2H_UNDER_3_5_STRONG",
    },
    "BTTS_YES": {
        "min_rate":   0.70,
        "points":     4,
        "baseline":   0.52,
        "min_sample": 4,
        "label":      "H2H_BTTS_YES_STRONG",
    },
    "BTTS_NO": {
        "min_rate":   0.70,
        "points":     5,
        "baseline":   0.48,
        "min_sample": 4,
        "label":      "H2H_BTTS_NO_STRONG",
    },
    "HOME_DNB": {
        "min_rate":   0.85,
        "points":     4,
        "baseline":   0.75,
        "min_sample": 4,
        "label":      "H2H_HOME_DNB_STRONG",
    },
    "AWAY_DNB": {
        "min_rate":   0.70,
        "points":     4,
        "baseline":   0.45,
        "min_sample": 4,
        "label":      "H2H_AWAY_DNB_STRONG",
    },
}

# Polarity pairs — hard conflicts that cancel each other on a single line.
# DNB pair is intentionally OUT of this list: HOME_DNB + AWAY_DNB jointly
# describe a draw-heavy profile (legitimate H2H pattern, not a conflict).
POLARITY_PAIRS: list[tuple[str, str]] = [
    ("OVER_1_5", "UNDER_1_5"),
    ("OVER_2_5", "UNDER_2_5"),
    ("OVER_3_5", "UNDER_3_5"),
    ("BTTS_YES", "BTTS_NO"),
]
DNB_OVERLAP_PAIR: tuple[str, str] = ("HOME_DNB", "AWAY_DNB")

# Reason codes (machine-readable, surfaced in ``decision.reason_codes``).
RC_NO_SAMPLE                = "H2H_NO_SAMPLE"
RC_SAMPLE_BELOW_THRESHOLD   = "H2H_SAMPLE_BELOW_DECISION_THRESHOLD"
RC_RECENT_BELOW_THRESHOLD   = "H2H_RECENT_SAMPLE_BELOW_THRESHOLD"
RC_DECISION_USEFUL          = "H2H_DECISION_USEFUL"
RC_LOW_SAMPLE_SIGNAL        = "LOW_SAMPLE_H2H_SIGNAL"
RC_POLARITY_GUARD_TRIGGERED = "H2H_POLARITY_GUARD_TRIGGERED"
RC_POLARITY_UNRESOLVED      = "H2H_POLARITY_UNRESOLVED"
RC_DNB_OVERLAP_DRAW_HEAVY   = "H2H_DNB_OVERLAP_DRAW_HEAVY"
RC_POINTS_CAPPED            = "H2H_POINTS_CAPPED"


# ─────────────────────────────────────────────────────────────────────
# Helpers privados (parseo / fechas / scores)
# ─────────────────────────────────────────────────────────────────────
def _parse_iso(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _is_recent(date_str: Any, *, max_days: int = MAX_RECENT_DAYS) -> bool:
    d = _parse_iso(date_str)
    if d is None:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d) <= timedelta(days=max_days)


def _score_pair(score_str: Any) -> Optional[tuple[int, int]]:
    """Parse '2-1' / '0-0' / 'NaN-1' robustly. Returns None on failure."""
    if not isinstance(score_str, str) or "-" not in score_str:
        return None
    try:
        h, a = score_str.split("-", 1)
        return int(h.strip()), int(a.strip())
    except Exception:  # noqa: BLE001
        return None


def _total_goals(m: dict) -> Optional[int]:
    p = _score_pair(m.get("score"))
    return None if p is None else (p[0] + p[1])


def _side_of(m: dict, team_name: str) -> Optional[str]:
    """Was ``team_name`` home or away in this match?"""
    if not team_name:
        return None
    tn = team_name.strip().lower()
    home_name = (m.get("home") or "").strip().lower()
    away_name = (m.get("away") or "").strip().lower()
    if tn == home_name:
        return "home"
    if tn == away_name:
        return "away"
    return None


def _rate_of(matches: list[dict], predicate: Callable[[dict], bool]) -> float:
    if not matches:
        return 0.0
    return sum(1 for m in matches if predicate(m)) / len(matches)


# ─────────────────────────────────────────────────────────────────────
# Active rules (env-overridable)
# ─────────────────────────────────────────────────────────────────────
def get_active_rules() -> dict:
    """Return :data:`H2H_POINT_RULES` merged with an env-var override.

    Reads ``os.environ["H2H_POINT_RULES_OVERRIDE"]`` **at call time** so
    that tests using ``monkeypatch.setenv`` work without reloading the
    module. The override must be a JSON object of the form::

        {"OVER_2_5": {"min_rate": 0.80, "points": 5}, ...}

    Unknown markets and invalid shapes are logged at WARNING and ignored.
    On JSON parse failure the function falls back to the defaults.
    """
    raw = (os.environ.get("H2H_POINT_RULES_OVERRIDE") or "").strip()
    if not raw:
        return {k: dict(v) for k, v in H2H_POINT_RULES.items()}
    try:
        override = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("[h2h_rules] override parse failed: %s — using defaults", exc)
        return {k: dict(v) for k, v in H2H_POINT_RULES.items()}

    merged: dict[str, dict] = {k: dict(v) for k, v in H2H_POINT_RULES.items()}
    if not isinstance(override, dict):
        log.warning("[h2h_rules] override must be a JSON object; got %s", type(override).__name__)
        return merged

    for market, rule_override in override.items():
        if market not in merged:
            log.warning("[h2h_rules] ignoring unknown override market=%s", market)
            continue
        if not isinstance(rule_override, dict):
            log.warning("[h2h_rules] invalid override for market=%s (expected dict, got %s)",
                        market, type(rule_override).__name__)
            continue
        merged[market].update(rule_override)
    return merged


# ─────────────────────────────────────────────────────────────────────
# classify_h2h_context (unchanged API)
# ─────────────────────────────────────────────────────────────────────
def classify_h2h_context(
    h2h_context: dict | None,
    h2h_recent: list[dict] | None,
) -> dict:
    """Annotate the H2H context with decision-policy fields.

    The input ``h2h_context`` is preserved (shallow-copied + extended).
    """
    matches = h2h_recent or []
    recent  = [m for m in matches if _is_recent(m.get("date"))]
    sample_total  = len(matches)
    sample_recent = len(recent)

    warnings: list[str] = []
    reason_codes: list[str] = []

    if sample_total == 0:
        warnings.append("Sin enfrentamientos directos registrados.")
        reason_codes.append(RC_NO_SAMPLE)
        decision_useful = False
    elif sample_total < MIN_DECISION_SAMPLE:
        warnings.append(
            f"Solo se registran {sample_total} enfrentamientos directos — "
            "muestra limitada, contexto pero no fuente primaria."
        )
        reason_codes.append(RC_SAMPLE_BELOW_THRESHOLD)
        decision_useful = False
    elif sample_recent < MIN_DECISION_SAMPLE:
        warnings.append(
            f"{sample_total} enfrentamientos totales, pero solo {sample_recent} "
            f"en los últimos {MAX_RECENT_DAYS // 30} meses — "
            "contexto histórico, no afecta decisión."
        )
        reason_codes.append(RC_RECENT_BELOW_THRESHOLD)
        decision_useful = False
    else:
        reason_codes.append(RC_DECISION_USEFUL)
        decision_useful = True

    out = dict(h2h_context or {})
    out["recent_matches"]     = matches
    out["recent_within_1y"]   = recent
    out["sample_size_total"]  = sample_total
    out["sample_size_recent"] = sample_recent
    out["decision_useful"]    = decision_useful
    out["warnings"]           = warnings
    existing_codes = list(out.get("reason_codes") or [])
    out["reason_codes"]       = existing_codes + [
        c for c in reason_codes if c not in existing_codes
    ]
    return out


# ─────────────────────────────────────────────────────────────────────
# Polarity guard (hard conflicts)
# ─────────────────────────────────────────────────────────────────────
def apply_polarity_guard(
    out: dict,
    market_to_rate: dict,
    active_rules: dict,
) -> dict:
    """Drop the losing side when an OVER_X / UNDER_X or BTTS_YES / NO pair
    is dual-applied.

    Tie-breaking order:
      1. Higher *rate* wins.
      2. Higher *points* wins.
      3. ``H2H_POLARITY_UNRESOLVED`` reason code; signals removed from
         BOTH sides but ``polarity_conflicts`` still recorded.

    Mutates ``out`` in place and returns it for chaining.
    """
    points: dict = dict(out.get("points_by_market") or {})
    signals: list = list(out.get("signals") or [])
    conflicts: list[dict] = []
    triggered = False

    for a, b in POLARITY_PAIRS:
        if a not in points or b not in points:
            continue
        triggered = True
        rate_a = float(market_to_rate.get(a, 0) or 0)
        rate_b = float(market_to_rate.get(b, 0) or 0)
        pts_a  = int(active_rules.get(a, {}).get("points", 0) or 0)
        pts_b  = int(active_rules.get(b, {}).get("points", 0) or 0)

        if rate_a > rate_b:
            loser, winner = b, a
        elif rate_b > rate_a:
            loser, winner = a, b
        elif pts_a > pts_b:
            loser, winner = b, a
        elif pts_b > pts_a:
            loser, winner = a, b
        else:
            loser, winner = None, None

        conflict_entry = {
            "a":          a,
            "b":          b,
            "rate_a":     rate_a,
            "rate_b":     rate_b,
            "resolution": "DROP_LOSER" if loser else "UNRESOLVED",
        }
        conflicts.append(conflict_entry)

        if loser:
            loser_label = active_rules.get(loser, {}).get("label")
            points.pop(loser, None)
            if loser_label:
                signals = [s for s in signals if s != loser_label]
            log.warning(
                "[h2h_polarity_guard] conflict %s(%.2f) vs %s(%.2f) — "
                "dropping %s (winner=%s). Review thresholds.",
                a, rate_a, b, rate_b, loser, winner,
            )
        else:
            # Unresolved: drop BOTH points but keep the entries in
            # conflicts for audit. Add reason code.
            label_a = active_rules.get(a, {}).get("label")
            label_b = active_rules.get(b, {}).get("label")
            points.pop(a, None)
            points.pop(b, None)
            signals = [s for s in signals if s not in (label_a, label_b)]
            log.warning(
                "[h2h_polarity_guard] unresolved conflict %s(%.2f) vs %s(%.2f) — "
                "dropping both. Review thresholds.",
                a, rate_a, b, rate_b,
            )
            out.setdefault("reason_codes", [])
            if RC_POLARITY_UNRESOLVED not in out["reason_codes"]:
                out["reason_codes"].append(RC_POLARITY_UNRESOLVED)

    out["points_by_market"] = points
    out["signals"]          = signals
    if triggered:
        out["polarity_conflicts"] = conflicts
        out.setdefault("reason_codes", [])
        if RC_POLARITY_GUARD_TRIGGERED not in out["reason_codes"]:
            out["reason_codes"].append(RC_POLARITY_GUARD_TRIGGERED)
    return out


# ─────────────────────────────────────────────────────────────────────
# DNB overlap (soft conflict)
# ─────────────────────────────────────────────────────────────────────
def apply_dnb_overlap_guard(out: dict) -> dict:
    """Annotate DNB overlap as a *soft* conflict — keep both points, but
    cap their combined contribution at 4 to reflect that the underlying
    pattern is "draw-heavy / low margin" rather than two independent
    dominant teams.
    """
    points = out.get("points_by_market") or {}
    a, b = DNB_OVERLAP_PAIR
    if a not in points or b not in points:
        return out
    out.setdefault("reason_codes", [])
    if RC_DNB_OVERLAP_DRAW_HEAVY not in out["reason_codes"]:
        out["reason_codes"].append(RC_DNB_OVERLAP_DRAW_HEAVY)
    out.setdefault("soft_conflicts", []).append({
        "type":           "DNB_OVERLAP",
        "markets":        [a, b],
        "interpretation": ("Draw-heavy or low-margin H2H profile; "
                           "do not treat as hard polarity conflict."),
    })
    # Cap combined contribution at 4 (both at 2 each, preserving ratio).
    total = int(points[a]) + int(points[b])
    if total > 4:
        ratio_a = int(points[a]) / total
        new_a   = max(1, math.floor(4 * ratio_a))
        new_b   = max(1, 4 - new_a)
        out.setdefault("soft_conflict_adjustments", {})
        out["soft_conflict_adjustments"][a] = {"from": points[a], "to": new_a}
        out["soft_conflict_adjustments"][b] = {"from": points[b], "to": new_b}
        points[a] = new_a
        points[b] = new_b
        out["points_by_market"] = points
    return out


# ─────────────────────────────────────────────────────────────────────
# Total cap (preserve signals, clamp numeric impact)
# ─────────────────────────────────────────────────────────────────────
def apply_total_cap(out: dict) -> dict:
    """Cap the aggregated H2H points at :data:`MAX_H2H_POINTS_TOTAL`.

    Signals are preserved; only the reported ``h2h_points_total`` is
    clamped. ``h2h_points_uncapped`` stays available for audit.
    """
    points = out.get("points_by_market") or {}
    try:
        total = sum(int(v) for v in points.values())
    except (TypeError, ValueError):
        total = 0
    if total > MAX_H2H_POINTS_TOTAL:
        out["h2h_points_uncapped"] = total
        out["h2h_points_total"]    = MAX_H2H_POINTS_TOTAL
        out.setdefault("reason_codes", [])
        if RC_POINTS_CAPPED not in out["reason_codes"]:
            out["reason_codes"].append(RC_POINTS_CAPPED)
    else:
        out["h2h_points_total"] = total
    return out


# ─────────────────────────────────────────────────────────────────────
# apply_h2h_decision_points (rewritten to use active rules + guards)
# ─────────────────────────────────────────────────────────────────────
def apply_h2h_decision_points(
    classified: dict,
    home_name: str,
    away_name: str,
) -> dict:
    """Compute per-market points contributed by H2H.

    The function now reads thresholds from :func:`get_active_rules`
    (env-overridable), enforces polarity / DNB / total caps and emits
    ``LOW_SAMPLE_H2H_SIGNAL`` + halved points when the recent sample is
    below the rule's ``min_sample``.
    """
    out: dict = {
        "points_by_market":  {},
        "signals":           [],
        "applied":           False,
        "rates":             {},
        "sample_size":       0,
        "reason_codes":      [],
        "h2h_points_total":  0,
    }

    if not classified.get("decision_useful"):
        return out

    recent = classified.get("recent_within_1y") or []
    if not recent:
        return out

    # Tasa por mercado (siempre se computa, incluso si no dispara).
    rates = {
        "over_1_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 2),
        "over_2_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 3),
        "over_3_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 4),
        "under_1_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 1),
        "under_2_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 2),
        "under_3_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 3),
    }

    def _btts(m: dict) -> bool:
        p = _score_pair(m.get("score"))
        return p is not None and p[0] >= 1 and p[1] >= 1
    rates["btts_yes"] = _rate_of(recent, _btts)
    rates["btts_no"]  = 1.0 - rates["btts_yes"] if recent else 0.0

    def _team_did_not_lose(m: dict, team: str) -> bool:
        p = _score_pair(m.get("score"))
        if p is None:
            return False
        side = _side_of(m, team)
        if side == "home":
            return p[0] >= p[1]
        if side == "away":
            return p[1] >= p[0]
        return False

    rates["home_dnb"] = _rate_of(recent, lambda m: _team_did_not_lose(m, home_name))
    rates["away_dnb"] = _rate_of(recent, lambda m: _team_did_not_lose(m, away_name))

    market_to_rate = {
        "OVER_1_5":  rates["over_1_5"],
        "OVER_2_5":  rates["over_2_5"],
        "OVER_3_5":  rates["over_3_5"],
        "UNDER_1_5": rates["under_1_5"],
        "UNDER_2_5": rates["under_2_5"],
        "UNDER_3_5": rates["under_3_5"],
        "BTTS_YES":  rates["btts_yes"],
        "BTTS_NO":   rates["btts_no"],
        "HOME_DNB":  rates["home_dnb"],
        "AWAY_DNB":  rates["away_dnb"],
    }

    active_rules = get_active_rules()
    sample_size  = len(recent)
    low_sample_markets: list[str] = []

    for market, rule in active_rules.items():
        rate = float(market_to_rate.get(market, 0.0))
        try:
            min_rate = float(rule.get("min_rate", 1.0))
        except (TypeError, ValueError):
            min_rate = 1.0
        if rate < min_rate:
            continue
        try:
            full_points = int(rule.get("points", 0))
        except (TypeError, ValueError):
            full_points = 0
        try:
            rule_min_sample = int(rule.get("min_sample", MIN_DECISION_SAMPLE))
        except (TypeError, ValueError):
            rule_min_sample = MIN_DECISION_SAMPLE

        # Per-rule sample guard: when sample < rule.min_sample we still
        # *emit* the signal (so the editorial sees the pattern) but at
        # half the points, and we surface LOW_SAMPLE_H2H_SIGNAL.
        if sample_size < rule_min_sample:
            applied_points = max(1, full_points // 2)
            low_sample_markets.append(market)
        else:
            applied_points = full_points

        out["points_by_market"][market] = applied_points
        label = rule.get("label")
        if isinstance(label, str) and label and label not in out["signals"]:
            out["signals"].append(label)

    if low_sample_markets:
        if RC_LOW_SAMPLE_SIGNAL not in out["reason_codes"]:
            out["reason_codes"].append(RC_LOW_SAMPLE_SIGNAL)
        out["low_sample_markets"] = low_sample_markets

    out["rates"]       = rates
    out["sample_size"] = sample_size
    out["applied"]     = True

    # Hard polarity guard first (drops conflicting signals).
    apply_polarity_guard(out, market_to_rate, active_rules)
    # DNB soft overlap (keeps both, caps combined).
    apply_dnb_overlap_guard(out)
    # Aggregated cap (preserves signals).
    apply_total_cap(out)

    return out


# ─────────────────────────────────────────────────────────────────────
# build_h2h_decision (unchanged signature)
# ─────────────────────────────────────────────────────────────────────
def build_h2h_decision(match_doc: dict) -> tuple[dict, dict]:
    """Convenience wrapper used by the ingestor.

    Returns ``(classified_context, decision_payload)``.
    """
    classified = classify_h2h_context(
        match_doc.get("h2h_context") or {},
        match_doc.get("h2h_recent")  or [],
    )
    decision = apply_h2h_decision_points(
        classified,
        home_name=(match_doc.get("home_team", {}) or {}).get("name") or "",
        away_name=(match_doc.get("away_team", {}) or {}).get("name") or "",
    )
    return classified, decision


__all__ = [
    "classify_h2h_context",
    "apply_h2h_decision_points",
    "apply_polarity_guard",
    "apply_dnb_overlap_guard",
    "apply_total_cap",
    "build_h2h_decision",
    "get_active_rules",
    "MIN_DECISION_SAMPLE", "MAX_RECENT_DAYS", "MAX_H2H_POINTS_TOTAL",
    "H2H_POINT_RULES", "POLARITY_PAIRS", "DNB_OVERLAP_PAIR",
    "RC_NO_SAMPLE", "RC_SAMPLE_BELOW_THRESHOLD",
    "RC_RECENT_BELOW_THRESHOLD", "RC_DECISION_USEFUL",
    "RC_LOW_SAMPLE_SIGNAL", "RC_POLARITY_GUARD_TRIGGERED",
    "RC_POLARITY_UNRESOLVED", "RC_DNB_OVERLAP_DRAW_HEAVY",
    "RC_POINTS_CAPPED",
]
