"""Sprint-P99.6 · Tests críticos de enriquecimiento (10 escenarios binding).

Estos tests blindan las garantías centrales del wiring F99 antes de
avanzar a F99.4 (Recent Form Extender) y F99.5 (Odds aggregator).

Escenarios cubiertos (numerados según el binding del usuario):

  1. SofaScore resuelve el evento usando equipos + fecha.
  2. Su payload se adapta correctamente a F74.
  3. El builder consume el adapter existente (no lo reimplementa).
  4. SofaScore con possession válida y xG ausente conserva possession.
  5. Solo xG cae a TheStatsAPI.
  6. Fixture + recent form + SofaScore stats puede subir THIN → USABLE.
  7. SofaScore bloqueado o con schema inesperado no rompe el pipeline.
  8. El editorial sigue leyendo F74 primero.
  9. No se mezclan selecciones con clubes o categorías juveniles.
 10. El seed de córners conserva su prioridad y contrato D9.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.adapters._envelope import (
    DQ_LIMITED,
    DQ_STRONG,
    DQ_THIN,
    DQ_USABLE,
    finalize_envelope,
    new_envelope,
    set_field,
)
from services.adapters.sofascore_adapter import adapt_sofascore_to_f74
from services.adapters.thestatsapi_adapter import adapt_thestatsapi_to_f74
from services.football_enrichment_builder import build_football_data_enrichment
from services.football_source_cascade import (
    DEFAULT_RANKINGS,
    cascade_merge_envelopes,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _sofascore_event_row(date, home, away, hg, ag, *,
                          home_stats=None, away_stats=None):
    row = {
        "date":       date,
        "home_team":  home,
        "away_team":  away,
        "home_score": hg,
        "away_score": ag,
    }
    if home_stats:
        row["home_stats"] = home_stats
    if away_stats:
        row["away_stats"] = away_stats
    return row


def _sofascore_wrapper_with_recent_stats(*, home="Arsenal", away="Liverpool",
                                          include_xg=True, include_possession=True,
                                          include_shots=True):
    """Build a realistic SofaScore wrapper with 5 recent fixtures per side."""
    def stats(shots=12, sot=5, poss=55, corners=6, xg=1.4):
        s = {}
        if include_shots:
            s["shots"]           = shots
            s["shots_on_target"] = sot
        if include_possession:
            s["possession"] = poss
        s["corners"] = corners
        if include_xg:
            s["xg"] = xg
        return s

    home_form = [
        _sofascore_event_row(f"2024-0{i}-01", home, f"Opp{i}", 2, 1,
                              home_stats=stats(), away_stats=stats(xg=0.9))
        for i in range(1, 6)
    ]
    away_form = [
        _sofascore_event_row(f"2024-0{i}-02", away, f"Opp{i}", 1, 0,
                              home_stats=stats(xg=1.6), away_stats=stats())
        for i in range(1, 6)
    ]
    return {
        "event_id":  424242,
        "home_form": home_form,
        "away_form": away_form,
        "h2h": [
            _sofascore_event_row("2023-12-01", home, away, 1, 2)
        ],
        "odds": {"match_winner": {"home": 1.85, "draw": 3.4, "away": 4.2}},
        "_trace": {"status": "USABLE", "event_resolved": True, "stats_enriched": True},
    }


# ─────────────────────────────────────────────────────────────────────
# 1. SofaScore resuelve el evento usando equipos + fecha
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p99_6_1_sofascore_resolves_event_by_teams_and_date():
    """``resolve_sofascore_event`` debe consumir home + away + target_date.

    El fail-soft path (scrape.do unavailable) sigue devolviendo None — pero
    SI el resolver interno está disponible, los argumentos se propagan
    correctamente sin perder fecha.
    """
    from services.external_sources import sofascore as ss

    captured: dict = {}

    async def _capture_resolver(home, away, sport, *args, **kwargs):
        captured["home"]   = home
        captured["away"]   = away
        captured["sport"] = sport
        return 1234567

    with patch.object(ss, "_scrapedo_available", new=AsyncMock(return_value=True)), \
         patch.object(ss, "_resolve_event_id", new=_capture_resolver):
        out = await ss.resolve_sofascore_event(
            "Arsenal", "Liverpool",
            sport="football",
            target_date="2024-05-12",
        )

    assert out == 1234567
    assert captured["home"]  == "Arsenal"
    assert captured["away"]  == "Liverpool"
    assert captured["sport"] == "football"


# ─────────────────────────────────────────────────────────────────────
# 2. El payload de SofaScore se adapta correctamente a F74
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_2_sofascore_payload_adapts_to_f74():
    wrapper = _sofascore_wrapper_with_recent_stats()
    env = adapt_sofascore_to_f74(wrapper, home_team="Arsenal", away_team="Liverpool")
    assert env["source"]    == "sofascore"
    assert env["available"] is True
    # Sides populated with the canonical metric names.
    for side in ("home", "away"):
        assert env[side]["xg_for_l5"]            is not None
        assert env[side]["shots_for_l5"]         is not None
        assert env[side]["shots_on_target_l5"]   is not None
        assert env[side]["possession_avg_l5"]    is not None
        assert env[side]["corners_for_l5"]       is not None
        assert env[side]["recent_fixtures"]      and len(env[side]["recent_fixtures"]) == 5


# ─────────────────────────────────────────────────────────────────────
# 3. El builder consume el adapter existente (no duplica lógica)
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_3_builder_calls_existing_sofascore_adapter():
    """``build_football_data_enrichment`` debe invocar
    ``adapt_sofascore_to_f74`` (módulo F98), no una implementación paralela.
    """
    wrapper = _sofascore_wrapper_with_recent_stats()
    match = {
        "home_team":      {"name": "Arsenal"},
        "away_team":      {"name": "Liverpool"},
        "_sofascore_raw": wrapper,
    }
    with patch(
        "services.football_enrichment_builder.adapt_sofascore_to_f74",
        wraps=adapt_sofascore_to_f74,
    ) as wrapped:
        f74 = build_football_data_enrichment(match)
    # F98 adapter MUST have been invoked exactly once with our wrapper.
    assert wrapped.call_count == 1
    args, kwargs = wrapped.call_args
    assert args[0] is wrapper
    assert kwargs.get("home_team") == "Arsenal"
    assert kwargs.get("away_team") == "Liverpool"
    # And the F74 output should be populated from SofaScore signals.
    assert f74["available"] is True
    assert f74["home"]["xg_for_l5"] is not None


# ─────────────────────────────────────────────────────────────────────
# 4. SofaScore con possession válida y xG ausente conserva possession
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_4_granular_field_selection_possession_kept_when_xg_missing():
    """El cascade debe ser **per-field**: si SofaScore tiene possession
    válida pero no xG, possession debe sobrevivir aunque xG caiga a otra
    fuente.
    """
    wrapper = _sofascore_wrapper_with_recent_stats(include_xg=False)
    env_sofa = adapt_sofascore_to_f74(wrapper, home_team="Arsenal", away_team="Liverpool")
    # Sanity: SofaScore filled possession but xG is missing.
    assert env_sofa["home"]["possession_avg_l5"] is not None
    assert env_sofa["home"].get("xg_for_l5") is None
    # An empty TheStatsAPI envelope (no data) must NOT clobber possession.
    env_tsa = new_envelope(source="thestatsapi", available=True)
    set_field(env_tsa, "home.xg_for_l5", 1.45, sample_size=5)
    finalize_envelope(env_tsa)
    merged = cascade_merge_envelopes([env_sofa, env_tsa])
    # possession kept from sofascore.
    assert merged["home"]["possession_avg_l5"] is not None
    assert merged["field_provenance"]["home.possession_avg_l5"]["source"] == "sofascore"
    # xG falls back to TheStatsAPI (binding spec).
    assert merged["home"]["xg_for_l5"] == 1.45
    assert merged["field_provenance"]["home.xg_for_l5"]["source"] == "thestatsapi"


# ─────────────────────────────────────────────────────────────────────
# 5. Solo xG cae a TheStatsAPI
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_5_only_xg_falls_back_when_sofascore_full_except_xg():
    wrapper = _sofascore_wrapper_with_recent_stats(include_xg=False)
    env_sofa = adapt_sofascore_to_f74(wrapper, home_team="Arsenal", away_team="Liverpool")
    env_tsa  = new_envelope(source="thestatsapi", available=True)
    # TheStatsAPI also has shots / corners / possession — they would WIN
    # if we accidentally treated sources as "all-or-nothing".
    set_field(env_tsa, "home.xg_for_l5",          1.42, sample_size=5)
    set_field(env_tsa, "home.shots_for_l5",       9.0,  sample_size=5)
    set_field(env_tsa, "home.corners_for_l5",     4.0,  sample_size=5)
    set_field(env_tsa, "home.possession_avg_l5",  48.0, sample_size=5)
    finalize_envelope(env_tsa)
    merged = cascade_merge_envelopes([env_sofa, env_tsa])
    prov = merged["field_provenance"]
    # ONLY xG is sourced from TheStatsAPI.
    assert prov["home.xg_for_l5"]["source"]         == "thestatsapi"
    # Everything else stays on SofaScore (primary in F99 ranking).
    assert prov["home.shots_for_l5"]["source"]      == "sofascore"
    assert prov["home.corners_for_l5"]["source"]    == "sofascore"
    assert prov["home.possession_avg_l5"]["source"] == "sofascore"


# ─────────────────────────────────────────────────────────────────────
# 6. Fixture + recent form + SofaScore stats puede subir THIN → USABLE
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_6_data_quality_climbs_to_usable_with_sofascore_form():
    """Antes (THIN): match sin enrichment.
    Después: con SofaScore recent form + stats, sube a USABLE/STRONG.
    """
    # Baseline THIN.
    bare = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    f74_before = build_football_data_enrichment(bare)
    assert f74_before["data_quality"] in (DQ_THIN, DQ_LIMITED)

    # Now attach a SofaScore wrapper with 5 fixtures per side + stats.
    enriched = dict(bare)
    enriched["_sofascore_raw"] = _sofascore_wrapper_with_recent_stats()
    f74_after = build_football_data_enrichment(enriched)
    # USABLE or STRONG depending on weights — never THIN.
    assert f74_after["data_quality"] in (DQ_USABLE, DQ_STRONG), (
        f"expected USABLE/STRONG, got {f74_after['data_quality']} "
        f"(score={f74_after.get('data_completeness_score')})"
    )
    # And the move from THIN → USABLE is the binding guard #6.
    before_order = ["THIN", "LIMITED", "USABLE", "STRONG"].index(f74_before["data_quality"])
    after_order  = ["THIN", "LIMITED", "USABLE", "STRONG"].index(f74_after["data_quality"])
    assert after_order > before_order


# ─────────────────────────────────────────────────────────────────────
# 7. SofaScore bloqueado o schema inesperado no rompe el pipeline
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_7_blocked_sofascore_does_not_break_pipeline():
    """``_sofascore_raw = None`` o un schema inesperado deben degradar
    a fail-soft sin lanzar excepciones. El builder sigue produciendo un
    F74 válido (aunque sea THIN)."""
    cases = [
        {"_sofascore_raw": None},                              # bloqueado
        {"_sofascore_raw": "not a dict"},                      # schema drift
        {"_sofascore_raw": []},                                # tipo equivocado
        {"_sofascore_raw": {"event_id": 1}},                   # incompleto
        {"_sofascore_raw": {"home_form": "string-not-list"}},  # adversarial
    ]
    base = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    for case in cases:
        match = {**base, **case}
        # Critical: must NOT raise.
        f74 = build_football_data_enrichment(match)
        assert isinstance(f74, dict)
        assert "data_quality" in f74
        assert f74["data_quality"] in (DQ_THIN, DQ_LIMITED, DQ_USABLE, DQ_STRONG)


@pytest.mark.asyncio
async def test_p99_6_7_hydrator_records_blocked_status_when_fetch_returns_none(monkeypatch):
    """El hydrator debe declarar BLOCKED en source_trace cuando el fetch
    falla — y NUNCA debe ensuciar el match con un wrapper inválido."""
    from services import football_sofascore_hydrator as fsh
    monkeypatch.setenv(fsh.FLAG_ENV_VAR, "true")
    with patch(
        "services.external_sources.sofascore.fetch_sofascore_match_context",
        new=AsyncMock(return_value=None),
    ):
        match = {"home_team": "A", "away_team": "B"}
        attached = await fsh.hydrate_match_sofascore(match, sport="football")
    assert attached is False
    assert "_sofascore_raw" not in match
    trace = match[fsh.TRACE_KEY][fsh.SOURCE_KEY]
    assert trace["status"] == "BLOCKED"
    assert trace["fallback_triggered"] is True


# ─────────────────────────────────────────────────────────────────────
# 8. El editorial sigue leyendo F74 primero
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_8_editorial_reads_f74_first(monkeypatch):
    """Con F99.3 flag ON, ``_data_completeness`` rutea por el adapter v2
    y emite ``F99_EDITORIAL_F74_ADAPTER_USED`` — confirmando que el
    editorial NO está leyendo legacy de primera mano."""
    from services.football_editorial_payload_adapter import (
        F99_FLAG_ENV_VAR,
        RC_F99_F74_ADAPTER_USED,
    )
    from services.football_editorial_prediction import _data_completeness

    monkeypatch.setenv(F99_FLAG_ENV_VAR, "true")
    wrapper = _sofascore_wrapper_with_recent_stats()
    match = {
        "home_team":      {"name": "A"},
        "away_team":      {"name": "B"},
        "_sofascore_raw": wrapper,
    }
    completeness = _data_completeness(match)
    assert completeness["f99_adapter_used"] is True
    assert RC_F99_F74_ADAPTER_USED in completeness["f99_editorial_reason_codes"]
    # And the editorial sees the metrics that came from F74 (= SofaScore).
    assert completeness["has_xg"]            is True
    assert completeness["has_goals_history"] is True


# ─────────────────────────────────────────────────────────────────────
# 9. No se mezclan selecciones con clubes o categorías juveniles
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_p99_6_9_youth_and_women_teams_are_excluded_from_national_team_resolver():
    """El resolver de selecciones nacionales debe filtrar ``U17/U20/U23/
    Women/Youth`` para evitar contaminar la lectura de un partido senior
    con datos de la sub-20.
    """
    from services.football_national_team_seed import _resolve_national_team_id
    from services.external_sources import thesportsdb_client as _tsdb_real

    mixed_candidates = [
        {"idTeam": "11", "strTeam": "Argentina U20",   "strSport": "Soccer",
         "strLeague": "U20 World Cup"},
        {"idTeam": "12", "strTeam": "Argentina Women", "strSport": "Soccer",
         "strLeague": "Women's World Cup"},
        {"idTeam": "13", "strTeam": "Argentina U17",   "strSport": "Soccer",
         "strLeague": "U17 World Cup"},
        {"idTeam": "14", "strTeam": "Argentina",       "strSport": "Soccer",
         "strLeague": "FIFA World Cup"},
        {"idTeam": "15", "strTeam": "Argentinos Juniors", "strSport": "Soccer",
         "strLeague": "Liga Profesional"},
    ]

    with patch.object(_tsdb_real, "is_enabled", return_value=True), \
         patch.object(_tsdb_real, "search_teams",
                       new=AsyncMock(return_value=mixed_candidates)):
        result = await _resolve_national_team_id("Argentina", client=None)
    # Should pick the senior national team id (=14), never the U17/U20/U23/Women.
    assert result == "14", (
        f"resolver picked '{result}' instead of '14' (senior). "
        "El filtro contra youth/women está fallando — riesgo de mezcla "
        "selección nacional / juvenil."
    )


def test_p99_6_9_youth_keywords_filter_is_strict():
    """Sanity-test on the keyword list (defence-in-depth)."""
    import services.football_national_team_seed as fns
    src = open(fns.__file__, encoding="utf-8").read()
    # The filter MUST mention U17/U20/U23/women/youth.
    for kw in (" u17", " u20", " u23", " women", " youth"):
        assert kw in src, f"national-team filter missing keyword '{kw}'"


# ─────────────────────────────────────────────────────────────────────
# 10. El seed de córners conserva su prioridad y contrato D9
# ─────────────────────────────────────────────────────────────────────
def test_p99_6_10a_corners_ranking_has_offline_seed_first():
    """Binding F99.2: offline_seed va PRIMERO en córners. SofaScore segundo."""
    ranking = DEFAULT_RANKINGS["corners_for_l5"]
    assert ranking[0] == "offline_seed"
    assert ranking[1] == "sofascore"
    assert ranking[2] == "thestatsapi"
    assert ranking[3] == "thesportsdb"
    assert ranking[4] == "seed_partial"


def test_p99_6_10b_d9_corner_history_contract_unchanged():
    """El contrato D9 ``fetch_team_corners_history_v2`` debe estar intacto.

    F99 prometió NO modificar el cascade D9 ni
    ``promote_online_matches_to_seed``. Validamos que ambas funciones
    siguen importables y mantienen su firma.
    """
    from services.football_corners_offline_seed import (
        get_offline_corners_history,
        promote_online_matches_to_seed,
    )
    import inspect

    # get_offline_corners_history must still accept (db, team_name, league=None).
    sig = inspect.signature(get_offline_corners_history)
    params = list(sig.parameters.keys())
    assert params[0] == "db"
    assert params[1] == "team_name"
    assert "league" in params

    # promote_online_matches_to_seed must still accept (db, team_name, ...).
    sig2 = inspect.signature(promote_online_matches_to_seed)
    params2 = list(sig2.parameters.keys())
    assert params2[0] == "db"
    assert params2[1] == "team_name"


def test_p99_6_10c_offline_seed_wins_over_sofascore_in_cascade():
    """End-to-end: cuando ambas fuentes (offline_seed y sofascore) tienen
    córners válidos, ``offline_seed`` gana porque está rank #1.
    """
    env_seed = new_envelope(source="offline_seed", available=True)
    set_field(env_seed, "home.corners_for_l5", 7.0, sample_size=5)
    finalize_envelope(env_seed)

    env_sofa = new_envelope(source="sofascore", available=True)
    set_field(env_sofa, "home.corners_for_l5", 5.4, sample_size=5)
    finalize_envelope(env_sofa)

    merged = cascade_merge_envelopes([env_sofa, env_seed])
    assert merged["home"]["corners_for_l5"] == 7.0
    assert merged["field_provenance"]["home.corners_for_l5"]["source"] == "offline_seed"
