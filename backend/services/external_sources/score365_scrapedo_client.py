"""Phase F83-update — 365Scores client with structured Scrape.do diagnostics.

Sister of :mod:`score365_client` (the legacy JSON-API client). This module
focuses on **observable failures**: every call returns a dict that tells
the caller *exactly* at which stage the cascade failed and which message
the UI should show to the user.

Why a separate module?
----------------------
The legacy :mod:`score365_client` returns ``{}`` / ``None`` on failure,
which makes it impossible to differentiate "no ID", "scrape.do has no
token", "365Scores returned HTML but no corners section", etc. We need
those distinctions to wire the F83 debug endpoint.

Public API
----------
* :func:`extract_365scores_ids`              — resolves IDs / URLs from the match_doc cascade.
* :func:`fetch_365scores_match_page`         — fetches the match page (HTML) with diagnostics.
* :func:`fetch_365scores_game_stats`         — fetches the JSON stats endpoint with diagnostics.
* :func:`parse_365scores_corners_from_html`  — parses ``__NEXT_DATA__`` / embedded JSON / DOM.
* :func:`normalize_365scores_corners`        — canonical corners shape.

All public functions are fail-soft and return dicts. The legacy
:mod:`score365_client` is **not** changed.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from ..scrape_do_client import (
    breaker_status,
    fetch_via_scrapedo_result,
    is_enabled,
    RC_BREAKER_OPEN,
    RC_EMPTY_BODY,
    RC_EXCEPTION,
    RC_HTTP_ERROR,
    RC_TIMEOUT,
    RC_TOKEN_MISSING,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
PROVIDER          = "365scores"
TRANSPORT         = "scrape_do"
SOURCE_LABEL      = "365scores_scrapedo"
WEBWS_BASE        = "https://webws.365scores.com/web"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_RENDER    = True
DEFAULT_GEO       = "mx"

# Reason codes raised inside this module (in addition to the scrape.do
# transport ones forwarded as-is).
RC_ID_MISSING              = "SCORE365_ID_MISSING"
RC_BLOCKED_OR_FORBIDDEN    = "SCORE365_BLOCKED_OR_FORBIDDEN"
RC_STATS_EMPTY             = "SCORE365_STATS_EMPTY"
RC_CORNERS_NOT_FOUND       = "SCORE365_CORNERS_NOT_FOUND"
RC_JSON_PARSE_FAILED       = "SCORE365_JSON_PARSE_FAILED"
RC_CORNERS_FOUND           = "CORNERS_FROM_365SCORES_SCRAPEDO"

# Corner aliases — comprehensive, accent-stripped, lowercase.
_CORNER_ALIASES: tuple[str, ...] = (
    "corner kicks",
    "corner kick",
    "corners",
    "corner",
    "corners won",
    "córner",
    "córners",
    "corners totales",
    "total corners",
    "tiros de esquina",
    "tiros de esquina ganados",
    "saques de esquina",
    "escanteios",
    "escanteio",
)

# Regex helpers
_GAME_ID_RX  = re.compile(r"(?:[?&#]id=|/game/)(\d+)", re.IGNORECASE)
_MATCHUP_RX  = re.compile(r"-(\d+)-(\d+)-(\d+)(?:[/?#]|$)")
_NEXT_DATA_RX = re.compile(
    r"<script\s+id=\"__NEXT_DATA__\"[^>]*>(?P<json>.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
# Generic ``window.__INITIAL_STATE__ = {...};`` pattern.
_INITIAL_STATE_RX = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(?P<json>\{.*\})\s*;?\s*</script>",
    re.DOTALL,
)


# ─────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_accents_lower(s: Any) -> str:
    if not isinstance(s, str):
        return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn").lower().strip()


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _stat_name_matches(stat_name: Any) -> bool:
    sn = _strip_accents_lower(stat_name)
    if not sn:
        return False
    return any(alias in sn or sn in alias for alias in _CORNER_ALIASES)


# ─────────────────────────────────────────────────────────────────────
# ID resolution (PARTE 3)
# ─────────────────────────────────────────────────────────────────────
def _extract_game_id_from_url(url: Any) -> Optional[str]:
    if not isinstance(url, str) or not url:
        return None
    m = _GAME_ID_RX.search(url)
    if m:
        return m.group(1)
    m2 = _MATCHUP_RX.search(url)
    if m2:
        return m2.group(3)
    return None


def _extract_matchup_id_from_url(url: Any) -> Optional[str]:
    if not isinstance(url, str) or not url:
        return None
    m = _MATCHUP_RX.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def extract_365scores_ids(match_doc: dict) -> dict:
    """Resolve the 365Scores IDs / URLs from a match_doc cascade.

    Cascade (in order):
      1. ``match_doc.external_ids.365scores.game_id`` + ``matchup_id``.
      2. ``match_doc.external_ids.365scores.url``.
      3. ``match_doc.match_url``.
      4. ``match_doc.external_urls.365scores`` or
         ``match_doc.external_urls.365scores_url``.
      5. ``match_doc.pick.match_url`` / ``match_doc.ui.match_url``.

    Returns::

        {
          "game_id":     str | None,
          "matchup_id":  str | None,
          "match_url":   str | None,   # canonical URL when resolvable
          "available":   bool,         # True iff game_id OR match_url
          "resolved_from": str,        # audit hint ("external_ids", "match_url", …)
        }
    """
    out: dict = {
        "game_id":       None,
        "matchup_id":    None,
        "match_url":     None,
        "available":     False,
        "resolved_from": None,
    }
    if not isinstance(match_doc, dict):
        return out

    ext = match_doc.get("external_ids") or {}
    s365 = ext.get("365scores") if isinstance(ext, dict) else None

    # 1) external_ids.365scores.game_id / matchup_id.
    if isinstance(s365, dict):
        gid = s365.get("game_id") or s365.get("id")
        mid = s365.get("matchup_id")
        if gid:
            out["game_id"]       = str(gid)
            out["matchup_id"]    = str(mid) if mid else None
            out["resolved_from"] = "external_ids.365scores.game_id"
            out["available"]     = True
        # 2) external_ids.365scores.url (also useful even if game_id set).
        url = s365.get("url") or s365.get("match_url")
        if url:
            out["match_url"] = str(url)
            if not out["game_id"]:
                out["game_id"]    = _extract_game_id_from_url(url)
                out["matchup_id"] = _extract_matchup_id_from_url(url)
                if out["game_id"]:
                    out["resolved_from"] = "external_ids.365scores.url"
                    out["available"]     = True

    # 3) match_doc.match_url.
    if not out["available"]:
        url = match_doc.get("match_url") or match_doc.get("365scores_url")
        if url:
            out["match_url"]  = str(url)
            out["game_id"]    = _extract_game_id_from_url(url)
            out["matchup_id"] = _extract_matchup_id_from_url(url)
            if out["game_id"]:
                out["resolved_from"] = "match_url"
                out["available"]     = True

    # 4) match_doc.external_urls.365scores.
    if not out["available"]:
        eu = match_doc.get("external_urls") or {}
        if isinstance(eu, dict):
            url = (eu.get("365scores")
                   or eu.get("365scores_url")
                   or eu.get("score365"))
            if url:
                out["match_url"]  = str(url)
                out["game_id"]    = _extract_game_id_from_url(url)
                out["matchup_id"] = _extract_matchup_id_from_url(url)
                if out["game_id"]:
                    out["resolved_from"] = "external_urls.365scores"
                    out["available"]     = True

    # 5) match_doc.pick.match_url / match_doc.ui.match_url.
    if not out["available"]:
        for container_key in ("pick", "ui", "ui_payload"):
            container = match_doc.get(container_key) or {}
            if isinstance(container, dict):
                url = (container.get("match_url")
                       or container.get("365scores_url"))
                if url:
                    out["match_url"]  = str(url)
                    out["game_id"]    = _extract_game_id_from_url(url)
                    out["matchup_id"] = _extract_matchup_id_from_url(url)
                    if out["game_id"]:
                        out["resolved_from"] = f"{container_key}.match_url"
                        out["available"]     = True
                        break
    return out


# ─────────────────────────────────────────────────────────────────────
# Fetch helpers (PARTE 4)
# ─────────────────────────────────────────────────────────────────────
def _id_missing_result(match_doc: dict) -> dict:
    return {
        "available":     False,
        "stage":         "ID_RESOLUTION",
        "reason_code":   RC_ID_MISSING,
        "message_user":  ("No se pudo cargar córners porque no hay ID de "
                          "365Scores para este partido."),
        "message_debug": ("Missing external_ids.365scores.game_id and "
                          "match_url"),
        "retryable":     False,
        "provider":      PROVIDER,
        "transport":     TRANSPORT,
    }


def _map_scrapedo_reason(reason: Optional[str], status_code: Optional[int]) -> str:
    """Translate a scrape.do reason code into a corners-cascade reason.

    HTTP 403/429/503 → ``SCORE365_BLOCKED_OR_FORBIDDEN`` (anti-bot).
    Other HTTP errors → keep ``SCRAPEDO_HTTP_ERROR``.
    """
    if reason == RC_HTTP_ERROR and isinstance(status_code, int) and status_code in (403, 429, 503):
        return RC_BLOCKED_OR_FORBIDDEN
    return reason or RC_EXCEPTION


def _scrapedo_user_message(reason: str) -> str:
    """Human-facing copy keyed by reason code."""
    return {
        RC_TOKEN_MISSING:        "No se pudo cargar córners: Scrape.do no tiene token configurado.",
        RC_BREAKER_OPEN:         "No se pudo cargar córners: Scrape.do está pausado temporalmente (demasiados fallos recientes).",
        RC_TIMEOUT:              "No se pudo cargar córners: la solicitud a 365Scores tardó demasiado.",
        RC_EMPTY_BODY:           "No se pudo cargar córners: 365Scores respondió pero sin contenido.",
        RC_HTTP_ERROR:           "No se pudo cargar córners: 365Scores no respondió correctamente.",
        RC_BLOCKED_OR_FORBIDDEN: "No se pudo cargar córners: 365Scores bloqueó o no devolvió la página.",
        RC_EXCEPTION:            "No se pudo cargar córners: error de transporte con Scrape.do.",
        RC_STATS_EMPTY:          "No se pudo cargar córners: la página cargó, pero no contiene estadísticas.",
        RC_CORNERS_NOT_FOUND:    "No se pudo cargar córners: se encontraron estadísticas, pero no córners.",
        RC_JSON_PARSE_FAILED:    "No se pudo cargar córners: el formato de la respuesta de 365Scores no es válido.",
    }.get(reason, "No se pudieron cargar los córners desde 365Scores.")


async def fetch_365scores_match_page(client: Any, match_url: str,
                                      *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Fetch an arbitrary 365Scores match-page URL via scrape.do.

    Returns the structured-result dict from
    :func:`scrape_do_client.fetch_via_scrapedo_result` *plus* a
    ``stage='FETCH_PAGE'`` annotation and a UI-friendly message when it
    fails.
    """
    if not isinstance(match_url, str) or not match_url:
        return _id_missing_result({})

    if not is_enabled():
        return {
            "available":     False,
            "stage":         "TRANSPORT_INIT",
            "reason_code":   RC_TOKEN_MISSING,
            "message_user":  _scrapedo_user_message(RC_TOKEN_MISSING),
            "message_debug": "SCRAPEDO_TOKEN env var is missing",
            "retryable":     False,
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "target_url":    match_url,
        }

    res = await fetch_via_scrapedo_result(
        match_url, timeout=timeout_s, render=DEFAULT_RENDER, geo=DEFAULT_GEO,
    )
    if not res.get("ok"):
        reason = _map_scrapedo_reason(res.get("reason_code"), res.get("status_code"))
        return {
            "available":     False,
            "stage":         "FETCH_PAGE",
            "reason_code":   reason,
            "message_user":  _scrapedo_user_message(reason),
            "message_debug": res.get("message_debug"),
            "status_code":   res.get("status_code"),
            "retryable":     reason in (RC_BLOCKED_OR_FORBIDDEN, RC_TIMEOUT,
                                          RC_HTTP_ERROR, RC_EXCEPTION,
                                          RC_BREAKER_OPEN, RC_EMPTY_BODY),
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "target_url":    match_url,
        }
    return {
        "available":     True,
        "stage":         "FETCH_PAGE",
        "html":          res["html"],
        "status_code":   res.get("status_code"),
        "fetched_at":    res.get("fetched_at") or _now_iso(),
        "provider":      PROVIDER,
        "transport":     TRANSPORT,
        "target_url":    match_url,
    }


