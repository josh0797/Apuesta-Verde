"""Phase F85.3 — Tests for the public xG normaliser + signals + ingestor."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from services import football_xg_public_normalizer as nrm
from services import football_xg_public_signals as sig
from services import football_xg_public_ingestor as ing
from services.external_sources import fbref_client as fb


# ─────────────────────────────────────────────────────────────────────
# Test fixtures: log builders
# ─────────────────────────────────────────────────────────────────────
def _log(*, xg_for=None, xg_against=None, npxg_for=None, npxg_against=None,
         opp="Opp", date="2026-01-01") -> dict:
    return {
        "date": date, "opponent": opp,
        "xg_for": xg_for, "xg_against": xg_against,
        "npxg_for": npxg_for, "npxg_against": npxg_against,
    }


def _series(values_for: list[float], values_against: list[float],
            *, npxg_for: list[float] | None = None,
            npxg_against: list[float] | None = None) -> list[dict]:
    """Build a list of newest-first logs with the given xG series."""
    out = []
    n = len(values_for)
    for i in range(n):
        out.append(_log(
            xg_for=values_for[i],
            xg_against=values_against[i],
            npxg_for=(npxg_for[i] if npxg_for else None),
            npxg_against=(npxg_against[i] if npxg_against else None),
            opp=f"T{i}", date=f"2026-{12 - i:02d}-01",
        ))
    return out


# =====================================================================
# Normaliser
# =====================================================================
class TestComputeFbrefXgRecentAverages:
    def test_full_l15_window_yields_clean_averages(self):
        home = _series([1.5] * 15, [1.0] * 15)
        away = _series([1.2] * 15, [1.1] * 15)
        out = nrm.compute_fbref_xg_recent_averages(home, away)
        assert out["available"] is True
        assert out["partial"]   is False
        # L1 = first row.
        assert out["home"]["l1"]["xg_for_avg"]   == 1.5
        assert out["home"]["l5"]["xg_for_avg"]   == 1.5
        assert out["home"]["l5"]["sample_size"]  == 5
        assert out["home"]["l15"]["xg_for_avg"]  == 1.5
        assert out["home"]["l15"]["sample_size"] == 15
        # Combined derived.
        d = out["derived"]
        assert d["combined_l5_xg_for"]  == round(1.5 + 1.2, 3)
        assert d["combined_l15_xg_for"] == round(1.5 + 1.2, 3)
        assert d["combined_l5_xga"]     == round(1.0 + 1.1, 3)
        # Reason codes positive.
        assert nrm.RC_AVAILABLE     in out["reason_codes"]
        assert nrm.RC_L5_AVAILABLE  in out["reason_codes"]
        assert nrm.RC_L15_AVAILABLE in out["reason_codes"]

    def test_npxg_propagated_when_present(self):
        home = _series([1.5] * 15, [1.0] * 15,
                        npxg_for=[1.3] * 15, npxg_against=[1.0] * 15)
        away = _series([1.2] * 15, [1.1] * 15)
        out = nrm.compute_fbref_xg_recent_averages(home, away)
        assert out["home"]["l5"]["npxg_for_avg"]     == 1.3
        assert out["home"]["l15"]["npxg_against_avg"] == 1.0
        assert "npxg_for_avg" not in (out["away"]["l5"] or {})

    def test_partial_when_only_l5_available(self):
        home = _series([1.5] * 5, [1.0] * 5)
        away = _series([1.2] * 5, [1.1] * 5)
        out = nrm.compute_fbref_xg_recent_averages(home, away)
        assert out["available"] is True
        assert out["partial"]   is True
        # L15 windows are computed but sample_size < 15.
        assert out["home"]["l15"]["sample_size"] == 5
        assert nrm.RC_PARTIAL in out["reason_codes"]

    def test_unavailable_when_both_sides_empty(self):
        out = nrm.compute_fbref_xg_recent_averages([], [])
        assert out["available"] is False
        assert nrm.RC_NOT_AVAILABLE in out["reason_codes"]

    def test_one_side_missing_keeps_other_side(self):
        home = _series([1.5] * 15, [1.0] * 15)
        away: list[dict] = []
        out = nrm.compute_fbref_xg_recent_averages(home, away)
        assert out["available"] is True
        assert out["partial"]   is True
        assert out["home"]["l15"] is not None
        assert out["away"]["l15"] is None
        # No combined values when one side is None.
        assert out["derived"]["combined_l5_xg_for"]  is None
        assert out["derived"]["combined_l15_xg_for"] is None

    def test_rows_without_xg_are_skipped_in_averages(self):
        """Rows with xg_for=None must not skew the average."""
        home = [_log(xg_for=1.5, xg_against=1.0)] * 3 + [_log()] * 2
        away = _series([1.0] * 5, [1.0] * 5)
        out = nrm.compute_fbref_xg_recent_averages(home, away)
        # 1.5 average over 3 usable rows (None rows skipped).
        assert out["home"]["l5"]["xg_for_avg"] == 1.5
        assert out["home"]["l5"]["sample_size"] == 3  # only usable rows

    def test_never_raises_on_garbage_input(self):
        assert nrm.compute_fbref_xg_recent_averages(None, None)["available"] is False  # type: ignore[arg-type]
        assert nrm.compute_fbref_xg_recent_averages([], {"not": "a list"})["available"] is False  # type: ignore[arg-type]


# =====================================================================
# Signals
# =====================================================================
def _xg_snapshot(*, c_l5=None, c_l15=None, c_l5_ag=None,
                  home_l5_ag=None, away_l5_ag=None,
                  partial=False, available=True) -> dict:
    return {
        "available": available, "partial": partial, "source": "fbref",
        "home": {"team": "H",
                  "l5":  {"xg_for_avg": (c_l5 or 0) / 2, "xg_against_avg": home_l5_ag,
                           "sample_size": 5} if c_l5 is not None else None,
                  "l15": {"xg_for_avg": (c_l15 or 0) / 2, "xg_against_avg": None,
                           "sample_size": 15} if c_l15 is not None else None},
        "away": {"team": "A",
                  "l5":  {"xg_for_avg": (c_l5 or 0) / 2, "xg_against_avg": away_l5_ag,
                           "sample_size": 5} if c_l5 is not None else None,
                  "l15": {"xg_for_avg": (c_l15 or 0) / 2, "xg_against_avg": None,
                           "sample_size": 15} if c_l15 is not None else None},
        "derived": {
            "combined_l5_xg_for":  c_l5,
            "combined_l15_xg_for": c_l15,
            "combined_l5_xga":     c_l5_ag,
        },
    }


class TestPublicXgSignals:
    def test_under_profile_emits_xg_supports_under(self):
        snap = _xg_snapshot(c_l5=2.30, c_l15=2.40)
        out = sig.derive_public_xg_signals(snap)
        assert sig.XG_SUPPORTS_UNDER     in out["signals"]
        assert sig.LOW_RECENT_XG_PROFILE in out["signals"]
        assert sig.XG_SUPPORTS_OVER      not in out["signals"]

    def test_over_profile_emits_xg_supports_over(self):
        snap = _xg_snapshot(c_l5=3.00, c_l15=2.90)
        out = sig.derive_public_xg_signals(snap)
        assert sig.XG_SUPPORTS_OVER       in out["signals"]
        assert sig.HIGH_RECENT_XG_PROFILE in out["signals"]
        assert sig.XG_SUPPORTS_UNDER      not in out["signals"]

    def test_form_shift_when_l5_l15_diverge(self):
        snap = _xg_snapshot(c_l5=3.20, c_l15=2.50)  # delta 0.70 >= 0.45
        out = sig.derive_public_xg_signals(snap)
        assert sig.XG_FORM_SHIFT in out["signals"]

    def test_defensive_suppression_signal(self):
        snap = _xg_snapshot(c_l5=2.20, c_l15=2.40,
                              home_l5_ag=0.9, away_l5_ag=0.8)
        out = sig.derive_public_xg_signals(snap)
        assert sig.DEFENSIVE_XG_SUPPRESSION in out["signals"]

    def test_partial_sample_signal(self):
        snap = _xg_snapshot(c_l5=2.0, c_l15=2.1, partial=True)
        out = sig.derive_public_xg_signals(snap)
        assert sig.PUBLIC_XG_PARTIAL_SAMPLE in out["signals"]

    def test_forebet_conflict_with_under_xg(self):
        snap = _xg_snapshot(c_l5=2.30, c_l15=2.40)  # supports UNDER
        forebet = {"available": True, "predicted_score": "3-2"}  # 5 goals
        out = sig.derive_public_xg_signals(snap, forebet)
        assert sig.FOREBET_CONFLICTS_WITH_XG in out["signals"]
        assert sig.XG_SUPPORTS_UNDER         in out["signals"]

    def test_forebet_confirms_with_under_xg(self):
        snap = _xg_snapshot(c_l5=2.30, c_l15=2.40)
        forebet = {"available": True, "predicted_score": "1-1"}  # 2 goals
        out = sig.derive_public_xg_signals(snap, forebet)
        assert sig.FOREBET_CONFIRMS_XG in out["signals"]

    def test_unavailable_snapshot_returns_empty_signals(self):
        out = sig.derive_public_xg_signals({"available": False})
        assert out["signals"] == []
        out2 = sig.derive_public_xg_signals(None)  # type: ignore[arg-type]
        assert out2["signals"] == []


# =====================================================================
# Ingestor (orchestrator)
# =====================================================================
def _fbref_html_with_rows(rows: list[dict]) -> str:
    """Build a tiny FBref page with the given xG-bearing rows."""
    body = ""
    for r in rows:
        body += (
            "<tr>"
            f'<td>{r["date"]}</td><td>Comp</td><td>Home</td><td>W</td>'
            f'<td>{r["opponent"]}</td>'
            f'<td>{r["xg_for"]}</td><td>{r["xg_against"]}</td>'
            f'<td></td><td></td><td>10</td><td>4</td><td>52</td>'
            "</tr>"
        )
    header = (
        '<th data-stat="date"></th>'
        '<th data-stat="comp"></th>'
        '<th data-stat="venue"></th>'
        '<th data-stat="result"></th>'
        '<th data-stat="opponent"></th>'
        '<th data-stat="xg_for"></th>'
        '<th data-stat="xg_against"></th>'
        '<th data-stat="npxg_for"></th>'
        '<th data-stat="npxg_against"></th>'
        '<th data-stat="shots_for"></th>'
        '<th data-stat="shots_on_target_for"></th>'
        '<th data-stat="possession"></th>'
    )
    return (
        f'<table id="matchlogs_for">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{body}</tbody></table>'
    )


def _forebet_match_html(predicted: str = "2-1") -> str:
    return f"""
