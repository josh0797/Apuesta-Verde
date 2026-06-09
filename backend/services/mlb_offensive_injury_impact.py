"""MLB Offensive Injury Impact Score.

Measures whether the injured players on a team's IL are ACTUALLY
important offensive contributors. The classic engine treated all
injuries equally ("52 players injured") which is meaningless — a 4th
outfielder hitting .220 and a 35-HR cleanup hitter are not the same
loss.

This module:
    1. Ranks each team's roster by a composite offensive score.
    2. Identifies the top 5 offensive contributors per team.
    3. Checks how many of those top 5 are injured / unavailable.
    4. Returns a 0-100 ``offensive_injury_score`` per team, plus a
       per-matchup imbalance summary.

Composite player offensive score weights (sum = 100%):
    OPS / wRC+  : 35%
    Runs + RBI  : 25%
    HR + XBH    : 20%
    OBP         : 10%
    PA / lineup : 10%

Penalty curve:
    missing 0 top-5 → score 0  (no penalty)
    missing 1 top-5 → 15-25  (small)
    missing 2 top-5 → 35-50  (moderate)
    missing 3 top-5 → 55-70  (strong)
    missing 4+      → 75-90  (severe)

Hard rules:
    • Pitchers and bench-only injuries NEVER trigger an offensive
      penalty. We only consider players whose composite offensive
      score puts them in the team's top 5.
    • Players listed both in the roster AND the injury list are NOT
      double-counted — IL membership wins.
    • Fail-soft: missing stats fall back to OPS + Runs + HR.
    • The module is PURE (no DB / HTTP), all data is injected.

Matchup-level interpretation:
    Both teams depleted (>= MEDIUM) → favor Under.
    One team depleted, the other healthy → small lean toward the
      healthier team for ML / Run Line (engine pick polarity NEVER
      flipped automatically).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

ENGINE_VERSION = "mlb_offensive_injury_impact.1"

# ── Reason codes ────────────────────────────────────────────────────
RC_OFFENSIVE_INJURY_IMPACT_USED        = "OFFENSIVE_INJURY_IMPACT_USED"
RC_TOP_OFFENSIVE_PLAYER_MISSING        = "TOP_OFFENSIVE_PLAYER_MISSING"
RC_MULTIPLE_TOP5_BATS_MISSING          = "MULTIPLE_TOP5_BATS_MISSING"
RC_HIGH_RUN_CREATION_LOST              = "HIGH_RUN_CREATION_LOST"
RC_BOTH_TEAMS_OFFENSIVELY_DEPLETED     = "BOTH_TEAMS_OFFENSIVELY_DEPLETED"
RC_INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM = "INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM"
RC_UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES = "UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES"
RC_PITCHER_ONLY_INJURIES_NO_PENALTY    = "PITCHER_ONLY_INJURIES_NO_PENALTY"
RC_DATA_INCOMPLETE_FALLBACK_USED       = "DATA_INCOMPLETE_FALLBACK_USED"

# ── Impact buckets ──────────────────────────────────────────────────
BUCKET_LOW    = "LOW"
BUCKET_MEDIUM = "MEDIUM"
BUCKET_HIGH   = "HIGH"

# ── Position whitelist for "offensive" players ──────────────────────
# Anything else (P, SP, RP, CL) is filtered out before ranking.
OFFENSIVE_POSITIONS = {
    "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF",
    "OF", "DH", "IF", "UT", "UTL",
}


# ─────────────────────────────────────────────────────────────────────
# Math primitives
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _is_pitcher(player: dict) -> bool:
    """Identify pitchers via position field. Both 'P' and 'SP/RP' work.
    The two-way Ohtani case: if a player has hitting stats AND batting
    PA > 50, we DON'T classify as pitcher even if position contains P."""
    pos = (player.get("position") or player.get("pos") or "").upper()
    if not pos:
        return False
    pa = _safe(player.get("pa")) or _safe(player.get("plate_appearances")) or 0
    # Two-way exception: significant batting volume → not a pitcher.
    if pa >= 50:
        return False
    return pos.startswith("P") or pos in {"SP", "RP", "CL"}


