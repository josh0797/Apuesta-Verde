"""Generate `possible_alternative_markets` for a discarded match.

Sport-aware rules — never proposes a market that doesn't exist for the
match's sport:
    football   → Double Chance, Under/Over goles, Both Teams To Score,
                 Asian Handicap, Team Total Under/Over, Corners totals.
    basketball → Spread alt, Total Points Under/Over, Team Total
                 Under/Over, First Half Spread, Race To X points.
    baseball   → Run Line ±1.5, F5 Moneyline, F5 Total, Total Runs
                 Under/Over, Team Total Runs Under, Pitcher Strikeouts.

The function reads the discard `reason` and the match's signals to pick
the alternatives that BEST fit the explanation, so we don't bombard the
user with the whole sport-specific menu.
"""
from __future__ import annotations

from typing import Any, Optional

_ALT_MARKETS_BY_SPORT: dict[str, list[str]] = {
    "football": [
        "Doble Oportunidad", "Under 2.5", "Under 3.5", "Under 4.5",
        "Over 2.5", "Ambos equipos anotan: NO",
        "Hándicap asiático ±0.5", "Hándicap asiático ±1.0",
        "Team Total Under", "Team Total Over",
        "Total de córners Over 9.5", "Total de córners Under 10.5",
    ],
    "basketball": [
        "Total Points Under", "Total Points Over",
        "Spread alt ±5.5", "Spread alt ±8.5",
        "Team Total Under", "Team Total Over",
        "1H Spread", "Race To 20", "Race To 30",
    ],
    "baseball": [
        "Run Line +1.5", "Run Line -1.5",
        "F5 Moneyline", "F5 Total Runs Over", "F5 Total Runs Under",
        "Total Runs Under 8.5", "Total Runs Over 8.5",
        "Team Total Runs Under", "Pitcher Strikeouts Over",
    ],
}


def _bucket_for(sport: str) -> list[str]:
    return _ALT_MARKETS_BY_SPORT.get((sport or "football").lower(), [])


def _filter_by_reason(menu: list[str], reason_lower: str, signals: list[dict]) -> list[str]:
    """Narrow the menu based on the textual discard reason + signals."""
    # When the reason hints at "favorito sobrevalorado" / "cuota corta" we
    # tend to recommend protective markets (DC / Run Line / Spread alt).
    protective_kw   = ("favorito", "cuota corta", "sin valor", "edge negativ", "implied", "no value")
    under_kw        = ("under", "tendencia under", "marcadores bajos", "pitcher duel", "pace bajo")
    over_kw         = ("over", "marcadores altos", "pace alto", "ofensiva")
    corners_kw      = ("corner", "córner")
    h2h_kw          = ("h2h", "histórico", "historial")
    motivation_kw   = ("motivación", "motivation")

    signal_codes = {s.get("code") for s in (signals or []) if isinstance(s, dict)}

    selected: list[str] = []
    keep = selected.append

    if any(k in reason_lower for k in protective_kw) or "FAVORITE_NAME_BIAS" in signal_codes:
        for m in menu:
            if any(tok in m for tok in ("Doble", "Run Line", "Spread alt", "Hándicap")):
                keep(m)

    if "UNDER_TREND_DETECTED" in signal_codes or "PITCHER_DUEL_SIGNAL" in signal_codes \
            or any(k in reason_lower for k in under_kw):
        for m in menu:
            if "Under" in m or m.startswith("F5"):
                keep(m)

    if "PACE_OVER_SIGNAL" in signal_codes or any(k in reason_lower for k in over_kw):
        for m in menu:
            if "Over" in m:
                keep(m)

    if "CORNER_VOLUME_DETECTED" in signal_codes or any(k in reason_lower for k in corners_kw):
        for m in menu:
            if "córner" in m or "corner" in m.lower():
                keep(m)

    if "TEAM_TOTAL_UNDER_SIGNAL" in signal_codes:
        for m in menu:
            if "Team Total Under" in m:
                keep(m)

    if "BULLPEN_FATIGUE_SIGNAL" in signal_codes:
        for m in menu:
            if m.startswith("F5") or "Under" in m:
                keep(m)

    # Dedup but preserve order.
    seen = set()
    result: list[str] = []
    for m in selected:
        if m not in seen:
            seen.add(m)
            result.append(m)

    # Fallback: when no signal/reason hint matched, return a sensible
    # default short-list per sport (so the UI never shows an empty array
    # for a discarded match that DID have signals).
    if not result:
        if signal_codes or reason_lower:
            result = menu[:3]
    return result[:5]


def generate_alternatives(
    discard_entry: dict,
    sport: str,
    *,
    signals: Optional[list[dict]] = None,
) -> list[str]:
    """Return up to 5 alternative market suggestions for a discarded match.

    Parameters
    ----------
    discard_entry : dict from `summary.discarded_market` /
                    `discarded_motivation` / `incomplete_data`.
    sport         : 'football' | 'basketball' | 'baseball'.
    signals       : the `editorial_context_signals` already aggregated for
                    this match (optional but improves precision).

    Returns
    -------
    list[str]
        Markets the user can review manually. Empty list when no sport
        bucket exists (defensive).
    """
    menu = _bucket_for(sport)
    if not menu:
        return []
    reason = str(discard_entry.get("reason") or discard_entry.get("missing") or "").lower()
    return _filter_by_reason(menu, reason, signals or [])


