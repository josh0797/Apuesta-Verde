"""Canonical match-key utilities for the Editorial Context Engine.

Problem this solves
-------------------
Editorial signals come from many sources, in different languages, using
different team spellings (Atlético-MG / Atletico Mineiro / CA Mineiro).
We need a stable key to deduplicate signals and to LOOKUP signals when the
analyst engine asks 'do you have editorial context for Match X?'.

Design
------
    canonical_match_key(sport, home, away, kickoff_iso) =
        'football:alaves:rayo_vallecano:2026-05-22'

Locality-agnostic 'encounter' key (for cross-fixture memory) =
    'football:alaves|rayo_vallecano:2026-05-22'  (teams alphabetically sorted)

Normalisation pipeline:
    1. lowercase
    2. NFKD unicode → strip accents
    3. strip common suffixes (FC, CF, AC, SC, U23, B, II)
    4. replace non-alphanumeric → underscore
    5. dedupe underscores
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

_GENERIC_SUFFIX_RE = re.compile(
    r"\b("
    r"f\.?c\.?|c\.?f\.?|a\.?c\.?|s\.?c\.?|s\.?d\.?|u\.?d\.?|u\.?c\.?"
    r"|s\.?a\.?d\.?|c\.?p\.?|a\.?d\.?|c\.?d\.?"
    r"|club|sporting club|football club|deportivo"
    r")\b",
    re.IGNORECASE,
)
_AGE_SUFFIX_RE = re.compile(r"\b(u\d{1,2}|sub\d{1,2}|b|ii|reserves?|reserva?s?)\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _strip_accents(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_team_name(name: Optional[str]) -> str:
    """Return a deterministic, ASCII slug for a team name.

    Examples:
        'Atlético Mineiro'   → 'atletico_mineiro'
        'Rayo Vallecano CF'  → 'rayo_vallecano'
        'Real Madrid B'      → 'real_madrid'
        'A.S. Roma'          → 'a_s_roma'   (kept because AS is short)
    """
    if not name:
        return ""
    s = _strip_accents(str(name).strip().lower())
    s = _GENERIC_SUFFIX_RE.sub(" ", s)
    s = _AGE_SUFFIX_RE.sub(" ", s)
    s = _NON_ALNUM_RE.sub("_", s)
    s = _MULTI_UNDERSCORE_RE.sub("_", s).strip("_")
    return s


def _date_part(kickoff_iso: Optional[str]) -> str:
    if not kickoff_iso:
        return "unknown_date"
    try:
        dt = datetime.fromisoformat(str(kickoff_iso).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        try:
            return str(kickoff_iso)[:10]
        except Exception:
            return "unknown_date"


def canonical_match_key(
    sport: Optional[str],
    home: Optional[str],
    away: Optional[str],
    kickoff_iso: Optional[str] = None,
) -> str:
    """Build the home-vs-away locality-preserving key.

        canonical_match_key('football', 'Alavés', 'Rayo Vallecano', '2026-05-22T19:00:00Z')
            → 'football:alaves:rayo_vallecano:2026-05-22'
    """
    s = (sport or "football").strip().lower()
    h = normalize_team_name(home)
    a = normalize_team_name(away)
    d = _date_part(kickoff_iso)
    return f"{s}:{h}:{a}:{d}"


def encounter_key(
    sport: Optional[str],
    home: Optional[str],
    away: Optional[str],
    kickoff_iso: Optional[str] = None,
) -> str:
    """Locality-agnostic key for encounter memory (cross-fixture H2H signals).

        encounter_key('football', 'Rayo Vallecano', 'Alavés', '2026-05-22')
            → 'football:alaves|rayo_vallecano:2026-05-22'
        encounter_key('football', 'Alavés', 'Rayo Vallecano', '2026-05-22')
            → 'football:alaves|rayo_vallecano:2026-05-22'  (same key)
    """
    s = (sport or "football").strip().lower()
    teams = sorted([normalize_team_name(home), normalize_team_name(away)])
    d = _date_part(kickoff_iso)
    return f"{s}:{teams[0]}|{teams[1]}:{d}"


__all__ = [
    "canonical_match_key",
    "encounter_key",
    "normalize_team_name",
]
