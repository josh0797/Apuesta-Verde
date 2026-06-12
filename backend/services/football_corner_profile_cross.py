"""Football Corner Profile Cross L5 vs L15 — Fix 3 (Phase F59).

Cross-classifies the corner-event profile of both teams' L5 vs L15 to
detect Under/Over edges in **corner markets** when the main 1X2 / BTTS /
goals markets don't have a clear value bet.

Six profiles
------------
1. ``STRONG_CORNERS_UNDER_CROSS`` — ambos generan pocos y conceden pocos.
2. ``LOW_CORNERS_CROSS``          — ambos generan pocos (defensa neutral).
3. ``STRONG_CORNERS_OVER_CROSS``  — ambos generan muchos y conceden muchos.
4. ``HIGH_CORNERS_CROSS``         — ambos generan muchos O ambos conceden muchos.
5. ``ASYMMETRIC_CORNERS_PROFILE`` — uno genera y el otro concede mucho.
6. ``MIXED_CORNERS_PROFILE``      — no hay cruce limpio.

External confirmation
---------------------
Cuando se recibe un ``scores24_payload`` (resultado del scraper
:mod:`services.scores24_scraper`) con una predicción explícita de corners
(side=UNDER|OVER + line + odds), se evalúa contra el perfil del engine y
se emite ``external_confirmation`` (alineado) o ``external_conflict``
(opuesto).

Contract
--------
* Pure module, **fail-soft**, no I/O.
* Devuelve siempre un dict con la misma forma (campos None cuando no
  hay datos).
* Enrichment-only: NO decide picks por sí solo. El orchestrator decide.
"""
from __future__ import annotations

from typing import Any, Optional

ENGINE_VERSION = "football_corner_profile_cross.v1"

# ── Threshold knobs (Fix 3 spec) ─────────────────────────────────────
LOW_CORNER_FOR_L5    = 4.0    # team generates ≤4 corners / match
HIGH_CORNER_FOR_L5   = 5.5    # team generates ≥5.5 corners / match
HIGH_CORNER_FOR_L5_SOFT = 5.0
LOW_CORNER_AGAINST_L5  = 4.0  # team concedes ≤4
HIGH_CORNER_AGAINST_L5 = 5.0  # team concedes ≥5 (over_cross / high_cross)

# Reason codes
RC_BOTH_LOW_FOR             = "BOTH_TEAMS_LOW_CORNERS_FOR_L5"
RC_BOTH_LOW_AGAINST         = "BOTH_TEAMS_LOW_CORNERS_AGAINST_L5"
RC_BOTH_HIGH_FOR            = "BOTH_TEAMS_HIGH_CORNERS_FOR_L5"
RC_BOTH_HIGH_AGAINST        = "BOTH_TEAMS_HIGH_CORNERS_AGAINST_L5"
RC_STRONG_CORNERS_UNDER     = "STRONG_CORNERS_UNDER_CROSS"
RC_LOW_CORNERS              = "LOW_CORNERS_CROSS"
RC_STRONG_CORNERS_OVER      = "STRONG_CORNERS_OVER_CROSS"
RC_HIGH_CORNERS             = "HIGH_CORNERS_CROSS"
RC_ASYMMETRIC_CORNERS       = "ASYMMETRIC_CORNERS_PROFILE"
RC_MIXED_CORNERS            = "MIXED_CORNERS_PROFILE_NO_CLEAR_EDGE"
RC_S24_CONFIRMS             = "SCORES24_CORNERS_CONTEXT_CONFIRMS_ENGINE"
RC_S24_CONFLICT             = "SCORES24_CORNERS_CONTEXT_CONFLICT"

