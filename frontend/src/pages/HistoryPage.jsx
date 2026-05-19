import { useEffect, useState, useCallback } from 'react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Skeleton } from '@/components/ui/skeleton';
import { tierClass, confidenceTier } from '@/lib/format';
import { BadgeCheck, ThumbsDown, Equal, Clock } from 'lucide-react';

export default function HistoryPage() {
  const { t, lang } = useI18n();
  const [stats, setStats] = useState(null);
  const [tracked, setTracked] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, tr] = await Promise.all([api.get('/stats/dashboard'), api.get('/picks/tracked')]);
      setStats(s.data);
      setTracked(tr.data.items || []);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8"><Skeleton className="h-32 rounded-xl mb-4" /><Skeleton className="h-64 rounded-xl" /></div>;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <div>
        <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.history.title}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t.history.subtitle}</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="history-kpi-strip">
        <KPI label={t.history.winRate} value={`${stats?.win_rate ?? 0}%`} accent="emerald" />
        <KPI label={t.history.settled} value={(stats?.won ?? 0) + (stats?.lost ?? 0)} />
        <KPI label={t.history.streak} value={stats?.streak ?? 0} accent="amber" />
        <KPI label={t.history.last10} value={`${(stats?.last10 || []).filter(x => x.outcome === 'won').length}/${(stats?.last10 || []).length}`} />
      </div>

      <section className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.history.byTier}</div>
        <div className="grid sm:grid-cols-3 gap-3">
          {['Maxima', 'Alta', 'Media'].map((tier) => (
            <div key={tier} className={`rounded-lg p-3 ${tierClass(tier)}`} data-testid={`tier-${tier}`}>
              <div className="text-[11px] uppercase opacity-80">{t.confidence[tier]}</div>
              <div className="text-2xl mono font-mono-tabular font-semibold">{stats?.accuracy_by_tier?.[tier]?.rate ?? 0}%</div>
              <div className="text-[11px] opacity-80 mono font-mono-tabular">{stats?.accuracy_by_tier?.[tier]?.won ?? 0}/{stats?.accuracy_by_tier?.[tier]?.settled ?? 0}</div>
            </div>
          ))}
        </div>
      </section>

      {tracked.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center" data-testid="history-empty">
          <p className="text-sm text-muted-foreground">{t.history.empty}</p>
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-secondary/50">
              <tr>
                <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Match</th>
                <th className="text-left px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Market</th>
                <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Confidence</th>
                <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Outcome</th>
                <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map((t2, i) => (
                <tr key={i} className="border-t border-border hover:bg-white/[0.03]" data-testid={`tracked-row-${i}`}>
                  <td className="px-3 py-2">{t2.match_label || t2.match_id}</td>
                  <td className="px-3 py-2 text-muted-foreground">{t2.market}: {t2.selection}</td>
                  <td className="px-3 py-2 text-right mono font-mono-tabular">{t2.confidence_score}</td>
                  <td className="px-3 py-2 text-right"><OutcomePill outcome={t2.outcome} /></td>
                  <td className="px-3 py-2 text-right text-muted-foreground mono font-mono-tabular text-xs">{new Date(t2.tracked_at).toLocaleString(lang === 'es' ? 'es-ES' : 'en-US')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function KPI({ label, value, accent }) {
  const cls = accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : accent === 'amber' ? 'border-amber-500/30 bg-amber-500/5 text-amber-300' : 'border-border bg-card';
  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <div className="text-[11px] uppercase tracking-wide opacity-80">{label}</div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
    </div>
  );
}

function OutcomePill({ outcome }) {
  if (outcome === 'won') return <span className="inline-flex items-center gap-1 text-emerald-300 text-xs"><BadgeCheck className="h-3.5 w-3.5" />Won</span>;
  if (outcome === 'lost') return <span className="inline-flex items-center gap-1 text-red-300 text-xs"><ThumbsDown className="h-3.5 w-3.5" />Lost</span>;
  if (outcome === 'push') return <span className="inline-flex items-center gap-1 text-muted-foreground text-xs"><Equal className="h-3.5 w-3.5" />Push</span>;
  return <span className="inline-flex items-center gap-1 text-cyan-300 text-xs"><Clock className="h-3.5 w-3.5" />Pending</span>;
}
