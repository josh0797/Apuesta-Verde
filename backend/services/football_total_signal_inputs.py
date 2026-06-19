"""
Football Total Signal Preview Inputs Builder — Sprint D10 wiring.

Función pura `build_football_total_signal_preview_inputs(match)` que
toma el `match` dict del pipeline football y construye el contrato
de inputs que consume el endpoint `/api/football/manual-odds/preview`
y el componente `InlineManualOddsInput` (vía
`pickContext.football_total_signal_preview_inputs`).

Reglas
------
* PURO: sin I/O, sin Mongo, sin APIs.
* Fail-soft: si faltan datos críticos (lambdas), devuelve None — la
  UI simplemente no renderiza el panel D10.
* No inventa datos: las muestras que no estén disponibles se omiten
  (la función `calculate_football_total_signal` ya maneja `None`).
"""

from __future__ import annotations

from typing import Any, Optional


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _extract_lambdas(match: dict) -> tuple[Optional[float], Optional[float]]:
    """Intenta resolver las lambdas base del partido en distintos
    formatos comunes en el pipeline."""
    if not isinstance(match, dict):
        return None, None
    # Convención canónica
    lam_h = _safe_float(match.get("lambda_home"))
    lam_a = _safe_float(match.get("lambda_away"))
    if lam_h is not None and lam_a is not None:
        return lam_h, lam_a
    # Dixon-Coles output cached on the match
    dc = match.get("dc_lambdas") or match.get("dixon_coles") or {}
    if isinstance(dc, dict):
        lam_h = lam_h or _safe_float(dc.get("lambda_home"))
        lam_a = lam_a or _safe_float(dc.get("lambda_away"))
        if lam_h is not None and lam_a is not None:
            return lam_h, lam_a
    # xG averages used as proxy lambdas (last-resort cascade).
    lam_h = lam_h or _safe_float(match.get("home_xg"))
    lam_a = lam_a or _safe_float(match.get("away_xg"))
    return lam_h, lam_a


def _extract_h2h_games(match: dict) -> list[dict]:
    """Acepta varias shapes y normaliza a la que pide
    `calculate_weighted_h2h_goals`."""
    out: list[dict] = []
    raw = (
        match.get("h2h_games")
        or match.get("recent_h2h_games")
        or match.get("h2h")
        or []
    )
    if not isinstance(raw, list):
        return out
    for g in raw:
        if not isinstance(g, dict):
            continue
        # Normalize keys
        hg = g.get("home_goals", g.get("home_score", g.get("home")))
        ag = g.get("away_goals", g.get("away_score", g.get("away")))
        tot = g.get("total_goals")
        status = (g.get("status") or g.get("match_status") or "FINAL").upper()
        out.append({
            "home_goals":     hg,
            "away_goals":     ag,
            "total_goals":    tot,
            "status":         status,
            "date":           g.get("date") or g.get("kickoff") or g.get("kickoff_iso"),
            "age_days":       g.get("age_days"),
            "is_friendly":    g.get("is_friendly", False),
            "competition_id": g.get("competition_id") or g.get("league_id"),
            "competition_type": g.get("competition_type") or g.get("league_type"),
        })
    return out


def _extract_team_recent_matches(match: dict, side: str) -> list[dict]:
    """`side` ∈ {'home', 'away'}. Devuelve hasta 5 partidos recientes."""
    raw = (
        match.get(f"{side}_recent_matches")
        or match.get(f"{side}_l5_matches")
        or match.get(f"{side}_form")
        or []
    )
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for m in raw:
        if not isinstance(m, dict):
            continue
        gs = m.get("goals_scored", m.get("goals_for"))
        gc = m.get("goals_conceded", m.get("goals_against"))
        out.append({
            "goals_scored":      gs,
            "goals_conceded":    gc,
            "opponent_strength": m.get("opponent_strength", "average"),
            "is_friendly":       m.get("is_friendly", False),
            "status":            (m.get("status") or "FINAL").upper(),
            "date":              m.get("date") or m.get("kickoff") or m.get("kickoff_iso"),
        })
        if len(out) >= 5:
            break
    return out


def _extract_xg_block(match: dict, side: str) -> Optional[dict]:
    """Lee xG L5 + L15 si están disponibles. Devuelve None cuando no
    hay datos suficientes (no sustituye con 0.0)."""
    src = (
        match.get(f"{side}_xg_recent")
        or match.get(f"{side}_xg_l15")
        or {}
    )
    if not isinstance(src, dict):
        return None
    # Convenciones aceptadas:
    xg_for_l5 = _safe_float(src.get("xg_for_l5") or src.get("xg_l5_mean")
                              or src.get("xg_l5"))
    xg_against_l5 = _safe_float(src.get("xg_against_l5"))
    xg_for_l15 = _safe_float(src.get("xg_for_l15") or src.get("xg_l15_mean")
                                or src.get("xg_l15"))
    xg_against_l15 = _safe_float(src.get("xg_against_l15"))
    matches_avail = src.get("matches_available")
    if (xg_for_l5 is None and xg_for_l15 is None):
        return None
    return {
        "xg_for_l5":         xg_for_l5,
        "xg_against_l5":     xg_against_l5,
        "xg_for_l15":        xg_for_l15,
        "xg_against_l15":    xg_against_l15,
        "matches_available": matches_avail,
    }


def build_football_total_signal_preview_inputs(
    match: Optional[dict],
) -> Optional[dict]:
    """Construye el dict que la UI envía al endpoint D10. Devuelve
    None cuando las lambdas base no se pueden resolver — sin lambdas
    no hay modelo, por lo que no tiene sentido renderizar el panel."""
    if not isinstance(match, dict):
        return None
    lam_h, lam_a = _extract_lambdas(match)
    if lam_h is None or lam_a is None:
        return None

    h2h = _extract_h2h_games(match)
    home_recent = _extract_team_recent_matches(match, "home")
    away_recent = _extract_team_recent_matches(match, "away")
    home_xg = _extract_xg_block(match, "home")
    away_xg = _extract_xg_block(match, "away")

    base_eg = lam_h + lam_a
    return {
        "base_lambda_home":     round(lam_h, 4),
        "base_lambda_away":     round(lam_a, 4),
        "base_expected_goals":  round(base_eg, 4),
        # Optional samples — endpoint handles missing gracefully.
        "recent_h2h_games":     h2h or None,
        "home_recent_matches":  home_recent or None,
        "away_recent_matches":  away_recent or None,
        "home_xg_recent":       home_xg,
        "away_xg_recent":       away_xg,
        # Optional metadata.
        "current_match_context": {
            "competition_id":   match.get("competition_id") or match.get("league_id"),
            "competition_type": match.get("competition_type") or match.get("league_type"),
            "is_friendly":      bool(match.get("is_friendly")),
        },
    }


__all__ = ["build_football_total_signal_preview_inputs"]
