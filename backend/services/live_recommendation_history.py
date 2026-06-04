"""Live Recommendation History — snapshot every live recommendation
produced by the engine + allow manual backfill.

Why this exists
---------------
Live football reevaluation can change the recommendation as the match
state evolves (e.g. BTTS YES @ 1-0 → Over 3.5 @ 1-1). Without
persistence, prior recommendations and their outcomes are lost. This
module owns the ``live_recommendation_events`` collection: one
canonical snapshot per recommendation, with supersedes / outcome /
manual backfill semantics.

Design principles (NON-NEGOTIABLE):
  * Fail-soft. DB errors must not break live reevaluation: every
    coroutine returns ``None`` / ``False`` rather than raising.
  * Idempotent within a 1-minute dedup window
    (``user_id + sport + match_id + minute + score.label + market + selection``).
  * Engine + manual + settlement sources all share the same schema so a
    single endpoint can render them.
  * Auto-settlement is bounded to mercados simples (BTTS / Over / Under)
    so we never claim a win we can't verify deterministically.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("live_recommendation_history")

COLLECTION = "live_recommendation_events"

_NOW = lambda: datetime.now(timezone.utc).isoformat()

# Action / state gates for engine persistence.
_PERSIST_ACTIONS = {"LIVE_ENTRY", "WATCHLIST", "HOLD", "CASHOUT"}
_PERSIST_STATES = {
    "LIVE_VALUE_WINDOW", "MOMENTUM_SHIFT", "MARKET_OVERREACTION",
    "CASH_OUT_RECOMMENDED", "HOLD_RECOMMENDED",
}


# ─────────────────────────────────────────────────────────────────────
# Indexes
# ─────────────────────────────────────────────────────────────────────
async def ensure_live_recommendation_indexes(db) -> dict:
    if db is None:
        return {"available": False, "reason": "db_is_none"}
    result = {"available": True, "created": [], "errors": []}

    async def _safe(keys, **kw):
        try:
            await db[COLLECTION].create_index(keys, **kw)
            result["created"].append(str(keys))
        except Exception as exc:
            result["errors"].append({"keys": str(keys), "error": str(exc)[:200]})

    await _safe([("event_id", 1)], unique=True)
    await _safe([("user_id", 1), ("sport", 1), ("match_id", 1), ("created_at", -1)])
    await _safe([("sport", 1), ("match_id", 1), ("minute", 1)])
    await _safe([("source", 1)])
    await _safe([("status", 1)])
    return result


# ─────────────────────────────────────────────────────────────────────
# Helpers (pure)
# ─────────────────────────────────────────────────────────────────────
def _score_label(score: Any) -> str | None:
    if not isinstance(score, dict):
        return None
    h, a = score.get("home"), score.get("away")
    if h is None or a is None:
        return score.get("label")
    return f"{int(h)}-{int(a)}"


def _normalize_score(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"home": None, "away": None, "label": None}
    h, a = raw.get("home"), raw.get("away")
    try:
        h = int(h) if h is not None else None
    except (TypeError, ValueError):
        h = None
    try:
        a = int(a) if a is not None else None
    except (TypeError, ValueError):
        a = None
    label = raw.get("label") or (f"{h}-{a}" if (h is not None and a is not None) else None)
    return {"home": h, "away": a, "label": label}


def _build_dedup_key(*, user_id, sport, match_id, minute, score_label, market, selection) -> str:
    parts = [str(user_id or "_"), str(sport or "_"), str(match_id or "_"),
              str(minute if minute is not None else "_"),
              str(score_label or "_"), str(market or "_"), str(selection or "_")]
    return "|".join(parts)


def _market_lower(s: Any) -> str:
    return str(s or "").strip().lower()


# ─────────────────────────────────────────────────────────────────────
# Auto-settlement from current score (pure)
# ─────────────────────────────────────────────────────────────────────
def settle_live_event_from_score(
    event: dict | None,
    current_score: dict | None,
    *,
    minute: int | None = None,
    match_ended: bool = False,
) -> dict:
    """Deterministic settlement for BTTS / Over / Under markets.

    Returns ``{result: 'hit'|'miss'|'pending'|'void', minute, score, reason}``.
    Pure: never touches the DB.
    """
    out: dict[str, Any] = {
        "result":            "pending",
        "settled_minute":    minute,
        "settled_score":     _score_label(current_score),
        "settlement_reason": "Pendiente: el mercado todavía no resuelve.",
    }
    if not isinstance(event, dict) or not isinstance(current_score, dict):
        return out

    h = current_score.get("home")
    a = current_score.get("away")
    if h is None or a is None:
        return out
    try:
        h, a = int(h), int(a)
    except (TypeError, ValueError):
        return out
    total = h + a

    rec = event.get("recommendation") or {}
    market = _market_lower(rec.get("market") or rec.get("suggested_market"))

    # ── BTTS YES ────────────────────────────────────────────────────
    if "btts" in market and ("yes" in market or "sí" in market or "si" in market):
        if h > 0 and a > 0:
            out.update({
                "result":            "hit",
                "settlement_reason": "BTTS YES cumplido: ambos equipos marcaron.",
            })
        elif match_ended:
            out.update({
                "result":            "miss",
                "settlement_reason": "BTTS YES no cumplido al cierre del partido.",
            })
        return out

    # ── BTTS NO ─────────────────────────────────────────────────────
    if "btts" in market and ("no" in market):
        if h > 0 and a > 0:
            out.update({
                "result":            "miss",
                "settlement_reason": "BTTS NO falló: ambos equipos marcaron.",
            })
        elif match_ended:
            out.update({
                "result":            "hit",
                "settlement_reason": "BTTS NO cumplido al cierre.",
            })
        return out

    # ── Over / Under X.5 ────────────────────────────────────────────
    line: float | None = None
    for cand in ("0.5", "1.5", "2.5", "3.5", "4.5", "5.5"):
        if cand in market:
            line = float(cand)
            break
    if line is None:
        return out

    is_over = "over" in market or "más de" in market or "mas de" in market
    is_under = "under" in market or "menos de" in market

    if is_over:
        if total > line:
            out.update({
                "result":            "hit",
                "settlement_reason": f"Over {line:.1f} cumplido al marcador {h}-{a}.",
            })
        elif match_ended:
            out.update({
                "result":            "miss",
                "settlement_reason": f"Over {line:.1f} no cumplido al cierre ({h}-{a}).",
            })
        return out

    if is_under:
        if total > line:
            out.update({
                "result":            "miss",
                "settlement_reason": f"Under {line:.1f} falló: marcador {h}-{a} superó la línea.",
            })
        elif match_ended:
            out.update({
                "result":            "hit",
                "settlement_reason": f"Under {line:.1f} cumplido al cierre ({h}-{a}).",
            })
        return out

    return out


# ─────────────────────────────────────────────────────────────────────
# Persist (engine + manual)
# ─────────────────────────────────────────────────────────────────────
def _should_persist_engine_event(
    reeval_result: dict | None, interpreter: dict | None,
) -> bool:
    if not isinstance(reeval_result, dict):
        return False
    action = (reeval_result.get("recommended_action") or "").upper()
    state = (reeval_result.get("live_state") or "").upper()
    has_interp_market = bool(
        isinstance(interpreter, dict)
        and (interpreter.get("suggested_market") or interpreter.get("market"))
    )
    if action and action != "PASS":
        return action in _PERSIST_ACTIONS or has_interp_market
    if state and state in _PERSIST_STATES:
        return True
    return has_interp_market


async def _find_open_event_for_match(
    db, *, user_id: str | None, sport: str, match_id: str,
) -> dict | None:
    try:
        return await db[COLLECTION].find_one({
            "user_id":  user_id,
            "sport":    sport,
            "match_id": str(match_id),
            "status":   {"$in": ["open", "manual_recorded"]},
        }, sort=[("created_at", -1)])
    except Exception:
        return None


async def _exists_dedup(db, dedup_key: str) -> bool:
    try:
        existing = await db[COLLECTION].find_one({"dedup_key": dedup_key})
        return existing is not None
    except Exception:
        return False


async def persist_live_recommendation_event(
    db,
    *,
    user_id: str | None,
    match: dict | None,
    reeval_result: dict | None = None,
    interpreter: dict | None = None,
    source: str = "engine",
) -> Optional[dict]:
    """Persist a new live recommendation snapshot. Fail-soft."""
    if db is None or not isinstance(match, dict):
        return None
    try:
        if source == "engine" and not _should_persist_engine_event(reeval_result, interpreter):
            return None

        sport = match.get("sport") or "football"
        match_id = match.get("match_id") or (match.get("fixture") or {}).get("id")
        if not match_id:
            return None

        home = (match.get("home_team") or {}).get("name")
        away = (match.get("away_team") or {}).get("name")
        match_label = (
            match.get("match_label")
            or (f"{home} vs {away}" if home and away else None)
        )
        league = (
            (match.get("league") or {}).get("name")
            if isinstance(match.get("league"), dict)
            else match.get("league")
        )

        live = reeval_result or {}
        snapshot = live.get("live_snapshot") or {}
        minute = snapshot.get("minute") or live.get("minute") or (match.get("live_stats") or {}).get("minute")
        try:
            minute = int(minute) if minute is not None else None
        except (TypeError, ValueError):
            minute = None
        score_raw = snapshot.get("score") or (match.get("live_stats") or {}).get("score") or {}
        score = _normalize_score(score_raw if isinstance(score_raw, dict) else {})

        rec_market = (
            (live.get("market") if isinstance(live.get("market"), str) else None)
            or (interpreter or {}).get("suggested_market")
            or (interpreter or {}).get("market")
        )
        rec_selection = (
            live.get("selection")
            or (interpreter or {}).get("selection")
            or rec_market
        )
        rec_confidence = live.get("confidence") or (interpreter or {}).get("confidence")
        rec_risk = live.get("risk_level") or (interpreter or {}).get("risk_level")
        rec_action = (live.get("recommended_action") or (interpreter or {}).get("recommended_action") or "").upper() or None
        rec_title = (interpreter or {}).get("title") or rec_market

        recommendation = {
            "title":               rec_title,
            "market":              rec_market,
            "selection":           rec_selection,
            "suggested_market":    (interpreter or {}).get("suggested_market"),
            "confidence":          rec_confidence,
            "risk_level":          rec_risk,
            "recommended_action":  rec_action,
        }

        dedup_key = _build_dedup_key(
            user_id=user_id, sport=sport, match_id=match_id,
            minute=minute, score_label=score.get("label"),
            market=rec_market, selection=rec_selection,
        )

        if await _exists_dedup(db, dedup_key):
            return None

        live_ctx = match.get("live_stats") if isinstance(match.get("live_stats"), dict) else {}
        h_stats = live_ctx.get("home_stats") if isinstance(live_ctx.get("home_stats"), dict) else {}
        a_stats = live_ctx.get("away_stats") if isinstance(live_ctx.get("away_stats"), dict) else {}

        # If there's an existing open event for this match, mark it as
        # superseded — only when the NEW market/selection differs.
        previous = await _find_open_event_for_match(
            db, user_id=user_id, sport=sport, match_id=str(match_id),
        )

        event_id = str(uuid.uuid4())
        now = _NOW()
        doc = {
            "event_id":    event_id,
            "user_id":     user_id,
            "sport":       sport,
            "match_id":    str(match_id),
            "match_label": match_label,
            "league":      league,
            "source":      source,
            "event_type":  "live_recommendation",
            "minute":      minute,
            "score":       score,
            "recommendation": recommendation,
            "live_context": {
                "shots_home":             h_stats.get("shots_total"),
                "shots_away":             a_stats.get("shots_total"),
                "shots_on_target_home":   h_stats.get("shots_on_goal") or h_stats.get("shots_on_target"),
                "shots_on_target_away":   a_stats.get("shots_on_goal") or a_stats.get("shots_on_target"),
                "corners_home":           h_stats.get("corners"),
                "corners_away":           a_stats.get("corners"),
                "possession_home":        h_stats.get("possession"),
                "possession_away":        a_stats.get("possession"),
                "dangerous_attacks_home": h_stats.get("dangerous_attacks"),
                "dangerous_attacks_away": a_stats.get("dangerous_attacks"),
                "xg_home":                h_stats.get("xg"),
                "xg_away":                a_stats.get("xg"),
            },
            "reason":      (interpreter or {}).get("narrative") or live.get("reason"),
            "reason_codes": list((interpreter or {}).get("reason_codes") or live.get("reason_codes") or []),
            "status":      "open",
            "outcome": {
                "result":            "pending",
                "settled_minute":    None,
                "settled_score":     None,
                "settlement_reason": None,
            },
            "superseded_by_event_id": None,
            "dedup_key":   dedup_key,
            "created_at":  now,
            "updated_at":  now,
            "_schema":     "live_recommendation_event.1",
        }

        await db[COLLECTION].insert_one(doc)

        # Mark the previous open event as superseded (only if market changed).
        if previous and previous.get("event_id"):
            prev_market = ((previous.get("recommendation") or {}).get("market") or "").lower()
            new_market = (rec_market or "").lower()
            if prev_market != new_market:
                try:
                    await db[COLLECTION].update_one(
                        {"event_id": previous["event_id"], "status": {"$in": ["open", "manual_recorded"]}},
                        {"$set": {
                            "status":                  "superseded",
                            "superseded_by_event_id":  event_id,
                            "updated_at":              now,
                        }},
                    )
                except Exception as exc:
                    log.debug("supersede update failed: %s", exc)

        # Additionally: link any already-settled previous event for the
        # same match to this new event WITHOUT changing its status. This
        # implements the product rule: "if a recommendation was HIT and
        # the engine later changes, the prior event MUST stay HIT but
        # can carry superseded_by_event_id pointing at the new lecture".
        try:
            last_any = await db[COLLECTION].find_one(
                {
                    "user_id":  user_id,
                    "sport":    sport,
                    "match_id": str(match_id),
                    "event_id": {"$ne": event_id},
                    "superseded_by_event_id": None,
                    "status":  {"$in": ["hit", "miss", "push", "void"]},
                },
                sort=[("created_at", -1)],
            )
            if last_any and last_any.get("event_id"):
                prev_market = ((last_any.get("recommendation") or {}).get("market") or "").lower()
                if prev_market != (rec_market or "").lower():
                    await link_supersede_only(
                        db,
                        previous_event_id=last_any["event_id"],
                        new_event_id=event_id,
                    )
        except Exception as exc:
            log.debug("settled-supersede link failed: %s", exc)
        doc.pop("_id", None)
        return doc
    except Exception as exc:
        log.debug("persist_live_recommendation_event failed: %s", exc)
        return None


async def record_manual_live_event(
    db,
    *,
    user_id: str | None,
    payload: dict,
) -> Optional[dict]:
    """Persist a user-supplied manual event. Fail-soft."""
    if db is None or not isinstance(payload, dict):
        return None
    try:
        sport = payload.get("sport") or "football"
        match_id = payload.get("match_id")
        market = ((payload.get("recommendation") or {}).get("market"))
        minute = payload.get("minute")
        if not (sport and match_id and market):
            return None
        if minute is not None:
            try:
                minute = int(minute)
            except (TypeError, ValueError):
                minute = None

        score = _normalize_score(payload.get("score") or {})
        rec = payload.get("recommendation") or {}
        outcome = payload.get("outcome") or {}
        result = (outcome.get("result") or "pending").lower()

        dedup_key = _build_dedup_key(
            user_id=user_id, sport=sport, match_id=match_id,
            minute=minute, score_label=score.get("label"),
            market=market, selection=rec.get("selection"),
        )
        if await _exists_dedup(db, dedup_key):
            return None

        status = (
            "hit" if result == "hit"
            else "miss" if result == "miss"
            else "void" if result == "void"
            else "manual_recorded"
        )

        event_id = str(uuid.uuid4())
        now = _NOW()
        doc = {
            "event_id":    event_id,
            "user_id":     user_id,
            "sport":       sport,
            "match_id":    str(match_id),
            "match_label": payload.get("match_label"),
            "league":      payload.get("league"),
            "source":      "manual",
            "event_type":  "manual_event",
            "minute":      minute,
            "score":       score,
            "recommendation": {
                "title":              rec.get("title") or rec.get("market"),
                "market":             rec.get("market"),
                "selection":          rec.get("selection") or rec.get("market"),
                "suggested_market":   rec.get("market"),
                "confidence":         rec.get("confidence"),
                "risk_level":         rec.get("risk_level"),
                "recommended_action": (rec.get("recommended_action") or "LIVE_ENTRY").upper(),
            },
            "live_context": payload.get("live_context") or {},
            "reason":      payload.get("reason"),
            "reason_codes": list(payload.get("reason_codes") or []),
            "notes":       payload.get("notes"),
            "status":      status,
            "outcome": {
                "result":            result,
                "settled_minute":    outcome.get("settled_minute"),
                "settled_score":     outcome.get("settled_score"),
                "settlement_reason": outcome.get("settlement_reason"),
            },
            "superseded_by_event_id": None,
            "dedup_key":   dedup_key,
            "created_at":  now,
            "updated_at":  now,
            "_schema":     "live_recommendation_event.1",
        }
        await db[COLLECTION].insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception as exc:
        log.debug("record_manual_live_event failed: %s", exc)
        return None


async def settle_live_recommendation_event(
    db,
    *,
    event_id: str,
    result: str,
    settled_score: str | None = None,
    settled_minute: int | None = None,
    settlement_reason: str | None = None,
) -> bool:
    if db is None or not event_id:
        return False
    try:
        status = (
            "hit" if result == "hit"
            else "miss" if result == "miss"
            else "void" if result == "void"
            else "open"
        )
        await db[COLLECTION].update_one(
            {"event_id": event_id},
            {"$set": {
                "status":  status,
                "outcome": {
                    "result":            result,
                    "settled_minute":    settled_minute,
                    "settled_score":     settled_score,
                    "settlement_reason": settlement_reason,
                },
                "updated_at": _NOW(),
            }},
        )
        return True
    except Exception as exc:
        log.debug("settle_live_recommendation_event failed: %s", exc)
        return False


_RESULT_TO_STATUS = {
    "hit":  "hit",
    "miss": "miss",
    "push": "push",
    "void": "void",
}


async def query_live_recommendation_events(
    db,
    *,
    user_id: str | None,
    sport: str | None = "football",
    match_id: str | None = None,
    status: str | None = None,
    result: str | None = None,
    source: str | None = None,
    event_type: str | None = None,
    settled: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query live recommendation events with full filter support.

    Sorting rule (confirmed by product):
      * If ``match_id`` is provided → ascending ``(minute, created_at)``
        so the timeline renders chronologically per match.
      * Otherwise → descending ``created_at`` (most recent first).

    Fail-soft: returns ``[]`` on any DB error.
    """
    if db is None:
        return []
    try:
        q: dict[str, Any] = {}
        if sport:
            q["sport"] = sport
        # We DO NOT scope manual backfill to user_id automatically,
        # because the timeline is shared (the engine may store events
        # with user_id and the manual backfill may target the same
        # match_id without it). Match-level queries should return all
        # events for the match regardless of user_id.
        if user_id and not match_id:
            q["user_id"] = user_id
        if match_id:
            q["match_id"] = str(match_id)
        if status:
            q["status"] = status
        if result:
            q["outcome.result"] = result
        if source:
            q["source"] = source
        if event_type:
            q["event_type"] = event_type
        if settled is True:
            q["status"] = {"$in": ["hit", "miss", "push", "void"]}
        elif settled is False:
            q["status"] = {"$in": ["open", "manual_recorded", "superseded", "watchlist"]}
        if date_from or date_to:
            rng: dict[str, str] = {}
            if date_from:
                rng["$gte"] = str(date_from)
            if date_to:
                # Inclusive end-of-day if only a date was given.
                rng["$lte"] = str(date_to) + ("T23:59:59Z" if len(str(date_to)) == 10 else "")
            q["created_at"] = rng

        if match_id:
            sort_spec = [("minute", 1), ("created_at", 1)]
        else:
            sort_spec = [("created_at", -1)]

        cursor = db[COLLECTION].find(q).sort(sort_spec).limit(int(limit))
        out: list[dict] = []
        async for d in cursor:
            d.pop("_id", None)
            out.append(d)
        return out
    except Exception as exc:
        log.debug("query_live_recommendation_events failed: %s", exc)
        return []


async def link_supersede_only(
    db, *, previous_event_id: str, new_event_id: str,
) -> bool:
    """Attach ``superseded_by_event_id`` WITHOUT changing the status.

    Used when the previous event is already settled (``hit``/``miss``)
    and the engine emits a NEW recommendation for the same match. We
    keep the prior status intact (per product rule: "if a recommendation
    is hit and later changes, the original stays HIT") and just record
    the link.
    """
    if db is None or not previous_event_id or not new_event_id:
        return False
    try:
        await db[COLLECTION].update_one(
            {"event_id": previous_event_id, "superseded_by_event_id": None},
            {"$set": {
                "superseded_by_event_id": new_event_id,
                "updated_at": _NOW(),
            }},
        )
        return True
    except Exception as exc:
        log.debug("link_supersede_only failed: %s", exc)
        return False


__all__ = [
    "COLLECTION",
    "ensure_live_recommendation_indexes",
    "settle_live_event_from_score",
    "persist_live_recommendation_event",
    "record_manual_live_event",
    "settle_live_recommendation_event",
    "query_live_recommendation_events",
    "link_supersede_only",
]
