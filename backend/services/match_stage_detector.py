"""Competition-stage / match-importance detector.

Inspects a match's metadata (league name, round, name, custom context) and
returns a structured classification of where the match sits in its
competition — final / semifinal / playoff / regular league — plus a derived
`pressure_state` used downstream by the motivation engine.

Why this exists:
    Standings-based motivation (relegation, qualification race) fails for
    matches whose importance comes from the STAGE itself (a cup final is
    always max motivation regardless of league position).

Public API:
    detect_match_stage(match) -> dict with keys:
        competition_stage     str   one of {final, semifinal, quarterfinal,
                                            round_of_16, playoff, group_stage,
                                            league, unknown}
        match_importance      str   maximum | high | normal | low | unknown
        is_knockout           bool
        is_final              bool
        is_two_legged_tie     bool
        leg                   1|2|None
        aggregate_score       str|None  (e.g. "2-1") if obviously parseable
        competition_type      str   league | domestic_cup | continental_cup
                                    | international_tournament | unknown
        pressure_state        str   FINAL | KNOCKOUT_HIGH_PRESSURE
                                    | LEAGUE_URGENCY | NORMAL_LEAGUE
                                    | LOW_STAKES
        evidence              list[str]  the keywords/sources that triggered
                                         the classification (for transparency)

All matching is case-insensitive, accent-insensitive, and tolerant of the
quirky formats different feeds use ("Final - 1st Leg", "Semifinal Ida",
"Octavos de Final", "Knockout Round Play-offs").
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ── Keyword maps ────────────────────────────────────────────────────────────
# Order matters: longer / more specific patterns must come first so that
# "final" doesn't accidentally win over "semi-final".
_FINAL_PATTERNS = [
    r"\bgrand\s*final\b",
    r"\bcup\s*final\b",
    r"\bchampionship\s*final\b",
    r"\bfinalissima\b",
    r"\bgran\s*final\b",
    r"\bfinale\b",
    r"\bfinal\b",
]

_SEMI_PATTERNS = [
    r"\bsemi[-\s]?finals?\b",
    r"\bsemifinales?\b",
]

_QUARTER_PATTERNS = [
    r"\bquarter[-\s]?finals?\b",
    r"\bquarterfinals?\b",
    r"\bcuartos\s*(de\s*final)?\b",
]

_R16_PATTERNS = [
    r"\bround\s*of\s*16\b",
    r"\b1\/8\s*finals?\b",
    r"\boctavos\s*(de\s*final)?\b",
    r"\beighth[-\s]?finals?\b",
]

_PLAYOFF_PATTERNS = [
    r"\bplay[-\s]?offs?\b",
    r"\brepechaje\b",
    r"\bliguilla\b",
    r"\bpromotion\s*play[-\s]?off\b",
    r"\brelegation\s*play[-\s]?off\b",
    r"\bpromocion\b",
    r"\bdescenso\s*directo\b",
]

_GENERIC_KNOCKOUT_PATTERNS = [
    r"\bknockout\b",
    r"\beliminations?\b",
    r"\beliminator[ia]+\b",
    r"\bdirect\s*elimination\b",
]

_GROUP_STAGE_PATTERNS = [
    r"\bgroup\s*stage\b",
    r"\bgroup\s*[a-h](\b|\s)",
    r"\bphase\s*de\s*groupes\b",
    r"\bfase\s*de\s*grupos\b",
]

_REGULAR_LEAGUE_PATTERNS = [
    r"\bregular\s*season\b",
    r"\bmatchday\s*\d+\b",
    r"\bjornada\s*\d+\b",
    r"\bgiornata\s*\d+\b",
    r"\bspieltag\s*\d+\b",
    r"\bj\d+\b",
    r"\bweek\s*\d+\b",
]

_LEG_1_PATTERNS = [
    r"\b1st\s*leg\b",
    r"\bfirst\s*leg\b",
    r"\bida\b",
    r"\baller\b",
    r"\bandata\b",
    r"\baller\s*retour\b",
]

_LEG_2_PATTERNS = [
    r"\b2nd\s*leg\b",
    r"\bsecond\s*leg\b",
    r"\bvuelta\b",
    r"\bretour\b",
    r"\britorno\b",
]

# Cup / continental competition names — used to flag the competition TYPE
# even when round is missing. Includes well-known cups across regions.
_DOMESTIC_CUP_HINTS = [
    "fa cup", "efl cup", "carabao cup", "league cup",
    "copa del rey",
    "coppa italia",
    "dfb-pokal", "dfb pokal",
    "coupe de france",
    "copa mx",
    "copa argentina",
    "copa brasil", "copa do brasil",
    "taca de portugal",
]

_CONTINENTAL_CUP_HINTS = [
    "uefa champions league", "champions league",
    "uefa europa league", "europa league",
    "uefa conference league", "conference league",
    "copa libertadores", "libertadores",
    "copa sudamericana", "sudamericana",
    "concacaf champions",
    "afc champions",
    "caf champions",
]

_INTERNATIONAL_HINTS = [
    "fifa world cup", "world cup", "copa mundial", "mundial",
    "uefa european championship", "eurocopa", "uefa euro", "european championship",
    "copa america", "copa américa",
    "concacaf gold cup", "gold cup", "copa de oro",
    "fifa club world cup", "club world cup",
]


# ── Helpers ────────────────────────────────────────────────────────────────
def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    return _strip_accents(str(s)).lower().strip()


def _any_match(text: str, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _aggregate_score(text: str) -> Optional[str]:
    """Pick up patterns like 'agg 2-1', 'aggregate 3-3', 'global 1-2'."""
    m = re.search(
        r"\b(?:agg(?:regate)?|global|gesamt|globale|aggregato)\s*[:\-]?\s*(\d+\s*[-:]\s*\d+)\b",
        text, flags=re.IGNORECASE,
    )
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).replace(":", "-")


# ── Public API ─────────────────────────────────────────────────────────────
def detect_match_stage(match: dict) -> dict:
    """Classify the competition stage / importance of a single match.

    Reads (any may be missing):
        match.league, match.round, match.tournament, match.stage, match.name,
        match.custom_research_context
    Plus already-annotated competition metadata if present:
        match.competition_canonical_name, match.competition_type

    Returns a structured dict (see module docstring).
    """
    # Aggregate every textual signal into one searchable haystack.
    haystack_parts: list[str] = []
    for key in ("round", "tournament", "stage", "name", "title",
                "custom_research_context", "competition_round"):
        v = match.get(key) if isinstance(match, dict) else None
        if v:
            haystack_parts.append(str(v))
    league = match.get("league") or match.get("competition_canonical_name") or ""
    if league:
        haystack_parts.append(str(league))

    raw_text = " | ".join(haystack_parts)
    text = _normalize(raw_text)
    evidence: list[str] = []

    # ── Stage detection (specific → general) ────────────────────────────
    stage = "unknown"
    is_final = False
    is_knockout = False

    if (hit := _any_match(text, _SEMI_PATTERNS)):
        stage = "semifinal"
        is_knockout = True
        evidence.append(f"semi:{hit}")
    elif (hit := _any_match(text, _QUARTER_PATTERNS)):
        stage = "quarterfinal"
        is_knockout = True
        evidence.append(f"qf:{hit}")
    elif (hit := _any_match(text, _R16_PATTERNS)):
        stage = "round_of_16"
        is_knockout = True
        evidence.append(f"r16:{hit}")
    elif (hit := _any_match(text, _FINAL_PATTERNS)):
        stage = "final"
        is_final = True
        is_knockout = True
        evidence.append(f"final:{hit}")
    elif (hit := _any_match(text, _PLAYOFF_PATTERNS)):
        stage = "playoff"
        is_knockout = True
        evidence.append(f"playoff:{hit}")
    elif (hit := _any_match(text, _GENERIC_KNOCKOUT_PATTERNS)):
        stage = "playoff"
        is_knockout = True
        evidence.append(f"knockout:{hit}")
    elif (hit := _any_match(text, _GROUP_STAGE_PATTERNS)):
        stage = "group_stage"
        evidence.append(f"group:{hit}")
    elif (hit := _any_match(text, _REGULAR_LEAGUE_PATTERNS)):
        stage = "league"
        evidence.append(f"league:{hit}")

    # ── Two-legged tie detection ────────────────────────────────────────
    leg: Optional[int] = None
    if (hit := _any_match(text, _LEG_2_PATTERNS)):
        leg = 2
        evidence.append(f"leg2:{hit}")
    elif (hit := _any_match(text, _LEG_1_PATTERNS)):
        leg = 1
        evidence.append(f"leg1:{hit}")
    is_two_legged = leg is not None
    aggregate_score = _aggregate_score(raw_text)

    # ── Competition type ────────────────────────────────────────────────
    competition_type = "unknown"
    league_norm = _normalize(league)
    explicit_type = match.get("competition_type")
    if explicit_type in ("league", "domestic_cup", "continental_cup", "international_tournament", "cup", "continental", "international"):
        # Translate from football_competitions.py vocabulary
        translation = {
            "league": "league", "cup": "domestic_cup",
            "continental": "continental_cup",
            "international": "international_tournament",
            "domestic_cup": "domestic_cup",
            "continental_cup": "continental_cup",
            "international_tournament": "international_tournament",
        }
        competition_type = translation[explicit_type]
    else:
        if any(h in league_norm for h in _DOMESTIC_CUP_HINTS):
            competition_type = "domestic_cup"
        elif any(h in league_norm for h in _CONTINENTAL_CUP_HINTS):
            competition_type = "continental_cup"
        elif any(h in league_norm for h in _INTERNATIONAL_HINTS):
            competition_type = "international_tournament"
        elif stage in ("league", "group_stage"):
            competition_type = "league"

    # ── Importance + pressure_state ─────────────────────────────────────
    if is_final:
        match_importance = "maximum"
        pressure_state = "FINAL"
    elif stage in ("semifinal", "quarterfinal", "round_of_16", "playoff"):
        match_importance = "high"
        pressure_state = "KNOCKOUT_HIGH_PRESSURE"
    elif stage == "group_stage" and competition_type in ("continental_cup", "international_tournament"):
        # Group-stage of major tournaments still carries above-normal weight
        match_importance = "high"
        pressure_state = "KNOCKOUT_HIGH_PRESSURE"
    elif stage in ("league", "unknown"):
        # League urgency is determined downstream by standings; default normal
        match_importance = "normal"
        pressure_state = "NORMAL_LEAGUE"
    else:
        match_importance = "normal"
        pressure_state = "NORMAL_LEAGUE"

    return {
        "competition_stage": stage,
        "match_importance": match_importance,
        "is_knockout": is_knockout,
        "is_final": is_final,
        "is_two_legged_tie": is_two_legged,
        "leg": leg,
        "aggregate_score": aggregate_score,
        "competition_type": competition_type,
        "pressure_state": pressure_state,
        "evidence": evidence,
    }


def is_high_pressure(stage_info: dict) -> bool:
    """Convenience predicate used by the post-LLM validation guard."""
    return bool(stage_info) and stage_info.get("pressure_state") in (
        "FINAL", "KNOCKOUT_HIGH_PRESSURE",
    )


def is_final(stage_info: dict) -> bool:
    return bool(stage_info) and stage_info.get("is_final") is True
