import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Info } from 'lucide-react';

/**
 * FootballTotalSignalPanel — Sprint D10-C.2
 *
 * Renderiza el contrato unificado del endpoint
 * POST /api/football/manual-odds/preview. Espera el payload completo
 * `{ status, signal, valuation, observe_only }`.
 *
 * Vista resumida (cuando colapsado): selección · modelo % · cuota
 * justa · cuota ingresada · EV · contexto · score.
 *
 * Vista expandida: proyección base/ajustada, distancia a la línea,
 * H2H ponderado, forma reciente, xG contextual, muestra efectiva,
 * confiabilidad, CV, P(win/push/loss), desglose del score.
 *
 * La UI no recalcula matemáticas. Sólo renderiza.
 */

function fmtPct(v, digits = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

function fmtNum(v, digits = 2) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(digits);
}

function fmtSigned(v, digits = 1) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}`;
}

function valueClassTone(vc) {
  switch (vc) {
    case 'HIGH_EDGE_REVIEW_REQUIRED':
      return 'bg-amber-500/15 text-amber-200 border-amber-500/40';
    case 'GOOD_VALUE':
      return 'bg-emerald-500/15 text-emerald-200 border-emerald-500/40';
    case 'MILD_VALUE':
      return 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30';
    case 'FAIR':
      return 'bg-slate-500/15 text-slate-200 border-slate-500/30';
    case 'NEGATIVE_VALUE':
      return 'bg-rose-500/10 text-rose-200 border-rose-500/30';
    case 'STRONG_NEGATIVE_VALUE':
      return 'bg-rose-500/20 text-rose-100 border-rose-500/45';
    default:
      return 'bg-slate-500/10 text-slate-300 border-slate-500/30';
  }
}

function supportTone(sc) {
  switch (sc) {
    case 'STRONG_SUPPORT': return 'text-emerald-200';
    case 'MILD_SUPPORT':   return 'text-emerald-300';
    case 'STRONG_CONFLICT': return 'text-rose-200';
    case 'MILD_CONFLICT':   return 'text-rose-300';
    default:               return 'text-slate-300';
  }
}

const VARIABILITY_LABEL = {
  STABLE:   'Estable',
  MODERATE: 'Moderada',
  VOLATILE: 'Volátil',
  INSUFFICIENT_SAMPLE: 'Muestra insuficiente',
};

const STATUS_LABEL = {
  FOOTBALL_REPRICED:           'Reprice listo',
  FOOTBALL_TOTAL_SIGNAL_READY: 'Señal lista — falta cuota',
  FOOTBALL_BASE_MODEL_ONLY:    'Modelo base — sin contexto suficiente',
  FOOTBALL_MARKET_LINE_MISSING: 'Falta la línea',
  FOOTBALL_INVALID_INPUTS:     'Inputs inválidos',
};

export function FootballTotalSignalPanel({ payload, testId = 'football-total-signal' }) {
  const [expanded, setExpanded] = useState(false);

  const sig = payload?.signal || {};
  const val = payload?.valuation || {};
  const probs = val.probabilities || {};
  const valuation = val.valuation || {};
  const context = val.context || {};
  const market = val.market || {};
  const asianSplit = val.asian_split || null;

  const view = useMemo(() => {
    return {
      status: payload?.status || 'UNKNOWN',
      base: sig.base || {},
      ctxSources: sig.context_sources || {},
      sample: sig.sample || {},
      adjustment: sig.adjustment || {},
      variability: sig.variability || {},
      marketCtx: sig.market_context || {},
      reasonCodes: sig.reason_codes || [],
    };
  }, [payload, sig]);

  if (!payload) return null;
  const statusLabel = STATUS_LABEL[view.status] || view.status;
  const isReady = view.status === 'FOOTBALL_REPRICED'
    || view.status === 'FOOTBALL_TOTAL_SIGNAL_READY';

  const score = Number(view.marketCtx.influence_score ?? 0);
  const scoreText = fmtSigned(score, 1);
  const supportClass = context.support_class || 'NEUTRAL';
  const decision = val.decision || 'UNKNOWN';

  return (
    <div
      className="rounded-md border border-emerald-500/30 bg-emerald-500/5 text-emerald-100/95 px-2.5 py-1.5 text-[11.5px] leading-snug space-y-1"
      data-testid={testId}
      data-football-signal-status={view.status}
    >
      <button
        type="button"
        className="w-full flex items-center gap-1.5 font-semibold text-left hover:opacity-90 transition-opacity"
        onClick={() => setExpanded((e) => !e)}
        data-testid={`${testId}-toggle`}
      >
        <span aria-hidden>⚽</span>
        <span>Football Total Signal</span>
        <span
          className={`ml-auto inline-block px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide border ${valueClassTone(valuation.value_class)}`}
          data-testid={`${testId}-status-badge`}
        >
          {statusLabel}
        </span>
        {expanded
          ? <ChevronUp className="w-3.5 h-3.5 opacity-80" />
          : <ChevronDown className="w-3.5 h-3.5 opacity-80" />}
      </button>

      {/* Vista resumida */}
      {isReady && (
        <div className="text-[10.5px] grid grid-cols-2 gap-x-3 gap-y-0.5" data-testid={`${testId}-summary`}>
          {market.selection && market.line != null && (
            <span>
              {market.selection} {fmtNum(market.line, market.line_class === '.0' || market.line_class === '.5' ? 1 : 2)}
            </span>
          )}
          {probs.win != null && (
            <span>
              Modelo: <span className="font-semibold tabular-nums">{fmtPct(probs.win)}</span>
            </span>
          )}
          {valuation.fair_odds != null && (
            <span>
              Cuota justa: <span className="font-semibold tabular-nums">{fmtNum(valuation.fair_odds)}</span>
            </span>
          )}
          {market.decimal_odds != null && (
            <span>
              Cuota: <span className="font-semibold tabular-nums">{fmtNum(market.decimal_odds)}</span>
            </span>
          )}
          {valuation.ev_percentage != null && (
            <span
              className={`font-semibold ${valuation.ev_percentage > 0 ? 'text-emerald-200' : 'text-rose-200'}`}
              data-testid={`${testId}-ev`}
            >
              EV: <span className="tabular-nums">{fmtSigned(valuation.ev_percentage)}%</span>
            </span>
          )}
          <span className={`${supportTone(supportClass)}`} data-testid={`${testId}-support`}>
            Score: <span className="font-semibold tabular-nums">{scoreText}/10</span>
          </span>
        </div>
      )}

      {/* Vista expandida */}
      {expanded && (
        <div className="text-[10.5px] mt-1 space-y-1.5 pt-1 border-t border-current/15" data-testid={`${testId}-expanded`}>
          {/* Proyecciones */}
          <div className="space-y-0.5">
            <div>Proyección base: <span className="font-semibold tabular-nums">{fmtNum(view.base.expected_goals)}</span></div>
            <div>Proyección ajustada: <span className="font-semibold tabular-nums">{fmtNum(view.adjustment.adjusted_expected_goals)}</span></div>
            {view.marketCtx.total_edge_goals != null && (
              <div>
                Distancia a la línea:{' '}
                <span className={`font-semibold tabular-nums ${
                  view.marketCtx.total_edge_goals > 0 ? 'text-emerald-200' : 'text-rose-200'
                }`}>
                  {fmtSigned(view.marketCtx.total_edge_goals, 2)} goles
                </span>
              </div>
            )}
          </div>

          {/* Fuentes contextuales */}
          <div className="space-y-0.5">
            <div className="opacity-80 uppercase tracking-wide text-[9.5px] font-semibold">Fuentes contextuales</div>
            {view.ctxSources.weighted_h2h_goals != null && (
              <div>H2H ponderado: <span className="font-semibold tabular-nums">{fmtNum(view.ctxSources.weighted_h2h_goals)}</span></div>
            )}
            {view.ctxSources.weighted_recent_goals != null && (
              <div>Forma reciente: <span className="font-semibold tabular-nums">{fmtNum(view.ctxSources.weighted_recent_goals)}</span></div>
            )}
            {view.ctxSources.weighted_xg_total != null ? (
              <div>xG contextual: <span className="font-semibold tabular-nums">{fmtNum(view.ctxSources.weighted_xg_total)}</span></div>
            ) : (
              <div className="opacity-70 italic">xG no disponible</div>
            )}
          </div>

          {/* Muestra & confiabilidad */}
          <div className="space-y-0.5">
            <div>Muestra efectiva: <span className="font-semibold tabular-nums">{fmtNum(view.sample.effective_n)}</span></div>
            <div>Confiabilidad: <span className="font-semibold tabular-nums">{fmtPct(view.sample.reliability)}</span></div>
            {view.variability.cv != null && (
              <div>
                CV: <span className="font-semibold tabular-nums">{fmtNum(view.variability.cv, 2)}</span>
                {' · '}
                <span className="opacity-90">{VARIABILITY_LABEL[view.variability.class] || view.variability.class}</span>
              </div>
            )}
          </div>

          {/* Probabilidades */}
          {(probs.win != null || probs.push != null || probs.loss != null) && (
            <div className="space-y-0.5" data-testid={`${testId}-probs`}>
              <div className="opacity-80 uppercase tracking-wide text-[9.5px] font-semibold">Probabilidades</div>
              <div>P(win): <span className="font-semibold tabular-nums">{fmtPct(probs.win)}</span></div>
              <div>P(push): <span className="font-semibold tabular-nums">{fmtPct(probs.push)}</span></div>
              <div>P(loss): <span className="font-semibold tabular-nums">{fmtPct(probs.loss)}</span></div>
            </div>
          )}

          {/* Asian split (.25/.75) */}
          {Array.isArray(asianSplit) && asianSplit.length > 0 && (
            <div className="space-y-0.5" data-testid={`${testId}-asian-split`}>
              <div className="opacity-80 uppercase tracking-wide text-[9.5px] font-semibold">Asian split (50/50)</div>
              {asianSplit.map((leg) => (
                <div key={leg.line} className="flex items-center gap-1.5">
                  <span>Línea {fmtNum(leg.line, 1)}:</span>
                  <span className="opacity-90 tabular-nums">
                    win {fmtPct(leg.win)} · push {fmtPct(leg.push)} · loss {fmtPct(leg.loss)}
                  </span>
                  {leg.ev_percentage != null && (
                    <span className={`ml-auto font-semibold tabular-nums ${
                      leg.ev_percentage > 0 ? 'text-emerald-200' : 'text-rose-200'
                    }`}>
                      EV {fmtSigned(leg.ev_percentage)}%
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Decision tag */}
          {decision && decision !== 'UNKNOWN' && (
            <div className={`text-[10.5px] flex items-center gap-1.5 ${supportTone(supportClass)}`}>
              <Info className="w-3 h-3" />
              <span className="font-semibold uppercase tracking-wide">{decision.replace(/_/g, ' ')}</span>
            </div>
          )}

          {/* Reason codes */}
          {view.reasonCodes.length > 0 && (
            <div className="text-[10px] opacity-80 leading-tight" data-testid={`${testId}-reason-codes`}>
              <span className="font-semibold opacity-90">Reason codes: </span>
              {view.reasonCodes.join(' · ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default FootballTotalSignalPanel;
