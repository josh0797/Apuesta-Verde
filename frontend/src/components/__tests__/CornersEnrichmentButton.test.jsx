/**
 * Phase F82.2 — Frontend tests for CornersEnrichmentButton.
 *
 * The 5 product-mandated tests:
 *   1. renders CornersEnrichmentButton for football match with pending corners
 *   2. does NOT render CornersEnrichmentButton for non-football sports
 *   3. calls /run-now endpoint when clicking the update button
 *   4. shows 365Scores confirmation when cross profile is confirmed
 *   5. does NOT show legacy Scores24 labels anymore
 */

import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

// ── Mock the api module BEFORE importing the component ──────────────
const mockPost = jest.fn();
const mockGet  = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { post: (...a) => mockPost(...a), get: (...a) => mockGet(...a) },
}));

// Mock sonner toasts so we don't need its provider mounted.
jest.mock('sonner', () => ({
  __esModule: true,
  toast: {
    error:   jest.fn(),
    message: jest.fn(),
    success: jest.fn(),
  },
}));

// Now import the component under test.
import { CornersEnrichmentButton } from '../CornersEnrichmentButton';

// ── Shared helpers ──────────────────────────────────────────────────
const baseFootballMatch = {
  match_id: 'm-001',
  sport:    'football',
  // Engine cross profile present so the cross block renders even when
  // the corners snapshot hasn't been fetched yet.
  combined_football_corner_profile_cross: {
    available: true,
    profile:   'STRONG_CORNERS_UNDER_CROSS',
    supports:  'UNDER',
    home: { corners_for_l5: 3.5, corners_for_l15: 3.7 },
    away: { corners_for_l5: 3.2, corners_for_l15: 3.4 },
    external_source:        '365scores',
    external_confirmation:  true,
    external_conflict:      false,
    external_reason_codes:  ['365SCORES_CONFIRMS_UNDER_PROFILE'],
  },
  football_corner_365_cross_applied: {
    engine_version:        'football_corner_365_cross_integration.v1',
    external_source:       '365scores',
    external_confirmation: true,
    external_conflict:     false,
  },
};

beforeEach(() => {
  mockPost.mockReset();
  mockGet.mockReset();
});


// ─── Test 1 — Renders for football match with pending corners ───────
describe('CornersEnrichmentButton — render gating', () => {
  it('renders CornersEnrichmentButton for football match with pending corners', () => {
    render(<CornersEnrichmentButton match={baseFootballMatch} lang="es" />);
    // Root container is keyed by match_id.
    expect(screen.getByTestId('corners-enrichment-m-001')).toBeInTheDocument();
    // The CTA button must be visible.
    expect(screen.getByTestId('corners-enrichment-btn-m-001')).toBeInTheDocument();
    expect(screen.getByText(/Cargar stats de córners/i)).toBeInTheDocument();
  });

  // ─── Test 2 — Hidden for non-football sports ──────────────────────
  it('does not render CornersEnrichmentButton for non-football sports', () => {
    const baseballMatch = { ...baseFootballMatch, sport: 'baseball' };
    const { container } = render(
      <CornersEnrichmentButton match={baseballMatch} lang="es" />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId('corners-enrichment-m-001')).not.toBeInTheDocument();

    const basketballMatch = { ...baseFootballMatch, sport: 'basketball' };
    const out2 = render(
      <CornersEnrichmentButton match={basketballMatch} lang="es" />,
    );
    expect(out2.container.firstChild).toBeNull();
  });

  it('does not render when match_id is missing', () => {
    const noId = { ...baseFootballMatch, match_id: null };
    const { container } = render(
      <CornersEnrichmentButton match={noId} lang="es" />,
    );
    expect(container.firstChild).toBeNull();
  });
});


// ─── Test 3 — Calls /run-now when button is clicked ────────────────
describe('CornersEnrichmentButton — click → /run-now', () => {
  it('calls run-now endpoint when clicking the update button', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: 'SUCCESS',
        available: true,
        source: '365scores',
        current_match: { home: 4, away: 4, total: 8 },
      },
    });

    render(<CornersEnrichmentButton match={baseFootballMatch} lang="es" />);
    const btn = screen.getByTestId('corners-enrichment-btn-m-001');

    await act(async () => {
      fireEvent.click(btn);
    });

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/football/corners-enrichment/run-now');
    expect(body).toEqual({ match_id: 'm-001' });
  });

  it('triggers background fallback on TIMEOUT status', async () => {
    // 1st POST → /run-now returns TIMEOUT.
    mockPost.mockResolvedValueOnce({ data: { status: 'TIMEOUT' } });
    // 2nd POST → /background returns QUEUED.
    mockPost.mockResolvedValueOnce({ data: { status: 'QUEUED', match_id: 'm-001' } });

    render(<CornersEnrichmentButton match={baseFootballMatch} lang="es" />);
    const btn = screen.getByTestId('corners-enrichment-btn-m-001');

    await act(async () => {
      fireEvent.click(btn);
      // Let the chained promises settle.
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockPost).toHaveBeenCalledTimes(2);
    expect(mockPost.mock.calls[0][0]).toBe('/football/corners-enrichment/run-now');
    expect(mockPost.mock.calls[1][0]).toBe('/football/corners-enrichment/background');
  });
});


