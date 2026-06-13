import React, { useState } from 'react';
import { MapPin, Activity, Target, TrendingUp, ListChecks, History, Trophy, AlertTriangle, Info } from 'lucide-react';
import { PlayerHeatmapDialog } from '@/components/PlayerHeatmapDialog';
import { ExternalEditorialPanel } from '@/components/ExternalEditorialPanel';
import { H2HContextPanel } from '@/components/H2HContextPanel';
import { CornersRefreshPanel } from '@/components/CornersRefreshPanel';


/**
 * Phase F71 — Compute frontend reconciliation between the internal
 * editorial probable_score and Forebet's algorithmic score.
 *
 * Returns either:
 *   - null when no contradiction OR no external data.
 *   - { suppressInternal: true, externalScore, externalXG } when the
 *     UI should hide the internal chip and surface the external one.
 */
function reconcileScorelinesFrontend(internalScore, forebet) {
  if (!forebet || forebet.predicted_score == null) return null;
  if (!internalScore) {
    return {
      suppressInternal: false,
      externalScore: forebet.predicted_score,
      externalXG: forebet.goals_avg || forebet.expected_goals || null,
    };
  }
  const winner = (s) => {
    if (typeof s !== 'string') return null;
    const m = s.replace(/[\u2013\u2014]/g, '-').split('-');
    if (m.length !== 2) return null;
    const h = parseInt(m[0], 10), a = parseInt(m[1], 10);
    if (Number.isNaN(h) || Number.isNaN(a)) return null;
    if (h > a) return 'HOME';
    if (h < a) return 'AWAY';
    return 'DRAW';
  };
  const winInternal = winner(internalScore);
  const winExternal = winner(forebet.predicted_score);
  if (winInternal && winExternal && winInternal !== winExternal) {
    return {
      suppressInternal: true,
      externalScore: forebet.predicted_score,
      externalXG: forebet.goals_avg || forebet.expected_goals || null,
    };
  }
  return null;
}


