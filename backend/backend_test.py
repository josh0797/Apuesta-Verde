"""Backend validation for MLB Pregame Engine GAP #0-#6 reinforcement.

Tests the 7 critical bug fixes WITHOUT requiring external API calls.
Treats empty StatsAPI responses as expected sandbox behavior.
"""
import sys
import logging
from datetime import datetime, timezone

# Suppress noisy logs during test
logging.basicConfig(level=logging.WARNING)

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

class MLBEngineValidator:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name: str, condition: bool, details: str = ""):
        """Run a single test assertion."""
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        if condition:
            self.tests_passed += 1
            print(f"✅ PASS - {details or name}")
            return True
        else:
            self.tests_failed += 1
            msg = f"❌ FAIL - {name}: {details}"
            print(msg)
            self.failures.append(msg)
            return False

    def section(self, title: str):
        """Print a section header."""
        print(f"\n{'='*70}")
        print(f"  {title}")
        print(f"{'='*70}")

    def summary(self):
        """Print final summary."""
        print(f"\n{'='*70}")
        print(f"  TEST SUMMARY")
        print(f"{'='*70}")
        print(f"Total:  {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_failed}")
        if self.failures:
            print(f"\n❌ FAILURES:")
            for f in self.failures:
                print(f"  • {f}")
        print(f"{'='*70}\n")
        return self.tests_failed == 0


def test_module_imports():
    """GAP sanity: importing the touched modules must not raise."""
    v = MLBEngineValidator()
    v.section("MODULE IMPORTS")
    
    try:
        from services import mlb_day_orchestrator
        v.test("mlb_day_orchestrator import", True, "Module imported successfully")
    except Exception as e:
        v.test("mlb_day_orchestrator import", False, f"Import failed: {e}")
        return v
    
    try:
        from services import mlb_stats_api
        v.test("mlb_stats_api import", True, "Module imported successfully")
    except Exception as e:
        v.test("mlb_stats_api import", False, f"Import failed: {e}")
        return v
    
    try:
        from services import mlb_pregame_analytics
        v.test("mlb_pregame_analytics import", True, "Module imported successfully")
    except Exception as e:
        v.test("mlb_pregame_analytics import", False, f"Import failed: {e}")
        return v
    
    try:
        from services import signal_catalog
        v.test("signal_catalog import", True, "Module imported successfully")
    except Exception as e:
        v.test("signal_catalog import", False, f"Import failed: {e}")
        return v
    
    try:
        from services import time_filter
        v.test("time_filter import", True, "Module imported successfully")
    except Exception as e:
        v.test("time_filter import", False, f"Import failed: {e}")
        return v
    
    try:
        import server
        v.test("server.py import", True, "FastAPI app imported successfully")
    except Exception as e:
        v.test("server.py import", False, f"Import failed: {e}")
        return v
    
    return v


