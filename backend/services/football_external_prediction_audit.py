"""Phase F72 — External prediction auditor (Forebet vs internal splits).

This module separates **favoritism** (1X2 direction) from **scoreline /
goals projection** when reconciling Forebet against the internal data
sources (recent matches, TheStatsAPI metrics, opponent quality).

Public API
----------
``audit_opponent_strength_context(current_opponent, recent_opponents,
team_name, *, max_recent=5)`` → opponent strength comparison block.

``audit_forebet_prediction_against_match_splits(forebet, match_payload,
*, statsapi=None)`` → returns two independent verdicts:

    {
        "forebet_direction_signal": {
            "status":         "CONFIRMED | WEAK_CONFIRMED | CONFLICTED | INSUFFICIENT_DATA",
            "favorite":       "HOME | AWAY | DRAW",
            "text":           "…",
            "reason_codes":   [...],
        },
        "forebet_scoreline_audit": {
            "status":         "TRUSTED | DEGRADED | BLOCKED_FOR_AGGRESSIVE_MARKETS | INSUFFICIENT_DATA",
            "predicted_score": "3-1",
            "favorite_predicted_goals": 3,
            "text":           "…",
            "block_aggressive_overs": True,
            "reason_codes":   [...],
        },
        "opponent_strength_audit": { ... },
        "competition_context": {
            "upcoming_match_type": "official|friendly|knockout|unknown",
            "reason_codes":        [...],
        },
        "reason_codes": [aggregated],
    }

All branches are fail-soft: missing data → status="INSUFFICIENT_DATA"
with explicit reason code; we NEVER invent a verdict.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

log = logging.getLogger("external_prediction_audit")

# Tier thresholds (calibrated from publicly available 2024-2026 ratings).
# Used when ranking inputs (elo, fifa_rank, xGA) feed the tier classifier.
TIER_ELO_BANDS = (
    ("ELITE",  1900),
    ("STRONG", 1750),
    ("MEDIUM", 1550),
    ("WEAK",   0),  # everything below 1550 → WEAK
)

TIER_FIFA_BANDS = (
    # FIFA ranking is INVERSE — lower = stronger.
    ("ELITE",  10),
    ("STRONG", 35),
    ("MEDIUM", 80),
    ("WEAK",   1_000),  # weakest catch-all
)

TIER_XGA_BANDS = (
    # Average xG conceded per match. Lower xGA → stronger defence → stronger team.
    ("ELITE",  0.8),
    ("STRONG", 1.1),
    ("MEDIUM", 1.5),
    ("WEAK",   99.0),
)

TIER_RANK = {"ELITE": 4, "STRONG": 3, "MEDIUM": 2, "WEAK": 1, "UNKNOWN": 0}

# Aggressive-Over thresholds (the favorite must convert these to "trust").
HIGH_SCORE_GOALS_THRESHOLD = 3      # favorite_predicted_goals >= 3 → "high"
OFFICIAL_OVER2_RATE_LOW    = 0.25   # ≤25% means the team rarely > 2 goals
FRIENDLY_GOALS_HIGH        = 4      # >=4 goals in a friendly counts as "high-scoring"


# ─────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────
def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:  # noqa: BLE001
        try:
            m = re.search(r"-?\d+(\.\d+)?", str(v))
            if m:
                return float(m.group(0))
        except Exception:  # noqa: BLE001
            return None
        return None


def _strip(s: str) -> str:
    if not isinstance(s, str):
        return ""
    n = unicodedata.normalize("NFD", s)
    return "".join(c for c in n if unicodedata.category(c) != "Mn").lower().strip()


def _is_friendly(match: dict) -> bool:
    """Identify a friendly via either explicit ``match_type`` or the
    competition string."""
    mt = (match.get("match_type") or "").lower()
    if mt in ("friendly", "amistoso", "amistosos"):
        return True
    comp = (match.get("competition") or match.get("league") or "").lower()
    return ("amistoso" in comp or "friendly" in comp or comp == "intl")


def _classify_opponent_tier(opp: dict) -> str:
    """Return one of ELITE / STRONG / MEDIUM / WEAK / UNKNOWN."""
    if not isinstance(opp, dict):
        return "UNKNOWN"

    elo = _safe_float(opp.get("elo_rating") or opp.get("elo"))
    if elo is not None:
        for tier, threshold in TIER_ELO_BANDS:
            if elo >= threshold:
                return tier
        return "WEAK"

    fifa = _safe_float(opp.get("fifa_rank"))
    if fifa is not None:
        for tier, threshold in TIER_FIFA_BANDS:
            if fifa <= threshold:
                return tier
        return "WEAK"

    # TheStatsAPI / engine rating fields.
    strength = _safe_float(
        opp.get("recent_form_rating") or opp.get("team_strength")
        or opp.get("strength_rating")
    )
    if strength is not None:
        if strength >= 80:  return "ELITE"
        if strength >= 65:  return "STRONG"
        if strength >= 45:  return "MEDIUM"
        return "WEAK"

    # Defensive rating (engine internal): typically higher = stronger defense.
    def_rating = _safe_float(opp.get("defensive_rating"))
    if def_rating is not None:
        if def_rating >= 80:  return "ELITE"
        if def_rating >= 65:  return "STRONG"
        if def_rating >= 45:  return "MEDIUM"
        return "WEAK"

    # xGA average (lower = stronger defense → stronger team).
    xga = _safe_float(opp.get("xga_avg") or opp.get("xg_against_avg"))
    if xga is not None:
        for tier, threshold in TIER_XGA_BANDS:
            if xga <= threshold:
                return tier
        return "WEAK"

    # Goals conceded average (lower = stronger).
    gca = _safe_float(opp.get("goals_conceded_avg")
                       or opp.get("goals_against_avg"))
    if gca is not None:
        if gca <= 0.7:  return "ELITE"
        if gca <= 1.1:  return "STRONG"
        if gca <= 1.6:  return "MEDIUM"
        return "WEAK"

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────
# Opponent strength audit
# ─────────────────────────────────────────────────────────────────────
def audit_opponent_strength_context(
    current_opponent: dict,
    recent_opponents: list[dict],
    team_name: str,
    *, max_recent: int = 5,
) -> dict:
    """Compare the upcoming opponent against the favorite's last N rivals.

    Detects whether high-scoring matches happened against weaker rivals
    (an inflation signal that should degrade Forebet's aggressive
    scorelines).
    """
    out: dict[str, Any] = {
        "available":      False,
        "team":           team_name,
        "current_opponent":  (current_opponent or {}).get("name")
                              if isinstance(current_opponent, dict) else None,
        "reason_codes":   [],
    }

    if not isinstance(current_opponent, dict) or not isinstance(recent_opponents, list):
        out["reason_codes"].append("OPPONENT_STRENGTH_DATA_INSUFFICIENT")
        out["status"]    = "INSUFFICIENT_DATA"
        out["text"]      = ("No hay datos suficientes para comparar la fuerza "
                            "del rival actual.")
        return out

    sample = [o for o in recent_opponents if isinstance(o, dict)][:max_recent]
    current_tier = _classify_opponent_tier(current_opponent)
    sample_tiers = [_classify_opponent_tier(o) for o in sample]

    known_tiers = [t for t in sample_tiers if t != "UNKNOWN"]
    if current_tier == "UNKNOWN" or len(known_tiers) < 2:
        out["reason_codes"].append("OPPONENT_STRENGTH_DATA_INSUFFICIENT")
        out["status"]                          = "INSUFFICIENT_DATA"
        out["current_opponent_strength_tier"]  = current_tier
        out["recent_opponents_checked"]        = len(sample)
        out["recent_opponents_avg_strength_tier"] = (
            _mode_tier(known_tiers) if known_tiers else "UNKNOWN"
        )
        out["text"] = ("Datos de fuerza de rival insuficientes — auditoría "
                       "neutral.")
        return out

    avg_rank = sum(TIER_RANK[t] for t in known_tiers) / len(known_tiers)
    current_rank = TIER_RANK[current_tier]
    avg_tier = _tier_from_rank(avg_rank)

    if current_rank > avg_rank + 0.5:
        relationship = "STRONGER"
        out["reason_codes"].append("CURRENT_OPPONENT_STRONGER_THAN_RECENT_AVG")
    elif current_rank < avg_rank - 0.5:
        relationship = "WEAKER"
        out["reason_codes"].append("CURRENT_OPPONENT_WEAKER_THAN_RECENT_AVG")
    else:
        relationship = "SIMILAR"
        out["reason_codes"].append("CURRENT_OPPONENT_SIMILAR_TO_RECENT_AVG")

    # High-scoring friendlies vs weaker rivals heuristic.
    high_scoring_vs_weak = False
    high_scoring_count   = 0
    for s in sample:
        goals = _safe_float(s.get("goals_scored_by_team")
                              or s.get("goals_for")
                              or s.get("home_goals_if_team")
                              or s.get("away_goals_if_team"))
        if goals is None:
            continue
        if goals >= FRIENDLY_GOALS_HIGH and _is_friendly(s):
            high_scoring_count += 1
            tier = _classify_opponent_tier(s)
            if tier != "UNKNOWN" and TIER_RANK[tier] < current_rank:
                high_scoring_vs_weak = True

    goals_inflation_risk = "LOW"
    if high_scoring_vs_weak and relationship == "STRONGER":
        goals_inflation_risk = "HIGH"
        out["reason_codes"].append("FRIENDLY_GOALS_VS_WEAKER_OPPONENTS_DETECTED")
    elif high_scoring_count > 0 and relationship == "STRONGER":
        goals_inflation_risk = "MEDIUM"

    text_parts: list[str] = []
    cur_label = out["current_opponent"] or "el rival actual"
    if relationship == "STRONGER":
        text_parts.append(
            f"{cur_label} ({current_tier}) es más fuerte que el promedio "
            f"({avg_tier}) de los últimos {len(known_tiers)} rivales de "
            f"{team_name}."
        )
    elif relationship == "WEAKER":
        text_parts.append(
            f"{cur_label} ({current_tier}) es más débil que el promedio "
            f"({avg_tier}) de los últimos rivales de {team_name}."
        )
    else:
        text_parts.append(
            f"{cur_label} ({current_tier}) tiene una exigencia similar a "
            f"los últimos rivales de {team_name} ({avg_tier})."
        )
    if high_scoring_vs_weak:
        text_parts.append(
            f"Los amistosos muestran mayor producción ofensiva, pero parte "
            f"de esa producción llegó ante rivales de menor exigencia que "
            f"{cur_label}."
        )

    out.update({
        "available":      True,
        "status":         "OK",
        "current_opponent_strength_tier":     current_tier,
        "recent_opponents_checked":           len(sample),
        "recent_opponents_avg_strength_tier": avg_tier,
        "current_opponent_harder_than_recent_avg":  relationship == "STRONGER",
        "current_opponent_easier_than_recent_avg":  relationship == "WEAKER",
        "high_scoring_matches_vs_weaker_opponents": high_scoring_vs_weak,
        "high_scoring_friendlies_count":            high_scoring_count,
        "goals_inflation_risk":                     goals_inflation_risk,
        "text":                                     " ".join(text_parts),
    })
    if "OPPONENT_STRENGTH_AUDIT_USED" not in out["reason_codes"]:
        out["reason_codes"].insert(0, "OPPONENT_STRENGTH_AUDIT_USED")
    return out


def _mode_tier(tiers: list[str]) -> str:
    if not tiers:
        return "UNKNOWN"
    from collections import Counter
    return Counter(tiers).most_common(1)[0][0]


def _tier_from_rank(rank: float) -> str:
    if rank >= 3.5:  return "ELITE"
    if rank >= 2.5:  return "STRONG"
    if rank >= 1.5:  return "MEDIUM"
    if rank >= 0.5:  return "WEAK"
    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────
# Splits: official vs friendly
# ─────────────────────────────────────────────────────────────────────
def split_recent_matches_official_vs_friendly(recent: list[dict]) -> dict:
    """Partition a recent_matches list into official + friendly buckets
    and compute key aggregates per bucket.
    """
    official: list[dict] = []
    friendly: list[dict] = []
    for m in recent or []:
        if not isinstance(m, dict):
            continue
        (friendly if _is_friendly(m) else official).append(m)

    def _aggregate(bucket: list[dict]) -> dict:
        if not bucket:
            return {"count": 0,
                    "goals_for_avg":      None,
                    "goals_against_avg":  None,
                    "over_2_team_goals_rate": None}
        gfs, gas, over2 = [], [], 0
        for m in bucket:
            # Phase F72 — explicit None check (cannot use ``or`` because
            # 0 is a valid goals value but falsy in Python).
            raw_g = m.get("goals_scored_by_team")
            if raw_g is None: raw_g = m.get("goals_for")
            raw_a = m.get("goals_conceded_by_team")
            if raw_a is None: raw_a = m.get("goals_against")
            g = _safe_float(raw_g)
            a = _safe_float(raw_a)
            if g is not None:
                gfs.append(g)
                if g > 2:
                    over2 += 1
            if a is not None:
                gas.append(a)
        return {
            "count":             len(bucket),
            "goals_for_avg":     (sum(gfs)/len(gfs) if gfs else None),
            "goals_against_avg": (sum(gas)/len(gas) if gas else None),
            "over_2_team_goals_rate":
                (over2/len(bucket) if bucket else None),
        }

    return {
        "official_count":   len(official),
        "friendly_count":   len(friendly),
        "official":         _aggregate(official),
        "friendly":         _aggregate(friendly),
    }


# ─────────────────────────────────────────────────────────────────────
# Direction (1X2 / favoritism) audit
# ─────────────────────────────────────────────────────────────────────
def _favorite_from_forebet(forebet: dict) -> Optional[str]:
    pick = (forebet or {}).get("pick_1x2")
    if pick == "1":     return "HOME"
    if pick == "2":     return "AWAY"
    if pick == "X":     return "DRAW"
    # Fallback: pick the side with highest pct.
    pcs = [(forebet.get("forebet_pct_1"), "HOME"),
           (forebet.get("forebet_pct_x"), "DRAW"),
           (forebet.get("forebet_pct_2"), "AWAY")]
    pcs = [(p, s) for p, s in pcs if isinstance(p, (int, float))]
    if not pcs:
        return None
    pcs.sort(reverse=True)
    return pcs[0][1]


def audit_forebet_direction(forebet: dict, match_payload: dict,
                              *, statsapi: Optional[dict] = None) -> dict:
    """Validate Forebet's favorite with team metrics (xG, shots, SoT,
    goals, possession). Returns one of the 4 statuses."""
    out: dict[str, Any] = {
        "favorite":      _favorite_from_forebet(forebet),
        "forebet_pct":   None,
        "reason_codes":  [],
    }
    if not out["favorite"]:
        out["status"] = "INSUFFICIENT_DATA"
        out["text"]   = "Forebet no aportó un favorito claro."
        out["reason_codes"].append("FOREBET_DIRECTION_MISSING")
        return out

    fav_pct = ({"HOME": forebet.get("forebet_pct_1"),
                "DRAW": forebet.get("forebet_pct_x"),
                "AWAY": forebet.get("forebet_pct_2")}[out["favorite"]])
    out["forebet_pct"] = fav_pct

    home_team = match_payload.get("home_team") if isinstance(match_payload.get("home_team"), dict) else {}
    away_team = match_payload.get("away_team") if isinstance(match_payload.get("away_team"), dict) else {}

    def _metric(side: str, *keys) -> Optional[float]:
        bag = home_team if side == "HOME" else away_team
        for k in keys:
            v = _safe_float(bag.get(k)
                            or (statsapi or {}).get(side.lower(), {}).get(k))
            if v is not None:
                return v
        # also look at flat payload (home_xg etc.)
        prefix = "home_" if side == "HOME" else "away_"
        for k in keys:
            v = _safe_float(match_payload.get(prefix + k))
            if v is not None:
                return v
        return None

    fav  = out["favorite"]
    rival = "AWAY" if fav == "HOME" else ("HOME" if fav == "AWAY" else None)

    # When the favorite is DRAW, direction is by definition weak / neutral.
    if fav == "DRAW":
        out["status"] = "WEAK_CONFIRMED"
        out["text"]   = "Forebet inclina hacia el empate; señal de dirección débil por naturaleza."
        out["reason_codes"].append("FOREBET_DIRECTION_DRAW_FAVORED")
        return out

    metrics_checked  = 0
    metrics_confirm  = 0
    metrics_contra   = 0
    if rival:
        for key_tuple in (("xg_avg", "xg", "xg_for_avg"),
                          ("xga_avg", "xg_against_avg"),
                          ("shots_avg", "shots"),
                          ("sot_avg", "shots_on_target", "sot"),
                          ("goals_scored_l5", "goals_scored_avg"),
                          ("possession_avg",)):
            fv = _metric(fav, *key_tuple)
            rv = _metric(rival, *key_tuple)
            if fv is None or rv is None:
                continue
            metrics_checked += 1
            # For xGA / goals conceded, lower = better.
            inverted = "xga_avg" in key_tuple or "xg_against_avg" in key_tuple
            if inverted:
                if fv < rv:   metrics_confirm += 1
                elif fv > rv: metrics_contra  += 1
            else:
                if fv > rv:   metrics_confirm += 1
                elif fv < rv: metrics_contra  += 1

    if metrics_checked == 0:
        out["status"]        = "INSUFFICIENT_DATA"
        out["text"]          = (
            f"Forebet favorece a {_label_side(fav, match_payload)} "
            f"({fav_pct}%) pero no hay métricas recientes suficientes "
            "para validar la dirección."
        )
        out["reason_codes"].append("FOREBET_DIRECTION_NO_METRICS")
        return out

    confirm_rate = metrics_confirm / metrics_checked
    contra_rate  = metrics_contra  / metrics_checked

    if confirm_rate >= 0.6 and contra_rate <= 0.2:
        status = "CONFIRMED"
        text   = (f"Forebet favorece a {_label_side(fav, match_payload)} "
                  f"({fav_pct}%) y las métricas recientes lo respaldan "
                  f"(xG/SoT/goles a favor del favorito).")
    elif confirm_rate >= 0.4:
        status = "WEAK_CONFIRMED"
        text   = (f"Forebet favorece a {_label_side(fav, match_payload)} "
                  f"({fav_pct}%), pero las métricas recientes solo lo "
                  "confirman de forma moderada.")
        out["reason_codes"].append("FOREBET_FAVORITISM_WEAKENED_BY_TEAM_METRICS")
    else:
        status = "CONFLICTED"
        text   = (f"Forebet favorece a {_label_side(fav, match_payload)} "
                  f"({fav_pct}%), pero TheStatsAPI / recent form muestra "
                  "métricas inferiores al rival.")
        out["reason_codes"].append("FOREBET_DIRECTION_CONFLICTED_BY_THESTATSAPI")

    out["status"]            = status
    out["text"]               = text
    out["metrics_checked"]   = metrics_checked
    out["metrics_confirm"]   = metrics_confirm
    out["metrics_contra"]    = metrics_contra
    return out


def _label_side(side: str, match: dict) -> str:
    """Render HOME/AWAY/DRAW with the actual team name when available."""
    if side == "HOME":
        h = match.get("home_team")
        if isinstance(h, dict):  return h.get("name") or "el equipo local"
        if isinstance(h, str):   return h
        return "el equipo local"
    if side == "AWAY":
        a = match.get("away_team")
        if isinstance(a, dict):  return a.get("name") or "el equipo visitante"
        if isinstance(a, str):   return a
        return "el equipo visitante"
    return "el empate"


# ─────────────────────────────────────────────────────────────────────
# Scoreline / goals audit
# ─────────────────────────────────────────────────────────────────────
def _competition_context(match_payload: dict) -> dict:
    raw = (match_payload.get("upcoming_match_type")
            or match_payload.get("match_type")
            or "").lower()
    comp = (match_payload.get("competition") or "").lower()
    if "friendly" in raw or "amistos" in raw or "amistos" in comp:
        ctx = "friendly"
        code = "UPCOMING_MATCH_FRIENDLY_CONTEXT"
    elif ("knockout" in raw or "final" in raw or "octavos" in comp
          or "cuartos" in comp or "semifinal" in comp or "final" in comp):
        ctx = "knockout"
        code = "UPCOMING_MATCH_KNOCKOUT_CONTEXT"
    elif raw in ("league", "qualifier", "official") or comp:
        ctx = "official"
        code = "UPCOMING_MATCH_OFFICIAL_CONTEXT"
    else:
        ctx = "unknown"
        code = "UPCOMING_MATCH_UNKNOWN_CONTEXT"
    return {"upcoming_match_type": ctx, "reason_codes": [code]}


def audit_forebet_scoreline(forebet: dict, match_payload: dict,
                              *, splits: dict,
                              opponent_strength: dict,
                              competition_ctx: dict) -> dict:
    """Audit the Forebet scoreline + Over projection.

    Status decision tree:
      INSUFFICIENT_DATA  → no splits + no opponent strength
      BLOCKED_FOR_AGGRESSIVE_MARKETS → high score + low official over2 rate
                                       AND current opponent harder
      DEGRADED           → high score + opponent harder OR low official over2
      TRUSTED            → otherwise (low scoreline OR backed by data)
    """
    out: dict[str, Any] = {
        "predicted_score":          forebet.get("predicted_score"),
        "favorite_predicted_goals": None,
        "block_aggressive_overs":   False,
        "reason_codes":             [],
    }
    score = forebet.get("predicted_score") or ""
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$",
                  score.replace("\u2013", "-").replace("\u2014", "-"))
    if not m:
        out["status"] = "INSUFFICIENT_DATA"
        out["text"]   = "Forebet no aportó un marcador parseable."
        out["reason_codes"].append("FOREBET_SCORELINE_UNPARSEABLE")
        return out

    h, a = int(m.group(1)), int(m.group(2))
    fav_pick = (forebet.get("pick_1x2") or "").upper()
    fav_goals = h if fav_pick in ("1", "HOME") else (a if fav_pick in ("2", "AWAY") else max(h, a))
    out["favorite_predicted_goals"] = fav_goals

    official    = (splits or {}).get("official") or {}
    over2_rate  = official.get("over_2_team_goals_rate")
    official_n  = official.get("count") or 0

    upcoming = (competition_ctx or {}).get("upcoming_match_type")
    is_official_upcoming = upcoming in ("official", "knockout")
    is_knockout          = upcoming == "knockout"

    if is_official_upcoming:
        out["reason_codes"].append("FRIENDLY_GOAL_DATA_DOWNWEIGHTED_FOR_OFFICIAL_MATCH")

    opp_harder = bool((opponent_strength or {}).get("current_opponent_harder_than_recent_avg"))

    # Bottom-of-spec decision tree.
    if fav_goals < HIGH_SCORE_GOALS_THRESHOLD:
        out["status"] = "TRUSTED"
        out["text"]   = (f"Forebet predice marcador moderado ({score}); "
                          "no requiere degradación.")
        return out

    insufficient_data = (
        official_n < 3 and not opponent_strength.get("available", False)
    )
    if insufficient_data:
        out["status"] = "INSUFFICIENT_DATA"
        out["text"]   = (f"Forebet predice marcador agresivo ({score}), "
                          "pero faltan oficiales recientes y datos del "
                          "rival para auditar.")
        out["reason_codes"].append("FOREBET_SCORELINE_INSUFFICIENT_AUDIT_DATA")
        return out

    block_aggressive = False
    reasons_text: list[str] = []

    if over2_rate is not None and over2_rate <= OFFICIAL_OVER2_RATE_LOW \
            and is_official_upcoming:
        block_aggressive = True
        out["reason_codes"].append(
            "FOREBET_OVER_BLOCKED_BY_OFFICIAL_FORM_AND_OPPONENT_STRENGTH"
        )
        favored_label = _label_side(out_to_side(fav_pick), match_payload)
        reasons_text.append(
            f"{favored_label} solo superó los 2 goles en "
            f"{over2_rate*100:.0f}% de sus últimos {official_n} oficiales."
        )

    if opp_harder:
        out["reason_codes"].append(
            "FOREBET_SCORELINE_DEGRADED_BY_OPPONENT_STRENGTH"
        )
        reasons_text.append(
            (opponent_strength.get("text") or "").strip()
        )

    if is_knockout:
        # Knockouts already penalize aggressive overs.
        if fav_goals >= 3:
            block_aggressive = True or block_aggressive

    if block_aggressive:
        out["status"] = "BLOCKED_FOR_AGGRESSIVE_MARKETS"
        out["block_aggressive_overs"] = True
        out["text"] = (
            f"Marcador {score} degradado y Over agresivo bloqueado: "
            + " ".join([r for r in reasons_text if r])
        )
    elif opp_harder or (over2_rate is not None and over2_rate <= 0.35):
        out["status"] = "DEGRADED"
        out["text"] = (
            f"Marcador {score} degradado por exigencia del rival y/o "
            f"forma oficial: " + " ".join([r for r in reasons_text if r])
        )
    else:
        out["status"] = "TRUSTED"
        out["text"] = (f"Marcador {score} respaldado por los splits "
                        "oficiales y la calidad del rival.")
    return out


def out_to_side(pick: str) -> str:
    if pick in ("1", "HOME"):  return "HOME"
    if pick in ("2", "AWAY"):  return "AWAY"
    return "DRAW"


# ─────────────────────────────────────────────────────────────────────
# Top-level entry
# ─────────────────────────────────────────────────────────────────────
def audit_forebet_prediction_against_match_splits(
    forebet: dict, match_payload: dict,
    *, statsapi: Optional[dict] = None,
) -> dict:
    """Top-level F72 audit. Returns the dict described in the module
    docstring. Always fail-soft."""
    out: dict[str, Any] = {"reason_codes": []}
    if not isinstance(forebet, dict) or not forebet:
        out["status"] = "INSUFFICIENT_DATA"
        out["reason_codes"].append("FOREBET_PAYLOAD_MISSING")
        return out

    # Step 0 — competition context.
    competition_ctx = _competition_context(match_payload or {})
    out["competition_context"] = competition_ctx
    for c in competition_ctx["reason_codes"]:
        if c not in out["reason_codes"]:
            out["reason_codes"].append(c)

    # Step 1 — splits (official vs friendly).
    recent = (match_payload or {}).get("recent_matches") or []
    splits = split_recent_matches_official_vs_friendly(recent)
    out["splits"] = splits

    # Step 2 — opponent strength audit (favorite-side).
    fav_side = _favorite_from_forebet(forebet)
    favorite_team_dict, rival_team_dict, favorite_name = None, None, ""
    if fav_side == "HOME":
        favorite_team_dict = (match_payload or {}).get("home_team") or {}
        rival_team_dict    = (match_payload or {}).get("away_team") or {}
    elif fav_side == "AWAY":
        favorite_team_dict = (match_payload or {}).get("away_team") or {}
        rival_team_dict    = (match_payload or {}).get("home_team") or {}
    if isinstance(favorite_team_dict, dict):
        favorite_name = favorite_team_dict.get("name") or ""

    favorite_recent_opponents = (favorite_team_dict.get("recent_opponents")
                                  if isinstance(favorite_team_dict, dict)
                                  else None) or []
    opponent_strength = audit_opponent_strength_context(
        current_opponent=rival_team_dict if isinstance(rival_team_dict, dict) else {},
        recent_opponents=favorite_recent_opponents,
        team_name=favorite_name or "el favorito",
        max_recent=5,
    )
    out["opponent_strength_audit"] = opponent_strength
    for c in opponent_strength.get("reason_codes", []):
        if c not in out["reason_codes"]:
            out["reason_codes"].append(c)

    # Step 3 — direction signal.
    direction = audit_forebet_direction(forebet, match_payload or {},
                                          statsapi=statsapi)
    out["forebet_direction_signal"] = direction
    for c in direction.get("reason_codes", []):
        if c not in out["reason_codes"]:
            out["reason_codes"].append(c)

    # Step 4 — scoreline audit.
    scoreline = audit_forebet_scoreline(
        forebet, match_payload or {},
        splits=splits,
        opponent_strength=opponent_strength,
        competition_ctx=competition_ctx,
    )
    out["forebet_scoreline_audit"] = scoreline
    for c in scoreline.get("reason_codes", []):
        if c not in out["reason_codes"]:
            out["reason_codes"].append(c)

    out["available"] = True
    return out


__all__ = [
    "audit_opponent_strength_context",
    "audit_forebet_direction",
    "audit_forebet_scoreline",
    "audit_forebet_prediction_against_match_splits",
    "split_recent_matches_official_vs_friendly",
]
