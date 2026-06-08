/**
 * SiegePressureBadge — Phase 45.
 *
 * Surfaces the verdict from `services.football_siege_pressure_guard`
 * which is piped through `live_reevaluation._reevaluate_football()` as
 * `reeval.siege_pressure`.
 *
 * Behavior:
 *   - verdict === 'BLOCK_UNDER'         → red badge "Under bloqueado por asedio"
 *   - verdict === 'DOWNGRADE_UNDER_3_5' → amber badge "Confianza limitada"
 *   - siege_pressure_high && verdict === 'ALLOW_UNDER'
 *                                       → amber "Alerta de asedio (no bloqueado)"
 *   - otherwise                          → renders nothing
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, ChevronDown, ChevronUp, ShieldAlert } from 'lucide-react';

const VERDICT_LABELS = {
  BLOCK_UNDER: {
    es: 'Under bloqueado por asedio',
    en: 'Under blocked by siege',
    tone: 'red',
    Icon: ShieldAlert,
  },
  DOWNGRADE_UNDER_3_5: {
    es: 'Asedio sostenido — confianza limitada',
    en: 'Sustained siege — confidence capped',
    tone: 'amber',
    Icon: AlertTriangle,
  },
  ALLOW_UNDER: {
    es: 'Asedio detectado — sin bloqueo',
    en: 'Siege detected — not blocked',
    tone: 'amber',
    Icon: AlertTriangle,
  },
};

const TONES = {
  red:     'bg-rose-500/15 border-rose-500/40 text-rose-200',
  amber:   'bg-amber-500/15 border-amber-500/40 text-amber-200',
};

const REASON_DESCRIPTIONS = {
  SIEGE_PRESSURE_HIGH:             'Asedio ofensivo confirmado.',
  DELAYED_CONVERSION_RISK:         'Riesgo de conversión tardía.',
  DOMINANT_TEAM_ASSEDIO:           'Asedio del equipo dominante.',
  LOW_SCORE_MISLEADING:            'Marcador bajo engañoso.',
  UNDER_BLOCKED_BY_PRESSURE:       'Under bloqueado por presión.',
  LATE_GOAL_RISK_HIGH:             'Alto riesgo de gol tardío.',
  LOW_SCORE_WITH_SIEGE:            'Marcador bajo + asedio.',
  TWENTY_MINUTES_LEFT:             'Quedan ≥ 20 minutos.',
  OVER_0_5_LIVE_SUPPORTED:         'Over 0.5 live soportado por el modelo.',
  DOMINANT_PRESSURE_GOAL_EXPECTED: 'Se espera un gol por presión sostenida.',
  UNDER_REJECTED_DESPITE_LOW_SCORE: 'Under rechazado pese a marcador bajo.',
};

export function SiegePressureBadge({ data, lang = 'es', testId }) {
  const [open, setOpen] = useState(false);
  if (!data || typeof data !== 'object') return null;
  // Only render when the layer detected something worth showing.
  if (!data.siege_pressure_high) return null;
  const verdict = data.verdict || 'ALLOW_UNDER';
  const cfg = VERDICT_LABELS[verdict] || VERDICT_LABELS.ALLOW_UNDER;
  const label = lang === 'en' ? cfg.en : cfg.es;
  const Icon = cfg.Icon;
  const toneCls = TONES[cfg.tone];
  const reasonCodes = Array.isArray(data.reason_codes) ? data.reason_codes : [];
  const preferMarkets = Array.isArray(data.prefer_markets) ? data.prefer_markets : [];
  const m = data.metrics || {};

  return (
    <div
      className={`rounded-md border px-2.5 py-1.5 space-y-1 ${toneCls}`}
      data-testid={testId || 'siege-pressure-badge'}
      data-verdict={verdict}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-1.5">
            <Icon className="h-3.5 w-3.5 shrink-0" />
            <span className="text-[11px] font-semibold leading-tight">{label}</span>
          </div>
          {data.ui_message_es && (
            <span className="text-[10px] opacity-85 leading-snug">{data.ui_message_es}</span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="opacity-70 hover:opacity-100 shrink-0"
          aria-label={lang === 'en' ? 'Toggle details' : 'Ver detalles'}
          data-testid="siege-pressure-badge-toggle"
        >
          {open ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>
      </div>

      {open && (
        <div className="space-y-1.5 pt-1 border-t border-current/20" data-testid="siege-pressure-badge-details">
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px]">
            {m.minute != null && <div>Min: <strong>{m.minute}</strong></div>}
            {data.dominant_side && <div>Dominante: <strong>{data.dominant_side}</strong></div>}
            {m.dominant_possession != null && (
              <div>Posesión: <strong>{m.dominant_possession}%</strong> vs {m.weak_possession ?? '—'}%</div>
            )}
            {m.dominant_shots != null && (
              <div>Remates: <strong>{m.dominant_shots}</strong> vs {m.weak_shots ?? '—'}</div>
            )}
            {m.dominant_sot != null && (
              <div>Al arco: <strong>{m.dominant_sot}</strong> vs {m.weak_sot ?? '—'}</div>
            )}
            {m.dominant_xg != null && (
              <div>xG: <strong>{Number(m.dominant_xg).toFixed(2)}</strong> vs {m.weak_xg != null ? Number(m.weak_xg).toFixed(2) : '—'}</div>
            )}
            {m.shots_ratio != null && <div>Ratio remates: <strong>{m.shots_ratio}:1</strong></div>}
            {m.sot_ratio != null    && <div>Ratio SOT: <strong>{m.sot_ratio}:1</strong></div>}
          </div>

          {preferMarkets.length > 0 && (
            <div className="text-[10px]">
              <span className="opacity-70">{lang === 'en' ? 'Prefer: ' : 'Preferir: '}</span>
              <span>{preferMarkets.join(' · ')}</span>
            </div>
          )}

          {reasonCodes.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {reasonCodes.map((rc) => (
                <Badge
                  key={rc}
                  variant="outline"
                  className="text-[9px]"
                  title={REASON_DESCRIPTIONS[rc] || rc}
                  data-testid={`siege-rc-${rc}`}
                >
                  {rc}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default SiegePressureBadge;