export function EditorialPredictionPanel({
  editorial,
  testIdPrefix = 'editorial',
  lang = 'es',
  topPlayers = null,
  matchId = null,
  homeTeamName = null,
  awayTeamName = null,
}) {
  // Phase F71 — capture external Forebet/Sportytrader payload as it
  // arrives from the lazy-loaded child panel so we can reconcile the
  // internal probable_score (suppress contradictory "1-1 heuristic"
  // chips when Forebet predicts a different winner).
  const [externalPayload, setExternalPayload] = useState(null);
  if (!editorial || editorial.available === false) {
    return null;
  }
  const audit = editorial.internal_editorial_analysis || {};
  // Phase F74-post — internal_analysis_debug surfaces WHY this match
  // couldn't generate an editorial (recent_fixtures, TheStatsAPI,
  // market_identity availability). Always rendered as collapsible so the
  // operator can diagnose THIN-data cases without cluttering the UI.
  const debugBlock = editorial.internal_analysis_debug;
  const renderDebugCollapsible = () => {
    if (!debugBlock || typeof debugBlock !== 'object') return null;
    const flagLabel = (ok) => (ok
      ? <span className="text-emerald-300">✓</span>
      : <span className="text-rose-300">✗</span>);
    return (
      <details
        className="mt-2 rounded-md border border-slate-700/50 bg-slate-950/40 px-3 py-2 text-[11px] text-slate-400"
        data-testid={`${testIdPrefix}-internal-debug`}
      >
        <summary className="cursor-pointer select-none font-medium text-slate-300">
          Ver detalle del análisis
        </summary>
        <ul className="mt-2 space-y-1 pl-1">
          <li>{flagLabel(debugBlock.recent_fixtures_found)} Forma reciente encontrada</li>
          <li>{flagLabel(debugBlock.recent_fixtures_flattened)} Forma reciente normalizada para el editorial</li>
          <li>{flagLabel(debugBlock.thestatsapi_found)} TheStatsAPI / xG disponible</li>
          <li>{flagLabel(debugBlock.h2h_found)} H2H reciente cargado</li>
          <li>{flagLabel(debugBlock.market_identity_found)} Mercado identificado</li>
          <li className="pt-1 text-slate-400">
            <span className="font-medium">Calidad de datos:</span>{' '}
            <span className="text-slate-200">{debugBlock.data_quality || 'THIN'}</span>
          </li>
          {Array.isArray(debugBlock.missing) && debugBlock.missing.length > 0 && (
            <li className="pt-1 text-slate-400">
              <span className="font-medium">Faltantes:</span>{' '}
              <span className="text-amber-200">{debugBlock.missing.join(', ')}</span>
            </li>
          )}
        </ul>
      </details>
    );
  };

  // Phase F69 — when the agregator flagged the editorial as a generic
  // template, render an honest empty state instead of misleading content.
  if (audit.is_generic_fallback === true) {
    // Phase F74-post — texto específico cuando hay debug info: explicar
    // qué falta exactamente en lugar del genérico "Datos insuficientes".
    const specificMessages = [];
    if (debugBlock) {
      if (debugBlock.recent_fixtures_found && !debugBlock.recent_fixtures_flattened) {
        specificMessages.push('Forma reciente encontrada, pero no normalizada para el editorial.');
      } else if (!debugBlock.recent_fixtures_found) {
        specificMessages.push('Sin forma reciente (últimos 5/15 partidos) disponible.');
      }
      if (!debugBlock.thestatsapi_found) {
        specificMessages.push('TheStatsAPI no disponible o no mapeado.');
      }
      if (!debugBlock.market_identity_found) {
        specificMessages.push('Mercado no identificado.');
      }
    }
    return (
      <div
        className="rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-3 text-xs text-slate-400"
        data-testid={`${testIdPrefix}-generic-fallback`}
      >
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-300" />
          <span className="font-semibold text-slate-200">
            Análisis interno no disponible
          </span>
        </div>
        {specificMessages.length > 0 ? (
          <ul className="mt-1 list-disc pl-6 space-y-0.5" data-testid={`${testIdPrefix}-specific-reasons`}>
            {specificMessages.map((msg, i) => <li key={i}>{msg}</li>)}
          </ul>
        ) : (
          <p className="mt-1 pl-6">
            Datos insuficientes para generar una lectura específica del partido.
          </p>
        )}
        {renderDebugCollapsible()}
      </div>
    );
  }

  const secs = editorial.editorial_sections || {};
  const best = editorial.best_protected_market;
  const dataQuality = editorial.data_quality
    || editorial.data_completeness?.data_quality
    || null;
  const t = (lang === 'en')
    ? {
        header: 'Internal editorial analysis',
        sub:    'Fallback Sportytrader — generated from your own L5/L15 model',
        bestPrefix: 'Highlighted protected market',
      }
    : {
        header: 'Análisis editorial interno',
        sub:    'Fallback interno — Sportytrader no encontrado · generado desde tu modelo L5/L15',
        bestPrefix: 'Mercado protegido destacado',
      };

  return (
    <div
      className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-3 space-y-3"
      data-testid={`${testIdPrefix}-root`}
      data-data-quality={dataQuality || 'UNKNOWN'}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Activity className="h-4 w-4 text-emerald-300" />
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-200">
          {t.header}
        </div>
        {/* Phase F69 — data_quality (4-tier) pill */}
        {dataQuality && (
          <span
            className={`ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
              dataQuality === 'STRONG'
                ? 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30'
                : dataQuality === 'USABLE'
                  ? 'bg-cyan-500/15 text-cyan-200 border-cyan-500/30'
                  : dataQuality === 'LIMITED'
                    ? 'bg-amber-500/15 text-amber-200 border-amber-500/30'
                    : 'bg-slate-500/15 text-slate-300 border-slate-500/30'
            }`}
            title={`Calidad de datos: ${dataQuality} · Fuentes: ${(editorial.data_completeness?.available_sources || []).join(', ') || 'ninguna'}`}
            data-testid={`${testIdPrefix}-data-quality-pill`}
          >
            Calidad: {dataQuality}
          </span>
        )}
      </div>
      <div className="text-[11px] text-emerald-300/70 -mt-2">{t.sub}</div>

      {/* Phase F69 — discard reason narrative (market trap / edge insuf /
          fragile market). Always rendered first when present so the user
          sees WHY the pick was discarded with real numbers. */}
      <DiscardReasonSection
        section={secs.discard_reason_narrative}
        testId={`${testIdPrefix}-discard-reason`}
      />

      {/* Best protected market highlight */}
      {best && (
        <div
          className="rounded-md border border-emerald-400/40 bg-emerald-400/10 px-3 py-2 text-xs"
          data-testid={`${testIdPrefix}-best-market`}
        >
          <span className="text-emerald-200 font-medium">{t.bestPrefix}: </span>
          <span className="text-emerald-100 font-semibold">{best.market}</span>
          {best.odds && (
            <span className="text-emerald-200/80"> · cuota {best.odds.toFixed(2)}</span>
          )}
          <span className="text-emerald-300/70"> · confianza {best.confidence}/100</span>
        </div>
      )}

      <SubSection
        icon={Target}
        section={secs.corners_prediction}
        testId={`${testIdPrefix}-corners`}
      />
      <SubSection
        icon={Trophy}
        section={secs.goals_prediction}
        testId={`${testIdPrefix}-goals`}
      />
      <KeyTrendsSection
        section={secs.key_trends}
        testId={`${testIdPrefix}-trends`}
      />
      <SubSection
        icon={History}
        section={secs.head_to_head}
        testId={`${testIdPrefix}-h2h`}
      />
      <ProbableScoreSection
        section={secs.probable_score}
        testId={`${testIdPrefix}-score`}
        reconciliation={reconcileScorelinesFrontend(
          secs.probable_score?.score,
          externalPayload?.forebet
        )}
        lang={lang}
      />
      <KeyPlayersSection
        players={topPlayers}
        matchId={matchId}
        testIdPrefix={`${testIdPrefix}-key-players`}
        lang={lang}
      />
      {/* Phase F82 — Rich H2H Context: shows concrete results + metrics. */}
      {editorial.h2h_context && (
        <H2HContextPanel
          context={editorial.h2h_context}
          testIdPrefix={`${testIdPrefix}-h2h-context`}
        />
      )}
      {/* Phase F82.1-adjust — When corners_snapshot is in the deferred
          state (PENDING_BACKGROUND_ENRICHMENT), surface the
          "Actualizar córners con 365Scores" button. */}
      {editorial.corners_snapshot && matchId && (
        <CornersRefreshPanel
          matchId={matchId}
          cornersSnapshot={editorial.corners_snapshot}
          testIdPrefix={`${testIdPrefix}-corners-refresh`}
        />
      )}
      {/* Phase F70 — External editorial enrichment (Forebet + Sportytrader). */}
      {homeTeamName && awayTeamName && (
        <ExternalEditorialPanel
          homeTeam={homeTeamName}
          awayTeam={awayTeamName}
          matchId={matchId}
          lang={lang}
          testIdPrefix={`${testIdPrefix}-external`}
          onExternalLoaded={setExternalPayload}
        />
      )}
      {/* Phase F74-post — collapsible diagnostic block showing which
          sub-sources fed (o no) este análisis. Disponible siempre como
          diagnóstico, sin invadir la UI cuando todo está bien. */}
      {renderDebugCollapsible()}
    </div>
  );
}


