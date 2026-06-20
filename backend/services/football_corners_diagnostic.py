"""Sprint-D8/E PASO 1 · Football corners scraper **diagnostic** (3-layer).

Goal
====
Diagnosticar **dónde** falla la cascada de córners de 365Scores
(transporte vs endpoint vs parser) usando un único módulo que
instrumenta los dos endpoints relevantes del API ``webws.365scores``:

  * ``/web/game/?gameId=...``            ← ``fetch_game_detail``
  * ``/web/game/stats/?gameId=...``      ← ``fetch_game_stats``

El usuario ya verificó (lectura de código) que la implementación
actual NO scrapea la URL HTML (``www.365scores.com/es/football/match/``).
La cuestión empírica es: **¿el endpoint ``/game/`` trae el bloque
``statistics`` con córners, o sólo lo trae ``/game/stats/``?**
Ese descubrimiento decide qué endpoint hay que llamar en producción.

Disciplina
==========
* **Función pura asincrónica**: recibe los fetchers como argumentos
  (DI) → los tests usan mocks sin scrape.do.
* **3 niveles de instrumentación**: transporte, presencia, parser.
  Cada nivel reporta sus métricas en su sub-bloque del output.
* **Verdict ranged**: identifica la primera capa que falló
  (TRANSPORT / ENDPOINT_NO_CORNERS_KEY / PARSER), o ``OK`` si ambos
  endpoints entregan córners correctamente.
* **Fail-soft total**: cualquier excepción se atrapa y se reporta en
  ``error`` del nivel correspondiente.
* **Sin red en tests**: los tests inyectan mocks vía ``fetch_detail``
  y ``fetch_stats``.

Output (dict)
=============
``{
   "game_id":             "...",
   "layers": {
      "transport":  {detail: {...}, stats: {...}},
      "endpoint":   {detail: {...}, stats: {...}},
      "parser":     {detail: {...}, stats: {...}},
   },
   "verdict":      "OK" | "TRANSPORT_FAILURE" | "ENDPOINT_NO_CORNERS_KEY"
                                | "PARSER_FAILURE",
   "winning_endpoint": "detail" | "stats" | None,
   "reason_codes": [...],
   "ground_truth": {... optional ...}
}``
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("services.football_corners_diagnostic")

# Same alias set the production normalizer uses (kept inline to avoid
# coupling the diagnostic to private state of score365_client).
CORNER_KEY_SUBSTRINGS = (
    "corner", "corners",
    "corner kick", "corner kicks",
    "corner_kicks", "total corners", "corners total",
    "córner", "corneres",  # Spanish localisation
)

# Reason codes
RC_TRANSPORT_FAILED            = "TRANSPORT_FAILED"
RC_TRANSPORT_OK                = "TRANSPORT_OK"
RC_PAYLOAD_EMPTY               = "PAYLOAD_EMPTY"
RC_CORNERS_KEY_NOT_FOUND       = "CORNERS_KEY_NOT_FOUND_IN_PAYLOAD"
RC_CORNERS_KEY_PRESENT         = "CORNERS_KEY_PRESENT_IN_PAYLOAD"
RC_PARSER_OK                   = "PARSER_EXTRACTED_CORNERS"
RC_PARSER_FAILED               = "PARSER_FAILED_TO_EXTRACT"
RC_GROUND_TRUTH_MATCH          = "GROUND_TRUTH_MATCH"
RC_GROUND_TRUTH_MISMATCH       = "GROUND_TRUTH_MISMATCH"


# ─────────────────────────────────────────────────────────────────────
# Level 2 helper — find corner-like keys recursively in any JSON payload
# ─────────────────────────────────────────────────────────────────────
def find_corner_paths(payload: Any, *,
                       max_depth: int = 8) -> list[dict]:
    """Return a list of ``{path, key, value_preview}`` for every node
    in ``payload`` whose key (or ``name`` field, when the node is a
    stat dict) matches a corner alias.

    The function is depth-bounded to keep the diagnostic fast even on
    very deep payloads.
    """
    found: list[dict] = []

    def _matches_corner(s: str) -> bool:
        if not isinstance(s, str):
            return False
        lo = s.lower().strip()
        return any(sub in lo for sub in CORNER_KEY_SUBSTRINGS)

    def _walk(node: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if _matches_corner(k):
                    found.append({
                        "path":  f"{path}.{k}",
                        "via":   "dict_key",
                        "key":   k,
                        "value_preview": _preview(v),
                    })
                # Special-case stat entries with ``name`` field.
                if k == "name" and _matches_corner(v):
                    found.append({
                        "path":  path,
                        "via":   "stat_name_field",
                        "key":   "name",
                        "value_preview": _preview(node),
                    })
                _walk(v, f"{path}.{k}" if path else k, depth + 1)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]", depth + 1)

    _walk(payload, "", 0)
    return found


def _preview(v: Any, max_len: int = 120) -> str:
    """Short, safe string preview of any JSON value."""
    try:
        s = str(v)
    except Exception:  # noqa: BLE001
        return "<unrepr>"
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


# ─────────────────────────────────────────────────────────────────────
# Level 1 — Transport
# ─────────────────────────────────────────────────────────────────────
async def _layer1_transport(
    fetcher: Callable[..., Awaitable[Any]],
    game_id: str,
    label: str,
    timeout_s: float,
) -> dict:
    """Call the fetcher; report status/ok/body length."""
    out: dict[str, Any] = {
        "endpoint":   label,
        "ok":         False,
        "raw":        None,
        "raw_kind":   None,
        "raw_size":   0,
        "elapsed_ms": None,
        "error":      None,
        "reason_codes": [],
    }
    loop = asyncio.get_event_loop()
    start = loop.time()
    try:
        raw = await asyncio.wait_for(fetcher(game_id), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        out["error"] = f"timeout after {timeout_s}s"
        out["reason_codes"].append(RC_TRANSPORT_FAILED)
        log.debug("[corners_diag][%s] transport timeout: %s", label, exc)
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        out["reason_codes"].append(RC_TRANSPORT_FAILED)
        log.debug("[corners_diag][%s] transport raised: %s", label, exc)
        return out
    finally:
        out["elapsed_ms"] = round((loop.time() - start) * 1000.0, 1)

    out["raw"] = raw
    out["raw_kind"] = type(raw).__name__
    try:
        if isinstance(raw, (dict, list)):
            import json as _json
            out["raw_size"] = len(_json.dumps(raw))
        elif raw is None:
            out["raw_size"] = 0
        else:
            out["raw_size"] = len(str(raw))
    except Exception:  # noqa: BLE001
        out["raw_size"] = -1

    if raw is None or raw == {} or raw == []:
        out["reason_codes"].append(RC_PAYLOAD_EMPTY)
        out["ok"] = False
    else:
        out["ok"] = True
        out["reason_codes"].append(RC_TRANSPORT_OK)
    return out


# ─────────────────────────────────────────────────────────────────────
# Level 2 — Endpoint payload presence
# ─────────────────────────────────────────────────────────────────────
def _layer2_endpoint(transport_out: dict) -> dict:
    """Search the transport payload for corner-like keys."""
    out: dict[str, Any] = {
        "endpoint":   transport_out.get("endpoint"),
        "paths":      [],
        "n_paths":    0,
        "reason_codes": [],
    }
    if not transport_out.get("ok") or transport_out.get("raw") is None:
        out["reason_codes"].append(RC_PAYLOAD_EMPTY)
        return out
    paths = find_corner_paths(transport_out["raw"])
    out["paths"]   = paths
    out["n_paths"] = len(paths)
    if paths:
        out["reason_codes"].append(RC_CORNERS_KEY_PRESENT)
    else:
        out["reason_codes"].append(RC_CORNERS_KEY_NOT_FOUND)
    return out


# ─────────────────────────────────────────────────────────────────────
# Level 3 — Parser extraction
# ─────────────────────────────────────────────────────────────────────
def _layer3_parser(transport_out: dict,
                   normalizer: Callable[[dict], dict] | None,
                   *, ground_truth: Optional[dict] = None) -> dict:
    """Run the production normalizer on the payload and report what came out.

    ``ground_truth``, when provided, lets the diagnostic flag
    parser-vs-real mismatches even if the normalizer returned something.
    """
    out: dict[str, Any] = {
        "endpoint":      transport_out.get("endpoint"),
        "normalizer_ran": False,
        "available":     False,
        "home_corners":  None,
        "away_corners":  None,
        "total_corners": None,
        "raw_stat_names": [],
        "reason_codes":  [],
        "error":         None,
    }
    if not transport_out.get("ok") or transport_out.get("raw") is None:
        out["reason_codes"].append(RC_PAYLOAD_EMPTY)
        return out
    if normalizer is None:
        out["error"] = "no normalizer wired"
        return out
    try:
        normalised = normalizer(transport_out["raw"])
        out["normalizer_ran"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"normalizer raised: {exc}"
        out["reason_codes"].append(RC_PARSER_FAILED)
        return out

    out["available"]     = bool(normalised.get("available"))
    out["raw_stat_names"] = list(normalised.get("raw_stat_names") or [])
    home_block = normalised.get("home") or {}
    away_block = normalised.get("away") or {}
    out["home_corners"]  = home_block.get("corners")
    out["away_corners"]  = away_block.get("corners")
    out["total_corners"] = normalised.get("total_corners")

    if out["available"] and out["total_corners"] is not None:
        out["reason_codes"].append(RC_PARSER_OK)
    else:
        out["reason_codes"].append(RC_PARSER_FAILED)

    # Ground truth comparison, when provided.
    if ground_truth and out["total_corners"] is not None:
        gt_total = ground_truth.get("total_corners")
        gt_home  = ground_truth.get("home_corners")
        gt_away  = ground_truth.get("away_corners")
        match = True
        if gt_total is not None and gt_total != out["total_corners"]:
            match = False
        if gt_home is not None and out["home_corners"] is not None \
                and gt_home != out["home_corners"]:
            match = False
        if gt_away is not None and out["away_corners"] is not None \
                and gt_away != out["away_corners"]:
            match = False
        out["reason_codes"].append(
            RC_GROUND_TRUTH_MATCH if match else RC_GROUND_TRUTH_MISMATCH,
        )

    return out


# ─────────────────────────────────────────────────────────────────────
# Diagnostic orchestrator
# ─────────────────────────────────────────────────────────────────────
async def diagnose_corners_pipeline(
    game_id: str,
    *,
    fetch_detail: Callable[[str], Awaitable[Any]],
    fetch_stats:  Callable[[str], Awaitable[Any]],
    normalizer:   Callable[[dict], dict] | None = None,
    ground_truth: Optional[dict] = None,
    timeout_s:    float = 35.0,
) -> dict:
    """Run all 3 diagnostic levels on the two endpoints in parallel.

    The function is **pure** w.r.t. I/O: callers inject their own
    async fetchers (production code wires them to
    ``three65scores_live_fetchers.fetch_game_detail`` and
    ``score365_client.fetch_game_stats``; tests inject mocks).

    Args:
      game_id:       365Scores numeric game id.
      fetch_detail:  async fn(game_id) → dict (``/web/game/?gameId=``).
      fetch_stats:   async fn(game_id) → dict (``/web/game/stats/?gameId=``).
      normalizer:    optional sync fn(raw) → canonical dict (the prod
                     ``normalize_365scores_match_stats``). Tests use
                     this to exercise the parser layer; if ``None``,
                     parser layer is skipped.
      ground_truth:  optional ``{home_corners, away_corners, total_corners}``
                     used to flag parser-vs-reality mismatches.
      timeout_s:     timeout per transport call.

    Returns:
      Dict describing the 3 layers per endpoint + a global verdict.
    """
    # Transport in parallel for both endpoints.
    t_detail_task = _layer1_transport(fetch_detail, game_id, "detail", timeout_s)
    t_stats_task  = _layer1_transport(fetch_stats,  game_id, "stats",  timeout_s)
    t_detail, t_stats = await asyncio.gather(t_detail_task, t_stats_task)

    e_detail = _layer2_endpoint(t_detail)
    e_stats  = _layer2_endpoint(t_stats)

    p_detail = _layer3_parser(t_detail, normalizer, ground_truth=ground_truth)
    p_stats  = _layer3_parser(t_stats,  normalizer, ground_truth=ground_truth)

    # Synthesise verdict.
    reason_codes: list[str] = []
    transport_ok_any = t_detail["ok"] or t_stats["ok"]
    if not transport_ok_any:
        verdict = "TRANSPORT_FAILURE"
        winner = None
        reason_codes.append(RC_TRANSPORT_FAILED)
    elif e_detail["n_paths"] == 0 and e_stats["n_paths"] == 0:
        verdict = "ENDPOINT_NO_CORNERS_KEY"
        winner = None
        reason_codes.append(RC_CORNERS_KEY_NOT_FOUND)
    else:
        # At least one endpoint exposes corner keys.
        parser_ok_detail = (p_detail.get("available")
                            and p_detail.get("total_corners") is not None)
        parser_ok_stats  = (p_stats.get("available")
                            and p_stats.get("total_corners") is not None)
        if parser_ok_detail or parser_ok_stats:
            verdict = "OK"
            # Prefer the endpoint whose parser worked AND matches ground truth.
            if parser_ok_stats and RC_GROUND_TRUTH_MATCH in (p_stats.get("reason_codes") or []):
                winner = "stats"
            elif parser_ok_detail and RC_GROUND_TRUTH_MATCH in (p_detail.get("reason_codes") or []):
                winner = "detail"
            elif parser_ok_stats:
                winner = "stats"
            else:
                winner = "detail"
            reason_codes.append(RC_PARSER_OK)
        else:
            verdict = "PARSER_FAILURE"
            winner = None
            reason_codes.append(RC_PARSER_FAILED)

    return {
        "game_id": game_id,
        "layers": {
            "transport": {"detail": _scrub(t_detail), "stats": _scrub(t_stats)},
            "endpoint":  {"detail": e_detail,         "stats": e_stats},
            "parser":    {"detail": p_detail,         "stats": p_stats},
        },
        "verdict":          verdict,
        "winning_endpoint": winner,
        "reason_codes":     reason_codes,
        "ground_truth":     ground_truth or None,
    }


def _scrub(transport_out: dict) -> dict:
    """Strip the raw payload before persisting — too big and not useful
    in the verdict file. Keep only structural metrics.
    """
    return {
        k: v for k, v in transport_out.items()
        if k != "raw"
    }


__all__ = [
    "diagnose_corners_pipeline",
    "find_corner_paths",
    "CORNER_KEY_SUBSTRINGS",
    "RC_TRANSPORT_FAILED",
    "RC_TRANSPORT_OK",
    "RC_PAYLOAD_EMPTY",
    "RC_CORNERS_KEY_NOT_FOUND",
    "RC_CORNERS_KEY_PRESENT",
    "RC_PARSER_OK",
    "RC_PARSER_FAILED",
    "RC_GROUND_TRUTH_MATCH",
    "RC_GROUND_TRUTH_MISMATCH",
]
