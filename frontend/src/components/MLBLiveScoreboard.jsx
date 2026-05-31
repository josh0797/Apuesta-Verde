/**
 * MLBLiveScoreboard — sport-specific live scoreboard for MLB matches.
 *
 * Renders the canonical live state coming from `useLiveMatchDetail`:
 *   • Score (BIG, monospace, with team names + abbreviations)
 *   • Inning ordinal + half (top/bot)
 *   • Outs (0-3 dot indicators)
 *   • Balls / strikes (when available)
 *   • Bases (diamond with filled corners for runners on)
 *   • Current batter + pitcher
 *   • State banner (final / partial / no-data)
 *   • Last fetched timestamp + Refresh button
 *
 * Sport gate is in the hook — this component already assumes baseball.
 * Use specific data-testid attributes so the testing agent can drive
 * granular assertions (e.g. `mlb-live-score`, `mlb-live-inning`, ...).
 */
import { RefreshCw, Circle, Dot, Activity, Trophy, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';

const STATE_LABELS = {
  loading: { es: 'Cargando…', en: 'Loading…' },
  'live-data-ready':   { es: 'EN VIVO', en: 'LIVE' },
  'live-data-partial': { es: 'Datos live parciales — recomendación basada en lectura estructural', en: 'Partial live data — recommendation based on structural reading' },
  final:               { es: 'Finalizado', en: 'Final' },
  'no-live-data':      { es: 'El partido aún no comienza', en: "Game hasn't started yet" },
};

function _stateBadgeStyles(state) {
  switch (state) {
    case 'live-data-ready':
      return 'bg-rose-500/15 text-rose-200 border-rose-500/40 animate-pulse';
    case 'live-data-partial':
      return 'bg-amber-500/15 text-amber-100 border-amber-500/40';
    case 'final':
      return 'bg-cyan-500/15 text-cyan-100 border-cyan-500/40';
    case 'loading':
      return 'bg-slate-500/15 text-slate-200 border-slate-500/30';
    default:
      return 'bg-slate-500/10 text-slate-300 border-slate-500/30';
  }
}

function _formatRelative(iso, lang) {
  if (!iso) return null;
  try {
    const dt = new Date(iso);
    const diff = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (diff < 30) return lang === 'en' ? 'just now' : 'hace un momento';
    if (diff < 60) return lang === 'en' ? `${diff}s ago` : `hace ${diff}s`;
    const m = Math.floor(diff / 60);
    if (m < 60) return lang === 'en' ? `${m}m ago` : `hace ${m}m`;
    const h = Math.floor(m / 60);
    return lang === 'en' ? `${h}h ago` : `hace ${h}h`;
  } catch { return null; }
}

function BasesDiamond({ runners }) {
  const r = runners || {};
  // 45deg rotated square; render second base at top, first to right, third to left, home at bottom.
  const cls = (on) => on ? 'fill-emerald-400 stroke-emerald-200' : 'fill-transparent stroke-slate-500';
  return (
    <svg viewBox="0 0 80 80" className="h-12 w-12" aria-label="bases" data-testid="mlb-live-bases">
      {/* 2B (top) */}
      <rect x="32" y="6"  width="16" height="16" transform="rotate(45 40 14)" className={cls(r.second)} strokeWidth="2" />
      {/* 1B (right) */}
      <rect x="58" y="32" width="16" height="16" transform="rotate(45 66 40)" className={cls(r.first)}  strokeWidth="2" />
      {/* 3B (left) */}
      <rect x="6"  y="32" width="16" height="16" transform="rotate(45 14 40)" className={cls(r.third)}  strokeWidth="2" />
      {/* home plate */}
      <rect x="32" y="58" width="16" height="16" transform="rotate(45 40 66)" className="fill-transparent stroke-slate-600" strokeWidth="1.5" />
    </svg>
  );
}

function OutsDots({ outs }) {
  const safeOuts = Math.max(0, Math.min(3, Number(outs) || 0));
  return (
    <div className="flex items-center gap-1" aria-label="outs" data-testid="mlb-live-outs">
      {[0, 1, 2].map((i) => (
        <Dot
          key={i}
          className={`h-5 w-5 ${i < safeOuts ? 'text-amber-300' : 'text-slate-600'}`}
          strokeWidth={i < safeOuts ? 3 : 2}
        />
      ))}
    </div>
  );
}

export function MLBLiveScoreboard({
  homeName,
  awayName,
  live,
  state,
  lastFetch,
  refreshing,
  onRefresh,
  lang = 'es',
}) {
  const label = STATE_LABELS[state] || STATE_LABELS.loading;
  const labelTxt = label[lang] || label.es;
  const isLiveOrFinal = state === 'live-data-ready' || state === 'live-data-partial' || state === 'final';
  const score   = live?.score || {};
  const inning  = live?.inning;
  const batter  = live?.current_batter;
  const pitcher = live?.current_pitcher;

  // Pretty inning string: "7th ▲" (top) / "7th ▼" (bottom).
  let inningStr = null;
  if (inning?.ordinal) {
    const arrow = inning.half === 'top' ? '▲' : inning.half === 'bottom' ? '▼' : '';
    inningStr = `${inning.ordinal} ${arrow}`.trim();
  }

  return (
    <div
      className="rounded-xl border border-border bg-card p-4 space-y-3"
      data-testid="mlb-live-scoreboard"
    >
      {/* Header — state badge + refresh */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[11px] font-semibold uppercase tracking-wide ${_stateBadgeStyles(state)}`}
          data-testid="mlb-live-state-badge"
        >
          {state === 'final' ? <Trophy className="h-3 w-3" /> :
           state === 'live-data-ready' ? <Activity className="h-3 w-3" /> :
           state === 'no-live-data' ? <Clock className="h-3 w-3" /> :
           <Circle className="h-3 w-3" />}
          {state === 'live-data-ready'   ? labelTxt :
           state === 'live-data-partial' ? (lang === 'en' ? 'PARTIAL' : 'PARCIAL') :
           state === 'final' ? (lang === 'en' ? 'FINAL' : 'FINAL') :
           state === 'no-live-data' ? (lang === 'en' ? 'NOT STARTED' : 'NO INICIA') :
           (lang === 'en' ? 'LOADING' : 'CARGANDO')}
        </span>
        {state === 'live-data-partial' && (
          <span className="text-[11px] text-amber-200/90" data-testid="mlb-live-partial-banner">
            {STATE_LABELS['live-data-partial'][lang]}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
          {lastFetch && (
            <span className="font-mono-tabular" data-testid="mlb-live-last-fetch">
              {lang === 'en' ? 'updated' : 'actualizado'} {_formatRelative(lastFetch, lang)}
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            disabled={refreshing}
            data-testid="mlb-live-refresh-button"
            className="h-7 px-2 text-cyan-200 hover:bg-cyan-500/10"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            <span className="ml-1 text-[11px]">{lang === 'en' ? 'Refresh' : 'Actualizar'}</span>
          </Button>
        </div>
      </div>

      {/* Scoreboard */}
      {isLiveOrFinal ? (
        <div className="grid grid-cols-3 items-center gap-3" data-testid="mlb-live-score">
          <div className="text-left">
            <div className="text-xs text-muted-foreground">{lang === 'en' ? 'Home' : 'Local'}</div>
            <div className="text-base font-semibold truncate">{homeName}</div>
            <div className="mt-0.5 text-4xl font-bold font-mono-tabular tabular-nums leading-none">
              {score.home != null ? score.home : '—'}
            </div>
          </div>
          <div className="text-center">
            {inningStr && (
              <div
                className="text-[11px] uppercase tracking-wider text-rose-200/90 font-semibold"
                data-testid="mlb-live-inning"
              >
                {inningStr}
              </div>
            )}
            <div className="flex flex-col items-center gap-2 mt-1">
              <BasesDiamond runners={live?.runners_on} />
              <OutsDots outs={live?.outs} />
              {(live?.balls != null && live?.strikes != null) && (
                <div className="text-[10.5px] font-mono-tabular text-muted-foreground" data-testid="mlb-live-count">
                  {live.balls} - {live.strikes}
                </div>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-muted-foreground">{lang === 'en' ? 'Away' : 'Visitante'}</div>
            <div className="text-base font-semibold truncate">{awayName}</div>
            <div className="mt-0.5 text-4xl font-bold font-mono-tabular tabular-nums leading-none">
              {score.away != null ? score.away : '—'}
            </div>
          </div>
        </div>
      ) : (
        <div
          className="text-center py-6 text-sm text-muted-foreground"
          data-testid="mlb-live-empty"
        >
          {labelTxt}
        </div>
      )}

      {/* Batter / Pitcher (only when live) */}
      {(batter || pitcher) && isLiveOrFinal && state !== 'final' && (
        <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border/60 text-[12px]">
          <div className="space-y-0.5" data-testid="mlb-live-batter">
            <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">
              {lang === 'en' ? 'At bat' : 'Bateando'}
            </div>
            <div className="font-semibold truncate">{batter?.name || '—'}</div>
          </div>
          <div className="space-y-0.5 text-right" data-testid="mlb-live-pitcher">
            <div className="text-[10.5px] uppercase tracking-wide text-muted-foreground">
              {lang === 'en' ? 'Pitching' : 'Lanzando'}
            </div>
            <div className="font-semibold truncate">{pitcher?.name || '—'}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MLBLiveScoreboard;
