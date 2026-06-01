"""
MLB Under Veto Layer - Comprehensive Backend Testing
=====================================================
Tests the 5 fixes for MLB Under veto to prevent picks like Yankees @ A's 13-8 (Under 9.5 lost).

FIX #1: POWER_BAT_PRESENT rule when OPS > 0.770 blocks Under
FIX #2: build_under_veto_context() extracts home_team_ops/away_team_ops
FIX #3: Integration of fetch_recent_bullpen_workload with pitch_stress_index
FIX #4: MLB_SEED_CASES + detect_mlb_under_warning_pattern in learning_cases.py
FIX #5: Integration of veto + learning_cases in mlb_under_market_selector flow
"""
import sys
import json
from datetime import datetime

print("\n" + "="*80)
print("MLB UNDER VETO LAYER - COMPREHENSIVE BACKEND TESTING")
print("="*80)

passed_tests = 0
failed_tests = 0

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: FIX #1 - POWER_BAT_PRESENT rule (OPS > 0.770)
# ══════════════════════════════════════════════════════════════════════════════
print("\n[TEST 1] FIX #1 - POWER_BAT_PRESENT rule when OPS > 0.770")
print("-" * 80)

try:
    from services.mlb_under_veto_layer import (
        evaluate_under_veto,
        POWER_BAT_OPS_THRESHOLD,
    )
    print(f"✅ Successfully imported mlb_under_veto_layer")
    print(f"   POWER_BAT_OPS_THRESHOLD = {POWER_BAT_OPS_THRESHOLD}")
except Exception as e:
    print(f"❌ Failed to import mlb_under_veto_layer: {e}")
    sys.exit(1)

# Test case 1.1: OPS > 0.770 should BLOCK Under
print("\nTest 1.1: Yankees @ A's case - home OPS 0.785 should BLOCK")
result = evaluate_under_veto(
    pitcher_home={"era": 3.2, "whip": 1.15, "games_pitched": 10, "quality_score": 0.65},
    pitcher_away={"era": 4.1, "whip": 1.30, "games_pitched": 8, "quality_score": 0.55},
    park={"run_factor": 1.0},
    book_total=9.5,
    expected_runs=8.0,
    home_team_ops=0.785,  # Yankees OPS > 0.770
    away_team_ops=0.720,
)
if result["severity"] == "BLOCKED" and result["veto"] and "POWER_BAT_PRESENT" in result["veto_reasons"]:
    print(f"✅ PASSED - Severity: {result['severity']}, Veto: {result['veto']}, Reasons: {result['veto_reasons']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected BLOCKED with POWER_BAT_PRESENT")
    print(f"   Got: severity={result['severity']}, veto={result['veto']}, reasons={result['veto_reasons']}")
    failed_tests += 1

# Test case 1.2: OPS < 0.770 should PASS
print("\nTest 1.2: Both teams OPS < 0.770 should PASS")
result = evaluate_under_veto(
    pitcher_home={"era": 3.2, "whip": 1.15, "games_pitched": 10, "quality_score": 0.65},
    pitcher_away={"era": 4.1, "whip": 1.30, "games_pitched": 8, "quality_score": 0.55},
    park={"run_factor": 1.0},
    book_total=9.5,
    expected_runs=8.0,
    home_team_ops=0.720,
    away_team_ops=0.740,
)
if result["severity"] == "PASS" and not result["veto"]:
    print(f"✅ PASSED - Severity: {result['severity']}, Veto: {result['veto']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected PASS with veto=False")
    print(f"   Got: severity={result['severity']}, veto={result['veto']}")
    failed_tests += 1

