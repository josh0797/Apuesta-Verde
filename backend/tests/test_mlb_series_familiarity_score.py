"""Tests for MLB Series Familiarity Score (Priority 3)."""
from __future__ import annotations

import pytest

from services.mlb_series_familiarity_score import (
    compute_series_familiarity_score,
    evaluate_lambda_boost,
    BUCKET_LOW,
    BUCKET_MEDIUM,
    BUCKET_HIGH,
    MAX_LAMBDA_BOOST,
    RC_SERIES_FAMILIARITY_DETECTED,
    RC_RECENT_REPEAT_MATCHUP,
    RC_SERIES_FAMILIARITY_TRAFFIC_BOOST,
    RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT,
    RC_SERIES_FAMILIARITY_CAPPED,
    RC_BULLPEN_FAMILIARITY_TRAFFIC_BOOST,
)


def _schedule_entry(date, home_id, away_id):
    return {
        "gameDate": date,
        "teams": {
            "home": {"team": {"id": home_id}},
            "away": {"team": {"id": away_id}},
        },
    }


class TestFailSoft:
    def test_invalid_game_date_returns_unavailable(self):
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="not-a-date",
            schedule=[],
        )
        assert out["available"] is False
        assert out["series_familiarity_score"] is None
        assert RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT in out["reason_codes"]

    def test_missing_schedule_returns_unavailable(self):
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=None,
        )
        assert out["available"] is False

    def test_empty_schedule_low_score(self):
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[],
        )
        assert out["available"] is True
        assert out["series_familiarity_score"] == 0.0
        assert out["bucket"] == BUCKET_LOW
        assert RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT in out["reason_codes"]


class TestRepeatedMatchup:
    def test_single_recent_matchup_medium_score(self):
        """One game in the last 3 days → ~18pts → low/medium boundary."""
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[
                _schedule_entry("2026-06-08T19:00:00Z", 111, 147),
            ],
        )
        assert out["series_familiarity_score"] >= 18
        assert RC_RECENT_REPEAT_MATCHUP in out["reason_codes"]

    def test_three_game_series_high_score(self):
        """Full 3-game series in last 3 days → HIGH bucket."""
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[
                _schedule_entry("2026-06-07T19:00:00Z", 111, 147),
                _schedule_entry("2026-06-08T19:00:00Z", 111, 147),
                _schedule_entry("2026-06-09T19:00:00Z", 111, 147),
            ],
        )
        assert out["series_familiarity_score"] >= 70
        assert out["bucket"] == BUCKET_HIGH
        assert RC_SERIES_FAMILIARITY_DETECTED in out["reason_codes"]

    def test_unrelated_games_dont_count(self):
        out = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[
                _schedule_entry("2026-06-08T19:00:00Z", 111, 999),
                _schedule_entry("2026-06-09T19:00:00Z", 888, 147),
            ],
        )
        assert out["series_familiarity_score"] == 0.0


class TestBullpenInteraction:
    def test_bullpen_usage_adds_to_score(self):
        out_low = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[
                _schedule_entry("2026-06-08T19:00:00Z", 111, 147),
                _schedule_entry("2026-06-09T19:00:00Z", 111, 147),
            ],
            bullpen_usage_5d=0.30,
        )
        out_high = compute_series_familiarity_score(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            schedule=[
                _schedule_entry("2026-06-08T19:00:00Z", 111, 147),
                _schedule_entry("2026-06-09T19:00:00Z", 111, 147),
            ],
            bullpen_usage_5d=0.85,
        )
        assert out_high["series_familiarity_score"] > out_low["series_familiarity_score"]
        assert RC_BULLPEN_FAMILIARITY_TRAFFIC_BOOST in out_high["reason_codes"]


class TestLambdaBoost:
    def test_low_familiarity_no_boost(self):
        b = evaluate_lambda_boost(
            series_familiarity_score=20,
            bullpen_fatigue=0.8,
            normalized_traffic=0.8,
        )
        assert b["boost"] == 0.0
        assert RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT in b["reason_codes"]

    def test_high_familiarity_alone_modest_boost(self):
        """Per spec: familiarity ALONE shouldn't inflate λ7-9 strongly.
        With fatigue=0 + traffic=0, boost = score * 0 = 0."""
        b = evaluate_lambda_boost(
            series_familiarity_score=85,
            bullpen_fatigue=0.0,
            normalized_traffic=0.0,
        )
        assert b["boost"] == 0.0

    def test_high_familiarity_plus_traffic_raises(self):
        b = evaluate_lambda_boost(
            series_familiarity_score=80,
            bullpen_fatigue=0.0,
            normalized_traffic=0.7,
        )
        assert 0.0 < b["boost"] <= MAX_LAMBDA_BOOST
        assert RC_SERIES_FAMILIARITY_TRAFFIC_BOOST in b["reason_codes"]

    def test_boost_capped_at_max(self):
        b = evaluate_lambda_boost(
            series_familiarity_score=100,
            bullpen_fatigue=1.0,
            normalized_traffic=1.0,
        )
        assert b["boost"] == MAX_LAMBDA_BOOST
        assert RC_SERIES_FAMILIARITY_CAPPED in b["reason_codes"]


class TestNoH2HAverageUsed:
    """Regression: ensure we don't accept / require a 'last_3_totals_average'
    parameter. The module signature must NOT include it."""
    def test_signature_excludes_h2h_totals(self):
        import inspect
        sig = inspect.signature(compute_series_familiarity_score)
        forbidden = {"last_3_totals_average", "h2h_total_avg", "h2h_average"}
        assert not (set(sig.parameters) & forbidden)


class TestCacheIntegration:
    @pytest.mark.asyncio
    async def test_hydrate_uses_cache(self):
        from services.mlb_series_familiarity_score import hydrate_series_familiarity
        calls = {"fetched": 0, "cache_gets": 0, "cache_sets": 0}
        cached = {}

        async def cache_get(k):
            calls["cache_gets"] += 1
            return cached.get(k)

        async def cache_set(k, v, ttl=None):
            calls["cache_sets"] += 1
            cached[k] = v

        async def fetch_schedule(start, end):
            calls["fetched"] += 1
            return [_schedule_entry("2026-06-08T19:00:00Z", 111, 147)]

        # First call: hits fetch + populates cache.
        out1 = await hydrate_series_familiarity(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            fetch_schedule=fetch_schedule,
            cache_get=cache_get, cache_set=cache_set,
        )
        # Second call: hits cache, no fetch.
        out2 = await hydrate_series_familiarity(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            fetch_schedule=fetch_schedule,
            cache_get=cache_get, cache_set=cache_set,
        )
        assert calls["fetched"] == 1
        assert out1 == out2
        assert out1["available"] is True

    @pytest.mark.asyncio
    async def test_hydrate_failsoft_on_fetch_error(self):
        from services.mlb_series_familiarity_score import hydrate_series_familiarity

        async def boom(start, end):
            raise RuntimeError("api down")

        out = await hydrate_series_familiarity(
            home_team_id=111, away_team_id=147, game_date="2026-06-10",
            fetch_schedule=boom,
        )
        assert out["available"] is False
        assert RC_SERIES_FAMILIARITY_LOW_NO_ADJUSTMENT in out["reason_codes"]
