"""Simple carryover test - check if carryover logic is working by inspecting database."""
import sys
import os
sys.path.insert(0, '/app/backend')

from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
from datetime import datetime, timezone

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

async def test_carryover_in_db():
    """Check if any pick_runs in the database have carryover_picks."""
    print("=" * 80)
    print("CARRYOVER DATABASE INSPECTION TEST")
    print("=" * 80)
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    try:
        # Find the most recent pick_runs
        cursor = db.picks.find({}).sort("generated_at", -1).limit(10)
        runs = await cursor.to_list(length=10)
        
        print(f"\nFound {len(runs)} recent pick_runs in database")
        
        carryover_found = False
        for i, run in enumerate(runs, 1):
            run_id = run.get("id", "unknown")
            sport = run.get("sport", "football")
            generated_at = run.get("generated_at", "unknown")
            payload = run.get("payload", {})
            summary = payload.get("summary", {})
            
            # Check for carryover_picks
            carryover_picks = summary.get("carryover_picks", [])
            
            # Check for _pipeline.carryover
            pipeline = payload.get("_pipeline", {})
            carryover_meta = pipeline.get("carryover")
            
            print(f"\n[{i}] Run ID: {run_id[:30]}...")
            print(f"    Sport: {sport}")
            print(f"    Generated: {generated_at}")
            print(f"    Carryover picks: {len(carryover_picks)}")
            
            if carryover_meta:
                print(f"    _pipeline.carryover present:")
                print(f"      Prior run: {carryover_meta.get('prior_run_id', 'N/A')[:30]}...")
                print(f"      Preserved: {carryover_meta.get('preserved', 0)}")
                print(f"      Skipped: {carryover_meta.get('skipped_breakdown', {})}")
                carryover_found = True
            
            if carryover_picks:
                print(f"    ✅ CARRYOVER PICKS FOUND!")
                for j, cp in enumerate(carryover_picks[:2], 1):
                    print(f"      Pick {j}: {cp.get('match_label', 'unknown')}")
                    print(f"        Market: {cp.get('recommendation', {}).get('market', 'unknown')}")
                    print(f"        Confidence: {cp.get('recommendation', {}).get('confidence_score', 0)}")
                    
                    # Check _carryover metadata
                    carryover_meta_pick = cp.get("_carryover", {})
                    if carryover_meta_pick.get("is_carryover"):
                        print(f"        ✓ _carryover.is_carryover: True")
                        print(f"        Original run: {carryover_meta_pick.get('original_run_id', 'N/A')[:30]}...")
                    
                    # Check CARRYOVER tag
                    tags = cp.get("recommendation", {}).get("tags", [])
                    if "CARRYOVER" in tags:
                        print(f"        ✓ 'CARRYOVER' tag present")
                
                carryover_found = True
        
        print("\n" + "=" * 80)
        if carryover_found:
            print("✅ CARRYOVER FEATURE IS WORKING - Evidence found in database")
        else:
            print("⚠ No carryover evidence found in recent runs")
            print("   This may be normal if:")
            print("   - No consecutive runs for same sport within 24h")
            print("   - All prior picks had confidence < 60")
            print("   - All prior matches already started")
            print("   - All prior picks were duplicates in new run")
        print("=" * 80)
        
        return carryover_found
        
    finally:
        client.close()

if __name__ == "__main__":
    result = asyncio.run(test_carryover_in_db())
    sys.exit(0 if result else 1)
