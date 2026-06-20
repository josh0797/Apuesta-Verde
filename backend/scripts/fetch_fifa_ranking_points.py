"""One-shot helper to download FIFA world ranking points (Dato-Futbol source)
and produce per-tournament team→points lookups.

Source: https://raw.githubusercontent.com/Dato-Futbol/fifa-ranking/master/ranking_fifa_historical.csv
        (Dec 1992 → Sep 2024)

Output: /app/data/fifa_ranking/team_points_by_tournament.json
Schema:
  {
    "_meta": {...},
    "tournaments": {
      "wc2022":            {"snapshot_date": "2022-10-06", "n_teams": ..., "points": {"Argentina": 1773.88, ...}},
      "euro2024":          {"snapshot_date": "2024-04-04", "n_teams": ..., "points": {...}},
      "copa_america_2024": {"snapshot_date": "2024-04-04", "n_teams": ..., "points": {...}}
    }
  }

Team-name normalization is light: the source uses canonical names that
align with openfootball mostly 1:1 (Argentina, Brazil, etc.). A small
alias map covers the known mismatches (USA → "United States", etc.).
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

URL = ("https://raw.githubusercontent.com/Dato-Futbol/fifa-ranking/"
        "master/ranking_fifa_historical.csv")
OUT = Path("/app/data/fifa_ranking/team_points_by_tournament.json")

# Snapshot dates: the latest official FIFA release just BEFORE each
# tournament's first match. These are real publication dates.
TOURNAMENTS = {
    "wc2022":            "2022-10-06",  # last pre-WC release
    "euro2024":          "2024-04-04",  # last pre-Euro release
    "copa_america_2024": "2024-04-04",  # same release for Copa Am
}

# Aliases to align FIFA names with openfootball names.
ALIASES = {
    "USA":            "United States",
    "IR Iran":        "Iran",
    "Korea Republic": "South Korea",
    "Republic of Ireland": "Ireland",
    "Türkiye":        "Turkey",
    "Czechia":        "Czech Republic",
    "DR Congo":       "Congo DR",
}


def main():
    print(f"[download] {URL}")
    with urllib.request.urlopen(URL, timeout=20) as r:
        raw = r.read().decode("utf-8")
    print(f"[download] OK ({len(raw)} bytes)")
    rdr = csv.DictReader(io.StringIO(raw))
    rows = list(rdr)
    print(f"[parse] {len(rows)} ranking rows")

    out = {"_meta": {"source": URL,
                       "rows_loaded": len(rows),
                       "aliases_applied": ALIASES},
           "tournaments": {}}

    for tname, snap_date in TOURNAMENTS.items():
        # The CSV has multiple release dates. Pick the LATEST date <= snap_date.
        candidate_dates = sorted({r["date"] for r in rows if r["date"] <= snap_date})
        if not candidate_dates:
            print(f"[warn] no rows on/before {snap_date} for {tname}")
            continue
        chosen = candidate_dates[-1]
        slice_ = [r for r in rows if r["date"] == chosen]
        points: dict[str, float] = {}
        for r in slice_:
            tm = r["team"]
            # Strip "(unranked)" suffixes and aliases.
            tm_clean = tm.replace(" (unranked)", "").strip()
            tm_norm = ALIASES.get(tm_clean, tm_clean)
            try:
                pts = float(r["total_points"])
            except (TypeError, ValueError):
                continue
            points[tm_norm] = pts
        out["tournaments"][tname] = {
            "snapshot_date_requested": snap_date,
            "snapshot_date_used":      chosen,
            "n_teams":                 len(points),
            "points":                  points,
        }
        print(f"[{tname}] snapshot={chosen} n_teams={len(points)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[write] {OUT}")
    # Spot-check a few teams.
    for tname in TOURNAMENTS:
        block = out["tournaments"].get(tname, {})
        pts = block.get("points") or {}
        for t in ("Argentina", "Spain", "Brazil", "France", "Germany",
                   "Colombia", "Ecuador", "Hungary"):
            if t in pts:
                print(f"  [{tname}] {t}: {pts[t]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
