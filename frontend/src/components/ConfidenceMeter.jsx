import { useI18n } from '@/lib/i18n';
import { Progress } from '@/components/ui/progress';
import { tierClass, confidenceTier } from '@/lib/format';

export function ConfidenceMeter({ score = 0, size = 'md', testId }) {
  const tier = confidenceTier(score);
  const { t } = useI18n();
  const cls = tierClass(tier);
  const label = tier === 'Below' ? '< 68' : t.confidence[tier] || tier;
  return (
    <div data-testid={testId || 'confidence-meter'} className={`rounded-lg border ${cls} p-3 flex items-center gap-3`}>
      <div className="flex flex-col">
        <span className="text-[10px] uppercase tracking-wide opacity-70">Confidence</span>
        <span className="mono font-mono-tabular text-2xl font-semibold leading-none">{Math.round(score)}</span>
      </div>
      <div className="flex-1 min-w-[120px]">
        <Progress value={Math.max(0, Math.min(100, score))} className="h-2" />
        <div className="flex justify-between text-[10px] mt-1 opacity-70 mono font-mono-tabular">
          <span>0</span><span>68</span><span>78</span><span>88</span><span>100</span>
        </div>
      </div>
      <span className="px-2 py-1 text-[11px] font-semibold rounded border bg-background/30 border-current">{label}</span>
    </div>
  );
}
