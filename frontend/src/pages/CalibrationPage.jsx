/**
 * CalibrationPage — /dashboard/calibration
 *
 * Dedicated route showing Engine Accuracy vs User Accuracy side by side,
 * plus a divergence table where the user diverged from the engine's
 * recommendation.
 *
 * Backed by:
 *   GET /api/calibration/summary?days=N&sport=...
 *   GET /api/calibration/divergences?days=N&sport=...
 *
 * Design rules:
 *   • Engine column = neutral / emerald (it's the engine's perspective).
 *   • User column   = amber/cyan to differentiate.
 *   • Critical rows (engine_won_user_lost / engine_lost_user_won) get
 *     destructive tone so they pop.
 *   • Spanish UI by default, en/es toggle respected from i18n.
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import { useSport } from '@/lib/sport';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  TrendingUp, TrendingDown, Target, AlertTriangle, ShieldCheck,
  ArrowDownRight, ArrowUpRight, RefreshCw,
} from 'lucide-react';

const DELTA_LABELS = {
  NONE:                  { es: 'Sin variación',          en: 'No variation' },
  USER_PROTECTED_LINE:   { es: 'Línea más protegida',    en: 'More protected line' },
  USER_AGGRESSIVE_LINE:  { es: 'Línea más agresiva',     en: 'More aggressive line' },
  DIFFERENT_MARKET:      { es: 'Mercado distinto',       en: 'Different market' },
  OPPOSITE_SIDE:         { es: 'Lado contrario',         en: 'Opposite side' },
};

const RESULT_TONE = {
  WIN:  'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  LOSS: 'bg-rose-500/10    text-rose-300    border-rose-500/30',
  PUSH: 'bg-amber-500/10   text-amber-300   border-amber-500/30',
  PENDING: 'bg-slate-500/10 text-slate-300 border-slate-500/30',
  VOID: 'bg-slate-500/10   text-slate-300   border-slate-500/30',
};

function pctOrDash(v) {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

export default function CalibrationPage() {
  const { t, lang } = useI18n();
  const { sport } = useSport();
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState(null);
  const [divergences, setDivergences] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, d] = await Promise.all([
        api.get(`/calibration/summary?days=${days}${sport ? `&sport=${sport}` : ''}`),
        api.get(`/calibration/divergences?days=${days}&limit=50${sport ? `&sport=${sport}` : ''}`),
      ]);
      setSummary(s.data);
      setDivergences((d.data && d.data.items) || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-line */ }, [days, sport]);

  return (
    <div
      className="container max-w-6xl px-4 py-6 space-y-6"
      data-testid="calibration-page"
    >
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2">
            <Target className="h-5 w-5 text-emerald-400" />
            {lang === 'en' ? 'Calibration · Engine vs You' : 'Calibración · Engine vs Tú'}
          </h1>
          <p className="text-xs text-muted-foreground pt-1">
            {lang === 'en'
              ? 'Compare the engine\'s pure accuracy against the bets you actually placed.'
              : 'Compara la precisión pura del engine contra las apuestas que realmente realizaste.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger
              className="w-[140px]"
              data-testid="calibration-days-select"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">  {lang === 'en' ? '7 days' : '7 días'}</SelectItem>
              <SelectItem value="14"> {lang === 'en' ? '14 days' : '14 días'}</SelectItem>
              <SelectItem value="30"> {lang === 'en' ? '30 days' : '30 días'}</SelectItem>
              <SelectItem value="90"> {lang === 'en' ? '90 days' : '90 días'}</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={load}
            disabled={loading}
            data-testid="calibration-refresh"
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            {lang === 'en' ? 'Refresh' : 'Recargar'}
          </Button>
        </div>
      </header>

      {error && (
        <Card className="border-rose-500/40 bg-rose-500/5">
          <CardContent className="py-3 text-xs text-rose-200">{error}</CardContent>
        </Card>
      )}

      {/* Top KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3" data-testid="calibration-kpis">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] uppercase tracking-wide opacity-70">
              {lang === 'en' ? 'Picks tracked' : 'Picks registrados'}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className="text-2xl font-semibold"
              data-testid="kpi-total-picks"
            >
              {summary?.total_picks ?? '—'}
            </div>
            <p className="text-[10px] opacity-60 mt-1">
              {lang === 'en' ? `last ${days} days` : `últimos ${days} días`}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] uppercase tracking-wide opacity-70">
              {lang === 'en' ? 'Followed engine' : 'Siguieron al engine'}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className="text-2xl font-semibold"
              data-testid="kpi-followed-rate"
            >
              {pctOrDash(summary?.followed_engine_rate)}
            </div>
            <p className="text-[10px] opacity-60 mt-1">
              {lang === 'en'
                ? 'rate of exact-match wagers'
                : 'tasa de apuestas idénticas a la recomendación'}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-[11px] uppercase tracking-wide opacity-70">
              {lang === 'en' ? 'Avg. line protection' : 'Protección media'}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className="text-2xl font-semibold"
              data-testid="kpi-avg-protection"
            >
              {summary?.avg_line_protection != null ? `+${summary.avg_line_protection}` : '—'}
            </div>
            <p className="text-[10px] opacity-60 mt-1">
              {lang === 'en'
                ? 'mean |Δ line| on diverged bets'
                : 'media de |Δ línea| en apuestas divergentes'}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Engine vs User accuracy side-by-side */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <AccuracyCard
          title={lang === 'en' ? 'Engine Accuracy' : 'Precisión del Engine'}
          subtitle={lang === 'en'
            ? 'What the engine would have hit if followed exactly'
            : 'Qué habría acertado el engine si se hubiera seguido al pie de la letra'}
          icon={<ShieldCheck className="h-4 w-4 text-emerald-400" />}
          tone="emerald"
          block={summary?.engine}
          testId="accuracy-engine"
          lang={lang}
        />
        <AccuracyCard
          title={lang === 'en' ? 'Your Accuracy' : 'Tu Precisión'}
          subtitle={lang === 'en'
            ? 'How your actual bets performed'
            : 'Cómo se desempeñaron tus apuestas reales'}
          icon={<Target className="h-4 w-4 text-cyan-400" />}
          tone="cyan"
          block={summary?.user}
          testId="accuracy-user"
          lang={lang}
        />
      </div>

      {/* Divergence drilldown */}
      <Card data-testid="divergence-card">
        <CardHeader className="pb-2 flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-400" />
            {lang === 'en' ? 'Engine vs You — outcome divergences' : 'Engine vs Tú — divergencias de resultado'}
          </CardTitle>
          <div className="flex gap-2">
            <Badge
              variant="outline"
              className="text-[10px] bg-emerald-500/10 border-emerald-500/40 text-emerald-200"
              data-testid="badge-engine-lost-user-won"
            >
              <ArrowUpRight className="h-3 w-3 mr-1" />
              {lang === 'en' ? 'Engine LOST · You WON' : 'Engine PERDIÓ · Tú GANASTE'}
              : {summary?.engine_lost_user_won ?? 0}
            </Badge>
            <Badge
              variant="outline"
              className="text-[10px] bg-rose-500/10 border-rose-500/40 text-rose-200"
              data-testid="badge-engine-won-user-lost"
            >
              <ArrowDownRight className="h-3 w-3 mr-1" />
              {lang === 'en' ? 'Engine WON · You LOST' : 'Engine GANÓ · Tú PERDISTE'}
              : {summary?.engine_won_user_lost ?? 0}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <DeltaBreakdown breakdown={summary?.delta_breakdown} lang={lang} />

          {(divergences.length === 0) ? (
            <p
              className="text-xs opacity-60 py-4 text-center"
              data-testid="divergence-empty"
            >
              {lang === 'en'
                ? 'No divergences recorded in this window.'
                : 'Sin divergencias registradas en este rango.'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table
                className="w-full text-xs"
                data-testid="divergence-table"
              >
                <thead>
                  <tr className="opacity-60 border-b border-border">
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Match' : 'Partido'}</th>
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Engine' : 'Engine'}</th>
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Your bet' : 'Tu apuesta'}</th>
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Engine R.' : 'R. Engine'}</th>
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Your R.' : 'R. Tuya'}</th>
                    <th className="text-left py-2 pr-2">{lang === 'en' ? 'Δ' : 'Δ'}</th>
                  </tr>
                </thead>
                <tbody>
                  {divergences.map((row) => (
                    <DivergenceRow key={row.pick_uid} row={row} lang={lang} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function AccuracyCard({ title, subtitle, icon, tone, block, testId, lang }) {
  const wr = block?.win_rate;
  return (
    <Card data-testid={testId}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          {icon} {title}
        </CardTitle>
        <p className="text-[10.5px] opacity-60">{subtitle}</p>
      </CardHeader>
      <CardContent>
        <div className="flex items-baseline gap-3">
          <div
            className={`text-3xl font-semibold ${tone === 'emerald' ? 'text-emerald-300' : 'text-cyan-300'}`}
            data-testid={`${testId}-winrate`}
          >
            {pctOrDash(wr)}
          </div>
          <div className="text-[11px] opacity-70">
            <span data-testid={`${testId}-wins`}>{block?.wins ?? 0} {lang === 'en' ? 'wins' : 'ganadas'}</span>
            <span className="mx-1.5">·</span>
            <span data-testid={`${testId}-losses`}>{block?.losses ?? 0} {lang === 'en' ? 'losses' : 'perdidas'}</span>
            <span className="mx-1.5">·</span>
            <span data-testid={`${testId}-pushes`}>{block?.pushes ?? 0} push</span>
          </div>
        </div>
        <div className="mt-2 h-1.5 rounded bg-border/40 overflow-hidden">
          <div
            className={`h-full ${tone === 'emerald' ? 'bg-emerald-500/70' : 'bg-cyan-500/70'}`}
            style={{ width: wr != null ? `${Math.max(2, Math.min(100, wr * 100))}%` : '0%' }}
          />
        </div>
        <p className="text-[10px] opacity-50 mt-2">
          {lang === 'en'
            ? `Sample: ${block?.sample ?? 0} settled picks`
            : `Muestra: ${block?.sample ?? 0} picks liquidados`}
        </p>
      </CardContent>
    </Card>
  );
}

function DeltaBreakdown({ breakdown, lang }) {
  if (!breakdown || Object.keys(breakdown).length === 0) return null;
  return (
    <div
      className="grid grid-cols-2 md:grid-cols-5 gap-1.5 pb-3"
      data-testid="delta-breakdown"
    >
      {Object.entries(breakdown).map(([k, v]) => {
        const lbl = (DELTA_LABELS[k] || {})[lang] || (DELTA_LABELS[k] || {}).es || k;
        return (
          <div
            key={k}
            className="rounded border border-border bg-card/40 px-2 py-1.5"
            data-testid={`delta-bucket-${k}`}
          >
            <p className="text-[9.5px] opacity-70 uppercase tracking-wide truncate">{lbl}</p>
            <p className="text-base font-semibold">{v}</p>
          </div>
        );
      })}
    </div>
  );
}

function DivergenceRow({ row, lang }) {
  const eng = row.engine_recommendation || {};
  const usr = row.actual_bet || {};
  const er  = row.engine_result || 'PENDING';
  const ur  = row.user_result   || 'PENDING';
  const deltaLbl = (DELTA_LABELS[row.delta] || {})[lang] || row.delta || '—';
  const isCriticalUserWin  = er === 'LOSS' && ur === 'WIN';
  const isCriticalUserLoss = er === 'WIN'  && ur === 'LOSS';

  return (
    <tr
      className={`border-b border-border/40 ${
        isCriticalUserWin ? 'bg-emerald-500/[0.04]' :
        isCriticalUserLoss ? 'bg-rose-500/[0.04]'   : ''
      }`}
      data-testid={`divergence-row-${row.pick_uid}`}
    >
      <td className="py-2 pr-2 truncate max-w-[180px]">
        {row.match_label || row.match_id}
      </td>
      <td className="py-2 pr-2 font-mono opacity-90">
        {(eng.selection || '').toUpperCase()}
        {eng.line != null ? ` ${eng.line}` : ''}
      </td>
      <td className="py-2 pr-2 font-mono opacity-90">
        {(usr.selection || '').toUpperCase()}
        {usr.line != null ? ` ${usr.line}` : ''}
      </td>
      <td className="py-2 pr-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] border ${RESULT_TONE[er] || RESULT_TONE.PENDING}`}>
          {er}
        </span>
      </td>
      <td className="py-2 pr-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] border ${RESULT_TONE[ur] || RESULT_TONE.PENDING}`}>
          {ur}
        </span>
      </td>
      <td className="py-2 pr-2 opacity-80">
        <span className="text-[10px]">{deltaLbl}</span>
        {row.line_difference != null && (
          <span className="ml-1 font-mono opacity-70">
            ({row.line_direction === 'MORE_PROTECTED' ? '+' : '−'}{row.line_difference})
          </span>
        )}
      </td>
    </tr>
  );
}
