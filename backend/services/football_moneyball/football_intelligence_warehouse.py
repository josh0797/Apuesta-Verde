"""Football Intelligence Warehouse — Mongo IO for the Football Moneyball
layer.

Four Mongo collections:
  * ``football_team_daily_profiles``        — daily team snapshot (under
    rates, btts, gf/ga avg, early-goal pct, corner profile, etc.).
  * ``football_match_intelligence_snapshots`` — full pregame/live snapshot
    digest tied to ``match_id`` (+ ``day``).
  * ``football_market_results``             — settled markets (post-settle
    feedback). Contains the pattern keys used at pick time.
  * ``football_pattern_memory``             — per-pattern aggregate hit
    rate / ROI used as a conservative confidence adjuster.

Design principles (NON-NEGOTIABLE):
  * Pure read/write helpers. No business logic except the conservative
    sample-size gates that govern adjustment caps.
  * Fail-soft. Every coroutine returns a neutral result when ``db`` is
    None or any exception bubbles up.
  * Idempotent writes via ``replace_one(..., upsert=True)`` with natural
    composite keys, never ``_id``.
  * Football-only. No MLB / Basketball touchpoints.

Sample-size gates (mirrors MLB policy, conservative):
  * ``sample_size < 20``       → warning only, no confidence adjustment.
  * ``20 ≤ sample_size < 50``  → moderate ±4 adjustment allowed.
  * ``sample_size ≥ 50 AND roi > 0`` → larger adjustment up to ±7.

These gates are intentionally tighter than MLB (MLB caps at ±5/±8)
because football has more variance per match.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("football_moneyball.warehouse")

# ─────────────────────────────────────────────────────────────────────
# Collections
# ─────────────────────────────────────────────────────────────────────
COLL_TEAM_DAILY      = "football_team_daily_profiles"
COLL_MATCH_SNAPSHOTS = "football_match_intelligence_snapshots"
COLL_MARKET_RESULTS  = "football_market_results"
COLL_PATTERN_MEMORY  = "football_pattern_memory"

# Freshness window for a cached daily profile (UTC days).
FRESHNESS_HOURS_DEFAULT = 24

# Pattern memory sample-size gates (more conservative than MLB).
PATTERN_SAMPLE_NO_ADJUST  = 20
PATTERN_SAMPLE_MODERATE   = 50
PATTERN_MAX_ADJUSTMENT_MODERATE = 4.0
PATTERN_MAX_ADJUSTMENT_STRONG   = 7.0

# Reason codes (canonical — used by the orchestrator + UI).
RC_PATTERN_LOW_SAMPLE     = "FOOTBALL_PATTERN_LOW_SAMPLE"
RC_PATTERN_MODERATE_BOOST = "FOOTBALL_PATTERN_MODERATE_BOOST"
RC_PATTERN_STRONG_BOOST   = "FOOTBALL_PATTERN_STRONG_BOOST"
RC_PATTERN_NEGATIVE_ROI   = "FOOTBALL_PATTERN_NEGATIVE_ROI"
RC_PATTERN_NO_MATCH       = "FOOTBALL_PATTERN_NO_MATCH"
RC_PATTERN_DISABLED       = "FOOTBALL_PATTERN_DISABLED"
RC_WAREHOUSE_DISABLED     = "FOOTBALL_WAREHOUSE_DISABLED"

# Canonical pattern keys. Conservative + football-specific.
KNOWN_PATTERNS = (
    "BOTH_TEAMS_LOW_PRESSURE_UNDER_PROFILE",
    "HIGH_PRESSURE_BOTH_SIDES",
    "UNDER_PROFILE_STRONG_BOTH",
    "EARLY_GOAL_RISK_HIGH",
    "CLEAN_SHEET_BIAS_BOTH",
    "BTTS_PROFILE_STRONG",
    "CORNERS_VOLATILE_TRAP",
    "PROTECTED_UNDER_3_5_OVER_UNDER_2_5",
    "FORM_GUARD_FRAGILE",
    "LEAGUE_LOW_QUALITY_WARNING",
)


# ─────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────
def _day_key(when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    return when.strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_fresh(updated_iso: str | None, hours: float) -> bool:
    if not updated_iso:
        return False
    try:
        dt = datetime.fromisoformat(updated_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    try:
        delta = datetime.now(timezone.utc) - dt
    except Exception:
        return False
    return delta.total_seconds() < hours * 3600


# ─────────────────────────────────────────────────────────────────────
# Indexes (best-effort, idempotent)
# ─────────────────────────────────────────────────────────────────────
async def ensure_football_indexes(db) -> dict:
    """Create Mongo indexes for the 4 football collections.

    Best-effort & idempotent: if any index creation fails (permissions,
    cluster topology, etc.), log a warning and continue. Returns a small
    audit dict so the caller can log what happened.
    """
    if db is None:
        return {"available": False, "reason": "db_is_none"}

    result = {"available": True, "created": [], "errors": []}

    async def _safe_idx(coll: str, keys, **kwargs):
        try:
            await db[coll].create_index(keys, **kwargs)
            result["created"].append(f"{coll}:{keys}")
        except Exception as exc:
            result["errors"].append({
                "collection": coll, "keys": str(keys), "error": str(exc)[:200],
            })
            log.debug("ensure_football_indexes failed (%s, %s): %s", coll, keys, exc)

    # football_team_daily_profiles
    await _safe_idx(COLL_TEAM_DAILY, [("team_id", 1), ("day", 1)], unique=False)
    await _safe_idx(COLL_TEAM_DAILY, [("team_name", 1), ("day", 1)])

    # football_match_intelligence_snapshots
    await _safe_idx(COLL_MATCH_SNAPSHOTS, [("match_id", 1), ("day", 1)])
    await _safe_idx(COLL_MATCH_SNAPSHOTS, [("day", 1)])
    await _safe_idx(COLL_MATCH_SNAPSHOTS, [("league", 1)])
    await _safe_idx(COLL_MATCH_SNAPSHOTS, [("selected_market", 1)])

    # football_market_results
    await _safe_idx(COLL_MARKET_RESULTS, [("user_id", 1), ("match_id", 1)])
    await _safe_idx(COLL_MARKET_RESULTS, [("match_id", 1), ("market", 1)])
    await _safe_idx(COLL_MARKET_RESULTS, [("settled_at", -1)])
    await _safe_idx(COLL_MARKET_RESULTS, [("result", 1)])
    await _safe_idx(COLL_MARKET_RESULTS, [("pattern_keys", 1)])

    # football_pattern_memory
    await _safe_idx(COLL_PATTERN_MEMORY, [("pattern_key", 1)], unique=True)
    await _safe_idx(COLL_PATTERN_MEMORY, [("sport", 1)])
    await _safe_idx(COLL_PATTERN_MEMORY, [("enabled", 1)])
    await _safe_idx(COLL_PATTERN_MEMORY, [("sample_size", -1)])
    await _safe_idx(COLL_PATTERN_MEMORY, [("last_updated", -1)])

    return result


# ─────────────────────────────────────────────────────────────────────
# Team daily profile read / write
# ─────────────────────────────────────────────────────────────────────
async def load_team_profile(
    db,
    team_id: str | int,
    *,
    day: str | None = None,
    max_age_hours: float = FRESHNESS_HOURS_DEFAULT,
) -> dict | None:
    if db is None or team_id is None:
        return None
    try:
        day = day or _day_key()
        doc = await db[COLL_TEAM_DAILY].find_one({
            "team_id": str(team_id),
            "day":     day,
        })
        if not doc:
            return None
        if not _is_fresh(doc.get("updated_at"), max_age_hours):
            return None
        doc.pop("_id", None)
        return doc
    except Exception as exc:
        log.debug("load_team_profile failed: %s", exc)
        return None


async def upsert_team_profile(
    db,
    team_id: str | int,
    profile: dict,
    *,
    team_name: str | None = None,
    day: str | None = None,
) -> bool:
    if db is None or team_id is None or not isinstance(profile, dict):
        return False
    try:
        day = day or _day_key()
        payload = {
            "team_id":    str(team_id),
            "team_name":  team_name,
            "day":        day,
            "updated_at": _now_iso(),
            "profile":    profile,
        }
        await db[COLL_TEAM_DAILY].replace_one(
            {"team_id": str(team_id), "day": day},
            payload,
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("upsert_team_profile failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Match intelligence snapshot
# ─────────────────────────────────────────────────────────────────────
async def persist_match_intelligence_snapshot(
    db,
    *,
    match_id: str | int,
    snapshot: dict,
    day: str | None = None,
    league: str | None = None,
    selected_market: str | None = None,
    pattern_keys: list[str] | None = None,
) -> bool:
    """Idempotently persist a pregame/live snapshot for a football match.

    The snapshot is expected to be produced by
    :func:`football_snapshot_builder.build_full_intelligence_snapshot`.
    """
    if db is None or match_id is None or not isinstance(snapshot, dict):
        return False
    try:
        day = day or _day_key()
        payload = {
            "match_id":         str(match_id),
            "day":              day,
            "league":           league,
            "selected_market":  selected_market,
            "pattern_keys":     list(pattern_keys or []),
            "updated_at":       _now_iso(),
            "snapshot":         snapshot,
        }
        await db[COLL_MATCH_SNAPSHOTS].replace_one(
            {"match_id": str(match_id), "day": day},
            payload,
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("persist_match_intelligence_snapshot failed: %s", exc)
        return False


async def load_match_intelligence_snapshot(
    db,
    match_id: str | int,
    *,
    day: str | None = None,
) -> dict | None:
    if db is None or match_id is None:
        return None
    try:
        query: dict[str, Any] = {"match_id": str(match_id)}
        if day:
            query["day"] = day
        doc = await db[COLL_MATCH_SNAPSHOTS].find_one(
            query, sort=[("updated_at", -1)],
        )
        if not doc:
            return None
        doc.pop("_id", None)
        clean: dict = dict(doc)
        return clean
    except Exception as exc:
        log.debug("load_match_intelligence_snapshot failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────
# Pattern memory — lookup + update
# ─────────────────────────────────────────────────────────────────────
async def lookup_pattern_match(db, pattern_keys: list[str]) -> dict:
    """Aggregate pattern memory for the supplied keys.

    Returns a canonical summary dict (always safe to read even when DB
    is None or empty).
    """
    out: dict[str, Any] = {
        "matched":               [],
        "primary_key":           None,
        "sample_size":           0,
        "hit_rate":              None,
        "roi":                   None,
        "best_market":           None,
        "enabled":               True,
        "reason_codes":          [],
        "confidence_adjustment": 0.0,
        "warning":               None,
    }
    if db is None or not pattern_keys:
        out["reason_codes"].append(RC_PATTERN_NO_MATCH)
        if db is None:
            out["reason_codes"].append(RC_WAREHOUSE_DISABLED)
        return out

    try:
        cursor = db[COLL_PATTERN_MEMORY].find(
            {"pattern_key": {"$in": list(pattern_keys)}},
        )
        docs: list[dict] = []
        async for d in cursor:
            docs.append(d)
        if not docs:
            out["reason_codes"].append(RC_PATTERN_NO_MATCH)
            return out

        # Filter out explicitly disabled patterns. They still count in
        # `matched` (for transparency) but they don't drive adjustments.
        enabled_docs = [d for d in docs if d.get("enabled", True)]
        if not enabled_docs:
            out["matched"] = [d.get("pattern_key") for d in docs]
            out["enabled"] = False
            out["reason_codes"].append(RC_PATTERN_DISABLED)
            return out

        enabled_docs.sort(
            key=lambda d: int(d.get("sample_size") or 0),
            reverse=True,
        )
        primary = enabled_docs[0]
        sample_size = int(primary.get("sample_size") or 0)
        hit_rate = float(primary.get("hit_rate") or 0.0)
        roi = float(primary.get("roi") or 0.0)
        best_market = primary.get("best_market")

        out.update({
            "matched":     [d.get("pattern_key") for d in docs],
            "primary_key": primary.get("pattern_key"),
            "sample_size": sample_size,
            "hit_rate":    hit_rate,
            "roi":         roi,
            "best_market": best_market,
        })

        adjustment, codes, warning = _compute_pattern_adjustment(
            sample_size=sample_size, hit_rate=hit_rate, roi=roi,
        )
        out["confidence_adjustment"] = adjustment
        out["reason_codes"].extend(codes)
        out["warning"] = warning
        return out
    except Exception as exc:
        log.debug("lookup_pattern_match failed: %s", exc)
        out["reason_codes"].append(RC_WAREHOUSE_DISABLED)
        return out


def _compute_pattern_adjustment(
    *, sample_size: int, hit_rate: float, roi: float,
) -> tuple[float, list[str], str | None]:
    """Pure: translate aggregate stats into a capped adjustment."""
    codes: list[str] = []
    warning: str | None = None

    if sample_size < PATTERN_SAMPLE_NO_ADJUST:
        codes.append(RC_PATTERN_LOW_SAMPLE)
        warning = (
            f"Patrón histórico con muestra baja (n={sample_size}); "
            "se omite ajuste por confianza."
        )
        return 0.0, codes, warning

    if sample_size < PATTERN_SAMPLE_MODERATE:
        sign = 1.0 if roi >= 0 else -1.0
        magnitude = min(1.0, abs(hit_rate - 0.50) * 5.0)
        adjustment = round(sign * magnitude * PATTERN_MAX_ADJUSTMENT_MODERATE, 2)
        codes.append(RC_PATTERN_MODERATE_BOOST)
        if roi < 0:
            codes.append(RC_PATTERN_NEGATIVE_ROI)
        return adjustment, codes, warning

    # Strong sample
    if roi > 0:
        magnitude = min(1.0, abs(hit_rate - 0.50) * 5.0 + min(0.4, roi))
        adjustment = round(magnitude * PATTERN_MAX_ADJUSTMENT_STRONG, 2)
        codes.append(RC_PATTERN_STRONG_BOOST)
    else:
        adjustment = round(-PATTERN_MAX_ADJUSTMENT_MODERATE * 0.6, 2)
        codes.append(RC_PATTERN_NEGATIVE_ROI)
    return adjustment, codes, warning


_VOID_OUTCOMES = {"void", "push", "refund", "refunded", "cancelled", "canceled"}


def _normalise_result_outcome(outcome: str | None, won: bool) -> str:
    """Return a canonical warehouse outcome.

    Back-compat: callers that have not been updated still pass only
    ``won``. In that case we preserve the old won/lost behavior.
    """
    o = (outcome or "").strip().lower()
    if o in _VOID_OUTCOMES:
        return "void"
    if o in {"won", "win", "hit", "w"}:
        return "won"
    if o in {"lost", "loss", "miss", "l"}:
        return "lost"
    return "won" if won else "lost"


async def update_pattern_memory_from_result(
    db,
    *,
    pattern_keys: list[str],
    market: str | None,
    stake: float,
    won: bool,
    payout: float = 0.0,
    outcome: str | None = None,
) -> bool:
    """Increment pattern memory using a settled bet result. Fail-soft.

    ``outcome`` fixes the void/push/refund path: void-like outcomes are
    financially neutral but are NOT valid attempts for hit-rate purposes.
    They therefore increment ``voids`` plus stake/payout, but do not
    increment ``sample_size``/``wins`` nor market-ledger ``samples``.
    """
    if db is None or not pattern_keys:
        return False
    try:
        ts = _now_iso()
        normalized_outcome = _normalise_result_outcome(outcome, won)
        is_void = normalized_outcome == "void"
        is_win = normalized_outcome == "won"

        for pk in pattern_keys:
            existing = await db[COLL_PATTERN_MEMORY].find_one({"pattern_key": pk}) or {}

            prev_sample_size = int(existing.get("sample_size") or 0)
            prev_wins = int(existing.get("wins") or 0)
            prev_voids = int(existing.get("voids") or 0)

            sample_size = prev_sample_size + (0 if is_void else 1)
            wins = prev_wins + (1 if is_win else 0)
            voids = prev_voids + (1 if is_void else 0)

            total_stake = float(existing.get("total_stake") or 0.0) + float(stake)
            total_payout = float(existing.get("total_payout") or 0.0) + float(payout)
            hit_rate = round(wins / sample_size, 4) if sample_size else 0.0
            roi = (
                round((total_payout - total_stake) / total_stake, 4)
                if total_stake > 0 else 0.0
            )

            market_ledger = existing.get("market_ledger") or {}
            if market:
                ml_existing = market_ledger.get(market) or {}
                ml = {
                    "samples": int(ml_existing.get("samples") or 0),
                    "wins":    int(ml_existing.get("wins") or 0),
                    "voids":   int(ml_existing.get("voids") or 0),
                    "stake":   float(ml_existing.get("stake") or 0.0),
                    "payout":  float(ml_existing.get("payout") or 0.0),
                }
                if is_void:
                    ml["voids"] += 1
                else:
                    ml["samples"] += 1
                    if is_win:
                        ml["wins"] += 1
                ml["stake"]  += float(stake)
                ml["payout"] += float(payout)
                market_ledger[market] = ml

            best_market = None
            best_score = -1e9
            for mname, m in (market_ledger or {}).items():
                samples = int(m.get("samples") or 0)
                # Skip all-void/no-attempt markets and low valid samples.
                if samples == 0 or samples < 5:
                    continue
                m_hr = (int(m.get("wins") or 0) / samples) if samples else 0
                m_stake = float(m.get("stake") or 0.0)
                m_payout = float(m.get("payout") or 0.0)
                m_roi = ((m_payout - m_stake) / m_stake) if m_stake > 0 else 0
                score = m_hr * (1 + max(0.0, m_roi))
                if score > best_score:
                    best_score = score
                    best_market = mname

            await db[COLL_PATTERN_MEMORY].replace_one(
                {"pattern_key": pk},
                {
                    "pattern_key":  pk,
                    "sport":        "football",
                    "enabled":      bool(existing.get("enabled", True)),
                    "sample_size":  sample_size,
                    "wins":         wins,
                    "voids":        voids,
                    "hit_rate":     hit_rate,
                    "total_stake":  total_stake,
                    "total_payout": total_payout,
                    "roi":          roi,
                    "market_ledger": market_ledger,
                    "best_market":  best_market,
                    "last_updated": ts,
                },
                upsert=True,
            )
        return True
    except Exception as exc:
        log.debug("update_pattern_memory_from_result failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Market results (settled feedback)
# ─────────────────────────────────────────────────────────────────────
async def persist_football_market_result(
    db,
    *,
    match_id: str | int,
    user_id: str | None,
    market: str | None,
    selection: str | None = None,
    odds: float | None = None,
    pattern_keys: list[str] | None = None,
    stake: float = 1.0,
    won: bool = False,
    payout: float = 0.0,
    result: str | None = None,
    final_score: dict | None = None,
    snapshot_ref: dict | None = None,
    league_tier: str | None = None,
    offense_bucket: str | None = None,
    lambda_total: float | None = None,
    lambda_home: float | None = None,
    lambda_away: float | None = None,
    dc_rho_used: float | None = None,
    goals_dispersion_ratio: float | None = None,
    p_under_2_5_poisson: float | None = None,
    p_under_3_5_poisson: float | None = None,
    p_under_2_5_dc_nb: float | None = None,
    p_under_3_5_dc_nb: float | None = None,
    dc_nb_delta_2_5_pts: float | None = None,
    dc_nb_delta_3_5_pts: float | None = None,
) -> bool:
    """Insert a settled football market result and update pattern memory.

    Extended (Pieza 4 / 5):
      * ``league_tier``     defaults to ``"UNKNOWN_LEAGUE"`` (fail-soft)
      * ``offense_bucket``  defaults to ``"MODERATE_OFFENSE"`` (fail-soft)
      * full DC/NB telemetry persisted so the calibration loop never has
        to recompute lambdas from scratch.

    Fail-soft: if anything raises, returns False and logs at debug level.
    """
    if db is None or match_id is None:
        return False
    try:
        payload = {
            "match_id":     str(match_id),
            "user_id":      user_id,
            "sport":        "football",
            "market":       market,
            "selection":    selection,
            "odds":         float(odds) if odds is not None else None,
            "pattern_keys": list(pattern_keys or []),
            "stake":        float(stake),
            "won":          bool(won),
            "payout":       float(payout),
            "result":       result,
            "final_score":  final_score,
            "snapshot_ref": snapshot_ref,
            # Calibration buckets (Pieza 4 / 5)
            "league_tier":   league_tier or "UNKNOWN_LEAGUE",
            "offense_bucket": offense_bucket or "MODERATE_OFFENSE",
            # DC + NB telemetry
            "lambda_total":          lambda_total,
            "lambda_home":           lambda_home,
            "lambda_away":           lambda_away,
            "dc_rho_used":           dc_rho_used,
            "goals_dispersion_ratio": goals_dispersion_ratio,
            "p_under_2_5_poisson":   p_under_2_5_poisson,
            "p_under_3_5_poisson":   p_under_3_5_poisson,
            "p_under_2_5_dc_nb":     p_under_2_5_dc_nb,
            "p_under_3_5_dc_nb":     p_under_3_5_dc_nb,
            "dc_nb_delta_2_5_pts":   dc_nb_delta_2_5_pts,
            "dc_nb_delta_3_5_pts":   dc_nb_delta_3_5_pts,
            "settled_at":   _now_iso(),
        }
        await db[COLL_MARKET_RESULTS].insert_one(payload)
        await update_pattern_memory_from_result(
            db,
            pattern_keys=pattern_keys or [],
            market=market,
            stake=stake,
            won=won,
            payout=payout,
            outcome=result,
        )
        return True
    except Exception as exc:
        log.debug("persist_football_market_result failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Orchestrator-friendly facade
# ─────────────────────────────────────────────────────────────────────
async def attach_pattern_match_to_payload(
    db,
    pick_payload: dict,
    pattern_keys: list[str] | None = None,
) -> dict:
    """Compute / use pattern keys + lookup memory + mutate payload.

    Pure-ish: mutates ``pick_payload`` in place to add a canonical
    ``historical_pattern_match`` block (mirrors top-level fields too for
    UI/back-compat). Returns the summary dict.
    """
    if not isinstance(pick_payload, dict):
        return {}

    keys = list(pattern_keys or [])
    if not keys:
        # Lazy import to avoid circular ref at package init.
        try:
            from .football_pattern_memory import derive_pattern_keys as _dk
            keys = _dk(pick_payload)
        except Exception as exc:
            log.debug("derive_pattern_keys failed: %s", exc)
            keys = []

    summary = await lookup_pattern_match(db, keys)

    pick_payload["historical_pattern_match"] = {
        "matched_patterns":              summary["matched"],
        "primary_pattern":               summary["primary_key"],
        "sample_size":                   summary["sample_size"],
        "historical_hit_rate":           summary["hit_rate"],
        "historical_roi":                summary["roi"],
        "best_historical_market":        summary["best_market"],
        "pattern_confidence_adjustment": summary["confidence_adjustment"],
        "pattern_reason_codes":          summary["reason_codes"],
        "warning":                       summary["warning"],
        "enabled":                       summary["enabled"],
    }
    pick_payload.setdefault("historical_hit_rate",            summary["hit_rate"])
    pick_payload.setdefault("historical_roi",                 summary["roi"])
    pick_payload.setdefault("best_historical_market",         summary["best_market"])
    pick_payload.setdefault("pattern_confidence_adjustment",  summary["confidence_adjustment"])
    pick_payload.setdefault("pattern_reason_codes",           summary["reason_codes"])
    return summary


async def summarize_pattern_memory(
    db,
    *,
    limit: int = 25,
    enabled_only: bool = True,
) -> dict:
    """Return a compact roll-up of the football pattern memory for the
    /api/football/pattern-memory/summary endpoint.

    Fail-soft: returns ``{available:false, reason:...}`` on any DB error.
    """
    if db is None:
        return {"available": False, "reason": "db_unavailable", "items": []}
    try:
        query: dict[str, Any] = {}
        if enabled_only:
            query["enabled"] = {"$ne": False}
        cursor = db[COLL_PATTERN_MEMORY].find(query).sort(
            "sample_size", -1
        ).limit(int(limit))
        items: list[dict] = []
        async for d in cursor:
            items.append({
                "pattern_key":  d.get("pattern_key"),
                "sport":        d.get("sport", "football"),
                "enabled":      bool(d.get("enabled", True)),
                "sample_size":  int(d.get("sample_size") or 0),
                "wins":         int(d.get("wins") or 0),
                "hit_rate":     d.get("hit_rate"),
                "roi":          d.get("roi"),
                "voids":       int(d.get("voids") or 0),
                "best_market":  d.get("best_market"),
                "last_updated": d.get("last_updated"),
            })
        return {
            "available":    True,
            "count":        len(items),
            "items":        items,
            "generated_at": _now_iso(),
        }
    except Exception as exc:
        log.debug("summarize_pattern_memory failed: %s", exc)
        return {"available": False, "reason": "db_error", "items": []}


__all__ = [
    # Collections
    "COLL_TEAM_DAILY",
    "COLL_MATCH_SNAPSHOTS",
    "COLL_MARKET_RESULTS",
    "COLL_PATTERN_MEMORY",
    # Constants
    "FRESHNESS_HOURS_DEFAULT",
    "PATTERN_SAMPLE_NO_ADJUST",
    "PATTERN_SAMPLE_MODERATE",
    "PATTERN_MAX_ADJUSTMENT_MODERATE",
    "PATTERN_MAX_ADJUSTMENT_STRONG",
    "KNOWN_PATTERNS",
    # Reason codes
    "RC_PATTERN_LOW_SAMPLE",
    "RC_PATTERN_MODERATE_BOOST",
    "RC_PATTERN_STRONG_BOOST",
    "RC_PATTERN_NEGATIVE_ROI",
    "RC_PATTERN_NO_MATCH",
    "RC_PATTERN_DISABLED",
    "RC_WAREHOUSE_DISABLED",
    # Public API
    "ensure_football_indexes",
    "load_team_profile",
    "upsert_team_profile",
    "persist_match_intelligence_snapshot",
    "load_match_intelligence_snapshot",
    "lookup_pattern_match",
    "update_pattern_memory_from_result",
    "persist_football_market_result",
    "attach_pattern_match_to_payload",
    "summarize_pattern_memory",
]
