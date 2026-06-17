#!/usr/bin/env python3
"""Sprint-D5 · Combined runner for the multi-league + multi-tournament
DRAW backtest with cohort detection.

Outputs (all written under ``--out-dir``, default ``/app``):
    /app/backtest_d5_league_<key>.json            (per league)
    /app/backtest_d5_tournament_<slug>.json       (per tournament)
    /app/domestic_leagues_summary.md
    /app/national_tournaments_summary.md
    /app/combined_comparison.md
"""
import argparse, asyncio, json, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.football_historical_ingestor import (
    parse_footballdata_csv, parse_openfootball_json,
)
from services.football_backtest_engine import run_backtest
from services.football_backtest_metrics import compute_backtest_metrics
from services.football_cohort_detector import (
    summarise_picks_by_cohort, ALL_COHORTS,
    COHORT_DOMINANT_FAVORITE, COHORT_TOURNAMENT_GROUP,
    COHORT_LOW_GOAL_UNDERDOG, COHORT_TAIL_EDGE,
)


# ─── Datasets ──────────────────────────────────────────────────────────
LEAGUES = [
    ("EPL",        "Premier League 2024-25",   "/tmp/E0_2425.csv"),
    ("LaLiga",     "La Liga 2024-25",          "/tmp/SP1_2425.csv"),
    ("SerieA",     "Serie A 2024-25",          "/tmp/I1_2425.csv"),
    ("Bundesliga", "Bundesliga 2024-25",       "/tmp/D1_2425.csv"),
    ("Ligue1",     "Ligue 1 2024-25",          "/tmp/F1_2425.csv"),
]
TOURNAMENTS = [
    ("WC2018",   "World Cup 2018", "/tmp/worldcup_2018.json"),
    ("WC2022",   "World Cup 2022", "/tmp/worldcup.json"),
    ("Euro2024", "Euro 2024",      "/tmp/euro.json"),
]


# ─── Helpers ───────────────────────────────────────────────────────────
def _run_league(key, name, path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        text = fh.read()
    matches = parse_footballdata_csv(text, competition=name)
    if not matches:
        return None
    r = run_backtest(matches, market="DRAW", use_calibration=True,
                      walk_forward=True, min_edge_pp=4.0)
    m = compute_backtest_metrics(r)
    # Cohort breakdown on the fired picks.
    feats = [p.get("_features") for p in r["picks"]]
    m["cohorts"] = summarise_picks_by_cohort(r["picks"], feats)
    m["_picks_sample"] = (r["picks"] or [])[:50]
    m["league_key"] = key
    m["competition"] = name
    return m


def _run_tournament(key, name, path):
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        data = json.load(fh)
    matches = parse_openfootball_json(data, competition=name)
    if not matches:
        return None
    r = run_backtest(matches, market="DRAW", no_market=True,
                      use_calibration=True, walk_forward=True,
                      min_pred_prob_pp=28.0)
    m = compute_backtest_metrics(r)
    feats = [p.get("_features") for p in r["picks"]]
    m["cohorts"] = summarise_picks_by_cohort(r["picks"], feats)
    m["_picks_sample"] = (r["picks"] or [])[:50]
    m["tournament_key"] = key
    m["competition"] = name
    return m


def _fmt(x, dec=4):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{dec}f}"
    return str(x)


def _cohort_table(cohorts: dict) -> list[str]:
    out = ["| Cohorte | n | won | hit_rate |", "|---|---:|---:|---:|"]
    for c in ALL_COHORTS:
        row = cohorts.get(c, {})
        out.append(
            f"| {c} | {row.get('n', 0)} | {row.get('won', 0)} "
            f"| {_fmt(row.get('hit_rate'), 4)} |"
        )
    return out


