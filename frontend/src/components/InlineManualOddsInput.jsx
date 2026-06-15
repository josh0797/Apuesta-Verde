import { useState } from 'react';
import { Calculator, Loader2, RefreshCcw, RotateCw, Bug, X } from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';

/**
 * Normalize a team value that may arrive as a string or as a vendor
 * object ({ name, displayName, team_name, … }). Returns ``undefined``
 * when the value is empty so the backend lookup can fall back to the
 * other identifiers.
 */
function normalizeTeamName(team) {
  if (!team) return undefined;
  if (typeof team === 'string') return team || undefined;
  if (typeof team === 'object') {
    return team.name || team.displayName || team.team_name || undefined;
  }
  return undefined;
}

/**
 * Build the trimmed `pick_context` blob the backend understands. We
 * forward only the fields the reprice cascade cares about so the payload
 * stays small and the server side stays decoupled from React state.
 */
function buildPickContext(p) {
  if (!p || typeof p !== 'object') return undefined;
  return {
    id:                          p.id || p.pick_id,
    match_id:                    p.match_id,
    game_pk:                     p.game_pk || p.gamePk,
    match_label:                 p.match_label,
    home_team:                   normalizeTeamName(p.home_team),
    away_team:                   normalizeTeamName(p.away_team),
    commence_date:               (p.commence_time || '').slice(0, 10)
                                  || p.commence_date,
    recommendation:              p.recommendation,
    confidence_score:            p.recommendation?.confidence_score
                                 ?? p.confidence_score,
    key_data:                    p.key_data,
    _mlb_script_v2:              p._mlb_script_v2,
    margin_v2:                   p.margin_v2,
    expected_runs_distribution:  p.expected_runs_distribution,
    baseballHistoricalProfile:   p.baseballHistoricalProfile,
    inning_lambda_projection:    p.inning_lambda_projection,
    tail_risk:                   p.tail_risk,
    market_profile:              p.market_profile,
    model_probability:           p.model_probability,
    cover_probability:           p.cover_probability,
    probability:                 p.probability,
  };
}

/**
 * InlineManualOddsInput  (MLB-F93 / F93.1)
 *
 * F93.1: passes the full `pickContext` blob; routes "Ver debug" through
 * the authenticated api client (inline panel); never opens a raw URL.
 */
export function InlineManualOddsInput({
  pickId,
  matchId,
  gamePk,
  homeTeam,
  awayTeam,
  commenceDate,
  market,
  selection,
  line,
  pickContext,
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
  const [debugOpen,    setDebugOpen]    = useState(false);
  const [debugLoading, setDebugLoading] = useState(false);
  const [debugData,    setDebugData]    = useState(null);
  const [debugError,   setDebugError]   = useState(null);

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
        home_team:        normalizeTeamName(homeTeam),
        away_team:        normalizeTeamName(awayTeam),
        commence_date:    commenceDate,
        market,
        selection,
        line:             typeof line === 'number' ? line : undefined,
        pick_context:     buildPickContext(pickContext),
      });
      setResult(r.data);

      if (typeof onReprice === 'function'
          && (r.data?.status === 'REPRICED'
              || r.data?.status === 'OVERRIDE_SAVED_ONLY')) {
        try { onReprice(r.data); } catch { /* ignore subscriber errors */ }
      }

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

  const openDebug = async () => {
    setDebugOpen(true);
    setDebugLoading(true);
    setDebugError(null);
    setDebugData(null);
    try {
      const r = await api.get('/mlb/manual-odds/debug', {
        params: {
          match_id: matchId || undefined,
          pick_id:  pickId  || undefined,
          game_pk:  gamePk ? String(gamePk) : undefined,
        },
      });
      setDebugData(r.data);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401 || status === 403) {
        const msg = lang === 'en'
          ? 'Session expired or missing authorization to view debug.'
          : 'Sesión expirada o falta autorización para ver debug.';
        setDebugError(msg);
        toast.error(msg);
      } else if (status === 422) {
        const msg = err?.response?.data?.detail?.message_user
          || (lang === 'en'
              ? 'Missing match_id / pick_id / game_pk.'
              : 'Falta match_id, pick_id o game_pk.');
        setDebugError(msg);
      } else {
        setDebugError(
          err?.response?.data?.detail
          || err?.message
          || (lang === 'en' ? 'Debug fetch failed.' : 'Falló la consulta de debug.'),
        );
      }
    } finally {
      setDebugLoading(false);
    }
  };

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
    // F93.1 — when OVERRIDE_SAVED_ONLY, force the visible label to
    // "SOLO INFO" — never NO VALUE — because there is no reprice basis.
    if (result?.status === 'OVERRIDE_SAVED_ONLY') {
      return lang === 'en' ? 'INFO ONLY' : 'SOLO INFO';
    }
    if (decision === 'VALUE')             return 'VALUE';
    if (decision === 'NO_VALUE')          return lang === 'en' ? 'NO VALUE' : 'NO VALUE';
    if (decision === 'WATCHLIST')         return 'WATCHLIST';
    if (decision === 'MANUAL_ODDS_ONLY')  return lang === 'en' ? 'INFO ONLY' : 'SOLO INFO';
    return decision;
  })();

  const edgePct = result?.reprice?.edge_pct ?? result?.manual_edge_pct;
  const showEdge = typeof edgePct === 'number'
                  && !Number.isNaN(edgePct)
                  && result?.status !== 'OVERRIDE_SAVED_ONLY';

  const isSavedOnly = result?.status === 'OVERRIDE_SAVED_ONLY';
  const isRepriced  = result?.status === 'REPRICED';

  const handleRefresh    = () => {
    if (typeof onRefreshCard === 'function') onRefreshCard();
    else if (typeof window !== 'undefined') window.location.reload();
  };
  const handleRegenerate = () => {
    if (typeof onRegenerate === 'function') onRegenerate();
    else if (typeof window !== 'undefined') window.location.reload();
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
              onClick={openDebug}
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

      {debugOpen && (
        <div
          className="rounded border border-cyan-500/25 bg-background/95 p-2 mt-1 flex flex-col gap-2"
          data-testid={`${testId || 'inline-manual-odds-input'}-debug-panel`}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="text-[11px] font-semibold text-cyan-200">
              {lang === 'en' ? 'Manual odds debug' : 'Debug de cuotas manuales'}
            </div>
            <button
              type="button"
              onClick={() => setDebugOpen(false)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
              data-testid={`${testId || 'inline-manual-odds-input'}-debug-close`}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          {debugLoading && (
            <div className="text-[10.5px] text-muted-foreground inline-flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              {lang === 'en' ? 'Loading…' : 'Cargando…'}
            </div>
          )}
          {debugError && (
            <div
              className="text-[10.5px] text-rose-200"
              data-testid={`${testId || 'inline-manual-odds-input'}-debug-error`}
            >
              {debugError}
            </div>
          )}
          {debugData && !debugLoading && (
            <pre
              className="text-[10px] font-mono whitespace-pre-wrap break-all bg-secondary/40 p-2 rounded max-h-56 overflow-auto"
              data-testid={`${testId || 'inline-manual-odds-input'}-debug-content`}
            >
              {JSON.stringify(debugData, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default InlineManualOddsInput;
