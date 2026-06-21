"""Sprint Corner-1+2 · Fase A · paso final — Calibración + Backtest probabilístico.

Carga el dataset enriquecido (football-data.co.uk + Understat), construye
features point-in-time (igual al pipeline de Fase 1.5) y corre el
``run_corner_backtest`` con walk-forward:

    Fold 1: train=2021/22  → test=2022/23
    Fold 2: train=2021/22+2022/23 → test=2023/24

Salidas:
    /app/diagnostics/corner_engine_phase_a_stats.json
    /app/diagnostics/corner_engine_phase_a_report.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Asegurar que backend está en path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football.corners.corner_backtest import (  # noqa: E402
    run_corner_backtest,
)

ENRICHED_DATASET = Path("/app/data/corners_history/all_leagues_enriched_dataset.json")
OUT_STATS  = Path("/app/diagnostics/corner_engine_phase_a_stats.json")
OUT_REPORT = Path("/app/diagnostics/corner_engine_phase_a_report.md")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _avg_last_n(items, n, key, venue_filter=None):
    pool = items if venue_filter is None else [it for it in items if it["venue"] == venue_filter]
    if not pool:
        return None
    return _mean([it.get(key) for it in pool[-n:]])


def _build_pit_rows(rows: list[dict]) -> list[dict]:
    """Re-construye features point-in-time (L15 ventana) sobre el dataset
    enriquecido. Idéntico en spíritu al pipeline de Fase 1.5.
    """
    history: dict[str, list[dict]] = defaultdict(list)
    out: list[dict] = []

    rows = sorted(rows, key=lambda r: (r["date"], r["league_code"]))

    for r in rows:
        home, away = r["home_team"], r["away_team"]
        hh, ah = history[home], history[away]

        ip_home = r.get("implied_prob_home")
        ip_away = r.get("implied_prob_away")

        feat = {
            "match_id":     r["match_id"],
            "date":         r["date"],
            "league":       r["league"],
            "league_code":  r["league_code"],
            "season":       r["season"],
            "home_team":    home,
            "away_team":    away,
            "home_corners": r["home_corners"],
            "away_corners": r["away_corners"],
            "total_corners":  r["total_corners"],
            "home_implied_prob": ip_home,
            "away_implied_prob": ip_away,
            "home_corners_for_L15":     _avg_last_n(hh, 15, "corners_for"),
            "home_corners_against_L15": _avg_last_n(hh, 15, "corners_against"),
            "away_corners_for_L15":     _avg_last_n(ah, 15, "corners_for"),
            "away_corners_against_L15": _avg_last_n(ah, 15, "corners_against"),
            "home_deep_allowed_L15":    _avg_last_n(hh, 15, "deep_allowed"),
            "away_deep_allowed_L15":    _avg_last_n(ah, 15, "deep_allowed"),
            # venue splits: home en local, away en visitante
            "home_venue_corner_split":  _avg_last_n(hh, 15, "corners_for", venue_filter="home"),
            "away_venue_corner_split":  _avg_last_n(ah, 15, "corners_for", venue_filter="away"),
        }
        out.append(feat)

        # Actualizar history
        history[home].append({
            "date":            r["date"],
            "venue":           "home",
            "corners_for":     r["home_corners"],
            "corners_against": r["away_corners"],
            "deep_allowed":    r.get("deep_allowed_h"),
        })
        history[away].append({
            "date":            r["date"],
            "venue":           "away",
            "corners_for":     r["away_corners"],
            "corners_against": r["home_corners"],
            "deep_allowed":    r.get("deep_allowed_a"),
        })
    return out


def _fmt(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _build_markdown(result: dict, n_rows: int, leagues: list[str]) -> str:
    L = []
    L.append("# Corner Engine — Fase A (Sprint Corner-1 + Corner-2)")
    L.append("")
    L.append(f"_Generado: {datetime.utcnow().isoformat()}Z_")
    L.append("")
    L.append("## 1. Resumen ejecutivo")
    L.append("")
    L.append(f"- Dataset: **{n_rows}** partidos enriquecidos (4 ligas europeas × 3 temporadas).")
    L.append("- Walk-forward: Fold 1 (train 2021/22 → test 2022/23) + Fold 2 (train 2021/22+2022/23 → test 2023/24).")
    L.append(f"- Total predicciones evaluadas: **{result['n_total_predictions']}**.")
    L.append(f"- **Cuotas reales (Asian Corners) disponibles**: `{result['real_odds_available']}`. {('⚠️ ' + ', '.join(result['warnings'])) if result['warnings'] else ''}")
    L.append("")
    gm = result["global_metrics"]
    L.append("### Métricas globales (acumulado walk-forward)")
    L.append("")
    L.append(f"- **Brier Score** (3-way home/away/tie): **{_fmt(gm['brier_score'])}**")
    L.append(f"- **Log Loss**: **{_fmt(gm['log_loss'])}**")
    L.append(f"- **Hit rate (entre decided, ignorando ties)**: **{_fmt(gm['hit_rate_decided'])}** ({gm.get('n_decided','—')} casos)")
    if gm.get("bet_hit_rate") is not None:
        L.append(f"- **Bet hit rate** (solo cuando el motor recomendó BET): **{_fmt(gm['bet_hit_rate'])}** ({gm.get('n_bet_correct','—')}/{gm.get('n_bet_decisions','—')})")
    L.append("")

    L.append("## 2. Calibración del modelo (por fold)")
    L.append("")
    for fr in result["fold_results"]:
        L.append(f"### Fold {fr['fold_idx']+1} — train={fr['train_seasons']} → test={fr['test_seasons']}")
        L.append("")
        L.append(f"- n test: **{fr['n']}**; n decided: **{fr.get('n_decided','—')}**")
        L.append(f"- Brier: **{_fmt(fr['brier_score'])}** | LogLoss: **{_fmt(fr['log_loss'])}** | "
                  f"Hit rate decided: **{_fmt(fr.get('hit_rate_decided'))}**")
        coefs = fr["calibrated_coefficients"]
        sig   = fr["calibrated_sigmoid"]
        L.append("")
        L.append("**β del corner_diff_model (OLS sobre `home_corners - away_corners`):**")
        L.append("")
        L.append("| Coef | Valor |")
        L.append("|------|-------|")
        for k, v in coefs.items():
            L.append(f"| `{k}` | {_fmt(v)} |")
        L.append("")
        L.append(f"**Sigmoid del corner_most_model:** a = {_fmt(sig['a'])}, b = {_fmt(sig['b'])}")
        L.append("")
        L.append("**Tie buckets calibrados (frecuencia empírica de empates por |edcd|):**")
        L.append("")
        L.append("| max abs(edcd) | P(tie) |")
        L.append("|---------------|--------|")
        for edge, p in fr["calibrated_tie_buckets"]:
            L.append(f"| {edge} | {_fmt(p)} |")
        L.append("")

    L.append("## 3. Métricas por liga")
    L.append("")
    L.append("| Liga | n | Brier | LogLoss | Hit rate decided | Bet hit rate |")
    L.append("|------|---|-------|---------|------------------|--------------|")
    for lg, m in sorted(result["by_league"].items()):
        L.append(f"| {lg} | {m.get('n','—')} | {_fmt(m.get('brier_score'))} | "
                  f"{_fmt(m.get('log_loss'))} | {_fmt(m.get('hit_rate_decided'))} | "
                  f"{_fmt(m.get('bet_hit_rate'))} |")
    L.append("")

    L.append("## 4. Calibración (probabilidad predicha vs realizada, P(home_most))")
    L.append("")
    L.append("| Bin | n | Predicted P | Observed P | Gap |")
    L.append("|-----|---|-------------|------------|-----|")
    for cb in gm["calibration_bins"]:
        gap = None
        if cb["predicted_p"] is not None and cb["observed_p"] is not None:
            gap = cb["predicted_p"] - cb["observed_p"]
        L.append(f"| {cb['bin']} | {cb['n']} | {_fmt(cb['predicted_p'])} | "
                  f"{_fmt(cb['observed_p'])} | {_fmt(gap, 4) if gap is not None else '—'} |")
    L.append("")

    L.append("## 5. Asian Corner markets — Probabilidades vs realización")
    L.append("")
    if not result["asian_metrics"]:
        L.append("_Sin datos suficientes._")
    else:
        L.append("| Market | n | Win rate observado | Prob predicha media | Gap |")
        L.append("|--------|---|--------------------|---------------------|-----|")
        # Ordenar por market name
        for mkey in sorted(result["asian_metrics"].keys()):
            d = result["asian_metrics"][mkey]
            L.append(f"| `{mkey}` | {d['n']} | {_fmt(d['observed_win_rate'])} | "
                      f"{_fmt(d['avg_predicted_win'])} | {_fmt(d['calibration_gap'])} |")
    L.append("")

    L.append("## 6. Hallazgos clave")
    L.append("")
    L.append(f"- **Brier score global**: {_fmt(gm['brier_score'])}. Para referencia, el baseline trivial (home_prob=0.45, away=0.40, tie=0.15) tiene Brier ≈ 0.60-0.65. Un modelo informativo debería estar por debajo de 0.60.")
    if gm.get('hit_rate_decided') is not None and gm['hit_rate_decided'] > 0.55:
        L.append(f"- ✅ **Hit rate decided = {gm['hit_rate_decided']*100:.2f}%** supera al baseline 50/50 (excluyendo ties).")
    if gm.get('bet_hit_rate') is not None and gm.get('n_bet_decisions', 0) > 0:
        L.append(f"- 📈 **Cuando el motor recomendó BET ({gm['n_bet_decisions']} casos), acertó {gm['bet_hit_rate']*100:.2f}%**. Comparar con el threshold de confidence ≥ 55 y prob ≥ 0.58.")
    L.append("")

    L.append("## 7. Limitaciones honestas")
    L.append("")
    L.append("- **REAL_ODDS_NOT_AVAILABLE**: este backtest es PROBABILÍSTICO puro. No afirma ROI. El siguiente paso (no incluido en Fase A) es backtest con cuotas reales del endpoint histórico de TheOddsAPI (~60 créditos por evento; muestra recomendada: 100-150 eventos con DOMINANT_FAVORITE).")
    L.append("- Liga MX sigue ausente (sin xG ni córners disponibles en fuentes gratuitas).")
    L.append("- Los β del modelo son lineales sin interacciones; un modelo logístico multivariado o un boosting podría dar un salto adicional pequeño.")
    L.append("- El modelo Most Corners reduce el techo del problema (3-way → binario con tie) y aprovecha el hallazgo DOMINANT_FAVORITE. Su valor depende de la cobertura de cuotas reales en producción.")
    L.append("")

    L.append("## 8. Próximos pasos sugeridos")
    L.append("")
    L.append("1. **Fase B** — Integrar al endpoint /api/football/picks con feature flags `ENABLE_CORNER_MOST_MODEL` y `ENABLE_ASIAN_CORNERS_MODEL`. UI cards.")
    L.append("2. **Backtest con cuotas reales**: ~100-150 partidos seleccionados (DOMINANT_FAVORITE detectado), endpoint histórico de TheOddsAPI, mercados `alternate_spreads_corners`. ROI real sobre Asian Corners.")
    L.append("3. **Refinamientos opcionales**: Skellam si calibramos lambdas Poisson por equipo, interacciones xG × deep_allowed.")
    L.append("")
    return "\n".join(L)


def main() -> int:
    if not ENRICHED_DATASET.exists():
        print(f"[error] dataset not found: {ENRICHED_DATASET}")
        return 2
    raw = json.loads(ENRICHED_DATASET.read_text(encoding="utf-8"))
    print(f"[load] {len(raw)} matches from {ENRICHED_DATASET}")

    rows = _build_pit_rows(raw)
    leagues = sorted({r["league"] for r in rows})
    print(f"[features] PIT rows built: {len(rows)} | leagues: {leagues}")

    result = run_corner_backtest(rows, odds_lookup=None)
    print("\n[result] global metrics:")
    gm = result["global_metrics"]
    for k in ("n", "n_decided", "brier_score", "log_loss",
               "hit_rate_decided", "n_bet_decisions", "bet_hit_rate"):
        print(f"  {k:<20s} {gm.get(k)}")

    # Stats by league preview
    print("\n[result] by league:")
    for lg, m in sorted(result["by_league"].items()):
        print(f"  {lg:<12s} n={m['n']} Brier={_fmt(m['brier_score'])} "
               f"HitRate={_fmt(m.get('hit_rate_decided'))}")

    # Asian preview
    if result["asian_metrics"]:
        print(f"\n[result] asian markets: {len(result['asian_metrics'])} entries")

    # Write outputs
    OUT_STATS.parent.mkdir(parents=True, exist_ok=True)
    OUT_STATS.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\n[write] stats → {OUT_STATS}")
    md = _build_markdown(result, len(rows), leagues)
    OUT_REPORT.write_text(md, encoding="utf-8")
    print(f"[write] report → {OUT_REPORT}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
