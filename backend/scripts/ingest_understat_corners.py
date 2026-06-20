"""Corner Momentum Study — Sprint C2 (datos ricos) · PASO C2.1

Ingestor para descargar datos de **Understat** (xG, xGA, npxG, npxGA,
deep, deep_allowed, PPDA y forecast pre-partido) para las 4 ligas
europeas × 3 temporadas (12 endpoints en total).

Endpoint usado:
  GET https://understat.com/getLeagueData/{league}/{season_year}
  Requiere header `Referer: https://understat.com/league/{league}/{season_year}`.

Mapeo de season "2122" (football-data.co.uk) → "2021" (Understat):
  Understat usa el año de **inicio** de la temporada como índice.

Salida:
  /app/data/corners_history/understat_raw/{league}_{season}.json
  /app/data/corners_history/understat_matches_consolidated.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import gzip

RAW_DIR = Path("/app/data/corners_history/understat_raw")
OUT_CONSOLIDATED = Path("/app/data/corners_history/understat_matches_consolidated.json")

# (football-data code, football-data season) → (Understat league, Understat season)
JOBS = [
    ("E0",  "2122", "EPL",        "2021"),
    ("E0",  "2223", "EPL",        "2022"),
    ("E0",  "2324", "EPL",        "2023"),
    ("D1",  "2122", "Bundesliga", "2021"),
    ("D1",  "2223", "Bundesliga", "2022"),
    ("D1",  "2324", "Bundesliga", "2023"),
    ("SP1", "2122", "La_liga",    "2021"),
    ("SP1", "2223", "La_liga",    "2022"),
    ("SP1", "2324", "La_liga",    "2023"),
    ("I1",  "2122", "Serie_A",    "2021"),
    ("I1",  "2223", "Serie_A",    "2022"),
    ("I1",  "2324", "Serie_A",    "2023"),
]

DELAY_BETWEEN_CALLS_S = 1.0
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _fetch(league_understat: str, season_understat: str) -> Optional[dict]:
    url = f"https://understat.com/getLeagueData/{league_understat}/{season_understat}"
    referer = f"https://understat.com/league/{league_understat}/{season_understat}"
    req = Request(
        url,
        headers={
            "User-Agent":         USER_AGENT,
            "Accept":             "application/json, text/javascript, */*; q=0.01",
            "Accept-Language":    "en-US,en;q=0.9",
            "Accept-Encoding":    "gzip, deflate, br",
            "Referer":            referer,
            "X-Requested-With":   "XMLHttpRequest",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            text = raw.decode("utf-8")
            return json.loads(text)
    except HTTPError as e:
        print(f"  [http-error] {e.code} {e.reason}")
    except URLError as e:
        print(f"  [url-error] {e.reason}")
    except Exception as e:
        print(f"  [error] {e}")
    return None


def _extract_matches(raw: dict, league_code: str, season_code: str) -> list[dict]:
    """Convierte el JSON de Understat (con keys `dates`, `teams`, `players`)
    en una lista plana de matches: 1 record por partido (no por equipo).
    """
    teams = raw.get("teams", {})
    dates = raw.get("dates", [])

    # Index team history by date for fast lookup (a date may have multiple games)
    # Build by team_id -> {date_iso -> match_dict}
    team_hist_by_date: dict[str, dict[str, dict]] = {}
    for team_id, team_obj in teams.items():
        team_hist_by_date.setdefault(team_id, {})
        for h in team_obj.get("history", []):
            d = h.get("date", "").split(" ")[0]  # YYYY-MM-DD
            team_hist_by_date[team_id][d] = h

    out: list[dict] = []
    for entry in dates:
        h_team = entry.get("h", {})
        a_team = entry.get("a", {})
        date_iso = (entry.get("datetime") or "").split(" ")[0]
        # localizar el match en historiales por equipo
        hh = team_hist_by_date.get(str(h_team.get("id")), {}).get(date_iso)
        ah = team_hist_by_date.get(str(a_team.get("id")), {}).get(date_iso)
        # Use forecast from dates entry
        fc = entry.get("forecast", {}) or {}
        rec = {
            "match_id_understat":  entry.get("id"),
            "league_code":         league_code,
            "season":              season_code,
            "date":                date_iso,
            "home_team":           h_team.get("title"),
            "away_team":           a_team.get("title"),
            "home_team_short":     h_team.get("short_title"),
            "away_team_short":     a_team.get("short_title"),
            "goals_h":             _to_int(entry.get("goals", {}).get("h")),
            "goals_a":             _to_int(entry.get("goals", {}).get("a")),
            "xg_h":                _to_float(entry.get("xG", {}).get("h")),
            "xg_a":                _to_float(entry.get("xG", {}).get("a")),
            "forecast_h":          _to_float(fc.get("w")),  # P(home win)
            "forecast_d":          _to_float(fc.get("d")),
            "forecast_a":          _to_float(fc.get("l")),  # P(home loss = away win)
            # Detalles desde historial por equipo (cuando aplica)
            "npxg_h":              _safe(hh, "npxG"),
            "npxg_a":              _safe(ah, "npxG"),
            "xga_h":               _safe(hh, "xGA"),
            "xga_a":               _safe(ah, "xGA"),
            "npxga_h":             _safe(hh, "npxGA"),
            "npxga_a":             _safe(ah, "npxGA"),
            "deep_h":              _safe(hh, "deep"),
            "deep_a":              _safe(ah, "deep"),
            "deep_allowed_h":      _safe(hh, "deep_allowed"),
            "deep_allowed_a":      _safe(ah, "deep_allowed"),
            "ppda_h":              _ppda_ratio(_safe(hh, "ppda")),
            "ppda_a":              _ppda_ratio(_safe(ah, "ppda")),
            "ppda_allowed_h":      _ppda_ratio(_safe(hh, "ppda_allowed")),
            "ppda_allowed_a":      _ppda_ratio(_safe(ah, "ppda_allowed")),
            "xpts_h":              _safe(hh, "xpts"),
            "xpts_a":              _safe(ah, "xpts"),
        }
        out.append(rec)
    return out


def _to_int(s) -> Optional[int]:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _to_float(s) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe(obj: Optional[dict], key: str):
    if obj is None:
        return None
    v = obj.get(key)
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _ppda_ratio(ppda_obj) -> Optional[float]:
    """PPDA típicamente se reporta como (att passes opp. half) / (defensive actions).
    Understat lo entrega como dict {att, def}. Calculamos el ratio.
    """
    if not isinstance(ppda_obj, dict):
        return None
    att = ppda_obj.get("att")
    deff = ppda_obj.get("def")
    if att is None or deff is None or deff == 0:
        return None
    try:
        return float(att) / float(deff)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    all_matches: list[dict] = []
    successes, failures = 0, 0
    for league_code, season_code, league_und, season_und in JOBS:
        out_path = RAW_DIR / f"{league_code}_{season_code}.json"
        if out_path.exists():
            try:
                raw = json.loads(out_path.read_text(encoding="utf-8"))
                print(f"[cache] {league_code} {season_code} ({league_und}/{season_und}): "
                       f"{len(raw.get('dates', []))} matches (loaded from cache)")
            except Exception:
                raw = None
        else:
            raw = None
        if raw is None:
            print(f"[fetch] {league_code} {season_code} ({league_und}/{season_und}) …", end=" ")
            raw = _fetch(league_und, season_und)
            if raw is None:
                print("FAIL")
                failures += 1
                continue
            out_path.write_text(json.dumps(raw), encoding="utf-8")
            print(f"OK ({len(raw.get('dates', []))} matches)")
            time.sleep(DELAY_BETWEEN_CALLS_S)
        successes += 1
        matches = _extract_matches(raw, league_code, season_code)
        all_matches.extend(matches)

    # Stats
    n = len(all_matches)
    print(f"\n[summary] jobs ok={successes} fail={failures}; matches extracted={n}")

    if all_matches:
        # cobertura
        keys_to_check = ["xg_h", "xg_a", "npxg_h", "xga_h", "deep_h", "ppda_h",
                          "forecast_h"]
        print("[coverage]")
        for k in keys_to_check:
            non_null = sum(1 for r in all_matches if r.get(k) is not None)
            pct = 100.0 * non_null / n if n else 0.0
            print(f"  {k:<14} {non_null}/{n}  ({pct:.1f}%)")

    OUT_CONSOLIDATED.parent.mkdir(parents=True, exist_ok=True)
    OUT_CONSOLIDATED.write_text(json.dumps(all_matches, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    print(f"\n[write] consolidated → {OUT_CONSOLIDATED}  ({n} matches)")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
