import { TrendingUp, AlertTriangle, Clock, Activity, Shield, Sparkles, XCircle, Wallet } from 'lucide-react';

/**
 * LiveCopilotCard — the new "Recomendación Live" prominent card.
 *
 * Reads `match._live_interpreter` (HumanLiveInterpreter output) and
 * renders the coach-voice payload: title + subtitle + big recommendation
 * + action + confidence + risk + ¿por qué? bullets + suggested market
 * pill + trap warning.
 *
 * Designed to sit at the TOP of every live football card, BEFORE the
 * raw metric grid. The goal: a bettor sees the decision FIRST, then the
 * supporting numbers — exactly like a pro analyst would deliver.
 */

const MOOD_TOKENS = {
  trap: {
    surface: 'border-red-500/45 bg-gradient-to-br from-red-500/15 via-red-500/8 to-red-500/0',
    accent: 'text-red-200',
    chip: 'bg-red-500/20 text-red-100 border-red-500/40',
    dot: 'bg-red-400',
  },
  value: {
    surface: 'border-emerald-500/45 bg-gradient-to-br from-emerald-500/15 via-emerald-500/8 to-emerald-500/0',
    accent: 'text-emerald-200',
    chip: 'bg-emerald-500/20 text-emerald-100 border-emerald-500/40',
    dot: 'bg-emerald-400',
  },
  watch: {
    surface: 'border-amber-500/45 bg-gradient-to-br from-amber-500/15 via-amber-500/8 to-amber-500/0',
    accent: 'text-amber-200',
    chip: 'bg-amber-500/20 text-amber-100 border-amber-500/40',
    dot: 'bg-amber-400',
  },
  neutral: {
    surface: 'border-slate-500/35 bg-slate-500/8',
    accent: 'text-slate-200',
    chip: 'bg-slate-500/20 text-slate-100 border-slate-500/40',
    dot: 'bg-slate-300',
  },
  insufficient: {
    surface: 'border-slate-500/30 bg-slate-500/5',
    accent: 'text-slate-300',
    chip: 'bg-slate-500/15 text-slate-200 border-slate-500/30',
    dot: 'bg-slate-400',
  },
};

const ACTION_ICON = {
  BET_NOW:        TrendingUp,
  WAIT:           Clock,
  WATCHLIST:      Activity,
  NO_BET:         XCircle,
  CASH_OUT:       Wallet,
  LOW_CONFIDENCE: AlertTriangle,
};

const RISK_TONE = {
  LOW:    { color: 'text-emerald-300', dot: 'bg-emerald-400', label_es: 'Bajo',  label_en: 'Low' },
  MEDIUM: { color: 'text-amber-300',   dot: 'bg-amber-400',   label_es: 'Medio', label_en: 'Medium' },
  HIGH:   { color: 'text-red-300',     dot: 'bg-red-400',     label_es: 'Alto',  label_en: 'High' },
};

const URGENCY_LABEL = {
  low:    { es: 'Sin prisa',       en: 'No rush' },
  medium: { es: 'Atento',          en: 'Watching' },
  high:   { es: 'Ventana abierta', en: 'Window open' },
};

