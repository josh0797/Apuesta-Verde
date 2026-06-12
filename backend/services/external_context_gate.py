"""External Context Gate — Cost Control Layer.

Decides whether the football pipeline should pay for a premium Scores24
fetch via Bright Data. The premium path is expensive, so the gate only
opens when a real signal warrants the cost.

The gate is **pure**, **fail-soft**, and **opt-in**: callers explicitly
invoke ``should_fetch_scores24_context(match_payload)`` and respect the
``should_fetch`` verdict. The gate never performs I/O itself.

Allow rules (any one is sufficient)
-----------------------------------
1. **Corner-market candidate** — secondary sources already detected a
   non-trivial corner profile (STRONG_*, LOW_*, HIGH_*, ASYMMETRIC_*).
   → ``SCORES24_GATE_CORNER_MARKET_CANDIDATE``
2. **No clear value in main markets** — engine couldn't find an edge on
   1X2 / Double Chance / Over-Under goals / BTTS / DNB / Team goals.
   → ``SCORES24_GATE_MAIN_MARKETS_NO_VALUE``
3. **High-priority match** — finals, semifinals, World Cup / Champions
   League knockouts, derbies, top-tier national teams.
   → ``SCORES24_GATE_HIGH_PRIORITY_MATCH``
4. **Layer conflict** — internal layers disagree (live pressure vs
   corner profile vs odds vs protected discovery).
   → ``SCORES24_GATE_LAYER_CONFLICT``
5. **Edge close to threshold** — corner line ∈ {7.5, 8.5, 9.5, 10.5}
   AND ``abs(model_edge) <= 1.0`` → external confirmation is worth it.
   → ``SCORES24_GATE_EDGE_NEEDS_EXTERNAL_CONFIRMATION``

Deny rules (any one blocks the call)
------------------------------------
* No corner-market candidate
* Low-priority match (e.g. friendly with bench players)
* Fresh cache available
* Main pick already has clean value
* No corner line available from any secondary source
* Corner profile is MIXED → no actionable hypothesis
* Match already too advanced (live, minute >= 80) → pregame data stale
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "external_context_gate.v1"

# ── Allow priorities ────────────────────────────────────────────────
PRIORITY_HIGH    = "HIGH"
PRIORITY_MEDIUM  = "MEDIUM"
PRIORITY_LOW     = "LOW"

# ── Reason codes ────────────────────────────────────────────────────
RC_CORNER_MARKET_CANDIDATE      = "SCORES24_GATE_CORNER_MARKET_CANDIDATE"
RC_MAIN_MARKETS_NO_VALUE        = "SCORES24_GATE_MAIN_MARKETS_NO_VALUE"
RC_HIGH_PRIORITY_MATCH          = "SCORES24_GATE_HIGH_PRIORITY_MATCH"
RC_LAYER_CONFLICT               = "SCORES24_GATE_LAYER_CONFLICT"
RC_EDGE_NEEDS_EXTERNAL_CONFIRM  = "SCORES24_GATE_EDGE_NEEDS_EXTERNAL_CONFIRMATION"
RC_VALUE_FILTER_PASSED          = "SCORES24_GATE_VALUE_FILTER_PASSED"

# Deny reason codes
RC_DENY_NO_CANDIDATE            = "SCORES24_GATE_DENY_NO_CANDIDATE"
RC_DENY_LOW_PRIORITY            = "SCORES24_GATE_DENY_LOW_PRIORITY"
RC_DENY_CACHE_FRESH             = "SCORES24_GATE_DENY_CACHE_FRESH"
RC_DENY_MAIN_VALUE_CLEAN        = "SCORES24_GATE_DENY_MAIN_VALUE_CLEAN"
RC_DENY_NO_CORNER_LINE          = "SCORES24_GATE_DENY_NO_CORNER_LINE"
RC_DENY_MIXED_PROFILE           = "SCORES24_GATE_DENY_MIXED_CORNERS_PROFILE"
RC_DENY_LATE_LIVE               = "SCORES24_GATE_DENY_LATE_LIVE"

# Corner profile keys that qualify as "candidate".
_QUALIFYING_CORNER_PROFILES = frozenset({
    "STRONG_CORNERS_UNDER_CROSS",
    "LOW_CORNERS_CROSS",
    "STRONG_CORNERS_OVER_CROSS",
    "HIGH_CORNERS_CROSS",
    "ASYMMETRIC_CORNERS_PROFILE",
})

# High-priority competition / team keywords (case-insensitive, ASCII-ish).
_HIGH_PRIORITY_COMPETITIONS = (
    "world cup", "copa del mundo", "mundial",
    "champions league", "uefa champions",
    "europa league", "europa conference",
    "copa libertadores",
    "final", "semifinal", "semifinales",
    "knockout",
)
_HIGH_PRIORITY_TEAMS = (
    "germany", "alemania",
    "england", "inglaterra",
    "mexico", "méxico",
    "portugal",
    "spain", "españa",
    "france", "francia",
    "brazil", "brasil",
    "argentina",
    "italy", "italia",
    "netherlands", "países bajos", "paises bajos",
)
_DERBY_KEYWORDS = (
    "el clasico", "el clásico",
    "derbi", "derby", "clasico", "clásico",
    "real madrid vs barcelona", "barcelona vs real madrid",
    "manchester united vs liverpool", "liverpool vs manchester united",
    "boca vs river", "river vs boca",
    "milan vs inter", "inter vs milan",
)

# Live-minute cutoff after which Scores24 pregame data is stale.
LATE_LIVE_MINUTE_CUTOFF = 80

# Corner lines worth confirming externally + max edge magnitude for it.
GATE_CORNER_LINES   = {7.5, 8.5, 9.5, 10.5}
GATE_EDGE_THRESHOLD = 1.0


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _lower(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip().lower()


def _is_main_market_clean_value(payload: dict) -> bool:
    """Heuristic: the engine already found a confident main-market pick."""
    rec = payload.get("recommendation") or {}
    market = _lower(rec.get("market"))
    if not market or "corner" in market:
        return False
    # Normalise punctuation so families like "over_2_5" match real-world
    # market strings such as "Over 2.5" or "over-2.5".
    norm_market = (
        market
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )
    main_families = (
        "moneyline", "match_winner", "1x2", "home_away",
        "double_chance", "draw_no_bet", "dnb",
        "over_2_5", "under_2_5", "over_3_5", "under_3_5",
        "over_1_5", "under_1_5",
        "btts", "ambos_marcan", "both_teams_to_score",
        "team_total", "asian_handicap",
    )
    if not any(fam in norm_market for fam in main_families):
        return False
    conf = _safe(rec.get("confidence_score"))
    if conf is None:
        return False
    # "Clean value" = confidence >= 65 + recommendation tier in a safe bucket.
    if conf < 65:
        return False
    # Optional: respect explicit ``value_clean`` flag.
    if payload.get("main_value_clean") is True:
        return True
    return conf >= 70


def _has_corner_candidate(payload: dict) -> tuple[bool, Optional[str]]:
    """Return (has_candidate, profile_key)."""
    cross = payload.get("combined_football_corner_profile_cross") \
            or payload.get("corner_profile_cross") \
            or payload.get("football_corner_profile_cross")
    if isinstance(cross, dict) and cross.get("available"):
        prof = cross.get("profile")
        if prof in _QUALIFYING_CORNER_PROFILES:
            return True, prof
        if prof == "MIXED_CORNERS_PROFILE":
            return False, prof
    # Secondary signal: secondary sources flagged a corner edge.
    sec = payload.get("secondary_corner_signals") or {}
    if isinstance(sec, dict) and sec.get("candidate"):
        return True, sec.get("profile_hint") or "secondary_source_hint"
    return False, None


def _has_main_market_value(payload: dict) -> bool:
    """Inverse of "no value in main markets". True when we DO have value."""
    return _is_main_market_clean_value(payload)


def _is_high_priority_match(payload: dict) -> bool:
    competition = _lower(payload.get("competition") or payload.get("league")
                         or (payload.get("competition_info") or {}).get("name"))
    home = _lower(payload.get("home_team_name")
                  or (payload.get("home_team") or {}).get("name")
                  or payload.get("home"))
    away = _lower(payload.get("away_team_name")
                  or (payload.get("away_team") or {}).get("name")
                  or payload.get("away"))
    label = f"{home} vs {away}"

    if any(k in competition for k in _HIGH_PRIORITY_COMPETITIONS):
        return True
    if any(k in home for k in _HIGH_PRIORITY_TEAMS) and \
            any(k in away for k in _HIGH_PRIORITY_TEAMS):
        return True
    # Single-team national-team match still qualifies (e.g. WC qualifier).
    if any(k in home for k in _HIGH_PRIORITY_TEAMS) or \
            any(k in away for k in _HIGH_PRIORITY_TEAMS):
        # But only when competition is international (or unknown).
        if "international" in competition or "world" in competition \
                or "qualifier" in competition or competition == "":
            return True
    if any(k in label for k in _DERBY_KEYWORDS):
        return True
    if bool(payload.get("is_final")) or bool(payload.get("is_semifinal")):
        return True
    if _lower(payload.get("priority")) == "high":
        return True
    return False


def _has_layer_conflict(payload: dict) -> bool:
    """True when internal layers disagree (live pressure vs corner profile,
    external odds vs engine, protected discovery vs main pick, etc.)."""
    audit = payload.get("layer_conflict_audit")
    if isinstance(audit, dict) and audit.get("has_conflict"):
        return True
    # Heuristic checks across known signals.
    live_pressure = _lower((payload.get("live_pressure") or {}).get("direction")
                           if isinstance(payload.get("live_pressure"), dict) else "")
    corner_cross = payload.get("combined_football_corner_profile_cross") or {}
    corner_supports = _lower(corner_cross.get("supports") if isinstance(corner_cross, dict) else "")
    if live_pressure and corner_supports and live_pressure != corner_supports \
            and live_pressure != "neutral" and corner_supports != "neutral":
        return True
    profile_cross = payload.get("combined_football_profile_cross") or {}
    if isinstance(profile_cross, dict) and isinstance(corner_cross, dict):
        pc_sup = _lower(profile_cross.get("supports"))
        cc_sup = _lower(corner_cross.get("supports"))
        if pc_sup and cc_sup and pc_sup != cc_sup \
                and pc_sup != "neutral" and cc_sup != "neutral":
            return True
    return False


def _edge_needs_external_confirmation(payload: dict) -> bool:
    """True when corner line is at a critical threshold AND edge is razor-thin."""
    corner_market = payload.get("corner_market") or {}
    if not isinstance(corner_market, dict):
        return False
    line = _safe(corner_market.get("line"))
    edge = _safe(corner_market.get("model_edge"))
    if line is None or edge is None:
        return False
    if line not in GATE_CORNER_LINES:
        return False
    return abs(edge) <= GATE_EDGE_THRESHOLD


def _is_match_late_live(payload: dict) -> bool:
    live = payload.get("live") or {}
    if not isinstance(live, dict):
        return False
    minute = _safe(live.get("minute"))
    if minute is None:
        return False
    return int(minute) >= LATE_LIVE_MINUTE_CUTOFF


def _cache_is_fresh(payload: dict) -> bool:
    s24 = payload.get("scores24_enrichment") or payload.get("scores24_cache")
    if isinstance(s24, dict) and s24.get("available") and s24.get("fetched_at"):
        return True
    return False


def _has_corner_line_available(payload: dict) -> bool:
    """True when at least one secondary source provided a corner line."""
    corner_market = payload.get("corner_market") or {}
    if isinstance(corner_market, dict) and _safe(corner_market.get("line")) is not None:
        return True
    sec = payload.get("secondary_corner_signals") or {}
    return bool(isinstance(sec, dict) and sec.get("line_available"))


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def should_fetch_scores24_context(match_payload: dict | None) -> dict:
    """Decide whether to call the premium Scores24 scraper for this match.

    Returns ``{should_fetch, reason, priority, allowed_fetch_type,
    reason_codes, deny_codes}``. Never raises.
    """
    if not isinstance(match_payload, dict):
        return {
            "should_fetch":       False,
            "reason":             "invalid_payload",
            "priority":           PRIORITY_LOW,
            "allowed_fetch_type": "none",
            "reason_codes":       [],
            "deny_codes":         ["SCORES24_GATE_DENY_INVALID_PAYLOAD"],
            "engine_version":     ENGINE_VERSION,
        }

    deny: list[str] = []

    # ── DENY rules (immediate stop) ─────────────────────────────────
    if _cache_is_fresh(match_payload):
        deny.append(RC_DENY_CACHE_FRESH)
    if _is_match_late_live(match_payload):
        deny.append(RC_DENY_LATE_LIVE)

    if deny:
        return {
            "should_fetch":       False,
            "reason":             "denied_hard_rule",
            "priority":           PRIORITY_LOW,
            "allowed_fetch_type": "none",
            "reason_codes":       [],
            "deny_codes":         deny,
            "engine_version":     ENGINE_VERSION,
        }

    # ── ALLOW rules (any of them triggers fetch) ─────────────────────
    allow_codes: list[str] = []
    priority = PRIORITY_LOW
    reason: Optional[str] = None

    has_candidate, profile_key = _has_corner_candidate(match_payload)
    if has_candidate:
        allow_codes.append(RC_CORNER_MARKET_CANDIDATE)
        priority = PRIORITY_HIGH
        reason = "corner_market_candidate"

    main_value_clean = _has_main_market_value(match_payload)
    if not main_value_clean:
        # Only counts as an allow signal if there's at least *some* line
        # available; otherwise we'd be wasting calls.
        if _has_corner_line_available(match_payload) or has_candidate:
            allow_codes.append(RC_MAIN_MARKETS_NO_VALUE)
            if priority == PRIORITY_LOW:
                priority = PRIORITY_MEDIUM
            reason = reason or "main_markets_no_value"

    if _is_high_priority_match(match_payload):
        allow_codes.append(RC_HIGH_PRIORITY_MATCH)
        priority = PRIORITY_HIGH
        reason = reason or "high_priority_match"

    if _has_layer_conflict(match_payload):
        allow_codes.append(RC_LAYER_CONFLICT)
        if priority != PRIORITY_HIGH:
            priority = PRIORITY_MEDIUM
        reason = reason or "layer_conflict"

    if _edge_needs_external_confirmation(match_payload):
        allow_codes.append(RC_EDGE_NEEDS_EXTERNAL_CONFIRM)
        if priority != PRIORITY_HIGH:
            priority = PRIORITY_MEDIUM
        reason = reason or "edge_needs_external_confirmation"

    # ── Soft DENY checks (only when no allow rule fired) ─────────────
    if not allow_codes:
        # Categorise the deny.
        cross = match_payload.get("combined_football_corner_profile_cross") or {}
        if isinstance(cross, dict) and cross.get("profile") == "MIXED_CORNERS_PROFILE":
            deny.append(RC_DENY_MIXED_PROFILE)
        if not has_candidate:
            deny.append(RC_DENY_NO_CANDIDATE)
        if main_value_clean:
            deny.append(RC_DENY_MAIN_VALUE_CLEAN)
        if not _has_corner_line_available(match_payload):
            deny.append(RC_DENY_NO_CORNER_LINE)
        if _lower(match_payload.get("priority")) == "low":
            deny.append(RC_DENY_LOW_PRIORITY)
        return {
            "should_fetch":       False,
            "reason":             "no_allow_rule_matched",
            "priority":           PRIORITY_LOW,
            "allowed_fetch_type": "none",
            "reason_codes":       [],
            "deny_codes":         deny or [RC_DENY_NO_CANDIDATE],
            "engine_version":     ENGINE_VERSION,
        }

    # ── Build final verdict ──────────────────────────────────────────
    allow_codes.append(RC_VALUE_FILTER_PASSED)
    return {
        "should_fetch":       True,
        "reason":             reason or "value_filter_passed",
        "priority":           priority,
        "allowed_fetch_type": "premium" if priority == PRIORITY_HIGH else "standard",
        "reason_codes":       allow_codes,
        "deny_codes":         [],
        "matched_profile":    profile_key,
        "engine_version":     ENGINE_VERSION,
    }


__all__ = [
    "ENGINE_VERSION",
    "PRIORITY_HIGH", "PRIORITY_MEDIUM", "PRIORITY_LOW",
    "should_fetch_scores24_context",
]
