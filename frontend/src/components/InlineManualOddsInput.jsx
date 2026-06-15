import { useState } from 'react';
import { Calculator, Loader2, RefreshCcw, RotateCw, Bug } from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';

/**
 * InlineManualOddsInput  (MLB-F93)
 * ---------------------------------
 * Inline replacement for the "Cuota aprox.: —" line on MLB pick cards.
 *
 * Posts to `POST /api/mlb/picks/{pick_id}/manual-odds` and consumes the
 * F93 response contract (`status` + `reprice`) while keeping back-compat
 * with the legacy fields (`value_status`, `manual_edge_pct`,
 * `fallback_override_created`, …) that downstream components may still
 * inspect.
 *
 * Visual states:
 *   • saving   → loading copy "Recalculando con cuota manual…"
 *   • REPRICED → coloured status pill with decision + edge%
 *   • OVERRIDE_SAVED_ONLY → amber notice + action buttons
 *                          (Refrescar, Regenerar, Ver debug)
 *   • PICK_NOT_FOUND / ERROR → red notice + retry hint
 *
 * Communication with the parent card (MatchCard):
 *   • Prop `onReprice(payload)` fires once the backend responds with any
 *     non-error status. The card uses the payload to refresh its
 *     headline odds / VALUE-NO VALUE badge / edge / EV inline.
 */
