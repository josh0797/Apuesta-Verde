/**
 * F87.1 — Discovery Debug Sheet.
 *
 * Right-side Sheet that surfaces the upstream football discovery audit
 * (Adapter → Contract → Discovery cascade) so users can see WHERE
 * fixtures were lost when `Analizados = 0` but raw fixtures existed.
 *
 * Consumes `GET /api/football/discovery/debug?refresh=true`.
 * Differentiates:
 *   - "Adapter returned empty"     (raw_count == 0 for every adapter)
 *   - "Contract rejected raw rows" (raw > 0 but normalised == 0)
 *
 * Never silently shows "no hay partidos".
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { AlertCircle, RefreshCcw, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';

function StatusPill({ tone, children, testId }) {
  const toneCls = {
    amber:   'border-amber-500/40 bg-amber-500/10 text-amber-200',
    red:     'border-red-500/40   bg-red-500/10   text-red-200',
    emerald: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
    neutral: 'border-border bg-secondary/40 text-muted-foreground',
  }[tone] || 'border-border bg-secondary/40 text-muted-foreground';
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold border ${toneCls}`}
      data-testid={testId}
    >
      {children}
    </span>
  );
}

function AdapterRow({ name, data, lang }) {
  const [open, setOpen] = useState(false);
  const raw     = data?.raw_count ?? 0;
  const kept    = data?.normalised_count ?? 0;
  const dropped = data?.dropped_count ?? 0;
  const topRC   = data?.top_reason || null;
  const samples = Array.isArray(data?.dropped_samples) ? data.dropped_samples : [];

  let tone = 'neutral';
  let summary = '';
  if (data?.adapter_returned_empty) {
    tone = 'amber';
    summary = lang === 'en' ? 'Adapter returned 0 fixtures' : 'Adapter devolvió 0 fixtures';
  } else if (data?.had_raw_but_all_rejected) {
    tone = 'red';
    summary = lang === 'en'
      ? `Raw=${raw} but contract rejected all`
      : `Raw=${raw} pero el contract rechazó todos`;
  } else if (raw > 0 && kept > 0) {
    tone = 'emerald';
    summary = lang === 'en' ? `Kept ${kept}/${raw}` : `Aceptados ${kept}/${raw}`;
  }

  return (
    <div
      className="border border-border rounded-md p-3 bg-card/40"
      data-testid={`discovery-debug-adapter-${name}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 text-left"
        data-testid={`discovery-debug-adapter-${name}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-xs uppercase tracking-wider text-foreground/90 truncate">
            {name}
          </span>
          <StatusPill tone={tone} testId={`discovery-debug-adapter-${name}-status`}>
            {summary}
          </StatusPill>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs font-mono text-muted-foreground">
            raw={raw} · kept={kept} · drop={dropped}
          </span>
          {open ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </button>

      {open && (
        <div className="mt-3 space-y-2" data-testid={`discovery-debug-adapter-${name}-detail`}>
          {topRC && (
            <div className="text-xs">
              <span className="text-muted-foreground">
                {lang === 'en' ? 'Top reason' : 'Razón dominante'}:{' '}
              </span>
              <code className="px-1 py-0.5 rounded bg-secondary/60 text-foreground">
                {topRC}
              </code>
            </div>
          )}
          {data?.message_debug && (
            <div className="text-xs text-muted-foreground italic">
              {data.message_debug}
            </div>
          )}
          {samples.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-semibold text-foreground/80">
                {lang === 'en'
                  ? `Dropped samples (showing ${data?.dropped_samples_shown ?? samples.length}/${dropped}, cap ${data?.dropped_samples_cap ?? '?'})`
                  : `Muestras rechazadas (mostrando ${data?.dropped_samples_shown ?? samples.length}/${dropped}, cap ${data?.dropped_samples_cap ?? '?'})`}
              </div>
              <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {samples.map((s, idx) => (
                  <div
                    key={idx}
                    className="rounded border border-border/70 bg-background/40 p-2 text-xs"
                    data-testid={`discovery-debug-adapter-${name}-sample-${idx}`}
                  >
                    <div className="flex flex-wrap gap-x-3 gap-y-1 mb-2">
                      <span><span className="text-muted-foreground">raw_id:</span> <code>{String(s?.raw_id ?? '—')}</code></span>
                      <span><span className="text-muted-foreground">league:</span> <code>{String(s?.league ?? '—')}</code></span>
                      <span><span className="text-muted-foreground">reason:</span> <code>{String(s?.reason_code ?? '—')}</code></span>
                    </div>
                    <details className="text-[11px]">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {lang === 'en' ? 'Paths probed (home / away / kickoff)' : 'Paths probados (home / away / kickoff)'}
                      </summary>
                      <pre className="mt-1 whitespace-pre-wrap break-all bg-secondary/40 p-2 rounded">
{JSON.stringify({
  home_candidates:    s?.home_candidates ?? {},
  away_candidates:    s?.away_candidates ?? {},
  kickoff_candidates: s?.kickoff_candidates ?? {},
}, null, 2)}
                      </pre>
                    </details>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function DiscoveryDebugSheet({ open, onOpenChange, lang = 'es' }) {
  const [loading, setLoading] = useState(false);
  const [data,    setData]    = useState(null);
  const [error,   setError]   = useState(null);

  const fetchDebug = useCallback(async (refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get('/football/discovery/debug', {
        params: refresh ? { refresh: true } : undefined,
      });
      setData(r.data);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('[DISCOVERY_DEBUG] fetch failed', err);
      setError(err?.response?.data?.detail || err?.message || 'Network error');
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on initial open. We guard with a ref so a quick close+open
  // does not re-trigger a fetch mid-flight.
  const fetchedOnceRef = useRef(false);
  useEffect(() => {
    if (!open) {
      fetchedOnceRef.current = false;
      return;
    }
    if (fetchedOnceRef.current) return;
    fetchedOnceRef.current = true;
    fetchDebug(false);
  }, [open, fetchDebug]);

  const adapterAudit = data?.adapter_audit || {};
  const adapterKeys  = Object.keys(adapterAudit);
  const rawTotal     = data?.raw_total ?? 0;
  const normTotal    = data?.normalised_total ?? 0;
  const total        = data?.total ?? 0;
  const hadRawAllRejected = !!data?.had_raw_but_all_rejected;
  const anyEmpty          = !!data?.any_adapter_returned_empty;

  let bannerTone = 'neutral';
  let bannerMsg  = data?.ui_message
    || (lang === 'en' ? 'Discovery audit pending.' : 'Auditoría de discovery pendiente.');
  if (hadRawAllRejected) {
    bannerTone = 'red';
  } else if (rawTotal === 0 && anyEmpty) {
    bannerTone = 'amber';
  } else if (normTotal > 0) {
    bannerTone = 'emerald';
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-2xl overflow-y-auto"
        data-testid="discovery-debug-sheet"
      >
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <AlertCircle className="h-5 w-5 text-amber-400" />
            {lang === 'en' ? 'Discovery debug' : 'Debug de discovery'}
          </SheetTitle>
          <SheetDescription>
            {lang === 'en'
              ? 'Adapter → Contract → Cascade audit for the last football discovery run.'
              : 'Auditoría Adapter → Contract → Cascada de la última corrida de discovery de fútbol.'}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4 flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => fetchDebug(false)}
            disabled={loading}
            data-testid="discovery-debug-reload-btn"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
            ) : (
              <RefreshCcw className="h-4 w-4 mr-1.5" />
            )}
            {lang === 'en' ? 'Reload' : 'Recargar'}
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => fetchDebug(true)}
            disabled={loading}
            data-testid="discovery-debug-refresh-btn"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
            ) : (
              <RefreshCcw className="h-4 w-4 mr-1.5" />
            )}
            {lang === 'en' ? 'Force refresh' : 'Forzar refresh'}
          </Button>
        </div>

        {error && (
          <div
            className="mt-4 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200"
            data-testid="discovery-debug-error"
          >
            {error}
          </div>
        )}

        {loading && !data && (
          <div className="mt-6 space-y-3" data-testid="discovery-debug-loading">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}

        {data?.ok && data?.ran === false && (
          <div
            className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100"
            data-testid="discovery-debug-not-run"
          >
            {data?.message
              || (lang === 'en'
                  ? 'No discovery audit recorded yet. Hit "Force refresh" to run the cascade now.'
                  : 'Aún no hay auditoría de discovery. Pulsa "Forzar refresh" para correr la cascada.')}
          </div>
        )}

        {data?.ran && (
          <div className="mt-4 space-y-4" data-testid="discovery-debug-content">
            {/* Banner with the diagnosis. */}
            <div
              className={`rounded-md border p-3 text-sm ${
                bannerTone === 'red'     ? 'border-red-500/40 bg-red-500/10 text-red-100' :
                bannerTone === 'amber'   ? 'border-amber-500/40 bg-amber-500/10 text-amber-100' :
                bannerTone === 'emerald' ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100' :
                'border-border bg-secondary/40 text-foreground'
              }`}
              data-testid="discovery-debug-banner"
            >
              <div className="font-semibold mb-1">
                {hadRawAllRejected
                  ? (lang === 'en' ? 'Fixtures rejected by contract' : 'Fixtures rechazados por contract')
                  : rawTotal === 0
                    ? (lang === 'en' ? 'Adapters returned empty' : 'Los adapters devolvieron vacío')
                    : (lang === 'en' ? 'Discovery succeeded' : 'Discovery exitoso')}
              </div>
              <div data-testid="discovery-debug-banner-message">{bannerMsg}</div>
            </div>

            {/* High-level KPIs. */}
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-md border border-border bg-card p-2 text-center">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {lang === 'en' ? 'Raw total' : 'Raw total'}
                </div>
                <div className="text-xl font-bold font-mono" data-testid="discovery-debug-kpi-raw">
                  {rawTotal}
                </div>
              </div>
              <div className="rounded-md border border-border bg-card p-2 text-center">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {lang === 'en' ? 'Normalised' : 'Normalizados'}
                </div>
                <div className="text-xl font-bold font-mono" data-testid="discovery-debug-kpi-normalised">
                  {normTotal}
                </div>
              </div>
              <div className="rounded-md border border-border bg-card p-2 text-center">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {lang === 'en' ? 'Final' : 'Final'}
                </div>
                <div className="text-xl font-bold font-mono" data-testid="discovery-debug-kpi-final">
                  {total}
                </div>
              </div>
            </div>

            {/* Sources called. */}
            {Array.isArray(data?.sources_called) && data.sources_called.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {data.sources_called.map((src) => (
                  <Badge
                    key={src}
                    variant="outline"
                    className="text-[10px] font-mono"
                    data-testid={`discovery-debug-source-${src}`}
                  >
                    {src}
                  </Badge>
                ))}
                {data?.primary_winner && (
                  <Badge
                    className="text-[10px] font-mono bg-emerald-500/20 text-emerald-100 border-emerald-500/40"
                    data-testid="discovery-debug-primary-winner"
                  >
                    {lang === 'en' ? 'winner' : 'ganador'}: {data.primary_winner}
                  </Badge>
                )}
                {data?.merged && (
                  <Badge variant="outline" className="text-[10px]" data-testid="discovery-debug-merged">
                    {lang === 'en' ? 'merged' : 'mergeado'}
                  </Badge>
                )}
              </div>
            )}

            {/* Per-adapter breakdown. */}
            <div className="space-y-2" data-testid="discovery-debug-adapter-list">
              {adapterKeys.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  {lang === 'en'
                    ? 'No adapter audit available.'
                    : 'No hay auditoría de adapters disponible.'}
                </div>
              ) : (
                adapterKeys.map((name) => (
                  <AdapterRow
                    key={name}
                    name={name}
                    data={adapterAudit[name]}
                    lang={lang}
                  />
                ))
              )}
            </div>

            {/* Sample fixtures that DID make it through. */}
            {Array.isArray(data?.sample_fixtures) && data.sample_fixtures.length > 0 && (
              <details
                className="rounded-md border border-border bg-card/40 p-3 text-xs"
                data-testid="discovery-debug-samples"
              >
                <summary className="cursor-pointer font-semibold text-foreground/80">
                  {lang === 'en' ? 'Sample of accepted fixtures' : 'Muestra de fixtures aceptados'}{' '}
                  ({data.sample_fixtures.length})
                </summary>
                <ul className="mt-2 space-y-1">
                  {data.sample_fixtures.map((s, idx) => (
                    <li key={idx} className="font-mono">
                      <span className="text-muted-foreground">[{s?.source || '?'}]</span>{' '}
                      {s?.home || '—'} vs {s?.away || '—'} ·{' '}
                      <span className="text-muted-foreground">{s?.league || '—'}</span>{' '}
                      <span className="text-muted-foreground">{s?.kickoff_iso || ''}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

export default DiscoveryDebugSheet;
