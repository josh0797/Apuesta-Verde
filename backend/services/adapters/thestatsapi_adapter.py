"""Sprint-F98 · TheStatsAPI → F74 envelope adapter (pure).

TheStatsAPI is the **primary source** for:
  * xG / xGA (high-quality, calibrated)
  * structural team_stats (averages over the season)
  * standings (we don't surface here — lives in F84.d)

It's a **secondary source** for shots/possession (still better than
proxy estimates when SofaScore is unavailable).

Expected raw shape (our internal canonical TheStatsAPI wrapper)::

    raw = {
        "match_id":      "abc",
        "team_stats":    {"home": {...avg_xg, avg_shots...}, "away": {...}},
        "home_form":     [{...}, ...],    # optional
        "away_form":     [{...}, ...],    # optional
        "h2h":           [{...}, ...],    # optional
        "odds":          {...},           # optional (TheStatsAPI mirrors
                                            # the-odds-api)
    }
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

SOURCE = "thestatsapi"

# Keys TheStatsAPI uses for xG-related metrics (seen across versions).
_XG_KEYS  = ("expected_goals_per_match", "xg_per_match", "xg",
              "expected_goals_for", "avg_xg", "xG", "xg_for")
_XGA_KEYS = ("expected_goals_against_per_match", "xga_per_match", "xga",
              "expected_goals_against", "avg_xga", "xGA", "xg_against")
_SHOTS_KEYS = ("shots_per_match", "avg_shots", "total_shots_per_match",
                "shots", "shots_total")
_SOT_KEYS   = ("shots_on_target_per_match", "avg_shots_on_target",
                "sot_per_match", "shots_on_target")
_POSS_KEYS  = ("possession_avg", "avg_possession", "possession",
                "possession_per_match")


def _first_present(d: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _from_team_stats(env: dict, side: str, stats: Any) -> None:
    if not isinstance(stats, dict):
        return
    xg  = _safe_float(_first_present(stats, _XG_KEYS))
    xga = _safe_float(_first_present(stats, _XGA_KEYS))
    sh  = _safe_float(_first_present(stats, _SHOTS_KEYS))
    sot = _safe_float(_first_present(stats, _SOT_KEYS))
    pos = _safe_float(_first_present(stats, _POSS_KEYS))
    # team_stats is season-average; we project onto the L5 slot AS A
    # FALLBACK ONLY (the cascade selector will prefer L5 from
    # SofaScore when available). To make the priority explicit we tag
    # provenance with sample_size=None signalling "season avg".
    if xg  is not None:
        set_field(env, f"{side}.xg_for_l5",     xg)
    if xga is not None:
        set_field(env, f"{side}.xg_against_l5", xga)
    if sh  is not None:
        set_field(env, f"{side}.shots_for_l5",   sh)
    if sot is not None:
        set_field(env, f"{side}.shots_on_target_l5", sot)
    if pos is not None:
        set_field(env, f"{side}.possession_avg_l5", pos)


def _coalesce(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _normalise_form(raw_form: Any, *, team_name: str = "", n: int = 5) -> tuple[list[dict], list[str]]:
    if not isinstance(raw_form, list):
        return [], []
    out: list[dict] = []
    form: list[str] = []
    for item in _last_n(raw_form, n):
        if not isinstance(item, dict):
            continue
        home_n = _coalesce(item, "home_team") or _coalesce(item.get("home") or {}, "name")
        away_n = _coalesce(item, "away_team") or _coalesce(item.get("away") or {}, "name")
        h_g = _safe_int(_coalesce(item, "home_score", "home_goals"))
        a_g = _safe_int(_coalesce(item, "away_score", "away_goals"))
        if h_g is None or a_g is None:
            continue
        is_home = (team_name or "").strip().lower() == str(home_n or "").strip().lower()
        scored, conceded = (h_g, a_g) if is_home else (a_g, h_g)
        letter = _normalize_form_letter(item.get("result"), team_side=("home" if is_home else "away"),
                                          scored=scored, conceded=conceded)
        if letter:
            form.append(letter)
        out.append({
            "date":          item.get("date"),
            "home_team":     home_n,
            "away_team":     away_n,
            "home_goals":    h_g,
            "away_goals":    a_g,
            "team_scored":   scored,
            "team_conceded": conceded,
        })
    return out, form


def adapt_thestatsapi_to_f74(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_NOT_DICT)
    if not raw:
        return envelope_unavailable(source=SOURCE, reason=RC_RAW_EMPTY)

    env = new_envelope(source=SOURCE, available=True)
    env["sources"]["raw_keys"] = sorted([str(k) for k in raw.keys()])
    if raw.get("match_id") is not None:
        env["sources"]["match_id"] = raw.get("match_id")

    # ── Team-stats season-avg fallback ───────────────────────────────
    ts = raw.get("team_stats")
    if isinstance(ts, dict):
        _from_team_stats(env, "home", ts.get("home"))
        _from_team_stats(env, "away", ts.get("away"))

    # ── Recent fixtures (when present) ───────────────────────────────
    home_team = (raw.get("home_team_name") or "")
    away_team = (raw.get("away_team_name") or "")
    fh, form_h = _normalise_form(raw.get("home_form"), team_name=home_team)
    fa, form_a = _normalise_form(raw.get("away_form"), team_name=away_team)
    if fh:
        set_field(env, "home.recent_fixtures", fh, sample_size=len(fh))
        gf = [m["team_scored"]   for m in fh if m.get("team_scored")   is not None]
        ga = [m["team_conceded"] for m in fh if m.get("team_conceded") is not None]
        if gf:
            set_field(env, "home.goals_scored_l5",   sum(gf) / len(gf), sample_size=len(gf))
        if ga:
            set_field(env, "home.goals_conceded_l5", sum(ga) / len(ga), sample_size=len(ga))
    if form_h:
        set_field(env, "home.form_string_l5", "".join(form_h), sample_size=len(form_h))
    if fa:
        set_field(env, "away.recent_fixtures", fa, sample_size=len(fa))
        gf = [m["team_scored"]   for m in fa if m.get("team_scored")   is not None]
        ga = [m["team_conceded"] for m in fa if m.get("team_conceded") is not None]
        if gf:
            set_field(env, "away.goals_scored_l5",   sum(gf) / len(gf), sample_size=len(gf))
        if ga:
            set_field(env, "away.goals_conceded_l5", sum(ga) / len(ga), sample_size=len(ga))
    if form_a:
        set_field(env, "away.form_string_l5", "".join(form_a), sample_size=len(form_a))

    # ── H2H ───────────────────────────────────────────────────────────
    h2h_raw = raw.get("h2h")
    if isinstance(h2h_raw, list) and h2h_raw:
        hh: list[dict] = []
        hw = aw = dr = 0
        for it in h2h_raw[-10:]:
            if not isinstance(it, dict):
                continue
            home_n = _coalesce(it, "home_team") or _coalesce(it.get("home") or {}, "name")
            away_n = _coalesce(it, "away_team") or _coalesce(it.get("away") or {}, "name")
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

    # ── Odds pass-through ────────────────────────────────────────────
    odds_raw = raw.get("odds")
    if isinstance(odds_raw, dict):
        for market, sels in odds_raw.items():
            if sels is None:
                continue
            set_field(env, f"odds.{str(market)}", sels)

    codes: list[str] = []
    if env["home"] or env["away"] or env["h2h"] or env["odds"]:
        codes.append(RC_MAPPING_OK if (env["home"] and env["away"]) else RC_MAPPING_PARTIAL)
    else:
        codes.append(RC_NO_USABLE_FIELDS)
        env["available"] = False
    return finalize_envelope(env, extra_codes=codes)
