import { useState } from 'react';
import { Activity, ChevronDown, TrendingUp, TrendingDown, Minus, Calendar } from 'lucide-react';

/**
 * HistoricalProfilePanel — Renders the "Historial profundo" block for
 * basketball (and, later, baseball) matches.
 *
 * Inputs (all optional — panel renders nothing when none are present):
 *   profile        — backend `basketballHistoricalProfile`/`baseballHistoricalProfile`
 *                    payload. Must have `available=true` to render.
 *   sport          — "basketball" | "baseball" (drives labels)
 *   testId         — data-testid prefix
 *
 * The panel is COLLAPSED by default and uses a calm sky/indigo tone so it
 * doesn't fight for attention with picks / editorial visuals.
 */
export function HistoricalProfilePanel({ profile, sport = 'basketball', testId = 'historical-profile-panel' }) {
  const [open, setOpen] = useState(false);
  if (!profile || !profile.available) return null;

  const home = profile.home || {};
  const away = profile.away || {};
  const combined = profile.combined || {};
  const isBasketball = (sport || 'basketball').toLowerCase() === 'basketball';
  const unit = isBasketball ? 'pts' : 'carreras';
  const overUnit = isBasketball ? 'puntos' : 'carreras';

  const lean = combined.overUnderLean || 'NEUTRAL';
  const fit = Number(combined.marketFitScore || 0);
  const frag = Number(combined.fragilityScore || 0);

  const tone = lean === 'OVER'
    ? { border: 'border-emerald-500/30', bg: 'bg-emerald-500/5', icon: 'text-emerald-300', accent: 'text-emerald-200' }
    : lean === 'UNDER'
      ? { border: 'border-sky-500/30', bg: 'bg-sky-500/5', icon: 'text-sky-300', accent: 'text-sky-200' }
      : { border: 'border-slate-500/30', bg: 'bg-slate-500/5', icon: 'text-slate-300', accent: 'text-slate-200' };

  const leanIcon = lean === 'OVER' ? <TrendingUp className="w-3.5 h-3.5" />
    : lean === 'UNDER' ? <TrendingDown className="w-3.5 h-3.5" />
    : <Minus className="w-3.5 h-3.5" />;

  const leanLabel = lean === 'OVER'
    ? `Lean Over ${overUnit}`
    : lean === 'UNDER'
      ? `Lean Under ${overUnit}`
      : 'Sin lean claro';

  const phrases = Array.isArray(combined.trendSummary) ? combined.trendSummary : [];
  const gamesAnalyzed = Math.min(home.gamesAnalyzed || 0, away.gamesAnalyzed || 0);

  const fmt = (v, digits = 1) => (v === null || v === undefined || Number.isNaN(Number(v)))
    ? '—'
    : Number(v).toFixed(digits);
  const pct = (v) => (v === null || v === undefined || Number.isNaN(Number(v)))
    ? '—'
    : `${Math.round(Number(v) * 100)}%`;

  return (
    <div
      className={`mt-3 rounded-lg border ${tone.border} ${tone.bg} text-xs`}
      data-testid={testId}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors"
        data-testid={`${testId}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Activity className={`w-4 h-4 ${tone.icon} shrink-0`} />
          <span className={`font-semibold ${tone.accent} truncate`}>
            Historial profundo
          </span>
          <span className="text-slate-400 hidden sm:inline">
            • últimos {gamesAnalyzed} partidos
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide bg-black/30 ${tone.accent}`}>
            {leanIcon}
            {leanLabel}
          </span>
          <ChevronDown
            className={`w-4 h-4 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </div>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-white/5">
          {/* Combined metrics summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-3">
            <div className="rounded-md bg-black/30 p-2" data-testid={`${testId}-projected`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">Proyección total</div>
              <div className={`text-base font-semibold ${tone.accent}`}>
                {fmt(combined.projectedTotalPoints, 1)} <span className="text-[10px] text-slate-400">{unit}</span>
              </div>
            </div>
            <div className="rounded-md bg-black/30 p-2" data-testid={`${testId}-pace`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                {isBasketball ? 'Pace proyectado' : 'Hits prom.'}
              </div>
              <div className="text-base font-semibold text-slate-200">
                {fmt(combined.projectedPace, 1)}
              </div>
            </div>
            <div className="rounded-md bg-black/30 p-2" data-testid={`${testId}-fit`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">Market fit</div>
              <div className="text-base font-semibold text-slate-200">
                {fit}<span className="text-[10px] text-slate-400">/100</span>
              </div>
            </div>
            <div className="rounded-md bg-black/30 p-2" data-testid={`${testId}-fragility`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">Fragilidad</div>
              <div className={`text-base font-semibold ${frag >= 60 ? 'text-amber-300' : 'text-slate-200'}`}>
                {frag}<span className="text-[10px] text-slate-400">/100</span>
              </div>
            </div>
          </div>

          {/* H2H badge if present */}
          {combined.h2hTotalPointsAvg && (
            <div className="flex items-center gap-2 text-slate-400">
              <Calendar className="w-3.5 h-3.5" />
              <span>
                H2H reciente: promedio <span className="text-slate-200 font-medium">
                  {fmt(combined.h2hTotalPointsAvg, 1)} {unit}
                </span> por enfrentamiento.
              </span>
            </div>
          )}

          {/* Per-team blocks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <TeamBlock label="Local" data={home} unit={unit} testId={`${testId}-home`} />
            <TeamBlock label="Visitante" data={away} unit={unit} testId={`${testId}-away`} />
          </div>

          {/* Trend phrases */}
          {phrases.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
                Patrones detectados
              </div>
              <ul className="space-y-1 text-slate-200">
                {phrases.slice(0, 6).map((p, i) => (
                  <li
                    key={i}
                    className="flex gap-2"
                    data-testid={`${testId}-trend-${i}`}
                  >
                    <span className={`${tone.accent} shrink-0`}>•</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Footer micro-note */}
          <div className="text-[10px] text-slate-500 italic pt-1">
            Datos calculados sobre los {gamesAnalyzed} partidos finalizados más recientes.
            Las cifras de pace / rating son aproximaciones derivadas del marcador final
            (sin datos de posesiones cuando la API no los expone).
          </div>
        </div>
      )}
    </div>
  );
}

function TeamBlock({ label, data, unit, testId }) {
  if (!data || data.missingData) {
    return (
      <div className="rounded-md bg-black/20 p-2" data-testid={testId}>
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label}
        </div>
        <div className="text-slate-500 italic mt-1 text-[11px]">Sin datos suficientes</div>
      </div>
    );
  }
  const trend = data.last5ScoringTrend;
  const trendIcon = trend === 'RISING'
    ? <TrendingUp className="w-3 h-3 text-emerald-300" />
    : trend === 'FALLING'
      ? <TrendingDown className="w-3 h-3 text-rose-300" />
      : <Minus className="w-3 h-3 text-slate-400" />;
  const trendLabel = trend === 'RISING' ? 'subiendo'
    : trend === 'FALLING' ? 'bajando'
    : trend === 'STABLE' ? 'estable'
    : '—';

  return (
    <div className="rounded-md bg-black/30 p-2 space-y-1" data-testid={testId}>
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label} <span className="text-slate-500 normal-case">({data.gamesAnalyzed} juegos)</span>
        </div>
        <span className="inline-flex items-center gap-1 text-[10px] text-slate-300">
          {trendIcon} {trendLabel}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5 text-[11px]">
        <div>
          <span className="text-slate-400">Anotó:</span>{' '}
          <span className="text-slate-100 font-medium">
            {fmtNum(data.pointsForAvg)} <span className="text-slate-500">{unit}</span>
          </span>
        </div>
        <div>
          <span className="text-slate-400">Recibió:</span>{' '}
          <span className="text-slate-100 font-medium">
            {fmtNum(data.pointsAgainstAvg)} <span className="text-slate-500">{unit}</span>
          </span>
        </div>
        <div>
          <span className="text-slate-400">Total avg:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtNum(data.totalPointsAvg)}</span>
        </div>
        <div>
          <span className="text-slate-400">Over rate:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtPct(data.overRate)}</span>
        </div>
        <div>
          <span className="text-slate-400">Sup. team-tot:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtPct(data.exceededTeamTotalRate)}</span>
        </div>
        <div>
          <span className="text-slate-400">B2B:</span>{' '}
          <span className="text-slate-100 font-medium">{data.backToBackCount ?? 0}</span>
        </div>
      </div>
    </div>
  );
}

function fmtNum(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

export default HistoricalProfilePanel;
