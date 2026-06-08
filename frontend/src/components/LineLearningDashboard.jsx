/**
 * LineLearningDashboard — Feature 8 (Phase 43 + extensions).
 *
 * Consumes GET /api/learning/line/analytics and renders:
 *   1. Push rate / near-miss rate por mercado y línea (KPI grid).
 *   2. Ajustes recomendados por el engine (insights list).
 *   3. Tabla de últimos samples.
 *   4. Histograma de distancias de línea (signed buckets).
 *
 * Filtros: deporte, mercado, rango de fechas (start_date / end_date).
 *
 * Props:
 *   - embedded: boolean — when true, renders without page padding so the
 *     component can be dropped inside DashboardPage as a collapsible panel.
 */
import { useEffect, useState, useMemo, useCallback } from 'react';
import { Sparkles, ChevronDown, ChevronUp } from 'lucide-react';
import { api } from '@/lib/api';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';

const SPORT_OPTIONS = [
  { value: 'all', label: 'Todos los deportes' },
  { value: 'football', label: 'Fútbol' },
  { value: 'basketball', label: 'Basketball' },
  { value: 'baseball', label: 'Baseball / MLB' },
];

const MARKET_OPTIONS = [
  { value: 'all', label: 'Todos los mercados' },
  { value: 'total_goals', label: 'Total Goals' },
  { value: 'total_points', label: 'Total Points' },
  { value: 'total_runs', label: 'Total Runs' },
  { value: 'spread', label: 'Spread' },
  { value: 'other', label: 'Otros' },
];

function pct(v) {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso.slice(0, 10);
    return d.toISOString().slice(0, 10);
  } catch {
    return iso.slice(0, 10);
  }
}

const CLASSIFICATION_TONES = {
  EXACT_HIT:            'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  SAFE_LINE_HIT:        'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  NEAR_MISS:            'bg-amber-500/10 text-amber-300 border-amber-500/30',
  PUSH_SAVED:           'bg-sky-500/10 text-sky-300 border-sky-500/30',
  AGGRESSIVE_LINE_MISS: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
  PROFILE_WRONG:        'bg-zinc-500/15 text-zinc-300 border-zinc-500/40',
  UNDEFINED:            'bg-zinc-500/10 text-zinc-400 border-zinc-500/30',
};

