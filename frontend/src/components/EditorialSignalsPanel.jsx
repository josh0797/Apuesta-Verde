import { useState, useMemo } from 'react';
import {
  AlertOctagon, ShieldCheck, Sparkles, TrendingDown, TrendingUp,
  Info, ChevronDown, ChevronUp,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';

/**
 * Canonical editorial-context signal panel.
 *
 * Renders the `editorial_context_signals` array (built by
 * backend/services/signal_aggregator.py) in a uniform way that respects
 * severity + signal_type and is accessible / mobile-friendly.
 *
 * Props
 * -----
 *   signals : Array<{ code, label, severity, category, signal_type,
 *                     explanation, impact, confidence }>
 *   variant : 'compact' (default) or 'expanded' — 'expanded' shows all
 *             signals open; 'compact' starts collapsed with a toggle.
 *   title   : optional override for the section header label.
 *   testId  : data-testid base.
 *   filter  : optional 'positive' | 'negative' | 'neutral' | undefined
 *             to show only signals of that signal_type.
 *   defaultOpen : initial open state for compact variant (default false).
 *   maxVisible  : for compact mode, max signals shown before "+N more".
 *
 * The component renders nothing when `signals` is empty / undefined.
 */

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function severityClass(sev) {
  return {
    critical: 'bg-red-600/20 text-red-200 border-red-600/40',
    high:     'bg-red-500/15 text-red-300 border-red-500/30',
    medium:   'bg-amber-500/15 text-amber-300 border-amber-500/30',
    low:      'bg-slate-500/15 text-slate-300 border-slate-500/30',
  }[sev] || 'bg-slate-500/15 text-slate-300 border-slate-500/30';
}

function typeIcon(signalType) {
  if (signalType === 'positive') {
    return <ShieldCheck className="h-3.5 w-3.5 text-emerald-300 shrink-0" />;
  }
  if (signalType === 'negative') {
    return <AlertOctagon className="h-3.5 w-3.5 text-red-300 shrink-0" />;
  }
  return <Info className="h-3.5 w-3.5 text-slate-400 shrink-0" />;
}

function typeBadgeClass(t) {
  return {
    positive: 'bg-emerald-500/12 text-emerald-300 border-emerald-500/30',
    negative: 'bg-red-500/12 text-red-300 border-red-500/30',
    neutral:  'bg-slate-500/12 text-slate-300 border-slate-500/30',
  }[t] || 'bg-slate-500/12 text-slate-300 border-slate-500/30';
}

function SignalItem({ signal, testId }) {
  return (
    <li
      className="flex items-start gap-2 rounded-md border border-border/60 bg-background/30 p-2"
      data-testid={`${testId}-${signal.code}`}
    >
      {typeIcon(signal.signal_type)}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
          <span className="text-xs font-medium text-foreground/90 break-words">
            {signal.label}
          </span>
          <span
            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide border ${severityClass(signal.severity)}`}
            data-testid={`${testId}-${signal.code}-severity`}
          >
            {signal.severity}
          </span>
          <span
            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide border ${typeBadgeClass(signal.signal_type)}`}
          >
            {signal.signal_type}
          </span>
          {typeof signal.confidence === 'number' && (
            <span className="text-[10px] mono font-mono-tabular text-muted-foreground border border-border rounded px-1.5">
              {signal.confidence}%
            </span>
          )}
        </div>
        {signal.explanation && (
          <div className="text-[11px] text-muted-foreground leading-snug break-words">
            {signal.explanation}
          </div>
        )}
        {signal.impact && (
          <div className="text-[11px] text-foreground/70 leading-snug mt-1 italic break-words">
            <span className="not-italic font-semibold text-foreground/80">
              ⤷ </span>
            {signal.impact}
          </div>
        )}
      </div>
    </li>
  );
}

