"""One-shot scraper: extract CARDS (yellow + red, with 1T/2T timing) for
the 123 selecciones records from 365Scores via scrape.do.

Strategy (credit-efficient):
  1. Read openfootball JSONs (wc2022, euro2024, copa_america_2024) to get
     the (date, team1, team2) set.
  2. For each unique date, call ``fetch_games_by_date`` ONCE (1 credit/day).
  3. Filter by competition_id in {7=EPL no, 18=WC, 134=Euro, 138=Copa America}
     OR by team-name match against openfootball.
  4. For each resolved game, call ``fetch_game_detail(game_id)`` (1 credit).
  5. Extract: home/away yellow/red, total goals, half (≤45 / >45) per card.

Output: /app/data/cards_history/selecciones_cards_dataset.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Load .env
_env = Path(__file__).resolve().parents[1] / ".env"
if _env.exists():
    for L in _env.read_text().splitlines():
        if "=" in L and not L.startswith("#"):
            k, _, v = L.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from services.external_sources.three65scores_live_fetchers import (  # noqa: E402
    fetch_games_by_date, fetch_game_detail,
)
from services.football_selecciones_ingestor import normalise_team_name  # noqa: E402

OF_FILES = [
    ("wc2022",            "/app/data/openfootball/wc2022.json"),
    ("euro2024",          "/app/data/openfootball/euro2024.json"),
    ("copa_america_2024", "/app/data/openfootball/copa_america_2024.json"),
]
OUT = Path("/app/data/cards_history/selecciones_cards_dataset.json")


def _team_set(a, b) -> set[str]:
    return {normalise_team_name(a).lower(), normalise_team_name(b).lower()}


def _load_all_of_matches() -> list[dict]:
    out = []
    for tname, path in OF_FILES:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in doc.get("matches", []):
            out.append({**m, "_tournament": tname})
    return out


def _extract_cards(game_detail: dict) -> dict:
    g = game_detail.get("game") if isinstance(game_detail, dict) else None
    if not g:
        return {"available": False}
    competitors = g.get("competitors") or [g.get("homeCompetitor"), g.get("awayCompetitor")]
    home_id = (competitors[0] or {}).get("id") if competitors else None
    away_id = (competitors[1] or {}).get("id") if competitors else None
    home_name = (competitors[0] or {}).get("name") if competitors else None
    away_name = (competitors[1] or {}).get("name") if competitors else None
    home_score = (competitors[0] or {}).get("score") if competitors else None
    away_score = (competitors[1] or {}).get("score") if competitors else None

    events = g.get("events") or []
    hy = ay = hr = ar = 0
    hy_1t = ay_1t = hr_1t = ar_1t = 0
    hy_2t = ay_2t = hr_2t = ar_2t = 0
    for e in events:
        et = (e.get("eventType") or {})
        etid = et.get("id")
        if etid not in (2, 3):  # 2=Yellow, 3=Red
            continue
        cid = e.get("competitorId")
        gt = e.get("gameTime") or 0
        first_half = gt <= 45
        if etid == 2:  # Yellow
            if cid == home_id:
                hy += 1; (hy_1t := hy_1t + 1) if first_half else (hy_2t := hy_2t + 1)
            elif cid == away_id:
                ay += 1; (ay_1t := ay_1t + 1) if first_half else (ay_2t := ay_2t + 1)
        else:  # Red
            if cid == home_id:
                hr += 1; (hr_1t := hr_1t + 1) if first_half else (hr_2t := hr_2t + 1)
            elif cid == away_id:
                ar += 1; (ar_1t := ar_1t + 1) if first_half else (ar_2t := ar_2t + 1)
    referee_block = g.get("officials") or g.get("referee") or []
    referee = ((referee_block[0] or {}).get("name")
                if isinstance(referee_block, list) and referee_block
                else (referee_block.get("name") if isinstance(referee_block, dict) else None))
    return {
        "available": True,
        "home_team": home_name, "away_team": away_name,
        "home_id": home_id, "away_id": away_id,
        "home_score": home_score, "away_score": away_score,
        "home_yellow": hy, "away_yellow": ay,
        "home_red": hr,    "away_red": ar,
        "home_yellow_1t": hy_1t, "away_yellow_1t": ay_1t,
        "home_yellow_2t": hy_2t, "away_yellow_2t": ay_2t,
        "home_red_1t": hr_1t, "away_red_1t": ar_1t,
        "home_red_2t": hr_2t, "away_red_2t": ar_2t,
        "referee": referee,
        "n_events": len(events),
    }


async def main() -> int:
    of_matches = _load_all_of_matches()
    print(f"[load] {len(of_matches)} openfootball matches "
           f"(WC+Euro+Copa)")

    by_date: dict[str, list[dict]] = {}
    for m in of_matches:
        by_date.setdefault(m["date"], []).append(m)
    print(f"[group] {len(by_date)} unique tournament dates")

    out: list[dict] = []
    credits_listings = 0
    credits_details  = 0

    for date_iso in sorted(by_date.keys()):
        try:
            games = await fetch_games_by_date(date_iso)
            credits_listings += 1
        except Exception as exc:
            print(f"[error] listing {date_iso}: {exc}")
            continue
        # Index games by team_set.
        for of_m in by_date[date_iso]:
            target_set = _team_set(of_m["team1"], of_m["team2"])
            game = None
            for g in games:
                hn = (g.get("homeCompetitor") or {}).get("name") or ""
                an = (g.get("awayCompetitor") or {}).get("name") or ""
                if _team_set(hn, an) == target_set:
                    game = g
                    break
            if game is None:
                out.append({**of_m, "available": False,
                             "reason": "NO_365SCORES_GAME_FOUND"})
                continue
            game_id = str(game.get("id"))
            try:
                detail = await fetch_game_detail(game_id)
                credits_details += 1
            except Exception as exc:
                print(f"[error] detail {game_id}: {exc}")
                out.append({**of_m, "available": False, "game_id": game_id,
                              "reason": "DETAIL_FETCH_FAILED"})
                continue
            cards = _extract_cards(detail)
            out.append({
                "tournament": of_m["_tournament"],
                "date":       of_m["date"],
                "team1":      of_m["team1"],
                "team2":      of_m["team2"],
                "of_score":   of_m.get("score", {}).get("ft"),
                "round":      of_m.get("round"),
                "game_id":    game_id,
                **cards,
            })
        # Progress.
        if credits_listings % 5 == 0:
            print(f"  progress: listings={credits_listings}, "
                   f"details={credits_details}, records={len(out)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[write] {OUT}")
    print(f"[summary] credits_listings={credits_listings}, "
           f"credits_details={credits_details}, total={credits_listings+credits_details}")
    avail = sum(1 for r in out if r.get("available"))
    print(f"[summary] records={len(out)}, available={avail}, "
           f"unavailable={len(out)-avail}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
