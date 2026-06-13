/**
 * Phase F83.1 — Tests for matchResolver helpers.
 *
 * Both helpers guarantee:
 *   1. ``resolveMatchId(...)`` always returns a non-blank string or null
 *      (never undefined/NaN/"null"/"undefined" leaks through).
 *   2. ``resolveDetectedOdd(...)`` always returns a finite number in
 *      [1.01, 50] or null — never a shared value across cards.
 */

import { resolveMatchId, resolveDetectedOdd } from '../matchResolver';

// Silence the diagnostic warn() inside resolveMatchId during tests.
const originalWarn = console.warn;
beforeEach(() => { console.warn = jest.fn(); });
afterAll(()  => { console.warn = originalWarn; });

describe('resolveMatchId', () => {
  it('returns match_id from match object', () => {
    expect(resolveMatchId({ match_id: 'abc-123' })).toBe('abc-123');
  });

  it('coerces a numeric match_id to a string', () => {
    expect(resolveMatchId({ match_id: 12345 })).toBe('12345');
  });

  it('falls back to fixture_id then id then game_id', () => {
    expect(resolveMatchId({ fixture_id: 'fx-1' })).toBe('fx-1');
    expect(resolveMatchId({ id: 999 })).toBe('999');
    expect(resolveMatchId({ game_id: 'g-7' })).toBe('g-7');
  });

  it('reads from nested fixture.id', () => {
    expect(resolveMatchId({ fixture: { id: 'nested-1' } })).toBe('nested-1');
  });

  it('reads from live_stats.game_pk for MLB-style payloads', () => {
    expect(resolveMatchId({ live_stats: { game_pk: 7777 } })).toBe('7777');
  });

  it('falls back to pick.match_id when match has no ID', () => {
    expect(resolveMatchId({}, { match_id: 'from-pick' })).toBe('from-pick');
  });

  it('falls back to auditRow.market_trace.match_id as last resort', () => {
    expect(resolveMatchId({}, {}, { market_trace: { match_id: 'from-trace' } }))
      .toBe('from-trace');
  });

  it('returns null when all candidates are missing / blank / sentinel', () => {
    expect(resolveMatchId({})).toBeNull();
    expect(resolveMatchId({ match_id: null })).toBeNull();
    expect(resolveMatchId({ match_id: undefined })).toBeNull();
    expect(resolveMatchId({ match_id: '' })).toBeNull();
    expect(resolveMatchId({ match_id: '   ' })).toBeNull();
    expect(resolveMatchId({ match_id: 'undefined' })).toBeNull();
    expect(resolveMatchId({ match_id: 'null' })).toBeNull();
    expect(resolveMatchId({ match_id: 'NaN' })).toBeNull();
  });

  it('never throws on weird input', () => {
    expect(() => resolveMatchId(null, null, null)).not.toThrow();
    expect(() => resolveMatchId(undefined)).not.toThrow();
  });
});


describe('resolveDetectedOdd', () => {
  it('returns trace.detected_odd when present', () => {
    expect(resolveDetectedOdd({}, {}, {
      market_trace: { detected_odd: 2.15 },
    })).toBe(2.15);
  });

  it('falls back through pick → match', () => {
    expect(resolveDetectedOdd({}, { detected_odd: 1.85 })).toBe(1.85);
    expect(resolveDetectedOdd({ detected_odd: 3.40 })).toBe(3.40);
  });

  it('reads from manual_market_identity.detected_odd', () => {
    expect(resolveDetectedOdd({}, {
      manual_market_identity: { detected_odd: 1.92 },
    })).toBe(1.92);
  });

  it('rejects out-of-range numbers (<1.01 or >50)', () => {
    expect(resolveDetectedOdd({ detected_odd: 0.95 })).toBeNull();
    expect(resolveDetectedOdd({ detected_odd: 99 })).toBeNull();
    expect(resolveDetectedOdd({ detected_odd: -2 })).toBeNull();
  });

  it('rejects non-finite values', () => {
    expect(resolveDetectedOdd({ detected_odd: 'abc' })).toBeNull();
    expect(resolveDetectedOdd({ detected_odd: NaN })).toBeNull();
    expect(resolveDetectedOdd({ detected_odd: Infinity })).toBeNull();
  });

  it('returns null when nothing valid is found', () => {
    expect(resolveDetectedOdd({})).toBeNull();
    expect(resolveDetectedOdd(null, null, null)).toBeNull();
  });

  it('does NOT leak the same value across two different cards', () => {
    /* Phase F83.1 regression test for the production bug where every
       card showed 1.25 because the UI reached for a shared variable. */
    const card1 = { match_id: 'A', detected_odd: 1.25 };
    const card2 = { match_id: 'B', detected_odd: 2.75 };
    const odd1 = resolveDetectedOdd(card1, card1, card1);
    const odd2 = resolveDetectedOdd(card2, card2, card2);
    expect(odd1).toBe(1.25);
    expect(odd2).toBe(2.75);
    expect(odd1).not.toBe(odd2);
  });
});
