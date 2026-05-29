"""Comprehensive test for MLB Pre-game Analytics Engine (Phase MLB-1).

Tests all 9 pure scoring functions + orchestrator + endpoint + signal catalog.

Test Coverage:
1. Unit tests for pitcher_quality_score (PITCHER_OVERPERFORMING/UNDERVALUED)
2. Unit tests for bullpen_fatigue_score (8+/10+ IP guardrails)
3. Unit tests for park_factor_analyzer (weather + PARK_OVER/UNDER_SIGNAL)
4. Unit tests for run_line_predictor (RUN_LINE_TRAP guardrail)
5. Unit tests for over_under_predictor (expected_runs model)
6. Unit tests for nrfi_yrfi_analyzer (1st-inning specific stats)
7. Unit tests for mlb_fragility_score (0-100 with labels)
8. Unit tests for emit_signals (source_url transparency)
9. Integration test for GET /api/mlb/day endpoint
10. Signal catalog sport-aware validation (BASEBALL_ONLY codes)
11. Auth test (demo@valuebet.app / demo1234)
"""
import sys
import requests
from datetime import datetime

# Import the pure functions directly for unit testing
sys.path.insert(0, '/app/backend')
from services.mlb_pregame_analytics import (
    pitcher_quality_score,
    bullpen_fatigue_score,
    park_factor_analyzer,
    run_line_predictor,
    over_under_predictor,
    nrfi_yrfi_analyzer,
    mlb_fragility_score,
    emit_signals,
    PARK_FACTORS,
)
from services.signal_catalog import make_signal

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"


