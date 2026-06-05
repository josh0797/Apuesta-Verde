"""Live recommendation **settlement** helpers.

This module is the post-hoc resolution layer for the
``live_recommendation_events`` collection. It is intentionally *pure* —
it never recommends bets; it only inspects an event and (optionally) the
final match statistics, and returns whether the event hit, missed,
voided, or remains pending.

Settlement coverage (Phase 35 — Fix 3):
  * Football corner markets — total corners, team corners, simple
    corner handicap (half and integer lines).
  * Asian quarter corner handicap (±0.25 / ±0.75) is parsed but routed
    to ``requires_manual_settlement`` because it splits the stake into
    two halves and requires bookmaker-specific rules.

BTTS and total-goals settlement live in
``services.live_recommendation_history.settle_live_event_from_score``
and remain unchanged. The corner branch is dispatched explicitly by
the caller (``settle_event_extended``) when it detects a corner market.

Philosophy:
  * Deterministic — same inputs always produce the same output.
  * Auditable    — returns reason_codes + Spanish narrative.
  * Fail-soft    — missing stats return ``pending``, never ``miss``.
"""

from __future__ import annotations

import re
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Reason codes (canonical, exported)
# ─────────────────────────────────────────────────────────────────────
RC_CORNER_TOTAL_OVER_HIT     = "CORNER_TOTAL_OVER_HIT"
RC_CORNER_TOTAL_OVER_MISS    = "CORNER_TOTAL_OVER_MISS"
RC_CORNER_TOTAL_UNDER_HIT    = "CORNER_TOTAL_UNDER_HIT"
RC_CORNER_TOTAL_UNDER_MISS   = "CORNER_TOTAL_UNDER_MISS"
RC_CORNER_TOTAL_VOID_PUSH    = "CORNER_TOTAL_VOID_PUSH"
RC_TEAM_CORNERS_OVER_HIT     = "TEAM_CORNERS_OVER_HIT"
RC_TEAM_CORNERS_OVER_MISS    = "TEAM_CORNERS_OVER_MISS"
RC_TEAM_CORNERS_UNDER_HIT    = "TEAM_CORNERS_UNDER_HIT"
RC_TEAM_CORNERS_UNDER_MISS   = "TEAM_CORNERS_UNDER_MISS"
RC_CORNER_HANDICAP_HIT       = "CORNER_HANDICAP_HIT"
RC_CORNER_HANDICAP_MISS      = "CORNER_HANDICAP_MISS"
RC_CORNER_HANDICAP_VOID_PUSH = "CORNER_HANDICAP_VOID_PUSH"
RC_MISSING_CORNER_STATS      = "MISSING_CORNER_STATS"
RC_UNKNOWN_CORNER_MARKET     = "UNKNOWN_CORNER_MARKET"
RC_ASIAN_REQUIRES_MANUAL     = "ASIAN_CORNER_HANDICAP_REQUIRES_MANUAL_SETTLEMENT"

# ─────────────────────────────────────────────────────────────────────
# Market detection helpers
# ─────────────────────────────────────────────────────────────────────
_CORNER_TOKENS = (
    "corner", "corners",
    "córner", "córners",
    "corner kick", "corner kicks",
    "tiro de esquina", "tiros de esquina",
    "saque de esquina",
)
_OVER_TOKENS = ("over", "más de", "mas de", "arriba de", "mayor de")
_UNDER_TOKENS = ("under", "menos de", "debajo de", "menor de")

# Team side detection (post-lowering).
_HOME_TOKENS = (" home ", "home ", " local ", "local ", " casa ", "casa ")
_AWAY_TOKENS = (" away ", "away ", " visitante ", "visitante ", " fuera ", "fuera ")


def _market_text(event: dict | None) -> str:
    """Concatenate every text source a market may live in."""
    if not isinstance(event, dict):
        return ""
    rec = event.get("recommendation") or {}
    parts = [
        event.get("market"),
        event.get("selection"),
        rec.get("market"),
        rec.get("selection"),
        rec.get("title"),
        rec.get("suggested_market"),
    ]
    return " | ".join(str(p) for p in parts if p).lower()


