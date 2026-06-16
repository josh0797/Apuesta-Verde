"""F94.x REFACTOR — Pure / cohesive helpers extracted from
``services.data_ingestion``.

This package exists to keep the main pipeline file readable. Every
module here is **non-public** (leading underscore) and re-imported by
``data_ingestion`` only.

Rules:
  * No behavioural changes vs the pre-refactor implementation.
  * Same logging messages and reason codes.
  * Same side-effects on ``match_doc`` / DB / fixtures.
  * Helpers MUST be fail-soft (never raise) where the original code
    swallowed exceptions; we keep that contract intact.

If a helper grows beyond ~150 LOC it should be split further.
"""
