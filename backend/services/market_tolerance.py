"""Market Tolerance Model — clasifica mercados en AGGRESSIVE / BALANCED /
PROTECTED y proporciona los umbrales de edge que se deben aplicar en cada
categoría.

La idea central es que **no todo edge negativo significa lo mismo**:
  - Un -1% en Moneyline favorito a cuota 1.30 es una trampa clásica.
  - Un -1% en Under 3.5 con baja fragilidad puede ser perfectamente
    aceptable porque el mercado refleja bien la lectura del partido y
    pagas casi lo justo por una cobertura mucho más amplia.

Este módulo NO toma decisiones: solo clasifica y entrega los parámetros
de tolerancia. La decisión final la toma `moneyball_layer.classify_pick`
usando estos parámetros + fragility + confidence + trap signals.
"""
from __future__ import annotations

import re
from typing import Optional


# ── Categorías ──────────────────────────────────────────────────────────────
CATEGORY_AGGRESSIVE = "aggressive"
CATEGORY_BALANCED   = "balanced"
CATEGORY_PROTECTED  = "protected"
CATEGORY_UNKNOWN    = "unknown"


# Parámetros por categoría. Calibrados según la especificación del producto:
#   - AGGRESSIVE: requiere edge mínimo +3%; cualquier edge negativo descarta.
#   - BALANCED:   requiere edge mínimo +1.5%; entre -1% y +1.5% → WATCHLIST.
#   - PROTECTED:  permite edge negativo hasta -1.5% si fragility ≤ 45 y
#                 confidence ≥ 68 y trap_signals ≤ 1. Encima de 0 es
#                 directamente value bet.
TOLERANCE_PARAMS: dict[str, dict] = {
    CATEGORY_AGGRESSIVE: {
        "min_edge":                0.03,   # umbral mínimo para VALUE_BET
        "negative_edge_floor":     0.00,   # cero tolerancia a negativo
        "watchlist_floor":         0.00,   # no aplica watchlist en agresivos
        "max_fragility_acceptable": 55,    # por encima de esto, descartar
        "max_trap_signals":         1,
        "min_confidence":           60,
    },
    CATEGORY_BALANCED: {
        "min_edge":                0.015,
        "negative_edge_floor":     -0.01,  # hasta -1% puede ir a watchlist
        "watchlist_floor":         -0.01,
        "max_fragility_acceptable": 65,
        "max_trap_signals":         2,
        "min_confidence":           60,
    },
    CATEGORY_PROTECTED: {
        "min_edge":                0.00,   # edge >=0 ya es VALUE_BET
        "negative_edge_floor":     -0.015, # tolera hasta -1.5%
        "watchlist_floor":         -0.025, # entre -1.5% y -2.5% va a watchlist
        "max_fragility_acceptable": 45,    # solo si fragility ≤ 45
        "max_trap_signals":         1,
        "min_confidence":           68,
    },
    CATEGORY_UNKNOWN: {
        "min_edge":                0.025,
        "negative_edge_floor":     0.00,
        "watchlist_floor":         -0.005,
        "max_fragility_acceptable": 60,
        "max_trap_signals":         2,
        "min_confidence":           60,
    },
}


