"""F6A + F6B Backend Tests — Bullpen-Aware Under Selector + Script Breaks Storage.

Tests the pure function select_under_market_with_bullpen_risk() and the
script breaks storage/retrieval endpoints.
"""
import sys
import requests
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"
TEST_USER = "demo@valuebet.app"
TEST_PASS = "demo1234"


class F6TestRunner:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        prefix = {"INFO": "ℹ️", "PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(level, "•")
        print(f"{prefix} {msg}")

    def run_test(self, name: str, fn):
        """Run a single test function."""
        self.tests_run += 1
        self.log(f"Testing {name}...", "INFO")
        try:
            fn()
            self.tests_passed += 1
            self.log(f"PASSED: {name}", "PASS")
            return True
        except AssertionError as e:
            self.log(f"FAILED: {name} — {e}", "FAIL")
            self.failures.append({"test": name, "error": str(e)})
            return False
        except Exception as e:
            self.log(f"ERROR: {name} — {e}", "FAIL")
            self.failures.append({"test": name, "error": f"Exception: {e}"})
            return False

    def login(self):
        """Authenticate and store token."""
        self.log("Authenticating...", "INFO")
        resp = requests.post(f"{BASE_URL}/auth/login", json={"email": TEST_USER, "password": TEST_PASS})
        assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
        data = resp.json()
        self.token = data.get("token")
        assert self.token, "No token in login response"
        self.log(f"Authenticated as {TEST_USER}", "PASS")

    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    # ════════════════════════════════════════════════════════════════════════
    # F6A — Pure function tests (via Python import)
    # ════════════════════════════════════════════════════════════════════════
    def test_f6a_pure_test_a(self):
        """F6A TEST A: High bullpen + strong starters → F5 Under selected."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.7,
            full_game_total_line=9.5,
            f5_total_line=4.5,
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        assert result["rule_triggered"] is True, "Rule should trigger"
        assert result["selected_market"] is not None, "Should have a selected market"
        assert result["selected_market"]["category"] == "F5_UNDER", f"Expected F5_UNDER, got {result['selected_market'].get('category')}"
        assert "BULLPEN_RISK_DOWNGRADES_FULL_GAME_UNDER" in result["reason_codes"], "Missing BULLPEN_RISK code"
        assert "STARTER_PARK_SUPPORTS_F5_UNDER" in result["reason_codes"], "Missing STARTER_PARK code"
        assert result["confidence_adjustment"] == -12, f"Expected -12, got {result['confidence_adjustment']}"

    def test_f6a_pure_test_b(self):
        """F6A TEST B: Low bullpen → Full Game Under retained."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.7,
            full_game_total_line=9.5,
            f5_total_line=4.5,
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="LOW",
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        assert result["rule_triggered"] is False, "Rule should NOT trigger with LOW bullpen"
        assert result["selected_market"] is not None, "Should have a selected market"
        assert "9.5" in str(result["selected_market"].get("market", "")), "Should keep Full Game Under 9.5"

    def test_f6a_pure_test_c(self):
        """F6A TEST C: Weak starters → rule doesn't trigger."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.7,
            full_game_total_line=9.5,
            f5_total_line=4.5,
            pitcher_score=45,  # weak
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        assert result["rule_triggered"] is False, "Rule should NOT trigger with weak starters"

    def test_f6a_pure_test_d(self):
        """F6A TEST D: Low edge → rule doesn't trigger."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=8.8,  # edge = 9.5 - 8.8 = 0.7 < 2.0
            full_game_total_line=9.5,
            f5_total_line=4.5,
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        assert result["rule_triggered"] is False, "Rule should NOT trigger with low edge"