def _is_offensive_role(player: dict) -> bool:
    pos = (player.get("position") or player.get("pos") or "").upper()
    if not pos:
        # If no position is provided but the player has batting stats,
        # treat as offensive. Otherwise filter out.
        pa = _safe(player.get("pa")) or _safe(player.get("plate_appearances")) or 0
        return pa >= 50
    if _is_pitcher(player):
        return False
    # Multi-position support (e.g. "P/DH", "OF/DH", "1B-3B"). The player
    # is offensive if ANY part of the composite position is an offensive
    # slot. This is critical for two-way players like Ohtani whose
    # primary listed position can be "P" but who also hit as "DH".
    parts = []
    for chunk in pos.split("/"):
        parts.extend(chunk.split("-"))
    parts = [p.strip() for p in parts if p.strip()]
    for part in parts:
        if part in OFFENSIVE_POSITIONS or part in {"INF", "OUT"}:
            return True
    # Last-resort fallback: significant batting volume → offensive.
    pa = _safe(player.get("pa")) or _safe(player.get("plate_appearances")) or 0
    return pa >= 50


# ─────────────────────────────────────────────────────────────────────
# Composite offensive score
# ─────────────────────────────────────────────────────────────────────
def _composite_offensive_score(player: dict) -> tuple[float, dict]:
    """Return a 0-100 composite offensive score for a player.

    Uses OPS (or wRC+ when available) as the headline metric, plus
    counting stats (R, RBI, HR, XBH), OBP, and PA volume.

    Returns ``(score, debug)``. Score is clamped to [0, 100].
    """
    ops    = _safe(player.get("ops"))
    wrcp   = _safe(player.get("wrc_plus") or player.get("wRC+"))
    runs   = _safe(player.get("runs") or player.get("r"))
    rbi    = _safe(player.get("rbi"))
    hr     = _safe(player.get("hr") or player.get("home_runs"))
    xbh    = _safe(player.get("xbh") or player.get("extra_base_hits"))
    obp    = _safe(player.get("obp"))
    pa     = _safe(player.get("pa") or player.get("plate_appearances"))

    used_fallback = False

    # Headline metric (35%): wRC+ if available, else OPS, else fallback.
    if wrcp is not None:
        # wRC+ 100 = avg. Normalize so 60 → 0, 100 → 35, 160 → 70.
        # Saturates at wRC+ 180 = 100% of the 35-point slice.
        head = _clamp((wrcp - 60) / 120.0) * 35.0
    elif ops is not None:
        # OPS .600 = 0, .800 = ~20, 1.000 = 35.
        head = _clamp((ops - 0.600) / 0.400) * 35.0
    elif runs is not None:
        head = _clamp((runs - 20) / 80.0) * 35.0
        used_fallback = True
    else:
        head = 0.0
        used_fallback = True

    # Run creation block (25%): runs + RBI normalized vs. ~80 each.
    if runs is not None and rbi is not None:
        rc = _clamp(((runs / 100.0) + (rbi / 100.0)) / 2.0) * 25.0
    elif runs is not None:
        rc = _clamp(runs / 100.0) * 25.0
    elif rbi is not None:
        rc = _clamp(rbi / 100.0) * 25.0
    else:
        rc = 0.0

    # Power block (20%): HR + XBH. HR scaled vs ~35, XBH vs ~70.
    if hr is not None and xbh is not None:
        pwr = _clamp(((hr / 40.0) + (xbh / 80.0)) / 2.0) * 20.0
    elif hr is not None:
        pwr = _clamp(hr / 35.0) * 20.0
    elif xbh is not None:
        pwr = _clamp(xbh / 70.0) * 20.0
    else:
        pwr = 0.0

    # On-base block (10%): OBP .300 = 0, .400 = 10.
    if obp is not None:
        ob = _clamp((obp - 0.300) / 0.100) * 10.0
    else:
        ob = 0.0

    # Volume block (10%): PA proxy. 600 PA = full 10pts.
    if pa is not None:
        vol = _clamp(pa / 600.0) * 10.0
    else:
        vol = 5.0  # neutral midpoint when unknown

    raw = head + rc + pwr + ob + vol
    score = round(_clamp(raw, 0.0, 100.0), 2)

    return score, {
        "headline":  round(head, 2),
        "run_creation": round(rc, 2),
        "power":     round(pwr, 2),
        "obp":       round(ob, 2),
        "volume":    round(vol, 2),
        "fallback":  used_fallback,
    }


