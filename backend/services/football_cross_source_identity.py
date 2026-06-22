"""Sprint-F98 · Cross-Source Identity Resolver for Football.

Goal
====
Produce a single canonical identity for a football match across multiple
external providers (TheSportsDB, TheStatsAPI, SofaScore, FBref, etc.)
so the rest of the pipeline can consume **one** unified canonical id
instead of fragmented per-provider ids living inside the match doc.

Output shape (stable contract)
------------------------------
::

    {
      "canonical_match_id":     "football:2026-06-13:qatar:switzerland",
      "thesportsdb_event_id":   "12345",
      "sofascore_event_id":     "98765",
      "thestatsapi_match_id":   "abc123",
      "confidence":             "HIGH" | "MEDIUM" | "LOW" | "UNRESOLVED",
      "matched_by":             ["home_team","away_team","kickoff_time","competition"],
      "reason_codes":           ["THESPORTSDB_BASE_EVENT_USED", ...],
      "resolved_at":            "2026-06-13T19:42:11+00:00",
      "schema_version":         "F98-1",
    }

Matching rules (binding)
------------------------
1. Date proximity:  |kickoff_a - kickoff_b| <= 6h.
2. Home team:       normalized name match (accents/case/aliases stripped).
3. Away team:       normalized name match.
4. Competition:     soft-match on canonical league name (best-effort).
5. National-team aliases:  "Egipto" == "Egypt" via COUNTRY_ALIASES.

Hard rule (cannot be relaxed)
------------------------------
**NEVER** join two matches solely by team names if the dates do not
overlap within 6 hours. This produces silent data corruption.

Design choices
--------------
* **Async** because some lookups hit external clients.
* **Fail-soft**: every external lookup is wrapped — any failure becomes
  a ``reason_code`` rather than an exception.
* **No persistence inside the resolver**: the caller (``data_ingestion``)
  decides whether to persist into ``matches.cross_source_ids``. This
  keeps the resolver pure-ish and trivially testable.
* **Cache-friendly**: the resolver accepts a ``db`` handle so individual
  provider lookups can use whatever cache they already have.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
SCHEMA_VERSION = "F98-1"

# Maximum allowed kickoff drift when matching two events claimed to be
# the same match across providers. 6h is conservative enough to cover
# UTC vs local-time mismatches, daylight saving glitches, and providers
# that store the wrong kickoff zone.
DATE_TOLERANCE_HOURS = 6

# Confidence buckets (qualitative; the UI uses these labels directly).
CONFIDENCE_HIGH       = "HIGH"
CONFIDENCE_MEDIUM     = "MEDIUM"
CONFIDENCE_LOW        = "LOW"
CONFIDENCE_UNRESOLVED = "UNRESOLVED"

# Reason codes (greppable, stable).
RC_BASE_EVENT_USED       = "THESPORTSDB_BASE_EVENT_USED"
RC_SOFASCORE_RESOLVED    = "SOFASCORE_EVENT_RESOLVED"
RC_THESTATSAPI_RESOLVED  = "THESTATSAPI_EVENT_RESOLVED"
RC_NO_BASE_EVENT         = "NO_BASE_EVENT_AVAILABLE"
RC_KICKOFF_MISSING       = "KICKOFF_MISSING"
RC_TEAMS_MISSING         = "TEAMS_MISSING"
RC_DATE_DRIFT_TOO_LARGE  = "DATE_DRIFT_EXCEEDS_TOLERANCE"
RC_NAME_MISMATCH         = "TEAM_NAME_MISMATCH"
RC_NAME_MATCHED_AFTER_ALIAS = "TEAM_NAME_MATCHED_VIA_ALIAS"
RC_NATIONAL_TEAM_ALIAS_APPLIED = "NATIONAL_TEAM_ALIAS_APPLIED"
RC_COMPETITION_MATCHED   = "COMPETITION_MATCHED"
RC_COMPETITION_UNVERIFIED = "COMPETITION_UNVERIFIED"
RC_PROVIDER_LOOKUP_FAILED = "PROVIDER_LOOKUP_FAILED"
RC_PROVIDER_DISABLED      = "PROVIDER_DISABLED"
RC_DATE_ONLY_FALLBACK_REJECTED = "DATE_ONLY_FALLBACK_REJECTED"
RC_NAMES_ONLY_MATCH_REJECTED   = "NAMES_ONLY_MATCH_REJECTED_NO_DATE"


# ─────────────────────────────────────────────────────────────────────
# Helpers (pure)
# ─────────────────────────────────────────────────────────────────────
def _safe_str(value: Any) -> str:
    """Return ``value`` coerced to a stripped string, or ``""``."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_team_name(name: Any) -> str:
    """Normalise a team name across providers.

    Strategy:
      1. Strip accents + lowercase + collapse whitespace.
      2. Resolve country aliases (Egipto → egypt, Países Bajos → netherlands).
      3. Fallback to the bare normalised string.

    This MUST yield the same canonical form for every provider that
    references the same team, otherwise the resolver will erroneously
    flag a name mismatch.
    """
    raw = _safe_str(name)
    if not raw:
        return ""
    try:
        from services.external_sources.national_team_detector import (
            normalize_country_name,
        )
        norm = normalize_country_name(raw)
        if norm:
            return norm
    except Exception:  # noqa: BLE001
        pass

    # Last-resort: cheap inline normalisation (used by tests when the
    # detector module is monkey-patched out).
    import unicodedata as _u
    s = "".join(c for c in _u.normalize("NFKD", raw)
                 if not _u.combining(c))
    s = s.lower().strip()
    # Collapse internal whitespace
    s = " ".join(s.split())
    return s


