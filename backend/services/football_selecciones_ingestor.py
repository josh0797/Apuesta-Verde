"""Sprint-D8-Fase2 · Football selecciones (national teams) ingestor.

Une 3 fuentes para producir el dataset prematch que alimenta
``football_draw_potential`` + ``football_cohort_detector`` + el
módulo D9 de calibración:

  1. **Ground truth** ← openfootball JSON  (FT score → DRAW outcome)
  2. **Odds prematch (t-3h)** ← The Odds API historical via
     ``theoddsapi_historical_client.fetch_tournament_pit_odds``
  3. **Strength proxy** ← FIFA ranking points (PIT, pre-tournament)
     ``/app/data/fifa_ranking/team_points_by_tournament.json``

Disciplina
==========
* **Función pura**: el ingestor recibe datasets ya cargados + un
  fetcher inyectable. El I/O (disco/HTTP) lo hace el caller.
* **observe_only**: no escribe predicciones, no toca producción.
* **point-in-time estricto**: la snapshot FIFA es pre-torneo, las
  odds son prematch (t-3h). El ground truth nunca contamina
  features.
* **Fail-soft total**: cualquier mismatch de nombres devuelve
  ``available=False`` con reason code, sin abortar el sprint.
* **Devig clásico** (proportional) para extraer la probabilidad
  implicita justa del DRAW desde el mercado h2h.

NOTA: CLV no es computable en este sprint — el fetch histórico al
``t-3h`` no provee el ``odd_close`` (cierre real). El reporte lo
declara como ``CLV_NOT_AVAILABLE_T_MINUS_3H_ONLY``.
"""
from __future__ import annotations

import logging
import statistics
import unicodedata
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("services.football_selecciones_ingestor")

# ── Reason codes
RC_OK                          = "OK"
RC_NO_OPENFOOTBALL_MATCH       = "NO_OPENFOOTBALL_GROUND_TRUTH_MATCH"
RC_NO_H2H_MARKET               = "NO_H2H_MARKET_IN_PAYLOAD"
RC_NO_DRAW_OUTCOME             = "NO_DRAW_OUTCOME_IN_H2H"
RC_NO_FIFA_POINTS_HOME         = "NO_FIFA_POINTS_FOR_HOME"
RC_NO_FIFA_POINTS_AWAY         = "NO_FIFA_POINTS_FOR_AWAY"
RC_INSUFFICIENT_BOOKMAKERS     = "INSUFFICIENT_BOOKMAKERS_FOR_CONSENSUS"
RC_CLV_NOT_AVAILABLE           = "CLV_NOT_AVAILABLE_T_MINUS_3H_ONLY"


# ─────────────────────────────────────────────────────────────────────
# Team name normalization (light)
# ─────────────────────────────────────────────────────────────────────
# Map alternate names → canonical openfootball spelling.
TEAM_ALIASES: dict[str, str] = {
    "United States":            "United States",
    "USA":                      "United States",
    "United States of America": "United States",
    "Korea Republic":           "South Korea",
    "Republic of Korea":        "South Korea",
    "IR Iran":                  "Iran",
    "Iran (Islamic Republic of)": "Iran",
    "Türkiye":                  "Turkey",
    "Turkiye":                  "Turkey",
    "Czechia":                  "Czech Republic",
    "Bosnia and Herzegovina":   "Bosnia & Herzegovina",
    "Côte d'Ivoire":            "Ivory Coast",
    "Cote d'Ivoire":            "Ivory Coast",
    "DR Congo":                 "Congo DR",
}


def normalise_team_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = name.strip()
    # Strip diacritics.
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("`", "'").strip()
    return TEAM_ALIASES.get(s, s)


