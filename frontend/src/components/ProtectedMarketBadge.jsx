import { useState } from 'react';
import { ChevronDown, ChevronRight, Shield, Lightbulb, TrendingDown, BarChart3 } from 'lucide-react';

/**
 * ProtectedMarketBadge — surfaces the Phase 9 Alternative Market Scan result
 * on a MatchCard.
 *
 * Consumes the `_alternative_market_payload` produced by
 * services/under_market_scan.py and lifted into the pick by
 * services/analyst_engine._apply_protected_alternative_scan:
 *
 *   {
 *     market: "Under 3.5",
 *     selection: "Under 3.5",
 *     decimal_odds: 1.35,
 *     edge_pct: 3.70,
 *     profile_score: 84,
 *     state: "PROTECTED_MARKET_RECOMMENDED" | "UNDER35_WATCHLIST",
 *     reasons: [...],
 *     why_3_5_safer_than_2_5: [...],
 *     h2h_under_rate: 1.0,
 *     h2h_avg_goals: 1.4,
 *     samples_h2h: 5,
 *     profile_3_5: {...},
 *     profile_2_5: {...},
 *     combo_candidate?: { selection, approximate_decimal_odds, edge_pct, ... },
 *   }
 *
 * Renders inline on the MatchCard so the user understands:
 *   • WHY this pick was rescued (direct market had no edge)
 *   • WHICH protected market and odds
 *   • WHY 3.5 is safer than 2.5 (when applicable)
 *   • The H2H trend that drove the call
 *   • An optional DC + Under combo ticket
 */
const STATE_LABELS = {
  PROTECTED_MARKET_RECOMMENDED: { es: 'Mercado protegido — recomendado', en: 'Protected market — recommended' },
  UNDER35_WATCHLIST:            { es: 'Under 3.5 — watchlist',            en: 'Under 3.5 — watchlist' },
  UNDER25_WATCHLIST:            { es: 'Under 2.5 — watchlist',            en: 'Under 2.5 — watchlist' },
};

const STATE_TONE = {
  PROTECTED_MARKET_RECOMMENDED: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  UNDER35_WATCHLIST:            'border-amber-500/40 bg-amber-500/10 text-amber-200',
  UNDER25_WATCHLIST:            'border-amber-500/40 bg-amber-500/10 text-amber-200',
};

