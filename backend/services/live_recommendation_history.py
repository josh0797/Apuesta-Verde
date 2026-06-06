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
# Market normalization (BTTS + Totals) — robust against any text source
# ─────────────────────────────────────────────────────────────────────
# Canonical normalized labels (machine-readable). The display labels
# (e.g. "BTTS YES", "Ambos equipos marcan: Sí") are decoupled so the
# UI can render in the user's locale.
NORM_BTTS_YES   = "BTTS_YES"
NORM_BTTS_NO    = "BTTS_NO"
NORM_OVER_05    = "OVER_0_5"
NORM_OVER_15    = "OVER_1_5"
NORM_OVER_25    = "OVER_2_5"
NORM_OVER_35    = "OVER_3_5"
NORM_OVER_45    = "OVER_4_5"
NORM_UNDER_15   = "UNDER_1_5"
NORM_UNDER_25   = "UNDER_2_5"
NORM_UNDER_35   = "UNDER_3_5"

# Canonical display per normalized label (Spanish-first; selectable in UI).
NORM_DISPLAY = {
    NORM_BTTS_YES:  ("BTTS YES",   "Ambos equipos marcan: Sí"),
    NORM_BTTS_NO:   ("BTTS NO",    "Ambos equipos marcan: No"),
    NORM_OVER_05:   ("Over 0.5",   "Más de 0.5 goles"),
    NORM_OVER_15:   ("Over 1.5",   "Más de 1.5 goles"),
    NORM_OVER_25:   ("Over 2.5",   "Más de 2.5 goles"),
    NORM_OVER_35:   ("Over 3.5",   "Más de 3.5 goles"),
    NORM_OVER_45:   ("Over 4.5",   "Más de 4.5 goles"),
    NORM_UNDER_15:  ("Under 1.5",  "Menos de 1.5 goles"),
    NORM_UNDER_25:  ("Under 2.5",  "Menos de 2.5 goles"),
    NORM_UNDER_35:  ("Under 3.5",  "Menos de 3.5 goles"),
}

_BTTS_YES_PATTERNS = (
    "btts yes",
    "btts sí",
    "btts si",
    "btts (sí)",
    "btts (si)",
    "ambos marcan",
    "ambos equipos marcan",
    "ambos equipos anotan",
    "both teams score",
    "both teams to score",
    "btts (ambos marcan)",
    "btts (ambos equipos marcan)",
)
_BTTS_NO_PATTERNS = (
    "btts no",
    "btts (no)",
    "ambos no marcan",
    "ambos equipos no marcan",
    "both teams don't score",
)
# Bare "btts" mention is interpreted as BTTS YES unless an explicit
# negative pattern is detected — this matches how the engine emits
# "BTTS (Ambos marcan)" without an explicit "yes" token.


def _contains_any(text: str, patterns) -> bool:
    return any(p in text for p in patterns)


def normalize_live_market_label(
    market: Any = None,
    selection: Any = None,
    title: Any = None,
    *extra_texts: Any,
) -> str | None:
    """Detect a canonical normalized market label across heterogeneous text.

    Returns one of ``NORM_*`` constants or ``None`` when no supported
    market is found. Pure & fail-soft.

    Resolution order (each text is scanned):
      1. Explicit "Over X.5" / "Under X.5" mentions (more specific first).
      2. BTTS YES patterns (including bare "btts" w/o "no").
      3. BTTS NO patterns.
    """
    chunks: list[str] = []
    for raw in (market, selection, title, *extra_texts):
        if raw is None:
            continue
        if isinstance(raw, (list, tuple, set)):
            for r in raw:
                if r is not None:
                    chunks.append(_market_lower(r))
            continue
        chunks.append(_market_lower(raw))

    combined = " | ".join(c for c in chunks if c)
    if not combined:
        return None

    # ── Totals first (most specific) ─────────────────────────────────
    is_over = "over" in combined or "más de" in combined or "mas de" in combined
    is_under = "under" in combined or "menos de" in combined
    if is_over or is_under:
        # Find the largest line mentioned (so "Over 2.5" beats "Over 0.5"
        # when both happen to appear in narrative).
        line_to_norm = {
            "0.5":  (NORM_OVER_05, None),
            "1.5":  (NORM_OVER_15, NORM_UNDER_15),
            "2.5":  (NORM_OVER_25, NORM_UNDER_25),
            "3.5":  (NORM_OVER_35, NORM_UNDER_35),
            "4.5":  (NORM_OVER_45, None),
        }
        # Scan the more specific markets first when both Over/Under share text.
        for cand in ("3.5", "2.5", "1.5", "4.5", "0.5"):
            if cand in combined:
                over_norm, under_norm = line_to_norm[cand]
                if is_over and over_norm:
                    return over_norm
                if is_under and under_norm:
                    return under_norm

    # ── BTTS NO (must be checked before bare BTTS YES) ────────────────
    if _contains_any(combined, _BTTS_NO_PATTERNS):
        return NORM_BTTS_NO

    # ── BTTS YES (explicit patterns OR bare "btts" mention) ──────────
    if _contains_any(combined, _BTTS_YES_PATTERNS):
        return NORM_BTTS_YES
    if "btts" in combined and " no" not in combined:
        return NORM_BTTS_YES

    return None


