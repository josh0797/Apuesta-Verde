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
import { Eye, AlertTriangle, RefreshCcw, Loader2, Database, AlertOctagon } from 'lucide-react';
import { api } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { WorldCupLiveCard } from './WorldCupLiveCard';

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
  // F94.2 — pull World Cup matches for the pinned card above the KPI grid.
  const worldCupItems = Array.isArray(data?.items)
    ? data.items.filter((it) => Boolean(it?._is_world_cup))
    : [];
  const tsDiag = debug?.thestatsapi_diag || null;

  // F94.3 — Live enrichment persistence audit banner.
  // Fires when the upstream provider returned fixtures but the
  // persistence pipeline dropped every single one of them (historical
  // h2h_source bug failure mode).
  const enrichmentDropped = Boolean(debug?.enrichment_dropped_all_fixtures);
  const enrichmentErrorCode    = debug?.enrichment_error_code || null;
  const enrichmentErrorMessage = debug?.enrichment_error_message || null;
  const persistedLiveCount     = Number(debug?.persisted_live_count ?? 0);

  // Don't render the strip when there's nothing to surface AND the
  // provider returned 0 raw fixtures (avoid empty noise).
  const providerRaw = Number(debug?.provider_live_count ?? 0);
  const visible     = Number(debug?.visible_live_count ?? 0);
  // F94.2 — keep the strip visible whenever WC is detected, even if
  // the provider somehow returned 0.
  // F94.3 — also keep visible whenever the enrichment-dropped banner
  // must be shown, regardless of provider count.
  if (!loading && !error && providerRaw === 0 && worldCupItems.length === 0 && !enrichmentDropped) {
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

      {/* F94.3 — Live enrichment persistence audit banner (high severity). */}
      {enrichmentDropped && (
        <div
          className="rounded-lg border-2 border-rose-500/60 bg-rose-500/15 p-3 flex flex-col gap-1.5"
          role="alert"
          data-testid="live-enrichment-dropped-banner"
        >
          <div className="flex items-center gap-2">
            <AlertOctagon className="h-4 w-4 text-rose-200 shrink-0" />
            <span className="text-[12px] font-bold uppercase tracking-wider text-rose-100">
              {lang === 'en'
                ? 'Technical error — live enrichment dropped all fixtures'
                : 'Error técnico — la ingesta perdió todos los fixtures'}
            </span>
            {enrichmentErrorCode && (
              <Badge
                variant="outline"
                className="text-[10px] font-mono border-rose-300/40 bg-rose-500/20 text-rose-50 ml-auto"
                data-testid="live-enrichment-dropped-code"
              >
                {enrichmentErrorCode}
              </Badge>
            )}
          </div>
          <div
            className="text-[11.5px] text-rose-100/90 leading-snug"
            data-testid="live-enrichment-dropped-message"
          >
            {enrichmentErrorMessage || (
              lang === 'en'
                ? 'The provider returned live fixtures but none were persisted.'
                : 'El proveedor devolvió partidos en vivo pero ninguno fue persistido.'
            )}
          </div>
          <div className="flex items-center gap-3 text-[10.5px] font-mono text-rose-200/80">
            <span data-testid="live-enrichment-dropped-discovery">
              {lang === 'en' ? 'discovery' : 'descubrimiento'}: <b className="text-rose-50">{providerRaw}</b>
            </span>
            <span data-testid="live-enrichment-dropped-persisted">
              {lang === 'en' ? 'persisted' : 'persistidos'}: <b className="text-rose-50">{persistedLiveCount}</b>
            </span>
          </div>
          <div className="text-[10.5px] text-rose-200/70 italic">
            {lang === 'en'
              ? 'Hint: review ingest_live / data_ingestion logs and provider connectivity, then retry refresh.'
              : 'Sugerencia: revisar logs de ingest_live / data_ingestion y la conectividad de proveedores, luego reintentar refresh.'}
          </div>
        </div>
      )}

      {/* F94.2 — World Cup pinned card (above everything else). */}
      {worldCupItems.length > 0 && (
        <WorldCupLiveCard
          items={data?.items}
          worldCupDebug={debug}
          lang={lang}
          testId="football-live-world-cup-card"
        />
      )}

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

      {/* F94.2 — TheStatsAPI structured diagnostic (only when present + non-OK) */}
      {tsDiag && tsDiag.status && tsDiag.status !== 'OK' && (
        <div
          className="rounded-lg border border-blue-500/20 bg-blue-500/[0.04] p-2.5 flex flex-col gap-1.5"
          data-testid="football-live-visibility-thestatsapi-diag"
        >
          <div className="flex items-center gap-1.5 text-[11px] text-blue-200">
            <Database className="h-3 w-3" />
            <span className="font-semibold uppercase tracking-wider">
              {lang === 'en' ? 'TheStatsAPI probe' : 'Sonda TheStatsAPI'}
            </span>
            <Badge
              variant="outline"
              className="text-[10px] font-mono border-blue-500/30 bg-blue-500/10 text-blue-100"
              data-testid="football-live-visibility-thestatsapi-status"
            >
              {tsDiag.status}
            </Badge>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1 text-[10.5px] text-muted-foreground font-mono">
            {tsDiag.endpoint && (
              <span data-testid="football-live-visibility-thestatsapi-endpoint">
                <span className="opacity-70 uppercase tracking-wider">
                  {lang === 'en' ? 'Endpoint' : 'Endpoint'}:
                </span>{' '}
                <span className="text-foreground/80">{tsDiag.endpoint}</span>
              </span>
            )}
            {tsDiag.http_status != null && (
              <span data-testid="football-live-visibility-thestatsapi-http">
                <span className="opacity-70 uppercase tracking-wider">HTTP:</span>{' '}
                <span className="text-foreground/80">{tsDiag.http_status}</span>
              </span>
            )}
            <span data-testid="football-live-visibility-thestatsapi-raw">
              <span className="opacity-70 uppercase tracking-wider">
                {lang === 'en' ? 'Raw' : 'Raw'}:
              </span>{' '}
              <span className="text-foreground/80">{tsDiag.raw_count ?? 0}</span>
            </span>
            {tsDiag.reason && (
              <span
                className="col-span-2 sm:col-span-3"
                data-testid="football-live-visibility-thestatsapi-reason"
              >
                <span className="opacity-70 uppercase tracking-wider">
                  {lang === 'en' ? 'Reason' : 'Motivo'}:
                </span>{' '}
                <span className="text-foreground/80">{tsDiag.reason}</span>
              </span>
            )}
          </div>
          {Array.isArray(tsDiag.sample_payload_keys) && tsDiag.sample_payload_keys.length > 0 && (
            <div
              className="text-[10px] text-blue-200/70 font-mono break-all"
              data-testid="football-live-visibility-thestatsapi-keys"
            >
              keys: [{tsDiag.sample_payload_keys.join(', ')}]
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default FootballLiveVisibilityStrip;
