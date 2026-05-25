import { Trophy, ShieldAlert, Sparkles, Database, AlertTriangle, Search } from 'lucide-react';

/**
 * FootballQualityBadge — Compact pill rendered inline on each football
 * MatchCard. Surfaces the Phase 8 selection state (PRIORITY_MATCH /
 * HIGH_LIQUIDITY / STANDARD / EXOTIC_LEAGUE_WARNING / LOW_DATA_QUALITY /
 * LOW_MARKET_SUPPORT) plus the Tier number, so the user immediately knows
 * whether the engine considered this match high-signal.
 */
const STATE_META = {
  PRIORITY_MATCH:        { tone: 'emerald', icon: Trophy,        es: 'PRIORIDAD',    en: 'PRIORITY' },
  HIGH_LIQUIDITY:        { tone: 'cyan',    icon: Sparkles,      es: 'LIQUIDEZ ALTA', en: 'HIGH LIQUIDITY' },
  STANDARD:              { tone: 'slate',   icon: null,           es: 'ESTÁNDAR',     en: 'STANDARD' },
  LOW_DATA_QUALITY:      { tone: 'amber',   icon: Database,      es: 'DATOS BAJOS',  en: 'LOW DATA' },
  LOW_MARKET_SUPPORT:    { tone: 'amber',   icon: AlertTriangle, es: 'MERCADO DÉBIL', en: 'WEAK MARKET' },
  EXOTIC_LEAGUE_WARNING: { tone: 'rose',    icon: ShieldAlert,   es: 'LIGA EXÓTICA',  en: 'EXOTIC LEAGUE' },
  SKIPPED_LOW_RELEVANCE:   { tone: 'rose',    icon: ShieldAlert,   es: 'BAJA RELEVANCIA',     en: 'LOW RELEVANCE' },
  PRIORITY_OVERRIDE:       { tone: 'cyan',    icon: Sparkles,      es: 'PRIORIDAD OVERRIDE',  en: 'PRIORITY OVERRIDE' },
  ALTERNATIVE_MARKET_SCAN: { tone: 'amber',   icon: Search,        es: 'MERCADO ALTERNATIVO', en: 'ALT. MARKET' },
};

const TONE_CLASSES = {
  emerald: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  cyan:    'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
  amber:   'border-amber-500/40 bg-amber-500/10 text-amber-200',
  rose:    'border-red-500/40 bg-red-500/10 text-red-200',
  slate:   'border-border bg-secondary/50 text-muted-foreground',
};

export function FootballQualityBadge({ quality, lang = 'es', testId = 'football-quality-badge' }) {
  if (!quality) return null;
  const state = quality.state || 'STANDARD';
  const meta = STATE_META[state] || STATE_META.STANDARD;
  const tone = TONE_CLASSES[meta.tone] || TONE_CLASSES.slate;
  const Icon = meta.icon;
  const tier = quality.tier;
  const score = quality.score;
  const liq = quality.market_liquidity?.score;

  const titleLines = [
    `${lang === 'en' ? meta.en : meta.es} · Tier ${tier} · Score ${score}/100`,
    quality.priority_reason || quality.skip_reason || '',
    liq != null ? `${lang === 'en' ? 'Liquidity' : 'Liquidez'} ${liq}/100` : '',
  ].filter(Boolean).join('\n');

  return (
    <span
      data-testid={testId}
      title={titleLines}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono-tabular font-semibold border ${tone}`}
    >
      {Icon && <Icon className="h-3 w-3" />}
      {lang === 'en' ? meta.en : meta.es}
      <span className="opacity-70 ml-0.5">T{tier}·{score}</span>
    </span>
  );
}

/**
 * SkippedMatchRow — Renders one entry of the "Skipped low relevance" list
 * inside the Dashboard descartados section, with a clear reason.
 */
export function SkippedMatchRow({ item, lang = 'es', testId = 'skipped-match-row' }) {
  if (!item) return null;
  const stateMeta = STATE_META[item.state] || STATE_META.SKIPPED_LOW_RELEVANCE;
  const tone = TONE_CLASSES[stateMeta.tone] || TONE_CLASSES.rose;
  const Icon = stateMeta.icon;

  return (
    <div className="rounded-lg border border-border/60 bg-card/60 p-3 flex flex-col gap-2" data-testid={testId}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{item.match_label || '—'}</div>
          <div className="text-[11px] text-muted-foreground mt-0.5">
            {item.league || (lang === 'en' ? 'Unknown league' : 'Liga desconocida')}
            {item.tier ? ` · Tier ${item.tier}` : ''}
            {item.score != null ? ` · ${item.score}/100` : ''}
          </div>
        </div>
        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono-tabular font-semibold border ${tone} shrink-0`}>
          {Icon && <Icon className="h-3 w-3" />}
          {lang === 'en' ? stateMeta.en : stateMeta.es}
        </span>
      </div>
      {item.reason && (
        <p className="text-[11px] text-muted-foreground italic leading-relaxed">{item.reason}</p>
      )}
    </div>
  );
}
