import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Loader2, ChevronDown, ChevronUp, ExternalLink, Activity, Shield, TrendingDown, AlertCircle, Eye, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n, sportTerms } from '@/lib/i18n';
import { useSport, sportLabel } from '@/lib/sport';
import { api } from '@/lib/api';
import { applyEnginePreset } from '@/lib/intelligence';
import { AnalysisProgressModal } from '@/components/AnalysisProgressModal';

import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { MatchCard } from '@/components/MatchCard';
import { SkippedMatchRow } from '@/components/FootballQualityBadge';
import { EmptyStateNoValue } from '@/components/EmptyStateNoValue';
import { PicksFilterBar } from '@/components/PicksFilterBar';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime, tierClass } from '@/lib/format';

function GroupSection({ title, count, tier, children, defaultOpen = true, testId, sectionRef, icon: Icon }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="space-y-3" data-testid={testId} ref={sectionRef}>
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-2 w-full text-left group">
        <span className={`inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-xs font-semibold ${tierClass(tier)}`}>
          {Icon && <Icon className="h-3.5 w-3.5" />}
          {title}
          <span className="mono font-mono-tabular bg-background/30 px-1.5 rounded">{count}</span>
        </span>
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground group-hover:text-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground group-hover:text-foreground" />}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="overflow-hidden">
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function KpiCard({ label, value, accent, onClick, testId, hint }) {
  const accentCls = {
    emerald: 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200 hover:bg-emerald-500/10',
    red: 'border-red-500/30 bg-red-500/5 text-red-200 hover:bg-red-500/10',
    cyan: 'border-cyan-500/30 bg-cyan-500/5 text-cyan-200 hover:bg-cyan-500/10',
    neutral: 'border-border bg-card text-foreground hover:bg-secondary/40',
  }[accent] || 'border-border bg-card text-foreground';
  return (
    <button onClick={onClick} disabled={!onClick} data-testid={testId} className={`text-left rounded-lg border p-3 transition-colors ${accentCls} ${onClick ? 'cursor-pointer' : 'cursor-default'}`}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide opacity-80">{label}</span>
        {onClick && <Eye className="h-3.5 w-3.5 opacity-60" />}
      </div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5">{hint}</div>}
    </button>
  );
}

function DiscardedRow({ item, testId, type }) {
  // Try to link if match_id available
  const inner = (
    <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-border bg-card/60 hover:border-cyan-500/30 transition-colors" data-testid={testId}>
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {type === 'incomplete' && <AlertCircle className="h-3.5 w-3.5 text-slate-300 shrink-0" />}
        {type === 'motivation' && <TrendingDown className="h-3.5 w-3.5 text-orange-300 shrink-0" />}
        {type === 'market' && <Shield className="h-3.5 w-3.5 text-red-300 shrink-0" />}
        <span className="text-sm text-foreground/90 truncate">{item.match_label}</span>
      </div>
      <span className="text-xs text-muted-foreground text-right max-w-[55%] truncate" title={item.reason || item.missing}>{item.reason || item.missing}</span>
      {item.match_id && <ExternalLink className="h-3 w-3 text-muted-foreground shrink-0" />}
    </div>
  );
  if (item.match_id) {
    return <Link to={`/match/${item.match_id}`} className="block">{inner}</Link>;
  }
  return inner;
}

export default function DashboardPage() {
  const { t, lang } = useI18n();
  const { sport, sports } = useSport();
  const currentSport = sports.find((s) => s.id === sport) || sports[0];
  const terms = sportTerms(lang, sport);
  const dynamicSubtitle = lang === 'es'
    ? `Análisis de valor para los próximos ${terms.eventPlural} (48h)`
    : `Value analysis for the next ${terms.eventPlural} (48h)`;
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ league: '', market: '', minConfidence: 0, enginePreset: '' });
  const [activeJobId, setActiveJobId] = useState(null);

  const refs = {
    high: useRef(null), medium: useRef(null),
    discMot: useRef(null), discMkt: useRef(null), incomplete: useRef(null),
  };

  const loadLast = useCallback(async () => {
    try {
      setLoading(true);
      const r = await api.get('/picks/today', { params: { sport } });
      setRun(r.data.pick_run);
    } catch (e) { /* noop */ }
    finally { setLoading(false); }
  }, [sport]);

  useEffect(() => { loadLast(); }, [loadLast]);

  // On mount / sport change, resume any active job for this user.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get('/analysis/jobs');
        if (cancelled) return;
        const active = (r.data?.active || []).find((j) => j.kind === 'analysis_run' && j?.params?.sport === sport);
        if (active) setActiveJobId(active.id);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [sport]);

  const generate = async () => {
    setRunning(true);
    try {
      // Use background mode so the UI shows real-time progress instead of a 60-120s spinner.
      const r = await api.post('/analysis/run', {
        refresh: true,
        include_live: true,
        max_matches: 8,
        sport,
        background: true,
      });
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      } else if (r.data?.result) {
        // Backward-compat: if backend ever returns sync, hydrate immediately.
        setRun({ id: r.data.pick_run_id, sport: r.data.sport, generated_at: r.data.generated_at, payload: r.data.result });
        toast.success(t.dashboard.title + ' ✓');
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Error');
    } finally { setRunning(false); }
  };

  const onJobDone = useCallback((result) => {
    // Refresh the dashboard with the freshly completed run.
    toast.success(t.dashboard.title + ' ✓');
    loadLast();
  }, [loadLast, t.dashboard.title]);

  const closeProgressModal = useCallback(() => {
    setActiveJobId(null);
  }, []);

  const exportCsv = async () => {
    try {
      const r = await api.get('/picks/today/export.csv', { responseType: 'blob', params: { sport } });
      const url = URL.createObjectURL(new Blob([r.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url; a.download = `picks-${sport}-today.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error('Export failed'); }
  };

  const data = run?.payload;
  const allPicks = useMemo(() => (data?.picks || []), [data]);
  const filteredPicks = useMemo(() => {
    // First apply field filters, then apply engine preset (composable).
    const fieldFiltered = allPicks.filter((p) => {
      if (filters.league && !(p.league || '').toLowerCase().includes(filters.league.toLowerCase())) return false;
      if (filters.market && !(p.recommendation?.market || '').toLowerCase().includes(filters.market.toLowerCase())) return false;
      if ((p.recommendation?.confidence_score || 0) < (filters.minConfidence || 0)) return false;
      return true;
    });
    return filters.enginePreset
      ? applyEnginePreset(fieldFiltered, filters.enginePreset)
      : fieldFiltered;
  }, [allPicks, filters]);

  const { high, medium, discMot, discMkt, incomplete, skippedLowRel } = useMemo(() => {
    if (!data) return { high: [], medium: [], discMot: [], discMkt: [], incomplete: [], skippedLowRel: [] };
    return {
      high: filteredPicks.filter((p) => (p.recommendation?.confidence_score || 0) >= 70).sort((a, b) => (b.recommendation?.confidence_score || 0) - (a.recommendation?.confidence_score || 0)),
      medium: filteredPicks.filter((p) => { const c = p.recommendation?.confidence_score || 0; return c >= 60 && c < 70; }).sort((a, b) => (b.recommendation?.confidence_score || 0) - (a.recommendation?.confidence_score || 0)),
      discMot: data.summary?.discarded_motivation || [],
      discMkt: data.summary?.discarded_market || [],
      incomplete: data.summary?.incomplete_data || [],
      skippedLowRel: data.summary?.skipped_low_relevance || [],
    };
  }, [data, filteredPicks]);

  const scrollTo = (key) => {
    const el = refs[key]?.current;
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  const hasAnyDiscarded = (discMot.length + discMkt.length + incomplete.length + skippedLowRel.length) > 0;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      {activeJobId && (
        <AnalysisProgressModal
          jobId={activeJobId}
          onClose={closeProgressModal}
          onDone={onJobDone}
          sport={sport}
        />
      )}
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight flex items-center gap-3">
            <span className="text-3xl" aria-hidden>{currentSport?.icon}</span>
            <span>{t.dashboard.title}</span>
            <span className="text-xs font-normal text-muted-foreground border border-border rounded-md px-2 py-0.5 align-middle" data-testid="active-sport-pill">
              {sportLabel(currentSport, lang)}
            </span>
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">{dynamicSubtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          {run?.generated_at && (
            <div className="text-xs text-muted-foreground">
              {t.dashboard.lastRun}: <span className="mono font-mono-tabular text-foreground">{formatDateTime(run.generated_at, lang)}</span>
            </div>
          )}
          <Button onClick={generate} disabled={running || !!activeJobId} data-testid="generate-picks-button" className="shadow-[0_0_0_1px_rgba(46,229,157,0.2),0_8px_24px_rgba(46,229,157,0.15)]">
            {activeJobId ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" />{lang === 'es' ? 'Procesando…' : 'Processing…'}</>
            ) : running ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.running}</>
            ) : (
              <><Sparkles className="h-4 w-4 mr-2" />{t.dashboard.generateBtn}</>
            )}
          </Button>
        </div>
      </motion.div>

      {/* Summary strip — now CLICKABLE */}
      {data?.summary && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3" data-testid="summary-strip">
          <KpiCard
            label={t.dashboard.analyzed} value={data.summary.total_analyzed ?? 0} accent="neutral"
            onClick={hasAnyDiscarded || high.length || medium.length ? () => scrollTo('incomplete') : null}
            testId="kpi-analyzed"
            hint={t.dashboard.openDetails}
          />
          <KpiCard
            label={t.dashboard.recommended} value={data.summary.total_recommended ?? 0} accent="emerald"
            onClick={high.length + medium.length > 0 ? () => scrollTo(high.length ? 'high' : 'medium') : null}
            testId="kpi-recommended"
          />
          <KpiCard
            label={t.dashboard.discarded} value={data.summary.total_discarded ?? 0} accent="red"
            onClick={hasAnyDiscarded ? () => scrollTo(discMot.length ? 'discMot' : discMkt.length ? 'discMkt' : 'incomplete') : null}
            testId="kpi-discarded"
          />
          <KpiCard
            label={lang === 'en' ? 'Exotic ignored' : 'Exóticos ignorados'}
            value={skippedLowRel.length}
            accent="amber"
            onClick={skippedLowRel.length > 0 ? () => scrollTo('skipped') : null}
            testId="kpi-exotic-ignored"
            hint={lang === 'en' ? 'Filtered before LLM' : 'Filtrados antes del LLM'}
          />
          <KpiCard label="Live" value={data.summary.data_freshness?.live_active ?? 0} accent="cyan" testId="kpi-live" />
        </div>
      )}

      {data && allPicks.length > 0 && (
        <PicksFilterBar
          filters={filters} onChange={setFilters} onExportCsv={exportCsv}
          totalCount={allPicks.length} filteredCount={filteredPicks.length}
        />
      )}

      {loading && (
        <div className="grid gap-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-40 rounded-xl" />)}</div>
      )}

      {!loading && !run && (
        <div className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center" data-testid="no-run-empty">
          <p className="text-sm text-muted-foreground">{t.dashboard.noRunYet}</p>
        </div>
      )}

      {/* Picks (high + medium) */}
      {!loading && data && (high.length > 0 || medium.length > 0) && (
        <div className="space-y-6">
          {high.length > 0 && (
            <GroupSection title={t.dashboard.groupHigh} count={high.length} tier="Alta" testId="group-high" sectionRef={refs.high} icon={Activity}>
              <div className="grid gap-3">{high.map((p, i) => <MatchCard key={p.match_id || i} pick={p} idx={i} sport={sport} runId={run?.id} />)}</div>
            </GroupSection>
          )}
          {medium.length > 0 && (
            <GroupSection title={t.dashboard.groupMedium} count={medium.length} tier="Media" testId="group-medium" sectionRef={refs.medium} icon={Activity}>
              <div className="grid gap-3">{medium.map((p, i) => <MatchCard key={p.match_id || i} pick={p} idx={i} sport={sport} runId={run?.id} />)}</div>
            </GroupSection>
          )}
        </div>
      )}

      {/* No-value message — ONLY shown when no picks; discarded sections still appear below */}
      {!loading && data && data.verdict === 'no_value' && high.length === 0 && medium.length === 0 && (
        <EmptyStateNoValue summary={data.summary} />
      )}

      {/* ALWAYS show discarded/incomplete sections when data exists — this was the missing UX */}
      {!loading && data && hasAnyDiscarded && (
        <div className="space-y-6">
          <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground pt-2 border-t border-border">
            {t.dashboard.detailsTitle}
          </div>

          {/* Phase 8.1 — Priority-league discards FIRST (Tier 1/2/3 matches
              that did reach the LLM and were rejected by motivation /
              market / data-quality gates). These deserve the top of the
              detail list because they're the actual relevant fixtures. */}
          {(discMot.length + discMkt.length + incomplete.length) > 0 && (
            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-cyan-200" data-testid="priority-league-banner">
              {lang === 'en'
                ? `Discarded from priority leagues (${discMot.length + discMkt.length + incomplete.length})`
                : `Descartados de ligas prioritarias (${discMot.length + discMkt.length + incomplete.length})`}
            </div>
          )}
          {discMot.length > 0 && (
            <GroupSection title={t.dashboard.groupDiscMotivation} count={discMot.length} tier="Below" defaultOpen={true} testId="group-discarded-motivation" sectionRef={refs.discMot} icon={TrendingDown}>
              <div className="grid gap-2">{discMot.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-motivation-row" type="motivation" />)}</div>
            </GroupSection>
          )}
          {discMkt.length > 0 && (
            <GroupSection title={t.dashboard.groupDiscMarket} count={discMkt.length} tier="Below" defaultOpen={true} testId="group-discarded-market" sectionRef={refs.discMkt} icon={Shield}>
              <div className="grid gap-2">{discMkt.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-market-row" type="market" />)}</div>
            </GroupSection>
          )}
          {incomplete.length > 0 && (
            <GroupSection title={t.dashboard.groupIncomplete} count={incomplete.length} tier="Below" defaultOpen={true} testId="group-incomplete" sectionRef={refs.incomplete} icon={AlertCircle}>
              <div className="grid gap-2">{incomplete.map((d, i) => <DiscardedRow key={i} item={{ match_id: d.match_id, match_label: d.match_label, reason: d.missing }} testId="incomplete-data-row" type="incomplete" />)}</div>
            </GroupSection>
          )}

          {/* Phase 8.1 — Exotic / low-relevance matches at the END,
              clearly labelled as filtered BEFORE any LLM analysis. */}
          {skippedLowRel.length > 0 && (
            <>
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-amber-200 mt-4" data-testid="exotic-banner">
                {lang === 'en'
                  ? `Exotic leagues — filtered before analysis (${skippedLowRel.length})`
                  : `Ligas exóticas — filtradas antes del análisis (${skippedLowRel.length})`}
              </div>
              <GroupSection
                title={lang === 'en' ? 'Skipped — low relevance' : 'Saltados — baja relevancia'}
                count={skippedLowRel.length}
                tier="Below"
                defaultOpen={false}
                testId="group-skipped-low-relevance"
                sectionRef={refs.skipped || refs.discMot}
                icon={ShieldAlert}
              >
                <div className="grid gap-2">
                  {skippedLowRel.slice(0, 20).map((d, i) => (
                    <SkippedMatchRow key={i} item={d} lang={lang} testId={`skipped-row-${i}`} />
                  ))}
                </div>
              </GroupSection>
            </>
          )}
        </div>
      )}
    </div>
  );
}