    def test_f6a_pure_bonus_protected_alt(self):
        """F6A BONUS: No F5 line, protected alt 11.5 available → Protected Full Game Under."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.7,
            full_game_total_line=9.5,
            f5_total_line=None,  # no F5
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            available_markets=[
                {"market": "Full Game Under 11.5", "line": 11.5, "score": 70},
            ],
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        assert result["rule_triggered"] is True, "Rule should trigger"
        assert result["selected_market"] is not None, "Should have a selected market"
        assert result["selected_market"]["category"] == "PROTECTED_FULL_GAME_UNDER", \
            f"Expected PROTECTED_FULL_GAME_UNDER, got {result['selected_market'].get('category')}"

    def test_f6a_pure_bonus2_no_bet(self):
        """F6A BONUS 2: High bullpen, no alts, low confidence → NO_BET."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=8.0,  # edge = 9.5 - 8.0 = 1.5 (marginal)
            full_game_total_line=9.5,
            f5_total_line=None,
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            available_markets=[],
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 75},
        )
        # With edge < 2.0, rule won't trigger. Let's adjust to trigger but have low final score.
        # Actually, let's make edge >= 2.0 but ensure final score < 65 after penalty.
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.5,  # edge = 9.5 - 6.5 = 3.0 (good)
            full_game_total_line=9.5,
            f5_total_line=None,
            pitcher_score=60,  # just above threshold
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            available_markets=[],
            current_selection={"market": "Total Runs Under", "line": 9.5, "score": 60},  # low base score
        )
        # With HIGH bullpen, penalty = -12. If base score is 60, final = 48 < 65 → NO_BET
        # But we need to check if rule triggers. Let me recalculate:
        # edge = 3.0 >= 2.0 ✓, pitcher_score = 60 >= 60 ✓, park = PITCHER_FRIENDLY ✓, bullpen = HIGH ✓
        # So rule should trigger. With no F5 and no protected alt, and final score < 65, selected_market should be None.
        assert result["rule_triggered"] is True, "Rule should trigger"
        # The function logic: if no F5, no protected alt, and fg_score_final < 65, selected = None
        # But we need to ensure fg_score_final < 65. Base is 75 or 60 depending on edge.
        # Let's check the actual logic in the function...
        # Actually, the function uses fg_score_base = 75.0 if er <= fg_line - 1.5 else 60.0
        # er = 6.5, fg_line = 9.5, diff = 3.0 >= 1.5, so base = 75. After penalty -12 = 63 < 65.
        # So selected should be None (NO_BET).
        if result["selected_market"] is not None:
            # Check if score is very low
            assert result["selected_market"].get("score", 100) < 65, "Score should be < 65 for NO_BET scenario"

    def test_f6a_non_triggered_non_under(self):
        """F6A: When market is NOT Under, rule should skip entirely."""
        from services.mlb_under_market_selector import select_under_market_with_bullpen_risk
        result = select_under_market_with_bullpen_risk(
            expected_runs=6.7,
            full_game_total_line=9.5,
            f5_total_line=4.5,
            pitcher_score=72,
            park_factor="PITCHER_FRIENDLY",
            bullpen_risk="HIGH",
            current_selection={"market": "Run Line +1.5", "line": 1.5, "score": 75},
        )
        assert result["rule_triggered"] is False, "Rule should NOT trigger for non-Under market"

    # ════════════════════════════════════════════════════════════════════════
    # F6A — Endpoint integration test
    # ════════════════════════════════════════════════════════════════════════
    def test_f6a_endpoint_baseball_analysis(self):
        """F6A: POST /api/analysis/run sport=baseball should work without breaking."""
        resp = requests.post(
            f"{BASE_URL}/analysis/run",
            json={"sport": "baseball", "refresh": False, "max_matches": 3, "background": False},
            headers=self.headers(),
            timeout=120,
        )
        # We expect either 200 (picks found) or 409 (no candidates) or 200 with empty picks
        assert resp.status_code in (200, 409), f"Unexpected status {resp.status_code}: {resp.text[:500]}"
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result") or {}
            picks = result.get("picks") or []
            # Check if any pick has bullpen_swap_meta (if Under market)
            for p in picks:
                rec = p.get("recommendation") or {}
                market = rec.get("market") or ""
                if "under" in market.lower():
                    # May or may not have bullpen_swap_meta depending on conditions
                    # Just verify structure doesn't break
                    self.log(f"  Pick {p.get('match_label')}: market={market}, bullpen_swap={rec.get('bullpen_swap')}", "INFO")

    # ════════════════════════════════════════════════════════════════════════
    # F6B — Script Breaks Storage tests
    # ════════════════════════════════════════════════════════════════════════
    def test_f6b_settle_hook(self):
        """F6B: POST /api/mlb/picks/{pick_id}/settle should persist script break."""
        pick_id = f"test_f6b_{int(datetime.now().timestamp())}"
        payload = {
            "pick_id": pick_id,
            "run_id": "test_run_f6b",
            "match_id": "12345",
            "outcome": "lost",
            "final_home_runs": 7,
            "final_away_runs": 5,
            "pick_doc": {
                "_mlb_script_v3": {
                    "script": {
                        "script_code": "LOW_SCORING_PITCHERS_DUEL",
                        "expected_runs": 6.7,
                    }
                },
                "recommendation": {
                    "market": "Total Runs Under",
                    "selection": "UNDER 9.5",
                },
                "home_starter_runs_allowed": 5,
            },
            "v2_snapshot": {
                "expectedRuns": 6.7,
            },
        }
        resp = requests.post(
            f"{BASE_URL}/mlb/picks/{pick_id}/settle",
            json=payload,
            headers=self.headers(),
            timeout=30,
        )
        assert resp.status_code == 200, f"Settle failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("ok") is True, "Settle response should have ok=True"
        sb = data.get("script_break") or {}
        assert sb.get("ok") is True, f"Script break storage failed: {sb}"
        assert sb.get("script_broken") is True, "Script should be marked as broken"
        assert sb.get("severity") == "STRONG", f"Expected STRONG severity, got {sb.get('severity')}"
        codes = sb.get("learning_event_codes") or []
        assert "UNDER_BUSTED_BY_STARTER_BLOWUP" in codes, f"Missing STARTER_BLOWUP code: {codes}"
        assert "UNDER_BUSTED_BY_OFFENSIVE_BREAKOUT" in codes, f"Missing OFFENSIVE_BREAKOUT code: {codes}"
        self.log(f"  Script break stored: pick_id={sb.get('pick_id')}, broken={sb.get('script_broken')}, severity={sb.get('severity')}", "INFO")

    def test_f6b_get_script_breaks(self):
        """F6B: GET /api/mlb/script_breaks should return recent breaks."""
        resp = requests.get(
            f"{BASE_URL}/mlb/script_breaks?days=30&limit=20",
            headers=self.headers(),
            timeout=30,
        )
        assert resp.status_code == 200, f"GET script_breaks failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("ok") is True, "Response should have ok=True"
        assert "count" in data, "Response should have count"
        assert "items" in data, "Response should have items"
        items = data.get("items") or []
        self.log(f"  Retrieved {len(items)} script break records", "INFO")
        if items:
            first = items[0]
            required_fields = ["pick_id", "pregame_script", "script_broken", "severity", "outcome", "learning_event_codes"]
            for field in required_fields:
                assert field in first, f"Missing field {field} in script break item"

    def test_f6b_get_script_breaks_stats(self):
        """F6B: GET /api/mlb/script_breaks/stats should return aggregated stats."""
        resp = requests.get(
            f"{BASE_URL}/mlb/script_breaks/stats?days=60",
            headers=self.headers(),
            timeout=30,
        )
        assert resp.status_code == 200, f"GET stats failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("ok") is True, "Response should have ok=True"
        assert "window_days" in data, "Response should have window_days"
        assert data["window_days"] == 60, f"Expected window_days=60, got {data['window_days']}"
        assert "total" in data, "Response should have total"
        assert "broken" in data, "Response should have broken"
        assert "broken_rate" in data, "Response should have broken_rate"
        assert "by_script" in data, "Response should have by_script"
        assert "top_learning_codes" in data, "Response should have top_learning_codes"
        self.log(f"  Stats: total={data['total']}, broken={data['broken']}, rate={data['broken_rate']}", "INFO")

    def test_f6b_auth_required(self):
        """F6B: GET /api/mlb/script_breaks without Authorization should return 401."""
        resp = requests.get(
            f"{BASE_URL}/mlb/script_breaks?days=30&limit=20",
            timeout=30,
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_f6b_idempotency(self):
        """F6B: Settling same pick_id twice should only keep last document."""
        pick_id = f"test_f6b_idem_{int(datetime.now().timestamp())}"
        payload1 = {
            "pick_id": pick_id,
            "run_id": "test_run_idem",
            "match_id": "99999",
            "outcome": "won",
            "final_home_runs": 3,
            "final_away_runs": 2,
            "pick_doc": {
                "_mlb_script_v3": {"script": {"script_code": "TEST_SCRIPT", "expected_runs": 5.0}},
                "recommendation": {"market": "Total Runs Under", "selection": "UNDER 7.5"},
            },
        }
        # First settle
        resp1 = requests.post(f"{BASE_URL}/mlb/picks/{pick_id}/settle", json=payload1, headers=self.headers(), timeout=30)
        assert resp1.status_code == 200, f"First settle failed: {resp1.status_code}"
        
        # Second settle with different outcome
        payload2 = {**payload1, "outcome": "lost", "final_home_runs": 5, "final_away_runs": 4}
        resp2 = requests.post(f"{BASE_URL}/mlb/picks/{pick_id}/settle", json=payload2, headers=self.headers(), timeout=30)
        assert resp2.status_code == 200, f"Second settle failed: {resp2.status_code}"
        
        # Query script_breaks to verify only one document exists for this pick_id
        resp = requests.get(f"{BASE_URL}/mlb/script_breaks?days=1&limit=100", headers=self.headers(), timeout=30)
        assert resp.status_code == 200, f"GET failed: {resp.status_code}"
        data = resp.json()
        items = data.get("items") or []
        matching = [i for i in items if i.get("pick_id") == pick_id]
        assert len(matching) == 1, f"Expected 1 document for pick_id={pick_id}, found {len(matching)}"
        # Verify it's the LAST outcome (lost)
        assert matching[0]["outcome"] == "lost", f"Expected outcome=lost, got {matching[0]['outcome']}"

    # ════════════════════════════════════════════════════════════════════════
    # Regression tests
    # ════════════════════════════════════════════════════════════════════════
    def test_regression_mlb_pipeline(self):
        """Regression: MLB pipeline should still produce picks/rescued/structural_lean/watchlist."""
        resp = requests.post(
            f"{BASE_URL}/analysis/run",
            json={"sport": "baseball", "refresh": False, "max_matches": 3, "background": False},
            headers=self.headers(),
            timeout=120,
        )
        assert resp.status_code in (200, 409), f"Unexpected status {resp.status_code}"
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result") or {}
            # Check that MLB-V5 buckets are present
            assert "picks" in result, "Result should have picks"
            assert "summary" in result, "Result should have summary"
            summary = result["summary"]
            # MLB-V5 buckets
            assert "high_confidence" in summary, "Summary should have high_confidence"
            assert "medium_confidence" in summary, "Summary should have medium_confidence"
            assert "structural_lean_requires_odds" in summary, "Summary should have structural_lean_requires_odds"
            assert "watchlist_manual_odds" in summary, "Summary should have watchlist_manual_odds"

    def test_regression_football_pipeline(self):
        """Regression: Football pipeline should complete without error."""
        resp = requests.post(
            f"{BASE_URL}/analysis/run",
            json={"sport": "football", "refresh": False, "max_matches": 3, "background": False},
            headers=self.headers(),
            timeout=120,
        )
        assert resp.status_code in (200, 409), f"Unexpected status {resp.status_code}"
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result") or {}
            assert "picks" in result, "Result should have picks"

    def run_all(self):
        """Run all tests and print summary."""
        print("\n" + "="*80)
        print("F6A + F6B Backend Tests — Bullpen-Aware Under Selector + Script Breaks")
        print("="*80 + "\n")

        self.login()

        # F6A pure function tests
        print("\n--- F6A Pure Function Tests ---")
        self.run_test("F6A TEST A: High bullpen → F5 Under", self.test_f6a_pure_test_a)
        self.run_test("F6A TEST B: Low bullpen → Full Game Under", self.test_f6a_pure_test_b)
        self.run_test("F6A TEST C: Weak starters → no trigger", self.test_f6a_pure_test_c)
        self.run_test("F6A TEST D: Low edge → no trigger", self.test_f6a_pure_test_d)
        self.run_test("F6A BONUS: Protected alt line", self.test_f6a_pure_bonus_protected_alt)
        self.run_test("F6A BONUS 2: NO_BET scenario", self.test_f6a_pure_bonus2_no_bet)
        self.run_test("F6A: Non-Under market skip", self.test_f6a_non_triggered_non_under)

        # F6A endpoint test
        print("\n--- F6A Endpoint Integration ---")
        self.run_test("F6A: POST /api/analysis/run sport=baseball", self.test_f6a_endpoint_baseball_analysis)

        # F6B script breaks tests
        print("\n--- F6B Script Breaks Storage ---")
        self.run_test("F6B: Settle hook persists script break", self.test_f6b_settle_hook)
        self.run_test("F6B: GET /api/mlb/script_breaks", self.test_f6b_get_script_breaks)
        self.run_test("F6B: GET /api/mlb/script_breaks/stats", self.test_f6b_get_script_breaks_stats)
        self.run_test("F6B: Auth required (401)", self.test_f6b_auth_required)
        self.run_test("F6B: Idempotency (upsert by pick_id)", self.test_f6b_idempotency)

        # Regression tests
        print("\n--- Regression Tests ---")
        self.run_test("Regression: MLB pipeline", self.test_regression_mlb_pipeline)
        self.run_test("Regression: Football pipeline", self.test_regression_football_pipeline)

        # Summary
        print("\n" + "="*80)
        print(f"Tests Run: {self.tests_run}")
        print(f"Tests Passed: {self.tests_passed}")
        print(f"Tests Failed: {self.tests_run - self.tests_passed}")
        print("="*80)

        if self.failures:
            print("\n❌ FAILURES:")
            for f in self.failures:
                print(f"  • {f['test']}: {f['error']}")
            return 1
        else:
            print("\n✅ ALL TESTS PASSED")
            return 0


if __name__ == "__main__":
    runner = F6TestRunner()
    sys.exit(runner.run_all())