# ─────────────────────────────────────────────────────────────────────
# Team-level: top-5 + missing
# ─────────────────────────────────────────────────────────────────────
def _player_name(p: dict) -> str:
    return (p.get("name") or p.get("full_name") or p.get("player") or "Unknown").strip()


def _player_id(p: dict) -> str:
    """Stable ID for matching roster vs injury list."""
    return str(p.get("id") or p.get("player_id") or _player_name(p).lower())


def _rank_offensive_players(players: Iterable[dict]) -> list[tuple[dict, float, dict]]:
    """Return players sorted by composite offensive score, descending.
    Filters out pitchers and clearly bench-only entries."""
    out: list[tuple[dict, float, dict]] = []
    for p in (players or []):
        if not isinstance(p, dict):
            continue
        if not _is_offensive_role(p):
            continue
        score, debug = _composite_offensive_score(p)
        out.append((p, score, debug))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _bucket_from_missing_count(count: int) -> str:
    if count >= 3:
        return BUCKET_HIGH
    if count == 2:
        return BUCKET_MEDIUM
    if count == 1:
        return BUCKET_LOW
    return BUCKET_LOW


def _injury_score_from_missing(
    missing_top5: list[tuple[dict, float, dict]],
    top5_total_score: float,
) -> float:
    """Map (count, share) into a 0-100 ``offensive_injury_score``."""
    if not missing_top5:
        return 0.0
    count = len(missing_top5)
    missing_score_sum = sum(s for _, s, _ in missing_top5)
    share = (missing_score_sum / top5_total_score) if top5_total_score > 0 else 0.0

    # Base by count (per the penalty curve).
    base_by_count = {1: 20.0, 2: 42.0, 3: 60.0, 4: 78.0}
    base = base_by_count.get(count, 88.0 if count >= 5 else 0.0)

    # +/- 10pts shift based on how concentrated the loss is.
    # share=0.3 → -5, share=0.6 → 0, share=1.0 → +10.
    shift = _clamp((share - 0.30) / 0.70, 0.0, 1.0) * 15.0 - 5.0
    return _clamp(base + shift, 0.0, 95.0)


def _estimate_run_creation_lost(missing: list[tuple[dict, float, dict]]) -> float:
    """Rough Spanish-narrative-friendly "runs/game lost" estimate.

    For each missing top player, take their runs + RBI per game (using
    typical 162-game season) and sum the contribution. Rough heuristic
    but transparent.
    """
    total = 0.0
    for p, _score, _dbg in missing:
        runs = _safe(p.get("runs")) or 0.0
        rbi  = _safe(p.get("rbi"))  or 0.0
        games = _safe(p.get("games_played")) or _safe(p.get("games")) or 130.0
        if games <= 0:
            games = 130.0
        # Approx half of (runs+rbi) is double-counted ⇒ contribution per game.
        per_game = (runs + rbi) / 2.0 / games
        total += per_game
    return round(total, 3)


