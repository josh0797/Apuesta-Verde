/**
 * FIX-3 — TailRiskPanel polarity-guard escalation tests.
 *
 * Verifies that when the backend returns
 * ``TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL`` in the
 * ``tail_fragility.reason_codes`` payload, the UI renders the
 * escalation notice so the user does NOT see the contradictory pair:
 *
 *     "Riesgo de cola explosiva: Alta"
 *     "Tail Fragility: Bajo"
 *
 * The notice must be in Spanish by default and switch to English when
 * ``lang="en"``.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TailRiskPanel } from '../TailRiskPanel';

const TAIL_RISK_HIGH = {
  available:        true,
  tail_bucket:      'HIGH',
  p_ge_12:          0.31,
  p_ge_14:          0.14,
  p_ge_16:          0.05,
  p_ge_18:          0.02,
  market_line:      7.5,
  under_probability: 0.55,
  over_probability:  0.45,
  interpretation_es: 'Demo interpretation.',
};

const TAIL_FRAGILITY_ESCALATED = {
  available:            true,
  tail_bucket:          'MEDIUM',
  explosive_tail_score: 40,
  base_adjustment:      5,
  interaction_total:    0,
  total_adjustment:     5,
  cap_hit:              false,
  interactions:         [],
  reason_codes: [
    'TAIL_FRAGILITY_USED',
    'EXPLOSIVE_TAIL_MEDIUM',
    'TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL',
  ],
  narrative_es: 'Riesgo de cola explosiva MEDIO (40/100). Tail Fragility escalado porque la distribución asigna alta probabilidad a escenarios de 12+ (≈ 31%) / 14+ (≈ 14%) carreras.',
};

const TAIL_FRAGILITY_LOW_NO_ESCALATION = {
  available:            true,
  tail_bucket:          'LOW',
  explosive_tail_score: 8,
  base_adjustment:      0,
  interaction_total:    0,
  total_adjustment:     0,
  cap_hit:              false,
  interactions:         [],
  reason_codes:        ['TAIL_FRAGILITY_USED', 'EXPLOSIVE_TAIL_LOW'],
  narrative_es:        'Riesgo de cola explosiva BAJO (8/100). La distribución concentra la masa cerca de la media.',
};

test('FIX-3 — escalation notice renders in Spanish when reason code present', async () => {
  render(
    <TailRiskPanel
      tailRisk={TAIL_RISK_HIGH}
      tailFragility={TAIL_FRAGILITY_ESCALATED}
      lang="es"
    />,
  );
  // Expand the collapsible card.
  await userEvent.click(screen.getByTestId('tail-risk-panel-toggle'));
  const notice = screen.getByTestId('tail-risk-panel-fragility-engine-escalation-notice');
  expect(notice).toBeInTheDocument();
  expect(notice).toHaveTextContent(/Tail Fragility escalado/i);
  expect(notice).toHaveTextContent(/12\+\s*\/\s*14\+ carreras/);
});

test('FIX-3 — escalation notice renders in English when lang="en"', async () => {
  render(
    <TailRiskPanel
      tailRisk={TAIL_RISK_HIGH}
      tailFragility={TAIL_FRAGILITY_ESCALATED}
      lang="en"
    />,
  );
  await userEvent.click(screen.getByTestId('tail-risk-panel-toggle'));
  const notice = screen.getByTestId('tail-risk-panel-fragility-engine-escalation-notice');
  expect(notice).toHaveTextContent(/Tail Fragility escalated/i);
  expect(notice).toHaveTextContent(/12\+\s*\/\s*14\+ runs/);
});

test('FIX-3 — escalation notice HIDDEN when reason code absent (no false positives)', async () => {
  render(
    <TailRiskPanel
      tailRisk={TAIL_RISK_HIGH}
      tailFragility={TAIL_FRAGILITY_LOW_NO_ESCALATION}
      lang="es"
    />,
  );
  await userEvent.click(screen.getByTestId('tail-risk-panel-toggle'));
  expect(
    screen.queryByTestId('tail-risk-panel-fragility-engine-escalation-notice'),
  ).toBeNull();
});

test('FIX-3 — when escalation fires, bucket badge no longer reads LOW', async () => {
  render(
    <TailRiskPanel
      tailRisk={TAIL_RISK_HIGH}
      tailFragility={TAIL_FRAGILITY_ESCALATED}
      lang="es"
    />,
  );
  await userEvent.click(screen.getByTestId('tail-risk-panel-toggle'));
  const bucketBadge = screen.getByTestId('tail-risk-panel-fragility-engine-bucket');
  // Spanish label for MEDIUM is "Medio". Must NOT be "Bajo".
  expect(bucketBadge).toHaveTextContent(/Medio/i);
  expect(bucketBadge).not.toHaveTextContent(/Bajo/i);
});
