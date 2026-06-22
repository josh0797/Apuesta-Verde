import React, { useEffect, useState } from 'react';
import { Sparkles, ExternalLink, Activity, History, ChevronDown, ChevronUp } from 'lucide-react';

/**
 * Phase F70 — External Editorial Panel.
 *
 * Lazy-loads ``/api/football/external-editorial/by-teams`` and renders
 * the Forebet 1X2 / score prediction + Sportytrader recent results &
 * stats (if a sportytrader_url is known for the match).
 *
 * Fail-soft: when nothing returns, the panel renders nothing — it is
 * meant to *enrich* the internal editorial, not replace it.
 *
 * Props
 * -----
 *   homeTeam, awayTeam: strings used to query the backend.
 *   testIdPrefix: optional prefix for data-testid attributes.
 *   lang: 'es' | 'en' — labels.
 */
export function ExternalEditorialPanel({
  homeTeam,
  awayTeam,
  matchId = null,
  testIdPrefix = 'external-editorial',
  lang = 'es',
  onExternalLoaded = null,
}) {
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!homeTeam || !awayTeam) return;
    const backendUrl = process.env.REACT_APP_BACKEND_URL || '';
    // Phase F72 — prefer the match_id endpoint when available because
    // it triggers the full Forebet audit (direction + scoreline +
    // opponent strength). Fall back to by-teams which is faster but
    // skips the audit.
    const url = matchId
      ? `${backendUrl}/api/football/external-editorial/${encodeURIComponent(matchId)}`
      : `${backendUrl}/api/football/external-editorial/by-teams`
          + `?home=${encodeURIComponent(homeTeam)}`
          + `&away=${encodeURIComponent(awayTeam)}`;
    let cancelled = false;
    setState({ loading: true, data: null, error: null });
    fetch(url)
      .then(r => r.json())
      .then(data => {
        // F72 — if the matchId endpoint failed (e.g. stale run / not found),
        // retry against /by-teams so the UI still gets Forebet enrichment.
        if (matchId && data && data.available === false
            && Array.isArray(data.reason_codes)
            && data.reason_codes.includes('MATCH_NOT_FOUND_IN_LATEST_RUN')) {
          return fetch(`${backendUrl}/api/football/external-editorial/by-teams`
                       + `?home=${encodeURIComponent(homeTeam)}`
                       + `&away=${encodeURIComponent(awayTeam)}`)
                  .then(r => r.json());
        }
        return data;
      })
      .then(data => {
        if (!cancelled) {
          setState({ loading: false, data, error: null });
          if (typeof onExternalLoaded === 'function') {
            try { onExternalLoaded(data); } catch (_) { /* noop */ }
          }
        }
      })
      .catch(err => { if (!cancelled) setState({ loading: false, data: null, error: String(err) }); });
    return () => { cancelled = true; };
  }, [homeTeam, awayTeam, matchId]);

  if (state.loading) {
    return (
      <div
        className="rounded-lg border border-slate-700/40 bg-slate-900/30 px-3 py-2 text-xs text-slate-400 animate-pulse"
        data-testid={`${testIdPrefix}-loading`}
      >
        Cargando datos externos…
      </div>
    );
  }
  const data = state.data;
  if (!data || data.available === false) {
    // Fully silent when no external context — internal engine carries the load.
    return null;
  }

  const t = lang === 'en'
    ? {
        header: 'External context',
        sub: 'Forebet algorithmic prediction + Sportytrader recent results',
        forebetLabel: 'Forebet algorithm',
        sportytraderLabel: 'Sportytrader insight',
        openSporty: 'Open in Sportytrader',
        recent: 'Last results',
        statsAvg: 'Last 6 averages',
        score: 'Predicted score',
        pick: 'Recommended',
        toggleMore: 'See more',
        toggleLess: 'Show less',
      }
    : {
        header: 'Contexto externo',
        sub: 'Predicción algorítmica de Forebet + resultados recientes de Sportytrader',
        forebetLabel: 'Algoritmo Forebet',
        sportytraderLabel: 'Sportytrader',
        openSporty: 'Abrir en Sportytrader',
        recent: 'Últimos resultados',
        statsAvg: 'Promedio últimos 6',
        score: 'Marcador predicho',
        pick: 'Recomendado',
        toggleMore: 'Ver más',
        toggleLess: 'Ver menos',
      };

  const forebet = data.forebet || {};
  const sporty  = data.sportytrader || {};

  return (
    <div
      className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 px-3 py-3 space-y-3"
      data-testid={`${testIdPrefix}-root`}
    >
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-cyan-300" />
        <div className="text-xs font-semibold uppercase tracking-wider text-cyan-200">
          {t.header}
        </div>
      </div>
      <div className="text-[11px] text-cyan-300/70 -mt-2">{t.sub}</div>

      {/* Phase F72 — Forebet audit summary (direction + scoreline). */}
      <ForebetAuditSection
        audit={data.forebet_audit}
        testId={`${testIdPrefix}-audit`}
        lang={lang}
      />

      {/* ── Forebet algorithm ──────────────────────────────────────── */}
      {forebet && forebet.forebet_pct_1 != null ? (
        <div
          className="rounded-md border border-cyan-400/30 bg-cyan-400/5 px-3 py-2 text-xs space-y-1"
          data-testid={`${testIdPrefix}-forebet`}
        >
          <div className="flex items-center gap-2">
            <Activity className="h-3.5 w-3.5 text-cyan-300" />
            <span className="font-semibold text-cyan-100">{t.forebetLabel}</span>
          </div>
          <div className="grid grid-cols-3 gap-2 pt-1 font-mono text-center">
            <ForebetPill
              label="1" pct={forebet.forebet_pct_1}
              highlight={forebet.pick_1x2 === '1'}
              testId={`${testIdPrefix}-forebet-pct-1`} />
            <ForebetPill
              label="X" pct={forebet.forebet_pct_x}
              highlight={forebet.pick_1x2 === 'X'}
              testId={`${testIdPrefix}-forebet-pct-x`} />
            <ForebetPill
              label="2" pct={forebet.forebet_pct_2}
              highlight={forebet.pick_1x2 === '2'}
              testId={`${testIdPrefix}-forebet-pct-2`} />
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {forebet.predicted_score && (
              <span
                className="px-2 py-0.5 rounded font-mono text-[11px] bg-cyan-500/20 text-cyan-100 border border-cyan-500/40"
                data-testid={`${testIdPrefix}-forebet-score`}
              >
                {t.score}: {forebet.predicted_score}
              </span>
            )}
            {forebet.goals_avg && (
              <span
                className="px-2 py-0.5 rounded font-mono text-[11px] bg-slate-700/30 text-slate-200 border border-slate-600/40"
                data-testid={`${testIdPrefix}-forebet-goals-avg`}
              >
                Goles esperados: {forebet.goals_avg.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      ) : (
        /* F99-followup: placeholder "no encontrado" — el panel SIEMPRE se muestra,
           incluso cuando Forebet no devolvió predicción (por ejemplo amistosos o
           ligas pequeñas). El usuario pidió explícitamente este comportamiento. */
        <div
          className="rounded-md border border-cyan-400/20 bg-cyan-400/[0.03] px-3 py-2 text-xs"
          data-testid={`${testIdPrefix}-forebet-empty`}
        >
          <div className="flex items-center gap-2 text-cyan-300/70">
            <Activity className="h-3.5 w-3.5 text-cyan-400/60" />
            <span className="font-semibold text-cyan-200/80">{t.forebetLabel}</span>
            <span className="ml-auto text-[10px] uppercase tracking-wider text-cyan-300/60">
              {lang === 'en' ? 'Not found' : 'No encontrado'}
            </span>
          </div>
          <div className="text-[11px] text-cyan-300/60 pt-1">
            {lang === 'en'
              ? 'Forebet has no algorithmic prediction for this match.'
              : 'Forebet no tiene predicción algorítmica para este partido.'}
          </div>
        </div>
      )}

      {/* ── Sportytrader (when match URL is known) ─────────────────── */}
      {sporty && sporty.available && (
        <div
          className="rounded-md border border-cyan-400/30 bg-cyan-400/5 px-3 py-2 text-xs space-y-2"
          data-testid={`${testIdPrefix}-sportytrader`}
        >
          <div className="flex items-center gap-2">
            <History className="h-3.5 w-3.5 text-cyan-300" />
            <span className="font-semibold text-cyan-100">{t.sportytraderLabel}</span>
            {sporty.source_url && (
              <a
                href={sporty.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto inline-flex items-center gap-1 text-cyan-200 hover:text-cyan-100 text-[10px] underline"
                data-testid={`${testIdPrefix}-sportytrader-link`}
              >
                {t.openSporty}
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>
          {sporty.prediction?.final_prediction && (
            <div
              className="font-semibold text-cyan-50"
              data-testid={`${testIdPrefix}-sporty-final-prediction`}
            >
              {sporty.prediction.final_prediction}
            </div>
          )}
          {Array.isArray(sporty.team_stats) && sporty.team_stats.length === 2 && (
            <SportyStatsTable
              homeStats={sporty.team_stats[0]}
              awayStats={sporty.team_stats[1]}
              homeName={sporty.home_team}
              awayName={sporty.away_team}
              testId={`${testIdPrefix}-sporty-stats`}
              labels={t}
            />
          )}
          {Array.isArray(sporty.recent_results) && sporty.recent_results.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-semibold text-cyan-200">
                {t.recent}
              </div>
              <ul className="text-[11px] space-y-0.5 font-mono">
                {sporty.recent_results
                  .slice(0, expanded ? 12 : 4)
                  .map((r, i) => (
                    <li
                      key={`sr-${i}`}
                      className="text-slate-200 grid grid-cols-[80px_1fr_auto_1fr] gap-1"
                      data-testid={`${testIdPrefix}-sporty-recent-${i}`}
                    >
                      <span className="text-slate-500">{r.date}</span>
                      <span className="text-right truncate">{r.home_team}</span>
                      <span className={(
                        r.home_score > r.away_score ? 'text-emerald-300' :
                        r.home_score < r.away_score ? 'text-red-300' :
                        'text-slate-400'
                      )}>
                        {r.home_score}-{r.away_score}
                      </span>
                      <span className="truncate">{r.away_team}</span>
                    </li>
                  ))}
              </ul>
              {sporty.recent_results.length > 4 && (
                <button
                  type="button"
                  onClick={() => setExpanded(!expanded)}
                  className="inline-flex items-center gap-1 text-[10px] text-cyan-300 hover:text-cyan-100"
                  data-testid={`${testIdPrefix}-sporty-toggle-recent`}
                >
                  {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                  {expanded ? t.toggleLess : `${t.toggleMore} (${sporty.recent_results.length})`}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Sportytrader fallback search link ──────────────────────── */}
      {sporty && !sporty.available && sporty.search_url && (
        <a
          href={sporty.search_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[11px] text-cyan-300 hover:text-cyan-100 underline"
          data-testid={`${testIdPrefix}-sportytrader-search`}
        >
          {t.openSporty}
          <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </div>
  );
}


function ForebetPill({ label, pct, highlight, testId }) {
  return (
    <div
      className={`rounded px-1.5 py-1 border ${
        highlight
          ? 'bg-cyan-500/30 border-cyan-400 text-cyan-50 font-bold'
          : 'bg-slate-800/40 border-slate-700 text-slate-300'
      }`}
      data-testid={testId}
    >
      <div className="text-[10px] opacity-70">{label}</div>
      <div className="text-sm">{pct}%</div>
    </div>
  );
}


function SportyStatsTable({ homeStats, awayStats, homeName, awayName, testId, labels }) {
  if (!homeStats || !awayStats) return null;
  const rows = [
    ['Total de goles',     homeStats.total_goals_avg,    awayStats.total_goals_avg],
    ['Ambos marcan %',     homeStats.btts_pct + '%',     awayStats.btts_pct + '%'],
    ['Goles marcados',     homeStats.goals_scored_avg,   awayStats.goals_scored_avg],
    ['Goles recibidos',    homeStats.goals_conceded_avg, awayStats.goals_conceded_avg],
    ['Over 2.5 %',         homeStats.over_2_5_pct + '%',  awayStats.over_2_5_pct + '%'],
    ['Under 2.5 %',        homeStats.under_2_5_pct + '%', awayStats.under_2_5_pct + '%'],
  ];
  return (
    <div
      className="grid grid-cols-[1fr_auto_auto] gap-x-3 gap-y-0.5 text-[11px] font-mono"
      data-testid={testId}
    >
      <span className="text-cyan-200 font-semibold"></span>
      <span className="text-cyan-200 font-semibold text-right">{homeName}</span>
      <span className="text-cyan-200 font-semibold text-right">{awayName}</span>
      {rows.map(([k, h, a], i) => (
        <React.Fragment key={k}>
          <span className="text-slate-400">{k}</span>
          <span className="text-slate-100 text-right" data-testid={`${testId}-row-${i}-home`}>{h}</span>
          <span className="text-slate-100 text-right" data-testid={`${testId}-row-${i}-away`}>{a}</span>
        </React.Fragment>
      ))}
    </div>
  );
}



/**
 * Phase F72 — Forebet Audit Section.
 * Renders the direction signal + scoreline audit + opponent strength
 * verdicts so the user understands WHY the engine kept or degraded the
 * Forebet projection.
 */
function ForebetAuditSection({ audit, testId, lang = 'es' }) {
  if (!audit || audit.available !== true) return null;
  const direction  = audit.forebet_direction_signal || {};
  const scoreline  = audit.forebet_scoreline_audit || {};
  const opponent   = audit.opponent_strength_audit || {};
  const t = lang === 'en'
    ? { header: 'Forebet audit', directionLabel: 'Direction',
        scorelineLabel: 'Scoreline', opponentLabel: 'Opponent context' }
    : { header: 'Auditoría Forebet', directionLabel: 'Dirección',
        scorelineLabel: 'Marcador', opponentLabel: 'Contexto rival' };

  const STATUS_STYLE = {
    CONFIRMED:                     'bg-emerald-500/15 text-emerald-200 border-emerald-500/40',
    TRUSTED:                       'bg-emerald-500/15 text-emerald-200 border-emerald-500/40',
    WEAK_CONFIRMED:                'bg-amber-500/15 text-amber-200 border-amber-500/40',
    DEGRADED:                      'bg-amber-500/15 text-amber-200 border-amber-500/40',
    CONFLICTED:                    'bg-red-500/15 text-red-200 border-red-500/40',
    BLOCKED_FOR_AGGRESSIVE_MARKETS:'bg-red-500/15 text-red-200 border-red-500/40',
    INSUFFICIENT_DATA:             'bg-slate-500/15 text-slate-300 border-slate-500/40',
  };

  return (
    <div
      className="rounded-md border border-cyan-400/30 bg-slate-900/30 px-3 py-2 text-xs space-y-2"
      data-testid={`${testId}-root`}
    >
      <div className="text-[11px] font-semibold text-cyan-200">
        {t.header}
      </div>
      {direction.status && (
        <div className="flex flex-col gap-1" data-testid={`${testId}-direction`}>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400 w-16 shrink-0">{t.directionLabel}</span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                STATUS_STYLE[direction.status] || STATUS_STYLE.INSUFFICIENT_DATA
              }`}
              data-testid={`${testId}-direction-status`}
            >
              {direction.status.replace(/_/g, ' ')}
            </span>
          </div>
          {direction.text && (
            <p className="text-[11px] text-slate-300 pl-[4.5rem] leading-relaxed">
              {direction.text}
            </p>
          )}
        </div>
      )}
      {scoreline.status && (
        <div className="flex flex-col gap-1" data-testid={`${testId}-scoreline`}>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400 w-16 shrink-0">{t.scorelineLabel}</span>
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                STATUS_STYLE[scoreline.status] || STATUS_STYLE.INSUFFICIENT_DATA
              }`}
              data-testid={`${testId}-scoreline-status`}
            >
              {scoreline.status.replace(/_/g, ' ')}
            </span>
            {scoreline.block_aggressive_overs && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-red-500/20 text-red-200 border border-red-500/50"
                data-testid={`${testId}-scoreline-blocked`}
              >
                Over agresivo bloqueado
              </span>
            )}
          </div>
          {scoreline.text && (
            <p className="text-[11px] text-slate-300 pl-[4.5rem] leading-relaxed">
              {scoreline.text}
            </p>
          )}
        </div>
      )}
      {opponent.available && opponent.text && (
        <div className="flex flex-col gap-1" data-testid={`${testId}-opponent`}>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-400 w-16 shrink-0">{t.opponentLabel}</span>
            {opponent.goals_inflation_risk && opponent.goals_inflation_risk !== 'LOW' && (
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                  opponent.goals_inflation_risk === 'HIGH'
                    ? 'bg-red-500/15 text-red-200 border-red-500/40'
                    : 'bg-amber-500/15 text-amber-200 border-amber-500/40'
                }`}
                data-testid={`${testId}-opponent-risk`}
              >
                Inflación de goles: {opponent.goals_inflation_risk}
              </span>
            )}
          </div>
          <p className="text-[11px] text-slate-300 pl-[4.5rem] leading-relaxed">
            {opponent.text}
          </p>
        </div>
      )}
    </div>
  );
}
