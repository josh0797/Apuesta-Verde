"""Sprint-D2 · Tests for the openfootball JSON parser + point-in-time
group standings + tournament-context integration.

These tests cover D2.1 + D2.2 and verify the strict no-leakage rule
for tournament-context standings.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from services.football_historical_ingestor import (
    parse_openfootball_json,
    _classify_openfootball_round,
    compute_group_standings_pit,
    build_point_in_time_features,
)


# ─── Synthetic 4-team group: A, B, C, D — 3 matchdays ────────────────────
_GROUP_JSON = {
    "name": "Test Cup 2099",
    "matches": [
        # MD1
        {"round": "Matchday 1", "date": "2099-06-10", "team1": "A", "team2": "B",
         "score": {"ft": [1, 1], "ht": [0, 0]}, "group": "Group X"},
        {"round": "Matchday 1", "date": "2099-06-10", "team1": "C", "team2": "D",
         "score": {"ft": [2, 0], "ht": [1, 0]}, "group": "Group X"},
        # MD2
        {"round": "Matchday 2", "date": "2099-06-14", "team1": "A", "team2": "C",
         "score": {"ft": [0, 0], "ht": [0, 0]}, "group": "Group X"},
        {"round": "Matchday 2", "date": "2099-06-14", "team1": "B", "team2": "D",
         "score": {"ft": [2, 1], "ht": [1, 0]}, "group": "Group X"},
        # MD3
        {"round": "Matchday 3", "date": "2099-06-18", "team1": "A", "team2": "D",
         "score": {"ft": [1, 1], "ht": [0, 1]}, "group": "Group X"},
        {"round": "Matchday 3", "date": "2099-06-18", "team1": "B", "team2": "C",
         "score": {"ft": [0, 0], "ht": [0, 0]}, "group": "Group X"},
        # Knockout — A vs C (winners)
        {"round": "Round of 16", "date": "2099-06-25", "team1": "A", "team2": "C",
         "score": {"ft": [2, 1], "ht": [1, 0]}},
        # Final
        {"round": "Final", "date": "2099-07-02", "team1": "A", "team2": "B",
         "score": {"ft": [1, 1], "ht": [0, 0]}},
    ],
}


# ════════════════════════════════════════════════════════════════════════
# _classify_openfootball_round
# ════════════════════════════════════════════════════════════════════════
class TestClassifyRound:
    @pytest.mark.parametrize("round_str,expected_phase,expected_md", [
        ("Matchday 1", "GROUP", 1),
        ("Matchday 2", "GROUP", 2),
        ("Matchday 3", "GROUP", 3),
        ("Matchday 10", "GROUP", 10),   # raw openfootball matchday-of-tournament
        ("Round of 16", "KNOCKOUT", None),
        ("Round of 32", "KNOCKOUT", None),
        ("Quarter-final", "KNOCKOUT", None),
        ("Quarter-finals", "KNOCKOUT", None),
        ("Semi-final", "KNOCKOUT", None),
        ("Third-place play-off", "KNOCKOUT", None),
        ("Third place playoff", "KNOCKOUT", None),
        ("Final", "KNOCKOUT", None),
        ("", "UNKNOWN", None),
        ("Friendly", "UNKNOWN", None),
    ])
    def test_round_classification(self, round_str, expected_phase, expected_md):
        phase, md = _classify_openfootball_round(round_str, None)
        assert phase == expected_phase
        assert md == expected_md


# ════════════════════════════════════════════════════════════════════════
# parse_openfootball_json
# ════════════════════════════════════════════════════════════════════════
class TestParseOpenfootball:
    def test_parses_dict_payload(self):
        matches = parse_openfootball_json(_GROUP_JSON, competition="Test Cup 2099")
        assert len(matches) == 8

    def test_parses_string_payload(self):
        matches = parse_openfootball_json(
            json.dumps(_GROUP_JSON), competition="Test Cup 2099"
        )
        assert len(matches) == 8

    def test_canonical_schema_fields(self):
        matches = parse_openfootball_json(_GROUP_JSON, competition="Test Cup 2099")
        m = matches[0]
        for key in ("competition", "date", "home_team", "away_team",
                    "fthg", "ftag", "ftr",
                    "tournament_phase", "matchday", "group_label",
                    "is_group_stage",
                    "odd_home", "odd_draw", "odd_away"):
            assert key in m, f"missing key {key}"

    def test_no_odds_for_openfootball(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        assert all(m["odd_home"] is None for m in matches)
        assert all(m["odd_draw"] is None for m in matches)
        assert all(m["odd_away"] is None for m in matches)

    def test_phase_classification_group_vs_knockout(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        group_matches = [m for m in matches if m["tournament_phase"] == "GROUP"]
        knockout_matches = [m for m in matches if m["tournament_phase"] == "KNOCKOUT"]
        assert len(group_matches) == 6
        assert len(knockout_matches) == 2

    def test_group_label_extracted(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        # All group matches should have group_label == "Group X".
        group_matches = [m for m in matches if m["tournament_phase"] == "GROUP"]
        assert all(m["group_label"] == "Group X" for m in group_matches)
        # Knockout matches should have group_label == None.
        knockout_matches = [m for m in matches if m["tournament_phase"] == "KNOCKOUT"]
        assert all(m["group_label"] is None for m in knockout_matches)

    def test_sorted_by_date(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        dates = [m["date"] for m in matches]
        assert dates == sorted(dates)

    def test_ftr_computation(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        # A 1-1 B → D, C 2-0 D → H, B 2-1 D → H, etc.
        first_md1_ab = next(m for m in matches
                            if m["home_team"] == "A" and m["away_team"] == "B")
        assert first_md1_ab["ftr"] == "D"
        cd_md1 = next(m for m in matches
                      if m["home_team"] == "C" and m["away_team"] == "D"
                      and m["date"].year == 2099 and m["date"].month == 6
                      and m["date"].day == 10)
        assert cd_md1["ftr"] == "H"

    def test_fail_soft_on_missing_score(self):
        bad = {"name": "X", "matches": [
            {"round": "Matchday 1", "date": "2099-06-10",
             "team1": "A", "team2": "B"},   # no score
            {"round": "Matchday 1", "date": "2099-06-10",
             "team1": "C", "team2": "D",
             "score": {"ft": [1, 0]}},
        ]}
        matches = parse_openfootball_json(bad)
        assert len(matches) == 1

    def test_fail_soft_on_missing_teams(self):
        bad = {"name": "X", "matches": [
            {"round": "Matchday 1", "date": "2099-06-10",
             "team1": "", "team2": "B",
             "score": {"ft": [1, 0]}},
        ]}
        matches = parse_openfootball_json(bad)
        assert len(matches) == 0

    def test_empty_payload(self):
        assert parse_openfootball_json({}) == []
        assert parse_openfootball_json({"matches": []}) == []

    def test_invalid_json_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_openfootball_json("not-json")


# ════════════════════════════════════════════════════════════════════════
# compute_group_standings_pit — strict no-leakage
# ════════════════════════════════════════════════════════════════════════
class TestGroupStandingsPIT:
    def test_no_standings_before_first_match(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        # Index 0 = first match A vs B on MD1.
        h, a = compute_group_standings_pit(
            matches, 0, home="A", away="B",
            group_label="Group X", competition="Test Cup 2099",
        )
        assert h["played"] == 0
        assert a["played"] == 0
        assert h["points"] == 0
        assert a["points"] == 0

    def test_standings_after_md1(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        # Find first MD2 match index (A vs C).
        md2_ac_idx = next(i for i, m in enumerate(matches)
                          if m["home_team"] == "A" and m["away_team"] == "C")
        h, a = compute_group_standings_pit(
            matches, md2_ac_idx, home="A", away="C",
            group_label="Group X", competition="Test Cup 2099",
        )
        # After MD1: A drew B (1pt), C beat D (3pt).
        assert h["played"] == 1 and h["points"] == 1
        assert a["played"] == 1 and a["points"] == 3

    def test_standings_after_md2_strict_leakage_check(self):
        """Index = MD3 match. Standings must NOT include any MD3
        result (even if same calendar day; here MD3 is 2099-06-18)."""
        matches = parse_openfootball_json(_GROUP_JSON)
        # Find MD3 A vs D.
        md3_ad_idx = next(
            i for i, m in enumerate(matches)
            if m["home_team"] == "A" and m["away_team"] == "D"
            and m["date"].month == 6 and m["date"].day == 18
        )
        h, a = compute_group_standings_pit(
            matches, md3_ad_idx, home="A", away="D",
            group_label="Group X", competition="Test Cup 2099",
        )
        # A: drew B (1pt), drew C (1pt) → 2 pts after 2 games.
        assert h["played"] == 2 and h["points"] == 2
        # D: lost to C (0pt), lost to B (0pt) → 0 pts after 2 games.
        assert a["played"] == 2 and a["points"] == 0
        # GD checks:
        assert h["gf"] == 1 and h["ga"] == 1 and h["gd"] == 0
        assert a["gf"] == 1 and a["ga"] == 4 and a["gd"] == -3

    def test_no_group_label_returns_empty(self):
        matches = parse_openfootball_json(_GROUP_JSON)
        h, a = compute_group_standings_pit(
            matches, 0, home="A", away="B",
            group_label=None, competition="Test Cup 2099",
        )
        assert h["played"] == 0
        assert a["played"] == 0

    def test_does_not_mix_competitions(self):
        """Even if group_label matches, only same-competition matches
        should contribute."""
        matches = parse_openfootball_json(_GROUP_JSON)
        # Pretend we're looking at MD3 but with the wrong competition.
        md3_ad_idx = next(
            i for i, m in enumerate(matches)
            if m["home_team"] == "A" and m["away_team"] == "D"
            and m["date"].month == 6 and m["date"].day == 18
        )
        h, a = compute_group_standings_pit(
            matches, md3_ad_idx, home="A", away="D",
            group_label="Group X", competition="Different Cup",
        )
        assert h["played"] == 0
        assert a["played"] == 0


# ════════════════════════════════════════════════════════════════════════
# build_point_in_time_features integration with openfootball
# ════════════════════════════════════════════════════════════════════════
class TestPITFeaturesOpenfootball:
    def test_features_include_tournament_context_score(self):
        matches = parse_openfootball_json(_GROUP_JSON, competition="Test Cup 2099")
        # MD3 A vs D (idx in sorted list).
        md3_ad_idx = next(
            i for i, m in enumerate(matches)
            if m["home_team"] == "A" and m["away_team"] == "D"
            and m["date"].month == 6 and m["date"].day == 18
        )
        features = build_point_in_time_features(matches, md3_ad_idx)
        assert "tournament_context_score" in features
        assert features["tournament_context_score"] is not None

    def test_group_matchday_derived_from_played_count(self):
        matches = parse_openfootball_json(_GROUP_JSON, competition="Test Cup 2099")
        md3_ad_idx = next(
            i for i, m in enumerate(matches)
            if m["home_team"] == "A" and m["away_team"] == "D"
            and m["date"].month == 6 and m["date"].day == 18
        )
        features = build_point_in_time_features(matches, md3_ad_idx)
        # Both A and D have played 2 games → group_matchday = 3
        assert features["_audit"]["group_matchday"] == 3

    def test_knockout_match_has_no_meaningful_context(self):
        matches = parse_openfootball_json(_GROUP_JSON, competition="Test Cup 2099")
        ko_idx = next(i for i, m in enumerate(matches)
                       if m["tournament_phase"] == "KNOCKOUT")
        features = build_point_in_time_features(matches, ko_idx)
        # Knockout: tournament_context_score capped at KNOCKOUT_MAX_SCORE.
        if features["tournament_context_score"] is not None:
            assert features["tournament_context_score"] <= 0.30
