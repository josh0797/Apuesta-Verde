/**
 * LiveScriptRealityBadge — compact live badge for MLB cards.
 *
 * Reads either:
 *   - `match.live_script_reality_check` (persisted by backend), or
 *   - a manually computed payload passed via the `check` prop.
 *
 * Renders one of:
 *   - "Under confirmado en vivo"  (LIVE_UNDER_CONFIRMATION) — emerald
 *   - "Over en riesgo"             (LIVE_OVER_WARNING)       — amber
 *   - "Over confirmado en vivo"   (LIVE_OVER_CONFIRMATION)  — emerald
 *   - "Under en riesgo"            (LIVE_OVER_DANGER)        — amber/red
 *   - nothing                      (LIVE_NEUTRAL)
 *
 * Supporting stats (hits, walks, HR, LOB, inning, live proj) shown in a
 * collapsible drawer below the badge.
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp } from 'lucide-react';

const LABELS = {
  LIVE_UNDER_CONFIRMATION: { es: 'Under confirmado en vivo', en: 'Under confirmed live', tone: 'emerald' },
  LIVE_OVER_WARNING:       { es: 'Over en riesgo',           en: 'Over warning',         tone: 'amber'   },
  LIVE_OVER_CONFIRMATION:  { es: 'Over confirmado en vivo',  en: 'Over confirmed live',  tone: 'emerald' },
  LIVE_OVER_DANGER:        { es: 'Under en riesgo',           en: 'Over in danger',       tone: 'red'     },
};

const TONE_CLASSES = {
  emerald: 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200',
  amber:   'bg-amber-500/15 border-amber-500/40 text-amber-200',
  red:     'bg-red-500/15 border-red-500/40 text-red-200',
};

export function LiveScriptRealityBadge({
  check,
  match,
  lang = 'es',
  stats = null,
  testId,
}) {
  const data = check || match?.live_script_reality_check;
  const [open, setOpen] = useState(false);

  if (!data || data.classification === 'LIVE_NEUTRAL' || !LABELS[data.classification]) {
    return null;
  }
  const cfg = LABELS[data.classification];
  const matchId = match?.match_id || '';

  return (
    <div
      className={`rounded-md border px-2.5 py-1.5 space-y-1 ${TONE_CLASSES[cfg.tone]}`}
      data-testid={testId || `live-script-badge-${matchId}`}
      data-classification={data.classification}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] font-semibold leading-tight">
            {lang === 'en' ? cfg.en : cfg.es}
          </span>
          {data.summary_es && (
            <span className="text-[10px] opacity-80 leading-snug">
              {lang === 'en' ? (data.summary_en || data.summary_es) : data.summary_es}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="opacity-70 hover:opacity-100 shrink-0"
          aria-label={lang === 'en' ? 'Toggle details' : 'Ver detalles'}
          data-testid={`live-script-badge-toggle-${matchId}`}
        >
          {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>
      </div>

      {open && (
        <div
          className="grid grid-cols-2 gap-1.5 text-[10px] pt-1 border-t border-current/20"
          data-testid={`live-script-badge-details-${matchId}`}
        >
          {data.expected_runs != null && (
            <div>{lang === 'en' ? 'Expected' : 'Proy. inicial'}: <strong>{data.expected_runs}</strong></div>
          )}
          {data.live_projected_final_runs != null && (
            <div>{lang === 'en' ? 'Live proj.' : 'Proy. live'}: <strong>{data.live_projected_final_runs}</strong></div>
          )}
          {stats?.inning != null && <div>Inning: <strong>{stats.inning}</strong></div>}
          {stats?.score_total != null && <div>Score: <strong>{stats.score_total}</strong></div>}
          {stats?.hits != null && <div>Hits: <strong>{stats.hits}</strong></div>}
          {stats?.walks != null && <div>Walks: <strong>{stats.walks}</strong></div>}
          {stats?.home_runs != null && <div>HR: <strong>{stats.home_runs}</strong></div>}
          {stats?.left_on_base != null && <div>LOB: <strong>{stats.left_on_base}</strong></div>}
          {data.fragility_live != null && (
            <div className="col-span-2">
              {lang === 'en' ? 'Live fragility' : 'Fragilidad live'}:{' '}
              <strong>{Math.round(data.fragility_live * 100)}%</strong>
            </div>
          )}
          {(data.reason_codes || []).length > 0 && (
            <div className="col-span-2 flex flex-wrap gap-1 pt-1">
              {data.reason_codes.map((rc) => (
                <Badge key={rc} variant="outline" className="text-[9px]">{rc}</Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default LiveScriptRealityBadge;
