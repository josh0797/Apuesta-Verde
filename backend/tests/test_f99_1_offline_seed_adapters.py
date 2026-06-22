"""Sprint-F99.1 · Tests del adapter ``offline_seed`` / ``seed_partial`` (córners) + hydrator.

Guardas binding del usuario:

  1. Adapters puros: ningún IO, ningún acceso a db, ningún proveedor externo.
  2. Misma colección: offline_seed y seed_partial leen del mismo raw payload.
  3. seed_partial se deriva de sample_size insuficiente OR underlying_source=promoted_from_online.
  4. El adapter de córners NUNCA llena xG / goles / shots / possession.
  5. Hydrator solo lee la colección de córners (no mezcla con xG seed).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.adapters.offline_seed_corners_adapter import (
    SOURCE_OFFLINE,
    SOURCE_PARTIAL,
    adapt_offline_seed_corners_to_f74,
    adapt_seed_partial_corners_to_f74,
    RC_SEED_FULL_SAMPLE,
    RC_SEED_NO_MATCHES,
    RC_SEED_PARTIAL_LOW_SAMPLE,
    RC_SEED_PARTIAL_FROM_PROMOTION,
)


def _match_row(date, opp, cf, ca, gf=None, ga=None, venue="home"):
    return {
        "date":            date,
        "opponent":        opp,
        "corners_for":     cf,
        "corners_against": ca,
        "goals_for":       gf,
        "goals_against":   ga,
        "venue":           venue,
    }


def _seed_doc(matches, *, underlying_source="historical_dataset_2021_2023"):
    return {
        "available":         True,
        "source":            "offline_seed",
        "team_name":         "Team X",
        "league":            "EPL",
        "matches":           list(matches),
        "matches_count":     len(matches),
        "from_cache":        True,
        "reason_code":       "CORNERS_OFFLINE_SEED_HIT",
        "underlying_source": underlying_source,
    }


# ─────────────────────────────────────────────────────────────────────
# 1. Pureza del adapter (sin IO)
# ─────────────────────────────────────────────────────────────────────
def test_adapter_is_pure_no_io(monkeypatch):
    """Si el adapter intenta hacer IO esto debe romper de inmediato."""
    import socket
    real_socket = socket.socket

    def _explode(*_a, **_k):
        raise AssertionError("Adapter intentó hacer IO — viola la pureza F98/F99.1")

    monkeypatch.setattr(socket, "socket", _explode)
    try:
        env = adapt_offline_seed_corners_to_f74(
            {"home": _seed_doc([_match_row("2024-05-01", "A", 5, 3) for _ in range(5)])},
            home_team="Team X", away_team="Team Y",
        )
        assert env["source"] == SOURCE_OFFLINE
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)


# ─────────────────────────────────────────────────────────────────────
# 2. offline_seed cuando ambos lados tienen sample suficiente
# ─────────────────────────────────────────────────────────────────────
def test_offline_seed_full_sample_fills_both_sides():
    matches_home = [_match_row(f"2024-0{i}-01", f"Opp{i}", 6, 4) for i in range(1, 6)]
    matches_away = [_match_row(f"2024-0{i}-01", f"Opp{i}", 5, 5) for i in range(1, 6)]
    raw = {
        "home": _seed_doc(matches_home),
        "away": _seed_doc(matches_away),
        "min_sample": 3,
    }
    env = adapt_offline_seed_corners_to_f74(raw, home_team="H", away_team="A")

    assert env["source"]    == SOURCE_OFFLINE
    assert env["available"] is True
    assert env["home"]["corners_for_l5"]     == 6
    assert env["home"]["corners_against_l5"] == 4
    assert env["away"]["corners_for_l5"]     == 5
    assert env["away"]["corners_against_l5"] == 5
    assert env["home"]["corners_total_l5"]   == 10
    assert env["sample_sizes"]["home.l5_sample_size"] == 5
    assert RC_SEED_FULL_SAMPLE in env["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 3. seed_partial cuando sample < min_sample
# ─────────────────────────────────────────────────────────────────────
def test_seed_partial_from_low_sample_size():
    raw = {
        "home": _seed_doc([_match_row("2024-05-01", "A", 4, 2)]),  # sample=1
        "away": _seed_doc([_match_row("2024-05-01", "A", 7, 1)]),  # sample=1
        "min_sample": 3,
    }
    # offline_seed NO debe rellenar nada (ambos lados son partial).
    env_off = adapt_offline_seed_corners_to_f74(raw)
    assert env_off["available"] is False
    assert env_off["home"] == {}
    assert env_off["away"] == {}

    # seed_partial SÍ debe rellenar ambos lados, con SAMPLE_TOO_SMALL.
    env_p = adapt_seed_partial_corners_to_f74(raw)
    assert env_p["source"] == SOURCE_PARTIAL
    assert env_p["available"] is True
    assert env_p["home"]["corners_for_l5"] == 4
    assert env_p["away"]["corners_for_l5"] == 7
    assert "SAMPLE_TOO_SMALL" in env_p["reason_codes"]
    assert RC_SEED_PARTIAL_LOW_SAMPLE in env_p["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 4. seed_partial cuando underlying_source == promoted_from_online
# ─────────────────────────────────────────────────────────────────────
def test_seed_partial_from_promoted_online_marker():
    matches = [_match_row(f"2024-0{i}-01", f"O{i}", 5, 4) for i in range(1, 6)]
    raw = {
        "home": _seed_doc(matches, underlying_source="promoted_from_online"),
        "away": _seed_doc(matches, underlying_source="promoted_from_online"),
        "min_sample": 3,
    }
    env_off = adapt_offline_seed_corners_to_f74(raw)
    # Ambos lados marcados como partial → offline_seed unavailable.
    assert env_off["available"] is False

    env_p = adapt_seed_partial_corners_to_f74(raw)
    assert env_p["available"] is True
    assert env_p["home"]["corners_for_l5"] == 5
    assert RC_SEED_PARTIAL_FROM_PROMOTION in env_p["reason_codes"]


# ─────────────────────────────────────────────────────────────────────
# 5. Mixto: un lado rico y otro partial → cada envelope llena su lado
# ─────────────────────────────────────────────────────────────────────
def test_mixed_sides_route_to_their_own_envelope():
    rich = [_match_row(f"2024-0{i}-01", f"O{i}", 6, 4) for i in range(1, 6)]
    thin = [_match_row("2024-05-01", "A", 8, 2)]
    raw = {
        "home": _seed_doc(rich),   # full
        "away": _seed_doc(thin),   # partial
        "min_sample": 3,
    }
    env_off = adapt_offline_seed_corners_to_f74(raw)
    assert env_off["available"] is True
    assert env_off["home"].get("corners_for_l5") == 6
    assert env_off["away"] == {}  # away skipped (partial)

    env_p = adapt_seed_partial_corners_to_f74(raw)
    assert env_p["available"] is True
    assert env_p["away"].get("corners_for_l5") == 8
    assert env_p["home"] == {}    # home skipped (already full)


# ─────────────────────────────────────────────────────────────────────
# 6. seed_partial leyendo de la MISMA colección (raw idéntico → ambos envelopes)
# ─────────────────────────────────────────────────────────────────────
def test_same_raw_payload_drives_both_envelopes():
    """Garantía binding: NO existe colección separada para seed_partial."""
    raw = {
        "home": _seed_doc([_match_row("2024-05-01", "A", 5, 3)]),     # 1 row
        "away": _seed_doc([_match_row(f"2024-0{i}-01", f"O{i}", 6, 4)
                            for i in range(1, 6)]),                    # 5 rows
        "min_sample": 3,
    }
    env_off = adapt_offline_seed_corners_to_f74(raw)
    env_p   = adapt_seed_partial_corners_to_f74(raw)
    # offline_seed → away (full); seed_partial → home (partial).
    assert env_off["away"].get("corners_for_l5") == 6
    assert env_p["home"].get("corners_for_l5")   == 5
    # Ambos vinieron del mismo dict.
    assert env_off["sources"]["raw_keys"] == ["home", "away"]
    assert env_p["sources"]["raw_keys"]   == ["home", "away"]


# ─────────────────────────────────────────────────────────────────────
# 7. NO se rellenan métricas fuera del scope (xG / goles / shots)
# ─────────────────────────────────────────────────────────────────────
def test_adapter_never_fills_xg_goals_or_shots():
    """Incluso si el documento del seed lleva goles, el adapter de córners
    NUNCA debe escribir goals_scored / xg_for / shots_for en el envelope."""
    matches = [
        _match_row(f"2024-0{i}-01", f"O{i}", 6, 4, gf=2, ga=1)
        for i in range(1, 6)
    ]
    raw = {"home": _seed_doc(matches), "away": _seed_doc(matches), "min_sample": 3}
    env = adapt_offline_seed_corners_to_f74(raw)
    for side in ("home", "away"):
        for forbidden in (
            "goals_scored_l5", "goals_conceded_l5",
            "xg_for_l5", "xg_against_l5",
            "shots_for_l5", "shots_on_target_l5",
            "possession_avg_l5",
        ):
            assert forbidden not in env[side], (
                f"adapter rellenó {side}.{forbidden} (debería estar fuera del scope)"
            )

    env_p = adapt_seed_partial_corners_to_f74(raw)
    # Mismo invariante en partial.
    for side in ("home", "away"):
        for forbidden in (
            "goals_scored_l5", "goals_conceded_l5",
            "xg_for_l5", "xg_against_l5",
            "shots_for_l5", "shots_on_target_l5",
        ):
            assert forbidden not in env_p[side]


# ─────────────────────────────────────────────────────────────────────
# 8. recent_fixtures incluye SOLO datos de córners
# ─────────────────────────────────────────────────────────────────────
def test_recent_fixtures_projection_is_corners_only():
    matches = [_match_row(f"2024-0{i}-01", f"O{i}", 6, 4, gf=3, ga=2)
                for i in range(1, 6)]
    raw = {"home": _seed_doc(matches), "away": _seed_doc(matches), "min_sample": 3}
    env = adapt_offline_seed_corners_to_f74(raw)
    rf = env["home"].get("recent_fixtures") or []
    assert len(rf) == 5
    for row in rf:
        assert "corners_for" in row
        assert "corners_against" in row
        assert "goals_for"     not in row
        assert "goals_against" not in row
        assert "xg"            not in row


# ─────────────────────────────────────────────────────────────────────
# 9. Raw vacío / inválido → unavailable
# ─────────────────────────────────────────────────────────────────────
def test_empty_raw_returns_unavailable():
    env_off = adapt_offline_seed_corners_to_f74(None)
    assert env_off["available"] is False
    assert env_off["source"] == SOURCE_OFFLINE
    env_p = adapt_seed_partial_corners_to_f74("not a dict")
    assert env_p["available"] is False
    assert env_p["source"] == SOURCE_PARTIAL


def test_no_matches_returns_unavailable():
    raw = {"home": _seed_doc([]), "away": _seed_doc([]), "min_sample": 3}
    env_off = adapt_offline_seed_corners_to_f74(raw)
    env_p   = adapt_seed_partial_corners_to_f74(raw)
    assert env_off["available"] is False
    assert env_p["available"]   is False
    # Provenance debe explicar el motivo.
    assert any("_side_skipped" in k for k in env_off["field_provenance"])
    skip_reasons_off = [
        r for k, v in env_off["field_provenance"].items()
        if k.endswith("_side_skipped")
        for r in v["reason_codes"]
    ]
    assert RC_SEED_NO_MATCHES in skip_reasons_off


# ─────────────────────────────────────────────────────────────────────
# 10. Hydrator F99.1 — feature flag, fail-soft, no consulta otra colección
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hydrator_skips_when_flag_off(monkeypatch):
    from services import football_offline_seed_hydrator as fosh
    monkeypatch.delenv(fosh.FLAG_ENV_VAR, raising=False)
    match = {"home_team": {"name": "A"}, "away_team": {"name": "B"}}
    attached = await fosh.hydrate_match_corners_offline_seed(match, db=object())
    assert attached is False
    trace = match[fosh.TRACE_KEY][fosh.SOURCE_KEY]
    assert trace["status"] == "SKIPPED"
    assert trace["reason"] == "feature_flag_off"


@pytest.mark.asyncio
async def test_hydrator_reads_only_corners_seed_collection(monkeypatch):
    """El hydrator debe usar EXCLUSIVAMENTE get_offline_corners_history.

    NO debe consultar football_team_xg_offline_seed ni ninguna otra colección.
    """
    from services import football_offline_seed_hydrator as fosh
    monkeypatch.setenv(fosh.FLAG_ENV_VAR, "true")

    seed_doc = {
        "available":     True,
        "source":        "offline_seed",
        "team_name":     "Arsenal",
        "matches":       [_match_row(f"2024-0{i}-01", "X", 6, 4) for i in range(1, 6)],
        "matches_count": 5,
        "reason_code":   "CORNERS_OFFLINE_SEED_HIT",
    }

    fake_get = AsyncMock(return_value=seed_doc)
    with patch(
        "services.football_corners_offline_seed.get_offline_corners_history",
        new=fake_get,
    ):
        match = {"home_team": {"name": "Arsenal"}, "away_team": {"name": "Liverpool"}}
        attached = await fosh.hydrate_match_corners_offline_seed(match, db=object())

    assert attached is True
    # Sólo la función de córners fue consultada (2 llamadas: home+away).
    assert fake_get.await_count == 2
    raw = match[fosh.RAW_KEY]
    assert raw["home"] is not None and raw["away"] is not None
    trace = match[fosh.TRACE_KEY][fosh.SOURCE_KEY]
    assert trace["status"] == "RICH"
    assert trace["sides"]["home"]["classified_as"] == "offline_seed"


@pytest.mark.asyncio
async def test_hydrator_classifies_partial_when_promoted_from_online(monkeypatch):
    from services import football_offline_seed_hydrator as fosh
    monkeypatch.setenv(fosh.FLAG_ENV_VAR, "true")

    partial_doc = {
        "available":         True,
        "source":            "offline_seed",
        "team_name":         "PSG",
        "matches":           [_match_row(f"2024-0{i}-01", "X", 7, 3) for i in range(1, 6)],
        "matches_count":     5,
        "underlying_source": "promoted_from_online",
    }

    with patch(
        "services.football_corners_offline_seed.get_offline_corners_history",
        new=AsyncMock(return_value=partial_doc),
    ):
        match = {"home_team": {"name": "PSG"}, "away_team": {"name": "Marseille"}}
        attached = await fosh.hydrate_match_corners_offline_seed(match, db=object())

    assert attached is True
    trace = match[fosh.TRACE_KEY][fosh.SOURCE_KEY]
    assert trace["sides"]["home"]["classified_as"] == "seed_partial"
    assert trace["sides"]["away"]["classified_as"] == "seed_partial"
    assert trace["status"] == "PARTIAL"  # nadie es "rico"


@pytest.mark.asyncio
async def test_hydrator_swallows_lookup_exceptions(monkeypatch):
    from services import football_offline_seed_hydrator as fosh
    monkeypatch.setenv(fosh.FLAG_ENV_VAR, "true")

    async def _boom(*_a, **_k):
        raise RuntimeError("mongo down")

    with patch(
        "services.football_corners_offline_seed.get_offline_corners_history",
        new=_boom,
    ):
        match = {"home_team": "A", "away_team": "B"}
        attached = await fosh.hydrate_match_corners_offline_seed(match, db=object())

    assert attached is False
    trace = match[fosh.TRACE_KEY][fosh.SOURCE_KEY]
    assert trace["status"] == "NO_DATA"


# ─────────────────────────────────────────────────────────────────────
# 11. Cascade integra los nuevos envelopes correctamente
# ─────────────────────────────────────────────────────────────────────
def test_cascade_uses_offline_seed_first_then_falls_back():
    """Con SofaScore en el envelope + offline_seed disponible, el ranking
    de córners ya pone offline_seed primero (F99-P2). Aquí validamos que
    el adapter+cascade funcionan en conjunto."""
    from services.adapters._envelope import new_envelope, set_field, finalize_envelope
    from services.football_source_cascade import cascade_merge_envelopes

    rich = [_match_row(f"2024-0{i}-01", f"O{i}", 7, 3) for i in range(1, 6)]
    raw = {"home": _seed_doc(rich), "away": _seed_doc(rich), "min_sample": 3}
    env_off = adapt_offline_seed_corners_to_f74(raw)

    # Simula SofaScore con un valor distinto.
    env_ss = new_envelope(source="sofascore", available=True)
    set_field(env_ss, "home.corners_for_l5", 4.9, sample_size=5)
    set_field(env_ss, "away.corners_for_l5", 5.1, sample_size=5)
    finalize_envelope(env_ss)

    merged = cascade_merge_envelopes([env_ss, env_off])
    # offline_seed gana porque es rank #1 en F99-P2.
    assert merged["home"]["corners_for_l5"] == 7
    assert merged["field_provenance"]["home.corners_for_l5"]["source"] == "offline_seed"
