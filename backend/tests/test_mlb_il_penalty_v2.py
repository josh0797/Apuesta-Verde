"""Regression tests for the IL-penalty refactor (2026-06-02).

Cubre los 4 patrones del análisis del usuario:
  * Patrón B: distinguir 10-day vs 60-day vs minor-league IL.
  * Patrón C: fórmula dinámica proporcional (no saturada).
  * Patrón D: separar raw vs applied counts.
  * Integración contextual: offensive vs defensive direction.
"""
from __future__ import annotations

import pytest

from services.mlb_il_penalty import (
    apply_il_penalty,
    market_is_offensive,
    market_is_defensive,
    _classify_status,
    KEY_POSITIONS,
)


# ──────────────────────────────────────────────────────────────────
# Status classification (Patrón B)
# ──────────────────────────────────────────────────────────────────
class TestStatusClassification:
    @pytest.mark.parametrize("status,expected", [
        ("On the 10-day Injured List", "active_10d"),
        ("On the 10-Day Injured List", "active_10d"),
        ("On the 15-day Injured List", "active_10d"),
        ("On the 60-day Injured List", "long_term_60d"),
        ("On the 7-day Injured List",  "minors"),
        ("Restricted List",            "long_term_60d"),
        ("Day-To-Day",                 "day_to_day"),
        ("Day To Day",                 "day_to_day"),
        ("",                           "unknown"),
        (None,                         "unknown"),
    ])
    def test_classify(self, status, expected):
        assert _classify_status(status) == expected


