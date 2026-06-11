import { AlertTriangle, Eye, Gauge, Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ConfidenceMeter } from './ConfidenceMeter';
import { tierClass, confidenceTier } from '@/lib/format';
import { useI18n } from '@/lib/i18n';

/**
 * Reusable confidence badge that visually reflects pattern-contradiction
 * penalties applied to the engine's confidence score.
 *
 * Props
 * -----
 *   confidence           number   final (post-penalty) confidence_score
 *   conflictState        string?  "VALUE_CON_CONFLICTO" | "VALUE_REVISAR" | null
 *   penaltyApplied       object?  { supporting, contradicting, ratio, penalty, market_side }
 *   confidencePrePenalty number?  original confidence before the penalty
 *   testId               string?  override for data-testid
 *   size                 string?  "inline" (default) — kept for future variants
 *
 * Behavior
 * --------
 *   - When `conflictState` is null/undefined → delegates to ConfidenceMeter
 *     (tier-based color), no visual change vs. legacy callers.
 *   - VALUE_CON_CONFLICTO (ratio ≥ 0.65, penalty 12 or 18) → AMBER badge,
 *     warning icon, tooltip detailing the contradiction.
 *   - VALUE_REVISAR (ratio ≥ 0.55, penalty 8) → BLUE/CYAN badge, eye icon,
 *     tooltip with softer "review" wording.
 *
 * Used in: MatchDetailPage, MatchCard, HistoricalProfilePanel (eventual).
 */

const CONFLICT_PRESETS = {
  VALUE_CON_CONFLICTO: {
    tone: 'amber',
    Icon: AlertTriangle,
    labelEs: 'VALUE C/CONFLICTO',
    labelEn: 'VALUE w/CONFLICT',
    headlineEs: 'Conflicto fuerte con el patrón histórico',
    headlineEn: 'Strong conflict with historical pattern',
    bodyEs:
      'Más del 65% de los patrones históricos contradicen el lado recomendado. La confianza se redujo automáticamente para reflejar el riesgo real. El pick se mantiene — pero conviene revisarlo a fondo.',
    bodyEn:
      'More than 65% of historical patterns contradict the recommended side. Confidence was automatically reduced to reflect the real risk. The pick stands — but review carefully.',
  },
  VALUE_REVISAR: {
    tone: 'cyan',
    Icon: Eye,
    labelEs: 'VALUE A REVISAR',
    labelEn: 'VALUE — REVIEW',
    headlineEs: 'Conflicto moderado con patrones históricos',
    headlineEn: 'Moderate conflict with historical patterns',
    bodyEs:
      'Hay más patrones en contra que a favor (55–64%). La confianza se ajustó a la baja. Revisar antes de jugar.',
    bodyEn:
      'There are more patterns against than in favor (55–64%). Confidence was adjusted downward. Review before playing.',
  },
};

function formatRatio(r) {
  if (r === null || r === undefined || Number.isNaN(Number(r))) return null;
  return `${Math.round(Number(r) * 100)}%`;
}

export function ConfidenceBadge({
  confidence,
  conflictState,
  penaltyApplied,
  confidencePrePenalty,
  testId = 'confidence-badge',
  size = 'inline',
}) {
  const { lang } = useI18n();
  const score = Math.max(0, Math.min(100, Math.round(Number(confidence) || 0)));
  const tier = confidenceTier(score);

  // No conflict → delegate to the existing meter so non-MLB sports and
  // picks without pattern data keep their original UI.
  if (!conflictState || !CONFLICT_PRESETS[conflictState]) {
    return (
      <ConfidenceMeter
        score={score}
        size={size}
        testId={testId}
      />
    );
  }

  const preset = CONFLICT_PRESETS[conflictState];
  const Icon = preset.Icon;
  const label = lang === 'en' ? preset.labelEn : preset.labelEs;
  const headline = lang === 'en' ? preset.headlineEn : preset.headlineEs;
  const body = lang === 'en' ? preset.bodyEn : preset.bodyEs;

  const supporting = Number(penaltyApplied?.supporting ?? 0);
  const contradicting = Number(penaltyApplied?.contradicting ?? 0);
  const ratioPct = formatRatio(penaltyApplied?.ratio);
  const penalty = Number(penaltyApplied?.penalty ?? 0);
  const prePenalty =
    confidencePrePenalty !== undefined && confidencePrePenalty !== null
      ? Math.round(Number(confidencePrePenalty))
      : null;

  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            data-testid={testId}
            data-conflict-state={conflictState}
            data-tier={tier}
            className={`inline-flex items-center gap-2 rounded-md border tone-${preset.tone} px-2 py-1 cursor-help select-none`}
            role="status"
            aria-label={`${label} — ${score}`}
          >
            <Icon className="h-3.5 w-3.5 shrink-0" />
            <span
              className="font-mono-tabular text-sm font-semibold leading-none"
              data-testid={`${testId}-score`}
            >
              {score}
            </span>
            <span
              className="text-[10px] font-semibold uppercase tracking-wider opacity-90 whitespace-nowrap"
              data-testid={`${testId}-label`}
            >
              {label}
            </span>
            {prePenalty !== null && prePenalty > score && (
              <span
                className="text-[10px] font-mono-tabular line-through opacity-60"
                data-testid={`${testId}-pre-penalty`}
                aria-label={lang === 'en' ? 'Original confidence' : 'Confianza original'}
              >
                {prePenalty}
              </span>
            )}
            <Info className="h-3 w-3 opacity-50 shrink-0" aria-hidden="true" />
          </div>
        </TooltipTrigger>
        <TooltipContent
          className="glass-surface text-xs max-w-[300px] leading-relaxed space-y-2 p-3"
          data-testid={`${testId}-tooltip`}
        >
          <div className="font-semibold text-foreground">{headline}</div>
          <div className="text-muted-foreground">{body}</div>
          {(supporting > 0 || contradicting > 0) && (
            <div
              className="text-[11px] border-t border-border/40 pt-2 font-mono-tabular flex flex-wrap items-center gap-x-3 gap-y-1"
              data-testid={`${testId}-breakdown`}
            >
              <span>
                <Gauge className="inline h-3 w-3 mr-1 opacity-60" aria-hidden="true" />
                {lang === 'en' ? 'Patterns' : 'Patrones'}:{' '}
                <span className="text-emerald-300">{supporting}</span>
                {' / '}
                <span className="text-rose-300">{contradicting}</span>
                {ratioPct && (
                  <span className="opacity-60"> · {ratioPct}</span>
                )}
              </span>
              {penalty > 0 && prePenalty !== null && (
                <span data-testid={`${testId}-delta`}>
                  <span className="opacity-60">{lang === 'en' ? 'Confidence' : 'Confianza'}:</span>{' '}
                  <span>{prePenalty}</span>
                  <span className="opacity-60"> → </span>
                  <span className="font-semibold">{score}</span>
                  <span className={`ml-1 tone-${preset.tone} px-1 py-0.5 rounded`}>
                    -{penalty}
                  </span>
                </span>
              )}
            </div>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export default ConfidenceBadge;
