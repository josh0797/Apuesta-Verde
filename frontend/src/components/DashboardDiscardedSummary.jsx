/**
 * F94 — DashboardDiscardedSummary
 *
 * Restores the previously-implemented "discards always visible" behaviour
 * on the Dashboard / Picks del día tab when there are zero recommended
 * picks but the engine DID analyze fixtures.
 *
 * Scope (per F94): football only.
 *
 * Visibility rule:
 *   - Render only when sport === 'football'
 *   - Render only when (high + medium + rescued + protectedAcceptable) === 0
 *   - Render only when there is at least one discarded / incomplete /
 *     skipped / watchlist_odds_needed entry to surface.
 *
 * UI shape (Opción 2 confirmed by user):
 *   - Intro banner with the explicit copy:
 *       "No hay picks recomendados hoy, pero se analizaron X partidos.
 *        Revisa los descartados para ver por qué no pasaron los filtros."
 *   - Collapsed toggle row: "N partidos descartados — ver detalle"
 *   - When expanded: per-fixture row with match, discard_reason,
 *     secondary_reasons, stage, provider/status (all fail-soft).
 *
 * Back-compat: every field is optional. If the legacy payload only has
 * `reason` / `missing`, the component degrades gracefully without throwing.
 */
import { useState, useMemo } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  AlertCircle, ChevronDown, ChevronUp, ShieldOff, Filter,
  Info, ListTree,
} from 'lucide-react';

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

function pickReason(item) {
  // Order matters: prefer the most structured field, fall back to legacy
  // free-text reason / missing. Never throw on null/undefined.
  return (
    item?.discard_reason
    || item?.reason
    || item?.missing
    || null
  );
}

function pickSecondary(item) {
  const sec = item?.secondary_reasons;
  if (Array.isArray(sec)) return sec.filter(Boolean);
  return [];
}

function pickStage(item) {
  return (
    item?.stage
    || item?.pipeline_stage
    || item?.discard_stage
    || null
  );
}

function pickProvider(item) {
  return (
    item?.provider
    || item?.source
    || item?.odds_provider
    || null
  );
}

function pickStatus(item) {
  return (
    item?.status
    || item?.analysis_status
    || item?.value_status
    || null
  );
}

function pickMatchLabel(item) {
  if (item?.match_label) return item.match_label;
  const home = item?.home_team?.name || item?.home_team || item?.teams?.home?.name;
  const away = item?.away_team?.name || item?.away_team || item?.teams?.away?.name;
  if (home && away) return `${home} vs ${away}`;
  return item?.match_id || '—';
}

/* ------------------------------------------------------------------ */
/* DiscardedFixtureRow                                                */
/* ------------------------------------------------------------------ */

