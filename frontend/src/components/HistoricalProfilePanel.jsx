import { useState } from 'react';
import { Activity, ChevronDown, TrendingUp, TrendingDown, Minus, Calendar, Wind } from 'lucide-react';

/**
 * HistoricalProfilePanel — Renders the "Historial profundo" block for
 * basketball and baseball matches.
 *
 * Inputs:
 *   profile  — backend `basketballHistoricalProfile` OR
 *              `baseballHistoricalProfile` payload (must have available=true).
 *   sport    — "basketball" | "baseball" (drives labels/units/pitching block).
 *   testId   — data-testid prefix.
 *
 * Field-naming differences are encapsulated in `_metric()` so both
 * sports share the same visual layout while reading from the correct
 * keys: projectedTotalPoints / projectedTotalRuns,
 *       h2hTotalPointsAvg     / h2hTotalRunsAvg,
 *       pointsForAvg          / runsForAvg, etc.
 *
 * The panel is COLLAPSED by default; expanding shows per-team metrics
 * + (baseball only) pitching block + trend phrases in Spanish.
 */
export function HistoricalProfilePanel({ profile, sport = 'basketball', testId = 'historical-profile-panel' }) {
  const [open, setOpen] = useState(false);
  if (!profile || !profile.available) return null;

  const isBasketball = (sport || 'basketball').toLowerCase() === 'basketball';
  const isBaseball   = (sport || '').toLowerCase() === 'baseball';
  const unit        = isBasketball ? 'pts'    : 'carreras';
  const overUnit    = isBasketball ? 'puntos' : 'carreras';

  const home     = profile.home     || {};
  const away     = profile.away     || {};
  const combined = profile.combined || {};
  const pitching = profile.pitching || {};

  // Field-naming bridge: read the right keys per sport
  const projTotal   = isBasketball ? combined.projectedTotalPoints  : combined.projectedTotalRuns;
  const projPaceVal = isBasketball ? combined.projectedPace          : combined.f5ProjectedRuns;
  const projPaceLabel = isBasketball ? 'Pace proyectado'             : 'F5 proyectado';
  const h2hAvg      = isBasketball ? combined.h2hTotalPointsAvg     : combined.h2hTotalRunsAvg;

  const lean = combined.overUnderLean || 'NEUTRAL';
  const fit  = Number(combined.marketFitScore || 0);
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
            • últimos {gamesAnalyzed} {isBasketball ? 'partidos' : 'juegos'}
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
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                {isBasketball ? 'Proyección total' : 'Proyección carreras'}
              </div>
              <div className={`text-base font-semibold ${tone.accent}`}>
                {fmtNum(projTotal)} <span className="text-[10px] text-slate-400">{unit}</span>
              </div>
            </div>
            <div className="rounded-md bg-black/30 p-2" data-testid={`${testId}-pace`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                {projPaceLabel}
              </div>
              <div className="text-base font-semibold text-slate-200">
                {fmtNum(projPaceVal)}
                {isBaseball && combined.f5Lean && combined.f5Lean !== 'NEUTRAL' && (
                  <span className="ml-1 text-[10px] text-slate-400">
                    ({combined.f5Lean})
                  </span>
                )}
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

          {/* H2H badge */}
          {h2hAvg && (
            <div className="flex items-center gap-2 text-slate-400">
              <Calendar className="w-3.5 h-3.5" />
              <span>
                H2H reciente: promedio <span className="text-slate-200 font-medium">
                  {fmtNum(h2hAvg)} {unit}
                </span> por enfrentamiento.
              </span>
            </div>
          )}

          {/* Per-team blocks */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <TeamBlock label="Local"     data={home} unit={unit} isBaseball={isBaseball} testId={`${testId}-home`} />
            <TeamBlock label="Visitante" data={away} unit={unit} isBaseball={isBaseball} testId={`${testId}-away`} />
          </div>

          {/* Baseball-only: pitching block */}
          {isBaseball && (pitching.homeStarter || pitching.awayStarter || pitching.homeBullpen) && (
            <PitchingBlock pitching={pitching} testId={`${testId}-pitching`} />
          )}

          {/* Trend phrases */}
          {phrases.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
                Patrones detectados
              </div>
              <ul className="space-y-1 text-slate-200">
                {phrases.slice(0, 7).map((p, i) => (
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
            Datos calculados sobre los {gamesAnalyzed} {isBasketball ? 'partidos' : 'juegos'} finalizados más recientes.
            {isBasketball
              ? ' Pace/rating son proxies derivados del marcador (no posesiones reales).'
              : ' Stats de bateo provienen de la temporada; bullpen estima fatiga últimos 3-5 días.'}
          </div>
        </div>
      )}
    </div>
  );
}

function TeamBlock({ label, data, unit, isBaseball, testId }) {
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

  // Field-naming bridge
  const pointsForAvg     = isBaseball ? data.runsForAvg      : data.pointsForAvg;
  const pointsAgainstAvg = isBaseball ? data.runsAgainstAvg  : data.pointsAgainstAvg;
  const totalAvg         = isBaseball ? data.totalRunsAvg    : data.totalPointsAvg;

  return (
    <div className="rounded-md bg-black/30 p-2 space-y-1" data-testid={testId}>
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label} <span className="text-slate-500 normal-case">({data.gamesAnalyzed} {isBaseball ? 'juegos' : 'juegos'})</span>
        </div>
        <span className="inline-flex items-center gap-1 text-[10px] text-slate-300">
          {trendIcon} {trendLabel}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5 text-[11px]">
        <div>
          <span className="text-slate-400">Anotó:</span>{' '}
          <span className="text-slate-100 font-medium">
            {fmtNum(pointsForAvg)} <span className="text-slate-500">{unit}</span>
          </span>
        </div>
        <div>
          <span className="text-slate-400">Recibió:</span>{' '}
          <span className="text-slate-100 font-medium">
            {fmtNum(pointsAgainstAvg)} <span className="text-slate-500">{unit}</span>
          </span>
        </div>
        <div>
          <span className="text-slate-400">Total avg:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtNum(totalAvg)}</span>
        </div>
        <div>
          <span className="text-slate-400">Over rate:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtPct(data.overRate)}</span>
        </div>
        <div>
          <span className="text-slate-400">Sup. team-tot:</span>{' '}
          <span className="text-slate-100 font-medium">{fmtPct(data.exceededTeamTotalRate)}</span>
        </div>
        {isBaseball ? (
          <div>
            <span className="text-slate-400">OPS:</span>{' '}
            <span className="text-slate-100 font-medium">{fmtNum(data.opsTrend, 3)}</span>
          </div>
        ) : (
          <div>
            <span className="text-slate-400">B2B:</span>{' '}
            <span className="text-slate-100 font-medium">{data.backToBackCount ?? 0}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function PitchingBlock({ pitching, testId }) {
  const hs = pitching.homeStarter || {};
  const as = pitching.awayStarter || {};
  const hb = pitching.homeBullpen || {};
  const ab = pitching.awayBullpen || {};

  return (
    <div className="rounded-md bg-black/20 p-2 space-y-2" data-testid={testId}>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
        <Wind className="w-3 h-3" />
        Pitcheo + Bullpen
      </div>

      {/* Starters row */}
      <div className="grid grid-cols-2 gap-2">
        <div className="text-[11px]" data-testid={`${testId}-home-starter`}>
          <div className="text-slate-400 text-[10px] uppercase tracking-wide">Abridor local</div>
          <div className="text-slate-100 font-medium truncate">
            {hs.name || '—'}
          </div>
          <div className="text-slate-300 text-[10px]">
            ERA <span className="text-slate-100">{fmtNum(hs.era, 2)}</span>
            {' • '}WHIP <span className="text-slate-100">{fmtNum(hs.whip, 2)}</span>
          </div>
        </div>
        <div className="text-[11px]" data-testid={`${testId}-away-starter`}>
          <div className="text-slate-400 text-[10px] uppercase tracking-wide">Abridor visitante</div>
          <div className="text-slate-100 font-medium truncate">
            {as.name || '—'}
          </div>
          <div className="text-slate-300 text-[10px]">
            ERA <span className="text-slate-100">{fmtNum(as.era, 2)}</span>
            {' • '}WHIP <span className="text-slate-100">{fmtNum(as.whip, 2)}</span>
          </div>
        </div>
      </div>

      {/* Pitcher / Bullpen advantage */}
      <div className="flex flex-wrap gap-1.5">
        {pitching.pitcherAdvantage && pitching.pitcherAdvantage !== 'none' && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded-md border border-sky-500/30 bg-sky-500/10 text-sky-200 text-[10px]">
            Ventaja abridor: {pitching.pitcherAdvantage === 'home' ? 'Local' : 'Visitante'}
          </span>
        )}
        {pitching.bullpenAdvantage && pitching.bullpenAdvantage !== 'none' && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 text-[10px]">
            Bullpen descansado: {pitching.bullpenAdvantage === 'home' ? 'Local' : 'Visitante'}
          </span>
        )}
      </div>

      {/* Bullpen fatigue rows */}
      <div className="grid grid-cols-2 gap-2 text-[10px]">
        <BullpenLabel side="local" data={hb} />
        <BullpenLabel side="visitante" data={ab} />
      </div>
    </div>
  );
}

function BullpenLabel({ side, data }) {
  if (!data || !data.fatigueLabel) {
    return (
      <div className="text-slate-500 italic">Bullpen {side}: sin datos</div>
    );
  }
  const labelColor = data.fatigueLabel === 'fresh' ? 'text-emerald-300'
    : data.fatigueLabel === 'moderate' ? 'text-slate-200'
    : data.fatigueLabel === 'high' ? 'text-amber-300'
    : 'text-rose-300';
  const labelEs = {
    fresh: 'descansado',
    moderate: 'moderado',
    high: 'cargado',
    extreme: 'agotado',
  }[data.fatigueLabel] || data.fatigueLabel;
  return (
    <div>
      <span className="text-slate-400">Bullpen {side}:</span>{' '}
      <span className={`font-medium ${labelColor}`}>{labelEs}</span>{' '}
      <span className="text-slate-500">
        ({data.gamesPlayedRecent ?? '?'} jg/{data.lookbackDays ?? 3}d)
      </span>
    </div>
  );
}

function fmtNum(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return `${Math.round(Number(v) * 100)}%`;
}

export default HistoricalProfilePanel;