function ConfidenceBar({ value, tone }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="flex flex-col gap-1 min-w-[110px]" data-testid="copilot-confidence">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wide opacity-70">
        <span>Confianza</span>
        <span className="font-mono-tabular">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-foreground/10 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${tone === 'value' ? 'bg-emerald-400' : tone === 'trap' ? 'bg-red-400' : tone === 'watch' ? 'bg-amber-400' : 'bg-slate-400'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function LiveCopilotCard({ interpreter, lang = 'es', testId = 'live-copilot' }) {
  if (!interpreter || typeof interpreter !== 'object') return null;

  const mood = interpreter.mood || 'neutral';
  const tokens = MOOD_TOKENS[mood] || MOOD_TOKENS.neutral;
  const ActionIcon = ACTION_ICON[interpreter.action] || Sparkles;
  const risk = RISK_TONE[interpreter.risk] || RISK_TONE.MEDIUM;
  const urgency = URGENCY_LABEL[interpreter.urgency] || URGENCY_LABEL.low;
  const title = interpreter.title || (lang === 'en' ? 'Balanced' : 'Balanceado');
  const subtitle = interpreter.subtitle || '';
  const recommendation = interpreter.recommendation || '';
  const narration = interpreter.narration || '';
  const why = Array.isArray(interpreter.why) ? interpreter.why : [];

  return (
    <div
      className={`rounded-xl border ${tokens.surface} p-3 space-y-2.5`}
      data-testid={testId}
      data-mood={mood}
      data-action={interpreter.action}
    >
      {/* Title row — replaces the cold "BALANCEADO" label */}
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <h3
            className={`text-base sm:text-lg font-bold leading-tight ${tokens.accent}`}
            data-testid={`${testId}-title`}
          >
            {title}
          </h3>
          {subtitle && (
            <p className="text-xs mt-0.5 opacity-90" data-testid={`${testId}-subtitle`}>
              {subtitle}
            </p>
          )}
        </div>
        <ConfidenceBar value={interpreter.confidence} tone={mood} />
      </div>

      {/* Main recommendation row */}
      <div className="flex items-center justify-between gap-3 pt-2 border-t border-foreground/10 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <ActionIcon className={`h-5 w-5 shrink-0 ${tokens.accent}`} aria-hidden />
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-wider opacity-70">
              {lang === 'en' ? 'Recommendation' : 'Recomendación Live'}
            </div>
            <div className="text-sm sm:text-base font-semibold leading-tight" data-testid={`${testId}-recommendation`}>
              {recommendation}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-medium ${tokens.chip}`}
            data-testid={`${testId}-action`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${tokens.dot}`} aria-hidden />
            {interpreter.action_label || interpreter.action}
          </span>
          <span
            className="inline-flex items-center gap-1 text-[11px] opacity-90"
            data-testid={`${testId}-risk`}
            title={lang === 'en' ? `Risk: ${risk.label_en}` : `Riesgo: ${risk.label_es}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${risk.dot}`} aria-hidden />
            <span className="uppercase tracking-wide opacity-80">
              {lang === 'en' ? 'Risk' : 'Riesgo'}
            </span>
            <span className={`font-semibold ${risk.color}`}>
              {lang === 'en' ? risk.label_en : risk.label_es}
            </span>
          </span>
          <span
            className="hidden sm:inline-flex text-[11px] opacity-70"
            data-testid={`${testId}-urgency`}
          >
            · {lang === 'en' ? urgency.en : urgency.es}
          </span>
        </div>
      </div>

      {/* Narration "Razón:" paragraph */}
      {narration && (
        <div className="text-xs leading-snug opacity-95" data-testid={`${testId}-narration`}>
          <span className="font-semibold opacity-80 mr-1">{lang === 'en' ? 'Reason:' : 'Razón:'}</span>
          {narration}
        </div>
      )}

      {/* Why-bullets */}
      {why.length > 0 && (
        <div className="space-y-0.5 pt-1.5 border-t border-foreground/10" data-testid={`${testId}-why`}>
          <div className="text-[10px] uppercase tracking-wider opacity-70">
            {lang === 'en' ? 'Why?' : '¿Por qué?'}
          </div>
          <ul className="space-y-0.5">
            {why.map((w, i) => (
              <li key={i} className="text-[12px] leading-snug opacity-95 flex gap-1.5">
                <span className="opacity-60">·</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Suggested protected market + Trap chip */}
      {(interpreter.suggested_market || (interpreter.trap && interpreter.trap.triggered)) && (
        <div className="flex flex-wrap gap-1.5 pt-1.5 border-t border-foreground/10">
          {interpreter.suggested_market && (
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium ${
                interpreter.suggested_market.includes('Over') ||
                interpreter.suggested_market.includes('BTTS') ||
                interpreter.suggested_market.includes('Ambos')
                  ? 'border-amber-500/40 bg-amber-500/10 text-amber-100'
                  : 'border-cyan-500/40 bg-cyan-500/10 text-cyan-100'
              }`}
              data-testid={`${testId}-suggested-market`}
            >
              {interpreter.suggested_market.includes('Over') ||
               interpreter.suggested_market.includes('BTTS') ||
               interpreter.suggested_market.includes('Ambos')
                ? <TrendingUp className="h-3 w-3" aria-hidden />
                : <Shield className="h-3 w-3" aria-hidden />
              }
              {interpreter.suggested_market.includes('Over') ||
               interpreter.suggested_market.includes('BTTS') ||
               interpreter.suggested_market.includes('Ambos')
                ? (lang === 'en' ? 'Offensive market: ' : 'Mercado ofensivo: ')
                : (lang === 'en' ? 'Protected market: ' : 'Mercado protegido: ')
              }
              <span className="font-semibold">{interpreter.suggested_market}</span>
            </span>
          )}
          {interpreter.trap && interpreter.trap.triggered && (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-red-500/40 bg-red-500/15 text-red-100 text-[11px] font-medium"
              data-testid={`${testId}-trap-warning`}
            >
              <AlertTriangle className="h-3 w-3" aria-hidden />
              {lang === 'en' ? 'Possible market trap' : 'Posible trampa de mercado'}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
