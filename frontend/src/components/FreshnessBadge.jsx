import { useI18n } from '@/lib/i18n';
import { Clock, AlertTriangle, MinusCircle } from 'lucide-react';

export function FreshnessBadge({ status = 'fresh', kind = 'odds', testId }) {
  const { t } = useI18n();
  const cls =
    status === 'fresh'
      ? 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30'
      : status === 'stale'
      ? 'bg-amber-500/15 text-amber-200 border-amber-500/30'
      : 'bg-slate-500/15 text-slate-200 border-slate-500/30';
  const Icon = status === 'fresh' ? Clock : status === 'stale' ? AlertTriangle : MinusCircle;
  return (
    <span data-testid={testId || `freshness-${kind}-${status}`} className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md border ${cls}`}>
      <Icon className="h-3 w-3" />
      <span className="uppercase tracking-wide">{kind}</span>
      <span>{t.freshness[status] || status}</span>
    </span>
  );
}
