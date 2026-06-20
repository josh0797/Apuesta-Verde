"""Sprint-D8/E PASO 0 · CLI runner for goals-3.5 closure.

Reads the model-only diagnostics produced by Sprint-D8 Fase 1
(``/app/diagnostics/calibration_{over|under}_3_5_{scope}_modelonly.json``),
feeds them into the pure closure module and writes the verdict to
``/app/diagnostics/sprint_d8e_goals_3_5_closure.json``.

Usage::

    python scripts/run_goals_3_5_close.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.football_goals_3_5_closure import (  # noqa: E402
    evaluate_goals_3_5_closure,
)

log = logging.getLogger("d8e_paso0_close_goals_3_5")

DEFAULT_DIAG_DIR = Path("/app/diagnostics")
SCOPES = ("premier_2425", "top5_2425", "premier_multiseason")
MARKETS = ("over", "under")


def _read_modelonly(diag_dir: Path) -> list[dict]:
    records: list[dict] = []
    missing: list[str] = []
    for market in MARKETS:
        for scope in SCOPES:
            fname = f"calibration_{market}_3_5_{scope}_modelonly.json"
            p = diag_dir / fname
            if not p.exists():
                missing.append(fname)
                continue
            try:
                with p.open("r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError) as exc:
                log.warning("could not read %s: %s", fname, exc)
                continue
            # The closure module expects market+scope+auc_model+n_records.
            # The diagnostic JSONs already carry those keys.
            doc.setdefault("scope", scope)
            records.append(doc)
    if missing:
        log.info("missing model-only inputs (skipped): %s", ", ".join(missing))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--diag-dir", default=str(DEFAULT_DIAG_DIR),
        help="Folder containing the model-only diagnostic JSONs.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output JSON path (default: <diag-dir>/sprint_d8e_goals_3_5_closure.json)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    diag_dir = Path(args.diag_dir)
    if not diag_dir.exists():
        log.error("diag-dir does not exist: %s", diag_dir)
        return 2

    records = _read_modelonly(diag_dir)
    if not records:
        log.error("no model-only diagnostic JSONs found in %s", diag_dir)
        return 2

    closure = evaluate_goals_3_5_closure(records)
    closure["_meta"] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "diag_dir":         str(diag_dir),
        "sprint":           "D8E-PASO-0",
        "records_read":     len(records),
    }

    out_path = Path(args.out) if args.out else diag_dir / "sprint_d8e_goals_3_5_closure.json"
    out_path.write_text(json.dumps(closure, indent=2), encoding="utf-8")

    # Friendly console summary.
    print("=" * 64)
    print("Sprint-D8/E PASO 0 · Goals 3.5 closure")
    print("=" * 64)
    print(f"Verdict:                 {closure['verdict']}")
    print(f"Reason codes:            {closure['reason_codes']}")
    print(f"Market data available:   {closure['market_data_available']}")
    over_s  = closure["over_summary"]
    under_s = closure["under_summary"]
    print()
    print(f"OVER_3_5 AUCs: {over_s['auc_values']} "
          f"(max={over_s['auc_max']}, disp={over_s['auc_dispersion']})")
    print(f"UNDER_3_5 AUCs: {under_s['auc_values']} "
          f"(max={under_s['auc_max']}, disp={under_s['auc_dispersion']})")
    print()
    print(f"Output written to: {out_path}")
    print("=" * 64)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
