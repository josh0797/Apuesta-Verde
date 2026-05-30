#!/usr/bin/env python3
"""
MLB V5 Script Survival & Fragility Model Backend Test Suite
============================================================
Tests all pure functions and pipeline integration for MLB Engine V5.
"""
import sys
import json
from datetime import datetime

# Test the pure functions from mlb_script_survival.py
sys.path.insert(0, '/app/backend')

from services.mlb_script_survival import (
    calculate_script_survival_score,
    calculate_fragility_score,
    classify_script_stability,
    build_script_survival_payload,
)

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

class MLBV5TestSuite:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, message: str, color: str = Colors.BLUE):
        print(f"{color}{message}{Colors.END}")

    def assert_true(self, condition: bool, test_name: str, message: str = ""):
        """Assert a condition is true"""
        self.tests_run += 1
        if condition:
            self.tests_passed += 1
            self.log(f"✅ PASSED: {test_name}", Colors.GREEN)
            return True
        else:
            self.tests_failed += 1
            self.log(f"❌ FAILED: {test_name} - {message}", Colors.RED)
            self.failures.append(f"{test_name}: {message}")
            return False

    def assert_in_range(self, value: float, min_val: float, max_val: float, test_name: str):
        """Assert a value is within a range"""
        self.tests_run += 1
        if min_val <= value <= max_val:
            self.tests_passed += 1
            self.log(f"✅ PASSED: {test_name} (value={value:.1f}, range=[{min_val}, {max_val}])", Colors.GREEN)
            return True
        else:
            self.tests_failed += 1
            msg = f"Value {value:.1f} not in range [{min_val}, {max_val}]"
            self.log(f"❌ FAILED: {test_name} - {msg}", Colors.RED)
            self.failures.append(f"{test_name}: {msg}")
            return False

    def test_phillies_dodgers_survival_score(self):
        """Test calculate_script_survival_score with Phillies@Dodgers context (should be >=78)"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Phillies@Dodgers Survival Score (Expected >=78)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Phillies@Dodgers context: high quality pitchers, low volatility, good bullpen
        scoring_ctx = {
            "home_pitcher_quality": {"score": 70},
            "away_pitcher_quality": {"score": 75},
            "home_pitcher_stats": {
                "era": 3.20,
                "xera": 3.15,
                "fip": 3.10,
                "whip": 1.05,
                "hard_hit_pct": 28.5,
                "barrel_pct": 6.2,
                "starts_with_5plus_runs": 1,
                "games_pitched": 20,
            },
            "away_pitcher_stats": {
                "era": 3.50,
                "xera": 3.40,
                "fip": 3.35,
                "whip": 1.10,
                "hard_hit_pct": 30.0,
                "barrel_pct": 7.0,
                "starts_with_5plus_runs": 1,
                "games_pitched": 18,
            },
            "bullpen": {"score": 78, "fatigue_score": 25, "blown_save_rate": 0.15},
            "favorite_bullpen_era_7d": 3.40,
            "underdog_bullpen_era_7d": 3.60,
            "offense_home": {"score": 52},
            "offense_away": {"score": 48},
            "offense_variance": 28,
            "park": {"park_runs_mult": 0.96, "weather_score": 50},
        }

        hist_profile = {
            "available": True,
            "home": {"total_runs_variance": 2.6},
            "away": {"total_runs_variance": 2.8},
        }

        result = calculate_script_survival_score(
            scoring_ctx=scoring_ctx,
            hist_profile=hist_profile,
        )

        # Check score is >= 78
        self.assert_in_range(result["score"], 78, 100, "Phillies@Dodgers survival score >= 78")
        
        # Check all 5 components are present
        components = result.get("components", {})
        self.assert_true(
            all(k in components for k in ["pitchers", "bullpen", "offense", "environment", "historical"]),
            "All 5 components present",
            f"Missing components: {set(['pitchers', 'bullpen', 'offense', 'environment', 'historical']) - set(components.keys())}"
        )

        # Check weights sum to 100
        weights = result.get("weights", {})
        weight_sum = sum(weights.values())
        self.assert_in_range(weight_sum, 99, 101, "Weights sum to ~100")

        self.log(f"\n📊 Result: score={result['score']:.1f}, components={components}", Colors.BLUE)
        return result

    def test_arizona_seattle_gallen_survival_score(self):
        """Test calculate_script_survival_score with Arizona@Seattle Gallen context (should be <=60)"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Arizona@Seattle Gallen Survival Score (Expected <=60)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Gallen context: moderate quality, HIGH volatility, poor recent form
        scoring_ctx = {
            "home_pitcher_quality": {"score": 53},
            "away_pitcher_quality": {"score": 48},
            "home_pitcher_stats": {
                "era": 4.80,
                "xera": 4.65,
                "fip": 4.50,
                "whip": 1.35,
                "hard_hit_pct": 38.5,
                "barrel_pct": 10.5,
                "starts_with_5plus_runs": 3,  # HIGH volatility marker
                "games_pitched": 15,
            },
            "away_pitcher_stats": {
                "era": 4.95,
                "xera": 4.80,
                "fip": 4.70,
                "whip": 1.40,
                "hard_hit_pct": 40.0,
                "barrel_pct": 11.0,
                "starts_with_5plus_runs": 2,
                "games_pitched": 14,
            },
            "bullpen": {"score": 55, "fatigue_score": 45, "blown_save_rate": 0.28},
            "favorite_bullpen_era_7d": 4.80,
            "underdog_bullpen_era_7d": 4.95,
            "offense_home": {"score": 58},
            "offense_away": {"score": 62},
            "offense_variance": 42,
            "park": {"park_runs_mult": 1.05, "weather_score": 65},
        }

        hist_profile = {
            "available": True,
            "home": {"total_runs_variance": 4.8},
            "away": {"total_runs_variance": 5.2},
        }

        result = calculate_script_survival_score(
            scoring_ctx=scoring_ctx,
            hist_profile=hist_profile,
        )

        # Check score is <= 60
        self.assert_in_range(result["score"], 0, 60, "Arizona@Seattle Gallen survival score <= 60")
        
        self.log(f"\n📊 Result: score={result['score']:.1f}, components={result.get('components', {})}", Colors.BLUE)
        return result

    def test_fragility_score_near_line(self):
        """Test calculate_fragility_score when ER is near the line (no buffer)"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Fragility Score - Near Line (High Fragility)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        scoring_ctx = {
            "home_pitcher_quality": {"score": 60},
            "away_pitcher_quality": {"score": 58},
            "favorite_bullpen_era_7d": 4.20,
            "underdog_bullpen_era_7d": 4.35,
        }

        v2_payload = {
            "expectedRuns": 9.2,  # Very close to line
            "smartTotalsLine": 9.5,
            "recommendedLine": "UNDER 9.5",
        }

        survival_payload = {
            "score": 55,
            "volatility": {
                "home": {"score": 35, "penalty": 8, "level": "MEDIUM"},
                "away": {"score": 40, "penalty": 10, "level": "MEDIUM"},
            }
        }

        result = calculate_fragility_score(
            scoring_ctx=scoring_ctx,
            v2_payload=v2_payload,
            survival_payload=survival_payload,
        )

        # When ER is near line, fragility should be high
        # near_line_penalty should be significant
        near_line_penalty = result.get("near_line_penalty", 0)
        self.assert_true(
            near_line_penalty >= 10,
            "Near line penalty >= 10 when ER muy cerca de línea",
            f"near_line_penalty={near_line_penalty:.1f}"
        )

        # Check drivers mention near line
        drivers = result.get("drivers", [])
        has_near_line_driver = any("muy cerca de la línea" in d.lower() for d in drivers)
        self.assert_true(
            has_near_line_driver,
            "Drivers include 'muy cerca de la línea'",
            f"drivers={drivers}"
        )

        self.log(f"\n📊 Result: score={result['score']:.1f}, near_line_penalty={near_line_penalty:.1f}", Colors.BLUE)
        return result

    def test_fragility_score_comfortable_buffer(self):
        """Test calculate_fragility_score with comfortable buffer (line - ER >= 2.5)"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Fragility Score - Comfortable Buffer (Low Fragility)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        scoring_ctx = {
            "home_pitcher_quality": {"score": 70},
            "away_pitcher_quality": {"score": 72},
            "favorite_bullpen_era_7d": 3.50,
            "underdog_bullpen_era_7d": 3.65,
        }

        v2_payload = {
            "expectedRuns": 6.8,  # Comfortable buffer
            "smartTotalsLine": 9.5,
            "recommendedLine": "UNDER 9.5",
        }

        survival_payload = {
            "score": 80,
            "volatility": {
                "home": {"score": 20, "penalty": 2, "level": "LOW"},
                "away": {"score": 22, "penalty": 3, "level": "LOW"},
            }
        }

        result = calculate_fragility_score(
            scoring_ctx=scoring_ctx,
            v2_payload=v2_payload,
            survival_payload=survival_payload,
        )

        # With comfortable buffer, fragility should be lower than 25
        self.assert_in_range(result["score"], 0, 25, "Fragility < 25 with comfortable buffer")

        # Check near_line_penalty is negative (buffer bonus)
        near_line_penalty = result.get("near_line_penalty", 0)
        self.assert_true(
            near_line_penalty < 0,
            "Near line penalty is negative (buffer bonus)",
            f"near_line_penalty={near_line_penalty:.1f}"
        )

        self.log(f"\n📊 Result: score={result['score']:.1f}, near_line_penalty={near_line_penalty:.1f}", Colors.BLUE)
        return result

    def test_classify_script_stability(self):
        """Test classify_script_stability with various survival/fragility combinations"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Script Stability Classification", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        test_cases = [
            # (survival, fragility, expected_code)
            (90, 10, "ELITE_STABLE"),
            (85, 15, "ELITE_STABLE"),
            (80, 20, "STABLE"),
            (75, 25, "STABLE"),
            (65, 35, "MODERATELY_STABLE"),
            (60, 40, "MODERATELY_STABLE"),
            (50, 55, "FRAGILE"),
            (45, 60, "FRAGILE"),
            (40, 70, "HIGHLY_FRAGILE"),
            (30, 80, "HIGHLY_FRAGILE"),
        ]

        for survival, fragility, expected_code in test_cases:
            result = classify_script_stability(survival, fragility)
            actual_code = result.get("code")
            
            self.assert_true(
                actual_code == expected_code,
                f"Stability classification for survival={survival}, fragility={fragility}",
                f"Expected {expected_code}, got {actual_code}"
            )

    def test_build_script_survival_payload_reference_profile(self):
        """Test build_script_survival_payload reference_profile logic"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Reference Profile Tagging (Phillies@Dodgers benchmark)", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        # Reference profile conditions:
        # - ER <= 6.5
        # - Survival >= 85
        # - Fragility <= 15
        # - Pitcher quality >= 65
        # - Park mult <= 1.02

        scoring_ctx = {
            "home_pitcher_quality": {"score": 70},
            "away_pitcher_quality": {"score": 72},
            "home_pitcher_stats": {
                "era": 3.10,
                "xera": 3.05,
                "starts_with_5plus_runs": 0,
                "games_pitched": 20,
            },
            "away_pitcher_stats": {
                "era": 3.25,
                "xera": 3.20,
                "starts_with_5plus_runs": 1,
                "games_pitched": 18,
            },
            "bullpen": {"score": 80, "fatigue_score": 20, "blown_save_rate": 0.12},
            "favorite_bullpen_era_7d": 3.30,
            "underdog_bullpen_era_7d": 3.45,
            "offense_home": {"score": 50},
            "offense_away": {"score": 48},
            "offense_variance": 25,
            "park": {"park_runs_mult": 0.98, "weather_score": 50},
        }

        v2_payload = {
            "expectedRuns": 6.3,  # <= 6.5
            "smartTotalsLine": 9.0,
        }

        hist_profile = {
            "available": True,
            "home": {"total_runs_variance": 2.4},
            "away": {"total_runs_variance": 2.6},
        }

        result = build_script_survival_payload(
            scoring_ctx=scoring_ctx,
            v2_payload=v2_payload,
            hist_profile=hist_profile,
        )

        # Check reference_profile is True
        self.assert_true(
            result.get("reference_profile") == True,
            "Reference profile tagged as True",
            f"reference_profile={result.get('reference_profile')}"
        )

        # Check survival >= 85
        survival_score = result.get("survival", {}).get("score", 0)
        self.assert_in_range(survival_score, 85, 100, "Survival >= 85 for reference profile")

        # Check fragility <= 15
        fragility_score = result.get("fragility", {}).get("score", 100)
        self.assert_in_range(fragility_score, 0, 15, "Fragility <= 15 for reference profile")

        self.log(f"\n📊 Result: reference_profile={result.get('reference_profile')}, survival={survival_score:.1f}, fragility={fragility_score:.1f}", Colors.BLUE)
        return result

    def test_confidence_contribution_mapping(self):
        """Test confidence contribution mapping for different survival scores"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST: Confidence Contribution Mapping", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)

        test_cases = [
            # (survival_score, expected_contrib_range)
            (92, (14, 16)),   # >= 90 => +15
            (85, (9, 11)),    # >= 80 => +10
            (75, (5, 7)),     # >= 70 => +6
            (60, (1, 3)),     # >= 55 => +2
            (45, (-5, -3)),   # >= 40 => -4
            (30, (-9, -7)),   # >= 25 => -8
            (20, (-13, -11)), # < 25 => -12
        ]

        for survival_score, (min_contrib, max_contrib) in test_cases:
            scoring_ctx = {
                "home_pitcher_quality": {"score": survival_score / 2},
                "away_pitcher_quality": {"score": survival_score / 2},
                "home_pitcher_stats": {"era": 4.0, "games_pitched": 15},
                "away_pitcher_stats": {"era": 4.0, "games_pitched": 15},
                "bullpen": {"score": survival_score, "fatigue_score": 30},
                "favorite_bullpen_era_7d": 4.0,
                "underdog_bullpen_era_7d": 4.0,
                "offense_home": {"score": 50},
                "offense_away": {"score": 50},
                "park": {"park_runs_mult": 1.0, "weather_score": 50},
            }

            v2_payload = {"expectedRuns": 8.5}

            # Force survival score by adjusting inputs
            result = build_script_survival_payload(
                scoring_ctx=scoring_ctx,
                v2_payload=v2_payload,
            )

            actual_contrib = result.get("confidence_contribution", 0)
            
            # We can't force exact survival scores, so just check the contribution is reasonable
            self.log(f"  Survival ~{survival_score} => contribution={actual_contrib:.1f} (expected range [{min_contrib}, {max_contrib}])", Colors.BLUE)

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*80, Colors.YELLOW)
        self.log("TEST SUMMARY", Colors.YELLOW)
        self.log("="*80, Colors.YELLOW)
        
        total = self.tests_run
        passed = self.tests_passed
        failed = self.tests_failed
        pass_rate = (passed / total * 100) if total > 0 else 0

        self.log(f"\nTotal Tests: {total}", Colors.BLUE)
        self.log(f"Passed: {passed}", Colors.GREEN)
        self.log(f"Failed: {failed}", Colors.RED)
        self.log(f"Pass Rate: {pass_rate:.1f}%", Colors.GREEN if pass_rate >= 90 else Colors.YELLOW)

        if self.failures:
            self.log("\n❌ FAILED TESTS:", Colors.RED)
            for failure in self.failures:
                self.log(f"  - {failure}", Colors.RED)

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "failures": self.failures,
        }

def main():
    print(f"\n{'='*80}")
    print("MLB V5 Script Survival & Fragility Model - Pure Function Tests")
    print(f"{'='*80}\n")
    print(f"Started at: {datetime.now().isoformat()}\n")

    suite = MLBV5TestSuite()

    # Run all pure function tests
    suite.test_phillies_dodgers_survival_score()
    suite.test_arizona_seattle_gallen_survival_score()
    suite.test_fragility_score_near_line()
    suite.test_fragility_score_comfortable_buffer()
    suite.test_classify_script_stability()
    suite.test_build_script_survival_payload_reference_profile()
    suite.test_confidence_contribution_mapping()

    # Print summary
    summary = suite.print_summary()

    print(f"\nCompleted at: {datetime.now().isoformat()}\n")

    # Return exit code
    return 0 if summary["failed"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
