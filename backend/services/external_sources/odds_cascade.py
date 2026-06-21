"""Sprint-D9-OddsCascade · Orquestador unificado de cuotas H2H.

Reemplaza a Sportytrader como fuente de cuotas H2H prematch.

Cascada:
  1. **TheOddsAPI** (primario)
       Endpoint ``/v4/sports/{sport}/odds`` con ``markets=h2h``.
       Cubre ~95% de ligas top globales con varias casas.
  2. **OddsPortal** (fallback)
       Scraper vía scrape.do que extrae el promedio H2H mostrado en
       el sitio. Útil para ligas exóticas donde TheOddsAPI no llega.
  3. ``available=False`` con ``reason_codes`` completos (la UI sigue
     mostrando "REQUIRES_MARKET_IDENTIFICATION" sin error).

La cascada es **fail-soft** en cada paso: si TheOddsAPI no encuentra el
partido o falla la red, automáticamente se intenta OddsPortal.

Public API:
  ``async fetch_direct_match_odds_cascade(home, away, *, sport_key, ...)``

Feature flag:
  ``ENABLE_ODDS_CASCADE_FALLBACK`` (default: true)
      Si "false", solo intenta TheOddsAPI y omite OddsPortal.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("odds_cascade")


def _flag_enabled(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or ("true" if default else "false")).strip().lower()
    return raw not in ("0", "false", "no", "off")


async def _try_the_odds_api(
    *,
    home: str,
    away: str,
    sport_key: str,
    regions: str,
    use_cache: bool,
) -> Optional[dict]:
    """Intenta resolver odds H2H usando TheOddsAPI fetch_current_odds.

    Returns dict con shape::

        {
          "available":  True,
          "source":     "the_odds_api",
          "odd_home":   ...,
          "odd_draw":   ...,
          "odd_away":   ...,
          "bookmaker":  "Bet365",
          ...
        }
    o ``None`` si no encontró el partido / falló el fetch.
    """
    try:
        from services.external_sources import the_odds_api_client as toa
    except Exception as exc:  # noqa: BLE001
        log.warning("[odds_cascade] no se pudo importar the_odds_api_client: %s", exc)
        return None

    if not toa._api_key():
        return None  # caller decidirá si caer al fallback.

    try:
        payload = await toa.fetch_current_odds(
            sport=sport_key,
            regions=regions,
            markets="h2h",
            use_cache=use_cache,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[odds_cascade] the_odds_api fetch failed: %s", exc)
        return None

    if not payload:
        return None

    extracted = toa.extract_match_odds(
        payload, home_team=home, away_team=away, market="h2h",
    )
    if not extracted:
        return None

    return {
        "available":  True,
        "source":     "the_odds_api",
        "home_team":  home,
        "away_team":  away,
        "odd_home":   extracted["odd_home"],
        "odd_draw":   extracted["odd_draw"],
        "odd_away":   extracted["odd_away"],
        "bookmaker":  extracted.get("bookmaker") or "the_odds_api",
        "last_update": extracted.get("last_update"),
        "quota":      (payload.get("quota") if isinstance(payload, dict) else None),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def _try_odds_portal(
    *,
    home: str,
    away: str,
    league_slug: Optional[str],
    use_cache: bool,
) -> Optional[dict]:
    """Intenta resolver odds H2H usando OddsPortal scraper.

    Returns dict con shape unificado o ``None`` si falló.
    """
    try:
        from services.external_sources.odds_portal_client import (
            fetch_oddsportal_h2h,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[odds_cascade] no se pudo importar odds_portal_client: %s", exc)
        return None

    try:
        result = await fetch_oddsportal_h2h(
            home, away, league_slug=league_slug, use_cache=use_cache,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[odds_cascade] odds_portal fetch failed: %s", exc)
        return None

    if not result or not result.get("available"):
        return None
    return result


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
async def fetch_direct_match_odds_cascade(
    home: str,
    away: str,
    *,
    sport_key: str = "soccer_epl",
    regions: str = "uk,eu",
    league_slug: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    """Orquesta TheOddsAPI → OddsPortal y devuelve un payload uniforme.

    Returns
    -------
    dict
        Shape estable independientemente del source ganador::

            {
              "available":    bool,
              "source":       "the_odds_api" | "oddsportal" | "none",
              "home_team":    ...,
              "away_team":    ...,
              "odd_home":     float | None,
              "odd_draw":     float | None,
              "odd_away":     float | None,
              "bookmaker":    str | None,
              "cascade_audit": {
                  "sources_tried": ["the_odds_api", "oddsportal"],
                  "winner":        "the_odds_api" | "oddsportal" | None,
                  "reason_codes":  [...],
              },
              "fetched_at":   ISO-8601 UTC
            }

    Fail-soft: nunca levanta. Si ningún source devuelve datos,
    ``available=False`` con ``cascade_audit.reason_codes`` poblado.
    """
    audit = {
        "sources_tried": [],
        "winner":        None,
        "reason_codes":  [],
    }

    if not home or not away:
        audit["reason_codes"].append("ODDS_CASCADE_TEAMS_MISSING")
        return {
            "available":     False,
            "source":        "none",
            "cascade_audit": audit,
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
        }

    # ── 1) TheOddsAPI primario ──
    audit["sources_tried"].append("the_odds_api")
    primary = await _try_the_odds_api(
        home=home, away=away, sport_key=sport_key,
        regions=regions, use_cache=use_cache,
    )
    if primary and primary.get("available"):
        audit["winner"] = "the_odds_api"
        audit["reason_codes"].append("THE_ODDS_API_MATCH_FOUND")
        primary["cascade_audit"] = audit
        return primary

    audit["reason_codes"].append("THE_ODDS_API_NO_MATCH")

    # ── 2) OddsPortal fallback (controlado por flag) ──
    if not _flag_enabled("ENABLE_ODDS_CASCADE_FALLBACK", default=True):
        audit["reason_codes"].append("ODDS_CASCADE_FALLBACK_DISABLED")
        return {
            "available":     False,
            "source":        "none",
            "home_team":     home,
            "away_team":     away,
            "cascade_audit": audit,
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
        }

    audit["sources_tried"].append("oddsportal")
    fallback = await _try_odds_portal(
        home=home, away=away, league_slug=league_slug, use_cache=use_cache,
    )
    if fallback and fallback.get("available"):
        audit["winner"] = "oddsportal"
        audit["reason_codes"].append("ODDS_PORTAL_MATCH_FOUND")
        fallback["cascade_audit"] = audit
        return fallback

    audit["reason_codes"].append("ODDS_PORTAL_NO_MATCH")
    return {
        "available":     False,
        "source":        "none",
        "home_team":     home,
        "away_team":     away,
        "cascade_audit": audit,
        "fetched_at":    datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "fetch_direct_match_odds_cascade",
]