def teams_match(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return normalise_team_name(a).lower() == normalise_team_name(b).lower()


# ─────────────────────────────────────────────────────────────────────
# h2h market parsing
# ─────────────────────────────────────────────────────────────────────
def extract_consensus_h2h(event_payload: dict,
                          *,
                          home_team: str,
                          away_team: str,
                          min_bookmakers: int = 2,
                          ) -> dict:
    """Median across bookmakers of (home_odd, draw_odd, away_odd).

    Returns:
      ``{"available": bool, "draw_odd": float, "home_odd": float,
         "away_odd": float, "n_books": int, "reason_codes": [...]}``
    """
    reasons: list[str] = []
    if not isinstance(event_payload, dict):
        return {"available": False, "reason_codes": [RC_NO_H2H_MARKET],
                "n_books": 0}
    bookmakers = event_payload.get("bookmakers") or []
    draw_odds: list[float] = []
    home_odds: list[float] = []
    away_odds: list[float] = []
    for bk in bookmakers:
        if not isinstance(bk, dict):
            continue
        markets = bk.get("markets") or []
        h2h = next((m for m in markets if isinstance(m, dict)
                    and m.get("key") == "h2h"), None)
        if not h2h:
            continue
        outcomes = h2h.get("outcomes") or []
        h_odd = d_odd = a_odd = None
        for o in outcomes:
            if not isinstance(o, dict):
                continue
            name = (o.get("name") or "").strip()
            price = o.get("price")
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None
            if price is None or price <= 1.0:
                continue
            if name.lower() == "draw":
                d_odd = price
            elif teams_match(name, home_team):
                h_odd = price
            elif teams_match(name, away_team):
                a_odd = price
        if d_odd is not None and h_odd is not None and a_odd is not None:
            draw_odds.append(d_odd)
            home_odds.append(h_odd)
            away_odds.append(a_odd)

    n_books = len(draw_odds)
    if n_books == 0:
        return {"available": False,
                "reason_codes": [RC_NO_H2H_MARKET, RC_NO_DRAW_OUTCOME],
                "n_books": 0}
    if n_books < min_bookmakers:
        reasons.append(RC_INSUFFICIENT_BOOKMAKERS)
    return {
        "available":  True,
        "draw_odd":   statistics.median(draw_odds),
        "home_odd":   statistics.median(home_odds),
        "away_odd":   statistics.median(away_odds),
        "n_books":    n_books,
        "reason_codes": reasons,
    }


def devig_h2h(home_odd: float, draw_odd: float, away_odd: float
              ) -> dict:
    """Proportional de-vigging across the 3-outcome h2h market.

    Returns ``{"home", "draw", "away", "vig_pp"}`` (probabilities).
    """
    if not all(o and o > 1.0 for o in (home_odd, draw_odd, away_odd)):
        return {"home": None, "draw": None, "away": None, "vig_pp": None}
    p_h_raw = 1.0 / home_odd
    p_d_raw = 1.0 / draw_odd
    p_a_raw = 1.0 / away_odd
    sm = p_h_raw + p_d_raw + p_a_raw
    if sm <= 0:
        return {"home": None, "draw": None, "away": None, "vig_pp": None}
    return {
        "home":   p_h_raw / sm,
        "draw":   p_d_raw / sm,
        "away":   p_a_raw / sm,
        "vig_pp": round((sm - 1.0) * 100.0, 3),
    }


# ─────────────────────────────────────────────────────────────────────
# Match resolver — link an Odds API event ↔ openfootball match
# ─────────────────────────────────────────────────────────────────────
def resolve_groundtruth(odds_event: dict,
                         openfootball_matches: list[dict]) -> Optional[dict]:
    """Find the openfootball match that corresponds to an Odds API event.

    Match key: same date (YYYY-MM-DD) AND same {team1, team2} set
    (order-insensitive).
    """
    commence = odds_event.get("commence_time") or ""
    date_iso = commence[:10] if commence else None
    if not date_iso:
        return None
    a = normalise_team_name(odds_event.get("home_team")).lower()
    b = normalise_team_name(odds_event.get("away_team")).lower()
    if not a or not b:
        return None
    target = {a, b}
    for m in openfootball_matches:
        if m.get("date") != date_iso:
            continue
        t1 = normalise_team_name(m.get("team1")).lower()
        t2 = normalise_team_name(m.get("team2")).lower()
        if {t1, t2} == target:
            return m
    return None


def settle_draw(openfootball_match: dict) -> Optional[int]:
    """Return 1 if FT (regulation) was a draw, 0 if not, None if unknown.

    Critical: uses ``score.ft`` which is the 90' regulation score —
    NOT the post-extra-time / penalties result. This is the h2h market
    settlement convention used by The Odds API.
    """
    sc = (openfootball_match or {}).get("score") or {}
    ft = sc.get("ft") or [None, None]
    try:
        h, a = int(ft[0]), int(ft[1])
    except (TypeError, ValueError, IndexError):
        return None
    return 1 if h == a else 0


# ─────────────────────────────────────────────────────────────────────
# Feature builder per match
# ─────────────────────────────────────────────────────────────────────
def build_match_record(
    *,
    odds_event:         dict,
    openfootball_match: dict,
    fifa_points:        dict,
    tournament_name:    str,
    sport_key:          str,
    min_bookmakers:     int = 2,
) -> dict:
    """Build a single record for the calibration + cohort pipeline.

    Returns a dict containing:
      * source_audit:  odds_timestamp, commence_time, fifa_snapshot,
                       n_bookmakers, reason_codes.
      * features:      kwargs ready for ``compute_draw_potential``.
      * pick:          ``predicted_prob``, ``market_prob``, ``edge_pp``,
                       ``hit`` (post-settlement), ``odd_close`` (None
                       here — only t-3h available).
      * record:        flat dict ready for D9 ``compute_calibration_diagnostics``.
    """
    home_team = odds_event.get("home_team")
    away_team = odds_event.get("away_team")

    # The bookmakers payload lives inside ``event_payload`` (set by
    # ``fetch_tournament_pit_odds`` in the historical client), not at
    # the root of ``odds_event``. Fall back to the root for the smoke
    # tests that pass the inlined payload.
    payload = odds_event.get("event_payload") or odds_event

    # 1) H2H consensus across bookmakers.
    h2h = extract_consensus_h2h(payload,
                                 home_team=home_team,
                                 away_team=away_team,
                                 min_bookmakers=min_bookmakers)
    reasons: list[str] = list(h2h.get("reason_codes") or [])
    if not h2h["available"]:
        return _unavailable_record(odds_event, reasons)

    devig = devig_h2h(h2h["home_odd"], h2h["draw_odd"], h2h["away_odd"])
    market_implied_raw   = 1.0 / h2h["draw_odd"]
    market_implied_devig = devig["draw"]

    # 2) FIFA points → ELO proxy.
    home_norm = normalise_team_name(home_team)
    away_norm = normalise_team_name(away_team)
    elo_home = fifa_points.get(home_norm)
    elo_away = fifa_points.get(away_norm)
    if elo_home is None:
        reasons.append(RC_NO_FIFA_POINTS_HOME)
    if elo_away is None:
        reasons.append(RC_NO_FIFA_POINTS_AWAY)

    # 3) Tournament context.
    is_group_stage = (openfootball_match.get("round") == "Group Stage")

    # 4) Settlement (ground truth) — from openfootball, NOT from odds.
    hit = settle_draw(openfootball_match)

    # 5) Run the production model.
    from .football_draw_potential import compute_draw_potential
    model = compute_draw_potential(
        home_team=home_team or "",
        away_team=away_team or "",
        elo_home=elo_home, elo_away=elo_away,
        xg_home_l5=None, xg_away_l5=None,
        is_group_stage=is_group_stage,
        market_implied_draw_prob=market_implied_devig,
        tournament_context_score=0.7 if is_group_stage else 0.5,
    )

    predicted_prob = (model.get("draw_probability") / 100.0
                      if model.get("draw_probability") is not None else None)
    edge_pp = ((predicted_prob - market_implied_devig) * 100.0
               if (predicted_prob is not None
                    and market_implied_devig is not None) else None)

    record_for_d9 = {
        "predicted_prob":       predicted_prob,
        "market_implied_raw":   market_implied_raw,
        "market_implied_devig": market_implied_devig,
        "odd_close":            None,  # t-3h only; no closing line available
        "odd_taken":            h2h["draw_odd"],
        "hit":                  hit,
        "side":                 "DRAW",
    }

    features = {
        "elo_home":            elo_home,
        "elo_away":             elo_away,
        "is_group_stage":       is_group_stage,
        "tournament_context_score": 0.7 if is_group_stage else 0.5,
        "xg_home_l5":           None,
        "xg_away_l5":           None,
    }

    pick = {
        "tournament":           tournament_name,
        "sport_key":            sport_key,
        "event_id":             odds_event.get("event_id"),
        "commence_time":        odds_event.get("commence_time"),
        "home_team":            home_team,
        "away_team":            away_team,
        "predicted_prob":       predicted_prob,
        "market_prob":          market_implied_devig,
        "edge_pp":              edge_pp,
        "is_group_stage":       is_group_stage,
        "tournament_context_score": 0.7 if is_group_stage else 0.5,
        "label":                model.get("label"),
        "model_reason_codes":   model.get("reason_codes", []),
    }

    reasons.append(RC_OK)
    reasons.append(RC_CLV_NOT_AVAILABLE)

    return {
        "available":   True,
        "source_audit": {
            "odds_timestamp":   odds_event.get("odds_timestamp"),
            "commence_time":    odds_event.get("commence_time"),
            "sport_key":        sport_key,
            "tournament":       tournament_name,
            "n_bookmakers":     h2h.get("n_books"),
            "reason_codes":     reasons,
        },
        "features":    features,
        "pick":        pick,
        "record":      record_for_d9,
        "model_full":  model,
        "ground_truth": {
            "ft":   (openfootball_match.get("score") or {}).get("ft"),
            "round": openfootball_match.get("round"),
            "date": openfootball_match.get("date"),
        },
    }


def _unavailable_record(odds_event: dict, reasons: list[str]) -> dict:
    return {
        "available":    False,
        "source_audit": {
            "odds_timestamp":  odds_event.get("odds_timestamp"),
            "commence_time":   odds_event.get("commence_time"),
            "n_bookmakers":    0,
            "reason_codes":    reasons,
        },
        "features":  None,
        "pick":      None,
        "record":    None,
    }


# ─────────────────────────────────────────────────────────────────────
# High-level orchestrator (one tournament)
# ─────────────────────────────────────────────────────────────────────
async def ingest_tournament(
    *,
    sport_key:           str,
    dates_iso:           list[str],
    openfootball_matches: list[dict],
    fifa_points:         dict,
    tournament_name:     str,
    max_credits:         int,
    fetch_tournament_pit_odds_fn: Callable[..., Awaitable[dict]],
    api_key:             Optional[str] = None,
    http:                Optional[Callable[..., Awaitable[dict]]] = None,
    min_bookmakers:      int = 2,
) -> dict:
    """Run the full tournament ingestion.

    Returns:
      ``{"available", "records", "picks", "features",
         "source_audits", "credits_used", "credits_total_account",
         "aborted", "reason_codes", "tournament_name", "sport_key",
         "n_matches_total", "n_matches_resolved", "n_records_built"}``
    """
    odds_run = await fetch_tournament_pit_odds_fn(
        sport_key=sport_key,
        dates_iso=dates_iso,
        max_credits=max_credits,
        api_key=api_key,
        http=http,
    )

    records:  list[dict] = []
    picks:    list[dict] = []
    features: list[dict] = []
    audits:   list[dict] = []
    n_resolved = 0
    seen_event_ids: set[str] = set()

    for ev in odds_run.get("events", []):
        # Dedup: the daily /events listing returns ALL events available
        # at that snapshot (not only the events of that calendar day),
        # so the same event_id appears N times across N snapshots.
        evid = ev.get("event_id")
        if evid is not None and evid in seen_event_ids:
            continue
        if evid is not None:
            seen_event_ids.add(evid)

        gt = resolve_groundtruth(ev, openfootball_matches)
        if gt is None:
            audits.append({"reason_codes": [RC_NO_OPENFOOTBALL_MATCH],
                             "event_id": ev.get("event_id")})
            continue
        n_resolved += 1
        built = build_match_record(
            odds_event=ev,
            openfootball_match=gt,
            fifa_points=fifa_points,
            tournament_name=tournament_name,
            sport_key=sport_key,
            min_bookmakers=min_bookmakers,
        )
        audits.append(built["source_audit"])
        if not built["available"]:
            continue
        records.append(built["record"])
        picks.append(built["pick"])
        features.append(built["features"])

    return {
        "available":               bool(records),
        "tournament_name":         tournament_name,
        "sport_key":               sport_key,
        "records":                 records,
        "picks":                   picks,
        "features":                features,
        "source_audits":           audits,
        "credits_used":            odds_run.get("credits_used", 0),
        "credits_total_account":   odds_run.get("credits_total_account"),
        "aborted":                 odds_run.get("aborted", False),
        "reason_codes":            odds_run.get("reason_codes", []),
        "n_matches_in_odds_run":   len(odds_run.get("events", [])),
        "n_matches_resolved":      n_resolved,
        "n_records_built":         len(records),
    }


__all__ = [
    "normalise_team_name", "teams_match",
    "extract_consensus_h2h", "devig_h2h",
    "resolve_groundtruth", "settle_draw",
    "build_match_record", "ingest_tournament",
    "RC_OK", "RC_NO_OPENFOOTBALL_MATCH", "RC_NO_H2H_MARKET",
    "RC_NO_DRAW_OUTCOME", "RC_NO_FIFA_POINTS_HOME",
    "RC_NO_FIFA_POINTS_AWAY", "RC_INSUFFICIENT_BOOKMAKERS",
    "RC_CLV_NOT_AVAILABLE",
]
