"""Sprint F.2 — 365Scores **Top Trends** client (JSON contract).

Discovered endpoint
-------------------
::

    GET https://webws.365scores.com/web/trends/
        ?appTypeId=5&langId=1&timezoneName=UTC
        &userCountryId=333&games=<game_id>&topBookmaker=103

Returns a structured JSON document with a ``trends`` array. Each trend
carries:

* ``id``, ``lineTypeId``, ``text``, ``cause``, ``betCTA``
* ``competitorIds`` (list of ``team_id`` ints)
* ``gameId``
* ``percentage`` (0..1 — the hit rate)
* ``isTop`` (the *Top Trends* badge: ``True`` for the highlighted ones)
* ``odds`` (rate / oldRate / originalRate / trend)
* ``confidenceTrendIds`` (cross-reference to corroborating trends)

This module:

1. Calls the endpoint via a pluggable transport (``Scrape.do`` in
   production, a deterministic stub in tests).
2. Normalises every trend into the canonical row consumed by the UI.
3. Caches the normalised payload in MongoDB
   (collection ``football_365scores_top_trends``) with a TTL.

Public API
----------
:func:`fetch_top_trends`
    Low-level: takes a 365Scores ``game_id`` and returns normalised rows.
:func:`fetch_top_trends_for_match`
    High-level: takes a canonical match descriptor (``home_team``,
    ``away_team``, ``commence_time``, …), resolves the 365Scores
    identity via Sprint F.1, then fetches trends.
:func:`normalize_trends_payload`
    Pure parser exposed for unit-testing — takes a raw 365Scores
    response and returns the canonical list.
:func:`ensure_indexes`
    Create the TTL/cache indexes on
    ``football_365scores_top_trends``.

Strictly observe-only. Trends are evidence; they do **not** modify
the engine's picks.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Optional

from . import three65scores_identity_resolver as id_resolver

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
SOURCE_LABEL                 = "365scores_top_trends"
PROVIDER                     = "365scores"
ENDPOINT_BASE                = "https://webws.365scores.com/web/trends/"
MONGO_COLLECTION             = "football_365scores_top_trends"
DEFAULT_CACHE_TTL_SECONDS    = 30 * 60          # 30 min
DEFAULT_LANG_ID              = 1                # 1=English, 29=Spanish
DEFAULT_TIMEZONE             = "UTC"
DEFAULT_USER_COUNTRY_ID      = 333
DEFAULT_TOP_BOOKMAKER        = 103
DEFAULT_TIMEOUT_S            = 35.0

# Status / reason codes.
RC_TRENDS_FOUND              = "F2_TOP_TRENDS_FOUND"
RC_TRENDS_EMPTY              = "F2_TOP_TRENDS_EMPTY"
RC_FROM_CACHE                = "F2_TOP_TRENDS_FROM_CACHE"
RC_TRANSPORT_UNAVAILABLE     = "F2_TRANSPORT_UNAVAILABLE"
RC_TRANSPORT_ERROR           = "F2_TRANSPORT_ERROR"
RC_PARSE_FAILED              = "F2_PARSE_FAILED"
RC_IDENTITY_REQUIRED         = "F2_IDENTITY_REQUIRED"
RC_IDENTITY_NOT_RESOLVED     = "F2_IDENTITY_NOT_RESOLVED"
RC_GAME_ID_MISSING           = "F2_GAME_ID_MISSING"

# Confidence buckets.
CONFIDENCE_HIGH    = "HIGH"
CONFIDENCE_MEDIUM  = "MEDIUM"
CONFIDENCE_LOW     = "LOW"

# Map of observed ``lineTypeId`` → canonical market label. Values not
# in this table are surfaced verbatim as ``LINE_TYPE_<id>`` (we DO NOT
# silently drop unknown markets).
LINE_TYPE_MAP: dict[int, str] = {
    1:  "ML",
    3:  "OU_GOALS",
    5:  "1H_ML",
    7:  "FIRST_GOAL",
    12: "BTTS",
}

# Regex helpers for natural-language sample extraction.
_RX_SAMPLE = re.compile(r"(\d+)\s*/\s*(\d+)")
_RX_LAST_N = re.compile(
    r"(?:ultimos|últimos|last)\s+(\d+)\s*(?:partidos|matches|juegos|games)?",
    re.IGNORECASE,
)
_RX_NUMERIC_LINE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Pure parser
# ─────────────────────────────────────────────────────────────────────
def _detect_scope(text: str, line_type_id: Optional[int]) -> str:
    """Return ``home`` / ``away`` / ``first_half`` / ``all``."""
    if not isinstance(text, str):
        return "all"
    tl = text.lower()
    if "first half" in tl or "primer tiempo" in tl or "1st half" in tl:
        return "first_half"
    if " away" in tl or " visitante" in tl or " as visitor" in tl:
        return "away"
    if " at home" in tl or "en casa" in tl or " home" in tl:
        return "home"
    if line_type_id == 5:
        return "first_half"
    return "all"


def _detect_team_side(
    competitor_ids: Optional[list[int]],
    *,
    home_team_id: Optional[int],
    away_team_id: Optional[int],
) -> str:
    """Return ``home`` / ``away`` / ``both`` / ``unknown``."""
    if not isinstance(competitor_ids, list) or not competitor_ids:
        return "unknown"
    ids = {_safe_int(c) for c in competitor_ids}
    ids.discard(None)
    h_in = (home_team_id is not None) and (home_team_id in ids)
    a_in = (away_team_id is not None) and (away_team_id in ids)
    if h_in and a_in:
        return "both"
    if h_in:
        return "home"
    if a_in:
        return "away"
    return "unknown"


def _detect_team_name(
    *,
    team_side: str,
    home_team_name: Optional[str],
    away_team_name: Optional[str],
) -> Optional[str]:
    if team_side == "home":
        return home_team_name
    if team_side == "away":
        return away_team_name
    if team_side == "both":
        return None
    return None


def _extract_sample(text: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None
    m = _RX_SAMPLE.search(text)
    if not m:
        return None
    try:
        hits  = int(m.group(1))
        total = int(m.group(2))
        if total <= 0 or hits > total:
            return None
        return {"hits": hits, "total": total, "rate": round(hits / total, 4)}
    except ValueError:
        return None


def _extract_period(text: str, sample: Optional[dict]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    m = _RX_LAST_N.search(text)
    if m:
        try:
            return f"last_{int(m.group(1))}_matches"
        except ValueError:
            pass
    if sample and sample.get("total"):
        return f"last_{sample['total']}_matches"
    return None


def _confidence_from_trend(
    *,
    sample: Optional[dict],
    is_top: bool,
    percentage: Optional[float],
) -> str:
    """Confidence heuristic, biased upwards when ``isTop`` is set."""
    total = (sample or {}).get("total") if sample else None
    pct = percentage if percentage is not None else (
        (sample or {}).get("rate") if sample else None
    )
    if total and pct is not None:
        if total >= 10 and pct >= 0.80:
            return CONFIDENCE_HIGH
        if total >= 5 and pct >= 0.70:
            return CONFIDENCE_MEDIUM
    # isTop floor is MEDIUM (the source explicitly highlighted the row).
    if is_top:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _market_label(line_type_id: Optional[int]) -> str:
    if line_type_id in LINE_TYPE_MAP:
        return LINE_TYPE_MAP[line_type_id]
    if line_type_id is None:
        return "UNKNOWN"
    return f"LINE_TYPE_{int(line_type_id)}"


def normalize_trend(
    trend: dict,
    *,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
    language: str = "en",
) -> Optional[dict]:
    """Translate one 365Scores trend dict into the canonical row.

    Returns ``None`` when the input cannot be parsed at all (no ``text``
    and no ``id``). Pure / deterministic.
    """
    if not isinstance(trend, dict):
        return None
    text = trend.get("text") or trend.get("description")
    if not text and trend.get("id") is None:
        return None
    line_type_id = _safe_int(trend.get("lineTypeId"))
    is_top = bool(trend.get("isTop"))
    competitor_ids = trend.get("competitorIds") or []
    if not isinstance(competitor_ids, list):
        competitor_ids = []
    pct = _safe_float(trend.get("percentage"))
    sample = _extract_sample(text or "")
    period = _extract_period(text or "", sample)
    scope = _detect_scope(text or "", line_type_id)
    team_side = _detect_team_side(
        competitor_ids, home_team_id=home_team_id, away_team_id=away_team_id,
    )
    team_name = _detect_team_name(
        team_side=team_side, home_team_name=home_team_name,
        away_team_name=away_team_name,
    )
    # Use percentage when sample failed (some lineTypes don't include
    # numerator/denominator in the text).
    rate = (sample or {}).get("rate") if sample else pct
    return {
        "raw":          text or "",
        "trend_id":     _safe_int(trend.get("id")),
        "is_top":       is_top,
        "line_type_id": line_type_id,
        "market":       _market_label(line_type_id),
        "cause":        trend.get("cause"),
        "bet_cta":      trend.get("betCTA"),
        "team_ids":     [c for c in (_safe_int(x) for x in competitor_ids) if c is not None],
        "team_side":    team_side,
        "team_name":    team_name,
        "scope":        scope,
        "sample":       sample,
        "rate":         rate,
        "percentage":   pct,
        "period":       period,
        "odds":         _summarise_odds(trend.get("odds")),
        "confidence":   _confidence_from_trend(
            sample=sample, is_top=is_top, percentage=pct,
        ),
        "language":     language,
    }


def _summarise_odds(odds: Any) -> Optional[dict]:
    """Pull a compact view of the ``odds`` block (best-effort)."""
    if not isinstance(odds, dict):
        return None
    out: dict[str, Any] = {}
    rate = odds.get("rate")
    if isinstance(rate, dict):
        out["decimal"] = _safe_float(rate.get("decimal"))
    old_rate = odds.get("oldRate")
    if isinstance(old_rate, dict):
        out["decimal_old"] = _safe_float(old_rate.get("decimal"))
    orig = odds.get("originalRate")
    if isinstance(orig, dict):
        out["decimal_original"] = _safe_float(orig.get("decimal"))
    out["bookmaker_id"] = _safe_int(odds.get("bookmakerId"))
    out["trend_dir"]    = _safe_int(odds.get("trend"))
    return out


def normalize_trends_payload(
    payload: Any,
    *,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
    language: str = "en",
) -> list[dict]:
    """Translate a raw ``/web/trends/`` payload into a canonical list.

    Accepts both the full response (with ``trends`` key) and a bare
    list of trend dicts. Items that fail to parse are silently
    skipped — never raises.
    """
    if isinstance(payload, dict):
        items = payload.get("trends") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out: list[dict] = []
    for it in items:
        row = normalize_trend(
            it, home_team_id=home_team_id, away_team_id=away_team_id,
            home_team_name=home_team_name, away_team_name=away_team_name,
            language=language,
        )
        if row is not None:
            out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────
# Mongo cache
# ─────────────────────────────────────────────────────────────────────
async def ensure_indexes(db: Any) -> dict:
    """Create cache indexes (unique by game_id+lang, TTL on fetched_at)."""
    if db is None:
        return {"created": [], "skipped": "no_db"}
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return {"created": [], "skipped": "no_collection"}
    created: list[str] = []
    try:
        await coll.create_index(
            [("game_id", 1), ("language", 1)],
            unique=True, name="ix_game_language",
        )
        created.append("ix_game_language")
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_trends] ix_game_language failed: %s", exc)
    try:
        # TTL — 6h hard ceiling regardless of cache_ttl_seconds.
        await coll.create_index(
            "fetched_at", expireAfterSeconds=6 * 3600,
            name="ttl_fetched_at",
        )
        created.append("ttl_fetched_at")
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_trends] ttl_fetched_at failed: %s", exc)
    return {"created": created}


async def _get_cached(
    *, db: Any, game_id: int, language: str, cache_ttl_seconds: int,
) -> Optional[dict]:
    if db is None or not game_id:
        return None
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return None
    try:
        doc = await coll.find_one({"game_id": game_id, "language": language})
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_trends] cache lookup failed: %s", exc)
        return None
    if not doc:
        return None
    fetched_at = doc.get("fetched_at")
    if isinstance(fetched_at, str):
        try:
            fetched_at = datetime.fromisoformat(
                fetched_at.replace("Z", "+00:00")
            )
        except ValueError:
            fetched_at = None
    if not isinstance(fetched_at, datetime):
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    if age > cache_ttl_seconds:
        return None
    return doc


async def _persist_cache(*, db: Any, doc: dict) -> bool:
    if db is None:
        return False
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return False
    try:
        await coll.update_one(
            {"game_id": doc["game_id"], "language": doc["language"]},
            {"$set": doc}, upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_trends] persist failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────
def build_endpoint_url(
    *, game_id: int,
    lang_id: int = DEFAULT_LANG_ID,
    timezone_name: str = DEFAULT_TIMEZONE,
    user_country_id: int = DEFAULT_USER_COUNTRY_ID,
    top_bookmaker: int = DEFAULT_TOP_BOOKMAKER,
) -> str:
    return (
        f"{ENDPOINT_BASE}?appTypeId=5&langId={lang_id}"
        f"&timezoneName={timezone_name}&userCountryId={user_country_id}"
        f"&games={int(game_id)}&topBookmaker={top_bookmaker}"
    )


def _lang_id_for(language: str) -> int:
    return 29 if (language or "").lower().startswith("es") else 1


async def _default_transport(url: str) -> dict:
    """Production transport via Scrape.do. Returns ``{"ok": bool,
    "payload": dict|None, "reason_code": str|None, "status_code": int|None,
    "message_debug": str|None}``.
    """
    try:
        from ..scrape_do_client import fetch_via_scrapedo_result, is_enabled
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "payload": None,
                "reason_code": RC_TRANSPORT_UNAVAILABLE,
                "status_code": None, "message_debug": str(exc)}
    if not is_enabled():
        return {"ok": False, "payload": None,
                "reason_code": RC_TRANSPORT_UNAVAILABLE,
                "status_code": None,
                "message_debug": "SCRAPEDO_TOKEN missing"}
    res = await fetch_via_scrapedo_result(
        url, timeout=DEFAULT_TIMEOUT_S, render=False, geo="mx",
    )
    if not res.get("ok"):
        return {"ok": False, "payload": None,
                "reason_code": res.get("reason_code") or RC_TRANSPORT_ERROR,
                "status_code": res.get("status_code"),
                "message_debug": res.get("message_debug")}
    body = res.get("html") or ""
    import json
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "payload": None,
                "reason_code": RC_PARSE_FAILED,
                "status_code": res.get("status_code"),
                "message_debug": f"json parse: {exc}"}
    return {"ok": True, "payload": payload,
            "reason_code": None, "status_code": res.get("status_code"),
            "message_debug": None}


# ─────────────────────────────────────────────────────────────────────
# Low-level entry point
# ─────────────────────────────────────────────────────────────────────
async def fetch_top_trends(
    *,
    game_id: int,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
    language: str = "en",
    top_bookmaker: int = DEFAULT_TOP_BOOKMAKER,
    db: Any = None,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    transport: Optional[Callable[[str], Awaitable[dict]]] = None,
    force_refresh: bool = False,
    only_top: bool = False,
) -> dict:
    """Fetch + normalise the Top Trends for a single 365Scores ``game_id``.

    Returns a dict (never raises) with the canonical shape documented
    in the module docstring.
    """
    if not game_id:
        return {
            "available": False, "trends": [], "trends_count": 0,
            "top_trends_count": 0,
            "reason_code": RC_GAME_ID_MISSING,
            "fetched_at": _now_iso(), "from_cache": False,
            "source": SOURCE_LABEL,
        }

    game_id_int = int(game_id)
    lang_norm = (language or "en").lower()

    # ── Cache lookup ─────────────────────────────────────────────────
    if not force_refresh:
        cached = await _get_cached(
            db=db, game_id=game_id_int, language=lang_norm,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        if cached and isinstance(cached.get("trends"), list):
            trends = cached["trends"]
            if only_top:
                trends = [t for t in trends if t.get("is_top")]
            return {
                "available":         True,
                "trends":            trends,
                "trends_count":      len(trends),
                "top_trends_count":  sum(1 for t in trends if t.get("is_top")),
                "reason_code":       RC_FROM_CACHE,
                "fetched_at":        cached.get("fetched_at"),
                "from_cache":        True,
                "game_id":           game_id_int,
                "language":          lang_norm,
                "source":            SOURCE_LABEL,
            }

    # ── Transport ────────────────────────────────────────────────────
    url = build_endpoint_url(
        game_id=game_id_int,
        lang_id=_lang_id_for(lang_norm),
        top_bookmaker=top_bookmaker,
    )
    use_transport = transport or _default_transport
    try:
        res = await use_transport(url)
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False, "trends": [], "trends_count": 0,
            "top_trends_count": 0,
            "reason_code": RC_TRANSPORT_ERROR,
            "message_debug": str(exc),
            "fetched_at": _now_iso(), "from_cache": False,
            "source": SOURCE_LABEL, "target_url": url,
        }

    if not (isinstance(res, dict) and res.get("ok")):
        return {
            "available":     False,
            "trends":        [], "trends_count": 0, "top_trends_count": 0,
            "reason_code":   (res or {}).get("reason_code") or RC_TRANSPORT_ERROR,
            "status_code":   (res or {}).get("status_code"),
            "message_debug": (res or {}).get("message_debug"),
            "fetched_at":    _now_iso(), "from_cache": False,
            "source":        SOURCE_LABEL, "target_url": url,
        }

    payload = res.get("payload")
    trends = normalize_trends_payload(
        payload,
        home_team_id=home_team_id, away_team_id=away_team_id,
        home_team_name=home_team_name, away_team_name=away_team_name,
        language=lang_norm,
    )
    if not trends:
        # Either parser failed entirely OR the source returned no trends.
        has_items = bool(isinstance(payload, dict) and payload.get("trends"))
        return {
            "available":     False,
            "trends":        [], "trends_count": 0, "top_trends_count": 0,
            "reason_code":   (RC_PARSE_FAILED if has_items else RC_TRENDS_EMPTY),
            "fetched_at":    _now_iso(), "from_cache": False,
            "source":        SOURCE_LABEL, "target_url": url,
            "game_id":       game_id_int, "language": lang_norm,
        }

    fetched_at_dt = datetime.now(timezone.utc)
    cache_doc = {
        "game_id":        game_id_int,
        "language":       lang_norm,
        "trends":         trends,
        "trends_count":   len(trends),
        "top_trends_count": sum(1 for t in trends if t.get("is_top")),
        "fetched_at":     fetched_at_dt,
        "source":         SOURCE_LABEL,
        "endpoint":       url,
    }
    await _persist_cache(db=db, doc=cache_doc)

    out_trends = ([t for t in trends if t.get("is_top")]
                  if only_top else trends)
    return {
        "available":        True,
        "trends":           out_trends,
        "trends_count":     len(out_trends),
        "top_trends_count": sum(1 for t in trends if t.get("is_top")),
        "reason_code":      RC_TRENDS_FOUND,
        "fetched_at":       fetched_at_dt.isoformat(),
        "from_cache":       False,
        "game_id":          game_id_int,
        "language":         lang_norm,
        "source":           SOURCE_LABEL,
        "target_url":       url,
    }


# ─────────────────────────────────────────────────────────────────────
# High-level entry point (identity + trends in one call)
# ─────────────────────────────────────────────────────────────────────
async def fetch_top_trends_for_match(
    *,
    internal_match_id: str,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    competition_id: Optional[int] = None,
    competition: Optional[str] = None,
    match_url: Optional[str] = None,
    language: str = "en",
    db: Any = None,
    games_fetcher: Optional[Callable[..., Awaitable[Any]]] = None,
    game_detail_fetcher: Optional[Callable[..., Awaitable[Any]]] = None,
    transport: Optional[Callable[[str], Awaitable[dict]]] = None,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    force_refresh: bool = False,
    only_top: bool = False,
) -> dict:
    """Orchestration: resolve identity (F.1) then fetch trends (F.2)."""
    identity = await id_resolver.resolve_match_identity(
        internal_match_id=internal_match_id,
        home_team=home_team, away_team=away_team,
        commence_time=commence_time,
        competition=competition, competition_id=competition_id,
        match_url=match_url,
        db=db,
        games_fetcher=games_fetcher,
        game_detail_fetcher=game_detail_fetcher,
        force_refresh=force_refresh,
    )
    if identity.get("status") != id_resolver.STATUS_RESOLVED:
        return {
            "available":         False,
            "trends":            [], "trends_count": 0, "top_trends_count": 0,
            "reason_code":       RC_IDENTITY_NOT_RESOLVED,
            "identity_status":   identity.get("status"),
            "identity_reason":   identity.get("reason_code"),
            "identity":          identity,
            "fetched_at":        _now_iso(), "from_cache": False,
            "source":            SOURCE_LABEL,
        }
    game_id = identity.get("game_id")
    if not game_id:
        return {
            "available":       False,
            "trends":          [], "trends_count": 0, "top_trends_count": 0,
            "reason_code":     RC_GAME_ID_MISSING,
            "identity":        identity,
            "fetched_at":      _now_iso(), "from_cache": False,
            "source":          SOURCE_LABEL,
        }
    result = await fetch_top_trends(
        game_id=int(game_id),
        home_team_id=identity.get("home_team_id"),
        away_team_id=identity.get("away_team_id"),
        home_team_name=home_team, away_team_name=away_team,
        language=language, db=db, cache_ttl_seconds=cache_ttl_seconds,
        transport=transport, force_refresh=force_refresh, only_top=only_top,
    )
    result["identity"] = {
        "game_id":            identity.get("game_id"),
        "home_team_id":       identity.get("home_team_id"),
        "away_team_id":       identity.get("away_team_id"),
        "competition_id":     identity.get("competition_id"),
        "status":             identity.get("status"),
        "confidence":         identity.get("confidence"),
        "resolved_from":      identity.get("resolved_from"),
        "mapping_reason":     identity.get("mapping_reason"),
    }
    return result


__all__ = [
    "SOURCE_LABEL", "PROVIDER", "ENDPOINT_BASE",
    "MONGO_COLLECTION", "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_LANG_ID", "DEFAULT_TIMEZONE",
    "DEFAULT_USER_COUNTRY_ID", "DEFAULT_TOP_BOOKMAKER",
    "LINE_TYPE_MAP",
    "RC_TRENDS_FOUND", "RC_TRENDS_EMPTY", "RC_FROM_CACHE",
    "RC_TRANSPORT_UNAVAILABLE", "RC_TRANSPORT_ERROR", "RC_PARSE_FAILED",
    "RC_IDENTITY_REQUIRED", "RC_IDENTITY_NOT_RESOLVED",
    "RC_GAME_ID_MISSING",
    "CONFIDENCE_HIGH", "CONFIDENCE_MEDIUM", "CONFIDENCE_LOW",
    "build_endpoint_url",
    "normalize_trend", "normalize_trends_payload",
    "ensure_indexes",
    "fetch_top_trends", "fetch_top_trends_for_match",
]
