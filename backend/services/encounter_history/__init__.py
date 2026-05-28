"""Encounter History Engine — long-term memory of past matchups.

Mission
-------
When the system has already analysed (and graded) the same matchup before, this
module lets the analyst engine answer questions like:
  • What did we recommend last time?
  • Did it win or lose?
  • Which markets historically performed best between these two teams?
  • Are there repeated patterns (tight games, lots of cards, under 2.5 trend)?

This is the "learning across encounters" loop the user explicitly asked for.

Public API
----------
    await record_encounter(db, *, pick, sport, ...)     — upsert on pick settle
    await get_encounter_memory(db, sport, home, away)   — fetch + detect patterns
    detect_patterns(history_items)                       — pure analyser

Design principles
-----------------
1. **Locality-agnostic key**: `encounter_key()` from match_key — so
   Pumas vs Cruz Azul and Cruz Azul vs Pumas point to the same memory bucket.
2. **Append-only**: we never overwrite an old encounter; we just upsert
   the row whose pick_uid matches (re-grading allowed).
3. **Fail-soft**: if mongo is unavailable, the engine continues without
   memory. The analyst payload simply has `encounter_memory.available = False`.
4. **Multi-sport**: works for football, basketball, baseball — patterns are
   sport-aware. For MVP only football has rich pattern detectors; the other
   sports return raw history + win-rate, no special pattern detection yet.
"""
from __future__ import annotations

from .encounter_service import (
    ENCOUNTER_VERSION,
    record_encounter,
    get_encounter_memory,
    ensure_indexes as ensure_encounter_indexes,
)
from .pattern_detector import detect_patterns

__all__ = [
    "ENCOUNTER_VERSION",
    "record_encounter",
    "get_encounter_memory",
    "detect_patterns",
    "ensure_encounter_indexes",
]
