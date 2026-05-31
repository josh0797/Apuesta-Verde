import { TreePine, Plus, Minus, Equal, Sigma, ShieldAlert, AlertTriangle } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { deriveConfidenceBreakdown } from '@/lib/intelligence';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * ConfidenceBreakdown — visual decomposition of confidence_score.
 *
 * Shows each contributing factor as a horizontal signed bar (positive bars
 * grow to the right of the baseline, negative bars to the left).
 *
 * Reconciliation row at the bottom shows the baseline (50) + sum + final score.
 * If LLM-reported score differs from the computed sum, a small "Engine
 * reconciliation" badge surfaces the gap — useful to inspect "hidden risks".
 *
 * GAP #2 — When the LLM is more than 10 pts above the factor sum, we
 * actively PENALIZE the score (anchor it to factor_sum + min(15, gap/2))
 * and surface a LLM_OVERCONFIDENT badge so the user sees the adjustment.
 */
const ITEM_LABELS = {
  motivation:       { es: 'Motivación',         en: 'Motivation' },
  form:             { es: 'Forma reciente',     en: 'Recent form' },
  'home-advantage': { es: 'Localía',            en: 'Home advantage' },
  absences:         { es: 'Bajas',              en: 'Injuries' },
  volatility:       { es: 'Volatilidad',        en: 'Volatility' },
  'risk-flags':     { es: 'Banderas de riesgo', en: 'Risk flags' },
};

function SignedBar({ delta, max = 25 }) {
  // Visual half-width so positive and negative bars share the same axis center.
  const abs = Math.min(Math.abs(delta), max);
  const pct = (abs / max) * 100;
  const positive = delta >= 0;
  return (
    <div className="relative h-1.5 w-full bg-secondary/40 rounded">
      <div className="absolute top-0 bottom-0 left-1/2 w-px bg-border" />
      <div
        className={`absolute top-0 bottom-0 rounded ${positive ? 'bg-emerald-400/70' : 'bg-rose-400/60'}`}
        style={{
          left: positive ? '50%' : `${50 - pct / 2}%`,
          width: `${pct / 2}%`,
          transition: 'width 220ms ease, left 220ms ease',
        }}
      />
    </div>
  );
}