def test_gap0_normalize_time_fields():
    """GAP #0: normalize_mlb_time_fields() must populate kickoff_iso from gameDate."""
    v = MLBEngineValidator()
    v.section("GAP #0: normalize_mlb_time_fields()")
    
    try:
        from services.mlb_day_orchestrator import normalize_mlb_time_fields
        
        # Test 1: gameDate only → should populate kickoff_iso
        game1 = {"gameDate": "2025-05-30T19:10:00Z", "gamePk": 12345}
        result1 = normalize_mlb_time_fields(game1)
        v.test(
            "gameDate only → kickoff_iso populated",
            result1.get("kickoff_iso") == "2025-05-30T19:10:00Z",
            f"kickoff_iso={result1.get('kickoff_iso')}"
        )
        
        # Test 2: kickoff_iso only → should populate gameDate
        game2 = {"kickoff_iso": "2025-05-30T20:00:00Z", "gamePk": 67890}
        result2 = normalize_mlb_time_fields(game2)
        v.test(
            "kickoff_iso only → gameDate populated",
            result2.get("gameDate") == "2025-05-30T20:00:00Z",
            f"gameDate={result2.get('gameDate')}"
        )
        
        # Test 3: neither field → should log warning but not raise
        game3 = {"gamePk": 99999}
        try:
            result3 = normalize_mlb_time_fields(game3)
            v.test(
                "neither field → no exception raised",
                True,
                "Function returned without raising"
            )
            v.test(
                "neither field → dict returned untouched",
                result3.get("gamePk") == 99999 and "gameDate" not in result3,
                f"result={result3}"
            )
        except Exception as e:
            v.test("neither field → no exception raised", False, f"Raised: {e}")
        
        # Test 4: both fields present → should preserve both
        game4 = {"gameDate": "2025-05-30T19:10:00Z", "kickoff_iso": "2025-05-30T19:10:00Z"}
        result4 = normalize_mlb_time_fields(game4)
        v.test(
            "both fields present → both preserved",
            result4.get("gameDate") == result4.get("kickoff_iso"),
            f"gameDate={result4.get('gameDate')}, kickoff_iso={result4.get('kickoff_iso')}"
        )
        
    except Exception as e:
        v.test("GAP #0 execution", False, f"Exception: {e}")
    
    return v


def test_gap1_pitcher_confirmation():
    """GAP #1: _confirm_pitchers_statsapi() must discard games with only one pitcher."""
    v = MLBEngineValidator()
    v.section("GAP #1: Pitcher Confirmation (Both Required)")
    
    try:
        # We can't easily test the private _confirm_pitchers_statsapi without mocking,
        # but we can verify the logic is present in the code
        from services import mlb_day_orchestrator
        import inspect
        
        source = inspect.getsource(mlb_day_orchestrator._confirm_pitchers_statsapi)
        
        v.test(
            "Code checks for both home_probable_id and away_probable_id",
            "home_probable_id" in source and "away_probable_id" in source,
            "Both pitcher IDs are referenced in the function"
        )
        
        v.test(
            "Code discards games with missing pitchers",
            "if not home_pid or not away_pid:" in source or "not home_pid or not away_pid" in source,
            "Conditional check for both pitchers present"
        )
        
        v.test(
            "Code logs discard reason",
            "descartado" in source or "discard" in source.lower(),
            "Discard logging present"
        )
        
    except Exception as e:
        v.test("GAP #1 code inspection", False, f"Exception: {e}")
    
    return v


def test_gap2_fallback_mechanism():
    """GAP #2: analyze_mlb_day() must attempt mlb.com fallback and set abort_reason."""
    v = MLBEngineValidator()
    v.section("GAP #2: Fallback Mechanism & Abort Reason")
    
    try:
        from services import mlb_day_orchestrator
        import inspect
        
        source = inspect.getsource(mlb_day_orchestrator.analyze_mlb_day)
        
        v.test(
            "Code attempts mlb.com fallback",
            "mlb.com" in source and "fallback" in source.lower(),
            "Fallback mechanism present"
        )
        
        v.test(
            "Code sets abort_reason on failure",
            "abort_reason" in source and "no_probable_pitchers_all_sources" in source,
            "Abort reason set when both sources fail"
        )
        
        v.test(
            "Code emits PITCHER_NOT_CONFIRMED signal",
            "PITCHER_NOT_CONFIRMED" in source and "pipeline_signals" in source,
            "Signal emission present"
        )
        
        v.test(
            "Code checks for empty confirmed_by_pk",
            "if not confirmed_by_pk:" in source,
            "Empty pitcher check present"
        )
        
    except Exception as e:
        v.test("GAP #2 code inspection", False, f"Exception: {e}")
    
    return v


