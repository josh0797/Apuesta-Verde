"""MLB Intelligence Warehouse (Fix 3).

A lightweight Moneyball-style historical layer for MLB that caches the
precomputed intelligence (statcast/pressure/sabermetrics/ghost-edges/
market_selection) per day so the engine **does not have to recompute
every signal from scratch** on every analysis run.

Five Mongo collections:

  • ``mlb_team_daily_profiles``       — daily team snapshot (hits/runs
    L5/L15, BB, HR, OPS, WAR, pressure_tier).
  • ``mlb_pitcher_daily_profiles``    — daily pitcher snapshot (ERA,
    xERA, xwOBA allowed, hard-hit%, barrel%, K%, BB%, FIP, WHIP).
  • ``mlb_game_intelligence_snapshots`` — full per-game snapshot tied
    to ``game_pk`` (includes the entire ``pick_payload`` digest).
  • ``mlb_market_results``            — settled markets with EV/ROI.
  • ``mlb_pattern_memory``            — per-pattern hit rate / ROI
    aggregated over time. Used as a conservative confidence adjuster.

Design principles (NON-NEGOTIABLE):
  * **Pure read/write helpers.** No business logic outside the rules
    that explicitly govern sample size / adjustment caps.
  * **Fail-soft.** Every public coroutine returns a neutral result
    when ``db is None`` or any operation raises — the engine MUST
    keep running unchanged.
  * **Idempotent writes.** Use ``replace_one(..., upsert=True)`` with
    composite natural keys, never `_id`.
  * **MLB-only.** No football/basketball touchpoints exist.

Sample-size gates (per user spec):
  * ``sample_size < 20``  → warning only, **no confidence adjustment**.
  * ``20 ≤ sample_size < 50`` → moderate ±5 adjustment allowed.
  * ``sample_size ≥ 50 AND roi > 0`` → larger adjustment up to ±8.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
COLL_TEAM_DAILY        = "mlb_team_daily_profiles"
COLL_PITCHER_DAILY     = "mlb_pitcher_daily_profiles"
COLL_GAME_SNAPSHOTS    = "mlb_game_intelligence_snapshots"
COLL_MARKET_RESULTS    = "mlb_market_results"
COLL_PATTERN_MEMORY    = "mlb_pattern_memory"

# Profile freshness window (default: 1 calendar day).
FRESHNESS_HOURS_DEFAULT = 24

# Pattern memory sample-size gates
PATTERN_SAMPLE_NO_ADJUST  = 20
PATTERN_SAMPLE_MODERATE   = 50
PATTERN_MAX_ADJUSTMENT_MODERATE = 5.0
PATTERN_MAX_ADJUSTMENT_STRONG   = 8.0

# Pattern reason codes
RC_PATTERN_LOW_SAMPLE       = "PATTERN_MEMORY_LOW_SAMPLE"
RC_PATTERN_MODERATE_BOOST   = "PATTERN_MEMORY_MODERATE_BOOST"
RC_PATTERN_STRONG_BOOST     = "PATTERN_MEMORY_STRONG_BOOST"
RC_PATTERN_NEGATIVE_ROI     = "PATTERN_MEMORY_NEGATIVE_ROI"
RC_PATTERN_NO_MATCH         = "PATTERN_MEMORY_NO_MATCH"
RC_WAREHOUSE_DISABLED       = "WAREHOUSE_DISABLED"

# Canonical pattern keys (the user spec lists these explicitly).
KNOWN_PATTERNS = (
    "LOW_PRESSURE_STRONG_FIP_BOTH",
    "HIGH_HIT_PRESSURE_LOW_RUN_CONVERSION",
    "ERA_UNDERSTATES_RISK",
    "F5_UNDER_BETTER_THAN_FULL_GAME",
    "RUN_LINE_MARGIN_SUPPORTED",
    "MONEYLINE_SAFER_THAN_RUN_LINE",
    "GHOST_EDGE_BLOCKED_PICK",
)


# ─────────────────────────────────────────────────────────────────────
# Utility — date keys (UTC YYYY-MM-DD)
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
    except ValueError:
        return False
    delta = datetime.now(timezone.utc) - dt
    return delta.total_seconds() < hours * 3600


# ─────────────────────────────────────────────────────────────────────
# Team profile read/write
# ─────────────────────────────────────────────────────────────────────
async def load_team_profile(db, team_id: str | int,
                              *, day: str | None = None,
                              max_age_hours: float = FRESHNESS_HOURS_DEFAULT,
                              ) -> dict | None:
    """Return the cached daily team profile or None when stale/absent.

    Fail-soft: if ``db is None`` or any error occurs, returns None so the
    engine recomputes from primary sources.
    """
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
        return doc
    except Exception as exc:
        log.debug("load_team_profile failed: %s", exc)
        return None


async def upsert_team_profile(db, team_id: str | int, profile: dict,
                                *, day: str | None = None) -> bool:
    if db is None or team_id is None or not isinstance(profile, dict):
        return False
    try:
        day = day or _day_key()
        payload = {
            "team_id":   str(team_id),
            "day":       day,
            "updated_at": _now_iso(),
            "profile":   profile,
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
# Pitcher profile read/write
# ─────────────────────────────────────────────────────────────────────
async def load_pitcher_profile(db, pitcher_id: str | int,
                                  *, day: str | None = None,
                                  max_age_hours: float = FRESHNESS_HOURS_DEFAULT,
                                  ) -> dict | None:
    if db is None or pitcher_id is None:
        return None
    try:
        day = day or _day_key()
        doc = await db[COLL_PITCHER_DAILY].find_one({
            "pitcher_id": str(pitcher_id),
            "day":        day,
        })
        if not doc:
            return None
        if not _is_fresh(doc.get("updated_at"), max_age_hours):
            return None
        return doc
    except Exception as exc:
        log.debug("load_pitcher_profile failed: %s", exc)
        return None


async def upsert_pitcher_profile(db, pitcher_id: str | int, profile: dict,
                                    *, day: str | None = None) -> bool:
    if db is None or pitcher_id is None or not isinstance(profile, dict):
        return False
    try:
        day = day or _day_key()
        payload = {
            "pitcher_id": str(pitcher_id),
            "day":        day,
            "updated_at": _now_iso(),
            "profile":    profile,
        }
        await db[COLL_PITCHER_DAILY].replace_one(
            {"pitcher_id": str(pitcher_id), "day": day},
            payload,
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("upsert_pitcher_profile failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Game snapshot (full digest tied to game_pk)
# ─────────────────────────────────────────────────────────────────────
def _digest_pick_payload(pick_payload: dict) -> dict:
    """Extract the relevant intelligence fields from a pick_payload.

    Keeps the snapshot compact — we don't store the entire payload, only
    the layers needed for pattern memory / future analysis.
    """
    return {
        "advanced_stats_snapshot": pick_payload.get("advanced_stats_snapshot"),
        "advanced_adjustments":    pick_payload.get("advanced_adjustments"),
        "sabermetrics":            pick_payload.get("sabermetrics"),
        "sabermetrics_audit":      pick_payload.get("sabermetrics_audit"),
        "pressure_base":           pick_payload.get("pressure_base"),
        "pressure_base_impact":    pick_payload.get("pressure_base_impact"),
        "fragility":               pick_payload.get("fragility"),
        "script_survival":         pick_payload.get("script_survival"),
        "pitcher_quality_score":   pick_payload.get("pitcher_quality_score"),
        "market_selection":        pick_payload.get("market_selection"),
        "model_verification":      pick_payload.get("model_verification"),
        "recommendation":          pick_payload.get("recommendation"),
        "reason_codes":            pick_payload.get("reason_codes"),
        "pipeline_meta":           pick_payload.get("pipeline_meta"),
    }


async def persist_game_intelligence_snapshot(
    db,
    *,
    game_pk: str | int,
    match_id: str | int | None,
    home_team_id: str | int | None,
    away_team_id: str | int | None,
    pick_payload: dict,
    day: str | None = None,
) -> bool:
    if db is None or game_pk is None or not isinstance(pick_payload, dict):
        return False
    try:
        day = day or _day_key()
        digest = _digest_pick_payload(pick_payload)
        payload = {
            "game_pk":      str(game_pk),
            "match_id":     str(match_id) if match_id is not None else None,
            "home_team_id": str(home_team_id) if home_team_id is not None else None,
            "away_team_id": str(away_team_id) if away_team_id is not None else None,
            "day":          day,
            "updated_at":   _now_iso(),
            "pattern_keys": derive_pattern_keys(pick_payload),
            "digest":       digest,
        }
        await db[COLL_GAME_SNAPSHOTS].replace_one(
            {"game_pk": str(game_pk), "day": day},
            payload,
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("persist_game_intelligence_snapshot failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Pattern memory — derive + lookup + update
# ─────────────────────────────────────────────────────────────────────
def derive_pattern_keys(pick_payload: dict) -> list[str]:
    """Translate a pick_payload into the canonical pattern keys it matches.

    Pure function (no IO). Returns at most a handful of keys.
    """
    keys: list[str] = []
    if not isinstance(pick_payload, dict):
        return keys

    # Pressure-based
    pb = (pick_payload.get("pressure_base") or {}).get("combined") or {}
    pb_tier = pb.get("pressure_tier")
    pb_flags = pb.get("flags") or {}

    # Sabermetrics edges
    saber = pick_payload.get("sabermetrics") or {}
    home_fip_tier = ((saber.get("home") or {}).get("starting_pitcher_fip") or {}).get("tier")
    away_fip_tier = ((saber.get("away") or {}).get("starting_pitcher_fip") or {}).get("tier")

    # Ghost-edges
    discrepancies = (pick_payload.get("model_verification") or {}).get("discrepancies") or []
    ghost_flags = {d.get("flag") for d in discrepancies if isinstance(d, dict)}

    # Market selection reason codes
    ms_codes = ((pick_payload.get("market_selection") or {}).get("reason_codes") or [])

    # 1. LOW_PRESSURE + STRONG_FIP_BOTH
    if pb_tier == "LOW_PRESSURE" and home_fip_tier in ("ELITE_FIP", "STRONG_FIP") \
            and away_fip_tier in ("ELITE_FIP", "STRONG_FIP"):
        keys.append("LOW_PRESSURE_STRONG_FIP_BOTH")

    # 2. HIGH_HIT_PRESSURE_LOW_RUN_CONVERSION
    if pb_tier == "HIGH_PRESSURE" or pb_flags.get("both_teams_high") \
            or pb_flags.get("any_team_high"):
        keys.append("HIGH_HIT_PRESSURE_LOW_RUN_CONVERSION")

    # 3. ERA_UNDERSTATES_RISK (ghost)
    if "ERA_UNDERSTATES_RISK" in ghost_flags:
        keys.append("ERA_UNDERSTATES_RISK")

    # 4. F5_UNDER_BETTER_THAN_FULL_GAME
    if "F5_UNDER_PREFERRED_OVER_FULL_GAME" in ms_codes:
        keys.append("F5_UNDER_BETTER_THAN_FULL_GAME")

    # 5. RUN_LINE_MARGIN_SUPPORTED
    v2 = pick_payload.get("_mlb_script_v2") or {}
    margin = v2.get("marginProjection") or v2.get("projectedMargin")
    cover  = v2.get("runLineCoverProb") or v2.get("rl_cover_prob")
    try:
        if margin is not None and float(margin) >= 2.0 \
                and cover is not None and float(cover) >= 0.50:
            keys.append("RUN_LINE_MARGIN_SUPPORTED")
    except (TypeError, ValueError):
        pass

    # 6. MONEYLINE_SAFER_THAN_RUN_LINE
    if "MONEYLINE_SAFER_THAN_RUN_LINE" in ms_codes:
        keys.append("MONEYLINE_SAFER_THAN_RUN_LINE")

    # 7. GHOST_EDGE_BLOCKED_PICK
    if "GHOST_EDGE_BLOCKED_PICK" in ms_codes:
        keys.append("GHOST_EDGE_BLOCKED_PICK")

    return keys


async def lookup_pattern_match(db, pattern_keys: list[str]) -> dict:
    """Aggregate the pattern memory for the supplied keys.

    Returns ``{matched: [key], primary_key, sample_size, hit_rate, roi,
    best_market, reason_codes, confidence_adjustment, warning}``.
    """
    out = {
        "matched":               [],
        "primary_key":           None,
        "sample_size":           0,
        "hit_rate":              None,
        "roi":                   None,
        "best_market":           None,
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
        docs = []
        async for d in cursor:
            docs.append(d)
        if not docs:
            out["reason_codes"].append(RC_PATTERN_NO_MATCH)
            return out

        # Pick the most-evidenced pattern as primary.
        docs.sort(key=lambda d: int(d.get("sample_size") or 0), reverse=True)
        primary = docs[0]
        sample_size = int(primary.get("sample_size") or 0)
        hit_rate = float(primary.get("hit_rate") or 0.0)
        roi = float(primary.get("roi") or 0.0)
        best_market = primary.get("best_market")

        out.update({
            "matched":      [d.get("pattern_key") for d in docs],
            "primary_key":  primary.get("pattern_key"),
            "sample_size":  sample_size,
            "hit_rate":     hit_rate,
            "roi":          roi,
            "best_market":  best_market,
        })

        # Apply sample-size gates
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


def _compute_pattern_adjustment(*, sample_size: int, hit_rate: float,
                                   roi: float) -> tuple[float, list[str], str | None]:
    """Pure helper — translate pattern stats into a capped adjustment.

    Returns ``(adjustment, reason_codes, warning_message)``.
    """
    codes: list[str] = []
    warning: str | None = None
    adjustment = 0.0

    if sample_size < PATTERN_SAMPLE_NO_ADJUST:
        codes.append(RC_PATTERN_LOW_SAMPLE)
        warning = (
            f"Patrón histórico con muestra baja (n={sample_size}); "
            "se omite ajuste por confianza."
        )
        return 0.0, codes, warning

    if sample_size < PATTERN_SAMPLE_MODERATE:
        # Moderate: roi sign determines direction, capped at ±5
        sign = 1.0 if roi >= 0 else -1.0
        # Use hit_rate deviation from 0.50 as magnitude proxy
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
        # ROI negative — pattern actively LOSES — small negative adjustment
        adjustment = round(-PATTERN_MAX_ADJUSTMENT_MODERATE * 0.6, 2)
        codes.append(RC_PATTERN_NEGATIVE_ROI)
    return adjustment, codes, warning


async def update_pattern_memory_from_result(
    db,
    *,
    pattern_keys: list[str],
    market: str | None,
    stake: float,
    won: bool,
    payout: float = 0.0,
) -> bool:
    """Increment the pattern memory using a settled bet result.

    For each pattern key:
      * sample_size += 1
      * wins += 1 if won
      * hit_rate = wins / sample_size
      * roi = (total_payout - total_stake) / total_stake
      * best_market tracks the market with the highest hit_rate * roi.
    """
    if db is None or not pattern_keys:
        return False
    try:
        ts = _now_iso()
        for pk in pattern_keys:
            existing = await db[COLL_PATTERN_MEMORY].find_one({"pattern_key": pk}) or {}
            sample_size = int(existing.get("sample_size") or 0) + 1
            wins = int(existing.get("wins") or 0) + (1 if won else 0)
            total_stake = float(existing.get("total_stake") or 0.0) + float(stake)
            total_payout = float(existing.get("total_payout") or 0.0) + float(payout)
            hit_rate = round(wins / sample_size, 4) if sample_size else 0.0
            roi = round((total_payout - total_stake) / total_stake, 4) \
                if total_stake > 0 else 0.0

            # Track best_market with simple per-market ledger.
            market_ledger = existing.get("market_ledger") or {}
            if market:
                ml = market_ledger.get(market) or {"samples": 0, "wins": 0,
                                                     "stake": 0.0, "payout": 0.0}
                ml["samples"] += 1
                if won:
                    ml["wins"] += 1
                ml["stake"]  += float(stake)
                ml["payout"] += float(payout)
                market_ledger[market] = ml

            # Decide best_market: highest hit_rate*roi with at least 5 samples
            best_market = None
            best_score = -1e9
            for mname, m in (market_ledger or {}).items():
                if m.get("samples", 0) < 5:
                    continue
                m_hr = (m["wins"] / m["samples"]) if m["samples"] else 0
                m_roi = (m["payout"] - m["stake"]) / m["stake"] if m["stake"] > 0 else 0
                score = m_hr * (1 + max(0.0, m_roi))
                if score > best_score:
                    best_score = score
                    best_market = mname

            await db[COLL_PATTERN_MEMORY].replace_one(
                {"pattern_key": pk},
                {
                    "pattern_key":  pk,
                    "sample_size":  sample_size,
                    "wins":         wins,
                    "hit_rate":     hit_rate,
                    "total_stake":  total_stake,
                    "total_payout": total_payout,
                    "roi":          roi,
                    "market_ledger": market_ledger,
                    "best_market":  best_market,
                    "updated_at":   ts,
                },
                upsert=True,
            )
        return True
    except Exception as exc:
        log.debug("update_pattern_memory_from_result failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Market result persistence (for feedback loop)
# ─────────────────────────────────────────────────────────────────────
async def persist_market_result(
    db,
    *,
    game_pk: str | int,
    pattern_keys: list[str],
    market: str | None,
    stake: float,
    won: bool,
    payout: float = 0.0,
) -> bool:
    if db is None or game_pk is None:
        return False
    try:
        payload = {
            "game_pk":      str(game_pk),
            "pattern_keys": list(pattern_keys or []),
            "market":       market,
            "stake":        float(stake),
            "won":          bool(won),
            "payout":       float(payout),
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
        )
        return True
    except Exception as exc:
        log.debug("persist_market_result failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Orchestrator-facing helper
# ─────────────────────────────────────────────────────────────────────
async def attach_pattern_match_to_payload(db, pick_payload: dict) -> dict:
    """Compute pattern keys + lookup memory + attach result to payload.

    Adds these fields to ``pick_payload`` in place AND returns the
    summary dict. Fail-soft: returns a neutral summary if db is None.
    """
    if not isinstance(pick_payload, dict):
        return {}
    keys = derive_pattern_keys(pick_payload)
    summary = await lookup_pattern_match(db, keys)

    # Translate to user-spec canonical fields on the pick_payload.
    pick_payload["historical_pattern_match"] = {
        "matched_patterns":      summary["matched"],
        "primary_pattern":       summary["primary_key"],
        "sample_size":           summary["sample_size"],
        "historical_hit_rate":   summary["hit_rate"],
        "historical_roi":        summary["roi"],
        "best_historical_market": summary["best_market"],
        "pattern_confidence_adjustment": summary["confidence_adjustment"],
        "pattern_reason_codes":  summary["reason_codes"],
        "warning":               summary["warning"],
    }
    # Also expose the same metrics at the top level as the user spec
    # explicitly requests them at the pick root.
    pick_payload.setdefault("historical_hit_rate",  summary["hit_rate"])
    pick_payload.setdefault("historical_roi",       summary["roi"])
    pick_payload.setdefault("best_historical_market", summary["best_market"])
    pick_payload.setdefault("pattern_confidence_adjustment",
                              summary["confidence_adjustment"])
    pick_payload.setdefault("pattern_reason_codes", summary["reason_codes"])
    return summary


__all__ = [
    # Collection names
    "COLL_TEAM_DAILY", "COLL_PITCHER_DAILY", "COLL_GAME_SNAPSHOTS",
    "COLL_MARKET_RESULTS", "COLL_PATTERN_MEMORY",
    # Constants
    "FRESHNESS_HOURS_DEFAULT",
    "PATTERN_SAMPLE_NO_ADJUST", "PATTERN_SAMPLE_MODERATE",
    "PATTERN_MAX_ADJUSTMENT_MODERATE", "PATTERN_MAX_ADJUSTMENT_STRONG",
    "KNOWN_PATTERNS",
    # Reason codes
    "RC_PATTERN_LOW_SAMPLE", "RC_PATTERN_MODERATE_BOOST",
    "RC_PATTERN_STRONG_BOOST", "RC_PATTERN_NEGATIVE_ROI",
    "RC_PATTERN_NO_MATCH", "RC_WAREHOUSE_DISABLED",
    # Public API
    "load_team_profile", "upsert_team_profile",
    "load_pitcher_profile", "upsert_pitcher_profile",
    "persist_game_intelligence_snapshot",
    "derive_pattern_keys", "lookup_pattern_match",
    "update_pattern_memory_from_result",
    "persist_market_result",
    "attach_pattern_match_to_payload",
]
