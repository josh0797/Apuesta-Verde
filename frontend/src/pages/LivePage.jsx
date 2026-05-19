import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, RefreshCcw } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { useSport } from '@/lib/sport';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { LivePulse } from '@/components/LivePulse';
import { Skeleton } from '@/components/ui/skeleton';

function stat(side, key) {
  if (!side) return null;
  return side[key];
}

export default function LivePage() {
  const { t } = useI18n();
  const { sport } = useSport();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    try {
      const r = await api.get('/matches/live', { params: { refresh, sport } });
      setItems(r.data.items || []);
    } catch (e) {
      // noop
    } finally { setLoading(false); setRefreshing(false); }
  }, [sport]);

  useEffect(() => { load(true); const id = setInterval(() => load(true), 60_000); return () => clearInterval(id); }, [load]);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.live.title}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t.live.subtitle}</p>
        </div>
        <Button variant="secondary" data-testid="live-refresh-btn" onClick={() => load(true)} disabled={refreshing}>
          {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
        </Button>
      </div>

      {loading && <div className="grid gap-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div>}

      {!loading && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center" data-testid="live-empty">
          <p className="text-sm text-muted-foreground">{t.live.noLive}</p>
        </div>
      )}

      <div className="grid gap-3">
        {items.map((m) => {
          const live = m.live_stats || {};
          const h = live.home_stats || {};
          const a = live.away_stats || {};
          return (
            <Link to={`/match/${m.match_id}`} key={m.match_id} className="card-glow rounded-xl border border-border/80 bg-card p-4 flex flex-col gap-2" data-testid={`live-row-${m.match_id}`}>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <LivePulse minute={live.minute} label={t.match.livePill} />
                  <span className="text-xs text-muted-foreground">{m.league}</span>
                </div>
                <div className="mono font-mono-tabular text-2xl font-semibold">
                  <span>{live.score?.home ?? 0}</span>
                  <span className="text-muted-foreground mx-1">–</span>
                  <span>{live.score?.away ?? 0}</span>
                </div>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-base font-medium">{m.home_team?.name}</span>
                <span className="text-base font-medium">{m.away_team?.name}</span>
              </div>
              <div className="grid grid-cols-4 gap-2 text-[11px] text-muted-foreground mt-1">
                <StatCell label={t.live.possession} h={stat(h, 'Ball Possession')} a={stat(a, 'Ball Possession')} />
                <StatCell label={t.live.shots} h={stat(h, 'Total Shots')} a={stat(a, 'Total Shots')} />
                <StatCell label={t.live.shotsOn} h={stat(h, 'Shots on Goal')} a={stat(a, 'Shots on Goal')} />
                <StatCell label={t.live.xg} h={stat(h, 'expected_goals')} a={stat(a, 'expected_goals')} />
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function StatCell({ label, h, a }) {
  return (
    <div className="rounded-md border border-border bg-secondary/30 p-2 text-center">
      <div className="text-[10px] uppercase tracking-wide opacity-70">{label}</div>
      <div className="mono font-mono-tabular text-sm text-foreground">
        {h ?? '—'} <span className="text-muted-foreground">|</span> {a ?? '—'}
      </div>
    </div>
  );
}
