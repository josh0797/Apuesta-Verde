"""MLB Offensive Pressure Base (Objetivo 2).

Detecta cuándo un partido MLB tiene **presión ofensiva oculta**: alto volumen
de hits (o times-on-base) frente a bajo run conversion. Estos escenarios
suelen romper Unders "controlados" porque el ruido es una bomba de tiempo.

Diseño (NON-NEGOTIABLE):
  * **Fail-soft**: si falta ``baseballHistoricalProfile`` / ``recentRunSplit`` /
    ``on_base_profile`` → retorna ``{"available": False, ...}`` sin reventar.
  * **Pure**: sin IO. Solo lee diccionarios.
  * **Determinista**: thresholds en constantes (ajustables) para que el
    test suite no dependa de magic numbers internos.
  * **Explicable**: cada equipo y el combinado llevan ``reasons`` con códigos
    canónicos (``HIGH_HIT_LOW_RUN``, ``COMBINED_HIDDEN_OFFENSIVE_PRESSURE``,
    ``LOW_PRESSURE_CONTROLLED``...).

Umbrales (especificación del usuario):
  * HIGH_PRESSURE   ── hits_avg_L5 >= 9.0 AND runs_avg_L5 <= 3.5
  * MODERATE_PRESSURE ── hits_avg_L5 >= 8.0 AND runs_avg_L5 <= 4.0
  * LOW_PRESSURE    ── hits_avg_L5 <= 6.5 AND runs_avg_L5 <= 3.5
  * NEUTRAL_PRESSURE ── otherwise (no clear signal)

Si ``live_hits`` opcional está disponible (in-game), se considera para
detectar `LIVE_HIT_ACCELERATION` (carrera de hits sin runs en curso).
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────
# Constants (intentionally module-level so tests can introspect/override)
# ─────────────────────────────────────────────────────────────────────
HIGH_PRESSURE      = "HIGH_PRESSURE"
MODERATE_PRESSURE  = "MODERATE_PRESSURE"
LOW_PRESSURE       = "LOW_PRESSURE"
NEUTRAL_PRESSURE   = "NEUTRAL_PRESSURE"
UNAVAILABLE        = "UNAVAILABLE"

# Per-team thresholds
HIGH_HITS_THRESHOLD     = 9.0
HIGH_RUNS_CEILING       = 3.5
MOD_HITS_THRESHOLD      = 8.0
MOD_RUNS_CEILING        = 4.0
LOW_HITS_CEILING        = 6.5
LOW_RUNS_CEILING        = 3.5

# Combined thresholds (sum of home + away)
COMBINED_HIGH_HITS      = 17.0   # ≈ both teams in HIGH or MODERATE
COMBINED_HIGH_RUNS_CAP  = 7.5
COMBINED_MOD_HITS       = 15.0
COMBINED_MOD_RUNS_CAP   = 8.5
COMBINED_LOW_HITS_CAP   = 12.5
COMBINED_LOW_RUNS_CAP   = 7.0

# Live override (when live_hits provided)
LIVE_HIT_ACCEL_DELTA    = 3.0    # +3 hits vs L5 baseline mid-game

# Reason codes (canonical — exported for downstream scorers/UI)
RC_HIGH_HIT_LOW_RUN              = "HIGH_HIT_LOW_RUN"
RC_MODERATE_HIT_LOW_RUN          = "MODERATE_HIT_LOW_RUN"
RC_LOW_HITS_QUIET_OFFENSE        = "LOW_HITS_QUIET_OFFENSE"
RC_COMBINED_HIDDEN_PRESSURE      = "COMBINED_HIDDEN_OFFENSIVE_PRESSURE"
RC_COMBINED_MOD_PRESSURE         = "COMBINED_MODERATE_OFFENSIVE_PRESSURE"
RC_LOW_PRESSURE_CONTROLLED       = "LOW_PRESSURE_CONTROLLED"
RC_NEUTRAL_PRESSURE              = "NEUTRAL_PRESSURE_SIGNAL"
RC_LIVE_HIT_ACCELERATION         = "LIVE_HIT_ACCELERATION"
RC_PRESSURE_DATA_MISSING         = "PRESSURE_DATA_MISSING"
RC_UNDER_PICK_HIGH_PRESSURE      = "UNDER_PICK_AT_HIGH_PRESSURE_RISK"
RC_UNDER_PICK_MODERATE_PRESSURE  = "UNDER_PICK_AT_MODERATE_PRESSURE_RISK"


_TIER_SCORES = {
    HIGH_PRESSURE:     80,
    MODERATE_PRESSURE: 55,
    NEUTRAL_PRESSURE:  35,
    LOW_PRESSURE:      15,
    UNAVAILABLE:       0,
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    """Coerce to float or None (handles strings, ints, NaN-ish)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _classify_tier(hits_l5: float | None, runs_l5: float | None) -> str:
    """Pure tier classifier using ONLY hits/runs L5."""
    if hits_l5 is None or runs_l5 is None:
        return UNAVAILABLE
    if hits_l5 >= HIGH_HITS_THRESHOLD and runs_l5 <= HIGH_RUNS_CEILING:
        return HIGH_PRESSURE
    if hits_l5 >= MOD_HITS_THRESHOLD and runs_l5 <= MOD_RUNS_CEILING:
        return MODERATE_PRESSURE
    if hits_l5 <= LOW_HITS_CEILING and runs_l5 <= LOW_RUNS_CEILING:
        return LOW_PRESSURE
    return NEUTRAL_PRESSURE


