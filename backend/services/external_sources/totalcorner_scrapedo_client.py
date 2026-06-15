"""Phase F93 — TotalCorner client via Scrape.do (structured diagnostics).

TotalCorner (`https://www.totalcorner.com/`) is the spec-mandated tertiary
corners source. Like the F83 365Scores client, this module is fail-soft
and surfaces structured failure diagnostics so the F93 cascade can pick
the next provider rather than spinning.

Public API
----------
* :func:`extract_totalcorner_match_url`      — resolve a match URL from match_doc.
* :func:`fetch_totalcorner_match_page`       — fetch the match page (HTML) with diagnostics.
* :func:`parse_totalcorner_corners_from_html` — parse the corners table.
* :func:`normalize_totalcorner_corners`      — canonical corners shape.

All functions return dicts; nothing raises. Transport is delegated to
``services.scrape_do_client`` (no new API key required).
"""
from __future__ import annotations

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
PROVIDER          = "totalcorner"
TRANSPORT         = "scrape_do"
SOURCE_LABEL      = "totalcorner_scrapedo"
BASE_URL          = "https://www.totalcorner.com"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_RENDER    = True
DEFAULT_GEO: Optional[str] = None  # tc has no strong geo gate

# Reason codes specific to TotalCorner.
RC_URL_MISSING            = "TOTALCORNER_URL_MISSING"
RC_BLOCKED_OR_FORBIDDEN   = "TOTALCORNER_BLOCKED_OR_FORBIDDEN"
RC_STATS_EMPTY            = "TOTALCORNER_STATS_EMPTY"
RC_CORNERS_NOT_FOUND      = "TOTALCORNER_CORNERS_NOT_FOUND"
RC_HTML_PARSE_FAILED      = "TOTALCORNER_HTML_PARSE_FAILED"
RC_CORNERS_FOUND          = "CORNERS_FROM_TOTALCORNER_SCRAPEDO"
RC_SEARCH_UNAVAILABLE     = "TOTALCORNER_SEARCH_UNAVAILABLE"


# ─────────────────────────────────────────────────────────────────────
# Helpers
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


def _slugify_team(name: str) -> str:
    s = _strip_accents_lower(name)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# ─────────────────────────────────────────────────────────────────────
# URL resolution
# ─────────────────────────────────────────────────────────────────────
def extract_totalcorner_match_url(match_doc: dict) -> dict:
    """Resolve a TotalCorner match URL from ``match_doc``.

    Priority cascade (cheap → expensive):
      1. ``match_doc.external_ids.totalcorner.match_url`` (explicit).
      2. ``match_doc.external_ids.totalcorner.match_id``  → build canonical URL.
      3. Any other ``totalcorner_*`` direct hint at the top level.

    Returns a dict::

        {"available": bool,
         "match_url": str | None,
         "match_id":  str | None,
         "source":    "explicit" | "match_id" | "missing"}

    Fail-soft: ``available=False`` whenever nothing actionable was found.
    """
    if not isinstance(match_doc, dict):
        return {"available": False, "match_url": None, "match_id": None, "source": "missing"}

    ext = match_doc.get("external_ids") or {}
    if isinstance(ext, dict):
        tc = ext.get("totalcorner") or ext.get("total_corner") or {}
        if isinstance(tc, dict):
            url = tc.get("match_url") or tc.get("url")
            if isinstance(url, str) and url.strip():
                return {
                    "available": True,
                    "match_url": url.strip(),
                    "match_id":  str(tc.get("match_id")) if tc.get("match_id") else None,
                    "source":    "explicit",
                }
            mid = tc.get("match_id") or tc.get("id")
            if mid:
                return {
                    "available": True,
                    "match_url": f"{BASE_URL}/matches/{mid}",
                    "match_id":  str(mid),
                    "source":    "match_id",
                }

    # Top-level legacy field.
    tc_url = match_doc.get("totalcorner_match_url")
    if isinstance(tc_url, str) and tc_url.strip():
        return {
            "available": True,
            "match_url": tc_url.strip(),
            "match_id":  None,
            "source":    "explicit",
        }
    tc_id = match_doc.get("totalcorner_match_id")
    if tc_id:
        return {
            "available": True,
            "match_url": f"{BASE_URL}/matches/{tc_id}",
            "match_id":  str(tc_id),
            "source":    "match_id",
        }

    return {"available": False, "match_url": None, "match_id": None, "source": "missing"}


