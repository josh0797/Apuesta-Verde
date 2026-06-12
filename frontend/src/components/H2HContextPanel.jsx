import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { History, AlertTriangle } from 'lucide-react';

/**
 * Phase F82 — Rich H2H Context Panel.
 *
 * Renders the canonical ``h2h_context`` payload produced by
 * ``services/football_h2h_context_builder.py`` as a real list of
 * matches + key signals (Under 3.5, BTTS, avg goals). Replaces the
 * useless 'Se identifican 4 enfrentamientos directos recientes…' line.
 *
 * Props:
 *   - context: the h2h_context dict from the backend.
 *   - testIdPrefix: optional prefix for data-testid attributes.
 */
export const H2HContextPanel = ({ context, testIdPrefix = 'h2h-context' }) => {
  if (!context || typeof context !== 'object') return null;

  // No H2H disponible — explicar la razón.
  if (!context.available) {
    return (
      <Card
        className="border-slate-700/60 bg-slate-900/40"
        data-testid={`${testIdPrefix}-empty`}
      >
        <CardContent className="py-3 px-4 text-xs text-slate-400 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-300 flex-shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-slate-200">Enfrentamientos directos</div>
            <p className="mt-0.5">{context.editorial_text || 'No hay H2H reciente confiable.'}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const matches = Array.isArray(context.matches) ? context.matches : [];
  const summary = context.summary || {};
  const quality = context.sample_quality || 'USABLE';
  const qualityVariant = {
    STRONG:  'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
    USABLE:  'bg-sky-500/20 text-sky-200 border-sky-500/30',
    LIMITED: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
  }[quality] || 'bg-slate-500/20 text-slate-300 border-slate-500/30';

  // Métricas que el operador realmente quiere ver.
  const metricLine = [];
  if (typeof summary.avg_goals === 'number') {
    metricLine.push(`Promedio de goles: ${summary.avg_goals.toFixed(2)}`);
  }
  if (typeof summary.under_3_5_rate === 'number') {
    metricLine.push(`Under 3.5: ${Math.round(summary.under_3_5_rate * 100)}%`);
  }
  if (typeof summary.btts_rate === 'number') {
    metricLine.push(`BTTS: ${Math.round(summary.btts_rate * 100)}%`);
  }
  if (typeof summary.over_2_5_rate === 'number') {
    metricLine.push(`Over 2.5: ${Math.round(summary.over_2_5_rate * 100)}%`);
  }

  return (
    <Card
      className="border-slate-700/60 bg-slate-900/40"
      data-testid={`${testIdPrefix}-card`}
    >
      <CardContent className="py-3 px-4">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-slate-300" />
            <span className="text-sm font-semibold text-slate-200">
              Enfrentamientos directos recientes
            </span>
            <span className="text-xs text-slate-500">
              ({context.sample_size} {context.sample_size === 1 ? 'partido' : 'partidos'})
            </span>
          </div>
          <Badge
            className={`text-[10px] uppercase tracking-wider ${qualityVariant}`}
            data-testid={`${testIdPrefix}-quality`}
          >
            {quality}
          </Badge>
        </div>

        <ul className="space-y-1 mb-3" data-testid={`${testIdPrefix}-list`}>
          {matches.map((m, idx) => (
            <li
              key={`${m.date}-${idx}`}
              className="flex justify-between gap-2 text-xs text-slate-300"
              data-testid={`${testIdPrefix}-item-${idx}`}
            >
              <span className="text-slate-500 font-mono">{m.date || '—'}</span>
              <span className="flex-1 text-slate-200">{m.result}</span>
              {m.friendly && (
                <Badge className="text-[9px] bg-slate-700/60 text-slate-300">amistoso</Badge>
              )}
            </li>
          ))}
        </ul>

        {metricLine.length > 0 && (
          <div
            className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400 border-t border-slate-700/60 pt-2"
            data-testid={`${testIdPrefix}-summary`}
          >
            {metricLine.map((m, idx) => (
              <span key={idx}>{m}</span>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default H2HContextPanel;
