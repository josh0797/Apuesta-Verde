"""Football Corner Cross Integration — Phase F60.

Glue layer that wires three independent modules together at attach-time:

    1. :mod:`services.external_context_gate` — decides whether the
       expensive Scores24 fetch is worth its cost for this match.
    2. :mod:`services.scores24_scraper`      — premium Bright Data
       fetcher (only invoked when the gate opens).
    3. :mod:`services.football_corner_profile_cross` — pure L5-vs-L15
       cross engine that emits a corner-market hypothesis and a
       cross-check vs the external Scores24 prediction.

Design contract
---------------
* **Async, fail-soft, always-attaches.** The helper mutates
  ``pick_payload`` in place and never raises. If the gate denies or
  the scraper fails, the cross is still computed using internal data
  only (``scores24_payload=None``).
* **Cost guard.** The premium fetch is only attempted when the gate
  verdict is ``should_fetch=True`` AND a match URL is available.
* **Audit-first.** Everything is recorded under
  ``pick_payload["football_corner_cross_applied"]`` so the
  downstream selector / UI can observe the decision chain without
  re-running it.

The integration is enrichment-only: it does NOT mutate
``recommendation`` directly. Selection logic in
``football_market_selection`` and the UI consume the attached
``combined_football_corner_profile_cross`` block.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("football_corner_cross_integration")

ENGINE_VERSION = "football_corner_cross_integration.v1"

# Reason codes emitted on the audit block.
RC_GATE_OPENED          = "CORNER_CROSS_GATE_OPENED"
RC_GATE_DENIED          = "CORNER_CROSS_GATE_DENIED"
RC_NO_MATCH_URL         = "CORNER_CROSS_NO_MATCH_URL"
RC_SCRAPER_OK           = "CORNER_CROSS_SCORES24_OK"
RC_SCRAPER_FAILED       = "CORNER_CROSS_SCORES24_FAILED"
RC_SCRAPER_SKIPPED      = "CORNER_CROSS_SCORES24_SKIPPED"
RC_CROSS_COMPUTED       = "CORNER_CROSS_COMPUTED"
RC_CROSS_UNAVAILABLE    = "CORNER_CROSS_UNAVAILABLE"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _team_side(side: Any) -> dict:
    if not isinstance(side, dict):
        return {}
    # Some pipelines wrap stats under ``context``.
    ctx = side.get("context") if isinstance(side.get("context"), dict) else None
    return ctx or side


def _resolve_match_url(match: dict, pick_payload: dict) -> Optional[str]:
    """Look up the Scores24 URL in known locations.

    The pipeline may stash it under several keys depending on where the
    match was hydrated from. We probe a small list, returning the first
    non-empty string.
    """
    candidates = (
        match.get("scores24_url"),
        match.get("external_urls", {}).get("scores24") if isinstance(match.get("external_urls"), dict) else None,
        match.get("links", {}).get("scores24") if isinstance(match.get("links"), dict) else None,
        pick_payload.get("scores24_url"),
        (pick_payload.get("external_urls") or {}).get("scores24")
            if isinstance(pick_payload.get("external_urls"), dict) else None,
    )
    for c in candidates:
        if isinstance(c, str) and c.strip().startswith("http"):
            return c.strip()
    return None


def _build_gate_payload(pick_payload: dict, match: dict,
                        existing_cross: Optional[dict]) -> dict:
    """Assemble a flat payload that ``external_context_gate`` expects.

    The gate is generic and reads keys at the top level, so we project
    the relevant slices into a small dict instead of passing the entire
    ``pick_payload`` (which can be huge).
    """
    out: dict[str, Any] = {
        "recommendation":      pick_payload.get("recommendation"),
        "main_value_clean":    pick_payload.get("main_value_clean"),
        "competition":         match.get("competition")
                                or match.get("league")
                                or (match.get("competition_info") or {}).get("name"),
        "home_team_name":      (match.get("home_team") or {}).get("name")
                                or match.get("home_team_name")
                                or match.get("home"),
        "away_team_name":      (match.get("away_team") or {}).get("name")
                                or match.get("away_team_name")
                                or match.get("away"),
        "priority":            match.get("priority") or pick_payload.get("priority"),
        "is_final":            bool(match.get("is_final")),
        "is_semifinal":        bool(match.get("is_semifinal")),
        "live":                match.get("live") or pick_payload.get("live"),
        "live_pressure":       pick_payload.get("live_pressure"),
        "scores24_enrichment": pick_payload.get("scores24_enrichment"),
        "corner_market":       pick_payload.get("corner_market")
                                or match.get("corner_market"),
        "secondary_corner_signals": pick_payload.get("secondary_corner_signals"),
        "combined_football_profile_cross": pick_payload.get("combined_football_profile_cross"),
        "layer_conflict_audit": pick_payload.get("layer_conflict_audit"),
    }
    if existing_cross is not None:
        # Allow the gate to inspect the already-computed cross profile.
        out["combined_football_corner_profile_cross"] = existing_cross
    return out


# ─────────────────────────────────────────────────────────────────────
# Public entry — async
# ─────────────────────────────────────────────────────────────────────
async def attach_football_corner_cross_to_payload(
    pick_payload: dict | None,
    match: dict | None,
    *,
    scores24_fetcher: Optional[Any] = None,
    enable_premium_fetch: bool = True,
) -> dict:
    """Compute the corner-market cross profile and attach it to the pick.

    Parameters
    ----------
    pick_payload
        Mutated in place. The integrator writes:
        * ``combined_football_corner_profile_cross`` (engine result).
        * ``football_corner_cross_applied`` (decision audit).
        * ``scores24_corner_payload`` (optional, raw scraper output).
    match
        Raw match dict with ``home_team`` / ``away_team`` and (optionally)
        a ``scores24_url`` for the premium fetch.
    scores24_fetcher
        Optional injectable async callable for tests. When provided, it
        replaces the default Bright Data fetcher in
        :func:`services.scores24_scraper.scrape_scores24_match`.
    enable_premium_fetch
        Master kill-switch. When ``False`` the gate is bypassed and the
        cross runs internal-only. Useful for unit tests.

    Returns
    -------
    dict — audit block (also stored on the payload). Never raises.
    """
    audit: dict[str, Any] = {
        "available":         False,
        "engine_version":    ENGINE_VERSION,
        "gate_should_fetch": False,
        "gate_priority":     None,
        "gate_reason":       None,
        "gate_reason_codes": [],
        "gate_deny_codes":   [],
        "scores24_attempted": False,
        "scores24_ok":        False,
        "cross_available":    False,
        "cross_profile":      None,
        "cross_supports":     "NEUTRAL",
        "external_confirmation": False,
        "external_conflict":     False,
        "reason_codes":       [],
    }

    if not isinstance(pick_payload, dict):
        audit["_reason"] = "no_pick_payload"
        return audit
    if not isinstance(match, dict):
        match = {}

    # ── 1. Compute the cross *first* (internal-only path). ────────────
    # The cross is pure / cheap; doing it first lets the gate consult
    # the engine's verdict as one of its allow signals.
    try:
        from services.football_corner_profile_cross import (
            compute_football_corner_profile_cross,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("corner_profile_cross import failed: %s", exc)
        audit["_reason"] = "module_unavailable"
        audit["error"] = str(exc)
        return audit

    home_side = _team_side(match.get("home_team") or match.get("home"))
    away_side = _team_side(match.get("away_team") or match.get("away"))

    try:
        first_pass = compute_football_corner_profile_cross(
            home=home_side,
            away=away_side,
            scores24_payload=None,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("compute_football_corner_profile_cross (first pass) failed: %s", exc)
        first_pass = {"available": False, "_skipped_reason": "compute_failed"}

    # Stash the internal-only cross so the gate (and any later step that
    # short-circuits) sees a real result, not None.
    pick_payload["combined_football_corner_profile_cross"] = first_pass

    if first_pass.get("available"):
        audit["cross_available"] = True
        audit["cross_profile"]   = first_pass.get("profile")
        audit["cross_supports"]  = first_pass.get("supports") or "NEUTRAL"
        audit["reason_codes"].append(RC_CROSS_COMPUTED)
    else:
        audit["reason_codes"].append(RC_CROSS_UNAVAILABLE)

    # ── 2. Ask the cost-control gate. ────────────────────────────────
    try:
        from services.external_context_gate import should_fetch_scores24_context
    except Exception as exc:  # noqa: BLE001
        log.debug("external_context_gate import failed: %s", exc)
        should_fetch_scores24_context = None  # type: ignore[assignment]

    gate_verdict: dict[str, Any] = {"should_fetch": False}
    if enable_premium_fetch and should_fetch_scores24_context is not None:
        try:
            gate_payload = _build_gate_payload(pick_payload, match, first_pass)
            gate_verdict = should_fetch_scores24_context(gate_payload) or {}
        except Exception as exc:  # noqa: BLE001
            log.debug("external_context_gate raised: %s", exc)
            gate_verdict = {"should_fetch": False, "reason": "gate_error", "error": str(exc)}

    audit["gate_should_fetch"] = bool(gate_verdict.get("should_fetch"))
    audit["gate_priority"]     = gate_verdict.get("priority")
    audit["gate_reason"]       = gate_verdict.get("reason")
    audit["gate_reason_codes"] = list(gate_verdict.get("reason_codes") or [])
    audit["gate_deny_codes"]   = list(gate_verdict.get("deny_codes") or [])

    if audit["gate_should_fetch"]:
        audit["reason_codes"].append(RC_GATE_OPENED)
    else:
        audit["reason_codes"].append(RC_GATE_DENIED)

    # ── 3. Premium fetch (only when the gate opens). ─────────────────
    scores24_payload: Optional[dict] = None
    if audit["gate_should_fetch"]:
        match_url = _resolve_match_url(match, pick_payload)
        if not match_url:
            audit["reason_codes"].append(RC_NO_MATCH_URL)
        else:
            audit["scores24_attempted"] = True
            try:
                from services.scores24_scraper import scrape_scores24_match
                scores24_payload = await scrape_scores24_match(
                    url=match_url,
                    use_cache=True,
                    fetcher=scores24_fetcher,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("scrape_scores24_match raised: %s", exc)
                scores24_payload = None

            if isinstance(scores24_payload, dict) and scores24_payload.get("available"):
                audit["scores24_ok"] = True
                audit["reason_codes"].append(RC_SCRAPER_OK)
                pick_payload["scores24_corner_payload"] = scores24_payload
            else:
                audit["reason_codes"].append(RC_SCRAPER_FAILED)
    else:
        audit["reason_codes"].append(RC_SCRAPER_SKIPPED)

    # ── 4. Re-run the cross with the external payload (if we got one). ─
    if scores24_payload and audit["scores24_ok"]:
        try:
            second_pass = compute_football_corner_profile_cross(
                home=home_side,
                away=away_side,
                scores24_payload=scores24_payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("compute_football_corner_profile_cross (second pass) failed: %s", exc)
            second_pass = first_pass  # fall back to internal-only

        if second_pass.get("available"):
            pick_payload["combined_football_corner_profile_cross"] = second_pass
            audit["cross_profile"]  = second_pass.get("profile")
            audit["cross_supports"] = second_pass.get("supports") or "NEUTRAL"
            audit["external_confirmation"] = bool(second_pass.get("external_confirmation"))
            audit["external_conflict"]     = bool(second_pass.get("external_conflict"))

    # Mirror into footballHistoricalProfile camelCase (UI convenience).
    fhp = pick_payload.get("footballHistoricalProfile") or {}
    fhp["combinedFootballCornerProfileCross"] = pick_payload.get(
        "combined_football_corner_profile_cross"
    )
    pick_payload["footballHistoricalProfile"] = fhp

    audit["available"] = audit["cross_available"] or audit["scores24_ok"]
    pick_payload["football_corner_cross_applied"] = audit
    return audit


__all__ = [
    "ENGINE_VERSION",
    "attach_football_corner_cross_to_payload",
    "RC_GATE_OPENED", "RC_GATE_DENIED",
    "RC_NO_MATCH_URL",
    "RC_SCRAPER_OK", "RC_SCRAPER_FAILED", "RC_SCRAPER_SKIPPED",
    "RC_CROSS_COMPUTED", "RC_CROSS_UNAVAILABLE",
]
