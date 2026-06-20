/**
 * RTL tests for UnderHiddenRisksCard (Sprint D12 UI).
 *
 * Verifica:
 *  - No rinde nada cuando totalRiskOverlay es null/undefined.
 *  - Renderiza header con badge de verdict + tail risk.
 *  - Renderiza las 6 tarjetas con sus data-testid.
 *  - Traduce reason codes al español.
 *  - Muestra el badge de dispersion NB cuando multiplier > 1.0.
 */
import { render, screen } from '@testing-library/react';
import { UnderHiddenRisksCard } from '../UnderHiddenRisksCard';

const buildBlockOverlay = () => ({
  verdict: 'BLOCK',
  explosive_tail_risk: 'EXTREME',
  dispersion_multiplier: 1.80,
  editorial_summary: 'Riesgo de explosión muy alto — Under no recomendado.',
  reason_codes: [
    'VOLATILE_STARTER_VS_EXPLOSIVE_LINEUP',
    'EXTREME_FIRST_INNING_COLLAPSE_RISK',
    'BULLPEN_EXHAUSTION_RISK',
    'DOMINO_RISK_STARTER_TO_BULLPEN',
    'UNDER_TAIL_RISK_RECALIBRATED',
  ],
  components: {
    starter_volatility: {
      home: { starter_volatility_score: 88, bucket: 'EXTREME' },
      away: { starter_volatility_score: 75, bucket: 'HIGH' },
    },
    first_inning_collapse: {
      home: { first_inning_collapse_score: 92 },
      away: { first_inning_collapse_score: 70 },
    },
    recent_offensive_quality: {
      home: { bucket: 'EXPLOSIVE' },
      away: { bucket: 'HOT' },
    },
    lineup_explosiveness: {
      home: { lineup_explosiveness_score: 86, bucket: 'EXPLOSIVE' },
      away: { lineup_explosiveness_score: 72, bucket: 'STRONG' },
    },
    bullpen_stress: {
      home: { bullpen_stress_score: 82, bucket: 'EXHAUSTED' },
      away: { bullpen_stress_score: 55, bucket: 'NORMAL' },
    },
    domino_risk: {
      home: { domino_risk_score: 84 },
      away: { domino_risk_score: 60 },
    },
  },
});

describe('UnderHiddenRisksCard — rendering', () => {
  test('returns null when overlay is missing', () => {
    const { container } = render(<UnderHiddenRisksCard totalRiskOverlay={null} />);
    expect(container.firstChild).toBeNull();
  });

  test('returns null when overlay is undefined', () => {
    const { container } = render(<UnderHiddenRisksCard />);
    expect(container.firstChild).toBeNull();
  });

  test('renders root container with default testId', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    expect(screen.getByTestId('under-hidden-risks')).toBeTruthy();
  });

  test('uses custom testId when provided', () => {
    render(
      <UnderHiddenRisksCard
        totalRiskOverlay={buildBlockOverlay()}
        testId="mlb-script-abc-under-hidden-risks"
      />,
    );
    expect(screen.getByTestId('mlb-script-abc-under-hidden-risks')).toBeTruthy();
    expect(screen.getByTestId('mlb-script-abc-under-hidden-risks-verdict-badge')).toBeTruthy();
  });

  test('renders verdict badge with translated label (BLOCK → Bloqueado)', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const badge = screen.getByTestId('under-hidden-risks-verdict-badge');
    expect(badge.textContent).toContain('Bloqueado');
  });

  test('renders tail risk badge (EXTREME → Cola extrema)', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const tail = screen.getByTestId('under-hidden-risks-tail-badge');
    expect(tail.textContent).toContain('Cola extrema');
  });

  test('renders dispersion badge when multiplier > 1.0', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const disp = screen.getByTestId('under-hidden-risks-dispersion-badge');
    expect(disp.textContent).toContain('NB');
    expect(disp.textContent).toContain('1.80');
  });

  test('does NOT render dispersion badge when multiplier is 1.0', () => {
    const overlay = { ...buildBlockOverlay(), dispersion_multiplier: 1.0 };
    render(<UnderHiddenRisksCard totalRiskOverlay={overlay} />);
    expect(screen.queryByTestId('under-hidden-risks-dispersion-badge')).toBeNull();
  });

  test('renders editorial summary when present', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const ed = screen.getByTestId('under-hidden-risks-editorial');
    expect(ed.textContent).toContain('Riesgo de explosión muy alto');
  });

  test('renders all 6 pillar cards with data-testid', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const expectedKeys = [
      'starter-volatility',
      'first-inning',
      'recent-offense',
      'lineup-explosiveness',
      'bullpen-stress',
      'domino-risk',
    ];
    for (const k of expectedKeys) {
      expect(screen.getByTestId(`under-hidden-risks-card-${k}`)).toBeTruthy();
    }
  });

  test('shows worst-side score per pillar (starter volatility: peak 88)', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const score = screen.getByTestId('under-hidden-risks-card-starter-volatility-score');
    expect(score.textContent).toBe('88');
    const bucket = screen.getByTestId('under-hidden-risks-card-starter-volatility-bucket');
    expect(bucket.textContent).toBe('Extremo');
  });

  test('translates recent_offensive_quality bucket to Spanish (EXPLOSIVE → Explosiva)', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const bucket = screen.getByTestId('under-hidden-risks-card-recent-offense-bucket');
    expect(bucket.textContent).toBe('Explosiva');
  });

  test('translates bullpen bucket to Spanish (EXHAUSTED → Agotado)', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const bucket = screen.getByTestId('under-hidden-risks-card-bullpen-stress-bucket');
    expect(bucket.textContent).toBe('Agotado');
  });

  test('translates reason codes to Spanish in chips', () => {
    render(<UnderHiddenRisksCard totalRiskOverlay={buildBlockOverlay()} />);
    const list = screen.getByTestId('under-hidden-risks-reason-codes');
    expect(list.textContent).toContain('Abridor volátil enfrenta lineup explosivo');
    expect(list.textContent).toContain('Riesgo extremo de colapso en la 1ª entrada');
    expect(list.textContent).toContain('Bullpen al borde del agotamiento');
    expect(list.textContent).toContain('Cola del Under recalibrada');
  });

  test('renders ALLOW verdict correctly (Permitido)', () => {
    const overlay = {
      verdict: 'ALLOW',
      explosive_tail_risk: 'LOW',
      dispersion_multiplier: 1.0,
      editorial_summary: 'Sin señales adversas relevantes para el Under.',
      reason_codes: [],
      components: {},
    };
    render(<UnderHiddenRisksCard totalRiskOverlay={overlay} />);
    const badge = screen.getByTestId('under-hidden-risks-verdict-badge');
    expect(badge.textContent).toContain('Permitido');
    const tail = screen.getByTestId('under-hidden-risks-tail-badge');
    expect(tail.textContent).toContain('Cola baja');
  });

  test('renders gracefully with empty components (no scores)', () => {
    const overlay = {
      verdict: 'WARN',
      explosive_tail_risk: 'MEDIUM',
      dispersion_multiplier: 1.15,
      reason_codes: [],
      components: {},
    };
    render(<UnderHiddenRisksCard totalRiskOverlay={overlay} />);
    // All 6 cards should still render with "Sin datos" buckets.
    const sv = screen.getByTestId('under-hidden-risks-card-starter-volatility-bucket');
    expect(sv.textContent).toBe('Sin datos');
    // No score node when score is null.
    expect(
      screen.queryByTestId('under-hidden-risks-card-starter-volatility-score'),
    ).toBeNull();
  });
});
