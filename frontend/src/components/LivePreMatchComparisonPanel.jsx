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
    expectedVsActual:    'Esperado vs real',
    period:              'Periodo',
    suggested:           'Líneas live sugeridas',
    noPregame:           'No hay análisis pregame disponible. Recomendación basada solo en datos live.',
    insufficient:        'Datos live insuficientes para comparar contra el análisis previo.',
    script: {
      on_script:         'En guion',
      soft_deviation:    'Desviación leve',
      hard_deviation:    'Desviación fuerte',
      broken_script:     'Guion roto',
      insufficient_data: 'Datos insuficientes',
    },
    pregame: {
      pending:           'Pendiente',
      already_won:       'Ya cumplido',
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
  },
  en: {
    title: 'Pregame ↔ live comparison',
    scriptStatus:        'Script status',
    pregameStatus:       'Pregame pick',
    liveStatus:          'Live read',
    expectedVsActual:    'Expected vs actual',
    period:              'Period',
    suggested:           'Suggested live lines',
    noPregame:           'No pregame analysis available. Recommendation based on live data only.',
    insufficient:        'Insufficient live data to compare against the pregame analysis.',
    script: {
      on_script:         'On script',
      soft_deviation:    'Soft deviation',
      hard_deviation:    'Hard deviation',
      broken_script:     'Broken script',
      insufficient_data: 'Insufficient data',
    },
    pregame: {
      pending:           'Pending',
      already_won:       'Settled win',
      already_lost:      'Settled loss',
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
  },
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
  const sd = comparison.score_delta;
  const expected = comparison.expected_total_through;
  const actual   = comparison.actual_total;
  const period   = comparison.period_n;
  const reasons  = comparison.reason_codes || [];
  const alts     = comparison.suggested_alternatives || [];
  const summary  = lang === 'en' ? comparison.human_summary_en : comparison.human_summary_es;

  // Special-case when there's no pregame pick at all → show a soft banner.
  if (reasons.includes('NO_PREGAME_PICK')) {
    return (
      <div
        className="rounded-md border border-slate-500/30 bg-slate-500/5 p-3 text-[12px] text-slate-300 flex items-start gap-2"
        data-testid="script-comparison-empty"
      >
        <Info className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
        <div>{t.noPregame}</div>
      </div>
    );
  }
  if (ss === 'insufficient_data') {
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

      {/* Human summary */}
      {summary && (
        <div className="text-[12.5px] leading-relaxed text-foreground/90" data-testid="cmp-summary">
          {summary}
        </div>
      )}

      {/* Alternatives — surface only when the pick is settled. */}
      {alts.length > 0 && (
        <div data-testid="cmp-alternatives">
          <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground mb-1">{t.suggested}</div>
          <div className="flex flex-wrap gap-1.5">
            {alts.map((a) => (
              <span
                key={a}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-cyan-500/30 bg-cyan-500/10 text-cyan-100 text-[11px] font-mono-tabular"
              >
                {a}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export default LivePreMatchComparisonPanel;
