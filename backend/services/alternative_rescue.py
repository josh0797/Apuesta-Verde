"""Alternative Market Rescue Layer — Capa de rescate de mercados alternativos.

Se ejecuta DESPUÉS de que la capa Moneyball ha clasificado los picks y ha
movido los descartes a `summary.discarded_market`. Su misión es:

  Antes de descartar definitivamente un partido, buscar mercados
  alternativos PROTEGIDOS que sí puedan tener valor real, incluso
  cuando los mercados directos (Moneyline, 1X2, Spread) no lo tienen.

Flujo:
  1. Para cada match descartado, leer odds_snapshots[-1].markets.
  2. Probar candidatos protegidos por deporte:
      - football:    Under 3.5, Over 1.5, Doble Oportunidad, AH +1.0
      - basketball:  Over/Under puntos totales, Spread alternativo amplio
      - baseball:    Run Line ±1.5/±3.0, Total Runs Over/Under conservador
  3. Para cada candidato:
      - clasificar mercado (debería caer en PROTECTED)
      - estimar edge usando la confianza original del pick descartado
      - pasar por contextual_edge_decision
      - si la classification es PROTECTED_ACCEPTABLE / VALUE_BET / WATCHLIST → keep
  4. Devolver el MEJOR candidato (mayor edge), enriquecido con:
      - whyDirectMarketsFailed
      - whyThisMarketIsSafer
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import market_tolerance as mt
from . import moneyball_layer as mb

log = logging.getLogger("rescue")


# ── Candidatos protegidos por deporte ───────────────────────────────────────
# Cada candidato es (market_name, selection_template, line_value_or_none).
# Las selecciones / líneas se resuelven contra `odds_snapshots[-1].markets`.

FOOTBALL_PROTECTED_CANDIDATES = [
    {"market": "Under 3.5",     "selection": "Under",     "source": "Over/Under", "line": "3.5"},
    {"market": "Under 4.5",     "selection": "Under",     "source": "Over/Under", "line": "4.5"},
    {"market": "Over 1.5",      "selection": "Over",      "source": "Over/Under", "line": "1.5"},
    {"market": "Doble Oportunidad", "selection": "1X",    "source": "Double Chance", "line": "Home/Draw"},
    {"market": "Doble Oportunidad", "selection": "X2",    "source": "Double Chance", "line": "Draw/Away"},
    {"market": "Doble Oportunidad", "selection": "12",    "source": "Double Chance", "line": "Home/Away"},
]

BASKETBALL_PROTECTED_CANDIDATES = [
    {"market": "Total", "selection": "Over",  "source": "Total", "line": "auto_low"},
    {"market": "Total", "selection": "Under", "source": "Total", "line": "auto_high"},
]

BASEBALL_PROTECTED_CANDIDATES = [
    {"market": "Run Line +1.5", "selection": "+1.5", "source": "Spread", "line": "+1.5"},
    {"market": "Run Line +3.0", "selection": "+3.0", "source": "Spread", "line": "+3.0"},
    {"market": "Total Runs",    "selection": "Under","source": "Total",  "line": "auto_high"},
]


# ── Phase F74-post — aliases de mercado por proveedor ──────────────────────
# Diferentes proveedores (API-Sports, OddsPortal, Forebet, etc.) usan
# nombres distintos para los mismos mercados. Centralizamos los alias.
OVER_UNDER_ALIASES: list[str] = [
    "Over/Under",
    "Goals Over/Under",
    "Total Goals",
    "Totals",
    "Total",
    "Match Goals",
    "Goles Totales",
    "Over Under",
    "Over_Under",
    "Total de goles",
]

DOUBLE_CHANCE_ALIASES: list[str] = [
    "Double Chance",
    "Doble Oportunidad",
    "Double chance",
    "1X2 Double Chance",
    "doble oportunidad",
    "DC",
]

# Selección DC: normalisar variantes ES/EN a la convención canónica
# (``1X`` / ``X2`` / ``12``).
DOUBLE_CHANCE_SELECTION_ALIASES: dict[str, str] = {
    "Home/Draw":           "1X",
    "Draw/Away":           "X2",
    "Home/Away":           "12",
    "Local/Empate":        "1X",
    "Empate/Visitante":    "X2",
    "Local/Visitante":     "12",
    "home_draw":           "1X",
    "draw_away":           "X2",
    "home_away":           "12",
}


def get_market_rows_by_alias(markets: dict, aliases: list[str]) -> list[dict]:
    """Devuelve las filas de bookmaker para el primer alias que matchee.

    Es **case-insensitive** y normaliza acentos (NFD). Si ningún alias
    matchea, devuelve lista vacía (fail-soft).
    """
    if not isinstance(markets, dict) or not markets:
        return []
    import unicodedata as _u

    def _norm(s: str) -> str:
        if not isinstance(s, str):
            return ""
        nf = _u.normalize("NFD", s)
        return "".join(c for c in nf if _u.category(c) != "Mn").lower().strip()

    norm_aliases = {_norm(a) for a in aliases}
    for k, v in markets.items():
        if _norm(k) in norm_aliases and isinstance(v, list):
            return v
    return []


# ── Helpers ─────────────────────────────────────────────────────────────────
def _best_odds_from_market_list(rows: list[dict], key: str) -> Optional[float]:
    """Encuentra la mejor cuota (más alta) para una clave dada en filas de bookmaker."""
    best = None
    for r in rows or []:
        v = r.get(key)
        if isinstance(v, (int, float)) and v > 1.01:
            if best is None or v > best:
                best = float(v)
    return best


def _find_line_odds(rows: list[dict], line: str) -> Optional[float]:
    """Encuentra cuota Over/Total para una línea específica en rows tipo
    ``[{"bookmaker":..., "lines":{"Over 3.5": 1.45, "Under 3.5": 2.75}}]``
    o bien (Phase F74-post) ``[{"lines":[{"value":"Más de 1.5","odd":1.30}, ...]}]``.

    Acepta también equivalencias EN/ES (``Over``↔``Más de``,
    ``Under``↔``Menos de``).
    """
    best = None
    target = line.strip()
    # Equivalencias EN↔ES.
    targets = {target}
    for en, es in (("Over", "Más de"), ("Under", "Menos de"),
                    ("Over", "Mas de"), ("Under", "Menos De")):
        if target.startswith(en):
            targets.add(target.replace(en, es, 1))
        if target.startswith(es):
            targets.add(target.replace(es, en, 1))
    for r in rows or []:
        lines = r.get("lines") or {}
        # Pattern A: dict de "Over 3.5" → 1.45
        if isinstance(lines, dict):
            for k, v in lines.items():
                if not isinstance(v, (int, float)) or v <= 1.01:
                    continue
                k_str = str(k).strip()
                if any(t == k_str or t in k_str or k_str in t for t in targets):
                    if best is None or v > best:
                        best = float(v)
        # Pattern B: lista de {"value":"Más de 1.5","odd":1.30}
        elif isinstance(lines, list):
            for ln in lines:
                if not isinstance(ln, dict):
                    continue
                v = ln.get("odd")
                val = str(ln.get("value") or "").strip()
                if not isinstance(v, (int, float)) or v <= 1.01:
                    continue
                if any(t == val or t in val or val in t for t in targets):
                    if best is None or v > best:
                        best = float(v)
    return best


def _football_extract_protected_odds(markets: dict) -> dict[str, float]:
    """De los markets, extrae odds disponibles para los candidatos football protegidos.

    Phase F74-post — soporta aliases de mercado (los proveedores usan
    distintos nombres para el mismo mercado): ``Over/Under``,
    ``Goals Over/Under``, ``Total Goals``, ``Goles Totales``, etc.

    Returns: { "Under 3.5": 1.85, "Over 1.5": 1.30, "1X": 1.40, ... }
    """
    out: dict[str, float] = {}

    # Over/Under — aliases conocidos (ES/EN, varios proveedores)
    ou_rows = get_market_rows_by_alias(markets, OVER_UNDER_ALIASES)
    for line in ["3.5", "4.5", "1.5", "2.5"]:
        # Try "Under X.X" then "Over X.X"
        for side_label in [f"Under {line}", f"Over {line}",
                            f"Menos de {line}", f"Más de {line}"]:
            o = _find_line_odds(ou_rows, side_label)
            if o:
                # Persist under the EN-canonical key so downstream code
                # (which expects Under/Over) keeps working.
                canon = side_label.replace("Menos de", "Under").replace("Más de", "Over")
                out[canon] = o

    # Double Chance — aliases ES/EN
    dc_rows = get_market_rows_by_alias(markets, DOUBLE_CHANCE_ALIASES)
    for sel in (
        "Home/Draw", "Draw/Away", "Home/Away",
        "Local/Empate", "Empate/Visitante", "Local/Visitante",
        "1X", "X2", "12",
    ):
        o = _best_odds_from_market_list(dc_rows, sel)
        if o:
            # Normalise selection name to canonical (1X / X2 / 12).
            canon = DOUBLE_CHANCE_SELECTION_ALIASES.get(sel, sel)
            out[canon] = o
    return out


def _basketball_baseball_extract_total(markets: dict) -> dict[str, float]:
    """Extrae odds Total para basket/baseball. Devuelve dict de líneas."""
    out: dict[str, float] = {}
    total_rows = markets.get("Total") or markets.get("Over/Under") or []
    for r in total_rows or []:
        lines = r.get("lines") or {}
        if isinstance(lines, dict):
            for k, v in lines.items():
                if isinstance(v, (int, float)) and v > 1.01:
                    if k not in out or v > out[k]:
                        out[k] = float(v)
    return out


def _make_synthetic_pick(
    match: dict,
    *,
    market: str,
    selection: str,
    decimal_odds: float,
    base_confidence: int,
) -> dict:
    """Construye un pick sintético consumible por moneyball_layer.analyze_pick."""
    return {
        "match_id":    match.get("match_id"),
        "match_label": f"{(match.get('home_team') or {}).get('name','?')} "
                       f"vs {(match.get('away_team') or {}).get('name','?')}",
        "recommendation": {
            "market":           market,
            "selection":        selection,
            "odds_range":       f"{decimal_odds:.2f}-{decimal_odds:.2f}",
            "confidence_score": base_confidence,
        },
        "reasoning":   "Rescate de mercado protegido tras descarte de mercados directos.",
        "risks":       [],
        "is_live":     False,
        "key_data":    {},
    }


def attempt_alternative_market_rescue(
    match: dict,
    sport: str,
    *,
    base_confidence: int = 65,
    why_direct_failed: Optional[str] = None,
    original_pick_side: Optional[str] = None,
) -> Optional[dict]:
    """Intenta rescatar un partido descartado encontrando un mercado protegido.

    **IMPORTANTE — Direccionalidad**: en esta versión v1 solo se rescatan
    mercados de TOTALES (no direccionales) — Under X.Y, Over 1.5,
    Total Runs Under, Total Points Under. Esto evita el bug de "invertir
    el pick" (ej. rescatar X2 cuando el LLM apoyaba Home Win).

    Mercados direccionales como Doble Oportunidad (1X, X2), Asian Handicap
    o Run Line solo se considerarán si se pasa `original_pick_side`
    ("home" o "away") indicando qué lado apoyaba el LLM original.

    Args:
        match: doc completo del partido (con `odds_snapshots`, `home_team`, etc.)
        sport: "football" | "basketball" | "baseball"
        base_confidence: confianza base a usar al evaluar el mercado alternativo.
                         Por defecto 65 (conservador).
        why_direct_failed: texto explicativo del descarte original.
        original_pick_side: "home" | "away" | None — habilita rescate
                            direccional (Doble Op, AH, Run Line ±) solo
                            hacia el mismo lado del pick original.

    Returns:
        None si no hay mercado rescatable, o el dict de rescate.
    """
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    if not markets:
        return None

    if sport == "football":
        # ── Path A (preferido): delegar al motor especializado de Under ──
        # `scan_protected_alternatives` ya combina Poisson (statsbomb_features)
        # + H2H Bayesian shrinkage + tactical/fragility, lo cual es mucho más
        # preciso que aplicar una confidence genérica a Under/Over.
        # Solo lo intentamos para fútbol porque ese motor es football-only.
        try:
            from . import under_market_scan as ums  # local import (cycles)
            ums_out = ums.scan_protected_alternatives(
                match,
                tactical_score=base_confidence,  # confidence ≈ tactical hint
                fragility_score=50,
            )
        except Exception as exc:
            log.debug("rescue: scan_protected_alternatives failed: %s", exc)
            ums_out = None

        if ums_out and ums_out.get("state") in (
            "PROTECTED_MARKET_RECOMMENDED",
            "UNDER35_WATCHLIST",
            "UNDER25_WATCHLIST",
        ):
            routed_to = (
                "rescued_picks"
                if ums_out["state"] == "PROTECTED_MARKET_RECOMMENDED"
                else "watchlist"
            )
            # P2A — Expose home/away historical_goal_profile in the rescue
            # payload so the UI can render a transparent "Tendencia últimos
            # 15 partidos" section per team (under_rate, failed_to_score>2
            # rate, trend_summary). This is what made the engine confident
            # enough to rescue — show it explicitly to the user.
            home_hgp = (((match.get("home_team") or {}).get("context") or {})
                        .get("recent_fixtures") or {}).get("historical_goal_profile") or {}
            away_hgp = (((match.get("away_team") or {}).get("context") or {})
                        .get("recent_fixtures") or {}).get("historical_goal_profile") or {}
            historical_profile_summary: dict | None = None
            if home_hgp or away_hgp:
                historical_profile_summary = {
                    "home": {
                        "team":                     (match.get("home_team") or {}).get("name"),
                        "matches_analyzed":         home_hgp.get("matches_analyzed"),
                        "goals_for_avg":            home_hgp.get("goals_for_avg"),
                        "under_3_5_rate":           home_hgp.get("under_3_5_rate"),
                        "under_2_5_rate":           home_hgp.get("under_2_5_rate"),
                        "team_exceeded_2_goals_rate": home_hgp.get("team_exceeded_2_goals_rate"),
                        "failed_to_score_over_2_rate": home_hgp.get("failed_to_score_over_2_rate"),
                        "trend_summary":            home_hgp.get("trend_summary"),
                    } if home_hgp else None,
                    "away": {
                        "team":                     (match.get("away_team") or {}).get("name"),
                        "matches_analyzed":         away_hgp.get("matches_analyzed"),
                        "goals_for_avg":            away_hgp.get("goals_for_avg"),
                        "under_3_5_rate":           away_hgp.get("under_3_5_rate"),
                        "under_2_5_rate":           away_hgp.get("under_2_5_rate"),
                        "team_exceeded_2_goals_rate": away_hgp.get("team_exceeded_2_goals_rate"),
                        "failed_to_score_over_2_rate": away_hgp.get("failed_to_score_over_2_rate"),
                        "trend_summary":            away_hgp.get("trend_summary"),
                    } if away_hgp else None,
                }
            return {
                "rescued":         ums_out["state"] == "PROTECTED_MARKET_RECOMMENDED",
                "rescueType":      "GOAL_MARKET",
                "routed_to":       routed_to,
                "market":          ums_out.get("market"),
                "selection":       ums_out.get("selection"),
                "decimal_odds":    ums_out.get("decimal_odds"),
                "edge":            ums_out.get("edge"),
                "tolerance_used":  mt.CATEGORY_PROTECTED,
                "market_category": mt.CATEGORY_PROTECTED,
                "fragility_score": ums_out.get("fragility_score"),
                "confidence":      base_confidence,
                "classification":  (
                    "PROTECTED_ACCEPTABLE"
                    if ums_out["state"] == "PROTECTED_MARKET_RECOMMENDED"
                    else "WATCHLIST"
                ),
                "reason":          " ; ".join(ums_out.get("reasons") or []) or "Mercado protegido respaldado por modelo Poisson + H2H.",
                "estimated_probability": ums_out.get("estimated_probability"),
                "implied_probability":   ums_out.get("implied_probability"),
                "profile_score":         ums_out.get("profile_score"),
                "h2h_under_rate":        ums_out.get("h2h_under_rate"),
                "statsbomb_features":    ums_out.get("statsbomb_features"),
                "historical_profile":    historical_profile_summary,
                "whyDirectMarketsFailed": (
                    why_direct_failed
                    or "Mercados directos (Moneyline / 1X2) sin edge real frente al modelo."
                ),
                "whyThisMarketIsSafer": _why_safer_explanation(
                    ums_out.get("market") or "", ums_out.get("selection") or "",
                    sport, {"market_category": mt.CATEGORY_PROTECTED,
                            "fragility": {"score": ums_out.get("fragility_score")}},
                ),
                "_source": "scan_protected_alternatives_v1",
            }

        # ── Path B (NEW): Corner Market Rescue Layer ────────────────────
        # Si el motor de goles no rescató, probamos el mercado de córners.
        # Solo se activa cuando _corner_form fue pre-cargado por el caller
        # (analyst_engine Phase 10a).
        try:
            from . import corner_market_layer as _cml
            corner_out = _cml.find_corner_value(
                match,
                why_direct_failed=why_direct_failed,
            )
        except Exception as exc:
            log.debug("rescue: corner_market_layer failed: %s", exc)
            corner_out = None
        if corner_out:
            return corner_out

        # Football: ningún path encontró rescate
        return None

    # ── Path B (basketball / baseball): construir candidatos directos ──
    # Construir lista de (market, selection, decimal_odds, directional_side)
    # directional_side = None | "home" | "away"
    candidates: list[tuple[str, str, float, Optional[str]]] = []

    if sport == "basketball":
        # ── Path B1 (NEW): Basketball Pace & Scoring rescue ────────────
        # Si el caller pre-cargó _basketball_pace_form, probarlo primero.
        try:
            from . import basketball_pace_layer as _bpl
            bpl_out = _bpl.find_basketball_pace_value(
                match,
                why_direct_failed=why_direct_failed,
            )
        except Exception as exc:
            log.debug("rescue: basketball_pace_layer failed: %s", exc)
            bpl_out = None
        if bpl_out:
            # Enrich with historical trap signals + raw historical profile so
            # the UI can render the "Historial profundo" panel alongside the
            # rescue pick.
            try:
                from .historical_enrichment import (
                    collect_basketball_trap_signals,
                    compute_extra_fragility,
                )
                signals = collect_basketball_trap_signals(
                    match,
                    bookmaker_total_line=(bpl_out.get("metrics") or {}).get("leagueAvgTotal"),
                )
                if signals:
                    bpl_out["trap_signals_structured"] = (
                        list(bpl_out.get("trap_signals_structured") or []) + signals
                    )
                    bpl_out["fragility_score"] = min(
                        100,
                        int(bpl_out.get("fragility_score") or 0)
                        + compute_extra_fragility(signals),
                    )
                prof = match.get("basketballHistoricalProfile")
                if prof:
                    bpl_out["basketballHistoricalProfile"] = prof
            except Exception as exc:
                log.debug("rescue: basketball trap enrichment failed: %s", exc)
            return bpl_out
        # Fall through to legacy total-line cascade if pace layer didn't trigger
        totals = _basketball_baseball_extract_total(markets)
        for line_key, o in totals.items():
            if "Over" in line_key:
                candidates.append((line_key, "Over", o, None))
            elif "Under" in line_key:
                candidates.append((line_key, "Under", o, None))
    elif sport == "baseball":
        # ── Path B2 (NEW): Baseball Runs rescue from historical profile ─
        # Cuando el caller pre-cargó `baseballHistoricalProfile` (vía
        # `prefetch_baseball_profiles`), intentar primero el motor de
        # runs/F5/team-total/run-line basado en últimos 10-15 juegos.
        try:
            from . import baseball_runs_rescue as _brr
            brr_out = _brr.find_baseball_runs_value(
                match,
                why_direct_failed=why_direct_failed,
            )
        except Exception as exc:
            log.debug("rescue: baseball_runs_rescue failed: %s", exc)
            brr_out = None
        if brr_out:
            try:
                from .historical_enrichment import (
                    collect_baseball_trap_signals,
                    compute_baseball_extra_fragility,
                )
                signals = collect_baseball_trap_signals(
                    match,
                    bookmaker_total_line=(brr_out.get("metrics") or {}).get("bookmaker_total_line"),
                )
                if signals:
                    brr_out["trap_signals_structured"] = (
                        list(brr_out.get("trap_signals_structured") or []) + signals
                    )
                    brr_out["fragility_score"] = min(
                        100,
                        int(brr_out.get("fragility_score") or 0)
                        + compute_baseball_extra_fragility(signals),
                    )
                prof = match.get("baseballHistoricalProfile")
                if prof:
                    brr_out["baseballHistoricalProfile"] = prof
            except Exception as exc:
                log.debug("rescue: baseball trap enrichment failed: %s", exc)
            return brr_out
        # Fall through to legacy Run Line / Total cascade if runs layer didn't trigger.
        # Spreads direccionales (Run Line)
        spread_rows = markets.get("Spread") or []
        for r in spread_rows:
            lines = r.get("lines") or []
            if isinstance(lines, list):
                for ln in lines:
                    val = ln.get("value")
                    odd = ln.get("odd")
                    if isinstance(odd, (int, float)) and odd > 1.01 and val:
                        # Run Line +1.5 home / +1.5 away — direccional
                        # Por convención API-Sports, '+' significa underdog cubierto.
                        # Sin más info no sabemos el lado — pedimos hint del caller.
                        if str(val) in ("+1.5", "+3.0", "+1", "+3"):
                            candidates.append(
                                (f"Run Line {val}", str(val), float(odd),
                                 original_pick_side or "home"),
                            )
        totals = _basketball_baseball_extract_total(markets)
        for line_key, o in totals.items():
            if "Under" in line_key:
                candidates.append((f"Total Runs {line_key}", "Under", o, None))

    if not candidates:
        return None

    # ── Guardrails ──
    # 1. SOLO categoría PROTECTED.
    # 2. Direccionales solo si coinciden con original_pick_side.
    best_rescue: Optional[dict] = None
    best_edge = float("-inf")
    for market_name, selection, odds, dir_side in candidates:
        # Filtro direccional
        if dir_side is not None and original_pick_side is not None:
            if dir_side != original_pick_side:
                continue
        elif dir_side is not None and original_pick_side is None:
            # Direccional pero no sabemos el side → skip por seguridad
            continue

        cat = mt.classify_market_tolerance(market_name, selection, decimal_odds=odds)
        if not mt.is_protected(cat):
            continue
        synthetic_pick = _make_synthetic_pick(
            match,
            market=market_name,
            selection=selection,
            decimal_odds=odds,
            base_confidence=base_confidence,
        )
        try:
            result = mb.analyze_pick(synthetic_pick, sport=sport)
        except Exception as exc:
            log.debug("rescue: analyze_pick failed for %s/%s: %s", market_name, selection, exc)
            continue
        me  = result.get("_market_edge") or {}
        mbp = result.get("_moneyball")   or {}
        edge = me.get("edge")
        cls  = mbp.get("classification")
        if cls not in (
            "VALUE_BET", "STRONG_VALUE_BET", "UNDERVALUED_EDGE",
            "PROTECTED_ACCEPTABLE", "WATCHLIST",
        ):
            continue
        if edge is None:
            continue
        if edge > best_edge:
            best_edge = edge
            best_rescue = {
                "rescued":         True,
                "market":          market_name,
                "selection":       selection,
                "decimal_odds":    odds,
                "edge":            edge,
                "tolerance_used":  mbp.get("tolerance_used"),
                "market_category": mbp.get("market_category"),
                "fragility_score": (mbp.get("fragility") or {}).get("score"),
                "confidence":      base_confidence,
                "classification":  cls,
                "reason":          mbp.get("classification_reason"),
                "whyDirectMarketsFailed": (
                    why_direct_failed
                    or "Mercados directos (Moneyline / 1X2 / Spread principal) sin edge real frente al modelo."
                ),
                "whyThisMarketIsSafer": _why_safer_explanation(
                    market_name, selection, sport, mbp,
                ),
                "_market_edge":   me,
                "_moneyball":     mbp,
                "_synthetic_pick": synthetic_pick,
            }

    if best_rescue is None:
        return None

    # Si la mejor opción es watchlist, no la promovemos a "rescue" — la
    # caller debe ponerla en summary.watchlist en su lugar.
    if best_rescue["classification"] == "WATCHLIST":
        best_rescue["rescued"] = False
        best_rescue["routed_to"] = "watchlist"
    else:
        best_rescue["routed_to"] = "rescued_picks"

    return best_rescue


def _why_safer_explanation(
    market: str,
    selection: str,
    sport: str,
    moneyball_payload: dict,
) -> str:
    """Genera explicación humana de por qué este mercado es más seguro.

    CRÍTICO: el texto debe ser sport-aware. Baseball NUNCA debe decir
    "goles" o "córners". Basketball NUNCA debe decir "goles". Football usa
    su vocabulario natural.
    """
    market_l = (market or "").lower()
    cat      = moneyball_payload.get("market_category")
    frag     = (moneyball_payload.get("fragility") or {}).get("score", 50)

    # ── Baseball — siempre lenguaje MLB ──
    if sport == "baseball":
        if "run line" in market_l and "+" in (selection or ""):
            return (
                f"Run Line {selection} permite que el equipo pierda por hasta {selection[1:]} "
                f"carreras y aún así cubrir. Más resistente a explosiones aisladas del rival "
                f"(rallies de bullpen, jonrones puntuales)."
            )
        if "total runs" in market_l or "total" in market_l:
            side = "Under" if "under" in market_l or "menos" in (selection or "").lower() else "Over"
            return (
                f"Total Runs {side} cubre el escenario sin importar el ganador del partido. "
                f"Ideal cuando hay incertidumbre sobre quién gana pero buena lectura del "
                f"matchup de pitchers/bullpen."
            )
        if "team total" in market_l:
            return (
                f"Team Total ({selection}) aísla el rendimiento ofensivo de un solo equipo, "
                f"sin depender del resultado final del partido."
            )
        # Fallback baseball
        return (
            f"Mercado protegido de baseball ({cat}) con fragilidad {frag}/100. "
            f"Menor dependencia del resultado puntual del partido."
        )

    # ── Basketball — lenguaje hoops ──
    if sport == "basketball":
        if "total" in market_l and ("over" in market_l or "under" in market_l):
            side = "Over" if "over" in market_l else "Under"
            return (
                f"Total Points {side} captura el ritmo global del partido sin necesidad de "
                f"acertar al ganador. Cubre cualquier diferencia de marcador dentro de la línea."
            )
        if "spread" in market_l:
            return (
                f"Spread alternativo ({selection}) absorbe rachas puntuales del rival. "
                f"Más robusto frente a runs ofensivos cortos que un Moneyline directo."
            )
        if "team total" in market_l:
            return (
                f"Team Total ({selection}) mide solo la producción ofensiva de un equipo "
                f"— independiente del resultado final."
            )
        return (
            f"Mercado protegido de baloncesto ({cat}) con fragilidad {frag}/100. "
            f"Cobertura sobre el ritmo o el spread, no sobre el ganador exacto."
        )

    # ── Football — lenguaje fútbol (default histórico) ──
    if "under" in market_l and "3.5" in market_l:
        return ("Under 3.5 goles cubre todos los partidos con ≤3 goles: una franja amplia. "
                "Si la lectura del partido apunta a ritmo bajo o defensas dominando, "
                "esta cobertura es estadísticamente más robusta que el ganador.")
    if "under" in market_l and "4.5" in market_l:
        return ("Under 4.5 goles cubre prácticamente cualquier partido salvo goleadas. "
                "Riesgo mínimo cuando no hay señales claras de festival ofensivo.")
    if "over 1.5" in market_l:
        return ("Over 1.5 goles solo exige 2 goles en el partido — escenario muy probable "
                "salvo en duelos extremadamente cerrados.")
    if "doble" in market_l or "double chance" in market_l:
        return (f"Doble Oportunidad ({selection}) cubre dos de los tres resultados posibles. "
                "Pierdes valor frente a Moneyline directo pero ganas amplitud de cobertura.")
    return (f"Mercado protegido de fútbol ({cat}) con fragilidad {frag}/100. "
            f"Menor dependencia de un único evento decisivo.")


# ─────────────────────────────────────────────────────────────────────
# Phase P2 — infer_original_pick_side (4 fuentes en cascada)
# ─────────────────────────────────────────────────────────────────────
#
# Antes de F-P2, ``attempt_alternative_market_rescue`` recibía
# ``original_pick_side=None`` por defecto, así que TODOS los rescates
# direccionales (Doble Op 1X / X2, AH, Run Line ±) se descartaban por
# guardrail de seguridad. Esto era correcto pero perdía rescates
# legítimos cuando el lado original era inferible.
#
# La función inferencial intenta 4 fuentes en orden, devolviendo el
# primer hit confiable. Si ninguna fuente da señal clara, devuelve
# ``None`` (el comportamiento conservador previo).


def _infer_side_from_recommendation(entry: dict, home_name: str,
                                     away_name: str) -> Optional[str]:
    """Source 1 — recommendation.selection escrita por el LLM.

    El generador ya escribe `recommendation.selection` con strings
    expandidos como "Manchester City gana", "Real Madrid o Empate",
    "Liverpool -1.5". Buscamos tokens directos o nombres de equipo.
    """
    if not isinstance(entry, dict):
        return None
    rec = entry.get("recommendation") or {}
    if not isinstance(rec, dict):
        return None
    raw_sel = rec.get("selection")
    if not raw_sel or not isinstance(raw_sel, str):
        return None
    sel = raw_sel.strip().lower()

    # 1.a — tokens cortos 1X2.
    compact = sel.replace(" ", "").upper()
    if compact in ("1", "1X", "HOME") or compact.startswith("HOME"):
        return "home"
    if compact in ("2", "X2", "AWAY") or compact.startswith("AWAY"):
        return "away"

    # 1.b — nombres de equipo expandidos.
    if home_name and home_name.lower() in sel:
        return "home"
    if away_name and away_name.lower() in sel:
        return "away"

    # 1.c — spread prefix "home -1.5" / "away +1.5".
    if sel.startswith(("home ", "local ", "h ")):
        return "home"
    if sel.startswith(("away ", "visit", "v ", "visitor")):
        return "away"

    return None


def _infer_side_from_forebet(match: dict) -> Optional[str]:
    """Source 2 — Forebet predicted score / winner.

    Forebet writes ``predicted_score`` like "2-1" (home-away) and
    ``predicted_winner`` like "home" / "away" / "draw" depending on
    the ingestion path. Both shapes are tolerated.
    """
    if not isinstance(match, dict):
        return None
    # Try the canonical editorial enrichment first.
    ed = (match.get("football_data_enrichment") or {}).get("editorial") or {}
    fb = ed.get("forebet") or match.get("forebet") or {}
    if not isinstance(fb, dict):
        return None

    winner = fb.get("predicted_winner") or fb.get("winner")
    if isinstance(winner, str):
        w = winner.strip().lower()
        if w in ("home", "1", "local"):
            return "home"
        if w in ("away", "2", "visitor", "visitante"):
            return "away"
        if w in ("draw", "x", "tie", "empate"):
            return None  # explicit draw → no directional rescue

    score = fb.get("predicted_score")
    if isinstance(score, str) and "-" in score:
        try:
            h, a = score.split("-", 1)
            h_i, a_i = int(h.strip()), int(a.strip())
            if h_i > a_i:
                return "home"
            if a_i > h_i:
                return "away"
        except (ValueError, TypeError):
            pass
    return None


def _infer_side_from_odds(match: dict) -> Optional[str]:
    """Source 3 — Match-winner odds favourite.

    Looks at the latest odds_snapshots for the Match Winner / Moneyline
    market and picks the side with the lowest price (the implicit
    favourite). Requires a clear gap (≥10% difference) to avoid noisy
    50/50 matches.
    """
    if not isinstance(match, dict):
        return None
    snaps = match.get("odds_snapshots") or []
    if not snaps:
        return None
    markets = (snaps[-1] or {}).get("markets") or {}
    # API-Sports → "Match Winner" rows; TheStatsAPI mirror → "Moneyline".
    candidate_rows = []
    for key in ("Match Winner", "Moneyline", "1X2"):
        rows = markets.get(key) or []
        if isinstance(rows, list):
            candidate_rows.extend(rows)

    home_odd: Optional[float] = None
    away_odd: Optional[float] = None
    for r in candidate_rows:
        val = (r.get("value") or "").strip().lower()
        odd = r.get("odd")
        if not isinstance(odd, (int, float)) or odd < 1.01:
            continue
        if val in ("home", "1", "local"):
            home_odd = float(odd) if home_odd is None else min(home_odd, float(odd))
        elif val in ("away", "2", "visitor", "visitante"):
            away_odd = float(odd) if away_odd is None else min(away_odd, float(odd))

    if home_odd is None or away_odd is None:
        return None
    # Require a clear edge — at least 10% difference between the two
    # legs to call a favourite. Otherwise the match is roughly even and
    # a directional rescue would be too risky.
    gap = abs(home_odd - away_odd) / max(home_odd, away_odd)
    if gap < 0.10:
        return None
    return "home" if home_odd < away_odd else "away"


def _infer_side_from_thestatsapi_edge(entry: dict, match: dict) -> Optional[str]:
    """Source 4 — TheStatsAPI edge.

    The structural enrichment writes ``_market_edge`` on the pick and a
    ``thestatsapi`` block on the match. If either records a directional
    edge (home_edge / away_edge or a verdict containing 'home'/'away'),
    we use it as a last-resort hint.
    """
    if isinstance(entry, dict):
        edge = entry.get("_market_edge") or {}
        if isinstance(edge, dict):
            side = edge.get("side") or edge.get("favoured_side")
            if isinstance(side, str):
                s = side.strip().lower()
                if s in ("home", "away"):
                    return s
            verdict = edge.get("verdict") or ""
            if isinstance(verdict, str):
                vl = verdict.lower()
                if "home" in vl and "away" not in vl:
                    return "home"
                if "away" in vl and "home" not in vl:
                    return "away"

    if isinstance(match, dict):
        tsa = (match.get("football_data_enrichment") or {}).get("thestatsapi") or {}
        if isinstance(tsa, dict):
            he = tsa.get("home_edge") or tsa.get("home_value")
            ae = tsa.get("away_edge") or tsa.get("away_value")
            if isinstance(he, (int, float)) and isinstance(ae, (int, float)):
                if abs(he - ae) >= 0.02:  # need a meaningful gap (≥2 pts)
                    return "home" if he > ae else "away"
    return None


def infer_original_pick_side(match: dict,
                              entry: Optional[dict] = None) -> Optional[str]:
    """Infer the directional side of the LLM's original pick.

    Strategy — first non-null answer wins:
      1. ``entry.recommendation.selection``       — explicit LLM choice.
      2. ``match.football_data_enrichment.editorial.forebet`` — Forebet
         predicted_winner / predicted_score.
      3. Match Winner odds favourite                — implicit favourite.
      4. TheStatsAPI directional edge              — structural lean.

    Returns ``"home"``, ``"away"``, or ``None`` when no source can give
    a confident answer. ``None`` keeps the legacy conservative behaviour
    (skip directional rescues).
    """
    if not isinstance(match, dict):
        return None
    entry = entry if isinstance(entry, dict) else {}
    home_name = ((match.get("home_team") or {}).get("name")
                 if isinstance(match.get("home_team"), dict)
                 else match.get("home_team") or "")
    away_name = ((match.get("away_team") or {}).get("name")
                 if isinstance(match.get("away_team"), dict)
                 else match.get("away_team") or "")

    for fn, label in (
        (_infer_side_from_recommendation, "recommendation"),
        (_infer_side_from_forebet,        "forebet"),
        (_infer_side_from_odds,           "odds_favourite"),
        (_infer_side_from_thestatsapi_edge, "thestatsapi_edge"),
    ):
        try:
            if fn is _infer_side_from_recommendation:
                side = fn(entry, home_name, away_name)
            elif fn is _infer_side_from_thestatsapi_edge:
                side = fn(entry, match)
            else:
                side = fn(match)
        except Exception as exc:  # noqa: BLE001
            log.debug("infer_original_pick_side: source=%s crashed: %s", label, exc)
            side = None
        if side in ("home", "away"):
            log.debug("infer_original_pick_side → side=%s via source=%s", side, label)
            return side
    return None


__all__ = [
    "attempt_alternative_market_rescue",
    "infer_original_pick_side",
]
