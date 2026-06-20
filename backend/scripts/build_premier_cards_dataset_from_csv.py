"""Sprint-D8/E PASO 2 (live) · Build Premier League cards dataset from
football-data.co.uk CSV (free, no scrape.do credits needed).

Source: ``/app/data/football_data_co_uk/E0_2425.csv``
Columns used:
  Date, HomeTeam, AwayTeam, Referee,
  HF (home fouls), AF (away fouls),
  HY (home yellows), AY (away yellows),
  HR (home reds), AR (away reds)

Output: ``/app/data/cards_history/premier_last_4_months.json``

The user asked for "~150 partidos Premier últimos 4 meses". We take the
last 150 chronological matches from E0_2425.csv (the 24/25 season). To
respect the PIT discipline of the ingestor, we ALSO include the full
season (380 matches) as history; the ingestor itself filters
``row_dt < target_dt`` so older matches contribute to the referee
average correctly.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

CSV_PATH = Path("/app/data/football_data_co_uk/E0_2425.csv")
OUT      = Path("/app/data/cards_history/premier_last_4_months.json")
N_TARGET = 150


def _parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_int(s: str) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def main() -> int:
    if not CSV_PATH.exists():
        print(f"[error] CSV not found: {CSV_PATH}")
        return 2
    with CSV_PATH.open("r", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        rows = list(rdr)
    print(f"[load] {len(rows)} rows from {CSV_PATH.name}")

    parsed: list[dict] = []
    skipped_no_date = 0
    skipped_no_ref  = 0
    for r in rows:
        d = _parse_date(r.get("Date", ""))
        if d is None:
            skipped_no_date += 1
            continue
        ref = (r.get("Referee") or "").strip()
        if not ref:
            skipped_no_ref += 1
        hy = _to_int(r.get("HY")) or 0
        ay = _to_int(r.get("AY")) or 0
        hr = _to_int(r.get("HR")) or 0
        ar = _to_int(r.get("AR")) or 0
        hf = _to_int(r.get("HF"))
        af = _to_int(r.get("AF"))
        parsed.append({
            "match_id":   f"E0_{d.strftime('%Y%m%d')}_{r.get('HomeTeam','').replace(' ','_')}",
            "date":       d.strftime("%Y-%m-%d"),
            "league":     "Premier League",
            "home_team":  (r.get("HomeTeam") or "").strip(),
            "away_team":  (r.get("AwayTeam") or "").strip(),
            "referee":    ref,
            "home_cards": hy + hr,    # yellow + red home (player penalised)
            "away_cards": ay + ar,
            "home_fouls": hf,
            "away_fouls": af,
        })

    parsed.sort(key=lambda r: r["date"])
    # Take the last 150 matches chronologically — but keep the FULL
    # season in the file so the PIT ingestor can use older matches as
    # history for the referee/team averages. The downstream
    # ``run_cards_phase1_modelonly.py`` evaluator iterates over EVERY
    # row in the file and uses the rest as history for each one, so we
    # keep the full season here and let evaluation focus on the last
    # 150 via a separate flag if needed.
    last_150 = parsed[-N_TARGET:]
    print(f"[parse] {len(parsed)} matches with parseable dates "
           f"(skipped: no_date={skipped_no_date}, no_referee={skipped_no_ref})")
    print(f"[select] last {len(last_150)} matches (chronological): "
           f"{last_150[0]['date']} → {last_150[-1]['date']}")

    # Spot-check: distinct referees + distribution of total cards.
    refs = sorted({r["referee"] for r in last_150 if r["referee"]})
    print(f"[stats] distinct referees in last-150 window: {len(refs)}")
    totals = [r["home_cards"] + r["away_cards"] for r in last_150]
    if totals:
        avg = sum(totals) / len(totals)
        print(f"[stats] avg total cards/match in window: {avg:.2f}")

    # We WRITE the full season so the PIT history is rich.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(parsed, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[write] {OUT}  ({len(parsed)} matches; last 150 = evaluation focus)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
