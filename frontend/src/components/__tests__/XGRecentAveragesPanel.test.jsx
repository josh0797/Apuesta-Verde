/**
 * Phase F83.2-E5 — Frontend tests for XGRecentAveragesPanel.
 *
 * Coverage:
 *  1. Render gating: football + match_id required.
 *  2. Initial state: renders CTA when no snapshot is attached.
 *  3. Click → POST /football/xg-recent-averages/run-now with the
 *     correct payload.
 *  4. SUCCESS shape with full data renders home & away L1/L5/L15 values.
 *  5. Partial snapshot shows the "Muestra parcial" badge and STILL
 *     renders the available windows (no hiding).
 *  6. L1-only snapshot shows the dedicated L1_ONLY badge.
 *  7. Signal codes attached to the snapshot render as a list with
 *     explanations.
 *  8. Error path: failed POST surfaces the error message.
 */

import { render, screen, fireEvent, act } from '@testing-library/react';

// ── Mock the api module BEFORE importing the component ──────────────
const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { post: (...a) => mockPost(...a) },
}));

import { XGRecentAveragesPanel } from '../XGRecentAveragesPanel';

const baseFootballMatch = {
  match_id: 'mt_001',
  sport:    'football',
};

const fullSnapshot = {
  available: true,
  partial:   false,
  source:    'thestatsapi_shotmap',
  home: {
    team: 'Home FC',
    l1:   { xg_for_avg: 1.20, xg_against_avg: 0.80, sample_size: 1 },
    l5:   { xg_for_avg: 1.40, xg_against_avg: 0.95, sample_size: 5 },
    l15:  { xg_for_avg: 1.30, xg_against_avg: 1.10, sample_size: 15 },
  },
  away: {
    team: 'Away FC',
    l1:   { xg_for_avg: 0.80, xg_against_avg: 1.20, sample_size: 1 },
    l5:   { xg_for_avg: 1.00, xg_against_avg: 1.30, sample_size: 5 },
    l15:  { xg_for_avg: 1.10, xg_against_avg: 1.20, sample_size: 15 },
  },
};

beforeEach(() => {
  mockPost.mockReset();
});


// ─── Render gating ──────────────────────────────────────────────────
describe('XGRecentAveragesPanel — render gating', () => {
  it('does not render for non-football sports', () => {
    const { container } = render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, sport: 'basketball' }}
        lang="es"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('does not render without a match_id', () => {
    const { container } = render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, match_id: null }}
        lang="es"
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders root + CTA button for a valid football match', () => {
    render(<XGRecentAveragesPanel match={baseFootballMatch} lang="es" />);
    expect(screen.getByTestId('xg-recent-mt_001')).toBeInTheDocument();
    expect(screen.getByTestId('xg-recent-btn-mt_001')).toBeInTheDocument();
  });
});


// ─── CTA click → /run-now ──────────────────────────────────────────
describe('XGRecentAveragesPanel — fetch trigger', () => {
  it('POSTs /football/xg-recent-averages/run-now with the match_id', async () => {
    mockPost.mockResolvedValueOnce({ data: { status: 'SUCCESS', ...fullSnapshot } });

    render(<XGRecentAveragesPanel match={baseFootballMatch} lang="es" />);
    const btn = screen.getByTestId('xg-recent-btn-mt_001');

    await act(async () => {
      fireEvent.click(btn);
    });

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/football/xg-recent-averages/run-now');
    expect(body).toEqual({ match_id: 'mt_001' });
  });

  it('coerces numeric match_id to string when calling the API', async () => {
    mockPost.mockResolvedValueOnce({ data: { status: 'SUCCESS', ...fullSnapshot } });
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, match_id: 12345 }}
        lang="es"
      />,
    );
    const btn = screen.getByTestId('xg-recent-btn-12345');
    await act(async () => { fireEvent.click(btn); });
    const [, body] = mockPost.mock.calls[0];
    expect(body).toEqual({ match_id: '12345' });
  });

  it('surfaces the error message on a failed POST', async () => {
    mockPost.mockRejectedValueOnce({
      response: { data: { detail: 'shotmap unreachable' } },
    });

    render(<XGRecentAveragesPanel match={baseFootballMatch} lang="es" />);
    const btn = screen.getByTestId('xg-recent-btn-mt_001');
    await act(async () => { fireEvent.click(btn); });

    const err = await screen.findByTestId('xg-recent-error-mt_001');
    expect(err).toBeInTheDocument();
    expect(err.textContent).toMatch(/shotmap unreachable/i);
  });
});


