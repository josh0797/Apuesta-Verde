import { useEffect, useState, useCallback } from 'react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Skeleton } from '@/components/ui/skeleton';
import { tierClass, humanizeSelection } from '@/lib/format';
import { BadgeCheck, ThumbsDown, Equal, Clock, Download, DollarSign, TrendingUp, TrendingDown, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { WinRateChart } from '@/components/WinRateChart';
import { toast } from 'sonner';

export default function HistoryPage() {
  const { t, lang } = useI18n();
  const [stats, setStats] = useState(null);
  const [tracked, setTracked] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stake, setStake] = useState(() => Number(localStorage.getItem('vbi_stake') || '10'));
  const [settling, setSettling] = useState({}); // { [pick_id]: true } while updating

  const load = useCallback(async (currentStake) => {
    setLoading(true);
    try {
      const [s, tr, tl] = await Promise.all([
        api.get('/stats/dashboard', { params: { stake: currentStake } }),
        api.get('/picks/tracked'),
        api.get('/stats/timeline'),
      ]);
      setStats(s.data);
      setTracked(tr.data.items || []);
      setTimeline(tl.data.timeline || []);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(stake); }, [load, stake]);

  const updateStake = (v) => {
    const n = Math.max(0.1, Number(v) || 10);
    setStake(n);
    localStorage.setItem('vbi_stake', String(n));
  };

  const exportCsv = async () => {
    try {
      const r = await api.get('/picks/tracked/export.csv', { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([r.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url; a.download = 'picks-tracked.csv';
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error('Export failed'); }
  };

  // Settle a pending pick by upserting it through the same /picks/track endpoint.
  // The server uses (user_id, match_id, pick_id) as the upsert key so we only
  // need to resend the existing fields plus the new outcome.
  const settlePick = async (row, outcome) => {
    if (!row || settling[row.pick_id]) return;
    setSettling((m) => ({ ...m, [row.pick_id]: true }));
    try {
      await api.post('/picks/track', {
        run_id: row.run_id,
        match_id: row.match_id,
        market: row.market,
        selection: row.selection,
        confidence_score: row.confidence_score || 0,
        outcome,
        odds: row.odds ?? null,
        league: row.league || null,
        match_label: row.match_label || null,
        sport: row.sport || 'football',
      });
      toast.success(t.history.settledOk);
      await load(stake);
    } catch (e) {
      toast.error(e?.response?.data?.detail || t.history.settleError);
    } finally {
      setSettling((m) => {
        const next = { ...m };
        delete next[row.pick_id];
        return next;
      });
    }
  };

  if (loading) return <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8"><Skeleton className="h-32 rounded-xl mb-4" /><Skeleton className="h-64 rounded-xl" /></div>;

  const roi = stats?.roi || {};
  const profitColor = (roi.total_profit ?? 0) >= 0 ? 'emerald' : 'red';

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.history.title}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t.history.subtitle}</p>
        </div>
        <Button variant="secondary" size="sm" onClick={exportCsv} data-testid="history-export-csv-btn">
          <Download className="h-3.5 w-3.5 mr-1.5" />{t.history.exportCsv}
        </Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="history-kpi-strip">
        <KPI label={t.history.winRate} value={`${stats?.win_rate ?? 0}%`} accent="emerald" />
        <KPI label={t.history.settled} value={(stats?.won ?? 0) + (stats?.lost ?? 0)} />
        <KPI label={t.history.streak} value={stats?.streak ?? 0} accent="amber" />
        <KPI label={t.history.last10} value={`${(stats?.last10 || []).filter(x => x.outcome === 'won').length}/${(stats?.last10 || []).length}`} />
      </div>

      {/* ROI calculator card */}
      <section className="rounded-xl border border-border bg-card p-4 md:p-5" data-testid="roi-calculator">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
            <DollarSign className="h-4 w-4" />
            {t.history.roiTitle}
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="stake-input" className="text-xs text-muted-foreground">{t.history.stakeLabel}</label>
            <Input
              id="stake-input" data-testid="stake-input" type="number" min="0.1" step="0.1"
              value={stake}
              onChange={(e) => updateStake(e.target.value)}
              className="h-8 w-24 text-right mono font-mono-tabular"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <KPI label={t.history.roiTotalWagered} value={`${(roi.total_wagered ?? 0).toFixed(2)}`} hint={`${roi.settled_with_odds || 0} ${t.history.settledWithOdds}`} />
          <KPI label={t.history.roiNetProfit} value={`${(roi.total_profit ?? 0) >= 0 ? '+' : ''}${(roi.total_profit ?? 0).toFixed(2)}`} accent={profitColor} icon={(roi.total_profit ?? 0) >= 0 ? TrendingUp : TrendingDown} />
          <KPI label={t.history.roiPct} value={`${(roi.roi_pct ?? 0) >= 0 ? '+' : ''}${(roi.roi_pct ?? 0).toFixed(2)}%`} accent={profitColor} />
          <KPI label={t.history.roiAvgWonOdds} value={(roi.avg_won_odds ?? 0).toFixed(2)} hint={`${t.history.roiAvgLostOdds}: ${(roi.avg_lost_odds ?? 0).toFixed(2)}`} />
        </div>
        {(roi.settled_with_odds ?? 0) < (roi.settled_total ?? 0) && (roi.settled_total ?? 0) > 0 && (
          <p className="text-[11px] text-muted-foreground mt-3 italic">{t.history.roiHint}</p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-card p-4" data-testid="winrate-evolution-section">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.history.evolution}</div>
        <WinRateChart data={timeline} />
      </section>

      <section className="rounded-xl border border-border bg-card p-4">
        <div className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.history.byTier}</div>
        <div className="grid sm:grid-cols-3 gap-3">
          {['Maxima', 'Alta', 'Media'].map((tier) => {
            const tierData = stats?.accuracy_by_tier?.[tier] || {};
            const tierRoi = tierData.roi_pct ?? 0;
            return (
              <div key={tier} className={`rounded-lg p-3 ${tierClass(tier)}`} data-testid={`tier-${tier}`}>
                <div className="text-[11px] uppercase opacity-80">{t.confidence[tier]}</div>
                <div className="text-2xl mono font-mono-tabular font-semibold">{tierData.rate ?? 0}%</div>
                <div className="text-[11px] opacity-80 mono font-mono-tabular">{tierData.won ?? 0}/{tierData.settled ?? 0}</div>
                <div className={`text-[11px] mono font-mono-tabular mt-1 ${tierRoi >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>
                  ROI: {tierRoi >= 0 ? '+' : ''}{tierRoi.toFixed(1)}%
                </div>
              </div>
            );
          })}
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
                <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">{t.history.actions}</th>
                <th className="text-right px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map((row, i) => {
                // Split "Bayern vs Bremen" so humanizeSelection has home/away to work with.
                const parts = String(row.match_label || '').split(/\s+vs\s+/i);
                const homeName = parts[0]?.trim() || '';
                const awayName = parts[1]?.trim() || '';
                const humanSelection = humanizeSelection(
                  row.selection,
                  row.market,
                  homeName,
                  awayName,
                  lang,
                  row.sport || 'football',
                );
                const isPending = row.outcome === 'pending';
                const busy = !!settling[row.pick_id];
                return (
                  <tr key={row.pick_id || i} className="border-t border-border hover:bg-white/[0.03]" data-testid={`tracked-row-${i}`}>
                    <td className="px-3 py-2">{row.match_label || row.match_id}</td>
                    <td className="px-3 py-2 text-muted-foreground" data-testid={`tracked-selection-${i}`}>
                      <span className="text-foreground/80 font-medium">{row.market}</span>
                      <span className="text-muted-foreground/80">: </span>
                      <span className="text-foreground">{humanSelection}</span>
                    </td>
                    <td className="px-3 py-2 text-right mono font-mono-tabular">{row.confidence_score}</td>
                    <td className="px-3 py-2 text-right">
                      {isPending ? (
                        <div className="inline-flex items-center gap-1 justify-end" data-testid={`settle-actions-${i}`}>
                          {busy ? (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                          ) : (
                            <>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => settlePick(row, 'won')}
                                className="h-7 px-2 text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/10"
                                data-testid={`settle-won-${i}`}
                                title={t.history.markWon}
                              >
                                <BadgeCheck className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => settlePick(row, 'lost')}
                                className="h-7 px-2 text-red-300 hover:text-red-200 hover:bg-red-500/10"
                                data-testid={`settle-lost-${i}`}
                                title={t.history.markLost}
                              >
                                <ThumbsDown className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => settlePick(row, 'push')}
                                className="h-7 px-2 text-muted-foreground hover:text-foreground hover:bg-white/[0.06]"
                                data-testid={`settle-push-${i}`}
                                title={t.history.markPush}
                              >
                                <Equal className="h-3.5 w-3.5" />
                              </Button>
                            </>
                          )}
                        </div>
                      ) : (
                        <OutcomePill outcome={row.outcome} t={t} />
                      )}
                    </td>
                    <td className="px-3 py-2 text-right text-muted-foreground mono font-mono-tabular text-xs">{new Date(row.tracked_at).toLocaleString(lang === 'es' ? 'es-ES' : 'en-US')}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function KPI({ label, value, accent, icon: Icon, hint }) {
  const cls = accent === 'emerald' ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300' : accent === 'red' ? 'border-red-500/30 bg-red-500/5 text-red-300' : accent === 'amber' ? 'border-amber-500/30 bg-amber-500/5 text-amber-300' : 'border-border bg-card';
  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <div className="text-[11px] uppercase tracking-wide opacity-80 flex items-center gap-1.5">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5 mono font-mono-tabular">{hint}</div>}
    </div>
  );
}

function OutcomePill({ outcome, t }) {
  if (outcome === 'won') return <span className="inline-flex items-center gap-1 text-emerald-300 text-xs"><BadgeCheck className="h-3.5 w-3.5" />{t.history.markWon}</span>;
  if (outcome === 'lost') return <span className="inline-flex items-center gap-1 text-red-300 text-xs"><ThumbsDown className="h-3.5 w-3.5" />{t.history.markLost}</span>;
  if (outcome === 'push') return <span className="inline-flex items-center gap-1 text-muted-foreground text-xs"><Equal className="h-3.5 w-3.5" />{t.history.markPush}</span>;
  return <span className="inline-flex items-center gap-1 text-cyan-300 text-xs"><Clock className="h-3.5 w-3.5" />{t.history.outcomePending}</span>;
}