export function ProtectedMarketBadge({ payload, lang = 'es', testId = 'protected-market-badge' }) {
  const [open, setOpen] = useState(false);
  if (!payload) return null;

  const state = payload.state || 'PROTECTED_MARKET_RECOMMENDED';
  const tone = STATE_TONE[state] || STATE_TONE.PROTECTED_MARKET_RECOMMENDED;
  const stateLabel = (STATE_LABELS[state] || {})[lang] || state;
  const edgePct = payload.edge_pct ?? 0;
  const profile = payload.profile_score ?? 0;
  const rate = Math.round((payload.h2h_under_rate || 0) * 100);
  const samples = payload.samples_h2h || 0;
  const avg = payload.h2h_avg_goals;

  return (
    <div
      className={`rounded-lg border ${tone} p-3 space-y-2`}
      data-testid={testId}
    >
      {/* Header — always visible */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 text-left"
        data-testid={`${testId}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Shield className="h-4 w-4 shrink-0" />
          <span className="text-xs font-semibold uppercase tracking-wide truncate">
            {stateLabel}
          </span>
        </div>
        {open ? <ChevronDown className="h-4 w-4 opacity-70" /> : <ChevronRight className="h-4 w-4 opacity-70" />}
      </button>

      {/* Compact metric row */}
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <Pill label={payload.market || payload.selection} accent="emerald" testId={`${testId}-market`} />
        <Pill label={`Cuota ${payload.decimal_odds}`} testId={`${testId}-odds`} />
        <Pill
          label={`Edge ${edgePct >= 0 ? '+' : ''}${edgePct.toFixed(2)}%`}
          accent={edgePct >= 3 ? 'emerald' : edgePct >= 1 ? 'cyan' : 'amber'}
          testId={`${testId}-edge`}
        />
        <Pill label={`Score ${profile}/100`} testId={`${testId}-score`} />
        {samples > 0 && (
          <Pill
            label={lang === 'en'
              ? `H2H ${rate}% under (${samples})`
              : `H2H ${rate}% under (${samples})`}
            accent={rate >= 80 ? 'emerald' : rate >= 60 ? 'cyan' : 'amber'}
            testId={`${testId}-h2h`}
          />
        )}
      </div>

      {/* Lead-in sentence — always visible (this is the "aha" moment) */}
      <p className="text-xs leading-relaxed">
        <Lightbulb className="inline h-3 w-3 mr-1 opacity-80" />
        {lang === 'en'
          ? `Direct market had no value. The engine rescued the match via ${payload.market} (protected goal-line).`
          : `Mercado directo sin valor. El motor rescató el partido en ${payload.market} (línea de goles protegida).`}
      </p>

      {/* Expanded body */}
      {open && (
        <div className="space-y-3 pt-2 border-t border-current/20" data-testid={`${testId}-body`}>
          {/* H2H summary block */}
          {samples > 0 && (
            <div className="rounded-md border border-current/20 bg-background/30 px-2.5 py-2">
              <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase opacity-80 mb-1">
                <BarChart3 className="h-3 w-3" />
                {lang === 'en' ? 'Head-to-head trend' : 'Tendencia H2H'}
              </div>
              <div className="text-xs space-y-0.5">
                <div>
                  {lang === 'en'
                    ? `Last ${samples} matches between these teams: ${rate}% finished under ${payload.market.replace('Under ', '')} goals.`
                    : `Últimos ${samples} partidos entre estos equipos: ${rate}% terminaron bajo ${payload.market.replace('Under ', '')} goles.`}
                </div>
                {avg != null && (
                  <div className="opacity-80">
                    {lang === 'en' ? `Average total goals: ${avg.toFixed(1)}.` : `Media de goles totales: ${avg.toFixed(1)}.`}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Reasons */}
          {(payload.reasons || []).length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold uppercase opacity-80">
                {lang === 'en' ? 'Why this market' : 'Por qué este mercado'}
              </div>
              <ul className="text-xs space-y-0.5 list-disc list-inside opacity-90">
                {payload.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}

          {/* Why 3.5 safer than 2.5 */}
          {(payload.why_3_5_safer_than_2_5 || []).length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold uppercase opacity-80 flex items-center gap-1.5">
                <TrendingDown className="h-3 w-3" />
                {lang === 'en' ? 'Why Under 3.5 vs Under 2.5' : 'Por qué Under 3.5 frente a Under 2.5'}
              </div>
              <ul className="text-xs space-y-0.5 list-disc list-inside opacity-90">
                {payload.why_3_5_safer_than_2_5.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}

          {/* Combo candidate (DC + Under) */}
          {payload.combo_candidate && (
            <div className="rounded-md border border-current/20 bg-background/30 px-2.5 py-2 space-y-1" data-testid={`${testId}-combo`}>
              <div className="text-[11px] font-semibold uppercase opacity-80">
                {lang === 'en' ? 'Combo ticket (approximate)' : 'Ticket combinado (aproximado)'}
              </div>
              <div className="text-xs">
                <span className="font-mono-tabular">{payload.combo_candidate.selection}</span>
                {' · '}
                <span className="opacity-90">
                  {lang === 'en' ? 'odds' : 'cuota'} ≈ {payload.combo_candidate.approximate_decimal_odds}
                </span>
                {' · '}
                <span className="opacity-90">
                  edge {payload.combo_candidate.edge_pct >= 0 ? '+' : ''}{payload.combo_candidate.edge_pct?.toFixed?.(2)}%
                </span>
              </div>
              <p className="text-[10px] opacity-70 italic leading-tight">
                {payload.combo_candidate.approximation_note}
              </p>
            </div>
          )}

          {/* Sub-scores (collapsed by default, dev-friendly transparency) */}
          {payload.profile_3_5 && payload.profile_3_5._sub && (
            <details className="text-[10px] opacity-70">
              <summary className="cursor-pointer hover:opacity-100">
                {lang === 'en' ? 'Sub-score breakdown' : 'Desglose de sub-scores'}
              </summary>
              <pre className="mt-1 font-mono whitespace-pre-wrap break-words">
                {JSON.stringify({ '3.5': payload.profile_3_5._sub, '2.5': payload.profile_2_5?._sub }, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function Pill({ label, accent, testId }) {
  const cls = accent === 'emerald'
    ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-100'
    : accent === 'cyan'
      ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-100'
      : accent === 'amber'
        ? 'border-amber-500/40 bg-amber-500/15 text-amber-100'
        : 'border-border bg-background/30';
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded border font-mono-tabular tabular-nums ${cls}`}
      data-testid={testId}
    >
      {label}
    </span>
  );
}
