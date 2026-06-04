import { useEffect, useState, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, Clock, AlertOctagon, ShieldCheck, BadgeCheck, ThumbsDown, Equal } from 'lucide-react';
import { useI18n, sportTerms } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from 'sonner';
import { MotivationBadge, MotivationContextBlock } from '@/components/MotivationBadge';
import { FreshnessBadge } from '@/components/FreshnessBadge';
import { LivePulse } from '@/components/LivePulse';
import { ConfidenceMeter, ConfidenceIntelligenceCard } from '@/components/ConfidenceMeter';
import { OddsComparisonTable } from '@/components/OddsComparisonTable';
import { LineMovement } from '@/components/LineMovement';
import { MatchIntelligencePanel } from '@/components/MatchIntelligencePanel';
import { MoneyballPanel } from '@/components/MoneyballPanel';
import { MLBMatchupPanel } from '@/components/MLBMatchupPanel';
import { MLBLiveIntelPanel } from '@/components/MLBLiveIntelPanel';
import { MLBScriptPanel } from '@/components/MLBScriptPanel';
import { MLBLiveScoreboard } from '@/components/MLBLiveScoreboard';
import { InjuryIntelligencePanel } from '@/components/InjuryIntelligencePanel';
import { LivePreMatchComparisonPanel } from '@/components/LivePreMatchComparisonPanel';
import { useLiveMatchDetail } from '@/hooks/useLiveMatchDetail';
import { LiveTerritorialControlPanel } from '@/components/LiveTerritorialControlPanel';
import { formatDateTime, humanizeSelection } from '@/lib/format';