def display_market_for(normalized: str | None) -> tuple[str | None, str | None]:
    """Return ``(market_display, selection_display)`` for a canonical
    normalized label, or ``(None, None)`` if unknown."""
    if normalized and normalized in NORM_DISPLAY:
        return NORM_DISPLAY[normalized]
    return (None, None)


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

    The function consults ``recommendation.normalized_market`` first
    (set by ``persist_live_recommendation_event`` or manual entry) and
    falls back to a free-form text scan when it is missing — this
    guarantees deterministic settling for engine-generated events while
    staying backwards-compatible with older docs.
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
    normalized = (
        rec.get("normalized_market")
        or event.get("normalized_market")
        or normalize_live_market_label(
            rec.get("market"),
            rec.get("selection"),
            rec.get("title"),
            rec.get("suggested_market"),
            event.get("reason"),
        )
    )

    # ── BTTS YES ────────────────────────────────────────────────────
    if normalized == NORM_BTTS_YES:
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
    if normalized == NORM_BTTS_NO:
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
    OVER_LINES = {
        NORM_OVER_05: 0.5, NORM_OVER_15: 1.5, NORM_OVER_25: 2.5,
        NORM_OVER_35: 3.5, NORM_OVER_45: 4.5,
    }
    UNDER_LINES = {
        NORM_UNDER_15: 1.5, NORM_UNDER_25: 2.5, NORM_UNDER_35: 3.5,
    }
    if normalized in OVER_LINES:
        line = OVER_LINES[normalized]
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

    if normalized in UNDER_LINES:
        line = UNDER_LINES[normalized]
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
    # ── New (Phase 33 P0 fix): persist when the offensive market shows up
    # only in narrative fields. The engine sometimes leaves `suggested_market`
    # empty but encodes BTTS / Over X.5 in `interpreter.reason`/`why`/
    # `narrative` (rendered as the "Mercado ofensivo: …" badge in the UI).
    if not has_interp_market and isinstance(interpreter, dict):
        narrative_texts = []
        for fld in ("reason", "why", "narrative", "context",
                     "reason_pre", "headline", "title"):
            v = interpreter.get(fld)
            if isinstance(v, str):
                narrative_texts.append(v)
        for fld in ("reasons", "reason_codes", "reasons_list"):
            v = interpreter.get(fld)
            if isinstance(v, (list, tuple)):
                for it in v:
                    if isinstance(it, str):
                        narrative_texts.append(it)
        if narrative_texts and normalize_live_market_label(*narrative_texts):
            has_interp_market = True

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

        # ── Detect a normalized market across all UI-visible texts ────
        # The engine occasionally surfaces the offensive market only in
        # narrative fields (e.g. `interpreter.reason`, `interpreter.why`,
        # `live.reason`, `live.suggested_market_label`) while `market`
        # itself remains "momentum local". We must still register the
        # event because that's what the user sees as the "Mercado ofensivo:
        # BTTS (Ambos marcan)" badge in the UI.
        interp = interpreter or {}
        live_reasons_text: list[str] = []
        for fld in ("reason", "why", "narrative", "context",
                     "reason_pre", "headline", "suggested_market_label"):
            v = interp.get(fld)
            if isinstance(v, str) and v:
                live_reasons_text.append(v)
            v = (live or {}).get(fld) if fld != "suggested_market_label" else None
            if isinstance(v, str) and v:
                live_reasons_text.append(v)
        # reasons_list / reason_codes can carry BTTS as a code.
        for fld in ("reasons", "reason_codes", "reasons_list"):
            v = interp.get(fld)
            if isinstance(v, (list, tuple)):
                for it in v:
                    if isinstance(it, str):
                        live_reasons_text.append(it)

        normalized_market = normalize_live_market_label(
            rec_market, rec_selection, rec_title,
            interp.get("suggested_market"),
            interp.get("market"),
            *live_reasons_text,
        )
        if normalized_market:
            disp_market, disp_selection = display_market_for(normalized_market)
            if disp_market:
                # Surface the canonical market in the persisted document so
                # the timeline and auto-settlement consume a consistent
                # vocabulary instead of "momentum local".
                rec_market = disp_market
            if disp_selection and (
                not rec_selection
                or _market_lower(rec_selection) == _market_lower(rec_title or "")
            ):
                rec_selection = disp_selection
            # Force a sensible title when the original is a status label.
            if rec_title and _market_lower(rec_title) in (
                "momentum local", "momentum visitante",
                "partido abierto", "partido controlado",
            ):
                rec_title = disp_market or rec_title

        recommendation = {
            "title":               rec_title,
            "market":              rec_market,
            "selection":           rec_selection,
            "suggested_market":    (interpreter or {}).get("suggested_market"),
            "normalized_market":   normalized_market,
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

        # Detect normalized market across the recommendation/reason.
        normalized_market = normalize_live_market_label(
            rec.get("market"),
            rec.get("selection"),
            rec.get("title"),
            payload.get("reason"),
        )

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
                "normalized_market":  normalized_market,
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
        # Fix 5+6: void/push/refund/cancelled converge to status="void"
        # so the UI renders a neutral row AND the pattern_memory layer
        # downstream skips degradation (mirrors warehouse._VOID_OUTCOMES).
        _r = (result or "").lower()
        if _r in ("hit", "won", "win"):
            status = "hit"
        elif _r in ("miss", "lost", "lose"):
            status = "miss"
        elif _r in ("push",):
            status = "push"
        elif _r in ("void", "refund", "refunded", "cancelled", "canceled"):
            status = "void"
        else:
            status = "open"
        await db[COLLECTION].update_one(
            {"event_id": event_id},
            {"$set": {
                "status":  status,
                "outcome": {
                    "result":            _r or "pending",
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
    "hit":      "hit",
    "won":      "hit",
    "win":      "hit",
    "miss":     "miss",
    "lost":     "miss",
    "lose":     "miss",
    "push":     "push",
    "void":     "void",
    "refund":   "void",
    "refunded": "void",
    "cancelled": "void",
    "canceled":  "void",
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


async def settle_open_live_events_for_match(
    db,
    *,
    sport: str,
    match: dict | None,
    user_id: str | None = None,
) -> dict:
    """Find every open / watchlist event for ``match`` and auto-settle
    those that resolve against the current score (BTTS / Over / Under).

    Fail-soft: returns ``{"updated": 0, "errors": [...]}`` on errors.

    Use cases:
      * Called from ``/api/live/reevaluate`` right after persisting the
        new live recommendation.
      * Called from the timeline ``GET`` endpoint (already in place via
        the ``auto_settle=true`` path).
      * Could be called from a periodic sweeper / live ingest job.
    """
    result = {"updated": 0, "errors": []}
    if db is None or not isinstance(match, dict):
        return result
    try:
        match_id = match.get("match_id") or (match.get("fixture") or {}).get("id")
        if not match_id:
            return result
        live = match.get("live_stats") if isinstance(match.get("live_stats"), dict) else {}
        score_raw = live.get("score") if isinstance(live.get("score"), dict) else match.get("score")
        if not isinstance(score_raw, dict):
            score_raw = {
                "home": live.get("goals_home") or live.get("home_goals"),
                "away": live.get("goals_away") or live.get("away_goals"),
            }
        score = _normalize_score(score_raw)
        if score.get("home") is None or score.get("away") is None:
            return result

        minute = live.get("minute") or (live.get("status") or {}).get("elapsed")
        try:
            minute_int = int(minute) if minute is not None else None
        except (TypeError, ValueError):
            minute_int = None
        status_txt = (match.get("status") or live.get("status") or "")
        match_ended = (
            (isinstance(status_txt, str) and status_txt in ("FT", "AET", "PEN", "FINISHED"))
            or (isinstance(status_txt, dict) and (status_txt.get("short") or status_txt.get("long")) in ("FT", "AET", "PEN", "FINISHED"))
        )

        q = {
            "sport":    sport,
            "match_id": str(match_id),
            "status":   {"$in": ["open", "manual_recorded", "watchlist"]},
        }
        if user_id:
            q["user_id"] = user_id
        cursor = db[COLLECTION].find(q)
        async for ev in cursor:
            try:
                # ── Extended branch: corners / future markets ─────────
                # Try the extended settlement first. If it doesn't apply
                # (returns None), fall back to the legacy BTTS/Over-Under
                # path. The extended branch never re-settles already-hit
                # events because we don't even reach this code path for
                # them (status filter above excludes hit/miss).
                ext_settlement = None
                try:
                    from . import live_recommendation_settlement as _lrs
                    fms = {
                        # Surface anything that could carry corner stats.
                        "corners_home":  live.get("corners_home"),
                        "corners_away":  live.get("corners_away"),
                        "home_corners":  live.get("home_corners"),
                        "away_corners":  live.get("away_corners"),
                        "stats":         live.get("stats") or match.get("stats"),
                        "final_stats":   match.get("final_stats"),
                        "corners":       live.get("corners") or match.get("corners"),
                        "home_team":     match.get("home_team"),
                        "away_team":     match.get("away_team"),
                    }
                    ext_settlement = _lrs.settle_event_extended(ev, fms)
                except Exception as _exc:
                    log.debug("extended settlement dispatch failed: %s", _exc)

                if ext_settlement is not None:
                    if ext_settlement.get("status") in ("hit", "miss", "void"):
                        ok = await settle_live_recommendation_event(
                            db,
                            event_id=ev["event_id"],
                            result=ext_settlement["status"],
                            settled_score=_score_label(score),
                            settled_minute=minute_int,
                            settlement_reason=ext_settlement.get("reason_es"),
                        )
                        if ok:
                            result["updated"] += 1
                    # pending / requires_manual_settlement → leave event alone.
                    continue

                # ── Legacy BTTS / total goals settlement ──────────────
                settlement = settle_live_event_from_score(
                    ev, score, minute=minute_int, match_ended=bool(match_ended),
                )
                if settlement.get("result") in ("hit", "miss", "void", "push"):
                    ok = await settle_live_recommendation_event(
                        db,
                        event_id=ev["event_id"],
                        result=settlement["result"],
                        settled_score=settlement.get("settled_score"),
                        settled_minute=settlement.get("settled_minute"),
                        settlement_reason=settlement.get("settlement_reason"),
                    )
                    if ok:
                        result["updated"] += 1
            except Exception as exc:
                result["errors"].append(str(exc)[:200])
        return result
    except Exception as exc:
        log.debug("settle_open_live_events_for_match failed: %s", exc)
        result["errors"].append(str(exc)[:200])
        return result


__all__ = [
    "COLLECTION",
    "ensure_live_recommendation_indexes",
    "settle_live_event_from_score",
    "persist_live_recommendation_event",
    "record_manual_live_event",
    "settle_live_recommendation_event",
    "query_live_recommendation_events",
    "link_supersede_only",
    "settle_open_live_events_for_match",
    # Market normalization API
    "normalize_live_market_label",
    "display_market_for",
    "NORM_BTTS_YES", "NORM_BTTS_NO",
    "NORM_OVER_05", "NORM_OVER_15", "NORM_OVER_25", "NORM_OVER_35", "NORM_OVER_45",
    "NORM_UNDER_15", "NORM_UNDER_25", "NORM_UNDER_35",
]
