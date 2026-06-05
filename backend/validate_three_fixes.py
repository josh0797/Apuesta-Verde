"""Final validation script for three integrated fixes.

This script demonstrates all three fixes working correctly:
1. Fix 1: game_openness unilateral dominance detection
2. Fix 2: interpreter guards for BTTS/Over
3. Fix 3: corner settlement extended
"""

import sys
sys.path.insert(0, '/app/backend')

from services.game_openness import (
    compute_game_openness,
    compute_unilateral_dominance_over_profile,
    guard_total_recommendation
)
from services.live_recommendation_settlement import (
    settle_corner_market,
    settle_event_extended
)
from services.human_live_interpreter import interpret_live

def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

def test_fix_1_unilateral_dominance():
    """Fix 1: Mexico-Serbia unilateral dominance detection"""
    print_section("FIX 1: Unilateral Dominance Detection (Mexico-Serbia 5-1)")
    
    # Mexico-Serbia stats
    home = {
        "expected_goals": 1.90,
        "shots": 17,
        "shots_on_target": 7,
        "corners": 6
    }
    away = {
        "expected_goals": 0.35,
        "shots": 3,
        "shots_on_target": 1,
        "corners": 1,
        "saves": 5,
        "own_goals": 2
    }
    
    # Test compute_game_openness
    openness = compute_game_openness(home, away, minute=70, current_total=3)
    print(f"\n✅ compute_game_openness:")
    print(f"   - combined_xg: {openness['combined_xg']}")
    print(f"   - is_one_sided: {openness['is_one_sided']}")
    print(f"   - is_bilateral: {openness['is_bilateral']}")
    print(f"   - supports_over_35: {openness['supports_over_35']}")
    print(f"   - supports_btts: {openness['supports_btts']}")
    
    assert openness['is_one_sided'] is True, "Mexico-Serbia must be one-sided"
    assert openness['is_bilateral'] is False, "Mexico-Serbia must NOT be bilateral"
    assert openness['supports_over_35'] is False, "Over 3.5 must NOT be supported by bilateral openness"
    assert openness['supports_btts'] is False, "BTTS must NOT be supported"
    print(f"   ✅ All assertions passed!")
    
    # Test compute_unilateral_dominance_over_profile
    profile = compute_unilateral_dominance_over_profile(
        home, away,
        match_context={"minute": 75, "current_total": 4, "score_diff": 3}
    )
    print(f"\n✅ compute_unilateral_dominance_over_profile:")
    print(f"   - profile_type: {profile['profile_type']}")
    print(f"   - is_dominant: {profile['is_dominant']}")
    print(f"   - has_collapse: {profile['has_collapse']}")
    print(f"   - supports_team_total: {profile['supports_team_total']}")
    print(f"   - supports_match_over_high: {profile['supports_match_over_high']}")
    print(f"   - collapse_indicators: {profile['collapse_indicators']}")
    
    assert profile['is_dominant'] is True, "Mexico must be dominant"
    assert profile['has_collapse'] is True, "Serbia must show collapse"
    assert profile['supports_match_over_high'] is True, "Match Over high supported via dominance"
    assert 'supports_btts' not in profile, "Dominance profile must NOT expose supports_btts"
    print(f"   ✅ All assertions passed!")

def test_fix_2_interpreter_guards():
    """Fix 2: Interpreter guards for BTTS/Over"""
    print_section("FIX 2: Interpreter Guards (BTTS/Over)")
    
    # Test BTTS guard when both teams scored
    match = {
        "home_team": {"name": "Team A"},
        "away_team": {"name": "Team B"},
        "live_stats": {
            "minute": 70,
            "score": {"home": 1, "away": 1},
            "stats_by_side": {"home": {}, "away": {}}
        }
    }
    reeval = {
        "market": "BTTS (Ambos marcan)",
        "live_state": "LIVE_VALUE_WINDOW",
        "recommended_action": "LIVE_ENTRY",
        "edge": 0.10,
        "confidence": 65,
        "game_openness": None
    }
    
    result = interpret_live(match, analysis={}, reeval=reeval)
    suggested = (result or {}).get("suggested_market") or ""
    
    print(f"\n✅ BTTS guard when both teams scored (1-1):")
    print(f"   - Input market: BTTS (Ambos marcan)")
    print(f"   - Current score: 1-1")
    print(f"   - Suggested market: {suggested}")
    assert "btts" not in suggested.lower(), "BTTS must be stripped when both teams scored"
    print(f"   ✅ BTTS correctly stripped!")
    
    # Test Over 2.5 guard when openness blocks it
    match2 = {
        "home_team": {"name": "Team C"},
        "away_team": {"name": "Team D"},
        "live_stats": {
            "minute": 55,
            "score": {"home": 1, "away": 0},
            "stats_by_side": {"home": {}, "away": {}}
        }
    }
    openness = {
        "combined_xg": 1.10,
        "is_one_sided": True,
        "supports_over_35": False,
        "supports_over_25": False,
        "reason_es": "Apertura unilateral; Over 2.5 sin respaldo."
    }
    reeval2 = {
        "market": "Over 2.5",
        "live_state": "LIVE_VALUE_WINDOW",
        "recommended_action": "LIVE_ENTRY",
        "edge": 0.08,
        "confidence": 60,
        "game_openness": openness
    }
    
    result2 = interpret_live(match2, analysis={}, reeval=reeval2)
    suggested2 = (result2 or {}).get("suggested_market") or ""
    
    print(f"\n✅ Over 2.5 guard when openness.supports_over_25=False:")
    print(f"   - Input market: Over 2.5")
    print(f"   - openness.supports_over_25: False")
    print(f"   - Suggested market: {suggested2}")
    assert "over 2.5" not in suggested2.lower() and "más de 2.5" not in suggested2.lower(), \
        "Over 2.5 must be stripped when openness blocks it"
    print(f"   ✅ Over 2.5 correctly stripped!")