def _cohort_examples_table(cohorts: dict, c: str) -> list[str]:
    rows = (cohorts.get(c) or {}).get("examples") or []
    if not rows:
        return [f"_(sin ejemplos en `{c}`)_", ""]
    out = [f"**{c}** — top {len(rows)} ejemplos:",
           "", "| Date | Comp | Match | pred | mkt | edge_pp | odd | hit | score |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        match = f"{r.get('home','')} vs {r.get('away','')}"
        out.append(
            f"| {(r.get('date') or '')[:10]} | {r.get('competition','')[:18]} "
            f"| {match} | {_fmt(r.get('predicted_prob'), 3)} "
            f"| {_fmt(r.get('market_prob'), 3)} | {_fmt(r.get('edge_pp'), 1)} "
            f"| {_fmt(r.get('odd_draw'), 2)} | {r.get('hit')} "
            f"| {r.get('actual_score')} |"
        )
    out.append("")
    return out


def render_domestic_summary(results: list) -> str:
    lines = ["# Sprint-D5 · Domestic Leagues DRAW Backtest Summary",
             "",
             f"Generated: {datetime.utcnow().isoformat()}Z",
             "",
             "> Mode: `observe_only` · DRAW market · min_edge=4pp · walk-forward + calibration.",
             "> Odds: opening (B365 / Pinnacle); closing warnings propagated.",
             "",
             "## Headline (per league)",
             "",
             "| League | N bets | hit_rate | ROI | CI 95% | sample_status | sig? |",
             "|---|---:|---:|---:|:---|:---|:---:|"]
    for r in results:
        if r is None:
            continue
        lines.append(
            f"| {r['competition']} | {r['n_bets']} | {_fmt(r['hit_rate'])} "
            f"| {_fmt(r['roi'])} | [{_fmt(r.get('roi_ci_low'))}, "
            f"{_fmt(r.get('roi_ci_high'))}] | "
            f"{r.get('sample_status')} | "
            f"{'✅' if r.get('is_roi_significant') else '❌'} |"
        )

    # Aggregate across all leagues.
    tot_bets = sum(r["n_bets"] for r in results if r)
    tot_won  = sum(r["n_won"] for r in results if r)
    tot_staked = sum((r.get("total_staked") or 0) for r in results if r)
    tot_pnl    = sum((r.get("net_pnl") or 0) for r in results if r)
    agg_hit = tot_won / tot_bets if tot_bets else None
    agg_roi = tot_pnl / tot_staked if tot_staked else None
    lines += ["",
              "## Aggregate (combined 5 leagues)",
              "",
              f"- **n_bets total**: {tot_bets}",
              f"- **hits**: {tot_won}",
              f"- **hit_rate aggregate**: {_fmt(agg_hit)}",
              f"- **net_pnl aggregate**: {_fmt(tot_pnl, 2)}",
              f"- **total_staked aggregate**: {_fmt(tot_staked, 2)}",
              f"- **ROI aggregate**: {_fmt(agg_roi)}",
              ""]

    # Cohort matrix — aggregate.
    agg_cohorts: dict = {c: {"n": 0, "won": 0} for c in ALL_COHORTS}
    examples_per_cohort: dict = {c: [] for c in ALL_COHORTS}
    for r in results:
        if not r:
            continue
        for c, row in (r.get("cohorts") or {}).items():
            agg_cohorts[c]["n"] += row["n"]
            agg_cohorts[c]["won"] += row["won"]
            examples_per_cohort[c].extend(row.get("examples", []))
    for c, row in agg_cohorts.items():
        row["hit_rate"] = (round(row["won"] / row["n"], 4)
                            if row["n"] else None)
        row["examples"] = examples_per_cohort[c][:5]

    lines += ["## Cohort breakdown (aggregate across 5 leagues)",
              ""] + _cohort_table(agg_cohorts) + [""]
    for c in ALL_COHORTS:
        lines += _cohort_examples_table(agg_cohorts, c)

    return "\n".join(lines) + "\n"


def render_tournament_summary(results: list) -> str:
    lines = ["# Sprint-D5 · National Tournaments DRAW Backtest Summary",
             "",
             f"Generated: {datetime.utcnow().isoformat()}Z",
             "",
             "> Mode: `observe_only` · DRAW · `no_market` (calibration-only).",
             "> No odds → metrics focus on Brier / calibration / label hit-rate.",
             "",
             "## Headline (per tournament)",
             "",
             "| Tournament | N preds | N fired | hit_rate | base_rate | Brier | calibration |",
             "|---|---:|---:|---:|---:|---:|:---|"]
    for r in results:
        if r is None:
            continue
        cm = r.get("combined_metrics") or {}
        lines.append(
            f"| {r['competition']} | {r.get('n_predictions','-')} "
            f"| {r.get('n_picks_fired','-')} | {_fmt(r.get('hit_rate'))} "
            f"| {_fmt(cm.get('base_rate'))} "
            f"| {_fmt(cm.get('brier_score'), 4)} "
            f"| {cm.get('calibration_label','-')} |"
        )

    agg_cohorts: dict = {c: {"n": 0, "won": 0} for c in ALL_COHORTS}
    examples_per_cohort: dict = {c: [] for c in ALL_COHORTS}
    for r in results:
        if not r:
            continue
        for c, row in (r.get("cohorts") or {}).items():
            agg_cohorts[c]["n"] += row["n"]
            agg_cohorts[c]["won"] += row["won"]
            examples_per_cohort[c].extend(row.get("examples", []))
    for c, row in agg_cohorts.items():
        row["hit_rate"] = (round(row["won"] / row["n"], 4)
                            if row["n"] else None)
        row["examples"] = examples_per_cohort[c][:5]

    lines += ["",
              "## Cohort breakdown (aggregate across 3 tournaments)",
              ""] + _cohort_table(agg_cohorts) + [""]
    for c in ALL_COHORTS:
        lines += _cohort_examples_table(agg_cohorts, c)
    return "\n".join(lines) + "\n"


def render_combined_comparison(leagues: list, tournaments: list) -> str:
    lines = ["# Sprint-D5 · Combined Comparison · Domestic vs National Tournaments",
             "",
             f"Generated: {datetime.utcnow().isoformat()}Z",
             "",
             "> **Question:** Does Draw Potential perform better in national",
             "> tournaments than in domestic leagues? Is there a repeatable",
             "> 'Spain vs Cape Verde' archetype?",
             "",
             "## Domestic vs National headline",
             "",
             "| Setting | N picks | hit_rate | base_rate | Brier (combined) |",
             "|---|---:|---:|---:|---:|"]
    # Aggregate domestic.
    dom_n = sum(r["n_bets"] for r in leagues if r)
    dom_w = sum(r["n_won"] for r in leagues if r)
    dom_hr = dom_w/dom_n if dom_n else None
    # Domestic Brier: weighted avg of curves not available; we report
    # mean per-league Brier as a proxy.
    dom_brier_vals = [r.get("calibration_label")
                      for r in leagues if r]
    nat_n = sum(r.get("n_picks_fired", 0) for r in tournaments if r)
    nat_w = sum((r.get("n_won") or 0) for r in tournaments if r)
    nat_hr = nat_w/nat_n if nat_n else None
    nat_pred_n = sum(r.get("n_predictions", 0) for r in tournaments if r)
    nat_pred_hits = sum((r.get("combined_metrics") or {}).get("n_hits", 0) for r in tournaments if r)
    nat_base = nat_pred_hits/nat_pred_n if nat_pred_n else None
    nat_briers = [(r.get("combined_metrics") or {}).get("brier_score")
                  for r in tournaments if r and r.get("combined_metrics")]
    nat_avg_brier = (sum(b for b in nat_briers if b is not None)
                     / max(1, sum(1 for b in nat_briers if b is not None)))

    lines.append(
        f"| Domestic (5 leagues, opening odds) | {dom_n} | {_fmt(dom_hr)} "
        f"| ~0.24 (typical) | n/a (with odds) |"
    )
    lines.append(
        f"| National (3 tournaments, no_market) | {nat_n} | {_fmt(nat_hr)} "
        f"| {_fmt(nat_base)} | {_fmt(nat_avg_brier, 4)} |"
    )

    # Cohort matrix combining both sources.
    agg_cohorts: dict = {c: {"n": 0, "won": 0, "n_dom": 0, "won_dom": 0,
                              "n_nat": 0, "won_nat": 0, "examples": []}
                          for c in ALL_COHORTS}
    examples_per_cohort: dict = {c: [] for c in ALL_COHORTS}
    for src, rows, key_n, key_w in (("dom", leagues, "n_dom", "won_dom"),
                                     ("nat", tournaments, "n_nat", "won_nat")):
        for r in rows:
            if not r:
                continue
            for c, row in (r.get("cohorts") or {}).items():
                agg_cohorts[c]["n"] += row["n"]
                agg_cohorts[c]["won"] += row["won"]
                agg_cohorts[c][key_n] += row["n"]
                agg_cohorts[c][key_w] += row["won"]
                examples_per_cohort[c].extend(row.get("examples", []))
    # Stamp examples back onto agg_cohorts so the renderer picks them up.
    for c in ALL_COHORTS:
        agg_cohorts[c]["examples"] = examples_per_cohort[c][:5]
    lines += ["",
              "## Cohort comparison (Domestic vs National)",
              "",
              "| Cohorte | n_total | n_domestic | hit_rate_dom | n_national | hit_rate_nat |",
              "|---|---:|---:|---:|---:|---:|"]
    for c in ALL_COHORTS:
        row = agg_cohorts[c]
        hr_d = row["won_dom"]/row["n_dom"] if row["n_dom"] else None
        hr_n = row["won_nat"]/row["n_nat"] if row["n_nat"] else None
        lines.append(
            f"| {c} | {row['n']} | {row['n_dom']} | {_fmt(hr_d)} "
            f"| {row['n_nat']} | {_fmt(hr_n)} |"
        )
    lines.append("")

    # Spain vs Cape Verde-style pattern (DOMINANT_FAVORITE_DRAW_VALUE).
    sp = agg_cohorts[COHORT_DOMINANT_FAVORITE]
    lines += ["## Patrón arquetipo «España vs Cabo Verde»",
              "",
              f"- Cohorte `DOMINANT_FAVORITE_DRAW_VALUE` total: **n={sp['n']}**, won={sp['won']}",
              f"- Hit-rate global del arquetipo: **{_fmt(sp['won']/sp['n'] if sp['n'] else None)}**",
              ""]
    if sp["n"] >= 5 and (sp["won"] / sp["n"]) >= 0.28:
        lines.append("> ✅ El arquetipo **gana sobre la base rate típica** (~24%).")
    elif sp["n"] >= 5:
        lines.append("> 🟡 El arquetipo NO supera consistentemente la base rate del 24%.")
    else:
        lines.append("> ⚠️ **Muestra insuficiente** (< 5 picks) para concluir nada sobre el arquetipo.")
    lines.append("")

    lines += _cohort_examples_table(agg_cohorts, COHORT_DOMINANT_FAVORITE)
    lines += _cohort_examples_table(agg_cohorts, COHORT_TAIL_EDGE)

    lines += ["## Veredicto final D5",
              ""]
    # Multi-axis verdict: compare aggregate hit-rate, LOW_GOAL_UNDERDOG_BLOCK,
    # and TOURNAMENT_GROUP_STAGE existence in tournaments.
    dom_dom_n = agg_cohorts[COHORT_DOMINANT_FAVORITE]["n_dom"]
    nat_dom_n = agg_cohorts[COHORT_DOMINANT_FAVORITE]["n_nat"]
    dom_dom_hr = (agg_cohorts[COHORT_DOMINANT_FAVORITE]["won_dom"]/dom_dom_n
                   if dom_dom_n else None)
    nat_dom_hr = (agg_cohorts[COHORT_DOMINANT_FAVORITE]["won_nat"]/nat_dom_n
                   if nat_dom_n else None)
    lgu_n_d = agg_cohorts[COHORT_LOW_GOAL_UNDERDOG]["n_dom"]
    lgu_n_n = agg_cohorts[COHORT_LOW_GOAL_UNDERDOG]["n_nat"]
    lgu_hr_d = (agg_cohorts[COHORT_LOW_GOAL_UNDERDOG]["won_dom"]/lgu_n_d
                 if lgu_n_d else None)
    lgu_hr_n = (agg_cohorts[COHORT_LOW_GOAL_UNDERDOG]["won_nat"]/lgu_n_n
                 if lgu_n_n else None)
    tgs_n = agg_cohorts[COHORT_TOURNAMENT_GROUP]["n_nat"]
    tgs_hr = (agg_cohorts[COHORT_TOURNAMENT_GROUP]["won_nat"]/tgs_n
               if tgs_n else None)

    points_for_nat = 0
    if nat_hr is not None and dom_hr is not None and nat_hr - dom_hr > 0.05:
        lines.append(f"- ✅ **Hit-rate global**: national `{_fmt(nat_hr)}` "
                     f"vs domestic `{_fmt(dom_hr)}` (≥5pp ventaja)")
        points_for_nat += 2
    if (lgu_hr_n is not None and lgu_hr_d is not None
            and lgu_hr_n - lgu_hr_d > 0.05):
        lines.append(f"- ✅ **LOW_GOAL_UNDERDOG_BLOCK**: national "
                     f"`{_fmt(lgu_hr_n)}` vs domestic `{_fmt(lgu_hr_d)}`"
                     f" (≥5pp ventaja)")
        points_for_nat += 1
    if tgs_hr is not None and tgs_hr > 0.28:
        lines.append(f"- ✅ **TOURNAMENT_GROUP_STAGE_DRAW_VALUE**: "
                     f"`{_fmt(tgs_hr)}` supera baseline ~24% en "
                     f"torneos (n={tgs_n})")
        points_for_nat += 1
    if dom_dom_hr is not None and dom_dom_hr < 0.22:
        lines.append(f"- ⚠️ **DOMINANT_FAVORITE_DRAW_VALUE en ligas**: "
                     f"`{_fmt(dom_dom_hr)}` (n={dom_dom_n}) NO supera el "
                     f"baseline; el arquetipo \"España vs Cabo Verde\" "
                     f"NO funciona en clubes europeos.")
    if nat_dom_n == 0:
        lines.append("- ℹ️ `DOMINANT_FAVORITE_DRAW_VALUE` no aparece en"
                     " torneos nacionales — los equipos clasificados"
                     " tienen ELOs similares, así que la asimetría no se"
                     " materializa con el threshold actual (Δ ELO ≥ 150).")
    lines.append("")

    if points_for_nat >= 2:
        lines.append("> 🟢 **VEREDICTO: SÍ** — Draw Potential funciona"
                     " mejor en torneos nacionales que en ligas"
                     " domésticas en esta muestra. Múltiples cohortes"
                     " confirman el efecto.")
    elif points_for_nat == 1:
        lines.append("> 🟡 **VEREDICTO PARCIAL** — algunos cohortes"
                     " sugieren ventaja nacional pero la evidencia"
                     " agregada no es contundente.")
    else:
        lines.append("> 🔴 **VEREDICTO: NO** — no hay evidencia clara"
                     " de que Draw Potential funcione mejor en torneos.")
    lines.append("")
    lines.append("> ⚠️ `observe_only` mantenido. No se activan picks reales."
                 " El módulo permanece en modo observación.")
    return "\n".join(lines) + "\n"


# ─── Main ──────────────────────────────────────────────────────────────
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/app")
    args = p.parse_args()

    print("=== Domestic leagues ===")
    league_results = []
    for key, name, path in LEAGUES:
        r = _run_league(key, name, path)
        league_results.append(r)
        if r:
            print(f"  ✓ {key:11s} n_bets={r['n_bets']:3d} "
                  f"hit_rate={_fmt(r['hit_rate'])} "
                  f"ROI={_fmt(r['roi'])} sig={r['is_roi_significant']}")
            with open(f"{args.out_dir}/backtest_d5_league_{key}.json", "w") as fh:
                json.dump(r, fh, indent=2, default=str)
        else:
            print(f"  ⚠️  {key}: dataset missing or empty")

    print("\n=== National tournaments ===")
    tournament_results = []
    for key, name, path in TOURNAMENTS:
        r = _run_tournament(key, name, path)
        tournament_results.append(r)
        if r:
            cm = r.get("combined_metrics") or {}
            print(f"  ✓ {key:9s} n_pred={r.get('n_predictions','-'):>3} "
                  f"n_fired={r.get('n_picks_fired','-'):>3} "
                  f"hit_rate={_fmt(r.get('hit_rate'))} "
                  f"Brier={_fmt(cm.get('brier_score'), 4)}")
            with open(f"{args.out_dir}/backtest_d5_tournament_{key}.json", "w") as fh:
                json.dump(r, fh, indent=2, default=str)
        else:
            print(f"  ⚠️  {key}: dataset missing or empty")

    with open(f"{args.out_dir}/domestic_leagues_summary.md", "w") as fh:
        fh.write(render_domestic_summary(league_results))
    with open(f"{args.out_dir}/national_tournaments_summary.md", "w") as fh:
        fh.write(render_tournament_summary(tournament_results))
    with open(f"{args.out_dir}/combined_comparison.md", "w") as fh:
        fh.write(render_combined_comparison(league_results, tournament_results))
    print(f"\n✓ Reports written under {args.out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
