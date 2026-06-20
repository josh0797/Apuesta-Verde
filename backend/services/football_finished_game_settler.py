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

# Reason codes específicos del settler (final score)
RC_SETTLER_NO_DATA              = "SETTLER_NO_FINAL_SCORE_AVAILABLE"
RC_SETTLER_FROM_DB              = "SETTLER_SCORE_FROM_DB_MATCHES"
RC_SETTLER_FROM_THESTATSAPI     = "SETTLER_SCORE_FROM_THESTATSAPI"
RC_SETTLER_FROM_THESPORTSDB     = "SETTLER_SCORE_FROM_THESPORTSDB"
RC_SETTLER_FROM_API_SPORTS      = "SETTLER_SCORE_FROM_API_SPORTS"

# Reason codes específicos del settler (corners)
RC_CORNERS_FROM_THESTATSAPI         = "CORNERS_FROM_THESTATSAPI"
RC_CORNERS_FROM_THESPORTSDB         = "CORNERS_FROM_THESPORTSDB"
RC_THESPORTSDB_CORNERS_NOT_AVAILABLE = "THESPORTSDB_CORNERS_NOT_AVAILABLE"
RC_PARTIAL_CORNERS_DATA              = "PARTIAL_CORNERS_DATA"
RC_CORNERS_NOT_AVAILABLE             = "CORNERS_NOT_AVAILABLE"

# Normalised candidate names for "corners" stats coming from any provider.
# Comparison is done on stripped, lowercased, underscore-removed strings.
CORNER_STAT_ALIASES: tuple[str, ...] = (
    "corners",
    "corner kicks",
    "corner_kicks",
    "cornerkicks",
    "total corners",
    "corners total",
    "totalcorners",
    "cornerstotal",
)


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


