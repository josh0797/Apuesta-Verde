import { useMemo } from 'react';
import { MessageCircle, ShieldOff, AlertTriangle, Check } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { buildEngineNarrative } from '@/lib/intelligence';

/**
 * EngineNarrativeBlock — three short professional bullets explaining the
 * engine's stance on the pick:
 *   • Engine says       — endorsements
 *   • Engine avoids     — vetoes
 *   • Engine is cautious because — conditional positives / risks
 *
 * Designed to read like a Bloomberg / equity research opening paragraph,
 * NOT a betting tipster pitch.
 */
export function EngineNarrativeBlock({ pick, intel, historicalSignal, contradictions }) {
  const { lang } = useI18n();
  const narrative = useMemo(
    () => buildEngineNarrative(pick, intel, historicalSignal, contradictions || []),
    [pick, intel, historicalSignal, contradictions],
  );

  if (!narrative) return null;
  const { says = [], avoids = [], cautious = [] } = narrative;
  if (!says.length && !avoids.length && !cautious.length) return null;

  const labels = lang === 'en'
    ? { header: 'Engine narrative', says: 'Engine says', avoids: 'Engine avoids', cautious: 'Engine is cautious because' }
    : { header: 'Narrativa del motor', says: 'El motor recomienda', avoids: 'El motor evita', cautious: 'El motor es cauto porque' };

  return (
    <div className="space-y-2" data-testid="engine-narrative-block">
      <div className="micro-label flex items-center gap-1.5">
        <MessageCircle className="h-3 w-3" />
        {labels.header}
      </div>
      <div className="grid md:grid-cols-3 gap-2">
        <NarrativeColumn
          icon={Check}
          title={labels.says}
          items={says}
          tone="emerald"
          lang={lang}
          emptyEs="Sin endoso explícito."
          emptyEn="No explicit endorsement."
          testId="narrative-says"
        />
        <NarrativeColumn
          icon={ShieldOff}
          title={labels.avoids}
          items={avoids}
          tone="rose"
          lang={lang}
          emptyEs="Sin vetos críticos."
          emptyEn="No critical vetoes."
          testId="narrative-avoids"
        />
        <NarrativeColumn
          icon={AlertTriangle}
          title={labels.cautious}
          items={cautious}
          tone="amber"
          lang={lang}
          emptyEs="Sin matices condicionales."
          emptyEn="No conditional caveats."
          testId="narrative-cautious"
        />
      </div>
    </div>
  );
}

function NarrativeColumn({ icon: Icon, title, items, tone, lang, emptyEs, emptyEn, testId }) {
  const cls = {
    emerald: 'border-emerald-500/30 bg-emerald-500/[0.05] text-emerald-100',
    rose: 'border-rose-500/30 bg-rose-500/[0.05] text-rose-100',
    amber: 'border-amber-500/30 bg-amber-500/[0.05] text-amber-100',
  }[tone];
  const iconCls = {
    emerald: 'text-emerald-300',
    rose: 'text-rose-300',
    amber: 'text-amber-300',
  }[tone];
  return (
    <div className={`rounded-lg border p-3 ${cls}`} data-testid={testId}>
      <div className="flex items-center gap-1.5 mb-2">
        <Icon className={`h-3.5 w-3.5 ${iconCls}`} />
        <span className="text-[10.5px] uppercase tracking-wider font-semibold opacity-90">{title}</span>
      </div>
      {items.length === 0 ? (
        <p className="text-[11.5px] italic opacity-70 leading-snug">
          {lang === 'en' ? emptyEn : emptyEs}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {items.slice(0, 4).map((it, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[12px] leading-snug">
              <span className={`h-1 w-1 rounded-full mt-1.5 shrink-0 ${iconCls.replace('text-', 'bg-')}`} />
              <span>{lang === 'en' ? it.en : it.es}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
