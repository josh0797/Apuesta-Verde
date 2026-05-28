"""Signal catalog — canonical list of editorial context signals.

This catalog is the SINGLE SOURCE OF TRUTH for every signal the betting
engine can surface to the user. It exists so:

  • The same code (`UNDER_TREND_DETECTED`) always has the same human
    label, severity, category, signal_type and explanation no matter
    which layer emitted it (moneyball, alternative_rescue, editorial,
    form_guard).

  • The UI can render a signal without having to switch on its origin
    (the row component just reads `category`, `severity`, `signal_type`,
    `impact` from the dict — every signal has the same shape).

  • Signals can be tagged with the SPORTS they are valid for. The
    aggregator drops cross-sport signals to avoid e.g. surfacing
    `CORNER_VOLUME_DETECTED` on an MLB match (which has no corners).

Schema of a CatalogEntry:
    {
        "code":              str,            # canonical code, e.g. "UNDER_TREND_DETECTED"
        "label":              str,           # human-readable Spanish label
        "label_en":           str,           # English label (UI lang switch)
        "severity":           "low"|"medium"|"high"|"critical",
        "category":           "market"|"motivation"|"historical"|"live"
                              |"tactical"|"statistical"|"liquidity"
                              |"risk"|"protected_market"|"trap",
        "signal_type":        "positive"|"negative"|"neutral",
        "explanation":        str,           # WHY this signal is fired
        "default_impact":     str,           # what it implies for the user
        "applicable_sports":  set[str],      # {'football'}, {'basketball'},
                                              # {'baseball'}, or any combo
    }

To add a new signal: add an entry below. Every layer that emits this
signal must use `make_signal(code, ...)` so the catalog is the only
place where wording / severity / category live.
"""
from __future__ import annotations

from typing import Any, Optional

# ────────────────────────────────────────────────────────────────────────────
# Sport sets — referenced by every catalog entry. Using sets means we can
# do `if sport in entry["applicable_sports"]` cheaply in the aggregator.
# ────────────────────────────────────────────────────────────────────────────
ALL_SPORTS:         set[str] = {"football", "basketball", "baseball"}
FOOTBALL_ONLY:      set[str] = {"football"}
BASKETBALL_ONLY:    set[str] = {"basketball"}
BASEBALL_ONLY:      set[str] = {"baseball"}
FOOT_BASKET:        set[str] = {"football", "basketball"}
BASKET_BASEBALL:    set[str] = {"basketball", "baseball"}


