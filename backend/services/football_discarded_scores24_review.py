"""Football Discarded Match Scores24 Review — Phase F62.

Triggers an external Scores24 review for every football match that ends
up in any ``discarded_*`` bucket (motivation, market, edge, unknown,
layer conflict, market-no-value). The goal is NOT to mutate the pick
automatically — it is to add an external audit layer that may:

    1. CONFIRM_DISCARD            — Scores24 confirms there's no value.
    2. MOVE_TO_WATCHLIST          — interesting external read, no edge.
    3. RESCUE_ALTERNATIVE_MARKET  — typically a corners pick (Under 9.5).

Design contract
---------------
* Async, fail-soft. NEVER raises; mutates ``match_payload`` only by
  attaching a ``scores24_review`` block.
* Cost-aware. Uses ``scores24_discarded_review_cache`` (Mongo) and a
  daily quota counter to honour ``SCORES24_DISCARDED_MAX_PER_DAY=40``.
* Slug-deterministic resolution. We do NOT do search-engine fallback
  in this phase; if no slug candidate works the call short-circuits
  with ``SCORES24_URL_NOT_RESOLVED``.
* Targeted extraction. Reuses :func:`scores24_scraper.scrape_scores24_match`
  which already extracts ONLY editorial + corners predictions (it
  ignores ads, telegram, comments and player props).

Environment variables (all OPTIONAL)
------------------------------------
* ``SCORES24_DISCARDED_REVIEW_ENABLED``  — master kill-switch (default true).
* ``SCORES24_DISCARDED_MAX_PER_RUN``     — per-analyst-run cap (default 10).
* ``SCORES24_DISCARDED_MAX_PER_DAY``     — global daily cap (default 40).
* ``SCORES24_PREMIUM_ENABLED``           — allow web_unlocker premium (default true).
* ``SCORES24_USE_BROWSER_API``           — opt-in heavy browser API (default false).

The browser-API toggle is honoured today as a no-op marker; the heavy
fetch is wired in the scraper layer, not here.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger("football_discarded_scores24_review")

ENGINE_VERSION = "football_discarded_scores24_review.v1"

# Mongo collection names.
COLL_CACHE = "scores24_discarded_review_cache"
COLL_QUOTA = "scores24_discarded_quota"

# Decision constants.
DECISION_CONFIRM_DISCARD       = "CONFIRM_DISCARD"
DECISION_MOVE_TO_WATCHLIST     = "MOVE_TO_WATCHLIST"
DECISION_RESCUE_ALT_MARKET     = "RESCUE_ALTERNATIVE_MARKET"

# Reason codes.
RC_USED                        = "DISCARDED_MATCH_SCORES24_REVIEW_USED"
RC_NO_ACTIONABLE_CTX           = "SCORES24_NO_ACTIONABLE_CONTEXT"
RC_DISCARD_CONFIRMED           = "DISCARD_CONFIRMED_EXTERNAL_CONTEXT"
RC_INTERESTING_NOT_ENOUGH      = "SCORES24_CONTEXT_INTERESTING_BUT_NOT_ENOUGH_EDGE"
RC_RESCUED_CORNERS             = "DISCARDED_MATCH_RESCUED_AS_CORNERS_WATCHLIST"
RC_CORNERS_CTX_FOUND           = "SCORES24_CORNERS_CONTEXT_FOUND"
RC_EDITORIAL_CTX_FOUND         = "SCORES24_EDITORIAL_CONTEXT_FOUND"
RC_URL_NOT_RESOLVED            = "SCORES24_URL_NOT_RESOLVED"
RC_DISABLED_BY_ENV             = "SCORES24_REVIEW_DISABLED_BY_ENV"
RC_QUOTA_DAILY_EXCEEDED        = "SCORES24_QUOTA_DAILY_EXCEEDED"
RC_QUOTA_RUN_EXCEEDED          = "SCORES24_QUOTA_RUN_EXCEEDED"
RC_FROM_CACHE                  = "SCORES24_REVIEW_FROM_CACHE"
RC_FETCH_FAILED                = "SCORES24_REVIEW_FETCH_FAILED"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: Any) -> str:
    """Lowercase, ASCII-fold, hyphenate. Empty string on falsy input."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # NFKD fold + drop combining marks (José → jose).
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    # Replace anything non-alphanumeric with a hyphen, then collapse.
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def _date_candidates(match: dict) -> list[str]:
    """Return [DD-MM-YYYY] candidates derived from match metadata."""
    out: list[str] = []
    raw = (match.get("match_date") or match.get("date")
           or match.get("kickoff") or match.get("commence_time"))
    if raw is None:
        return out
    if isinstance(raw, (int, float)):
        # Unix seconds or millis.
        ts = raw / 1000.0 if raw > 1e12 else float(raw)
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            out.append(dt.strftime("%d-%m-%Y"))
        except (ValueError, OverflowError):
            pass
        return out
    s = str(raw).strip()
    if not s:
        return out
    # ISO 8601 yyyy-mm-dd... → DD-MM-YYYY.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        out.append(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
    # DD/MM/YYYY or DD-MM-YYYY already.
    m = re.match(r"^(\d{2})[\/\-](\d{2})[\/\-](\d{4})", s)
    if m:
        out.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    # De-dupe preserving order.
    seen: set[str] = set()
    return [d for d in out if not (d in seen or seen.add(d))]


