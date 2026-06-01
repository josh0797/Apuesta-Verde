import {
  Flame,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Target,
  Users,
  TrendingUp,
  Wind,
  CircleAlert,
} from 'lucide-react';

/**
 * ExplosiveInningV2Panel — renders the v2 live explosive-inning evaluation
 * produced by `mlb_explosive_inning_engine` and surfaced through
 * `/api/mlb/live/reevaluate` → intelligence.explosive_v2.
 *
 * Mirrors the visual treatment of the football LiveCornerIntelligence
 * card: pressure score + state badge + recommended market + trap
 * warnings + top-3 market candidates. Each state has its own colour
 * theme (rose for danger, amber for medium, emerald for clean).
 *
 * Renders NOTHING when the v2 payload is absent so legacy MLB matches
 * (without the v2 hydration) keep their current layout.
 */

const STATE_META = {
  BULLPEN_EXPLOSION_RISK: {
    icon: Flame,
    label: 'Bullpen al borde',
    description: 'Abridor fuera + bullpen con fatiga y relevista pobre.',
    tone: 'rose',
  },
  COMMAND_COLLAPSE_RISK: {
    icon: Wind,
    label: 'Comando del pitcher colapsando',
    description: 'Pitcheos altos + bases por bola + cuentas perdidas.',
    tone: 'rose',
  },
  HARD_CONTACT_CLUSTER: {
    icon: Target,
    label: 'Cluster de batazos duros',
    description: 'Múltiples hard-hits + barrels + EV alta.',
    tone: 'rose',
  },
  BASE_TRAFFIC_PRESSURE: {
    icon: AlertTriangle,
    label: 'Presión en bases',
    description: 'Bases llenas o RISP con pocos outs.',
    tone: 'amber',
  },
  TWO_OUT_RALLY_RISK: {
    icon: CircleAlert,
    label: 'Rally con 2 outs',
    description: 'Múltiples baserunners con 2 outs.',
    tone: 'amber',
  },
  LINEUP_TURNOVER_DANGER: {
    icon: Users,
    label: 'Top de la alineación al bate',
    description: 'Top order + 3ª vuelta + matchup desfavorable.',
    tone: 'amber',
  },
  CLEAN_INNING_LOW_RISK: {
    icon: ShieldCheck,
    label: 'Inning limpio',
    description: 'Sin señales explosivas relevantes.',
    tone: 'emerald',
  },
};

const TIER_META = {
  HIGH:   { label: 'HIGH',   chip: 'border-rose-500/45 bg-rose-500/15 text-rose-200' },
  MEDIUM: { label: 'MEDIUM', chip: 'border-amber-500/45 bg-amber-500/15 text-amber-200' },
  LOW:    { label: 'LOW',    chip: 'border-emerald-500/45 bg-emerald-500/15 text-emerald-200' },
};

const RISK_META = {
  LOW:    { label: 'Riesgo bajo de equivocarse',     icon: ShieldCheck },
  MEDIUM: { label: 'Riesgo medio',                   icon: ShieldAlert },
  HIGH:   { label: 'Riesgo alto — esperar señal',    icon: AlertTriangle },
};

const TONE_WRAPPER = {
  rose:    'border-rose-500/35 bg-rose-500/[0.06]',
  amber:   'border-amber-500/35 bg-amber-500/[0.06]',
  emerald: 'border-emerald-500/35 bg-emerald-500/[0.06]',
};

const TONE_ICON = {
  rose:    'text-rose-300',
  amber:   'text-amber-300',
  emerald: 'text-emerald-300',
};

const TONE_HEADING = {
  rose:    'text-rose-200',
  amber:   'text-amber-200',
  emerald: 'text-emerald-200',
};

const TRAP_LABELS_ES = {
  LINE_ALREADY_MOVED:         'La línea ya se movió contra el Over.',
  LINE_OVERREACTED:           'La línea sobre-reaccionó — Over ya no tiene valor.',
  RISP_TWO_OUTS_BOTTOM_ORDER: 'RISP con 2 outs y bottom de la alineación — falso positivo probable.',
  ELITE_RELIEVER_DAMPENS_COLLAPSE:
    'Pitch count alto pero el próximo relevista es élite — colapso amortiguado.',
  MODERATE_PRESSURE_WITH_INFLATED_LINE:
    'Presión moderada pero la línea ya está inflada — no perseguir.',
};

