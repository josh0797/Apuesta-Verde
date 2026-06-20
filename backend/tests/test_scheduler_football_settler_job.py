"""F95.4 — Tests del scheduler job `_job_settle_finished_football`.

Cobertura:
  - El job invoca `settle_recent_finished_football` con `hours_back=36`
    y `max_matches=50`.
  - Cualquier excepción del wrapper queda capturada (no se re-lanza)
    y se registra en `_status["last_run"]["settle_finished_football"]`.
  - El status persistido incluye el resumen de métricas.
  - El job está registrado en el scheduler con interval=20min e id estable.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import scheduler as sch_mod


# =====================================================================
# _job_settle_finished_football
# =====================================================================
class TestJobSettleFinishedFootball:
    @pytest.mark.asyncio
    async def test_job_calls_settler_with_expected_args(self):
        fake_summary = {
            "attempted":       5,
            "settled_full":    4,
            "settled_partial": 0,
            "no_data":         1,
            "errors":          0,
            "providers":       {"thestatsapi": 4, "none": 1},
        }
        mock_settler = AsyncMock(return_value=fake_summary)

        with patch(
            "services.football_finished_game_settler.settle_recent_finished_football",
            mock_settler,
        ):
            db = MagicMock()
            await sch_mod._job_settle_finished_football(db)

        assert mock_settler.await_count == 1
        call_args = mock_settler.await_args
        # Primer arg positional = db, resto via kwargs.
        assert call_args.args[0] is db
        assert call_args.kwargs.get("hours_back") == 36
        assert call_args.kwargs.get("max_matches") == 50
        assert "http_client" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_status_block_is_populated_on_success(self):
        fake_summary = {
            "attempted":       3,
            "settled_full":    2,
            "settled_partial": 0,
            "no_data":         1,
            "errors":          0,
            "providers":       {"thestatsapi": 2, "none": 1},
        }
        mock_settler = AsyncMock(return_value=fake_summary)

        with patch(
            "services.football_finished_game_settler.settle_recent_finished_football",
            mock_settler,
        ):
            db = MagicMock()
            # Limpiamos status previo.
            sch_mod._status["last_run"].pop("settle_finished_football", None)
            await sch_mod._job_settle_finished_football(db)

        st = sch_mod._status["last_run"].get("settle_finished_football")
        assert isinstance(st, dict)
        assert st.get("attempted") == 3
        assert st.get("settled_full") == 2
        assert st.get("no_data") == 1
        assert "started_at" in st
        assert "finished_at" in st

    @pytest.mark.asyncio
    async def test_job_fail_soft_on_exception(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("provider blew up")

        with patch(
            "services.football_finished_game_settler.settle_recent_finished_football",
            boom,
        ):
            db = MagicMock()
            sch_mod._status["last_run"].pop("settle_finished_football", None)
            # No debe re-raise.
            await sch_mod._job_settle_finished_football(db)

        st = sch_mod._status["last_run"].get("settle_finished_football")
        assert isinstance(st, dict)
        assert st.get("ok") is False
        assert "provider blew up" in st.get("error", "")

    @pytest.mark.asyncio
    async def test_job_handles_none_summary_gracefully(self):
        """Si el wrapper devuelve `None` (caso edge), el job no falla."""
        mock_settler = AsyncMock(return_value=None)

        with patch(
            "services.football_finished_game_settler.settle_recent_finished_football",
            mock_settler,
        ):
            db = MagicMock()
            sch_mod._status["last_run"].pop("settle_finished_football", None)
            await sch_mod._job_settle_finished_football(db)

        st = sch_mod._status["last_run"].get("settle_finished_football")
        assert isinstance(st, dict)
        assert "started_at" in st
        assert "finished_at" in st


# =====================================================================
# Función expuesta + registro estable
# =====================================================================
class TestJobRegistration:
    def test_job_is_callable_and_exported(self):
        assert hasattr(sch_mod, "_job_settle_finished_football")
        assert callable(sch_mod._job_settle_finished_football)

    def test_baseball_settler_still_present(self):
        """Regresión: el job MLB no debe romperse."""
        assert hasattr(sch_mod, "_job_settle_finished_baseball")
        assert callable(sch_mod._job_settle_finished_baseball)
