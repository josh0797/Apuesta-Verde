#!/usr/bin/env python3
"""Bullpen-Under Hypothesis Backtest — MLB Stats API edition.

Phase 43 / Backtest v2 — observe-only.

Question being asked:
  ¿Un bullpen vulnerable solo (alto ERA 7d) empeora los Under, o solo
  empeora cuando se combina con tráfico ofensivo en el line-up rival?

Differences vs v1:
  * v1 leía snapshots persistidos en la BD interna. En preview/dev no
    hay datos suficientes y el output era vacío.
  * v2 obtiene TODOS los datos directamente de la **MLB Stats API**
    pública (``https://statsapi.mlb.com``). No requiere API key y no
    toca la BD interna del proyecto.

Methodology (sin data-leakage):
  * Para cada juego del rango ``--start .. --end``:
      1. Pulls schedule + final box-score (sólo se usa el total final
         del juego como settlement; jamás como input al modelo).
      2. Para cada equipo del juego, calcula su ``bullpen_era_7d`` y
         ``bullpen_whip_7d`` usando ESTRICTAMENTE las apariciones de
         relievers en los 7 días previos a la fecha del juego.
      3. Calcula el ``traffic_score`` 7d = (hits + walks) por juego de
         ambas ofensivas en los 7 días previos.
      4. Asigna cohorte A/B/A1/A2 según los umbrales configurables.
      5. Liquida Under contra una línea base ``--line`` y contra el
         set de líneas proxy (8.5, 9.0, 9.5, 10.0, 10.5) asumiendo
         odds planas a -110.

CLI:
    python scripts/backtest_bullpen_under_hypothesis.py \\
        --start 2024-05-01 --end 2024-06-01 \\
        --line 9.5

    # fallback automático cuando no se pasan fechas
    python scripts/backtest_bullpen_under_hypothesis.py --days 45

Output:
    /app/backend/scripts/out/bullpen_under_backtest_<UTC>.json

Estrictamente observe-only. No modifica picks, no escribe a BD de
producción, no inserta telemetría.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "scripts" / "out"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("bullpen_under_backtest")

# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────
MLB_API_BASE = os.environ.get("MLB_API_BASE", "https://statsapi.mlb.com/api/v1")
HTTP_TIMEOUT = float(os.environ.get("MLB_API_TIMEOUT", "20"))
HTTP_CONCURRENCY = int(os.environ.get("MLB_API_CONCURRENCY", "6"))

DEFAULT_PROXY_LINES = [8.5, 9.0, 9.5, 10.0, 10.5]

# Cohort thresholds (configurable via CLI)
DEFAULT_THRESHOLDS = {
    "bullpen_era_high":      5.50,   # Cohort A
    "bullpen_era_normal_max": 4.50,  # Cohort B
    "traffic_high":          12.0,   # (H + BB) / juego promedio combinado
    "min_bullpen_innings_7d": 3.0,   # mínimo IP en 7d para considerar dato fiable
    "min_offense_games_7d":  2,      # mínimo de juegos previos por equipo
}

# Pitcher fatigue / role detection: el starter es el primer pitcher
# listado en el ``pitchers`` array del box-score y/o el que aparece en
# ``gameStatus.isPitching`` like-flag. Como heurística simple y robusta,
# clasificamos como **relievers** a todos los pitchers cuyo ``IP`` < 5.0
# Y que no aparezcan como el 1er pitcher del side. Esto reproduce la
# distinción starter/relief sin depender de fields propietarios.


# ─────────────────────────────────────────────────────────────────────
# HTTP helpers (cached, async, concurrency-bounded)
# ─────────────────────────────────────────────────────────────────────
class MLBClient:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(HTTP_CONCURRENCY)
        self._schedule_cache: dict[str, list[dict]] = {}
        self._boxscore_cache: dict[int, dict] = {}
        self.requests = 0
        self.errors  = 0

    async def __aenter__(self) -> "MLBClient":
        self._client = httpx.AsyncClient(
            base_url=MLB_API_BASE,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "valuebet-backtest/1.0"},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        assert self._client is not None
        async with self._sem:
            self.requests += 1
            try:
                r = await self._client.get(path, params=params or {})
                if r.status_code != 200:
                    self.errors += 1
                    log.debug("MLB API %s status=%s", path, r.status_code)
                    return None
                return r.json()
            except Exception as exc:
                self.errors += 1
                log.debug("MLB API %s failed: %s", path, exc)
                return None

    async def schedule(self, start: str, end: str, team_id: Optional[int] = None) -> list[dict]:
        key = f"{start}:{end}:{team_id or '*'}"
        if key in self._schedule_cache:
            return self._schedule_cache[key]
        params = {
            "sportId":    1,
            "startDate":  start,
            "endDate":    end,
            "gameType":   "R",            # regular season only
            "hydrate":    "linescore",
        }
        if team_id is not None:
            params["teamId"] = team_id
        data = await self._get("/schedule", params)
        games: list[dict] = []
        if data and isinstance(data.get("dates"), list):
            for d in data["dates"]:
                for g in d.get("games") or []:
                    games.append(g)
        self._schedule_cache[key] = games
        return games

    async def boxscore(self, game_pk: int) -> Optional[dict]:
        if game_pk in self._boxscore_cache:
            return self._boxscore_cache[game_pk]
        data = await self._get(f"/game/{game_pk}/boxscore")
        if data:
            self._boxscore_cache[game_pk] = data
        return data


# ─────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────
def _parse_ip(ip_raw: Any) -> float:
    """MLB exposes IP como string "5.2" donde el decimal es OUTS/3.

    "5.2" → 5 + 2/3 → 5.6667 IP reales.
    """
    if ip_raw in (None, ""):
        return 0.0
    try:
        s = str(ip_raw)
        if "." in s:
            whole, frac = s.split(".")
            outs = int(frac)
            return float(whole) + (outs / 3.0)
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def extract_pitching(boxscore: dict, side: str) -> dict:
    """Returns aggregated pitching stats for the team's *bullpen*.

    side: 'home' or 'away'.
    """
    team = (boxscore.get("teams") or {}).get(side) or {}
    players = team.get("players") or {}
    pitcher_ids = team.get("pitchers") or []
    if not pitcher_ids:
        return {"ip": 0.0, "er": 0, "h": 0, "bb": 0, "hr": 0, "n_relievers": 0}
    # Starter = the first pitcher in the order.
    starter_id = pitcher_ids[0]

    ip_sum = 0.0
    er = 0
    h = 0
    bb = 0
    hr = 0
    n = 0
    for pid in pitcher_ids:
        if pid == starter_id:
            continue
        key = f"ID{pid}"
        pdata = players.get(key) or {}
        pstats = ((pdata.get("stats") or {}).get("pitching") or {})
        ip = _parse_ip(pstats.get("inningsPitched"))
        if ip <= 0:
            continue
        ip_sum += ip
        er += _safe_int(pstats.get("earnedRuns"))
        h  += _safe_int(pstats.get("hits"))
        bb += _safe_int(pstats.get("baseOnBalls"))
        hr += _safe_int(pstats.get("homeRuns"))
        n += 1
    return {"ip": ip_sum, "er": er, "h": h, "bb": bb, "hr": hr, "n_relievers": n}


def extract_offense(boxscore: dict, side: str) -> dict:
    """Aggregated offense stats for the team (full extraction for OPS / OBP / SLG)."""
    team = (boxscore.get("teams") or {}).get(side) or {}
    bat = (team.get("teamStats") or {}).get("batting") or {}
    return {
        "h":       _safe_int(bat.get("hits")),
        "doubles": _safe_int(bat.get("doubles")),
        "triples": _safe_int(bat.get("triples")),
        "hr":      _safe_int(bat.get("homeRuns")),
        "bb":      _safe_int(bat.get("baseOnBalls")),
        "hbp":     _safe_int(bat.get("hitByPitch")),
        "sf":      _safe_int(bat.get("sacFlies")),
        "sh":      _safe_int(bat.get("sacBunts")),
        "ab":      _safe_int(bat.get("atBats")),
        "k":       _safe_int(bat.get("strikeOuts")),
        "r":       _safe_int(bat.get("runs")),
    }


def extract_final_total(boxscore: dict) -> Optional[int]:
    teams = boxscore.get("teams") or {}
    try:
        h = ((teams.get("home") or {}).get("teamStats") or {}).get("batting") or {}
        a = ((teams.get("away") or {}).get("teamStats") or {}).get("batting") or {}
        if "runs" in h and "runs" in a:
            return int(h["runs"]) + int(a["runs"])
    except Exception:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────
# Per-team prior-7d aggregator
# ─────────────────────────────────────────────────────────────────────
async def compute_team_prior_window(
    client: MLBClient,
    team_id: int,
    game_date: date,
    window_days: int = 7,
) -> dict:
    """Bullpen ERA/WHIP + traffic-score components over the last ``window_days``.

    Strictly uses games with ``officialDate`` < game_date. Returns enough
    raw stats so ``services.traffic_score`` can build a composite score
    without needing to re-fetch anything.
    """
    end_d = game_date - timedelta(days=1)
    start_d = end_d - timedelta(days=window_days - 1)
    if start_d > end_d:
        return {}
    sched = await client.schedule(start_d.isoformat(), end_d.isoformat(), team_id=team_id)
    bullpen_ip = 0.0
    bullpen_er = bullpen_h = bullpen_bb = bullpen_hr = 0
    off = {"h": 0, "doubles": 0, "triples": 0, "hr": 0, "bb": 0,
           "hbp": 0, "sf": 0, "sh": 0, "ab": 0, "k": 0, "r": 0}
    games_dates_runs: list[tuple[date, int]] = []
    n_games = 0

    for g in sched:
        official = g.get("officialDate") or g.get("gameDate", "")[:10]
        try:
            d = datetime.strptime(official, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if d >= game_date:
            continue  # post-match data leakage guard
        status = ((g.get("status") or {}).get("abstractGameState") or "").lower()
        if status != "final":
            continue
        gpk = g.get("gamePk")
        if not gpk:
            continue
        box = await client.boxscore(gpk)
        if not box:
            continue
        teams = box.get("teams") or {}
        if (teams.get("home") or {}).get("team", {}).get("id") == team_id:
            side = "home"
        elif (teams.get("away") or {}).get("team", {}).get("id") == team_id:
            side = "away"
        else:
            continue
        bp = extract_pitching(box, side)
        bullpen_ip += bp["ip"]
        bullpen_er += bp["er"]
        bullpen_h  += bp["h"]
        bullpen_bb += bp["bb"]
        bullpen_hr += bp["hr"]
        ob = extract_offense(box, side)
        for k in off:
            off[k] += ob.get(k, 0)
        games_dates_runs.append((d, ob.get("r", 0)))
        n_games += 1

    out: dict[str, Any] = {
        "n_games":          n_games,
        "bullpen_ip_7d":    round(bullpen_ip, 2),
        "bullpen_er_7d":    bullpen_er,
        "bullpen_h_7d":     bullpen_h,
        "bullpen_bb_7d":    bullpen_bb,
        "bullpen_hr_7d":    bullpen_hr,
        "off_h_7d":         off["h"],
        "off_bb_7d":        off["bb"],
        "off_hr_7d":        off["hr"],
        "off_r_7d":         off["r"],
        "off_doubles_7d":   off["doubles"],
        "off_triples_7d":   off["triples"],
        "off_ab_7d":        off["ab"],
        "off_hbp_7d":       off["hbp"],
        "off_sf_7d":        off["sf"],
        "off_sh_7d":        off["sh"],
        "off_k_7d":         off["k"],
    }
    if bullpen_ip >= 0.1:
        out["bullpen_era_7d"]  = round((bullpen_er * 9.0) / bullpen_ip, 3)
        out["bullpen_whip_7d"] = round((bullpen_h + bullpen_bb) / bullpen_ip, 3)
    if n_games > 0:
        out["offense_traffic_legacy_7d"] = round((off["h"] + off["bb"]) / n_games, 2)
        out["offense_hr_per_game_7d"]    = round(off["hr"] / n_games, 3)
        out["offense_runs_per_game_7d"]  = round(off["r"]  / n_games, 3)
        # Last 5g runs/game (recent_form). Sort by date and take tail-5.
        last5 = sorted(games_dates_runs)[-5:]
        if last5:
            out["recent_form_rpg_5d"] = round(sum(r for _, r in last5) / len(last5), 3)
    # Hand to the traffic_score module to build the composite 0-100.
    from services.traffic_score import (
        compute_offense_window_metrics,
        compute_traffic_score,
    )
    metrics = compute_offense_window_metrics({
        **off,
        "n_games": n_games,
    })
    out["offense_metrics_7d"] = metrics
    ts = compute_traffic_score(
        metrics=metrics,
        recent_form_rpg=out.get("recent_form_rpg_5d"),
        # No historical book lines available — leave implied_team_total None
        # so the score falls back to runs/game as proxy.
        implied_team_total=None,
        # No Statcast in the public MLB API box — let it fall back to SLG.
        hard_contact_proxy=None,
    )
    out["traffic_score_obj"] = ts
    return out


# ─────────────────────────────────────────────────────────────────────
# Cohort assignment + settlement
# ─────────────────────────────────────────────────────────────────────
def assign_cohort(
    home: dict,
    away: dict,
    thresholds: dict,
) -> tuple[Optional[str], Optional[str], dict]:
    """Returns (primary_cohort, sub_cohort, signal_dict).

    primary_cohort: 'A' (bullpen vulnerable), 'B' (normal), None
    sub_cohort:     'A1' (A + high traffic), 'A2' (A + low traffic), None
    """
    bp_h = home.get("bullpen_era_7d")
    bp_a = away.get("bullpen_era_7d")
    if bp_h is None or bp_a is None:
        return (None, None, {"reason": "missing_bullpen_era"})
    if (home.get("bullpen_ip_7d") or 0) < thresholds["min_bullpen_innings_7d"]:
        return (None, None, {"reason": "low_bullpen_ip_home"})
    if (away.get("bullpen_ip_7d") or 0) < thresholds["min_bullpen_innings_7d"]:
        return (None, None, {"reason": "low_bullpen_ip_away"})
    if (home.get("n_games") or 0) < thresholds["min_offense_games_7d"]:
        return (None, None, {"reason": "low_offense_games_home"})
    if (away.get("n_games") or 0) < thresholds["min_offense_games_7d"]:
        return (None, None, {"reason": "low_offense_games_away"})

    combined_max_era = max(bp_h, bp_a)
    combined_avg_era = (bp_h + bp_a) / 2

    # Build the game-level traffic score by combining the per-team
    # composite scores. Falls back to the legacy hits+walks/game when
    # the traffic_score module didn't produce a bucket (missing data).
    from services.traffic_score import combine_team_traffic_scores
    home_ts = home.get("traffic_score_obj") or {}
    away_ts = away.get("traffic_score_obj") or {}
    combined_ts = combine_team_traffic_scores(home_ts, away_ts)
    traffic_bucket = combined_ts.get("traffic_bucket")
    combined_score = combined_ts.get("traffic_score") or 0

    # Legacy hits+walks/game retained for backwards-compat insights.
    legacy_h = home.get("offense_traffic_legacy_7d") or 0.0
    legacy_a = away.get("offense_traffic_legacy_7d") or 0.0
    legacy_combined = (legacy_h + legacy_a) / 2

    signal = {
        "bullpen_era_7d_home":   bp_h,
        "bullpen_era_7d_away":   bp_a,
        "bullpen_era_7d_max":    round(combined_max_era, 3),
        "bullpen_era_7d_avg":    round(combined_avg_era, 3),
        "bullpen_whip_7d_home":  home.get("bullpen_whip_7d"),
        "bullpen_whip_7d_away":  away.get("bullpen_whip_7d"),
        "offense_traffic_legacy_7d_avg":  round(legacy_combined, 2),
        # New composite traffic score (per game).
        "traffic_score":         combined_score,
        "traffic_bucket":        traffic_bucket,
        "traffic_score_home":    home_ts.get("traffic_score"),
        "traffic_score_away":    away_ts.get("traffic_score"),
        "traffic_breakdown_home": home_ts.get("components"),
        "traffic_breakdown_away": away_ts.get("components"),
    }

    primary: Optional[str] = None
    if combined_max_era > thresholds["bullpen_era_high"]:
        primary = "A"
    elif combined_max_era < thresholds["bullpen_era_normal_max"]:
        primary = "B"
    # else: gap zone — excluido para limpiar la separación de cohortes.

    sub: Optional[str] = None
    if primary == "A":
        # Use the composite traffic bucket (HIGH_TRAFFIC vs LOW_TRAFFIC).
        # MEDIUM_TRAFFIC and missing-data games are excluded from the
        # sub-cohorts to keep the separation clean.
        if traffic_bucket == "HIGH_TRAFFIC":
            sub = "A1"
        elif traffic_bucket == "LOW_TRAFFIC":
            sub = "A2"

    return primary, sub, signal


def settle_under(line: float, final_runs: Optional[int]) -> tuple[str, float]:
    """Returns (outcome, pnl_unit) for a 1-unit Under bet at -110."""
    if line is None or final_runs is None:
        return ("void", 0.0)
    if abs(final_runs - line) < 1e-9:
        return ("push", 0.0)
    if final_runs < line:
        return ("won", 0.9091)
    return ("lost", -1.0)


# ─────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────
def aggregate_rows(rows: list[dict], proxy_lines: list[float]) -> dict:
    """Compute per-line + overall metrics for a cohort."""
    n_rows = len(rows)
    if n_rows == 0:
        return {"sample_size": 0, "per_line": {}}

    per_line: dict[str, dict] = {}
    for line in proxy_lines:
        wins = pushes = losses = voids = 0
        pnl_sum = 0.0
        for r in rows:
            outcome, pnl = settle_under(line, r["final_total_runs"])
            if outcome == "won":
                wins += 1
            elif outcome == "lost":
                losses += 1
            elif outcome == "push":
                pushes += 1
            else:
                voids += 1
            pnl_sum += pnl
        countable = wins + losses
        roi_div = n_rows - voids
        per_line[f"{line}"] = {
            "n":         n_rows,
            "wins":      wins,
            "losses":    losses,
            "pushes":    pushes,
            "voids":     voids,
            "hit_rate":  round(wins / countable, 4) if countable else 0.0,
            "push_rate": round(pushes / n_rows, 4),
            "roi":       round(pnl_sum / roi_div, 4) if roi_div else 0.0,
        }

    def _avg(field: str) -> Optional[float]:
        vals = [r.get(field) for r in rows]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(fmean(vals), 3) if vals else None

    return {
        "sample_size":              n_rows,
        "avg_final_total_runs":     _avg("final_total_runs"),
        "avg_bullpen_era_7d_max":   _avg("bullpen_era_7d_max"),
        "avg_bullpen_era_7d_avg":   _avg("bullpen_era_7d_avg"),
        "avg_offense_traffic_legacy_7d": _avg("offense_traffic_legacy_7d_avg"),
        "avg_traffic_score":        _avg("traffic_score"),
        "per_line":                 per_line,
    }


def _line_distance_histogram(rows: list[dict], line: float) -> dict[str, int]:
    """Distribution of |final_runs - line| bucketed in 1-run bins."""
    hist: dict[str, int] = defaultdict(int)
    for r in rows:
        total = r.get("final_total_runs")
        if total is None:
            continue
        d = total - line
        bucket = f"{int(d):+d}"
        hist[bucket] += 1
    return dict(sorted(hist.items(), key=lambda kv: int(kv[0])))


# ─────────────────────────────────────────────────────────────────────
# Insight builder (Spanish)
# ─────────────────────────────────────────────────────────────────────
def build_analysis(cohort_metrics: dict, base_line: float) -> list[dict]:
    notes: list[dict] = []
    a = cohort_metrics.get("A",  {}) or {}
    b = cohort_metrics.get("B",  {}) or {}
    a1 = cohort_metrics.get("A1", {}) or {}
    a2 = cohort_metrics.get("A2", {}) or {}
    base_key = f"{base_line}"

    def _hit(block: dict) -> Optional[float]:
        return ((block.get("per_line") or {}).get(base_key) or {}).get("hit_rate")
    def _roi(block: dict) -> Optional[float]:
        return ((block.get("per_line") or {}).get(base_key) or {}).get("roi")

    if a.get("sample_size", 0) >= 10 and b.get("sample_size", 0) >= 10:
        ha, hb = _hit(a) or 0.0, _hit(b) or 0.0
        delta = ha - hb
        notes.append({
            "metric": "under_hit_rate_delta_A_vs_B",
            "value":  round(delta, 4),
            "es": f"En la línea base {base_line}, los Under en juegos con bullpen "
                  f"vulnerable (A) cerraron {ha:.0%} vs {hb:.0%} en bullpen normal (B). "
                  f"Delta {delta:+.0%} (n_A={a['sample_size']}, n_B={b['sample_size']}).",
        })
    if a1.get("sample_size", 0) >= 5 and a2.get("sample_size", 0) >= 5:
        h1, h2 = _hit(a1) or 0.0, _hit(a2) or 0.0
        delta = h1 - h2
        notes.append({
            "metric": "under_hit_rate_A1_vs_A2",
            "value":  round(delta, 4),
            "es": f"Dentro de A (bullpen vulnerable), tráfico ALTO (A1) tuvo {h1:.0%} "
                  f"de Under hits vs {h2:.0%} en tráfico BAJO (A2). "
                  f"Delta {delta:+.0%} (n_A1={a1['sample_size']}, n_A2={a2['sample_size']}).",
        })
    if a.get("sample_size", 0) >= 10:
        roi_a = _roi(a)
        if roi_a is not None:
            notes.append({
                "metric": "roi_cohort_A_at_base_line",
                "value":  roi_a,
                "es": f"ROI implícito (-110) de Under en cohorte A a línea {base_line}: "
                      f"{roi_a:+.2%} sobre {a['sample_size']} muestras.",
            })
    if not notes:
        notes.append({
            "metric": "insufficient_data",
            "value":  None,
            "es": "Muestras insuficientes para una conclusión sólida. "
                  "Ampliar la ventana o relajar los umbrales.",
        })
    return notes


# ─────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────
async def run_backtest(
    start_date: date,
    end_date: date,
    base_line: float,
    proxy_lines: list[float],
    thresholds: dict,
) -> dict:
    rows_by_cohort: dict[str, list[dict]] = {"A": [], "B": [], "A1": [], "A2": []}
    skipped = defaultdict(int)
    scanned = 0
    evaluated = 0

    async with MLBClient() as client:
        master_sched = await client.schedule(start_date.isoformat(), end_date.isoformat())

        # Pre-hydrate boxscores in parallel for evaluable games only.
        finals = []
        for g in master_sched:
            status = ((g.get("status") or {}).get("abstractGameState") or "").lower()
            if status != "final":
                skipped["not_finished"] += 1
                continue
            finals.append(g)
        scanned = len(finals)

        # Iterate sequentially per game so the cache builds up cleanly.
        for g in finals:
            gpk = g.get("gamePk")
            official = g.get("officialDate") or g.get("gameDate", "")[:10]
            try:
                gdate = datetime.strptime(official, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                skipped["bad_date"] += 1
                continue
            box = await client.boxscore(gpk) if gpk else None
            if not box:
                skipped["no_boxscore"] += 1
                continue
            teams = box.get("teams") or {}
            home_id = ((teams.get("home") or {}).get("team") or {}).get("id")
            away_id = ((teams.get("away") or {}).get("team") or {}).get("id")
            if not home_id or not away_id:
                skipped["missing_team_ids"] += 1
                continue
            final_total = extract_final_total(box)
            if final_total is None:
                skipped["missing_final_score"] += 1
                continue

            # Compute prior-7d windows for both teams in parallel.
            home_prior, away_prior = await asyncio.gather(
                compute_team_prior_window(client, home_id, gdate),
                compute_team_prior_window(client, away_id, gdate),
            )
            cohort, sub, signal = assign_cohort(home_prior, away_prior, thresholds)
            if cohort is None:
                skipped[signal.get("reason", "no_cohort")] += 1
                continue

            row = {
                "game_pk":           gpk,
                "official_date":     official,
                "home_team_id":      home_id,
                "away_team_id":      away_id,
                "home_team_name":   ((teams.get("home") or {}).get("team") or {}).get("name"),
                "away_team_name":   ((teams.get("away") or {}).get("team") or {}).get("name"),
                "final_total_runs":  final_total,
                **signal,
            }
            rows_by_cohort[cohort].append(row)
            if sub:
                rows_by_cohort[sub].append(row)
            evaluated += 1

        http_stats = {"requests": client.requests, "errors": client.errors}

    # Aggregate.
    cohort_metrics = {
        code: aggregate_rows(rows_by_cohort[code], proxy_lines)
        for code in ("A", "B", "A1", "A2")
    }
    histograms = {
        code: _line_distance_histogram(rows_by_cohort[code], base_line)
        for code in ("A", "B", "A1", "A2")
    }

    report = {
        "engine_version": "bullpen_under_backtest_mlb_api.1",
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "source":         "mlb_stats_api",
        "params": {
            "start":        start_date.isoformat(),
            "end":          end_date.isoformat(),
            "base_line":    base_line,
            "proxy_lines":  proxy_lines,
            "thresholds":   thresholds,
        },
        "scanned_games":   scanned,
        "evaluated_games": evaluated,
        "skipped":         dict(skipped),
        "http":            http_stats,
        "cohorts": {
            "A":  {"name": "Bullpen Vulnerable",
                   "rules": f"max(bullpen_era_7d_home, away) > {thresholds['bullpen_era_high']}",
                   **cohort_metrics["A"]},
            "B":  {"name": "Bullpen Normal/No Vulnerable",
                   "rules": f"max(bullpen_era_7d_home, away) < {thresholds['bullpen_era_normal_max']}",
                   **cohort_metrics["B"]},
            "A1": {"name": "A — Tráfico ofensivo ALTO",
                   "rules": "Cohort A + traffic_bucket == HIGH_TRAFFIC (composite score ≥ 70)",
                   **cohort_metrics["A1"]},
            "A2": {"name": "A — Tráfico ofensivo BAJO",
                   "rules": "Cohort A + traffic_bucket == LOW_TRAFFIC (composite score ≤ 39)",
                   **cohort_metrics["A2"]},
        },
        "line_distance_histograms": histograms,
        "analysis":     build_analysis(cohort_metrics, base_line),
        "observe_only": True,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUTPUT_DIR / f"bullpen_under_backtest_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))

    _print_human_summary(report, out_path)
    return report


def _print_human_summary(report: dict, out_path: Path) -> None:
    p = report["params"]
    print("\n=== BULLPEN UNDER HYPOTHESIS — BACKTEST (MLB Stats API) ===")
    print(f"range:            {p['start']} → {p['end']}")
    print(f"base_line:        {p['base_line']}    proxy_lines: {p['proxy_lines']}")
    print(f"scanned_games:    {report['scanned_games']}   evaluated: {report['evaluated_games']}")
    if report["skipped"]:
        print(f"skipped:          {report['skipped']}")
    print(f"http:             requests={report['http']['requests']}  errors={report['http']['errors']}")

    base_key = f"{p['base_line']}"
    for code, block in report["cohorts"].items():
        n = block.get("sample_size", 0)
        if n == 0:
            print(f"\n[{code}] {block['name']} — sin muestras")
            continue
        pl = (block.get("per_line") or {}).get(base_key) or {}
        print(f"\n[{code}] {block['name']} (n={n})")
        print(f"  Rules: {block['rules']}")
        print(f"  Avg total runs:       {block.get('avg_final_total_runs')}")
        print(f"  Avg bullpen ERA 7d:   {block.get('avg_bullpen_era_7d_max')}")
        print(f"  Avg traffic score:    {block.get('avg_traffic_score')}  "
              f"(legacy H+BB/g: {block.get('avg_offense_traffic_legacy_7d')})")
        if pl:
            print(f"  Línea base ({base_key}): "
                  f"hit_rate={pl.get('hit_rate'):.2%}  roi={pl.get('roi'):+.2%}  "
                  f"push_rate={pl.get('push_rate'):.2%}  W/L/P/V={pl['wins']}/{pl['losses']}/{pl['pushes']}/{pl['voids']}")
        # Per-line compact table
        print("  Por línea proxy:")
        for ln, stats in (block.get("per_line") or {}).items():
            print(f"    {ln:>5}: hit={stats['hit_rate']:.2%}  roi={stats['roi']:+.2%}  "
                  f"W/L/P/V={stats['wins']}/{stats['losses']}/{stats['pushes']}/{stats['voids']}")

    print("\n--- ANALYSIS ---")
    for n in report["analysis"]:
        print(f"  • {n['es']}")
    print(f"\nReport saved → {out_path}")
    print("observe_only=True (este script no modifica picks ni BD).")


# ─────────────────────────────────────────────────────────────────────
# CLI entry
# ─────────────────────────────────────────────────────────────────────
def _resolve_dates(args) -> tuple[date, date]:
    if args.start and args.end:
        s = datetime.strptime(args.start, "%Y-%m-%d").date()
        e = datetime.strptime(args.end,   "%Y-%m-%d").date()
        return s, e
    days = args.days or 45
    today = datetime.now(timezone.utc).date()
    # Yesterday as upper bound to ensure all games are final.
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", help="ISO date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end",   help="ISO date YYYY-MM-DD (inclusive)")
    parser.add_argument("--days",  type=int, default=None,
                        help="fallback window in days if --start/--end not given (default 45)")
    parser.add_argument("--line",  type=float, default=9.5,
                        help="línea base (default 9.5)")
    parser.add_argument("--proxy-lines", type=str, default=None,
                        help="CSV de líneas proxy. Default: 8.5,9.0,9.5,10.0,10.5")
    parser.add_argument("--bullpen-high", type=float, default=DEFAULT_THRESHOLDS["bullpen_era_high"])
    parser.add_argument("--bullpen-normal", type=float, default=DEFAULT_THRESHOLDS["bullpen_era_normal_max"])
    parser.add_argument("--traffic-high", type=float, default=DEFAULT_THRESHOLDS["traffic_high"])
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    start, end = _resolve_dates(args)
    proxy = [float(x) for x in args.proxy_lines.split(",")] if args.proxy_lines else list(DEFAULT_PROXY_LINES)
    thresholds = {
        **DEFAULT_THRESHOLDS,
        "bullpen_era_high":       args.bullpen_high,
        "bullpen_era_normal_max": args.bullpen_normal,
        "traffic_high":           args.traffic_high,
    }
    asyncio.run(run_backtest(start, end, args.line, proxy, thresholds))


if __name__ == "__main__":
    main()
