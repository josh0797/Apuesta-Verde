/**
 * Phase F85.5 — Public xG Enrichment Panel (FBref + Forebet).
 *
 * Renders next to the XGRecentAveragesPanel inside MatchIntelligencePanel.
 * Triggers POST /football/public-xg-enrichment/run-now and renders the
 * FBref L1/L5/L15 averages + Forebet context as soon as the response
 * comes back. Partial-render rules:
 *   * If FBref returns xG but Forebet fails → still show xG.
 *   * If Forebet works but FBref fails → still show Forebet context.
 *   * If both fail → show a clear empty state with the reason codes.
 *
 * Gating: only renders for football matches with a usable match_id and
 * either no xG yet OR the current snapshot is partial OR the operator
 * forced ``alwaysShow``.
 */
import { useMemo, useState, useCallback } from 'react';
import { Loader2, BarChart3, ExternalLink, AlertCircle, TrendingDown } from 'lucide-react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';

const T_ES = {
  title:         'xG público — FBref / Forebet',
  cta:           'Actualizar xG con FBref / Forebet',
  refresh:       'Refrescar',
  loading:       'Consultando FBref y Forebet…',
  noXG:          'FBref no entregó xG utilizable para este partido.',
  forebetDown:   'Forebet no disponible. El xG de FBref se conserva.',
  fbrefDown:     'FBref sin datos utilizables. Se conserva el contexto Forebet.',
  partialBadge:  'Muestra parcial — usar como contexto, no como confirmación robusta.',
  l1:            'Último partido',
  l5:            'Últimos 5',
  l15:           'Últimos 15',
  forLabel:      'xG a favor',
  agLabel:       'xG en contra',
  notAvail:      'no disponible',
  forebetHdr:    'Forebet (contexto, no xG)',
  predicted:     'Predicción',
  prob:          'Probabilidades',
  avgGoals:      'Promedio goles esperados',
  reading:       'Lectura',
  signalsHdr:    'Señales',
  reasonHdr:     'Motivo',
};
const T_EN = {
  title:         'Public xG — FBref / Forebet',
  cta:           'Refresh xG with FBref / Forebet',
  refresh:       'Refresh',
  loading:       'Fetching FBref & Forebet…',
  noXG:          "FBref didn't provide usable xG for this match.",
  forebetDown:   'Forebet unavailable. FBref xG is preserved.',
  fbrefDown:     'FBref unavailable. Forebet context is preserved.',
  partialBadge:  'Partial sample — treat as context, not as a strong confirmation.',
  l1:            'Last match',
  l5:            'Last 5',
  l15:           'Last 15',
  forLabel:      'xG for',
  agLabel:       'xG against',
  notAvail:      'not available',
  forebetHdr:    'Forebet (context only, not xG)',
  predicted:     'Prediction',
  prob:          'Probabilities',
  avgGoals:      'Expected average goals',
  reading:       'Reading',
  signalsHdr:    'Signals',
  reasonHdr:     'Reason',
};

function _val(side, win, key) {
  const w = side?.[win];
  if (!w || typeof w !== 'object') return null;
  const v = w[key];
  return v === undefined ? null : v;
}

function _fmt(v) {
  if (v === null || v === undefined) return null;
  if (typeof v !== 'number' || Number.isNaN(v)) return null;
  return v.toFixed(2);
}

