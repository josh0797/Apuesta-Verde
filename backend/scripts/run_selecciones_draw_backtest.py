"""Sprint-D8-Fase2 · DRAW + DOMINANT_FAVORITE cohort backtest on selecciones.

Orquesta los 3 torneos (WC2022, Euro2024, Copa América 2024) con:
  1. Verificación FREE de sport_keys disponibles.
  2. Dry-run de costo estimado (n_matches × 11 créditos worst-case).
  3. Aborto si el estimado excede ``MAX_CREDITS`` (default 2500).
  4. Ingesta tournament-by-tournament con tope duro por torneo y
     global (CreditTracker abort-on-exhausted).
  5. Análisis D9 (AUC, Brier, calibración vs devig) sobre los DRAW
     records + análisis por cohorte (DOMINANT_FAVORITE, GROUP_STAGE).
  6. Cross-tab vs el desempeño en LIGAS (reusa diagnósticos cacheados).
  7. Veredicto Bonferroni: REAL / PATTERN_NOT_YET_PROVEN / CLOSED.

Usage:
  python scripts/run_selecciones_draw_backtest.py --max-credits 2500
  python scripts/run_selecciones_draw_backtest.py --dry-run-only

Output: /app/diagnostics/sprint_d8_fase2_selecciones_draw_backtest.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone, date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load backend/.env so THE_ODDS_API_KEY (and friends) are available
# when running the script directly from CLI.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    # Fallback: parse the .env manually.
    _env_path = Path(__file__).resolve().parents[1] / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from services.football_calibration_diagnostics import (  # noqa: E402
    compute_calibration_diagnostics,
    classify_model_quality,
)
from services.football_cohort_detector import (         # noqa: E402
    COHORT_DOMINANT_FAVORITE,
    COHORT_TOURNAMENT_GROUP,
    detect_cohorts,
    summarise_picks_by_cohort,
)
from services.football_selecciones_ingestor import (    # noqa: E402
    ingest_tournament,
)
from services.theoddsapi_historical_client import (     # noqa: E402
    fetch_tournament_pit_odds,
    verify_sport_keys_available,
    estimate_credit_cost,
    DEFAULT_MAX_CREDITS,
    RC_TOKEN_MISSING,
)

log = logging.getLogger("d8_fase2_selecciones")

# ─────────────────────────────────────────────────────────────────────
# Tournament catalog
# ─────────────────────────────────────────────────────────────────────
TOURNAMENTS = [
    {
        "name":         "wc2022",
        "title":        "FIFA World Cup 2022",
        "sport_key":    "soccer_fifa_world_cup",
        "openfootball": "/app/data/openfootball/wc2022.json",
        "fifa_key":     "wc2022",
        "date_start":   "2022-11-20",
        "date_end":     "2022-12-18",
    },
    {
        "name":         "euro2024",
        "title":        "UEFA Euro 2024",
        "sport_key":    "soccer_uefa_european_championship",
        "openfootball": "/app/data/openfootball/euro2024.json",
        "fifa_key":     "euro2024",
        "date_start":   "2024-06-14",
        "date_end":     "2024-07-14",
    },
    {
        "name":         "copa_america_2024",
        "title":        "Copa América 2024",
        "sport_key":    "soccer_conmebol_copa_america",
        "openfootball": "/app/data/openfootball/copa_america_2024.json",
        "fifa_key":     "copa_america_2024",
        "date_start":   "2024-06-20",
        "date_end":     "2024-07-14",
    },
]

DEFAULT_OUT      = Path("/app/diagnostics/sprint_d8_fase2_selecciones_draw_backtest.json")
LEAGUES_CACHE    = Path("/app/diagnostics/calibration_draw_premier_multiseason.json")

# ─────────────────────────────────────────────────────────────────────
# Bonferroni verdict
# ─────────────────────────────────────────────────────────────────────
def _bonferroni_z(alpha_per_test: float = 0.01) -> float:
    """Approximate one-sided z-score for ~99% CI (Bonferroni-corrected
    for a single-claim test inside a sequence of 6 tests).
    """
    # alpha_per_test=0.01 → two-sided z≈2.576. For one-sided ~2.33.
    return 2.576


def _final_verdict(diagnostics: dict,
                    dom_cohort: dict | None) -> dict:
    """Implements the user-defined Bonferroni rubric:
       REAL only if AUC>0.55, delta_brier_vs_devig<0, and
       DOMINANT_FAVORITE cohort: n≥30 with roi_ci_low>0 at ~99%.
    """
    auc = ((diagnostics or {}).get("discrimination") or {}).get("auc_model")
    delta_brier = (((diagnostics or {}).get("model_vs_market") or {})
                   .get("delta_brier_vs_devig"))

    tags: list[str] = []
    auc_ok    = auc is not None and auc > 0.55
    edge_ok   = delta_brier is not None and delta_brier < 0
    dom_n     = (dom_cohort or {}).get("n", 0)
    dom_roi   = (dom_cohort or {}).get("roi")
    dom_ci_lo = (dom_cohort or {}).get("roi_ci_low")
    sample_ok = dom_n >= 30 and dom_ci_lo is not None and dom_ci_lo > 0

    if not auc_ok:
        tags.append("AUC_MODEL_NOT_ABOVE_RANDOM")
    if not edge_ok:
        tags.append("MODEL_DOES_NOT_BEAT_MARKET_DEVIG")
    if dom_n < 30:
        tags.append("PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE")
    elif not sample_ok:
        tags.append("ROI_CI_LOW_NOT_POSITIVE_AT_99")

    if auc_ok and edge_ok and sample_ok:
        verdict = "PATTERN_CONFIRMED_REAL"
        tags.append("HYPOTHESIS_CONFIRMED")
    elif (not auc_ok) and (not edge_ok):
        verdict = "CLOSED_SAME_AS_LIGAS"
        tags.append("HYPOTHESIS_REFUTED")
    elif dom_n < 30:
        verdict = "PATTERN_NOT_YET_PROVEN_INSUFFICIENT_SAMPLE"
        tags.append("HYPOTHESIS_SUGGESTIVE_BUT_NOT_PROVEN")
    else:
        verdict = "PARTIAL_SIGNAL_NOT_ENOUGH_EVIDENCE"
        tags.append("HYPOTHESIS_PARTIALLY_SUPPORTED")

    return {
        "verdict":      verdict,
        "tags":         tags,
        "auc_model":    auc,
        "delta_brier_vs_devig": delta_brier,
        "dominant_favorite_n":         dom_n,
        "dominant_favorite_roi":       dom_roi,
        "dominant_favorite_roi_ci_low": dom_ci_lo,
        "rubric": {
            "auc_threshold":     0.55,
            "delta_brier_target": "< 0",
            "min_n":             30,
            "ci_alpha":          0.01,
            "ci_level":          "~99% (Bonferroni-aware: 6th test in sequence)",
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Dates iterator
# ─────────────────────────────────────────────────────────────────────
def _daterange(start_iso: str, end_iso: str) -> list[str]:
    s = date_cls.fromisoformat(start_iso)
    e = date_cls.fromisoformat(end_iso)
    out = []
    cur = s
    while cur <= e:
        out.append(cur.isoformat() + "T00:00:00Z")
        cur += timedelta(days=1)
    return out


# ─────────────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────────────
def _load_openfootball(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    doc = json.loads(p.read_text(encoding="utf-8"))
    return doc.get("matches", []) or []


def _load_fifa(fifa_key: str) -> dict:
    p = Path("/app/data/fifa_ranking/team_points_by_tournament.json")
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return ((doc.get("tournaments") or {}).get(fifa_key) or {}).get("points") or {}


def _leagues_dominant_favorite() -> dict:
    """Reuse cached league diagnostics for cross-tab.

    The Premier multiseason draw diagnostics (D7) already include
    per-cohort summaries. We extract the DOMINANT_FAVORITE row if
    available; otherwise mark UNAVAILABLE.
    """
    if not LEAGUES_CACHE.exists():
        return {"available": False, "reason": "leagues cache missing"}
    try:
        doc = json.loads(LEAGUES_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"cache read error: {exc}"}
    cohorts_blk = (doc.get("cohorts") or {}).get("per_cohort") or {}
    dom = cohorts_blk.get(COHORT_DOMINANT_FAVORITE)
    if not dom:
        return {"available": False, "reason": "no DOMINANT_FAVORITE row in leagues cache",
                "leagues_disc": (doc.get("discrimination") or {}).get("auc_model")}
    return {
        "available": True,
        "source":    str(LEAGUES_CACHE),
        "n":         dom.get("n"),
        "hit_rate":  dom.get("hit_rate"),
        "roi":       dom.get("roi"),
        "roi_ci_low":  dom.get("roi_ci_low"),
        "roi_ci_high": dom.get("roi_ci_high"),
    }


# ─────────────────────────────────────────────────────────────────────
# Main async flow
# ─────────────────────────────────────────────────────────────────────
async def _run_async(args: argparse.Namespace) -> dict:
    started = datetime.now(timezone.utc).isoformat()

    # Token check.
    has_key = bool(os.environ.get("THE_ODDS_API_KEY")
                    or os.environ.get("ODDS_API_KEY"))
    out: dict = {
        "_meta": {
            "sprint":          "D8-FASE2",
            "started_at_utc":  started,
            "max_credits":     args.max_credits,
            "dry_run_only":    bool(args.dry_run_only),
            "has_api_key":     has_key,
        },
        "preflight":   {},
        "ingestion":   {},
        "diagnostics": {},
        "cohorts":     {},
        "cross_tab":   {},
        "verdict":     {},
    }

    # PRE-1: estimate dry-run cost based on openfootball ground truth.
    dry: dict = {"per_tournament": [], "total_estimate_floor": 0,
                  "total_estimate_ceiling": 0}
    for t in TOURNAMENTS:
        matches = _load_openfootball(t["openfootball"])
        est = estimate_credit_cost(n_matches=len(matches))
        dry["per_tournament"].append({
            "name":               t["name"],
            "openfootball_n":     len(matches),
            "est_credits_floor":  est["est_credits_floor"],
            "est_credits_ceiling": est["est_credits_ceiling"],
        })
        dry["total_estimate_floor"]   += est["est_credits_floor"]
        dry["total_estimate_ceiling"] += est["est_credits_ceiling"]
    out["preflight"]["dry_run"] = dry

    # PRE-2: verify sport_keys (free, /v4/sports?all=true).
    if has_key:
        sport_keys = [t["sport_key"] for t in TOURNAMENTS]
        ver = await verify_sport_keys_available(sport_keys)
        out["preflight"]["sport_keys_verification"] = ver
        valid_keys = set(ver.get("valid_keys") or [])
    else:
        out["preflight"]["sport_keys_verification"] = {
            "available": False, "reason_code": RC_TOKEN_MISSING,
        }
        valid_keys = set()

    # Abort guard: estimate > tope → abort.
    if dry["total_estimate_ceiling"] > args.max_credits:
        out["_meta"]["aborted_in_preflight"] = True
        out["_meta"]["abort_reason"] = (
            f"Dry-run ceiling ({dry['total_estimate_ceiling']}) exceeds "
            f"MAX_CREDITS ({args.max_credits}). Aborting before any "
            f"credits are spent."
        )

    if args.dry_run_only or out["_meta"].get("aborted_in_preflight") or not has_key:
        if not has_key:
            out["_meta"]["abort_reason"] = (
                "THE_ODDS_API_KEY missing. Backtest not executed against "
                "real credits. The infrastructure is in place; provision "
                "the key in the backend .env and re-run."
            )
        return out

    # INGESTION per tournament with hard cap.
    credits_remaining = args.max_credits
    all_records: list[dict] = []
    all_picks:   list[dict] = []
    all_features: list[dict] = []
    per_tournament_summary: list[dict] = []

    for t in TOURNAMENTS:
        if credits_remaining <= 100:
            log.warning("Skipping %s: only %d credits remaining",
                        t["name"], credits_remaining)
            per_tournament_summary.append({
                "name":           t["name"],
                "skipped":        True,
                "reason":         "INSUFFICIENT_CREDITS_REMAINING",
            })
            continue
        if t["sport_key"] not in valid_keys:
            per_tournament_summary.append({
                "name":     t["name"],
                "skipped":  True,
                "reason":   "SPORT_KEY_UNAVAILABLE_NO_COVERAGE",
            })
            continue

        of_matches = _load_openfootball(t["openfootball"])
        fifa_pts   = _load_fifa(t["fifa_key"])
        dates      = _daterange(t["date_start"], t["date_end"])

        log.info("Ingesting %s (sport_key=%s, dates=%d, credits_rem=%d)",
                 t["name"], t["sport_key"], len(dates), credits_remaining)
        run = await ingest_tournament(
            sport_key=           t["sport_key"],
            dates_iso=           dates,
            openfootball_matches=of_matches,
            fifa_points=         fifa_pts,
            tournament_name=     t["name"],
            max_credits=         credits_remaining,
            fetch_tournament_pit_odds_fn=fetch_tournament_pit_odds,
        )
        credits_remaining -= run.get("credits_used", 0)
        per_tournament_summary.append({
            "name":               t["name"],
            "credits_used":       run.get("credits_used"),
            "n_matches_in_odds_run": run.get("n_matches_in_odds_run"),
            "n_matches_resolved":  run.get("n_matches_resolved"),
            "n_records_built":     run.get("n_records_built"),
            "aborted":             run.get("aborted"),
            "reason_codes":        run.get("reason_codes"),
        })
        all_records.extend(run.get("records", []))
        all_picks.extend(run.get("picks", []))
        all_features.extend(run.get("features", []))

    out["ingestion"]["per_tournament"] = per_tournament_summary
    out["ingestion"]["credits_used_total"]      = (args.max_credits - credits_remaining)
    out["ingestion"]["credits_remaining"]       = credits_remaining
    out["ingestion"]["n_records_total"]         = len(all_records)

    if not all_records:
        out["_meta"]["abort_reason"] = "no records built — see per_tournament reason_codes"
        return out

    # D9 calibration diagnostics.
    diag = compute_calibration_diagnostics(all_records, market="DRAW")
    quality = classify_model_quality(diag)
    out["diagnostics"] = diag
    out["diagnostics"]["quality_class"] = quality

    # Cohort breakdown — full sample (winners AND losers).
    coh = summarise_picks_by_cohort(all_picks,
                                      features_by_index=all_features)
    out["cohorts"] = coh

    # Cross-tab vs ligas.
    out["cross_tab"]["dominant_favorite_leagues"] = _leagues_dominant_favorite()
    dom_cohort_selecciones = (coh.get("per_cohort") or {}).get(
        COHORT_DOMINANT_FAVORITE)
    out["cross_tab"]["dominant_favorite_selecciones"] = dom_cohort_selecciones

    # Final Bonferroni verdict.
    out["verdict"] = _final_verdict(diag, dom_cohort_selecciones)
    out["_meta"]["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-credits", type=int, default=2500,
                        help="Hard credit cap for the whole sprint (default 2500)")
    parser.add_argument("--dry-run-only", action="store_true",
                        help="Only run preflight + estimate, do not call odds")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    out = asyncio.run(_run_async(args))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str),
                         encoding="utf-8")

    print("=" * 72)
    print("Sprint-D8-Fase2 · Selecciones DRAW backtest")
    print("=" * 72)
    pre = out.get("preflight", {})
    dry = pre.get("dry_run", {})
    print("  Dry-run estimate:")
    for row in dry.get("per_tournament", []):
        print(f"    {row['name']:24} n={row['openfootball_n']:>3} "
              f"est_credits=[{row['est_credits_floor']:>4} .. {row['est_credits_ceiling']:>4}]")
    print(f"    TOTAL ceiling: {dry.get('total_estimate_ceiling')} credits")
    print(f"  MAX_CREDITS: {args.max_credits}")
    if "aborted_in_preflight" in out.get("_meta", {}):
        print(f"\n  ABORTED IN PREFLIGHT: {out['_meta'].get('abort_reason')}")
    elif "abort_reason" in out.get("_meta", {}):
        print(f"\n  ABORTED: {out['_meta']['abort_reason']}")
    else:
        ing = out.get("ingestion", {})
        ver = out.get("verdict", {})
        print(f"\n  Credits used:   {ing.get('credits_used_total')}")
        print(f"  Records built:  {ing.get('n_records_total')}")
        print(f"  AUC model:      {ver.get('auc_model')}")
        print(f"  Δ Brier vs devig: {ver.get('delta_brier_vs_devig')}")
        print(f"  DOMINANT_FAVORITE n: {ver.get('dominant_favorite_n')}, "
              f"ROI={ver.get('dominant_favorite_roi')}, "
              f"CI_low={ver.get('dominant_favorite_roi_ci_low')}")
        print(f"\n  VERDICT:      {ver.get('verdict')}")
        print(f"  TAGS:         {ver.get('tags')}")
    print(f"\n  Output: {out_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
