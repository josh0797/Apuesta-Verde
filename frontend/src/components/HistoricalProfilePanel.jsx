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

export default HistoricalProfilePanel;
