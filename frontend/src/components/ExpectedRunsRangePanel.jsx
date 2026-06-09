/**
 * ExpectedRunsRangePanel — Priority 4.
 *
 * Renders the full Expected Runs distribution (mean / median / p10-p90)
 * plus the recommended value / protected / ultra-safe lines.
 *
 * Observe-only: this panel NEVER mutates the engine pick. It exists to
 * help the user choose a safer line variant of the SAME side (Under
 * stays Under, Over stays Over).
 *
 * Backend contract:
 *   match.expected_runs_distribution = {
 *     available, distribution, mean, median, p10, p25, p75, p90,
 *     uncertainty_bucket, probabilities, protected_lines,
 *     side, explanation_es, reason_codes, ...
 *   }
 */
import { useState } from 'react';
import { ChevronDown, ChevronUp, GitCommitHorizontal, ShieldCheck } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

const BUCKET_LABELS = {
  LOW:    { es: 'Baja',  en: 'Low'    },
  MEDIUM: { es: 'Media', en: 'Medium' },
  HIGH:   { es: 'Alta',  en: 'High'   },
};

const BUCKET_TONE = {
  LOW:    'border-emerald-500/30 bg-emerald-500/5 text-emerald-200',
  MEDIUM: 'border-cyan-500/30    bg-cyan-500/5    text-cyan-200',
  HIGH:   'border-amber-500/40   bg-amber-500/10  text-amber-200',
};

