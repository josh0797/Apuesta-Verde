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
    # Over 1.5 (cobertura media — equipo necesita 2 goles)
    r"\bover\s*1\.5\b", r"\bmás de\s*1\.5\b",
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


__all__ = [
    "CATEGORY_AGGRESSIVE",
    "CATEGORY_BALANCED",
    "CATEGORY_PROTECTED",
    "CATEGORY_UNKNOWN",
    "TOLERANCE_PARAMS",
    "classify_market_tolerance",
    "tolerance_params",
    "is_protected",
    "is_aggressive",
    "is_balanced",
]
