/**
 * Scores24ReviewBadge — Phase F63
 *
 * Surfaces the external Scores24 review state for a discarded match.
 * Renders as a compact chip + optional details panel.
 *
 * State derivation:
 *   - review missing                               → "Scores24: pendiente"
 *   - review.available=false + URL_NOT_RESOLVED    → "Scores24: no encontrado"
 *   - review.available=false + DIRECT_SLUG_FAILED  → "Scores24: buscando URL"
 *   - review.available=false + FETCH_FAILED        → "Scores24: no encontrado"
 *   - review.available=true  + decision=CONFIRM    → "Scores24: descarte confirmado"
 *   - review.available=true  + decision=WATCHLIST  → "Scores24: revisión Watchlist"
 *   - review.available=true  + decision=RESCUE     → "Scores24: alternativa detectada"
 *
 * Color mapping respects the design tokens (no raw hex).
 */
import React from 'react';
import { Eye, Search, CheckCircle2, AlertTriangle, Sparkles, XCircle } from 'lucide-react';

const TONE = {
  pending:   { cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',     Icon: Search },
  searching: { cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200',     Icon: Search },
  reviewed:  { cls: 'border-slate-500/30 bg-slate-500/10 text-slate-200',  Icon: Eye },
  rescued:   { cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200', Icon: Sparkles },
  watchlist: { cls: 'border-amber-500/40 bg-amber-500/10 text-amber-200',  Icon: AlertTriangle },
  confirmed: { cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300',  Icon: CheckCircle2 },
  missing:   { cls: 'border-rose-500/30 bg-rose-500/10 text-rose-200',     Icon: XCircle },
};

// Sprint-D9 + Sprint-F99 follow-up: textos agnósticos al proveedor.  Antes
// decían literalmente "Sportytrader" (proveedor legacy bloqueado por Bright
// Data).  Tras el wiring de TheOddsAPI + OddsPortal (Sprint-D9) y editorial
// interno F99.3, la UI debe hablar de "Editorial externo" / "Auditoría",
// no referir al proveedor histórico.
const LABELS_ES = {
  pending:   'Editorial externo: pendiente',
  searching: 'Editorial externo: buscando',
  reviewed:  'Editorial externo: revisado',
  rescued:   'Auditoría: alternativa detectada',
  watchlist: 'Auditoría: revisión Watchlist',
  confirmed: 'Auditoría: descarte confirmado',
  missing:   'Editorial externo no disponible',
};

const LABELS_EN = {
  pending:   'External editorial: pending',
  searching: 'External editorial: searching',
  reviewed:  'External editorial: reviewed',
  rescued:   'Audit: alternative detected',
  watchlist: 'Audit: watchlist review',
  confirmed: 'Audit: discard confirmed',
  missing:   'External editorial unavailable',
};

/**
 * Map the raw review payload into one of the badge states above.
 */
export function deriveScores24State(review) {
  if (!review || typeof review !== 'object') return 'pending';
  const codes = Array.isArray(review.reason_codes) ? review.reason_codes : [];

  if (review.available === false) {
    if (codes.includes('SCORES24_URL_NOT_RESOLVED') || codes.includes('SCORES24_REVIEW_FETCH_FAILED')) {
      return 'missing';
    }
    if (codes.includes('SCORES24_DIRECT_SLUG_FAILED')) return 'searching';
    if (codes.includes('SCORES24_REVIEW_DISABLED_BY_ENV')) return 'confirmed';
    return 'missing';
  }

  // Available — branch on decision.
  switch (review.decision) {
    case 'RESCUE_ALTERNATIVE_MARKET': return 'rescued';
    case 'MOVE_TO_WATCHLIST':         return 'watchlist';
    case 'CONFIRM_DISCARD':           return 'confirmed';
    default:                          return 'reviewed';
  }
}

export function Scores24ReviewBadge({ review, lang = 'es', testIdPrefix = 'scores24-review' }) {
  const state = deriveScores24State(review);
  const tone = TONE[state] || TONE.pending;
  const labels = lang === 'en' ? LABELS_EN : LABELS_ES;
  const Icon = tone.Icon;
  const rescued = review?.rescued_market;

  return (
    <div className="flex flex-col gap-1" data-testid={`${testIdPrefix}-wrapper`}>
      <span
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${tone.cls}`}
        data-testid={`${testIdPrefix}-${state}`}
      >
        <Icon className="h-3 w-3" />
        <span>{labels[state]}</span>
      </span>
      {state === 'rescued' && rescued && (
        <div className="text-xs text-emerald-200/90 pl-1" data-testid={`${testIdPrefix}-rescued-detail`}>
          {lang === 'en' ? 'Alternative: ' : 'Alternativa: '}
          <span className="font-semibold">
            {rescued.market || `${rescued.side} ${rescued.line} ${rescued.market_family?.toLowerCase()}`}
          </span>
          {rescued.odds ? (
            <span className="text-emerald-300/70"> @ {Number(rescued.odds).toFixed(2)}</span>
          ) : null}
        </div>
      )}
      {state === 'watchlist' && review?.editorial_prediction?.market && (
        <div className="text-xs text-amber-200/90 pl-1" data-testid={`${testIdPrefix}-editorial`}>
          {lang === 'en' ? 'Editorial: ' : 'Redacción: '}
          <span className="font-semibold">{review.editorial_prediction.market}</span>
          {review.editorial_prediction.odds ? (
            <span className="text-amber-300/70"> @ {Number(review.editorial_prediction.odds).toFixed(2)}</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default Scores24ReviewBadge;
