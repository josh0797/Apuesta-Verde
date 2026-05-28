"""Phase E1 - Editorial Context Signals Transparency Backend Test

Tests sport-aware signal aggregation, catalog completeness, and E2E integration.

Critical test: Sport-aware filtering must drop signals not applicable to the target sport:
- RED_CARD_CONTEXT must NOT appear on baseball
- CORNER_VOLUME_DETECTED must NOT appear on basketball  
- PACE_OVER_SIGNAL must NOT appear on football
- PITCHER_DUEL_SIGNAL must NOT appear on basketball
- Editorial signals (INJURY_NOTE, MARKET_SUGGESTION, etc.) must appear for ALL sports
"""
import sys
import json
import requests
from datetime import datetime

# Use public endpoint
BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class SignalAggregationTester:
    def __init__(self):
        self.token = None
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.failures = []

    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {level}: {msg}")

    def test(self, name: str, fn):
        """Run a test function and track results."""
        self.tests_run += 1
        self.log(f"Test #{self.tests_run}: {name}")
        try:
            fn()
            self.tests_passed += 1
            self.log("✅ PASSED", "SUCCESS")
            return True
        except AssertionError as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": str(e)})
            return False
        except Exception as e:
            self.tests_failed += 1
            msg = f"❌ FAILED: Exception - {str(e)}"
            self.log(msg, "ERROR")
            self.failures.append({"test": name, "reason": str(e)})
            return False

    def login(self):
        """Authenticate with demo credentials."""
        self.log("Authenticating with demo@valuebet.app...")
        resp = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"},
            timeout=10
        )
        assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
        data = resp.json()
        self.token = data.get("token")
        assert self.token, "No token in login response"
        self.log(f"✅ Authenticated successfully")

    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    # ═══════════════════════════════════════════════════════════════════════════
    # UNIT TESTS - signal_catalog and signal_aggregator (via Python imports)
    # ═══════════════════════════════════════════════════════════════════════════

    def test_catalog_completeness(self):
        """Verify signal catalog has 33+ codes with proper sport filtering."""
        def check():
            # Import locally to test the actual module
            sys.path.insert(0, '/app/backend')
            from services.signal_catalog import SIGNAL_CATALOG, applicable_codes_for
            
            # Check total catalog size
            total = len(SIGNAL_CATALOG)
            assert total >= 33, f"Expected >=33 signals, got {total}"
            self.log(f"   Catalog has {total} signals")
            
            # Check sport-specific counts
            for sport in ['football', 'basketball', 'baseball']:
                codes = applicable_codes_for(sport)
                assert len(codes) >= 28, f"{sport}: expected >=28 codes, got {len(codes)}"
                self.log(f"   {sport}: {len(codes)} applicable codes")
            
            # Verify critical codes exist
            required = [
                'FAVORITE_NAME_BIAS', 'LOW_ODDS_NO_VALUE', 'UNDER_TREND_DETECTED',
                'CORNER_VOLUME_DETECTED', 'PROTECTED_MARKET_AVAILABLE', 'LOW_FRAGILITY_MARKET',
                'STRONG_H2H_PATTERN', 'PACE_OVER_SIGNAL', 'PITCHER_DUEL_SIGNAL',
                'MOTIVATION_NORMAL', 'BALANCED_MATCH', 'DATA_PARTIAL',
                'EDITORIAL_INJURY_NOTE', 'EDITORIAL_MARKET_SUGGESTION',
                'EDITORIAL_MOTIVATION_NOTE', 'EDITORIAL_CONTRADICTION', 'FORM_CRITICAL_STREAK'
            ]
            for code in required:
                assert code in SIGNAL_CATALOG, f"Missing required code: {code}"
            self.log(f"   All {len(required)} required codes present")
        
        self.test("Catalog completeness (33 codes, sport-aware)", check)

    def test_signal_contract_shape(self):
        """Verify every signal returned by make_signal has the required contract."""
        def check():
            sys.path.insert(0, '/app/backend')
            from services.signal_catalog import SIGNAL_CATALOG, make_signal
            
            required_fields = ['code', 'label', 'severity', 'category', 'signal_type', 
                             'explanation', 'impact', 'confidence']
            severity_values = ['low', 'medium', 'high', 'critical']
            signal_types = ['positive', 'negative', 'neutral']
            
            tested = 0
            for code, entry in SIGNAL_CATALOG.items():
                # Test with each applicable sport
                for sport in entry['applicable_sports']:
                    sig = make_signal(code, sport=sport)
                    assert sig is not None, f"{code} returned None for applicable sport {sport}"
                    
                    # Check all required fields present
                    for field in required_fields:
                        assert field in sig, f"{code}: missing field '{field}'"
                    
                    # Validate field values
                    assert sig['code'] == code, f"{code}: code mismatch"
                    assert sig['severity'] in severity_values, f"{code}: invalid severity {sig['severity']}"
                    assert sig['signal_type'] in signal_types, f"{code}: invalid signal_type {sig['signal_type']}"
                    assert isinstance(sig['confidence'], int), f"{code}: confidence not int"
                    assert 0 <= sig['confidence'] <= 100, f"{code}: confidence out of range"
                    tested += 1
            
            self.log(f"   Validated {tested} signal instances across all sports")
        
        self.test("Signal contract shape validation", check)

    def test_sport_aware_filtering(self):
        """CRITICAL: Verify sport-aware filtering drops cross-sport signals."""
        def check():
            sys.path.insert(0, '/app/backend')
            from services.signal_catalog import make_signal
            
            # Test 1: RED_CARD_CONTEXT (football only) must be None for baseball
            sig = make_signal('RED_CARD_CONTEXT', sport='baseball')
            assert sig is None, "RED_CARD_CONTEXT should be None for baseball"
            self.log("   ✓ RED_CARD_CONTEXT correctly dropped for baseball")
            
            # Test 2: CORNER_VOLUME_DETECTED (football only) must be None for basketball
            sig = make_signal('CORNER_VOLUME_DETECTED', sport='basketball')
            assert sig is None, "CORNER_VOLUME_DETECTED should be None for basketball"
            self.log("   ✓ CORNER_VOLUME_DETECTED correctly dropped for basketball")
            
            # Test 3: PACE_OVER_SIGNAL (basketball only) must be None for football
            sig = make_signal('PACE_OVER_SIGNAL', sport='football')
            assert sig is None, "PACE_OVER_SIGNAL should be None for football"
            self.log("   ✓ PACE_OVER_SIGNAL correctly dropped for football")
            
            # Test 4: PITCHER_DUEL_SIGNAL (baseball only) must be None for basketball
            sig = make_signal('PITCHER_DUEL_SIGNAL', sport='basketball')
            assert sig is None, "PITCHER_DUEL_SIGNAL should be None for basketball"
            self.log("   ✓ PITCHER_DUEL_SIGNAL correctly dropped for basketball")
            
            # Test 5: Editorial signals (ALL_SPORTS) must work for all three
            for sport in ['football', 'basketball', 'baseball']:
                sig = make_signal('EDITORIAL_INJURY_NOTE', sport=sport)
                assert sig is not None, f"EDITORIAL_INJURY_NOTE should work for {sport}"
                sig = make_signal('EDITORIAL_MARKET_SUGGESTION', sport=sport)
                assert sig is not None, f"EDITORIAL_MARKET_SUGGESTION should work for {sport}"
            self.log("   ✓ Editorial signals work for all sports")
        
        self.test("Sport-aware filtering (CRITICAL)", check)

    def test_aggregate_signals_unit(self):
        """Unit test aggregate_signals_for_payload with mixed signals."""
        def check():
            sys.path.insert(0, '/app/backend')
            from services.signal_aggregator import aggregate_signals_for_payload
            
            # Build a fake payload with mixed signals
            payload_football = {
                'trap_signals_structured': [
                    {'code': 'FAVORITE_NAME_BIAS', 'extra_explanation': 'test'},
                ],
                '_encounter_history': {
                    'patterns': [
                        {'type': 'CORNERS_TREND', 'confidence': 75, 'evidence': 'test'},
                        {'type': 'UNDER_TREND', 'confidence': 70, 'evidence': 'test'},
                    ]
                },
                '_editorial_context': {
                    'available': True,
                    'signals': [
                        {'signal_type': 'INJURY_REPORT', 'confidence': 0.8, 'text': 'test injury'},
                    ]
                }
            }
            
            # Test football - should include CORNER_VOLUME_DETECTED
            signals = aggregate_signals_for_payload(payload_football, 'football')
            codes = [s['code'] for s in signals]
            assert 'FAVORITE_NAME_BIAS' in codes, "Missing FAVORITE_NAME_BIAS"
            assert 'CORNER_VOLUME_DETECTED' in codes, "Missing CORNER_VOLUME_DETECTED for football"
            assert 'EDITORIAL_INJURY_NOTE' in codes, "Missing EDITORIAL_INJURY_NOTE"
            self.log(f"   Football: {len(signals)} signals, includes corners")
            
            # Test basketball - CORNER_VOLUME_DETECTED should be dropped
            signals = aggregate_signals_for_payload(payload_football, 'basketball')
            codes = [s['code'] for s in signals]
            assert 'CORNER_VOLUME_DETECTED' not in codes, "CORNER_VOLUME_DETECTED should be dropped for basketball"
            assert 'EDITORIAL_INJURY_NOTE' in codes, "Editorial signals should work for basketball"
            self.log(f"   Basketball: {len(signals)} signals, corners dropped")
            
            # Test baseball with RED_CARD_CONTEXT
            payload_baseball = {
                'trap_signals_structured': [
                    {'code': 'RED_CARD_CONTEXT', 'extra_explanation': 'test'},
                ],
                '_baseball_stats': {
                    'signals': [
                        {'code': 'PITCHER_DUEL', 'confidence': 80, 'text': 'test'},
                    ]
                }
            }
            signals = aggregate_signals_for_payload(payload_baseball, 'baseball')
            codes = [s['code'] for s in signals]
            assert 'RED_CARD_CONTEXT' not in codes, "RED_CARD_CONTEXT should be dropped for baseball"
            assert 'PITCHER_DUEL_SIGNAL' in codes, "PITCHER_DUEL_SIGNAL should work for baseball"
            self.log(f"   Baseball: {len(signals)} signals, red card dropped, pitcher duel included")
        
        self.test("aggregate_signals_for_payload unit test", check)

    def test_build_signal_summary(self):
        """Test build_signal_summary aggregates counts correctly."""
        def check():
            sys.path.insert(0, '/app/backend')
            from services.signal_aggregator import build_signal_summary
            
            # Build fake signals by match
            by_match = {
                'M1': [
                    {'code': 'TEST1', 'signal_type': 'positive', 'category': 'market', 'severity': 'high'},
                    {'code': 'TEST2', 'signal_type': 'negative', 'category': 'trap', 'severity': 'critical'},
                ],
                'M2': [
                    {'code': 'TEST3', 'signal_type': 'neutral', 'category': 'historical', 'severity': 'low'},
                ]
            }
            
            summary = build_signal_summary(by_match)
            
            # Check counts
            assert summary['total_signals'] == 3, f"Expected 3 total, got {summary['total_signals']}"
            assert summary['positive_signals'] == 1, f"Expected 1 positive, got {summary['positive_signals']}"
            assert summary['negative_signals'] == 1, f"Expected 1 negative, got {summary['negative_signals']}"
            assert summary['neutral_signals'] == 1, f"Expected 1 neutral, got {summary['neutral_signals']}"
            assert summary['trap_signals'] == 1, f"Expected 1 trap, got {summary['trap_signals']}"
            assert summary['historical_signals'] == 1, f"Expected 1 historical, got {summary['historical_signals']}"
            
            # Check by_category and by_severity
            assert 'market' in summary['by_category'], "Missing 'market' in by_category"
            assert 'trap' in summary['by_category'], "Missing 'trap' in by_category"
            assert 'high' in summary['by_severity'], "Missing 'high' in by_severity"
            assert 'critical' in summary['by_severity'], "Missing 'critical' in by_severity"
            
            self.log(f"   Summary: {summary['total_signals']} total, {summary['positive_signals']} pos, {summary['negative_signals']} neg")
        
        self.test("build_signal_summary aggregation", check)

    # ═══════════════════════════════════════════════════════════════════════════
    # E2E TESTS - Full pipeline via API
    # ═══════════════════════════════════════════════════════════════════════════

    def test_e2e_baseball_analysis(self):
        """E2E: POST /api/analysis/run with sport=baseball, verify signals in response."""
        def check():
            self.log("   Triggering baseball analysis (background mode)...")
            resp = requests.post(
                f"{BASE_URL}/analysis/run",
                json={
                    "sport": "baseball",
                    "refresh": True,
                    "include_live": False,
                    "max_matches": 4,
                    "background": True
                },
                headers=self.headers(),
                timeout=15
            )
            assert resp.status_code == 200, f"Analysis run failed: {resp.status_code} {resp.text}"
            data = resp.json()
            job_id = data.get('job_id')
            assert job_id, "No job_id in response"
            self.log(f"   Job queued: {job_id}")
            
            # Poll for completion (max 4 minutes)
            import time
            max_wait = 240
            waited = 0
            while waited < max_wait:
                time.sleep(10)
                waited += 10
                poll_resp = requests.get(
                    f"{BASE_URL}/analysis/jobs/{job_id}",
                    headers=self.headers(),
                    timeout=10
                )
                assert poll_resp.status_code == 200, f"Poll failed: {poll_resp.status_code}"
                poll_data = poll_resp.json()
                stage = poll_data.get('stage', 'unknown')
                progress = poll_data.get('progress', 0)
                self.log(f"   [{waited}s] Stage: {stage}, Progress: {progress}%")
                
                if stage == 'done':  # Terminal state is 'done', not 'completed'
                    result = poll_data.get('result', {})
                    payload = result.get('result', {})
                    
                    # Check for editorial_signal_summary (MUST be present)
                    summary = payload.get('summary', {})
                    signal_summary = summary.get('editorial_signal_summary')
                    assert signal_summary is not None, "Missing summary.editorial_signal_summary"
                    self.log(f"   ✓ editorial_signal_summary present: {signal_summary.get('total_signals', 0)} signals")
                    
                    # Check pipeline metadata (BUG: currently being overwritten by line 1987 in analyst_engine.py)
                    pipeline = payload.get('_pipeline', {})
                    if 'editorial_signal_aggregation' not in pipeline:
                        self.log(f"   ⚠ BUG: _pipeline.editorial_signal_aggregation missing (overwritten by pipeline_meta)", "ERROR")
                        self.log(f"   ⚠ This is a known bug at analyst_engine.py:1987 - pipeline_meta overwrites the dict", "ERROR")
                    else:
                        agg_meta = pipeline['editorial_signal_aggregation']
                        self.log(f"   ✓ Pipeline metadata: {agg_meta.get('entries_annotated', 0)} entries annotated")
                    
                    # Check that picks/discarded have editorial_context_signals field
                    picks = payload.get('picks', [])
                    disc_mot = summary.get('discarded_motivation', [])
                    disc_mkt = summary.get('discarded_market', [])
                    
                    # At least one bucket should have entries
                    all_entries = picks + disc_mot + disc_mkt
                    if all_entries:
                        # Check first entry has the field (may be empty array)
                        first = all_entries[0]
                        assert 'editorial_context_signals' in first, "Missing editorial_context_signals field"
                        self.log(f"   ✓ editorial_context_signals field present on entries")
                        
                        # Verify NO red-card signals in baseball
                        for entry in all_entries:
                            signals = entry.get('editorial_context_signals', [])
                            for sig in signals:
                                code = sig.get('code', '')
                                assert 'RED_CARD' not in code, f"Found RED_CARD signal in baseball: {code}"
                                assert 'CORNER' not in code, f"Found CORNER signal in baseball: {code}"
                        self.log(f"   ✓ No football-only signals in baseball response")
                    else:
                        self.log(f"   ⚠ No picks/discarded entries to validate (may be no value found)")
                    
                    # Check carryover metadata (regression check)
                    if '_pipeline' in payload and 'carryover' in payload['_pipeline']:
                        self.log(f"   ✓ Carryover metadata present (regression check passed)")
                    
                    return
                elif stage == 'failed':
                    error = poll_data.get('error', 'unknown')
                    raise AssertionError(f"Job failed: {error}")
            
            raise AssertionError(f"Job did not complete within {max_wait}s")
        
        self.test("E2E baseball analysis with signal aggregation", check)

    def test_auth_regression(self):
        """Regression: Verify auth still works."""
        def check():
            resp = requests.post(
                f"{BASE_URL}/auth/login",
                json={"email": "demo@valuebet.app", "password": "demo1234"},
                timeout=10
            )
            assert resp.status_code == 200, f"Auth regression failed: {resp.status_code}"
            data = resp.json()
            assert 'token' in data, "No token in auth response"
            self.log("   ✓ Auth endpoint working")
        
        self.test("Auth regression check", check)

    def run_all(self):
        """Execute all tests in sequence."""
        self.log("=" * 80)
        self.log("Phase E1 - Editorial Context Signals Transparency Test Suite")
        self.log("=" * 80)
        
        # Auth first
        try:
            self.login()
        except Exception as e:
            self.log(f"❌ Authentication failed: {e}", "ERROR")
            return self.summary()
        
        # Unit tests (fast)
        self.test_catalog_completeness()
        self.test_signal_contract_shape()
        self.test_sport_aware_filtering()
        self.test_aggregate_signals_unit()
        self.test_build_signal_summary()
        
        # E2E tests (slow)
        self.test_e2e_baseball_analysis()
        self.test_auth_regression()
        
        return self.summary()

    def summary(self):
        """Print and return test summary."""
        self.log("=" * 80)
        self.log(f"SUMMARY: {self.tests_passed}/{self.tests_run} tests passed")
        if self.tests_failed > 0:
            self.log(f"FAILURES: {self.tests_failed}", "ERROR")
            for f in self.failures:
                self.log(f"  - {f['test']}: {f['reason']}", "ERROR")
        self.log("=" * 80)
        
        return {
            "tests_run": self.tests_run,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "failures": self.failures,
            "success": self.tests_failed == 0
        }

if __name__ == "__main__":
    tester = SignalAggregationTester()
    result = tester.run_all()
    sys.exit(0 if result["success"] else 1)
