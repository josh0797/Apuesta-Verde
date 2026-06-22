"""Sprint-F98 · SofaScore → F74 envelope adapter (pure).

SofaScore is the **primary source** for:
  * shots / shots on target
  * possession / pass accuracy
  * recent form
  * head-to-head
  * corners (sometimes)

This adapter expects the ``raw`` dict produced by ``services
.external_sources.sofascore``-style fetchers. It is intentionally
resilient to slight schema drift because SofaScore HTML/JSON shape
changes across deployments.
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
    _safe_mean,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE = "sofascore"


def _coalesce(d: Any, *keys: str) -> Any:
    """Return the first key present in ``d`` (not the first truthy).

    Critical: ``home_score`` can legitimately be 0; using ``or`` would
    silently drop those entries.
    """
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _normalise_form_block(raw_form: Any, *, team_name: str, n: int = 5) -> tuple[list[dict], list[str], dict]:
    """Normalise SofaScore form list into our shape + aggregate stats."""
    if not isinstance(raw_form, list):
        return [], [], {}
    out: list[dict] = []
    form_letters: list[str] = []
    shots_for: list[float] = []
    shots_sot: list[float] = []
    poss:      list[float] = []
    corners_for: list[int] = []
    corners_ag:  list[int] = []
    xg_for:    list[float] = []
    xg_ag:     list[float] = []
    btts_count = 0
    clean_sheets = 0
    for item in _last_n(raw_form, n):
        if not isinstance(item, dict):
            continue
        home_n = _coalesce(item, "home_team") or _coalesce(item.get("homeTeam") or {}, "name")
        away_n = _coalesce(item, "away_team") or _coalesce(item.get("awayTeam") or {}, "name")
        h_g = _safe_int(_coalesce(item, "home_score", "home_goals"))
        a_g = _safe_int(_coalesce(item, "away_score", "away_goals"))
        if home_n is None or away_n is None or h_g is None or a_g is None:
            continue
        is_home = str(team_name or "").strip().lower() == str(home_n or "").strip().lower()
        scored, conceded = (h_g, a_g) if is_home else (a_g, h_g)
        letter = _normalize_form_letter(item.get("result"), team_side=("home" if is_home else "away"),
                                          scored=scored, conceded=conceded)
        if letter:
            form_letters.append(letter)
        # Per-team stats live under "home_stats"/"away_stats" in our wrapper.
        side_stats = item.get("home_stats" if is_home else "away_stats") or {}
        opp_stats  = item.get("away_stats" if is_home else "home_stats") or {}
        sf = _safe_float(side_stats.get("shots") or side_stats.get("total_shots"))
        if sf is not None:
            shots_for.append(sf)
        ssot = _safe_float(side_stats.get("shots_on_target") or side_stats.get("shots_on_goal"))
        if ssot is not None:
            shots_sot.append(ssot)
        p = _safe_float(side_stats.get("possession"))
        if p is not None:
            poss.append(p)
        cf = _safe_int(side_stats.get("corners"))
        if cf is not None:
            corners_for.append(cf)
        ca = _safe_int(opp_stats.get("corners"))
        if ca is not None:
            corners_ag.append(ca)
        xf = _safe_float(side_stats.get("xg") or side_stats.get("expected_goals"))
        if xf is not None:
            xg_for.append(xf)
        xa = _safe_float(opp_stats.get("xg") or opp_stats.get("expected_goals"))
        if xa is not None:
            xg_ag.append(xa)
        if scored > 0 and conceded > 0:
            btts_count += 1
        if conceded == 0:
            clean_sheets += 1
        out.append({
            "date":          item.get("date"),
            "home_team":     home_n,
            "away_team":     away_n,
            "home_goals":    h_g,
            "away_goals":    a_g,
            "team_scored":   scored,
            "team_conceded": conceded,
        })
    aggregates = {
        "shots_for_l5":         _safe_mean(shots_for),
        "shots_on_target_l5":   _safe_mean(shots_sot),
        "possession_avg_l5":    _safe_mean(poss),
        "corners_for_l5":       _safe_mean(corners_for),
        "corners_against_l5":   _safe_mean(corners_ag),
        "xg_for_l5":            _safe_mean(xg_for),
        "xg_against_l5":        _safe_mean(xg_ag),
        "btts_rate_l5":         (btts_count / len(out)) if out else None,
        "clean_sheets_l5":      clean_sheets,
        "sample":               len(out),
    }
    return out, form_letters, aggregates


def _set_side(env: dict, side: str, fixtures: list[dict], form: list[str],
               aggregates: dict) -> None:
    if not fixtures:
        return
    n = aggregates.get("sample") or len(fixtures)
    set_field(env, f"{side}.recent_fixtures", fixtures, sample_size=n)
    if form:
        set_field(env, f"{side}.form_string_l5", "".join(form), sample_size=len(form))
    # Goals (always derivable from fixtures).
    gf = [m["team_scored"]   for m in fixtures if m.get("team_scored")   is not None]
    ga = [m["team_conceded"] for m in fixtures if m.get("team_conceded") is not None]
    if gf:
        set_field(env, f"{side}.goals_scored_l5",   sum(gf) / len(gf), sample_size=len(gf))
    if ga:
        set_field(env, f"{side}.goals_conceded_l5", sum(ga) / len(ga), sample_size=len(ga))
    for key in ("shots_for_l5", "shots_on_target_l5", "possession_avg_l5",
                 "corners_for_l5", "corners_against_l5",
                 "xg_for_l5", "xg_against_l5",
                 "btts_rate_l5", "clean_sheets_l5"):
        v = aggregates.get(key)
        if v is not None:
            set_field(env, f"{side}.{key}", v, sample_size=n)


def adapt_sofascore_to_f74(raw: Any, *, home_team: str = "", away_team: str = "") -> dict:
    """Convert a SofaScore raw payload to the F98 envelope.

    Expected (canonical wrapper produced by our SofaScore fetchers)::

        raw = {
            "event_id":   12345,
            "home_form":  [{ ... last 5 fixtures with stats ... }],
            "away_form":  [{ ... }],
            "h2h":        [{ ... }],
            "odds":       {"match_winner": {"home": 1.8, ...}},
        }
    """
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
    if not raw:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([str(k) for k in raw.keys()])
    if raw.get("event_id") is not None:
        env["sources"]["event_id"] = raw.get("event_id")

    # ── Form per side ────────────────────────────────────────────────
    fh, form_h, agg_h = _normalise_form_block(raw.get("home_form"), team_name=home_team)
    fa, form_a, agg_a = _normalise_form_block(raw.get("away_form"), team_name=away_team)
    _set_side(env, "home", fh, form_h, agg_h)
    _set_side(env, "away", fa, form_a, agg_a)

    # ── H2H ───────────────────────────────────────────────────────────
    h2h_raw = raw.get("h2h")
    if isinstance(h2h_raw, list) and h2h_raw:
        hh: list[dict] = []
        hw = aw = dr = 0
        for it in h2h_raw[-10:]:
            if not isinstance(it, dict):
                continue
            home_n = _coalesce(it, "home_team") or _coalesce(it.get("homeTeam") or {}, "name")
            away_n = _coalesce(it, "away_team") or _coalesce(it.get("awayTeam") or {}, "name")
            h_g = _safe_int(_coalesce(it, "home_score", "home_goals"))
            a_g = _safe_int(_coalesce(it, "away_score", "away_goals"))
            if h_g is None or a_g is None:
                continue
            hh.append({
                "date":       it.get("date"),
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
        if hh:
            set_field(env, "h2h.matches",   hh, sample_size=len(hh))
            set_field(env, "h2h.home_wins", hw, sample_size=len(hh))
            set_field(env, "h2h.away_wins", aw, sample_size=len(hh))
            set_field(env, "h2h.draws",     dr, sample_size=len(hh))
            set_field(env, "h2h.sample",    len(hh), sample_size=len(hh))

    # ── Odds (pass-through; cascade selector merges across sources) ──
    odds_raw = raw.get("odds")
    if isinstance(odds_raw, dict):
        for market, sels in odds_raw.items():
            if sels is None:
                continue
            set_field(env, f"odds.{str(market)}", sels)

    codes: list[str] = []
    if env["home"] or env["away"] or env["h2h"] or env["odds"]:
        if env["home"] and env["away"]:
            codes.append(RC_MAPPING_OK)
        else:
            codes.append(RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
