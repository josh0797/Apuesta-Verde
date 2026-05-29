import { useState } from 'react';
import { ChevronDown, AlertCircle, Activity, Target, Gauge } from 'lucide-react';

/**
 * ManualOddsReviewPanel — renders MLB games that the v2 engine identified as
 * having a structural lean but for which automatic odds were not available.
 *
 * These games come from the new `summary.structural_lean_requires_odds`
 * bucket (and optionally `summary.watchlist_manual_odds`) populated by the
 * MLB-V5 orchestrator. Critically they are NOT routed to "Descartados por
 * mercado frágil" anymore — instead the user gets a clear "Revisión
 * manual — falta cuota" card with suggested markets and a placeholder for
 * a future "Pegar cuota manual" interaction.
 *
 * Constraints:
 *  - Baseball-only — caller (DashboardPage) must skip rendering for other sports.
 *  - Renders nothing when the bucket is empty.
 *  - Does NOT touch the existing "Descartados por mercado frágil" rendering
 *    for football/basketball.
 */

const TONE_BY_CLASSIFICATION = {
  STRUCTURAL_LEAN:          { tone: 'cyan',   label: 'Revisión manual — falta cuota' },
  WATCHLIST_MANUAL_REVIEW:  { tone: 'amber',  label: 'Watchlist — cuota manual' },
};

function asNumber(v) {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function ManualReviewRow({ item, idx, testId }) {
  const [expanded, setExpanded] = useState(false);
  const cls = item.classification || 'STRUCTURAL_LEAN';
  const meta = TONE_BY_CLASSIFICATION[cls] || TONE_BY_CLASSIFICATION.STRUCTURAL_LEAN;
  const v2 = item.mlb_script_v2 || {};
  const sq = item.structural_quality || {};

  const marginNum = asNumber(v2.marginProjection);
  const coverNum  = asNumber(v2.coverProbability);
  const erNum     = asNumber(v2.expectedRuns);

  return (
    <div
      className={`rounded-lg border border-${meta.tone}-500/25 bg-${meta.tone}-500/[0.04] overflow-hidden`}
      data-testid={testId || `manual-review-row-${idx}`}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-foreground/[0.02] transition-colors"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid={`manual-review-row-${idx}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <AlertCircle className={`h-4 w-4 shrink-0 text-${meta.tone}-300/80`} />
          <span className="font-medium text-sm truncate">{item.match_label}</span>
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border border-${meta.tone}-500/40 bg-${meta.tone}-500/15 text-${meta.tone}-200`}>
            {meta.label}
          </span>
          {sq.level ? (
            <span className="text-[10px] text-muted-foreground/80">
              calidad estructural · {sq.level} ({sq.score}/100)
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {marginNum !== null ? (
            <span className="text-[10px] tabular-nums text-muted-foreground">
              marg {marginNum >= 0 ? '+' : ''}{marginNum.toFixed(2)}
            </span>
          ) : null}
          {coverNum !== null ? (
            <span className="text-[10px] tabular-nums text-muted-foreground">
              cover {coverNum.toFixed(0)}%
            </span>
          ) : null}
          <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-border/30 pt-3" data-testid={`manual-review-row-${idx}-body`}>
          {/* Reason */}
          {item.reason ? (
            <p className="text-[12px] text-muted-foreground leading-relaxed">
              {item.reason}
            </p>
          ) : null}

          {/* v2 script metrics */}
          {(marginNum !== null || coverNum !== null || erNum !== null || v2.recommendedLine) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px]">
              {marginNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Activity className="h-3 w-3" /> Projected margin</div>
                  <div className="font-medium tabular-nums mt-0.5">{marginNum >= 0 ? '+' : ''}{marginNum.toFixed(2)} carreras</div>
                </div>
              ) : null}
              {coverNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Target className="h-3 w-3" /> Cover probability</div>
                  <div className="font-medium tabular-nums mt-0.5">{coverNum.toFixed(1)}%</div>
                </div>
              ) : null}
              {erNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Gauge className="h-3 w-3" /> Expected runs</div>
                  <div className="font-medium tabular-nums mt-0.5">{erNum.toFixed(1)}</div>
                </div>
              ) : null}
              {v2.recommendedLine ? (
                <div className="rounded-md border border-emerald-500/20 px-2 py-1.5 bg-emerald-500/[0.05]">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">Línea recomendada</div>
                  <div className="font-medium mt-0.5 text-emerald-200">{v2.recommendedLine}</div>
                </div>
              ) : null}
            </div>
          )}

          {/* Suggested markets */}
          {(item.suggested_markets || []).length ? (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80 mb-1.5">
                Mercados a revisar manualmente
              </div>
              <div className="flex flex-wrap gap-1.5">
                {item.suggested_markets.map((m, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] border border-border bg-background/60"
                    data-testid={`manual-review-row-${idx}-market-${i}`}
                  >
                    {m}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {/* Manual odds paste affordance (placeholder — no edge calc yet) */}
          <div className="flex items-center justify-between rounded-md border border-dashed border-border/60 bg-background/30 px-3 py-2">
            <div className="text-[11px] text-muted-foreground">
              Pega tu cuota para calcular edge (próximo release).
            </div>
            <input
              type="number"
              step="0.01"
              placeholder="1.90"
              className="w-20 text-[11px] tabular-nums bg-background border border-border rounded px-2 py-1 disabled:opacity-50"
              disabled
              data-testid={`manual-review-row-${idx}-odds-input`}
            />
          </div>

          {/* Structural quality reasons */}
          {(sq.reasons || []).length ? (
            <details className="text-[10px] text-muted-foreground/80">
              <summary className="cursor-pointer hover:text-muted-foreground">¿Por qué entró aquí?</summary>
              <ul className="mt-1.5 space-y-0.5 pl-4 list-disc">
                {sq.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function ManualOddsReviewPanel({ items, lang = 'es', testId }) {
  if (!items || items.length === 0) return null;
  const headline = lang === 'en'
    ? `Manual review — odds missing (${items.length})`
    : `Revisión manual — falta cuota (${items.length})`;
  const subcopy = lang === 'en'
    ? 'No automatic odds for these games. The MLB engine found possible markets — paste your bookmaker odds to compute edge.'
    : 'Sin cuota automática. El análisis estructural encontró posibles mercados para revisar con tu bookie.';

  return (
    <div className="space-y-3" data-testid={testId || 'manual-odds-review-section'}>
      <div className="rounded-lg border border-cyan-500/25 bg-cyan-500/[0.05] px-3 py-2 text-xs uppercase tracking-wide font-semibold text-cyan-200 flex items-center gap-2">
        <AlertCircle className="h-3.5 w-3.5" />
        {headline}
      </div>
      <p className="text-xs text-muted-foreground">{subcopy}</p>
      <div className="grid gap-2">
        {items.map((it, i) => (
          <ManualReviewRow key={(it.match_id || '') + '-' + i} item={it} idx={i} />
        ))}
      </div>
    </div>
  );
}

export default ManualOddsReviewPanel;