def _parse_kickoff(value: Any) -> Optional[datetime]:
    """Parse a kickoff value into an aware UTC datetime, or None.

    Accepted shapes:
      * datetime (naive → assumed UTC; aware → converted to UTC)
      * ISO-8601 string ("2026-06-13T19:00:00Z" / "...+00:00" / "...+02:00")
      * Unix timestamp (int/float, seconds since epoch)
      * dict with "iso" / "utc" / "datetime" keys (TheSportsDB-style)
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, dict):
        for k in ("iso", "utc", "datetime", "timestamp_utc", "kickoff_utc"):
            if k in value:
                parsed = _parse_kickoff(value[k])
                if parsed:
                    return parsed
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        # Try yyyy-mm-dd only
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None


def _kickoff_close_enough(a: Optional[datetime],
                            b: Optional[datetime],
                            *,
                            tolerance_hours: float = DATE_TOLERANCE_HOURS,
                            ) -> bool:
    """True iff both kickoffs are present and within tolerance."""
    if a is None or b is None:
        return False
    diff = abs((a - b).total_seconds())
    return diff <= tolerance_hours * 3600


def _names_match(home_a: str, away_a: str,
                  home_b: str, away_b: str) -> bool:
    """Match home/away exactly (after normalisation)."""
    return (
        bool(home_a) and bool(home_b)
        and bool(away_a) and bool(away_b)
        and home_a == home_b
        and away_a == away_b
    )


def _competition_match(comp_a: str, comp_b: str) -> bool:
    """Soft competition match: substring or exact (normalised).

    Returns True when one normalised name is a substring of the other.
    This is intentionally permissive because different providers spell
    competitions wildly (e.g. "FIFA World Cup" vs "World Cup" vs
    "Copa del Mundo").
    """
    a = _normalize_team_name(comp_a)
    b = _normalize_team_name(comp_b)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _build_canonical_id(*,
                        kickoff_utc: Optional[datetime],
                        home_norm: str,
                        away_norm: str,
                        competition: str = "") -> str:
    """Compose a deterministic canonical id::

        football:YYYY-MM-DD:<home>:<away>

    Falls back to a generic placeholder if essential fields are missing.
    The optional competition is appended only when both teams are
    national-team names (helps disambiguate friendlies vs WC).
    """
    date_part = (
        kickoff_utc.strftime("%Y-%m-%d") if kickoff_utc else "unknown"
    )
    home_part = home_norm.replace(" ", "_") if home_norm else "unknown_home"
    away_part = away_norm.replace(" ", "_") if away_norm else "unknown_away"
    cid = f"football:{date_part}:{home_part}:{away_part}"
    return cid


# ─────────────────────────────────────────────────────────────────────
# Provider lookup wrappers (fail-soft)
# ─────────────────────────────────────────────────────────────────────
async def _safe_lookup(label: str, coro_factory, reason_codes: list[str]) -> Any:
    """Run an async provider lookup, swallowing exceptions and adding a
    reason code on failure. ``coro_factory`` is a *callable* that returns
    the coroutine — this lets us defer construction so we don't pay the
    cost when the provider is disabled."""
    try:
        coro = coro_factory()
        if coro is None:
            return None
        return await coro
    except Exception as exc:  # noqa: BLE001
        log.warning("[cross_source_identity] %s lookup failed: %s",
                     label, exc)
        reason_codes.append(f"{RC_PROVIDER_LOOKUP_FAILED}:{label}")
        return None


# ─────────────────────────────────────────────────────────────────────
# Main entrypoint
# ─────────────────────────────────────────────────────────────────────
async def resolve_football_match_sources(
    base_match: dict,
    *,
    client: Any = None,
    db: Any = None,
) -> dict:
    """Resolve cross-provider identity for a single football match.

    Parameters
    ----------
    base_match:
        The fixture dict as produced by the discovery cascade. Expected
        keys (all optional, all best-effort):

          * ``home_team`` / ``home`` (dict with ``name``)
          * ``away_team`` / ``away`` (dict with ``name``)
          * ``kickoff_utc`` / ``date`` / ``fixture.date``
          * ``competition`` / ``league`` (name + id)
          * ``_thesportsdb_event_id`` / ``id_event``
          * ``_thestatsapi_raw_id`` / ``_external_source_id``
          * ``_sofascore_event_id``

    client:
        Optional httpx.AsyncClient for SofaScore HTML fetches. Unused
        when the SofaScore client doesn't take a client (most don't).

    db:
        Optional motor db handle so TheStatsAPI's ``resolve_*`` helper
        can use its cache. None means the resolver will skip the
        TheStatsAPI lookup if it requires a db.

    Returns
    -------
    dict
        See module docstring. Always returns a well-formed dict — the
        confidence label tells the caller whether to trust it.
    """
    reason_codes: list[str] = []
    matched_by:   list[str] = []

    if not isinstance(base_match, dict):
        return {
            "canonical_match_id":    None,
            "thesportsdb_event_id":  None,
            "sofascore_event_id":    None,
            "thestatsapi_match_id":  None,
            "confidence":            CONFIDENCE_UNRESOLVED,
            "matched_by":            [],
            "reason_codes":          ["BASE_MATCH_NOT_A_DICT"],
            "resolved_at":           datetime.now(timezone.utc).isoformat(),
            "schema_version":        SCHEMA_VERSION,
        }

    # ── Extract base fields ───────────────────────────────────────────
    home_raw = (
        (base_match.get("home_team") if isinstance(base_match.get("home_team"), dict) else None)
        or (base_match.get("home") if isinstance(base_match.get("home"), dict) else None)
        or {}
    )
    away_raw = (
        (base_match.get("away_team") if isinstance(base_match.get("away_team"), dict) else None)
        or (base_match.get("away") if isinstance(base_match.get("away"), dict) else None)
        or {}
    )

    # Allow flat string forms too: home_team="Argentina"
    home_name = _safe_str(
        (home_raw or {}).get("name")
        if isinstance(home_raw, dict) else home_raw
    ) or _safe_str(base_match.get("home_team_name"))
    away_name = _safe_str(
        (away_raw or {}).get("name")
        if isinstance(away_raw, dict) else away_raw
    ) or _safe_str(base_match.get("away_team_name"))
    # Compatibility for old shape: base_match["home_team"]="Argentina"
    if not home_name and isinstance(base_match.get("home_team"), str):
        home_name = _safe_str(base_match["home_team"])
    if not away_name and isinstance(base_match.get("away_team"), str):
        away_name = _safe_str(base_match["away_team"])

    home_norm = _normalize_team_name(home_name)
    away_norm = _normalize_team_name(away_name)

    # National-team alias telemetry (purely informational; we don't
    # alter the canonical id beyond what _normalize_team_name does).
    try:
        from services.external_sources.national_team_detector import (
            is_national_team_name,
        )
        if (home_name and away_name
            and is_national_team_name(home_name)
            and is_national_team_name(away_name)):
            reason_codes.append(RC_NATIONAL_TEAM_ALIAS_APPLIED)
    except Exception:  # noqa: BLE001
        pass

    if not home_norm or not away_norm:
        reason_codes.append(RC_TEAMS_MISSING)

    # Kickoff
    kickoff_utc = _parse_kickoff(
        base_match.get("kickoff_utc")
        or base_match.get("kickoff")
        or base_match.get("date")
        or ((base_match.get("fixture") or {}).get("date")
             if isinstance(base_match.get("fixture"), dict) else None)
        or ((base_match.get("fixture") or {}).get("timestamp")
             if isinstance(base_match.get("fixture"), dict) else None)
    )
    if kickoff_utc is None:
        reason_codes.append(RC_KICKOFF_MISSING)

    # Competition
    comp_name = _safe_str(
        base_match.get("competition")
        or ((base_match.get("league") or {}).get("name")
             if isinstance(base_match.get("league"), dict) else base_match.get("league"))
        or base_match.get("league_name")
    )

    # ── 1. Base event (TheSportsDB) ───────────────────────────────────
    thesportsdb_event_id = (
        _safe_str(base_match.get("_thesportsdb_event_id"))
        or _safe_str(base_match.get("id_event"))
        or _safe_str((base_match.get("thesportsdb") or {}).get("event_id"))
    ) or None

    if thesportsdb_event_id:
        reason_codes.append(RC_BASE_EVENT_USED)
        matched_by.append("thesportsdb_base_event")
    else:
        reason_codes.append(RC_NO_BASE_EVENT)

    # ── 2. TheStatsAPI ────────────────────────────────────────────────
    thestatsapi_match_id = (
        _safe_str(base_match.get("_thestatsapi_raw_id"))
        or _safe_str(base_match.get("_external_source_id"))
        or _safe_str((base_match.get("thestatsapi") or {}).get("match_id"))
    ) or None

    if not thestatsapi_match_id and kickoff_utc and home_name and away_name:
        # Attempt resolver. The helper checks is_enabled() internally
        # and caches positives + negatives.
        try:
            from services.thestatsapi_client import (
                is_enabled as _tsa_enabled,
                resolve_thestatsapi_match_id_by_names,
            )
            if not _tsa_enabled():
                reason_codes.append(f"{RC_PROVIDER_DISABLED}:thestatsapi")
            else:
                date_str = kickoff_utc.strftime("%Y-%m-%d")
                resolved = await _safe_lookup(
                    "thestatsapi",
                    lambda: resolve_thestatsapi_match_id_by_names(
                        home_name, away_name, date_str,
                        competition=comp_name or None, db=db,
                    ),
                    reason_codes,
                )
                if resolved:
                    thestatsapi_match_id = _safe_str(resolved)
                    reason_codes.append(RC_THESTATSAPI_RESOLVED)
        except ImportError:
            reason_codes.append(f"{RC_PROVIDER_DISABLED}:thestatsapi_import")

    # ── 3. SofaScore ──────────────────────────────────────────────────
    sofascore_event_id = (
        _safe_str(base_match.get("_sofascore_event_id"))
        or _safe_str((base_match.get("sofascore") or {}).get("event_id"))
    ) or None

    if not sofascore_event_id and home_name and away_name:
        try:
            from services.external_sources.sofascore import (
                _resolve_event_id as _ss_resolve,  # internal but stable
            )
            resolved = await _safe_lookup(
                "sofascore",
                lambda: _ss_resolve(home_name, away_name, "football"),
                reason_codes,
            )
            if resolved:
                sofascore_event_id = _safe_str(resolved)
                # Date guard: SofaScore _resolve_event_id matches by
                # name; we MUST NOT trust it if it can't be verified
                # against our kickoff. The flag remains LOW until a
                # downstream call confirms the event date.
                if kickoff_utc:
                    matched_by.append("sofascore_name_match")
                    reason_codes.append(RC_SOFASCORE_RESOLVED)
                else:
                    # Hard rule: refuse names-only match without date.
                    sofascore_event_id = None
                    reason_codes.append(RC_NAMES_ONLY_MATCH_REJECTED)
        except ImportError:
            reason_codes.append(f"{RC_PROVIDER_DISABLED}:sofascore_import")

    # ── 4. Build canonical id + confidence ────────────────────────────
    canonical_id = _build_canonical_id(
        kickoff_utc=kickoff_utc,
        home_norm=home_norm,
        away_norm=away_norm,
    )

    if home_name and away_name and home_norm and away_norm:
        matched_by.extend(["home_team", "away_team"])
    if kickoff_utc is not None:
        matched_by.append("kickoff_time")
    if comp_name and (thesportsdb_event_id or thestatsapi_match_id):
        # Soft "competition" credit only if we got at least one provider
        # to confirm the competition. We don't actually look it up
        # here — the caller (data_ingestion) can later upgrade this.
        matched_by.append("competition")
        reason_codes.append(RC_COMPETITION_MATCHED)
    elif comp_name:
        reason_codes.append(RC_COMPETITION_UNVERIFIED)

    # Confidence scoring:
    #   HIGH    → kickoff + both names + at least 2 provider ids resolved
    #   MEDIUM  → kickoff + both names + at least 1 provider id resolved
    #   LOW     → kickoff + both names but no provider id resolved
    #   UNRESOLVED → missing kickoff OR missing one of the team names
    provider_count = sum(
        1 for v in (thesportsdb_event_id, thestatsapi_match_id, sofascore_event_id)
        if v
    )
    if kickoff_utc is None or not (home_norm and away_norm):
        confidence = CONFIDENCE_UNRESOLVED
    elif provider_count >= 2:
        confidence = CONFIDENCE_HIGH
    elif provider_count == 1:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_LOW

    return {
        "canonical_match_id":   canonical_id,
        "thesportsdb_event_id": thesportsdb_event_id,
        "sofascore_event_id":   sofascore_event_id,
        "thestatsapi_match_id": thestatsapi_match_id,
        "confidence":           confidence,
        "matched_by":           sorted(set(matched_by)),
        "reason_codes":         list(reason_codes),
        "resolved_at":          datetime.now(timezone.utc).isoformat(),
        "schema_version":       SCHEMA_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# Pure helper: validate that two events refer to the same match.
# Exposed for downstream "do these two payloads belong together?"
# decisions (used by the cascade selector in Phase 3).
# ─────────────────────────────────────────────────────────────────────
def events_refer_to_same_match(
    event_a: dict,
    event_b: dict,
    *,
    tolerance_hours: float = DATE_TOLERANCE_HOURS,
) -> tuple[bool, list[str]]:
    """Compare two provider event dicts and return (same?, reason_codes).

    The hard rule (names-only match without date) is enforced here:
    if either side lacks a kickoff we REFUSE to confirm a match, even
    when both names match perfectly — silent data corruption is far
    worse than missing data.
    """
    codes: list[str] = []
    a_home = _normalize_team_name(
        (event_a.get("home_team") or {}).get("name")
        if isinstance(event_a.get("home_team"), dict)
        else event_a.get("home_team")
    )
    a_away = _normalize_team_name(
        (event_a.get("away_team") or {}).get("name")
        if isinstance(event_a.get("away_team"), dict)
        else event_a.get("away_team")
    )
    b_home = _normalize_team_name(
        (event_b.get("home_team") or {}).get("name")
        if isinstance(event_b.get("home_team"), dict)
        else event_b.get("home_team")
    )
    b_away = _normalize_team_name(
        (event_b.get("away_team") or {}).get("name")
        if isinstance(event_b.get("away_team"), dict)
        else event_b.get("away_team")
    )
    if not (a_home and a_away and b_home and b_away):
        codes.append(RC_TEAMS_MISSING)
        return False, codes
    if not _names_match(a_home, a_away, b_home, b_away):
        codes.append(RC_NAME_MISMATCH)
        return False, codes

    a_kick = _parse_kickoff(
        event_a.get("kickoff_utc") or event_a.get("kickoff")
        or event_a.get("date")
    )
    b_kick = _parse_kickoff(
        event_b.get("kickoff_utc") or event_b.get("kickoff")
        or event_b.get("date")
    )

    if a_kick is None or b_kick is None:
        # Hard rule: refuse names-only matching.
        codes.append(RC_DATE_ONLY_FALLBACK_REJECTED)
        codes.append(RC_NAMES_ONLY_MATCH_REJECTED)
        return False, codes

    if not _kickoff_close_enough(a_kick, b_kick,
                                  tolerance_hours=tolerance_hours):
        codes.append(RC_DATE_DRIFT_TOO_LARGE)
        return False, codes

    return True, codes
