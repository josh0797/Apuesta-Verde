/**
 * F94 — DashboardDiscardedSummary tests.
 *
 * Tests the restored "discards always visible on the Dashboard" behaviour:
 *
 *   1. When sport === 'football' AND recommended === 0 AND there are
 *      discarded fixtures, the banner + counter + bucket pills render
 *      with the exact F94 copy.
 *   2. The detail list is COLLAPSED by default and expands on click,
 *      revealing each fixture with match label, reason, secondary
 *      reasons, stage and provider/status.
 *   3. Non-football sports never render anything (F94 scope guard).
 *   4. Recommended > 0 → component returns null (existing detail block
 *      handles that case).
 *   5. Back-compat: legacy items with only `reason` / `missing` still
 *      render without throwing.
 */
import { fireEvent, render, screen } from '@testing-library/react';

import { DashboardDiscardedSummary } from '../DashboardDiscardedSummary';

// ─────────────────────────────────────────────────────────────────────
// Sample payloads
// ─────────────────────────────────────────────────────────────────────

const FOOTBALL_DISCARDED_MARKET = [
  {
    match_id: 'm-1',
    match_label: 'Foo FC vs Bar United',
    discard_reason: 'EDGE_INSUFFICIENT',
    secondary_reasons: ['LOW_CONFIDENCE', 'ODDS_INFLATED'],
    stage: 'MARKET_GUARDRAIL',
    provider: 'TheStatsAPI',
    status: 'NO_VALUE',
  },
];

const FOOTBALL_DISCARDED_MOTIVATION = [
  {
    match_id: 'm-2',
    match_label: 'Alpha vs Beta',
    reason: 'Equipo sin nada en juego',  // legacy field
    stage: 'MOTIVATION_GATE',
  },
];

const FOOTBALL_INCOMPLETE = [
  {
    match_id: 'm-3',
    match_label: 'Delta vs Echo',
    missing: 'odds_missing',  // legacy field
  },
];

const SUMMARY = { total_analyzed: 7 };

// ─────────────────────────────────────────────────────────────────────
// 1. Banner + counter + bucket pills render for football w/ 0 picks.
// ─────────────────────────────────────────────────────────────────────
test('shows intro banner and collapsed counter for football w/ 0 recommended', () => {
  render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={FOOTBALL_DISCARDED_MOTIVATION}
      discardedMarket={FOOTBALL_DISCARDED_MARKET}
      incomplete={FOOTBALL_INCOMPLETE}
      lang="es"
    />
  );

  // Intro banner copy includes the exact text agreed with the user.
  const banner = screen.getByTestId('dashboard-discarded-summary-banner-text');
  expect(banner.textContent).toMatch(/No hay picks recomendados hoy/);
  expect(banner.textContent).toMatch(/se analizaron 7 partidos/);
  expect(banner.textContent).toMatch(/Revisa los descartados/);

  // Collapsed counter shows total discards across all buckets.
  const counter = screen.getByTestId('dashboard-discarded-summary-counter');
  expect(counter.textContent).toMatch(/3 partidos descartados/);
  expect(counter.textContent).toMatch(/ver detalle/);

  // Bucket pills surface each non-empty group with its count.
  expect(screen.getByTestId('dashboard-discarded-summary-bucket-pill-motivation')).toHaveTextContent('1');
  expect(screen.getByTestId('dashboard-discarded-summary-bucket-pill-market')).toHaveTextContent('1');
  expect(screen.getByTestId('dashboard-discarded-summary-bucket-pill-incomplete')).toHaveTextContent('1');

  // Detail content is NOT visible yet (collapsed by default).
  expect(screen.queryByTestId('dashboard-discarded-summary-content-wrapper')).toBeNull();
});

// ─────────────────────────────────────────────────────────────────────
// 2. Expanding reveals match, reason, secondary, stage, provider/status.
// ─────────────────────────────────────────────────────────────────────
test('expands on click and shows match + reason + secondary + stage + provider + status', () => {
  render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={[]}
      discardedMarket={FOOTBALL_DISCARDED_MARKET}
      incomplete={[]}
      lang="es"
    />
  );

  // Click toggle to expand.
  fireEvent.click(screen.getByTestId('dashboard-discarded-summary-toggle'));

  // Content wrapper is now mounted.
  expect(screen.getByTestId('dashboard-discarded-summary-content-wrapper')).toBeInTheDocument();

  // Row exposes match label, reason, secondary reasons, stage, provider, status.
  const rowId = 'dashboard-discarded-summary-row-market-0';
  expect(screen.getByTestId(`${rowId}-match`)).toHaveTextContent('Foo FC vs Bar United');
  expect(screen.getByTestId(`${rowId}-reason`)).toHaveTextContent(/EDGE_INSUFFICIENT/);
  expect(screen.getByTestId(`${rowId}-secondary-0`)).toHaveTextContent('LOW_CONFIDENCE');
  expect(screen.getByTestId(`${rowId}-secondary-1`)).toHaveTextContent('ODDS_INFLATED');
  expect(screen.getByTestId(`${rowId}-stage`)).toHaveTextContent('MARKET_GUARDRAIL');
  expect(screen.getByTestId(`${rowId}-provider`)).toHaveTextContent('TheStatsAPI');
  expect(screen.getByTestId(`${rowId}-status`)).toHaveTextContent('NO_VALUE');
});

