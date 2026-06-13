/**
 * Phase F83.1 — Match identity & odds resolvers.
 *
 * These helpers consolidate the many shapes the dashboard uses to
 * identify a match and to surface its detected odd. They MUST be the
 * single source of truth for any UI panel that needs to:
 *
 *   1. Send a POST to /api/football/... with a ``match_id``.
 *   2. Pre-fill an odds input with the *detected* odd of THIS card —
 *      never a globally-shared odd that would leak across cards.
 *
 * The functions are intentionally permissive about the input shapes
 * (match, pick, auditRow) so callers can pass whatever they have on
 * hand. They never throw; they return ``null`` when nothing is usable.
 */

/**
 * Resolve the canonical match_id (as a non-empty string) for a card.
 *
 * Order of preference — first non-blank wins:
 *   match.match_id
 *   match.id
 *   match.fixture_id
 *   match.fixture.id
 *   match.game_id
 *   match.live_stats.game_pk
 *   pick.match_id
 *   pick.fixture_id
 *   pick.match.match_id
 *   auditRow.match_id
 *   auditRow.fixture_id
 *   auditRow.market_trace.match_id
 *
 * @returns {string|null}
 */
export function resolveMatchId(match, pick, auditRow) {
  const candidates = [
    match?.match_id,
    match?.id,
    match?.fixture_id,
    match?.fixture?.id,
    match?.game_id,
    match?.live_stats?.game_pk,
    pick?.match_id,
    pick?.fixture_id,
    pick?.match?.match_id,
    auditRow?.match_id,
    auditRow?.fixture_id,
    auditRow?.market_trace?.match_id,
  ];
  for (const value of candidates) {
    if (value === null || value === undefined) continue;
    const s = String(value).trim();
    if (s && s !== 'undefined' && s !== 'null' && s !== 'NaN') return s;
  }
  // Diagnostic log — helps the UI engineer trace why a panel cannot
  // build a manual-market payload. Only logs the shape, never PII.
  // eslint-disable-next-line no-console
  console.warn('[manual_market_identity] missing match_id', {
    match_keys:    match    ? Object.keys(match)    : null,
    pick_keys:     pick     ? Object.keys(pick)     : null,
    audit_keys:    auditRow ? Object.keys(auditRow) : null,
  });
  return null;
}

/**
 * Resolve the *detected* odd for a card (always a finite number in the
 * inclusive range [1.01, 50.0], or ``null``).
 *
 * Each card MUST resolve its own detected odd — never reach for a
 * dashboard-level shared variable, otherwise every card would render
 * the same number (the production bug from Phase F83.1).
 *
 * @returns {number|null}
 */
export function resolveDetectedOdd(match, pick, auditRow) {
  const candidates = [
    auditRow?.market_trace?.detected_odd,
    auditRow?.market_trace?.odd,
    auditRow?.odd,
    auditRow?.detected_odd,
    pick?.manual_market_identity?.detected_odd,
    pick?.detected_odd,
    pick?.odd,
    pick?.odds,
    match?.manual_market_identity?.detected_odd,
    match?.detected_odd,
    match?.odds_detected,
  ];
  for (const value of candidates) {
    const n = Number(value);
    if (Number.isFinite(n) && n >= 1.01 && n <= 50) return n;
  }
  return null;
}