export default function MatchDetailPage() {
  const { id } = useParams();
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [match, setMatch] = useState(null);
  const [pickRun, setPickRun] = useState(null);
  const [loading, setLoading] = useState(true);
  const [marking, setMarking] = useState(false);

  const sport = match?.sport || 'football';
  const terms = sportTerms(lang, sport);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // 1) Fetch the match first so we know its sport, then fetch /picks/today
      //    for THAT sport. This makes the Acciones panel (Gané/Perdí/Push)
      //    work for NBA + MLB picks, not just football.
      const m = await api.get(`/matches/${id}`);
      setMatch(m.data);
      const matchSport = (m.data?.sport || 'football').toLowerCase();
      try {
        const p = await api.get('/picks/today', { params: { sport: matchSport } });
        setPickRun(p.data?.pick_run || null);
      } catch {
        setPickRun(null);
      }
    } catch (e) {
      toast.error('Error');
    } finally { setLoading(false); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const llmPick = (pickRun?.payload?.picks || []).find((p) => String(p.match_id) === String(id));

  // ── Live state (MLB only — hook self-gates for other sports) ────────
  // Refreshes every 30s while the game is in progress so the user sees
  // a live scoreboard instead of a stale `is_live=false` from ingestion.
  const liveCtx = useLiveMatchDetail(id, match?.sport, { enabled: !!match });
  const liveStateRaw = liveCtx?.state || 'loading';
  // The hook returns "no-live-data" for non-baseball sports — keep
  // showing the upcoming/finished badge in that case (no banner).
  const isBaseball = match?.sport === 'baseball';
  const showLiveScoreboard = isBaseball && (
    liveStateRaw === 'live-data-ready' ||
    liveStateRaw === 'live-data-partial' ||
    liveStateRaw === 'final' ||
    (match?.is_live && liveStateRaw === 'loading')
  );
  // Treat the match as live whenever the live hook says so — overrides
  // any stale `is_live=false` cached on the doc.
  const effectiveIsLive = isBaseball
    ? (liveStateRaw === 'live-data-ready' || liveStateRaw === 'live-data-partial')
    : !!match?.is_live;

  const markPick = async (outcome) => {
    if (!llmPick || !pickRun) return;
    setMarking(true);
    try {
      // Parse odds from odds_range (e.g., "1.25-1.45" -> midpoint, or "1.70" -> 1.70)
      let oddsValue = null;
      const range = llmPick.recommendation?.odds_range;
      if (range) {
        const nums = String(range).match(/\d+\.?\d*/g) || [];
        const parsed = nums.map(Number).filter((n) => n > 1 && n < 10);
        if (parsed.length === 2) oddsValue = (parsed[0] + parsed[1]) / 2;
        else if (parsed.length === 1) oddsValue = parsed[0];
      }
      await api.post('/picks/track', {
        run_id: pickRun.id,
        match_id: llmPick.match_id,
        market: llmPick.recommendation?.market,
        selection: llmPick.recommendation?.selection,
        confidence_score: llmPick.recommendation?.confidence_score || 0,
        outcome,
        odds: oddsValue,
        league: llmPick.league,
        match_label: llmPick.match_label,
        sport,
      });
      toast.success(outcome === 'won' ? 'Pick marcado como Gané' : outcome === 'lost' ? 'Pick marcado como Perdí' : 'Pick marcado como Push');
    } catch (e) { toast.error(e?.response?.data?.detail || 'Error'); }
    finally { setMarking(false); }
  };

  if (loading) return <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8"><Skeleton className="h-64 rounded-xl" /></div>;
  if (!match) return null;

  const home = match.home_team;
  const away = match.away_team;
  const odds = (match.odds_snapshots || [])[0];
  const live = match.live_stats;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 md:py-8 space-y-6">
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="text-muted-foreground" data-testid="match-back-btn"><ArrowLeft className="h-4 w-4 mr-1" />{t.match.backToList}</Button>

      {/* Header */}
      <div className="card-glow rounded-xl border border-border/80 bg-card p-5 flex flex-col gap-3">
        <div className="flex items-center flex-wrap gap-2">
          {effectiveIsLive ? <LivePulse minute={live?.minute} label={t.match.livePill} /> : (
            liveStateRaw === 'final' ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/40 bg-cyan-500/15 text-cyan-100 text-[11px] font-semibold" data-testid="match-final-pill">
                {lang === 'en' ? 'FINAL' : 'FINAL'}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-200 text-[11px] font-semibold"><Clock className="h-3 w-3" />{t.match.upcomingPill}</span>
            )
          )}
          <span className="text-xs text-muted-foreground">{typeof match.league === 'object' ? (match.league?.name || match.league?.id || '') : match.league}</span>
          <span className="text-xs text-muted-foreground mono font-mono-tabular">{formatDateTime(match.kickoff_iso, lang)}</span>
          {match.venue && <span className="text-xs text-muted-foreground">· {match.venue}</span>}
          <div className="ml-auto flex items-center gap-2">
            <FreshnessBadge status={odds?.available ? 'fresh' : 'missing'} kind="odds" />
            <FreshnessBadge status={home?.context?.position ? 'fresh' : 'stale'} kind="ctx" />
          </div>
        </div>
        <div className="grid grid-cols-3 items-center gap-3">
          <div className="text-left"><div className="text-xs text-muted-foreground">{t.match.home}</div><div className="text-xl font-semibold mt-0.5">{home?.name}</div></div>
          {effectiveIsLive ? (
            <div className="text-center mono font-mono-tabular text-4xl font-semibold" data-testid="header-live-score">
              {(liveCtx?.live?.score?.home ?? live?.score?.home ?? 0)} <span className="text-muted-foreground">–</span> {(liveCtx?.live?.score?.away ?? live?.score?.away ?? 0)}
            </div>
          ) : (
            <div className="text-center text-xs text-muted-foreground"><div className="text-[10px] uppercase tracking-wide">{t.match.kickoff}</div><div className="mono font-mono-tabular text-foreground text-sm">{formatDateTime(match.kickoff_iso, lang)}</div></div>
          )}
          <div className="text-right"><div className="text-xs text-muted-foreground">{t.match.away}</div><div className="text-xl font-semibold mt-0.5">{away?.name}</div></div>
        </div>
      </div>

      {/* MLB Live Scoreboard — sport-specific live state with polling.
          Self-renders when state is live-ready/partial/final OR loading
          while the doc says live. For other sports / pre-game, returns
          null and the regular kickoff timer above is shown instead. */}
      {showLiveScoreboard && (
        <MLBLiveScoreboard
          homeName={home?.name}
          awayName={away?.name}
          live={liveCtx.live}
          state={liveStateRaw}
          lastFetch={liveCtx.lastFetch}
          refreshing={liveCtx.refreshing}
          onRefresh={liveCtx.refresh}
          lang={lang}
        />
      )}

      <div className="grid lg:grid-cols-12 gap-6">
        {/* Main */}
        <div className="lg:col-span-8 space-y-6">
          {/* Injury Intelligence (basketball only, Phase 1) — high-priority
              risk layer rendered ABOVE the pick so users see absences and
              market warnings before reading the recommendation. The panel
              self-hides for non-basketball sports and when no data is
              available, so it adds zero noise to MLB / football flows. */}
          <InjuryIntelligencePanel
            matchId={match?.match_id ?? match?.id}
            sport={sport}
            lang={lang}
          />
          {/* Live ↔ pregame comparison (always above the pick so the user
              sees the verdict before the pick itself when the market is
              already settled). */}
          {match?.script_comparison && (
            <LivePreMatchComparisonPanel
              comparison={match.script_comparison}
              lang={lang}
            />
          )}
          {/* LLM pick or fallback — DEMOTED when settled / not actionable. */}
          {(() => {
            const cmp = match?.script_comparison || {};
            const ps  = cmp.pregame_pick_status;
            const settled = ['already_won', 'already_lost', 'not_actionable'].includes(ps);
            // Differentiated header copy — was a single generic
            // "ya cumplido / no accionable" which conflated wins, losses
            // and unresolved cases. Now each branch is unambiguous.
            const headerByStatus = {
              already_won:    lang === 'en' ? 'Pregame pick — already won'        : 'Pick pregame · ya ganó',
              already_lost:   lang === 'en' ? 'Pregame pick — already lost'       : 'Pick pregame · ya perdió',
              not_actionable: lang === 'en' ? 'Pregame pick — not actionable now' : 'Pick pregame · no accionable',
            };
            const toneByStatus = {
              already_won:    'border-cyan-500/30 bg-cyan-500/[0.06]   text-cyan-200',
              already_lost:   'border-rose-500/30 bg-rose-500/[0.06]   text-rose-200',
              not_actionable: 'border-amber-500/30 bg-amber-500/[0.06] text-amber-200',
            };
            if (llmPick && settled) {
              const tone = toneByStatus[ps] || toneByStatus.not_actionable;
              return (
                <div
                  className={`rounded-xl border p-5 space-y-3 ${tone}`}
                  data-testid="llm-pick-block-settled"
                >
                  <div className="text-[11px] uppercase tracking-wide font-semibold">
                    {headerByStatus[ps] || headerByStatus.not_actionable}
                  </div>
                  <div className="flex items-center justify-between gap-3 flex-wrap opacity-90">
                    <div>
                      <div className="text-lg font-semibold flex items-center flex-wrap gap-2">
                        <span className="px-2 py-0.5 rounded-md bg-white/10 border border-white/20 text-xs">
                          {llmPick.recommendation?.market}
                        </span>
                        {humanizeSelection(llmPick.recommendation?.selection, llmPick.recommendation?.market, home?.name, away?.name, lang, sport)}
                      </div>
                      {llmPick.recommendation?.odds_range && (
                        <div className="text-xs text-muted-foreground mt-1">
                          {t.match.oddsRange}: <span className="text-foreground mono font-mono-tabular">{llmPick.recommendation?.odds_range}</span>
                        </div>
                      )}
                    </div>
                    <ConfidenceMeter
                      score={llmPick.recommendation?.confidence_score || 0}
                      size="inline"
                    />
                  </div>
                </div>
              );
            }
            return llmPick ? (
            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-5 space-y-4" data-testid="llm-pick-block">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-emerald-200">{t.match.recommendation}</div>
                  <div className="text-lg font-semibold mt-1 flex items-center flex-wrap gap-2">
                    <span className="px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-200 border border-emerald-500/30 text-xs">{llmPick.recommendation?.market}</span>
                    {humanizeSelection(llmPick.recommendation?.selection, llmPick.recommendation?.market, home?.name, away?.name, lang, sport)}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">{t.match.oddsRange}: <span className="text-foreground mono font-mono-tabular">{llmPick.recommendation?.odds_range}</span></div>
                </div>
                <ConfidenceMeter score={llmPick.recommendation?.confidence_score || 0} size="inline" />
              </div>
              {llmPick.reasoning && <p className="text-sm text-muted-foreground leading-relaxed border-l-2 border-cyan-500/40 pl-3">{llmPick.reasoning}</p>}
              {(llmPick.risks || []).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {llmPick.risks.map((r, i) => (
                    <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-red-500/10 text-red-200 border border-red-500/25"><AlertOctagon className="h-3 w-3" />{r}</span>
                  ))}
                </div>
              )}
              {llmPick.cash_out && (
                <div className="text-xs flex items-center gap-1.5 text-cyan-200"><ShieldCheck className="h-3.5 w-3.5" />{t.match.cashOut}: <span className="text-foreground">{llmPick.cash_out}</span></div>
              )}

              {/* Full intelligence card */}
              <ConfidenceIntelligenceCard pick={llmPick} sport={sport} />

              {/* Motivation context block */}
              {llmPick.motivation && (
                <MotivationContextBlock
                  motivation={llmPick.motivation}
                  motivationState={llmPick.motivation_state}
                  pressureState={llmPick.pressure_state}
                  homeName={home?.name}
                  awayName={away?.name}
                  lang={lang}
                />
              )}

              {/* Decision Intelligence Panel (radar + drivers timeline + markets matrix + risk breakdown) */}
              <MatchIntelligencePanel pick={llmPick} sport={sport} match={match} />
              <LineMovement movement={llmPick.key_data?.line_movement} />

              {/* Universal Moneyball Layer — subsumes the prior Market Edge Panel.
                  Renders classification + EV math + fragility + overreaction +
                  trap/undervalued signals + consolidated "why this can fail". */}
              {(llmPick._moneyball || llmPick._market_edge) && (
                <MoneyballPanel
                  moneyball={llmPick._moneyball}
                  marketEdge={llmPick._market_edge}
                  lang={lang}
                />
              )}

              {/* MLB-specific structural matchup (pitchers / bullpen / offense) */}
              {sport === 'baseball' && match?.mlb_matchup && (
                <MLBMatchupPanel matchup={match.mlb_matchup} lang={lang} />
              )}

              {/* MLB Script Panel — surfaces v2/v5/v6 narrative plus the new
                  contextual layers (M1 active series, M3 series degradation,
                  M4 verification, M5 park factor, GAP #1 IL penalty). Always
                  renders when the pick was generated by the MLB pipeline
                  (presence of _mlb_script_v2 or active_series_context). */}
              {sport === 'baseball' && (llmPick._mlb_script_v2 || llmPick.active_series_context || llmPick.il_penalty) && (
                <MLBScriptPanel
                  scriptV2={llmPick._mlb_script_v2 || {}}
                  scriptV5={llmPick._mlb_script_v5 || null}
                  parlay={llmPick._mlb_parlay_context || null}
                  underFragilityWarning={llmPick.under_fragility_warning || null}
                  scriptPickMismatchNarrative={llmPick.script_pick_mismatch_narrative || null}
                  scriptPickMismatchDetails={llmPick.script_pick_mismatch_details || null}
                  biasPenaltyMeta={llmPick.bias_penalty_applied ? (llmPick.bias_penalty_meta || { triggered: true }) : null}
                  activeSeriesContext={llmPick.active_series_context || null}
                  seriesDegradation={llmPick.series_degradation || null}
                  modelVerification={llmPick.model_verification || null}
                  activeSeriesBlock={llmPick.active_series_block || null}
                  chosenMarket={llmPick.recommendation?.market || null}
                  ilPenalty={llmPick.il_penalty || null}
                  testId={`mlb-script-detail-${id}`}
                />
              )}

              {/* MLB-V8 Live Intelligence (Volatility + Script Break + Cashout).
                  Triple gate enforced inside the component:
                  sport==='baseball' && (effective live state) && llmPick._mlb_script_v3.
                  This means the panel ONLY appears for MLB picks that PASSED
                  the pregame filter AND the match is currently live — never
                  for the full live list.

                  P4 polish: we now pass `liveDetail` (canonical snapshot
                  from /api/matches/{id}/live-refresh) so the panel can
                  detect inning/score/is_live even when the matches doc
                  hasn't been refreshed yet. */}
              <MLBLiveIntelPanel
                sport={sport}
                match={match}
                llmPick={llmPick}
                liveDetail={liveCtx?.live}
                effectiveIsLive={effectiveIsLive}
              />
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border bg-card/40 p-5" data-testid="no-llm-pick">
              <p className="text-sm text-muted-foreground">{t.match.noLLMPick}</p>
              <Link to="/" className="text-cyan-300 text-sm hover:text-cyan-200">{t.match.generateForMatch}</Link>
            </div>
          );
          })()}

          {/* Live Territorial Control + Corner Intelligence (FOOTBALL ONLY).
              The component self-gates: it returns null unless
              sport==='football' && match.is_live. Renders the 📐 Control
              Territorial card, the Corner Intelligence score (always
              visible) + recommendation (surfaced only above threshold),
              and the dynamic live-market ranking. */}
          <LiveTerritorialControlPanel sport={sport} match={match} t={t} />

          {/* Motivational context */}
          <section className="rounded-xl border border-border bg-card p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.match.motivationCtx}</h3>
            <div className="grid sm:grid-cols-2 gap-3">
              <TeamMotivationBlock team={home} side="home" llmPick={llmPick} lang={lang} t={t} />
              <TeamMotivationBlock team={away} side="away" llmPick={llmPick} lang={lang} t={t} />
            </div>
          </section>

          {/* Key data */}
          <section className="rounded-xl border border-border bg-card p-5">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.match.keyData}</h3>
            <div className="grid sm:grid-cols-2 gap-4">
              <KeyDataBlock team={home} t={t} terms={terms} lang={lang} />
              <KeyDataBlock team={away} t={t} terms={terms} lang={lang} />
            </div>
          </section>

          {/* H2H */}
          {match.h2h_recent?.length > 0 && (
            <section className="rounded-xl border border-border bg-card p-5">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">{t.match.headToHead}</h3>
              <div className="space-y-1">
                {match.h2h_recent.map((h, i) => (
                  <div key={i} className="flex items-center justify-between text-sm border-b border-border/50 last:border-0 py-1.5" data-testid={`h2h-row-${i}`}>
                    <span className="text-muted-foreground mono font-mono-tabular text-xs">{new Date(h.date).toLocaleDateString(lang === 'es' ? 'es-ES' : 'en-US')}</span>
                    <span className="truncate">{h.home}</span>
                    <span className="mono font-mono-tabular font-semibold mx-2">{h.score}</span>
                    <span className="truncate text-right">{h.away}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Live stats (if live) */}
          {match.is_live && live && (
            <section className="rounded-xl border border-border bg-card p-5">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground mb-3">Live Stats</h3>
              <LiveStatsTable home={live.home_stats} away={live.away_stats} t={t} />
            </section>
          )}
        </div>

        {/* Right rail */}
        <div className="lg:col-span-4 space-y-6">
          <section>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">{t.match.oddsTable}</div>
            <OddsComparisonTable snapshot={odds} />
          </section>
          {llmPick && (
            <section className="rounded-xl border border-border bg-card p-4">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground mb-2">{t.match.actions}</div>
              <div className="flex flex-col gap-2">
                <Button data-testid="mark-pick-won-button" onClick={() => markPick('won')} disabled={marking} className="bg-emerald-500/20 text-emerald-200 border border-emerald-500/40 hover:bg-emerald-500/30"><BadgeCheck className="h-4 w-4 mr-2" />{t.match.markWon}</Button>
                <Button data-testid="mark-pick-lost-button" onClick={() => markPick('lost')} disabled={marking} variant="secondary" className="bg-red-500/15 text-red-200 border border-red-500/30 hover:bg-red-500/25"><ThumbsDown className="h-4 w-4 mr-2" />{t.match.markLost}</Button>
                <Button data-testid="mark-pick-push-button" onClick={() => markPick('push')} disabled={marking} variant="secondary" className=""><Equal className="h-4 w-4 mr-2" />{t.match.markPush}</Button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function TeamMotivationBlock({ team, side, llmPick, lang, t }) {
  const llmMot = llmPick?.motivation?.[side];
  const level = llmMot?.level || 3;
  return (
    <div className="rounded-lg border border-border bg-secondary/30 p-3" data-testid={`motivation-${side}`}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{team?.name}</span>
        <MotivationBadge level={level} lang={lang} />
      </div>
      {llmMot?.reason && <p className="text-xs text-muted-foreground mt-2">{llmMot.reason}</p>}
      {team?.context?.description && <p className="text-[11px] text-muted-foreground/80 mt-1">{team.context.description}</p>}
    </div>
  );
}

function KeyDataBlock({ team, t, terms, lang }) {
  const c = team?.context || {};
  // Sport-aware labels: for football we keep "Goles a favor/contra"; for NBA/MLB
  // the matching field is `points_for_avg/points_against_avg` populated by
  // normalize_team_context_generic. Fall back to football fields when present.
  const scoredFor = c.points_for_avg ?? c.goals_for_avg;
  const scoredAgainst = c.points_against_avg ?? c.goals_against_avg;
  const scoreUnit = terms?.scoreUnit || (lang === 'es' ? 'goles' : 'goals');
  const labelFor = lang === 'es' ? `${scoreUnit.charAt(0).toUpperCase()}${scoreUnit.slice(1)} a favor (prom.)` : `${scoreUnit.charAt(0).toUpperCase()}${scoreUnit.slice(1)} for (avg.)`;
  const labelAgainst = lang === 'es' ? `${scoreUnit.charAt(0).toUpperCase()}${scoreUnit.slice(1)} en contra (prom.)` : `${scoreUnit.charAt(0).toUpperCase()}${scoreUnit.slice(1)} against (avg.)`;

  const items = [
    { label: t.match.form, value: c.form_last_5 || (c.wins_total != null ? `${c.wins_total}W-${c.losses_total ?? '—'}L` : '—') },
    { label: t.match.position, value: c.position ?? '—' },
    { label: labelFor, value: scoredFor != null ? Number(scoredFor).toFixed(2) : '—' },
    { label: labelAgainst, value: scoredAgainst != null ? Number(scoredAgainst).toFixed(2) : '—' },
    { label: t.match.injuries, value: c.injuries_count ?? '—' },
  ];
  return (
    <div className="rounded-lg border border-border bg-secondary/30 p-3">
      <div className="text-sm font-semibold mb-2">{team?.name}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        {items.map((it, i) => (
          <div key={i} className="flex items-center justify-between">
            <span className="text-muted-foreground">{it.label}</span>
            <span className="mono font-mono-tabular text-foreground">{String(it.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LiveStatsTable({ home, away, t }) {
  const keys = [
    ['Ball Possession', t.live.possession],
    ['Total Shots', t.live.shots],
    ['Shots on Goal', t.live.shotsOn],
    ['Corner Kicks', t.live.corners],
    ['expected_goals', t.live.xg],
  ];
  return (
    <table className="w-full text-sm">
      <tbody>
        {keys.map(([k, label]) => (
          <tr key={k} className="border-t border-border/60">
            <td className="py-1.5 px-2 mono font-mono-tabular text-foreground text-right w-16">{(home || {})[k] ?? '—'}</td>
            <td className="py-1.5 px-3 text-center text-muted-foreground text-xs">{label}</td>
            <td className="py-1.5 px-2 mono font-mono-tabular text-foreground w-16">{(away || {})[k] ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