def _is_corner_market(event: dict | None) -> bool:
    text = _market_text(event)
    if not text:
        return False
    return any(tok in text for tok in _CORNER_TOKENS)


def _contains_any(text: str, tokens) -> bool:
    return any(tok in text for tok in tokens)


# Quarter-Asian detection MUST happen BEFORE generic half-line parsing
# because "+0.25" / "-0.75" must short-circuit to manual settlement.
_ASIAN_QUARTER_RE = re.compile(r"[+\-]?0\.(25|75)\b")


def _looks_asian_quarter(text: str) -> bool:
    return bool(_ASIAN_QUARTER_RE.search(text or ""))


# Numeric line extraction: prefer the LARGEST sensible number (markets
# like "Over 8.5 corners" beat tagentially-present digits).
_NUMBER_RE = re.compile(r"[+\-]?\d+(?:\.\d+)?")


def _extract_line(text: str) -> float | None:
    if not text:
        return None
    candidates = _NUMBER_RE.findall(text)
    if not candidates:
        return None
    # Filter out tiny ID-like numbers (e.g. 0.25 in "+0.25" reuses).
    nums = []
    for c in candidates:
        try:
            v = float(c)
        except ValueError:
            continue
        # Skip implausibly large numbers (would be IDs, not corner lines).
        if abs(v) > 30:
            continue
        nums.append(v)
    if not nums:
        return None
    # The biggest non-negative number is almost always the line.
    pos = [n for n in nums if n >= 0]
    if pos:
        return max(pos)
    return max(nums, key=abs)


# Handicap-specific line extraction: keep the sign.
_HANDICAP_RE = re.compile(r"[+\-]\d+(?:\.\d+)?")


def _extract_signed_handicap(text: str) -> float | None:
    if not text:
        return None
    m = _HANDICAP_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────
# Corner stats normalization
# ─────────────────────────────────────────────────────────────────────
def _coerce_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _extract_corners(final_match_stats: Any) -> tuple[int | None, int | None]:
    """Normalize home/away corner counts from heterogenous shapes."""
    if not isinstance(final_match_stats, dict):
        return None, None
    fms = final_match_stats

    # 1. Direct flat keys.
    for hk, ak in (
        ("corners_home", "corners_away"),
        ("home_corners", "away_corners"),
        ("home_corner_count", "away_corner_count"),
    ):
        h = _coerce_int(fms.get(hk))
        a = _coerce_int(fms.get(ak))
        if h is not None and a is not None:
            return h, a

    # 2. Nested under stats.{home,away}.corners.
    stats = fms.get("stats")
    if isinstance(stats, dict):
        home_s = stats.get("home") or {}
        away_s = stats.get("away") or {}
        h = _coerce_int(home_s.get("corners"))
        a = _coerce_int(away_s.get("corners"))
        if h is not None and a is not None:
            return h, a

    # 3. Nested under final_stats.corners.{home,away}.
    fs = fms.get("final_stats") or {}
    if isinstance(fs, dict):
        c = fs.get("corners")
        if isinstance(c, dict):
            h = _coerce_int(c.get("home"))
            a = _coerce_int(c.get("away"))
            if h is not None and a is not None:
                return h, a

    # 4. Nested under corners.{home,away}.
    c = fms.get("corners")
    if isinstance(c, dict):
        h = _coerce_int(c.get("home"))
        a = _coerce_int(c.get("away"))
        if h is not None and a is not None:
            return h, a

    return None, None


