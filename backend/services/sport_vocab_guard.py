"""Sport-Vocabulary Guardrail — final safety net against terminology leakage.

Problem this solves
-------------------
Even with sport-specific prompts and per-sport branches in the analytical
engine, occasional terminology leaks into pick payloads:

  • Baseball picks recommending "Más de 2.5 goles" or "Corners Over 10.5"
  • Basketball picks talking about "córners" or "goles"
  • Football picks using "carreras" / "innings"

These leaks come from three sources:
  1. LLM hallucinations against the prompt
  2. Reused football-only modules (e.g. corner_market_layer, under_market_scan)
     accidentally invoked for non-football sports
  3. Cached historical picks from older runs before the sport branches existed

This module is a pure-Python post-processor that runs AFTER all other
layers (market guardrail, moneyball, rescue) and:

  • Detects forbidden vocabulary per sport in `recommendation`, `reasoning`,
    `risks`, and `market_label`
  • Re-routes contaminated picks from `picks` → `summary.discarded_market`
    with reason `SPORT_VOCAB_LEAK`
  • Adds a `_pipeline.sport_vocab_guard` audit entry so the UI can show
    "X picks discarded because they used the wrong sport's vocabulary"

It is intentionally STRICT: any leak = discard. Better to lose a pick
than to surface "Apostar Más de 2.5 goles" on a baseball card.
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("sport_vocab_guard")


# ── Forbidden vocabulary per sport ──────────────────────────────────────────
# Each entry is a list of regex patterns (case-insensitive) that should
# NEVER appear in a pick for that sport. If any pattern matches anywhere
# in the pick payload's text fields, the pick is rerouted.
FORBIDDEN_TERMS: dict[str, list[str]] = {
    "baseball": [
        r"\bgol\b", r"\bgoles\b", r"\bgoal\b", r"\bgoals\b",
        r"\bcorner(s)?\b", r"\bcórner(es)?\b", r"\bcorner kick\b",
        r"\btarjeta(s)?\b", r"\byellow card\b", r"\bred card\b",
        r"\bBTTS\b", r"\bambos equipos marcan\b",
        r"\bpenalti\b", r"\bpenalty kick\b",
        r"\boffside\b", r"\bfuera de juego\b",
        r"\bhalf[- ]?time\b", r"\bdescanso\b", r"\b1er tiempo\b", r"\b2do tiempo\b",
        # Football-specific markets that have NO equivalent in baseball
        r"\bover 2\.5\b", r"\bunder 2\.5\b", r"\bover 3\.5\b", r"\bunder 3\.5\b",
        r"\b1X2\b", r"\bdoble oportunidad\b", r"\bdouble chance\b",
        r"\bhandicap asi[áa]tico\b", r"\basian handicap\b",
    ],
    "basketball": [
        r"\bgol\b", r"\bgoles\b", r"\bgoal\b", r"\bgoals\b",
        r"\bcorner(s)?\b", r"\bcórner(es)?\b",
        r"\bcarrera(s)?\b(?! pol[íi]tica)",   # "carreras" = runs in baseball
        r"\binning(s)?\b", r"\bentrada(s)?\b(?! libre| dramática)",
        r"\bbullpen\b", r"\bpitcher\b", r"\bbateador\b", r"\bhome run\b",
        r"\bBTTS\b", r"\bambos equipos marcan\b",
        r"\btarjeta(s)?\b", r"\byellow card\b", r"\bred card\b",
        r"\bpenalti\b",
        # Football-specific markets
        r"\bover 2\.5 goles\b", r"\bunder 2\.5 goles\b",
        r"\bover 3\.5 goles\b", r"\bunder 3\.5 goles\b",
        r"\bdoble oportunidad\b",
    ],
    "football": [
        # Most non-football terminology is uncommon for football LLM outputs.
        # We still guard the obvious leaks from basket/baseball-only modules.
        r"\bcarrera(s)?\b(?! pol[íi]tica)",
        r"\binning(s)?\b",
        r"\bbullpen\b", r"\bpitcher\b", r"\bbateador\b", r"\bhome run\b",
        r"\bquarter(s)?\b", r"\bcuarto Q\d\b",
        r"\brun line\b",
    ],
}


# Precompile for speed
_COMPILED: dict[str, list[re.Pattern]] = {
    sport: [re.compile(p, re.IGNORECASE) for p in patterns]
    for sport, patterns in FORBIDDEN_TERMS.items()
}


def _gather_text(pick: dict) -> str:
    """Concatenate all human-visible text fields in a pick into one string."""
    parts: list[str] = []
    rec = pick.get("recommendation") or {}
    for k in ("market", "selection", "label", "market_label", "reasoning"):
        v = rec.get(k)
        if isinstance(v, str):
            parts.append(v)
    for k in ("market", "selection", "reasoning", "market_label", "match_label"):
        v = pick.get(k)
        if isinstance(v, str):
            parts.append(v)
    risks = pick.get("risks") or []
    if isinstance(risks, list):
        for r in risks:
            if isinstance(r, str):
                parts.append(r)
    # Moneyball / rescue payloads also store human strings
    mb = pick.get("_moneyball") or {}
    for k in ("market", "selection", "reason", "explanation", "whyDirectMarketsFailed",
              "whyThisMarketIsSafer"):
        v = mb.get(k)
        if isinstance(v, str):
            parts.append(v)
    return " || ".join(parts)


def detect_vocab_leaks(pick: dict, sport: str) -> list[str]:
    """Return a list of forbidden terms (regex patterns) found in `pick`.

    Empty list = clean pick.
    """
    sport = (sport or "football").lower()
    patterns = _COMPILED.get(sport, [])
    if not patterns:
        return []
    text = _gather_text(pick)
    if not text:
        return []
    hits: list[str] = []
    for pat in patterns:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def apply_sport_vocab_guard(parsed: dict, sport: str = "football") -> dict:
    """Mutate `parsed` to discard picks containing forbidden vocabulary.

    Inputs
    ------
    parsed : dict with `picks` list + `summary.discarded_market` list
    sport  : "football" | "basketball" | "baseball"

    Returns
    -------
    The same `parsed` dict, mutated:
      • `picks` only keeps picks whose text fields don't contain any
        sport-forbidden term.
      • `summary.discarded_market` receives the rejected picks with
        `reason = "SPORT_VOCAB_LEAK ..."` and `_sport_vocab_guard` audit.
      • `_pipeline.sport_vocab_guard` summary added.
    """
    if not parsed or not isinstance(parsed, dict):
        return parsed
    sport_norm = (sport or "football").lower()
    if sport_norm not in _COMPILED:
        return parsed

    picks = list(parsed.get("picks") or [])
    summary = parsed.get("summary") or {}
    disc_mkt = list(summary.get("discarded_market") or [])

    kept: list[dict] = []
    rerouted: list[dict] = []

    for p in picks:
        leaks = detect_vocab_leaks(p, sport_norm)
        if leaks:
            p["_sport_vocab_guard"] = {
                "sport": sport_norm,
                "forbidden_terms_found": leaks,
                "rerouted": True,
            }
            disc_mkt.append({
                "match_id":   p.get("match_id"),
                "match_label": p.get("match_label"),
                "reason": (
                    f"SPORT_VOCAB_LEAK: pick de {sport_norm} contiene vocabulario "
                    f"de otro deporte: {', '.join(set(leaks[:5]))}. "
                    f"Descartado por el firewall de vocabulario."
                ),
                "_sport_vocab_guard": p["_sport_vocab_guard"],
                "_sport_vocab_guard_reroute": True,
                "original_pick": {
                    "market":     (p.get("recommendation") or {}).get("market"),
                    "selection":  (p.get("recommendation") or {}).get("selection"),
                },
            })
            rerouted.append(p)
        else:
            kept.append(p)

    parsed["picks"] = kept
    summary["discarded_market"] = disc_mkt
    parsed["summary"] = summary
    parsed.setdefault("_pipeline", {})
    parsed["_pipeline"]["sport_vocab_guard"] = {
        "sport":            sport_norm,
        "evaluated":        len(picks),
        "kept":             len(kept),
        "rerouted":         len(rerouted),
        "rerouted_match_ids": [p.get("match_id") for p in rerouted],
    }
    if rerouted:
        log.warning(
            "sport_vocab_guard[%s]: discarded %d/%d picks for terminology leak",
            sport_norm, len(rerouted), len(picks),
        )
    return parsed


__all__ = [
    "FORBIDDEN_TERMS",
    "detect_vocab_leaks",
    "apply_sport_vocab_guard",
]
