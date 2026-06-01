"""StatBunker diagnostic probe.

Fetches the 3 canonical URLs the user pointed us to (LeagueTable / GoalsFor /
GoalsAgainst for La Liga 25/26 = comp_id=777), records DOM signals and prints
a structured diagnostic report so we can validate the real HTML structure
before wiring the enrichment to the pipeline.

Run:
    cd /app/backend && python scripts/probe_statbunker.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# Allow direct execution from /app/backend
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from services.brightdata_client import fetch_unlocked

OUT_DIR = "/tmp/statbunker_probe"
os.makedirs(OUT_DIR, exist_ok=True)


PROBE_URLS = [
    # User-provided canonical URLs
    ("LeagueTable_LaLiga_2526",  "https://www.statbunker.com/competitions/LeagueTable?comp_id=777"),
    ("GoalsFor_LaLiga_2526",     "https://www.statbunker.com/competitions/GoalsFor?comp_id=777"),
    ("GoalsAgainst_LaLiga_2526", "https://www.statbunker.com/competitions/GoalsAgainst?comp_id=777"),
    # Alt domain fallback
    ("GoalsFor_no_www",          "https://statbunker.com/competitions/GoalsFor?comp_id=777"),
    # Sanity check with EPL 25/26
    ("GoalsFor_EPL_2526",        "https://www.statbunker.com/competitions/GoalsFor?comp_id=776"),
    # Previous-season cross-check
    ("GoalsFor_LaLiga_2425",     "https://www.statbunker.com/competitions/GoalsFor?comp_id=731"),
    # Commentary URL provided
    ("Commentary_Osasuna_Oviedo","https://www.statbunker.com/competitions/LiveFootballCommentary/La-Liga/Osasuna-VS-Real-Oviedo?comp_id=777&date=17-Jan-2026&match_id=128235"),
]


def _extract_title(body: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", body, re.IGNORECASE)
    return (m.group(1).strip() if m else "")[:140]


def _extract_tables(body: str) -> list[dict]:
    """Find each <table>, extract its first header row + first 3 data rows."""
    tables = []
    for tbl_match in re.finditer(r"<table[^>]*>(.*?)</table>", body, re.IGNORECASE | re.DOTALL):
        raw = tbl_match.group(1)
        # Pull rows
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.IGNORECASE | re.DOTALL)
        if not rows:
            continue
        header_cells = []
        data_rows = []
        for ri, row in enumerate(rows):
            # th cells first (header)
            ths = re.findall(r"<th[^>]*>(.*?)</th>", row, re.IGNORECASE | re.DOTALL)
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.IGNORECASE | re.DOTALL)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in (ths or tds)]
            cells = [re.sub(r"\s+", " ", c) for c in cells if c.strip()]
            if not cells:
                continue
            if ths and not header_cells:
                header_cells = cells
            elif tds:
                if len(data_rows) < 3:
                    data_rows.append(cells)
        if header_cells or data_rows:
            tables.append({
                "header": header_cells,
                "first_rows": data_rows,
                "row_count": len(rows),
            })
    return tables


async def probe_one(label: str, url: str) -> dict:
    started = datetime.now(timezone.utc)
    try:
        body = await asyncio.wait_for(fetch_unlocked(url), timeout=25.0)
    except asyncio.TimeoutError:
        return {"label": label, "url": url, "ok": False, "error": "timeout"}
    except Exception as exc:
        return {"label": label, "url": url, "ok": False, "error": str(exc)[:200]}

    if not body:
        return {"label": label, "url": url, "ok": False, "error": "empty_body"}

    out_path = os.path.join(OUT_DIR, f"{label}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)

    title = _extract_title(body)
    is_404 = "404" in title or "Page Not Found" in body[:500]
    tables = _extract_tables(body)

    # Look for goal-timing signals in the body
    signals = {
        s: (s in body) for s in (
            "F15", "L10", "FH", "SH",
            "First 15", "First Half", "Last 10", "Second Half",
            "GoalsFor", "GoalsAgainst", "Pld",
        )
    }

    return {
        "label":          label,
        "url":            url,
        "ok":             not is_404,
        "fetched_at":     started.isoformat(),
        "content_length": len(body),
        "title":          title,
        "is_404":         is_404,
        "tables_found":   len(tables),
        "tables_summary": [
            {
                "header":     t["header"][:14],
                "row_count":  t["row_count"],
                "sample_row": t["first_rows"][0] if t["first_rows"] else [],
            }
            for t in tables[:5]
        ],
        "signal_hits": {k: v for k, v in signals.items() if v},
        "html_path":   out_path,
    }


async def main():
    print("=" * 78)
    print("STATBUNKER DIAGNOSTIC PROBE")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print(f"Output:  {OUT_DIR}/")
    print("=" * 78)

    results = []
    for label, url in PROBE_URLS:
        print(f"\n▶ Probing {label}")
        print(f"  URL: {url}")
        result = await probe_one(label, url)
        results.append(result)

        if not result.get("ok"):
            err = result.get("error", "404/empty")
            print(f"  ❌ FAIL: {err}  (title={result.get('title','-')!r})")
            continue

        print(f"  ✅ {result['content_length']} chars  title={result['title']!r}")
        print(f"     tables_found={result['tables_found']}")
        print(f"     signals: {sorted(result['signal_hits'].keys())}")
        for i, t in enumerate(result.get("tables_summary", [])[:3]):
            print(f"     table[{i}] header={t['header']}")
            print(f"               sample={t['sample_row']}")

    # Final report
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"{'Label':<28} {'OK?':<6} {'Len':<10} {'Tables':<8} {'Signals'}")
    for r in results:
        ok = "✓" if r.get("ok") else "✗"
        ln = r.get("content_length", 0)
        tb = r.get("tables_found", 0)
        sg = ",".join(sorted(r.get("signal_hits") or {}.keys()))[:50]
        print(f"{r['label']:<28} {ok:<6} {ln:<10} {tb:<8} {sg}")


if __name__ == "__main__":
    asyncio.run(main())
