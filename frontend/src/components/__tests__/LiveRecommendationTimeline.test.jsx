/**
 * RTL tests for LiveRecommendationTimeline.
 *
 * Uses jest.mock to swap the api / sonner modules so jsdom doesn't need
 * real network access.
 */

import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: {
    get:  (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
  },
}));
jest.mock('sonner', () => ({
  __esModule: true,
  toast: { success: jest.fn(), error: jest.fn() },
}));

import { LiveRecommendationTimeline } from '../LiveRecommendationTimeline';

const EVENT_ENGINE_BTTS_HIT = {
  event_id: 'evt-engine-1',
  source:   'engine',
  status:   'hit',
  minute:   38,
  score:    { home: 1, away: 1, label: '1-1' },
  recommendation: {
    market:           'BTTS YES',
    selection:        'Ambos equipos marcan: Sí',
    normalized_market:'BTTS_YES',
    confidence:       66,
  },
  outcome: {
    result: 'hit',
    settled_minute: 38,
    settled_score: '1-1',
    settlement_reason: 'BTTS YES se cumplió cuando Mexico empató.',
  },
  reason_codes: ['BTTS_LIVE_SUPPORT', 'LIVE_ENGINE_HIT'],
};

const EVENT_MANUAL_HIT = {
  event_id: 'evt-manual-1',
  source:   'manual',
  status:   'hit',
  minute:   34,
  score:    { home: 0, away: 1, label: '0-1' },
  recommendation: {
    market:           'BTTS YES',
    selection:        'Ambos equipos marcan: Sí',
    normalized_market:'BTTS_YES',
    confidence:       66,
  },
  outcome: {
    result: 'hit', settled_minute: 38, settled_score: '1-1',
    settlement_reason: 'BTTS YES cumplido.',
  },
};

const EVENT_OPEN = {
  event_id: 'evt-open-1',
  source: 'engine',
  status: 'open',
  minute: 54,
  recommendation: { market: 'Over 3.5', selection: 'Más de 3.5 goles' },
};

const EVENT_SUPERSEDED = {
  event_id: 'evt-superseded-1',
  source: 'engine',
  status: 'superseded',
  minute: 42,
  recommendation: { market: 'Over 1.5' },
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

describe('LiveRecommendationTimeline', () => {
  test('renders an engine BTTS HIT event with settlement info', async () => {
    mockGet.mockResolvedValueOnce({ data: { ok: true, items: [EVENT_ENGINE_BTTS_HIT] } });
    render(<LiveRecommendationTimeline matchId="m1" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    const card = await screen.findByTestId('live-reco-event-evt-engine-1');
    expect(card).toHaveTextContent(/BTTS YES/);
    expect(card).toHaveTextContent(/CUMPLIDO/i);
    expect(card).toHaveTextContent(/1-1/);
    expect(card).toHaveTextContent(/BTTS YES se cumplió/i);
  });

  test('renders a manual MANUAL+HIT event with both badges', async () => {
    mockGet.mockResolvedValueOnce({ data: { ok: true, items: [EVENT_MANUAL_HIT] } });
    render(<LiveRecommendationTimeline matchId="m2" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    const card = await screen.findByTestId('live-reco-event-evt-manual-1');
    expect(card).toHaveTextContent(/MANUAL/i);
    expect(card).toHaveTextContent(/CUMPLIDO/i);
  });

  test('shows OPEN status correctly', async () => {
    mockGet.mockResolvedValueOnce({ data: { ok: true, items: [EVENT_OPEN] } });
    render(<LiveRecommendationTimeline matchId="m3" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    const card = await screen.findByTestId('live-reco-event-evt-open-1');
    expect(card).toHaveTextContent(/EN OBSERVACIÓN/i);
  });

  test('shows SUPERSEDED status correctly', async () => {
    mockGet.mockResolvedValueOnce({ data: { ok: true, items: [EVENT_SUPERSEDED] } });
    render(<LiveRecommendationTimeline matchId="m4" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    const card = await screen.findByTestId('live-reco-event-evt-superseded-1');
    expect(card).toHaveTextContent(/REEMPLAZADO/i);
  });

  test('shows empty state when there are no events', async () => {
    mockGet.mockResolvedValueOnce({ data: { ok: true, items: [] } });
    render(<LiveRecommendationTimeline matchId="m5" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    expect(await screen.findByTestId('live-reco-empty')).toBeInTheDocument();
  });

  test('refresh button calls the endpoint again', async () => {
    mockGet.mockResolvedValue({ data: { ok: true, items: [EVENT_ENGINE_BTTS_HIT] } });
    render(<LiveRecommendationTimeline matchId="m6" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
    // Wait for the first fetch to complete and the refresh button to be enabled.
    await waitFor(() => {
      const btn = screen.getByTestId('live-reco-refresh');
      expect(btn).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId('live-reco-refresh'));
    await waitFor(() => expect(mockGet.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  test('does NOT hide a HIT event when a later recommendation arrives', async () => {
    mockGet.mockResolvedValueOnce({
      data: { ok: true, items: [EVENT_ENGINE_BTTS_HIT, EVENT_OPEN] },
    });
    render(<LiveRecommendationTimeline matchId="m7" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    expect(await screen.findByTestId('live-reco-event-evt-engine-1')).toBeInTheDocument();
    expect(screen.getByTestId('live-reco-event-evt-open-1')).toBeInTheDocument();
  });

  test('endpoint failure surfaces an error indicator without crashing', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    render(<LiveRecommendationTimeline matchId="m8" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(await screen.findByTestId('live-reco-error')).toBeInTheDocument();
  });

  test('manual entry form posts to the manual endpoint', async () => {
    mockGet.mockResolvedValue({ data: { ok: true, items: [] } });
    mockPost.mockResolvedValueOnce({
      data: { ok: true, event: { event_id: 'new-1' } },
    });
    render(<LiveRecommendationTimeline matchId="m9" matchLabel="X vs Y" league="Friendlies" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    // Open the manual form.
    fireEvent.click(screen.getByTestId('live-reco-toggle-manual'));

    // Fill the minimum required fields (minute + market).
    fireEvent.change(screen.getByTestId('manual-input-minute'),  { target: { value: '34' } });
    fireEvent.change(screen.getByTestId('manual-input-market'),  { target: { value: 'BTTS YES' } });
    fireEvent.change(screen.getByTestId('manual-input-score'),   { target: { value: '0-1' } });

    fireEvent.click(screen.getByTestId('manual-submit'));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [endpoint, payload] = mockPost.mock.calls[0];
    expect(endpoint).toMatch(/recommendation-events\/manual/);
    expect(payload).toMatchObject({
      sport:       'football',
      match_id:    'm9',
      recommendation: expect.objectContaining({
        market: 'BTTS YES',
      }),
    });
  });
});
