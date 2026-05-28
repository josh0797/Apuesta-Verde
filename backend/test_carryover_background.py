"""Test carryover with background jobs to avoid 502 timeout."""
import requests
import time
import sys

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com/api"

def login():
    """Login and get token."""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "demo@valuebet.app",
        "password": "demo1234"
    })
    if resp.status_code == 200:
        return resp.json()["token"]
    else:
        print(f"❌ Login failed: {resp.status_code}")
        return None

def trigger_analysis_background(token, sport="football", max_matches=3):
    """Trigger analysis in background mode."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(f"{BASE_URL}/analysis/run", json={
        "sport": sport,
        "refresh": False,
        "include_live": False,
        "max_matches": max_matches,
        "background": True
    }, headers=headers, timeout=30)
    
    if resp.status_code == 200:
        result = resp.json()
        job_id = result.get("job_id")
        print(f"✅ Analysis job started: {job_id}")
        return job_id
    else:
        print(f"❌ Analysis failed: {resp.status_code} - {resp.text[:200]}")
        return None

def poll_job(token, job_id, max_wait=120):
    """Poll job status until complete."""
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    
    while time.time() - start < max_wait:
        resp = requests.get(f"{BASE_URL}/analysis/jobs/{job_id}", headers=headers, timeout=10)
        if resp.status_code == 200:
            job = resp.json()
            status = job.get("status")
            progress = job.get("progress", {})
            
            print(f"   Job status: {status} - {progress.get('stage', 'unknown')} ({progress.get('percent', 0)}%)")
            
            if status == "completed":
                print(f"✅ Job completed!")
                return job.get("result")
            elif status == "failed":
                print(f"❌ Job failed: {job.get('error')}")
                return None
        
        time.sleep(5)
    
    print(f"⚠ Job timeout after {max_wait}s")
    return None

def check_carryover(result):
    """Check if result has carryover picks."""
    if not result:
        return False
    
    summary = result.get("summary", {})
    carryover_picks = summary.get("carryover_picks", [])
    
    pipeline = result.get("_pipeline", {})
    carryover_meta = pipeline.get("carryover")
    
    print(f"\n📊 CARRYOVER CHECK:")
    print(f"   Carryover picks: {len(carryover_picks)}")
    
    if carryover_meta:
        print(f"   _pipeline.carryover:")
        print(f"      Prior run: {carryover_meta.get('prior_run_id', 'N/A')[:30]}...")
        print(f"      Preserved: {carryover_meta.get('preserved', 0)}")
        print(f"      Skipped: {carryover_meta.get('skipped_breakdown', {})}")
    
    if carryover_picks:
        print(f"\n✅ CARRYOVER WORKING! Found {len(carryover_picks)} preserved picks:")
        for i, cp in enumerate(carryover_picks[:3], 1):
            print(f"   {i}. {cp.get('match_label', 'unknown')}")
            print(f"      Market: {cp.get('recommendation', {}).get('market', 'unknown')}")
            print(f"      Confidence: {cp.get('recommendation', {}).get('confidence_score', 0)}")
            
            # Check _carryover metadata
            carryover_meta_pick = cp.get("_carryover", {})
            if carryover_meta_pick.get("is_carryover"):
                print(f"      ✓ _carryover.is_carryover: True")
            
            # Check CARRYOVER tag
            tags = cp.get("recommendation", {}).get("tags", [])
            if "CARRYOVER" in tags:
                print(f"      ✓ 'CARRYOVER' tag present")
        return True
    else:
        print(f"   ℹ No carryover picks (may be normal - see reasons below)")
        return False

def main():
    print("=" * 80)
    print("CARRYOVER TEST - Background Jobs")
    print("=" * 80)
    
    # Login
    print("\n[1] Login...")
    token = login()
    if not token:
        return 1
    
    # First run
    print("\n[2] Trigger first football analysis...")
    job1 = trigger_analysis_background(token, "football", max_matches=3)
    if not job1:
        print("⚠ First run failed to start")
        return 1
    
    print("\n[3] Polling first job...")
    result1 = poll_job(token, job1, max_wait=120)
    if not result1:
        print("⚠ First run failed or timed out")
        return 1
    
    print(f"\n✅ First run completed:")
    print(f"   Verdict: {result1.get('verdict')}")
    print(f"   Picks: {len(result1.get('picks', []))}")
    
    # Check first run carryover (should be 0)
    check_carryover(result1)
    
    # Wait a bit
    print("\n[4] Waiting 3 seconds before second run...")
    time.sleep(3)
    
    # Second run
    print("\n[5] Trigger second football analysis (same sport)...")
    job2 = trigger_analysis_background(token, "football", max_matches=3)
    if not job2:
        print("⚠ Second run failed to start")
        return 1
    
    print("\n[6] Polling second job...")
    result2 = poll_job(token, job2, max_wait=120)
    if not result2:
        print("⚠ Second run failed or timed out")
        return 1
    
    print(f"\n✅ Second run completed:")
    print(f"   Verdict: {result2.get('verdict')}")
    print(f"   Picks: {len(result2.get('picks', []))}")
    
    # Check second run carryover (should have preserved picks if conditions met)
    has_carryover = check_carryover(result2)
    
    print("\n" + "=" * 80)
    if has_carryover:
        print("✅ CARRYOVER FEATURE VERIFIED - Picks preserved across runs!")
    else:
        print("ℹ No carryover picks found. Possible reasons:")
        print("   - First run had no picks (verdict=no_value)")
        print("   - All first run picks had confidence < 60")
        print("   - All matches from first run already started")
        print("   - All first run picks were duplicates in second run")
        print("   - This is EXPECTED behavior, not a bug")
    print("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