export default function LineLearningDashboard({ embedded = false } = {}) {
  const [sport, setSport] = useState('all');
  const [marketType, setMarketType] = useState('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  const queryParams = useMemo(() => ({
    recent_limit: 10,
    ...(sport && sport !== 'all' ? { sport } : {}),
    ...(marketType && marketType !== 'all' ? { market_type: marketType } : {}),
    ...(startDate ? { start_date: startDate } : {}),
    ...(endDate ? { end_date: endDate } : {}),
  }), [sport, marketType, startDate, endDate]);

  const [state, setState] = useState({ data: null, loading: true, error: null });

  const fetchAnalytics = useCallback((signal) => {
    return api.get('/learning/line/analytics', { params: queryParams, signal })
      .then((r) => {
        if (signal?.aborted) return;
        setState({ data: r.data, loading: false, error: null });
      })
      .catch((e) => {
        if (signal?.aborted) return;
        setState({
          data: null,
          loading: false,
          error: e?.response?.data?.detail || 'No se pudo cargar el dashboard.',
        });
      });
  }, [queryParams]);

  useEffect(() => {
    const ctl = new AbortController();
    fetchAnalytics(ctl.signal);
    return () => ctl.abort();
  }, [fetchAnalytics]);

  const { data, loading, error } = state;

  const m = data?.metrics || {};
  const perLine = data?.per_line_success || {};
  const perLineN = data?.per_line_sample_size || {};
  const histogram = data?.line_distance_histogram || {};
  const recent = data?.recent_samples || [];
  const insights = data?.insights || [];
  const sampleSize = m?.sample_size || 0;

  const histogramMax = useMemo(() => {
    const vals = Object.values(histogram);
    return vals.length ? Math.max(...vals) : 0;
  }, [histogram]);

  const onResetFilters = () => {
    setSport('all'); setMarketType('all'); setStartDate(''); setEndDate('');
  };

  const wrapperCls = embedded
    ? 'space-y-4'
    : 'space-y-4 p-4 sm:p-6 max-w-5xl mx-auto';

  return (
    <div className={wrapperCls} data-testid="line-learning-dashboard">
      {!embedded && (
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">Aprendizaje de líneas</h1>
          <p className="text-xs opacity-70">
            Estadísticas sobre tus picks reales. Modo observe-only hasta que las cohortes
            superen el umbral mínimo de muestras.
          </p>
        </div>
      )}

      <div className="flex flex-wrap gap-2 items-end" data-testid="lld-filters">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider opacity-60">Deporte</label>
          <Select value={sport} onValueChange={setSport}>
            <SelectTrigger className="w-40 h-8 text-xs" data-testid="lld-sport-filter">
              <SelectValue placeholder="Deporte" />
            </SelectTrigger>
            <SelectContent>
              {SPORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider opacity-60">Mercado</label>
          <Select value={marketType} onValueChange={setMarketType}>
            <SelectTrigger className="w-40 h-8 text-xs" data-testid="lld-market-filter">
              <SelectValue placeholder="Mercado" />
            </SelectTrigger>
            <SelectContent>
              {MARKET_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider opacity-60">Desde</label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-36 h-8 text-xs"
            data-testid="lld-start-date"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider opacity-60">Hasta</label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-36 h-8 text-xs"
            data-testid="lld-end-date"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs"
          onClick={onResetFilters}
          data-testid="lld-reset-filters"
        >
          Limpiar
        </Button>
        <span className="text-[10px] opacity-60 ml-auto self-center" data-testid="lld-sample-count">
          {sampleSize} muestras
        </span>
      </div>

      {error && (
        <Card>
          <CardContent className="py-4 text-xs text-rose-300" data-testid="lld-error">
            {error}
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
        </div>
      ) : !data || sampleSize === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm opacity-70" data-testid="lld-empty">
            Aún no hay muestras para este filtro. Marca el resultado de algunos picks
            en el modal &quot;Marcar resultado (avanzado)&quot; y vuelve aquí.
          </CardContent>
        </Card>
      ) : (
        <>
          {/* 1. Push rate / near-miss / overall metrics */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="lld-metrics">
            <Metric label="Push rate" value={pct(m.push_rate)} testId="lld-push-rate" />
            <Metric label="Near-miss rate" value={pct(m.near_miss_rate)} testId="lld-near-miss" />
            <Metric label="Half-run loss" value={pct(m.half_run_loss_rate)} testId="lld-half-run-loss" />
            <Metric label="Línea agresiva miss" value={pct(m.aggressive_line_miss_rate)} testId="lld-aggressive-miss" />
            <Metric label="Línea segura hit" value={pct(m.safe_line_hit_rate)} testId="lld-safe-hit" />
            <Metric label="Exact hit" value={pct(m.exact_hit_rate)} testId="lld-exact-hit" />
            <Metric label="Profile wrong" value={pct(m.profile_wrong_rate)} testId="lld-profile-wrong" />
            <Metric
              label="Protegida vs agresiva"
              value={`${pct(m.protected_line_success_rate)} / ${pct(m.aggressive_line_success_rate)}`}
              testId="lld-prot-vs-aggr"
              hint={`n=${m.protected_line_n || 0}/${m.aggressive_line_n || 0}`}
            />
          </div>

          {/* 2. Ajustes recomendados por el engine */}
          {insights.length > 0 && (
            <Card data-testid="lld-insights-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-amber-300" />
                  Ajustes recomendados por el engine
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1.5 text-xs" data-testid="lld-insights">
                  {insights.map((ins, i) => (
                    <li key={i} className="flex gap-2 items-start">
                      <Badge variant="outline" className="text-[10px] shrink-0">{i + 1}</Badge>
                      <span className="leading-snug">{ins}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {/* Per-line success table (push rate por línea) */}
          {Object.keys(perLine).length > 0 && (
            <Card data-testid="lld-per-line-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Success rate por línea</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid="lld-per-line">
                  {Object.entries(perLine).sort((a, b) => Number(a[0]) - Number(b[0])).map(([ln, rate]) => (
                    <div key={ln} className="rounded-md border border-border bg-card/50 p-2 text-xs">
                      <div className="opacity-70">Línea {ln}</div>
                      <div className="text-base font-semibold">{pct(rate)}</div>
                      <div className="text-[10px] opacity-60">n={perLineN[ln] || 0}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* 3. Tabla últimos samples */}
          {recent.length > 0 && (
            <Card data-testid="lld-recent-samples-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Últimos picks ({recent.length})</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-xs" data-testid="lld-recent-samples">
                  <thead className="text-[10px] uppercase tracking-wider opacity-60">
                    <tr className="border-b border-border">
                      <th className="text-left py-1.5 pr-2">Fecha</th>
                      <th className="text-left py-1.5 pr-2">Deporte</th>
                      <th className="text-left py-1.5 pr-2">Engine</th>
                      <th className="text-left py-1.5 pr-2">Tú</th>
                      <th className="text-left py-1.5 pr-2">Distancia</th>
                      <th className="text-left py-1.5 pr-2">Clasificación</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((s) => {
                      const tone = CLASSIFICATION_TONES[(s.classification || 'UNDEFINED').toUpperCase()]
                        || CLASSIFICATION_TONES.UNDEFINED;
                      return (
                        <tr key={s.sample_id} className="border-b border-border/40 last:border-0" data-testid={`lld-sample-${s.sample_id}`}>
                          <td className="py-1.5 pr-2 whitespace-nowrap text-muted-foreground">{fmtDate(s.created_at)}</td>
                          <td className="py-1.5 pr-2 whitespace-nowrap opacity-80">{s.sport || '—'}</td>
                          <td className="py-1.5 pr-2 whitespace-nowrap">
                            <span className="opacity-70">{s.engine?.selection || `Línea ${s.engine?.line ?? '—'}`}</span>
                            <span className="opacity-50"> → {s.engine?.outcome || '—'}</span>
                          </td>
                          <td className="py-1.5 pr-2 whitespace-nowrap">
                            <span className="opacity-70">{s.user_actual?.selection || `Línea ${s.user_actual?.line ?? '—'}`}</span>
                            <span className="opacity-50"> → {s.user_actual?.outcome || '—'}</span>
                          </td>
                          <td className="py-1.5 pr-2 whitespace-nowrap font-mono text-xs">
                            {s.line_distance == null ? '—' : (s.line_distance > 0 ? `+${s.line_distance}` : s.line_distance)}
                          </td>
                          <td className="py-1.5 pr-2">
                            <span className={`inline-block rounded-md border px-1.5 py-0.5 text-[10px] ${tone}`}>
                              {s.classification || 'UNDEFINED'}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )}

          {/* 4. Histograma de distancias de línea */}
          {Object.keys(histogram).length > 0 && (
            <Card data-testid="lld-histogram-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Histograma de distancias de línea</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-end gap-1 h-32 overflow-x-auto pb-1" data-testid="lld-histogram">
                  {Object.entries(histogram).map(([bucket, count]) => {
                    const heightPct = histogramMax > 0 ? Math.max(4, (count / histogramMax) * 100) : 0;
                    const isZero = bucket === '0.0';
                    return (
                      <div key={bucket} className="flex flex-col items-center gap-1 min-w-[36px]">
                        <span className="text-[10px] opacity-70 font-mono">{count}</span>
                        <div
                          className={`w-7 rounded-t-sm ${isZero ? 'bg-amber-400/70' : 'bg-emerald-400/60'}`}
                          style={{ height: `${heightPct}%` }}
                          data-testid={`lld-hist-bar-${bucket}`}
                          title={`Distancia ${bucket}: ${count} muestras`}
                        />
                        <span className="text-[10px] font-mono opacity-80">{bucket}</span>
                      </div>
                    );
                  })}
                </div>
                <p className="text-[10px] opacity-60 mt-2">
                  Distancia = línea elegida − línea recomendada. Positivo = más protegida, negativo = más agresiva.
                </p>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Metric({ label, value, testId, hint }) {
  return (
    <div className="rounded-md border border-border bg-card/50 p-3" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wider opacity-60">{label}</div>
      <div className="text-lg font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] opacity-50 mt-0.5">{hint}</div>}
    </div>
  );
}

/**
 * LineLearningPanel — Collapsible wrapper used by DashboardPage so the
 * dashboard can show the learning analytics as a panel rather than a
 * separate page. Stays closed by default to avoid eager API calls on
 * page load.
 */
export function LineLearningPanel({ testId = 'line-learning-panel' }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-xl border border-border bg-card/40" data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-secondary/30 transition-colors rounded-xl"
        data-testid={`${testId}-toggle`}
      >
        <span className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-amber-300" />
          <span className="text-sm font-semibold">Aprendizaje de líneas</span>
          <Badge variant="outline" className="text-[10px]">observe-only</Badge>
        </span>
        {open
          ? <ChevronUp className="h-4 w-4 text-muted-foreground" />
          : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
      </button>
      {open && (
        <div className="px-4 pb-4 pt-1">
          <LineLearningDashboard embedded />
        </div>
      )}
    </section>
  );
}
