import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { History, AlertTriangle, Info, CheckCircle2 } from 'lucide-react';

/**
 * Phase F86.2 — H2H block (consumer of `editorial.h2h_block`).
 *
 * Replaces the legacy "✓ H2H reciente cargado" check with:
 *  - matches_detail rendered one-by-one (always).
 *  - decision_useful=False → warning banner + "Solo contexto" badge.
 *  - decision_useful=True  → narrative + rates table + applied_signals
 *    rendered as green chips ("+4 OVER_2_5"…).
 */
export const H2HBlockPanel = ({ block, testIdPrefix = 'h2h-block' }) => {
  if (!block || typeof block !== 'object') return null;
  const matches = Array.isArray(block.matches_detail) ? block.matches_detail : [];
  const warnings = Array.isArray(block.warnings) ? block.warnings : [];
  const decisionUseful = block.decision_useful === true;
  const appliedSignals = Array.isArray(block.applied_signals) ? block.applied_signals : [];
  const pointsByMarket = (block.points_by_market && typeof block.points_by_market === 'object')
    ? block.points_by_market : {};
  const rates = (block.rates && typeof block.rates === 'object') ? block.rates : {};

  if (matches.length === 0 && (block.sample_size_total ?? 0) === 0) {
    return (
      <Card
        className="border-slate-700/60 bg-slate-900/40"
        data-testid={`${testIdPrefix}-empty`}
      >
        <CardContent className="py-3 px-4 text-xs text-slate-400 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-300 flex-shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-slate-200">Enfrentamientos directos</div>
            <p className="mt-0.5">{block.narrative || 'Sin enfrentamientos directos previos registrados — sin contexto H2H.'}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const fmtPct = (r) => {
    if (typeof r !== 'number') return null;
    return `${Math.round(r * 100)}%`;
  };

  return (
    <Card
      className="border-slate-700/60 bg-slate-900/40"
      data-testid={`${testIdPrefix}-card`}
    >
      <CardContent className="py-3 px-4 space-y-2">
        {/* Header */}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-slate-300" />
            <span className="text-sm font-semibold text-slate-200">
              Enfrentamientos directos
            </span>
            <span className="text-xs text-slate-500">
              ({block.sample_size_total || matches.length}{' '}
              {((block.sample_size_total || matches.length) === 1) ? 'partido' : 'partidos'},{' '}
              {block.sample_size_recent || 0} en último año)
            </span>
          </div>
          {decisionUseful ? (
            <Badge
              className="text-[10px] uppercase tracking-wider bg-emerald-500/20 text-emerald-200 border-emerald-500/30"
              data-testid={`${testIdPrefix}-badge-decisivo`}
            >
              Decisivo
            </Badge>
          ) : (
            <Badge
              className="text-[10px] uppercase tracking-wider bg-amber-500/20 text-amber-200 border-amber-500/30"
              data-testid={`${testIdPrefix}-badge-context`}
            >
              Solo contexto
            </Badge>
          )}
        </div>

        {/* Narrative */}
        {block.narrative && (
          <p
            className="text-xs leading-relaxed text-slate-200"
            data-testid={`${testIdPrefix}-narrative`}
          >
            {block.narrative}
          </p>
        )}

        {/* Warnings */}
        {warnings.length > 0 && (
          <div
            className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5"
            data-testid={`${testIdPrefix}-warning`}
          >
            <AlertTriangle className="h-3.5 w-3.5 text-amber-300 mt-0.5 flex-shrink-0" />
            <p className="text-[11px] text-amber-100 leading-snug">{warnings[0]}</p>
          </div>
        )}

        {/* Applied signals chips (decision_useful=True only) */}
        {decisionUseful && appliedSignals.length > 0 && (
          <div className="flex flex-wrap gap-1" data-testid={`${testIdPrefix}-applied-signals`}>
            {appliedSignals.map((sig, i) => {
              // Find the matching market_key + points for chip text.
              const marketEntry = Object.entries(pointsByMarket).find(
                ([k]) => sig.replace(/_/g, '').includes(k.replace(/_/g, '')),
              );
              const points = marketEntry ? marketEntry[1] : null;
              const marketKey = marketEntry ? marketEntry[0] : null;
              return (
                <span
                  key={`h2h-sig-${i}`}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-100 border border-emerald-500/40"
                  data-testid={`${testIdPrefix}-chip-${i}`}
                >
                  <CheckCircle2 className="h-3 w-3" />
                  {marketKey ? `+${points} ${marketKey}` : sig}
                </span>
              );
            })}
          </div>
        )}

        {/* Rates table (decision_useful=True only) */}
        {decisionUseful && Object.keys(rates).length > 0 && (
          <div
            className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400 border-t border-slate-700/60 pt-1.5"
            data-testid={`${testIdPrefix}-rates`}
          >
            {['over_2_5', 'btts_yes', 'btts_no', 'home_dnb', 'away_dnb'].map((k) => {
              const v = rates[k];
              if (typeof v !== 'number') return null;
              return (
                <span key={k}>
                  <span className="text-slate-500 mr-1">{k.replace(/_/g, ' ')}:</span>
                  <span className="text-slate-200 font-mono">{fmtPct(v)}</span>
                </span>
              );
            })}
          </div>
        )}

        {/* Matches detail — always rendered, collapsible when ≥ 4 for decisive */}
        <MatchesDetailList
          matches={matches}
          testIdPrefix={`${testIdPrefix}-matches`}
          collapsible={decisionUseful && matches.length > 3}
        />
      </CardContent>
    </Card>
  );
};


function MatchesDetailList({ matches, testIdPrefix, collapsible = false }) {
  const items = matches.map((m, idx) => {
    const result = m.result_for_home;
    const badgeCls = result === 'W'
      ? 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30'
      : result === 'L'
        ? 'bg-rose-500/20 text-rose-200 border-rose-500/30'
        : result === 'D'
          ? 'bg-slate-500/20 text-slate-300 border-slate-500/30'
          : null;
    return (
      <li
        key={`h2h-m-${idx}-${m.date}`}
        className="flex items-center justify-between gap-2 text-xs text-slate-300"
        data-testid={`${testIdPrefix}-item-${idx}`}
      >
        <div className="flex items-center gap-2 flex-1">
          <span className="text-slate-500 font-mono text-[11px]">
            📅 {m.date || '—'}
          </span>
          <span className="text-slate-200">
            {m.home || '—'} <span className="font-mono text-slate-100">{m.score || '—'}</span> {m.away || '—'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {!m.is_recent && (
            <Badge className="text-[9px] bg-slate-700/60 text-slate-400 border border-slate-700/60">
              Histórico
            </Badge>
          )}
          {result && badgeCls && (
            <Badge
              className={`text-[9px] uppercase font-mono border ${badgeCls}`}
              data-testid={`${testIdPrefix}-item-${idx}-result`}
            >
              {result}
            </Badge>
          )}
        </div>
      </li>
    );
  });

  if (collapsible) {
    return (
      <details
        className="rounded-md border border-slate-700/40 bg-slate-950/30 px-2 py-1.5"
        data-testid={`${testIdPrefix}-collapsible`}
      >
        <summary className="cursor-pointer select-none text-[11px] text-slate-400 font-medium">
          Ver detalle de los {matches.length} enfrentamientos
        </summary>
        <ul className="mt-1.5 space-y-1" data-testid={`${testIdPrefix}-list`}>
          {items}
        </ul>
      </details>
    );
  }
  return (
    <ul className="space-y-1" data-testid={`${testIdPrefix}-list`}>
      {items}
    </ul>
  );
}


export default H2HBlockPanel;
