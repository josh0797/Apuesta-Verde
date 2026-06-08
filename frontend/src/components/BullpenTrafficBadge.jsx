/**
 * BullpenTrafficBadge — Phase 44.
 *
 * Surfaces the bullpen-vs-traffic verdict produced by
 * `services.traffic_score.classify_bullpen_traffic_interaction()` and
 * piped through `live_reevaluation._reevaluate_baseball()` as
 * `reeval.bullpen_traffic` (also persisted on the match doc when the
 * MLB pregame pipeline pre-hydrates it).
 *
 * Behavior:
 *   - verdict === 'penalize_under' → red badge "Bullpen risk confirmed by traffic"
 *   - verdict === 'hold_under'     → emerald "Bullpen aislado — Under viable"
 *   - verdict === 'no_signal' AND has reason codes → amber neutral pill
 *   - missing data / no signal       → renders nothing
 *
 * Strict observe-only: never changes the engine pick.
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp, AlertTriangle, ShieldCheck } from 'lucide-react';

const VERDICT_LABELS = {
  penalize_under: {
    es: 'Riesgo de bullpen confirmado por tráfico',
    en: 'Bullpen risk confirmed by traffic',
    tone: 'red',
    Icon: AlertTriangle,
  },
  hold_under: {
    es: 'Bullpen aislado — Under viable',
    en: 'Bullpen risk isolated — Under viable',
    tone: 'emerald',
    Icon: ShieldCheck,
  },
};

const TONES = {
  red:     'bg-rose-500/15 border-rose-500/40 text-rose-200',
  emerald: 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200',
  amber:   'bg-amber-500/15 border-amber-500/40 text-amber-200',
};

const REASON_DESCRIPTIONS = {
  BULLPEN_RISK_CONFIRMED_BY_TRAFFIC: 'Bullpen vulnerable + tráfico ofensivo alto.',
  HIGH_TRAFFIC_UNDER_DANGER:         'Under en riesgo por presión ofensiva.',
  BULLPEN_RISK_ISOLATED_NOT_ENOUGH:  'Bullpen vulnerable sin tráfico que lo explote.',
  LOW_TRAFFIC_UNDER_SURVIVED:        'Under históricamente sobrevive con tráfico bajo.',
};

export function BullpenTrafficBadge({ data, lang = 'es', testId }) {
  const [open, setOpen] = useState(false);
  if (!data || typeof data !== 'object') return null;
  const verdict = data.verdict;
  const cfg = VERDICT_LABELS[verdict];
  const reasonCodes = Array.isArray(data.reason_codes) ? data.reason_codes : [];
  if (!cfg && reasonCodes.length === 0) return null;

  const fallbackLabel = lang === 'en'
    ? 'Bullpen-traffic observation'
    : 'Observación bullpen-tráfico';
  const label = cfg ? (lang === 'en' ? cfg.en : cfg.es) : fallbackLabel;
  const Icon = cfg?.Icon || AlertTriangle;
  const toneCls = TONES[cfg?.tone || 'amber'];

  return (
    <div
      className={`rounded-md border px-2.5 py-1.5 space-y-1 ${toneCls}`}
      data-testid={testId || 'bullpen-traffic-badge'}
      data-verdict={verdict || 'no_signal'}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span className="text-[11px] font-semibold leading-tight">{label}</span>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="opacity-70 hover:opacity-100 shrink-0"
          aria-label={lang === 'en' ? 'Toggle details' : 'Ver detalles'}
          data-testid="bullpen-traffic-badge-toggle"
        >
          {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>
      </div>

      {open && (
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] pt-1 border-t border-current/20" data-testid="bullpen-traffic-badge-details">
          {data.bullpen_era_7d_max != null && (
            <div>Bullpen ERA 7d (máx): <strong>{Number(data.bullpen_era_7d_max).toFixed(2)}</strong></div>
          )}
          {data.traffic_bucket && (
            <div>Tráfico: <strong>{data.traffic_bucket}</strong></div>
          )}
          {data.traffic_score != null && (
            <div className="col-span-2">Score compuesto: <strong>{data.traffic_score}/100</strong></div>
          )}
          {reasonCodes.length > 0 && (
            <div className="col-span-2 flex flex-wrap gap-1 pt-1">
              {reasonCodes.map((rc) => (
                <Badge
                  key={rc}
                  variant="outline"
                  className="text-[9px]"
                  title={REASON_DESCRIPTIONS[rc] || rc}
                  data-testid={`bullpen-traffic-rc-${rc}`}
                >
                  {rc}
                </Badge>
              ))}
            </div>
          )}
          <div className="col-span-2 opacity-70 text-[9px] pt-1">
            {lang === 'en'
              ? 'Observe-only — engine pick unchanged.'
              : 'Observe-only — la recomendación del motor no se modifica.'}
          </div>
        </div>
      )}
    </div>
  );
}

export default BullpenTrafficBadge;
