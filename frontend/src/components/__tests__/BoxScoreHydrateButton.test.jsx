/**
 * RTL tests for BoxScoreHydrateButton (Phase 41 P2 follow-up).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockApiPost = jest.fn();
const apiClient = { post: (...args) => mockApiPost(...args) };

jest.mock('sonner', () => ({
  __esModule: true,
  toast: { success: jest.fn(), error: jest.fn() },
}));

import { BoxScoreHydrateButton } from '../BoxScoreHydrateButton';

beforeEach(() => mockApiPost.mockReset());

const basketballMatch = { match_id: 'nba-1', sport: 'basketball' };
const baseballMatch   = { match_id: 'mlb-1', sport: 'baseball' };
const footballMatch   = { match_id: 'epl-1', sport: 'football' };


describe('BoxScoreHydrateButton — gating', () => {
  test('renders for basketball', () => {
    render(<BoxScoreHydrateButton match={basketballMatch} apiClient={apiClient} />);
    expect(screen.getByTestId('box-score-hydrate-btn-nba-1')).toBeInTheDocument();
  });

  test('renders for baseball', () => {
    render(<BoxScoreHydrateButton match={baseballMatch} apiClient={apiClient} />);
    expect(screen.getByTestId('box-score-hydrate-btn-mlb-1')).toBeInTheDocument();
  });

  test('hidden for football', () => {
    const { container } = render(<BoxScoreHydrateButton match={footballMatch} apiClient={apiClient} />);
    expect(container.firstChild).toBeNull();
  });

  test('hidden when match has no id', () => {
    const { container } = render(<BoxScoreHydrateButton match={{ sport: 'basketball' }} apiClient={apiClient} />);
    expect(container.firstChild).toBeNull();
  });
});


describe('BoxScoreHydrateButton — fetch flow', () => {
  test('clicking hits POST /analysis/box-scores/hydrate with correct payload', async () => {
    mockApiPost.mockResolvedValueOnce({
      data: {
        ok: true,
        home_games: 8,
        away_games: 7,
        provider_summary: { home: 'api_sports', away: 'api_sports' },
      },
    });
    render(<BoxScoreHydrateButton match={basketballMatch} apiClient={apiClient} lang="es" />);
    fireEvent.click(screen.getByTestId('box-score-hydrate-btn-nba-1'));
    await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    const [path, body, opts] = mockApiPost.mock.calls[0];
    expect(path).toBe('/analysis/box-scores/hydrate');
    expect(body).toEqual({ match_id: 'nba-1', sport: 'basketball', last_n: 8 });
    expect(opts.timeout).toBe(45000);
    // Inline summary chips appear.
    await waitFor(() => expect(screen.getByTestId('box-score-hydrate-summary-nba-1')).toBeInTheDocument());
    expect(screen.getByTestId('box-score-hydrate-summary-nba-1').textContent).toMatch(/api_sports/);
  });

  test('shows toast.error when backend returns ok=false', async () => {
    const { toast } = require('sonner');
    mockApiPost.mockResolvedValueOnce({
      data: { ok: false, reason: 'rate_limit' },
    });
    render(<BoxScoreHydrateButton match={baseballMatch} apiClient={apiClient} />);
    fireEvent.click(screen.getByTestId('box-score-hydrate-btn-mlb-1'));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });

  test('shows toast.error when fetch throws', async () => {
    const { toast } = require('sonner');
    mockApiPost.mockRejectedValueOnce({ response: { data: { detail: 'boom' } } });
    render(<BoxScoreHydrateButton match={baseballMatch} apiClient={apiClient} />);
    fireEvent.click(screen.getByTestId('box-score-hydrate-btn-mlb-1'));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });
});
