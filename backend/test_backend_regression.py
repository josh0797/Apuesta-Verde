"""Backend API smoke tests for competition stage override feature."""
import requests
import sys

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

def test_api_health():
    """Test basic API connectivity."""
    print("=" * 80)
    print("TEST: API Health Check")
    print("=" * 80)
    try:
        resp = requests.get(f"{BASE_URL}/api/health", timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print("✅ API is reachable")
            return True
        else:
            print(f"❌ API returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ API health check failed: {e}")
        return False

def test_learning_stats():
    """Test /api/learning/stats endpoint (regression)."""
    print("\n" + "=" * 80)
    print("TEST: /api/learning/stats (regression)")
    print("=" * 80)
    try:
        resp = requests.get(f"{BASE_URL}/api/learning/stats", timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Patterns returned: {len(data.get('patterns', []))}")
            print("✅ Learning stats endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Learning stats test failed: {e}")
        return False

def test_profile_saved_views():
    """Test /api/profile/saved-views endpoint (regression)."""
    print("\n" + "=" * 80)
    print("TEST: /api/profile/saved-views (regression)")
    print("=" * 80)
    try:
        # This endpoint requires auth, but should return 401 not 500
        resp = requests.get(f"{BASE_URL}/api/profile/saved-views", timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code in (200, 401):
            print("✅ Saved views endpoint structure intact")
            return True
        else:
            print(f"❌ Unexpected status {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Saved views test failed: {e}")
        return False

def test_stats_timeline():
    """Test /api/stats/timeline endpoint (regression)."""
    print("\n" + "=" * 80)
    print("TEST: /api/stats/timeline (regression)")
    print("=" * 80)
    try:
        resp = requests.get(f"{BASE_URL}/api/stats/timeline", timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Timeline entries: {len(data.get('timeline', []))}")
            print("✅ Stats timeline endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Stats timeline test failed: {e}")
        return False

def main():
    print("\n" + "=" * 80)
    print("BACKEND API REGRESSION TESTS")
    print("=" * 80 + "\n")
    
    results = []
    results.append(("API Health", test_api_health()))
    results.append(("Learning Stats", test_learning_stats()))
    results.append(("Saved Views", test_profile_saved_views()))
    results.append(("Stats Timeline", test_stats_timeline()))
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
    
    all_passed = all(r[1] for r in results)
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL REGRESSION TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 80)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