async def fetch_365scores_game_stats(client: Any, game_id: str,
                                       matchup_id: Optional[str] = None,
                                       *, timezone_name: str = "America/Mexico_City",
                                       timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Fetch the JSON stats endpoint ``/web/game/stats/?gameId=...``.

    Returns either::

        {"available": True, "stage": "FETCH_STATS", "json": {...},
         "provider": "365scores", "transport": "scrape_do", ...}

    or an error dict mirroring :func:`fetch_365scores_match_page`.
    """
    if not game_id:
        return _id_missing_result({})

    if not is_enabled():
        return {
            "available":     False,
            "stage":         "TRANSPORT_INIT",
            "reason_code":   RC_TOKEN_MISSING,
            "message_user":  _scrapedo_user_message(RC_TOKEN_MISSING),
            "message_debug": "SCRAPEDO_TOKEN env var is missing",
            "retryable":     False,
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
        }

    base = (f"{WEBWS_BASE}/game/stats/?appTypeId=5&langId=1&timezoneName="
            f"{timezone_name}&gameId={game_id}")
    if matchup_id:
        base += f"&matchupId={matchup_id}"

    res = await fetch_via_scrapedo_result(
        base, timeout=timeout_s, render=False, geo=DEFAULT_GEO,
    )
    if not res.get("ok"):
        reason = _map_scrapedo_reason(res.get("reason_code"), res.get("status_code"))
        return {
            "available":     False,
            "stage":         "FETCH_STATS",
            "reason_code":   reason,
            "message_user":  _scrapedo_user_message(reason),
            "message_debug": res.get("message_debug"),
            "status_code":   res.get("status_code"),
            "retryable":     reason in (RC_BLOCKED_OR_FORBIDDEN, RC_TIMEOUT,
                                          RC_HTTP_ERROR, RC_EXCEPTION,
                                          RC_BREAKER_OPEN, RC_EMPTY_BODY),
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "target_url":    base,
        }
    body = res.get("html") or ""
    try:
        payload = json.loads(body)
    except Exception as exc:  # noqa: BLE001
        log.debug("[score365_scrapedo] json parse failed: %s", exc)
        return {
            "available":     False,
            "stage":         "FETCH_STATS",
            "reason_code":   RC_JSON_PARSE_FAILED,
            "message_user":  _scrapedo_user_message(RC_JSON_PARSE_FAILED),
            "message_debug": f"json.loads failed: {exc}",
            "status_code":   res.get("status_code"),
            "retryable":     True,
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "target_url":    base,
        }
    return {
        "available":   True,
        "stage":       "FETCH_STATS",
        "json":        payload,
        "status_code": res.get("status_code"),
        "fetched_at":  res.get("fetched_at") or _now_iso(),
        "provider":    PROVIDER,
        "transport":   TRANSPORT,
        "target_url":  base,
    }


# ─────────────────────────────────────────────────────────────────────
# Parser (PARTE 5)
# ─────────────────────────────────────────────────────────────────────
def _extract_embedded_json(html: str) -> list[dict]:
    """Find embedded ``__NEXT_DATA__`` and ``__INITIAL_STATE__`` blobs."""
    out: list[dict] = []
    if not isinstance(html, str) or not html:
        return out
    # __NEXT_DATA__: capture script body and try json.loads — when the
    # body has trailing whitespace ``json.loads`` tolerates it.
    m = _NEXT_DATA_RX.search(html)
    if m:
        body = (m.group("json") or "").strip()
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    out.append(data)
            except Exception as exc:  # noqa: BLE001
                log.debug("[score365_scrapedo] NEXT_DATA parse failed: %s", exc)
    # __INITIAL_STATE__: best-effort, may be inside a wrapping script.
    m2 = _INITIAL_STATE_RX.search(html)
    if m2:
        body = (m2.group("json") or "").strip().rstrip(";").strip()
        if body:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    out.append(data)
            except Exception as exc:  # noqa: BLE001
                log.debug("[score365_scrapedo] INITIAL_STATE parse failed: %s", exc)
    return out


def _walk_dicts(node: Any, depth: int = 0, limit: int = 30):
    """Yield every dict reachable from ``node`` (DFS, bounded depth)."""
    if depth > limit:
        return
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_dicts(v, depth + 1, limit)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_dicts(v, depth + 1, limit)


def _find_stats_list(blob: dict) -> list:
    """Heuristic: locate a ``statistics`` / ``gameStats`` / ``stats`` list
    that contains stat entries with ``name``/``home``/``away`` fields.
    """
    candidates: list[list] = []
    for d in _walk_dicts(blob):
        for key in ("statistics", "gameStats", "stats", "matchStats"):
            v = d.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                # Check it looks like stats (has a 'name' or 'type').
                if any(("name" in s) or ("title" in s) or ("type" in s)
                       for s in v if isinstance(s, dict)):
                    candidates.append(v)
    # Prefer the longest list — usually the comprehensive one.
    return max(candidates, key=len) if candidates else []


def _team_names_from_blob(blob: dict) -> tuple[Optional[str], Optional[str]]:
    """Try to discover ``(home_team_name, away_team_name)`` from typical
    365Scores payloads. Fail-soft."""
    home_name = away_name = None
    for d in _walk_dicts(blob):
        comps = d.get("competitors") or d.get("teams")
        if isinstance(comps, list) and len(comps) >= 2:
            h, a = comps[0], comps[1]
            if isinstance(h, dict) and isinstance(a, dict):
                home_name = home_name or h.get("name") or h.get("symbolicName")
                away_name = away_name or a.get("name") or a.get("symbolicName")
        if home_name and away_name:
            break
    return home_name, away_name


def parse_365scores_corners_from_html(html: str) -> dict:
    """Best-effort corner extraction from a 365Scores HTML page.

    Strategy:
      1. Search ``__NEXT_DATA__`` / ``__INITIAL_STATE__`` JSON blobs.
      2. Walk the parsed tree to find a ``statistics``/``gameStats``
         list containing stat rows.
      3. Match corners by alias list (multi-language).

    Returns the same shape as :func:`normalize_365scores_corners`.
    """
    if not isinstance(html, str) or not html:
        return {
            "available":     False,
            "stage":         "PARSE_HTML",
            "reason_code":   RC_STATS_EMPTY,
            "message_user":  _scrapedo_user_message(RC_STATS_EMPTY),
            "message_debug": "HTML is empty",
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "fetched_at":    _now_iso(),
        }

    blobs = _extract_embedded_json(html)
    if not blobs:
        return {
            "available":     False,
            "stage":         "PARSE_HTML",
            "reason_code":   RC_STATS_EMPTY,
            "message_user":  _scrapedo_user_message(RC_STATS_EMPTY),
            "message_debug": ("No __NEXT_DATA__ / __INITIAL_STATE__ found "
                                "in HTML page"),
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "fetched_at":    _now_iso(),
        }

    stats_list: list = []
    home_name = away_name = None
    for blob in blobs:
        sl = _find_stats_list(blob)
        if sl:
            stats_list = sl
        h, a = _team_names_from_blob(blob)
        home_name = home_name or h
        away_name = away_name or a
        if stats_list and (home_name or away_name):
            break

    if not stats_list:
        return {
            "available":     False,
            "stage":         "PARSE_HTML",
            "reason_code":   RC_STATS_EMPTY,
            "message_user":  _scrapedo_user_message(RC_STATS_EMPTY),
            "message_debug": "No statistics list found in embedded JSON",
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "fetched_at":    _now_iso(),
        }

    return normalize_365scores_corners({
        "statistics":     stats_list,
        "home_team_name": home_name,
        "away_team_name": away_name,
    })


def normalize_365scores_corners(raw: dict) -> dict:
    """Canonical corners shape.

    Accepts either:
      * Raw 365Scores ``/web/game/stats/`` JSON (looks for ``statistics``).
      * Output of :func:`parse_365scores_corners_from_html` (already
        normalized; idempotent re-call).
    """
    if not isinstance(raw, dict):
        return {
            "available":     False,
            "stage":         "NORMALIZE",
            "reason_code":   RC_CORNERS_NOT_FOUND,
            "message_user":  _scrapedo_user_message(RC_CORNERS_NOT_FOUND),
            "message_debug": "raw payload is not a dict",
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "fetched_at":    _now_iso(),
        }

    # If already normalised, re-emit (idempotency).
    if raw.get("source") == SOURCE_LABEL and "home" in raw and "away" in raw:
        return raw

    # Path A: top-level stats from /web/game/stats endpoint.
    stats_list = raw.get("statistics")
    if not isinstance(stats_list, list):
        # Path A.2: legacy /web/game payload nests under ``game``.
        game = raw.get("game") if isinstance(raw.get("game"), dict) else None
        if game:
            stats_list = game.get("statistics") or []

    if not isinstance(stats_list, list) or not stats_list:
        return {
            "available":     False,
            "stage":         "NORMALIZE",
            "reason_code":   RC_STATS_EMPTY,
            "message_user":  _scrapedo_user_message(RC_STATS_EMPTY),
            "message_debug": "raw.statistics list is missing/empty",
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "fetched_at":    _now_iso(),
        }

    raw_stat_names: list[str] = []
    home_corners = None
    away_corners = None
    home_name = raw.get("home_team_name")
    away_name = raw.get("away_team_name")

    # Try to discover team names from competitors if not provided.
    if not (home_name and away_name):
        comps = raw.get("competitors") or raw.get("teams") or []
        if isinstance(comps, list) and len(comps) >= 2:
            h, a = comps[0], comps[1]
            if isinstance(h, dict):
                home_name = home_name or h.get("name") or h.get("symbolicName")
            if isinstance(a, dict):
                away_name = away_name or a.get("name") or a.get("symbolicName")

    for s in stats_list:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("title") or s.get("type") or s.get("shortName") or ""
        raw_stat_names.append(str(name))
        if not _stat_name_matches(name):
            continue
        # Read home / away values.
        h_val = s.get("home")
        a_val = s.get("away")
        if h_val is None and "homeValue" in s:
            h_val = s.get("homeValue")
        if a_val is None and "awayValue" in s:
            a_val = s.get("awayValue")
        # Some payloads put values under nested {value:..., competitorId:...}.
        if h_val is None and isinstance(s.get("homeStat"), dict):
            h_val = s["homeStat"].get("value")
        if a_val is None and isinstance(s.get("awayStat"), dict):
            a_val = s["awayStat"].get("value")
        if home_corners is None:
            home_corners = _safe_int(h_val)
        if away_corners is None:
            away_corners = _safe_int(a_val)
        if home_corners is not None and away_corners is not None:
            break

    if home_corners is None and away_corners is None:
        return {
            "available":      False,
            "stage":          "NORMALIZE",
            "reason_code":    RC_CORNERS_NOT_FOUND,
            "message_user":   _scrapedo_user_message(RC_CORNERS_NOT_FOUND),
            "message_debug":  ("Statistics list parsed but no corner "
                                "aliases matched"),
            "raw_stat_names": raw_stat_names,
            "provider":       PROVIDER,
            "transport":      TRANSPORT,
            "fetched_at":     _now_iso(),
        }

    total_corners = None
    if home_corners is not None and away_corners is not None:
        total_corners = home_corners + away_corners

    return {
        "available":      True,
        "source":         SOURCE_LABEL,
        "provider":       PROVIDER,
        "transport":      TRANSPORT,
        "home":           {"team": home_name, "corners": home_corners},
        "away":           {"team": away_name, "corners": away_corners},
        "total_corners":  total_corners,
        "raw_stat_names": raw_stat_names,
        "confidence":     "USABLE",
        "reason_codes":   [RC_CORNERS_FOUND],
        "fetched_at":     _now_iso(),
    }


# Re-export the scrape.do health helpers so the debug endpoint can build
# the response without importing two modules.
__all__ = [
    "PROVIDER", "TRANSPORT", "SOURCE_LABEL",
    "RC_ID_MISSING", "RC_BLOCKED_OR_FORBIDDEN", "RC_STATS_EMPTY",
    "RC_CORNERS_NOT_FOUND", "RC_JSON_PARSE_FAILED", "RC_CORNERS_FOUND",
    "breaker_status", "is_enabled",
    "extract_365scores_ids",
    "fetch_365scores_match_page",
    "fetch_365scores_game_stats",
    "parse_365scores_corners_from_html",
    "normalize_365scores_corners",
]
