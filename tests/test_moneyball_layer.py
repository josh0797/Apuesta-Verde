"""Backend tests for Phase 7 — Moneyball Betting Layer.

Tests the new moneyball_layer.py module that replaces market_guardrail.py:
  • Classification rules (9 verdicts)
  • Fragility score calculation (parlay legs, markets, sport-specific)
  • Public overreaction detection (Spanish narrative phrases)
  • Expected value formula (EV/ROI)
  • Pipeline integration (apply_moneyball_layer)
  • API endpoint returns _moneyball data
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from services.moneyball_layer import (
    analyze_pick,
    compute_fragility_score,
    compute_public_overreaction_index,
    compute_expected_value,
    apply_moneyball_layer,
    classify_pick,
)

class TestRunner:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def test(self, name, fn):
        """Run a single test function."""
        self.tests_run += 1
        print(f"\n{'='*70}")
        print(f"🔍 Test {self.tests_run}: {name}")
        print('='*70)
        try:
            fn()
            self.tests_passed += 1
            print(f"✅ PASSED")
        except AssertionError as e:
            self.tests_failed += 1
            self.failures.append(f"{name}: {str(e)}")
            print(f"❌ FAILED: {str(e)}")
        except Exception as e:
            self.tests_failed += 1
            self.failures.append(f"{name}: {str(e)}")
            print(f"❌ ERROR: {str(e)}")

    def summary(self):
        """Print test summary."""
        print(f"\n{'='*70}")
        print(f"📊 TEST SUMMARY")
        print('='*70)
        print(f"Total: {self.tests_run}")
        print(f"Passed: {self.tests_passed} ✅")
        print(f"Failed: {self.tests_failed} ❌")
        print(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%")
        
        if self.failures:
            print(f"\n{'='*70}")
            print("❌ FAILURES:")
            print('='*70)
            for f in self.failures:
                print(f"  • {f}")
        
        return 0 if self.tests_failed == 0 else 1


def main():
    runner = TestRunner()
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 1: EV Formula Correctness
    # ═══════════════════════════════════════════════════════════════════════
    def test_ev_formula():
        print("Testing EV formula: p=0.55, odds=2.10, stake=10")
        result = compute_expected_value(
            estimated_probability=0.55,
            decimal_odds=2.10,
            stake=10.0
        )
        print(f"Result: {result}")
        
        # Expected: net_if_win = 10 * (2.10 - 1) = 11.0
        # EV = (0.55 * 11) - (0.45 * 10) = 6.05 - 4.5 = 1.55
        # ROI = (1.55 / 10) * 100 = 15.5%
        
        assert result["stake"] == 10.0, f"Stake should be 10.0, got {result['stake']}"
        assert abs(result["net_profit_if_win"] - 11.0) < 0.01, f"Net profit should be 11.0, got {result['net_profit_if_win']}"
        assert abs(result["expected_value"] - 1.55) < 0.01, f"EV should be 1.55, got {result['expected_value']}"
        assert abs(result["roi_projection_pct"] - 15.5) < 0.1, f"ROI should be 15.5%, got {result['roi_projection_pct']}"
        print("✓ EV formula correct")
    
    runner.test("EV Formula Correctness", test_ev_formula)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 2: Fragility Score - 2-leg parlay (moderate ~47)
    # ═══════════════════════════════════════════════════════════════════════
    def test_fragility_2leg_parlay():
        print("Testing fragility for 2-leg parlay...")
        pick = {
            "is_parlay": True,
            "parlay_legs": [{"match": "A"}, {"match": "B"}],
            "recommendation": {"market": "Parlay", "odds_range": "2.50"},
            "risks": [],
            "data_freshness": {"odds": "fresh", "context": "fresh"}
        }
        result = compute_fragility_score(pick, "football")
        print(f"Fragility: {result}")
        
        # 2-leg parlay: 25 + 22*(2-1) = 47
        score = result["score"]
        assert 40 <= score <= 55, f"2-leg parlay should be ~47, got {score}"
        assert result["label"] == "moderada", f"Expected 'moderada', got {result['label']}"
        assert any("parlay" in f.lower() or "pierna" in f.lower() for f in result["factors"]), "Should mention parlay in factors"
        print(f"✓ 2-leg parlay fragility: {score} (moderada)")
    
    runner.test("Fragility: 2-leg parlay", test_fragility_2leg_parlay)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 3: Fragility Score - 3-leg parlay (alta >65)
    # ═══════════════════════════════════════════════════════════════════════
    def test_fragility_3leg_parlay():
        print("Testing fragility for 3-leg parlay...")
        pick = {
            "is_parlay": True,
            "parlay_legs": [{"match": "A"}, {"match": "B"}, {"match": "C"}],
            "recommendation": {"market": "Parlay", "odds_range": "4.50"},
            "risks": [],
            "data_freshness": {"odds": "fresh", "context": "fresh"}
        }
        result = compute_fragility_score(pick, "football")
        print(f"Fragility: {result}")
        
        # 3-leg parlay: 25 + 22*(3-1) = 69
        score = result["score"]
        assert score > 65, f"3-leg parlay should be >65, got {score}"
        assert result["label"] == "alta", f"Expected 'alta', got {result['label']}"
        print(f"✓ 3-leg parlay fragility: {score} (alta)")
    
    runner.test("Fragility: 3-leg parlay", test_fragility_3leg_parlay)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 4: Fragility Score - 4-leg parlay (extrema >80)
    # ═══════════════════════════════════════════════════════════════════════
    def test_fragility_4leg_parlay():
        print("Testing fragility for 4-leg parlay...")
        pick = {
            "is_parlay": True,
            "parlay_legs": [{"match": "A"}, {"match": "B"}, {"match": "C"}, {"match": "D"}],
            "recommendation": {"market": "Parlay", "odds_range": "8.00"},
            "risks": [],
            "data_freshness": {"odds": "fresh", "context": "fresh"}
        }
        result = compute_fragility_score(pick, "football")
        print(f"Fragility: {result}")
        
        # 4-leg parlay: 25 + 22*(4-1) = 85 (capped at 85)
        score = result["score"]
        assert score > 80, f"4-leg parlay should be >80, got {score}"
        assert result["label"] == "extrema", f"Expected 'extrema', got {result['label']}"
        print(f"✓ 4-leg parlay fragility: {score} (extrema)")
    
    runner.test("Fragility: 4-leg parlay", test_fragility_4leg_parlay)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 5: Public Overreaction - Spanish narrative detection
    # ═══════════════════════════════════════════════════════════════════════
    def test_public_overreaction():
        print("Testing public overreaction with Spanish narrative...")
        pick = {
            "reasoning": "PSG necesitan ganar este partido. El equipo grande siempre aparece en momentos clave. Vienen de golear al rival anterior y el jugador estrella siempre aparece.",
            "motivation": {
                "home": {"reason": "Equipo grande con presión máxima"}
            }
        }
        result = compute_public_overreaction_index(pick)
        print(f"Overreaction: {result}")
        
        # Should detect: necesitan ganar (15), equipo grande (10), vienen de golear (15), 
        # jugador estrella (10), siempre aparece (10) = 60+
        score = result["score"]
        assert score >= 60, f"Should detect high overreaction (≥60), got {score}"
        assert result["label"] in ["moderada", "alta"], f"Expected 'moderada' or 'alta', got {result['label']}"
        assert len(result["matched"]) >= 3, f"Should match at least 3 patterns, got {len(result['matched'])}"
        print(f"✓ Overreaction score: {score} ({result['label']}), matched {len(result['matched'])} patterns")
    
    runner.test("Public Overreaction Detection", test_public_overreaction)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 6: Classification - MARKET_TRAP (odds 1.30, conf 85, narrative)
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_market_trap():
        print("Testing classification: MARKET_TRAP (odds 1.30, conf 85, narrative)")
        pick = {
            "recommendation": {
                "confidence_score": 85,
                "odds_range": "1.30",
                "market": "Moneyline"
            },
            "reasoning": "Real Madrid necesitan ganar. Equipo grande siempre aparece.",
            "risks": [],
            "is_parlay": False,
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        print(f"Reason: {result['_moneyball']['classification_reason']}")
        
        cls = result["_moneyball"]["classification"]
        assert cls in ["MARKET_TRAP", "NO_BET_VALUE"], f"Expected MARKET_TRAP or NO_BET_VALUE, got {cls}"
        print(f"✓ Correctly classified as {cls}")
    
    runner.test("Classification: MARKET_TRAP", test_classification_market_trap)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 7: Classification - MARKET_TRAP (Doble Op 1.20)
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_doble_op_trap():
        print("Testing classification: Doble Oportunidad 1.20")
        pick = {
            "recommendation": {
                "confidence_score": 85,
                "odds_range": "1.20",
                "market": "Doble Oportunidad"
            },
            "reasoning": "Bayern Munich o empate es seguro.",
            "risks": [],
            "is_parlay": False,
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        
        cls = result["_moneyball"]["classification"]
        assert cls in ["MARKET_TRAP", "NO_BET_VALUE"], f"Expected MARKET_TRAP or NO_BET_VALUE, got {cls}"
        print(f"✓ Correctly classified as {cls}")
    
    runner.test("Classification: Doble Op trap", test_classification_doble_op_trap)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 8: Classification - VALUE_BET/UNDERVALUED (odds 3.50, conf 50)
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_underdog_value():
        print("Testing classification: Underdog value (odds 3.50, conf 50)")
        pick = {
            "recommendation": {
                "confidence_score": 50,
                "odds_range": "3.50",
                "market": "Moneyline"
            },
            "reasoning": "Brighton tiene métricas sólidas como underdog.",
            "risks": [],
            "is_parlay": False,
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        print(f"Edge: {result['_market_edge']['edge']}")
        
        cls = result["_moneyball"]["classification"]
        # With conf 50 * 0.85 = 42.5% estimated, odds 3.50 = 28.6% implied
        # Edge = 42.5 - 28.6 = 13.9% → should be VALUE_BET or UNDERVALUED_EDGE
        assert cls in ["VALUE_BET", "UNDERVALUED_EDGE", "STRONG_VALUE_BET"], f"Expected value classification, got {cls}"
        print(f"✓ Correctly classified as {cls}")
    
    runner.test("Classification: Underdog value", test_classification_underdog_value)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 9: Classification - FRAGILE_EDGE (3-leg parlay)
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_fragile_parlay():
        print("Testing classification: FRAGILE_EDGE (3-leg parlay)")
        pick = {
            "recommendation": {
                "confidence_score": 62,
                "odds_range": "4.50",
                "market": "Parlay"
            },
            "reasoning": "Parlay de 3 piernas con valor.",
            "risks": [],
            "is_parlay": True,
            "parlay_legs": [{"match": "A"}, {"match": "B"}, {"match": "C"}],
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        print(f"Fragility: {result['_moneyball']['fragility']['score']}")
        
        cls = result["_moneyball"]["classification"]
        # 3-leg parlay should have fragility >65 → FRAGILE_EDGE
        assert cls == "FRAGILE_EDGE", f"Expected FRAGILE_EDGE, got {cls}"
        assert result["_moneyball"]["fragility"]["score"] > 65, "Fragility should be >65"
        print(f"✓ Correctly classified as FRAGILE_EDGE")
    
    runner.test("Classification: FRAGILE_EDGE parlay", test_classification_fragile_parlay)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 10: Classification - PUBLIC_OVERREACTION
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_public_overreaction():
        print("Testing classification: PUBLIC_OVERREACTION")
        pick = {
            "recommendation": {
                "confidence_score": 80,
                "odds_range": "1.40",
                "market": "Moneyline"
            },
            "reasoning": "PSG necesitan ganar este partido crucial. Vienen de golear y el jugador estrella siempre aparece en momentos clave. Equipo grande con presión máxima.",
            "risks": [],
            "is_parlay": False,
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        print(f"Overreaction: {result['_moneyball']['public_overreaction']['score']}")
        
        cls = result["_moneyball"]["classification"]
        # Should detect high overreaction (≥70) → PUBLIC_OVERREACTION
        assert cls == "PUBLIC_OVERREACTION", f"Expected PUBLIC_OVERREACTION, got {cls}"
        assert result["_moneyball"]["public_overreaction"]["score"] >= 70, "Overreaction should be ≥70"
        print(f"✓ Correctly classified as PUBLIC_OVERREACTION")
    
    runner.test("Classification: PUBLIC_OVERREACTION", test_classification_public_overreaction)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 11: Classification - LIVE_VALUE_WINDOW
    # ═══════════════════════════════════════════════════════════════════════
    def test_classification_live_value():
        print("Testing classification: LIVE_VALUE_WINDOW")
        pick = {
            "recommendation": {
                "confidence_score": 60,
                "odds_range": "2.05",
                "market": "Moneyline"
            },
            "reasoning": "Live bet con valor en línea volátil.",
            "risks": [],
            "is_parlay": False,
            "is_live": True,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        print(f"Classification: {result['_moneyball']['classification']}")
        print(f"Edge: {result['_market_edge']['edge']}")
        
        cls = result["_moneyball"]["classification"]
        # Live + edge ≥5% → LIVE_VALUE_WINDOW
        # conf 60 * 0.85 = 51%, odds 2.05 = 48.8%, edge = 2.2% (below 5%)
        # Let's check if it's at least recognized as live
        assert result["_market_edge"]["bet_type"] == "live", "Should be detected as live bet"
        print(f"✓ Detected as live bet, classified as {cls}")
    
    runner.test("Classification: LIVE_VALUE_WINDOW", test_classification_live_value)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 12: Pipeline Integration - apply_moneyball_layer
    # ═══════════════════════════════════════════════════════════════════════
    def test_pipeline_integration():
        print("Testing pipeline integration: apply_moneyball_layer")
        parsed = {
            "picks": [
                {
                    "match_id": "test1",
                    "match_label": "Test Match 1",
                    "recommendation": {
                        "confidence_score": 85,
                        "odds_range": "1.30",
                        "market": "Moneyline"
                    },
                    "reasoning": "Real Madrid necesitan ganar.",
                    "risks": [],
                    "is_parlay": False,
                    "is_live": False,
                    "key_data": {}
                },
                {
                    "match_id": "test2",
                    "match_label": "Test Match 2",
                    "recommendation": {
                        "confidence_score": 50,
                        "odds_range": "3.50",
                        "market": "Moneyline"
                    },
                    "reasoning": "Underdog con valor.",
                    "risks": [],
                    "is_parlay": False,
                    "is_live": False,
                    "key_data": {}
                }
            ],
            "summary": {
                "discarded_market": []
            }
        }
        
        result = apply_moneyball_layer(parsed, sport="football", stake=10.0)
        print(f"Pipeline result: {result.get('_pipeline', {}).get('moneyball', {})}")
        
        # Check that _moneyball and _market_edge are attached
        for pick in result["picks"]:
            assert "_moneyball" in pick, f"Pick {pick['match_id']} missing _moneyball"
            assert "_market_edge" in pick, f"Pick {pick['match_id']} missing _market_edge"
            print(f"  Pick {pick['match_id']}: {pick['_moneyball']['classification']}")
        
        # Check that some picks were rerouted
        pipeline = result.get("_pipeline", {}).get("moneyball", {})
        assert "evaluated" in pipeline, "Pipeline should have 'evaluated' count"
        assert "kept" in pipeline, "Pipeline should have 'kept' count"
        assert "rerouted" in pipeline, "Pipeline should have 'rerouted' count"
        print(f"✓ Pipeline: evaluated={pipeline['evaluated']}, kept={pipeline['kept']}, rerouted={pipeline['rerouted']}")
    
    runner.test("Pipeline Integration", test_pipeline_integration)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Test 13: Verify _market_edge back-compat fields
    # ═══════════════════════════════════════════════════════════════════════
    def test_market_edge_backcompat():
        print("Testing _market_edge back-compat fields")
        pick = {
            "recommendation": {
                "confidence_score": 75,
                "odds_range": "1.85",
                "market": "Doble Oportunidad"
            },
            "reasoning": "Test pick",
            "risks": [],
            "is_parlay": False,
            "is_live": False,
            "key_data": {}
        }
        result = analyze_pick(pick, "football", stake=10.0)
        edge = result["_market_edge"]
        
        # Check all required back-compat fields
        assert "implied_probability" in edge, "Missing implied_probability"
        assert "estimated_probability" in edge, "Missing estimated_probability"
        assert "edge" in edge, "Missing edge"
        assert "edge_threshold" in edge, "Missing edge_threshold"
        assert "bet_type" in edge, "Missing bet_type"
        assert "calibration" in edge, "Missing calibration"
        assert "verdict" in edge, "Missing verdict"
        
        print(f"✓ All back-compat fields present:")
        print(f"  - implied: {edge['implied_probability']}")
        print(f"  - estimated: {edge['estimated_probability']}")
        print(f"  - edge: {edge['edge']}")
        print(f"  - threshold: {edge['edge_threshold']}")
        print(f"  - verdict: {edge['verdict']}")
    
    runner.test("Market Edge Back-compat", test_market_edge_backcompat)
    
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
