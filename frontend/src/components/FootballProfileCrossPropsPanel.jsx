/**
 * FootballProfileCrossPropsPanel
 * ===============================
 * Phase F58 — Panel UI **independiente** que renderiza:
 *
 *   1. **Cross Profile (L5 vs L15)** — 7 perfiles (STRONG_UNDER_CROSS,
 *      LOW_EVENT_UNDER_CROSS, STRONG_OVER_CROSS, BILATERAL_BTTS_CROSS,
 *      UNILATERAL_DOMINANCE_CROSS, CORNERS_OVER_CROSS, MIXED_PROFILE).
 *      Lee `combined_football_profile_cross` desde el pick payload.
 *
 *   2. **Override Alert** — visible solo cuando el cross emitió un
 *      override recomendado (perfil STRONG + contradice pick).
 *      Lee `football_profile_cross_applied.override`.
 *
 *   3. **Player Props (Moneyball)** — lista ordenada de Tier 1/2/3.
 *      Lee `player_props_discovery.props` cuando esté presente.
 *
 * Diseño (independiente del HistoricalProfilePanel de MLB):
 * - Card-based layout con header propio "Inteligencia F58".
 * - Color tokens del sistema (emerald/cyan/amber/rose) — sin purple.
 * - data-testid en cada sub-bloque para tests RTL.
 * - Fail-soft: cada sección retorna null si su payload no está.
 */

import React from 'react';
import {
  Activity, AlertTriangle, ArrowRight, Crosshair, Target, TrendingUp,
} from 'lucide-react';
import { PlayerHeatmapDialog } from '@/components/PlayerHeatmapDialog';

// ─────────────────────────────────────────────────────────────────────
// Helpers compartidos (locales — no rompemos otros paneles)
// ─────────────────────────────────────────────────────────────────────
const SUPPORTS_LABEL = {
  OVER:    'Apoya Over',
  UNDER:   'Apoya Under',
  BTTS:    'Apoya BTTS',
  CORNERS: 'Apoya Corners',
  NEUTRAL: 'Cruce mixto',
};

const SUPPORTS_TONE = {
  OVER:    'border-rose-500/40    bg-rose-500/10    text-rose-200',
  UNDER:   'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  BTTS:    'border-cyan-500/40    bg-cyan-500/10    text-cyan-200',
  CORNERS: 'border-amber-500/40   bg-amber-500/10   text-amber-200',
  NEUTRAL: 'border-slate-500/40   bg-slate-500/10   text-slate-200',
};

const PROFILE_ICON = {
  STRONG_UNDER_CROSS:         '🛡️',
  LOW_EVENT_UNDER_CROSS:      '💤',
  STRONG_OVER_CROSS:          '🔥',
  BILATERAL_BTTS_CROSS:       '⚔️',
  UNILATERAL_DOMINANCE_CROSS: '↗️',
  CORNERS_OVER_CROSS:         '🚩',
  MIXED_PROFILE:              '🌀',
};

