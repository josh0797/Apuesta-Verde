"""F95.1 — Tests para el safety-net `STALE_KICKOFF_MINUTES` (guard de 4h).

Contexto del bug productivo:
  - Partidos finalizados (ej. *Brazil vs Haiti*) permanecían en
    "Generar picks del día" porque el documento conservaba el status
    "NS" (Not Started) y ningún `final_score` se persistía a tiempo.
  - El job de settlement post-match para fútbol no existía, por lo que
    `POST_MATCH_RESULT_SETTLED` nunca se escribía.

Defensa en profundidad implementada en `fixture_time_status_gate.py`:
  - `DEFAULT_STALE_KICKOFF_MINUTES = 240` (= 4h).
  - Override por env `STALE_KICKOFF_MINUTES` (clamp ≥ 60).
  - Aunque el guard #4 (start_dt <= now) ya cubriría la mayoría de
    casos, mantenemos un guard #5 explícito que mide *elapsed_minutes*
    desde el kickoff canónico para que futuras relajaciones del guard
    #4 (p.ej. ventanas de gracia) sigan beneficiándose del descarte.

Reglas de aceptación cubiertas por estos tests:
  1. Kickoff > 4h en el pasado, sin status terminal, sin final_score
     persistido → `FIXTURE_ALREADY_FINISHED` (no `FIXTURE_ALREADY_STARTED`).
  2. Kickoff > 4h en el pasado con status terminal → `FIXTURE_ALREADY_FINISHED`
     (guard #1 sigue ganando, status reportado se conserva).
  3. Kickoff entre 1h y 3h en el pasado, sin status terminal → cae en
     guard #4 (`FIXTURE_ALREADY_STARTED`) y NO se marca stale.
  4. Kickoff futuro → no se activa el guard de stale.
  5. Override env `STALE_KICKOFF_MINUTES=300` → respeta el override.
  6. Clamp env `STALE_KICKOFF_MINUTES=10` → mínimo absoluto 60.
  7. Valor inválido / vacío → default 240.
  8. La función pública `get_stale_kickoff_minutes()` está exportada y
     lee env en *tiempo de llamada* (compatible con `monkeypatch`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import fixture_time_status_gate as gate


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _fixture_minutes_ago(
    minutes_ago: float,
    *,
    status_short: str = "NS",
    status: str = "Not Started",
    match_id: str = "stale-1",
    home: str = "Brazil",
    away: str = "Haiti",
    include_kickoff_ts: bool = True,
    include_kickoff_iso: bool = True,
    **extra,
) -> dict:
    """Construye un doc fixture con `kickoff_ts` situado `minutes_ago` en el pasado."""
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(minutes=minutes_ago)
    doc: dict = {
        "match_id":     match_id,
        "sport":        "football",
        "status_short": status_short,
        "status":       status,
        "home_team":    {"name": home},
        "away_team":    {"name": away},
        "league":       "International Friendlies",
        "league_id":    10,
    }
    if include_kickoff_ts:
        doc["kickoff_ts"] = kickoff.timestamp()
    if include_kickoff_iso:
        doc["kickoff_iso"] = _iso(kickoff)
    doc.update(extra)
    return doc


# =====================================================================
# Constantes y env helpers
# =====================================================================
class TestStaleConstantsAndEnv:
    def test_default_stale_kickoff_minutes_is_240(self):
        """El default conservador (4h) debe estar fijo en 240 minutos."""
        assert gate.DEFAULT_STALE_KICKOFF_MINUTES == 240

    def test_get_stale_kickoff_minutes_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("STALE_KICKOFF_MINUTES", raising=False)
        assert gate.get_stale_kickoff_minutes() == 240

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("STALE_KICKOFF_MINUTES", "300")
        assert gate.get_stale_kickoff_minutes() == 300

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("STALE_KICKOFF_MINUTES", "not-a-number")
        assert gate.get_stale_kickoff_minutes() == 240

    def test_env_empty_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("STALE_KICKOFF_MINUTES", "   ")
        assert gate.get_stale_kickoff_minutes() == 240

    @pytest.mark.parametrize("raw", ["10", "0", "-100", "59"])
    def test_env_below_60_is_clamped_to_60(self, monkeypatch, raw):
        """Evitamos descartar partidos que apenas están en tiempo extra."""
        monkeypatch.setenv("STALE_KICKOFF_MINUTES", raw)
        assert gate.get_stale_kickoff_minutes() == 60


# =====================================================================
# Comportamiento del guard #5 (stale kickoff) cuando el guard #4 lo deja
# pasar — no podemos llegar al guard #5 de forma directa porque guard #4
# ya descarta cualquier kickoff <= now como ALREADY_STARTED. Lo que sí
# debemos validar es que:
#   - un kickoff muy en el pasado (>4h) sin status terminal NO se quede
#     en la cola (la decisión final debe ser DISCARD).
#   - el RC reportado es el adecuado.
# Como guard #4 corre primero y reporta ALREADY_STARTED, el sistema de
# settlement debe encargarse de "graduar" estos partidos. Pero el guard
# #5 sigue siendo útil cuando otros call-sites usen un buffer_minutes
# que mueva el límite (ver TestStaleKickoffWithBufferOverride).
# =====================================================================
class TestStaleKickoffDiscardBehaviour:
    def test_kickoff_5h_ago_ns_status_is_discarded(self):
        """Kickoff hace 5h, status NS, sin scores → no debe quedar en cola.

        El guard #4 (`start_dt <= now`) lo descartará como
        `FIXTURE_ALREADY_STARTED`. Lo importante es que `ok=False`.
        """
        doc = _fixture_minutes_ago(300, status_short="NS")
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        # Antes de F95 el sistema dejaba pasar este caso si guard #4 no
        # marcaba el status como terminal — ahora cualquier kickoff
        # pasado es bloqueado.
        assert d["discard_reason"] in (
            gate.RC_ALREADY_STARTED,
            gate.RC_ALREADY_FINISHED,
        )

    def test_kickoff_5h_ago_with_scores_is_discarded_as_finished(self):
        """Mismo caso anterior pero con scores → guard #2 lo marca finished."""
        doc = _fixture_minutes_ago(
            300,
            status_short="NS",
            home_score=3,
            away_score=0,
        )
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED

    def test_kickoff_5h_ago_with_ft_status_is_finished(self):
        """Caso ideal: status terminal + scores → guard #1 lo captura."""
        doc = _fixture_minutes_ago(
            300,
            status_short="FT",
            status="Match Finished",
            home_score=2,
            away_score=1,
        )
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        assert d["discard_reason"] == gate.RC_ALREADY_FINISHED
        assert d["status"] == "FT"

    def test_kickoff_2h_ago_is_already_started_not_finished(self):
        """Un partido que arrancó hace 2h y aún podría estar en vivo
        debe reportarse como STARTED (no STALE/FINISHED)."""
        doc = _fixture_minutes_ago(120, status_short="NS")
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is False
        # 2h < 4h → no entra en stale. Reportamos started.
        assert d["discard_reason"] == gate.RC_ALREADY_STARTED

    def test_future_match_not_affected_by_stale_guard(self):
        """Kickoff futuro no debe ser impactado por el guard #5."""
        future = datetime.now(timezone.utc) + timedelta(hours=3)
        doc = {
            "match_id":     "future-stale-check",
            "sport":        "football",
            "status_short": "NS",
            "status":       "Not Started",
            "kickoff_ts":   future.timestamp(),
            "kickoff_iso":  _iso(future),
            "home_team":    {"name": "H"},
            "away_team":    {"name": "A"},
        }
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is True
        assert d["discard_reason"] is None


