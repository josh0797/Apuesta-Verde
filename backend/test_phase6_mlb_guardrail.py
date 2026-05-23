"""Phase 6 MLB Intelligence + Universal Market Guardrail Tests.

Tests the new features:
1. Market guardrail - evaluate_pick() for NO_BET_VALUE and VALUE_FOUND
2. Market guardrail - apply_market_guardrail() mutation
3. MLB sanitization - sanitize_mlb_picks() for forbidden markets
4. MLB Stats API - get_schedule_with_probables, get_pitcher_season_stats, get_team_batting_form
5. MLB intelligence - score_mlb_matchup()
6. Analyst engine - analyze_matches with db parameter
7. Per-sport calibration constants
"""
import sys
import asyncio
from datetime import datetime, timezone

# Test imports
try:
    from services import market_guardrail as mg
    from services import mlb_intelligence as mli
    from services import mlb_stats_api as msapi
    from services import analyst_engine as ae
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)


class Phase6Tester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []
        self.db = None

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def test(self, name: str, test_fn):
        """Run a single test function."""
        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        try:
            result = test_fn()
            if result:
                self.tests_passed += 1
                self.log(f"✅ PASSED", "SUCCESS")
                return True
            else:
                self.tests_failed += 1
                self.log(f"❌ FAILED", "ERROR")
                self.failures.append({"test": name, "reason": "Test function returned False"})
                return False
        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": str(e)})
            return False

    async def setup_db(self):
        """Setup MongoDB connection."""
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        client = AsyncIOMotorClient(mongo_url)
        self.db = client[os.environ.get("DB_NAME", "test_database")]
        self.log("✅ MongoDB connection established")

    # ── Market Guardrail Tests ──────────────────────────────────────────────

    def test_market_guardrail_favorite_trap(self):
        """Test NO_BET_VALUE verdict for favorite trap (high confidence, low odds)."""
        pick = {
            "recommendation": {
                "confidence_score": 80,
                "odds_range": "1.35"
            }
        }
        result = mg.evaluate_pick(pick, sport="football")
        
        # Expected: confidence 80 * calibration 0.85 = 68% estimated
        # Implied: 1/1.35 = 74%
        # Edge: 68% - 74% = -6% < 3% threshold → NO_BET_VALUE
        assert result["verdict"] == "NO_BET_VALUE", f"Expected NO_BET_VALUE, got {result['verdict']}"
        assert result["edge"] < 0, f"Expected negative edge, got {result['edge']}"
        assert result["edge"] < result["edge_threshold"], "Edge should be below threshold"
        self.log(f"   Edge: {result['edge']*100:.1f}%, Threshold: {result['edge_threshold']*100:.1f}%")
        return True

    def test_market_guardrail_value_found(self):
        """Test VALUE_FOUND verdict for underdog with real edge."""
        pick = {
            "recommendation": {
                "confidence_score": 65,
                "odds_range": "2.10"
            }
        }
        result = mg.evaluate_pick(pick, sport="football")
        
        # Expected: confidence 65 * calibration 0.85 = 55.25% estimated
        # Implied: 1/2.10 = 47.6%
        # Edge: 55.25% - 47.6% = ~7.6% > 3% threshold → VALUE_FOUND
        assert result["verdict"] == "VALUE_FOUND", f"Expected VALUE_FOUND, got {result['verdict']}"
        assert result["edge"] > 0, f"Expected positive edge, got {result['edge']}"
        assert result["edge"] >= result["edge_threshold"], "Edge should be above threshold"
        self.log(f"   Edge: {result['edge']*100:.1f}%, Threshold: {result['edge_threshold']*100:.1f}%")
        return True

    def test_market_guardrail_apply_mutation(self):
        """Test apply_market_guardrail() mutates parsed dict correctly."""
        parsed = {
            "picks": [
                {
                    "match_id": 1,
                    "match_label": "Team A vs Team B",
                    "recommendation": {"confidence_score": 80, "odds_range": "1.35"}
                },
                {
                    "match_id": 2,
                    "match_label": "Team C vs Team D",
                    "recommendation": {"confidence_score": 65, "odds_range": "2.10"}
                }
            ],
            "summary": {}
        }
        
        result = mg.apply_market_guardrail(parsed, sport="football")
        
        # Check that picks were filtered
        assert len(result["picks"]) == 1, f"Expected 1 pick kept, got {len(result['picks'])}"
        assert result["picks"][0]["match_id"] == 2, "Wrong pick kept"
        
        # Check that NO_BET_VALUE pick was rerouted
        discarded = result["summary"].get("discarded_market", [])
        assert len(discarded) == 1, f"Expected 1 discarded, got {len(discarded)}"
        assert discarded[0]["match_id"] == 1, "Wrong pick discarded"
        assert discarded[0].get("_market_guardrail_reroute") is True, "Missing reroute flag"
        
        # Check that _market_edge was attached to kept pick
        assert result["picks"][0].get("_market_edge") is not None, "Missing _market_edge"
        assert result["picks"][0]["_market_edge"]["verdict"] == "VALUE_FOUND"
        
        # Check pipeline metadata
        assert "_pipeline" in result, "Missing _pipeline"
        assert "market_guardrail" in result["_pipeline"], "Missing market_guardrail metadata"
        stats = result["_pipeline"]["market_guardrail"]
        assert stats["evaluated"] == 2, f"Expected 2 evaluated, got {stats['evaluated']}"
        assert stats["value_found"] == 1, f"Expected 1 value_found, got {stats['value_found']}"
        assert stats["no_bet_value_rerouted"] == 1, f"Expected 1 rerouted, got {stats['no_bet_value_rerouted']}"
        
        self.log(f"   Kept: {len(result['picks'])}, Discarded: {len(discarded)}")
        return True

    def test_calibration_constants(self):
        """Test per-sport calibration constants are loaded from env."""
        # Check default values
        assert mg.DEFAULT_CALIBRATION["football"] == 0.85, "Football calibration incorrect"
        assert mg.DEFAULT_CALIBRATION["basketball"] == 0.82, "Basketball calibration incorrect"
        assert mg.DEFAULT_CALIBRATION["baseball"] == 0.78, "Baseball calibration incorrect"
        
        # Test that estimated_probability_from_confidence uses calibration
        est_football = mg.estimated_probability_from_confidence(80, sport="football")
        est_baseball = mg.estimated_probability_from_confidence(80, sport="baseball")
        
        assert est_football == 0.68, f"Expected 0.68 for football, got {est_football}"  # 80 * 0.85 / 100
        assert est_baseball == 0.624, f"Expected 0.624 for baseball, got {est_baseball}"  # 80 * 0.78 / 100
        
        self.log(f"   Football 80 conf → {est_football*100:.1f}%, Baseball 80 conf → {est_baseball*100:.1f}%")
        return True

    # ── MLB Intelligence Tests ──────────────────────────────────────────────

    def test_mlb_sanitize_doble_oportunidad(self):
        """Test sanitize_mlb_picks() reroutes Doble Oportunidad picks."""
        parsed = {
            "_sport": "baseball",
            "picks": [
                {
                    "match_id": 1,
                    "match_label": "Rangers vs Angels",
                    "recommendation": {
                        "market": "Doble Oportunidad",
                        "selection": "Texas Rangers o empate"
                    }
                },
                {
                    "match_id": 2,
                    "match_label": "Yankees vs Red Sox",
                    "recommendation": {
                        "market": "Moneyline",
                        "selection": "Yankees gana"
                    }
                }
            ],
            "summary": {}
        }
        
        result = mli.sanitize_mlb_picks(parsed)
        
        # Check that forbidden pick was rerouted
        assert len(result["picks"]) == 1, f"Expected 1 pick kept, got {len(result['picks'])}"
        assert result["picks"][0]["match_id"] == 2, "Wrong pick kept"
        
        discarded = result["summary"].get("discarded_market", [])
        assert len(discarded) == 1, f"Expected 1 discarded, got {len(discarded)}"
        assert discarded[0]["match_id"] == 1, "Wrong pick discarded"
        assert "_mlb_sanitization" in discarded[0], "Missing _mlb_sanitization metadata"
        assert "Doble Oportunidad" in discarded[0]["_mlb_sanitization"]["original_market"]
        
        # Check pipeline metadata
        assert "_pipeline" in result, "Missing _pipeline"
        assert result["_pipeline"]["mlb_sanitization"]["sanitized"] == 1
        
        self.log(f"   Sanitized: {result['_pipeline']['mlb_sanitization']['sanitized']}")
        return True

    def test_mlb_sanitize_draw_no_bet(self):
        """Test sanitize_mlb_picks() reroutes Draw No Bet picks."""
        parsed = {
            "_sport": "baseball",
            "picks": [
                {
                    "match_id": 1,
                    "match_label": "Astros vs Cubs",
                    "recommendation": {
                        "market": "Draw No Bet",
                        "selection": "Astros"
                    }
                }
            ],
            "summary": {}
        }
        
        result = mli.sanitize_mlb_picks(parsed)
        
        assert len(result["picks"]) == 0, f"Expected 0 picks kept, got {len(result['picks'])}"
        discarded = result["summary"].get("discarded_market", [])
        assert len(discarded) == 1, f"Expected 1 discarded, got {len(discarded)}"
        assert "Draw No Bet" in discarded[0]["_mlb_sanitization"]["original_market"]
        
        return True

    def test_mlb_matchup_scoring(self):
        """Test score_mlb_matchup() with synthetic context."""
        mlb_context = {
            "available": True,
            "home_probable": "Gerrit Cole",
            "away_probable": "Jameson Taillon",
            "home_pitcher": {
                "era": 2.50,
                "whip": 1.00,
                "k_per_bb": 4.5,
                "hr_per_9": 0.8,
                "innings_pitched": 180.0
            },
            "away_pitcher": {
                "era": 4.50,
                "whip": 1.40,
                "k_per_bb": 2.0,
                "hr_per_9": 1.5,
                "innings_pitched": 150.0
            },
            "home_batting": {
                "ops": 0.780,
                "runs_per_game": 5.2
            },
            "away_batting": {
                "ops": 0.720,
                "runs_per_game": 4.5
            },
            "home_bullpen": {
                "fatigue_score_0_100": 30,
                "fatigue_label": "fresh"
            },
            "away_bullpen": {
                "fatigue_score_0_100": 70,
                "fatigue_label": "high"
            }
        }
        
        result = mli.score_mlb_matchup(mlb_context)
        
        assert result["available"] is True, "Matchup should be available"
        assert result["data_quality"] == "full", f"Expected full data quality, got {result['data_quality']}"
        
        # Home pitcher should be better (lower ERA, WHIP, higher K/BB)
        assert result["pitcher_advantage"] == "home", f"Expected home pitcher advantage, got {result['pitcher_advantage']}"
        
        # Home offense should be better (higher OPS, runs/game)
        assert result["offensive_pressure_side"] == "home", f"Expected home offensive pressure, got {result['offensive_pressure_side']}"
        
        # Home bullpen should be better (lower fatigue)
        assert result["bullpen_risk_side"] == "home", f"Expected home bullpen advantage, got {result['bullpen_risk_side']}"
        
        # Overall structural edge should favor home
        assert result["structural_edge_side"] == "home", f"Expected home structural edge, got {result['structural_edge_side']}"
        assert result["structural_edge_strength"] > 0.5, f"Expected strong edge, got {result['structural_edge_strength']}"
        
        self.log(f"   Structural edge: {result['structural_edge_side']} ({result['structural_edge_strength']*100:.0f}%)")
        self.log(f"   Narrative: {result['narrative'][:80]}...")
        return True

    # ── MLB Stats API Tests (best-effort, may be empty) ─────────────────────

    async def test_mlb_stats_api_schedule(self):
        """Test get_schedule_with_probables() - best effort."""
        if self.db is None:
            await self.setup_db()
        
        today = datetime.now(timezone.utc).date().isoformat()
        schedule = await msapi.get_schedule_with_probables(self.db, today)
        
        # Best-effort: may be empty if no MLB games today
        self.log(f"   Found {len(schedule)} MLB games for {today}")
        
        if schedule:
            sample = schedule[0]
            assert "gamePk" in sample, "Missing gamePk"
            assert "home_team" in sample, "Missing home_team"
            assert "away_team" in sample, "Missing away_team"
            self.log(f"   Sample: {sample['home_team']} vs {sample['away_team']}")
        else:
            self.log(f"   No MLB games scheduled today (expected for off-season)")
        
        return True

    async def test_mlb_stats_api_pitcher(self):
        """Test get_pitcher_season_stats() - best effort with known pitcher."""
        if self.db is None:
            await self.setup_db()
        
        # Use a known pitcher ID (Gerrit Cole: 543037)
        pitcher_id = 543037
        stats = await msapi.get_pitcher_season_stats(self.db, pitcher_id, season=2024)
        
        if stats:
            assert "era" in stats, "Missing ERA"
            assert "whip" in stats, "Missing WHIP"
            assert "innings_pitched" in stats, "Missing IP"
            self.log(f"   Pitcher {pitcher_id}: ERA={stats.get('era')}, WHIP={stats.get('whip')}, IP={stats.get('innings_pitched')}")
        else:
            self.log(f"   No stats found for pitcher {pitcher_id} (may be off-season or API issue)")
        
        return True

    async def test_mlb_stats_api_team_batting(self):
        """Test get_team_batting_form() - best effort with known team."""
        if self.db is None:
            await self.setup_db()
        
        # Use a known team ID (Yankees: 147)
        team_id = 147
        batting = await msapi.get_team_batting_form(self.db, team_id, season=2024)
        
        if batting:
            assert "ops" in batting, "Missing OPS"
            assert "runs_per_game" in batting, "Missing runs_per_game"
            self.log(f"   Team {team_id}: OPS={batting.get('ops')}, R/G={batting.get('runs_per_game')}")
        else:
            self.log(f"   No batting stats found for team {team_id} (may be off-season)")
        
        return True

    # ── Integration Tests ───────────────────────────────────────────────────

    def test_analyst_engine_accepts_db(self):
        """Test that analyze_matches accepts db parameter."""
        # This is a signature test - we don't run the full analysis
        import inspect
        sig = inspect.signature(ae.analyze_matches)
        params = list(sig.parameters.keys())
        
        assert "db" in params, f"analyze_matches missing 'db' parameter. Found: {params}"
        self.log(f"   analyze_matches signature: {params}")
        return True

    def test_market_guardrail_thresholds(self):
        """Test that market guardrail thresholds are correct."""
        assert mg.EDGE_THRESHOLDS["simple"] == 0.03, "Simple threshold should be 3%"
        assert mg.EDGE_THRESHOLDS["live"] == 0.05, "Live threshold should be 5%"
        assert mg.EDGE_THRESHOLDS["parlay"] == 0.07, "Parlay threshold should be 7%"
        
        self.log(f"   Thresholds: simple={mg.EDGE_THRESHOLDS['simple']*100}%, live={mg.EDGE_THRESHOLDS['live']*100}%, parlay={mg.EDGE_THRESHOLDS['parlay']*100}%")
        return True

    def print_summary(self):
        """Print test summary."""
        print("\n" + "="*80)
        print(f"PHASE 6 TEST SUMMARY")
        print("="*80)
        print(f"Total tests run:    {self.tests_run}")
        print(f"Tests passed:       {self.tests_passed} ✅")
        print(f"Tests failed:       {self.tests_failed} ❌")
        print(f"Success rate:       {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            print("\n" + "="*80)
            print("FAILURES:")
            print("="*80)
            for f in self.failures:
                print(f"\n❌ {f['test']}")
                print(f"   Reason: {f['reason']}")
        
        print("\n" + "="*80)
        return self.tests_failed == 0


async def main():
    tester = Phase6Tester()
    
    print("="*80)
    print("PHASE 6 MLB INTELLIGENCE + UNIVERSAL MARKET GUARDRAIL TESTS")
    print("="*80)
    print()
    
    # Market Guardrail Tests
    print("\n── MARKET GUARDRAIL TESTS ──")
    tester.test("Market Guardrail - Favorite Trap (NO_BET_VALUE)", 
                tester.test_market_guardrail_favorite_trap)
    tester.test("Market Guardrail - Underdog Value (VALUE_FOUND)", 
                tester.test_market_guardrail_value_found)
    tester.test("Market Guardrail - apply_market_guardrail() mutation", 
                tester.test_market_guardrail_apply_mutation)
    tester.test("Market Guardrail - Per-sport calibration constants", 
                tester.test_calibration_constants)
    tester.test("Market Guardrail - Thresholds (3%/5%/7%)", 
                tester.test_market_guardrail_thresholds)
    
    # MLB Intelligence Tests
    print("\n── MLB INTELLIGENCE TESTS ──")
    tester.test("MLB Sanitization - Doble Oportunidad reroute", 
                tester.test_mlb_sanitize_doble_oportunidad)
    tester.test("MLB Sanitization - Draw No Bet reroute", 
                tester.test_mlb_sanitize_draw_no_bet)
    tester.test("MLB Matchup Scoring - score_mlb_matchup()", 
                tester.test_mlb_matchup_scoring)
    
    # MLB Stats API Tests (async, best-effort)
    print("\n── MLB STATS API TESTS (best-effort) ──")
    await tester.test_mlb_stats_api_schedule()
    await tester.test_mlb_stats_api_pitcher()
    await tester.test_mlb_stats_api_team_batting()
    
    # Integration Tests
    print("\n── INTEGRATION TESTS ──")
    tester.test("Analyst Engine - accepts db parameter", 
                tester.test_analyst_engine_accepts_db)
    
    # Print summary
    success = tester.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
