/**
 * TailRiskPanel — Tail Risk + Hidden Over Routes display.
 *
 * Collapsible card that surfaces:
 *   • Under / Over probability at the engine's chosen market line.
 *   • Tail probabilities: P(>= 12), P(>= 14), P(>= 16) runs.
 *   • Tail bucket badge (LOW/MEDIUM/HIGH/EXTREME).
 *   • Under-quality verdict (clean vs. mean-supported-tail-fragile, etc.)
 *   • Adjusted fragility "20 → 34" when the fragility calibrator fired,
 *     including the hidden-Over-route narrative.
 *   • Phase 55 — TAIL FRAGILITY sub-block with explosive_tail_score,
 *     bucket, base/interaction/total adjustment, drivers, narrative.
 *
 * Observe-only: this panel NEVER mutates the engine pick polarity.
 *
 * Backend contracts:
 *   match.tail_risk             = compute_tail_risk(...) output
 *   match.tail_fragility        = compute_tail_fragility(...) output (Phase 55)
 *   match.market_profile        = interpret_market_profile(...) output
 *   match.fragility_calibration = calibrate_fragility(...) output
 */
import { useState } from 'react';
import {
  ChevronDown, ChevronUp, Flame, ShieldAlert, AlertTriangle, Zap,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';

const TAIL_BUCKET_LABELS = {
  LOW:     { es: 'Baja',    en: 'Low'     },
  MEDIUM:  { es: 'Media',   en: 'Medium'  },
  HIGH:    { es: 'Alta',    en: 'High'    },
  EXTREME: { es: 'Extrema', en: 'Extreme' },
};

const TAIL_BUCKET_TONE = {
  LOW:     'border-emerald-500/30 bg-emerald-500/5  text-emerald-200',
  MEDIUM:  'border-cyan-500/30    bg-cyan-500/5     text-cyan-200',
  HIGH:    'border-amber-500/40   bg-amber-500/10   text-amber-200',
  EXTREME: 'border-red-500/40     bg-red-500/10     text-red-200',
};

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

export function TailRiskPanel({
  tailRisk,
  tailFragility,           // Phase 55 payload
  fragilityCalibration,
  marketProfile,
  lang = 'es',
  testId,
}) {
  const [open, setOpen] = useState(false);
  if (!tailRisk || !tailRisk.available) {
    // If only fragility calibration is available, still show the fragility row
    if (!fragilityCalibration || !fragilityCalibration.available) return null;
  }

  const bucket = (tailRisk && tailRisk.tail_bucket) || 'LOW';
  const tone = TAIL_BUCKET_TONE[bucket] || TAIL_BUCKET_TONE.LOW;
  const bucketLabel = (TAIL_BUCKET_LABELS[bucket] || {})[lang]
                      || (TAIL_BUCKET_LABELS[bucket] || {}).es
                      || bucket;

  const tr = tailRisk || {};
  const fc = fragilityCalibration || {};
  const mp = marketProfile || {};

  const fragilityChanged = fc.available && Number(fc.delta || 0) > 0;
  const fragilityLine = fragilityChanged
    ? `${fc.base_fragility} → ${fc.adjusted_fragility}`
    : null;

  return (
    <section
      className={`rounded-md border ${tone} px-2.5 py-2 space-y-1.5`}
      data-testid={testId || 'tail-risk-panel'}
      data-tail-bucket={bucket}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 text-left"
        data-testid={`${testId || 'tail-risk-panel'}-toggle`}
      >
        <div className="flex items-center gap-1.5 text-[10.5px] font-semibold">
          <Flame className="h-3 w-3" />
          <span>
            {lang === 'en' ? 'Explosive tail risk' : 'Riesgo de cola explosiva'}
          </span>
          <Badge variant="outline" className="text-[8.5px] py-0 px-1 ml-0.5">
            {bucketLabel}
          </Badge>
          {fragilityChanged && (
            <Badge
              variant="outline"
              className="text-[8.5px] py-0 px-1 bg-amber-500/10 border-amber-500/40 text-amber-200"
              data-testid={`${testId || 'tail-risk-panel'}-fragility-pill`}
            >
              <ShieldAlert className="h-2.5 w-2.5 mr-0.5" />
              Fragility {fragilityLine}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2 text-[10px] opacity-85">
          {tr.under_probability != null && (
            <span className="font-mono">
              U {fmtPct(tr.under_probability)}
            </span>
          )}
          {open ? <ChevronUp className="h-3 w-3 opacity-70" />
                : <ChevronDown className="h-3 w-3 opacity-70" />}
        </div>
      </button>

      {open && (
        <div className="space-y-2 pt-0.5" data-testid={`${testId || 'tail-risk-panel'}-content`}>
          {/* Under / Over headline */}
          {tr.market_line != null && (
            <div className="grid grid-cols-2 gap-1.5">
              <ProbRow
                label={`${lang === 'en' ? 'Under' : 'Under'} ${tr.market_line}`}
                value={tr.under_probability}
                tone="under"
                testId={`${testId || 'tail-risk-panel'}-under`}
              />
              <ProbRow
                label={`${lang === 'en' ? 'Over' : 'Over'} ${tr.market_line}`}
                value={tr.over_probability}
                tone="over"
                testId={`${testId || 'tail-risk-panel'}-over`}
              />
            </div>
          )}

          {/* Tail probabilities */}
          <div className="grid grid-cols-3 gap-1.5">
            <ProbRow
              label={lang === 'en' ? '12+ runs' : '12+ carreras'}
              value={tr.p_ge_12}
              tone="tail"
              testId={`${testId || 'tail-risk-panel'}-p12`}
            />
            <ProbRow
              label={lang === 'en' ? '14+ runs' : '14+ carreras'}
              value={tr.p_ge_14}
              tone="tail"
              testId={`${testId || 'tail-risk-panel'}-p14`}
            />
            <ProbRow
              label={lang === 'en' ? '16+ runs' : '16+ carreras'}
              value={tr.p_ge_16}
              tone="tail"
              testId={`${testId || 'tail-risk-panel'}-p16`}
            />
            {tr.p_ge_18 != null && (
              <ProbRow
                label={lang === 'en' ? '18+ runs' : '18+ carreras'}
                value={tr.p_ge_18}
                tone="tail"
                testId={`${testId || 'tail-risk-panel'}-p18`}
              />
            )}
          </div>

          {/* Tail-risk interpretation */}
          {(tr.interpretation_es || mp.headline_es) && (
            <p
              className="text-[10px] leading-snug opacity-90 border-l-2 border-current/40 pl-2"
              data-testid={`${testId || 'tail-risk-panel'}-interpretation`}
            >
              {tr.interpretation_es || mp.headline_es}
            </p>
          )}

          {/* Phase 55 — TAIL FRAGILITY sub-block. Surfaces the
              explosive_tail_score, bucket, base + interaction deltas,
              and the Spanish narrative. Observe-only. */}
          {tailFragility && tailFragility.available && (
            <TailFragilitySubBlock
              tf={tailFragility}
              lang={lang}
              testId={`${testId || 'tail-risk-panel'}-fragility-engine`}
            />
          )}

          {/* Fragility calibration block (when fired) */}
          {fragilityChanged && (
            <div
              className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 space-y-1"
              data-testid={`${testId || 'tail-risk-panel'}-fragility-block`}
            >
              <div className="flex items-center justify-between text-[10.5px]">
                <span className="flex items-center gap-1.5 font-semibold text-amber-200">
                  <AlertTriangle className="h-3 w-3" />
                  {lang === 'en' ? 'Fragility' : 'Fragilidad'}
                </span>
                <span className="font-mono text-amber-100">
                  {fragilityLine}
                  <span className="opacity-60 ml-1">(+{fc.delta})</span>
                </span>
              </div>
              {fc.narrative_es && (
                <p className="text-[10px] opacity-90 leading-snug">
                  {fc.narrative_es}
                </p>
              )}
              {Array.isArray(fc.hidden_over_routes) && fc.hidden_over_routes.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {fc.hidden_over_routes.map((r) => (
                    <Badge
                      key={r}
                      variant="outline"
                      className="text-[8.5px] py-0 px-1 border-amber-500/40"
                      data-testid={`${testId || 'tail-risk-panel'}-route-${r}`}
                    >
                      {r.replaceAll('_', ' ').toLowerCase()}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ProbRow({ label, value, tone, testId }) {
  const v = Number(value ?? 0);
  let toneCls = 'text-foreground';
  if (tone === 'under') toneCls = v >= 0.6 ? 'text-emerald-300' : 'text-cyan-200';
  if (tone === 'over')  toneCls = v >= 0.5 ? 'text-amber-300'  : 'text-muted-foreground';
  if (tone === 'tail')  toneCls = v >= 0.20 ? 'text-red-300'
                                : v >= 0.12 ? 'text-amber-300'
                                : 'text-emerald-300';
  return (
    <div
      className="rounded border border-border bg-card/40 px-2 py-1 flex items-center justify-between"
      data-testid={testId}
    >
      <span className="text-[10px] opacity-90 truncate">{label}</span>
      <span className={`text-[11px] font-mono font-semibold ${toneCls}`}>
        {fmtPct(value)}
      </span>
    </div>
  );
}

// ── Phase 55 — Tail Fragility sub-block ───────────────────────────────
const TF_BUCKET_TONE = {
  LOW:     'border-emerald-500/30 bg-emerald-500/5  text-emerald-200',
  MEDIUM:  'border-cyan-500/30    bg-cyan-500/5     text-cyan-200',
  HIGH:    'border-amber-500/40   bg-amber-500/10   text-amber-200',
  EXTREME: 'border-red-500/40     bg-red-500/10     text-red-200',
};
const TF_BUCKET_LABELS = {
  LOW:     { es: 'Bajo',     en: 'Low'     },
  MEDIUM:  { es: 'Medio',    en: 'Medium'  },
  HIGH:    { es: 'Alto',     en: 'High'    },
  EXTREME: { es: 'Extremo',  en: 'Extreme' },
};

function TailFragilitySubBlock({ tf, lang = 'es', testId }) {
  const bucket = tf.tail_bucket || 'LOW';
  const tone   = TF_BUCKET_TONE[bucket] || TF_BUCKET_TONE.LOW;
  const bucketLabel = (TF_BUCKET_LABELS[bucket] || {})[lang]
                      || (TF_BUCKET_LABELS[bucket] || {}).es
                      || bucket;

  const score             = tf.explosive_tail_score;
  const baseAdj           = tf.base_adjustment ?? 0;
  const interactionTotal  = tf.interaction_total ?? 0;
  const totalAdj          = tf.total_adjustment ?? 0;
  const capHit            = Boolean(tf.cap_hit);
  const interactions      = Array.isArray(tf.interactions) ? tf.interactions : [];
  // FIX-3 — Polarity guard signal. When the weighted score would have
  // bucketed LOW but the explicit probability tail forced an
  // escalation, surface the explanation prominently so the user does
  // not see a contradictory "Alta / Bajo" pair.
  const reasonCodes  = Array.isArray(tf.reason_codes) ? tf.reason_codes : [];
  const escalatedByExplosive = reasonCodes.includes(
    'TAIL_FRAGILITY_ESCALATED_BY_EXPLOSIVE_TAIL',
  );

  return (
    <div
      className={`rounded-md border ${tone} px-2 py-1.5 space-y-1.5`}
      data-testid={testId}
      data-bucket={bucket}
    >
      {/* Header line */}
      <div className="flex items-center justify-between gap-2 text-[10.5px]">
        <span className="flex items-center gap-1.5 font-semibold">
          <Zap className="h-3 w-3" />
          {lang === 'en' ? 'TAIL FRAGILITY' : 'TAIL FRAGILITY'}
        </span>
        <div className="flex items-center gap-1.5">
          <Badge
            variant="outline"
            className={`text-[9px] py-0 px-1 ${tone}`}
            data-testid={`${testId}-bucket`}
          >
            {bucketLabel}
          </Badge>
          <span
            className="font-mono text-[10.5px]"
            data-testid={`${testId}-score`}
          >
            {score != null ? `${score}/100` : '—/100'}
          </span>
        </div>
      </div>

      {/* Adjustment breakdown */}
      <div className="grid grid-cols-3 gap-1.5">
        <AdjustmentCell
          label={lang === 'en' ? 'Base' : 'Base'}
          value={baseAdj}
          testId={`${testId}-adj-base`}
        />
        <AdjustmentCell
          label={lang === 'en' ? 'Interactions' : 'Interacciones'}
          value={interactionTotal}
          testId={`${testId}-adj-interactions`}
        />
        <AdjustmentCell
          label={lang === 'en' ? 'Total' : 'Total'}
          value={totalAdj}
          highlight
          capHit={capHit}
          testId={`${testId}-adj-total`}
        />
      </div>

      {/* Active interaction modifiers */}
      {interactions.length > 0 && (
        <div
          className="flex flex-wrap gap-1 pt-0.5"
          data-testid={`${testId}-interactions`}
        >
          {interactions.map((i) => (
            <Badge
              key={i.code}
              variant="outline"
              className="text-[8.5px] py-0 px-1 border-current/40"
              data-testid={`${testId}-int-${i.code}`}
            >
              {(i.label || i.code.replaceAll('_', ' ').toLowerCase())} +{i.delta}
            </Badge>
          ))}
        </div>
      )}

      {/* FIX-3 — Polarity-guard escalation notice. Shown when the
          internal weighted score buckets LOW but the explicit
          probability tail (P(12+)/P(14+)) or the external tail bucket
          forces a non-LOW Tail Fragility verdict. Without this the UI
          would simultaneously render "Riesgo de cola explosiva: Alta"
          and "Tail Fragility: Bajo". */}
      {escalatedByExplosive && (
        <div
          className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 flex items-start gap-1.5"
          data-testid={`${testId}-escalation-notice`}
        >
          <AlertTriangle className="h-3 w-3 text-amber-200 shrink-0 mt-0.5" />
          <p className="text-[10px] leading-snug text-amber-100">
            {lang === 'en'
              ? 'Tail Fragility escalated because the distribution assigns '
                + 'high probability to 12+ / 14+ runs scenarios.'
              : 'Tail Fragility escalado porque la distribución asigna alta '
                + 'probabilidad a escenarios de 12+ / 14+ carreras.'}
          </p>
        </div>
      )}

      {/* Narrative */}
      {tf.narrative_es && (
        <p
          className="text-[10px] leading-snug opacity-90 border-l-2 border-current/40 pl-2"
          data-testid={`${testId}-narrative`}
        >
          {tf.narrative_es}
        </p>
      )}
    </div>
  );
}

function AdjustmentCell({ label, value, highlight, capHit, testId }) {
  const cls = highlight
    ? `text-[11.5px] font-mono font-semibold ${capHit ? 'text-red-300' : 'text-foreground'}`
    : 'text-[11px] font-mono opacity-90';
  return (
    <div
      className="rounded border border-border bg-card/40 px-2 py-1"
      data-testid={testId}
    >
      <p className="text-[9px] uppercase tracking-wide opacity-60">{label}</p>
      <p className={cls}>
        +{value}
        {capHit && <span className="ml-1 text-[9px] opacity-70">(cap)</span>}
      </p>
    </div>
  );
}

export default TailRiskPanel;
