import { Radio, Zap, Clock, AlertTriangle, Hourglass, Flag } from 'lucide-react';

/**
 * LiveStateBadge — renders the live state computed by the backend
 * (`services/live_lifecycle.compute_live_state`).
 *
 * States visualized:
 *   LIVE_ACTIVE   — green, pulsing dot, minute label
 *   LIVE_LATE     — amber, "85'+" badge (88+ minute window)
 *   GARBAGE_TIME  — orange, "Tiempo muerto" warning
 *   HT            — cyan, "Descanso"
 *   LIVE_STALE    — red, "LIVE STALE", non-clickable warning
 *   FINISHED      — gray, shouldn't render (UI filters it)
 *   NOT_STARTED   — gray, shouldn't render
 *
 * Designed to live next to the league name in each LivePage card.
 */
const STATE_META = {
  LIVE_ACTIVE:  { tone: 'emerald', icon: Radio,         es: 'En vivo',       en: 'Live' },
  LIVE_LATE:    { tone: 'amber',   icon: Hourglass,     es: 'Cierre',        en: 'Late' },
  GARBAGE_TIME: { tone: 'orange',  icon: Zap,           es: 'Tiempo muerto', en: 'Garbage time' },
  HT:           { tone: 'cyan',    icon: Clock,         es: 'Descanso',      en: 'Half-time' },
  LIVE_STALE:   { tone: 'red',     icon: AlertTriangle, es: 'Live stale',    en: 'Live stale' },
  FINISHED:     { tone: 'slate',   icon: Flag,          es: 'Finalizado',    en: 'Finished' },
  NOT_STARTED:  { tone: 'slate',   icon: Clock,         es: 'No iniciado',   en: 'Not started' },
};

const TONE_CLS = {
  emerald: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
  amber:   'border-amber-500/40 bg-amber-500/10 text-amber-200',
  orange:  'border-orange-500/40 bg-orange-500/10 text-orange-200',
  cyan:    'border-cyan-500/40 bg-cyan-500/10 text-cyan-200',
  red:     'border-red-500/40 bg-red-500/10 text-red-200',
  slate:   'border-slate-500/30 bg-slate-500/10 text-slate-300',
};

export function LiveStateBadge({ liveState, lang = 'es', testId = 'live-state-badge' }) {
  if (!liveState) return null;
  const meta = STATE_META[liveState.state] || STATE_META.LIVE_ACTIVE;
  const Icon = meta.icon;
  const label = liveState.minute_label || meta[lang] || meta.es;
  const tooltip = lang === 'en'
    ? `${meta.en} · ${liveState.reason || ''}`.trim()
    : `${meta.es} · ${liveState.reason || ''}`.trim();
  return (
    <span
      data-testid={testId}
      title={tooltip}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-semibold ${TONE_CLS[meta.tone]}`}
    >
      <Icon className={`h-3 w-3 ${liveState.state === 'LIVE_ACTIVE' ? 'animate-pulse' : ''}`} aria-hidden />
      <span className="font-mono-tabular">{label}</span>
    </span>
  );
}

/**
 * FreshnessBadge — renders the freshness score (0-100) computed by the
 * backend (`compute_freshness`). Three buckets:
 *   ≥70  → DATOS_FRESCOS    (green)
 *   ≥50  → DATOS_RETRASADOS (amber, warning)
 *   ≥30  → LIVE_STALE       (red, do-not-bet)
 *   <30  → EXPIRED          (filtered out before reaching the UI)
 */
const FRESH_META = {
  DATOS_FRESCOS:   { tone: 'emerald', es: 'Datos frescos',     en: 'Fresh data' },
  DATOS_RETRASADOS:{ tone: 'amber',   es: 'Datos retrasados',  en: 'Delayed data' },
  LIVE_STALE:      { tone: 'red',     es: 'Live stale',        en: 'Live stale' },
  EXPIRED:         { tone: 'slate',   es: 'Expirado',          en: 'Expired' },
};

export function LiveFreshnessBadge({ freshness, lang = 'es', testId = 'live-freshness-badge' }) {
  if (!freshness) return null;
  const meta = FRESH_META[freshness.label] || FRESH_META.DATOS_RETRASADOS;
  const ageSec = freshness.heartbeat_age_sec;
  const ageLabel =
    ageSec == null ? '—' :
    ageSec < 60   ? `${ageSec}s` :
    ageSec < 3600 ? `${Math.floor(ageSec / 60)} min` :
    `${Math.floor(ageSec / 3600)} h`;
  const tooltip = lang === 'en'
    ? `Score ${freshness.score}/100 · heartbeat ${ageLabel} ago`
    : `Score ${freshness.score}/100 · heartbeat hace ${ageLabel}`;
  return (
    <span
      data-testid={testId}
      title={tooltip}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium ${TONE_CLS[meta.tone]}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.tone === 'emerald' ? 'bg-emerald-400' : meta.tone === 'amber' ? 'bg-amber-400' : 'bg-red-400'}`} aria-hidden />
      <span className="font-mono-tabular text-[10px]">{freshness.score}</span>
      <span>{lang === 'en' ? meta.en : meta.es}</span>
    </span>
  );
}