export function InlineManualOddsInput({
  pickId,
  matchId,
  gamePk,
  homeTeam,
  awayTeam,
  commenceDate,
  market,
  line,
  lang = 'es',
  testId,
  onReprice,
  onRefreshCard,
  onRegenerate,
}) {
  const [value,  setValue]   = useState('');
  const [saving, setSaving]  = useState(false);
  const [result, setResult]  = useState(null);
  const [error,  setError]   = useState(null);

  if (!pickId) return null;

  const ranLabel = lang === 'en' ? 'Add manual odds' : 'Agregar cuota manual';
  const help     = lang === 'en'
    ? 'Paste your bookie odds to compute edge'
    : 'Pega la cuota de tu bookie para calcular edge';

  const submit = async () => {
    if (saving || !value) return;
    const normalized = String(value).trim().replace(',', '.');
    setSaving(true);
    setError(null);
    try {
      const r = await api.post(`/mlb/picks/${pickId}/manual-odds`, {
        manual_odds:      normalized,
        promote_if_value: false,
        match_id:         matchId,
        game_pk:          gamePk ? String(gamePk) : undefined,
        home_team:        homeTeam,
        away_team:        awayTeam,
        commence_date:    commenceDate,
        market,
        line:             typeof line === 'number' ? line : undefined,
      });
      setResult(r.data);

      // Notify the parent card so it can refresh inline (cuota / badge /
      // edge / EV). We propagate even on OVERRIDE_SAVED_ONLY because the
      // card may want to show the manual odd as the displayed price.
      if (typeof onReprice === 'function'
          && (r.data?.status === 'REPRICED'
              || r.data?.status === 'OVERRIDE_SAVED_ONLY')) {
        try { onReprice(r.data); } catch { /* ignore subscriber errors */ }
      }

      // Status-specific toast.
      const decision = r.data?.reprice?.decision;
      const edgePct  = Number(r.data?.reprice?.edge_pct ?? r.data?.manual_edge_pct ?? 0);
      const fair     = r.data?.reprice?.fair_odds;
      const odd      = Number(r.data?.reprice?.manual_odd ?? r.data?.manual_odds ?? normalized);

      if (r.data?.status === 'REPRICED' && decision === 'VALUE') {
        toast.success(
          lang === 'en'
            ? `Now value at ${odd.toFixed(2)} (+${edgePct.toFixed(1)}% edge).`
            : `Ahora hay valor a ${odd.toFixed(2)} (+${edgePct.toFixed(1)}% edge).`,
        );
      } else if (r.data?.status === 'REPRICED' && decision === 'NO_VALUE') {
        toast.success(
          lang === 'en'
            ? `Saved: no value at ${odd.toFixed(2)}${fair ? ` (fair ${Number(fair).toFixed(2)})` : ''}.`
            : `Cuota aplicada: sigue sin valor a ${odd.toFixed(2)}${fair ? ` (fair ${Number(fair).toFixed(2)})` : ''}.`,
        );
      } else if (r.data?.status === 'REPRICED' && decision === 'WATCHLIST') {
        toast.success(
          lang === 'en'
            ? `Close to value at ${odd.toFixed(2)} — needs market confirmation.`
            : `Cerca de valor a ${odd.toFixed(2)} — falta confirmación del mercado.`,
        );
      } else if (r.data?.status === 'REPRICED') {
        toast.success(
          lang === 'en'
            ? `Saved at ${odd.toFixed(2)} — informational.`
            : `Cuota guardada a ${odd.toFixed(2)} — informativo.`,
        );
      } else if (r.data?.status === 'OVERRIDE_SAVED_ONLY') {
        toast.warning(
          lang === 'en'
            ? 'Odds saved, but could not reprice (pick context not found).'
            : 'Cuota guardada, pero no se pudo recalcular (contexto del pick no encontrado).',
        );
      } else {
        toast.error(
          lang === 'en' ? 'Could not save odds.' : 'No se pudo guardar la cuota.',
        );
      }
    } catch (err) {
      const detail = err?.response?.data?.detail
        || (lang === 'en' ? 'Could not save odds' : 'No se pudo guardar la cuota');
      setError(detail);
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  // ── Helpers for rendering ───────────────────────────────────────────
  const decision    = result?.reprice?.decision || result?.value_status;
  const decisionCls = decision === 'VALUE'
    ? 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10'
    : decision === 'WATCHLIST'
      ? 'text-amber-300 border-amber-500/40 bg-amber-500/10'
      : decision === 'NO_VALUE'
        ? 'text-rose-300 border-rose-500/40 bg-rose-500/10'
        : decision === 'MANUAL_ODDS_ONLY'
          ? 'text-cyan-200 border-cyan-500/30 bg-cyan-500/10'
          : 'text-muted-foreground border-border bg-secondary/40';

  const decisionLabel = (() => {
    if (!decision) return null;
    if (decision === 'VALUE')             return lang === 'en' ? 'VALUE'        : 'VALUE';
    if (decision === 'NO_VALUE')          return lang === 'en' ? 'NO VALUE'     : 'NO VALUE';
    if (decision === 'WATCHLIST')         return lang === 'en' ? 'WATCHLIST'    : 'WATCHLIST';
    if (decision === 'MANUAL_ODDS_ONLY')  return lang === 'en' ? 'INFO ONLY'    : 'SOLO INFO';
    return decision;
  })();

  const edgePct = result?.reprice?.edge_pct ?? result?.manual_edge_pct;
  const showEdge = typeof edgePct === 'number' && !Number.isNaN(edgePct);

  const isSavedOnly = result?.status === 'OVERRIDE_SAVED_ONLY';
  const isRepriced  = result?.status === 'REPRICED';

  // Saved-only CTAs.
  const handleRefresh    = () => {
    if (typeof onRefreshCard === 'function') onRefreshCard();
    else if (typeof window !== 'undefined') window.location.reload();
  };
  const handleRegenerate = () => {
    if (typeof onRegenerate === 'function') onRegenerate();
    else if (typeof window !== 'undefined') window.location.reload();
  };
  const handleDebug = () => {
    // Best-effort: open the debug endpoint in a new tab (auth cookies
    // travel with the request). Headers-based auth users can copy the
    // URL from the toast.
    if (typeof window !== 'undefined') {
      const url = `${(window.location?.origin || '')}/api/mlb/manual-odds/debug?match_id=${encodeURIComponent(matchId || '')}&pick_id=${encodeURIComponent(pickId || '')}&game_pk=${encodeURIComponent(gamePk || '')}`;
      window.open(url, '_blank', 'noopener');
    }
  };

  return (
    <div
      className="rounded-md border border-cyan-500/25 bg-cyan-500/[0.05] px-2.5 py-2 flex flex-col gap-2"
      data-testid={testId || 'inline-manual-odds-input'}
    >
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={`inline-odds-${pickId}`}
          className="text-[11px] text-cyan-200 flex items-center gap-1.5 shrink-0"
        >
          <Calculator className="h-3 w-3" />
          {ranLabel}
        </label>
        <input
          id={`inline-odds-${pickId}`}
          type="text"
          inputMode="decimal"
          placeholder={lang === 'en' ? '1.90 or 1,90' : '1.90 o 1,90'}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={saving}
          className="w-20 text-[12px] tabular-nums bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-cyan-500/40 disabled:opacity-60"
          data-testid={`${testId || 'inline-manual-odds-input'}-field`}
        />
        <button
          type="button"
          onClick={submit}
          disabled={saving || !value}
          className="text-[10.5px] px-2 py-1 rounded border border-cyan-500/30 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors inline-flex items-center gap-1"
          data-testid={`${testId || 'inline-manual-odds-input'}-submit`}
        >
          {saving ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />
              {lang === 'en' ? 'Recalculating…' : 'Recalculando…'}
            </>
          ) : (
            lang === 'en' ? 'Save' : 'Guardar'
          )}
        </button>

        {/* Decision pill — only when we got a non-error response. */}
        {result && decisionLabel && (
          <span
            className={`text-[10.5px] font-semibold tabular-nums px-1.5 py-0.5 rounded border ${decisionCls}`}
            data-testid={`${testId || 'inline-manual-odds-input'}-decision`}
          >
            {decisionLabel}
            {showEdge && (
              <span className="ml-1 opacity-80">
                ({Number(edgePct) >= 0 ? '+' : ''}{Number(edgePct).toFixed(1)}%)
              </span>
            )}
          </span>
        )}

        {!result && !saving && (
          <span className="text-[10px] text-muted-foreground/80 ml-auto">{help}</span>
        )}
        {saving && (
          <span
            className="text-[10px] text-cyan-200/90 ml-auto"
            data-testid={`${testId || 'inline-manual-odds-input'}-loading-msg`}
          >
            {lang === 'en' ? 'Recalculating with manual odds…' : 'Recalculando con cuota manual…'}
          </span>
        )}
      </div>

      {/* Status-specific descriptive line + actions. */}
      {result?.message_user && isRepriced && (
        <div
          className="text-[10.5px] text-cyan-100/90"
          data-testid={`${testId || 'inline-manual-odds-input'}-reprice-msg`}
        >
          {result.message_user}
        </div>
      )}

      {isSavedOnly && (
        <div
          className="rounded border border-amber-500/30 bg-amber-500/[0.06] p-2 flex flex-col gap-1.5"
          data-testid={`${testId || 'inline-manual-odds-input'}-saved-only`}
        >
          <div className="text-[10.5px] text-amber-100">
            {result.message_user
              || (lang === 'en'
                  ? 'Saved, but the pick context could not be located.'
                  : 'Cuota guardada, pero no se pudo recalcular esta card porque el pick no está en runs recientes.')}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={handleRefresh}
              className="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20 inline-flex items-center gap-1 transition-colors"
              data-testid={`${testId || 'inline-manual-odds-input'}-refresh`}
            >
              <RefreshCcw className="h-3 w-3" />
              {lang === 'en' ? 'Refresh card' : 'Refrescar card'}
            </button>
            <button
              type="button"
              onClick={handleRegenerate}
              className="text-[10px] px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20 inline-flex items-center gap-1 transition-colors"
              data-testid={`${testId || 'inline-manual-odds-input'}-regenerate`}
            >
              <RotateCw className="h-3 w-3" />
              {lang === 'en' ? 'Regenerate analysis' : 'Regenerar análisis'}
            </button>
            <button
              type="button"
              onClick={handleDebug}
              className="text-[10px] px-1.5 py-0.5 rounded border border-border text-muted-foreground hover:text-foreground hover:border-foreground/40 inline-flex items-center gap-1 transition-colors"
              data-testid={`${testId || 'inline-manual-odds-input'}-debug`}
            >
              <Bug className="h-3 w-3" />
              {lang === 'en' ? 'View debug' : 'Ver debug'}
            </button>
          </div>
        </div>
      )}

      {error && (
        <div
          className="text-[10.5px] text-rose-200"
          data-testid={`${testId || 'inline-manual-odds-input'}-error`}
        >
          {error}
        </div>
      )}
    </div>
  );
}

export default InlineManualOddsInput;
