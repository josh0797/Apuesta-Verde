"""Phase F86 — H2H Decision Policy.

Define cuándo el contexto H2H puede influir en decisiones y cuándo es
solo contexto narrativo.

Reglas
------
* ``sample_size_total < MIN_DECISION_SAMPLE``  → solo contexto + warning,
  NO afecta puntos.
* ``sample_size_recent < MIN_DECISION_SAMPLE`` → solo contexto + warning
  (partidos > 1 año), NO afecta puntos.
* ``sample_size_recent ≥ MIN_DECISION_SAMPLE`` → aplica puntos por mercado
  (Over/Under 1.5/2.5/3.5, BTTS, DNB).

El módulo es puro (sin Mongo, sin httpx) — testeable con ``pytest`` sin
mocks externos.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

# ─────────────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────────────

# Muestra mínima para que el H2H influya en decisión.
# 4 partidos recientes es el piso defendible estadísticamente:
# - 3 ó menos: cualquier outlier sesga el rate >25 pts porcentuales.
# - 4 ó más: un outlier sesga ≤25 pts, suficiente para señal direccional.
MIN_DECISION_SAMPLE = 4

# Edad máxima en días para considerar un partido "recencia útil".
MAX_RECENT_DAYS = 365

# Tabla de puntos por mercado cuando H2H es decision-useful.
# Los valores se suman al ``confidence_score`` del mercado correspondiente
# en el motor de scoring. Mantén los puntos pequeños (≤ +5) — H2H es
# solo UN factor entre xG, forma reciente, lesiones, motivación, etc.
H2H_POINT_RULES: dict[str, dict] = {
    # Over/Under
    "OVER_1_5":  {"min_rate": 0.80, "points": +5, "label": "H2H_PROFILE_OVER_1_5"},
    "UNDER_1_5": {"min_rate": 0.50, "points": +4, "label": "H2H_PROFILE_UNDER_1_5"},
    "OVER_2_5":  {"min_rate": 0.70, "points": +4, "label": "H2H_PROFILE_OVER_2_5"},
    "UNDER_2_5": {"min_rate": 0.65, "points": +4, "label": "H2H_PROFILE_UNDER_2_5"},
    "OVER_3_5":  {"min_rate": 0.60, "points": +5, "label": "H2H_PROFILE_OVER_3_5"},
    "UNDER_3_5": {"min_rate": 0.75, "points": +5, "label": "H2H_PROFILE_UNDER_3_5"},
    # BTTS
    "BTTS_YES":  {"min_rate": 0.60, "points": +4, "label": "H2H_PROFILE_BTTS_YES"},
    "BTTS_NO":   {"min_rate": 0.70, "points": +5, "label": "H2H_PROFILE_BTTS_NO"},
    # Home / Away dominance (Doble Oportunidad / DNB).
    "HOME_DNB":  {"min_rate": 0.60, "points": +4, "label": "H2H_HOME_DOMINANT"},
    "AWAY_DNB":  {"min_rate": 0.60, "points": +4, "label": "H2H_AWAY_DOMINANT"},
}

# Reason codes (machine-readable).
RC_NO_SAMPLE              = "H2H_NO_SAMPLE"
RC_SAMPLE_BELOW_THRESHOLD = "H2H_SAMPLE_BELOW_DECISION_THRESHOLD"
RC_RECENT_BELOW_THRESHOLD = "H2H_RECENT_SAMPLE_BELOW_THRESHOLD"
RC_DECISION_USEFUL        = "H2H_DECISION_USEFUL"


# ─────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────
def _parse_iso(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _is_recent(date_str: Any, *, max_days: int = MAX_RECENT_DAYS) -> bool:
    d = _parse_iso(date_str)
    if d is None:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d) <= timedelta(days=max_days)


def _score_pair(score_str: Any) -> Optional[tuple[int, int]]:
    """Parse '2-1' / '0-0' / 'NaN-1' robustly. Returns None on failure."""
    if not isinstance(score_str, str) or "-" not in score_str:
        return None
    try:
        h, a = score_str.split("-", 1)
        return int(h.strip()), int(a.strip())
    except Exception:  # noqa: BLE001
        return None


def _total_goals(m: dict) -> Optional[int]:
    p = _score_pair(m.get("score"))
    return None if p is None else (p[0] + p[1])


def _side_of(m: dict, team_name: str) -> Optional[str]:
    """Was ``team_name`` home or away in this match? Returns ``'home'``,
    ``'away'`` or ``None``."""
    if not team_name:
        return None
    tn = team_name.strip().lower()
    home_name = (m.get("home") or "").strip().lower()
    away_name = (m.get("away") or "").strip().lower()
    if tn == home_name:
        return "home"
    if tn == away_name:
        return "away"
    return None


def _rate_of(matches: list[dict], predicate: Callable[[dict], bool]) -> float:
    if not matches:
        return 0.0
    return sum(1 for m in matches if predicate(m)) / len(matches)


# ─────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────
def classify_h2h_context(
    h2h_context: dict | None,
    h2h_recent: list[dict] | None,
) -> dict:
    """Annotate the H2H context with decision-policy fields.

    The input ``h2h_context`` is preserved (shallow-copied + extended).
    """
    matches = h2h_recent or []
    recent  = [m for m in matches if _is_recent(m.get("date"))]
    sample_total  = len(matches)
    sample_recent = len(recent)

    warnings: list[str] = []
    reason_codes: list[str] = []

    if sample_total == 0:
        warnings.append("Sin enfrentamientos directos registrados.")
        reason_codes.append(RC_NO_SAMPLE)
        decision_useful = False
    elif sample_total < MIN_DECISION_SAMPLE:
        warnings.append(
            f"Solo se registran {sample_total} enfrentamientos directos — "
            "muestra limitada, contexto pero no fuente primaria."
        )
        reason_codes.append(RC_SAMPLE_BELOW_THRESHOLD)
        decision_useful = False
    elif sample_recent < MIN_DECISION_SAMPLE:
        warnings.append(
            f"{sample_total} enfrentamientos totales, pero solo {sample_recent} "
            f"en los últimos {MAX_RECENT_DAYS // 30} meses — "
            "contexto histórico, no afecta decisión."
        )
        reason_codes.append(RC_RECENT_BELOW_THRESHOLD)
        decision_useful = False
    else:
        reason_codes.append(RC_DECISION_USEFUL)
        decision_useful = True

    out = dict(h2h_context or {})
    out["recent_matches"]     = matches
    out["recent_within_1y"]   = recent
    out["sample_size_total"]  = sample_total
    out["sample_size_recent"] = sample_recent
    out["decision_useful"]    = decision_useful
    out["warnings"]           = warnings
    existing_codes = list(out.get("reason_codes") or [])
    out["reason_codes"]       = existing_codes + [
        c for c in reason_codes if c not in existing_codes
    ]
    return out


def apply_h2h_decision_points(
    classified: dict,
    home_name: str,
    away_name: str,
) -> dict:
    """Compute per-market points contributed by H2H.

    Returns
    -------
    dict
        ``{
            "points_by_market": {"OVER_2_5": +4, "BTTS_NO": +5, ...},
            "signals":          ["H2H_PROFILE_OVER_2_5", ...],
            "applied":          True/False,    # False when not decision_useful
            "rates":            {"over_2_5": 0.71, "btts_yes": 0.43, ...},
            "sample_size":      sample_size_recent,
        }``
    """
    out: dict = {
        "points_by_market": {},
        "signals":          [],
        "applied":          False,
        "rates":            {},
        "sample_size":      0,
    }

    if not classified.get("decision_useful"):
        return out

    recent = classified.get("recent_within_1y") or []
    if not recent:
        return out

    # Goal-based rates.
    rates = {
        "over_1_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 2),
        "over_2_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 3),
        "over_3_5":  _rate_of(recent, lambda m: (_total_goals(m) or 0) >= 4),
        "under_1_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 1),
        "under_2_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 2),
        "under_3_5": _rate_of(recent, lambda m: (_total_goals(m) or 99) <= 3),
    }

    def _btts(m: dict) -> bool:
        p = _score_pair(m.get("score"))
        return p is not None and p[0] >= 1 and p[1] >= 1
    rates["btts_yes"] = _rate_of(recent, _btts)
    rates["btts_no"]  = 1.0 - rates["btts_yes"] if recent else 0.0

    # DNB (Draw No Bet) = gana o empata.
    def _team_did_not_lose(m: dict, team: str) -> bool:
        p = _score_pair(m.get("score"))
        if p is None:
            return False
        side = _side_of(m, team)
        if side == "home":
            return p[0] >= p[1]
        if side == "away":
            return p[1] >= p[0]
        return False

    rates["home_dnb"] = _rate_of(recent, lambda m: _team_did_not_lose(m, home_name))
    rates["away_dnb"] = _rate_of(recent, lambda m: _team_did_not_lose(m, away_name))

    market_to_rate = {
        "OVER_1_5":  rates["over_1_5"],
        "OVER_2_5":  rates["over_2_5"],
        "OVER_3_5":  rates["over_3_5"],
        "UNDER_1_5": rates["under_1_5"],
        "UNDER_2_5": rates["under_2_5"],
        "UNDER_3_5": rates["under_3_5"],
        "BTTS_YES":  rates["btts_yes"],
        "BTTS_NO":   rates["btts_no"],
        "HOME_DNB":  rates["home_dnb"],
        "AWAY_DNB":  rates["away_dnb"],
    }
    for market, rule in H2H_POINT_RULES.items():
        rate = market_to_rate.get(market, 0.0)
        if rate >= rule["min_rate"]:
            out["points_by_market"][market] = rule["points"]
            out["signals"].append(rule["label"])

    out["rates"]       = rates
    out["applied"]     = True
    out["sample_size"] = len(recent)
    return out


def build_h2h_decision(match_doc: dict) -> tuple[dict, dict]:
    """Convenience wrapper used by the ingestor.

    Returns ``(classified_context, decision_payload)``.
    """
    classified = classify_h2h_context(
        match_doc.get("h2h_context") or {},
        match_doc.get("h2h_recent")  or [],
    )
    decision = apply_h2h_decision_points(
        classified,
        home_name=(match_doc.get("home_team", {}) or {}).get("name") or "",
        away_name=(match_doc.get("away_team", {}) or {}).get("name") or "",
    )
    return classified, decision


__all__ = [
    "classify_h2h_context",
    "apply_h2h_decision_points",
    "build_h2h_decision",
    "MIN_DECISION_SAMPLE", "MAX_RECENT_DAYS",
    "H2H_POINT_RULES",
    "RC_NO_SAMPLE", "RC_SAMPLE_BELOW_THRESHOLD",
    "RC_RECENT_BELOW_THRESHOLD", "RC_DECISION_USEFUL",
]