# ──────────────────────────────────────────────────────────────────
# Patrón B: long-term IL no debe penalizar
# ──────────────────────────────────────────────────────────────────
class TestLongTermILExcluded:
    def test_60day_il_does_not_penalize(self):
        ctx = {
            "home_il_players": [
                {"name": f"Player{i}", "position": "SS",
                 "status": "On the 60-day Injured List"}
                for i in range(5)
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        # 5 bateadores clave en 60-day IL → NO se penaliza.
        assert out["home_key_il_count_applied"] == 0
        assert out["away_key_il_count_applied"] == 0
        assert out["er_adjustment"] == 0.0
        assert out["confidence_penalty"] == 0
        assert out["il_impact_label"] == "BAJO"
        # Pero el raw count SÍ los muestra (para diagnostics).
        assert out["home_key_il_count_raw"] == 5
        # Breakdown lo expone.
        assert out["_status_breakdown"]["home"]["long_term_60d"] == 5

    def test_minor_league_il_does_not_penalize(self):
        ctx = {
            "home_il_players": [
                {"name": "P1", "position": "C",
                 "status": "On the 7-day Injured List"},
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        assert out["home_key_il_count_applied"] == 0
        assert out["er_adjustment"] == 0.0


# ──────────────────────────────────────────────────────────────────
# Patrón C: fórmula dinámica
# ──────────────────────────────────────────────────────────────────
class TestDynamicPenalty:
    def _build_ctx(self, home_active_key: int, away_active_key: int) -> dict:
        positions = ["SS", "CF", "C", "2B"]
        home = [
            {"name": f"H{i}", "position": positions[i % 4],
             "status": "On the 10-day Injured List"}
            for i in range(home_active_key)
        ]
        away = [
            {"name": f"A{i}", "position": positions[i % 4],
             "status": "On the 10-day Injured List"}
            for i in range(away_active_key)
        ]
        return {"home_il_players": home, "away_il_players": away}

    def test_zero_il_zero_penalty(self):
        out = apply_il_penalty({"home_il_players": [], "away_il_players": []})
        assert out["er_adjustment"] == 0.0
        assert out["confidence_penalty"] == 0
        assert out["home_key_il_count"] == 0
        assert out["away_key_il_count"] == 0
        assert out["il_impact_label"] == "BAJO"
        assert out["cap_applied"] is False

    def test_one_il_proportional(self):
        out = apply_il_penalty(self._build_ctx(1, 0))
        # total = 1 → ER = -0.3, conf = -5
        assert out["er_adjustment"] == -0.3
        assert out["confidence_penalty"] == 5
        assert out["il_impact_label"] == "MEDIO"

    def test_two_il_proportional(self):
        out = apply_il_penalty(self._build_ctx(1, 1))
        # total = 2 → ER = -0.6, conf = -10
        assert out["er_adjustment"] == -0.6
        assert out["confidence_penalty"] == 10
        assert out["il_impact_label"] == "MEDIO"

    def test_three_il_high_label(self):
        out = apply_il_penalty(self._build_ctx(2, 1))
        # total = 3 → ER = -0.9, conf = -10 (cap)
        assert out["er_adjustment"] == -0.9
        assert out["il_impact_label"] == "ALTO"

    def test_cap_saturates(self):
        out = apply_il_penalty(self._build_ctx(4, 4))
        # total = 8 → ER cap = -1.0, conf cap = -10
        assert out["er_adjustment"] == -1.0
        assert out["confidence_penalty"] == 10


# ──────────────────────────────────────────────────────────────────
# Patrón D: raw vs applied
# ──────────────────────────────────────────────────────────────────
class TestRawVsApplied:
    def test_cap_applied_flag_when_too_many(self):
        # 7 active 10-day IL bats → cap at 4
        ctx = {
            "home_il_players": [
                {"name": f"H{i}", "position": "1B",
                 "status": "On the 10-day Injured List"}
                for i in range(7)
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        assert out["home_key_il_count_applied"] == 4
        assert out["home_key_il_count_raw"]     == 7
        assert out["cap_applied"] is True
        assert out["over_cap_excluded"]["home"] == 3

    def test_no_cap_when_under_threshold(self):
        ctx = {
            "home_il_players": [
                {"name": "H1", "position": "SS",
                 "status": "On the 10-day Injured List"},
                {"name": "H2", "position": "C",
                 "status": "On the 10-day Injured List"},
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        assert out["home_key_il_count_applied"] == 2
        assert out["home_key_il_count_raw"]     == 2
        assert out["cap_applied"] is False
        assert out["over_cap_excluded"]["home"] == 0

    def test_raw_count_includes_long_term(self):
        # raw es por posición clave SIN filtrar status. applied filtra.
        ctx = {
            "home_il_players": [
                {"name": "H1", "position": "SS",
                 "status": "On the 10-day Injured List"},
                {"name": "H2", "position": "C",
                 "status": "On the 60-day Injured List"},  # long-term
                {"name": "H3", "position": "1B",
                 "status": "On the 60-day Injured List"},
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        assert out["home_key_il_count_applied"] == 1
        assert out["home_key_il_count_raw"]     == 3  # incluye los 60-day


# ──────────────────────────────────────────────────────────────────
# Pitcher separation
# ──────────────────────────────────────────────────────────────────
class TestPitcherSeparation:
    def test_pitchers_not_in_key_count(self):
        ctx = {
            "home_il_players": [
                {"name": "SP1", "position": "P",
                 "status": "On the 15-day Injured List"},
                {"name": "RP1", "position": "RP",
                 "status": "On the 10-day Injured List"},
            ],
            "away_il_players": [],
        }
        out = apply_il_penalty(ctx)
        # Cero penalización ofensiva.
        assert out["home_key_il_count_applied"] == 0
        # Pero el contador de pitchers SÍ refleja.
        assert out["home_pitcher_il_count"] == 2


# ──────────────────────────────────────────────────────────────────
# Reproduce el bug original del usuario (3 partidos = HOME 4 AWAY 4)
# ──────────────────────────────────────────────────────────────────
class TestUserBugRegression:
    """Antes del refactor, 3 partidos distintos mostraban exactamente
    HOME 4 · AWAY 4 · ER -1.00 · Conf -10. La causa: jugadores en 60-day
    IL contaban igual que 10-day IL → el cap se saturaba siempre.

    Verificamos que ahora un equipo con 2 jugadores activos + 5 en 60-day
    NO devuelve 4: devuelve 2 (los reales activos).
    """

    def test_mixed_60day_and_10day_returns_real_count(self):
        ctx = {
            "home_il_players": [
                # 2 jugadores realmente activos en 10-day IL
                {"name": "Brandon Lowe", "position": "2B",
                 "status": "On the 10-day Injured List"},
                {"name": "Wander Franco", "position": "SS",
                 "status": "On the 10-day Injured List"},
                # 5 jugadores en 60-day o minor (no deben contar)
                {"name": "Long1", "position": "1B",
                 "status": "On the 60-day Injured List"},
                {"name": "Long2", "position": "OF",
                 "status": "On the 60-day Injured List"},
                {"name": "Long3", "position": "C",
                 "status": "Restricted List"},
                {"name": "Minors1", "position": "3B",
                 "status": "On the 7-day Injured List"},
                {"name": "Minors2", "position": "OF",
                 "status": "On the 7-day Injured List"},
            ],
            "away_il_players": [
                # 1 activo + 6 en 60-day/minor
                {"name": "Real1", "position": "C",
                 "status": "On the 10-day Injured List"},
                {"name": "Long1", "position": "SS",
                 "status": "On the 60-day Injured List"},
                {"name": "Long2", "position": "1B",
                 "status": "On the 60-day Injured List"},
                {"name": "Long3", "position": "OF",
                 "status": "On the 60-day Injured List"},
                {"name": "Long4", "position": "3B",
                 "status": "Restricted List"},
                {"name": "Minor1", "position": "OF",
                 "status": "On the 7-day Injured List"},
                {"name": "Minor2", "position": "1B",
                 "status": "On the 7-day Injured List"},
            ],
        }
        out = apply_il_penalty(ctx)
        assert out["home_key_il_count_applied"] == 2, (
            "Antes del fix: HOME devolvía 4 (cap saturado por 60-day IL). "
            "Ahora debe devolver 2 (solo activos)."
        )
        assert out["away_key_il_count_applied"] == 1
        # El ER ajuste ya NO es -1.0; ahora es -0.9 (3 totales * 0.3)
        assert out["er_adjustment"] == pytest.approx(-0.9, abs=0.01)
        # Conf penalty proporcional: 3 * 5 = 15 → cap 10
        assert out["confidence_penalty"] == 10
        # Raw vs applied diferencian.
        assert out["home_key_il_count_raw"] == 7  # 2 + 5
        assert out["away_key_il_count_raw"] == 7  # 1 + 6
        assert out["cap_applied"] is True


# ──────────────────────────────────────────────────────────────────
# Integración contextual
# ──────────────────────────────────────────────────────────────────
class TestContextualHelpers:
    @pytest.mark.parametrize("label,expected", [
        ("Over 8.5",                True),
        ("Run Line -1.5",           True),
        ("Team Total Over",         True),
        ("F5 Over 4.5",             True),
        ("Under 9.5",               False),
        ("F5 Under",                False),
        ("NRFI Yes",                False),
        ("",                        False),
        (None,                      False),
    ])
    def test_offensive_classification(self, label, expected):
        assert market_is_offensive(label) is expected

    @pytest.mark.parametrize("label,expected", [
        ("Under 9.5",               True),
        ("F5 Under",                True),
        ("NRFI Yes",                True),
        ("Over 8.5",                False),
        ("Run Line -1.5",           False),
        ("Team Total Over",         False),
    ])
    def test_defensive_classification(self, label, expected):
        assert market_is_defensive(label) is expected
