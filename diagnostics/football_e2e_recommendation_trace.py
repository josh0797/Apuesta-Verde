#!/usr/bin/env python3
"""Sprint-D9-E2E-Trace · Diagnóstico reproducible end-to-end del flujo
de recomendaciones football vs lo que ve la UI.

Uso:
    cd /app/backend && python /app/diagnostics/football_e2e_recommendation_trace.py

Outputs:
    /app/diagnostics/output/football_analysis_run_raw.json
        Respuesta cruda de POST /api/analysis/run con national_teams_only=true.
    /app/diagnostics/output/football_ui_feed_raw.json
        Respuesta cruda del/los endpoints que consume la UI para pintar
        dashboard / discarded cards.
    /app/diagnostics/output/football_e2e_diff.json
        Comparación partido-por-partido (match_id, bucket, market, etc.).
    stdout
        Resumen humano-legible con la causa raíz detectada.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Bootstrap path (this script lives in /app/diagnostics, backend in /app/backend).
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

import asyncio  # noqa: E402

import httpx  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services.auth import _make_token  # noqa: E402

OUTPUT_DIR = Path("/app/diagnostics/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

PAYLOAD = {
    "sport": "football",
    "national_teams_only": True,
    "refresh": True,
    "max_matches": 30,
    "background": False,
}

# Sprint-D9-E2E-Trace · partidos focales solicitados por el usuario.
# Si están presentes en el feed, los resaltamos al final del reporte humano.
FOCUS_MATCHES = [
    ("Argentina", "Austria"),
    ("Uruguay",   "Cabo Verde"),
    ("Uruguay",   "Cape Verde"),
    ("Nueva Zelanda", "Egipto"),
    ("New Zealand",   "Egypt"),
]


def _is_focus_match(label: str) -> bool:
    if not label:
        return False
    L = label.lower()
    for h, a in FOCUS_MATCHES:
        if h.lower() in L and a.lower() in L:
            return True
    return False


def _safe_dump(obj, path: Path) -> None:
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"  ✍️  wrote {path.relative_to(Path('/app'))}")


async def _get_jwt() -> tuple[str, dict]:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "test_database")]
    user = await db.users.find_one({"email": "demo@valuebet.app"}) \
            or await db.users.find_one({})
    if not user:
        raise RuntimeError("No user in db.users. Seed demo@valuebet.app first.")
    return _make_token(user["id"], user["email"]), user


def _match_key(entry: dict) -> str:
    """Stable key for cross-bucket join."""
    mid = entry.get("match_id")
    if mid is not None:
        return str(mid)
    lbl = entry.get("match_label") or "?"
    return f"label::{lbl}"


def _flatten_buckets(summary: dict) -> dict[str, dict]:
    """Devuelve {match_key: {bucket, payload}} a partir del summary."""
    out: dict[str, dict] = {}
    bucket_order = [
        "high_confidence", "medium_confidence", "rescued_picks",
        "protected_acceptable", "watchlist", "watchlist_odds_needed",
        "discarded_motivation", "discarded_market", "incomplete_data",
        "skipped_low_relevance",
    ]
    for bk in bucket_order:
        for it in (summary.get(bk) or []):
            if not isinstance(it, dict):
                continue
            key = _match_key(it)
            if key in out:
                # Prefer the first bucket (which is highest priority in order).
                continue
            out[key] = {"bucket": bk, "payload": it}
    return out


def _extract_market_fingerprint(it: dict) -> dict:
    rec = it.get("recommendation") or {}
    edge = it.get("_market_edge") or {}
    me   = it.get("market_selection") or {}
    return {
        "market":          rec.get("market") or me.get("market_name") or it.get("market") or "unknown",
        "selection":       rec.get("selection") or it.get("selection") or me.get("selection"),
        "odds":            rec.get("odds_range") or it.get("odds_range") or it.get("odds_used"),
        "confidence":      rec.get("confidence_score") or it.get("confidence"),
        "edge":            edge.get("edge") or it.get("edge"),
        "estimated_prob":  edge.get("estimated_prob") or it.get("estimated_probability"),
        "implied_prob":    edge.get("implied_prob")    or it.get("implied_probability"),
        "reason":          it.get("reason") or it.get("discard_reason"),
        "classification":  (it.get("_moneyball") or {}).get("classification"),
    }


def _detect_unknown_pollution(it: dict) -> list[str]:
    """Detecta los marcadores específicos que dispararon el reporte del usuario:
       SPORTYTRADER NO ENCONTRADO + Mercado desconocido + sin cuota."""
    flags = []
    rec = it.get("recommendation") or {}
    fp = _extract_market_fingerprint(it)
    if (fp["market"] or "").strip().lower() in ("unknown", "mercado desconocido", "—", ""):
        flags.append("MARKET_LABEL_UNKNOWN")
    if not fp["odds"]:
        flags.append("ODDS_MISSING")
    if fp["edge"] is None:
        flags.append("EDGE_MISSING")
    # Reasons referencing sportytrader
    blob = json.dumps(it, default=str).lower()
    if "sportytrader" in blob and ("not_found" in blob or "not found" in blob or "no encontrado" in blob.lower()):
        flags.append("SPORTYTRADER_NOT_FOUND_REFERENCED")
    if "unknown" in (fp.get("reason") or "").lower():
        flags.append("DISCARD_REASON_UNKNOWN")
    if (fp.get("classification") or "").lower() in ("market_unknown", "no_bet_value", ""):
        if fp["market"] in ("unknown", "Mercado desconocido", None, ""):
            flags.append("CLASSIFICATION_VS_MARKET_INCONSISTENT")
    return flags


async def _post_analysis_run(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=180.0) as c:
        r = await c.post(f"{BASE_URL}/api/analysis/run", json=PAYLOAD, headers=headers)
        try:
            return {"status_code": r.status_code, "body": r.json()}
        except Exception:
            return {"status_code": r.status_code, "body": {"_raw_text": r.text[:1000]}}


async def _poll_job(token: str, job_id: str, *, max_wait_s: int = 240) -> dict:
    """Sprint-D9 · cuando /api/analysis/run auto-promueve a background
    (max_matches > 4), hacemos polling al endpoint de jobs hasta que el
    stage sea 'completed' o 'failed'. Devuelve el body final con `result`."""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = asyncio.get_event_loop().time() + max_wait_s
    last_stage: Optional[str] = None
    async with httpx.AsyncClient(timeout=30.0) as c:
        while True:
            r = await c.get(f"{BASE_URL}/api/analysis/jobs/{job_id}", headers=headers)
            try:
                body = r.json()
            except Exception:
                body = {"_raw_text": r.text[:600]}
            stage = (body or {}).get("stage") or (body or {}).get("status")
            progress = (body or {}).get("progress")
            if stage != last_stage:
                print(f"  job stage={stage!r}  progress={progress}")
                last_stage = stage
            if stage in ("completed", "failed", "error", "done"):
                return {"status_code": r.status_code, "body": body}
            if asyncio.get_event_loop().time() > deadline:
                print(f"  ⚠️  polling timed out after {max_wait_s}s (last stage={stage!r})")
                return {"status_code": r.status_code, "body": body}
            await asyncio.sleep(4.0)


async def _fetch_ui_feeds(token: str, pick_run_id: str | None) -> dict:
    """Replica las llamadas que hace la UI tras un /analysis/run:
       - GET /api/picks/today?sport=football       (consumido por DashboardPage)
       - GET /api/picks/run/{pick_run_id}          (detalle del run)
       - GET /api/analysis/jobs/{pick_run_id}      (status del job; útil cuando
         el run fue auto-promovido a background)
    """
    headers = {"Authorization": f"Bearer {token}"}
    out: dict[str, object] = {}
    candidates = [
        ("picks_today_football",         "/api/picks/today?sport=football"),
        ("picks_run_by_id",              f"/api/picks/run/{pick_run_id}"     if pick_run_id else None),
        ("analysis_jobs_by_id",          f"/api/analysis/jobs/{pick_run_id}" if pick_run_id else None),
    ]
    async with httpx.AsyncClient(timeout=60.0) as c:
        for label, url_path in candidates:
            if not url_path:
                continue
            try:
                r = await c.get(BASE_URL + url_path, headers=headers)
                out[label] = {
                    "url": url_path,
                    "status_code": r.status_code,
                    "body": (
                        r.json() if r.headers.get("content-type","").startswith("application/json")
                        else {"_raw_text": r.text[:600]}
                    ),
                }
            except Exception as exc:
                out[label] = {"url": url_path, "status_code": None,
                                "error": f"{type(exc).__name__}: {exc}"}
    return out


def _extract_summary_from_any(payload: dict) -> dict:
    """Sprint-D9 · helper resiliente para localizar ``summary`` dentro de
    los muchos wrappers en los que aparece en este codebase:

    * `/api/analysis/run` (síncrono)  →  body.result.summary
    * `/api/analysis/jobs/{id}`       →  body.result.result.summary   (job doc)
    * `/api/picks/run/{id}`           →  body.result.summary  *o*  body.summary
    * `/api/picks/today`              →  body.summary
    """
    if not isinstance(payload, dict):
        return {}
    candidates = [
        payload,
        payload.get("result"),
        (payload.get("result") or {}).get("result") if isinstance(payload.get("result"), dict) else None,
        payload.get("payload"),
    ]
    for c in candidates:
        if isinstance(c, dict) and isinstance(c.get("summary"), dict):
            return c["summary"]
    return {}


def _build_diff(backend: dict, ui_feed: dict) -> dict:
    backend_summary = _extract_summary_from_any(backend.get("body") or {})
    backend_idx = _flatten_buckets(backend_summary)

    # Heuristics: la UI usa `result.summary` del MISMO analysis/run object
    # tras llegar al cliente; pero tras un refresh subsiguiente puede leer
    # de una colección distinta. Recogemos el "summary" de cualquier
    # endpoint UI que devolviera algo.
    ui_idx: dict[str, dict] = {}
    for label, payload in ui_feed.items():
        body = (payload or {}).get("body") or {}
        summ = _extract_summary_from_any(body)
        if not summ:
            continue
        for k, v in _flatten_buckets(summ).items():
            ui_idx.setdefault(k, {**v, "_via": label})

    all_keys = set(backend_idx) | set(ui_idx)
    per_match = []
    for k in sorted(all_keys):
        b = backend_idx.get(k) or {}
        u = ui_idx.get(k) or {}
        b_fp = _extract_market_fingerprint(b.get("payload") or {})
        u_fp = _extract_market_fingerprint(u.get("payload") or {}) if u else {}
        anomalies = _detect_unknown_pollution(b.get("payload") or {})
        per_match.append({
            "match_key": k,
            "match_label": (
                (b.get("payload") or {}).get("match_label")
                or (u.get("payload") or {}).get("match_label")
                or "?"
            ),
            "backend_analysis_run": {
                "bucket":     b.get("bucket"),
                **b_fp,
            },
            "ui_feed": {
                "via":        u.get("_via"),
                "bucket":     u.get("bucket"),
                **u_fp,
            } if u else None,
            "diff": {
                "bucket_changed":      bool(u) and (b.get("bucket") != u.get("bucket")),
                "market_label_lost":   (b_fp.get("market") not in (None, "unknown")) and (u_fp.get("market") in (None, "unknown")),
                "odds_lost":           bool(b_fp.get("odds")) and not u_fp.get("odds"),
                "anomalies_detected_in_backend": anomalies,
            },
        })
    return {
        "backend_bucket_counts": {k: len(backend_summary.get(k) or []) for k in (
            "high_confidence","medium_confidence","rescued_picks",
            "protected_acceptable","watchlist","watchlist_odds_needed",
            "discarded_motivation","discarded_market","incomplete_data",
        )},
        "per_match": per_match,
    }


async def main():
    print("== Sprint-D9 E2E Trace ==")
    print(f"  base_url:  {BASE_URL}")
    print(f"  payload:   {PAYLOAD}")

    token, user = await _get_jwt()
    print(f"  jwt_user:  {user.get('email')}")

    print("\n-- step 1: POST /api/analysis/run --")
    backend_resp = await _post_analysis_run(token)
    print(f"  HTTP {backend_resp['status_code']}")

    # Sprint-D9 · si el backend auto-promovió a background (max_matches>4),
    # ``body`` contendrá `job_id` + `_auto_promoted=true` sin `result`.
    # Hacemos polling explícito.
    body0 = backend_resp.get("body") or {}
    job_id = body0.get("job_id")
    if body0.get("_auto_promoted") and job_id:
        print(f"  ↻ analysis run auto-promoted to background (job_id={job_id}); polling...")
        backend_resp = await _poll_job(token, job_id, max_wait_s=240)
        print(f"  poll done HTTP {backend_resp['status_code']}")

    _safe_dump(backend_resp, OUTPUT_DIR / "football_analysis_run_raw.json")

    body_final = backend_resp.get("body") or {}
    pick_run_id = (
        body_final.get("pick_run_id")
        or (body_final.get("result") or {}).get("pick_run_id")
        or job_id
    )
    print(f"  pick_run_id: {pick_run_id}")

    print("\n-- step 2: fetch UI feed endpoints --")
    ui_feed = await _fetch_ui_feeds(token, pick_run_id)
    for label, payload in ui_feed.items():
        st = payload.get("status_code")
        print(f"  {label:36s}  HTTP {st}  {payload.get('url')}")
    _safe_dump(ui_feed, OUTPUT_DIR / "football_ui_feed_raw.json")

    print("\n-- step 3: diff backend vs UI --")
    diff = _build_diff(backend_resp, ui_feed)
    _safe_dump(diff, OUTPUT_DIR / "football_e2e_diff.json")

    # Resumen humano
    print("\n=== SUMMARY ===")
    print(f"Backend buckets: {diff['backend_bucket_counts']}")
    print()
    for m in diff["per_match"]:
        be = m["backend_analysis_run"]
        ui = m["ui_feed"]
        anoms = m["diff"]["anomalies_detected_in_backend"]
        print(f"  {m['match_label']}:")
        print(f"     backend  bucket={be.get('bucket')!r}  market={be.get('market')!r}  conf={be.get('confidence')}  edge={be.get('edge')}")
        if ui:
            print(f"     ui_feed  bucket={ui.get('bucket')!r}  market={ui.get('market')!r}  (via {ui.get('via')})")
        else:
            print(f"     ui_feed  (NOT FOUND in any UI feed endpoint)")
        if anoms:
            print(f"     anomalies: {anoms}")

    # Detectar root cause
    print("\n=== ROOT CAUSE HEURISTICS ===")
    root_causes: list[str] = []
    for m in diff["per_match"]:
        anoms = m["diff"]["anomalies_detected_in_backend"]
        if "MARKET_LABEL_UNKNOWN" in anoms and m["backend_analysis_run"]["bucket"] in (
            "discarded_market", "discarded_motivation", "incomplete_data",
        ):
            root_causes.append(
                f"Backend persisted '{m['match_label']}' in bucket "
                f"'{m['backend_analysis_run']['bucket']}' with market='unknown' "
                "→ UI renders 'Mercado desconocido' literally"
            )
    if not root_causes:
        print("  No 'unknown' market pollution detected in current run.")
    else:
        for rc in root_causes:
            print(f"  - {rc}")

    # Focus-match callout — comparación específica con los partidos
    # solicitados por el usuario (Argentina-Austria / Uruguay-Cabo Verde /
    # Nueva Zelanda-Egipto). Si no aparecen en el feed lo decimos
    # explícitamente para no maquillar el reporte.
    print("\n=== FOCUS MATCHES (Argentina-Austria, Uruguay-Cabo Verde, NZ-Egypt) ===")
    focus_hits = [m for m in diff["per_match"] if _is_focus_match(m["match_label"])]
    if not focus_hits:
        print("  ⚠️  Ninguno de los 3 partidos focales aparece en el feed actual.")
        print("      Esto puede deberse a que no estén dentro del horizonte de 48h")
        print("      del cascade de ingesta, o a un fallo upstream. Revisar logs.")
    else:
        for m in focus_hits:
            be = m["backend_analysis_run"]
            ui = m["ui_feed"]
            print(f"  ✅ {m['match_label']}")
            print(f"     backend bucket={be.get('bucket')!r} market={be.get('market')!r}"
                  f" conf={be.get('confidence')} edge={be.get('edge')}")
            if ui:
                bucket_ok = "OK" if be.get("bucket") == ui.get("bucket") else "MISMATCH"
                market_ok = "OK" if (be.get("market") or "") == (ui.get("market") or "") else "MISMATCH"
                print(f"     ui_feed bucket={ui.get('bucket')!r} market={ui.get('market')!r}"
                      f"  bucket_parity={bucket_ok}  market_parity={market_ok}")
            else:
                print("     ui_feed: NOT FOUND in any UI feed endpoint")
            if m["diff"]["anomalies_detected_in_backend"]:
                print(f"     anomalies: {m['diff']['anomalies_detected_in_backend']}")

    print("\nDone. Outputs:")
    for p in OUTPUT_DIR.iterdir():
        print(f"  - {p}")


if __name__ == "__main__":
    asyncio.run(main())
