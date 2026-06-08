/**
 * InningLambdaPanel — Phase 47.
 *
 * Renders the per-phase Poisson projection (λ_1_3 + λ_4_6 + λ_7_9) that
 * the MLB pregame pipeline persists at
 * ``match.inning_lambda_projection``. Observe-only badge by default.
 *
 * Layout (collapsible):
 *   ─ Header: chevron + total expected runs + delta vs baseline
 *   ─ Three side-by-side cards with each phase λ and its dominant factor
 *   ─ F5 projection row
 *   ─ Traffic interaction line (only when bullpen phase fired siege code)
 *   ─ Reason-code chips (filtered, with Spanish tooltips)
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp, TrendingUp, TrendingDown, Sigma } from 'lucide-react';

const REASON_DESCRIPTIONS = {
  INNING_LAMBDA_MODEL_USED:           'Modelo de λ por innings aplicado.',
  STARTER_SUPPRESSES_EARLY_RUNS:      'Los abridores limitan runs tempranas.',
  STARTER_EARLY_RISK:                 'Abridores débiles → riesgo temprano.',
  TRANSITION_PHASE_RISK:              'Fase de transición (3rd time through).',
  STARTER_DURABILITY_LOW:             'Bajo promedio de innings del abridor.',
  BULLPEN_PHASE_RISK:                 'Bullpen frágil — riesgo en innings 7-9.',
  BULLPEN_FATIGUE_RAISES_LATE_LAMBDA: 'Bullpen agotado eleva λ tardío.',
  HIGH_TRAFFIC_RAISES_LATE_LAMBDA:    'Tráfico ofensivo alto eleva λ tardío.',
  LOW_TRAFFIC_LIMITS_BULLPEN_RISK:    'Tráfico bajo limita el riesgo de bullpen.',
  TRAFFIC_SCORE_MISSING_NEUTRAL_USED: 'Sin traffic_score: se usa valor neutral 0.50.',
  LATE_EXPLOSION_RISK_EMBEDDED:       'Riesgo de explosión tardía incorporado.',
  INNING_LAMBDA_SIGNIFICANT_DELTA:    'Diferencia significativa vs proyección base.',
};

function fmt(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

export function InningLambdaPanel({ projection, lang = 'es', testId }) {
  const [open, setOpen] = useState(false);
  if (!projection || !projection.available) return null;

  const phases = projection.phase_breakdown || {};
  const reasonCodes = Array.isArray(projection.reason_codes) ? projection.reason_codes : [];
  const trafficSignal = (phases.bullpen_phase?.reason_codes || []).find(
    (rc) => rc === 'HIGH_TRAFFIC_RAISES_LATE_LAMBDA' || rc === 'LOW_TRAFFIC_LIMITS_BULLPEN_RISK',
  );
  const delta = projection.delta_vs_baseline ?? 0;
  const deltaTone = delta > 0 ? 'amber' : delta < 0 ? 'emerald' : 'neutral';
  const DeltaIcon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Sigma;
  const trafficScore = phases.bullpen_phase?.traffic_score;

  const trafficNarrative = trafficSignal === 'HIGH_TRAFFIC_RAISES_LATE_LAMBDA'
    ? (lang === 'en'
        ? `Late innings projected at ${fmt(projection.lambda_7_9)} runs because the bullpen is fatigued and the offensive traffic is high.`
        : `El modelo proyecta ${fmt(projection.lambda_7_9)} carreras en innings 7-9 porque el bullpen está agotado y el tráfico ofensivo es alto.`)
    : trafficSignal === 'LOW_TRAFFIC_LIMITS_BULLPEN_RISK'
      ? (lang === 'en'
          ? 'Bullpen is vulnerable, but low offensive traffic limits the late-explosion risk.'
          : 'El bullpen es vulnerable, pero el tráfico ofensivo bajo limita el riesgo de explosión tardía.')
      : null;

  return (
    <section
      className="rounded-lg border border-border bg-card/50"
      data-testid={testId || 'inning-lambda-panel'}
      data-observe-only={String(projection.observe_only !== false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-secondary/30 transition-colors rounded-lg"
        data-testid="inning-lambda-toggle"
      >
        <div className="flex items-center gap-2">
          <Sigma className="h-3.5 w-3.5 text-violet-300" />
          <span className="text-xs font-semibold">
            {lang === 'en' ? 'Run distribution by innings' : 'Distribución de carreras por innings'}
          </span>
          <Badge variant="outline" className="text-[9px]">observe-only</Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] opacity-80 font-mono">
            {lang === 'en' ? 'Total' : 'Total'}: <strong>{fmt(projection.expected_runs)}</strong>
          </span>
          {Math.abs(delta) >= 0.05 && (
            <span
              className={`flex items-center gap-0.5 text-[10px] font-mono ${
                deltaTone === 'amber' ? 'text-amber-300'
                : deltaTone === 'emerald' ? 'text-emerald-300'
                : 'opacity-60'
              }`}
              data-testid="inning-lambda-delta"
            >
              <DeltaIcon className="h-3 w-3" />
              {delta > 0 ? '+' : ''}{fmt(delta)}
            </span>
          )}
          {open ? <ChevronUp className="h-3.5 w-3.5 opacity-70" />
                : <ChevronDown className="h-3.5 w-3.5 opacity-70" />}
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2" data-testid="inning-lambda-details">
          <div className="grid grid-cols-3 gap-2">
            <PhaseCard
              label={lang === 'en' ? 'λ 1-3 (Starter)' : 'λ 1-3 (Abridor)'}
              value={projection.lambda_1_3}
              testId="inning-lambda-phase-1-3"
              factorLabel={lang === 'en' ? 'starter' : 'abridor'}
              factor={phases.starter_phase?.starter_factor}
            />
            <PhaseCard
              label={lang === 'en' ? 'λ 4-6 (Transition)' : 'λ 4-6 (Transición)'}
              value={projection.lambda_4_6}
              testId="inning-lambda-phase-4-6"
              factorLabel={lang === 'en' ? 'durability' : 'durabilidad'}
              factor={phases.transition_phase?.durability_factor}
            />
            <PhaseCard
              label={lang === 'en' ? 'λ 7-9 (Bullpen)' : 'λ 7-9 (Bullpen)'}
              value={projection.lambda_7_9}
              testId="inning-lambda-phase-7-9"
              factorLabel={lang === 'en' ? 'bullpen' : 'bullpen'}
              factor={phases.bullpen_phase?.bullpen_factor}
              tone={(phases.bullpen_phase?.bullpen_factor || 1) > 1.05 ? 'amber' : 'neutral'}
            />
          </div>

          <div className="flex items-center justify-between text-[10px] pt-1 border-t border-border">
            <span className="opacity-70">
              {lang === 'en' ? 'F5 projection' : 'Proyección F5'}:
              {' '}<strong className="text-foreground">{fmt(projection.f5_expected_runs)}</strong>
            </span>
            <span className="opacity-70">
              {lang === 'en' ? 'Full game baseline' : 'Total base'}:
              {' '}<strong className="text-foreground">{fmt(projection.baseline_expected_runs)}</strong>
            </span>
            {trafficScore != null && (
              <span className="opacity-70">
                {lang === 'en' ? 'Traffic' : 'Tráfico'}:
                {' '}<strong className="text-foreground">{trafficScore}/100</strong>
              </span>
            )}
          </div>

          {trafficNarrative && (
            <p className="text-[10px] leading-snug bg-violet-500/10 border border-violet-500/30 rounded px-2 py-1.5"
               data-testid="inning-lambda-traffic-narrative">
              {trafficNarrative}
            </p>
          )}

          {reasonCodes.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5" data-testid="inning-lambda-reason-codes">
              {reasonCodes.map((rc) => (
                <Badge
                  key={rc}
                  variant="outline"
                  className="text-[9px]"
                  title={REASON_DESCRIPTIONS[rc] || rc}
                  data-testid={`inning-lambda-rc-${rc}`}
                >
                  {rc}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function PhaseCard({ label, value, factor, factorLabel, tone = 'neutral', testId }) {
  const toneCls = tone === 'amber'
    ? 'border-amber-500/40 bg-amber-500/5'
    : 'border-border bg-card/40';
  return (
    <div
      className={`rounded-md border ${toneCls} p-2 space-y-0.5`}
      data-testid={testId}
    >
      <div className="text-[9px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-lg font-semibold font-mono leading-tight">{fmt(value)}</div>
      {factor != null && (
        <div className="text-[9px] opacity-60 font-mono">
          ×{fmt(factor, 3)} {factorLabel}
        </div>
      )}
    </div>
  );
}

export default InningLambdaPanel;
