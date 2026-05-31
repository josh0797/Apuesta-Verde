"""
Basketball Pattern Alignment Classifier
=======================================

Espejo del clasificador MLB (`pattern_alignment_classifier.py`) — clasifica
cada frase en español producida por `basketball_historical._build_trend_phrases`
como `SUPPORTS`, `OPPOSES` o `NEUTRAL` respecto al mercado final
recomendado para el pick de básquet (Total Points Over/Under, Spread
±X.5, Moneyline).

Diseño
------
- **Función pura**: sin I/O, determinística, fail-soft.
- **Spanish-aware**: las frases vienen en español del módulo histórico
  (`El equipo local superó X puntos…`, `La defensa visitante permitió…`,
  `Los enfrentamientos directos recientes promediaron…`).
- **Single source of truth**: la matriz `_ALIGNMENT_MATRIX` mantiene la
  decisión en un solo sitio para auditoría futura.

Compatibilidad MLB
------------------
La forma de retorno es idéntica al clasificador MLB (mismos campos
`alignment`, `pattern_direction`, `market_polarity`, `reason`, `summary`,
`consistency`) de modo que el frontend reutilice el mismo
`PatternAlignmentSection` sin código adicional.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Polarity model ─────────────────────────────────────────────────────────
#
# Recommended market → scoring polarity:
#   OVER_LIKE          → Total Points Over X.5  → necesita partido alto
#   UNDER_LIKE         → Total Points Under X.5 → necesita partido bajo
#   FAVORITE_BLOWOUT   → Spread -X.5 / -X       → necesita victoria amplia
#                        del favorito
#   UNDERDOG_COVER     → Spread +X.5            → necesita partido cerrado
#   ML / OTHER         → moneyline / otros      → la mayoría de patrones
#                                                  scoring → NEUTRAL
# ───────────────────────────────────────────────────────────────────────────

def _detect_market_polarity(recommended_market: Optional[str]) -> str:
    """Mapea una etiqueta de mercado de básquet a una polaridad."""
    if not recommended_market or not isinstance(recommended_market, str):
        return "OTHER"
    m = recommended_market.strip().lower()

    # Totals
    if "under" in m:
        return "UNDER_LIKE"
    if "over" in m:
        return "OVER_LIKE"

    # Spread / Hándicap / Línea de victoria.
    if ("spread" in m or "handicap" in m or "h\u00e1ndicap" in m
            or "puntos line" in m or "point spread" in m):
        sign_match = re.search(r"([+\-])\s*\d+(?:\.\d+)?", m)
        if sign_match:
            return "UNDERDOG_COVER" if sign_match.group(1) == "+" else "FAVORITE_BLOWOUT"
        return "FAVORITE_BLOWOUT"  # default ⇒ favorite side cubre

    # Moneyline / Win.
    if "moneyline" in m or m in ("ml", "win") or "gana" in m:
        return "ML"

    return "OTHER"


# ── Pattern direction regex catalog (Spanish) ──────────────────────────────
#
# Cada frase básquet del histórico aporta una direccionalidad scoring.
# Las regex se mantienen estrechas para evitar dobles matches.
# ───────────────────────────────────────────────────────────────────────────

_PHRASE_RULES: list[tuple[re.Pattern[str], str]] = [
    # ── HIGH-SCORING signals ──
    (re.compile(r"super[óo]\s+\d+(?:\.\d+)?\s+puntos", re.IGNORECASE),                 "HIGH_SCORING"),
    (re.compile(r"permiti[óo].*?\d{3}(?:\.\d+)?\s*puntos", re.IGNORECASE),             "HIGH_SCORING"),
    (re.compile(r"defensa\b.*\bpermiti", re.IGNORECASE),                                "HIGH_SCORING"),
    (re.compile(r"pace\s+(?:alto|elevado|por\s+encima)", re.IGNORECASE),                "HIGH_SCORING"),
    (re.compile(r"three[-_\s]?point|triples?\b.*caliente|3pt\s+racha", re.IGNORECASE),  "HIGH_SCORING"),
    (re.compile(r"m[áa]s\s+altos?\s+que\s+la\s+proyecci[óo]n", re.IGNORECASE),          "HIGH_SCORING"),
    (re.compile(r"por\s+encima\s+del?\s+promedio\s+de\s+la\s+liga", re.IGNORECASE),     "HIGH_SCORING"),
    (re.compile(r"ofensiva\b.*(?:caliente|en\s+racha|explosi[óo]n)", re.IGNORECASE),    "HIGH_SCORING"),

    # ── LOW-SCORING signals ──
    (re.compile(r"no\s+lleg[óo]\s+a\s+\d+(?:\.\d+)?\s+puntos", re.IGNORECASE),          "LOW_SCORING"),
    (re.compile(r"defensa\s+(?:s[óo]lida|el[ií]te|f[ée]rrea)", re.IGNORECASE),          "LOW_SCORING"),
    (re.compile(r"pace\s+(?:bajo|lento|por\s+debajo)", re.IGNORECASE),                  "LOW_SCORING"),
    (re.compile(r"m[áa]s\s+bajos?\s+que\s+la\s+proyecci[óo]n", re.IGNORECASE),          "LOW_SCORING"),
    (re.compile(r"por\s+debajo\s+del?\s+promedio\s+de\s+la\s+liga", re.IGNORECASE),     "LOW_SCORING"),
    (re.compile(r"ofensiva\b.*(?:fr[íi]a|apagada|en\s+sequ[íi]a)", re.IGNORECASE),       "LOW_SCORING"),

    # ── Fatigue / Back-to-back → ligeramente UNDER (legs cansadas, tiros más fríos). ──
    (re.compile(r"back[-_\s]?to[-_\s]?back", re.IGNORECASE),                            "LOW_SCORING"),
    (re.compile(r"\d+\s+b2b\b", re.IGNORECASE),                                          "LOW_SCORING"),

    # ── Competitive / Blowout H2H ──
    (re.compile(r"(?:juegos?\s+(?:muy\s+)?cerrados?|overtime|tiempo\s+extra)", re.IGNORECASE), "COMPETITIVE"),
    (re.compile(r"(?:paliza|aplast(?:[óo]|aron)|blowout|diferencia\s+amplia)", re.IGNORECASE), "BLOWOUT"),

    # ── Pure informational. NEUTRAL. ──
    (re.compile(r"muestra\s+insuficiente",       re.IGNORECASE),                        "INFO"),
    (re.compile(r"usando\s+promedio\s+de\s+liga", re.IGNORECASE),                       "INFO"),
]


def _detect_pattern_direction(phrase: str) -> str:
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
    ("OVER_LIKE",         "BLOWOUT"):      "NEUTRAL",     # blowouts en básquet
                                                          # pueden disparar OT
                                                          # o garbage time — ambiguo
    # Under-style markets need LOW scoring.
    ("UNDER_LIKE",        "HIGH_SCORING"): "OPPOSES",
    ("UNDER_LIKE",        "LOW_SCORING"):  "SUPPORTS",
    ("UNDER_LIKE",        "COMPETITIVE"):  "OPPOSES",     # juegos cerrados →
                                                          # más posesiones extra
    ("UNDER_LIKE",        "BLOWOUT"):      "NEUTRAL",
    # Favorite spread (-X.5) — necesita partido controlado por el favorito.
    ("FAVORITE_BLOWOUT",  "HIGH_SCORING"): "NEUTRAL",
    ("FAVORITE_BLOWOUT",  "LOW_SCORING"):  "OPPOSES",
    ("FAVORITE_BLOWOUT",  "COMPETITIVE"):  "OPPOSES",
    ("FAVORITE_BLOWOUT",  "BLOWOUT"):      "SUPPORTS",
    # Underdog spread (+X.5) — likes competitive games.
    ("UNDERDOG_COVER",    "HIGH_SCORING"): "NEUTRAL",
    ("UNDERDOG_COVER",    "LOW_SCORING"):  "SUPPORTS",
    ("UNDERDOG_COVER",    "COMPETITIVE"):  "SUPPORTS",
    ("UNDERDOG_COVER",    "BLOWOUT"):      "OPPOSES",
}


# ── Public API ─────────────────────────────────────────────────────────────

def classify_pattern_alignment(
    pattern: str,
    recommended_market: Optional[str],
) -> dict:
    """Clasifica una sola frase del histórico contra el mercado del pick.

    Returns un dict con `pattern`, `alignment`, `pattern_direction`,
    `market_polarity`, `reason` (espejo de la API MLB).
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


