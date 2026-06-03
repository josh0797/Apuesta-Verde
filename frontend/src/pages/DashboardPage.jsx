import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Sparkles, Loader2, ChevronDown, ChevronUp, ExternalLink, Activity, Shield, TrendingDown, AlertCircle, Eye, ShieldAlert, Flag, RefreshCcw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useI18n, sportTerms } from '@/lib/i18n';
import { useSport, sportLabel } from '@/lib/sport';
import { api } from '@/lib/api';
import { applyEnginePreset } from '@/lib/intelligence';
import { AnalysisProgressModal } from '@/components/AnalysisProgressModal';

import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { MatchCard } from '@/components/MatchCard';
import { LiveTodayPanel } from '@/components/LiveTodayPanel';
import { SkippedMatchRow } from '@/components/FootballQualityBadge';
import { EmptyStateNoValue } from '@/components/EmptyStateNoValue';
import { PicksFilterBar } from '@/components/PicksFilterBar';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTime, tierClass } from '@/lib/format';
import { EditorialSignalsPanel, EditorialSignalsSummary } from '@/components/EditorialSignalsPanel';
import { ExternalSourceEvidencePanel, PossibleAlternativeMarkets } from '@/components/ExternalSourceEvidencePanel';
import { SourcesConsultedPanel } from '@/components/SourcesConsultedPanel';
import { ManualOddsReviewPanel } from '@/components/ManualOddsReviewPanel';
import { FootballMarketAuditPanel } from '@/components/FootballMarketAuditPanel';
import { CornerPregamePanel } from '@/components/CornerPregamePanel';

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

function SeverityBadge({ severity }) {
  const cls = {
    high:   'bg-red-500/15 text-red-300 border-red-500/30',
    medium: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    low:    'bg-slate-500/15 text-slate-300 border-slate-500/30',
  }[severity] || 'bg-slate-500/15 text-slate-300 border-slate-500/30';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${cls}`}>
      {severity}
    </span>
  );
}

function FragilityChip({ score, label }) {
  if (score == null) return null;
  const tone =
    score <= 30 ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' :
    score <= 55 ? 'bg-amber-500/15 text-amber-300 border-amber-500/30' :
    score <= 75 ? 'bg-orange-500/15 text-orange-300 border-orange-500/30' :
                  'bg-red-500/15 text-red-300 border-red-500/30';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${tone}`} title={`Fragilidad: ${label || score}/100`}>
      Frag {score}
    </span>
  );
}

