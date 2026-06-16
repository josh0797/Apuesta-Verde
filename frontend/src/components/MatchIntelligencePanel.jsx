import { useMemo, useState, useCallback } from 'react';
import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, ResponsiveContainer,
} from 'recharts';
import {
  Activity, AlertTriangle, Brain, ShieldCheck, Target, Zap,
  Layers, X, Check, BadgeAlert, History,
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useI18n } from '@/lib/i18n';
import {
  deriveIntelligence, DRIVER_META, MATCH_STATES,
  detectContradictions,
} from '@/lib/intelligence';
import { HistoricalPatternBadge } from './HistoricalPatternBadge';
import { ContradictionWarnings } from './ContradictionWarnings';
import { EngineNarrativeBlock } from './EngineNarrativeBlock';
import { ConfidenceBreakdown } from './ConfidenceBreakdown';
import { CornersEnrichmentButton } from './CornersEnrichmentButton';
import { CornersProfilePanel } from './CornersProfilePanel';
import { PublicXGPanel } from './PublicXGPanel';

const ICON_MAP = { Activity, ShieldCheck, Target, Zap, Brain };

/**
 * MatchIntelligencePanel — terminal-style narrative breakdown of a pick.
 *
 * Sections (top → bottom):
 *   1. Contradiction warnings (only when present)
 *   2. Engine narrative (says / avoids / cautious because)
 *   3. Signals strip (4 key tags)
 *   4. Risk radar + Drivers timeline
 *   5. Confidence breakdown (decomposed factors)
 *   6. Markets matrix (best for / avoid)
 *   7. Historical learning v2 (HistoricalPatternBadge)
 *   8. Risk breakdown (analyst risks + cash-out advice)
 */
