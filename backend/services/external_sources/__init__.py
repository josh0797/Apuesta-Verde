"""External Source Evidence Layer — surfaces what each external source
extracted for a given match.

Public API
==========
    collect_external_evidence(matches, sport, *, timeout_sec=20.0)
        Returns: dict[match_id_str] -> list[EvidenceItem]
            EvidenceItem = {
                source:           str,    # "fotmob" | "sofascore" | ...
                url:              str,
                title:            str|None,
                evidence_type:    str,    # one of EVIDENCE_TYPES
                extracted_data:   list[str],   # human-readable bullets
                confidence:       int,    # 0..100
                freshness:        str,    # "fresh" | "stale" | "unknown"
                used_in_analysis: bool,
                status:           str,    # "ok" | "failed" | "skipped"
                errors:           list[str],
                fetched_at:       str,    # ISO-8601
            }

Design goals
============
* **Fail-soft**: every scraper catches its own exceptions; the dispatcher
  never raises.
* **Sport-aware**: each scraper declares the sports it supports.
* **Cost-aware**: scrapers tagged `requires_unlocker=True` are skipped
  when no BrightData credentials are present.
* **Cached**: the dispatcher reads/writes a 6h TTL cache in MongoDB
  (collection `external_source_evidence`) so re-runs of the same match
  don't re-hit the upstream provider.
"""
from .dispatcher import collect_external_evidence
from .schema import EVIDENCE_TYPES, EvidenceItem  # type: ignore

__all__ = ["collect_external_evidence", "EVIDENCE_TYPES", "EvidenceItem"]
