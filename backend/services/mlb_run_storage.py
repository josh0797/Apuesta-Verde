"""Storage layer for MLB Explosive Inning Intelligence v2 evaluations.

Equivalente directo de ``live_corner_storage.py`` del sistema de fútbol,
adaptado a baseball. Persiste cada evaluación fuerte del motor explosivo
en la colección dedicada ``mlb_run_evaluations`` para alimentar el
feedback loop, medir hit-rate y calibrar umbrales con evidencia real
en lugar de seed cases manuales (``learning_cases.py``).

Documento (UUID PK, ISO-8601 UTC datetimes)::

    {
        # Identidad
        "id":                       str (uuid4),
        "user_id":                  str,
        "match_id":                 str | int,
        "sport":                    "baseball",
        "game_date":                "YYYY-MM-DD",
        "inning":                   int | None,
        "game_state":               "pregame" | "live_f5" | "live_9inn",

        # Marcador al momento de la evaluación
        "score_home":               int,
        "score_away":               int,
        "current_total_runs":       int,
        "home_team":                str,
        "away_team":                str,

        # Snapshot del pitching
        "starter_home":             str | None,
        "starter_away":             str | None,
        "pitcher_stress_index":     float | None,

        # Desglose del risk score
        "explosive_risk_score":     int (0..100),
        "risk_tier":                "LOW" | "MEDIUM" | "HIGH",
        "ops_score_contribution":   int,
        "bullpen_era_contribution": int,
        "park_factor_contribution": int,
        "gap_contribution":         int,
        "script_survival_contribution": int,

        # Recomendación
        "recommended_market":       str,
        "recommended_line":         float | None,
        "recommended_odds":         float | None,
        "flip_triggered":           bool,

        # Calidad
        "confidence":               int (0..100),
        "risk":                     "LOW" | "MEDIUM" | "HIGH",
        "reason_codes":             list[str],
        "human_reasons":            list[str],
        "explanation":              str | None,
        "avoid_markets":            list[str],

        # Línea / contexto
        "pregame_total_line":       float | None,
        "live_total_line":          float | None,
        "line_gap":                 float | None,

        # Reproducibilidad
        "raw_metrics_snapshot":     dict,

        # Resolución post-partido
        "final_runs_home":          int | None,
        "final_runs_away":          int | None,
        "final_total":              int | None,
        "result":                   "won" | "lost" | "pending" | "void",
        "miss_type":                str | None,
        "reference_profile_tag":    str | None,

        "generated_at":             ISO-8601 UTC,
        "resolved_at":              ISO-8601 UTC | None,
        "_v":                       1,
    }
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("mlb_run_storage")

# ---------------------------------------------------------------------------
# Reference profile tag — equivalente al PSG-Arsenal corner profile.
#
# Identifica casos positivos de referencia:
#   - flip_triggered == True
#   - risk_tier == "HIGH"
#   - explosive_risk_score >= 70
#   - result == "won"
# ---------------------------------------------------------------------------
REFERENCE_MLB_POWER_BAT_EXPLOSIVE = "REFERENCE_MLB_POWER_BAT_EXPLOSIVE"

VALID_RESULTS = {"won", "lost", "pending", "void"}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------
def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


def _safe_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _derive_reference_tag(evaluation: dict,
                           result: Optional[str]) -> Optional[str]:
    """Devuelve ``REFERENCE_MLB_POWER_BAT_EXPLOSIVE`` cuando se cumplen
    TODAS las condiciones:

        * flip_triggered == True
        * risk_tier == "HIGH"
        * explosive_risk_score >= 70
        * result == "won"

    En cualquier otro caso devuelve ``None``.

    Se invoca tanto al construir el documento con ``result="pending"``
    (devolverá None) como al resolverlo después del partido.
    """
    if not evaluation:
        return None
    try:
        if not evaluation.get("flip_triggered"):
            return None
        if evaluation.get("risk_tier") != "HIGH":
            return None
        if _safe_int(evaluation.get("explosive_risk_score"), 0) < 70:
            return None
        if result != "won":
            return None
        return REFERENCE_MLB_POWER_BAT_EXPLOSIVE
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------
def build_run_evaluation_document(*,
                                    user_id: str,
                                    match_id: Any,
                                    run_evaluation: dict,
                                    metrics: dict,
                                    result: str = "pending",
                                    final_runs_home: Optional[int] = None,
                                    final_runs_away: Optional[int] = None,
                                    ) -> dict:
    """Traduce el dict en memoria del motor explosivo en un documento
    BSON-friendly listo para insertar en MongoDB.

    Fail-soft: cualquier campo faltante se rellena con default razonable.
    """
    re_ = run_evaluation or {}
    mx_ = metrics or {}

    # ---- Marcador ----------------------------------------------------
    score_home = _safe_int(re_.get("score_home")
                           if "score_home" in re_
                           else mx_.get("score_home"), 0)
    score_away = _safe_int(re_.get("score_away")
                           if "score_away" in re_
                           else mx_.get("score_away"), 0)
    current_total_runs = score_home + score_away

    # ---- Línea -------------------------------------------------------
    pregame_line = _safe_float(re_.get("pregame_total_line")
                               or mx_.get("pregame_total_line"))
    live_line = _safe_float(re_.get("live_total_line")
                            or mx_.get("live_total_line"))
    line_gap: Optional[float] = None
    if pregame_line is not None and live_line is not None:
        line_gap = round(live_line - pregame_line, 2)

    # ---- Final (cuando se construye ya resuelto) --------------------
    final_total: Optional[int] = None
    if final_runs_home is not None and final_runs_away is not None:
        final_total = _safe_int(final_runs_home) + _safe_int(final_runs_away)

    # ---- Desglose del risk score ------------------------------------
    contribs = re_.get("score_contributions") or {}

    doc: dict = {
        # Identidad
        "id":                          str(uuid.uuid4()),
        "user_id":                     user_id,
        "match_id":                    match_id,
        "sport":                       "baseball",
        "game_date":                   _safe_str(re_.get("game_date")
                                                  or mx_.get("game_date")),
        "inning":                      (_safe_int(re_.get("inning"), 0)
                                         if re_.get("inning") is not None
                                         else None),
        "game_state":                  _safe_str(re_.get("game_state")
                                                  or "pregame"),

        # Marcador
        "score_home":                  score_home,
        "score_away":                  score_away,
        "current_total_runs":          current_total_runs,
        "home_team":                   _safe_str(mx_.get("home_team")
                                                  or re_.get("home_team")),
        "away_team":                   _safe_str(mx_.get("away_team")
                                                  or re_.get("away_team")),

        # Pitching snapshot
        "starter_home":                _safe_str(mx_.get("starter_home")
                                                  or re_.get("starter_home")),
        "starter_away":                _safe_str(mx_.get("starter_away")
                                                  or re_.get("starter_away")),
        "pitcher_stress_index":        _safe_float(mx_.get("pitcher_stress_index")
                                                    or re_.get("pitcher_stress_index")),

        # Risk score
        "explosive_risk_score":        _safe_int(re_.get("explosive_risk_score"), 0),
        "risk_tier":                   _safe_str(re_.get("risk_tier")) or "LOW",
        "ops_score_contribution":      _safe_int(contribs.get("ops_score"), 0),
        "bullpen_era_contribution":    _safe_int(contribs.get("bullpen_era"), 0),
        "park_factor_contribution":    _safe_int(contribs.get("park_factor"), 0),
        "gap_contribution":            _safe_int(contribs.get("gap"), 0),
        "script_survival_contribution": _safe_int(contribs.get("script_survival"), 0),

        # Recomendación
        "recommended_market":          _safe_str(re_.get("recommended_market")),
        "recommended_line":            _safe_float(re_.get("recommended_line")),
        "recommended_odds":            _safe_float(re_.get("recommended_odds")),
        "flip_triggered":              bool(re_.get("flip_triggered")),

        # Calidad
        "confidence":                  _safe_int(re_.get("confidence"), 0),
        "risk":                        _safe_str(re_.get("risk")) or "LOW",
        "reason_codes":                _safe_list(re_.get("reason_codes")),
        "human_reasons":               _safe_list(re_.get("human_reasons")),
        "explanation":                 _safe_str(re_.get("explanation")),
        "avoid_markets":               _safe_list(re_.get("avoid_markets")),

        # Línea / contexto
        "pregame_total_line":          pregame_line,
        "live_total_line":             live_line,
        "line_gap":                    line_gap,

        # Snapshot completo para reproducibilidad
        "raw_metrics_snapshot":        mx_,

        # Resolución post-partido
        "final_runs_home":             (_safe_int(final_runs_home)
                                         if final_runs_home is not None else None),
        "final_runs_away":             (_safe_int(final_runs_away)
                                         if final_runs_away is not None else None),
        "final_total":                 final_total,
        "result":                      result if result in VALID_RESULTS else "pending",
        "miss_type":                   None,
        "reference_profile_tag":       None,  # se setea abajo

        "generated_at":                datetime.now(timezone.utc).isoformat(),
        "resolved_at":                 None,
        "_v":                          1,
    }

    # Tag de referencia (solo será no-None si llega ya resuelto como "won"
    # con flip + HIGH + score>=70).
    doc["reference_profile_tag"] = _derive_reference_tag(doc, doc["result"])
    return doc


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _is_strong_enough(run_evaluation: dict) -> bool:
    """Política de persistencia más permisiva que en fútbol.

    En MLB queremos guardar también casos HIGH y todos los flips, incluso
    cuando el motor decidió no recomendar por guardrail. Esto permite
    medir falsos positivos y detectar si el motor fue demasiado
    conservador.
    """
    if not run_evaluation:
        return False
    if run_evaluation.get("should_recommend"):
        return True
    if run_evaluation.get("risk_tier") == "HIGH":
        return True
    if run_evaluation.get("flip_triggered"):
        return True
    return False


async def store_run_evaluation(db, *,
                                 user_id: str,
                                 match_id: Any,
                                 run_evaluation: dict,
                                 metrics: dict,
                                 only_strong: bool = True,
                                 ) -> Optional[str]:
    """Persiste una evaluación del motor explosivo en
    ``db.mlb_run_evaluations``.

    Devuelve:
        * ``doc["id"]`` si insertó correctamente.
        * ``None`` si se saltó por ``only_strong`` o si hubo error.

    Los errores se loggean con ``log.warning`` y nunca rompen el flujo
    de análisis.
    """
    try:
        if only_strong and not _is_strong_enough(run_evaluation or {}):
            return None
        doc = build_run_evaluation_document(
            user_id=user_id,
            match_id=match_id,
            run_evaluation=run_evaluation,
            metrics=metrics,
            result="pending",
        )
        await db.mlb_run_evaluations.insert_one(doc)
        return doc["id"]
    except Exception as exc:
        log.warning("store_run_evaluation failed: %s", exc)
        return None


async def update_run_evaluation_result(db, *,
                                         evaluation_id: str,
                                         final_runs_home: int,
                                         final_runs_away: int,
                                         result: str,
                                         miss_type: Optional[str] = None,
                                         ) -> bool:
    """Parchea el documento una vez que el partido termina.

    Recalcula el ``reference_profile_tag`` porque depende del resultado.

    Devuelve ``True`` si actualizó correctamente, ``False`` si no
    encontró el documento o si hubo error.
    """
    try:
        if result not in VALID_RESULTS:
            log.warning("update_run_evaluation_result invalid result=%r", result)
            return False

        doc = await db.mlb_run_evaluations.find_one({"id": evaluation_id})
        if not doc:
            return False

        fh = _safe_int(final_runs_home)
        fa = _safe_int(final_runs_away)
        final_total = fh + fa

        # Re-derivar tag con el resultado real, usando los campos ya
        # persistidos (flip_triggered, risk_tier, explosive_risk_score).
        tag = _derive_reference_tag(doc, result)

        await db.mlb_run_evaluations.update_one(
            {"id": evaluation_id},
            {"$set": {
                "final_runs_home":         fh,
                "final_runs_away":         fa,
                "final_total":             final_total,
                "result":                  result,
                "miss_type":               miss_type,
                "reference_profile_tag":   tag,
                "resolved_at":             datetime.now(timezone.utc).isoformat(),
            }},
        )
        return True
    except Exception as exc:
        log.warning("update_run_evaluation_result failed: %s", exc)
        return False


async def query_run_evaluations(db, *,
                                  user_id: str,
                                  match_id: Optional[Any] = None,
                                  reference_only: bool = False,
                                  risk_tier: Optional[str] = None,
                                  result: Optional[str] = None,
                                  limit: int = 30,
                                  ) -> list[dict]:
    """Lee evaluaciones recientes de un usuario para calibración o UI.

    Parameters
    ----------
    match_id : optional
        Filtra por partido (acepta str o int).
    reference_only : bool
        Si es True, devuelve solo documentos taggeados con
        ``REFERENCE_MLB_POWER_BAT_EXPLOSIVE``.
    risk_tier : optional
        Filtra por ``risk_tier`` ("LOW" | "MEDIUM" | "HIGH").
    result : optional
        Filtra por resultado ("won" | "lost" | "pending" | "void").
    limit : int
        Cap duro en 100.
    """
    try:
        q: dict = {"user_id": user_id, "sport": "baseball"}
        if match_id is not None:
            q["match_id"] = {"$in": [str(match_id), match_id]}
        if reference_only:
            q["reference_profile_tag"] = REFERENCE_MLB_POWER_BAT_EXPLOSIVE
        if risk_tier:
            q["risk_tier"] = risk_tier
        if result:
            q["result"] = result

        capped = max(1, min(100, _safe_int(limit, 30)))
        cur = db.mlb_run_evaluations.find(q, {"_id": 0}).sort(
            "generated_at", -1).limit(capped)
        return await cur.to_list(length=capped)
    except Exception as exc:
        log.warning("query_run_evaluations failed: %s", exc)
        return []


__all__ = [
    "REFERENCE_MLB_POWER_BAT_EXPLOSIVE",
    "build_run_evaluation_document",
    "store_run_evaluation",
    "update_run_evaluation_result",
    "query_run_evaluations",
]
