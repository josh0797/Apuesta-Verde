"""Sprint-F99.4 · Recent Form Consolidator (puro).

Module pure: NO IO, NO db, NO requests. Consumes already-hydrated lists
of recent fixtures from up to four sources and returns a canonical
consolidated history per team.

Binding del usuario (F99.4):

  1. **Una sola fuente canónica**: este módulo NO crea una fuente
     paralela; consolida lo que las fuentes existentes ya producen.
  2. **Hibrido por campo**: cuando un mismo partido aparece en varias
     fuentes, los campos se fusionan según prioridad por campo —
     un proveedor preferido no bloquea completar campos ausentes
     desde el siguiente proveedor.
  3. **Prioridad base** (cuando un campo está en varias fuentes):
       seed → sofascore → thestatsapi → thesportsdb
  4. **Deduplicación**: por identidad canónica
       canonical_match_id → cross-source resolved id → composite key
       (kickoff_bucket, opponent_norm, venue, competition_norm).
  5. **Ventanas L5/L15**:
       * ``l5_is_partial = sample_size < 5``
       * ``l15_is_partial = sample_size < 15``
       * ``l5_insufficient`` cuando no hay datos suficientes para la
         ventana L5 (sample_size < 1).
       * ``l15_insufficient`` cuando sample_size < 5 (no se presenta
         L15 con menos de 5 partidos — ver binding).
  6. **No inventar valores faltantes**: si un campo no aparece en
     ninguna fuente, queda como ``None`` (no se imputa media, mediana, etc.)
  7. **No mezclar selecciones y clubes** ni senior con youth/women:
     esto se garantiza upstream (en el hydrator vía resolver F98); el
     consolidator simplemente respeta la identidad que recibió.
  8. **Splits oficial vs amistoso**: cada record consolidado conserva
     ``competition_kind`` (``"official" | "friendly" | "unknown"``) para
     que el builder calcule splits sin perder información.

Firma pública::

    consolidate_recent_form(
        sources: dict,                     # {"seed": [...], "sofascore": [...], ...}
        *,
        team_norm: str,
        canonical_match_ids: dict | None = None,  # optional precomputed F98 IDs
    ) -> dict
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# Prioridad base de fuentes (más alta primero).
SOURCE_PRIORITY = ("seed", "sofascore", "thestatsapi", "thesportsdb")

# Field-level priority overrides — when set, the field follows a custom
# ranking instead of the base. Useful for corners (seed wins) vs xG
# (sofascore wins). All entries default to ``SOURCE_PRIORITY``.
_FIELD_PRIORITY_OVERRIDES: dict[str, tuple[str, ...]] = {
    # xG / shots / possession family — SofaScore is the most accurate
    # primary by binding F99-P2; we still allow seed to win when present
    # for fixture-level metrics (goals/result).
    "xg_for":            ("sofascore", "thestatsapi", "seed", "thesportsdb"),
    "xg_against":        ("sofascore", "thestatsapi", "seed", "thesportsdb"),
    "shots":             ("sofascore", "thestatsapi", "thesportsdb", "seed"),
    "shots_on_target":   ("sofascore", "thestatsapi", "thesportsdb", "seed"),
    "possession":        ("sofascore", "thestatsapi", "thesportsdb", "seed"),
    # Corners family — seed wins per F99-P2 / F99.1 binding when present.
    "corners_for":       ("seed", "sofascore", "thestatsapi", "thesportsdb"),
    "corners_against":   ("seed", "sofascore", "thestatsapi", "thesportsdb"),
}

# Canonical record schema fields.
_RECORD_FIELDS = (
    "kickoff_utc",          # ISO string normalised to UTC
    "competition_name",     # string
    "competition_kind",     # "official" | "friendly" | "unknown"
    "venue",                # "home" | "away" | "neutral" | None
    "opponent_name",        # string
    "opponent_norm",        # normalised opponent name
    "goals_for",            # int
    "goals_against",        # int
    "result",               # "W" | "D" | "L" | None
    "xg_for",               # float
    "xg_against",           # float
    "shots",                # int
    "shots_on_target",      # int
    "possession",           # float (0..100)
    "corners_for",          # int
    "corners_against",      # int
)

_KICKOFF_BUCKET_SECONDS = 60 * 30  # ±30 min tolerance for matching kickoffs


# ─────────────────────────────────────────────────────────────────────
# Helpers — normalization
# ─────────────────────────────────────────────────────────────────────
def _norm_team(name: Any) -> str:
    if name is None:
        return ""
    import unicodedata as _u
    s = "".join(c for c in _u.normalize("NFKD", str(name)) if not _u.combining(c))
    return " ".join(s.lower().strip().split())


def _norm_kickoff(value: Any) -> Optional[str]:
    """Coerce a kickoff value to an ISO-8601 UTC string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            return None
    s = str(value).strip()
    if not s:
        return None
    # Try common ISO variants.
    for parse in (
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
        lambda v: datetime.strptime(v, "%Y-%m-%d"),
    ):
        try:
            dt = parse(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            continue
    return None


def _kickoff_bucket(iso: Optional[str]) -> Optional[str]:
    """Compute a date-only bucket (YYYY-MM-DD UTC) used for dedupe.

    Using calendar date is robust to per-source kickoff drift (different
    feeds publish slightly different timestamps for the same match). The
    composite key always includes ``opponent_norm``, ``venue`` and
    ``competition_norm`` so the date alone is not the only discriminator.
    """
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _classify_competition(name: Optional[str]) -> str:
    """Best-effort split: ``friendly`` vs ``official`` vs ``unknown``."""
    if not name:
        return "unknown"
    low = str(name).lower()
    if any(kw in low for kw in ("friendly", "amistoso", "friendlies",
                                  "club friendly", "international friendly")):
        return "friendly"
    # Official competitions we recognise — keep the list conservative
    # since the consolidator can also accept callers' explicit flag.
    OFFICIAL_KWS = (
        "world cup", "qualif", "champions league", "europa league",
        "conference league", "nations league", "premier league", "la liga",
        "serie a", "bundesliga", "ligue 1", "primeira", "eredivisie",
        "copa libertadores", "copa sudamericana", "copa america",
        "afcon", "asian cup", "concacaf", "gold cup",
        "championship", "cup", "league", "liga", "serie",
    )
    if any(kw in low for kw in OFFICIAL_KWS):
        return "official"
    return "unknown"


def _coerce_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _result_from_goals(gf: Optional[int], ga: Optional[int]) -> Optional[str]:
    if gf is None or ga is None:
        return None
    if gf > ga:
        return "W"
    if gf < ga:
        return "L"
    return "D"


# ─────────────────────────────────────────────────────────────────────
# Input normalization — each source has a slightly different shape.
# ─────────────────────────────────────────────────────────────────────
def _normalize_source_row(source: str, row: Any) -> Optional[dict]:
    """Normalise a single row from any source into a uniform shape::

        {
          "source":            "seed" | "sofascore" | "thestatsapi" | "thesportsdb",
          "source_id":         str | None,
          "kickoff_utc":       str | None,
          "competition_name":  str | None,
          "competition_kind":  "official" | "friendly" | "unknown",
          "venue":             "home" | "away" | None,
          "opponent_name":     str | None,
          "opponent_norm":     str,
          "goals_for":         int | None,
          "goals_against":     int | None,
          "xg_for":            float | None,
          "xg_against":        float | None,
          "shots":             int | None,
          "shots_on_target":   int | None,
          "possession":        float | None,
          "corners_for":       int | None,
          "corners_against":   int | None,
        }

    Returns ``None`` when the row cannot be salvaged.
    """
    if not isinstance(row, dict):
        return None

    def _g(*keys, default=None):
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return default

    kickoff = _norm_kickoff(_g("kickoff_utc", "date", "datetime",
                                 "startTimestamp", "kickoff"))
    opponent = _g("opponent_name", "opponent", "opp", "rival")
    venue = _g("venue")
    if venue not in ("home", "away", "neutral"):
        venue = None

    comp_name = _g("competition_name", "league", "league_name", "tournament")
    comp_kind = _g("competition_kind")
    if comp_kind not in ("official", "friendly", "unknown"):
        comp_kind = _classify_competition(comp_name)

    gf = _coerce_int(_g("goals_for", "team_scored", "scored", "home_goals_for"))
    ga = _coerce_int(_g("goals_against", "team_conceded", "conceded"))

    out = {
        "source":           source,
        "source_id":        str(_g("source_id", "fixture_id", "event_id", "id")) or None,
        "kickoff_utc":      kickoff,
        "competition_name": str(comp_name) if comp_name else None,
        "competition_kind": comp_kind,
        "venue":            venue,
        "opponent_name":    str(opponent) if opponent else None,
        "opponent_norm":    _norm_team(opponent),
        "goals_for":        gf,
        "goals_against":    ga,
        "result":           _result_from_goals(gf, ga),
        "xg_for":           _coerce_float(_g("xg_for", "xg", "xg_team")),
        "xg_against":       _coerce_float(_g("xg_against", "xga", "xg_opponent")),
        "shots":            _coerce_int(_g("shots", "shots_total")),
        "shots_on_target":  _coerce_int(_g("shots_on_target", "shots_on_goal",
                                              "sot")),
        "possession":       _coerce_float(_g("possession", "possession_avg",
                                                "ball_possession")),
        "corners_for":      _coerce_int(_g("corners_for", "corners")),
        "corners_against":  _coerce_int(_g("corners_against", "corners_opp")),
    }
    # Reject rows without ANY signal (no kickoff and no goals).
    if not out["kickoff_utc"] and out["goals_for"] is None and out["goals_against"] is None:
        return None
    return out


# ─────────────────────────────────────────────────────────────────────
# Identity / dedupe
# ─────────────────────────────────────────────────────────────────────
def _composite_key(rec: dict) -> tuple:
    """Composite identity key — fallback when no canonical id is available."""
    return (
        _kickoff_bucket(rec.get("kickoff_utc")),
        rec.get("opponent_norm") or "",
        rec.get("venue") or "",
        _norm_team(rec.get("competition_name")),
    )


def _canonical_key(rec: dict, canonical_match_ids: Optional[dict]) -> Any:
    """Return the canonical id when available; otherwise the composite key."""
    if canonical_match_ids:
        for src in SOURCE_PRIORITY:
            sid = rec.get("source_id")
            if sid:
                key = f"{src}:{sid}"
                cid = canonical_match_ids.get(key)
                if cid:
                    return ("canonical", cid)
    return ("composite", _composite_key(rec))


# ─────────────────────────────────────────────────────────────────────
# Field merge — hybrid by field
# ─────────────────────────────────────────────────────────────────────
def _field_priority(field: str) -> tuple[str, ...]:
    return _FIELD_PRIORITY_OVERRIDES.get(field, SOURCE_PRIORITY)


def _merge_records(records: list[dict]) -> dict:
    """Combine N normalised records of the SAME match into one canonical row.

    For each field we pick the value from the highest-ranked source that
    has a non-null value. The record metadata block records the choice
    so downstream consumers can audit provenance.
    """
    # Index by source.
    by_src: dict[str, dict] = {r["source"]: r for r in records}
    out: dict[str, Any] = {}
    field_provenance: dict[str, dict] = {}

    for field in _RECORD_FIELDS:
        ranking = _field_priority(field)
        picked_source: Optional[str] = None
        attempts: list[str] = []
        for src in ranking:
            if src not in by_src:
                continue
            attempts.append(src)
            v = by_src[src].get(field)
            if v is None:
                continue
            out[field] = v
            picked_source = src
            break
        if picked_source is None:
            out[field] = None
        field_provenance[field] = {
            "selected_source":  picked_source,
            "attempts":         attempts,
            "fallback_used":    bool(picked_source and picked_source != ranking[0]),
        }

    # Identity metadata.
    out["source_ids"] = {
        src: rec.get("source_id") for src, rec in by_src.items()
        if rec.get("source_id")
    }
    out["field_provenance"]   = field_provenance
    out["sources_contributed"] = sorted(by_src.keys())
    return out


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def consolidate_recent_form(
    sources: Any,
    *,
    team_norm: str = "",
    canonical_match_ids: Optional[dict] = None,
) -> dict:
    """Build the consolidated recent-form history for a single team.

    Parameters
    ----------
    sources:
        Mapping ``{"seed": [...], "sofascore": [...], "thestatsapi": [...],
        "thesportsdb": [...]}``. Any missing key is treated as empty.
        Lists are NOT mutated.
    team_norm:
        Normalised team name (used for telemetry; ``opponent_norm`` is
        computed per-row regardless).
    canonical_match_ids:
        Optional precomputed F98 cross-source id map. When provided the
        consolidator prefers it over the composite-key dedupe.

    Returns
    -------
    dict
        Canonical shape (see module docstring).
    """
    if not isinstance(sources, dict):
        sources = {}

    # 1) Normalise rows per source.
    normalised: list[dict] = []
    counts_per_source: dict[str, int] = {}
    for src in SOURCE_PRIORITY:
        rows = sources.get(src) or []
        if not isinstance(rows, list):
            continue
        kept = 0
        for r in rows:
            rec = _normalize_source_row(src, r)
            if rec is not None:
                normalised.append(rec)
                kept += 1
        counts_per_source[src] = kept

    # 2) Group by canonical identity key.
    groups: dict[Any, list[dict]] = {}
    for rec in normalised:
        key = _canonical_key(rec, canonical_match_ids)
        groups.setdefault(key, []).append(rec)

    # 3) Merge each group into a single canonical row.
    consolidated: list[dict] = [_merge_records(grp) for grp in groups.values()]

    # 4) Sort by kickoff desc (None pushed to the end).
    def _sort_key(r):
        ko = r.get("kickoff_utc") or ""
        return ko

    consolidated.sort(key=_sort_key, reverse=True)

    # 5) Build windows.
    l5  = consolidated[:5]
    l15 = consolidated[:15]
    sample = len(consolidated)
    partial_flags = {
        "l5_is_partial":    sample < 5,
        "l15_is_partial":   sample < 15,
        # Insufficient = fewer than the minimum required for the window
        # (binding F99.4 d): less than 1 makes L5 unusable; less than 5
        # is too thin to claim L15.
        "l5_insufficient":  sample < 1,
        "l15_insufficient": sample < 5,
    }

    # 6) Telemetry summary (lightweight; no raw payload).
    summary = {
        "team_norm":           team_norm,
        "sample_size_total":   sample,
        "sample_size_l5":      len(l5),
        "sample_size_l15":     len(l15),
        "counts_per_source":   counts_per_source,
        "groups_before_merge": len(groups),
        "consolidator_version": "F99.4-1",
    }
    return {
        "recent":         consolidated,
        "windows":        {"l5": l5, "l15": l15},
        "partial_flags":  partial_flags,
        "summary":        summary,
    }


__all__ = [
    "SOURCE_PRIORITY",
    "consolidate_recent_form",
]