// ─── Test 4 — Shows 365Scores confirmation in the cross block ───────
describe('CornersEnrichmentButton — cross profile confirmation labels', () => {
  it('shows "365Scores confirma" when cross profile is confirmed', () => {
    render(<CornersEnrichmentButton match={baseFootballMatch} lang="es" />);
    // Confirmation chip visible.
    const chip = screen.getByTestId('corner-cross-external-confirms');
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveTextContent(/365Scores confirma/i);
  });

  it('shows "365Scores confirms" in English', () => {
    render(<CornersEnrichmentButton match={baseFootballMatch} lang="en" />);
    const chip = screen.getByTestId('corner-cross-external-confirms');
    expect(chip).toHaveTextContent(/365Scores confirms/i);
  });

  it('shows "365Scores contradice" when cross profile conflicts', () => {
    const conflictMatch = {
      ...baseFootballMatch,
      combined_football_corner_profile_cross: {
        ...baseFootballMatch.combined_football_corner_profile_cross,
        external_confirmation: false,
        external_conflict:     true,
        external_reason_codes: ['365SCORES_CONFLICTS_UNDER_PROFILE'],
      },
    };
    render(<CornersEnrichmentButton match={conflictMatch} lang="es" />);
    const chip = screen.getByTestId('corner-cross-external-conflicts');
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveTextContent(/365Scores contradice/i);
  });

  it('shows "Confirmación externa no disponible" when no external data', () => {
    const unavailMatch = {
      ...baseFootballMatch,
      combined_football_corner_profile_cross: {
        ...baseFootballMatch.combined_football_corner_profile_cross,
        external_confirmation: false,
        external_conflict:     false,
        external_reason_codes: ['NO_365SCORES_CONFIRMATION_AVAILABLE'],
        external_source:        null,
      },
      football_corner_365_cross_applied: {
        external_source:       null,
        external_confirmation: false,
        external_conflict:     false,
      },
    };
    render(<CornersEnrichmentButton match={unavailMatch} lang="es" />);
    const chip = screen.getByTestId('corner-cross-external-unavailable');
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveTextContent(/Confirmación externa no disponible/i);
  });
});


// ─── Test 5 — Does NOT show legacy Scores24 labels anymore ──────────
describe('CornersEnrichmentButton — Scores24 labels removed', () => {
  it('does not show legacy "Scores24" labels for confirmed match', () => {
    render(<CornersEnrichmentButton match={baseFootballMatch} lang="es" />);
    const root = screen.getByTestId('corners-enrichment-m-001');
    expect(root.textContent).not.toMatch(/Scores24/i);
  });

  it('does not show legacy "Scores24" labels for conflicting match', () => {
    const conflictMatch = {
      ...baseFootballMatch,
      combined_football_corner_profile_cross: {
        ...baseFootballMatch.combined_football_corner_profile_cross,
        external_confirmation: false,
        external_conflict:     true,
      },
    };
    render(<CornersEnrichmentButton match={conflictMatch} lang="en" />);
    const root = screen.getByTestId('corners-enrichment-m-001');
    expect(root.textContent).not.toMatch(/Scores24/i);
  });

  it('does not show legacy "Scores24 gate denied" wording', () => {
    const noExtMatch = {
      ...baseFootballMatch,
      combined_football_corner_profile_cross: {
        ...baseFootballMatch.combined_football_corner_profile_cross,
        external_confirmation: false,
        external_conflict:     false,
        external_reason_codes: ['NO_365SCORES_CONFIRMATION_AVAILABLE'],
      },
      // Use a legacy-shaped audit to ensure even old payloads don't
      // re-introduce the Scores24 wording.
      football_corner_365_cross_applied: null,
      football_corner_cross_applied: { gate_should_fetch: false },
    };
    render(<CornersEnrichmentButton match={noExtMatch} lang="es" />);
    const root = screen.getByTestId('corners-enrichment-m-001');
    expect(root.textContent).not.toMatch(/Scores24/i);
    expect(root.textContent).not.toMatch(/gate denied/i);
  });
});