# ── Patrones de clasificación (en orden de prioridad) ───────────────────────
# Importante: los patrones más específicos primero (PROTECTED y AGGRESSIVE)
# antes de los neutros (BALANCED). Cualquier mercado que no haga match
# explícito cae a UNKNOWN con parámetros conservadores.
#
# Cada patrón es (regex, category). Se aplican sobre "market + selection"
# en minúsculas para capturar matices tipo "Over 2.5" vs "Under 2.5".
_AGGRESSIVE_PATTERNS: list[str] = [
    # Resultado exacto
    r"\bresultado exacto\b", r"\bcorrect score\b", r"\bexact score\b",
    r"\bexacto\b",
    # Over 2.5 / Over 3.5 — mercados muy líquidos pero exigentes
    r"\bover\s*2\.5\b", r"\bover\s*3\.5\b", r"\bover\s*4\.5\b",
    r"\bmás de\s*2\.5\b", r"\bmás de\s*3\.5\b",
    # BTTS (Both Teams To Score = sí)
    r"\bbtts\b.*\b(s[ií]|yes)\b", r"\bambos.*marcan\b",
    r"\bboth teams to score\b.*\b(yes|s[ií])\b",
    # Hándicap fuerte (-1.5 o más a favor) → muy agresivo
    r"\bh[áa]ndicap\s*-1\.5\b", r"\bh[áa]ndicap\s*-2\.5\b",
    r"\bhandicap\s*-1\.5\b",   r"\bhandicap\s*-2\.5\b",
    r"\bspread\s*-?[2-9]",     # NBA spread fuerte
    r"\brun line\s*-1\.5\b",
    # Player props volátiles
    r"\banotador\b", r"\bgoleador\b", r"\bscorer\b",
    r"\bplayer to score\b", r"\bfirst goal\b", r"\bprimer gol\b",
    r"\bhits\b.*\bover\b",  # hits over X — alto varianza
]

_PROTECTED_PATTERNS: list[str] = [
    # Under 3.5 / Under 4.5 — coberturas amplias
    r"\bunder\s*3\.5\b", r"\bunder\s*4\.5\b",
    r"\bmenos de\s*3\.5\b", r"\bmenos de\s*4\.5\b",
    # Phase F74 — Over 1.5 promovido a PROTECTED (cobertura amplia
    # equivalente: el equipo necesita solo 2 goles totales para cubrir).
    r"\bover\s*1\.5\b", r"\bm[áa]s de\s*1\.5\b",
    # Doble Oportunidad
    r"\bdoble\s*oportunidad\b", r"\bdouble\s*chance\b",
    r"\b1x\b", r"\b12\b", r"\bx2\b",
    # Draw No Bet
    r"\bdraw no bet\b", r"\bempate anula\b", r"\bdnb\b",
    # Asian Handicap +0.5 / +1.0 / +1.5 (cobertura)
    r"\bh[áa]ndicap\s*\+0\.5\b", r"\bh[áa]ndicap\s*\+1\.0\b",
    r"\bh[áa]ndicap\s*\+1\b",    r"\bh[áa]ndicap\s*\+1\.5\b",
    r"\bhandicap\s*\+0\.5\b",    r"\bhandicap\s*\+1\.0\b",
    r"\bhandicap\s*\+1\b",       r"\bhandicap\s*\+1\.5\b",
    r"\bah\s*\+0\.5\b",          r"\bah\s*\+1\b",
    r"\bah\s*\+1\.5\b",
    # Run Line +1.5 / +3.0 (baseball cobertura)
    r"\brun line\s*\+1\.5\b", r"\brun line\s*\+3\.0\b",
    r"\brun line\s*\+1\b",    r"\brun line\s*\+3\b",
    # Team Total Under conservador
    r"\bteam total under\b", r"\btotal equipo under\b",
    r"\btotal de\s+[a-z\s]+\s+under\b",
]

_BALANCED_PATTERNS: list[str] = [
    # Under 2.5 (cobertura media)
    r"\bunder\s*2\.5\b", r"\bmenos de\s*2\.5\b",
    # (Phase F74 — Over 1.5 movido a PROTECTED, no listar aquí.)
    # Spread corto NBA/MLB (≤1.5 puntos / ≤0.5 runs)
    r"\bspread\s*[+-]?[01](\.5)?\b",
    # Moneyline (sin discriminar favorito → balanced; reglas dependen del odds)
    r"\bmoneyline\b", r"\bmatch winner\b", r"\bganador\b",
    r"\bhome/away\b", r"\bganador del partido\b",
    # 1X2 directo
    r"\b1x2\b", r"\bvictoria local\b", r"\bvictoria visitante\b",
    # BTTS = No
    r"\bbtts\b.*\bno\b", r"\bambos no marcan\b",
    r"\bboth teams to score\b.*\bno\b",
    # Asian Handicap 0 (DNB-like)
    r"\bh[áa]ndicap\s*0\b", r"\bhandicap\s*0\b", r"\bah\s*0\b",
    r"\bah\s*\+0\b",
]