const REASON_LABELS_ES = {
  BASES_LOADED_LOW_OUTS:       'Bases llenas con pocos outs',
  RISP_LOW_OUTS:               'Corredor en posición anotadora con pocos outs',
  RISP_TWO_OUTS:               'RISP con 2 outs',
  ANY_RUNNER_LOW_OUTS:         'Corredor en base con pocos outs',
  MULTI_BASERUNNER_INNING:     'Múltiples corredores este inning',
  INNING_PITCH_OVERLOAD:       'Sobrecarga de pitcheos en el inning',
  INNING_PITCH_ELEVATED:       'Pitcheos del inning elevados',
  MULTI_WALK_INNING:           'Múltiples bases por bola en el inning',
  FALLING_BEHIND_HITTERS:      'Pitcher cae en cuentas',
  WILD_PITCH_OR_HBP:           'Wild pitch o HBP este inning',
  PITCH_COUNT_OVER_LIMIT:      'Pitch count sobre el límite',
  HARD_CONTACT_CLUSTER:        'Cluster de batazos duros',
  BARREL_PRESENT:              'Barrel(s) en el inning',
  HIGH_EXIT_VELOCITY:          'Velocidad de salida alta',
  LINE_DRIVE_CLUSTER:          'Cluster de line drives',
  TOP_OF_ORDER_DUE:            'Top de la alineación al bate',
  THIRD_TIME_THROUGH_ORDER:    '3ª vuelta a la alineación',
  PLATOON_UNFAVORABLE:         'Matchup de mano desfavorable',
  STARTER_REMOVED_EARLY:       'Abridor salió temprano',
  BULLPEN_FATIGUE_HIGH:        'Bullpen con fatiga alta',
  LOW_QUALITY_NEXT_RELIEVER:   'Próximo relevista de baja calidad',
  RELIEVER_BACK_TO_BACK:       'Relevista en días consecutivos',
  RELIEVER_IN_FATIGUE_BAND:    'Relevista en banda de fatiga',
  TWO_OUT_RALLY_BUILDING:      'Rally con 2 outs en construcción',
};

