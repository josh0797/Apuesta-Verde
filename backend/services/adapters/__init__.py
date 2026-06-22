"""Sprint-F98 · Adapter layer.

Every external football data provider is converted into the
**canonical adapter envelope** by a pure function (no IO when
possible). The envelope is later fed to the cascade selector
(Phase 3) and merged into the F74 ``football_data_enrichment``
schema (Phase 4).

Exposed adapters (each accepts ``raw`` + meta and returns an
envelope dict):

  * ``adapt_thesportsdb_to_f74(raw)``
  * ``adapt_sofascore_to_f74(raw, *, home_team, away_team)``
  * ``adapt_thestatsapi_to_f74(raw)``
  * ``adapt_statsbomb_to_f74(raw)``
  * ``adapt_fbref_to_f74(raw)``

All adapters MUST be:
  * pure (no IO when possible)
  * fail-soft (never raise on malformed input)
  * deterministic
  * sample-size aware
  * provenance-recording
"""
from services.adapters.thesportsdb_adapter import adapt_thesportsdb_to_f74
from services.adapters.sofascore_adapter    import adapt_sofascore_to_f74
from services.adapters.thestatsapi_adapter  import adapt_thestatsapi_to_f74
from services.adapters.statsbomb_adapter    import adapt_statsbomb_to_f74
from services.adapters.fbref_adapter        import adapt_fbref_to_f74
from services.adapters._envelope            import (
    ENVELOPE_SCHEMA_VERSION,
    new_envelope,
    set_field,
    compute_data_quality,
)

__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "new_envelope",
    "set_field",
    "compute_data_quality",
    "adapt_thesportsdb_to_f74",
    "adapt_sofascore_to_f74",
    "adapt_thestatsapi_to_f74",
    "adapt_statsbomb_to_f74",
    "adapt_fbref_to_f74",
]
