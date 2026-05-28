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
    `[{"bookmaker":..., "lines":{"Over 3.5": 1.45, "Under 3.5": 2.75}}]`."""
    best = None
    target = line.strip()
    for r in rows or []:
        lines = r.get("lines") or {}
        if isinstance(lines, dict):
            # Try several key formats
            for k, v in lines.items():
                if not isinstance(v, (int, float)) or v <= 1.01:
                    continue
                k_str = str(k).strip()
                # Exact match or contains the line value
                if k_str == target or target in k_str:
                    if best is None or v > best:
                        best = float(v)
    return best


def _football_extract_protected_odds(markets: dict) -> dict[str, float]:
    """De los markets, extrae odds disponibles para los candidatos football protegidos.

    Returns: { "Under 3.5": 1.85, "Over 1.5": 1.30, "1X": 1.40, ... }
    """
    out: dict[str, float] = {}

    # Over/Under
    ou_rows = markets.get("Over/Under") or []
    for line in ["3.5", "4.5", "1.5", "2.5"]:
        # Try "Under X.X" then "Over X.X"
        for side_label in [f"Under {line}", f"Over {line}"]:
            o = _find_line_odds(ou_rows, side_label)
            if o:
                out[side_label] = o

    # Double Chance
    dc_rows = markets.get("Double Chance") or []
    for sel in ("Home/Draw", "Draw/Away", "Home/Away", "1X", "X2", "12"):
        o = _best_odds_from_market_list(dc_rows, sel)
        if o:
            out[sel] = o
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


__all__ = [
    "attempt_alternative_market_rescue",
]
