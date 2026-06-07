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
import { normalizeDecimalOdds, normalizeManualOddsInput, isValidBookieOdds } from '@/lib/normalizeDecimalOdds';

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
    expect(normalizeDecimalOdds('0')).toBeNull();
    expect(normalizeDecimalOdds('-1.5')).toBeNull();
  });

  test('isValidBookieOdds floor at 1.01', () => {
    expect(isValidBookieOdds('1.01')).toBe(false);
    expect(isValidBookieOdds('1,02')).toBe(true);
    expect(isValidBookieOdds('2.50')).toBe(true);
    expect(isValidBookieOdds('not-a-number')).toBe(false);
  });

  // Phase 41 / Fix 3 — Mobile keyboard normalization
  test('normalizeManualOddsInput accepts mobile commas like "1,21"', () => {
    expect(normalizeManualOddsInput('1,21')).toBe(1.21);
    expect(normalizeManualOddsInput('1.21')).toBe(1.21);
    expect(normalizeManualOddsInput(' 1,85 ')).toBe(1.85);
    expect(normalizeManualOddsInput('2,05')).toBe(2.05);
  });

  test('normalizeManualOddsInput rejects values below 1.01 floor', () => {
    expect(normalizeManualOddsInput('1.01')).toBeNull();
    expect(normalizeManualOddsInput('1,01')).toBeNull();
    expect(normalizeManualOddsInput('1.00')).toBeNull();
    expect(normalizeManualOddsInput('0,50')).toBeNull();
  });

  test('normalizeManualOddsInput rejects malformed input', () => {
    expect(normalizeManualOddsInput('')).toBeNull();
    expect(normalizeManualOddsInput(null)).toBeNull();
    expect(normalizeManualOddsInput('abc')).toBeNull();
    expect(normalizeManualOddsInput('1,2,3')).toBeNull();
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
    expect(path).toBe('/analysis/live/reevaluate-one');
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
// Fix 3 — Engine vs Manual source + register outcome
// ─────────────────────────────────────────────────────────────────────
describe('LiveReevalPanel engine vs manual source (Fix 3)', () => {
  test('default source=engine; track WON sends source=engine + entry context', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        result: {
          live_state: 'LIVE_VALUE_WINDOW',
          risk_level: 'LOW',
          recommended_action: 'BET',
          market: 'Under 2.5',
          selection: 'Under 2.5',
          confidence: 70,
          decimal_odds: 1.85,
          live_snapshot: { minute: 55, score: { home: 0, away: 1 } },
        },
      },
    });
    mockPost.mockResolvedValueOnce({ data: { ok: true } });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.getByTestId('reeval-track-m-77')).toBeInTheDocument());
    expect(screen.getByTestId('reeval-source-engine-m-77')).toBeChecked();
    fireEvent.click(screen.getByTestId('reeval-track-won-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
    const [path, body] = mockPost.mock.calls[1];
    expect(path).toBe('/picks/track');
    expect(body.source).toBe('engine');
    expect(body.is_live).toBe(true);
    expect(body.entry_minute).toBe(55);
    expect(body.entry_score_home).toBe(0);
    expect(body.entry_score_away).toBe(1);
    expect(body.entry_score_display).toBe('0-1');
    expect(body.market).toBe('Under 2.5');
  });

  test('switching source=manual uses manualMarket + normalized manualOdds', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        result: {
          live_state: 'LIVE_VALUE_WINDOW',
          risk_level: 'LOW',
          recommended_action: 'BET',
          market: 'Over 2.5',
          selection: 'Over 2.5',
          confidence: 60,
          decimal_odds: 2.10,
          live_snapshot: { minute: 30, score: { home: 1, away: 0 } },
        },
      },
    });
    mockPost.mockResolvedValueOnce({ data: { ok: true } });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-use-manual-m-77'));
    fireEvent.change(screen.getByTestId('reeval-manual-odds-m-77'), { target: { value: '1,55' } });
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.getByTestId('reeval-track-m-77')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reeval-source-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-track-won-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
    const [, body] = mockPost.mock.calls[1];
    expect(body.source).toBe('manual');
    expect(body.odds).toBe(1.55); // normalized from "1,55"
    // Default football market in the dropdown is Under 2.5.
    expect(body.market).toBe('Under 2.5');
  });

  test('Cancelled outcome is supported and sent verbatim', async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        result: {
          live_state: 'WATCHLIST', risk_level: 'MEDIUM', recommended_action: 'WATCH',
          market: 'Over 1.5', selection: 'Over 1.5', confidence: 50, decimal_odds: 1.40,
          live_snapshot: { minute: 12, score: { home: 0, away: 0 } },
        },
      },
    });
    mockPost.mockResolvedValueOnce({ data: { ok: true } });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.getByTestId('reeval-track-cancelled-m-77')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reeval-track-cancelled-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
    const [, body] = mockPost.mock.calls[1];
    expect(body.outcome).toBe('cancelled');
    expect(body.source).toBe('engine');
  });
});