def attach_alternatives_to_summary(summary: dict, sport: str,
                                    match_lookup: dict | None = None) -> int:
    """Mutates `summary` in place, attaching `possible_alternative_markets`
    to every entry in discarded_market / discarded_motivation /
    incomplete_data. Returns the number of entries annotated.

    Phase F66 — also attaches the internal ``editorial_prediction`` to
    every football entry. The editorial engine is fail-soft and ONLY
    requires the match payload to exist; it surfaces 4 sections
    (corners / goals / key_trends / probable_score) and replaces the
    runtime dependency on Scores24 in the UI.

    The function is fail-soft — any per-entry crash is swallowed.
    """
    if not isinstance(summary, dict):
        return 0
    # Phase F66 — preload editorial engine once (import is cheap but a
    # module-level cache avoids repeated lookups across hundreds of picks).
    editorial_fn = None
    if sport == "football":
        try:
            from services.football_editorial_prediction import (
                generate_football_editorial_prediction as _editorial,
            )
            editorial_fn = _editorial
        except Exception:  # noqa: BLE001
            editorial_fn = None
    match_lookup = match_lookup or {}

    count = 0
    for bucket_key in ("discarded_market", "discarded_motivation", "incomplete_data"):
        bucket = summary.get(bucket_key) or []
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            try:
                signals = entry.get("editorial_context_signals") or []
                alts = generate_alternatives(entry, sport, signals=signals)
                entry["possible_alternative_markets"] = alts
                entry["user_review_note"] = _build_review_note(entry, alts)
                # Phase F66 — internal editorial prediction.
                if editorial_fn and not entry.get("editorial_prediction"):
                    # Phase F69 — pick the HYDRATED match doc first (with
                    # home_team / away_team / xG / L5-L15 stats) so the
                    # engine can produce a match-specific report. We then
                    # *merge in* the discard fields (reason, odds, edge,
                    # implied prob, fragility) so the engine can also
                    # render a "discard_reason_narrative" tied to this
                    # entry's actual market trap.
                    hydrated = match_lookup.get(entry.get("match_id"))
                    if hydrated is None and entry.get("match_id") is not None:
                        hydrated = match_lookup.get(str(entry.get("match_id")))
                    match_doc = dict(hydrated) if isinstance(hydrated, dict) else {}
                    # Merge discard-side context — explicit field names so we
                    # never clobber hydrated team/stats data.
                    for _k in (
                        "match_id", "match_label", "reason",
                        "odds", "estimated_probability", "implied_probability",
                        "edge", "fragility_score", "market_evaluated",
                        "discard_strength", "discard_reason", "confidence",
                    ):
                        if entry.get(_k) is not None and match_doc.get(_k) is None:
                            match_doc[_k] = entry.get(_k)
                    # Always pass match_id from the entry as canonical id.
                    if entry.get("match_id") is not None:
                        match_doc["match_id"] = entry["match_id"]
                    try:
                        # Phase F74-post — adaptador editorial + normalizador
                        # TheStatsAPI antes de invocar el motor editorial.
                        try:
                            from services.football_data_enrichment_normalizer import (
                                normalize_football_data_enrichment,
                            )
                            from services.football_editorial_payload_adapter import (
                                build_editorial_ready_match_payload,
                            )
                            normalize_football_data_enrichment(match_doc)
                            editorial_payload = build_editorial_ready_match_payload(match_doc)
                        except Exception:  # noqa: BLE001
                            editorial_payload = match_doc

                        editorial = editorial_fn(
                            editorial_payload,
                            h2h_matches=(editorial_payload.get("h2h_recent")
                                         if isinstance(editorial_payload, dict) else None),
                        )
                        # Propagar debug block del adapter al editorial.
                        if (isinstance(editorial_payload, dict)
                                and editorial_payload.get("internal_analysis_debug")
                                and isinstance(editorial, dict)):
                            editorial["internal_analysis_debug"] = editorial_payload[
                                "internal_analysis_debug"
                            ]
                        # Phase F82 — propagar rich H2H context.
                        if (isinstance(editorial_payload, dict)
                                and editorial_payload.get("h2h_context")
                                and isinstance(editorial, dict)):
                            editorial["h2h_context"] = editorial_payload["h2h_context"]
                        # Phase F82.1-adjust — propagar corners_snapshot
                        # para que la UI detecte el estado PENDING.
                        if (isinstance(editorial_payload, dict)
                                and editorial_payload.get("corners_snapshot")
                                and isinstance(editorial, dict)):
                            editorial["corners_snapshot"] = editorial_payload[
                                "corners_snapshot"
                            ]
                        entry["editorial_prediction"] = editorial
                    except Exception:  # noqa: BLE001
                        # Never let a single bad payload poison the whole
                        # summary annotation pass.
                        pass
                count += 1
            except Exception:
                continue

    # Phase F69 — Intra-run anti-duplicate scan over the editorial blocks.
    # When two entries share >85% of their normalised editorial text, we
    # flag both as generic fallbacks so the UI can suppress them.
    try:
        from services.football_editorial_prediction import (
            detect_duplicate_internal_editorials,
        )
        detect_duplicate_internal_editorials(summary)
    except Exception:  # noqa: BLE001
        # Anti-duplicate is purely advisory; never break the summary.
        pass

    return count


def _build_review_note(entry: dict, alts: list[str]) -> str:
    """Short natural-language note for the user, explaining why the
    engine didn't recommend AND what they could review manually.
    """
    label = entry.get("match_label") or "este partido"
    reason = entry.get("reason") or entry.get("missing") or "sin valor directo"
    if not alts:
        return f"El engine no recomendó {label} ({reason}). Sin mercados alternativos claros."
    head = alts[0]
    rest = ", ".join(alts[1:3])
    extra = f" También revisa: {rest}." if rest else ""
    return (
        f"El engine no recomendó {label} ({reason}). "
        f"Podrías revisar manualmente: {head}.{extra}"
    )


__all__ = [
    "generate_alternatives",
    "attach_alternatives_to_summary",
]
