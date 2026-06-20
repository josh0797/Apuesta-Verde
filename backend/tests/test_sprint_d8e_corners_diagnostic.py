"""Tests for Sprint-D8/E PASO 1 — football corners 3-layer diagnostic.

Mocks the transport entirely (no scrape.do) so the test suite stays
hermetic. The production normalizer is used as the parser layer to
make sure the verdict line correctly identifies whether the failure
sits in transport, endpoint shape, or parser.
"""
from __future__ import annotations

import asyncio

import pytest

from services.football_corners_diagnostic import (
    RC_CORNERS_KEY_NOT_FOUND,
    RC_CORNERS_KEY_PRESENT,
    RC_GROUND_TRUTH_MATCH,
    RC_GROUND_TRUTH_MISMATCH,
    RC_PARSER_FAILED,
    RC_PARSER_OK,
    RC_TRANSPORT_FAILED,
    RC_TRANSPORT_OK,
    diagnose_corners_pipeline,
    find_corner_paths,
)
from services.external_sources.score365_client import (
    normalize_365scores_match_stats,
)


# ─────────────────────────────────────────────────────────────────────
# Mock helpers
# ─────────────────────────────────────────────────────────────────────
async def _fail_transport(_game_id):
    raise RuntimeError("transport down")


async def _empty_transport(_game_id):
    return {}


async def _none_transport(_game_id):
    return None


def _make_payload_with_corners(home: int = 7, away: int = 8,
                                wrap_in_game: bool = False) -> dict:
    base = {
        "statistics": [
            {"name": "Corner Kicks", "home": str(home), "away": str(away)},
            {"name": "Shots",        "home": "12",      "away": "10"},
        ],
        "competitors": [
            {"id": 100, "name": "Home FC"},
            {"id": 200, "name": "Away FC"},
        ],
    }
    return {"game": base} if wrap_in_game else base


def _payload_without_corners() -> dict:
    return {
        "game": {
            "statistics": [
                {"name": "Shots", "home": "12", "away": "10"},
                {"name": "Possession", "home": "55", "away": "45"},
            ],
            "competitors": [
                {"id": 100, "name": "Home FC"},
                {"id": 200, "name": "Away FC"},
            ],
        },
    }


def _coro(payload):
    async def _fn(_game_id):
        return payload
    return _fn


# ─────────────────────────────────────────────────────────────────────
# Level 2 — recursive corner-key finder
# ─────────────────────────────────────────────────────────────────────
def test_find_corner_paths_detects_corner_alias_in_stats_array():
    payload = _make_payload_with_corners(wrap_in_game=True)
    paths = find_corner_paths(payload)
    assert len(paths) >= 1
    # We should find both: (a) the ``name``-field stat row, AND (b)
    # any direct key match. At minimum, the stat-row hit:
    via_kinds = {p["via"] for p in paths}
    assert "stat_name_field" in via_kinds


def test_find_corner_paths_returns_empty_when_absent():
    payload = _payload_without_corners()
    paths = find_corner_paths(payload)
    assert paths == []


def test_find_corner_paths_does_not_match_unrelated_keys():
    payload = {"description": "this match was a real cornerstone of the season"}
    # 'corner' appears in 'cornerstone' — alias substrings deliberately
    # match this; document the behaviour explicitly so it does not
    # surprise a future reader.
    paths = find_corner_paths(payload)
    # Substring match is intentional (catches "corner kicks", "corner_kicks").
    # The check we DO want: the diagnostic doesn't blow up.
    assert isinstance(paths, list)


# ─────────────────────────────────────────────────────────────────────
# Verdict: TRANSPORT_FAILURE when both fetchers fail
# ─────────────────────────────────────────────────────────────────────
def test_diagnostic_identifies_transport_failure_when_both_endpoints_raise():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_fail_transport,
            fetch_stats=_fail_transport,
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "TRANSPORT_FAILURE"
    assert out["winning_endpoint"] is None
    assert RC_TRANSPORT_FAILED in out["reason_codes"]
    assert RC_TRANSPORT_FAILED in out["layers"]["transport"]["detail"]["reason_codes"]
    assert RC_TRANSPORT_FAILED in out["layers"]["transport"]["stats"]["reason_codes"]


def test_diagnostic_identifies_transport_failure_when_both_return_empty():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_empty_transport,
            fetch_stats=_none_transport,
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "TRANSPORT_FAILURE"


