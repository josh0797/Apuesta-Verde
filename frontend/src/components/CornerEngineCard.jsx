/**
 * Sprint Corner Fase B — Corner Engine Card
 *
 * Renderiza:
 *   1. Most Corners (clasificación binaria con probabilidades + recomendación).
 *   2. Asian Corners (14 mercados: HOME/AWAY x 7 líneas).
 *
 * Datasource: POST /api/football/corner-engine/predict
 * Feature flags: lee debug.feature_flags del response para mostrar
 * cuáles modelos están activos.
 *
 * Estados:
 *   • LOADING — skeletons.
 *   • DISABLED — banner discreto (feature flags off).
 *   • OK — render completo.
 *   • ERROR — banner con razón legible.
 *   • REAL_ODDS_NOT_AVAILABLE — warning específico arriba de Asian Corners.
 */
import { useCallback, useEffect, useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, AlertTriangle, AlertCircle,
  Goal, Target, Info, RefreshCcw, ShieldAlert, History,
} from 'lucide-react';

import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

// ─────────────────────────────────────────────────────────────────────
//   Helpers
// ─────────────────────────────────────────────────────────────────────

const fmtPct = (p) => (p == null ? '—' : `${(p * 100).toFixed(1)}%`);
const fmtNum = (n, d = 2) => (n == null ? '—' : Number(n).toFixed(d));

const SIDE_STYLE = {
  HOME:   'border-emerald-400/40 bg-emerald-500/15 text-emerald-100',
  AWAY:   'border-sky-400/40 bg-sky-500/15 text-sky-100',
  NO_BET: 'border-zinc-400/40 bg-zinc-500/10 text-zinc-300',
};

const REC_STYLE = {
  BET:    'border-emerald-400/60 bg-emerald-500/25 text-emerald-50',
  WATCH:  'border-amber-400/60 bg-amber-500/15 text-amber-100',
  NO_BET: 'border-zinc-500/40 bg-zinc-700/30 text-zinc-300',
};

// ─────────────────────────────────────────────────────────────────────
//   Sub-components
// ─────────────────────────────────────────────────────────────────────

