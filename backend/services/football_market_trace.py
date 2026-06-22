"""Football Market Trace — explicit per-market audit for discarded picks.

Resuelve el problema reportado por el usuario:
    > Cuando un partido de fútbol es descartado, la UI muestra explicaciones
    > genéricas como "Cuota baja (<1.40)" o "Mercado protegido con edge
    > -12.9%". El usuario NO puede determinar:
    >   - qué mercado fue evaluado
    >   - de qué equipo
    >   - qué cuota se usó
    >   - por qué se rechazó.

Este módulo produce, para cada pick descartado, un objeto explícito::

    market_trace = {
        "market":                 "Doble Oportunidad",
        "selection":              "PSG or Draw",
        "market_code":            "1X",
        "team_side":              "home" | "away" | null,
        "odds":                   1.33,
        "estimated_probability":  0.71,   # del engine (0..1)
        "implied_probability":    0.75,   # 1/odds (0..1)
        "edge":                   -0.04,  # est - implied (signed fraction)
        "edge_pct":               -4.0,   # cosmético; edge * 100
        "fragility":              12,
        "confidence":             63,
        "rejection_reason":       "Low edge — implied 75% > estimated 71%",
        "rejection_code":         "LOW_EDGE" | "PROTECTED_BELOW_FLOOR" | ...
        "fragility_drivers":      [str, ...],
    }

Adicionalmente expone ``markets_checked`` por partido — la lista de
mercados que fueron evaluados (mercado principal + alternativas) con su
estado ``status ∈ {"selected_for_review", "rejected"}`` para que la UI
pueda renderizar la "Auditoría completa".

Funciones puras (sin IO).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

log = logging.getLogger("football_market_trace")


# ════════════════════════════════════════════════════════════════════════════
# Constants — short mnemonic codes for football markets so the UI can
# show e.g. "PSG Doble Oportunidad (1X)" without re-deriving the code.
# ════════════════════════════════════════════════════════════════════════════
_MARKET_CODE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"doble\s+oportunidad|double\s+chance", re.I), "1X|X2|12"),
    (re.compile(r"ambos\s+equipos\s+anotan.*no|btts.*no", re.I), "BTTS-NO"),
    (re.compile(r"ambos\s+equipos\s+anotan.*s[ií]|btts.*yes", re.I), "BTTS-YES"),
    (re.compile(r"h[áa]ndicap\s+asi[áa]tico", re.I),           "AH"),
    (re.compile(r"over\s+(\d+(?:\.\d+)?)", re.I),               "OVER"),
    (re.compile(r"under\s+(\d+(?:\.\d+)?)", re.I),              "UNDER"),
    (re.compile(r"c[óo]rner", re.I),                            "CORNERS"),
    (re.compile(r"resultado\s+exacto|correct\s+score", re.I),   "CS"),
    (re.compile(r"moneyline|gana", re.I),                       "ML"),
    (re.compile(r"draw\s+no\s+bet", re.I),                      "DNB"),
    (re.compile(r"team\s+total", re.I),                         "TT"),
]


def _detect_market_code(market_label: str) -> str:
    """Best-effort short code for a football market label."""
    if not market_label:
        return "UNKNOWN"
    for pattern, code in _MARKET_CODE_RULES:
        m = pattern.search(market_label)
        if m:
            # Append the line for Over/Under and Asian Handicap when available.
            if code in ("OVER", "UNDER") and m.groups():
                return f"{code}-{m.group(1)}"
            return code
    return market_label.strip().upper()[:24] or "UNKNOWN"


def _detect_team_side(selection: str, match_label: str) -> Optional[str]:
    """Try to associate the selection with home/away based on `match_label`
    (format: "Home vs Away" / "Home @ Away" / "Home - Away").
    """
    if not selection or not match_label:
        return None
    sep_match = re.split(r"\s+(?:vs\.?|@|-)\s+", match_label, maxsplit=1)
    if len(sep_match) != 2:
        return None
    home, away = sep_match[0].strip(), sep_match[1].strip()
    sel_lower = selection.lower()
    if home and home.lower() in sel_lower:
        return "home"
    if away and away.lower() in sel_lower:
        return "away"
    return None


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _parse_midpoint_odds(odds_range: Any) -> Optional[float]:
    """Tolerant midpoint parser for odds ranges expressed as strings/numbers.

    Accepts: 1.33 · "1.33" · "1.30 – 1.40" · "1.30 - 1.40" · "1.30/1.40" ·
    {"min": 1.30, "max": 1.40} · {"value": 1.33}.
    """
    if odds_range is None:
        return None
    if isinstance(odds_range, (int, float)):
        return float(odds_range) if odds_range > 0 else None
    if isinstance(odds_range, dict):
        for k in ("midpoint", "value", "mid", "decimal"):
            if k in odds_range and odds_range[k] is not None:
                v = _f(odds_range[k])
                if v > 0:
                    return v
        if "min" in odds_range and "max" in odds_range:
            lo, hi = _f(odds_range["min"]), _f(odds_range["max"])
            if lo > 0 and hi >= lo:
                return round((lo + hi) / 2.0, 3)
        return None
    s = str(odds_range)
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if not nums:
        return None
    vals = [float(n) for n in nums if float(n) > 0]
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    return round(sum(vals) / len(vals), 3)


# ════════════════════════════════════════════════════════════════════════════
# Rejection-code derivation — turn the moneyball `classification` /
# `reason` into a stable, short code the UI can switch on.
# ════════════════════════════════════════════════════════════════════════════
_REJECTION_CODE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"piso\s+de\s+tolerancia", re.I),         "PROTECTED_BELOW_FLOOR"),
    (re.compile(r"piso\s+aceptable",       re.I),         "EDGE_BELOW_NEG_FLOOR"),
    (re.compile(r"umbral",                 re.I),         "EDGE_BELOW_MIN"),
    (re.compile(r"fragilidad\s+muy\s+alta",re.I),         "FRAGILITY_TOO_HIGH"),
    (re.compile(r"fragilidad\s+alta",      re.I),         "FRAGILITY_HIGH"),
    (re.compile(r"se[ñn]al(?:es)?\s+trampa",re.I),        "TRAP_SIGNALS"),
    (re.compile(r"public\s+overreaction|sobre[-\s]?reacc",re.I), "PUBLIC_OVERREACTION"),
    (re.compile(r"market\s*trap|mercado\s+trampa",re.I),  "MARKET_TRAP"),
    (re.compile(r"cuota\s+baja",           re.I),         "LOW_ODDS_NO_CUSHION"),
    (re.compile(r"sin\s+valor|no\s+value", re.I),         "NO_VALUE"),
    # F99-P0 (Fase 6 follow-up) — patrones REALES vistos en runtime.
    # El reason típico de un partido en watchlist post-F99 es:
    #   "Cuotas no atractivas y contexto competitivo normal."
    # Antes caía al fallback UNKNOWN y disparaba el catch-all
    # UNCLASSIFIED_DISCARD_REQUIRES_AUDIT.  Ahora se clasifica.
    (re.compile(r"cuotas?\s+no\s+atractiv",re.I),         "ODDS_NOT_ATTRACTIVE"),
    (re.compile(r"contexto\s+competitivo\s+normal",re.I), "COMPETITIVE_CONTEXT_NORMAL"),
    (re.compile(r"watchlist[_\s]*insufficient[_\s]*support",re.I),
                                                          "WATCHLIST_INSUFFICIENT_SUPPORT"),
    (re.compile(r"insufficient[_\s]*support",re.I),       "WATCHLIST_INSUFFICIENT_SUPPORT"),
    (re.compile(r"market[_\s]*identity[_\s]*unresolv",re.I),
                                                          "MARKET_IDENTITY_UNRESOLVED"),
]


def _derive_rejection_code(classification: str, reason: str) -> str:
    """Map a moneyball classification + reason into a stable rejection code."""
    cls = (classification or "").upper()
    if cls in ("NO_BET_VALUE",):
        # Look at the reason text for a refined code.
        for rx, code in _REJECTION_CODE_RULES:
            if rx.search(reason or ""):
                return code
        return "NO_VALUE"
    if cls in ("MARKET_TRAP",):
        return "MARKET_TRAP"
    if cls in ("PUBLIC_OVERREACTION",):
        return "PUBLIC_OVERREACTION"
    if cls in ("FRAGILE_EDGE",):
        return "FRAGILITY_HIGH"
    if cls in ("WATCHLIST",):
        return "WATCHLIST_ONLY"
    if reason:
        for rx, code in _REJECTION_CODE_RULES:
            if rx.search(reason):
                return code
    return cls or "UNKNOWN"


def _humanize_rejection_reason(rejection_code: str,
                                edge_pct: Optional[float],
                                fragility: Optional[int],
                                confidence: Optional[int],
                                odds: Optional[float]) -> str:
    """Spanish, user-friendly rejection sentence."""
    e = f"{edge_pct:+.1f}%" if edge_pct is not None else "N/D"
    f = f"{fragility}" if fragility is not None else "N/D"
    c = f"{confidence}" if confidence is not None else "N/D"
    o = f"{odds:.2f}" if odds is not None else "N/D"

    if rejection_code == "LOW_ODDS_NO_CUSHION":
        return (f"La cuota {o} es demasiado baja para compensar la "
                f"fragilidad detectada ({f}/100). Edge {e}.")
    if rejection_code == "PROTECTED_BELOW_FLOOR":
        return (f"Mercado protegido con edge {e} bajo el piso de "
                f"tolerancia. La cuota no compensa el riesgo.")
    if rejection_code == "EDGE_BELOW_NEG_FLOOR":
        return f"Edge {e} bajo el piso aceptable del mercado."
    if rejection_code == "EDGE_BELOW_MIN":
        return f"Edge {e} por debajo del umbral mínimo de valor."
    if rejection_code == "FRAGILITY_TOO_HIGH":
        return (f"Edge {e} real pero fragilidad muy alta ({f}/100). "
                f"Riesgo no aceptable.")
    if rejection_code == "FRAGILITY_HIGH":
        return (f"Edge {e} real pero fragilidad alta ({f}/100). "
                f"Considerar reducir stake o evitar.")
    if rejection_code == "MARKET_TRAP":
        return "Señales de trampa detectadas; el mercado parece engañar."
    if rejection_code == "PUBLIC_OVERREACTION":
        return "Sobre-reacción del público; la cuota se movió fuera de valor."
    if rejection_code == "TRAP_SIGNALS":
        return "Señales trampa estructuradas activas; confianza no fiable."
    if rejection_code == "NO_VALUE":
        return f"No se encontró valor real (edge {e}, confianza {c}/100)."
    if rejection_code == "WATCHLIST_INSUFFICIENT_SUPPORT":
        return ("Watchlist por soporte insuficiente: las señales disponibles "
                f"no alcanzan el umbral mínimo (confianza {c}/100).")
    if rejection_code == "WATCHLIST_ONLY":
        return f"Confianza {c}/100 insuficiente para recomendar; queda en watchlist."
    if rejection_code == "ODDS_NOT_ATTRACTIVE":
        return f"Cuotas {o} no atractivas para el riesgo evaluado."
    if rejection_code == "COMPETITIVE_CONTEXT_NORMAL":
        return "Contexto competitivo normal sin ventaja explotable identificada."
    if rejection_code == "MARKET_IDENTITY_UNRESOLVED":
        return "El motor no pudo identificar el mercado canónico para evaluar valor."
    if rejection_code == "MARKET_IDENTITY_MISSING":
        return "Mercado del pick descartado no se pudo mapear a una identidad conocida."
    if rejection_code == "NO_ODDS_AVAILABLE":
        return ("Sin cuotas disponibles: ninguna de las fuentes consultadas "
                "(Cuotasahora, TheStatsAPI, SofaScore, OddsPortal, manuales) "
                "devolvió mercados. Calidad estadística usable; falta el momio.")
    if rejection_code == "UNCLASSIFIED_DISCARD_REQUIRES_AUDIT":
        return ("Descarte sin causa clasificable — requiere revisión manual del trace.")
    return f"Pick descartado ({rejection_code})."


# ════════════════════════════════════════════════════════════════════════════
# Main builders
# ════════════════════════════════════════════════════════════════════════════
def build_market_trace(pick_or_entry: dict,
                        *,
                        sport: str = "football") -> dict:
    """Build the explicit ``market_trace`` for a single discarded pick.

    Works on either:
      - the full pick dict (with `_moneyball`, `_market_edge`, `recommendation`)
      - a leaner `discarded_market` entry (with `match_label`, `reason`,
        `_moneyball`, `_market_edge`)
    """
    p = pick_or_entry or {}
    rec = p.get("recommendation") or {}
    mb = p.get("_moneyball") or {}
    me = p.get("_market_edge") or {}
    # Sprint-D9-MarketTraceFix · cuando el pick fue descartado SIN llegar
    # a tener una ``recommendation`` (porque el LLM no produjo una o el
    # gate previo lo bloqueó), todavía tenemos el mercado recomendado
    # por el motor moneyball en ``market_selection``. SIN este fallback,
    # la UI renderiza literal "Mercado desconocido / unknown".
    msel = p.get("market_selection") or {}

    market_label  = (
        rec.get("market")
        or p.get("market")
        or me.get("market")
        or msel.get("recommended_market")
        or msel.get("market_name")
        or ""
    )
    selection_lab = (
        rec.get("selection")
        or p.get("selection")
        or me.get("selection")
        or msel.get("selection")
        or ""
    )
    match_label   = p.get("match_label") or ""

    odds = _parse_midpoint_odds(rec.get("odds_range")
                                 or rec.get("odds")
                                 or p.get("odds")
                                 or me.get("odds_used"))
    confidence = p.get("confidence_score") or rec.get("confidence") or mb.get("confidence")
    confidence = int(_f(confidence)) if confidence is not None else None

    # Estimated probability: prefer explicit numeric, otherwise derive from
    # confidence (engine's calibrated belief) capped to [0.05, 0.95].
    est_prob_raw = (
        rec.get("estimated_probability")
        or p.get("estimated_probability")
        or me.get("estimated_probability")
        or me.get("model_probability")
    )
    if est_prob_raw is not None:
        est_prob = _f(est_prob_raw)
        # Tolerate "75" / "0.75" / "75%" inputs.
        if est_prob > 1.5:
            est_prob = est_prob / 100.0
        est_prob = max(0.0, min(1.0, est_prob))
    elif confidence is not None:
        est_prob = max(0.05, min(0.95, confidence / 100.0))
    else:
        est_prob = None

    implied_prob = (1.0 / odds) if (odds and odds > 0) else None
    edge = None
    if est_prob is not None and implied_prob is not None:
        edge = round(est_prob - implied_prob, 4)
    elif me.get("edge") is not None:
        edge = round(_f(me.get("edge")), 4)
    edge_pct = round(edge * 100, 2) if edge is not None else None

    frag = (mb.get("fragility") or {})
    fragility_score = frag.get("score")
    fragility_score = int(_f(fragility_score)) if fragility_score is not None else None
    fragility_drivers = list(frag.get("factors") or [])

    classification = mb.get("classification") or ""
    reason_raw     = mb.get("classification_reason") or p.get("reason") or ""
    rejection_code = _derive_rejection_code(classification, reason_raw)

    # Sprint-D9-followup-2 (Jun-2026) — PRIORITY OVERRIDE: cuando el motor
    # determinó que NO HAY CUOTAS DISPONIBLES en ninguna fuente (estado
    # ``NO_ODDS_AVAILABLE`` del aggregator), NO debemos emitir códigos como
    # ``MARKET_IDENTITY_MISSING``, ``MARKET_TRAP``, ``EDGE_*`` o
    # ``WATCHLIST_*`` que sugieren un análisis estadístico fallido.  El
    # motivo real es **operacional** (no hay datos de mercado, no es un
    # problema del modelo).  Este override va PRIMERO porque tiene
    # precedencia sobre cualquier inferencia downstream.
    odds_status_raw = (
        p.get("odds_status")
        or (p.get("odds_snapshot") or {}).get("state")
        or (p.get("_odds_status"))
    )
    if odds_status_raw == "NO_ODDS_AVAILABLE":
        log.info(
            "[market_trace] odds_status=NO_ODDS_AVAILABLE overriding "
            "rejection_code from %r → NO_ODDS_AVAILABLE",
            rejection_code,
        )
        rejection_code = "NO_ODDS_AVAILABLE"
    elif rejection_code in ("UNKNOWN", "WATCHLIST"):
        # F99-P0 (Fase 6 follow-up) — clasificación específica para
        # "market=Watchlist sin classification ni reason discriminatoria".
        market_lower = (market_label or "").strip().lower()
        if market_lower == "watchlist":
            ms = (p.get("market_selection") or rec.get("market_selection") or {})
            upstream_codes = ms.get("reason_codes") or []
            if any("INSUFFICIENT_SUPPORT" in str(c).upper() for c in upstream_codes):
                rejection_code = "WATCHLIST_INSUFFICIENT_SUPPORT"
            else:
                rejection_code = "WATCHLIST_ONLY"

    rejection_human = _humanize_rejection_reason(
        rejection_code, edge_pct, fragility_score, confidence, odds)

    # Phase F71 — canonical market identity. Lets the UI display a
    # meaningful "Mercado evaluado" line even when the upstream pick
    # only carried odds/edge/probability without explicit market+selection
    # strings. Also drives like-vs-like comparisons (OddsPortal, etc).
    try:
        from services.market_identity import normalize_market_identity
        # Parse home/away from match_label for side resolution.
        home_n, away_n = None, None
        if match_label:
            import re as _re
            parts = _re.split(r"\s+(?:vs\.?|v|-|–|—)\s+",
                              match_label, maxsplit=1,
                              flags=_re.IGNORECASE)
            if len(parts) == 2:
                home_n, away_n = parts[0].strip(), parts[1].strip()
        market_identity = normalize_market_identity(
            {"market":    market_label or rec.get("market_type") or p.get("market_type"),
             "side":      selection_lab,
             "line":      rec.get("line") or p.get("line") or me.get("line"),
             "selection": selection_lab},
            home_name=home_n, away_name=away_n,
        )
    except Exception:  # noqa: BLE001
        market_identity = {"identity_key": "UNKNOWN:RAW:empty",
                            "display":      market_label or "—",
                            "family":       None}

    trace = {
        "market":                 market_label or None,
        "selection":              selection_lab or None,
        "market_code":            _detect_market_code(market_label),
        # Phase F71 — canonical market identity (used by UI + validators)
        "market_identity":        market_identity,
        "market_identity_key":    market_identity.get("identity_key"),
        "market_display":         market_identity.get("display"),
        "team_side":              _detect_team_side(selection_lab, match_label),
        "odds":                   round(odds, 3) if odds else None,
        "estimated_probability":  round(est_prob, 4) if est_prob is not None else None,
        "implied_probability":    round(implied_prob, 4) if implied_prob is not None else None,
        "edge":                   edge,
        "edge_pct":               edge_pct,
        "fragility":              fragility_score,
        "confidence":             confidence,
        "rejection_code":         rejection_code,
        "rejection_reason":       rejection_human,
        "rejection_reason_raw":   reason_raw or None,
        "fragility_drivers":      fragility_drivers[:6],
        "classification":         classification or None,
        "sport":                  sport,
    }

    # Fix B-1 (Sprint-F98.2) — Explicit ``evaluated_market`` block.
    # UI consumers should NEVER render "Mercado desconocido" again when
    # the engine actually identified the market. This block is the
    # canonical contract the UI binds to:
    #   {
    #     "market_family":        "DOUBLE_CHANCE",
    #     "market_name":          "Doble oportunidad",
    #     "selection":            "Canada o empate",
    #     "side":                 "1X",
    #     "line":                 null,
    #     "odds":                 1.25,
    #     "market_identity_key":  "DOUBLE_CHANCE:1X"
    #   }
    # If the identity could NOT be resolved (key starts with UNKNOWN:),
    # ``evaluated_market`` is set to None so the UI knows to render the
    # "Mercado no identificado" state instead of a fake market name.
    identity_key = (market_identity or {}).get("identity_key") or ""
    if identity_key and not identity_key.startswith("UNKNOWN:"):
        trace["evaluated_market"] = {
            "market_family":       market_identity.get("family"),
            "market_name":         market_identity.get("display") or market_label or None,
            "selection":           selection_lab or None,
            "side":                market_identity.get("side"),
            "line":                market_identity.get("line"),
            "odds":                trace["odds"],
            "market_identity_key": identity_key,
        }
    else:
        trace["evaluated_market"] = None
    # ── Phase F73 — Market Identity Guard ─────────────────────────────
    # If we don't actually know the market (family is None or key starts
    # with UNKNOWN:), we MUST NOT classify the pick as MARKET_TRAP,
    # PROTECTED_BELOW_FLOOR, etc. Override classification and surface
    # the F73 state so the UI can route the entry to the new bucket.
    try:
        from services.market_identity_guards import (
            FORBIDDEN_WHEN_IDENTITY_MISSING,
            has_valid_market_identity,
        )
        if not has_valid_market_identity(market_identity):
            orig_code = rejection_code
            forbid = (rejection_code in FORBIDDEN_WHEN_IDENTITY_MISSING
                       or (classification or "").upper()
                          in FORBIDDEN_WHEN_IDENTITY_MISSING)
            if forbid:
                trace["original_rejection_code"]   = orig_code
                trace["original_classification"]    = classification or None
                # Preserve the visible odds so the UI can show "Cuota
                # detectada: 1.25" while the financial numbers are blanked.
                trace["odds_visible"]               = trace.get("odds")
                trace["original_odds"]              = trace.get("odds")
                trace["rejection_code"]             = "MARKET_IDENTITY_MISSING"
                trace["classification"]             = "MARKET_IDENTITY_MISSING"
                trace["state"]                       = "REQUIRES_MARKET_IDENTIFICATION"
                # Fix B-2 (Sprint-F98.2) — When the rejection was driven
                # by a "low odds" heuristic (LOW_ODDS_NO_CUSHION) AND we
                # cannot identify the market, surface the user-binding
                # phrasing requested: "Cuota baja detectada, pero no se
                # puede evaluar trampa sin identificar el mercado exacto."
                visible_odds = trace.get("odds")
                is_low_odds = (
                    orig_code == "LOW_ODDS_NO_CUSHION"
                    or (visible_odds is not None and visible_odds < 1.40)
                )
                if is_low_odds:
                    trace["rejection_reason"] = (
                        f"Cuota baja detectada ({visible_odds if visible_odds is not None else 'N/D'}), "
                        "pero no se puede evaluar trampa sin identificar el "
                        "mercado exacto. Una cuota baja puede ser mala para un "
                        "mercado y aceptable para otro si el modelo estima "
                        "≥90% (ej. Doble Oportunidad, DNB, Over 1.5)."
                    )
                else:
                    trace["rejection_reason"] = (
                        f"Cuota detectada ({visible_odds if visible_odds is not None else 'N/D'}) "
                        "pero no se identificó a qué mercado pertenece. No se puede "
                        "calcular edge ni declarar trampa de mercado hasta "
                        "mapear la cuota a un mercado específico (Doble "
                        "Oportunidad, DNB, 1X2, Over/Under, BTTS, córners, "
                        "hándicap, etc.)."
                    )
                # Blank out edge-related fields to prevent UI from
                # displaying misleading negatives.
                trace["edge"]                  = None
                trace["edge_pct"]              = None
                trace["estimated_probability"] = None
                trace["implied_probability"]   = None
                trace["f73_reason_codes"] = [
                    "MARKET_IDENTITY_MISSING",
                    "EDGE_CALCULATION_BLOCKED_UNKNOWN_MARKET",
                ]
                if orig_code == "LOW_ODDS_NO_CUSHION" or is_low_odds:
                    trace["f73_reason_codes"].append(
                        "LOW_ODDS_TRAP_SUPPRESSED_BY_F73_NO_MARKET_ID"
                    )
                # evaluated_market stays None (UI signals
                # "Mercado no identificado" instead of "Mercado desconocido").
                trace["evaluated_market"] = None
    except Exception:  # noqa: BLE001
        pass
    return trace


def build_markets_checked(pick_or_entry: dict,
                           alternative_markets: Optional[Iterable[str]] = None,
                           *,
                           sport: str = "football",
                           main_trace: Optional[dict] = None) -> list[dict]:
    """Return the list of markets that were evaluated for this match.

    Each item::
        {
          "market":     str,
          "selection":  str | None,
          "status":     "rejected" | "selected_for_review",
          "odds":       float | None,
          "edge_pct":   float | None,
          "confidence": int | None,
          "reason":     str | None,
        }

    The main market (the one that was rejected) is always included first
    with status="rejected" + the original rejection reason. The
    alternative markets are appended with status="selected_for_review"
    (they were not bet by the engine but the user could review them
    manually) and a synthetic note.
    """
    p = pick_or_entry or {}
    main_trace = main_trace or build_market_trace(p, sport=sport)

    main_market = (main_trace or {}).get("market") or "Mercado principal"
    main_entry = {
        "market":     main_market,
        "selection":  main_trace.get("selection"),
        "status":     "rejected",
        "odds":       main_trace.get("odds"),
        "edge_pct":   main_trace.get("edge_pct"),
        "confidence": main_trace.get("confidence"),
        "reason":     main_trace.get("rejection_reason"),
        "rejection_code": main_trace.get("rejection_code"),
    }
    out: list[dict] = [main_entry]

    seen = {(main_market or "").strip().lower()}
    for alt in (alternative_markets or []):
        alt_label = (alt or "").strip()
        if not alt_label:
            continue
        key = alt_label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "market":     alt_label,
            "selection":  None,
            "status":     "selected_for_review",
            "odds":       None,
            "edge_pct":   None,
            "confidence": None,
            "reason":     "Sugerido para revisión manual (alternativa de protección).",
            "rejection_code": None,
        })
    return out


# ════════════════════════════════════════════════════════════════════════════
# Header / summary helpers
# ════════════════════════════════════════════════════════════════════════════
def build_discarded_header(trace: dict) -> str:
    """Human-readable card header.

    Example: "PSG Doble Oportunidad (1X) descartado por edge insuficiente (-12.9%)"

    **F99-P0 (Fase 6) — Prohibición de `unknown` como reason_code visible:**
    si ``rejection_code`` no pertenece al catálogo conocido (lista de ``elif``
    abajo), se emite el código auditable
    ``UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`` con un tag traducido genérico.
    El consumidor (logs estructurados, UI debug) podrá distinguir este
    caso del resto.  **Jamás** se devuelve ``"... descartado por unknown"``.
    """
    t = trace or {}
    sel = t.get("selection") or t.get("market") or "Mercado"
    code = t.get("market_code")
    market = t.get("market") or ""
    edge_pct = t.get("edge_pct")
    raw_rejection_code = t.get("rejection_code")

    # Normalización: si el código viene vacío o como string "UNKNOWN" (default
    # legacy), lo convertimos a `UNCLASSIFIED_DISCARD_REQUIRES_AUDIT`.
    if not raw_rejection_code or str(raw_rejection_code).strip().upper() in (
        "", "UNKNOWN", "NONE", "NULL",
    ):
        rejection_code = "UNCLASSIFIED_DISCARD_REQUIRES_AUDIT"
    else:
        rejection_code = str(raw_rejection_code)

    code_str = f" ({code})" if code and code not in ("UNKNOWN", market.upper()) else ""
    # Choose a short tag.
    if rejection_code in ("EDGE_BELOW_MIN", "EDGE_BELOW_NEG_FLOOR", "NO_VALUE", "PROTECTED_BELOW_FLOOR"):
        tag = (
            f"edge insuficiente ({edge_pct:+.1f}%)"
            if edge_pct is not None else "edge insuficiente"
        )
    elif rejection_code in ("FRAGILITY_TOO_HIGH", "FRAGILITY_HIGH"):
        f = t.get("fragility")
        tag = f"fragilidad elevada ({f}/100)" if f is not None else "fragilidad elevada"
    elif rejection_code == "LOW_ODDS_NO_CUSHION":
        o = t.get("odds")
        tag = f"cuota baja ({o:.2f})" if o else "cuota baja"
    elif rejection_code in ("MARKET_TRAP", "TRAP_SIGNALS"):
        tag = "señales trampa"
    elif rejection_code == "PUBLIC_OVERREACTION":
        tag = "sobre-reacción pública"
    elif rejection_code == "WATCHLIST_ONLY":
        c = t.get("confidence")
        tag = f"confianza insuficiente ({c}/100)" if c is not None else "confianza insuficiente"
    elif rejection_code == "WATCHLIST_INSUFFICIENT_SUPPORT":
        tag = "watchlist por soporte insuficiente"
    elif rejection_code == "ODDS_NOT_ATTRACTIVE":
        tag = "cuotas no atractivas"
    elif rejection_code == "COMPETITIVE_CONTEXT_NORMAL":
        tag = "contexto competitivo normal"
    elif rejection_code == "MARKET_IDENTITY_UNRESOLVED":
        tag = "mercado no identificado"
    elif rejection_code == "MARKET_IDENTITY_MISSING":
        tag = "mercado no identificado"
    elif rejection_code == "NO_ODDS_AVAILABLE":
        tag = "sin cuotas disponibles"
    elif rejection_code == "UNCLASSIFIED_DISCARD_REQUIRES_AUDIT":
        # F99-P0 (Fase 6): tag legible que NO contiene la palabra "unknown".
        tag = "motivo no clasificado (revisión pendiente)"
        log.warning(
            "[football_market_trace] UNCLASSIFIED_DISCARD selection=%r market=%r "
            "raw_rejection_code=%r edge_pct=%r confidence=%r",
            sel, market, raw_rejection_code, edge_pct, t.get("confidence"),
        )
    else:
        # Código conocido pero sin tag dedicado — generamos uno legible
        # SIN producir nunca la cadena "unknown".
        tag = rejection_code.replace("_", " ").lower()

    if market:
        return f"{sel}{code_str} descartado por {tag}"
    return f"{sel} descartado por {tag}"


# ════════════════════════════════════════════════════════════════════════════
# Attach helpers — mutate summary in-place
# ════════════════════════════════════════════════════════════════════════════
_DISCARD_BUCKETS = ("discarded_market", "discarded_motivation", "incomplete_data")


def attach_market_trace_to_summary(summary: dict,
                                    *,
                                    sport: str = "football") -> dict:
    """Iterates every discarded bucket and adds ``market_trace`` +
    ``markets_checked`` + ``card_header`` to each entry.

    Fail-soft (any per-entry crash is swallowed and logged).

    Returns
    -------
    {"annotated": int, "buckets": {bucket_name: count, ...}}
    """
    if not isinstance(summary, dict):
        return {"annotated": 0, "buckets": {}}

    total = 0
    per_bucket: dict[str, int] = {}

    for bucket_key in _DISCARD_BUCKETS:
        bucket = summary.get(bucket_key) or []
        if not isinstance(bucket, list):
            continue
        ok = 0
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            try:
                trace = build_market_trace(entry, sport=sport)
                alts = entry.get("possible_alternative_markets") or []
                checked = build_markets_checked(
                    entry, alts, sport=sport, main_trace=trace)
                header = build_discarded_header(trace)
                entry["market_trace"]   = trace
                entry["markets_checked"] = checked
                entry["card_header"]    = header
                ok += 1
            except Exception as exc:
                log.debug("attach_market_trace_to_summary failed: %s", exc)
                continue
        per_bucket[bucket_key] = ok
        total += ok

    summary["_football_market_audit_attached"] = {
        "version": 1,
        "total":   total,
        "buckets": per_bucket,
        "sport":   sport,
    }
    return {"annotated": total, "buckets": per_bucket}


def build_run_audit_payload(summary: dict,
                             *,
                             sport: str = "football",
                             run_id: Optional[str] = None,
                             user_id: Optional[str] = None) -> dict:
    """Build the payload that will be persisted to MongoDB collection
    ``football_market_audit`` so the user can query historic per-day
    market audits later.
    """
    summary = summary or {}
    rows: list[dict] = []
    for bucket_key in _DISCARD_BUCKETS:
        for entry in (summary.get(bucket_key) or []):
            if not isinstance(entry, dict):
                continue
            trace = entry.get("market_trace") or build_market_trace(entry, sport=sport)
            rows.append({
                "match_id":         entry.get("match_id"),
                "match_label":      entry.get("match_label"),
                "bucket":           bucket_key,
                "market_trace":     trace,
                "markets_checked":  entry.get("markets_checked") or [],
                "card_header":      entry.get("card_header"),
                "possible_alternative_markets": entry.get("possible_alternative_markets") or [],
            })
    return {
        "run_id":     run_id,
        "user_id":    user_id,
        "sport":      sport,
        "total_discarded": len(rows),
        "audit_rows": rows,
    }


__all__ = [
    "build_market_trace",
    "build_markets_checked",
    "build_discarded_header",
    "attach_market_trace_to_summary",
    "build_run_audit_payload",
]
