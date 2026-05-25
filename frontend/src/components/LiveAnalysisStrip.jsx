import { AlertTriangle, TrendingUp, Activity, Target, Crosshair, Flag } from 'lucide-react';

/**
 * LiveAnalysisStrip — auto-rendered on every live football card.
 *
 * Surfaces the per-side metrics computed in backend `live_xg_proxy.py`
 * (kloppy-normalised stats + soccer_xg shot-quality xG + socceraction
 * threat index + pressure rate) and prominently flags the late-lead
 * trap when triggered.
 *
 * Data shape mirrors `compute_live_analysis()` output:
 *   {
 *     minute, score, home, away, deltas, leader_odds, favorite_side,
 *     trap: null | {...},
 *     verdict: { label, side, reason_es, reason_en }
 *   }
 *
 * Designed to live BETWEEN the score block and the LiveReevalPanel
 * inside each LivePage card.
 */

const VERDICT_META = {
  TRAP_LATE_LEAD:    { tone: 'red',     icon: AlertTriangle, es: 'Trampa detectada',   en: 'Trap detected' },
  LIVE_VALUE_PUSH:   { tone: 'emerald', icon: TrendingUp,    es: 'Empuje con valor',   en: 'Push with value' },
  BALANCED:          { tone: 'slate',   icon: Activity,      es: 'Balanceado',         en: 'Balanced' },
  INSUFFICIENT_DATA: { tone: 'slate',   icon: Activity,      es: 'Sin datos',          en: 'No data' },
};

const TONE_CLS = {
  red:     'border-red-500/40 bg-red-500/10 text-red-100',
  emerald: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100',
  slate:   'border-slate-500/30 bg-slate-500/5 text-slate-200',
};

const TONE_ICON_CLS = {
  red:     'text-red-300',
  emerald: 'text-emerald-300',
  slate:   'text-slate-400',
};

function fmt(n, d = 2) {
  if (n == null || Number.isNaN(n)) return '—';
  if (typeof n !== 'number') return String(n);
  return n.toFixed(d);
}

function MetricCell({ label, home, away, fmtFn = (v) => fmt(v, 1), highlight = null, testId }) {
  const homeWin = highlight === 'home' || (highlight == null && Number(home) > Number(away));
  const awayWin = highlight === 'away' || (highlight == null && Number(away) > Number(home));
  return (
    <div className="flex flex-col gap-0.5" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80">{label}</div>
      <div className="flex items-center justify-between gap-2 font-mono-tabular text-xs">
        <span className={homeWin ? 'text-foreground font-semibold' : 'text-muted-foreground'}>
          {fmtFn(home)}
        </span>
        <span className="text-muted-foreground/60">·</span>
        <span className={awayWin ? 'text-foreground font-semibold' : 'text-muted-foreground'}>
          {fmtFn(away)}
        </span>
      </div>
    </div>
  );
}