def _classify_combined_tier(
    hits_l5_combined: float | None,
    runs_l5_combined: float | None,
) -> str:
    """Combined tier classifier with looser thresholds."""
    if hits_l5_combined is None or runs_l5_combined is None:
        return UNAVAILABLE
    if (hits_l5_combined >= COMBINED_HIGH_HITS
            and runs_l5_combined <= COMBINED_HIGH_RUNS_CAP):
        return HIGH_PRESSURE
    if (hits_l5_combined >= COMBINED_MOD_HITS
            and runs_l5_combined <= COMBINED_MOD_RUNS_CAP):
        return MODERATE_PRESSURE
    if (hits_l5_combined <= COMBINED_LOW_HITS_CAP
            and runs_l5_combined <= COMBINED_LOW_RUNS_CAP):
        return LOW_PRESSURE
    return NEUTRAL_PRESSURE


# ─────────────────────────────────────────────────────────────────────
# Public — per team pressure
# ─────────────────────────────────────────────────────────────────────
def calculate_team_pressure_base(
    side_form: dict | None,
    *,
    runs_avg_l5: float | None = None,
    live_hits: int | float | None = None,
) -> dict:
    """Calculate pressure tier for ONE team using its L5/L15 form.

    Args:
        side_form: per-team block from ``on_base_profile.{home|away}``.
            Expected keys (any subset is OK, missing → fail-soft):
                ``hits_avg_last_5``, ``hits_avg_last_15``,
                ``walks_avg_last_5``, ``walks_avg_last_15``,
                ``times_on_base_avg_last_5``, ``times_on_base_avg_last_15``,
        runs_avg_l5: per-team runs average L5 from ``recent_run_split``
            (we read it externally because ``side_form`` doesn't carry it).
        live_hits: optional live hits count (for in-game override).

    Returns:
        dict with ``available``, ``pressure_tier``, ``score`` (0..100),
        ``reasons`` (canonical codes), ``inputs`` (echo for debugging).
    """
    if not isinstance(side_form, dict):
        return {
            "available":     False,
            "pressure_tier": UNAVAILABLE,
            "score":         0,
            "reasons":       [RC_PRESSURE_DATA_MISSING],
            "inputs":        {},
        }

    hits_l5  = _f(side_form.get("hits_avg_last_5"))
    hits_l15 = _f(side_form.get("hits_avg_last_15"))
    tob_l5   = _f(side_form.get("times_on_base_avg_last_5"))
    tob_l15  = _f(side_form.get("times_on_base_avg_last_15"))
    runs_l5  = _f(runs_avg_l5)
    live_h   = _f(live_hits)

    tier = _classify_tier(hits_l5, runs_l5)

    reasons: list[str] = []
    if tier == HIGH_PRESSURE:
        reasons.append(RC_HIGH_HIT_LOW_RUN)
    elif tier == MODERATE_PRESSURE:
        reasons.append(RC_MODERATE_HIT_LOW_RUN)
    elif tier == LOW_PRESSURE:
        reasons.append(RC_LOW_HITS_QUIET_OFFENSE)
    elif tier == NEUTRAL_PRESSURE:
        reasons.append(RC_NEUTRAL_PRESSURE)
    else:
        reasons.append(RC_PRESSURE_DATA_MISSING)

    # Live acceleration override — promote tier when in-game hits run hot
    if live_h is not None and hits_l5 is not None:
        if (live_h - hits_l5) >= LIVE_HIT_ACCEL_DELTA:
            reasons.append(RC_LIVE_HIT_ACCELERATION)
            if tier in (NEUTRAL_PRESSURE, LOW_PRESSURE):
                tier = MODERATE_PRESSURE
            elif tier == MODERATE_PRESSURE:
                tier = HIGH_PRESSURE

    score = _TIER_SCORES.get(tier, 0)
    return {
        "available":     tier != UNAVAILABLE,
        "pressure_tier": tier,
        "score":         score,
        "reasons":       reasons,
        "inputs": {
            "hits_avg_last_5":  hits_l5,
            "hits_avg_last_15": hits_l15,
            "times_on_base_avg_last_5":  tob_l5,
            "times_on_base_avg_last_15": tob_l15,
            "runs_avg_last_5": runs_l5,
            "live_hits":        live_h,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Public — match-level pressure context
# ─────────────────────────────────────────────────────────────────────
def calculate_match_pressure_context(payload: dict | None) -> dict:
    """Build the full ``pressure_base`` payload for a pick / match doc.

    Accepts a ``pick_payload``, ``match_doc`` or directly a dict with the
    shape ``{baseballHistoricalProfile: {...}}`` / ``{recent_run_split: ...,
    on_base_profile: ...}``.

    Returns the canonical shape::

        {
          "available": bool,
          "home": {"pressure_tier", "score", "reasons", "inputs"},
          "away": {"pressure_tier", "score", "reasons", "inputs"},
          "combined": {
              "pressure_tier", "score", "reasons", "flags",
              "inputs": {"hits_l5_combined": float, "runs_l5_combined": float}
          },
          "reason_codes": [str, ...],  # union of all reason codes
        }
    """
    if not isinstance(payload, dict):
        return _unavailable(reason=RC_PRESSURE_DATA_MISSING)

    # 1. Locate recent_run_split / on_base_profile, regardless of nesting.
    rrs, obp = _locate_form_blocks(payload)
    if not isinstance(rrs, dict) or not isinstance(obp, dict):
        return _unavailable(reason=RC_PRESSURE_DATA_MISSING)

    home_form = obp.get("home") if isinstance(obp.get("home"), dict) else {}
    away_form = obp.get("away") if isinstance(obp.get("away"), dict) else {}

    home_runs_l5 = rrs.get("runs_scored_avg_last_5_home")
    away_runs_l5 = rrs.get("runs_scored_avg_last_5_away")

    # 2. Live hits (optional) — read from common live_state shapes.
    live_box = _locate_live_hits(payload)
    home_live_hits = live_box.get("home")
    away_live_hits = live_box.get("away")

    home = calculate_team_pressure_base(
        home_form, runs_avg_l5=home_runs_l5, live_hits=home_live_hits,
    )
    away = calculate_team_pressure_base(
        away_form, runs_avg_l5=away_runs_l5, live_hits=away_live_hits,
    )

    # 3. Combined block — sum of hits + runs L5
    hits_l5_combined: float | None
    if home["inputs"].get("hits_avg_last_5") is not None \
            and away["inputs"].get("hits_avg_last_5") is not None:
        hits_l5_combined = round(
            home["inputs"]["hits_avg_last_5"] + away["inputs"]["hits_avg_last_5"], 3,
        )
    else:
        hits_l5_combined = None

    runs_l5_combined = _f(rrs.get("total_runs_avg_last_5"))
    if runs_l5_combined is None and home_runs_l5 is not None and away_runs_l5 is not None:
        runs_l5_combined = round(_f(home_runs_l5) + _f(away_runs_l5), 3)

    combined_tier = _classify_combined_tier(hits_l5_combined, runs_l5_combined)

    combined_reasons: list[str] = []
    if combined_tier == HIGH_PRESSURE:
        combined_reasons.append(RC_COMBINED_HIDDEN_PRESSURE)
    elif combined_tier == MODERATE_PRESSURE:
        combined_reasons.append(RC_COMBINED_MOD_PRESSURE)
    elif combined_tier == LOW_PRESSURE:
        combined_reasons.append(RC_LOW_PRESSURE_CONTROLLED)
    elif combined_tier == NEUTRAL_PRESSURE:
        combined_reasons.append(RC_NEUTRAL_PRESSURE)
    else:
        combined_reasons.append(RC_PRESSURE_DATA_MISSING)

    # Flags surface common downstream patterns
    flags = {
        "any_team_high":     home["pressure_tier"] == HIGH_PRESSURE
                              or away["pressure_tier"] == HIGH_PRESSURE,
        "both_teams_high":   home["pressure_tier"] == HIGH_PRESSURE
                              and away["pressure_tier"] == HIGH_PRESSURE,
        "any_team_moderate_or_high": home["pressure_tier"] in (
            HIGH_PRESSURE, MODERATE_PRESSURE,
        ) or away["pressure_tier"] in (HIGH_PRESSURE, MODERATE_PRESSURE),
        "both_teams_low":    home["pressure_tier"] == LOW_PRESSURE
                              and away["pressure_tier"] == LOW_PRESSURE,
        "live_acceleration": (
            RC_LIVE_HIT_ACCELERATION in home["reasons"]
            or RC_LIVE_HIT_ACCELERATION in away["reasons"]
        ),
    }

    # Union reason codes
    all_codes: list[str] = []
    for src in (home["reasons"], away["reasons"], combined_reasons):
        for rc in src:
            if rc not in all_codes:
                all_codes.append(rc)

    return {
        "available":      home["available"] or away["available"]
                          or combined_tier != UNAVAILABLE,
        "home":           home,
        "away":           away,
        "combined": {
            "pressure_tier": combined_tier,
            "score":         _TIER_SCORES.get(combined_tier, 0),
            "reasons":       combined_reasons,
            "flags":         flags,
            "inputs": {
                "hits_l5_combined":  hits_l5_combined,
                "runs_l5_combined":  runs_l5_combined,
            },
        },
        "reason_codes": all_codes,
    }


# ─────────────────────────────────────────────────────────────────────
# Downstream impact helper — used by orchestrator
# ─────────────────────────────────────────────────────────────────────
def derive_pressure_impact_for_under_pick(
    pressure_context: dict | None,
    *,
    pick_market: str | None = None,
) -> dict:
    """Return adjustment hints for a pick recommendation.

    Convention: positive ``fragility_delta`` increases fragility (worse
    for Under picks). ``confidence_delta`` is signed (Under support → +;
    Under risk → -). Capped at ±10.

    The orchestrator decides whether to apply these deltas — this
    function is pure.
    """
    market = (pick_market or "").lower()
    is_under = "under" in market and "team total" not in market
    out: dict[str, Any] = {
        "applied":           False,
        "fragility_delta":   0,
        "confidence_delta":  0,
        "reason_codes":      [],
    }
    if not isinstance(pressure_context, dict) or not pressure_context.get("available"):
        return out

    combined = pressure_context.get("combined") or {}
    tier = combined.get("pressure_tier")
    flags = combined.get("flags") or {}

    if is_under:
        if tier == HIGH_PRESSURE or flags.get("both_teams_high"):
            out["fragility_delta"]  = 8
            out["confidence_delta"] = -7
            out["reason_codes"].append(RC_UNDER_PICK_HIGH_PRESSURE)
            out["applied"] = True
        elif tier == MODERATE_PRESSURE or flags.get("any_team_high"):
            out["fragility_delta"]  = 5
            out["confidence_delta"] = -4
            out["reason_codes"].append(RC_UNDER_PICK_MODERATE_PRESSURE)
            out["applied"] = True
        elif tier == LOW_PRESSURE and flags.get("both_teams_low"):
            out["fragility_delta"]  = -3
            out["confidence_delta"] = 2
            out["reason_codes"].append(RC_LOW_PRESSURE_CONTROLLED)
            out["applied"] = True
    # For Over markets, mirror inversely (light effect)
    elif "over" in market and "team total" not in market:
        if tier == HIGH_PRESSURE or flags.get("any_team_high"):
            out["confidence_delta"] = 3
            out["reason_codes"].append(RC_HIGH_HIT_LOW_RUN)
            out["applied"] = True
        elif tier == LOW_PRESSURE and flags.get("both_teams_low"):
            out["confidence_delta"] = -2
            out["reason_codes"].append(RC_LOW_PRESSURE_CONTROLLED)
            out["applied"] = True

    # Clamp
    out["fragility_delta"]  = max(-10, min(10, out["fragility_delta"]))
    out["confidence_delta"] = max(-10, min(10, out["confidence_delta"]))
    return out


# ─────────────────────────────────────────────────────────────────────
# Internal locators
# ─────────────────────────────────────────────────────────────────────
def _locate_form_blocks(payload: dict) -> tuple[dict | None, dict | None]:
    """Return ``(recent_run_split, on_base_profile)`` from any common
    shape (pick_payload, baseballHistoricalProfile mirror, or raw blocks).
    """
    # 1. Pick payload root
    rrs = payload.get("recent_run_split")
    obp = payload.get("on_base_profile")
    if isinstance(rrs, dict) and isinstance(obp, dict):
        return rrs, obp

    # 2. baseballHistoricalProfile mirror
    bhp = payload.get("baseballHistoricalProfile") or {}
    if isinstance(bhp, dict):
        rrs2 = bhp.get("recentRunSplit") or bhp.get("recent_run_split")
        obp2 = bhp.get("onBaseProfileL5") or bhp.get("on_base_profile")
        if isinstance(rrs2, dict) and isinstance(obp2, dict):
            return rrs2, obp2

    return rrs if isinstance(rrs, dict) else None, obp if isinstance(obp, dict) else None


def _locate_live_hits(payload: dict) -> dict:
    """Return ``{"home": int|None, "away": int|None}`` from live_state."""
    out = {"home": None, "away": None}
    live = payload.get("live_state") or payload.get("live_stats")
    if not isinstance(live, dict):
        return out
    box = live.get("box_score") if isinstance(live.get("box_score"), dict) else {}
    hits = box.get("hits") if isinstance(box.get("hits"), dict) else {}
    if hits:
        out["home"] = _f(hits.get("home"))
        out["away"] = _f(hits.get("away"))
    return out


def _unavailable(reason: str) -> dict:
    return {
        "available": False,
        "home":      {"available": False, "pressure_tier": UNAVAILABLE,
                      "score": 0, "reasons": [reason], "inputs": {}},
        "away":      {"available": False, "pressure_tier": UNAVAILABLE,
                      "score": 0, "reasons": [reason], "inputs": {}},
        "combined": {
            "pressure_tier": UNAVAILABLE,
            "score":         0,
            "reasons":       [reason],
            "flags":         {},
            "inputs":        {"hits_l5_combined": None, "runs_l5_combined": None},
        },
        "reason_codes": [reason],
    }


__all__ = [
    # Constants
    "HIGH_PRESSURE", "MODERATE_PRESSURE", "LOW_PRESSURE",
    "NEUTRAL_PRESSURE", "UNAVAILABLE",
    "HIGH_HITS_THRESHOLD", "HIGH_RUNS_CEILING",
    "MOD_HITS_THRESHOLD",  "MOD_RUNS_CEILING",
    "LOW_HITS_CEILING",    "LOW_RUNS_CEILING",
    # Reason codes
    "RC_HIGH_HIT_LOW_RUN", "RC_MODERATE_HIT_LOW_RUN",
    "RC_LOW_HITS_QUIET_OFFENSE", "RC_COMBINED_HIDDEN_PRESSURE",
    "RC_COMBINED_MOD_PRESSURE", "RC_LOW_PRESSURE_CONTROLLED",
    "RC_NEUTRAL_PRESSURE", "RC_LIVE_HIT_ACCELERATION",
    "RC_PRESSURE_DATA_MISSING",
    "RC_UNDER_PICK_HIGH_PRESSURE", "RC_UNDER_PICK_MODERATE_PRESSURE",
    # API
    "calculate_team_pressure_base",
    "calculate_match_pressure_context",
    "derive_pressure_impact_for_under_pick",
]