// Replace emoji icons with Lucide to comply with design rules (no AI/decorative emojis).
function ProfileBadge({ profile, supports, strong, testId }) {
  const tone = SUPPORTS_TONE[supports] || SUPPORTS_TONE.NEUTRAL;
  const label = SUPPORTS_LABEL[supports] || 'Neutral';
  const Icon =
    supports === 'OVER'   ? TrendingUp :
    supports === 'UNDER'  ? Activity   :
    supports === 'BTTS'   ? Crosshair  :
    supports === 'CORNERS'? Target     :
    Activity;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border text-[11px] font-semibold tabular-nums ${tone}`}
    >
      <Icon className="w-3 h-3" />
      {label}{strong ? ' (fuerte)' : ''}
    </span>
  );
}

function fmt(v, ndigits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(ndigits);
}

function TeamLineRow({ label, l5, l15, l5n, l15n, deltaPositiveBad, testId }) {
  const delta =
    (l5 !== null && l5 !== undefined && l15 !== null && l15 !== undefined)
      ? Number(l5) - Number(l15) : null;
  const arrow =
    delta === null ? '' :
    delta >= 0.3 ? ' ↑' :
    delta <= -0.3 ? ' ↓' :
    '';
  const arrowColor =
    delta === null ? 'text-slate-500' :
    (delta >= 0.3 ?
      (deltaPositiveBad ? 'text-rose-300' : 'text-emerald-300') :
      delta <= -0.3 ?
        (deltaPositiveBad ? 'text-emerald-300' : 'text-rose-300') :
        'text-slate-500');
  // Sprint-B prereq · render sample-size subscripts when provided so
  // the user can see when L5/L15 averaged over the same underlying
  // fixtures (the "Goles 2.33 vs 2.33" pathology).
  const sampleSubscript = (n) =>
    (typeof n === 'number' && n > 0)
      ? <sub className="text-[8px] text-slate-500 ml-0.5 tabular-nums">n={n}</sub>
      : null;
  return (
    <div className="flex items-center justify-between gap-3 text-[11px] leading-snug" data-testid={testId}>
      <span className="text-slate-400 min-w-[68px]">{label}</span>
      <span className="text-slate-100 font-medium tabular-nums">
        {fmt(l5)}{sampleSubscript(l5n)}
      </span>
      <span className="text-slate-500">vs</span>
      <span className="text-slate-300 font-medium tabular-nums">
        {fmt(l15)}{sampleSubscript(l15n)}
      </span>
      <span className={`tabular-nums w-3 ${arrowColor}`}>{arrow}</span>
    </div>
  );
}

function TeamColumn({ title, side, sample, deltaPositiveBad, testId }) {
  if (!side) return null;
  const s = sample || {};
  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-3 space-y-1.5" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold">{title}</div>
      <TeamLineRow label="Goles+"   l5={side.goals_for_l5}     l15={side.goals_for_l15}     l5n={s.goals_for_l5_n}     l15n={s.goals_for_l15_n}     deltaPositiveBad={false} testId={`${testId}-gf`} />
      <TeamLineRow label="Goles−"   l5={side.goals_against_l5} l15={side.goals_against_l15} l5n={s.goals_against_l5_n} l15n={s.goals_against_l15_n} deltaPositiveBad={true}  testId={`${testId}-ga`} />
      {(side.shots_l5 !== null && side.shots_l5 !== undefined) && (
        <TeamLineRow label="Tiros"    l5={side.shots_l5}        l15={side.shots_l15}        l5n={s.shots_l5_n}    l15n={s.shots_l15_n}    deltaPositiveBad={false} testId={`${testId}-shots`} />
      )}
      {(side.sot_l5 !== null && side.sot_l5 !== undefined) && (
        <TeamLineRow label="SOT"      l5={side.sot_l5}          l15={side.sot_l15}          l5n={s.sot_l5_n}      l15n={s.sot_l15_n}      deltaPositiveBad={false} testId={`${testId}-sot`} />
      )}
      {(side.corners_l5 !== null && side.corners_l5 !== undefined) && (
        <TeamLineRow label="Corners"  l5={side.corners_l5}      l15={side.corners_l15}      l5n={s.corners_l5_n}  l15n={s.corners_l15_n}  deltaPositiveBad={false} testId={`${testId}-corners`} />
      )}
      {(side.xg_l5 !== null && side.xg_l5 !== undefined) && (
        <TeamLineRow label="xG"       l5={side.xg_l5}           l15={side.xg_l15}           deltaPositiveBad={false} testId={`${testId}-xg`} />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 1. Cross Profile sub-panel
// ─────────────────────────────────────────────────────────────────────
function CrossProfileSection({ cross, testId }) {
  if (!cross || !cross.available || !cross.profile) return null;
  const supports = String(cross.supports || 'NEUTRAL').toUpperCase();
  const strong = String(cross.profile).startsWith('STRONG_');

  return (
    <div className="space-y-3" data-testid={`${testId}-cross`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-300 font-semibold">
          <Activity className="w-3.5 h-3.5 text-cyan-300" />
          Cross Profile · L5 vs L15
        </div>
        <ProfileBadge
          profile={cross.profile}
          supports={supports}
          strong={strong}
          testId={`${testId}-cross-badge`}
        />
      </div>

      <div className="text-[10px] font-mono uppercase tracking-wide text-slate-500" data-testid={`${testId}-cross-profile-key`}>
        Perfil: <span className="text-slate-300">{cross.profile}</span>
        {typeof cross.confidence_delta === 'number' && (
          <>
            <span className="text-slate-600 mx-1.5">·</span>
            Δconf <span className="text-cyan-300 tabular-nums">{cross.confidence_delta > 0 ? '+' : ''}{cross.confidence_delta}</span>
          </>
        )}
        {typeof cross.fragility_delta === 'number' && (
          <>
            <span className="text-slate-600 mx-1.5">·</span>
            Δfrag <span className="text-amber-300 tabular-nums">{cross.fragility_delta > 0 ? '+' : ''}{cross.fragility_delta}</span>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        <TeamColumn title="Local"     side={cross?.per_team?.home} sample={cross?._l5_l15_sample?.home} deltaPositiveBad={false} testId={`${testId}-cross-home`} />
        <TeamColumn title="Visitante" side={cross?.per_team?.away} sample={cross?._l5_l15_sample?.away} deltaPositiveBad={false} testId={`${testId}-cross-away`} />
      </div>

      {/* Sprint-B prereq · Thin-sample banner. Shown when EITHER team
          has fewer than 5 fixtures (L5/L15 collapse to identical
          averages over the same window — the user-reported "los goles
          parecen córners" symptom). */}
      {(cross?._l5_l15_sample?.home?.l5_eq_l15_collapsed
        || cross?._l5_l15_sample?.away?.l5_eq_l15_collapsed
        || cross?._l5_l15_sample?.home?.l15_thin_sample
        || cross?._l5_l15_sample?.away?.l15_thin_sample) && (
        <div
          className="rounded-md border border-amber-500/30 bg-amber-500/[0.06] px-2.5 py-1.5 text-[10.5px] text-amber-200/90 leading-snug"
          data-testid={`${testId}-cross-thin-sample`}
        >
          <span className="font-semibold uppercase tracking-wider mr-1.5">Muestra delgada</span>
          L5 y L15 se computan sobre la misma ventana de partidos para al menos un equipo.
          Los promedios mostrados pueden ser idénticos (n insuficiente) — interpretar con precaución.
        </div>
      )}

      {cross.narrative_es && (
        <div
          className="text-[11.5px] text-slate-300 italic leading-snug border-l-2 border-cyan-500/40 pl-2.5"
          data-testid={`${testId}-cross-narrative`}
        >
          {cross.narrative_es}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 2. Override alert sub-panel
// ─────────────────────────────────────────────────────────────────────
function OverrideAlert({ applied, testId }) {
  if (!applied || !applied.override || applied.override.enabled !== true) return null;
  const ov = applied.override;
  return (
    <div
      className="rounded-lg border border-amber-500/40 bg-amber-500/[0.08] p-3 space-y-1.5"
      data-testid={`${testId}-override`}
    >
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-amber-200">
        <AlertTriangle className="w-3.5 h-3.5" />
        Override sugerido por Cross
      </div>
      <div className="text-[11.5px] text-amber-100/90 leading-snug" data-testid={`${testId}-override-detail`}>
        Perfil <span className="font-mono">{ov.profile}</span> contradice el pick actual.
        El motor sugiere migrar a{' '}
        <span className="inline-flex items-center gap-1 font-semibold text-amber-50">
          <ArrowRight className="w-3 h-3" />
          {ov.recommended_market} ({ov.recommended_side})
        </span>.
      </div>
      {ov.previous_market && (
        <div className="text-[10px] text-amber-200/70 font-mono">
          Pick previo: {ov.previous_market} → {ov.previous_side || '—'}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 3. Player Props sub-panel (Moneyball)
// ─────────────────────────────────────────────────────────────────────
const TIER_TONE = {
  1: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  2: 'border-cyan-500/40    bg-cyan-500/10    text-cyan-200',
  3: 'border-amber-500/40   bg-amber-500/10   text-amber-200',
};

const CONF_TIER_TONE = {
  PREMIUM: 'border-emerald-400/60 bg-emerald-500/15 text-emerald-100',
  VALUE:   'border-cyan-400/60    bg-cyan-500/15    text-cyan-100',
  LEAN:    'border-amber-400/60   bg-amber-500/15   text-amber-100',
  WATCH:   'border-slate-400/60   bg-slate-500/15   text-slate-100',
};

function PropRow({ prop, testId, matchId, teamHint }) {
  const tier = prop.tier || 2;
  const score = prop.player_prop_score ?? prop.edge_score;
  const fragility = prop.player_prop_fragility ?? prop.fragility;
  const playerName = prop.player_name || prop.player;
  return (
    <div
      className="flex items-start justify-between gap-3 rounded-md border border-slate-700/40 bg-slate-900/30 p-2.5"
      data-testid={testId}
    >
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide border ${TIER_TONE[tier] || TIER_TONE[2]}`}
            data-testid={`${testId}-tier`}
          >
            Tier {tier}
          </span>
          <span className="text-[11.5px] font-semibold text-slate-100 truncate" data-testid={`${testId}-player`}>
            {playerName}
          </span>
          <span className="text-[10px] text-slate-500 font-mono">
            {prop.market} {fmt(prop.line, 1)} (OVER)
          </span>
          {/* Phase F68 — Lazy heatmap viewer. Activates only when we
              have a player name + match context; degrades gracefully
              otherwise (the dialog itself reports MISSING_*). */}
          {playerName && matchId && (
            <PlayerHeatmapDialog
              playerName={playerName}
              matchId={matchId}
              teamHint={teamHint || prop.team || prop.team_name}
              testIdPrefix={`${testId}-heatmap`}
            />
          )}
        </div>
        {prop.narrative_es && (
          <div className="text-[11px] text-slate-300/90 leading-snug" data-testid={`${testId}-narrative`}>
            {prop.narrative_es}
          </div>
        )}
        <div className="flex items-center gap-3 text-[10px] text-slate-400 font-mono">
          <span>λ <span className="text-slate-200 tabular-nums">{fmt(prop.lambda_estimate, 2)}</span></span>
          <span>prob <span className="text-emerald-300 tabular-nums">{fmt((prop.model_probability || 0) * 100, 1)}%</span></span>
          <span>edge <span className="text-cyan-300 tabular-nums">+{fmt(prop.edge_points, 1)}pts</span></span>
        </div>
      </div>
      <div className="flex flex-col items-end gap-1 min-w-[80px]">
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${CONF_TIER_TONE[prop.confidence_tier] || CONF_TIER_TONE.WATCH}`}
          data-testid={`${testId}-conf`}
        >
          {prop.confidence_tier || 'WATCH'}
        </span>
        <div className="text-[10px] text-slate-400 font-mono">
          Score <span className="text-slate-100 tabular-nums" data-testid={`${testId}-score`}>{score ?? '—'}</span>
        </div>
        <div className="text-[10px] text-slate-500 font-mono">
          Frag <span className="text-amber-300 tabular-nums" data-testid={`${testId}-fragility`}>{fragility ?? '—'}</span>
        </div>
      </div>
    </div>
  );
}

function PlayerPropsSection({ discovery, testId, matchId, teamHint }) {
  if (!discovery || !discovery.available) return null;
  // v2 prefers `top_player_props`; falls back to `props` for backward compat.
  const props = Array.isArray(discovery.top_player_props) && discovery.top_player_props.length
    ? discovery.top_player_props
    : (Array.isArray(discovery.props) ? discovery.props : []);
  if (!props.length) return null;
  const summary = discovery.summary || {};

  return (
    <div className="space-y-2" data-testid={`${testId}-props`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-300 font-semibold">
          <Target className="w-3.5 h-3.5 text-emerald-300" />
          Player Props (Moneyball)
        </div>
        <div className="text-[10px] font-mono text-slate-500 tabular-nums">
          T1 {summary.tier_1 ?? 0} · T2 {summary.tier_2 ?? 0} · T3 {summary.tier_3 ?? 0}
          {typeof summary.top_count === 'number' && (
            <> · <span className="text-emerald-300">Top {summary.top_count}</span></>
          )}
        </div>
      </div>
      <div className="space-y-1.5">
        {props.slice(0, 6).map((p, idx) => (
          <PropRow
            key={`${p.player_name || p.player}-${p.market}-${idx}`}
            prop={p}
            testId={`${testId}-prop-${idx}`}
            matchId={matchId}
            teamHint={teamHint}
          />
        ))}
        {props.length > 6 && (
          <div className="text-[10px] text-slate-500 font-mono pl-1">
            +{props.length - 6} más en la cola
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Main panel — combines all three sections.
// ─────────────────────────────────────────────────────────────────────
export function FootballProfileCrossPropsPanel({ pick, testId = 'football-f58-panel' }) {
  if (!pick || typeof pick !== 'object') return null;
  const cross =
    pick.combined_football_profile_cross ||
    pick.footballHistoricalProfile?.combinedFootballProfileCross ||
    null;
  const applied = pick.football_profile_cross_applied || null;
  const discovery = pick.player_props_discovery || null;

  // No data of any of the three → render nothing.
  const hasCross = !!(cross && cross.available && cross.profile);
  const hasOverride = !!(applied && applied.override && applied.override.enabled);
  const hasProps = !!(
    discovery && discovery.available &&
    ((Array.isArray(discovery.top_player_props) && discovery.top_player_props.length) ||
     (Array.isArray(discovery.props) && discovery.props.length))
  );
  if (!hasCross && !hasOverride && !hasProps) return null;

  return (
    <section
      className="rounded-xl border border-slate-700/40 bg-slate-900/60 p-4 space-y-4"
      data-testid={testId}
      aria-label="Football Phase F58 Intelligence Panel"
    >
      {/* Header */}
      <header className="flex items-center justify-between gap-3 border-b border-slate-700/30 pb-2">
        <div className="flex items-center gap-2">
          <div className="w-1 h-5 rounded-full bg-gradient-to-b from-emerald-400 to-cyan-400" aria-hidden />
          <h3 className="text-[12px] uppercase tracking-wider font-semibold text-slate-200">
            Inteligencia F58 · Cross & Props
          </h3>
        </div>
        <span className="text-[10px] font-mono text-slate-500 uppercase tracking-wide">
          Phase F58
        </span>
      </header>

      {hasCross && <CrossProfileSection cross={cross} testId={testId} />}
      {hasOverride && <OverrideAlert applied={applied} testId={testId} />}
      {hasProps && (
        <PlayerPropsSection
          discovery={discovery}
          testId={testId}
          matchId={pick.match_id}
          teamHint={
            (typeof pick.home_team === 'object' ? pick.home_team?.name : pick.home_team)
            || (typeof pick.away_team === 'object' ? pick.away_team?.name : pick.away_team)
          }
        />
      )}
    </section>
  );
}

export default FootballProfileCrossPropsPanel;