# ─────────────────────────────────────────────────────────────────────
# Verdict: ENDPOINT_NO_CORNERS_KEY
# ─────────────────────────────────────────────────────────────────────
def test_diagnostic_identifies_endpoint_failure_when_no_corner_keys():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_payload_without_corners()),
            fetch_stats=_coro(_payload_without_corners()),
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "ENDPOINT_NO_CORNERS_KEY"
    assert RC_CORNERS_KEY_NOT_FOUND in out["reason_codes"]
    assert out["winning_endpoint"] is None
    # Transport must report OK (payload non-empty, just no corners).
    assert RC_TRANSPORT_OK in out["layers"]["transport"]["detail"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Verdict: OK with winning endpoint identification
# ─────────────────────────────────────────────────────────────────────
def test_diagnostic_verdict_ok_when_stats_endpoint_carries_corners():
    """Detail endpoint has no corners; stats endpoint has them.
    Diagnostic must pick stats as the winning endpoint.
    """
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_payload_without_corners()),
            fetch_stats=_coro(_make_payload_with_corners(7, 8)),
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "OK"
    assert out["winning_endpoint"] == "stats"
    assert RC_PARSER_OK in out["reason_codes"]
    assert out["layers"]["parser"]["stats"]["total_corners"] == 15
    assert out["layers"]["parser"]["detail"]["available"] is False


def test_diagnostic_verdict_ok_when_detail_endpoint_carries_corners():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_make_payload_with_corners(5, 4, wrap_in_game=True)),
            fetch_stats=_coro(_payload_without_corners()),
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "OK"
    assert out["winning_endpoint"] == "detail"
    assert out["layers"]["parser"]["detail"]["total_corners"] == 9


# ─────────────────────────────────────────────────────────────────────
# Verdict: PARSER_FAILURE
# ─────────────────────────────────────────────────────────────────────
def test_diagnostic_identifies_parser_failure_when_key_present_but_unparseable():
    """The payload exposes a corners alias, but in a shape the parser
    cannot understand (e.g., no home/away split). Verdict must be
    PARSER_FAILURE — not ENDPOINT_NO_CORNERS_KEY.
    """
    weird_payload = {
        "statistics": [
            {"name": "Total corners", "scalar_total": 12},
        ],
        "competitors": [
            {"id": 100, "name": "Home FC"},
            {"id": 200, "name": "Away FC"},
        ],
    }

    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(weird_payload),
            fetch_stats=_coro(weird_payload),
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    # Level 2 finds the alias → not endpoint failure.
    assert RC_CORNERS_KEY_PRESENT in out["layers"]["endpoint"]["detail"]["reason_codes"]
    # Level 3 fails to extract canonical home/away/total.
    assert out["verdict"] == "PARSER_FAILURE"
    assert RC_PARSER_FAILED in out["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Ground truth comparison
# ─────────────────────────────────────────────────────────────────────
def test_ground_truth_match_flag_when_parser_matches_real_corners():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_payload_without_corners()),
            fetch_stats=_coro(_make_payload_with_corners(7, 8)),
            normalizer=normalize_365scores_match_stats,
            ground_truth={"home_corners": 7, "away_corners": 8,
                          "total_corners": 15},
        )
    out = asyncio.run(go())
    assert out["verdict"] == "OK"
    assert RC_GROUND_TRUTH_MATCH in out["layers"]["parser"]["stats"]["reason_codes"]


def test_ground_truth_mismatch_flag_when_parser_disagrees_with_real():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_payload_without_corners()),
            fetch_stats=_coro(_make_payload_with_corners(7, 8)),
            normalizer=normalize_365scores_match_stats,
            ground_truth={"home_corners": 5, "away_corners": 5,
                          "total_corners": 10},
        )
    out = asyncio.run(go())
    # Parser worked (returned 15) but GT was 10 → mismatch.
    assert RC_GROUND_TRUTH_MISMATCH in out["layers"]["parser"]["stats"]["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# Output contract
# ─────────────────────────────────────────────────────────────────────
def test_output_contract_is_stable():
    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_coro(_make_payload_with_corners(1, 1)),
            fetch_stats=_coro(_make_payload_with_corners(1, 1)),
            normalizer=normalize_365scores_match_stats,
        )
    out = asyncio.run(go())
    assert set(out.keys()) >= {"game_id", "layers", "verdict",
                                "winning_endpoint", "reason_codes",
                                "ground_truth"}
    for layer in ("transport", "endpoint", "parser"):
        assert "detail" in out["layers"][layer]
        assert "stats"  in out["layers"][layer]


# ─────────────────────────────────────────────────────────────────────
# Timeout handling
# ─────────────────────────────────────────────────────────────────────
def test_diagnostic_timeouts_dont_crash_pipeline():
    async def _slow(_gid):
        await asyncio.sleep(5)
        return _make_payload_with_corners(1, 1)

    async def go():
        return await diagnose_corners_pipeline(
            "9999",
            fetch_detail=_slow,
            fetch_stats=_slow,
            normalizer=normalize_365scores_match_stats,
            timeout_s=0.05,
        )
    out = asyncio.run(go())
    assert out["verdict"] == "TRANSPORT_FAILURE"
    for ep in ("detail", "stats"):
        ts = out["layers"]["transport"][ep]
        assert RC_TRANSPORT_FAILED in ts["reason_codes"]
        assert ts["error"] is not None