SIGNAL_CATALOG: dict[str, dict[str, Any]] = {
    # ════════════════════════════════════════════════════════════════════
    # ─── TRAP / RISK (negative) ─────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "FAVORITE_NAME_BIAS": {
        "label":       "Favorito sobrevalorado por nombre",
        "label_en":    "Favorite overpriced by reputation",
        "severity":    "high",
        "category":    "trap",
        "signal_type": "negative",
        "explanation": "El mercado paga menos por la reputación del equipo que por dominio estadístico real.",
        "default_impact": "Evita el moneyline directo; revisa mercados protegidos (DC / Spread).",
        "applicable_sports": ALL_SPORTS,
    },
    "LOW_ODDS_NO_VALUE": {
        "label":       "Cuota baja sin valor real",
        "label_en":    "Low odds with no value",
        "severity":    "high",
        "category":    "market",
        "signal_type": "negative",
        "explanation": "Cuota corta sin colchón de EV: el mercado ya descuenta toda la probabilidad y no queda margen.",
        "default_impact": "Salta al siguiente partido o busca cobertura alternativa.",
        "applicable_sports": ALL_SPORTS,
    },
    "SCOREBOARD_TRAP": {
        "label":       "Marcador engañoso",
        "label_en":    "Scoreboard trap",
        "severity":    "medium",
        "category":    "live",
        "signal_type": "negative",
        "explanation": "El marcador no refleja el balance de juego — el equipo con ventaja puede estar siendo dominado.",
        "default_impact": "Si juegas live, cuidado con cash-out tardío; el resultado puede revertir.",
        "applicable_sports": ALL_SPORTS,
    },
    "NO_STATISTICAL_DOMINANCE": {
        "label":       "Sin dominio estadístico real",
        "label_en":    "No statistical dominance",
        "severity":    "high",
        "category":    "statistical",
        "signal_type": "negative",
        "explanation": "El pick depende de la confianza del modelo, no de un dominio estadístico claro (xG/pace/runs).",
        "default_impact": "Reduce stake o evita; el partido está más parejo de lo que la cuota sugiere.",
        "applicable_sports": ALL_SPORTS,
    },
    "PUBLIC_NARRATIVE_OVERREACTION": {
        "label":       "Sobre-reacción de narrativa pública",
        "label_en":    "Public narrative overreaction",
        "severity":    "medium",
        "category":    "motivation",
        "signal_type": "negative",
        "explanation": "El argumento apoya el pick en frases hechas ('necesitan ganar') sin métricas que las respalden.",
        "default_impact": "El mercado ya descontó la narrativa; busca contra-mercados.",
        "applicable_sports": ALL_SPORTS,
    },
    "MOTIVATION_OVERPRICED": {
        "label":       "Motivación ya descontada por el mercado",
        "label_en":    "Motivation already priced in",
        "severity":    "medium",
        "category":    "motivation",
        "signal_type": "negative",
        "explanation": "La presión / motivación está reflejada en la cuota; el upside ya se pagó hace días.",
        "default_impact": "Sin edge: pasa o juega el lado opuesto si hay valor.",
        "applicable_sports": ALL_SPORTS,
    },
    "H2H_MISLEADING": {
        "label":       "Histórico H2H engañoso",
        "label_en":    "Misleading head-to-head",
        "severity":    "low",
        "category":    "historical",
        "signal_type": "negative",
        "explanation": "El H2H reciente sesga al pick, pero las condiciones de los equipos cambiaron desde entonces.",
        "default_impact": "No bases la decisión solo en H2H; pondera forma reciente.",
        "applicable_sports": ALL_SPORTS,
    },
    "LINE_MOVEMENT_AGAINST_PICK": {
        "label":       "Línea se mueve en contra",
        "label_en":    "Line moves against the pick",
        "severity":    "high",
        "category":    "market",
        "signal_type": "negative",
        "explanation": "La cuota se ha alargado/movido contra el pick: el dinero inteligente está en el otro lado.",
        "default_impact": "Si entras, hazlo con stake reducido o evita.",
        "applicable_sports": ALL_SPORTS,
    },
    "LOW_LIQUIDITY_MARKET": {
        "label":       "Mercado con baja liquidez",
        "label_en":    "Low liquidity market",
        "severity":    "low",
        "category":    "liquidity",
        "signal_type": "negative",
        "explanation": "Pocos books cotizan este mercado — la línea puede no reflejar consenso.",
        "default_impact": "Cuidado con cuotas atípicas; verifica en 2+ books antes de apostar.",
        "applicable_sports": ALL_SPORTS,
    },
    "LIVE_MOMENTUM_OPPOSITE": {
        "label":       "Momentum live contrario",
        "label_en":    "Live momentum against pick",
        "severity":    "high",
        "category":    "live",
        "signal_type": "negative",
        "explanation": "El partido live tiene momentum claramente en contra del pick — riesgo de cambio de marcador.",
        "default_impact": "Considera cash-out o cobertura del lado contrario.",
        "applicable_sports": ALL_SPORTS,
    },
    "RED_CARD_CONTEXT": {
        "label":       "Contexto de tarjeta roja",
        "label_en":    "Red card context",
        "severity":    "high",
        "category":    "risk",
        "signal_type": "negative",
        "explanation": "Hay inferioridad numérica que altera la dinámica esperada del partido.",
        "default_impact": "Reevalúa goles esperados y mercado de corners; el partido cambió.",
        "applicable_sports": FOOTBALL_ONLY,   # red card sólo en fútbol
    },
    "LATE_GAME_VOLATILITY": {
        "label":       "Volatilidad de fin de partido",
        "label_en":    "Late-game volatility",
        "severity":    "medium",
        "category":    "live",
        "signal_type": "negative",
        "explanation": "El partido entra en fase volátil (último cuarto / final / extra) donde un evento decide todo.",
        "default_impact": "Reduce stake si juegas live; el resultado puede flipear.",
        "applicable_sports": ALL_SPORTS,
    },
    "WEAK_DEFENSIVE_PROFILE": {
        "label":       "Perfil defensivo débil",
        "label_en":    "Weak defensive profile",
        "severity":    "medium",
        "category":    "statistical",
        "signal_type": "negative",
        "explanation": "Las defensas no soportan el pick: equipos concedieron muchos goles/runs/puntos recientes.",
        "default_impact": "Si pickeaste Under, revisa el racional; si Over, refuerza la lectura.",
        "applicable_sports": ALL_SPORTS,
    },
    "OVERDEPENDENT_ON_ONE_EVENT": {
        "label":       "Depende de un solo evento",
        "label_en":    "Over-dependent on a single event",
        "severity":    "medium",
        "category":    "risk",
        "signal_type": "negative",
        "explanation": "El pick necesita un evento puntual (gol exacto, primer corner, scorer) para resolverse positivamente.",
        "default_impact": "Stake recreativo; no hagas grandes apuestas en mercados de evento único.",
        "applicable_sports": ALL_SPORTS,
    },
    "CONFIDENCE_ALREADY_PRICED": {
        "label":       "Confianza ya descontada",
        "label_en":    "Confidence already priced",
        "severity":    "medium",
        "category":    "market",
        "signal_type": "negative",
        "explanation": "Confianza alta pero la implied probability ya cubre el escenario — no hay margen de edge.",
        "default_impact": "Sin valor: descarta o busca alternativos.",
        "applicable_sports": ALL_SPORTS,
    },
    "CASH_OUT_LOW": {
        "label":       "Cash-out muy bajo",
        "label_en":    "Cash-out too low",
        "severity":    "low",
        "category":    "live",
        "signal_type": "negative",
        "explanation": "El cash-out ofrecido es bajo, indicando que el mercado descuenta probabilidad real baja.",
        "default_impact": "Considera dejar correr; cash-out no compensa el riesgo.",
        "applicable_sports": ALL_SPORTS,
    },

    # ════════════════════════════════════════════════════════════════════
    # ─── POSITIVE / PROTECTIVE SIGNALS ──────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "PROTECTED_MARKET_AVAILABLE": {
        "label":       "Mercado protegido disponible",
        "label_en":    "Protected market available",
        "severity":    "medium",
        "category":    "protected_market",
        "signal_type": "positive",
        "explanation": "Aunque el moneyline directo no tenga valor, hay mercados protegidos (DC, +1.5, Run Line) coherentes con la lectura.",
        "default_impact": "Revisa el alternativo: stake similar con menor varianza.",
        "applicable_sports": ALL_SPORTS,
    },
    "LOW_FRAGILITY_MARKET": {
        "label":       "Mercado de baja fragilidad",
        "label_en":    "Low-fragility market",
        "severity":    "low",
        "category":    "protected_market",
        "signal_type": "positive",
        "explanation": "Pocas trampas detectadas (≤1) y línea estable: el pick aguanta variaciones razonables.",
        "default_impact": "Jugable como tu stake estándar.",
        "applicable_sports": ALL_SPORTS,
    },
    "STRONG_H2H_PATTERN": {
        "label":       "Patrón H2H fuerte",
        "label_en":    "Strong H2H pattern",
        "severity":    "medium",
        "category":    "historical",
        "signal_type": "positive",
        "explanation": "Histórico reciente entre los equipos respalda la lectura del pick (≥4 de 5 alineados con el mercado elegido).",
        "default_impact": "Aporta convicción extra al pick.",
        "applicable_sports": ALL_SPORTS,
    },
    "UNDER_TREND_DETECTED": {
        "label":       "Tendencia fuerte al Under",
        "label_en":    "Strong Under trend",
        "severity":    "medium",
        "category":    "historical",
        "signal_type": "positive",
        "explanation": "Los equipos vienen de varias rachas Under (goles/runs/puntos por debajo de la línea típica).",
        "default_impact": "Considera Under 2.5 / 3.5 (fútbol), Under total runs (MLB), Under total points (NBA).",
        "applicable_sports": ALL_SPORTS,
    },
    "CORNER_VOLUME_DETECTED": {
        "label":       "Volumen alto de córners detectado",
        "label_en":    "High corner volume detected",
        "severity":    "medium",
        "category":    "tactical",
        "signal_type": "positive",
        "explanation": "Ambos equipos generan muchos córners — el mercado de corners directos o team corners ofrece edge.",
        "default_impact": "Revisa Over córners 9.5 / 10.5 o team corners Over.",
        "applicable_sports": FOOTBALL_ONLY,   # corners sólo en fútbol
    },
    "TEAM_TOTAL_UNDER_SIGNAL": {
        "label":       "Señal Team Total Under",
        "label_en":    "Team Total Under signal",
        "severity":    "medium",
        "category":    "historical",
        "signal_type": "positive",
        "explanation": "Un equipo en particular tiene una racha clara de no superar su team total típico.",
        "default_impact": "Apuesta sobre el team total Under del equipo específico (no del total).",
        "applicable_sports": ALL_SPORTS,
    },
    "PACE_OVER_SIGNAL": {
        "label":       "Pace alto → Over total points",
        "label_en":    "High pace → Over total points",
        "severity":    "medium",
        "category":    "statistical",
        "signal_type": "positive",
        "explanation": "Ambos equipos juegan a ritmo alto (posesiones/48' por encima de la media liga).",
        "default_impact": "Considera Over total points o Over team total del equipo con mejor anotación.",
        "applicable_sports": BASKETBALL_ONLY,
    },
    "PITCHER_DUEL_SIGNAL": {
        "label":       "Duelo de pitchers — Under runs",
        "label_en":    "Pitcher duel — Under runs",
        "severity":    "medium",
        "category":    "tactical",
        "signal_type": "positive",
        "explanation": "Ambos abridores tienen ERA bajo y K/BB alto. Esperable juego de pocas carreras.",
        "default_impact": "Considera Under total runs o F5 Under.",
        "applicable_sports": BASEBALL_ONLY,
    },
    "BULLPEN_FATIGUE_SIGNAL": {
        "label":       "Bullpen fatigado",
        "label_en":    "Bullpen fatigue",
        "severity":    "medium",
        "category":    "tactical",
        "signal_type": "negative",
        "explanation": "Uno o ambos bullpens vienen con uso elevado en los últimos 3 días. Riesgo de runs tardíos.",
        "default_impact": "Si pickeaste Under, refuerza con F5 (más seguro que full game).",
        "applicable_sports": BASEBALL_ONLY,
    },

    # ════════════════════════════════════════════════════════════════════
    # ─── NEUTRAL SIGNALS (informational) ────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "MOTIVATION_NORMAL": {
        "label":       "Motivación equilibrada",
        "label_en":    "Balanced motivation",
        "severity":    "low",
        "category":    "motivation",
        "signal_type": "neutral",
        "explanation": "Ambos equipos tienen motivación media-alta; ningún lado tiene incentivos extra.",
        "default_impact": "Decisión sólo en función de mercado/forma.",
        "applicable_sports": ALL_SPORTS,
    },
    "BALANCED_MATCH": {
        "label":       "Partido parejo",
        "label_en":    "Balanced match",
        "severity":    "low",
        "category":    "statistical",
        "signal_type": "neutral",
        "explanation": "Métricas y forma sugieren un partido parejo; el favorito en cuota lo está sólo marginalmente.",
        "default_impact": "Mercados de doble oportunidad o protegidos son los más viables.",
        "applicable_sports": ALL_SPORTS,
    },
    "DATA_PARTIAL": {
        "label":       "Datos parciales",
        "label_en":    "Partial data",
        "severity":    "medium",
        "category":    "risk",
        "signal_type": "neutral",
        "explanation": "Faltan métricas clave (cuotas, alineaciones, lesiones, forma) para una lectura completa.",
        "default_impact": "Stake reducido o esperar a que llegue más información.",
        "applicable_sports": ALL_SPORTS,
    },

    # ════════════════════════════════════════════════════════════════════
    # ─── EDITORIAL ORIGIN (mapped from Scrapy / Playwright signals) ────
    # ════════════════════════════════════════════════════════════════════
    "EDITORIAL_MARKET_SUGGESTION": {
        "label":       "Sugerencia editorial de mercado",
        "label_en":    "Editorial market suggestion",
        "severity":    "low",
        "category":    "market",
        "signal_type": "neutral",
        "explanation": "Una o más fuentes editoriales recomiendan un mercado/cuota concretos. Úsalo como contexto, no como verdad.",
        "default_impact": "Compara contra la lectura del engine antes de confiar.",
        "applicable_sports": ALL_SPORTS,
    },
    "EDITORIAL_INJURY_NOTE": {
        "label":       "Lesión reportada (editorial)",
        "label_en":    "Reported injury (editorial)",
        "severity":    "high",
        "category":    "risk",
        "signal_type": "negative",
        "explanation": "Las fuentes editoriales reportan baja confirmada/probable de un jugador clave.",
        "default_impact": "Reevalúa o evita si el jugador es titular fijo.",
        "applicable_sports": ALL_SPORTS,
    },
    "EDITORIAL_MOTIVATION_NOTE": {
        "label":       "Nota de motivación editorial",
        "label_en":    "Editorial motivation note",
        "severity":    "medium",
        "category":    "motivation",
        "signal_type": "neutral",
        "explanation": "Las fuentes editoriales mencionan motivación extra (jugarse el descenso, ronda final, etc.).",
        "default_impact": "Considera si esa motivación ya está descontada en la línea.",
        "applicable_sports": ALL_SPORTS,
    },
    "EDITORIAL_CONTRADICTION": {
        "label":       "Contradicción entre fuentes",
        "label_en":    "Sources contradict each other",
        "severity":    "medium",
        "category":    "risk",
        "signal_type": "negative",
        "explanation": "Las fuentes editoriales se contradicen — la lectura del partido no es consenso.",
        "default_impact": "Trata el partido con cautela; stake reducido o evita.",
        "applicable_sports": ALL_SPORTS,
    },

    # ════════════════════════════════════════════════════════════════════
    # ─── FORM GUARD ORIGIN ──────────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "FORM_CRITICAL_STREAK": {
        "label":       "Racha negativa crítica",
        "label_en":    "Critical losing streak",
        "severity":    "critical",
        "category":    "historical",
        "signal_type": "negative",
        "explanation": "Un equipo viene en racha negativa crítica (≥4 derrotas seguidas o form_score ≤ -60).",
        "default_impact": "Evita pickear a ese equipo a ganar; refuerza el lado contrario.",
        "applicable_sports": ALL_SPORTS,
    },
}