# Compilamos una vez al cargar el módulo para velocidad.
_RE_AGGRESSIVE = [re.compile(p, re.IGNORECASE) for p in _AGGRESSIVE_PATTERNS]
_RE_PROTECTED  = [re.compile(p, re.IGNORECASE) for p in _PROTECTED_PATTERNS]
_RE_BALANCED   = [re.compile(p, re.IGNORECASE) for p in _BALANCED_PATTERNS]


def _normalise(text: Optional[str]) -> str:
    if not text:
        return ""
    return str(text).strip().lower()


def classify_market_tolerance(
    market: Optional[str],
    selection: Optional[str] = None,
    *,
    decimal_odds: Optional[float] = None,
) -> str:
    """Devuelve la categoría de tolerancia para un (mercado, selección).

    Estrategia:
      1. Combina `market + selection` en un solo blob normalizado.
      2. Aplica patrones AGGRESSIVE → PROTECTED → BALANCED en orden.
      3. **Excepción Moneyline favorito**: si el mercado es Moneyline /
         1X2 / Match Winner pero la cuota es ≤ 1.40, se reclasifica como
         AGGRESSIVE (favorito popular a precio corto sin colchón de EV).
      4. Si ningún patrón hace match, devuelve UNKNOWN.

    Args:
        market:        nombre del mercado (ej. "Moneyline", "Under 3.5",
                       "Asian Handicap").
        selection:     selección concreta (ej. "Home", "Under", "Manchester
                       City -1.0"). Se incluye en el matching para captar
                       matices.
        decimal_odds:  cuota decimal usada — solo relevante para la regla
                       de Moneyline favorito.

    Returns:
        Una de las constantes CATEGORY_* (string).
    """
    blob = f"{_normalise(market)} {_normalise(selection)}".strip()

    if not blob:
        return CATEGORY_UNKNOWN

    # 1) Aggressive patterns
    for pat in _RE_AGGRESSIVE:
        if pat.search(blob):
            return CATEGORY_AGGRESSIVE

    # 2) Protected patterns
    for pat in _RE_PROTECTED:
        if pat.search(blob):
            return CATEGORY_PROTECTED

    # 3) Balanced patterns
    for pat in _RE_BALANCED:
        if pat.search(blob):
            # Excepción: Moneyline / 1X2 / match-winner a cuota corta
            # se vuelve agresivo (favorito popular sin valor).
            is_moneyline_like = any(
                tok in blob for tok in (
                    "moneyline", "match winner", "1x2", "ganador",
                    "home/away", "victoria local", "victoria visitante",
                )
            )
            if is_moneyline_like and decimal_odds and 0 < decimal_odds <= 1.40:
                return CATEGORY_AGGRESSIVE
            return CATEGORY_BALANCED

    return CATEGORY_UNKNOWN


def tolerance_params(category: str) -> dict:
    """Devuelve el dict de parámetros para una categoría dada.

    Si la categoría es desconocida, devuelve los parámetros de UNKNOWN
    (conservadores). Siempre devuelve una copia para evitar mutación
    accidental.
    """
    return dict(TOLERANCE_PARAMS.get(category, TOLERANCE_PARAMS[CATEGORY_UNKNOWN]))


def is_protected(category: str) -> bool:
    return category == CATEGORY_PROTECTED


def is_aggressive(category: str) -> bool:
    return category == CATEGORY_AGGRESSIVE


def is_balanced(category: str) -> bool:
    return category == CATEGORY_BALANCED


