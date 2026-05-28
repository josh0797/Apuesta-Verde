"""Editorial normalizer — raw scrape → EditorialContextSignal payload.

Given raw items emitted by the Scrapy spider (one item per article page),
this module produces the canonical signal structure consumed by:
  - the analyst engine (attach to match payload before LLM)
  - the UI ("Contexto editorial" block)
  - MongoDB cache (editorial_context_signals)

It is the deterministic layer on top of the heuristic signal mapper.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from . import editorial_signal_mapper as sm
from .match_key import canonical_match_key, normalize_team_name


# ── Scoring helpers ─────────────────────────────────────────────────────
def editorial_freshness_score(published_at: Optional[str], *, now: Optional[datetime] = None) -> int:
    """0-100 freshness score.

    Rules:
      - last 24h  → 100
      - 24-48h    → 80
      - 48-72h    → 60
      - 72-168h   → 30 (still usable for season-long context)
      - >168h     → 10 (stale, low signal)
      - missing   → 50 (we cannot prove it's fresh OR stale)
    """
    if not published_at:
        return 50
    n = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return 50
    age = (n - ts).total_seconds() / 3600.0
    if age < 0:
        return 50    # published in the future = clock skew
    if age < 24:
        return 100
    if age < 48:
        return 80
    if age < 72:
        return 60
    if age < 168:
        return 30
    return 10


# Static reliability per source. Hand-tuned starting points; eventually
# should be replaced by tracked accuracy (sourceReliabilityScore).
_SOURCE_BASE_RELIABILITY = {
    "sportytrader_es": 65,
    "besoccer_es":     70,
}


def source_reliability_score(source: str, *, body_length: int = 0, has_market: bool = False) -> int:
    """0-100 reliability score.

    Static per-source baseline + small bonuses for richer articles.
    Will be augmented by historical-accuracy tracking in a later phase.
    """
    base = _SOURCE_BASE_RELIABILITY.get(source, 50)
    if body_length >= 1500:
        base += 5
    if has_market:
        base += 5
    return max(0, min(100, base))


# Narrative-bias detection: high score = MORE biased / hype
_HYPE_PATTERNS = [
    r"\bapuesta\s+segura\b",
    r"\bclar\w+\s+favorito\b",
    r"\bno\s+hay\s+color\b",
    r"\bgan(?:ar[aá]|a)\s+sin\s+problema\b",
    r"\baplastante\b",
    r"\binbatible\b",
    r"\bgoleada\s+asegurada\b",
    r"\bdebe\s+ganar\b",
    r"\bobligado\s+a\s+ganar\b",
]
_HYPE_RE = [re.compile(p, re.IGNORECASE) for p in _HYPE_PATTERNS]


def narrative_bias_score(raw_text: str) -> int:
    """0-100; higher = more sensational / less data-driven.

    Counts hype tropes; ignores once-only mentions (a single 'favorito'
    is normal language). Score saturates at 100.
    """
    if not raw_text:
        return 0
    hits = 0
    for pat in _HYPE_RE:
        hits += len(pat.findall(raw_text))
    return max(0, min(100, hits * 18))


# ── Confidence inference ─────────────────────────────────────────────────
def _infer_confidence(signal_buckets: dict[str, list[dict]], narrative_bias: int) -> str:
    """Map signal counts → 'high' | 'medium' | 'low'.

    Heuristic:
      - high   if >=2 factual + >=1 motivation AND narrative_bias <= 30
      - low    if narrative_bias >= 70 OR only opinions/no factuals
      - medium otherwise
    """
    factuals = len(signal_buckets.get("FACTUAL_CONTEXT", []))
    motivs   = len(signal_buckets.get("MOTIVATION_NOTE", []))
    opinions = len(signal_buckets.get("OPINION", []))
    if narrative_bias >= 70 or (factuals == 0 and motivs == 0 and opinions > 0):
        return "low"
    if factuals >= 2 and motivs >= 1 and narrative_bias <= 30:
        return "high"
    return "medium"


def _bucket_signals(signals: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in signals:
        t = s.get("signal_type") or "OPINION"
        out.setdefault(t, []).append(s)
    return out


# ── Public API ───────────────────────────────────────────────────────────
def build_editorial_context_signal(
    *,
    raw: dict,
    sport: str,
    home_team: Optional[str],
    away_team: Optional[str],
    league: Optional[str],
    kickoff_iso: Optional[str],
) -> dict:
    """Convert a raw scrape dict into a normalized EditorialContextSignal.

    The raw shape is whatever the spider emits:
        {
            "source":         str,
            "source_url":     str,
            "published_at":   str (iso) or None,
            "title":          str,
            "raw_text":       str,
            "matched_home":   str (best-effort team name found in title/text),
            "matched_away":   str,
            ...
        }
    """
    raw       = raw or {}
    title     = (raw.get("title") or "").strip()
    body      = (raw.get("raw_text") or "").strip()
    full_text = f"{title}\n\n{body}" if title else body
    summary   = body[:600] + ("…" if len(body) > 600 else "")

    signals = sm.extract_signals_from_text(full_text)
    buckets = _bucket_signals(signals)

    pred_score = sm.extract_predicted_score(full_text)
    market     = sm.extract_market_suggestion(full_text)

    fresh = editorial_freshness_score(raw.get("published_at"))
    bias  = narrative_bias_score(full_text)
    rel   = source_reliability_score(
        raw.get("source") or "",
        body_length=len(body),
        has_market=bool(market and market.get("market")),
    )
    usable = (fresh >= 30) and (rel >= 40)

    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)
    match_key = canonical_match_key(sport, home_team, away_team, kickoff_iso)

    # Pull MOTIVATION_NOTE & INJURY_NOTE & WARNING grouped texts for the UI.
    motivation_notes = [s["text"] for s in buckets.get("MOTIVATION_NOTE", [])]
    injury_notes     = [s["text"] for s in buckets.get("INJURY_NOTE", [])]
    risk_notes       = [s["text"] for s in buckets.get("WARNING", [])]
    factual_notes    = [s["text"] for s in buckets.get("FACTUAL_CONTEXT", [])]

    # editorial_prediction = the FIRST high-confidence opinion or market
    editorial_prediction: Optional[str] = None
    for s in signals:
        if s.get("signal_type") in ("MARKET_SUGGESTION", "SCORE_PREDICTION") and s.get("confidence", 0) >= 0.65:
            editorial_prediction = s["text"]
            break
    if not editorial_prediction and motivation_notes:
        editorial_prediction = motivation_notes[0]

    confidence_from_editorial = _infer_confidence(buckets, bias)

    return {
        "id":                    str(uuid.uuid4()),
        "sport":                 sport,
        "match_key":             match_key,
        "home_team":             home_team,
        "home_team_normalized":  home_norm,
        "away_team":             away_team,
        "away_team_normalized":  away_norm,
        "league":                league,
        "source":                raw.get("source"),
        "source_url":            raw.get("source_url"),
        "published_at":          raw.get("published_at"),
        "scraped_at":            raw.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
        "language":              raw.get("language") or "es",
        "title":                 title or None,
        "raw_text":              body[:4000],   # cap to keep doc size bounded
        "summary":                summary,
        "editorial_prediction":  editorial_prediction,
        "predicted_score":       pred_score,
        "suggested_market":      (market or {}).get("market"),
        "suggested_selection":   None,
        "suggested_odds":        (market or {}).get("odds"),
        "motivation_notes":      motivation_notes,
        "injury_notes":          injury_notes,
        "risk_notes":            risk_notes,
        "factual_notes":         factual_notes,
        "signals_structured":    signals,
        "confidence":            confidence_from_editorial,
        "freshness_score":       fresh,
        "reliability_score":     rel,
        "narrative_bias_score":  bias,
        "usable_for_analysis":   bool(usable),
        "hash":                  _hash_signal(match_key, raw.get("source_url") or title),
    }


def _hash_signal(match_key: str, fingerprint: Optional[str]) -> str:
    """Stable dedupe key: SHA1 of '(match_key)|(fingerprint)'."""
    payload = f"{match_key}|{fingerprint or ''}".encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()


def build_consensus(signals: list[dict]) -> dict:
    """Aggregate a LIST of EditorialContextSignal payloads into a per-match consensus.

    Returns:
        {
            "available":          bool,
            "sources_count":      int,
            "sources":            list[str],
            "signals":            list (the input echoed back),
            "consensus_market":   str | None,    # majority-vote market
            "consensus_direction":"home" | "away" | "draw" | None,
            "motivation_notes":   list[str],
            "risks":              list[str],
            "injury_notes":       list[str],
            "factual_notes":      list[str],
            "contradiction_flags":list[str],     # e.g. ['score_disagreement']
            "freshness_score":    int            # max across signals
            "reliability_score":  int            # avg across signals
            "narrative_bias_score": int          # max across signals (most biased)
        }
    """
    if not signals:
        return {
            "available":             False,
            "sources_count":         0,
            "sources":               [],
            "signals":               [],
            "consensus_market":      None,
            "consensus_direction":   None,
            "motivation_notes":      [],
            "risks":                 [],
            "injury_notes":          [],
            "factual_notes":         [],
            "contradiction_flags":   [],
            "freshness_score":       0,
            "reliability_score":     0,
            "narrative_bias_score":  0,
        }

    markets: dict[str, int] = {}
    scores:  dict[str, int] = {}
    motivation_notes: list[str] = []
    injury_notes:     list[str] = []
    risks:            list[str] = []
    factual_notes:    list[str] = []
    sources: list[str] = []
    freshness_scores:    list[int] = []
    reliability_scores:  list[int] = []
    bias_scores:         list[int] = []

    for s in signals:
        sources.append(s.get("source") or "unknown")
        if s.get("suggested_market"):
            markets[s["suggested_market"]] = markets.get(s["suggested_market"], 0) + 1
        if s.get("predicted_score"):
            scores[s["predicted_score"]] = scores.get(s["predicted_score"], 0) + 1
        motivation_notes.extend(s.get("motivation_notes") or [])
        injury_notes.extend(s.get("injury_notes") or [])
        risks.extend(s.get("risk_notes") or [])
        factual_notes.extend(s.get("factual_notes") or [])
        if isinstance(s.get("freshness_score"), int):
            freshness_scores.append(s["freshness_score"])
        if isinstance(s.get("reliability_score"), int):
            reliability_scores.append(s["reliability_score"])
        if isinstance(s.get("narrative_bias_score"), int):
            bias_scores.append(s["narrative_bias_score"])

    consensus_market = max(markets.items(), key=lambda kv: kv[1])[0] if markets else None
    contradictions: list[str] = []
    if len(scores) >= 2:
        contradictions.append("score_disagreement")
    if len(markets) >= 2 and consensus_market and markets[consensus_market] == 1:
        contradictions.append("market_disagreement")

    # Direction is inferred from consensus market or score keywords
    direction: Optional[str] = None
    for s in signals:
        ed = (s.get("editorial_prediction") or "").lower()
        if "home" in ed or s.get("home_team") and s["home_team"].lower() in ed:
            direction = "home"
            break
        if "away" in ed or s.get("away_team") and s["away_team"].lower() in ed:
            direction = "away"
            break
        if "empate" in ed or "draw" in ed:
            direction = "draw"
            break

    return {
        "available":             True,
        "sources_count":         len(set(sources)),
        "sources":               sorted(set(sources)),
        "signals":               signals,
        "consensus_market":      consensus_market,
        "consensus_direction":   direction,
        "motivation_notes":      _dedupe_keep_order(motivation_notes)[:10],
        "risks":                 _dedupe_keep_order(risks)[:10],
        "injury_notes":          _dedupe_keep_order(injury_notes)[:10],
        "factual_notes":         _dedupe_keep_order(factual_notes)[:15],
        "contradiction_flags":   contradictions,
        "freshness_score":       max(freshness_scores) if freshness_scores else 0,
        "reliability_score":     int(round(sum(reliability_scores) / len(reliability_scores))) if reliability_scores else 0,
        "narrative_bias_score":  max(bias_scores) if bias_scores else 0,
    }


def _dedupe_keep_order(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        key = x.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(x.strip())
    return out


__all__ = [
    "editorial_freshness_score",
    "source_reliability_score",
    "narrative_bias_score",
    "build_editorial_context_signal",
    "build_consensus",
]
