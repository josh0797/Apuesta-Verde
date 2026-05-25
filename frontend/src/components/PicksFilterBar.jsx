import { useEffect, useMemo, useState, useCallback } from 'react';
import {
  Filter, Download, X, Shield, Lock, Layers, Search, Zap,
  Bookmark, Trash2, Save, Pencil, Check, Loader2, AlertTriangle,
} from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { useSport } from '@/lib/sport';
import { api } from '@/lib/api';
import { toast } from 'sonner';
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
const SAVED_VIEWS_MAX = 10;

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
  const { sport } = useSport();
  const [leagues, setLeagues] = useState([]);
  const [savedViews, setSavedViews] = useState([]);
  const [loadingViews, setLoadingViews] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [newViewName, setNewViewName] = useState('');
  const [savingNew, setSavingNew] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editingName, setEditingName] = useState('');
  const [editingBusy, setEditingBusy] = useState(false);

  useEffect(() => {
    api.get('/meta/leagues').then((r) => setLeagues(r.data.leagues || [])).catch(() => {});
  }, []);

  // Load saved views from backend (single source of truth)
  const fetchViews = useCallback(async () => {
    setLoadingViews(true);
    try {
      const r = await api.get('/profile/saved-views');
      setSavedViews(r.data.items || []);
    } catch (err) {
      console.error('Failed to load saved views', err);
    } finally {
      setLoadingViews(false);
    }
  }, []);

  useEffect(() => { fetchViews(); }, [fetchViews]);

  // Refresh views whenever the drawer opens (in case other tabs/devices changed them)
  useEffect(() => {
    if (sheetOpen) fetchViews();
  }, [sheetOpen, fetchViews]);

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
    toast.success(lang === 'en' ? `View "${view.name || view.name_es || view.name_en}" applied` : `Vista "${view.name || view.name_es || view.name_en}" aplicada`);
  };

  const atLimit = savedViews.length >= SAVED_VIEWS_MAX;

  const saveCurrent = async () => {
    const name = newViewName.trim();
    if (!name || !active || savingNew) return;
    setSavingNew(true);
    try {
      const payload = {
        name,
        filters: {
          league: filters.league || '',
          market: filters.market || '',
          minConfidence: filters.minConfidence || 0,
        },
        enginePreset: enginePreset || undefined,
        sport: sport || undefined,
      };
      const r = await api.post('/profile/saved-views', payload);
      const evicted = r.data?._evicted_id;
      setNewViewName('');
      await fetchViews();
      if (evicted) {
        toast.success(lang === 'en'
          ? `View saved. Oldest view was removed (limit ${SAVED_VIEWS_MAX}).`
          : `Vista guardada. Se eliminó la más antigua (límite ${SAVED_VIEWS_MAX}).`);
      } else {
        toast.success(lang === 'en' ? 'View saved' : 'Vista guardada');
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Failed to save view' : 'Error al guardar vista'));
    } finally {
      setSavingNew(false);
    }
  };

  const deleteView = async (id) => {
    try {
      await api.delete(`/profile/saved-views/${id}`);
      setSavedViews((prev) => prev.filter((v) => v.id !== id));
      toast.success(lang === 'en' ? 'View deleted' : 'Vista eliminada');
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Delete failed' : 'Error al eliminar'));
    }
  };

  const startEdit = (view) => {
    setEditingId(view.id);
    setEditingName(view.name || view.name_es || view.name_en || '');
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingName('');
  };

  const saveEditName = async (id) => {
    const trimmed = editingName.trim();
    if (!trimmed || editingBusy) return;
    setEditingBusy(true);
    try {
      const r = await api.patch(`/profile/saved-views/${id}`, { name: trimmed });
      setSavedViews((prev) => prev.map((v) => (v.id === id ? { ...v, ...r.data } : v)));
      toast.success(lang === 'en' ? 'View renamed' : 'Vista renombrada');
      cancelEdit();
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Edit failed' : 'Error al editar'));
    } finally {
      setEditingBusy(false);
    }
  };

  const updateViewWithCurrentFilters = async (id) => {
    if (!active) {
      toast.error(lang === 'en' ? 'No active filters to save' : 'No hay filtros activos para guardar');
      return;
    }
    setEditingBusy(true);
    try {
      const payload = {
        filters: {
          league: filters.league || '',
          market: filters.market || '',
          minConfidence: filters.minConfidence || 0,
        },
        enginePreset: enginePreset || '',
        sport: sport || undefined,
      };
      const r = await api.patch(`/profile/saved-views/${id}`, payload);
      setSavedViews((prev) => prev.map((v) => (v.id === id ? { ...v, ...r.data } : v)));
      toast.success(lang === 'en' ? 'View updated with current filters' : 'Vista actualizada con filtros actuales');
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Update failed' : 'Error al actualizar'));
    } finally {
      setEditingBusy(false);
    }
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
            <SelectValue placeholder={lang === 'en' ? 'All' : 'Todas'} />
          </SelectTrigger>
          <SelectContent>
            {MIN_CONFIDENCES.map((c) => (
              <SelectItem key={c} value={String(c)} data-testid={`confidence-option-${c}`}>
                {c === 0
                  ? (lang === 'en' ? 'All' : 'Todas')
                  : `≥ ${c}`}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Saved views opens a Sheet */}
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetTrigger asChild>
            <Button variant="secondary" size="sm" data-testid="saved-views-open-button" className="h-8 text-xs">
              <Bookmark className="h-3.5 w-3.5 mr-1.5" />
              {lang === 'en' ? 'Views' : 'Vistas'}
              {savedViews.length > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-background/50 text-[10px] font-mono-tabular" data-testid="saved-views-count">
                  {savedViews.length}
                </span>
              )}
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="glass-surface w-[360px] sm:w-[440px] flex flex-col">
            <SheetHeader>
              <SheetTitle>{lang === 'en' ? 'Filter views' : 'Vistas de filtros'}</SheetTitle>
              <SheetDescription>
                {lang === 'en'
                  ? 'Built-in presets or your saved views (synced across devices).'
                  : 'Presets incluidos o tus vistas guardadas (sincronizadas entre dispositivos).'}
              </SheetDescription>
            </SheetHeader>

            <div className="py-4 space-y-5 overflow-y-auto flex-1">
              {/* Built-in section */}
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

              {/* User views section */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="micro-label">{lang === 'en' ? 'YOUR VIEWS' : 'TUS VISTAS'}</div>
                  <span className="text-[10px] font-mono-tabular text-muted-foreground" data-testid="saved-views-counter">
                    {savedViews.length}/{SAVED_VIEWS_MAX}
                  </span>
                </div>
                {loadingViews ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {lang === 'en' ? 'Loading…' : 'Cargando…'}
                  </div>
                ) : savedViews.length === 0 ? (
                  <p className="text-[11px] text-muted-foreground italic" data-testid="no-saved-views">
                    {lang === 'en' ? 'No saved views yet.' : 'Aún no guardaste vistas.'}
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {savedViews.map((v) => {
                      const isEditing = editingId === v.id;
                      const description = [
                        v.filters?.league,
                        v.filters?.market,
                        v.filters?.minConfidence ? `≥${v.filters.minConfidence}` : null,
                        v.enginePreset,
                      ].filter(Boolean).join(' · ');
                      return (
                        <div
                          key={v.id}
                          className="flex flex-col gap-1 px-3 py-2 rounded-md border border-border bg-secondary/30 hover:border-border/80 transition-colors"
                          data-testid={`saved-view-row-${v.id}`}
                        >
                          {isEditing ? (
                            <div className="flex items-center gap-2">
                              <Input
                                type="text"
                                value={editingName}
                                onChange={(e) => setEditingName(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') saveEditName(v.id);
                                  if (e.key === 'Escape') cancelEdit();
                                }}
                                className="h-7 text-xs flex-1"
                                autoFocus
                                data-testid={`edit-view-name-input-${v.id}`}
                              />
                              <button
                                type="button"
                                onClick={() => saveEditName(v.id)}
                                disabled={editingBusy || !editingName.trim()}
                                className="p-1 rounded text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-40"
                                aria-label="Confirm rename"
                                data-testid={`confirm-edit-${v.id}`}
                              >
                                {editingBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                              </button>
                              <button
                                type="button"
                                onClick={cancelEdit}
                                disabled={editingBusy}
                                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-secondary/60"
                                aria-label="Cancel rename"
                                data-testid={`cancel-edit-${v.id}`}
                              >
                                <X className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => applyView(v)}
                                className="flex-1 text-left text-[12.5px] truncate"
                                data-testid={`user-view-${v.id}`}
                              >
                                <Bookmark className="h-3.5 w-3.5 inline mr-1.5 text-emerald-300" />
                                {v.name || v.name_es || v.name_en}
                              </button>
                              <TooltipProvider delayDuration={150}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <button
                                      type="button"
                                      onClick={() => updateViewWithCurrentFilters(v.id)}
                                      disabled={!active || editingBusy}
                                      className="p-1 rounded text-muted-foreground hover:text-cyan-300 hover:bg-cyan-500/10 transition-colors disabled:opacity-40"
                                      aria-label="Update with current filters"
                                      data-testid={`update-filters-${v.id}`}
                                    >
                                      <Save className="h-3.5 w-3.5" />
                                    </button>
                                  </TooltipTrigger>
                                  <TooltipContent className="glass-surface text-xs max-w-[220px]">
                                    {lang === 'en' ? 'Overwrite with current filters' : 'Sobrescribir con filtros actuales'}
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                              <button
                                type="button"
                                onClick={() => startEdit(v)}
                                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
                                aria-label="Rename view"
                                data-testid={`rename-view-${v.id}`}
                              >
                                <Pencil className="h-3.5 w-3.5" />
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
                          )}
                          {!isEditing && (description || (v.filters?.minConfidence || 0) > 0) && (
                            <div
                              className="text-[10.5px] text-muted-foreground truncate pl-[22px]"
                              title={[description, (v.filters?.minConfidence || 0) > 0 ? `≥${v.filters.minConfidence}% conf.` : ''].filter(Boolean).join(' · ')}
                            >
                              {[
                                description,
                                (v.filters?.minConfidence || 0) > 0
                                  ? `≥${v.filters.minConfidence}% conf.`
                                  : null,
                              ].filter(Boolean).join(' · ')}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Save current section */}
              <div>
                <div className="micro-label mb-2">{lang === 'en' ? 'SAVE CURRENT FILTERS' : 'GUARDAR FILTROS ACTUALES'}</div>
                <div className="flex items-center gap-2">
                  <Input
                    type="text"
                    value={newViewName}
                    onChange={(e) => setNewViewName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') saveCurrent(); }}
                    placeholder={lang === 'en' ? 'View name…' : 'Nombre de la vista…'}
                    className="h-8 text-xs"
                    data-testid="new-view-name-input"
                    maxLength={60}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={saveCurrent}
                    disabled={!newViewName.trim() || !active || savingNew}
                    data-testid="save-view-button"
                    className="h-8 text-xs"
                  >
                    {savingNew ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1" /> : <Save className="h-3.5 w-3.5 mr-1" />}
                    {lang === 'en' ? 'Save' : 'Guardar'}
                  </Button>
                </div>
                {!active && (
                  <p className="text-[10.5px] text-muted-foreground mt-1.5">
                    {lang === 'en' ? 'Apply filters first, then save.' : 'Aplica filtros primero, luego guarda.'}
                  </p>
                )}
                {atLimit && active && (
                  <div className="mt-1.5 flex items-start gap-1.5 text-[10.5px] text-amber-300/90" data-testid="saved-views-limit-warning">
                    <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                    <span>
                      {lang === 'en'
                        ? `Limit reached (${SAVED_VIEWS_MAX}). Saving will remove your oldest view.`
                        : `Límite alcanzado (${SAVED_VIEWS_MAX}). Guardar eliminará la vista más antigua.`}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <SheetFooter>
              <SheetClose asChild>
                <Button variant="outline" size="sm" className="h-8 text-xs" data-testid="saved-views-close">
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