// ─── Full snapshot rendering ────────────────────────────────────────
describe('XGRecentAveragesPanel — full snapshot', () => {
  it('renders home & away L1/L5/L15 values when xg_recent_averages is attached', () => {
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, xg_recent_averages: fullSnapshot }}
        lang="es"
      />,
    );
    // Both side blocks render.
    expect(screen.getByTestId('xg-side-H')).toBeInTheDocument();
    expect(screen.getByTestId('xg-side-A')).toBeInTheDocument();
    // Values formatted to 2 decimals appear somewhere on screen.
    const content = screen.getByTestId('xg-recent-content');
    expect(content.textContent).toMatch(/1\.20/);
    expect(content.textContent).toMatch(/1\.40/);
    expect(content.textContent).toMatch(/1\.30/);
    expect(content.textContent).toMatch(/Home FC/);
    expect(content.textContent).toMatch(/Away FC/);
  });

  it('does NOT show the partial badge when snapshot is full', () => {
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, xg_recent_averages: fullSnapshot }}
        lang="es"
      />,
    );
    expect(screen.queryByTestId('xg-recent-partial-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('xg-recent-l1-only-badge')).not.toBeInTheDocument();
  });

  it('reads snapshot from football_data_enrichment.xg_recent_averages too', () => {
    render(
      <XGRecentAveragesPanel
        match={{
          ...baseFootballMatch,
          football_data_enrichment: { xg_recent_averages: fullSnapshot },
        }}
        lang="es"
      />,
    );
    expect(screen.getByTestId('xg-recent-content')).toBeInTheDocument();
  });
});


// ─── Partial snapshot rendering ─────────────────────────────────────
describe('XGRecentAveragesPanel — partial snapshot', () => {
  it('renders the partial badge and still shows available windows', () => {
    const partialSnapshot = {
      available: true,
      partial:   true,
      source:    'thestatsapi_shotmap',
      home: {
        team: 'Home FC',
        l1:   { xg_for_avg: 1.20, xg_against_avg: 0.80, sample_size: 1 },
        l5:   { xg_for_avg: 1.40, xg_against_avg: 0.95, sample_size: 5 },
        l15:  null,   // ← missing
      },
      away: {
        team: 'Away FC',
        l1:   { xg_for_avg: 0.80, xg_against_avg: 1.20, sample_size: 1 },
        l5:   { xg_for_avg: 1.00, xg_against_avg: 1.30, sample_size: 5 },
        l15:  null,   // ← missing
      },
      signals: ['XG_PARTIAL_SAMPLE', 'XG_L5_AVAILABLE_L15_MISSING'],
      explanations: {
        XG_PARTIAL_SAMPLE: 'Muestra parcial — falta L15.',
        XG_L5_AVAILABLE_L15_MISSING: 'L5 OK, L15 ausente.',
      },
    };
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, xg_recent_averages: partialSnapshot }}
        lang="es"
      />,
    );
    // Partial badge present.
    expect(screen.getByTestId('xg-recent-partial-badge')).toBeInTheDocument();
    // The available L5 numbers are STILL rendered.
    const content = screen.getByTestId('xg-recent-content');
    expect(content.textContent).toMatch(/1\.40/);
    expect(content.textContent).toMatch(/1\.00/);
    // L15 row renders the "no disponible" placeholder, never hidden.
    expect(content.textContent).toMatch(/no disponible/i);
    // Signals chips emitted.
    const sigs = screen.getByTestId('xg-recent-signals');
    expect(sigs.textContent).toMatch(/XG_PARTIAL_SAMPLE/);
    expect(sigs.textContent).toMatch(/XG_L5_AVAILABLE_L15_MISSING/);
  });

  it('renders the dedicated L1_ONLY badge when only L1 is available both sides', () => {
    const l1OnlySnapshot = {
      available: true,
      partial:   true,
      source:    'thestatsapi_shotmap',
      home: {
        team: 'Home FC',
        l1:   { xg_for_avg: 1.20, xg_against_avg: 0.80, sample_size: 1 },
        l5:   null, l15: null,
      },
      away: {
        team: 'Away FC',
        l1:   { xg_for_avg: 0.80, xg_against_avg: 1.20, sample_size: 1 },
        l5:   null, l15: null,
      },
      signals: ['XG_PARTIAL_SAMPLE', 'XG_L1_ONLY', 'XG_RECENT_SAMPLE_INSUFFICIENT'],
      explanations: {
        XG_L1_ONLY: 'Solo L1 disponible.',
      },
    };
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, xg_recent_averages: l1OnlySnapshot }}
        lang="es"
      />,
    );
    expect(screen.getByTestId('xg-recent-l1-only-badge')).toBeInTheDocument();
    // Both side L1 numbers visible.
    const content = screen.getByTestId('xg-recent-content');
    expect(content.textContent).toMatch(/1\.20/);
    expect(content.textContent).toMatch(/0\.80/);
    // L5 / L15 rows show "no disponible".
    expect(content.textContent).toMatch(/no disponible/i);
  });
});


// ─── i18n smoke test ────────────────────────────────────────────────
describe('XGRecentAveragesPanel — i18n', () => {
  it('renders English title and badges when lang=en', () => {
    const partial = {
      available: true,
      partial:   true,
      source:    'thestatsapi_shotmap',
      home: { team: 'H', l1: { xg_for_avg: 1.0, xg_against_avg: 0.5, sample_size: 1 },
              l5: null, l15: null },
      away: { team: 'A', l1: { xg_for_avg: 1.0, xg_against_avg: 0.5, sample_size: 1 },
              l5: null, l15: null },
      signals: ['XG_L1_ONLY'],
    };
    render(
      <XGRecentAveragesPanel
        match={{ ...baseFootballMatch, xg_recent_averages: partial }}
        lang="en"
      />,
    );
    expect(screen.getByText(/TheStatsAPI \/ xG recent/i)).toBeInTheDocument();
    expect(screen.getByTestId('xg-recent-l1-only-badge').textContent)
      .toMatch(/Only L1 available/i);
  });
});
