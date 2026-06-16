/**
 * F94 — FootballLiveVisibilityStrip tests.
 *
 * Two obligatory tests:
 *   1. Exotic-league live fixture renders with the "Visible / no analizado"
 *      chip and the discard reason badge.
 *   2. When the provider returns 0 live fixtures the strip is hidden
 *      (avoids empty noise on the live page).
 *
 * Two bonus tests for tighter coverage:
 *   3. KPI grid renders with debug counts (provider/visible/analyzable).
 *   4. When all live fixtures are analyzable the strip surfaces a quiet
 *      "all-analyzable" message instead of an empty exotic list.
 */
import { render, screen, waitFor } from '@testing-library/react';

const mockGet = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { get: (...a) => mockGet(...a) },
}));

import { FootballLiveVisibilityStrip } from '../FootballLiveVisibilityStrip';

beforeEach(() => mockGet.mockReset());

// ─────────────────────────────────────────────────────────────────────
// 1. Exotic fixture rendered with reason chip + status copy.
// ─────────────────────────────────────────────────────────────────────
test('renders exotic live fixture with visibility status chip', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [
        {
          fixture_id: 1524549,
          status_short: '1H',
          elapsed: 33,
          league: { id: 256, name: 'USL League Two', country: 'USA' },
          teams: {
            home: { name: 'Christos' },
            away: { name: 'Annapolis Blues' },
          },
          visibility_status: 'VISIBLE',
          analysis_status:   'DISCARDED',
          discard_reason:    'EXOTIC_LEAGUE',
          secondary_reasons: [],
        },
      ],
      live_debug: {
        provider_live_count:          1,
        after_sport_filter_count:     1,
        after_league_filter_count:    1,
        visible_live_count:           1,
        analysis_eligible_live_count: 0,
        hidden_by_priority_filter:    0,
      },
    },
  });

  render(<FootballLiveVisibilityStrip lang="es" />);
  await waitFor(() => {
    expect(screen.getByTestId('live-visibility-row-1524549'))
      .toBeInTheDocument();
  });
  // Reason chip with the exotic label.
  expect(screen.getByTestId('live-visibility-reason-EXOTIC_LEAGUE'))
    .toHaveTextContent(/liga ex[óo]tica/i);
  // Status copy "Visible / no analizado".
  expect(screen.getByTestId('live-visibility-status-1524549'))
    .toHaveTextContent(/visible.*no analizado/i);
  // Team names visible.
  const row = screen.getByTestId('live-visibility-row-1524549');
  expect(row).toHaveTextContent(/Christos/);
  expect(row).toHaveTextContent(/Annapolis Blues/);
});

// ─────────────────────────────────────────────────────────────────────
// 2. Empty provider → strip is hidden.
// ─────────────────────────────────────────────────────────────────────
test('strip hidden when provider returns 0 live fixtures', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true, items: [],
      live_debug: {
        provider_live_count: 0, after_sport_filter_count: 0,
        after_league_filter_count: 0, visible_live_count: 0,
        analysis_eligible_live_count: 0, hidden_by_priority_filter: 0,
      },
    },
  });
  const { container } = render(<FootballLiveVisibilityStrip lang="es" />);
  // Wait for the fetch to resolve AND the component to commit the
  // post-loading state (data set, loading=false).
  await waitFor(() => {
    expect(container.querySelector('[data-testid="football-live-visibility-strip"]'))
      .toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────
// 3. KPI grid surfaces the debug counts.
// ─────────────────────────────────────────────────────────────────────
test('KPI grid surfaces provider / visible / analyzable counts', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [
        {
          fixture_id: 'x',
          status_short: '1H',
          league: { id: 256, name: 'USL', country: 'USA' },
          teams: { home: { name: 'A' }, away: { name: 'B' } },
          visibility_status: 'VISIBLE',
          analysis_status:   'DISCARDED',
          discard_reason:    'EXOTIC_LEAGUE',
          secondary_reasons: [],
        },
      ],
      live_debug: {
        provider_live_count: 5, after_sport_filter_count: 5,
        after_league_filter_count: 5, visible_live_count: 5,
        analysis_eligible_live_count: 1, hidden_by_priority_filter: 0,
      },
    },
  });
  render(<FootballLiveVisibilityStrip lang="es" />);
  await waitFor(() => {
    expect(screen.getByTestId('live-visibility-kpi-provider'))
      .toHaveTextContent('5');
  });
  expect(screen.getByTestId('live-visibility-kpi-visible')).toHaveTextContent('5');
  expect(screen.getByTestId('live-visibility-kpi-analyzable')).toHaveTextContent('1');
  expect(screen.getByTestId('live-visibility-kpi-hidden')).toHaveTextContent('0');
});

