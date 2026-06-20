"""Sprint-D8/E PASO 2 · Cards predictor Fase 1 POC + ablación.

Recibe un dataset de partidos finalizados (Premier últimos 4 meses,
~150 partidos) y reporta el **AUC discriminante** del predictor
``compute_cards_potential`` vs el resultado real de over/under
tarjetas por línea (3.5 / 4.5 / 5.5), con **ablación**:

  * Run A — sin árbitro (``use_referee_factor=False``)
  * Run B — con árbitro (``use_referee_factor=True``)

Entregable estrella (decisión del usuario): la tabla
``AUC_sin_árbitro vs AUC_con_árbitro`` por línea.

Inputs
======
El script espera ENCONTRAR un JSON con partidos finalizados en:
  ``/app/data/cards_history/premier_last_4_months.json``

Si no existe, genera un placeholder informativo y sale sin gastar
créditos. Cuando el caller (scrape.do habilitado) tenga el dataset:

  [{
     "match_id":   "...",
     "date":       "2024-10-01",
     "league":     "Premier League",
     "home_team":  "Liverpool",
     "away_team":  "Arsenal",
     "referee":    "Michael Oliver",
     "home_cards": 2,    # yellow + red home (post-match)
     "away_cards": 4,
     "home_fouls": 12,   # opcional
     "away_fouls": 14,
   }, ...]

Disciplina
==========
* PIT estricto: para cada partido objetivo se construyen los features
  usando SOLO partidos con fecha < target_date (la función ingestor
  ya lo aplica internamente).
* Calibration metrics puras (sin scipy): AUC vía Mann-Whitney U,
  reliability curve por buckets de 0.1.
* Output JSON: ``/app/diagnostics/cards_phase1_modelonly.json``.

Usage::

  python scripts/run_cards_phase1_modelonly.py
  python scripts/run_cards_phase1_modelonly.py --dataset /tmp/sample.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_cards_potential import compute_cards_potential  # noqa: E402
from services.football_cards_ingestor import build_cards_features_pit  # noqa: E402

log = logging.getLogger("d8e_paso2_cards_phase1")

DEFAULT_DATASET = Path("/app/data/cards_history/premier_last_4_months.json")
DEFAULT_OUT     = Path("/app/diagnostics/cards_phase1_modelonly.json")
LINES_TO_TEST   = (3.5, 4.5, 5.5)


# ─────────────────────────────────────────────────────────────────────
# Pure metric helpers (no scipy / sklearn)
# ─────────────────────────────────────────────────────────────────────
def _auc(scores: list[float], labels: list[int]) -> float | None:
    """Mann-Whitney U AUC. Ties get half-credit. Returns None if either
    class has zero samples.
    """
    n = len(scores)
    if n == 0 or len(labels) != n:
        return None
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1
            elif p == q:
                ties += 1
    return round((wins + 0.5 * ties) / (len(pos) * len(neg)), 4)


def _brier(scores: list[float], labels: list[int]) -> float | None:
    if not scores:
        return None
    return round(sum((s - y) ** 2 for s, y in zip(scores, labels)) / len(scores), 5)


def _reliability_curve(scores: list[float], labels: list[int],
                       n_buckets: int = 10) -> list[dict]:
    out = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        bs = [(s, y) for s, y in zip(scores, labels) if lo <= s < hi]
        if i == n_buckets - 1:
            bs += [(s, y) for s, y in zip(scores, labels) if s == 1.0]
        if not bs:
            out.append({"bucket": [round(lo, 2), round(hi, 2)], "n": 0,
                        "mean_pred": None, "hit_rate": None})
            continue
        bn = len(bs)
        mp = sum(s for s, _ in bs) / bn
        hr = sum(y for _, y in bs) / bn
        out.append({
            "bucket":    [round(lo, 2), round(hi, 2)],
            "n":         bn,
            "mean_pred": round(mp, 4),
            "hit_rate":  round(hr, 4),
        })
    return out


def _verdict_for_auc(auc: float | None) -> list[str]:
    if auc is None:
        return ["AUC_NOT_COMPUTABLE"]
    if auc >= 0.60:
        return ["AUC_GOOD_JUSTIFIES_PHASE_2"]
    if auc >= 0.55:
        return ["AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2"]
    if auc >= 0.52:
        return ["AUC_WEAK_DO_NOT_PROCEED"]
    return ["AUC_CHANCE_LEVEL_STOP"]


# ─────────────────────────────────────────────────────────────────────
# Evaluation driver
# ─────────────────────────────────────────────────────────────────────
def _evaluate_dataset(
    dataset: list[dict],
    *,
    use_referee_factor: bool,
    lines: tuple[float, ...] = LINES_TO_TEST,
) -> dict:
    """Returns per-line AUC + reliability + sample stats."""
    # We iterate chronologically — but each call to
    # build_cards_features_pit already enforces "history < target_date"
    # using ALL rows except the target by date filtering, so the order
    # is not critical for correctness.
    by_line: dict[float, dict[str, list]] = {
        line: {"scores": [], "labels": [], "ns": []} for line in lines
    }
    n_skipped = 0
    n_used    = 0
    for target in dataset:
        try:
            home_c = int(target.get("home_cards"))
            away_c = int(target.get("away_cards"))
        except (TypeError, ValueError):
            n_skipped += 1
            continue
        total = home_c + away_c
        feats = build_cards_features_pit(target, dataset)
        feats_kw = {k: v for k, v in feats["features"].items()
                    if k != "min_referee_sample" and k != "referee_n_prior"}
        # Inject ablation switch.
        feats_kw["use_referee_factor"] = use_referee_factor
        # Carry referee_n_prior through for the low-sample audit.
        feats_kw["referee_n_prior"] = feats["features"].get("referee_n_prior")

        for line in lines:
            feats_kw["line"] = line
            pred = compute_cards_potential(**feats_kw)
            score = pred["over_cards_probability"]
            label = 1 if total > line else 0  # half-line, no push
            by_line[line]["scores"].append(score)
            by_line[line]["labels"].append(label)
        n_used += 1

    per_line: dict[str, dict] = {}
    for line, blk in by_line.items():
        scores = blk["scores"]
        labels = blk["labels"]
        auc = _auc(scores, labels)
        per_line[str(line)] = {
            "n":                 len(scores),
            "base_rate":         round(sum(labels) / len(labels), 4) if labels else None,
            "auc_model":         auc,
            "brier_model":       _brier(scores, labels),
            "sharpness":         round(sum(scores) / len(scores), 4) if scores else None,
            "reliability_curve": _reliability_curve(scores, labels),
            "verdict_tags":      _verdict_for_auc(auc),
        }
    return {
        "use_referee_factor": use_referee_factor,
        "n_matches_used":     n_used,
        "n_matches_skipped":  n_skipped,
        "per_line":           per_line,
    }


# ─────────────────────────────────────────────────────────────────────
# Ablation diff
# ─────────────────────────────────────────────────────────────────────
def _ablation_diff(no_ref: dict, with_ref: dict,
                   lines: tuple[float, ...] = LINES_TO_TEST) -> dict:
    table = {}
    for line in lines:
        L = str(line)
        a = no_ref["per_line"].get(L, {}).get("auc_model")
        b = with_ref["per_line"].get(L, {}).get("auc_model")
        delta = round(b - a, 4) if (a is not None and b is not None) else None
        table[L] = {
            "auc_without_referee": a,
            "auc_with_referee":    b,
            "delta_auc":           delta,
            "referee_helps":       (delta or 0) > 0.005,
        }
    helps_count = sum(1 for v in table.values() if v["referee_helps"])
    verdict_tags: list[str] = []
    if helps_count == len(lines):
        verdict_tags.append("REFEREE_FACTOR_ADDS_SIGNAL_ALL_LINES")
    elif helps_count == 0:
        verdict_tags.append("REFEREE_FACTOR_DOES_NOT_HELP")
    else:
        verdict_tags.append("REFEREE_FACTOR_MIXED_SIGNAL")
    return {"per_line": table, "verdict_tags": verdict_tags}


# ─────────────────────────────────────────────────────────────────────
# Placeholder when no dataset is available
# ─────────────────────────────────────────────────────────────────────
def _emit_placeholder(out_path: Path, dataset_path: Path) -> int:
    placeholder = {
        "_meta": {
            "sprint": "D8E-PASO-2",
            "status": "PENDING_DATASET",
            "reason": (
                f"No dataset found at {dataset_path}. The cards predictor "
                "and ingestor modules are unit-tested (17/17 passing) and "
                "ready to run as soon as a Premier last-4-months dataset "
                "(home_cards, away_cards, referee, home/away_fouls per "
                "finished match) is provided. POC sample target: ~150 "
                "matches."
            ),
            "how_to_run": [
                "1) Build/scrape the dataset JSON with shape documented at the top of run_cards_phase1_modelonly.py",
                f"2) Place it at {dataset_path}",
                "3) Re-run: python scripts/run_cards_phase1_modelonly.py",
                "4) The script will write the AUC ablation table to /app/diagnostics/cards_phase1_modelonly.json",
            ],
            "verdict_thresholds": {
                "AUC_GOOD_JUSTIFIES_PHASE_2":            ">= 0.60",
                "AUC_MARGINAL_INVESTIGATE_BEFORE_PHASE_2": "0.55–0.60",
                "AUC_WEAK_DO_NOT_PROCEED":               "0.52–0.55",
                "AUC_CHANCE_LEVEL_STOP":                 "< 0.52",
            },
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(placeholder, indent=2), encoding="utf-8")
    print(f"[cards_phase1] No dataset at {dataset_path}.")
    print(f"[cards_phase1] Wrote placeholder to {out_path}.")
    return 0


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--out",     default=str(DEFAULT_OUT))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dataset_path = Path(args.dataset)
    out_path     = Path(args.out)
    if not dataset_path.exists():
        return _emit_placeholder(out_path, dataset_path)

    try:
        with dataset_path.open("r", encoding="utf-8") as fh:
            dataset = json.load(fh)
    except (OSError, ValueError) as exc:
        log.error("dataset read failed: %s", exc)
        return 2
    if not isinstance(dataset, list) or not dataset:
        log.error("dataset is empty or not a list")
        return 2

    print(f"[cards_phase1] Loaded {len(dataset)} matches from {dataset_path}")

    no_ref   = _evaluate_dataset(dataset, use_referee_factor=False)
    with_ref = _evaluate_dataset(dataset, use_referee_factor=True)
    ablation = _ablation_diff(no_ref, with_ref)

    report = {
        "_meta": {
            "sprint":            "D8E-PASO-2",
            "generated_at_utc":  datetime.now(timezone.utc).isoformat(),
            "dataset":           str(dataset_path),
            "n_matches_total":   len(dataset),
            "lines_evaluated":   list(LINES_TO_TEST),
        },
        "run_without_referee": no_ref,
        "run_with_referee":    with_ref,
        "ablation_table":      ablation,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Friendly console table.
    print("=" * 72)
    print("Sprint-D8/E PASO 2 · Cards predictor Fase 1 POC (AUC ablation)")
    print("=" * 72)
    print(f"  N matches: {len(dataset)}")
    print()
    print(f"  {'Line':>6}  {'AUC (no ref)':>14}  {'AUC (with ref)':>16}  "
          f"{'Δ AUC':>8}  Referee helps?")
    for line in LINES_TO_TEST:
        L = str(line)
        a = no_ref["per_line"][L]["auc_model"]
        b = with_ref["per_line"][L]["auc_model"]
        d = ablation["per_line"][L]["delta_auc"]
        helps = ablation["per_line"][L]["referee_helps"]
        print(f"  {line:>6.1f}  {str(a):>14}  {str(b):>16}  {str(d):>8}  {helps}")
    print()
    print(f"  Verdict: {ablation['verdict_tags']}")
    print(f"  Output:  {out_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
