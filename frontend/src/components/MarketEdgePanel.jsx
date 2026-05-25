import { TrendingUp, TrendingDown, ShieldAlert, ShieldCheck, AlertTriangle, Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * MarketEdgePanel — Universal across sports.
 *
 * Shows the user the breakdown that the backend Market Implied Probability
 * Guardrail used to validate a pick: implied vs estimated (calibrated)
 * probability, the resulting edge, the per-bet-type threshold, and the final
 * verdict. Also surfaces the "Why this pick can fail" hint that the analyst
 * engine writes alongside risks.
 *
 * Props:
 *   edge:       backend `pick._market_edge` payload, or null
 *   risks:      array of strings (from pick.risks) used for "why it can fail"
 *   lang:       'es' | 'en'
 *   sport:      'football' | 'basketball' | 'baseball' (for labeling only)
 */
export function MarketEdgePanel({ edge, risks = [], lang = 'es', sport = 'football' }) {
  if (!edge) return null;

  const isValue = edge.verdict === 'VALUE_FOUND';
  const isNoBet = edge.verdict === 'NO_BET_VALUE';
  const isInsuf = edge.verdict === 'INSUFFICIENT_DATA';

  const verdictLabel =
    lang === 'en'
      ? (isValue ? 'Value detected' : isNoBet ? 'No bet value' : 'Insufficient data')
      : (isValue ? 'Valor detectado' : isNoBet ? 'Sin valor de mercado' : 'Datos insuficientes');

  const verdictTone = isValue
    ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200'
    : isNoBet
    ? 'border-red-500/30 bg-red-500/5 text-red-200'
    : 'border-amber-500/30 bg-amber-500/5 text-amber-200';

  const VerdictIcon = isValue ? ShieldCheck : isNoBet ? ShieldAlert : Info;

  const fmt = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  const edgeSign = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`);

  return (
    <section className={`rounded-xl border bg-card p-4 md:p-5 ${verdictTone}`} data-testid="market-edge-panel">
      <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          <VerdictIcon className="h-4 w-4" />
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            {lang === 'en' ? 'Market Edge Validation' : 'Validación de Valor de Mercado'}
          </h3>
        </div>
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border border-current"
          data-testid="market-edge-verdict"
        >
          {verdictLabel}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <Stat
          label={lang === 'en' ? 'Implied (book)' : 'Implícita (book)'}
          value={fmt(edge.implied_probability)}
          testId="edge-implied"
          icon={TrendingDown}
        />
        <Stat
          label={lang === 'en' ? 'Our estimate' : 'Nuestra estimación'}
          value={fmt(edge.estimated_probability)}
          testId="edge-estimated"
          hint={
            edge.calibration
              ? lang === 'en'
                ? `cal ${(edge.calibration * 100).toFixed(0)}%`
                : `cal ${(edge.calibration * 100).toFixed(0)}%`
              : null
          }
          icon={TrendingUp}
        />
        <Stat
          label={lang === 'en' ? 'Edge' : 'Edge'}
          value={edgeSign(edge.edge)}
          testId="edge-value"
          accent={edge.edge != null && edge.edge >= 0 ? 'emerald' : 'red'}
        />
        <Stat
          label={lang === 'en' ? 'Threshold' : 'Umbral'}
          value={`≥ ${fmt(edge.edge_threshold)}`}
          testId="edge-threshold"
          hint={`(${edge.bet_type})`}
        />
      </div>

      {(edge.reason || isInsuf) && (
        <p className="text-xs leading-relaxed opacity-90 italic" data-testid="market-edge-reason">
          {edge.reason || (
            lang === 'en'
              ? 'Not enough market data to calculate edge with precision. Odds snapshots or historical form may be missing.'
              : 'Sin suficientes datos de mercado para calcular el edge con precisión. Pueden faltar snapshots de cuotas o forma histórica.'
          )}
        </p>
      )}

      {/* Why this pick can fail */}
      {risks && risks.length > 0 && (
        <div className="mt-4 border-t border-current/20 pt-3" data-testid="why-can-fail">
          <div className="text-[11px] uppercase tracking-wide opacity-80 flex items-center gap-1.5 mb-2">
            <AlertTriangle className="h-3.5 w-3.5" />
            {lang === 'en' ? 'Why this pick can fail' : 'Por qué este pick puede fallar'}
          </div>
          <ul className="space-y-1 text-xs opacity-90">
            {risks.slice(0, 5).map((r, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="opacity-60">•</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * MarketEdgeBadge — Compact inline badge for MatchCard / list rows.
 * Shows edge percentage colored by verdict.
 */
export function MarketEdgeBadge({ edge, lang = 'es', testId = 'market-edge-badge' }) {
  if (!edge || edge.edge == null) return null;
  const verdict = edge.verdict;
  const tone =
    verdict === 'VALUE_FOUND'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
      : verdict === 'NO_BET_VALUE'
      ? 'border-red-500/40 bg-red-500/10 text-red-200'
      : 'border-amber-500/40 bg-amber-500/10 text-amber-200';
  const sign = edge.edge >= 0 ? '+' : '';
  const label = lang === 'en' ? 'EDGE' : 'EDGE';
  const badge = (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono-tabular font-semibold border ${tone}`}
      data-testid={testId}
      title={edge.reason || ''}
    >
      {label} {sign}{(edge.edge * 100).toFixed(1)}%
    </span>
  );
  if (!edge.reason) return badge;
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>{badge}</TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[260px] leading-relaxed">
          {edge.reason}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function Stat({ label, value, hint, testId, accent, icon: Icon }) {
  const accentCls =
    accent === 'emerald'
      ? 'text-emerald-200'
      : accent === 'red'
      ? 'text-red-200'
      : 'text-current';
  return (
    <div className="rounded-md border border-current/15 bg-black/10 px-2.5 py-2" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide opacity-70 flex items-center gap-1">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className={`text-base mono font-mono-tabular font-semibold mt-0.5 ${accentCls}`}>
        {value}
      </div>
      {hint && <div className="text-[10px] opacity-60 mt-0.5">{hint}</div>}
    </div>
  );
}
