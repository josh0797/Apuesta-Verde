import { render, screen } from '@testing-library/react';
import { FootballTotalsModelPanel } from '../FootballDcNbPanels';

const baseModel = {
  available: true,
  mode: 'defaults',
  rho_used: -0.05,
  goals_dispersion_ratio: 1.0,
  under_2_5: { poisson: 0.55, dc_nb: 0.58, delta_pts: 3.0 },
  under_3_5: { poisson: 0.78, dc_nb: 0.82, delta_pts: 4.0 },
  offense_bucket: 'MID',
  league_tier: 'TIER_1',
  sample_size: 0,
};

describe('FootballTotalsModelPanel', () => {
  it('renders Poisson Under 3.5 probability', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-u35-poisson')).toHaveTextContent('78%');
  });

  it('renders DC/NB Under 3.5 probability', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-u35-dcnb')).toHaveTextContent('82%');
  });

  it('shows the delta as points (not a percentage of 100)', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    const delta = screen.getByTestId('totals-model-u35-delta');
    // Must show +4.0 pts — not "+400 pts" (which would mean *100 bug).
    expect(delta).toHaveTextContent(/\+4\.0 pts/);
  });

  it('shows the rho used', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-rho')).toHaveTextContent('-0.050');
  });

  it('shows the NB ratio', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-ratio')).toHaveTextContent('1.00');
  });

  it('shows DEFAULT CALIBRATION badge when mode=defaults', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-mode-defaults')).toBeInTheDocument();
  });

  it('shows EMPIRICAL CALIBRATION badge when mode=empirical', () => {
    render(<FootballTotalsModelPanel totalsModel={{ ...baseModel, mode: 'empirical' }} />);
    expect(screen.getByTestId('totals-model-mode-empirical')).toBeInTheDocument();
  });

  it('shows NB INERT badge when ratio == 1.0', () => {
    render(<FootballTotalsModelPanel totalsModel={baseModel} />);
    expect(screen.getByTestId('totals-model-nb-inert')).toBeInTheDocument();
  });

  it('shows NB ACTIVE badge when ratio > 1.0', () => {
    render(<FootballTotalsModelPanel totalsModel={{ ...baseModel, goals_dispersion_ratio: 1.4 }} />);
    expect(screen.getByTestId('totals-model-nb-active')).toBeInTheDocument();
  });

  it('renders nothing when available is false', () => {
    const { container } = render(
      <FootballTotalsModelPanel totalsModel={{ ...baseModel, available: false }} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when totalsModel is null', () => {
    const { container } = render(<FootballTotalsModelPanel totalsModel={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
