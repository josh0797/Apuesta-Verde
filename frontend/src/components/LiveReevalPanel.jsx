import { useState } from 'react';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Radio, RefreshCw, AlertCircle, TrendingUp, Hand, Eye, X } from 'lucide-react';
import { LiveCopilotCard } from '@/components/LiveCopilotCard';

/**
 * LiveReevalPanel — Phase 10 entry point on the LivePage.
 *
 * UX flow:
 *   1. Compact CTA: "Reevaluar ahora" button + optional manual odds form.
 *   2. POST /api/live/reevaluate { match_id, sport, manual_odds?, manual_market? }
 *   3. Render result: live_state badge + market/selection + edge + reason
 *      + confidence + risk_level + live snapshot (minute, score, momentum).
 *
 * Manual odds path: when the user pastes the cuota they see in their bookie,
 * the engine uses THAT as authoritative implied probability and reports
 * `manual_odds_used: true`. The UI marks it with a "cuota tuya" badge so
 * there's no ambiguity about which number was the input.
 */

// ─── State display ─────────────────────────────────────────────────────────
const STATE_META = {
  LIVE_VALUE_WINDOW:    { es: 'Ventana de valor', en: 'Live value window',  tone: 'emerald' },
  MARKET_OVERREACTION:  { es: 'Sobre-reacción',   en: 'Market overreaction', tone: 'cyan' },
  MOMENTUM_SHIFT:       { es: 'Cambio momentum',  en: 'Momentum shift',     tone: 'amber' },
  WATCHLIST:            { es: 'Watchlist',        en: 'Watchlist',          tone: 'amber' },
  HOLD_RECOMMENDED:     { es: 'Mantener',         en: 'Hold',               tone: 'cyan' },
  CASH_OUT_RECOMMENDED: { es: 'Cash-out',         en: 'Cash-out',           tone: 'amber' },
  NO_LIVE_VALUE:        { es: 'Sin valor live',   en: 'No live value',      tone: 'red' },
};
const ACTION_META = {
  BET:      { es: 'Apostar', en: 'Bet',     icon: TrendingUp },
  WATCH:    { es: 'Vigilar', en: 'Watch',   icon: Eye },
  HOLD:     { es: 'Mantener', en: 'Hold',   icon: Hand },
  CASH_OUT: { es: 'Cash-out', en: 'Cash-out', icon: X },
  PASS:     { es: 'Pasar',    en: 'Pass',     icon: X },
};
const TONE_BG = {
  emerald: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  cyan:    'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
  amber:   'border-amber-500/40 bg-amber-500/10 text-amber-200',
  red:     'border-red-500/40 bg-red-500/10 text-red-200',
};
const RISK_BG = {
  LOW:    'border-emerald-500/40 bg-emerald-500/15 text-emerald-200',
  MEDIUM: 'border-amber-500/40 bg-amber-500/15 text-amber-200',
  HIGH:   'border-red-500/40 bg-red-500/15 text-red-200',
};

const DEFAULT_MARKETS_FOOTBALL = [
  'Under 1.5', 'Under 2.5', 'Under 3.5',
  'Over 1.5',  'Over 2.5',  'Over 3.5',
  'Resultado Final: home', 'Resultado Final: draw', 'Resultado Final: away',
];

// P4 — Basketball markets per user spec: Money Line + Total Points + Spread.
// We seed a few common Total / Spread half-points so the dropdown is useful
// without forcing the user to type. Manual odds path still works for any.
const DEFAULT_MARKETS_BASKETBALL = [
  'Money Line: home', 'Money Line: away',
  'Total: Over 205.5', 'Total: Under 205.5',
  'Total: Over 215.5', 'Total: Under 215.5',
  'Total: Over 220.5', 'Total: Under 220.5',
  'Spread: home -3.5', 'Spread: away -3.5',
  'Spread: home -6.5', 'Spread: away -6.5',
];

function defaultMarketFor(sport) {
  return (sport === 'basketball') ? 'Total: Over 215.5' : 'Under 2.5';
}
function marketsFor(sport) {
  return (sport === 'basketball') ? DEFAULT_MARKETS_BASKETBALL : DEFAULT_MARKETS_FOOTBALL;
}

