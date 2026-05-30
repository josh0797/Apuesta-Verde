"""
MLB Pattern Alignment Classifier - Backend Testing
===================================================
Tests the new pattern alignment classifier feature that classifies
Spanish-language trend phrases as SUPPORTS/OPPOSES/NEUTRAL relative
to the final recommended market.
"""
import sys
import json
from datetime import datetime

# Test the classifier directly (unit-level)
print("\n" + "="*70)
print("BACKEND TEST: MLB Pattern Alignment Classifier")
print("="*70)

# ── Test 1: Unit-level classifier validation ──────────────────────────
print("\n[TEST 1] Unit-level classifier validation")
print("-" * 70)

try:
    from services.pattern_alignment_classifier import (
        classify_pattern_alignment,
        classify_patterns_for_market,
    )
    print("✅ Successfully imported pattern_alignment_classifier")
except Exception as e:
    print(f"❌ Failed to import pattern_alignment_classifier: {e}")
    sys.exit(1)

# Test cases from the feature spec
test_cases = [
    {
        "pattern": "El equipo local no superó 4.5 carreras en 9 de sus últimos 15 partidos.",
        "market": "Total Runs Over 9.5",
        "expected_alignment": "OPPOSES",
        "description": "Cold offense pattern vs Over market"
    },
    {
        "pattern": "El equipo local no superó 4.5 carreras en 9 de sus últimos 15 partidos.",
        "market": "Total Runs Under 8.5",
        "expected_alignment": "SUPPORTS",
        "description": "Cold offense pattern vs Under market"
    },
    {
        "pattern": "El equipo visitante anotó más de 4.5 carreras en 10 de sus últimos 15 partidos.",
        "market": "Total Runs Over 9.5",
        "expected_alignment": "SUPPORTS",
        "description": "Hot offense pattern vs Over market"
    },
    {
        "pattern": "Abridor local con ERA elite (2.85) en la temporada.",
        "market": "Total Runs Over 9.5",
        "expected_alignment": "OPPOSES",
        "description": "Elite pitcher ERA vs Over market"
    },
    {
        "pattern": "Los primeros 5 innings históricamente cierran por debajo de la media (lean Under F5).",
        "market": "Total Runs Under 8.5",
        "expected_alignment": "SUPPORTS",
        "description": "F5 Under lean vs Under market"
    },
    {
        "pattern": "Muestra insuficiente: usando promedio de liga como referencia.",
        "market": "Total Runs Over 9.5",
        "expected_alignment": "NEUTRAL",
        "description": "Informational pattern (any market)"
    },
    {
        "pattern": "El bullpen visitante llega cargado tras 5 juegos en los últimos 3 días.",
        "market": "NRFI",
        "expected_alignment": "OPPOSES",
        "description": "Fatigued bullpen vs NRFI"
    },
    {
        "pattern": "El equipo local anotó más de 4.5 carreras en 10 de sus últimos 15 partidos.",
        "market": "Moneyline",
        "expected_alignment": "NEUTRAL",
        "description": "Any scoring pattern vs Moneyline"
    },
]

passed = 0
failed = 0

for i, tc in enumerate(test_cases, 1):
    try:
        result = classify_pattern_alignment(tc["pattern"], tc["market"])
        alignment = result.get("alignment")
        
        if alignment == tc["expected_alignment"]:
            print(f"✅ Test {i}: {tc['description']}")
            print(f"   Pattern: {tc['pattern'][:60]}...")
            print(f"   Market: {tc['market']}")
            print(f"   Expected: {tc['expected_alignment']} | Got: {alignment}")
            passed += 1
        else:
            print(f"❌ Test {i}: {tc['description']}")
            print(f"   Pattern: {tc['pattern'][:60]}...")
            print(f"   Market: {tc['market']}")
            print(f"   Expected: {tc['expected_alignment']} | Got: {alignment}")
            print(f"   Full result: {json.dumps(result, indent=2, ensure_ascii=False)}")
            failed += 1
    except Exception as e:
        print(f"❌ Test {i} crashed: {e}")
        failed += 1

