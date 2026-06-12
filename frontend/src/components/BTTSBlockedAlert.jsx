/**
 * BTTSBlockedAlert
 * =================
 * Fix 2 (Phase F58+) — UI block que explica por qué la guard de BTTS Live
 * bloqueó el mercado y propone la alternativa (Over 1.5 o WATCHLIST).
 *
 * Espera leer ``copilot.btts_live_guard`` con el shape que produce
 * ``football_btts_live_guard.guard_btts_live_recommendation``:
 *
 *   {
 *     "btts_allowed":       false,
 *     "blocked_market":     "BTTS",
 *     "replacement_market": "OVER_1_5" | "WATCHLIST",
 *     "replacement_label":  "Más de 1.5 goles" | "Watchlist",
 *     "risk":               "LOW" | "MEDIUM" | "HIGH",
 *     "reason_codes":       [...],
 *     "narrative_es":       "Aunque México tiene momentum ofensivo..."
 *   }
 *
 * Self-hides si ``btts_allowed`` es true o si la guard no fue ejecutada.
 */

import React from 'react';
import { AlertOctagon, ArrowRight } from 'lucide-react';

const RISK_TONE = {
  HIGH:   'border-rose-500/50 bg-rose-500/10    text-rose-100',
  MEDIUM: 'border-amber-500/50 bg-amber-500/10   text-amber-100',
  LOW:    'border-slate-500/50 bg-slate-500/10   text-slate-100',
};

function ReasonCodePill({ code }) {
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-[9px] font-mono uppercase tracking-wide border border-rose-500/30 bg-rose-500/[0.08] text-rose-200/90"
      data-testid={`btts-blocked-rc-${code.toLowerCase()}`}
    >
      {code}
    </span>
  );
}

export function BTTSBlockedAlert({ copilot, testId = 'btts-blocked-alert' }) {
  if (!copilot || typeof copilot !== 'object') return null;
  const guard = copilot.btts_live_guard;
  if (!guard || guard.btts_allowed !== false) return null;

  const risk = (guard.risk || 'LOW').toUpperCase();
  const riskTone = RISK_TONE[risk] || RISK_TONE.LOW;
  const repl = guard.replacement_label || guard.replacement_market;

  return (
    <section
      className="rounded-xl border border-rose-500/40 bg-rose-500/[0.06] p-3.5 space-y-3"
      data-testid={testId}
      role="alert"
    >
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <AlertOctagon className="w-4 h-4 text-rose-300" />
          <h4 className="text-[12px] uppercase tracking-wider font-semibold text-rose-100">
            BTTS bloqueado
          </h4>
        </div>
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${riskTone}`}
          data-testid={`${testId}-risk`}
        >
          Riesgo {risk}
        </span>
      </header>

      {guard.narrative_es && (
        <p
          className="text-[12.5px] leading-snug text-rose-50/90"
          data-testid={`${testId}-narrative`}
        >
          {guard.narrative_es}
        </p>
      )}

      {repl && (
        <div
          className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/[0.08] px-2.5 py-2"
          data-testid={`${testId}-replacement`}
        >
          <ArrowRight className="w-3.5 h-3.5 text-emerald-300 shrink-0" />
          <div className="flex-1">
            <div className="text-[10px] uppercase tracking-wider text-emerald-200/80 font-semibold">
              Alternativa sugerida
            </div>
            <div className="text-[13px] font-semibold text-emerald-50">
              {repl}
            </div>
          </div>
        </div>
      )}

      {Array.isArray(guard.reason_codes) && guard.reason_codes.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid={`${testId}-codes`}>
          {guard.reason_codes.slice(0, 6).map((code) => (
            <ReasonCodePill key={code} code={code} />
          ))}
        </div>
      )}
    </section>
  );
}

export default BTTSBlockedAlert;
