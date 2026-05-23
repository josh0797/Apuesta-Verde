import { useMemo, useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { TrendingUp, TrendingDown, Minus, Flame, Trophy, Target, Layers } from 'lucide-react';

/**
 * SportStatsPanel — Sport-segmented KPIs.
 *
 * Two halves:
 *  1) Cross-sport comparator card (top). Side-by-side ROI / win-rate /
 *     settled for every sport the user has tracked — at a glance you see
 *     where your edge is concentrated.
 *  2) Tab per sport with the same KPI block users had globally before
 *     (Win Rate, ROI, accuracy_by_tier, top markets, last10).
 *
 * Data contract — expects the new Phase P2 shape returned by
 * /api/stats/dashboard:
 *   data.cross_sport_comparison: Array<{sport, total, settled, win_rate,
 *                                       roi_pct, total_profit, streak}>
 *   data.by_sport:        Record<sport, KPIBlock>
 *   data.by_sport_market: Record<sport, Record<market, KPIBlock>>
 *
 * Backward compat: falls back to the legacy global top-level shape if the
 * new fields are absent (older API responses).
 */
const SPORT_META = {
  football:   { icon: '⚽', label_es: 'Fútbol',   label_en: 'Football'   },
  basketball: { icon: '🏀', label_es: 'NBA',      label_en: 'Basketball' },
  baseball:   { icon: '⚾', label_es: 'MLB',      label_en: 'Baseball'   },
};

function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return '—';
  const v = Number(n);
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
}
function fmtMoney(n) {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}$${Math.abs(Number(n)).toFixed(2)}`;
}

function toneForRoi(roi) {
  if (roi == null) return 'neutral';
  if (roi >= 5) return 'good';
  if (roi <= -5) return 'bad';
  return 'flat';
}
const TONE_BG = {
  good:    'border-emerald-500/30 bg-emerald-500/5 text-emerald-200',
  flat:    'border-border bg-secondary/30 text-foreground',
  bad:     'border-red-500/30 bg-red-500/5 text-red-200',
  neutral: 'border-border bg-secondary/20 text-muted-foreground',
};

function sportLabel(sport, lang) {
  const m = SPORT_META[sport];
  if (!m) return sport;
  return lang === 'en' ? m.label_en : m.label_es;
}

export function CrossSportComparator({ rows, lang = 'es', testId = 'cross-sport-comparator' }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div
      className="rounded-xl border border-border bg-card p-4 sm:p-5 space-y-3"
      data-testid={testId}
    >
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 text-cyan-300" />
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {lang === 'en' ? 'Cross-sport performance' : 'Rendimiento por deporte'}
        </h3>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {rows.map((r) => {
          const tone = TONE_BG[toneForRoi(r.roi_pct)] || TONE_BG.neutral;
          const meta = SPORT_META[r.sport];
          return (
            <div
              key={r.sport}
              className={`rounded-lg border p-3 ${tone}`}
              data-testid={`cross-sport-card-${r.sport}`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <span className="text-base" aria-hidden>{meta?.icon || '🎯'}</span>
                  <span className="text-xs font-semibold uppercase tracking-wide">
                    {sportLabel(r.sport, lang)}
                  </span>
                </div>
                <RoiBadge roi={r.roi_pct} />
              </div>
              <div className="mt-2 flex items-end justify-between gap-3">
                <div>
                  <div className="text-[10px] uppercase opacity-70 leading-none">
                    {lang === 'en' ? 'Win rate' : 'Acierto'}
                  </div>
                  <div className="font-mono-tabular text-2xl font-semibold tabular-nums">
                    {r.win_rate ?? 0}%
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] uppercase opacity-70 leading-none">
                    {lang === 'en' ? 'P&L' : 'Ganancia'}
                  </div>
                  <div className="font-mono-tabular text-sm font-semibold tabular-nums">
                    {fmtMoney(r.total_profit)}
                  </div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-1.5 text-[10px] opacity-80">
                <Cell label={lang === 'en' ? 'Total' : 'Total'} value={r.total ?? 0} />
                <Cell label={lang === 'en' ? 'Settled' : 'Liquidados'} value={r.settled ?? 0} />
                <Cell label={lang === 'en' ? 'Streak' : 'Racha'} value={r.streak ?? 0} icon={r.streak >= 3 ? Flame : null} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RoiBadge({ roi }) {
  const tone = toneForRoi(roi);
  const Icon = tone === 'good' ? TrendingUp : tone === 'bad' ? TrendingDown : Minus;
  const cls = tone === 'good'
    ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200'
    : tone === 'bad'
      ? 'border-red-500/40 bg-red-500/15 text-red-200'
      : 'border-border bg-secondary/40 text-muted-foreground';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono-tabular font-semibold border ${cls}`}>
      <Icon className="h-3 w-3" />
      {fmtPct(roi)}
    </span>
  );
}

