import { useState } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { Clock, AlertOctagon, ShieldCheck, ChevronDown, Gauge, BookmarkPlus, BookmarkCheck, Loader2 } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { formatDateTime, tierClass, confidenceTier, humanizeSelection } from '@/lib/format';
import { ConfidenceMeter, ConfidenceIntelligenceCard } from './ConfidenceMeter';
import { MotivationBadge } from './MotivationBadge';
import { FreshnessBadge } from './FreshnessBadge';
import { LivePulse } from './LivePulse';
import { LineMovement } from './LineMovement';
import { deriveIntelligence, DRIVER_META } from '@/lib/intelligence';
import { MoneyballBadge } from './MoneyballPanel';
import { FootballQualityBadge } from './FootballQualityBadge';
import { ProtectedMarketBadge } from './ProtectedMarketBadge';
import { ProvenanceBadge } from './ProvenancePanel';
import { EditorialContextPanel } from './EditorialContextPanel';
import { HistoricalProfilePanel } from './HistoricalProfilePanel';
import { BoxScoreHydrateButton } from './BoxScoreHydrateButton';
import { LiveScriptRealityBadge } from './LiveScriptRealityBadge';
import { BullpenTrafficBadge } from './BullpenTrafficBadge';
import { SiegePressureBadge } from './SiegePressureBadge';
import { InningLambdaPanel } from './InningLambdaPanel';
import { SeriesFamiliarityBadge } from './SeriesFamiliarityBadge';
import { ExpectedRunsRangePanel } from './ExpectedRunsRangePanel';
import { TailRiskPanel } from './TailRiskPanel';
import { OffensiveInjuryImpactPanel } from './OffensiveInjuryImpactPanel';
import { EditorialSignalsPanel } from './EditorialSignalsPanel';
import { ExternalSourceEvidencePanel } from './ExternalSourceEvidencePanel';
import { SourcesConsultedPanel } from './SourcesConsultedPanel';
import { MLBScriptPanel } from './MLBScriptPanel';
import { MLBScriptV3Panel, MLBDiversityBadge, MLBBullpenSwapBadge, MLBOverSwapBadge, MLBFalseCompetitiveUnderdogBadge } from './MLBScriptV3Panel';
import { MLBAdvancedStatsPanel } from './MLBAdvancedStatsPanel';
import { FootballIntelligencePanel, FootballPatternMemoryPanel, FootballLiveVsPregamePanel } from './FootballMoneyballPanels';
import { FootballProfileCrossPropsPanel } from './FootballProfileCrossPropsPanel';
import { FootballTotalsModelPanel, FootballOverSupportPanel } from './FootballDcNbPanels';
import { LiveRecommendationTimeline } from './LiveRecommendationTimeline';
import { InlineManualOddsInput } from './InlineManualOddsInput';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { api } from '@/lib/api';

