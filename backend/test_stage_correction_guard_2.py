"""Test _apply_stage_correction: rewriting 'motivación normal' in discarded_market."""
import sys
sys.path.insert(0, '/app/backend')

from services.analyst_engine import _apply_stage_correction

input_payload = [
    {
        "match_id": 2001,
        "league": "Copa del Rey",
        "round": "Final",
        "home_team": {"name": "Real Madrid"},
        "away_team": {"name": "Barcelona"},
    },
]

llm_output = {
    "verdict": "no_value",
    "picks": [],
    "summary": {
        "high_confidence": [],
        "medium_confidence": [],
        "discarded_motivation": [],
        "discarded_market": [
            {
                "match_id": 2001,
                "match_label": "Real Madrid vs Barcelona",
                "reason": "Ambos equipos tienen motivación normal. Cuotas no atractivas.",
            }
        ],
        "incomplete_data": [],
        "total_analyzed": 1,
        "total_recommended": 0,
        "total_discarded": 1,
    },
}

print("=" * 80)
print("TEST: _apply_stage_correction - rewrite 'motivación normal' in discarded_market")
print("=" * 80)
print("\nINPUT:")
print(f"  - Match 2001: {input_payload[0]['league']} - {input_payload[0]['round']}")
print("\nLLM OUTPUT (before correction):")
print(f"  discarded_market[0].reason: {llm_output['summary']['discarded_market'][0]['reason']}")

corrected = _apply_stage_correction(llm_output, input_payload)

print("\nOUTPUT (after correction):")
disc_mkt = corrected['summary']['discarded_market']
print(f"  discarded_market[0].reason: {disc_mkt[0]['reason']}")
print(f"  _stage_corrected: {disc_mkt[0].get('_stage_corrected', False)}")

meta = corrected.get('_pipeline', {}).get('stage_corrections', {})
print("\nPIPELINE METADATA:")
print(f"  moved_finals_to_market: {meta.get('moved_finals_to_market', 0)}")
print(f"  rewrote_normal_reasons: {meta.get('rewrote_normal_reasons', 0)}")

# Assertions
assert 'motivación máxima' in disc_mkt[0]['reason'], \
    f"Reason not corrected: {disc_mkt[0]['reason']}"
assert 'motivación normal' not in disc_mkt[0]['reason'].lower(), \
    f"'motivación normal' still present: {disc_mkt[0]['reason']}"
assert disc_mkt[0].get('_stage_corrected') is True, "_stage_corrected flag missing"
assert meta['rewrote_normal_reasons'] == 1, f"Expected 1 rewrite, got {meta['rewrote_normal_reasons']}"

print("\n" + "=" * 80)
print("✅ ALL TESTS PASSED")
print("=" * 80)
