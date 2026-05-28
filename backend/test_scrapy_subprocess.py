"""Quick test to verify Scrapy subprocess execution and log output."""
import sys
import asyncio
import logging

sys.path.insert(0, '/app/backend')

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)

async def test_scrapy():
    from services.editorial_context import fetch_editorial_context_bulk
    
    print("\n" + "="*70)
    print("SCRAPY SUBPROCESS TEST")
    print("="*70)
    
    matches = [
        {
            "match_id": "scrapy_test_1",
            "sport": "football",
            "home": "Alavés",
            "away": "Rayo Vallecano",
            "league": "La Liga",
            "kickoff_iso": "2026-05-22T19:00:00Z"
        }
    ]
    
    print("\nCalling fetch_editorial_context_bulk with force_refresh=True...")
    print("This should trigger Scrapy subprocess.")
    print("Watch for [SCRAPY_EDITORIAL_START] and [SCRAPY_EDITORIAL_DONE] in logs.\n")
    
    result = await fetch_editorial_context_bulk(
        matches, 
        db=None, 
        force_refresh=True, 
        timeout_sec=20.0
    )
    
    print("\n" + "="*70)
    print("RESULT")
    print("="*70)
    print(f"Result keys: {list(result.keys())}")
    
    if "scrapy_test_1" in result:
        editorial = result["scrapy_test_1"]
        print(f"\nEditorial context for scrapy_test_1:")
        print(f"  available: {editorial.get('available')}")
        print(f"  sources_count: {editorial.get('sources_count')}")
        print(f"  _reason: {editorial.get('_reason')}")
        print(f"  _engine_version: {editorial.get('_engine_version')}")
        
        if editorial.get('available'):
            print(f"  signals: {len(editorial.get('signals', []))} signals")
            print(f"  consensus_market: {editorial.get('consensus_market')}")
        else:
            print(f"\n  ℹ️  Editorial context not available (expected for synthetic matches)")
            print(f"  ℹ️  This is CORRECT fail-soft behavior")
    
    print("\n" + "="*70)
    print("Check backend logs for Scrapy subprocess execution:")
    print("  tail -n 100 /var/log/supervisor/backend.*.log | grep SCRAPY_EDITORIAL")
    print("="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(test_scrapy())
