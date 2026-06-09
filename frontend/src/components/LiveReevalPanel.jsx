import { useState } from 'react';
import { api } from '@/lib/api';
import { normalizeDecimalOdds, normalizeManualOddsInput, isValidBookieOdds } from '@/lib/normalizeDecimalOdds';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Radio, RefreshCw, AlertCircle, TrendingUp, Hand, Eye, X, Pencil } from 'lucide-react';
import { LiveCopilotCard } from '@/components/LiveCopilotCard';
import { MatchOutcomeModal } from '@/components/MatchOutcomeModal';
import { BullpenTrafficBadge } from '@/components/BullpenTrafficBadge';
import { SiegePressureBadge } from '@/components/SiegePressureBadge';

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
  'Under 0.5', 'Under 1.5', 'Under 2.5', 'Under 3.5',
  'Over 0.5',  'Over 1.5',  'Over 2.5',  'Over 3.5',
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

// MLB — Baseball common live markets. Match the labels used by the
// HumanLiveInterpreter (TOTAL_UNDER_REMAINING / TOTAL_OVER_REMAINING /
// NRFI, etc.) so the engine can directly parse the manual market the
// user picks against the current pregame line.
const DEFAULT_MARKETS_BASEBALL = [
  'Total Runs: Over 7.5', 'Total Runs: Under 7.5',
  'Total Runs: Over 8.5', 'Total Runs: Under 8.5',
  'Total Runs: Over 9.5', 'Total Runs: Under 9.5',
  'F5 Total Runs: Over 4.5', 'F5 Total Runs: Under 4.5',
  'Run Line: home -1.5', 'Run Line: home +1.5',
  'Run Line: away -1.5', 'Run Line: away +1.5',
  'NRFI', 'YRFI',
];

function defaultMarketFor(sport) {
  if (sport === 'basketball') return 'Total: Over 215.5';
  if (sport === 'baseball')   return 'Total Runs: Under 8.5';
  return 'Under 2.5';
}
function marketsFor(sport) {
  if (sport === 'basketball') return DEFAULT_MARKETS_BASKETBALL;
  if (sport === 'baseball')   return DEFAULT_MARKETS_BASEBALL;
  return DEFAULT_MARKETS_FOOTBALL;
}

