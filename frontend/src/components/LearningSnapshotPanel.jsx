/**
 * LearningSnapshotPanel
 * =====================
 * Sprint-B · B4 — UI panel that renders the learning snapshot stored
 * in the new ``football_match_learning_snapshots`` collection.
 *
 * Shows the user (in order):
 *   1. **Pre-match inputs** — xG L5/L15, corners L1/L5/L15, market
 *      odds. Highlights missing fields.
 *   2. **Sources used** — cascade audit (TheStatsAPI / API-Sports /
 *      Scrape.do) with status per source.
 *   3. **Post-match outcome** — score, hits, real corners.
 *   4. **Markets evaluated** — quick "would the engine have hit this?"
 *      summary derived from the snapshot.
 *
 * Design constraints honoured:
 *   * Shadcn UI components (Card-style layouts, Badges via custom).
 *   * lucide-react icons only — no emojis.
 *   * Card-based layout with token-based colours.
 *   * Fail-soft: returns ``null`` when snapshot is unavailable.
 *   * data-testid on every interactive / displayed value.
 */

import React from 'react';
import {
  Activity, AlertCircle, CheckCircle2, CircleSlash,
  Database, Flag, History, TrendingUp,
} from 'lucide-react';

const STATUS_TONE = {
  COMPLETE: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  PARTIAL:  'border-amber-500/40   bg-amber-500/10   text-amber-200',
  FAILED:   'border-rose-500/40    bg-rose-500/10    text-rose-200',
  PENDING:  'border-slate-500/40   bg-slate-500/10   text-slate-200',
};

function fmt(v, ndigits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(ndigits);
}