class MLBAnalyticsTester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def assert_test(self, name: str, condition: bool, error_msg: str = ""):
        """Assert a condition and track test result."""
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            self.log(f"✅ PASSED: {name}", "SUCCESS")
            return True
        else:
            self.tests_failed += 1
            self.log(f"❌ FAILED: {name}", "ERROR")
            if error_msg:
                self.log(f"   {error_msg}", "ERROR")
            self.failures.append({"test": name, "reason": error_msg})
            return False

    def run_all_tests(self):
        """Execute all MLB analytics tests."""
        self.log("=" * 80)
        self.log("MLB PRE-GAME ANALYTICS ENGINE TESTING")
        self.log("=" * 80)

        # ═══════════════════════════════════════════════════════════════════════
        # 1. AUTH TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[1] AUTH TEST", "SECTION")
        try:
            resp = requests.post(
                f"{BASE_URL}/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            if resp.status_code == 200:
                result = resp.json()
                self.token = result.get("token")
                self.assert_test(
                    "Login with demo@valuebet.app",
                    self.token is not None,
                    f"No token in response: {result}"
                )
                self.log(f"   Token acquired: {self.token[:20]}...")
            else:
                self.assert_test(
                    "Login with demo@valuebet.app",
                    False,
                    f"Status {resp.status_code}: {resp.text[:200]}"
                )
        except Exception as e:
            self.assert_test("Login with demo@valuebet.app", False, str(e))

        # ═══════════════════════════════════════════════════════════════════════
        # 2. UNIT TEST: pitcher_quality_score
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[2] UNIT TEST: pitcher_quality_score", "SECTION")

        # Test 2.1: PITCHER_OVERPERFORMING (ERA=2.80, xERA=4.50)
        pitcher_overperforming = {
            "era": 2.80,
            "xera": 4.50,  # xERA - ERA = 1.70 >= 1.20 → OVERPERFORMING
            "fip": 3.80,
            "whip": 1.15,
            "k9": 9.2,
            "bb9": 2.8,
        }
        result = pitcher_quality_score(pitcher_overperforming)
        self.assert_test(
            "pitcher_quality_score: PITCHER_OVERPERFORMING tag",
            "PITCHER_OVERPERFORMING" in result.get("tags", []),
            f"Tags: {result.get('tags')}, Score: {result.get('score')}"
        )
        self.assert_test(
            "pitcher_quality_score: OVERPERFORMING reduces score",
            result.get("score", 100) < 70,
            f"Score should be reduced, got {result.get('score')}"
        )

        # Test 2.2: PITCHER_UNDERVALUED (ERA=4.60, xERA=3.20)
        pitcher_undervalued = {
            "era": 4.60,
            "xera": 3.20,  # ERA - xERA = 1.40 >= 1.20 → UNDERVALUED
            "fip": 3.50,
            "whip": 1.25,
            "k9": 8.5,
            "bb9": 3.0,
        }
        result = pitcher_quality_score(pitcher_undervalued)
        self.assert_test(
            "pitcher_quality_score: PITCHER_UNDERVALUED tag",
            "PITCHER_UNDERVALUED" in result.get("tags", []),
            f"Tags: {result.get('tags')}, Score: {result.get('score')}"
        )
        self.assert_test(
            "pitcher_quality_score: UNDERVALUED boosts score",
            result.get("score", 0) > 50,
            f"Score should be boosted, got {result.get('score')}"
        )

        # Test 2.3: Normal pitcher (no regression)
        pitcher_normal = {
            "era": 3.80,
            "xera": 3.90,
            "fip": 3.85,
            "whip": 1.28,
            "k9": 8.7,
            "bb9": 3.2,
        }
        result = pitcher_quality_score(pitcher_normal)
        self.assert_test(
            "pitcher_quality_score: Normal pitcher has no regression tags",
            "PITCHER_OVERPERFORMING" not in result.get("tags", []) and
            "PITCHER_UNDERVALUED" not in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 3. UNIT TEST: bullpen_fatigue_score
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[3] UNIT TEST: bullpen_fatigue_score", "SECTION")

        # Test 3.1: High fatigue (innings_last_48h=10)
        usage_high_fatigue = {
            "innings_last_48h": 10.0,
            "innings_last_3d": 14.0,
            "bullpen_era_7d": 4.20,
        }
        result = bullpen_fatigue_score(usage_high_fatigue)
        self.assert_test(
            "bullpen_fatigue_score: 10+ IP triggers BULLPEN_FATIGUE tag",
            "BULLPEN_FATIGUE" in result.get("tags", []),
            f"Tags: {result.get('tags')}, Score: {result.get('score')}"
        )
        self.assert_test(
            "bullpen_fatigue_score: 10+ IP caps score at 40",
            result.get("score", 100) <= 40,
            f"Score should be ≤40, got {result.get('score')}"
        )

        # Test 3.2: High ERA (bullpen_era_7d=5.10)
        usage_high_era = {
            "innings_last_48h": 6.0,
            "bullpen_era_7d": 5.10,  # >= 4.75 → BULLPEN_FATIGUE
        }
        result = bullpen_fatigue_score(usage_high_era)
        self.assert_test(
            "bullpen_fatigue_score: ERA 7d >= 4.75 triggers BULLPEN_FATIGUE",
            "BULLPEN_FATIGUE" in result.get("tags", []),
            f"Tags: {result.get('tags')}, ERA: {usage_high_era['bullpen_era_7d']}"
        )

        # Test 3.3: Fresh bullpen
        usage_fresh = {
            "innings_last_48h": 3.0,
            "innings_last_3d": 5.0,
            "bullpen_era_7d": 3.20,
            "save_conversion_pct": 0.85,
        }
        result = bullpen_fatigue_score(usage_fresh)
        self.assert_test(
            "bullpen_fatigue_score: Fresh bullpen has no BULLPEN_FATIGUE tag",
            "BULLPEN_FATIGUE" not in result.get("tags", []),
            f"Tags: {result.get('tags')}, Score: {result.get('score')}"
        )
        self.assert_test(
            "bullpen_fatigue_score: Fresh bullpen has high score",
            result.get("score", 0) >= 70,
            f"Score should be ≥70, got {result.get('score')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 4. UNIT TEST: park_factor_analyzer
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[4] UNIT TEST: park_factor_analyzer", "SECTION")

        # Test 4.1: Coors Field + hot weather + wind out
        weather_coors_hot = {
            "temperature_f": 85,
            "wind_mph": 15,
            "wind_direction": "out_to_left",
        }
        result = park_factor_analyzer("Coors Field", weather_coors_hot)
        self.assert_test(
            "park_factor_analyzer: Coors + hot + wind out → PARK_OVER_SIGNAL",
            "PARK_OVER_SIGNAL" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "park_factor_analyzer: Coors + hot + wind out → HOT_WEATHER_OVER",
            "HOT_WEATHER_OVER" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "park_factor_analyzer: Coors + hot + wind out → WIND_OUT_OVER",
            "WIND_OUT_OVER" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "park_factor_analyzer: Coors park_runs_mult = 1.15",
            result.get("park_runs_mult") == 1.15,
            f"Expected 1.15, got {result.get('park_runs_mult')}"
        )

        # Test 4.2: Oracle Park + wind in
        weather_oracle_wind_in = {
            "temperature_f": 62,
            "wind_mph": 12,
            "wind_direction": "in_from_cf",
        }
        result = park_factor_analyzer("Oracle Park", weather_oracle_wind_in)
        self.assert_test(
            "park_factor_analyzer: Oracle + wind in → PARK_UNDER_SIGNAL",
            "PARK_UNDER_SIGNAL" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "park_factor_analyzer: Oracle + wind in → WIND_IN_UNDER",
            "WIND_IN_UNDER" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "park_factor_analyzer: Oracle park_runs_mult = 0.92",
            result.get("park_runs_mult") == 0.92,
            f"Expected 0.92, got {result.get('park_runs_mult')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 5. UNIT TEST: run_line_predictor
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[5] UNIT TEST: run_line_predictor", "SECTION")

        # Test 5.1: RUN_LINE_TRAP (bullpen ERA 7d > 4.75, IP 48h > 8, 1-run win% > 40%)
        ctx_trap = {
            "pitcher_edge": {"score": 65},
            "bullpen": {"score": 50},
            "offense_home": {"score": 55},
            "offense_away": {"score": 52},
            "park": {"park_runs_mult": 1.0},
            "favorite_bullpen_era_7d": 5.20,  # > 4.75
            "favorite_bullpen_ip_48h": 10.0,  # > 8
            "favorite_one_run_win_pct": 0.45,  # > 0.40
        }
        result = run_line_predictor(ctx_trap)
        self.assert_test(
            "run_line_predictor: RUN_LINE_TRAP tag when all conditions met",
            "RUN_LINE_TRAP" in result.get("tags", []),
            f"Tags: {result.get('tags')}, Score: {result.get('score')}"
        )
        self.assert_test(
            "run_line_predictor: RUN_LINE_TRAP caps score at 45",
            result.get("score", 100) <= 45,
            f"Score should be ≤45, got {result.get('score')}"
        )

        # Test 5.2: No trap (normal conditions)
        ctx_no_trap = {
            "pitcher_edge": {"score": 70},
            "bullpen": {"score": 75},
            "offense_home": {"score": 60},
            "offense_away": {"score": 55},
            "park": {"park_runs_mult": 1.0},
            "favorite_bullpen_era_7d": 3.80,
            "favorite_bullpen_ip_48h": 5.0,
            "favorite_one_run_win_pct": 0.30,
        }
        result = run_line_predictor(ctx_no_trap)
        self.assert_test(
            "run_line_predictor: No RUN_LINE_TRAP when conditions not met",
            "RUN_LINE_TRAP" not in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 6. UNIT TEST: over_under_predictor
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[6] UNIT TEST: over_under_predictor", "SECTION")

        # Test 6.1: Dual elite pitchers + Oracle Park → UNDER
        ctx_under = {
            "home_pitcher_quality": {"score": 80},
            "away_pitcher_quality": {"score": 78},
            "bullpen": {"score": 70},
            "offense_home": {"score": 50},
            "offense_away": {"score": 48},
            "park": {"park_runs_mult": 0.92, "weather_score": 45},
        }
        result = over_under_predictor(ctx_under, book_line=8.5)
        self.assert_test(
            "over_under_predictor: Elite pitchers + Oracle → UNDER verdict",
            result.get("verdict") == "UNDER",
            f"Verdict: {result.get('verdict')}, Expected runs: {result.get('expected_runs')}"
        )
        self.assert_test(
            "over_under_predictor: UNDER verdict → UNDER_VALUE tag",
            "UNDER_VALUE" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )
        self.assert_test(
            "over_under_predictor: UNDER verdict → score >= 65",
            result.get("score", 0) >= 65,
            f"Score: {result.get('score')}"
        )

        # Test 6.2: Weak pitchers + Coors → OVER
        ctx_over = {
            "home_pitcher_quality": {"score": 35},
            "away_pitcher_quality": {"score": 40},
            "bullpen": {"score": 45},
            "offense_home": {"score": 70},
            "offense_away": {"score": 68},
            "park": {"park_runs_mult": 1.15, "weather_score": 65},
        }
        result = over_under_predictor(ctx_over, book_line=8.5)
        self.assert_test(
            "over_under_predictor: Weak pitchers + Coors → OVER verdict",
            result.get("verdict") == "OVER",
            f"Verdict: {result.get('verdict')}, Expected runs: {result.get('expected_runs')}"
        )
        self.assert_test(
            "over_under_predictor: OVER verdict → OVER_VALUE tag",
            "OVER_VALUE" in result.get("tags", []),
            f"Tags: {result.get('tags')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 7. UNIT TEST: nrfi_yrfi_analyzer
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[7] UNIT TEST: nrfi_yrfi_analyzer", "SECTION")

        # Test 7.1: NRFI scenario (sharp pitchers + weak top3 lineup)
        ctx_nrfi = {
            "home_pitcher_stats": {
                "first_pitch_strike_pct": 0.65,
                "first_inning_era": 2.5,
                "first_inning_whip": 1.10,
            },
            "away_pitcher_stats": {
                "first_pitch_strike_pct": 0.63,
                "first_inning_era": 2.8,
                "first_inning_whip": 1.15,
            },
            "home_team": {
                "top3_lineup": {"obp": 0.290, "slg": 0.380, "k_rate": 0.25},
                "nrfi_rate_10g": 0.60,
            },
            "away_team": {
                "top3_lineup": {"obp": 0.295, "slg": 0.390, "k_rate": 0.24},
                "nrfi_rate_10g": 0.58,
            },
            "park": {"park_runs_mult": 0.95},
        }
        result = nrfi_yrfi_analyzer(ctx_nrfi)
        self.assert_test(
            "nrfi_yrfi_analyzer: Sharp pitchers + weak lineup → nrfi_score > 50",
            result.get("nrfi_score", 0) > 50,
            f"NRFI score: {result.get('nrfi_score')}, YRFI score: {result.get('yrfi_score')}"
        )
        self.assert_test(
            "nrfi_yrfi_analyzer: yrfi_score = 100 - nrfi_score + 8",
            abs(result.get("yrfi_score", 0) - (100 - result.get("nrfi_score", 0) + 8)) <= 2,
            f"NRFI: {result.get('nrfi_score')}, YRFI: {result.get('yrfi_score')}"
        )

        # Test 7.2: NRFI_SIGNAL tag when nrfi_score >= 72
        ctx_strong_nrfi = {
            "home_pitcher_stats": {
                "first_pitch_strike_pct": 0.70,
                "first_inning_era": 2.0,
                "first_inning_whip": 1.00,
            },
            "away_pitcher_stats": {
                "first_pitch_strike_pct": 0.68,
                "first_inning_era": 2.2,
                "first_inning_whip": 1.05,
            },
            "home_team": {
                "top3_lineup": {"obp": 0.280, "slg": 0.360, "k_rate": 0.28},
                "nrfi_rate_10g": 0.65,
            },
            "away_team": {
                "top3_lineup": {"obp": 0.285, "slg": 0.370, "k_rate": 0.27},
                "nrfi_rate_10g": 0.62,
            },
            "park": {"park_runs_mult": 0.92},
        }
        result = nrfi_yrfi_analyzer(ctx_strong_nrfi)
        if result.get("nrfi_score", 0) >= 72:
            self.assert_test(
                "nrfi_yrfi_analyzer: nrfi_score >= 72 → NRFI_SIGNAL tag",
                "NRFI_SIGNAL" in result.get("tags", []),
                f"Tags: {result.get('tags')}, NRFI score: {result.get('nrfi_score')}"
            )

        # ═══════════════════════════════════════════════════════════════════════
        # 8. UNIT TEST: mlb_fragility_score
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[8] UNIT TEST: mlb_fragility_score", "SECTION")

        # Test 8.1: High fragility (BULLPEN_FATIGUE + extreme_weather + low pitcher quality)
        ctx_fragile = {
            "bullpen": {"tags": ["BULLPEN_FATIGUE"], "score": 35},
            "home_pitcher_quality": {"tags": ["PITCHER_OVERPERFORMING"], "score": 30},
            "away_pitcher_quality": {"tags": [], "score": 45},
            "park": {"park_runs_mult": 1.12},
            "extreme_weather": True,
            "inexperienced_pitcher": True,
        }
        result = mlb_fragility_score(ctx_fragile)
        self.assert_test(
            "mlb_fragility_score: High fragility → score >= 60",
            result.get("score", 0) >= 60,
            f"Score: {result.get('score')}, Label: {result.get('label')}"
        )
        self.assert_test(
            "mlb_fragility_score: High fragility → label = FRAGIL",
            result.get("label") == "FRAGIL",
            f"Label: {result.get('label')}"
        )

        # Test 8.2: Low fragility (empty context)
        ctx_protected = {}
        result = mlb_fragility_score(ctx_protected)
        self.assert_test(
            "mlb_fragility_score: Empty context → label = MUY_PROTEGIDO",
            result.get("label") == "MUY_PROTEGIDO",
            f"Label: {result.get('label')}, Score: {result.get('score')}"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 9. UNIT TEST: emit_signals (source_url transparency)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[9] UNIT TEST: emit_signals (source_url transparency)", "SECTION")

        # Test 9.1: Signals have source_url when provided
        ctx_signals = {}
        parts_signals = {
            "home_pitcher_quality": {"tags": ["PITCHER_OVERPERFORMING"]},
            "away_pitcher_quality": {"tags": ["PITCHER_UNDERVALUED"]},
            "bullpen": {"tags": ["BULLPEN_FATIGUE"]},
            "park": {"tags": ["PARK_OVER_SIGNAL"]},
            "run_line": {"tags": ["RUN_LINE_TRAP"]},
            "nrfi": {"tags": ["NRFI_SIGNAL"]},
            "pitcher_edge": {"edge_type": "STRONG"},
        }
        source_url = "https://statsapi.mlb.com/api/v1/schedule?date=2025-08-15&sportId=1&hydrate=probablePitcher"
        signals = emit_signals(ctx_signals, parts_signals, source_url=source_url)
        
        self.assert_test(
            "emit_signals: Returns list of signals",
            isinstance(signals, list) and len(signals) > 0,
            f"Signals: {len(signals)} emitted"
        )
        
        # Check every signal has source_url
        all_have_source = all(sig.get("source_url") == source_url for sig in signals)
        self.assert_test(
            "emit_signals: Every signal has source_url field",
            all_have_source,
            f"Signals without source_url: {[s.get('code') for s in signals if s.get('source_url') != source_url]}"
        )

        # Test 9.2: Signals are catalog-validated
        for sig in signals:
            code = sig.get("code")
            self.assert_test(
                f"emit_signals: Signal {code} has required fields",
                all(k in sig for k in ["code", "label", "severity", "category", "signal_type"]),
                f"Signal: {sig}"
            )

        # ═══════════════════════════════════════════════════════════════════════
        # 10. SIGNAL CATALOG: Sport-aware validation
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[10] SIGNAL CATALOG: Sport-aware validation", "SECTION")

        # Test 10.1: PITCHER_OVERPERFORMING is BASEBALL_ONLY
        sig_baseball = make_signal("PITCHER_OVERPERFORMING", sport="baseball")
        sig_football = make_signal("PITCHER_OVERPERFORMING", sport="football")
        sig_basketball = make_signal("PITCHER_OVERPERFORMING", sport="basketball")
        
        self.assert_test(
            "signal_catalog: PITCHER_OVERPERFORMING valid for baseball",
            sig_baseball is not None,
            f"Signal: {sig_baseball}"
        )
        self.assert_test(
            "signal_catalog: PITCHER_OVERPERFORMING returns None for football",
            sig_football is None,
            f"Should be None, got: {sig_football}"
        )
        self.assert_test(
            "signal_catalog: PITCHER_OVERPERFORMING returns None for basketball",
            sig_basketball is None,
            f"Should be None, got: {sig_basketball}"
        )

        # Test 10.2: RUN_LINE_TRAP is BASEBALL_ONLY
        sig_baseball = make_signal("RUN_LINE_TRAP", sport="baseball")
        sig_football = make_signal("RUN_LINE_TRAP", sport="football")
        
        self.assert_test(
            "signal_catalog: RUN_LINE_TRAP valid for baseball",
            sig_baseball is not None,
            f"Signal: {sig_baseball}"
        )
        self.assert_test(
            "signal_catalog: RUN_LINE_TRAP returns None for football",
            sig_football is None,
            f"Should be None, got: {sig_football}"
        )

        # Test 10.3: All new MLB codes are BASEBALL_ONLY
        mlb_codes = [
            "PITCHER_OVERPERFORMING",
            "PITCHER_UNDERVALUED",
            "RUN_LINE_TRAP",
            "STRONG_PITCHER_EDGE",
            "PARK_OVER_SIGNAL",
            "PARK_UNDER_SIGNAL",
            "NRFI_SIGNAL",
            "YRFI_SIGNAL",
        ]
        for code in mlb_codes:
            sig_baseball = make_signal(code, sport="baseball")
            sig_football = make_signal(code, sport="football")
            self.assert_test(
                f"signal_catalog: {code} is BASEBALL_ONLY",
                sig_baseball is not None and sig_football is None,
                f"Baseball: {sig_baseball is not None}, Football: {sig_football is None}"
            )

        # ═══════════════════════════════════════════════════════════════════════
        # 11. INTEGRATION TEST: GET /api/mlb/day endpoint
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[11] INTEGRATION TEST: GET /api/mlb/day endpoint", "SECTION")

        if not self.token:
            self.log("   ⚠ Skipping integration test (no auth token)", "WARN")
        else:
            # Test 11.1: GET /api/mlb/day with demo auth
            try:
                # Use a mid-season date for better chance of games
                test_date = "2025-08-15"
                resp = requests.get(
                    f"{BASE_URL}/mlb/day?date={test_date}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=60
                )
                
                self.assert_test(
                    "GET /api/mlb/day returns 200",
                    resp.status_code == 200,
                    f"Status: {resp.status_code}, Response: {resp.text[:500]}"
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    
                    # Test 11.2: Response shape
                    required_fields = [
                        "date",
                        "engine",
                        "picks",
                        "rescued_picks",
                        "discarded_picks",
                        "fragility_scores",
                        "editorial_context_signals_by_game",
                        "pipeline_meta"
                    ]
                    self.assert_test(
                        "GET /api/mlb/day has required fields",
                        all(f in result for f in required_fields),
                        f"Missing: {[f for f in required_fields if f not in result]}"
                    )
                    
                    # Test 11.3: Engine field
                    self.assert_test(
                        "GET /api/mlb/day engine = mlb_pregame_analytics_v1",
                        result.get("engine") == "mlb_pregame_analytics_v1",
                        f"Engine: {result.get('engine')}"
                    )
                    
                    # Test 11.4: pipeline_meta has statsapi_url
                    pipeline_meta = result.get("pipeline_meta", {})
                    statsapi_url = pipeline_meta.get("statsapi_url")
                    self.assert_test(
                        "GET /api/mlb/day pipeline_meta has statsapi_url",
                        statsapi_url is not None and statsapi_url.startswith("https://statsapi.mlb.com"),
                        f"statsapi_url: {statsapi_url}"
                    )
                    
                    self.log(f"   Date: {result.get('date')}")
                    self.log(f"   Confirmed games: {pipeline_meta.get('confirmed_games', 0)}")
                    self.log(f"   Picks: {len(result.get('picks', []))}")
                    self.log(f"   Rescued: {len(result.get('rescued_picks', []))}")
                    self.log(f"   Discarded: {len(result.get('discarded_picks', []))}")
                    
                    # Test 11.5: Discarded entries have pitcher_confirmation_source_url
                    discarded = result.get("discarded_picks", [])
                    if discarded:
                        for entry in discarded:
                            self.assert_test(
                                f"Discarded entry has pitcher_confirmation_source_url",
                                "pitcher_confirmation_source_url" in entry and
                                entry["pitcher_confirmation_source_url"] == statsapi_url,
                                f"Entry: {entry.get('game_pk')}, URL: {entry.get('pitcher_confirmation_source_url')}"
                            )
                    
                    # Test 11.6: Picks have editorial_context_signals with source_url
                    picks = result.get("picks", [])
                    if picks:
                        for pick in picks[:3]:  # Check first 3 picks
                            signals = pick.get("editorial_context_signals", [])
                            if signals:
                                for sig in signals:
                                    self.assert_test(
                                        f"Pick signal has source_url",
                                        "source_url" in sig and sig["source_url"] == statsapi_url,
                                        f"Signal: {sig.get('code')}, URL: {sig.get('source_url')}"
                                    )
                    
                    # Test 11.7: editorial_context_signals_by_game structure
                    signals_by_game = result.get("editorial_context_signals_by_game", {})
                    if signals_by_game:
                        sample_game_pk = list(signals_by_game.keys())[0]
                        sample_signals = signals_by_game[sample_game_pk]
                        self.assert_test(
                            "editorial_context_signals_by_game has signal arrays",
                            isinstance(sample_signals, list),
                            f"Game {sample_game_pk}: {type(sample_signals)}"
                        )
                        if sample_signals:
                            for sig in sample_signals:
                                self.assert_test(
                                    f"Signal in signals_by_game has source_url",
                                    "source_url" in sig,
                                    f"Signal: {sig.get('code')}"
                                )
                    
                    # Test 11.8: fragility_scores structure
                    fragility_scores = result.get("fragility_scores", {})
                    if fragility_scores:
                        sample_game_pk = list(fragility_scores.keys())[0]
                        sample_frag = fragility_scores[sample_game_pk]
                        self.assert_test(
                            "fragility_scores has score and label",
                            "score" in sample_frag and "label" in sample_frag,
                            f"Fragility: {sample_frag}"
                        )
                        self.assert_test(
                            "fragility_scores label is valid",
                            sample_frag.get("label") in ["MUY_PROTEGIDO", "PROTEGIDO", "RIESGO_MEDIO", "FRAGIL"],
                            f"Label: {sample_frag.get('label')}"
                        )
                
            except Exception as e:
                self.assert_test("GET /api/mlb/day", False, str(e))

        # ═══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n" + "=" * 80)
        self.log("TEST SUMMARY", "SECTION")
        self.log("=" * 80)
        self.log(f"Total tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed} ✅")
        self.log(f"Failed: {self.tests_failed} ❌")
        self.log(f"Success rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            self.log("\nFAILED TESTS:", "ERROR")
            for i, failure in enumerate(self.failures, 1):
                self.log(f"{i}. {failure['test']}", "ERROR")
                if failure['reason']:
                    self.log(f"   Reason: {failure['reason']}", "ERROR")
        
        return self.tests_failed == 0


def main():
    tester = MLBAnalyticsTester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
