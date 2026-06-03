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
import { ChevronDown, ChevronUp, Activity, Brain, Layers, Shield } from 'lucide-react';

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

export function MLBAdvancedStatsPanel({ pick, lang = 'es' }) {
  const [open, setOpen] = useState(false);
  const snap = pick?.advanced_stats_snapshot;
  const saber = pick?.sabermetrics;
  const audit = pick?.advanced_adjustments;
  const saberAudit = pick?.sabermetrics_audit;
  const selection = pick?.market_selection;
  const pressure = pick?.pressure_base?.combined;

  // Determine whether ANY block has data — if none, render nothing.
  const hasStatcast = !!(snap && (
    snap.home_pitcher_advanced?.available ||
    snap.away_pitcher_advanced?.available ||
    snap.home_team_advanced?.available ||
    snap.away_team_advanced?.available
  ));
  const hasSaber = !!saber?.available;
  const hasSelection = !!selection?.recommended_market;

  if (!hasStatcast && !hasSaber && !hasSelection) {
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
        </div>
      )}
    </div>
  );
}

export default MLBAdvancedStatsPanel;
