import { useState } from 'react';
import {
  ChevronDown,
  Target,
  User,
  Gauge,
  Sparkles,
  TrendingDown,
  TrendingUp,
  CheckCircle2,
  CircleDot,
  AlertTriangle,
  Layers,
  ShieldCheck,
  Shield,
  Activity,
  Flame,
  Zap,
  ArrowRightLeft,
} from 'lucide-react';

/**
 * MLBScriptV3Panel — MLB Engine V3 Explainability layer.
 *
 * Renders the `_mlb_script_v3` payload produced by the orchestrator:
 *
 *   - script         { script_code, label_es, narrative_es, key_drivers,
 *                       expected_runs, projected_margin, variance }
 *   - pitchers_block { home: {name, qualityScore, primary_stats[]},
 *                       away: {...}, bothConfirmed, edgeSide, qualityDiff }
 *   - why_this_pick  [ { key, label, value, tone } , ... ]
 *   - confidence_breakdown { total, components: [{label, value, weight}] }
 *   - baseball_reasons [ str, ... ]
 *
 * Strictly baseball-only — caller (MatchCard) must skip rendering for
 * non-baseball sports.
 *
 * Compact by default + expandable for the full breakdown.
 */

const SCRIPT_TONE = {
  LOW_SCORING_PITCHERS_DUEL: 'sky',
  OFFENSIVE_SHOOTOUT:        'rose',
  FAVORITE_DOMINANCE:        'emerald',
  BULLPEN_BATTLE:            'amber',
  UNDERDOG_CAN_COMPETE:      'violet',
  PITCHER_MISMATCH:          'emerald',
  HIGH_VARIANCE_GAME:        'orange',
  LOW_VARIANCE_GAME:         'slate',
};

const SCRIPT_ICON = {
  LOW_SCORING_PITCHERS_DUEL: TrendingDown,
  OFFENSIVE_SHOOTOUT:        TrendingUp,
  FAVORITE_DOMINANCE:        Sparkles,
  BULLPEN_BATTLE:            AlertTriangle,
  UNDERDOG_CAN_COMPETE:      CircleDot,
  PITCHER_MISMATCH:          Target,
  HIGH_VARIANCE_GAME:        AlertTriangle,
  LOW_VARIANCE_GAME:         CircleDot,
};

const TONE_CLASSES = {
  emerald: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/25',
  amber:   'text-amber-300 bg-amber-500/10 border-amber-500/25',
  sky:     'text-sky-300 bg-sky-500/10 border-sky-500/25',
  violet:  'text-violet-300 bg-violet-500/10 border-violet-500/25',
  rose:    'text-rose-300 bg-rose-500/10 border-rose-500/25',
  orange:  'text-orange-300 bg-orange-500/10 border-orange-500/25',
  slate:   'text-slate-300 bg-slate-500/10 border-slate-500/25',
  cyan:    'text-cyan-300 bg-cyan-500/10 border-cyan-500/25',
};

const TONE_BAR = {
  emerald: 'bg-emerald-500/70',
  amber:   'bg-amber-500/70',
  sky:     'bg-sky-500/70',
  violet:  'bg-violet-500/70',
  rose:    'bg-rose-500/70',
  slate:   'bg-slate-500/70',
  cyan:    'bg-cyan-500/70',
};

