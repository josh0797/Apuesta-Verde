import { useState, useMemo } from 'react';
import {
  Trophy, AlertTriangle, ShieldAlert, ShieldCheck, Info,
  TrendingUp, TrendingDown, Activity, Megaphone, Search, Hourglass, Flame,
  Calculator, Loader2, Check,
} from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';

/**
 * MoneyballPanel — Universal value-bet analyzer panel.
 *
 * Subsumes the prior MarketEdgePanel. Renders:
 *   • The Moneyball classification (one of 9 verdicts) as a big pill
 *   • Implied / Estimated / Edge / Threshold (the EV math)
 *   • Expected Value + ROI projection over the chosen stake
 *   • Fragility gauge (0–100) with label and contributing factors
 *   • Public Overreaction gauge (0–100) with matched narrative tokens
 *   • Market Trap signals (if any)
 *   • Undervalued reasons (if any)
 *   • "Why this pick can fail" — consolidated risks
 *
 * Driven entirely by the backend `_moneyball` + `_market_edge` payloads.
 */

const CLASSIFICATION_META = {
  STRONG_VALUE_BET:     { tone: 'emerald', icon: Trophy,        es: 'Apuesta de valor fuerte',    en: 'Strong Value Bet' },
  VALUE_BET:            { tone: 'emerald', icon: ShieldCheck,   es: 'Apuesta de valor',           en: 'Value Bet' },
  UNDERVALUED_EDGE:     { tone: 'cyan',    icon: Search,        es: 'Edge infravalorado',         en: 'Undervalued Edge' },
  LIVE_VALUE_WINDOW:    { tone: 'cyan',    icon: Flame,         es: 'Ventana de valor live',      en: 'Live Value Window' },
  FRAGILE_EDGE:         { tone: 'amber',   icon: AlertTriangle, es: 'Edge frágil',                en: 'Fragile Edge' },
  WAIT_FOR_BETTER_LINE: { tone: 'amber',   icon: Hourglass,     es: 'Esperar mejor línea',        en: 'Wait for better line' },
  PUBLIC_OVERREACTION:  { tone: 'rose',    icon: Megaphone,     es: 'Sobre-reacción pública',     en: 'Public Overreaction' },
  MARKET_TRAP:          { tone: 'rose',    icon: ShieldAlert,   es: 'Trampa de mercado',          en: 'Market Trap' },
  NO_BET_VALUE:         { tone: 'rose',    icon: Info,          es: 'Sin valor de mercado',       en: 'No Bet Value' },
};

const TONE_CLASSES = {
  emerald: 'border-emerald-500/40 bg-emerald-500/5 text-emerald-200',
  cyan:    'border-cyan-500/40 bg-cyan-500/5 text-cyan-200',
  amber:   'border-amber-500/40 bg-amber-500/5 text-amber-200',
  rose:    'border-red-500/40 bg-red-500/5 text-red-200',
};

