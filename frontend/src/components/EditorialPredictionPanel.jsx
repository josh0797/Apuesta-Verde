import React from 'react';
import { MapPin, Activity, Target, TrendingUp, ListChecks, History, Trophy, AlertTriangle, Info } from 'lucide-react';
import { PlayerHeatmapDialog } from '@/components/PlayerHeatmapDialog';

/**
 * Phase F66 — Editorial Prediction Panel.
 * Phase F69 — Match-specific guards:
 *  - Honour ``internal_editorial_analysis.is_generic_fallback`` → render
 *    an honest empty-state message (never the fabricated "1-1 heuristic").
 *  - Render ``data_quality`` pill (THIN / LIMITED / USABLE / STRONG)
 *    alongside the legacy ``completeness`` pill (FULL / PARTIAL / THIN).
 *  - Render a dedicated ``discard_reason_narrative`` section above the
 *    sub-sections when the entry carries market-trap / edge / fragility
 *    signals.
 *  - Header now reads "Análisis editorial interno · fallback Sportytrader"
 *    in place of the legacy Scores24 wording.
 *
 * Props
 * -----
 *  - editorial: dict from ``generate_football_editorial_prediction``.
 *  - testIdPrefix: optional string for data-testid prefixes.
 *  - lang: 'es' | 'en'.
 */
export function EditorialPredictionPanel({
  editorial,
  testIdPrefix = 'editorial',
  lang = 'es',
  topPlayers = null,
  matchId = null,
}) {
  if (!editorial || editorial.available === false) {
    return null;
  }
  const audit = editorial.internal_editorial_analysis || {};
  // Phase F69 — when the agregator flagged the editorial as a generic
  // template, render an honest empty state instead of misleading content.
  if (audit.is_generic_fallback === true) {
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
        <p className="mt-1 pl-6">
          Datos insuficientes para generar una lectura específica del partido.
        </p>
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
      />
      <KeyPlayersSection
        players={topPlayers}
        matchId={matchId}
        testIdPrefix={`${testIdPrefix}-key-players`}
        lang={lang}
      />
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


function ProbableScoreSection({ section, testId }) {
  if (!section) return null;
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
