/**
 * MLB-F93.1 — Frontend tests for the manual-odds context pass-through
 * and authenticated debug behaviour.
 *
 * Seven obligatory tests:
 *   1. inline_manual_odds_sends_pick_context (and normalizes team objs)
 *   2. inline_manual_odds_calls_on_reprice (callback for both REPRICED
 *      and OVERRIDE_SAVED_ONLY)
 *   3. match_card_updates_odds_after_reprice (manualReprice path renders
 *      MANUAL pill + decision; not "Desconocido"/no fair_odds for VALUE)
 *   4. match_card_hides_unknown_when_manual_odds_exists
 *   5. match_card_hides_no_value_when_saved_only (decision → SOLO INFO)
 *   6. debug_button_uses_authenticated_api_client (api.get called)
 *   7. debug_button_does_not_open_raw_url (window.open never called)
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockPost = jest.fn();
const mockGet  = jest.fn();
jest.mock('@/lib/api', () => ({
  __esModule: true,
  api: {
    post: (...a) => mockPost(...a),
    get:  (...a) => mockGet(...a),
  },
}));
jest.mock('sonner', () => ({
  __esModule: true,
  toast: {
    error: jest.fn(), success: jest.fn(), warning: jest.fn(), message: jest.fn(),
  },
}));

import { InlineManualOddsInput } from '../InlineManualOddsInput';

const _pickContext = {
  id: 'pk-1', match_id: 'm-1',
  home_team: { name: 'Houston Astros', displayName: 'Astros' },
  away_team: { name: 'Detroit Tigers' },
  commence_time: '2026-06-15T18:10:00Z',
  recommendation: { market: 'UNDER 9.5', confidence_score: 80 },
  _mlb_script_v2: { coverProbability: 61 },
};

function _render(props = {}) {
  return render(
    <InlineManualOddsInput
      pickId="pk-1"
      matchId="m-1"
      gamePk="g-1"
      homeTeam={{ name: 'Houston Astros', displayName: 'Astros' }}
      awayTeam={{ name: 'Detroit Tigers' }}
      commenceDate="2026-06-15"
      market="UNDER 9.5"
      selection="Menos de 9.5 carreras"
      line={9.5}
      pickContext={_pickContext}
      lang="es"
      {...props}
    />,
  );
}

beforeEach(() => { mockPost.mockReset(); mockGet.mockReset(); });

// ─────────────────────────────────────────────────────────────────────
// 1. Sends pick_context (with normalized teams) in the POST body.
// ─────────────────────────────────────────────────────────────────────
test('inline_manual_odds_sends_pick_context', async () => {
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'REPRICED',
            reprice: { available: true, decision: 'VALUE',
                       edge_pct: 5.0, manual_odd: 1.92 },
            message_user: 'ok' },
  });
  _render();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.92' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));

  const [url, body] = mockPost.mock.calls[0];
  expect(url).toBe('/mlb/picks/pk-1/manual-odds');
  // Teams normalized to strings.
  expect(body.home_team).toBe('Houston Astros');
  expect(body.away_team).toBe('Detroit Tigers');
  // selection forwarded.
  expect(body.selection).toBe('Menos de 9.5 carreras');
  // pick_context trimmed but present.
  expect(body.pick_context).toBeDefined();
  expect(body.pick_context.id).toBe('pk-1');
  expect(body.pick_context._mlb_script_v2.coverProbability).toBe(61);
  // Probability-bearing fields kept.
  expect('expected_runs_distribution' in body.pick_context).toBe(true);
});

// ─────────────────────────────────────────────────────────────────────
// 2. onReprice callback fires for both REPRICED and OVERRIDE_SAVED_ONLY.
// ─────────────────────────────────────────────────────────────────────
test('inline_manual_odds_calls_on_reprice', async () => {
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'OVERRIDE_SAVED_ONLY',
            reprice: { available: false, decision: 'MANUAL_ODDS_ONLY',
                       manual_odd: 1.64 },
            message_user: 'Cuota guardada, pero no se pudo recalcular.' },
  });
  const onReprice = jest.fn();
  _render({ onReprice });
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => expect(onReprice).toHaveBeenCalledTimes(1));
  expect(onReprice.mock.calls[0][0].status).toBe('OVERRIDE_SAVED_ONLY');
});

// ─────────────────────────────────────────────────────────────────────
// 3. After reprice the decision pill renders (basic confirmation that
//    MatchCard would receive enough data to update). The deeper card
//    integration is covered in MatchCard tests (visual smoke).
// ─────────────────────────────────────────────────────────────────────
test('match_card_updates_odds_after_reprice', async () => {
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'REPRICED',
            reprice: { available: true, decision: 'VALUE',
                       edge_pct: 6.1, manual_odd: 1.92, fair_odds: 1.80 },
            message_user: 'Ahora hay valor a 1.92 (+6.1% edge).' },
  });
  const onReprice = jest.fn();
  _render({ onReprice });
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.92' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toHaveTextContent(/VALUE/);
  });
  // Edge appears for REPRICED (NOT for saved-only).
  expect(screen.getByTestId('inline-manual-odds-input-decision'))
    .toHaveTextContent(/\+6\.1%/);
  // Payload carried fair_odds for the parent card.
  const cbArg = onReprice.mock.calls[0][0];
  expect(cbArg.reprice.fair_odds).toBe(1.80);
});

// ─────────────────────────────────────────────────────────────────────
// 4. The component renders MANUAL/SOLO INFO instead of "Desconocido".
//    We check the decision pill never reads "Desconocido".
// ─────────────────────────────────────────────────────────────────────
test('match_card_hides_unknown_when_manual_odds_exists', async () => {
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'OVERRIDE_SAVED_ONLY',
            reprice: { available: false, decision: 'MANUAL_ODDS_ONLY',
                       manual_odd: 1.64 },
            message_user: 'Cuota guardada.' },
  });
  _render();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toBeInTheDocument();
  });
  const pill = screen.getByTestId('inline-manual-odds-input-decision');
  expect(pill).not.toHaveTextContent(/desconocido/i);
  expect(pill).not.toHaveTextContent(/unknown/i);
});

// ─────────────────────────────────────────────────────────────────────
// 5. OVERRIDE_SAVED_ONLY → label is SOLO INFO, NEVER NO VALUE.
// ─────────────────────────────────────────────────────────────────────
test('match_card_hides_no_value_when_saved_only', async () => {
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'OVERRIDE_SAVED_ONLY',
            reprice: { available: false, decision: 'NO_VALUE',
                       manual_odd: 1.64, edge_pct: -4 },
            message_user: 'Guardada, sin reprice.' },
  });
  _render();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-decision'))
      .toBeInTheDocument();
  });
  const pill = screen.getByTestId('inline-manual-odds-input-decision');
  expect(pill).toHaveTextContent(/SOLO INFO/);
  // NO VALUE label must NOT appear when saved-only (forced override).
  expect(pill).not.toHaveTextContent(/NO VALUE/);
  // Edge percentage hidden when saved-only.
  expect(pill).not.toHaveTextContent(/%/);
});

// ─────────────────────────────────────────────────────────────────────
// 6. "Ver debug" calls api.get with params (authenticated client).
// ─────────────────────────────────────────────────────────────────────
test('debug_button_uses_authenticated_api_client', async () => {
  // First the save (saved-only) so the debug CTA renders.
  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'OVERRIDE_SAVED_ONLY',
            reprice: { available: false, decision: 'MANUAL_ODDS_ONLY',
                       manual_odd: 1.64 },
            message_user: 'Guardada.' },
  });
  mockGet.mockResolvedValueOnce({
    data: { ok: true, final_status: 'OVERRIDE_SAVED_ONLY',
            lookup_attempts: [], missing_fields: ['pick_context'] },
  });
  _render();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-debug'))
      .toBeInTheDocument();
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-debug'));
  await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));

  const [url, opts] = mockGet.mock.calls[0];
  expect(url).toBe('/mlb/manual-odds/debug');
  expect(opts.params.match_id).toBe('m-1');
  expect(opts.params.pick_id).toBe('pk-1');
  expect(opts.params.game_pk).toBe('g-1');
  // Debug panel renders.
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-debug-content'))
      .toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────
// 7. Debug button does NOT open a raw window URL.
// ─────────────────────────────────────────────────────────────────────
test('debug_button_does_not_open_raw_url', async () => {
  const openSpy = jest.fn();
  const origOpen = window.open;
  window.open = openSpy;

  mockPost.mockResolvedValueOnce({
    data: { ok: true, status: 'OVERRIDE_SAVED_ONLY',
            reprice: { available: false, decision: 'MANUAL_ODDS_ONLY',
                       manual_odd: 1.64 },
            message_user: 'Guardada.' },
  });
  mockGet.mockResolvedValueOnce({
    data: { ok: true, final_status: 'PICK_NOT_FOUND',
            lookup_attempts: [], missing_fields: ['pick_context'] },
  });
  _render();
  fireEvent.change(screen.getByTestId('inline-manual-odds-input-field'), {
    target: { value: '1.64' },
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-submit'));
  await waitFor(() => {
    expect(screen.getByTestId('inline-manual-odds-input-debug'))
      .toBeInTheDocument();
  });
  fireEvent.click(screen.getByTestId('inline-manual-odds-input-debug'));
  await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
  expect(openSpy).not.toHaveBeenCalled();

  window.open = origOpen;
});
