import { useState } from 'react';
import { ChevronDown, Target, Activity, Gauge, Link as LinkIcon, AlertTriangle, Shield } from 'lucide-react';

/**
 * MLBScriptPanel — visualises the v2 Margin & Total Script Engine output.
 *
 * Renders the per-pick `_mlb_script_v2` payload produced by
 * /api/mlb/day (and bubbled through /api/analysis/run when sport=baseball):
 *
 *   marginProjection, coverProbability, runLineScore, fragilityScore
 *   bestLine, safeLine, aggressiveLine, recommendedLine, lineSafetyScore
 *   sameGameCorrelation (+ reason), pickType (+ reason)
 *   reasons[], risks[]
 *
 * Constraints:
 *  - **Baseball-only** — caller (MatchCard) must skip rendering for other sports.
 *  - Collapsible, opt-in: hidden by default to keep MatchCard compact.
 *  - Renders nothing when the payload is empty / missing.
 *  - Does NOT modify the UI of basketball or football pickcards.
 */

const PICK_TYPE_LABEL = {
  DOMINANT_FAVORITE_RUN_LINE: 'Run Line dominante',
  SMART_LOW_OVER:             'Over protegido (línea baja)',
  PITCHER_UNDER:              'Under por abridores',
  F5_EDGE:                    'Edge F5 (primeras 5)',
  TEAM_TOTAL_EDGE:            'Team Total',
  SAME_GAME_CORRELATED_PAIR:  'Par mismo juego (correlación)',
  GENERIC:                    'Pick MLB',
};

const PICK_TYPE_TONE = {
  DOMINANT_FAVORITE_RUN_LINE: 'emerald',
  SMART_LOW_OVER:             'amber',
  PITCHER_UNDER:              'sky',
  F5_EDGE:                    'violet',
  TEAM_TOTAL_EDGE:            'rose',
  SAME_GAME_CORRELATED_PAIR:  'cyan',
  GENERIC:                    'slate',
};

const SGC_LABEL = {
  POSITIVE: { es: 'Correlación positiva', tone: 'emerald' },
  NEGATIVE: { es: 'Correlación negativa', tone: 'rose' },
  NEUTRAL:  { es: 'Sin correlación clara', tone: 'slate' },
};

