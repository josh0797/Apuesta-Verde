import React from 'react';
import { Activity, Target, TrendingUp, ListChecks, History, Trophy } from 'lucide-react';

/**
 * Phase F66 — Editorial Prediction Panel.
 *
 * Renders the four mandatory sub-sections of the internal editorial
 * engine (corners / goals / key_trends / probable_score) plus the
 * head_to_head section (always present, surfaces "insufficient sample"
 * placeholder until the H2H source is wired).
 *
 * Always fail-soft: when a section reports ``available=false`` or
 * ``status="MISSING"``, the chip stays subdued; when ``status="OK"`` the
 * recommended market is highlighted in primary brand colour and the
 * dynamic narrative text is shown verbatim.
 *
 * Props
 * -----
 *  - editorial: dict from ``generate_football_editorial_prediction``.
 *  - testIdPrefix: optional string for data-testid prefixes.
 *  - lang: 'es' | 'en' — narrative is ES-only for now; this knob just
 *    swaps section titles.
 */
export function EditorialPredictionPanel({ editorial, testIdPrefix = 'editorial', lang = 'es' }) {
  if (!editorial || editorial.available === false) {
    return null;
  }
  const secs = editorial.editorial_sections || {};
  const best = editorial.best_protected_market;
  const t = (lang === 'en')
    ? {
        header: 'Internal editorial analysis',
        sub:    'Replaces Scores24 — generated from your own L5/L15 model',
        bestPrefix: 'Highlighted protected market',
      }
    : {
        header: 'Análisis editorial interno',
        sub:    'Reemplaza a Scores24 — generado desde tu propio modelo L5/L15',
        bestPrefix: 'Mercado protegido destacado',
      };

  return (
    <div
      className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-3 space-y-3"
      data-testid={`${testIdPrefix}-root`}
    >
      <div className="flex items-center gap-2">
        <Activity className="h-4 w-4 text-emerald-300" />
        <div className="text-xs font-semibold uppercase tracking-wider text-emerald-200">
          {t.header}
        </div>
        {/* Phase F67 — data completeness pill */}
        {editorial.data_completeness?.completeness && (
          <span
            className={`ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
              editorial.data_completeness.completeness === 'FULL'
                ? 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30'
                : editorial.data_completeness.completeness === 'PARTIAL'
                  ? 'bg-amber-500/15 text-amber-200 border-amber-500/30'
                  : 'bg-slate-500/15 text-slate-300 border-slate-500/30'
            }`}
            title={`Fuentes: ${(editorial.data_completeness.available_sources || []).join(', ') || 'ninguna'}`}
            data-testid={`${testIdPrefix}-completeness-pill`}
          >
            {editorial.data_completeness.completeness}
          </span>
        )}
      </div>
      <div className="text-[11px] text-emerald-300/70 -mt-2">{t.sub}</div>

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
      {/* Phase F67 — explicit contextual-only disclaimer for Dixon-Coles
          scorelines, so the user never confuses the most-likely score
          with the recommended pick. */}
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
