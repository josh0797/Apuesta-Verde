"""Phase F65 — Circuit breaker unit tests.

Pins the state machine + policy-block detection + per-host isolation +
opt-IN gate (BRIGHTDATA_ENABLED).
"""
from __future__ import annotations

import os
import time

import pytest

from services.external_sources import circuit_breaker as cb


@pytest.fixture(autouse=True)
def _reset_breaker():
    cb.reset()
    yield
    cb.reset()


# ─────────────────────────────────────────────────────────────────────
# host_for — URL → canonical host key.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url,expected", [
    ("https://www.fbref.com/en/foo",        "fbref.com"),
    ("https://fbref.com/en/foo",            "fbref.com"),
    ("https://en.fbref.com/foo",            "fbref.com"),
    ("https://scores24.live/es/match",      "scores24.live"),
    ("http://understat.com:8080/league",    "understat.com"),
    ("",                                    "_unknown"),
])
def test_host_for_normalises_subdomains(url: str, expected: str) -> None:
    assert cb.host_for(url) == expected


# ─────────────────────────────────────────────────────────────────────
# Closed → Open transition after FAIL_THRESHOLD consecutive failures.
# ─────────────────────────────────────────────────────────────────────
def test_closes_until_fail_threshold_reached() -> None:
    url = "https://fbref.com/foo"
    for _ in range(cb.FAIL_THRESHOLD - 1):
        cb.record_failure(url, error_code="timeout", error_msg="x")
        assert cb.is_open(url) is False, "Should still be CLOSED below threshold"

    cb.record_failure(url, error_code="timeout", error_msg="x")
    assert cb.is_open(url) is True
    snap = [s for s in cb.snapshot_all() if s["host"] == "fbref.com"][0]
    assert snap["state"] == cb.STATE_OPEN
    assert snap["consecutive_failures"] == cb.FAIL_THRESHOLD


# ─────────────────────────────────────────────────────────────────────
# Recovery — record_success closes the breaker and resets counters.
# ─────────────────────────────────────────────────────────────────────
def test_record_success_resets_counters_and_closes() -> None:
    url = "https://fbref.com/foo"
    for _ in range(cb.FAIL_THRESHOLD):
        cb.record_failure(url, error_code="x", error_msg="x")
    assert cb.is_open(url) is True

    cb.record_success(url)
    assert cb.is_open(url) is False
    snap = [s for s in cb.snapshot_all() if s["host"] == "fbref.com"][0]
    assert snap["state"] == cb.STATE_CLOSED
    assert snap["consecutive_failures"] == 0


# ─────────────────────────────────────────────────────────────────────
# Per-host isolation — opening fbref.com MUST NOT affect understat.com.
# ─────────────────────────────────────────────────────────────────────
def test_per_host_isolation() -> None:
    for _ in range(cb.FAIL_THRESHOLD):
        cb.record_failure("https://fbref.com/foo", error_code="x", error_msg="x")

    assert cb.is_open("https://fbref.com/anything")    is True
    assert cb.is_open("https://understat.com/league")  is False


# ─────────────────────────────────────────────────────────────────────
# Policy block — Bright Data refuses gambling/adult/copyright domains.
# ─────────────────────────────────────────────────────────────────────
def test_policy_block_opens_immediately_for_24h() -> None:
    url = "https://scores24.live/es/match"
    cb.record_failure(
        url,
        error_code="proxy_error",
        error_msg=("Access denied: scores24.live is classified as Gambling "
                   "and blocked by Bright Data as it might breach Bright Data "
                   "usage policy."),
    )
    assert cb.is_open(url) is True
    snap = [s for s in cb.snapshot_all() if s["host"] == "scores24.live"][0]
    assert snap["state"] == cb.STATE_OPEN
    # The reopen_after timestamp must be at least 23h into the future
    # (allowing for clock skew on slow CI hosts).
    assert (snap["reopen_after"] - time.time()) >= 23 * 3600


# ─────────────────────────────────────────────────────────────────────
# Half-open probe path — after cooldown, breaker auto-probes.
# ─────────────────────────────────────────────────────────────────────
def test_half_open_allows_single_probe_then_locks_back(monkeypatch) -> None:
    url = "https://fbref.com/foo"
    for _ in range(cb.FAIL_THRESHOLD):
        cb.record_failure(url, error_code="x", error_msg="x")
    assert cb.is_open(url) is True

    # Fast-forward the reopen_after past the cooldown by mutating state directly.
    snap_host = cb.host_for(url)
    with cb._lock:
        c = cb._circuits[snap_host]
        c.reopen_after = time.time() - 1  # cooldown elapsed

    # First call after cooldown → HALF_OPEN, allows the probe through.
    assert cb.is_open(url) is False
    # Second call within HALF_OPEN_PROBE_GAP_SEC → still True (locked).
    assert cb.is_open(url) is True

    # Probe fails → breaker re-opens.
    cb.record_failure(url, error_code="x", error_msg="x")
    snap = [s for s in cb.snapshot_all() if s["host"] == snap_host][0]
    assert snap["state"] == cb.STATE_OPEN


# ─────────────────────────────────────────────────────────────────────
# reset() — admin helper wipes one or all circuits.
# ─────────────────────────────────────────────────────────────────────
def test_reset_clears_host_state() -> None:
    cb.record_failure("https://fbref.com/foo", error_code="x", error_msg="x")
    cb.record_failure("https://understat.com/x", error_code="x", error_msg="x")
    assert len(cb.snapshot_all()) == 2

    cb.reset("fbref.com")
    hosts = {s["host"] for s in cb.snapshot_all()}
    assert hosts == {"understat.com"}

    cb.reset()
    assert cb.snapshot_all() == []


# ─────────────────────────────────────────────────────────────────────
# Opt-IN gate — BRIGHTDATA_ENABLED env flag.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("flag_val,expected", [
    (None,    True),       # default ON when credentials present
    ("",      True),
    ("true",  True),
    ("1",     True),
    ("false", False),
    ("FALSE", False),
    ("0",     False),
    ("off",   False),
    ("no",    False),
])
def test_is_brightdata_enabled_respects_flag(monkeypatch, flag_val, expected):
    if flag_val is None:
        monkeypatch.delenv("BRIGHTDATA_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BRIGHTDATA_ENABLED", flag_val)
    assert cb.is_brightdata_enabled() is expected
