"""Sprint-D8/E-LIVE · Comprehensive quantitative analysis report.

Produces a single markdown report covering all 5 parts of the user's
research brief:
  1. Cards backtest (selecciones n=44 + EPL n=380)
  2. Corners backtest (EPL n=380 — corners not available for selecciones)
  3. Dominant favorite → corners hypothesis validation
  4. Football-data.co.uk evaluation as a corner data source
  5. Architecture proposal for a corners-probability engine

Inputs:
  /app/data/cards_history/selecciones_cards_dataset.json  (44 records)
  /app/data/football_data_co_uk/E0_2425.csv               (380 records)

Outputs:
  /app/diagnostics/sprint_d8_cards_corners_research_report.md
  /app/diagnostics/sprint_d8_cards_corners_research_stats.json
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Pure stats helpers (no scipy/numpy)
# ─────────────────────────────────────────────────────────────────────
def _pct(xs: list[float], q: float) -> float | None:
    if not xs: return None
    s = sorted(xs)
    idx = max(0, min(len(s)-1, int(round(q*(len(s)-1)))))
    return s[idx]

def _corr(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or len(ys) != n: return None
    mx = sum(xs)/n; my = sum(ys)/n
    num = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    if dx == 0 or dy == 0: return None
    return round(num/(dx*dy), 4)

def _summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {
        "n":      len(xs),
        "mean":   round(statistics.mean(xs), 3),
        "median": round(statistics.median(xs), 3),
        "stdev":  round(statistics.pstdev(xs), 3) if len(xs) >= 2 else 0.0,
        "min":    min(xs), "max": max(xs),
        "p10":    _pct(xs, 0.10),  "p25": _pct(xs, 0.25),
        "p75":    _pct(xs, 0.75),  "p90": _pct(xs, 0.90),
    }

def _t_test_means(xs: list[float], ys: list[float]) -> dict:
    """Welch's t-test for two independent samples. Returns t, df, and a
    rough two-tailed p-value (using a normal approximation; OK for
    n ≥ 30)."""
    if len(xs) < 2 or len(ys) < 2:
        return {"t": None, "df": None, "p_approx": None}
    mx, my = statistics.mean(xs), statistics.mean(ys)
    vx = statistics.pvariance(xs); vy = statistics.pvariance(ys)
    nx, ny = len(xs), len(ys)
    se = math.sqrt(vx/nx + vy/ny) if (vx/nx + vy/ny) > 0 else 0
    if se == 0: return {"t": None, "df": None, "p_approx": None}
    t = (mx - my) / se
    # Welch-Satterthwaite df.
    num = (vx/nx + vy/ny) ** 2
    den = ((vx/nx)**2)/(nx-1 if nx > 1 else 1) + ((vy/ny)**2)/(ny-1 if ny > 1 else 1)
    df = num/den if den > 0 else None
    # Normal-approx 2-tailed p-value via Abramowitz erf.
    def _erfc(x):
        # Cheap erfc using math.erfc if available.
        return math.erfc(x)
    p_two = _erfc(abs(t)/math.sqrt(2))
    return {"t": round(t, 4), "df": round(df, 2) if df else None,
             "p_approx": round(p_two, 5)}

def _bootstrap_mean_ci(xs: list[float], n_boot: int = 1000,
                        ci: float = 0.95) -> tuple[float, float] | None:
    if len(xs) < 5: return None
    import random
    rng = random.Random(42)
    means = []
    nx = len(xs)
    for _ in range(n_boot):
        sample = [xs[rng.randrange(nx)] for _ in range(nx)]
        means.append(sum(sample)/nx)
    alpha = (1 - ci) / 2
    lo = _pct(means, alpha); hi = _pct(means, 1 - alpha)
    return (round(lo, 3), round(hi, 3))


# ─────────────────────────────────────────────────────────────────────
# Load datasets
# ─────────────────────────────────────────────────────────────────────
SELE = json.loads(Path("/app/data/cards_history/selecciones_cards_dataset.json")
                    .read_text(encoding="utf-8"))
SELE_OK = [r for r in SELE if r.get("available")]

def _to_int(s):
    try: return int(float(s))
    except (TypeError, ValueError): return None

def _to_float(s):
    try: return float(s)
    except (TypeError, ValueError): return None

with Path("/app/data/football_data_co_uk/E0_2425.csv").open("r", encoding="utf-8") as fh:
    EPL = list(csv.DictReader(fh))

def epl_row(r):
    return {
        "date":   r.get("Date"),
        "home":   r.get("HomeTeam"), "away": r.get("AwayTeam"),
        "fthg":   _to_int(r.get("FTHG")),  "ftag": _to_int(r.get("FTAG")),
        "hthg":   _to_int(r.get("HTHG")),  "htag": _to_int(r.get("HTAG")),
        "ftr":    r.get("FTR"),
        "hc":     _to_int(r.get("HC")),    "ac":   _to_int(r.get("AC")),
        "hf":     _to_int(r.get("HF")),    "af":   _to_int(r.get("AF")),
        "hy":     _to_int(r.get("HY")) or 0, "ay":  _to_int(r.get("AY")) or 0,
        "hr":     _to_int(r.get("HR")) or 0, "ar":  _to_int(r.get("AR")) or 0,
        "hs":     _to_int(r.get("HS")),    "as_":  _to_int(r.get("AS")),
        "hst":    _to_int(r.get("HST")),   "ast":  _to_int(r.get("AST")),
        "referee": r.get("Referee"),
        # h2h pre-match consensus draw odd / home / away
        "psh":    _to_float(r.get("PSH")), "psd": _to_float(r.get("PSD")),
        "psa":    _to_float(r.get("PSA")),
    }

EPL_ROWS = [epl_row(r) for r in EPL if _to_int(r.get("HC")) is not None]
print(f"Loaded: {len(SELE_OK)} selecciones cards records, {len(EPL_ROWS)} EPL records")

# ─────────────────────────────────────────────────────────────────────
# PARTE 1 — Cards analysis
# ─────────────────────────────────────────────────────────────────────
def part1_cards() -> dict:
    out = {}

    # SELECCIONES (n=44, Euro+Copa)
    s_home_y  = [r["home_yellow"] for r in SELE_OK]
    s_away_y  = [r["away_yellow"] for r in SELE_OK]
    s_home_r  = [r["home_red"]    for r in SELE_OK]
    s_away_r  = [r["away_red"]    for r in SELE_OK]
    s_home_cards = [hy + hr for hy, hr in zip(s_home_y, s_home_r)]
    s_away_cards = [ay + ar for ay, ar in zip(s_away_y, s_away_r)]
    s_total      = [h + a   for h, a in zip(s_home_cards, s_away_cards)]
    s_diff       = [abs(h - a) for h, a in zip(s_home_cards, s_away_cards)]
    s_1t = [r["home_yellow_1t"] + r["away_yellow_1t"]
             + r["home_red_1t"]   + r["away_red_1t"]   for r in SELE_OK]
    s_2t = [r["home_yellow_2t"] + r["away_yellow_2t"]
             + r["home_red_2t"]   + r["away_red_2t"]   for r in SELE_OK]
    s_goals = [(r.get("of_score") or [None, None])[0] +
                 (r.get("of_score") or [None, None])[1]
                 if r.get("of_score") and r["of_score"][0] is not None else 0
                 for r in SELE_OK]
    out["selecciones"] = {
        "n":                len(SELE_OK),
        "total":            _summary(s_total),
        "home_cards":       _summary(s_home_cards),
        "away_cards":       _summary(s_away_cards),
        "diff_abs":         _summary(s_diff),
        "cards_1t":         _summary(s_1t),
        "cards_2t":         _summary(s_2t),
        "corr_cards_goals": _corr(s_total, s_goals),
        "tournaments_covered": dict(Counter(r["tournament"] for r in SELE_OK)),
    }

    # Distribution buckets.
    buckets = Counter()
    for t in s_total:
        bucket = "0-2" if t <= 2 else "3-4" if t <= 4 else "5-6" if t <= 6 else "7-8" if t <= 8 else "9+"
        buckets[bucket] += 1
    out["selecciones"]["distribution_total_cards"] = dict(buckets)

    # EPL (n=380)
    e_total  = [r["hy"] + r["ay"] + r["hr"] + r["ar"] for r in EPL_ROWS]
    e_home   = [r["hy"] + r["hr"] for r in EPL_ROWS]
    e_away   = [r["ay"] + r["ar"] for r in EPL_ROWS]
    e_diff   = [abs(h - a) for h, a in zip(e_home, e_away)]
    e_goals  = [r["fthg"] + r["ftag"] for r in EPL_ROWS]
    e_corners = [r["hc"] + r["ac"] for r in EPL_ROWS]
    e_fouls  = [(r["hf"] or 0) + (r["af"] or 0) for r in EPL_ROWS]
    out["epl"] = {
        "n":                  len(EPL_ROWS),
        "total":              _summary(e_total),
        "home_cards":         _summary(e_home),
        "away_cards":         _summary(e_away),
        "diff_abs":           _summary(e_diff),
        "corr_cards_goals":   _corr(e_total, e_goals),
        "corr_cards_corners": _corr(e_total, e_corners),
        "corr_cards_fouls":   _corr(e_total, e_fouls),
    }

    # Distribution for EPL.
    buckets_e = Counter()
    for t in e_total:
        bucket = "0-2" if t <= 2 else "3-4" if t <= 4 else "5-6" if t <= 6 else "7-8" if t <= 8 else "9+"
        buckets_e[bucket] += 1
    out["epl"]["distribution_total_cards"] = dict(buckets_e)

    # ¿Partidos con MUCHAS tarjetas → más o menos goles?
    cards_high = [g for c, g in zip(e_total, e_goals) if c >= 5]
    cards_low  = [g for c, g in zip(e_total, e_goals) if c <= 2]
    out["epl"]["high_cards_goals_test"] = {
        "n_high_cards_(≥5)": len(cards_high),
        "n_low_cards_(≤2)":   len(cards_low),
        "mean_goals_high":   round(statistics.mean(cards_high), 3) if cards_high else None,
        "mean_goals_low":    round(statistics.mean(cards_low), 3)  if cards_low  else None,
        "t_test":            _t_test_means(cards_high, cards_low),
    }

    # ¿Existe umbral útil 4.5 tarjetas? Over/Under cards threshold ROI hypothetical.
    for line in (3.5, 4.5, 5.5):
        over_hits = sum(1 for t in e_total if t > line)
        out["epl"][f"base_rate_over_{line}_cards"] = round(over_hits / len(e_total), 4)
    # ROI hipotético si apostáramos OVER 4.5 a una cuota promedio típica (1.95).
    avg_cards_odd = 1.95   # typical bookmaker line
    over_4_5_hits = sum(1 for t in e_total if t > 4.5)
    hit_rate = over_4_5_hits / len(e_total)
    out["epl"]["hypothetical_roi_over_4_5_cards"] = {
        "hit_rate":   round(hit_rate, 4),
        "avg_odd":    avg_cards_odd,
        "roi_pct":    round((hit_rate * avg_cards_odd - 1) * 100, 2),
    }

    # Cards vs result (1-2-X).
    e_result = [r["ftr"] for r in EPL_ROWS]
    cards_by_result = {"H": [], "D": [], "A": []}
    for t, res in zip(e_total, e_result):
        if res in cards_by_result:
            cards_by_result[res].append(t)
    out["epl"]["cards_by_ftr"] = {
        k: {"n": len(v), "mean": round(statistics.mean(v), 3) if v else None}
        for k, v in cards_by_result.items()
    }

    return out


# ─────────────────────────────────────────────────────────────────────
# PARTE 2 — Corners analysis (EPL only)
# ─────────────────────────────────────────────────────────────────────
def part2_corners() -> dict:
    e_total = [r["hc"] + r["ac"] for r in EPL_ROWS]
    e_home  = [r["hc"] for r in EPL_ROWS]
    e_away  = [r["ac"] for r in EPL_ROWS]
    e_diff  = [r["hc"] - r["ac"] for r in EPL_ROWS]
    out = {
        "n":      len(EPL_ROWS),
        "total":  _summary(e_total),
        "home":   _summary(e_home),
        "away":   _summary(e_away),
        "diff_signed": _summary(e_diff),
    }
    # Over/Under base rates and hypothetical ROI at typical odd 1.85.
    typical_odd = 1.85
    out["over_under_lines"] = {}
    for line in (5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5):
        over_hits = sum(1 for t in e_total if t > line)
        under_hits = sum(1 for t in e_total if t <= line)
        out["over_under_lines"][str(line)] = {
            "over_rate":  round(over_hits / len(e_total), 4),
            "under_rate": round(under_hits / len(e_total), 4),
            "hypo_over_roi_at_1_85":  round((over_hits/len(e_total) * typical_odd - 1)*100, 2),
            "hypo_under_roi_at_1_85": round((under_hits/len(e_total) * typical_odd - 1)*100, 2),
        }
    # Correlations.
    out["correlations"] = {
        "corr_corners_goals":  _corr(e_total, [r["fthg"]+r["ftag"] for r in EPL_ROWS]),
        "corr_corners_shots":  _corr(e_total, [(r["hs"] or 0)+(r["as_"] or 0) for r in EPL_ROWS]),
        "corr_corners_sot":    _corr(e_total, [(r["hst"] or 0)+(r["ast"] or 0) for r in EPL_ROWS]),
        "corr_corners_fouls":  _corr(e_total, [(r["hf"] or 0)+(r["af"] or 0) for r in EPL_ROWS]),
        "corr_corners_cards":  _corr(e_total, [r["hy"]+r["ay"]+r["hr"]+r["ar"] for r in EPL_ROWS]),
        "corr_home_corners_home_shots": _corr(e_home, [r["hs"] or 0 for r in EPL_ROWS]),
        "corr_away_corners_away_shots": _corr(e_away, [r["as_"] or 0 for r in EPL_ROWS]),
    }
    return out


# ─────────────────────────────────────────────────────────────────────
# PARTE 3 — DOMINANT_FAVORITE → corners hypothesis
# Uses Pinnacle h2h odds as the proxy for "favorite": favorite = lower h2h odd.
# A "dominant" favorite is one with implied_prob_devig ≥ 0.65.
# ─────────────────────────────────────────────────────────────────────
def _devig_h2h_row(r):
    h, d, a = r["psh"], r["psd"], r["psa"]
    if not all(o and o > 1.0 for o in (h, d, a)): return None, None, None
    ph, pd, pa = 1.0/h, 1.0/d, 1.0/a
    s = ph + pd + pa
    return ph/s, pd/s, pa/s

def part3_dominant_favorite_corners() -> dict:
    home_fav_dom = []   # rows where HOME team is dominant favorite (prob ≥ 0.65)
    away_fav_dom = []
    balanced     = []
    for r in EPL_ROWS:
        ph, pd, pa = _devig_h2h_row(r)
        if ph is None: continue
        if ph >= 0.65:
            home_fav_dom.append(r)
        elif pa >= 0.65:
            away_fav_dom.append(r)
        else:
            balanced.append(r)

    # For each dominant-favorite match, did the favorite outcorner the dog?
    fav_more_corners = 0
    fav_less_corners = 0
    fav_equal_corners = 0
    fav_corners = []        # favorite's corners
    dog_corners = []        # underdog's corners
    diff_signed = []
    for r in home_fav_dom:
        if r["hc"] > r["ac"]: fav_more_corners += 1
        elif r["hc"] < r["ac"]: fav_less_corners += 1
        else: fav_equal_corners += 1
        fav_corners.append(r["hc"])
        dog_corners.append(r["ac"])
        diff_signed.append(r["hc"] - r["ac"])
    for r in away_fav_dom:
        if r["ac"] > r["hc"]: fav_more_corners += 1
        elif r["ac"] < r["hc"]: fav_less_corners += 1
        else: fav_equal_corners += 1
        fav_corners.append(r["ac"])
        dog_corners.append(r["hc"])
        diff_signed.append(r["ac"] - r["hc"])
    n_dom = len(home_fav_dom) + len(away_fav_dom)
    out = {
        "definition": ("favorite = pre-match implied prob devig ≥ 0.65; "
                        "uses Pinnacle PSH/PSD/PSA"),
        "n_dominant_matches":  n_dom,
        "n_home_dominant":     len(home_fav_dom),
        "n_away_dominant":     len(away_fav_dom),
        "n_balanced":          len(balanced),
        "pct_fav_more_corners":   round(fav_more_corners / n_dom * 100, 2) if n_dom else None,
        "pct_fav_less_corners":   round(fav_less_corners / n_dom * 100, 2) if n_dom else None,
        "pct_equal":              round(fav_equal_corners / n_dom * 100, 2) if n_dom else None,
        "fav_corners":            _summary(fav_corners),
        "dog_corners":            _summary(dog_corners),
        "diff_signed_summary":    _summary(diff_signed),
        "diff_mean_bootstrap_ci": _bootstrap_mean_ci(diff_signed),
        "t_test_fav_vs_dog":      _t_test_means(fav_corners, dog_corners),
    }

    # Variables that explain corners (correlation ranking against TOTAL corners).
    total_corners = [r["hc"] + r["ac"] for r in EPL_ROWS]
    feats = {
        "shots_total":      [(r["hs"] or 0) + (r["as_"] or 0) for r in EPL_ROWS],
        "shots_on_target":  [(r["hst"] or 0) + (r["ast"] or 0) for r in EPL_ROWS],
        "fouls_total":      [(r["hf"] or 0) + (r["af"] or 0) for r in EPL_ROWS],
        "cards_total":      [r["hy"]+r["ay"]+r["hr"]+r["ar"] for r in EPL_ROWS],
        "goals_total":      [r["fthg"] + r["ftag"] for r in EPL_ROWS],
        "home_implied_prob":[(_devig_h2h_row(r)[0] or 0) for r in EPL_ROWS],
        "abs_implied_diff": [abs((_devig_h2h_row(r)[0] or 0.5) -
                                  (_devig_h2h_row(r)[2] or 0.5))
                              for r in EPL_ROWS],
    }
    importance = {}
    for k, v in feats.items():
        c = _corr(v, total_corners)
        importance[k] = c
    out["feature_importance_corr_with_total_corners"] = dict(
        sorted(importance.items(), key=lambda kv: abs(kv[1] or 0), reverse=True)
    )
    return out


# ─────────────────────────────────────────────────────────────────────
# Main runner that produces the markdown report.
# ─────────────────────────────────────────────────────────────────────
def main() -> int:
    p1 = part1_cards()
    p2 = part2_corners()
    p3 = part3_dominant_favorite_corners()

    all_stats = {"_meta": {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "selecciones_n": len(SELE_OK),
        "epl_n":         len(EPL_ROWS),
        "scope_note":    ("Opción B aprobada: cards en selecciones; "
                           "corners + variables explicativas en EPL 380."),
    }, "parte1_cards": p1, "parte2_corners": p2, "parte3_hypothesis": p3}

    stats_path = Path("/app/diagnostics/sprint_d8_cards_corners_research_stats.json")
    stats_path.write_text(json.dumps(all_stats, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"[write] {stats_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
