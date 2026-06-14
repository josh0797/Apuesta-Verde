import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  AlertCircle, CheckCircle2, Bug, RefreshCw, Loader2, X,
} from 'lucide-react';
import { api } from '@/lib/api';

/**
 * Phase F83-update — Corners debug dialog.
 *
 * Consumer of ``GET /api/football/corners/debug?match_id=...``. Shows
 * the exact cascade order used, which providers were tried, and which
 * reason_code each one returned so users (and engineers) can see why
 * corners failed to load.
 *
 * Drop-in component; opens via the ``open`` controlled prop.
 */
export const CornersDebugDialog = ({ matchId, open, onOpenChange, lang = 'es' }) => {
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const [data,    setData]    = useState(null);

  const T = lang === 'en'
    ? {
        title:        'Corners debug',
        subtitle:     'Provider-by-provider cascade trace.',
        retry:        'Refresh',
        close:        'Close',
        loading:      'Running cascade…',
        cascadeOrder: 'Cascade order used',
        flag:         'F83 flag',
        flagOn:       'ON',
        flagOff:      'OFF',
        scrapedo:     'Scrape.do',
        token:        'Token configured',
        breakerOpen:  'Open hosts',
        providers:    'Providers checked',
        winner:       'Winner',
        none:         'None',
        final:        'Final outcome',
        available:    'Available',
        notAvailable: 'Not available',
        stage:        'Stage',
        reason:       'Reason',
        retryable:    'Retryable',
        yes:          'Yes',
        no:           'No',
      }
    : {
        title:        'Diagnóstico de córners',
        subtitle:     'Traza proveedor por proveedor de la cascada.',
        retry:        'Reejecutar',
        close:        'Cerrar',
        loading:      'Ejecutando cascada…',
        cascadeOrder: 'Orden de cascada usado',
        flag:         'Flag F83',
        flagOn:       'ACTIVA',
        flagOff:      'INACTIVA',
        scrapedo:     'Scrape.do',
        token:        'Token configurado',
        breakerOpen:  'Hosts pausados',
        providers:    'Proveedores consultados',
        winner:       'Ganador',
        none:         'Ninguno',
        final:        'Resultado final',
        available:    'Disponible',
        notAvailable: 'No disponible',
        stage:        'Etapa',
        reason:       'Reason code',
        retryable:    'Reintentable',
        yes:          'Sí',
        no:           'No',
      };

  // Phase F83-update — fetch the debug payload. Returns a cancel
  // function that lets useEffect drop the in-flight response when the
  // dialog closes or the match_id changes.
  const fetchDebug = (mid) => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const url = `/football/corners/debug?match_id=${encodeURIComponent(mid)}`;
    api.get(url)
      .then((res) => {
        if (cancelled) return;
        setData(res?.data || res || null);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e?.response?.data?.detail || e?.message || 'Error');
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => { cancelled = true; };
  };

  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!open || !matchId) return undefined;
    // ``fetchDebug`` performs the network call and updates state via
    // setLoading/setData/setError when the response resolves. We
    // intentionally re-run only when the dialog (re)opens for a given
    // ``matchId`` — both deps are listed.
    return fetchDebug(matchId);
  }, [open, matchId]);
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  // Manual retry handler — bypasses the effect-guard rule.
  const onRetry = () => {
    if (matchId) fetchDebug(matchId);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-2xl"
        data-testid={`corners-debug-dialog-${matchId}`}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bug className="h-4 w-4 text-cyan-300" />
            {T.title}
          </DialogTitle>
          <p className="text-xs text-muted-foreground">{T.subtitle}</p>
        </DialogHeader>

        <div className="flex items-center justify-between pb-2">
          <code className="text-[11px] text-muted-foreground">match_id: {matchId || '—'}</code>
          <Button
            size="sm"
            variant="ghost"
            onClick={onRetry}
            disabled={loading}
            data-testid="corners-debug-refresh"
          >
            {loading
              ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
              : <RefreshCw className="h-3.5 w-3.5 mr-1" />}
            {T.retry}
          </Button>
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {T.loading}
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 text-xs text-rose-300/90 border border-rose-500/40 bg-rose-500/10 rounded p-2">
            <AlertCircle className="h-3.5 w-3.5 mt-0.5" />
            <span>{String(error)}</span>
          </div>
        )}

        {data && !loading && (
          <div className="space-y-3 text-xs">
            {/* Cascade order + flag */}
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted-foreground uppercase tracking-wider text-[10px]">
                {T.cascadeOrder}:
              </span>
              {(data.cascade_order_used || []).map((p, idx) => (
                <Badge
                  key={`order-${p}`}
                  className="text-[10px] bg-slate-500/15 text-slate-200 border border-slate-500/30"
                  data-testid={`corners-debug-order-${idx}`}
                >
                  {idx + 1}. {p}
                </Badge>
              ))}
              <Badge
                className={`text-[10px] ${data.flag_enabled
                  ? 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30'
                  : 'bg-slate-500/15 text-slate-300 border-slate-500/30'}`}
                data-testid="corners-debug-flag"
              >
                {T.flag}: {data.flag_enabled ? T.flagOn : T.flagOff}
              </Badge>
            </div>

            {/* Scrape.do health */}
            <div className="rounded border border-border/40 bg-background/40 p-2">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  {T.scrapedo}
                </span>
                <Badge
                  className={`text-[10px] ${data?.scrapedo?.enabled
                    ? 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30'
                    : 'bg-rose-500/15 text-rose-200 border-rose-500/30'}`}
                  data-testid="corners-debug-scrapedo-enabled"
                >
                  {T.token}: {data?.scrapedo?.enabled ? T.yes : T.no}
                </Badge>
              </div>
              <div className="flex flex-wrap gap-1 text-[11px]">
                <span className="text-muted-foreground">{T.breakerOpen}:</span>
                {(data?.scrapedo?.breaker_status?.open_hosts || []).length === 0 ? (
                  <span className="text-emerald-300/80">{T.none}</span>
                ) : (
                  data.scrapedo.breaker_status.open_hosts.map((h) => (
                    <Badge key={h} className="text-[10px] bg-rose-500/15 text-rose-200 border-rose-500/30">
                      {h}
                    </Badge>
                  ))
                )}
              </div>
            </div>

            {/* Providers checked */}
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {T.providers}
              </div>
              {(data.providers_checked || []).map((p, idx) => (
                <div
                  key={`prov-${idx}-${p.provider}`}
                  className={`rounded border p-2 ${p.available
                    ? 'border-emerald-500/40 bg-emerald-500/5'
                    : 'border-slate-700/40 bg-slate-900/30'}`}
                  data-testid={`corners-debug-provider-${p.provider}`}
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      {p.available
                        ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
                        : <X className="h-3.5 w-3.5 text-slate-400" />}
                      <span className="font-semibold text-slate-100">{p.provider}</span>
                      {p.transport && (
                        <span className="text-[10px] text-muted-foreground">
                          via {p.transport}
                        </span>
                      )}
                    </div>
                    <Badge className={`text-[10px] ${p.available
                      ? 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30'
                      : 'bg-slate-500/15 text-slate-300 border-slate-500/30'}`}>
                      {p.available ? T.available : T.notAvailable}
                    </Badge>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-1 text-[11px]">
                    {p.stage && (
                      <span><span className="text-muted-foreground">{T.stage}:</span> <span className="font-mono">{p.stage}</span></span>
                    )}
                    {p.reason_code && (
                      <span><span className="text-muted-foreground">{T.reason}:</span> <span className="font-mono">{p.reason_code}</span></span>
                    )}
                  </div>
                  {p.message_user && (
                    <p className="text-[11px] text-muted-foreground mt-1">{p.message_user}</p>
                  )}
                  {p.message_debug && (
                    <p className="text-[10px] text-slate-500 mt-0.5 font-mono whitespace-pre-wrap break-words">
                      {p.message_debug}
                    </p>
                  )}
                </div>
              ))}
            </div>

            {/* Final */}
            <div className="rounded border border-border/40 bg-background/40 p-2">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                {T.final}
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <Badge className={`text-[10px] ${data?.final?.available
                  ? 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30'
                  : 'bg-rose-500/20 text-rose-200 border-rose-500/30'}`}>
                  {data?.final?.available ? T.available : T.notAvailable}
                </Badge>
                {data?.winner?.provider && (
                  <span className="text-[11px]">
                    <span className="text-muted-foreground">{T.winner}:</span>{' '}
                    <span className="font-mono">{data.winner.provider}</span>
                  </span>
                )}
              </div>
              {data?.final?.message_user && (
                <p className="text-xs text-slate-200 mt-1">{data.final.message_user}</p>
              )}
              {data?.final?.message_debug && (
                <p className="text-[10px] text-slate-500 mt-1 font-mono whitespace-pre-wrap break-words">
                  {data.final.message_debug}
                </p>
              )}
            </div>
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onOpenChange?.(false)}
            data-testid="corners-debug-close"
          >
            {T.close}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default CornersDebugDialog;
