/**
 * UnderDistributionTailsCard — F97.3 (UI)
 *
 * Card de “Distribución y colas” para picks MLB de Under. Se renderiza
 * al lado / debajo de `UnderHiddenRisksCard` en la vista detalle MLB,
 * consumiendo el output de NIVEL 3 Bloque 2 (§1-§4) + Bloque 3 (§5-§6):
 *
 *   - `expected_runs_distribution` (post-NIVEL3 writeback) → p90/p95/p99
 *     + final_over_probabilities por línea.
 *   - `run_distribution_mixer` → distribución usada (POISSON/NB/MIXTURE),
 *     pesos Poisson/NB, dispersión efectiva.
 *   - `tail_calibration` → bucket (LOW/MEDIUM/HIGH/EXTREME),
 *     `tail_multiplier`, reason_codes incluyendo `P90_TOO_COMPRESSED_FOR_CONTEXT`
 *     y `P90_RECALIBRATED`.
 *   - `distribution_blender` → `blend_weights`, `divergence_flags`.
 *   - `under_hard_rules` (F97.1) → acción WARN/AVOID/BLOCK + over_risk
 *     + degradación.
 *
 * Constraints:
 *   - Read-only, observe-only.
 *   - Si no hay datos de NIVEL3, retorna `null`.
 *   - Todos los nodos críticos llevan data-testid kebab-case.
 *   - Per design guidelines: usar Shadcn components (`Badge`, `Card`).
 */
import { AlertTriangle, BarChart3, Sparkles, ShieldOff, Activity, Layers, Gauge } from 'lucide-react';
import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

// ──────────────────────────────────────────────────────────────────────
// Labels & color tones
// ──────────────────────────────────────────────────────────────────────
const ACTION_LABEL = {
  NONE:  'Sin alerta',
  WARN:  'Precaución',
  AVOID: 'Evitar',
  BLOCK: 'Bloqueado',
};

const ACTION_TONE = {
  NONE:  'bg-emerald-500/15 text-emerald-100 border-emerald-500/40',
  WARN:  'bg-amber-500/15 text-amber-100 border-amber-500/40',
  AVOID: 'bg-orange-500/20 text-orange-100 border-orange-500/45',
  BLOCK: 'bg-rose-500/20 text-rose-100 border-rose-500/50',
};

const DIST_LABEL = {
  POISSON:  'Poisson',
  NB:       'Binomial Negativa',
  MIXTURE:  'Mezcla',
  UNKNOWN:  'Desconocida',
};

const TAIL_LABEL = {
  LOW:     'Cola baja',
  MEDIUM:  'Cola media',
  HIGH:    'Cola alta',
  EXTREME: 'Cola extrema',
};

const TAIL_TONE = {
  LOW:     'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  MEDIUM:  'bg-amber-500/10 text-amber-200 border-amber-500/30',
  HIGH:    'bg-orange-500/20 text-orange-100 border-orange-500/40',
  EXTREME: 'bg-rose-500/20 text-rose-100 border-rose-500/50',
};

// Warnings que renderizamos como badges inline.
const WARNING_KEYS = [
  {
    id:      'p90-compressed',
    label:   'P90 comprimido',
    icon:    AlertTriangle,
    rcCheck: (codes) => codes.includes('P90_TOO_COMPRESSED_FOR_CONTEXT')
                       || codes.includes('CENTRAL_MEAN_NOT_ENOUGH'),
    tone:    'bg-amber-500/20 text-amber-100 border-amber-500/45',
  },
  {
    id:      'p90-recalibrated',
    label:   'Cola recalibrada',
    icon:    Sparkles,
    rcCheck: (codes) => codes.includes('P90_RECALIBRATED'),
    tone:    'bg-sky-500/20 text-sky-100 border-sky-500/45',
  },
  {
    id:      'divergence',
    label:   'Divergencia dist vs modelo',
    icon:    Activity,
    customCheck: ({ divergenceFlags }) =>
      Array.isArray(divergenceFlags) && divergenceFlags.length > 0,
    tone:    'bg-violet-500/20 text-violet-100 border-violet-500/45',
  },
  {
    id:      'under-degraded',
    label:   'Under degradado',
    icon:    ShieldOff,
    customCheck: ({ underAction }) =>
      ['AVOID', 'BLOCK'].includes(underAction),
    tone:    'bg-rose-500/20 text-rose-100 border-rose-500/50',
  },
];


