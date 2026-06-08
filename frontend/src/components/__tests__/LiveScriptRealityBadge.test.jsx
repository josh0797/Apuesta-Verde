/**
 * RTL tests for LiveScriptRealityBadge (Phase 44 / P3).
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { LiveScriptRealityBadge } from '../LiveScriptRealityBadge';

const match = { match_id: 'mlb-1', sport: 'baseball', is_live: true };

describe('LiveScriptRealityBadge — rendering', () => {
  test('renders LIVE_UNDER_CONFIRMATION with emerald tone', () => {
    const check = {
      classification: 'LIVE_UNDER_CONFIRMATION',
      summary_es: 'Partido más cerrado que la proyección.',
      reason_codes: ['LIVE_UNDER_SCRIPT_CONFIRMED'],
      expected_runs: 7.5,
      live_projected_final_runs: 4.5,
      fragility_live: 0.1,
    };
    render(<LiveScriptRealityBadge check={check} match={match} lang="es" />);
    const badge = screen.getByTestId('live-script-badge-mlb-1');
    expect(badge.textContent).toContain('Under confirmado en vivo');
    expect(badge.getAttribute('data-classification')).toBe('LIVE_UNDER_CONFIRMATION');
  });

  test('renders LIVE_OVER_DANGER with red tone', () => {
    const check = { classification: 'LIVE_OVER_DANGER', summary_es: 'Over en peligro' };
    render(<LiveScriptRealityBadge check={check} match={match} />);
    const badge = screen.getByTestId('live-script-badge-mlb-1');
    expect(badge.textContent).toContain('Under en riesgo');
    expect(badge.className).toMatch(/red-500/);
  });

  test('renders LIVE_OVER_WARNING with amber tone', () => {
    const check = { classification: 'LIVE_OVER_WARNING', summary_es: 'Frágil' };
    render(<LiveScriptRealityBadge check={check} match={match} />);
    expect(screen.getByTestId('live-script-badge-mlb-1').textContent).toContain('Over en riesgo');
  });

  test('renders LIVE_OVER_CONFIRMATION with emerald tone', () => {
    const check = { classification: 'LIVE_OVER_CONFIRMATION', summary_es: 'OK' };
    render(<LiveScriptRealityBadge check={check} match={match} />);
    expect(screen.getByTestId('live-script-badge-mlb-1').textContent).toContain('Over confirmado');
  });

  test('returns null on LIVE_NEUTRAL', () => {
    const { container } = render(<LiveScriptRealityBadge check={{ classification: 'LIVE_NEUTRAL' }} match={match} />);
    expect(container.firstChild).toBeNull();
  });

  test('returns null when no data', () => {
    const { container } = render(<LiveScriptRealityBadge check={null} match={match} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('LiveScriptRealityBadge — details drawer', () => {
  test('toggle reveals reason codes + supporting stats', () => {
    const check = {
      classification: 'LIVE_UNDER_CONFIRMATION',
      summary_es: '...', reason_codes: ['STARTERS_DOMINATING_ABOVE_EXPECTATION', 'NO_HOME_RUN_SIGNAL'],
      expected_runs: 7.5, live_projected_final_runs: 4.5, fragility_live: 0.2,
    };
    const stats = { inning: 7, score_total: 2, hits: 8, walks: 3, home_runs: 0, left_on_base: 6 };
    render(<LiveScriptRealityBadge check={check} match={match} stats={stats} />);
    expect(screen.queryByTestId('live-script-badge-details-mlb-1')).toBeNull();
    fireEvent.click(screen.getByTestId('live-script-badge-toggle-mlb-1'));
    const details = screen.getByTestId('live-script-badge-details-mlb-1');
    expect(details.textContent).toMatch(/7.5/);
    expect(details.textContent).toMatch(/4.5/);
    expect(details.textContent).toMatch(/Inning/);
    expect(details.textContent).toMatch(/STARTERS_DOMINATING_ABOVE_EXPECTATION/);
  });
});
