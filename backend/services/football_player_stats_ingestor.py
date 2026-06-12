"""Football Player Stats Ingestor — Phase F58.

Hydrates **per-90 player rate stats** used by the Football Player Props
Discovery layer. Two-stage hybrid pipeline:

    Primary:  StatMuse  (HTML scraping, fail-soft, cached)
    Fallback: Understat (uses :mod:`services.understat_scraper`)

Output (siempre el mismo shape)
-------------------------------
``hydrate_player_stats(...)`` retorna SIEMPRE un dict con la forma::

    {
        "available":          bool,
        "source":             "statmuse" | "understat" | "unavailable",
        "confidence_penalty": int,            # 0 si fuente primaria; >0 si degradada
        "minutes_sample":     int | None,
        "stats": {
            "shots_p90":   float | None,
            "sot_p90":     float | None,
            "passes_p90":  float | None,
            "tackles_p90": float | None,
            "fouls_p90":   float | None,
            "cards_p90":   float | None,
            "xg_p90":      float | None,
            "minutes_p_game": float | None,
        },
        "raw": {...optional debug...},
    }

Fail-soft contract
------------------
* Cualquier excepción → ``{available: False, source: "unavailable",
  confidence_penalty: 0, stats: {}, ...}``.  El caller debe degradar
  graciosamente (Discovery devuelve lista vacía o skip por jugador).
* In-memory cache: TTL **6h** keyed por ``(player_name_norm, league_norm)``.

Diseño
------
* Pure-Python, sin dependencias nuevas.
* `requests`-free en imports: usa ``services.external_sources.base``
  (httpx + Bright Data) cuando el scraping HTTP es necesario.
* Sub-modulares: ``_fetch_statmuse_player(...)`` y
  ``_fetch_understat_player(...)`` se pueden testear por separado con
  mocks. Para los smoke tests vamos a inyectar fakers vía monkeypatch.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from typing import Any, Optional

log = logging.getLogger("football_player_stats_ingestor")

ENGINE_VERSION = "football_player_stats_ingestor.v1"

# ── Cache ────────────────────────────────────────────────────────────
_CACHE_TTL = timedelta(hours=6)
_CACHE: dict[str, tuple[datetime, dict]] = {}

# ── Confidence penalties por fuente ──────────────────────────────────
PENALTY_PRIMARY  = 0
PENALTY_FALLBACK = 8
PENALTY_NO_SAMPLE = 12  # source ok pero minutes_sample insuficiente
MIN_MINUTES_SAMPLE = 270  # 3 partidos completos como piso

# ── Output skeleton ──────────────────────────────────────────────────
_EMPTY_STATS = {
    "shots_p90":   None,
    "sot_p90":     None,
    "passes_p90":  None,
    "tackles_p90": None,
    "fouls_p90":   None,
    "cards_p90":   None,
    "xg_p90":      None,
    "minutes_p_game": None,
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _cache_key(player_name: str, league: Optional[str]) -> str:
    return f"{_norm(player_name)}|{_norm(league)}"


def _cache_get(key: str) -> Optional[dict]:
    hit = _CACHE.get(key)
    if not hit:
        return None
    exp, val = hit
    if datetime.now(timezone.utc) > exp:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, value: dict) -> None:
    _CACHE[key] = (datetime.now(timezone.utc) + _CACHE_TTL, value)


def cache_clear() -> None:
    """Test helper — drops the in-memory cache."""
    _CACHE.clear()


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", ""))
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _build_empty(reason: str = "no_source") -> dict:
    return {
        "available":          False,
        "source":             "unavailable",
        "confidence_penalty": 0,
        "minutes_sample":     None,
        "stats":              dict(_EMPTY_STATS),
        "raw":                {"_reason": reason},
        "engine_version":     ENGINE_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────
# StatMuse player parser (primary)
# ─────────────────────────────────────────────────────────────────────
class _StatMusePlayerTableParser(HTMLParser):
    """Mini parser que captura la primera tabla de la respuesta StatMuse
    para una pregunta tipo "shots per 90 for {player} last season".

    Reutiliza el patrón de ``statmuse_recent_form._StatMuseTableParser``
    pero local para mantener desacoplado el módulo.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._row: list[str] = []
        self._cell: list[str] = []
        self._is_header_row = False
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._captured = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self._captured:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._row = []
            self._is_header_row = False
        elif self._in_table and tag == "th":
            self._in_cell = True
            self._cell = []
            self._is_header_row = True
        elif self._in_table and tag == "td":
            self._in_cell = True
            self._cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._in_cell:
            self._row.append("".join(self._cell).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._is_header_row and not self.headers:
                self.headers = list(self._row)
            elif self._row:
                self.rows.append(self._row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            self._in_table = False
            self._captured = True


# Map común de aliases → claves canónicas
_STATMUSE_ALIASES = {
    "shots":          "shots",
    "sh":             "shots",
    "sot":            "sot",
    "shots on goal":  "sot",
    "shots on target": "sot",
    "passes":         "passes",
    "pass":           "passes",
    "tackles":        "tackles",
    "tkl":            "tackles",
    "fouls":          "fouls",
    "f":              "fouls",
    "cards":          "cards",
    "yc":             "cards",
    "yellow cards":   "cards",
    "xg":             "xg",
    "min":            "minutes",
    "minutes":        "minutes",
    "mp":             "matches",
    "matches":        "matches",
}


def _parse_statmuse_player_html(html: str) -> dict:
    """Parsea HTML de StatMuse para extraer las stats por-90 del jugador.

    Soporta dos formatos comunes:
      a) Tabla con una fila resumen del jugador (season totals + per90).
      b) Tabla con varias filas por temporada (tomamos la más reciente
         con minutos > MIN_MINUTES_SAMPLE).

    Si no se puede extraer nada → dict vacío (caller maneja fail-soft).
    """
    parser = _StatMusePlayerTableParser()
    try:
        parser.feed(html or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse player parser failure: %s", exc)
        return {}
    if not parser.headers or not parser.rows:
        return {}

    headers_lc = [h.strip().lower() for h in parser.headers]
    # Mapping header_index → canonical_key
    col_map: dict[int, str] = {}
    for i, h in enumerate(headers_lc):
        if h in _STATMUSE_ALIASES:
            col_map[i] = _STATMUSE_ALIASES[h]

    if not col_map:
        return {}

    # Buscamos la fila con MAYOR cantidad de minutos (mejor sample).
    best_row: Optional[list[str]] = None
    best_minutes: float = -1.0
    for row in parser.rows:
        minutes_idx = next((i for i, c in col_map.items() if c == "minutes"), None)
        minutes_val = _safe_float(row[minutes_idx]) if minutes_idx is not None and minutes_idx < len(row) else None
        if minutes_val is not None and minutes_val > best_minutes:
            best_minutes = minutes_val
            best_row = row
        elif best_row is None:
            best_row = row

    if not best_row:
        return {}

    extracted: dict[str, Any] = {}
    for idx, key in col_map.items():
        if idx >= len(best_row):
            continue
        extracted[key] = _safe_float(best_row[idx])
    return extracted


def _to_per90(totals: dict) -> dict:
    """Convierte totales temporada → tasas per-90.

    Si StatMuse ya devuelve la métrica per-90 directamente, el valor
    pasará sin alteración (heurística: si minutes < 90, asumimos que ya
    son tasas).
    """
    minutes = _safe_float(totals.get("minutes"))
    matches = _safe_float(totals.get("matches"))
    out = dict(_EMPTY_STATS)
    minutes_sample: Optional[int] = None

    # Si tenemos minutos → calculamos per-90.
    if minutes and minutes >= 90:
        scale = 90.0 / minutes
        if "shots" in totals and totals["shots"] is not None:
            out["shots_p90"]   = round(totals["shots"]   * scale, 3)
        if "sot" in totals and totals["sot"] is not None:
            out["sot_p90"]     = round(totals["sot"]     * scale, 3)
        if "passes" in totals and totals["passes"] is not None:
            out["passes_p90"]  = round(totals["passes"]  * scale, 3)
        if "tackles" in totals and totals["tackles"] is not None:
            out["tackles_p90"] = round(totals["tackles"] * scale, 3)
        if "fouls" in totals and totals["fouls"] is not None:
            out["fouls_p90"]   = round(totals["fouls"]   * scale, 3)
        if "cards" in totals and totals["cards"] is not None:
            out["cards_p90"]   = round(totals["cards"]   * scale, 3)
        if "xg" in totals and totals["xg"] is not None:
            out["xg_p90"]      = round(totals["xg"]      * scale, 3)
        minutes_sample = int(minutes)
        if matches and matches > 0:
            out["minutes_p_game"] = round(minutes / matches, 1)
    else:
        # Suponemos que ya vienen como per-90.
        for k_in, k_out in (
            ("shots", "shots_p90"), ("sot", "sot_p90"),
            ("passes", "passes_p90"), ("tackles", "tackles_p90"),
            ("fouls", "fouls_p90"), ("cards", "cards_p90"),
            ("xg", "xg_p90"),
        ):
            if k_in in totals and totals[k_in] is not None:
                out[k_out] = round(totals[k_in], 3)
        if minutes is not None:
            minutes_sample = int(minutes)

    return {"stats": out, "minutes_sample": minutes_sample}


# ─────────────────────────────────────────────────────────────────────
# StatMuse primary fetch (fail-soft)
# ─────────────────────────────────────────────────────────────────────
async def _fetch_statmuse_player_html(player_name: str) -> Optional[str]:
    """Hace GET al endpoint StatMuse para una pregunta del jugador.

    Convierte el nombre en un slug url-safe y consulta una pregunta
    pre-diseñada que renderiza una tabla con totales/per90 de la temporada.
    Fail-soft: devuelve None si no hay conexión, brightdata no está, etc.
    """
    try:
        # Late import para no romper cuando external_sources no esté listo
        # en entornos de testing aislado.
        from services.external_sources.base import (
            brightdata_fetch, brightdata_available, direct_fetch,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse external_sources unavailable: %s", exc)
        return None

    slug = re.sub(r"[^a-z0-9]+", "-", player_name.lower()).strip("-")
    if not slug:
        return None
    url = f"https://www.statmuse.com/fc/ask/{slug}-stats-this-season"
    try:
        if brightdata_available():
            html = await brightdata_fetch(url, country="us", timeout_sec=15.0)
            if html:
                return html
        return await direct_fetch(
            url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout_sec=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse fetch failed for %s: %s", player_name, exc)
        return None


async def _fetch_statmuse_player(player_name: str) -> Optional[dict]:
    """Devuelve dict normalizado del jugador desde StatMuse o ``None``.

    Retorna ``None`` en cualquier fallo (parsing, http, sin datos).
    """
    html = await _fetch_statmuse_player_html(player_name)
    if not html:
        return None
    totals = _parse_statmuse_player_html(html)
    if not totals:
        return None
    normalized = _to_per90(totals)
    if not any(v is not None for v in normalized["stats"].values()):
        return None
    return {
        "source":         "statmuse",
        "minutes_sample": normalized["minutes_sample"],
        "stats":          normalized["stats"],
        "raw":            {"totals": totals},
    }


# ─────────────────────────────────────────────────────────────────────
# Understat fallback (xG por jugador) — fail-soft
# ─────────────────────────────────────────────────────────────────────
async def _fetch_understat_player(player_name: str, league: Optional[str]) -> Optional[dict]:
    """Intenta hidratar xg_p90 (y opcionalmente shots/sot) desde Understat.

    Implementación MVP: usa ``find_player_xg_in_recent_matches`` si está
    disponible en el módulo Understat; si no, retorna ``None``.
    """
    try:
        from services import understat_scraper as us  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log.debug("understat module unavailable: %s", exc)
        return None

    # API minima — usamos un helper soft. Si no existe, fail-soft.
    helper = getattr(us, "find_player_season_aggregates", None)
    if not callable(helper):
        return None
    try:
        agg = helper(player_name=player_name, league=league)
    except Exception as exc:  # noqa: BLE001
        log.debug("understat find_player_season_aggregates failed: %s", exc)
        return None
    if not isinstance(agg, dict) or not agg:
        return None

    minutes = _safe_float(agg.get("minutes"))
    matches = _safe_float(agg.get("matches"))
    out = dict(_EMPTY_STATS)
    if minutes and minutes >= 90:
        scale = 90.0 / minutes
        xg = _safe_float(agg.get("xg"))
        shots = _safe_float(agg.get("shots"))
        sot = _safe_float(agg.get("sot")) or _safe_float(agg.get("shots_on_target"))
        if xg is not None:
            out["xg_p90"] = round(xg * scale, 3)
        if shots is not None:
            out["shots_p90"] = round(shots * scale, 3)
        if sot is not None:
            out["sot_p90"] = round(sot * scale, 3)
        if matches:
            out["minutes_p_game"] = round(minutes / matches, 1)
    else:
        # Si vienen ya como per-90.
        for k_in, k_out in (("xg", "xg_p90"), ("shots", "shots_p90"), ("sot", "sot_p90")):
            v = _safe_float(agg.get(k_in))
            if v is not None:
                out[k_out] = round(v, 3)

    if not any(v is not None for v in out.values()):
        return None
    return {
        "source":         "understat",
        "minutes_sample": int(minutes) if minutes else None,
        "stats":          out,
        "raw":            {"understat": agg},
    }


# ─────────────────────────────────────────────────────────────────────
# FBref scraper (tertiary fallback) — fail-soft
# ─────────────────────────────────────────────────────────────────────
# FBref structure
# ----------------
# Step 1: search ``/en/search/search.fcgi?search={name}`` → parse the
#         first result link to the player page (HTML pattern:
#         ``<a href="/en/players/{hash}/{slug}">{Name}</a>``).
# Step 2: GET the player page; extract the **Standard Stats** table
#         (table id="stats_standard_*" or h2 "Standard Stats" + sibling
#         table). Read the season totals row matching the most recent
#         "Premier League" / domestic league season.
#         FBref tables encode the season row with a `data-stat`
#         attribute on each `<td>` so the parser uses that as the
#         canonical key — much more robust than column index.
#
# All errors are caught and converted to ``None``. The hydrator chain
# treats this as the third try after StatMuse / Understat.


class _FBrefPlayerLinkParser(HTMLParser):
    """Find the first ``/en/players/<hash>/<slug>`` link in the search
    results page. FBref's search renders the matches as a list of
    ``<div class="search-item-name"><a href="...">...</a></div>`` items.
    """

    def __init__(self) -> None:
        super().__init__()
        self.first_player_href: Optional[str] = None
        self._capture_next_link = False

    def handle_starttag(self, tag, attrs):
        if self.first_player_href:
            return
        if tag == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href") or ""
            # Match canonical player paths only.
            if href.startswith("/en/players/") and "/scout/" not in href:
                # Skip URLs with extra path segments deeper than 3 (we
                # want the canonical "/en/players/{hash}/{slug}" page).
                parts = [p for p in href.split("/") if p]
                if len(parts) == 4 and parts[0] == "en" and parts[1] == "players":
                    self.first_player_href = href


class _FBrefStatsTableParser(HTMLParser):
    """Capture rows of FBref's "Standard Stats" table.

    FBref encodes each ``<td>`` with a ``data-stat`` attribute (e.g.
    ``goals``, ``shots``, ``shots_on_target``, ``passes``,
    ``tackles``, ``fouls``, ``cards_yellow``, ``minutes``, ``games``,
    ``xg``). We collect (data_stat → value) mappings for each row,
    along with the row's ``comp`` (competition) so the caller can pick
    the relevant league season.
    """

    def __init__(self) -> None:
        super().__init__()
        self._depth_in_table = 0
        self._in_row = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._current_stat_key: Optional[str] = None
        self._row: dict[str, str] = {}
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "table":
            tbl_id = (ad.get("id") or "").lower()
            if tbl_id.startswith("stats_standard"):
                self._depth_in_table = 1
            return
        if self._depth_in_table <= 0:
            return
        if tag == "tr":
            self._in_row = True
            self._row = {}
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell_buf = []
            self._current_stat_key = ad.get("data-stat")

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_cell:
            text = "".join(self._cell_buf).strip()
            if self._current_stat_key:
                self._row[self._current_stat_key] = text
            self._in_cell = False
            self._current_stat_key = None
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
        elif tag == "table" and self._depth_in_table > 0:
            self._depth_in_table = 0


def _parse_fbref_player_link(html: str) -> Optional[str]:
    if not html:
        return None
    parser = _FBrefPlayerLinkParser()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        log.debug("fbref link parser failure: %s", exc)
        return None
    return parser.first_player_href


def _parse_fbref_standard_stats(html: str) -> Optional[dict]:
    """Parse FBref's Standard Stats table and pick the best season row.

    "Best" = highest minutes, where ``comp`` matches a domestic league
    name (Premier League / La Liga / Bundesliga / Serie A / Ligue 1)
    or the row is the first one. Falls back to "any season with
    minutes > 0" if no league match.
    """
    if not html:
        return None
    parser = _FBrefStatsTableParser()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        log.debug("fbref standard-stats parser failure: %s", exc)
        return None
    if not parser.rows:
        return None

    DOMESTIC = ("Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
                "Eredivisie", "Primeira Liga", "MLS")

    best: Optional[dict[str, str]] = None
    best_minutes = -1.0
    for row in parser.rows:
        minutes_str = row.get("minutes") or row.get("Min")
        minutes = _safe_float(minutes_str.replace(",", "") if isinstance(minutes_str, str) else minutes_str)
        if minutes is None or minutes <= 0:
            continue
        comp = row.get("comp_level") or row.get("comp") or ""
        is_domestic = any(d.lower() in comp.lower() for d in DOMESTIC)
        # Prefer domestic-league rows; otherwise prefer highest-minutes.
        if is_domestic and minutes > best_minutes:
            best = row
            best_minutes = minutes
        elif best is None and minutes > best_minutes:
            best = row
            best_minutes = minutes

    if not best:
        return None

    def _g(key: str) -> Optional[float]:
        v = best.get(key)
        if v is None or v == "":
            return None
        return _safe_float(v.replace(",", "") if isinstance(v, str) else v)

    return {
        "shots":    _g("shots"),
        "sot":      _g("shots_on_target"),
        "passes":   _g("passes_completed") or _g("passes"),
        "tackles":  _g("tackles"),
        "fouls":    _g("fouls"),
        "cards":    _g("cards_yellow"),
        "xg":       _g("xg"),
        "minutes":  _g("minutes"),
        "matches":  _g("games"),
        "_comp":    best.get("comp_level") or best.get("comp"),
    }


async def _fetch_fbref_html(url: str, timeout_sec: float = 10.0) -> Optional[str]:
    """Polite GET to FBref. Tries Bright Data first when available."""
    try:
        from services.external_sources.base import (
            brightdata_fetch, brightdata_available, direct_fetch,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("fbref external_sources unavailable: %s", exc)
        return None
    try:
        if brightdata_available():
            html = await brightdata_fetch(url, country="us", timeout_sec=timeout_sec + 5)
            if html:
                return html
        return await direct_fetch(
            url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout_sec=timeout_sec,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("fbref fetch failed for %s: %s", url, exc)
        return None


async def _fetch_fbref_player(player_name: str) -> Optional[dict]:
    """Search FBref, follow first hit, extract standard stats. Fail-soft.

    Returns ``{source: "fbref", minutes_sample, stats: {...per90...}, raw}``
    on success, ``None`` otherwise.
    """
    if not player_name:
        return None
    # Step 1 — search. Bright Data + FBref needs ~30-45s typically.
    search_url = f"https://fbref.com/en/search/search.fcgi?search={player_name.replace(' ', '+')}"
    search_html = await _fetch_fbref_html(search_url, timeout_sec=55.0)
    if not search_html:
        return None
    href = _parse_fbref_player_link(search_html)
    if not href:
        return None
    # Step 2 — player page.
    player_url = f"https://fbref.com{href}"
    player_html = await _fetch_fbref_html(player_url, timeout_sec=55.0)
    if not player_html:
        return None
    totals = _parse_fbref_standard_stats(player_html)
    if not totals:
        return None
    normalized = _to_per90(totals)
    if not any(v is not None for v in normalized["stats"].values()):
        return None
    return {
        "source":         "fbref",
        "minutes_sample": normalized["minutes_sample"],
        "stats":          normalized["stats"],
        "raw":            {"totals": totals, "player_url": player_url},
    }


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def hydrate_player_stats(
    *,
    player_name: str,
    team: Optional[str] = None,
    league: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """Hidrata las stats per-90 del jugador en cadena:

        StatMuse (primario) → FBref (enriquecimiento) → Understat (xG fallback)

    Si StatMuse devuelve datos parciales (e.g. solo ``shots_p90`` / ``sot_p90``),
    FBref **complementa** los campos faltantes (``passes``, ``tackles``,
    ``fouls``, ``cards``, ``xg``). Understat actúa como último recurso solo
    cuando StatMuse y FBref fallan completamente.

    Siempre devuelve un dict con la forma documentada arriba. Nunca
    levanta excepciones (fail-soft).
    """
    if not player_name or not isinstance(player_name, str):
        return _build_empty("invalid_player_name")

    ck = _cache_key(player_name, league)
    if use_cache:
        cached = _cache_get(ck)
        if cached is not None:
            return cached

    # ── 1. Primary: StatMuse
    try:
        primary = await _fetch_statmuse_player(player_name)
    except Exception as exc:  # noqa: BLE001
        log.debug("statmuse primary fetch raised: %s", exc)
        primary = None

    # ── 2. FBref (enrichment / fallback)
    fbref = None
    primary_partial = bool(
        primary and any(v is None for v in primary["stats"].values())
    )
    try_fbref = (primary is None) or primary_partial
    if try_fbref:
        try:
            fbref = await _fetch_fbref_player(player_name)
        except Exception as exc:  # noqa: BLE001
            log.debug("fbref fetch raised: %s", exc)
            fbref = None

    # ── 3. Understat (xG-only last resort)
    understat = None
    if primary is None and fbref is None:
        try:
            understat = await _fetch_understat_player(player_name, league)
        except Exception as exc:  # noqa: BLE001
            log.debug("understat fallback fetch raised: %s", exc)
            understat = None

    # ── Merge logic
    payload: Optional[dict] = None
    if primary and fbref:
        # Merge: prefer StatMuse where available, fill nulls from FBref.
        merged_stats = dict(primary["stats"])
        for k, v in (fbref["stats"] or {}).items():
            if merged_stats.get(k) is None and v is not None:
                merged_stats[k] = v
        # Minutes_sample: take the larger of the two as authoritative.
        m1 = primary.get("minutes_sample") or 0
        m2 = fbref.get("minutes_sample") or 0
        minutes_sample = max(m1, m2) or None
        penalty = PENALTY_PRIMARY if (minutes_sample or 0) >= MIN_MINUTES_SAMPLE else PENALTY_NO_SAMPLE
        payload = {
            "available":          True,
            "source":             "statmuse+fbref",
            "confidence_penalty": penalty,
            "minutes_sample":     minutes_sample,
            "stats":              merged_stats,
            "raw":                {
                "statmuse": primary.get("raw") or {},
                "fbref":    fbref.get("raw") or {},
            },
            "engine_version":     ENGINE_VERSION,
        }
    elif primary:
        minutes_sample = primary.get("minutes_sample") or 0
        penalty = PENALTY_PRIMARY if minutes_sample >= MIN_MINUTES_SAMPLE else PENALTY_NO_SAMPLE
        payload = {
            "available":          True,
            "source":             "statmuse",
            "confidence_penalty": penalty,
            "minutes_sample":     primary.get("minutes_sample"),
            "stats":              primary["stats"],
            "raw":                primary.get("raw") or {},
            "engine_version":     ENGINE_VERSION,
        }
    elif fbref:
        minutes_sample = fbref.get("minutes_sample") or 0
        penalty = PENALTY_FALLBACK if minutes_sample >= MIN_MINUTES_SAMPLE else PENALTY_NO_SAMPLE
        payload = {
            "available":          True,
            "source":             "fbref",
            "confidence_penalty": penalty,
            "minutes_sample":     fbref.get("minutes_sample"),
            "stats":              fbref["stats"],
            "raw":                fbref.get("raw") or {},
            "engine_version":     ENGINE_VERSION,
        }
    elif understat:
        minutes_sample = understat.get("minutes_sample") or 0
        base_pen = PENALTY_FALLBACK
        if minutes_sample < MIN_MINUTES_SAMPLE:
            base_pen = max(base_pen, PENALTY_NO_SAMPLE)
        payload = {
            "available":          True,
            "source":             "understat",
            "confidence_penalty": base_pen,
            "minutes_sample":     understat.get("minutes_sample"),
            "stats":              understat["stats"],
            "raw":                understat.get("raw") or {},
            "engine_version":     ENGINE_VERSION,
        }

    if payload is None:
        payload = _build_empty("all_sources_failed")

    # Cachear (incluso payloads vacíos, para no martillar fuentes muertas).
    _cache_set(ck, payload)
    return payload


__all__ = [
    "ENGINE_VERSION",
    "PENALTY_PRIMARY",
    "PENALTY_FALLBACK",
    "PENALTY_NO_SAMPLE",
    "MIN_MINUTES_SAMPLE",
    "cache_clear",
    "hydrate_player_stats",
    # Exposed for monkeypatching in tests:
    "_fetch_statmuse_player",
    "_fetch_understat_player",
    "_fetch_fbref_player",
    "_parse_statmuse_player_html",
    "_parse_fbref_player_link",
    "_parse_fbref_standard_stats",
    "_to_per90",
]
