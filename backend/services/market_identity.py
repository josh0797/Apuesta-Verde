"""Phase F71 — Market Identity normaliser.

Every odds price / cuota / pick in the system MUST carry the canonical
market it belongs to so that:

  * The UI never renders "Cuota 1.22" without context (which market?).
  * External validators (OddsPortal, etc.) compare like-vs-like.
  * Anti-duplicate / reconciliation logic can group picks safely.

The normaliser returns a compact ``identity_key`` plus a structured
``parts`` dict for displays. Examples::

    >>> normalize_market_identity({"market": "1x2", "side": "home"})
    {"identity_key": "1X2:HOME", "family": "1X2", ...}
    >>> normalize_market_identity({"market": "Over/Under", "side": "OVER", "line": 2.5})
    {"identity_key": "TOTAL_GOALS:OVER:2.5", "family": "TOTAL_GOALS", ...}
    >>> normalize_market_identity({"market": "Doble oportunidad", "side": "1X"})
    {"identity_key": "DOUBLE_CHANCE:1X", "family": "DOUBLE_CHANCE", ...}

The function is intentionally permissive: it tries hard to infer the
family / side / line from sloppy upstream strings before giving up. On
total failure returns ``identity_key="UNKNOWN:RAW:<raw>"`` so the trail
is still auditable.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────
# Family resolution
# ─────────────────────────────────────────────────────────────────────
_FAMILY_PATTERNS = [
    # 1X2 / Moneyline
    ("1X2",             r"\b(1[\s_\-]?x[\s_\-]?2|moneyline|three[\s_\-]?way|h2h|head[\s_\-]?to[\s_\-]?head|ganador)\b"),
    ("DOUBLE_CHANCE",   r"\b(doble\s*oportunidad|double[\s_\-]?chance|dc)\b"),
    ("DNB",             r"\b(draw[\s_\-]?no[\s_\-]?bet|empate[\s_\-]?devuelve|dnb|sin\s*empate)\b"),
    ("BTTS",            r"\b(btts|ambos\s*marcan|both[\s_\-]?teams[\s_\-]?to[\s_\-]?score|ambos[\s_\-]?anotan)\b"),
    ("TOTAL_CORNERS",   r"\b(corners?|c[oó]rners?|saques?\s*de\s*esquina)\b"),
    ("TOTAL_CARDS",     r"\b(cards?|tarjetas?|amarillas?|yellows?|rojas?|reds?)\b"),
    ("TOTAL_GOALS",     r"(over[\s_/\-]?under|\bo[\s_/\-]?u\b|total(?:es)?[\s_\-]?(?:de)?[\s_\-]?goles?|m[áa]s\s*de|menos\s*de|\bover\b|\bunder\b)"),
    ("HANDICAP_ASIAN",  r"\b(asian[\s_\-]?handicap|hand?icap[\s_\-]?asi[áa]tico|ah[\s_\-]?\-?\d)\b"),
    ("HANDICAP",        r"\b(handicap|hand[íi]cap|spread|line)\b"),
    ("EXACT_SCORE",     r"\b(correct[\s_\-]?score|marcador[\s_\-]?exacto|exact[\s_\-]?score)\b"),
    ("HALF_FULL",       r"\b(half[\s_\-]?time[\s_\-]?full[\s_\-]?time|ht[\s_\-]?ft|descanso[\s_\-]?final)\b"),
    ("CLEAN_SHEET",     r"\b(clean[\s_\-]?sheet|porter[íi]a[\s_\-]?(?:a)?[\s_\-]?cero)\b"),
    ("PLAYER_SHOTS",    r"\b(shots?|tiros?|disparos?)\b"),
    ("PLAYER_SOT",      r"\b(sot|shots?[\s_\-]?on[\s_\-]?target|disparos?[\s_\-]?al[\s_\-]?arco|tiros?[\s_\-]?al[\s_\-]?arco)\b"),
    ("PLAYER_ASSISTS",  r"\b(assists?|asistencias?)\b"),
    ("PLAYER_TACKLES",  r"\b(tackles?|entradas?)\b"),
    ("PLAYER_PASSES",   r"\b(passes?|pases?)\b"),
    ("PLAYER_FOULS",    r"\b(fouls?|faltas?)\b"),
    ("PLAYER_TO_SCORE", r"\b(anytime[\s_\-]?(?:goal|scorer)|to[\s_\-]?score|marcar[áa])\b"),
]


def _strip(s: str) -> str:
    if not isinstance(s, str):
        return ""
    n = unicodedata.normalize("NFD", s)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.lower().strip()


def _resolve_family(market_str: str) -> Optional[str]:
    if not market_str:
        return None
    s = _strip(market_str)
    for fam, pat in _FAMILY_PATTERNS:
        if re.search(pat, s, flags=re.IGNORECASE):
            return fam
    return None


# ─────────────────────────────────────────────────────────────────────
# Side / selection resolution
# ─────────────────────────────────────────────────────────────────────
_HOME_TOKENS = (
    "home", "local", "1", "casa", "house",
)
_AWAY_TOKENS = (
    "away", "visitante", "2", "visita", "road",
)
_DRAW_TOKENS = (
    "draw", "empate", "x", "tie",
)


def _resolve_side(family: str, side_raw: Any, line: Optional[float],
                   *, home_name: Optional[str] = None,
                   away_name: Optional[str] = None) -> Optional[str]:
    if side_raw is None:
        return None
    raw = _strip(str(side_raw))
    if not raw:
        return None

    # Family-specific overrides.
    if family in ("TOTAL_GOALS", "TOTAL_CORNERS", "TOTAL_CARDS",
                  "PLAYER_SHOTS", "PLAYER_SOT", "PLAYER_ASSISTS",
                  "PLAYER_TACKLES", "PLAYER_PASSES", "PLAYER_FOULS"):
        if "over" in raw or "mas" in raw or "más" in raw or raw == "o":
            return "OVER"
        if "under" in raw or "menos" in raw or raw == "u":
            return "UNDER"

    if family == "DOUBLE_CHANCE":
        # Recognise 1X / X2 / 12 patterns
        compact = re.sub(r"[^0-9xX]", "", raw)
        if "1" in compact and "x" in compact.lower() and "2" not in compact:
            return "1X"
        if "2" in compact and "x" in compact.lower() and "1" not in compact:
            return "X2"
        if "1" in compact and "2" in compact:
            return "12"

    if family == "BTTS":
        if any(tok in raw for tok in ("yes", "si", "sí")):
            return "YES"
        if any(tok in raw for tok in ("no", "non")):
            return "NO"

    # Generic home/draw/away
    if any(tok == raw or tok in raw.split() for tok in _HOME_TOKENS):
        return "HOME"
    if any(tok == raw or tok in raw.split() for tok in _AWAY_TOKENS):
        return "AWAY"
    if any(tok == raw or tok in raw.split() for tok in _DRAW_TOKENS):
        return "DRAW"

    # Team-name based resolution.
    if home_name and _strip(home_name) in raw:
        return "HOME"
    if away_name and _strip(away_name) in raw:
        return "AWAY"

    return raw.upper().replace(" ", "_")


# ─────────────────────────────────────────────────────────────────────
# Line resolution
# ─────────────────────────────────────────────────────────────────────
def _resolve_line(family: str, raw: Any, market_str: str = "") -> Optional[float]:
    """Lines apply to OVER/UNDER families and handicaps."""
    if family not in ("TOTAL_GOALS", "TOTAL_CORNERS", "TOTAL_CARDS",
                       "PLAYER_SHOTS", "PLAYER_SOT", "PLAYER_ASSISTS",
                       "PLAYER_TACKLES", "PLAYER_PASSES", "PLAYER_FOULS",
                       "HANDICAP", "HANDICAP_ASIAN"):
        return None
    if raw is None:
        # Try to scrape the line from the market string ("Over 2.5", "AH -1.0").
        if market_str:
            m = re.search(r"([+\-]?\d+(?:\.\d+)?)", market_str)
            if m:
                try:
                    return float(m.group(1))
                except Exception:  # noqa: BLE001
                    return None
        return None
    try:
        return float(raw)
    except Exception:  # noqa: BLE001
        try:
            m = re.search(r"([+\-]?\d+(?:\.\d+)?)", str(raw))
            if m:
                return float(m.group(1))
        except Exception:  # noqa: BLE001
            return None
        return None


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def normalize_market_identity(market: Any,
                                *, home_name: Optional[str] = None,
                                away_name: Optional[str] = None) -> dict:
    """Compute the canonical market identity.

    Accepts either a dict with keys ``market``, ``side``, ``line``
    (and optional ``team_name``) OR a plain string like
    ``"Over 2.5 goles"``.

    Returns::

        {
          "identity_key": "TOTAL_GOALS:OVER:2.5",
          "family":       "TOTAL_GOALS",
          "side":         "OVER",
          "line":         2.5,
          "display":      "Over 2.5 goles",
          "raw":          {...},
          "reason_codes": ["MARKET_IDENTITY_RESOLVED"],
        }
    """
    if isinstance(market, str):
        raw = {"market": market, "side": None, "line": None}
    elif isinstance(market, dict):
        raw = {
            "market": market.get("market") or market.get("market_name")
                     or market.get("market_evaluated") or market.get("type"),
            "side":   market.get("side") or market.get("selection")
                     or market.get("pick") or market.get("outcome"),
            "line":   market.get("line") or market.get("handicap")
                     or market.get("total"),
        }
    else:
        raw = {"market": None, "side": None, "line": None}

    family = _resolve_family(raw.get("market") or "")
    # Phase F71 — if side is None but the market string contains "over"
    # or "under", lift it into side so downstream key-builders work.
    if raw.get("side") is None and isinstance(raw.get("market"), str):
        ms = _strip(raw["market"])
        if re.search(r"\bover\b|m[áa]s\s*de", ms):
            raw["side"] = "OVER"
        elif re.search(r"\bunder\b|menos\s*de", ms):
            raw["side"] = "UNDER"

    side = _resolve_side(family or "",
                          raw.get("side"),
                          raw.get("line"),
                          home_name=home_name, away_name=away_name)
    # Phase F71 — if side resolved to OVER/UNDER but family is empty,
    # we MUST infer a totals family (otherwise the identity_key drops
    # the line, breaking line-aware comparisons). Default to TOTAL_GOALS
    # unless the raw market hints at corners / cards.
    if family is None and side in ("OVER", "UNDER"):
        ms = _strip(raw.get("market") or "")
        if re.search(r"corner|c[oó]rner|esquina", ms):
            family = "TOTAL_CORNERS"
        elif re.search(r"card|tarjeta", ms):
            family = "TOTAL_CARDS"
        else:
            family = "TOTAL_GOALS"

    line = _resolve_line(family or "", raw.get("line"),
                          market_str=raw.get("market") or "")

    parts: list[str] = []
    if family:
        parts.append(family)
    if side:
        parts.append(side)
    if line is not None:
        parts.append(_format_line(line))

    if parts:
        identity_key = ":".join(parts)
        codes = ["MARKET_IDENTITY_RESOLVED"]
    else:
        identity_key = ("UNKNOWN:RAW:" + (_strip(raw.get("market") or "?")[:32]
                                            or "empty"))
        codes = ["MARKET_IDENTITY_UNRESOLVED"]

    return {
        "identity_key": identity_key,
        "family":       family,
        "side":         side,
        "line":         line,
        "display":      _build_display(family, side, line, raw.get("market")),
        "raw":          raw,
        "reason_codes": codes,
    }


def _format_line(v: float) -> str:
    # Render 2.5 → "2.5", 1.0 → "1", -0.5 → "-0.5".
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _build_display(family: Optional[str], side: Optional[str],
                    line: Optional[float], market_raw: Optional[str]) -> str:
    """Human-readable label for the UI."""
    if not family:
        return (market_raw or "Mercado desconocido")
    if family == "1X2":
        return ({"HOME": "1X2 — Home",
                 "DRAW": "1X2 — Empate",
                 "AWAY": "1X2 — Away"}.get(side or "", "1X2"))
    if family == "DOUBLE_CHANCE":
        return ({"1X": "Doble oportunidad 1X",
                 "X2": "Doble oportunidad X2",
                 "12": "Doble oportunidad 12"}.get(side or "", "Doble oportunidad"))
    if family == "DNB":
        return ({"HOME": "DNB — Home",
                 "AWAY": "DNB — Away"}.get(side or "", "Draw No Bet"))
    if family == "BTTS":
        return ({"YES": "BTTS — Sí",
                 "NO":  "BTTS — No"}.get(side or "", "BTTS"))
    if family == "TOTAL_GOALS":
        return (f"Over {_format_line(line)} goles" if side == "OVER" and line is not None else
                f"Under {_format_line(line)} goles" if side == "UNDER" and line is not None else
                "Over/Under goles")
    if family == "TOTAL_CORNERS":
        return (f"Over {_format_line(line)} córners" if side == "OVER" and line is not None else
                f"Under {_format_line(line)} córners" if side == "UNDER" and line is not None else
                "Córners totales")
    if family == "TOTAL_CARDS":
        return (f"Over {_format_line(line)} tarjetas" if side == "OVER" and line is not None else
                f"Under {_format_line(line)} tarjetas" if side == "UNDER" and line is not None else
                "Tarjetas totales")
    if family in ("HANDICAP", "HANDICAP_ASIAN"):
        label = "Hándicap" if family == "HANDICAP" else "Hándicap asiático"
        return (f"{label} {side or ''} {_format_line(line) if line is not None else ''}".strip())
    return family.replace("_", " ").title()


def same_market(a: dict | str, b: dict | str,
                 *, home_name: Optional[str] = None,
                 away_name: Optional[str] = None) -> bool:
    """Return True when both inputs resolve to the same identity_key.

    Used by OddsPortal-style validators to compare like-vs-like.
    """
    ia = normalize_market_identity(a, home_name=home_name, away_name=away_name)
    ib = normalize_market_identity(b, home_name=home_name, away_name=away_name)
    if ia["identity_key"].startswith("UNKNOWN:") or ib["identity_key"].startswith("UNKNOWN:"):
        return False
    return ia["identity_key"] == ib["identity_key"]


__all__ = ["normalize_market_identity", "same_market"]
