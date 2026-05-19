import { useEffect, useMemo, useState } from 'react';
import {
  Filter, Download, X, Shield, Lock, Layers, Search, Zap,
  BookmarkPlus, Bookmark, Trash2, Save,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetTrigger, SheetFooter, SheetClose,
} from '@/components/ui/sheet';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ENGINE_STYLES, BUILTIN_VIEWS } from '@/lib/intelligence';

const ICON_MAP = { Shield, Lock, Layers, Search, Zap };
const MARKETS = ['1X2', 'Doble Oportunidad', 'Under 2.5', 'Under 3.5', 'Handicap Asiatico', 'Draw No Bet', 'DO 1er Tiempo', 'Moneyline'];
const MIN_CONFIDENCES = [0, 60, 70, 80];

const SAVED_VIEWS_KEY = 'vbi_saved_views';

function loadSavedViews() {
  try { return JSON.parse(localStorage.getItem(SAVED_VIEWS_KEY) || '[]'); }
  catch { return []; }
}

function saveSavedViews(views) {
  try { localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(views)); } catch (_) {}
}

function EnginePresetChip({ presetKey, active, onClick, lang }) {
  const meta = ENGINE_STYLES[presetKey];
  if (!meta) return null;
  const Icon = ICON_MAP[meta.icon] || Shield;
  const label = lang === 'en' ? meta.label_en : meta.label_es;
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => onClick(presetKey)}
            data-testid={`engine-preset-${presetKey}`}
            aria-pressed={active}
            className={`inline-flex shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-full border text-[12px] font-medium transition-all
              ${active
                ? `tone-${meta.tone} ring-1 ring-current/30 translate-y-[-1px]`
                : 'bg-secondary/40 border-border text-muted-foreground hover:text-foreground hover:border-border/80 hover:translate-y-[-1px]'}`}
          >
            <Icon className="h-3.5 w-3.5" />
            <span>{label}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent className="glass-surface text-xs max-w-[260px]">
          {lang === 'en'
            ? `Quick filter: ${label}. Click again to deselect.`
            : `Filtro rápido: ${label}. Click otra vez para deseleccionar.`}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * Public props
 *   filters: { league, market, minConfidence, enginePreset }
 *   onChange(nextFilters)
 *   onExportCsv()
 *   totalCount, filteredCount
 */