def test_gap3_il_data_safety():
    """GAP #3: get_team_il_players() must never raise, return [] on failure."""
    v = MLBEngineValidator()
    v.section("GAP #3: IL Data Fetching Safety")
    
    try:
        from services.mlb_stats_api import get_team_il_players
        import inspect
        
        source = inspect.getsource(get_team_il_players)
        
        v.test(
            "Function returns [] on missing team_id",
            "if not team_id:" in source and "return []" in source,
            "Early return [] for missing team_id"
        )
        
        v.test(
            "Function has try-except with [] fallback",
            "except" in source and "return []" in source,
            "Exception handling returns []"
        )
        
        v.test(
            "Function never raises (no 'raise' statements)",
            "raise" not in source or source.count("raise") == source.count("raise_for_status"),
            "No explicit raise statements (except HTTP)"
        )
        
        # Test actual execution with invalid team_id
        import asyncio
        
        async def test_invalid_team():
            # Mock db object
            class MockDB:
                class MockCollection:
                    async def find_one(self, *args, **kwargs):
                        return None
                    async def update_one(self, *args, **kwargs):
                        pass
                mlb_cache = MockCollection()
            
            result = await get_team_il_players(MockDB(), None)
            return result
        
        result = asyncio.run(test_invalid_team())
        v.test(
            "Function returns [] for None team_id (runtime test)",
            result == [],
            f"Returned: {result}"
        )
        
    except Exception as e:
        v.test("GAP #3 execution", False, f"Exception: {e}")
    
    return v


def test_gap4_gap5_fragility_il_penalties():
    """GAP #4 & #5: mlb_fragility_score() must apply additive IL penalties."""
    v = MLBEngineValidator()
    v.section("GAP #4 & #5: Fragility Score IL Penalties (Additive)")
    
    try:
        from services.mlb_pregame_analytics import mlb_fragility_score
        
        # Test 1: No IL players → base score
        ctx1 = {
            "home_il_count": 0,
            "away_il_count": 0,
            "bullpen": {"tags": []},
            "home_pitcher_quality": {"score": 70, "tags": []},
            "away_pitcher_quality": {"score": 70, "tags": []},
            "park": {"park_runs_mult": 1.0},
        }
        result1 = mlb_fragility_score(ctx1)
        base_score = result1.get("score", 0)
        v.test(
            "No IL players → base fragility score",
            isinstance(base_score, int) and base_score >= 0,
            f"Base score={base_score}"
        )
        v.test(
            "Output contains 'tags' field",
            "tags" in result1,
            f"tags={result1.get('tags')}"
        )
        v.test(
            "Output contains 'il_home' field",
            "il_home" in result1,
            f"il_home={result1.get('il_home')}"
        )
        v.test(
            "Output contains 'il_away' field",
            "il_away" in result1,
            f"il_away={result1.get('il_away')}"
        )
        
        # Test 2: 3 IL players → +10 penalty, IL_DEPTH_RISK_3PLUS tag
        ctx2 = {**ctx1, "home_il_count": 3, "away_il_count": 0}
        result2 = mlb_fragility_score(ctx2)
        score2 = result2.get("score", 0)
        tags2 = result2.get("tags", [])
        v.test(
            "3 IL players → +10 penalty applied",
            score2 >= base_score + 10,
            f"score={score2} (base={base_score}, expected≥{base_score+10})"
        )
        v.test(
            "3 IL players → IL_DEPTH_RISK_3PLUS tag present",
            "IL_DEPTH_RISK_3PLUS" in tags2,
            f"tags={tags2}"
        )
        
        # Test 3: 5 IL players → +18 cumulative penalty (10+8), both tags
        ctx3 = {**ctx1, "home_il_count": 5, "away_il_count": 0}
        result3 = mlb_fragility_score(ctx3)
        score3 = result3.get("score", 0)
        tags3 = result3.get("tags", [])
        v.test(
            "5 IL players → +18 cumulative penalty (10+8)",
            score3 >= base_score + 18,
            f"score={score3} (base={base_score}, expected≥{base_score+18})"
        )
        v.test(
            "5 IL players → IL_DEPTH_RISK_3PLUS tag present",
            "IL_DEPTH_RISK_3PLUS" in tags3,
            f"tags={tags3}"
        )
        v.test(
            "5 IL players → IL_DEPTH_RISK_5PLUS tag present",
            "IL_DEPTH_RISK_5PLUS" in tags3,
            f"tags={tags3}"
        )
        v.test(
            "5 IL players → both IL tags present (additive)",
            "IL_DEPTH_RISK_3PLUS" in tags3 and "IL_DEPTH_RISK_5PLUS" in tags3,
            f"tags={tags3}"
        )
        
        # Test 4: Verify additive behavior (5 IL should be more fragile than 3 IL)
        v.test(
            "Additive behavior: 5 IL score > 3 IL score",
            score3 > score2,
            f"5 IL score={score3}, 3 IL score={score2}"
        )
        
    except Exception as e:
        v.test("GAP #4 & #5 execution", False, f"Exception: {e}")
    
    return v


