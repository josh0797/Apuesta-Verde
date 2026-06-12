"""Phase F70 — Forebet parser & scraper.

Forebet exposes an algorithmic 1X2 probability + score prediction for
every match. Bright Data blocks their match pages (Gambling policy);
scrape.do works for both the home and the predictions index.

What we extract (per fixture):
  * home_team, away_team, competition
  * kickoff_iso (date + time)
  * forebet_pct_1, forebet_pct_x, forebet_pct_2  (algorithmic probs)
  * predicted_score (e.g. "1-1")
  * goals_avg (Forebet's calibrated total-goals estimate)
  * pick_tag (the highlighted recommendation, e.g. "Over 2.5")

Forebet's home page exposes rows like:
    <div class="rcnt" onclick="location.href=..."> ... </div>

The text payload of each row looks like:
    "WCSouth KoreaCzech Republic12/06/2026 04:00 33 35 32 X 1-1 1 - 1 2.00 77° ..."

We parse it with a positional regex + competition lookup.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from selectolax.parser import HTMLParser

log = logging.getLogger("forebet_parser")


# Forebet competition codes (very partial, extracted from the home page).
# We don't need to be exhaustive — unknown codes are emitted verbatim.
_COMP_CODE_RE = re.compile(r"^([A-Z]{1,5})(?=[A-ZÁÉÍÓÚÑ])")


def _txt(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(strip=True)).strip()


# ─────────────────────────────────────────────────────────────────────
# Fixture row parser
# ─────────────────────────────────────────────────────────────────────
def _parse_rcnt_row(row_node) -> Optional[dict]:
    """Parse one ``div.rcnt`` row. The row's onclick handler usually
    holds the canonical match URL — but those deep pages are blocked
    by anti-bot. We extract everything we can from the row text.
    """
    href = row_node.attributes.get("onclick") or ""
    m_url = re.search(r"location\.href=['\"]([^'\"]+)['\"]", href)
    match_url = m_url.group(1) if m_url else None

    # Spans usually carry semantic info.
    text = row_node.text(separator=" ", strip=False) if row_node else ""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    # Pattern (positional, observed on multiple matches):
    #   <COMP><HomeTeam><AwayTeam><date> <time> <hPct> <dPct> <aPct> <pick> <score>
    # Example:
    #   "WC South Korea Czech Republic 12/06/2026 04:00 33 35 32 X 1-1 ..."
    # We work backwards from the date token because team names can
    # contain spaces and the competition code is variable.
    m = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+"
        r"(\d{1,3})\s*(\d{1,3})\s*(\d{1,3})\s+"
        r"([12X])\s+(\d+\s*-\s*\d+)",
        text,
    )
    if not m:
        return None
    date_str   = m.group(1)
    time_str   = m.group(2)
    home_pct   = int(m.group(3))
    draw_pct   = int(m.group(4))
    away_pct   = int(m.group(5))
    pick_1x2   = m.group(6)
    score_pred = re.sub(r"\s*", "", m.group(7))

    # Everything BEFORE the date is "<COMP><HOME><AWAY>".
    head = text[:m.start()].strip()
    # Extract leading short uppercase competition code.
    comp_m = re.match(r"([A-ZÁÉÍÓÚÑ]{1,5})\s*(.+)", head)
    if not comp_m:
        return None
    competition = comp_m.group(1)
    remainder = comp_m.group(2).strip()

    # Splitting "South Korea Czech Republic" requires either a fixed
    # team-name dictionary OR a heuristic. Forebet emits the row without
    # a separator. We use a heuristic: prefer the longest split point
    # where both halves start with an uppercase letter. Fallback: split
    # at the middle whitespace.
    home, away = _split_team_names(remainder)

    # Goals total estimate appears AFTER the score in many rows:
    tail = text[m.end():].strip()
    m_g = re.search(r"(\d+\.\d+)\s*", tail)
    goals_avg = float(m_g.group(1)) if m_g else None

    return {
        "competition":    competition,
        "home_team":      home,
        "away_team":      away,
        "match_date":     date_str,
        "kickoff_time":   time_str,
        "forebet_pct_1":  home_pct,
        "forebet_pct_x":  draw_pct,
        "forebet_pct_2":  away_pct,
        "pick_1x2":       pick_1x2,
        "predicted_score": score_pred,
        "goals_avg":      goals_avg,
        "match_url":      match_url,
    }


def _split_team_names(remainder: str) -> tuple[str, str]:
    """Heuristic team-name splitter for Forebet rows.

    Forebet renders ``<HomeTeam><AwayTeam>`` without a separator. We try
    multiple strategies in order:
      1. Split at any uppercase letter that follows a lowercase letter
         (``South KoreaCzech Republic`` → ``South Korea`` + ``Czech Republic``).
      2. Split at the first uppercase letter that follows a space when
         the remainder has spaces (``South Korea Czech Republic`` →
         3-token home + 2-token away ?).  We pick the split that yields
         the most balanced word counts.
      3. Fallback: split at the middle.
    """
    s = remainder.strip()
    if not s:
        return ("", "")

    # 1) lower→upper boundary.
    m = re.search(r"([a-záéíóúñ])([A-ZÁÉÍÓÚÑ])", s)
    if m:
        idx = m.start() + 1
        return (s[:idx].strip(), s[idx:].strip())

    # 2) balanced word split.
    words = s.split()
    if len(words) >= 2:
        # Prefer the middle.
        mid = len(words) // 2
        return (" ".join(words[:mid]).strip(), " ".join(words[mid:]).strip())

    # 3) fallback: half-string.
    half = len(s) // 2
    return (s[:half].strip(), s[half:].strip())


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def parse_forebet_fixtures_page(html: str) -> dict:
    """Parse Forebet's fixtures / predictions index page.

    Returns a dict with:
        available: bool,
        fixtures: [ {...}, ... ],
        reason_codes: [...]
    """
    if not isinstance(html, str) or len(html) < 1000:
        return {"available": False, "reason_codes": ["FOREBET_EMPTY_HTML"]}
    try:
        tree = HTMLParser(html)
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_FOREBET_PARSE] HTML parse failed: %s", exc)
        return {"available": False, "reason_codes": ["FOREBET_PARSE_FAIL"]}

    rows = tree.css("div.rcnt")
    out: list[dict] = []
    for r in rows:
        fixt = _parse_rcnt_row(r)
        if fixt:
            out.append(fixt)
    return {
        "available":    True,
        "source":       "forebet",
        "fixtures":     out,
        "reason_codes": (["FOREBET_PARSED",
                          f"FOREBET_FIXTURES_FOUND_{len(out)}"]
                          if out else
                          ["FOREBET_PARSED", "FOREBET_NO_FIXTURES_FOUND"]),
    }


def find_fixture(forebet_payload: dict,
                  home_query: str, away_query: str) -> Optional[dict]:
    """Find a fixture by team-name fuzzy match.

    The home/away inputs are normalised (lowercased, accent-stripped)
    and we look for a fixture whose team names contain those tokens.
    Returns the matched fixture dict or None.
    """
    if not isinstance(forebet_payload, dict) or not forebet_payload.get("available"):
        return None
    import re as _re
    import unicodedata
    def _norm(s: str) -> str:
        if not s:
            return ""
        n = unicodedata.normalize("NFD", s)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn").lower()
        # Collapse any non-alphanumeric run to a single space so
        # "bosnia y herzegovina" and "bosnia-herzegovina" normalise to
        # the same token bag.
        n = _re.sub(r"[^a-z0-9]+", " ", n)
        return _re.sub(r"\s+", " ", n).strip()

    def _token_overlap(a: str, b: str) -> bool:
        """Return True when ``a`` and ``b`` share enough tokens to be
        considered the same team. Removes short connector tokens (y, and,
        de, of, the) before comparing."""
        if not a or not b:
            return False
        STOP = {"y", "and", "de", "del", "of", "the", "el", "la"}
        ta = {t for t in a.split() if t not in STOP and len(t) > 1}
        tb = {t for t in b.split() if t not in STOP and len(t) > 1}
        if not ta or not tb:
            return False
        # At least one significant token must match.
        return len(ta & tb) >= max(1, min(len(ta), len(tb)) - 1)

    hq, aq = _norm(home_query), _norm(away_query)
    for fx in forebet_payload.get("fixtures") or []:
        fh = _norm(fx.get("home_team") or "")
        fa = _norm(fx.get("away_team") or "")
        if hq and aq:
            if _token_overlap(hq, fh) and _token_overlap(aq, fa):
                return fx
        # Allow swapped order in case Forebet lists it differently.
        if hq and aq:
            if _token_overlap(hq, fa) and _token_overlap(aq, fh):
                return {**fx, "_orientation": "swapped"}
    return None


__all__ = ["parse_forebet_fixtures_page", "find_fixture"]
