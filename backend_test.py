"""MLB Engine V6 — Over Discovery Engine + Daily Market Audit Testing

Tests the pure functions in mlb_over_discovery.py and the daily_market_audit endpoint.
"""
import sys
import json
from datetime import datetime

# Add backend to path
sys.path.insert(0, '/app/backend')

# Test the pure functions from mlb_over_discovery
try:
    from services.mlb_over_discovery import (
        calculate_offensive_explosion_score,
        classify_offensive_script,
        calculate_over_survival_score,
        evaluate_over_markets,
        over_discovery_engine,
        market_competition,
        daily_market_audit,
        OFFENSIVE_SCRIPTS,
    )
    print("✅ Successfully imported mlb_over_discovery functions")
except Exception as e:
    print(f"❌ Failed to import mlb_over_discovery: {e}")
    sys.exit(1)


class TestMLBOverDiscovery:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        
    def run_test(self, name, test_func):
        """Run a single test"""
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        try:
            test_func()
            self.tests_passed += 1
            print(f"✅ Passed - {name}")
            return True
        except AssertionError as e:
            print(f"❌ Failed - {name}: {e}")
            return False
        except Exception as e:
            print(f"❌ Error - {name}: {e}")
            return False
    
    def test_offensive_explosion_score_extreme_over(self):
        """Test extreme over profile (weak pitchers + Coors + wind out + hot lineups)"""
        scoring_ctx = {
            "offense_home": {"score": 75, "last7_score": 80, "team_ops": 0.800, "team_iso": 0.200, "hr_per_game": 1.8},
            "offense_away": {"score": 72, "last7_score": 78, "team_ops": 0.790, "team_iso": 0.190, "hr_per_game": 1.6},
            "home_pitcher_quality": {"score": 35},
            "away_pitcher_quality": {"score": 38},
            "home_pitcher_stats": {"hr_per_9": 1.60, "hard_hit_pct": 42},
            "away_pitcher_stats": {"hr_per_9": 1.55, "hard_hit_pct": 41},
            "favorite_bullpen_era_7d": 5.50,
            "underdog_bullpen_era_7d": 5.30,
            "bullpen": {"fatigue_score": 70},
            "park": {"park_runs_mult": 1.15, "weather_score": 80},
        }
        result = calculate_offensive_explosion_score(scoring_ctx, {})
        
        # Verify score is in [0,100]
        assert 0 <= result["score"] <= 100, f"Score {result['score']} out of range"
        
        # Verify components sum with declared weights (28+26+22+14+10=100)
        weights = result["weights"]
        assert weights["lineups"] == 28, f"Lineups weight {weights['lineups']} != 28"
        assert weights["pitchers"] == 26, f"Pitchers weight {weights['pitchers']} != 26"
        assert weights["bullpens"] == 22, f"Bullpens weight {weights['bullpens']} != 22"
        assert weights["park"] == 14, f"Park weight {weights['park']} != 14"
        assert weights["weather"] == 10, f"Weather weight {weights['weather']} != 10"
        
        # Verify high explosion score for extreme over profile
        assert result["score"] >= 70, f"Expected high explosion score, got {result['score']}"
        
        # Verify drivers are present
        assert len(result["drivers"]) > 0, "No drivers found"
        
        print(f"  Explosion score: {result['score']}/100")
        print(f"  Components: {result['components']}")
        print(f"  Drivers: {result['drivers'][:3]}")
    
    def test_offensive_explosion_score_extreme_under(self):
        """Test extreme under profile (elite pitchers + cold weather + pitcher park)"""
        scoring_ctx = {
            "offense_home": {"score": 40, "last7_score": 38, "team_ops": 0.680, "team_iso": 0.130, "hr_per_game": 0.8},
            "offense_away": {"score": 42, "last7_score": 40, "team_ops": 0.690, "team_iso": 0.135, "hr_per_game": 0.9},
            "home_pitcher_quality": {"score": 85},
            "away_pitcher_quality": {"score": 82},
            "home_pitcher_stats": {"hr_per_9": 0.70, "hard_hit_pct": 28},
            "away_pitcher_stats": {"hr_per_9": 0.75, "hard_hit_pct": 29},
            "favorite_bullpen_era_7d": 2.80,
            "underdog_bullpen_era_7d": 3.00,
            "bullpen": {"fatigue_score": 20},
            "park": {"park_runs_mult": 0.92, "weather_score": 25},
        }
        result = calculate_offensive_explosion_score(scoring_ctx, {})
        
        # Verify low explosion score for extreme under profile
        assert result["score"] <= 40, f"Expected low explosion score, got {result['score']}"
        
        print(f"  Explosion score: {result['score']}/100")
        print(f"  Components: {result['components']}")
    
    def test_offensive_explosion_score_neutral(self):
        """Test neutral scenario"""
        scoring_ctx = {
            "offense_home": {"score": 50, "last7_score": 50, "team_ops": 0.730, "team_iso": 0.150, "hr_per_game": 1.2},
            "offense_away": {"score": 50, "last7_score": 50, "team_ops": 0.730, "team_iso": 0.150, "hr_per_game": 1.2},
            "home_pitcher_quality": {"score": 50},
            "away_pitcher_quality": {"score": 50},
            "home_pitcher_stats": {"hr_per_9": 1.10, "hard_hit_pct": 35},
            "away_pitcher_stats": {"hr_per_9": 1.10, "hard_hit_pct": 35},
            "favorite_bullpen_era_7d": 4.00,
            "underdog_bullpen_era_7d": 4.00,
            "bullpen": {"fatigue_score": 40},
            "park": {"park_runs_mult": 1.00, "weather_score": 50},
        }
        result = calculate_offensive_explosion_score(scoring_ctx, {})
        
        # Verify neutral score
        assert 40 <= result["score"] <= 60, f"Expected neutral score, got {result['score']}"
        
        print(f"  Explosion score: {result['score']}/100")
    
    def test_classify_offensive_script(self):
        """Test offensive script classification with correct thresholds"""
        test_cases = [
            (85, "OFFENSIVE_EXPLOSION", "Explosión ofensiva"),
            (75, "HIGH_SCORING", "Alto scoring"),
            (60, "ABOVE_AVERAGE_SCORING", "Sobre el promedio"),
            (50, "NEUTRAL", "Neutral"),
            (35, "LOW_SCORING", "Bajo scoring"),
            (25, "PITCHERS_DUEL", "Duelo de pitchers"),
        ]
        
        for score, expected_code, expected_label in test_cases:
            result = classify_offensive_script(score, {})
            assert result["code"] == expected_code, f"Score {score}: expected {expected_code}, got {result['code']}"
            assert result["label_es"] == expected_label, f"Score {score}: expected {expected_label}, got {result['label_es']}"
            assert result["score"] == score, f"Score mismatch: {result['score']} != {score}"
            assert result["code"] in OFFENSIVE_SCRIPTS, f"Code {result['code']} not in OFFENSIVE_SCRIPTS"
            print(f"  Score {score} → {result['code']} ({result['label_es']}) ✓")
    
    def test_over_survival_score(self):
        """Test over survival score calculation"""
        scoring_ctx = {
            "offense_home": {"score": 65},
            "offense_away": {"score": 62},
            "favorite_bullpen_era_7d": 5.00,
            "underdog_bullpen_era_7d": 4.80,
            "park": {"park_runs_mult": 1.08, "weather_score": 70},
        }
        explosion_payload = {"score": 75}
        
        result = calculate_over_survival_score(scoring_ctx, {}, explosion_payload=explosion_payload)
        
        # Verify score is in [0,100]
        assert 0 <= result["score"] <= 100, f"Score {result['score']} out of range"
        
        # Verify components are present
        assert "components" in result, "Missing components"
        assert "drivers" in result, "Missing drivers"
        
        print(f"  Over survival score: {result['score']}/100")
        print(f"  Components: {result['components']}")
        print(f"  Drivers: {result['drivers'][:3]}")
    
    def test_evaluate_over_markets(self):
        """Test over markets evaluation and ranking"""
        scoring_ctx = {
            "offense_home": {"score": 65},
            "offense_away": {"score": 62},
            "home_pitcher_quality": {"score": 45},
            "away_pitcher_quality": {"score": 48},
            "favorite_bullpen_era_7d": 4.50,
            "underdog_bullpen_era_7d": 4.30,
            "park": {"park_runs_mult": 1.05, "weather_score": 65},
        }
        v2_payload = {"expectedRuns": 9.2, "smartTotalsLine": 8.5}
        over_lines = {
            "full_game": 8.5,
            "f5": 4.5,
            "team_total_home": 4.5,
            "team_total_away": 4.0,
            "yrfi": True,
        }
        explosion_payload = {"score": 68}
        over_survival_payload = {"score": 72}
        
        result = evaluate_over_markets(
            scoring_ctx, v2_payload,
            over_lines=over_lines,
            explosion_payload=explosion_payload,
            over_survival_payload=over_survival_payload,
        )
        
        # Verify result is a list
        assert isinstance(result, list), "Result should be a list"
        
        # Verify markets are sorted by edge descending
        if len(result) > 1:
            for i in range(len(result) - 1):
                edge1 = result[i].get("edge", 0)
                edge2 = result[i+1].get("edge", 0)
                assert edge1 >= edge2, f"Markets not sorted by edge: {edge1} < {edge2}"
        
        # Verify categories are correct
        valid_categories = {"OVER_FULL_GAME", "OVER_F5", "TEAM_TOTAL_OVER", "YRFI"}
        for market in result:
            assert market["category"] in valid_categories, f"Invalid category: {market['category']}"
            print(f"  {market['market']}: edge {market['edge']:+.2f}, score {market['score']:.1f}, category {market['category']}")
    
    def test_market_competition_over_wins(self):
        """Test market competition when Over edge dominates"""
        under_candidate = {
            "market": "Full Game Under 8.5",
            "line": 8.5,
            "edge": 0.5,
            "score": 70,
        }
        over_candidate = {
            "market": "Full Game Over 8.5",
            "line": 8.5,
            "edge": 2.8,  # Over edge - Under edge = 2.3 >= 2.0
            "score": 75,
        }
        current = {"market": "Full Game Under 8.5"}
        
        result = market_competition(under_candidate, over_candidate, current=current)
        
        # Verify Over wins
        assert result["winner_side"] == "OVER", f"Expected OVER to win, got {result['winner_side']}"
        assert result["swap_required"] == True, "Swap should be required"
        assert result["edge_gap"] >= 2.0, f"Edge gap {result['edge_gap']} should be >= 2.0"
        
        print(f"  Winner: {result['winner_side']}, swap_required: {result['swap_required']}")
        print(f"  Edge gap: {result['edge_gap']:.2f}")
        print(f"  Explanation: {result['explanation']}")
    
    def test_market_competition_under_wins(self):
        """Test market competition when Under edge is clearly stronger"""
        under_candidate = {
            "market": "Full Game Under 8.5",
            "line": 8.5,
            "edge": 1.8,
            "score": 75,
        }
        over_candidate = {
            "market": "Full Game Over 8.5",
            "line": 8.5,
            "edge": 0.5,
            "score": 65,
        }
        current = {"market": "Full Game Under 8.5"}
        
        result = market_competition(under_candidate, over_candidate, current=current)
        
        # Verify Under wins
        assert result["winner_side"] == "UNDER", f"Expected UNDER to win, got {result['winner_side']}"
        assert result["swap_required"] == False, "Swap should not be required"
        
        print(f"  Winner: {result['winner_side']}, swap_required: {result['swap_required']}")
    
    def test_market_competition_current_wins(self):
        """Test market competition when edges are similar"""
        under_candidate = {
            "market": "Full Game Under 8.5",
            "line": 8.5,
            "edge": 1.2,
            "score": 70,
        }
        over_candidate = {
            "market": "Full Game Over 8.5",
            "line": 8.5,
            "edge": 1.5,  # Difference is only 0.3, not enough to swap
            "score": 72,
        }
        current = {"market": "Full Game Under 8.5"}
        
        result = market_competition(under_candidate, over_candidate, current=current)
        
        # Verify current selection is kept
        assert result["winner_side"] == "CURRENT", f"Expected CURRENT to win, got {result['winner_side']}"
        assert result["swap_required"] == False, "Swap should not be required"
        
        print(f"  Winner: {result['winner_side']}, swap_required: {result['swap_required']}")
    
    def test_daily_market_audit_under_bias(self):
        """Test daily market audit detects under bias"""
        picks = [
            {"recommendation": {"market": "Full Game Under 8.5"}},
            {"recommendation": {"market": "F5 Under 4.5"}},
            {"recommendation": {"market": "Full Game Under 9.5"}},
            {"recommendation": {"market": "NRFI"}},
            {"recommendation": {"market": "Full Game Under 7.5"}},
        ]
        
        result = daily_market_audit(picks, evaluated_count=5)
        
        # Verify structure
        assert "report" in result, "Missing report"
        assert "bias" in result, "Missing bias"
        assert "diversity" in result, "Missing diversity"
        assert "narrative_es" in result, "Missing narrative_es"
        
        # Verify under bias warning
        warnings = result["bias"]["warning_codes"]
        assert "UNDER_BIAS_WARNING" in warnings or "OVER_STARVATION" in warnings, \
            f"Expected under bias warning, got {warnings}"
        
        # Verify diversity score is in [0,100]
        diversity_score = result["diversity"]["score"]
        assert 0 <= diversity_score <= 100, f"Diversity score {diversity_score} out of range"
        
        # Verify diversity level
        assert result["diversity"]["level"] in ["HEALTHY", "MODERATE", "POOR", "CRITICAL"], \
            f"Invalid diversity level: {result['diversity']['level']}"
        
        print(f"  Total picks: {result['report']['total_picks']}")
        print(f"  Under total: {result['report']['under_total']}")
        print(f"  Over total: {result['report']['over_total']}")
        print(f"  Warnings: {warnings}")
        print(f"  Diversity: {diversity_score:.1f}/100 ({result['diversity']['level']})")
    
    def test_daily_market_audit_balanced(self):
        """Test daily market audit with balanced markets"""
        picks = [
            {"recommendation": {"market": "Full Game Under 8.5"}},
            {"recommendation": {"market": "Full Game Over 9.5"}},
            {"recommendation": {"market": "F5 Under 4.5"}},
            {"recommendation": {"market": "Team Total Home Over 4.5"}},
            {"recommendation": {"market": "Run Line -1.5"}},
            {"recommendation": {"market": "YRFI"}},
        ]
        
        result = daily_market_audit(picks, evaluated_count=6)
        
        # Verify no major warnings
        warnings = result["bias"]["warning_codes"]
        assert "UNDER_BIAS_WARNING" not in warnings, "Should not have under bias"
        assert "OVER_BIAS_WARNING" not in warnings, "Should not have over bias"
        
        # Verify higher diversity score
        diversity_score = result["diversity"]["score"]
        assert diversity_score >= 50, f"Expected higher diversity, got {diversity_score}"
        
        print(f"  Diversity: {diversity_score:.1f}/100 ({result['diversity']['level']})")
        print(f"  Distinct markets: {result['diversity']['distinct_used']}")


