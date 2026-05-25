import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, RefreshCcw, Brain, Trophy, Trophy as TrophyIcon, Archive } from 'lucide-react';
import { toast } from 'sonner';
import { useI18n } from '@/lib/i18n';
import { useSport } from '@/lib/sport';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { LivePulse } from '@/components/LivePulse';
import { Skeleton } from '@/components/ui/skeleton';
import { AnalysisProgressModal } from '@/components/AnalysisProgressModal';
import { MatchCard } from '@/components/MatchCard';
import { LiveReevalPanel } from '@/components/LiveReevalPanel';
import { LiveStateBadge, LiveFreshnessBadge } from '@/components/LiveStateBadges';
import { LiveAnalysisStrip } from '@/components/LiveAnalysisStrip';
import { LiveCopilotCard } from '@/components/LiveCopilotCard';
import { ProvenanceBadge } from '@/components/ProvenancePanel';
import { isBigFive } from '@/lib/competitions';
import { partitionLive, LIVE_CACHE_TTL_SEC, validLiveMatch } from '@/lib/liveValidation';

function stat(side, key) {
  if (!side) return null;
  return side[key];
}

export default function LivePage() {
  const { t, lang } = useI18n();
  const { sport } = useSport();
  const [items, setItems] = useState([]);
  const [archivedItems, setArchivedItems] = useState([]);
  const [archivedCount, setArchivedCount] = useState(0);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Live analysis (Big Five) — separate from the daily dashboard analysis.
  // We store the run + active job in component state so refreshing the stats
  // doesn't blow away the latest live picks.
  const [livePicks, setLivePicks] = useState([]);
  const [activeJobId, setActiveJobId] = useState(null);
  const [running, setRunning] = useState(false);
  const [liveRunGeneratedAt, setLiveRunGeneratedAt] = useState(null);
  const [liveRunMatchesAnalyzed, setLiveRunMatchesAnalyzed] = useState(0);

  // Big Five filter — ON by default for football to keep the page focused on
  // the same league universe used by the "Analyze live" button. The user can
  // opt out with the "Show all" toggle if they want to see lower-tier games.
  const [bigFiveOnly, setBigFiveOnly] = useState(true);

  // Bug-2 fix (sport-scoped requests, same pattern as DashboardPage): ref to
  // the current sport, used to DROP responses that arrive after the user has
  // already switched tabs. Prevents football matches from briefly appearing
  // on the basketball / baseball Live page.
  const sportRef = useRef(sport);
  useEffect(() => { sportRef.current = sport; }, [sport]);

  // Clear stale state the instant the sport changes.
  useEffect(() => {
    console.log('[SPORT_SWITCH] live', sport);
    setItems([]);
    setArchivedItems([]);
    setArchivedCount(0);
    setLivePicks([]);
    setActiveJobId(null);
    setLoading(true);
  }, [sport]);

  const load = useCallback(async (refresh = false) => {
    const requestSport = sport;
    if (refresh) setRefreshing(true); else setLoading(true);
    try {
      // Bust HTTP/proxy cache via _ts so the freshness check has truthful
      // updated_at timestamps even on aggressive intermediaries.
      const r = await api.get('/matches/live', {
        params: { refresh, sport, _ts: Date.now() },
      });
      if (sportRef.current !== requestSport) {
        console.log('[SPORT_SWITCH] discarded stale /matches/live for', requestSport);
        return;
      }
      const raw = r.data?.items || [];
      // Defence-in-depth: backend already filters, but we re-validate
      // client-side in case the tab sat idle and heartbeats aged out.
      const { valid, archived } = partitionLive(raw);
      console.log('[LIVE_MATCH_VALIDATION]', {
        sport: requestSport,
        received: raw.length,
        valid: valid.length,
        archived_client: archived.length,
        archived_server: r.data?.archived_count || 0,
      });
      setItems(valid);
      setArchivedItems(archived);
      setArchivedCount((r.data?.archived_count || 0) + archived.length);
    } catch (e) {
      // noop
    } finally {
      if (sportRef.current === requestSport) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [sport]);

  // Refresh cadence is dictated by the backend per sport (60s football,
  // 30s basket, 45s baseball). Re-fetch on focus too so leaving + coming
  // back to the tab always shows a truthful list.
  //
  // First load uses refresh=false to serve cached data INSTANTLY (the
  // backend has already applied the strict lifecycle gate + sweep on
  // /matches/live regardless of refresh). The interval polls with
  // refresh=true to keep data fresh.
  useEffect(() => {
    load(false);
    const ttlMs = (LIVE_CACHE_TTL_SEC[sport] || 60) * 1000;
    const id = setInterval(() => load(true), ttlMs);
    const onFocus = () => load(true);
    window.addEventListener('focus', onFocus);
    return () => { clearInterval(id); window.removeEventListener('focus', onFocus); };
  }, [load, sport]);

  // Local heartbeat ticker — every 30s we revalidate the items already in
  // state. If a card's heartbeat has aged past the TTL while the user sat
  // on the page we hide it instantly without waiting for the next /live
  // call (key UX fix for "page sitting open shows 90'+15 ghosts").
  useEffect(() => {
    const id = setInterval(() => {
      setItems((prev) => {
        const next = prev.filter((m) => validLiveMatch(m));
        if (next.length !== prev.length) {
          console.log('[LIVE_TICKER_EXPIRY]', { removed: prev.length - next.length, sport });
        }
        return next;
      });
    }, 30_000);
    return () => clearInterval(id);
  }, [sport]);

  const isFootball = sport === 'football';
  const supportsLiveAnalytics = sport === 'football' || sport === 'basketball';
  // For football, apply the Big Five filter unless the user disabled it.
  // For NBA/MLB we always show everything (there is no equivalent allowlist).
  const visibleItems = useMemo(() => {
    if (!isFootball || !bigFiveOnly) return items;
    return items.filter((m) => isBigFive(m));
  }, [items, isFootball, bigFiveOnly]);
  const hiddenCount = isFootball && bigFiveOnly ? items.length - visibleItems.length : 0;

  const runLiveAnalysis = async () => {
    if (running) return;
    setRunning(true);
    setLivePicks([]);
    try {
      const r = await api.post('/analysis/run', {
        refresh: false,
        include_live: true,
        live_only: true,
        big_five_only: isFootball,
        max_matches: 6,
        sport,
        background: true,
      });
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Failed to start analysis' : 'No se pudo iniciar el análisis'));
      setRunning(false);
    }
  };

  const onJobComplete = (result) => {
    setActiveJobId(null);
    setRunning(false);
    const payload = result?.payload || result?.result || result || {};
    const picks = (payload?.picks || []).slice();
    setLivePicks(picks);
    setLiveRunGeneratedAt(result?.generated_at || payload?._generated_at || new Date().toISOString());
    setLiveRunMatchesAnalyzed(result?.matches_analyzed || payload?.summary?.total_analyzed || 0);
    if (picks.length === 0) {
      toast.info(lang === 'en' ? 'No live value found right now' : 'Sin valor en vivo por ahora');
    } else {
      toast.success(
        lang === 'en'
          ? `${picks.length} live pick${picks.length > 1 ? 's' : ''} found`
          : `${picks.length} pick${picks.length > 1 ? 's' : ''} en vivo encontrado${picks.length > 1 ? 's' : ''}`,
      );
    }
  };

  const onJobError = (err) => {
    setActiveJobId(null);
    setRunning(false);
    toast.error(err?.message || (lang === 'en' ? 'Analysis failed' : 'El análisis falló'));
  };

  const ctaLabel = isFootball
    ? (lang === 'en' ? 'Analyze live — Big Five only' : 'Analizar en vivo — solo 5 grandes')
    : (lang === 'en' ? 'Analyze live matches' : 'Analizar partidos en vivo');

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl md:text-4xl font-semibold tracking-tight">{t.live.title}</h1>
          <p className="text-sm text-muted-foreground mt-1">{t.live.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            data-testid="live-analyze-btn"
            onClick={runLiveAnalysis}
            disabled={running || visibleItems.length === 0}
            className="bg-emerald-500/15 text-emerald-200 border border-emerald-500/30 hover:bg-emerald-500/20"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Brain className="h-4 w-4 mr-2" />}
            {ctaLabel}
          </Button>
          <Button variant="secondary" data-testid="live-refresh-btn" onClick={() => load(true)} disabled={refreshing}>
            {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {/* Analysis Progress Modal — reuses the dashboard one so the UX feels consistent */}
      {activeJobId && (
        <AnalysisProgressModal
          jobId={activeJobId}
          onClose={() => { setActiveJobId(null); setRunning(false); }}
          onComplete={onJobComplete}
          onError={onJobError}
        />
      )}

      {/* Live picks block (rendered first when present so the user sees the analysis above the stats) */}
      {livePicks.length > 0 && (
        <section data-testid="live-picks-section" className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-emerald-300" />
              <h2 className="text-lg font-semibold tracking-tight">
                {isFootball
                  ? (lang === 'en' ? 'Live picks — Big Five' : 'Picks en vivo — 5 grandes')
                  : (lang === 'en' ? 'Live picks' : 'Picks en vivo')}
              </h2>
              <span className="text-[10.5px] font-mono-tabular text-muted-foreground bg-secondary/40 px-1.5 py-0.5 rounded">
                {livePicks.length}
              </span>
            </div>
            <div className="text-[11px] text-muted-foreground">
              {lang === 'en' ? 'Analyzed' : 'Analizados'}: {liveRunMatchesAnalyzed}
              {liveRunGeneratedAt && ` · ${new Date(liveRunGeneratedAt).toLocaleTimeString(lang === 'en' ? 'en-US' : 'es-ES', { hour: '2-digit', minute: '2-digit' })}`}
            </div>
          </div>
          <div className="grid gap-3">
            {livePicks.map((p, i) => (
              <MatchCard key={p.match_id || i} pick={p} idx={i} sport={sport} />
            ))}
          </div>
        </section>
      )}

      {/* Live stats list */}
      <section className="space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="text-base font-medium tracking-tight text-muted-foreground uppercase letter-spacing-wider">
            {lang === 'en' ? 'Live now' : 'En curso ahora'}
          </h2>
          <span className="text-[10.5px] font-mono-tabular text-muted-foreground bg-secondary/40 px-1.5 py-0.5 rounded" data-testid="live-count-pill">
            {visibleItems.length}{hiddenCount > 0 ? `/${items.length}` : ''}
          </span>

          {/* Big Five filter toggle (only meaningful for football) */}
          {isFootball && items.length > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant={bigFiveOnly ? 'secondary' : 'ghost'}
                onClick={() => setBigFiveOnly(true)}
                disabled={bigFiveOnly}
                data-testid="live-big-five-toggle"
                className={
                  bigFiveOnly
                    ? 'h-7 px-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/30 hover:bg-amber-500/15'
                    : 'h-7 px-2 text-[11px] text-muted-foreground hover:text-foreground'
                }
              >
                <TrophyIcon className="h-3.5 w-3.5 mr-1" />
                {t.live.bigFiveOnly}
              </Button>
              <Button
                type="button"
                size="sm"
                variant={!bigFiveOnly ? 'secondary' : 'ghost'}
                onClick={() => setBigFiveOnly(false)}
                disabled={!bigFiveOnly}
                data-testid="live-show-all-toggle"
                className={
                  !bigFiveOnly
                    ? 'h-7 px-2 text-[11px] text-cyan-200 bg-cyan-500/10 border border-cyan-500/30 hover:bg-cyan-500/15'
                    : 'h-7 px-2 text-[11px] text-muted-foreground hover:text-foreground'
                }
              >
                {t.live.showAll}
              </Button>
            </div>
          )}
        </div>

        {/* Hint when filter is hiding matches */}
        {isFootball && bigFiveOnly && hiddenCount > 0 && visibleItems.length > 0 && (
          <p className="text-[11px] text-muted-foreground italic" data-testid="live-filter-hint">
            {t.live.filteredHint.replace('{hidden}', String(hiddenCount))}
          </p>
        )}

        {loading && <div className="grid gap-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}</div>}

        {!loading && items.length === 0 && (
          <div className="rounded-xl border border-dashed border-border bg-card/40 p-8 text-center" data-testid="live-empty">
            <p className="text-sm text-muted-foreground">{t.live.noLive}</p>
          </div>
        )}

        {!loading && items.length > 0 && visibleItems.length === 0 && isFootball && bigFiveOnly && (
          <div className="rounded-xl border border-dashed border-amber-500/30 bg-amber-500/5 p-6 text-center" data-testid="live-big-five-empty">
            <p className="text-sm text-amber-200">{t.live.noLiveBigFive}</p>
          </div>
        )}

        <div className="grid gap-3">
          {visibleItems.map((m) => {
            const live = m.live_stats || {};
            const h = live.home_stats || {};
            const a = live.away_stats || {};
            const lstate = m._live_state;
            const fresh = m._freshness;
            // Hide cards whose freshness dipped below 30 since the last
            // /live fetch (defence-in-depth — the backend filters these
            // out but we re-check here for any that aged in transit).
            if (fresh && fresh.score < 30) {
              return null;
            }
            return (
              <div
                key={m.match_id}
                className={`card-glow rounded-xl border bg-card p-4 flex flex-col gap-2 ${
                  fresh && fresh.score < 50 ? 'border-amber-500/30' : 'border-border/80'
                }`}
                data-testid={`live-row-${m.match_id}`}
                data-live-state={lstate?.state}
                data-freshness-label={fresh?.label}
              >
                {/* Top portion is clickable → match detail */}
                <Link to={`/match/${m.match_id}`} className="flex flex-col gap-2 -m-4 p-4 mb-0 hover:bg-secondary/10 rounded-t-xl transition-colors">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      {/* Backend-derived live state badge takes priority
                          over the static LivePulse — it tells us LIVE_ACTIVE
                          vs LIVE_LATE vs GARBAGE_TIME vs HT. */}
                      {lstate ? (
                        <LiveStateBadge liveState={lstate} lang={lang} testId={`live-state-${m.match_id}`} />
                      ) : (
                        <LivePulse minute={live.minute} label={t.match.livePill} />
                      )}
                      <span className="text-xs text-muted-foreground">{m.league}</span>
                      {fresh && (
                        <LiveFreshnessBadge
                          freshness={fresh}
                          lang={lang}
                          testId={`live-freshness-${m.match_id}`}
                        />
                      )}
                      {m._provenance && (
                        <ProvenanceBadge
                          provenance={m._provenance}
                          lang={lang}
                          testId={`live-provenance-${m.match_id}`}
                        />
                      )}
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
                {/* P3.1 — Copilot Card (HumanLiveInterpreter): coach-voice
                    recommendation FIRST, then supporting metrics. Always
                    renders when _live_interpreter is present so the user
                    sees a decision before raw numbers. */}
                {supportsLiveAnalytics && m._live_interpreter && (
                  <LiveCopilotCard
                    interpreter={m._live_interpreter}
                    lang={lang}
                    testId={`live-copilot-${m.match_id}`}
                  />
                )}
                {/* P3 — Live xG/Threat/Pressure auto-analysis strip
                    (kloppy/socceraction/soccer_xg inspired). Now the
                    SUPPORTING evidence below the Copilot recommendation. */}
                {supportsLiveAnalytics && m._live_analysis && (
                  <LiveAnalysisStrip
                    analysis={m._live_analysis}
                    lang={lang}
                    testId={`live-analysis-${m.match_id}`}
                  />
                )}
                {/* Phase 10 / P4 — Live Re-Eval panel for football AND
                    basketball. Lives OUTSIDE the Link so its inputs don't
                    navigate away. Suppress for GARBAGE_TIME / stale. */}
                {supportsLiveAnalytics && lstate && ['LIVE_ACTIVE', 'LIVE_LATE', 'HT'].includes(lstate.state) && (
                  <LiveReevalPanel match={m} lang={lang} sport={sport} />
                )}
                {supportsLiveAnalytics && lstate && lstate.state === 'GARBAGE_TIME' && (
                  <div className="rounded-md border border-orange-500/30 bg-orange-500/5 px-3 py-2 text-[11px] text-orange-200" data-testid={`live-garbage-warning-${m.match_id}`}>
                    {lang === 'en'
                      ? 'Garbage time — live re-evaluation disabled. No actionable value remaining.'
                      : 'Tiempo muerto — reevaluación live deshabilitada. Sin valor accionable.'}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Archived live (collapsed) — partidos que se descartaron por
            stale/finished. Útil como audit trail; no se muestra por
            defecto. */}
        {archivedCount > 0 && (
          <div className="mt-4 rounded-lg border border-dashed border-slate-500/30 bg-slate-500/5">
            <button
              type="button"
              onClick={() => setShowArchived((v) => !v)}
              className="w-full flex items-center justify-between gap-2 px-3 py-2 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              data-testid="live-archived-toggle"
            >
              <span className="inline-flex items-center gap-1.5">
                <Archive className="h-3 w-3" />
                {lang === 'en'
                  ? `Archived live (${archivedCount})`
                  : `Live archivados (${archivedCount})`}
              </span>
              <span className="opacity-60">{showArchived ? '−' : '+'}</span>
            </button>
            {showArchived && archivedItems.length > 0 && (
              <ul className="px-3 pb-3 space-y-1 text-[11px] text-muted-foreground/80" data-testid="live-archived-list">
                {archivedItems.slice(0, 10).map((m) => (
                  <li key={m.match_id} className="font-mono-tabular">
                    · {m.match_id} · {m.home_team?.name} vs {m.away_team?.name}
                    {' '}<span className="opacity-60">[{m.status_short || '—'}]</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
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