print(f"\n[TEST 1 SUMMARY] Passed: {passed}/{len(test_cases)} | Failed: {failed}/{len(test_cases)}")

# ── Test 2: Batch classifier (classify_patterns_for_market) ────────────
print("\n[TEST 2] Batch classifier validation")
print("-" * 70)

test_patterns = [
    "El equipo local no superó 4.5 carreras en 9 de sus últimos 15 partidos.",
    "El equipo visitante anotó más de 4.5 carreras en 10 de sus últimos 15 partidos.",
    "Abridor local con ERA elite (2.85) en la temporada.",
    "Muestra insuficiente: usando promedio de liga como referencia.",
]

try:
    batch_result = classify_patterns_for_market(test_patterns, "Total Runs Over 9.5")
    
    # Validate structure
    required_keys = ["recommendedMarket", "marketPolarity", "supports", "opposes", "neutral", "counts", "summary", "consistency"]
    missing_keys = [k for k in required_keys if k not in batch_result]
    
    if missing_keys:
        print(f"❌ Missing keys in batch result: {missing_keys}")
        failed += 1
    else:
        print("✅ Batch result has all required keys")
        print(f"   Recommended Market: {batch_result['recommendedMarket']}")
        print(f"   Market Polarity: {batch_result['marketPolarity']}")
        print(f"   Supports: {batch_result['counts']['supports']}")
        print(f"   Opposes: {batch_result['counts']['opposes']}")
        print(f"   Neutral: {batch_result['counts']['neutral']}")
        print(f"   Summary: {batch_result['summary']}")
        print(f"   Consistency: {batch_result['consistency']}")
        
        # Verify sum matches input
        total_classified = sum(batch_result['counts'].values())
        if total_classified == len(test_patterns):
            print(f"✅ All {len(test_patterns)} patterns classified (none dropped)")
            passed += 1
        else:
            print(f"❌ Pattern count mismatch: {total_classified} classified vs {len(test_patterns)} input")
            failed += 1
        
        # Verify consistency is valid
        valid_consistency = ["STRONG", "MIXED", "CONFLICTED", "INFO_ONLY"]
        if batch_result['consistency'] in valid_consistency:
            print(f"✅ Consistency value is valid: {batch_result['consistency']}")
            passed += 1
        else:
            print(f"❌ Invalid consistency value: {batch_result['consistency']}")
            failed += 1
            
except Exception as e:
    print(f"❌ Batch classifier test failed: {e}")
    import traceback
    traceback.print_exc()
    failed += 1

# ── Test 3: Integration test - /api/mlb/day endpoint ───────────────────
print("\n[TEST 3] Integration test - /api/mlb/day endpoint")
print("-" * 70)

import requests

BASE_URL = "https://low-volatility-plays.preview.emergentagent.com"
LOGIN_EMAIL = "demo@valuebet.app"
LOGIN_PASSWORD = "demo1234"

