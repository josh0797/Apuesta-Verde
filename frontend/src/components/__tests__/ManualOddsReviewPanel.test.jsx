/**
 * MLB-F93 — ManualOddsReviewPanel must consume the new `status`/`reprice`
 * contract while remaining backwards-compatible with the legacy
 * `value_status` + `manual_edge_pct` shape.
 *
 * Four tests:
 *   1. legacy contract still renders the value_status pill and (+x%) edge.
 *   2. F93 REPRICED renders the new decision badge (e.g. NO VALUE) and
 *      the reprice message line.
 *   3. F93 OVERRIDE_SAVED_ONLY renders the amber "no se pudo recalcular"
 *      note (and DOES NOT silently say "Guardada (override)" only).
 *   4. F93 REPRICED VALUE renders the +edge% suffix from `reprice.edge_pct`.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { post: (...a) => mockPost(...a) },
}));
jest.mock('sonner', () => ({
  __esModule: true,
  toast: {
    error:   jest.fn(),
    success: jest.fn(),
    warning: jest.fn(),
    message: jest.fn(),
  },
}));

import { ManualOddsReviewPanel } from '../ManualOddsReviewPanel';

function _item(overrides = {}) {
  return {
    id:        'pk-1',
    match_id:  'm-1',
    match_label: 'Detroit Tigers @ Houston Astros',
    classification: 'STRUCTURAL_LEAN',
    home_team: 'Houston Astros',
    away_team: 'Detroit Tigers',
    commence_time: '2026-06-15T18:10:00Z',
    mlb_script_v2: {
      coverProbability: 60,           // engine probability percentage
      recommendedLine:  'UNDER 9.5',
      expectedRuns:     8.7,
    },
    structural_quality: { level: 'high', score: 82 },
    ...overrides,
  };
}

async function _openRowAndType(odds = '1.90') {
  render(<ManualOddsReviewPanel items={[_item()]} />);
  // Expand the row.
  fireEvent.click(screen.getByTestId('manual-review-row-0-toggle'));
  // Type the odds.
  fireEvent.change(screen.getByTestId('manual-review-row-0-odds-input'), {
    target: { value: odds },
  });
  // Wait for the inline EV calculator to render the save button.
  await waitFor(() => {
    expect(screen.getByTestId('manual-review-row-0-save-odds-btn'))
      .toBeInTheDocument();
  });
}

beforeEach(() => mockPost.mockReset());

// ─────────────────────────────────────────────────────────────────────
// 1. Legacy contract (no F93 fields) still works.
// ─────────────────────────────────────────────────────────────────────
test('ManualOddsReviewPanel — legacy contract still renders value_status + edge%', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      value_status:    'VALUE',
      manual_edge_pct: 5.0,
      // No status / reprice keys (older backend).
    },
  });
  await _openRowAndType('1.90');
  fireEvent.click(screen.getByTestId('manual-review-row-0-save-odds-btn'));
  await waitFor(() => {
    expect(screen.getByTestId('manual-review-row-0-saved-status'))
      .toHaveTextContent(/VALUE/);
  });
  expect(screen.getByTestId('manual-review-row-0-saved-status'))
    .toHaveTextContent(/\+5\.0%/);
});

// ─────────────────────────────────────────────────────────────────────
// 2. F93 REPRICED with NO_VALUE → uses new decision badge + msg line.
// ─────────────────────────────────────────────────────────────────────
test('ManualOddsReviewPanel — F93 REPRICED NO_VALUE renders new badge + message', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'REPRICED',
      reprice: {
        available:  true,
        decision:   'NO_VALUE',
        edge_pct:   -4.2,
        manual_odd: 1.64,
        fair_odds:  1.57,
        ev:         -0.065,
      },
      message_user: 'Cuota aplicada: sigue sin valor a 1.64. El precio justo estimado es 1.57.',
      // Legacy mirrors also present (back-compat).
      value_status:    'NO_VALUE',
      manual_edge_pct: -4.2,
    },
  });
  await _openRowAndType('1.64');
  fireEvent.click(screen.getByTestId('manual-review-row-0-save-odds-btn'));
  await waitFor(() => {
    expect(screen.getByTestId('manual-review-row-0-saved-status'))
      .toHaveTextContent(/NO VALUE/);
  });
  // Reprice message line is rendered (basis-full row).
  expect(screen.getByTestId('manual-review-row-0-reprice-msg'))
    .toHaveTextContent(/sigue sin valor/i);
  // Edge percentage and fair odds appear in the pill.
  const pill = screen.getByTestId('manual-review-row-0-saved-status');
  expect(pill).toHaveTextContent(/-4\.2%/);
  expect(pill).toHaveTextContent(/fair 1\.57/i);
});

// ─────────────────────────────────────────────────────────────────────
// 3. F93 OVERRIDE_SAVED_ONLY surfaces the amber explanation, NOT a bare
//    "Guardada (override)" copy.
// ─────────────────────────────────────────────────────────────────────
test('ManualOddsReviewPanel — F93 OVERRIDE_SAVED_ONLY surfaces explanation', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'OVERRIDE_SAVED_ONLY',
      reprice: {
        available:    false,
        decision:     'MANUAL_ODDS_ONLY',
        manual_odd:   1.64,
        reason_codes: ['PICK_CONTEXT_NOT_FOUND'],
      },
      message_user: 'Cuota guardada, pero no se pudo recalcular porque no se encontró el contexto del pick.',
      next_action:  'REFRESH_OR_REGENERATE_REQUIRED',
      // Legacy mirrors.
      value_status: 'MANUAL_ODDS_ONLY',
      manual_edge_pct: null,
    },
  });
  await _openRowAndType('1.64');
  fireEvent.click(screen.getByTestId('manual-review-row-0-save-odds-btn'));
  await waitFor(() => {
    expect(screen.getByTestId('manual-review-row-0-override-saved-msg'))
      .toBeInTheDocument();
  });
  const msg = screen.getByTestId('manual-review-row-0-override-saved-msg');
  expect(msg).toHaveTextContent(/no se pudo recalcular/i);
  // The status pill is still shown but reflects MANUAL_ODDS_ONLY.
  expect(screen.getByTestId('manual-review-row-0-saved-status'))
    .toHaveTextContent(/SOLO INFO/);
});

// ─────────────────────────────────────────────────────────────────────
// 4. F93 REPRICED VALUE → +edge% comes from reprice.edge_pct
// ─────────────────────────────────────────────────────────────────────
test('ManualOddsReviewPanel — F93 REPRICED VALUE renders +edge% from reprice.edge_pct', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'REPRICED',
      reprice: {
        available:  true,
        decision:   'VALUE',
        edge_pct:   6.1,
        manual_odd: 1.92,
        fair_odds:  1.80,
        ev:         0.117,
      },
      message_user: 'Cuota aplicada: ahora hay valor a 1.92 (+6.1% edge).',
      value_status: 'VALUE',
      manual_edge_pct: 6.1,
    },
  });
  await _openRowAndType('1.92');
  fireEvent.click(screen.getByTestId('manual-review-row-0-save-odds-btn'));
  await waitFor(() => {
    expect(screen.getByTestId('manual-review-row-0-saved-status'))
      .toHaveTextContent(/VALUE/);
  });
  expect(screen.getByTestId('manual-review-row-0-saved-status'))
    .toHaveTextContent(/\+6\.1%/);
  expect(screen.getByTestId('manual-review-row-0-reprice-msg'))
    .toHaveTextContent(/hay valor a 1\.92/i);
});
