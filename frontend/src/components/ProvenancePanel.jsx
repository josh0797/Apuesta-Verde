import { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, Database, Globe, CircleDot, Clock, AlertCircle, CheckCircle2 } from 'lucide-react';

/**
 * ProvenancePanel — renders the per-section data lineage of a match.
 *
 * Two visible layers:
 *  • Always-on compact BADGE with the primary source. Hover/click expands.
 *  • Collapsible breakdown listing every section (odds / stats / h2h /
 *    lineups / context / live) with its source and freshness ("hace 12 min").
 *
 * Consumes the `_provenance` object attached by services/provenance.py to
 * the match doc returned by GET /api/matches/{match_id}:
 *
 *   {
 *     primary_source: "api_sports",
 *     primary_source_label: "API-Sports",
 *     fetched_at: "2026-05-23T13:05:10Z",
 *     sections: {
 *       odds:    { source, source_label, fetched_at, available },
 *       stats:   { ... },
 *       h2h:     { ... },
 *       lineups: { ... },
 *       context: { ... },
 *       live:    { ... },
 *     }
 *   }
 */
const SECTION_META = {
  odds:    { es: 'Cuotas',         en: 'Odds' },
  stats:   { es: 'Estadísticas',   en: 'Stats' },
  h2h:     { es: 'Historial H2H',  en: 'H2H history' },
  lineups: { es: 'Alineaciones',   en: 'Lineups' },
  context: { es: 'Contexto equipo', en: 'Team context' },
  live:    { es: 'En vivo',        en: 'Live' },
};

// Tone per primary source — used by the always-visible badge.
const SOURCE_TONE = {
  api_sports:   'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
  api_football: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
  mlb_stats_api: 'border-violet-500/40 bg-violet-500/10 text-violet-200',
  espn:         'border-amber-500/40 bg-amber-500/10 text-amber-200',
  flashscore:   'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  sofascore:    'border-rose-500/40 bg-rose-500/10 text-rose-200',
  sportytrader: 'border-slate-500/40 bg-slate-500/10 text-slate-200',
};

function relativeTime(isoTs, lang) {
  if (!isoTs) return lang === 'en' ? 'never' : 'nunca';
  const ts = Date.parse(isoTs);
  if (Number.isNaN(ts)) return lang === 'en' ? 'unknown' : 'desconocido';
  const diffMs = Date.now() - ts;
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return lang === 'en' ? 'just now' : 'ahora';
  if (m < 60) return lang === 'en' ? `${m} min ago` : `hace ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return lang === 'en' ? `${h} h ago` : `hace ${h} h`;
  const d = Math.floor(h / 24);
  if (d < 30) return lang === 'en' ? `${d} d ago` : `hace ${d} d`;
  const mo = Math.floor(d / 30);
  return lang === 'en' ? `${mo} mo ago` : `hace ${mo} meses`;
}

function freshnessTone(isoTs) {
  if (!isoTs) return 'stale';
  const m = (Date.now() - Date.parse(isoTs)) / 60000;
  if (Number.isNaN(m)) return 'stale';
  if (m < 30) return 'fresh';
  if (m < 360) return 'recent';     // < 6 h
  if (m < 1440) return 'aging';     // < 24 h
  return 'stale';
}

const FRESH_DOT = {
  fresh:  'bg-emerald-400',
  recent: 'bg-cyan-400',
  aging:  'bg-amber-400',
  stale:  'bg-red-400',
};

/** Compact one-line badge — always visible in the match header. */
export function ProvenanceBadge({ provenance, lang = 'es', onClick, testId = 'provenance-badge' }) {
  if (!provenance) return null;
  const primary = provenance.primary_source || 'unknown';
  const label = provenance.primary_source_label || primary;
  const tone = SOURCE_TONE[primary] || 'border-border bg-secondary/40 text-muted-foreground';
  const freshness = freshnessTone(provenance.fetched_at);
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      title={lang === 'en'
        ? `Primary source · ${label} · ${relativeTime(provenance.fetched_at, lang)}`
        : `Fuente principal · ${label} · ${relativeTime(provenance.fetched_at, lang)}`}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[11px] font-semibold hover:brightness-125 transition ${tone}`}
    >
      <Database className="h-3 w-3" />
      <span>{lang === 'en' ? 'Source' : 'Fuente'}: {label}</span>
      <span className={`h-1.5 w-1.5 rounded-full ${FRESH_DOT[freshness] || FRESH_DOT.stale}`} aria-hidden />
    </button>
  );
}