export function LiveReevalPanel({ match, lang = 'es', sport = 'football', testId }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  // Phase 41 / Fix 3 — Per-card error state. Surfaces inline so OTHER
  // cards on the screen keep working even when this one fails.
  const [error, setError] = useState(null);
  const [manualOdds, setManualOdds] = useState('');
  const [manualMarket, setManualMarket] = useState(defaultMarketFor(sport));
  const [useManual, setUseManual] = useState(false);
  // P4 — Tracking state. After the user gets a recommendation we let them
  // mark Gané / Perdí / Devolución, the same way Picks del día works.
  const [tracking, setTracking] = useState(false);
  const [trackedOutcome, setTrackedOutcome] = useState(null);
  // Fix 3 (Phase 39) — Source selection: after re-eval, the user can save
  // either (a) the engine recommendation as-is, or (b) their OWN pick.
  // The manual selection reuses the same `manualMarket` field from the
  // bookie-odds form so the UX is consistent.
  // Values: 'engine' | 'manual'.
  const [playSource, setPlaySource] = useState('engine');
  // Phase 42 / Fix 7 — Outcome modal lifecycle
  const [outcomeModalOpen, setOutcomeModalOpen] = useState(false);

  const matchId = match.match_id;

  // P5.1 — Re-eval timeouts:
  // - Path normal (sin cuota manual): 20s (rápido, basta para refresco live).
  // - Path con cuota manual: 15s. Acotado para evitar la sensación de
  //   "spinner infinito" que el usuario reportó; el backend resuelve
  //   en <5s salvo cuelgues reales. Mensaje de error en español ahora
  //   es inequívoco: "No se pudo recalcular la cuota. Intenta de nuevo".
  const REEVAL_TIMEOUT_NORMAL_MS = 20000;
  const REEVAL_TIMEOUT_MANUAL_MS = 15000;

  const run = async () => {
    setLoading(true);
    setError(null);   // Fix 3 — clear inline error each time
    try {
      const body = { match_id: matchId, sport, refresh: true };
      let timeoutMs = REEVAL_TIMEOUT_NORMAL_MS;
      if (useManual) {
        // Accept both "1.85" and "1,85" via the shared helper. The
        // manual-odds variant additionally clamps at > 1.01 (no value
        // below that floor can be priced by a bookmaker).
        const odds = normalizeManualOddsInput(manualOdds);
        if (odds === null || !isValidBookieOdds(manualOdds)) {
          toast.error(lang === 'en' ? 'Enter decimal odds > 1.01' : 'Ingresa cuota decimal > 1.01');
          setLoading(false);
          return;
        }
        body.manual_odds = odds;
        body.manual_market = manualMarket;
        timeoutMs = REEVAL_TIMEOUT_MANUAL_MS;
      }
      // Phase 41 / Fix 3 — Per-card endpoint. Same contract as the
      // legacy `/live/reevaluate` but the URL makes it explicit that
      // ONLY this card is being re-analyzed (no batch sweep). Backend
      // exposes both routes so older clients still work.
      const r = await api.post('/analysis/live/reevaluate-one', body, { timeout: timeoutMs });
      // Fix P0 — Backend fail-soft path. When the engine cannot
      // produce a verdict it returns HTTP 200 with `{ ok: false,
      // error, message, result: { live_state: 'REEVAL_FAILED', ... } }`.
      // We surface the message inline (no spinner persists) and DON'T
      // treat it as success.
      if (r.data && r.data.ok === false) {
        const failMsg = r.data.message
          || (lang === 'en'
              ? 'Re-evaluation could not be completed. Please try again.'
              : 'No se pudo completar la reevaluación. Intenta de nuevo.');
        toast.error(failMsg);
        setError(failMsg);
        // Keep result null so the UI doesn't render a misleading verdict.
        setResult(null);
        return;
      }
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
      let detail;
      if (err?.code === 'ECONNABORTED' || /timeout/i.test(err?.message || '')) {
        // Fix (P5.2): Mensaje claro de FALLO, no de loading continuo.
        // El texto previo "Estamos recalculando..." inducía al usuario
        // a creer que el spinner seguía activo. Ahora es inequívoco:
        // operación fallida + invitación explícita a reintentar.
        const usedManual = useManual;
        if (usedManual) {
          detail = lang === 'en'
            ? 'Could not recalculate with your manual odds. Please try again — your inputs are preserved.'
            : 'No se pudo recalcular la cuota. Intenta de nuevo (tus datos siguen ingresados).';
        } else {
          detail = lang === 'en'
            ? 'Re-evaluation timed out. Try again in a moment.'
            : 'La reevaluación tardó más de lo esperado. Intenta nuevamente en unos segundos.';
        }
      } else if (raw && typeof raw === 'object') {
        detail = raw.message || raw.error || JSON.stringify(raw);
      } else {
        detail = raw || err?.message || (lang === 'en' ? 'Re-eval failed' : 'Re-evaluación falló');
      }
      // eslint-disable-next-line no-console
      console.error('[LIVE_REEVAL_ERROR]', { match_id: matchId, status: err?.response?.status, code: err?.code, error: err });
      toast.error(detail);
      // Fix 3 — surface error inline on THIS card so other cards
      // remain usable. UI clears it on next successful run().
      setError(typeof detail === 'string' ? detail : 'Error');
    } finally {
      setLoading(false);
    }
  };

  // P4 — Tracking: persist outcome through /api/picks/track so the user's
  // live re-eval picks show up in the same history as their daily picks.
  // We use a `run_id` prefixed with "live-reeval-" to keep them grouped
  // and searchable downstream.
  // Fix 3 (Phase 39) — playSource decides whether the saved pick mirrors
  // the engine recommendation or the user's own market+selection from
  // the manual-odds form. Source is propagated to the backend so the
  // history can show "Tuya" vs "Engine".
  const track = async (outcome) => {
    if (!result || tracking) return;
    setTracking(true);
    try {
      const runId = `live-reeval-${matchId}-${result.computed_at || Date.now()}`;
      // Resolve market + selection + odds based on source.
      const isManual = playSource === 'manual';
      const useEngineMarket  = !isManual ? (result.market || 'Live') : (manualMarket || 'Live');
      const useEngineSel     = !isManual ? (result.selection || result.market || 'Live') : (manualMarket || 'Live');
      const useOdds          = isManual
        ? (normalizeDecimalOdds(manualOdds) ?? result.decimal_odds)
        : result.decimal_odds;
      // Snapshot the live-entry context so we can reconstruct WHEN the
      // user pulled the trigger even if the score changes later.
      const snap = result.live_snapshot || {};
      const entryMinute = snap.minute != null ? Number(snap.minute) : null;
      const entryScoreHome = snap.score?.home != null ? Number(snap.score.home) : null;
      const entryScoreAway = snap.score?.away != null ? Number(snap.score.away) : null;
      const entryScoreDisplay =
        entryScoreHome != null && entryScoreAway != null
          ? `${entryScoreHome}-${entryScoreAway}` : null;
      await api.post('/picks/track', {
        run_id: runId,
        match_id: String(matchId),
        match_label: `${match?.home_team?.name || 'Home'} vs ${match?.away_team?.name || 'Away'}`,
        league: match?.league || '',
        market: useEngineMarket,
        selection: useEngineSel,
        confidence_score: result.confidence ?? 0,
        outcome,                       // 'won' | 'lost' | 'push' | 'void' | 'cancelled' | 'refund'
        odds: useOdds,
        notes: `Live re-eval @ ${snap.minute ?? '—'} · ${useEngineMarket}${isManual ? ' · selección propia' : ''}`,
        sport,
        is_live: true,
        source: isManual ? 'manual' : 'engine',
        entry_minute: entryMinute,
        entry_score_home: entryScoreHome,
        entry_score_away: entryScoreAway,
        entry_score_display: entryScoreDisplay,
      });
      setTrackedOutcome(outcome);
      const labels = {
        won:       lang === 'en' ? 'Marked as WON'    : 'Marcado como GANÉ',
        lost:      lang === 'en' ? 'Marked as LOST'   : 'Marcado como PERDÍ',
        push:      lang === 'en' ? 'Marked as PUSH'   : 'Marcado como DEVOLUCIÓN',
        void:      lang === 'en' ? 'Marked as VOID'   : 'Marcado como VOID',
        cancelled: lang === 'en' ? 'Marked as CANCELLED' : 'Marcada como CANCELADA',
        refund:    lang === 'en' ? 'Marked as REFUND' : 'Marcada como REEMBOLSO',
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

      {/* Fix 3 (Phase 41) — Inline per-card error banner. Stays visible
          until the next successful run() clears it. Other cards on
          the screen remain usable. */}
      {error && !loading && (
        <div
          className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-2.5 py-1.5 text-[11px] text-red-200"
          role="alert"
          data-testid={`reeval-error-${matchId}`}
        >
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span className="leading-snug">{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="ml-auto opacity-70 hover:opacity-100"
            aria-label={lang === 'en' ? 'Dismiss error' : 'Cerrar error'}
            data-testid={`reeval-error-dismiss-${matchId}`}
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

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
              type="text"
              inputMode="decimal"
              pattern="[0-9]+([.,][0-9]+)?"
              placeholder={lang === 'en' ? 'Decimal odds, e.g. 1.85 or 1,85' : 'Cuota decimal, ej. 1.85 o 1,85'}
              value={manualOdds}
              onChange={(e) => {
                // Allow only digits + one decimal separator (. or ,).
                // Do NOT validate on every keystroke — the user might
                // be in the middle of typing "1," before the decimals.
                const v = e.target.value.replace(/[^0-9.,]/g, '');
                setManualOdds(v);
              }}
              onBlur={(e) => {
                // Lazy validation only on blur — mobile keyboards finish
                // entry then. Empty string is acceptable (treated as
                // "no manual cuota").
                const raw = (e.target.value || '').trim();
                if (!raw) return;
                if (normalizeManualOddsInput(raw) === null) {
                  toast.error(lang === 'en'
                    ? 'Invalid decimal odds (must be > 1.01)'
                    : 'Cuota inválida (debe ser > 1.01)');
                }
              }}
              disabled={!useManual}
              data-testid={`reeval-manual-odds-${matchId}`}
              className="h-9 text-xs"
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck="false"
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
          {result.interpreter ? (
            <LiveCopilotCard
              interpreter={result.interpreter}
              lang={lang}
              testId={`reeval-copilot-${matchId}`}
            />
          ) : (
            /* Fallback cuando el backend no retornó interpreter:
               muestra un card mínimo derivado del resultado del reeval
               para que el usuario siempre vea una recomendación legible */
            <div
              className={`rounded-lg border px-3 py-2.5 space-y-1 text-sm ${
                result.live_state === 'LIVE_VALUE_WINDOW' || result.live_state === 'MARKET_OVERREACTION'
                  ? 'border-emerald-500/40 bg-emerald-500/10'
                  : result.live_state === 'NO_LIVE_VALUE' || result.live_state === 'TRAP_DETECTED'
                  ? 'border-red-500/40 bg-red-500/10'
                  : 'border-amber-500/40 bg-amber-500/10'
              }`}
              data-testid={`reeval-fallback-copilot-${matchId}`}
            >
              <div className="font-semibold">
                {result.live_state === 'LIVE_VALUE_WINDOW'   && '✅ Valor live detectado'}
                {result.live_state === 'MARKET_OVERREACTION' && '✅ Sobre-reacción del mercado'}
                {result.live_state === 'WATCHLIST'           && '👀 En observación'}
                {result.live_state === 'MOMENTUM_SHIFT'      && '⚡ Cambio de momentum'}
                {result.live_state === 'NO_LIVE_VALUE'       && '⛔ Sin valor live'}
                {result.live_state === 'TRAP_DETECTED'       && '⚠️ Trampa detectada'}
                {result.live_state === 'HOLD_RECOMMENDED'    && '🤚 Mantener posición'}
                {result.live_state === 'CASH_OUT_RECOMMENDED' && '💰 Cash-out recomendado'}
                {!['LIVE_VALUE_WINDOW','MARKET_OVERREACTION','WATCHLIST',
                   'MOMENTUM_SHIFT','NO_LIVE_VALUE','TRAP_DETECTED',
                   'HOLD_RECOMMENDED','CASH_OUT_RECOMMENDED'].includes(result.live_state)
                  && (result.live_state || 'Reevaluación completada')}
              </div>
              {result.reason && (
                <p className="text-xs text-muted-foreground leading-relaxed">{result.reason}</p>
              )}
            </div>
          )}
          {/* Phase 44 — Bullpen + Traffic verdict (MLB only). Renders only
              when the live reeval response carries the verdict payload. */}
          {result.bullpen_traffic && (
            <BullpenTrafficBadge
              data={result.bullpen_traffic}
              lang={lang}
              testId={`reeval-bullpen-traffic-${matchId}`}
            />
          )}
          {/* Phase 45 — Football Siege Pressure Guard verdict.
              Renders only when the live reeval response carries the verdict. */}
          {result.siege_pressure?.siege_pressure_high && (
            <SiegePressureBadge
              data={result.siege_pressure}
              lang={lang}
              testId={`reeval-siege-pressure-${matchId}`}
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
                <span>{lang === 'en' ? 'Min' : 'Min'}. {result.live_snapshot.minute}&apos;</span>
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

          {/* Fix 3 (Phase 39) — Source selector: engine recommendation
              vs user's own manual selection. The buttons below save
              with the chosen source. The manual market reuses the value
              from the bookie-odds form (above) so the UX is consistent. */}
          {!trackedOutcome && (
            <div
              className="rounded-md border border-cyan-500/20 bg-cyan-500/5 px-2 py-2 space-y-1.5"
              data-testid={`reeval-source-toggle-${matchId}`}
            >
              <div className="text-[10px] uppercase tracking-wider opacity-70">
                {lang === 'en' ? 'Save this play as' : 'Guardar esta jugada como'}
              </div>
              <div className="flex flex-wrap gap-2 text-[11px]">
                <label className="inline-flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name={`source-${matchId}`}
                    value="engine"
                    checked={playSource === 'engine'}
                    onChange={() => setPlaySource('engine')}
                    data-testid={`reeval-source-engine-${matchId}`}
                    className="accent-cyan-400"
                  />
                  <span>
                    {lang === 'en' ? 'Engine recommendation' : 'Recomendación del engine'}
                    {result.market && (
                      <span className="ml-1 opacity-70">({result.market})</span>
                    )}
                  </span>
                </label>
                <label className="inline-flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name={`source-${matchId}`}
                    value="manual"
                    checked={playSource === 'manual'}
                    onChange={() => setPlaySource('manual')}
                    data-testid={`reeval-source-manual-${matchId}`}
                    className="accent-amber-400"
                  />
                  <span>
                    {lang === 'en' ? 'My own selection' : 'Mi selección propia'}
                    {playSource === 'manual' && manualMarket && (
                      <span className="ml-1 opacity-70">({manualMarket})</span>
                    )}
                  </span>
                </label>
              </div>
              {playSource === 'manual' && (
                <p className="text-[10px] text-amber-300/80 italic">
                  {lang === 'en'
                    ? 'Pick your market from the "paste your bookie odds" form above before saving.'
                    : 'Elige tu mercado en el formulario "pegar cuota de tu bookie" arriba antes de guardar.'}
                </p>
              )}
            </div>
          )}

          {/* Phase 42 / Fix 7 — "Marcar resultado" launcher.
              Opens the dedicated MatchOutcomeModal so the user can pick
              the outcome AND record their actual bet (market / line /
              odds). The inline tracking buttons below remain as a
              quick path for users who don't want the modal. */}
          {!trackedOutcome && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOutcomeModalOpen(true)}
              className="h-7 text-[11px] border-fuchsia-500/40 hover:bg-fuchsia-500/15 text-fuchsia-200 w-full"
              data-testid={`reeval-open-outcome-modal-${matchId}`}
            >
              <Pencil className="h-3 w-3 mr-1.5" />
              {lang === 'en' ? 'Mark outcome (advanced)' : 'Marcar resultado (avanzado)'}
            </Button>
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
                  trackedOutcome === 'cancelled' ? 'bg-slate-500/15 text-slate-200 border border-slate-500/40' :
                                              'bg-amber-500/15 text-amber-200 border border-amber-500/40'
                }`}
                data-testid={`reeval-tracked-${matchId}`}
                data-outcome={trackedOutcome}
              >
                <span>
                  {trackedOutcome === 'won'       && (lang === 'en' ? '✅ Marked as WON' : '✅ Marcado como GANÉ')}
                  {trackedOutcome === 'lost'      && (lang === 'en' ? '❌ Marked as LOST' : '❌ Marcado como PERDÍ')}
                  {trackedOutcome === 'push'      && (lang === 'en' ? '↩ Marked as PUSH' : '↩ Marcado como DEVOLUCIÓN')}
                  {trackedOutcome === 'void'      && (lang === 'en' ? '↩ Marked as VOID' : '↩ Marcado como VOID')}
                  {trackedOutcome === 'cancelled' && (lang === 'en' ? '⊘ Marked as CANCELLED' : '⊘ Marcada como CANCELADA')}
                  {trackedOutcome === 'refund'    && (lang === 'en' ? '⊘ Marked as REFUND' : '⊘ Marcada como REEMBOLSO')}
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
                <Button
                  size="sm"
                  variant="outline"
                  disabled={tracking}
                  onClick={() => track('cancelled')}
                  className="h-7 text-[11px] border-slate-500/40 hover:bg-slate-500/15 text-slate-200"
                  data-testid={`reeval-track-cancelled-${matchId}`}
                >
                  ⊘ {lang === 'en' ? 'Cancelled' : 'Cancelada'}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Phase 42 / Fix 7 — Outcome modal (recoge actual bet + result).
          Use a changing key so the modal re-mounts (and resets internal
          state) every time it opens with a different result snapshot. */}
      {result && (
        <MatchOutcomeModal
          key={`outcome-${matchId}-${result.computed_at || result.market || ''}`}
          open={outcomeModalOpen}
          onOpenChange={setOutcomeModalOpen}
          match={match}
          engineRec={{
            market:     result.market,
            selection:  result.selection,
            line:       result.line,
            odds:       result.decimal_odds,
            projection: result.expected_value || result.projection,
            confidence: result.confidence,
            is_live:    true,
          }}
          apiClient={api}
          lang={lang}
          onConfirmed={({ payload }) => {
            // Mirror the inline tracked-outcome banner so the user sees
            // the same confirmation regardless of which path they used.
            setTrackedOutcome(payload?.outcome || 'won');
          }}
        />
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
