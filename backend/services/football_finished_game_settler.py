"""F95.3 — Football Finished Game Settler (cascade: TheStatsAPI → TheSportsDB → API-Sports).

Contexto del bug productivo
---------------------------
Partidos finalizados (ej. *Brazil vs Haiti*) permanecían en
"Generar picks del día" porque:
  1. El documento conservaba `status_short = "NS"`.
  2. Nadie llamaba a ``settle_post_match()`` para fútbol, por lo que
     ``POST_MATCH_RESULT_SETTLED`` no se escribía y el snapshot quedaba
     "abierto" indefinidamente.

Este módulo implementa el "settler periódico" análogo al de MLB
(``mlb_finished_game_settler.py`` + ``_job_settle_finished_baseball``).

Diseño
------
- **Cascada de proveedores (orden estricto):**
    1) **TheStatsAPI** (primario)  → `fetch_match_details`
    2) **TheSportsDB** (secundario) → `fetch_livescore("soccer")`
    3) **API-Sports**  (terciario)  → `fixture_by_id`
- Cada proveedor es fail-soft: cualquier error / timeout devuelve un
  resultado vacío sin levantar la excepción al caller.
- Antes de llamar a la cascada se intenta hidratar desde ``db.matches``
  (los scores ya pudieron haberse persistido por otro job).
- Una vez se obtiene ``home_goals``/``away_goals``, se llama a
  ``settle_post_match()`` con los outputs derivados.
- Filtrado de candidatos: snapshots cuyo ``kickoff_ts`` esté entre
  ``[now - hours_back, now - MIN_AGE_HOURS]`` y que NO tengan
  ``POST_MATCH_RESULT_SETTLED`` en ``reason_codes``.
- ``MIN_AGE_HOURS = 2.5`` evita pisar partidos que todavía podrían estar
  en juego (90' + ET + buffer).

Public API
----------
- ``async settle_recent_finished_football(db, *, hours_back=36, max_matches=50, http_client=None)``
  → ``{"attempted": int, "settled_full": int, "settled_partial": int,
       "no_data": int, "errors": int, "providers": dict}``
- ``async lookup_final_score(match_id, match_doc, *, http_client=None)``
  → ``{"available": bool, "home_goals": int|None, "away_goals": int|None,
       "source": str|None, "reason_codes": list[str]}``
  (función expuesta para tests + posible reutilización por otros jobs).

El módulo NO levanta excepciones hacia el scheduler: cualquier error
queda registrado en ``log`` y se contabiliza en ``errors`` del resumen.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("services.football_finished_game_settler")

# ───────────────────────────────────────────────────────────────────────
# Constantes
# ───────────────────────────────────────────────────────────────────────
COLLECTION_SNAPSHOTS = "football_match_learning_snapshots"
COLLECTION_MATCHES   = "matches"

# Ventana mínima desde kickoff para considerar el partido como
# "potencialmente finalizado" (90' + ET + penales + buffer).
MIN_AGE_HOURS_DEFAULT = 2.5

# Ventana máxima hacia atrás (snapshots viejos ya deberían haberse
# settleado en pasadas anteriores; si no, los descartamos del job).
DEFAULT_HOURS_BACK = 36

# Tope duro de partidos procesados por corrida (proteje cuota de APIs).
DEFAULT_MAX_MATCHES = 50

# Provider names para audit
PROVIDER_DB_HYDRATED    = "db_matches_collection"
PROVIDER_THESTATSAPI    = "thestatsapi"
PROVIDER_THESPORTSDB    = "thesportsdb"
PROVIDER_API_SPORTS     = "api_sports"

# Reason codes específicos del settler
RC_SETTLER_NO_DATA              = "SETTLER_NO_FINAL_SCORE_AVAILABLE"
RC_SETTLER_FROM_DB              = "SETTLER_SCORE_FROM_DB_MATCHES"
RC_SETTLER_FROM_THESTATSAPI     = "SETTLER_SCORE_FROM_THESTATSAPI"
RC_SETTLER_FROM_THESPORTSDB     = "SETTLER_SCORE_FROM_THESPORTSDB"
RC_SETTLER_FROM_API_SPORTS      = "SETTLER_SCORE_FROM_API_SPORTS"


# ───────────────────────────────────────────────────────────────────────
# Helpers internos
# ───────────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            f = float(value)
            return int(f)
        except (TypeError, ValueError):
            return None


def _get_min_age_hours() -> float:
    """Override-friendly: env ``FOOTBALL_SETTLER_MIN_AGE_HOURS``."""
    raw = os.environ.get("FOOTBALL_SETTLER_MIN_AGE_HOURS")
    if not raw:
        return MIN_AGE_HOURS_DEFAULT
    try:
        v = float(raw)
        return max(1.5, v)  # nunca por debajo de 1.5h para evitar pisar live
    except (TypeError, ValueError):
        return MIN_AGE_HOURS_DEFAULT


def _extract_team_names(match_doc: dict) -> tuple[Optional[str], Optional[str]]:
    """Resuelve home/away name a partir del shape canónico del snapshot."""
    home = match_doc.get("home_team")
    away = match_doc.get("away_team")
    home_name = (
        home.get("name") if isinstance(home, dict) else home
        if isinstance(home, str) else None
    )
    away_name = (
        away.get("name") if isinstance(away, dict) else away
        if isinstance(away, str) else None
    )
    return home_name, away_name


# ───────────────────────────────────────────────────────────────────────
# Cascada de fuentes
# ───────────────────────────────────────────────────────────────────────
async def _lookup_from_db_matches(db, match_id) -> dict:
    """Si la colección `matches` ya tiene los scores, los reusamos."""
    if db is None:
        return {"available": False, "source": None}
    try:
        doc = await db[COLLECTION_MATCHES].find_one({"match_id": match_id})
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] db_matches lookup failed match_id=%s: %s",
                  match_id, exc)
        return {"available": False, "source": None}
    if not isinstance(doc, dict):
        return {"available": False, "source": None}
    # Try shape #1: top-level home_score/away_score.
    hs = doc.get("home_score")
    as_ = doc.get("away_score")
    if isinstance(hs, (int, float)) and isinstance(as_, (int, float)):
        return {
            "available": True,
            "home_goals": int(hs),
            "away_goals": int(as_),
            "source":     PROVIDER_DB_HYDRATED,
            "reason_codes": [RC_SETTLER_FROM_DB],
        }
    # Shape #2: goals.{home,away} (API-Sports raw).
    goals = doc.get("goals")
    if isinstance(goals, dict):
        gh, ga = goals.get("home"), goals.get("away")
        if isinstance(gh, (int, float)) and isinstance(ga, (int, float)):
            return {
                "available": True,
                "home_goals": int(gh),
                "away_goals": int(ga),
                "source":     PROVIDER_DB_HYDRATED,
                "reason_codes": [RC_SETTLER_FROM_DB],
            }
    # Shape #3: nested team dicts.
    home_team = doc.get("home_team")
    away_team = doc.get("away_team")
    if isinstance(home_team, dict) and isinstance(away_team, dict):
        hs2 = home_team.get("score")
        as2 = away_team.get("score")
        if isinstance(hs2, (int, float)) and isinstance(as2, (int, float)):
            return {
                "available": True,
                "home_goals": int(hs2),
                "away_goals": int(as2),
                "source":     PROVIDER_DB_HYDRATED,
                "reason_codes": [RC_SETTLER_FROM_DB],
            }
    return {"available": False, "source": None}


async def _lookup_from_thestatsapi(match_id, *, http_client) -> dict:
    """Primario: TheStatsAPI ``/football/matches/{id}``."""
    try:
        from .external_sources import thestatsapi_client as ts_client
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thestatsapi import failed: %s", exc)
        return {"available": False, "source": None}
    if not ts_client.is_enabled():
        return {"available": False, "source": None,
                "reason_codes": ["THESTATSAPI_DISABLED"]}
    try:
        details = await ts_client.fetch_match_details(
            http_client, match_id, sport="football"
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thestatsapi fetch failed match_id=%s: %s",
                  match_id, exc)
        return {"available": False, "source": None,
                "reason_codes": ["THESTATSAPI_FETCH_FAILED"]}
    if not isinstance(details, dict) or not details:
        return {"available": False, "source": None}
    # Locate score in common shapes.
    candidates: list[tuple[Any, Any]] = []
    candidates.append((details.get("home_score"), details.get("away_score")))
    candidates.append((details.get("homeScore"),  details.get("awayScore")))
    goals = details.get("goals")
    if isinstance(goals, dict):
        candidates.append((goals.get("home"), goals.get("away")))
    score = details.get("score")
    if isinstance(score, dict):
        candidates.append((score.get("home"), score.get("away")))
        ft = score.get("fulltime")
        if isinstance(ft, dict):
            candidates.append((ft.get("home"), ft.get("away")))
    home_team = details.get("home_team") or details.get("homeTeam")
    away_team = details.get("away_team") or details.get("awayTeam")
    if isinstance(home_team, dict) and isinstance(away_team, dict):
        candidates.append((home_team.get("score"), away_team.get("score")))
    # Pick first valid pair.
    for h, a in candidates:
        hi, ai = _safe_int(h), _safe_int(a)
        if hi is not None and ai is not None:
            # Status sanity: si está provisto, solo aceptamos finished.
            status_raw = ""
            for k in ("status_short", "status", "matchStatus"):
                v = details.get(k)
                if isinstance(v, str):
                    status_raw = v.upper().strip()
                    break
                if isinstance(v, dict):
                    inner = v.get("short") or v.get("type") or v.get("long")
                    if isinstance(inner, str):
                        status_raw = inner.upper().strip()
                        break
            if status_raw and status_raw not in (
                "FT", "AET", "PEN", "FINAL", "FINISHED", "COMPLETED",
                "FT_PEN", "ENDED",
            ):
                # Score parcial (probablemente live): no settlear.
                continue
            return {
                "available":    True,
                "home_goals":   hi,
                "away_goals":   ai,
                "source":       PROVIDER_THESTATSAPI,
                "reason_codes": [RC_SETTLER_FROM_THESTATSAPI],
            }
    return {"available": False, "source": None}


def _date_distance_days(date_str: Optional[str], target: datetime) -> Optional[int]:
    """Distancia (en días absolutos) entre `date_str` y `target`."""
    if not date_str:
        return None
    try:
        # Accept "YYYY-MM-DD" or ISO 8601.
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(date_str + "T00:00:00+00:00")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt.astimezone(timezone.utc) - target.astimezone(timezone.utc))
        return abs(int(delta.total_seconds() // 86400))
    except (TypeError, ValueError):
        return None


def _names_match(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    aa = a.strip().lower()
    bb = b.strip().lower()
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    # Token overlap: si el 70% de los tokens coinciden, lo consideramos match.
    ta = set(aa.split())
    tb = set(bb.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.7


async def _lookup_from_thesportsdb(
    snapshot_doc: dict, kickoff_dt: Optional[datetime],
    *, http_client,
) -> dict:
    """Secundario: TheSportsDB livescore('soccer') filtrado por nombres."""
    home_name, away_name = _extract_team_names(snapshot_doc)
    if not home_name or not away_name:
        return {"available": False, "source": None}
    try:
        from .external_sources import thesportsdb_client as tsdb_client
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thesportsdb import failed: %s", exc)
        return {"available": False, "source": None}
    if not tsdb_client.is_enabled():
        return {"available": False, "source": None,
                "reason_codes": ["THESPORTSDB_DISABLED"]}
    try:
        envelope = await tsdb_client.fetch_livescore(
            "soccer", client=http_client,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thesportsdb fetch failed: %s", exc)
        return {"available": False, "source": None,
                "reason_codes": ["THESPORTSDB_FETCH_FAILED"]}
    if not isinstance(envelope, dict) or not envelope.get("available"):
        return {"available": False, "source": None}
    items = envelope.get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        it_home = (it.get("home_team") or {}).get("name") if isinstance(it.get("home_team"), dict) else None
        it_away = (it.get("away_team") or {}).get("name") if isinstance(it.get("away_team"), dict) else None
        if not _names_match(home_name, it_home) or not _names_match(away_name, it_away):
            continue
        # Optional date sanity check.
        if kickoff_dt is not None:
            dist = _date_distance_days(it.get("date_event"), kickoff_dt)
            if dist is not None and dist > 1:
                continue
        status_norm = (it.get("status_normalized") or "").upper()
        if status_norm not in ("FINISHED",):
            continue
        hi, ai = _safe_int(it.get("home_score")), _safe_int(it.get("away_score"))
        if hi is None or ai is None:
            continue
        return {
            "available":    True,
            "home_goals":   hi,
            "away_goals":   ai,
            "source":       PROVIDER_THESPORTSDB,
            "reason_codes": [RC_SETTLER_FROM_THESPORTSDB],
        }
    return {"available": False, "source": None}


async def _lookup_from_api_sports(
    snapshot_doc: dict, *, http_client,
) -> dict:
    """Terciario: API-Sports ``fixture_by_id``."""
    # Snapshot may carry the API-Sports fixture id under various keys.
    fixture_id = None
    for k in (
        "api_sports_fixture_id", "fixture_id", "apisports_fixture_id",
        "api_football_fixture_id",
    ):
        v = snapshot_doc.get(k)
        if isinstance(v, (int, str)) and str(v).strip():
            try:
                fixture_id = int(v)
                break
            except (TypeError, ValueError):
                continue
    if fixture_id is None:
        return {"available": False, "source": None,
                "reason_codes": ["API_SPORTS_NO_FIXTURE_ID"]}
    try:
        from . import api_football as af
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] api_football import failed: %s", exc)
        return {"available": False, "source": None}
    try:
        fx = await af.fixture_by_id(http_client, fixture_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] api_football fetch failed fid=%s: %s",
                  fixture_id, exc)
        return {"available": False, "source": None,
                "reason_codes": ["API_SPORTS_FETCH_FAILED"]}
    if not isinstance(fx, dict):
        return {"available": False, "source": None}
    # Status guard.
    status_short = ""
    fixture_block = fx.get("fixture")
    if isinstance(fixture_block, dict):
        st = fixture_block.get("status")
        if isinstance(st, dict):
            v = st.get("short")
            if isinstance(v, str):
                status_short = v.upper().strip()
    if status_short and status_short not in (
        "FT", "AET", "PEN", "FINAL", "FINISHED", "COMPLETED",
    ):
        return {"available": False, "source": None,
                "reason_codes": ["API_SPORTS_NOT_FINISHED"]}
    goals = fx.get("goals")
    if isinstance(goals, dict):
        hi, ai = _safe_int(goals.get("home")), _safe_int(goals.get("away"))
        if hi is not None and ai is not None:
            return {
                "available":    True,
                "home_goals":   hi,
                "away_goals":   ai,
                "source":       PROVIDER_API_SPORTS,
                "reason_codes": [RC_SETTLER_FROM_API_SPORTS],
            }
    return {"available": False, "source": None}


# ───────────────────────────────────────────────────────────────────────
# Public — lookup orchestrator
# ───────────────────────────────────────────────────────────────────────
async def lookup_final_score(
    match_id,
    snapshot_doc: dict,
    *,
    db=None,
    http_client=None,
    kickoff_dt: Optional[datetime] = None,
) -> dict:
    """Cascada completa de lookup de final_score para fútbol.

    Orden:
      0) db.matches (si ya hay scores persistidos).
      1) TheStatsAPI (primario).
      2) TheSportsDB (secundario).
      3) API-Sports (terciario).

    Retorna siempre un dict canónico::

        {
          "available":     bool,
          "home_goals":    int | None,
          "away_goals":    int | None,
          "source":        str | None,
          "reason_codes":  list[str],
        }
    """
    audit: list[str] = []

    # 0) DB cache
    try:
        db_res = await _lookup_from_db_matches(db, match_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] db lookup raised match_id=%s: %s", match_id, exc)
        db_res = {"available": False, "source": None}
    audit.extend(db_res.get("reason_codes") or [])
    if db_res.get("available"):
        db_res.setdefault("reason_codes", []).extend(audit[:-1])
        return db_res

    # 1) TheStatsAPI
    try:
        ts_res = await _lookup_from_thestatsapi(match_id, http_client=http_client)
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thestatsapi raised match_id=%s: %s", match_id, exc)
        ts_res = {"available": False, "source": None}
    audit.extend(ts_res.get("reason_codes") or [])
    if ts_res.get("available"):
        ts_res["reason_codes"] = list(audit)
        return ts_res

    # 2) TheSportsDB
    try:
        sdb_res = await _lookup_from_thesportsdb(
            snapshot_doc, kickoff_dt, http_client=http_client,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] thesportsdb raised match_id=%s: %s", match_id, exc)
        sdb_res = {"available": False, "source": None}
    audit.extend(sdb_res.get("reason_codes") or [])
    if sdb_res.get("available"):
        sdb_res["reason_codes"] = list(audit)
        return sdb_res

    # 3) API-Sports
    try:
        af_res = await _lookup_from_api_sports(snapshot_doc, http_client=http_client)
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] api_football raised match_id=%s: %s", match_id, exc)
        af_res = {"available": False, "source": None}
    audit.extend(af_res.get("reason_codes") or [])
    if af_res.get("available"):
        af_res["reason_codes"] = list(audit)
        return af_res

    audit.append(RC_SETTLER_NO_DATA)
    return {
        "available":    False,
        "home_goals":   None,
        "away_goals":   None,
        "source":       None,
        "reason_codes": list(audit),
    }


# ───────────────────────────────────────────────────────────────────────
# Public — settler entrypoint
# ───────────────────────────────────────────────────────────────────────
async def _get_candidate_snapshots(
    db,
    *,
    hours_back: int,
    min_age_hours: float,
    max_matches: int,
) -> list[dict]:
    """Snapshots con kickoff_ts en ventana de settlement, sin RC_POST_MATCH_RESULT_SETTLED."""
    if db is None:
        return []
    now = _utcnow()
    too_old = now - timedelta(hours=hours_back)
    too_new = now - timedelta(hours=min_age_hours)
    query = {
        "snapshot_taken_at": {"$gte": too_old - timedelta(hours=12)},
        "reason_codes": {"$nin": ["POST_MATCH_RESULT_SETTLED"]},
        # Excluir baseball/basketball/etc.
        "$or": [
            {"sport": "football"},
            {"sport": {"$exists": False}},
        ],
    }
    out: list[dict] = []
    try:
        cursor = db[COLLECTION_SNAPSHOTS].find(query).limit(max_matches * 3)
        async for doc in cursor:
            # Defence-in-depth: defensive filters re-applied in Python in
            # case the Mongo query operators are not honoured (e.g. by an
            # in-memory shim or a partial document).
            rcs = doc.get("reason_codes") or []
            if "POST_MATCH_RESULT_SETTLED" in rcs:
                continue
            sport_v = doc.get("sport")
            if sport_v is not None and sport_v != "football":
                continue
            mdate = doc.get("match_date")
            if isinstance(mdate, datetime):
                kdt = mdate
                if kdt.tzinfo is None:
                    kdt = kdt.replace(tzinfo=timezone.utc)
            else:
                # Try inputs / snapshot_taken_at as a coarse approximation.
                continue
            if kdt < too_old or kdt > too_new:
                continue
            out.append(doc)
            if len(out) >= max_matches:
                break
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler] candidate query failed: %s", exc)
    return out


async def settle_recent_finished_football(
    db,
    *,
    hours_back: int = DEFAULT_HOURS_BACK,
    max_matches: int = DEFAULT_MAX_MATCHES,
    http_client=None,
    settle_fn=None,
) -> dict:
    """Itera snapshots candidatos y aplica settlement con cascade lookup.

    ``settle_fn`` es inyectable para tests; cuando es None se usa
    ``football_learning_snapshot_manager.settle_post_match``.
    """
    if db is None:
        return {"attempted": 0, "settled_full": 0, "settled_partial": 0,
                "no_data": 0, "errors": 0, "providers": {}}

    if settle_fn is None:
        try:
            from .football_learning_snapshot_manager import settle_post_match as _spm
            settle_fn = _spm
        except Exception as exc:  # noqa: BLE001
            log.warning("[settler] cannot import settle_post_match: %s", exc)
            return {"attempted": 0, "settled_full": 0, "settled_partial": 0,
                    "no_data": 0, "errors": 1, "providers": {}}

    min_age_hours = _get_min_age_hours()
    candidates = await _get_candidate_snapshots(
        db,
        hours_back=hours_back,
        min_age_hours=min_age_hours,
        max_matches=max_matches,
    )

    summary = {
        "attempted":       len(candidates),
        "settled_full":    0,
        "settled_partial": 0,
        "no_data":         0,
        "errors":          0,
        "providers":       {},
    }

    for snap in candidates:
        match_id = snap.get("match_id")
        if match_id is None:
            summary["errors"] += 1
            continue
        kickoff_dt = snap.get("match_date")
        if isinstance(kickoff_dt, datetime) and kickoff_dt.tzinfo is None:
            kickoff_dt = kickoff_dt.replace(tzinfo=timezone.utc)
        try:
            result = await lookup_final_score(
                match_id, snap,
                db=db, http_client=http_client, kickoff_dt=kickoff_dt,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[settler] lookup raised match_id=%s: %s", match_id, exc)
            summary["errors"] += 1
            continue

        provider = result.get("source") or "none"
        summary["providers"][provider] = summary["providers"].get(provider, 0) + 1

        if not result.get("available"):
            summary["no_data"] += 1
            continue

        outputs = {
            "home_goals": result.get("home_goals"),
            "away_goals": result.get("away_goals"),
        }
        try:
            settled = await settle_fn(
                db,
                match_id=match_id,
                outputs=outputs,
                source_audit_entries=[{
                    "stage":  "football_finished_game_settler",
                    "source": provider,
                    "status": "COMPLETE",
                    "reason_codes": result.get("reason_codes") or [],
                    "settled_at": _utcnow().isoformat(),
                }],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[settler] settle_post_match failed match_id=%s: %s",
                        match_id, exc)
            summary["errors"] += 1
            continue

        rcs = (settled or {}).get("reason_codes") or []
        if "POST_MATCH_RESULT_SETTLED" in rcs:
            summary["settled_full"] += 1
        else:
            summary["settled_partial"] += 1

    log.info(
        "[settler] football: attempted=%d full=%d partial=%d no_data=%d errors=%d providers=%s",
        summary["attempted"], summary["settled_full"], summary["settled_partial"],
        summary["no_data"], summary["errors"], summary["providers"],
    )
    return summary


__all__ = [
    "settle_recent_finished_football",
    "lookup_final_score",
    "MIN_AGE_HOURS_DEFAULT",
    "DEFAULT_HOURS_BACK",
    "DEFAULT_MAX_MATCHES",
    "PROVIDER_DB_HYDRATED",
    "PROVIDER_THESTATSAPI",
    "PROVIDER_THESPORTSDB",
    "PROVIDER_API_SPORTS",
    "RC_SETTLER_NO_DATA",
    "RC_SETTLER_FROM_DB",
    "RC_SETTLER_FROM_THESTATSAPI",
    "RC_SETTLER_FROM_THESPORTSDB",
    "RC_SETTLER_FROM_API_SPORTS",
]