def _resolve_team_side(text: str, event: dict, final_match_stats: dict) -> str | None:
    """Best-effort team-side detection from market text + names."""
    if not text:
        return None
    # Try generic tokens first.
    padded = f" {text} "
    if _contains_any(padded, _HOME_TOKENS):
        return "home"
    if _contains_any(padded, _AWAY_TOKENS):
        return "away"
    # Fall back to explicit team names if present.
    for src in (event, final_match_stats):
        if not isinstance(src, dict):
            continue
        ht = (src.get("home_team") or {}).get("name") if isinstance(src.get("home_team"), dict) else src.get("home_team")
        at = (src.get("away_team") or {}).get("name") if isinstance(src.get("away_team"), dict) else src.get("away_team")
        if isinstance(ht, str) and ht.lower() in text:
            return "home"
        if isinstance(at, str) and at.lower() in text:
            return "away"
    return None


# ─────────────────────────────────────────────────────────────────────
# Result builders
# ─────────────────────────────────────────────────────────────────────
def _pending(reason_code: str, reason_es: str, **extra) -> dict:
    out = {
        "settled":        False,
        "status":         "pending",
        "result":         None,
        "market_type":    extra.pop("market_type", "UNKNOWN"),
        "line":           extra.pop("line", None),
        "side":           extra.pop("side", None),
        "actual_value":   extra.pop("actual_value", None),
        "home_corners":   extra.pop("home_corners", None),
        "away_corners":   extra.pop("away_corners", None),
        "total_corners":  extra.pop("total_corners", None),
        "reason_codes":   [reason_code] + list(extra.pop("reason_codes", [])),
        "reason_es":      reason_es,
    }
    return out


def _settled(
    *,
    market_type: str,
    line: float | None,
    side: str | None,
    actual_value: float | int | None,
    home_corners: int | None,
    away_corners: int | None,
    status: str,
    reason_codes: list[str],
    reason_es: str,
) -> dict:
    return {
        "settled":        status in ("hit", "miss", "void"),
        "status":         status,
        "result":         status if status in ("hit", "miss", "void") else None,
        "market_type":    market_type,
        "line":           line,
        "side":           side,
        "actual_value":   actual_value,
        "home_corners":   home_corners,
        "away_corners":   away_corners,
        "total_corners":  ((home_corners or 0) + (away_corners or 0))
                          if (home_corners is not None and away_corners is not None) else None,
        "reason_codes":   list(reason_codes),
        "reason_es":      reason_es,
    }