function fmt(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

export function ExpectedRunsRangePanel({ data, lang = 'es', testId }) {
  const [open, setOpen] = useState(false);
  if (!data || !data.available) return null;

  const bucket = data.uncertainty_bucket || 'MEDIUM';
  const tone = BUCKET_TONE[bucket] || BUCKET_TONE.MEDIUM;
  const bucketLabel = (BUCKET_LABELS[bucket] || {})[lang]
                      || (BUCKET_LABELS[bucket] || {}).es
                      || bucket;

  const protectedLines = data.protected_lines || {};
  const probs = data.probabilities || {};
  // Probability keys we want to show inline (Under / Over for the
  // engine's chosen side).
  const isUnder = data.side === 'under';
  const headlineLines = isUnder
    ? ['under_7_5', 'under_8_5', 'under_9_5', 'under_10_5', 'under_11_5']
    : ['over_7_5', 'over_8_5', 'over_9_5', 'over_10_5'];

  return (
    <section
      className={`rounded-md border ${tone} px-2.5 py-2 space-y-1.5`}
      data-testid={testId || 'expected-runs-range-panel'}
      data-bucket={bucket}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 text-left"
        data-testid={`${testId || 'expected-runs-range-panel'}-toggle`}
      >
        <div className="flex items-center gap-1.5 text-[10.5px] font-semibold">
          <GitCommitHorizontal className="h-3 w-3" />
          <span>
            {lang === 'en' ? 'Probable runs range' : 'Rango probable de carreras'}
          </span>
          <Badge variant="outline" className="text-[8.5px] py-0 px-1 ml-0.5">
            {lang === 'en' ? 'Uncertainty' : 'Incertidumbre'}: {bucketLabel}
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-[10px] opacity-85">
          <span className="font-mono">μ {fmt(data.mean)}</span>
          <span className="font-mono opacity-70">
            {fmt(data.p25, 0)}–{fmt(data.p75, 0)}
          </span>
          {open ? <ChevronUp className="h-3 w-3 opacity-70" />
                : <ChevronDown className="h-3 w-3 opacity-70" />}
        </div>
      </button>

      {open && (
        <div className="space-y-2 pt-0.5" data-testid={`${testId || 'expected-runs-range-panel'}-content`}>
          {/* Stat strip */}
          <div className="grid grid-cols-5 gap-1.5 text-center">
            <Stat label={lang === 'en' ? 'p10' : 'p10'} value={fmt(data.p10, 0)} />
            <Stat label={lang === 'en' ? 'p25' : 'p25'} value={fmt(data.p25, 0)} />
            <Stat label={lang === 'en' ? 'Median' : 'Mediana'} value={fmt(data.median, 1)} highlight />
            <Stat label={lang === 'en' ? 'p75' : 'p75'} value={fmt(data.p75, 0)} />
            <Stat label={lang === 'en' ? 'p90' : 'p90'} value={fmt(data.p90, 0)} />
          </div>

          {/* Distribution band visualization */}
          <RangeBar
            p10={data.p10}
            p25={data.p25}
            mean={data.mean}
            p75={data.p75}
            p90={data.p90}
          />

          {/* Probability rows for headline lines */}
          {Object.keys(probs).length > 0 && (
            <div className="space-y-0.5">
              {headlineLines.map((k) => {
                const v = probs[k];
                if (v == null) return null;
                const line = k.replace(/^(under|over)_/, '').replace('_', '.');
                const side = k.startsWith('under') ? (lang === 'en' ? 'Under' : 'Under')
                                                   : (lang === 'en' ? 'Over'  : 'Over');
                const pct = Number(v);
                const tonePct = pct >= 0.60 ? 'text-emerald-300'
                              : pct >= 0.45 ? 'text-cyan-300'
                              : 'text-amber-300';
                return (
                  <div
                    key={k}
                    className="flex items-center justify-between text-[10.5px]"
                    data-testid={`${testId || 'expected-runs-range-panel'}-prob-${k}`}
                  >
                    <span className="opacity-90">{side} {line}</span>
                    <span className={`font-mono ${tonePct}`}>{fmtPct(v)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Protected line suggestion */}
          {(protectedLines.value_line || protectedLines.protected_line) && (
            <div
              className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-1.5 space-y-0.5"
              data-testid={`${testId || 'expected-runs-range-panel'}-protected`}
            >
              <div className="flex items-center gap-1.5 text-[10.5px] font-semibold text-cyan-100">
                <ShieldCheck className="h-3 w-3" />
                {lang === 'en' ? 'Line suggestions' : 'Sugerencia de líneas'}
              </div>
              <div className="text-[10px] grid grid-cols-3 gap-1">
                <LinePill
                  label={lang === 'en' ? 'Value' : 'Valor'}
                  value={protectedLines.value_line}
                />
                <LinePill
                  label={lang === 'en' ? 'Protected' : 'Protegida'}
                  value={protectedLines.protected_line}
                  tone="protected"
                />
                <LinePill
                  label={lang === 'en' ? 'Ultra-safe' : 'Ultra-segura'}
                  value={protectedLines.ultra_safe_line}
                  tone="ultra"
                />
              </div>
            </div>
          )}

          {/* Spanish explanation */}
          {data.explanation_es && (
            <p
              className="text-[10px] opacity-90 leading-snug"
              data-testid={`${testId || 'expected-runs-range-panel'}-explanation`}
            >
              {data.explanation_es}
            </p>
          )}

          {/* Distribution metadata */}
          <div className="flex items-center justify-between text-[9.5px] opacity-60 pt-1">
            <span>
              {lang === 'en' ? 'Distribution' : 'Distribución'}: {data.distribution || '—'}
            </span>
            <span className="font-mono">
              {lang === 'en' ? 'Dispersion' : 'Dispersión'} ×{fmt(data.effective_dispersion_ratio, 2)}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, highlight = false }) {
  return (
    <div className="rounded border border-border bg-card/40 px-1 py-1">
      <div className="text-[8.5px] uppercase tracking-wider opacity-70">{label}</div>
      <div className={`text-[12px] font-mono leading-tight ${highlight ? 'font-semibold' : ''}`}>
        {value}
      </div>
    </div>
  );
}

function LinePill({ label, value, tone = 'value' }) {
  if (!value) return <div className="opacity-50 text-center">—</div>;
  const cls = tone === 'protected'
    ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200'
    : tone === 'ultra'
    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
    : 'border-border bg-card/60 text-foreground';
  return (
    <div className={`rounded border ${cls} px-1.5 py-1`}>
      <div className="text-[8.5px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-[11px] font-mono leading-tight font-semibold">{value}</div>
    </div>
  );
}

/**
 * RangeBar — visual band of the p10..p90 range with mean marker.
 * Uses CSS positioning so it's deterministic across rerenders.
 */
function RangeBar({ p10, p25, mean, p75, p90 }) {
  if ([p10, p25, mean, p75, p90].some((v) => v == null)) return null;
  const lo = Number(p10);
  const hi = Number(p90);
  if (hi <= lo) return null;
  const range = hi - lo;
  const pct = (v) => `${Math.max(0, Math.min(100, ((Number(v) - lo) / range) * 100))}%`;

  return (
    <div className="relative h-3 w-full rounded-full bg-card/40 border border-border overflow-hidden">
      {/* p25..p75 inner band */}
      <div
        className="absolute top-0 bottom-0 bg-cyan-500/25"
        style={{ left: pct(p25), width: `calc(${pct(p75)} - ${pct(p25)})` }}
      />
      {/* mean marker */}
      <div
        className="absolute top-0 bottom-0 w-[2px] bg-amber-300"
        style={{ left: pct(mean) }}
      />
    </div>
  );
}

export default ExpectedRunsRangePanel;
