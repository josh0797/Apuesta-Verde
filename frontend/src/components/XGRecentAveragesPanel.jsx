import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Activity, AlertCircle, CheckCircle2, Loader2, RefreshCw, TrendingDown, TrendingUp } from 'lucide-react';
import { api } from '@/lib/api';

/**
 * Phase F83.2-E4 — TheStatsAPI / xG recent averages panel.
 *
 * Renders the L1/L5/L15 non-penalty xG averages for both teams when
 * present on the match doc, plus a manual refresh button that triggers
 * the shotmap aggregation via /api/football/xg-recent-averages/run-now.
 *
 * Designed to coexist with CornersEnrichmentButton: gated by
 * ``sport === 'football'`` and only shown when there is data to render
 * OR the operator may want to fetch it.
 */
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TICKS   = 20;

const _fmtXg = (v) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2));

function XGWindowRow({ label, w, labels }) {
  if (!w) {
    return (
      <div className="text-[11px] text-muted-foreground">
        <span className="font-semibold text-foreground/80">{label}:</span>{' '}
        <span className="italic">{labels.notAvail}</span>
      </div>
    );
  }
  return (
    <div className="text-[11px]">
      <span className="font-semibold text-foreground/80">{label}</span>{' '}
      <span className="text-muted-foreground">({labels.forShort}/{labels.agShort}):</span>{' '}
      <span className="font-mono-tabular text-emerald-300">{_fmtXg(w.xg_for_avg)}</span>
      <span className="text-muted-foreground"> / </span>
      <span className="font-mono-tabular text-rose-300">{_fmtXg(w.xg_against_avg)}</span>
    </div>
  );
}

function XGSideBlock({ side, header, labels }) {
  return (
    <div className="space-y-1" data-testid={`xg-side-${header}`}>
      <div className="text-[10.5px] uppercase tracking-wider text-muted-foreground">
        {header}: <span className="font-semibold text-foreground/90">{side?.team || '—'}</span>
      </div>
      <XGWindowRow label={labels.l1}  w={side?.l1}  labels={labels} />
      <XGWindowRow label={labels.l5}  w={side?.l5}  labels={labels} />
      <XGWindowRow label={labels.l15} w={side?.l15} labels={labels} />
    </div>
  );
}

