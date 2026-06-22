"""Sprint-F99.4 · Tests del Recent Form Extender (consolidator + adapter + hydrator).

Guardas binding del usuario:

  1. El extender NO crea otra fuente canónica — solo consolida.
  2. Dedupe por identidad canónica (composite key cuando no hay F98 IDs).
  3. Híbrido por campo: prioridad por campo, no all-or-nothing.
  4. L15 partial activa cuando sample < 15; L15 insufficient cuando < 5.
  5. Splits oficial/amistoso conservados.
  6. Consolidator es PURO (sin IO, sin db).
  7. No mezclar selecciones/clubes ni senior/youth (heredado de F98).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.adapters.recent_form_consolidated_adapter import (
    SOURCE,
    RC_F99_4_CONSOLIDATED,
    RC_F99_4_L15_PARTIAL,
    RC_F99_4_OFFICIAL_FRIENDLY,
    adapt_recent_form_to_f74,
)
from services.football_recent_form_consolidator import (
    SOURCE_PRIORITY,
    consolidate_recent_form,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Consolidator es PURO — no toca IO
# ─────────────────────────────────────────────────────────────────────
def test_consolidator_is_pure_no_io(monkeypatch):
    import socket
    real = socket.socket
    monkeypatch.setattr(socket, "socket",
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError("consolidator hizo IO")))
    try:
        out = consolidate_recent_form({}, team_norm="x")
        assert out["recent"] == []
        assert out["windows"]["l5"] == []
    finally:
        monkeypatch.setattr(socket, "socket", real)


def test_consolidator_does_not_mutate_inputs():
    sources = {
        "seed": [{"date": "2024-05-01", "opponent_name": "X",
                  "goals_for": 2, "goals_against": 1, "venue": "home",
                  "competition_name": "Premier League"}],
        "sofascore": [{"date": "2024-05-01", "opponent_name": "X",
                        "goals_for": 2, "goals_against": 1,
                        "xg_for": 1.8, "venue": "home",
                        "competition_name": "Premier League"}],
    }
    snapshot = {k: list(v) for k, v in sources.items()}
    _ = consolidate_recent_form(sources, team_norm="t")
    assert sources == snapshot


# ─────────────────────────────────────────────────────────────────────
# 2. Dedupe por composite key (mismo partido en varias fuentes)
# ─────────────────────────────────────────────────────────────────────
def test_dedupes_same_match_across_sources_via_composite_key():
    sources = {
        "seed":     [{"date": "2024-05-01T15:00:00+00:00",
                       "opponent_name": "Liverpool", "venue": "home",
                       "competition_name": "Premier League",
                       "goals_for": 2, "goals_against": 1}],
        "sofascore":[{"date": "2024-05-01T15:00:00+00:00",
                       "opponent_name": "Liverpool", "venue": "home",
                       "competition_name": "Premier League",
                       "goals_for": 2, "goals_against": 1,
                       "xg_for": 1.85, "corners_for": 6}],
        "thestatsapi":[{"date": "2024-05-01T15:30:00+00:00",  # within 30-min bucket
                         "opponent_name": "Liverpool", "venue": "home",
                         "competition_name": "Premier League",
                         "goals_for": 2, "goals_against": 1,
                         "shots": 14, "shots_on_target": 6}],
    }
    out = consolidate_recent_form(sources, team_norm="arsenal")
    assert len(out["recent"]) == 1, (
        f"Expected dedupe to 1 row, got {len(out['recent'])}: {out['recent']}"
    )
    row = out["recent"][0]
    # Hybrid by field: xG from sofascore, shots from thestatsapi, goals from seed.
    assert row["xg_for"]          == 1.85
    assert row["shots"]           == 14
    assert row["shots_on_target"] == 6
    assert row["goals_for"]       == 2
    # Source IDs preserved.
    assert set(row["sources_contributed"]) == {"seed", "sofascore", "thestatsapi"}


# ─────────────────────────────────────────────────────────────────────
# 3. Híbrido por campo — prioridad por campo, no all-or-nothing
# ─────────────────────────────────────────────────────────────────────
def test_field_priority_overrides_for_xg_and_corners():
    """xG prefers SofaScore (per F99-P2). Corners prefer seed."""
    sources = {
        "seed":      [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "goals_for": 2, "goals_against": 1,
                        "corners_for": 7, "corners_against": 3,
                        "xg_for": 9.99}],   # adversarial seed xG
        "sofascore": [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "goals_for": 2, "goals_against": 1,
                        "corners_for": 5, "corners_against": 4,
                        "xg_for": 1.85}],
    }
    out = consolidate_recent_form(sources, team_norm="t")
    row = out["recent"][0]
    # xG follows the override → sofascore.
    assert row["xg_for"]      == 1.85
    assert row["field_provenance"]["xg_for"]["selected_source"] == "sofascore"
    # Corners follows the override → seed.
    assert row["corners_for"] == 7
    assert row["field_provenance"]["corners_for"]["selected_source"] == "seed"


def test_field_priority_base_ranking_for_goals():
    """Goals are NOT overridden — base ranking applies: seed first."""
    sources = {
        "seed":      [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "goals_for": 2}],
        "sofascore": [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "goals_for": 9}],   # adversarial
    }
    out = consolidate_recent_form(sources, team_norm="t")
    row = out["recent"][0]
    assert row["goals_for"] == 2
    assert row["field_provenance"]["goals_for"]["selected_source"] == "seed"


def test_field_fallback_when_primary_source_lacks_field():
    """seed has goals but no xG; sofascore has xG → consolidated has both."""
    sources = {
        "seed":      [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "goals_for": 2, "goals_against": 1}],
        "sofascore": [{"date": "2024-05-01T15:00:00+00:00",
                        "opponent_name": "L", "venue": "home",
                        "competition_name": "PL",
                        "xg_for": 1.85, "xg_against": 0.95}],
    }
    out = consolidate_recent_form(sources, team_norm="t")
    row = out["recent"][0]
    assert row["goals_for"]  == 2
    assert row["xg_for"]     == 1.85
    assert row["xg_against"] == 0.95


# ─────────────────────────────────────────────────────────────────────
# 4. L5/L15 windows + partial flags
# ─────────────────────────────────────────────────────────────────────
def _row(date, opp, gf=1, ga=1, comp="Premier League"):
    return {
        "date": f"2024-{date}T18:00:00+00:00",
        "opponent_name": opp, "venue": "home",
        "competition_name": comp,
        "goals_for": gf, "goals_against": ga,
    }


def test_l15_partial_when_sample_below_15():
    sources = {"seed": [_row(f"05-{i:02d}", f"O{i}") for i in range(1, 11)]}
    out = consolidate_recent_form(sources, team_norm="t")
    assert out["partial_flags"]["l15_is_partial"] is True
    assert out["partial_flags"]["l5_is_partial"]  is False
    assert out["partial_flags"]["l15_insufficient"] is False


def test_l15_insufficient_when_sample_below_5():
    sources = {"seed": [_row(f"05-{i:02d}", f"O{i}") for i in range(1, 4)]}
    out = consolidate_recent_form(sources, team_norm="t")
    assert out["partial_flags"]["l5_is_partial"]    is True
    assert out["partial_flags"]["l15_insufficient"] is True


def test_l5_insufficient_when_sample_is_zero():
    out = consolidate_recent_form({}, team_norm="t")
    assert out["partial_flags"]["l5_insufficient"]  is True
    assert out["partial_flags"]["l15_insufficient"] is True


def test_l15_complete_when_sample_15_or_more():
    sources = {"seed": [_row(f"01-{i:02d}", f"O{i}")
                          if i <= 28 else _row(f"02-{i-28:02d}", f"O{i}")
                          for i in range(1, 17)]}
    out = consolidate_recent_form(sources, team_norm="t")
    assert out["partial_flags"]["l15_is_partial"] is False
    assert len(out["windows"]["l15"]) == 15


# ─────────────────────────────────────────────────────────────────────
# 5. Sort por kickoff desc
# ─────────────────────────────────────────────────────────────────────
def test_records_sorted_by_kickoff_desc():
    sources = {"seed": [
        _row("01-15", "A"),
        _row("03-20", "B"),
        _row("02-10", "C"),
    ]}
    out = consolidate_recent_form(sources, team_norm="t")
    dates = [r["kickoff_utc"] for r in out["recent"]]
    assert dates == sorted(dates, reverse=True)


# ─────────────────────────────────────────────────────────────────────
# 6. Splits oficial vs amistoso conservados
# ─────────────────────────────────────────────────────────────────────
def test_official_friendly_split_preserved():
    sources = {"seed": [
        _row("05-01", "A", comp="Friendlies International"),
        _row("05-08", "B", comp="Premier League"),
        _row("05-15", "C", comp="Premier League"),
    ]}
    out = consolidate_recent_form(sources, team_norm="t")
    kinds = [r["competition_kind"] for r in out["recent"]]
    assert "friendly" in kinds
    assert "official" in kinds


# ─────────────────────────────────────────────────────────────────────
# 7. Adapter — output puro, sin filtraciones
# ─────────────────────────────────────────────────────────────────────
def test_adapter_produces_canonical_envelope():
    sources = {"seed": [_row(f"05-{i:02d}", f"O{i}", gf=2, ga=1)
                          for i in range(1, 6)]}
    home_consolidated = consolidate_recent_form(sources, team_norm="home")
    away_consolidated = consolidate_recent_form(sources, team_norm="away")
    raw = {"home": home_consolidated, "away": away_consolidated}
    env = adapt_recent_form_to_f74(raw, home_team="H", away_team="A")
    assert env["source"]    == SOURCE
    assert env["available"] is True
    assert RC_F99_4_CONSOLIDATED in env["reason_codes"]
    # Each side has goals_scored_l5 + recent_fixtures projection.
    for side in ("home", "away"):
        assert env[side]["goals_scored_l5"] == 2
        assert env[side]["goals_conceded_l5"] == 1
        assert env[side]["recent_fixtures"] and len(env[side]["recent_fixtures"]) == 5


def test_adapter_marks_l15_partial_when_sample_below_15():
    sources = {"seed": [_row(f"05-{i:02d}", f"O{i}") for i in range(1, 8)]}
    cons = consolidate_recent_form(sources, team_norm="t")
    raw = {"home": cons, "away": cons}
    env = adapt_recent_form_to_f74(raw)
    assert RC_F99_4_L15_PARTIAL in env["reason_codes"]
    assert env["sample_sizes"]["home.l15_is_partial"] is True


def test_adapter_emits_official_friendly_split_code_when_present():
    sources = {"seed": [
        _row(f"05-{i:02d}", f"O{i}",
              comp="Premier League" if i % 2 else "Club Friendly")
        for i in range(1, 8)
    ]}
    cons = consolidate_recent_form(sources, team_norm="t")
    raw = {"home": cons, "away": cons}
    env = adapt_recent_form_to_f74(raw)
    assert RC_F99_4_OFFICIAL_FRIENDLY in env["reason_codes"]


def test_adapter_unavailable_when_raw_empty():
    env = adapt_recent_form_to_f74(None)
    assert env["source"]    == SOURCE
    assert env["available"] is False
    env2 = adapt_recent_form_to_f74("not a dict")
    assert env2["available"] is False


# ─────────────────────────────────────────────────────────────────────
# 8. Hydrator F99.4 — feature flag + fail-soft
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hydrator_skips_when_flag_off(monkeypatch):
    from services import football_recent_form_hydrator as frh
    monkeypatch.delenv(frh.FLAG_ENV_VAR, raising=False)
    match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    attached = await frh.hydrate_match_recent_form(match, db=object())
    assert attached is False
    trace = match[frh.TRACE_KEY][frh.SOURCE_KEY]
    assert trace["status"] == "SKIPPED"
    assert trace["reason"] == "feature_flag_off"


@pytest.mark.asyncio
async def test_hydrator_extracts_sofascore_rows_and_consolidates(monkeypatch):
    """Si el match ya trae ``_sofascore_raw``, el hydrator debe extraerlo
    y consolidarlo SIN llamar IO adicional."""
    from services import football_recent_form_hydrator as frh
    monkeypatch.setenv(frh.FLAG_ENV_VAR, "true")

    sofa_wrapper = {
        "event_id": 1,
        "home_form": [
            {"date": "2024-05-01", "home_team": "Arsenal", "away_team": "X",
             "home_score": 2, "away_score": 1,
             "home_stats": {"xg": 1.85, "corners": 6, "shots": 14}},
            {"date": "2024-04-20", "home_team": "Y", "away_team": "Arsenal",
             "home_score": 0, "away_score": 0,
             "away_stats": {"xg": 1.10, "corners": 3, "shots": 9}},
        ],
        "away_form": [
            {"date": "2024-05-02", "home_team": "Liverpool", "away_team": "Z",
             "home_score": 3, "away_score": 0,
             "home_stats": {"xg": 2.45, "corners": 8}},
        ],
    }
    match = {
        "home_team":      {"name": "Arsenal"},
        "away_team":      {"name": "Liverpool"},
        "_sofascore_raw": sofa_wrapper,
    }

    class _FakeDB:
        def __getitem__(self, _):
            class _Coll:
                async def find_one(self, *_a, **_k):
                    return None
            return _Coll()

    attached = await frh.hydrate_match_recent_form(match, _FakeDB())
    assert attached is True
    raw = match[frh.RAW_KEY]
    assert "home" in raw and "away" in raw
    # Home consolidated has 2 records.
    assert len(raw["home"]["recent"]) == 2
    # Sample sizes propagated to trace.
    trace = match[frh.TRACE_KEY][frh.SOURCE_KEY]
    assert trace["sample_sizes"]["home"] == 2
    assert trace["sample_sizes"]["away"] == 1


@pytest.mark.asyncio
async def test_hydrator_failsoft_when_seed_lookup_crashes(monkeypatch):
    from services import football_recent_form_hydrator as frh
    monkeypatch.setenv(frh.FLAG_ENV_VAR, "true")

    class _DB:
        def __getitem__(self, _):
            class _Coll:
                async def find_one(self, *_a, **_k):
                    raise RuntimeError("mongo down")
            return _Coll()

    match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    attached = await frh.hydrate_match_recent_form(match, _DB())
    assert attached is False
    trace = match[frh.TRACE_KEY][frh.SOURCE_KEY]
    assert trace["status"] == "NO_DATA"


# ─────────────────────────────────────────────────────────────────────
# 9. F99.4 NO crea otra fuente canónica — el builder sigue siendo el único
#    orquestador y F74 sigue siendo el canónico.
# ─────────────────────────────────────────────────────────────────────
def test_consolidator_does_not_produce_canonical_f74_directly():
    """El consolidator devuelve un payload de "recent form" — NO un envelope
    F74 ni un ``football_data_enrichment``. El builder es el único que
    proyecta F74."""
    out = consolidate_recent_form({"seed": [_row("05-01", "X")]}, team_norm="t")
    # Output debe NO contener llaves del contrato F74 ni del enrichment.
    forbidden_keys = {"football_data_enrichment", "data_quality",
                       "field_provenance", "schema_migration"}
    assert forbidden_keys.isdisjoint(out.keys())
    # Debe contener llaves del contrato F99.4.
    assert set(out.keys()) == {"recent", "windows", "partial_flags", "summary"}


def test_priority_constants_match_user_spec():
    """``SOURCE_PRIORITY`` debe respetar binding:
    seed → sofascore → thestatsapi → thesportsdb"""
    assert SOURCE_PRIORITY == ("seed", "sofascore", "thestatsapi", "thesportsdb")


# ─────────────────────────────────────────────────────────────────────
# 10. Integración cascade — recent_form_consolidated participa en goals_l5
# ─────────────────────────────────────────────────────────────────────
def test_cascade_picks_recent_form_consolidated_for_goals_when_present():
    from services.football_source_cascade import (
        DEFAULT_RANKINGS, cascade_merge_envelopes,
    )
    # F99.4 ranking: recent_form_consolidated is the primary for goals_l5.
    assert DEFAULT_RANKINGS["goals_scored_l5"][0] == "recent_form_consolidated"

    sources = {"seed": [_row(f"05-{i:02d}", f"O{i}", gf=3, ga=1)
                          for i in range(1, 6)]}
    cons = consolidate_recent_form(sources, team_norm="t")
    raw = {"home": cons, "away": cons}
    env_rf = adapt_recent_form_to_f74(raw)

    # Simulate a sofascore envelope with a different value.
    from services.adapters._envelope import new_envelope, set_field, finalize_envelope
    env_sofa = new_envelope(source="sofascore", available=True)
    set_field(env_sofa, "home.goals_scored_l5", 1.0, sample_size=5)
    finalize_envelope(env_sofa)

    merged = cascade_merge_envelopes([env_sofa, env_rf])
    # recent_form_consolidated wins for goals_scored_l5.
    assert merged["home"]["goals_scored_l5"] == 3
    assert merged["field_provenance"]["home.goals_scored_l5"]["source"] == "recent_form_consolidated"
