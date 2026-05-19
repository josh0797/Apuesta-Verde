import { useEffect, useState, useMemo } from 'react';
import { History, TrendingUp, Database, Award, Info } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * HistoricalPatternBadge — surfaces the engine's learned pattern stats for the
 * (market, match_state) combination of the current pick.
 *
 * Hits GET /api/learning/stats?market=…&match_state=…
 * Renders:
 *   - winrate %
 *   - sample size (n)
 *   - reliability tier (high/medium/low based on reliability score)
 *   - engineAgreement
 * Gracefully hides when sample size is too small (≤2) — we don't want users
 * trusting noise.
 *
 * IMPORTANT: This is read-only, observational data — the system is NOT
 * auto-adjusting future picks based on this layer (would create feedback loops).
 */
export function HistoricalPatternBadge({ pick, sport = 'football' }) {
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

  const [stats, setStats] = useState(null);
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
      setStats(pat || null);
    }).catch((e) => {
      if (!cancelled) setErr(e?.message);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [market, matchState, sport]);

  if (loading) {
    return (
      <div className="rounded-lg border border-dashed border-border/60 bg-card/30 p-3 animate-pulse" data-testid="historical-pattern-loading">
        <div className="h-3 w-32 rounded bg-secondary/40 mb-2" />
        <div className="h-2 w-48 rounded bg-secondary/30" />
      </div>
    );
  }

  if (err || !stats || (stats.samples || 0) <= 2) {
    // Not enough sample size to be informative — show a neutral placeholder
    // so users understand the layer exists but hasn't learned this combo yet.
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
              ? `Not enough settled picks for ${market} in ${matchState} yet. The engine will learn over time.`
              : `Aún sin datos suficientes para ${market} en ${matchState}. El motor aprenderá con el tiempo.`}
          </p>
        </div>
      </div>
    );
  }

  const tier = stats.reliability >= 50 ? 'emerald' : stats.reliability >= 25 ? 'amber' : 'slate';
  const winrateTone = (stats.winrate ?? 0) >= 60 ? 'text-emerald-300' : (stats.winrate ?? 0) >= 50 ? 'text-amber-300' : 'text-rose-300';

  // Narrative copy — keeps the Bloomberg-like terminal voice.
  const narrative = lang === 'en'
    ? `Historically: ${market} in ${matchState} has won ${stats.winrate}% of the last ${stats.samples} analyses.`
    : `Históricamente: ${market} en ${matchState} ha ganado ${stats.winrate}% de las últimas ${stats.samples} análisis.`;

  return (
    <div
      data-testid="historical-pattern-badge"
      className={`rounded-lg border tone-${tier} p-3 flex items-start gap-3`}
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
                <TooltipContent className="glass-surface text-xs max-w-[260px] leading-relaxed">
                  {lang === 'en'
                    ? 'Read-only learning layer. Aggregates your tracked outcomes per (market, match-state) bucket. Not used to auto-adjust future picks.'
                    : 'Capa de aprendizaje read-only. Agrega tus resultados tracked por bucket (mercado, estado). No se usa para auto-ajustar picks futuros.'}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
          <span className="text-[10px] font-mono-tabular opacity-70">n={stats.samples}</span>
        </div>

        <p className="text-[12.5px] leading-snug" data-testid="historical-pattern-narrative">
          {narrative}
        </p>

        <div className="grid grid-cols-3 gap-2 pt-1">
          <Metric
            label={lang === 'en' ? 'Win rate' : 'Winrate'}
            value={stats.winrate != null ? `${stats.winrate}%` : '—'}
            valueClassName={winrateTone}
            tooltip={lang === 'en'
              ? 'Decisive wins ÷ (wins + losses). Voids excluded.'
              : 'Victorias decisivas ÷ (victorias + derrotas). Anuladas excluidas.'}
            testId="historical-winrate"
          />
          <Metric
            label={lang === 'en' ? 'Reliability' : 'Confiabilidad'}
            value={`${stats.reliability}`}
            tooltip={lang === 'en'
              ? 'Win rate weighted by sample-size confidence (saturates at 30 samples).'
              : 'Winrate ponderado por confianza muestral (satura a 30 muestras).'}
            testId="historical-reliability"
          />
          <Metric
            label={lang === 'en' ? 'Engine agree' : 'Acuerdo motor'}
            value={`${stats.engine_agreement}`}
            tooltip={lang === 'en'
              ? 'How consistently the engine repeatedly picks this combo and wins.'
              : 'Cuán consistentemente el motor repite este combo con éxito.'}
            testId="historical-engine-agreement"
          />
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, valueClassName, tooltip, testId }) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div data-testid={testId} className="cursor-help">
            <div className="text-[10px] uppercase tracking-wider opacity-70 mb-0.5">{label}</div>
            <div className={`font-mono-tabular text-[13px] font-semibold ${valueClassName || ''}`}>{value}</div>
          </div>
        </TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[260px]">{tooltip}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
