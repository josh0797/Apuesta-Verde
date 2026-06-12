import { useState } from 'react';
import {
  Search,
  ChevronDown,
  ChevronUp,
  CircleSlash,
  CircleCheck,
  TrendingDown,
  TrendingUp,
  AlertTriangle,
  ShieldOff,
  ListChecks,
} from 'lucide-react';

/**
 * FootballMarketAuditPanel — V4 — Explicit market trace for discarded
 * football picks. Replaces the generic "Cuota baja (<1.40)" /
 * "Mercado protegido con edge -12.9%" messages with a fully explicit
 * audit so the user can answer the question:
 *
 *     "¿Cuál era el mercado de cuota 1.40?"
 *
 * Renders:
 *  1. Card header (e.g. "PSG Doble Oportunidad (1X) descartado por edge
 *     insuficiente (-12.9%)").
 *  2. Detail block with: Mercado / Cuota / Probabilidad estimada /
 *     Probabilidad implícita / Edge / Resultado / Motivo.
 *  3. "Auditoría completa de mercados evaluados" — table of every
 *     market the engine checked with ✓/✗ status, odds, edge,
 *     confidence and rejection reason.
 *
 * Consumes the per-discard payload emitted by the backend:
 *
 *   item.market_trace      = { market, selection, market_code, odds,
 *                              estimated_probability, implied_probability,
 *                              edge, edge_pct, fragility, confidence,
 *                              rejection_code, rejection_reason,
 *                              fragility_drivers, ... }
 *   item.markets_checked   = [ { market, selection, status, odds,
 *                                edge_pct, confidence, reason,
 *                                rejection_code } ... ]
 *   item.card_header       = pre-formatted header text
 */

const REJECTION_CODE_TONE = {
  EDGE_BELOW_MIN:           'amber',
  EDGE_BELOW_NEG_FLOOR:     'amber',
  PROTECTED_BELOW_FLOOR:    'amber',
  LOW_ODDS_NO_CUSHION:      'amber',
  NO_VALUE:                 'amber',
  FRAGILITY_HIGH:           'orange',
  FRAGILITY_TOO_HIGH:       'rose',
  MARKET_TRAP:              'rose',
  TRAP_SIGNALS:             'rose',
  PUBLIC_OVERREACTION:      'rose',
  WATCHLIST_ONLY:           'sky',
  UNKNOWN:                  'slate',
};

const TONE_CLASSES = {
  amber:   'bg-amber-500/10 text-amber-200 border-amber-500/30',
  orange:  'bg-orange-500/10 text-orange-200 border-orange-500/30',
  rose:    'bg-rose-500/10 text-rose-200 border-rose-500/30',
  sky:     'bg-sky-500/10 text-sky-200 border-sky-500/30',
  emerald: 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  slate:   'bg-slate-500/10 text-slate-200 border-slate-500/30',
};

function fmtPct(value, { signed = false, digits = 1 } = {}) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
}

function fmtProb(p) {
  if (p == null || Number.isNaN(Number(p))) return '—';
  return `${(Number(p) * 100).toFixed(1)}%`;
}

function fmtOdds(o) {
  if (o == null || Number.isNaN(Number(o))) return '—';
  return Number(o).toFixed(2);
}

/**
 * FootballMarketTraceHeader — compact line that goes on top of the
 * existing "Descartados por mercado frágil" row, replacing the generic
 * "Cuota baja (<1.40)" sentence.
 */
export function FootballMarketTraceHeader({ trace, cardHeader, testIdPrefix }) {
  if (!trace || typeof trace !== 'object') return null;
  const code = trace.rejection_code || 'UNKNOWN';
  const tone = REJECTION_CODE_TONE[code] || 'slate';
  const cls  = TONE_CLASSES[tone] || TONE_CLASSES.slate;
  const headerText = cardHeader || trace.rejection_reason || 'Pick descartado';
  return (
    <div
      className={`flex items-start gap-2 px-2.5 py-1.5 rounded-md border text-[11px] ${cls}`}
      data-testid={`${testIdPrefix}-fmt-header`}
    >
      <ShieldOff className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span className="font-medium leading-snug" data-testid={`${testIdPrefix}-fmt-header-text`}>
        {headerText}
      </span>
    </div>
  );
}


/**
 * FootballMarketTraceDetail — the 6-field detail block:
 *   Mercado / Cuota / Prob estimada / Prob implícita / Edge / Motivo.
 * Always rendered when the trace is present.
 */
