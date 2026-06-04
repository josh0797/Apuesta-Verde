/**
 * MLBCalibrationPanel
 * ────────────────────────────────────────────────────────────────────
 * Renders the calibration of the Negative-Binomial totals model vs.
 * the legacy Poisson model. Reads `/api/mlb/run-evaluations/summary`
 * which already returns UI-friendly fields:
 *
 *   • summary.headline                  → KPI strip (hit-rate, settled, pending)
 *   • summary.totals_dispersion_calibration {current_ratio, empirical_ratio,
 *       under_hit_rate, nb_mean_delta, sample_size, confidence_tier,
 *       recommendation}
 *   • summary.totals_dispersion_by_bucket {pressure, f5, fragility, park}
 *   • summary.nb_vs_poisson_aggregate   → avg / share corrected
 *   • summary.f5_vs_full_game_under     → bucket reconciliation
 *
 * Fail-soft: the panel returns `null` while loading, an error banner
 * if the fetch fails, and renders any block independently of the rest
 * if its data is missing.
 *
 * Test IDs follow the convention `mlb-calibration-*` so e2e flows can
 * pin the rendered values.
 */
import { useEffect, useState, useCallback } from 'react';
import {
  Activity, AlertTriangle, BarChart3, ChevronDown, ChevronUp,
  Gauge, Info, RefreshCcw, ShieldCheck, Sparkles, TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';

// ── Static labels ──────────────────────────────────────────────────
const PRESSURE_LABEL = {
  LOW_PRESSURE:       'Presión baja',
  MODERATE_PRESSURE:  'Presión moderada',
  HIGH_PRESSURE:      'Presión alta',
  CHAOTIC_PRESSURE:   'Presión caótica',
};

const FRAGILITY_LABEL = {
  LOW:    'Fragilidad baja',
  MEDIUM: 'Fragilidad media',
  HIGH:   'Fragilidad alta',
};

const F5_LABEL = {
  F5:        'First 5 Innings',
  FULL_GAME: 'Juego completo',
};

const PARK_LABEL = {
  HITTER_FRIENDLY:   'Parque ofensivo',
  NEUTRAL_PARK:      'Parque neutral',
  PITCHER_FRIENDLY:  'Parque pitcher-friendly',
  UNKNOWN_PARK:      'Parque desconocido',
};

const CONFIDENCE_BADGE = {
  VALIDATED:  { label: 'Validado',   cls: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30' },
  USEFUL:     { label: 'Útil',       cls: 'bg-cyan-500/15 text-cyan-200 border-cyan-500/30' },
  LOW_SAMPLE: { label: 'Muestra baja', cls: 'bg-amber-500/15 text-amber-200 border-amber-500/30' },
};

const RECOMMENDATION_LABEL = {
  default_ok:                'Ratio default (1.5) sigue calibrado',
  tighten_dispersion_lower:  'Sugerencia: bajar dispersion_ratio (<1.4)',
  loosen_dispersion_higher:  'Sugerencia: subir dispersion_ratio (>1.6)',
};

// ── Helpers ────────────────────────────────────────────────────────
function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(digits)}%`;
}

function fmtRatio(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(3);
}

function fmtDelta(value, suffix = ' pts') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const sign = Number(value) > 0 ? '+' : '';
  return `${sign}${Number(value).toFixed(1)}${suffix}`;
}

function deltaTone(value) {
  if (value === null || value === undefined) return 'text-muted-foreground';
  const n = Number(value);
  if (n >= 5) return 'text-orange-300';
  if (n >= 2) return 'text-amber-300';
  if (n <= -2) return 'text-cyan-300';
  return 'text-foreground/80';
}

// ── Sub-components ─────────────────────────────────────────────────
function KPI({ label, value, hint, accent, icon: Icon, testId }) {
  const accentClass = {
    emerald: 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200',
    amber:   'border-amber-500/30 bg-amber-500/5 text-amber-200',
    cyan:    'border-cyan-500/30 bg-cyan-500/5 text-cyan-200',
    rose:    'border-rose-500/30 bg-rose-500/5 text-rose-200',
  }[accent] || 'border-border bg-card';
  return (
    <div className={`rounded-lg border p-3 ${accentClass}`} data-testid={testId}>
      <div className="text-[11px] uppercase tracking-wide opacity-80 flex items-center gap-1.5">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className="text-2xl mono font-mono-tabular font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] opacity-70 mt-0.5 mono font-mono-tabular">{hint}</div>}
    </div>
  );
}

function BucketRow({ label, bucket, testId }) {
  const safe = bucket || {};
  const ratio = safe.suggested_ratio;
  const delta = safe.avg_calibration_delta_pts;
  const hit = safe.hit_rate;
  const samples = safe.sample_size || 0;
  return (
    <div
      className="grid grid-cols-12 gap-2 items-center py-2 border-t border-border first:border-t-0 text-xs"
      data-testid={testId}
    >
      <div className="col-span-4 truncate text-foreground/90">{label}</div>
      <div className="col-span-2 text-right mono font-mono-tabular text-muted-foreground" data-testid={`${testId}-samples`}>
        n={samples}
      </div>
      <div className="col-span-2 text-right mono font-mono-tabular" data-testid={`${testId}-ratio`}>
        {fmtRatio(ratio)}
      </div>
      <div
        className={`col-span-2 text-right mono font-mono-tabular ${deltaTone(delta)}`}
        data-testid={`${testId}-delta`}
      >
        {fmtDelta(delta)}
      </div>
      <div className="col-span-2 text-right mono font-mono-tabular text-foreground/80" data-testid={`${testId}-hit-rate`}>
        {hit === null || hit === undefined ? '—' : fmtPct(hit, 1)}
      </div>
    </div>
  );
}

function BucketTable({ title, icon: Icon, dimension, labelMap }) {
  if (!dimension || typeof dimension !== 'object') return null;
  const rows = Object.entries(dimension);
  if (rows.length === 0) return null;
  // Sort so non-empty buckets surface first.
  rows.sort((a, b) => (b[1]?.sample_size || 0) - (a[1]?.sample_size || 0));
  return (
    <div className="rounded-lg border border-border bg-secondary/20 p-3" data-testid={`mlb-calibration-bucket-${title}`}>
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
        {Icon && <Icon className="h-3 w-3" />}
        <span>{title}</span>
      </div>
      <div className="grid grid-cols-12 gap-2 text-[10px] uppercase tracking-wider text-muted-foreground/80 pb-1 border-b border-border">
        <div className="col-span-4">Bucket</div>
        <div className="col-span-2 text-right">Muestra</div>
        <div className="col-span-2 text-right">Ratio</div>
        <div className="col-span-2 text-right">Δ NB-Poisson</div>
        <div className="col-span-2 text-right">Acierto</div>
      </div>
      {rows.map(([key, bucket]) => (
        <BucketRow
          key={key}
          label={(labelMap && labelMap[key]) || key}
          bucket={bucket}
          testId={`mlb-calibration-${title}-${key}`}
        />
      ))}
    </div>
  );
}

// ── Main panel ─────────────────────────────────────────────────────
export default function MLBCalibrationPanel({ days = 30 }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(true);
  // Cohort toggle: "_slate" = real pregame picks (default),
  // "_slate_backtest" = historical replay seeded by mlb_backtest_runner.
  const [cohort, setCohort] = useState('_slate');

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { days };
      if (cohort && cohort !== '_slate') params.user_id = cohort;
      // Backtest cohort: widen the window so historical seeds are visible.
      // The server caps non-slate cohorts at 730 days so we can request
      // a generous window without worrying about validation.
      if (cohort === '_slate_backtest') params.days = 730;
      const r = await api.get('/mlb/run-evaluations/summary', { params });
      const sum = r?.data?.summary || r?.data || null;
      setSummary(sum);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('MLBCalibrationPanel fetch failed:', err);
      setError(
        err?.response?.data?.detail
          || err?.message
          || 'No se pudo cargar la calibración NB.',
      );
    } finally {
      setLoading(false);
    }
  }, [days, cohort]);

  useEffect(() => { fetchSummary(); }, [fetchSummary]);

  if (loading && !summary) {
    return (
      <section
        className="rounded-xl border border-border bg-card p-4 md:p-5 space-y-3"
        data-testid="mlb-calibration-loading"
      >
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-32 w-full" />
      </section>
    );
  }

  if (error) {
    return (
      <section
        className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-4"
        data-testid="mlb-calibration-error"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-start gap-2 text-rose-200">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">No se pudo cargar la calibración del modelo NB.</p>
              <p className="text-xs opacity-80 mt-1">{error}</p>
            </div>
          </div>
          <Button
            size="sm" variant="ghost"
            onClick={fetchSummary}
            data-testid="mlb-calibration-retry"
          >
            <RefreshCcw className="h-3.5 w-3.5 mr-1.5" />Reintentar
          </Button>
        </div>
      </section>
    );
  }

  if (!summary) return null;

  const headline = summary.headline || {};
  const disp = summary.totals_dispersion_calibration || {};
  const byBucket = summary.totals_dispersion_by_bucket || {};
  const nbAgg = summary.nb_vs_poisson_aggregate || {};
  const f5 = summary.f5_vs_full_game_under || {};
  const confTier = CONFIDENCE_BADGE[disp.confidence_tier] || null;
  const recoLabel = RECOMMENDATION_LABEL[disp.recommendation];
  const empiricalRatio = disp.empirical_ratio ?? disp.suggested_ratio;
  const currentRatio = disp.current_ratio ?? disp.current_default ?? 1.5;
  const sampleSize = disp.sample_size || 0;
  const underHitRate =
    disp.under_hit_rate !== undefined && disp.under_hit_rate !== null
      ? Number(disp.under_hit_rate) * (Number(disp.under_hit_rate) <= 1 ? 100 : 1)
      : null;

  return (
    <section
      className="rounded-xl border border-border bg-card p-4 md:p-5 space-y-4"
      data-testid="mlb-calibration-panel"
    >
      {/* Header */}
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <div className="rounded-lg bg-cyan-500/15 text-cyan-200 p-1.5">
            <Gauge className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold tracking-tight flex items-center gap-2">
              Calibración Modelo NB · MLB Totales
              {confTier && (
                <Badge
                  variant="outline"
                  className={`text-[10px] uppercase ${confTier.cls}`}
                  data-testid="mlb-calibration-confidence-tier"
                >
                  {confTier.label}
                </Badge>
              )}
            </h2>
            <p className="text-[11px] text-muted-foreground" data-testid="mlb-calibration-subtitle">
              {cohort === '_slate_backtest'
                ? `Cohort: Backtest histórico · ventana ${summary.window_days || days} días`
                : `Comparación Negative-Binomial vs Poisson · ventana ${summary.window_days || days} días`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Cohort toggle — switch between live slate and historical backtest. */}
          <div
            className="inline-flex rounded-md border border-border overflow-hidden text-[11px]"
            role="tablist"
            data-testid="mlb-calibration-cohort-tabs"
          >
            <button
              type="button"
              onClick={() => setCohort('_slate')}
              className={`px-2.5 py-1 transition-colors ${cohort === '_slate' ? 'bg-cyan-500/20 text-cyan-100' : 'bg-transparent text-muted-foreground hover:text-foreground'}`}
              data-testid="mlb-calibration-cohort-slate"
              aria-pressed={cohort === '_slate'}
            >
              Picks reales
            </button>
            <button
              type="button"
              onClick={() => setCohort('_slate_backtest')}
              className={`px-2.5 py-1 border-l border-border transition-colors ${cohort === '_slate_backtest' ? 'bg-amber-500/20 text-amber-100' : 'bg-transparent text-muted-foreground hover:text-foreground'}`}
              data-testid="mlb-calibration-cohort-backtest"
              aria-pressed={cohort === '_slate_backtest'}
            >
              Backtest
            </button>
          </div>
          <Button
            size="sm" variant="ghost"
            onClick={fetchSummary}
            disabled={loading}
            data-testid="mlb-calibration-refresh"
          >
            <RefreshCcw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            {loading ? 'Actualizando' : 'Refrescar'}
          </Button>
          <Button
            size="sm" variant="ghost"
            onClick={() => setExpanded(v => !v)}
            data-testid="mlb-calibration-toggle"
          >
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      {/* Headline KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="mlb-calibration-headline">
        <KPI
          label="Ratio actual"
          value={fmtRatio(currentRatio)}
          hint="MLB_TOTALS_DISPERSION_RATIO"
          accent="cyan"
          icon={Sparkles}
          testId="mlb-calibration-kpi-current-ratio"
        />
        <KPI
          label="Ratio empírico"
          value={fmtRatio(empiricalRatio)}
          hint={`n=${sampleSize}`}
          accent={
            empiricalRatio == null ? undefined
              : (Math.abs((empiricalRatio || 0) - 1.5) <= 0.1 ? 'emerald' : 'amber')
          }
          icon={BarChart3}
          testId="mlb-calibration-kpi-empirical-ratio"
        />
        <KPI
          label="Acierto global"
          value={fmtPct(headline.hit_rate)}
          hint={`${headline.won || 0}/${headline.total_settled || 0} liquidados`}
          accent={(headline.hit_rate ?? 0) >= 55 ? 'emerald' : 'amber'}
          icon={ShieldCheck}
          testId="mlb-calibration-kpi-hit-rate"
        />
        <KPI
          label="Δ NB-Poisson promedio"
          value={fmtDelta(nbAgg.avg_delta_pts)}
          hint={
            nbAgg.available
              ? `${nbAgg.share_under_corrected ?? 0}% Unders corregidos`
              : 'Sin telemetría aún'
          }
          accent={(nbAgg.avg_delta_pts ?? 0) >= 3 ? 'amber' : undefined}
          icon={(nbAgg.avg_delta_pts ?? 0) >= 0 ? TrendingDown : TrendingUp}
          testId="mlb-calibration-kpi-nb-delta"
        />
      </div>

      {/* Calibration insight block */}
      {expanded && (
        <>
          <div
            className="rounded-lg border border-border bg-secondary/20 p-3 text-xs"
            data-testid="mlb-calibration-insight"
          >
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 mt-0.5 text-cyan-300 shrink-0" />
              <div className="space-y-1 flex-1">
                <p className="text-foreground/90">
                  {disp.available
                    ? `Modelo NB activo con dispersion_ratio=${fmtRatio(currentRatio)}; la varianza/media empírica sugiere ${fmtRatio(empiricalRatio)} (muestra ${sampleSize}).`
                    : `Aún no hay suficientes liquidaciones (${sampleSize}/${disp.min_samples_required ?? 30}) para sugerir un ratio empírico. Mostrando el valor por defecto 1.5.`}
                </p>
                {underHitRate !== null && (
                  <p className="text-muted-foreground" data-testid="mlb-calibration-under-hit-rate">
                    Acierto Unders (F5 + Full Game): <span className="text-foreground/90 mono font-mono-tabular">{fmtPct(underHitRate)}</span>
                  </p>
                )}
                {recoLabel && (
                  <p className="text-muted-foreground">{recoLabel}</p>
                )}
              </div>
            </div>
          </div>

          {/* F5 vs Full Game Under reconciliation */}
          {(f5.f5_under?.total > 0 || f5.full_game_won?.total > 0 || f5.bullpen_broke_under?.total > 0) && (
            <div
              className="rounded-lg border border-border bg-secondary/20 p-3"
              data-testid="mlb-calibration-f5-vs-full"
            >
              <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted-foreground mb-2">
                <Activity className="h-3 w-3" />
                <span>F5 Under vs Full Game Under</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <BadgeStat
                  label="F5 Under"
                  value={`${f5.f5_under?.won || 0}/${f5.f5_under?.total || 0}`}
                  hint={f5.f5_under?.hit_rate != null ? `${fmtPct(f5.f5_under.hit_rate)}` : null}
                />
                <BadgeStat
                  label="Full Game Under"
                  value={`${f5.full_game_under?.won || 0}/${f5.full_game_under?.total || 0}`}
                  hint={f5.full_game_under?.hit_rate != null ? `${fmtPct(f5.full_game_under.hit_rate)}` : null}
                />
                <BadgeStat
                  label="Bullpen rompe Under"
                  value={`${f5.bullpen_broke_under?.total || 0}`}
                  hint={`${f5.games_with_both_markets || 0} juegos cruzados`}
                />
                <BadgeStat
                  label="F5 ✓ / FG ✗"
                  value={`${f5.f5_won_full_game_lost?.total || 0}`}
                  hint="bullpens fallidos"
                />
              </div>
            </div>
          )}

          {/* Bucketed dispersion grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <BucketTable
              title="pressure"
              icon={TrendingUp}
              dimension={byBucket.pressure}
              labelMap={PRESSURE_LABEL}
            />
            <BucketTable
              title="f5"
              icon={Activity}
              dimension={byBucket.f5}
              labelMap={F5_LABEL}
            />
            <BucketTable
              title="fragility"
              icon={AlertTriangle}
              dimension={byBucket.fragility}
              labelMap={FRAGILITY_LABEL}
            />
            <BucketTable
              title="park"
              icon={BarChart3}
              dimension={byBucket.park}
              labelMap={PARK_LABEL}
            />
          </div>

          {/* Footer note */}
          <p className="text-[10px] text-muted-foreground italic" data-testid="mlb-calibration-footer">
            Los buckets se actualizan a medida que los partidos se liquidan. Muestra mínima por bucket: 10. Δ NB-Poisson positivo = NB corrige hacia abajo el Under.
          </p>
        </>
      )}
    </section>
  );
}

function BadgeStat({ label, value, hint }) {
  return (
    <div className="rounded-md border border-border bg-card/60 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-base mono font-mono-tabular font-semibold mt-0.5">{value}</div>
      {hint && <div className="text-[10px] text-muted-foreground/80 mono font-mono-tabular">{hint}</div>}
    </div>
  );
}

export { MLBCalibrationPanel };
