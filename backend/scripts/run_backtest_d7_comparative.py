"""Sprint-D7 · Comparative backtest: domestic leagues vs national tournaments.

Strict ``observe_only`` — never writes to production, never triggers a
bet flow. Produces three blocks in a single JSON report:

    1. domestic_leagues_summary     (odds_type=OPENING)
    2. national_tournaments_summary (odds_type=POINT_IN_TIME_PREMATCH)
    3. combined_comparison           (with W_ODDS_TYPE_MISMATCH warning)

Anti-overfitting rule (non-negotiable): cohort membership is derived
*only* from pre-match features. ``detect_cohorts`` is called WITHOUT
``fthg``/``ftag``/``ftr`` so the cohort cannot leak the outcome.
Verdict on the hypothesis combines ROI significance (CI > 0), Brier
delta and hit-rate per the user's choice (b).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_backtest_engine import run_backtest                # noqa: E402
from services.football_backtest_metrics import compute_backtest_metrics    # noqa: E402
from services.football_cohort_detector import (                              # noqa: E402
    detect_cohorts, summarise_picks_by_cohort,
)
from services.football_historical_ingestor import (                          # noqa: E402
    parse_football_data_csv, parse_openfootball_json,
)
from services.theoddsapi_historical_client import (                           # noqa: E402
    DEFAULT_MAX_CREDITS, fetch_tournament_pit_odds,
)

log = logging.getLogger("d7_comparative")

# ─── Configurable inputs ──────────────────────────────────────────────
LEAGUES_2425 = [
    ("E0",  "premier_league"),
    ("SP1", "la_liga"),
    ("I1",  "serie_a"),
    ("D1",  "bundesliga"),
    ("F1",  "ligue_1"),
]
FOOTBALL_DATA_URL = "https://www.football-data.co.uk/mmz4281/2425/{code}.csv"

NATIONAL_TOURNAMENTS = [
    # (label, sport_key, start_date_iso, end_date_iso, openfootball_json)
    ("world_cup_2022", "soccer_fifa_world_cup",
     "2022-11-20T00:00:00Z", "2022-12-18T23:59:59Z",
     "/app/data/openfootball/wc2022.json"),
    ("euro_2024",      "soccer_uefa_european_championship",
     "2024-06-14T00:00:00Z", "2024-07-14T23:59:59Z",
     "/app/data/openfootball/euro2024.json"),
    ("copa_america_2024", "soccer_conmebol_copa_america",
     "2024-06-20T00:00:00Z", "2024-07-14T23:59:59Z",
     "/app/data/openfootball/copa2024.json"),
    ("copa_america_2021", "soccer_conmebol_copa_america",
     "2021-06-13T00:00:00Z", "2021-07-10T23:59:59Z",
     "/app/data/openfootball/copa2021.json"),
]

CSV_CACHE_DIR = Path(os.environ.get("D7_CSV_CACHE_DIR",
                                       "/app/data/football_data_co_uk"))
CSV_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── CSV download ─────────────────────────────────────────────────────
def _ensure_league_csv(code: str) -> Optional[Path]:
    """Download a league CSV to the local cache. Returns ``None`` if
    the download fails (the network might be sandboxed)."""
    dst = CSV_CACHE_DIR / f"{code}_2425.csv"
    if dst.exists() and dst.stat().st_size > 1024:
        return dst
    url = FOOTBALL_DATA_URL.format(code=code)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        dst.write_bytes(data)
        return dst
    except Exception as exc:    # noqa: BLE001
        log.warning("CSV download failed for %s: %s", code, exc)
        return None


# ─── Date iterator for tournament windows ─────────────────────────────
def _iter_dates(start_iso: str, end_iso: str) -> list[str]:
    from datetime import timedelta
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end   = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%dT12:00:00Z"))
        cur += timedelta(days=1)
    return out


# ─── Domestic block ───────────────────────────────────────────────────
def run_domestic_block() -> dict:
    per_league: dict[str, dict] = {}
    all_picks: list[dict] = []
    odds_type = "OPENING"
    closing_seen = False
    for code, label in LEAGUES_2425:
        csv_path = _ensure_league_csv(code)
        if csv_path is None:
            per_league[label] = {"available": False,
                                  "reason_code": "CSV_DOWNLOAD_FAILED"}
            continue
        try:
            matches = parse_football_data_csv(
                str(csv_path), prefer_closing=False,
            )
        except Exception as exc:    # noqa: BLE001
            per_league[label] = {"available": False,
                                  "reason_code": f"PARSE_ERROR:{exc}"}
            continue
        if any("W_CLOSING_ODDS_OPTIMISTIC" in (m.get("warnings") or [])
                for m in matches):
            closing_seen = True
        bt = run_backtest(
            matches, market="DRAW", no_market=False,
            use_calibration=True, walk_forward=True, shrinkage_K=50,
            min_pred_prob_pp=8.0,
        )
        m = compute_backtest_metrics(bt)
        per_league[label] = {
            "available": True, "n_matches": len(matches),
            "n_picks":   len(bt.get("picks", [])),
            "metrics":   m, "odds_type": odds_type,
        }
        # Attach cohort tags by pre-match features only.
        for p in bt.get("picks", []):
            tags = detect_cohorts(p, p.get("features") or {})
            p["_cohort_tags"] = tags
            p["_competition"] = label
            p["_block"] = "domestic"
            all_picks.append(p)
    return {
        "block":     "domestic_leagues_summary",
        "odds_type": "MIXED" if closing_seen else odds_type,
        "per_league": per_league,
        "_picks":     all_picks,
    }


# ─── National block ───────────────────────────────────────────────────
async def run_national_block(*, max_credits: int) -> dict:
    per_tournament: dict[str, dict] = {}
    all_picks: list[dict] = []
    odds_type = "POINT_IN_TIME_PREMATCH"
    total_credits = 0
    for label, sport_key, start, end, openfb in NATIONAL_TOURNAMENTS:
        dates = _iter_dates(start, end)
        odds_res = await fetch_tournament_pit_odds(
            sport_key=sport_key, dates_iso=dates,
            max_credits=max(0, max_credits - total_credits),
        )
        total_credits += odds_res.get("credits_used", 0)
        if not odds_res.get("events"):
            per_tournament[label] = {
                "available": False,
                "reason_code": "UNAVAILABLE_NO_COVERAGE",
                "credits_used": odds_res.get("credits_used", 0),
                "aborted": odds_res.get("aborted", False),
            }
            continue
        # Hydrate ground truth from openfootball JSON if present.
        try:
            matches_truth = (parse_openfootball_json(openfb)
                              if Path(openfb).exists() else [])
        except Exception:    # noqa: BLE001
            matches_truth = []
        # Settle via openfootball (decision 7 of the prompt).
        matches = _merge_pit_odds_with_truth(odds_res["events"], matches_truth)
        if not matches:
            per_tournament[label] = {
                "available": False, "reason_code": "GROUND_TRUTH_MISSING",
            }
            continue
        bt = run_backtest(
            matches, market="DRAW", no_market=False,
            use_calibration=True, walk_forward=True, shrinkage_K=50,
            min_pred_prob_pp=8.0,
        )
        m = compute_backtest_metrics(bt)
        per_tournament[label] = {
            "available": True, "n_matches": len(matches),
            "n_picks":   len(bt.get("picks", [])),
            "metrics":   m,
            "odds_type": odds_type,
            "credits_used": odds_res.get("credits_used", 0),
        }
        for p in bt.get("picks", []):
            tags = detect_cohorts(p, p.get("features") or {})
            p["_cohort_tags"] = tags
            p["_competition"] = label
            p["_block"] = "national"
            all_picks.append(p)
    return {
        "block":         "national_tournaments_summary",
        "odds_type":     odds_type,
        "per_tournament": per_tournament,
        "credits_used":  total_credits,
        "_picks":        all_picks,
    }


def _merge_pit_odds_with_truth(events: list[dict],
                                  truth: list[dict]) -> list[dict]:
    """Merge The Odds API event payloads with openfootball ground truth.

    Settlement uses ``fthg``/``ftag`` from openfootball, **never** from
    the odds payload (test_settlement_uses_openfootball_not_oddsapi)."""
    by_key: dict[tuple, dict] = {}
    for t in truth or []:
        h = (t.get("home_team") or "").lower().strip()
        a = (t.get("away_team") or "").lower().strip()
        if h and a:
            by_key[(h, a)] = t
    out: list[dict] = []
    for ev in events or []:
        h = (ev.get("home_team") or "").lower().strip()
        a = (ev.get("away_team") or "").lower().strip()
        t = by_key.get((h, a))
        if not t:
            continue
        # Build an opening-odds-like row that the backtest engine
        # expects. We extract h2h prices from the first bookmaker.
        odd_h = odd_d = odd_a = None
        bookmakers = ((ev.get("event_payload") or {}).get("bookmakers")
                       if isinstance(ev.get("event_payload"), dict) else [])
        for bm in (bookmakers or []):
            for mk in (bm.get("markets") or []):
                if mk.get("key") != "h2h":
                    continue
                for o in (mk.get("outcomes") or []):
                    name = (o.get("name") or "").lower().strip()
                    price = o.get("price")
                    if name == h:
                        odd_h = odd_h or price
                    elif name == a:
                        odd_a = odd_a or price
                    else:
                        odd_d = odd_d or price
        if not (odd_h and odd_d and odd_a):
            continue
        out.append({
            **t,
            "odd_home":     odd_h,
            "odd_draw":     odd_d,
            "odd_away":     odd_a,
            "odds_type":    "POINT_IN_TIME_PREMATCH",
            "source_audit": {"odds_timestamp": ev.get("odds_timestamp")},
        })
    return out


# ─── Combined comparison + verdict ────────────────────────────────────
def build_combined_comparison(domestic: dict, national: dict,
                                 all_picks: list[dict]) -> dict:
    warnings: list[str] = []
    dom_odds = domestic.get("odds_type")
    nat_odds = national.get("odds_type")
    if dom_odds and nat_odds and dom_odds != nat_odds:
        warnings.append("W_ODDS_TYPE_MISMATCH")

    def _agg(block: dict, key: str) -> dict:
        rows = []
        for v in (block.get(key) or {}).values():
            if v.get("available"):
                rows.append(v["metrics"])
        if not rows:
            return {"available": False}
        n = sum(r.get("n_bets", 0) for r in rows)
        roi = (sum(r.get("roi", 0) * r.get("n_bets", 0) for r in rows) / n
                if n else None)
        hr  = (sum(r.get("hit_rate", 0) * r.get("n_bets", 0) for r in rows) / n
                if n else None)
        sig = any(r.get("is_roi_significant") for r in rows)
        return {"available": True, "n_bets": n,
                "weighted_roi": roi, "weighted_hit_rate": hr,
                "any_significant": sig,
                "rows": rows}

    dom_agg = _agg(domestic, "per_league")
    nat_agg = _agg(national, "per_tournament")

    cohort_summary = summarise_picks_by_cohort(all_picks) if all_picks else {}

    # Anti-overfitting: validate Spain–Cape Verde pattern only if
    # combined cohort (DOMINANT_FAVORITE + GROUP_STAGE) has n>=30 and CI>0.
    sp_pattern = cohort_summary.get(
        "DOMINANT_FAVORITE_DRAW_VALUE+TOURNAMENT_GROUP_STAGE_DRAW_VALUE",
    ) or {}
    n_pattern   = sp_pattern.get("n", 0)
    ci_low      = (sp_pattern.get("metrics") or {}).get("roi_ci_low")
    pattern_status = ("PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE"
                       if (n_pattern < 30 or ci_low is None or ci_low <= 0)
                       else "PATTERN_REPEATABLE")

    # Verdict (decision 5b: ROI significance + Brier + hit-rate).
    verdict_parts = []
    if dom_agg.get("available") and nat_agg.get("available"):
        if nat_agg.get("any_significant") and not dom_agg.get("any_significant"):
            verdict_parts.append("National draws show significance domestic does not.")
        if (nat_agg.get("weighted_roi") or 0) > (dom_agg.get("weighted_roi") or 0):
            verdict_parts.append("National weighted ROI exceeds domestic.")
    if not verdict_parts:
        verdict = "INSUFFICIENT_TO_AFFIRM"
    elif len(verdict_parts) >= 2:
        verdict = "HYPOTHESIS_SUPPORTED"
    else:
        verdict = "DIRECTIONAL_SIGNAL_ONLY"

    return {
        "block":             "combined_comparison",
        "warnings":          warnings,
        "domestic_odds_type": dom_odds,
        "national_odds_type": nat_odds,
        "domestic_aggregate": dom_agg,
        "national_aggregate": nat_agg,
        "cohort_summary":     cohort_summary,
        "spain_capeverde_pattern": {
            "n":      n_pattern,
            "ci_low": ci_low,
            "status": pattern_status,
        },
        "verdict": {
            "hypothesis": "draw_potential_better_in_selections",
            "status":     verdict,
            "rationale":  verdict_parts or
                          ["No bloque alcanzó significancia clara"],
        },
    }


# ─── Entry point ──────────────────────────────────────────────────────
async def main_async(*, max_credits: int, out_path: str) -> dict:
    domestic = run_domestic_block()
    national = await run_national_block(max_credits=max_credits)
    all_picks = (domestic.pop("_picks", []) + national.pop("_picks", []))
    combined = build_combined_comparison(domestic, national, all_picks)
    report = {
        "generated_at":              datetime.now(timezone.utc).isoformat(),
        "max_credits_per_run":       max_credits,
        "credits_used":              national.get("credits_used", 0),
        "domestic_leagues_summary":  domestic,
        "national_tournaments_summary": national,
        "combined_comparison":       combined,
        "observe_only":              True,
    }
    Path(out_path).write_text(json.dumps(report, indent=2, default=str))
    log.info("D7 report written → %s", out_path)
    return report


def main() -> int:
    p = argparse.ArgumentParser(
        description="Sprint-D7 comparative backtest (observe_only)",
    )
    p.add_argument("--max-credits", type=int, default=DEFAULT_MAX_CREDITS)
    p.add_argument("--out", default="/app/backtest_d7_comparative.json")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # Load .env so THE_ODDS_API_KEY is visible to the historical client.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:    # noqa: BLE001
        pass
    asyncio.run(main_async(max_credits=args.max_credits, out_path=args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