try:
    # Login
    print(f"Logging in as {LOGIN_EMAIL}...")
    login_resp = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        timeout=10
    )
    
    if login_resp.status_code != 200:
        print(f"❌ Login failed: {login_resp.status_code}")
        print(f"   Response: {login_resp.text[:200]}")
        failed += 1
    else:
        print("✅ Login successful")
        token = login_resp.json().get("token")
        
        # Call /api/mlb/day
        print("\nFetching MLB picks from /api/mlb/day...")
        headers = {"Authorization": f"Bearer {token}"}
        mlb_resp = requests.get(
            f"{BASE_URL}/api/mlb/day",
            headers=headers,
            timeout=30
        )
        
        if mlb_resp.status_code != 200:
            print(f"❌ /api/mlb/day failed: {mlb_resp.status_code}")
            print(f"   Response: {mlb_resp.text[:200]}")
            failed += 1
        else:
            print("✅ /api/mlb/day returned 200")
            data = mlb_resp.json()
            picks = data.get("picks", [])
            
            print(f"   Found {len(picks)} picks")
            
            if len(picks) == 0:
                print("⚠️  No picks returned (might be off-season or no games today)")
                print("   This is not a regression - validating structure only")
                
                # Check for pipeline_meta to understand why
                pipeline_meta = data.get("pipeline_meta", {})
                print(f"   Pipeline meta: schedule_games_found={pipeline_meta.get('schedule_games_found')}")
                print(f"   Abort reason: {pipeline_meta.get('abort_reason')}")
                passed += 1
            else:
                # Find at least one pick with patternAlignment
                found_alignment = False
                found_trendSummary = False
                found_overUnderLean = False
                
                for pick in picks:
                    hist_profile = pick.get("baseballHistoricalProfile", {})
                    combined = hist_profile.get("combined", {})
                    
                    # Check for patternAlignment
                    pattern_alignment = combined.get("patternAlignment")
                    if pattern_alignment:
                        found_alignment = True
                        print(f"\n✅ Found patternAlignment in pick {pick.get('match_label', 'unknown')}")
                        print(f"   Recommended Market: {pattern_alignment.get('recommendedMarket')}")
                        print(f"   Market Polarity: {pattern_alignment.get('marketPolarity')}")
                        print(f"   Supports: {len(pattern_alignment.get('supports', []))}")
                        print(f"   Opposes: {len(pattern_alignment.get('opposes', []))}")
                        print(f"   Neutral: {len(pattern_alignment.get('neutral', []))}")
                        print(f"   Consistency: {pattern_alignment.get('consistency')}")
                        print(f"   Summary: {pattern_alignment.get('summary')}")
                        
                        # Verify sum matches trendSummary length
                        trend_summary = combined.get("trendSummary", [])
                        total_classified = (
                            len(pattern_alignment.get('supports', [])) +
                            len(pattern_alignment.get('opposes', [])) +
                            len(pattern_alignment.get('neutral', []))
                        )
                        
                        if total_classified == len(trend_summary):
                            print(f"✅ All {len(trend_summary)} phrases classified (none dropped)")
                            passed += 1
                        else:
                            print(f"❌ Pattern count mismatch: {total_classified} classified vs {len(trend_summary)} in trendSummary")
                            failed += 1
                    
                    # Check backward compatibility - trendSummary still present
                    if combined.get("trendSummary"):
                        found_trendSummary = True
                    
                    # Check backward compatibility - overUnderLean still present
                    if combined.get("overUnderLean"):
                        found_overUnderLean = True
                
                if found_alignment:
                    print("\n✅ At least one pick has patternAlignment")
                    passed += 1
                else:
                    print("\n⚠️  No picks have patternAlignment (might be empty trendSummary)")
                    # This is not necessarily a failure - picks might not have historical data
                
                if found_trendSummary:
                    print("✅ Backward compat: trendSummary still present")
                    passed += 1
                else:
                    print("❌ Backward compat broken: trendSummary missing")
                    failed += 1
                
                if found_overUnderLean:
                    print("✅ Backward compat: overUnderLean still present")
                    passed += 1
                else:
                    print("❌ Backward compat broken: overUnderLean missing")
                    failed += 1
                    
except Exception as e:
    print(f"❌ Integration test failed: {e}")
    import traceback
    traceback.print_exc()
    failed += 1

# ── Final Summary ──────────────────────────────────────────────────────
print("\n" + "="*70)
print("FINAL SUMMARY")
print("="*70)
print(f"Total Passed: {passed}")
print(f"Total Failed: {failed}")

if failed == 0:
    print("\n✅ ALL TESTS PASSED")
    sys.exit(0)
else:
    print(f"\n❌ {failed} TEST(S) FAILED")
    sys.exit(1)