function Cell({ label, value, icon: Icon }) {
  return (
    <div className="rounded border border-border/50 bg-background/30 px-1.5 py-1">
      <div className="uppercase opacity-70 leading-none">{label}</div>
      <div className="font-mono-tabular text-foreground mt-0.5 flex items-center gap-1 tabular-nums">
        {Icon && <Icon className="h-3 w-3 text-amber-300" />}
        <span>{value}</span>
      </div>
    </div>
  );
}

export function SportStatsPanel({ data, lang = 'es', stake = 10, testId = 'sport-stats-panel' }) {
  const sportsAvailable = useMemo(() => {
    const bySport = data?.by_sport || {};
    const keys = Object.keys(bySport);
    // Stable order: football → basketball → baseball → anything else.
    const order = ['football', 'basketball', 'baseball'];
    return [...keys].sort((a, b) => {
      const ia = order.indexOf(a);
      const ib = order.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
  }, [data]);

  const [activeSport, setActiveSport] = useState(sportsAvailable[0] || 'football');

  // No tracking data at all → render an empty-state hint.
  if (!data) return null;
  if (sportsAvailable.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card/40 p-5 text-center" data-testid={`${testId}-empty`}>
        <Trophy className="h-5 w-5 mx-auto text-muted-foreground" />
        <p className="text-sm text-muted-foreground mt-2">
          {lang === 'en'
            ? 'No tracked picks yet. Mark picks as Won / Lost / Push to populate per-sport stats.'
            : 'Aún no hay picks liquidados. Marca como Gané / Perdí / Push para poblar las estadísticas por deporte.'}
        </p>
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-4" data-testid={testId}>
        <CrossSportComparator rows={data.cross_sport_comparison || []} lang={lang} />

        <Tabs value={activeSport} onValueChange={setActiveSport}>
          <TabsList data-testid="sport-stats-tabs">
            {sportsAvailable.map((s) => (
              <TabsTrigger
                key={s}
                value={s}
                data-testid={`sport-stats-tab-${s}`}
                className="text-xs"
              >
                <span aria-hidden className="mr-1">{SPORT_META[s]?.icon || '🎯'}</span>
                {sportLabel(s, lang)}
              </TabsTrigger>
            ))}
          </TabsList>

          {sportsAvailable.map((s) => (
            <TabsContent key={s} value={s} className="space-y-4 mt-4">
              <SportKpiBlock
                kpis={data.by_sport?.[s]}
                markets={data.by_sport_market?.[s] || {}}
                stake={stake}
                lang={lang}
                sport={s}
              />
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </TooltipProvider>
  );
}

function SportKpiBlock({ kpis, markets, lang, sport, stake }) {
  if (!kpis) return null;
  const roi = kpis.roi || {};
  const tiers = kpis.accuracy_by_tier || {};
  const marketEntries = Object.entries(markets || {});

  return (
    <div className="space-y-4" data-testid={`sport-stats-block-${sport}`}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Kpi label={lang === 'en' ? 'Total' : 'Total'}     value={kpis.total ?? 0} testId={`kpi-total-${sport}`} />
        <Kpi label={lang === 'en' ? 'Win rate' : 'Acierto'} value={`${kpis.win_rate ?? 0}%`} accent="emerald" testId={`kpi-winrate-${sport}`} />
        <Kpi label={lang === 'en' ? 'ROI'      : 'ROI'}     value={fmtPct(roi.roi_pct)} accent={toneForRoi(roi.roi_pct)} testId={`kpi-roi-${sport}`} />
        <Kpi label={lang === 'en' ? 'P&L'      : 'Ganancia'} value={fmtMoney(roi.total_profit)} accent={toneForRoi(roi.roi_pct)} testId={`kpi-profit-${sport}`} />
      </div>

      {/* Accuracy by tier */}
      <div className="rounded-lg border border-border bg-card/60 p-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2 flex items-center gap-1.5">
          <Target className="h-3.5 w-3.5" />
          {lang === 'en' ? 'Accuracy by confidence tier' : 'Acierto por nivel de confianza'}
        </div>
        <div className="grid grid-cols-3 gap-2">
          {['Maxima', 'Alta', 'Media'].map((t) => {
            const v = tiers[t] || {};
            const tone = toneForRoi(v.roi_pct);
            return (
              <Tooltip key={t}>
                <TooltipTrigger asChild>
                  <div className={`rounded border p-2 ${TONE_BG[tone] || TONE_BG.neutral}`} data-testid={`tier-${sport}-${t.toLowerCase()}`}>
                    <div className="text-[10px] uppercase opacity-70 leading-none">{t}</div>
                    <div className="font-mono-tabular text-base font-semibold mt-0.5 tabular-nums">{v.rate ?? 0}%</div>
                    <div className="text-[10px] opacity-70 tabular-nums">
                      {v.won ?? 0}-{v.lost ?? 0}{' · '}{fmtPct(v.roi_pct)}
                    </div>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-xs">
                  <div className="space-y-0.5">
                    <div>{lang === 'en' ? 'Settled' : 'Liquidados'}: <span className="mono">{v.settled ?? 0}</span></div>
                    <div>{lang === 'en' ? 'Profit' : 'Ganancia'}: <span className="mono">{fmtMoney(v.profit)}</span></div>
                    <div>{lang === 'en' ? 'Stake' : 'Apuesta'}: <span className="mono">${stake}/pick</span></div>
                  </div>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      </div>

      {/* Top markets within this sport */}
      {marketEntries.length > 0 && (
        <div className="rounded-lg border border-border bg-card/60 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            {lang === 'en' ? 'Top markets' : 'Mercados principales'}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {marketEntries.map(([mk, v]) => (
              <div key={mk} className="rounded border border-border/60 bg-background/30 px-2.5 py-2 flex items-center justify-between gap-3" data-testid={`market-${sport}-${mk.toLowerCase().replace(/\s+/g, '-')}`}>
                <div className="min-w-0">
                  <div className="text-xs truncate font-medium">{mk}</div>
                  <div className="text-[10px] text-muted-foreground tabular-nums">
                    {v.won ?? 0}W · {v.lost ?? 0}L · {v.total ?? 0} {lang === 'en' ? 'total' : 'totales'}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="font-mono-tabular text-sm font-semibold tabular-nums">{v.win_rate ?? 0}%</div>
                  <RoiBadge roi={v.roi?.roi_pct} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Kpi({ label, value, accent, testId }) {
  const cls = accent === 'good' || accent === 'emerald'
    ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300'
    : accent === 'bad'
      ? 'border-red-500/30 bg-red-500/5 text-red-300'
      : accent === 'flat'
        ? 'border-amber-500/30 bg-amber-500/5 text-amber-300'
        : 'border-border bg-secondary/30';
  return (
    <div className={`rounded-lg border p-3 ${cls}`} data-testid={testId}>
      <div className="text-[11px] uppercase opacity-80">{label}</div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5 tabular-nums">{value}</div>
    </div>
  );
}
