"""Phase F93 — FootyStats client via Scrape.do (structured diagnostics).

FootyStats (`https://footystats.org/`) is the last-resort corners source
in the F93 cascade. The shape mirrors the TotalCorner / 365Scores clients
so the corners provider can compose them uniformly.

Public API
----------
* :func:`extract_footystats_match_url`        — resolve a match URL from match_doc.
* :func:`fetch_footystats_match_page`         — fetch the match page (HTML) with diagnostics.
* :func:`parse_footystats_corners_from_html`  — parse the corners block.
* :func:`normalize_footystats_corners`        — canonical corners shape.

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
PROVIDER          = "footystats"
TRANSPORT         = "scrape_do"
SOURCE_LABEL      = "footystats_scrapedo"
BASE_URL          = "https://footystats.org"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_RENDER    = True
DEFAULT_GEO: Optional[str] = None

# Reason codes specific to FootyStats.
RC_URL_MISSING            = "FOOTYSTATS_URL_MISSING"
RC_BLOCKED_OR_FORBIDDEN   = "FOOTYSTATS_BLOCKED_OR_FORBIDDEN"
RC_STATS_EMPTY            = "FOOTYSTATS_STATS_EMPTY"
RC_CORNERS_NOT_FOUND      = "FOOTYSTATS_CORNERS_NOT_FOUND"
RC_HTML_PARSE_FAILED      = "FOOTYSTATS_HTML_PARSE_FAILED"
RC_CORNERS_FOUND          = "CORNERS_FROM_FOOTYSTATS_SCRAPEDO"
RC_SEARCH_UNAVAILABLE     = "FOOTYSTATS_SEARCH_UNAVAILABLE"


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
        # Footystats sometimes embeds the value as ``"7.0"`` in JSON.
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# URL resolution
# ─────────────────────────────────────────────────────────────────────
def extract_footystats_match_url(match_doc: dict) -> dict:
    """Resolve a FootyStats match URL from ``match_doc``.

    Priority cascade:
      1. ``match_doc.external_ids.footystats.match_url``.
      2. ``match_doc.external_ids.footystats.slug`` → canonical URL.
      3. Legacy top-level ``footystats_match_url`` field.

    Returns the canonical resolver dict.
    """
    if not isinstance(match_doc, dict):
        return {"available": False, "match_url": None, "slug": None, "source": "missing"}

    ext = match_doc.get("external_ids") or {}
    if isinstance(ext, dict):
        fs = ext.get("footystats") or {}
        if isinstance(fs, dict):
            url = fs.get("match_url") or fs.get("url")
            if isinstance(url, str) and url.strip():
                return {
                    "available": True,
                    "match_url": url.strip(),
                    "slug":      fs.get("slug"),
                    "source":    "explicit",
                }
            slug = fs.get("slug")
            if isinstance(slug, str) and slug.strip():
                return {
                    "available": True,
                    "match_url": f"{BASE_URL}/{slug.strip('/').strip()}",
                    "slug":      slug.strip(),
                    "source":    "slug",
                }

    fs_url = match_doc.get("footystats_match_url")
    if isinstance(fs_url, str) and fs_url.strip():
        return {
            "available": True,
            "match_url": fs_url.strip(),
            "slug":      None,
            "source":    "explicit",
        }
    return {"available": False, "match_url": None, "slug": None, "source": "missing"}


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
    RC_TIMEOUT:              "No se pudo cargar córners: la solicitud a FootyStats tardó demasiado.",
    RC_EMPTY_BODY:           "No se pudo cargar córners: FootyStats respondió pero sin contenido.",
    RC_HTTP_ERROR:           "No se pudo cargar córners: FootyStats no respondió correctamente.",
    RC_BLOCKED_OR_FORBIDDEN: "No se pudo cargar córners: FootyStats bloqueó o no devolvió la página.",
    RC_EXCEPTION:            "No se pudo cargar córners: error de transporte con Scrape.do.",
    RC_STATS_EMPTY:          "FootyStats cargó la página pero no tiene estadísticas para este partido.",
    RC_CORNERS_NOT_FOUND:    "FootyStats cargó estadísticas pero no incluye córners por equipo.",
    RC_HTML_PARSE_FAILED:    "FootyStats devolvió HTML pero no se pudo analizar.",
    RC_URL_MISSING:          "No se pudo consultar FootyStats: falta URL del partido.",
    RC_SEARCH_UNAVAILABLE:   "No se pudo localizar el partido en FootyStats por nombre.",
}


def _user_message(reason: Optional[str]) -> str:
    return _USER_MESSAGES.get(reason or "",
                              "No se pudo cargar córners desde FootyStats.")


async def fetch_footystats_match_page(
    client: Any,
    match_url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    render: bool = DEFAULT_RENDER,
) -> dict:
    """Fetch a FootyStats match page via Scrape.do (never raises)."""
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
        out["message_debug"] = "Empty/non-str match_url passed to fetch_footystats_match_page"
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
# Parsing — FootyStats match pages render corners inside small stat
# cards / divs with data-stat or with sibling <span> "Corners" labels.
# Two complementary patterns are tried, in order:
# ─────────────────────────────────────────────────────────────────────
_DATA_STAT_CORNERS_RX = re.compile(
    r"data-stat=[\"']corners[\"'][^>]*>.*?"
    r"<span[^>]*>(?P<home>[^<]+)</span>.*?"
    r"<span[^>]*>(?P<away>[^<]+)</span>",
    re.IGNORECASE | re.DOTALL,
)
_LABEL_BLOCK_RX = re.compile(
    r"<(?:div|li|tr)[^>]*>\s*"
    r"(?:<[^>]+>\s*)*"
    r"(?P<home>\d+(?:\.\d+)?)\s*"
    r"(?:</[^>]+>\s*)+"
    r"(?P<label>corners?|tiros de esquina|c[oó]rner(?:es|s)?)"
    r"\s*(?:</[^>]+>\s*)+"
    r"(?P<away>\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)
# Loose triplet (label THEN two numbers).
_LABEL_THEN_NUMS_RX = re.compile(
    r"(?P<label>corner kicks?|corners?|c[oó]rner(?:es|s)?|tiros de esquina)"
    r"[^0-9]{1,200}?"
    r"(?P<home>\d{1,2})"
    r"[^0-9]{1,80}?"
    r"(?P<away>\d{1,2})",
    re.IGNORECASE | re.DOTALL,
)


def parse_footystats_corners_from_html(html: Optional[str]) -> dict:
    """Parse corners from a FootyStats HTML match page.

    Returns a normalized dict; never raises.
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

    # 1) data-stat first (cleanest).
    try:
        m = _DATA_STAT_CORNERS_RX.search(html)
    except Exception as exc:  # noqa: BLE001
        base["reason_code"]   = RC_HTML_PARSE_FAILED
        base["message_debug"] = f"data-stat regex failed: {exc!r}"
        return base
    if m:
        h = _safe_int(m.group("home"))
        a = _safe_int(m.group("away"))
        if h is not None or a is not None:
            total = (h or 0) + (a or 0) if (h is not None and a is not None) else None
            base.update({
                "available":     True,
                "home":          {"corners": h},
                "away":          {"corners": a},
                "total_corners": total,
                "reason_code":   RC_CORNERS_FOUND,
                "confidence":    "USABLE" if (h is not None and a is not None) else "LIMITED",
                "raw_label":     "data-stat=corners",
            })
            return base

    # 2) Label block (HOME <label> AWAY layout).
    try:
        m = _LABEL_BLOCK_RX.search(html)
    except Exception as exc:  # noqa: BLE001
        m = None
        base["message_debug"] = f"label-block regex failed: {exc!r}"
    if m:
        h = _safe_int(m.group("home"))
        a = _safe_int(m.group("away"))
        if h is not None or a is not None:
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

    # 3) Last resort: label THEN two numbers (label-prefixed).
    try:
        m = _LABEL_THEN_NUMS_RX.search(html)
    except Exception as exc:  # noqa: BLE001
        m = None
        base["message_debug"] = (base.get("message_debug") or "") + f" | label-then-nums failed: {exc!r}"
    if m:
        h = _safe_int(m.group("home"))
        a = _safe_int(m.group("away"))
        if h is not None or a is not None:
            total = (h or 0) + (a or 0) if (h is not None and a is not None) else None
            base.update({
                "available":     True,
                "home":          {"corners": h},
                "away":          {"corners": a},
                "total_corners": total,
                "reason_code":   RC_CORNERS_FOUND,
                "confidence":    "LIMITED",
                "raw_label":     m.group("label").strip(),
            })
            return base

    base["reason_code"]   = RC_CORNERS_NOT_FOUND
    base["message_debug"] = (base.get("message_debug") or "") or "All FootyStats parser patterns missed"
    return base


def normalize_footystats_corners(parsed: dict) -> dict:
    """Identity normaliser (kept for cascade symmetry)."""
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
    "extract_footystats_match_url",
    "fetch_footystats_match_page",
    "parse_footystats_corners_from_html",
    "normalize_footystats_corners",
    "breaker_status", "is_enabled",
]