export function MoneyballPanel({
  moneyball,
  marketEdge,
  lang = 'es',
  matchId = null,
  marketKey = null,
  sport = 'football',
}) {
  // Hooks (must run on every render — NEVER conditional / after early return).
  const [manualOdds, setManualOdds] = useState('');
  const [savedManual, setSavedManual] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Live preview of derived values when the user is typing odds.
  const liveDerived = useMemo(() => {
    const od = parseFloat(manualOdds);
    if (!od || od <= 1.01) return null;
    const implied = 1 / od;
    const est = marketEdge?.estimated_probability;
    const edge = est != null ? est - implied : null;
    return { implied_probability: implied, edge, decimal_odds: od };
  }, [manualOdds, marketEdge?.estimated_probability]);

  if (!moneyball || !marketEdge) return null;

  const cls = moneyball.classification || 'NO_BET_VALUE';
  const meta = CLASSIFICATION_META[cls] || CLASSIFICATION_META.NO_BET_VALUE;
  const Icon = meta.icon;
  const toneCls = TONE_CLASSES[meta.tone] || TONE_CLASSES.rose;

  const fmt = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  const sign = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`);
  const num = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));

  // ── Manual-odds derived values (cuando el book no tiene precio) ──
  const hasBookOdds = marketEdge.implied_probability != null;
  const showManualInput = !hasBookOdds && marketEdge.estimated_probability != null && Boolean(matchId);

  // Vista efectiva: precedencia book > manual guardada > en vivo > nada.
  const effective = hasBookOdds
    ? {
        implied_probability: marketEdge.implied_probability,
        edge: marketEdge.edge,
        source: 'book',
      }
    : savedManual
      ? {
          implied_probability: savedManual.implied_prob,
          edge: savedManual.edge,
          decimal_odds: savedManual.decimal_odds,
          source: 'manual_saved',
        }
      : liveDerived
        ? { ...liveDerived, source: 'manual_live' }
        : null;

  const handleManualSubmit = async (e) => {
    e?.preventDefault?.();
    const od = parseFloat(manualOdds);
    if (!od || od <= 1.01) {
      toast.error(lang === 'en' ? 'Enter a decimal odds value > 1.01' : 'Ingresa una cuota decimal > 1.01');
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post('/api/picks/manual-odds', {
        match_id: String(matchId),
        sport,
        market_key: marketKey || 'unknown',
        decimal_odds: od,
        estimated_prob: marketEdge.estimated_probability,
        lang,
      });
      if (data?.manual_odds) {
        setSavedManual(data.manual_odds);
        toast.success(
          lang === 'en'
            ? `Manual odds saved: ${od.toFixed(2)} (edge ${((data.manual_odds.edge || 0) * 100).toFixed(1)}%)`
            : `Cuota guardada: ${od.toFixed(2)} (edge ${((data.manual_odds.edge || 0) * 100).toFixed(1)}%)`,
        );
      } else {
        toast.error(lang === 'en' ? 'Could not save odds' : 'No se pudo guardar la cuota');
      }
    } catch (err) {
      toast.error(
        lang === 'en'
          ? `Error: ${err?.response?.data?.detail || err.message}`
          : `Error: ${err?.response?.data?.detail || err.message}`,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      className={`rounded-xl border ${toneCls} bg-card p-4 md:p-5 space-y-4`}
      data-testid="moneyball-panel"
    >
      {/* Header — classification pill */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Trophy className="h-4 w-4" />
          <h3 className="text-sm font-semibold uppercase tracking-wide">
            {lang === 'en' ? 'Moneyball Analysis' : 'Análisis Moneyball'}
          </h3>
        </div>
        <span
          data-testid="moneyball-classification"
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-semibold border border-current uppercase tracking-wide"
        >
          <Icon className="h-3.5 w-3.5" />
          {lang === 'en' ? meta.en : meta.es}
        </span>
      </div>

      {moneyball.classification_reason && (
        <p className="text-xs leading-relaxed opacity-90 italic" data-testid="moneyball-classification-reason">
          {moneyball.classification_reason}
        </p>
      )}

      {/* EV math row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="moneyball-edge-row">
        <Stat
          label={lang === 'en' ? 'Implied (book)' : 'Implícita (book)'}
          value={fmt(effective?.implied_probability ?? marketEdge.implied_probability)}
          hint={
            effective?.source === 'manual_saved'
              ? (lang === 'en' ? `manual ${effective.decimal_odds?.toFixed?.(2)}` : `manual ${effective.decimal_odds?.toFixed?.(2)}`)
              : effective?.source === 'manual_live'
                ? (lang === 'en' ? 'manual (preview)' : 'manual (preview)')
                : null
          }
          icon={TrendingDown}
          testId="mb-implied"
        />
        <Stat
          label={lang === 'en' ? 'Our estimate' : 'Nuestra estimación'}
          value={fmt(marketEdge.estimated_probability)}
          hint={marketEdge.calibration ? `cal ${(marketEdge.calibration * 100).toFixed(0)}%` : null}
          icon={TrendingUp}
          testId="mb-estimated"
        />
        <Stat
          label="Edge"
          value={sign(effective?.edge ?? marketEdge.edge)}
          accent={(effective?.edge ?? marketEdge.edge) != null && (effective?.edge ?? marketEdge.edge) >= 0 ? 'emerald' : 'red'}
          testId="mb-edge"
        />
        <Stat
          label={lang === 'en' ? 'Threshold' : 'Umbral'}
          value={`≥ ${fmt(marketEdge.edge_threshold)}`}
          hint={`(${marketEdge.bet_type})`}
          testId="mb-threshold"
        />
      </div>

      {/* Manual-odds input (visible solo cuando no hay precio del book) */}
      {showManualInput && (
        <div
          className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 space-y-2"
          data-testid="moneyball-manual-odds-block"
        >
          <div className="flex items-center gap-2 text-amber-200 text-xs uppercase tracking-wide font-semibold">
            <Calculator className="h-3.5 w-3.5" />
            {lang === 'en'
              ? 'Activate Moneyball with your bookmaker odds'
              : 'Activa Moneyball con la cuota de tu casa'}
          </div>
          <p className="text-[11px] opacity-80 leading-relaxed">
            {lang === 'en'
              ? 'The book did not return a price for this market. Paste the decimal odds you see at your bookmaker to compute edge vs. our estimate.'
              : 'El book no devolvió precio para este mercado. Pega tu cuota decimal del bookie para calcular el edge contra nuestra estimación.'}
          </p>
          <form
            onSubmit={handleManualSubmit}
            className="flex flex-col sm:flex-row gap-2 items-stretch"
            data-testid="moneyball-manual-odds-form"
          >
            <input
              type="number"
              step="0.01"
              min="1.02"
              max="1000"
              inputMode="decimal"
              placeholder={lang === 'en' ? 'Decimal odds (e.g. 2.15)' : 'Cuota decimal (ej. 2.15)'}
              value={manualOdds}
              onChange={(ev) => setManualOdds(ev.target.value)}
              disabled={submitting}
              className="flex-1 px-3 py-2 rounded-md bg-black/30 border border-amber-500/30 text-sm font-mono-tabular text-amber-100 placeholder:text-amber-200/40 focus:outline-none focus:ring-1 focus:ring-amber-400 disabled:opacity-50"
              data-testid="moneyball-manual-odds-input"
            />
            <button
              type="submit"
              disabled={submitting || !parseFloat(manualOdds) || parseFloat(manualOdds) <= 1.01}
              className="px-3 py-2 rounded-md bg-amber-500/20 hover:bg-amber-500/30 active:bg-amber-500/40 border border-amber-500/40 text-amber-100 text-xs font-semibold uppercase tracking-wide inline-flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              data-testid="moneyball-manual-odds-submit"
            >
              {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : savedManual ? <Check className="h-3.5 w-3.5" />
                : <Calculator className="h-3.5 w-3.5" />}
              {submitting
                ? (lang === 'en' ? 'Saving…' : 'Guardando…')
                : savedManual
                  ? (lang === 'en' ? 'Saved' : 'Guardada')
                  : (lang === 'en' ? 'Activate' : 'Activar')}
            </button>
          </form>
          {liveDerived && !savedManual && (
            <div
              className="text-[11px] font-mono-tabular text-amber-200 flex flex-wrap gap-3"
              data-testid="moneyball-manual-odds-preview"
            >
              <span>{lang === 'en' ? 'Implied' : 'Implícita'}: {(liveDerived.implied_probability * 100).toFixed(1)}%</span>
              {liveDerived.edge != null && (
                <span className={liveDerived.edge >= 0 ? 'text-emerald-300' : 'text-red-300'}>
                  Edge: {liveDerived.edge >= 0 ? '+' : ''}{(liveDerived.edge * 100).toFixed(1)}%
                </span>
              )}
            </div>
          )}
        </div>
      )}
      {/* EV / ROI row — only when we have valid numbers */}
      {moneyball.expected_value != null && (
        <div className="grid grid-cols-3 gap-3" data-testid="moneyball-ev-row">
          <Stat
            label={lang === 'en' ? `EV (stake ${moneyball.stake_used})` : `EV (stake ${moneyball.stake_used})`}
            value={`${moneyball.expected_value >= 0 ? '+' : ''}${num(moneyball.expected_value, 2)}`}
            accent={moneyball.expected_value >= 0 ? 'emerald' : 'red'}
            testId="mb-ev"
          />
          <Stat
            label={lang === 'en' ? 'ROI projection' : 'ROI proyectado'}
            value={`${moneyball.roi_projection_pct >= 0 ? '+' : ''}${num(moneyball.roi_projection_pct, 1)}%`}
            accent={moneyball.roi_projection_pct >= 0 ? 'emerald' : 'red'}
            testId="mb-roi"
          />
          <Stat
            label={lang === 'en' ? 'Net if win' : 'Ganancia si gana'}
            value={`+${num(moneyball.net_profit_if_win, 2)}`}
            testId="mb-net-win"
          />
        </div>
      )}

      {/* Gauges row — Fragility + Public Overreaction */}
      <div className="grid sm:grid-cols-2 gap-3">
        <Gauge
          label={lang === 'en' ? 'Fragility Score' : 'Fragilidad'}
          score={moneyball.fragility?.score ?? 0}
          status={moneyball.fragility?.label}
          factors={moneyball.fragility?.factors || []}
          inverted /* higher = worse */
          testId="moneyball-fragility"
          lang={lang}
        />
        <Gauge
          label={lang === 'en' ? 'Public Overreaction' : 'Sobre-reacción pública'}
          score={moneyball.public_overreaction?.score ?? 0}
          status={moneyball.public_overreaction?.label}
          factors={(moneyball.public_overreaction?.matched || []).slice(0, 4).map(transformNarrativeLabel)}
          inverted /* higher = worse */
          testId="moneyball-overreaction"
          lang={lang}
        />
      </div>

      {/* Signal lists — traps + undervalued */}
      <div className="grid sm:grid-cols-2 gap-3">
        <SignalList
          title={lang === 'en' ? 'Market trap signals' : 'Señales de trampa de mercado'}
          items={moneyball.market_trap_signals || []}
          tone="rose"
          emptyText={lang === 'en' ? 'No traps detected' : 'Ninguna trampa detectada'}
          testId="moneyball-traps"
        />
        <SignalList
          title={lang === 'en' ? 'Undervalued reasons' : 'Valor infravalorado'}
          items={moneyball.undervalued_reasons || []}
          tone="emerald"
          emptyText={lang === 'en' ? 'No undervalued signals' : 'Sin señales de infravaloración'}
          testId="moneyball-undervalued"
        />
      </div>

      {/* Why this can fail — consolidated */}
      {moneyball.why_this_can_fail && moneyball.why_this_can_fail.length > 0 && (
        <div className="border-t border-current/20 pt-3" data-testid="moneyball-why-can-fail">
          <div className="text-[11px] uppercase tracking-wide opacity-80 flex items-center gap-1.5 mb-2">
            <AlertTriangle className="h-3.5 w-3.5" />
            {lang === 'en' ? 'Why this pick can fail' : 'Por qué este pick puede fallar'}
          </div>
          <ul className="space-y-1 text-xs opacity-90">
            {moneyball.why_this_can_fail.slice(0, 6).map((r, i) => (
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


/* ─── Inline badge for MatchCard ───────────────────────────────────────── */
const SHORT_LABEL = {
  STRONG_VALUE_BET: 'STRONG VALUE',
  VALUE_BET: 'VALUE',
  UNDERVALUED_EDGE: 'UNDERVALUED',
  LIVE_VALUE_WINDOW: 'LIVE VALUE',
  FRAGILE_EDGE: 'FRAGILE',
  WAIT_FOR_BETTER_LINE: 'WAIT LINE',
  PUBLIC_OVERREACTION: 'OVERREACTION',
  MARKET_TRAP: 'TRAP',
  NO_BET_VALUE: 'NO VALUE',
};

export function MoneyballBadge({ moneyball, marketEdge, lang = 'es', testId = 'moneyball-badge' }) {
  if (!moneyball) return null;
  const cls = moneyball.classification || 'NO_BET_VALUE';
  const meta = CLASSIFICATION_META[cls] || CLASSIFICATION_META.NO_BET_VALUE;
  const toneCls = TONE_CLASSES[meta.tone] || TONE_CLASSES.rose;
  const Icon = meta.icon;
  const edgePct =
    marketEdge && marketEdge.edge != null
      ? ` ${marketEdge.edge >= 0 ? '+' : ''}${(marketEdge.edge * 100).toFixed(1)}%`
      : '';
  const title = lang === 'en' ? meta.en : meta.es;
  const tooltipLines = [
    `${title}${edgePct}`,
    moneyball.classification_reason || '',
  ].filter(Boolean).join('\n');
  return (
    <span
      data-testid={testId}
      title={tooltipLines}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono-tabular font-semibold border ${toneCls}`}
    >
      <Icon className="h-3 w-3" />
      {SHORT_LABEL[cls] || cls}{edgePct}
    </span>
  );
}


