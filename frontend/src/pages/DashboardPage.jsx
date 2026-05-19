import { useEffect, useState, useMemo, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Sparkles, Loader2, RefreshCcw, ChevronDown, ChevronUp } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { MatchCard } from '@/components/MatchCard';
import { EmptyStateNoValue } from '@/components/EmptyStateNoValue';
import { Skeleton } from '@/components/ui/skeleton';
import { Link } from 'react-router-dom';
import { formatDateTime, tierClass } from '@/lib/format';

function GroupSection({ title, count, tier, children, defaultOpen = true, testId }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="space-y-3" data-testid={testId}>
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 w-full text-left">
        <span className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-xs font-semibold ${tierClass(tier)}`}>
          {title}
          <span className="mono font-mono-tabular bg-background/30 px-1.5 rounded">{count}</span>
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && children}
    </section>
  );
}

export default function DashboardPage() {
  const { t, lang } = useI18n();
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadLast = useCallback(async () => {
    try {
      setLoading(true);
      const r = await api.get('/picks/today');
      setRun(r.data.pick_run);
    } catch (e) { /* noop */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadLast(); }, [loadLast]);

  const generate = async () => {
    setRunning(true);
    try {
      const r = await api.post('/analysis/run', { refresh: true, include_live: true, max_matches: 15 });
      setRun({ id: r.data.pick_run_id, generated_at: r.data.generated_at, payload: r.data.result });
      toast.success(t.dashboard.title + ' ✓');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Error');
    } finally { setRunning(false); }
  };

  const data = run?.payload;
  const { high, medium, discMot, discMkt, incomplete } = useMemo(() => {
    if (!data) return { high: [], medium: [], discMot: [], discMkt: [], incomplete: [] };
    const picks = data.picks || [];
    return {
      high: picks.filter((p) => (p.recommendation?.confidence_score || 0) >= 78),
      medium: picks.filter((p) => { const c = p.recommendation?.confidence_score || 0; return c >= 68 && c < 78; }),
      discMot: data.summary?.discarded_motivation || [],
      discMkt: data.summary?.discarded_market || [],
      incomplete: data.summary?.incomplete_data || [],
    };
  }, [data]);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.dashboard.title}</h1>
          <p className="text-muted-foreground mt-1 text-sm">{t.dashboard.subtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          {run?.generated_at && (
            <div className="text-xs text-muted-foreground">
              {t.dashboard.lastRun}: <span className="mono font-mono-tabular text-foreground">{formatDateTime(run.generated_at, lang)}</span>
            </div>
          )}
          <Button onClick={generate} disabled={running} data-testid="generate-picks-button" className="shadow-[0_0_0_1px_rgba(46,229,157,0.2),0_8px_24px_rgba(46,229,157,0.15)]">
            {running ? (<><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.running}</>) : (<><Sparkles className="h-4 w-4 mr-2" />{t.dashboard.generateBtn}</>)}
          </Button>
        </div>
      </motion.div>

      {/* Summary strip */}
      {data?.summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="summary-strip">
          <div className="rounded-lg border border-border bg-card p-3"><div className="text-[11px] uppercase text-muted-foreground">{t.dashboard.analyzed}</div><div className="text-2xl mono font-mono-tabular font-semibold">{data.summary.total_analyzed ?? 0}</div></div>
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3"><div className="text-[11px] uppercase text-emerald-200">{t.dashboard.recommended}</div><div className="text-2xl mono font-mono-tabular font-semibold text-emerald-300">{data.summary.total_recommended ?? 0}</div></div>
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3"><div className="text-[11px] uppercase text-red-200">{t.dashboard.discarded}</div><div className="text-2xl mono font-mono-tabular font-semibold text-red-300">{data.summary.total_discarded ?? 0}</div></div>
          <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 p-3"><div className="text-[11px] uppercase text-cyan-200">Live</div><div className="text-2xl mono font-mono-tabular font-semibold text-cyan-300">{data.summary.data_freshness?.live_active ?? 0}</div></div>
        </div>
      )}

      {loading && (
        <div className="grid gap-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}
        </div>
      )}

      {!loading && !run && (
        <div className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center" data-testid="no-run-empty">
          <p className="text-sm text-muted-foreground">{t.dashboard.noRunYet}</p>
        </div>
      )}

      {!loading && data && data.verdict === 'no_value' && (
        <EmptyStateNoValue />
      )}

      {!loading && data && data.verdict === 'value_found' && (
        <div className="space-y-6">
          {high.length > 0 && (
            <GroupSection title={t.dashboard.groupHigh} count={high.length} tier="Alta" testId="group-high">
              <div className="grid gap-3">{high.map((p, i) => <MatchCard key={p.match_id || i} pick={p} idx={i} />)}</div>
            </GroupSection>
          )}
          {medium.length > 0 && (
            <GroupSection title={t.dashboard.groupMedium} count={medium.length} tier="Media" testId="group-medium">
              <div className="grid gap-3">{medium.map((p, i) => <MatchCard key={p.match_id || i} pick={p} idx={i} />)}</div>
            </GroupSection>
          )}
          {discMot.length > 0 && (
            <GroupSection title={t.dashboard.groupDiscMotivation} count={discMot.length} tier="Below" defaultOpen={false} testId="group-discarded-motivation">
              <div className="grid gap-2">{discMot.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-motivation-row" />)}</div>
            </GroupSection>
          )}
          {discMkt.length > 0 && (
            <GroupSection title={t.dashboard.groupDiscMarket} count={discMkt.length} tier="Below" defaultOpen={false} testId="group-discarded-market">
              <div className="grid gap-2">{discMkt.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-market-row" />)}</div>
            </GroupSection>
          )}
          {incomplete.length > 0 && (
            <GroupSection title={t.dashboard.groupIncomplete} count={incomplete.length} tier="Below" defaultOpen={false} testId="group-incomplete">
              <div className="grid gap-2">{incomplete.map((d, i) => <DiscardedRow key={i} item={{ match_label: d.match_label, reason: d.missing }} testId="incomplete-data-row" />)}</div>
            </GroupSection>
          )}
          {high.length === 0 && medium.length === 0 && <EmptyStateNoValue />}
        </div>
      )}
    </div>
  );
}

function DiscardedRow({ item, testId }) {
  return (
    <div className="flex items-center justify-between px-3 py-2 rounded-md border border-border bg-card/60" data-testid={testId}>
      <span className="text-sm text-foreground/90">{item.match_label}</span>
      <span className="text-xs text-muted-foreground text-right max-w-[55%]">{item.reason}</span>
    </div>
  );
}
