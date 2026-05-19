import {
  TrendingUp, Target, Home, UserMinus, BatteryWarning, Shuffle,
  ShieldCheck, Activity, Flame, Clock, Check, X, Info, Lock, AlertTriangle, Gauge,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { Progress } from '@/components/ui/progress';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { tierClass, confidenceTier } from '@/lib/format';
import { DRIVER_META, MATCH_STATES, deriveIntelligence } from '@/lib/intelligence';

const ICON_MAP = {
  TrendingUp, Target, Home, UserMinus, BatteryWarning, Shuffle,
  ShieldCheck, Activity, Flame, Clock,
};

function Tag({ tone = 'slate', icon: Icon, children, testId }) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] leading-5 tone-${tone}`}
    >
      {Icon && <Icon className="h-3 w-3 shrink-0" />}
      <span className="truncate">{children}</span>
    </span>
  );
}

function XAITooltip({ children, why, testId }) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild data-testid={testId}>
          <span className="inline-flex items-center gap-1 cursor-help">{children}<Info className="h-3 w-3 opacity-50" /></span>
        </TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[260px] leading-relaxed">{why}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function MetricRow({ label, value, tier, why, testId }) {
  const tone = tier === 'high' ? 'rose' : tier === 'medium' ? 'amber' : 'emerald';
  return (
    <div className="flex items-center justify-between gap-3 terminal-row" data-testid={testId}>
      <span className="micro-label">{label}</span>
      <div className="flex items-center gap-2">
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider tone-${tone}`}>
          {tier === 'high' ? (
            <>ALTA</>
          ) : tier === 'medium' ? (
            <>MEDIA</>
          ) : (
            <>BAJA</>
          )}
        </span>
        <XAITooltip why={why}>
          <span className="font-mono-tabular text-[13px] text-foreground">{Math.round(value)}</span>
        </XAITooltip>
      </div>
    </div>
  );
}

function DriverRow({ driver, lang }) {
  const meta = DRIVER_META[driver.key] || {};
  const Icon = ICON_MAP[meta.icon] || Activity;
  const sign = driver.sign;
  const tone = sign === 'positive' ? 'emerald' : sign === 'negative' ? 'rose' : 'slate';
  const label = lang === 'en' ? meta.label_en : meta.label_es;
  const detail = lang === 'en' ? driver.detail_en : driver.detail_es;
  const strength = Math.max(0, Math.min(100, driver.strength || 0));
  return (
    <div className="flex items-center gap-3 py-1.5" data-testid={`driver-${driver.key}`}>
      <div className={`h-7 w-7 rounded-md flex items-center justify-center border tone-${tone}`}>
        <Icon className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-foreground truncate">{label}</span>
          <span className={`text-[10px] font-mono-tabular ${sign === 'positive' ? 'text-emerald-300' : sign === 'negative' ? 'text-rose-300' : 'text-muted-foreground'}`}>
            {sign === 'positive' ? '+' : sign === 'negative' ? '−' : '·'}{strength}
          </span>
        </div>
        <div className="mt-1 h-1 rounded-full bg-secondary/60 overflow-hidden">
          <div
            className={`h-full ${sign === 'positive' ? 'bg-emerald-400/70' : sign === 'negative' ? 'bg-rose-400/60' : 'bg-cyan-400/50'}`}
            style={{ width: `${strength}%`, transition: 'width 220ms ease' }}
          />
        </div>
        {detail && (
          <div className="text-[10.5px] text-muted-foreground mt-0.5 leading-snug">{detail}</div>
        )}
      </div>
    </div>
  );
}

