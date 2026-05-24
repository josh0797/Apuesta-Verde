"""Phase 10 Live Re-Evaluation focused test."""
import requests
import sys
from datetime import datetime

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {level}: {msg}")

def main():
    log("=" * 80)
    log("PHASE 10 - LIVE RE-EVALUATION TESTS")
    log("=" * 80)
    
    # Login
    log("\n1. Login")
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": "demo@valuebet.app", "password": "demo1234"})
    if resp.status_code != 200:
        log(f"Login failed: {resp.status_code}", "ERROR")
        return 1
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    log("✅ Logged in")
    
    # Get live matches (without refresh to avoid timeout)
    log("\n2. Get live matches (no refresh)")
    resp = requests.get(f"{BASE_URL}/matches/live?sport=football&refresh=false", headers=headers, timeout=10)
    if resp.status_code != 200:
        log(f"Failed to get live matches: {resp.status_code}", "ERROR")
        return 1
    
    data = resp.json()
    live_matches = data.get("items", [])
    log(f"✅ Found {len(live_matches)} live matches")
    
    if not live_matches:
        log("⚠ No live matches available, testing with validation only", "WARN")
        test_match_id = "999999"
    else:
        test_match_id = live_matches[0]["match_id"]
        match_label = f"{live_matches[0].get('home_team', {}).get('name', 'Home')} vs {live_matches[0].get('away_team', {}).get('name', 'Away')}"
        log(f"Using match: {match_label} (ID: {test_match_id})")
    
    # Test 1: Without manual_odds
    log("\n3. Test: POST /api/live/reevaluate (no manual_odds)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={"match_id": test_match_id, "sport": "football", "refresh": False},
        headers=headers,
        timeout=15
    )
    if resp.status_code == 404 and test_match_id == "999999":
        log("✅ Correctly returned 404 for non-existent match")
    elif resp.status_code == 200:
        result = resp.json().get("result", {})
        log(f"✅ Status: 200")
        log(f"   Live state: {result.get('live_state')}")
        log(f"   Action: {result.get('recommended_action')}")
        log(f"   Reason: {result.get('reason', '')[:150]}")
        if result.get("live_state") == "NO_LIVE_VALUE":
            log(f"   ✓ Returns NO_LIVE_VALUE when no manual odds provided")
    else:
        log(f"❌ Unexpected status: {resp.status_code}", "ERROR")
        log(f"   Response: {resp.text[:300]}", "ERROR")
    
    # Test 2: With manual_odds + manual_market
    log("\n4. Test: POST /api/live/reevaluate (with manual_odds)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={
            "match_id": test_match_id,
            "sport": "football",
            "refresh": False,
            "manual_odds": 1.85,
            "manual_market": "Under 2.5"
        },
        headers=headers,
        timeout=15
    )
    if resp.status_code == 404 and test_match_id == "999999":
        log("✅ Correctly returned 404 for non-existent match")
    elif resp.status_code == 200:
        result = resp.json().get("result", {})
        log(f"✅ Status: 200")
        log(f"   Live state: {result.get('live_state')}")
        log(f"   Action: {result.get('recommended_action')}")
        log(f"   Market: {result.get('market')}")
        log(f"   Edge: {result.get('edge_pct')}%")
        log(f"   Confidence: {result.get('confidence')}/100")
        log(f"   Risk: {result.get('risk_level')}")
        log(f"   Manual odds used: {result.get('manual_odds_used')}")
        log(f"   Estimated prob: {result.get('estimated_probability')}")
        log(f"   Implied prob: {result.get('implied_probability')}")
        
        # Verify required fields
        required = ["live_state", "recommended_action", "edge_pct", "confidence", "risk_level", "manual_odds_used"]
        missing = [f for f in required if f not in result]
        if missing:
            log(f"❌ Missing fields: {missing}", "ERROR")
        elif result.get("manual_odds_used") != True:
            log(f"❌ manual_odds_used should be True", "ERROR")
        else:
            log(f"   ✓ All required fields present and manual_odds_used=True")
    else:
        log(f"❌ Unexpected status: {resp.status_code}", "ERROR")
        log(f"   Response: {resp.text[:300]}", "ERROR")
    
    # Test 3: Invalid odds ≤1.01
    log("\n5. Test: Invalid odds ≤1.01 (should return 400)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={
            "match_id": test_match_id,
            "sport": "football",
            "manual_odds": 1.0,
            "manual_market": "Under 2.5"
        },
        headers=headers,
        timeout=10
    )
    if resp.status_code == 400:
        log(f"✅ Correctly returned 400 for invalid odds")
        log(f"   Detail: {resp.json().get('detail', '')}")
    else:
        log(f"❌ Expected 400, got {resp.status_code}", "ERROR")
    
    # Test 4: manual_odds without manual_market
    log("\n6. Test: manual_odds without manual_market (should return 400)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={
            "match_id": test_match_id,
            "sport": "football",
            "manual_odds": 1.85
        },
        headers=headers,
        timeout=10
    )
    if resp.status_code == 400:
        log(f"✅ Correctly returned 400")
        log(f"   Detail: {resp.json().get('detail', '')}")
    else:
        log(f"❌ Expected 400, got {resp.status_code}", "ERROR")
    
    # Test 5: Non-existent match
    log("\n7. Test: Non-existent match (should return 404)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={
            "match_id": "nonexistent_99999999",
            "sport": "football"
        },
        headers=headers,
        timeout=10
    )
    if resp.status_code == 404:
        log(f"✅ Correctly returned 404 for non-existent match")
    else:
        log(f"❌ Expected 404, got {resp.status_code}", "ERROR")
    
    # Test 6: Non-football sport
    log("\n8. Test: Basketball (should return 400 - football only)")
    resp = requests.post(
        f"{BASE_URL}/live/reevaluate",
        json={
            "match_id": test_match_id,
            "sport": "basketball"
        },
        headers=headers,
        timeout=10
    )
    if resp.status_code == 400:
        log(f"✅ Correctly returned 400 for non-football sport")
        log(f"   Detail: {resp.json().get('detail', '')}")
    else:
        log(f"❌ Expected 400, got {resp.status_code}", "ERROR")
    
    log("\n" + "=" * 80)
    log("PHASE 10 BACKEND TESTS COMPLETE")
    log("=" * 80)
    return 0

if __name__ == "__main__":
    sys.exit(main())
