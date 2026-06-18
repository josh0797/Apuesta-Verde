"""Sprint F.2 — capture live 365Scores Top Trends payload via Scrape.do.

One-shot fixture capture. Saves the raw JSON body to
``tests/fixtures/365scores_top_trends_4627854.json`` for offline parser
tests, AND prints a status summary. Idempotent: if the fixture file
already exists and ``--force`` is not provided, the script aborts.

This script also doubles as a transport smoke-test: if Scrape.do can
hit ``webws.365scores.com/web/trends/`` from this container the live
ingestion will work too.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Required for sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from services.scrape_do_client import fetch_via_scrapedo_result, is_enabled


DEFAULT_ENDPOINT = (
    "https://webws.365scores.com/web/trends/"
    "?appTypeId=5&langId=1&timezoneName=UTC"
    "&userCountryId=333&games=4627854&topBookmaker=103"
)
DEFAULT_FIXTURE_PATH = Path(
    "/app/backend/tests/fixtures/365scores_top_trends_4627854.json"
)


async def capture(endpoint: str, dest: Path, *, force: bool) -> int:
    if dest.exists() and not force:
        print(f"[skip] fixture already exists: {dest}")
        return 0
    if not is_enabled():
        print("[FAIL] SCRAPEDO_TOKEN missing — cannot reach the endpoint via Scrape.do.")
        return 1
    print(f"[GET] {endpoint}")
    res = await fetch_via_scrapedo_result(endpoint, timeout=45.0,
                                            render=False, geo="mx")
    print(f"[status] ok={res.get('ok')} status={res.get('status_code')} "
          f"reason={res.get('reason_code')}")
    if not res.get("ok"):
        print(f"[FAIL] {res}")
        return 2
    body = res.get("html") or ""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        print(f"[FAIL] JSON parse: {exc}")
        return 3
    if not isinstance(payload, dict) or "trends" not in payload:
        print(f"[FAIL] unexpected schema. top-level keys: "
              f"{list(payload.keys()) if isinstance(payload, dict) else type(payload)}")
        return 4
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[ok] fixture saved: {dest} ({len(body)} bytes, "
          f"{len(payload.get('trends', []))} trends)")
    # Print 3 first text samples for sanity.
    for t in payload.get("trends", [])[:3]:
        print(f"     - lineTypeId={t.get('lineTypeId')} "
              f"isTop={t.get('isTop')} text={t.get('text')!r}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--dest", default=str(DEFAULT_FIXTURE_PATH))
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    rc = asyncio.run(capture(args.endpoint, Path(args.dest), force=args.force))
    sys.exit(rc)


if __name__ == "__main__":
    main()
