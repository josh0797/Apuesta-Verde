import { useState } from 'react';
import { Activity, ChevronDown, TrendingUp, TrendingDown, Minus, Calendar, Wind, HeartPulse, AlertTriangle, CheckCircle2, XCircle, Info } from 'lucide-react';

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
  // V2 — SINGLE SOURCE OF TRUTH consistency badge: when the orchestrator
  // computed the lean against the actual market line and detected a
  // mismatch with the recommended pick, `overUnderLeanDisplay` is set to
  // 'REVIEW_REQUIRED' and we show "⚠ Revisión requerida" instead of the
  // contradictory badge.
  const leanDisplay = combined.overUnderLeanDisplay || lean;
  const leanConsistency = combined.overUnderLeanConsistency || null;
  const leanConfidence = combined.overUnderLeanConfidence;
  const leanReason = combined.overUnderLeanReason;
  const isReviewRequired = leanDisplay === 'REVIEW_REQUIRED';
  const fit  = Number(combined.marketFitScore || 0);
  const frag = Number(combined.fragilityScore || 0);

  const tone = isReviewRequired
    ? { border: 'border-amber-500/40', bg: 'bg-amber-500/8', icon: 'text-amber-300', accent: 'text-amber-200' }
    : lean === 'OVER'
    ? { border: 'border-emerald-500/30', bg: 'bg-emerald-500/5', icon: 'text-emerald-300', accent: 'text-emerald-200' }
    : lean === 'UNDER'
      ? { border: 'border-sky-500/30', bg: 'bg-sky-500/5', icon: 'text-sky-300', accent: 'text-sky-200' }
      : { border: 'border-slate-500/30', bg: 'bg-slate-500/5', icon: 'text-slate-300', accent: 'text-slate-200' };

  const leanIcon = isReviewRequired ? <AlertTriangle className="w-3.5 h-3.5" />
    : lean === 'OVER' ? <TrendingUp className="w-3.5 h-3.5" />
    : lean === 'UNDER' ? <TrendingDown className="w-3.5 h-3.5" />
    : <Minus className="w-3.5 h-3.5" />;

  const leanLabel = isReviewRequired
    ? 'Revisión requerida'
    : lean === 'OVER'
      ? `Lean Over ${overUnit}`
      : lean === 'UNDER'
        ? `Lean Under ${overUnit}`
        : 'Sin lean claro';

  const phrases = Array.isArray(combined.trendSummary) ? combined.trendSummary : [];
  // MLB Pattern Alignment — when the backend has classified each phrase
  // as SUPPORTS / OPPOSES / NEUTRAL with respect to the final recommended
  // market, we render three distinct sections instead of a flat list.
  const patternAlignment = combined.patternAlignment || null;
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
          {/* V2 — Review-required notice. Shown when the orchestrator
              detected an inconsistency between the historical lean and
              the recommended pick. Replaces the user-confusing
              "LEAN OVER vs PICK UNDER" scenario. */}
          {isReviewRequired && leanConsistency?.warning ? (
            <div
              className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/8 px-3 py-2 text-[11px] text-amber-200 leading-snug flex items-start gap-2"
              data-testid={`${testId}-review-required`}
            >
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{leanConsistency.warning}</span>
            </div>
          ) : leanReason && !isReviewRequired ? (
            <div
              className={`mt-3 rounded-md border ${tone.border} ${tone.bg} px-3 py-1.5 text-[10.5px] ${tone.accent} leading-snug`}
              data-testid={`${testId}-lean-reason`}
            >
              {leanReason}
              {leanConfidence != null ? (
                <span className="ml-1 font-mono opacity-80">· conf {leanConfidence}/100</span>
              ) : null}
            </div>
          ) : null}

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

          {/* Baseball-only: Injured List block (GAP #4 — IL transparency) */}
          {isBaseball && profile.injuries && (
            (profile.injuries.home_il_count > 0 || profile.injuries.away_il_count > 0) && (
              <InjuriesBlock injuries={profile.injuries} testId={`${testId}-injuries`} />
            )
          )}

          {/* Basketball-only: enriched context blocks (Phase 2 mirror of MLB) */}
          {isBasketball && (profile.restAdvantage || profile.paceFactor || profile.keyPlayers) && (
            <BasketballExtrasBlock
              restAdvantage={profile.restAdvantage}
              paceFactor={profile.paceFactor}
              keyPlayers={profile.keyPlayers}
              testId={`${testId}-bk-extras`}
            />
          )}

          {/* Recent form split — Últimos 5 vs Últimos 15 juegos. Reads
              the `recentRunSplit`, `recentRunTrend` and `onBaseProfileL5`
              fields mirrored on the profile by the orchestrator. Only
              renders when at least one of the L5 averages is present. */}
          {isBaseball && (profile.recentRunSplit || profile.onBaseProfileL5) ? (
            <RecentFormSplitBlock
              runSplit={profile.recentRunSplit}
              runTrend={profile.recentRunTrend}
              onBase={profile.onBaseProfileL5}
              testId={`${testId}-recent-form`}
            />
          ) : null}

          {/* Mixed signals ribbon — when the legacy heuristic (15-game
              total runs vs league average) disagrees with the
              consolidated final lean (expected_runs vs market line).
              Shows the user WHY the header now says LEAN UNDER even
              though some sub-signals point Over. */}
          {isBaseball && combined.mixedSignals?.has_mixed_signals ? (
            <MixedSignalsBlock
              mixed={combined.mixedSignals}
              testId={`${testId}-mixed-signals`}
            />
          ) : null}

          {/* Trend phrases */}
          {patternAlignment ? (
            <PatternAlignmentSection
              alignment={patternAlignment}
              testId={`${testId}-patterns`}
            />
          ) : phrases.length > 0 && (
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

function InjuriesBlock({ injuries, testId }) {
  const homePlayers = Array.isArray(injuries.home_il_players) ? injuries.home_il_players : [];
  const awayPlayers = Array.isArray(injuries.away_il_players) ? injuries.away_il_players : [];
  const homeCount = Number(injuries.home_il_count || homePlayers.length || 0);
  const awayCount = Number(injuries.away_il_count || awayPlayers.length || 0);
  const totalCount = homeCount + awayCount;

  // Severity tone — 3+ on either side raises the panel to amber; 5+ to rose
  const maxSide = Math.max(homeCount, awayCount);
  const tone = maxSide >= 5
    ? { border: 'border-rose-500/40',   bg: 'bg-rose-500/10',   text: 'text-rose-200',   iconColor: 'text-rose-300',   chipBg: 'bg-rose-500/15' }
    : maxSide >= 3
    ? { border: 'border-amber-500/40',  bg: 'bg-amber-500/10',  text: 'text-amber-200',  iconColor: 'text-amber-300',  chipBg: 'bg-amber-500/15' }
    : { border: 'border-slate-500/30',  bg: 'bg-slate-500/5',   text: 'text-slate-200',  iconColor: 'text-slate-300',  chipBg: 'bg-slate-500/10' };

  return (
    <div
      className={`rounded-md ${tone.bg} border ${tone.border} p-2 space-y-2`}
      data-testid={testId}
    >
      <div className={`flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide font-semibold ${tone.text}`}>
        <span className="flex items-center gap-1.5">
          <HeartPulse className={`w-3.5 h-3.5 ${tone.iconColor}`} />
          Lesionados (IL)
        </span>
        <span className={`text-[10px] normal-case px-1.5 py-0.5 rounded-md ${tone.chipBg} ${tone.text}`}>
          {totalCount} {totalCount === 1 ? 'jugador' : 'jugadores'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <InjurySide label="Local"      players={homePlayers} count={homeCount} testId={`${testId}-home`} tone={tone} />
        <InjurySide label="Visitante"  players={awayPlayers} count={awayCount} testId={`${testId}-away`} tone={tone} />
      </div>

      {maxSide >= 3 && (
        <div className={`text-[10px] italic ${tone.text}`}>
          {maxSide >= 5
            ? 'Equipo muy diezmado: fragilidad elevada (+18). Evita líneas ajustadas o mercados de victorias claras.'
            : 'Equipo con bajas significativas: fragilidad elevada (+10).'}
        </div>
      )}

      <div className="text-[10px] text-slate-500 italic">
        Fuente: {injuries._source || 'MLB Stats API (roster/injuries)'}
      </div>
    </div>
  );
}

function InjurySide({ label, players, count, testId, tone }) {
  if (!count || players.length === 0) {
    return (
      <div className="rounded-md bg-black/20 p-2 text-[11px]" data-testid={testId}>
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold mb-1">
          {label}
        </div>
        <div className="text-slate-500 italic">Sin lesionados reportados</div>
      </div>
    );
  }
  // Show up to 6 players, then "+N más"
  const visible = players.slice(0, 6);
  const remaining = players.length - visible.length;
  return (
    <div className="rounded-md bg-black/20 p-2 text-[11px]" data-testid={testId}>
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label}
        </span>
        <span className={`text-[10px] font-medium ${tone.text}`}>
          {count} {count === 1 ? 'jugador' : 'jugadores'}
        </span>
      </div>
      <ul className="space-y-1">
        {visible.map((p, i) => (
          <li
            key={`${p.name || i}-${i}`}
            className="flex items-start gap-1.5 leading-tight"
            data-testid={`${testId}-player-${i}`}
          >
            <span className="text-slate-500 mt-[2px]">•</span>
            <span className="flex-1 min-w-0">
              <span className="text-slate-100 font-medium truncate inline-block max-w-full align-middle">
                {p.name || 'Jugador sin nombre'}
              </span>
              {p.position && (
                <span className="text-slate-500 ml-1.5">({p.position})</span>
              )}
              {p.status && (
                <span className="block text-[10px] text-slate-400 truncate">
                  {p.status}
                </span>
              )}
            </span>
          </li>
        ))}
        {remaining > 0 && (
          <li className="text-[10px] text-slate-500 italic pl-3" data-testid={`${testId}-more`}>
            +{remaining} más
          </li>
        )}
      </ul>
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

/**
 * BasketballExtrasBlock
 * ---------------------
 * Surfaces the three Phase-2 basketball enrichment blocks:
 *
 *   • restAdvantage  → días de descanso de cada lado y el side con la ventaja.
 *   • paceFactor     → factor pace proyectado vs media de la liga.
 *   • keyPlayers     → bajas/dudas con impact ofensivo/defensivo.
 *
 * The block stays inert when none of the three are present, so the
 * baseline 1c profile (no injuries / no pace / no kickoff) remains
 * visually unchanged.
 */
function BasketballExtrasBlock({ restAdvantage, paceFactor, keyPlayers, testId }) {
  const hasRest    = !!restAdvantage;
  const hasPace    = !!paceFactor;
  const homeKp     = (keyPlayers?.home || []);
  const awayKp     = (keyPlayers?.away || []);
  const hasPlayers = homeKp.length > 0 || awayKp.length > 0;
  if (!hasRest && !hasPace && !hasPlayers) return null;

  return (
    <div className="rounded-md border border-slate-700/40 bg-slate-900/30 p-2.5 space-y-2" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
        Contexto avanzado
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {hasRest && (
          <div
            className={`rounded-md border px-2 py-1.5 text-[11px] leading-snug ${
              restAdvantage.advantageSide === 'home'
                ? 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200'
                : restAdvantage.advantageSide === 'away'
                  ? 'border-cyan-500/30 bg-cyan-500/5 text-cyan-200'
                  : 'border-slate-500/30 bg-slate-500/5 text-slate-200'
            }`}
            data-testid={`${testId}-rest`}
          >
            <div className="text-slate-400 text-[10px] uppercase tracking-wide">Ventaja de descanso</div>
            <div className="mt-0.5 tabular-nums">
              Local {restAdvantage.homeRestDays}d · Visit. {restAdvantage.awayRestDays}d
            </div>
            <div className="text-[10px] text-slate-300/80 mt-0.5">
              {restAdvantage.advantageSide === 'home'
                ? `+${Math.abs(restAdvantage.edge).toFixed(1)}d a favor del local`
                : restAdvantage.advantageSide === 'away'
                  ? `+${Math.abs(restAdvantage.edge).toFixed(1)}d a favor del visitante`
                  : 'Sin ventaja relevante'}
            </div>
          </div>
        )}
        {hasPace && (
          <div
            className={`rounded-md border px-2 py-1.5 text-[11px] leading-snug ${
              paceFactor.code === 'HIGH'
                ? 'border-rose-500/30 bg-rose-500/5 text-rose-200'
                : paceFactor.code === 'LOW'
                  ? 'border-sky-500/30 bg-sky-500/5 text-sky-200'
                  : 'border-slate-500/30 bg-slate-500/5 text-slate-200'
            }`}
            data-testid={`${testId}-pace`}
          >
            <div className="text-slate-400 text-[10px] uppercase tracking-wide">Pace proyectado</div>
            <div className="mt-0.5 tabular-nums">
              {paceFactor.projectedPace.toFixed(1)} vs liga {paceFactor.leagueAvgPace.toFixed(0)}
            </div>
            <div className="text-[10px] text-slate-300/80 mt-0.5">
              Factor {paceFactor.factor.toFixed(2)} · {
                paceFactor.code === 'HIGH' ? 'Favorece total alto' :
                paceFactor.code === 'LOW'  ? 'Favorece total bajo' : 'Pace neutral'
              }
            </div>
          </div>
        )}
      </div>
      {hasPlayers && (
        <div className="rounded-md border border-amber-500/25 bg-amber-500/5 px-2 py-1.5" data-testid={`${testId}-players`}>
          <div className="text-amber-200 text-[10px] uppercase tracking-wide font-semibold">
            Bajas relevantes
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-0.5 mt-1 text-[11px] text-amber-100">
            {homeKp.length > 0 && (
              <div data-testid={`${testId}-players-home`}>
                <span className="text-amber-300/80 text-[10px] uppercase mr-1">Local:</span>
                {homeKp.map((p, i) => (
                  <span key={i} className="mr-2">
                    {p.name}{p.status && p.status !== 'out' ? ` (${p.status})` : ''}
                  </span>
                ))}
              </div>
            )}
            {awayKp.length > 0 && (
              <div data-testid={`${testId}-players-away`}>
                <span className="text-amber-300/80 text-[10px] uppercase mr-1">Visit.:</span>
                {awayKp.map((p, i) => (
                  <span key={i} className="mr-2">
                    {p.name}{p.status && p.status !== 'out' ? ` (${p.status})` : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * PatternAlignmentSection
 * -----------------------
 * Renders the historical pattern phrases split into three categories
 * relative to the final recommended market:
 *
 *   ✓ Apoyan la recomendación  (supports)
 *   ✗ Contradicen la recomendación  (opposes)
 *   • Neutrales / contexto  (neutral)
 *
 * Each section is collapsible; the consistency ribbon on top mirrors the
 * single source of truth the backend classifier emits.
 */
function PatternAlignmentSection({ alignment, testId }) {
  const supports = Array.isArray(alignment?.supports) ? alignment.supports : [];
  const opposes  = Array.isArray(alignment?.opposes)  ? alignment.opposes  : [];
  const neutral  = Array.isArray(alignment?.neutral)  ? alignment.neutral  : [];
  const total = supports.length + opposes.length + neutral.length;
  if (total === 0) return null;

  const consistency = alignment?.consistency || 'INFO_ONLY';
  const ribbon = (() => {
    switch (consistency) {
      case 'STRONG':
        return { label: 'Patrones alineados con la recomendación', tone: 'emerald', icon: <CheckCircle2 className="w-3.5 h-3.5" /> };
      case 'CONFLICTED':
        return { label: 'Patrones contradicen la recomendación', tone: 'rose', icon: <AlertTriangle className="w-3.5 h-3.5" /> };
      case 'MIXED':
        return { label: 'Lectura mixta — revisa argumentos en contra', tone: 'amber', icon: <AlertTriangle className="w-3.5 h-3.5" /> };
      case 'INFO_ONLY':
      default:
        return { label: 'Patrones informativos (sin lean claro)', tone: 'slate', icon: <Info className="w-3.5 h-3.5" /> };
    }
  })();
  const ribbonStyle = {
    emerald: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
    rose:    'border-rose-500/40   bg-rose-500/10   text-rose-200',
    amber:   'border-amber-500/40  bg-amber-500/10  text-amber-200',
    slate:   'border-slate-500/30  bg-slate-500/10  text-slate-200',
  }[ribbon.tone];

  return (
    <div className="space-y-2" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          Patrones detectados
          {alignment?.recommendedMarket && (
            <span className="ml-1.5 normal-case text-slate-500">
              · vs {alignment.recommendedMarket}
            </span>
          )}
        </div>
        <span
          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${ribbonStyle}`}
          data-testid={`${testId}-consistency`}
          title={alignment?.summary || ''}
        >
          {ribbon.icon}
          {alignment?.summary || ribbon.label}
        </span>
      </div>

      <PatternBucket
        title="Apoyan la recomendación"
        items={supports}
        kind="supports"
        testId={`${testId}-supports`}
      />
      <PatternBucket
        title="Contradicen la recomendación"
        items={opposes}
        kind="opposes"
        testId={`${testId}-opposes`}
      />
      <PatternBucket
        title="Neutrales / contexto"
        items={neutral}
        kind="neutral"
        testId={`${testId}-neutral`}
      />
    </div>
  );
}

function PatternBucket({ title, items, kind, testId }) {
  if (!items || items.length === 0) return null;
  const palette = {
    supports: { bg: 'bg-emerald-500/5', border: 'border-emerald-500/25', dot: 'text-emerald-300', icon: <CheckCircle2 className="w-3 h-3 text-emerald-300" /> },
    opposes:  { bg: 'bg-rose-500/5',    border: 'border-rose-500/25',    dot: 'text-rose-300',    icon: <XCircle className="w-3 h-3 text-rose-300" /> },
    neutral:  { bg: 'bg-slate-500/5',   border: 'border-slate-500/20',   dot: 'text-slate-300',   icon: <Minus className="w-3 h-3 text-slate-400" /> },
  }[kind] || { bg: 'bg-slate-500/5', border: 'border-slate-500/20', dot: 'text-slate-300', icon: null };

  return (
    <div
      className={`rounded-md border ${palette.border} ${palette.bg} p-2`}
      data-testid={testId}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        {palette.icon}
        <div className="text-[10px] uppercase tracking-wide text-slate-300 font-semibold">
          {title}
          <span className="ml-1 normal-case text-slate-500">({items.length})</span>
        </div>
      </div>
      <ul className="space-y-1 text-[11px] text-slate-100">
        {items.slice(0, 6).map((row, i) => (
          <li
            key={i}
            className="flex gap-2 leading-snug"
            data-testid={`${testId}-item-${i}`}
            title={row?.reason || ''}
          >
            <span className={`${palette.dot} shrink-0`}>•</span>
            <span>{row?.pattern || ''}</span>
          </li>
        ))}
      </ul>
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

function fmtDelta(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`;
}

const RUN_TREND_META_ES = {
  RISING_RUN_ENVIRONMENT:    { label: 'Subiendo',   tone: 'emerald', icon: TrendingUp },
  DECLINING_RUN_ENVIRONMENT: { label: 'Bajando',    tone: 'rose',    icon: TrendingDown },
  STABLE_RUN_ENVIRONMENT:    { label: 'Estable',    tone: 'slate',   icon: Minus },
  UNKNOWN_RUN_ENVIRONMENT:   { label: 'Sin datos',  tone: 'slate',   icon: Minus },
};

const OB_TREND_META_ES = {
  RISING_ON_BASE_PRESSURE:    { label: 'Subiendo',  tone: 'emerald', icon: TrendingUp },
  DECLINING_ON_BASE_PRESSURE: { label: 'Bajando',   tone: 'rose',    icon: TrendingDown },
  STABLE_ON_BASE_PRESSURE:    { label: 'Estable',   tone: 'slate',   icon: Minus },
  UNKNOWN_ON_BASE_PRESSURE:   { label: 'Sin datos', tone: 'slate',   icon: Minus },
};

const TREND_TONE_CLS = {
  emerald: 'text-emerald-300',
  rose:    'text-rose-300',
  slate:   'text-slate-400',
};

/**
 * RecentFormSplitBlock
 * --------------------
 * Compares Últimos 5 vs Últimos 15 juegos for both run-environment and
 * on-base pressure. The data already lives on
 * ``baseballHistoricalProfile`` as ``recentRunSplit`` (totals) and
 * ``onBaseProfileL5`` (hits + walks + HBP + OBP) when the orchestrator
 * was able to reach MLB Stats API (12h cache).
 *
 * Renders three columns for the run block (Local / Visitante /
 * Combinado) and two columns for the on-base block (Local / Visitante),
 * each with L15, L5, Δ and a trend chip.
 */
function RecentFormSplitBlock({ runSplit, runTrend, onBase, testId }) {
  const rs   = runSplit || {};
  const home = onBase?.home || {};
  const away = onBase?.away || {};
  const homeTrendCode = home.trend || 'UNKNOWN_ON_BASE_PRESSURE';
  const awayTrendCode = away.trend || 'UNKNOWN_ON_BASE_PRESSURE';
  const combinedRunTrendCode = runTrend || 'UNKNOWN_RUN_ENVIRONMENT';

  // Derive per-side run trend from delta (RISING_RUN_THRESHOLD = 1.25 in backend).
  const sideRunTrend = (delta) => {
    if (delta === null || delta === undefined || Number.isNaN(Number(delta))) {
      return 'UNKNOWN_RUN_ENVIRONMENT';
    }
    const d = Number(delta);
    if (d >= 1.25)  return 'RISING_RUN_ENVIRONMENT';
    if (d <= -1.25) return 'DECLINING_RUN_ENVIRONMENT';
    return 'STABLE_RUN_ENVIRONMENT';
  };

  const homeDelta = rs.runs_scored_delta_5_vs_15_home;
  const awayDelta = rs.runs_scored_delta_5_vs_15_away;
  const homeRunTrend = sideRunTrend(homeDelta);
  const awayRunTrend = sideRunTrend(awayDelta);

  // Hide the entire block when nothing meaningful is available.
  const anyRun = rs.runs_scored_avg_last_5_home != null
              || rs.runs_scored_avg_last_15_home != null
              || rs.runs_scored_avg_last_5_away != null;
  const anyOb  = home.times_on_base_avg_last_5 != null
              || away.times_on_base_avg_last_5 != null;
  if (!anyRun && !anyOb) return null;

  return (
    <div
      className="rounded-md border border-slate-700/40 bg-slate-900/30 p-2.5 space-y-2.5"
      data-testid={testId}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-300 font-semibold">
        <TrendingUp className="w-3 h-3 text-cyan-300" />
        Tendencia carreras 5 vs 15 juegos
      </div>

      {anyRun ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
          <RunTrendCell
            label="Local"
            l15={rs.runs_scored_avg_last_15_home}
            l5={rs.runs_scored_avg_last_5_home}
            delta={homeDelta}
            trendCode={homeRunTrend}
            unit="anotó / juego"
            testId={`${testId}-runs-home`}
          />
          <RunTrendCell
            label="Visitante"
            l15={rs.runs_scored_avg_last_15_away}
            l5={rs.runs_scored_avg_last_5_away}
            delta={awayDelta}
            trendCode={awayRunTrend}
            unit="anotó / juego"
            testId={`${testId}-runs-away`}
          />
          <RunTrendCell
            label="Combinado"
            l15={rs.total_runs_avg_last_15}
            l5={rs.total_runs_avg_last_5}
            delta={rs.total_runs_delta_5_vs_15}
            trendCode={combinedRunTrendCode}
            unit="total / juego"
            testId={`${testId}-runs-combined`}
            emphasis
          />
        </div>
      ) : (
        <div className="text-[10px] text-slate-500 italic">
          Sin datos de L5/L15 para este partido.
        </div>
      )}

      {anyOb ? (
        <>
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-300 font-semibold pt-1">
            <Activity className="w-3 h-3 text-cyan-300" />
            Presión en base 5 vs 15 juegos
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
            <OnBaseTrendCell
              label="Local"
              data={home}
              trendCode={homeTrendCode}
              testId={`${testId}-ob-home`}
            />
            <OnBaseTrendCell
              label="Visitante"
              data={away}
              trendCode={awayTrendCode}
              testId={`${testId}-ob-away`}
            />
          </div>
        </>
      ) : null}

      <div className="text-[10px] text-slate-500 italic pt-0.5">
        Fuente: MLB Stats API · lastXGames. Caché 12h.
      </div>
    </div>
  );
}

function RunTrendCell({ label, l15, l5, delta, trendCode, unit, testId, emphasis }) {
  const meta = RUN_TREND_META_ES[trendCode] || RUN_TREND_META_ES.UNKNOWN_RUN_ENVIRONMENT;
  const Icon = meta.icon;
  const toneCls = TREND_TONE_CLS[meta.tone] || TREND_TONE_CLS.slate;
  return (
    <div
      className={`rounded-md ${emphasis ? 'bg-cyan-500/[0.06] border border-cyan-500/25' : 'bg-black/30 border border-slate-700/30'} p-2 space-y-0.5`}
      data-testid={testId}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label}
        </span>
        <span className={`inline-flex items-center gap-1 text-[10px] ${toneCls}`}>
          <Icon className="w-3 h-3" />
          {meta.label}
        </span>
      </div>
      <div className="text-[10.5px] text-slate-300 leading-tight">
        L15: <span className="text-slate-100 font-medium tabular-nums">{fmtNum(l15)}</span>
        <span className="text-slate-500"> · </span>
        L5: <span className="text-slate-100 font-medium tabular-nums">{fmtNum(l5)}</span>
      </div>
      <div className={`text-[10.5px] tabular-nums ${toneCls}`}>
        Δ {fmtDelta(delta)} <span className="text-slate-500">({unit})</span>
      </div>
    </div>
  );
}

function OnBaseTrendCell({ label, data, trendCode, testId }) {
  const meta = OB_TREND_META_ES[trendCode] || OB_TREND_META_ES.UNKNOWN_ON_BASE_PRESSURE;
  const Icon = meta.icon;
  const toneCls = TREND_TONE_CLS[meta.tone] || TREND_TONE_CLS.slate;
  const l15 = data?.times_on_base_avg_last_15;
  const l5  = data?.times_on_base_avg_last_5;
  const delta = data?.times_on_base_delta_5_vs_15;
  // Secondary metrics surfaced 2026-06: hits / walks / HR with their own
  // L15/L5/Δ so the panel actually shows the breakdown the user
  // requested instead of only the aggregate "times-on-base".
  const subRows = [
    { key: 'hits',      label: 'Hits',  l15: data?.hits_avg_last_15,       l5: data?.hits_avg_last_5,       d: data?.hits_delta_5_vs_15 },
    { key: 'walks',     label: 'BB',    l15: data?.walks_avg_last_15,      l5: data?.walks_avg_last_5,      d: data?.walks_delta_5_vs_15 },
    { key: 'home_runs', label: 'HR',    l15: data?.home_runs_avg_last_15,  l5: data?.home_runs_avg_last_5,  d: data?.home_runs_delta_5_vs_15 },
  ].filter((r) => r.l15 != null || r.l5 != null);

  return (
    <div
      className="rounded-md bg-black/30 border border-slate-700/30 p-2 space-y-0.5"
      data-testid={testId}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">
          {label}
        </span>
        <span className={`inline-flex items-center gap-1 text-[10px] ${toneCls}`}>
          <Icon className="w-3 h-3" />
          {meta.label}
        </span>
      </div>
      <div className="text-[10.5px] text-slate-300 leading-tight">
        L15: <span className="text-slate-100 font-medium tabular-nums">{fmtNum(l15)}</span>
        <span className="text-slate-500"> · </span>
        L5: <span className="text-slate-100 font-medium tabular-nums">{fmtNum(l5)}</span>
      </div>
      <div className={`text-[10.5px] tabular-nums ${toneCls}`}>
        Δ {fmtDelta(delta)} <span className="text-slate-500">(veces en base / juego)</span>
      </div>
      {subRows.length > 0 ? (
        <div className="pt-1 mt-0.5 border-t border-slate-700/30 space-y-0.5">
          {subRows.map((row) => {
            const d = row.d;
            const sign = d != null ? (d > 0 ? 'text-emerald-300' : d < 0 ? 'text-rose-300' : 'text-slate-400') : 'text-slate-500';
            return (
              <div
                key={row.key}
                className="grid grid-cols-[40px_minmax(0,1fr)] gap-1 text-[10px] tabular-nums"
                data-testid={`${testId}-${row.key}`}
              >
                <span className="text-slate-400">{row.label}:</span>
                <span className="text-slate-300">
                  L15 <span className="text-slate-100">{fmtNum(row.l15, row.key === 'home_runs' ? 2 : 1)}</span>
                  <span className="text-slate-500"> · </span>
                  L5 <span className="text-slate-100">{fmtNum(row.l5, row.key === 'home_runs' ? 2 : 1)}</span>
                  <span className="text-slate-500"> · </span>
                  <span className={sign}>Δ {fmtDelta(d, row.key === 'home_runs' ? 2 : 1)}</span>
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
      {(data?.obp_last_5 != null || data?.obp_last_15 != null) ? (
        <div className="text-[10px] text-slate-400 tabular-nums">
          OBP L15: <span className="text-slate-200">{fmtNum(data.obp_last_15, 3)}</span>
          <span className="text-slate-500"> · </span>
          L5: <span className="text-slate-200">{fmtNum(data.obp_last_5, 3)}</span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * MixedSignalsBlock
 * -----------------
 * Renders when the consolidated final lean (expected_runs vs market line)
 * disagrees with the legacy historical heuristic OR with the
 * recent_run_trend / on_base_pressure_trend. Lists each side's signals
 * so the user can audit the resolution.
 */
const MIXED_SIGNAL_LABELS_ES = {
  HISTORICAL_HEURISTIC_LEAN_OVER:  'Heurística histórica 15j: Lean Over',
  HISTORICAL_HEURISTIC_LEAN_UNDER: 'Heurística histórica 15j: Lean Under',
  RISING_RUN_ENVIRONMENT:          'Tendencia reciente subiendo (L5 > L15)',
  DECLINING_RUN_ENVIRONMENT:       'Tendencia reciente bajando (L5 < L15)',
  RISING_ON_BASE_PRESSURE:         'Presión en base subiendo',
  DECLINING_ON_BASE_PRESSURE:      'Presión en base bajando',
  EXPECTED_RUNS_BELOW_LINE:        'Expected runs por debajo de la línea',
  EXPECTED_RUNS_ABOVE_LINE:        'Expected runs por encima de la línea',
  LOW_VARIANCE_GAME:               'Juego de baja varianza',
  OVER_SURVIVAL_LOW:               'Over Survival bajo',
};

function MixedSignalsBlock({ mixed, testId }) {
  const over  = Array.isArray(mixed?.over_signals)  ? mixed.over_signals  : [];
  const under = Array.isArray(mixed?.under_signals) ? mixed.under_signals : [];
  const resolution = mixed?.final_resolution || 'NEUTRAL';
  const resolutionLabel = {
    LEAN_UNDER: 'Lean Under',
    LEAN_OVER:  'Lean Over',
    NEUTRAL:    'Sin lean claro',
  }[resolution] || resolution;
  const resolutionTone = resolution === 'LEAN_UNDER'
    ? 'border-sky-500/40 bg-sky-500/10 text-sky-100'
    : resolution === 'LEAN_OVER'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100'
      : 'border-slate-500/40 bg-slate-500/10 text-slate-100';

  return (
    <div
      className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] p-2.5 space-y-2"
      data-testid={testId}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-amber-200 font-semibold">
        <AlertTriangle className="w-3 h-3" />
        Señales mixtas detectadas
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
        <SignalColumn
          title="Apuntan a Over"
          tone="emerald"
          icon={<TrendingUp className="w-3 h-3 text-emerald-300" />}
          items={over}
          testId={`${testId}-over`}
        />
        <SignalColumn
          title="Apuntan a Under"
          tone="sky"
          icon={<TrendingDown className="w-3 h-3 text-sky-300" />}
          items={under}
          testId={`${testId}-under`}
        />
      </div>
      <div
        className={`rounded-md border px-2 py-1.5 text-[11px] ${resolutionTone}`}
        data-testid={`${testId}-resolution`}
      >
        <span className="opacity-80 mr-1.5">Resolución final del engine:</span>
        <span className="font-semibold">{resolutionLabel}</span>
      </div>
    </div>
  );
}

function SignalColumn({ title, tone, icon, items, testId }) {
  const safeItems = Array.isArray(items) ? items : [];
  const bg = {
    emerald: 'bg-emerald-500/[0.04] border-emerald-500/25',
    sky:     'bg-sky-500/[0.04] border-sky-500/25',
  }[tone] || 'bg-slate-500/[0.04] border-slate-500/25';
  return (
    <div className={`rounded-md border ${bg} p-2`} data-testid={testId}>
      <div className="flex items-center gap-1 mb-1 text-[10px] uppercase tracking-wide text-slate-300 font-semibold">
        {icon}
        {title}
        <span className="ml-1 normal-case text-slate-500">({safeItems.length})</span>
      </div>
      {safeItems.length === 0 ? (
        <div className="text-[10px] text-slate-500 italic">Sin señales activas</div>
      ) : (
        <ul className="space-y-0.5">
          {safeItems.map((code, i) => (
            <li
              key={`${code}-${i}`}
              className="flex gap-1.5 text-[10.5px] text-slate-100 leading-tight"
              data-testid={`${testId}-item-${i}`}
            >
              <span className="text-slate-500 shrink-0">•</span>
              <span>{MIXED_SIGNAL_LABELS_ES[code] || code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default HistoricalProfilePanel;
