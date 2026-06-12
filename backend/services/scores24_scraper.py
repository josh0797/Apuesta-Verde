"""Scores24 Scraper — Fix 1 (Phase F58 enrichment).

Pulls the *Pronóstico* tab of a Scores24 match URL through Bright Data
``web_unlocker1`` and extracts **only** three editorial sections:

  1. ``corners_prediction``  → "Predicción sobre córners"
  2. ``apuesta_fiable``      → "Apuesta fiable"
  3. ``prediccion_redaccion`` → "Predicción de la redacción"

The parser extracts the **explicit bet** mentioned in the text (e.g. "under
9.5 córners totales" → ``side=UNDER, line=9.5, market_type=corners_total``)
and separates **narrative context** from the recommended market — the
explicit bet text always trumps the surrounding narrative.

Integration philosophy
----------------------
* **Enrichment-only.** The caller attaches the result as additional
  context; it MUST NOT drive a pick by itself. The football engine keeps
  its own selection logic.
* **Fail-soft.** No Bright Data → return ``{available: False, ...}``.
  Network errors / parse errors → same shape, never raises.
* **Cached.** In-memory cache 6h per match URL (politeness + cost).

API key handling
----------------
NO HARDCODED KEYS. The Bright Data credentials live in the environment
variables already used by ``services.editorial_context.brightdata_fetcher``:

  * ``BRIGHTDATA_API_KEY``
  * ``BRIGHTDATA_ZONE``  (default ``web_unlocker1``)

The scraper reuses ``_BrightDataClient`` so the auth + zone routing stays
consistent across the codebase.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

log = logging.getLogger("scores24_scraper")

ENGINE_VERSION = "scores24_scraper.v1"

# ── Cache ────────────────────────────────────────────────────────────
_CACHE_TTL = timedelta(hours=6)
_CACHE: dict[str, tuple[datetime, dict]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_get(key: str) -> Optional[dict]:
    hit = _CACHE.get(key)
    if not hit:
        return None
    exp, val = hit
    if _now() > exp:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, value: dict) -> None:
    _CACHE[key] = (_now() + _CACHE_TTL, value)


def cache_clear() -> None:
    """Test helper — drops the in-memory cache."""
    _CACHE.clear()


# ─────────────────────────────────────────────────────────────────────
# Helpers — normalisation
# ─────────────────────────────────────────────────────────────────────
def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_text(s: str) -> str:
    """Collapse whitespace + remove control characters for matching."""
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", "."))
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────
# Section detector (heading + body)
# ─────────────────────────────────────────────────────────────────────
# Map heading keywords (accent-insensitive, lowercased) → canonical key.
_SECTION_HEADINGS = [
    # (regex on normalised heading text, canonical_key, display_title)
    (re.compile(r"prediccion\s+sobre\s+corners", re.IGNORECASE),
     "corners_prediction",   "Predicción sobre córners"),
    (re.compile(r"apuesta\s+fiable",             re.IGNORECASE),
     "apuesta_fiable",       "Apuesta fiable"),
    (re.compile(r"prediccion\s+de\s+la\s+redaccion", re.IGNORECASE),
     "prediccion_redaccion", "Predicción de la redacción"),
]


def _extract_sections_from_html(html: str) -> list[dict]:
    """Walk the HTML and return [{section, title, text}] for the 3 target
    sections. Uses BeautifulSoup when available; otherwise falls back to a
    naive regex on heading tags.
    """
    sections: list[dict] = []
    if not html:
        return sections

    # Try BeautifulSoup first (already a dependency of the editorial code).
    soup = None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log.debug("bs4 import/parse failed, falling back to regex: %s", exc)

    if soup is not None:
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            heading_norm = _normalize_text(_strip_accents(heading.get_text(" ", strip=True)))
            for pat, key, display in _SECTION_HEADINGS:
                if pat.search(heading_norm):
                    body = []
                    sib = heading.next_sibling
                    # Capture up to ~6 siblings until the next heading.
                    for _ in range(12):
                        if sib is None:
                            break
                        if getattr(sib, "name", None) in ("h1", "h2", "h3", "h4"):
                            break
                        if hasattr(sib, "get_text"):
                            txt = sib.get_text(" ", strip=True)
                            if txt:
                                body.append(txt)
                        sib = sib.next_sibling
                    text = _normalize_text(" ".join(body))
                    if text:
                        sections.append({
                            "section": key,
                            "title":   display,
                            "text":    text,
                        })
                    break
        if sections:
            return sections

    # Regex fallback (very conservative — captures only `<h*>...heading...</h*>` +
    # following block of text up to the next heading).
    for pat, key, display in _SECTION_HEADINGS:
        # Match any `<h{2,4}>...heading...</h>` then capture until next heading.
        m = re.search(
            r"<h[1-4][^>]*>[^<]*?"
            + pat.pattern
            + r"[^<]*?</h[1-4]>(.*?)(?=<h[1-4][^>]*>|$)",
            html, flags=re.IGNORECASE | re.DOTALL,
        )
        if not m:
            continue
        raw = m.group(1)
        clean = re.sub(r"<[^>]+>", " ", raw)
        text = _normalize_text(clean)
        if text:
            sections.append({"section": key, "title": display, "text": text})
    return sections


# ─────────────────────────────────────────────────────────────────────
# Bet parsing — extract the explicit bet from a Spanish editorial text
# ─────────────────────────────────────────────────────────────────────
# Patterns ordered: corners → goals → BTTS → handicap → playerToScore.
_BET_PATTERNS: list[tuple[re.Pattern[str], dict]] = [
    # ── Corners totals: "under/over 9.5 córners totales"
    (re.compile(
        r"\b(under|over|menos\s+de|mas\s+de|m[aá]s\s+de)\s+(\d+(?:[.,]\d+)?)\s+c[oó]rner",
        re.IGNORECASE,
     ),
     {"market_type": "corners_total", "side_map": {
         "under": "UNDER", "over": "OVER",
         "menos de": "UNDER", "mas de": "OVER", "más de": "OVER",
     }}),
    # ── Match goals total: "over 1.5 goles totales" / "over 1.5 goles"
    (re.compile(
        r"\b(under|over|menos\s+de|mas\s+de|m[aá]s\s+de)\s+(\d+(?:[.,]\d+)?)\s+goles",
        re.IGNORECASE,
     ),
     {"market_type": "goals_total", "side_map": {
         "under": "UNDER", "over": "OVER",
         "menos de": "UNDER", "mas de": "OVER", "más de": "OVER",
     }}),
    # ── BTTS: "ambos marcan / both teams to score"
    (re.compile(
        r"\b(ambos\s+marcan|both\s+teams?\s+to\s+score|btts)\b",
        re.IGNORECASE,
     ),
     {"market_type": "btts", "side_map": {"_default": "YES"}}),
    # ── Handicap: "handicap -1" / "hándicap (-1)" / "handicap +1.5"
    (re.compile(
        r"h[aá]ndicap\s*\(?\s*([+-]?\d+(?:[.,]\d+)?)\s*\)?",
        re.IGNORECASE,
     ),
     {"market_type": "handicap", "side_map": {}}),
]


# Odds pattern — supports both "Cuota: 1.58" and "una cuota cercana a 1.35*".
# Allows up to ~30 chars of filler words between "cuota" and the numeric value.
_ODDS_PATTERN = re.compile(
    r"cuota[s]?\b[^0-9]{0,30}?([0-9]+(?:[.,][0-9]+)?)\b", re.IGNORECASE,
)


def _extract_explicit_bet(text: str) -> dict:
    """Return {recommended_market, market_type, side, line, odds, reason_codes}.

    Always returns a dict (possibly with all-None values) — fail-soft. The
    `reason_codes` audit trail lets the caller understand *why* the parser
    landed on a given verdict.
    """
    if not text:
        return {
            "recommended_market": None,
            "market_type":        None,
            "side":               None,
            "line":               None,
            "odds":               None,
            "reason_codes":       [],
        }

    # Strip accents for matching (keeps raw text for the recommended_market label).
    raw  = text
    norm = _strip_accents(text)

    matched_market_type: Optional[str] = None
    matched_side:        Optional[str] = None
    matched_line:        Optional[float] = None
    matched_label:       Optional[str] = None
    reason_codes:        list[str] = []

    for pat, meta in _BET_PATTERNS:
        m = pat.search(norm)
        if not m:
            continue
        matched_market_type = meta["market_type"]
        if meta["market_type"] in ("corners_total", "goals_total"):
            side_token = m.group(1).lower().strip()
            line_str   = m.group(2).replace(",", ".")
            matched_side = meta["side_map"].get(side_token) or meta["side_map"].get(side_token.replace("á", "a"))
            matched_line = _safe_float(line_str)
            # Re-extract label from original text preserving accents.
            _esc_side = re.escape(side_token).replace(" ", r"\s+")
            _esc_line = re.escape(line_str)
            label_m = re.search(
                rf"\b{_esc_side}\s+{_esc_line}\s+(c[oó]rner\w*|goles)",
                raw, re.IGNORECASE,
            )
            if label_m:
                matched_label = label_m.group(0).strip()
            else:
                kind = "córners" if meta["market_type"] == "corners_total" else "goles"
                matched_label = f"{matched_side.title()} {matched_line:g} {kind}"
            reason_codes.append(
                "SCORES24_CORNERS_PREDICTION_FOUND" if meta["market_type"] == "corners_total"
                else "SCORES24_GOALS_TOTAL_FOUND"
            )
            if matched_side == "UNDER":
                reason_codes.append(
                    "SCORES24_CORNERS_UNDER_LINE_FOUND" if meta["market_type"] == "corners_total"
                    else "SCORES24_GOALS_UNDER_LINE_FOUND"
                )
            elif matched_side == "OVER":
                reason_codes.append(
                    "SCORES24_CORNERS_OVER_LINE_FOUND" if meta["market_type"] == "corners_total"
                    else "SCORES24_GOALS_OVER_LINE_FOUND"
                )
            break
        if meta["market_type"] == "btts":
            matched_market_type = "btts"
            matched_side  = "YES"
            matched_label = "Ambos marcan"
            reason_codes.append("SCORES24_BTTS_FOUND")
            break
        if meta["market_type"] == "handicap":
            line_str = m.group(1).replace(",", ".")
            matched_line = _safe_float(line_str)
            matched_side = None
            matched_label = f"Hándicap ({line_str})"
            reason_codes.append("SCORES24_HANDICAP_FOUND")
            break

    # Odds extraction (search in the same text near the bet expression).
    odds_val: Optional[float] = None
    odds_match = _ODDS_PATTERN.search(raw)
    if odds_match:
        odds_val = _safe_float(odds_match.group(1))
        if odds_val is not None:
            reason_codes.append("SCORES24_ODDS_FOUND")

    return {
        "recommended_market": matched_label,
        "market_type":        matched_market_type,
        "side":               matched_side,
        "line":               matched_line,
        "odds":               odds_val,
        "reason_codes":       reason_codes,
    }


def _build_section_payload(section: dict) -> dict:
    """Combine raw text + bet extraction + narrative_context separation."""
    text = section.get("text") or ""
    bet  = _extract_explicit_bet(text)

    # Narrative context = the prose before / around the explicit bet sentence.
    # Heuristic: drop the sentence(s) containing the bet match.
    narrative_context = text
    if bet.get("recommended_market"):
        narrative_context = re.sub(
            re.escape(bet["recommended_market"]), "[BET]", text, flags=re.IGNORECASE,
        )
        # Trim to first ~280 chars for UI use.
        narrative_context = (narrative_context or "")[:280].strip()

    return {
        "section":           section.get("section"),
        "title":             section.get("title"),
        "text":              text,
        "narrative_context": narrative_context,
        "recommended_market": bet["recommended_market"],
        "market_type":       bet["market_type"],
        "side":              bet["side"],
        "line":              bet["line"],
        "odds":              bet["odds"],
        "reason_codes":      bet["reason_codes"],
    }


# ─────────────────────────────────────────────────────────────────────
# Fetcher (Bright Data web_unlocker1) — direct /request with format=raw
# ─────────────────────────────────────────────────────────────────────
_BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"
_BRIGHTDATA_TIMEOUT_SEC = 30.0


async def _fetch_scores24_html(url: str) -> Optional[str]:
    """Fetch ``url`` via Bright Data ``web_unlocker1`` zone using
    ``format=raw`` (per the user's spec).

    Returns the raw HTML on 2xx upstream, ``None`` on any failure
    (network, 4xx/5xx, anti-bot, or missing env credentials). Never raises.

    Credentials are read from environment variables ONLY — never hardcoded:

      * ``BRIGHTDATA_API_KEY`` — Bearer token
      * ``BRIGHTDATA_ZONE``    — defaults to ``web_unlocker1``

    The function also caches the most recent Bright Data error metadata
    in ``_LAST_BRIGHTDATA_DIAGNOSTIC`` so the caller / preview endpoint
    can surface configuration issues (e.g. wrong zone product type).
    """
    import os
    import httpx

    api_key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    zone    = os.environ.get("BRIGHTDATA_ZONE", "web_unlocker1").strip()
    if not api_key or not zone:
        _set_last_diagnostic({"reason": "missing_credentials"})
        log.info("[SCORES24] BrightData not configured (BRIGHTDATA_API_KEY / BRIGHTDATA_ZONE)")
        return None

    payload = {
        "zone":    zone,
        "url":     url,
        "format":  "raw",
        # Country routing — scores24.live serves different anti-bot logic
        # per geo. ES gives best results for /es/soccer paths.
        "country": "es",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_BRIGHTDATA_TIMEOUT_SEC, http2=False) as client:
            r = await client.post(_BRIGHTDATA_ENDPOINT, json=payload, headers=headers)
    except (httpx.HTTPError, Exception) as exc:  # noqa: BLE001
        _set_last_diagnostic({"reason": "transport_error", "detail": str(exc)})
        log.warning("[SCORES24_FETCH_FAILED] %s: transport error: %s", url, exc)
        return None

    # Surface Bright Data diagnostic headers for the operator (wrong zone
    # type / SERP-vs-WebUnlocker mismatch is invisible without them).
    brd_err_code  = r.headers.get("x-brd-error-code")  or r.headers.get("x-brd-err-code")
    brd_err_msg   = r.headers.get("x-brd-error")       or r.headers.get("x-brd-err-msg")
    brd_status    = r.headers.get("x-brd-status-code") or r.headers.get("x-brd-status")
    proxy_status  = r.headers.get("proxy-status")
    diagnostic = {
        "api_http_status":   r.status_code,
        "brd_error_code":    brd_err_code,
        "brd_error":         brd_err_msg,
        "brd_status_code":   brd_status,
        "proxy_status":      proxy_status,
        "zone":              zone,
    }

    if r.status_code != 200:
        diagnostic["reason"] = "bd_api_error"
        diagnostic["body_preview"] = (r.text or "")[:200]
        _set_last_diagnostic(diagnostic)
        log.warning("[SCORES24_FETCH_FAILED] %s: BD API HTTP %s (%s)",
                    url, r.status_code, (r.text or "")[:200])
        return None

    # 200 from BD but upstream may have failed — header tells us.
    if brd_err_code or (brd_status and str(brd_status).startswith("4")):
        diagnostic["reason"] = "bd_upstream_error"
        diagnostic["body_preview"] = (r.text or "")[:200]
        _set_last_diagnostic(diagnostic)
        log.warning("[SCORES24_FETCH_BLOCKED] %s: brd_err_code=%s brd_status=%s msg=%s",
                    url, brd_err_code, brd_status, (brd_err_msg or "")[:200])
        return None

    body = r.text or ""
    if len(body) < 200:
        diagnostic["reason"] = "tiny_body"
        diagnostic["body_preview"] = body[:200]
        _set_last_diagnostic(diagnostic)
        log.info("[SCORES24_FETCH_EMPTY] %s: tiny body (%d bytes)", url, len(body))
        return None

    diagnostic["reason"] = "ok"
    _set_last_diagnostic(diagnostic)
    return body


# ── Last Bright Data diagnostic (for UI/preview operator visibility) ──
_LAST_BRIGHTDATA_DIAGNOSTIC: dict = {}


def _set_last_diagnostic(d: dict) -> None:
    global _LAST_BRIGHTDATA_DIAGNOSTIC
    _LAST_BRIGHTDATA_DIAGNOSTIC = dict(d or {})


def get_last_brightdata_diagnostic() -> dict:
    """Return the most recent Bright Data diagnostic metadata.

    Useful for ``/api/scores24/preview`` to expose **why** a fetch failed
    (e.g. "wrong_api" → zone is SERP product instead of Web Unlocker).
    """
    return dict(_LAST_BRIGHTDATA_DIAGNOSTIC)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def scrape_scores24_match(
    *,
    url: str,
    use_cache: bool = True,
    fetcher: Optional[Any] = None,
) -> dict:
    """Scrape Scores24 *Pronóstico* sections for a single match URL.

    Parameters
    ----------
    url
        Full Scores24 match URL (e.g. ``https://scores24.live/es/soccer/
        m-11-06-2026-mexico-south-africa-prediction``).
    use_cache
        Whether to honour the 6h in-memory cache. Tests pass ``False``.
    fetcher
        Optional injectable async callable ``fetcher(url) -> str | None``
        for testing. Defaults to the Bright Data fetcher.

    Returns
    -------
    dict (ALWAYS, fail-soft)::

        {
          "available":      bool,
          "engine_version": str,
          "url":            str,
          "source":         "scores24:web_unlocker1" | "unavailable",
          "fetched_at":     iso str,
          "sections":       [ {section, title, text, recommended_market, ...} ],
          "consensus":      { "primary_market": str|None, ... },
          "raw_html_size":  int | None,
          "reason_codes":   [str, ...],
        }
    """
    if not url or not isinstance(url, str):
        return {
            "available":      False,
            "engine_version": ENGINE_VERSION,
            "url":             url,
            "source":          "unavailable",
            "sections":        [],
            "consensus":       {},
            "reason_codes":    ["SCORES24_INVALID_URL"],
        }

    if use_cache:
        cached = _cache_get(url)
        if cached is not None:
            return cached

    f = fetcher or _fetch_scores24_html
    try:
        html = await f(url)
    except Exception as exc:  # noqa: BLE001
        log.debug("scores24 fetcher raised: %s", exc)
        html = None

    if not html:
        payload = {
            "available":      False,
            "engine_version": ENGINE_VERSION,
            "url":             url,
            "source":          "unavailable",
            "fetched_at":      _now().isoformat(),
            "sections":        [],
            "consensus":       {},
            "raw_html_size":   None,
            "reason_codes":    ["SCORES24_FETCH_FAILED"],
        }
        _cache_set(url, payload)
        return payload

    raw_sections = _extract_sections_from_html(html)
    enriched = [_build_section_payload(s) for s in raw_sections]

    # Consensus: aggregate explicit-bet picks across sections.
    explicit_markets = [s for s in enriched if s.get("recommended_market")]
    primary = None
    if explicit_markets:
        # Prefer Apuesta Fiable > Predicción sobre córners > Predicción de la redacción
        priority = {"apuesta_fiable": 0, "corners_prediction": 1, "prediccion_redaccion": 2}
        explicit_markets.sort(key=lambda s: priority.get(s["section"], 99))
        primary = explicit_markets[0]

    consensus = {
        "primary_section":    primary["section"] if primary else None,
        "primary_market":     primary["recommended_market"] if primary else None,
        "primary_market_type": primary["market_type"] if primary else None,
        "primary_side":       primary["side"] if primary else None,
        "primary_line":       primary["line"] if primary else None,
        "primary_odds":       primary["odds"] if primary else None,
        "explicit_market_count": len(explicit_markets),
    }

    reason_codes = ["SCORES24_FETCH_OK"]
    if enriched:
        reason_codes.append(f"SCORES24_SECTIONS_FOUND_{len(enriched)}")
    if primary:
        reason_codes.append("SCORES24_PRIMARY_MARKET_IDENTIFIED")
    for s in enriched:
        reason_codes.extend(s.get("reason_codes") or [])
    # De-dupe
    seen: set[str] = set()
    reason_codes = [r for r in reason_codes if not (r in seen or seen.add(r))]

    payload = {
        "available":      bool(enriched),
        "engine_version": ENGINE_VERSION,
        "url":             url,
        "source":          "scores24:web_unlocker1",
        "fetched_at":      _now().isoformat(),
        "sections":        enriched,
        "consensus":       consensus,
        "raw_html_size":   len(html),
        "reason_codes":    reason_codes,
    }
    _cache_set(url, payload)
    return payload


__all__ = [
    "ENGINE_VERSION",
    "cache_clear",
    "scrape_scores24_match",
    "get_last_brightdata_diagnostic",
    # Exposed for tests:
    "_extract_sections_from_html",
    "_extract_explicit_bet",
    "_build_section_payload",
    "_fetch_scores24_html",
]
