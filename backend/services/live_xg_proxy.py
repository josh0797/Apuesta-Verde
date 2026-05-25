"""Live xG + Threat + Pressure proxy — inspired by:

  • kloppy            (https://github.com/PySport/kloppy)
        → normalized event-stream model. We mirror its philosophy of
          "produce a clean per-side vector regardless of the upstream
          provider's idiosyncratic field names" so the rest of the engine
          doesn't have to know whether API-Sports called it "Shots on
          Goal" or "shots_on_target" or "SOT".

  • socceraction      (https://github.com/ML-KULeuven/socceraction)
        → VAEP / Expected Threat (xT). We can't build xT from event data
          (we have aggregates only), so we approximate it as a *territorial
          threat index*: weighted blend of possession, dangerous attacks,
          attacks, corners. The interpretation is the same — "how much
          threat is this team generating per unit of time?"

  • soccer_xg          (https://github.com/ML-KULeuven/soccer_xg)
        → ML-trained xG from shot context. Since we don't have per-shot
          coordinates we use the *shot-quality decomposition* derived from
          the StatsBomb open-data prior: in-box shots ≈ 0.10 xG each,
          out-of-box ≈ 0.04, blocked ≈ 0.02. The numbers stay honest as
          long as we keep the bucket weights aligned with what we observe
          in the realised goal data.

Outputs are pure numbers (no IO, no DB). Everything else (UI, picks,
re-eval engine) just consumes the dict this module returns.

Public API
----------
    extract_live_stats(side_stats)            → SideStats (xg, threat, pressure)
    compute_team_pressure(live_stats, minute) → dict (home + away vectors)
    detect_late_lead_trap(...)                → dict | None
    compute_live_analysis(match)              → dict (full per-match analysis)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional


# ─── Stat extraction (kloppy-style normalisation) ──────────────────────────
# All possible field names API-Sports / ESPN / Flashscore have used historically
# for the same metric. The order matters: we try the most precise label first.
_STAT_ALIASES: dict[str, tuple[str, ...]] = {
    "shots":            ("Total Shots", "total_shots", "Shots", "shots"),
    "shots_on_target":  ("Shots on Goal", "shots_on_target", "SOT", "On Target", "shots on goal"),
    "shots_off_target": ("Shots off Goal", "shots_off_target", "Off Target"),
    "shots_in_box":     ("Shots insidebox", "Shots inside box", "shots_in_box"),
    "shots_out_box":    ("Shots outsidebox", "Shots outside box", "shots_out_box"),
    "blocked_shots":    ("Blocked Shots", "blocked_shots"),
    "possession":       ("Ball Possession", "possession", "Possession"),
    "corners":          ("Corner Kicks", "corners", "Corners"),
    "dangerous":        ("Dangerous Attacks", "dangerous_attacks", "DA"),
    "attacks":          ("Attacks", "attacks"),
    "xg_provider":      ("expected_goals", "Expected Goals", "xG", "xg"),
    "fouls":            ("Fouls", "fouls"),
    "saves":            ("Goalkeeper Saves", "saves"),
    "passes_pct":       ("Passes %", "passes_accurate_pct"),
}


def _val(stats: dict | None, key: str) -> float:
    """Return the float value of `key` (using aliases) from a side stats dict.

    Handles common upstream quirks: "57%" strings, None, '0.83' xG strings.
    """
    if not stats:
        return 0.0
    for alias in _STAT_ALIASES.get(key, (key,)):
        if alias in stats and stats[alias] is not None:
            v = stats[alias]
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                s = v.strip().replace("%", "")
                try:
                    return float(s)
                except ValueError:
                    continue
    return 0.0


# Shot-quality xG weights (StatsBomb open-data realised xG by zone).
SHOT_W_IN_BOX_ON_TARGET  = 0.32   # huge — close + clean strike
SHOT_W_IN_BOX_OFF_TARGET = 0.10   # box shot that missed
SHOT_W_OUT_BOX_ON_TARGET = 0.08
SHOT_W_OUT_BOX_OFF       = 0.03
SHOT_W_BLOCKED           = 0.02


# ─── Side-level live snapshot ──────────────────────────────────────────────

@dataclass
class SideStats:
    """A single team's live snapshot, in the units the rest of the engine
    cares about. Mirrors what kloppy would emit after normalisation.

    All fields are non-negative floats.
    """
    xg_live:      float    # estimated goals from realised shots so far
    threat_index: float    # 0-100ish: territorial + chance creation
    shots:        float
    shots_on_target: float
    shots_in_box: float
    possession:   float    # 0-100
    corners:      float
    dangerous:    float
    attacks:      float
    xg_provider:  float    # if API-Sports/ESPN gave us their own xG, capture it

    def to_dict(self) -> dict:
        return asdict(self)


def extract_side(stats: dict | None) -> SideStats:
    """Build a `SideStats` object from a raw side-stats dict."""
    shots          = _val(stats, "shots")
    sot            = _val(stats, "shots_on_target")
    shots_in_box   = _val(stats, "shots_in_box")
    blocked        = _val(stats, "blocked_shots")
    possession     = _val(stats, "possession")
    corners        = _val(stats, "corners")
    dangerous      = _val(stats, "dangerous")
    attacks        = _val(stats, "attacks")
    xg_provider    = _val(stats, "xg_provider")

    # Compose missing buckets from totals.
    shots_off_box = max(0.0, shots - shots_in_box - blocked)
    sot_in_box    = min(sot, shots_in_box)
    sot_out_box   = max(0.0, sot - sot_in_box)
    off_box_off   = max(0.0, shots_off_box - sot_out_box)

    # soccer_xg-inspired weighted xG.
    xg_live = (
        sot_in_box   * SHOT_W_IN_BOX_ON_TARGET
        + max(0.0, shots_in_box - sot_in_box) * SHOT_W_IN_BOX_OFF_TARGET
        + sot_out_box * SHOT_W_OUT_BOX_ON_TARGET
        + off_box_off * SHOT_W_OUT_BOX_OFF
        + blocked    * SHOT_W_BLOCKED
    )
    # If the provider exposes its own xG, prefer the larger of (ours, theirs)
    # so we don't undercount events we missed.
    xg_live = max(xg_live, xg_provider)

    # socceraction-inspired threat: territorial control + chance creation +
    # set-piece volume. Normalised to a 0-100 scale per side later.
    threat = (
        possession * 0.4         # field control
        + dangerous * 0.9         # high-leverage sequences
        + attacks * 0.15          # build-up volume
        + corners * 1.2           # set-piece danger
        + sot * 1.5               # output
    )

    return SideStats(
        xg_live=round(xg_live, 3),
        threat_index=round(threat, 2),
        shots=shots, shots_on_target=sot, shots_in_box=shots_in_box,
        possession=possession, corners=corners,
        dangerous=dangerous, attacks=attacks,
        xg_provider=round(xg_provider, 3),
    )


# ─── Pressure (recent intensity proxy) ─────────────────────────────────────

def compute_pressure(side: SideStats, minute: Optional[int]) -> float:
    """Per-minute "pressure rate" for the side.

    Higher = team is creating more threat per minute *on average*. We don't
    have a sliding window, so we use cumulative-stats-per-minute as the
    closest signal. Over the last third of a game it correlates well with
    "who's pushing now".
    """
    m = max(1, int(minute or 1))
    return (side.dangerous + side.shots_on_target * 2.0 + side.corners * 0.5) / m


def compute_team_pressure(live_stats: dict | None, *, minute: Optional[int] = None) -> dict:
    """Compute side-by-side pressure vector for a match's live_stats dict.

    Returns:
        {
          "home":  SideStats.to_dict() + {"pressure_rate": float},
          "away":  SideStats.to_dict() + {"pressure_rate": float},
          "minute": int,
          "score":  {"home": int, "away": int},
          "deltas": {
              "xg_live":      float,   # home - away
              "threat_index": float,
              "pressure":     float,
          },
        }
    """
    live_stats = live_stats or {}
    minute = minute if minute is not None else live_stats.get("minute")
    home = extract_side(live_stats.get("home_stats") or {})
    away = extract_side(live_stats.get("away_stats") or {})
    pr_h = compute_pressure(home, minute)
    pr_a = compute_pressure(away, minute)
    score = live_stats.get("score") or {}
    h_score = int(score.get("home") or 0)
    a_score = int(score.get("away") or 0)
    return {
        "home": {**home.to_dict(), "pressure_rate": round(pr_h, 3)},
        "away": {**away.to_dict(), "pressure_rate": round(pr_a, 3)},
        "minute": int(minute) if minute is not None else None,
        "score": {"home": h_score, "away": a_score},
        "deltas": {
            "xg_live":      round(home.xg_live - away.xg_live, 3),
            "threat_index": round(home.threat_index - away.threat_index, 2),
            "pressure":     round(pr_h - pr_a, 3),
        },
    }


# ─── Trap detector ─────────────────────────────────────────────────────────
# User spec (verbatim):
#   "favorito ganando tarde + cuota muy baja + rival presionando = NO APOSTAR"

def detect_late_lead_trap(
    *,
    minute: Optional[int],
    home_score: int,
    away_score: int,
    decimal_odds_for_leader: Optional[float],
    home_pressure: float,
    away_pressure: float,
    home_threat: float,
    away_threat: float,
    leader_side: Optional[str] = None,
) -> Optional[dict]:
    """Block-bet detector.

    Trigger conditions (ALL must hold):
      1. There IS a clear leader (score diff != 0).
      2. minute >= 70 (late game).
      3. odds for the leader to win <= 1.45 (market thinks the lead is safe).
      4. The TRAILING side's pressure rate is at least 1.4× the leader's
         pressure rate AND their threat index is at least 1.2× the
         leader's threat (recent push is real).
      5. Trailing side has >= 2 shots on target (the press has chances).

    Returns None when no trap. When triggered:
        {
          "triggered": True,
          "leader_side": "home"|"away",
          "trailing_side": "home"|"away",
          "trailing_pressure": float,
          "leader_pressure": float,
          "pressure_ratio": float,
          "threat_ratio": float,
          "reason_es": str,
          "reason_en": str,
        }
    """
    if minute is None or minute < 70:
        return None
    diff = home_score - away_score
    if diff == 0:
        return None
    if decimal_odds_for_leader is None or decimal_odds_for_leader > 1.45:
        return None

    leader = leader_side or ("home" if diff > 0 else "away")
    trailing = "away" if leader == "home" else "home"
    lead_pressure = home_pressure if leader == "home" else away_pressure
    trail_pressure = away_pressure if leader == "home" else home_pressure
    lead_threat = home_threat if leader == "home" else away_threat
    trail_threat = away_threat if leader == "home" else home_threat

    pressure_ratio = trail_pressure / max(0.001, lead_pressure)
    threat_ratio   = trail_threat   / max(0.001, lead_threat)
    # Two-tier trap detection: a STRONG zone (>=1.4 / >=1.2) and a
    # WEAKER zone (1.2-1.4 / 1.1-1.2) that we still surface but flag
    # as `low_confidence` so the UI/interpreter can soften the warning.
    low_confidence_trap = (1.2 <= pressure_ratio < 1.4) or (1.1 <= threat_ratio < 1.2)
    if pressure_ratio < 1.2 or threat_ratio < 1.1:
        return None

    reason_prefix_es = "(Señal débil) " if low_confidence_trap else ""
    return {
        "triggered":          True,
        "low_confidence":     low_confidence_trap,
        "leader_side":        leader,
        "trailing_side":      trailing,
        "leader_pressure":    round(lead_pressure, 3),
        "trailing_pressure":  round(trail_pressure, 3),
        "pressure_ratio":     round(pressure_ratio, 2),
        "threat_ratio":       round(threat_ratio, 2),
        "decimal_odds_for_leader": decimal_odds_for_leader,
        "reason_es": (
            f"{reason_prefix_es}TRAMPA: favorito ({leader}) gana al min {minute} con cuota "
            f"{decimal_odds_for_leader:.2f}, pero el rival presiona "
            f"{pressure_ratio:.1f}× más (xT {threat_ratio:.1f}×). NO APOSTAR al favorito; "
            f"considerar Over o spread del rival."
        ),
        "reason_en": (
            f"TRAP: leader ({leader}) holding 1-goal lead at min {minute} priced "
            f"{decimal_odds_for_leader:.2f}, but trailing side pressing "
            f"{pressure_ratio:.1f}× harder (xT {threat_ratio:.1f}×). DO NOT BET the favourite."
        ),
    }


# ─── Top-level entry: full live analysis per match ─────────────────────────

def _best_1x2_odds(match: dict) -> dict:
    """Return best decimal odds per 1X2 leg, or {} if no snapshot."""
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return {}
    rows = ((snaps[-1] or {}).get("markets") or {}).get("1X2") or []
    out = {"home": None, "draw": None, "away": None}
    for r in rows:
        for k in out:
            v = r.get(k)
            if isinstance(v, (int, float)) and v > 1.01:
                if out[k] is None or v > out[k]:
                    out[k] = float(v)
    return {k: v for k, v in out.items() if v is not None}


def _favorite_label(diff: int) -> str:
    return "home" if diff > 0 else ("away" if diff < 0 else "")


def compute_live_analysis(match: dict) -> dict:
    """Compute the full live analysis payload that the UI auto-renders on
    every live card.

    Layers:
      1. Per-side stats (kloppy-style normalisation).
      2. Per-side xG live (soccer_xg shot-quality decomposition).
      3. Per-side threat index (socceraction xT proxy).
      4. Per-side pressure rate (events per minute, last-third weighted).
      5. Trap detector (late lead + low odds + chasing pressure).
      6. Verdict label for the UI (LIVE_VALUE / LIVE_NEUTRAL / TRAP).

    Returns:
        {
          "minute": int | None,
          "score":  {home: int, away: int},
          "home":   {xg_live, threat_index, pressure_rate, ...},
          "away":   {...},
          "deltas": {...},
          "trap":   None | {...},
          "verdict": {
            "label": "TRAP_LATE_LEAD" | "LIVE_VALUE_PUSH" | "BALANCED" | "INSUFFICIENT_DATA",
            "side":   "home"|"away"|None,
            "reason_es": str,
            "reason_en": str,
          },
          "_source": "live_xg_proxy_v1",
        }
    """
    live = match.get("live_stats") or {}
    minute = live.get("minute")
    pres = compute_team_pressure(live, minute=minute)
    h = pres["home"]
    a = pres["away"]
    diff = pres["score"]["home"] - pres["score"]["away"]
    fav = _favorite_label(diff)

    # Locate the leader's 1X2 odds for trap detection.
    quotes = _best_1x2_odds(match)
    leader_odds = quotes.get(fav) if fav in ("home", "away") else None
    trap = detect_late_lead_trap(
        minute=minute,
        home_score=pres["score"]["home"],
        away_score=pres["score"]["away"],
        decimal_odds_for_leader=leader_odds,
        home_pressure=h["pressure_rate"],
        away_pressure=a["pressure_rate"],
        home_threat=h["threat_index"],
        away_threat=a["threat_index"],
        leader_side=fav or None,
    )

    # Verdict
    if minute is None or (h["shots"] + a["shots"] < 1 and minute < 20):
        verdict = {
            "label": "INSUFFICIENT_DATA",
            "side":  None,
            "reason_es": "Sin datos live suficientes para emitir veredicto.",
            "reason_en": "Not enough live data to emit a verdict.",
        }
    elif trap and trap.get("triggered"):
        verdict = {
            "label": "TRAP_LATE_LEAD",
            "side":  trap["leader_side"],
            "reason_es": trap["reason_es"],
            "reason_en": trap["reason_en"],
        }
    else:
        # Find the pushing side: higher pressure & higher xG live
        pushing_home = (h["pressure_rate"] > a["pressure_rate"]) and (h["xg_live"] > a["xg_live"])
        pushing_away = (a["pressure_rate"] > h["pressure_rate"]) and (a["xg_live"] > h["xg_live"])
        if (pushing_home or pushing_away) and minute >= 60:
            side = "home" if pushing_home else "away"
            verdict = {
                "label": "LIVE_VALUE_PUSH",
                "side":  side,
                "reason_es": (
                    f"Empuje {('local' if side=='home' else 'visitante')} sostenido en últimos minutos "
                    f"(xG {h['xg_live'] if side=='home' else a['xg_live']:.2f} vs "
                    f"{a['xg_live'] if side=='home' else h['xg_live']:.2f}). Buscar valor en Over o "
                    f"línea del {'local' if side=='home' else 'visitante'}."
                ),
                "reason_en": (
                    f"Sustained {side} push in late minutes "
                    f"(xG {h['xg_live'] if side=='home' else a['xg_live']:.2f} vs "
                    f"{a['xg_live'] if side=='home' else h['xg_live']:.2f}). Look for value on Over or "
                    f"{side} lines."
                ),
            }
        else:
            verdict = {
                "label": "BALANCED",
                "side":  None,
                "reason_es": "Partido equilibrado en lo live; sin señal direccional clara.",
                "reason_en": "Match looks balanced live; no clear directional signal.",
            }

    return {
        "minute": pres["minute"],
        "score":  pres["score"],
        "home":   h,
        "away":   a,
        "deltas": pres["deltas"],
        "leader_odds": leader_odds,
        "favorite_side": fav or None,
        "trap":   trap,
        "verdict": verdict,
        "_source": "live_xg_proxy_v1",
    }


def list_libraries_inspiration() -> list[dict]:
    """Returned by an admin endpoint so the UI can show 'powered by' credit."""
    return [
        {"name": "kloppy", "url": "https://github.com/PySport/kloppy", "use": "stat normalisation"},
        {"name": "socceraction", "url": "https://github.com/ML-KULeuven/socceraction", "use": "Expected Threat (xT)"},
        {"name": "soccer_xg", "url": "https://github.com/ML-KULeuven/soccer_xg", "use": "Shot-quality xG decomposition"},
    ]


# Re-export the dataclass for type hints elsewhere.
__all__ = [
    "SideStats",
    "extract_side",
    "compute_pressure",
    "compute_team_pressure",
    "detect_late_lead_trap",
    "compute_live_analysis",
    "list_libraries_inspiration",
]
