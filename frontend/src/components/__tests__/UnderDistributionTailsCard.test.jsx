/**
 * F97.3 — Tests RTL para `UnderDistributionTailsCard`.
 */
import { render, screen } from '@testing-library/react';
import { UnderDistributionTailsCard } from '../UnderDistributionTailsCard';

const baseProps = {
  expectedRunsDistribution: {
    available:      true,
    nivel3_applied: true,
    p90:            10.4,
    p95:            12.1,
    p99:            14.8,
    probabilities:  { "over_8.5": 0.42, "under_8.5": 0.58 },
    reason_codes:   [],
  },
  runDistributionMixer: {
    distribution_family:  'MIXTURE',
    poisson_weight:       0.40,
    nb_weight:            0.60,
    effective_dispersion: 1.20,
    tail_risk:            { bucket: 'MEDIUM' },
  },
  tailCalibration: {
    tail_risk_bucket: 'HIGH',
    tail_multiplier:  1.35,
    reason_codes:     ['P90_RECALIBRATED'],
    after: {
      percentiles: { p90: 10.4, p95: 12.1, p99: 14.8 },
    },
  },
  distributionBlender: {
    blend_weights:    { threshold_model: 0.55, distribution: 0.45 },
    divergence_flags: ['THRESHOLD_DIST_DIVERGENCE_9_5'],
    reason_codes:     ['DISTRIBUTION_THRESHOLD_DIVERGENCE'],
  },
  underHardRules: {
    applicable:     true,
    action:         'WARN',
    over_risk:      0.42,
    line_used:      8.5,
    tail_bucket:    'HIGH',
    triggered_rules: ['OVER_RISK_WARN'],
    reason_codes:    ['UNDER_RULES_WARN_OVER_RISK'],
    signals:         ['over_risk=0.420 ∈ [0.42, 0.48) → WARN'],
    score_delta:     -3,
    is_blocked:      false,
    block_max_pick:  false,
    exclude_from_main_feed: false,
    category:        null,
  },
  selectedLine: 8.5,
  testId:       'under-dist-tails',
};

describe('UnderDistributionTailsCard', () => {
  it('renders nothing when there is no NIVEL 3 data', () => {
    const { container } = render(<UnderDistributionTailsCard />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders distribution family label', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-distribution-label'))
      .toHaveTextContent('Mezcla');
  });

  it('renders Poisson and NB weights', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    const w = screen.getByTestId('under-dist-tails-weights');
    expect(w).toHaveTextContent(/Poisson/);
    expect(w).toHaveTextContent(/40.0%/);
    expect(w).toHaveTextContent(/NB/);
    expect(w).toHaveTextContent(/60.0%/);
  });

  it('renders effective dispersion + tail bucket badge', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    const disp = screen.getByTestId('under-dist-tails-dispersion');
    expect(disp).toHaveTextContent('×1.20');
    expect(screen.getByTestId('under-dist-tails-tail-bucket'))
      .toHaveTextContent('Cola alta');
  });

  it('renders p90 / p95 / p99', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-p90')).toHaveTextContent('10.40');
    expect(screen.getByTestId('under-dist-tails-p95')).toHaveTextContent('12.10');
    expect(screen.getByTestId('under-dist-tails-p99')).toHaveTextContent('14.80');
  });

  it('renders over_risk and line', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-over-risk-value'))
      .toHaveTextContent('42.0%');
    expect(screen.getByTestId('under-dist-tails-line')).toHaveTextContent('8.5');
  });

  it('renders the action badge (WARN)', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-action-badge'))
      .toHaveTextContent('Precaución');
  });

  it('renders divergence + recalibrated warnings as badges', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-warning-p90-recalibrated'))
      .toBeInTheDocument();
    expect(screen.getByTestId('under-dist-tails-warning-divergence'))
      .toBeInTheDocument();
  });

  it('renders Under degraded warning when AVOID', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      underHardRules={{ ...baseProps.underHardRules, action: 'AVOID' }}
    />);
    expect(screen.getByTestId('under-dist-tails-warning-under-degraded'))
      .toBeInTheDocument();
    expect(screen.getByTestId('under-dist-tails-action-badge'))
      .toHaveTextContent('Evitar');
  });

  it('renders Under degraded warning when BLOCK', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      underHardRules={{ ...baseProps.underHardRules, action: 'BLOCK' }}
    />);
    expect(screen.getByTestId('under-dist-tails-warning-under-degraded'))
      .toBeInTheDocument();
    expect(screen.getByTestId('under-dist-tails-action-badge'))
      .toHaveTextContent('Bloqueado');
  });

  it('renders P90 compressed warning when in reason codes', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      tailCalibration={{
        ...baseProps.tailCalibration,
        reason_codes: ['P90_TOO_COMPRESSED_FOR_CONTEXT', 'P90_RECALIBRATED'],
      }}
    />);
    expect(screen.getByTestId('under-dist-tails-warning-p90-compressed'))
      .toBeInTheDocument();
  });

  it('does not render final-action block when action is NONE', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      underHardRules={{
        ...baseProps.underHardRules,
        action: 'NONE',
        signals: [],
      }}
    />);
    expect(screen.queryByTestId('under-dist-tails-final-action'))
      .not.toBeInTheDocument();
  });

  it('renders final action signals when present', () => {
    render(<UnderDistributionTailsCard {...baseProps} />);
    expect(screen.getByTestId('under-dist-tails-final-action'))
      .toBeInTheDocument();
    expect(screen.getByTestId('under-dist-tails-signal-0'))
      .toHaveTextContent(/over_risk=0\.420/);
  });

  it('gracefully handles missing weights and dispersion', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      runDistributionMixer={{
        distribution_family: 'POISSON',
        // no weights, no dispersion
      }}
    />);
    expect(screen.getByTestId('under-dist-tails-distribution-label'))
      .toHaveTextContent('Poisson');
    expect(screen.getByTestId('under-dist-tails-dispersion'))
      .toHaveTextContent('—');
  });

  it('renders fallback when over_risk is null', () => {
    render(<UnderDistributionTailsCard
      {...baseProps}
      underHardRules={{
        ...baseProps.underHardRules,
        over_risk: null,
      }}
    />);
    expect(screen.getByTestId('under-dist-tails-over-risk-value'))
      .toHaveTextContent('—');
  });
});