function DiscardedRow({ item, testId, type, sport }) {
  const { lang } = useI18n();
  const [open, setOpen] = useState(false);
  const mb = item._moneyball || {};
  const structuredTraps = mb.trap_signals_structured || [];
  const frag = mb.fragility || {};
  const editorialSignals = item.editorial_context_signals || [];
  const externalEvidence = item.external_source_evidence || [];
  const possibleAlts = item.possible_alternative_markets || [];
  const reviewNote = item.user_review_note || '';
  // V4 — Football Market Trace payload (only present when sport === 'football'
  // and the engine attached the explicit market_trace + markets_checked).
  const isFootball = sport === 'football';
  const marketTrace = item.market_trace || null;
  const marketsChecked = Array.isArray(item.markets_checked) ? item.markets_checked : [];
  const hasFootballAudit = isFootball && (marketTrace || marketsChecked.length > 0);

  const hasDetails = structuredTraps.length > 0
    || frag.score != null
    || editorialSignals.length > 0
    || externalEvidence.some((e) => e.status === 'ok')
    || possibleAlts.length > 0
    || hasFootballAudit;
  const trapCount = structuredTraps.length;
  const sigCount = editorialSignals.length;
  const humanReason = useMemo(() => {
    // V4 — Prefer the explicit card_header from the engine when available
    // (e.g. "PSG Doble Oportunidad (1X) descartado por edge insuficiente (-12.9%)").
    if (hasFootballAudit && item.card_header) return item.card_header;
    if (!trapCount && !sigCount) return item.reason || item.missing || '';
    const verb = type === 'market' ? 'Descartado: mercado directo sin valor' : (item.reason || '');
    const parts = [];
    if (trapCount) parts.push(`${trapCount} señal${trapCount === 1 ? '' : 'es'} trampa`);
    if (sigCount && sigCount !== trapCount) parts.push(`${sigCount} señal${sigCount === 1 ? '' : 'es'} de contexto`);
    return parts.length ? `${verb}. ${parts.join(' · ')}.` : verb;
  }, [item, type, trapCount, sigCount, hasFootballAudit]);

  const inner = (
    <div className="rounded-md border border-border bg-card/60 hover:border-cyan-500/30 transition-colors" data-testid={testId}>
      {/* Mobile-first: stack icon+label on row 1, reason+chip+toggle on row 2.
          Desktop (md+) keeps the original horizontal layout. */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-2 md:gap-3 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {type === 'incomplete' && <AlertCircle className="h-3.5 w-3.5 text-slate-300 shrink-0" />}
          {type === 'motivation' && <TrendingDown className="h-3.5 w-3.5 text-orange-300 shrink-0" />}
          {type === 'market' && <Shield className="h-3.5 w-3.5 text-red-300 shrink-0" />}
          <span className="text-sm text-foreground/90 truncate min-w-0">{item.match_label}</span>
          {frag.score != null && <FragilityChip score={frag.score} label={frag.label} />}
        </div>
        <div className="flex items-center gap-2 md:shrink-0 min-w-0">
          {/* On mobile the reason wraps freely (no max-w cap); on desktop
              we keep the 420px cap with truncate for visual balance. */}
          <span
            className="text-xs text-muted-foreground md:text-right md:max-w-[420px] md:truncate flex-1 md:flex-initial min-w-0"
            title={humanReason}
          >
            {humanReason}
          </span>
          {hasDetails && (
            <button
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
              className="text-xs text-cyan-400 hover:text-cyan-300 px-1.5 py-0.5 rounded border border-cyan-500/30 shrink-0"
              data-testid={`${testId}-toggle`}
            >
              {open ? '▲' : '▼'}
            </button>
          )}
          {item.match_id && <ExternalLink className="h-3 w-3 text-muted-foreground shrink-0 hidden md:inline" />}
        </div>
      </div>
      {open && hasDetails && (
        <div className="border-t border-border/60 px-3 py-2.5 space-y-2 bg-background/30">
          {/* V4 — Football Market Trace audit (renders ONLY for football
              when the backend attached `market_trace` + `markets_checked`).
              Goes FIRST so the user immediately sees the explicit Market /
              Cuota / Edge / Motivo block instead of generic wording. */}
          {hasFootballAudit && (
            <FootballMarketAuditPanel
              item={item}
              testIdPrefix={`${testId}-${item.match_id || 'na'}`}
            />
          )}
          {possibleAlts.length > 0 && (
            <PossibleAlternativeMarkets
              markets={possibleAlts}
              note={reviewNote}
              testId={`${testId}-alts`}
            />
          )}
          {externalEvidence.length > 0 && (
            <ExternalSourceEvidencePanel
              evidence={externalEvidence}
              defaultOpen={false}
              testId={`${testId}-ext-sources`}
            />
          )}
          {item.external_sources && (
            <SourcesConsultedPanel
              external={item.external_sources}
              defaultOpen={false}
              testId={`${testId}-sources-consulted`}
              lang={lang}
            />
          )}
          {editorialSignals.length > 0 && (
            <EditorialSignalsPanel
              signals={editorialSignals}
              variant="expanded"
              testId={`${testId}-signals`}
            />
          )}
          {structuredTraps.length > 0 && (
            <div data-testid={`${testId}-traps`}>
              <div className="text-[11px] uppercase tracking-wide text-amber-300 font-semibold mb-1.5">⚠️ Señales trampa detectadas</div>
              <ul className="space-y-1.5">
                {structuredTraps.map((t, i) => (
                  <li key={t.code || i} className="flex items-start gap-2 text-xs" data-testid={`trap-signal-${t.code}`}>
                    <SeverityBadge severity={t.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-foreground/90">{t.label}</div>
                      {t.explanation && <div className="text-muted-foreground text-[11px] mt-0.5">{t.explanation}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {frag.factors && frag.factors.length > 0 && (
            <div data-testid={`${testId}-fragility`}>
              <div className="text-[11px] uppercase tracking-wide text-orange-300 font-semibold mb-1">Factores de fragilidad</div>
              <ul className="text-[11px] text-muted-foreground list-disc list-inside space-y-0.5">
                {frag.factors.slice(0, 5).map((f, i) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
  if (item.match_id && !open) {
    return <Link to={`/match/${item.match_id}`} className="block">{inner}</Link>;
  }
  return inner;
}

function RescuedRow({ item, testId }) {
  const [open, setOpen] = useState(false);
  const edgePct = item.edge != null ? (item.edge * 100).toFixed(1) : null;
  const isCorner = item.rescueType === 'CORNER_MARKET' || item.rescueType === 'CORNER_MARKET_PREGAME';
  const isCornerPregame = item.rescueType === 'CORNER_MARKET_PREGAME';
  const m = item.metrics || {};
  // Pregame corner picks have no automatic odds — show "manual" instead.
  const oddsDisplay = item.decimal_odds != null
    ? `@${item.decimal_odds}`
    : (isCornerPregame ? '@manual' : '@—');
  return (
    <div className={`rounded-md border ${isCorner ? 'border-violet-500/30 bg-violet-500/5 hover:border-violet-500/50' : 'border-emerald-500/30 bg-emerald-500/5 hover:border-emerald-500/50'} transition-colors`} data-testid={testId}>
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {isCorner ? <Flag className="h-3.5 w-3.5 text-violet-300 shrink-0" /> : <Sparkles className="h-3.5 w-3.5 text-emerald-300 shrink-0" />}
          <span className="text-sm text-foreground/90 truncate">{item.match_label}</span>
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${isCorner ? 'bg-violet-500/15 text-violet-300 border-violet-500/30' : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'}`}>
            {isCornerPregame ? 'Córners Pre-match' : (isCorner ? 'Córners' : (item.classification === 'PROTECTED_ACCEPTABLE' ? 'Protegido' : 'Rescatado'))}
          </span>
          {item.fragility_score != null && <FragilityChip score={item.fragility_score} />}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs font-mono text-foreground/80 truncate max-w-[260px]">
            {isCorner ? item.selection : `${item.market} (${item.selection})`}
          </span>
          <span className="text-xs font-mono text-foreground/70">{oddsDisplay}</span>
          {edgePct != null && (
            <span className={`text-xs font-mono ${item.edge >= 0 ? 'text-emerald-400' : 'text-amber-400'}`}>
              {item.edge >= 0 ? '+' : ''}{edgePct}%
            </span>
          )}
          <button
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
            className={`text-xs ${isCorner ? 'text-violet-400 hover:text-violet-300 border-violet-500/30' : 'text-emerald-400 hover:text-emerald-300 border-emerald-500/30'} px-1.5 py-0.5 rounded border`}
            data-testid={`${testId}-toggle`}
          >
            {open ? '▲' : '▼'}
          </button>
        </div>
      </div>
      {open && (
        <div className={`border-t ${isCorner ? 'border-violet-500/20' : 'border-emerald-500/20'} px-3 py-2.5 space-y-2.5 bg-background/30 text-xs`}>
          {/* Dedicated pre-match corner panel — replaces the live metrics
              grid when the pick came from the pregame branch and surfaces
              per-team averages, trap signals and a paste-odds calculator. */}
          {isCornerPregame && (item.corner_form || item._corner_form) ? (
            <CornerPregamePanel item={item} testId={`${testId}-pregame-panel`} />
          ) : null}
          {isCorner && !isCornerPregame && (m.cornerForAvgHomeLast5 != null || m.cornerForAvgAwayLast5 != null) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid={`${testId}-corner-metrics`}>
              <div className="bg-card/60 rounded px-2 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Local (Últ. 5)</div>
                <div className="font-mono text-sm text-foreground/90">
                  {m.cornerForAvgHomeLast5?.toFixed(1) ?? '—'} <span className="text-[10px] text-muted-foreground">a favor</span>
                </div>
                <div className="font-mono text-[11px] text-muted-foreground">
                  {m.cornerAgainstAvgHomeLast5?.toFixed(1) ?? '—'} concedidos
                </div>
              </div>
              <div className="bg-card/60 rounded px-2 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Visitante (Últ. 5)</div>
                <div className="font-mono text-sm text-foreground/90">
                  {m.cornerForAvgAwayLast5?.toFixed(1) ?? '—'} <span className="text-[10px] text-muted-foreground">a favor</span>
                </div>
                <div className="font-mono text-[11px] text-muted-foreground">
                  {m.cornerAgainstAvgAwayLast5?.toFixed(1) ?? '—'} concedidos
                </div>
              </div>
              <div className="bg-violet-500/10 rounded px-2 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-violet-300">Proyección motor</div>
                <div className="font-mono text-sm text-violet-200">{m.combinedCornerProjection?.toFixed(1) ?? '—'}</div>
                {m.h2hCornerAvg != null && (
                  <div className="font-mono text-[11px] text-muted-foreground">H2H: {m.h2hCornerAvg?.toFixed(1)}</div>
                )}
              </div>
              <div className="bg-card/60 rounded px-2 py-1.5">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Fit / Fragilidad</div>
                <div className="font-mono text-sm text-foreground/90">{m.cornerFitScore ?? 0}/100</div>
                <div className="font-mono text-[11px] text-muted-foreground">Frag: {m.cornerFragilityScore ?? 0}/100</div>
              </div>
            </div>
          )}
          {item.reasons && item.reasons.length > 0 && (
            <div>
              <div className={`text-[11px] uppercase tracking-wide ${isCorner ? 'text-violet-300' : 'text-emerald-300'} font-semibold mb-1`}>Lectura del motor</div>
              <ul className="space-y-0.5 list-disc list-inside text-muted-foreground">
                {item.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}
          {item.risks && item.risks.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-amber-300 font-semibold mb-1">Riesgos a considerar</div>
              <ul className="space-y-0.5 list-disc list-inside text-muted-foreground">
                {item.risks.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}
          {item.trap_signals_structured && item.trap_signals_structured.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-amber-300 font-semibold mb-1">⚠️ Señales trampa detectadas</div>
              <ul className="space-y-1">
                {item.trap_signals_structured.map((t, i) => (
                  <li key={t.code || i} className="flex items-start gap-2">
                    <SeverityBadge severity={t.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-foreground/90">{t.label}</div>
                      {t.explanation && <div className="text-muted-foreground text-[11px] mt-0.5">{t.explanation}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="border-t border-border/40 pt-2 grid grid-cols-1 sm:grid-cols-2 gap-2">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-red-300 font-semibold mb-1">¿Por qué falló el mercado directo?</div>
              <div className="text-muted-foreground">{item.whyDirectMarketsFailed}</div>
            </div>
            <div>
              <div className={`text-[11px] uppercase tracking-wide ${isCorner ? 'text-violet-300' : 'text-emerald-300'} font-semibold mb-1`}>¿Por qué este mercado es más seguro?</div>
              <div className="text-muted-foreground">{item.whyThisMarketIsSafer}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function WatchlistRow({ item, testId }) {
  const edgePct = item.edge != null ? (item.edge * 100).toFixed(1) : null;
  const isPick = !!item.recommendation;  // diferencia entre watchlist-from-pick vs watchlist-from-rescue
  const market = isPick
    ? `${item.recommendation?.market || ''} (${item.recommendation?.selection || ''})`
    : `${item.market || ''} (${item.selection || ''})`;
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 flex items-center justify-between gap-3" data-testid={testId}>
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <Eye className="h-3.5 w-3.5 text-amber-300 shrink-0" />
        <span className="text-sm text-foreground/90 truncate">{item.match_label}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span className="text-xs font-mono text-foreground/70">{market}</span>
        {edgePct != null && (
          <span className="text-xs font-mono text-amber-300">{item.edge >= 0 ? '+' : ''}{edgePct}%</span>
        )}
      </div>
    </div>
  );
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
  // Auto-refresh hint: when /api/picks/today reports stale=true and
  // dispatches a background analysis run, surface a subtle banner so
  // the user knows fresh picks are on the way and a re-poll will happen
  // shortly.
  const [autoRefresh, setAutoRefresh] = useState({ active: false, jobId: null });
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ league: '', market: '', minConfidence: 0, enginePreset: '' });
  const [activeJobId, setActiveJobId] = useState(null);

  // Bug-2 fix — sport-scoped requests:
  // We keep a ref of the CURRENT sport. Every async call captures the sport
  // it was launched with; when the response comes back we check the ref. If
  // the user has since switched tabs, the response is DISCARDED (so we never
  // paint football picks on the basketball tab). Without this, network calls
  // raced against the tab switch and you saw the previous sport flicker
  // through.
  const sportRef = useRef(sport);
  useEffect(() => { sportRef.current = sport; }, [sport]);

  // Bug-2 fix — also clear stale data the INSTANT the sport changes, so the
  // UI doesn't keep showing the previous sport's KPIs while the new fetch
  // is in flight.
  useEffect(() => {
    console.log('[SPORT_SWITCH]', sport);
    setRun(null);
    setActiveJobId(null);
    setLoading(true);
  }, [sport]);

  const refs = {
    high: useRef(null), medium: useRef(null),
    discMot: useRef(null), discMkt: useRef(null), incomplete: useRef(null),
  };

  const loadLast = useCallback(async () => {
    const requestSport = sport;
    try {
      setLoading(true);
      const r = await api.get('/picks/today', { params: { sport } });
      if (sportRef.current !== requestSport) {
        console.log('[SPORT_SWITCH] discarded stale /picks/today for', requestSport, '→ now', sportRef.current);
        return;
      }
      setRun(r.data.pick_run);
      // Surface the auto-refresh banner only when the backend reports
      // it dispatched a refresh job. We re-poll once after ~25s so the
      // dashboard picks up the fresh snapshot without the user having
      // to click anything.
      if (r.data?.refresh_dispatched && r.data?.refresh_job_id) {
        setAutoRefresh({ active: true, jobId: r.data.refresh_job_id });
        setTimeout(() => {
          if (sportRef.current === requestSport) {
            api.get('/picks/today', { params: { sport } })
              .then((r2) => {
                if (sportRef.current !== requestSport) return;
                if (r2.data?.pick_run) setRun(r2.data.pick_run);
                if (!r2.data?.stale) setAutoRefresh({ active: false, jobId: null });
              })
              .catch(() => {});
          }
        }, 25000);
      } else {
        setAutoRefresh({ active: false, jobId: null });
      }
    } catch (e) { /* noop */ }
    finally {
      if (sportRef.current === requestSport) setLoading(false);
    }
  }, [sport]);

  useEffect(() => { loadLast(); }, [loadLast]);

  // On mount / sport change, resume any active job for this user.
  useEffect(() => {
    let cancelled = false;
    const requestSport = sport;
    (async () => {
      try {
        const r = await api.get('/analysis/jobs');
        if (cancelled || sportRef.current !== requestSport) return;
        const active = (r.data?.active || []).find((j) => j.kind === 'analysis_run' && j?.params?.sport === sport);
        if (active) setActiveJobId(active.id);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [sport]);

  /**
   * Translate the raw error from `/analysis/run` into a user-friendly toast
   * AND a structured console log telling us WHICH step failed. This is the
   * Bug-1 defense-in-depth so we never again see a bare "Error" toast
   * without context.
   */
  const reportGenerationError = useCallback((err, step) => {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    const networkErr = !err?.response;
    const isBson = /documents must have only string keys/i.test(detail || err?.message || '');
    // Structured log for engineers — visible in browser devtools.
    // eslint-disable-next-line no-console
    console.error(`[${sport.toUpperCase()}_GENERATION_ERROR]`, {
      step,
      status,
      detail,
      networkErr,
      isBson,
      sport,
      error: err,
    });
    let msg;
    if (isBson) {
      msg = lang === 'en'
        ? 'Could not save the analysis (numeric keys in payload). System is hardened — please redeploy if you see this in production.'
        : 'No se pudo guardar el análisis (claves numéricas en el payload). El sistema ya está reforzado — si lo ves en producción, redéploy.';
    } else if (status === 409 && /no .* matches available|NO_PRIORITY_FIXTURES_FOUND/i.test(detail || '')) {
      msg = lang === 'en'
        ? 'No priority fixtures available in the next 48 h. Try again later or enable high-volume mode.'
        : 'No hay partidos prioritarios en las próximas 48 h. Intenta más tarde o activa modo alto volumen.';
    } else if (status === 502 || status === 504 || networkErr) {
      msg = lang === 'en'
        ? `Could not start ${sport} analysis (gateway/network). Step: ${step}.`
        : `No se pudo iniciar el análisis de ${sport} (pasarela/red). Paso: ${step}.`;
    } else if (status >= 500) {
      msg = lang === 'en'
        ? `${sport} analysis failed in step "${step}" (server ${status}). ${detail || ''}`.trim()
        : `Falló el análisis de ${sport} en el paso "${step}" (servidor ${status}). ${detail || ''}`.trim();
    } else {
      msg = lang === 'en'
        ? `Could not generate ${sport} picks. Step: ${step}. ${detail || err?.message || ''}`.trim()
        : `No se pudo generar picks de ${sport}. Paso: ${step}. ${detail || err?.message || ''}`.trim();
    }
    toast.error(msg);
  }, [sport, lang]);

  const generate = async () => {
    const requestSport = sport;
    setRunning(true);
    try {
      // Use background mode so the UI shows real-time progress instead of a 60-120s spinner.
      const r = await api.post('/analysis/run', {
        refresh: true,
        include_live: true,
        max_matches: 10,
        sport,
        background: true,
      });
      // Bug-2 — if the user already switched sports while POST was in flight,
      // don't hijack the new sport's tab with a stale job id.
      if (sportRef.current !== requestSport) {
        console.log('[SPORT_SWITCH] discarded stale /analysis/run response for', requestSport);
        return;
      }
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      } else if (r.data?.result) {
        // Backward-compat: if backend ever returns sync, hydrate immediately.
        setRun({ id: r.data.pick_run_id, sport: r.data.sport, generated_at: r.data.generated_at, payload: r.data.result });
        toast.success(t.dashboard.title + ' ✓');
      }
    } catch (err) {
      reportGenerationError(err, 'analysis/run · job submit');
    } finally {
      if (sportRef.current === requestSport) setRunning(false);
    }
  };

  // ── Lightweight recalibration ─────────────────────────────────────────
  // Re-runs the analyst layers on the matches already captured in the most
  // recent pick_run for this sport. No external ingestion, no fresh odds —
  // only fresh "engine" output. Useful right after a deploy that adds new
  // features (M1–M5, vetoes, scripts) so the picks adapt to the new math.
  // Only MLB + Basketball are supported in this iteration.
  const recalibrate = async () => {
    if (!['baseball', 'basketball'].includes(sport)) {
      toast.error(t.dashboard.recalibrateOnlySupported);
      return;
    }
    if (!run?.id && !data?.summary) {
      toast.error(t.dashboard.recalibrateNoRun);
      return;
    }
    const requestSport = sport;
    setRunning(true);
    try {
      const r = await api.post('/analysis/recalibrate', {
        sport,
        background: true,
      });
      if (sportRef.current !== requestSport) {
        console.log('[SPORT_SWITCH] discarded stale /analysis/recalibrate response for', requestSport);
        return;
      }
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      } else if (r.data?.result) {
        setRun({
          id: r.data.pick_run_id,
          sport: r.data.sport,
          generated_at: r.data.generated_at,
          payload: r.data.result,
        });
        toast.success(t.dashboard.recalibrateDone);
      }
    } catch (err) {
      reportGenerationError(err, 'analysis/recalibrate · job submit');
    } finally {
      if (sportRef.current === requestSport) setRunning(false);
    }
  };

  const onJobDone = useCallback((result) => {
    // Refresh the dashboard with the freshly completed run.
    toast.success(t.dashboard.title + ' ✓');
    loadLast();
  }, [loadLast, t.dashboard.title]);

  // ── Football: refresh match pool WITHOUT firing LLM ──────────────────
  // Hits POST /api/football/refresh-matches → re-ingests fixtures + odds
  // and tells us the delta. The backend AUTO-PROMOTES the full pool
  // refresh to background to avoid the 60s ingress timeout, so the
  // response can be either:
  //   - sync result   (ok, before/after/delta, errors[])
  //   - job submission (job_id, status="queued") → we poll until done.
  // Dedupe key:  match_id (upsert) — the backend never duplicates rows.
  // Only enabled on the Football tab.
  const [refreshingMatches, setRefreshingMatches] = useState(false);
  const refreshMatches = async () => {
    if (sport !== 'football') return;
    const requestSport = sport;
    setRefreshingMatches(true);
    try {
      const r = await api.post('/football/refresh-matches', {
        include_live: true,
        national_teams_only: false,
      });
      if (sportRef.current !== requestSport) return;
      // Branch A: backend returned a job (background mode).
      if (r.data?.job_id) {
        // Poll the job until it finishes (≤ ~90s) and then surface the
        // delta to the user. Fail-soft: if polling times out, we just
        // re-fetch loadLast() so the dashboard still picks up whatever
        // landed in the DB.
        const jobId = r.data.job_id;
        const start = Date.now();
        const TIMEOUT_MS = 90_000;
        let final = null;
        while (Date.now() - start < TIMEOUT_MS) {
          await new Promise((resolve) => setTimeout(resolve, 3000));
          if (sportRef.current !== requestSport) break;
          try {
            const jr = await api.get(`/analysis/jobs/${jobId}`);
            const stage = jr.data?.stage;
            if (stage === 'done')   { final = jr.data?.result; break; }
            if (stage === 'failed') { throw new Error(jr.data?.error || 'job failed'); }
          } catch (poll_err) {
            // continue polling; if it's a 404/500 it'll naturally time out.
            // eslint-disable-next-line no-console
            console.warn('[FOOTBALL_REFRESH_POLL]', poll_err?.message || poll_err);
          }
        }
        if (sportRef.current !== requestSport) return;
        if (final) {
          const after = final?.after?.upcoming ?? 0;
          const delta = final?.delta?.upcoming ?? 0;
          const errors = final?.errors || [];
          if (errors.length > 0) {
            toast.warning(
              (lang === 'es'
                ? `Refrescados con ${errors.length} advertencia(s): `
                : `Refreshed with ${errors.length} warning(s): `) + errors[0]
            );
          } else {
            toast.success(
              t.dashboard.refreshMatchesDone
                .replace('{delta}', String(delta))
                .replace('{total}', String(after))
            );
          }
        } else {
          // Timed-out polling — still useful to refresh the snapshot.
          toast.info(lang === 'es'
            ? 'Refresco aún en curso en background. Volveré a cargar el snapshot.'
            : 'Refresh still running in background. Reloading snapshot.');
        }
        loadLast();
        return;
      }
      // Branch B: SYNC response (national_teams_only=false rarely hits this,
      // but national_teams_only=true does).
      const after = r.data?.after?.upcoming ?? 0;
      const delta = r.data?.delta?.upcoming ?? 0;
      const errors = r.data?.errors || [];
      if (errors.length > 0) {
        toast.warning(
          (lang === 'es'
            ? `Refrescados con ${errors.length} advertencia(s): `
            : `Refreshed with ${errors.length} warning(s): `) + errors[0]
        );
      } else {
        toast.success(
          t.dashboard.refreshMatchesDone
            .replace('{delta}', String(delta))
            .replace('{total}', String(after))
        );
      }
      loadLast();
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || '';
      toast.error(t.dashboard.refreshMatchesError + (detail ? ` — ${detail}` : ''));
      // eslint-disable-next-line no-console
      console.error('[FOOTBALL_REFRESH_ERROR]', err);
    } finally {
      if (sportRef.current === requestSport) setRefreshingMatches(false);
    }
  };

  // ── Football: National-team-only analysis ────────────────────────────
  // Hits POST /api/analysis/run with `national_teams_only=true` so the
  // pipeline keeps only fixtures with league_id ∈ NATIONAL_TEAM_LEAGUES.
  const analyzeNationalTeams = async () => {
    if (sport !== 'football') return;
    const requestSport = sport;
    setRunning(true);
    try {
      const r = await api.post('/analysis/run', {
        refresh: true,
        include_live: true,
        max_matches: 10,
        sport,
        background: true,
        national_teams_only: true,
      });
      if (sportRef.current !== requestSport) {
        // eslint-disable-next-line no-console
        console.log('[SPORT_SWITCH] discarded stale national-teams run for', requestSport);
        return;
      }
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      } else if (r.data?.result) {
        setRun({
          id: r.data.pick_run_id,
          sport: r.data.sport,
          generated_at: r.data.generated_at,
          payload: r.data.result,
        });
        toast.success(t.dashboard.title + ' ✓');
      }
    } catch (err) {
      reportGenerationError(err, 'analysis/run · national_teams');
    } finally {
      if (sportRef.current === requestSport) setRunning(false);
    }
  };

  // Batch 3 (P3) — Live-only analysis: triggers a normal run but with
  // `live_only=true` so the orchestrator restricts the candidate pool
  // to matches currently in progress. Useful when the user wants to
  // hunt live-edge bets without re-running the full pre-match pipeline.
  // Falls back to a regular `refresh=true` run if the server doesn't
  // know the flag yet (older deployments) — pipeline_meta will simply
  // not contain a `live_only` marker.
  const analyzeLive = async () => {
    const requestSport = sport;
    setRunning(true);
    try {
      const r = await api.post('/analysis/run', {
        refresh: true,
        include_live: true,
        max_matches: 10,
        sport,
        background: true,
        live_only: true,
      });
      if (sportRef.current !== requestSport) {
        // eslint-disable-next-line no-console
        console.log('[SPORT_SWITCH] discarded stale live-only run for', requestSport);
        return;
      }
      if (r.data?.job_id) {
        setActiveJobId(r.data.job_id);
      } else if (r.data?.result) {
        setRun({
          id: r.data.pick_run_id,
          sport: r.data.sport,
          generated_at: r.data.generated_at,
          payload: r.data.result,
        });
        toast.success(t.dashboard.title + ' ✓');
      }
    } catch (err) {
      reportGenerationError(err, 'analysis/run · live_only');
    } finally {
      if (sportRef.current === requestSport) setRunning(false);
    }
  };

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

  const { high, medium, discMot, discMkt, incomplete, skippedLowRel, rescued, watchlist, protectedAcceptable, carryover, structuralLean, watchlistManualOdds } = useMemo(() => {
    if (!data) return { high: [], medium: [], discMot: [], discMkt: [], incomplete: [], skippedLowRel: [], rescued: [], watchlist: [], protectedAcceptable: [], carryover: [], structuralLean: [], watchlistManualOdds: [] };
    return {
      high: filteredPicks.filter((p) => (p.recommendation?.confidence_score || 0) >= 70).sort((a, b) => (b.recommendation?.confidence_score || 0) - (a.recommendation?.confidence_score || 0)),
      medium: filteredPicks.filter((p) => { const c = p.recommendation?.confidence_score || 0; return c >= 60 && c < 70; }).sort((a, b) => (b.recommendation?.confidence_score || 0) - (a.recommendation?.confidence_score || 0)),
      discMot: data.summary?.discarded_motivation || [],
      discMkt: data.summary?.discarded_market || [],
      incomplete: data.summary?.incomplete_data || [],
      skippedLowRel: data.summary?.skipped_low_relevance || [],
      rescued: data.summary?.rescued_picks || [],
      watchlist: data.summary?.watchlist || [],
      protectedAcceptable: data.summary?.protected_acceptable || [],
      carryover: data.summary?.carryover_picks || [],
      // MLB-V5 new buckets — baseball-only manual review bucket.
      structuralLean:      data.summary?.structural_lean_requires_odds || [],
      watchlistManualOdds: data.summary?.watchlist_manual_odds || [],
    };
  }, [data, filteredPicks]);

  // MLB-V5 — When sport is baseball, the v2 engine routes "missing-odds"
  // games to `structural_lean_requires_odds`. The legacy `discarded_market`
  // bucket must NOT be confused with these — for baseball it should be
  // empty unless the engine truly discarded games after full analysis.
  const isBaseball = sport === 'baseball';
  const manualReviewItems = isBaseball
    ? [...structuralLean, ...watchlistManualOdds]
    : [];

  const scrollTo = (key) => {
    const el = refs[key]?.current;
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  const hasAnyDiscarded = (discMot.length + discMkt.length + incomplete.length + skippedLowRel.length) > 0;

  return (
    <div className="max-w-7xl mx-auto px-3 sm:px-4 md:px-6 lg:px-8 py-4 md:py-8 space-y-6 overflow-x-hidden">
      {activeJobId && (
        <AnalysisProgressModal
          jobId={activeJobId}
          onClose={closeProgressModal}
          onDone={onJobDone}
          sport={sport}
        />
      )}
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 md:gap-4">
        <div className="min-w-0">
          {/* Mobile: 2xl heading. Desktop: 4xl. flex-wrap so long sport
              pills don't push the title off-screen. */}
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-semibold tracking-tight flex flex-wrap items-center gap-2 md:gap-3">
            <span className="text-2xl md:text-3xl" aria-hidden>{currentSport?.icon}</span>
            <span className="break-words">{t.dashboard.title}</span>
            <span className="text-[10px] md:text-xs font-normal text-muted-foreground border border-border rounded-md px-2 py-0.5 align-middle" data-testid="active-sport-pill">
              {sportLabel(currentSport, lang)}
            </span>
          </h1>
          <p className="text-muted-foreground mt-1 text-xs md:text-sm break-words">{dynamicSubtitle}</p>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 shrink-0">
          {run?.generated_at && (
            <div className="text-[11px] md:text-xs text-muted-foreground">
              {t.dashboard.lastRun}: <span className="mono font-mono-tabular text-foreground">{formatDateTime(run.generated_at, lang)}</span>
              {autoRefresh.active && (
                <span
                  className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-cyan-500/10 text-cyan-200 border border-cyan-500/25 text-[10px] font-medium"
                  data-testid="auto-refresh-indicator"
                  title={lang === 'es' ? 'Snapshot anterior detectado como obsoleto — actualizando en segundo plano.' : 'Stale snapshot detected — refreshing in background.'}
                >
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {lang === 'es' ? 'Actualizando…' : 'Refreshing…'}
                </span>
              )}
            </div>
          )}
          <Button onClick={generate} disabled={running || !!activeJobId} data-testid="generate-picks-button" className="w-full sm:w-auto shadow-[0_0_0_1px_rgba(46,229,157,0.2),0_8px_24px_rgba(46,229,157,0.15)]">
            {activeJobId ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" />{lang === 'es' ? 'Procesando…' : 'Processing…'}</>
            ) : running ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.running}</>
            ) : (
              <><Sparkles className="h-4 w-4 mr-2" />{t.dashboard.generateBtn}</>
            )}
          </Button>
          {/* Recalibrate — lightweight re-score of the latest pick_run.
              MLB + Basketball only for now; football comes later. */}
          {['baseball', 'basketball'].includes(sport) && (
            <Button
              onClick={recalibrate}
              disabled={running || !!activeJobId || (!run?.id && !data?.summary)}
              data-testid="recalibrate-picks-button"
              variant="outline"
              title={t.dashboard.recalibrateHint}
              className="w-full sm:w-auto border-cyan-500/30 text-cyan-200 hover:bg-cyan-500/10 hover:text-cyan-100 transition-colors"
            >
              {running ? (
                <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.recalibrating}</>
              ) : (
                <><RefreshCcw className="h-4 w-4 mr-2" />{t.dashboard.recalibrateBtn}</>
              )}
            </Button>
          )}
          {/* Football-only: Refresh matches pool (no LLM run). Cheap
              ingest of fresh fixtures + odds + live data. */}
          {sport === 'football' && (
            <Button
              onClick={refreshMatches}
              disabled={refreshingMatches || running || !!activeJobId}
              data-testid="football-refresh-matches-button"
              variant="outline"
              title={t.dashboard.refreshMatchesHint}
              className="w-full sm:w-auto border-cyan-500/30 text-cyan-200 hover:bg-cyan-500/10 hover:text-cyan-100 transition-colors"
            >
              {refreshingMatches ? (
                <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.refreshingMatches}</>
              ) : (
                <><RefreshCcw className="h-4 w-4 mr-2" />{t.dashboard.refreshMatchesBtn}</>
              )}
            </Button>
          )}
          {/* Football-only: National-teams analysis (World Cup, Euros,
              Copa America, Nations League, qualifiers, friendlies). */}
          {sport === 'football' && (
            <Button
              onClick={analyzeNationalTeams}
              disabled={running || !!activeJobId || refreshingMatches}
              data-testid="football-national-teams-button"
              variant="outline"
              title={t.dashboard.nationalTeamsHint}
              className="w-full sm:w-auto border-amber-500/40 text-amber-200 hover:bg-amber-500/10 hover:text-amber-100 transition-colors"
            >
              {running ? (
                <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.running}</>
              ) : (
                <><Flag className="h-4 w-4 mr-2" />{t.dashboard.nationalTeamsBtn}</>
              )}
            </Button>
          )}
          {/* Batch 3 (P3) — Live-only analysis. Same pipeline as the
              regular run but restricted to fixtures currently in
              progress. Available for all 3 sports — handy when the
              user wants to hunt momentum / live-edge bets. */}
          <Button
            onClick={analyzeLive}
            disabled={running || !!activeJobId || refreshingMatches}
            data-testid="analyze-live-button"
            variant="outline"
            title={lang === 'en'
              ? 'Analyze only matches currently in progress'
              : 'Analizar solo partidos actualmente en vivo'}
            className="w-full sm:w-auto border-rose-500/40 text-rose-200 hover:bg-rose-500/10 hover:text-rose-100 transition-colors"
          >
            {running ? (
              <><Loader2 className="h-4 w-4 animate-spin mr-2" />{t.dashboard.running}</>
            ) : (
              <><Activity className="h-4 w-4 mr-2" />{lang === 'en' ? 'Analyze live' : 'Analizar en vivo'}</>
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

      {data?.summary?.editorial_signal_summary?.total_signals > 0 && (
        <EditorialSignalsSummary
          summary={data.summary.editorial_signal_summary}
          lang={lang}
          testId="editorial-signal-summary"
        />
      )}

      {/* In-progress picks panel — polls /api/picks/today/live every 30s
          and renders only the picks whose underlying matches were
          already mid-game when the snapshot was generated. Self-hides
          when there are no live picks AND the snapshot is fresh. */}
      <LiveTodayPanel sport={sport} lang={lang} testId="live-today-panel" />

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

      {/* NEW — Carryover picks (preserved from prior run, still valid) */}
      {!loading && data && carryover.length > 0 && (
        <div className="space-y-3" data-testid="carryover-section">
          <div className="rounded-lg border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-sky-200 flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5" />
            {lang === 'en'
              ? `Carried over from your previous run (${carryover.length})`
              : `Picks previos preservados (${carryover.length})`}
          </div>
          <p className="text-xs text-muted-foreground">
            {lang === 'en'
              ? 'These picks were recommended in your prior analysis and are still valid (the match has not started, and no hard invalidator was detected).'
              : 'Estos picks venían de tu análisis anterior y siguen siendo válidos (el partido no ha empezado y no hay invalidador duro como lesión o cambio de marcador).'}
          </p>
          <div className="grid gap-3" data-testid="carryover-grid">
            {carryover.map((p, i) => (
              <MatchCard key={p.match_id || `carry-${i}`} pick={p} idx={i} sport={sport} runId={run?.id} />
            ))}
          </div>
        </div>
      )}

      {/* MLB-V5 — Manual review section (baseball only). Renders games that
          the v2 engine identified as having a structural lean but for which
          automatic odds are missing. NOT routed to "Descartados por mercado
          frágil" anymore. */}
      {!loading && data && isBaseball && manualReviewItems.length > 0 && (
        <ManualOddsReviewPanel
          items={manualReviewItems}
          lang={lang}
          testId="mlb-manual-odds-review-section"
        />
      )}

      {/* NEW — Rescued picks (mercados protegidos + córners rescatados) */}
      {!loading && data && rescued.length > 0 && (
        <div className="space-y-3" data-testid="rescued-section">
          <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-emerald-200 flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5" />
            {lang === 'en'
              ? `Rescued via alternative markets (${rescued.length})`
              : `Rescatados vía mercados alternativos (${rescued.length})`}
            {rescued.some(r => r.rescueType === 'CORNER_MARKET') && (
              <span className="ml-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border border-violet-500/40 bg-violet-500/15 text-violet-200">
                <Flag className="h-3 w-3" /> {lang === 'en' ? 'Corners included' : 'Incluye córners'}
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {lang === 'en'
              ? 'Direct markets had no value, but an alternative reflects the game read better.'
              : (sport === 'baseball'
                  ? 'El ganador no tenía valor, pero un mercado alternativo (Total Runs / Run Line / Team Total) sí refleja la lectura del partido.'
                  : sport === 'basketball'
                    ? 'El ganador no tenía valor, pero un mercado alternativo (Total Points / Spread alt / Team Total) sí refleja la lectura del partido.'
                    : 'El ganador no tenía valor, pero un mercado alternativo (cobertura de goles / córners) sí refleja la lectura del partido.')}
          </p>
          <div className="grid gap-2">
            {rescued.map((r, i) => <RescuedRow key={r.match_id || i} item={r} testId={`rescued-row-${i}`} />)}
          </div>
        </div>
      )}

      {/* NEW — Protected acceptable (edge ligeramente negativo pero mercado protegido coherente) */}
      {!loading && data && protectedAcceptable.length > 0 && (
        <div className="space-y-3" data-testid="protected-acceptable-section">
          <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-cyan-200">
            {lang === 'en'
              ? `Protected acceptable (${protectedAcceptable.length})`
              : `Mercados protegidos aceptables (${protectedAcceptable.length})`}
          </div>
          <p className="text-xs text-muted-foreground">
            {lang === 'en'
              ? 'Slightly negative edge but low fragility and high confidence — playable as protection.'
              : 'Edge ligeramente negativo pero baja fragilidad y alta confianza — jugable como cobertura.'}
          </p>
          <div className="grid gap-3">
            {protectedAcceptable.map((p, i) => <MatchCard key={p.match_id || i} pick={p} idx={i} sport={sport} runId={run?.id} />)}
          </div>
        </div>
      )}

      {/* NEW — Watchlist */}
      {!loading && data && watchlist.length > 0 && (
        <div className="space-y-3" data-testid="watchlist-section">
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs uppercase tracking-wide font-semibold text-amber-200">
            {lang === 'en' ? `Watchlist (${watchlist.length})` : `Watchlist (${watchlist.length})`}
          </div>
          <p className="text-xs text-muted-foreground">
            {lang === 'en'
              ? 'No bet, just monitor: marginal edge or pending line movement.'
              : 'Sin apuesta — monitorear: edge marginal o esperar movimiento de línea.'}
          </p>
          <div className="grid gap-2">
            {watchlist.map((w, i) => <WatchlistRow key={(w.match_id || '') + '-' + i} item={w} testId={`watchlist-row-${i}`} />)}
          </div>
        </div>
      )}

      {/* No-value message — ONLY shown when no recommended picks; rescued/watchlist/discarded still appear */}
      {!loading && data && data.verdict === 'no_value' && high.length === 0 && medium.length === 0 && rescued.length === 0 && protectedAcceptable.length === 0 && (
        <EmptyStateNoValue summary={data.summary} watchlistCount={watchlist.length} />
      )}

      {/* Quick summary when no picks but we DO have rescued/watchlist content */}
      {!loading && data && high.length === 0 && medium.length === 0 && (rescued.length > 0 || watchlist.length > 0 || protectedAcceptable.length > 0) && (
        <div className="rounded-lg border border-border bg-card/40 px-4 py-3 text-sm" data-testid="no-strong-picks-banner">
          <span className="font-semibold text-foreground/90">
            {lang === 'en' ? 'No strong picks found' : 'No encontramos apuestas fuertes'}
          </span>
          <span className="text-muted-foreground">
            {lang === 'en'
              ? `, but ${watchlist.length} on watchlist · ${rescued.length} rescued · ${protectedAcceptable.length} protected acceptable.`
              : `, pero ${watchlist.length} en watchlist · ${rescued.length} rescatados · ${protectedAcceptable.length} mercados protegidos aceptables.`}
          </span>
        </div>
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
              <div className="grid gap-2">{discMot.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-motivation-row" type="motivation" sport={sport} />)}</div>
            </GroupSection>
          )}
          {discMkt.length > 0 && (
            <GroupSection
              title={
                isBaseball
                  ? (lang === 'en'
                      ? 'Discarded after full MLB analysis'
                      : 'Descartados tras análisis MLB completo')
                  : t.dashboard.groupDiscMarket
              }
              count={discMkt.length} tier="Below" defaultOpen={true} testId="group-discarded-market" sectionRef={refs.discMkt} icon={Shield}>
              <div className="grid gap-2">{discMkt.map((d, i) => <DiscardedRow key={i} item={d} testId="discarded-market-row" type="market" sport={sport} />)}</div>
            </GroupSection>
          )}
          {incomplete.length > 0 && (
            <GroupSection title={t.dashboard.groupIncomplete} count={incomplete.length} tier="Below" defaultOpen={true} testId="group-incomplete" sectionRef={refs.incomplete} icon={AlertCircle}>
              <div className="grid gap-2">{incomplete.map((d, i) => <DiscardedRow key={i} item={{ match_id: d.match_id, match_label: d.match_label, reason: d.missing }} testId="incomplete-data-row" type="incomplete" sport={sport} />)}</div>
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
