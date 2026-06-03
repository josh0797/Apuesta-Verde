/**
 * MLBAdvancedStatsPanel — Fase 13.2
 *
 * Collapsible UI block that surfaces:
 *   • Statcast (xERA / xwOBA / barrel% / hard-hit% / wRC+ / OPS)
 *   • Sabermetrics (OPS / FIP / WAR + match edges + summary)
 *   • Pressure base tier + Market Selection summary
 *
 * Strictly MLB-only (caller gates by `sport === 'baseball'`).
 * Fail-soft: if no data, renders nothing (returns null).
 * Source badges per block (pybaseball / TheStatsAPI / Bright Data).
 */
import { useState } from 'react';
import { ChevronDown, ChevronUp, Activity, Brain, Layers, Shield, AlertTriangle, History, FileWarning, Sparkles } from 'lucide-react';

const SOURCE_LABELS = {
  pybaseball:  { label: 'pybaseball',  className: 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30' },
  thestatsapi: { label: 'TheStatsAPI', className: 'bg-violet-500/10 text-violet-200 border-violet-500/30' },
  brightdata:  { label: 'Bright Data', className: 'bg-amber-500/10 text-amber-200 border-amber-500/30' },
  statmuse:    { label: 'StatMuse',    className: 'bg-cyan-500/10 text-cyan-200 border-cyan-500/30' },
};

const QUALITY_LABEL = {
  strong:  { es: 'Calidad alta',     en: 'Strong quality',  className: 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30' },
  partial: { es: 'Calidad parcial',  en: 'Partial quality', className: 'bg-amber-500/10 text-amber-200 border-amber-500/30' },
  thin:    { es: 'Datos limitados',  en: 'Thin data',       className: 'bg-amber-500/10 text-amber-200 border-amber-500/30' },
  missing: { es: 'Sin datos',        en: 'No data',         className: 'bg-slate-500/10 text-slate-300 border-slate-500/30' },
};

function fmt(v, opts = {}) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  if (opts.pct) return `${n.toFixed(1)}%`;
  if (opts.three) return n.toFixed(3);
  return n.toFixed(2);
}

function QualityBadge({ quality, lang = 'es', testId }) {
  const meta = QUALITY_LABEL[quality] || QUALITY_LABEL.missing;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border ${meta.className}`}
    >
      {lang === 'es' ? meta.es : meta.en}
    </span>
  );
}

function SourceBadges({ sources = [], testIdPrefix }) {
  if (!sources?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid={`${testIdPrefix}-sources`}>
      {sources.map((s, i) => {
        const meta = SOURCE_LABELS[s] || { label: s, className: 'bg-slate-500/10 text-slate-200 border-slate-500/30' };
        return (
          <span
            key={`${s}-${i}`}
            data-testid={`${testIdPrefix}-source-${s}`}
            className={`inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border ${meta.className}`}
          >
            {meta.label}
          </span>
        );
      })}
    </div>
  );
}

function PitcherStatcastBlock({ block, label, testId }) {
  if (!block?.pitcher) return null;
  const p = block.pitcher;
  const sources = block.sources_consulted || [];
  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3" data-testid={testId}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-slate-200">{label}</div>
        <div className="flex items-center gap-2">
          <QualityBadge
            quality={block.data_quality || 'missing'}
            testId={`${testId}-quality`}
          />
          <SourceBadges sources={sources} testIdPrefix={testId} />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <Stat label="ERA"           value={fmt(p.era)}                         testId={`${testId}-era`} />
        <Stat label="xERA"          value={fmt(p.xera)}                        testId={`${testId}-xera`} />
        <Stat label="xwOBA perm."   value={fmt(p.xwoba_allowed, { three: true })} testId={`${testId}-xwoba`} />
        <Stat label="Barrel %"      value={fmt(p.barrel_pct_allowed, { pct: true })} testId={`${testId}-barrel`} />
        <Stat label="Hard hit %"    value={fmt(p.hard_hit_pct_allowed, { pct: true })} testId={`${testId}-hardhit`} />
        <Stat label="Whiff %"       value={fmt(p.whiff_pct, { pct: true })}    testId={`${testId}-whiff`} />
        <Stat label="K %"           value={fmt(p.k_pct, { pct: true })}        testId={`${testId}-kpct`} />
        <Stat label="BB %"          value={fmt(p.bb_pct, { pct: true })}       testId={`${testId}-bbpct`} />
        <Stat label="WHIP"          value={fmt(p.whip)}                        testId={`${testId}-whip`} />
      </div>
    </div>
  );
}

function TeamStatcastBlock({ block, label, testId }) {
  if (!block?.team) return null;
  const t = block.team;
  const sources = block.sources_consulted || [];
  return (
    <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3" data-testid={testId}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-slate-200">{label}</div>
        <div className="flex items-center gap-2">
          <QualityBadge
            quality={block.data_quality || 'missing'}
            testId={`${testId}-quality`}
          />
          <SourceBadges sources={sources} testIdPrefix={testId} />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <Stat label="OPS"        value={fmt(t.team_ops, { three: true })}      testId={`${testId}-ops`} />
        <Stat label="wRC+"       value={fmt(t.team_wrc_plus)}                  testId={`${testId}-wrcplus`} />
        <Stat label="xwOBA"      value={fmt(t.team_xwoba, { three: true })}    testId={`${testId}-xwoba`} />
        <Stat label="Barrel %"   value={fmt(t.team_barrel_pct, { pct: true })} testId={`${testId}-barrel`} />
        <Stat label="Hard hit %" value={fmt(t.team_hard_hit_pct, { pct: true })} testId={`${testId}-hardhit`} />
        <Stat label="Exit velo"  value={fmt(t.team_avg_exit_velocity)}         testId={`${testId}-exitvelo`} />
      </div>
    </div>
  );
}

function Stat({ label, value, testId }) {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wide text-slate-500">{label}</span>
      <span className="text-slate-100 font-medium">{value}</span>
    </div>
  );
}

function SabermetricsBlock({ saber, lang = 'es' }) {
  if (!saber?.available) return null;
  const homeOps = saber.home?.ops_profile || {};
  const awayOps = saber.away?.ops_profile || {};
  const homeFip = saber.home?.starting_pitcher_fip || {};
  const awayFip = saber.away?.starting_pitcher_fip || {};
  const homeWar = saber.home?.war_impact || {};
  const awayWar = saber.away?.war_impact || {};
  const edges = saber.match_edges || {};

  const edgeChip = (side, kind) => {
    if (!side || side === 'neutral') return null;
    const className = side === 'home'
      ? 'bg-cyan-500/10 text-cyan-200 border-cyan-500/30'
      : 'bg-rose-500/10 text-rose-200 border-rose-500/30';
    return (
      <span
        className={`inline-flex items-center text-[10px] font-medium px-2 py-0.5 rounded-full border ${className}`}
        data-testid={`mlb-saber-edge-${kind}`}
      >
        {kind.toUpperCase()}: {side}
      </span>
    );
  };

  return (
    <div className="space-y-3" data-testid="mlb-sabermetrics-block">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
          <Brain className="w-3.5 h-3.5 text-amber-300" />
          {lang === 'es' ? 'Sabermetría · OPS / FIP / WAR' : 'Sabermetrics · OPS / FIP / WAR'}
        </div>
        <QualityBadge quality={saber.data_quality} lang={lang} testId="mlb-saber-quality" />
      </div>
      <div className="flex flex-wrap gap-1.5" data-testid="mlb-saber-edges">
        {edgeChip(edges.ops_edge, 'ops')}
        {edgeChip(edges.fip_edge, 'fip')}
        {edgeChip(edges.war_edge, 'war')}
        {edgeChip(edges.overall_sabermetric_edge, 'overall')}
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-2" data-testid="mlb-saber-home">
          <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">{lang === 'es' ? 'Local' : 'Home'}</div>
          <Stat label="OPS"  value={`${fmt(homeOps.ops, { three: true })} (${homeOps.tier || '—'})`} />
          <Stat label="FIP"  value={`${fmt(homeFip.fip)} (${homeFip.tier || '—'})`} />
          <Stat label="WAR core" value={`${fmt(homeWar.lineup_war_score)} · elite=${homeWar.elite_war_count ?? 0}`} />
        </div>
        <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-2" data-testid="mlb-saber-away">
          <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">{lang === 'es' ? 'Visitante' : 'Away'}</div>
          <Stat label="OPS"  value={`${fmt(awayOps.ops, { three: true })} (${awayOps.tier || '—'})`} />
          <Stat label="FIP"  value={`${fmt(awayFip.fip)} (${awayFip.tier || '—'})`} />
          <Stat label="WAR core" value={`${fmt(awayWar.lineup_war_score)} · elite=${awayWar.elite_war_count ?? 0}`} />
        </div>
      </div>
      {saber.summary ? (
        <div className="text-[11px] text-slate-300 italic" data-testid="mlb-saber-summary">
          {saber.summary}
        </div>
      ) : null}
    </div>
  );
}

function MarketSelectionBlock({ selection, lang = 'es' }) {
  if (!selection?.recommended_market) return null;
  return (
    <div
      className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 space-y-2"
      data-testid="mlb-market-selection-block"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
          <Shield className="w-3.5 h-3.5 text-emerald-300" />
          {lang === 'es' ? 'Selección de mercado' : 'Market selection'}
        </div>
        <span
          className="text-[10px] font-semibold uppercase tracking-wide text-slate-200 px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60"
          data-testid="mlb-market-selection-recommended"
        >
          {selection.recommended_market}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <Stat
          label={lang === 'es' ? 'Confianza' : 'Confidence'}
          value={selection.market_confidence ?? '—'}
          testId="mlb-market-selection-confidence"
        />
        <Stat
          label={lang === 'es' ? 'Fragilidad' : 'Fragility'}
          value={selection.fragility ?? '—'}
          testId="mlb-market-selection-fragility"
        />
      </div>
      {selection.protected_alternative ? (
        <div
          className="text-[11px] text-slate-300"
          data-testid="mlb-market-selection-alternative"
        >
          {lang === 'es' ? 'Alternativa protegida: ' : 'Protected alternative: '}
          <span className="text-slate-100 font-medium">{selection.protected_alternative}</span>
        </div>
      ) : null}
      {selection.why_this_market ? (
        <div className="text-[11px] text-slate-300" data-testid="mlb-market-selection-why">
          {selection.why_this_market}
        </div>
      ) : null}
      {selection.why_not_other_markets?.length ? (
        <ul
          className="text-[11px] text-slate-400 list-disc list-inside space-y-0.5"
          data-testid="mlb-market-selection-why-not"
        >
          {selection.why_not_other_markets.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-1.5" data-testid="mlb-market-selection-reason-codes">
        {(selection.reason_codes || []).slice(0, 6).map((rc, i) => (
          <span
            key={i}
            className="text-[9px] px-1.5 py-0.5 rounded border border-slate-700 bg-slate-800/40 text-slate-300"
            data-testid={`mlb-market-selection-rc-${rc}`}
          >
            {rc}
          </span>
        ))}
      </div>
    </div>
  );
}

function GhostEdgesBlock({ ghost, lang = 'es' }) {
  if (!ghost || ghost.available === false || !ghost.flags?.length) return null;
  const titleEs = 'Señales fantasma (ghost-edges)';
  const titleEn = 'Ghost edges';
  return (
    <div
      className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 space-y-2"
      data-testid="mlb-ghost-edges-block"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-rose-200">
          <AlertTriangle className="w-3.5 h-3.5 text-rose-300" />
          {lang === 'es' ? titleEs : titleEn}
        </div>
        {ghost.blocked_pick ? (
          <span
            className="text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full border border-rose-400/50 bg-rose-500/20 text-rose-100"
            data-testid="mlb-ghost-edges-blocked"
          >
            {lang === 'es' ? 'Pick bloqueado' : 'Pick blocked'}
          </span>
        ) : null}
      </div>
      {ghost.narrative ? (
        <div className="text-[11px] text-rose-100/90 italic" data-testid="mlb-ghost-edges-narrative">
          {ghost.narrative}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        {ghost.flags.map((f, i) => (
          <span
            key={`${f}-${i}`}
            className="text-[10px] px-2 py-0.5 rounded-full border bg-rose-500/15 text-rose-100 border-rose-500/40"
            data-testid={`mlb-ghost-edge-flag-${f}`}
          >
            {f}
          </span>
        ))}
      </div>
    </div>
  );
}

function FragilitySurvivalBlock({ fragility, survival, lang = 'es' }) {
  // Render only when at least one block has data.
  const hasFragility = fragility?.available && typeof fragility?.score === 'number';
  const hasSurvival  = survival?.available && typeof survival?.score === 'number';
  if (!hasFragility && !hasSurvival) return null;

  const tierLabelES = {
    LOW: 'Baja', MEDIUM: 'Media', HIGH: 'Alta',
    HIGH_SURVIVAL: 'Supervivencia alta',
    MEDIUM_SURVIVAL: 'Supervivencia media',
    LOW_SURVIVAL: 'Supervivencia baja',
  };
  const tierClass = (tier) => {
    if (tier === 'HIGH' || tier === 'LOW_SURVIVAL') {
      return 'bg-rose-500/10 text-rose-200 border-rose-500/30';
    }
    if (tier === 'MEDIUM' || tier === 'MEDIUM_SURVIVAL') {
      return 'bg-amber-500/10 text-amber-200 border-amber-500/30';
    }
    return 'bg-emerald-500/10 text-emerald-200 border-emerald-500/30';
  };
  return (
    <div
      className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 space-y-2"
      data-testid="mlb-fragility-survival-block"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
          <Sparkles className="w-3.5 h-3.5 text-amber-300" />
          {lang === 'es' ? 'Supervivencia del guion · Fragilidad' : 'Script survival · Fragility'}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        {hasSurvival ? (
          <div data-testid="mlb-survival-stat">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              {lang === 'es' ? 'Supervivencia' : 'Survival'}
            </span>
            <div className="text-slate-100 font-medium flex items-center gap-2">
              <span data-testid="mlb-survival-score">{survival.score.toFixed(0)}/100</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${tierClass(survival.tier)}`}
                data-testid={`mlb-survival-tier-${survival.tier}`}>
                {lang === 'es' ? (tierLabelES[survival.tier] || survival.tier) : survival.tier}
              </span>
            </div>
          </div>
        ) : null}
        {hasFragility ? (
          <div data-testid="mlb-fragility-stat">
            <span className="text-[10px] uppercase tracking-wide text-slate-500">
              {lang === 'es' ? 'Fragilidad' : 'Fragility'}
            </span>
            <div className="text-slate-100 font-medium flex items-center gap-2">
              <span data-testid="mlb-fragility-score">{fragility.score.toFixed(0)}/100</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${tierClass(fragility.tier)}`}
                data-testid={`mlb-fragility-tier-${fragility.tier}`}>
                {lang === 'es' ? (tierLabelES[fragility.tier] || fragility.tier) : fragility.tier}
              </span>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PatternMemoryBlock({ pattern, lang = 'es' }) {
  if (!pattern || pattern.sample_size === 0) return null;

  const sampleTier = pattern.sample_tier;
  const tierLabel = {
    NONE:       lang === 'es' ? 'Sin muestra'         : 'No sample',
    LOW_SAMPLE: lang === 'es' ? 'Muestra pequeña'     : 'Low sample',
    USEFUL:     lang === 'es' ? 'Patrón útil'         : 'Useful pattern',
    VALIDATED:  lang === 'es' ? 'Patrón validado'     : 'Validated pattern',
  }[sampleTier] || sampleTier;

  const tierClass = {
    LOW_SAMPLE: 'bg-amber-500/10 text-amber-200 border-amber-500/30',
    USEFUL:     'bg-cyan-500/10 text-cyan-200 border-cyan-500/30',
    VALIDATED:  'bg-emerald-500/10 text-emerald-200 border-emerald-500/30',
  }[sampleTier] || 'bg-slate-500/10 text-slate-300 border-slate-500/30';

  return (
    <div
      className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-3 space-y-2"
      data-testid="mlb-pattern-memory-block"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-100">
          <History className="w-3.5 h-3.5 text-violet-300" />
          {lang === 'es' ? 'Memoria de patrones (Moneyball)' : 'Pattern memory (Moneyball)'}
        </div>
        <span
          className={`text-[10px] px-2 py-0.5 rounded-full border ${tierClass}`}
          data-testid="mlb-pattern-memory-tier"
        >
          {tierLabel} (n={pattern.sample_size})
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <span className="text-[10px] uppercase tracking-wide text-slate-500">
            {lang === 'es' ? 'Hit rate' : 'Hit rate'}
          </span>
          <div className="text-slate-100 font-medium" data-testid="mlb-pattern-memory-hit-rate">
            {pattern.historical_hit_rate != null
              ? `${(Number(pattern.historical_hit_rate) * 100).toFixed(1)}%`
              : '—'}
          </div>
        </div>
        <div>
          <span className="text-[10px] uppercase tracking-wide text-slate-500">ROI</span>
          <div className="text-slate-100 font-medium" data-testid="mlb-pattern-memory-roi">
            {pattern.historical_roi != null
              ? `${(Number(pattern.historical_roi) * 100).toFixed(1)}%`
              : '—'}
          </div>
        </div>
        <div>
          <span className="text-[10px] uppercase tracking-wide text-slate-500">
            {lang === 'es' ? 'Mejor mercado' : 'Best market'}
          </span>
          <div className="text-slate-100 font-medium text-[10px]" data-testid="mlb-pattern-memory-best-market">
            {pattern.best_historical_market || '—'}
          </div>
        </div>
      </div>
      {pattern.warning ? (
        <div className="text-[11px] text-amber-200 italic" data-testid="mlb-pattern-memory-warning">
          {pattern.warning}
        </div>
      ) : null}
      {pattern.primary_pattern ? (
        <div className="text-[10px] text-slate-400" data-testid="mlb-pattern-memory-primary">
          {lang === 'es' ? 'Patrón principal: ' : 'Primary pattern: '}
          <span className="text-slate-200 font-mono">{pattern.primary_pattern}</span>
        </div>
      ) : null}
    </div>
  );
}

function ManualOddsReviewBlock({ review, lang = 'es' }) {
  if (!review || !review.required) return null;
  return (
    <div
      className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 space-y-2"
      data-testid="mlb-manual-odds-review-block"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-amber-100">
          <FileWarning className="w-3.5 h-3.5 text-amber-300" />
          {lang === 'es' ? 'Revisión manual de cuota' : 'Manual odds review'}
        </div>
        <span
          className="text-[10px] font-semibold uppercase tracking-wide text-amber-100 px-2 py-0.5 rounded-full border border-amber-400/50 bg-amber-500/20"
          data-testid="mlb-manual-odds-required-chip"
        >
          {lang === 'es' ? 'Requiere acción' : 'Action required'}
        </span>
      </div>
      <div className="text-[11px] text-amber-100/90" data-testid="mlb-manual-odds-action">
        {lang === 'es' ? review.user_action_es : review.user_action}
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div>
          <span className="text-[10px] uppercase tracking-wide text-amber-100/70">
            {lang === 'es' ? 'Mercado sugerido' : 'Suggested market'}
          </span>
          <div className="text-amber-50 font-medium" data-testid="mlb-manual-odds-market">
            {review.recommended_market || '—'}
          </div>
        </div>
        <div>
          <span className="text-[10px] uppercase tracking-wide text-amber-100/70">
            {lang === 'es' ? 'Prob. del modelo' : 'Model probability'}
          </span>
          <div className="text-amber-50 font-medium" data-testid="mlb-manual-odds-engine-prob">
            {review.engine_probability != null
              ? `${(review.engine_probability * 100).toFixed(1)}%`
              : '—'}
          </div>
        </div>
      </div>
      {review.why_this_market ? (
        <div className="text-[11px] text-amber-100/80 italic" data-testid="mlb-manual-odds-why">
          {review.why_this_market}
        </div>
      ) : null}
    </div>
  );
}

export function MLBAdvancedStatsPanel({ pick, lang = 'es' }) {
  const [open, setOpen] = useState(false);
  const snap = pick?.advanced_stats_snapshot;
  const saber = pick?.sabermetrics;
  const audit = pick?.advanced_adjustments;
  const saberAudit = pick?.sabermetrics_audit;
  const selection = pick?.market_selection;
  const pressure = pick?.pressure_base?.combined;
  const ghostEdges = pick?.ghost_edges;
  const patternAudit = pick?.pattern_memory_audit;
  const manualOdds = pick?.manual_odds_review;
  const fragilityAudit = pick?.fragility_audit;
  const survivalAudit = pick?.script_survival_audit;

  // Determine whether ANY block has data — if none, render nothing.
  const hasStatcast = !!(snap && (
    snap.home_pitcher_advanced?.available ||
    snap.away_pitcher_advanced?.available ||
    snap.home_team_advanced?.available ||
    snap.away_team_advanced?.available
  ));
  const hasSaber = !!saber?.available;
  const hasSelection = !!selection?.recommended_market;
  const hasGhost = !!(ghostEdges?.available && ghostEdges?.flags?.length);
  const hasPattern = !!(patternAudit && patternAudit.sample_size > 0);
  const hasManualOdds = !!manualOdds?.required;
  const hasFragSurv = !!((fragilityAudit?.available || survivalAudit?.available));

  if (!hasStatcast && !hasSaber && !hasSelection
      && !hasGhost && !hasPattern && !hasManualOdds && !hasFragSurv) {
    return null;
  }

  return (
    <div
      className="mt-3 rounded-xl border border-slate-700/60 bg-slate-900/40 backdrop-blur-sm"
      data-testid="mlb-advanced-stats-panel"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-slate-800/40 rounded-xl transition-colors"
        data-testid="mlb-advanced-stats-toggle"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <Layers className="w-4 h-4 text-amber-300" />
          <span className="text-sm font-semibold text-slate-100">
            {lang === 'es' ? 'MLB · Estadísticas avanzadas' : 'MLB · Advanced Stats'}
          </span>
          <div className="flex items-center gap-1.5">
            {hasStatcast && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-emerald-500/10 text-emerald-200 border-emerald-500/30"
                data-testid="mlb-advanced-stats-statcast-flag"
              >
                Statcast
              </span>
            )}
            {hasSaber && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-amber-500/10 text-amber-200 border-amber-500/30"
                data-testid="mlb-advanced-stats-saber-flag"
              >
                Sabermetría
              </span>
            )}
            {hasSelection && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-cyan-500/10 text-cyan-200 border-cyan-500/30"
                data-testid="mlb-advanced-stats-selection-flag"
              >
                {lang === 'es' ? 'Mercado' : 'Market'}
              </span>
            )}
            {hasGhost && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-rose-500/10 text-rose-200 border-rose-500/30"
                data-testid="mlb-advanced-stats-ghost-flag"
              >
                {lang === 'es' ? 'Ghost-edge' : 'Ghost-edge'}
              </span>
            )}
            {hasManualOdds && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-amber-500/10 text-amber-200 border-amber-500/30"
                data-testid="mlb-advanced-stats-manual-odds-flag"
              >
                {lang === 'es' ? 'Cuota manual' : 'Manual odds'}
              </span>
            )}
            {hasPattern && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full border bg-violet-500/10 text-violet-200 border-violet-500/30"
                data-testid="mlb-advanced-stats-pattern-flag"
              >
                {lang === 'es' ? 'Patrón' : 'Pattern'}
              </span>
            )}
          </div>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
      </button>
      {open && (
        <div className="px-4 pb-4 pt-2 space-y-3" data-testid="mlb-advanced-stats-content">
          {/* Pressure base + Audit summary row */}
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-2" data-testid="mlb-advanced-stats-pressure">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="w-3 h-3 text-rose-300" />
                <span className="text-[10px] uppercase tracking-wide text-slate-400">
                  {lang === 'es' ? 'Presión ofensiva' : 'Offensive pressure'}
                </span>
              </div>
              <div className="text-slate-100 font-medium" data-testid="mlb-advanced-stats-pressure-tier">
                {pressure?.pressure_tier || '—'}
              </div>
              {pressure?.inputs ? (
                <div className="text-[10px] text-slate-400 mt-0.5">
                  hits L5 = {fmt(pressure.inputs.hits_l5_combined)} · runs L5 = {fmt(pressure.inputs.runs_l5_combined)}
                </div>
              ) : null}
            </div>
            <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 p-2" data-testid="mlb-advanced-stats-audit">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                {lang === 'es' ? 'Ajustes Statcast / Sabermetría' : 'Statcast / Saber adjustments'}
              </div>
              <div className="text-[11px] text-slate-100">
                <span data-testid="mlb-advanced-stats-statcast-delta">
                  Statcast Δ: <strong>{fmt(audit?.weighted_conf_delta)}</strong> ({audit?.data_quality || '—'})
                </span>
              </div>
              <div className="text-[11px] text-slate-100">
                <span data-testid="mlb-advanced-stats-saber-delta">
                  Sabermetría Δ: <strong>{fmt(saberAudit?.sabermetrics_weighted_adjustment)}</strong> ({saberAudit?.sabermetrics_data_quality || '—'})
                </span>
              </div>
            </div>
          </div>

          {/* Market Selection summary */}
          <MarketSelectionBlock selection={selection} lang={lang} />

          {/* Manual Odds Review (high priority — show first when active) */}
          {hasManualOdds && <ManualOddsReviewBlock review={manualOdds} lang={lang} />}

          {/* Ghost Edges (high priority warnings) */}
          {hasGhost && <GhostEdgesBlock ghost={ghostEdges} lang={lang} />}

          {/* Script Survival + Fragility audit */}
          {hasFragSurv && (
            <FragilitySurvivalBlock
              fragility={fragilityAudit}
              survival={survivalAudit}
              lang={lang}
            />
          )}

          {/* Statcast blocks (pitchers + teams) */}
          {hasStatcast && (
            <div className="space-y-2" data-testid="mlb-advanced-stats-statcast-section">
              <div className="text-xs font-semibold text-slate-200 flex items-center gap-2">
                <Activity className="w-3.5 h-3.5 text-emerald-300" />
                Statcast
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <PitcherStatcastBlock
                  block={snap?.home_pitcher_advanced}
                  label={lang === 'es' ? 'Abridor local' : 'Home pitcher'}
                  testId="mlb-statcast-home-pitcher"
                />
                <PitcherStatcastBlock
                  block={snap?.away_pitcher_advanced}
                  label={lang === 'es' ? 'Abridor visitante' : 'Away pitcher'}
                  testId="mlb-statcast-away-pitcher"
                />
                <TeamStatcastBlock
                  block={snap?.home_team_advanced}
                  label={lang === 'es' ? 'Ofensiva local' : 'Home offense'}
                  testId="mlb-statcast-home-team"
                />
                <TeamStatcastBlock
                  block={snap?.away_team_advanced}
                  label={lang === 'es' ? 'Ofensiva visitante' : 'Away offense'}
                  testId="mlb-statcast-away-team"
                />
              </div>
            </div>
          )}

          {/* Sabermetrics block */}
          {hasSaber && <SabermetricsBlock saber={saber} lang={lang} />}

          {/* Pattern Memory (historical Moneyball warehouse) */}
          {hasPattern && <PatternMemoryBlock pattern={patternAudit} lang={lang} />}
        </div>
      )}
    </div>
  );
}

export default MLBAdvancedStatsPanel;
