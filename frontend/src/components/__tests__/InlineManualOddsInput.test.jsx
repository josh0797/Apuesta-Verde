/**
 * MLB-F93 — Frontend tests for the InlineManualOddsInput + MatchCard
 * manual-odds reprice + UI refresh flow.
 *
 * Five obligatory tests:
 *   1. button shows "Recalculating…" while saving
 *   2. REPRICED response updates the card badge / odds / message
 *   3. NO_VALUE message is specific (mentions fair odds, no value)
 *   4. OVERRIDE_SAVED_ONLY shows the regenerate CTA
 *   5. updating odds does NOT require a full page reload
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ── Mock the api + sonner modules BEFORE importing the component ────
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

import { InlineManualOddsInput } from '../InlineManualOddsInput';

function _renderInput(props = {}) {
  return render(
    <InlineManualOddsInput
      pickId="pk-1"
      matchId="m-1"
      gamePk="g-1"
      homeTeam="Houston Astros"
      awayTeam="Detroit Tigers"
      commenceDate="2026-06-15"
      market="UNDER_9_5"
      line={9.5}
      lang="es"
      {...props}
    />,
  );
}

beforeEach(() => {
  mockPost.mockReset();
});

// ─────────────────────────────────────────────────────────────────────
// 1. "Recalculating…" copy while waiting for the backend
// ─────────────────────────────────────────────────────────────────────
test('manual_odds_button_shows_recalculating', async () => {
  let resolveCall;
  mockPost.mockImplementation(() => new Promise((res) => { resolveCall = res; }));

  _renderInput();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.90' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));

  // Loading indicators visible.
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-loading-msg'))
      .toHaveTextContent(/recalculando/i);
  });
  expect(screen.getByTestId('inline-manual-odds-input-submit'))
    .toHaveTextContent(/recalculando/i);

  // Resolve so React can finish the state update.
  resolveCall({
    data: {
      ok: true, status: 'REPRICED',
      reprice: { available: true, decision: 'VALUE', edge_pct: 5.0,
                 manual_odd: 1.90 },
      message_user: 'Cuota aplicada: ahora hay valor a 1.90 (+5.0% edge).',
    },
  });
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────
// 2. REPRICED updates badge, decision pill, and message
// ─────────────────────────────────────────────────────────────────────
test('manual_odds_repriced_updates_card_badge', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'REPRICED',
      reprice: {
        available:           true,
        decision:            'VALUE',
        edge_pct:            6.1,
        manual_odd:          1.92,
        fair_odds:           1.80,
        implied_probability: 0.521,
        model_probability:   0.582,
        ev:                  0.117,
      },
      message_user:  'Cuota aplicada: ahora hay valor a 1.92 (+6.1% edge).',
      message_debug: 'pick_runs lookup.',
    },
  });

  const onReprice = jest.fn();
  _renderInput({ onReprice });

  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.92' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));

  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toHaveTextContent(/VALUE/);
  });
  // Edge is rendered.
  expect(screen.getByTestId('inline-manual-odds-input-decision'))
    .toHaveTextContent(/\+6\.1%/);
  // Reprice message line is visible.
  expect(screen.getByTestId('inline-manual-odds-input-reprice-msg'))
    .toHaveTextContent(/hay valor a 1\.92/i);
  // Callback fired so MatchCard can refresh.
  expect(onReprice).toHaveBeenCalledWith(expect.objectContaining({
    status:  'REPRICED',
    reprice: expect.objectContaining({ decision: 'VALUE', edge_pct: 6.1 }),
  }));
});

// ─────────────────────────────────────────────────────────────────────
// 3. NO_VALUE message is specific (mentions sigue sin valor + fair)
// ─────────────────────────────────────────────────────────────────────
test('manual_odds_no_value_message_specific', async () => {
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
      },
      message_user: 'Cuota aplicada: sigue sin valor a 1.64. El precio justo estimado es 1.57.',
    },
  });

  _renderInput();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));

  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toHaveTextContent(/NO VALUE/);
  });
  const msg = screen.getByTestId('inline-manual-odds-input-reprice-msg');
  expect(msg).toHaveTextContent(/sigue sin valor/i);
  expect(msg).toHaveTextContent(/1\.57/);
});

// ─────────────────────────────────────────────────────────────────────
// 4. OVERRIDE_SAVED_ONLY shows regenerate / refresh / debug CTAs
// ─────────────────────────────────────────────────────────────────────
test('manual_odds_saved_only_shows_regenerate_cta', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'OVERRIDE_SAVED_ONLY',
      reprice: {
        available: false,
        decision: 'MANUAL_ODDS_ONLY',
        manual_odd: 1.64,
        reason_codes: ['PICK_CONTEXT_NOT_FOUND', 'MANUAL_ODDS_OVERRIDE_USED'],
      },
      message_user: 'Cuota guardada, pero no se pudo recalcular porque no se encontró el contexto del pick.',
      next_action:  'REFRESH_OR_REGENERATE_REQUIRED',
    },
  });

  _renderInput();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));

  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-saved-only'))
      .toBeInTheDocument();
  });
  // All three CTAs.
  expect(screen.getByTestId('inline-manual-odds-input-refresh'))
    .toHaveTextContent(/refrescar/i);
  expect(screen.getByTestId('inline-manual-odds-input-regenerate'))
    .toHaveTextContent(/regenerar/i);
  expect(screen.getByTestId('inline-manual-odds-input-debug'))
    .toHaveTextContent(/debug/i);
  // The user-visible copy is NOT the bare "Guardada (override)".
  const text = screen.getByTestId('inline-manual-odds-input-saved-only').textContent.toLowerCase();
  expect(text).toMatch(/cuota guardada/);
  expect(text).toMatch(/no se pudo recalcular/);
});

// ─────────────────────────────────────────────────────────────────────
// 5. Successful reprice does NOT trigger a full-page reload
// ─────────────────────────────────────────────────────────────────────
test('manual_odds_does_not_require_full_page_reload', async () => {
  mockPost.mockResolvedValueOnce({
    data: {
      ok: true,
      status: 'REPRICED',
      reprice: { available: true, decision: 'NO_VALUE', edge_pct: -2.1,
                 manual_odd: 1.70, fair_odds: 1.65 },
      message_user: 'Cuota aplicada: sigue sin valor a 1.70.',
    },
  });
  // Spy on window.location to ensure no reload is triggered.
  const reloadSpy = jest.fn();
  const origLocation = window.location;
  delete window.location;
  window.location = { ...origLocation, reload: reloadSpy, href: origLocation.href, origin: origLocation.origin };

  const onReprice = jest.fn();
  _renderInput({ onReprice });

  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.70' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));

  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toBeInTheDocument();
  });
  expect(reloadSpy).not.toHaveBeenCalled();
  expect(onReprice).toHaveBeenCalledTimes(1);

  // Restore.
  window.location = origLocation;
});
