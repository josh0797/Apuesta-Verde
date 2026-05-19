import { Activity } from 'lucide-react';

const LEVELS = {
  1: { label_es: 'Mínima', label_en: 'Min', cls: 'bg-red-500/15 text-red-300 border-red-500/30' },
  2: { label_es: 'Baja', label_en: 'Low', cls: 'bg-orange-500/15 text-orange-300 border-orange-500/30' },
  3: { label_es: 'Media', label_en: 'Med', cls: 'bg-yellow-500/15 text-yellow-200 border-yellow-500/30' },
  4: { label_es: 'Alta', label_en: 'High', cls: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30' },
  5: { label_es: 'Máxima', label_en: 'Max', cls: 'bg-cyan-500/15 text-cyan-200 border-cyan-500/30' },
};

export function MotivationBadge({ level, lang = 'es', tooltip }) {
  const l = LEVELS[level] || LEVELS[3];
  const label = lang === 'es' ? l.label_es : l.label_en;
  return (
    <span
      title={tooltip}
      data-testid={`motivation-badge-${level}`}
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md border ${l.cls}`}
    >
      <Activity className="h-3 w-3" />
      <span className="mono font-mono-tabular">{level}</span>
      <span>{label}</span>
    </span>
  );
}