# ── Phase F74 — Floors granulares por familia/línea ─────────────────────────
#
# Cuando una categoría es PROTECTED, no todas las apuestas merecen el mismo
# colchón de edge negativo. Una Doble Oportunidad (cobertura doble) es
# estructuralmente más segura que un Under 3.5, y ambos lo son más que un
# Over 1.5. Mapeamos cada caso a su floor específico, en línea con la
# recalibración de producto F74:
#
#   - DOUBLE_CHANCE (DC)             → -4%
#   - DRAW_NO_BET (DNB)              → -3%
#   - Under 3.5 / Under 4.5          → -3%
#   - Over 1.5                       → -2%
#   - Resto de PROTECTED             → -1.5% (default)
#
# El resto de categorías (AGGRESSIVE / BALANCED / UNKNOWN) usan su floor
# global de ``TOLERANCE_PARAMS`` sin cambios.

# Default floor cuando un mercado es PROTECTED pero no calza con un override.
DEFAULT_PROTECTED_FLOOR   = TOLERANCE_PARAMS[CATEGORY_PROTECTED]["negative_edge_floor"]
DEFAULT_PROTECTED_WL_GAP  = (
    TOLERANCE_PARAMS[CATEGORY_PROTECTED]["watchlist_floor"]
    - TOLERANCE_PARAMS[CATEGORY_PROTECTED]["negative_edge_floor"]
)  # típicamente -0.010 (es decir, watchlist 1pp por debajo del floor)

# Floors granulares F74. Las claves se evalúan en orden contra el blob
# ``market + selection`` ya normalizado a minúsculas.
PROTECTED_GRANULAR_FLOORS: tuple[tuple[str, float], ...] = (
    # Doble Oportunidad (incluye 1X / X2 / 12 y variantes ES/EN)
    ("DOUBLE_CHANCE", -0.04),
    # Draw No Bet
    ("DNB",           -0.03),
    # Under 3.5 / Under 4.5
    ("UNDER_3_5",     -0.03),
    ("UNDER_4_5",     -0.03),
    # Over 1.5
    ("OVER_1_5",      -0.02),
)


def _classify_protected_family(
    market: Optional[str],
    selection: Optional[str] = None,
    *,
    market_identity: Optional[dict] = None,
) -> Optional[str]:
    """Resuelve el sub-tipo de PROTECTED para granular floors.

    Devuelve una de las constantes en PROTECTED_GRANULAR_FLOORS o None
    si no encaja con un override (en cuyo caso aplica el default
    protegido).

    Acepta opcionalmente un ``market_identity`` (forma F71) para tomar
    family/side/line directamente y evitar regex frágiles.
    """
    # 1) Si tenemos market_identity normalizado (F71), úsalo: es la
    #    forma más confiable y permite distinguir DC vs DNB vs Totals
    #    cuando los strings crudos son ambiguos.
    if isinstance(market_identity, dict):
        family = (market_identity.get("family")
                  or market_identity.get("market_family") or "")
        side   = (market_identity.get("side")
                  or market_identity.get("selection") or "")
        line   = market_identity.get("line")
        family = str(family or "").upper()
        side   = str(side or "").upper()
        try:
            line_f = float(line) if line is not None else None
        except (TypeError, ValueError):
            line_f = None

        if family == "DOUBLE_CHANCE":
            return "DOUBLE_CHANCE"
        if family == "DNB":
            return "DNB"
        if family == "TOTAL_GOALS":
            if side == "UNDER" and line_f is not None:
                if abs(line_f - 3.5) < 1e-6 or abs(line_f - 4.5) < 1e-6:
                    return "UNDER_3_5" if abs(line_f - 3.5) < 1e-6 else "UNDER_4_5"
            if side == "OVER" and line_f is not None:
                if abs(line_f - 1.5) < 1e-6:
                    return "OVER_1_5"
            return None

    # 2) Fallback regex sobre market + selection.
    blob = f"{_normalise(market)} {_normalise(selection)}".strip()
    if not blob:
        return None
    # DNB primero — patrones más específicos antes que DC (porque "DNB"
    # podría aparecer junto a "1x"/"x2" en strings sucios).
    if re.search(r"\b(draw no bet|empate anula|dnb|sin\s*empate)\b", blob):
        return "DNB"
    # Doble Oportunidad
    if re.search(r"\b(doble\s*oportunidad|double[\s_\-]?chance|dc)\b", blob):
        return "DOUBLE_CHANCE"
    if re.search(r"\b(1x|x2|12)\b", blob):
        return "DOUBLE_CHANCE"
    # Under 3.5 / Under 4.5
    if re.search(r"\bunder\s*3\.5\b|\bmenos\s*de\s*3\.5\b", blob):
        return "UNDER_3_5"
    if re.search(r"\bunder\s*4\.5\b|\bmenos\s*de\s*4\.5\b", blob):
        return "UNDER_4_5"
    # Over 1.5
    if re.search(r"\bover\s*1\.5\b|\bm[áa]s\s*de\s*1\.5\b", blob):
        return "OVER_1_5"
    return None


