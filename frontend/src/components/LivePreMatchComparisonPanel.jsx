/**
 * LivePreMatchComparisonPanel
 * ===========================
 * Renders the backend's `script_comparison` payload (see
 * `services/live_pre_match_comparison.py`) for live matches.
 *
 * Visual hierarchy:
 *   1. Three-pill row: script status / pregame status / live action
 *   2. Score delta numeric tile (actual vs expected through this period)
 *   3. Human summary in Spanish/English
 *   4. (When pick is settled) suggested alternative lines with copy
 *
 * Sport gate: this panel renders for any sport but the underlying
 * comparison is meaningful only when the engine has both a pregame pick
 * AND a live state. When `script_comparison == null` the parent should
 * not render us.
 */
import { Activity, ShieldAlert, Clock, CheckCircle2, XCircle, Eye, AlertTriangle, Info } from 'lucide-react';

function pillClasses(state) {
  switch (state) {
    // script_status
    case 'on_script':
      return 'bg-emerald-500/15 text-emerald-200 border-emerald-500/40';
    case 'soft_deviation':
      return 'bg-amber-500/15 text-amber-200 border-amber-500/40';
    case 'hard_deviation':
      return 'bg-orange-500/15 text-orange-200 border-orange-500/40';
    case 'broken_script':
      return 'bg-rose-500/15 text-rose-200 border-rose-500/40';
    case 'final_settled':
      return 'bg-slate-500/15 text-slate-200 border-slate-500/30';
    // pregame_pick_status
    case 'still_playable':
      return 'bg-emerald-500/15 text-emerald-200 border-emerald-500/40';
    case 'pending':
      return 'bg-slate-500/15 text-slate-200 border-slate-500/30';
    case 'already_won':
      return 'bg-cyan-500/15 text-cyan-100 border-cyan-500/40';
    case 'already_lost':
      return 'bg-rose-500/15 text-rose-100 border-rose-500/40';
    case 'not_actionable':
      return 'bg-amber-500/15 text-amber-100 border-amber-500/40';
    // live_recommendation_status
    case 'actionable':
      return 'bg-emerald-500/15 text-emerald-200 border-emerald-500/40';
    case 'wait':
      return 'bg-slate-500/15 text-slate-200 border-slate-500/30';
    case 'cashout_watch':
      return 'bg-amber-500/15 text-amber-200 border-amber-500/40';
    case 'hedge':
      return 'bg-cyan-500/15 text-cyan-200 border-cyan-500/40';
    case 'avoid':
      return 'bg-rose-500/15 text-rose-200 border-rose-500/40';
    default:
      return 'bg-slate-500/10 text-slate-300 border-slate-500/30';
  }
}

