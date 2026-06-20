"""Corner Momentum Study — Fase 1 (Opción B) · PASO 1.2

Construye un dataset unificado multi-liga / multi-temporada a partir
de los CSVs gratuitos de ``football-data.co.uk`` ya almacenados en
``/app/data/football_data_co_uk/``.

Ligas y temporadas incluidas (12 archivos = 12 temporadas):

  * EPL (E0): 2122, 2223, 2324
  * Bundesliga (D1): 2122, 2223, 2324
  * La Liga (SP1): 2122, 2223, 2324
  * Serie A (I1): 2122, 2223, 2324

(Se descarta deliberadamente Liga MX porque ``new/MEX.csv`` no contiene
columnas HC/AC; quedó archivado en ``extra_no_corners/MEX.csv``.)

Salida:
  /app/data/corners_history/all_leagues_dataset.json

Estructura por registro:
{
  "match_id": "...",
  "date": "YYYY-MM-DD",
  "league": "EPL|Bundesliga|LaLiga|SerieA",
  "league_code": "E0|D1|SP1|I1",
  "season": "2122|2223|2324",
  "home_team": "...",
  "away_team": "...",
  "home_corners": int,
  "away_corners": int,
  "total_corners": int,
  "home_shots": int|None,
  "away_shots": int|None,
  "home_shots_on_target": int|None,
  "away_shots_on_target": int|None,
  "home_fouls": int|None,
  "away_fouls": int|None,
  "home_yellow": int|None,
  "away_yellow": int|None,
  "home_red": int|None,
  "away_red": int|None,
  "home_cards": int|None,        # yellow + red
  "away_cards": int|None,
  "fthg": int|None,
  "ftag": int|None,
  "b365h": float|None,
  "b365d": float|None,
  "b365a": float|None,
  "implied_prob_home": float|None,
  "implied_prob_draw": float|None,
  "implied_prob_away": float|None
}

Reglas de calidad:
  * Drop fila si Date o HC/AC son inválidos (sin córners → no útil).
  * Parse robusto de fechas (`%d/%m/%Y`, `%d/%m/%y`, `%Y-%m-%d`).
  * Probabilidades implícitas normalizadas (vig removido).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

CSV_DIR = Path("/app/data/football_data_co_uk")
OUT     = Path("/app/data/corners_history/all_leagues_dataset.json")

LEAGUES = {
    "E0":  "EPL",
    "D1":  "Bundesliga",
    "SP1": "LaLiga",
    "I1":  "SerieA",
}
SEASONS = ["2122", "2223", "2324"]


def _parse_date(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_int(s) -> Optional[int]:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _to_float(s) -> Optional[float]:
    try:
        v = float(s)
        if v <= 0:
            return None
        return v
    except (TypeError, ValueError):
        return None


def _implied_probs(h: Optional[float], d: Optional[float], a: Optional[float]):
    """Probabilidades implícitas con vig removido (devigging proporcional)."""
    if not (h and d and a):
        return None, None, None
    ph, pd, pa = 1.0 / h, 1.0 / d, 1.0 / a
    s = ph + pd + pa
    if s <= 0:
        return None, None, None
    return ph / s, pd / s, pa / s


def _parse_csv(path: Path, league_code: str, season: str) -> list[dict]:
    league = LEAGUES[league_code]
    rows_out: list[dict] = []
    skipped_no_date = 0
    skipped_no_corners = 0
    with path.open("r", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for r in rdr:
            d = _parse_date(r.get("Date", ""))
            if d is None:
                skipped_no_date += 1
                continue
            hc = _to_int(r.get("HC"))
            ac = _to_int(r.get("AC"))
            if hc is None or ac is None:
                skipped_no_corners += 1
                continue

            hy = _to_int(r.get("HY"))
            ay = _to_int(r.get("AY"))
            hr_ = _to_int(r.get("HR"))
            ar_ = _to_int(r.get("AR"))
            home_cards = None
            away_cards = None
            if hy is not None or hr_ is not None:
                home_cards = (hy or 0) + (hr_ or 0)
            if ay is not None or ar_ is not None:
                away_cards = (ay or 0) + (ar_ or 0)

            b365h = _to_float(r.get("B365H"))
            b365d = _to_float(r.get("B365D"))
            b365a = _to_float(r.get("B365A"))
            # Fallback a AvgH/AvgD/AvgA si B365 falta
            if not (b365h and b365d and b365a):
                b365h = b365h or _to_float(r.get("AvgH"))
                b365d = b365d or _to_float(r.get("AvgD"))
                b365a = b365a or _to_float(r.get("AvgA"))
            iph, ipd, ipa = _implied_probs(b365h, b365d, b365a)

            home = (r.get("HomeTeam") or "").strip()
            away = (r.get("AwayTeam") or "").strip()
            rec = {
                "match_id":             f"{league_code}_{season}_{d.strftime('%Y%m%d')}_{home.replace(' ', '_')}",
                "date":                 d.strftime("%Y-%m-%d"),
                "league":               league,
                "league_code":          league_code,
                "season":               season,
                "home_team":            home,
                "away_team":            away,
                "home_corners":         hc,
                "away_corners":         ac,
                "total_corners":        hc + ac,
                "home_shots":           _to_int(r.get("HS")),
                "away_shots":           _to_int(r.get("AS")),
                "home_shots_on_target": _to_int(r.get("HST")),
                "away_shots_on_target": _to_int(r.get("AST")),
                "home_fouls":           _to_int(r.get("HF")),
                "away_fouls":           _to_int(r.get("AF")),
                "home_yellow":          hy,
                "away_yellow":          ay,
                "home_red":             hr_,
                "away_red":             ar_,
                "home_cards":           home_cards,
                "away_cards":           away_cards,
                "fthg":                 _to_int(r.get("FTHG")),
                "ftag":                 _to_int(r.get("FTAG")),
                "b365h":                b365h,
                "b365d":                b365d,
                "b365a":                b365a,
                "implied_prob_home":    iph,
                "implied_prob_draw":    ipd,
                "implied_prob_away":    ipa,
            }
            rows_out.append(rec)
    print(f"[parse] {path.name}: kept={len(rows_out)} "
           f"(skipped: no_date={skipped_no_date}, no_corners={skipped_no_corners})")
    return rows_out


def main() -> int:
    all_rows: list[dict] = []
    for code in LEAGUES:
        for season in SEASONS:
            p = CSV_DIR / f"{code}_{season}.csv"
            if not p.exists():
                print(f"[warn] missing: {p}")
                continue
            all_rows.extend(_parse_csv(p, code, season))

    all_rows.sort(key=lambda r: (r["date"], r["league_code"]))

    # Sanity stats por liga
    print("\n[summary] matches per league/season:")
    summary: dict[str, dict[str, int]] = {}
    for r in all_rows:
        summary.setdefault(r["league"], {})
        summary[r["league"]][r["season"]] = summary[r["league"]].get(r["season"], 0) + 1
    for league, by_season in sorted(summary.items()):
        for season, n in sorted(by_season.items()):
            print(f"  {league:<11} {season}: {n}")
        total = sum(by_season.values())
        print(f"  {league:<11} TOTAL: {total}")
    print(f"\n[summary] grand total matches: {len(all_rows)}")

    # Cobertura de columnas opcionales
    n = len(all_rows)
    cols_to_check = [
        "home_shots", "home_shots_on_target", "home_fouls",
        "home_cards", "b365h", "implied_prob_home",
    ]
    print("\n[coverage] non-null ratio for optional columns:")
    for c in cols_to_check:
        non_null = sum(1 for r in all_rows if r.get(c) is not None)
        pct = 100.0 * non_null / n if n else 0.0
        print(f"  {c:<26} {non_null}/{n}  ({pct:.1f}%)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_rows, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"\n[write] {OUT}  ({len(all_rows)} matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