export function PicksFilterBar({ filters, onChange, onExportCsv, totalCount, filteredCount }) {
  const { t, lang } = useI18n();
  const [leagues, setLeagues] = useState([]);
  const [savedViews, setSavedViews] = useState(loadSavedViews);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [newViewName, setNewViewName] = useState('');

  useEffect(() => {
    api.get('/meta/leagues').then((r) => setLeagues(r.data.leagues || [])).catch(() => {});
  }, []);

  const enginePreset = filters.enginePreset || '';
  const active = useMemo(
    () => !!(filters.league || filters.market || (filters.minConfidence || 0) > 0 || enginePreset),
    [filters, enginePreset],
  );

  const reset = () => onChange({ league: '', market: '', minConfidence: 0, enginePreset: '' });

  const applyView = (view) => {
    if (!view) return;
    onChange({
      league: view.filters?.league || '',
      market: view.filters?.market || '',
      minConfidence: view.filters?.minConfidence || 0,
      enginePreset: view.enginePreset || '',
    });
    setSheetOpen(false);
  };

  const saveCurrent = () => {
    const name = newViewName.trim();
    if (!name) return;
    const id = `user:${Date.now().toString(36)}`;
    const v = {
      id,
      name_es: name,
      name_en: name,
      builtin: false,
      filters: {
        league: filters.league || '',
        market: filters.market || '',
        minConfidence: filters.minConfidence || 0,
      },
      enginePreset: enginePreset || undefined,
    };
    const next = [v, ...savedViews].slice(0, 12);
    setSavedViews(next);
    saveSavedViews(next);
    setNewViewName('');
  };

  const deleteView = (id) => {
    const next = savedViews.filter((v) => v.id !== id);
    setSavedViews(next);
    saveSavedViews(next);
  };

  const presetKeys = Object.keys(ENGINE_STYLES);

  return (
    <div data-testid="picks-filter-bar" className="rounded-xl border border-border bg-card/60 backdrop-blur p-3 space-y-2.5">
      {/* Row 1: Engine style preset chips (horizontal scroll on mobile) */}
      <div className="flex items-center gap-2" data-testid="engine-style-presets">
        <span className="micro-label shrink-0 hidden sm:inline">ENGINE</span>
        <ScrollArea className="w-full whitespace-nowrap">
          <div className="inline-flex items-center gap-1.5 pr-2">
            {presetKeys.map((k) => (
              <EnginePresetChip
                key={k}
                presetKey={k}
                active={enginePreset === k}
                onClick={(pk) => onChange({ ...filters, enginePreset: enginePreset === pk ? '' : pk })}
                lang={lang}
              />
            ))}
          </div>
          <ScrollBar orientation="horizontal" className="opacity-30" />
        </ScrollArea>
      </div>

      {/* Row 2: Existing field filters + saved views + reset */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground shrink-0">
          <Filter className="h-3.5 w-3.5" />
          {t.dashboard.filtersTitle}
        </div>

        <Select value={filters.league || '__all__'} onValueChange={(v) => onChange({ ...filters, league: v === '__all__' ? '' : v })}>
          <SelectTrigger className="h-8 w-[160px] text-xs" data-testid="filter-league-trigger">
            <SelectValue placeholder={t.dashboard.filterLeague} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">{t.dashboard.filterAll}</SelectItem>
            {leagues.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
          </SelectContent>
        </Select>

        <Select value={filters.market || '__all__'} onValueChange={(v) => onChange({ ...filters, market: v === '__all__' ? '' : v })}>
          <SelectTrigger className="h-8 w-[160px] text-xs" data-testid="filter-market-trigger">
            <SelectValue placeholder={t.dashboard.filterMarket} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">{t.dashboard.filterAll}</SelectItem>
            {MARKETS.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
          </SelectContent>
        </Select>

        <Select value={String(filters.minConfidence ?? 0)} onValueChange={(v) => onChange({ ...filters, minConfidence: Number(v) })}>
          <SelectTrigger className="h-8 w-[140px] text-xs" data-testid="filter-confidence-trigger">
            <SelectValue placeholder={t.dashboard.filterMinConfidence} />
          </SelectTrigger>
          <SelectContent>
            {MIN_CONFIDENCES.map((v) => <SelectItem key={v} value={String(v)}>{v === 0 ? t.dashboard.filterAll : `≥ ${v}`}</SelectItem>)}
          </SelectContent>
        </Select>

        {/* Saved views opens a Sheet */}
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetTrigger asChild>
            <Button variant="secondary" size="sm" data-testid="saved-views-open-button" className="h-8 text-xs">
              <Bookmark className="h-3.5 w-3.5 mr-1.5" />
              {lang === 'en' ? 'Views' : 'Vistas'}
              {savedViews.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-background/50 text-[10px] font-mono-tabular">{savedViews.length}</span>
              )}
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="glass-surface w-[360px] sm:w-[420px]">
            <SheetHeader>
              <SheetTitle>{lang === 'en' ? 'Filter views' : 'Vistas de filtros'}</SheetTitle>
              <SheetDescription>
                {lang === 'en' ? 'Apply a built-in preset or save the current filters.' : 'Aplica un preset incluido o guarda los filtros actuales.'}
              </SheetDescription>
            </SheetHeader>

            <div className="py-4 space-y-4 overflow-y-auto">
              <div>
                <div className="micro-label mb-2">{lang === 'en' ? 'BUILT-IN' : 'INCLUIDAS'}</div>
                <div className="flex flex-col gap-1.5">
                  {BUILTIN_VIEWS.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      onClick={() => applyView(v)}
                      data-testid={`builtin-view-${v.id}`}
                      className="flex items-center justify-between gap-3 px-3 py-2 rounded-md border border-border bg-secondary/30 hover:border-border/80 hover:bg-secondary/60 transition-colors text-left"
                    >
                      <div className="flex items-center gap-2">
                        <Bookmark className="h-3.5 w-3.5 text-cyan-300" />
                        <span className="text-[12.5px]">{lang === 'en' ? v.name_en : v.name_es}</span>
                      </div>
                      <span className="text-[10px] text-muted-foreground font-mono-tabular">
                        {v.filters?.minConfidence ? `≥${v.filters.minConfidence}` : v.enginePreset ? v.enginePreset : ''}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <div className="micro-label mb-2">{lang === 'en' ? 'YOUR VIEWS' : 'TUS VISTAS'}</div>
                {savedViews.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground italic">
                    {lang === 'en' ? 'No saved views yet.' : 'Aún no guardaste vistas.'}
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {savedViews.map((v) => (
                      <div key={v.id} className="flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-secondary/30">
                        <button
                          type="button"
                          onClick={() => applyView(v)}
                          className="flex-1 text-left text-[12.5px] truncate"
                          data-testid={`user-view-${v.id}`}
                        >
                          <Bookmark className="h-3.5 w-3.5 inline mr-1.5 text-emerald-300" />
                          {v.name_es || v.name_en}
                        </button>
                        <button
                          type="button"
                          onClick={() => deleteView(v.id)}
                          className="p-1 rounded text-muted-foreground hover:text-rose-300 hover:bg-rose-500/10 transition-colors"
                          aria-label="Delete view"
                          data-testid={`delete-view-${v.id}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="micro-label mb-2">{lang === 'en' ? 'SAVE CURRENT FILTERS' : 'GUARDAR FILTROS ACTUALES'}</div>
                <div className="flex items-center gap-2">
                  <Input
                    type="text"
                    value={newViewName}
                    onChange={(e) => setNewViewName(e.target.value)}
                    placeholder={lang === 'en' ? 'View name…' : 'Nombre de la vista…'}
                    className="h-8 text-xs"
                    data-testid="new-view-name-input"
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={saveCurrent}
                    disabled={!newViewName.trim() || !active}
                    data-testid="save-view-button"
                    className="h-8 text-xs"
                  >
                    <Save className="h-3.5 w-3.5 mr-1" />
                    {lang === 'en' ? 'Save' : 'Guardar'}
                  </Button>
                </div>
                {!active && (
                  <p className="text-[10.5px] text-muted-foreground mt-1.5">
                    {lang === 'en' ? 'Apply filters first, then save.' : 'Aplica filtros primero, luego guarda.'}
                  </p>
                )}
              </div>
            </div>

            <SheetFooter>
              <SheetClose asChild>
                <Button variant="outline" size="sm" className="h-8 text-xs">
                  {lang === 'en' ? 'Close' : 'Cerrar'}
                </Button>
              </SheetClose>
            </SheetFooter>
          </SheetContent>
        </Sheet>

        {active && (
          <Button variant="ghost" size="sm" onClick={reset} data-testid="filters-reset-button" className="h-8 text-xs">
            <X className="h-3.5 w-3.5 mr-1" />{t.dashboard.filterReset}
          </Button>
        )}

        {(filteredCount !== undefined && totalCount !== undefined) && (
          <span className="text-[11px] text-muted-foreground ml-1 font-mono-tabular" data-testid="filter-counts">
            {t.dashboard.filteredOf.replace('{kept}', filteredCount).replace('{total}', totalCount)}
          </span>
        )}

        <Button variant="secondary" size="sm" onClick={onExportCsv} data-testid="export-csv-btn" className="ml-auto h-8 text-xs">
          <Download className="h-3.5 w-3.5 mr-1.5" />{t.dashboard.exportCsv}
        </Button>
      </div>
    </div>
  );
}