const LABELS = {
  es: {
    title: 'Comparación vs análisis previo',
    scriptStatus:        'Estado del guion',
    pregameStatus:       'Pick pregame',
    liveStatus:          'Lectura live',
    liveVerdict:         'Veredicto live',
    expectedVsActual:    'Esperado vs real',
    period:              'Periodo',
    suggested:           'Líneas live sugeridas',
    noPregame:           'No hay análisis pregame disponible. Recomendación basada solo en datos live.',
    insufficient:        'Datos live insuficientes para comparar contra el análisis previo.',
    liveBoxTitle:        'Datos live del partido',
    boxLabels: {
      total_runs:    'Carreras',
      hits:          'Hits',
      walks:         'Bases por bolas (BB)',
      home_runs:     'Home runs',
      errors:        'Errores',
      strikeouts:    'Ponches',
      pitches:       'Lanzamientos',
    },
    script: {
      on_script:         'En guion',
      soft_deviation:    'Desviación leve',
      hard_deviation:    'Desviación fuerte',
      broken_script:     'Guion roto',
      final_settled:     'Partido finalizado',
      insufficient_data: 'Datos insuficientes',
    },
    pregame: {
      pending:           'Pendiente',
      already_won:       'Ya ganó',
      already_lost:      'Ya perdió',
      still_playable:    'Aún jugable',
      not_actionable:    'No accionable',
    },
    live: {
      actionable:        'Entrar',
      wait:              'Esperar',
      avoid:             'Evitar',
      hedge:             'Cobertura',
      cashout_watch:     'Vigilar cashout',
    },
    verdict: {
      PICK_ALREADY_LOST:        'Pick ya perdió — no perseguir',
      PICK_ALREADY_WON:         'Pick ya ganó — cerrado',
      AVOID_UNDER_OR_LOOK_OVER: 'Evitar Under · buscar Over',
      AVOID_OVER_OR_CASHOUT:    'Evitar Over · cashout',
      MAINTAIN:                 'Mantener pick',
      CASHOUT:                  'Considerar cashout',
      NO_ACTIONABLE:            'No accionable',
    },
  },
  en: {
    title: 'Pregame ↔ live comparison',
    scriptStatus:        'Script status',
    pregameStatus:       'Pregame pick',
    liveStatus:          'Live read',
    liveVerdict:         'Live verdict',
    expectedVsActual:    'Expected vs actual',
    period:              'Period',
    suggested:           'Suggested live lines',
    noPregame:           'No pregame analysis available. Recommendation based on live data only.',
    insufficient:        'Insufficient live data to compare against the pregame analysis.',
    liveBoxTitle:        'Live box score',
    boxLabels: {
      total_runs:    'Runs',
      hits:          'Hits',
      walks:         'Walks (BB)',
      home_runs:     'Home runs',
      errors:        'Errors',
      strikeouts:    'Strikeouts',
      pitches:       'Pitches',
    },
    script: {
      on_script:         'On script',
      soft_deviation:    'Soft deviation',
      hard_deviation:    'Hard deviation',
      broken_script:     'Broken script',
      final_settled:     'Game finished',
      insufficient_data: 'Insufficient data',
    },
    pregame: {
      pending:           'Pending',
      already_won:       'Already won',
      already_lost:      'Already lost',
      still_playable:    'Still playable',
      not_actionable:    'Not actionable',
    },
    live: {
      actionable:        'Enter',
      wait:              'Wait',
      avoid:             'Avoid',
      hedge:             'Hedge',
      cashout_watch:     'Watch cashout',
    },
    verdict: {
      PICK_ALREADY_LOST:        'Pick lost — do not chase',
      PICK_ALREADY_WON:         'Pick won — closed',
      AVOID_UNDER_OR_LOOK_OVER: 'Avoid Under · look Over',
      AVOID_OVER_OR_CASHOUT:    'Avoid Over · cashout',
      MAINTAIN:                 'Maintain pick',
      CASHOUT:                  'Consider cashout',
      NO_ACTIONABLE:            'Not actionable',
    },
  },
};

const VERDICT_CLASSES = {
  PICK_ALREADY_LOST:        'bg-rose-500/15 text-rose-100 border-rose-500/40',
  PICK_ALREADY_WON:         'bg-cyan-500/15 text-cyan-100 border-cyan-500/40',
  AVOID_UNDER_OR_LOOK_OVER: 'bg-orange-500/15 text-orange-100 border-orange-500/40',
  AVOID_OVER_OR_CASHOUT:    'bg-orange-500/15 text-orange-100 border-orange-500/40',
  MAINTAIN:                 'bg-emerald-500/15 text-emerald-100 border-emerald-500/40',
  CASHOUT:                  'bg-amber-500/15 text-amber-100 border-amber-500/40',
  NO_ACTIONABLE:            'bg-slate-500/15 text-slate-200 border-slate-500/30',
};