export function LiveAnalysisStrip({ analysis, lang = 'es', testId = 'live-analysis' }) {
  if (!analysis || typeof analysis !== 'object') return null;

  const verdict = analysis.verdict || {};
  const meta = VERDICT_META[verdict.label] || VERDICT_META.BALANCED;
  const Icon = meta.icon;
  const reason = lang === 'en' ? (verdict.reason_en || verdict.reason_es) : verdict.reason_es;

  const home = analysis.home || {};
  const away = analysis.away || {};
  const trap = analysis.trap;

  // If nothing meaningful to show (no stats AND no trap), skip silently.
  const hasStats =
    (home.shots || 0) > 0 || (away.shots || 0) > 0 ||
    (home.xg_live || 0) > 0 || (away.xg_live || 0) > 0;
  if (!hasStats && verdict.label === 'INSUFFICIENT_DATA') {
    return null;
  }

  return (
    <div
      className={`rounded-lg border px-3 py-2 space-y-2 ${TONE_CLS[meta.tone]}`}
      data-testid={testId}
      data-verdict={verdict.label}
      data-trap={trap ? 'true' : 'false'}
    >
      {/* Compact evidence header — the rich verdict narration now lives
          in <LiveCopilotCard/>. We keep an icon + tiny tag here so the
          grid below still has visual anchor without duplicating prose. */}
      <div className="flex items-center gap-2">
        <Icon className={`h-3.5 w-3.5 shrink-0 ${TONE_ICON_CLS[meta.tone]}`} aria-hidden />
        <span className="text-[10px] uppercase tracking-wider opacity-80" data-testid={`${testId}-label`}>
          {lang === 'en' ? 'Live evidence' : 'Evidencia live'}
        </span>
        {trap?.triggered && (
          <span className="text-[10px] font-semibold opacity-95 font-mono-tabular border border-current rounded px-1.5 py-0.5">
            {lang === 'en' ? 'TRAP' : 'TRAMPA'}
          </span>
        )}
      </div>

      {/* Per-side metric grid — kloppy/socceraction/soccer_xg outputs */}
      {hasStats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-3 gap-y-1.5 pt-1.5 border-t border-current/15">
          <MetricCell
            label="xG live"
            home={home.xg_live} away={away.xg_live}
            fmtFn={(v) => fmt(v, 2)}
            testId={`${testId}-xg`}
          />
          <MetricCell
            label={lang === 'en' ? 'Threat (xT)' : 'Amenaza (xT)'}
            home={home.threat_index} away={away.threat_index}
            fmtFn={(v) => fmt(v, 0)}
            testId={`${testId}-threat`}
          />
          <MetricCell
            label={lang === 'en' ? 'Pressure / min' : 'Presión / min'}
            home={home.pressure_rate} away={away.pressure_rate}
            fmtFn={(v) => fmt(v, 2)}
            highlight={trap?.triggered ? trap.trailing_side : null}
            testId={`${testId}-pressure`}
          />
          <MetricCell
            label={lang === 'en' ? 'Shots (on)' : 'Tiros (a puerta)'}
            home={`${home.shots || 0} (${home.shots_on_target || 0})`}
            away={`${away.shots || 0} (${away.shots_on_target || 0})`}
            fmtFn={(v) => v}
            testId={`${testId}-shots`}
          />
          <MetricCell
            label={lang === 'en' ? 'Corners' : 'Córneres'}
            home={home.corners} away={away.corners}
            fmtFn={(v) => fmt(v, 0)}
            testId={`${testId}-corners`}
          />
          <MetricCell
            label={lang === 'en' ? 'Dangerous Att.' : 'Ataques peligrosos'}
            home={home.dangerous} away={away.dangerous}
            fmtFn={(v) => fmt(v, 0)}
            testId={`${testId}-dangerous`}
          />
          <MetricCell
            label={lang === 'en' ? 'Possession' : 'Posesión'}
            home={home.possession} away={away.possession}
            fmtFn={(v) => `${fmt(v, 0)}%`}
            testId={`${testId}-possession`}
          />
          <MetricCell
            label={lang === 'en' ? 'Shots in box' : 'Tiros en área'}
            home={home.shots_in_box} away={away.shots_in_box}
            fmtFn={(v) => fmt(v, 0)}
            testId={`${testId}-in-box`}
          />
        </div>
      )}

      {/* Trap detail block — only when triggered, with the ratios */}
      {trap?.triggered && (
        <div className="mt-1 text-[10px] font-mono-tabular opacity-90 border-t border-current/15 pt-1.5 flex flex-wrap gap-x-3 gap-y-0.5" data-testid={`${testId}-trap-detail`}>
          <span><Crosshair className="inline h-3 w-3 mr-0.5" /> {lang === 'en' ? 'odds leader' : 'cuota líder'}: {trap.decimal_odds_for_leader}</span>
          <span><Target className="inline h-3 w-3 mr-0.5" /> {lang === 'en' ? 'pressure ratio' : 'ratio presión'}: {trap.pressure_ratio}×</span>
          <span><Flag className="inline h-3 w-3 mr-0.5" /> {lang === 'en' ? 'threat ratio' : 'ratio amenaza'}: {trap.threat_ratio}×</span>
        </div>
      )}
    </div>
  );
}