def _team_name(side: Any) -> Optional[str]:
    if isinstance(side, dict):
        return side.get("name") or side.get("team_name") or side.get("short_name")
    if isinstance(side, str):
        return side
    return None


# ─────────────────────────────────────────────────────────────────────
# Slug candidates
# ─────────────────────────────────────────────────────────────────────
def build_scores24_slug_candidates(match_payload: dict) -> list[str]:
    """Build deterministic Scores24 URL candidates for a soccer match.

    Format observed::

        https://scores24.live/es/soccer/m-DD-MM-YYYY-home-away-prediction

    We emit a small ordered list (home-vs-away first, then away-vs-home
    swap, then with/without short-form team names). The caller iterates
    until one returns a non-empty scrape — but for cost reasons we
    typically attempt only the first candidate.
    """
    if not isinstance(match_payload, dict):
        return []

    # Honour an explicit URL if present (highest priority).
    explicit = (match_payload.get("scores24_url")
                or (match_payload.get("external_urls") or {}).get("scores24")
                or (match_payload.get("links") or {}).get("scores24"))
    if isinstance(explicit, str) and explicit.strip().startswith("http"):
        return [explicit.strip()]

    home_raw = _team_name(match_payload.get("home_team")) or match_payload.get("home_team_name") or match_payload.get("home")
    away_raw = _team_name(match_payload.get("away_team")) or match_payload.get("away_team_name") or match_payload.get("away")
    home = _slugify(home_raw)
    away = _slugify(away_raw)
    if not home or not away:
        return []

    dates = _date_candidates(match_payload)
    if not dates:
        return []

    base = "https://scores24.live/es/soccer"
    out: list[str] = []
    for d in dates:
        out.append(f"{base}/m-{d}-{home}-{away}-prediction")
        # Swap is sometimes how Scores24 stores fixtures (away first).
        out.append(f"{base}/m-{d}-{away}-{home}-prediction")
    # De-dupe preserving order.
    seen: set[str] = set()
    return [u for u in out if not (u in seen or seen.add(u))]


# ─────────────────────────────────────────────────────────────────────
# Cache + quota (Mongo helpers, all best-effort sync wrappers)
# ─────────────────────────────────────────────────────────────────────
def _cache_key(match: dict) -> str:
    mid = match.get("match_id") or match.get("id")
    if mid:
        return f"scores24_discarded:{mid}"
    home = _slugify(_team_name(match.get("home_team")) or match.get("home_team_name"))
    away = _slugify(_team_name(match.get("away_team")) or match.get("away_team_name"))
    date = "-".join(_date_candidates(match)[:1]) or "nodate"
    return f"scores24_discarded:{date}:{home}:{away}"


def _ttl_seconds_for_match(match: dict) -> int:
    """Pregame 12h / live 15min / postgame 24h."""
    status = str(match.get("status") or match.get("match_status") or "").lower()
    if any(kw in status for kw in ("live", "in_play", "1h", "2h", "ht")):
        return 15 * 60
    if any(kw in status for kw in ("ft", "final", "finished", "ended", "postgame", "fulltime")):
        return 24 * 3600
    return 12 * 3600