export function LiveReevalPanel({ match, lang = 'es', sport = 'football', testId }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [manualOdds, setManualOdds] = useState('');
  const [manualMarket, setManualMarket] = useState(defaultMarketFor(sport));
  const [useManual, setUseManual] = useState(false);
  // P4 — Tracking state. After the user gets a recommendation we let them
  // mark Gané / Perdí / Devolución, the same way Picks del día works.
  const [tracking, setTracking] = useState(false);
  const [trackedOutcome, setTrackedOutcome] = useState(null);

  const matchId = match.match_id;

  const run = async () => {
    setLoading(true);
    try {
      const body = { match_id: matchId, sport, refresh: true };
      if (useManual) {
        const odds = parseFloat(manualOdds);
        if (!odds || odds <= 1.01) {
          toast.error(lang === 'en' ? 'Enter decimal odds > 1.01' : 'Ingresa cuota decimal > 1.01');
          setLoading(false);
          return;
        }
        body.manual_odds = odds;
        body.manual_market = manualMarket;
      }
      const r = await api.post('/live/reevaluate', body);
      setResult(r.data?.result || null);
      // Reset tracking state every time we re-run.
      setTrackedOutcome(null);
      if (r.data?.result) {
        const s = r.data.result.live_state;
        if (s === 'LIVE_VALUE_WINDOW' || s === 'MARKET_OVERREACTION') {
          toast.success(lang === 'en' ? 'Live value detected' : 'Valor live detectado');
        }
      }
    } catch (err) {
      // Backend returns 409 with { detail: { error, message, live_state } }
      // for stale/finished matches. Surface the message string so the
      // toast doesn't render "[object Object]".
      const raw = err?.response?.data?.detail;
      const detail = (raw && typeof raw === 'object')
        ? (raw.message || raw.error || JSON.stringify(raw))
        : (raw || err?.message || (lang === 'en' ? 'Re-eval failed' : 'Re-evaluación falló'));
      // eslint-disable-next-line no-console
      console.error('[LIVE_REEVAL_ERROR]', { match_id: matchId, status: err?.response?.status, error: err });
      toast.error(detail);
    } finally {
      setLoading(false);
    }
  };

  // P4 — Tracking: persist outcome through /api/picks/track so the user's
  // live re-eval picks show up in the same history as their daily picks.
  // We use a `run_id` prefixed with "live-reeval-" to keep them grouped
  // and searchable downstream.
  const track = async (outcome) => {
    if (!result || tracking) return;
    setTracking(true);
    try {
      const runId = `live-reeval-${matchId}-${result.computed_at || Date.now()}`;
      await api.post('/picks/track', {
        run_id: runId,
        match_id: String(matchId),
        match_label: `${match?.home_team?.name || 'Home'} vs ${match?.away_team?.name || 'Away'}`,
        league: match?.league || '',
        market: result.market || 'Live',
        selection: result.selection || result.market || 'Live',
        confidence_score: result.confidence ?? 0,
        outcome,                       // 'won' | 'lost' | 'push' | 'pending'
        odds: result.decimal_odds,
        notes: `Live re-eval @ ${result.live_snapshot?.minute ?? '—'} · ${result.market}`,
        sport,
      });
      setTrackedOutcome(outcome);
      const labels = {
        won:  lang === 'en' ? 'Marked as WON' : 'Marcado como GANÉ',
        lost: lang === 'en' ? 'Marked as LOST' : 'Marcado como PERDÍ',
        push: lang === 'en' ? 'Marked as PUSH' : 'Marcado como DEVOLUCIÓN',
      };
      toast.success(labels[outcome] || 'Tracked');
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Track failed';
      toast.error(typeof detail === 'string' ? detail : 'No se pudo guardar el resultado');
      // eslint-disable-next-line no-console
      console.error('[LIVE_TRACK_ERROR]', err);
    } finally {
      setTracking(false);
    }
  };

  const stateMeta = result ? (STATE_META[result.live_state] || STATE_META.NO_LIVE_VALUE) : null;
  const actionMeta = result ? (ACTION_META[result.recommended_action] || ACTION_META.PASS) : null;
  const ActionIcon = actionMeta?.icon || Eye;

  return (
    <div className="rounded-lg border border-border bg-card/40 p-3 space-y-2" data-testid={testId || `live-reeval-${matchId}`}>
      {/* Trigger row */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          size="sm"
          variant="secondary"
          onClick={run}
          disabled={loading}
          data-testid={`reeval-btn-${matchId}`}
        >
          <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          {lang === 'en' ? 'Re-evaluate now' : 'Reevaluar ahora'}
        </Button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-xs text-cyan-300 hover:text-cyan-200 underline-offset-2 hover:underline"
          data-testid={`reeval-toggle-manual-${matchId}`}
        >
          {open
            ? (lang === 'en' ? '− hide manual odds' : '− ocultar cuota manual')
            : (lang === 'en' ? '+ paste your bookie odds' : '+ pegar cuota de tu bookie')}
        </button>
      </div>

      {/* Manual odds form */}
      {open && (
        <div className="rounded-md border border-cyan-500/20 bg-cyan-500/5 p-3 space-y-2" data-testid={`reeval-manual-form-${matchId}`}>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={useManual}
              onChange={(e) => setUseManual(e.target.checked)}
              data-testid={`reeval-use-manual-${matchId}`}
              className="accent-cyan-400"
            />
            <span>
              {lang === 'en'
                ? 'Use my bookie odds (more precise edge)'
                : 'Usar mi cuota del bookie (edge más preciso)'}
            </span>
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Select value={manualMarket} onValueChange={setManualMarket} disabled={!useManual}>
              <SelectTrigger className="h-9 text-xs" data-testid={`reeval-market-select-${matchId}`}>
                <SelectValue placeholder={lang === 'en' ? 'Market' : 'Mercado'} />
              </SelectTrigger>
              <SelectContent>
                {marketsFor(sport).map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              </SelectContent>
            </Select>
            <Input
              type="number"
              step="0.01"
              min="1.02"
              placeholder={lang === 'en' ? 'Decimal odds, e.g. 1.85' : 'Cuota decimal, ej. 1.85'}
              value={manualOdds}
              onChange={(e) => setManualOdds(e.target.value)}
              disabled={!useManual}
              data-testid={`reeval-manual-odds-${matchId}`}
              className="h-9 text-xs"
            />
          </div>
          <p className="text-[10px] text-muted-foreground italic">
            {lang === 'en'
              ? 'Optional. When set, the engine compares against THIS odds instead of the pre-match estimate.'
              : 'Opcional. Cuando lo activas, el motor compara contra ESTA cuota en vez de la estimación pre-match.'}
          </p>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-2" data-testid={`reeval-result-${matchId}`}>
          {/* P3.1 — Re-eval now ALWAYS surfaces a human-readable Copilot
              recommendation at the top of the result. Falls back to the
              raw badge row when interpreter is missing (defensive). */}
          {result.interpreter && (
            <LiveCopilotCard
              interpreter={result.interpreter}
              lang={lang}
              testId={`reeval-copilot-${matchId}`}
            />
          )}
          {/* Header badges */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold uppercase tracking-wide ${TONE_BG[stateMeta?.tone] || TONE_BG.amber}`} data-testid={`reeval-state-${matchId}`}>
              <Radio className="h-3 w-3 mr-1" />
              {(stateMeta || {})[lang] || result.live_state}
            </span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold ${RISK_BG[result.risk_level] || RISK_BG.MEDIUM}`}>
              {lang === 'en' ? 'Risk' : 'Riesgo'}: {result.risk_level}
            </span>
            {result.manual_odds_used && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-cyan-500/40 bg-cyan-500/15 text-cyan-100 text-[11px] font-semibold" data-testid={`reeval-manual-flag-${matchId}`}>
                {lang === 'en' ? 'Your bookie odds' : 'Cuota tuya'}
              </span>
            )}
          </div>

          {/* Action + metrics row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
            <Cell
              label={lang === 'en' ? 'Action' : 'Acción'}
              value={
                <span className="inline-flex items-center gap-1 font-semibold">
                  <ActionIcon className="h-3 w-3" />
                  {(actionMeta || {})[lang] || result.recommended_action}
                </span>
              }
              testId={`reeval-action-${matchId}`}
            />
            <Cell
              label={lang === 'en' ? 'Market' : 'Mercado'}
              value={<span className="font-mono-tabular">{result.market}</span>}
              testId={`reeval-market-${matchId}`}
            />
            <Cell
              label={lang === 'en' ? 'Edge' : 'Edge'}
              value={
                <span className={`font-mono-tabular font-semibold ${result.edge_pct >= 4 ? 'text-emerald-300' : result.edge_pct >= 1.5 ? 'text-cyan-300' : result.edge_pct <= -2 ? 'text-red-300' : 'text-amber-300'}`}>
                  {result.edge_pct >= 0 ? '+' : ''}{(result.edge_pct ?? 0).toFixed(2)}%
                </span>
              }
              testId={`reeval-edge-${matchId}`}
            />
            <Cell
              label={lang === 'en' ? 'Confidence' : 'Confianza'}
              value={<span className="font-mono-tabular">{result.confidence}/100</span>}
              testId={`reeval-confidence-${matchId}`}
            />
          </div>

          {/* Probabilities + odds */}
          <div className="text-[11px] text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              {lang === 'en' ? 'Estimated' : 'Estimada'}:{' '}
              <span className="font-mono-tabular text-foreground">
                {((result.estimated_probability ?? 0) * 100).toFixed(1)}%
              </span>
            </span>
            <span className="opacity-50">·</span>
            <span>
              {lang === 'en' ? 'Implied' : 'Implícita'}:{' '}
              <span className="font-mono-tabular text-foreground">
                {result.implied_probability != null ? ((result.implied_probability * 100).toFixed(1) + '%') : '—'}
              </span>
            </span>
            {result.decimal_odds && (
              <>
                <span className="opacity-50">·</span>
                <span>
                  {lang === 'en' ? 'Odds' : 'Cuota'}:{' '}
                  <span className="font-mono-tabular text-foreground">{result.decimal_odds}</span>
                </span>
              </>
            )}
          </div>

          {/* Reason */}
          <p className="text-xs leading-relaxed border-l-2 border-cyan-500/40 pl-3 text-muted-foreground">
            <AlertCircle className="inline h-3 w-3 mr-1 opacity-80" />
            {result.reason}
          </p>

          {/* Live snapshot footnote */}
          {result.live_snapshot && (
            <div className="text-[10px] text-muted-foreground/70 flex flex-wrap gap-x-2 gap-y-0.5 mt-1">
              {result.live_snapshot.minute != null && (
                <span>{lang === 'en' ? 'Min' : 'Min'}. {result.live_snapshot.minute}'</span>
              )}
              {result.live_snapshot.score && (
                <span>
                  {result.live_snapshot.score.home ?? 0}–{result.live_snapshot.score.away ?? 0}
                </span>
              )}
              {result.live_snapshot.momentum != null && (
                <span>
                  {lang === 'en' ? 'Momentum' : 'Momentum'}: {result.live_snapshot.momentum > 0 ? '🏠' : result.live_snapshot.momentum < 0 ? '✈︎' : '·'}{' '}
                  {Math.abs(result.live_snapshot.momentum)}/100
                </span>
              )}
              <span className="opacity-60">· {new Date(result.computed_at).toLocaleTimeString(lang === 'es' ? 'es-ES' : 'en-US')}</span>
            </div>
          )}

          {/* P4 — Outcome tracking (Gané / Perdí / Devolución) — same as
              Picks del día. Saved through /api/picks/track with a
              live-reeval prefix so they appear in the user's history. */}
          <div className="pt-2 mt-1 border-t border-border/60" data-testid={`reeval-track-${matchId}`}>
            {trackedOutcome ? (
              <div
                className={`flex items-center justify-between gap-2 text-[11px] font-medium px-2 py-1.5 rounded-md ${
                  trackedOutcome === 'won'  ? 'bg-emerald-500/15 text-emerald-200 border border-emerald-500/40' :
                  trackedOutcome === 'lost' ? 'bg-red-500/15 text-red-200 border border-red-500/40' :
                                              'bg-amber-500/15 text-amber-200 border border-amber-500/40'
                }`}
                data-testid={`reeval-tracked-${matchId}`}
                data-outcome={trackedOutcome}
              >
                <span>
                  {trackedOutcome === 'won' && (lang === 'en' ? '✅ Marked as WON' : '✅ Marcado como GANÉ')}
                  {trackedOutcome === 'lost' && (lang === 'en' ? '❌ Marked as LOST' : '❌ Marcado como PERDÍ')}
                  {trackedOutcome === 'push' && (lang === 'en' ? '↩ Marked as PUSH' : '↩ Marcado como DEVOLUCIÓN')}
                </span>
                <button
                  type="button"
                  onClick={() => setTrackedOutcome(null)}
                  className="text-[10px] opacity-70 hover:opacity-100 underline"
                  data-testid={`reeval-tracked-change-${matchId}`}
                >
                  {lang === 'en' ? 'Change' : 'Cambiar'}
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] uppercase tracking-wider opacity-70 mr-1">
                  {lang === 'en' ? 'Result of this pick' : 'Resultado de este pick'}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={tracking}
                  onClick={() => track('won')}
                  className="h-7 text-[11px] border-emerald-500/40 hover:bg-emerald-500/15 text-emerald-200"
                  data-testid={`reeval-track-won-${matchId}`}
                >
                  ✅ {lang === 'en' ? 'Won' : 'Gané'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={tracking}
                  onClick={() => track('lost')}
                  className="h-7 text-[11px] border-red-500/40 hover:bg-red-500/15 text-red-200"
                  data-testid={`reeval-track-lost-${matchId}`}
                >
                  ❌ {lang === 'en' ? 'Lost' : 'Perdí'}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={tracking}
                  onClick={() => track('push')}
                  className="h-7 text-[11px] border-amber-500/40 hover:bg-amber-500/15 text-amber-200"
                  data-testid={`reeval-track-push-${matchId}`}
                >
                  ↩ {lang === 'en' ? 'Push' : 'Devolución'}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Cell({ label, value, testId }) {
  return (
    <div className="rounded border border-border/60 bg-background/30 px-2 py-1" data-testid={testId}>
      <div className="text-[9px] uppercase opacity-70 leading-none">{label}</div>
      <div className="text-xs mt-0.5">{value}</div>
    </div>
  );
}