export function ConfidenceBreakdown({ pick, lang: explicitLang }) {
  const ctx = useI18n();
  const lang = explicitLang || ctx.lang || 'es';
  const data = deriveConfidenceBreakdown(pick);
  if (!data || data.items.length === 0) return null;

  const t = lang === 'en'
    ? {
        header: 'Confidence breakdown',
        baseline: 'Baseline',
        reconciliation: 'Engine reconciliation',
        reportedTotal: 'LLM reported',
        computed: 'Sum of factors',
        finalScore: 'Adjusted score',
        overconfident: 'LLM OVERCONFIDENT',
        underconfident: 'LLM UNDERCONFIDENT',
        anchorHint: (g, pen, fs) =>
          `LLM is ${g} pts more optimistic than the data. Score anchored to the factor sum (+${pen} → ${fs}).`,
        riskHint: (g) =>
          `LLM is ${g} pts more conservative — hidden risk the engine weighed in.`,
        balancedHint: 'LLM and factor sum are aligned (gap within ±10).',
      }
    : {
        header: 'Desglose de confianza',
        baseline: 'Base',
        reconciliation: 'Reconciliación del motor',
        reportedTotal: 'LLM reportó',
        computed: 'Suma de factores',
        finalScore: 'Score ajustado',
        overconfident: 'LLM SOBRECONFIANTE',
        underconfident: 'LLM SUBCONFIANTE',
        anchorHint: (g, pen, fs) =>
          `El LLM es ${g} pts más optimista que los datos. Score anclado a la suma de factores (+${pen} → ${fs}).`,
        riskHint: (g) =>
          `El LLM es ${g} pts más conservador — riesgo oculto que el motor detectó.`,
        balancedHint: 'LLM y suma de factores alineados (gap dentro de ±10).',
      };

  const isOverconfident  = data.reconciliation_label === 'LLM_OVERCONFIDENT';
  const isUnderconfident = data.reconciliation_label === 'LLM_UNDERCONFIDENT';
  const showReconciliation = data.gap !== 0;

  return (
    <div className="space-y-2" data-testid="confidence-breakdown">
      <div className="micro-label flex items-center gap-1.5">
        <TreePine className="h-3 w-3" />
        {t.header}
      </div>

      <ul className="space-y-1.5">
        <li className="flex items-center gap-3" data-testid="breakdown-baseline">
          <div className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground w-[38%] shrink-0">
            <Equal className="h-3 w-3" />
            <span>{t.baseline}</span>
          </div>
          <SignedBar delta={0} />
          <div className="w-[44px] text-right">
            <span className="font-mono-tabular text-[11.5px] text-muted-foreground">{data.baseline}</span>
          </div>
        </li>
        {data.items.map((it) => {
          const label = (ITEM_LABELS[it.key] || {})[lang] || it.key;
          const detail = lang === 'en' ? it.detail_en : it.detail_es;
          const Icon = it.delta > 0 ? Plus : it.delta < 0 ? Minus : Equal;
          const tone = it.delta > 0 ? 'text-emerald-200' : it.delta < 0 ? 'text-rose-200' : 'text-muted-foreground';
          return (
            <li key={it.key} className="flex items-center gap-3" data-testid={`breakdown-${it.key}`}>
              <TooltipProvider delayDuration={120}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center gap-1.5 w-[38%] shrink-0 cursor-help">
                      <Icon className={`h-3 w-3 ${tone}`} />
                      <span className="text-[12px] text-foreground truncate">{label}</span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent className="glass-surface text-xs max-w-[260px]">{detail}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              <SignedBar delta={it.delta} />
              <div className="w-[44px] text-right">
                <span className={`font-mono-tabular text-[11.5px] font-semibold ${tone}`}>
                  {it.delta > 0 ? '+' : ''}{it.delta}
                </span>
              </div>
            </li>
          );
        })}
        <li className="flex items-center gap-3 pt-1.5 border-t border-border/40" data-testid="breakdown-computed">
          <div className="flex items-center gap-1.5 text-[11.5px] text-foreground w-[38%] shrink-0">
            <Sigma className="h-3 w-3" />
            <span className="font-medium">{t.computed}</span>
          </div>
          <div className="flex-1" />
          <div className="w-[44px] text-right">
            <span className="font-mono-tabular text-[12px] font-semibold">{data.computed_total}</span>
          </div>
        </li>
      </ul>

      {showReconciliation && (
        <div
          className={`text-[11px] mt-1 px-2 py-1.5 rounded-md border flex items-start gap-1.5 ${
            isOverconfident
              ? 'border-rose-500/45 bg-rose-500/10 text-rose-100'
              : isUnderconfident
                ? 'border-amber-500/40 bg-amber-500/8 text-amber-100'
                : 'border-cyan-500/20 bg-cyan-500/5'
          }`}
          data-testid="breakdown-reconciliation"
        >
          {isOverconfident ? (
            <ShieldAlert className="h-3.5 w-3.5 mt-0.5 shrink-0 text-rose-300" />
          ) : isUnderconfident ? (
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-amber-300" />
          ) : (
            <Sigma className="h-3 w-3 mt-0.5 shrink-0 text-cyan-300" />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span
                className={`text-[10px] uppercase tracking-wide font-semibold ${
                  isOverconfident
                    ? 'text-rose-200'
                    : isUnderconfident
                      ? 'text-amber-200'
                      : 'text-cyan-300/80'
                }`}
              >
                {isOverconfident ? t.overconfident : isUnderconfident ? t.underconfident : t.reconciliation}
              </span>
              {isOverconfident && (
                <span
                  className="ml-auto text-[10px] font-mono-tabular px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-100 border border-rose-500/40"
                  data-testid="breakdown-final-score"
                >
                  {t.finalScore}: <span className="font-bold">{data.final_score}</span>
                </span>
              )}
            </div>
            <div className="text-muted-foreground leading-snug mt-0.5">
              {isOverconfident
                ? t.anchorHint(data.gap, data.penalty, data.final_score)
                : isUnderconfident
                  ? t.riskHint(Math.abs(data.gap))
                  : t.balancedHint}
              {' '}
              <span className="text-foreground font-mono-tabular">
                ({t.reportedTotal}: {data.reported} · {t.computed}: {data.computed_total})
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