def classify_patterns_for_market_bball(
    patterns: list[str],
    recommended_market: Optional[str],
) -> dict:
    """Splits a list of basketball patterns into the 3 buckets (mismo shape
    que MLB `classify_patterns_for_market`)."""
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
        "_engine":           "basketball",
    }


# ── Internal helpers ───────────────────────────────────────────────────────

def _alignment_reason(
    alignment: str,
    direction: str,
    polarity:  str,
    market:    Optional[str],
) -> str:
    m = market or "el mercado recomendado"
    if alignment == "SUPPORTS":
        if direction == "HIGH_SCORING":
            return f"Apoya {m}: el patrón sugiere partido alto."
        if direction == "LOW_SCORING":
            return f"Apoya {m}: el patrón sugiere partido bajo."
        if direction == "COMPETITIVE":
            return f"Apoya {m}: el patrón sugiere juego cerrado."
        if direction == "BLOWOUT":
            return f"Apoya {m}: el patrón sugiere paliza del favorito."
    if alignment == "OPPOSES":
        if direction == "HIGH_SCORING":
            return f"Contradice {m}: el patrón sugiere partido alto."
        if direction == "LOW_SCORING":
            return f"Contradice {m}: el patrón sugiere partido bajo."
        if direction == "COMPETITIVE":
            return f"Contradice {m}: el patrón sugiere juego cerrado."
        if direction == "BLOWOUT":
            return f"Contradice {m}: el patrón sugiere paliza."
    if direction == "INFO":
        return "Patrón informativo (muestra/contexto)."
    if polarity in ("ML", "OTHER"):
        return "Patrón no aplica directamente a este mercado."
    return "Patrón sin lean claro respecto al mercado."
