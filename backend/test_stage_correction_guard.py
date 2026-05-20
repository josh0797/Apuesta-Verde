"""Synthetic test for _apply_stage_correction guard.

Verifies that finals/knockouts misclassified in discarded_motivation are
automatically moved to discarded_market with corrected reasons.
"""
import sys
sys.path.insert(0, '/app/backend')

from services.analyst_engine import _apply_stage_correction

# Test case 1: Final wrongly in discarded_motivation
input_payload = [
    {
        "match_id": 1001,
        "league": "UEFA Europa League",
        "round": "Final",
        "home_team": {"name": "SC Freiburg"},
        "away_team": {"name": "Aston Villa"},
    },
    {
        "match_id": 1002,
        "league": "Copa Libertadores",
        "round": "Cuartos de final - Ida",
        "home_team": {"name": "Flamengo"},
        "away_team": {"name": "Estudiantes"},
    },
]

llm_output = {
    "verdict": "no_value",
    "picks": [],
    "summary": {
        "high_confidence": [],
        "medium_confidence": [],
        "discarded_motivation": [
            {
                "match_id": 1001,
                "match_label": "SC Freiburg vs Aston Villa",
                "reason": "Ambos equipos tienen motivación normal",
                "motivation_state": "NORMAL",
            },
            {
                "match_id": 1002,
                "match_label": "Flamengo vs Estudiantes",
                "reason": "Sin urgencia competitiva",
                "motivation_state": "LOW_BOTH",
            },
        ],
        "discarded_market": [],
        "incomplete_data": [],
        "total_analyzed": 2,
        "total_recommended": 0,
        "total_discarded": 2,
    },
}

print("=" * 80)
print("TEST: _apply_stage_correction guard")
print("=" * 80)
print("\nINPUT:")
print(f"  - Match 1001: {input_payload[0]['league']} - {input_payload[0]['round']}")
print(f"  - Match 1002: {input_payload[1]['league']} - {input_payload[1]['round']}")
print("\nLLM OUTPUT (before correction):")
print(f"  discarded_motivation: {len(llm_output['summary']['discarded_motivation'])} matches")
print(f"  discarded_market: {len(llm_output['summary']['discarded_market'])} matches")

# Apply the guard
corrected = _apply_stage_correction(llm_output, input_payload)

print("\nOUTPUT (after correction):")
print(f"  discarded_motivation: {len(corrected['summary']['discarded_motivation'])} matches")
print(f"  discarded_market: {len(corrected['summary']['discarded_market'])} matches")

disc_mot = corrected['summary']['discarded_motivation']
disc_mkt = corrected['summary']['discarded_market']

print("\nDISCARDED_MOTIVATION (should be empty or only non-finals):")
for entry in disc_mot:
    print(f"  - {entry['match_id']}: {entry['match_label']} | {entry['reason']}")

print("\nDISCARDED_MARKET (should contain both finals with corrected reasons):")
for entry in disc_mkt:
    print(f"  - {entry['match_id']}: {entry['match_label']}")
    print(f"    Reason: {entry['reason']}")
    print(f"    Stage corrected: {entry.get('_stage_corrected', False)}")

# Verify corrections
meta = corrected.get('_pipeline', {}).get('stage_corrections', {})
print("\nPIPELINE METADATA:")
print(f"  moved_finals_to_market: {meta.get('moved_finals_to_market', 0)}")
print(f"  rewrote_normal_reasons: {meta.get('rewrote_normal_reasons', 0)}")
print(f"  forced_motivation_state: {meta.get('forced_motivation_state', 0)}")

# Assertions
assert len(disc_mot) == 0, f"Expected 0 in discarded_motivation, got {len(disc_mot)}"
assert len(disc_mkt) == 2, f"Expected 2 in discarded_market, got {len(disc_mkt)}"
assert meta['moved_finals_to_market'] == 2, f"Expected 2 moved, got {meta['moved_finals_to_market']}"

# Check that reasons were corrected
for entry in disc_mkt:
    assert 'motivación máxima' in entry['reason'] or 'alta presión' in entry['reason'], \
        f"Reason not corrected for {entry['match_id']}: {entry['reason']}"
    assert entry.get('_stage_corrected') is True, f"_stage_corrected flag missing for {entry['match_id']}"

print("\n" + "=" * 80)
print("✅ ALL TESTS PASSED")
print("=" * 80)
