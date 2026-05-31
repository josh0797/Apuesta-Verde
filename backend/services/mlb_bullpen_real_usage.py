"""
MLB Bullpen Real-Usage (Module #2)
==================================

Hoy `get_bullpen_recent_usage()` mide la fatiga del bullpen solo con
`games_played * 25 + extra_inning_games * 15`. Esa heurística ignora
LA INTENSIDAD real: cuántos pitches lanzó el bullpen, cuántas entradas
duró el abridor y cuánto trabajo se les transfirió a los relevistas.

Caso real Twins @ Pirates (sábado 30 mayo):
  - PIT bullpen: 166 pitches totales, ERA 9.00
  - MIN bullpen: 163 pitches totales, ERA 10.13
  - Mitch Keller (SP PIT): 4.0 IP, 77 pitches → bullpen expuesto 5 IP
  - Bailey Ober (SP MIN): 4.2 IP, 97 pitches → bullpen expuesto 4.1 IP

Con `games_played * 25` los dos bullpens daban `fatigue_score` bajo
(uno solo) cuando en realidad estaban quemados.

Este módulo expone funciones puras + un helper async que hidrata
desde la MLB Stats API el box-score real y devuelve:

    {
      bullpen_pitches_48h:    int,
      bullpen_innings_48h:    float,
      starter_lasted_innings: float,
      pitch_stress_index:     float,   # bullpen_pitches_48h / 45
      fatigue_score_0_100:    int,     # recálculo con pitch_stress
    }

Fail-soft: cualquier error → `None` para que el caller siga con la
heurística vieja.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# MLB Stats API (no auth required, JSON, very stable).
_BASE = "https://statsapi.mlb.com/api/v1"
_TIMEOUT = 10.0

# Fatigue thresholds — same buckets as get_bullpen_recent_usage to keep
# the labels consistent across the engine.
PITCH_STRESS_THRESHOLD_DAILY = 45  # pitches/day above this is fatigued


def derive_pitch_stress_index(bullpen_pitches_48h: int) -> float:
    """Pure: stress = bullpen_pitches_48h / 45 (one full day of work)."""
    try:
        n = max(0, int(bullpen_pitches_48h or 0))
    except (TypeError, ValueError):
        return 0.0
    return round(n / float(PITCH_STRESS_THRESHOLD_DAILY), 2)


def compute_fatigue_score(
    games_played: int,
    extra_inning_games: int,
    pitch_stress_index: float,
) -> int:
    """Combine the legacy heuristic with the new pitch_stress signal.

    Weights (chosen to keep backward-compat for fresh bullpens):
      - games_played × 20      (was 25 in the legacy heuristic)
      - extra_inning_games × 15
      - int(pitch_stress × 25) (NEW)

    With pitch_stress = 0 the score lands very close to the legacy
    value, so existing thresholds stay meaningful.
    """
    try:
        gp = max(0, int(games_played or 0))
        ei = max(0, int(extra_inning_games or 0))
        ps = max(0.0, float(pitch_stress_index or 0.0))
    except (TypeError, ValueError):
        return 0
    return int(min(100, gp * 20 + ei * 15 + int(ps * 25)))


def derive_fatigue_label(score: int) -> str:
    if score < 30:
        return "fresh"
    if score < 60:
        return "moderate"
    if score < 85:
        return "high"
    return "extreme"


async def fetch_recent_bullpen_workload(
    team_id: int,
    days: int = 2,
    *,
    timeout: float = _TIMEOUT,
) -> Optional[dict]:
    """Hit `/schedule?...&hydrate=team,linescore,boxscore` and aggregate
    bullpen pitches across the last `days` finished games.

    Returns ``None`` when the MLB Stats API fails or the team has no
    finished games in the window. We intentionally keep `days=2` for the
    pitch-stress measurement (the 48h rule from the spec).
    """
    if not team_id:
        return None
    end_d = datetime.utcnow().date()
    start_d = end_d - timedelta(days=days)
    url = f"{_BASE}/schedule"
    params = {
        "teamId":   int(team_id),
        "startDate": start_d.isoformat(),
        "endDate":   end_d.isoformat(),
        "sportId":   1,
        "hydrate":   "linescore,team",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        log.debug("recent_bullpen_workload schedule fetch failed: %s", exc)
        return None

    bullpen_pitches_total = 0
    bullpen_innings_total = 0.0
    starter_innings_today: Optional[float] = None
    games_counted = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        for d in data.get("dates", []) or []:
            for g in d.get("games", []) or []:
                state = (g.get("status") or {}).get("abstractGameState")
                if state != "Final":
                    continue
                gid = g.get("gamePk")
                if not gid:
                    continue
                # Fetch box-score for pitching stats.
                try:
                    br = await client.get(f"{_BASE}/game/{gid}/boxscore")
                    br.raise_for_status()
                    box = br.json()
                except Exception as exc:
                    log.debug("boxscore %s failed: %s", gid, exc)
                    continue
                # Determine if our team is home or away in this game.
                teams = box.get("teams") or {}
                home_id = ((teams.get("home") or {}).get("team") or {}).get("id")
                side = "home" if home_id == int(team_id) else "away"
                side_block = teams.get(side) or {}
                pitchers_ids = side_block.get("pitchers") or []
                players = side_block.get("players") or {}
                # First pitcher in the order is the starter; the rest is
                # the bullpen. We sum `pitchesThrown` and `inningsPitched`.
                game_bullpen_pitches = 0
                game_bullpen_innings = 0.0
                game_starter_innings = 0.0
                for idx, pid in enumerate(pitchers_ids):
                    p = players.get(f"ID{pid}") or {}
                    pstats = ((p.get("stats") or {}).get("pitching") or {})
                    pitches = pstats.get("pitchesThrown")
                    ip = pstats.get("inningsPitched")
                    try:
                        pitches_i = int(pitches or 0)
                    except (TypeError, ValueError):
                        pitches_i = 0
                    try:
                        ip_f = _ip_to_float(ip)
                    except Exception:
                        ip_f = 0.0
                    if idx == 0:
                        game_starter_innings = ip_f
                    else:
                        game_bullpen_pitches += pitches_i
                        game_bullpen_innings += ip_f
                bullpen_pitches_total += game_bullpen_pitches
                bullpen_innings_total += game_bullpen_innings
                if starter_innings_today is None:
                    starter_innings_today = game_starter_innings
                games_counted += 1

    if games_counted == 0:
        return None

    pitch_stress = derive_pitch_stress_index(bullpen_pitches_total)
    return {
        "team_id":                int(team_id),
        "days":                   days,
        "games_counted":          games_counted,
        "bullpen_pitches_48h":    bullpen_pitches_total,
        "bullpen_innings_48h":    round(bullpen_innings_total, 1),
        "starter_lasted_innings": (
            round(starter_innings_today, 1) if starter_innings_today is not None else None
        ),
        "pitch_stress_index":     pitch_stress,
    }


def _ip_to_float(ip) -> float:
    """MLB Stats encodes innings as '6.1' = 6⅓, '5.2' = 5⅔. Convert
    to a decimal we can sum."""
    if ip is None:
        return 0.0
    try:
        s = str(ip).strip()
        if "." not in s:
            return float(int(s))
        whole, frac = s.split(".", 1)
        whole_i = int(whole)
        frac_i = int(frac[:1])  # 0,1,2
        return whole_i + (frac_i / 3.0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "derive_pitch_stress_index",
    "compute_fatigue_score",
    "derive_fatigue_label",
    "fetch_recent_bullpen_workload",
    "PITCH_STRESS_THRESHOLD_DAILY",
]
