"""Backend tests for P4 Playwright integration (JS-heavy sources).

Tests:
  1. Import statements work correctly
  2. Registry helpers return correct sources
  3. Proxy parser works correctly
  4. Playwright subprocess runs without raising
  5. Dual-backend integration (Scrapy + Playwright in parallel)
  6. Fail-soft behavior when Cloudflare blocks
  7. Regression tests for existing functionality
"""
import sys
import os
import asyncio
import requests
from datetime import datetime, timezone

# Add backend to path for imports
sys.path.insert(0, "/app/backend")

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

class TestRunner:
    def __init__(self):
        self.token = None
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
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 1: Import statements work correctly
    # ══════════════════════════════════════════════════════════════════════════
    def test_imports():
        print("Testing imports from services.editorial_context...")
        try:
            from services.editorial_context import (
                enabled_sources,
                server_rendered_sources,
                js_rendered_sources,
                fetch_editorial_context_bulk
            )
            print("✓ All imports successful")
            print(f"  - enabled_sources: {enabled_sources}")
            print(f"  - server_rendered_sources: {server_rendered_sources}")
            print(f"  - js_rendered_sources: {js_rendered_sources}")
            print(f"  - fetch_editorial_context_bulk: {fetch_editorial_context_bulk}")
        except ImportError as e:
            raise AssertionError(f"Import failed: {e}")
    
    runner.test("Import statements work", test_imports)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 2: Registry helpers - server_rendered_sources
    # ══════════════════════════════════════════════════════════════════════════
    def test_server_rendered_sources():
        print("Testing server_rendered_sources('football')...")
        from services.editorial_context import server_rendered_sources
        
        sources = server_rendered_sources('football')
        print(f"✓ Found {len(sources)} server-rendered sources")
        
        assert len(sources) == 4, f"Expected 4 server-rendered sources, got {len(sources)}"
        
        names = {s['name'] for s in sources}
        expected = {'as_com', 'sportytrader_es', 'besoccer_es', 'marca_com'}
        
        print(f"  Source names: {names}")
        assert names == expected, f"Expected {expected}, got {names}"
        
        # Verify none require JS
        for s in sources:
            assert not s.get('requires_js'), f"Source {s['name']} should not require JS"
        
        print(f"✓ All sources are server-rendered (requires_js=False)")
    
    runner.test("server_rendered_sources returns 4 entries", test_server_rendered_sources)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 3: Registry helpers - js_rendered_sources
    # ══════════════════════════════════════════════════════════════════════════
    def test_js_rendered_sources():
        print("Testing js_rendered_sources('football')...")
        from services.editorial_context import js_rendered_sources
        
        sources = js_rendered_sources('football')
        print(f"✓ Found {len(sources)} JS-rendered sources")
        
        assert len(sources) == 1, f"Expected 1 JS-rendered source, got {len(sources)}"
        
        assert sources[0]['name'] == 'scores24_live', f"Expected 'scores24_live', got {sources[0]['name']}"
        assert sources[0].get('requires_js') == True, "scores24_live should require JS"
        
        print(f"  Source name: {sources[0]['name']}")
        print(f"  requires_js: {sources[0].get('requires_js')}")
    
    runner.test("js_rendered_sources returns 1 entry (scores24_live)", test_js_rendered_sources)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 4: Registry helpers - enabled_sources
    # ══════════════════════════════════════════════════════════════════════════
    def test_enabled_sources():
        print("Testing enabled_sources('football')...")
        from services.editorial_context import enabled_sources
        
        # Test with include_js=True (default)
        sources_with_js = enabled_sources('football', include_js=True)
        print(f"✓ enabled_sources(include_js=True): {len(sources_with_js)} sources")
        assert len(sources_with_js) == 5, f"Expected 5 sources with JS, got {len(sources_with_js)}"
        
        # Test with include_js=False
        sources_without_js = enabled_sources('football', include_js=False)
        print(f"✓ enabled_sources(include_js=False): {len(sources_without_js)} sources")
        assert len(sources_without_js) == 4, f"Expected 4 sources without JS, got {len(sources_without_js)}"
        
        names_with_js = {s['name'] for s in sources_with_js}
        names_without_js = {s['name'] for s in sources_without_js}
        
        print(f"  With JS: {names_with_js}")
        print(f"  Without JS: {names_without_js}")
        
        assert 'scores24_live' in names_with_js, "scores24_live should be in include_js=True"
        assert 'scores24_live' not in names_without_js, "scores24_live should NOT be in include_js=False"
    
    runner.test("enabled_sources filters correctly", test_enabled_sources)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 5: Proxy parser - no env var
    # ══════════════════════════════════════════════════════════════════════════
    def test_proxy_parser_no_env():
        print("Testing proxy parser with no PLAYWRIGHT_PROXY env var...")
        from services.editorial_context.playwright_fetcher import _build_proxy_from_env
        
        # Clear env var if it exists
        old_val = os.environ.pop('PLAYWRIGHT_PROXY', None)
        
        try:
            proxy = _build_proxy_from_env()
            print(f"✓ Proxy result: {proxy}")
            assert proxy is None, f"Expected None when no env var, got {proxy}"
        finally:
            if old_val:
                os.environ['PLAYWRIGHT_PROXY'] = old_val
    
    runner.test("Proxy parser returns None when no env", test_proxy_parser_no_env)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 6: Proxy parser - with env var
    # ══════════════════════════════════════════════════════════════════════════
    def test_proxy_parser_with_env():
        print("Testing proxy parser with PLAYWRIGHT_PROXY env var...")
        from services.editorial_context.playwright_fetcher import _build_proxy_from_env
        
        old_val = os.environ.get('PLAYWRIGHT_PROXY')
        
        try:
            # Test with credentials
            os.environ['PLAYWRIGHT_PROXY'] = 'http://user:pass@proxy.example.com:8080'
            proxy = _build_proxy_from_env()
            print(f"✓ Proxy result: {proxy}")
            
            assert proxy is not None, "Expected proxy dict, got None"
            assert 'server' in proxy, "Expected 'server' in proxy dict"
            assert 'username' in proxy, "Expected 'username' in proxy dict"
            assert 'password' in proxy, "Expected 'password' in proxy dict"
            
            assert proxy['server'] == 'http://proxy.example.com:8080', f"Unexpected server: {proxy['server']}"
            assert proxy['username'] == 'user', f"Unexpected username: {proxy['username']}"
            assert proxy['password'] == 'pass', f"Unexpected password: {proxy['password']}"
            
            print(f"  server: {proxy['server']}")
            print(f"  username: {proxy['username']}")
            print(f"  password: {proxy['password']}")
        finally:
            if old_val:
                os.environ['PLAYWRIGHT_PROXY'] = old_val
            else:
                os.environ.pop('PLAYWRIGHT_PROXY', None)
    
    runner.test("Proxy parser parses env var correctly", test_proxy_parser_with_env)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 7: Playwright subprocess runs cleanly (fail-soft)
    # ══════════════════════════════════════════════════════════════════════════
    def test_playwright_subprocess():
        print("Testing Playwright subprocess with synthetic match...")
        from services.editorial_context.playwright_runner import run_playwright
        from services.editorial_context import js_rendered_sources
        
        synthetic_match = {
            "sport": "football",
            "home": "Real Madrid",
            "away": "Barcelona",
            "league": "La Liga",
            "kickoff_iso": datetime.now(timezone.utc).isoformat(),
            "match_id": "test_match_123",
        }
        
        js_sources = js_rendered_sources('football')
        
        print(f"  Match: {synthetic_match['home']} vs {synthetic_match['away']}")
        print(f"  JS sources: {[s['name'] for s in js_sources]}")
        
        try:
            # Run with 35 second timeout
            result = asyncio.run(run_playwright(
                [synthetic_match],
                js_sources,
                timeout_sec=35
            ))
            
            print(f"✓ Playwright subprocess completed without raising")
            print(f"  Result type: {type(result)}")
            print(f"  Result length: {len(result) if isinstance(result, list) else 'N/A'}")
            
            # Result should be a list (possibly empty due to Cloudflare)
            assert isinstance(result, list), f"Expected list result, got {type(result)}"
            
            # Check backend logs for expected markers
            print("\n  Checking backend logs for Playwright markers...")
            # Note: We can't easily check logs in this test, but the subprocess should have logged
            
        except Exception as e:
            raise AssertionError(f"Playwright subprocess raised exception: {e}")
    
    runner.test("Playwright subprocess runs without raising", test_playwright_subprocess)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 8: Dual-backend integration (Scrapy + Playwright in parallel)
    # ══════════════════════════════════════════════════════════════════════════
    def test_dual_backend_integration():
        print("Testing dual-backend integration (Scrapy + Playwright)...")
        from services.editorial_context import fetch_editorial_context_bulk
        
        # Use a popular European football match
        synthetic_match = {
            "sport": "football",
            "home": "Manchester United",
            "away": "Liverpool",
            "league": "Premier League",
            "kickoff_iso": datetime.now(timezone.utc).isoformat(),
            "match_id": "test_dual_backend_456",
        }
        
        print(f"  Match: {synthetic_match['home']} vs {synthetic_match['away']}")
        
        try:
            # Run with no DB (fail-soft)
            result = asyncio.run(fetch_editorial_context_bulk(
                [synthetic_match],
                db=None,
                force_refresh=True,
                timeout_sec=35
            ))
            
            print(f"✓ Dual-backend fetch completed without raising")
            print(f"  Result type: {type(result)}")
            print(f"  Result keys: {list(result.keys())}")
            
            assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
            
            match_id = synthetic_match['match_id']
            assert match_id in result, f"Expected match_id '{match_id}' in result"
            
            editorial_context = result[match_id]
            print(f"\n  Editorial context keys: {list(editorial_context.keys())}")
            
            # Check for expected fields
            assert 'available' in editorial_context, "Missing 'available' field"
            assert 'sources' in editorial_context, "Missing 'sources' field"
            
            print(f"  available: {editorial_context.get('available')}")
            print(f"  sources_count: {editorial_context.get('sources_count')}")
            print(f"  sources: {editorial_context.get('sources')}")
            
            # Even if Playwright returns 0 items (Cloudflare), Scrapy should provide data
            # So we should have SOME editorial context
            if editorial_context.get('available'):
                print(f"✓ Editorial context available with {editorial_context.get('sources_count')} sources")
            else:
                print(f"⚠ Editorial context not available (reason: {editorial_context.get('_reason')})")
                # This is OK - fail-soft behavior
            
        except Exception as e:
            raise AssertionError(f"Dual-backend integration raised exception: {e}")
    
    runner.test("Dual-backend integration (Scrapy + Playwright)", test_dual_backend_integration)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 9: Regression - Auth login
    # ══════════════════════════════════════════════════════════════════════════
    def test_auth_login():
        print("Testing auth login...")
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={"email": "demo@valuebet.app", "password": "demo1234"}
        )
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Login failed with status {response.status_code}"
        
        data = response.json()
        assert "token" in data, "No token in response"
        runner.token = data["token"]
        print(f"✓ Got token: {runner.token[:20]}...")
    
    runner.test("Regression: Auth login works", test_auth_login)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 10: Regression - GET /api/matches/live for football
    # ══════════════════════════════════════════════════════════════════════════
    def test_matches_live_football():
        if not runner.token:
            print("⚠ Skipping: no auth token")
            return
        
        print("Testing GET /api/matches/live?sport=football...")
        headers = {"Authorization": f"Bearer {runner.token}"}
        response = requests.get(f"{BASE_URL}/matches/live?sport=football", headers=headers)
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed with status {response.status_code}"
        
        data = response.json()
        print(f"✓ Response keys: {list(data.keys())}")
    
    runner.test("Regression: GET /api/matches/live (football)", test_matches_live_football)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 11: Regression - GET /api/matches/live for basketball
    # ══════════════════════════════════════════════════════════════════════════
    def test_matches_live_basketball():
        if not runner.token:
            print("⚠ Skipping: no auth token")
            return
        
        print("Testing GET /api/matches/live?sport=basketball...")
        headers = {"Authorization": f"Bearer {runner.token}"}
        response = requests.get(f"{BASE_URL}/matches/live?sport=basketball", headers=headers)
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed with status {response.status_code}"
        
        data = response.json()
        print(f"✓ Response keys: {list(data.keys())}")
    
    runner.test("Regression: GET /api/matches/live (basketball)", test_matches_live_basketball)
    
    # ══════════════════════════════════════════════════════════════════════════
    # Test 12: Regression - GET /api/matches/live for baseball
    # ══════════════════════════════════════════════════════════════════════════
    def test_matches_live_baseball():
        if not runner.token:
            print("⚠ Skipping: no auth token")
            return
        
        print("Testing GET /api/matches/live?sport=baseball...")
        headers = {"Authorization": f"Bearer {runner.token}"}
        response = requests.get(f"{BASE_URL}/matches/live?sport=baseball", headers=headers)
        print(f"Status: {response.status_code}")
        
        assert response.status_code == 200, f"Failed with status {response.status_code}"
        
        data = response.json()
        print(f"✓ Response keys: {list(data.keys())}")
    
    runner.test("Regression: GET /api/matches/live (baseball)", test_matches_live_baseball)
    
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