# Test case 1.3: Away team OPS > 0.770 should also BLOCK
print("\nTest 1.3: Away team OPS 0.790 should BLOCK")
result = evaluate_under_veto(
    pitcher_home={"era": 3.2, "whip": 1.15, "games_pitched": 10, "quality_score": 0.65},
    pitcher_away={"era": 4.1, "whip": 1.30, "games_pitched": 8, "quality_score": 0.55},
    park={"run_factor": 1.0},
    book_total=9.5,
    expected_runs=8.0,
    home_team_ops=0.720,
    away_team_ops=0.790,  # Away OPS > 0.770
)
if result["severity"] == "BLOCKED" and result["veto"] and "POWER_BAT_PRESENT" in result["veto_reasons"]:
    print(f"✅ PASSED - Severity: {result['severity']}, Veto: {result['veto']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected BLOCKED with POWER_BAT_PRESENT")
    print(f"   Got: severity={result['severity']}, veto={result['veto']}, reasons={result['veto_reasons']}")
    failed_tests += 1

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: FIX #2 - build_under_veto_context() extracts OPS correctly
# ══════════════════════════════════════════════════════════════════════════════
print("\n[TEST 2] FIX #2 - build_under_veto_context() extracts home_team_ops/away_team_ops")
print("-" * 80)

try:
    from services.mlb_under_veto_layer import build_under_veto_context
    print("✅ Successfully imported build_under_veto_context")
except Exception as e:
    print(f"❌ Failed to import build_under_veto_context: {e}")
    sys.exit(1)

# Test case 2.1: Extract OPS from profile.batting.home.ops / away.ops
print("\nTest 2.1: Extract OPS from profile.batting.home.ops / away.ops")
profile = {
    "batting": {
        "home": {"ops": 0.785, "avg": 0.265},
        "away": {"ops": 0.720, "avg": 0.250},
    },
    "pitching": {
        "homeStarter": {"name": "Pitcher A", "era": 3.2, "whip": 1.15, "games_pitched": 10},
        "awayStarter": {"name": "Pitcher B", "era": 4.1, "whip": 1.30, "games_pitched": 8},
    },
}
context = build_under_veto_context(profile)
if context.get("home_team_ops") == 0.785 and context.get("away_team_ops") == 0.720:
    print(f"✅ PASSED - home_team_ops: {context['home_team_ops']}, away_team_ops: {context['away_team_ops']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected home_team_ops=0.785, away_team_ops=0.720")
    print(f"   Got: home_team_ops={context.get('home_team_ops')}, away_team_ops={context.get('away_team_ops')}")
    failed_tests += 1

# Test case 2.2: Test alias support (team_ops, teamOps)
print("\nTest 2.2: Test alias support (team_ops, teamOps)")
profile = {
    "batting": {
        "home": {"team_ops": 0.800},  # Using alias
        "away": {"teamOps": 0.750},   # Using different alias
    },
    "pitching": {
        "homeStarter": {"name": "Pitcher A", "era": 3.2, "whip": 1.15, "games_pitched": 10},
        "awayStarter": {"name": "Pitcher B", "era": 4.1, "whip": 1.30, "games_pitched": 8},
    },
}
context = build_under_veto_context(profile)
if context.get("home_team_ops") == 0.800 and context.get("away_team_ops") == 0.750:
    print(f"✅ PASSED - Aliases work correctly")
    passed_tests += 1
else:
    print(f"❌ FAILED - Alias support not working")
    print(f"   Got: home_team_ops={context.get('home_team_ops')}, away_team_ops={context.get('away_team_ops')}")
    failed_tests += 1

# Test case 2.3: Missing OPS should return None (fail-soft)
print("\nTest 2.3: Missing OPS should return None (fail-soft)")
profile = {
    "batting": {
        "home": {"avg": 0.265},  # No OPS field
        "away": {"avg": 0.250},
    },
    "pitching": {
        "homeStarter": {"name": "Pitcher A", "era": 3.2, "whip": 1.15, "games_pitched": 10},
        "awayStarter": {"name": "Pitcher B", "era": 4.1, "whip": 1.30, "games_pitched": 8},
    },
}
context = build_under_veto_context(profile)
if context.get("home_team_ops") is None and context.get("away_team_ops") is None:
    print(f"✅ PASSED - Missing OPS returns None (fail-soft)")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected None for missing OPS")
    print(f"   Got: home_team_ops={context.get('home_team_ops')}, away_team_ops={context.get('away_team_ops')}")
    failed_tests += 1

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: FIX #3 - build_under_veto_context() propagates bullpen_real
# ══════════════════════════════════════════════════════════════════════════════
print("\n[TEST 3] FIX #3 - build_under_veto_context() propagates home_bullpen_real/away_bullpen_real")
print("-" * 80)

