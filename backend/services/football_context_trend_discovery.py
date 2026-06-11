"""
services.football_context_trend_discovery
==========================================

Phase F57 — Football Context + Trend Discovery Engine (observe-only).

Mission
-------
Detect football betting value the main engine misses because it does
not fully account for:

* Team news (disciplinary removals, internal conflict, sent home).
* Recent form streaks (losing/winning runs, scoring/conceding streaks).
* Corners trends (last-5 vs last-10, territorial dominance).
* Bilateral scoring trends (BTTS-light, Over 1.5/1.75 protected totals).
* Missed-match rescue (matches discarded by the main engine that have
  strong context/trend signal).

The module is strictly **observe-only**: it never modifies picks,
polarity or market selection. Its output is an annotated diagnostic
payload designed to be surfaced in the UI and consumed by analysts.

Submodules
----------
1. ``detect_squad_disruption(team, news_payload)``
2. ``detect_form_streaks(team_recent_results)``
3. ``analyze_corners_trend(team_corner_history, opponent_profile)``
4. ``analyze_protected_goals_trend(team_a_recent, team_b_recent)``
5. ``rescue_missed_match(match, original_status, context_score, trend_score)``

Top-level: ``analyze_football_context_trend()`` orchestrates all five.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .football_news_context_ingestion import (
    KEYWORD_SEVERITY,
    fetch_news_for_match,
)

log = logging.getLogger("football_context_trend_discovery")

ENGINE_VERSION = "football_context_trend_discovery.v1"

# Bucket thresholds.
_DISRUPTION_HIGH    = 60
_DISRUPTION_MEDIUM  = 30
_CONTEXT_HIGH       = 70
_CONTEXT_MEDIUM     = 40
_TREND_HIGH         = 65
_TREND_MEDIUM       = 40

# Form-streak detection thresholds.
_FORM_LOST_STREAK_MIN          = 3
_FORM_WON_STREAK_MIN           = 3
_FORM_SCORING_STREAK_MIN       = 3
_FORM_CONCEDED_STREAK_MIN      = 3
_FORM_TWO_PLUS_GOALS_MIN       = 3
_FORM_CLEAN_SHEET_STREAK_MIN   = 2

# Corners signal thresholds (using avg-last-10 vs avg-last-5).
_CORNERS_VOLUME_HIGH_AVG_10    = 6.5    # team-for last-10 avg
_CORNERS_VOLUME_HIGH_AVG_5     = 7.0    # team-for last-5 avg (acceleration)
_CORNERS_TOTAL_HIGH_AVG_10     = 10.5   # combined match total

# Protected goals thresholds.
_PROT_GOALS_STREAK_MIN_BOTH    = 3
_PROT_GOALS_OPS_OVER_1_5_PROB  = 0.65   # heuristic threshold

# Reason codes.
RC_AVAILABLE                       = "FOOTBALL_CONTEXT_TREND_AVAILABLE"
RC_UNAVAILABLE                     = "FOOTBALL_CONTEXT_TREND_UNAVAILABLE"

# Squad disruption.
RC_DISCIPLINARY_REMOVALS           = "DISCIPLINARY_REMOVALS"
RC_KEY_PLAYERS_ABSENT              = "KEY_PLAYERS_ABSENT"
RC_SQUAD_INSTABILITY               = "SQUAD_INSTABILITY"
RC_INTERNAL_CONFLICT               = "INTERNAL_CONFLICT"
RC_OPPONENT_SQUAD_DISRUPTION       = "OPPONENT_SQUAD_DISRUPTION"

# Form streaks.
RC_LOST_STREAK                     = "LOST_STREAK"
RC_WIN_STREAK                      = "WIN_STREAK"
RC_SCORING_STREAK                  = "SCORING_STREAK"
RC_CONCEDING_STREAK                = "CONCEDING_STREAK"
RC_TWO_PLUS_GOALS_STREAK           = "TWO_PLUS_GOALS_STREAK"
RC_CLEAN_SHEET_STREAK              = "CLEAN_SHEET_STREAK"

# Corners.
RC_TEAM_CORNER_VOLUME_HIGH         = "TEAM_CORNER_VOLUME_HIGH"
RC_FAVORITE_TERRITORIAL_DOMINANCE  = "FAVORITE_TERRITORIAL_DOMINANCE"
RC_CORNERS_ACCELERATING            = "CORNERS_ACCELERATING"
RC_OPPONENT_LOW_BLOCK_EXPECTED     = "OPPONENT_LOW_BLOCK_EXPECTED"

# Protected goals.
RC_TEAM_A_SCORING_STREAK           = "TEAM_A_SCORING_STREAK"
RC_TEAM_B_SCORING_STREAK           = "TEAM_B_SCORING_STREAK"
RC_PROTECTED_TOTAL_PREFERRED       = "PROTECTED_TOTAL_PREFERRED"
RC_AVOID_BTTS_ONE_SIDE_WEAK        = "AVOID_BTTS_ONE_SIDE_WEAK"

# Rescue.
RC_RESCUED_BY_CONTEXT              = "RESCUED_BY_CONTEXT_TREND"

# Recommended market codes.
RMK_ENGLAND_ML_STYLE_FAVORITE      = "FAVORITE_ML"
RMK_TOTAL_CORNERS_OVER             = "TOTAL_CORNERS_OVER"
RMK_TEAM_CORNERS_OVER              = "TEAM_CORNERS_OVER"
RMK_OVER_1_5                       = "OVER_1_5"
RMK_OVER_1_75                      = "OVER_1_75"
RMK_OVER_2_5                       = "OVER_2_5"
RMK_BTTS                           = "BTTS"


# ── Helpers ─────────────────────────────────────────────────
def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp_score(v: float) -> int:
    return max(0, min(100, int(round(v))))


def _bucket_from_score(score: int, high: int, medium: int) -> str:
    if score >= high:
        return "HIGH"
    if score >= medium:
        return "MEDIUM"
    return "LOW"


# ── 1. Squad Disruption ───────────────────────────────────────
def detect_squad_disruption(
    team: str,
    news_payload: Optional[dict],
) -> dict:
    """Translate news ingestion output into a squad disruption signal.

    Returns ``{available, squad_disruption_score, bucket, reason_codes,
    matched_items, evidence_sources}``.
    """
    if not news_payload or not isinstance(news_payload, dict):
        return {
            "available": False, "team": team,
            "reason": "no_news_payload",
            "squad_disruption_score": 0, "bucket": "LOW",
            "reason_codes": [], "matched_items": [], "evidence_sources": [],
        }
    items = news_payload.get("items") or []
    matched_items: list[dict] = [
        it for it in items if it.get("matched_phrases")
    ]

    if not matched_items:
        return {
            "available": True, "team": team,
            "squad_disruption_score": 0, "bucket": "LOW",
            "reason_codes": [], "matched_items": [],
            "evidence_sources": [],
        }

    # Aggregate severity: take max severity per code (avoid double-counting
    # the same outlet reproducing the story 10x).
    seen_codes: dict[str, int] = {}
    for it in matched_items:
        for code in (it.get("matched_phrases") or []):
            sev = KEYWORD_SEVERITY.get(code, 10)
            if seen_codes.get(code, 0) < sev:
                seen_codes[code] = sev

    # Total disruption = sum(max severity per code) capped at 100.
    score = _clamp_score(sum(seen_codes.values()))

    reason_codes: list[str] = []
    # Map severities to reason codes.
    for code in seen_codes:
        if code in ("APARTADO_DE_CONCENTRACION", "SEPARADO_POR_INDISCIPLINA",
                    "EXPULSADO_DE_CONVOCATORIA", "BAJA_DISCIPLINARIA",
                    "SANCIONADO", "EXCLUIDO", "REMOVED_FROM_SQUAD",
                    "DROPPED_FROM_NATIONAL_TEAM", "DISCIPLINARY_ACTION",
                    "SENT_HOME"):
            if RC_DISCIPLINARY_REMOVALS not in reason_codes:
                reason_codes.append(RC_DISCIPLINARY_REMOVALS)
        if code in ("PROBLEMAS_INTERNOS", "INTERNAL_CONFLICT"):
            if RC_INTERNAL_CONFLICT not in reason_codes:
                reason_codes.append(RC_INTERNAL_CONFLICT)
        if code in ("FUERA_DE_SELECCION", "NO_CONTINUARA_CON_SELECCION"):
            if RC_KEY_PLAYERS_ABSENT not in reason_codes:
                reason_codes.append(RC_KEY_PLAYERS_ABSENT)
        if code == "BALACERA":
            if RC_SQUAD_INSTABILITY not in reason_codes:
                reason_codes.append(RC_SQUAD_INSTABILITY)

    if score >= _DISRUPTION_HIGH and RC_SQUAD_INSTABILITY not in reason_codes:
        reason_codes.append(RC_SQUAD_INSTABILITY)

    bucket = _bucket_from_score(score, _DISRUPTION_HIGH, _DISRUPTION_MEDIUM)

    evidence_sources = []
    for it in matched_items[:5]:
        evidence_sources.append({
            "title":       it.get("title"),
            "source_url":   it.get("source_url") or it.get("link"),
            "source_name":  it.get("source_name"),
            "matched_phrases": it.get("matched_phrases"),
        })

    return {
        "available":              True,
        "team":                    team,
        "squad_disruption_score":  score,
        "bucket":                  bucket,
        "reason_codes":            reason_codes,
        "matched_items":           matched_items,
        "evidence_sources":        evidence_sources,
        "keyword_severity_used":   seen_codes,
    }


# ── 2. Recent Form Streak Detector ────────────────────────────────
def detect_form_streaks(
    team: str,
    recent_results: list[dict],
) -> dict:
    """Detect streaks from a list of recent results.

    Each result is a dict with keys ``goals_for`` and ``goals_against``
    (and optionally ``outcome`` ∈ {W,D,L}). Results must be ordered
    **most recent first**.
    """
    if not recent_results:
        return {
            "available": False, "team": team,
            "reason": "no_results",
            "form_streaks": [], "reason_codes": [], "counters": {},
        }

    losses = wins = draws = 0
    scoring = conceding = two_plus = clean = 0
    saw_non_loss = saw_non_win = False
    saw_non_scoring = saw_non_conceding = False
    saw_non_two_plus = saw_non_clean = False

    for r in recent_results:
        gf = _safe_int(r.get("goals_for"))
        ga = _safe_int(r.get("goals_against"))
        outcome = (r.get("outcome") or "").upper().strip()
        if not outcome:
            outcome = "W" if gf > ga else ("L" if gf < ga else "D")

        if not saw_non_loss and outcome == "L":
            losses += 1
        else:
            saw_non_loss = True
        if not saw_non_win and outcome == "W":
            wins += 1
        else:
            saw_non_win = True
        if outcome == "D":
            draws += 1

        if not saw_non_scoring and gf >= 1:
            scoring += 1
        else:
            saw_non_scoring = True
        if not saw_non_conceding and ga >= 1:
            conceding += 1
        else:
            saw_non_conceding = True
        if not saw_non_two_plus and gf >= 2:
            two_plus += 1
        else:
            saw_non_two_plus = True
        if not saw_non_clean and ga == 0:
            clean += 1
        else:
            saw_non_clean = True

    streaks: list[str] = []
    reason_codes: list[str] = []

    if losses >= _FORM_LOST_STREAK_MIN:
        streaks.append(f"LOST_{losses}_STRAIGHT")
        reason_codes.append(RC_LOST_STREAK)
    if wins >= _FORM_WON_STREAK_MIN:
        streaks.append(f"WON_{wins}_STRAIGHT")
        reason_codes.append(RC_WIN_STREAK)
    if scoring >= _FORM_SCORING_STREAK_MIN:
        streaks.append(f"SCORED_{scoring}_STRAIGHT")
        reason_codes.append(RC_SCORING_STREAK)
    if conceding >= _FORM_CONCEDED_STREAK_MIN:
        streaks.append(f"CONCEDED_{conceding}_STRAIGHT")
        reason_codes.append(RC_CONCEDING_STREAK)
    if two_plus >= _FORM_TWO_PLUS_GOALS_MIN:
        streaks.append(f"SCORED_2_PLUS_IN_{two_plus}_STRAIGHT")
        reason_codes.append(RC_TWO_PLUS_GOALS_STREAK)
    if clean >= _FORM_CLEAN_SHEET_STREAK_MIN:
        streaks.append(f"CLEAN_SHEET_{clean}_STRAIGHT")
        reason_codes.append(RC_CLEAN_SHEET_STREAK)

    return {
        "available":     True,
        "team":           team,
        "form_streaks":   streaks,
        "reason_codes":   reason_codes,
        "counters": {
            "losses": losses, "wins": wins, "draws": draws,
            "scoring_streak": scoring, "conceding_streak": conceding,
            "two_plus_goals_streak": two_plus,
            "clean_sheet_streak": clean,
            "matches_considered": len(recent_results),
        },
    }


# ── 3. Corners Trend Engine ───────────────────────────────────────
def _avg_safe(values: list[Any]) -> Optional[float]:
    vals = [_safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def analyze_corners_trend(
    team: str,
    *,
    team_corners_for_last_10:    list[Any],
    team_corners_for_last_5:     list[Any],
    team_corners_against_last_10: Optional[list[Any]] = None,
    opponent_corners_against_last_10: Optional[list[Any]] = None,
    possession_dominance_score:  Optional[float] = None,
    favorite_indicator:          Optional[bool] = None,
    opponent_low_block:          Optional[bool] = None,
) -> dict:
    """Build a corners signal from last-10 vs last-5 averages.

    Strategy
    --------
    * ``avg_for_10`` and ``avg_for_5`` are the user-requested baseline.
    * Acceleration (``avg_for_5 > avg_for_10``) is a strong signal.
    * Multiplicative bumps from possession dominance, favorite status
      and opponent low-block tendencies.
    """
    avg_for_10 = _avg_safe(team_corners_for_last_10 or [])
    avg_for_5  = _avg_safe(team_corners_for_last_5  or [])
    avg_against_10 = _avg_safe(team_corners_against_last_10 or [])
    avg_opp_against_10 = _avg_safe(opponent_corners_against_last_10 or [])

    if avg_for_10 is None and avg_for_5 is None:
        return {
            "available": False, "team": team,
            "reason": "no_corner_data",
            "corners_signal": False,
            "recommended_market": None,
            "confidence": 0, "reason_codes": [],
        }

    score = 0.0
    reason_codes: list[str] = []

    # Base: average for vs benchmark.
    if avg_for_10 is not None:
        if avg_for_10 >= _CORNERS_VOLUME_HIGH_AVG_10:
            score += 35
            reason_codes.append(RC_TEAM_CORNER_VOLUME_HIGH)
        elif avg_for_10 >= (_CORNERS_VOLUME_HIGH_AVG_10 - 1.5):
            score += 18

    # Acceleration: last-5 > last-10.
    if avg_for_5 is not None and avg_for_10 is not None:
        delta = avg_for_5 - avg_for_10
        if delta >= 1.0 and avg_for_5 >= _CORNERS_VOLUME_HIGH_AVG_5:
            score += 20
            reason_codes.append(RC_CORNERS_ACCELERATING)
        elif delta >= 0.4:
            score += 8
            reason_codes.append(RC_CORNERS_ACCELERATING)

    # Favorite + possession dominance.
    if possession_dominance_score is not None and possession_dominance_score >= 60:
        score += 12
        reason_codes.append(RC_FAVORITE_TERRITORIAL_DOMINANCE)
    if favorite_indicator and possession_dominance_score and possession_dominance_score >= 55:
        reason_codes.append(RC_FAVORITE_TERRITORIAL_DOMINANCE)

    # Opponent low block.
    if opponent_low_block:
        score += 10
        reason_codes.append(RC_OPPONENT_LOW_BLOCK_EXPECTED)
    if avg_opp_against_10 is not None and avg_opp_against_10 >= 6.0:
        score += 8
        if RC_OPPONENT_LOW_BLOCK_EXPECTED not in reason_codes:
            reason_codes.append(RC_OPPONENT_LOW_BLOCK_EXPECTED)

    score = _clamp_score(score)
    signal = score >= 50

    if signal:
        # Recommend a Total Corners line based on expected combined volume.
        expected_total = (avg_for_10 or 0) + (avg_opp_against_10 or avg_against_10 or 4.5)
        if expected_total >= _CORNERS_TOTAL_HIGH_AVG_10:
            recommended_market = "Total Corners Over 9.5"
        else:
            recommended_market = "Total Corners Over 7.5"
    else:
        recommended_market = None

    return {
        "available":         True,
        "team":               team,
        "corners_signal":     signal,
        "recommended_market": recommended_market,
        "confidence":         score,
        "avg_for_last_10":    avg_for_10,
        "avg_for_last_5":     avg_for_5,
        "avg_against_last_10": avg_against_10,
        "opponent_avg_against_last_10": avg_opp_against_10,
        "reason_codes":       list(dict.fromkeys(reason_codes)),
    }


# ── 4. Protected Goals Trend Engine ───────────────────────────────
async def _async_noop():
    return None


def analyze_protected_goals_trend(
    team_a: str, team_b: str,
    *,
    team_a_recent_results: list[dict],
    team_b_recent_results: list[dict],
    odds_over_2_5: Optional[float] = None,
) -> dict:
    """Identify bilateral scoring trends and recommend protected
    total markets (Over 1.5 / 1.75) vs aggressive (Over 2.5 / BTTS).
    """
    a_scoring = b_scoring = 0
    a_avg_for = b_avg_for = 0.0
    a_results = team_a_recent_results or []
    b_results = team_b_recent_results or []

    for r in a_results:
        if _safe_int(r.get("goals_for")) >= 1:
            a_scoring += 1
        else:
            break
    for r in b_results:
        if _safe_int(r.get("goals_for")) >= 1:
            b_scoring += 1
        else:
            break

    if a_results:
        a_avg_for = sum(_safe_int(r.get("goals_for")) for r in a_results) / len(a_results)
    if b_results:
        b_avg_for = sum(_safe_int(r.get("goals_for")) for r in b_results) / len(b_results)

    reason_codes: list[str] = []
    avoid: list[str] = []
    protected: list[str] = []

    if a_scoring >= _PROT_GOALS_STREAK_MIN_BOTH:
        reason_codes.append(RC_TEAM_A_SCORING_STREAK)
    if b_scoring >= _PROT_GOALS_STREAK_MIN_BOTH:
        reason_codes.append(RC_TEAM_B_SCORING_STREAK)

    both_scoring = (a_scoring >= _PROT_GOALS_STREAK_MIN_BOTH
                    and b_scoring >= _PROT_GOALS_STREAK_MIN_BOTH)

    signal = False
    recommended = None
    if both_scoring:
        signal = True
        # Protected markets first.
        protected = [RMK_OVER_1_5]
        # Use Over 1.75 when avg combined goals is moderate (>=2.4) but
        # not aggressive enough for Over 2.5.
        combined_avg = a_avg_for + b_avg_for
        if combined_avg >= 2.4 and combined_avg < 3.2:
            recommended = RMK_OVER_1_75
            protected.append(RMK_OVER_1_75)
            reason_codes.append(RC_PROTECTED_TOTAL_PREFERRED)
        else:
            recommended = RMK_OVER_1_5
            reason_codes.append(RC_PROTECTED_TOTAL_PREFERRED)
        # Avoid aggressive Over 2.5 / BTTS unless one side is clearly
        # producing 2+ goals per match.
        if a_avg_for < 1.6 or b_avg_for < 1.4:
            avoid.append(RMK_OVER_2_5)
            avoid.append(RMK_BTTS)
            reason_codes.append(RC_AVOID_BTTS_ONE_SIDE_WEAK)
    elif a_scoring >= _PROT_GOALS_STREAK_MIN_BOTH or b_scoring >= _PROT_GOALS_STREAK_MIN_BOTH:
        signal = True
        recommended = RMK_OVER_1_5
        protected = [RMK_OVER_1_5]
        avoid = [RMK_BTTS]
        reason_codes.append(RC_PROTECTED_TOTAL_PREFERRED)
        reason_codes.append(RC_AVOID_BTTS_ONE_SIDE_WEAK)

    confidence = 0
    if signal:
        confidence += 40
        if both_scoring:
            confidence += 20
        if (a_avg_for + b_avg_for) >= 2.6:
            confidence += 15
    confidence = _clamp_score(confidence)

    return {
        "available":                True,
        "team_a":                   team_a,
        "team_b":                   team_b,
        "goals_trend_signal":       signal,
        "recommended_market":       recommended,
        "protected_alternatives":   list(dict.fromkeys(protected)),
        "avoid_markets":            list(dict.fromkeys(avoid)),
        "confidence":               confidence,
        "team_a_scoring_streak":    a_scoring,
        "team_b_scoring_streak":    b_scoring,
        "team_a_avg_goals_for":     round(a_avg_for, 2),
        "team_b_avg_goals_for":     round(b_avg_for, 2),
        "reason_codes":             reason_codes,
    }


# ── 5. Missed-match Rescue ───────────────────────────────────────
def rescue_missed_match(
    *,
    original_engine_status: Optional[str],
    context_score:          int,
    trend_score:            int,
    min_context:            int = _CONTEXT_HIGH,
    min_trend:              int = _TREND_HIGH,
) -> dict:
    """Decide whether a match originally discarded/omitted by the main
    engine should be rescued by the context+trend layer.
    """
    if not original_engine_status:
        return {
            "rescued_by_context_trend": False,
            "original_engine_status":   None,
            "rescue_reason":            None,
        }
    rescued = (
        original_engine_status.upper() in ("DISCARDED", "OMITTED", "DISCARDED_OR_OMITTED")
        and (context_score >= min_context or trend_score >= min_trend)
    )
    return {
        "rescued_by_context_trend": rescued,
        "original_engine_status":   original_engine_status,
        "rescue_reason":            (
            "STRONG_CONTEXT_AND_TREND_SIGNAL" if rescued else None
        ),
    }


# ── Top-level orchestration ──────────────────────────────────────
def _aggregate_recommendations(
    *,
    home_team:        str,
    away_team:        str,
    disruption_home:  dict,
    disruption_away:  dict,
    form_home:        dict,
    form_away:        dict,
    corners_home:     dict,
    corners_away:     dict,
    protected_goals:  dict,
) -> list[dict]:
    recs: list[dict] = []

    # Favorite ML when opponent disruption is HIGH.
    if disruption_away.get("bucket") == "HIGH":
        recs.append({
            "market":      f"{home_team} ML",
            "market_code": RMK_ENGLAND_ML_STYLE_FAVORITE,
            "confidence":  74,
            "fragility":   32,
            "reason_codes": [
                RC_OPPONENT_SQUAD_DISRUPTION,
                *form_away.get("reason_codes", []),
            ],
        })
    if disruption_home.get("bucket") == "HIGH":
        recs.append({
            "market":      f"{away_team} ML",
            "market_code": RMK_ENGLAND_ML_STYLE_FAVORITE,
            "confidence":  68,
            "fragility":   36,
            "reason_codes": [
                RC_OPPONENT_SQUAD_DISRUPTION,
                *form_home.get("reason_codes", []),
            ],
        })

    # Corners.
    if corners_home.get("corners_signal"):
        recs.append({
            "market":      corners_home.get("recommended_market")
                            or "Total Corners Over 7.5",
            "market_code": RMK_TOTAL_CORNERS_OVER,
            "confidence":  corners_home.get("confidence", 0),
            "fragility":   max(20, 60 - corners_home.get("confidence", 0)),
            "reason_codes": corners_home.get("reason_codes", []),
            "side":         "home",
        })
    if corners_away.get("corners_signal"):
        recs.append({
            "market":      corners_away.get("recommended_market")
                            or "Total Corners Over 7.5",
            "market_code": RMK_TOTAL_CORNERS_OVER,
            "confidence":  corners_away.get("confidence", 0),
            "fragility":   max(20, 60 - corners_away.get("confidence", 0)),
            "reason_codes": corners_away.get("reason_codes", []),
            "side":         "away",
        })

    # Protected goals.
    if protected_goals.get("goals_trend_signal"):
        recs.append({
            "market":      protected_goals.get("recommended_market")
                            or RMK_OVER_1_5,
            "market_code": protected_goals.get("recommended_market")
                            or RMK_OVER_1_5,
            "confidence":  protected_goals.get("confidence", 0),
            "fragility":   max(25, 70 - protected_goals.get("confidence", 0)),
            "reason_codes": protected_goals.get("reason_codes", []),
        })

    # Deduplicate identical (market, market_code) entries keeping the best confidence.
    dedup: dict[tuple, dict] = {}
    for r in recs:
        k = (r.get("market"), r.get("market_code"))
        existing = dedup.get(k)
        if not existing or (r.get("confidence", 0) > existing.get("confidence", 0)):
            dedup[k] = r
    return list(dedup.values())


def _build_narrative_es(
    *,
    home_team: str, away_team: str,
    disruption_home: dict, disruption_away: dict,
    form_home: dict, form_away: dict,
    corners_home: dict, corners_away: dict,
    protected_goals: dict,
) -> str:
    chunks: list[str] = []
    # Disruption.
    if disruption_away.get("bucket") == "HIGH":
        chunks.append(
            f"{away_team} llega con disrupciones disciplinarias "
            f"(score {disruption_away.get('squad_disruption_score', 0)})"
        )
    if disruption_home.get("bucket") == "HIGH":
        chunks.append(
            f"{home_team} llega con disrupciones disciplinarias "
            f"(score {disruption_home.get('squad_disruption_score', 0)})"
        )
    # Form streaks.
    if RC_LOST_STREAK in form_away.get("reason_codes", []):
        chunks.append(f"{away_team} en mala racha reciente")
    if RC_LOST_STREAK in form_home.get("reason_codes", []):
        chunks.append(f"{home_team} en mala racha reciente")
    if RC_SCORING_STREAK in form_home.get("reason_codes", []):
        chunks.append(f"{home_team} anota con regularidad")
    if RC_SCORING_STREAK in form_away.get("reason_codes", []):
        chunks.append(f"{away_team} anota con regularidad")
    # Corners.
    if corners_home.get("corners_signal"):
        chunks.append(
            f"{home_team} muestra alto volumen de corners "
            f"(prom L10 {corners_home.get('avg_for_last_10')}, L5 {corners_home.get('avg_for_last_5')})"
        )
    if corners_away.get("corners_signal"):
        chunks.append(
            f"{away_team} muestra alto volumen de corners "
            f"(prom L10 {corners_away.get('avg_for_last_10')}, L5 {corners_away.get('avg_for_last_5')})"
        )
    # Protected goals.
    if protected_goals.get("goals_trend_signal"):
        chunks.append(
            f"tendencia goleadora bilateral favorece total protegido "
            f"{protected_goals.get('recommended_market')}"
        )
    if not chunks:
        return "Sin señales fuertes de contexto o tendencia."
    return "; ".join(chunks).capitalize() + "."


async def analyze_football_context_trend(
    *,
    home_team:           str,
    away_team:           str,
    match_id:             Optional[Any] = None,
    db:                  Any = None,
    use_news:             bool = True,
    locale:               str  = "es",
    # Form inputs.
    home_recent_results:  Optional[list[dict]] = None,
    away_recent_results:  Optional[list[dict]] = None,
    # Corner inputs (lists of corner counts, most recent first).
    home_corners_for_last_10:  Optional[list[Any]] = None,
    home_corners_for_last_5:   Optional[list[Any]] = None,
    home_corners_against_last_10: Optional[list[Any]] = None,
    away_corners_for_last_10:  Optional[list[Any]] = None,
    away_corners_for_last_5:   Optional[list[Any]] = None,
    away_corners_against_last_10: Optional[list[Any]] = None,
    # Tactical inputs.
    home_possession_dominance_score: Optional[float] = None,
    away_possession_dominance_score: Optional[float] = None,
    home_is_favorite: Optional[bool] = None,
    away_is_favorite: Optional[bool] = None,
    opponent_low_block_home_side: Optional[bool] = None,
    opponent_low_block_away_side: Optional[bool] = None,
    # Rescue.
    original_engine_status: Optional[str] = None,
    # Inject custom news payload (test hook).
    injected_news: Optional[dict] = None,
) -> dict:
    """Top-level orchestrator. Strictly observe-only — NEVER mutates
    the calling engine's picks or state."""
    if not home_team or not away_team:
        return {
            "available": False, "engine_version": ENGINE_VERSION,
            "reason": "missing_teams",
            "reason_codes": [RC_UNAVAILABLE],
        }

    # 1) News
    news_payload: Optional[dict] = injected_news
    if news_payload is None and use_news:
        try:
            news_payload = await fetch_news_for_match(
                home_team, away_team, db=db, locale=locale,
            )
        except Exception as exc:
            log.debug("news fetch failed for %s vs %s: %s",
                      home_team, away_team, exc)
            news_payload = None

    home_news = (news_payload or {}).get("home") if news_payload else None
    away_news = (news_payload or {}).get("away") if news_payload else None

    disruption_home = detect_squad_disruption(home_team, home_news)
    disruption_away = detect_squad_disruption(away_team, away_news)

    # 2) Form streaks
    form_home = detect_form_streaks(home_team, home_recent_results or [])
    form_away = detect_form_streaks(away_team, away_recent_results or [])

    # 3) Corners
    corners_home = analyze_corners_trend(
        home_team,
        team_corners_for_last_10=home_corners_for_last_10 or [],
        team_corners_for_last_5=home_corners_for_last_5 or [],
        team_corners_against_last_10=home_corners_against_last_10 or [],
        opponent_corners_against_last_10=away_corners_against_last_10 or [],
        possession_dominance_score=home_possession_dominance_score,
        favorite_indicator=home_is_favorite,
        opponent_low_block=opponent_low_block_home_side,
    )
    corners_away = analyze_corners_trend(
        away_team,
        team_corners_for_last_10=away_corners_for_last_10 or [],
        team_corners_for_last_5=away_corners_for_last_5 or [],
        team_corners_against_last_10=away_corners_against_last_10 or [],
        opponent_corners_against_last_10=home_corners_against_last_10 or [],
        possession_dominance_score=away_possession_dominance_score,
        favorite_indicator=away_is_favorite,
        opponent_low_block=opponent_low_block_away_side,
    )

    # 4) Protected goals
    protected_goals = analyze_protected_goals_trend(
        home_team, away_team,
        team_a_recent_results=home_recent_results or [],
        team_b_recent_results=away_recent_results or [],
    )

    # 5) Aggregate scores.
    context_score = _clamp_score(
        max(
            disruption_home.get("squad_disruption_score", 0),
            disruption_away.get("squad_disruption_score", 0),
        )
    )
    trend_score = _clamp_score(
        max(
            corners_home.get("confidence", 0),
            corners_away.get("confidence", 0),
            protected_goals.get("confidence", 0),
            len(form_home.get("form_streaks", [])) * 12,
            len(form_away.get("form_streaks", [])) * 12,
        )
    )

    rescue = rescue_missed_match(
        original_engine_status=original_engine_status,
        context_score=context_score,
        trend_score=trend_score,
    )

    recommendations = _aggregate_recommendations(
        home_team=home_team, away_team=away_team,
        disruption_home=disruption_home, disruption_away=disruption_away,
        form_home=form_home, form_away=form_away,
        corners_home=corners_home, corners_away=corners_away,
        protected_goals=protected_goals,
    )

    narrative = _build_narrative_es(
        home_team=home_team, away_team=away_team,
        disruption_home=disruption_home, disruption_away=disruption_away,
        form_home=form_home, form_away=form_away,
        corners_home=corners_home, corners_away=corners_away,
        protected_goals=protected_goals,
    )

    return {
        "available":          True,
        "engine_version":      ENGINE_VERSION,
        "match":               f"{home_team} vs {away_team}",
        "match_id":            match_id,
        "home_team":           home_team,
        "away_team":           away_team,
        "context_score":       context_score,
        "context_bucket":      _bucket_from_score(context_score, _CONTEXT_HIGH, _CONTEXT_MEDIUM),
        "trend_score":         trend_score,
        "trend_bucket":        _bucket_from_score(trend_score, _TREND_HIGH, _TREND_MEDIUM),
        "squad_disruption":    {"home": disruption_home, "away": disruption_away},
        "form_streaks":        {"home": form_home,        "away": form_away},
        "corners_trend":       {"home": corners_home,     "away": corners_away},
        "protected_goals_trend": protected_goals,
        "missed_match_rescue": rescue,
        "recommended_markets": recommendations,
        "narrative_es":        narrative,
        "reason_codes":        [RC_AVAILABLE]
        + ([RC_RESCUED_BY_CONTEXT] if rescue.get("rescued_by_context_trend") else []),
        "observe_only":        True,
        "news_payload":        news_payload,
    }


