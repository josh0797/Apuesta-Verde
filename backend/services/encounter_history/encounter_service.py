"""Encounter History Service — Mongo persistence + memory hydration.

Collection: `encounter_history`
Indexes:
    canonical_match_key  (locality-agnostic — PRIMARY lookup key)
    encounter_key        (alias of canonical_match_key)
    sport
    team_a_norm + team_b_norm   (compound, for team-pair lookups)
    pick_uid                    (unique, for idempotent upserts)
    match_date                  (sort key)

The document shape MIRRORS the spec the user provided:
    {
      id, sport, canonical_match_key,
      home_team, away_team, team_a, team_b,  (team_a/b sorted alphabetically)
      league, match_date, kickoff_iso,
      final_score,
      recommended_market, recommended_selection, odds, stake,
      result: WON|LOST|VOID|CASHED_OUT,
      profit_loss,
      confidence, edge, fragility_score,
      trap_signals, reasoning, risks,
      post_match_learning,
      created_at, updated_at,
      pick_uid                 # idempotency anchor (same uid → upsert)
    }
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.editorial_context.match_key import (
    canonical_match_key as _cmk,
    encounter_key as _ek,
    normalize_team_name,
)
from .pattern_detector import detect_patterns

log = logging.getLogger("encounter_history")

COLLECTION_NAME    = "encounter_history"
ENCOUNTER_VERSION  = "encounter-mvp.1"
# How many past encounters we hand back per memory call. Older items still
# live in mongo and are used for win-rate computation, just not returned.
MAX_RETURNED       = 10
# Hard cap on documents we scan for stats. >50 is overkill for two teams.
MAX_SCAN           = 50

# Result code normalisation: track_pick uses 'won'/'lost'/'push'/'pending';
# the spec asks WON/LOST/VOID/CASHED_OUT. Map both into a single namespace.
_RESULT_MAP = {
    "won":         "WON",
    "lost":        "LOST",
    "push":        "VOID",
    "void":        "VOID",
    "cashed_out":  "CASHED_OUT",
    "cashedout":   "CASHED_OUT",
    "cash_out":    "CASHED_OUT",
}


def _normalise_result(outcome: Optional[str]) -> Optional[str]:
    if not outcome:
        return None
    return _RESULT_MAP.get(outcome.lower().strip())


def _compute_profit_loss(
    *, result: Optional[str], stake: float, odds: Optional[float],
) -> Optional[float]:
    """Match the unit-economics already used by /api/picks/track:
        WON  → stake * (odds - 1)
        LOST → -stake
        VOID / CASHED_OUT → 0  (cash-out value would need to be passed explicitly)
    """
    if result is None or stake is None:
        return None
    if result == "WON":
        if odds and odds > 1:
            return round(stake * (float(odds) - 1.0), 2)
        return round(stake, 2)
    if result == "LOST":
        return round(-abs(stake), 2)
    return 0.0   # VOID / CASHED_OUT


async def ensure_indexes(db) -> None:
    """Idempotent index creation. Safe to call at startup AND from services."""
    if db is None:
        return
    try:
        await db[COLLECTION_NAME].create_index("canonical_match_key")
        await db[COLLECTION_NAME].create_index("encounter_key")
        await db[COLLECTION_NAME].create_index("sport")
        await db[COLLECTION_NAME].create_index(
            [("team_a_norm", 1), ("team_b_norm", 1)],
            name="team_pair_norm_idx",
        )
        await db[COLLECTION_NAME].create_index("pick_uid", unique=True, sparse=True)
        await db[COLLECTION_NAME].create_index("match_date")
        await db[COLLECTION_NAME].create_index("result")
    except Exception as exc:
        log.debug("ensure_indexes (encounter_history) skipped: %s", exc)


async def record_encounter(
    db,
    *,
    pick: dict,
    sport: str = "football",
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    league: Optional[str] = None,
    kickoff_iso: Optional[str] = None,
    final_score: Optional[str] = None,
    result_override: Optional[str] = None,
    stake: Optional[float] = None,
    post_match_learning: Optional[str] = None,
) -> Optional[dict]:
    """Upsert one encounter document.

    The caller passes a `pick` dict that loosely follows the analyst-engine
    shape (recommendation + market + selection + odds + confidence + edge +
    moneyball metadata). We extract what we need and store everything else
    under `_pick_snapshot` so future debugging is straightforward.
    """
    if db is None or not pick:
        return None
    pick = pick or {}
    rec = pick.get("recommendation") or {}
    mb  = pick.get("_moneyball") or {}
    market    = pick.get("market")    or rec.get("market")    or pick.get("recommended_market")
    selection = pick.get("selection") or rec.get("selection") or pick.get("recommended_selection")
    odds_val  = pick.get("odds")      or rec.get("odds")      or mb.get("decimal_odds")
    try:
        odds = float(odds_val) if odds_val is not None else None
    except (ValueError, TypeError):
        odds = None

    result_norm = _normalise_result(result_override or pick.get("outcome") or pick.get("result"))
    stake_val   = float(stake) if stake is not None else float(mb.get("stake_used") or pick.get("stake") or 10.0)
    profit_loss = _compute_profit_loss(result=result_norm, stake=stake_val, odds=odds)

    h_norm = normalize_team_name(home_team or pick.get("home_team"))
    a_norm = normalize_team_name(away_team or pick.get("away_team"))
    # Locality-agnostic pair: alphabetical order
    team_pair = sorted([h_norm, a_norm])
    cmk = _cmk(sport, home_team, away_team, kickoff_iso)
    enc_key = _ek(sport, home_team, away_team, kickoff_iso)

    pick_uid = pick.get("pick_id") or pick.get("pick_uid") or f"auto-{uuid.uuid4().hex[:12]}"

    doc = {
        "id":                     str(uuid.uuid4()),
        "sport":                  (sport or "football").lower(),
        "canonical_match_key":    cmk,
        "encounter_key":          enc_key,
        "home_team":              home_team,
        "away_team":              away_team,
        "team_a":                 team_pair[0],
        "team_b":                 team_pair[1],
        "team_a_norm":            team_pair[0],
        "team_b_norm":            team_pair[1],
        "league":                 league,
        "match_date":             (kickoff_iso or "")[:10] or None,
        "kickoff_iso":            kickoff_iso,
        "final_score":            final_score,
        "recommended_market":     market,
        "recommended_selection":  selection,
        "odds":                   odds,
        "stake":                  stake_val,
        "result":                 result_norm,
        "profit_loss":            profit_loss,
        "confidence":             pick.get("confidence_score") or rec.get("confidence") or mb.get("confidence"),
        "edge":                   mb.get("edge") or pick.get("edge"),
        "fragility_score":        mb.get("fragility_score") or (pick.get("_moneyball") or {}).get("fragility_score"),
        "trap_signals":           mb.get("trap_signals_structured") or pick.get("trap_signals") or [],
        "reasoning":              rec.get("reasoning") or pick.get("reasoning"),
        "risks":                  pick.get("risks") or [],
        "post_match_learning":    post_match_learning,
        "pick_uid":               pick_uid,
        "created_at":             datetime.now(timezone.utc).isoformat(),
        "updated_at":             datetime.now(timezone.utc).isoformat(),
        "_engine_version":        ENCOUNTER_VERSION,
        "_pick_snapshot":         {k: v for k, v in pick.items() if not k.startswith("_")},
    }
    try:
        await db[COLLECTION_NAME].update_one(
            {"pick_uid": pick_uid},
            {"$set": doc, "$setOnInsert": {"first_seen_at": doc["created_at"]}},
            upsert=True,
        )
        log.info(
            "[ENCOUNTER_RECORDED] sport=%s pair=%s|%s result=%s market=%s",
            sport, team_pair[0], team_pair[1], result_norm, market,
        )
        return doc
    except Exception as exc:
        log.warning("[ENCOUNTER_RECORD_FAILED] %s", exc)
        return None


async def get_encounter_memory(
    db,
    *,
    sport: str,
    home_team: Optional[str],
    away_team: Optional[str],
    kickoff_iso: Optional[str] = None,
    max_returned: int = MAX_RETURNED,
) -> dict:
    """Fetch encounter memory for a future/current match.

    Returns:
        {
            "available":             bool,
            "sport":                 str,
            "team_a":                str,
            "team_b":                str,
            "encounter_count":       int,
            "history":               list[dict]   # most recent first
            "last_recommendation":   dict | None  # most recent pick details
            "last_result":           str | None
            "market_performance":    dict          # by market: {wins, losses, total, win_rate}
            "win_rate":              float | None # overall win rate over settled picks
            "repeated_patterns":     list[str]    # human-readable observations
            "warnings":              list[str]    # cautionary notes
            "suggested_market_bias": str | None   # best-performing market (if confident enough)
            "narrative":             str
            "_engine_version":       str
        }
    """
    out_empty = {
        "available":              False,
        "sport":                  sport,
        "team_a":                 None,
        "team_b":                 None,
        "encounter_count":        0,
        "history":                [],
        "last_recommendation":    None,
        "last_result":            None,
        "market_performance":     {},
        "win_rate":               None,
        "repeated_patterns":      [],
        "warnings":                [],
        "suggested_market_bias":  None,
        "narrative":              "Sin enfrentamientos previos registrados entre estos equipos.",
        "_engine_version":        ENCOUNTER_VERSION,
    }
    if db is None or not home_team or not away_team:
        return out_empty
    h = normalize_team_name(home_team)
    a = normalize_team_name(away_team)
    pair = sorted([h, a])
    try:
        cursor = db[COLLECTION_NAME].find(
            {
                "sport":       (sport or "football").lower(),
                "team_a_norm": pair[0],
                "team_b_norm": pair[1],
            },
            {"_id": 0, "_pick_snapshot": 0},
        ).sort("match_date", -1).limit(MAX_SCAN)
        history = [doc async for doc in cursor]
    except Exception as exc:
        log.debug("get_encounter_memory query failed: %s", exc)
        return out_empty
    if not history:
        return {**out_empty, "team_a": pair[0], "team_b": pair[1]}

    settled = [h_ for h_ in history if h_.get("result") in ("WON", "LOST", "VOID", "CASHED_OUT")]
    win_lost = [h_ for h_ in settled if h_.get("result") in ("WON", "LOST")]
    wins = sum(1 for h_ in win_lost if h_.get("result") == "WON")
    win_rate = round(wins / len(win_lost), 3) if win_lost else None

    # Per-market performance
    market_perf: dict[str, dict] = {}
    for h_ in settled:
        m = (h_.get("recommended_market") or "").strip()
        if not m:
            continue
        bucket = market_perf.setdefault(m, {"wins": 0, "losses": 0, "void": 0, "total": 0})
        bucket["total"] += 1
        if h_.get("result") == "WON":
            bucket["wins"] += 1
        elif h_.get("result") == "LOST":
            bucket["losses"] += 1
        else:
            bucket["void"] += 1
    for m, b in market_perf.items():
        decided = b["wins"] + b["losses"]
        b["win_rate"] = round(b["wins"] / decided, 3) if decided else None

    patterns = detect_patterns(history, sport=sport)

    last = history[0]
    last_recommendation = {
        "market":     last.get("recommended_market"),
        "selection":  last.get("recommended_selection"),
        "odds":       last.get("odds"),
        "reasoning":  last.get("reasoning"),
        "final_score":last.get("final_score"),
        "match_date": last.get("match_date"),
        "league":     last.get("league"),
        "learning":   last.get("post_match_learning"),
    }

    # Best market = highest win-rate among those with >= 2 settled picks AND
    # win-rate strictly above 50%.
    suggested_bias: Optional[str] = None
    eligible = [
        (m, b) for m, b in market_perf.items()
        if (b["wins"] + b["losses"]) >= 2 and (b["win_rate"] or 0) > 0.5
    ]
    if eligible:
        eligible.sort(key=lambda mb_: (-(mb_[1]["win_rate"] or 0), -(mb_[1]["wins"] + mb_[1]["losses"])))
        suggested_bias = eligible[0][0]

    narrative = _build_narrative(
        pair, len(history), win_rate, suggested_bias, last_recommendation, patterns,
    )

    # Bound payload size: history truncated to max_returned
    return {
        "available":              True,
        "sport":                  sport,
        "team_a":                 pair[0],
        "team_b":                 pair[1],
        "encounter_count":        len(history),
        "history":                history[:max_returned],
        "last_recommendation":    last_recommendation,
        "last_result":            last.get("result"),
        "market_performance":     market_perf,
        "win_rate":               win_rate,
        "repeated_patterns":      patterns.get("repeated_patterns", []),
        "warnings":               patterns.get("warnings", []),
        "suggested_market_bias":  suggested_bias,
        "narrative":              narrative,
        "_engine_version":        ENCOUNTER_VERSION,
    }


def _build_narrative(
    pair: list[str],
    encounter_count: int,
    win_rate: Optional[float],
    suggested_bias: Optional[str],
    last_recommendation: dict,
    patterns: dict,
) -> str:
    pieces: list[str] = []
    pieces.append(
        f"Memoria de enfrentamientos {pair[0]} vs {pair[1]}: {encounter_count} pick(s) histórico(s)."
    )
    if last_recommendation and last_recommendation.get("market"):
        sel = last_recommendation.get("selection") or ""
        market = last_recommendation.get("market")
        final = last_recommendation.get("final_score")
        ldate = last_recommendation.get("match_date")
        pieces.append(
            f"Último pick ({ldate}): {market} {sel}. Resultado final {final or 's/d'}."
        )
    if win_rate is not None:
        pieces.append(f"Win-rate global del motor en este matchup: {win_rate*100:.0f}%.")
    if suggested_bias:
        pieces.append(f"Mercado con mejor histórico entre estos equipos: {suggested_bias}.")
    rp = patterns.get("repeated_patterns") or []
    if rp:
        pieces.append("Patrones repetidos detectados: " + "; ".join(rp[:3]))
    return " ".join(pieces)


__all__ = [
    "COLLECTION_NAME",
    "ENCOUNTER_VERSION",
    "ensure_indexes",
    "record_encounter",
    "get_encounter_memory",
]
