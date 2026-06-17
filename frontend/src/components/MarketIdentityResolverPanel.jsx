import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Wand2, Loader2, CheckCircle2, AlertTriangle, ChevronRight } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Sprint E.1.1-d / UI step · Market Identity Resolver Panel.
 *
 * Sits ABOVE ``ManualMarketIdentityPanel``. Lets the operator hit a
 * single button ("Resolver con The Odds API") that calls
 *   POST /api/football/market-identity/resolve
 * and renders the result:
 *
 *   • RESOLVED  → shows the single candidate + "Usar este mercado".
 *   • AMBIGUOUS → shows every candidate with a per-row "Usar este".
 *   • NOT_FOUND / errors → shows a reason badge so the operator
 *     understands why the auto-resolver couldn't help.
 *
 * Clicking "Usar este" calls ``onApplyCandidate(candidate)`` so the
 * parent can pre-fill the manual form. observe_only — never mutates
 * the original detected_odd.
 *
 * Props:
 *   - matchId, detectedOdd, homeName, awayName     (required)
 *   - leagueName?, commenceTime?, sportKeyHint?    (optional)
 *   - onApplyCandidate(candidate)                  (callback)
 *   - testIdPrefix
 */
export const MarketIdentityResolverPanel = ({
  matchId, detectedOdd, homeName, awayName,
  leagueName, commenceTime, sportKeyHint,
  onApplyCandidate,
  testIdPrefix = 'market-identity-resolver',
}) => {
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState(null);
  const [error, setError]     = useState(null);

  const canResolve = !!matchId && detectedOdd != null && detectedOdd > 1.0
                      && !!homeName && !!awayName;

  const handleResolve = useCallback(async ({ useCache = true } = {}) => {
    if (!canResolve) {
      setError('Faltan datos del partido para invocar el resolver.');
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await axios.post(
        `${BACKEND_URL}/api/football/market-identity/resolve`,
        {
          match_id:       String(matchId),
          home_team:      homeName,
          away_team:      awayName,
          detected_price: Number(detectedOdd),
          commence_time:  commenceTime || null,
          league:         leagueName || null,
          sport_key_hint: sportKeyHint || null,
          use_cache:      useCache,
        },
      );
      setResult(res.data || null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Error al resolver.');
    } finally {
      setLoading(false);
    }
  }, [canResolve, matchId, homeName, awayName, detectedOdd,
       commenceTime, leagueName, sportKeyHint]);

  const status   = result?.resolution_status;
  const reason   = result?.reason_code;
  const ladder   = result?.tolerance_ladder;
  const best     = result?.best;
  const ambList  = Array.isArray(result?.ambiguous) ? result.ambiguous : [];
  const allCands = Array.isArray(result?.candidates) ? result.candidates : [];

  // Map confidence → badge tone (kept restrained per design rules).
  const confTone = {
    HIGH:   'bg-emerald-700/40 text-emerald-200 border-emerald-600/60',
    MEDIUM: 'bg-amber-700/40 text-amber-200 border-amber-600/60',
    LOW:    'bg-slate-700/40 text-slate-200 border-slate-600/60',
  };

  const handleApply = (cand) => {
    if (!cand || typeof onApplyCandidate !== 'function') return;
    onApplyCandidate({
      market:    cand.resolved_market,
      selection: cand.resolved_selection,
      line:      cand.resolved_line,
      apiPrice:  cand.api_price,
      bookmakerKey:   cand.bookmaker_key,
      bookmakerTitle: cand.bookmaker_title,
      confidence:     cand.confidence,
      delta:          cand.delta,
      source:         'MARKET_IDENTITY_RESOLVED_BY_THE_ODDS_API',
    });
  };

  return (
    <Card
      className="border-sky-700/40 bg-sky-900/10"
      data-testid={`${testIdPrefix}-panel`}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 text-sky-200">
          <Wand2 className="h-4 w-4" />
          Resolver identidad de mercado (The Odds API)
        </CardTitle>
        <p className="text-[11px] text-slate-300 mt-1">
          Compara la cuota detectada contra todos los mercados que The Odds API publica para
          este partido y propone a cuál pertenece. <span className="text-slate-400">observe_only</span>.
        </p>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => handleResolve({ useCache: true })}
            disabled={!canResolve || loading}
            data-testid={`${testIdPrefix}-resolve-btn`}
            className="bg-sky-700 hover:bg-sky-600 text-white"
          >
            {loading ? (
              <><Loader2 className="h-3 w-3 animate-spin mr-2" /> Resolviendo…</>
            ) : (
              <><Wand2 className="h-3 w-3 mr-2" /> Resolver con The Odds API</>
            )}
          </Button>
          {result && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => handleResolve({ useCache: false })}
              disabled={loading}
              data-testid={`${testIdPrefix}-retry-fresh-btn`}
              className="text-sky-200 hover:text-sky-100"
            >
              Reintentar (sin caché)
            </Button>
          )}
        </div>

        {!canResolve && (
          <div className="text-[11px] text-amber-300 flex items-center gap-2">
            <AlertTriangle className="h-3 w-3" />
            Faltan datos (match_id / equipos / cuota detectada) para invocar el resolver.
          </div>
        )}

        {error && (
          <div
            className="text-[11px] text-rose-300 flex items-center gap-2"
            data-testid={`${testIdPrefix}-error`}
          >
            <AlertTriangle className="h-3 w-3" />
            {String(error)}
          </div>
        )}

        {result && (
          <div className="text-[11px] text-slate-200 space-y-2"
                data-testid={`${testIdPrefix}-result`}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={
                status === 'RESOLVED'  ? 'bg-emerald-700/40 text-emerald-200 border-emerald-600/60' :
                status === 'AMBIGUOUS' ? 'bg-amber-700/40 text-amber-200 border-amber-600/60'    :
                status === 'CACHED'    ? 'bg-sky-700/40 text-sky-200 border-sky-600/60'           :
                                          'bg-slate-700/40 text-slate-200 border-slate-600/60'
              } data-testid={`${testIdPrefix}-status-badge`}>
                {status || 'UNKNOWN'}
              </Badge>
              {reason && (
                <span className="text-slate-400 font-mono">{reason}</span>
              )}
              {result.event_id && (
                <span className="text-slate-500 text-[10px]">event: {result.event_id}</span>
              )}
            </div>

            {ladder && (
              <p className="text-[10px] text-slate-400">
                Tolerancias: HIGH ≤ {ladder.high} · MEDIUM ≤ {ladder.medium} · LOW ≤ {ladder.low}
              </p>
            )}

            {/* Single-best resolution */}
            {status === 'RESOLVED' && best && (
              <CandidateRow
                cand={best}
                confTone={confTone}
                onApply={() => handleApply(best)}
                testId={`${testIdPrefix}-best`}
                primary
              />
            )}

            {/* Ambiguous: show every candidate */}
            {status === 'AMBIGUOUS' && ambList.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[11px] text-amber-200 mb-1">
                  Varios mercados coinciden — elegí cuál corresponde:
                </p>
                {ambList.map((c, i) => (
                  <CandidateRow
                    key={i}
                    cand={c}
                    confTone={confTone}
                    onApply={() => handleApply(c)}
                    testId={`${testIdPrefix}-amb-${i}`}
                  />
                ))}
              </div>
            )}

            {/* Empty / not-found */}
            {(status === 'NOT_FOUND' || status === 'MATCH_NOT_FOUND' ||
              status === 'API_UNAVAILABLE' || status === 'INVALID_INPUT') && (
              <p className="text-[11px] text-slate-400">
                The Odds API no devolvió una identidad concluyente. Podés intentar
                de nuevo más tarde o completar el mercado manualmente abajo.
              </p>
            )}

            {/* Diagnostic: extra candidates beyond the chosen ones */}
            {allCands.length > (ambList.length || (best ? 1 : 0)) && (
              <details className="text-[10px] text-slate-500">
                <summary className="cursor-pointer hover:text-slate-300">
                  Otros precios cercanos ({allCands.length} en total)
                </summary>
                <div className="mt-1 space-y-1">
                  {allCands.map((c, i) => (
                    <div
                      key={`raw-${i}`}
                      className="font-mono text-slate-400"
                      data-testid={`${testIdPrefix}-raw-${i}`}
                    >
                      {c.api_market} · {c.api_outcome_name}
                      {c.api_point != null ? ` (line=${c.api_point})` : ''}
                      {' '}@ {c.api_price} (Δ {c.delta}) — {c.bookmaker_title}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const CandidateRow = ({ cand, confTone, onApply, testId, primary }) => {
  const market = cand.resolved_market || '—';
  const sel    = cand.resolved_selection || '—';
  const line   = cand.resolved_line;
  return (
    <div
      className={`rounded-md border p-2 flex items-center gap-2
                   ${primary ? 'bg-emerald-900/15 border-emerald-700/40'
                              : 'bg-slate-900/40 border-slate-700/40'}`}
      data-testid={testId}
    >
      <Badge
        className={confTone[cand.confidence] || confTone.LOW}
        data-testid={`${testId}-conf`}
      >
        {cand.confidence || 'LOW'}
      </Badge>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-slate-100 font-mono truncate">
          {market} · {sel}{line != null ? ` · línea ${line}` : ''}
        </div>
        <div className="text-[10px] text-slate-400">
          {cand.api_market} @ {cand.api_price}
          {' · Δ '}{cand.delta}
          {cand.bookmaker_title ? ` · ${cand.bookmaker_title}` : ''}
        </div>
      </div>
      <Button
        size="sm"
        onClick={onApply}
        disabled={!cand.resolved_market || !cand.resolved_selection}
        data-testid={`${testId}-apply-btn`}
        className="bg-emerald-700 hover:bg-emerald-600 text-white"
      >
        <CheckCircle2 className="h-3 w-3 mr-1" />
        Usar este
        <ChevronRight className="h-3 w-3 ml-1" />
      </Button>
    </div>
  );
};

export default MarketIdentityResolverPanel;
