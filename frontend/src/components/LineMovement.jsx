import { ArrowUpRight, Minus, ArrowDownRight, HelpCircle } from 'lucide-react';
import { useI18n } from '@/lib/i18n';

export function LineMovement({ movement = 'desconocido' }) {
  const { t } = useI18n();
  const map = {
    estable: { Icon: Minus, cls: 'text-slate-300', key: 'estable' },
    subiendo: { Icon: ArrowUpRight, cls: 'text-emerald-300', key: 'subiendo' },
    bajando: { Icon: ArrowDownRight, cls: 'text-red-300', key: 'bajando' },
    desconocido: { Icon: HelpCircle, cls: 'text-muted-foreground', key: 'desconocido' },
  };
  const m = map[movement] || map.desconocido;
  const Icon = m.Icon;
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${m.cls}`} data-testid={`line-movement-${m.key}`}>
      <Icon className="h-3.5 w-3.5" />
      <span>{t.match[m.key] || movement}</span>
    </span>
  );
}