function StatePill({ value, dict, testId }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10.5px] font-semibold uppercase tracking-wide ${pillClasses(value)}`}
      data-testid={testId}
    >
      {value === 'on_script'      && <CheckCircle2 className="h-3 w-3" />}
      {value === 'soft_deviation' && <Activity className="h-3 w-3" />}
      {value === 'hard_deviation' && <AlertTriangle className="h-3 w-3" />}
      {value === 'broken_script'  && <ShieldAlert className="h-3 w-3" />}
      {value === 'already_won'    && <CheckCircle2 className="h-3 w-3" />}
      {value === 'already_lost'   && <XCircle className="h-3 w-3" />}
      {value === 'not_actionable' && <Info className="h-3 w-3" />}
      {value === 'avoid'          && <XCircle className="h-3 w-3" />}
      {value === 'cashout_watch'  && <Eye className="h-3 w-3" />}
      {value === 'wait'           && <Clock className="h-3 w-3" />}
      {dict[value] || value}
    </span>
  );
}

export function LivePreMatchComparisonPanel({ comparison, lang = 'es' }) {
  if (!comparison) return null;
  const t = LABELS[lang] || LABELS.es;

  const ss = comparison.script_status || 'insufficient_data';
  const ps = comparison.pregame_pick_status || 'pending';
  const ls = comparison.live_recommendation_status || 'wait';
  const verdict = comparison.live_verdict || null;
  const sd = comparison.score_delta;
  const expected = comparison.expected_total_through;
  const actual   = comparison.actual_total;
  const period   = comparison.period_n;
  const reasons  = comparison.reason_codes || [];
  const alts     = comparison.suggested_alternatives || [];
  const summary  = lang === 'en' ? comparison.human_summary_en : comparison.human_summary_es;
  const liveData = comparison.live_data || {};

  // Special-case when there's no pregame pick at all → show a soft banner.
  if (reasons.includes('NO_PREGAME_PICK')) {
    // P4 Moneyball polish: when the comparison engine specifically
    // failed to find a pick via game_pk (vs a global "no pregame" run),
    // we surface the more specific message so the user knows the issue
    // is a join failure and not a data-quality gap.
    const noLinkByGamePk = reasons.includes('NO_PICK_FOUND_BY_GAME_PK')
      || comparison.no_pregame_reason === 'game_pk_not_found';
    return (
      <div
        className="rounded-md border border-slate-500/30 bg-slate-500/5 p-3 text-[12px] text-slate-300 flex items-start gap-2"
        data-testid={noLinkByGamePk
          ? 'script-comparison-no-pregame-by-gamepk'
          : 'script-comparison-empty'}
      >
        <Info className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
        <div>
          {noLinkByGamePk
            ? (lang === 'es'
                ? 'No se encontró pick pregame asociado por game_pk. Revisa el ID del partido si esperabas una comparación.'
                : 'No pregame pick found by game_pk. Check the game ID if you expected a comparison.')
            : t.noPregame}
        </div>
      </div>
    );
  }

  // Only treat as truly insufficient when we have NO score AND no useful
  // pregame-pick-status. When the game is FINAL with a known score, the
  // backend now promotes ss to "final_settled" and the validator
  // produces a meaningful pregame_pick_status — render normally.
  const hasUsefulInfo = (
    actual != null
    || ['already_won', 'already_lost', 'not_actionable'].includes(ps)
    || verdict
  );
  if (ss === 'insufficient_data' && !hasUsefulInfo) {
    return (
      <div
        className="rounded-md border border-slate-500/30 bg-slate-500/5 p-3 text-[12px] text-slate-300 flex items-start gap-2"
        data-testid="script-comparison-insufficient"
      >
        <Info className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
        <div>{t.insufficient}</div>
      </div>
    );
  }

  // Box-score chips to render — only those actually present.
  const boxRows = [];
  const pair = (homeKey, awayKey, label) => {
    const h = liveData[homeKey];
    const a = liveData[awayKey];
    if (h == null && a == null) return;
    boxRows.push({
      label,
      home: h ?? '—',
      away: a ?? '—',
      total: (typeof h === 'number' && typeof a === 'number') ? h + a : null,
    });
  };
  if (liveData.total_runs != null) {
    boxRows.push({
      label: t.boxLabels.total_runs,
      home: liveData.score_home ?? '—',
      away: liveData.score_away ?? '—',
      total: liveData.total_runs,
    });
  }
  pair('hits_home',       'hits_away',       t.boxLabels.hits);
  pair('walks_home',      'walks_away',      t.boxLabels.walks);
  pair('home_runs_home',  'home_runs_away',  t.boxLabels.home_runs);
  pair('errors_home',     'errors_away',     t.boxLabels.errors);
  pair('strikeouts_home', 'strikeouts_away', t.boxLabels.strikeouts);
  pair('pitches_home',    'pitches_away',    t.boxLabels.pitches);

  // ── P4 Moneyball polish: live hits pressure warning ─────────────
  // Many hits but few runs → bullpen / lineup is putting bodies on
  // base but failing to convert. The Under that looked safe is now
  // at risk because the marker hasn't caught up yet.
  const totalHits = (
    (typeof liveData.hits_home === 'number' ? liveData.hits_home : 0)
    + (typeof liveData.hits_away === 'number' ? liveData.hits_away : 0)
  );
  const totalRuns = (typeof liveData.total_runs === 'number'
    ? liveData.total_runs
    : (liveData.score_home ?? 0) + (liveData.score_away ?? 0));
  const liveHitsPressureWarning = (
    totalHits >= 8
    && totalRuns >= 0
    && (totalHits - totalRuns) >= 5
  );

  // ── Filter out suggested alternatives that already happened ─────
  // E.g. if the game is at 8 runs and the engine suggested "Over 7.5",
  // we MUST NOT render that line — the user can't bet what already
  // happened. We extract the numeric line and compare to actual.
  const extractLine = (label) => {
    const m = /(\d+(?:\.\d+)?)/.exec(String(label || ''));
    return m ? parseFloat(m[1]) : null;
  };
  const safeAlts = (alts || []).filter((a) => {
    const line = extractLine(a);
    if (line == null) return true;
    const isOver = /\bover\b|m[áa]s de/i.test(a);
    const isUnder = /\bunder\b|menos de/i.test(a);
    if (isOver && typeof totalRuns === 'number' && totalRuns >= line) {
      return false;   // Over already happened — drop
    }
    if (isUnder && typeof totalRuns === 'number' && totalRuns > line) {
      return false;   // Under already broken — drop
    }
    return true;
  });
  const filteredOutAlts = (alts || []).length - safeAlts.length;

  return (
    <section
      className="rounded-xl border border-border bg-card p-4 space-y-3"
      data-testid="script-comparison-panel"
      aria-label={t.title}
    >
      <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
        <Activity className="h-4 w-4 text-cyan-300" />
        {t.title}
      </h3>

      {/* 3-pill grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <div className="space-y-1">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.scriptStatus}</div>
          <StatePill value={ss} dict={t.script} testId="cmp-script-status" />
        </div>
        <div className="space-y-1">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.pregameStatus}</div>
          <StatePill value={ps} dict={t.pregame} testId="cmp-pregame-status" />
        </div>
        <div className="space-y-1">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.liveStatus}</div>
          <StatePill value={ls} dict={t.live} testId="cmp-live-status" />
        </div>
      </div>

      {/* Live verdict chip — the canonical action recommendation */}
      {verdict ? (
        <div className="space-y-1">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.liveVerdict}</div>
          <span
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-semibold ${VERDICT_CLASSES[verdict] || VERDICT_CLASSES.NO_ACTIONABLE}`}
            data-testid="cmp-live-verdict"
          >
            {verdict === 'PICK_ALREADY_LOST' && <XCircle className="h-3 w-3" />}
            {verdict === 'PICK_ALREADY_WON'  && <CheckCircle2 className="h-3 w-3" />}
            {(verdict === 'CASHOUT' || verdict === 'AVOID_OVER_OR_CASHOUT') && <Eye className="h-3 w-3" />}
            {verdict === 'AVOID_UNDER_OR_LOOK_OVER' && <AlertTriangle className="h-3 w-3" />}
            {verdict === 'MAINTAIN' && <CheckCircle2 className="h-3 w-3" />}
            {verdict === 'NO_ACTIONABLE' && <Info className="h-3 w-3" />}
            {t.verdict[verdict] || verdict}
          </span>
        </div>
      ) : null}

      {/* Numeric tile — expected vs actual through current period */}
      {(expected != null && actual != null) && (
        <div
          className="grid grid-cols-3 items-center gap-2 px-3 py-2 rounded-md bg-slate-500/5 border border-slate-500/20"
          data-testid="cmp-delta-tile"
        >
          <div>
            <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.expectedVsActual}</div>
            <div className="text-[12px] mono font-mono-tabular tabular-nums">
              <span className="text-foreground font-semibold">{actual}</span>
              <span className="text-muted-foreground"> vs </span>
              <span>{expected}</span>
            </div>
          </div>
          <div className="text-center">
            <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">Δ</div>
            <div
              className={`text-base mono font-mono-tabular tabular-nums font-semibold ${
                (sd ?? 0) > 0 ? 'text-rose-200' : (sd ?? 0) < 0 ? 'text-cyan-200' : 'text-muted-foreground'
              }`}
              data-testid="cmp-score-delta"
            >
              {sd != null ? (sd > 0 ? '+' : '') + sd : '—'}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">{t.period}</div>
            <div className="text-[12px] mono font-mono-tabular tabular-nums">{period ?? '—'}</div>
          </div>
        </div>
      )}

      {/* Live box score — runs, hits, BB, HR, errors, strikeouts, pitches */}
      {boxRows.length > 0 && (
        <div className="space-y-1.5" data-testid="cmp-live-box">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground font-semibold">
            {t.liveBoxTitle}
          </div>
          <div className="rounded-md bg-slate-500/5 border border-slate-500/20 overflow-hidden">
            <table className="w-full text-[11px] tabular-nums">
              <thead>
                <tr className="border-b border-slate-500/20 text-[10px] uppercase text-muted-foreground">
                  <th className="text-left px-2 py-1.5 font-medium">—</th>
                  <th className="text-right px-2 py-1.5 font-medium">L</th>
                  <th className="text-right px-2 py-1.5 font-medium">V</th>
                  <th className="text-right px-2 py-1.5 font-medium">Tot</th>
                </tr>
              </thead>
              <tbody>
                {boxRows.map((row, i) => (
                  <tr
                    key={`${row.label}-${i}`}
                    className="border-b last:border-b-0 border-slate-500/10"
                    data-testid={`cmp-live-box-row-${i}`}
                  >
                    <td className="px-2 py-1 text-foreground/85">{row.label}</td>
                    <td className="px-2 py-1 text-right text-foreground">{row.home}</td>
                    <td className="px-2 py-1 text-right text-foreground">{row.away}</td>
                    <td className="px-2 py-1 text-right text-foreground font-semibold">
                      {row.total ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* P4 Moneyball: live hits pressure warning */}
      {liveHitsPressureWarning && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 text-[12px] text-amber-100 flex items-start gap-2"
          data-testid="cmp-live-hits-pressure-warning"
        >
          <AlertTriangle className="h-3.5 w-3.5 text-amber-300 shrink-0 mt-0.5" />
          <div>
            {lang === 'es'
              ? `Aviso de presión live: ${totalHits} hits acumulados vs solo ${totalRuns} carreras — el marcador puede estar subestimando el riesgo.`
              : `Live hits-pressure warning: ${totalHits} hits but only ${totalRuns} runs — the score may be hiding offensive risk.`}
          </div>
        </div>
      )}

      {/* Human summary */}
      {summary && (
        <div className="text-[12.5px] leading-relaxed text-foreground/90" data-testid="cmp-summary">
          {summary}
        </div>
      )}

      {/* Alternatives — surface only when the pick is settled. Lines that
          already happened (e.g. Over 7.5 when totalRuns >= 8) are
          filtered out so the UI never recommends an impossible bet. */}
      {safeAlts.length > 0 && (
        <div data-testid="cmp-alternatives">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground mb-1">{t.suggested}</div>
          <div className="flex flex-wrap gap-1.5">
            {safeAlts.map((a) => (
              <span
                key={a}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-100 text-[11px] font-mono-tabular"
              >
                {a}
              </span>
            ))}
          </div>
          {filteredOutAlts > 0 && (
            <div
              className="text-[10.5px] text-muted-foreground mt-1 italic"
              data-testid="cmp-alternatives-filtered-out"
            >
              {lang === 'es'
                ? `Línea ya superada — se omitieron ${filteredOutAlts} sugerencia(s) que ya no son apostables.`
                : `Line already passed — ${filteredOutAlts} suggestion(s) hidden because they're no longer bettable.`}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default LivePreMatchComparisonPanel;