function PitcherCard({ side, data, testIdPrefix }) {
  if (!data || !data.name) return null;
  const tone = data.qualityScore >= 70 ? 'emerald' : data.qualityScore >= 55 ? 'sky' : data.qualityScore >= 40 ? 'amber' : 'rose';
  return (
    <div
      className="flex flex-col gap-1.5 rounded-lg border border-border/50 bg-white/[0.02] p-2.5"
      data-testid={`${testIdPrefix}-pitcher-${side}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <User className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {side === 'home' ? 'Local' : 'Visitante'}
          </span>
        </div>
        <span
          className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${TONE_CLASSES[tone]}`}
          data-testid={`${testIdPrefix}-pitcher-${side}-quality`}
        >
          {Math.round(data.qualityScore || 0)}/100
        </span>
      </div>
      <div className="text-sm font-medium leading-tight truncate" title={data.name}>
        {data.name}
      </div>
      {data.team ? (
        <div className="text-[10px] text-muted-foreground truncate">{data.team}</div>
      ) : null}
      {Array.isArray(data.primary_stats) && data.primary_stats.length > 0 ? (
        <div className="flex flex-wrap gap-1 pt-1">
          {data.primary_stats.map((s) => (
            <span
              key={s.key}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] bg-white/[0.04] border border-border/40 text-foreground/85 tabular-nums"
            >
              <span className="text-muted-foreground">{s.label}</span>
              <span className="font-medium">{s.value}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function WhyRow({ row, testIdPrefix }) {
  const toneCls =
    row.tone === 'positive' ? 'text-emerald-300' :
    row.tone === 'negative' ? 'text-rose-300' : 'text-foreground/85';
  return (
    <div
      className="flex items-center justify-between gap-3 py-1 border-b border-border/30 last:border-b-0"
      data-testid={`${testIdPrefix}-why-${row.key}`}
    >
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground min-w-0">
        <CheckCircle2 className={`h-3 w-3 ${toneCls} shrink-0`} />
        <span className="truncate">{row.label}</span>
      </div>
      <span className={`text-[11px] font-medium tabular-nums ${toneCls} text-right`}>
        {row.value}
      </span>
    </div>
  );
}

function ConfidenceBreakdownBar({ breakdown, testIdPrefix }) {
  if (!breakdown || !Array.isArray(breakdown.components)) return null;
  const total = Number(breakdown.total) || 0;
  // Compute denominator from components sum so the bars are proportional.
  const sum = breakdown.components.reduce((acc, c) => acc + (Number(c.value) || 0), 0) || 1;
  return (
    <div className="space-y-2" data-testid={`${testIdPrefix}-conf-breakdown`}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
          Desglose de confianza
        </span>
        <span className="text-[11px] font-mono tabular-nums text-foreground/90">
          {total.toFixed(0)}/100
        </span>
      </div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-white/[0.04] border border-border/40">
        {breakdown.components.map((c, idx) => {
          const pct = ((Number(c.value) || 0) / sum) * 100;
          const tones = ['emerald', 'sky', 'amber', 'violet', 'cyan'];
          const tone = tones[idx % tones.length];
          return (
            <div
              key={c.key}
              className={`${TONE_BAR[tone]} transition-all`}
              style={{ width: `${pct}%` }}
              title={`${c.label}: +${(Number(c.value) || 0).toFixed(1)}`}
            />
          );
        })}
      </div>
      <div className="grid grid-cols-2 gap-1 pt-0.5">
        {breakdown.components.map((c, idx) => {
          const tones = ['emerald', 'sky', 'amber', 'violet', 'cyan'];
          const tone = tones[idx % tones.length];
          return (
            <div
              key={c.key}
              className="flex items-center justify-between gap-2 text-[10px]"
              data-testid={`${testIdPrefix}-conf-${c.key}`}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={`h-1.5 w-1.5 rounded-full ${TONE_BAR[tone]}`} />
                <span className="text-muted-foreground truncate">{c.label}</span>
              </div>
              <span className="font-mono tabular-nums text-foreground/85">
                {(Number(c.value) || 0) >= 0 ? '+' : ''}{(Number(c.value) || 0).toFixed(1)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * MLBScriptSurvivalSummary — F5 — compact one-line summary of Script
 * Survival, Fragility, and Stability classification. Shown above the
 * baseball reasons in the V3 panel.
 */
const STABILITY_TONE_CLASSES = {
  emerald: 'bg-emerald-500/12 border-emerald-500/35 text-emerald-200',
  sky:     'bg-sky-500/12 border-sky-500/35 text-sky-200',
  amber:   'bg-amber-500/12 border-amber-500/35 text-amber-200',
  rose:    'bg-rose-500/12 border-rose-500/35 text-rose-200',
};

const STABILITY_LABEL_FALLBACK = {
  ELITE_STABLE:      'Elite estable',
  STABLE:            'Estable',
  MODERATELY_STABLE: 'Moderadamente estable',
  FRAGILE:           'Frágil',
  HIGHLY_FRAGILE:    'Altamente frágil',
};

export function MLBScriptSurvivalSummary({ scriptV5, testIdPrefix }) {
  if (!scriptV5 || typeof scriptV5 !== 'object') return null;
  const survival = Number(scriptV5?.survival?.score ?? 0);
  const fragility = Number(scriptV5?.fragility?.score ?? 0);
  const stab = scriptV5?.stability || {};
  const code = stab.code || 'MODERATELY_STABLE';
  const label = stab.label_es || STABILITY_LABEL_FALLBACK[code] || code;
  const tone = stab.tone || 'sky';
  const cls = STABILITY_TONE_CLASSES[tone] || STABILITY_TONE_CLASSES.sky;
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border ${cls}`}
      data-testid={`${testIdPrefix}-v5-summary`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
        <span className="text-[11px] uppercase tracking-wide opacity-90">Estabilidad</span>
        <span className="text-sm font-semibold truncate" data-testid={`${testIdPrefix}-v5-label`}>
          {label}
        </span>
        {scriptV5.reference_profile ? (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] bg-white/10 border border-current/30 uppercase tracking-wide">
            Benchmark
          </span>
        ) : null}
      </div>
      <div className="flex items-center gap-3 shrink-0 text-[11px] tabular-nums">
        <span data-testid={`${testIdPrefix}-v5-survival`}>
          <span className="opacity-70 mr-1">Survival</span>
          <span className="font-bold">{survival.toFixed(0)}/100</span>
        </span>
        <span data-testid={`${testIdPrefix}-v5-fragility`}>
          <span className="opacity-70 mr-1">Fragility</span>
          <span className="font-bold">{fragility.toFixed(0)}/100</span>
        </span>
      </div>
    </div>
  );
}


/**
 * MLBScriptSurvivalDetail — F5 — expandable detail card with the per-component
 * survival breakdown + drivers of fragility. Rendered inside the V3 expanded
 * body.
 */
export function MLBScriptSurvivalDetail({ scriptV5, testIdPrefix }) {
  if (!scriptV5 || typeof scriptV5 !== 'object') return null;
  const surv = scriptV5.survival || {};
  const frag = scriptV5.fragility || {};
  const components = surv.components || {};
  const weights = surv.weights || {};
  const labelMap = {
    pitchers:    'Pitchers',
    bullpen:     'Bullpen',
    offense:     'Lineups',
    environment: 'Park/Weather',
    historical:  'Historial',
  };
  const orderedKeys = ['pitchers', 'bullpen', 'offense', 'environment', 'historical'];
  return (
    <div className="rounded-lg border border-border/40 bg-white/[0.02] px-2.5 py-2 space-y-2" data-testid={`${testIdPrefix}-v5-detail`}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Shield className="h-3 w-3" />
          Script survival · desglose
        </span>
        <span className="text-[10px] text-muted-foreground">
          {scriptV5.confidence_contribution != null ? `Δ conf ${Number(scriptV5.confidence_contribution) >= 0 ? '+' : ''}${Number(scriptV5.confidence_contribution).toFixed(1)}` : ''}
        </span>
      </div>
      <div className="space-y-0.5">
        {orderedKeys.map((k) => {
          const v = Number(components[k] ?? 0);
          const w = Number(weights[k] ?? 0);
          const tone = v >= 70 ? 'emerald' : v >= 50 ? 'sky' : v >= 35 ? 'amber' : 'rose';
          return (
            <div key={k} className="flex items-center justify-between text-[11px]" data-testid={`${testIdPrefix}-v5-comp-${k}`}>
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={`h-1.5 w-1.5 rounded-full ${TONE_BAR[tone] || 'bg-slate-500/70'}`} />
                <span className="text-muted-foreground truncate">{labelMap[k] || k}</span>
                {w ? <span className="text-[9px] text-muted-foreground/60 ml-1">{w}%</span> : null}
              </div>
              <span className="font-mono tabular-nums text-foreground/85">{v.toFixed(1)}</span>
            </div>
          );
        })}
      </div>
      {Array.isArray(frag.drivers) && frag.drivers.length > 0 ? (
        <ul className="border-t border-border/30 pt-1.5 space-y-0.5">
          {frag.drivers.map((d, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[10px] text-foreground/75">
              <Activity className="h-3 w-3 text-amber-400/70 mt-0.5 shrink-0" />
              <span>{d}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {Array.isArray(surv.rationale) && surv.rationale.length > 0 ? (
        <ul className="space-y-0.5">
          {surv.rationale.slice(0, 3).map((r, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[10px] text-foreground/70">
              <CheckCircle2 className="h-3 w-3 text-emerald-400/70 mt-0.5 shrink-0" />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}


export function MLBScriptV3Panel({ scriptV3, scriptV5, overDiscovery, testId }) {
  const [expanded, setExpanded] = useState(false);
  if (!scriptV3 || typeof scriptV3 !== 'object') return null;

  const {
    script,
    pitchers_block: pitchers,
    why_this_pick: why,
    confidence_breakdown: breakdown,
    baseball_reasons: reasons,
  } = scriptV3;

  if (!script && !pitchers && (!why || why.length === 0)) return null;

  const scriptCode = script?.script_code || 'LOW_VARIANCE_GAME';
  const tone = SCRIPT_TONE[scriptCode] || 'slate';
  const Icon = SCRIPT_ICON[scriptCode] || CircleDot;
  const baseTestId = testId || 'mlb-v3';

  return (
    <div
      className="rounded-xl border border-border/50 bg-white/[0.015] overflow-hidden"
      data-testid={`${baseTestId}-root`}
    >
      {/* Header: Expected Script */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-3 py-2 hover:bg-white/[0.03] transition-colors text-left"
        aria-expanded={expanded}
        data-testid={`${baseTestId}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] uppercase tracking-wide font-medium border ${TONE_CLASSES[tone]}`}
          >
            <Icon className="h-3 w-3" />
            Expected script
          </span>
          <span className="text-sm font-medium text-foreground/95 truncate">
            {script?.label_es || 'Pick MLB'}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {script?.expected_runs != null ? (
            <span className="text-[10px] text-muted-foreground tabular-nums">
              ER {Number(script.expected_runs).toFixed(1)}
            </span>
          ) : null}
          {script?.projected_margin != null ? (
            <span className="text-[10px] text-muted-foreground tabular-nums">
              Mgn {Number(script.projected_margin).toFixed(1)}
            </span>
          ) : null}
          <ChevronDown
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
        </div>
      </button>

      {/* Always-visible narrative + baseball reasons preview (compact) */}
      {(script?.narrative_es || (reasons && reasons.length > 0)) && (
        <div className="px-3 pb-2 space-y-1.5">
          {script?.narrative_es ? (
            <p className="text-[12px] text-foreground/80 leading-relaxed border-l-2 border-cyan-500/40 pl-2">
              {script.narrative_es}
            </p>
          ) : null}

          {/* MLB-V10 — Script Survival summary line. Always visible when
              V5 payload is present so the user sees stability at a glance. */}
          <MLBScriptSurvivalSummary scriptV5={scriptV5} testIdPrefix={baseTestId} />
          {/* MLB-V6 — Offensive Explosion summary line. Always visible when
              V6 payload is present so the user sees offense potential at a glance. */}
          <MLBOffensiveExplosionSummary overDiscovery={overDiscovery} testIdPrefix={baseTestId} />
          {Array.isArray(reasons) && reasons.length > 0 ? (
            <ul className="space-y-0.5">
              {reasons.slice(0, expanded ? reasons.length : 2).map((r, i) => (
                <li
                  key={i}
                  className="flex items-start gap-1.5 text-[11px] text-foreground/75"
                  data-testid={`${baseTestId}-reason-${i}`}
                >
                  <Sparkles className="h-3 w-3 text-cyan-400/70 mt-0.5 shrink-0" />
                  <span>{r}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}

      {/* Expanded body */}
      {expanded ? (
        <div className="px-3 pb-3 pt-1 space-y-3 border-t border-border/30">
          {/* Pitchers grid */}
          {pitchers && pitchers.bothConfirmed ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
                  <User className="h-3 w-3" />
                  Abridores confirmados
                </span>
                {pitchers.qualityDiff > 0 ? (
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    Δ {pitchers.qualityDiff} pts
                  </span>
                ) : null}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <PitcherCard side="away" data={pitchers.away} testIdPrefix={baseTestId} />
                <PitcherCard side="home" data={pitchers.home} testIdPrefix={baseTestId} />
              </div>
            </div>
          ) : null}

          {/* Why this pick */}
          {Array.isArray(why) && why.length > 0 ? (
            <div className="space-y-1">
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
                <Target className="h-3 w-3" />
                Por qué este pick
              </span>
              <div className="rounded-lg border border-border/40 bg-white/[0.02] px-2.5 py-1.5">
                {why.map((row) => (
                  <WhyRow key={row.key} row={row} testIdPrefix={baseTestId} />
                ))}
              </div>
            </div>
          ) : null}

          {/* Confidence breakdown */}
          {breakdown ? (
            <div className="rounded-lg border border-border/40 bg-white/[0.02] px-2.5 py-2">
              <ConfidenceBreakdownBar breakdown={breakdown} testIdPrefix={baseTestId} />
            </div>
          ) : null}

          {/* MLB-V10 — Script Survival detailed breakdown (inside expanded body) */}
          <MLBScriptSurvivalDetail scriptV5={scriptV5} testIdPrefix={baseTestId} />

          {/* MLB-V6 — Offensive Explosion detailed breakdown (inside expanded body) */}
          <MLBOffensiveExplosionDetail overDiscovery={overDiscovery} testIdPrefix={baseTestId} />

          {/* Key drivers chips */}
          {Array.isArray(script?.key_drivers) && script.key_drivers.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground flex items-center gap-1 mr-1">
                <Gauge className="h-3 w-3" />
                Drivers
              </span>
              {script.key_drivers.map((d, i) => (
                <span
                  key={i}
                  className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] bg-white/[0.04] border border-border/40 text-foreground/80"
                >
                  {d}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * MLBDiversityBadge — compact warning chip when the engine detects market
 * over-concentration on this pick's day (uses `_mlb_script_v3_diversity`).
 */
export function MLBDiversityBadge({ diversity, testId }) {
  if (!diversity || !diversity.is_dominant) return null;
  const pct = Math.round((Number(diversity.dominant_share) || 0) * 100);
  return (
    <div
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] bg-amber-500/10 border border-amber-500/30 text-amber-200"
      data-testid={testId || 'mlb-diversity-badge'}
      title={diversity.note_es || ''}
    >
      <Layers className="h-3 w-3" />
      <span>{pct}% del día en este mercado · valora alternativas</span>
    </div>
  );
}


/**
 * MLBBullpenSwapBadge — F6A — shows when the engine swapped a Full Game
 * Under for F5 Under (or a protected alternate line) because bullpen
 * risk was MEDIUM/HIGH while the Under thesis was supported by starters
 * + park.
 *
 * Consumes the `bullpen_swap_meta` block emitted by the orchestrator
 * (see services/mlb_under_market_selector.py).
 */
export function MLBBullpenSwapBadge({ meta, testId }) {
  if (!meta || !meta.rule_triggered) return null;
  const codes = Array.isArray(meta.reason_codes) ? meta.reason_codes : [];
  const lvl = String(meta.bullpen_risk_level || 'MEDIUM').toUpperCase();
  const tone = lvl === 'HIGH' ? 'rose' : 'amber';
  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        tone === 'rose'
          ? 'border-rose-500/35 bg-rose-500/10 text-rose-200'
          : 'border-amber-500/35 bg-amber-500/10 text-amber-200'
      }`}
      data-testid={testId || 'mlb-bullpen-swap-badge'}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span className="text-[11px] uppercase tracking-wide opacity-90">
          Bullpen frágil · {lvl.toLowerCase()}
        </span>
        {codes.includes('STARTER_PARK_SUPPORTS_F5_UNDER') ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.08] border border-current/30">
            F5 Under preferido
          </span>
        ) : null}
      </div>
      {meta.explanation ? (
        <p className="text-[11px] mt-1 opacity-95 leading-snug">
          {meta.explanation}
        </p>
      ) : null}
      {typeof meta.confidence_adjustment === 'number' && meta.confidence_adjustment !== 0 ? (
        <div className="mt-1 text-[10px] opacity-80 tabular-nums">
          Ajuste de confianza: {meta.confidence_adjustment > 0 ? '+' : ''}{meta.confidence_adjustment} pts
        </div>
      ) : null}
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════════
 *  MLB ENGINE V6 — OVER DISCOVERY ENGINE
 *  Re-balances the engine's historical bias towards Unders.
 *  Consumes the `_mlb_over_discovery` payload emitted by the orchestrator:
 *    {
 *      outcome,
 *      offensive_explosion: { score, components, weights, drivers, raw_inputs },
 *      offensive_script:    { code, label_es, tone, score },
 *      over_survival:       { score, drivers, components },
 *      best_over_market:    { market, line, edge, score, category, ... } | null,
 *      competition:         { winner_side, edge_gap, swap_required, explanation },
 *      narrative_es,
 *    }
 * ════════════════════════════════════════════════════════════════════════ */

const OFFENSIVE_SCRIPT_LABEL_FALLBACK = {
  OFFENSIVE_EXPLOSION:    'Explosión ofensiva',
  HIGH_SCORING:           'Alto scoring',
  ABOVE_AVERAGE_SCORING:  'Sobre el promedio',
  NEUTRAL:                'Neutral',
  LOW_SCORING:            'Bajo scoring',
  PITCHERS_DUEL:          'Duelo de pitchers',
};

const OFFENSIVE_SCRIPT_ICON = {
  OFFENSIVE_EXPLOSION:    Flame,
  HIGH_SCORING:           TrendingUp,
  ABOVE_AVERAGE_SCORING:  Zap,
  NEUTRAL:                CircleDot,
  LOW_SCORING:            TrendingDown,
  PITCHERS_DUEL:          Shield,
};

const OVER_SCRIPT_TONE_CLASSES = {
  rose:    'bg-rose-500/12 border-rose-500/35 text-rose-200',
  amber:   'bg-amber-500/12 border-amber-500/35 text-amber-200',
  sky:     'bg-sky-500/12 border-sky-500/35 text-sky-200',
  slate:   'bg-slate-500/12 border-slate-500/35 text-slate-200',
  emerald: 'bg-emerald-500/12 border-emerald-500/35 text-emerald-200',
};

/**
 * MLBOffensiveExplosionSummary — V6 — compact one-line summary chip with
 * the Offensive Explosion Score, the Offensive Script badge and the Over
 * Survival score. Mirrors the layout of `MLBScriptSurvivalSummary` so the
 * two chips stack cleanly above the baseball reasons.
 */
export function MLBOffensiveExplosionSummary({ overDiscovery, testIdPrefix }) {
  if (!overDiscovery || typeof overDiscovery !== 'object') return null;
  const expl = overDiscovery.offensive_explosion || {};
  const scriptInfo = overDiscovery.offensive_script || {};
  const surv = overDiscovery.over_survival || {};
  if (expl.score == null && scriptInfo.code == null) return null;

  const score = Number(expl.score ?? 0);
  const survival = Number(surv.score ?? 0);
  const code = scriptInfo.code || 'NEUTRAL';
  const label = scriptInfo.label_es || OFFENSIVE_SCRIPT_LABEL_FALLBACK[code] || code;
  const tone = scriptInfo.tone || 'sky';
  const cls = OVER_SCRIPT_TONE_CLASSES[tone] || OVER_SCRIPT_TONE_CLASSES.sky;
  const Icon = OFFENSIVE_SCRIPT_ICON[code] || Flame;

  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border ${cls}`}
      data-testid={`${testIdPrefix}-v6-summary`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span className="text-[11px] uppercase tracking-wide opacity-90">Ofensiva</span>
        <span
          className="text-sm font-semibold truncate"
          data-testid={`${testIdPrefix}-v6-label`}
        >
          {label}
        </span>
      </div>
      <div className="flex items-center gap-3 shrink-0 text-[11px] tabular-nums">
        <span data-testid={`${testIdPrefix}-v6-explosion`}>
          <span className="opacity-70 mr-1">Explosion</span>
          <span className="font-bold">{score.toFixed(0)}/100</span>
        </span>
        {surv?.score != null ? (
          <span data-testid={`${testIdPrefix}-v6-over-survival`}>
            <span className="opacity-70 mr-1">Over Surv</span>
            <span className="font-bold">{survival.toFixed(0)}/100</span>
          </span>
        ) : null}
      </div>
    </div>
  );
}


/**
 * MLBOffensiveExplosionDetail — V6 — expanded detail card with the
 * per-component Offensive Explosion breakdown (lineups / pitchers /
 * bullpens / park / weather), the Top Offensive Drivers and, if
 * available, the best Over market with its edge.
 */
export function MLBOffensiveExplosionDetail({ overDiscovery, testIdPrefix }) {
  if (!overDiscovery || typeof overDiscovery !== 'object') return null;
  const expl = overDiscovery.offensive_explosion || {};
  const components = expl.components || {};
  const weights = expl.weights || {};
  const drivers = Array.isArray(expl.drivers) ? expl.drivers : [];
  const best = overDiscovery.best_over_market || null;
  const outcome = overDiscovery.outcome;

  if (expl.score == null && drivers.length === 0 && !best) return null;

  const labelMap = {
    lineups:  'Lineups',
    pitchers: 'Pitchers',
    bullpens: 'Bullpens',
    park:     'Park',
    weather:  'Weather',
  };
  const orderedKeys = ['lineups', 'pitchers', 'bullpens', 'park', 'weather'];

  return (
    <div
      className="rounded-lg border border-border/40 bg-white/[0.02] px-2.5 py-2 space-y-2"
      data-testid={`${testIdPrefix}-v6-detail`}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Flame className="h-3 w-3" />
          Offensive Explosion · desglose
        </span>
        <span className="text-[10px] text-muted-foreground tabular-nums">
          {expl.score != null
            ? `${Number(expl.score).toFixed(0)}/100`
            : ''}
        </span>
      </div>

      {/* Per-component breakdown bars */}
      <div className="space-y-0.5">
        {orderedKeys.map((k) => {
          if (components[k] == null) return null;
          const v = Number(components[k] ?? 0);
          const w = Number(weights[k] ?? 0);
          const tone = v >= 70 ? 'rose' : v >= 55 ? 'amber' : v >= 40 ? 'sky' : 'emerald';
          return (
            <div
              key={k}
              className="flex items-center justify-between text-[11px]"
              data-testid={`${testIdPrefix}-v6-comp-${k}`}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={`h-1.5 w-1.5 rounded-full ${TONE_BAR[tone] || 'bg-slate-500/70'}`} />
                <span className="text-muted-foreground truncate">{labelMap[k] || k}</span>
                {w ? (
                  <span className="text-[9px] text-muted-foreground/60 ml-1">
                    {w}%
                  </span>
                ) : null}
              </div>
              <span className="font-mono tabular-nums text-foreground/85">
                {v.toFixed(1)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Top offensive drivers */}
      {drivers.length > 0 ? (
        <ul className="border-t border-border/30 pt-1.5 space-y-0.5">
          {drivers.slice(0, 5).map((d, i) => (
            <li
              key={i}
              className="flex items-start gap-1.5 text-[10px] text-foreground/75"
              data-testid={`${testIdPrefix}-v6-driver-${i}`}
            >
              <Sparkles className="h-3 w-3 text-rose-400/70 mt-0.5 shrink-0" />
              <span>{d}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {/* Best Over market discovered */}
      {best ? (
        <div
          className="border-t border-border/30 pt-1.5 flex items-center justify-between gap-2 text-[11px]"
          data-testid={`${testIdPrefix}-v6-best-over`}
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <Target className="h-3 w-3 text-rose-400/80 shrink-0" />
            <span className="text-muted-foreground">Mejor Over</span>
            <span className="font-medium text-foreground/90 truncate">
              {best.market}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0 tabular-nums">
            {best.edge != null ? (
              <span className={`font-mono ${Number(best.edge) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                edge {Number(best.edge) >= 0 ? '+' : ''}{Number(best.edge).toFixed(1)}
              </span>
            ) : null}
            {best.score != null ? (
              <span className="font-mono text-foreground/80">
                {Number(best.score).toFixed(0)}/100
              </span>
            ) : null}
          </div>
        </div>
      ) : outcome === 'NO_OVER_EDGE' ? (
        <div
          className="border-t border-border/30 pt-1.5 text-[10px] text-muted-foreground italic"
          data-testid={`${testIdPrefix}-v6-no-edge`}
        >
          Sin Over con edge accionable.
        </div>
      ) : null}
    </div>
  );
}


/**
 * MLBOverSwapBadge — V6 — visible badge shown when the Market Competition
 * step swapped a Full Game Under for an Over candidate because the
 * offensive edge clearly dominated. Mirrors the layout of
 * `MLBBullpenSwapBadge` so the two badges stack consistently.
 *
 * Consumes either:
 *  - meta.over_swap === true + meta.over_swap_meta (from `recommendation`)
 *  - or the full `_mlb_over_discovery` payload when competition.winner_side === 'OVER'
 */
export function MLBOverSwapBadge({ meta, overDiscovery, testId }) {
  const swapMeta = meta?.over_swap_meta || overDiscovery || null;
  if (!swapMeta) return null;
  const comp = swapMeta.competition || {};
  const winnerSide = comp.winner_side || (meta?.over_swap ? 'OVER' : null);
  if (winnerSide !== 'OVER') return null;

  const best = swapMeta.best_over_market || null;
  const scriptInfo = swapMeta.offensive_script || {};
  const tone = scriptInfo.tone === 'rose' ? 'rose' : 'amber';

  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        tone === 'rose'
          ? 'border-rose-500/35 bg-rose-500/10 text-rose-200'
          : 'border-amber-500/35 bg-amber-500/10 text-amber-200'
      }`}
      data-testid={testId || 'mlb-over-swap-badge'}
    >
      <div className="flex items-center gap-2">
        <ArrowRightLeft className="h-3.5 w-3.5 shrink-0" />
        <span className="text-[11px] uppercase tracking-wide opacity-90">
          Mercado cambiado · Under → Over
        </span>
        {best?.market ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.08] border border-current/30 truncate max-w-[60%]">
            {best.market}
          </span>
        ) : null}
      </div>
      {comp.explanation ? (
        <p className="text-[11px] mt-1 opacity-95 leading-snug">
          {comp.explanation}
        </p>
      ) : swapMeta.narrative_es ? (
        <p className="text-[11px] mt-1 opacity-95 leading-snug">
          {swapMeta.narrative_es}
        </p>
      ) : null}
      {typeof comp.edge_gap === 'number' && comp.edge_gap > 0 ? (
        <div className="mt-1 text-[10px] opacity-80 tabular-nums">
          Ventaja de edge: {comp.edge_gap.toFixed(1)} carreras
        </div>
      ) : null}
    </div>
  );
}


/**
 * MLBMarketAuditBadge — V6 — daily market audit warning chip (e.g.
 * UNDER_BIAS_WARNING, OVER_STARVATION, MARKET_CONCENTRATION_WARNING).
 * Optional — only renders if a `bias.warning_codes` array is provided.
 */
export function MLBMarketAuditBadge({ audit, testId }) {
  if (!audit || !audit.bias) return null;
  const codes = Array.isArray(audit.bias.warning_codes) ? audit.bias.warning_codes : [];
  if (codes.length === 0) return null;
  const isUnderBias = codes.includes('UNDER_BIAS_WARNING') || codes.includes('OVER_STARVATION');
  const tone = isUnderBias ? 'rose' : 'amber';
  const label = isUnderBias
    ? 'Sesgo hacia Unders detectado'
    : codes.includes('OVER_BIAS_WARNING')
    ? 'Sesgo hacia Overs detectado'
    : 'Concentración de mercado';
  return (
    <div
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] border ${
        tone === 'rose'
          ? 'bg-rose-500/10 border-rose-500/30 text-rose-200'
          : 'bg-amber-500/10 border-amber-500/30 text-amber-200'
      }`}
      data-testid={testId || 'mlb-market-audit-badge'}
      title={audit.narrative_es || ''}
    >
      <AlertTriangle className="h-3 w-3" />
      <span>{label}</span>
    </div>
  );
}
