"""Sprint-F99 · SofaScore → F74 wiring (Prioridades 1 y 2).

Estos tests verifican:

  1. ``resolve_sofascore_event`` — fail-soft + uso del scraper interno.
  2. ``fetch_sofascore_match_context`` — wrapper canonical consumido por el
     adapter F98 ``adapt_sofascore_to_f74``.
  3. ``football_sofascore_hydrator`` — feature flag + telemetría estructurada
     + adjuntado de ``match["_sofascore_raw"]`` sin filtrar payload crudo.
  4. ``football_source_cascade`` — cambios de ranking F99 (xG primario =
     SofaScore, córners offline_seed → SofaScore → TheStatsAPI → TheSportsDB).
  5. ``api_football`` kill switch — ``DISABLE_API_FOOTBALL=true`` corta
     **toda** IO sin lanzar excepciones (purga funcional).

Reglas binding (NO romper):
  * Fail-soft: ninguna función F99 debe lanzar excepciones al caller.
  * Payload crudo de SofaScore nunca debe llegar al editorial.
  * Telemetría siempre escrita (incluso en SKIPPED/BLOCKED).
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from services.adapters._envelope import new_envelope, set_field, finalize_envelope
from services.adapters.sofascore_adapter import adapt_sofascore_to_f74
from services.football_source_cascade import (
    DEFAULT_RANKINGS,
    cascade_merge_envelopes,
)


# ─────────────────────────────────────────────────────────────────────
# 1. resolve_sofascore_event — public API + fail-soft
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolve_event_returns_none_when_empty_team_names():
    from services.external_sources.sofascore import resolve_sofascore_event
    assert await resolve_sofascore_event("", "Liverpool") is None
    assert await resolve_sofascore_event("Arsenal", "") is None
    assert await resolve_sofascore_event(None, None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_event_returns_none_for_unknown_sport():
    from services.external_sources.sofascore import resolve_sofascore_event
    assert await resolve_sofascore_event("A", "B", sport="cricket") is None


@pytest.mark.asyncio
async def test_resolve_event_short_circuits_when_scrapedo_unavailable():
    from services.external_sources import sofascore as ss
    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=False)):
        out = await ss.resolve_sofascore_event("Arsenal", "Liverpool")
    assert out is None


@pytest.mark.asyncio
async def test_resolve_event_delegates_to_internal_resolver_when_available():
    from services.external_sources import sofascore as ss
    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=True)), \
         patch.object(ss, "_resolve_event_id", new=AsyncMock(return_value=99887766)):
        out = await ss.resolve_sofascore_event("Arsenal", "Liverpool")
    assert out == 99887766


# ─────────────────────────────────────────────────────────────────────
# 2. fetch_sofascore_match_context — wrapper canonical
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_context_returns_none_when_event_not_resolved():
    from services.external_sources import sofascore as ss
    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=True)), \
         patch.object(ss, "_resolve_event_id", new=AsyncMock(return_value=None)):
        out = await ss.fetch_sofascore_match_context("Arsenal", "Liverpool")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_context_returns_none_on_unsupported_sport():
    from services.external_sources.sofascore import fetch_sofascore_match_context
    out = await fetch_sofascore_match_context("A", "B", sport="cricket")
    assert out is None


@pytest.mark.asyncio
async def test_fetch_context_returns_none_on_timeout():
    """The total_timeout_s budget MUST be honoured (fail-soft)."""
    from services.external_sources import sofascore as ss

    async def _slow_resolver(*_a, **_k):
        await asyncio.sleep(2.0)
        return 1

    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=True)), \
         patch.object(ss, "_resolve_event_id", new=_slow_resolver):
        out = await ss.fetch_sofascore_match_context(
            "A", "B", total_timeout_s=0.05,
        )
    assert out is None


@pytest.mark.asyncio
async def test_fetch_context_builds_wrapper_shape_expected_by_adapter():
    """End-to-end: stub fetchers, assert wrapper feeds the F98 adapter cleanly."""
    from services.external_sources import sofascore as ss

    event_meta_body = """{
        "event": {"homeTeam": {"id": 11}, "awayTeam": {"id": 22}}
    }"""

    arsenal_last_body = """{
        "events": [
            {"id": 1, "homeTeam": {"name": "Arsenal"}, "awayTeam": {"name": "X"},
             "homeScore": {"current": 2}, "awayScore": {"current": 1},
             "startTimestamp": 1717200000},
            {"id": 2, "homeTeam": {"name": "Y"}, "awayTeam": {"name": "Arsenal"},
             "homeScore": {"current": 0}, "awayScore": {"current": 0},
             "startTimestamp": 1717100000}
        ]
    }"""
    liverpool_last_body = """{
        "events": [
            {"id": 3, "homeTeam": {"name": "Liverpool"}, "awayTeam": {"name": "Z"},
             "homeScore": {"current": 3}, "awayScore": {"current": 0},
             "startTimestamp": 1717200000}
        ]
    }"""
    h2h_body = """{
        "events": [
            {"id": 9, "homeTeam": {"name": "Arsenal"}, "awayTeam": {"name": "Liverpool"},
             "homeScore": {"current": 1}, "awayScore": {"current": 2},
             "startTimestamp": 1716000000}
        ]
    }"""
    odds_body = "{}"  # No odds — wrapper should still be useable.

    async def _fake_fetch(url: str) -> str | None:
        if "/event/424242" in url and "/h2h" not in url and "/statistics" not in url and "/odds" not in url:
            return event_meta_body
        if "/team/11/" in url:
            return arsenal_last_body
        if "/team/22/" in url:
            return liverpool_last_body
        if "/h2h/" in url:
            return h2h_body
        if "/odds/" in url:
            return odds_body
        return None

    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=True)), \
         patch.object(ss, "_resolve_event_id", new=AsyncMock(return_value=424242)), \
         patch.object(ss, "_scrapedo_fetch", new=AsyncMock(side_effect=_fake_fetch)):
        wrapper = await ss.fetch_sofascore_match_context("Arsenal", "Liverpool")

    assert wrapper is not None
    # Shape contract.
    assert wrapper["event_id"] == 424242
    assert isinstance(wrapper["home_form"], list) and len(wrapper["home_form"]) == 2
    assert isinstance(wrapper["away_form"], list) and len(wrapper["away_form"]) == 1
    assert isinstance(wrapper["h2h"], list) and len(wrapper["h2h"]) == 1
    assert isinstance(wrapper["odds"], dict)
    # Telemetry block exists with descriptive status.
    assert wrapper["_trace"]["event_resolved"] is True
    assert wrapper["_trace"]["status"] in {"USABLE", "RICH", "PARTIAL"}
    # Critically: no raw HTML/JSON bodies leaked in the wrapper.
    txt = repr(wrapper)
    assert "events" not in txt or "h2h" in txt  # adapter-shape key, not raw payload
    # The wrapper is consumable by the F98 adapter (no exceptions, valid env).
    env = adapt_sofascore_to_f74(wrapper, home_team="Arsenal", away_team="Liverpool")
    assert env["source"] == "sofascore"
    assert env["available"] is True


# ─────────────────────────────────────────────────────────────────────
# 3. football_sofascore_hydrator — feature flag + telemetry
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hydrator_skips_when_feature_flag_off(monkeypatch):
    from services import football_sofascore_hydrator as fsh
    monkeypatch.delenv(fsh.FLAG_ENV_VAR, raising=False)
    match = {
        "home_team": {"name": "Arsenal"},
        "away_team": {"name": "Liverpool"},
    }
    attached = await fsh.hydrate_match_sofascore(match, sport="football")
    assert attached is False
    assert "_sofascore_raw" not in match
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["attempted"] is False
    assert trace["status"] == "SKIPPED"
    assert trace["reason"] == "feature_flag_off"
    assert "checked_at" in trace


@pytest.mark.asyncio
async def test_hydrator_skips_for_non_football_sport(monkeypatch):
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")
    match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    attached = await fsh.hydrate_match_sofascore(match, sport="basketball")
    assert attached is False
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["status"] == "SKIPPED"
    assert trace["reason"].startswith("sport_not_supported")


@pytest.mark.asyncio
async def test_hydrator_skips_when_team_names_missing(monkeypatch):
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")
    match = {"home_team": {}, "away_team": {}}
    attached = await fsh.hydrate_match_sofascore(match, sport="football")
    assert attached is False
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["reason"] == "missing_team_names"


@pytest.mark.asyncio
async def test_hydrator_attaches_wrapper_and_records_useable_trace(monkeypatch):
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")
    wrapper = {
        "event_id": 1,
        "home_form": [
            {"date": "2024-05-01", "home_team": "A", "away_team": "B",
             "home_score": 2, "away_score": 1},
            {"date": "2024-04-01", "home_team": "C", "away_team": "A",
             "home_score": 0, "away_score": 0},
        ],
        "away_form": [
            {"date": "2024-05-02", "home_team": "B", "away_team": "D",
             "home_score": 1, "away_score": 3},
        ],
        "h2h":  [{"date": "2024-03-01", "home_team": "A", "away_team": "B",
                   "home_score": 1, "away_score": 2}],
        "odds": {},
        "_trace": {"status": "USABLE", "event_resolved": True, "stats_enriched": False},
    }
    with patch(
        "services.external_sources.sofascore.fetch_sofascore_match_context",
        new=AsyncMock(return_value=wrapper),
    ):
        match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
        attached = await fsh.hydrate_match_sofascore(match, sport="football")

    assert attached is True
    assert match["_sofascore_raw"] == wrapper
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["attempted"] is True
    assert trace["status"] == "USABLE"
    assert "home_form" in trace["valid_fields"]
    assert "away_form" in trace["valid_fields"]
    assert "h2h"       in trace["valid_fields"]
    assert "odds"      in trace["missing_fields"]
    assert trace["fallback_triggered"] is False


@pytest.mark.asyncio
async def test_hydrator_records_blocked_trace_when_fetch_returns_none(monkeypatch):
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")
    with patch(
        "services.external_sources.sofascore.fetch_sofascore_match_context",
        new=AsyncMock(return_value=None),
    ):
        match = {"home_team": "A", "away_team": "B"}
        attached = await fsh.hydrate_match_sofascore(match, sport="football")

    assert attached is False
    assert "_sofascore_raw" not in match
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["attempted"] is True
    assert trace["status"] == "BLOCKED"
    assert trace["fallback_triggered"] is True


@pytest.mark.asyncio
async def test_hydrator_swallows_unexpected_fetcher_exceptions(monkeypatch):
    """Hydrator must never raise — defensive guard around the fetcher."""
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")

    async def _boom(*_a, **_k):
        raise RuntimeError("upstream went sideways")

    with patch(
        "services.external_sources.sofascore.fetch_sofascore_match_context",
        new=_boom,
    ):
        match = {"home_team": "A", "away_team": "B"}
        attached = await fsh.hydrate_match_sofascore(match, sport="football")

    assert attached is False
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["attempted"] is True
    assert trace["status"] == "BLOCKED"


# ─────────────────────────────────────────────────────────────────────
# 4. Cascade rankings (F99 P2 binding)
# ─────────────────────────────────────────────────────────────────────
def test_f99_xg_ranking_promotes_sofascore_to_primary():
    assert DEFAULT_RANKINGS["xg_for_l5"][0]     == "sofascore"
    assert DEFAULT_RANKINGS["xg_against_l5"][0] == "sofascore"
    # TheStatsAPI is the documented fallback.
    assert DEFAULT_RANKINGS["xg_for_l5"][1]     == "thestatsapi"


def test_f99_shots_ranking_keeps_sofascore_primary_thestatsapi_fallback():
    assert DEFAULT_RANKINGS["shots_for_l5"][:2]       == ["sofascore", "thestatsapi"]
    assert DEFAULT_RANKINGS["shots_on_target_l5"][:2] == ["sofascore", "thestatsapi"]


def test_f99_corners_ranking_matches_user_spec():
    expected = ["offline_seed", "sofascore", "thestatsapi", "thesportsdb", "seed_partial"]
    assert DEFAULT_RANKINGS["corners_for_l5"][:5]     == expected
    assert DEFAULT_RANKINGS["corners_against_l5"][:5] == expected


def _envelope_with(source: str, side: str, field: str, value, sample_size: int = 5) -> dict:
    env = new_envelope(source=source, available=True)
    set_field(env, f"{side}.{field}", value, sample_size=sample_size)
    return finalize_envelope(env)


def test_f99_cascade_xg_uses_sofascore_when_both_present():
    envs = [
        _envelope_with("thestatsapi", "home", "xg_for_l5", 1.55),
        _envelope_with("sofascore",   "home", "xg_for_l5", 1.10),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["xg_for_l5"] == 1.10
    assert merged["field_provenance"]["home.xg_for_l5"]["source"] == "sofascore"


def test_f99_cascade_corners_skips_absent_offline_seed_and_picks_sofascore():
    """offline_seed has no envelope → cascade must skip and use SofaScore.

    This proves the declarative ranking is fail-soft when the new
    PROVIDER_NOT_PRESENT slots are not yet implemented.
    """
    envs = [
        _envelope_with("sofascore",   "home", "corners_for_l5", 5.4),
        _envelope_with("thestatsapi", "home", "corners_for_l5", 4.9),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["corners_for_l5"] == 5.4
    assert merged["field_provenance"]["home.corners_for_l5"]["source"] == "sofascore"


def test_f99_cascade_corners_falls_back_to_thestatsapi_when_sofascore_missing():
    envs = [
        _envelope_with("thestatsapi", "home", "corners_for_l5", 4.9),
    ]
    merged = cascade_merge_envelopes(envs)
    assert merged["home"]["corners_for_l5"] == 4.9
    assert merged["field_provenance"]["home.corners_for_l5"]["source"] == "thestatsapi"


# ─────────────────────────────────────────────────────────────────────
# 5. API-Sports kill switch
# ─────────────────────────────────────────────────────────────────────
def test_api_football_kill_switch_flag_helper(monkeypatch):
    from services import api_football as af
    monkeypatch.delenv(af.DISABLE_FLAG_ENV_VAR, raising=False)
    assert af.is_disabled() is False
    monkeypatch.setenv(af.DISABLE_FLAG_ENV_VAR, "true")
    assert af.is_disabled() is True
    monkeypatch.setenv(af.DISABLE_FLAG_ENV_VAR, "0")
    assert af.is_disabled() is False


@pytest.mark.asyncio
async def test_api_football_get_short_circuits_when_disabled(monkeypatch):
    """F99.2 — ``_get`` is now a deprecated stub: ALWAYS short-circuits and
    NEVER performs HTTP IO, regardless of the legacy kill-switch flag.
    """
    from services import api_football as af
    monkeypatch.setenv(af.DISABLE_FLAG_ENV_VAR, "true")

    class _ExplodingClient:
        async def get(self, *_a, **_k):
            raise AssertionError("HTTP must not be invoked when kill switch is on")

    out = await af._get(_ExplodingClient(), "/fixtures", params={"date": "2025-01-01"})
    # Empty envelope is preserved for legacy callers ...
    assert out["response"] == []
    assert out["errors"]   == {}
    # ... and F99.2 adds the deprecation markers.
    assert out["_f99_disabled"]        is True
    assert out["_f99_deprecated_stub"] is True
    assert out["_reason_code"] == af.DEPRECATED_STUB_REASON_CODE


@pytest.mark.asyncio
async def test_api_football_get_is_failclosed_even_without_kill_switch(monkeypatch):
    """F99.2 — the stub is fail-closed REGARDLESS of env flag.

    Previously, with the kill switch off and ``API_FOOTBALL_KEY`` empty,
    ``_get`` would raise ``APIFootballError``. After F99.2 the stub no
    longer raises (decommissioned).
    """
    from services import api_football as af
    monkeypatch.delenv(af.DISABLE_FLAG_ENV_VAR, raising=False)
    monkeypatch.setattr(af, "API_KEY", "")
    out = await af._get(object(), "/fixtures")
    assert out["response"]              == []
    assert out["_f99_deprecated_stub"]  is True