// ─────────────────────────────────────────────────────────────────────
// 3. Non-football sports → renders nothing (F94 scope guard).
// ─────────────────────────────────────────────────────────────────────
test('does NOT render for non-football sports', () => {
  const { container } = render(
    <DashboardDiscardedSummary
      sport="baseball"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={FOOTBALL_DISCARDED_MOTIVATION}
      discardedMarket={FOOTBALL_DISCARDED_MARKET}
      incomplete={FOOTBALL_INCOMPLETE}
      lang="es"
    />
  );
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByTestId('dashboard-discarded-summary')).toBeNull();
});

// ─────────────────────────────────────────────────────────────────────
// 4. Recommended > 0 → component returns null (defers to existing block).
// ─────────────────────────────────────────────────────────────────────
test('does NOT render when there are recommended picks', () => {
  const { container } = render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={3}
      discardedMotivation={FOOTBALL_DISCARDED_MOTIVATION}
      discardedMarket={FOOTBALL_DISCARDED_MARKET}
      incomplete={FOOTBALL_INCOMPLETE}
      lang="es"
    />
  );
  expect(container).toBeEmptyDOMElement();
});

// ─────────────────────────────────────────────────────────────────────
// 5. Back-compat: legacy items with only `reason`/`missing` still work.
// ─────────────────────────────────────────────────────────────────────
test('legacy payload with only reason/missing still renders without throwing', () => {
  render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={FOOTBALL_DISCARDED_MOTIVATION}
      discardedMarket={[]}
      incomplete={FOOTBALL_INCOMPLETE}
      lang="es"
    />
  );

  fireEvent.click(screen.getByTestId('dashboard-discarded-summary-toggle'));

  // Motivation row uses the legacy `reason` field.
  const motId = 'dashboard-discarded-summary-row-motivation-0';
  expect(screen.getByTestId(`${motId}-match`)).toHaveTextContent('Alpha vs Beta');
  expect(screen.getByTestId(`${motId}-reason`)).toHaveTextContent(/Equipo sin nada en juego/);
  // No secondary block for legacy item.
  expect(screen.queryByTestId(`${motId}-secondary`)).toBeNull();

  // Incomplete row uses the legacy `missing` field as reason.
  const incId = 'dashboard-discarded-summary-row-incomplete-0';
  expect(screen.getByTestId(`${incId}-match`)).toHaveTextContent('Delta vs Echo');
  expect(screen.getByTestId(`${incId}-reason`)).toHaveTextContent(/odds_missing/);
});

// ─────────────────────────────────────────────────────────────────────
// 6. Zero discarded → nothing to surface, render null.
// ─────────────────────────────────────────────────────────────────────
test('renders nothing when all buckets are empty', () => {
  const { container } = render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={[]}
      discardedMarket={[]}
      incomplete={[]}
      lang="es"
    />
  );
  expect(container).toBeEmptyDOMElement();
});

// ─────────────────────────────────────────────────────────────────────
// 7. English copy is consistent with Spanish requirement.
// ─────────────────────────────────────────────────────────────────────
test('renders English copy when lang="en"', () => {
  render(
    <DashboardDiscardedSummary
      sport="football"
      summary={SUMMARY}
      recommendedCount={0}
      discardedMotivation={FOOTBALL_DISCARDED_MOTIVATION}
      discardedMarket={FOOTBALL_DISCARDED_MARKET}
      incomplete={[]}
      lang="en"
    />
  );
  const banner = screen.getByTestId('dashboard-discarded-summary-banner-text');
  expect(banner.textContent).toMatch(/No recommended picks today/);
  expect(banner.textContent).toMatch(/Review the discards/);
  const counter = screen.getByTestId('dashboard-discarded-summary-counter');
  expect(counter.textContent).toMatch(/2 matches discarded/);
  expect(counter.textContent).toMatch(/view detail/);
});