function DiscardReasonSection({ section, testId }) {
  if (!section || section.available !== true || !section.text) return null;
  return (
    <div
      className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs space-y-1"
      data-testid={`${testId}-root`}
    >
      <div className="flex items-center gap-2">
        <Info className="h-3.5 w-3.5 text-amber-300" />
        <div className="text-xs font-semibold text-amber-100">
          {section.title || 'Motivo del descarte'}
        </div>
      </div>
      <p className="text-amber-50/90 leading-relaxed pl-5" data-testid={`${testId}-text`}>
        {section.text}
      </p>
      {(section.odds || section.estimated_probability || section.edge != null) && (
        <div className="pl-5 flex flex-wrap gap-2 text-[10px] text-amber-200/80 font-mono">
          {section.odds && <span>Cuota {Number(section.odds).toFixed(2)}</span>}
          {section.implied_probability != null && (
            <span>Imp. {(Number(section.implied_probability) > 1
              ? Number(section.implied_probability)
              : Number(section.implied_probability) * 100).toFixed(1)}%</span>
          )}
          {section.estimated_probability != null && (
            <span>Est. {(Number(section.estimated_probability) > 1
              ? Number(section.estimated_probability)
              : Number(section.estimated_probability) * 100).toFixed(1)}%</span>
          )}
          {section.edge != null && (
            <span>Edge {(Number(section.edge) > 1 || Number(section.edge) < -1
              ? Number(section.edge)
              : Number(section.edge) * 100).toFixed(1)}%</span>
          )}
          {section.fragility != null && (
            <span>Frag {Number(section.fragility).toFixed(0)}/100</span>
          )}
        </div>
      )}
    </div>
  );
}