function asNumber(v, fallback = null) {
  if (v === null || v === undefined) return fallback;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

function MetricRow({ icon: Icon, label, value, hint, tone = 'slate', testId }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5" data-testid={testId}>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {Icon ? <Icon className="h-3.5 w-3.5 text-muted-foreground/80" /> : null}
        <span>{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className={`text-[12px] font-medium tabular-nums tone-${tone}`}>{value}</span>
        {hint ? <span className="text-[10px] text-muted-foreground/70">{hint}</span> : null}
      </div>
    </div>
  );
}

export function MLBScriptPanel({
  scriptV2,
  scriptV5,
  parlay,
  underFragilityWarning = null,
  scriptPickMismatchNarrative = null,
  scriptPickMismatchDetails = null,   // FIX #4b — structured discrepancy list
  biasPenaltyMeta = null,
  activeSeriesContext = null,
  seriesDegradation = null,
  seriesTotalSignal = null,    // D9.3-B — quantitative signal + score breakdown
  modelVerification = null,
  activeSeriesBlock = null,
  chosenMarket = null,        // e.g. "Run Line", "Total Runs Over", etc.
  ilPenalty = null,           // GAP #1 — {er_adjustment, confidence_penalty, ...}
  testId,
}) {
  const [expanded, setExpanded] = useState(false);

  if (!scriptV2 || typeof scriptV2 !== 'object') return null;

  const {
    pickType,
    pickTypeReason,
    marginProjection,
    coverProbability,
    runLineScore,
    fragilityScore,
    expectedRuns,
    bestLine,
    safeLine,
    aggressiveLine,
    recommendedLine,
    lineSafetyScore,
    sameGameCorrelation,
    sameGameCorrelationReason,
    reasons = [],
    risks = [],
    // BUGFIX — new fields surfaced from the engine.
    edgeVsLine,
    probabilityModel,
    probabilityUnder,
    probabilityOver,
    // FIX #2 — pick-aware margin (positive when the pattern supports the pick).
    marginVsLine,
    marginVsLineSide,
    // FIX #1 — legacy fragility kept for audit/transparency.
    _legacyFragilityScore: legacyFragility,
    _fragilitySource: fragilitySource,
  } = scriptV2;

  const hasAnything = Boolean(
    pickType ||
    marginProjection !== undefined ||
    coverProbability !== undefined ||
    recommendedLine ||
    reasons.length || risks.length,
  );
  if (!hasAnything) return null;

  const typeLabel = PICK_TYPE_LABEL[pickType] || PICK_TYPE_LABEL.GENERIC;
  const typeTone  = PICK_TYPE_TONE[pickType]  || 'slate';
  const sgc       = SGC_LABEL[String(sameGameCorrelation || '').toUpperCase()];

  const marginNum  = asNumber(marginProjection);
  const marginVsLineNum = asNumber(marginVsLine);
  const coverNum   = asNumber(coverProbability);
  const rlsNum     = asNumber(runLineScore);
  const erNum      = asNumber(expectedRuns);
  const fragNum    = asNumber(fragilityScore);
  const legacyFragNum = asNumber(legacyFragility);
  const lineSafeNum= asNumber(lineSafetyScore);
  const edgeNum    = asNumber(edgeVsLine);
  const probUnderNum = asNumber(probabilityUnder);
  const probOverNum  = asNumber(probabilityOver);

  return (
    <div
      className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.03] p-3 space-y-2"
      data-testid={testId || 'mlb-script-panel'}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 text-left group"
        aria-expanded={expanded}
        data-testid={`${testId || 'mlb-script-panel'}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="micro-label">MLB SCRIPT v2</span>
          <span
            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${typeTone}`}
            data-testid={`${testId || 'mlb-script'}-type-badge`}
          >
            <Target className="h-3 w-3" />
            {typeLabel}
          </span>
          {sgc ? (
            <span
              className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-${sgc.tone}`}
              data-testid={`${testId || 'mlb-script'}-sgc-badge`}
            >
              <LinkIcon className="h-3 w-3" />
              {sgc.es}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0 text-[10px] text-muted-foreground">
          {marginVsLineNum !== null ? (
            <span className="tabular-nums" data-testid={`${testId || 'mlb-script'}-margin-vs-line-summary`}>
              {marginVsLineNum >= 0 ? '+' : ''}{marginVsLineNum.toFixed(2)} vs línea
            </span>
          ) : marginNum !== null ? (
            <span className="tabular-nums" data-testid={`${testId || 'mlb-script'}-margin-summary`}>
              {marginNum >= 0 ? '+' : ''}{marginNum.toFixed(2)} marg.
            </span>
          ) : null}
          {coverNum !== null ? (
            <span className="tabular-nums">{coverNum.toFixed(1)}% cover</span>
          ) : null}
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {/* FIX #4 — Script ↔ Pick mismatch warning ribbon, expanded with
          structured details (label / value / interpretation / severity)
          when the orchestrator attached `script_pick_mismatch_details`. */}
      {(scriptPickMismatchNarrative || (Array.isArray(scriptPickMismatchDetails) && scriptPickMismatchDetails.length > 0)) ? (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11.5px] leading-snug text-amber-100 space-y-1.5"
          data-testid={`${testId || 'mlb-script'}-script-pick-mismatch`}
        >
          {scriptPickMismatchNarrative ? (
            <div className="flex items-start gap-1.5 font-medium">
              <span aria-hidden>⚠</span>
              <span>{String(scriptPickMismatchNarrative).replace(/^⚠\s*/, '')}</span>
            </div>
          ) : null}
          {Array.isArray(scriptPickMismatchDetails) && scriptPickMismatchDetails.length > 0 ? (
            <ul className="space-y-1 pl-4 text-[10.5px]" data-testid={`${testId || 'mlb-script'}-discrepancy-details`}>
              {scriptPickMismatchDetails.map((d, i) => {
                const sevColor = d?.severity === 'high'
                  ? 'text-rose-200'
                  : d?.severity === 'medium'
                    ? 'text-amber-200'
                    : 'text-emerald-200';
                return (
                  <li key={i} className="flex flex-col gap-0.5">
                    <div className="flex items-baseline gap-1.5 tabular-nums">
                      <span className="font-semibold opacity-90">{d?.label}:</span>
                      <span className={`font-bold ${sevColor}`}>{d?.value}</span>
                    </div>
                    {d?.interpretation ? (
                      <div className="opacity-80 italic pl-0.5">{d.interpretation}</div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* FIX #5 — Under + high Fragility warning ribbon */}
      {underFragilityWarning?.triggered ? (
        <div
          className="rounded-md border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-[11px] text-rose-200 leading-snug space-y-0.5"
          data-testid={`${testId || 'mlb-script'}-under-fragility-warning`}
        >
          <div className="font-medium">{underFragilityWarning.message}</div>
          {underFragilityWarning.alternative_suggested ? (
            <div className="text-rose-300/90">
              Alternativa sugerida:{' '}
              <span className="font-semibold tabular-nums">
                {underFragilityWarning.alternative_suggested}
              </span>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* FIX #6 — Bias penalty applied ribbon (low-key, informational) */}
      {biasPenaltyMeta ? (
        <div
          className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-1 text-[10.5px] text-amber-200/90 leading-snug"
          data-testid={`${testId || 'mlb-script'}-bias-penalty`}
        >
          Penalización de bias diario aplicada: -10 pts (mercado dominante:{' '}
          <span className="font-semibold uppercase">
            {biasPenaltyMeta.dominant_polarity || 'duplicado'}
          </span>
          {biasPenaltyMeta.share != null
            ? ` · ${(Number(biasPenaltyMeta.share) * 100).toFixed(0)}% del día`
            : ''}
          ).
        </div>
      ) : null}

      {/* Active Series Context Badge (Module #1 — D9.3-A hotfix).
            Renders one of three honest states:
              (1) ACTIVE_SERIES_CONFIRMED → full breakdown + over/under
                  line-aware counts + "muestra limitada" badge when n<3.
              (2) ACTIVE_SERIES_NO_COMPLETED_GAMES / SCORE_MISSING /
                  UNRESOLVED → truthful message, NO promedio / NO over
                  rate (zero observations does NOT mean zero runs).
              (3) Falsy / missing payload → nothing.
      */}
      {activeSeriesContext && activeSeriesContext.series_state ? (() => {
        const state = activeSeriesContext.series_state;
        const nValid = activeSeriesContext.games_in_series || 0;
        const games = activeSeriesContext.games_detail || [];
        const isRunLine = (chosenMarket || '').toLowerCase().includes('run line');
        const refLine = activeSeriesContext.reference_line ?? 9.5;

        // Truthful "no completed games yet" message — D9.3-A spec.
        if (state !== 'ACTIVE_SERIES_CONFIRMED' || nValid === 0) {
          const stateMessage = {
            ACTIVE_SERIES_NO_COMPLETED_GAMES: 'La serie actual todavía no tiene partidos finalizados.',
            ACTIVE_SERIES_SCORE_MISSING:      'Marcador no disponible para los partidos previos de la serie.',
            ACTIVE_SERIES_UNRESOLVED:         'Serie activa no resoluble con los datos disponibles.',
          }[state] || 'La serie actual todavía no tiene partidos finalizados.';
          return (
            <div
              className="rounded-md border px-2.5 py-1.5 text-[11.5px] leading-snug space-y-1 border-slate-500/30 bg-slate-500/5 text-slate-200"
              data-testid={`${testId || 'mlb-script'}-series-context`}
              data-series-state={state}
            >
              <div className="flex items-center gap-1.5 font-semibold">
                <span aria-hidden>📊</span>
                <span>Contexto de serie activa</span>
                <span
                  className="ml-auto inline-block px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wide bg-slate-500/15 text-slate-200 border border-slate-500/30"
                  data-testid={`${testId || 'mlb-script'}-series-state-badge`}
                >
                  Sin muestra válida
                </span>
              </div>
              <div className="text-[10.5px] opacity-90" data-testid={`${testId || 'mlb-script'}-series-empty-message`}>
                {stateMessage}
              </div>
            </div>
          );
        }

        // CONFIRMED path — there's at least 1 valid completed game.
        const nextG = activeSeriesContext.next_game_number || (games.length + 1);
        const isHotAvg  = Number(activeSeriesContext.total_runs_avg || 0) > 12;
        // Line-aware counts (fallback to derived counts if backend didn't send them).
        const overCount  = activeSeriesContext.over_count  ?? games.filter(g => Number(g.total_runs) >  refLine).length;
        const underCount = activeSeriesContext.under_count ?? games.filter(g => Number(g.total_runs) <  refLine).length;
        const limitedSample = nValid < 3;
        return (
          <div
            className={`rounded-md border px-2.5 py-1.5 text-[11.5px] leading-snug space-y-1 ${
              activeSeriesContext.series_override
                ? 'border-rose-500/45 bg-rose-500/10 text-rose-200'
                : 'border-amber-500/30 bg-amber-500/5 text-amber-200'
            }`}
            data-testid={`${testId || 'mlb-script'}-series-context`}
            data-series-state={state}
          >
            <div className="flex items-center gap-1.5 font-semibold">
              <span aria-hidden>📊</span>
              <span>Contexto de serie activa (G{nextG})</span>
              {/* Hide Lean badge entirely when chosen market is Run Line.
                  Run Line picks consume the series context as confirmation,
                  not as Over/Under signal. */}
              {!isRunLine && activeSeriesContext.series_lean && activeSeriesContext.series_lean !== 'NEUTRAL' && (
                <span className={`ml-auto inline-block px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                  activeSeriesContext.series_lean === 'OVER'
                    ? 'bg-rose-500/20 text-rose-100 border border-rose-500/40'
                    : 'bg-sky-500/15 text-sky-100 border border-sky-500/30'
                }`}>
                  Lean: {activeSeriesContext.series_lean}
                </span>
              )}
            </div>
            {/* Per-game breakdown — G1, G2, ... with home/away score + total. */}
            {games.length > 0 && (
              <ul className="text-[10.5px] font-mono-tabular space-y-0.5 pl-1" data-testid={`${testId || 'mlb-script'}-series-games-list`}>
                {games.map((g) => (
                  <li key={g.game_number} className="flex items-baseline gap-1" data-testid={`${testId || 'mlb-script'}-series-game-${g.game_number}`}>
                    <span className="text-foreground/90 font-semibold">G{g.game_number}:</span>
                    <span className="truncate">
                      {g.home_team} <span className="font-bold">{g.home}</span>
                      {' - '}
                      <span className="font-bold">{g.away}</span> {g.away_team}
                    </span>
                    <span className="ml-auto opacity-80">= {g.total_runs} carreras</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="text-[10.5px] flex items-center gap-2 flex-wrap">
              <span>
                Promedio:{' '}
                <span className={`font-bold tabular-nums ${
                  isHotAvg ? 'text-rose-100' : ''
                }`} data-testid={`${testId || 'mlb-script'}-series-avg`}>
                  {Number(activeSeriesContext.total_runs_avg).toFixed(1)} carreras
                </span>
              </span>
              <span className="opacity-70">· Partidos válidos: <span className="font-semibold tabular-nums">{nValid}</span></span>
            </div>
            {!isRunLine && (
              <div className="text-[10.5px] flex items-center gap-2 flex-wrap opacity-95" data-testid={`${testId || 'mlb-script'}-series-line-counts`}>
                <span>
                  Over {refLine}: <span className="font-semibold tabular-nums">{overCount} de {nValid}</span>
                </span>
                <span className="opacity-60">·</span>
                <span>
                  Under {refLine}: <span className="font-semibold tabular-nums">{underCount} de {nValid}</span>
                </span>
              </div>
            )}
            {limitedSample && (
              <div className="text-[10.5px] italic opacity-90" data-testid={`${testId || 'mlb-script'}-series-limited-sample`}>
                Muestra limitada — señal contextual, no concluyente.
              </div>
            )}
            {seriesDegradation && (
              <div className="text-[10.5px] opacity-95">
                ⚠ {seriesDegradation.game_in_series === 3 ? 'Tercer juego' : seriesDegradation.game_in_series === 2 ? 'Segundo juego' : `Juego ${seriesDegradation.game_in_series}`}: ER ajustado
                <span className="font-semibold tabular-nums ml-1">
                  {seriesDegradation.original_er} → {seriesDegradation.adjusted_er}
                </span>
                <span className="ml-1">(+{seriesDegradation.adjustment})</span>
              </div>
            )}
            {activeSeriesContext.override_reason && (
              <div className="text-[10.5px] italic opacity-90">{activeSeriesContext.override_reason}</div>
            )}

            {/* D9.3-B — Series Context Score breakdown (observe_only).
                Renders only when the backend attached a CONFIRMED signal
                with available=true. Shows the 5 component contributions
                and the final total + confidence modifier. */}
            {seriesTotalSignal && seriesTotalSignal.available && (() => {
              const sig = seriesTotalSignal;
              const score = Number(sig.series_context_score ?? 0);
              const confMod = Number(sig.confidence_modifier ?? 0);
              const adjER = sig.adjusted_expected_runs;
              const edge = sig.series_edge_runs;
              const bd = sig.score_breakdown || {};
              const isPositive = score > 0.5;
              const isNegative = score < -0.5;
              const verdict = isPositive ? 'Apoya Over' : isNegative ? 'Apoya Under' : 'Neutral';
              const verdictTone = isPositive
                ? 'bg-rose-500/15 text-rose-100 border-rose-500/35'
                : isNegative
                  ? 'bg-sky-500/15 text-sky-100 border-sky-500/35'
                  : 'bg-slate-500/15 text-slate-200 border-slate-500/30';
              const fmt = (v) => {
                const n = Number(v);
                if (!Number.isFinite(n)) return '0.0';
                const sign = n > 0 ? '+' : '';
                return `${sign}${n.toFixed(1)}`;
              };
              const variabilityBand = (sig.variability && sig.variability.band) || 'UNKNOWN';
              const variabilityLabel = {
                STABLE:   'Estable',
                MEDIUM:   'Media',
                VOLATILE: 'Volátil',
                UNKNOWN:  'Sin datos',
              }[variabilityBand];
              return (
                <div
                  className="mt-1.5 pt-1.5 border-t border-current/15 space-y-1"
                  data-testid={`${testId || 'mlb-script'}-series-signal`}
                >
                  <div className="text-[10.5px] flex items-center gap-1.5 flex-wrap">
                    <span className="font-semibold uppercase tracking-wide opacity-80">Score serie</span>
                    <span
                      className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold tabular-nums border ${verdictTone}`}
                      data-testid={`${testId || 'mlb-script'}-series-score`}
                    >
                      {fmt(score)} · {verdict}
                    </span>
                    {Math.abs(confMod) >= 0.5 && (
                      <span className="opacity-80">
                        Conf: <span className="font-semibold tabular-nums">{fmt(confMod)} pts</span>
                      </span>
                    )}
                  </div>
                  {adjER != null && (
                    <div className="text-[10.5px] opacity-90">
                      ER ajustado por serie:{' '}
                      <span className="font-semibold tabular-nums" data-testid={`${testId || 'mlb-script'}-series-adjusted-er`}>
                        {Number(adjER).toFixed(2)}
                      </span>
                      {edge != null && (
                        <span className="opacity-80">
                          {' '}· vs línea:{' '}
                          <span className={`font-semibold tabular-nums ${edge > 0 ? 'text-rose-100' : edge < 0 ? 'text-sky-100' : ''}`}>
                            {fmt(edge)}
                          </span>
                        </span>
                      )}
                    </div>
                  )}
                  <ul
                    className="text-[10.5px] font-mono-tabular space-y-0.5 pl-1"
                    data-testid={`${testId || 'mlb-script'}-series-score-breakdown`}
                  >
                    <li className="flex items-baseline gap-1">
                      <span className="opacity-80">Promedio ajustado de serie:</span>
                      <span className="ml-auto font-semibold">{fmt(bd.edge_runs)}</span>
                    </li>
                    <li className="flex items-baseline gap-1">
                      <span className="opacity-80">Tendencia de carreras:</span>
                      <span className="ml-auto font-semibold">{fmt(bd.slope)}</span>
                    </li>
                    <li className="flex items-baseline gap-1">
                      <span className="opacity-80">Bullpen fatigado:</span>
                      <span className="ml-auto font-semibold">{fmt(bd.bullpen_fatigue)}</span>
                    </li>
                    <li className="flex items-baseline gap-1">
                      <span className="opacity-80">Abridores de hoy:</span>
                      <span className="ml-auto font-semibold">{fmt(bd.pitching_matchup)}</span>
                    </li>
                    <li className="flex items-baseline gap-1">
                      <span className="opacity-80">Variabilidad ({variabilityLabel}):</span>
                      <span className="ml-auto font-semibold">{fmt(bd.variance)}</span>
                    </li>
                  </ul>
                </div>
              );
            })()}
          </div>
        );
      })() : null}

      {/* GAP #1 — IL impact ribbon. Refactor 2026-06-02:
            * Solo renderiza cuando hay >0 APLICADOS (no detected raw).
              Esto previene el bug histórico HOME 4 · AWAY 4 que aparecía
              en todos los partidos por culpa del cap saturado contando
              jugadores de 60-day IL y minor-league.
            * Muestra "applied de detected" cuando el cap se aplicó.
            * Texto direccional según impact_direction:
                negative_for_pick → "reduce ofensiva, castiga Over/RL/TT"
                positive_for_pick → "favorece lectura Under"
                neutral           → "ER ajustado; sin penalización"
      */}
      {ilPenalty && ((ilPenalty.home_key_il_count_applied ?? ilPenalty.home_key_il_count ?? 0) + (ilPenalty.away_key_il_count_applied ?? ilPenalty.away_key_il_count ?? 0)) > 0 && (
        <div
          className={`rounded-md border px-2.5 py-1.5 text-[11px] leading-snug space-y-0.5 ${
            ilPenalty.il_impact_label === 'ALTO'
              ? 'border-rose-500/40 bg-rose-500/10 text-rose-100'
              : 'border-amber-500/30 bg-amber-500/5 text-amber-100'
          }`}
          data-testid={`${testId || 'mlb-script'}-il-penalty`}
        >
          <div className="flex items-center gap-1.5 font-semibold">
            <span aria-hidden>🩹</span>
            <span>Bateadores clave en IL</span>
            <span className={`ml-auto inline-block px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
              ilPenalty.il_impact_label === 'ALTO'
                ? 'bg-rose-500/20 text-rose-100 border border-rose-500/40'
                : 'bg-amber-500/15 text-amber-100 border border-amber-500/30'
            }`}>
              Impacto {ilPenalty.il_impact_label}
            </span>
          </div>
          {(() => {
            // Compute applied vs raw with safe fallbacks for legacy payloads.
            const homeApplied = ilPenalty.home_key_il_count_applied ?? ilPenalty.home_key_il_count ?? 0;
            const awayApplied = ilPenalty.away_key_il_count_applied ?? ilPenalty.away_key_il_count ?? 0;
            const homeRaw     = ilPenalty.home_key_il_count_raw ?? homeApplied;
            const awayRaw     = ilPenalty.away_key_il_count_raw ?? awayApplied;
            const capApplied  = Boolean(ilPenalty.cap_applied) || homeRaw > homeApplied || awayRaw > awayApplied;
            const homeStr     = capApplied && homeRaw > homeApplied
              ? `${homeApplied} aplicados de ${homeRaw} detectados`
              : String(homeApplied);
            const awayStr     = capApplied && awayRaw > awayApplied
              ? `${awayApplied} aplicados de ${awayRaw} detectados`
              : String(awayApplied);
            const confApplied = ilPenalty.confidence_penalty_applied ?? ilPenalty.confidence_penalty ?? 0;
            return (
              <div className="text-[10.5px] tabular-nums" data-testid={`${testId || 'mlb-script'}-il-counts`}>
                HOME {homeStr} · AWAY {awayStr} · ER ajuste{' '}
                <span className="font-semibold">{Number(ilPenalty.er_adjustment || 0).toFixed(2)}</span>
                {confApplied > 0 && (
                  <span className="ml-1">· Conf −{confApplied}</span>
                )}
              </div>
            );
          })()}
          {ilPenalty.impact_narrative ? (
            <div
              className={`text-[10.5px] italic opacity-95 ${
                ilPenalty.impact_direction === 'positive_for_pick'
                  ? 'text-emerald-200'
                  : ilPenalty.impact_direction === 'negative_for_pick'
                    ? 'text-rose-200'
                    : 'text-amber-200/90'
              }`}
              data-testid={`${testId || 'mlb-script'}-il-narrative`}
            >
              {ilPenalty.impact_narrative}
            </div>
          ) : null}
          {(ilPenalty.home_missing_key?.length || ilPenalty.away_missing_key?.length) ? (
            <div className="text-[10px] opacity-90 truncate">
              {ilPenalty.home_missing_key?.length > 0 && (
                <span>HOME: {ilPenalty.home_missing_key.slice(0, 3).join(', ')}</span>
              )}
              {ilPenalty.home_missing_key?.length > 0 && ilPenalty.away_missing_key?.length > 0 && ' · '}
              {ilPenalty.away_missing_key?.length > 0 && (
                <span>AWAY: {ilPenalty.away_missing_key.slice(0, 3).join(', ')}</span>
              )}
            </div>
          ) : null}
        </div>
      )}

      {/* Active Series block — pick rechazado por contradicción de serie */}
      {activeSeriesBlock && (
        <div
          className="rounded-md border border-rose-500/50 bg-rose-500/15 px-2.5 py-1.5 text-[11px] leading-snug text-rose-100 space-y-0.5"
          data-testid={`${testId || 'mlb-script'}-series-block`}
        >
          <div className="font-semibold">⛔ Pick bloqueado por serie activa</div>
          <div className="text-[10.5px]">
            Mercado: <span className="font-medium">{activeSeriesBlock.blocked_market}</span>
            {activeSeriesBlock.series_avg_runs != null && (
              <span className="ml-2">· Serie {activeSeriesBlock.series_avg_runs} carreras / juego</span>
            )}
          </div>
          {activeSeriesBlock.override_reason && (
            <div className="text-[10.5px] opacity-90">{activeSeriesBlock.override_reason}</div>
          )}
        </div>
      )}

      {/* Model Verification ribbon — surfaces UNDERESTIMATE / OVERESTIMATE flags */}
      {modelVerification?.model_vs_reality?.flag && modelVerification.model_vs_reality.flag !== 'OK' && (
        <div
          className={`rounded-md border px-2.5 py-1 text-[10.5px] leading-snug ${
            modelVerification.model_vs_reality.flag === 'UNDERESTIMATE'
              ? 'border-rose-500/30 bg-rose-500/5 text-rose-200'
              : 'border-cyan-500/30 bg-cyan-500/5 text-cyan-200'
          }`}
          data-testid={`${testId || 'mlb-script'}-verification`}
        >
          {modelVerification.model_vs_reality.flag === 'UNDERESTIMATE' ? '↗ Modelo subestima' : '↘ Modelo sobreestima'}
          {modelVerification.model_vs_reality.delta != null && (
            <span className="ml-1 tabular-nums font-semibold">
              Δ {modelVerification.model_vs_reality.delta > 0 ? '+' : ''}
              {modelVerification.model_vs_reality.delta} carreras
            </span>
          )}
          {modelVerification.confidence_penalty > 0 && (
            <span className="ml-2 opacity-90">· -{modelVerification.confidence_penalty} pts conf.</span>
          )}
        </div>
      )}

      {expanded && (
        <div className="pt-1 space-y-3" data-testid={`${testId || 'mlb-script-panel'}-body`}>
          {/* Pick type rationale */}
          {pickTypeReason ? (
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              {pickTypeReason}
            </p>
          ) : null}

          {/* Margin & Cover metrics */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 border-t border-border/40 pt-2">
            {marginVsLineNum !== null ? (
              <MetricRow
                icon={Activity}
                label={`Margen vs línea (${marginVsLineSide === 'under' ? 'Under' : 'Over'})`}
                value={`${marginVsLineNum >= 0 ? '+' : ''}${marginVsLineNum.toFixed(2)}`}
                hint="carreras a favor del pick"
                tone={marginVsLineNum >= 1.8 ? 'emerald' : (marginVsLineNum >= 1.0 ? 'amber' : 'rose')}
                testId={`${testId || 'mlb-script'}-metric-margin-vs-line`}
              />
            ) : null}
            {marginNum !== null ? (
              <MetricRow
                icon={Activity}
                label={marginVsLineNum !== null ? "Margen run-line (equipos)" : "Projected margin"}
                value={`${marginNum >= 0 ? '+' : ''}${marginNum.toFixed(2)}`}
                hint="carreras"
                tone={
                  marginVsLineNum !== null
                    ? 'slate'
                    : (marginNum >= 1.8 ? 'emerald' : (marginNum >= 1.0 ? 'amber' : 'slate'))
                }
                testId={`${testId || 'mlb-script'}-metric-margin`}
              />
            ) : null}
            {coverNum !== null ? (
              <MetricRow
                icon={Target}
                label="Cover probability"
                value={`${coverNum.toFixed(1)}%`}
                tone={coverNum >= 55 ? 'emerald' : (coverNum >= 45 ? 'amber' : 'slate')}
                testId={`${testId || 'mlb-script'}-metric-cover`}
              />
            ) : null}
            {rlsNum !== null ? (
              <MetricRow
                icon={Gauge}
                label="Run Line score"
                value={`${rlsNum.toFixed(0)}/100`}
                tone={rlsNum >= 72 ? 'emerald' : (rlsNum >= 60 ? 'amber' : 'slate')}
                testId={`${testId || 'mlb-script'}-metric-rl-score`}
              />
            ) : null}
            {fragNum !== null ? (
              <MetricRow
                icon={Shield}
                label="Fragility"
                value={`${fragNum.toFixed(0)}/100`}
                hint={fragilitySource === 'mlb_script_survival' && legacyFragNum !== null
                  ? `unificado · legacy v2 ${legacyFragNum.toFixed(0)}`
                  : undefined}
                tone={fragNum <= 35 ? 'emerald' : (fragNum <= 55 ? 'amber' : 'rose')}
                testId={`${testId || 'mlb-script'}-metric-fragility`}
              />
            ) : null}
            {scriptV5 && scriptV5.survival?.score != null ? (
              <MetricRow
                icon={Shield}
                label="Script Survival"
                value={`${Number(scriptV5.survival.score).toFixed(0)}/100`}
                hint={scriptV5.stability?.label_es}
                tone={
                  Number(scriptV5.survival.score) >= 85 ? 'emerald' :
                  Number(scriptV5.survival.score) >= 70 ? 'sky' :
                  Number(scriptV5.survival.score) >= 50 ? 'amber' : 'rose'
                }
                testId={`${testId || 'mlb-script'}-metric-survival`}
              />
            ) : null}
            {scriptV5 && scriptV5.fragility?.score != null && fragNum === null ? (
              <MetricRow
                icon={Shield}
                label="Fragility (v5)"
                value={`${Number(scriptV5.fragility.score).toFixed(0)}/100`}
                tone={Number(scriptV5.fragility.score) <= 25 ? 'emerald' : (Number(scriptV5.fragility.score) <= 50 ? 'amber' : 'rose')}
                testId={`${testId || 'mlb-script'}-metric-fragility-v5`}
              />
            ) : null}
          </div>

          {/* Totals block — best line / safe / aggressive */}
          {(erNum !== null || recommendedLine) && (
            <div className="border-t border-border/40 pt-2 space-y-1.5">
              <div className="micro-label">TOTALES</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                {erNum !== null ? (
                  <MetricRow
                    label="Expected runs"
                    value={erNum.toFixed(1)}
                    testId={`${testId || 'mlb-script'}-metric-exp-runs`}
                  />
                ) : null}
                {recommendedLine ? (
                  <MetricRow
                    label="Línea recomendada"
                    value={recommendedLine}
                    tone="emerald"
                    testId={`${testId || 'mlb-script'}-metric-recommended`}
                  />
                ) : null}
                {safeLine ? (
                  <MetricRow
                    label="Línea protegida"
                    value={safeLine}
                    tone="sky"
                    testId={`${testId || 'mlb-script'}-metric-safe-line`}
                  />
                ) : null}
                {aggressiveLine ? (
                  <MetricRow
                    label="Línea agresiva"
                    value={aggressiveLine}
                    tone="amber"
                    testId={`${testId || 'mlb-script'}-metric-aggressive`}
                  />
                ) : null}
                {bestLine && bestLine !== recommendedLine ? (
                  <MetricRow
                    label="Best line"
                    value={bestLine}
                    testId={`${testId || 'mlb-script'}-metric-best`}
                  />
                ) : null}
                {lineSafeNum !== null ? (
                  <MetricRow
                    label="Line safety score"
                    value={`${lineSafeNum.toFixed(0)}/100`}
                    tone={lineSafeNum >= 65 ? 'emerald' : (lineSafeNum >= 45 ? 'amber' : 'slate')}
                    testId={`${testId || 'mlb-script'}-metric-line-safety`}
                  />
                ) : null}
                {/* BUGFIX — Edge vs line + Poisson under/over probabilities */}
                {edgeNum !== null ? (
                  <MetricRow
                    label="Edge vs línea"
                    value={`${edgeNum >= 0 ? '+' : ''}${edgeNum.toFixed(2)} carr.`}
                    tone={Math.abs(edgeNum) >= 1.5 ? 'emerald' : (Math.abs(edgeNum) >= 0.7 ? 'amber' : 'slate')}
                    testId={`${testId || 'mlb-script'}-metric-edge-vs-line`}
                  />
                ) : null}
                {(probUnderNum !== null && probOverNum !== null) ? (
                  <MetricRow
                    label="P(U/O)"
                    value={`${probUnderNum.toFixed(0)}% · ${probOverNum.toFixed(0)}%`}
                    hint={probabilityModel || ''}
                    testId={`${testId || 'mlb-script'}-metric-under-over-probs`}
                  />
                ) : null}
              </div>
            </div>
          )}

          {/* Same-game correlation reason */}
          {sgc && sameGameCorrelationReason ? (
            <div className="border-t border-border/40 pt-2">
              <div className="micro-label mb-1">CORRELACIÓN MISMO JUEGO</div>
              <p className={`text-[11px] leading-relaxed tone-${sgc.tone}`}>
                {sameGameCorrelationReason}
              </p>
            </div>
          ) : null}

          {/* Reasons + Risks */}
          {(reasons.length || risks.length) ? (
            <div className="border-t border-border/40 pt-2 grid grid-cols-1 sm:grid-cols-2 gap-3">
              {reasons.length ? (
                <div data-testid={`${testId || 'mlb-script'}-reasons`}>
                  <div className="micro-label mb-1 tone-emerald">A FAVOR</div>
                  <ul className="space-y-1">
                    {reasons.slice(0, 5).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">
                        • {r}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {risks.length ? (
                <div data-testid={`${testId || 'mlb-script'}-risks`}>
                  <div className="micro-label mb-1 tone-rose">RIESGOS</div>
                  <ul className="space-y-1">
                    {risks.slice(0, 5).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug flex items-start gap-1">
                        <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0 text-rose-500/80" />
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}

          {/* Parlay context (optional — passed by parent if present) */}
          {parlay && (parlay.whyThisParlayWorks?.length || parlay.whyThisParlayCanFail?.length) ? (
            <div className="border-t border-border/40 pt-2 space-y-2" data-testid={`${testId || 'mlb-script'}-parlay`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="micro-label">PARLAY MLB</span>
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border tone-emerald">
                  {parlay.parlayType || 'MLB_ONLY'}
                </span>
                {typeof parlay.finalParlayScore === 'number' ? (
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    score {parlay.finalParlayScore}/100
                  </span>
                ) : null}
                {parlay.riskLevel ? (
                  <span className={`text-[10px] tone-${parlay.riskLevel === 'LOW' ? 'emerald' : parlay.riskLevel === 'MEDIUM' ? 'amber' : 'rose'}`}>
                    riesgo {parlay.riskLevel}
                  </span>
                ) : null}
              </div>
              {parlay.whyThisParlayWorks?.length ? (
                <div>
                  <div className="micro-label mb-1 tone-emerald">POR QUÉ FUNCIONA</div>
                  <ul className="space-y-1">
                    {parlay.whyThisParlayWorks.slice(0, 4).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">• {r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {parlay.whyThisParlayCanFail?.length ? (
                <div>
                  <div className="micro-label mb-1 tone-rose">POR QUÉ PUEDE FALLAR</div>
                  <ul className="space-y-1">
                    {parlay.whyThisParlayCanFail.slice(0, 4).map((r, i) => (
                      <li key={i} className="text-[11px] text-muted-foreground leading-snug">• {r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default MLBScriptPanel;