// ──────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────
const fmtPct = (v) => {
  if (v === null || v === undefined) return '—';
  const num = Number(v);
  if (!Number.isFinite(num)) return '—';
  return `${(num * 100).toFixed(1)}%`;
};

const fmtNum = (v, digits = 2) => {
  if (v === null || v === undefined) return '—';
  const num = Number(v);
  if (!Number.isFinite(num)) return '—';
  return num.toFixed(digits);
};

const fmtLine = (line) => {
  if (line === null || line === undefined) return '—';
  const num = Number(line);
  if (!Number.isFinite(num)) return '—';
  return num.toFixed(1);
};


// ──────────────────────────────────────────────────────────────────────
// Component
// ──────────────────────────────────────────────────────────────────────
export function UnderDistributionTailsCard({
  expectedRunsDistribution = null,
  runDistributionMixer     = null,
  tailCalibration          = null,
  distributionBlender      = null,
  underHardRules           = null,
  selectedLine             = null,
  testId                   = 'under-distribution-tails',
}) {
  // Don't render if there is no NIVEL 3 data at all.
  const hasNivel3 = !!(
    expectedRunsDistribution?.nivel3_applied ||
    runDistributionMixer ||
    tailCalibration ||
    distributionBlender ||
    underHardRules
  );
  if (!hasNivel3) return null;

  // Distribution family.
  const distFamily = (
    runDistributionMixer?.distribution_family
    || runDistributionMixer?.family
    || 'UNKNOWN'
  );
  const distLabel = DIST_LABEL[distFamily] || DIST_LABEL.UNKNOWN;

  // Mixer weights.
  const poissonWeight = runDistributionMixer?.poisson_weight
                      ?? runDistributionMixer?.weights?.poisson
                      ?? null;
  const nbWeight      = runDistributionMixer?.nb_weight
                      ?? runDistributionMixer?.weights?.negative_binomial
                      ?? null;

  // Effective dispersion (NB shape).
  const dispersion = runDistributionMixer?.effective_dispersion
                   ?? runDistributionMixer?.dispersion
                   ?? null;

  // Percentiles (post tail calibration).
  const p90 = expectedRunsDistribution?.p90 ?? tailCalibration?.after?.percentiles?.p90 ?? null;
  const p95 = expectedRunsDistribution?.p95 ?? tailCalibration?.after?.percentiles?.p95 ?? null;
  const p99 = expectedRunsDistribution?.p99 ?? tailCalibration?.after?.percentiles?.p99 ?? null;

  // Tail bucket.
  const tailBucket = (
    tailCalibration?.tail_risk_bucket
    || tailCalibration?.bucket
    || runDistributionMixer?.tail_risk?.bucket
    || 'LOW'
  );
  const tailToneCls = TAIL_TONE[tailBucket] || TAIL_TONE.LOW;
  const tailLabel   = TAIL_LABEL[tailBucket] || TAIL_LABEL.LOW;

  // Action & over risk.
  const underAction = underHardRules?.action || 'NONE';
  const actionTone  = ACTION_TONE[underAction] || ACTION_TONE.NONE;
  const actionLabel = ACTION_LABEL[underAction] || ACTION_LABEL.NONE;
  const overRisk    = underHardRules?.over_risk ?? null;
  const lineUsed    = underHardRules?.line_used ?? selectedLine ?? null;

  // Divergence flags.
  const divergenceFlags = distributionBlender?.divergence_flags || [];

  // Reason codes union (used for warning badges).
  const allRcs = [
    ...(tailCalibration?.reason_codes || []),
    ...(distributionBlender?.reason_codes || []),
    ...(expectedRunsDistribution?.reason_codes || []),
    ...(underHardRules?.reason_codes || []),
  ];

  // Compute active warnings.
  const activeWarnings = WARNING_KEYS.filter((w) => {
    if (typeof w.rcCheck === 'function' && w.rcCheck(allRcs)) return true;
    if (typeof w.customCheck === 'function'
        && w.customCheck({ divergenceFlags, underAction })) return true;
    return false;
  });

  return (
    <Card
      data-testid={testId}
      className="border-slate-700/60 bg-slate-900/60 backdrop-blur-sm"
    >
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-base text-slate-100">
          <span className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-sky-300" aria-hidden />
            <span>Distribución y colas</span>
          </span>
          <Badge
            className={`border ${actionTone}`}
            data-testid={`${testId}-action-badge`}
          >
            {actionLabel}
          </Badge>
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Warning badges inline */}
        {activeWarnings.length > 0 && (
          <div
            className="flex flex-wrap gap-2"
            data-testid={`${testId}-warnings`}
          >
            {activeWarnings.map(({ id, label, icon: Icon, tone }) => (
              <Badge
                key={id}
                className={`border ${tone} flex items-center gap-1.5 text-xs`}
                data-testid={`${testId}-warning-${id}`}
              >
                <Icon className="h-3 w-3" aria-hidden />
                <span>{label}</span>
              </Badge>
            ))}
          </div>
        )}

        {/* Distribution + weights */}
        <div className="grid grid-cols-2 gap-3">
          <div
            className="rounded-md border border-slate-700/50 bg-slate-800/40 p-3"
            data-testid={`${testId}-distribution`}
          >
            <div className="mb-1 flex items-center gap-1.5 text-xs uppercase tracking-wide text-slate-400">
              <Layers className="h-3 w-3" aria-hidden />
              <span>Distribución</span>
            </div>
            <div className="text-sm font-semibold text-slate-100" data-testid={`${testId}-distribution-label`}>
              {distLabel}
            </div>
            <div className="mt-1 text-xs text-slate-400" data-testid={`${testId}-weights`}>
              Poisson&nbsp;{poissonWeight === null ? '—' : fmtPct(poissonWeight)}
              <span className="mx-1">·</span>
              NB&nbsp;{nbWeight === null ? '—' : fmtPct(nbWeight)}
            </div>
          </div>

          <div
            className="rounded-md border border-slate-700/50 bg-slate-800/40 p-3"
            data-testid={`${testId}-dispersion`}
          >
            <div className="mb-1 flex items-center gap-1.5 text-xs uppercase tracking-wide text-slate-400">
              <Gauge className="h-3 w-3" aria-hidden />
              <span>Dispersión efectiva</span>
            </div>
            <div className="text-sm font-semibold text-slate-100">
              {dispersion === null ? '—' : `×${fmtNum(dispersion, 2)}`}
            </div>
            <div className="mt-1 text-xs text-slate-400">
              Bucket:&nbsp;
              <span
                className={`inline-block rounded border px-1.5 py-0.5 text-[10px] ${tailToneCls}`}
                data-testid={`${testId}-tail-bucket`}
              >
                {tailLabel}
              </span>
            </div>
          </div>
        </div>

        {/* Percentiles */}
        <div
          className="rounded-md border border-slate-700/50 bg-slate-800/40 p-3"
          data-testid={`${testId}-percentiles`}
        >
          <div className="mb-2 text-xs uppercase tracking-wide text-slate-400">
            Percentiles del total (post-calibración)
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <div className="text-[10px] uppercase text-slate-500">P90</div>
              <div className="text-sm font-semibold text-slate-100" data-testid={`${testId}-p90`}>
                {fmtNum(p90, 2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-slate-500">P95</div>
              <div className="text-sm font-semibold text-slate-100" data-testid={`${testId}-p95`}>
                {fmtNum(p95, 2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase text-slate-500">P99</div>
              <div className="text-sm font-semibold text-slate-100" data-testid={`${testId}-p99`}>
                {fmtNum(p99, 2)}
              </div>
            </div>
          </div>
        </div>

        {/* Over risk + line selected */}
        <div
          className="rounded-md border border-slate-700/50 bg-slate-800/40 p-3"
          data-testid={`${testId}-over-risk`}
        >
          <div className="mb-1 flex items-center justify-between text-xs uppercase tracking-wide text-slate-400">
            <span>Riesgo de Over</span>
            <span data-testid={`${testId}-line`}>línea {fmtLine(lineUsed)}</span>
          </div>
          <div className="text-lg font-bold text-slate-100" data-testid={`${testId}-over-risk-value`}>
            {fmtPct(overRisk)}
          </div>
        </div>

        {/* Final action */}
        {underAction !== 'NONE' && (
          <div
            className={`rounded-md border p-3 ${actionTone}`}
            data-testid={`${testId}-final-action`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                <span>Acción aplicada</span>
              </div>
              <Badge className={`border ${actionTone}`}>{actionLabel}</Badge>
            </div>
            {Array.isArray(underHardRules?.signals) && underHardRules.signals.length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
                {underHardRules.signals.map((sig, i) => (
                  <li key={i} data-testid={`${testId}-signal-${i}`}>{sig}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default UnderDistributionTailsCard;
