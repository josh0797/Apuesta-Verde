"""Bright Data Circuit Breaker — Phase F65.

Goal
====
Stop hemorrhaging Web Unlocker requests (and dollars) when Bright Data is
clearly down, rate-limited, or refusing a domain by policy.

Design
======
* Per-host state (so a hard-block on ``scores24.live`` does NOT cripple
  ``fbref.com`` requests).
* Three states: CLOSED (normal) → OPEN (paused) → HALF_OPEN (probe).
* Defaults match the user's spec from Phase F65 planning:

    - ``BRIGHTDATA_BREAKER_FAIL_THRESHOLD`` = 5 consecutive failures.
    - ``BRIGHTDATA_BREAKER_PAUSE_SEC``     = 1800 (30 minutes).

* **Permanent-block hint**: when the upstream Bright Data error code is
  ``proxy_error`` *and* the message contains ``Access denied`` /
  ``classified as Gambling`` / ``blocked by Bright Data``, the breaker
  opens for **24h** instead of 30 min — there is no point retrying a
  policy-level refusal in the same hour.

* **Fail-soft**: the module never raises. Every public call has a
  bounded code-path even when state is corrupted.

* **Pure in-memory**: state lives in the process. We deliberately do NOT
  persist to Mongo — circuits should reset on deploy. Operators can
  call ``reset()`` from the admin endpoint to clear early.

Integration contract
====================
Callers wrap every Bright Data call like this::

    from services.external_sources.circuit_breaker import (
        is_open, record_success, record_failure,
    )

    if is_open(host):
        return None  # short-circuit, save the request
    html = await _do_bright_data_fetch(url)
    if html:
        record_success(host)
    else:
        record_failure(host, error_code=..., error_msg=...)
    return html

`host` is parsed from the URL by ``host_for(url)``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("brightdata.circuit_breaker")

# ─────────────────────────────────────────────────────────────────────
# Tunables (env-overridable)
# ─────────────────────────────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


FAIL_THRESHOLD       = _env_int("BRIGHTDATA_BREAKER_FAIL_THRESHOLD", 5)
PAUSE_SEC            = _env_int("BRIGHTDATA_BREAKER_PAUSE_SEC", 1800)        # 30 min
POLICY_BLOCK_PAUSE_SEC = _env_int("BRIGHTDATA_BREAKER_POLICY_PAUSE_SEC", 86400)  # 24h
HALF_OPEN_PROBE_GAP_SEC = _env_int("BRIGHTDATA_BREAKER_HALF_OPEN_GAP_SEC", 60)


STATE_CLOSED    = "CLOSED"
STATE_OPEN      = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


# ─────────────────────────────────────────────────────────────────────
# Per-host state
# ─────────────────────────────────────────────────────────────────────
@dataclass
class HostCircuit:
    host: str
    state: str = STATE_CLOSED
    consecutive_failures: int = 0
    opened_at: Optional[float] = None
    reopen_after: float = 0.0       # epoch when the breaker auto-transitions to HALF_OPEN
    last_probe_at: Optional[float] = None
    total_failures: int = 0
    total_successes: int = 0
    last_error_code: Optional[str] = None
    last_error_msg:  Optional[str] = None

    def snapshot(self) -> dict:
        return {
            "host":                 self.host,
            "state":                self.state,
            "consecutive_failures": self.consecutive_failures,
            "total_failures":       self.total_failures,
            "total_successes":      self.total_successes,
            "opened_at":            self.opened_at,
            "reopen_after":         self.reopen_after,
            "last_error_code":      self.last_error_code,
            "last_error_msg":       (self.last_error_msg or "")[:160] or None,
        }


_circuits: dict[str, HostCircuit] = {}
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def host_for(url: str) -> str:
    """Return a normalised hostname suitable as a circuit key.

    ``fbref.com``, ``www.fbref.com`` and ``en.fbref.com`` collapse to
    ``fbref.com`` so the breaker treats them as a single resource.
    """
    if not url:
        return "_unknown"
    try:
        netloc = urlparse(url).netloc.lower() or url.lower()
    except Exception:  # noqa: BLE001
        return "_unknown"
    netloc = netloc.split(":")[0]                        # drop :port
    netloc = netloc.split("@")[-1]                       # drop user@
    parts = netloc.split(".")
    if len(parts) > 2:
        # keep last two labels (e.g. "co.uk" stays intact for two-label TLDs
        # too — collisions there are acceptable for our circuit purposes).
        netloc = ".".join(parts[-2:])
    return netloc or "_unknown"


def _is_policy_block(error_code: Optional[str], error_msg: Optional[str]) -> bool:
    """Heuristic — Bright Data refusing a domain by policy (gambling,
    adult content, copyright). These do NOT recover by waiting 30 min,
    so we pause aggressively for 24h."""
    if not (error_code or error_msg):
        return False
    code = (error_code or "").lower()
    msg  = (error_msg  or "").lower()
    if "policy" in msg or "policy" in code:
        return True
    if "gambling" in msg or "adult" in msg or "copyright" in msg:
        return True
    if "access denied" in msg and "bright data" in msg:
        return True
    return False


def _get_or_create(host: str) -> HostCircuit:
    """Caller must hold _lock."""
    c = _circuits.get(host)
    if c is None:
        c = HostCircuit(host=host)
        _circuits[host] = c
    return c


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def is_open(url_or_host: str) -> bool:
    """Returns True when the breaker is OPEN and we should short-circuit.

    Auto-transitions OPEN → HALF_OPEN once ``reopen_after`` has passed.
    In HALF_OPEN we allow a single probe per ``HALF_OPEN_PROBE_GAP_SEC``
    window, all others return True.
    """
    host = host_for(url_or_host) if "/" in url_or_host else url_or_host.lower()
    now  = time.time()
    with _lock:
        c = _circuits.get(host)
        if not c:
            return False
        if c.state == STATE_CLOSED:
            return False
        if c.state == STATE_OPEN:
            if now >= c.reopen_after:
                c.state = STATE_HALF_OPEN
                # The cooldown elapsed → consume the *first* probe slot
                # right here. Subsequent calls within HALF_OPEN_PROBE_GAP_SEC
                # are short-circuited until either record_success closes the
                # breaker or record_failure re-opens it.
                c.last_probe_at = now
                log.info("[BREAKER] %s OPEN → HALF_OPEN (cooldown elapsed, probing)", host)
                return False
            return True
        # HALF_OPEN: allow one probe per gap.
        if c.last_probe_at is None or (now - c.last_probe_at) >= HALF_OPEN_PROBE_GAP_SEC:
            c.last_probe_at = now
            return False
        return True


def record_success(url_or_host: str) -> None:
    host = host_for(url_or_host) if "/" in url_or_host else url_or_host.lower()
    with _lock:
        c = _get_or_create(host)
        c.total_successes += 1
        if c.state != STATE_CLOSED:
            log.info("[BREAKER] %s %s → CLOSED (probe succeeded)", host, c.state)
        c.state = STATE_CLOSED
        c.consecutive_failures = 0
        c.opened_at = None
        c.reopen_after = 0.0
        c.last_probe_at = None


def record_failure(
    url_or_host: str,
    *,
    error_code: Optional[str] = None,
    error_msg:  Optional[str] = None,
) -> None:
    host = host_for(url_or_host) if "/" in url_or_host else url_or_host.lower()
    now  = time.time()
    with _lock:
        c = _get_or_create(host)
        c.total_failures += 1
        c.consecutive_failures += 1
        c.last_error_code = error_code
        c.last_error_msg  = error_msg

        # Policy-level refusal — open for 24h regardless of count.
        if _is_policy_block(error_code, error_msg):
            c.state = STATE_OPEN
            c.opened_at = now
            c.reopen_after = now + POLICY_BLOCK_PAUSE_SEC
            log.warning(
                "[BREAKER] %s OPENED for %ds — policy block (code=%s msg=%s)",
                host, POLICY_BLOCK_PAUSE_SEC, error_code, (error_msg or "")[:140],
            )
            return

        # Normal failure threshold.
        if c.consecutive_failures >= FAIL_THRESHOLD and c.state != STATE_OPEN:
            c.state = STATE_OPEN
            c.opened_at = now
            c.reopen_after = now + PAUSE_SEC
            log.warning(
                "[BREAKER] %s OPENED for %ds — %d consecutive failures (last=%s)",
                host, PAUSE_SEC, c.consecutive_failures,
                (error_msg or error_code or "?")[:140],
            )
            return

        # HALF_OPEN probe failed → re-open.
        if c.state == STATE_HALF_OPEN:
            c.state = STATE_OPEN
            c.opened_at = now
            c.reopen_after = now + PAUSE_SEC
            log.warning("[BREAKER] %s HALF_OPEN → OPEN (probe failed)", host)


def reset(host: Optional[str] = None) -> None:
    """Admin helper — wipe one or all circuits. Used by tests and by the
    `/api/admin/brightdata/reset` endpoint."""
    with _lock:
        if host is None:
            _circuits.clear()
        else:
            _circuits.pop(host_for(host) if "/" in host else host.lower(), None)


def snapshot_all() -> list[dict]:
    """Public snapshot of every tracked host — used by /api/admin/brightdata."""
    with _lock:
        return [c.snapshot() for c in _circuits.values()]


def is_brightdata_enabled() -> bool:
    """Phase F65 — global opt-IN gate.

    Defaults to True when both credentials are present (backward-compatible
    with the existing deployments that set ``BRIGHTDATA_API_KEY`` and
    expect immediate routing).

    Set ``BRIGHTDATA_ENABLED=false`` to disable globally without removing
    credentials (e.g. burning-budget incident).
    """
    flag = os.environ.get("BRIGHTDATA_ENABLED", "").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    return True


__all__ = [
    "FAIL_THRESHOLD", "PAUSE_SEC", "POLICY_BLOCK_PAUSE_SEC",
    "STATE_CLOSED", "STATE_OPEN", "STATE_HALF_OPEN",
    "HostCircuit",
    "host_for",
    "is_open", "record_success", "record_failure",
    "reset", "snapshot_all",
    "is_brightdata_enabled",
]