# Profile keys
PROFILE_STRONG_UNDER = "STRONG_CORNERS_UNDER_CROSS"
PROFILE_LOW         = "LOW_CORNERS_CROSS"
PROFILE_STRONG_OVER = "STRONG_CORNERS_OVER_CROSS"
PROFILE_HIGH        = "HIGH_CORNERS_CROSS"
PROFILE_ASYMMETRIC  = "ASYMMETRIC_CORNERS_PROFILE"
PROFILE_MIXED       = "MIXED_CORNERS_PROFILE"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _safe(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _slice_avg(values: list, k: int) -> Optional[float]:
    """Mean of the first ``k`` numeric values in ``values`` (newest-first)."""
    if not values:
        return None
    nums: list[float] = []
    for v in values[:k]:
        f = _safe(v)
        if f is not None:
            nums.append(f)
    if not nums:
        return None
    return round(sum(nums) / len(nums), 3)


def _round(v: Optional[float], ndigits: int = 2) -> Optional[float]:
    return None if v is None else round(v, ndigits)


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 3)


def extract_corner_side_from_match(
    match: dict | None,
    prefix: str,
) -> dict:
    """Extract a per-side corner stats block from FLAT keys on the match root.

    Phase F64 — supports the canonical user-provided shape::

        match = {
          "home_corners_for_l5":      4.2,
          "home_corners_for_l15":     4.8,
          "home_corners_against_l5":  3.8,
          "home_corners_against_l15": 4.1,
          "away_corners_for_l5":      5.1,
          "away_corners_for_l15":     4.7,
          "away_corners_against_l5":  4.9,
          "away_corners_against_l15": 5.0,
          ...
        }

    ``prefix`` must be ``"home"`` or ``"away"``. Returns a side-dict in the
    shape that :func:`compute_football_corner_profile_cross` accepts
    directly via the flat-keys branch (``corners_for_l5`` etc).

    Fail-soft: missing match → ``{}``; missing keys → ``None`` per field.
    """
    if not isinstance(match, dict) or prefix not in ("home", "away"):
        return {}
    cf5  = _safe(match.get(f"{prefix}_corners_for_l5"))
    cf15 = _safe(match.get(f"{prefix}_corners_for_l15"))
    ca5  = _safe(match.get(f"{prefix}_corners_against_l5"))
    ca15 = _safe(match.get(f"{prefix}_corners_against_l15"))
    return {
        "corners_for_l5":      cf5,
        "corners_for_l15":     cf15,
        "corners_against_l5":  ca5,
        "corners_against_l15": ca15,
    }


def _derive_team_corner_block(side: dict | None) -> dict:
    """From a team side dict (with ``recent_fixtures``), derive the
    corner aggregates L5/L15 + deltas.

    Accepts both the pre-normalised dict shape with arrays
    (``recent_fixtures = {"corners_for":[...], "corners_against":[...]}``)
    and a list of per-match dicts.
    """
    if not isinstance(side, dict):
        side = {}
    blk = side.get("context") if isinstance(side.get("context"), dict) else side
    recent = blk.get("recent_fixtures") or blk.get("last_matches") or {}

    cf_list: list = []
    ca_list: list = []

    if isinstance(recent, dict):
        cf_list = list(recent.get("corners_for") or recent.get("corners") or [])
        ca_list = list(recent.get("corners_against") or [])
    elif isinstance(recent, list):
        for f in recent:
            if not isinstance(f, dict):
                continue
            cf = f.get("corners_for") if f.get("corners_for") is not None else f.get("corners")
            ca = f.get("corners_against")
            if cf is not None:
                cf_list.append(cf)
            if ca is not None:
                ca_list.append(ca)

    cf5  = _slice_avg(cf_list, 5)
    cf15 = _slice_avg(cf_list, 15)
    ca5  = _slice_avg(ca_list, 5)
    ca15 = _slice_avg(ca_list, 15)
    total5  = None if (cf5 is None or ca5 is None) else round(cf5 + ca5, 3)
    total15 = None if (cf15 is None or ca15 is None) else round(cf15 + ca15, 3)

    return {
        "corners_for_l5":      cf5,
        "corners_for_l15":     cf15,
        "corners_for_delta":   _delta(cf5, cf15),
        "corners_against_l5":  ca5,
        "corners_against_l15": ca15,
        "corners_against_delta": _delta(ca5, ca15),
        "total_corners_l5":    total5,
        "total_corners_l15":   total15,
        "total_corners_delta": _delta(total5, total15),
    }