def compute_offensive_injury_impact_for_team(
    *,
    team_name: str,
    roster:    Optional[list[dict]] = None,
    injured:   Optional[list[dict]] = None,
) -> dict:
    """Compute the per-team offensive_injury_score and audit payload.

    Args:
        roster:  list of ACTIVE players (each a dict with stats).
        injured: list of players currently on the IL / unavailable.

    Returns the contract documented at module head. Fail-soft on
    missing inputs.
    """
    reason_codes: list[str] = [RC_OFFENSIVE_INJURY_IMPACT_USED]
    if not roster and not injured:
        return {
            "available":   False,
            "team":        team_name,
            "engine_version": ENGINE_VERSION,
            "offensive_injury_score": 0,
            "impact_bucket": BUCKET_LOW,
            "missing_top5_count": 0,
            "top5_missing": [],
            "top5_available": [],
            "run_creation_lost_estimate": 0.0,
            "reason_codes": reason_codes + [RC_DATA_INCOMPLETE_FALLBACK_USED],
        }

    # Combine roster + injured into a single "all known players" pool.
    # IL membership wins when a player appears in both lists.
    injured_ids = {_player_id(p) for p in (injured or []) if isinstance(p, dict)}
    pool: list[dict] = []
    for p in (roster or []):
        if isinstance(p, dict) and _player_id(p) not in injured_ids:
            pool.append(p)
    for p in (injured or []):
        if isinstance(p, dict):
            pool.append(p)

    # Rank ALL offensive players. The top-5 are our reference set.
    ranked = _rank_offensive_players(pool)
    if not ranked:
        # All injuries were pitchers / no offensive players.
        if injured:
            reason_codes.append(RC_PITCHER_ONLY_INJURIES_NO_PENALTY)
        return {
            "available":   True,
            "team":        team_name,
            "engine_version": ENGINE_VERSION,
            "offensive_injury_score": 0,
            "impact_bucket": BUCKET_LOW,
            "missing_top5_count": 0,
            "top5_missing": [],
            "top5_available": [],
            "run_creation_lost_estimate": 0.0,
            "reason_codes": reason_codes,
        }

    # ── Insufficient-roster guard ────────────────────────────────────
    # We need at least 5 ranked offensive players in TOTAL (active +
    # injured) to define a credible top-5. With fewer than 5 we cannot
    # safely judge "top-5 importance" — declare available=False and
    # emit no penalty.
    if len(ranked) < 5:
        return {
            "available":   False,
            "team":        team_name,
            "engine_version": ENGINE_VERSION,
            "offensive_injury_score": 0,
            "impact_bucket": BUCKET_LOW,
            "missing_top5_count": 0,
            "top5_missing": [],
            "top5_available": [],
            "run_creation_lost_estimate": 0.0,
            "reason_codes": reason_codes + [RC_DATA_INCOMPLETE_FALLBACK_USED],
        }

    top5 = ranked[:5]
    top5_total_score = sum(s for _, s, _ in top5)

    # Bucketize each top5 player into missing vs available.
    missing: list[tuple[dict, float, dict]] = []
    available: list[tuple[dict, float, dict]] = []
    for p, s, dbg in top5:
        if _player_id(p) in injured_ids:
            missing.append((p, s, dbg))
        else:
            available.append((p, s, dbg))

    # If injuries exist but NONE of them are top5 → pitcher-only or
    # depth-only injuries; surface that signal.
    if injured and not missing:
        # Filter the injured list to offensive vs pitchers.
        any_offensive_injured = any(
            _is_offensive_role(p) for p in injured if isinstance(p, dict)
        )
        if not any_offensive_injured:
            reason_codes.append(RC_PITCHER_ONLY_INJURIES_NO_PENALTY)

    score = _injury_score_from_missing(missing, top5_total_score)

    # Reason codes.
    if missing:
        reason_codes.append(RC_TOP_OFFENSIVE_PLAYER_MISSING)
    if len(missing) >= 2:
        reason_codes.append(RC_MULTIPLE_TOP5_BATS_MISSING)
    run_lost = _estimate_run_creation_lost(missing)
    if run_lost >= 1.0:
        reason_codes.append(RC_HIGH_RUN_CREATION_LOST)

    impact_bucket = _bucket_from_missing_count(len(missing))

    def _serialize(t: tuple[dict, float, dict]) -> dict:
        p, s, dbg = t
        return {
            "name":     _player_name(p),
            "id":       _player_id(p),
            "position": (p.get("position") or p.get("pos") or "").upper() or None,
            "score":    round(s, 2),
            "ops":      _safe(p.get("ops")),
            "runs":     _safe(p.get("runs")),
            "rbi":      _safe(p.get("rbi")),
            "hr":       _safe(p.get("hr") or p.get("home_runs")),
            "wrc_plus": _safe(p.get("wrc_plus") or p.get("wRC+")),
            "fallback": dbg.get("fallback", False),
        }

    return {
        "available":              True,
        "team":                   team_name,
        "engine_version":         ENGINE_VERSION,
        "offensive_injury_score": int(round(score)),
        "impact_bucket":          impact_bucket,
        "missing_top5_count":     len(missing),
        "top5_missing":           [_serialize(t) for t in missing],
        "top5_available":         [_serialize(t) for t in available],
        "run_creation_lost_estimate": run_lost,
        "reason_codes":           list(dict.fromkeys(reason_codes)),
    }