# ─────────────────────────────────────────────────────────────────────
# Transport
# ─────────────────────────────────────────────────────────────────────
def _map_scrapedo_reason(reason: Optional[str], status_code: Optional[int]) -> str:
    if reason == RC_HTTP_ERROR and isinstance(status_code, int) and status_code in (403, 429, 503):
        return RC_BLOCKED_OR_FORBIDDEN
    return reason or RC_EXCEPTION


_USER_MESSAGES: dict[str, str] = {
    RC_TOKEN_MISSING:        "No se pudo cargar córners: Scrape.do no tiene token configurado.",
    RC_BREAKER_OPEN:         "No se pudo cargar córners: Scrape.do está pausado temporalmente.",
    RC_TIMEOUT:              "No se pudo cargar córners: la solicitud a TotalCorner tardó demasiado.",
    RC_EMPTY_BODY:           "No se pudo cargar córners: TotalCorner respondió pero sin contenido.",
    RC_HTTP_ERROR:           "No se pudo cargar córners: TotalCorner no respondió correctamente.",
    RC_BLOCKED_OR_FORBIDDEN: "No se pudo cargar córners: TotalCorner bloqueó o no devolvió la página.",
    RC_EXCEPTION:            "No se pudo cargar córners: error de transporte con Scrape.do.",
    RC_STATS_EMPTY:          "TotalCorner cargó la página pero no tiene estadísticas para este partido.",
    RC_CORNERS_NOT_FOUND:    "TotalCorner cargó estadísticas pero no incluye córners por equipo.",
    RC_HTML_PARSE_FAILED:    "TotalCorner devolvió HTML pero no se pudo analizar.",
    RC_URL_MISSING:          "No se pudo consultar TotalCorner: falta URL del partido.",
    RC_SEARCH_UNAVAILABLE:   "No se pudo localizar el partido en TotalCorner por nombre.",
}


def _user_message(reason: Optional[str]) -> str:
    return _USER_MESSAGES.get(reason or "",
                              "No se pudo cargar córners desde TotalCorner.")


async def fetch_totalcorner_match_page(
    client: Any,
    match_url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    render: bool = DEFAULT_RENDER,
) -> dict:
    """Fetch a TotalCorner match page via Scrape.do.

    Returns a structured dict (never raises). The ``client`` argument is
    accepted for symmetry with the 365Scores helper but ignored — transport
    is owned by ``scrape_do_client.fetch_via_scrapedo_result``.
    """
    out: dict = {
        "provider":     PROVIDER,
        "transport":    TRANSPORT,
        "available":    False,
        "stage":        "FETCH_PAGE",
        "html":         None,
        "status_code":  None,
        "reason_code":  None,
        "message_user": None,
        "message_debug": None,
        "retryable":    True,
        "match_url":    match_url,
        "fetched_at":   _now_iso(),
    }

    if not isinstance(match_url, str) or not match_url.strip():
        out["reason_code"]   = RC_URL_MISSING
        out["message_user"]  = _user_message(RC_URL_MISSING)
        out["message_debug"] = "Empty/non-str match_url passed to fetch_totalcorner_match_page"
        out["retryable"]     = False
        return out

    res = await fetch_via_scrapedo_result(
        match_url, timeout=timeout_s, render=render, geo=DEFAULT_GEO,
    )
    out["status_code"] = res.get("status_code")
    if not res.get("ok"):
        rc = _map_scrapedo_reason(res.get("reason_code"), res.get("status_code"))
        out["reason_code"]   = rc
        out["message_user"]  = _user_message(rc)
        out["message_debug"] = res.get("message_debug")
        out["retryable"]     = rc not in (RC_TOKEN_MISSING, RC_URL_MISSING)
        return out

    out["available"] = True
    out["html"]      = res.get("html")
    return out


