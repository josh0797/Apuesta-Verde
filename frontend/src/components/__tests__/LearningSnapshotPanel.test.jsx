/**
 * Sprint-B · B4 — Tests for LearningSnapshotPanel.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { LearningSnapshotPanel } from '../LearningSnapshotPanel';

function makeSnapshot(overrides = {}) {
  return {
    match_id: 42,
    home_team: 'France',
    away_team: 'Senegal',
    competition: 'FIFA World Cup 2026 - Group I',
    snapshot_type: 'PRE_MATCH',
    pre_match_inputs: {
      home_xg_l5:        2.1,
      away_xg_l5:        1.4,
      home_xg_l15:       1.95,
      away_xg_l15:       1.20,
      home_corners_l5:   6.2,
      away_corners_l5:   5.1,
      home_corners_l15:  5.9,
      away_corners_l15:  4.7,
      btts_probability:  0.62,
      over25_probability: 0.64,
      draw_probability:  0.23,
      expected_corners:  11.3,
      market_odds: {
        over25: 1.85, btts_yes: 1.90, draw: 3.40, over85_corners: 1.90,
      },
    },
    post_match_outputs: {
      final_score: '3-1',
      home_goals: 3,
      away_goals: 1,
      total_goals: 4,
      btts_hit: true,
      over25_hit: true,
      draw_hit: false,
      total_corners: 11,
      over85_corners_hit: true,
      real_home_xg: 2.8,
      real_away_xg: 1.2,
    },
    source_audit: {
      pre_match_sources: [
        { source: 'thestatsapi', status: 'COMPLETE',
          fields_filled: ['home_xg_l5', 'away_xg_l5'] },
        { source: 'api_sports',  status: 'PARTIAL',
          fields_filled: ['home_corners_l5'] },
      ],
      post_match_sources: [
        { source: 'thestatsapi', status: 'COMPLETE',
          fields_filled: ['home_goals', 'away_goals', 'total_corners'] },
      ],
      scrape_status: 'COMPLETE',
    },
    reason_codes: [
      'PRE_MATCH_SNAPSHOT_CREATED',
      'POST_MATCH_RESULT_SETTLED',
    ],
    ...overrides,
  };
}

function makeSummary(overrides = {}) {
  return {
    pre_match_complete: true,
    pre_match_missing: [],
    post_match_settled: true,
    post_match_missing: [],
    pre_match_fields_filled: 6,
    pre_match_fields_total: 6,
    post_match_fields_filled: 4,
    post_match_fields_total: 4,
    ...overrides,
  };
}

describe('LearningSnapshotPanel', () => {
  it('renders null when snapshot is missing', () => {
    const { container } = render(<LearningSnapshotPanel snapshot={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the fixture header (teams + competition)', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    const fixture = screen.getByTestId('learning-snapshot-fixture');
    expect(fixture).toHaveTextContent('France vs Senegal');
    expect(fixture).toHaveTextContent('FIFA World Cup 2026');
  });

  it('renders all pre-match metric rows with values', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    expect(screen.getByTestId('learning-snapshot-home-xg-l5')).toHaveTextContent('2.10');
    expect(screen.getByTestId('learning-snapshot-away-xg-l5')).toHaveTextContent('1.40');
    expect(screen.getByTestId('learning-snapshot-home-corners-l5')).toHaveTextContent('6.2');
    expect(screen.getByTestId('learning-snapshot-btts-prob')).toHaveTextContent('0.620');
    expect(screen.getByTestId('learning-snapshot-over25-prob')).toHaveTextContent('0.640');
    expect(screen.getByTestId('learning-snapshot-odd-over25')).toHaveTextContent('1.85');
  });

  it('shows pre-match progress and COMPLETE status when nothing missing', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    expect(screen.getByTestId('learning-snapshot-pre-progress')).toHaveTextContent('6/6');
    expect(screen.getByTestId('learning-snapshot-pre-status')).toHaveTextContent('COMPLETE');
  });

  it('renders the missing-fields chip group when summary lists missing keys', () => {
    const summary = makeSummary({
      pre_match_complete: false,
      pre_match_missing: ['home_xg_l5', 'away_xg_l5'],
      pre_match_fields_filled: 4,
    });
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={summary} />);
    expect(screen.getByTestId('learning-snapshot-pre-status')).toHaveTextContent('PARTIAL');
    const missing = screen.getByTestId('learning-snapshot-pre-missing');
    expect(missing).toHaveTextContent('home_xg_l5');
    expect(missing).toHaveTextContent('away_xg_l5');
  });

  it('renders the sources cascade with status badges per entry', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    expect(screen.getByTestId('learning-snapshot-source-pre-0')).toHaveTextContent('thestatsapi');
    expect(screen.getByTestId('learning-snapshot-source-pre-0-status'))
      .toHaveTextContent('COMPLETE');
    expect(screen.getByTestId('learning-snapshot-source-pre-1')).toHaveTextContent('api_sports');
    expect(screen.getByTestId('learning-snapshot-source-pre-1-status'))
      .toHaveTextContent('PARTIAL');
    expect(screen.getByTestId('learning-snapshot-scrape-status'))
      .toHaveTextContent('COMPLETE');
  });

  it('renders the post-match outcome with derived hit chips', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    expect(screen.getByTestId('learning-snapshot-final-score')).toHaveTextContent('3-1');
    expect(screen.getByTestId('learning-snapshot-total-goals')).toHaveTextContent('4');
    // Hit chips colour-code by outcome.
    const chips = screen.getByTestId('learning-snapshot-hit-chips');
    expect(within(chips).getByTestId('learning-snapshot-chip-btts')).toHaveTextContent('BTTS');
    expect(within(chips).getByTestId('learning-snapshot-chip-over25')).toHaveTextContent('Over 2.5');
    expect(within(chips).getByTestId('learning-snapshot-chip-draw')).toHaveTextContent('Empate');
    expect(within(chips).getByTestId('learning-snapshot-chip-corners85')).toHaveTextContent('Over 8.5');
  });

  it('shows reason-code chips', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    const codes = screen.getByTestId('learning-snapshot-reason-codes');
    expect(codes).toHaveTextContent('PRE_MATCH_SNAPSHOT_CREATED');
    expect(codes).toHaveTextContent('POST_MATCH_RESULT_SETTLED');
  });

  it('handles a snapshot without post-match outcome (pre-only)', () => {
    const snap = makeSnapshot({
      post_match_outputs: {
        final_score: null, home_goals: null, away_goals: null,
        total_goals: null, btts_hit: null, over25_hit: null,
        draw_hit: null, total_corners: null,
        over85_corners_hit: null,
        real_home_xg: null, real_away_xg: null,
      },
    });
    const summary = makeSummary({
      post_match_settled: false,
      post_match_missing: ['final_score', 'home_goals', 'away_goals', 'total_goals'],
      post_match_fields_filled: 0,
    });
    render(<LearningSnapshotPanel snapshot={snap} summary={summary} />);
    expect(screen.getByTestId('learning-snapshot-post-status')).toHaveTextContent('PARTIAL');
    expect(screen.getByTestId('learning-snapshot-final-score')).toHaveTextContent('—');
  });

  it('every visible label is in Spanish (UI mandate)', () => {
    render(<LearningSnapshotPanel snapshot={makeSnapshot()} summary={makeSummary()} />);
    // Section titles.
    expect(screen.getByText(/Inputs pre-partido/i)).toBeInTheDocument();
    expect(screen.getByText(/Fuentes utilizadas/i)).toBeInTheDocument();
    expect(screen.getByText(/Resultado post-partido/i)).toBeInTheDocument();
    // Metric labels.
    expect(screen.getByText(/Goles totales/i)).toBeInTheDocument();
    expect(screen.getByText(/Marcador/i)).toBeInTheDocument();
  });
});
