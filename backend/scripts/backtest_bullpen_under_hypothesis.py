#!/usr/bin/env python3
"""Bullpen-Under Hypothesis Backtest — MLB only, observe-only.

Phase 43 / Backtest.

Question being asked:
  Does a vulnerable bullpen alone empeora los Under, or does it only
  hurt when COMBINED with offensive traffic / HR risk / base pressure?

How it works:
  * Reads finished MLB matches from mongo (``matches`` + the per-game
    intelligence snapshot in ``mlb_game_intelligence_snapshots``).
  * For each match, reconstructs the PRE-MATCH snapshot strictly from
    the data already persisted at pick-generation time. NO post-match
    derived metrics enter the cohort logic. The only post-match field
    we touch is the final score (used for settlement).
  * Assigns each match to cohort A / B / A1 / A2 per the spec.
  * Aggregates the requested metrics + an ``analysis`` block.

CLI:
    python scripts/backtest_bullpen_under_hypothesis.py --days 90

Output:
    /app/backend/scripts/out/bullpen_under_backtest_<ts>.json
    plus a human-readable summary printed to stdout.

This is OBSERVE-ONLY by design. It writes a telemetry row to
``backtest_telemetry`` collection for future calibration but does NOT
alter live recommendations.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean

# Ensure `/app/backend` is on sys.path so we can import `services.*`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",  "test_database")

OUTPUT_DIR = ROOT / "scripts" / "out"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# Cohort definitions  (per spec)
# ─────────────────────────────────────────────────────────────────────
COHORT_THRESHOLDS = {
    "expected_runs_below_line":   True,    # Expected Runs < market line
    "bullpen_era_high":            5.50,   # Cohort A
    "bullpen_era_normal_max":      4.50,   # Cohort B
    "script_survival_low":         60,
    "offensive_explosion_high":    50,
}


def _safe_float(v, default=None):
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:                       # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def _walk(d: dict, *paths: str):
    """Pull the first available value from a list of dotted paths."""
    for p in paths:
        cur = d
        ok = True
        for token in p.split("."):
            if isinstance(cur, dict) and token in cur:
                cur = cur[token]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return None


# ─────────────────────────────────────────────────────────────────────
# Snapshot reconstruction
# ─────────────────────────────────────────────────────────────────────
def reconstruct_prematch(snapshot: dict, match: dict) -> dict:
    """Pull only pre-match signals from the persisted snapshot.

    All field lookups are defensive — different pick generations have
    written slightly different paths over time. We never call into a
    live-derived field.
    """
    payload = snapshot.get("pick_payload") or snapshot.get("digest") or snapshot
    saber   = payload.get("sabermetrics") or {}

    bullpen_era_7d = _safe_float(_walk(
        payload, "bullpen_era_7d",
        "advanced_stats_snapshot.bullpen_era_7d",
        "sabermetrics.bullpen_era_7d",
        "advanced_adjustments.bullpen_era_7d",
        "pipeline_meta.bullpen_era_7d",
    ))
    bullpen_whip_7d = _safe_float(_walk(
        payload, "bullpen_whip_7d",
        "advanced_stats_snapshot.bullpen_whip_7d",
        "sabermetrics.bullpen_whip_7d",
        "pipeline_meta.bullpen_whip_7d",
    ))
    expected_runs = _safe_float(_walk(
        payload, "expected_runs", "model_verification.expected_runs",
        "sabermetrics.expected_runs", "advanced_stats_snapshot.expected_runs",
    ))
    market_line = _safe_float(_walk(
        payload, "market_line",
        "market_selection.line", "recommendation.line",
    ))
    recommended_pick = _walk(
        payload, "recommendation.selection", "recommendation.market",
        "market_selection.selection", "market_selection.market",
    )
    script_survival      = _safe_float(payload.get("script_survival") or saber.get("script_survival"))
    offensive_explosion  = _safe_float(_walk(
        payload, "offensive_explosion", "pipeline_meta.offensive_explosion",
        "sabermetrics.offensive_explosion",
    ))
    fragility            = _safe_float(payload.get("fragility"))
    # Optional traffic signals: HR risk, base pressure.
    hr_risk          = _safe_float(_walk(payload, "hr_risk", "advanced_stats_snapshot.hr_risk"))
    pressure_base    = _safe_float(payload.get("pressure_base"))

    return {
        "game_pk":               snapshot.get("game_pk") or match.get("game_pk") or match.get("match_id"),
        "match_id":              snapshot.get("match_id") or match.get("match_id"),
        "match_date":            snapshot.get("created_at") or match.get("match_date"),
        "expected_runs":         expected_runs,
        "market_line":           market_line,
        "recommended_pick":      recommended_pick,
        "bullpen_era_7d":        bullpen_era_7d,
        "bullpen_whip_7d":       bullpen_whip_7d,
        "script_survival":       script_survival,
        "offensive_explosion":   offensive_explosion,
        "fragility":             fragility,
        "hr_risk":               hr_risk,
        "pressure_base":         pressure_base,
        # Final settlement (only post-match field allowed).
        "total_runs_final":      _safe_float(_walk(match, "final_score.total",
                                                  "final_score.home", "final_score.away")) or None,
    }


def _is_under_pick(text):
    return bool(text) and "under" in str(text).lower()


def assign_cohort(s: dict) -> str | None:
    """A / B / None per the spec rules. Sub-cohorts handled separately."""
    if not _is_under_pick(s.get("recommended_pick")):
        return None
    if s["expected_runs"] is None or s["market_line"] is None:
        return None
    if not (s["expected_runs"] < s["market_line"]):
        return None
    if s["script_survival"] is None or s["script_survival"] >= COHORT_THRESHOLDS["script_survival_low"]:
        return None
    if s["offensive_explosion"] is None or s["offensive_explosion"] < COHORT_THRESHOLDS["offensive_explosion_high"]:
        return None
    bp = s["bullpen_era_7d"]
    if bp is None:
        return None
    if bp > COHORT_THRESHOLDS["bullpen_era_high"]:
        return "A"
    if bp < COHORT_THRESHOLDS["bullpen_era_normal_max"]:
        return "B"
    return None   # gap zone 4.50–5.50 excluded for clean separation.


def assign_sub_cohort(s: dict, cohort: str) -> str | None:
    """Sub-cohorts A1 / A2 by offensive-traffic signals."""
    if cohort != "A":
        return None
    traffic_signals = [s.get("offensive_explosion"), s.get("hr_risk"), s.get("pressure_base")]
    has_traffic = any((v is not None and v >= 50) for v in traffic_signals[:1])
    has_traffic = has_traffic or (s.get("hr_risk")       is not None and s.get("hr_risk")       >= 50)
    has_traffic = has_traffic or (s.get("pressure_base") is not None and s.get("pressure_base") >= 50)
    return "A2" if has_traffic else "A1"


# ─────────────────────────────────────────────────────────────────────
# Settlement + metrics
# ─────────────────────────────────────────────────────────────────────
def settle_under(line: float, final_runs: float) -> tuple[str, float]:
    """Returns (outcome, pnl_unit) for a 1-unit Under bet.

    Assumes flat odds of 1.91 (-110) for the ROI math; the caller can
    substitute real closing odds later.
    """
    if line is None or final_runs is None:
        return ("void", 0.0)
    if abs(final_runs - line) < 1e-9:
        return ("push", 0.0)
    if final_runs < line:
        return ("won", 0.91)
    return ("lost", -1.0)


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"sample_size": 0}
    n = len(rows)
    hits = sum(1 for r in rows if r["outcome"] == "won")
    pushes = sum(1 for r in rows if r["outcome"] == "push")
    voids  = sum(1 for r in rows if r["outcome"] == "void")
    losses = sum(1 for r in rows if r["outcome"] == "lost")
    countable = n - pushes - voids
    hit_rate = (hits / countable) if countable else 0.0
    roi = sum(r["pnl"] for r in rows) / (n - voids) if (n - voids) else 0.0

    def _avg(field):
        vals = [r["snapshot"].get(field) for r in rows]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(fmean(vals), 3) if vals else None

    actuals = [r["snapshot"].get("total_runs_final") for r in rows]
    expecteds = [r["snapshot"].get("expected_runs") for r in rows]
    errors = []
    for a, e in zip(actuals, expecteds):
        if isinstance(a, (int, float)) and isinstance(e, (int, float)):
            errors.append(a - e)

    return {
        "sample_size":            n,
        "under_hit_rate":         round(hit_rate, 4),
        "roi":                    round(roi, 4),
        "push_rate":              round(pushes / n, 4),
        "void_rate":              round(voids / n, 4),
        "loss_rate":              round(losses / countable, 4) if countable else 0.0,
        "wins":                   hits,
        "losses":                 losses,
        "pushes":                 pushes,
        "voids":                  voids,
        "average_actual_runs":    _avg("total_runs_final"),
        "average_expected_runs":  _avg("expected_runs"),
        "average_error":          round(fmean(errors), 3) if errors else None,
        "average_fragility":      _avg("fragility"),
        "average_script_survival": _avg("script_survival"),
        "average_offensive_explosion": _avg("offensive_explosion"),
        "average_bullpen_era_7d": _avg("bullpen_era_7d"),
    }


# ─────────────────────────────────────────────────────────────────────
# Main async pipeline
# ─────────────────────────────────────────────────────────────────────
async def run_backtest(days: int):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Pull finished MLB matches. Production sets `match_ended:true` but
    # legacy/manual rows just have `final_score`. Accept both so the
    # backtest works against any data source.
    matches_q = {
        "sport": {"$in": ["mlb", "baseball"]},
        "$and": [
            {"$or": [
                {"match_ended":   True},
                {"final_score":   {"$exists": True, "$ne": None}},
            ]},
            {"$or": [
                {"match_date":  {"$gte": cutoff.isoformat()}},
                {"kickoff_iso": {"$gte": cutoff.isoformat()}},
                {"settled_at":  {"$gte": cutoff.isoformat()}},
                {"match_date":  {"$exists": False}},   # no date = include
            ]},
        ],
    }
    cohort_rows: dict[str, list[dict]] = {"A": [], "B": [], "A1": [], "A2": []}
    scanned = 0
    skipped_no_snap = 0

    async for match in db.matches.find(matches_q):
        scanned += 1
        gpk = match.get("game_pk") or match.get("match_id")
        snap = await db.mlb_game_intelligence_snapshots.find_one({
            "$or": [{"game_pk": str(gpk)}, {"match_id": str(gpk)},
                    {"game_pk": gpk},      {"match_id": gpk}],
        })
        if not snap:
            skipped_no_snap += 1
            continue
        s = reconstruct_prematch(snap, match)
        cohort = assign_cohort(s)
        if cohort is None:
            continue
        outcome, pnl = settle_under(s["market_line"], s["total_runs_final"])
        row = {"snapshot": s, "outcome": outcome, "pnl": pnl, "cohort": cohort}
        cohort_rows[cohort].append(row)
        sub = assign_sub_cohort(s, cohort)
        if sub:
            cohort_rows[sub].append(row)

    report = {
        "engine_version": "bullpen_under_backtest.1",
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "params":         {"days": days, "thresholds": COHORT_THRESHOLDS},
        "scanned_matches": scanned,
        "skipped_no_snapshot": skipped_no_snap,
        "cohorts": {
            "A":  {"name": "Bullpen Vulnerable",
                   "rules": "Under pick + ExpRuns<Line + BullpenERA7d>5.50 + ScriptSurvival<60 + OffExplosion≥50",
                   "metrics": aggregate(cohort_rows["A"])},
            "B":  {"name": "Bullpen Normal",
                   "rules": "Same as A but BullpenERA7d<4.50",
                   "metrics": aggregate(cohort_rows["B"])},
            "A1": {"name": "A — Sin tráfico ofensivo",
                   "rules": "Cohort A + (HR risk<50 AND pressure_base<50)",
                   "metrics": aggregate(cohort_rows["A1"])},
            "A2": {"name": "A — Con tráfico ofensivo",
                   "rules": "Cohort A + (HR risk≥50 OR pressure_base≥50)",
                   "metrics": aggregate(cohort_rows["A2"])},
        },
        "analysis": _analyze(cohort_rows),
        "observe_only": True,
    }

    # Telemetry persistence — observe-only row.
    try:
        await db.backtest_telemetry.insert_one({
            "telemetry_id":   str(uuid.uuid4()),
            "kind":           "bullpen_under_hypothesis",
            "report":         report,
            "created_at":     datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        report["telemetry_warning"] = f"persist failed: {exc}"

    # Persist JSON to disk for easy sharing.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUTPUT_DIR / f"bullpen_under_backtest_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))

    _print_human_summary(report, out_path)
    client.close()
    return report


def _analyze(cohort_rows):
    """Generate the spec's 'observe-only' takeaways."""
    a = aggregate(cohort_rows["A"])
    b = aggregate(cohort_rows["B"])
    a1 = aggregate(cohort_rows["A1"])
    a2 = aggregate(cohort_rows["A2"])
    notes = []
    if a.get("sample_size", 0) >= 10 and b.get("sample_size", 0) >= 10:
        delta_hit = (a.get("under_hit_rate") or 0) - (b.get("under_hit_rate") or 0)
        notes.append({
            "metric": "under_hit_rate_delta_A_vs_B",
            "value":  round(delta_hit, 4),
            "es":     f"Cohorte A (bullpen vulnerable) tuvo {a['under_hit_rate']:.0%} de Under hits "
                      f"vs {b['under_hit_rate']:.0%} en B ({delta_hit:+.0%} delta).",
        })
    if a1.get("sample_size", 0) >= 5 and a2.get("sample_size", 0) >= 5:
        delta = (a2.get("under_hit_rate") or 0) - (a1.get("under_hit_rate") or 0)
        notes.append({
            "metric": "under_hit_rate_A1_vs_A2",
            "value":  round(delta, 4),
            "es":     f"Sub-cohorte A2 (bullpen + tráfico) tuvo {a2['under_hit_rate']:.0%} hits "
                      f"vs {a1['under_hit_rate']:.0%} en A1 (sin tráfico). Delta {delta:+.0%}.",
        })
    if not notes:
        notes.append({"metric": "insufficient_data", "value": None,
                      "es": "Muestras insuficientes para conclusión sólida."})
    return notes


