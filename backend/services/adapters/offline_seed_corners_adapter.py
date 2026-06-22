"""Sprint-F99.1 · Offline-seed corners → F74 envelope adapter (puro).

Este adapter convierte los documentos crudos de la colección
``football_team_corners_offline_seed`` en envelopes canónicos F98/F74.

Decisiones binding del usuario (F99.1):

  1. **El seed de córners es la única fuente** que rellena este adapter.
     Aquí NO se mezclan otras colecciones (xG, goles, posesión). Cada
     familia métrica usa su seed propio.

  2. **``seed_partial`` no es una colección nueva**: es un estado
     derivado de la lectura de la misma colección
     ``football_team_corners_offline_seed``. La clasificación entre
     ``offline_seed`` y ``seed_partial`` ocurre al EVALUAR el documento
     (sample_size, underlying_source) — no al guardar.

  3. **Adapters puros**: ni reciben ``db``, ni hacen IO, ni consultan
     Mongo. La hidratación es responsabilidad del hydrator
     (``football_offline_seed_hydrator``) que precede al builder.

  4. **Métricas cubiertas** (exclusivas de córners):
       * ``corners_for_l5``      / ``corners_against_l5``
       * ``corners_for_l15``     / ``corners_against_l15``
       * ``corners_total_l5``    / ``corners_total_l15``
       * ``recent_fixtures``     (rows mínimos: date, opponent, corners_for/against)
       * ``l5_sample_size``      / ``l15_sample_size`` (vía sample_sizes)
     **NO se rellenan**: xG, xGA, goles a favor/en contra, shots, possession.

  5. **Política de partial**: un lado se considera ``seed_partial`` cuando::

       sample_size < min_sample   ó
       underlying_source == "promoted_from_online"   ó
       documento marcado como muestra incompleta (heurística)

     De lo contrario, el lado se considera ``offline_seed`` (rico).

  6. **Granularidad por lado**: el adapter produce DOS envelopes
     independientes (``offline_seed`` y ``seed_partial``). Un equipo
     "rico" llena el envelope offline_seed; un equipo "parcial" llena
     el envelope seed_partial. El cascade luego selecciona campo por
     campo respetando el ranking F99.

Raw shape esperado (producido por el hydrator)::

    {
      "home": <lookup_doc_home>,
      "away": <lookup_doc_away>,
      "min_sample": 3,          # opcional, default 3
      "l5":  True,              # opcional, default True
      "l15": True,              # opcional, default True
    }

Donde ``<lookup_doc>`` proviene de
``services.football_corners_offline_seed.get_offline_corners_history`` y
trae al menos::

    {
      "available":        True,
      "matches":          [<row>, ...],
      "matches_count":    int,
      "underlying_source": Optional[str],
    }
"""
from __future__ import annotations

from typing import Any, Optional

from ._envelope import (
    RC_MAPPING_OK,
    RC_MAPPING_PARTIAL,
    RC_NO_USABLE_FIELDS,
    RC_RAW_EMPTY,
    RC_RAW_NOT_DICT,
    RC_SAMPLE_TOO_SMALL,
    _last_n,
    _safe_int,
    _safe_mean,
    envelope_unavailable,
    finalize_envelope,
    new_envelope,
    set_field,
)

SOURCE_OFFLINE   = "offline_seed"
SOURCE_PARTIAL   = "seed_partial"

# Reason codes específicos del adapter F99.1.
RC_SEED_PARTIAL_FROM_PROMOTION = "SEED_PARTIAL_PROMOTED_FROM_ONLINE"
RC_SEED_PARTIAL_LOW_SAMPLE     = "SEED_PARTIAL_LOW_SAMPLE"
RC_SEED_FULL_SAMPLE            = "SEED_FULL_SAMPLE"
RC_SEED_NO_MATCHES             = "SEED_NO_MATCHES"
RC_SEED_DOC_UNAVAILABLE        = "SEED_DOC_UNAVAILABLE"

_DEFAULT_MIN_SAMPLE = 3
_L5_WINDOW  = 5
_L15_WINDOW = 15


# ─────────────────────────────────────────────────────────────────────
# Helpers (pure)
# ─────────────────────────────────────────────────────────────────────
def _side_doc(raw: Any, side: str) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    doc = raw.get(side)
    if not isinstance(doc, dict):
        return None
    return doc


def _matches_of(doc: Optional[dict]) -> list[dict]:
    if not isinstance(doc, dict):
        return []
    matches = doc.get("matches")
    if not isinstance(matches, list):
        return []
    # Defensive: keep only well-shaped rows.
    out = []
    for m in matches:
        if isinstance(m, dict):
            out.append(m)
    return out


