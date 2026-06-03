"""MLB Explosive Inning Intelligence Engine (v2).

Mirror del ``live_corner_engine.py`` del sistema de fútbol, adaptado al
contexto MLB: detección en vivo de innings explosivos con
``explosive_inning_pressure_score`` 0..100 + estado categórico + market
candidates + trap signals.

Es complementario a ``compute_explosive_inning_risk`` (pregame-aggregate
basado en OPS / bullpen / park / line_gap / script_survival). El v2
opera con métricas del inning EN CURSO y produce recomendaciones
accionables o señales de watchlist.

Funciones puras — sin IO. Todo coercion via helpers ``_f`` / ``_i`` /
``_b``.

Inputs (dict ``metrics``)::

    # Contexto del partido
    inning, half_inning ('top'|'bottom'), outs,
    score_home, score_away, current_total_runs,
    home_team, away_team,
    batting_team ('home'|'away'),
    pitching_team ('home'|'away'),

    # Base runners
    base_runners: dict {'first': bool, 'second': bool, 'third': bool}
        or list[int] con bases ocupadas,
    runners_in_scoring_position: bool,   # derivable
    bases_loaded: bool,                  # derivable

    # Inning stats
    pitches_this_inning, walks_this_inning, hits_this_inning,
    hard_contact_this_inning, barrels_this_inning,
    avg_exit_velocity, line_drives_this_inning,
    wild_pitch_or_hbp: bool,
    falling_behind_count_rate: float (0..1),

    # Pitching
    current_pitcher: str,
    pitch_count, pitch_count_threshold (default 95),
    pitcher_role ('starter'|'reliever'),
    starter_removed_early: bool,
    bullpen_fatigue: float (0..1) — bullpen workload index,
    next_reliever_quality: float (0..1) — 0=poor, 1=elite,
    reliever_back_to_back: bool,

    # Lineup
    lineup_position_due_up: int (1..9),
    times_through_order: int (1..4+),
    handedness_matchup ('favorable'|'neutral'|'unfavorable'),

    # Markets
    pregame_total_line: float | None,
    live_total_line: float | None,
    current_odds: dict | None,
        e.g. {'live_over_8.5': 2.05, 'team_total_over_home_4.5': 2.30, ...}

Outputs (full ``run_recommendation`` dict)::

    {
      "explosive_inning_pressure_score": int 0..100,
      "state":                str,         # uno de los 7 estados
      "risk_tier":            "LOW"|"MEDIUM"|"HIGH",
      "confidence":           int 0..100,
      "risk":                 "LOW"|"MEDIUM"|"HIGH",
      "reason_codes":         list[str],
      "human_reasons":        list[str],
      "avoid_markets":        list[str],
      "market_candidates":    list[dict],
      "should_recommend":     bool,
      "recommended_market":   str | None,
      "recommended_line":     float | None,
      "recommended_odds":     float | None,
      "flip_triggered":       bool,
      "explanation":          str,
      "trap_signals":         list[str],
      "score_contributions":  dict,
      "narrative_es":         str,
      "version":              2,
    }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("mlb_explosive_inning_engine")

# ─────────────────────────────────────────────────────────────────────
# Thresholds / constants
# ─────────────────────────────────────────────────────────────────────
DEFAULT_SURFACE_THRESHOLD = 55          # >= surface_threshold → should_recommend
HIGH_RISK_THRESHOLD       = 70
MEDIUM_RISK_THRESHOLD     = 40

# Pitch / command
DEFAULT_PITCH_COUNT_LIMIT      = 95
INNING_PITCH_OVERLOAD          = 25     # pitches in a single inning
COMMAND_COLLAPSE_WALK_TRIGGER  = 2

# Hard contact
HARD_CONTACT_CLUSTER_TRIGGER   = 2      # ≥ N hard-hit balls in inning
BARREL_TRIGGER                 = 1
HIGH_EXIT_VELOCITY             = 95.0   # mph

# Lineup / order
TOP_OF_ORDER_RANGE             = (1, 5)
THIRD_TIME_THROUGH             = 3

# Bullpen
HIGH_BULLPEN_FATIGUE           = 0.65
LOW_QUALITY_RELIEVER           = 0.40   # next_reliever_quality below

# Risk states
STATE_BASE_TRAFFIC        = "BASE_TRAFFIC_PRESSURE"
STATE_COMMAND_COLLAPSE    = "COMMAND_COLLAPSE_RISK"
STATE_BULLPEN_EXPLOSION   = "BULLPEN_EXPLOSION_RISK"
STATE_TWO_OUT_RALLY       = "TWO_OUT_RALLY_RISK"
STATE_HARD_CONTACT        = "HARD_CONTACT_CLUSTER"
STATE_LINEUP_TURNOVER     = "LINEUP_TURNOVER_DANGER"
STATE_CLEAN_INNING        = "CLEAN_INNING_LOW_RISK"

RISK_LOW    = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH   = "HIGH"


# ─────────────────────────────────────────────────────────────────────
# Coercion helpers (fail-soft)
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _b(v: Any) -> bool:
    return bool(v)


def _safe_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return str(v)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Derived inputs
# ─────────────────────────────────────────────────────────────────────
def _runners_state(metrics: dict) -> tuple[bool, bool, int]:
    """Return (risp, bases_loaded, runners_on_count)."""
    br = metrics.get("base_runners")
    risp = False
    loaded = False
    count = 0
    if isinstance(br, dict):
        first  = _b(br.get("first")  or br.get(1))
        second = _b(br.get("second") or br.get(2))
        third  = _b(br.get("third")  or br.get(3))
        count = sum([first, second, third])
        risp = second or third
        loaded = first and second and third
    elif isinstance(br, (list, tuple, set)):
        bases = {int(x) for x in br if str(x).isdigit() or isinstance(x, int)}
        count = len(bases & {1, 2, 3})
        risp = 2 in bases or 3 in bases
        loaded = bases >= {1, 2, 3}
    # Fallback: pre-derived flags
    if not risp:
        risp = _b(metrics.get("runners_in_scoring_position"))
    if not loaded:
        loaded = _b(metrics.get("bases_loaded"))
    return risp, loaded, count


def _batting_team_name(metrics: dict) -> Optional[str]:
    side = metrics.get("batting_team")
    if side == "home":
        return metrics.get("home_team") or "Local"
    if side == "away":
        return metrics.get("away_team") or "Visitante"
    half = metrics.get("half_inning")
    if half == "top":   # visitor bats
        return metrics.get("away_team") or "Visitante"
    if half == "bottom":
        return metrics.get("home_team") or "Local"
    return None


def _resolve_batting_side(metrics: dict) -> Optional[str]:
    if metrics.get("batting_team") in ("home", "away"):
        return metrics["batting_team"]
    half = metrics.get("half_inning")
    if half == "top":
        return "away"
    if half == "bottom":
        return "home"
    return None


# ─────────────────────────────────────────────────────────────────────
# Sub-detectors — return (points, code | None, human | None)
# ─────────────────────────────────────────────────────────────────────
def _detect_base_traffic(metrics: dict) -> tuple[int, list[str], list[str], dict]:
    """Detect base traffic pressure. Não tratar todas as bases iguais."""
    risp, loaded, count = _runners_state(metrics)
    outs = _i(metrics.get("outs"))
    walks = _i(metrics.get("walks_this_inning"))
    hits = _i(metrics.get("hits_this_inning"))

    pts = 0
    codes: list[str] = []
    human: list[str] = []
    flags = {"risp": risp, "bases_loaded": loaded, "runners_count": count}

    if loaded and outs <= 1:
        pts += 22
        codes.append("BASES_LOADED_LOW_OUTS")
        human.append(f"Bases llenas con {outs} out{'s' if outs != 1 else ''}")
    elif risp and outs <= 1:
        pts += 14
        codes.append("RISP_LOW_OUTS")
        human.append(f"Corredor en posición anotadora con {outs} out{'s' if outs != 1 else ''}")
    elif risp:
        pts += 6
        codes.append("RISP_TWO_OUTS")
        human.append("Corredor en posición anotadora con 2 outs")
    elif count >= 1 and outs <= 1:
        pts += 5
        codes.append("ANY_RUNNER_LOW_OUTS")

    if walks + hits >= 3:
        pts += 6
        codes.append("MULTI_BASERUNNER_INNING")
        human.append(f"{walks + hits} corredores embarcaron este inning")

    return pts, codes, human, flags


def _detect_command_collapse(metrics: dict) -> tuple[int, list[str], list[str], dict]:
    """Pitcher command collapse: pitches/inning, walks, falling behind."""
    pitches_inn = _i(metrics.get("pitches_this_inning"))
    walks = _i(metrics.get("walks_this_inning"))
    fb_rate = _f(metrics.get("falling_behind_count_rate"))
    wp_hbp = _b(metrics.get("wild_pitch_or_hbp"))
    pitch_count = _i(metrics.get("pitch_count"))
    limit = _i(metrics.get("pitch_count_threshold"), DEFAULT_PITCH_COUNT_LIMIT)

    pts = 0
    codes: list[str] = []
    human: list[str] = []
    flags = {
        "pitches_inning_over": pitches_inn >= INNING_PITCH_OVERLOAD,
        "walks_trigger":       walks >= COMMAND_COLLAPSE_WALK_TRIGGER,
        "pitch_count_high":    limit > 0 and pitch_count >= limit,
    }

    if pitches_inn >= INNING_PITCH_OVERLOAD:
        pts += 12
        codes.append("INNING_PITCH_OVERLOAD")
        human.append(f"{pitches_inn} pitcheos en el inning (sobrecarga)")
    elif pitches_inn >= 18:
        pts += 6
        codes.append("INNING_PITCH_ELEVATED")

    if walks >= COMMAND_COLLAPSE_WALK_TRIGGER:
        pts += 12
        codes.append("MULTI_WALK_INNING")
        human.append(f"{walks} bases por bola en el inning")

    if fb_rate >= 0.55:
        pts += 8
        codes.append("FALLING_BEHIND_HITTERS")
        human.append(f"Pitcher cayendo en cuentas {fb_rate*100:.0f}%")
    elif fb_rate >= 0.40:
        pts += 4

    if wp_hbp:
        pts += 5
        codes.append("WILD_PITCH_OR_HBP")
        human.append("Wild pitch o HBP este inning")

    if limit > 0 and pitch_count >= limit:
        pts += 8
        codes.append("PITCH_COUNT_OVER_LIMIT")
        human.append(f"Pitcher con {pitch_count} pitcheos (sobre el límite {limit})")
    elif limit > 0 and pitch_count >= int(limit * 0.85):
        pts += 4

    return pts, codes, human, flags


def _detect_hard_contact_cluster(metrics: dict) -> tuple[int, list[str], list[str], dict]:
    """Cluster de batazos duros: hard-hit, EV, barrels, line drives."""
    hard = _i(metrics.get("hard_contact_this_inning"))
    barrels = _i(metrics.get("barrels_this_inning"))
    ev = _f(metrics.get("avg_exit_velocity"))
    line_drives = _i(metrics.get("line_drives_this_inning"))

    pts = 0
    codes: list[str] = []
    human: list[str] = []
    flags = {
        "hard_contact_cluster": hard >= HARD_CONTACT_CLUSTER_TRIGGER,
        "barrel_present":       barrels >= BARREL_TRIGGER,
        "high_ev":              ev >= HIGH_EXIT_VELOCITY,
    }

    if hard >= HARD_CONTACT_CLUSTER_TRIGGER:
        pts += 12
        codes.append("HARD_CONTACT_CLUSTER")
        human.append(f"{hard} batazos duros en el inning")

    if barrels >= BARREL_TRIGGER:
        pts += 10
        codes.append("BARREL_PRESENT")
        human.append(f"{barrels} barrel{'s' if barrels != 1 else ''} en el inning")

    if ev >= HIGH_EXIT_VELOCITY:
        pts += 6
        codes.append("HIGH_EXIT_VELOCITY")
        human.append(f"Velocidad de salida promedio {ev:.0f} mph")

    if line_drives >= 2:
        pts += 4
        codes.append("LINE_DRIVE_CLUSTER")

    return pts, codes, human, flags


def _detect_lineup_turnover(metrics: dict) -> tuple[int, list[str], list[str], dict]:
    """Top order due / 3rd time through / platoon mismatch."""
    due_up = _i(metrics.get("lineup_position_due_up"))
    ttt = _i(metrics.get("times_through_order"))
    handed = (_safe_str(metrics.get("handedness_matchup")) or "").lower()

    pts = 0
    codes: list[str] = []
    human: list[str] = []
    lo, hi = TOP_OF_ORDER_RANGE
    flags = {
        "top_of_order_due":     lo <= due_up <= hi,
        "third_time_through":   ttt >= THIRD_TIME_THROUGH,
        "platoon_unfavorable":  handed == "unfavorable",
    }

    if lo <= due_up <= hi:
        pts += 10
        codes.append("TOP_OF_ORDER_DUE")
        human.append(f"Top de la alineación al bate (pos {due_up})")

    if ttt >= THIRD_TIME_THROUGH:
        pts += 8
        codes.append("THIRD_TIME_THROUGH_ORDER")
        human.append(f"{ttt}ª vuelta a la alineación contra el mismo pitcher")

    if handed == "unfavorable":
        pts += 6
        codes.append("PLATOON_UNFAVORABLE")
        human.append("Matchup de mano desfavorable para el pitcher")

    return pts, codes, human, flags


def _detect_bullpen_explosion(metrics: dict) -> tuple[int, list[str], list[str], dict]:
    """Bullpen explosion risk: starter early out, fatigue, low-quality
    reliever, back-to-back usage."""
    starter_out = _b(metrics.get("starter_removed_early"))
    fatigue = _f(metrics.get("bullpen_fatigue"))
    next_quality = _f(metrics.get("next_reliever_quality"))
    back_to_back = _b(metrics.get("reliever_back_to_back"))
    role = (_safe_str(metrics.get("pitcher_role")) or "").lower()

    pts = 0
    codes: list[str] = []
    human: list[str] = []
    flags = {
        "starter_removed_early": starter_out,
        "high_fatigue":          fatigue >= HIGH_BULLPEN_FATIGUE,
        "low_quality_reliever":  0 < next_quality <= LOW_QUALITY_RELIEVER,
        "reliever_back_to_back": back_to_back,
    }

    if starter_out:
        pts += 10
        codes.append("STARTER_REMOVED_EARLY")
        human.append("Abridor salió temprano (bullpen forzado)")

    if fatigue >= HIGH_BULLPEN_FATIGUE:
        pts += 12
        codes.append("BULLPEN_FATIGUE_HIGH")
        human.append(f"Bullpen con fatiga alta ({fatigue*100:.0f}%)")
    elif fatigue >= 0.45:
        pts += 5

    # 0 = unknown, so require > 0 for the penalty
    if 0 < next_quality <= LOW_QUALITY_RELIEVER:
        pts += 10
        codes.append("LOW_QUALITY_NEXT_RELIEVER")
        human.append("Próximo relevista de calidad pobre")

    if back_to_back:
        pts += 6
        codes.append("RELIEVER_BACK_TO_BACK")
        human.append("Relevista usado días consecutivos")

    if role == "reliever" and fatigue >= 0.45:
        pts += 4
        codes.append("RELIEVER_IN_FATIGUE_BAND")

    return pts, codes, human, flags


def _detect_two_out_rally(metrics: dict, base_flags: dict) -> tuple[int, list[str], list[str]]:
    """Two-out rally risk — runners with 2 outs + command issues."""
    outs = _i(metrics.get("outs"))
    if outs != 2:
        return 0, [], []
    if not (base_flags.get("risp") or base_flags.get("runners_count", 0) >= 1):
        return 0, [], []
    walks = _i(metrics.get("walks_this_inning"))
    hits = _i(metrics.get("hits_this_inning"))
    if walks + hits < 2:
        return 0, [], []
    pts = 10
    return pts, ["TWO_OUT_RALLY_BUILDING"], ["Rally con 2 outs en construcción"]


# ─────────────────────────────────────────────────────────────────────
# Trap signal detection
# ─────────────────────────────────────────────────────────────────────
def _detect_trap_signals(metrics: dict,
                          base_flags: dict,
                          cmd_flags: dict,
                          bp_flags: dict,
                          pressure_score: float) -> list[str]:
    """Detect false-positive conditions that should suppress Over recs.

    These are not states — they are veto-style flags that downstream
    selection logic can use to skip a recommendation.
    """
    traps: list[str] = []

    outs = _i(metrics.get("outs"))
    risp = base_flags.get("risp", False)
    bottom_order = _i(metrics.get("lineup_position_due_up")) >= 7

    # Trap 1: market already moved beyond projected script.
    pre = metrics.get("pregame_total_line")
    live = metrics.get("live_total_line")
    if pre is not None and live is not None:
        if _f(live) - _f(pre) >= 1.0:
            traps.append("LINE_ALREADY_MOVED")
        if _f(live) - _f(pre) >= 1.5:
            traps.append("LINE_OVERREACTED")

    # Trap 2: base traffic with 2 outs and bottom of order due → false positive
    if outs == 2 and risp and bottom_order:
        traps.append("RISP_TWO_OUTS_BOTTOM_ORDER")

    # Trap 3: command flags raised but bullpen fresh + next reliever elite
    if cmd_flags.get("pitch_count_high") and not bp_flags.get("high_fatigue"):
        next_q = _f(metrics.get("next_reliever_quality"))
        if next_q >= 0.75:
            traps.append("ELITE_RELIEVER_DAMPENS_COLLAPSE")

    # Trap 4: pressure_score moderate but line gap already negative
    if pre is not None and live is not None:
        gap = _f(live) - _f(pre)
        if 30 <= pressure_score < 60 and gap >= 0.5:
            traps.append("MODERATE_PRESSURE_WITH_INFLATED_LINE")

    return traps


# ─────────────────────────────────────────────────────────────────────
# State classification (priority order)
# ─────────────────────────────────────────────────────────────────────
def _classify_state(pressure_score: float,
                     contribs: dict,
                     flags: dict) -> str:
    """Pick the dominant state — priority order matters. Highest danger
    classes win when multiple criteria fire."""
    # Bullpen explosion (most volatile)
    bp = flags.get("bullpen", {})
    if (bp.get("starter_removed_early") and bp.get("high_fatigue")) \
       or (bp.get("starter_removed_early") and bp.get("low_quality_reliever")):
        return STATE_BULLPEN_EXPLOSION

    # Command collapse
    cmd = flags.get("command", {})
    if cmd.get("walks_trigger") and (cmd.get("pitches_inning_over") or cmd.get("pitch_count_high")):
        return STATE_COMMAND_COLLAPSE

    # Hard contact cluster — bats are squaring up
    hc = flags.get("hard_contact", {})
    if hc.get("hard_contact_cluster") and (hc.get("barrel_present") or hc.get("high_ev")):
        return STATE_HARD_CONTACT

    # Base traffic pressure
    bt = flags.get("base", {})
    if bt.get("bases_loaded") or (bt.get("risp") and _i(flags.get("outs")) <= 1):
        return STATE_BASE_TRAFFIC

    # Two-out rally
    if "TWO_OUT_RALLY_BUILDING" in flags.get("reason_codes", []):
        return STATE_TWO_OUT_RALLY

    # Lineup turnover danger
    lt = flags.get("lineup", {})
    if lt.get("top_of_order_due") and (lt.get("third_time_through") or lt.get("platoon_unfavorable")):
        return STATE_LINEUP_TURNOVER

    # If pressure_score is still meaningful, prefer the dominant contributor
    if pressure_score >= MEDIUM_RISK_THRESHOLD:
        # Find dominant category
        winner = max(contribs.items(), key=lambda kv: kv[1]) if contribs else (None, 0)
        if winner[1] > 0:
            mapping = {
                "base_traffic":   STATE_BASE_TRAFFIC,
                "command":        STATE_COMMAND_COLLAPSE,
                "hard_contact":   STATE_HARD_CONTACT,
                "lineup":         STATE_LINEUP_TURNOVER,
                "bullpen":        STATE_BULLPEN_EXPLOSION,
                "two_out_rally":  STATE_TWO_OUT_RALLY,
            }
            return mapping.get(winner[0], STATE_CLEAN_INNING)

    return STATE_CLEAN_INNING


# ─────────────────────────────────────────────────────────────────────
# Risk tier + confidence
# ─────────────────────────────────────────────────────────────────────
def _risk_tier(pressure_score: float) -> str:
    if pressure_score >= HIGH_RISK_THRESHOLD:
        return RISK_HIGH
    if pressure_score >= MEDIUM_RISK_THRESHOLD:
        return RISK_MEDIUM
    return RISK_LOW


def _compute_confidence(pressure_score: float,
                         traps: list[str],
                         flags: dict) -> tuple[int, str]:
    """Confidence reflects how clean the signal is. Traps subtract."""
    conf = pressure_score
    # Boosts for clean dual-signal states
    bp = flags.get("bullpen", {})
    cmd = flags.get("command", {})
    if cmd.get("walks_trigger") and cmd.get("pitches_inning_over"):
        conf += 6
    if bp.get("starter_removed_early") and bp.get("high_fatigue"):
        conf += 6
    hc = flags.get("hard_contact", {})
    if hc.get("hard_contact_cluster") and hc.get("barrel_present"):
        conf += 4

    # Trap penalties
    if "LINE_OVERREACTED" in traps:
        conf -= 25
    elif "LINE_ALREADY_MOVED" in traps:
        conf -= 12
    if "RISP_TWO_OUTS_BOTTOM_ORDER" in traps:
        conf -= 10
    if "ELITE_RELIEVER_DAMPENS_COLLAPSE" in traps:
        conf -= 10
    if "MODERATE_PRESSURE_WITH_INFLATED_LINE" in traps:
        conf -= 8

    conf = max(0, min(100, int(round(conf))))
    # `risk` = risk of the recommendation being wrong. Mirrors the
    # football corner engine convention: HIGH confidence → LOW risk.
    # This is independent from `risk_tier` (= intensity of the explosive
    # signal derived from raw pressure_score).
    if conf >= 70:
        risk = RISK_LOW
    elif conf >= 45:
        risk = RISK_MEDIUM
    else:
        risk = RISK_HIGH
    return conf, risk


# ─────────────────────────────────────────────────────────────────────
# Market selection
# ─────────────────────────────────────────────────────────────────────
def _select_safe_over_line(current_total_runs: int,
                            pregame_line: Optional[float],
                            live_line: Optional[float]) -> Optional[float]:
    """Pick a half-line that still has cushion vs current_total_runs."""
    base = live_line if live_line is not None else pregame_line
    if base is None:
        return None
    floor = current_total_runs + 0.5
    target = max(floor, _f(base) - 0.5)
    half = round(target * 2) / 2.0
    if abs(half - round(half)) < 0.1:
        half += 0.5
    if half < floor:
        half = floor
    return half


def _build_market_candidates(metrics: dict,
                               pressure_score: float,
                               state: str,
                               traps: list[str],
                               batting_side: Optional[str],
                               current_total_runs: int) -> list[dict]:
    """Return ranked market candidates for the UI."""
    out: list[dict] = []
    if pressure_score < MEDIUM_RISK_THRESHOLD:
        return out

    pregame_line = metrics.get("pregame_total_line")
    live_line = metrics.get("live_total_line")
    line_overreacted = "LINE_OVERREACTED" in traps
    line_moved = "LINE_ALREADY_MOVED" in traps

    # Inning Over 0.5 — immediate pressure
    if state in (STATE_BASE_TRAFFIC, STATE_BULLPEN_EXPLOSION,
                  STATE_COMMAND_COLLAPSE, STATE_HARD_CONTACT,
                  STATE_TWO_OUT_RALLY):
        out.append({
            "market":    "Inning Over 0.5 carreras",
            "category":  "INNING_OVER_0_5",
            "score":     round(pressure_score, 1),
            "rationale": "Presión inmediata: alta probabilidad de al menos 1 carrera este inning.",
        })

    # Team Total Over — pressure team-specific (the batting team)
    if batting_side in ("home", "away") and pressure_score >= HIGH_RISK_THRESHOLD:
        team_name = (metrics.get("home_team") if batting_side == "home"
                      else metrics.get("away_team")) or batting_side.title()
        out.append({
            "market":    f"Team Total Over — {team_name}",
            "category":  "TEAM_TOTAL_OVER",
            "side":      batting_side,
            "score":     round(pressure_score * 0.92, 1),
            "rationale": f"Presión específica al ataque de {team_name}.",
        })

    # Live Total Over — only if line still has cushion (not overreacted)
    if not line_overreacted:
        line = _select_safe_over_line(current_total_runs, _f(pregame_line) if pregame_line is not None else None,
                                       _f(live_line) if live_line is not None else None)
        if line is not None:
            out.append({
                "market":    f"Live Total Over {line:.1f}",
                "category":  "LIVE_TOTAL_OVER",
                "line":      line,
                "score":     round(pressure_score * (0.78 if line_moved else 0.88), 1),
                "rationale": (
                    "Línea aún con colchón vs el guion proyectado."
                    if not line_moved else
                    "Línea ya movió pero el guion la sostiene parcialmente."
                ),
            })

    # Watchlist if pressure real but line inflated → no rec, just track
    if line_overreacted and pressure_score >= MEDIUM_RISK_THRESHOLD:
        out.append({
            "market":    "Watchlist — no recomendar",
            "category":  "WATCHLIST",
            "score":     round(pressure_score * 0.50, 1),
            "rationale": "Presión real pero la línea ya sobre-reaccionó.",
        })

    # Sort by score desc
    out.sort(key=lambda c: c.get("score") or 0, reverse=True)
    return out


def _build_avoid_markets(state: str,
                          pressure_score: float,
                          traps: list[str]) -> list[str]:
    avoid: list[str] = []
    if state == STATE_CLEAN_INNING and pressure_score < MEDIUM_RISK_THRESHOLD:
        return avoid
    # Full Game Under is dangerous when explosive risk is rising
    if pressure_score >= MEDIUM_RISK_THRESHOLD:
        avoid.append("Full Game Under")
    if state == STATE_BULLPEN_EXPLOSION:
        avoid.append("Live Under (próximos innings)")
        avoid.append("Under 7.5 / Under 8.0")
    if state in (STATE_COMMAND_COLLAPSE, STATE_HARD_CONTACT):
        avoid.append("Inning Under 0.5")
    if "LINE_OVERREACTED" in traps:
        avoid.append("Live Over (línea ya sobre-reaccionó)")
    return avoid


# ─────────────────────────────────────────────────────────────────────
# Phase 10 — Statcast contact detector (pregame Statcast context)
# ─────────────────────────────────────────────────────────────────────
def _detect_statcast_contact_context(
    metrics: dict,
    pitching_side: Optional[str],
    batting_side: Optional[str],
) -> tuple[int, list[str], list[str], dict]:
    """Adjust the inning pressure with pregame Statcast contact context.

    Reads ``metrics["advanced_stats_snapshot"]`` (canonical shape from
    ``mlb_statcast_adapter``). Fail-soft: returns ``(0, [], [], {})`` if
    snapshot missing or incomplete.

    Sign convention (additive to pressure score, capped at ±8):
      * pitcher serving (pitching side) high barrel/hard-hit/xwOBA
        allowed → +pressure (explosive risk).
      * batting side team_barrel_pct & team_xwoba elevated → +pressure.
      * Both starters with low hard-contact + low xwOBA allowed
        → -pressure (cooling factor).
    """
    snap = metrics.get("advanced_stats_snapshot")
    if not isinstance(snap, dict):
        return 0, [], [], {}

    def _pitcher(side: str) -> dict:
        blk = snap.get(f"{side}_pitcher_advanced") or {}
        return (blk.get("pitcher") or {}) if isinstance(blk, dict) else {}

    def _team(side: str) -> dict:
        blk = snap.get(f"{side}_team_advanced") or {}
        return (blk.get("team") or {}) if isinstance(blk, dict) else {}

    home_p = _pitcher("home")
    away_p = _pitcher("away")
    home_t = _team("home")
    away_t = _team("away")

    # Resolve which pitcher is currently serving + which team is batting.
    # If the caller does not supply sides, fall back to "either" worst-case
    # (we only react to the most fragile pitcher on the mound).
    serving_p: dict = {}
    batting_t: dict = {}
    if pitching_side == "home":
        serving_p = home_p
        batting_t = away_t  # away bats when home pitches
    elif pitching_side == "away":
        serving_p = away_p
        batting_t = home_t
    else:
        # Use the more fragile of the two as a conservative fallback.
        bar_h = home_p.get("barrel_pct_allowed") or 0
        bar_a = away_p.get("barrel_pct_allowed") or 0
        serving_p = home_p if bar_h >= bar_a else away_p
        if batting_side == "home":
            batting_t = home_t
        elif batting_side == "away":
            batting_t = away_t

    pts = 0
    codes: list[str] = []
    humans: list[str] = []
    flags: dict[str, Any] = {}

    # Pitcher fragility — Barrel %
    bar = serving_p.get("barrel_pct_allowed")
    if bar is not None and bar >= 9.0:
        pts += 4
        codes.append("BARREL_RISK_ELEVATED")
        humans.append(
            f"Lanzador permite barrel% ≥9% (actual {bar:.1f}%) — riesgo elevado de inning explosivo."
        )
        flags["pitcher_barrel_high"] = True

    # Pitcher fragility — Hard hit %
    har = serving_p.get("hard_hit_pct_allowed")
    if har is not None and har >= 42.0:
        pts += 3
        if "STATCAST_HARD_CONTACT_SUPPORT" not in codes:
            codes.append("STATCAST_HARD_CONTACT_SUPPORT")
        humans.append(
            f"Lanzador permite hard-hit% ≥42% (actual {har:.1f}%) — bombardeo probable."
        )
        flags["pitcher_hard_hit_high"] = True

    # Pitcher xwOBA allowed
    xw_p = serving_p.get("xwoba_allowed")
    if xw_p is not None and xw_p >= 0.345:
        pts += 3
        if "PITCHER_XWOBA_WARNING" not in codes:
            codes.append("PITCHER_XWOBA_WARNING")
        humans.append(
            f"xwOBA permitida ≥0.345 (actual {xw_p:.3f}) — calidad de contacto contra el abridor es alta."
        )
        flags["pitcher_xwoba_high"] = True

    # Batting team power profile (only when we know who's at bat)
    if batting_t:
        t_bar = batting_t.get("team_barrel_pct")
        t_xw = batting_t.get("team_xwoba")
        if t_bar is not None and t_bar >= 9.0 and t_xw is not None and t_xw >= 0.330:
            pts += 3
            codes.append("POWER_BAT_STATCAST_SUPPORT")
            humans.append(
                f"Equipo al bate con perfil power (barrel% {t_bar:.1f}, xwOBA {t_xw:.3f})."
            )
            flags["batting_power_profile"] = True

    # Cooling factor — both pitchers strong (rare but useful)
    if home_p and away_p:
        h_xw = home_p.get("xwoba_allowed")
        a_xw = away_p.get("xwoba_allowed")
        h_bar = home_p.get("barrel_pct_allowed")
        a_bar = away_p.get("barrel_pct_allowed")
        if (h_xw is not None and h_xw <= 0.305
                and a_xw is not None and a_xw <= 0.305
                and (h_bar is None or h_bar <= 6.5)
                and (a_bar is None or a_bar <= 6.5)):
            pts -= 4
            codes.append("LOW_HARD_CONTACT_ENVIRONMENT")
            humans.append(
                "Ambos abridores con xwOBA permitida ≤0.305 y barrel% ≤6.5 — ambiente cooled."
            )
            flags["env_cooled"] = True

    # Clamp at ±8 (single helper budget)
    pts = max(-8, min(8, pts))
    return pts, codes, humans, flags


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────
def evaluate_explosive_inning(metrics: dict,
                                *,
                                surface_threshold: int = DEFAULT_SURFACE_THRESHOLD,
                                ) -> dict:
    """Main entry. ALWAYS runs (per the user's spec for live engines).

    Returns the full ``run_recommendation`` dict described in the module
    docstring. Fail-soft on missing inputs.
    """
    metrics = metrics or {}

    # ── derive shared state ────────────────────────────────────────
    risp, loaded, count = _runners_state(metrics)
    base_flags_full = {"risp": risp, "bases_loaded": loaded, "runners_count": count}
    batting_side = _resolve_batting_side(metrics)
    batting_team = _batting_team_name(metrics)
    current_total_runs = (
        _i(metrics.get("current_total_runs"))
        or (_i(metrics.get("score_home")) + _i(metrics.get("score_away")))
    )

    # ── sub-detectors ──────────────────────────────────────────────
    pts_base, codes_base, hum_base, flags_base = _detect_base_traffic(metrics)
    pts_cmd,  codes_cmd,  hum_cmd,  flags_cmd  = _detect_command_collapse(metrics)
    pts_hc,   codes_hc,   hum_hc,   flags_hc   = _detect_hard_contact_cluster(metrics)
    pts_lin,  codes_lin,  hum_lin,  flags_lin  = _detect_lineup_turnover(metrics)
    pts_bp,   codes_bp,   hum_bp,   flags_bp   = _detect_bullpen_explosion(metrics)

    # two-out rally needs base flags
    pts_2o, codes_2o, hum_2o = _detect_two_out_rally(metrics, base_flags_full)

    # Phase 10 — Statcast contact context (pregame snapshot adjustment).
    pts_sc, codes_sc, hum_sc, flags_sc = _detect_statcast_contact_context(
        metrics,
        pitching_side=(_safe_str(metrics.get("pitching_team")) or "").lower() or None,
        batting_side=batting_side,
    )

    contribs = {
        "base_traffic":   pts_base,
        "command":        pts_cmd,
        "hard_contact":   pts_hc,
        "lineup":         pts_lin,
        "bullpen":        pts_bp,
        "two_out_rally":  pts_2o,
        "statcast_contact": pts_sc,
    }
    pressure_score = float(sum(contribs.values()))
    pressure_score = max(0.0, min(100.0, pressure_score))

    # ── trap signals ───────────────────────────────────────────────
    traps = _detect_trap_signals(metrics, base_flags_full, flags_cmd,
                                   flags_bp, pressure_score)

    # ── reason codes / human reasons ──────────────────────────────
    reason_codes = (codes_base + codes_cmd + codes_hc + codes_lin
                    + codes_bp + codes_2o + codes_sc)
    human_reasons = (hum_base + hum_cmd + hum_hc + hum_lin
                      + hum_bp + hum_2o + hum_sc)

    # ── state classification ──────────────────────────────────────
    state_flags = {
        "base":          base_flags_full,
        "command":       flags_cmd,
        "hard_contact":  flags_hc,
        "lineup":        flags_lin,
        "bullpen":       flags_bp,
        "outs":          _i(metrics.get("outs")),
        "reason_codes":  reason_codes,
    }
    state = _classify_state(pressure_score, contribs, state_flags)

    # ── confidence / risk ─────────────────────────────────────────
    confidence, risk = _compute_confidence(pressure_score, traps, state_flags)
    risk_tier = _risk_tier(pressure_score)

    # ── market candidates / avoid ─────────────────────────────────
    candidates = _build_market_candidates(metrics, pressure_score, state,
                                            traps, batting_side, current_total_runs)
    avoid_markets = _build_avoid_markets(state, pressure_score, traps)

    # ── should_recommend / market pick ────────────────────────────
    should_recommend = (
        pressure_score >= surface_threshold
        and risk in (RISK_LOW, RISK_MEDIUM)         # HIGH risk = wait
        and "LINE_OVERREACTED" not in traps
        and state != STATE_CLEAN_INNING
    )

    recommended_market = None
    recommended_line = None
    recommended_odds = None
    if should_recommend and candidates:
        top = candidates[0]
        if top.get("category") != "WATCHLIST":
            recommended_market = top.get("market")
            recommended_line = top.get("line")
            # Try to resolve odds from current_odds dict
            current_odds = metrics.get("current_odds") or {}
            if isinstance(current_odds, dict):
                cat = top.get("category")
                if cat == "INNING_OVER_0_5":
                    recommended_odds = current_odds.get("inning_over_0_5")
                elif cat == "TEAM_TOTAL_OVER":
                    side = top.get("side")
                    key = f"team_total_over_{side}"
                    recommended_odds = current_odds.get(key)
                elif cat == "LIVE_TOTAL_OVER" and recommended_line is not None:
                    key = f"live_over_{recommended_line:.1f}"
                    recommended_odds = current_odds.get(key)

    # ── flip_triggered (Under → Over flip) ────────────────────────
    # A flip is meaningful when:
    #   * pre-existing recommendation was Under (we don't track that here
    #     directly, but the caller can override). We expose the flag and
    #     let `evaluate_explosive_inning` mark it whenever pressure_score
    #     >= HIGH and risk != HIGH and traps don't veto.
    flip_triggered = bool(
        pressure_score >= HIGH_RISK_THRESHOLD
        and "LINE_OVERREACTED" not in traps
        and "RISP_TWO_OUTS_BOTTOM_ORDER" not in traps
        and recommended_market is not None
    )

    # Allow caller to override via metrics.previous_recommendation_side
    prev = (_safe_str(metrics.get("previous_recommendation_side")) or "").lower()
    if prev and prev != "under":
        flip_triggered = False

    # ── narrative / explanation ──────────────────────────────────
    inning = _i(metrics.get("inning"))
    half = (_safe_str(metrics.get("half_inning")) or "").lower()
    half_es = {"top": "alta", "bottom": "baja"}.get(half, "")
    inning_label = f"{inning}ª {half_es}".strip() if inning else "inning actual"
    team_label = batting_team or "equipo al bate"

    if state == STATE_CLEAN_INNING:
        narrative = (
            f"Inning limpio — sin presión explosiva relevante (score "
            f"{pressure_score:.0f}/100)."
        )
        explanation = "No hay señales claras de inning explosivo en este momento."
    else:
        narrative = (
            f"{team_label} con presión {state.replace('_', ' ').lower()} "
            f"en el {inning_label} (score {pressure_score:.0f}/100, "
            f"tier {risk_tier})."
        )
        if recommended_market:
            explanation = (
                f"{team_label} genera presión consistente en el {inning_label}. "
                f"Recomendación: {recommended_market}. "
                f"Confianza {confidence}/100 ({risk})."
            )
        else:
            explanation = (
                f"Presión detectada ({state}) pero "
                + ("la línea ya sobre-reaccionó. "
                    if "LINE_OVERREACTED" in traps else
                    "el riesgo es demasiado alto para ejecutar. ")
                + "Watchlist."
            )

    return {
        "explosive_inning_pressure_score": int(round(pressure_score)),
        "state":                  state,
        "risk_tier":              risk_tier,
        "confidence":             confidence,
        "risk":                   risk,
        "reason_codes":           reason_codes,
        "human_reasons":          human_reasons,
        "avoid_markets":          avoid_markets,
        "market_candidates":      candidates,
        "should_recommend":       bool(should_recommend),
        "recommended_market":     recommended_market,
        "recommended_line":       recommended_line,
        "recommended_odds":       recommended_odds,
        "flip_triggered":         flip_triggered,
        "explanation":            explanation,
        "trap_signals":           traps,
        "score_contributions":    contribs,
        "narrative_es":           narrative,
        "inning":                 inning if inning else None,
        "half_inning":            half or None,
        "batting_team":           batting_side,
        "batting_team_name":      batting_team,
        "current_total_runs":     current_total_runs,
        "version":                2,
    }


__all__ = [
    # Constants
    "DEFAULT_SURFACE_THRESHOLD",
    "HIGH_RISK_THRESHOLD",
    "MEDIUM_RISK_THRESHOLD",
    "STATE_BASE_TRAFFIC",
    "STATE_COMMAND_COLLAPSE",
    "STATE_BULLPEN_EXPLOSION",
    "STATE_TWO_OUT_RALLY",
    "STATE_HARD_CONTACT",
    "STATE_LINEUP_TURNOVER",
    "STATE_CLEAN_INNING",
    "RISK_LOW",
    "RISK_MEDIUM",
    "RISK_HIGH",
    # Entry point
    "evaluate_explosive_inning",
]
