/**
 * SeriesFamiliarityBadge — Priority 3.
 *
 * Renders the MLB Series Familiarity Score (0-100) on the match card.
 * Surfaces ONLY when the backend produced a score (``available=true``).
 * Observe-only by default: this signal never alters the engine pick,
 * it merely explains WHY late-game traffic risk might be elevated.
 *
 * The data object follows the contract emitted by
 * ``mlb_series_familiarity_score.compute_series_familiarity_score`` and
 * is persisted by the orchestrator at ``match.series_familiarity``.
 */
import { Users2 } from 'lucide-react';

const BUCKET_LABELS = {
  LOW_SERIES_FAMILIARITY:    { es: 'baja',  en: 'low' },
  MEDIUM_SERIES_FAMILIARITY: { es: 'media', en: 'medium' },
  HIGH_SERIES_FAMILIARITY:   { es: 'alta',  en: 'high' },
};

const BUCKET_TONE = {
  LOW_SERIES_FAMILIARITY:    'border-border bg-card/30 text-muted-foreground',
  MEDIUM_SERIES_FAMILIARITY: 'border-cyan-500/30 bg-cyan-500/5 text-cyan-200',
  HIGH_SERIES_FAMILIARITY:   'border-amber-500/40 bg-amber-500/10 text-amber-200',
};

export function SeriesFamiliarityBadge({ data, lang = 'es', testId }) {
  if (!data || !data.available) return null;
  const score = data.series_familiarity_score;
  const bucket = data.bucket || 'LOW_SERIES_FAMILIARITY';
  const tone = BUCKET_TONE[bucket] || BUCKET_TONE.LOW_SERIES_FAMILIARITY;
  const label = (BUCKET_LABELS[bucket] || {})[lang]
              || (BUCKET_LABELS[bucket] || {}).es
              || 'baja';

  // Narrative — only show when the score is meaningful (bucket != LOW).
  const showNarrative = bucket !== 'LOW_SERIES_FAMILIARITY';
  const narrative = lang === 'en'
    ? 'These teams have faced each other repeatedly. Combined with a used bullpen, this raises the chance of a late-game offensive adjustment.'
    : 'Estos equipos se han enfrentado varias veces recientemente; combinado con bullpen usado, aumenta el riesgo de ajuste ofensivo tardío.';

  const counts = data.counts || {};

  return (
    <section
      className={`rounded-md border ${tone} px-2.5 py-1.5 space-y-1`}
      data-testid={testId || 'series-familiarity-badge'}
      data-bucket={bucket}
    >
      <div className="flex items-center gap-2 text-[10.5px]">
        <Users2 className="h-3 w-3" />
        <span className="font-semibold">
          {lang === 'en' ? 'Series familiarity' : 'Familiaridad de serie'}: {label}
        </span>
        <span className="font-mono opacity-80 ml-auto" data-testid="series-familiarity-score">
          {score != null ? `${Number(score).toFixed(0)}/100` : '—'}
        </span>
      </div>
      {showNarrative && (
        <p className="text-[10px] leading-snug opacity-90" data-testid="series-familiarity-narrative">
          {narrative}
        </p>
      )}
      {(counts.same_teams_last_3_days != null || counts.same_teams_last_15_days != null) && (
        <div className="text-[9.5px] opacity-70 font-mono flex gap-3" data-testid="series-familiarity-counts">
          <span>3d: {counts.same_teams_last_3_days ?? 0}</span>
          <span>5d: {counts.same_teams_last_5_days ?? 0}</span>
          <span>15d: {counts.same_teams_last_15_days ?? 0}</span>
        </div>
      )}
    </section>
  );
}

export default SeriesFamiliarityBadge;
