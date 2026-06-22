"""Sprint-F98 · TheSportsDB → F74 envelope adapter (pure).

TheSportsDB is the **fixture/identity baseline** in the cascade. We
only extract the bits TheSportsDB actually exposes reliably:

  * Identity (event id, league, kickoff)
  * Recent fixtures per side (when ``recent_results`` is hydrated)
  * Head-to-head sample (when ``h2h`` is hydrated)

Rich stats (xG, shots, possession) are NOT TheSportsDB territory —
those come from SofaScore / TheStatsAPI in the cascade selector.
"""
from __future__ import annotations

from typing import Any

from services.adapters._envelope import (
    RC_MAPPING_OK,
    RC_MAPPING_PARTIAL,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    _last_n,
    _normalize_form_letter,
    _safe_float,
    _safe_int,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE = "thesportsdb"


def _coalesce(d: Any, *keys: str) -> Any:
    """Return d[key] for first key whose value is not None (NOT first truthy).

    Critical: scores legitimately equal 0; ``a or b`` would drop those.
    """
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_recent(raw_recent: Any, *, team_side: str, n: int = 5) -> tuple[list[dict], list[str]]:
    """Convert TheSportsDB recent_results blob into normalised entries.

    Expected raw shapes (any-of):
      * list of dicts with keys like ``strHomeTeam``, ``strAwayTeam``,
        ``intHomeScore``, ``intAwayScore``, ``dateEvent``
      * Pre-normalized dicts with ``home_team``/``away_team``/``score``.
    """
    if not isinstance(raw_recent, list):
        return [], []
    out: list[dict] = []
    form: list[str] = []
    for item in _last_n(raw_recent, n):
        if not isinstance(item, dict):
            continue
        home_n = item.get("strHomeTeam")
        if home_n is None:
            ht = item.get("home_team")
            home_n = ht.get("name") if isinstance(ht, dict) else ht
        away_n = item.get("strAwayTeam")
        if away_n is None:
            at = item.get("away_team")
            away_n = at.get("name") if isinstance(at, dict) else at
        h_goals = _safe_int(_coalesce(item, "intHomeScore", "home_score", "home_goals"))
        a_goals = _safe_int(_coalesce(item, "intAwayScore", "away_score", "away_goals"))
        date    = _coalesce(item, "dateEvent", "date")
        if home_n is None or away_n is None or h_goals is None or a_goals is None:
            continue
        is_home = bool(home_n and (str(team_side).lower() == "home"))  # heuristic only
        # When we don't actually know team_side per entry, we still
        # record the raw goals; the consumer pipeline rebuilds form
        # later if needed.
        scored, conceded = (h_goals, a_goals) if team_side == "home" else (a_goals, h_goals)
        letter = _normalize_form_letter(None, team_side=team_side, scored=scored, conceded=conceded)
        if letter:
            form.append(letter)
        out.append({
            "date":          date,
            "home_team":     home_n,
            "away_team":     away_n,
            "home_goals":    h_goals,
            "away_goals":    a_goals,
            "team_scored":   scored,
            "team_conceded": conceded,
        })
    return out, form


def adapt_thesportsdb_to_f74(raw: Any) -> dict:
    """Convert a TheSportsDB raw payload to the F98 envelope.

    Accepted ``raw`` shapes:
      * ``{"event": {...}, "recent_home": [...], "recent_away": [...],
           "h2h": [...]}``      ← our canonical wrapper
      * the bare event dict (only identity will be filled)
    """
    if not isinstance(raw, dict):
        env = envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
        return env
    if not raw:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([str(k) for k in raw.keys()])

    # ── Identity (informational; cascade will use it for canonical id) ─
    event = raw.get("event") or raw
    if isinstance(event, dict):
        env["sources"]["event_id"]     = event.get("idEvent") or event.get("id_event")
        env["sources"]["league_id"]    = event.get("idLeague")
        env["sources"]["league_name"]  = event.get("strLeague")
        env["sources"]["kickoff_iso"]  = event.get("strTimestamp") or event.get("strDate")

    # ── Recent fixtures (home + away) ─────────────────────────────────
    rh, form_h = _extract_recent(raw.get("recent_home"), team_side="home", n=5)
    ra, form_a = _extract_recent(raw.get("recent_away"), team_side="away", n=5)
    if rh:
        set_field(env, "home.recent_fixtures", rh, sample_size=len(rh))
        # Goals aggregates from recent fixtures.
        goals_for = [m["team_scored"]   for m in rh if m.get("team_scored")   is not None]
        goals_ag  = [m["team_conceded"] for m in rh if m.get("team_conceded") is not None]
        if goals_for:
            set_field(env, "home.goals_scored_l5",   sum(goals_for) / len(goals_for), sample_size=len(goals_for))
        if goals_ag:
            set_field(env, "home.goals_conceded_l5", sum(goals_ag) / len(goals_ag),   sample_size=len(goals_ag))
    if form_h:
        set_field(env, "home.form_string_l5", "".join(form_h), sample_size=len(form_h))
    if ra:
        set_field(env, "away.recent_fixtures", ra, sample_size=len(ra))
        goals_for = [m["team_scored"]   for m in ra if m.get("team_scored")   is not None]
        goals_ag  = [m["team_conceded"] for m in ra if m.get("team_conceded") is not None]
        if goals_for:
            set_field(env, "away.goals_scored_l5",   sum(goals_for) / len(goals_for), sample_size=len(goals_for))
        if goals_ag:
            set_field(env, "away.goals_conceded_l5", sum(goals_ag) / len(goals_ag),   sample_size=len(goals_ag))
    if form_a:
        set_field(env, "away.form_string_l5", "".join(form_a), sample_size=len(form_a))

    # ── H2H ───────────────────────────────────────────────────────────
    h2h = raw.get("h2h")
    if isinstance(h2h, list) and h2h:
        normalised: list[dict] = []
        hw = aw = dr = 0
        for it in h2h[-10:]:  # cap to last 10
            if not isinstance(it, dict):
                continue
            home_n = _coalesce(it, "strHomeTeam", "home_team")
            away_n = _coalesce(it, "strAwayTeam", "away_team")
            h_g = _safe_int(_coalesce(it, "intHomeScore", "home_score", "home_goals"))
            a_g = _safe_int(_coalesce(it, "intAwayScore", "away_score", "away_goals"))
            if h_g is None or a_g is None:
                continue
            normalised.append({
                "date":       it.get("dateEvent") or it.get("date"),
                "home_team":  home_n,
                "away_team":  away_n,
                "home_goals": h_g,
                "away_goals": a_g,
            })
            if h_g > a_g:
                hw += 1
            elif h_g < a_g:
                aw += 1
            else:
                dr += 1
        if normalised:
            set_field(env, "h2h.matches",   normalised, sample_size=len(normalised))
            set_field(env, "h2h.home_wins", hw, sample_size=len(normalised))
            set_field(env, "h2h.away_wins", aw, sample_size=len(normalised))
            set_field(env, "h2h.draws",     dr, sample_size=len(normalised))
            set_field(env, "h2h.sample",    len(normalised), sample_size=len(normalised))

    codes: list[str] = []
    if env["home"] or env["away"] or env["h2h"]:
        codes.append(RC_MAPPING_OK if (env["home"] and env["away"]) else RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
