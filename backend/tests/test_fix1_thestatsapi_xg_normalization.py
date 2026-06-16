"""FIX-1 — Tests for the xG TheStatsAPI normalization regression.

Two bugs combined to silently break the L1/L5/L15 xG averages:

  1. ``thestatsapi_client.fetch_recent_match_ids`` did NOT exist, so
     ``_ensure_thestatsapi_recent_match_ids`` in data_ingestion always
     hit ``AttributeError`` → ``thestatsapi_recent_match_ids`` was
     never populated → :func:`football_xg_recent_averages._fetch_one`
     queried IDs that TheStatsAPI did not recognise → 404 → no data.

  2. ``fetch_shotmap_xg`` read ``home_team_id`` / ``away_team_id``
     only from the *root* of the payload, but the real TheStatsAPI
     response nests them inside ``event`` (verified live with the
     ``Iran vs New Zealand`` match ``mt_986264843``):

         {
           "match_id": "mt_986264843",
           "event": {"home_team_id": "tm_65309",
                     "away_team_id": "tm_09373"},
           "data": [...]
         }

     With ``team_id=None`` the manual sum fell through to the
     ``RC_NO_TEAM_IDS`` branch → ``available=False``.

These tests lock both fixes in place.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.external_sources import thestatsapi_client as ts
from services.external_sources.thestatsapi_shotmap_client import fetch_shotmap_xg


# ─────────────────────────────────────────────────────────────────────
#   fetch_recent_match_ids — existence + payload extraction
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_recent_match_ids_exists_and_is_async():
    """The function MUST exist — its absence was the silent killer."""
    assert hasattr(ts, "fetch_recent_match_ids"), (
        "fetch_recent_match_ids is missing — every xG normalisation "
        "fails silently without it (AttributeError swallowed)."
    )


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_short_circuits_when_disabled(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: False)
    ids = await ts.fetch_recent_match_ids("tm_65309", n=5)
    assert ids == []


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_returns_empty_on_empty_team_id(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    assert (await ts.fetch_recent_match_ids("", n=5)) == []
    assert (await ts.fetch_recent_match_ids(None, n=5)) == []


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_extracts_data_array(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    fake_payload = {
        "data": [
            {"id": "mt_986264843", "status": "finished"},
            {"id": "mt_205268566", "status": "finished"},
            {"id": "mt_460244644", "status": "finished"},
        ]
    }
    with patch.object(ts, "_request", new=AsyncMock(return_value=fake_payload)):
        ids = await ts.fetch_recent_match_ids("tm_65309", n=15)
    assert ids == ["mt_986264843", "mt_205268566", "mt_460244644"]


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_respects_n_limit(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    fake_payload = {"data": [{"id": f"mt_{i:03d}"} for i in range(50)]}
    with patch.object(ts, "_request", new=AsyncMock(return_value=fake_payload)):
        ids = await ts.fetch_recent_match_ids("tm_X", n=5)
    assert len(ids) == 5
    assert ids[0] == "mt_000"
    assert ids[4] == "mt_004"


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_failsoft_on_request_exception(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    with patch.object(ts, "_request", new=AsyncMock(side_effect=RuntimeError("boom"))):
        ids = await ts.fetch_recent_match_ids("tm_X", n=5)
    assert ids == []


@pytest.mark.asyncio
async def test_fetch_recent_match_ids_accepts_alternate_payload_keys(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    fake_payload = {"matches": [{"id": "mt_A"}, {"match_id": "mt_B"}]}
    with patch.object(ts, "_request", new=AsyncMock(return_value=fake_payload)):
        ids = await ts.fetch_recent_match_ids("tm_X", n=5)
    assert ids == ["mt_A", "mt_B"]


# ─────────────────────────────────────────────────────────────────────
#   fetch_shotmap_xg — payload-shape regression
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_shotmap_xg_reads_team_ids_from_event_block(monkeypatch):
    """Reproduce the real TheStatsAPI payload shape from Iran vs NZ."""
    from services.external_sources import thestatsapi_shotmap_client as smc

    monkeypatch.setattr(ts, "is_enabled", lambda: True)

    real_payload = {
        "match_id": "mt_986264843",
        "event": {
            "id": "mt_986264843",
            "home_team_id": "tm_65309",
            "away_team_id": "tm_09373",
        },
        "data": [
            {"team_id": "tm_65309", "expected_goals": 0.50, "is_penalty": False},
            {"team_id": "tm_65309", "expected_goals": 0.99, "is_penalty": False},
            {"team_id": "tm_09373", "expected_goals": 0.30, "is_penalty": False},
            {"team_id": "tm_09373", "expected_goals": 0.94, "is_penalty": False},
        ],
    }
    with patch.object(ts, "_request", new=AsyncMock(return_value=real_payload)):
        out = await fetch_shotmap_xg(None, "mt_986264843")

    assert out["available"] is True
    assert out["home_team_id"] == "tm_65309"
    assert out["away_team_id"] == "tm_09373"
    assert out["home_np_xg"] == pytest.approx(1.49, abs=0.01)
    assert out["away_np_xg"] == pytest.approx(1.24, abs=0.01)


@pytest.mark.asyncio
async def test_fetch_shotmap_xg_still_reads_root_team_ids_when_event_missing(monkeypatch):
    """Backward compat: legacy payload shape with root-level ids must work."""
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    legacy_payload = {
        "match_id": "mt_legacy",
        "home_team_id": "tm_1",
        "away_team_id": "tm_2",
        "data": [
            {"team_id": "tm_1", "expected_goals": 0.7, "is_penalty": False},
            {"team_id": "tm_2", "expected_goals": 0.4, "is_penalty": False},
        ],
    }
    with patch.object(ts, "_request", new=AsyncMock(return_value=legacy_payload)):
        out = await fetch_shotmap_xg(None, "mt_legacy")
    assert out["available"] is True
    assert out["home_np_xg"] == 0.7
    assert out["away_np_xg"] == 0.4


@pytest.mark.asyncio
async def test_fetch_shotmap_xg_reports_no_team_ids_when_both_missing(monkeypatch):
    monkeypatch.setattr(ts, "is_enabled", lambda: True)
    bad_payload = {"data": [{"team_id": "tm_X", "expected_goals": 0.5}]}
    with patch.object(ts, "_request", new=AsyncMock(return_value=bad_payload)):
        out = await fetch_shotmap_xg(None, "mt_X")
    # Both ids missing → unavailable + missing-ids reason still surfaces.
    assert out["available"] is False
    rc = out.get("reason_codes") or []
    assert "SHOTMAP_TEAM_IDS_MISSING" in rc


# ─────────────────────────────────────────────────────────────────────
#   End-to-end via compute_xg_recent_averages with mocked client
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_xg_recent_averages_succeeds_when_thestatsapi_ids_present(monkeypatch):
    """End-to-end: when team blocks carry ``thestatsapi_recent_match_ids``
    and ``fetch_shotmap_xg`` returns valid payloads (now possible with
    both fixes in place), L1/L5 must surface real averages.
    """
    from services import football_xg_recent_averages as xgr

    monkeypatch.setattr(ts, "is_enabled", lambda: True)

    async def _fake_fetch_shotmap_xg(client, match_id, *, timeout=4.0):
        # Mimic the "Iran vs NZ" shotmap: Iran (home) ≈ 1.49 xG, NZ ≈ 1.24
        return {
            "available":    True,
            "source":       "thestatsapi_shotmap",
            "match_id":     str(match_id),
            "home_team_id": "tm_65309",
            "away_team_id": "tm_09373",
            "home_np_xg":   1.49,
            "away_np_xg":   1.24,
            "reason_codes": ["XG_FROM_STORED_SUMMARY"],
        }

    monkeypatch.setattr(xgr, "fetch_shotmap_xg", _fake_fetch_shotmap_xg)

    md = {
        "match_id": "mt_test_iran_nz",
        "home_team": {
            "id": "tm_65309", "name": "Iran",
            "thestatsapi_recent_match_ids": [
                "mt_986264843", "mt_205268566", "mt_460244644",
            ],
        },
        "away_team": {
            "id": "tm_09373", "name": "New Zealand",
            "thestatsapi_recent_match_ids": ["mt_986264843"],
        },
    }
    out = await xgr.compute_xg_recent_averages(md, use_cache=False)

    assert out["available"] is True
    assert out["source"] == "thestatsapi_shotmap"
    home_l1 = (out.get("home") or {}).get("l1") or {}
    assert home_l1.get("xg_for_avg") == pytest.approx(1.49, abs=0.01)
    assert home_l1.get("xg_against_avg") == pytest.approx(1.24, abs=0.01)
    assert home_l1.get("sample_size") == 1