function ProbBar({ label, value, side }) {
  const pct = Math.max(0, Math.min(100, (value ?? 0) * 100));
  return (
    <div className="space-y-1" data-testid={`prob-bar-${side?.toLowerCase()}`}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-zinc-200">{label}</span>
        <span className="font-mono text-zinc-100">{fmtPct(value)}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-zinc-800 overflow-hidden">
        <div
          className={
            side === 'HOME' ? 'h-full bg-emerald-400 transition-[width] duration-300'
            : side === 'AWAY' ? 'h-full bg-sky-400 transition-[width] duration-300'
            : 'h-full bg-zinc-400 transition-[width] duration-300'
          }
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function MostCornersSection({ data, expectedDiff }) {
  if (!data) return null;
  const sideClass = SIDE_STYLE[data.recommended_side] || SIDE_STYLE.NO_BET;
  const TrendIcon = data.recommended_side === 'HOME' ? TrendingUp
                  : data.recommended_side === 'AWAY' ? TrendingDown
                  : Info;

  return (
    <div className="space-y-4" data-testid="most-corners-section">
      {/* Headline */}
      <div className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${sideClass}`}>
        <TrendIcon className="h-5 w-5 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-xs uppercase tracking-wider opacity-80">
            Recomendación Most Corners
          </div>
          <div className="text-lg font-semibold" data-testid="most-corners-recommendation">
            {data.recommended_side === 'NO_BET'
              ? 'Sin pick (datos insuficientes o sin ventaja clara)'
              : `Apostar por ${data.recommended_side}`}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs opacity-80">Confidence</div>
          <div className="font-mono text-base" data-testid="most-corners-confidence">
            {fmtNum(data.confidence, 1)}
          </div>
        </div>
      </div>

      {/* Probabilities */}
      <div className="space-y-2.5">
        <ProbBar label="Home gana Most Corners"  value={data.home_most_corners_prob} side="HOME" />
        <ProbBar label="Away gana Most Corners"  value={data.away_most_corners_prob} side="AWAY" />
        <ProbBar label="Empate en córners"        value={data.tie_corners_prob}       side="TIE" />
      </div>

      {/* Stats compactas */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-md border border-zinc-700/50 bg-zinc-900/30 px-3 py-2">
          <div className="text-zinc-400">Expected Diff</div>
          <div className="font-mono text-zinc-100 text-base"
                data-testid="expected-corner-diff">
            {expectedDiff != null ? (expectedDiff >= 0 ? '+' : '') + fmtNum(expectedDiff, 2) : '—'}
          </div>
        </div>
        <div className="rounded-md border border-zinc-700/50 bg-zinc-900/30 px-3 py-2">
          <div className="text-zinc-400">Edge Score</div>
          <div className="font-mono text-zinc-100 text-base">
            {fmtNum(data.edge_score, 1)}
          </div>
        </div>
        <div className="rounded-md border border-zinc-700/50 bg-zinc-900/30 px-3 py-2">
          <div className="text-zinc-400">Tie Prob</div>
          <div className="font-mono text-zinc-100 text-base">
            {fmtPct(data.tie_corners_prob)}
          </div>
        </div>
      </div>

      {/* Reason codes */}
      {Array.isArray(data.reason_codes) && data.reason_codes.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs uppercase tracking-wider text-zinc-400">
            Razones detectadas
          </div>
          <div className="flex flex-wrap gap-1.5">
            {data.reason_codes.map((rc) => (
              <Badge key={rc} variant="outline" className="text-[10px] font-mono">
                {rc}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AsianCornerRow({ market }) {
  const recCls = REC_STYLE[market.recommendation] || REC_STYLE.NO_BET;
  const hasRealOdds = market.book_odds != null;
  return (
    <tr className="border-b border-zinc-800/50 last:border-0" data-testid={`asian-row-${market.market}`}>
      <td className="py-2 pr-2 font-mono text-xs text-zinc-100">{market.market}</td>
      <td className="py-2 pr-2 text-right font-mono text-zinc-200">{fmtPct(market.prob_win)}</td>
      <td className="py-2 pr-2 text-right font-mono text-zinc-400">
        {market.prob_push > 0 ? fmtPct(market.prob_push) : '—'}
      </td>
      <td className="py-2 pr-2 text-right font-mono text-zinc-100">
        {fmtNum(market.fair_odds, 2)}
      </td>
      <td className="py-2 pr-2 text-right font-mono text-zinc-300">
        {hasRealOdds ? fmtNum(market.book_odds, 2) : <span className="text-zinc-600">—</span>}
      </td>
      <td className="py-2 pr-2 text-right font-mono text-zinc-100">
        {market.ev != null ? (market.ev >= 0 ? '+' : '') + (market.ev * 100).toFixed(2) + '%' : '—'}
      </td>
      <td className="py-2 text-right">
        <Badge variant="outline" className={`text-[10px] font-semibold ${recCls}`}>
          {market.recommendation}
        </Badge>
      </td>
    </tr>
  );
}

function AsianCornersSection({ markets, hasRealOdds }) {
  if (!Array.isArray(markets) || markets.length === 0) return null;

  return (
    <div className="space-y-3" data-testid="asian-corners-section">
      {!hasRealOdds && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40
                          bg-amber-500/10 px-3 py-2 text-xs text-amber-100"
              data-testid="asian-corners-no-odds-warning">
          <ShieldAlert className="h-4 w-4 shrink-0 mt-0.5" />
          <div>
            <strong>Sin cuotas reales disponibles.</strong> Las fair odds calculadas
            son informativas. Para evaluar EV real, conecta cuotas del bookmaker.
          </div>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-zinc-700 text-zinc-400">
              <th className="py-2 pr-2 text-left font-medium">Mercado</th>
              <th className="py-2 pr-2 text-right font-medium">Win</th>
              <th className="py-2 pr-2 text-right font-medium">Push</th>
              <th className="py-2 pr-2 text-right font-medium">Fair Odds</th>
              <th className="py-2 pr-2 text-right font-medium">Book</th>
              <th className="py-2 pr-2 text-right font-medium">EV</th>
              <th className="py-2 text-right font-medium">Decisión</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <AsianCornerRow key={m.market} market={m} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
//   Sprint-D9 Iteration-3 — Corner History L5/L15 + sparkline
// ─────────────────────────────────────────────────────────────────────

/**
 * Mini SVG sparkline. Renders a polyline + dot for the last value.
 * - values: array of numbers (typically 5 ó 15 corners por partido)
 * - color: tailwind color slug for the stroke ('emerald' | 'sky' | …)
 */
function Sparkline({ values = [], color = 'emerald', width = 80, height = 22 }) {
  if (!Array.isArray(values) || values.length < 2) {
    return (
      <div
        className="text-[10px] text-zinc-500"
        style={{ width, height }}
        data-testid="sparkline-empty"
      >
        n/d
      </div>
    );
  }
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const range = max - min || 1;
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * stepX;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const last = values[values.length - 1];
  const lastX = (values.length - 1) * stepX;
  const lastY = height - ((last - min) / range) * (height - 4) - 2;
  const stroke =
    color === 'sky' ? '#38bdf8'
    : color === 'rose' ? '#fb7185'
    : color === 'amber' ? '#fbbf24'
    : '#34d399';

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`sparkline ${color}`}
      data-testid={`sparkline-${color}`}
    >
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
      <circle cx={lastX} cy={lastY} r="2" fill={stroke} />
    </svg>
  );
}

/**
 * Renders a single team's L5/L15 averages + sparkline.
 */
function TeamCornerWindow({ label, side, windows, recent }) {
  const l5  = windows?.L5  || {};
  const l15 = windows?.L15 || {};
  const sparkValues = (recent || [])
    .map((m) => m.corners_for)
    .filter((v) => v != null)
    .slice(-15);
  const color = side === 'home' ? 'emerald' : 'sky';
  const labelClr =
    side === 'home' ? 'text-emerald-200' : 'text-sky-200';
  return (
    <div
      className="rounded-lg border border-zinc-700/50 bg-zinc-900/40 p-3 space-y-2"
      data-testid={`corner-history-${side}`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-xs font-semibold truncate ${labelClr}`}>{label}</span>
        <Sparkline values={sparkValues} color={color} />
      </div>
      <div className="grid grid-cols-2 gap-1.5 text-[11px]">
        <div className="rounded-md bg-zinc-800/40 px-2 py-1.5">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">L5 a favor</div>
          <div className="font-mono text-zinc-100">
            {l5.corners_for_avg ?? '—'}
            <span className="text-zinc-500 ml-1">({l5.sample_size ?? 0})</span>
          </div>
        </div>
        <div className="rounded-md bg-zinc-800/40 px-2 py-1.5">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">L5 en contra</div>
          <div className="font-mono text-zinc-100">
            {l5.corners_against_avg ?? '—'}
          </div>
        </div>
        <div className="rounded-md bg-zinc-800/40 px-2 py-1.5">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">L15 a favor</div>
          <div className="font-mono text-zinc-100">
            {l15.corners_for_avg ?? '—'}
            <span className="text-zinc-500 ml-1">({l15.sample_size ?? 0})</span>
          </div>
        </div>
        <div className="rounded-md bg-zinc-800/40 px-2 py-1.5">
          <div className="text-zinc-500 uppercase text-[9px] tracking-wide">L15 en contra</div>
          <div className="font-mono text-zinc-100">
            {l15.corners_against_avg ?? '—'}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * CornerHistoryPanel: dual-fetch (home & away) → renders both windows.
 *
 * Lazy-fetches when the tab is opened (data-tab gated). The seed is
 * preferred via the backend cascade, so this is rate-limit friendly.
 */
function CornerHistoryPanel({ context }) {
  const [state, setState] = useState({
    status: 'idle', home: null, away: null, error: null,
  });

  const homeName = context?.home_team || context?.home_team_name;
  const awayName = context?.away_team || context?.away_team_name;
  const league   = context?.league || context?.competition || null;

  const fetchBoth = useCallback(async () => {
    if (!homeName || !awayName) {
      setState({
        status: 'error', home: null, away: null,
        error: 'Falta home_team / away_team en el context.',
      });
      return;
    }
    setState((s) => ({ ...s, status: 'loading', error: null }));
    try {
      const params = (team) => ({
        team, league: league || undefined, windows: '5,15',
      });
      const [hRes, aRes] = await Promise.all([
        api.get('/api/football/corners/team-history', { params: params(homeName) }),
        api.get('/api/football/corners/team-history', { params: params(awayName) }),
      ]);
      setState({
        status: 'ok',
        home: hRes?.data || null,
        away: aRes?.data || null,
        error: null,
      });
    } catch (e) {
      setState({
        status: 'error', home: null, away: null,
        error: e?.message || 'Network error',
      });
    }
  }, [homeName, awayName, league]);

  useEffect(() => { fetchBoth(); }, [fetchBoth]);

  if (state.status === 'loading') {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3"
            data-testid="corner-history-loading">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (state.status === 'error') {
    return (
      <div className="text-xs text-amber-200/80 flex items-center gap-1.5"
            data-testid="corner-history-error">
        <AlertCircle className="h-3.5 w-3.5" />
        {state.error}
      </div>
    );
  }

  const homeAvailable = state.home?.available;
  const awayAvailable = state.away?.available;
  const anyUnavailable = !homeAvailable || !awayAvailable;

  return (
    <div className="space-y-3" data-testid="corner-history-panel">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <TeamCornerWindow
          label={homeName}
          side="home"
          windows={state.home?.windows}
          recent={state.home?.recent}
        />
        <TeamCornerWindow
          label={awayName}
          side="away"
          windows={state.away?.windows}
          recent={state.away?.recent}
        />
      </div>
      <div className="flex items-center justify-between text-[10px] text-zinc-500"
            data-testid="corner-history-meta">
        <div className="flex items-center gap-2">
          <span>Fuentes:</span>
          {state.home?.source && (
            <Badge variant="outline" className="text-[9px] px-1.5 py-0">
              {homeName}: {state.home.source}
            </Badge>
          )}
          {state.away?.source && (
            <Badge variant="outline" className="text-[9px] px-1.5 py-0">
              {awayName}: {state.away.source}
            </Badge>
          )}
        </div>
        {anyUnavailable && (
          <span className="text-amber-300 inline-flex items-center gap-1">
            <ShieldAlert className="h-3 w-3" />
            Datos parciales (muestra &lt; 5)
          </span>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
//   Main exported component
// ─────────────────────────────────────────────────────────────────────

export function CornerEngineCard({ context, autoLoad = true }) {
  const [state, setState]   = useState({ status: 'idle', data: null, error: null });
  const [model, setModel]   = useState('linear_sigmoid'); // 'linear_sigmoid' | 'skellam'

  const load = useCallback(async () => {
    if (!context) {
      setState({ status: 'error', data: null,
                  error: 'Falta el context del partido.' });
      return;
    }
    setState({ status: 'loading', data: null, error: null });
    try {
      const r = await api.post('/api/football/corner-engine/predict', {
        context: { ...context, use_skellam: model === 'skellam' },
      });
      if (!r?.data?.ok) {
        setState({ status: 'error', data: r.data,
                    error: r?.data?.reason || 'Sin respuesta válida.' });
        return;
      }
      setState({ status: 'ok', data: r.data, error: null });
    } catch (e) {
      setState({ status: 'error', data: null,
                  error: e?.message || 'Network error' });
    }
  }, [context, model]);

  useEffect(() => {
    if (autoLoad) load();
  }, [autoLoad, load]);

  // -------- Render --------
  if (state.status === 'loading') {
    return (
      <div className="rounded-xl border border-zinc-700/50 bg-zinc-900/30 p-4 space-y-3"
            data-testid="corner-engine-card-loading">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-4
                        text-sm text-red-100" data-testid="corner-engine-card-error">
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4" />
          <span className="font-semibold">Corner Engine no disponible</span>
        </div>
        <div className="mt-1.5 text-xs text-red-200/80">{state.error}</div>
        <Button variant="outline" size="sm" className="mt-3" onClick={load}
                  data-testid="corner-engine-retry-btn">
          <RefreshCcw className="h-3 w-3 mr-1.5" /> Reintentar
        </Button>
      </div>
    );
  }

  const data = state.data;
  if (!data?.enabled) {
    return (
      <div className="rounded-xl border border-zinc-700/50 bg-zinc-900/30 p-4
                        text-sm text-zinc-400" data-testid="corner-engine-card-disabled">
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4" />
          <span>Motor de córners desactivado (feature flags off).</span>
        </div>
      </div>
    );
  }

  const hasRealOdds = Array.isArray(data.asian_corners)
    && data.asian_corners.some((m) => m.book_odds != null);

  return (
    <div className="rounded-xl border border-zinc-700/50 bg-zinc-900/30 p-4 space-y-4"
          data-testid="corner-engine-card">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Goal className="h-5 w-5 text-emerald-400" />
          <h3 className="font-semibold text-zinc-100" data-testid="corner-engine-card-title">
            Corner Markets Engine
          </h3>
          <Badge variant="outline" className="text-[10px] font-mono">
            {data.model === 'skellam' ? 'SKELLAM' : 'LINEAL'}
          </Badge>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            size="sm" variant={model === 'linear_sigmoid' ? 'default' : 'outline'}
            className="h-7 text-[11px]"
            onClick={() => setModel('linear_sigmoid')}
            data-testid="corner-engine-toggle-linear"
          >
            Lineal
          </Button>
          <Button
            size="sm" variant={model === 'skellam' ? 'default' : 'outline'}
            className="h-7 text-[11px]"
            onClick={() => setModel('skellam')}
            data-testid="corner-engine-toggle-skellam"
          >
            Skellam
          </Button>
          <Button
            size="sm" variant="outline" className="h-7 w-7 p-0"
            onClick={load} data-testid="corner-engine-refresh-btn"
            title="Recargar"
          >
            <RefreshCcw className="h-3 w-3" />
          </Button>
        </div>
      </div>

      <Tabs defaultValue="most" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="most" data-testid="tab-most-corners">
            <Target className="h-3.5 w-3.5 mr-1.5" />
            Most Corners
          </TabsTrigger>
          <TabsTrigger value="asian" data-testid="tab-asian-corners">
            <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />
            Asian Corners
            {hasRealOdds ? null : (
              <span className="ml-1.5 text-[9px] text-amber-400">·sin cuotas</span>
            )}
          </TabsTrigger>
          <TabsTrigger value="history" data-testid="tab-corner-history">
            <History className="h-3.5 w-3.5 mr-1.5" />
            Historial L5/L15
          </TabsTrigger>
        </TabsList>
        <TabsContent value="most" className="pt-3">
          {data.most_corners
            ? <MostCornersSection data={data.most_corners}
                                    expectedDiff={data.expected_corner_diff} />
            : <div className="text-xs text-zinc-400 py-2">
                Most Corners desactivado.
              </div>}
        </TabsContent>
        <TabsContent value="asian" className="pt-3">
          {data.asian_corners?.length
            ? <AsianCornersSection markets={data.asian_corners}
                                     hasRealOdds={hasRealOdds} />
            : <div className="text-xs text-zinc-400 py-2">
                Asian Corners desactivado.
              </div>}
        </TabsContent>
        <TabsContent value="history" className="pt-3">
          <CornerHistoryPanel context={context} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default CornerEngineCard;