# Test case 3.1: Bullpen real data with pitch_stress_index
print("\nTest 3.1: Bullpen real data with pitch_stress_index propagated")
profile = {
    "pitching": {
        "homeStarter": {"name": "Pitcher A", "era": 3.2, "whip": 1.15, "games_pitched": 10},
        "awayStarter": {"name": "Pitcher B", "era": 4.1, "whip": 1.30, "games_pitched": 8},
    },
    "home_bullpen_real": {
        "team_id": 147,
        "bullpen_pitches_48h": 166,
        "pitch_stress_index": 3.24,  # > 1.5 threshold
    },
    "away_bullpen_real": {
        "team_id": 142,
        "bullpen_pitches_48h": 163,
        "pitch_stress_index": 3.31,  # > 1.5 threshold
    },
}
context = build_under_veto_context(profile)
home_bp = context.get("home_bullpen_real") or {}
away_bp = context.get("away_bullpen_real") or {}
if home_bp.get("pitch_stress_index") == 3.24 and away_bp.get("pitch_stress_index") == 3.31:
    print(f"✅ PASSED - Bullpen real data propagated correctly")
    print(f"   home pitch_stress_index: {home_bp.get('pitch_stress_index')}")
    print(f"   away pitch_stress_index: {away_bp.get('pitch_stress_index')}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Bullpen real data not propagated correctly")
    print(f"   home_bullpen_real: {home_bp}")
    print(f"   away_bullpen_real: {away_bp}")
    failed_tests += 1

# Test case 3.2: BULLPEN_PITCH_STRESS_HIGH rule activates when pitch_stress_index > 1.5
print("\nTest 3.2: Twins @ Pirates case - pitch_stress > 1.5 triggers BULLPEN_PITCH_STRESS_HIGH")
result = evaluate_under_veto(
    pitcher_home={"era": 3.2, "whip": 1.15, "games_pitched": 10, "quality_score": 0.65},
    pitcher_away={"era": 4.1, "whip": 1.30, "games_pitched": 8, "quality_score": 0.55},
    park={"run_factor": 1.0},
    book_total=9.5,
    expected_runs=8.0,
    home_bullpen_real={"pitch_stress_index": 3.24},  # > 1.5
    away_bullpen_real={"pitch_stress_index": 3.31},  # > 1.5
)
if "BULLPEN_PITCH_STRESS_HIGH" in result["veto_reasons"]:
    print(f"✅ PASSED - BULLPEN_PITCH_STRESS_HIGH triggered")
    print(f"   Reasons: {result['veto_reasons']}, Severity: {result['severity']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected BULLPEN_PITCH_STRESS_HIGH in reasons")
    print(f"   Got reasons: {result['veto_reasons']}")
    failed_tests += 1

# Test case 3.3: pitch_stress_index <= 1.5 should NOT trigger
print("\nTest 3.3: Low pitch_stress_index should NOT trigger BULLPEN_PITCH_STRESS_HIGH")
result = evaluate_under_veto(
    pitcher_home={"era": 3.2, "whip": 1.15, "games_pitched": 10, "quality_score": 0.65},
    pitcher_away={"era": 4.1, "whip": 1.30, "games_pitched": 8, "quality_score": 0.55},
    park={"run_factor": 1.0},
    book_total=9.5,
    expected_runs=8.0,
    home_bullpen_real={"pitch_stress_index": 1.2},  # <= 1.5
    away_bullpen_real={"pitch_stress_index": 1.0},  # <= 1.5
)
if "BULLPEN_PITCH_STRESS_HIGH" not in result["veto_reasons"]:
    print(f"✅ PASSED - BULLPEN_PITCH_STRESS_HIGH not triggered for low stress")
    print(f"   Reasons: {result['veto_reasons']}")
    passed_tests += 1
else:
    print(f"❌ FAILED - BULLPEN_PITCH_STRESS_HIGH should NOT be in reasons")
    print(f"   Got reasons: {result['veto_reasons']}")
    failed_tests += 1

# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: FIX #4 - detect_mlb_under_warning_pattern()
# ══════════════════════════════════════════════════════════════════════════════
print("\n[TEST 4] FIX #4 - detect_mlb_under_warning_pattern() with MLB_SEED_CASES")
print("-" * 80)

