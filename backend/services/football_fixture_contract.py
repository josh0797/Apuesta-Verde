"""F87.1 — Football Fixture Contract (+Parte 1.5 upstream audit).

Single source of truth for the shape of a football fixture as it enters
``data_ingestion._enrich_football`` and the rest of the pipeline.

Every F87 discovery adapter (TheStatsAPI, API-Football, ESPN, Sofascore
PW, scrape.do) MUST flow through :func:`ensure_api_football_fixture_shape`
before the cascade does counting / short-circuit / merge / dedupe so the
downstream ``_enrich_football`` receives a uniform, nested shape:

    {
      "fixture": {"id", "date", "timestamp", "status": {"short"}, "venue"},
      "league":  {"id", "name", "country", "season"},
      "teams":   {"home": {"id", "name"}, "away": {"id", "name"}},
      "_external_source", "_external_source_id", "_discovery_source",
      "_is_national_team", "_is_international",
    }

Reason codes
------------
* ``FIXTURE_SHAPE_ALREADY_VALID``        — input was already nested.
* ``FIXTURE_SHAPE_NORMALIZED``           — flattened input promoted.
* ``FIXTURE_SHAPE_INVALID_MISSING_TEAMS`` — home or away name missing.
* ``FIXTURE_SHAPE_INVALID_MISSING_KICKOFF`` — kickoff cannot be parsed.
* ``FIXTURE_SHAPE_SYNTHETIC_ID_CREATED``  — id was missing; synthesised.
* ``FIXTURE_DATE_NAIVE_ASSUMED_UTC``      — naive ISO assumed UTC.
* ``ADAPTER_RETURNED_EMPTY``              — bucket received with zero raw rows.

Parte 1.5 — Upstream audit
--------------------------
``normalize_bucket`` now emits, in addition to the existing ``raw_count``,
``kept_count``, ``dropped_count`` and ``reason_codes`` aggregates:

* ``dropped_samples``      — up to ``DISCOVERY_DROPPED_SAMPLE_CAP`` (default 3)
  rich evidence dicts with ``raw_id``, ``league``, ``home_candidates``,
  ``away_candidates``, ``kickoff_candidates`` and the dominant
  ``reason_code``.
* ``dropped_samples_shown`` / ``dropped_samples_cap``.
* ``top_reason``           — most frequent reason code in this bucket.
* ``had_raw_but_all_rejected`` — True when ``raw_count > 0`` and
  ``kept_count == 0``.

The function NEVER raises. ``None`` is returned for inputs that cannot
be promoted (missing team names or unparseable kickoff).
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("services.football_fixture_contract")

# Reason codes (exported).
RC_ALREADY_VALID            = "FIXTURE_SHAPE_ALREADY_VALID"
RC_NORMALIZED               = "FIXTURE_SHAPE_NORMALIZED"
RC_INVALID_MISSING_TEAMS    = "FIXTURE_SHAPE_INVALID_MISSING_TEAMS"
RC_INVALID_MISSING_KICKOFF  = "FIXTURE_SHAPE_INVALID_MISSING_KICKOFF"
RC_SYNTHETIC_ID_CREATED     = "FIXTURE_SHAPE_SYNTHETIC_ID_CREATED"
RC_DATE_NAIVE_ASSUMED       = "FIXTURE_DATE_NAIVE_ASSUMED_UTC"
RC_ADAPTER_RETURNED_EMPTY   = "ADAPTER_RETURNED_EMPTY"


_INT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|conmebol|africa cup|asian cup|"
    r"copa america|euro\s|qualifi|friendl|club world|libertadores|"
    r"sudamericana|gold cup|afcon|concacaf|caf champions|"
    r"asian champions)",
)
_NT_NAME_RX = re.compile(
    r"(?i)(world cup|nations league|copa america|africa cup|"
    r"euro\s|gold cup|asian cup|qualifi|friendl)",
)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ASCII", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _coerce_kickoff(value: Any) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Try to derive ``(timestamp_int, iso_string, reason_code)``."""
    if value is None:
        return (None, None, None)
    if isinstance(value, (int, float)):
        try:
            ts = int(value)
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            return (ts, iso, None)
        except (TypeError, ValueError, OverflowError, OSError):
            return (None, None, None)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except (TypeError, ValueError):
            return (None, None, None)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            return (int(dt.timestamp()), dt.isoformat(), RC_DATE_NAIVE_ASSUMED)
        dt = dt.astimezone(timezone.utc)
        return (int(dt.timestamp()), dt.isoformat(), None)
    return (None, None, None)


