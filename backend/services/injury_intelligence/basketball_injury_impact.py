"""Basketball injury impact model.

Maps injuries → numeric adjustments to:
  * team_strength
  * offense / defense / pace
  * spread / moneyline / total_points
  * fragility
  * reason_codes (explicable)

Also classifies player role (superstar / star / starter / rotation / bench /
unknown) using a hardcoded NBA registry + heuristic fallback.

All functions are pure and fail-soft.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .injury_schema import (
    ABSENT_STATUSES, UNCERTAIN_STATUSES, RESTRICTED_STATUSES,
)

# ============================================================
# Hardcoded NBA superstar / star registry (2025-2026 season)
# ============================================================
# Maintainable list — add/remove as roster moves happen. Keys are
# normalised (ASCII-lower-no-punct) so misspellings don't break lookups.
#
# Superstars: franchise face, MVP-tier impact. Out → catastrophic.
# Stars:      All-Star calibre. Out → major adjustment.
NBA_SUPERSTARS = frozenset({
    "lebronjames", "stephencurry", "lukadoncic", "giannisantetokounmpo",
    "nikolajokic", "joelembiid", "kevindurant", "jaysontatum",
    "shaigilgeousalexander", "anthonyedwards", "victorwembanyama",
    "jamesharden", "damianlillard", "devinbooker", "karltownsmonk",
    "jaylenbrown", "kawhileonard", "paulgeorge", "jimmybutler",
    "anthonydavis",
})

NBA_STARS = frozenset({
    "deronfox", "trayyoung", "daminsabonis", "juliusrandle",
    "karlanthonytowns", "jaylenbrunson", "donovan mitchell",
    "donovanmitchell", "tylerherro", "derozandedmar", "demardodemarderozan",
    "demardoanthony", "demarderozan", "jamalmurray", "michaelporterjr",
    "zachlavine", "desmondbane", "jaroncollinsworth", "jaron jackson jr",
    "jarenjacksonjr", "jamorant", "zionwilliamson", "brandoningram",
    "chrispaul", "miketconley", "rudygobert", "bamadebayo",
    "tyresemaxey", "alperenungs", "alperensengun", "scottiebarnes",
    "paolobanchero", "franzwagner", "jalentbrunson", "cademcunningham",
    "laMeloball", "lameloball", "obeitoppin", "deaaronfox",
    "jadenivey", "chetholmgren", "jalenwilliams", "jrueholiday",
    "krisxianyer", "krystapsporzingis", "kristapsporzingis",
    "derrickwhite", "alhorford", "jrue holiday", "jruerobertholiday",
})


def _player_key(name: str) -> str:
    """Normalised lookup key matching the registry format."""
    if not name:
        return ""
    import re, unicodedata
    n = unicodedata.normalize("NFKD", str(name))
    n = n.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", n.lower())


def classify_player_role(
    player_name: str,
    *,
    player_stats: Optional[dict] = None,
    hint_role: Optional[str] = None,
) -> str:
    """Best-effort role classification.

    Priority order:
      1. ``hint_role`` if explicitly provided AND in ROLE_VALUES.
      2. Hardcoded NBA registry (superstar > star).
      3. ``player_stats`` heuristic (minutes / usage / ppg).
      4. Fallback ``"unknown"``.
    """
    if hint_role in ("superstar", "star", "starter", "rotation", "bench"):
        return hint_role

    key = _player_key(player_name)
    if key in NBA_SUPERSTARS:
        return "superstar"
    if key in NBA_STARS:
        return "star"

    if isinstance(player_stats, dict):
        mpg = float(player_stats.get("mpg") or player_stats.get("minutes_per_game") or 0)
        ppg = float(player_stats.get("ppg") or player_stats.get("points_per_game") or 0)
        usage = float(player_stats.get("usage_rate") or player_stats.get("usg") or 0)
        # Heuristic ladder
        if usage >= 28 or ppg >= 25:
            return "star"
        if mpg >= 30 or ppg >= 18:
            return "starter"
        if mpg >= 18 or ppg >= 8:
            return "rotation"
        if mpg > 0:
            return "bench"
    return "unknown"


# ============================================================
# Impact scoring — per-role base adjustments (team-strength points)
# ============================================================
# Values are NEGATIVE (penalty when player is out). Conservative midpoints
# from the user spec.
_BASE_OUT_PENALTY = {
    "superstar": -15,
    "star":      -10,
    "starter":   -6,
    "rotation":  -3,
    "bench":     -1,
    "unknown":   -2,
}

_BASE_QUESTIONABLE_PENALTY = {
    "superstar": -7,
    "star":      -5,
    "starter":   -3,
    "rotation":  -1,
    "bench":      0,
    "unknown":   -1,
}

_BASE_MIN_RESTRICTION_PENALTY = {
    "superstar": -8,
    "star":      -6,
    "starter":   -3,
    "rotation":  -1,
    "bench":      0,
    "unknown":   -1,
}

_BASE_PROBABLE_PENALTY = {
    "superstar": -1,
    "star":      -1,
    "starter":    0,
    "rotation":   0,
    "bench":      0,
    "unknown":    0,
}


def _position_is_offensive(position: str) -> bool:
    pos = (position or "").upper()
    return pos in ("PG", "SG", "G", "SF", "GF")


def _position_is_defensive(position: str) -> bool:
    pos = (position or "").upper()
    return pos in ("C", "PF", "F", "FC", "BIG")


def _position_is_point_guard(position: str) -> bool:
    return (position or "").upper() in ("PG", "G")


def _position_is_rim_protector(position: str, role: str) -> bool:
    pos = (position or "").upper()
    return pos in ("C", "FC") or (pos == "PF" and role in ("superstar", "star"))


def calculate_basketball_injury_impact(
    team_profile: dict,
    injuries: Iterable[dict],
    player_stats: Optional[dict] = None,
) -> dict:
    """Compute the team-side basketball injury impact block.

    Args:
        team_profile: optional ``{"team_id", "team_name", ...}``. Used for
            provenance only — the score itself depends on `injuries`.
        injuries: iterable of normalised injury records.
        player_stats: optional ``{player_name: {mpg, ppg, usage_rate, ...}}``
            used by the heuristic role classifier.

    Returns:
        A dict with the keys documented in the spec under
        ``basketball_injury_score`` PLUS a ``team_injury_impact`` block
        and ``reason_codes``.
    """
    injuries = list(injuries or [])
    out_block = {
        "team_strength_adjustment":  0,
        "offense_adjustment":        0,
        "defense_adjustment":        0,
        "pace_adjustment":           0,
        "spread_adjustment":         0,
        "moneyline_adjustment":      0,
        "total_points_adjustment":   0,
        "fragility_adjustment":      0,
        "reason_codes":              [],
    }
    team_block = {
        "total_absences":                 0,
        "star_absences":                  0,
        "starter_absences":               0,
        "questionable_key_players":       0,
        "minutes_restriction_key_players": 0,
        "team_strength_adjustment":       0,
        "impact_score":                   0,
        "impact_tier":                    "LOW",
        "reason_codes":                   [],
        "summary":                        "",
    }
    if not injuries:
        return {"basketball_injury_score": out_block, "team_injury_impact": team_block}

    reason_codes: set[str] = set()
    starter_outs: list[dict] = []
    star_outs: list[dict] = []
    superstar_outs: list[dict] = []
    offensive_outs: list[dict] = []
    defensive_outs: list[dict] = []
    questionable_key: list[dict] = []
    restricted_key:   list[dict] = []
    total_strength_delta = 0
    total_offense_delta  = 0
    total_defense_delta  = 0
    total_pace_delta     = 0

    # Score every injury.
    for inj in injuries:
        if not isinstance(inj, dict):
            continue
        name = inj.get("player_name") or ""
        status = inj.get("status") or "unknown"
        position = inj.get("position") or ""
        # Classify role (or trust source-provided role if it's reasonable).
        role = classify_player_role(
            name,
            player_stats=(player_stats or {}).get(_player_key(name)) if player_stats else None,
            hint_role=inj.get("role"),
        )
        # Update record with classified role (for downstream UI).
        inj["role"] = role

        is_offensive = _position_is_offensive(position) or role in ("superstar", "star") and not _position_is_defensive(position)
        is_defensive = _position_is_defensive(position)
        is_point_guard = _position_is_point_guard(position) and role in ("superstar", "star", "starter")
        is_rim_protector = _position_is_rim_protector(position, role)

        if status in ABSENT_STATUSES:
            penalty = _BASE_OUT_PENALTY.get(role, -2)
            total_strength_delta += penalty
            if is_offensive:
                total_offense_delta += int(round(penalty * 0.7))
                offensive_outs.append(inj)
            if is_defensive:
                total_defense_delta += int(round(penalty * 0.6))
                defensive_outs.append(inj)
            if is_point_guard:
                total_pace_delta -= 2
                total_offense_delta -= 2
                reason_codes.add("STARTING_POINT_GUARD_OUT")
            if is_rim_protector:
                reason_codes.add("RIM_PROTECTOR_OUT")
                total_defense_delta -= 2
            if role == "superstar":
                superstar_outs.append(inj)
                reason_codes.add("SUPERSTAR_OUT")
            elif role == "star":
                star_outs.append(inj)
                reason_codes.add("STAR_PLAYER_OUT")
            elif role == "starter":
                starter_outs.append(inj)
            inj["impact_score"] = abs(penalty)
            inj["impact_tier"]  = _tier_from_penalty(abs(penalty))
            inj["impact_reason_codes"] = _impact_reason_codes_for(role, position)
        elif status in UNCERTAIN_STATUSES:
            if role in ("superstar", "star", "starter"):
                questionable_key.append(inj)
                penalty = _BASE_QUESTIONABLE_PENALTY.get(role, -1)
                total_strength_delta += penalty
                reason_codes.add("QUESTIONABLE_STAR_RISK")
                inj["impact_score"] = abs(penalty)
                inj["impact_tier"]  = _tier_from_penalty(abs(penalty))
                inj["impact_reason_codes"] = ["QUESTIONABLE_KEY_PLAYER"]
            else:
                inj["impact_score"] = 0
                inj["impact_tier"]  = "LOW"
                inj["impact_reason_codes"] = []
        elif status in RESTRICTED_STATUSES:
            if role in ("superstar", "star", "starter"):
                restricted_key.append(inj)
                penalty = _BASE_MIN_RESTRICTION_PENALTY.get(role, -1)
                total_strength_delta += penalty
                reason_codes.add("MINUTES_RESTRICTION_KEY_PLAYER")
                inj["impact_score"] = abs(penalty)
                inj["impact_tier"]  = _tier_from_penalty(abs(penalty))
                inj["impact_reason_codes"] = ["MINUTES_RESTRICTION"]
            else:
                inj["impact_score"] = 0
                inj["impact_tier"]  = "LOW"
                inj["impact_reason_codes"] = []
        elif status == "probable":
            inj["impact_score"] = 1 if role in ("superstar", "star") else 0
            inj["impact_tier"]  = "LOW"
            inj["impact_reason_codes"] = ["PROBABLE_WARNING"] if role in ("superstar", "star") else []
            total_strength_delta += _BASE_PROBABLE_PENALTY.get(role, 0)
        else:
            inj["impact_score"] = 0
            inj["impact_tier"]  = "LOW"
            inj["impact_reason_codes"] = []

    # Accumulation penalties.
    extra_strength = 0
    if len(starter_outs) + len(star_outs) + len(superstar_outs) >= 3:
        extra_strength -= 6
        reason_codes.add("MULTIPLE_STARTERS_OUT")
    elif len(starter_outs) + len(star_outs) + len(superstar_outs) == 2:
        extra_strength -= 3
        reason_codes.add("MULTIPLE_STARTERS_OUT")
    if len(defensive_outs) >= 2:
        total_defense_delta -= 4
        reason_codes.add("DEFENSIVE_ANCHOR_OUT")
    if len(offensive_outs) >= 2:
        total_offense_delta -= 4
        reason_codes.add("BENCH_DEPTH_THIN")
    if superstar_outs and (starter_outs or star_outs):
        extra_strength -= 5
        reason_codes.add("INJURY_CLUSTER_HIGH")

    total_strength_delta += extra_strength

    # Cap strength at -25 to avoid runaway. UI prefers a single big chip anyway.
    if total_strength_delta < -25:
        total_strength_delta = -25

    # Translate strength delta → market adjustments.
    spread_adjustment = int(round(total_strength_delta * 0.6))
    moneyline_adjustment = int(round(total_strength_delta * 0.5))
    total_points_adjustment = int(round((total_offense_delta + total_defense_delta) * 0.4))
    fragility_adjustment = min(15, max(0, abs(total_strength_delta) // 2))

    out_block["team_strength_adjustment"]  = total_strength_delta
    out_block["offense_adjustment"]        = total_offense_delta
    out_block["defense_adjustment"]        = total_defense_delta
    out_block["pace_adjustment"]           = total_pace_delta
    out_block["spread_adjustment"]         = spread_adjustment
    out_block["moneyline_adjustment"]      = moneyline_adjustment
    out_block["total_points_adjustment"]   = total_points_adjustment
    out_block["fragility_adjustment"]      = fragility_adjustment
    out_block["reason_codes"]              = sorted(reason_codes)

    # team_injury_impact block (the higher-level summary used by the UI panel).
    team_block["total_absences"]              = len(
        [i for i in injuries if i.get("status") in ABSENT_STATUSES]
    )
    team_block["star_absences"]               = len(star_outs) + len(superstar_outs)
    team_block["starter_absences"]            = len(starter_outs)
    team_block["questionable_key_players"]    = len(questionable_key)
    team_block["minutes_restriction_key_players"] = len(restricted_key)
    team_block["team_strength_adjustment"]    = total_strength_delta
    team_block["impact_score"]                = abs(total_strength_delta)
    team_block["impact_tier"]                 = _tier_from_penalty(abs(total_strength_delta))
    team_block["reason_codes"]                = sorted(reason_codes)
    team_block["summary"]                     = _build_team_summary(
        superstar_outs, star_outs, starter_outs,
        questionable_key, restricted_key,
    )

    return {"basketball_injury_score": out_block, "team_injury_impact": team_block}


def _tier_from_penalty(abs_pen: float) -> str:
    if abs_pen >= 14:
        return "CRITICAL"
    if abs_pen >= 8:
        return "HIGH"
    if abs_pen >= 4:
        return "MEDIUM"
    return "LOW"


def _impact_reason_codes_for(role: str, position: str) -> list[str]:
    codes: list[str] = []
    if role == "superstar":
        codes.append("SUPERSTAR_OUT")
    elif role == "star":
        codes.append("STAR_PLAYER_OUT")
    elif role == "starter":
        codes.append("STARTER_OUT")
    if _position_is_point_guard(position) and role in ("superstar", "star", "starter"):
        codes.append("STARTING_POINT_GUARD_OUT")
    if _position_is_rim_protector(position, role):
        codes.append("RIM_PROTECTOR_OUT")
    return codes


def _build_team_summary(
    superstars: list[dict], stars: list[dict], starters: list[dict],
    questionable: list[dict], restricted: list[dict],
) -> str:
    parts: list[str] = []
    if superstars:
        names = ", ".join(i.get("player_name", "?") for i in superstars[:2])
        parts.append(f"Superestrella fuera: {names}")
    if stars:
        names = ", ".join(i.get("player_name", "?") for i in stars[:2])
        parts.append(f"Estrella fuera: {names}")
    if starters:
        parts.append(f"{len(starters)} titular(es) fuera")
    if questionable:
        parts.append(f"{len(questionable)} clave(s) en duda")
    if restricted:
        parts.append(f"{len(restricted)} con restricción de minutos")
    return "; ".join(parts) if parts else ""


__all__ = [
    "calculate_basketball_injury_impact",
    "classify_player_role",
    "NBA_SUPERSTARS",
    "NBA_STARS",
]
