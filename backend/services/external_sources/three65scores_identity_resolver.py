"""Sprint F.1 — 365Scores **Match Identity Resolver**.

Provides a single, observable entry point that resolves the 365Scores
``game_id`` + ``team_id``s for a canonical internal match. Used as the
prerequisite for the Top Trends ingestion (Sprint F.2) and as a clean
backfill source for the legacy cascade in
:mod:`score365_scrapedo_client`.

Why a dedicated module?
-----------------------
The legacy ``resolve_game_id_by_date_and_names`` returns just the
``game_id`` string and assumes the slug order ``home-away`` is correct.
The contract for Sprint F requires:

* Per-team validation: every ``team_id`` returned by 365Scores **must**
  match the canonical home/away team name (with alias normalisation);
  otherwise the resolution is flagged ``INVALID_TEAM_MAPPING``.
* Explicit status machine: ``RESOLVED`` / ``AMBIGUOUS`` /
  ``NOT_FOUND`` / ``SOURCE_UNAVAILABLE`` / ``INVALID_TEAM_MAPPING``.
* Confidence scoring (``HIGH`` / ``MEDIUM`` / ``LOW``).
* Mongo persistence in ``football_365scores_identities`` with unique
  indexes on ``internal_match_id`` and ``game_id`` so we never
  re-resolve once stable.
* Fail-soft transport: source outages return ``SOURCE_UNAVAILABLE``
  instead of raising.

Public API
----------
:func:`resolve_match_identity`
    Resolve one canonical match. Returns a structured dict; never raises.
:func:`normalize_team_name`
    Strip accents, lowercase, drop ``"national football team"`` /
    ``"fc"`` suffixes. Used by tests and by callers needing the same
    canonical form.
:func:`build_team_alias_set`
    Return the full alias bucket for a team name (e.g. ``"Mexico"``
    and ``"México"`` end up in the same set).
:func:`validate_team_mapping`
    Pure check: given canonical home/away and 365Scores competitors,
    decide whether they are aligned, swapped or mismatched.
:func:`ensure_indexes`
    Create the Mongo indexes for ``football_365scores_identities``.

All fixture IDs (``5106``/``2383``/``4627854``/``5930``) live in tests
or in the docstring only — they must **never** appear in production
control flow.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Iterable, Optional

from .score365_scrapedo_client import extract_365scores_ids

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────
SOURCE_LABEL = "365scores"
MONGO_COLLECTION = "football_365scores_identities"

# Status machine.
STATUS_RESOLVED              = "RESOLVED"
STATUS_AMBIGUOUS             = "AMBIGUOUS"
STATUS_NOT_FOUND             = "NOT_FOUND"
STATUS_SOURCE_UNAVAILABLE    = "SOURCE_UNAVAILABLE"
STATUS_INVALID_TEAM_MAPPING  = "INVALID_TEAM_MAPPING"

# Confidence scale.
CONFIDENCE_HIGH    = "HIGH"
CONFIDENCE_MEDIUM  = "MEDIUM"
CONFIDENCE_LOW     = "LOW"

# Reason codes.
RC_FROM_MONGO_CACHE      = "F1_FROM_MONGO_CACHE"
RC_FROM_URL              = "F1_FROM_URL"
RC_FROM_SEARCH           = "F1_FROM_SEARCH_BY_CONTEXT"
RC_NO_INPUTS             = "F1_MISSING_REQUIRED_INPUTS"
RC_NO_CANDIDATES         = "F1_NO_CANDIDATES_FROM_SOURCE"
RC_MULTIPLE_CANDIDATES   = "F1_MULTIPLE_CANDIDATES"
RC_SOURCE_TIMEOUT        = "F1_SOURCE_TIMEOUT_OR_ERROR"
RC_TEAM_MAPPING_OK       = "F1_TEAM_MAPPING_VALIDATED"
RC_TEAM_MAPPING_SWAPPED  = "F1_TEAM_MAPPING_SWAPPED"
RC_TEAM_MAPPING_INVALID  = "F1_TEAM_MAPPING_NEITHER_SIDE_MATCH"

# Default tolerance for kickoff window (Sprint F spec: ±6h).
DEFAULT_TOLERANCE_HOURS = 6.0


# ─────────────────────────────────────────────────────────────────────
# Alias map (extended)
# ─────────────────────────────────────────────────────────────────────
# Each key is a canonical bucket name. The matcher considers two team
# names equivalent if they fall in the same bucket (or if one is the
# canonical key of the other's bucket).
_TEAM_ALIASES: dict[str, set[str]] = {
    "mexico": {
        "mexico", "méxico", "mex", "selección de méxico",
        "mexico national football team",
    },
    "south korea": {
        "south korea", "korea republic", "korea south",
        "republic of korea", "corea del sur", "corea republica",
        "korea",
    },
    "north korea": {
        "north korea", "korea dpr", "dpr korea",
        "korea democratic peoples republic", "corea del norte",
    },
    "congo dr": {
        "congo dr", "dr congo", "rd congo", "republica democratica del congo",
        "democratic republic of the congo", "dem rep congo",
        "congo democratic republic", "rdc", "drc",
    },
    "congo": {
        "congo", "republic of the congo", "congo republic", "rep congo",
    },
    "ivory coast": {
        "ivory coast", "cote d ivoire", "côte d'ivoire", "cote d'ivoire",
        "costa de marfil", "civ",
    },
    "usa": {
        "usa", "united states", "united states of america", "eeuu",
        "estados unidos", "usmnt", "us",
    },
    "north macedonia": {
        "north macedonia", "macedonia", "macedonia del norte", "mkd",
        "fyr macedonia",
    },
    "bosnia": {
        "bosnia", "bosnia and herzegovina", "bosnia & herzegovina",
        "bosnia-herzegovina", "bosnia y herzegovina", "bih",
    },
    "iran": {
        "iran", "ir iran", "ir. iran", "islamic republic of iran",
        "iran islamic republic",
    },
    "uae": {
        "uae", "united arab emirates", "emiratos arabes unidos",
        "emirates",
    },
    "saudi arabia": {
        "saudi arabia", "ksa", "arabia saudita", "arabia saudi",
    },
    "cape verde": {
        "cape verde", "cabo verde", "cv",
    },
    "czech republic": {
        "czech republic", "czechia", "republica checa", "chequia",
    },
    "turkey": {
        "turkey", "türkiye", "turquia", "tur",
    },
}


def _strip_accents_lower(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    nf = unicodedata.normalize("NFD", s)
    out = "".join(c for c in nf if unicodedata.category(c) != "Mn").lower().strip()
    out = re.sub(
        r"\b(national football team|national team|seleccion|selección|fc|football club|cf)\b",
        "", out,
    ).strip()
    out = re.sub(r"\s+", " ", out)
    return out


def normalize_team_name(name: Any) -> str:
    """Public canonical form used by tests + callers.

    Lowercased, accent-stripped, common suffixes removed.
    """
    return _strip_accents_lower(name)


def build_team_alias_set(name: Any) -> set[str]:
    """Return every alias known for ``name`` (always includes the
    normalised version of ``name`` itself).
    """
    norm = _strip_accents_lower(name)
    result: set[str] = {norm} if norm else set()
    for canon, aliases in _TEAM_ALIASES.items():
        normalised_aliases = {_strip_accents_lower(a) for a in aliases}
        if norm and (norm == canon or norm in normalised_aliases):
            result.add(canon)
            result.update(normalised_aliases)
    return result


def _names_match(a: Any, b: Any) -> bool:
    """True iff ``a`` and ``b`` share at least one alias bucket."""
    set_a = build_team_alias_set(a)
    set_b = build_team_alias_set(b)
    if not set_a or not set_b:
        return False
    return bool(set_a & set_b)


# ─────────────────────────────────────────────────────────────────────
# Team mapping validation
# ─────────────────────────────────────────────────────────────────────
def validate_team_mapping(
    *,
    canonical_home: str,
    canonical_away: str,
    source_home_name: Optional[str],
    source_away_name: Optional[str],
) -> dict:
    """Decide whether the names returned by 365Scores align with the
    canonical home/away. Returns a dict::

        {
          "aligned":   bool,           # source order == canonical order
          "swapped":   bool,           # source order is inverted
          "valid":     bool,           # aligned OR swapped
          "reason":    str,
        }

    Pure function — used by both the URL path and the search path.
    """
    if not canonical_home or not canonical_away:
        return {
            "aligned": False, "swapped": False, "valid": False,
            "reason": RC_TEAM_MAPPING_INVALID,
        }
    src_h_ok_for_home = _names_match(canonical_home, source_home_name)
    src_a_ok_for_away = _names_match(canonical_away, source_away_name)
    src_h_ok_for_away = _names_match(canonical_away, source_home_name)
    src_a_ok_for_home = _names_match(canonical_home, source_away_name)

    if src_h_ok_for_home and src_a_ok_for_away:
        return {"aligned": True, "swapped": False, "valid": True,
                "reason": RC_TEAM_MAPPING_OK}
    if src_h_ok_for_away and src_a_ok_for_home:
        return {"aligned": False, "swapped": True, "valid": True,
                "reason": RC_TEAM_MAPPING_SWAPPED}
    return {"aligned": False, "swapped": False, "valid": False,
            "reason": RC_TEAM_MAPPING_INVALID}


# ─────────────────────────────────────────────────────────────────────
# Helpers — game extraction
# ─────────────────────────────────────────────────────────────────────
def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parser. Returns timezone-aware UTC ``datetime`` or ``None``."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None


def _extract_competitors(game: dict) -> tuple[Optional[dict], Optional[dict]]:
    """Pull ``(home_dict, away_dict)`` from a 365Scores game dict.

    365Scores sometimes uses ``isHome`` flag, sometimes uses positional
    order ``competitors[0]=home``. We honour the explicit flag first.
    """
    if not isinstance(game, dict):
        return None, None
    comps = game.get("competitors") or game.get("teams")
    if not isinstance(comps, list) or len(comps) < 2:
        return None, None
    home_doc = away_doc = None
    for c in comps:
        if not isinstance(c, dict):
            continue
        flag = c.get("isHome")
        side = (c.get("side") or "").lower()
        if flag is True or side in ("home", "h"):
            home_doc = home_doc or c
        elif flag is False or side in ("away", "a"):
            away_doc = away_doc or c
    if home_doc is None and away_doc is None:
        # Positional fallback.
        home_doc, away_doc = comps[0], comps[1]
        if not isinstance(home_doc, dict):
            home_doc = None
        if not isinstance(away_doc, dict):
            away_doc = None
    elif home_doc is None or away_doc is None:
        # One side identified via flag, the other by elimination.
        for c in comps:
            if c is home_doc or c is away_doc:
                continue
            if isinstance(c, dict):
                if home_doc is None:
                    home_doc = c
                elif away_doc is None:
                    away_doc = c
                    break
    return home_doc, away_doc


def _extract_kickoff(game: dict) -> Optional[datetime]:
    """Best-effort kickoff extraction for a 365Scores game dict."""
    if not isinstance(game, dict):
        return None
    for key in (
        "startTime", "start_time", "startTimeUtc", "startDate",
        "gameTime", "gameStartTime", "kickoffTime", "kickoff",
    ):
        dt = _parse_dt(game.get(key))
        if dt is not None:
            return dt
    return None


def _is_within_tolerance(
    *, candidate: datetime, target: datetime, tolerance_hours: float,
) -> bool:
    delta = abs((candidate - target).total_seconds())
    return delta <= tolerance_hours * 3600.0


# ─────────────────────────────────────────────────────────────────────
# Confidence
# ─────────────────────────────────────────────────────────────────────
def _confidence_for(
    *, resolved_from: str, mapping_valid: bool, mapping_aligned: bool,
    competition_matches: Optional[bool], kickoff_delta_seconds: Optional[float],
) -> str:
    """Heuristic confidence."""
    if not mapping_valid:
        return CONFIDENCE_LOW
    if resolved_from == "mongo_cache":
        return CONFIDENCE_HIGH
    if resolved_from == "url" and mapping_aligned:
        return CONFIDENCE_HIGH
    if resolved_from == "url" and mapping_valid:
        return CONFIDENCE_MEDIUM
    # search path
    base = CONFIDENCE_MEDIUM
    if competition_matches and kickoff_delta_seconds is not None and kickoff_delta_seconds <= 3600:
        base = CONFIDENCE_HIGH
    if kickoff_delta_seconds is not None and kickoff_delta_seconds > 5 * 3600:
        base = CONFIDENCE_LOW
    return base


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Mongo helpers
# ─────────────────────────────────────────────────────────────────────
async def ensure_indexes(db: Any) -> dict:
    """Create the indexes for ``football_365scores_identities``.

    Returns a dict with the names of the indexes created (or empty when
    ``db`` is ``None``). Fail-soft: never raises.
    """
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
        await coll.create_index("internal_match_id", unique=True,
                                 name="ix_internal_match_id")
        created.append("ix_internal_match_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_identity] index ix_internal_match_id failed: %s", exc)
    try:
        await coll.create_index(
            "game_id", unique=True, name="ix_game_id",
            partialFilterExpression={"game_id": {"$gt": 0}},
        )
        created.append("ix_game_id")
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_identity] index ix_game_id failed: %s", exc)
    try:
        await coll.create_index(
            [("home_team_id", 1), ("away_team_id", 1), ("commence_time", 1)],
            name="ix_teams_commence",
        )
        created.append("ix_teams_commence")
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_identity] index ix_teams_commence failed: %s", exc)
    return {"created": created}


async def _find_cached_identity(
    *, db: Any, internal_match_id: str,
) -> Optional[dict]:
    if db is None or not internal_match_id:
        return None
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return None
    try:
        doc = await coll.find_one({"internal_match_id": internal_match_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_identity] mongo lookup failed: %s", exc)
        return None
    return doc


async def _persist_identity(*, db: Any, doc: dict) -> bool:
    if db is None:
        return False
    coll = getattr(db, MONGO_COLLECTION, None)
    if coll is None:
        try:
            coll = db[MONGO_COLLECTION]
        except Exception:  # noqa: BLE001
            return False
    payload = dict(doc)
    payload["last_verified_at"] = _now_iso()
    try:
        await coll.update_one(
            {"internal_match_id": payload["internal_match_id"]},
            {"$set": payload},
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[365scores_identity] persist failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────
# Core search helpers
# ─────────────────────────────────────────────────────────────────────
def _select_candidates(
    *,
    games: Iterable[dict],
    canonical_home: str,
    canonical_away: str,
    commence_time: datetime,
    tolerance_hours: float,
    competition_id: Optional[int],
) -> list[dict]:
    """Filter the games list down to plausible matches.

    Pure function — no I/O. Each candidate dict carries ``score``,
    ``kickoff_delta_seconds`` and ``competition_matches`` to ease the
    final selection step.
    """
    candidates: list[dict] = []
    for game in games or []:
        if not isinstance(game, dict):
            continue
        h_doc, a_doc = _extract_competitors(game)
        if h_doc is None and a_doc is None:
            continue
        h_name = (h_doc or {}).get("name") or (h_doc or {}).get("symbolicName")
        a_name = (a_doc or {}).get("name") or (a_doc or {}).get("symbolicName")
        # Names must match either side (orientation-agnostic; mapping
        # validation later disambiguates).
        canon_set_home = build_team_alias_set(canonical_home)
        canon_set_away = build_team_alias_set(canonical_away)
        h_norm = _strip_accents_lower(h_name)
        a_norm = _strip_accents_lower(a_name)
        h_in_home = bool(canon_set_home) and h_norm in canon_set_home
        a_in_away = bool(canon_set_away) and a_norm in canon_set_away
        h_in_away = bool(canon_set_away) and h_norm in canon_set_away
        a_in_home = bool(canon_set_home) and a_norm in canon_set_home
        both_aligned = h_in_home and a_in_away
        both_swapped = h_in_away and a_in_home
        if not (both_aligned or both_swapped):
            continue
        kickoff = _extract_kickoff(game)
        delta_s: Optional[float] = None
        if kickoff is not None:
            if not _is_within_tolerance(
                candidate=kickoff, target=commence_time,
                tolerance_hours=tolerance_hours,
            ):
                continue
            delta_s = abs((kickoff - commence_time).total_seconds())
        comp_id_game = _safe_int(
            game.get("competitionId")
            or (game.get("competition") or {}).get("id")
            or game.get("compId"),
        )
        comp_match = (
            None if competition_id is None
            else (comp_id_game == competition_id)
        )
        # Hard guard: when competition_id is provided we MUST match.
        if competition_id is not None and comp_id_game is not None and comp_id_game != competition_id:
            continue
        candidates.append({
            "game":                    game,
            "home_doc":                h_doc,
            "away_doc":                a_doc,
            "kickoff":                 kickoff,
            "kickoff_delta_seconds":   delta_s,
            "both_aligned":            both_aligned,
            "both_swapped":            both_swapped,
            "competition_id":          comp_id_game,
            "competition_matches":     comp_match,
        })
    # De-duplicate by 365Scores game_id (the same game can appear in
    # multiple days when we query day-1 / day / day+1).
    seen_ids: set[int] = set()
    unique_candidates: list[dict] = []
    for c in candidates:
        gid = _safe_int(
            (c.get("game") or {}).get("id")
            or (c.get("game") or {}).get("gameId"),
        )
        if gid is None:
            unique_candidates.append(c)
            continue
        if gid in seen_ids:
            continue
        seen_ids.add(gid)
        unique_candidates.append(c)
    # Sort: prefer competition match, then smallest kickoff delta.
    unique_candidates.sort(key=lambda c: (
        0 if c.get("competition_matches") else (1 if c.get("competition_matches") is False else 2),
        c.get("kickoff_delta_seconds") if c.get("kickoff_delta_seconds") is not None else 10**9,
    ))
    return unique_candidates


def _build_identity_doc(
    *,
    internal_match_id: str,
    canonical_home: str,
    canonical_away: str,
    commence_time: datetime,
    competition: Optional[str],
    candidate: dict,
    resolved_from: str,
    source_url: Optional[str],
    aliases_used: list[str],
    mapping: dict,
) -> dict:
    h_doc = candidate.get("home_doc") or {}
    a_doc = candidate.get("away_doc") or {}
    game = candidate.get("game") or {}
    # If 365Scores returned the order swapped, align IDs back to our
    # canonical home/away. We always persist canonical → source mapping.
    if mapping.get("swapped"):
        h_doc, a_doc = a_doc, h_doc
    home_team_id = _safe_int(h_doc.get("id") or h_doc.get("competitorId"))
    away_team_id = _safe_int(a_doc.get("id") or a_doc.get("competitorId"))
    confidence = _confidence_for(
        resolved_from=resolved_from,
        mapping_valid=mapping.get("valid", False),
        mapping_aligned=mapping.get("aligned", False),
        competition_matches=candidate.get("competition_matches"),
        kickoff_delta_seconds=candidate.get("kickoff_delta_seconds"),
    )
    return {
        "internal_match_id":        internal_match_id,
        "source":                   SOURCE_LABEL,
        "game_id":                  _safe_int(game.get("id") or game.get("gameId")),
        "competition_id":           candidate.get("competition_id"),
        "competition_label":        competition,
        "home_team": {
            "name":     canonical_home,
            "team_id":  home_team_id,
        },
        "away_team": {
            "name":     canonical_away,
            "team_id":  away_team_id,
        },
        "home_team_id":             home_team_id,
        "away_team_id":             away_team_id,
        "home_team_name_source":    h_doc.get("name") or h_doc.get("symbolicName"),
        "away_team_name_source":    a_doc.get("name") or a_doc.get("symbolicName"),
        "source_url":               source_url,
        "aliases_used":             aliases_used,
        "confidence":               confidence,
        "status":                   STATUS_RESOLVED,
        "resolved_from":            resolved_from,
        "resolved_at":              _now_iso(),
        "commence_time":            commence_time.isoformat() if commence_time else None,
        "kickoff_delta_seconds":    candidate.get("kickoff_delta_seconds"),
        "competition_matches":      candidate.get("competition_matches"),
        "mapping_reason":           mapping.get("reason"),
    }


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────
async def resolve_match_identity(
    *,
    internal_match_id: str,
    home_team: str,
    away_team: str,
    commence_time: datetime,
    competition: Optional[str] = None,
    competition_id: Optional[int] = None,
    match_url: Optional[str] = None,
    tolerance_hours: float = DEFAULT_TOLERANCE_HOURS,
    db: Any = None,
    persist: bool = True,
    games_fetcher: Optional[Callable[..., Awaitable[Any]]] = None,
    game_detail_fetcher: Optional[Callable[..., Awaitable[Any]]] = None,
    force_refresh: bool = False,
) -> dict:
    """Resolve the 365Scores identity for a canonical match.

    Parameters
    ----------
    internal_match_id:
        Our canonical match identifier (e.g. ``"football:wc2026:..."``).
    home_team, away_team:
        Canonical team names (any language; alias map normalises them).
    commence_time:
        Canonical kickoff (timezone-aware ``datetime``).
    competition:
        Human label (e.g. ``"FIFA World Cup"``). Used only for audit.
    competition_id:
        365Scores numeric competition id; when present, candidates with
        a *different* known ``competitionId`` are rejected.
    match_url:
        Known 365Scores match URL (optional). When provided, we use
        :func:`extract_365scores_ids` first and validate IDs via
        ``game_detail_fetcher``.
    tolerance_hours:
        Kickoff window for the context search (default ±6h).
    db:
        Mongo handle (any object exposing ``football_365scores_identities``).
    persist:
        When ``True`` and a ``RESOLVED`` outcome is reached, upserts into
        Mongo. Defaults to ``True``.
    games_fetcher:
        ``async fn(date_iso, sport_id=1) -> list[dict]``. Replaces the
        live HTTP call in tests. When ``None`` and ``match_url`` is
        missing, the resolver returns ``SOURCE_UNAVAILABLE``.
    game_detail_fetcher:
        ``async fn(game_id) -> dict``. Used to validate IDs parsed from
        ``match_url``. When ``None`` and a URL was provided, the URL
        path is skipped (we fall through to the search path).
    force_refresh:
        Skip the Mongo cache lookup. Default ``False``.

    Returns
    -------
    dict
        Always a dict (never raises). See module docstring for shape.
    """
    aliases_used: list[str] = []
    if not internal_match_id or not home_team or not away_team or commence_time is None:
        return {
            "status":             STATUS_NOT_FOUND,
            "confidence":         CONFIDENCE_LOW,
            "internal_match_id":  internal_match_id,
            "reason_code":        RC_NO_INPUTS,
            "aliases_used":       aliases_used,
            "candidates":         [],
            "resolved_at":        _now_iso(),
            "source":             SOURCE_LABEL,
        }

    # Normalise commence_time to UTC.
    if commence_time.tzinfo is None:
        commence_time = commence_time.replace(tzinfo=timezone.utc)
    else:
        commence_time = commence_time.astimezone(timezone.utc)

    canonical_home = home_team
    canonical_away = away_team
    aliases_used = sorted(
        build_team_alias_set(canonical_home) | build_team_alias_set(canonical_away)
    )

    # ── 0) Mongo cache ────────────────────────────────────────────────
    if not force_refresh:
        cached = await _find_cached_identity(
            db=db, internal_match_id=internal_match_id,
        )
        if cached and cached.get("status") == STATUS_RESOLVED and cached.get("game_id"):
            return {
                **cached,
                "reason_code":  RC_FROM_MONGO_CACHE,
                "candidates":   [],
                "aliases_used": aliases_used,
                "resolved_from": "mongo_cache",
                "confidence":   cached.get("confidence") or CONFIDENCE_HIGH,
            }

    # ── 1) URL path ───────────────────────────────────────────────────
    if match_url:
        ids = extract_365scores_ids({"match_url": match_url})
        url_game_id = _safe_int(ids.get("game_id"))
        if url_game_id and game_detail_fetcher is not None:
            try:
                game_doc = await game_detail_fetcher(str(url_game_id))
            except Exception as exc:  # noqa: BLE001
                log.info("[365scores_identity] detail fetcher raised: %s", exc)
                game_doc = None
            if isinstance(game_doc, dict):
                # 365Scores nests under "game" sometimes.
                game = (game_doc.get("game")
                        if isinstance(game_doc.get("game"), dict)
                        else game_doc)
                h_doc, a_doc = _extract_competitors(game)
                mapping = validate_team_mapping(
                    canonical_home=canonical_home,
                    canonical_away=canonical_away,
                    source_home_name=(h_doc or {}).get("name"),
                    source_away_name=(a_doc or {}).get("name"),
                )
                # Build a candidate-shaped dict so we reuse _build_identity_doc.
                cand = {
                    "game":                  game,
                    "home_doc":              h_doc,
                    "away_doc":              a_doc,
                    "kickoff":               _extract_kickoff(game),
                    "kickoff_delta_seconds": None,
                    "competition_id":        _safe_int(
                        game.get("competitionId")
                        or (game.get("competition") or {}).get("id"),
                    ),
                    "competition_matches":   (
                        None if competition_id is None
                        else (_safe_int(
                            game.get("competitionId")
                            or (game.get("competition") or {}).get("id"),
                        ) == competition_id)
                    ),
                }
                if not mapping["valid"]:
                    out = {
                        "status":             STATUS_INVALID_TEAM_MAPPING,
                        "confidence":         CONFIDENCE_LOW,
                        "internal_match_id":  internal_match_id,
                        "game_id":            url_game_id,
                        "source_url":         match_url,
                        "home_team_name_source": (h_doc or {}).get("name"),
                        "away_team_name_source": (a_doc or {}).get("name"),
                        "reason_code":        mapping["reason"],
                        "resolved_from":      "url",
                        "aliases_used":       aliases_used,
                        "candidates":         [],
                        "resolved_at":        _now_iso(),
                        "source":             SOURCE_LABEL,
                    }
                    return out
                identity = _build_identity_doc(
                    internal_match_id=internal_match_id,
                    canonical_home=canonical_home,
                    canonical_away=canonical_away,
                    commence_time=commence_time,
                    competition=competition,
                    candidate=cand,
                    resolved_from="url",
                    source_url=match_url,
                    aliases_used=aliases_used,
                    mapping=mapping,
                )
                identity["reason_code"] = RC_FROM_URL
                if persist:
                    await _persist_identity(db=db, doc=identity)
                return identity

    # ── 2) Search by context ──────────────────────────────────────────
    if games_fetcher is None:
        return {
            "status":             STATUS_SOURCE_UNAVAILABLE,
            "confidence":         CONFIDENCE_LOW,
            "internal_match_id":  internal_match_id,
            "reason_code":        RC_SOURCE_TIMEOUT,
            "aliases_used":       aliases_used,
            "candidates":         [],
            "resolved_at":        _now_iso(),
            "source":             SOURCE_LABEL,
        }

    # Fetch ±1 day of games (the ±6h tolerance can straddle a date
    # boundary in some timezones; querying day-1, day and day+1 is the
    # safe minimum).
    all_games: list[dict] = []
    days_to_query = sorted({
        (commence_time + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (-1, 0, 1)
    })
    last_fetch_error: Optional[Exception] = None
    for iso_day in days_to_query:
        try:
            games = await games_fetcher(iso_day)
        except Exception as exc:  # noqa: BLE001
            last_fetch_error = exc
            log.info("[365scores_identity] games_fetcher raised for %s: %s",
                     iso_day, exc)
            continue
        if isinstance(games, list):
            all_games.extend(games)
        elif isinstance(games, dict):
            inner = games.get("games") or games.get("data") or []
            if isinstance(inner, list):
                all_games.extend(inner)

    if not all_games:
        return {
            "status":            (STATUS_SOURCE_UNAVAILABLE
                                   if last_fetch_error is not None
                                   else STATUS_NOT_FOUND),
            "confidence":         CONFIDENCE_LOW,
            "internal_match_id":  internal_match_id,
            "reason_code":        (RC_SOURCE_TIMEOUT
                                    if last_fetch_error is not None
                                    else RC_NO_CANDIDATES),
            "aliases_used":       aliases_used,
            "candidates":         [],
            "resolved_at":        _now_iso(),
            "source":             SOURCE_LABEL,
        }

    candidates = _select_candidates(
        games=all_games,
        canonical_home=canonical_home,
        canonical_away=canonical_away,
        commence_time=commence_time,
        tolerance_hours=tolerance_hours,
        competition_id=competition_id,
    )

    if not candidates:
        return {
            "status":             STATUS_NOT_FOUND,
            "confidence":         CONFIDENCE_LOW,
            "internal_match_id":  internal_match_id,
            "reason_code":        RC_NO_CANDIDATES,
            "aliases_used":       aliases_used,
            "candidates":         [],
            "resolved_at":        _now_iso(),
            "source":             SOURCE_LABEL,
        }

    # Ambiguity check: more than one candidate with kickoff within 1h.
    if len(candidates) > 1:
        top = candidates[0]
        second = candidates[1]
        top_delta = top.get("kickoff_delta_seconds")
        second_delta = second.get("kickoff_delta_seconds")
        # Both unknown deltas → ambiguous.
        # Both within 1h → ambiguous.
        ambiguous = False
        if top_delta is None and second_delta is None:
            ambiguous = True
        elif (top_delta is not None and second_delta is not None
              and abs(top_delta - second_delta) <= 3600.0
              and second_delta <= 3 * 3600.0):
            ambiguous = True
        if ambiguous:
            return {
                "status":             STATUS_AMBIGUOUS,
                "confidence":         CONFIDENCE_LOW,
                "internal_match_id":  internal_match_id,
                "reason_code":        RC_MULTIPLE_CANDIDATES,
                "aliases_used":       aliases_used,
                "candidates":         [
                    {
                        "game_id": _safe_int(
                            (c.get("game") or {}).get("id")
                            or (c.get("game") or {}).get("gameId"),
                        ),
                        "kickoff_delta_seconds": c.get("kickoff_delta_seconds"),
                        "competition_id":        c.get("competition_id"),
                        "home_name_source":      (c.get("home_doc") or {}).get("name"),
                        "away_name_source":      (c.get("away_doc") or {}).get("name"),
                    }
                    for c in candidates[:5]
                ],
                "resolved_at":        _now_iso(),
                "source":             SOURCE_LABEL,
            }

    best = candidates[0]
    h_doc = best.get("home_doc") or {}
    a_doc = best.get("away_doc") or {}
    mapping = validate_team_mapping(
        canonical_home=canonical_home,
        canonical_away=canonical_away,
        source_home_name=h_doc.get("name") or h_doc.get("symbolicName"),
        source_away_name=a_doc.get("name") or a_doc.get("symbolicName"),
    )
    if not mapping["valid"]:
        return {
            "status":             STATUS_INVALID_TEAM_MAPPING,
            "confidence":         CONFIDENCE_LOW,
            "internal_match_id":  internal_match_id,
            "game_id":            _safe_int(
                (best.get("game") or {}).get("id")
                or (best.get("game") or {}).get("gameId"),
            ),
            "home_team_name_source": h_doc.get("name"),
            "away_team_name_source": a_doc.get("name"),
            "reason_code":        mapping["reason"],
            "resolved_from":      "search",
            "aliases_used":       aliases_used,
            "candidates":         [],
            "resolved_at":        _now_iso(),
            "source":             SOURCE_LABEL,
        }
    identity = _build_identity_doc(
        internal_match_id=internal_match_id,
        canonical_home=canonical_home,
        canonical_away=canonical_away,
        commence_time=commence_time,
        competition=competition,
        candidate=best,
        resolved_from="search",
        source_url=match_url,
        aliases_used=aliases_used,
        mapping=mapping,
    )
    identity["reason_code"] = RC_FROM_SEARCH
    if persist:
        await _persist_identity(db=db, doc=identity)
    return identity


__all__ = [
    "SOURCE_LABEL", "MONGO_COLLECTION", "DEFAULT_TOLERANCE_HOURS",
    "STATUS_RESOLVED", "STATUS_AMBIGUOUS", "STATUS_NOT_FOUND",
    "STATUS_SOURCE_UNAVAILABLE", "STATUS_INVALID_TEAM_MAPPING",
    "CONFIDENCE_HIGH", "CONFIDENCE_MEDIUM", "CONFIDENCE_LOW",
    "RC_FROM_MONGO_CACHE", "RC_FROM_URL", "RC_FROM_SEARCH",
    "RC_NO_INPUTS", "RC_NO_CANDIDATES", "RC_MULTIPLE_CANDIDATES",
    "RC_SOURCE_TIMEOUT",
    "RC_TEAM_MAPPING_OK", "RC_TEAM_MAPPING_SWAPPED", "RC_TEAM_MAPPING_INVALID",
    "normalize_team_name", "build_team_alias_set",
    "validate_team_mapping", "ensure_indexes",
    "resolve_match_identity",
]