function DiscardedFixtureRow({ item, lang, bucketLabel, testId }) {
  const matchLabel = pickMatchLabel(item);
  const reason     = pickReason(item);
  const secondary  = pickSecondary(item);
  const stage      = pickStage(item);
  const provider   = pickProvider(item);
  const status     = pickStatus(item);

  return (
    <div
      className="rounded-md border border-border bg-card/60 px-3 py-2 flex flex-col gap-1.5"
      data-testid={testId}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-200 shrink-0"
          data-testid={`${testId}-bucket`}
        >
          {bucketLabel}
        </span>
        <span
          className="text-sm text-foreground/90 truncate"
          data-testid={`${testId}-match`}
          title={matchLabel}
        >
          {matchLabel}
        </span>
      </div>

      {reason && (
        <div className="text-[12px] text-muted-foreground leading-snug" data-testid={`${testId}-reason`}>
          <span className="text-amber-200/80 font-medium">
            {lang === 'en' ? 'Reason: ' : 'Motivo: '}
          </span>
          {reason}
        </div>
      )}

      {secondary.length > 0 && (
        <div
          className="flex flex-wrap gap-1 text-[10.5px]"
          data-testid={`${testId}-secondary`}
        >
          <span className="text-muted-foreground italic shrink-0">
            {lang === 'en' ? 'Also:' : 'Además:'}
          </span>
          {secondary.map((s, i) => (
            <span
              key={i}
              className="px-1.5 py-0.5 rounded border border-amber-500/25 bg-amber-500/5 text-amber-200/90"
              data-testid={`${testId}-secondary-${i}`}
            >
              {s}
            </span>
          ))}
        </div>
      )}

      {(stage || provider || status) && (
        <div
          className="flex flex-wrap gap-x-3 gap-y-1 text-[10.5px] text-muted-foreground pt-0.5 border-t border-border/40"
          data-testid={`${testId}-meta`}
        >
          {stage && (
            <span data-testid={`${testId}-stage`}>
              <span className="uppercase tracking-wider opacity-70">
                {lang === 'en' ? 'Stage' : 'Etapa'}:
              </span>{' '}
              <span className="font-mono text-foreground/80">{stage}</span>
            </span>
          )}
          {provider && (
            <span data-testid={`${testId}-provider`}>
              <span className="uppercase tracking-wider opacity-70">
                {lang === 'en' ? 'Provider' : 'Proveedor'}:
              </span>{' '}
              <span className="font-mono text-foreground/80">{provider}</span>
            </span>
          )}
          {status && (
            <span data-testid={`${testId}-status`}>
              <span className="uppercase tracking-wider opacity-70">
                {lang === 'en' ? 'Status' : 'Estado'}:
              </span>{' '}
              <span className="font-mono text-foreground/80">{status}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* DashboardDiscardedSummary                                          */
/* ------------------------------------------------------------------ */

export function DashboardDiscardedSummary({
  sport,
  summary,
  recommendedCount,
  discardedMotivation = [],
  discardedMarket     = [],
  incomplete          = [],
  skippedLowRelevance = [],
  watchlistOddsNeeded = [],
  lang = 'es',
  testId = 'dashboard-discarded-summary',
}) {
  // ── Hooks MUST be declared before any early-return ─────────────
  const [open, setOpen] = useState(false);

  // ── Aggregate buckets ───────────────────────────────────────────
  const grouped = useMemo(() => ([
    {
      key: 'motivation',
      label: lang === 'en' ? 'Motivation' : 'Motivación',
      items: Array.isArray(discardedMotivation) ? discardedMotivation : [],
    },
    {
      key: 'market',
      label: lang === 'en' ? 'Market' : 'Mercado',
      items: Array.isArray(discardedMarket) ? discardedMarket : [],
    },
    {
      key: 'incomplete',
      label: lang === 'en' ? 'Incomplete data' : 'Datos incompletos',
      items: Array.isArray(incomplete) ? incomplete : [],
    },
    {
      key: 'odds_needed',
      label: lang === 'en' ? 'Odds needed' : 'Falta cuota',
      items: Array.isArray(watchlistOddsNeeded) ? watchlistOddsNeeded : [],
    },
    {
      key: 'low_relevance',
      label: lang === 'en' ? 'Low relevance' : 'Baja relevancia',
      items: Array.isArray(skippedLowRelevance) ? skippedLowRelevance : [],
    },
  ]), [
    discardedMotivation, discardedMarket, incomplete,
    skippedLowRelevance, watchlistOddsNeeded, lang,
  ]);

  const totalDiscarded = grouped.reduce((acc, g) => acc + g.items.length, 0);

  // ── Scope guards (after hooks per Rules of Hooks) ───────────────
  // F94 → football only.
  if (sport !== 'football') return null;

  // Only kicks in when there is NO recommended pick.
  // (When there are recommendations, the existing detail block keeps
  // working as before.)
  if (Number(recommendedCount || 0) > 0) return null;

  // Don't render anything if there's literally nothing to surface.
  if (totalDiscarded === 0) return null;

  const totalAnalyzed = Number(summary?.total_analyzed ?? totalDiscarded);

  return (
    <div
      className="rounded-xl border border-amber-500/25 bg-amber-500/[0.04] overflow-hidden noise-overlay"
      data-testid={testId}
    >
      {/* ── Intro banner ───────────────────────────────────────── */}
      <div className="px-4 md:px-5 py-3 flex items-start gap-3 border-b border-amber-500/15">
        <div className="h-9 w-9 shrink-0 rounded-lg border border-amber-500/30 bg-amber-500/10 flex items-center justify-center">
          <ShieldOff className="h-4 w-4 text-amber-300" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[10.5px] uppercase tracking-wider text-amber-200/80 mb-0.5">
            {lang === 'en' ? 'Engine audit' : 'Auditoría del motor'}
          </div>
          <p
            className="text-sm text-foreground/90 leading-snug"
            data-testid={`${testId}-banner-text`}
          >
            {lang === 'en'
              ? `No recommended picks today, but ${totalAnalyzed} match${totalAnalyzed === 1 ? '' : 'es'} were analyzed. `
              : `No hay picks recomendados hoy, pero se analizaron ${totalAnalyzed} partido${totalAnalyzed === 1 ? '' : 's'}. `}
            <span className="text-muted-foreground">
              {lang === 'en'
                ? 'Review the discards below to see why they did not pass the filters.'
                : 'Revisa los descartados para ver por qué no pasaron los filtros.'}
            </span>
          </p>
        </div>
      </div>

      {/* ── Collapsed counter / toggle ─────────────────────────── */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full px-4 md:px-5 py-2.5 flex items-center justify-between gap-3 hover:bg-amber-500/[0.06] transition-colors text-left"
        aria-expanded={open}
        aria-controls={`${testId}-content`}
        data-testid={`${testId}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <ListTree className="h-4 w-4 text-amber-200 shrink-0" />
          <span
            className="text-sm font-semibold text-amber-100"
            data-testid={`${testId}-counter`}
          >
            {lang === 'en'
              ? `${totalDiscarded} match${totalDiscarded === 1 ? '' : 'es'} discarded — view detail`
              : `${totalDiscarded} partido${totalDiscarded === 1 ? '' : 's'} descartado${totalDiscarded === 1 ? '' : 's'} — ver detalle`}
          </span>
        </div>
        <span
          className="inline-flex items-center gap-1 text-[11px] text-amber-200/80"
          data-testid={`${testId}-toggle-hint`}
        >
          {open
            ? (lang === 'en' ? 'Hide' : 'Ocultar')
            : (lang === 'en' ? 'Expand' : 'Expandir')}
          {open
            ? <ChevronUp  className="h-4 w-4" />
            : <ChevronDown className="h-4 w-4" />}
        </span>
      </button>

      {/* ── Bucket counters strip ──────────────────────────────── */}
      <div
        className="px-4 md:px-5 pb-2 pt-1 flex flex-wrap gap-1.5"
        data-testid={`${testId}-bucket-strip`}
      >
        {grouped.filter((g) => g.items.length > 0).map((g) => (
          <span
            key={g.key}
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-amber-500/25 bg-amber-500/5 text-amber-200 text-[10.5px] font-medium"
            data-testid={`${testId}-bucket-pill-${g.key}`}
          >
            <Filter className="h-2.5 w-2.5" />
            {g.label}
            <span className="font-mono text-amber-100 ml-0.5">{g.items.length}</span>
          </span>
        ))}
      </div>

      {/* ── Expanded detail ────────────────────────────────────── */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={`${testId}-content`}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden border-t border-amber-500/15"
            data-testid={`${testId}-content-wrapper`}
          >
            <div className="px-4 md:px-5 py-3 flex flex-col gap-3">
              {grouped.filter((g) => g.items.length > 0).map((g) => (
                <div key={g.key} className="flex flex-col gap-1.5">
                  <div
                    className="text-[10.5px] uppercase tracking-wider text-amber-200/80 flex items-center gap-1.5"
                    data-testid={`${testId}-bucket-header-${g.key}`}
                  >
                    <Info className="h-3 w-3" />
                    {g.label}
                    <span className="font-mono text-amber-100">{g.items.length}</span>
                  </div>
                  <div className="grid gap-1.5">
                    {g.items.map((item, i) => (
                      <DiscardedFixtureRow
                        key={`${g.key}-${item?.match_id || item?.fixture_id || i}`}
                        item={item}
                        lang={lang}
                        bucketLabel={g.label}
                        testId={`${testId}-row-${g.key}-${i}`}
                      />
                    ))}
                  </div>
                </div>
              ))}

              {/* Footer hint reinforcing F94 rationale */}
              <div
                className="text-[11px] text-muted-foreground italic pt-2 border-t border-amber-500/10 flex items-start gap-1.5"
                data-testid={`${testId}-footer-hint`}
              >
                <AlertCircle className="h-3 w-3 text-amber-300 shrink-0 mt-0.5" />
                <span>
                  {lang === 'en'
                    ? 'These fixtures were analyzed but did not produce a recommended pick. The engine still surfaces them so you can audit every decision.'
                    : 'Estos partidos se analizaron pero no produjeron un pick recomendado. El motor los expone igual para que puedas auditar cada decisión.'}
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default DashboardDiscardedSummary;
