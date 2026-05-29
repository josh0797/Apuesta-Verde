import { useState } from 'react';
import {
  Network, ChevronDown, ChevronUp, ExternalLink, CheckCircle2, XCircle, AlertTriangle,
} from 'lucide-react';

/**
 * Renders the `external_sources.sources_consulted` array attached to a
 * baseball pick/discarded match by the MLB lineup-rescue layer.
 *
 * Shape coming from /app/backend/services/external_sources/mlb_lineup_rescue.py:
 *
 *   external_sources: {
 *     sources_consulted: [
 *       { source, status: 'success'|'partial'|'failed',
 *         url, data_types: [], matchup_count, confirmed_count?, reason? }
 *     ],
 *     data_quality: { overall, pitcher_quality, lineup_quality, ... },
 *     lineup_status: 'confirmed'|'projected'|'missing',
 *     home_pitcher_source: 'rotowire_mlb_lineups' | ... | null,
 *     away_pitcher_source: ...,
 *     conflict: boolean,
 *     rescued: boolean,
 *   }
 *
 * Props
 * -----
 *   external      : the `external_sources` object above (required)
 *   testId        : data-testid base
 *   defaultOpen   : initial open state (default false)
 *   lang          : 'es' | 'en'
 */

const SOURCE_LABELS = {
  rotowire_mlb_lineups:     'RotoWire',
  mlb_official_lineups:     'MLB.com',
  fantasypros_mlb_lineups:  'FantasyPros',
  espn_mlb_scoreboard:      'ESPN MLB',
  mlb_stats_api:            'MLB Stats API',
};

const DATA_TYPE_LABELS = {
  es: {
    probable_pitchers:  'Pitchers probables',
    projected_lineups:  'Lineups proyectados',
    confirmed_lineups:  'Lineups confirmados',
    weather:            'Clima',
    injuries:           'Lesiones',
    batting_splits:     'Splits de bateo',
  },
  en: {
    probable_pitchers:  'Probable pitchers',
    projected_lineups:  'Projected lineups',
    confirmed_lineups:  'Confirmed lineups',
    weather:            'Weather',
    injuries:           'Injuries',
    batting_splits:     'Batting splits',
  },
};

function StatusIcon({ status }) {
  if (status === 'success') {
    return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" aria-label="success" />;
  }
  if (status === 'partial') {
    return <AlertTriangle className="w-3.5 h-3.5 text-amber-400" aria-label="partial" />;
  }
  return <XCircle className="w-3.5 h-3.5 text-rose-400/80" aria-label="failed" />;
}

export function SourcesConsultedPanel({
  external,
  testId = 'sources-consulted-panel',
  defaultOpen = false,
  lang = 'es',
}) {
  const [open, setOpen] = useState(defaultOpen);

  if (!external || !Array.isArray(external.sources_consulted) || external.sources_consulted.length === 0) {
    return null;
  }

  const sources = external.sources_consulted;
  const successes = sources.filter(s => s.status === 'success' || s.status === 'partial').length;
  const failed = sources.length - successes;
  const dataQuality = external.data_quality?.overall ?? null;
  const conflict = Boolean(external.conflict);
  const rescued = Boolean(external.rescued);

  // Quality tone for the header chip
  const qualityTone = dataQuality == null
    ? 'border-slate-500/30 bg-slate-500/5 text-slate-300'
    : dataQuality >= 75
    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
    : dataQuality >= 50
    ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
    : 'border-rose-500/30 bg-rose-500/10 text-rose-200';

  return (
    <div
      className="rounded-md border border-border/60 bg-muted/20"
      data-testid={testId}
    >
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5 text-xs font-semibold text-foreground/90">
          <Network className="w-3.5 h-3.5 text-cyan-300" />
          {lang === 'es' ? 'Fuentes consultadas' : 'Sources consulted'}
          <span className="text-[10px] font-normal text-muted-foreground ml-1">
            ({successes}/{sources.length} OK)
          </span>
        </span>
        <span className="flex items-center gap-2">
          {dataQuality != null && (
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded-md border ${qualityTone}`}
              data-testid={`${testId}-quality-chip`}
            >
              {lang === 'es' ? 'Calidad' : 'Quality'}: {dataQuality}
            </span>
          )}
          {conflict && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-md border border-rose-500/40 bg-rose-500/10 text-rose-200">
              {lang === 'es' ? 'Conflicto' : 'Conflict'}
            </span>
          )}
          {open ? <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 pt-1 space-y-1.5" data-testid={`${testId}-body`}>
          {sources.map((s, i) => {
            const label = SOURCE_LABELS[s.source] || s.source;
            const tone = s.status === 'success'
              ? 'border-emerald-500/20'
              : s.status === 'partial'
              ? 'border-amber-500/30'
              : 'border-rose-500/20';
            const types = (s.data_types || []).map(t => (DATA_TYPE_LABELS[lang] || DATA_TYPE_LABELS.es)[t] || t).join(', ');
            return (
              <div
                key={`${s.source}-${i}`}
                className={`flex items-start gap-2 rounded-md border ${tone} bg-black/20 px-2 py-1.5 text-[11px]`}
                data-testid={`${testId}-row-${i}`}
              >
                <StatusIcon status={s.status} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground/90 truncate">{label}</span>
                    {s.url && (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="shrink-0 inline-flex items-center gap-0.5 text-[10px] text-cyan-300/80 hover:text-cyan-200"
                        data-testid={`${testId}-row-${i}-url`}
                      >
                        <ExternalLink className="w-3 h-3" />
                        {lang === 'es' ? 'Ver' : 'Open'}
                      </a>
                    )}
                  </div>
                  <div className="text-[10px] text-muted-foreground leading-snug">
                    {s.status === 'failed' && (s.reason || (lang === 'es' ? 'sin datos' : 'no data'))}
                    {(s.status === 'success' || s.status === 'partial') && (
                      <>
                        {types && <span>{types}</span>}
                        {typeof s.matchup_count === 'number' && (
                          <span className="ml-1.5 text-muted-foreground/80">
                            · {s.matchup_count} {lang === 'es' ? 'partidos' : 'matchups'}
                            {typeof s.confirmed_count === 'number' && s.confirmed_count > 0 && (
                              <span className="text-emerald-300/80"> ({s.confirmed_count} {lang === 'es' ? 'confirmados' : 'confirmed'})</span>
                            )}
                          </span>
                        )}
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {rescued && (
            <div className="text-[10px] italic text-emerald-300/80 pt-1 leading-snug">
              {lang === 'es'
                ? `Pitcher recuperado: home via ${SOURCE_LABELS[external.home_pitcher_source] || external.home_pitcher_source || '—'} · away via ${SOURCE_LABELS[external.away_pitcher_source] || external.away_pitcher_source || '—'}.`
                : `Pitchers recovered: home via ${SOURCE_LABELS[external.home_pitcher_source] || external.home_pitcher_source || '—'} · away via ${SOURCE_LABELS[external.away_pitcher_source] || external.away_pitcher_source || '—'}.`}
            </div>
          )}

          {!rescued && failed > 0 && successes === 0 && (
            <div className="text-[10px] italic text-rose-300/80 pt-1 leading-snug">
              {lang === 'es'
                ? 'Se descartó después de consultar todas las fuentes disponibles.'
                : 'Discarded after consulting all available sources.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default SourcesConsultedPanel;