/** Full panel with the badge + collapsible per-section breakdown. */
export function ProvenancePanel({ provenance, lang = 'es', defaultOpen = false, testId = 'provenance-panel' }) {
  const [open, setOpen] = useState(!!defaultOpen);

  const sections = useMemo(() => {
    if (!provenance?.sections) return [];
    return Object.entries(provenance.sections).map(([key, s]) => ({
      key,
      label_es: SECTION_META[key]?.es || key,
      label_en: SECTION_META[key]?.en || key,
      source_label: s?.source_label,
      fetched_at: s?.fetched_at,
      available: !!s?.available,
    }));
  }, [provenance]);

  if (!provenance) return null;

  return (
    <section
      className="rounded-xl border border-border bg-card p-4 sm:p-5"
      data-testid={testId}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 text-left"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Globe className="h-4 w-4 text-cyan-300 shrink-0" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {lang === 'en' ? 'Data provenance' : 'Origen de los datos'}
          </h3>
          <ProvenanceBadge provenance={provenance} lang={lang} testId={`${testId}-summary-badge`} />
        </div>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </button>

      {open && (
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2" data-testid={`${testId}-sections`}>
          {sections.map((s) => (
            <ProvenanceRow key={s.key} section={s} lang={lang} />
          ))}
        </div>
      )}

      {open && (
        <p className="text-[11px] text-muted-foreground italic mt-3 leading-relaxed">
          {lang === 'en'
            ? 'Each section reflects the source that produced its data and when it was last refreshed. Older timestamps may indicate cached or fallback data.'
            : 'Cada sección refleja la fuente que produjo sus datos y cuándo se refrescaron por última vez. Marcas de tiempo antiguas pueden indicar datos cacheados o de fallback.'}
        </p>
      )}
    </section>
  );
}

function ProvenanceRow({ section, lang }) {
  const label = lang === 'en' ? section.label_en : section.label_es;
  if (!section.available) {
    return (
      <div
        className="rounded-md border border-dashed border-border/60 bg-background/30 px-3 py-2 flex items-center justify-between gap-2"
        data-testid={`provenance-row-${section.key}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <AlertCircle className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <span className="text-xs font-medium truncate">{label}</span>
        </div>
        <span className="text-[10px] text-muted-foreground uppercase tracking-wide font-semibold">
          {lang === 'en' ? 'not available' : 'no disponible'}
        </span>
      </div>
    );
  }
  const tone = freshnessTone(section.fetched_at);
  return (
    <div
      className="rounded-md border border-border/60 bg-background/30 px-3 py-2 flex items-center justify-between gap-2"
      data-testid={`provenance-row-${section.key}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
        <span className="text-xs font-medium truncate">{label}</span>
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground shrink-0">
        <span className="font-mono-tabular tabular-nums" data-testid={`provenance-row-${section.key}-source`}>
          {section.source_label || '—'}
        </span>
        <CircleDot className={`h-2 w-2 ${tone === 'fresh' ? 'text-emerald-400' : tone === 'recent' ? 'text-cyan-400' : tone === 'aging' ? 'text-amber-400' : 'text-red-400'}`} />
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3 opacity-60" />
          {relativeTime(section.fetched_at, lang)}
        </span>
      </div>
    </div>
  );
}
