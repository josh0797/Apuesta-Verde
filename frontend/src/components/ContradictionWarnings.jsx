import { AlertTriangle, Info, AlertOctagon } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * ContradictionWarnings — banner of detected contradictions between
 * independent signals (visual confidence vs historical pattern, market vs
 * match state, etc.).
 *
 * Severities follow a clear contract:
 *   - critical (rose): the engine recommends pivoting or skipping the pick.
 *   - warn (amber):    the engine flags an inconsistency worth reviewing.
 *   - info (cyan):     contextual nuance, not blocking.
 *
 * IMPORTANT: contradictions are an OBSERVATION layer. We never auto-discard
 * picks here. The user keeps full authority over the final call.
 */

const SEVERITY_META = {
  critical: {
    tone: 'rose',
    icon: AlertOctagon,
    label_es: 'Crítico',
    label_en: 'Critical',
    cardCls: 'border-rose-500/40 bg-rose-500/[0.06] text-rose-100',
    iconCls: 'text-rose-300',
  },
  warn: {
    tone: 'amber',
    icon: AlertTriangle,
    label_es: 'Atención',
    label_en: 'Caution',
    cardCls: 'border-amber-500/40 bg-amber-500/[0.06] text-amber-100',
    iconCls: 'text-amber-300',
  },
  info: {
    tone: 'cyan',
    icon: Info,
    label_es: 'Contexto',
    label_en: 'Context',
    cardCls: 'border-cyan-500/30 bg-cyan-500/[0.05] text-cyan-100',
    iconCls: 'text-cyan-300',
  },
};

export function ContradictionWarnings({ contradictions }) {
  const { lang } = useI18n();
  if (!Array.isArray(contradictions) || contradictions.length === 0) {
    return null;
  }

  // Sort: critical first, then warn, then info
  const order = { critical: 0, warn: 1, info: 2 };
  const sorted = [...contradictions].sort(
    (a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9),
  );

  const heading = lang === 'en' ? 'Signal contradictions' : 'Contradicciones de señales';
  const subline = lang === 'en'
    ? 'Independent indicators that disagree — review before acting.'
    : 'Indicadores independientes que no concuerdan — revisar antes de actuar.';

  return (
    <div className="space-y-2" data-testid="contradiction-warnings">
      <div className="flex items-baseline justify-between gap-2">
        <div className="micro-label flex items-center gap-1.5">
          <AlertTriangle className="h-3 w-3" />
          {heading}
          <span className="font-mono-tabular bg-background/30 px-1.5 rounded text-[10px] ml-1" data-testid="contradictions-count">
            {sorted.length}
          </span>
        </div>
        <span className="text-[10.5px] text-muted-foreground italic hidden md:inline">{subline}</span>
      </div>

      <div className="space-y-1.5">
        {sorted.map((c) => {
          const meta = SEVERITY_META[c.severity] || SEVERITY_META.info;
          const Icon = meta.icon;
          const title = lang === 'en' ? c.title_en : c.title_es;
          const detail = lang === 'en' ? c.detail_en : c.detail_es;
          return (
            <TooltipProvider key={c.id} delayDuration={140}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div
                    className={`flex items-start gap-2.5 rounded-md border p-2.5 cursor-help ${meta.cardCls}`}
                    data-testid={`contradiction-${c.id}`}
                  >
                    <Icon className={`h-4 w-4 mt-0.5 shrink-0 ${meta.iconCls}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[12.5px] font-semibold leading-tight">{title}</span>
                        <span className={`text-[9.5px] uppercase tracking-wider font-mono-tabular px-1.5 py-0.5 rounded ${meta.iconCls} bg-background/40`}>
                          {lang === 'en' ? meta.label_en : meta.label_es}
                        </span>
                      </div>
                      <p className="text-[11.5px] opacity-85 leading-snug mt-0.5" data-testid={`contradiction-detail-${c.id}`}>
                        {detail}
                      </p>
                    </div>
                  </div>
                </TooltipTrigger>
                <TooltipContent className="glass-surface text-xs max-w-[280px] leading-relaxed">
                  {lang === 'en'
                    ? 'Observation only — the system never auto-overrides the analyst score.'
                    : 'Solo observación — el sistema nunca sobreescribe la puntuación del analista.'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          );
        })}
      </div>
    </div>
  );
}