def test_gap6_signal_catalog():
    """GAP #6: Signal catalog must have baseball-only signals that return None for football."""
    v = MLBEngineValidator()
    v.section("GAP #6: Signal Catalog Baseball-Only Enforcement")
    
    try:
        from services.signal_catalog import make_signal, SIGNAL_CATALOG, BASEBALL_ONLY
        
        # Test 1: PITCHER_NOT_CONFIRMED exists and is baseball-only
        v.test(
            "PITCHER_NOT_CONFIRMED signal exists",
            "PITCHER_NOT_CONFIRMED" in SIGNAL_CATALOG,
            "Signal present in catalog"
        )
        entry1 = SIGNAL_CATALOG.get("PITCHER_NOT_CONFIRMED", {})
        v.test(
            "PITCHER_NOT_CONFIRMED is baseball-only",
            entry1.get("applicable_sports") == BASEBALL_ONLY,
            f"applicable_sports={entry1.get('applicable_sports')}"
        )
        
        # Test 2: MLB_COM_FALLBACK_USED exists and is baseball-only
        v.test(
            "MLB_COM_FALLBACK_USED signal exists",
            "MLB_COM_FALLBACK_USED" in SIGNAL_CATALOG,
            "Signal present in catalog"
        )
        entry2 = SIGNAL_CATALOG.get("MLB_COM_FALLBACK_USED", {})
        v.test(
            "MLB_COM_FALLBACK_USED is baseball-only",
            entry2.get("applicable_sports") == BASEBALL_ONLY,
            f"applicable_sports={entry2.get('applicable_sports')}"
        )
        
        # Test 3: IL_DEPTH_RISK exists and is baseball-only
        v.test(
            "IL_DEPTH_RISK signal exists",
            "IL_DEPTH_RISK" in SIGNAL_CATALOG,
            "Signal present in catalog"
        )
        entry3 = SIGNAL_CATALOG.get("IL_DEPTH_RISK", {})
        v.test(
            "IL_DEPTH_RISK is baseball-only",
            entry3.get("applicable_sports") == BASEBALL_ONLY,
            f"applicable_sports={entry3.get('applicable_sports')}"
        )
        
        # Test 4: make_signal() returns None for football
        sig_football = make_signal("PITCHER_NOT_CONFIRMED", sport="football")
        v.test(
            "make_signal('PITCHER_NOT_CONFIRMED', sport='football') returns None",
            sig_football is None,
            f"Returned: {sig_football}"
        )
        
        # Test 5: make_signal() returns dict for baseball
        sig_baseball = make_signal("PITCHER_NOT_CONFIRMED", sport="baseball")
        v.test(
            "make_signal('PITCHER_NOT_CONFIRMED', sport='baseball') returns dict",
            isinstance(sig_baseball, dict) and sig_baseball.get("code") == "PITCHER_NOT_CONFIRMED",
            f"Returned: {sig_baseball}"
        )
        
        # Test 6: All three signals return None for football
        for code in ["PITCHER_NOT_CONFIRMED", "MLB_COM_FALLBACK_USED", "IL_DEPTH_RISK"]:
            sig = make_signal(code, sport="football")
            v.test(
                f"make_signal('{code}', sport='football') returns None",
                sig is None,
                f"Cross-sport guard working for {code}"
            )
        
    except Exception as e:
        v.test("GAP #6 execution", False, f"Exception: {e}")
    
    return v