def _is_partial_side(doc: Optional[dict], *, min_sample: int) -> tuple[bool, str]:
    """Return (is_partial, reason_code) for a side document.

    A side is considered partial when ANY of these hold:
      * the seed lookup itself is missing/unavailable (no doc),
      * total sample is below ``min_sample``,
      * the document is marked as ``promoted_from_online`` (online-derived).

    Returns ``(True, reason_code)`` if partial; ``(False, RC_SEED_FULL_SAMPLE)``
    otherwise. ``(True, RC_SEED_NO_MATCHES)`` when the seed has zero rows.
    """
    if not isinstance(doc, dict) or not doc.get("available", True):
        return True, RC_SEED_DOC_UNAVAILABLE
    matches = _matches_of(doc)
    if not matches:
        return True, RC_SEED_NO_MATCHES
    if len(matches) < int(min_sample):
        return True, RC_SEED_PARTIAL_LOW_SAMPLE
    underlying = doc.get("underlying_source") or doc.get("source")
    if isinstance(underlying, str) and underlying.lower() in (
        "promoted_from_online",
        "online_partial",
    ):
        return True, RC_SEED_PARTIAL_FROM_PROMOTION
    return False, RC_SEED_FULL_SAMPLE


def _aggregate_corners(matches: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """Compute (mean corners_for, mean corners_against) ignoring None rows."""
    if not matches:
        return None, None
    cfor: list[int] = []
    cag:  list[int] = []
    for row in matches:
        cf = _safe_int(row.get("corners_for"))
        ca = _safe_int(row.get("corners_against"))
        if cf is not None:
            cfor.append(cf)
        if ca is not None:
            cag.append(ca)
    mean_for = _safe_mean(cfor) if cfor else None
    mean_ag  = _safe_mean(cag)  if cag else None
    return mean_for, mean_ag


def _normalize_recent_row(row: dict) -> Optional[dict]:
    """Project a seed match row into the recent_fixtures contract used by F74.

    Critically: we DO NOT include goals/xG/shots fields here. Even if the
    seed document carries goals_for/goals_against, those metrics belong
    to a different seed family (see binding policy point 4).
    """
    if not isinstance(row, dict):
        return None
    date = row.get("date")
    opp  = row.get("opponent")
    cf   = _safe_int(row.get("corners_for"))
    ca   = _safe_int(row.get("corners_against"))
    if date is None or opp is None or (cf is None and ca is None):
        return None
    venue = row.get("venue")
    out = {
        "date":             date,
        "opponent":         opp,
        "corners_for":      cf,
        "corners_against":  ca,
    }
    if venue in ("home", "away"):
        out["venue"] = venue
    return out


def _fill_side_corners(
    env: dict,
    *,
    side: str,
    matches: list[dict],
    sample_size: int,
    l5: bool,
    l15: bool,
    is_l15_partial: bool,
) -> int:
    """Fill the side block of the envelope with corners-only metrics.

    Returns the number of non-null fields written (for completeness).
    """
    written = 0

    if l5:
        l5_rows = _last_n(matches, _L5_WINDOW)
        l5_n    = len(l5_rows)
        l5_for, l5_ag = _aggregate_corners(l5_rows)
        if set_field(env, f"{side}.corners_for_l5",     l5_for, sample_size=l5_n):
            written += 1
        if set_field(env, f"{side}.corners_against_l5", l5_ag, sample_size=l5_n):
            written += 1
        # Total = for + against if both available; otherwise skip (no fakes).
        if l5_for is not None and l5_ag is not None:
            if set_field(env, f"{side}.corners_total_l5", l5_for + l5_ag, sample_size=l5_n):
                written += 1
        # Sample size hint (kept consistent with builder expectations).
        env["sample_sizes"][f"{side}.l5_sample_size"] = l5_n
        # Recent fixtures (corners-only projection).
        proj = []
        for r in l5_rows:
            row = _normalize_recent_row(r)
            if row:
                proj.append(row)
        if proj:
            # We use set_field with reason_codes so provenance is consistent.
            set_field(env, f"{side}.recent_fixtures", proj, sample_size=l5_n)
            written += 1

    if l15:
        l15_rows = _last_n(matches, _L15_WINDOW)
        l15_n    = len(l15_rows)
        l15_for, l15_ag = _aggregate_corners(l15_rows)
        if set_field(env, f"{side}.corners_for_l15",     l15_for, sample_size=l15_n):
            written += 1
        if set_field(env, f"{side}.corners_against_l15", l15_ag, sample_size=l15_n):
            written += 1
        if l15_for is not None and l15_ag is not None:
            if set_field(env, f"{side}.corners_total_l15", l15_for + l15_ag,
                         sample_size=l15_n):
                written += 1
        env["sample_sizes"][f"{side}.l15_sample_size"] = l15_n
        if is_l15_partial:
            env["sample_sizes"][f"{side}.l15_is_partial"] = True

    return written


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def adapt_offline_seed_corners_to_f74(
    raw: Any,
    *,
    home_team: str = "",
    away_team: str = "",
    min_sample: int = _DEFAULT_MIN_SAMPLE,
    l5: bool = True,
    l15: bool = True,
) -> dict:
    """Build the ``source=offline_seed`` envelope (FULL-sample side only).

    A side is included **only** when ``_is_partial_side`` returns False
    for that side. The other side's slot in the envelope stays empty
    (``available=True`` overall as long as at least one side is rich).

    Side note: when BOTH sides are partial, this envelope reports
    ``available=False`` with reason ``NO_USABLE_FIELDS`` and the cascade
    falls through to the ``seed_partial`` ranking.
    """
    # Guards.
    if raw is None:
        return envelope_unavailable(source=SOURCE_OFFLINE, reason=RC_RAW_EMPTY)
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE_OFFLINE, reason=RC_RAW_NOT_DICT)

    min_sample = int(raw.get("min_sample", min_sample) or _DEFAULT_MIN_SAMPLE)
    env = new_envelope(source=SOURCE_OFFLINE, available=True)
    env["sources"]["raw_keys"] = ["home", "away"]

    extra_codes: list[str] = []
    fields_written = 0

    for side in ("home", "away"):
        doc = _side_doc(raw, side)
        partial, rc = _is_partial_side(doc, min_sample=min_sample)
        if partial:
            # Skip this side; seed_partial adapter will cover it.
            env["field_provenance"][f"{side}._side_skipped"] = {
                "source":       SOURCE_OFFLINE,
                "sample_size":  len(_matches_of(doc)),
                "reason_codes": [rc],
            }
            continue
        matches = _matches_of(doc)
        # L15 is partial when ``len(matches) < _L15_WINDOW`` even if L5 is full.
        l15_is_partial = len(matches) < _L15_WINDOW
        fields_written += _fill_side_corners(
            env,
            side=side,
            matches=matches,
            sample_size=len(matches),
            l5=l5,
            l15=l15,
            is_l15_partial=l15_is_partial,
        )
        extra_codes.append(RC_SEED_FULL_SAMPLE)

    if fields_written == 0:
        env["available"] = False
        extra_codes.append(RC_NO_USABLE_FIELDS)

    return finalize_envelope(env, extra_codes=extra_codes)