<html><body>
<h1>USA vs Paraguay</h1>
<div class="prediction-score">Predicción: {predicted}</div>
<div class="pick">1</div>
<div class="prob"><span class="percent home">45%</span>
<span class="percent draw">30%</span>
<span class="percent away">25%</span></div>
<div class="analysis">Promedio goles: 2.4. Probable Over 2.5.</div>
</body></html>
"""


class TestPublicXgEnrichmentDoesNotBlockMainPipeline:
    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_payload(self, monkeypatch):
        """If the underlying fetches exceed timeout, ingestor MUST
        return a TIMEOUT-shaped dict, NEVER raise."""
        async def _slow_fetch(self_, url, **kw):
            import asyncio as _a
            await _a.sleep(5)
            return None

        # Replace _do_fetch via a slow simulation.
        from services import football_xg_public_ingestor as ingmod

        async def _slow_do_fetch(*a, **kw):
            import asyncio as _a
            await _a.sleep(5)
            return {}

        monkeypatch.setattr(ingmod, "_do_fetch", _slow_do_fetch)

        match = {"match_id": "m1",
                 "home_team": {"name": "United States"},
                 "away_team": {"name": "Paraguay"}}
        out = await ingmod.enrich_public_xg_context(
            None, None, match, timeout_s=0.2,
        )
        assert out["available"]    is False
        assert out["status"]       == "TIMEOUT"
        assert ingmod.RC_TIMEOUT   in out["reason_codes"]
        # Message MUST tell the user the main analysis is unaffected.
        assert "no fue afectado" in (out.get("message") or "").lower()

    @pytest.mark.asyncio
    async def test_invalid_match_doc_returns_build_failed(self):
        out = await ing.enrich_public_xg_context(None, None, "not-a-dict")  # type: ignore[arg-type]
        assert out["available"] is False
        assert ing.RC_BUILD_FAILED in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_no_team_names_returns_no_teams_reason(self):
        match = {"match_id": "m1"}
        out = await ing.enrich_public_xg_context(
            httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(404, text=""))),
            None, match, timeout_s=2,
        )
        assert out["available"] is False
        assert ing.RC_NO_TEAMS in out["reason_codes"]


class TestPublicXgEnrichmentHappyPath:
    @pytest.mark.asyncio
    async def test_returns_xg_payload_and_forebet_when_both_succeed(self, monkeypatch):
        # Build 15-row FBref logs for both USA and Paraguay.
        usa_logs = [{"date": f"2026-{12-i:02d}-01", "opponent": f"T{i}",
                       "xg_for": 1.5, "xg_against": 1.0} for i in range(15)]
        par_logs = [{"date": f"2026-{12-i:02d}-01", "opponent": f"T{i}",
                       "xg_for": 1.2, "xg_against": 1.1} for i in range(15)]
        usa_html = _fbref_html_with_rows(usa_logs)
        par_html = _fbref_html_with_rows(par_logs)

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            if "United-States" in url:
                return httpx.Response(200, text=usa_html)
            if "Paraguay" in url:
                return httpx.Response(200, text=par_html)
            if "forebet.com" in url:
                return httpx.Response(200, text=_forebet_match_html("2-1"))
            return httpx.Response(404, text="")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await ing.enrich_public_xg_context(
                c, None,
                {"match_id": "m1",
                 "home_team": {"name": "United States"},
                 "away_team": {"name": "Paraguay"}},
                forebet_url="https://www.forebet.com/es/football/matches/usa-paraguay-1",
                timeout_s=4,
            )
        assert out["available"] is True
        # Both sources surfaced.
        assert out["xg_recent_averages"]["available"] is True
        assert out["forebet_context"]["available"]   is True
        assert ing.RC_FBREF_AVAILABLE   in out["reason_codes"]
        assert ing.RC_FOREBET_AVAILABLE in out["reason_codes"]
        # Data quality reflects full coverage.
        assert out["data_quality"] in {"USABLE", "PARTIAL"}
        # source_priority stamped.
        assert out["source_priority"] == ["thestatsapi", "fbref", "forebet"]

    @pytest.mark.asyncio
    async def test_forebet_failure_does_not_hide_fbref_xg(self):
        """FBref OK + Forebet 500 → payload still carries xG averages."""
        usa_html = _fbref_html_with_rows([
            {"date": f"2026-{12-i:02d}-01", "opponent": f"T{i}",
             "xg_for": 1.5, "xg_against": 1.0} for i in range(15)
        ])
        par_html = _fbref_html_with_rows([
            {"date": f"2026-{12-i:02d}-01", "opponent": f"T{i}",
             "xg_for": 1.2, "xg_against": 1.1} for i in range(15)
        ])

        def handler(req):
            url = str(req.url)
            if "United-States" in url:
                return httpx.Response(200, text=usa_html)
            if "Paraguay" in url:
                return httpx.Response(200, text=par_html)
            if "forebet.com" in url:
                return httpx.Response(500, text="boom")
            return httpx.Response(404, text="")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await ing.enrich_public_xg_context(
                c, None,
                {"match_id": "m1",
                 "home_team": {"name": "United States"},
                 "away_team": {"name": "Paraguay"}},
                forebet_url="https://www.forebet.com/es/football/matches/usa-paraguay-1",
                timeout_s=4,
            )
        assert out["xg_recent_averages"]["available"] is True
        assert out["forebet_context"]["available"]   is False
        # Payload as a whole still available.
        assert out["available"] is True
        assert ing.RC_FBREF_AVAILABLE in out["reason_codes"]

    @pytest.mark.asyncio
    async def test_fbref_failure_keeps_forebet_context(self):
        """If FBref both teams fail but Forebet works, payload still
        carries the Forebet context."""
        def handler(req):
            url = str(req.url)
            if "fbref.com" in url:
                return httpx.Response(500, text="boom")
            if "forebet.com" in url:
                return httpx.Response(200, text=_forebet_match_html("2-1"))
            return httpx.Response(404, text="")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await ing.enrich_public_xg_context(
                c, None,
                {"match_id": "m1",
                 "home_team": {"name": "United States"},
                 "away_team": {"name": "Paraguay"}},
                forebet_url="https://www.forebet.com/es/football/matches/usa-paraguay-1",
                timeout_s=4,
            )
        assert out["available"] is True
        assert out["xg_recent_averages"]["available"] is False
        assert out["forebet_context"]["available"]   is True


# =====================================================================
# Flag helpers
# =====================================================================
class TestFlagHelpers:
    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("True", True), ("1", True), ("on", True),
        ("false", False), ("0", False), ("", False), (None, False),
    ])
    def test_inline_default_off(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("ENABLE_INLINE_PUBLIC_XG_SCRAPING", raising=False)
        else:
            monkeypatch.setenv("ENABLE_INLINE_PUBLIC_XG_SCRAPING", raw)
        assert ing._enable_inline_scraping() is expected

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("false", False), ("0", False),
        ("", True),  # empty = default true (background allowed)
        (None, True),
    ])
    def test_background_default_on(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("ENABLE_BACKGROUND_PUBLIC_XG_SCRAPING", raising=False)
        else:
            monkeypatch.setenv("ENABLE_BACKGROUND_PUBLIC_XG_SCRAPING", raw)
        assert ing._enable_background_scraping() is expected