def _pick(*candidates):
    for c in candidates:
        if c is not None and c != "":
            return c
    return None


def _dict_or_empty(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_get(d: Any, path: str) -> Any:
    """Return ``d`` traversed by dotted ``path``; ``None`` on any miss."""
    if d is None:
        return None
    cur: Any = d
    for part in path.split("."):
        if "[" in part and part.endswith("]"):
            key, idx_str = part[:-1].split("[", 1)
            try:
                idx = int(idx_str)
            except ValueError:
                return None
            if key:
                if not isinstance(cur, dict) or key not in cur:
                    return None
                cur = cur[key]
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
            if cur is None:
                return None
    return cur


# ─────────────────────────────────────────────────────────────────────
# Parte 1.5 — Candidate extraction with audit
# ─────────────────────────────────────────────────────────────────────
_HOME_PATHS: tuple[str, ...] = (
    "teams.home.name",
    "home_team.name",
    "homeTeam.name",
    "home.name",
    "participants.home.name",
    "homeCompetitor.name",
    "localTeam.name",
    "team1.name",
)
_AWAY_PATHS: tuple[str, ...] = (
    "teams.away.name",
    "away_team.name",
    "awayTeam.name",
    "away.name",
    "participants.away.name",
    "awayCompetitor.name",
    "visitorTeam.name",
    "team2.name",
)
_KICKOFF_PATHS: tuple[str, ...] = (
    "fixture.timestamp",
    "fixture.date",
    "timestamp",
    "kickoff_ts",
    "kickoff_at",
    "starts_at",
    "date",
    "kickoff_iso",
    "startTimestamp",
    "commence_time",
)


def _competitors_split(competitors: Any) -> tuple[Optional[dict], Optional[dict]]:
    """Identify home/away dicts inside a ``competitors`` array.

    Strategy:
      1) Mark a competitor as home when ``homeAway=="home"`` /
         ``isHome==True`` / ``home==True`` / ``side=="home"``; analogous
         for away.
      2) Fallback: index 0 → home, index 1 → away.

    Returns ``(home_dict_or_None, away_dict_or_None)``.
    """
    if not isinstance(competitors, list) or not competitors:
        return (None, None)
    home: Optional[dict] = None
    away: Optional[dict] = None
    for c in competitors:
        if not isinstance(c, dict):
            continue
        ha = str(c.get("homeAway") or "").lower()
        side = str(c.get("side") or "").lower()
        if home is None and (
            ha == "home" or side == "home"
            or c.get("isHome") is True
            or c.get("home") is True
        ):
            home = c
        elif away is None and (
            ha == "away" or side == "away"
            or c.get("isHome") is False and home is not None
            or c.get("away") is True
        ):
            away = c
    # Index fallback when explicit markers are missing.
    if home is None and len(competitors) >= 1 and isinstance(competitors[0], dict):
        home = competitors[0]
    if away is None and len(competitors) >= 2 and isinstance(competitors[1], dict):
        # Avoid using the same dict twice if it was already used as home.
        if competitors[1] is not home:
            away = competitors[1]
    return (home, away)


def _competitor_name(c: Optional[dict]) -> Optional[str]:
    if not isinstance(c, dict):
        return None
    return _pick(
        c.get("name"),
        c.get("displayName"),
        (c.get("team") or {}).get("name") if isinstance(c.get("team"), dict) else None,
    )


def _extract_team_candidates(
    fx: dict, *, side: str,
) -> tuple[Optional[str], Optional[dict], dict]:
    """Return ``(name, raw_block_dict, candidates_audit)`` for one side.

    ``candidates_audit`` is a path → value map of every probed location
    so the discovery audit can show *exactly* what was tried.
    """
    paths = _HOME_PATHS if side == "home" else _AWAY_PATHS
    audit: dict = {p: _safe_get(fx, p) for p in paths}

    # First pass: dotted-path candidates.
    name: Optional[str] = None
    raw_block: Optional[dict] = None
    for p in paths:
        v = audit[p]
        if isinstance(v, str) and v.strip():
            name = v.strip()
            # Walk back to the parent block so we can preserve ids.
            parent_path = p.rsplit(".", 1)[0]
            raw_block = _safe_get(fx, parent_path)
            if not isinstance(raw_block, dict):
                raw_block = None
            break

    # Second pass: ``competitors`` array (ESPN-style + others).
    if name is None:
        competitors = fx.get("competitors")
        h, a = _competitors_split(competitors)
        candidate = h if side == "home" else a
        cname = _competitor_name(candidate)
        if isinstance(cname, str) and cname.strip():
            name = cname.strip()
            raw_block = candidate
            audit["competitors[]"] = cname
        else:
            audit["competitors[]"] = None

    return (name, raw_block, audit)


def _extract_kickoff_with_audit(
    fx: dict, reason_codes: list[str],
) -> tuple[Optional[int], Optional[str], dict]:
    """Resolve kickoff with a full audit of probed candidates."""
    audit: dict = {p: _safe_get(fx, p) for p in _KICKOFF_PATHS}

    ts: Optional[int] = None
    iso: Optional[str] = None
    for p in _KICKOFF_PATHS:
        v = audit[p]
        if v is None or v == "":
            continue
        c_ts, c_iso, rc = _coerce_kickoff(v)
        if c_ts is not None or c_iso is not None:
            ts = ts if ts is not None else c_ts
            iso = iso if iso is not None else c_iso
            if rc and rc not in reason_codes:
                reason_codes.append(rc)
        if ts is not None and iso is not None:
            break

    return (ts, iso, audit)


def ensure_api_football_fixture_shape(
    fx: Any,
    *,
    source: str = "unknown",
    reason_codes: Optional[list[str]] = None,
    drop_evidence: Optional[dict] = None,
) -> Optional[dict]:
    """Normalise *any* discovery-source fixture into the canonical
    API-Football nested shape that ``_enrich_football`` consumes.

    Accepts both:
      * The nested API-Football shape (kept as-is, only fills gaps).
      * Flat F87 legacy shape.
      * Sofascore ``homeTeam``/``awayTeam`` shape.
      * ESPN ``competitors[]`` shape (with optional ``homeAway``/``isHome``).
      * Other vendor variants (``participants``, ``team1/team2``, …).

    Returns ``None`` when home / away team names cannot be resolved or
    when the kickoff cannot be parsed. The ``reason_codes`` list is
    appended with diagnostic codes.

    When ``drop_evidence`` is provided AND the fixture is rejected, it
    is populated with the rich audit used by the discovery debug
    endpoint:

        {
          "raw_id":  ...,
          "league":  ...,
          "home_candidates":    {path: value, ...},
          "away_candidates":    {path: value, ...},
          "kickoff_candidates": {path: value, ...},
          "reason_code":        "FIXTURE_SHAPE_INVALID_MISSING_TEAMS" | ...,
        }
    """
    if reason_codes is None:
        reason_codes = []

    if not isinstance(fx, dict):
        reason_codes.append(RC_INVALID_MISSING_TEAMS)
        if drop_evidence is not None:
            drop_evidence.update({
                "raw_id":  None,
                "league":  None,
                "home_candidates":    {},
                "away_candidates":    {},
                "kickoff_candidates": {},
                "reason_code":        RC_INVALID_MISSING_TEAMS,
            })
        return None

    home_name, home_block, home_audit = _extract_team_candidates(fx, side="home")
    away_name, away_block, away_audit = _extract_team_candidates(fx, side="away")

    if not home_name or not away_name:
        reason_codes.append(RC_INVALID_MISSING_TEAMS)
        if drop_evidence is not None:
            league_block = _dict_or_empty(fx.get("league"))
            drop_evidence.update({
                "raw_id":  fx.get("id") or _safe_get(fx, "fixture.id"),
                "league":  _pick(league_block.get("name"), fx.get("league")),
                "home_candidates":    home_audit,
                "away_candidates":    away_audit,
                "kickoff_candidates": {p: _safe_get(fx, p) for p in _KICKOFF_PATHS},
                "reason_code":        RC_INVALID_MISSING_TEAMS,
            })
        return None

    home_block = _dict_or_empty(home_block)
    away_block = _dict_or_empty(away_block)
    home_id = _pick(home_block.get("id"), home_block.get("team_id"))
    away_id = _pick(away_block.get("id"), away_block.get("team_id"))

    # Kickoff resolution with audit.
    ts, iso, ko_audit = _extract_kickoff_with_audit(fx, reason_codes)

    if ts is None or iso is None:
        reason_codes.append(RC_INVALID_MISSING_KICKOFF)
        if drop_evidence is not None:
            league_block = _dict_or_empty(fx.get("league"))
            drop_evidence.update({
                "raw_id":  fx.get("id") or _safe_get(fx, "fixture.id"),
                "league":  _pick(league_block.get("name"), fx.get("league")),
                "home_candidates":    home_audit,
                "away_candidates":    away_audit,
                "kickoff_candidates": ko_audit,
                "reason_code":        RC_INVALID_MISSING_KICKOFF,
            })
        return None

    league_block = _dict_or_empty(fx.get("league"))
    fixture_block = _dict_or_empty(fx.get("fixture"))

    # Status — nested wins over flat.
    status_block = _dict_or_empty(fixture_block.get("status") or fx.get("status"))
    short = status_block.get("short")
    if not isinstance(short, str) or not short.strip():
        flat_status = fx.get("status")
        if isinstance(flat_status, str) and flat_status.strip():
            short = flat_status.strip()
        else:
            short = "NS"
    long_ = status_block.get("long") or short
    venue_block = _dict_or_empty(fixture_block.get("venue") or fx.get("venue"))

    # Fixture id — prefer nested, then top-level, then synthesise.
    fid = _pick(fixture_block.get("id"), fx.get("id"))
    if not fid:
        date_only = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        fid = f"{source}-{_slug(home_name)}-{_slug(away_name)}-{date_only}"
        reason_codes.append(RC_SYNTHETIC_ID_CREATED)
    fid = str(fid)

    # League season fallback.
    season = _pick(league_block.get("season"),
                   datetime.fromtimestamp(ts, tz=timezone.utc).year)

    league_name    = _pick(league_block.get("name"), fx.get("league")) or ""
    if not isinstance(league_name, str):
        league_name = str(league_name)
    league_country = _pick(league_block.get("country"),
                           (league_block.get("category") or {}).get("name")
                             if isinstance(league_block.get("category"), dict) else None)
    league_id      = _pick(league_block.get("id"), league_block.get("league_id"))

    is_intl = bool(fx.get("_is_international")
                   or _INT_NAME_RX.search(league_name or ""))
    is_nt   = bool(fx.get("_is_national_team")
                   or _NT_NAME_RX.search(league_name or ""))

    ext_src    = _pick(fx.get("_external_source"), source)
    ext_src_id = _pick(fx.get("_external_source_id"),
                       fx.get("_thestatsapi_raw_id"), str(fid))

    already_valid = (
        isinstance(fx.get("fixture"), dict)
        and isinstance(fx["fixture"].get("id"), (str, int))
        and isinstance(fx["fixture"].get("status"), dict)
        and isinstance(fx.get("league"), dict)
        and isinstance(fx.get("teams"), dict)
    )
    reason_codes.append(RC_ALREADY_VALID if already_valid else RC_NORMALIZED)

    out = {
        "fixture": {
            "id":        fid,
            "date":      iso,
            "timestamp": ts,
            "status":    {"short": short, "long": long_},
            "venue":     {"name": venue_block.get("name"),
                          "city": venue_block.get("city")},
        },
        "league": {
            "id":      league_id,
            "name":    league_name,
            "country": league_country,
            "season":  season,
        },
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "_external_source":     ext_src,
        "_external_source_id":  str(ext_src_id) if ext_src_id is not None else None,
        "_thestatsapi_raw_id":  fx.get("_thestatsapi_raw_id"),
        "_discovery_source":    _pick(fx.get("_discovery_source"), source),
        "_is_national_team":    is_nt,
        "_is_international":    is_intl,
        # Mirror top-level fields too — legacy call-sites still reach
        # for ``fx["timestamp"]`` / ``fx["status"]`` directly.
        "id":        fid,
        "date":      iso,
        "timestamp": ts,
        "status":    {"short": short, "long": long_},
    }
    return out


def _resolve_sample_cap() -> int:
    """Resolve the cap from env each call so tests can mutate it."""
    try:
        return max(0, int(os.environ.get("DISCOVERY_DROPPED_SAMPLE_CAP", "3")))
    except (TypeError, ValueError):
        return 3


def normalize_bucket(
    fixtures: list[Any], *, source: str,
) -> tuple[list[dict], dict]:
    """Apply :func:`ensure_api_football_fixture_shape` to a whole bucket
    of fixtures. Returns ``(valid_fixtures, audit)`` where ``audit``
    aggregates rich diagnostics for the bucket.

    Audit shape::

        {
          "source":                     "thestatsapi",
          "raw_count":                  12,
          "kept_count":                 0,
          "dropped_count":              12,
          "reason_codes":               {RC: count, ...},
          "top_reason":                 "FIXTURE_SHAPE_INVALID_MISSING_TEAMS",
          "dropped_samples":            [..., up to cap],
          "dropped_samples_shown":      3,
          "dropped_samples_cap":        3,
          "had_raw_but_all_rejected":   True,
          "adapter_returned_empty":     False,
          "message_debug":              "...human readable...",
        }

    NEVER raises.
    """
    cap = _resolve_sample_cap()
    raw_count = len(fixtures or [])
    audit: dict = {
        "source":                   source,
        "raw_count":                raw_count,
        "kept_count":               0,
        "dropped_count":            0,
        "reason_codes":             {},
        "dropped_samples":          [],
        "dropped_samples_shown":    0,
        "dropped_samples_cap":      cap,
        "had_raw_but_all_rejected": False,
        "adapter_returned_empty":   raw_count == 0,
        "top_reason":               None,
        "message_debug":            None,
    }
    out: list[dict] = []

    if raw_count == 0:
        # No raw rows — adapter returned empty. Surface a single
        # ADAPTER_RETURNED_EMPTY tally so the discovery debug endpoint
        # can distinguish this from "raw>0 but contract rejected all".
        audit["reason_codes"][RC_ADAPTER_RETURNED_EMPTY] = 1
        audit["top_reason"]    = RC_ADAPTER_RETURNED_EMPTY
        audit["message_debug"] = (
            f"{source} adapter returned zero fixtures before contract "
            f"normalization."
        )
        return (out, audit)

    for f in fixtures:
        rcs: list[str] = []
        evidence: dict = {}
        norm = ensure_api_football_fixture_shape(
            f, source=source, reason_codes=rcs, drop_evidence=evidence,
        )
        for rc in rcs:
            audit["reason_codes"][rc] = audit["reason_codes"].get(rc, 0) + 1
        if norm is None:
            audit["dropped_count"] += 1
            if (cap > 0
                    and audit["dropped_samples_shown"] < cap
                    and evidence):
                audit["dropped_samples"].append(evidence)
                audit["dropped_samples_shown"] = len(audit["dropped_samples"])
            continue
        out.append(norm)
        audit["kept_count"] += 1

    # Compute top reason among *rejection* codes (ignoring informational
    # ones like NORMALIZED / ALREADY_VALID).
    _IGNORE = {RC_NORMALIZED, RC_ALREADY_VALID, RC_SYNTHETIC_ID_CREATED,
               RC_DATE_NAIVE_ASSUMED}
    rejection_codes = {k: v for k, v in audit["reason_codes"].items()
                       if k not in _IGNORE}
    if rejection_codes:
        audit["top_reason"] = max(rejection_codes.items(), key=lambda kv: kv[1])[0]

    audit["had_raw_but_all_rejected"] = (
        audit["raw_count"] > 0 and audit["kept_count"] == 0
    )
    if audit["had_raw_but_all_rejected"]:
        audit["message_debug"] = (
            f"{source}: {audit['raw_count']} raw fixtures returned but "
            f"all rejected by contract (top_reason="
            f"{audit['top_reason']})."
        )
    elif audit["dropped_count"] > 0:
        audit["message_debug"] = (
            f"{source}: kept {audit['kept_count']}/{audit['raw_count']} "
            f"(dropped {audit['dropped_count']}, top_reason="
            f"{audit['top_reason']})."
        )

    return (out, audit)


__all__ = [
    "RC_ALREADY_VALID", "RC_NORMALIZED",
    "RC_INVALID_MISSING_TEAMS", "RC_INVALID_MISSING_KICKOFF",
    "RC_SYNTHETIC_ID_CREATED", "RC_DATE_NAIVE_ASSUMED",
    "RC_ADAPTER_RETURNED_EMPTY",
    "ensure_api_football_fixture_shape",
    "normalize_bucket",
]
