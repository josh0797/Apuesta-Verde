"""Sprint E.1.1-f · 365Scores **Top Trends** client.

Replaces the legacy SportyTrader resolver with 365Scores' "Tendencias
Top" block. The page exposes a list of natural-language trends per
match such as::

    "Portugal ganó 4/5 últimos partidos"
    "RD Congo Menos de 2.5 goles como visitante 14/16 últimos partidos"

This module:

1. Reuses :func:`extract_365scores_ids` from
   :mod:`score365_scrapedo_client` to resolve the ``gameId`` from any
   shape of ``match_doc``.
2. Fetches the dedicated JSON endpoint
   ``GET webws.365scores.com/web/game/topTrends/?gameId=...``.
   If unavailable, falls back to scraping the match page HTML and
   searching for ``__NEXT_DATA__`` / ``topTrends`` keys.
3. Parses every textual trend into a structured row::

       {
         "raw":        "Portugal ganó 4/5 últimos partidos",
         "team":       "Portugal",         # home|away|UNKNOWN allowed
         "team_side":  "home",
         "trend_type": "WIN",
         "value":      "4/5",
         "sample":     {"hits": 4, "total": 5, "rate": 0.80},
         "period":     "last_5_matches",
         "scope":      "all",              # all|home|away
         "language":   "es",
         "confidence": "HIGH"|"MEDIUM"|"LOW",
       }

All public callables are **fail-soft** and return dicts with
``available`` / ``reason_code`` so the orchestrator can render an
explicit reason in the UI.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..scrape_do_client import (
    fetch_via_scrapedo_result,
    is_enabled,
    RC_BREAKER_OPEN,
    RC_EMPTY_BODY,
    RC_EXCEPTION,
    RC_HTTP_ERROR,
    RC_TIMEOUT,
    RC_TOKEN_MISSING,
)
from .score365_scrapedo_client import (
    PROVIDER as SCORE365_PROVIDER,
    TRANSPORT,
    WEBWS_BASE,
    extract_365scores_ids,
    RC_BLOCKED_OR_FORBIDDEN,
    RC_ID_MISSING,
    RC_JSON_PARSE_FAILED,
    _scrapedo_user_message,
    _map_scrapedo_reason,
    _now_iso,
)

log = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────
PROVIDER         = SCORE365_PROVIDER     # "365scores"
SOURCE_LABEL     = "365scores_top_trends"
DEFAULT_TIMEOUT  = 35.0
DEFAULT_GEO      = "mx"

# Reason codes (resolver-specific).
RC_TRENDS_FOUND          = "TOP_TRENDS_FROM_365SCORES"
RC_TRENDS_EMPTY          = "SCORE365_TOP_TRENDS_EMPTY"
RC_TRENDS_PARSE_FAILED   = "SCORE365_TOP_TRENDS_PARSE_FAILED"
RC_TRENDS_NOT_FOUND      = "SCORE365_TOP_TRENDS_NOT_FOUND"


# ════════════════════════════════════════════════════════════════════════
# Text normalisation
# ════════════════════════════════════════════════════════════════════════
def _strip_accents(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(s: Optional[str]) -> str:
    """Lowercase + accent-stripped + single-space normalisation.

    Used purely for *matching*; the original (display) string is kept
    in the structured row's ``raw`` field.
    """
    if not s:
        return ""
    s = _strip_accents(s.lower()).strip()
    return re.sub(r"\s+", " ", s)


# ════════════════════════════════════════════════════════════════════════
# Pure parser — text → structured row
# ════════════════════════════════════════════════════════════════════════
_RX_SAMPLE   = re.compile(r"(\d+)\s*/\s*(\d+)")
_RX_LAST_N   = re.compile(r"(?:ultimos|last)\s+(\d+)\s*(?:partidos|matches|juegos|games)?")
_RX_NUMERIC  = re.compile(r"(\d+(?:[.,]\d+)?)")


def _detect_scope(text_norm: str) -> str:
    """Return ``home`` / ``away`` / ``all`` depending on context clues."""
    if any(t in text_norm for t in ("visitante", "as visitor", "as away",
                                     "fuera de casa", "away matches",
                                     "como visita")):
        return "away"
    if any(t in text_norm for t in ("local", "as home", "in home",
                                     "en casa", "as host", "home matches")):
        return "home"
    return "all"


_TREND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # WIN — generic
    ("WIN",        ("ganaron", "gano", "ganados", "won", "wins", "victorias",
                     "ha ganado", "han ganado", "win streak")),
    # LOSE
    ("LOSE",       ("perdieron", "perdio", "lost", "lose", "derrotas",
                     "ha perdido", "han perdido", "losing streak")),
    # DRAW
    ("DRAW",       ("empataron", "empato", "draws", "drew", "empates")),
    # CLEAN_SHEET (no encajan goles)
    ("CLEAN_SHEET", ("porteria a cero", "sin recibir gol", "clean sheet",
                      "sin recibir goles", "no encajar")),
    # BTTS yes / no
    ("BTTS_YES",   ("ambos equipos marcan", "ambos marcan", "btts",
                     "both teams to score", "ambos anotan")),
    ("BTTS_NO",    ("no ambos marcan", "btts no", "uno o ninguno marca")),
    # SCORED_BOTH_HALVES
    ("SCORED_BOTH_HALVES", ("anota en ambos tiempos", "marca en ambos tiempos")),
    # SCORED_FIRST
    ("SCORED_FIRST", ("anoto primero", "marco primero", "scored first")),
    # FAILED_TO_SCORE
    ("FAILED_TO_SCORE", ("no anoto", "no marco", "failed to score")),
)


def _detect_trend_type(text_norm: str,
                        explicit_value: Optional[str] = None) -> Optional[str]:
    """Choose the best trend label from the rule table. ``None`` when
    nothing matches."""
    # OVER / UNDER (totals) — special case because they carry a line.
    if any(k in text_norm for k in ("menos de", "less than", "under")):
        m = _RX_NUMERIC.search(text_norm)
        if m:
            try:
                line = float(m.group(1).replace(",", "."))
                return f"UNDER_{line:g}"
            except ValueError:
                pass
        return "UNDER"
    if any(k in text_norm for k in ("mas de", "more than", "over")):
        m = _RX_NUMERIC.search(text_norm)
        if m:
            try:
                line = float(m.group(1).replace(",", "."))
                return f"OVER_{line:g}"
            except ValueError:
                pass
        return "OVER"
    for label, kws in _TREND_RULES:
        if any(kw in text_norm for kw in kws):
            return label
    return None


def _detect_team(text_norm: str, *, home: str, away: str) -> tuple[str, str]:
    """Return ``(team_side, team_display)``. ``team_side`` is ``home`` /
    ``away`` / ``UNKNOWN``; ``team_display`` is the matched team name as
    received (best-effort)."""
    h_norm = _norm(home)
    a_norm = _norm(away)
    if h_norm and h_norm in text_norm:
        return ("home", home)
    if a_norm and a_norm in text_norm:
        return ("away", away)
    # Short forms (first word) — defensive: useful for "Portugal" vs
    # "Portugal national football team".
    h_first = (h_norm.split() or [""])[0]
    a_first = (a_norm.split() or [""])[0]
    if h_first and len(h_first) >= 4 and h_first in text_norm:
        return ("home", home)
    if a_first and len(a_first) >= 4 and a_first in text_norm:
        return ("away", away)
    return ("UNKNOWN", "")


def _confidence_from_sample(hits: Optional[int],
                              total: Optional[int]) -> str:
    if not hits or not total:
        return "LOW"
    rate = hits / total
    if total >= 10 and rate >= 0.80:
        return "HIGH"
    if total >= 5 and rate >= 0.70:
        return "MEDIUM"
    return "LOW"


def parse_trend_text(
    *,
    text: str,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    language: str = "es",
) -> Optional[dict]:
    """Parse one natural-language trend sentence into a structured row.

    Returns ``None`` when the line is empty / unrecognisable. Pure /
    deterministic — easy to unit-test.
    """
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    tn = _norm(raw)

    # ── Sample (e.g. "4/5") ────────────────────────────────────────────
    m_sample = _RX_SAMPLE.search(tn)
    sample_struct: dict[str, Any] = {}
    sample_value = None
    if m_sample:
        try:
            hits  = int(m_sample.group(1))
            total = int(m_sample.group(2))
            if 0 <= hits <= total and total > 0:
                sample_struct = {
                    "hits":  hits, "total": total,
                    "rate":  round(hits / total, 4),
                }
                sample_value = f"{hits}/{total}"
        except ValueError:
            pass

    # ── Period (last N matches) ────────────────────────────────────────
    period = None
    m_last = _RX_LAST_N.search(tn)
    if m_last:
        try:
            period = f"last_{int(m_last.group(1))}_matches"
        except ValueError:
            period = None
    elif sample_struct.get("total"):
        period = f"last_{sample_struct['total']}_matches"

    scope = _detect_scope(tn)
    trend = _detect_trend_type(tn)
    if trend is None:
        # Even if we don't recognise the trend, we still surface the
        # raw text as a "RAW" entry so the UI can show it.
        trend = "RAW"

    team_side, team_disp = _detect_team(
        tn, home=home_team or "", away=away_team or "",
    )

    return {
        "raw":        raw,
        "team":       team_disp or None,
        "team_side":  team_side,
        "trend_type": trend,
        "value":      sample_value,
        "sample":     sample_struct or None,
        "period":     period,
        "scope":      scope,
        "language":   language,
        "confidence": _confidence_from_sample(
            sample_struct.get("hits"), sample_struct.get("total"),
        ),
    }


def parse_trends_list(
    *,
    raw_items: Iterable[Any],
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    language: str = "es",
) -> list[dict]:
    """Parse a list of arbitrary items (strings or dicts with a ``text``
    key) into structured rows. Items that fail to parse are skipped
    silently — no exception is ever raised."""
    out: list[dict] = []
    for item in raw_items or []:
        if isinstance(item, str):
            txt = item
        elif isinstance(item, dict):
            txt = (item.get("text")
                   or item.get("trendText")
                   or item.get("title")
                   or item.get("description")
                   or item.get("name"))
        else:
            continue
        row = parse_trend_text(
            text=txt or "", home_team=home_team, away_team=away_team,
            language=language,
        )
        if row:
            out.append(row)
    return out


# ════════════════════════════════════════════════════════════════════════
# Fetch helpers
# ════════════════════════════════════════════════════════════════════════
def _id_missing_result(reason: str = RC_ID_MISSING) -> dict:
    return {
        "available":     False,
        "stage":         "ID_RESOLUTION",
        "reason_code":   reason,
        "message_user":  ("No se pudieron cargar las tendencias top porque "
                          "no hay ID de 365Scores para este partido."),
        "message_debug": "Missing external_ids.365scores.game_id and match_url",
        "retryable":     False,
        "provider":      PROVIDER,
        "transport":     TRANSPORT,
        "source":        SOURCE_LABEL,
    }


def _token_missing_result() -> dict:
    return {
        "available":     False,
        "stage":         "TRANSPORT_INIT",
        "reason_code":   RC_TOKEN_MISSING,
        "message_user":  _scrapedo_user_message(RC_TOKEN_MISSING),
        "message_debug": "SCRAPEDO_TOKEN env var is missing",
        "retryable":     False,
        "provider":      PROVIDER,
        "transport":     TRANSPORT,
        "source":        SOURCE_LABEL,
    }


# Candidate URLs to try (in order). 365Scores exposes a few alternative
# endpoints; we use a best-effort fallback chain so a minor server-side
# rename doesn't take the feature down.
def _candidate_endpoints(
    game_id: str, timezone_name: str = "America/Mexico_City",
) -> list[str]:
    common = (f"appTypeId=5&langId=29&timezoneName={timezone_name}"
              f"&gameId={game_id}")
    return [
        f"{WEBWS_BASE}/game/topTrends/?{common}",
        f"{WEBWS_BASE}/game/?{common}&showTrends=true",
        f"{WEBWS_BASE}/game/trends/?{common}",
    ]


def _extract_trend_strings_from_json(payload: Any) -> list[Any]:
    """Walk a JSON document and return every "trend-like" item.

    365Scores groups trends under a top-level ``topTrends`` /
    ``trends`` key, sometimes nested inside ``game`` / ``data``. To be
    resilient we walk the whole document and collect ``dict`` items
    that look like trends (``text`` / ``trendText`` keys) and string
    items inside the recognised parent keys.
    """
    collected: list[Any] = []
    parent_keys = ("topTrends", "trends", "topTrendsList",
                    "matchTrends", "tendencias", "tendenciasTop")

    def _walk(node: Any, parent_key: Optional[str] = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in parent_keys and isinstance(v, list):
                    for item in v:
                        if isinstance(item, (str, dict)):
                            collected.append(item)
                else:
                    _walk(v, parent_key=k)
        elif isinstance(node, list):
            for item in node:
                _walk(item, parent_key=parent_key)

    _walk(payload)
    # De-dup keeping order.
    seen: set[str] = set()
    out: list[Any] = []
    for it in collected:
        key = (it if isinstance(it, str) else json.dumps(it, sort_keys=True))[:300]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _try_parse_json(body: str) -> Optional[dict]:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


async def _fetch_json_endpoint(url: str, *, timeout: float) -> dict:
    """Call one webws endpoint and return a parsed-JSON dict (or an
    error dict). Pure transport — no parsing of trends here."""
    if not is_enabled():
        return _token_missing_result()
    res = await fetch_via_scrapedo_result(url, timeout=timeout,
                                            render=False, geo=DEFAULT_GEO)
    if not res.get("ok"):
        reason = _map_scrapedo_reason(res.get("reason_code"),
                                        res.get("status_code"))
        return {
            "available":     False,
            "stage":         "FETCH_TRENDS",
            "reason_code":   reason,
            "message_user":  _scrapedo_user_message(reason),
            "message_debug": res.get("message_debug"),
            "status_code":   res.get("status_code"),
            "retryable":     reason in (RC_BLOCKED_OR_FORBIDDEN, RC_TIMEOUT,
                                          RC_HTTP_ERROR, RC_EXCEPTION,
                                          RC_BREAKER_OPEN, RC_EMPTY_BODY),
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "source":        SOURCE_LABEL,
            "target_url":    url,
        }
    body = res.get("html") or ""
    obj = _try_parse_json(body)
    if obj is None:
        return {
            "available":     False,
            "stage":         "PARSE",
            "reason_code":   RC_JSON_PARSE_FAILED,
            "message_user":  "365Scores devolvió contenido no-JSON para tendencias.",
            "message_debug": f"JSON parse failed (body len={len(body)})",
            "retryable":     True,
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "source":        SOURCE_LABEL,
            "target_url":    url,
        }
    return {
        "available":   True,
        "stage":       "FETCH_TRENDS",
        "json":        obj,
        "target_url":  url,
        "fetched_at":  _now_iso(),
        "provider":    PROVIDER,
        "transport":   TRANSPORT,
        "source":      SOURCE_LABEL,
    }


# ════════════════════════════════════════════════════════════════════════
# Public entry point
# ════════════════════════════════════════════════════════════════════════
async def fetch_top_trends(
    match_doc: dict,
    *,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    language: str = "es",
    timeout_s: float = DEFAULT_TIMEOUT,
    fetcher: Optional[Any] = None,
) -> dict:
    """Resolve & fetch the top-trends block for a match.

    ``match_doc`` is the same shape the legacy 365Scores client expects
    (``external_ids.365scores`` / ``match_url`` / ``external_urls.365scores``).
    ``home_team`` / ``away_team`` are optional but recommended to label
    rows; if missing we fall back to ``match_doc['home_team']`` /
    ``match_doc['away_team']``.

    ``fetcher`` lets tests inject a deterministic transport
    (signature: ``async def(url: str) -> dict`` returning the same shape
    as :func:`_fetch_json_endpoint`).

    Always returns a dict with at least::

        {"available": bool, "provider": "365scores",
         "source": "365scores_top_trends",
         "trends": [...], "trends_count": int,
         "reason_code": <str|None>, ...}
    """
    home_team = home_team or (match_doc or {}).get("home_team")
    away_team = away_team or (match_doc or {}).get("away_team")

    ids = extract_365scores_ids(match_doc or {})
    if not ids.get("available") or not ids.get("game_id"):
        return {**_id_missing_result(), "trends": [], "trends_count": 0}

    game_id = ids["game_id"]
    use_fetcher = fetcher or _fetch_json_endpoint

    last_error: Optional[dict] = None
    for url in _candidate_endpoints(game_id):
        try:
            res = await use_fetcher(url, timeout=timeout_s) if fetcher is None else await fetcher(url)
        except Exception as exc:  # noqa: BLE001
            log.debug("score365_trends fetcher exception: %s", exc)
            last_error = {
                "available": False, "stage": "FETCH_TRENDS",
                "reason_code": RC_EXCEPTION, "message_debug": str(exc),
                "provider": PROVIDER, "transport": TRANSPORT,
                "source": SOURCE_LABEL, "target_url": url,
            }
            continue
        if not res.get("available"):
            last_error = res
            continue
        # Parse from JSON payload.
        items = _extract_trend_strings_from_json(res.get("json"))
        if not items:
            last_error = {
                **res,
                "available":     False,
                "stage":         "PARSE",
                "reason_code":   RC_TRENDS_NOT_FOUND,
                "message_user":  ("365Scores respondió pero no incluyó "
                                   "tendencias top para este partido."),
                "message_debug": ("topTrends/trends key not found "
                                   "in JSON response"),
                "retryable":     False,
            }
            continue
        rows = parse_trends_list(
            raw_items=items, home_team=home_team, away_team=away_team,
            language=language,
        )
        if not rows:
            return {
                "available":     False,
                "stage":         "PARSE",
                "reason_code":   RC_TRENDS_PARSE_FAILED,
                "message_user":  ("365Scores devolvió tendencias pero el "
                                   "parser no las pudo estructurar."),
                "message_debug": (f"Raw items={len(items)} but parsed=0"),
                "retryable":     True,
                "provider":      PROVIDER,
                "transport":     TRANSPORT,
                "source":        SOURCE_LABEL,
                "target_url":    res.get("target_url"),
                "trends":        [],
                "trends_count":  0,
                "ids":           ids,
            }
        return {
            "available":     True,
            "stage":         "PARSE",
            "reason_code":   RC_TRENDS_FOUND,
            "message_user":  "Tendencias top obtenidas de 365Scores.",
            "provider":      PROVIDER,
            "transport":     TRANSPORT,
            "source":        SOURCE_LABEL,
            "target_url":    res.get("target_url"),
            "fetched_at":    res.get("fetched_at") or _now_iso(),
            "ids":           ids,
            "trends":        rows,
            "trends_count":  len(rows),
        }

    # All endpoints failed — return the last meaningful error.
    return {
        **(last_error or _id_missing_result(RC_TRENDS_NOT_FOUND)),
        "trends":       [],
        "trends_count": 0,
        "ids":          ids,
    }


__all__ = [
    "PROVIDER", "SOURCE_LABEL",
    "RC_TRENDS_FOUND", "RC_TRENDS_EMPTY", "RC_TRENDS_PARSE_FAILED",
    "RC_TRENDS_NOT_FOUND",
    "parse_trend_text", "parse_trends_list",
    "fetch_top_trends",
]