# ─────────────────────────────────────────────────────────────────────
# Public API: settle_corner_market
# ─────────────────────────────────────────────────────────────────────
def settle_corner_market(event: dict, final_match_stats: dict) -> dict:
    """Settle a corner-based live recommendation.

    Returns a settlement dict (see module docstring). Never raises.
    """
    try:
        text = _market_text(event)

        if not _is_corner_market(event):
            return {
                "settled":      False,
                "status":       "pending",
                "result":       None,
                "market_type":  "UNKNOWN",
                "line":         None,
                "side":         None,
                "actual_value": None,
                "home_corners": None,
                "away_corners": None,
                "total_corners": None,
                "reason_codes": [RC_UNKNOWN_CORNER_MARKET],
                "reason_es":    "El mercado no parece ser de córners; settlement omitido.",
            }

        # ── 1) Quarter-Asian handicap → manual ────────────────────────
        is_handicap = "handicap" in text or "hándicap" in text or _HANDICAP_RE.search(text)
        if is_handicap and _looks_asian_quarter(text):
            return {
                "settled":      False,
                "status":       "requires_manual_settlement",
                "result":       None,
                "market_type":  "CORNER_HANDICAP",
                "line":         None,
                "side":         _resolve_team_side(text, event, final_match_stats),
                "actual_value": None,
                "home_corners": None,
                "away_corners": None,
                "total_corners": None,
                "reason_codes": [RC_ASIAN_REQUIRES_MANUAL],
                "reason_es":    (
                    "Hándicap asiático de córners con línea ¼ (±0.25 / ±0.75): "
                    "el stake se divide en dos mitades, settlement manual."
                ),
            }

        # ── 2) Resolve corner counts ──────────────────────────────────
        home_c, away_c = _extract_corners(final_match_stats)
        if home_c is None or away_c is None:
            return _pending(
                RC_MISSING_CORNER_STATS,
                "No hay datos de córners en las estadísticas finales; settlement pendiente.",
                market_type=("CORNER_HANDICAP" if is_handicap else "TOTAL_CORNERS"),
            )
        total_c = home_c + away_c

        # ── 3) Corner handicap (simple half/integer) ──────────────────
        if is_handicap:
            side = _resolve_team_side(text, event, final_match_stats)
            hcap = _extract_signed_handicap(text)
            if side is None or hcap is None:
                return _pending(
                    RC_MISSING_CORNER_STATS,
                    "Hándicap de córners con datos insuficientes (lado o línea).",
                    market_type="CORNER_HANDICAP",
                    home_corners=home_c, away_corners=away_c,
                )
            team_c = home_c if side == "home" else away_c
            opp_c = away_c if side == "home" else home_c
            adjusted = team_c + hcap
            is_half_line = abs(hcap - round(hcap)) > 1e-9
            if adjusted > opp_c:
                return _settled(
                    market_type="CORNER_HANDICAP", line=hcap, side=side,
                    actual_value=adjusted, home_corners=home_c, away_corners=away_c,
                    status="hit",
                    reason_codes=[RC_CORNER_HANDICAP_HIT],
                    reason_es=(f"Hándicap {side} {hcap:+.2f} cumplido: "
                                f"{team_c}{hcap:+.2f}={adjusted} > {opp_c}."),
                )
            if not is_half_line and abs(adjusted - opp_c) < 1e-9:
                return _settled(
                    market_type="CORNER_HANDICAP", line=hcap, side=side,
                    actual_value=adjusted, home_corners=home_c, away_corners=away_c,
                    status="void",
                    reason_codes=[RC_CORNER_HANDICAP_VOID_PUSH],
                    reason_es=(f"Hándicap {side} {hcap:+g} push: {adjusted} = {opp_c}."),
                )
            return _settled(
                market_type="CORNER_HANDICAP", line=hcap, side=side,
                actual_value=adjusted, home_corners=home_c, away_corners=away_c,
                status="miss",
                reason_codes=[RC_CORNER_HANDICAP_MISS],
                reason_es=(f"Hándicap {side} {hcap:+.2f} fallido: "
                            f"{team_c}{hcap:+.2f}={adjusted} ≤ {opp_c}."),
            )

        # ── 4) Team corners or total corners ──────────────────────────
        is_over  = _contains_any(text, _OVER_TOKENS)
        is_under = _contains_any(text, _UNDER_TOKENS)
        side = _resolve_team_side(text, event, final_match_stats)
        line = _extract_line(text)

        if line is None:
            return _pending(
                RC_UNKNOWN_CORNER_MARKET,
                "No se pudo extraer la línea del mercado de córners.",
                market_type=("TEAM_CORNERS" if side else "TOTAL_CORNERS"),
                home_corners=home_c, away_corners=away_c,
            )

        is_half_line = abs(line - round(line)) > 1e-9
        is_team = side is not None
        market_type = "TEAM_CORNERS" if is_team else "TOTAL_CORNERS"
        actual = (home_c if side == "home" else away_c) if is_team else total_c

        if is_over:
            if actual > line:
                code = RC_TEAM_CORNERS_OVER_HIT if is_team else RC_CORNER_TOTAL_OVER_HIT
                return _settled(
                    market_type=market_type, line=line, side=side,
                    actual_value=actual, home_corners=home_c, away_corners=away_c,
                    status="hit",
                    reason_codes=[code],
                    reason_es=f"Over {line:g} córners cumplido (actual={actual}).",
                )
            if not is_half_line and actual == int(line):
                return _settled(
                    market_type=market_type, line=line, side=side,
                    actual_value=actual, home_corners=home_c, away_corners=away_c,
                    status="void",
                    reason_codes=[RC_CORNER_TOTAL_VOID_PUSH],
                    reason_es=f"Over {int(line)} córners push: actual={actual} = línea.",
                )
            code = RC_TEAM_CORNERS_OVER_MISS if is_team else RC_CORNER_TOTAL_OVER_MISS
            return _settled(
                market_type=market_type, line=line, side=side,
                actual_value=actual, home_corners=home_c, away_corners=away_c,
                status="miss",
                reason_codes=[code],
                reason_es=f"Over {line:g} córners fallido (actual={actual} ≤ línea).",
            )

        if is_under:
            if actual < line:
                code = RC_TEAM_CORNERS_UNDER_HIT if is_team else RC_CORNER_TOTAL_UNDER_HIT
                return _settled(
                    market_type=market_type, line=line, side=side,
                    actual_value=actual, home_corners=home_c, away_corners=away_c,
                    status="hit",
                    reason_codes=[code],
                    reason_es=f"Under {line:g} córners cumplido (actual={actual}).",
                )
            if not is_half_line and actual == int(line):
                return _settled(
                    market_type=market_type, line=line, side=side,
                    actual_value=actual, home_corners=home_c, away_corners=away_c,
                    status="void",
                    reason_codes=[RC_CORNER_TOTAL_VOID_PUSH],
                    reason_es=f"Under {int(line)} córners push: actual={actual} = línea.",
                )
            code = RC_TEAM_CORNERS_UNDER_MISS if is_team else RC_CORNER_TOTAL_UNDER_MISS
            return _settled(
                market_type=market_type, line=line, side=side,
                actual_value=actual, home_corners=home_c, away_corners=away_c,
                status="miss",
                reason_codes=[code],
                reason_es=f"Under {line:g} córners fallido (actual={actual} ≥ línea).",
            )

        return _pending(
            RC_UNKNOWN_CORNER_MARKET,
            "Mercado de córners reconocido pero sin Over/Under detectable.",
            market_type=market_type, line=line, side=side,
            home_corners=home_c, away_corners=away_c,
        )

    except Exception as exc:  # pragma: no cover — defensive
        return _pending(
            RC_UNKNOWN_CORNER_MARKET,
            f"Settlement de córners falló (fail-soft): {exc}",
        )