async def _cache_get(db, key: str) -> Optional[dict]:
    if db is None:
        return None
    try:
        doc = await db[COLL_CACHE].find_one({"_id": key})
    except Exception as exc:  # noqa: BLE001
        log.debug("scores24_review cache_get failed: %s", exc)
        return None
    if not doc:
        return None
    exp = doc.get("expires_at")
    if isinstance(exp, datetime):
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < _now_utc():
            return None
    payload = doc.get("payload")
    return payload if isinstance(payload, dict) else None


async def _cache_set(db, key: str, payload: dict, ttl_seconds: int) -> None:
    if db is None:
        return
    try:
        expires_at = _now_utc() + timedelta(seconds=int(ttl_seconds))
        await db[COLL_CACHE].update_one(
            {"_id": key},
            {"$set": {
                "_id":        key,
                "payload":    payload,
                "expires_at": expires_at,
                "updated_at": _now_utc(),
            }},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("scores24_review cache_set failed: %s", exc)


async def _quota_check_and_increment(db, max_per_day: int) -> tuple[bool, int]:
    """Atomically increment today's quota counter. Returns (allowed, count)."""
    if db is None:
        return True, 0  # No db → no global quota enforcement.
    day_key = _now_utc().strftime("%Y-%m-%d")
    doc_id = f"scores24_discarded_quota:{day_key}"
    try:
        # Mongo $inc is atomic. We read after the increment to compare.
        doc = await db[COLL_QUOTA].find_one_and_update(
            {"_id": doc_id},
            {"$inc": {"count": 1}, "$set": {"updated_at": _now_utc(), "day": day_key}},
            upsert=True,
            return_document=True,  # ReturnDocument.AFTER
        )
        # motor returns the dict directly; with return_document=True (truthy)
        # we get the post-image. Some drivers default to BEFORE — handle both.
        count = (doc or {}).get("count") or 1
        if count > max_per_day:
            # Roll back (best-effort): don't fail the call if rollback fails.
            try:
                await db[COLL_QUOTA].update_one(
                    {"_id": doc_id}, {"$inc": {"count": -1}},
                )
            except Exception:  # noqa: BLE001
                pass
            return False, max_per_day
        return True, count
    except Exception as exc:  # noqa: BLE001
        log.debug("quota counter failed (fail-open): %s", exc)
        return True, 0


# ─────────────────────────────────────────────────────────────────────
# Decision logic
# ─────────────────────────────────────────────────────────────────────
def _build_corners_prediction(scrape: dict) -> dict:
    consensus = scrape.get("consensus") or {}
    sections  = scrape.get("sections") or []
    # Prefer the corners_prediction section if present.
    for s in sections:
        if s.get("section") == "corners_prediction" and s.get("recommended_market"):
            return {
                "available":  True,
                "side":       s.get("side"),
                "line":       s.get("line"),
                "odds":       s.get("odds"),
                "label":      s.get("recommended_market"),
            }
    # Fall back to consensus if it is a corners total.
    if consensus.get("primary_market_type") == "corners_total":
        return {
            "available":  True,
            "side":       consensus.get("primary_side"),
            "line":       consensus.get("primary_line"),
            "odds":       consensus.get("primary_odds"),
            "label":      consensus.get("primary_market"),
        }
    return {"available": False}


def _build_editorial_prediction(scrape: dict) -> dict:
    sections  = scrape.get("sections") or []
    consensus = scrape.get("consensus") or {}
    # Prefer prediccion_redaccion / apuesta_fiable explicitly.
    for sect in ("prediccion_redaccion", "apuesta_fiable"):
        for s in sections:
            if s.get("section") == sect and s.get("recommended_market"):
                return {
                    "available":       True,
                    "market":          s.get("recommended_market"),
                    "market_type":     s.get("market_type"),
                    "side":            s.get("side"),
                    "line":            s.get("line"),
                    "odds":            s.get("odds"),
                    "narrative_es":    (s.get("narrative_context") or "")[:280] or None,
                }
    if consensus.get("primary_market") and consensus.get("primary_market_type") != "corners_total":
        return {
            "available":   True,
            "market":      consensus.get("primary_market"),
            "market_type": consensus.get("primary_market_type"),
            "side":        consensus.get("primary_side"),
            "line":        consensus.get("primary_line"),
            "odds":        consensus.get("primary_odds"),
        }
    return {"available": False}


def _decide(
    scrape: dict,
    corners_pred: dict,
    editorial_pred: dict,
    discard_reason: str,
) -> tuple[str, dict, list[str]]:
    """Translate the scrape into a decision tuple.

    Returns (decision, rescued_market_dict, reason_codes).
    """
    reasons: list[str] = [RC_USED]

    if not scrape.get("available"):
        return DECISION_CONFIRM_DISCARD, {}, reasons + [
            RC_NO_ACTIONABLE_CTX, RC_DISCARD_CONFIRMED,
        ]

    # Rescue path — corners typically have the most edge in discarded
    # matches because the main-market engine wasn't even looking there.
    if corners_pred.get("available") and corners_pred.get("side") and corners_pred.get("line") is not None:
        reasons.append(RC_CORNERS_CTX_FOUND)
        reasons.append(RC_RESCUED_CORNERS)
        rescued = {
            "market_family": "CORNERS",
            "market":        corners_pred.get("label") or f"{corners_pred['side'].title()} {corners_pred['line']} corners",
            "side":          corners_pred["side"],
            "line":          corners_pred["line"],
            "odds":          corners_pred.get("odds"),
        }
        return DECISION_RESCUE_ALT_MARKET, rescued, reasons

    # Watchlist path — there is editorial context but no corners pick.
    if editorial_pred.get("available"):
        reasons.append(RC_EDITORIAL_CTX_FOUND)
        reasons.append(RC_INTERESTING_NOT_ENOUGH)
        return DECISION_MOVE_TO_WATCHLIST, {}, reasons

    # No actionable info — confirm the discard.
    return DECISION_CONFIRM_DISCARD, {}, reasons + [
        RC_NO_ACTIONABLE_CTX, RC_DISCARD_CONFIRMED,
    ]


def _empty_review(reason_code: str, *, extra_codes: Iterable[str] = ()) -> dict:
    return {
        "available":             False,
        "engine_version":        ENGINE_VERSION,
        "source":                "scores24",
        "review_type":           "DISCARDED_MATCH_EXTERNAL_REVIEW",
        "external_context_found": False,
        "decision":              DECISION_CONFIRM_DISCARD,
        "rescued_market":        None,
        "editorial_prediction":  {"available": False},
        "corners_prediction":    {"available": False},
        "reason_codes":          [reason_code, *extra_codes],
    }


# ─────────────────────────────────────────────────────────────────────
# Per-run quota helper (in-memory; shared across coroutines in the run).
# ─────────────────────────────────────────────────────────────────────
class _RunCounter:
    """Tiny mutable counter object the caller can pass in to enforce
    ``SCORES24_DISCARDED_MAX_PER_RUN`` across many matches in the same
    analyst run without touching the database.
    """
    __slots__ = ("count", "limit")

    def __init__(self, limit: int):
        self.count = 0
        self.limit = max(0, int(limit))

    def try_consume(self) -> bool:
        if self.count >= self.limit:
            return False
        self.count += 1
        return True


def make_run_counter(limit: Optional[int] = None) -> _RunCounter:
    """Public factory used by the analyst pipeline."""
    if limit is None:
        limit = _env_int("SCORES24_DISCARDED_MAX_PER_RUN", 10)
    return _RunCounter(limit)


# ─────────────────────────────────────────────────────────────────────
# Public entry — async
# ─────────────────────────────────────────────────────────────────────
async def review_discarded_match_with_scores24(
    match_payload: dict | None,
    *,
    db=None,
    force: bool = False,
    run_counter: Optional[_RunCounter] = None,
    discard_reason: str = "edge_insufficient",
    scrape_fn=None,
) -> dict:
    """Run the external review for one discarded match.

    Parameters
    ----------
    match_payload
        The discarded match dict. NOT mutated by this function.
    db
        Optional Mongo handle (motor). When ``None``, cache + global
        daily quota are skipped (still safe / fail-soft).
    force
        Bypass the cache (still respects daily quota unless quota is
        disabled).
    run_counter
        Per-run counter from :func:`make_run_counter` for the analyst
        pipeline. Ignored when ``None``.
    discard_reason
        The bucket the match was discarded from (used in the audit).
    scrape_fn
        Injectable async scraper. Defaults to
        :func:`services.scores24_scraper.scrape_scores24_match`. The
        signature is ``await scrape_fn(url=...) -> dict``.

    Returns
    -------
    dict — the review payload (also safe to store anywhere). Never raises.
    """
    # Master kill-switch.
    if not _env_bool("SCORES24_DISCARDED_REVIEW_ENABLED", True):
        return _empty_review(RC_DISABLED_BY_ENV)

    if not isinstance(match_payload, dict):
        return _empty_review(RC_NO_ACTIONABLE_CTX)

    # Per-run gate (in-memory).
    if run_counter is not None and not run_counter.try_consume():
        return _empty_review(RC_QUOTA_RUN_EXCEEDED)

    # Cache lookup (unless force).
    key = _cache_key(match_payload)
    if not force:
        cached = await _cache_get(db, key)
        if cached is not None:
            cached = dict(cached)  # don't share refs.
            rcs = list(cached.get("reason_codes") or [])
            if RC_FROM_CACHE not in rcs:
                rcs.append(RC_FROM_CACHE)
            cached["reason_codes"] = rcs
            return cached

    # Daily quota gate (Mongo).
    max_day = _env_int("SCORES24_DISCARDED_MAX_PER_DAY", 40)
    allowed, _count = await _quota_check_and_increment(db, max_day)
    if not allowed:
        return _empty_review(RC_QUOTA_DAILY_EXCEEDED)

    # Resolve URL candidates.
    candidates = build_scores24_slug_candidates(match_payload)
    if not candidates:
        out = _empty_review(RC_URL_NOT_RESOLVED)
        await _cache_set(db, key, out, _ttl_seconds_for_match(match_payload))
        return out

    # Scrape (only the first candidate to keep costs predictable).
    if scrape_fn is None:
        try:
            from services.scores24_scraper import scrape_scores24_match as scrape_fn  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001
            log.debug("scrape_scores24_match unavailable: %s", exc)
            scrape_fn = None  # type: ignore[assignment]
    target_url = candidates[0]
    scrape: dict = {"available": False}
    if scrape_fn is not None:
        try:
            scrape = await scrape_fn(url=target_url)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            log.debug("scores24 scrape raised: %s", exc)
            scrape = {"available": False, "reason_codes": [RC_FETCH_FAILED]}

    if not isinstance(scrape, dict) or not scrape.get("available"):
        out = _empty_review(RC_FETCH_FAILED)
        out["url_tried"] = target_url
        await _cache_set(db, key, out, _ttl_seconds_for_match(match_payload))
        return out

    corners_pred   = _build_corners_prediction(scrape)
    editorial_pred = _build_editorial_prediction(scrape)
    decision, rescued, rcodes = _decide(scrape, corners_pred, editorial_pred, discard_reason)

    out = {
        "available":               True,
        "engine_version":          ENGINE_VERSION,
        "source":                  "scores24",
        "review_type":             "DISCARDED_MATCH_EXTERNAL_REVIEW",
        "original_discard_reason": discard_reason,
        "external_context_found":  bool(corners_pred.get("available") or editorial_pred.get("available")),
        "decision":                decision,
        "rescued_market":          rescued or None,
        "editorial_prediction":    editorial_pred,
        "corners_prediction":      corners_pred,
        "url_used":                target_url,
        "reason_codes":            rcodes,
    }
    await _cache_set(db, key, out, _ttl_seconds_for_match(match_payload))
    return out


__all__ = [
    "ENGINE_VERSION",
    "DECISION_CONFIRM_DISCARD",
    "DECISION_MOVE_TO_WATCHLIST",
    "DECISION_RESCUE_ALT_MARKET",
    "RC_USED", "RC_NO_ACTIONABLE_CTX", "RC_DISCARD_CONFIRMED",
    "RC_INTERESTING_NOT_ENOUGH", "RC_RESCUED_CORNERS",
    "RC_CORNERS_CTX_FOUND", "RC_EDITORIAL_CTX_FOUND",
    "RC_URL_NOT_RESOLVED", "RC_DISABLED_BY_ENV",
    "RC_QUOTA_DAILY_EXCEEDED", "RC_QUOTA_RUN_EXCEEDED",
    "RC_FROM_CACHE", "RC_FETCH_FAILED",
    "build_scores24_slug_candidates",
    "make_run_counter",
    "review_discarded_match_with_scores24",
]