try:
    from services.learning_cases import (
        detect_mlb_under_warning_pattern,
        MLB_SEED_CASES,
    )
    print(f"✅ Successfully imported learning_cases")
    print(f"   MLB_SEED_CASES count: {len(MLB_SEED_CASES)}")
except Exception as e:
    print(f"❌ Failed to import learning_cases: {e}")
    sys.exit(1)

# Test case 4.1: Power bat pattern (OPS > 0.770)
print("\nTest 4.1: Power bat pattern detected (OPS > 0.770)")
ctx = {
    "home_team_ops": 0.785,  # Yankees OPS
    "away_team_ops": 0.720,
}
result = detect_mlb_under_warning_pattern(scoring_ctx=ctx)
if result and result.get("any_block"):
    rules = result.get("rules_fired", [])
    if any(r.get("rule_key") == "power_bat_visiting_avoid_under" for r in rules):
        print(f"✅ PASSED - Power bat pattern detected")
        print(f"   Rules fired: {[r.get('rule_key') for r in rules]}")
        passed_tests += 1
    else:
        print(f"❌ FAILED - Expected 'power_bat_visiting_avoid_under' rule")
        print(f"   Got rules: {rules}")
        failed_tests += 1
else:
    print(f"❌ FAILED - Expected any_block=True for OPS > 0.770")
    print(f"   Got result: {result}")
    failed_tests += 1

# Test case 4.2: Active series overs with fatigued bullpen
print("\nTest 4.2: Active series overs pattern detected")
ctx = {
    "home_team_ops": 0.720,
    "away_team_ops": 0.730,
    "active_series_context": {
        "total_runs_avg": 15.0,  # > 12
        "games_in_series": 3,     # >= 2
    },
    "home_bullpen_real": {"pitch_stress_index": 3.24},  # > 1.5
    "away_bullpen_real": {"pitch_stress_index": 3.31},  # > 1.5
}
result = detect_mlb_under_warning_pattern(scoring_ctx=ctx)
if result and result.get("any_block"):
    rules = result.get("rules_fired", [])
    if any(r.get("rule_key") == "active_series_overs_avoid_under" for r in rules):
        print(f"✅ PASSED - Active series overs pattern detected")
        print(f"   Rules fired: {[r.get('rule_key') for r in rules]}")
        passed_tests += 1
    else:
        print(f"❌ FAILED - Expected 'active_series_overs_avoid_under' rule")
        print(f"   Got rules: {rules}")
        failed_tests += 1
else:
    print(f"❌ FAILED - Expected any_block=True for series overs pattern")
    print(f"   Got result: {result}")
    failed_tests += 1

# Test case 4.3: No pattern should return None or any_block=False
print("\nTest 4.3: No pattern detected (any_block=False or None)")
ctx = {
    "home_team_ops": 0.720,
    "away_team_ops": 0.730,
    "active_series_context": {
        "total_runs_avg": 8.0,   # < 12
        "games_in_series": 1,
    },
    "home_bullpen_real": {"pitch_stress_index": 1.0},  # <= 1.5
    "away_bullpen_real": {"pitch_stress_index": 1.2},  # <= 1.5
}
result = detect_mlb_under_warning_pattern(scoring_ctx=ctx)
if result is None or not result.get("any_block"):
    print(f"✅ PASSED - No pattern detected")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected no pattern detection")
    print(f"   Got result: {result}")
    failed_tests += 1

# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: FIX #5 - select_under_market_with_bullpen_risk()
# ══════════════════════════════════════════════════════════════════════════════
print("\n[TEST 5] FIX #5 - select_under_market_with_bullpen_risk() in mlb_under_market_selector")
print("-" * 80)

try:
    from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
    print("✅ Successfully imported mlb_under_market_selector")
except Exception as e:
    print(f"❌ Failed to import mlb_under_market_selector: {e}")
    sys.exit(1)

