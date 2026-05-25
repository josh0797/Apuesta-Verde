"""Phase 4 Backend Test — Basketball Live Re-Eval + Football Tracking.

Tests:
1. GET /api/matches/live?sport=basketball → _live_analysis + _live_interpreter
2. POST /api/live/reevaluate with basketball + Total market
3. POST /api/live/reevaluate with basketball + Money Line (trap detection)
4. POST /api/live/reevaluate with basketball + Spread
5. POST /api/picks/track with live-reeval prefix (won/lost/push)
6. GET /api/picks/tracked?sport=football → verify live-reeval entries
7. Football match 1545451 re-eval + tracking flow
"""
import requests
import sys
import time
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class Phase4Tester:
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
            elif method == "DELETE":
                resp = requests.delete(url, headers=headers, timeout=timeout)
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

    def run_all(self):
        """Execute all Phase 4 tests."""
        self.log("=" * 80)
        self.log("PHASE 4 BACKEND TEST — Basketball Live Re-Eval + Tracking")
        self.log("=" * 80)

        # 1. Login
        self.log("\n[1] AUTH — Login")
        success, result = self.test(
            "Login with demo credentials",
            "POST", "auth/login", 200,
            data={"email": "demo@valuebet.app", "password": "demo1234"},
            check_fn=lambda r: "token" in r and "user" in r
        )
        if not success:
            self.log("❌ Login failed, aborting", "ERROR")
            return self.print_summary()
        
        self.token = result["token"]
        self.user_id = result["user"]["id"]
        self.log(f"   Token: {self.token[:20]}...", "INFO")
        self.log(f"   User ID: {self.user_id}", "INFO")

        # 2. Basketball live matches
        self.log("\n[2] BASKETBALL LIVE MATCHES — GET /api/matches/live?sport=basketball")
        success, result = self.test(
            "Get basketball live matches",
            "GET", "matches/live?sport=basketball&refresh=true", 200,
            check_fn=lambda r: isinstance(r, dict) and "items" in r and "sport" in r
        )
        
        basketball_matches = []
        if success and result:
            basketball_matches = result.get("items", [])
            self.log(f"   Found {len(basketball_matches)} basketball live matches", "INFO")
            
            # Check shape of response even if no matches
            if len(basketball_matches) > 0:
                first = basketball_matches[0]
                # Verify _live_analysis shape
                if "_live_analysis" in first:
                    analysis = first["_live_analysis"]
                    required_fields = ["minute", "score", "home", "away", "deltas", "verdict", "_sport"]
                    missing = [f for f in required_fields if f not in analysis]
                    if missing:
                        self.log(f"   ⚠ _live_analysis missing fields: {missing}", "WARN")
                    else:
                        self.log(f"   ✓ _live_analysis has correct shape", "SUCCESS")
                        # Check basketball-specific fields
                        home_block = analysis.get("home", {})
                        basketball_fields = ["points", "points_per_min", "lead_pts", "projected_total"]
                        missing_bball = [f for f in basketball_fields if f not in home_block]
                        if missing_bball:
                            self.log(f"   ⚠ home block missing basketball fields: {missing_bball}", "WARN")
                        else:
                            self.log(f"   ✓ Basketball-specific fields present", "SUCCESS")
                        
                        # Verify _sport field
                        if analysis.get("_sport") != "basketball":
                            self.log(f"   ⚠ _sport is '{analysis.get('_sport')}', expected 'basketball'", "WARN")
                        else:
                            self.log(f"   ✓ _sport='basketball'", "SUCCESS")
                else:
                    self.log(f"   ⚠ _live_analysis not present", "WARN")
                
                # Verify _live_interpreter shape
                if "_live_interpreter" in first:
                    interpreter = first["_live_interpreter"]
                    required_interp = ["title", "subtitle", "recommendation", "action", "risk", "confidence"]
                    missing_interp = [f for f in required_interp if f not in interpreter]
                    if missing_interp:
                        self.log(f"   ⚠ _live_interpreter missing fields: {missing_interp}", "WARN")
                    else:
                        self.log(f"   ✓ _live_interpreter has correct shape", "SUCCESS")
                        # Check Spanish text
                        title = interpreter.get("title", "")
                        if title and any(word in title.lower() for word in ["puntos", "ventaja", "pace", "total"]):
                            self.log(f"   ✓ Interpreter text appears to be in Spanish", "SUCCESS")
                else:
                    self.log(f"   ⚠ _live_interpreter not present", "WARN")
            else:
                self.log(f"   ℹ No basketball matches available (count=0 is acceptable per spec)", "INFO")

        # 3. Basketball re-evaluation — Total market
        self.log("\n[3] BASKETBALL RE-EVAL — Total: Over 215.5")
        if len(basketball_matches) > 0:
            match_id = basketball_matches[0]["match_id"]
            success, result = self.test(
                f"Re-evaluate basketball match {match_id} with Total market",
                "POST", "live/reevaluate", 200,
                data={
                    "match_id": match_id,
                    "sport": "basketball",
                    "manual_odds": 1.85,
                    "manual_market": "Total: Over 215.5",
                    "refresh": True
                },
                check_fn=lambda r: "result" in r and "edge_pct" in r["result"] and "recommended_action" in r["result"]
            )
            if success and result:
                res = result["result"]
                self.log(f"   Edge: {res.get('edge_pct')}%", "INFO")
                self.log(f"   Action: {res.get('recommended_action')}", "INFO")
                self.log(f"   Live state: {res.get('live_state')}", "INFO")
                self.log(f"   Market: {res.get('market')}", "INFO")
        else:
            self.log(f"   ⊘ Skipped (no basketball matches available)", "INFO")

        # 4. Basketball re-evaluation — Money Line (trap detection)
        self.log("\n[4] BASKETBALL RE-EVAL — Money Line: home (trap detection)")
        if len(basketball_matches) > 0:
            match_id = basketball_matches[0]["match_id"]
            # Use low odds to potentially trigger trap
            success, result = self.test(
                f"Re-evaluate basketball match {match_id} with Money Line",
                "POST", "live/reevaluate", 200,
                data={
                    "match_id": match_id,
                    "sport": "basketball",
                    "manual_odds": 1.05,
                    "manual_market": "Money Line: home",
                    "refresh": True
                },
                check_fn=lambda r: "result" in r
            )
            if success and result:
                res = result["result"]
                self.log(f"   Live state: {res.get('live_state')}", "INFO")
                self.log(f"   Action: {res.get('recommended_action')}", "INFO")
                if res.get("live_state") == "TRAP_DETECTED":
                    self.log(f"   ✓ Trap detected correctly", "SUCCESS")
                else:
                    self.log(f"   ℹ No trap detected (depends on game state)", "INFO")
        else:
            self.log(f"   ⊘ Skipped (no basketball matches available)", "INFO")

        # 5. Basketball re-evaluation — Spread
        self.log("\n[5] BASKETBALL RE-EVAL — Spread: home -3.5")
        if len(basketball_matches) > 0:
            match_id = basketball_matches[0]["match_id"]
            success, result = self.test(
                f"Re-evaluate basketball match {match_id} with Spread",
                "POST", "live/reevaluate", 200,
                data={
                    "match_id": match_id,
                    "sport": "basketball",
                    "manual_odds": 1.90,
                    "manual_market": "Spread: home -3.5",
                    "refresh": True
                },
                check_fn=lambda r: "result" in r and "edge_pct" in r["result"]
            )
            if success and result:
                res = result["result"]
                self.log(f"   Edge: {res.get('edge_pct')}%", "INFO")
                self.log(f"   Action: {res.get('recommended_action')}", "INFO")
        else:
            self.log(f"   ⊘ Skipped (no basketball matches available)", "INFO")

        # 6. Football tracking flow — match 1545451
        self.log("\n[6] FOOTBALL TRACKING FLOW — Match 1545451 (U.N.A.M. - Pumas vs Cruz Azul)")
        football_match_id = 1545451
        
        # Re-evaluate the football match
        success, result = self.test(
            f"Re-evaluate football match {football_match_id}",
            "POST", "live/reevaluate", 200,
            data={
                "match_id": football_match_id,
                "sport": "football",
                "manual_odds": 1.85,
                "manual_market": "Under 2.5",
                "refresh": True
            },
            check_fn=lambda r: "result" in r
        )
        
        reeval_result = None
        if success and result:
            reeval_result = result["result"]
            self.log(f"   Live state: {reeval_result.get('live_state')}", "INFO")
            self.log(f"   Edge: {reeval_result.get('edge_pct')}%", "INFO")
            self.log(f"   Action: {reeval_result.get('recommended_action')}", "INFO")

        # 7. Track outcome — won
        self.log("\n[7] TRACK OUTCOME — Won")
        if reeval_result:
            timestamp = int(time.time())
            run_id = f"live-reeval-{football_match_id}-{timestamp}"
            success, result = self.test(
                "Track pick as WON",
                "POST", "picks/track", 200,
                data={
                    "run_id": run_id,
                    "match_id": football_match_id,
                    "match_label": "U.N.A.M. - Pumas vs Cruz Azul",
                    "league": "Liga MX",
                    "market": reeval_result.get("market", "Under 2.5"),
                    "selection": reeval_result.get("selection", "Under 2.5"),
                    "confidence_score": reeval_result.get("confidence", 50),
                    "outcome": "won",
                    "odds": reeval_result.get("decimal_odds", 1.85),
                    "notes": "Live re-eval test",
                    "sport": "football"
                },
                check_fn=lambda r: r.get("ok") is True and r.get("outcome") == "won"
            )
            if success:
                self.log(f"   ✓ Tracked as WON", "SUCCESS")

        # 8. Track outcome — lost
        self.log("\n[8] TRACK OUTCOME — Lost")
        if reeval_result:
            timestamp = int(time.time()) + 1
            run_id = f"live-reeval-{football_match_id}-{timestamp}"
            success, result = self.test(
                "Track pick as LOST",
                "POST", "picks/track", 200,
                data={
                    "run_id": run_id,
                    "match_id": football_match_id,
                    "match_label": "U.N.A.M. - Pumas vs Cruz Azul",
                    "league": "Liga MX",
                    "market": reeval_result.get("market", "Under 2.5"),
                    "selection": reeval_result.get("selection", "Under 2.5"),
                    "confidence_score": reeval_result.get("confidence", 50),
                    "outcome": "lost",
                    "odds": reeval_result.get("decimal_odds", 1.85),
                    "notes": "Live re-eval test",
                    "sport": "football"
                },
                check_fn=lambda r: r.get("ok") is True and r.get("outcome") == "lost"
            )
            if success:
                self.log(f"   ✓ Tracked as LOST", "SUCCESS")

        # 9. Track outcome — push
        self.log("\n[9] TRACK OUTCOME — Push")
        if reeval_result:
            timestamp = int(time.time()) + 2
            run_id = f"live-reeval-{football_match_id}-{timestamp}"
            success, result = self.test(
                "Track pick as PUSH",
                "POST", "picks/track", 200,
                data={
                    "run_id": run_id,
                    "match_id": football_match_id,
                    "match_label": "U.N.A.M. - Pumas vs Cruz Azul",
                    "league": "Liga MX",
                    "market": reeval_result.get("market", "Under 2.5"),
                    "selection": reeval_result.get("selection", "Under 2.5"),
                    "confidence_score": reeval_result.get("confidence", 50),
                    "outcome": "push",
                    "odds": reeval_result.get("decimal_odds", 1.85),
                    "notes": "Live re-eval test",
                    "sport": "football"
                },
                check_fn=lambda r: r.get("ok") is True and r.get("outcome") == "push"
            )
            if success:
                self.log(f"   ✓ Tracked as PUSH", "SUCCESS")

        # 10. Verify tracked picks appear in history
        self.log("\n[10] VERIFY TRACKED PICKS — GET /api/picks/tracked")
        success, result = self.test(
            "Get tracked picks",
            "GET", "picks/tracked?limit=20", 200,
            check_fn=lambda r: "items" in r and isinstance(r["items"], list)
        )
        if success and result:
            items = result.get("items", [])
            live_reeval_picks = [p for p in items if p.get("run_id", "").startswith("live-reeval-")]
            self.log(f"   Total tracked: {len(items)}", "INFO")
            self.log(f"   Live re-eval picks: {len(live_reeval_picks)}", "INFO")
            if len(live_reeval_picks) > 0:
                self.log(f"   ✓ Live re-eval picks found in history", "SUCCESS")
                # Show sample
                sample = live_reeval_picks[0]
                self.log(f"   Sample: {sample.get('match_label')} | {sample.get('outcome')} | {sample.get('market')}", "INFO")
            else:
                self.log(f"   ⚠ No live re-eval picks found (may have been tracked earlier)", "WARN")

        return self.print_summary()

    def print_summary(self):
        """Print test summary."""
        self.log("\n" + "=" * 80)
        self.log("TEST SUMMARY")
        self.log("=" * 80)
        self.log(f"Total tests: {self.tests_run}")
        self.log(f"Passed: {self.tests_passed}")
        self.log(f"Failed: {self.tests_failed}")
        
        if self.tests_failed > 0:
            self.log("\nFAILURES:", "ERROR")
            for i, f in enumerate(self.failures, 1):
                self.log(f"{i}. {f['test']}", "ERROR")
                self.log(f"   Reason: {f['reason']}", "ERROR")
                if f['response']:
                    self.log(f"   Response: {f['response'][:200]}", "ERROR")
        
        success_rate = (self.tests_passed / self.tests_run * 100) if self.tests_run > 0 else 0
        self.log(f"\nSuccess rate: {success_rate:.1f}%")
        
        return 0 if self.tests_failed == 0 else 1

def main():
    tester = Phase4Tester()
    return tester.run_all()

if __name__ == "__main__":
    sys.exit(main())