export function XGRecentAveragesPanel({ match, lang = 'es' }) {
  const matchId  = match?.match_id;
  const sport    = match?.sport || 'football';
  const initial  = match?.xg_recent_averages
                || match?.football_data_enrichment?.xg_recent_averages
                || null;

  const [snapshot, setSnapshot] = useState(initial);
  const [phase,    setPhase]    = useState('idle');   // idle|loading|polling|done|error
  const [error,    setError]    = useState(null);

  const T = lang === 'en'
    ? {
        title:    'TheStatsAPI / xG recent',
        cta:      'Fetch xG L1/L5/L15',
        refresh:  'Refresh',
        loading:  'Fetching shotmaps…',
        polling:  'Running in background…',
        empty:    'No usable xG data for this match.',
        errPref:  'xG fetch failed',
        l1:       'Last match',
        l5:       'Last 5',
        l15:      'Last 15',
        forShort: 'xG for',
        agShort:  'xG against',
        notAvail: 'not available',
        sigsHdr:  'Signals',
        partialBadge: 'Partial sample — some ranges unavailable.',
        l1OnlyBadge:  'Only L1 available — treat as context, not as Over/Under confirmation.',
      }
    : {
        title:    'TheStatsAPI / xG reciente',
        cta:      'Calcular xG L1/L5/L15',
        refresh:  'Refrescar',
        loading:  'Consultando shotmaps…',
        polling:  'Ejecutando en segundo plano…',
        empty:    'xG reciente no disponible en TheStatsAPI para este partido.',
        errPref:  'Falló la carga de xG',
        l1:       'Último partido',
        l5:       'Últimos 5',
        l15:      'Últimos 15',
        forShort: 'xG a favor',
        agShort:  'xG en contra',
        notAvail: 'no disponible',
        sigsHdr:  'Señales',
        partialBadge: 'Muestra parcial — algunos rangos no están disponibles.',
        l1OnlyBadge:  'Solo L1 disponible — úsalo como contexto, no como confirmación de Over/Under.',
      };

  if (sport !== 'football' || !matchId) return null;

  const handleSuccess = (data) => { setSnapshot(data); setPhase('done'); setError(null); };
  const handleErr = (msg) => { setPhase('error'); setError(msg || T.empty); };

  const trigger = async () => {
    if (phase === 'loading' || phase === 'polling') return;
    setPhase('loading'); setError(null);
    try {
      const res = await api.post('/football/xg-recent-averages/run-now',
                                  { match_id: String(matchId) });
      const data = res?.data || res || {};
      if (data.status === 'SUCCESS') handleSuccess(data);
      else if (data.status === 'TIMEOUT') handleErr(T.errPref + ' (timeout)');
      else handleErr(data.reason_codes?.[0] || T.empty);
    } catch (e) {
      handleErr(e?.response?.data?.detail || e?.message || T.errPref);
    }
  };

  const hasSnap = snapshot && snapshot.available === true;
  const busy = phase === 'loading' || phase === 'polling';

  return (
    <div
      className="rounded-lg border border-border/60 bg-background/30 p-3 flex flex-col gap-2"
      data-testid={`xg-recent-${matchId}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11.5px] uppercase tracking-wider text-muted-foreground">
          <Activity className="h-3.5 w-3.5" />
          <span>{T.title}</span>
        </div>
        <Button
          size="sm"
          variant={hasSnap ? 'ghost' : 'secondary'}
          onClick={trigger}
          disabled={busy}
          data-testid={`xg-recent-btn-${matchId}`}
          className="text-[11.5px] h-7 px-2"
        >
          {busy
            ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
            : hasSnap
              ? <RefreshCw className="h-3.5 w-3.5 mr-1" />
              : null}
          {phase === 'loading' ? T.loading
            : phase === 'polling' ? T.polling
            : hasSnap            ? T.refresh
            :                      T.cta}
        </Button>
      </div>

      {phase === 'error' && (
        <div className="flex items-start gap-1.5 text-[11.5px] text-rose-300/90" data-testid={`xg-recent-error-${matchId}`}>
          <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
          <span>{T.errPref}: {String(error || T.empty)}</span>
        </div>
      )}

      {hasSnap && (
        <div data-testid="xg-recent-content" className="space-y-2 pt-1 border-t border-border/40">
          {snapshot.partial && (
            <div
              className="text-[10.5px] text-amber-300/80 flex items-center gap-1"
              data-testid="xg-recent-partial-badge"
            >
              <TrendingDown className="h-3 w-3" /> {T.partialBadge}
            </div>
          )}
          {Array.isArray(snapshot.signals) && snapshot.signals.includes('XG_L1_ONLY') && (
            <div
              className="text-[10.5px] text-amber-300/90 flex items-center gap-1"
              data-testid="xg-recent-l1-only-badge"
            >
              <AlertCircle className="h-3 w-3" /> {T.l1OnlyBadge}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <XGSideBlock side={snapshot.home} header="H" labels={T} />
            <XGSideBlock side={snapshot.away} header="A" labels={T} />
          </div>
          {Array.isArray(snapshot.signals) && snapshot.signals.length > 0 && (
            <div className="pt-1 border-t border-border/40 space-y-0.5" data-testid="xg-recent-signals">
              <div className="text-[10.5px] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                <TrendingUp className="h-3 w-3" /> {T.sigsHdr}
              </div>
              {snapshot.signals.map((code) => (
                <div key={code} className="text-[11px] text-foreground/85 flex items-start gap-1.5">
                  <CheckCircle2 className="h-3 w-3 text-emerald-400/80 mt-0.5 shrink-0" />
                  <span>
                    <span className="font-mono-tabular text-[10.5px] text-foreground/70">{code}</span>
                    {snapshot.explanations?.[code] && (
                      <> — <span className="text-muted-foreground">{snapshot.explanations[code]}</span></>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default XGRecentAveragesPanel;
