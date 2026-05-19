import { TreePine, Plus, Minus, Equal, Sigma } from 'lucide-react';
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
        gapHint: (g) => g === 0
          ? 'Factors fully explain the LLM score.'
          : g > 0
            ? `LLM is ${g} points more optimistic than the decomposed factors — possible hidden positive signal.`
            : `LLM is ${Math.abs(g)} points more conservative — possible hidden risk the engine weighed in.`,
      }
    : {
        header: 'Desglose de confianza',
        baseline: 'Base',
        reconciliation: 'Reconciliación del motor',
        reportedTotal: 'LLM reportó',
        computed: 'Suma de factores',
        gapHint: (g) => g === 0
          ? 'Los factores explican íntegramente el score del LLM.'
          : g > 0
            ? `El LLM es ${g} puntos más optimista que la descomposición — posible señal positiva oculta.`
            : `El LLM es ${Math.abs(g)} puntos más conservador — posible riesgo oculto detectado.`,
      };

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

      {data.gap !== 0 && (
        <div
          className="text-[11px] mt-1 px-2 py-1.5 rounded-md border border-cyan-500/20 bg-cyan-500/5 flex items-start gap-1.5"
          data-testid="breakdown-reconciliation"
        >
          <Sigma className="h-3 w-3 mt-0.5 shrink-0 text-cyan-300" />
          <div className="flex-1 min-w-0">
            <div className="text-[10.5px] uppercase tracking-wide text-cyan-300/80 font-medium">
              {t.reconciliation}
            </div>
            <div className="text-muted-foreground leading-snug">
              {t.gapHint(data.gap)} <span className="text-foreground font-mono-tabular">({t.reportedTotal}: {data.reported} · {t.computed}: {data.computed_total})</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
