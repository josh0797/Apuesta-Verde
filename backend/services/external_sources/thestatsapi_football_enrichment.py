"""Phase F74 — TheStatsAPI football enrichment.

Este módulo toma el schema canónico producido por
``services.football_data_enrichment.normalize_football_enrichment`` y
lo **enriquece con probabilidades estimadas por mercado** usando los
xG (expected goals) cuando están disponibles.

Estrategia (tier ladder)
========================
  1. **Dixon-Coles** (preferido) cuando ``home_xg`` y ``away_xg`` son
     ambos positivos. Calcula el grid de scorelines y deriva las
     probabilidades por mercado por agregación de celdas.

  2. **Poisson simple** (fallback) cuando solo queremos coordenadas
     puras independientes (sin corrección de bajo score). Usamos el
     mismo ``compute_scoreline_grid`` con ``use_dixon_coles=False``.

  3. **Heurística logística** (observe-only) cuando no hay xG pero sí
     hay una proxy comparable (p.ej. ratio de goles esperados Forebet
     vs. media histórica). Las probabilidades resultantes se marcan
     como ``quality="OBSERVE_ONLY"`` y NO deben alimentar edge real.

Reglas críticas Phase F73/F74
==============================
  * Si ``canonical.data_quality == THIN`` → **no se inyecta nada**.
  * Si ``canonical.requires_market_identity`` → **no se inyecta nada**.
  * Si el ``market_identity_key`` viene UNKNOWN → se marca el canonical
    y se omite la inyección.

Mercados soportados en esta primera fase
========================================
  * ``1X2:HOME`` / ``1X2:DRAW`` / ``1X2:AWAY``
  * ``DOUBLE_CHANCE:1X`` / ``DOUBLE_CHANCE:X2`` / ``DOUBLE_CHANCE:12``
  * ``DNB:HOME`` / ``DNB:AWAY``
  * ``TOTAL_GOALS:OVER:1.5`` / ``TOTAL_GOALS:UNDER:1.5``
  * ``TOTAL_GOALS:OVER:2.5`` / ``TOTAL_GOALS:UNDER:2.5``
  * ``TOTAL_GOALS:OVER:3.5`` / ``TOTAL_GOALS:UNDER:3.5``
  * ``BTTS:YES`` / ``BTTS:NO``
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import football_data_enrichment as fde
from .. import football_dixon_coles as fdc

log = logging.getLogger(__name__)

METHOD_DC          = fdc.METHOD_DC
METHOD_POISSON     = fdc.METHOD_POISSON
METHOD_OBSERVE     = "LOGISTIC_OBSERVE_ONLY"

QUALITY_STRONG       = "STRONG"
QUALITY_USABLE       = "USABLE"
QUALITY_OBSERVE_ONLY = "OBSERVE_ONLY"


# ─────────────────────────────────────────────────────────────────────
# Grid aggregation helpers
# ─────────────────────────────────────────────────────────────────────
def _build_full_grid(home_xg: float, away_xg: float,
                      *, use_dixon_coles: bool) -> list[dict]:
    """Construye el grid completo de scorelines (no solo top-N).

    Reutiliza la matemática de ``football_dixon_coles`` pero devuelve
    el grid íntegro normalizado, necesario para agregar probabilidades
    por mercado.
    """
    grid: list[dict] = []
    for h in range(fdc.MAX_GOALS + 1):
        for a in range(fdc.MAX_GOALS + 1):
            p_h = fdc._poisson_pmf(h, home_xg)
            p_a = fdc._poisson_pmf(a, away_xg)
            base = p_h * p_a
            if use_dixon_coles:
                base *= fdc._tau(h, a, home_xg, away_xg, fdc.DEFAULT_TAU_RHO)
            grid.append({"h": h, "a": a, "p": max(base, 0.0)})

    total = sum(c["p"] for c in grid) or 1.0
    for c in grid:
        c["p"] = c["p"] / total
    return grid


def _aggregate(grid: list[dict], predicate) -> float:
    return round(sum(c["p"] for c in grid if predicate(c["h"], c["a"])), 4)


def _probabilities_from_grid(grid: list[dict]) -> dict:
    """Mapea grid → diccionario de probabilidades por market_identity_key."""
    p_home = _aggregate(grid, lambda h, a: h > a)
    p_draw = _aggregate(grid, lambda h, a: h == a)
    p_away = _aggregate(grid, lambda h, a: a > h)
    # DNB: condicional sobre no-draw, renormalizado.
    nodraw = p_home + p_away
    dnb_home = round(p_home / nodraw, 4) if nodraw > 0 else None
    dnb_away = round(p_away / nodraw, 4) if nodraw > 0 else None
    # BTTS
    btts_yes = _aggregate(grid, lambda h, a: h >= 1 and a >= 1)
    btts_no  = round(1.0 - btts_yes, 4)
    # Totales
    def _over(line: float):
        # Para 2.5: total>=3 cuenta. Para 1.5: total>=2. Para 3.5: total>=4.
        threshold = int(line) + 1  # 2.5 → 3, 1.5 → 2, 3.5 → 4
        return _aggregate(grid, lambda h, a: (h + a) >= threshold)

    def _under(line: float):
        return round(1.0 - _over(line), 4)

    return {
        "1X2:HOME":              p_home,
        "1X2:DRAW":              p_draw,
        "1X2:AWAY":              p_away,
        "DOUBLE_CHANCE:1X":      round(p_home + p_draw, 4),
        "DOUBLE_CHANCE:X2":      round(p_draw + p_away, 4),
        "DOUBLE_CHANCE:12":      round(p_home + p_away, 4),
        "DNB:HOME":              dnb_home,
        "DNB:AWAY":              dnb_away,
        "BTTS:YES":              btts_yes,
        "BTTS:NO":               btts_no,
        "TOTAL_GOALS:OVER:1.5":  _over(1.5),
        "TOTAL_GOALS:UNDER:1.5": _under(1.5),
        "TOTAL_GOALS:OVER:2.5":  _over(2.5),
        "TOTAL_GOALS:UNDER:2.5": _under(2.5),
        "TOTAL_GOALS:OVER:3.5":  _over(3.5),
        "TOTAL_GOALS:UNDER:3.5": _under(3.5),
    }


# ─────────────────────────────────────────────────────────────────────
# Observe-only logistic heuristic
# ─────────────────────────────────────────────────────────────────────
def _heuristic_logistic(forebet_ctx: Optional[dict]) -> Optional[dict]:
    """Construye un mapa de probabilidades muy débil para mercados 1X2
    cuando solo tenemos predicción Forebet (no xG).

    Estos números son **observe-only**: NO deben alimentar edge real.
    Sirven para que la UI/auditoría tenga una referencia comparable
    con el mercado.
    """
    if not isinstance(forebet_ctx, dict):
        return None
    probs = forebet_ctx.get("probabilities") or {}
    if not isinstance(probs, dict):
        return None
    p_home = probs.get("home") or probs.get("1")
    p_draw = probs.get("draw") or probs.get("X")
    p_away = probs.get("away") or probs.get("2")
    try:
        ph = float(p_home) / 100.0 if p_home is not None else None
        pd = float(p_draw) / 100.0 if p_draw is not None else None
        pa = float(p_away) / 100.0 if p_away is not None else None
    except (TypeError, ValueError):
        return None
    if None in (ph, pd, pa):
        return None
    total = ph + pd + pa
    if total <= 0:
        return None
    ph, pd, pa = ph / total, pd / total, pa / total
    return {
        "1X2:HOME":          round(ph, 4),
        "1X2:DRAW":          round(pd, 4),
        "1X2:AWAY":          round(pa, 4),
        "DOUBLE_CHANCE:1X":  round(ph + pd, 4),
        "DOUBLE_CHANCE:X2":  round(pd + pa, 4),
        "DOUBLE_CHANCE:12":  round(ph + pa, 4),
    }


# ─────────────────────────────────────────────────────────────────────
# Public entry
# ─────────────────────────────────────────────────────────────────────
def enrich_football_match_with_thestatsapi(
    match_doc: dict,
    *,
    canonical: Optional[dict] = None,
    market_identity: Optional[dict] = None,
    prefer_dixon_coles: bool = True,
) -> dict:
    """Inyecta ``estimated_probabilities`` en el schema canónico.

    Parameters
    ----------
    match_doc :
        Documento del match (mismo input que ``normalize_football_enrichment``).
    canonical :
        (Opcional) schema canónico ya construido. Si no se provee, se
        construye llamando ``normalize_football_enrichment(match_doc)``.
    market_identity :
        (Opcional) market_identity asociado al pick. Si es UNKNOWN, se
        bloquea la inyección.
    prefer_dixon_coles :
        Si False, usa Poisson simple aunque haya xG.

    Returns
    -------
    dict
        El mismo objeto ``canonical`` (mutado) con ``estimated_probabilities``
        poblado cuando los gates lo permitan.
    """
    if canonical is None:
        canonical = fde.normalize_football_enrichment(
            match_doc, market_identity=market_identity,
        )

    # Gate de calidad — THIN no debe llenar probabilidades.
    if not fde._is_data_quality_sufficient(canonical):
        fde._append_code(canonical, fde.RC_PROBABILITIES_BLOCKED_THIN)
        return canonical
    if canonical.get("requires_market_identity"):
        fde._append_code(canonical, fde.RC_REQUIRES_MARKET_IDENTITY)
        return canonical

    xg_home = canonical.get("xg", {}).get("home")
    xg_away = canonical.get("xg", {}).get("away")

    # ── Tier 1 / Tier 2: necesitamos xG en ambos lados ──────────────
    if xg_home is not None and xg_away is not None and xg_home >= 0 and xg_away >= 0:
        try:
            grid = _build_full_grid(
                float(xg_home), float(xg_away),
                use_dixon_coles=bool(prefer_dixon_coles),
            )
            probs = _probabilities_from_grid(grid)
        except Exception as exc:  # noqa: BLE001
            log.warning("[ts_football_enrichment] grid build failed: %s", exc)
            return canonical

        method  = METHOD_DC if prefer_dixon_coles else METHOD_POISSON
        # STRONG only if grid built from xG on both sides AND we trust DC.
        quality = (QUALITY_STRONG
                    if canonical.get("data_quality") == fde.DQ_STRONG
                    else QUALITY_USABLE)
        for key, p in probs.items():
            if p is None:
                continue
            fde.attach_estimated_probability(
                canonical, key,
                probability=p, method=method, quality=quality,
                inputs={"home_xg": xg_home, "away_xg": xg_away},
            )
        return canonical

    # ── Tier 3: heurística logística observe-only ───────────────────
    forebet_ctx = (canonical.get("external_context") or {}).get("forebet")
    obs = _heuristic_logistic(forebet_ctx)
    if obs:
        for key, p in obs.items():
            fde.attach_estimated_probability(
                canonical, key,
                probability=p, method=METHOD_OBSERVE,
                quality=QUALITY_OBSERVE_ONLY,
                inputs={"source": "forebet_1x2_probabilities"},
            )

    return canonical


__all__ = [
    "METHOD_DC", "METHOD_POISSON", "METHOD_OBSERVE",
    "QUALITY_STRONG", "QUALITY_USABLE", "QUALITY_OBSERVE_ONLY",
    "enrich_football_match_with_thestatsapi",
]
