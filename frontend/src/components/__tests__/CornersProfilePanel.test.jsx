/**
 * FIX-4 — CornersProfilePanel FE tests.
 *
 * Verifies:
 *   - OK profile renders L1/L5/L15 per team + momentum + expected.
 *   - PARTIAL profile renders submarket-locked notice.
 *   - PENDING (background still computing) renders loading skeleton.
 *   - UNAVAILABLE renders explainer + retry + debug.
 *   - Pre-match banner appears when current_fixture_corners_available=false.
 *   - English localisation works.
 */
import { render, screen } from '@testing-library/react';

jest.mock('@/lib/api', () => ({
  api: { get: jest.fn() },
}));
import { api } from '@/lib/api';

import { CornersProfilePanel } from '../CornersProfilePanel';

const OK_PROFILE = {
  status:   'OK',
  provider: 'thestatsapi',
  is_pre_match: true,
  current_fixture_corners_available: false,
  home: {
    team: 'Argentina',
    sample_size: 15,
    last_match_corners: 8,
    l1_corners_for: 8,
    l1_corners_against: 4,
    l5_avg_corners_for: 6.2,
    l5_avg_corners_against: 4.5,
    l15_avg_corners_for: 5.4,
    l15_avg_corners_against: 4.6,
    momentum: { state: 'BULLISH_STRONG', label_es: 'Alcista fuerte',
                label_en: 'Strongly bullish', trend_delta: 0.8, trend_pct: 14.8 },
    reason_codes: [],
  },
  away: {
    team: 'Argelia',
    sample_size: 15,
    last_match_corners: 6,
    l1_corners_for: 6,
    l1_corners_against: 5,
    l5_avg_corners_for: 5.8,
    l5_avg_corners_against: 5.0,
    l15_avg_corners_for: 5.1,
    l15_avg_corners_against: 4.85,
    momentum: { state: 'BULLISH_STABLE', label_es: 'Alcista estable',
                label_en: 'Steadily bullish', trend_delta: 0.7, trend_pct: 13.7 },
    reason_codes: [],
  },
  combined_l5_avg:  12.0,
  combined_l15_avg: 10.5,
  expected_corners: 10.85,
  line_projections: {
    over_8_5:  'FAVORABLE',
    over_9_5:  'FAVORABLE',
    over_10_5: 'NEUTRAL',
    over_11_5: 'RISKY',
  },
  picks_blocked: false,
  reason_codes:  ['CORNERS_PROFILE_OK', 'CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED'],
};


test('FIX-4 — OK profile renders L1/L5/L15 per team + expected corners', () => {
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={OK_PROFILE} />);
  expect(screen.getByTestId('corners-profile-panel')).toBeInTheDocument();
  expect(screen.getByTestId('corners-profile-home-name')).toHaveTextContent('Argentina');
  expect(screen.getByTestId('corners-profile-home-l1')).toHaveTextContent('8');
  expect(screen.getByTestId('corners-profile-home-l5')).toHaveTextContent('6.20');
  expect(screen.getByTestId('corners-profile-home-l15')).toHaveTextContent('5.40');
  expect(screen.getByTestId('corners-profile-away-name')).toHaveTextContent('Argelia');
  expect(screen.getByTestId('corners-profile-away-l1')).toHaveTextContent('6');
  expect(screen.getByTestId('corners-profile-expected')).toHaveTextContent('10.85');
});


test('FIX-4 — momentum badge surfaces state in ES', () => {
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={OK_PROFILE} />);
  expect(screen.getByTestId('corners-profile-home-momentum'))
    .toHaveTextContent(/Alcista fuerte/i);
  expect(screen.getByTestId('corners-profile-home-momentum'))
    .toHaveTextContent('+14.8%');
});


test('FIX-4 — pre-match banner appears when current fixture corners unavailable', () => {
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={OK_PROFILE} />);
  expect(screen.getByTestId('corners-profile-prematch-banner'))
    .toHaveTextContent(/Análisis pre-match/i);
});


test('FIX-4 — line projections render with correct labels', () => {
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={OK_PROFILE} />);
  expect(screen.getByTestId('corners-profile-projection-over_8_5'))
    .toHaveTextContent(/Favorable/i);
  expect(screen.getByTestId('corners-profile-projection-over_11_5'))
    .toHaveTextContent(/Riesgoso/i);
});


test('FIX-4 — PARTIAL profile shows submarket locked notice', () => {
  const partial = {
    ...OK_PROFILE,
    status: 'PARTIAL',
    picks_blocked: true,
    away: { ...OK_PROFILE.away, sample_size: 2 },
    reason_codes: ['CORNERS_HISTORY_INSUFFICIENT_SAMPLE',
                   'CURRENT_FIXTURE_CORNERS_UNAVAILABLE_PREMATCH_EXPECTED'],
  };
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={partial} />);
  expect(screen.getByTestId('corners-profile-submarket-locked'))
    .toHaveTextContent(/Sub-mercado de córners bloqueado/i);
});


test('FIX-4 — English localisation works', () => {
  render(<CornersProfilePanel matchId="m1" lang="en" initialProfile={OK_PROFILE} />);
  expect(screen.getByText(/Corners Profile/i)).toBeInTheDocument();
  expect(screen.getByTestId('corners-profile-prematch-banner'))
    .toHaveTextContent(/Pre-match analysis/i);
});


test('FIX-4 — PENDING (background still computing) shows loading skeleton', async () => {
  api.get.mockResolvedValueOnce({ data: { ok: true, status: 'PENDING', profile: null } });
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={null} />);
  // Initial loading state.
  expect(screen.getByTestId('corners-profile-panel-loading')).toBeInTheDocument();
});


test('FIX-4 — NOT_FOUND renders unavailable explainer + retry button', async () => {
  api.get.mockResolvedValueOnce({ data: { ok: false, status: 'NOT_FOUND', profile: null,
                                          message_user: 'Partido no encontrado.' } });
  render(<CornersProfilePanel matchId="m1" lang="es" initialProfile={null} />);
  // Wait for fetch.
  await screen.findByTestId('corners-profile-panel-unavailable');
  expect(screen.getByTestId('corners-profile-panel-retry')).toBeInTheDocument();
});
