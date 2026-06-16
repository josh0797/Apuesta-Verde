/**
 * F94.2 — WorldCupLiveCard tests.
 *
 * Mandatory coverage (per user spec):
 *   1. Iran vs New Zealand (mock fixture) renders pinned at the top
 *      with FIFA World Cup badge, minute 24', and the F93-style
 *      manual odds CTA.
 *   2. The manual odds CTA reveals an input, accepts a valid value,
 *      persists it to localStorage, and surfaces a "Saved odds" badge.
 *   3. When secondary_reasons includes VISIBLE_PENDING_MARKET, the
 *      "Visible / pending market" notice is shown.
 *   4. When no items are World Cup, the component returns null.
 *   5. world_cup_hidden_by_filter > 0 → contract violation warning.
 *   6. English copy mirrors Spanish copy.
 */
import { fireEvent, render, screen } from '@testing-library/react';

import { WorldCupLiveCard } from '../WorldCupLiveCard';

// ─────────────────────────────────────────────────────────────────────
// Reset localStorage between tests so the saved-odds badge does not
// leak across cases.
// ─────────────────────────────────────────────────────────────────────
beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* no-op */ }
});

// Build the user-reported real-world Iran vs New Zealand fixture.
const IRAN_NZ_FIXTURE = {
  fixture_id:        'fx-wc-iran-nz',
  elapsed:           24,
  status_short:      '1H',
  league:            { id: null, name: 'FIFA World Cup', country: 'World' },
  teams:             { home: { name: 'Iran' }, away: { name: 'New Zealand' } },
  analysis_status:   'ANALYZABLE',
  discard_reason:    null,
  secondary_reasons: ['SPORTYTRADER_NOT_FOUND', 'VISIBLE_PENDING_MARKET'],
  _is_world_cup:     true,
};

const DEBUG_WITH_WC = {
  world_cup_live_count:       1,
  world_cup_live_detected:    true,
  world_cup_hidden_by_filter: 0,
  world_cup_examples:         ['Iran vs New Zealand'],
};

// ─────────────────────────────────────────────────────────────────────
// 1. Iran vs New Zealand renders with FIFA World Cup badge + minute.
// ─────────────────────────────────────────────────────────────────────
test('renders Iran vs New Zealand pinned with FIFA World Cup badge', () => {
  render(
    <WorldCupLiveCard
      items={[IRAN_NZ_FIXTURE]}
      worldCupDebug={DEBUG_WITH_WC}
      lang="es"
    />
  );

  const teams = screen.getByTestId('world-cup-live-card-row-fx-wc-iran-nz-teams');
  expect(teams).toHaveTextContent('Iran');
  expect(teams).toHaveTextContent('New Zealand');

  const minute = screen.getByTestId('world-cup-live-card-row-fx-wc-iran-nz-minute');
  expect(minute).toHaveTextContent("24'");

  const league = screen.getByTestId('world-cup-live-card-row-fx-wc-iran-nz-league');
  expect(league).toHaveTextContent('FIFA World Cup');
  expect(league).toHaveTextContent('World');

  // Title shows the canonical count.
  expect(screen.getByTestId('world-cup-live-card-title'))
    .toHaveTextContent(/FIFA Copa del Mundo en vivo — 1 partido/);
});

// ─────────────────────────────────────────────────────────────────────
// 2. Manual odds CTA → input → save → "Saved odds" badge.
// ─────────────────────────────────────────────────────────────────────
test('manual odds CTA captures and persists user input', () => {
  render(
    <WorldCupLiveCard
      items={[IRAN_NZ_FIXTURE]}
      worldCupDebug={DEBUG_WITH_WC}
      lang="es"
    />
  );

  // Initial state — CTA visible, no input yet.
  const openBtn = screen.getByTestId(
    'world-cup-live-card-row-fx-wc-iran-nz-manual-odds-open'
  );
  expect(openBtn).toHaveTextContent(/Ingresar cuota manual/);
  fireEvent.click(openBtn);

  // Input appears.
  const input = screen.getByTestId(
    'world-cup-live-card-row-fx-wc-iran-nz-manual-odds-input'
  );
  fireEvent.change(input, { target: { value: '2.10' } });
  fireEvent.click(
    screen.getByTestId('world-cup-live-card-row-fx-wc-iran-nz-manual-odds-save')
  );

  // "Saved odds" badge surfaces the captured value.
  const saved = screen.getByTestId(
    'world-cup-live-card-row-fx-wc-iran-nz-manual-odds-saved'
  );
  expect(saved).toHaveTextContent(/Cuota guardada/);
  expect(saved).toHaveTextContent(/2\.1/);

  // localStorage persists the value.
  expect(window.localStorage.getItem('wc_manual_odds:fx-wc-iran-nz')).toBe('2.1');
});

// ─────────────────────────────────────────────────────────────────────
// 3. VISIBLE_PENDING_MARKET surfaces explanatory notice.
// ─────────────────────────────────────────────────────────────────────
test('shows VISIBLE_PENDING_MARKET notice when no odds available', () => {
  render(
    <WorldCupLiveCard
      items={[IRAN_NZ_FIXTURE]}
      worldCupDebug={DEBUG_WITH_WC}
      lang="es"
    />
  );

  const pending = screen.getByTestId(
    'world-cup-live-card-row-fx-wc-iran-nz-pending'
  );
  expect(pending).toHaveTextContent(/Visible \/ pendiente de mercado/);
});

// ─────────────────────────────────────────────────────────────────────
// 4. No World Cup items → render null.
// ─────────────────────────────────────────────────────────────────────
test('returns null when there are no World Cup items', () => {
  const otherFixture = {
    fixture_id:      'fx-other',
    league:          { name: 'Serie B', country: 'Brazil' },
    teams:           { home: { name: 'Avai' }, away: { name: 'Sport' } },
    analysis_status: 'DISCARDED',
    _is_world_cup:   false,
  };
  const { container } = render(
    <WorldCupLiveCard items={[otherFixture]} lang="es" />
  );
  expect(container).toBeEmptyDOMElement();
});

// ─────────────────────────────────────────────────────────────────────
// 5. world_cup_hidden_by_filter > 0 → contract-violation warning.
// ─────────────────────────────────────────────────────────────────────
test('surfaces contract-violation warning when WC was hidden by a filter', () => {
  render(
    <WorldCupLiveCard
      items={[IRAN_NZ_FIXTURE]}
      worldCupDebug={{ ...DEBUG_WITH_WC, world_cup_hidden_by_filter: 1 }}
      lang="es"
    />
  );
  const warn = screen.getByTestId('world-cup-live-card-hidden-warn');
  expect(warn).toHaveTextContent(/violación de contrato/);
});

// ─────────────────────────────────────────────────────────────────────
// 6. English copy parity check.
// ─────────────────────────────────────────────────────────────────────
test('renders English copy when lang="en"', () => {
  render(
    <WorldCupLiveCard
      items={[IRAN_NZ_FIXTURE]}
      worldCupDebug={DEBUG_WITH_WC}
      lang="en"
    />
  );
  expect(screen.getByTestId('world-cup-live-card-title'))
    .toHaveTextContent(/FIFA World Cup live — 1 match/);
  expect(screen.getByTestId(
    'world-cup-live-card-row-fx-wc-iran-nz-manual-odds-open'
  )).toHaveTextContent(/Add manual odds/);
});
