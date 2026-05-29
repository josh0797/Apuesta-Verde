import { useState, useMemo } from 'react';
import {
  Database, ChevronDown, ChevronUp, ExternalLink, Check, X, Clock,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';

/**
 * Renders the `external_source_evidence` array attached to a match
 * (picks or discarded rows) — collected by
 * /app/backend/services/external_sources/dispatcher.py.
 *
 * Props
 * -----
 *   evidence      : Array<EvidenceItem> (see schema.py)
 *   testId        : data-testid base
 *   defaultOpen   : initial open state (default false)
 *   showFailed    : whether to display failed/skipped entries (default false)
 */

const SOURCE_LABELS = {
  fotmob:               'FotMob',
  sofascore:            'SofaScore',
  flashscore:           'Flashscore',
  fbref:                'FBref',
  basketball_reference: 'Basketball-Reference',
  nba_stats:            'NBA Stats',
  understat:            'Understat',
};

const EVIDENCE_TYPE_LABELS = {
  es: {
    news_context:       'Contexto editorial',
    injuries:           'Lesiones',
    probable_lineups:   'Alineaciones probables',
    historical_trends:  'Tendencias históricas',
    h2h:                'Histórico H2H',
    recent_form:        'Forma reciente',
    tactical_context:   'Contexto táctico',
    standings_context:  'Clasificación',
    odds_context:       'Cuotas',
    live_stats:         'Stats en vivo',
    market_context:     'Contexto de mercado',
  },
  en: {
    news_context:       'News context',
    injuries:           'Injuries',
    probable_lineups:   'Probable lineups',
    historical_trends:  'Historical trends',
    h2h:                'Head-to-head',
    recent_form:        'Recent form',
    tactical_context:   'Tactical context',
    standings_context:  'Standings',
    odds_context:       'Odds',
    live_stats:         'Live stats',
    market_context:     'Market context',
  },
};

function FreshnessBadge({ freshness, lang }) {
  const label = freshness === 'fresh'
    ? (lang === 'en' ? 'fresh' : 'reciente')
    : freshness === 'stale'
      ? (lang === 'en' ? 'stale' : 'antigua')
      : '—';
  const cls = freshness === 'fresh'
    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
    : freshness === 'stale'
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : 'bg-slate-500/15 text-slate-300 border-slate-500/30';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide border ${cls}`}>
      <Clock className="h-2.5 w-2.5" />{label}
    </span>
  );
}

function SourceItem({ item, lang, testId }) {
  const label = SOURCE_LABELS[item.source] || item.source;
  const failed = item.status !== 'ok';
  const typeLabel = (EVIDENCE_TYPE_LABELS[lang] || EVIDENCE_TYPE_LABELS.es)[item.evidence_type] || item.evidence_type;
  return (
    <li
      className={`rounded-md border ${failed ? 'border-border/40 bg-card/20 opacity-60' : 'border-border/60 bg-background/40'} p-2`}
      data-testid={`${testId}-${item.source}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Database className="h-3.5 w-3.5 text-cyan-300 shrink-0" />
        <span className="text-xs font-semibold text-foreground/90">{label}</span>
        <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-500/15 text-slate-300 border border-slate-500/30">
          {typeLabel}
        </span>
        <FreshnessBadge freshness={item.freshness} lang={lang} />
        {item.used_in_analysis
          ? <span className="inline-flex items-center gap-1 text-[10px] text-emerald-300 border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 rounded">
              <Check className="h-2.5 w-2.5" />{lang === 'en' ? 'used' : 'usado'}
            </span>
          : !failed && <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground border border-border px-1.5 py-0.5 rounded">
              {lang === 'en' ? 'reference' : 'referencia'}
            </span>}
        {item.url && (
          <a
            href={item.url} target="_blank" rel="noreferrer"
            className="ml-auto text-[10px] text-cyan-400 hover:text-cyan-300 inline-flex items-center gap-1"
            onClick={(e) => e.stopPropagation()}
          >
            {lang === 'en' ? 'open' : 'abrir'} <ExternalLink className="h-2.5 w-2.5" />
          </a>
        )}
      </div>
      {failed ? (
        <div className="mt-1 text-[11px] text-red-300/80 flex items-center gap-1">
          <X className="h-3 w-3" />
          {(item.errors && item.errors[0]) || (lang === 'en' ? 'no data' : 'sin datos')}
        </div>
      ) : (
        Array.isArray(item.extracted_data) && item.extracted_data.length > 0 && (
          <ul className="mt-1.5 list-disc list-inside text-[11px] text-foreground/85 space-y-0.5">
            {item.extracted_data.slice(0, 6).map((b, i) => (
              <li key={i} className="break-words">{b}</li>
            ))}
          </ul>
        )
      )}
    </li>
  );
}

export function ExternalSourceEvidencePanel({
  evidence,
  testId = 'external-sources',
  defaultOpen = false,
  showFailed = false,
}) {
  const { lang } = useI18n();
  const filtered = useMemo(() => {
    const list = Array.isArray(evidence) ? evidence : [];
    return showFailed ? list : list.filter((e) => e.status === 'ok');
  }, [evidence, showFailed]);
  const [open, setOpen] = useState(defaultOpen);

  if (!filtered.length) return null;

  const usedCount = filtered.filter((e) => e.used_in_analysis).length;

  return (
    <div className="rounded-md border border-border/60 bg-card/40" data-testid={testId}>
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-secondary/30 transition-colors"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Database className="h-3.5 w-3.5 text-cyan-300 shrink-0" />
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/85 truncate">
            {lang === 'en' ? 'External sources found' : 'Fuentes externas encontradas'}
          </span>
          <span className="text-[10px] mono font-mono-tabular text-muted-foreground border border-border rounded px-1.5">
            {filtered.length}
          </span>
          {usedCount > 0 && (
            <span className="text-[10px] mono font-mono-tabular text-emerald-300 border border-emerald-500/30 bg-emerald-500/10 rounded px-1.5">
              {usedCount} {lang === 'en' ? 'used' : 'usadas'}
            </span>
          )}
        </div>
        {open
          ? <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
          : <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />}
      </button>
      {open && (
        <ul className="px-3 pb-3 pt-1 space-y-1.5" data-testid={`${testId}-list`}>
          {filtered.map((ev, i) => (
            <SourceItem key={`${ev.source}-${i}`} item={ev} lang={lang} testId={testId} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Compact inline display of possible alternative markets for a discarded
    match. Renders a small badge row + the user_review_note when present. */
export function PossibleAlternativeMarkets({ markets, note, testId = 'possible-alternatives' }) {
  const { lang } = useI18n();
  if (!Array.isArray(markets) || markets.length === 0) return null;
  return (
    <div className="rounded-md border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 space-y-1.5" data-testid={testId}>
      <div className="text-[10px] uppercase tracking-wide text-cyan-200 font-semibold">
        {lang === 'en' ? 'Manual review — possible alternatives' : 'Revisión manual — alternativas posibles'}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {markets.map((m, i) => (
          <span
            key={`${m}-${i}`}
            className="inline-flex items-center px-2 py-0.5 rounded-md bg-cyan-500/10 text-cyan-200 border border-cyan-500/30 text-[11px]"
            data-testid={`${testId}-item-${i}`}
          >
            {m}
          </span>
        ))}
      </div>
      {note && (
        <p className="text-[11px] text-muted-foreground italic leading-snug" data-testid={`${testId}-note`}>
          {note}
        </p>
      )}
    </div>
  );
}