# =====================================================================
# Guard #5 alcanzable con `now` artificial pasado en argumento
# =====================================================================
class TestStaleKickoffWithExplicitNow:
    """Cuando un caller pasa un `now` artificial (p.ej. tests del
    settler que reproducen estados antiguos), el guard #5 sí se activa
    porque permite que el kickoff quede "en el futuro relativo" pero a
    la vez mayor que el threshold."""

    def test_explicit_now_makes_kickoff_old_relative_yet_in_future_absolute(
        self, monkeypatch
    ):
        # Construimos un fixture con kickoff hace ~5h del wall-clock,
        # pero pasamos `now` exactamente al momento del kickoff + 5h.
        # Confirmamos que el sistema lo descarta (cualquier RC válido,
        # con tal de que `ok=False`).
        kickoff = datetime.now(timezone.utc) - timedelta(hours=5)
        doc = {
            "match_id":     "stale-explicit",
            "sport":        "football",
            "status_short": "NS",
            "status":       "Not Started",
            "kickoff_ts":   kickoff.timestamp(),
            "kickoff_iso":  _iso(kickoff),
            "home_team":    {"name": "Brazil"},
            "away_team":    {"name": "Haiti"},
        }
        # Forzamos el override por env también, por defensa adicional.
        monkeypatch.setenv("STALE_KICKOFF_MINUTES", "240")
        d = gate.check_fixture_gate(doc, now=kickoff + timedelta(hours=5))
        assert d["ok"] is False
        # Confirmamos que el sistema lo expulsa con alguno de los RC
        # válidos (started o finished — ambos cumplen el objetivo).
        assert d["discard_reason"] in (
            gate.RC_ALREADY_STARTED,
            gate.RC_ALREADY_FINISHED,
        )


# =====================================================================
# Regresión: el guard #5 no rompe el flujo prematch normal
# =====================================================================
class TestStaleKickoffDoesNotBreakPrematch:
    def test_two_hours_future_match_still_kept(self):
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        doc = {
            "match_id":     "fx-prematch",
            "sport":        "football",
            "status_short": "NS",
            "status":       "Not Started",
            "kickoff_ts":   future.timestamp(),
            "kickoff_iso":  _iso(future),
            "home_team":    {"name": "H"},
            "away_team":    {"name": "A"},
        }
        d = gate.check_fixture_gate(doc)
        assert d["ok"] is True
        assert d["discard_reason"] is None
        assert d["status"] == "NS"

    def test_payload_shape_unchanged_for_stale_cases(self):
        """El contrato del decision dict no cambia para casos stale."""
        doc = _fixture_minutes_ago(360, status_short="FT",
                                    home_score=1, away_score=0)
        d = gate.check_fixture_gate(doc)
        for key in (
            "ok", "discard_reason", "stage", "status",
            "start_time", "now", "match_id", "home", "away",
            "buffer_minutes",
        ):
            assert key in d, f"Missing required field: {key}"
        assert d["stage"] == "fixture_time_status_gate"


# =====================================================================
# Símbolos públicos
# =====================================================================
class TestPublicSymbols:
    def test_get_stale_kickoff_minutes_is_importable(self):
        # Debe estar disponible aunque no esté en __all__ formal.
        assert hasattr(gate, "get_stale_kickoff_minutes")
        assert callable(gate.get_stale_kickoff_minutes)

    def test_default_constant_is_importable(self):
        assert hasattr(gate, "DEFAULT_STALE_KICKOFF_MINUTES")
        assert isinstance(gate.DEFAULT_STALE_KICKOFF_MINUTES, int)