# ─────────────────────────────────────────────────────────────────────
# Matchup-level orchestration
# ─────────────────────────────────────────────────────────────────────
def compute_offensive_injury_impact(
    *,
    home_team_name: Optional[str] = None,
    away_team_name: Optional[str] = None,
    home_roster:    Optional[list[dict]] = None,
    home_injured:   Optional[list[dict]] = None,
    away_roster:    Optional[list[dict]] = None,
    away_injured:   Optional[list[dict]] = None,
) -> dict:
    """Matchup-level summary combining both teams.

    Returns:
        {
            "home": <per-team payload>,
            "away": <per-team payload>,
            "imbalance":         "BALANCED" | "HOME_HEALTHIER" | "AWAY_HEALTHIER",
            "imbalance_delta":   abs(home.score - away.score),
            "favors_team":       "home" | "away" | None,
            "under_support":     bool,
            "narrative_es":      str,
            "reason_codes":      [...],
        }
    """
    home = compute_offensive_injury_impact_for_team(
        team_name=home_team_name or "home", roster=home_roster, injured=home_injured,
    )
    away = compute_offensive_injury_impact_for_team(
        team_name=away_team_name or "away", roster=away_roster, injured=away_injured,
    )

    reason_codes: list[str] = []
    if home.get("available") or away.get("available"):
        reason_codes.append(RC_OFFENSIVE_INJURY_IMPACT_USED)

    home_score = home.get("offensive_injury_score", 0)
    away_score = away.get("offensive_injury_score", 0)
    delta = abs(home_score - away_score)

    # ── Imbalance classification ─────────────────────────────────────
    # BALANCED: |delta| <= 15 AND both buckets within one step.
    imbalance = "BALANCED"
    favors_team: Optional[str] = None
    if home_score >= 35 and away_score >= 35:
        # Both teams meaningfully depleted → favor Under.
        reason_codes.append(RC_BOTH_TEAMS_OFFENSIVELY_DEPLETED)
        reason_codes.append(RC_UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES)
    elif delta >= 25:
        if home_score < away_score:
            imbalance = "HOME_HEALTHIER"
            favors_team = "home"
        else:
            imbalance = "AWAY_HEALTHIER"
            favors_team = "away"
        reason_codes.append(RC_INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM)

    under_support = (home_score >= 35 and away_score >= 35)

    narrative_es = _build_narrative_es(
        home=home, away=away, imbalance=imbalance,
        favors_team=favors_team, under_support=under_support,
    )

    # Carry per-team reason codes upward.
    for src in (home, away):
        for rc in src.get("reason_codes") or []:
            if rc not in reason_codes:
                reason_codes.append(rc)

    return {
        "available":      home.get("available") or away.get("available"),
        "engine_version": ENGINE_VERSION,
        "home":           home,
        "away":           away,
        "imbalance":      imbalance,
        "imbalance_delta": delta,
        "favors_team":    favors_team,
        "under_support":  under_support,
        "narrative_es":   narrative_es,
        "reason_codes":   reason_codes,
    }