def _normalise_stat_name(raw: Any) -> str:
    """Normalize a stat name for fuzzy lookup against CORNER_STAT_ALIASES.

    Lowercases, strips, collapses underscores/dashes, removes parentheses
    and excess whitespace. Returns ``""`` for invalid input.
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip().lower()
    if not s:
        return ""
    # Drop trailing/inner parenthesised qualifiers like "Corners (Total)".
    while "(" in s and ")" in s:
        a = s.find("(")
        b = s.find(")", a)
        if a >= 0 and b > a:
            s = (s[:a] + s[b + 1:]).strip()
        else:
            break
    s = s.replace("_", " ").replace("-", " ")
    s = " ".join(s.split())
    return s


def _name_matches_corner(raw: Any) -> bool:
    norm = _normalise_stat_name(raw)
    if not norm:
        return False
    if norm in CORNER_STAT_ALIASES:
        return True
    # Compact comparison too (drop spaces).
    compact = norm.replace(" ", "")
    if compact in CORNER_STAT_ALIASES:
        return True
    return False


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
# F96.1 — Corners extraction (TheStatsAPI + future TheSportsDB)
# ───────────────────────────────────────────────────────────────────────
def _extract_corners_from_payload(payload: Any) -> tuple[Optional[int], Optional[int], list[str]]:
    """Multi-shape defensive extractor for football corners.

    Tries several common shapes used by TheStatsAPI / API-Sports /
    TheSportsDB and returns ``(home_corners, away_corners, raw_names)``.
    ``raw_names`` is the list of stat names actually observed in the
    payload (useful for debug logging).

    Recognised shapes (in priority order):
      1. ``{"home_corners": int, "away_corners": int}`` (flat).
      2. ``{"corners": {"home": int, "away": int}}``.
      3. ``{"corners": int}`` interpreted as ``total_corners`` only
         (we return ``(None, None)`` for sides and stash the total
         via reason — caller may still settle with total only).
      4. ``{"stats": [{"name": "Corners", "home": int, "away": int}, ...]}``.
      5. ``{"stats": [{"type": "...", "value": ...}]}`` per side.
      6. ``{"home_team": {"stats": {"corners": int}}, "away_team": {...}}``.
    """
    if not isinstance(payload, dict):
        return (None, None, [])

    raw_names: list[str] = []

    # Shape 1 — flat keys.
    hc, ac = payload.get("home_corners"), payload.get("away_corners")
    hi, ai = _safe_int(hc), _safe_int(ac)
    if hi is not None and ai is not None:
        raw_names.extend(["home_corners", "away_corners"])
        return (hi, ai, raw_names)

    # Shape 2 — nested ``corners`` dict.
    corners = payload.get("corners")
    if isinstance(corners, dict):
        ch, ca = corners.get("home"), corners.get("away")
        hi2, ai2 = _safe_int(ch), _safe_int(ca)
        raw_names.append("corners")
        if hi2 is not None and ai2 is not None:
            return (hi2, ai2, raw_names)
        # Shape 3 — total only.
        ct = corners.get("total")
        if ct is not None and _safe_int(ct) is not None:
            # Caller can detect this as "partial".
            return (None, None, raw_names)
    elif corners is not None:
        # Shape 3b — corners is a scalar total.
        raw_names.append("corners")
        # Cannot split into home/away → partial.
        if _safe_int(corners) is not None:
            return (None, None, raw_names)

    # Shape 4 — list of stats with name/home/away.
    stats = payload.get("stats")
    if isinstance(stats, list):
        for entry in stats:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("type") or entry.get("label")
            if isinstance(name, str):
                raw_names.append(name)
            if not _name_matches_corner(name):
                continue
            # Shape 4a — entry has home/away.
            sh = entry.get("home")
            sa = entry.get("away")
            hi3, ai3 = _safe_int(sh), _safe_int(sa)
            if hi3 is not None and ai3 is not None:
                return (hi3, ai3, raw_names)
            # Shape 4b — entry has a single value (total).
            v = entry.get("value") or entry.get("total")
            if v is not None and _safe_int(v) is not None:
                # Total only — caller treats as partial unless we can
                # cross-reference per-side values.
                return (None, None, raw_names)

    # Shape 5/6 — per-side nested team stats.
    home_team = payload.get("home_team") or payload.get("home")
    away_team = payload.get("away_team") or payload.get("away")
    if isinstance(home_team, dict) and isinstance(away_team, dict):
        # Try a few common keys for nested stats.
        for ht_key in ("stats", "statistics"):
            ht_stats = home_team.get(ht_key)
            at_stats = away_team.get(ht_key)
            if isinstance(ht_stats, dict) and isinstance(at_stats, dict):
                # Scan for a corner-like key.
                for k_h, v_h in ht_stats.items():
                    if not _name_matches_corner(k_h):
                        continue
                    raw_names.append(k_h)
                    # Find matching key on away side.
                    for k_a, v_a in at_stats.items():
                        if not _name_matches_corner(k_a):
                            continue
                        hi4, ai4 = _safe_int(v_h), _safe_int(v_a)
                        if hi4 is not None and ai4 is not None:
                            return (hi4, ai4, raw_names)
    return (None, None, raw_names)


async def _lookup_corners_from_thestatsapi(
    match_id, *, http_client,
) -> dict:
    """F96.1 — Try to hydrate corners via TheStatsAPI ``/matches/{id}/stats``.

    Returns canonical envelope::

        {
          "available":    bool,
          "home_corners": int | None,
          "away_corners": int | None,
          "total_corners": int | None,
          "source":       str | None,
          "raw_names":    list[str],
          "reason_codes": list[str],
        }
    """
    try:
        from .external_sources import thestatsapi_client as ts_client
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners] thestatsapi import failed: %s", exc)
        return {"available": False, "home_corners": None,
                "away_corners": None, "total_corners": None,
                "source": None, "raw_names": [],
                "reason_codes": ["THESTATSAPI_IMPORT_FAILED"]}
    if not ts_client.is_enabled():
        return {"available": False, "home_corners": None,
                "away_corners": None, "total_corners": None,
                "source": None, "raw_names": [],
                "reason_codes": ["THESTATSAPI_DISABLED"]}
    try:
        stats = await ts_client.fetch_match_stats(http_client, match_id)
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners] thestatsapi fetch_match_stats failed "
                  "match_id=%s: %s", match_id, exc)
        return {"available": False, "home_corners": None,
                "away_corners": None, "total_corners": None,
                "source": None, "raw_names": [],
                "reason_codes": ["THESTATSAPI_FETCH_FAILED"]}
    if not isinstance(stats, dict) or not stats:
        return {"available": False, "home_corners": None,
                "away_corners": None, "total_corners": None,
                "source": None, "raw_names": [],
                "reason_codes": []}
    h, a, raw_names = _extract_corners_from_payload(stats)
    if h is not None and a is not None:
        return {
            "available":     True,
            "home_corners":  h,
            "away_corners":  a,
            "total_corners": h + a,
            "source":        PROVIDER_THESTATSAPI,
            "raw_names":     raw_names,
            "reason_codes":  [RC_CORNERS_FROM_THESTATSAPI],
        }
    # No corners or only total (partial) → not enough to settle.
    return {"available": False, "home_corners": None,
            "away_corners": None, "total_corners": None,
            "source": None, "raw_names": raw_names,
            "reason_codes": []}


async def lookup_total_corners(
    match_id,
    snapshot_doc: dict,
    *,
    http_client=None,
) -> dict:
    """Cascada de lookup de corners para fútbol post-match.

    Orden:
      1) **TheStatsAPI** (primario): `match_stats` con extractor defensivo.
      2) **TheSportsDB** (secundario, experimental): `lookup_event_stats`
         (cableado en F96.2; este paso siempre devuelve "no disponible"
         hasta que F96.2 esté completo).

    Retorna SIEMPRE un dict canónico, nunca raise.
    """
    audit: list[str] = []
    raw_names_audit: list[str] = []

    # 1) TheStatsAPI
    try:
        ts_res = await _lookup_corners_from_thestatsapi(
            match_id, http_client=http_client,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners] thestatsapi raised match_id=%s: %s",
                  match_id, exc)
        ts_res = {"available": False, "home_corners": None,
                  "away_corners": None, "total_corners": None,
                  "source": None, "raw_names": [], "reason_codes": []}
    audit.extend(ts_res.get("reason_codes") or [])
    raw_names_audit.extend(ts_res.get("raw_names") or [])
    if ts_res.get("available"):
        ts_res["reason_codes"] = list(audit)
        ts_res["raw_names"]    = list(raw_names_audit)
        return ts_res

    # 2) TheSportsDB experimental (F96.2 wiring).
    try:
        sdb_res = await _lookup_corners_from_thesportsdb(
            snapshot_doc, http_client=http_client,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners] thesportsdb raised match_id=%s: %s",
                  match_id, exc)
        sdb_res = {"available": False, "home_corners": None,
                   "away_corners": None, "total_corners": None,
                   "source": None, "raw_names": [], "reason_codes": []}
    audit.extend(sdb_res.get("reason_codes") or [])
    raw_names_audit.extend(sdb_res.get("raw_names") or [])
    if sdb_res.get("available"):
        sdb_res["reason_codes"] = list(audit)
        sdb_res["raw_names"]    = list(raw_names_audit)
        return sdb_res

    audit.append(RC_CORNERS_NOT_AVAILABLE)
    return {
        "available":     False,
        "home_corners":  None,
        "away_corners":  None,
        "total_corners": None,
        "source":        None,
        "raw_names":     list(raw_names_audit),
        "reason_codes":  list(audit),
    }


async def _lookup_corners_from_thesportsdb(
    snapshot_doc: dict, *, http_client,
) -> dict:
    """F96.2 — Experimental secondary source for post-match corners.

    Strategy:
      1) Resolve a TheSportsDB ``event_id`` for the snapshot:
         a) prefer ``snapshot_doc["thesportsdb_event_id"]`` if present.
         b) else, call ``fetch_livescore("soccer")`` and match by team
            names + finished status (same as final_score lookup).
      2) Call :func:`thesportsdb_client.lookup_event_stats(event_id)`.
      3) Run the defensive parser on the returned ``raw_stats`` list.
      4) Reason codes:
         * ``CORNERS_FROM_THESPORTSDB`` — full home+away values found.
         * ``PARTIAL_CORNERS_DATA`` — only total / single side present.
         * ``THESPORTSDB_CORNERS_NOT_AVAILABLE`` — no usable stats.

    Returns canonical envelope; never raises.
    """
    empty = {
        "available":     False,
        "home_corners":  None,
        "away_corners":  None,
        "total_corners": None,
        "source":        None,
        "raw_names":     [],
        "reason_codes":  [],
    }
    try:
        from .external_sources import thesportsdb_client as tsdb_client
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners/sdb] import failed: %s", exc)
        return empty
    if not tsdb_client.is_enabled():
        empty["reason_codes"] = ["THESPORTSDB_DISABLED"]
        return empty

    # Step 1 — resolve event_id.
    event_id: Optional[str] = None
    for k in ("thesportsdb_event_id", "tsdb_event_id"):
        v = snapshot_doc.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            event_id = str(v).strip()
            break
    if event_id is None:
        home_name, away_name = _extract_team_names(snapshot_doc)
        if home_name and away_name:
            try:
                envelope = await tsdb_client.fetch_livescore(
                    "soccer", client=http_client,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("[settler/corners/sdb] livescore raised: %s", exc)
                envelope = None
            if isinstance(envelope, dict) and envelope.get("available"):
                for it in envelope.get("items") or []:
                    if not isinstance(it, dict):
                        continue
                    ih = (it.get("home_team") or {}).get("name") \
                        if isinstance(it.get("home_team"), dict) else None
                    ia = (it.get("away_team") or {}).get("name") \
                        if isinstance(it.get("away_team"), dict) else None
                    if _names_match(home_name, ih) and _names_match(away_name, ia):
                        event_id = str(it.get("match_id") or "").strip() or None
                        if event_id:
                            break
    if not event_id:
        empty["reason_codes"] = [RC_THESPORTSDB_CORNERS_NOT_AVAILABLE]
        return empty

    # Step 2 — call lookup_event_stats.
    try:
        stats_env = await tsdb_client.lookup_event_stats(
            event_id, client=http_client,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("[settler/corners/sdb] lookup_event_stats raised: %s", exc)
        empty["reason_codes"] = [RC_THESPORTSDB_CORNERS_NOT_AVAILABLE]
        return empty
    if not isinstance(stats_env, dict) or not stats_env.get("available"):
        empty["reason_codes"] = [RC_THESPORTSDB_CORNERS_NOT_AVAILABLE]
        empty["raw_names"]   = (stats_env or {}).get("raw_names") or []
        return empty

    raw_stats = stats_env.get("raw_stats") or []
    raw_names_full: list[str] = list(stats_env.get("raw_names") or [])

    # Step 3 — defensive parse over each row.
    # The provider typically returns one row per stat with intHome/intAway
    # (V1) or home/away (V2). We map any corner-aliased name and extract.
    h_corners: Optional[int] = None
    a_corners: Optional[int] = None
    seen_partial = False
    for row in raw_stats:
        if not isinstance(row, dict):
            continue
        name = (
            row.get("strStat") or row.get("name")
            or row.get("type")  or row.get("stat")
        )
        if not _name_matches_corner(name):
            continue
        # Try multiple key combos for home/away.
        for h_key, a_key in (
            ("intHome", "intAway"),
            ("home", "away"),
            ("homeValue", "awayValue"),
        ):
            hv = row.get(h_key)
            av = row.get(a_key)
            hi, ai = _safe_int(hv), _safe_int(av)
            if hi is not None and ai is not None:
                h_corners, a_corners = hi, ai
                break
        # Fallback: single value (total only) → partial.
        if h_corners is None or a_corners is None:
            for tk in ("value", "total", "intValue"):
                if row.get(tk) is not None and _safe_int(row.get(tk)) is not None:
                    seen_partial = True
                    break
        if h_corners is not None and a_corners is not None:
            break

    if h_corners is not None and a_corners is not None:
        return {
            "available":     True,
            "home_corners":  h_corners,
            "away_corners":  a_corners,
            "total_corners": h_corners + a_corners,
            "source":        PROVIDER_THESPORTSDB,
            "raw_names":     raw_names_full,
            "reason_codes":  [RC_CORNERS_FROM_THESPORTSDB],
        }

    # Not enough data → reason code that distinguishes "partial" vs "none".
    rc = RC_PARTIAL_CORNERS_DATA if seen_partial \
        else RC_THESPORTSDB_CORNERS_NOT_AVAILABLE
    return {
        "available":     False,
        "home_corners":  None,
        "away_corners":  None,
        "total_corners": None,
        "source":        None,
        "raw_names":     raw_names_full,
        "reason_codes":  [rc],
    }


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
        "corners":         {
            "attempted":     0,
            "hydrated":      0,
            "not_available": 0,
            "providers":     {},
        },
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

        outputs: dict[str, Any] = {
            "home_goals": result.get("home_goals"),
            "away_goals": result.get("away_goals"),
        }
        audit_entries: list[dict] = [{
            "stage":  "football_finished_game_settler",
            "source": provider,
            "status": "COMPLETE",
            "reason_codes": result.get("reason_codes") or [],
            "settled_at": _utcnow().isoformat(),
        }]

        # F96.1 — Best-effort corners hydration (does NOT block the
        # final_score settle if it fails or returns no data).
        try:
            corners_res = await lookup_total_corners(
                match_id, snap, http_client=http_client,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("[settler/corners] lookup raised match_id=%s: %s",
                      match_id, exc)
            corners_res = {"available": False, "home_corners": None,
                           "away_corners": None, "total_corners": None,
                           "source": None, "raw_names": [],
                           "reason_codes": []}
        summary["corners"]["attempted"] += 1
        c_provider = corners_res.get("source") or "none"
        summary["corners"]["providers"][c_provider] = (
            summary["corners"]["providers"].get(c_provider, 0) + 1
        )
        if corners_res.get("available"):
            summary["corners"]["hydrated"] += 1
            outputs["total_corners"] = corners_res.get("total_corners")
            audit_entries.append({
                "stage":  "football_finished_game_settler:corners",
                "source": c_provider,
                "status": "COMPLETE",
                "home_corners":  corners_res.get("home_corners"),
                "away_corners":  corners_res.get("away_corners"),
                "raw_names":     corners_res.get("raw_names") or [],
                "reason_codes":  corners_res.get("reason_codes") or [],
                "settled_at":    _utcnow().isoformat(),
            })
        else:
            summary["corners"]["not_available"] += 1
            # Surface debug audit even when corners are missing — keeps
            # the raw_names visible for triage.
            if corners_res.get("raw_names") or corners_res.get("reason_codes"):
                audit_entries.append({
                    "stage":  "football_finished_game_settler:corners",
                    "source": c_provider,
                    "status": "PARTIAL",
                    "raw_names":    corners_res.get("raw_names") or [],
                    "reason_codes": corners_res.get("reason_codes") or [],
                    "settled_at":   _utcnow().isoformat(),
                })

        try:
            settled = await settle_fn(
                db,
                match_id=match_id,
                outputs=outputs,
                source_audit_entries=audit_entries,
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
        "[settler] football: attempted=%d full=%d partial=%d no_data=%d "
        "errors=%d providers=%s corners=%s",
        summary["attempted"], summary["settled_full"], summary["settled_partial"],
        summary["no_data"], summary["errors"], summary["providers"],
        summary["corners"],
    )
    return summary


__all__ = [
    "settle_recent_finished_football",
    "lookup_final_score",
    "lookup_total_corners",
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
    "RC_CORNERS_FROM_THESTATSAPI",
    "RC_CORNERS_FROM_THESPORTSDB",
    "RC_THESPORTSDB_CORNERS_NOT_AVAILABLE",
    "RC_PARTIAL_CORNERS_DATA",
    "RC_CORNERS_NOT_AVAILABLE",
    "CORNER_STAT_ALIASES",
]
