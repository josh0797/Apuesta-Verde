"""Phase F85.2 — Forebet match-detail context scraper.

Sibling of the legacy :mod:`services.forebet_scraper` (which only parses
Forebet's HOME PAGE list of fixtures). This module pulls **per-match
detail pages** like::

    https://www.forebet.com/es/football/matches/usa-paraguay-2463132

and returns the algorithmic prediction context (predicted scoreline,
1X2 probabilities, over/under hints, average-goals estimate). It is
**never** used as a source of xG — only as soft context that the
editorial layer can use to confirm/conflict the FBref xG profile.

Fail-soft: every error path returns ``{"available": False, ...}``.
The fetcher uses :func:`services.scrape_do_client.fetch_via_scrapedo`
in production (Forebet's WAF blocks plain UAs) and degrades to the
caller-supplied ``httpx.AsyncClient`` when one is provided (tests).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from selectolax.parser import HTMLParser

log = logging.getLogger("forebet_client")

# Reason codes — stable strings.
RC_URL_MISSING       = "FOREBET_URL_MISSING"
RC_UNAVAILABLE       = "FOREBET_UNAVAILABLE"
RC_PARSE_FAILED      = "FOREBET_PARSE_FAILED"
RC_CONTEXT_AVAILABLE = "FOREBET_CONTEXT_AVAILABLE"

DEFAULT_FETCH_TIMEOUT  = float(os.environ.get("FOREBET_FETCH_TIMEOUT_SECONDS", "8"))
CACHE_TTL_HOURS        = int(os.environ.get("FOREBET_CACHE_TTL_HOURS", "6"))
FOREBET_HOST_REGEX     = re.compile(r"^https?://(www\.)?forebet\.com/", re.I)


# ─────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────
_SCORE_REGEX = re.compile(r"\b(\d)\s*[-:]\s*(\d)\b")
_PCT_REGEX   = re.compile(r"(\d{1,3})\s*%")
_AVG_GOALS_REGEX = re.compile(
    r"(?:promedio|media|avg(?:erage)?)[^0-9]{0,30}(\d+(?:[.,]\d+)?)",
    re.I,
)


def _clean(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _to_int_safe(s: Any) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _to_float_safe(s: Any) -> Optional[float]:
    if s is None:
        return None
    txt = str(s).strip().replace(",", ".")
    if not txt:
        return None
    try:
        return float(txt)
    except (TypeError, ValueError):
        return None


def _extract_teams(tree: HTMLParser) -> tuple[Optional[str], Optional[str]]:
    """Return ``(home, away)`` team names.

    Forebet detail pages expose two anchors with class ``homeTeam`` /
    ``awayTeam`` inside a header block. We also accept generic
    ``h1`` / OG meta as a fallback.
    """
    home = away = None
    h = tree.css_first(".homeTeam, .home-team, .teamHome, .team-home")
    a = tree.css_first(".awayTeam, .away-team, .teamAway, .team-away")
    if h is not None:
        home = _clean(h.text())
    if a is not None:
        away = _clean(a.text())

    if not (home and away):
        og = tree.css_first('meta[property="og:title"]')
        title = (og.attributes or {}).get("content") if og is not None else None
        if title:
            m = re.search(r"(.+?)\s*[-–vs.]+\s*(.+?)(?:[,:]|$)", title)
            if m:
                home = home or _clean(m.group(1))
                away = away or _clean(m.group(2))

    if not (home and away):
        h1 = tree.css_first("h1")
        if h1 is not None:
            m = re.search(r"(.+?)\s*[-–vs.]+\s*(.+)", _clean(h1.text()))
            if m:
                home = home or _clean(m.group(1))
                away = away or _clean(m.group(2))

    return home, away


def _extract_predicted_score(tree: HTMLParser) -> Optional[str]:
    # Try a few well-known containers first.
    candidates = tree.css(".predicted, .prediction-score, .predictionScore, "
                          ".scorePrediction, span.exact")
    for c in candidates:
        m = _SCORE_REGEX.search(c.text() or "")
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    # Fallback: any element bearing the text "predicción" near a score.
    body_text = tree.body.text(separator=" ") if tree.body else ""
    m = re.search(
        r"(?:predicci[oó]n|prediction)[^0-9]{0,40}(\d)\s*[-:]\s*(\d)",
        body_text, re.I,
    )
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def _extract_prediction_1x2(tree: HTMLParser) -> Optional[str]:
    """Return ``"1"``, ``"X"`` or ``"2"`` for the highlighted pick."""
    node = tree.css_first(".pick, .prediction-tip, .forebet-tip")
    if node is not None:
        txt = _clean(node.text()).upper()
        if txt in {"1", "X", "2"}:
            return txt
    # Fallback: scan the body for "Pick: 1" style phrases.
    body_text = tree.body.text(separator=" ") if tree.body else ""
    m = re.search(r"\b(?:pick|tip)\s*[:\-]?\s*([1X2])\b", body_text, re.I)
    if m:
        return m.group(1).upper()
    return None


def _extract_probabilities(tree: HTMLParser) -> dict:
    """Best-effort ``{home, draw, away}`` percentages."""
    # Common pattern: 3 spans with class indicating the side + percent.
    out: dict[str, Optional[int]] = {"home": None, "draw": None, "away": None}
    side_map = {
        "home": (".percent.home, .prob-home, .p1, td.percent-home"),
        "draw": (".percent.draw, .prob-draw, .pX, td.percent-draw"),
        "away": (".percent.away, .prob-away, .p2, td.percent-away"),
    }
    for side, selector in side_map.items():
        node = tree.css_first(selector)
        if node is None:
            continue
        m = _PCT_REGEX.search(node.text() or "")
        if m:
            out[side] = _to_int_safe(m.group(1))

    # Heuristic fallback: any 3 consecutive numbers in [0,100] that sum
    # to ~100 inside a probability block.
    if not any(out.values()):
        block = tree.css_first(".prob, .percent_block, .forebetProb")
        if block is not None:
            nums = [_to_int_safe(x) for x in _PCT_REGEX.findall(block.text() or "")]
            nums = [n for n in nums if n is not None]
            if len(nums) >= 3 and 90 <= sum(nums[:3]) <= 110:
                out["home"], out["draw"], out["away"] = nums[:3]
    return out


def _extract_goals_context(tree: HTMLParser) -> dict:
    """Return ``{avg_goals_hint, over_2_5_hint, under_3_5_hint}``."""
    body_text = tree.body.text(separator=" ") if tree.body else ""
    out: dict[str, Any] = {
        "avg_goals_hint":  None,
        "over_2_5_hint":   None,
        "under_3_5_hint":  None,
    }

    m_avg = _AVG_GOALS_REGEX.search(body_text)
    if m_avg:
        out["avg_goals_hint"] = _to_float_safe(m_avg.group(1))

    # Over / Under hints.
    if re.search(r"\bover\s*2[.,]?5\b", body_text, re.I):
        out["over_2_5_hint"] = True
    if re.search(r"\bunder\s*3[.,]?5\b", body_text, re.I):
        out["under_3_5_hint"] = True
    if re.search(r"\bunder\s*2[.,]?5\b", body_text, re.I):
        out["over_2_5_hint"] = False
    if re.search(r"\bover\s*3[.,]?5\b", body_text, re.I):
        out["under_3_5_hint"] = False

    return out


def _extract_raw_summary(tree: HTMLParser, *, max_len: int = 400) -> Optional[str]:
    # Forebet detail pages include a free-form analysis paragraph.
    for selector in (".analysis", ".match-analysis", ".forebet-analysis",
                      ".prediction-text", "div.preview"):
        node = tree.css_first(selector)
        if node is None:
            continue
        txt = _clean(node.text())
        if txt:
            return txt[:max_len]
    return None


def parse_forebet_match_html(html: str, *, source_url: Optional[str] = None) -> dict:
    """Parse a Forebet match-detail HTML payload. Fail-soft."""
    if not isinstance(html, str) or not html.strip():
        return {"available": False, "reason_codes": [RC_UNAVAILABLE],
                "match_url": source_url}
    try:
        tree = HTMLParser(html)
    except Exception as exc:  # noqa: BLE001
        log.warning("[forebet] HTMLParser failed: %s", exc)
        return {"available": False, "reason_codes": [RC_PARSE_FAILED],
                "match_url": source_url}

    home, away = _extract_teams(tree)
    predicted_score = _extract_predicted_score(tree)
    prediction_1x2  = _extract_prediction_1x2(tree)
    probabilities   = _extract_probabilities(tree)
    goals_context   = _extract_goals_context(tree)
    summary         = _extract_raw_summary(tree)

    # If we couldn't extract teams AND a prediction AND any prob → we
    # never confirmed it was a real Forebet match page (could be 404 /
    # generic landing). Mark unavailable so the orchestrator knows.
    has_payload = any([
        home and away,
        predicted_score,
        prediction_1x2,
        any(v is not None for v in probabilities.values()),
        goals_context["avg_goals_hint"] is not None,
    ])
    if not has_payload:
        return {"available": False, "reason_codes": [RC_PARSE_FAILED],
                "match_url": source_url}

    return {
        "available":       True,
        "source":          "forebet",
        "match_url":       source_url,
        "home_team":       home,
        "away_team":       away,
        "predicted_score": predicted_score,
        "prediction":      prediction_1x2,
        "probabilities":   probabilities,
        "goals_context":   goals_context,
        "raw_text_summary": summary,
        "reason_codes":    [RC_CONTEXT_AVAILABLE],
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# Network layer
# ─────────────────────────────────────────────────────────────────────
async def _fetch_html(
    client: Optional[httpx.AsyncClient],
    url: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> Optional[str]:
    if client is not None:
        try:
            r = await client.get(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("[forebet] httpx fetch failed for %s: %s", url, exc)
            return None
        if r.status_code != 200 or not r.text:
            return None
        return r.text
    try:
        from .. import scrape_do_client as sdc
    except Exception:  # noqa: BLE001
        return None
    return await sdc.fetch_via_scrapedo(url, timeout=timeout)


async def fetch_forebet_match_context(
    client: Optional[httpx.AsyncClient],
    url: str,
    *,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> dict:
    """Fetch and parse a Forebet match-detail URL. Fail-soft."""
    if not url or not isinstance(url, str):
        return {"available": False, "reason_codes": [RC_URL_MISSING]}
    if not FOREBET_HOST_REGEX.match(url):
        return {"available": False, "reason_codes": [RC_URL_MISSING],
                "match_url": url}

    html = await _fetch_html(client, url, timeout=timeout)
    if not html:
        return {"available": False, "reason_codes": [RC_UNAVAILABLE],
                "match_url": url}
    return parse_forebet_match_html(html, source_url=url)


__all__ = [
    "fetch_forebet_match_context", "parse_forebet_match_html",
    "RC_URL_MISSING", "RC_UNAVAILABLE", "RC_PARSE_FAILED",
    "RC_CONTEXT_AVAILABLE",
    "DEFAULT_FETCH_TIMEOUT", "CACHE_TTL_HOURS",
]