function StatusBadge({ status, testId }) {
  const tone = STATUS_TONE[String(status || 'PENDING').toUpperCase()] || STATUS_TONE.PENDING;
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border tabular-nums ${tone}`}
    >
      {status}
    </span>
  );
}

function MetricRow({ label, value, testId, suffix = '' }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[11.5px] leading-snug" data-testid={testId}>
      <span className="text-slate-400">{label}</span>
      <span className="text-slate-100 font-medium tabular-nums">
        {value === null || value === undefined ? '—' : `${value}${suffix}`}
      </span>
    </div>
  );
}

function MissingChips({ items, testId }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1" data-testid={testId}>
      {items.map((k) => (
        <span
          key={k}
          className="px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/[0.08] text-amber-200 text-[9.5px] font-mono"
        >
          {k}
        </span>
      ))}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 1. Pre-match inputs sub-panel
// ──────────────────────────────────────────────────────────────────
function PreMatchInputsSection({ pre, summary, testId }) {
  if (!pre) return null;
  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-3 space-y-2" data-testid={`${testId}-pre-inputs`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-300 font-semibold">
          <Activity className="w-3.5 h-3.5 text-cyan-300" />
          Inputs pre-partido
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-400 tabular-nums" data-testid={`${testId}-pre-progress`}>
            {summary?.pre_match_fields_filled ?? '—'}/{summary?.pre_match_fields_total ?? '—'}
          </span>
          <StatusBadge
            status={summary?.pre_match_complete ? 'COMPLETE' : 'PARTIAL'}
            testId={`${testId}-pre-status`}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
        <MetricRow label="xG home L5"     value={fmt(pre.home_xg_l5)}    testId={`${testId}-home-xg-l5`} />
        <MetricRow label="xG away L5"     value={fmt(pre.away_xg_l5)}    testId={`${testId}-away-xg-l5`} />
        <MetricRow label="xG home L15"    value={fmt(pre.home_xg_l15)}   testId={`${testId}-home-xg-l15`} />
        <MetricRow label="xG away L15"    value={fmt(pre.away_xg_l15)}   testId={`${testId}-away-xg-l15`} />
        <MetricRow label="Corners home L5"  value={fmt(pre.home_corners_l5, 1)} testId={`${testId}-home-corners-l5`} />
        <MetricRow label="Corners away L5"  value={fmt(pre.away_corners_l5, 1)} testId={`${testId}-away-corners-l5`} />
        <MetricRow label="Corners home L15" value={fmt(pre.home_corners_l15, 1)} testId={`${testId}-home-corners-l15`} />
        <MetricRow label="Corners away L15" value={fmt(pre.away_corners_l15, 1)} testId={`${testId}-away-corners-l15`} />
        <MetricRow label="Prob BTTS"      value={fmt(pre.btts_probability, 3)}   testId={`${testId}-btts-prob`} />
        <MetricRow label="Prob Over 2.5"  value={fmt(pre.over25_probability, 3)} testId={`${testId}-over25-prob`} />
        <MetricRow label="Prob Empate"    value={fmt(pre.draw_probability, 3)}   testId={`${testId}-draw-prob`} />
        <MetricRow label="Expected corners" value={fmt(pre.expected_corners, 1)} testId={`${testId}-exp-corners`} />
      </div>

      {pre.market_odds && (
        <div className="pt-2 border-t border-slate-800/60 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
          <MetricRow label="Odd Over 2.5"     value={fmt(pre.market_odds.over25, 2)}         testId={`${testId}-odd-over25`} />
          <MetricRow label="Odd BTTS Sí"      value={fmt(pre.market_odds.btts_yes, 2)}       testId={`${testId}-odd-btts`} />
          <MetricRow label="Odd Empate"       value={fmt(pre.market_odds.draw, 2)}           testId={`${testId}-odd-draw`} />
          <MetricRow label="Odd Over 8.5 c"   value={fmt(pre.market_odds.over85_corners, 2)} testId={`${testId}-odd-corners85`} />
        </div>
      )}

      {summary?.pre_match_missing?.length > 0 && (
        <div className="pt-1">
          <div className="text-[10px] text-amber-300 mb-0.5">Campos faltantes:</div>
          <MissingChips items={summary.pre_match_missing} testId={`${testId}-pre-missing`} />
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 2. Sources used sub-panel
// ──────────────────────────────────────────────────────────────────
function SourcesSection({ audit, testId }) {
  if (!audit) return null;
  const pre = audit.pre_match_sources || [];
  const post = audit.post_match_sources || [];
  if (pre.length === 0 && post.length === 0) return null;

  const renderList = (items, bucket) => (
    items.map((e, i) => (
      <li
        key={`${bucket}-${i}`}
        className="flex items-center justify-between gap-2 text-[11px] py-1 border-b border-slate-800/40 last:border-b-0"
        data-testid={`${testId}-source-${bucket}-${i}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Database className="w-3 h-3 text-slate-400 shrink-0" />
          <span className="text-slate-300 truncate" title={e.source}>{e.source}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-slate-500 tabular-nums">
            {(e.fields_filled || []).length} fields
          </span>
          <StatusBadge status={e.status} testId={`${testId}-source-${bucket}-${i}-status`} />
        </div>
      </li>
    ))
  );

  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-3 space-y-2" data-testid={`${testId}-sources`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-300 font-semibold">
          <Database className="w-3.5 h-3.5 text-cyan-300" />
          Fuentes utilizadas
        </div>
        <StatusBadge status={audit.scrape_status} testId={`${testId}-scrape-status`} />
      </div>
      {pre.length > 0 && (
        <div data-testid={`${testId}-sources-pre`}>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">Pre-match</div>
          <ul>{renderList(pre, 'pre')}</ul>
        </div>
      )}
      {post.length > 0 && (
        <div data-testid={`${testId}-sources-post`}>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5 mt-2">Post-match</div>
          <ul>{renderList(post, 'post')}</ul>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 3. Post-match outcome sub-panel
// ──────────────────────────────────────────────────────────────────
function HitChip({ label, hit, testId }) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border ${
        hit === true
          ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
          : hit === false
            ? 'border-rose-500/40 bg-rose-500/10 text-rose-200'
            : 'border-slate-500/40 bg-slate-500/10 text-slate-300'
      }`}
    >
      {hit === true ? <CheckCircle2 className="w-3 h-3" />
        : hit === false ? <CircleSlash className="w-3 h-3" />
        : <AlertCircle className="w-3 h-3" />}
      {label}
    </span>
  );
}

function PostMatchSection({ post, summary, testId }) {
  if (!post) return null;
  return (
    <div className="rounded-lg border border-slate-700/40 bg-slate-900/40 p-3 space-y-2" data-testid={`${testId}-post`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-slate-300 font-semibold">
          <Flag className="w-3.5 h-3.5 text-amber-300" />
          Resultado post-partido
        </div>
        <StatusBadge
          status={summary?.post_match_settled ? 'COMPLETE' : 'PARTIAL'}
          testId={`${testId}-post-status`}
        />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
        <MetricRow label="Marcador"      value={post.final_score || '—'}        testId={`${testId}-final-score`} />
        <MetricRow label="Goles totales" value={post.total_goals}               testId={`${testId}-total-goals`} />
        <MetricRow label="Goles home"    value={post.home_goals}                testId={`${testId}-home-goals`} />
        <MetricRow label="Goles away"    value={post.away_goals}                testId={`${testId}-away-goals`} />
        <MetricRow label="Corners total" value={post.total_corners}             testId={`${testId}-total-corners`} />
        <MetricRow label="xG real home"  value={fmt(post.real_home_xg)}         testId={`${testId}-real-home-xg`} />
        <MetricRow label="xG real away"  value={fmt(post.real_away_xg)}         testId={`${testId}-real-away-xg`} />
      </div>
      <div className="flex flex-wrap gap-1.5 pt-1.5 border-t border-slate-800/60" data-testid={`${testId}-hit-chips`}>
        <HitChip label="Empate"       hit={post.draw_hit}            testId={`${testId}-chip-draw`} />
        <HitChip label="BTTS Sí"      hit={post.btts_hit}            testId={`${testId}-chip-btts`} />
        <HitChip label="Over 2.5"     hit={post.over25_hit}          testId={`${testId}-chip-over25`} />
        <HitChip label="Over 8.5 cor" hit={post.over85_corners_hit}  testId={`${testId}-chip-corners85`} />
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Top-level panel
// ──────────────────────────────────────────────────────────────────
export function LearningSnapshotPanel({ snapshot, summary, testId = 'learning-snapshot' }) {
  if (!snapshot) return null;
  const pre  = snapshot.pre_match_inputs   || null;
  const post = snapshot.post_match_outputs || null;
  const audit = snapshot.source_audit      || null;

  return (
    <section
      className="rounded-xl border border-slate-700/50 bg-slate-950/60 p-4 space-y-3"
      data-testid={testId}
      aria-label="Football Match Learning Snapshot"
    >
      <header className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 text-[12px] uppercase tracking-wider text-slate-200 font-semibold">
          <History className="w-4 h-4 text-cyan-300" />
          Learning Snapshot
        </div>
        <div className="text-[10px] text-slate-500 font-mono" data-testid={`${testId}-fixture`}>
          {snapshot.home_team} vs {snapshot.away_team}
          {snapshot.competition ? <span className="ml-1.5">· {snapshot.competition}</span> : null}
        </div>
      </header>

      <PreMatchInputsSection pre={pre} summary={summary} testId={testId} />
      <SourcesSection audit={audit} testId={testId} />
      <PostMatchSection post={post} summary={summary} testId={testId} />

      {Array.isArray(snapshot.reason_codes) && snapshot.reason_codes.length > 0 && (
        <div
          className="flex flex-wrap gap-1 pt-1"
          data-testid={`${testId}-reason-codes`}
        >
          {snapshot.reason_codes.map((rc) => (
            <span
              key={rc}
              className="px-1.5 py-0.5 rounded border border-slate-600/40 bg-slate-800/40 text-slate-300 text-[9.5px] font-mono"
            >
              {rc}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

export default LearningSnapshotPanel;