# ─────────────────────────────────────────────────────────────────────
# Profile selection
# ─────────────────────────────────────────────────────────────────────
def _classify_profile(home: dict, away: dict) -> dict:
    h_cf = home.get("corners_for_l5")
    a_cf = away.get("corners_for_l5")
    h_ca = home.get("corners_against_l5")
    a_ca = away.get("corners_against_l5")

    def _both_below(values: list[Optional[float]], lim: float) -> bool:
        return all(v is not None and v <= lim for v in values)

    def _both_above(values: list[Optional[float]], lim: float) -> bool:
        return all(v is not None and v >= lim for v in values)

    # 1. STRONG_CORNERS_UNDER_CROSS — ambos ≤ 4.0 (for) AND ambos ≤ 4.0 (against)
    if (_both_below([h_cf, a_cf], LOW_CORNER_FOR_L5)
            and _both_below([h_ca, a_ca], LOW_CORNER_AGAINST_L5)):
        return {
            "profile":  PROFILE_STRONG_UNDER,
            "supports": "CORNERS_UNDER",
            "recommended_market_family": "TOTAL_CORNERS_UNDER",
            "confidence_delta": 10,
            "fragility_delta": -6,
            "reason_codes": [RC_BOTH_LOW_FOR, RC_BOTH_LOW_AGAINST, RC_STRONG_CORNERS_UNDER],
            "narrative_es": (
                "Ambos equipos generan pocos tiros de esquina y también conceden pocos. "
                "El cruce apoya mercados Under de córners."
            ),
        }

    # 3. STRONG_CORNERS_OVER_CROSS — ambos ≥ 5.5 (for) AND ambos ≥ 5.0 (against)
    if (_both_above([h_cf, a_cf], HIGH_CORNER_FOR_L5)
            and _both_above([h_ca, a_ca], HIGH_CORNER_AGAINST_L5)):
        return {
            "profile":  PROFILE_STRONG_OVER,
            "supports": "CORNERS_OVER",
            "recommended_market_family": "TOTAL_CORNERS_OVER",
            "confidence_delta": 10,
            "fragility_delta": -5,
            "reason_codes": [RC_BOTH_HIGH_FOR, RC_BOTH_HIGH_AGAINST, RC_STRONG_CORNERS_OVER],
            "narrative_es": (
                "Ambos equipos generan muchos córners y también conceden muchos. "
                "El partido tiene perfil de Over de córners."
            ),
        }

    # 4. HIGH_CORNERS_CROSS — ambos ≥ 5.0 (for) OR ambos ≥ 5.0 (against)
    if (_both_above([h_cf, a_cf], HIGH_CORNER_FOR_L5_SOFT)
            or _both_above([h_ca, a_ca], HIGH_CORNER_AGAINST_L5)):
        rcs = []
        if _both_above([h_cf, a_cf], HIGH_CORNER_FOR_L5_SOFT):
            rcs.append(RC_BOTH_HIGH_FOR)
        if _both_above([h_ca, a_ca], HIGH_CORNER_AGAINST_L5):
            rcs.append(RC_BOTH_HIGH_AGAINST)
        rcs.append(RC_HIGH_CORNERS)
        return {
            "profile":  PROFILE_HIGH,
            "supports": "CORNERS_OVER",
            "recommended_market_family": "TOTAL_CORNERS_OVER",
            "confidence_delta": 6,
            "fragility_delta": -3,
            "reason_codes": rcs,
            "narrative_es": (
                "Ambos equipos suman volumen de córners alto en sus últimos 5 "
                "partidos. Cruce favorable a Over de córners."
            ),
        }

    # 5. ASYMMETRIC_CORNERS_PROFILE — uno genera mucho y el otro concede mucho
    asym_home = (h_cf is not None and h_cf >= HIGH_CORNER_FOR_L5_SOFT
                 and a_ca is not None and a_ca >= HIGH_CORNER_AGAINST_L5)
    asym_away = (a_cf is not None and a_cf >= HIGH_CORNER_FOR_L5_SOFT
                 and h_ca is not None and h_ca >= HIGH_CORNER_AGAINST_L5)
    if asym_home or asym_away:
        dom = "home" if asym_home else "away"
        return {
            "profile":  PROFILE_ASYMMETRIC,
            "supports": "TEAM_CORNERS_OVER",
            "recommended_market_family": "TEAM_CORNERS_OVER",
            "confidence_delta": 5,
            "fragility_delta": -2,
            "reason_codes": [RC_ASYMMETRIC_CORNERS],
            "dominant_side": dom,
            "narrative_es": (
                "Un equipo genera mucho volumen de córners y el rival concede muchos. "
                "Puede haber valor en córners de equipo, más que en total de córners."
            ),
        }

    # 2. LOW_CORNERS_CROSS — ambos ≤ 4.0 (for) (defensa neutral)
    if _both_below([h_cf, a_cf], LOW_CORNER_FOR_L5):
        return {
            "profile":  PROFILE_LOW,
            "supports": "CORNERS_UNDER",
            "recommended_market_family": "TOTAL_CORNERS_UNDER",
            "confidence_delta": 6,
            "fragility_delta": -4,
            "reason_codes": [RC_BOTH_LOW_FOR, RC_LOW_CORNERS],
            "narrative_es": (
                "Ambos equipos generan poco volumen de córners en sus últimos 5 partidos. "
                "El Under de córners gana soporte."
            ),
        }

    # 6. MIXED_CORNERS_PROFILE — fallback
    return {
        "profile":  PROFILE_MIXED,
        "supports": "NEUTRAL",
        "recommended_market_family": None,
        "confidence_delta": 0,
        "fragility_delta": 2,
        "reason_codes": [RC_MIXED_CORNERS],
        "narrative_es": (
            "El cruce de córners no entrega una señal limpia; los equipos muestran "
            "perfiles divergentes entre L5 y L15."
        ),
    }


