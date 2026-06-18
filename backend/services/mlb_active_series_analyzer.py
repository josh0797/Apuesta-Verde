"""
MLB Active-Series Context Analyzer (Module #1) — Sprint D9.3-A hotfix.

Reads finished games of the current matchup from the past `days_back`
days (default 4) directly from MongoDB. Computes:

  - games_in_series, total_runs_avg, list, over/under counts vs line
  - bullpen pitch counts when present in the match doc
  - series_lean (OVER / UNDER / NEUTRAL)
  - series_override flag + reason
  - series_state ∈ {ACTIVE_SERIES_CONFIRMED, ACTIVE_SERIES_NO_COMPLETED_GAMES,
                    ACTIVE_SERIES_SCORE_MISSING, ACTIVE_SERIES_UNRESOLVED}

D9.3-A fix highlights
---------------------
* `_extract_runs` and `_extract_per_team_runs` NO LONGER coalesce
  missing keys to 0 (the previous behaviour caused phantom "0-0 final"
  games to be counted as valid, contaminating the projection through
  `apply_series_degradation`).
* Strict status validation: only docs whose status is in
  {FINAL, COMPLETED, GAME_OVER, FT, "match finished", ...} are
  eligible. When the status field is missing, the doc is allowed only
  when *both* scores are present and non-null.
* Suspicious 0-0 guard for MLB: a final 0-0 MLB game is statistically
  rare (~once per 20 years per franchise); we mark such docs as
  suspicious (`SCORE_SUSPICIOUS_ZERO_ZERO`) and exclude them unless an
  explicit `score_confirmed=True` flag is on the doc.
* Honest fail-soft: when no valid completed games exist, the payload
  exposes `available=False` AND a clear `series_state` so the UI can
  render a truthful message instead of "Promedio: 0.0 carreras / Over
  rate: 0%".

Fail-soft
---------
Any exception or empty DB result returns a payload with
`available=False` and a defensive `series_state`. Downstream code
(`apply_series_degradation`, UI) must check `available` before using
the numeric fields.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── Series states ──────────────────────────────────────────────────────
SERIES_STATE_CONFIRMED         = "ACTIVE_SERIES_CONFIRMED"
SERIES_STATE_NO_COMPLETED      = "ACTIVE_SERIES_NO_COMPLETED_GAMES"
SERIES_STATE_SCORE_MISSING     = "ACTIVE_SERIES_SCORE_MISSING"
SERIES_STATE_UNRESOLVED        = "ACTIVE_SERIES_UNRESOLVED"

# Final / completed statuses accepted (case-insensitive). Includes the
# common variants from different sources (API-Sports, MLB statsapi,
# generic feeds). When the status field is absent on a doc, the doc is
# allowed only if BOTH scores are present and non-null.
FINAL_STATUSES_LOWER = frozenset({
    "final", "completed", "game_over", "game over",
    "ft", "finished", "match_finished", "match finished",
    "ended", "fc",      # API-Sports MLB final code
    "fin",
})

# Non-final / suspicious statuses we must reject explicitly.
NON_FINAL_STATUSES_LOWER = frozenset({
    "postponed", "cancelled", "canceled", "suspended",
    "rain delay", "delay", "ppd",
    "scheduled", "tbd", "live", "in progress", "in_progress",
    "pre", "pregame", "warmup",
    "abandoned", "forfeit",
})


def _normalise(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _team_match(doc: dict, home: str, away: str) -> bool:
    """Does this doc represent any game between the two teams (home/away interchangeable)?"""
    h = _normalise((doc.get("home_team") or {}).get("name") if isinstance(doc.get("home_team"), dict) else doc.get("home_team"))
    a = _normalise((doc.get("away_team") or {}).get("name") if isinstance(doc.get("away_team"), dict) else doc.get("away_team"))
    home_n, away_n = _normalise(home), _normalise(away)
    if not h or not a:
        return False
    return {h, a} == {home_n, away_n}


def _doc_status(doc: dict) -> Optional[str]:
    """Best-effort status extractor across the ingestion shapes.

    Looks for `status`, `match_status`, `state`, `fixture.status.long`,
    `fixture.status.short`, etc. Returns lowercased value or None.
    """
    for key in ("status", "match_status", "state", "game_status"):
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
        if isinstance(v, dict):
            for sub in ("long", "short", "name"):
                vv = v.get(sub)
                if isinstance(vv, str) and vv.strip():
                    return vv.strip().lower()
    fx = doc.get("fixture")
    if isinstance(fx, dict):
        s = fx.get("status")
        if isinstance(s, dict):
            for sub in ("long", "short", "name"):
                vv = s.get(sub)
                if isinstance(vv, str) and vv.strip():
                    return vv.strip().lower()
        if isinstance(s, str) and s.strip():
            return s.strip().lower()
    ls = doc.get("live_stats")
    if isinstance(ls, dict):
        s = ls.get("status")
        if isinstance(s, str) and s.strip():
            return s.strip().lower()
    return None


def _is_status_final(status: Optional[str]) -> tuple[bool, bool]:
    """Return (is_final, is_explicit_non_final).

    * (True, False)  → doc is explicitly final.
    * (False, True)  → doc is explicitly non-final/cancelled/etc.
    * (False, False) → status missing / ambiguous (caller decides).
    """
    if not status:
        return (False, False)
    s = status.strip().lower()
    if s in FINAL_STATUSES_LOWER:
        return (True, False)
    if s in NON_FINAL_STATUSES_LOWER:
        return (False, True)
    # Heuristic: "final" anywhere in the long status string.
    if "final" in s and "non" not in s and "before" not in s:
        return (True, False)
    return (False, False)


def _parse_int_strict(v: Any) -> Optional[int]:
    """Strict int parser — returns None when the value is missing/None/
    non-numeric. Crucially does NOT coalesce None/missing keys to 0."""
    if v is None:
        return None
    if isinstance(v, bool):  # bool is a subclass of int — guard.
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def _read_scores_strict(doc: dict) -> tuple[Optional[int], Optional[int]]:
    """Return (home_runs, away_runs) using STRICT key presence checks.

    The previous implementation used `path.get("home", 0)` which
    coalesced missing keys to 0 and produced phantom 0-0 finals. We now
    require the keys to be explicitly present AND parseable as int.
    Returns (None, None) when scores can't be determined.
    """
    for path in (doc.get("final_score"), doc.get("score")):
        if isinstance(path, dict) and ("home" in path) and ("away" in path):
            h = _parse_int_strict(path.get("home"))
            a = _parse_int_strict(path.get("away"))
            if h is not None and a is not None:
                return (h, a)
    ls = doc.get("live_stats")
    if isinstance(ls, dict):
        sc = ls.get("score")
        if isinstance(sc, dict) and ("home" in sc) and ("away" in sc):
            h = _parse_int_strict(sc.get("home"))
            a = _parse_int_strict(sc.get("away"))
            if h is not None and a is not None:
                return (h, a)
        # Box-score fields per side — accept only if BOTH keys present.
        hs = ls.get("home_stats") or {}
        as_ = ls.get("away_stats") or {}
        if isinstance(hs, dict) and isinstance(as_, dict):
            h_runs = hs.get("Runs") if "Runs" in hs else hs.get("runs")
            a_runs = as_.get("Runs") if "Runs" in as_ else as_.get("runs")
            h = _parse_int_strict(h_runs)
            a = _parse_int_strict(a_runs)
            if h is not None and a is not None:
                return (h, a)
    return (None, None)


def _doc_is_score_confirmed(doc: dict) -> bool:
    """Some upstream sources set an explicit `score_confirmed=True`
    when they verified the box score (e.g. settlement job). This is
    used to whitelist legitimate 0-0 finals (extremely rare in MLB)."""
    for k in ("score_confirmed", "scoreConfirmed", "settled"):
        v = doc.get(k)
        if isinstance(v, bool) and v:
            return True
    sett = doc.get("settlement") or {}
    if isinstance(sett, dict) and sett.get("confirmed") is True:
        return True
    return False


def _classify_doc(doc: dict, *, sport: str = "MLB") -> dict:
    """Classify a doc as VALID / NON_FINAL / SCORE_MISSING / SUSPICIOUS.

    Returns dict with: `eligible` (bool), `reason` (str or None),
    `home_runs` (int or None), `away_runs` (int or None).
    """
    status = _doc_status(doc)
    is_final, is_explicit_non_final = _is_status_final(status)
    home_runs, away_runs = _read_scores_strict(doc)

    # Hard reject: explicit non-final status (postponed, cancelled, live, etc.)
    if is_explicit_non_final:
        return {
            "eligible":  False,
            "reason":    "STATUS_NON_FINAL",
            "status":    status,
            "home_runs": home_runs,
            "away_runs": away_runs,
        }
    # No scores at all → score-missing.
    if home_runs is None or away_runs is None:
        return {
            "eligible":  False,
            "reason":    "SCORE_MISSING",
            "status":    status,
            "home_runs": home_runs,
            "away_runs": away_runs,
        }
    # Score present but status absent: require BOTH scores parseable
    # (already guaranteed) — we accept it as a soft-final since the
    # ingestion is known to archive matches without re-stamping
    # `status` in some flows. Suspicious 0-0 will still be filtered.
    if not is_final and not status:
        is_final = True  # promote to soft-final
    # Status present but not in known final set → reject.
    if not is_final:
        return {
            "eligible":  False,
            "reason":    "STATUS_UNKNOWN",
            "status":    status,
            "home_runs": home_runs,
            "away_runs": away_runs,
        }
    # Negative score → garbage.
    if home_runs < 0 or away_runs < 0:
        return {
            "eligible":  False,
            "reason":    "SCORE_NEGATIVE",
            "status":    status,
            "home_runs": home_runs,
            "away_runs": away_runs,
        }
    # MLB suspicious 0-0 guard — extremely rare in MLB; if not
    # explicitly confirmed, exclude.
    if str(sport).upper() == "MLB" and home_runs == 0 and away_runs == 0:
        if not _doc_is_score_confirmed(doc):
            return {
                "eligible":  False,
                "reason":    "SCORE_SUSPICIOUS_ZERO_ZERO",
                "status":    status,
                "home_runs": 0,
                "away_runs": 0,
            }
    return {
        "eligible":  True,
        "reason":    None,
        "status":    status,
        "home_runs": home_runs,
        "away_runs": away_runs,
    }


def _extract_per_team_runs(doc: dict, home_team: str, away_team: str,
                              classification: dict) -> Optional[dict]:
    """Return `{home, away, total, home_team, away_team, kickoff}`
    aligned to the TARGET orientation (so the UI always sees the same
    team on the same side regardless of who hosted that day).

    Uses the already-validated scores in `classification`.
    """
    raw_home = classification.get("home_runs")
    raw_away = classification.get("away_runs")
    if raw_home is None or raw_away is None:
        return None
    doc_home = _normalise(
        (doc.get("home_team") or {}).get("name") if isinstance(doc.get("home_team"), dict)
        else doc.get("home_team")
    )
    doc_away = _normalise(
        (doc.get("away_team") or {}).get("name") if isinstance(doc.get("away_team"), dict)
        else doc.get("away_team")
    )
    target_home = _normalise(home_team)
    target_away = _normalise(away_team)
    kickoff = doc.get("kickoff_iso") or doc.get("gameDate") or doc.get("date")

    if doc_home == target_home and doc_away == target_away:
        return {
            "home": raw_home, "away": raw_away,
            "total": raw_home + raw_away,
            "home_team": home_team, "away_team": away_team,
            "kickoff": kickoff,
        }
    if doc_home == target_away and doc_away == target_home:
        return {
            "home": raw_away, "away": raw_home,
            "total": raw_home + raw_away,
            "home_team": home_team, "away_team": away_team,
            "kickoff": kickoff,
        }
    return None


def _extract_bullpen_pitches(doc: dict, side: str) -> int:
    """Best-effort: read bullpen pitch counts when the upstream ingestion
    stored them on the match doc. Returns 0 when unknown."""
    bp = doc.get("bullpen_usage") or {}
    if isinstance(bp, dict):
        v = bp.get(f"{side}_pitches") or bp.get(side, {}).get("pitches")
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _empty_payload(series_state: str = SERIES_STATE_UNRESOLVED,
                     reason_codes: Optional[list[str]] = None,
                     reference_line: float = 9.5) -> dict:
    return {
        "available":           False,
        "series_state":        series_state,
        "games_in_series":     0,
        "total_runs_avg":      None,
        "total_runs_list":     [],
        "games_detail":        [],
        "over_rate":           None,
        "over_count":          None,
        "under_count":         None,
        "push_count":          None,
        "bullpen_pitches_home": 0,
        "bullpen_pitches_away": 0,
        "series_lean":         "NEUTRAL",
        "series_override":     False,
        "override_reason":     None,
        "next_game_number":    1,
        "reference_line":      reference_line,
        "reason_codes":        list(reason_codes or []),
        "excluded_docs":       [],
    }


async def get_active_series_context(
    db: Any,
    home_team: str,
    away_team: str,
    date_str: Optional[str] = None,
    *,
    days_back: int = 4,
    model_expected_runs: Optional[float] = None,
    over_under_line: float = 9.5,
    sport: str = "MLB",
) -> dict:
    """See module docstring.

    `date_str` is the kickoff of the NEXT game (`YYYY-MM-DD`); we look
    back from there. When None, falls back to `datetime.utcnow().date()`.

    Returns a payload that ALWAYS carries `series_state` so consumers
    (UI + `apply_series_degradation` chain) can render truthful
    fallbacks instead of bogus "0 carreras" data.
    """
    if db is None or not home_team or not away_team:
        return _empty_payload(SERIES_STATE_UNRESOLVED,
                              ["MISSING_DB_OR_TEAM_INPUTS"],
                              over_under_line)
    try:
        if date_str:
            try:
                ref = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except ValueError:
                ref = datetime.now(timezone.utc)
        else:
            ref = datetime.now(timezone.utc)
        from_ts = ref - timedelta(days=days_back)

        # We support three collections the ingestion pipeline writes to:
        #   • `finished_games` — settlement job output (when present).
        #   • `matches`         — current-day fixtures with `status=Final`.
        #   • `archived_live_matches` — the live ingester moves a match
        #     here when it finishes; the box-score lives in `live_stats`.
        collections = ["finished_games", "matches", "archived_live_matches"]
        candidates: list[dict] = []
        for coll_name in collections:
            try:
                coll = db[coll_name]
            except Exception:
                continue
            window_from = from_ts.isoformat().replace("+00:00", "")
            window_to   = ref.isoformat().replace("+00:00", "")
            query = {
                "sport": "baseball",
                "$or": [
                    {"kickoff_iso": {"$gte": window_from, "$lt": window_to}},
                    {"kickoff_iso": {"$gte": from_ts.isoformat(), "$lt": ref.isoformat()}},
                ],
            }
            try:
                async for d in coll.find(query).limit(40):
                    candidates.append(d)
            except Exception as exc:
                log.debug("active_series query on %s failed: %s", coll_name, exc)
        # Filter to this matchup.
        matched = [d for d in candidates if _team_match(d, home_team, away_team)]
        if not matched:
            return _empty_payload(SERIES_STATE_NO_COMPLETED,
                                  ["NO_MATCHUP_IN_WINDOW"],
                                  over_under_line)

        # Strict classification per doc.
        classifications: list[tuple[dict, dict]] = []
        for d in matched:
            cls = _classify_doc(d, sport=sport)
            classifications.append((d, cls))

        eligible_pairs = [(d, c) for d, c in classifications if c["eligible"]]
        excluded = [
            {"reason": c["reason"], "status": c.get("status"),
             "home_runs": c.get("home_runs"), "away_runs": c.get("away_runs")}
            for d, c in classifications if not c["eligible"]
        ]

        reason_codes: list[str] = []
        # Derive series state.
        if not eligible_pairs:
            # No eligible games — pick state based on the dominant
            # exclusion reason.
            reasons = [c["reason"] for _, c in classifications]
            if any(r == "SCORE_MISSING" for r in reasons):
                state = SERIES_STATE_SCORE_MISSING
            elif any(r in ("STATUS_NON_FINAL", "STATUS_UNKNOWN") for r in reasons):
                state = SERIES_STATE_NO_COMPLETED
            elif any(r == "SCORE_SUSPICIOUS_ZERO_ZERO" for r in reasons):
                state = SERIES_STATE_SCORE_MISSING
                reason_codes.append("SUSPICIOUS_ZERO_ZERO_EXCLUDED")
            else:
                state = SERIES_STATE_NO_COMPLETED
            payload = _empty_payload(state, reason_codes, over_under_line)
            payload["excluded_docs"] = excluded
            return payload

        # Build per-game breakdown — sorted oldest→newest.
        per_game_raw = [
            _extract_per_team_runs(d, home_team, away_team, c)
            for d, c in eligible_pairs
        ]
        per_game = [g for g in per_game_raw if g is not None]
        per_game.sort(key=lambda g: (g.get("kickoff") or ""))
        games_detail = []
        for idx, g in enumerate(per_game, start=1):
            games_detail.append({
                "game_number":   idx,
                "home":          g["home"],
                "away":          g["away"],
                "home_team":     g["home_team"],
                "away_team":     g["away_team"],
                "total_runs":    g["total"],
                "kickoff":       g.get("kickoff"),
                "summary":       f"G{idx}: {home_team} {g['home']} - {g['away']} {away_team} = {g['total']} carreras",
            })

        runs_list = [g["total"] for g in per_game]
        n = len(runs_list)
        avg = sum(runs_list) / n
        # Line-aware counts.
        line = float(over_under_line)
        over_count  = sum(1 for r in runs_list if r >  line)
        under_count = sum(1 for r in runs_list if r <  line)
        push_count  = n - over_count - under_count
        over_rate   = over_count / n  # back-compat for downstream consumers
        bullpen_home = max(
            (_extract_bullpen_pitches(d, "home") for d, _ in eligible_pairs),
            default=0,
        )
        bullpen_away = max(
            (_extract_bullpen_pitches(d, "away") for d, _ in eligible_pairs),
            default=0,
        )

        # ── Override rules ──
        override = False
        reason: Optional[str] = None
        lean = "NEUTRAL"
        if n >= 2:
            if avg > line + 2.0:
                lean = "OVER"
            elif avg < line - 2.0:
                lean = "UNDER"
            # Override #1: model violently underestimates the series avg.
            if model_expected_runs and avg > float(model_expected_runs) * 1.4:
                override = True
                lean = "OVER"
                reason = (f"Serie activa promedia {avg:.1f} runs vs ER "
                          f"{float(model_expected_runs):.1f} del modelo.")
            # Override #2: hard-cap — series averaging >12 runs is a
            # clear high-scoring environment regardless of the model.
            if avg > 12.0:
                override = True
                lean = "OVER"
                hard_reason = (f"Promedio de serie {avg:.1f} carreras > 12 "
                               f"— entorno claramente ofensivo.")
                reason = (reason + " " + hard_reason) if reason else hard_reason
            if bullpen_home > 80 or bullpen_away > 80:
                override = True
                reason = ((reason + " " if reason else "")
                          + f"Bullpens agotados (HOME {bullpen_home} pitches, "
                          f"AWAY {bullpen_away} pitches en 2 días).")

        if n < 3:
            reason_codes.append("LIMITED_SAMPLE_SERIES_SIGNAL")

        return {
            "available":           True,
            "series_state":        SERIES_STATE_CONFIRMED,
            "games_in_series":     n,
            "total_runs_avg":      round(avg, 2),
            "total_runs_list":     runs_list,
            "games_detail":        games_detail,
            "next_game_number":    n + 1,
            "over_rate":           round(over_rate, 2),
            "over_count":          over_count,
            "under_count":         under_count,
            "push_count":          push_count,
            "bullpen_pitches_home": bullpen_home,
            "bullpen_pitches_away": bullpen_away,
            "series_lean":         lean,
            "series_override":     override,
            "override_reason":     reason,
            "days_back":           days_back,
            "reference_line":      line,
            "reason_codes":        reason_codes,
            "excluded_docs":       excluded,
        }
    except Exception as exc:
        log.warning("get_active_series_context failed: %s", exc)
        return _empty_payload(SERIES_STATE_UNRESOLVED,
                              [f"EXCEPTION:{type(exc).__name__}"],
                              over_under_line)


__all__ = [
    "get_active_series_context",
    "SERIES_STATE_CONFIRMED",
    "SERIES_STATE_NO_COMPLETED",
    "SERIES_STATE_SCORE_MISSING",
    "SERIES_STATE_UNRESOLVED",
]
