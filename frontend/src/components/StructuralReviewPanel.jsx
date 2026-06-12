/**
 * StructuralReviewPanel — Phase F64
 *
 * Renders the football structural review block attached to a discarded /
 * watchlist-odds-needed match. Surfaces:
 *   - goal_profile_cross.profile
 *   - corner_profile_cross.profile
 *   - under_support.score / over_support.score
 *   - max_structural_support
 *   - final_state badge
 *   - market_candidates (top 3)
 *   - narrative_es
 *
 * Designed to render below the existing rejection reason in DiscardedRow.
 */
import React from 'react';
import { Activity, Target, Goal, AlertTriangle } from 'lucide-react';

const STATE_LABELS_ES = {
  VALUE_CANDIDATE:                      { label: 'Value candidate', cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200' },
  WATCHLIST_ODDS_NEEDED:                { label: 'Watchlist por cuota', cls: 'border-amber-500/40 bg-amber-500/10 text-amber-200' },
  STRUCTURAL_VALUE_BUT_ODDS_NOT_READY:  { label: 'Estructura sin cuota', cls: 'border-amber-500/40 bg-amber-500/10 text-amber-200' },
  MOVE_TO_WATCHLIST:                    { label: 'Watchlist', cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200' },
  NO_STRUCTURAL_VALUE:                  { label: 'Sin valor estructural', cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300' },
};

const STATE_LABELS_EN = {
  VALUE_CANDIDATE:                      { label: 'Value candidate' },
  WATCHLIST_ODDS_NEEDED:                { label: 'Watchlist — odds needed' },
  STRUCTURAL_VALUE_BUT_ODDS_NOT_READY:  { label: 'Structural value, odds not ready' },
  MOVE_TO_WATCHLIST:                    { label: 'Watchlist' },
  NO_STRUCTURAL_VALUE:                  { label: 'No structural value' },
};

export function StructuralReviewPanel({ review, lang = 'es', testIdPrefix = 'structural-review' }) {
  if (!review || review.available === false) return null;

  const tone = STATE_LABELS_ES[review.final_state] || STATE_LABELS_ES.NO_STRUCTURAL_VALUE;
  const label = (lang === 'en' ? STATE_LABELS_EN : STATE_LABELS_ES)[review.final_state]?.label
                || review.final_state;

  const corner = review.corner_profile_cross || {};
  const goal   = review.goal_profile_cross   || {};
  const under  = review.under_support        || {};
  const over   = review.over_support         || {};
  const candidates = Array.isArray(review.market_candidates) ? review.market_candidates : [];

  return (
    <div
      className="mt-2 rounded-md border border-cyan-500/15 bg-cyan-500/5 px-2.5 py-2 space-y-1.5"
      data-testid={`${testIdPrefix}-panel`}
    >
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide font-semibold text-cyan-200">
        <Activity className="h-3.5 w-3.5" />
        <span>{lang === 'en' ? 'Structural analysis' : 'Análisis estructural'}</span>
        <span
          className={`ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-medium ${tone.cls}`}
          data-testid={`${testIdPrefix}-state`}
        >
          {label}
        </span>
      </div>

      {/* Score grid: Under / Over / Max */}
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <ScoreCell
          icon={<Goal className="h-3 w-3" />}
          label={lang === 'en' ? 'Under support' : 'Under'}
          value={under.available ? `${under.score}/100` : '—'}
          testId={`${testIdPrefix}-under-score`}
        />
        <ScoreCell
          icon={<Target className="h-3 w-3" />}
          label={lang === 'en' ? 'Over support' : 'Over'}
          value={over.available ? `${over.score}/100` : '—'}
          testId={`${testIdPrefix}-over-score`}
        />
        <ScoreCell
          icon={<AlertTriangle className="h-3 w-3" />}
          label={lang === 'en' ? 'Max support' : 'Soporte máx.'}
          value={`${review.max_structural_support ?? 0}/100`}
          testId={`${testIdPrefix}-max-support`}
          highlight={review.max_structural_support >= 75}
        />
      </div>

      {/* Profile labels */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-1 text-[11px] text-muted-foreground">
        {goal.available && (
          <div data-testid={`${testIdPrefix}-goal-profile`}>
            <span className="text-foreground/70">{lang === 'en' ? 'Goal profile: ' : 'Perfil goles: '}</span>
            <span className="font-medium text-foreground/90">{goal.profile || '—'}</span>
          </div>
        )}
        {corner.available && (
          <div data-testid={`${testIdPrefix}-corner-profile`}>
            <span className="text-foreground/70">{lang === 'en' ? 'Corner profile: ' : 'Perfil córners: '}</span>
            <span className="font-medium text-foreground/90">{corner.profile || '—'}</span>
          </div>
        )}
      </div>

      {/* Top candidates */}
      {candidates.length > 0 && (
        <div className="space-y-1" data-testid={`${testIdPrefix}-candidates`}>
          <div className="text-[10px] uppercase tracking-wide font-semibold text-cyan-200/70">
            {lang === 'en' ? 'Top candidates' : 'Mejores candidatos'}
          </div>
          <div className="space-y-1">
            {candidates.slice(0, 3).map((c, i) => (
              <div
                key={`${c.market}-${i}`}
                className="flex items-center justify-between text-[11px] rounded border border-border/60 px-2 py-1 bg-background/40"
                data-testid={`${testIdPrefix}-candidate-${i}`}
              >
                <span className="text-foreground/90 truncate min-w-0">
                  {c.market} <span className="text-muted-foreground">— {c.family}</span>
                </span>
                <span className="font-semibold text-cyan-200 shrink-0 ml-2">
                  {c.structural_support}/100
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Narrative */}
      {review.narrative_es && (
        <div className="text-[11px] text-foreground/80 italic pt-1 border-t border-border/40">
          {review.narrative_es}
        </div>
      )}
    </div>
  );
}

function ScoreCell({ icon, label, value, testId, highlight = false }) {
  return (
    <div
      className={`flex items-center gap-1.5 rounded border px-2 py-1 ${
        highlight ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200' : 'border-border bg-background/40 text-foreground/80'
      }`}
      data-testid={testId}
    >
      <span className="opacity-70">{icon}</span>
      <span className="flex-1 truncate text-[10px] uppercase tracking-wide opacity-70">{label}</span>
      <span className="font-semibold text-[11px]">{value}</span>
    </div>
  );
}

export default StructuralReviewPanel;
