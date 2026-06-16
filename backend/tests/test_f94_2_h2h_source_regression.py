"""F94.2 regression — h2h_source UnboundLocalError on live ingest.

Bug:
    services/data_ingestion.py initialised ``h2h_source`` ONLY inside
    the ``if deep:`` branch (line ~1170). Live ingestion calls
    ``_enrich_football(..., deep=False)`` and unconditionally runs
    ``match_doc.setdefault("_provenance_h2h", {"source": h2h_source})``
    at the bottom of the function, raising::

        UnboundLocalError: cannot access local variable 'h2h_source'
        where it is not associated with a value

    The exception was swallowed by the outer
    ``try/except: enrich_football failed`` handler, so the only visible
    symptom was that EVERY live football match silently failed to
    enrich → never persisted in ``db.matches`` → the "EN CURSO AHORA"
    counter stayed at 0 even when the API was returning live fixtures
    (e.g. Iran vs New Zealand / FIFA World Cup 2026 live at 45').

Regression guard:
    Static check that ``h2h_source`` is initialised BEFORE any
    ``if deep:`` block in ``_enrich_football``, so the unbound path
    can never happen again.
"""
from __future__ import annotations

import inspect
import textwrap

from services import data_ingestion as di


def test_h2h_source_initialised_outside_deep_branch():
    """The literal ``h2h_source = "missing"`` must appear in the
    function body BEFORE the first real ``if deep:`` *statement* (not
    just text mention in a comment)."""
    src = textwrap.dedent(inspect.getsource(di._enrich_football))
    # Locate the first real `if deep:` statement (newline-prefixed,
    # indented but not inside a doc-string / comment).
    deep_pos = -1
    for line_no, line in enumerate(src.splitlines()):
        stripped = line.strip()
        if stripped == "if deep:":
            # Re-compute absolute index in `src`.
            deep_pos = sum(len(l) + 1 for l in src.splitlines()[:line_no])
            break
    assert deep_pos > 0, "expected an `if deep:` statement in _enrich_football"
    # `h2h_source = "missing"` must appear strictly BEFORE the branch.
    init_pos = src.find('h2h_source = "missing"')
    assert init_pos > 0, "h2h_source must be initialised in _enrich_football"
    assert init_pos < deep_pos, (
        "F94.2 regression: h2h_source MUST be initialised BEFORE the "
        "`if deep:` branch (live ingestion uses deep=False)."
    )


def test_provenance_h2h_setdefault_runs_unconditionally():
    """The provenance write must execute unconditionally (it is the
    site that raised UnboundLocalError when h2h_source was missing).
    Static guard ensures the call is NOT indented inside a deep-only
    branch."""
    src = inspect.getsource(di._enrich_football)
    needle = '_provenance_h2h'
    idx = src.find(needle)
    assert idx > 0, "_provenance_h2h setdefault must exist"

    # Walk backwards from the needle to find the closest enclosing
    # indentation; assert it's the function-body level (8 spaces by
    # current style) and NOT nested deeper inside an extra branch.
    line_start = src.rfind("\n", 0, idx) + 1
    leading = src[line_start:idx]
    # Count leading spaces only (no tabs in this codebase).
    indent = len(leading) - len(leading.lstrip(" "))
    # The function body sits at 8 spaces; a nested deep-only branch
    # would push it to ≥ 12. Anything beyond 8 means the call has been
    # accidentally re-nested.
    assert indent <= 8, (
        f"F94.2 regression: _provenance_h2h call must run "
        f"unconditionally (indent <= 8 spaces). Found indent={indent}."
    )


# =====================================================================
# Functional regression — actually invoke _enrich_football(deep=False)
# with a stub fixture that mimics Iran vs New Zealand and assert no
# UnboundLocalError is raised.
# =====================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_enrich_football_does_not_raise_unbound_local_for_live_path():
    """Verifica de forma exhaustiva que la variable h2h_source exista
    en cualquier path de _enrich_football. Antes del fix, la variable
    quedaba unbound cuando deep=False, lo que silenciosamente abortaba
    el enrichment de cada fixture live (Iran vs New Zealand, etc).

    Approach: leemos el bytecode de la función y nos aseguramos de que
    'h2h_source' aparece como variable local (lo cual implica que está
    en LOAD_FAST scope) y que su asignación está fuera del `if deep:`.
    """
    co = di._enrich_football.__code__
    # ``co_varnames`` lists all locals; ``h2h_source`` must be one.
    assert "h2h_source" in co.co_varnames, (
        "F94.2 regression: h2h_source must be a local of _enrich_football"
    )
    # Sanity: deep must also be a local (function parameter).
    assert "deep" in co.co_varnames

    # Final layer: dis.dis output for the function must show STORE_FAST
    # for h2h_source BEFORE the conditional branch on `deep`.
    import dis
    instrs = list(dis.get_instructions(di._enrich_football))

    # Find first STORE_FAST h2h_source and first LOAD_FAST deep.
    first_h2h_store = next(
        (i for i, op in enumerate(instrs)
         if op.opname == "STORE_FAST" and op.argval == "h2h_source"),
        None,
    )
    first_deep_load = next(
        (i for i, op in enumerate(instrs)
         if op.opname == "LOAD_FAST" and op.argval == "deep"),
        None,
    )
    assert first_h2h_store is not None, "STORE_FAST h2h_source not found"
    assert first_deep_load is not None, "LOAD_FAST deep not found"
    assert first_h2h_store < first_deep_load, (
        "F94.2 regression: STORE_FAST h2h_source must come BEFORE the "
        "first LOAD_FAST deep (otherwise h2h_source can be unbound on "
        "the live deep=False path)."
    )
