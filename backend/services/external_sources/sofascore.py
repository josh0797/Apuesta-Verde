"""SofaScore scraper — Sprint-D9-HOTFIX3 · Migrado a Scrape.do.

SofaScore es **fuertemente protegido por Cloudflare**. Antes este módulo
ruteaba sus llamadas vía Bright Data, pero el usuario decidió mover
TODO el scraping a Scrape.do (single-provider strategy, mejor visibilidad
de costos y políticas de gambling). Este archivo ahora consume
``services.scrape_do_client.fetch_via_scrapedo_result`` con
``render=False`` (los endpoints ``api.sofascore.com`` devuelven JSON
puro, no requieren JS rendering).

Fail-soft end-to-end:
  * Si ``SCRAPEDO_TOKEN`` no está configurado → ``skipped_evidence``.
  * Si el fetch falla (status != 2xx, timeout, etc.) → ``failed_evidence``.
  * Si el JSON no parsea o ``id`` no se resuelve → ``failed_evidence``.

F99 — SofaScore Wiring to F74 (binding del usuario):
  Este módulo además expone funciones públicas usadas por el pipeline F99:
    * :func:`resolve_sofascore_event`         — resuelve un ``event_id``.
    * :func:`fetch_sofascore_match_context`   — produce el **wrapper raw**
      canónico que consume el adapter F98 ``adapt_sofascore_to_f74``.

Reglas binding (NO romper):
  * Fail-soft total; nunca raise hacia el caller.
  * NUNCA devolver payload crudo HTML/JSON sin normalizar.
  * Logs en DEBUG para fallos esperados (blocked/timeout/schema drift).
    WARNING reservado a problemas sistémicos (caller decide).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .base import clean_text
from .schema import failed_evidence, make_evidence, skipped_evidence

log = logging.getLogger("external_sources.sofascore")

NAME = "sofascore"
APPLICABLE_SPORTS = {"football", "basketball", "baseball"}
REQUIRES_UNLOCKER = True  # Cloudflare-protected; debemos pasar por Scrape.do.
UNLOCKER_PROVIDER = "scrapedo"  # Sprint-D9-HOTFIX3: migrado de Bright Data

# ─────────────────────────────────────────────────────────────────────
# Endpoints (api.sofascore.com — JSON puro, no requiere render JS)
# ─────────────────────────────────────────────────────────────────────
_SEARCH_URL  = "https://api.sofascore.com/api/v1/search/events?q={q}"
_EVENT_URL   = "https://api.sofascore.com/api/v1/event/{eid}"
_LINEUPS_URL = "https://api.sofascore.com/api/v1/event/{eid}/lineups"
_H2H_URL     = "https://api.sofascore.com/api/v1/event/{eid}/h2h/events"
_TEAM_LAST_URL = "https://api.sofascore.com/api/v1/team/{tid}/events/last/{page}"
_EVENT_STATS_URL = "https://api.sofascore.com/api/v1/event/{eid}/statistics"
_EVENT_ODDS_URL  = "https://api.sofascore.com/api/v1/event/{eid}/odds/1/all"

# Timeout default conservador para endpoints JSON; cada fetch < 30s.
_SCRAPEDO_TIMEOUT_S = 30.0


# ─────────────────────────────────────────────────────────────────────
# Scrape.do helpers (fail-soft)
# ─────────────────────────────────────────────────────────────────────
async def _scrapedo_available() -> bool:
    try:
        from services.scrape_do_client import is_enabled
        return bool(is_enabled())
    except Exception:  # noqa: BLE001
        return False


async def _scrapedo_fetch(url: str) -> Optional[str]:
    """Fetch JSON body via Scrape.do (no render). Returns ``None`` si
    el fetch falla — el caller decide el reason_code."""
    try:
        from services.scrape_do_client import fetch_via_scrapedo_result
        res = await fetch_via_scrapedo_result(
            url, timeout=_SCRAPEDO_TIMEOUT_S, render=False,
        )
    except Exception as exc:  # noqa: BLE001
        # F99 — los crashes de Scrape.do son fallas esperadas (timeouts,
        # cloudflare drift). Reservamos WARNING para problemas sistémicos
        # (caller decide vía circuit-breaker). Aquí degradamos a DEBUG.
        log.debug("sofascore scrape.do fetch crashed for %s: %s", url, exc)
        return None
    if not res or not res.get("ok"):
        # F99 — fail-soft sin logs ruidosos por partido.
        log.debug(
            "sofascore scrape.do non-ok response url=%s status=%s reason=%s",
            url, res.get("status_code") if res else None,
            res.get("reason_code") if res else "no_result",
        )
        return None
    return res.get("html") or None


# ─────────────────────────────────────────────────────────────────────
# Event ID resolver
# ─────────────────────────────────────────────────────────────────────
async def _resolve_event_id(home: str, away: str, sport: str) -> Optional[int]:
    if not home or not away:
        return None
    q = f"{home} {away}".replace(" ", "%20")
    body = await _scrapedo_fetch(_SEARCH_URL.format(q=q))
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    for item in (data.get("results") or [])[:5]:
        entity = item.get("entity") or {}
        if entity.get("type") != "event":
            continue
        # Filter by sport — Sofascore usa slug "football"/"basketball"/"baseball"
        sp = (
            (entity.get("tournament") or {})
            .get("category", {}).get("sport", {})
            .get("slug", "")
            .lower()
        )
        if sport == "football" and sp not in ("football", "soccer"):
            continue
        if sport == "basketball" and sp != "basketball":
            continue
        if sport == "baseball" and sp != "baseball":
            continue
        eid = entity.get("id")
        if eid:
            try:
                return int(eid)
            except (TypeError, ValueError):
                continue
    return None


# ─────────────────────────────────────────────────────────────────────
# Evidence builders
# ─────────────────────────────────────────────────────────────────────
def _bullets_from_event(payload: dict, sport: str) -> tuple[list[str], str]:
    bullets: list[str] = []
    evt = payload.get("event") or payload
    status = (evt.get("status") or {}).get("type", "")
    if status:
        bullets.append(f"Estado SofaScore: {status}")
    for side in ("homeTeam", "awayTeam"):
        team = evt.get(side) or {}
        form = team.get("form") or []
        if form:
            bullets.append(
                f"Forma {team.get('shortName') or team.get('name')}: "
                f"{' '.join(form[:5])}"
            )
    pred = evt.get("winnerCode") or {}
    if isinstance(pred, dict) and pred:
        bullets.append(f"Predicción ganador SofaScore: {pred}")
    if sport == "football":
        if evt.get("hasXg"):
            bullets.append("xG disponible en SofaScore para este partido.")
    elif sport == "basketball":
        if evt.get("hasEventPlayerStatistics"):
            bullets.append("Stats por jugador disponibles en SofaScore.")
    elif sport == "baseball":
        if evt.get("seasonType"):
            bullets.append(f"Fase MLB: {evt.get('seasonType')}")
    etype = "recent_form" if any("Forma" in b for b in bullets) else "news_context"
    return bullets, etype


# ─────────────────────────────────────────────────────────────────────
# Public fetcher
# ─────────────────────────────────────────────────────────────────────
async def fetch(home: str, away: str, *, league: str = "",
                  sport: str = "football", **_) -> dict:
    if sport not in APPLICABLE_SPORTS:
        return skipped_evidence(NAME, reason="sport_not_supported")
    if not await _scrapedo_available():
        return skipped_evidence(NAME, reason="scrapedo_not_configured")
    try:
        eid = await _resolve_event_id(home, away, sport)
        if not eid:
            return failed_evidence(NAME, reason="event_id_not_resolved")
        body = await _scrapedo_fetch(_EVENT_URL.format(eid=eid))
        if not body:
            return failed_evidence(NAME, reason="event_blocked")
        try:
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            return failed_evidence(NAME, reason=f"json_parse:{exc}"[:120])
        bullets, etype = _bullets_from_event(payload, sport)
        # H2H — fail-soft (no abortamos la evidencia principal si falla).
        h2h_body = await _scrapedo_fetch(_H2H_URL.format(eid=eid))
        if h2h_body:
            try:
                h2h = json.loads(h2h_body)
                count = len((h2h.get("events") or [])[:5])
                if count:
                    bullets.append(
                        f"H2H reciente disponible en SofaScore ({count} encuentros)."
                    )
                    if etype == "news_context":
                        etype = "h2h"
            except Exception:  # noqa: BLE001
                pass
        url = f"https://www.sofascore.com/event/{eid}"
        return make_evidence(
            NAME, url=url,
            title=clean_text(((payload.get("event") or payload).get("slug") or "")) or None,
            evidence_type=etype,
            extracted_data=bullets,
            confidence=80 if bullets else 40,
            freshness="fresh",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("sofascore.fetch crashed: %s", exc)
        return failed_evidence(NAME, reason=f"unexpected:{exc}"[:120])



# ═════════════════════════════════════════════════════════════════════
# F99 — Public API for the F74 wiring pipeline
# ─────────────────────────────────────────────────────────────────────
# These helpers are consumed by ``services.football_sofascore_hydrator``
# (and tests) to produce the **wrapper raw** that ``adapt_sofascore_to_f74``
# understands.  None of them ever return raw HTML or JSON bodies — only
# pre-normalised dicts.  All errors degrade to ``None`` (fail-soft) so the
# caller never has to wrap calls in try/except.
# ═════════════════════════════════════════════════════════════════════

# Default wiring caps (kept small to bound IO budget per match).
_DEFAULT_RECENT_FORM_N = 5
_DEFAULT_H2H_N         = 5
_DEFAULT_TOTAL_TIMEOUT_S = 25.0
_DEFAULT_ENRICH_STATS  = False  # off by default; opt-in via param.


async def resolve_sofascore_event(
    home: str,
    away: str,
    *,
    sport: str = "football",
    target_date: Optional[str] = None,
) -> Optional[int]:
    """Resolve a SofaScore numeric ``event_id`` for ``home`` vs ``away``.

    Thin public wrapper around the legacy ``_resolve_event_id`` so
    higher layers (hydrator, tests) don't have to depend on a private name.

    Parameters
    ----------
    home, away:
        Team names. Empty strings short-circuit to ``None``.
    sport:
        One of ``football``/``basketball``/``baseball``.
    target_date:
        Reserved (ISO date) for future filtering; currently advisory only.

    Returns
    -------
    Optional[int]
        Numeric event id when found; otherwise ``None`` (fail-soft).
    """
    if not home or not away:
        return None
    if sport not in APPLICABLE_SPORTS:
        return None
    if not await _scrapedo_available():
        return None
    try:
        return await _resolve_event_id(home, away, sport)
    except Exception as exc:  # noqa: BLE001
        log.debug("resolve_sofascore_event crashed: %s", exc)
        return None


def _safe_json(body: Optional[str]) -> Optional[dict]:
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _event_team_ids(event_meta: dict) -> tuple[Optional[int], Optional[int]]:
    """Extract numeric ``(home_id, away_id)`` from a SofaScore event dict."""
    if not isinstance(event_meta, dict):
        return None, None
    ev = event_meta.get("event") or event_meta
    home_id = (ev.get("homeTeam") or {}).get("id")
    away_id = (ev.get("awayTeam") or {}).get("id")

    def _coerce(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return _coerce(home_id), _coerce(away_id)


def _normalize_stats_block(stats_payload: Optional[dict]) -> dict:
    """Reduce a ``/event/{eid}/statistics`` response to the keys the adapter expects.

    SofaScore returns ``statistics: [{period, groups: [{statisticsItems: [...]}]}]``.
    We only care about the ALL-period totals.
    Returns ``{"home_stats": {...}, "away_stats": {...}}`` (each side may be empty).
    """
    out = {"home_stats": {}, "away_stats": {}}
    if not isinstance(stats_payload, dict):
        return out
    periods = stats_payload.get("statistics") or []
    if not isinstance(periods, list):
        return out
    # Prefer the "ALL" period; otherwise take the first available.
    target = None
    for p in periods:
        if not isinstance(p, dict):
            continue
        if (p.get("period") or "").upper() == "ALL":
            target = p
            break
    if target is None and periods:
        target = periods[0] if isinstance(periods[0], dict) else None
    if not isinstance(target, dict):
        return out

    # Mapping of SofaScore "key" → our flat metric name (per side).
    KEY_MAP = {
        "totalShotsOnGoal":    "shots_on_target",
        "shotsOnGoal":         "shots_on_target",
        "totalShots":          "shots",
        "ballPossession":      "possession",
        "cornerKicks":         "corners",
        "expectedGoals":       "xg",
    }

    for group in target.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("statisticsItems") or []:
            if not isinstance(item, dict):
                continue
            sof_key = item.get("key") or item.get("name") or ""
            metric  = KEY_MAP.get(sof_key)
            if not metric:
                continue
            h_raw = item.get("home")
            a_raw = item.get("away")

            def _to_num(v):
                if v is None:
                    return None
                if isinstance(v, (int, float)):
                    return v
                s = str(v).strip().rstrip("%")
                try:
                    return float(s)
                except (TypeError, ValueError):
                    return None

            h_v = _to_num(h_raw)
            a_v = _to_num(a_raw)
            if h_v is not None:
                out["home_stats"][metric] = h_v
            if a_v is not None:
                out["away_stats"][metric] = a_v
    return out


def _normalize_event_row(ev: Any) -> Optional[dict]:
    """Map one SofaScore event dict (from team-last endpoint) to the wrapper row.

    Returns a dict shaped so the adapter's ``_normalise_form_block`` can read it,
    or ``None`` if the row lacks the minimum needed fields.
    """
    if not isinstance(ev, dict):
        return None
    home = (ev.get("homeTeam") or {}).get("name")
    away = (ev.get("awayTeam") or {}).get("name")
    h_score = ((ev.get("homeScore") or {}).get("current"))
    a_score = ((ev.get("awayScore") or {}).get("current"))
    if h_score is None:
        h_score = (ev.get("homeScore") or {}).get("display")
    if a_score is None:
        a_score = (ev.get("awayScore") or {}).get("display")
    if not home or not away or h_score is None or a_score is None:
        return None
    ts = ev.get("startTimestamp")
    iso_date = None
    if isinstance(ts, (int, float)):
        try:
            iso_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            iso_date = None
    return {
        "event_id":    ev.get("id"),
        "date":        iso_date,
        "home_team":   home,
        "away_team":   away,
        "home_score":  h_score,
        "away_score":  a_score,
    }


async def _fetch_team_last_events(team_id: int, n: int) -> list[dict]:
    """Fetch the last ``n`` events for a SofaScore team id (fail-soft)."""
    if not team_id or n <= 0:
        return []
    body = await _scrapedo_fetch(_TEAM_LAST_URL.format(tid=int(team_id), page=0))
    data = _safe_json(body)
    if not data:
        return []
    events = data.get("events") or []
    out: list[dict] = []
    for ev in events[:n]:
        row = _normalize_event_row(ev)
        if row:
            out.append(row)
    return out


async def _enrich_row_with_stats(row: dict) -> dict:
    """Best-effort attach ``home_stats``/``away_stats`` to a normalised row.

    On any error the row is returned **unchanged** (no stats keys added).
    """
    eid = row.get("event_id")
    if not eid:
        return row
    body = await _scrapedo_fetch(_EVENT_STATS_URL.format(eid=int(eid)))
    stats = _normalize_stats_block(_safe_json(body))
    if stats.get("home_stats"):
        row["home_stats"] = stats["home_stats"]
    if stats.get("away_stats"):
        row["away_stats"] = stats["away_stats"]
    return row


def _normalize_h2h_events(h2h_payload: Optional[dict], n: int) -> list[dict]:
    if not isinstance(h2h_payload, dict):
        return []
    events = h2h_payload.get("events") or []
    out: list[dict] = []
    for ev in events[-n:]:
        row = _normalize_event_row(ev)
        if row:
            out.append(row)
    return out


def _normalize_odds_block(odds_payload: Optional[dict]) -> dict:
    """Reduce ``/event/{eid}/odds/1/all`` to ``{market_key: {selection: decimal_price}}``.

    The mapping is intentionally minimal because the F74 cascade cares only
    about the **section being non-empty** for the source ranking decision.
    """
    if not isinstance(odds_payload, dict):
        return {}
    markets = odds_payload.get("markets") or []
    if not isinstance(markets, list):
        return {}
    out: dict[str, dict] = {}

    NAME_MAP = {
        "Full time":           "match_winner",
        "1X2":                 "match_winner",
        "Both teams to score": "btts",
        "Total":               "total_goals",
    }

    def _coerce_decimal(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    for m in markets[:25]:
        if not isinstance(m, dict):
            continue
        market_name = m.get("marketName") or m.get("marketSlug") or ""
        key = NAME_MAP.get(market_name)
        if not key:
            continue
        sels = {}
        for choice in m.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            label = (choice.get("name") or "").strip().lower()
            price = _coerce_decimal(choice.get("fractionalValue") or choice.get("initialFractionalValue"))
            # Some payloads expose decimal odds directly.
            if price is None:
                price = _coerce_decimal(choice.get("decimalValue") or choice.get("value"))
            if not label or price is None:
                continue
            sels[label] = price
        if sels:
            out[key] = sels
    return out


async def fetch_sofascore_match_context(
    home: str,
    away: str,
    *,
    sport: str = "football",
    target_date: Optional[str] = None,
    recent_n: int = _DEFAULT_RECENT_FORM_N,
    h2h_n: int = _DEFAULT_H2H_N,
    enrich_stats: bool = _DEFAULT_ENRICH_STATS,
    total_timeout_s: float = _DEFAULT_TOTAL_TIMEOUT_S,
) -> Optional[dict]:
    """Build the **wrapper raw** payload consumed by ``adapt_sofascore_to_f74``.

    Output shape (or ``None`` if the source is unreachable / blocked)::

        {
          "event_id":  <int>,
          "home_form": [<row>, ...],
          "away_form": [<row>, ...],
          "h2h":       [<row>, ...],
          "odds":      {"match_winner": {...}, "btts": {...}, ...},
          "_trace": {
              "status":         "RICH" | "USABLE" | "PARTIAL" | "NO_DATA",
              "event_resolved": <bool>,
              "stats_enriched": <bool>,
          }
        }

    Notes
    -----
    * Strict fail-soft. Never raises. ``None`` => caller should skip wiring.
    * Caller MUST NOT persist this dict beyond the in-memory match object —
      it is the **wrapper**, not raw upstream HTML/JSON.
    * ``enrich_stats=True`` adds shots/possession/corners per recent fixture
      (extra SofaScore IO; opt-in).
    * The overall budget is bounded by ``total_timeout_s``; on timeout we
      degrade gracefully (returning whatever partial data we already have).
    """
    if sport not in APPLICABLE_SPORTS:
        return None
    if not home or not away:
        return None
    if not await _scrapedo_available():
        return None

    async def _runner() -> Optional[dict]:
        # 1) Resolve event id.
        eid = await _resolve_event_id(home, away, sport)
        if not eid:
            return None

        # 2) Event metadata (for team ids + odds fallback).
        meta_body = await _scrapedo_fetch(_EVENT_URL.format(eid=eid))
        meta = _safe_json(meta_body) or {}
        home_id, away_id = _event_team_ids(meta)

        # 3) Recent form per team (parallel).
        home_form_task = asyncio.create_task(_fetch_team_last_events(home_id, recent_n)) if home_id else None
        away_form_task = asyncio.create_task(_fetch_team_last_events(away_id, recent_n)) if away_id else None
        h2h_task       = asyncio.create_task(_scrapedo_fetch(_H2H_URL.format(eid=eid)))
        odds_task      = asyncio.create_task(_scrapedo_fetch(_EVENT_ODDS_URL.format(eid=eid)))

        home_form = await home_form_task if home_form_task else []
        away_form = await away_form_task if away_form_task else []

        # 4) Optionally enrich each row with statistics (extra IO).
        stats_enriched = False
        if enrich_stats:
            try:
                home_form = await asyncio.gather(
                    *(_enrich_row_with_stats(r) for r in home_form),
                    return_exceptions=False,
                )
                away_form = await asyncio.gather(
                    *(_enrich_row_with_stats(r) for r in away_form),
                    return_exceptions=False,
                )
                stats_enriched = True
            except Exception as exc:  # noqa: BLE001
                log.debug("sofascore stats enrich crashed: %s", exc)
                # keep rows as-is (no stats); still useable for goals/form.

        # 5) H2H + odds.
        h2h_payload  = _safe_json(await h2h_task)
        odds_payload = _safe_json(await odds_task)
        h2h          = _normalize_h2h_events(h2h_payload, h2h_n)
        odds         = _normalize_odds_block(odds_payload)

        # 6) Trace status (descriptive only — does NOT block field selection).
        usable_rows = (len(home_form) >= 1) + (len(away_form) >= 1)
        has_h2h     = bool(h2h)
        has_odds    = bool(odds)
        if stats_enriched and usable_rows == 2 and (has_h2h or has_odds):
            status = "RICH"
        elif usable_rows == 2:
            status = "USABLE"
        elif usable_rows >= 1 or has_h2h or has_odds:
            status = "PARTIAL"
        else:
            status = "NO_DATA"

        return {
            "event_id":  int(eid),
            "home_form": home_form,
            "away_form": away_form,
            "h2h":       h2h,
            "odds":      odds,
            "_trace": {
                "status":         status,
                "event_resolved": True,
                "stats_enriched": stats_enriched,
            },
        }

    try:
        return await asyncio.wait_for(_runner(), timeout=total_timeout_s)
    except asyncio.TimeoutError:
        # F99 binding: silent fail-soft.
        log.debug("fetch_sofascore_match_context timed out (home=%s away=%s)", home, away)
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("fetch_sofascore_match_context crashed: %s", exc)
        return None


__all__ = [
    "NAME",
    "APPLICABLE_SPORTS",
    "REQUIRES_UNLOCKER",
    "UNLOCKER_PROVIDER",
    "fetch",
    "resolve_sofascore_event",
    "fetch_sofascore_match_context",
]