def test_fix_3_corner_settlement():
    """Fix 3: Corner settlement extended"""
    print_section("FIX 3: Corner Settlement Extended")
    
    # Test total corners Over
    event1 = {
        "sport": "football",
        "match_id": "m-1",
        "recommendation": {
            "market": "Over 8.5 corners",
            "selection": "Over 8.5 corners"
        }
    }
    stats1 = {"corners_home": 6, "corners_away": 4}
    
    result1 = settle_corner_market(event1, stats1)
    print(f"\n✅ Total corners Over 8.5 with 10 corners:")
    print(f"   - status: {result1['status']}")
    print(f"   - market_type: {result1['market_type']}")
    print(f"   - total_corners: {result1['total_corners']}")
    assert result1['status'] == 'hit', "Over 8.5 with 10 corners must hit"
    print(f"   ✅ Correctly settled as HIT!")
    
    # Test team corners
    event2 = {
        "sport": "football",
        "match_id": "m-2",
        "recommendation": {
            "market": "Home team Over 4.5 corners",
            "selection": "Home team Over 4.5 corners"
        }
    }
    stats2 = {"corners_home": 5, "corners_away": 2}
    
    result2 = settle_corner_market(event2, stats2)
    print(f"\n✅ Home team Over 4.5 corners with home=5:")
    print(f"   - status: {result2['status']}")
    print(f"   - side: {result2['side']}")
    print(f"   - actual_value: {result2['actual_value']}")
    assert result2['status'] == 'hit', "Home Over 4.5 with 5 must hit"
    assert result2['side'] == 'home', "Side must be home"
    print(f"   ✅ Correctly settled as HIT!")
    
    # Test Spanish detection
    event3 = {
        "sport": "football",
        "match_id": "m-3",
        "recommendation": {
            "market": "Más de 8.5 córners",
            "selection": "Más de 8.5 córners"
        }
    }
    stats3 = {"corners_home": 5, "corners_away": 5}
    
    result3 = settle_corner_market(event3, stats3)
    print(f"\n✅ Spanish: 'Más de 8.5 córners' with 10 corners:")
    print(f"   - status: {result3['status']}")
    print(f"   - line: {result3['line']}")
    assert result3['status'] == 'hit', "Spanish Over 8.5 with 10 must hit"
    assert result3['line'] == 8.5, "Line must be 8.5"
    print(f"   ✅ Spanish detection working!")
    
    # Test Asian quarter handicap → manual
    event4 = {
        "sport": "football",
        "match_id": "m-4",
        "recommendation": {
            "market": "Home corner handicap +0.25",
            "selection": "Home corner handicap +0.25"
        }
    }
    stats4 = {"corners_home": 6, "corners_away": 5}
    
    result4 = settle_corner_market(event4, stats4)
    print(f"\n✅ Asian quarter handicap +0.25:")
    print(f"   - status: {result4['status']}")
    assert result4['status'] == 'requires_manual_settlement', "Asian quarter must require manual"
    print(f"   ✅ Correctly routed to manual settlement!")
    
    # Test non-corner market → None
    event5 = {
        "sport": "football",
        "match_id": "m-5",
        "recommendation": {
            "market": "BTTS YES",
            "selection": "BTTS YES"
        }
    }
    stats5 = {"corners_home": 6, "corners_away": 4}
    
    result5 = settle_event_extended(event5, stats5)
    print(f"\n✅ Non-corner market (BTTS YES):")
    print(f"   - settle_event_extended result: {result5}")
    assert result5 is None, "Non-corner markets must return None"
    print(f"   ✅ Correctly returns None (fall-back to legacy)!")

def main():
    print("\n" + "="*70)
    print("  THREE FIXES VALIDATION SCRIPT")
    print("="*70)
    
    try:
        test_fix_1_unilateral_dominance()
        test_fix_2_interpreter_guards()
        test_fix_3_corner_settlement()
        
        print("\n" + "="*70)
        print("  ✅ ALL THREE FIXES VALIDATED SUCCESSFULLY!")
        print("="*70)
        print("\nSummary:")
        print("  ✅ Fix 1: Unilateral dominance detection working")
        print("  ✅ Fix 2: Interpreter guards for BTTS/Over working")
        print("  ✅ Fix 3: Corner settlement extended working")
        print("\n" + "="*70)
        return 0
    except AssertionError as e:
        print(f"\n❌ VALIDATION FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
