/**
 * RTL tests for MatchOutcomeModal (Phase 42 / Feature 7).
 *
 * Covers:
 *  - Renders engine summary chips
 *  - Computes line_distance live (delta line shown when actualLine differs)
 *  - Submit posts /picks/track with the full Line-Learning payload
 *  - Cashout_win / cashout_loss outcomes supported
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockApiPost = jest.fn();
const apiClient = { post: (...args) => mockApiPost(...args) };

jest.mock('sonner', () => ({
  __esModule: true,
  toast: { success: jest.fn(), error: jest.fn() },
}));

import { MatchOutcomeModal } from '../MatchOutcomeModal';

beforeEach(() => {
  mockApiPost.mockReset();
});

const baseMatch = {
  match_id: 'm-42',
  home_team: { name: 'Yankees' },
  away_team: { name: 'Red Sox' },
  league: 'MLB',
  sport: 'baseball',
};

const engineRec = {
  market:     'total_runs_under',
  selection:  'Under 9.5',
  line:       9.5,
  odds:       1.85,
  projection: 7.8,
  confidence: 65,
  is_live:    false,
};

function renderModal(extra = {}) {
  const onOpenChange = jest.fn();
  const onConfirmed  = jest.fn();
  const utils = render(
    <MatchOutcomeModal
      open={true}
      onOpenChange={onOpenChange}
      match={baseMatch}
      engineRec={engineRec}
      apiClient={apiClient}
      lang="es"
      onConfirmed={onConfirmed}
      {...extra}
    />
  );
  return { ...utils, onOpenChange, onConfirmed };
}


describe('MatchOutcomeModal — engine summary + actual bet form', () => {
  test('renders engine recommendation chips', () => {
    renderModal();
    const summary = screen.getByTestId('outcome-modal-engine-summary-m-42');
    expect(summary.textContent).toMatch(/total_runs_under/);
    expect(summary.textContent).toMatch(/Under 9.5/);
    expect(summary.textContent).toMatch(/9.5/);
    expect(summary.textContent).toMatch(/1.85/);
  });

  test('shows line-distance chip when user line differs from engine', () => {
    renderModal();
    // Open with engine line 9.5; change actualLine to 10.0 (comma decimal).
    fireEvent.change(screen.getByTestId('outcome-modal-actual-line-m-42'), {
      target: { value: '10,0' },
    });
    const chip = screen.getByTestId('outcome-modal-line-distance-m-42');
    expect(chip.textContent).toContain('+0.5');
  });
});


describe('MatchOutcomeModal — submit → /picks/track payload', () => {
  test('PUSH outcome with actual line 10.0 sends full line-learning fields', async () => {
    mockApiPost.mockResolvedValueOnce({
      data: {
        ok: true,
        line_learning: {
          classification: 'PUSH_SAVED',
          reason_codes: ['PUSH_SAVED_BY_LINE', 'AGGRESSIVE_LINE_TOO_TIGHT'],
          summary_es: 'Lectura correcta; línea engine demasiado agresiva, push salvó bankroll',
          observe_only: true,
        },
      },
    });
    const { onConfirmed } = renderModal();
    fireEvent.change(screen.getByTestId('outcome-modal-actual-line-m-42'), {
      target: { value: '10,0' },
    });
    fireEvent.change(screen.getByTestId('outcome-modal-actual-odds-m-42'), {
      target: { value: '1,26' },
    });
    fireEvent.change(screen.getByTestId('outcome-modal-final-value-m-42'), {
      target: { value: '10' },
    });
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-push-m-42'));
    fireEvent.click(screen.getByTestId('outcome-modal-submit-m-42'));

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    const [path, body] = mockApiPost.mock.calls[0];
    expect(path).toBe('/picks/track');
    expect(body.outcome).toBe('push');
    expect(body.actual_line).toBe(10.0);
    expect(body.actual_odds).toBe(1.26);
    expect(body.final_value).toBe(10);
    expect(body.engine_projection).toBe(7.8);
    expect(body.market_type).toBe('total_runs');
    // source becomes manual because the line differs from engine.
    expect(body.source).toBe('manual');
    // onConfirmed is called with the response.
    await waitFor(() => expect(onConfirmed).toHaveBeenCalled());
  });

  test('cashout_win outcome is accepted and sent verbatim', async () => {
    mockApiPost.mockResolvedValueOnce({ data: { ok: true } });
    renderModal();
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-cashout_win-m-42'));
    fireEvent.click(screen.getByTestId('outcome-modal-submit-m-42'));
    await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    expect(mockApiPost.mock.calls[0][1].outcome).toBe('cashout_win');
  });

  test('cashout_loss outcome is supported', async () => {
    mockApiPost.mockResolvedValueOnce({ data: { ok: true } });
    renderModal();
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-cashout_loss-m-42'));
    fireEvent.click(screen.getByTestId('outcome-modal-submit-m-42'));
    await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    expect(mockApiPost.mock.calls[0][1].outcome).toBe('cashout_loss');
  });

  test('blocks submit until an outcome is picked', () => {
    renderModal();
    const submitBtn = screen.getByTestId('outcome-modal-submit-m-42');
    expect(submitBtn).toBeDisabled();
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-won-m-42'));
    expect(submitBtn).not.toBeDisabled();
  });

  test('user keeping the engine line uses source=engine', async () => {
    mockApiPost.mockResolvedValueOnce({ data: { ok: true } });
    renderModal();
    // No edits — keep engine market/line/odds.
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-won-m-42'));
    fireEvent.click(screen.getByTestId('outcome-modal-submit-m-42'));
    await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(1));
    expect(mockApiPost.mock.calls[0][1].source).toBe('engine');
  });
});


describe('MatchOutcomeModal — learning summary surface', () => {
  test('renders learning preview after a successful submit', async () => {
    mockApiPost.mockResolvedValueOnce({
      data: {
        ok: true,
        line_learning: {
          classification: 'PUSH_SAVED',
          reason_codes: ['PUSH_SAVED_BY_LINE'],
          summary_es: 'Lectura correcta; línea demasiado agresiva, push salvó bankroll',
          summary_en: 'Correct profile; engine line too tight',
          observe_only: true,
        },
      },
    });
    renderModal();
    fireEvent.click(screen.getByTestId('outcome-modal-outcome-push-m-42'));
    fireEvent.click(screen.getByTestId('outcome-modal-submit-m-42'));
    await waitFor(() => expect(screen.getByTestId('outcome-modal-learning-m-42')).toBeInTheDocument());
    const learning = screen.getByTestId('outcome-modal-learning-m-42');
    expect(learning.textContent).toMatch(/PUSH_SAVED/);
    expect(learning.textContent).toMatch(/observe-only/i);
  });
});
