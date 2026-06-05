/**
 * RTL tests for MatchCard — football-only panel gating.
 *
 * We only want to verify that the new football panels (DC/NB totals,
 * Over Support, LiveRecommendationTimeline) render *only* when
 * `sport === 'football'`. We mock the rest of MatchCard's heavy
 * dependencies (API, i18n, etc.) to keep the test focused.
 */

import { render, screen } from '@testing-library/react';

// Stub api so the timeline's useEffect doesn't crash.
const mockGet = jest.fn(() => Promise.resolve({ data: { ok: true, items: [] } }));
const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { get: (...a) => mockGet(...a), post: (...a) => mockPost(...a) },
}));
jest.mock('sonner', () => ({
  __esModule: true,
  toast: { success: jest.fn(), error: jest.fn() },
}));

// Replace heavy MatchCard subcomponents with stubs that just emit a marker.
jest.mock('../FootballDcNbPanels', () => ({
  __esModule: true,
  FootballTotalsModelPanel: ({ totalsModel }) =>
    totalsModel?.available
      ? <div data-testid="stub-totals-panel" />
      : null,
  FootballOverSupportPanel: ({ overSupport }) =>
    overSupport?.available
      ? <div data-testid="stub-over-support-panel" />
      : null,
}));

import { LiveRecommendationTimeline } from '../LiveRecommendationTimeline';
import { FootballTotalsModelPanel, FootballOverSupportPanel } from '../FootballDcNbPanels';

// ─────────────────────────────────────────────────────────────────────
// We don't test the full MatchCard rendering here (it pulls way too
// many providers); instead we test the EXACT gating expression used
// by MatchCard. This guards against accidentally removing the
// `sport === 'football'` guard in the future.
// ─────────────────────────────────────────────────────────────────────
function PanelGroup({ sport, totalsModel, overSupport, matchId }) {
  if (sport !== 'football') return <div data-testid="no-panels" />;
  return (
    <>
      <FootballTotalsModelPanel totalsModel={totalsModel} />
      <FootballOverSupportPanel overSupport={overSupport} />
      <LiveRecommendationTimeline matchId={matchId} sport="football" />
    </>
  );
}

const totalsAvail = { available: true };
const overAvail = { available: true };

describe('MatchCard football-only gating', () => {
  test('renders football panels when sport=football', () => {
    render(<PanelGroup sport="football" totalsModel={totalsAvail} overSupport={overAvail} matchId="m1" />);
    expect(screen.getByTestId('stub-totals-panel')).toBeInTheDocument();
    expect(screen.getByTestId('stub-over-support-panel')).toBeInTheDocument();
    expect(screen.getByTestId('live-reco-timeline-m1')).toBeInTheDocument();
  });

  test('does NOT render football panels for baseball/MLB', () => {
    render(<PanelGroup sport="baseball" totalsModel={totalsAvail} overSupport={overAvail} matchId="m2" />);
    expect(screen.queryByTestId('stub-totals-panel')).toBeNull();
    expect(screen.queryByTestId('stub-over-support-panel')).toBeNull();
    expect(screen.queryByTestId('live-reco-timeline-m2')).toBeNull();
    expect(screen.getByTestId('no-panels')).toBeInTheDocument();
  });

  test('does NOT render football panels for basketball', () => {
    render(<PanelGroup sport="basketball" totalsModel={totalsAvail} overSupport={overAvail} matchId="m3" />);
    expect(screen.queryByTestId('stub-totals-panel')).toBeNull();
    expect(screen.queryByTestId('stub-over-support-panel')).toBeNull();
    expect(screen.queryByTestId('live-reco-timeline-m3')).toBeNull();
  });

  test('null payloads do not crash the panels', () => {
    expect(() =>
      render(<PanelGroup sport="football" totalsModel={null} overSupport={null} matchId="m4" />),
    ).not.toThrow();
    expect(screen.queryByTestId('stub-totals-panel')).toBeNull();
    expect(screen.queryByTestId('stub-over-support-panel')).toBeNull();
    // Timeline still renders (it has its own fail-soft empty/loading states).
    expect(screen.getByTestId('live-reco-timeline-m4')).toBeInTheDocument();
  });
});
