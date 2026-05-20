"""Backend API tests with authentication."""
import requests
import sys

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"
TEST_USER = "demo@valuebet.app"
TEST_PASS = "demo1234"

def login():
    """Login and get auth token."""
    print("=" * 80)
    print("TEST: Login")
    print("=" * 80)
    try:
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_USER, "password": TEST_PASS},
            timeout=10
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token")
            if token:
                print("✅ Login successful")
                return token
            else:
                print("❌ No token in response")
                print(f"Response: {data}")
                return None
        else:
            print(f"❌ Login failed: {resp.status_code}")
            print(f"Response: {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return None

def test_learning_stats(token):
    """Test /api/learning/stats endpoint."""
    print("\n" + "=" * 80)
    print("TEST: /api/learning/stats")
    print("=" * 80)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{BASE_URL}/api/learning/stats", headers=headers, timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Patterns returned: {len(data.get('patterns', []))}")
            print("✅ Learning stats endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            print(f"Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Learning stats test failed: {e}")
        return False

def test_profile_saved_views(token):
    """Test /api/profile/saved-views endpoint."""
    print("\n" + "=" * 80)
    print("TEST: /api/profile/saved-views")
    print("=" * 80)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{BASE_URL}/api/profile/saved-views", headers=headers, timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Saved views: {len(data.get('views', []))}")
            print("✅ Saved views endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            print(f"Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Saved views test failed: {e}")
        return False

def test_stats_timeline(token):
    """Test /api/stats/timeline endpoint."""
    print("\n" + "=" * 80)
    print("TEST: /api/stats/timeline")
    print("=" * 80)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{BASE_URL}/api/stats/timeline", headers=headers, timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"Timeline entries: {len(data.get('timeline', []))}")
            print("✅ Stats timeline endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            print(f"Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Stats timeline test failed: {e}")
        return False

def test_picks_today(token):
    """Test /api/picks/today endpoint."""
    print("\n" + "=" * 80)
    print("TEST: /api/picks/today")
    print("=" * 80)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(f"{BASE_URL}/api/picks/today", headers=headers, timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            pick_run = data.get('pick_run')
            if pick_run:
                picks = pick_run.get('payload', {}).get('picks', [])
                print(f"Picks available: {len(picks)}")
                # Check if any pick has pressure_state field
                for pick in picks[:3]:  # Check first 3
                    if 'pressure_state' in pick:
                        print(f"  ✓ Pick {pick.get('match_id')} has pressure_state: {pick['pressure_state']}")
                    if 'competition_stage' in pick:
                        print(f"  ✓ Pick {pick.get('match_id')} has competition_stage: {pick['competition_stage']}")
            else:
                print("No pick_run available (expected if no analysis has been run)")
            print("✅ Picks endpoint working")
            return True
        else:
            print(f"❌ Endpoint returned {resp.status_code}")
            print(f"Response: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ Picks test failed: {e}")
        return False

def main():
    print("\n" + "=" * 80)
    print("BACKEND API TESTS (with auth)")
    print("=" * 80 + "\n")
    
    token = login()
    if not token:
        print("\n❌ Cannot proceed without authentication")
        return 1
    
    results = []
    results.append(("Learning Stats", test_learning_stats(token)))
    results.append(("Saved Views", test_profile_saved_views(token)))
    results.append(("Stats Timeline", test_stats_timeline(token)))
    results.append(("Picks Today", test_picks_today(token)))
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
    
    all_passed = all(r[1] for r in results)
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL BACKEND TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 80)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
