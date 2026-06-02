"""
MLB Results Settler — Auto-settle F6C feedback loop
====================================================

Cierra automáticamente las evaluaciones `pending` en
``db.mlb_run_evaluations`` sin requerir que el usuario haga manual
``POST /api/picks/{pick_id}/settle``.

Trigger
-------
Job APScheduler que corre cada 15 min (después de
``settle_finished_baseball`` que persiste ``final_score`` en
``db.matches``). Para cada evaluación pending:

1. Resuelve el partido en ``db.matches`` por ``match_id`` o ``game_pk``.
2. Si el partido tiene ``final_score.home/away`` ya escrito por
   ``mlb_finished_game_settler``, calcula el outcome con
   ``_resolve_result`` y llama a ``update_run_evaluation_result``.
3. Si el mercado no se puede resolver con un final-score plano
   (F5, NRFI, inning-explosive, etc.), la evaluación se marca como
   ``auto_settle_skipped`` con un motivo legible y se deja pending
   para resolución manual.

Markets soportados (auto-settle determinístico)
-----------------------------------------------
* ``over X.X``  / ``under X.X``       — full-game total
* ``team_total_over``                 — total por equipo (full-game)
* ``team_total_under``                — total por equipo (full-game)

Markets NO auto-settle (quedan pending → manual)
------------------------------------------------
* F5 / first-5-innings: requiere ``live_stats.linescore`` por inning
* NRFI / YRFI: requiere primer inning resuelto
* Inning explosive: requiere inning específico (1°, 5°, 7°)

Idempotencia
------------
``update_run_evaluation_result`` solo actualiza si el doc existe; un
documento ya resuelto (``result != "pending"``) NO se re-procesa
porque el query base filtra ``result="pending"``.

Diseñado para no bloquear el scheduler ante fallos: cada evaluación
es try/except aislado, y la función devuelve estadísticas agregadas.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────
_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+")


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        # fallback: extraer primer número de un string tipo "Over 8.5"
        m = _FLOAT_RE.search(str(v))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
        return None


def _detect_side(text: str) -> Optional[str]:
    """Detecta ``over`` | ``under`` | ``team_total_over`` | ``team_total_under``
    a partir de cualquier combinación de ``recommended_market``,
    ``recommended_side`` y ``market_scope``.
    """
    t = (text or "").lower()
    if not t:
        return None
    is_team_total = "team total" in t or "team_total" in t
    if "under" in t:
        return "team_total_under" if is_team_total else "under"
    if "over" in t:
        return "team_total_over" if is_team_total else "over"
    return None


def _detect_team_side(text: str) -> Optional[str]:
    """Detecta si el team_total se refiere a ``home`` o ``away``.

    Ejemplos:
        "Home Team Total Over 4.5"   -> "home"
        "away_team_total_under 3.5"  -> "away"
    """
    t = (text or "").lower()
    if "home" in t:
        return "home"
    if "away" in t or "visit" in t:
        return "away"
    return None


# ────────────────────────────────────────────────────────────────────────────
# Core: _resolve_result
# ────────────────────────────────────────────────────────────────────────────
def _resolve_result(
    *,
    final_runs_home: Optional[int],
    final_runs_away: Optional[int],
    recommended_market: Optional[str],
    recommended_side: Optional[str] = None,
    recommended_line: Optional[float] = None,
    market_scope: Optional[str] = None,
) -> dict:
    """Calcula el outcome ('won'|'lost'|'push') de una evaluación
    resuelta con final-score plano.

    Returns
    -------
    dict con:
        * ``result``: "won" | "lost" | "push" | None
        * ``miss_type``: "OVER_BEAT_UNDER" | "UNDER_BEAT_OVER" | "PUSH" | None
        * ``skipped_reason``: str | None — motivo si no se pudo resolver

    Si ``result`` es ``None`` el caller debe dejar el documento pending
    y NO llamar a ``update_run_evaluation_result``.
    """
    out: dict[str, Any] = {
        "result": None,
        "miss_type": None,
        "skipped_reason": None,
    }

    # 1) Validar final-score disponible.
    if final_runs_home is None or final_runs_away is None:
        out["skipped_reason"] = "missing_final_score"
        return out
    try:
        fh, fa = int(final_runs_home), int(final_runs_away)
    except (TypeError, ValueError):
        out["skipped_reason"] = "non_numeric_final_score"
        return out

    # 2) Filtrar mercados que NO se pueden resolver con final-score plano.
    scope = (market_scope or "").lower()
    market_text = (recommended_market or "")
    side_text = (recommended_side or "")
    combined = f"{market_text} {side_text} {scope}".lower()

    if scope in {"f5", "first_5", "first_five"} or "f5" in combined or "first 5" in combined:
        out["skipped_reason"] = "f5_requires_inning_data"
        return out
    if "nrfi" in combined or "yrfi" in combined:
        out["skipped_reason"] = "nrfi_requires_inning_data"
        return out
    if scope == "inning" or "inning_over" in combined or "inning explosive" in combined:
        out["skipped_reason"] = "inning_market_requires_inning_data"
        return out

    # 3) Detectar lado (over/under, team_total_*).
    side = _detect_side(side_text) or _detect_side(market_text)
    if not side:
        out["skipped_reason"] = "unknown_market_side"
        return out

    # 4) Resolver line.
    line = _safe_float(recommended_line)
    if line is None:
        # Intentar extraer del texto del mercado ("Under 8.5")
        line = _safe_float(market_text)
    if line is None:
        out["skipped_reason"] = "missing_recommended_line"
        return out

    # 5) Calcular el total relevante.
    if side in {"over", "under"}:
        total = fh + fa
    elif side in {"team_total_over", "team_total_under"}:
        team_side = _detect_team_side(market_text) or _detect_team_side(side_text)
        if team_side is None:
            out["skipped_reason"] = "team_total_missing_home_away_marker"
            return out
        total = fh if team_side == "home" else fa
    else:
        out["skipped_reason"] = f"unsupported_market_side:{side}"
        return out

    # 6) Comparar contra la línea.
    # Push solo aplica cuando la línea es entera (8.0, 7.0). Líneas
    # con decimal .5 nunca pushean.
    if abs(total - line) < 1e-9:
        out["result"] = "push"
        out["miss_type"] = "PUSH"
        return out

    is_over_side = side in {"over", "team_total_over"}
    total_exceeded = total > line

    if (is_over_side and total_exceeded) or ((not is_over_side) and (not total_exceeded)):
        out["result"] = "won"
        return out

    out["result"] = "lost"
    if is_over_side:
        # Apostó Over y perdió → el Under ganó.
        out["miss_type"] = "UNDER_BEAT_OVER"
    else:
        # Apostó Under y perdió → el Over ganó.
        out["miss_type"] = "OVER_BEAT_UNDER"
    return out


# ────────────────────────────────────────────────────────────────────────────
# Bulk auto-settler
# ────────────────────────────────────────────────────────────────────────────
async def _fetch_final_score(db, match_id: Any) -> Optional[dict]:
    """Busca ``final_score`` en ``db.matches`` (o ``archived_live_matches``)
    por ``match_id``. Devuelve ``{"home": int, "away": int}`` o None.
    """
    if match_id is None:
        return None
    candidates = [str(match_id), match_id]
    try:
        # Buscar primero en matches activas
        doc = await db.matches.find_one(
            {"match_id": {"$in": candidates}, "final_score": {"$exists": True}},
            {"final_score": 1, "_id": 0},
        )
        if not doc:
            # Fallback: archived_live_matches
            try:
                doc = await db.archived_live_matches.find_one(
                    {"match_id": {"$in": candidates}, "final_score": {"$exists": True}},
                    {"final_score": 1, "_id": 0},
                )
            except Exception:
                doc = None
        if not doc:
            return None
        fs = doc.get("final_score") or {}
        h = fs.get("home")
        a = fs.get("away")
        if h is None or a is None:
            return None
        return {"home": _safe_int(h), "away": _safe_int(a)}
    except Exception as exc:
        log.debug("_fetch_final_score failed for match_id=%s: %s", match_id, exc)
        return None


async def auto_settle_pending_evaluations(
    db,
    *,
    days_back: int = 3,
    max_docs: int = 200,
) -> dict:
    """Sweep ``mlb_run_evaluations`` por documentos pending y resolverlos
    contra ``db.matches.final_score``.

    Returns
    -------
    dict con::
        {
            "scanned":  int,   # evaluaciones pending leídas
            "settled":  int,   # resueltas a won/lost/push
            "skipped":  int,   # no resolvibles (F5, NRFI, etc.)
            "no_score": int,   # match aún sin final_score
            "errors":   int,
            "by_result": {"won": int, "lost": int, "push": int},
            "by_skip_reason": {reason: count, ...},
        }
    """
    from .mlb_run_storage import update_run_evaluation_result

    stats: dict[str, Any] = {
        "scanned": 0,
        "settled": 0,
        "skipped": 0,
        "no_score": 0,
        "errors": 0,
        "by_result": {"won": 0, "lost": 0, "push": 0},
        "by_skip_reason": {},
    }

    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

    query = {
        "sport":         "baseball",
        "result":        "pending",
        "generated_at":  {"$gte": cutoff_iso},
    }

    try:
        cursor = db.mlb_run_evaluations.find(query, {"_id": 0}).limit(max_docs)
        async for ev in cursor:
            stats["scanned"] += 1
            try:
                fs = await _fetch_final_score(db, ev.get("match_id"))
                if not fs:
                    stats["no_score"] += 1
                    continue

                outcome = _resolve_result(
                    final_runs_home=fs["home"],
                    final_runs_away=fs["away"],
                    recommended_market=ev.get("recommended_market"),
                    recommended_side=ev.get("recommended_side"),
                    recommended_line=ev.get("recommended_line"),
                    market_scope=ev.get("market_scope"),
                )

                if outcome["result"] is None:
                    stats["skipped"] += 1
                    reason = outcome.get("skipped_reason") or "unknown"
                    stats["by_skip_reason"][reason] = (
                        stats["by_skip_reason"].get(reason, 0) + 1
                    )
                    # Persistir el motivo para diagnostics + evitar re-loop
                    # en cada tick. Solo el campo informativo, NO cambia result.
                    try:
                        await db.mlb_run_evaluations.update_one(
                            {"id": ev.get("id")},
                            {"$set": {
                                "auto_settle_skipped_reason": reason,
                                "auto_settle_attempted_at":   datetime.now(
                                    timezone.utc).isoformat(),
                            }},
                        )
                    except Exception:
                        pass
                    continue

                ok = await update_run_evaluation_result(
                    db,
                    evaluation_id=ev.get("id"),
                    final_runs_home=fs["home"],
                    final_runs_away=fs["away"],
                    result=outcome["result"],
                    miss_type=outcome["miss_type"],
                )
                if ok:
                    stats["settled"] += 1
                    stats["by_result"][outcome["result"]] = (
                        stats["by_result"].get(outcome["result"], 0) + 1
                    )
                    log.info(
                        "auto-settle eval=%s match_id=%s → %s (%d-%d, line=%s, "
                        "market=%r)",
                        str(ev.get("id"))[:8],
                        ev.get("match_id"),
                        outcome["result"],
                        fs["home"], fs["away"],
                        ev.get("recommended_line"),
                        ev.get("recommended_market"),
                    )
                else:
                    stats["errors"] += 1
            except Exception as exc:
                stats["errors"] += 1
                log.warning(
                    "auto_settle eval=%s failed: %s",
                    str(ev.get("id"))[:8], exc,
                )
    except Exception as exc:
        log.warning("auto_settle_pending_evaluations sweep failed: %s", exc)
        stats["errors"] += 1

    if stats["scanned"]:
        log.info(
            "mlb_results_settler: scanned=%d settled=%d skipped=%d no_score=%d "
            "errors=%d results=%s",
            stats["scanned"], stats["settled"], stats["skipped"],
            stats["no_score"], stats["errors"], stats["by_result"],
        )
    return stats


__all__ = [
    "_resolve_result",
    "auto_settle_pending_evaluations",
]