function PublicXGSideBlock({ side, header, labels }) {
  if (!side) return null;
  const rows = [
    { key: 'l1',  label: labels.l1  },
    { key: 'l5',  label: labels.l5  },
    { key: 'l15', label: labels.l15 },
  ];
  return (
    <div
      className="flex-1 rounded-md border border-border/40 p-2"
      data-testid={`public-xg-side-${header}`}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
        {header} · {side.team || '—'}
      </div>
      <div className="space-y-1.5">
        {rows.map((r) => {
          const xgFor = _fmt(_val(side, r.key, 'xg_for_avg'));
          const xgAg  = _fmt(_val(side, r.key, 'xg_against_avg'));
          if (xgFor === null && xgAg === null) {
            return (
              <div key={r.key} className="text-[11px] text-muted-foreground">
                {r.label}: {labels.notAvail}
              </div>
            );
          }
          return (
            <div key={r.key} className="text-[11px]">
              <div className="font-medium text-foreground/90">{r.label}</div>
              <div className="flex gap-3 text-foreground/80">
                <span data-testid={`public-xg-${header}-${r.key}-for`}>
                  {labels.forLabel}: {xgFor ?? labels.notAvail}
                </span>
                <span data-testid={`public-xg-${header}-${r.key}-against`}>
                  {labels.agLabel}: {xgAg ?? labels.notAvail}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ForebetBlock({ forebet, labels }) {
  if (!forebet || forebet.available === false) return null;
  const prob = forebet.probabilities || {};
  const ctx  = forebet.goals_context || {};
  return (
    <div
      className="rounded-md border border-border/40 p-2 mt-2"
      data-testid="public-xg-forebet"
    >
      <div className="text-[11px] font-medium text-foreground/90 flex items-center gap-1">
        <ExternalLink className="h-3 w-3" /> {labels.forebetHdr}
      </div>
      <div className="text-[11px] mt-1 grid grid-cols-1 gap-0.5">
        {forebet.predicted_score && (
          <div>
            <span className="text-muted-foreground">{labels.predicted}: </span>
            <span data-testid="public-xg-forebet-score">{forebet.predicted_score}</span>
          </div>
        )}
        {(prob.home != null || prob.draw != null || prob.away != null) && (
          <div data-testid="public-xg-forebet-prob">
            <span className="text-muted-foreground">{labels.prob}: </span>
            {(prob.home ?? '—')}% / {(prob.draw ?? '—')}% / {(prob.away ?? '—')}%
          </div>
        )}
        {ctx.avg_goals_hint != null && (
          <div>
            <span className="text-muted-foreground">{labels.avgGoals}: </span>
            {ctx.avg_goals_hint}
          </div>
        )}
        {forebet.raw_text_summary && (
          <div className="text-muted-foreground italic mt-1">
            {forebet.raw_text_summary}
          </div>
        )}
      </div>
    </div>
  );
}

function SignalsList({ payload, labels }) {
  const signals = payload?.signals || [];
  if (!signals.length) return null;
  const explanations = payload?.explanations || {};
  return (
    <div className="mt-2" data-testid="public-xg-signals">
      <div className="text-[10.5px] font-medium text-foreground/90 mb-1">
        {labels.signalsHdr}
      </div>
      <ul className="space-y-0.5 list-disc list-inside text-[10.5px] text-foreground/75">
        {signals.map((s) => (
          <li key={s}>
            <span className="font-mono text-[10px] text-amber-300/90 mr-1">{s}</span>
            {explanations[s] && <span>— {explanations[s]}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PublicXGPanel({ match, lang = 'es', forebetUrl = null }) {
  const T = lang === 'en' ? T_EN : T_ES;
  const sport   = match?.sport;
  const matchId = useMemo(() => {
    const raw = match?.match_id ?? match?.id ?? null;
    if (raw == null) return null;
    const s = String(raw).trim();
    return s && s !== 'undefined' && s !== 'null' ? s : null;
  }, [match]);

  const initialSnap = (
    match?.xg_public_enrichment
    ?? match?.football_data_enrichment?.xg_public_enrichment
    ?? null
  );

  const [snap, setSnap] = useState(initialSnap);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const xg      = snap?.xg_recent_averages || snap?.xg_recent_averages_snapshot || null;
  const forebet = snap?.forebet_context    || null;
  const xgUsable = !!(xg && xg.available);
  const fbUsable = !!(forebet && forebet.available);
  const partial  = !!(xg && xg.partial);

  const handleClick = useCallback(async () => {
    if (!matchId || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.post(
        '/football/public-xg-enrichment/run-now',
        {
          match_id:    matchId,
          forebet_url: forebetUrl || undefined,
          force:       true,
        },
      );
      const data = res?.data || {};
      if (data.status === 'TIMEOUT') {
        setError(data.message || 'timeout');
      }
      setSnap(data.xg_public_enrichment ? data : data);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [matchId, loading, forebetUrl]);

  if (sport !== 'football' || !matchId) return null;

  return (
    <div
      className="rounded-lg border border-border/50 bg-card/50 p-3 mt-3 space-y-2"
      data-testid={`public-xg-${matchId}`}
    >
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-medium text-foreground/90 flex items-center gap-1.5">
          <BarChart3 className="h-3.5 w-3.5" /> {T.title}
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[11px]"
          disabled={loading}
          onClick={handleClick}
          data-testid={`public-xg-btn-${matchId}`}
        >
          {loading ? (
            <span className="inline-flex items-center gap-1">
              <Loader2 className="h-3 w-3 animate-spin" /> {T.loading}
            </span>
          ) : (
            snap ? T.refresh : T.cta
          )}
        </Button>
      </div>

      {error && (
        <div
          className="text-[11px] text-rose-400/90 flex items-start gap-1"
          data-testid={`public-xg-error-${matchId}`}
        >
          <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {snap && (
        <div
          className="space-y-2 pt-1 border-t border-border/40"
          data-testid="public-xg-content"
        >
          {partial && (
            <div
              className="text-[10.5px] text-amber-300/80 flex items-center gap-1"
              data-testid="public-xg-partial-badge"
            >
              <TrendingDown className="h-3 w-3" /> {T.partialBadge}
            </div>
          )}

          {xgUsable ? (
            <div className="flex gap-2" data-testid="public-xg-grid">
              <PublicXGSideBlock side={xg.home} header="H" labels={T} />
              <PublicXGSideBlock side={xg.away} header="A" labels={T} />
            </div>
          ) : (
            <div
              className="text-[11px] text-muted-foreground italic"
              data-testid="public-xg-empty"
            >
              {T.noXG}
              {Array.isArray(xg?.reason_codes) && xg.reason_codes.length > 0 && (
                <div className="font-mono text-[10px] mt-0.5">
                  {T.reasonHdr}: {xg.reason_codes.join(', ')}
                </div>
              )}
            </div>
          )}

          {fbUsable ? (
            <ForebetBlock forebet={forebet} labels={T} />
          ) : (
            forebet && (
              <div
                className="text-[10.5px] text-muted-foreground italic"
                data-testid="public-xg-forebet-down"
              >
                {T.forebetDown}
              </div>
            )
          )}

          <SignalsList payload={snap} labels={T} />
        </div>
      )}
    </div>
  );
}

export default PublicXGPanel;