// ─────────────────────────────────────────────────────────────────────
// 4. All-analyzable → quiet message instead of an empty exotic list.
// ─────────────────────────────────────────────────────────────────────
test('shows quiet message when every live fixture is already analyzable', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [
        {
          fixture_id: 'wc-1',
          status_short: '1H',
          league: { id: 1, name: 'World Cup', country: 'World' },
          teams: { home: { name: 'Saudi Arabia' }, away: { name: 'Uruguay' } },
          visibility_status: 'VISIBLE',
          analysis_status:   'ANALYZABLE',
          discard_reason:    null,
          secondary_reasons: [],
        },
      ],
      live_debug: {
        provider_live_count: 1, after_sport_filter_count: 1,
        after_league_filter_count: 1, visible_live_count: 1,
        analysis_eligible_live_count: 1, hidden_by_priority_filter: 0,
      },
    },
  });
  render(<FootballLiveVisibilityStrip lang="es" />);
  await waitFor(() => {
    expect(screen.getByTestId('football-live-visibility-all-analyzable'))
      .toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────
// 5. F94.3 — Live enrichment dropped fixtures banner (high severity).
// ─────────────────────────────────────────────────────────────────────
test('renders LIVE_ENRICHMENT_DROPPED_FIXTURES banner when discovery>0 but persisted=0', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [],
      live_debug: {
        provider_live_count:                3,
        after_sport_filter_count:           3,
        after_league_filter_count:          0,
        visible_live_count:                 0,
        analysis_eligible_live_count:       0,
        hidden_by_priority_filter:          0,
        persisted_live_count:               0,
        enrichment_dropped_all_fixtures:    true,
        enrichment_error_code:              'LIVE_ENRICHMENT_DROPPED_FIXTURES',
        enrichment_error_message:           'Discovered 3 live fixture(s) but persisted 0.',
      },
    },
  });
  render(<FootballLiveVisibilityStrip lang="es" />);
  await waitFor(() => {
    expect(screen.getByTestId('live-enrichment-dropped-banner')).toBeInTheDocument();
  });
  expect(screen.getByTestId('live-enrichment-dropped-code'))
    .toHaveTextContent('LIVE_ENRICHMENT_DROPPED_FIXTURES');
  expect(screen.getByTestId('live-enrichment-dropped-message'))
    .toHaveTextContent(/Discovered 3 live fixture/i);
  expect(screen.getByTestId('live-enrichment-dropped-discovery'))
    .toHaveTextContent('3');
  expect(screen.getByTestId('live-enrichment-dropped-persisted'))
    .toHaveTextContent('0');
});

// ─────────────────────────────────────────────────────────────────────
// 6. F94.3 — banner stays HIDDEN when persistence is healthy.
// ─────────────────────────────────────────────────────────────────────
test('LIVE_ENRICHMENT_DROPPED_FIXTURES banner hidden when persistence is healthy', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [
        {
          fixture_id: 'x',
          status_short: '1H',
          league: { id: 39, name: 'Premier League', country: 'England' },
          teams: { home: { name: 'A' }, away: { name: 'B' } },
          visibility_status: 'VISIBLE',
          analysis_status:   'ANALYZABLE',
          discard_reason:    null,
          secondary_reasons: [],
        },
      ],
      live_debug: {
        provider_live_count: 2, after_sport_filter_count: 2,
        after_league_filter_count: 2, visible_live_count: 2,
        analysis_eligible_live_count: 2, hidden_by_priority_filter: 0,
        persisted_live_count: 2,
        enrichment_dropped_all_fixtures: false,
        enrichment_error_code: null,
        enrichment_error_message: null,
      },
    },
  });
  render(<FootballLiveVisibilityStrip lang="es" />);
  // Wait for any fetch-driven UI to settle, then assert banner absent.
  await waitFor(() => {
    expect(screen.getByTestId('football-live-visibility-strip')).toBeInTheDocument();
  });
  expect(screen.queryByTestId('live-enrichment-dropped-banner')).toBeNull();
});

// ─────────────────────────────────────────────────────────────────────
// 7. F94.3 — banner forces the strip to render even with 0 items.
// ─────────────────────────────────────────────────────────────────────
test('strip stays visible to render the enrichment-dropped banner even when items list is empty', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [],
      live_debug: {
        provider_live_count: 4,
        after_sport_filter_count: 4,
        after_league_filter_count: 0,
        visible_live_count: 0,
        analysis_eligible_live_count: 0,
        hidden_by_priority_filter: 0,
        persisted_live_count: 0,
        enrichment_dropped_all_fixtures: true,
        enrichment_error_code: 'LIVE_ENRICHMENT_DROPPED_FIXTURES',
        enrichment_error_message: 'Discovered 4 live fixture(s) but persisted 0.',
      },
    },
  });
  const { container } = render(<FootballLiveVisibilityStrip lang="es" />);
  await waitFor(() => {
    expect(container.querySelector('[data-testid="football-live-visibility-strip"]'))
      .not.toBeNull();
  });
  expect(screen.getByTestId('live-enrichment-dropped-banner')).toBeInTheDocument();
});

// ─────────────────────────────────────────────────────────────────────
// 8. F94.3 — banner copy localised to English when lang="en".
// ─────────────────────────────────────────────────────────────────────
test('LIVE_ENRICHMENT_DROPPED_FIXTURES banner copy localises to English', async () => {
  mockGet.mockResolvedValueOnce({
    data: {
      ok: true,
      items: [],
      live_debug: {
        provider_live_count: 2,
        persisted_live_count: 0,
        enrichment_dropped_all_fixtures: true,
        enrichment_error_code: 'LIVE_ENRICHMENT_DROPPED_FIXTURES',
        enrichment_error_message: 'Discovered 2 live fixture(s) but persisted 0.',
      },
    },
  });
  render(<FootballLiveVisibilityStrip lang="en" />);
  await waitFor(() => {
    expect(screen.getByTestId('live-enrichment-dropped-banner'))
      .toHaveTextContent(/Technical error/i);
  });
});
