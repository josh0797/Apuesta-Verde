import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Loader2, AlertTriangle, TrendingUp } from 'lucide-react';

/**
 * Phase F86.2 — xG block (consumer of `editorial.xg_block`).
 *
 * Honours the PENDING/SUCCESS/TIMEOUT/UNAVAILABLE status stamped by the
 * background dispatcher in `data_ingestion._schedule_xg_recent_background`
 * (F87) and renders the 2×3 L1/L5/L15 grid when ready.
 */
const fmt = (v) => (typeof v === 'number' ? v.toFixed(2) : '—');

export const XGBlockPanel = ({ block, testIdPrefix = 'xg-block' }) => {
  if (!block || typeof block !== 'object') return null;
  const status = String(block.status || 'UNAVAILABLE').toUpperCase();

  if (status === 'PENDING') {
    return (
      <Card
        className="border-slate-700/60 bg-slate-900/40"
        data-testid={`${testIdPrefix}-pending`}
      >
        <CardContent className="py-3 px-4 flex items-center gap-2 text-xs text-slate-300">
          <Loader2 className="h-4 w-4 text-cyan-300 animate-spin" />
          <div>
            <div className="font-semibold text-slate-200">xG reciente (L1/L5/L15)</div>
            <p className="text-slate-400 mt-0.5">
              {block.missing_reason || 'Calculando xG reciente...'}
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (status === 'UNAVAILABLE' || status === 'TIMEOUT') {
    return (
      <Card
        className="border-amber-500/30 bg-amber-500/5"
        data-testid={`${testIdPrefix}-unavailable`}
      >
        <CardContent className="py-3 px-4 flex items-start gap-2 text-xs text-amber-100">
          <AlertTriangle className="h-4 w-4 text-amber-300 flex-shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-amber-200">xG reciente no disponible</div>
            <p className="text-amber-100/80 mt-0.5">
              {block.missing_reason || 'xG no disponible para este partido.'}
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // SUCCESS path — render the 2×3 table + signals.
  const home = block.home || {};
  const away = block.away || {};
  const signals = Array.isArray(block.signals) ? block.signals : [];
  const explanations = (block.explanations && typeof block.explanations === 'object')
    ? block.explanations : {};

  const renderRow = (label, side) => (
    <tr data-testid={`${testIdPrefix}-row-${label.toLowerCase()}`}>
      <th
        scope="row"
        className="text-left px-2 py-1 text-[11px] font-semibold text-slate-300"
      >
        {label}
      </th>
      {['l1', 'l5', 'l15'].map((w) => {
        const cell = side?.[w];
        return (
          <td
            key={`${label}-${w}`}
            className="px-2 py-1 text-[11px] text-slate-200 font-mono text-center"
            data-testid={`${testIdPrefix}-${label.toLowerCase()}-${w}`}
          >
            {cell ? (
              <div className="flex flex-col leading-tight">
                <span>{fmt(cell.xg_for)} <span className="text-slate-500">/</span> {fmt(cell.xg_against)}</span>
                {typeof cell.sample === 'number' && (
                  <span className="text-[9px] text-slate-500">n={cell.sample}</span>
                )}
              </div>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        );
      })}
    </tr>
  );

  return (
    <Card
      className="border-slate-700/60 bg-slate-900/40"
      data-testid={`${testIdPrefix}-success`}
    >
      <CardContent className="py-3 px-4 space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-cyan-300" />
            <span className="text-sm font-semibold text-slate-200">
              xG reciente (xG · xG against)
            </span>
          </div>
          {block.partial && (
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-200 border border-amber-500/30">
              Muestra parcial
            </span>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid={`${testIdPrefix}-grid`}>
            <thead>
              <tr className="text-[10px] text-slate-500">
                <th className="text-left px-2 py-1" scope="col">Equipo</th>
                <th className="px-2 py-1" scope="col">L1</th>
                <th className="px-2 py-1" scope="col">L5</th>
                <th className="px-2 py-1" scope="col">L15</th>
              </tr>
            </thead>
            <tbody>
              {renderRow(home.team || 'Home', home)}
              {renderRow(away.team || 'Away', away)}
            </tbody>
          </table>
        </div>

        {signals.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1 border-t border-slate-700/60">
            {signals.slice(0, 6).map((sig) => (
              <span
                key={sig}
                title={explanations[sig] || sig}
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-cyan-500/15 text-cyan-100 border border-cyan-500/30"
                data-testid={`${testIdPrefix}-signal-${sig}`}
              >
                {sig}
              </span>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};


export default XGBlockPanel;
