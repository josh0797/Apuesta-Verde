"""Comprehensive backend test for Value Bet Intelligence Phase 3 features.

Tests:
1. AUTH - login, register, /me
2. ANALYSIS provider chain - POST /api/analysis/run with _provider field
3. SYSTEM STATUS - scheduler, providers
4. FALLBACK SOURCES - ESPN, Sofascore, SportyTrader
5. FILTERS - /api/picks/today/filtered
6. CSV EXPORT - /api/picks/today/export.csv, /api/picks/tracked/export.csv
7. TIMELINE - /api/stats/timeline
8. META LEAGUES - /api/meta/leagues
9. EXISTING ENDPOINTS - matches, picks, tracking, stats
10. AUTHZ - 401 without token
"""
import requests
import sys
import time
import csv
import io
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class Phase3Tester:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {level}: {msg}")

    def test(self, name: str, method: str, endpoint: str, expected_status: int, 
             data=None, headers=None, check_fn=None, timeout=30):
        """Run a single test."""
        url = f"{BASE_URL}/{endpoint}"
        if headers is None:
            headers = {}
        if self.token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            elif method == "PATCH":
                resp = requests.patch(url, json=data, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            # Check status code
            if resp.status_code != expected_status:
                self.tests_failed += 1
                msg = f"❌ FAILED: Expected {expected_status}, got {resp.status_code}"
                self.log(msg, "ERROR")
                self.log(f"   Response: {resp.text[:500]}", "ERROR")
                self.failures.append({"test": name, "reason": msg, "response": resp.text[:500]})
                return False, None

            # Parse response
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                result = resp.json()
            elif resp.headers.get("Content-Type", "").startswith("text/csv"):
                result = resp.text
            else:
                result = resp.text

            # Run custom check function
            if check_fn:
                check_result = check_fn(result)
                if not check_result:
                    self.tests_failed += 1
                    msg = f"❌ FAILED: Custom check failed"
                    self.log(msg, "ERROR")
                    self.failures.append({"test": name, "reason": msg, "response": str(result)[:500]})
                    return False, result

            self.tests_passed += 1
            self.log(f"✅ PASSED", "SUCCESS")
            return True, result

        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": msg, "response": ""})
            return False, None

    def run_all_tests(self):
        """Execute all Phase 3 tests."""
        self.log("=" * 80)
        self.log("PHASE 3 BACKEND TESTING - Value Bet Intelligence")
        self.log("=" * 80)

        # ═══════════════════════════════════════════════════════════════════════
        # 1. AUTH TESTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[1] AUTH TESTS", "SECTION")
        
        # Test login with demo user
        success, result = self.test(
            "Login with demo@valuebet.app",
            "POST", "auth/login", 200,
            data={"email": "demo@valuebet.app", "password": "demo1234"},
            check_fn=lambda r: "token" in r and "user" in r
        )
        if success and result:
            self.token = result["token"]
            self.user_id = result["user"]["id"]
            self.log(f"   Token acquired: {self.token[:20]}...")
            self.log(f"   User ID: {self.user_id}")

        # Test /me endpoint
        self.test(
            "GET /api/auth/me",
            "GET", "auth/me", 200,
            check_fn=lambda r: r.get("email") == "demo@valuebet.app"
        )

        # Test register new user
        test_email = f"test_{int(time.time())}@valuebet.app"
        self.test(
            "Register new user",
            "POST", "auth/register", 200,
            data={"email": test_email, "password": "test1234", "name": "Test User"},
            check_fn=lambda r: "token" in r and r["user"]["email"] == test_email
        )

        # ═══════════════════════════════════════════════════════════════════════
        # 2. SYSTEM STATUS TESTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[2] SYSTEM STATUS TESTS", "SECTION")
        
        success, result = self.test(
            "GET /api/system/status",
            "GET", "system/status", 200,
            check_fn=lambda r: (
                "scheduler" in r and 
                "providers" in r and
                r["scheduler"].get("enabled") == True and
                "refresh_upcoming" in r["scheduler"].get("jobs", {}) and
                "refresh_live" in r["scheduler"].get("jobs", {}) and
                "purge_context" in r["scheduler"].get("jobs", {}) and
                r["providers"].get("openai_configured") == True and
                r["providers"].get("emergent_configured") == True and
                r["providers"].get("api_football_configured") == True
            )
        )
        if success and result:
            self.log(f"   Scheduler enabled: {result['scheduler']['enabled']}")
            self.log(f"   Jobs: {list(result['scheduler'].get('jobs', {}).keys())}")
            self.log(f"   Providers: OpenAI={result['providers']['openai_configured']}, "
                    f"Emergent={result['providers']['emergent_configured']}, "
                    f"API-Football={result['providers']['api_football_configured']}")

        # ═══════════════════════════════════════════════════════════════════════
        # 3. FALLBACK SOURCES TESTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[3] FALLBACK SOURCES TESTS", "SECTION")
        
        success, result = self.test(
            "GET /api/system/fallback-sources",
            "GET", "system/fallback-sources", 200,
            timeout=60,
            check_fn=lambda r: (
                "summary" in r and 
                "data" in r and
                "espn" in r["data"] and
                isinstance(r["data"]["espn"], list)
            )
        )
        if success and result:
            summary = result["summary"]
            self.log(f"   ESPN count: {summary.get('espn', 0)}")
            self.log(f"   Sofascore count: {summary.get('sofascore', 0)}")
            self.log(f"   SportyTrader count: {summary.get('sportytrader', 0)}")
            
            # Check ESPN has data
            espn_data = result["data"]["espn"]
            if len(espn_data) > 0:
                sample = espn_data[0]
                self.log(f"   ESPN sample: {sample.get('league')} - {sample.get('home_team', {}).get('name')} vs {sample.get('away_team', {}).get('name')}")
                # Verify ESPN data structure
                if all(k in sample for k in ["id", "source", "league", "kickoff_iso", "home_team", "away_team"]):
                    self.log(f"   ✓ ESPN data structure valid")
                else:
                    self.log(f"   ⚠ ESPN data structure incomplete", "WARN")
            else:
                self.log(f"   ⚠ ESPN returned 0 entries (may be temporary)", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 4. EXISTING ENDPOINTS TESTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[4] EXISTING ENDPOINTS TESTS", "SECTION")
        
        # Test matches/upcoming
        success, result = self.test(
            "GET /api/matches/upcoming",
            "GET", "matches/upcoming", 200,
            check_fn=lambda r: "count" in r and "items" in r
        )
        if success and result:
            self.log(f"   Upcoming matches: {result['count']}")

        # Test matches/live
        success, result = self.test(
            "GET /api/matches/live",
            "GET", "matches/live", 200,
            check_fn=lambda r: "count" in r and "items" in r
        )
        if success and result:
            self.log(f"   Live matches: {result['count']}")

        # Test picks/today
        success, result = self.test(
            "GET /api/picks/today",
            "GET", "picks/today", 200,
            check_fn=lambda r: "pick_run" in r
        )
        if success and result:
            has_picks = result["pick_run"] is not None
            self.log(f"   Has picks: {has_picks}")

        # Test picks/history
        success, result = self.test(
            "GET /api/picks/history",
            "GET", "picks/history", 200,
            check_fn=lambda r: "count" in r and "items" in r
        )
        if success and result:
            self.log(f"   Pick history count: {result['count']}")

        # Test picks/tracked
        success, result = self.test(
            "GET /api/picks/tracked",
            "GET", "picks/tracked", 200,
            check_fn=lambda r: "count" in r and "items" in r
        )
        if success and result:
            self.log(f"   Tracked picks: {result['count']}")

        # Test stats/dashboard
        success, result = self.test(
            "GET /api/stats/dashboard",
            "GET", "stats/dashboard", 200,
            check_fn=lambda r: all(k in r for k in ["total", "won", "lost", "win_rate", "streak"])
        )
        if success and result:
            self.log(f"   Dashboard: {result['won']}/{result['total']} won, {result['win_rate']}% win rate")

        # ═══════════════════════════════════════════════════════════════════════
        # 5. ANALYSIS PROVIDER CHAIN TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[5] ANALYSIS PROVIDER CHAIN TEST", "SECTION")
        self.log("   ⚠ This test may take 20-90 seconds (LLM analysis)...", "WARN")
        
        success, result = self.test(
            "POST /api/analysis/run (refresh=false)",
            "POST", "analysis/run", 200,
            data={"refresh": False, "include_live": True, "max_matches": 4},
            timeout=120,
            check_fn=lambda r: (
                "result" in r and
                "_provider" in r["result"] and
                r["result"]["_provider"] in ["openai:gpt-4o-mini", "emergent:claude-sonnet-4-5"] and
                "verdict" in r["result"] and
                r["result"]["verdict"] in ["value_found", "no_value"]
            )
        )
        if success and result:
            provider = result["result"]["_provider"]
            verdict = result["result"]["verdict"]
            self.log(f"   Provider used: {provider}")
            self.log(f"   Verdict: {verdict}")
            if verdict == "value_found":
                picks = result["result"].get("picks", [])
                self.log(f"   Picks recommended: {len(picks)}")
                if picks:
                    sample = picks[0]
                    self.log(f"   Sample pick: {sample.get('match_label')} - {sample.get('recommendation', {}).get('market')}")

        # ═══════════════════════════════════════════════════════════════════════
        # 6. META LEAGUES TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[6] META LEAGUES TEST", "SECTION")
        
        success, result = self.test(
            "GET /api/meta/leagues",
            "GET", "meta/leagues", 200,
            check_fn=lambda r: "leagues" in r and isinstance(r["leagues"], list)
        )
        if success and result:
            leagues = result["leagues"]
            self.log(f"   Leagues available: {len(leagues)}")
            if leagues:
                self.log(f"   Sample leagues: {', '.join(leagues[:5])}")

        # ═══════════════════════════════════════════════════════════════════════
        # 7. FILTERS TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[7] FILTERS TEST", "SECTION")
        
        success, result = self.test(
            "GET /api/picks/today/filtered (no filters)",
            "GET", "picks/today/filtered", 200,
            check_fn=lambda r: "pick_run" in r
        )
        if success and result and result["pick_run"]:
            payload = result["pick_run"].get("payload", {})
            total_picks = len(payload.get("picks", []))
            self.log(f"   Total picks (unfiltered): {total_picks}")

        # Test with filters
        success, result = self.test(
            "GET /api/picks/today/filtered?min_confidence=68",
            "GET", "picks/today/filtered?min_confidence=68", 200,
            check_fn=lambda r: (
                "pick_run" in r and
                (r["pick_run"] is None or "_filtered" in r["pick_run"].get("payload", {}))
            )
        )
        if success and result and result["pick_run"]:
            filtered_meta = result["pick_run"]["payload"].get("_filtered", {})
            self.log(f"   Filtered: {filtered_meta.get('kept')}/{filtered_meta.get('total')} picks (min_confidence={filtered_meta.get('min_confidence')})")

        # ═══════════════════════════════════════════════════════════════════════
        # 8. CSV EXPORT TESTS
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[8] CSV EXPORT TESTS", "SECTION")
        
        # Test picks/today/export.csv
        success, result = self.test(
            "GET /api/picks/today/export.csv",
            "GET", "picks/today/export.csv", 200,
            check_fn=lambda r: (
                isinstance(r, str) and
                "generated_at,league,match_label,kickoff,market,selection,odds_range,confidence,confidence_level,is_live,reasoning" in r
            )
        )
        if success and result:
            lines = result.strip().split("\n")
            self.log(f"   CSV rows: {len(lines)} (including header)")
            # Parse CSV to verify structure
            try:
                reader = csv.DictReader(io.StringIO(result))
                headers = reader.fieldnames
                expected_headers = ["generated_at", "league", "match_label", "kickoff", "market", "selection", 
                                   "odds_range", "confidence", "confidence_level", "is_live", "reasoning"]
                if headers == expected_headers:
                    self.log(f"   ✓ CSV headers valid")
                else:
                    self.log(f"   ⚠ CSV headers mismatch: {headers}", "WARN")
            except Exception as e:
                self.log(f"   ⚠ CSV parse error: {e}", "WARN")

        # Test picks/tracked/export.csv
        success, result = self.test(
            "GET /api/picks/tracked/export.csv",
            "GET", "picks/tracked/export.csv", 200,
            check_fn=lambda r: (
                isinstance(r, str) and
                "tracked_at,league,match_label,market,selection,confidence_score,odds,outcome,notes" in r
            )
        )
        if success and result:
            lines = result.strip().split("\n")
            self.log(f"   CSV rows: {len(lines)} (including header)")

        # ═══════════════════════════════════════════════════════════════════════
        # 9. TIMELINE TEST
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[9] TIMELINE TEST", "SECTION")
        
        success, result = self.test(
            "GET /api/stats/timeline",
            "GET", "stats/timeline", 200,
            check_fn=lambda r: (
                "count" in r and
                "timeline" in r and
                isinstance(r["timeline"], list)
            )
        )
        if success and result:
            timeline = result["timeline"]
            self.log(f"   Timeline entries: {len(timeline)}")
            if timeline:
                sample = timeline[0]
                required_fields = ["tracked_at", "match_label", "outcome", "confidence_score", 
                                  "cumulative_won", "cumulative_settled", "win_rate"]
                if all(k in sample for k in required_fields):
                    self.log(f"   ✓ Timeline entry structure valid")
                    self.log(f"   Sample: {sample['match_label']} - {sample['outcome']} (win_rate: {sample['win_rate']}%)")
                else:
                    self.log(f"   ⚠ Timeline entry missing fields", "WARN")

        # ═══════════════════════════════════════════════════════════════════════
        # 10. AUTHZ TESTS (401 without token)
        # ═══════════════════════════════════════════════════════════════════════
        self.log("\n[10] AUTHZ TESTS (401 without token)", "SECTION")
        
        # Temporarily clear token
        saved_token = self.token
        self.token = None
        
        endpoints_requiring_auth = [
            ("system/status", "GET"),
            ("system/fallback-sources", "GET"),
            ("picks/today/filtered", "GET"),
            ("picks/today/export.csv", "GET"),
            ("picks/tracked/export.csv", "GET"),
            ("stats/timeline", "GET"),
            ("meta/leagues", "GET"),
            ("matches/upcoming", "GET"),
        ]
        
        for endpoint, method in endpoints_requiring_auth:
            self.test(
                f"AUTHZ: {method} /api/{endpoint} without token",
                method, endpoint, 401
            )
        
        # Restore token
        self.token = saved_token

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
                self.log(f"   Reason: {failure['reason']}", "ERROR")
                if failure['response']:
                    self.log(f"   Response: {failure['response']}", "ERROR")
        
        return self.tests_failed == 0


def main():
    tester = Phase3Tester()
    success = tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