// ─────────────────────────────────────────────────────────────────────
// Phase 41 / Fix 3 — Per-card reevaluate endpoint + inline error UX
// ─────────────────────────────────────────────────────────────────────
describe('LiveReevalPanel per-card reanalysis (Fix 3)', () => {
  test('default (non-manual) path hits the per-card alias endpoint and sends ONLY this match_id', async () => {
    mockPost.mockResolvedValueOnce({
      data: { result: { live_state: 'WATCHLIST', risk_level: 'LOW', recommended_action: 'WATCH' } },
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const [path, body] = mockPost.mock.calls[0];
    expect(path).toBe('/analysis/live/reevaluate-one');   // alias URL
    expect(body.match_id).toBe('m-77');
    // No batch sweep — the payload is scoped to this card only.
    expect(Object.keys(body).sort()).toEqual(['match_id', 'refresh', 'sport'].sort());
  });

  test('error path shows inline banner with dismiss button — does NOT affect other cards', async () => {
    mockPost.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'Engine boom' } },
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.getByTestId('reeval-error-m-77')).toBeInTheDocument());
    expect(screen.getByTestId('reeval-error-m-77').textContent).toContain('Engine boom');
    // Dismiss clears the banner.
    fireEvent.click(screen.getByTestId('reeval-error-dismiss-m-77'));
    expect(screen.queryByTestId('reeval-error-m-77')).toBeNull();
  });

  test('successful re-run clears any previous inline error banner', async () => {
    mockPost.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'transient failure' } },
    });
    mockPost.mockResolvedValueOnce({
      data: { result: { live_state: 'WATCHLIST', risk_level: 'LOW', recommended_action: 'WATCH' } },
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.getByTestId('reeval-error-m-77')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reeval-btn-m-77'));
    await waitFor(() => expect(screen.queryByTestId('reeval-error-m-77')).toBeNull());
  });

  test('blur on manual-odds field with invalid value triggers a toast (lazy validation)', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    fireEvent.click(screen.getByTestId('reeval-use-manual-m-77'));
    const input = screen.getByTestId('reeval-manual-odds-m-77');
    // User typed an invalid (<=1.01) odds. Validation only fires on blur.
    fireEvent.change(input, { target: { value: '1,00' } });
    expect(mockToastError).not.toHaveBeenCalled();   // not on change
    fireEvent.blur(input);
    expect(mockToastError).toHaveBeenCalledTimes(1); // fires on blur
  });

  test('input enforces inputMode=decimal + type=text for mobile compatibility', () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reeval-toggle-manual-m-77'));
    const input = screen.getByTestId('reeval-manual-odds-m-77');
    expect(input.getAttribute('inputmode')).toBe('decimal');
    expect(input.getAttribute('type')).toBe('text');
    // Pattern must allow both comma and dot as decimal separator.
    expect(input.getAttribute('pattern')).toMatch(/[,.]/);
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
    const [path, body, opts] = mockPost.mock.calls[0];
    expect(path).toBe('/analysis/live/reevaluate-one');
    expect(body.manual_odds).toBeUndefined();
    expect(opts.timeout).toBe(20000);
  });
});