def _print_human_summary(report: dict, out_path: Path):
    print("\n=== BULLPEN UNDER HYPOTHESIS — BACKTEST ===")
    print(f"days_window:      {report['params']['days']}")
    print(f"scanned_matches:  {report['scanned_matches']}  "
          f"(skipped {report['skipped_no_snapshot']} without snapshot)")
    for code, block in report["cohorts"].items():
        m = block["metrics"]
        n = m.get("sample_size", 0)
        if n == 0:
            print(f"\n[{code}] {block['name']} — sin muestras")
            continue
        print(f"\n[{code}] {block['name']} (n={n})")
        print(f"  Under hit rate: {m.get('under_hit_rate'):.2%}")
        print(f"  ROI:            {m.get('roi'):+.2%}")
        print(f"  Push rate:      {m.get('push_rate'):.2%}    Void rate: {m.get('void_rate'):.2%}")
        print(f"  Avg actual:     {m.get('average_actual_runs')}")
        print(f"  Avg expected:   {m.get('average_expected_runs')}")
        print(f"  Avg error:      {m.get('average_error')}")
        print(f"  Avg fragility:  {m.get('average_fragility')}")
    print("\n--- ANALYSIS ---")
    for n in report["analysis"]:
        print(f"  • {n['es']}")
    print(f"\nReport saved → {out_path}")
    print("observe_only=True (no live recommendations were modified).")


# ─────────────────────────────────────────────────────────────────────
# CLI entry
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90,
                        help="window in days (default 90)")
    args = parser.parse_args()
    asyncio.run(run_backtest(args.days))


if __name__ == "__main__":
    main()
