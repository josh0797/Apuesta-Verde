import { useEffect, useState } from 'react';
import { Filter, Download, X } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const MARKETS = [
  '1X2',
  'Doble Oportunidad',
  'Under 2.5',
  'Under 3.5',
  'Handicap Asiatico',
  'Draw No Bet',
  'DO 1er Tiempo',
];
const MIN_CONFIDENCES = [0, 68, 78, 88];

export function PicksFilterBar({ filters, onChange, onExportCsv, totalCount, filteredCount }) {
  const { t } = useI18n();
  const [leagues, setLeagues] = useState([]);

  useEffect(() => {
    api.get('/meta/leagues').then((r) => setLeagues(r.data.leagues || [])).catch(() => {});
  }, []);

  const reset = () => onChange({ league: '', market: '', minConfidence: 0 });
  const active = (filters.league || filters.market || filters.minConfidence > 0);

  return (
    <div className="rounded-xl border border-border bg-card/60 backdrop-blur p-3 flex flex-wrap items-center gap-3" data-testid="picks-filter-bar">
      <div className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
        <Filter className="h-3.5 w-3.5" />
        {t.dashboard.filtersTitle}
      </div>

      <Select value={filters.league || '__all__'} onValueChange={(v) => onChange({ ...filters, league: v === '__all__' ? '' : v })}>
        <SelectTrigger className="h-8 w-[180px] text-xs" data-testid="filter-league-trigger">
          <SelectValue placeholder={t.dashboard.filterLeague} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">{t.dashboard.filterAll}</SelectItem>
          {leagues.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
        </SelectContent>
      </Select>

      <Select value={filters.market || '__all__'} onValueChange={(v) => onChange({ ...filters, market: v === '__all__' ? '' : v })}>
        <SelectTrigger className="h-8 w-[180px] text-xs" data-testid="filter-market-trigger">
          <SelectValue placeholder={t.dashboard.filterMarket} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">{t.dashboard.filterAll}</SelectItem>
          {MARKETS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
        </SelectContent>
      </Select>

      <Select value={String(filters.minConfidence ?? 0)} onValueChange={(v) => onChange({ ...filters, minConfidence: Number(v) })}>
        <SelectTrigger className="h-8 w-[160px] text-xs" data-testid="filter-confidence-trigger">
          <SelectValue placeholder={t.dashboard.filterMinConfidence} />
        </SelectTrigger>
        <SelectContent>
          {MIN_CONFIDENCES.map((v) => <SelectItem key={v} value={String(v)}>{v === 0 ? t.dashboard.filterAll : `≥ ${v}`}</SelectItem>)}
        </SelectContent>
      </Select>

      {active && (
        <Button variant="ghost" size="sm" onClick={reset} data-testid="filter-reset-btn" className="h-8 text-xs">
          <X className="h-3.5 w-3.5 mr-1" />{t.dashboard.filterReset}
        </Button>
      )}

      {(filteredCount !== undefined && totalCount !== undefined) && (
        <span className="text-[11px] text-muted-foreground ml-1">
          {t.dashboard.filteredOf.replace('{kept}', filteredCount).replace('{total}', totalCount)}
        </span>
      )}

      <Button variant="secondary" size="sm" onClick={onExportCsv} data-testid="export-csv-btn" className="ml-auto h-8 text-xs">
        <Download className="h-3.5 w-3.5 mr-1.5" />{t.dashboard.exportCsv}
      </Button>
    </div>
  );
}
