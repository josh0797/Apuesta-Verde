/**
 * LiveTodayPanel
 * ==============
 *
 * Surface today's picks that are currently mid-game.
 *
 * Data source
 * -----------
 *   GET /api/picks/today/live?sport={sport}
 *
 * The backend filters the most recent pick_run snapshot to only include
 * entries marked `is_live_route=True` (i.e. games already in progress
 * at the time of pick generation). When the snapshot is stale (>2h or
 * different UTC day) the endpoint auto-dispatches a background refresh
 * — we surface that with a subtle "Actualizando…" hint.
 *
 * Refresh strategy
 * ----------------
 *   • Polling every 30s while mounted.
 *   • Tab visibility guard: when the page is hidden we skip the poll to
 *     save quota and avoid stale toasts on return.
 *   • Manual refresh button for power users.
 *
 * Placement
 * ---------
 * Rendered inside `DashboardPage` underneath the summary strip. It self-
 * hides when there are 0 live picks AND we already polled once (so it
 * doesn't blank out the dashboard on the first paint).
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Activity, RefreshCw, ChevronRight } from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { formatDateTime } from '@/lib/format';

const POLL_INTERVAL_MS = 30_000;

export function LiveTodayPanel({ sport, lang = 'es', testId = 'live-today-panel' }) {
  const [items, setItems]               = useState([]);
  const [stale, setStale]               = useState(false);
  const [refreshing, setRefreshing]     = useState(false);
  const [lastFetchAt, setLastFetchAt]   = useState(null);
  const [loaded, setLoaded]             = useState(false);
  const sportRef                        = useRef(sport);

  // Keep a live ref so the polling loop never compares against a stale
  // sport closure (DashboardPage already uses this pattern).
  useEffect(() => { sportRef.current = sport; }, [sport]);

  const fetchOnce = useCallback(async () => {
    const requestSport = sportRef.current;
    try {
      setRefreshing(true);
      const r = await api.get('/picks/today/live', { params: { sport: requestSport } });
      if (sportRef.current !== requestSport) return;
      const pr = r.data?.pick_run || {};
      const payload = pr.payload || {};
      const all = [
        ...(payload.picks || []),
        ...(payload.rescued_picks || []),
      ];
      setItems(all);
      setStale(!!r.data?.stale);
      setLastFetchAt(new Date());
    } catch (e) {
      // Silently ignore — the dashboard already surfaces global errors.
    } finally {
      setRefreshing(false);
      setLoaded(true);
    }
  }, []);

  // Initial load + visibility-aware polling.
  useEffect(() => {
    let alive = true;
    fetchOnce();
    const tick = () => {
      if (document.visibilityState !== 'visible') return;
      if (!alive) return;
      fetchOnce();
    };
    const id = setInterval(tick, POLL_INTERVAL_MS);
    const onVis = () => {
      // When tab regains focus, refresh immediately so the user never
      // sees an old in-progress score after returning to the page.
      if (document.visibilityState === 'visible') fetchOnce();
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      alive = false;
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [fetchOnce, sport]);

  // Don't blink the empty-state on the first render; wait until the
  // first poll has resolved.
  if (loaded && items.length === 0 && !stale) return null;

  const tEN = {
    title:        'In progress right now',
    subtitle:     'Picks from today with mid-game analysis',
    refresh:      'Refresh',
    refreshing:   'Refreshing…',
    empty:        'No in-progress picks at the moment.',
    loadingHint:  'Loading live picks…',
    seeAll:       'See match',
    badge:        'LIVE',
    lastUpdate:   'Updated',
  };
  const tES = {
    title:        'En curso ahora',
    subtitle:     'Picks del día con análisis en juego',
    refresh:      'Actualizar',
    refreshing:   'Actualizando…',
    empty:        'No hay picks en curso en este momento.',
    loadingHint:  'Cargando picks en vivo…',
    seeAll:       'Ver partido',
    badge:        'EN VIVO',
    lastUpdate:   'Última actualización',
  };
  const t = lang === 'en' ? tEN : tES;

  return (
    <section
      data-testid={testId}
      className="rounded-xl border border-rose-500/20 bg-gradient-to-b from-rose-500/[0.04] to-transparent p-4 space-y-3"
    >
      <header className="flex items-start sm:items-center justify-between gap-2 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide bg-rose-500/15 text-rose-200 border border-rose-500/30">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-rose-300 animate-pulse" />
              {t.badge}
            </span>
            <h3 className="text-sm font-semibold text-foreground">{t.title}</h3>
            {items.length > 0 && (
              <span
                className="inline-flex items-center justify-center min-w-[20px] h-5 px-1 rounded-md bg-rose-500/15 text-rose-200 text-[11px] font-semibold border border-rose-500/30"
                data-testid={`${testId}-count`}
              >
                {items.length}
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground mt-0.5">{t.subtitle}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {lastFetchAt && (
            <span className="text-[10px] text-muted-foreground">
              {t.lastUpdate}: {formatTimeOnly(lastFetchAt, lang)}
            </span>
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={fetchOnce}
            disabled={refreshing}
            data-testid={`${testId}-refresh`}
            className="h-7 px-2 text-[11px]"
          >
            {refreshing ? (
              <><Loader2 className="h-3 w-3 animate-spin mr-1" />{t.refreshing}</>
            ) : (
              <><RefreshCw className="h-3 w-3 mr-1" />{t.refresh}</>
            )}
          </Button>
        </div>
      </header>

      {/* Stale hint */}
      {stale && (
        <div
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-cyan-500/10 text-cyan-200 border border-cyan-500/25 text-[10px] font-medium"
          data-testid={`${testId}-stale-hint`}
        >
          <Loader2 className="h-3 w-3 animate-spin" />
          {lang === 'en'
            ? 'Stale snapshot — fresh data on the way.'
            : 'Snapshot obsoleto — datos frescos en camino.'}
        </div>
      )}

      {/* Content */}
      {!loaded ? (
        <div className="text-[11px] text-muted-foreground flex items-center gap-2">
          <Loader2 className="h-3 w-3 animate-spin" /> {t.loadingHint}
        </div>
      ) : items.length === 0 ? (
        <div
          className="rounded-md border border-dashed border-border bg-card/40 px-3 py-4 text-center"
          data-testid={`${testId}-empty`}
        >
          <p className="text-[11px] text-muted-foreground">{t.empty}</p>
        </div>
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-2" data-testid={`${testId}-list`}>
          {items.map((m, i) => (
            <LiveTodayCard
              key={m.match_id || i}
              match={m}
              lang={lang}
              seeAllLabel={t.seeAll}
              testId={`${testId}-card-${i}`}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function LiveTodayCard({ match, lang, seeAllLabel, testId }) {
  const rec = match.recommendation || {};
  const confidence = Number(rec.confidence_score ?? rec.confidence ?? 0);
  const homeTeam = match.home_team?.name || match.home_team || (match.match_label || '').split(/\s+vs\s+/i)[0];
  const awayTeam = match.away_team?.name || match.away_team || (match.match_label || '').split(/\s+vs\s+/i)[1];

  return (
    <li
      data-testid={testId}
      className="rounded-md border border-border bg-card/60 hover:border-rose-500/30 transition-colors p-2.5 space-y-1.5"
    >
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1 px-1 py-0.5 rounded bg-rose-500/15 text-rose-200 border border-rose-500/30 font-semibold uppercase tracking-wide">
          <Activity className="h-2.5 w-2.5" />
          {lang === 'en' ? 'LIVE' : 'EN VIVO'}
        </span>
        <span className="truncate">{match.league || ''}</span>
        {match.kickoff_iso && (
          <span className="mono font-mono-tabular ml-auto">{formatDateTime(match.kickoff_iso, lang)}</span>
        )}
      </div>
      <Link
        to={`/match/${match.match_id}`}
        className="block text-sm font-semibold text-foreground hover:text-rose-200 transition-colors truncate"
        data-testid={`${testId}-title`}
      >
        {homeTeam} <span className="text-muted-foreground font-normal">vs</span> {awayTeam}
      </Link>
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <div className="min-w-0 truncate">
          {rec.market && (
            <span className="px-1.5 py-0.5 rounded-md bg-emerald-500/10 text-emerald-200 border border-emerald-500/25 mr-1.5 inline-block">
              {rec.market}
            </span>
          )}
          {rec.selection && (
            <span className="text-foreground" data-testid={`${testId}-selection`}>{rec.selection}</span>
          )}
        </div>
        <div className="shrink-0 flex items-center gap-1">
          {confidence > 0 && (
            <span
              className={`tabular-nums text-[10px] font-semibold px-1 py-0.5 rounded ${
                confidence >= 70 ? 'bg-emerald-500/15 text-emerald-200' :
                confidence >= 50 ? 'bg-amber-500/15 text-amber-200' :
                                   'bg-slate-500/15 text-slate-200'
              }`}
              title="Confidence"
            >
              {confidence}%
            </span>
          )}
          <Link
            to={`/match/${match.match_id}`}
            className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-rose-200 transition-colors"
            data-testid={`${testId}-cta`}
          >
            {seeAllLabel}
            <ChevronRight className="h-3 w-3" />
          </Link>
        </div>
      </div>
    </li>
  );
}

function formatTimeOnly(date, lang) {
  if (!date) return '';
  try {
    return new Intl.DateTimeFormat(lang === 'en' ? 'en-US' : 'es-ES', {
      hour: '2-digit', minute: '2-digit',
    }).format(date);
  } catch {
    return '';
  }
}

export default LiveTodayPanel;
