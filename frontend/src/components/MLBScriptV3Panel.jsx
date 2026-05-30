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
                +{(Number(c.value) || 0).toFixed(1)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function MLBScriptV3Panel({ scriptV3, testId }) {
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
