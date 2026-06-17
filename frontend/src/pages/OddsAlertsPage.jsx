import React from 'react';
import { OddsAlertsPanel } from '@/components/OddsAlertsPanel';

/**
 * Sprint E.2 / UI · Standalone page for the Odds Alerts dashboard.
 *
 * Used by ``/odds-alerts`` route in App.js. Currently a thin wrapper
 * around the panel so we can grow it later with summary cards / charts
 * without touching the panel itself.
 */
export default function OddsAlertsPage() {
  return (
    <div
      className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-4"
      data-testid="odds-alerts-page"
    >
      <header className="space-y-1">
        <h1 className="text-lg font-semibold text-slate-100">
          Alertas de cuotas
        </h1>
        <p className="text-[12px] text-slate-400 max-w-2xl">
          Señales detectadas sobre las snapshots de odds en vivo (E.1) — cuotas
          atípicas vs consenso, edge vs modelo, movimientos rápidos de línea y
          dispersión entre bookmakers. Estricto <span className="font-mono">observe_only</span>:
          ningún flujo automatiza apuestas.
        </p>
      </header>
      <OddsAlertsPanel testIdPrefix="odds-alerts" />
    </div>
  );
}
