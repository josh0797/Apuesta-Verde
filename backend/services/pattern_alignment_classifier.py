"""
MLB Pattern Alignment Classifier
================================

Single-source classifier that determines whether each Spanish-language
"detected pattern" phrase coming from the historical profile actually
SUPPORTS, OPPOSES or is NEUTRAL with respect to the final recommended
market for an MLB pick.

Why this exists
---------------
The frontend used to render every `combined.trendSummary` phrase under a
single bullet list titled "Patrones detectados". This was confusing for
the user: a pick recommending `Total Runs Over 9.5` could still show
patterns like "El bullpen visitante llega cargado" or "Abridor local con
ERA elite (2.85)" with no visual cue that those phrases actually
contradict the Over thesis (they support an UNDER).

This module classifies each pattern into one of three buckets:

    SUPPORTS  → pattern argues IN FAVOR of the recommended market
    OPPOSES   → pattern argues AGAINST the recommended market
    NEUTRAL   → pattern is informational / does not lean either way

Design constraints
------------------
- **Pure function**: no I/O, no DB calls, deterministic.
- **Spanish-aware**: the trend phrases are emitted in Spanish (see
  `baseball_historical._build_trend_phrases`); the regex catalog reads
  Spanish keywords directly.
- **Fail-soft**: any unparseable phrase falls back to NEUTRAL with a
  diagnostic reason so the engine never crashes a pick.
- **Backward compatible**: callers still receive the raw `trendSummary`
  flat list; the alignment payload is attached alongside it.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Polarity model ─────────────────────────────────────────────────────────
#
# We map a recommended market label to a *scoring polarity* that captures
# what kind of run-total environment the pick needs in order to cash:
#
#   OVER_LIKE   →  needs high scoring (Total Runs Over, F5 Over, YRFI, …)
#   UNDER_LIKE  →  needs low scoring  (Total Runs Under, F5 Under, NRFI, …)
#   FAVORITE_BLOWOUT  → Run Line -1.5 / Spread on the favorite
#   UNDERDOG_COVER    → Run Line +1.5 / Spread on the underdog
#   ML / OTHER  →  not a scoring market — most scoring patterns become NEUTRAL
#
# Each pattern phrase is then mapped to a *pattern direction*:
#
#   HIGH_SCORING  → suggests the total will be HIGH
#   LOW_SCORING   → suggests the total will be LOW
#   COMPETITIVE   → suggests a close, tight game
#   BLOWOUT       → suggests a one-sided game
#   INFO          → context with no directional lean (sample size, etc.)
#
# Resolution is a small 2D matrix.
# ───────────────────────────────────────────────────────────────────────────

MARKET_POLARITY = {
    "OVER_LIKE":         "OVER_LIKE",
    "UNDER_LIKE":        "UNDER_LIKE",
    "FAVORITE_BLOWOUT":  "FAVORITE_BLOWOUT",
    "UNDERDOG_COVER":    "UNDERDOG_COVER",
    "ML":                "ML",
    "OTHER":             "OTHER",
}


def _detect_market_polarity(recommended_market: Optional[str]) -> str:
    """Map a raw market label into one of the MARKET_POLARITY buckets."""
    if not recommended_market or not isinstance(recommended_market, str):
        return "OTHER"
    m = recommended_market.strip().lower()

    # No-Run-First-Inning is an Under-style micro market.
    if "nrfi" in m:
        return "UNDER_LIKE"
    if "yrfi" in m:
        return "OVER_LIKE"

    # Generic Under / Over keywords cover Total Runs, F5, Team Total Under,
    # Smart Totals Under, alternate lines, etc.
    if "under" in m:
        return "UNDER_LIKE"
    if "over" in m:
        return "OVER_LIKE"

    # Run-line / spreads — the sign of the handicap tells us which side
    # we're backing.
    if "run line" in m or "runline" in m or "spread" in m:
        # Look for an explicit +1.5 / -1.5 (or any signed handicap).
        sign_match = re.search(r"([+\-])\s*\d+(?:\.\d+)?", m)
        if sign_match:
            return "UNDERDOG_COVER" if sign_match.group(1) == "+" else "FAVORITE_BLOWOUT"
        # No sign in label → assume the favorite side (most common pick).
        return "FAVORITE_BLOWOUT"

    # Moneyline / Win
    if "moneyline" in m or m in ("ml", "win") or "gana" in m:
        return "ML"

    return "OTHER"


# ── Pattern direction regex catalog (Spanish) ──────────────────────────────
#
# Each entry is a compiled regex + the directional label it implies. Order
# matters only when multiple regexes could match the same phrase — we keep
# the catalog narrow and disjoint to avoid double-counting.
# ───────────────────────────────────────────────────────────────────────────

_PHRASE_RULES: list[tuple[re.Pattern[str], str]] = [
    # ── HIGH-SCORING signals ──
    (re.compile(r"anot[óo]\s+m[áa]s\s+de\s+\d", re.IGNORECASE),                    "HIGH_SCORING"),
    (re.compile(r"\blean\s+over\b",              re.IGNORECASE),                    "HIGH_SCORING"),
    (re.compile(r"por\s+encima\s+del?\s+promedio\s+de\s+la\s+liga", re.IGNORECASE), "HIGH_SCORING"),
    (re.compile(r"bullpen\b.*\bllega\s+cargado",  re.IGNORECASE),                   "HIGH_SCORING"),
    (re.compile(r"bullpen\b.*\bagotado",          re.IGNORECASE),                   "HIGH_SCORING"),
    (re.compile(r"m[áa]s\s+altos?\s+que\s+la\s+proyecci[óo]n", re.IGNORECASE),      "HIGH_SCORING"),
    (re.compile(r"ofensiva\b.*(?:caliente|en\s+racha|explosi[óo]n)", re.IGNORECASE), "HIGH_SCORING"),

    # ── LOW-SCORING signals ──
    (re.compile(r"no\s+super[óo]\s+\d",          re.IGNORECASE),                    "LOW_SCORING"),
    (re.compile(r"\blean\s+under\b",             re.IGNORECASE),                    "LOW_SCORING"),
    (re.compile(r"por\s+debajo\s+del?\s+promedio\s+de\s+la\s+liga", re.IGNORECASE), "LOW_SCORING"),
    (re.compile(r"abridor\b.*era\s+elite",       re.IGNORECASE),                    "LOW_SCORING"),
    (re.compile(r"m[áa]s\s+bajos?\s+que\s+la\s+proyecci[óo]n", re.IGNORECASE),      "LOW_SCORING"),
    (re.compile(r"ofensiva\b.*(?:fr[íi]a|apagada|en\s+sequ[íi]a)", re.IGNORECASE),   "LOW_SCORING"),

    # ── Competitive / blowout signals ──
    (re.compile(r"juegos?\s+(?:muy\s+)?cerrados?",        re.IGNORECASE),           "COMPETITIVE"),
    (re.compile(r"extra\s+innings",                       re.IGNORECASE),           "COMPETITIVE"),
    (re.compile(r"(?:paliza|aplast(?:[óo]|aron)|blowout)", re.IGNORECASE),          "BLOWOUT"),

    # ── Pure informational (sample size, low confidence). NEUTRAL. ──
    (re.compile(r"muestra\s+insuficiente",       re.IGNORECASE),                    "INFO"),
    (re.compile(r"usando\s+promedio\s+de\s+liga", re.IGNORECASE),                   "INFO"),
]


def _detect_pattern_direction(phrase: str) -> str:
    """Run the regex catalog over a Spanish pattern phrase."""
    if not phrase or not isinstance(phrase, str):
        return "INFO"
    for rx, label in _PHRASE_RULES:
        if rx.search(phrase):
            return label
    return "INFO"


# ── Resolution matrix ──────────────────────────────────────────────────────

_ALIGNMENT_MATRIX: dict[tuple[str, str], str] = {
    # Over-style markets need HIGH scoring.
    ("OVER_LIKE",         "HIGH_SCORING"): "SUPPORTS",
    ("OVER_LIKE",         "LOW_SCORING"):  "OPPOSES",
    ("OVER_LIKE",         "COMPETITIVE"):  "NEUTRAL",
    ("OVER_LIKE",         "BLOWOUT"):      "SUPPORTS",   # blowouts pump totals
    # Under-style markets need LOW scoring.
    ("UNDER_LIKE",        "HIGH_SCORING"): "OPPOSES",
    ("UNDER_LIKE",        "LOW_SCORING"):  "SUPPORTS",
    ("UNDER_LIKE",        "COMPETITIVE"):  "SUPPORTS",
    ("UNDER_LIKE",        "BLOWOUT"):      "OPPOSES",
    # Favorite spread (-1.5) — likes blowouts and confident offense.
    ("FAVORITE_BLOWOUT",  "HIGH_SCORING"): "NEUTRAL",    # could be both sides
    ("FAVORITE_BLOWOUT",  "LOW_SCORING"):  "OPPOSES",    # close games kill -1.5
    ("FAVORITE_BLOWOUT",  "COMPETITIVE"):  "OPPOSES",
    ("FAVORITE_BLOWOUT",  "BLOWOUT"):      "SUPPORTS",
    # Underdog spread (+1.5) — likes competitive / low-scoring games.
    ("UNDERDOG_COVER",    "HIGH_SCORING"): "OPPOSES",
    ("UNDERDOG_COVER",    "LOW_SCORING"):  "SUPPORTS",
    ("UNDERDOG_COVER",    "COMPETITIVE"):  "SUPPORTS",
    ("UNDERDOG_COVER",    "BLOWOUT"):      "OPPOSES",
}


# ── Public API ─────────────────────────────────────────────────────────────

def classify_pattern_alignment(
    pattern: str,
    recommended_market: Optional[str],
) -> dict:
    """Classify a single trend phrase against the recommended market.

    Returns a dict with:
      - ``alignment``         : "SUPPORTS" | "OPPOSES" | "NEUTRAL"
      - ``pattern``           : original phrase (echoed for the UI)
      - ``pattern_direction`` : one of HIGH_SCORING | LOW_SCORING | COMPETITIVE | BLOWOUT | INFO
      - ``market_polarity``   : one of OVER_LIKE | UNDER_LIKE | FAVORITE_BLOWOUT | UNDERDOG_COVER | ML | OTHER
      - ``reason``            : short Spanish explanation suitable for tooltips
    """
    direction = _detect_pattern_direction(pattern)
    polarity  = _detect_market_polarity(recommended_market)

    if direction == "INFO" or polarity in ("ML", "OTHER"):
        alignment = "NEUTRAL"
    else:
        alignment = _ALIGNMENT_MATRIX.get((polarity, direction), "NEUTRAL")

    return {
        "pattern":           pattern,
        "alignment":         alignment,
        "pattern_direction": direction,
        "market_polarity":   polarity,
        "reason":            _alignment_reason(alignment, direction, polarity, recommended_market),
    }


def classify_patterns_for_market(
    patterns: list[str],
    recommended_market: Optional[str],
) -> dict:
    """Batch helper that splits a list of patterns into the three buckets.

    Returns a dict ready to be attached to the frontend payload:

        {
          "recommendedMarket": "Total Runs Over 9.5",
          "marketPolarity":    "OVER_LIKE",
          "supports":          [ {pattern, reason, ...}, ... ],
          "opposes":           [ {pattern, reason, ...}, ... ],
          "neutral":           [ {pattern, reason, ...}, ... ],
          "summary":           "3 a favor · 1 en contra · 2 informativos",
          "consistency":       "STRONG" | "MIXED" | "CONFLICTED" | "INFO_ONLY",
        }
    """
    patterns = [p for p in (patterns or []) if isinstance(p, str) and p.strip()]
    polarity = _detect_market_polarity(recommended_market)
    supports, opposes, neutral = [], [], []
    for p in patterns:
        row = classify_pattern_alignment(p, recommended_market)
        if row["alignment"] == "SUPPORTS":
            supports.append(row)
        elif row["alignment"] == "OPPOSES":
            opposes.append(row)
        else:
            neutral.append(row)

    n_sup, n_opp, n_neu = len(supports), len(opposes), len(neutral)
    if n_sup == 0 and n_opp == 0:
        consistency = "INFO_ONLY"
    elif n_opp == 0 and n_sup >= 2:
        consistency = "STRONG"
    elif n_sup == 0 and n_opp >= 2:
        # All "patterns" actively contradict the pick — caller may want to
        # surface this as a warning ribbon. We still keep the engine's
        # recommendation; downstream guardrails handle the final veto.
        consistency = "CONFLICTED"
    elif n_opp > n_sup:
        consistency = "CONFLICTED"
    else:
        consistency = "MIXED"

    summary_parts: list[str] = []
    if n_sup:
        summary_parts.append(f"{n_sup} a favor")
    if n_opp:
        summary_parts.append(f"{n_opp} en contra")
    if n_neu:
        summary_parts.append(f"{n_neu} informativos")
    summary = " · ".join(summary_parts) if summary_parts else "Sin patrones detectados"

    return {
        "recommendedMarket": recommended_market,
        "marketPolarity":    polarity,
        "supports":          supports,
        "opposes":           opposes,
        "neutral":           neutral,
        "counts":            {"supports": n_sup, "opposes": n_opp, "neutral": n_neu},
        "summary":           summary,
        "consistency":       consistency,
    }


# ── Internal helpers ───────────────────────────────────────────────────────

def _alignment_reason(
    alignment: str,
    direction: str,
    polarity:  str,
    market:    Optional[str],
) -> str:
    """Short Spanish phrase used as a tooltip in the UI."""
    m = market or "el mercado recomendado"
    if alignment == "SUPPORTS":
        if direction == "HIGH_SCORING":
            return f"Apoya {m}: el patrón sugiere un total alto."
        if direction == "LOW_SCORING":
            return f"Apoya {m}: el patrón sugiere un total bajo."
        if direction == "COMPETITIVE":
            return f"Apoya {m}: el patrón sugiere un juego cerrado."
        if direction == "BLOWOUT":
            return f"Apoya {m}: el patrón sugiere una paliza."
    if alignment == "OPPOSES":
        if direction == "HIGH_SCORING":
            return f"Contradice {m}: el patrón sugiere total alto."
        if direction == "LOW_SCORING":
            return f"Contradice {m}: el patrón sugiere total bajo."
        if direction == "COMPETITIVE":
            return f"Contradice {m}: el patrón sugiere juego cerrado."
        if direction == "BLOWOUT":
            return f"Contradice {m}: el patrón sugiere paliza."
    # NEUTRAL
    if direction == "INFO":
        return "Patrón informativo (muestra/contexto)."
    if polarity in ("ML", "OTHER"):
        return "Patrón no aplica directamente a este mercado."
    return "Patrón sin lean claro respecto al mercado."