# ─────────────────────────────────────────────────────────────────────
# Convenience: top-level dispatcher (corners + future market types)
# ─────────────────────────────────────────────────────────────────────
def settle_event_extended(event: dict, final_match_stats: dict) -> dict | None:
    """Dispatch settlement for *extended* markets (currently corners).

    Returns the settlement dict when this branch matches, ``None`` when
    the event isn't a corner market (so the caller can fall back to the
    legacy BTTS / Over-Under settlement).
    """
    if _is_corner_market(event):
        return settle_corner_market(event, final_match_stats)
    return None


__all__ = [
    "settle_corner_market",
    "settle_event_extended",
    # Reason codes
    "RC_CORNER_TOTAL_OVER_HIT",
    "RC_CORNER_TOTAL_OVER_MISS",
    "RC_CORNER_TOTAL_UNDER_HIT",
    "RC_CORNER_TOTAL_UNDER_MISS",
    "RC_CORNER_TOTAL_VOID_PUSH",
    "RC_TEAM_CORNERS_OVER_HIT",
    "RC_TEAM_CORNERS_OVER_MISS",
    "RC_TEAM_CORNERS_UNDER_HIT",
    "RC_TEAM_CORNERS_UNDER_MISS",
    "RC_CORNER_HANDICAP_HIT",
    "RC_CORNER_HANDICAP_MISS",
    "RC_CORNER_HANDICAP_VOID_PUSH",
    "RC_MISSING_CORNER_STATS",
    "RC_UNKNOWN_CORNER_MARKET",
    "RC_ASIAN_REQUIRES_MANUAL",
]