def test_endpoint_regression():
    """Verify existing endpoints still respond (no regression)."""
    v = MLBEngineValidator()
    v.section("ENDPOINT REGRESSION CHECKS")
    
    import requests
    
    try:
        # Test 1: Health endpoint
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        v.test(
            "GET /api/ (health)",
            r.status_code == 200,
            f"Status: {r.status_code}"
        )
        
        # Test 2: MLB day endpoint (may return empty or abort_reason - both are valid)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = requests.get(f"{BASE_URL}/api/mlb/day?date={today}", timeout=30)
        v.test(
            "GET /api/mlb/day (endpoint responds)",
            r.status_code in [200, 401, 403],  # 401/403 if auth required
            f"Status: {r.status_code}"
        )
        
        if r.status_code == 200:
            data = r.json()
            # Check for abort_reason - this is VALID in sandbox
            if data.get("pipeline_meta", {}).get("abort_reason") == "no_probable_pitchers_all_sources":
                v.test(
                    "MLB day returns abort_reason (expected in sandbox)",
                    True,
                    "abort_reason=no_probable_pitchers_all_sources (PASS)"
                )
                # Check for PITCHER_NOT_CONFIRMED signal
                signals = data.get("pipeline_meta", {}).get("pipeline_signals", [])
                has_signal = any(s.get("code") == "PITCHER_NOT_CONFIRMED" for s in signals)
                v.test(
                    "MLB day includes PITCHER_NOT_CONFIRMED signal",
                    has_signal,
                    f"pipeline_signals={[s.get('code') for s in signals]}"
                )
            else:
                v.test(
                    "MLB day returns valid structure",
                    "picks" in data or "pipeline_meta" in data,
                    f"Keys: {list(data.keys())}"
                )
        
    except Exception as e:
        v.test("Endpoint regression", False, f"Exception: {e}")
    
    return v


def main():
    """Run all validation tests."""
    print(f"\n{'='*70}")
    print(f"  MLB PREGAME ENGINE GAP #0-#6 VALIDATION")
    print(f"  Backend-only testing (no external API calls required)")
    print(f"{'='*70}\n")
    
    all_validators = []
    
    # Run all test suites
    all_validators.append(test_module_imports())
    all_validators.append(test_gap0_normalize_time_fields())
    all_validators.append(test_gap1_pitcher_confirmation())
    all_validators.append(test_gap2_fallback_mechanism())
    all_validators.append(test_gap3_il_data_safety())
    all_validators.append(test_gap4_gap5_fragility_il_penalties())
    all_validators.append(test_gap6_signal_catalog())
    all_validators.append(test_endpoint_regression())
    
    # Aggregate results
    total_tests = sum(v.tests_run for v in all_validators)
    total_passed = sum(v.tests_passed for v in all_validators)
    total_failed = sum(v.tests_failed for v in all_validators)
    
    print(f"\n{'='*70}")
    print(f"  FINAL SUMMARY")
    print(f"{'='*70}")
    print(f"Total Tests:  {total_tests}")
    print(f"Passed:       {total_passed}")
    print(f"Failed:       {total_failed}")
    print(f"Success Rate: {(total_passed/total_tests*100):.1f}%")
    
    if total_failed > 0:
        print(f"\n❌ FAILURES DETECTED:")
        for v in all_validators:
            for f in v.failures:
                print(f"  • {f}")
    else:
        print(f"\n✅ ALL TESTS PASSED!")
    
    print(f"{'='*70}\n")
    
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
