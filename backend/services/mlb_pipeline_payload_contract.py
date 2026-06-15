"""MLB Pipeline Payload Contract (Moneyball alignment).

Final fail-soft sealing layer for every per-game ``pick_payload`` produced
by ``mlb_day_orchestrator.analyze_mlb_day``. Guarantees that the
following keys are **always present** with a canonical shape so the UI /
analyst engine never crashes on a missing key — every layer that could
not produce real data simply emits ``{"available": False, ...}``.

The contract enforced (per user spec, NON-NEGOTIABLE):

    1.  advanced_stats_snapshot
    2.  pressure_base
    3.  sabermetrics_audit
    4.  ghost_edges
    5.  fragility_score
    6.  script_survival_score
    7.  market_selection
    8.  historical_pattern_match
    9.  pattern_memory_audit
    10. manual_odds_review
    11. pipeline_meta.external_sources

This module is **pure** (no IO) and never raises — every helper is
guarded so a malformed payload degrades gracefully.

The orchestrator calls :func:`seal_pick_payload` once at the end of each
per-game pipeline; the function mutates the payload in place and returns
it for chaining.
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "seal_pick_payload",
    "build_manual_odds_review",
    "build_ghost_edges_summary",
    "build_pattern_memory_audit",
    "merge_pipeline_external_sources",
    "CONTRACT_FIELDS",
]

# Canonical contract fields exposed by every sealed pick_payload.
CONTRACT_FIELDS: tuple[str, ...] = (
    "advanced_stats_snapshot",
    "pressure_base",
    "sabermetrics_audit",
    "ghost_edges",
    "fragility_score",
    "script_survival_score",
    "market_selection",
    "historical_pattern_match",
    "pattern_memory_audit",
    "manual_odds_review",
    "quality_contact_matchup",   # F91 (contextual; never mutates picks).
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe_dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _safe_list(v: Any) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _empty_layer(reason: str = "layer_not_executed") -> dict:
    return {"available": False, "reason": reason}


# ─────────────────────────────────────────────────────────────────────
# Ghost-edges summary (Phase 11)
# ─────────────────────────────────────────────────────────────────────
_GHOST_EDGE_FLAGS = {
    # Statcast / xERA / xwOBA
    "ERA_UNDERSTATES_RISK",
    "ERA_OVERSTATES_RISK",
    "PITCHER_XWOBA_WARNING",
    "GHOST_EDGE_HARD_CONTACT_VS_UNDER",
    "GHOST_EDGE_TEAM_XWOBA_VS_UNDER",
    # L5/L15 trend ghosts
    "GHOST_EDGE_UNDER_VS_L5_HIGH_SCORING",
    "GHOST_EDGE_OVER_VS_L5_LOW_SCORING",
    "GHOST_EDGE_F5_UNDER_VS_L5",
    "GHOST_EDGE_RISING_ON_BASE_VS_UNDER",
    "RECENT_RUN_TREND_CONTRADICTS_UNDER",
    "RECENT_RUN_TREND_CONTRADICTS_OVER",
}


def build_ghost_edges_summary(pick_payload: Any) -> dict:
    """Aggregate ghost-edge flags into a single canonical block.

    Pulls from ``model_verification.discrepancies`` and the engine's
    flat ``reason_codes`` array.  Always returns a dict — never None —
    so the UI can render an empty (``available=False``) state.
    """
    if not isinstance(pick_payload, dict):
        return _empty_layer("payload_missing")

    discrepancies = _safe_list(
        (_safe_dict(pick_payload.get("model_verification"))).get("discrepancies")
    )
    flags_from_disc = [
        d.get("flag") for d in discrepancies
        if isinstance(d, dict) and d.get("flag") in _GHOST_EDGE_FLAGS
    ]

    rcs = _safe_list(pick_payload.get("reason_codes"))
    flags_from_rcs = [rc for rc in rcs if rc in _GHOST_EDGE_FLAGS]

    all_flags = sorted({*flags_from_disc, *flags_from_rcs})
    if not all_flags:
        return {
            "available":      False,
            "flags":           [],
            "count":           0,
            "blocked_pick":    False,
            "reason":          "no_ghost_edges_detected",
        }

    ms = _safe_dict(pick_payload.get("market_selection"))
    blocked = "GHOST_EDGE_BLOCKED_PICK" in _safe_list(ms.get("reason_codes"))

    return {
        "available":   True,
        "flags":        all_flags,
        "count":        len(all_flags),
        "blocked_pick": bool(blocked),
        "narrative":   (
            "Señales fantasma detectadas: el dato superficial parecía "
            "favorable, pero métricas avanzadas elevaron el riesgo."
        ) if all_flags else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Pattern memory audit (warehouse / Moneyball)
# ─────────────────────────────────────────────────────────────────────
def build_pattern_memory_audit(pick_payload: Any) -> dict:
    """Translate ``historical_pattern_match`` into a UI-friendly audit.

    Adds canonical fields the UI panel needs even when the warehouse is
    empty (sample_size=0 → ``available:false``).
    """
    if not isinstance(pick_payload, dict):
        return _empty_layer("payload_missing")

    hpm = _safe_dict(pick_payload.get("historical_pattern_match"))
    sample_size = int(hpm.get("sample_size") or 0)
    hit_rate    = hpm.get("historical_hit_rate")
    roi         = hpm.get("historical_roi")
    best_mkt    = hpm.get("best_historical_market")
    adj         = float(hpm.get("pattern_confidence_adjustment") or 0.0)
    codes       = _safe_list(hpm.get("pattern_reason_codes"))

    if sample_size <= 0:
        return {
            "available":             False,
            "sample_size":           0,
            "sample_tier":           "NONE",
            "historical_hit_rate":   None,
            "historical_roi":        None,
            "best_historical_market": None,
            "pattern_confidence_adjustment": 0.0,
            "reason_codes":          codes,
            "warning":               "Sin patrones históricos coincidentes.",
        }

    if sample_size < 20:
        tier   = "LOW_SAMPLE"
        usable = False
    elif sample_size < 50:
        tier   = "USEFUL"
        usable = True
    else:
        tier   = "VALIDATED" if (roi is not None and float(roi) > 0) else "USEFUL"
        usable = True

    return {
        "available":             usable,
        "sample_size":           sample_size,
        "sample_tier":           tier,
        "historical_hit_rate":   hit_rate,
        "historical_roi":        roi,
        "best_historical_market": best_mkt,
        "pattern_confidence_adjustment": adj,
        "reason_codes":          codes,
        "matched_patterns":      _safe_list(hpm.get("matched_patterns")),
        "primary_pattern":       hpm.get("primary_pattern"),
        "warning":               hpm.get("warning"),
    }


# ─────────────────────────────────────────────────────────────────────
# Manual odds review block
# ─────────────────────────────────────────────────────────────────────
def build_manual_odds_review(pick_payload: Any) -> dict:
    """Summarise manual-odds-review needs for the pick.

    The pick lands in manual-review when:
      * market_selection.requires_manual_odds is True, OR
      * pipeline bucket is structural_lean_requires_odds / watchlist_manual_odds, OR
      * recommendation.odds_range is empty AND we still have a directional read.

    Returns a fail-soft dict the UI can render directly.
    """
    if not isinstance(pick_payload, dict):
        return _empty_layer("payload_missing")

    rec = _safe_dict(pick_payload.get("recommendation"))
    odds = rec.get("odds_range") or rec.get("odds") or rec.get("recommended_odds")
    has_odds = bool(odds)

    ms = _safe_dict(pick_payload.get("market_selection"))
    requires_manual = bool(ms.get("requires_manual_odds"))
    bucket = pick_payload.get("_bucket")

    structural_lean = bucket in (
        "structural_lean_requires_odds",
        "watchlist_manual_odds",
    )

    needs_review = bool(requires_manual or structural_lean or (not has_odds and ms.get("recommended_market")))

    if not needs_review:
        return {
            "available":             False,
            "required":              False,
            "reason":                "odds_present_or_not_applicable",
            "has_engine_odds":       has_odds,
            "recommended_market":    ms.get("recommended_market"),
        }

    # Build a structured review block.
    confidence = float(rec.get("confidence_score") or 0)
    engine_probability = max(0.0, min(1.0, confidence / 100.0)) if confidence else None

    return {
        "available":              True,
        "required":               True,
        "reason":                 _manual_review_reason(
            requires_manual=requires_manual,
            structural_lean=structural_lean,
            has_odds=has_odds,
        ),
        "recommended_market":     ms.get("recommended_market"),
        "protected_alternative":  ms.get("protected_alternative"),
        "has_engine_odds":        has_odds,
        "engine_odds_range":      odds,
        "engine_probability":     engine_probability,
        "market_confidence":      ms.get("market_confidence"),
        "fragility":              ms.get("fragility"),
        "why_this_market":        ms.get("why_this_market"),
        "user_action":            _manual_review_user_action(
            requires_manual=requires_manual,
            structural_lean=structural_lean,
        ),
        "user_action_es":         _manual_review_user_action_es(
            requires_manual=requires_manual,
            structural_lean=structural_lean,
        ),
    }


def _manual_review_reason(*, requires_manual: bool, structural_lean: bool,
                            has_odds: bool) -> str:
    if requires_manual:
        return "market_selection_requires_manual_odds"
    if structural_lean and not has_odds:
        return "structural_lean_no_odds_available"
    if structural_lean:
        return "structural_lean_pending_user_odds"
    if not has_odds:
        return "no_engine_odds_available"
    return "manual_review_recommended"


def _manual_review_user_action(*, requires_manual: bool,
                                  structural_lean: bool) -> str:
    if structural_lean:
        return "PASTE_BOOKIE_ODDS_TO_VALIDATE_EDGE"
    if requires_manual:
        return "PASTE_ODDS_FOR_MANUAL_REVIEW"
    return "REVIEW_LINE_BEFORE_BETTING"


def _manual_review_user_action_es(*, requires_manual: bool,
                                      structural_lean: bool) -> str:
    if structural_lean:
        return (
            "Lectura estructural detectada. Pega la cuota de tu bookie "
            "para calcular el edge real."
        )
    if requires_manual:
        return "Revisión manual de cuota requerida antes de apostar."
    return "Revisa la cuota actual antes de apostar."


# ─────────────────────────────────────────────────────────────────────
# Source-status aggregator
# ─────────────────────────────────────────────────────────────────────
def merge_pipeline_external_sources(
    pipeline_meta: dict,
    *,
    snapshot_source: str | None = None,
    adapter_status: dict | None = None,
    editorial_status: dict | None = None,
) -> dict:
    """Aggregate every external-source signal into ``pipeline_meta``.

    Always returns the pipeline_meta dict (mutated) with a canonical
    ``external_sources`` sub-block::

        {
            "statcast":      {"used": bool, "status": "ok|partial|failed|missing"},
            "sabermetrics":  {"used": bool, "status": ...},
            "editorial":     {"used": bool, "status": ...},
            "warehouse":     {"used": bool, "status": ...},
            "statsapi":      {"used": bool, "status": ...},
        }
    """
    if not isinstance(pipeline_meta, dict):
        return {}
    ext = _safe_dict(pipeline_meta.get("external_sources"))

    def _set(name: str, used: bool, status: str, **extra: Any) -> None:
        entry = _safe_dict(ext.get(name)) or {"used": False, "status": "missing"}
        # Only upgrade — never downgrade. ok > partial > failed > missing.
        rank = {"ok": 3, "partial": 2, "failed": 1, "missing": 0}
        if rank.get(status, 0) > rank.get(entry.get("status", "missing"), 0):
            entry["status"] = status
        entry["used"] = bool(used or entry.get("used"))
        if extra:
            entry.update({k: v for k, v in extra.items() if v is not None})
        ext[name] = entry

    # Defaults (always declared so UI never sees missing keys)
    for default_name in ("statcast", "sabermetrics", "editorial",
                         "warehouse", "statsapi"):
        ext.setdefault(default_name, {"used": False, "status": "missing"})

    # Translate orchestrator-side meta into the canonical block.
    if pipeline_meta.get("source_used"):
        _set("statsapi", True, "ok" if pipeline_meta.get("source_used") in
             ("statsapi", "mlb.com_fallback") else "failed",
             source_url=pipeline_meta.get("statsapi_url"))

    if pipeline_meta.get("cache_status"):
        # If we used cache, mark warehouse as ok.
        if pipeline_meta["cache_status"] in ("hit", "warm"):
            _set("warehouse", True, "ok")
        elif pipeline_meta["cache_status"] in ("miss",):
            _set("warehouse", False, "partial")

    if snapshot_source:
        _set("statcast", True, "ok" if snapshot_source == "ok" else
             ("partial" if snapshot_source == "partial" else "failed"))

    if isinstance(adapter_status, dict):
        for key, st in adapter_status.items():
            if key in ext:
                _set(key, True, st)

    if isinstance(editorial_status, dict) and editorial_status.get("used"):
        _set("editorial", True, editorial_status.get("status") or "ok",
             sources_count=editorial_status.get("sources_count"))

    pipeline_meta["external_sources"] = ext
    return pipeline_meta


# ─────────────────────────────────────────────────────────────────────
# Pure helpers — read-only audit of upstream layer availability
# ─────────────────────────────────────────────────────────────────────
def _coerce_advanced_snapshot(payload: dict) -> dict:
    """Return advanced_stats_snapshot or a fail-soft equivalent."""
    snap = payload.get("advanced_stats_snapshot")
    if isinstance(snap, dict) and snap:
        snap.setdefault("available", True)
        return snap
    # Fall back to ``advanced_adjustments.summary`` if present so the UI
    # can still show the audit even when the snapshot wasn't attached.
    adv = _safe_dict(payload.get("advanced_adjustments"))
    if adv.get("data_quality") and adv["data_quality"] != "missing":
        return {
            "available":    True,
            "data_quality": adv.get("data_quality"),
            "summary":      adv.get("summary") or {},
            "reason_codes": _safe_list(adv.get("reason_codes")),
            "source":       "advanced_adjustments_fallback",
        }
    return {"available": False, "reason": "no_advanced_snapshot"}


def _coerce_pressure_base(payload: dict) -> dict:
    pb = payload.get("pressure_base")
    if isinstance(pb, dict) and pb.get("available"):
        return pb
    if isinstance(pb, dict) and pb:
        pb.setdefault("available", False)
        pb.setdefault("reason", "pressure_base_partial")
        return pb
    return {"available": False, "reason": "pressure_base_missing"}


def _coerce_sabermetrics_audit(payload: dict) -> dict:
    audit = payload.get("sabermetrics_audit")
    if isinstance(audit, dict) and audit.get("sabermetrics_used"):
        audit.setdefault("available", True)
        return audit
    # Fallback: derive a thin audit from the sabermetrics block.
    saber = _safe_dict(payload.get("sabermetrics"))
    if saber.get("available"):
        return {
            "available":                True,
            "sabermetrics_used":        True,
            "sabermetrics_data_quality": saber.get("data_quality") or "missing",
            "sabermetrics_adjustment_weight": 0.0,
            "sabermetrics_raw_adjustment":    0.0,
            "sabermetrics_weighted_adjustment": 0.0,
            "sabermetrics_raw_breakdown":     {},
            "sabermetrics_reason_codes":      [],
            "summary":                  saber.get("summary"),
            "source":                   "sabermetrics_block_fallback",
        }
    return {
        "available":                False,
        "sabermetrics_used":        False,
        "sabermetrics_data_quality": "missing",
        "reason":                   "sabermetrics_layer_unavailable",
    }


def _coerce_market_selection(payload: dict) -> dict:
    ms = payload.get("market_selection")
    if isinstance(ms, dict) and ms.get("recommended_market"):
        ms.setdefault("available", True)
        return ms
    return {"available": False, "reason": "market_selection_not_computed"}


def _coerce_fragility_score(payload: dict) -> dict:
    # Multiple historical layers stamp fragility — prefer the explicit
    # block, fall back to the flat field stamped by V5.
    frag_block = payload.get("fragility")
    if isinstance(frag_block, dict) and frag_block:
        score = frag_block.get("score")
        if isinstance(score, (int, float)):
            return {
                "available":  True,
                "score":      float(score),
                "tier":       _fragility_tier(float(score)),
                "components": frag_block,
            }
    fs = payload.get("fragility_score")
    if isinstance(fs, (int, float)):
        return {
            "available": True,
            "score":     float(fs),
            "tier":      _fragility_tier(float(fs)),
        }
    return {"available": False, "reason": "fragility_not_computed"}


def _fragility_tier(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _coerce_script_survival(payload: dict) -> dict:
    v5 = _safe_dict(payload.get("_mlb_script_v5"))
    surv_block = _safe_dict(v5.get("survival")) if v5 else {}
    score = surv_block.get("score") if surv_block else payload.get("script_survival_score") \
            or payload.get("script_survival")
    if isinstance(score, (int, float)):
        score = float(score)
        return {
            "available":  True,
            "score":      score,
            "tier":       _survival_tier(score),
            "stability_code": (v5.get("stability") or {}).get("code")
                              if v5 else payload.get("stability_code"),
        }
    return {"available": False, "reason": "script_survival_not_computed"}


def _survival_tier(score: float) -> str:
    if score >= 65:
        return "HIGH_SURVIVAL"
    if score >= 40:
        return "MEDIUM_SURVIVAL"
    return "LOW_SURVIVAL"


def _coerce_historical_pattern_match(payload: dict) -> dict:
    hpm = payload.get("historical_pattern_match")
    if isinstance(hpm, dict) and (hpm.get("sample_size") or hpm.get("matched_patterns")):
        hpm.setdefault("available", bool(hpm.get("sample_size", 0)))
        return hpm
    return {"available": False, "reason": "no_pattern_memory_match"}


# ─────────────────────────────────────────────────────────────────────
# Public sealing API
# ─────────────────────────────────────────────────────────────────────
def seal_pick_payload(pick_payload: Any) -> dict:
    """Apply the canonical Moneyball payload contract IN PLACE.

    Returns the same payload for chaining.  Fail-soft: a non-dict input
    is converted into a new dict with all layers marked as
    ``available:false``.

    **F87 isolation guard:** when the payload's ``sport`` field is set
    and is NOT in the MLB/baseball family, this function is a strict
    no-op — it returns the payload unchanged so the football pipeline
    can never accidentally pull in MLB-only modules (advanced snapshot,
    sabermetrics audit, quality_contact_matchup, …). When ``sport`` is
    missing the legacy MLB behaviour is preserved (assumed MLB), which
    is what the existing F91/F92 tests expect.
    """
    if not isinstance(pick_payload, dict):
        pick_payload = {}

    # F87 — sport isolation guard. Reject any non-MLB sport early.
    sport = pick_payload.get("sport")
    if sport is not None:
        sport_norm = str(sport).strip().lower()
        if sport_norm and sport_norm not in ("mlb", "baseball"):
            # Stamp a single marker so callers / tests can verify the
            # gate fired, but do NOT mutate picks or add MLB-specific
            # blocks. Returning early prevents accidental imports of
            # MLB QCM / sabermetrics / advanced_snapshot modules.
            pick_payload.setdefault(
                "qcm_audit",
                {"applied": False,
                 "reason":  "PAYLOAD_NOT_MLB",
                 "sport":   sport_norm},
            )
            return pick_payload

    # Phase F91 — compute QCM BEFORE the advanced_snapshot coercion
    # would overwrite the legacy top-level ``*_team_advanced`` keys.
    # The QCM module reads those keys directly via
    # ``extract_mlb_advanced_context``; if we let the coercion run
    # first the QCM block would always show as unavailable.
    _qcm_block = _coerce_quality_contact_matchup(pick_payload)

    pick_payload["advanced_stats_snapshot"] = _coerce_advanced_snapshot(pick_payload)
    pick_payload["pressure_base"]            = _coerce_pressure_base(pick_payload)
    pick_payload["sabermetrics_audit"]       = _coerce_sabermetrics_audit(pick_payload)
    pick_payload["market_selection"]         = _coerce_market_selection(pick_payload)
    # Backward compat: legacy consumers read `fragility_score` as a
    # scalar number (e.g. `item.fragility_score != null`). Keep the
    # scalar in place if it already exists, and expose the structured
    # tier/components in a NEW field `fragility_audit`.
    if not isinstance(pick_payload.get("fragility_score"), (int, float)):
        # No legacy scalar → it's safe to attach a structured block here.
        pick_payload["fragility_score"]      = _coerce_fragility_score(pick_payload)
    pick_payload["fragility_audit"]          = _coerce_fragility_score(pick_payload)
    if not isinstance(pick_payload.get("script_survival_score"), (int, float)):
        pick_payload["script_survival_score"] = _coerce_script_survival(pick_payload)
    pick_payload["script_survival_audit"]    = _coerce_script_survival(pick_payload)
    pick_payload["historical_pattern_match"] = _coerce_historical_pattern_match(pick_payload)

    # Computed audit blocks.
    pick_payload["ghost_edges"]         = build_ghost_edges_summary(pick_payload)
    pick_payload["pattern_memory_audit"] = build_pattern_memory_audit(pick_payload)
    pick_payload["manual_odds_review"]  = build_manual_odds_review(pick_payload)
    # Phase F91 — Quality Contact Matchup (computed early; CONTEXTUAL,
    # NEVER mutates picks).
    pick_payload["quality_contact_matchup"] = _qcm_block
    # Phase F92 — apply QCM signals to candidate picks (in-place delta).
    _apply_qcm_signals_to_picks(pick_payload)

    return pick_payload


def _apply_qcm_signals_to_picks(payload: dict) -> None:
    """Phase F92 — Run the QCM signals applier on every pick of this
    payload. Fail-soft: missing block / module → no-op. Mutates picks
    in place; never invents new picks; never modifies pick ordering.

    Audit trail goes into ``payload["qcm_audit"]`` so the orchestrator
    and UI can show *why* each Over/Under was adjusted.
    """
    qcm_block = payload.get("quality_contact_matchup") or {}
    if not qcm_block.get("available"):
        return
    picks = payload.get("picks")
    if not isinstance(picks, list) or not picks:
        return
    try:
        from .mlb_qcm_signals_applier import (
            apply_qcm_to_candidate, qcm_hard_veto_active,
        )
    except Exception:  # noqa: BLE001
        return

    audits: list[dict] = []
    for idx, pick in enumerate(picks):
        if not isinstance(pick, dict):
            continue
        try:
            audit = apply_qcm_to_candidate(pick, qcm_block)
        except Exception:  # noqa: BLE001
            continue
        audit["pick_index"] = idx
        audit["market"]     = pick.get("market") or pick.get("market_key")
        audits.append(audit)

    try:
        hard_veto = qcm_hard_veto_active(qcm_block)
    except Exception:  # noqa: BLE001
        hard_veto = False

    payload["qcm_audit"] = {
        "applied_count":    sum(1 for a in audits if a.get("applied")),
        "hard_veto_hint":   hard_veto,
        "audits":           audits,
    }


def _coerce_quality_contact_matchup(payload: dict) -> dict:
    """Phase F91 — attach the Quality Contact Matchup block.

    The block is computed by the pure module
    ``services.mlb_quality_contact_matchup`` and is **contextual only**:
    it never mutates ``picks[]`` and never participates in ranking.
    Fail-soft — when the module / data isn't available we surface a
    canonical ``{"available": False, ...}`` shape so the UI can always
    read the key without branching.
    """
    try:
        from .mlb_quality_contact_matchup import compute_quality_contact_matchup
    except Exception as exc:  # noqa: BLE001
        return {
            "available":     False,
            "reason_codes":  ["QCM_MODULE_UNAVAILABLE"],
            "signals":       [],
            "message_debug": f"import failed: {exc}",
        }
    try:
        return compute_quality_contact_matchup(payload)
    except Exception as exc:  # noqa: BLE001 — fail-soft contract.
        return {
            "available":     False,
            "reason_codes":  ["QCM_COMPUTE_EXCEPTION"],
            "signals":       [],
            "message_debug": str(exc),
        }


# ─────────────────────────────────────────────────────────────────────
# Sport guard — used by callers that explicitly want to skip non-MLB.
# ─────────────────────────────────────────────────────────────────────
def is_mlb_payload(pick_payload: Any) -> bool:
    if not isinstance(pick_payload, dict):
        return False
    sport = (pick_payload.get("sport") or "").lower()
    return sport in ("baseball", "mlb")