def adapt_seed_partial_corners_to_f74(
    raw: Any,
    *,
    home_team: str = "",
    away_team: str = "",
    min_sample: int = _DEFAULT_MIN_SAMPLE,
    l5: bool = True,
    l15: bool = True,
) -> dict:
    """Build the ``source=seed_partial`` envelope (PARTIAL-sample sides only).

    Mirrors :func:`adapt_offline_seed_corners_to_f74` but inverts the
    side selection: only sides classified as partial (or whose doc is
    promoted_from_online) contribute. Useful as a last-resort fallback
    when SofaScore / TheStatsAPI / TheSportsDB are all silent for the
    match's corners metrics.
    """
    if raw is None:
        return envelope_unavailable(source=SOURCE_PARTIAL, reason=RC_RAW_EMPTY)
    if not isinstance(raw, dict):
        return envelope_unavailable(source=SOURCE_PARTIAL, reason=RC_RAW_NOT_DICT)

    min_sample = int(raw.get("min_sample", min_sample) or _DEFAULT_MIN_SAMPLE)
    env = new_envelope(source=SOURCE_PARTIAL, available=True)
    env["sources"]["raw_keys"] = ["home", "away"]

    extra_codes: list[str] = []
    fields_written = 0

    for side in ("home", "away"):
        doc = _side_doc(raw, side)
        partial, rc = _is_partial_side(doc, min_sample=min_sample)
        # Include this side only when partial AND we have at least one row.
        matches = _matches_of(doc)
        if not partial or not matches:
            env["field_provenance"][f"{side}._side_skipped"] = {
                "source":       SOURCE_PARTIAL,
                "sample_size":  len(matches),
                "reason_codes": [rc] if partial else ["SIDE_NOT_PARTIAL"],
            }
            continue
        # We allow filling L5 metrics regardless of size, but mark them as
        # PARTIAL via reason codes attached on the envelope.
        l15_is_partial = True  # always partial in this adapter by definition
        fields_written += _fill_side_corners(
            env,
            side=side,
            matches=matches,
            sample_size=len(matches),
            l5=l5,
            l15=l15,
            is_l15_partial=l15_is_partial,
        )
        extra_codes.append(rc)
        extra_codes.append(RC_MAPPING_PARTIAL)
        if len(matches) < int(min_sample):
            extra_codes.append(RC_SAMPLE_TOO_SMALL)

    if fields_written == 0:
        env["available"] = False
        extra_codes.append(RC_NO_USABLE_FIELDS)

    return finalize_envelope(env, extra_codes=extra_codes)


__all__ = [
    "SOURCE_OFFLINE",
    "SOURCE_PARTIAL",
    "RC_SEED_PARTIAL_FROM_PROMOTION",
    "RC_SEED_PARTIAL_LOW_SAMPLE",
    "RC_SEED_FULL_SAMPLE",
    "RC_SEED_NO_MATCHES",
    "RC_SEED_DOC_UNAVAILABLE",
    "adapt_offline_seed_corners_to_f74",
    "adapt_seed_partial_corners_to_f74",
]
