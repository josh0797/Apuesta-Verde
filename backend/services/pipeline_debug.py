"""Pipeline debug instrumentation — per-stage candidate counts.

Provides a single ``PipelineDebug`` accumulator that the analysis
pipeline (``server._run_analysis_pipeline``) feeds at every stage so the
UI / observers can see EXACTLY where fixtures disappear.

Output shape (always JSON-serialisable, never raises)::

    {
      "pipeline_debug": {
        "provider_response_count":           42,
        "raw_fixtures_count":                42,
        "after_sport_filter_count":          42,
        "after_date_window_count":           38,
        "after_priority_league_filter_count": 12,
        "after_status_filter_count":         10,
        "after_market_filter_count":          8,
        "analysis_candidates_count":          8,
        "failure_stage":                     null,
        "failure_message":                   null,
        "stages":                            [...]   # ordered audit trail
      }
    }

Rule of thumb: ``failure_stage`` is the **first** stage whose count fell
to 0. When every stage is > 0 it stays ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Stage ids — exposed as constants so the orchestrator can't typo them.
STAGE_PROVIDER_RESPONSE        = "provider_response"
STAGE_RAW_FIXTURES             = "raw_fixtures"
STAGE_AFTER_SPORT_FILTER       = "after_sport_filter"
STAGE_AFTER_DATE_WINDOW        = "after_date_window"
STAGE_AFTER_PRIORITY_LEAGUE    = "after_priority_league_filter"
STAGE_AFTER_STATUS_FILTER      = "after_status_filter"
STAGE_AFTER_MARKET_FILTER      = "after_market_filter"
STAGE_ANALYSIS_CANDIDATES      = "analysis_candidates"

# Human-friendly diagnostic messages.
_USER_MESSAGES_ES: dict[str, str] = {
    STAGE_PROVIDER_RESPONSE:     "No se recibieron partidos desde el proveedor. Revisa provider, fecha, deporte o caché.",
    STAGE_RAW_FIXTURES:          "No hay fixtures crudos para esta ventana. La API respondió pero sin partidos.",
    STAGE_AFTER_SPORT_FILTER:    "Ningún fixture coincide con el deporte solicitado (chequea el filtro `sport`).",
    STAGE_AFTER_DATE_WINDOW:     "No hay partidos en la ventana de fechas actual (próximas 48h).",
    STAGE_AFTER_PRIORITY_LEAGUE: "Ningún partido sobrevivió al filtro de ligas prioritarias.",
    STAGE_AFTER_STATUS_FILTER:   "Todos los partidos quedaron descartados por el filtro de estado (terminados, en juego o demasiado próximos al pitazo).",
    STAGE_AFTER_MARKET_FILTER:   "Ningún partido superó el filtro de mercados disponibles.",
    STAGE_ANALYSIS_CANDIDATES:   "No quedan candidatos para analizar después de toda la cascada.",
}


# Canonical, user-required order for downstream UI rendering.
ORDERED_STAGES: tuple[str, ...] = (
    STAGE_PROVIDER_RESPONSE,
    STAGE_RAW_FIXTURES,
    STAGE_AFTER_SPORT_FILTER,
    STAGE_AFTER_DATE_WINDOW,
    STAGE_AFTER_PRIORITY_LEAGUE,
    STAGE_AFTER_STATUS_FILTER,
    STAGE_AFTER_MARKET_FILTER,
    STAGE_ANALYSIS_CANDIDATES,
)


@dataclass
class PipelineDebug:
    """Accumulator that the orchestrator updates as the pipeline runs.

    Each stage is recorded EXACTLY ONCE (subsequent calls overwrite the
    previous value but also append an audit trail entry, so we can spot
    accidental double-recordings during code review). The ``failure_stage``
    field is automatically maintained — it points to the FIRST stage that
    dropped to 0 (i.e. the one where the funnel broke).
    """
    stages: dict[str, int] = field(default_factory=dict)
    audit:  list[dict]     = field(default_factory=list)

    def record(self, stage: str, count: int, *, note: Optional[str] = None) -> None:
        """Record the count surviving ``stage``. Negative / non-int counts
        are coerced to 0 for safety. ``note`` is an optional debug string
        appended to the audit trail."""
        try:
            n = int(count)
        except (TypeError, ValueError):
            n = 0
        if n < 0:
            n = 0
        first_time = stage not in self.stages
        self.stages[stage] = n
        self.audit.append({
            "stage":   stage,
            "count":   n,
            "note":    note,
            "first":   first_time,
        })

    # ─────────────────────────────────────────────────────────────────
    # Derived properties
    # ─────────────────────────────────────────────────────────────────
    @property
    def failure_stage(self) -> Optional[str]:
        """Return the first stage in :data:`ORDERED_STAGES` where the
        funnel collapses. The rule covers two cases:

          1. A stage was recorded with ``count == 0`` → that's the failure.
          2. A stage was NEVER recorded but a *later* stage was recorded
             with ``count == 0`` → the un-recorded gap is where the
             pipeline silently broke, so we surface that as the failure.

        Stages that were never recorded and have no downstream zero
        records do NOT count as a failure (the pipeline is just
        in-progress / never reached that stage)."""
        any_recorded = bool(self.stages)
        any_downstream_zero = False
        first_zero_or_gap: Optional[str] = None
        # Walk the canonical order. Look ahead so a gap is only a failure
        # when something later collapses to zero.
        for idx, stage in enumerate(ORDERED_STAGES):
            if stage in self.stages:
                if self.stages[stage] == 0:
                    return stage
            else:
                # Check whether any later stage is recorded at 0 — if so
                # this gap caused the funnel to break.
                downstream = ORDERED_STAGES[idx + 1:]
                if any(s in self.stages and self.stages[s] == 0 for s in downstream):
                    return stage
        # If we recorded anything at all and every recorded stage was
        # positive AND every gap had no downstream zero, the pipeline
        # didn't fail.
        if not any_recorded:
            return None
        return None

    @property
    def failure_message(self) -> Optional[str]:
        s = self.failure_stage
        if s is None:
            return None
        return _USER_MESSAGES_ES.get(s) or "El pipeline cayó a cero en una etapa intermedia."

    # ─────────────────────────────────────────────────────────────────
    # Serialisation
    # ─────────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Render the canonical JSON shape consumed by the frontend.

        Missing stages surface as ``None`` (rather than 0) so the UI can
        distinguish "never reached this stage" from "reached and dropped
        to 0". Failure detection only fires on explicit 0s.
        """
        payload: dict[str, Any] = {}
        for stage in ORDERED_STAGES:
            payload[f"{stage}_count"] = self.stages.get(stage)
        payload["failure_stage"]   = self.failure_stage
        payload["failure_message"] = self.failure_message
        # Surface the audit trail too — useful for ops to spot the
        # order in which stages actually ran.
        payload["stages"]          = list(self.audit)
        return payload


def empty_debug_payload() -> dict:
    """Convenience: return a fully-populated debug dict where every
    stage is recorded as 0 + a global failure note. Used by paths that
    bail out before the orchestrator even reaches the first stage."""
    dbg = PipelineDebug()
    for stage in ORDERED_STAGES:
        dbg.record(stage, 0)
    return dbg.to_dict()


__all__ = [
    "PipelineDebug",
    "ORDERED_STAGES",
    "STAGE_PROVIDER_RESPONSE",
    "STAGE_RAW_FIXTURES",
    "STAGE_AFTER_SPORT_FILTER",
    "STAGE_AFTER_DATE_WINDOW",
    "STAGE_AFTER_PRIORITY_LEAGUE",
    "STAGE_AFTER_STATUS_FILTER",
    "STAGE_AFTER_MARKET_FILTER",
    "STAGE_ANALYSIS_CANDIDATES",
    "empty_debug_payload",
]
