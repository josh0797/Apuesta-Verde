"""F94.2 — FIFA World Cup alias detection.

Single source of truth for "is this fixture a FIFA World Cup match?".
Used by:
  * services.football_live_visibility — bypass priority filters so the
    World Cup can NEVER be classified as EXOTIC / LOW_PRIORITY / hidden.
  * services.football_live_aggregator (fallback path) — pull World Cup
    fixtures from TheStatsAPI when API-Football misses them.
  * tests — alias coverage across ES / EN / PT / FR variants.

Detection is intentionally permissive: any league name that contains one
of the canonical aliases (after accent + lowercase normalisation) is
considered a World Cup. We DO NOT match qualifying tournaments here —
those are handled by ``football_competitions`` under a different bucket
(e.g. ``World Cup Qualifying CONMEBOL``).

Public API
----------
* :data:`WORLD_CUP_ALIASES` — frozenset of canonical alias substrings.
* :func:`is_world_cup` — bool detector.
* :func:`normalize_world_cup_league_name` — returns the canonical
  ``"FIFA World Cup"`` string when the input matches, else ``None``.

Fail-soft: every helper accepts ``None`` / non-str without throwing.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


# ── Canonical alias set (case + accent insensitive after _normalize) ─
# Order is irrelevant for the detector but kept readable for the user.
# Each entry must already be normalised (lowercase, no diacritics) so
# the in-memory comparison stays a pure substring test.
WORLD_CUP_ALIASES: frozenset[str] = frozenset({
    # English
    "fifa world cup",
    "world cup",
    "fifa wc",
    # Spanish
    "copa mundial",
    "copa mundial de futbol",
    "copa del mundo",
    "mundial fifa",
    # Portuguese
    "copa do mundo",
    "copa do mundo fifa",
    # French
    "coupe du monde",
    "coupe du monde fifa",
    # German
    "fussball weltmeisterschaft",
    "weltmeisterschaft",
    # Italian
    "coppa del mondo",
    "mondiali fifa",
    "campionato del mondo",
})

# ── Negative guards: aliases that LOOK like World Cup but must NOT
#    trigger the bypass. Qualifying / age-group / women competitions
#    are real competitions on their own merits and are handled by the
#    standard tier ladder (some are tier_2, some tier_3). Keeping them
#    OUT of the World Cup bypass prevents false positives.
_NEGATIVE_TOKENS: tuple[str, ...] = (
    "qualif",       # World Cup Qualifying  (any confederation)
    "qualifying",
    "eliminator",   # Eliminatorias (ES qualifying)
    "eliminatoria",
    "u-17", "u17",
    "u-19", "u19",
    "u-20", "u20",
    "u-21", "u21",
    "u-23", "u23",
    "women",
    "femen",        # femenina / feminine / feminin
    "club world cup",       # FIFA Club World Cup → separate tier_2
    "club worldcup",
)


_ACCENT_RE = re.compile(r"[\u0300-\u036f]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(s: Optional[str]) -> str:
    """Lowercase + strip diacritics + collapse whitespace. Fail-soft."""
    if not isinstance(s, str):
        return ""
    n = unicodedata.normalize("NFKD", s)
    n = _ACCENT_RE.sub("", n)
    n = n.lower()
    n = _WHITESPACE_RE.sub(" ", n).strip()
    return n


def _has_negative_token(norm: str) -> bool:
    """Return True if ``norm`` mentions a qualifying / youth / women
    World Cup that must NOT receive the senior-WC bypass."""
    return any(tok in norm for tok in _NEGATIVE_TOKENS)


def is_world_cup(
    league_name: Optional[str],
    country: Optional[str] = None,
) -> bool:
    """Return True if ``league_name`` (optionally cross-checked against
    ``country``) is the senior FIFA World Cup tournament.

    Detection rules:
      * Normalise the league name (lowercase, no diacritics).
      * Reject if the name contains a *negative* token (qualifying,
        women, U-XX, club world cup, etc.).
      * Accept if it contains ANY alias from :data:`WORLD_CUP_ALIASES`.
      * ``country`` is checked only as a corroborating signal: when the
        API ships ``country="World"`` together with a generic name like
        ``"World Cup"`` the detector still fires (already covered by
        alias match). We do NOT *require* ``country == "World"`` to
        accept — some feeds ship ``country=""`` or omit it.
    """
    norm_name = _normalize(league_name)
    if not norm_name:
        return False
    if _has_negative_token(norm_name):
        return False
    for alias in WORLD_CUP_ALIASES:
        if alias in norm_name:
            return True
    # Last-resort heuristic: country == "World" combined with the
    # ambiguous "mundial" token (Spanish/Italian shorthand) is enough
    # context to qualify it as the senior tournament.
    if _normalize(country) == "world" and "mundial" in norm_name:
        return True
    return False


def normalize_world_cup_league_name(
    league_name: Optional[str],
    country: Optional[str] = None,
) -> Optional[str]:
    """Return the canonical string ``"FIFA World Cup"`` when the input
    matches the senior WC; otherwise ``None``.

    This is the helper to call when persisting normalised league names
    for cross-provider deduplication.
    """
    if is_world_cup(league_name, country=country):
        return "FIFA World Cup"
    return None


__all__ = [
    "WORLD_CUP_ALIASES",
    "is_world_cup",
    "normalize_world_cup_league_name",
]