def get_protected_floor(
    market: Optional[str] = None,
    selection: Optional[str] = None,
    *,
    market_identity: Optional[dict] = None,
) -> float:
    """Devuelve el floor de edge negativo aplicable a un mercado PROTECTED.

    Si el mercado no coincide con un override granular, devuelve el
    floor por defecto de la categoría PROTECTED (típicamente -1.5%).
    """
    sub = _classify_protected_family(
        market, selection, market_identity=market_identity,
    )
    if sub is None:
        return DEFAULT_PROTECTED_FLOOR
    for key, floor in PROTECTED_GRANULAR_FLOORS:
        if key == sub:
            return floor
    return DEFAULT_PROTECTED_FLOOR


def resolve_edge_floors(
    category: str,
    *,
    market: Optional[str] = None,
    selection: Optional[str] = None,
    market_identity: Optional[dict] = None,
) -> dict:
    """Devuelve los floors efectivos (granulares cuando aplica).

    Output::

        {
          "category":            "protected",
          "negative_edge_floor": -0.04,        # granular si protected, si no, default
          "watchlist_floor":     -0.05,        # ajustado al gap default protegido
          "protected_subfamily": "DOUBLE_CHANCE" | None,
          "is_default":          False,        # True si cae al default de la categoría
        }
    """
    params = tolerance_params(category)
    if category != CATEGORY_PROTECTED:
        return {
            "category":            category,
            "negative_edge_floor": params["negative_edge_floor"],
            "watchlist_floor":     params["watchlist_floor"],
            "protected_subfamily": None,
            "is_default":          True,
        }

    sub = _classify_protected_family(
        market, selection, market_identity=market_identity,
    )
    if sub is None:
        return {
            "category":            CATEGORY_PROTECTED,
            "negative_edge_floor": params["negative_edge_floor"],
            "watchlist_floor":     params["watchlist_floor"],
            "protected_subfamily": None,
            "is_default":          True,
        }

    granular_floor = next(
        (f for k, f in PROTECTED_GRANULAR_FLOORS if k == sub),
        DEFAULT_PROTECTED_FLOOR,
    )
    # Watchlist se sitúa un escalón por debajo del floor para mantener la
    # semántica del flujo PROTECTED en moneyball_layer (watchlist_floor
    # ≤ negative_edge_floor < 0). El gap usa el mismo margen relativo que
    # el default (≈1 punto porcentual) para no cambiar la forma del rango.
    watchlist_floor = round(granular_floor + DEFAULT_PROTECTED_WL_GAP, 4)
    return {
        "category":            CATEGORY_PROTECTED,
        "negative_edge_floor": granular_floor,
        "watchlist_floor":     watchlist_floor,
        "protected_subfamily": sub,
        "is_default":          False,
    }


__all__ = [
    "CATEGORY_AGGRESSIVE",
    "CATEGORY_BALANCED",
    "CATEGORY_PROTECTED",
    "CATEGORY_UNKNOWN",
    "TOLERANCE_PARAMS",
    "PROTECTED_GRANULAR_FLOORS",
    "DEFAULT_PROTECTED_FLOOR",
    "classify_market_tolerance",
    "tolerance_params",
    "is_protected",
    "is_aggressive",
    "is_balanced",
    "get_protected_floor",
    "resolve_edge_floors",
]
