import { render, screen } from '@testing-library/react';
import { FootballOverSupportPanel } from '../FootballDcNbPanels';

const base = {
  available: true,
  mode: 'observe_only',
  over_1_5_support_score: 78,
  over_2_5_support_score: 55,
  first_half_pressure_score: 60,
  early_goal_pressure_score: 65,
  combined_offensive_pressure: 58,
  fragility_score: 42,
  recommended_over_market: 'OVER_1_5',
  reason_codes: ['OVER_1_5_PROTECTED', 'LIVE_OVER_CONFIRMED_BY_PRESSURE'],
  egp_home: 52,
  egp_away: 48,
};

describe('FootballOverSupportPanel', () => {
  it('renders Over 1.5 support score', () => {
    render(<FootballOverSupportPanel overSupport={base} />);
    expect(screen.getByTestId('over-support-score-1-5')).toHaveTextContent('78');
  });

  it('renders Over 2.5 support score', () => {
    render(<FootballOverSupportPanel overSupport={base} />);
    expect(screen.getByTestId('over-support-score-2-5')).toHaveTextContent('55');
  });

  it('renders recommended_over_market display label', () => {
    render(<FootballOverSupportPanel overSupport={base} />);
    expect(screen.getByTestId('over-support-recommended')).toHaveTextContent('1.5');
  });

  it('shows OBSERVE ONLY badge when mode=observe_only', () => {
    render(<FootballOverSupportPanel overSupport={base} />);
    expect(screen.getByTestId('over-support-observe-only')).toBeInTheDocument();
  });

  it('renders reason codes', () => {
    render(<FootballOverSupportPanel overSupport={base} />);
    const codes = screen.getByTestId('over-support-codes');
    expect(codes).toHaveTextContent('OVER_1_5_PROTECTED');
    expect(codes).toHaveTextContent('LIVE_OVER_CONFIRMED_BY_PRESSURE');
  });

  it('renders nothing when available is false', () => {
    const { container } = render(
      <FootballOverSupportPanel overSupport={{ ...base, available: false }} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when overSupport is null', () => {
    const { container } = render(<FootballOverSupportPanel overSupport={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