def main():
    print("=" * 80)
    print("MLB ENGINE V6 — OVER DISCOVERY ENGINE + DAILY MARKET AUDIT")
    print("Backend Pure Functions Testing")
    print("=" * 80)
    
    tester = TestMLBOverDiscovery()
    
    # Test offensive explosion score
    tester.run_test(
        "Offensive Explosion Score - Extreme Over Profile",
        tester.test_offensive_explosion_score_extreme_over
    )
    tester.run_test(
        "Offensive Explosion Score - Extreme Under Profile",
        tester.test_offensive_explosion_score_extreme_under
    )
    tester.run_test(
        "Offensive Explosion Score - Neutral Scenario",
        tester.test_offensive_explosion_score_neutral
    )
    
    # Test offensive script classification
    tester.run_test(
        "Offensive Script Classification - All Thresholds",
        tester.test_classify_offensive_script
    )
    
    # Test over survival score
    tester.run_test(
        "Over Survival Score Calculation",
        tester.test_over_survival_score
    )
    
    # Test over markets evaluation
    tester.run_test(
        "Over Markets Evaluation and Ranking",
        tester.test_evaluate_over_markets
    )
    
    # Test market competition
    tester.run_test(
        "Market Competition - Over Wins (edge >= 2.0)",
        tester.test_market_competition_over_wins
    )
    tester.run_test(
        "Market Competition - Under Wins",
        tester.test_market_competition_under_wins
    )
    tester.run_test(
        "Market Competition - Current Wins (similar edges)",
        tester.test_market_competition_current_wins
    )
    
    # Test daily market audit
    tester.run_test(
        "Daily Market Audit - Under Bias Detection",
        tester.test_daily_market_audit_under_bias
    )
    tester.run_test(
        "Daily Market Audit - Balanced Markets",
        tester.test_daily_market_audit_balanced
    )
    
    # Print summary
    print("\n" + "=" * 80)
    print(f"📊 Tests passed: {tester.tests_passed}/{tester.tests_run}")
    print("=" * 80)
    
    return 0 if tester.tests_passed == tester.tests_run else 1


if __name__ == "__main__":
    sys.exit(main())
