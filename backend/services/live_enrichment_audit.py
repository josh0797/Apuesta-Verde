"""F94.3 — Live Enrichment Persistence Audit.

This module enforces a single, hard rule:

    Live fixture *discovery* != Live fixture *persistence*.

If the provider returned ``discovery_count > 0`` (i.e. there *are*
fixtures in the world right now) but our pipeline persisted
``persisted_count == 0`` rows, then something downstream (enrichment,
normalisation, ingestion) silently dropped every single one of them.

That is exactly the failure mode of the historical
``UnboundLocalError: h2h_source`` bug in ``data_ingestion.py`` — the
discovery feed was healthy, but every enrich crashed and the user saw
"EN CURSO AHORA: 0" with zero diagnostics.

To prevent that from regressing silently, the visibility endpoint must
surface an explicit, machine-readable error code:

    ``LIVE_ENRICHMENT_DROPPED_FIXTURES``

This module is the **pure function** that decides whether the rule
fires. The rest of the system (the visibility computer, the UI banner,
tests) only need to agree on this contract.

Design notes:
  * Pure (no I/O, no DB, no httpx, no logging side-effects).
  * Fail-soft inputs: ``None`` is treated as 0 / unknown.
  * The output dict is small and stable so consumers can rely on the
    keys regardless of whether the rule triggered.

Public API:
  * :data:`LIVE_ENRICHMENT_DROPPED_FIXTURES` — canonical error code.
  * :func:`evaluate_enrichment_drop` — pure evaluator.

Contract::

    >>> evaluate_enrichment_drop(discovery_count=3, persisted_count=0)
    {
      "triggered": True,
      "error_code": "LIVE_ENRICHMENT_DROPPED_FIXTURES",
      "message": "Discovered 3 live fixture(s) from upstream provider(s) but persisted 0. ..."
    }

    >>> evaluate_enrichment_drop(discovery_count=3, persisted_count=2)
    {"triggered": False, "error_code": None, "message": None}

    >>> evaluate_enrichment_drop(discovery_count=0, persisted_count=0)
    {"triggered": False, "error_code": None, "message": None}
"""
from __future__ import annotations

from typing import Optional, TypedDict

LIVE_ENRICHMENT_DROPPED_FIXTURES = "LIVE_ENRICHMENT_DROPPED_FIXTURES"

_MESSAGE_TEMPLATE = (
    "Discovered {discovery} live fixture(s) from upstream provider(s) "
    "but persisted {persisted}. The enrichment/persistence pipeline "
    "dropped every fixture silently. Review ingest_live / "
    "data_ingestion logs and provider connectivity, then retry."
)


class EnrichmentDropEvaluation(TypedDict):
    """Stable output shape of :func:`evaluate_enrichment_drop`."""
    triggered: bool
    error_code: Optional[str]
    message: Optional[str]


def _safe_int(value, default: int = 0) -> int:
    """Coerce ``value`` to a non-negative int. ``None``/garbage -> default."""
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def evaluate_enrichment_drop(
    discovery_count,
    persisted_count,
) -> EnrichmentDropEvaluation:
    """Decide whether the *Live Enrichment Dropped Fixtures* rule fires.

    Args:
      discovery_count: Number of live fixtures returned by the upstream
        provider/aggregator (pre-enrichment). ``None`` is coerced to 0.
      persisted_count: Number of live fixtures currently persisted in
        the matches collection (``is_live=True``). ``None`` is coerced
        to 0.

    Returns:
      A dict with ``triggered`` (bool), ``error_code`` (str or None) and
      ``message`` (str or None). Stable schema — keys always present.

    Rule:
      Triggered ⟺ ``discovery_count > 0`` AND ``persisted_count == 0``.
    """
    d = _safe_int(discovery_count)
    p = _safe_int(persisted_count)

    if d > 0 and p == 0:
        return EnrichmentDropEvaluation(
            triggered=True,
            error_code=LIVE_ENRICHMENT_DROPPED_FIXTURES,
            message=_MESSAGE_TEMPLATE.format(discovery=d, persisted=p),
        )
    return EnrichmentDropEvaluation(
        triggered=False,
        error_code=None,
        message=None,
    )


__all__ = [
    "LIVE_ENRICHMENT_DROPPED_FIXTURES",
    "EnrichmentDropEvaluation",
    "evaluate_enrichment_drop",
]