# ─────────────────────────────────────────────────────────────────────
# External confirmation (Scores24)
# ─────────────────────────────────────────────────────────────────────
def _scores24_corner_consensus(s24_payload: dict | None) -> Optional[dict]:
    """Extract the corners-explicit market consensus from a Scores24
    payload. Returns ``None`` when no corners market is present.
    """
    if not isinstance(s24_payload, dict) or not s24_payload.get("available"):
        return None
    consensus = s24_payload.get("consensus") or {}
    if consensus.get("primary_market_type") == "corners_total":
        return {
            "section":     consensus.get("primary_section"),
            "side":        (consensus.get("primary_side") or "").upper(),
            "line":        consensus.get("primary_line"),
            "odds":        consensus.get("primary_odds"),
            "from_consensus": True,
        }
    # Otherwise look section-by-section.
    for s in (s24_payload.get("sections") or []):
        if s.get("market_type") == "corners_total":
            return {
                "section": s.get("section"),
                "side":    (s.get("side") or "").upper(),
                "line":    s.get("line"),
                "odds":    s.get("odds"),
                "from_consensus": False,
            }
    return None


def _compare_external_with_engine(engine_supports: str,
                                  external: Optional[dict]) -> dict:
    """Return ``{external_confirmation: bool, external_conflict: bool, reason_codes: [...]}``."""
    if not external or not external.get("side"):
        return {"external_confirmation": False, "external_conflict": False, "reason_codes": []}
    side = external["side"].upper()
    eng = (engine_supports or "").upper()
    if "UNDER" in side and eng == "CORNERS_UNDER":
        return {"external_confirmation": True, "external_conflict": False,
                "reason_codes": [RC_S24_CONFIRMS]}
    if "OVER" in side and eng in ("CORNERS_OVER", "TEAM_CORNERS_OVER"):
        return {"external_confirmation": True, "external_conflict": False,
                "reason_codes": [RC_S24_CONFIRMS]}
    if ("UNDER" in side and eng == "CORNERS_OVER") or ("OVER" in side and eng == "CORNERS_UNDER"):
        return {"external_confirmation": False, "external_conflict": True,
                "reason_codes": [RC_S24_CONFLICT]}
    # Neutral engine or asymmetric → no strong conflict.
    return {"external_confirmation": False, "external_conflict": False, "reason_codes": []}


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def compute_football_corner_profile_cross(
    *,
    home: dict,
    away: dict,
    scores24_payload: Optional[dict] = None,
) -> dict:
    """Compute the corners L5-vs-L15 cross profile.

    Parameters
    ----------
    home, away
        Team side dicts with ``recent_fixtures`` (dict-of-arrays or list).
        Alternatively can be a flat dict with ``corners_for_l5``, etc.
    scores24_payload
        Optional output of :func:`services.scores24_scraper.scrape_scores24_match`.
        When provided, the engine cross-checks against any explicit
        corners prediction and emits ``external_confirmation`` /
        ``external_conflict``.

    Returns
    -------
    dict (ALWAYS, fail-soft).
    """
    # Allow callers to pass pre-aggregated stats directly.
    def _resolve(side: Any) -> dict:
        if isinstance(side, dict) and any(k in side for k in (
                "corners_for_l5", "corners_for_l15",
                "corners_against_l5", "corners_against_l15")):
            cf5 = _safe(side.get("corners_for_l5"))
            cf15 = _safe(side.get("corners_for_l15"))
            ca5 = _safe(side.get("corners_against_l5"))
            ca15 = _safe(side.get("corners_against_l15"))
            total5 = None if (cf5 is None or ca5 is None) else round(cf5 + ca5, 3)
            total15 = None if (cf15 is None or ca15 is None) else round(cf15 + ca15, 3)
            return {
                "corners_for_l5":      _round(cf5),
                "corners_for_l15":     _round(cf15),
                "corners_for_delta":   _delta(cf5, cf15),
                "corners_against_l5":  _round(ca5),
                "corners_against_l15": _round(ca15),
                "corners_against_delta": _delta(ca5, ca15),
                "total_corners_l5":    total5,
                "total_corners_l15":   total15,
                "total_corners_delta": _delta(total5, total15),
            }
        return _derive_team_corner_block(side)

    home_blk = _resolve(home)
    away_blk = _resolve(away)

    # Fail-soft: need at least corners_for_l5 + corners_against_l5 from BOTH.
    if any(home_blk[k] is None for k in ("corners_for_l5", "corners_against_l5")) \
            or any(away_blk[k] is None for k in ("corners_for_l5", "corners_against_l5")):
        return {
            "available":      False,
            "engine_version": ENGINE_VERSION,
            "home":           home_blk,
            "away":           away_blk,
            "profile":        None,
            "supports":       "NEUTRAL",
            "recommended_market_family": None,
            "confidence_delta": 0,
            "fragility_delta": 0,
            "reason_codes":    [],
            "narrative_es":    None,
            "external_confirmation": False,
            "external_conflict":     False,
            "_skipped_reason":       "missing_l5_corners",
        }

    cls = _classify_profile(home_blk, away_blk)

    # External confirmation (Scores24)
    s24_market = _scores24_corner_consensus(scores24_payload)
    cmp_res = _compare_external_with_engine(cls.get("supports"), s24_market)
    combined_rcs = list(cls.get("reason_codes") or []) + list(cmp_res.get("reason_codes") or [])

    return {
        "available":      True,
        "engine_version": ENGINE_VERSION,
        "home":           home_blk,
        "away":           away_blk,
        "profile":        cls["profile"],
        "supports":       cls["supports"],
        "recommended_market_family": cls.get("recommended_market_family"),
        "confidence_delta": int(cls["confidence_delta"]),
        "fragility_delta":  int(cls["fragility_delta"]),
        "reason_codes":   combined_rcs,
        "narrative_es":   cls.get("narrative_es"),
        "dominant_side":  cls.get("dominant_side"),
        "external_market":       s24_market,
        "external_confirmation": bool(cmp_res.get("external_confirmation")),
        "external_conflict":     bool(cmp_res.get("external_conflict")),
    }


__all__ = [
    "ENGINE_VERSION",
    "PROFILE_STRONG_UNDER", "PROFILE_LOW", "PROFILE_STRONG_OVER",
    "PROFILE_HIGH", "PROFILE_ASYMMETRIC", "PROFILE_MIXED",
    "compute_football_corner_profile_cross",
    "extract_corner_side_from_match",
]