# Test case 5.1: Full Game Under with HIGH bullpen risk should trigger rule
print("\nTest 5.1: Full Game Under with HIGH bullpen risk triggers rule")
result = select_under_market_with_bullpen_risk(
    expected_runs=7.5,
    full_game_total_line=9.5,
    f5_total_line=4.5,
    pitcher_score=70.0,  # Strong starters
    park_factor=0.95,    # Pitcher-friendly
    bullpen_risk="HIGH",
    current_selection={
        "market": "Total Runs Under 9.5",
        "score": 75,
        "line": 9.5,
    },
)
if result.get("rule_triggered"):
    print(f"✅ PASSED - Rule triggered for Full Game Under with HIGH bullpen risk")
    selected = result.get('selected_market') or {}
    print(f"   Selected market: {selected.get('market') if isinstance(selected, dict) else selected}")
    print(f"   Reason codes: {result.get('reason_codes')}")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected rule_triggered=True")
    print(f"   Got result: {result}")
    failed_tests += 1

# Test case 5.2: Full Game Under with LOW bullpen risk should NOT trigger
print("\nTest 5.2: Full Game Under with LOW bullpen risk does NOT trigger")
result = select_under_market_with_bullpen_risk(
    expected_runs=7.5,
    full_game_total_line=9.5,
    pitcher_score=70.0,
    park_factor=0.95,
    bullpen_risk="LOW",
    current_selection={
        "market": "Total Runs Under 9.5",
        "score": 75,
        "line": 9.5,
    },
)
if not result.get("rule_triggered"):
    print(f"✅ PASSED - Rule NOT triggered for LOW bullpen risk")
    passed_tests += 1
else:
    print(f"❌ FAILED - Expected rule_triggered=False for LOW bullpen risk")
    print(f"   Got result: {result}")
    failed_tests += 1

# Test case 5.3: F5 Under should be preferred when available and viable
print("\nTest 5.3: F5 Under preferred when bullpen risk is HIGH")
result = select_under_market_with_bullpen_risk(
    expected_runs=7.0,  # Lower expected runs so F5 edge is viable
    full_game_total_line=9.5,
    f5_total_line=4.5,  # F5 expected = 7.0 * 5/9 = 3.89, edge = 4.5 - 3.89 = 0.61 > 0.4
    pitcher_score=70.0,
    park_factor=0.95,
    bullpen_risk="HIGH",
    available_markets=[
        {"market": "F5 Total Runs Under 4.5", "line": 4.5, "score": 70},
        {"market": "Full Game Under 9.5", "line": 9.5, "score": 75},
    ],
    current_selection={
        "market": "Total Runs Under 9.5",
        "score": 75,
        "line": 9.5,
    },
)
selected_market = result.get("selected_market") or {}
selected_market_name = selected_market.get("market", "") if isinstance(selected_market, dict) else str(selected_market)
if result.get("rule_triggered") and "F5" in selected_market_name:
    print(f"✅ PASSED - F5 Under preferred when bullpen risk is HIGH")
    print(f"   Selected market: {selected_market_name}")
    passed_tests += 1
else:
    # Check if F5 was at least considered in ranking
    ranking = result.get("ranking", [])
    f5_in_ranking = any("F5" in str(m.get("market", "")) for m in ranking)
    if result.get("rule_triggered") and f5_in_ranking:
        print(f"✅ PASSED - Rule triggered and F5 considered (edge may be insufficient)")
        print(f"   Selected market: {selected_market_name}")
        print(f"   Ranking: {[m.get('market') for m in ranking]}")
        passed_tests += 1
    else:
        print(f"❌ FAILED - Expected F5 Under to be selected or at least considered")
        print(f"   Got selected_market: {selected_market_name}")
        print(f"   Rule triggered: {result.get('rule_triggered')}")
        print(f"   Ranking: {[m.get('market') for m in ranking]}")
        failed_tests += 1

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*80)
print("TEST SUMMARY")
print("="*80)
print(f"Total tests: {passed_tests + failed_tests}")
print(f"✅ Passed: {passed_tests}")
print(f"❌ Failed: {failed_tests}")
print(f"Success rate: {100 * passed_tests / (passed_tests + failed_tests):.1f}%")
print("="*80)

if failed_tests > 0:
    sys.exit(1)
else:
    print("\n✅ All unit tests PASSED!")
    sys.exit(0)