function MarketList({ markets, kind, testId }) {
  if (!markets || markets.length === 0) return null;
  const Icon = kind === 'avoid' ? X : Check;
  const tone = kind === 'avoid' ? 'rose' : 'emerald';
  return (
    <div data-testid={testId}>
      <div className="micro-label mb-1.5">
        {kind === 'avoid' ? 'EVITAR' : 'IDEAL PARA'}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {markets.map((m, i) => (
          <span key={`${kind}-${i}`} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] tone-${tone}`}>
            <Icon className="h-3 w-3" />
            <span>{m}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * Public API
 * ----------
 * <ConfidenceIntelligenceCard pick={pick} sport={sport} compact={false} />
 *
 * Props:
 *   - pick: full pick object from analyst output. If absent, falls back to score-only.
 *   - score: legacy prop (kept for callers that only have a numeric score)
 *   - sport: 'football' | 'basketball' | 'baseball'
 *   - compact: render the inline (row) version used in PickCards
 *
 * Backward compatibility:
 *   - data-testid="confidence-meter" remains on the root element.
 *   - When only `score` is given, behaves like the legacy ConfidenceMeter.
 */
export function ConfidenceIntelligenceCard({ pick, score, sport = 'football', compact = false, testId }) {
  const { t, lang } = useI18n();
  // Derive intel; if no pick is provided we degrade to score-only.
  const intel = pick ? deriveIntelligence(pick, sport) : null;
  const finalScore = intel ? intel.confidence : Math.max(0, Math.min(100, score || 0));
  const tier = confidenceTier(finalScore);
  const tierCls = tierClass(tier);
  const label = tier === 'Below' ? '< 68' : t.confidence[tier] || tier;
  const ms = intel?.matchState ? MATCH_STATES[intel.matchState] : null;
  const MsIcon = ms ? (ICON_MAP[ms.icon] || ShieldCheck) : null;

  // Compact variant for pick rows
  if (compact) {
    return (
      <div data-testid={testId || 'confidence-meter'} className={`inline-flex items-center gap-2 rounded-md border ${tierCls} px-2 py-1`}>
        <Gauge className="h-3.5 w-3.5 opacity-70" />
        <span className="font-mono-tabular text-sm font-semibold leading-none">{Math.round(finalScore)}</span>
        <span className="text-[10px] font-semibold uppercase tracking-wider opacity-80">{label}</span>
        {ms && (
          <span className={`hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${ms.tone}`}>
            <MsIcon className="h-3 w-3" />
            <span>{lang === 'en' ? ms.label_en : ms.label_es}</span>
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid={testId || 'confidence-meter'}
      data-confidence-card="true"
      className={`rounded-xl border ${tierCls} bg-card/60 overflow-hidden`}
    >
      {/* Header — Score + Tier + Match state */}
      <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3 border-b border-border/60">
        <div className="flex items-center gap-3">
          <div className="flex flex-col">
            <span className="micro-label">SCORE DE CONFIANZA</span>
            <XAITooltip
              why={lang === 'en'
                ? 'Composite score blending motivation, form, market clarity and data freshness. Above 70 = high.'
                : 'Score compuesto que combina motivación, forma, claridad de mercado y frescura de datos. Sobre 70 = alta.'}
              testId="confidence-intelligence-score"
            >
              <span className="font-mono-tabular text-3xl font-semibold leading-none tracking-tight">{Math.round(finalScore)}</span>
            </XAITooltip>
          </div>
          <div className="flex flex-col gap-1">
            <span className="px-2 py-0.5 rounded-md border text-[10px] font-semibold uppercase tracking-wider self-start bg-background/40">
              {label}
            </span>
            {ms && (
              <Tag tone={ms.tone} icon={MsIcon} testId="match-state-tag">
                {lang === 'en' ? ms.label_en : ms.label_es}
              </Tag>
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="grid md:grid-cols-3 gap-0 md:gap-4 p-4">
        {/* Column 1: Progress + reasoning summary */}
        <div className="space-y-3 md:col-span-1">
          <div>
            <Progress
              value={Math.max(0, Math.min(100, finalScore))}
              className="h-2"
              data-testid="confidence-intelligence-progress"
            />
            <div className="flex justify-between text-[10px] mt-1 text-muted-foreground font-mono-tabular">
              <span>0</span><span>60</span><span>70</span><span>80</span><span>100</span>
            </div>
          </div>
          {intel?.reasoning && (
            <p className="text-[12px] text-muted-foreground leading-relaxed">{intel.reasoning}</p>
          )}
        </div>

        {/* Column 2: Drivers */}
        {intel?.drivers?.length > 0 && (
          <div className="md:col-span-1 md:border-l md:border-border/50 md:pl-4 mt-3 md:mt-0" data-testid="confidence-intelligence-drivers">
            <div className="micro-label mb-2 flex items-center gap-1.5"><Target className="h-3 w-3" />DRIVERS</div>
            <div className="space-y-0.5">
              {intel.drivers.slice(0, 5).map((d) => (
                <DriverRow key={d.key} driver={d} lang={lang} />
              ))}
            </div>
          </div>
        )}

        {/* Column 3: Risk / Volatility / Fragility + Best-for/Avoid */}
        {intel && (
          <div className="md:col-span-1 md:border-l md:border-border/50 md:pl-4 mt-3 md:mt-0 space-y-3">
            <div>
              <div className="micro-label mb-1.5 flex items-center gap-1.5"><AlertTriangle className="h-3 w-3" />SEÑALES DE RIESGO</div>
              <MetricRow
                label="Riesgo"
                value={intel.risk}
                tier={intel.risk >= 60 ? 'high' : intel.risk >= 35 ? 'medium' : 'low'}
                why={lang === 'en'
                  ? 'Composite of inverse confidence + volatility + fragility. Lower is better.'
                  : 'Compuesto de confianza inversa + volatilidad + fragilidad. Menor es mejor.'}
                testId="metric-risk"
              />
              <MetricRow
                label="Volatilidad"
                value={intel.volatility}
                tier={intel.volatility >= 55 ? 'high' : intel.volatility >= 30 ? 'medium' : 'low'}
                why={lang === 'en'
                  ? 'Contradiction in signals (data staleness, motivation gap, sparse odds).'
                  : 'Contradicción entre señales (datos antiguos, brecha motivacional, pocas odds).'}
                testId="metric-volatility"
              />
              <MetricRow
                label="Fragilidad"
                value={intel.fragility}
                tier={intel.fragility >= 55 ? 'high' : intel.fragility >= 30 ? 'medium' : 'low'}
                why={lang === 'en'
                  ? 'How easily this bet collapses: line stability, key absences, market type.'
                  : 'Cuán fácil se cae esta apuesta: estabilidad de línea, bajas, tipo de mercado.'}
                testId="metric-fragility"
              />
            </div>
            <div className="space-y-2">
              <MarketList markets={intel.bestFor} kind="best" testId="confidence-intelligence-best-for" />
              <MarketList markets={intel.avoid} kind="avoid" testId="confidence-intelligence-avoid" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Backward-compatible simple meter — kept for the few existing callers that only
 * pass a numeric `score`. Internally delegates to the rich card with no pick.
 */
export function ConfidenceMeter({ score = 0, size = 'md', testId }) {
  // Inline pill (legacy small variant)
  if (size === 'sm' || size === 'inline') {
    return <ConfidenceIntelligenceCard score={score} compact testId={testId} />;
  }
  return <ConfidenceIntelligenceCard score={score} testId={testId} />;
}