# ─────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────
# TotalCorner match pages render a "Corners" / "Corner Kicks" stat row
# inside a stats table; rows look like::
#
#   <tr><th>Corners</th><td>9</td><td>5</td></tr>
#
# The first <td> is home, the second is away. We use a permissive regex
# walker so partial DOM changes do not destroy the parser.
_STAT_ROW_RX = re.compile(
    r"<tr[^>]*>\s*"
    r"<t[dh][^>]*>(?P<label>[^<]+)</t[dh]>\s*"
    r"<t[dh][^>]*>(?P<home>[^<]*)</t[dh]>\s*"
    r"<t[dh][^>]*>(?P<away>[^<]*)</t[dh]>",
    re.IGNORECASE | re.DOTALL,
)

_CORNER_LABEL_ALIASES: tuple[str, ...] = (
    "corner kicks", "corner kick", "corners", "corner",
    "total corners", "corners won", "tiros de esquina",
)


def parse_totalcorner_corners_from_html(html: Optional[str]) -> dict:
    """Parse corners from a TotalCorner HTML match page.

    Returns a normalized dict::

        {
          "available":   True/False,
          "home":        {"corners": int | None},
          "away":        {"corners": int | None},
          "total_corners": int | None,
          "reason_code": str | None,
          "confidence":  "USABLE" | "LIMITED",
          "raw_label":   matched stat-row label (debugging),
        }
    """
    base: dict = {
        "available":     False,
        "home":          {"corners": None},
        "away":          {"corners": None},
        "total_corners": None,
        "reason_code":   None,
        "confidence":    "LIMITED",
        "raw_label":     None,
        "message_debug": None,
    }
    if not isinstance(html, str) or not html.strip():
        base["reason_code"]   = RC_STATS_EMPTY
        base["message_debug"] = "Empty / non-str HTML payload"
        return base

    try:
        matches = list(_STAT_ROW_RX.finditer(html))
    except Exception as exc:  # noqa: BLE001
        base["reason_code"]   = RC_HTML_PARSE_FAILED
        base["message_debug"] = f"Regex walk failed: {exc!r}"
        return base

    if not matches:
        base["reason_code"]   = RC_STATS_EMPTY
        base["message_debug"] = "No <tr><th>label</th><td>h</td><td>a</td></tr> rows found"
        return base

    for m in matches:
        label = _strip_accents_lower(m.group("label"))
        if not label:
            continue
        if not any(alias in label for alias in _CORNER_LABEL_ALIASES):
            continue
        h = _safe_int(m.group("home"))
        a = _safe_int(m.group("away"))
        if h is None and a is None:
            continue
        total = (h or 0) + (a or 0) if (h is not None and a is not None) else None
        base.update({
            "available":     True,
            "home":          {"corners": h},
            "away":          {"corners": a},
            "total_corners": total,
            "reason_code":   RC_CORNERS_FOUND,
            "confidence":    "USABLE" if (h is not None and a is not None) else "LIMITED",
            "raw_label":     m.group("label").strip(),
        })
        return base

    base["reason_code"]   = RC_CORNERS_NOT_FOUND
    base["message_debug"] = f"{len(matches)} stat rows parsed; none matched corner aliases"
    return base


def normalize_totalcorner_corners(parsed: dict) -> dict:
    """Identity normaliser kept for symmetry with the 365Scores client.

    The HTML parser already emits a canonical shape; this wrapper exists
    so callers can use the same name pattern (``normalize_*_corners``)
    when composing the cascade.
    """
    if not isinstance(parsed, dict):
        return {"available": False, "home": {"corners": None}, "away": {"corners": None},
                "total_corners": None, "reason_code": RC_HTML_PARSE_FAILED,
                "confidence": "LIMITED"}
    return parsed


__all__ = [
    "PROVIDER", "TRANSPORT", "SOURCE_LABEL", "BASE_URL",
    "RC_URL_MISSING", "RC_BLOCKED_OR_FORBIDDEN", "RC_STATS_EMPTY",
    "RC_CORNERS_NOT_FOUND", "RC_HTML_PARSE_FAILED", "RC_CORNERS_FOUND",
    "RC_SEARCH_UNAVAILABLE",
    "extract_totalcorner_match_url",
    "fetch_totalcorner_match_page",
    "parse_totalcorner_corners_from_html",
    "normalize_totalcorner_corners",
    # Transport observability — re-exported for callers.
    "breaker_status", "is_enabled",
]