export function MatchIntelligencePanel({ pick, sport = 'football' }) {
  const { lang } = useI18n();
  const intel = useMemo(() => (pick ? deriveIntelligence(pick, sport) : null), [pick, sport]);
  const [historicalSignal, setHistoricalSignal] = useState(null);

  const onHistoricalSignal = useCallback((sig) => {
    setHistoricalSignal(sig);
  }, []);

  const contradictions = useMemo(
    () => (pick && intel ? detectContradictions(pick, intel, historicalSignal) : []),
    [pick, intel, historicalSignal],
  );

  const radarData = useMemo(() => {
    if (!intel) return [];
    return [
      { axis: lang === 'en' ? 'Confidence' : 'Confianza',     value: Math.round(intel.confidence) },
      { axis: lang === 'en' ? 'Calm market' : 'Mercado calm', value: Math.round(100 - intel.volatility) },
      { axis: lang === 'en' ? 'Signal clarity' : 'Claridad',  value: Math.round(100 - (intel.risks?.length ?? 0) * 15) },
      { axis: lang === 'en' ? 'Stability' : 'Estabilidad',    value: Math.round(100 - intel.fragility) },
      { axis: lang === 'en' ? 'Low risk' : 'Riesgo bajo',     value: Math.round(100 - intel.risk) },
    ].map((r) => ({ ...r, value: Math.max(0, Math.min(100, r.value)) }));
  }, [intel, lang]);

  if (!pick || !intel) return null;

  const ms = MATCH_STATES[intel.matchState] || MATCH_STATES.CONTROLLED_MATCH;
  const MsIcon = ICON_MAP[ms.icon] || ShieldCheck;

  const labels = lang === 'en'
    ? {
        signals: 'Key signals', radar: 'Risk radar', timeline: 'Drivers timeline',
        markets: 'Markets matrix', breakdown: 'Risk breakdown', risks: 'Risk flags',
        cashOut: 'Cash-out advice', bestFor: 'Best for', avoid: 'Avoid',
        positive: 'Positive contributions', negative: 'Negative contributions',
        noPositive: 'No strong positive driver detected.',
        noNegative: 'No critical negative driver detected.',
        noFlags: 'No risk flags emitted by the engine.',
        noCashOut: 'No cash-out advice for this pick.',
        scoreUnit: '/ 100',
        historical: 'HISTORICAL LEARNING v2',
      }
    : {
        signals: 'Señales clave', radar: 'Radar de riesgo', timeline: 'Línea de drivers',
        markets: 'Matriz de mercados', breakdown: 'Desglose de riesgo', risks: 'Banderas de riesgo',
        cashOut: 'Consejo de cash-out', bestFor: 'Ideal para', avoid: 'Evitar',
        positive: 'Contribuciones positivas', negative: 'Contribuciones negativas',
        noPositive: 'Sin driver positivo fuerte.',
        noNegative: 'Sin driver negativo crítico.',
        noFlags: 'El motor no emitió banderas de riesgo.',
        noCashOut: 'Sin consejo de cash-out para este pick.',
        scoreUnit: '/ 100',
        historical: 'APRENDIZAJE HISTÓRICO v2',
      };

  const driversPos = intel.drivers.filter((d) => d.sign === 'positive');
  const driversNeg = intel.drivers.filter((d) => d.sign === 'negative');
  const driversNeu = intel.drivers.filter((d) => d.sign === 'neutral' || !d.sign);

  return (
    <section
      data-testid="match-intelligence-panel"
      className="rounded-xl border border-border/80 bg-card/50 overflow-hidden"
    >
      {/* Header */}
      <div className="px-4 md:px-5 py-3 border-b border-border/50 flex items-center gap-2">
        <Brain className="h-4 w-4 text-cyan-300" />
        <span className="micro-label tracking-[0.18em] text-foreground/90">DECISION INTELLIGENCE</span>
      </div>

      {/* (1) Contradictions banner — only renders when contradictions exist */}
      {contradictions.length > 0 && (
        <div className="px-4 md:px-5 py-4 border-b border-border/40">
          <ContradictionWarnings contradictions={contradictions} />
        </div>
      )}

      {/* (2) Engine narrative */}
      <div className="px-4 md:px-5 py-4 border-b border-border/40">
        <EngineNarrativeBlock
          pick={pick}
          intel={intel}
          historicalSignal={historicalSignal}
          contradictions={contradictions}
        />
      </div>

      {/* (3) Signals strip */}
      <div className="px-4 md:px-5 py-3 border-b border-border/40">
        <div className="micro-label mb-2">{labels.signals}</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2" data-testid="signals-strip">
          <SignalCell tone={ms.tone} icon={MsIcon} label={labels.signals} value={lang === 'en' ? ms.label_en : ms.label_es} testId="signal-state" />
          <SignalCell
            tone={intel.confidence >= 70 ? 'emerald' : intel.confidence >= 60 ? 'amber' : 'slate'}
            icon={Target}
            label={lang === 'en' ? 'Confidence' : 'Confianza'}
            value={`${intel.confidence}${labels.scoreUnit}`}
            testId="signal-confidence"
          />
          <SignalCell
            tone={intel.volatility < 30 ? 'emerald' : intel.volatility < 55 ? 'amber' : 'rose'}
            icon={Activity}
            label={lang === 'en' ? 'Volatility' : 'Volatilidad'}
            value={`${intel.volatility}${labels.scoreUnit}`}
            testId="signal-volatility"
          />
          <SignalCell
            tone={intel.fragility < 30 ? 'emerald' : intel.fragility < 55 ? 'amber' : 'rose'}
            icon={Layers}
            label={lang === 'en' ? 'Fragility' : 'Fragilidad'}
            value={`${intel.fragility}${labels.scoreUnit}`}
            testId="signal-fragility"
          />
        </div>
      </div>

      {/* (4) Radar + drivers timeline */}
      <div className="grid md:grid-cols-2">
        <div className="p-4 md:p-5 md:border-r md:border-border/40" data-testid="risk-radar">
          <div className="micro-label mb-2">{labels.radar}</div>
          <div className="h-[230px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData} margin={{ top: 8, right: 12, bottom: 8, left: 12 }} outerRadius="72%">
                <PolarGrid stroke="hsl(var(--border))" radialLines={false} />
                <PolarAngleAxis
                  dataKey="axis"
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                  tickLine={false}
                />
                <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} axisLine={false} />
                <Radar
                  dataKey="value"
                  stroke="hsl(var(--chart-positive))"
                  fill="hsl(var(--chart-positive))"
                  fillOpacity={0.18}
                  strokeWidth={1.5}
                  isAnimationActive={false}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="p-4 md:p-5" data-testid="drivers-timeline">
          <div className="micro-label mb-2">{labels.timeline}</div>
          <DriverList title={labels.positive} drivers={driversPos} fallback={labels.noPositive} sign="positive" lang={lang} />
          <DriverList title={labels.negative} drivers={driversNeg} fallback={labels.noNegative} sign="negative" lang={lang} />
          {driversNeu.length > 0 && (
            <DriverList title={lang === 'en' ? 'Neutral' : 'Neutral'} drivers={driversNeu} sign="neutral" lang={lang} />
          )}
        </div>
      </div>

      {/* (5) Confidence breakdown */}
      <div className="px-4 md:px-5 py-4 border-t border-border/40" data-testid="confidence-breakdown-section">
        <ConfidenceBreakdown pick={pick} />
      </div>

      {/* (6) Markets matrix */}
      <div className="px-4 md:px-5 py-4 border-t border-border/40">
        <div className="micro-label mb-2">{labels.markets}</div>
        <div className="grid md:grid-cols-2 gap-3" data-testid="markets-matrix">
          <MarketCell
            label={labels.bestFor}
            markets={intel.bestFor || []}
            kind="best"
            empty={lang === 'en' ? 'No safe markets matched.' : 'Ningún mercado seguro coincidió.'}
          />
          <MarketCell
            label={labels.avoid}
            markets={intel.avoid || []}
            kind="avoid"
            empty={lang === 'en' ? 'No fragile markets to avoid right now.' : 'No hay mercados frágiles que evitar.'}
          />
        </div>
      </div>

      {/* (7) Historical learning v2 */}
      <div className="px-4 md:px-5 py-4 border-t border-border/40">
        <div className="micro-label mb-2 flex items-center gap-1.5">
          <History className="h-3 w-3" />{labels.historical}
        </div>
        <HistoricalPatternBadge pick={pick} sport={sport} onSignal={onHistoricalSignal} />
      </div>

      {/* (8) Risk breakdown */}
      <div className="px-4 md:px-5 py-4 border-t border-border/40">
        <div className="micro-label mb-2">{labels.breakdown}</div>
        <div className="grid md:grid-cols-2 gap-4">
          <div data-testid="risk-flags">
            <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground mb-1.5">
              <BadgeAlert className="h-3.5 w-3.5" /> {labels.risks}
            </div>
            {(pick.risks && pick.risks.length > 0) ? (
              <ul className="space-y-1">
                {pick.risks.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-[12px] text-rose-200/90">
                    <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[11.5px] text-muted-foreground italic">{labels.noFlags}</p>
            )}
          </div>
          <div data-testid="cashout-advice">
            <div className="flex items-center gap-1.5 text-[12px] text-muted-foreground mb-1.5">
              <ShieldCheck className="h-3.5 w-3.5" /> {labels.cashOut}
            </div>
            {pick.cash_out ? (
              <p className="text-[12px] text-cyan-200/90 leading-relaxed">{pick.cash_out}</p>
            ) : (
              <p className="text-[11.5px] text-muted-foreground italic">{labels.noCashOut}</p>
            )}
          </div>
        </div>

        {/* Phase F82.2 — Manual 365Scores corners enrichment button +
            engine cross profile (L5 vs L15). Gated by sport==='football'
            internally. Renders the cross block whenever the engine
            attached one and exposes the button to refresh corners from
            365Scores on demand. */}
        {sport === 'football' && (
          <CornersEnrichmentButton match={pick} lang={lang} />
        )}
        {/* FIX-4 — Pre-match Corners Profile (L1/L5/L15 + momentum +
            expected corners + line projections). Always pre-match-safe:
            absence of current-fixture corners is NOT an error. */}
        {sport === 'football' && (pick?.match_id || pick?.id) && (
          <CornersProfilePanel
            matchId={pick.match_id || pick.id}
            lang={lang}
            initialProfile={pick.corners_profile || null}
          />
        )}
        {/* Phase F83.2-E4 — xG L1/L5/L15 from TheStatsAPI shotmap. */}
        {sport === 'football' && (
          <XGRecentAveragesPanel match={pick} lang={lang} />
        )}
        {/* Phase F85 — Public xG enrichment (FBref + Forebet). */}
        {sport === 'football' && (
          <PublicXGPanel match={pick} lang={lang} />
        )}
      </div>
    </section>
  );
}

