"""Signal aggregator — sport-aware unification of editorial / trap / market /
historical / live signals across the analyst pipeline.

Goal
====
The user wants to see, on EVERY analyzed match (recommended OR discarded),
the same canonical `editorial_context_signals` list so they can:

  • Audit why the engine recommended (or didn't recommend) a pick.
  • Spot opportunities that the engine refused to take but they
    personally want to explore.

Where signals come from
=======================
The pipeline already emits structured-ish signals in several places, but
they use different shapes:

    1. `trap_signals_structured`        moneyball_layer / alternative_rescue
    2. `editorial_context.signals`      editorial_normalizer (Scrapy / PW / BD)
    3. `editorial_context.contradiction_flags`
    4. `form_guard.signals`             form_guard
    5. `protectedMarketContext`         alternative_rescue (PROTECTED_*)
    6. trap_signals (legacy strings)    backwards-compat

This aggregator collects from all of them, canonicalises through
`signal_catalog.make_signal()`, deduplicates by `code`, **filters by
sport using the catalog's `applicable_sports`**, and sorts by severity.

The output is a single list of canonical dicts (see signal_catalog for
the shape) that the UI can render uniformly.

The aggregator is intentionally a pure function — no I/O, no DB calls —
so it can be unit-tested in isolation.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from .signal_catalog import (
    SIGNAL_CATALOG,
    make_signal,
    is_known_code,
)

log = logging.getLogger("signal_aggregator")

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ────────────────────────────────────────────────────────────────────────────
# Source-specific extractors. Each one takes the raw match/pick payload and
# yields canonical signal dicts. Unknown / sport-mismatched codes are
# silently dropped by `make_signal`.
# ────────────────────────────────────────────────────────────────────────────
def _from_trap_signals_structured(payload: dict, sport: str) -> Iterable[dict]:
    """Trap signals already use canonical codes — just re-wrap them
    through `make_signal` to enforce sport gating + add category/type/impact.
    """
    raw_list = payload.get("trap_signals_structured") or []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not code or not is_known_code(code):
            continue
        sig = make_signal(
            code,
            sport=sport,
            extra_explanation=item.get("extra_explanation") or "",
        )
        if sig is None:
            continue
        # The legacy emitter sometimes overrides severity; respect that
        # only when it's a stricter level (catalog is otherwise the
        # source of truth).
        legacy_sev = item.get("severity")
        if legacy_sev in _SEVERITY_ORDER and _SEVERITY_ORDER[legacy_sev] < _SEVERITY_ORDER.get(sig["severity"], 99):
            sig["severity"] = legacy_sev
        yield sig


def _from_editorial_context(payload: dict, sport: str) -> Iterable[dict]:
    """Editorial layer emits semi-structured `signals` with `signal_type`
    in {OPINION, MARKET_SUGGESTION, INJURY_REPORT, MOTIVATION_NOTE,
    SCORE_PREDICTION, CONTRADICTION}.

    We map editorial signal_types to our canonical catalog codes.
    """
    ed = payload.get("_editorial_context") or payload.get("editorial_context") or {}
    if not isinstance(ed, dict):
        return
    if not ed.get("available"):
        return

    for s in ed.get("signals") or []:
        if not isinstance(s, dict):
            continue
        stype = (s.get("signal_type") or "").upper()
        conf = int(round((s.get("confidence") or 0.5) * 100))
        if stype == "MARKET_SUGGESTION":
            sig = make_signal("EDITORIAL_MARKET_SUGGESTION",
                              sport=sport,
                              confidence=conf,
                              extra_explanation=(s.get("text") or "")[:140])
        elif stype == "INJURY_REPORT":
            sig = make_signal("EDITORIAL_INJURY_NOTE",
                              sport=sport,
                              confidence=conf,
                              extra_explanation=(s.get("text") or "")[:140])
        elif stype == "MOTIVATION_NOTE":
            sig = make_signal("EDITORIAL_MOTIVATION_NOTE",
                              sport=sport,
                              confidence=conf,
                              extra_explanation=(s.get("text") or "")[:140])
        else:
            sig = None
        if sig:
            yield sig

    if ed.get("contradiction_flags"):
        sig = make_signal("EDITORIAL_CONTRADICTION", sport=sport,
                          confidence=70,
                          extra_explanation=", ".join((ed.get("contradiction_flags") or [])[:3])[:140])
        if sig:
            yield sig


def _from_form_guard(payload: dict, sport: str) -> Iterable[dict]:
    fg = payload.get("_form_guard") or payload.get("form_guard") or {}
    if not isinstance(fg, dict):
        return
    for s in fg.get("signals") or []:
        if not isinstance(s, dict):
            continue
        if (s.get("severity") or "").lower() in ("critical", "warn"):
            sig = make_signal(
                "FORM_CRITICAL_STREAK",
                sport=sport,
                confidence=int(s.get("confidence") or 75),
                extra_explanation=(s.get("text") or s.get("reason") or "")[:140],
            )
            if sig:
                yield sig


def _from_protected_market(payload: dict, sport: str) -> Iterable[dict]:
    """If the alternative_rescue layer found a protected market for this
    match (PROTECTED_ACCEPTABLE / RESCUE_*), surface it as a positive
    signal."""
    rescue_kind = (
        payload.get("rescueType")
        or (payload.get("_alternative_rescue") or {}).get("rescue_type")
    )
    if rescue_kind:
        sig = make_signal("PROTECTED_MARKET_AVAILABLE", sport=sport,
                          confidence=70,
                          extra_explanation=str(rescue_kind))
        if sig:
            yield sig
    # Low fragility signal — surfaced when fragilityScore < 30
    frag = payload.get("fragilityScore")
    if isinstance(frag, (int, float)) and 0 <= frag < 30:
        sig = make_signal("LOW_FRAGILITY_MARKET", sport=sport,
                          confidence=75,
                          extra_explanation=f"fragility={int(frag)}")
        if sig:
            yield sig


def _from_historical_patterns(payload: dict, sport: str) -> Iterable[dict]:
    """Patterns from the encounter/historical enrichment layers.

    We look at three structured fields:
      • _encounter_history.patterns       (football)
      • _basketball_pace_form.signals     (basketball)
      • _baseball_stats.signals           (baseball)
    All three already use code-like labels — we map them to catalog codes.
    """
    eh = payload.get("_encounter_history") or {}
    for p in (eh.get("patterns") or []) if isinstance(eh, dict) else []:
        if not isinstance(p, dict):
            continue
        ptype = (p.get("type") or "").upper()
        if ptype in {"UNDER_TREND", "FEW_GOALS_TREND"}:
            sig = make_signal("UNDER_TREND_DETECTED", sport=sport,
                              confidence=int(p.get("confidence") or 75),
                              extra_explanation=p.get("evidence") or "")
            if sig:
                yield sig
        elif ptype in {"CORNERS_TREND", "HIGH_CORNERS"}:
            sig = make_signal("CORNER_VOLUME_DETECTED", sport=sport,
                              confidence=int(p.get("confidence") or 70),
                              extra_explanation=p.get("evidence") or "")
            if sig:
                yield sig
        elif ptype in {"STRONG_H2H", "H2H_PATTERN"}:
            sig = make_signal("STRONG_H2H_PATTERN", sport=sport,
                              confidence=int(p.get("confidence") or 70),
                              extra_explanation=p.get("evidence") or "")
            if sig:
                yield sig
        elif ptype in {"TEAM_TOTAL_UNDER"}:
            sig = make_signal("TEAM_TOTAL_UNDER_SIGNAL", sport=sport,
                              confidence=int(p.get("confidence") or 70),
                              extra_explanation=p.get("evidence") or "")
            if sig:
                yield sig

    # Basketball pace signals
    bpf = payload.get("_basketball_pace_form") or {}
    if isinstance(bpf, dict):
        for s in bpf.get("signals") or []:
            if not isinstance(s, dict):
                continue
            code = (s.get("code") or "").upper()
            if code in {"HIGH_PACE", "PACE_OVER"}:
                sig = make_signal("PACE_OVER_SIGNAL", sport=sport,
                                  confidence=int(s.get("confidence") or 70),
                                  extra_explanation=s.get("text") or "")
                if sig:
                    yield sig
            elif code in {"UNDER_TREND", "DEFENSIVE_BATTLE"}:
                sig = make_signal("UNDER_TREND_DETECTED", sport=sport,
                                  confidence=int(s.get("confidence") or 70),
                                  extra_explanation=s.get("text") or "")
                if sig:
                    yield sig

    # Baseball signals
    bb = payload.get("_baseball_stats") or {}
    if isinstance(bb, dict):
        for s in bb.get("signals") or []:
            if not isinstance(s, dict):
                continue
            code = (s.get("code") or "").upper()
            if code in {"PITCHER_DUEL", "LOW_RUNS_EXPECTED"}:
                sig = make_signal("PITCHER_DUEL_SIGNAL", sport=sport,
                                  confidence=int(s.get("confidence") or 75),
                                  extra_explanation=s.get("text") or "")
                if sig:
                    yield sig
            elif code in {"BULLPEN_FATIGUE", "TIRED_BULLPEN"}:
                sig = make_signal("BULLPEN_FATIGUE_SIGNAL", sport=sport,
                                  confidence=int(s.get("confidence") or 70),
                                  extra_explanation=s.get("text") or "")
                if sig:
                    yield sig
            elif code in {"UNDER_TREND"}:
                sig = make_signal("UNDER_TREND_DETECTED", sport=sport,
                                  confidence=int(s.get("confidence") or 70),
                                  extra_explanation=s.get("text") or "")
                if sig:
                    yield sig


def _dedupe_and_sort(signals: list[dict]) -> list[dict]:
    by_code: dict[str, dict] = {}
    for sig in signals:
        code = sig.get("code")
        if not code:
            continue
        existing = by_code.get(code)
        if existing is None:
            by_code[code] = sig
            continue
        # Keep the stricter severity + the longer explanation.
        if _SEVERITY_ORDER.get(sig["severity"], 99) < _SEVERITY_ORDER.get(existing["severity"], 99):
            by_code[code] = sig
        elif len((sig.get("explanation") or "")) > len((existing.get("explanation") or "")):
            existing["explanation"] = sig["explanation"]
    return sorted(
        by_code.values(),
        key=lambda s: (_SEVERITY_ORDER.get(s["severity"], 99), -int(s.get("confidence") or 0)),
    )


def aggregate_signals_for_payload(payload: dict, sport: str) -> list[dict]:
    """Build the canonical `editorial_context_signals` list for one match
    payload. Pure function — never mutates `payload`.

    `payload` may be a pick (from `result['picks']`), a discarded entry
    (from `summary.discarded_*`), or the raw match input. The extractors
    are defensive — they only read fields that exist.
    """
    sport = (sport or "football").lower()
    bag: list[dict] = []
    for extractor in (
        _from_trap_signals_structured,
        _from_editorial_context,
        _from_form_guard,
        _from_protected_market,
        _from_historical_patterns,
    ):
        try:
            bag.extend(list(extractor(payload, sport)))
        except Exception as exc:
            log.debug("signal extractor %s failed: %s", extractor.__name__, exc)
    return _dedupe_and_sort(bag)


def build_signal_summary(all_signals_by_match: dict[str, list[dict]]) -> dict:
    """Top-level summary for the dashboard 'Señales detectadas hoy' panel.

    Counts are deliberately based on UNIQUE (match_id, code) pairs so we
    don't inflate when the same signal fires multiple times.
    """
    total = 0
    positive = 0
    negative = 0
    neutral = 0
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    trap_count = 0
    protected_count = 0
    historical_count = 0
    for _, signals in all_signals_by_match.items():
        for s in signals or []:
            total += 1
            st = s.get("signal_type")
            if   st == "positive": positive += 1
            elif st == "negative": negative += 1
            else:                  neutral  += 1
            cat = s.get("category") or "other"
            by_category[cat] = by_category.get(cat, 0) + 1
            sev = s.get("severity") or "low"
            by_severity[sev] = by_severity.get(sev, 0) + 1
            if cat == "trap":             trap_count += 1
            if cat == "protected_market": protected_count += 1
            if cat == "historical":       historical_count += 1
    return {
        "total_signals":             total,
        "positive_signals":          positive,
        "negative_signals":          negative,
        "neutral_signals":           neutral,
        "trap_signals":              trap_count,
        "protected_market_signals":  protected_count,
        "historical_signals":        historical_count,
        "by_category":               by_category,
        "by_severity":               by_severity,
    }


def known_catalog_codes(sport: str | None = None) -> list[str]:
    """Used by the admin endpoint to introspect what's possible."""
    if sport is None:
        return list(SIGNAL_CATALOG.keys())
    return [c for c, e in SIGNAL_CATALOG.items() if sport in e["applicable_sports"]]


__all__ = [
    "aggregate_signals_for_payload",
    "build_signal_summary",
    "known_catalog_codes",
]