__all__ = [
    "ENGINE_VERSION",
    "detect_squad_disruption",
    "detect_form_streaks",
    "analyze_corners_trend",
    "analyze_protected_goals_trend",
    "rescue_missed_match",
    "analyze_football_context_trend",
    "RC_AVAILABLE", "RC_UNAVAILABLE",
    "RC_DISCIPLINARY_REMOVALS", "RC_KEY_PLAYERS_ABSENT",
    "RC_SQUAD_INSTABILITY", "RC_INTERNAL_CONFLICT",
    "RC_OPPONENT_SQUAD_DISRUPTION",
    "RC_LOST_STREAK", "RC_WIN_STREAK",
    "RC_SCORING_STREAK", "RC_CONCEDING_STREAK",
    "RC_TWO_PLUS_GOALS_STREAK", "RC_CLEAN_SHEET_STREAK",
    "RC_TEAM_CORNER_VOLUME_HIGH", "RC_FAVORITE_TERRITORIAL_DOMINANCE",
    "RC_CORNERS_ACCELERATING", "RC_OPPONENT_LOW_BLOCK_EXPECTED",
    "RC_TEAM_A_SCORING_STREAK", "RC_TEAM_B_SCORING_STREAK",
    "RC_PROTECTED_TOTAL_PREFERRED", "RC_AVOID_BTTS_ONE_SIDE_WEAK",
    "RC_RESCUED_BY_CONTEXT",
    "RMK_ENGLAND_ML_STYLE_FAVORITE", "RMK_TOTAL_CORNERS_OVER",
    "RMK_TEAM_CORNERS_OVER", "RMK_OVER_1_5", "RMK_OVER_1_75",
    "RMK_OVER_2_5", "RMK_BTTS",
]