function SignalCell({ tone, icon: Icon, label, value, testId }) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            data-testid={testId}
            className={`flex flex-col gap-1 p-2.5 rounded-lg border bg-background/30 tone-${tone} cursor-help`}
          >
            <div className="flex items-center gap-1.5">
              <Icon className="h-3.5 w-3.5" />
              <span className="text-[10.5px] uppercase tracking-wider opacity-80">{label}</span>
            </div>
            <div className="text-[13px] font-mono-tabular font-semibold leading-none">{value}</div>
          </div>
        </TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[220px]">
          {label}: {value}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function DriverList({ title, drivers, fallback, sign, lang }) {
  return (
    <div className="mb-2">
      <div className={`text-[11px] font-medium mb-1 ${sign === 'positive' ? 'text-emerald-300/80' : sign === 'negative' ? 'text-rose-300/80' : 'text-muted-foreground'}`}>
        {title}
      </div>
      {drivers.length === 0 && fallback ? (
        <p className="text-[11px] text-muted-foreground italic">{fallback}</p>
      ) : (
        <ul className="space-y-1">
          {drivers.map((d) => {
            const meta = DRIVER_META[d.key] || {};
            const label = lang === 'en' ? meta.label_en : meta.label_es;
            const detail = lang === 'en' ? d.detail_en : d.detail_es;
            const dot = sign === 'positive' ? 'bg-emerald-400' : sign === 'negative' ? 'bg-rose-400' : 'bg-cyan-300';
            return (
              <li key={d.key} className="flex items-start gap-2 text-[12px]">
                <span className={`h-1.5 w-1.5 rounded-full mt-1.5 shrink-0 ${dot}`} />
                <div className="min-w-0">
                  <span className="font-medium">{label}</span>
                  {detail && <span className="text-muted-foreground"> — {detail}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function MarketCell({ label, markets, kind, empty }) {
  const tone = kind === 'avoid' ? 'rose' : 'emerald';
  const Icon = kind === 'avoid' ? X : Check;
  return (
    <div className={`rounded-lg border p-3 tone-${tone}`} data-testid={`market-cell-${kind}`}>
      <div className="flex items-center gap-1.5 text-[12px] font-semibold mb-2 opacity-90">
        <Icon className="h-3.5 w-3.5" />
        <span className="uppercase tracking-wider text-[10.5px]">{label}</span>
      </div>
      {markets && markets.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {markets.map((m, i) => (
            <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] bg-background/40 border border-current/20">
              {m}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-[11.5px] opacity-70 italic">{empty}</p>
      )}
    </div>
  );
}
