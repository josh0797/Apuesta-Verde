/**
 * RTL tests for LiveReevalPanel (Phase 38 — Fix 2):
 *   - Comma decimal input is normalized to a dot before sending.
 *   - Over/Under 0.5 markets are present in the football dropdown.
 *   - Timeout error shows the Spanish "no perdiste los datos" message
 *     and KEEPS the user's manual_odds + manual_market state intact.
 *   - Manual-odds path uses the extended 45s timeout.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockPost = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: { post: (...args) => mockPost(...args) },
}));

const mockToastError = jest.fn();
const mockToastSuccess = jest.fn();
jest.mock('sonner', () => ({
  __esModule: true,
  toast: { error: (...a) => mockToastError(...a), success: (...a) => mockToastSuccess(...a) },
}));

// Stub heavy Copilot card — the result section uses it only when
// `result.interpreter` exists. Our tests don't reach that branch.
jest.mock('../LiveCopilotCard', () => ({
  __esModule: true,
  LiveCopilotCard: () => null,
}));

import { LiveReevalPanel } from '../LiveReevalPanel';
import { normalizeDecimalOdds, isValidBookieOdds } from '@/lib/normalizeDecimalOdds';

beforeEach(() => {
  mockPost.mockReset();
  mockToastError.mockReset();
  mockToastSuccess.mockReset();
});

const baseMatch = {
  match_id: 'm-77',
  home_team: { name: 'Home' },
  away_team: { name: 'Away' },
  league: 'Test League',
};

function renderPanel(extra = {}) {
  return render(<LiveReevalPanel match={baseMatch} sport="football" {...extra} />);
}

// ─────────────────────────────────────────────────────────────────────
// normalizeDecimalOdds helper
// ─────────────────────────────────────────────────────────────────────
describe('normalizeDecimalOdds helper', () => {
  test('accepts dot and comma separators', () => {
    expect(normalizeDecimalOdds('1.20')).toBe(1.2);
    expect(normalizeDecimalOdds('1,20')).toBe(1.2);
    expect(normalizeDecimalOdds('1.2')).toBe(1.2);
    expect(normalizeDecimalOdds('1,2')).toBe(1.2);
    expect(normalizeDecimalOdds(' 1,85 ')).toBe(1.85);
  });

  test('returns null for invalid input', () => {
    expect(normalizeDecimalOdds(null)).toBeNull();
    expect(normalizeDecimalOdds('')).toBeNull();
    expect(normalizeDecimalOdds('abc')).toBeNull();
  });

  test('isValidBookieOdds floor at 1.01', () => {
    expect(isValidBookieOdds('1.01')).toBe(false);
    expect(isValidBookieOdds('1,02')).toBe(true);
    expect(isValidBookieOdds('2.50')).toBe(true);
    expect(isValidBookieOdds('not-a-number')).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Football market dropdown — Over/Under 0.5 must be available
// ─────────────────────────────────────────────────────────────────────
describe('LiveReevalPanel football markets', () => {
  test('toggle exposes the manual form when clicked', () => {
    renderPanel();
    const toggle = screen.getByTestId('reeval-toggle-manual-m-77');
    fireEvent.click(toggle);
    expect(screen.getByTestId('reeval-manual-form-m-77')).toBeInTheDocument();
    expect(screen.getByTestId('reeval-market-select-m-77')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────
// Comma decimal payload normalization
// ─────────────────────────────────────────────────────────────────────
describe('LiveReevalPanel manual odds comma decimal', () => {
  test('sends manual_odds as Number with dot decimal when user types a comma', async () => {
    mockPost.mockResolvedValueOnce({ data: { result: { live_state: 'WATCHLIST', risk_level: 'MEDIUM', recommended_action: 'WATCH' } } });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-use-manual-m-77'));
    fireEvent.change(screen.getByTestId('reeval-manual-odds-m-77'), {
      target: { value: '1,35' },
    });
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const [path, body, opts] = mockPost.mock.calls[0];
    expect(path).toBe('/live/reevaluate');
    expect(body.manual_odds).toBe(1.35);
    expect(typeof body.manual_odds).toBe('number');
    // Manual path uses the extended 45s timeout.
    expect(opts.timeout).toBe(45000);
  });

  test('rejects invalid odds <= 1.01 with a toast and no API call', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-use-manual-m-77'));
    fireEvent.change(screen.getByTestId('reeval-manual-odds-m-77'), {
      target: { value: '1,00' },
    });
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1));
    expect(mockPost).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────
// Timeout / fail-soft UX
// ─────────────────────────────────────────────────────────────────────
describe('LiveReevalPanel timeout UX', () => {
  test('timeout in manual mode shows the "no pierdes los datos" Spanish message and preserves inputs', async () => {
    const err = Object.assign(new Error('timeout of 45000ms exceeded'), { code: 'ECONNABORTED' });
    mockPost.mockRejectedValueOnce(err);

    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-use-manual-m-77'));
    const oddsInput = screen.getByTestId('reeval-manual-odds-m-77');
    fireEvent.change(oddsInput, { target: { value: '1,20' } });

    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1));

    const message = mockToastError.mock.calls[0][0];
    // Spanish copy must include both: "cuota manual" + "sin perder los datos"
    expect(message).toMatch(/cuota manual/i);
    expect(message).toMatch(/sin perder los datos/i);

    // After the timeout, the user's typed odds remain in the input.
    expect(oddsInput.value).toBe('1,20');
  });

  test('default (non-manual) path uses the 20s timeout', async () => {
    mockPost.mockResolvedValueOnce({ data: { result: { live_state: 'WATCHLIST', risk_level: 'LOW', recommended_action: 'WATCH' } } });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const [, body, opts] = mockPost.mock.calls[0];
    expect(body.manual_odds).toBeUndefined();
    expect(opts.timeout).toBe(20000);
  });
});
