import { useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts';
import { useI18n } from '@/lib/i18n';
import { BadgeCheck, ThumbsDown } from 'lucide-react';

/**
 * WinRateChart — cumulative winrate evolution, terminal-styled.
 *
 * Design alignment:
 *   - All colors come from theme tokens (CSS HSL variables) so it adapts to
 *     light/dark + brand re-themes without code changes.
 *   - Custom dot color encodes outcome (won = positive, lost = negative) so a
 *     glance reveals streaks.
 *   - Tooltip carries the full context for that data point:
 *       match · market · selection · confidence · outcome · odds.
 *
 * Accepts the /api/stats/timeline payload directly.
 */
export function WinRateChart({ data }) {
  const { t, lang } = useI18n();

  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((d, i) => ({
      idx: i + 1,
      rate: d.win_rate,
      label: d.match_label,
      league: d.league,
      market: d.market,
      selection: d.selection,
      outcome: d.outcome,
      confidence: d.confidence_score,
      odds: d.odds,
      tracked_at: d.tracked_at,
    }));
  }, [data]);

  if (chartData.length === 0) {
    return (
      <div
        className="text-sm text-muted-foreground italic px-4 py-8 text-center"
        data-testid="winrate-chart-empty"
      >
        {t.history.empty}
      </div>
    );
  }

  // Centralized theme references (resolved at runtime via CSS variables)
  const COLORS = {
    grid: 'hsl(var(--border))',
    axis: 'hsl(var(--muted-foreground))',
    line: 'hsl(var(--chart-1))',        // semantic positive (emerald)
    won: 'hsl(var(--chart-1))',
    lost: 'hsl(var(--chart-3))',        // semantic negative (rose)
    referenceLine: 'hsl(var(--border))',
  };

  return (
    <div className="h-56 w-full" data-testid="winrate-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid stroke={COLORS.grid} strokeDasharray="3 3" />
          <XAxis
            dataKey="idx" stroke={COLORS.axis} fontSize={11}
            tickLine={false} axisLine={false}
          />
          <YAxis
            stroke={COLORS.axis} fontSize={11}
            tickLine={false} axisLine={false} domain={[0, 100]} unit="%"
          />
          <Tooltip
            cursor={{ stroke: COLORS.grid, strokeWidth: 1, strokeDasharray: '3 3' }}
            content={<WinRateTooltip lang={lang} winRateLabel={t.history.winRate} />}
          />
          <ReferenceLine
            y={50}
            stroke={COLORS.referenceLine}
            strokeDasharray="4 4"
            label={{
              value: '50%',
              position: 'left',
              fill: COLORS.axis,
              fontSize: 10,
            }}
          />
          <Line
            type="monotone"
            dataKey="rate"
            stroke={COLORS.line}
            strokeWidth={2}
            dot={<WinRateDot />}
            activeDot={{ r: 5, fill: COLORS.line, stroke: 'hsl(var(--card))', strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Custom dot — color-coded by outcome (recharts passes payload from the data
 * row). Renders as a clean SVG circle so it stays sharp at any scale.
 */
function WinRateDot(props) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null) return null;
  const won = payload?.outcome === 'won';
  const fill = won ? 'hsl(var(--chart-1))' : 'hsl(var(--chart-3))';
  return (
    <circle
      cx={cx} cy={cy} r={3}
      fill={fill}
      stroke="hsl(var(--card))"
      strokeWidth={1}
      data-testid={`winrate-dot-${payload?.idx}`}
    />
  );
}

/**
 * Custom recharts tooltip — enriched, terminal-styled.
 * Uses popover token surface (theme-driven), monospace tabular figures, and
 * the same micro-label tone as the rest of the intelligence terminal.
 */
function WinRateTooltip({ active, payload, lang, winRateLabel }) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  const isWon = d.outcome === 'won';
  const OutcomeIcon = isWon ? BadgeCheck : ThumbsDown;
  const outcomeText = isWon
    ? (lang === 'en' ? 'Won' : 'Ganada')
    : (lang === 'en' ? 'Lost' : 'Perdida');
  const outcomeTone = isWon ? 'text-emerald-300' : 'text-rose-300';

  const labels = lang === 'en'
    ? {
        match: 'Match', market: 'Market', selection: 'Pick', confidence: 'Confidence',
        outcome: 'Outcome', odds: 'Closing odds', settled: 'Settled #',
      }
    : {
        match: 'Partido', market: 'Mercado', selection: 'Selección', confidence: 'Confianza',
        outcome: 'Resultado', odds: 'Odds al cierre', settled: 'Resuelta #',
      };

  return (
    <div
      data-testid="winrate-tooltip"
      className="rounded-md border border-border bg-popover/95 backdrop-blur shadow-lg p-3 min-w-[240px] max-w-[300px]"
    >
      <div className="flex items-center justify-between gap-2 pb-1.5 mb-2 border-b border-border/40">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-foreground font-mono-tabular">
          {labels.settled}{d.idx}
        </span>
        <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${outcomeTone}`}>
          <OutcomeIcon className="h-3 w-3" />
          {outcomeText}
        </span>
      </div>

      {/* Headline: winrate at this point */}
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <span className="text-[10.5px] uppercase tracking-wider text-muted-foreground">
          {winRateLabel}
        </span>
        <span className="font-mono-tabular text-[16px] font-semibold text-foreground">
          {d.rate}%
        </span>
      </div>

      <dl className="space-y-1">
        {d.label && (
          <TooltipRow label={labels.match} value={d.label} mono={false} truncate />
        )}
        {d.market && (
          <TooltipRow label={labels.market} value={d.selection ? `${d.market} · ${d.selection}` : d.market} mono={false} truncate />
        )}
        {d.confidence != null && (
          <TooltipRow
            label={labels.confidence}
            value={`${d.confidence}/100`}
            valueClassName={
              d.confidence >= 70 ? 'text-emerald-300'
                : d.confidence >= 60 ? 'text-amber-300'
                  : 'text-muted-foreground'
            }
          />
        )}
        {d.odds != null && Number(d.odds) > 1.0 && (
          <TooltipRow label={labels.odds} value={Number(d.odds).toFixed(2)} />
        )}
      </dl>
    </div>
  );
}

function TooltipRow({ label, value, mono = true, truncate = false, valueClassName }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[11.5px]">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd
        className={`${mono ? 'font-mono-tabular' : ''} ${truncate ? 'truncate max-w-[60%] text-right' : ''} ${valueClassName || 'text-foreground'}`}
        title={truncate ? String(value) : undefined}
      >
        {value}
      </dd>
    </div>
  );
}