def _entry(code: str) -> Optional[dict[str, Any]]:
    return SIGNAL_CATALOG.get(code)


def make_signal(
    code: str,
    *,
    sport: Optional[str] = None,
    confidence: Optional[int] = None,
    extra_explanation: str = "",
    impact_override: Optional[str] = None,
    lang: str = "es",
) -> Optional[dict[str, Any]]:
    """Build a canonical signal dict from a catalog code.

    Returns ``None`` (caller should skip) when the code is unknown OR when
    the catalog says this code is not applicable to the requested sport.
    This is the sport-aware guardrail requested by the user — e.g.
    ``make_signal("RED_CARD_CONTEXT", sport="baseball")`` returns ``None``.
    """
    entry = _entry(code)
    if not entry:
        return None
    if sport and sport not in entry["applicable_sports"]:
        # Cross-sport signal — drop silently.
        return None
    explanation = entry["explanation"]
    if extra_explanation:
        explanation = f"{explanation} {extra_explanation}".strip()
    label = entry["label_en"] if lang == "en" else entry["label"]
    out: dict[str, Any] = {
        "code":          code,
        "label":         label,
        "severity":      entry["severity"],
        "category":      entry["category"],
        "signal_type":   entry["signal_type"],
        "explanation":   explanation,
        "impact":        impact_override or entry["default_impact"],
        "confidence":    confidence if confidence is not None else _default_confidence(entry["severity"]),
    }
    return out


def _default_confidence(severity: str) -> int:
    """Map severity → a default confidence percentage when caller doesn't
    pass one explicitly. High-severity catalog signals are surfaced with
    higher confidence so the UI can sort them first."""
    return {
        "critical": 90,
        "high":     80,
        "medium":   65,
        "low":      50,
    }.get(severity, 60)


def is_known_code(code: str) -> bool:
    return code in SIGNAL_CATALOG


def applicable_codes_for(sport: str) -> list[str]:
    """Return the list of codes valid for a given sport (used by tests
    and the admin endpoint)."""
    return [c for c, e in SIGNAL_CATALOG.items() if sport in e["applicable_sports"]]


__all__ = [
    "SIGNAL_CATALOG",
    "make_signal",
    "is_known_code",
    "applicable_codes_for",
    "ALL_SPORTS",
    "FOOTBALL_ONLY",
    "BASKETBALL_ONLY",
    "BASEBALL_ONLY",
]
