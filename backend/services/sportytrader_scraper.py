"""Phase F70 — Sportytrader parser & scraper.

Sportytrader is a "free betting tips" site. Each match has a page like:
    https://www.sportytrader.com/es/pronosticos/<home>-<away>-<numeric_id>/

Bright Data blocks them (policy: gambling). We use scrape.do instead.

What we extract (mirrors the screenshots provided by the user):
  * H1 title → ``home_team_label`` & ``away_team_label`` & ``competition``.
  * "¿Cuál es el pronóstico…" article block (Spanish editorial summary).
  * **Final prediction tag** (e.g. "¡Primer Gol Antes del Minuto 30 – No!").
  * **Últimos resultados** per team — list of recent matches with
    date, competition, home, away, home_score, away_score.
  * **Estadísticas promedio** per team:
    total_goals_avg, btts_pct, goals_scored_avg, goals_conceded_avg,
    over_2_5_pct, under_2_5_pct.

All extraction is fail-soft: missing fields → None / empty list; never
raises.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

from selectolax.parser import HTMLParser

log = logging.getLogger("sportytrader_parser")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _txt(node) -> str:
    """Trim + collapse internal whitespace."""
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(strip=True)).strip()


def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def _parse_h1_title(h1_text: str) -> dict:
    """``Pronóstico Canadá - Bosnia Y Herzegovina - Mundial`` →
       {home: ..., away: ..., competition: ...}."""
    out = {"home_team": None, "away_team": None, "competition": None}
    if not isinstance(h1_text, str) or not h1_text.strip():
        return out
    s = re.sub(r"^Pron(o|ó)stico\s+", "", h1_text.strip(), flags=re.IGNORECASE)
    parts = [p.strip() for p in s.split(" - ") if p.strip()]
    if len(parts) >= 2:
        out["home_team"] = parts[0]
        out["away_team"] = parts[1]
    if len(parts) >= 3:
        out["competition"] = parts[2]
    return out


_DATE_TOKEN_RE = re.compile(r"(\d{1,2})\s+(\w{3,12})\s+(\d{4})", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────
# Section parsers
# ─────────────────────────────────────────────────────────────────────
def _parse_recent_results(tree: HTMLParser) -> list[dict]:
    """Each recent-match card has:
        <div class="bg-gray-100 m-2 rounded-lg ...">
          <p class="text-center text-xs text-gray-600 ...">
              5 jun 2026 - Amistosos
          </p>
          <div class="w-full flex pt-box">
              <div class="w-5/12 ... text-right">Canadá</div>
              <div class="w-2/12 ...">
                  <span>1</span> : <span>1</span>
              </div>
              <div class="w-5/12 ... pl-3">Irlanda</div>
          </div>
        </div>

    The page contains two columns (home team history, away team
    history). We return a flat list; each entry carries a ``team_focus``
    hint when we can infer from layout order, but the consumer should
    rely on home/away names directly.
    """
    cards: list[dict] = []
    for card in tree.css("div.bg-gray-100"):
        # The date+competition header
        header = card.css_first("p.text-center")
        if not header:
            continue
        header_txt = _txt(header)
        # Look for "5 jun 2026 - Amistosos"
        m = re.match(r"(\d{1,2}\s+\w{3,12}\s+\d{4})\s*-\s*(.+)",
                     header_txt)
        if not m:
            continue
        date_str = m.group(1).strip()
        competition = m.group(2).strip()

        # The match body has a row of 3 cells (home name | score | away name).
        # We target the score block by its distinctive ``text-lg`` class.
        score_block_node = card.css_first("div.text-lg")
        home_name_node = card.css_first("div.text-right")
        # The away cell has "justify-start" + "pl-3"; selectolax pseudo
        # selectors are limited, so we resort to scanning all break-words
        # divs and excluding the home (text-right) one.
        away_name_node = None
        for d in card.css("div.break-words"):
            cls = d.attributes.get("class") or ""
            if "text-right" not in cls and "pr-3" not in cls and "justify-end" not in cls:
                away_name_node = d
                break

        if not (score_block_node and home_name_node and away_name_node):
            continue
        home_name = _txt(home_name_node)
        away_name = _txt(away_name_node)
        score_block = _txt(score_block_node)

        m_s = re.search(r"(\d+)\s*[:\-]\s*(\d+)", score_block)
        home_score = int(m_s.group(1)) if m_s else None
        away_score = int(m_s.group(2)) if m_s else None

        if not home_name or not away_name:
            continue
        # Reject obvious non-team values.
        if re.fullmatch(r"\d+\s*[:\-]\s*\d+", away_name):
            continue
        cards.append({
            "date":         date_str,
            "competition":  competition,
            "home_team":    home_name,
            "away_team":    away_name,
            "home_score":   home_score,
            "away_score":   away_score,
        })
    return cards


def _parse_team_stats_blocks(tree: HTMLParser) -> list[dict]:
    """The page renders TWO stat blocks (home team + away team), each
    laid out as a sequence of value→label pairs:

      1.83  Total de goles
      33%   Ambos marcan
      1.33  Goles marcados
      0.5   Goles recibidos
      17%   Over 2.5
      83%   Under 2.5

    Concatenated text reads:
      "1.83Total de goles33%Ambos marcan1.33Goles marcados..."

    We scan the page text for that pattern and emit two stat dicts. We
    also look for the "V V E E V E" recent-form streak that precedes
    the stats and the "3/6 (50%)" wins/draws/losses summary.
    """
    out: list[dict] = []
    # Find every "Estadísticas promedio" section
    all_text_nodes = tree.css("body *")
    # Easier: regex the raw HTML rendered as text
    rendered = re.sub(r"\s+", " ", tree.body.text(separator=" ", strip=False)
                                        if tree.body else "")
    # Pattern: <number>Total de goles<pct>Ambos marcan<number>Goles marcados<number>Goles recibidos<pct>Over 2.5<pct>Under 2.5
    pat = re.compile(
        r"([\d.,]+)\s*Total de goles\s*"
        r"(\d+)\s*%?\s*Ambos marcan\s*"
        r"([\d.,]+)\s*Goles marcados\s*"
        r"([\d.,]+)\s*Goles recibidos\s*"
        r"(\d+)\s*%?\s*Over 2\.5\s*"
        r"(\d+)\s*%?\s*Under 2\.5"
    )
    for m in pat.finditer(rendered):
        out.append({
            "total_goals_avg":      _safe_float(m.group(1)),
            "btts_pct":             _safe_int(m.group(2)),
            "goals_scored_avg":     _safe_float(m.group(3)),
            "goals_conceded_avg":   _safe_float(m.group(4)),
            "over_2_5_pct":         _safe_int(m.group(5)),
            "under_2_5_pct":        _safe_int(m.group(6)),
        })
    # The page may also include "Victorias 3/6 (50%) Empates 3/6 (50%) Derrotas 0/6 (0%)"
    streak_pat = re.compile(
        r"Victorias\s*(\d+)/(\d+)\s*\((\d+)%?\)\s*"
        r"Empates\s*(\d+)/(\d+)\s*\((\d+)%?\)\s*"
        r"Derrotas\s*(\d+)/(\d+)\s*\((\d+)%?\)"
    )
    streaks = []
    for sm in streak_pat.finditer(rendered):
        streaks.append({
            "wins":         _safe_int(sm.group(1)),
            "wins_total":   _safe_int(sm.group(2)),
            "wins_pct":     _safe_int(sm.group(3)),
            "draws":        _safe_int(sm.group(4)),
            "draws_pct":    _safe_int(sm.group(6)),
            "losses":       _safe_int(sm.group(7)),
            "losses_pct":   _safe_int(sm.group(9)),
        })
    # Merge stat blocks with streak blocks position-by-position.
    merged: list[dict] = []
    for i, s in enumerate(out):
        st = streaks[i] if i < len(streaks) else {}
        merged.append({**s, "streak": st})
    return merged


def _parse_prediction_article(tree: HTMLParser) -> dict:
    """Extract the editorial text + the final prediction tag.

    The page exposes:
      <h2>¿Cuál es el pronóstico de SportyTrader para el partido…?</h2>
      <p>… El pronóstico para el partido entre X y Y es:
         <span class="font-sem…">¡<TAG>!</span></p>

    We also pick up the first 1-2 paragraphs of editorial commentary
    inside ``div.prose``.
    """
    out: dict[str, Any] = {
        "final_prediction": None,
        "editorial_paragraphs": [],
        "article_url": None,
    }
    proses = tree.css("div.prose")
    seen_paragraphs: list[str] = []
    for div in proses:
        txt = _txt(div)
        if not txt or len(txt) < 60:
            continue
        if txt not in seen_paragraphs:
            seen_paragraphs.append(txt)
    out["editorial_paragraphs"] = seen_paragraphs[:5]

    # Final prediction sentence — search the raw rendered text.
    rendered = " ".join(seen_paragraphs)
    m = re.search(
        r"El pron[oó]stico para el partido entre [^:]+:\s*¡?([^!.\n]{3,80})[!.]",
        rendered,
    )
    if m:
        out["final_prediction"] = m.group(1).strip()

    # Canonical URL (og:url)
    og = tree.css_first("meta[property='og:url']")
    if og:
        out["article_url"] = og.attributes.get("content") or None
    return out


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "."))
    except Exception:  # noqa: BLE001
        return None


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(str(v).replace(",", ".")))
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def parse_sportytrader_match_page(html: str) -> dict:
    """Parse the full match page. Returns:
        {
          available: bool,
          home_team, away_team, competition,
          recent_results: [...],
          team_stats: [home_stats, away_stats],  # may be 0-2 entries
          prediction: {final_prediction, editorial_paragraphs, article_url},
          reason_codes: [...],
        }
    """
    if not isinstance(html, str) or len(html) < 1000:
        return {"available": False, "reason_codes": ["SPORTYTRADER_EMPTY_HTML"]}
    try:
        tree = HTMLParser(html)
    except Exception as exc:  # noqa: BLE001
        log.warning("[F70_SPORTY_PARSE] HTML parse failed: %s", exc)
        return {"available": False, "reason_codes": ["SPORTYTRADER_PARSE_FAIL"]}

    h1 = _txt(tree.css_first("h1"))
    teams = _parse_h1_title(h1)
    if not teams["home_team"] or not teams["away_team"]:
        return {
            "available":   False,
            "reason_codes": ["SPORTYTRADER_TITLE_NOT_FOUND"],
        }

    recent     = _parse_recent_results(tree)
    # Deduplicate by (date + home + away).
    seen_keys: set[tuple] = set()
    deduped_recent: list[dict] = []
    for r in recent:
        k = (r.get("date"), r.get("home_team"), r.get("away_team"))
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped_recent.append(r)
    recent = deduped_recent
    team_stats = _parse_team_stats_blocks(tree)
    # The page may render extra stat blocks for "Local" / "Visitante"
    # views beyond the first home/away tab. We only need 2 (home, away).
    team_stats = team_stats[:2]
    prediction = _parse_prediction_article(tree)

    reason_codes = ["SPORTYTRADER_PARSED"]
    if recent:
        reason_codes.append("SPORTYTRADER_RECENT_RESULTS_FOUND")
    if team_stats:
        reason_codes.append("SPORTYTRADER_TEAM_STATS_FOUND")
    if prediction.get("final_prediction"):
        reason_codes.append("SPORTYTRADER_FINAL_PREDICTION_FOUND")
    if prediction.get("editorial_paragraphs"):
        reason_codes.append("SPORTYTRADER_EDITORIAL_PARAGRAPHS_FOUND")

    return {
        "available":       True,
        "source":          "sportytrader",
        "h1":              h1,
        "home_team":       teams["home_team"],
        "away_team":       teams["away_team"],
        "competition":     teams["competition"],
        "recent_results":  recent,
        "team_stats":      team_stats,
        "prediction":      prediction,
        "reason_codes":    reason_codes,
    }


__all__ = ["parse_sportytrader_match_page"]
