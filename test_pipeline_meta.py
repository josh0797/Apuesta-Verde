"""Test pipeline_meta by polling job status"""
import requests
import json
import time

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"

# Login
response = requests.post(
    f"{BASE_URL}/api/auth/login",
    json={"email": "demo@valuebet.app", "password": "demo1234"}
)
token = response.json()['token']

def poll_job(job_id, token, max_wait=60):
    """Poll job until complete"""
    start = time.time()
    while time.time() - start < max_wait:
        response = requests.get(
            f"{BASE_URL}/api/analysis/jobs/{job_id}",
            headers={'Authorization': f'Bearer {token}'}
        )
        data = response.json()
        status = data.get('status')
        print(f"  Job status: {status} (progress: {data.get('progress', 0)}%)")
        
        if status == 'completed':
            return data
        elif status == 'failed':
            print(f"  Job failed: {data.get('error')}")
            return data
        
        time.sleep(2)
    
    print(f"  Timeout after {max_wait}s")
    return None

# Test baseball pipeline
print("="*80)
print("BASEBALL PIPELINE TEST")
print("="*80)
response = requests.post(
    f"{BASE_URL}/api/analysis/run",
    json={
        "sport": "baseball",
        "refresh": False,
        "include_live": False,
        "max_matches": 3,  # Reduced to avoid timeout
        "background": True  # Explicitly request background
    },
    headers={'Authorization': f'Bearer {token}'},
    timeout=60
)

print(f"Status: {response.status_code}")
data = response.json()
job_id = data.get('job_id')

if job_id:
    print(f"Job ID: {job_id}")
    print("Polling job status...")
    result = poll_job(job_id, token, max_wait=60)
    
    if result and result.get('status') == 'completed':
        payload = result.get('payload', {})
        if 'pipeline_meta' in payload:
            print(f"\n✅ Pipeline Meta found:")
            meta = payload['pipeline_meta']
            print(f"  sport: {meta.get('sport')}")
            print(f"  source_used: {meta.get('source_used')}")
            print(f"  mlb_stats_api_games_found: {meta.get('mlb_stats_api_games_found')}")
            print(f"  external_rescue_count: {meta.get('external_rescue_count')}")
            print(f"  external_sources_consulted: {len(meta.get('external_sources_consulted', []))} sources")
            print(f"\nFull pipeline_meta:")
            print(json.dumps(meta, indent=2))
        else:
            print("\n❌ pipeline_meta NOT FOUND")
            print(f"Payload keys: {list(payload.keys())}")

print("\n" + "="*80)
print("BASKETBALL PIPELINE TEST")
print("="*80)
response = requests.post(
    f"{BASE_URL}/api/analysis/run",
    json={
        "sport": "basketball",
        "refresh": False,
        "include_live": False,
        "max_matches": 3,
        "background": True
    },
    headers={'Authorization': f'Bearer {token}'},
    timeout=60
)

print(f"Status: {response.status_code}")
data = response.json()
job_id = data.get('job_id')

if job_id:
    print(f"Job ID: {job_id}")
    print("Polling job status...")
    result = poll_job(job_id, token, max_wait=60)
    
    if result and result.get('status') == 'completed':
        payload = result.get('payload', {})
        if 'pipeline_meta' in payload:
            print(f"\n✅ Pipeline Meta found:")
            meta = payload['pipeline_meta']
            print(f"  sport: {meta.get('sport')}")
            print(f"  source_used: {meta.get('source_used')}")
            print(f"  espn_nba_games_found: {meta.get('espn_nba_games_found')}")
            print(f"  abort_reason: {meta.get('abort_reason')}")
            print(f"\nFull pipeline_meta:")
            print(json.dumps(meta, indent=2))
        else:
            print("\n❌ pipeline_meta NOT FOUND")
            print(f"Payload keys: {list(payload.keys())}")