def _build_narrative_es(
    *, home: dict, away: dict, imbalance: str,
    favors_team: Optional[str], under_support: bool,
) -> Optional[str]:
    h_count = home.get("missing_top5_count", 0)
    a_count = away.get("missing_top5_count", 0)
    if h_count == 0 and a_count == 0:
        return None
    if under_support:
        return (
            f"Ambos equipos con bates importantes lesionados "
            f"({home.get('team')}: {h_count}, {away.get('team')}: {a_count}). "
            "Esto apoya el Under."
        )
    if favors_team == "home":
        return (
            f"{away.get('team')} llega con {a_count} bates del top-5 "
            f"lesionados frente a {h_count} de {home.get('team')}. "
            "Ligero apoyo al equipo más sano."
        )
    if favors_team == "away":
        return (
            f"{home.get('team')} llega con {h_count} bates del top-5 "
            f"lesionados frente a {a_count} de {away.get('team')}. "
            "Ligero apoyo al equipo más sano."
        )
    parts = []
    if h_count > 0:
        parts.append(f"{home.get('team')} tiene {h_count} bate(s) del top-5 fuera")
    if a_count > 0:
        parts.append(f"{away.get('team')} tiene {a_count} bate(s) del top-5 fuera")
    return ". ".join(parts) + "."


def apply_impact_to_pipeline(
    *,
    impact_payload: dict,
    side: str,                          # "home" | "away"
    base_lambda_7_9:   Optional[float] = None,
    base_traffic_score: Optional[float] = None,
    base_lineup_factor: Optional[float] = None,
) -> dict:
    """Compute the multiplicative adjustments other modules should apply.

    Returns ``{lambda_7_9_multiplier, traffic_score_multiplier,
    lineup_factor_multiplier, reason_codes}`` — observers may apply or
    ignore. Adjustments cap at 0.85× (15% suppression).

    The module NEVER flips Over/Under polarity — it only suppresses the
    side's projection.
    """
    team = (impact_payload or {}).get(side) or {}
    score = team.get("offensive_injury_score") or 0
    if score <= 0:
        return {
            "lambda_7_9_multiplier":    1.0,
            "traffic_score_multiplier": 1.0,
            "lineup_factor_multiplier": 1.0,
            "reason_codes":             [],
        }
    # Linear suppression: score 30 → 0.97×, score 60 → 0.91×, 90 → 0.85×.
    suppression = max(0.85, 1.0 - (score / 100.0) * 0.15)
    return {
        "lambda_7_9_multiplier":    round(suppression, 4),
        "traffic_score_multiplier": round(suppression, 4),
        "lineup_factor_multiplier": round(suppression, 4),
        "reason_codes":             team.get("reason_codes") or [],
    }


__all__ = [
    "ENGINE_VERSION",
    "BUCKET_LOW", "BUCKET_MEDIUM", "BUCKET_HIGH",
    "OFFENSIVE_POSITIONS",
    "RC_OFFENSIVE_INJURY_IMPACT_USED",
    "RC_TOP_OFFENSIVE_PLAYER_MISSING",
    "RC_MULTIPLE_TOP5_BATS_MISSING",
    "RC_HIGH_RUN_CREATION_LOST",
    "RC_BOTH_TEAMS_OFFENSIVELY_DEPLETED",
    "RC_INJURY_IMBALANCE_FAVORS_HEALTHIER_TEAM",
    "RC_UNDER_SUPPORTED_BY_OFFENSIVE_INJURIES",
    "RC_PITCHER_ONLY_INJURIES_NO_PENALTY",
    "RC_DATA_INCOMPLETE_FALLBACK_USED",
    "compute_offensive_injury_impact_for_team",
    "compute_offensive_injury_impact",
    "apply_impact_to_pipeline",
]
