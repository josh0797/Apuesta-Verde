"""Tests for services/friendly_dnb_rule.py (Phase 39, Fix 2).

Covers:
  * is_international_friendly — detection heuristics
  * detect_favorite_side
  * evaluate_friendly_dnb_preference — happy path + edge cases
  * build_learning_record — won/lost/push semantics
"""
from services import friendly_dnb_rule as fdr


# ─────────────────────────────────────────────────────────────────────
# is_international_friendly
# ─────────────────────────────────────────────────────────────────────
def test_detect_friendly_when_league_contains_keyword():
    match = {"league": {"name": "International Friendlies", "type": "Friendly"}}
    assert fdr.is_international_friendly(match) is True


def test_detect_friendly_when_competition_kind_national_team():
    match = {
        "league": {"name": "Amistosos selecciones"},
        "competition_kind": "international",
    }
    assert fdr.is_international_friendly(match) is True


def test_rejects_club_friendly():
    match = {"league": {"name": "Club friendly pre-season"}}
    assert fdr.is_international_friendly(match) is False


def test_rejects_regular_league():
    match = {"league": {"name": "Premier League", "type": "league"}}
    assert fdr.is_international_friendly(match) is False


def test_rejects_invalid_input():
    assert fdr.is_international_friendly(None) is False
    assert fdr.is_international_friendly("string") is False
    assert fdr.is_international_friendly({}) is False


# ─────────────────────────────────────────────────────────────────────
# detect_favorite_side
# ─────────────────────────────────────────────────────────────────────
def test_detect_home_favorite():
    assert fdr.detect_favorite_side(odds_home=1.40, odds_away=6.50) == "home"


def test_detect_away_favorite():
    assert fdr.detect_favorite_side(odds_home=8.00, odds_away=1.30) == "away"


def test_no_clear_favorite_when_balanced():
    assert fdr.detect_favorite_side(odds_home=2.10, odds_away=3.40) is None


def test_no_favorite_when_missing_odds():
    assert fdr.detect_favorite_side(odds_home=None, odds_away=1.40) is None


# ─────────────────────────────────────────────────────────────────────
# evaluate_friendly_dnb_preference
# ─────────────────────────────────────────────────────────────────────
def _match_intl_friendly():
    return {"league": {"name": "International Friendlies", "type": "Friendly"}}


def test_rule_fires_when_friendly_favorite_and_small_ml_premium():
    # ML 1.40 implied=0.71, DNB 1.20 implied=0.83 → premium = -0.12 (DNB
    # offers MORE protection than ML pays). Rule should fire.
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=1.40, odds_draw=4.50, odds_away=7.50,
        odds_dnb_home=1.20, odds_dnb_away=4.00,
    )
    assert out["applies"] is True
    assert out["favorite"] == "home"
    assert fdr.RC_FRIENDLY_INTL_DETECTED in out["reason_codes"]
    assert fdr.RC_DNB_PROTECTION_ATTRACTIVE in out["reason_codes"]
    # Without learned data, the "pattern not yet active" code must show.
    assert fdr.RC_PATTERN_NOT_YET_ACTIVE in out["reason_codes"]


def test_rule_does_not_fire_when_no_clear_favorite():
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=2.30, odds_draw=3.10, odds_away=3.20,
        odds_dnb_home=1.60, odds_dnb_away=2.20,
    )
    assert out["applies"] is False
    assert fdr.RC_FRIENDLY_INTL_DETECTED in out["reason_codes"]


def test_rule_does_not_fire_when_not_friendly():
    out = fdr.evaluate_friendly_dnb_preference(
        match={"league": {"name": "La Liga"}},
        odds_home=1.40, odds_draw=4.50, odds_away=7.50,
        odds_dnb_home=1.20, odds_dnb_away=4.00,
    )
    assert out["applies"] is False
    assert out["reason_codes"] == []


def test_rule_dampened_by_low_learned_hit_rate():
    learned = {"sample_size": 80, "hit_rate": 0.40}
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=1.40, odds_draw=4.50, odds_away=7.50,
        odds_dnb_home=1.20, odds_dnb_away=4.00,
        learned_pattern=learned,
    )
    assert out["applies"] is False
    assert out["pattern_active"] is True
    assert fdr.RC_PATTERN_LEARNED_DAMPENS in out["reason_codes"]


def test_rule_amplified_by_high_learned_hit_rate():
    learned = {"sample_size": 100, "hit_rate": 0.62}
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=1.40, odds_draw=4.50, odds_away=7.50,
        odds_dnb_home=1.20, odds_dnb_away=4.00,
        learned_pattern=learned,
    )
    assert out["applies"] is True
    assert out["pattern_active"] is True
    assert fdr.RC_PATTERN_LEARNED_AMPLIFIES in out["reason_codes"]


def test_rule_rejects_when_dnb_odds_too_low():
    # DNB below floor (1.18) → unprofitable protection.
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=1.50, odds_draw=4.50, odds_away=7.50,
        odds_dnb_home=1.05, odds_dnb_away=4.00,
    )
    assert out["applies"] is False


def test_rule_rejects_when_ml_premium_too_high():
    # Big premium ML vs DNB → ML is clearly the better value, no protection trade.
    # ml 1.20 (prob 0.833) vs dnb 1.85 (prob 0.541) → premium 0.29 > 0.10 → reject.
    out = fdr.evaluate_friendly_dnb_preference(
        match=_match_intl_friendly(),
        odds_home=1.20, odds_draw=6.00, odds_away=12.00,
        odds_dnb_home=1.85, odds_dnb_away=5.00,
    )
    assert out["applies"] is False


def test_rule_never_raises_on_bad_input():
    out = fdr.evaluate_friendly_dnb_preference(match=None)
    assert out["applies"] is False
    assert out["engine_version"] == "friendly_dnb.1"


# ─────────────────────────────────────────────────────────────────────
# build_learning_record
# ─────────────────────────────────────────────────────────────────────
def test_learning_record_dnb_pick_favorite_wins():
    rec = fdr.build_learning_record(
        match=_match_intl_friendly(),
        final_outcome="home", favorite="home", used_dnb=True,
    )
    assert rec["outcome"] == "won"
    assert rec["market"] == "DNB"
    assert rec["pattern_name"] == fdr.PATTERN_NAME


def test_learning_record_dnb_pick_draw_pushes():
    # Draw on DNB → push (warehouse void path → no degradation).
    rec = fdr.build_learning_record(
        match=_match_intl_friendly(),
        final_outcome="draw", favorite="home", used_dnb=True,
    )
    assert rec["outcome"] == "push"


def test_learning_record_moneyline_loses_on_draw():
    rec = fdr.build_learning_record(
        match=_match_intl_friendly(),
        final_outcome="draw", favorite="home", used_dnb=False,
    )
    assert rec["outcome"] == "lost"
    assert rec["market"] == "Moneyline"


def test_learning_record_returns_none_when_not_friendly():
    rec = fdr.build_learning_record(
        match={"league": {"name": "MLS"}},
        final_outcome="home", favorite="home", used_dnb=True,
    )
    assert rec is None
