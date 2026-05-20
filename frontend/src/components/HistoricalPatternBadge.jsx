import { useEffect, useState, useMemo } from 'react';
import { History, Database, Info, ShieldQuestion, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { deriveHistoricalSignal } from '@/lib/intelligence';

/**
 * HistoricalPatternBadge — Historical Learning v2
 *
 * Surfaces the engine's learned pattern stats for the (market, match_state)
 * combination of the current pick AND a decision-support signal derived from
 * those stats (pattern_strength, confidence_modifier_suggestion, etc.).
 *
 * Hits GET /api/learning/stats?market=…&match_state=…
 *
 * Decision philosophy:
 *   - READ-ONLY: the suggested confidence modifier is shown to the user but
 *     NEVER auto-applied. The LLM's confidence_score stays the source of truth.
 *   - LOW-SAMPLE WARNING: when sample size is ≤ 4 we surface a clear caveat to
 *     prevent over-trust on statistical noise.
 *   - EXPLAINABLE: every number has a tooltip with its formula.
 *
 * Optional callback:
 *   onSignal(signal) — emits the derived signal upward so the parent panel
 *   can pass it to ContradictionWarnings / EngineNarrativeBlock without
 *   re-fetching.
 */
export function HistoricalPatternBadge({ pick, sport = 'football', onSignal }) {
  const { lang } = useI18n();
  const market = pick?.recommendation?.market;
  const matchState = useMemo(() => {
    if (pick?.match_state) return pick.match_state;
    const motH = pick?.motivation?.home?.level ?? 3;
    const motA = pick?.motivation?.away?.level ?? 3;
    if (motH >= 4 && motA >= 4) return 'HIGH_MOTIVATION';
    if (motH <= 2 && motA <= 2) return 'LOW_URGENCY';
    return 'CONTROLLED_MATCH';
  }, [pick]);

  const [stat, setStat] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!market || !matchState) { setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    api.get('/learning/stats', {
      params: { sport, market, match_state: matchState },
    }).then((r) => {
      if (cancelled) return;
      const pat = (r.data?.patterns || []).find((p) =>
        (p.market || '').toLowerCase() === (market || '').toLowerCase() &&
        p.match_state === matchState
      );
      setStat(pat || null);
    }).catch((e) => {
      if (!cancelled) setErr(e?.message);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [market, matchState, sport]);

  // Derive the v2 signal (always run — even with null stats so the caller
  // receives a 'no-data' signal it can use for contradiction detection).
  const signal = useMemo(() => deriveHistoricalSignal(stat, pick), [stat, pick]);

  // Notify parent so it can wire ContradictionWarnings / EngineNarrative.
  useEffect(() => { if (onSignal) onSignal(signal); }, [signal, onSignal]);

  if (loading) {
    return (
      <div className="rounded-lg border border-dashed border-border/60 bg-card/30 p-3 animate-pulse" data-testid="historical-pattern-loading">
        <div className="h-3 w-32 rounded bg-secondary/40 mb-2" />
        <div className="h-2 w-48 rounded bg-secondary/30" />
      </div>
    );
  }

  // Empty / very-low-sample state — neutral placeholder
  if (err || !stat || signal.status === 'no-data' || signal.status === 'low-sample') {
    return (
      <div
        className="rounded-lg border border-dashed border-border/60 bg-card/30 p-3 flex items-start gap-2.5"
        data-testid="historical-pattern-empty"
      >
        <Database className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-0.5">
            {lang === 'en' ? 'Historical pattern' : 'Patrón histórico'}
          </div>
          <p className="text-[11.5px] text-muted-foreground leading-snug">
            {lang === 'en'
              ? `Not enough settled picks for ${market} in ${matchState} yet (n=${signal.sample_size}). The engine will learn over time.`
              : `Aún sin datos suficientes para ${market} en ${matchState} (n=${signal.sample_size}). El motor aprenderá con el tiempo.`}
          </p>
        </div>
      </div>
    );
  }

  // Tier visualization
  const statusTone = {
    weak: 'slate',
    neutral: 'cyan',
    strong: 'emerald',
    elite: 'emerald',
  }[signal.status] || 'slate';

  const winrateTone = (signal.winrate ?? 0) >= 60
    ? 'text-emerald-300'
    : (signal.winrate ?? 0) >= 50
      ? 'text-amber-300'
      : 'text-rose-300';

  const ModifierIcon = signal.confidence_modifier_suggestion > 0
    ? TrendingUp
    : signal.confidence_modifier_suggestion < 0
      ? TrendingDown
      : Minus;
  const modifierTone = signal.confidence_modifier_suggestion > 0
    ? 'text-emerald-300'
    : signal.confidence_modifier_suggestion < 0
      ? 'text-rose-300'
      : 'text-muted-foreground';

  // Narrative copy — keeps the Bloomberg-like terminal voice.
  const narrative = lang === 'en'
    ? `Historically: ${market} in ${matchState} has won ${signal.winrate}% of the last ${signal.sample_size} settled picks.`
    : `Históricamente: ${market} en ${matchState} ha ganado ${signal.winrate}% de los últimos ${signal.sample_size} picks resueltos.`;

  return (
    <div
      data-testid="historical-pattern-badge"
      data-signal-status={signal.status}
      className={`rounded-lg border tone-${statusTone} p-3 flex items-start gap-3`}
    >
      <History className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[11px] uppercase tracking-wider opacity-80 flex items-center gap-1.5">
            {lang === 'en' ? 'Historical pattern' : 'Patrón histórico'}
            <TooltipProvider delayDuration={120}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Info className="h-3 w-3 opacity-70 cursor-help" />
                </TooltipTrigger>
                <TooltipContent className="glass-surface text-xs max-w-[280px] leading-relaxed">
                  {lang === 'en'
                    ? 'Read-only learning v2. Aggregates your tracked outcomes per (market, match-state) bucket and derives a pattern strength + confidence modifier SUGGESTION. The system never auto-applies it.'
                    : 'Capa de aprendizaje v2 read-only. Agrega tus resultados tracked por bucket (mercado, estado) y deriva una fuerza de patrón + SUGERENCIA de modificador de confianza. El sistema no la aplica automáticamente.'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <span
            className={`text-[10px] font-mono-tabular px-1.5 py-0.5 rounded bg-background/40 uppercase tracking-wider`}
            data-testid="historical-pattern-status"
          >
            {lang === 'en' ? signal.trust_label_en : signal.trust_label_es}
          </span>
        </div>

        <p className="text-[12.5px] leading-snug" data-testid="historical-pattern-narrative">
          {narrative}
        </p>

        {/* Metric row 1: winrate / reliability / engine agreement */}
        <div className="grid grid-cols-3 gap-2 pt-1">
          <Metric
            label={lang === 'en' ? 'Win rate' : 'Winrate'}
            value={signal.winrate != null ? `${signal.winrate}%` : '—'}
            valueClassName={winrateTone}
            tooltip={lang === 'en'
              ? 'Decisive wins ÷ (wins + losses). Voids excluded.'
              : 'Victorias decisivas ÷ (victorias + derrotas). Anuladas excluidas.'}
            testId="historical-winrate"
          />
          <Metric
            label={lang === 'en' ? 'Reliability' : 'Confiabilidad'}
            value={`${signal.reliability}`}
            tooltip={lang === 'en'
              ? 'Win rate weighted by sample-size confidence (saturates at 30 samples).'
              : 'Winrate ponderado por confianza muestral (satura a 30 muestras).'}
            testId="historical-reliability"
          />
          <Metric
            label={lang === 'en' ? 'Engine agree' : 'Acuerdo motor'}
            value={`${signal.engine_agreement}`}
            tooltip={lang === 'en'
              ? 'How consistently the engine repeatedly picks this combo and wins.'
              : 'Cuán consistentemente el motor repite este combo con éxito.'}
            testId="historical-engine-agreement"
          />
        </div>

        {/* Metric row 2: pattern_strength + confidence_modifier_suggestion + n */}
        <div className="grid grid-cols-3 gap-2 pt-1 border-t border-current/15 mt-1.5">
          <Metric
            label={lang === 'en' ? 'Pattern strength' : 'Fuerza patrón'}
            value={`${signal.pattern_strength}`}
            tooltip={lang === 'en'
              ? 'Composite: 50% reliability + 30% engine agreement + 20% sample coverage (cap 30).'
              : 'Compuesto: 50% confiabilidad + 30% acuerdo motor + 20% cobertura muestral (cap 30).'}
            testId="historical-pattern-strength"
          />
          <Metric
            label={lang === 'en' ? 'Conf. nudge' : 'Sugerencia'}
            value={`${signal.confidence_modifier_suggestion > 0 ? '+' : ''}${signal.confidence_modifier_suggestion}`}
            valueClassName={modifierTone}
            icon={ModifierIcon}
            tooltip={lang === 'en'
              ? 'Display-only suggestion (range ±10). The LLM confidence_score is NEVER modified automatically — the user keeps full authority.'
              : 'Sugerencia visual (rango ±10). El confidence_score del LLM NO se modifica automáticamente — el usuario mantiene la autoridad final.'}
            testId="historical-confidence-modifier"
          />
          <Metric
            label={lang === 'en' ? 'Sample' : 'Muestra'}
            value={`n=${signal.sample_size}`}
            tooltip={lang === 'en'
              ? 'Number of settled tracked picks contributing to this pattern.'
              : 'Número de picks resueltos contribuyendo a este patrón.'}
            testId="historical-sample-size"
          />
        </div>

        {signal.warning_if_low_sample && (
          <div
            className="mt-1.5 flex items-start gap-1.5 text-[10.5px] text-amber-300/90"
            data-testid="historical-low-sample-warning"
          >
            <ShieldQuestion className="h-3 w-3 mt-0.5 shrink-0" />
            <span>
              {lang === 'en'
                ? `Small sample (n=${signal.sample_size}) — treat as observational, not conclusive.`
                : `Muestra pequeña (n=${signal.sample_size}) — tratar como observacional, no concluyente.`}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value, valueClassName, tooltip, testId, icon: Icon }) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div data-testid={testId} className="cursor-help">
            <div className="text-[10px] uppercase tracking-wider opacity-70 mb-0.5">{label}</div>
            <div className={`font-mono-tabular text-[13px] font-semibold inline-flex items-center gap-1 ${valueClassName || ''}`}>
              {Icon && <Icon className="h-3 w-3" />}
              {value}
            </div>
          </div>
        </TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[280px] leading-relaxed">{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
