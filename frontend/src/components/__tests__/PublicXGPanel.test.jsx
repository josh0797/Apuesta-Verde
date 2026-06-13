/**
 * Phase F85.5 — Frontend tests for PublicXGPanel.
 *
 * Coverage:
 *  1. Gating: football + match_id required.
 *  2. Click → POST /football/public-xg-enrichment/run-now with payload.
 *  3. Full snapshot renders FBref L1/L5/L15 + Forebet block.
 *  4. Partial snapshot shows badge + still renders available windows.
 *  5. FBref-only snapshot (Forebet down) shows xG and a Forebet-down note.
 *  6. Forebet-only snapshot (FBref down) shows the empty-xg copy and the
 *     Forebet block.
 *  7. Timeout response surfaces the message.
 *  8. forebetUrl prop is forwarded to the API call.
 */

import { render, screen, fireEvent, act } from '@testing-library/react';

const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { post: (...a) => mockPost(...a) },
}));

import { PublicXGPanel } from '../PublicXGPanel';

const baseMatch = { sport: 'football', match_id: 'mt_001' };

const fullSnapshot = {
  available: true,
  source_priority: ['thestatsapi', 'fbref', 'forebet'],
  xg_recent_averages: {
    available: true,
    partial: false,
    source: 'fbref',
    home: {
      team: 'USA',
      l1:  { xg_for_avg: 1.72, xg_against_avg: 0.91, sample_size: 1  },
      l5:  { xg_for_avg: 1.54, xg_against_avg: 1.02, sample_size: 5  },
      l15: { xg_for_avg: 1.39, xg_against_avg: 1.11, sample_size: 15 },
    },
    away: {
      team: 'Paraguay',
      l1:  { xg_for_avg: 0.88, xg_against_avg: 1.42, sample_size: 1  },
      l5:  { xg_for_avg: 1.05, xg_against_avg: 1.31, sample_size: 5  },
      l15: { xg_for_avg: 1.14, xg_against_avg: 1.22, sample_size: 15 },
    },
  },
  forebet_context: {
    available: true,
    predicted_score: '2-1',
    probabilities: { home: 45, draw: 30, away: 25 },
    goals_context: { avg_goals_hint: 2.4 },
  },
  signals: ['XG_SUPPORTS_OVER', 'FOREBET_CONFIRMS_XG'],
  explanations: {
    XG_SUPPORTS_OVER: 'xG apoya Over.',
  },
};

beforeEach(() => {
  mockPost.mockReset();
});


describe('PublicXGPanel — gating', () => {
  it('does not render for non-football matches', () => {
    const { container } = render(
      <PublicXGPanel match={{ sport: 'basketball', match_id: 'm1' }} lang="es" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('does not render when match_id is missing', () => {
    const { container } = render(
      <PublicXGPanel match={{ sport: 'football' }} lang="es" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders root + CTA for valid football match', () => {
    render(<PublicXGPanel match={baseMatch} lang="es" />);
    expect(screen.getByTestId('public-xg-mt_001')).toBeInTheDocument();
    expect(screen.getByTestId('public-xg-btn-mt_001')).toBeInTheDocument();
  });
});


describe('PublicXGPanel — fetch trigger', () => {
  it('POSTs /football/public-xg-enrichment/run-now with match_id', async () => {
    mockPost.mockResolvedValueOnce({ data: { status: 'SUCCESS', ...fullSnapshot } });
    render(<PublicXGPanel match={baseMatch} lang="es" />);
    const btn = screen.getByTestId('public-xg-btn-mt_001');
    await act(async () => { fireEvent.click(btn); });
    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/football/public-xg-enrichment/run-now');
    expect(body.match_id).toBe('mt_001');
    expect(body.force).toBe(true);
  });

  it('forwards forebetUrl prop into the API payload', async () => {
    mockPost.mockResolvedValueOnce({ data: { status: 'SUCCESS', ...fullSnapshot } });
    render(
      <PublicXGPanel
        match={baseMatch}
        lang="es"
        forebetUrl="https://www.forebet.com/es/football/matches/usa-paraguay-1"
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('public-xg-btn-mt_001'));
    });
    const [, body] = mockPost.mock.calls[0];
    expect(body.forebet_url).toBe(
      'https://www.forebet.com/es/football/matches/usa-paraguay-1',
    );
  });

  it('surfaces error message on failed POST', async () => {
    mockPost.mockRejectedValueOnce({
      response: { data: { detail: 'fbref unreachable' } },
    });
    render(<PublicXGPanel match={baseMatch} lang="es" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('public-xg-btn-mt_001'));
    });
    const err = await screen.findByTestId('public-xg-error-mt_001');
    expect(err.textContent).toMatch(/fbref unreachable/i);
  });

  it('surfaces TIMEOUT message returned by the backend', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: 'TIMEOUT',
        message: 'El enriquecimiento externo tardó demasiado. El análisis principal no fue afectado.',
        reason_codes: ['PUBLIC_XG_SCRAPER_TIMEOUT'],
      },
    });
    render(<PublicXGPanel match={baseMatch} lang="es" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('public-xg-btn-mt_001'));
    });
    const err = await screen.findByTestId('public-xg-error-mt_001');
    expect(err.textContent).toMatch(/no fue afectado/i);
  });
});