/* ─── Subcomponents ────────────────────────────────────────────────────── */

function Stat({ label, value, hint, icon: Icon, accent, testId }) {
  const accentCls =
    accent === 'emerald' ? 'text-emerald-200'
    : accent === 'red'   ? 'text-red-200'
    : 'text-current';
  return (
    <div className="rounded-md border border-current/15 bg-black/10 px-2.5 py-2" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide opacity-70 flex items-center gap-1">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </div>
      <div className={`text-base mono font-mono-tabular font-semibold mt-0.5 ${accentCls}`}>{value}</div>
      {hint && <div className="text-[10px] opacity-60 mt-0.5 mono font-mono-tabular">{hint}</div>}
    </div>
  );
}

function Gauge({ label, score, status, factors, inverted, testId, lang }) {
  // Normalise score 0–100 to a bar width. When `inverted=true`, the gauge is
  // semantically "higher = worse" so we colour-code accordingly.
  const pct = Math.max(0, Math.min(100, Number(score) || 0));
  const tone = inverted
    ? pct <= 30 ? 'emerald' : pct <= 60 ? 'amber' : 'rose'
    : pct >= 70 ? 'emerald' : pct >= 40 ? 'amber' : 'rose';
  const bar = {
    emerald: 'bg-emerald-400/80',
    amber: 'bg-amber-400/80',
    rose: 'bg-red-400/80',
  }[tone];
  const text = {
    emerald: 'text-emerald-300',
    amber: 'text-amber-300',
    rose: 'text-red-300',
  }[tone];
  return (
    <div className="rounded-md border border-current/15 bg-black/10 px-3 py-2.5" data-testid={testId}>
      <div className="flex items-center justify-between text-[11px] uppercase tracking-wide opacity-80">
        <span className="flex items-center gap-1.5">
          <Activity className="h-3 w-3" />
          {label}
        </span>
        <span className={`mono font-mono-tabular ${text}`} data-testid={`${testId}-score`}>
          {pct}/100 · {status || '—'}
        </span>
      </div>
      <div className="h-1.5 mt-2 rounded-full bg-white/[0.07] overflow-hidden">
        <div className={`h-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      {factors && factors.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] opacity-85">
          {factors.slice(0, 4).map((f, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="opacity-60">•</span>
              <span>{f}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SignalList({ title, items, tone, emptyText, testId }) {
  const toneText = {
    emerald: 'text-emerald-300',
    rose: 'text-red-300',
  }[tone] || 'text-current';
  return (
    <div className="rounded-md border border-current/15 bg-black/10 px-3 py-2.5" data-testid={testId}>
      <div className={`text-[11px] uppercase tracking-wide font-semibold ${toneText} mb-1.5`}>
        {title}
      </div>
      {items.length === 0 ? (
        <div className="text-[11px] opacity-60 italic">{emptyText}</div>
      ) : (
        <ul className="space-y-0.5 text-[11px] opacity-90">
          {items.slice(0, 5).map((s, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="opacity-60">•</span>
              <span>{s}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Convert regex-encoded patterns from the backend into something a human reads:
//   "necesitan? ganar"  →  "necesitan ganar"
//   "no puede(n)? perder"  →  "no puede perder"
function transformNarrativeLabel(pat) {
  if (!pat) return '';
  return String(pat)
    .replace(/\?\?+/g, '')           // collapse double quantifiers
    .replace(/\?/g, '')              // strip optional markers
    .replace(/\([^)]*\)/g, (m) => m.replace(/[()|]/g, ' ').trim().split(/\s+/)[0]) // pick first alt
    .replace(/\\s\+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