function KeyPlayersSection({ players, matchId, testIdPrefix, lang }) {
  if (!Array.isArray(players) || players.length === 0) return null;
  const title = lang === 'en' ? 'Key players' : 'Jugadores clave';
  return (
    <div className="space-y-2" data-testid={`${testIdPrefix}-root`}>
      <div className="flex items-center gap-2">
        <MapPin className="h-3.5 w-3.5 text-emerald-300" />
        <div className="text-xs font-semibold text-slate-100">{title}</div>
      </div>
      <div className="flex flex-wrap gap-2 pl-5">
        {players.slice(0, 6).map((p, i) => {
          if (!p || !p.name) return null;
          return (
            <div
              key={`kp-${i}-${p.name}`}
              className="inline-flex items-center gap-2 rounded-md border border-slate-700/40 bg-slate-900/40 px-2 py-1"
              data-testid={`${testIdPrefix}-item-${i}`}
            >
              <span className="text-[11px] text-slate-100">{p.name}</span>
              {p.team && (
                <span className="text-[10px] text-slate-500 font-mono">{p.team}</span>
              )}
              {matchId && (
                <PlayerHeatmapDialog
                  playerName={p.name}
                  matchId={matchId}
                  teamHint={p.team}
                  testIdPrefix={`${testIdPrefix}-${i}-dialog`}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


function SubSection({ icon: Icon, section, testId }) {
  if (!section) return null;
  const ok = section.status === 'OK';
  const watch = section.status === 'WATCHLIST';
  return (
    <div className="space-y-1" data-testid={testId}>
      <div className="flex items-center gap-2">
        <Icon className={`h-3.5 w-3.5 ${ok ? 'text-emerald-300' : watch ? 'text-amber-300' : 'text-slate-400'}`} />
        <div className="text-xs font-semibold text-slate-100">{section.title}</div>
        {ok && section.recommended_market && (
          <span
            className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-100 border border-emerald-500/40"
            data-testid={`${testId}-market-chip`}
          >
            {section.recommended_market}
            {section.odds && <span className="opacity-80">@ {section.odds.toFixed(2)}</span>}
          </span>
        )}
        {watch && (
          <span className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-500/20 text-amber-100 border border-amber-500/40">
            Watchlist
          </span>
        )}
      </div>
      <p className={`text-xs leading-relaxed ${ok ? 'text-slate-200' : 'text-slate-400'} pl-5`}>
        {section.text}
      </p>
    </div>
  );
}


function KeyTrendsSection({ section, testId }) {
  if (!section || section.available === false) {
    return (
      <div className="space-y-1" data-testid={testId}>
        <div className="flex items-center gap-2">
          <ListChecks className="h-3.5 w-3.5 text-slate-400" />
          <div className="text-xs font-semibold text-slate-100">{section?.title || 'Tendencias clave'}</div>
        </div>
        <p className="text-xs text-slate-400 pl-5">
          {section?.text || 'No hay tendencias claras con los datos disponibles.'}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1" data-testid={testId}>
      <div className="flex items-center gap-2">
        <ListChecks className="h-3.5 w-3.5 text-cyan-300" />
        <div className="text-xs font-semibold text-slate-100">{section.title}</div>
      </div>
      <ul className="text-xs text-slate-200 pl-5 space-y-1 list-disc list-inside">
        {section.items.map((it, i) => (
          <li key={`trend-${i}`} className="leading-relaxed" data-testid={`${testId}-item-${i}`}>
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}


function ProbableScoreSection({ section, testId, reconciliation = null, lang = 'es' }) {
  if (!section) return null;
  // Phase F71 — if Forebet contradicts the internal score, suppress the
  // internal chip and surface the external one with a clear "Forebet"
  // tag so the UI never shows two contradictory predictions.
  if (reconciliation && reconciliation.suppressInternal && reconciliation.externalScore) {
    const t = lang === 'en'
      ? { title: 'Probable score', method: 'Forebet algorithm', disclaimer: 'External algorithmic prediction — not a recommended pick.' }
      : { title: 'Resultado probable', method: 'algoritmo Forebet', disclaimer: 'Predicción algorítmica externa — no es pick recomendado.' };
    return (
      <div className="space-y-1" data-testid={testId}>
        <div className="flex items-center gap-2">
          <TrendingUp className="h-3.5 w-3.5 text-cyan-300" />
          <div className="text-xs font-semibold text-slate-100">{t.title}</div>
          <span
            className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-cyan-500/15 text-cyan-100 border border-cyan-500/30"
            data-testid={`${testId}-score-chip`}
          >
            {reconciliation.externalScore}
            <span className="opacity-60">· {t.method}</span>
          </span>
        </div>
        <p className="text-xs leading-relaxed text-slate-200 pl-5">
          {lang === 'en'
            ? `Forebet's algorithm predicts ${reconciliation.externalScore}` +
              (reconciliation.externalXG ? ` (xG ${Number(reconciliation.externalXG).toFixed(2)}).` : '.')
            : `El algoritmo de Forebet predice ${reconciliation.externalScore}` +
              (reconciliation.externalXG ? ` (goles esperados ${Number(reconciliation.externalXG).toFixed(2)}).` : '.')}
        </p>
        <p
          className="text-[10px] text-amber-300/90 italic pl-5"
          data-testid={`${testId}-contextual-disclaimer`}
        >
          {t.disclaimer}
        </p>
        <p
          className="text-[10px] text-slate-500 italic pl-5"
          data-testid={`${testId}-internal-suppressed`}
        >
          {lang === 'en'
            ? `Internal heuristic suppressed (${section.score || '—'}) — contradicts external source.`
            : `Heurística interna suprimida (${section.score || '—'}) — contradice fuente externa.`}
        </p>
      </div>
    );
  }
  const ok = section.available;
  return (
    <div className="space-y-1" data-testid={testId}>
      <div className="flex items-center gap-2">
        <TrendingUp className={`h-3.5 w-3.5 ${ok ? 'text-emerald-300' : 'text-slate-400'}`} />
        <div className="text-xs font-semibold text-slate-100">
          {section.title || 'Resultado probable'}
        </div>
        {ok && section.score && (
          <span
            className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-100 border border-emerald-500/30"
            data-testid={`${testId}-score-chip`}
          >
            {section.score}
            <span className="opacity-60">· {section.method?.replace(/_/g, ' ').toLowerCase()}</span>
          </span>
        )}
      </div>
      <p className={`text-xs leading-relaxed ${ok ? 'text-slate-200' : 'text-slate-400'} pl-5`}>
        {section.text}
      </p>
      {ok && section.is_contextual_only && (
        <p
          className="text-[10px] text-amber-300/90 italic pl-5"
          data-testid={`${testId}-contextual-disclaimer`}
        >
          {section.context_disclaimer || 'Marcador informativo — no es pick recomendado.'}
        </p>
      )}
      {ok && Array.isArray(section.top_scorelines) && section.top_scorelines.length > 1 && (
        <div className="pl-5 flex flex-wrap gap-1" data-testid={`${testId}-alt-scorelines`}>
          {section.top_scorelines.slice(1, 4).map((s, i) => (
            <span
              key={`alt-${i}`}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700"
            >
              {s.score}{' '}
              <span className="opacity-50">{Math.round((s.probability || 0) * 100)}%</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