describe('PublicXGPanel — render full snapshot', () => {
  it('renders FBref L1/L5/L15 + Forebet block', () => {
    render(
      <PublicXGPanel
        match={{ ...baseMatch, xg_public_enrichment: fullSnapshot }}
        lang="es"
      />,
    );
    // Both side blocks render.
    expect(screen.getByTestId('public-xg-side-H')).toBeInTheDocument();
    expect(screen.getByTestId('public-xg-side-A')).toBeInTheDocument();
    // Values to 2 decimals are present.
    expect(screen.getByTestId('public-xg-H-l5-for').textContent).toMatch(/1\.54/);
    expect(screen.getByTestId('public-xg-A-l15-against').textContent).toMatch(/1\.22/);
    // Forebet block rendered.
    expect(screen.getByTestId('public-xg-forebet')).toBeInTheDocument();
    expect(screen.getByTestId('public-xg-forebet-score').textContent).toBe('2-1');
    // Partial badge absent.
    expect(screen.queryByTestId('public-xg-partial-badge')).not.toBeInTheDocument();
    // Signals list rendered.
    expect(screen.getByTestId('public-xg-signals').textContent)
      .toMatch(/XG_SUPPORTS_OVER/);
  });

  it('renders partial badge when snapshot says partial=true', () => {
    const partial = {
      ...fullSnapshot,
      xg_recent_averages: {
        ...fullSnapshot.xg_recent_averages,
        partial: true,
        home: {
          ...fullSnapshot.xg_recent_averages.home,
          l15: null,
        },
        away: {
          ...fullSnapshot.xg_recent_averages.away,
          l15: null,
        },
      },
    };
    render(<PublicXGPanel match={{ ...baseMatch, xg_public_enrichment: partial }} lang="es" />);
    expect(screen.getByTestId('public-xg-partial-badge')).toBeInTheDocument();
    // L5 values STILL render (partial does not hide).
    expect(screen.getByTestId('public-xg-H-l5-for').textContent).toMatch(/1\.54/);
    // L15 marked "no disponible".
    const content = screen.getByTestId('public-xg-content');
    expect(content.textContent).toMatch(/no disponible/i);
  });
});


describe('PublicXGPanel — partial sources', () => {
  it('does not hide FBref xG when Forebet is unavailable', () => {
    const snap = {
      ...fullSnapshot,
      forebet_context: { available: false, reason_codes: ['FOREBET_UNAVAILABLE'] },
    };
    render(<PublicXGPanel match={{ ...baseMatch, xg_public_enrichment: snap }} lang="es" />);
    // FBref block STILL renders.
    expect(screen.getByTestId('public-xg-side-H')).toBeInTheDocument();
    // Forebet-down note appears.
    expect(screen.getByTestId('public-xg-forebet-down')).toBeInTheDocument();
    // Forebet block itself is gone.
    expect(screen.queryByTestId('public-xg-forebet')).not.toBeInTheDocument();
  });

  it('shows empty-xg copy when FBref returns nothing but keeps Forebet', () => {
    const snap = {
      available: true,
      xg_recent_averages: {
        available: false,
        reason_codes: ['FBREF_XG_NOT_AVAILABLE'],
      },
      forebet_context: fullSnapshot.forebet_context,
      signals: [],
    };
    render(<PublicXGPanel match={{ ...baseMatch, xg_public_enrichment: snap }} lang="es" />);
    expect(screen.getByTestId('public-xg-empty').textContent)
      .toMatch(/no entregó xG utilizable/i);
    // Reason code printed.
    expect(screen.getByTestId('public-xg-empty').textContent)
      .toMatch(/FBREF_XG_NOT_AVAILABLE/);
    // Forebet block still rendered.
    expect(screen.getByTestId('public-xg-forebet')).toBeInTheDocument();
  });

  it('reads snapshot from football_data_enrichment.xg_public_enrichment too', () => {
    render(
      <PublicXGPanel
        match={{
          ...baseMatch,
          football_data_enrichment: { xg_public_enrichment: fullSnapshot },
        }}
        lang="es"
      />,
    );
    expect(screen.getByTestId('public-xg-content')).toBeInTheDocument();
  });
});


describe('PublicXGPanel — i18n smoke', () => {
  it('renders English title and CTA when lang=en', () => {
    render(<PublicXGPanel match={baseMatch} lang="en" />);
    expect(screen.getByText(/Public xG/i)).toBeInTheDocument();
    expect(screen.getByTestId('public-xg-btn-mt_001').textContent)
      .toMatch(/Refresh xG with FBref/i);
  });
});