export function MatchCard({ pick, idx = 0, sport = 'football', runId = null }) {
  const { lang, t } = useI18n();
  const m = pick;
  const tier = confidenceTier(m.recommendation?.confidence_score || 0);
  const tierCls = tierClass(tier);
  const intel = deriveIntelligence(m, sport);
  const [expanded, setExpanded] = useState(false);
  const [pendingSaving, setPendingSaving] = useState(false);
  const [savedPending, setSavedPending] = useState(false);
  const visibleDrivers = (intel?.drivers || []).slice(0, 3);
  const remainingDrivers = Math.max(0, (intel?.drivers || []).length - visibleDrivers.length);

  // "Marcar para seguir" — store the pick with outcome=pending so the user
  // can settle it later from the History page, regardless of sport (NBA/MLB
  // games that end late at night, finals played on different days, etc.).
  const savePending = async () => {
    if (pendingSaving || !runId) return;
    setPendingSaving(true);
    try {
      // Parse a midpoint from "1.25-1.45" or a single odds value.
      let oddsValue = null;
      const range = m.recommendation?.odds_range;
      if (range) {
        const nums = String(range).match(/\d+\.?\d*/g) || [];
        const parsed = nums.map(Number).filter((n) => n > 1 && n < 10);
        if (parsed.length === 2) oddsValue = (parsed[0] + parsed[1]) / 2;
        else if (parsed.length === 1) oddsValue = parsed[0];
      }
      await api.post('/picks/track', {
        run_id: runId,
        match_id: m.match_id,
        market: m.recommendation?.market,
        selection: m.recommendation?.selection,
        confidence_score: m.recommendation?.confidence_score || 0,
        outcome: 'pending',
        odds: oddsValue,
        league: m.league,
        match_label: m.match_label,
        sport,
      });
      setSavedPending(true);
      toast.success(t.dashboard.savedAsPending);
    } catch (e) {
      toast.error(e?.response?.data?.detail || t.common.error);
    } finally {
      setPendingSaving(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: Math.min(idx * 0.04, 0.4) }}
      className="card-glow rounded-xl border border-border/80 bg-card p-4 md:p-5 flex flex-col gap-3"
      data-testid={`pick-card-${m.match_id}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {m.is_live ? <LivePulse minute={m.live_minute} label={t.match.livePill} /> : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-200 text-[11px] font-semibold">
                <Clock className="h-3 w-3" />{t.match.upcomingPill}
              </span>
            )}
            {m._carryover?.is_carryover && (
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-sky-500/40 bg-sky-500/10 text-sky-200 text-[10px] font-semibold uppercase tracking-wide"
                title={m._carryover?.reason || ''}
                data-testid={`pick-carryover-badge-${m.match_id}`}
              >
                <BookmarkCheck className="h-3 w-3" />
                {lang === 'en' ? 'Carryover' : 'Previo'}
              </span>
            )}
            <span className="text-[11px] text-muted-foreground">{m.league}</span>
            {/* Live-route badge — surfaced when the pregame orchestrator
                kept a game that was already in progress instead of
                dropping it. Lets the user know the analysis reflects
                mid-game conditions rather than purely pregame. */}
            {m.is_live_route && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide bg-rose-500/15 text-rose-200 border border-rose-500/30"
                data-testid={`pick-live-route-${m.match_id}`}
                title={lang === 'en' ? 'Analysis reflects in-progress game state' : 'Análisis basado en estado en curso del juego'}
              >
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-rose-300 animate-pulse" />
                {lang === 'en' ? 'LIVE' : 'EN VIVO'}
              </span>
            )}
            {/* Football Quality Badge (Phase 8) — only for football picks */}
            {sport === 'football' && m._football_quality && (
              <FootballQualityBadge
                quality={m._football_quality}
                lang={lang}
                testId={`pick-quality-badge-${m.match_id}`}
              />
            )}
            {/* MLB-TS1 Batch 2 — Multi-source coverage badge. Shows when
                a football fixture was confirmed by both API-Sports AND
                TheStatsAPI in the merge step. Strong signal that the
                fixture metadata is robust (two independent providers
                agree on teams + kickoff). */}
            {sport === 'football' && Array.isArray(m.external_sources_covered) && m.external_sources_covered.length >= 2 && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide bg-cyan-500/15 text-cyan-200 border border-cyan-500/30"
                data-testid={`pick-multisource-badge-${m.match_id}`}
                title={lang === 'en'
                  ? `Sources: ${m.external_sources_covered.map(s => s === 'api_sports' ? 'API-Sports' : s === 'thestatsapi' ? 'TheStatsAPI' : s).join(' + ')}`
                  : `Fuentes: ${m.external_sources_covered.map(s => s === 'api_sports' ? 'API-Sports' : s === 'thestatsapi' ? 'TheStatsAPI' : s).join(' + ')}`}
              >
                {lang === 'en' ? 'Multi-fuente' : 'Multi-fuente'}
              </span>
            )}
            {/* MLB-TS1 — TheStatsAPI provider badge. Surfaces when the live
                football aggregator found this fixture via TheStatsAPI
                (typically national-team / international matches that
                API-Sports missed). Tooltip in Spanish per user spec. */}
            {sport === 'football' && (
              m.external_source === 'thestatsapi' ||
              (Array.isArray(m.external_sources_covered) && m.external_sources_covered.includes('thestatsapi'))
            ) && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide bg-violet-500/15 text-violet-200 border border-violet-500/30"
                data-testid={`pick-thestatsapi-badge-${m.match_id}`}
                title={lang === 'en' ? 'Match data from TheStatsAPI' : 'Datos del partido vía TheStatsAPI'}
              >
                TheStatsAPI
              </span>
            )}
            {/* MLB-TS1 — National-team badge. Highlights international /
                national-team fixtures so users instantly identify e.g.
                World Cup qualifiers vs club competitions. */}
            {sport === 'football' && (m.is_national_team || m.is_international) && (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide bg-amber-500/15 text-amber-200 border border-amber-500/30"
                data-testid={`pick-national-team-badge-${m.match_id}`}
                title={lang === 'en' ? 'International / national-team competition' : 'Selecciones / competición internacional'}
              >
                {lang === 'en' ? 'Internacional' : 'Selecciones'}
              </span>
            )}
            <span className="text-[11px] text-muted-foreground">·</span>
            <span className="text-[11px] text-muted-foreground mono font-mono-tabular">{formatDateTime(m.kickoff_iso, lang)}</span>
          </div>
          <Link to={`/match/${m.match_id}`} className="text-lg font-semibold leading-tight hover:text-cyan-300 transition-colors" data-testid={`pick-title-${m.match_id}`}>
            {m.match_label}
          </Link>
          <div className="flex items-center gap-2 mt-1">
            <MotivationBadge level={m.motivation?.home?.level || 3} lang={lang} tooltip={`Home: ${m.motivation?.home?.reason || ''}`} />
            <span className="text-[10px] text-muted-foreground">vs</span>
            <MotivationBadge level={m.motivation?.away?.level || 3} lang={lang} tooltip={`Away: ${m.motivation?.away?.reason || ''}`} />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          {m._provenance && (
            <ProvenanceBadge
              provenance={m._provenance}
              lang={lang}
              testId={`pick-provenance-${m.match_id}`}
            />
          )}
          <FreshnessBadge status={m.data_freshness?.odds || 'fresh'} kind="odds" />
          <FreshnessBadge status={m.data_freshness?.context || 'fresh'} kind="ctx" />
        </div>
      </div>

      {/* Recommendation */}
      <div className="rounded-lg bg-secondary/40 border border-border p-3 flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{t.match.recommendation}</div>
          <div className="text-sm font-semibold mt-0.5 flex flex-wrap items-center gap-2">
            <span className="px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-200 border border-emerald-500/25 text-[11px]">{m.recommendation?.market}</span>
            <span className="text-foreground" data-testid={`pick-selection-${m.match_id}`}>{humanizeSelection(m.recommendation?.selection, m.recommendation?.market, m.home_team?.name || m.match_label?.split(/\s+vs\s+/i)[0], m.away_team?.name || m.match_label?.split(/\s+vs\s+/i)[1], lang, sport)}</span>
          </div>
          <div className="text-xs text-muted-foreground mt-1 flex items-center gap-3 flex-wrap">
            <span className="mono font-mono-tabular">{t.match.oddsRange}: <span className="text-foreground">{m.recommendation?.odds_range || '—'}</span></span>
            <LineMovement movement={m.key_data?.line_movement} />
            {(m._moneyball || m._market_edge) && (
              <MoneyballBadge
                moneyball={m._moneyball}
                marketEdge={m._market_edge}
                lang={lang}
                testId={`pick-moneyball-badge-${m.match_id}`}
              />
            )}
          </div>
          {/* Inline manual-odds entry — surfaces inside the recommendation
              row whenever an MLB pick was produced WITHOUT automatic odds
              (`odds_range` empty / "—") so the user can paste a bookie
              price on the spot instead of routing to the batch review
              panel. Baseball-only. */}
          {sport === 'baseball'
            && !m.recommendation?.odds_range
            && (m.id || m.match_id)
            ? (
              <div className="mt-2">
                <InlineManualOddsInput
                  pickId={m.id || m.match_id}
                  matchId={m.match_id}
                  gamePk={m.game_pk || m.gamePk}
                  homeTeam={m.home_team}
                  awayTeam={m.away_team}
                  commenceDate={(m.commence_time || '').slice(0, 10) || m.commence_date}
                  market={m.recommendation?.market}
                  line={m.recommendation?.line}
                  lang={lang}
                  testId={`pick-inline-manual-odds-${m.match_id}`}
                />
              </div>
            ) : null}
        </div>
        <ConfidenceMeter score={m.recommendation?.confidence_score || 0} size="inline" testId={`pick-conf-${m.match_id}`} />
      </div>

      {/* Phase 9 — Protected Alternative Market panel (Under 3.5/2.5 + DC
          combos). Only renders when the match was rescued by the alt-scan
          rather than coming from direct 1X2/DC analysis. */}
      {m._alternative_market && m._alternative_market_payload && (
        <ProtectedMarketBadge
          payload={m._alternative_market_payload}
          lang={lang}
          testId={`protected-market-${m.match_id}`}
        />
      )}

      {/* Reasoning */}
      {m.reasoning && (!Array.isArray(m.baseball_reasons) || m.baseball_reasons.length === 0) && (
        <p className="text-sm text-muted-foreground leading-relaxed border-l-2 border-cyan-500/40 pl-3">
          {m.reasoning}
        </p>
      )}

      {/* MLB-V7 — Diversity warning chip (baseball-only). Shows when this
          pick belongs to the day's dominant market. */}
      {sport === 'baseball' && m._mlb_script_v3_diversity?.is_dominant ? (
        <MLBDiversityBadge
          diversity={m._mlb_script_v3_diversity}
          testId={`mlb-diversity-${m.match_id}`}
        />
      ) : null}

      {/* F6A — Bullpen swap warning. Shown when the engine downgraded a
          Full Game Under and adopted F5 Under or a protected alternate
          line because bullpen risk was MEDIUM/HIGH. Baseball-only. */}
      {sport === 'baseball' && m.recommendation?.bullpen_swap_meta ? (
        <MLBBullpenSwapBadge
          meta={m.recommendation.bullpen_swap_meta}
          testId={`mlb-bullpen-swap-${m.match_id}`}
        />
      ) : null}

      {/* MLB-V6 — Over swap badge. Shown when the Market Competition step
          swapped a Full Game Under for an Over candidate because the
          offensive edge clearly dominated the current Under selection.
          Baseball-only. */}
      {sport === 'baseball' && (m.recommendation?.over_swap || m._mlb_over_discovery?.competition?.winner_side === 'OVER') ? (
        <MLBOverSwapBadge
          meta={m.recommendation}
          overDiscovery={m._mlb_over_discovery}
          testId={`mlb-over-swap-${m.match_id}`}
        />
      ) : null}

      {/* MLB-V7 — FALSE_COMPETITIVE_UNDERDOG badge. Shown when the engine
          detected the canonical failure mode (favorite top-offence +
          underdog weak bullpen + offensive gap ≥ 15). Two visual modes:
          BLOCK (severity=EXTREME, market swapped) and PENALTY (severity
          ∈ {MODERATE, HIGH}, confidence reduced).  Baseball-only. */}
      {sport === 'baseball' && m._mlb_false_competitive_underdog ? (
        <MLBFalseCompetitiveUnderdogBadge
          meta={m._mlb_false_competitive_underdog}
          pick={m}
          testId={`mlb-fcu-${m.match_id}`}
        />
      ) : null}

      {/* Risks + cash-out */}
      <div className="flex flex-wrap items-center gap-2">
        {(m.risks || []).slice(0, 3).map((r, i) => (
          <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-red-500/10 text-red-200 border border-red-500/25">
            <AlertOctagon className="h-3 w-3" />{r}
          </span>
        ))}
        {m.cash_out && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-md bg-cyan-500/10 text-cyan-200 border border-cyan-500/25 ml-auto">
            <ShieldCheck className="h-3 w-3" />{t.match.cashOut}: {m.cash_out}
          </span>
        )}
      </div>

      {/* Editorial context signals — split into "positive" (why the engine
          likes it) and "negative" (risk signals) so the user can audit
          both sides of the recommendation transparently. */}
      {Array.isArray(m.editorial_context_signals) && m.editorial_context_signals.length > 0 && (
        <div className="space-y-2">
          <EditorialSignalsPanel
            signals={m.editorial_context_signals}
            variant="compact"
            filter="positive"
            defaultOpen={false}
            testId={`pick-signals-positive-${m.match_id}`}
          />
          <EditorialSignalsPanel
            signals={m.editorial_context_signals}
            variant="compact"
            filter="negative"
            defaultOpen={false}
            testId={`pick-signals-negative-${m.match_id}`}
          />
        </div>
      )}

      {/* External source evidence — what external providers told us */}
      {Array.isArray(m.external_source_evidence) && m.external_source_evidence.length > 0 && (
        <ExternalSourceEvidencePanel
          evidence={m.external_source_evidence}
          testId={`pick-ext-sources-${m.match_id}`}
        />
      )}

      {/* Multi-source data ingestion — MLB lineup rescue layer */}
      {m.external_sources && (
        <SourcesConsultedPanel
          external={m.external_sources}
          testId={`pick-sources-consulted-${m.match_id}`}
          lang={lang}
        />
      )}

      {/* Track-pending action — works for every sport. Hidden if we don't
          have a runId (e.g. live picks rendered without a persisted run). */}
      {runId && (
        <div className="flex items-center justify-end pt-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={savePending}
            disabled={pendingSaving || savedPending}
            data-testid={`pick-pending-btn-${m.match_id}`}
            className={
              savedPending
                ? 'h-8 px-2 text-[11px] text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/10'
                : 'h-8 px-2 text-[11px] text-muted-foreground hover:text-foreground hover:bg-white/[0.06]'
            }
          >
            {pendingSaving ? (
              <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
            ) : savedPending ? (
              <BookmarkCheck className="h-3.5 w-3.5 mr-1" />
            ) : (
              <BookmarkPlus className="h-3.5 w-3.5 mr-1" />
            )}
            {savedPending ? t.dashboard.alreadyPending : t.dashboard.savePending}
          </Button>
        </div>
      )}

      {/* P3 — Editorial Context block (only renders when available) */}
      <EditorialContextPanel
        editorial={m._editorial_context}
        interpretation={m._editorial_interpretation}
        testId={`editorial-context-${m.match_id}`}
      />

      {/* Historical Detail Enrichment — Basketball/Baseball "Historial profundo"
          block. Renders only when the backend pre-fetch produced a usable
          profile (available=true). */}
      <HistoricalProfilePanel
        profile={m.basketballHistoricalProfile || m.baseballHistoricalProfile}
        sport={m.baseballHistoricalProfile ? 'baseball' : 'basketball'}
        testId={`historical-profile-${m.match_id}`}
      />

      {/* Phase 41 (P2 follow-up) — manual Four Factors hydration trigger.
          Renders for basketball + baseball cards. Calls
          POST /api/analysis/box-scores/hydrate which fetches real
          box-scores via API-Sports + provider fallback and persists
          them so the next analyze() uses REAL Four Factors instead of
          the historical proxy. Hidden for football. */}
      {(sport === 'basketball' || sport === 'baseball') && (
        <BoxScoreHydrateButton
          match={m}
          apiClient={api}
          lang={lang}
        />
      )}

      {/* Phase 44 / P3 — Live Script Reality Check badge.
          Only MLB live cards (baseball + is_live). The backend stamps
          `live_script_reality_check` on the match doc when the
          analyst pipeline / live re-eval runs; if absent the badge is
          a no-op. NEVER flips the engine pick — informational only. */}
      {/* Phase 47 — Inning-Lambda Model panel (MLB pregame).
          Surfaces the λ_1_3 + λ_4_6 + λ_7_9 decomposition with the
          continuous traffic-score interaction in the late phase.
          Observe-only by default: never alters the engine pick.
          Fix 3 — Always visible for MLB cards. The panel itself
          renders a "pending" placeholder when the pregame pipeline
          hasn't produced the projection yet. */}
      {sport === 'baseball' && (
        <InningLambdaPanel
          projection={m.inning_lambda_projection}
          lang={lang}
          testId={`inning-lambda-${m.match_id || ''}`}
        />
      )}

      {/* Priority 3 — Series Familiarity badge (MLB pregame only).
          Surfaces when these two teams have faced each other recently
          (3 / 5 / 15 day window). Observe-only narrative. */}
      {sport === 'baseball' && m.series_familiarity?.available && (
        <SeriesFamiliarityBadge
          data={m.series_familiarity}
          lang={lang}
          testId={`series-familiarity-${m.match_id || ''}`}
        />
      )}

      {/* Priority 4 — Expected Runs distribution panel. Renders the full
          probability range (p10..p90) + protected line suggestions for
          the engine's chosen side. Observe-only (never flips polarity). */}
      {sport === 'baseball' && m.expected_runs_distribution?.available && (
        <ExpectedRunsRangePanel
          data={m.expected_runs_distribution}
          lang={lang}
          testId={`expected-runs-range-${m.match_id || ''}`}
        />
      )}

      {/* Tail Risk + Hidden Over Routes (P5 / Priority 5). Renders
          explosive tail probabilities, market interpretation and the
          adjusted fragility (e.g. "20 → 34") when the calibrator fires.
          Observe-only (never flips polarity). */}
      {sport === 'baseball'
        && (m.tail_risk?.available || m.fragility_calibration?.available
            || m.tail_fragility?.available) && (
        <TailRiskPanel
          tailRisk={m.tail_risk}
          tailFragility={m.tail_fragility}
          fragilityCalibration={m.fragility_calibration}
          marketProfile={m.market_profile}
          lang={lang}
          testId={`tail-risk-${m.match_id || ''}`}
        />
      )}

      {/* MLB Offensive Injury Impact (P6). Replaces the meaningless
          "X jugadores lesionados" counter with a quality-weighted view of
          how many of the team's TOP-5 bats are actually unavailable, plus
          the estimated run creation lost. Observe-only — never flips
          Over/Under polarity. */}
      {sport === 'baseball' && m.offensive_injury_impact?.available && (
        <OffensiveInjuryImpactPanel
          impact={m.offensive_injury_impact}
          lang={lang}
          testId={`offensive-injury-impact-${m.match_id || ''}`}
        />
      )}

      {sport === 'baseball' && m.is_live && (
        <LiveScriptRealityBadge
          match={m}
          lang={lang}
          stats={{
            inning:       m.live_snapshot?.inning,
            score_total:  m.live_snapshot?.score?.total,
            hits:         m.live_snapshot?.combined_hits,
            walks:        m.live_snapshot?.combined_walks,
            home_runs:    m.live_snapshot?.combined_home_runs,
            left_on_base: m.live_snapshot?.combined_left_on_base,
          }}
        />
      )}

      {/* Phase 44 — Bullpen + Traffic interaction badge (observe-only).
          Reads `m.bullpen_traffic` (pre-hydrated by MLB pregame pipeline)
          OR `m.last_reeval?.bullpen_traffic` (stamped by the live
          reeval response). Renders only when the engine produced a
          verdict — silently no-op otherwise. MLB only. */}
      {sport === 'baseball' && (m.bullpen_traffic || m.last_reeval?.bullpen_traffic) && (
        <BullpenTrafficBadge
          data={m.bullpen_traffic || m.last_reeval?.bullpen_traffic}
          lang={lang}
          testId={`bullpen-traffic-badge-${m.match_id || ''}`}
        />
      )}

      {/* Phase 45 — Football Siege Pressure Guard badge.
          Reads `m.siege_pressure` (if persisted by the analyst
          pipeline) OR `m.last_reeval?.siege_pressure` (live reeval).
          Football live only. Observe-only message + optional block
          on Under markets — engine pick handling is owned by the
          backend; this badge surfaces the verdict to the user. */}
      {sport === 'football' && m.is_live && (m.siege_pressure || m.last_reeval?.siege_pressure) && (
        <SiegePressureBadge
          data={m.siege_pressure || m.last_reeval?.siege_pressure}
          lang={lang}
          testId={`siege-pressure-badge-${m.match_id || ''}`}
        />
      )}

      {/* Football Moneyball Intelligence Layer + Pattern Memory.
          Three independent, fail-soft panels. Each renders only when its
          payload is present and `available`. Football-only by gating
          on `sport === 'football'` so MLB / Basketball cards stay
          unchanged. */}
      {sport === 'football' && (
        <>
          <FootballIntelligencePanel
            pick={m}
            testId={`football-intel-${m.match_id}`}
          />
          <FootballPatternMemoryPanel
            pick={m}
            testId={`football-pattern-${m.match_id}`}
          />
          {/* Phase F58 — Cross Profile (L5 vs L15) + Override + Player Props.
              Self-hides when none of the three sub-blocks have data. */}
          <FootballProfileCrossPropsPanel
            pick={m}
            testId={`football-f58-${m.match_id}`}
          />
          <FootballLiveVsPregamePanel
            diff={m.football_live_vs_pregame || m._live_reeval?.football_live_vs_pregame}
            testId={`football-live-vs-pregame-${m.match_id}`}
          />
          <FootballTotalsModelPanel
            totalsModel={m.football_totals_model}
            recommendation={m.recommendation}
            testId={`football-totals-model-${m.match_id}`}
          />
          <FootballOverSupportPanel
            overSupport={m.football_over_support}
            testId={`football-over-support-${m.match_id}`}
          />
          <LiveRecommendationTimeline
            matchId={m.match_id}
            matchLabel={
              (m.home_team?.name && m.away_team?.name)
                ? `${m.home_team.name} vs ${m.away_team.name}`
                : null
            }
            league={m.league?.name || m.league || null}
            sport="football"
            testId={`live-reco-timeline-${m.match_id}`}
          />
        </>
      )}

      {/* MLB Engine V3 — Explainability layer (Game Script + Pitchers +
          Why This Pick + Confidence Breakdown + baseball-first reasons).
          Renders ABOVE the v2 numeric panel so the user sees the human
          narrative first. Baseball-only.  V5 (Script Survival) is passed
          through so the panel shows the stability summary line. */}
      {sport === 'baseball' && m._mlb_script_v3 ? (
        <MLBScriptV3Panel
          scriptV3={m._mlb_script_v3}
          scriptV5={m._mlb_script_v5}
          overDiscovery={m._mlb_over_discovery}
          testId={`mlb-v3-${m.match_id}`}
        />
      ) : null}

      {/* MLB Margin & Total Script Engine v2 — baseball-only.
          Renders the per-pick `_mlb_script_v2` payload (margin projection,
          cover probability, smart total line, same-game correlation, pick
          type, reasons & risks). Strictly gated by `sport === "baseball"`
          per the user requirement: basketball/football pickcards must NOT
          change. */}
      {sport === 'baseball' && (m._mlb_script_v2 || m.margin_v2) ? (
        <MLBScriptPanel
          scriptV2={m._mlb_script_v2 || {}}
          scriptV5={m._mlb_script_v5 || null}
          parlay={m._mlb_parlay_context || null}
          underFragilityWarning={m.under_fragility_warning || null}
          scriptPickMismatchNarrative={m.script_pick_mismatch_narrative || null}
          scriptPickMismatchDetails={m.script_pick_mismatch_details || null}
          biasPenaltyMeta={m.bias_penalty_applied ? (m.bias_penalty_meta || { triggered: true }) : null}
          activeSeriesContext={m.active_series_context || null}
          seriesDegradation={m.series_degradation || null}
          modelVerification={m.model_verification || null}
          activeSeriesBlock={m.active_series_block || null}
          chosenMarket={m.recommendation?.market || null}
          ilPenalty={m.il_penalty || null}
          testId={`mlb-script-${m.match_id}`}
        />
      ) : null}

      {/* MLB Advanced Stats + Sabermetrics + Market Selection (Phase 13.2).
          Collapsible block surfacing the Statcast snapshot, WAR/OPS/FIP
          sabermetrics summary and the final protected market selection.
          Baseball-only and fail-soft: renders nothing if no data. */}
      {sport === 'baseball' ? (
        <MLBAdvancedStatsPanel pick={m} lang={lang} />
      ) : null}

      {/* Inline drivers preview + expand */}
      {intel && (visibleDrivers.length > 0 || intel.bestFor?.length > 0) && (
        <div className="pt-2 border-t border-border/40">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            data-testid={`pick-row-expand-button-${m.match_id}`}
            className="w-full flex items-center justify-between gap-2 text-left group"
            aria-expanded={expanded}
          >
            <div className="flex items-center gap-1.5 flex-wrap min-w-0">
              <span className="micro-label">DRIVERS</span>
              {visibleDrivers.map((d) => {
                const meta = DRIVER_META[d.key] || {};
                const label = lang === 'en' ? meta.label_en : meta.label_es;
                const tone = d.sign === 'positive' ? 'emerald' : d.sign === 'negative' ? 'rose' : 'slate';
                return (
                  <span key={d.key} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${tone}`}>
                    {label}
                  </span>
                );
              })}
              {remainingDrivers > 0 && (
                <span className="text-[10px] text-muted-foreground font-mono-tabular">+{remainingDrivers}</span>
              )}
            </div>
            <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 group-hover:text-foreground transition-colors">
              <Gauge className="h-3 w-3" />
              <span className="hidden sm:inline">{expanded ? 'Ocultar' : 'Inteligencia'}</span>
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} />
            </span>
          </button>
          {expanded && (
            <div className="mt-3" data-testid={`pick-intel-expanded-${m.match_id}`}>
              <ConfidenceIntelligenceCard pick={m} sport={sport} />
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}
