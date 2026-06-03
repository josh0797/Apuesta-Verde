"""Basketball Intelligence Warehouse (Fix 1 — equivalent of MLB warehouse).

Strictly SEPARATE from MLB:
  * dedicated Mongo collections (``bball_*`` prefix);
  * basketball-specific pattern keys (pace, spread, totals, ORTG/DRTG,
    live momentum);
  * fail-soft public API mirroring the MLB warehouse contract.

Five collections::

    bball_team_daily_profiles
    bball_player_daily_profiles
    bball_game_intelligence_snapshots
    bball_market_results
    bball_pattern_memory

Sample-size gates (identical to MLB — user spec):
  * ``< 20``         → warning only, **no confidence adjustment**.
  * ``20–49``        → moderate ±5 adjustment allowed.
  * ``>= 50`` + roi>0 → larger adjustment up to ±8.

Canonical pattern keys (basketball-specific):
  * HIGH_PACE_OVER_PROFILE
  * LOW_PACE_UNDER_PROFILE
  * STRONG_OFFENSIVE_RATING_EDGE
  * STRONG_DEFENSIVE_RATING_EDGE
  * SPREAD_MARGIN_SUPPORTED
  * MONEYLINE_SAFER_THAN_SPREAD
  * LIVE_MOMENTUM_FAVORITE
  * LIVE_MOMENTUM_UNDERDOG
  * THREE_POINT_VARIANCE_RISK
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# Collections
COLL_TEAM_DAILY        = "bball_team_daily_profiles"
COLL_PLAYER_DAILY      = "bball_player_daily_profiles"
COLL_GAME_SNAPSHOTS    = "bball_game_intelligence_snapshots"
COLL_MARKET_RESULTS    = "bball_market_results"
COLL_PATTERN_MEMORY    = "bball_pattern_memory"

FRESHNESS_HOURS_DEFAULT = 24

# Gates (mirror MLB spec)
PATTERN_SAMPLE_NO_ADJUST  = 20
PATTERN_SAMPLE_MODERATE   = 50
PATTERN_MAX_ADJUSTMENT_MODERATE = 5.0
PATTERN_MAX_ADJUSTMENT_STRONG   = 8.0

# Reason codes
RC_PATTERN_LOW_SAMPLE     = "BBALL_PATTERN_MEMORY_LOW_SAMPLE"
RC_PATTERN_MODERATE_BOOST = "BBALL_PATTERN_MEMORY_MODERATE_BOOST"
RC_PATTERN_STRONG_BOOST   = "BBALL_PATTERN_MEMORY_STRONG_BOOST"
RC_PATTERN_NEGATIVE_ROI   = "BBALL_PATTERN_MEMORY_NEGATIVE_ROI"
RC_PATTERN_NO_MATCH       = "BBALL_PATTERN_MEMORY_NO_MATCH"
RC_WAREHOUSE_DISABLED     = "BBALL_WAREHOUSE_DISABLED"

KNOWN_PATTERNS = (
    "HIGH_PACE_OVER_PROFILE",
    "LOW_PACE_UNDER_PROFILE",
    "STRONG_OFFENSIVE_RATING_EDGE",
    "STRONG_DEFENSIVE_RATING_EDGE",
    "SPREAD_MARGIN_SUPPORTED",
    "MONEYLINE_SAFER_THAN_SPREAD",
    "LIVE_MOMENTUM_FAVORITE",
    "LIVE_MOMENTUM_UNDERDOG",
    "THREE_POINT_VARIANCE_RISK",
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _day_key(when: Optional[datetime] = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_fresh(updated_iso: str | None, hours: float) -> bool:
    if not updated_iso:
        return False
    try:
        dt = datetime.fromisoformat(updated_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() < hours * 3600


# ─────────────────────────────────────────────────────────────────────
# Daily team / player profile read/write
# ─────────────────────────────────────────────────────────────────────
async def load_team_profile(db, team_id: str | int,
                              *, day: str | None = None,
                              max_age_hours: float = FRESHNESS_HOURS_DEFAULT,
                              ) -> dict | None:
    if db is None or team_id is None:
        return None
    try:
        day = day or _day_key()
        doc = await db[COLL_TEAM_DAILY].find_one({
            "team_id": str(team_id), "day": day,
        })
        if not doc or not _is_fresh(doc.get("updated_at"), max_age_hours):
            return None
        return doc
    except Exception as exc:
        log.debug("[bball_wh] load_team_profile failed: %s", exc)
        return None


async def upsert_team_profile(db, team_id: str | int, profile: dict,
                                *, day: str | None = None) -> bool:
    if db is None or team_id is None or not isinstance(profile, dict):
        return False
    try:
        day = day or _day_key()
        await db[COLL_TEAM_DAILY].replace_one(
            {"team_id": str(team_id), "day": day},
            {
                "team_id":   str(team_id),
                "day":       day,
                "updated_at": _now_iso(),
                "profile":   profile,
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("[bball_wh] upsert_team_profile failed: %s", exc)
        return False


async def load_player_profile(db, player_id: str | int,
                                *, day: str | None = None,
                                max_age_hours: float = FRESHNESS_HOURS_DEFAULT,
                                ) -> dict | None:
    if db is None or player_id is None:
        return None
    try:
        day = day or _day_key()
        doc = await db[COLL_PLAYER_DAILY].find_one({
            "player_id": str(player_id), "day": day,
        })
        if not doc or not _is_fresh(doc.get("updated_at"), max_age_hours):
            return None
        return doc
    except Exception as exc:
        log.debug("[bball_wh] load_player_profile failed: %s", exc)
        return None


async def upsert_player_profile(db, player_id: str | int, profile: dict,
                                  *, day: str | None = None) -> bool:
    if db is None or player_id is None or not isinstance(profile, dict):
        return False
    try:
        day = day or _day_key()
        await db[COLL_PLAYER_DAILY].replace_one(
            {"player_id": str(player_id), "day": day},
            {
                "player_id":  str(player_id),
                "day":        day,
                "updated_at": _now_iso(),
                "profile":    profile,
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("[bball_wh] upsert_player_profile failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Pattern keys (basketball-specific)
# ─────────────────────────────────────────────────────────────────────
def derive_pattern_keys(pick_payload: dict) -> list[str]:
    """Translate a basketball pick payload into canonical pattern keys.

    Reads only well-known basketball signals. Pure / no IO.
    """
    if not isinstance(pick_payload, dict):
        return []
    keys: list[str] = []

    home = pick_payload.get("home_team_profile") or {}
    away = pick_payload.get("away_team_profile") or {}
    live = pick_payload.get("live_state") or pick_payload.get("live_stats") or {}
    rec  = pick_payload.get("recommendation") or {}
    market = (rec.get("market") or "").lower()

    # Pace average between teams
    pace_h = _f(home.get("pace"))
    pace_a = _f(away.get("pace"))
    if pace_h is not None and pace_a is not None:
        avg_pace = (pace_h + pace_a) / 2.0
        if avg_pace >= 102:
            keys.append("HIGH_PACE_OVER_PROFILE")
        elif avg_pace <= 95:
            keys.append("LOW_PACE_UNDER_PROFILE")

    # Offensive / defensive rating edges
    ortg_h = _f(home.get("offensive_rating") or home.get("ortg"))
    ortg_a = _f(away.get("offensive_rating") or away.get("ortg"))
    drtg_h = _f(home.get("defensive_rating") or home.get("drtg"))
    drtg_a = _f(away.get("defensive_rating") or away.get("drtg"))
    if ortg_h is not None and ortg_a is not None and abs(ortg_h - ortg_a) >= 6:
        keys.append("STRONG_OFFENSIVE_RATING_EDGE")
    if drtg_h is not None and drtg_a is not None and abs(drtg_h - drtg_a) >= 6:
        keys.append("STRONG_DEFENSIVE_RATING_EDGE")

    # Spread / moneyline support flags from upstream basketball script
    script = pick_payload.get("_basketball_script") or {}
    margin = _f(script.get("marginProjection"))
    cover  = _f(script.get("spreadCoverProb"))
    if margin is not None and cover is not None \
            and margin >= 4.0 and cover >= 0.55:
        keys.append("SPREAD_MARGIN_SUPPORTED")
    if "spread" in market or "handicap" in market:
        if (margin is not None and margin < 3.0) \
                or (cover is not None and cover < 0.50):
            keys.append("MONEYLINE_SAFER_THAN_SPREAD")

    # Live momentum
    momentum = (live.get("momentum") or {}) if isinstance(live, dict) else {}
    mom_side = momentum.get("side") or momentum.get("favored_side")
    if mom_side == "favorite":
        keys.append("LIVE_MOMENTUM_FAVORITE")
    elif mom_side == "underdog":
        keys.append("LIVE_MOMENTUM_UNDERDOG")

    # Three-point variance
    var_h = _f(home.get("three_pt_variance") or home.get("threes_volatility"))
    var_a = _f(away.get("three_pt_variance") or away.get("threes_volatility"))
    if (var_h is not None and var_h >= 0.30) or (var_a is not None and var_a >= 0.30):
        keys.append("THREE_POINT_VARIANCE_RISK")

    return keys


# ─────────────────────────────────────────────────────────────────────
# Game snapshot
# ─────────────────────────────────────────────────────────────────────
def _digest_pick_payload(pick_payload: dict) -> dict:
    return {
        "home_team_profile":  pick_payload.get("home_team_profile"),
        "away_team_profile":  pick_payload.get("away_team_profile"),
        "_basketball_script": pick_payload.get("_basketball_script"),
        "recommendation":     pick_payload.get("recommendation"),
        "reason_codes":       pick_payload.get("reason_codes"),
        "pipeline_meta":      pick_payload.get("pipeline_meta"),
    }


async def persist_game_intelligence_snapshot(
    db, *, game_id: str | int, match_id: str | int | None,
    home_team_id: str | int | None, away_team_id: str | int | None,
    pick_payload: dict, day: str | None = None,
) -> bool:
    if db is None or game_id is None or not isinstance(pick_payload, dict):
        return False
    try:
        day = day or _day_key()
        await db[COLL_GAME_SNAPSHOTS].replace_one(
            {"game_id": str(game_id), "day": day},
            {
                "game_id":      str(game_id),
                "match_id":     str(match_id) if match_id is not None else None,
                "home_team_id": str(home_team_id) if home_team_id is not None else None,
                "away_team_id": str(away_team_id) if away_team_id is not None else None,
                "day":          day,
                "updated_at":   _now_iso(),
                "pattern_keys": derive_pattern_keys(pick_payload),
                "digest":       _digest_pick_payload(pick_payload),
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        log.debug("[bball_wh] persist_game_intelligence_snapshot failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Pattern memory — lookup + update + market_results persistence
# ─────────────────────────────────────────────────────────────────────
def _compute_pattern_adjustment(*, sample_size: int, hit_rate: float,
                                   roi: float) -> tuple[float, list[str], str | None]:
    """Mirror of the MLB gate logic — kept INDEPENDENT to keep sports
    isolated (no cross-import). Returns ``(adj, codes, warning)``.
    """
    codes: list[str] = []
    warning: str | None = None
    if sample_size < PATTERN_SAMPLE_NO_ADJUST:
        codes.append(RC_PATTERN_LOW_SAMPLE)
        warning = (
            f"Patrón basketball con muestra baja (n={sample_size}); "
            "se omite ajuste por confianza."
        )
        return 0.0, codes, warning

    if sample_size < PATTERN_SAMPLE_MODERATE:
        sign = 1.0 if roi >= 0 else -1.0
        magnitude = min(1.0, abs(hit_rate - 0.50) * 5.0)
        adj = round(sign * magnitude * PATTERN_MAX_ADJUSTMENT_MODERATE, 2)
        codes.append(RC_PATTERN_MODERATE_BOOST)
        if roi < 0:
            codes.append(RC_PATTERN_NEGATIVE_ROI)
        return adj, codes, warning

    if roi > 0:
        magnitude = min(1.0, abs(hit_rate - 0.50) * 5.0 + min(0.4, roi))
        adj = round(magnitude * PATTERN_MAX_ADJUSTMENT_STRONG, 2)
        codes.append(RC_PATTERN_STRONG_BOOST)
        return adj, codes, warning

    adj = round(-PATTERN_MAX_ADJUSTMENT_MODERATE * 0.6, 2)
    codes.append(RC_PATTERN_NEGATIVE_ROI)
    return adj, codes, warning


async def lookup_pattern_match(db, pattern_keys: list[str]) -> dict:
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
        cursor = db[COLL_PATTERN_MEMORY].find({"pattern_key": {"$in": list(pattern_keys)}})
        docs: list[dict] = []
        async for d in cursor:
            docs.append(d)
        if not docs:
            out["reason_codes"].append(RC_PATTERN_NO_MATCH)
            return out

        docs.sort(key=lambda d: int(d.get("sample_size") or 0), reverse=True)
        primary = docs[0]
        sample_size = int(primary.get("sample_size") or 0)
        hit_rate = float(primary.get("hit_rate") or 0.0)
        roi = float(primary.get("roi") or 0.0)
        out.update({
            "matched":      [d.get("pattern_key") for d in docs],
            "primary_key":  primary.get("pattern_key"),
            "sample_size":  sample_size,
            "hit_rate":     hit_rate,
            "roi":          roi,
            "best_market":  primary.get("best_market"),
        })
        adj, codes, warning = _compute_pattern_adjustment(
            sample_size=sample_size, hit_rate=hit_rate, roi=roi,
        )
        out["confidence_adjustment"] = adj
        out["reason_codes"].extend(codes)
        out["warning"] = warning
        return out
    except Exception as exc:
        log.debug("[bball_wh] lookup_pattern_match failed: %s", exc)
        out["reason_codes"].append(RC_WAREHOUSE_DISABLED)
        return out


async def update_pattern_memory_from_result(
    db, *,
    pattern_keys: list[str], market: str | None,
    stake: float, won: bool, payout: float = 0.0,
) -> bool:
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

            market_ledger = existing.get("market_ledger") or {}
            if market:
                ml = market_ledger.get(market) or {
                    "samples": 0, "wins": 0, "stake": 0.0, "payout": 0.0,
                }
                ml["samples"] += 1
                if won:
                    ml["wins"] += 1
                ml["stake"]  += float(stake)
                ml["payout"] += float(payout)
                market_ledger[market] = ml

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
        log.debug("[bball_wh] update_pattern_memory_from_result failed: %s", exc)
        return False


async def persist_market_result(
    db, *, game_id: str | int, pattern_keys: list[str],
    market: str | None, stake: float, won: bool, payout: float = 0.0,
) -> bool:
    if db is None or game_id is None:
        return False
    try:
        await db[COLL_MARKET_RESULTS].insert_one({
            "game_id":      str(game_id),
            "pattern_keys": list(pattern_keys or []),
            "market":       market,
            "stake":        float(stake),
            "won":          bool(won),
            "payout":       float(payout),
            "settled_at":   _now_iso(),
        })
        await update_pattern_memory_from_result(
            db,
            pattern_keys=pattern_keys or [],
            market=market, stake=stake, won=won, payout=payout,
        )
        return True
    except Exception as exc:
        log.debug("[bball_wh] persist_market_result failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Orchestrator helper
# ─────────────────────────────────────────────────────────────────────
async def attach_pattern_match_to_payload(db, pick_payload: dict) -> dict:
    if not isinstance(pick_payload, dict):
        return {}
    keys = derive_pattern_keys(pick_payload)
    summary = await lookup_pattern_match(db, keys)

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
    pick_payload.setdefault("historical_hit_rate",  summary["hit_rate"])
    pick_payload.setdefault("historical_roi",       summary["roi"])
    pick_payload.setdefault("best_historical_market", summary["best_market"])
    pick_payload.setdefault("pattern_confidence_adjustment",
                              summary["confidence_adjustment"])
    pick_payload.setdefault("pattern_reason_codes", summary["reason_codes"])
    return summary


__all__ = [
    "COLL_TEAM_DAILY", "COLL_PLAYER_DAILY", "COLL_GAME_SNAPSHOTS",
    "COLL_MARKET_RESULTS", "COLL_PATTERN_MEMORY",
    "FRESHNESS_HOURS_DEFAULT",
    "PATTERN_SAMPLE_NO_ADJUST", "PATTERN_SAMPLE_MODERATE",
    "PATTERN_MAX_ADJUSTMENT_MODERATE", "PATTERN_MAX_ADJUSTMENT_STRONG",
    "KNOWN_PATTERNS",
    "RC_PATTERN_LOW_SAMPLE", "RC_PATTERN_MODERATE_BOOST",
    "RC_PATTERN_STRONG_BOOST", "RC_PATTERN_NEGATIVE_ROI",
    "RC_PATTERN_NO_MATCH", "RC_WAREHOUSE_DISABLED",
    "load_team_profile", "upsert_team_profile",
    "load_player_profile", "upsert_player_profile",
    "persist_game_intelligence_snapshot",
    "derive_pattern_keys", "lookup_pattern_match",
    "update_pattern_memory_from_result",
    "persist_market_result",
    "attach_pattern_match_to_payload",
]