export function FootballMarketTraceDetail({ trace, testIdPrefix }) {
  if (!trace || typeof trace !== 'object') return null;
  const edgePct = trace.edge_pct;
  const edgeIsNegative = edgePct != null && Number(edgePct) < 0;
  const edgeTone = edgeIsNegative
    ? 'text-rose-300'
    : (edgePct != null && Number(edgePct) > 0 ? 'text-emerald-300' : 'text-foreground/80');

  const code = trace.rejection_code || 'UNKNOWN';
  const codeTone = REJECTION_CODE_TONE[code] || 'slate';
  const codeCls  = TONE_CLASSES[codeTone] || TONE_CLASSES.slate;

  // 6 explicit rows the user asked for.
  const rows = [
    {
      key:   'market',
      label: 'Mercado evaluado',
      value: trace.selection
        ? (
          <span className="flex items-center gap-2 flex-wrap justify-end">
            <span className="font-medium text-foreground/95 text-right">{trace.selection}</span>
            {trace.market_code && trace.market_code !== 'UNKNOWN' ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.05] border border-border/40 text-muted-foreground font-mono">
                {trace.market_code}
              </span>
            ) : null}
            {trace.market_identity_key &&
             !String(trace.market_identity_key).startsWith('UNKNOWN:') ? (
              <span
                className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-200 font-mono"
                data-testid={`${testIdPrefix || ''}-market-identity-key`}
                title={`Market identity: ${trace.market_identity_key}`}
              >
                {trace.market_identity_key}
              </span>
            ) : null}
          </span>
        )
        : (trace.market_display && trace.market_display !== '—'
            ? (
              <span className="flex items-center gap-2 flex-wrap justify-end">
                <span className="font-medium text-foreground/95 text-right">
                  {trace.market_display}
                </span>
                {trace.market_identity_key &&
                 !String(trace.market_identity_key).startsWith('UNKNOWN:') ? (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-200 font-mono"
                    title={`Market identity: ${trace.market_identity_key}`}
                  >
                    {trace.market_identity_key}
                  </span>
                ) : null}
              </span>
            )
            : (trace.market || '—')),
    },
    {
      key:   'odds',
      label: 'Cuota',
      value: <span className="font-mono tabular-nums">{fmtOdds(trace.odds)}</span>,
    },
    {
      key:   'est-prob',
      label: 'Probabilidad estimada',
      value: <span className="font-mono tabular-nums">{fmtProb(trace.estimated_probability)}</span>,
    },
    {
      key:   'imp-prob',
      label: 'Probabilidad implícita',
      value: <span className="font-mono tabular-nums">{fmtProb(trace.implied_probability)}</span>,
    },
    {
      key:   'edge',
      label: 'Edge',
      value: (
        <span className={`font-mono tabular-nums font-semibold ${edgeTone}`}>
          {edgePct != null ? fmtPct(edgePct, { signed: true }) : '—'}
        </span>
      ),
    },
    {
      key:   'confidence',
      label: 'Confianza',
      value: <span className="font-mono tabular-nums">{trace.confidence ?? '—'}{trace.confidence != null ? '/100' : ''}</span>,
    },
    {
      key:   'fragility',
      label: 'Fragilidad',
      value: <span className="font-mono tabular-nums">{trace.fragility ?? '—'}{trace.fragility != null ? '/100' : ''}</span>,
    },
  ];

  return (
    <div
      className="rounded-lg border border-border/50 bg-white/[0.02] px-3 py-2.5 space-y-2"
      data-testid={`${testIdPrefix}-fmt-detail`}
    >
      {/* Result badge + label */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Search className="h-3 w-3" />
          Trace del mercado evaluado
        </span>
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] uppercase tracking-wide border ${codeCls}`}
          data-testid={`${testIdPrefix}-fmt-rejection-code`}
        >
          <CircleSlash className="h-3 w-3" />
          DESCARTADO · {code.replace(/_/g, ' ').toLowerCase()}
        </span>
      </div>

      {/* 7-field table (Mercado / Cuota / Prob est / Prob imp / Edge / Conf / Frag) */}
      <div className="divide-y divide-border/40">
        {rows.map((r) => (
          <div
            key={r.key}
            className="flex items-center justify-between gap-3 py-1 text-[12px]"
            data-testid={`${testIdPrefix}-fmt-row-${r.key}`}
          >
            <span className="text-muted-foreground">{r.label}</span>
            <span className="text-foreground/95 text-right">{r.value}</span>
          </div>
        ))}
      </div>

      {/* Motivo (explicit) */}
      {trace.rejection_reason ? (
        <div
          className="rounded-md border border-border/40 bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-foreground/80 leading-snug border-l-2 border-l-amber-500/50"
          data-testid={`${testIdPrefix}-fmt-reason`}
        >
          <span className="text-muted-foreground mr-1">Motivo:</span>
          {trace.rejection_reason}
        </div>
      ) : null}

      {/* Fragility drivers (explicit, not the generic line) */}
      {Array.isArray(trace.fragility_drivers) && trace.fragility_drivers.length > 0 ? (
        <div data-testid={`${testIdPrefix}-fmt-drivers`}>
          <div className="text-[10px] uppercase tracking-wide text-orange-300/90 font-semibold mb-0.5">
            Drivers de fragilidad
          </div>
          <ul className="text-[11px] text-muted-foreground space-y-0.5">
            {trace.fragility_drivers.slice(0, 6).map((d, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5"
                data-testid={`${testIdPrefix}-fmt-driver-${i}`}
              >
                <AlertTriangle className="h-3 w-3 text-orange-400/80 mt-0.5 shrink-0" />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}


/**
 * FootballMarketsCheckedTable — full "Auditoría completa de mercados
 * evaluados" panel. Expandable. Lists every market the engine checked,
 * including the alternatives the user could review manually.
 */
export function FootballMarketsCheckedTable({ marketsChecked, testIdPrefix }) {
  const [open, setOpen] = useState(false);
  if (!Array.isArray(marketsChecked) || marketsChecked.length === 0) return null;

  const rejected = marketsChecked.filter((m) => m.status === 'rejected');
  const review   = marketsChecked.filter((m) => m.status === 'selected_for_review');

  return (
    <div
      className="rounded-lg border border-border/50 bg-white/[0.015] overflow-hidden"
      data-testid={`${testIdPrefix}-fmt-audit-root`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-white/[0.03] transition-colors text-left"
        aria-expanded={open}
        data-testid={`${testIdPrefix}-fmt-audit-toggle`}
      >
        <span className="flex items-center gap-2 text-[12px] font-medium">
          <ListChecks className="h-3.5 w-3.5 text-cyan-300/80" />
          Auditoría completa de mercados evaluados
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.05] border border-border/40 text-muted-foreground tabular-nums">
            {marketsChecked.length}
          </span>
        </span>
        {open ? (
          <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>

      {open ? (
        <div className="px-3 pb-3 pt-1 space-y-2 border-t border-border/40">
          {/* Rejected markets */}
          {rejected.length > 0 ? (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-rose-300/90 font-semibold flex items-center gap-1.5">
                <TrendingDown className="h-3 w-3" />
                Mercados descartados ({rejected.length})
              </div>
              <ul className="space-y-1">
                {rejected.map((m, i) => (
                  <li
                    key={`rej-${i}`}
                    className="flex flex-wrap items-center justify-between gap-2 px-2.5 py-1.5 rounded border border-rose-500/25 bg-rose-500/[0.04]"
                    data-testid={`${testIdPrefix}-fmt-audit-rejected-${i}`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <CircleSlash className="h-3 w-3 text-rose-400 shrink-0" />
                      <span className="text-[12px] font-medium text-foreground/95 truncate">
                        {m.selection ? `${m.selection} · ` : ''}{m.market}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] tabular-nums shrink-0">
                      <span className="text-muted-foreground">
                        Cuota <span className="font-mono text-foreground/90 ml-0.5">{fmtOdds(m.odds)}</span>
                      </span>
                      {m.edge_pct != null ? (
                        <span className={`font-mono ${Number(m.edge_pct) < 0 ? 'text-rose-300' : 'text-emerald-300'}`}>
                          {fmtPct(m.edge_pct, { signed: true })}
                        </span>
                      ) : null}
                      {m.confidence != null ? (
                        <span className="text-muted-foreground">
                          Conf <span className="font-mono text-foreground/90 ml-0.5">{m.confidence}</span>
                        </span>
                      ) : null}
                    </div>
                    {m.reason ? (
                      <div className="w-full text-[10.5px] text-muted-foreground leading-snug pl-5">
                        {m.reason}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Markets selected for manual review */}
          {review.length > 0 ? (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-emerald-300/90 font-semibold flex items-center gap-1.5">
                <TrendingUp className="h-3 w-3" />
                Alternativas para revisión manual ({review.length})
              </div>
              <ul className="space-y-1">
                {review.map((m, i) => (
                  <li
                    key={`alt-${i}`}
                    className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded border border-emerald-500/25 bg-emerald-500/[0.04]"
                    data-testid={`${testIdPrefix}-fmt-audit-review-${i}`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <CircleCheck className="h-3 w-3 text-emerald-400 shrink-0" />
                      <span className="text-[12px] font-medium text-foreground/95 truncate">
                        {m.market}
                      </span>
                    </div>
                    <span className="text-[10px] text-muted-foreground italic">
                      revisar manualmente
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}


/**
 * FootballMarketAuditPanel — wraps the 3 sub-panels into a single
 * convenient component for `DiscardedRow`. Renders nothing when the
 * trace payload is absent (e.g. non-football sports).
 */
export function FootballMarketAuditPanel({ item, testIdPrefix = 'fmt' }) {
  const trace = item?.market_trace || null;
  const markets = Array.isArray(item?.markets_checked) ? item.markets_checked : [];
  const cardHeader = item?.card_header || null;
  if (!trace && markets.length === 0) return null;
  return (
    <div className="space-y-2" data-testid={`${testIdPrefix}-fmt-root`}>
      <FootballMarketTraceHeader
        trace={trace}
        cardHeader={cardHeader}
        testIdPrefix={testIdPrefix}
      />
      <FootballMarketTraceDetail trace={trace} testIdPrefix={testIdPrefix} />
      <FootballMarketsCheckedTable
        marketsChecked={markets}
        testIdPrefix={testIdPrefix}
      />
    </div>
  );
}
