"""Editorial signal mapper — classify text fragments into structured signals.

This is the heuristic CORE of P3. Given a sentence (or short paragraph)
from a preview/prediction article, it decides whether the fragment is:

  FACTUAL_CONTEXT   — hard contextual info the engine can use
                       ('Rayo pelea Europa', 'Alavés ya aseguró permanencia')
  MARKET_SUGGESTION — the editorial recommends a market
                       ('apuesta segura', 'recomendamos under 2.5')
  SCORE_PREDICTION  — the editorial predicts a final score
                       ('marcador 0-1', 'gana 2-1 el Atlético')
  OPINION           — narrative without factual backing
                       ('claro favorito', 'no hay color')
  WARNING           — explicit risk flag
                       ('partido trampa', 'cuidado con la rotación')
  INJURY_NOTE       — mentions injuries / unavailable players
                       ('baja confirmada', 'lesionado de gravedad')
  MOTIVATION_NOTE   — motivation/objectives context
                       ('necesita ganar para salvarse', 'permanencia asegurada')

Classification is keyword + regex based (no LLM). Confidence is a heuristic
in {0.50, 0.65, 0.80, 0.90} reflecting how many distinct positive markers
hit AND whether any negation pattern fired.

The classifier is fail-soft: when uncertain, it returns OPINION with low
confidence rather than guessing FACTUAL_CONTEXT.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


SIGNAL_TYPES = {
    "FACTUAL_CONTEXT",
    "MARKET_SUGGESTION",
    "SCORE_PREDICTION",
    "OPINION",
    "WARNING",
    "INJURY_NOTE",
    "MOTIVATION_NOTE",
}


# ── Regex catalogue ────────────────────────────────────────────────────────
# Each list = positive markers for that signal type. We compile them once.
_SCORE_PREDICTION_PATTERNS = [
    r"\b\d\s*[-:\u2013]\s*\d\b",                             # 1-0, 2-1, 1–1, 0:0
    r"\bmarcador\s+(?:probable|predicho|esperado)\b",
    r"\bcontundente\s+\d\s*[-:\u2013]\s*\d\b",
    r"\b(?:gana|vence|cae)\s+(?:por\s+)?\d\s*[-:\u2013]\s*\d\b",
]

_MARKET_SUGGESTION_PATTERNS = [
    r"\b(recomend\w+|sugerimos|apostamos por|apuesta\s+(?:segura|recomendada|del día)|nuestro pron[oo]stico|nuestro pick|nuestra apuesta)\b",
    r"\b(under|over|más de|menos de)\s+\d+(?:[.,]\d+)?\b",
    r"\b(doble oportunidad|hand[ie]cap|hándicap|empate no|no pierde|gana|gana o empata)\b",
    r"\b(btts|ambos equipos marcan|ambos anotan)\b",
    r"\bcuota\s+\d+[.,]\d+\b",
    r"@\s*\d+[.,]\d+\b",
    # ── MLB markets (Moneyball P4) ──────────────────────────────────
    r"\b(run\s*line|moneyline|línea\s+de\s+carreras|línea\s+de\s+runs)\b",
    r"\b(total\s+(?:runs|carreras))\b",
    r"\b(?:f5|first\s+5\s+innings?|primeras?\s+5\s+entradas?|primeras?\s+cinco\s+entradas?)\b",
    r"\b(?:nrfi|yrfi|no\s+run\s+first\s+inning|primera\s+entrada\s+sin\s+carreras?)\b",
    r"\b(team\s+total|total\s+del\s+equipo|over\s+(?:de\s+)?carreras?)\b",
    r"\b(más|mas|menos)\s+de\s+\d+(?:[.,]\d+)?\s+(?:carreras?|hits?|entradas?)\b",
    # ── NBA / Basketball markets ────────────────────────────────────
    r"\b(spread|línea\s+puntos|handicap\s+puntos)\b",
    r"\b(total\s+points|total\s+de\s+puntos)\b",
    r"\b(player\s+prop|prop\s+de\s+jugador)\b",
]

# ── MLB / Baseball factual context — sabermetrics + traditional ────────
_FACTUAL_BASEBALL_PATTERNS = [
    r"\b(era|xera|fip|whip|ops|war|xwoba|wrc\+?|babip)\b",
    r"\b(hard[-\s]?hit|barrel(?:\s+rate)?|exit\s+velocity|launch\s+angle)\b",
    r"\b(whiff|chase|zone|in\s+zone|fastball\s+velocity)\b",
    r"\b(strikeouts?|ponches?|k\s*%|k/9)\b",
    r"\b(walks?|bb%?|bb/9|boletos?|bases?\s+por\s+bolas?)\b",
    r"\b(home\s+runs?|jonrones?|hr\s*/9|hr%)\b",
    r"\b(bullpen|relevo|relevistas?|cerrador|closer|setup)\b",
    r"\b(?:probable|starting)\s+pitcher\b",
    r"\b(lineup(?:\s+confirmed)?|alineaci[oó]n\s+confirmada)\b",
    r"\b(pitcher\s+scratched|abridor\s+borrado|scratched)\b",
    r"\b(opener|abridor\s+corto|piggyback)\b",
    r"\b(descanso\s+bullpen|bullpen\s+rest(?:ed)?|rested\s+bullpen)\b",
    r"\b(innings\s+pitched|entradas\s+lanzadas|ip)\b",
    r"\b(left\s+on\s+base|lob%?|corredores\s+en\s+base)\b",
    r"\b\d+(?:[.,]\d+)?\s+(?:carreras?|hits?|jonrones?|ponches?|innings?|entradas?)\b",
]

# ── NBA / Basketball factual context ───────────────────────────────────
_FACTUAL_BASKETBALL_PATTERNS = [
    r"\b(pace|ritmo\s+de\s+juego|posesiones\s+por\s+partido)\b",
    r"\b(offensive\s+rating|defensive\s+rating|ortg|drtg)\b",
    r"\b(back[-\s]?to[-\s]?back|b2b|segundo\s+partido\s+en)\b",
    r"\b(rest\s+advantage|ventaja\s+de\s+descanso|días?\s+de\s+descanso)\b",
    r"\b(injury\s+report|reporte\s+de\s+lesiones)\b",
    r"\b(minutes\s+restriction|restricción\s+de\s+minutos|minutos\s+limitados)\b",
    r"\b(efg%?|true\s+shooting|ts%?|3p%?|fg%?)\b",
    r"\b(rebounds?|rebotes?|assists?|asistencias?)\b",
]

_OPINION_PATTERNS = [
    r"\b(claro favorito|imbatible|no hay color|favoritísimo)\b",
    r"\bclar\w+ favorito\b",
    r"\bel(?:los)?\s+(?:debe|deben)\s+ganar\b",
    r"\bsin sorpresas\b",
    r"\b(siempre|nunca)\s+(?:gana|pierde|saca)\b",
    r"\bes una apuesta segura\b",
    r"\bsuper(?:ior|ioridad)\s+aplastante\b",
]

_WARNING_PATTERNS = [
    r"\b(partido\s+trampa|cuidado con|atenci[oó]n a|riesgo|rotaci[oó]n|sin (?:su|el) goleador|alineaci[oó]n\s+rara|partido\s+de\s+poco\s+inter[eé]s)\b",
    r"\b(suspensi[oó]n|sancionad[oa])\b",
    r"\b(volatil(?:idad)?|imprevis(?:ible|to))\b",
    r"\b(ya cumpli[oó]|ya asegur[oó])\b.{0,40}\b(no\s+arries|no\s+forzar|sin\s+presi[oó]n)\b",
    # ── MLB warnings ────────────────────────────────────────────────
    r"\b(bullpen\s+fatigad[oa]|tired\s+bullpen|relevo\s+fatigado|relievers?\s+overworked)\b",
    r"\b(pitcher\s+risk|riesgo\s+del?\s+abridor|abridor\s+riesgoso)\b",
    r"\b(hard\s+contact\s+risk|riesgo\s+de\s+contacto\s+duro)\b",
    r"\b(lineup\s+weak(?:ened)?|alineaci[oó]n\s+debilitada)\b",
    r"\b(pitcher\s+scratched|abridor\s+borrado|opener\s+inesperado)\b",
    r"\b(weather\s+boosts?\s+offense|clima\s+favorece\s+ofensiva|viento\s+a\s+favor)\b",
    r"\b(park\s+factor|coors\s+field|hitter[-\s]?friendly\s+park|estadio\s+ofensivo)\b",
    r"\b(high\s+hit\s+pressure|presi[oó]n\s+de\s+hits\s+alta)\b",
    r"\b(public\s+over\s+trap|trampa\s+(?:de|del)\s+over|over\s+público)\b",
    r"\b(under\s+fragile|under\s+frágil|under\s+vulnerable)\b",
    r"\b(explosive\s+inning\s+risk|riesgo\s+de\s+entrada\s+explosiva)\b",
    # ── NBA warnings ────────────────────────────────────────────────
    r"\b(blowout\s+risk|riesgo\s+de\s+paliza|garbage\s+time)\b",
    r"\b(live\s+momentum|momentum\s+en\s+vivo|impulso\s+en\s+vivo)\b",
    r"\b(load\s+management|gestión\s+de\s+carga)\b",
]

_INJURY_PATTERNS = [
    r"\b(baja(?:s)?\s+(?:confirmada|sensible|importante|grave|por lesi[oó]n)|lesionad[oa]|fuera\s+por\s+lesi[oó]n|duda\s+por\s+lesi[oó]n)\b",
    r"\bse\s+pierde\s+el\s+partido\b",
    r"\b(no\s+ser[aá]\s+de\s+la\s+partida|no\s+podr[aá]\s+jugar)\b",
]

_MOTIVATION_PATTERNS = [
    r"\b(pelea\s+(?:el|por|playoff|playoffs|europa|champions|descenso|permanencia|salvaci[oó]n|t[ií]tulo|ascenso))\b",
    r"\b(necesita\s+(?:ganar|sumar|los\s+3\s+puntos))\b",
    r"\b(se juega\s+(?:la\s+vida|el\s+partido|todo))\b",
    r"\b(asegur(?:ar|ad[oa])\s+(?:la\s+)?permanencia|salvad[oa]\s+matem[aá]ticamente)\b",
    r"\b(ya\s+(?:no\s+)?(?:se\s+juega\s+nada|no\s+tiene\s+objetivos|cumpli[oó]\s+sus?\s+objetivos?))\b",
    r"\b(racha\s+(?:invicta|positiva|negativa|de\s+derrotas|de\s+victorias))\b",
    r"\b(motivaci[oó]n\s+(?:alta|baja|máxima)|sin\s+motivaci[oó]n)\b",
]

_FACTUAL_FRAGMENT_PATTERNS = [
    # Quantitative facts: 'promedia 2.3 goles', '13 de 15 partidos', '70% de victorias'
    r"\b\d+(?:[.,]\d+)?\s*(?:goles?|tarjetas?|c[oó]rner(?:s|es)?|tiros?|disparos?|posesi[oó]n)\b",
    r"\b\d+\s*(?:de|/)\s*\d+\s*(?:partidos?|jornadas?|encuentros?)\b",
    r"\b\d+(?:[.,]\d+)?\s*%\b",
    r"\b(promedi(?:a|o)|media)\s+\d+(?:[.,]\d+)?\b",
    r"\b(?:ha\s+)?(?:gan(?:ad|ó)|perd(?:id|ió)|empat(?:ad|ó))\s+\d+\b",
    r"\b(?:lleva|suma)\s+\d+\s+(?:partidos?|jornadas?|victorias?|derrotas?|empates?)\b",
    # Standings / positions
    r"\b(?:est[aá]|se\s+encuentra)\s+(?:en\s+)?(?:la\s+)?(?:zona\s+de|posici[oó]n)\b",
    r"\b(último|penúltimo|colista|l[ií]der|sublider)\b",
    # Historical
    r"\b(en\s+los?\s+últimos?\s+\d+\s+(?:partidos?|encuentros?))\b",
    r"\bh2h\b",
]


# ── Sport hint vocabulary ─────────────────────────────────────────────
# Discriminates the dominant sport of a sentence. The mapper attaches
# the hint to the output so downstream consumers (analyst_engine,
# editorial_context_service) can route MLB / NBA / Football signals
# correctly.  Order: more specific keywords first.
_SPORT_HINT_RULES: list[tuple[str, re.Pattern]] = [
    ("baseball", re.compile(
        r"\b(pitcher|abridor|bullpen|relev(?:o|ista)|"
        r"closer|cerrador|opener|"
        r"home\s*runs?|jonr[oó]nes?|"
        r"strikeouts?|ponches?|"
        r"hits?\s+(?:permitidos?|allowed)?|"
        r"carreras?\s+(?:limpias?)?|"
        r"innings?|entradas?|"
        r"era|xera|fip|whip|ops|war|xwoba|wrc\+?|"
        r"nrfi|yrfi|"
        r"f5|first\s+5\s+innings?|primeras?\s+5\s+entradas?|"
        r"team\s+total\s+over|"
        r"run\s*line|moneyline|"
        r"hard[-\s]?hit|barrel\s+rate|"
        r"coors\s+field|park\s+factor)\b",
        re.IGNORECASE,
    )),
    ("basketball", re.compile(
        r"\b(puntos|spread|"
        r"pace|ritmo\s+de\s+juego|"
        r"offensive\s+rating|defensive\s+rating|"
        r"back[-\s]?to[-\s]?back|b2b|"
        r"rebound(?:e?s)?|rebotes?|"
        r"assists?|asistencias?|"
        r"three[-\s]?point(?:er)?|tiros?\s+de\s+3|triple|"
        r"player\s+prop|"
        r"minutes\s+restriction|"
        r"true\s+shooting|"
        r"load\s+management|"
        r"ortg|drtg)\b",
        re.IGNORECASE,
    )),
    ("football", re.compile(
        r"\b(goles?|gol\b|"
        r"c[oó]rner(?:s|es)?|"
        r"tarjetas?\s+(?:amarillas?|rojas?)?|"
        r"penalti|penalty|"
        r"fuera\s+de\s+juego|"
        r"jornadas?|liga|"
        r"prórroga|prorroga|"
        r"empate)\b",
        re.IGNORECASE,
    )),
]


def detect_sport_hint(sentence: str) -> Optional[str]:
    """Return ``baseball`` / ``basketball`` / ``football`` or None.

    Pure heuristic — first matching sport wins. Returns None when the
    sentence is too ambiguous to discriminate.
    """
    if not sentence:
        return None
    for sport, regex in _SPORT_HINT_RULES:
        if regex.search(sentence):
            return sport
    return None


_NEGATION_RE = re.compile(
    r"\b(no|nunca|jam[aá]s|ning[uú]n[oa]?|tampoco)\b",
    re.IGNORECASE,
)


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]


_COMPILED: dict[str, list[re.Pattern]] = {
    "SCORE_PREDICTION":  _compile(_SCORE_PREDICTION_PATTERNS),
    "MARKET_SUGGESTION": _compile(_MARKET_SUGGESTION_PATTERNS),
    "OPINION":           _compile(_OPINION_PATTERNS),
    "WARNING":           _compile(_WARNING_PATTERNS),
    "INJURY_NOTE":       _compile(_INJURY_PATTERNS),
    "MOTIVATION_NOTE":   _compile(_MOTIVATION_PATTERNS),
    "FACTUAL_CONTEXT":   _compile(
        _FACTUAL_FRAGMENT_PATTERNS
        + _FACTUAL_BASEBALL_PATTERNS
        + _FACTUAL_BASKETBALL_PATTERNS
    ),
}

# Order matters: a sentence that matches both SCORE_PREDICTION and OPINION
# should be tagged SCORE_PREDICTION (more specific).
_PRIORITY = [
    "SCORE_PREDICTION",
    "MARKET_SUGGESTION",
    "INJURY_NOTE",
    "MOTIVATION_NOTE",
    "WARNING",
    "FACTUAL_CONTEXT",
    "OPINION",
]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _sentence_split(text: str) -> list[str]:
    """Lightweight Spanish/English sentence splitter."""
    if not text:
        return []
    # Normalise whitespace; keep ', ' as a soft delimiter for very long clauses
    cleaned = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    # Drop tiny remnants
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def classify_signal(sentence: str) -> dict:
    """Classify a SINGLE sentence into a signal type with confidence.

    Returns:
        {
            "signal_type":  one of SIGNAL_TYPES,
            "confidence":   0.50 | 0.65 | 0.80 | 0.90,
            "matched":      ["score_prediction", "opinion"]  # all hits, for debug
            "is_negated":   bool,
            "sport_hint":   "baseball" | "basketball" | "football" | None,
            "tags":         list[str]   # ('MLB_NORMAL_MOTIVATION_NEUTRAL', ...)
        }
    """
    if not sentence:
        return {
            "signal_type": "OPINION",
            "confidence":  0.5,
            "matched":     [],
            "is_negated":  False,
            "sport_hint":  None,
            "tags":        [],
        }
    s = sentence.strip()
    matched: list[str] = []
    hit_counts: dict[str, int] = {}
    for sig_type, patterns in _COMPILED.items():
        n = 0
        for p in patterns:
            if p.search(s):
                n += 1
        if n > 0:
            matched.append(sig_type)
            hit_counts[sig_type] = n
    # Highest-priority match wins
    chosen: Optional[str] = None
    for t in _PRIORITY:
        if t in matched:
            chosen = t
            break
    if chosen is None:
        chosen = "OPINION"
    is_negated = bool(_NEGATION_RE.search(s))
    # Confidence scale
    hits = hit_counts.get(chosen, 0)
    if hits >= 3:
        confidence = 0.90
    elif hits == 2:
        confidence = 0.80
    elif hits == 1:
        confidence = 0.65
    else:
        confidence = 0.50
    # If the sentence is short and we found OPINION patterns only, keep it lower.
    if chosen == "OPINION" and hits == 0:
        confidence = 0.50

    sport_hint = detect_sport_hint(s)
    tags: list[str] = []

    # ── MLB-specific motivation rule (P4) ─────────────────────────────
    # MLB regular-season motivation is mostly noise: every game counts
    # the same in a 162-game schedule. If the editorial mentions
    # "necesita ganar"-style motivation for MLB, we DOWNGRADE it to a
    # neutral tag so the engine doesn't over-weight narrative.
    if chosen == "MOTIVATION_NOTE" and sport_hint == "baseball":
        # Detect explicit playoff/postseason language; if absent, treat
        # the motivation as neutral.
        is_playoff = bool(re.search(
            r"\b(playoff|postseason|postemporada|wildcard|comod[ií]n|"
            r"world\s+series|series\s+mundial|"
            r"divisional|alcs|nlcs|alds|nlds)\b",
            s, re.IGNORECASE,
        ))
        if not is_playoff:
            tags.append("MLB_NORMAL_MOTIVATION_NEUTRAL")
            # Soft demotion: keep MOTIVATION_NOTE but lower confidence
            # so downstream code can treat it as informational only.
            confidence = min(confidence, 0.50)

    return {
        "signal_type":  chosen,
        "confidence":   confidence,
        "matched":      matched,
        "is_negated":   is_negated,
        "sport_hint":   sport_hint,
        "tags":         tags,
    }


def extract_signals_from_text(text: str, *, max_signals: int = 25) -> list[dict]:
    """Sentence-split a raw editorial blob and classify every sentence.

    Output items:
        {
            "text":          str,
            "signal_type":   str,
            "confidence":    float,
            "matched":       list[str],
            "is_negated":    bool,
        }
    """
    if not text:
        return []
    sentences = _sentence_split(text)[:max_signals]
    out: list[dict] = []
    for s in sentences:
        cl = classify_signal(s)
        out.append({"text": s, **cl})
    return out


# ── Score prediction extractor ───────────────────────────────────────────
_SCORE_RE = re.compile(r"\b(\d)\s*[-:\u2013]\s*(\d)\b")


def extract_predicted_score(text: str) -> Optional[str]:
    """Return the FIRST plausible scoreline mentioned in the text, or None.

    Looks only within sentences that also contain a prediction verb to avoid
    matching arbitrary scores from H2H history.
    """
    if not text:
        return None
    for sent in _sentence_split(text):
        norm = _strip_accents(sent.lower())
        if any(k in norm for k in ("marcador", "prediccion", "predict", "resultado", "final")):
            m = _SCORE_RE.search(sent)
            if m:
                return f"{m.group(1)}-{m.group(2)}"
    return None


# ── Market suggestion extractor ──────────────────────────────────────────
_MARKET_HINTS = [
    (r"\bdoble\s+oportunidad\b", "Doble Oportunidad"),
    (r"\bno\s+pierde\b",         "No Pierde"),
    # Over/Under MUST be a float (0.5/1.5/2.5/3.5/etc) — integer-only like
    # "Más de 10 partidos" produces false positives so we require the
    # decimal point. This is the canonical betting format anyway.
    (r"\bover\s+(\d+[.,]5)\s*(?:goles?|corners?|c[oó]rner(?:es|s)?|tarjetas?)?\b", "Over {0}"),
    (r"\bm[aá]s\s+de\s+(\d+[.,]5)\s*(?:goles?|corners?|c[oó]rner(?:es|s)?|tarjetas?)\b", "Más de {0}"),
    (r"\bunder\s+(\d+[.,]5)\s*(?:goles?|corners?|c[oó]rner(?:es|s)?|tarjetas?)?\b", "Under {0}"),
    (r"\bmenos\s+de\s+(\d+[.,]5)\s*(?:goles?|corners?|c[oó]rner(?:es|s)?|tarjetas?)\b", "Menos de {0}"),
    (r"\bambos\s+equipos\s+marcan\b", "BTTS"),
    (r"\bbtts\b", "BTTS"),
    # 1X2 / direct outcome markets (common in AS.com previews)
    (r"\btip\s+principal\s*:\s*victoria\s+de\s+([\w\s\u00C0-\u017F\-]{3,40}?)(?=[,\.\(]|$)", "Victoria {0}"),
    (r"\brecomendamos?\s+victoria\s+de\s+([\w\s\u00C0-\u017F\-]{3,40}?)(?=[,\.\(]|$)", "Victoria {0}"),
    (r"\bvictoria\s+local\b", "Victoria local (1X2)"),
    (r"\bvictoria\s+visitante\b", "Victoria visitante (1X2)"),
    (r"\bh[aá]nd[ie]cap\s+as[ií]atico\b", "Hándicap Asiático"),
    (r"\bh[aá]nd[ie]cap\b", "Hándicap"),
]

_ODDS_RE = re.compile(r"(?:cuota\s+|@\s*)(\d+[.,]\d+)", re.IGNORECASE)


def extract_market_suggestion(text: str) -> Optional[dict]:
    """Return {'market': str, 'odds': float|None} or None."""
    if not text:
        return None
    for pattern, label in _MARKET_HINTS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                market = label.format(*m.groups()) if m.groups() else label
            except IndexError:
                market = label
            odds: Optional[float] = None
            o = _ODDS_RE.search(text)
            if o:
                try:
                    odds = float(o.group(1).replace(",", "."))
                    if not (1.01 <= odds <= 30.0):
                        odds = None
                except (ValueError, TypeError):
                    odds = None
            return {"market": market, "odds": odds}
    return None


__all__ = [
    "SIGNAL_TYPES",
    "classify_signal",
    "extract_signals_from_text",
    "extract_predicted_score",
    "extract_market_suggestion",
    "detect_sport_hint",
]
