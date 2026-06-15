/**
 * F94 — FootballLiveVisibilityStrip
 *
 * Renders the football live fixtures the priority filter would have
 * hidden (exotic / low-priority / no-market-identity leagues). The
 * "EN CURSO AHORA" list above this strip remains the analyzable subset;
 * this strip is the audit trail so users never see "0 live" while the
 * provider is returning matches.
 *
 * Consumes `GET /api/football/live/visibility`.
 */
import { useEffect, useState } from 'react';
import { Eye, AlertTriangle, RefreshCcw, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

const REASON_LABEL = {
  EXOTIC_LEAGUE:        { es: 'Liga exótica',           en: 'Exotic league' },
  LOW_PRIORITY_LEAGUE:  { es: 'Baja prioridad',         en: 'Low priority' },
  NO_MARKET_IDENTITY:   { es: 'Sin mercado',            en: 'No market identity' },
  CLASSIFICATION_FAILED:{ es: 'Sin clasificar',         en: 'Unclassified' },
};

function ReasonChip({ reason, secondary, lang }) {
  if (!reason) return null;
  const label = (REASON_LABEL[reason] || {})[lang] || reason;
  return (
    <span
      className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-200"
      data-testid={`live-visibility-reason-${reason}`}
    >
      {label}
      {Array.isArray(secondary) && secondary.length > 0 && (
        <span className="ml-1 opacity-70">
          +{secondary.length}
        </span>
      )}
    </span>
  );
}

export function FootballLiveVisibilityStrip({ lang = 'es', testId }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const fetchVisibility = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get('/football/live/visibility');
      setData(r.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchVisibility();
    // Refresh every 60s like the rest of the live page.
    const id = setInterval(fetchVisibility, 60_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const debug   = data?.live_debug || {};
  // Surface every fixture in the visibility set EXCEPT those that are
  // already analysable (the "EN CURSO AHORA" list above already shows
  // them). Discarded but visible fixtures live here.
  const exotic  = Array.isArray(data?.items)
    ? data.items.filter((it) => it?.analysis_status === 'DISCARDED')
    : [];

  // Don't render the strip when there's nothing to surface AND the
  // provider returned 0 raw fixtures (avoid empty noise).
  const providerRaw = Number(debug?.provider_live_count ?? 0);
  const visible     = Number(debug?.visible_live_count ?? 0);
  if (!loading && !error && providerRaw === 0) {
    return null;
  }

  return (
    <div
      className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-3 flex flex-col gap-3"
      data-testid={testId || 'football-live-visibility-strip'}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Eye className="h-4 w-4 text-amber-200 shrink-0" />
          <span className="text-sm font-semibold text-amber-100">
            {lang === 'en'
              ? 'All live fixtures (visibility audit)'
              : 'Todos los fixtures en vivo (auditoría de visibilidad)'}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={fetchVisibility}
          disabled={loading}
          className="h-7 px-2 text-amber-200 hover:bg-amber-500/15"
          data-testid="football-live-visibility-refresh"
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCcw className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {/* KPI strip — always rendered. */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5 text-[10.5px]">
        {[
          ['provider',  debug.provider_live_count          ?? 0, lang === 'en' ? 'Provider' : 'Proveedor'],
          ['sport',     debug.after_sport_filter_count     ?? 0, lang === 'en' ? 'Sport'    : 'Deporte'],
          ['league',    debug.after_league_filter_count    ?? 0, lang === 'en' ? 'League'   : 'Liga'],
          ['visible',   debug.visible_live_count           ?? 0, lang === 'en' ? 'Visible'  : 'Visible'],
          ['analyzable',debug.analysis_eligible_live_count ?? 0, lang === 'en' ? 'Analyzable' : 'Analizable'],
          ['hidden',    debug.hidden_by_priority_filter    ?? 0, lang === 'en' ? 'Hidden' : 'Ocultos'],
        ].map(([key, value, label]) => (
          <div
            key={key}
            className="rounded border border-amber-500/15 bg-background/40 p-1.5 text-center"
            data-testid={`live-visibility-kpi-${key}`}
          >
            <div className="text-[9px] uppercase tracking-wider text-amber-200/70">
              {label}
            </div>
            <div className="font-mono font-semibold text-foreground">
              {value}
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div
          className="text-[11px] text-rose-200"
          data-testid="football-live-visibility-error"
        >
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="grid gap-2">
          <Skeleton className="h-10 rounded" />
          <Skeleton className="h-10 rounded" />
        </div>
      )}

      {!loading && exotic.length === 0 && visible > 0 && (
        <p
          className="text-[11px] text-muted-foreground italic"
          data-testid="football-live-visibility-all-analyzable"
        >
          {lang === 'en'
            ? 'All currently-live fixtures are already analyzable above.'
            : 'Todos los partidos en vivo actuales ya son analizables arriba.'}
        </p>
      )}

      {!loading && exotic.length > 0 && (
        <div className="flex flex-col gap-1.5" data-testid="football-live-visibility-list">
          <div className="text-[11px] text-amber-200/80 flex items-center gap-1.5">
            <AlertTriangle className="h-3 w-3" />
            {lang === 'en'
              ? `${exotic.length} live fixture(s) visible but not analyzed`
              : `${exotic.length} fixture(s) en vivo visibles pero no analizados`}
          </div>
          {exotic.map((it) => {
            const home = it?.teams?.home?.name || '—';
            const away = it?.teams?.away?.name || '—';
            const lg   = it?.league?.name || '—';
            const ctr  = it?.league?.country || null;
            const min  = it?.elapsed != null ? `${it.elapsed}'` : (it?.status_short || '');
            return (
              <div
                key={it.fixture_id || `${home}-${away}`}
                className="rounded border border-amber-500/15 bg-background/50 p-2 flex flex-wrap items-center gap-x-3 gap-y-1"
                data-testid={`live-visibility-row-${it.fixture_id || home + away}`}
              >
                <span className="text-[11px] font-mono tabular-nums text-amber-300 w-10 shrink-0">
                  {min}
                </span>
                <span className="text-[12px] text-foreground truncate">
                  {home} <span className="text-muted-foreground">vs</span> {away}
                </span>
                <Badge variant="outline" className="text-[10px] font-mono">
                  {lg}{ctr ? ` · ${ctr}` : ''}
                </Badge>
                <ReasonChip
                  reason={it.discard_reason}
                  secondary={it.secondary_reasons}
                  lang={lang}
                />
                {/* Always show the "Visible / no analizado" status. */}
                <span
                  className="text-[10px] text-muted-foreground italic"
                  data-testid={`live-visibility-status-${it.fixture_id}`}
                >
                  {lang === 'en'
                    ? 'Visible / not analyzed'
                    : 'Visible / no analizado'}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default FootballLiveVisibilityStrip;