function _num(v, fallback = null) {
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

export function ExplosiveInningV2Panel({ payload, idx = 0 }) {
  if (!payload || typeof payload !== 'object') return null;
  if (payload.version !== 2) return null;

  const pressure = _num(payload.explosive_inning_pressure_score, 0);
  const state = payload.state || 'CLEAN_INNING_LOW_RISK';
  const stateMeta = STATE_META[state] || STATE_META.CLEAN_INNING_LOW_RISK;
  const tone = stateMeta.tone;
  const Icon = stateMeta.icon;
  const tier = (payload.risk_tier || 'LOW').toUpperCase();
  const tierMeta = TIER_META[tier] || TIER_META.LOW;
  const risk = (payload.risk || 'MEDIUM').toUpperCase();
  const riskMeta = RISK_META[risk] || RISK_META.MEDIUM;
  const RiskIcon = riskMeta.icon;
  const confidence = _num(payload.confidence, 0);
  const traps = Array.isArray(payload.trap_signals) ? payload.trap_signals : [];
  const reasonCodes = Array.isArray(payload.reason_codes) ? payload.reason_codes : [];
  const candidates = Array.isArray(payload.market_candidates) ? payload.market_candidates.slice(0, 3) : [];
  const flipTriggered = Boolean(payload.flip_triggered);
  const shouldRecommend = Boolean(payload.should_recommend);
  const recommendedMarket = payload.recommended_market;
  const recommendedOdds = _num(payload.recommended_odds);
  const dataWarning = payload.data_warning;
  const inputCompleteness = _num(payload.input_completeness);
  const narrative = payload.narrative_es;

  return (
    <section
      className={`rounded-lg border ${TONE_WRAPPER[tone]} px-3 py-3 space-y-3`}
      data-testid={`mlb-explosive-v2-panel-${idx}`}
    >
      {/* Header — state label + pressure score + risk tier */}
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${TONE_ICON[tone]}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/80">
                Inning explosivo · v2
              </span>
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border ${tierMeta.chip} font-semibold`}
                data-testid={`mlb-explosive-v2-tier-${idx}`}
              >
                {tierMeta.label}
              </span>
              {flipTriggered ? (
                <span
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] border border-cyan-500/45 bg-cyan-500/15 text-cyan-200 font-semibold"
                  data-testid={`mlb-explosive-v2-flip-${idx}`}
                >
                  <TrendingUp className="h-2.5 w-2.5" />
                  Flip Under → Over
                </span>
              ) : null}
            </div>
            <div className={`text-[12.5px] mt-0.5 font-semibold ${TONE_HEADING[tone]}`}>
              {stateMeta.label}
            </div>
            <div className="text-[10.5px] text-muted-foreground/85 mt-0.5">
              {stateMeta.description}
            </div>
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80">
            Pressure
          </div>
          <div className={`text-lg font-bold tabular-nums ${TONE_HEADING[tone]}`} data-testid={`mlb-explosive-v2-score-${idx}`}>
            {Math.round(pressure)}
            <span className="text-[10px] text-muted-foreground/70 font-normal">/100</span>
          </div>
        </div>
      </header>

      {/* Narrative */}
      {narrative ? (
        <p className="text-[11.5px] text-foreground/80 leading-relaxed border-l-2 border-border/50 pl-2">
          {narrative}
        </p>
      ) : null}

      {/* Recommendation block */}
      {shouldRecommend && recommendedMarket ? (
        <div
          className="rounded-md border border-cyan-500/35 bg-cyan-500/10 px-2.5 py-2"
          data-testid={`mlb-explosive-v2-recommendation-${idx}`}
        >
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 min-w-0">
              <Target className="h-3.5 w-3.5 text-cyan-300 shrink-0" />
              <span className="text-[10px] uppercase tracking-wide text-cyan-200/90">
                Recomendación
              </span>
              <span className="text-[12.5px] font-semibold text-cyan-100">
                {recommendedMarket}
              </span>
            </div>
            {recommendedOdds !== null ? (
              <span className="text-[11px] tabular-nums text-cyan-200/90 font-mono">
                @ {recommendedOdds.toFixed(2)}
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex items-center gap-2 flex-wrap text-[10.5px] text-cyan-100/85">
            <RiskIcon className="h-3 w-3" />
            <span>{riskMeta.label}</span>
            <span className="opacity-60">·</span>
            <span className="tabular-nums">Confianza {confidence}/100</span>
          </div>
        </div>
      ) : null}

      {/* Trap signals — gating warnings */}
      {traps.length > 0 ? (
        <div data-testid={`mlb-explosive-v2-traps-${idx}`}>
          <div className="text-[10px] uppercase tracking-wide text-amber-200/90 mb-1 flex items-center gap-1.5">
            <AlertTriangle className="h-3 w-3" />
            Trap signals detectados
          </div>
          <ul className="space-y-0.5">
            {traps.map((t) => (
              <li
                key={t}
                className="text-[11px] text-amber-100/95 flex items-start gap-1.5"
                data-testid={`mlb-explosive-v2-trap-${idx}-${t}`}
              >
                <span className="opacity-60 mt-[1px]">·</span>
                <span>{TRAP_LABELS_ES[t] || t}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Market candidates — top 3 */}
      {candidates.length > 0 ? (
        <div data-testid={`mlb-explosive-v2-candidates-${idx}`}>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80 mb-1">
            Mercados sugeridos por el motor
          </div>
          <ul className="space-y-1">
            {candidates.map((c, i) => (
              <li
                key={i}
                className={`flex items-start justify-between gap-2 text-[11px] rounded-md border px-2 py-1.5 ${
                  c.category === 'WATCHLIST'
                    ? 'border-amber-500/30 bg-amber-500/[0.04] text-amber-100/90'
                    : 'border-border/50 bg-background/40 text-foreground/85'
                }`}
                data-testid={`mlb-explosive-v2-candidate-${idx}-${i}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{c.market}</div>
                  {c.rationale ? (
                    <div className="text-[10px] text-muted-foreground/75 mt-0.5 leading-snug">
                      {c.rationale}
                    </div>
                  ) : null}
                </div>
                {c.score !== undefined && c.score !== null ? (
                  <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/85 font-mono">
                    {Math.round(c.score)}/100
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Reason codes summary (collapsed) */}
      {reasonCodes.length > 0 ? (
        <details className="text-[10.5px] text-muted-foreground/80">
          <summary
            className="cursor-pointer hover:text-muted-foreground"
            data-testid={`mlb-explosive-v2-reasons-toggle-${idx}`}
          >
            Ver señales detectadas ({reasonCodes.length})
          </summary>
          <ul className="mt-1.5 space-y-0.5 pl-1">
            {reasonCodes.map((r) => (
              <li key={r} className="flex items-start gap-1.5 text-[11px]">
                <span className="opacity-60 mt-[1px]">·</span>
                <span>{REASON_LABELS_ES[r] || r}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {/* Data warning / completeness */}
      {dataWarning ? (
        <div
          className="rounded border border-border/40 bg-background/40 px-2 py-1.5 text-[10.5px] text-muted-foreground/85 leading-relaxed flex items-start gap-1.5"
          data-testid={`mlb-explosive-v2-warning-${idx}`}
        >
          <CircleAlert className="h-3 w-3 shrink-0 mt-0.5" />
          <span>
            {dataWarning}
            {inputCompleteness !== null ? (
              <span className="ml-1 font-mono opacity-75">
                ({Math.round(inputCompleteness * 100)}% datos disponibles)
              </span>
            ) : null}
          </span>
        </div>
      ) : null}
    </section>
  );
}

export default ExplosiveInningV2Panel;
