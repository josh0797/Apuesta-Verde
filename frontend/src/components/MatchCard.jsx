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
import { EditorialSignalsPanel } from './EditorialSignalsPanel';
import { ExternalSourceEvidencePanel } from './ExternalSourceEvidencePanel';
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
            {/* Football Quality Badge (Phase 8) — only for football picks */}
            {sport === 'football' && m._football_quality && (
              <FootballQualityBadge
                quality={m._football_quality}
                lang={lang}
                testId={`pick-quality-badge-${m.match_id}`}
              />
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
      {m.reasoning && (
        <p className="text-sm text-muted-foreground leading-relaxed border-l-2 border-cyan-500/40 pl-3">
          {m.reasoning}
        </p>
      )}

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