export function EditorialSignalsPanel({
  signals,
  variant = 'compact',
  title,
  testId = 'editorial-signals',
  filter,
  defaultOpen = false,
  maxVisible = 6,
}) {
  const { lang } = useI18n();
  const filtered = useMemo(() => {
    if (!Array.isArray(signals)) return [];
    const f = filter ? signals.filter((s) => s.signal_type === filter) : signals;
    return [...f].sort((a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
    );
  }, [signals, filter]);

  const [open, setOpen] = useState(defaultOpen || variant === 'expanded');

  if (!filtered.length) return null;

  const headerLabel = title
    || (filter === 'positive'
        ? (lang === 'en' ? 'Why the engine likes it' : 'Por qué el engine sí lo considera')
        : filter === 'negative'
          ? (lang === 'en' ? 'Risk signals detected' : 'Señales de riesgo detectadas')
          : (lang === 'en' ? 'Signals found' : 'Señales encontradas'));

  const isCompact = variant === 'compact';
  const visibleList = isCompact && !open ? filtered.slice(0, 0) : filtered;
  const summaryText = `${filtered.length} ${lang === 'en' ? (filtered.length === 1 ? 'signal' : 'signals') : (filtered.length === 1 ? 'señal' : 'señales')}`;

  return (
    <div className="rounded-md border border-border/60 bg-card/40" data-testid={testId}>
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-secondary/30 transition-colors"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          {filter === 'positive'
            ? <Sparkles className="h-3.5 w-3.5 text-emerald-300 shrink-0" />
            : filter === 'negative'
              ? <TrendingDown className="h-3.5 w-3.5 text-red-300 shrink-0" />
              : <TrendingUp className="h-3.5 w-3.5 text-cyan-300 shrink-0" />}
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/85 truncate">
            {headerLabel}
          </span>
          <span className="text-[10px] mono font-mono-tabular text-muted-foreground border border-border rounded px-1.5">
            {summaryText}
          </span>
        </div>
        {open
          ? <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
          : <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />}
      </button>
      {open && (
        <ul className="px-3 pb-3 pt-1 space-y-1.5" data-testid={`${testId}-list`}>
          {visibleList.length === 0
            ? filtered.slice(0, maxVisible).map((s) => (
              <SignalItem key={s.code} signal={s} testId={testId} />
            ))
            : visibleList.map((s) => (
              <SignalItem key={s.code} signal={s} testId={testId} />
            ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Top-level summary chips for the dashboard "Señales detectadas hoy" strip.
 * Reads `summary.editorial_signal_summary` built by the backend.
 */
export function EditorialSignalsSummary({ summary, lang, testId = 'editorial-signal-summary' }) {
  if (!summary || !summary.total_signals) return null;

  const items = [
    {
      key: 'protected',
      value: summary.protected_market_signals || 0,
      label: lang === 'en' ? 'Protected markets' : 'Mercados protegidos',
      tone: 'emerald',
      icon: ShieldCheck,
    },
    {
      key: 'trap',
      value: summary.trap_signals || 0,
      label: lang === 'en' ? 'Market traps detected' : 'Trampas de mercado',
      tone: 'red',
      icon: AlertOctagon,
    },
    {
      key: 'historical',
      value: summary.historical_signals || 0,
      label: lang === 'en' ? 'Historical patterns' : 'Tendencias históricas',
      tone: 'cyan',
      icon: TrendingUp,
    },
    {
      key: 'positive',
      value: summary.positive_signals || 0,
      label: lang === 'en' ? 'Positive signals' : 'Señales positivas',
      tone: 'emerald',
      icon: Sparkles,
    },
    {
      key: 'negative',
      value: summary.negative_signals || 0,
      label: lang === 'en' ? 'Risk signals' : 'Señales de riesgo',
      tone: 'red',
      icon: TrendingDown,
    },
  ];

  const tones = {
    emerald: 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200',
    red:     'border-red-500/30 bg-red-500/5 text-red-200',
    cyan:    'border-cyan-500/30 bg-cyan-500/5 text-cyan-200',
    amber:   'border-amber-500/30 bg-amber-500/5 text-amber-200',
  };

  return (
    <section
      className="rounded-xl border border-border bg-card/40 p-3 md:p-4 space-y-3"
      data-testid={testId}
    >
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-cyan-300" />
        <span className="text-xs uppercase tracking-wide font-semibold text-foreground/85">
          {lang === 'en' ? 'Signals detected today' : 'Señales detectadas hoy'}
        </span>
        <span className="text-[11px] mono font-mono-tabular text-muted-foreground border border-border rounded px-1.5">
          {summary.total_signals}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        {items.map((it) => {
          const Icon = it.icon;
          return (
            <div
              key={it.key}
              className={`rounded-lg border p-2.5 flex items-start gap-2 ${tones[it.tone]}`}
              data-testid={`${testId}-${it.key}`}
            >
              <Icon className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="text-lg mono font-mono-tabular font-semibold leading-none">
                  {it.value}
                </div>
                <div className="text-[10px] opacity-80 mt-0.5 break-words leading-tight">
                  {it.label}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
