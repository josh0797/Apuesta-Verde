/**
 * F94.2 — WorldCupLiveCard
 *
 * Dedicated card for senior FIFA World Cup live fixtures. Pinned above
 * the rest of the visibility strip so the user immediately spots that
 * a World Cup match is in progress, even when:
 *   - API-Football mis-classified it as exotic/low priority.
 *   - SportyTrader has no card.
 *   - No odds are available yet (analysis_status === 'VISIBLE_PENDING_MARKET').
 *
 * Behavior per F94.2 spec:
 *   - World Cup is ALWAYS visible (never hidden by any filter).
 *   - When the fixture is pending market (no odds), surface a manual
 *     odds CTA in the F93 style. Tap → reveals an inline input that
 *     captures the bookie odds and persists them locally so the user
 *     can audit/decide later without losing the data.
 *   - The manual capture writes to localStorage under
 *     `wc_manual_odds:<fixture_id>` so the experience survives page
 *     reloads / refreshes. (Wiring this to a backend endpoint is out
 *     of scope for this sprint and will be tackled when the WC analyze
 *     pipeline is ready.)
 */
import { useMemo, useState } from 'react';
import {
  Trophy, AlertTriangle, ChevronDown, ChevronUp, Check,
  PencilLine, Globe,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';

const STORAGE_PREFIX = 'wc_manual_odds:';

/* ------------------------------------------------------------------ */
/* Helpers                                                            */
/* ------------------------------------------------------------------ */

function pendingMarket(item) {
  const sec = Array.isArray(item?.secondary_reasons)
    ? item.secondary_reasons
    : [];
  return sec.includes('VISIBLE_PENDING_MARKET');
}

function readSavedOdds(fixtureId) {
  if (!fixtureId) return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + fixtureId);
    if (!raw) return null;
    const v = parseFloat(raw);
    return Number.isFinite(v) && v > 1 ? v : null;
  } catch {
    return null;
  }
}

function writeSavedOdds(fixtureId, value) {
  if (!fixtureId) return;
  try {
    if (value == null || value === '') {
      window.localStorage.removeItem(STORAGE_PREFIX + fixtureId);
    } else {
      window.localStorage.setItem(STORAGE_PREFIX + fixtureId, String(value));
    }
  } catch {
    /* fail-soft */
  }
}

/* ------------------------------------------------------------------ */
/* ManualOddsInline                                                   */
/* ------------------------------------------------------------------ */

function ManualOddsInline({ fixtureId, lang, testId }) {
  const [open,    setOpen]    = useState(false);
  const [value,   setValue]   = useState('');
  // Bump this counter every time we write/clear localStorage so the
  // derived `saved` value re-renders without needing setState-in-effect.
  const [version, setVersion] = useState(0);

  // Derived directly from storage on every render (cheap, no setState).
  // `version` is in the dep chain so React re-evaluates after submit/clear.
  // eslint-disable-next-line no-unused-vars
  const _bump   = version;
  const saved   = readSavedOdds(fixtureId);

  const submit = () => {
    const normalized = String(value).trim().replace(',', '.');
    const num = parseFloat(normalized);
    if (!Number.isFinite(num) || num <= 1) return;
    writeSavedOdds(fixtureId, num);
    setVersion((v) => v + 1);
    setValue('');
    setOpen(false);
  };

  const clear = () => {
    writeSavedOdds(fixtureId, null);
    setVersion((v) => v + 1);
    setValue('');
  };

  if (saved != null && !open) {
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <Badge
          variant="outline"
          className="text-[11px] font-mono border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
          data-testid={`${testId}-saved`}
        >
          <Check className="h-3 w-3 mr-1" />
          {lang === 'en' ? 'Saved odds:' : 'Cuota guardada:'}{' '}
          <span className="font-bold ml-1">{saved}</span>
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => { setValue(String(saved)); setOpen(true); }}
          className="h-7 px-2 text-[11px] text-amber-200 hover:bg-amber-500/15"
          data-testid={`${testId}-edit`}
        >
          <PencilLine className="h-3 w-3 mr-1" />
          {lang === 'en' ? 'Edit' : 'Editar'}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={clear}
          className="h-7 px-2 text-[11px] text-rose-200 hover:bg-rose-500/15"
          data-testid={`${testId}-clear`}
        >
          {lang === 'en' ? 'Clear' : 'Borrar'}
        </Button>
      </div>
    );
  }

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="h-7 px-2 text-[11px] border-amber-500/40 text-amber-100 bg-amber-500/10 hover:bg-amber-500/20"
        data-testid={`${testId}-open`}
      >
        <PencilLine className="h-3 w-3 mr-1" />
        {lang === 'en' ? 'Add manual odds' : 'Ingresar cuota manual'}
      </Button>
    );
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Input
        autoFocus
        type="text"
        inputMode="decimal"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
        placeholder={lang === 'en' ? 'e.g. 2.10' : 'ej. 2.10'}
        className="h-7 w-24 text-[12px] font-mono bg-background/60 border-amber-500/30"
        data-testid={`${testId}-input`}
      />
      <Button
        size="sm"
        onClick={submit}
        className="h-7 px-2 text-[11px] bg-amber-500/30 text-amber-50 hover:bg-amber-500/40"
        data-testid={`${testId}-save`}
      >
        {lang === 'en' ? 'Save' : 'Guardar'}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => { setOpen(false); setValue(''); }}
        className="h-7 px-2 text-[11px]"
        data-testid={`${testId}-cancel`}
      >
        {lang === 'en' ? 'Cancel' : 'Cancelar'}
      </Button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* WorldCupLiveCard                                                   */
/* ------------------------------------------------------------------ */

export function WorldCupLiveCard({
  items = [],
  worldCupDebug,
  lang = 'es',
  testId = 'world-cup-live-card',
}) {
  // Filter only senior World Cup fixtures (already classified by backend).
  const wcItems = useMemo(
    () => (Array.isArray(items)
      ? items.filter((it) => Boolean(it?._is_world_cup))
      : []),
    [items],
  );

  const [expanded, setExpanded] = useState(true);

  if (wcItems.length === 0) return null;

  const totalLive   = Number(worldCupDebug?.world_cup_live_count ?? wcItems.length);
  const hiddenCount = Number(worldCupDebug?.world_cup_hidden_by_filter ?? 0);

  return (
    <div
      className="rounded-xl border border-amber-300/40 bg-gradient-to-br from-amber-500/10 via-amber-500/5 to-transparent p-3 flex flex-col gap-2.5 noise-overlay"
      data-testid={testId}
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Trophy className="h-4 w-4 text-amber-300 shrink-0" />
          <span
            className="text-sm font-semibold text-amber-100"
            data-testid={`${testId}-title`}
          >
            {lang === 'en'
              ? `FIFA World Cup live — ${totalLive} match${totalLive === 1 ? '' : 'es'}`
              : `FIFA Copa del Mundo en vivo — ${totalLive} partido${totalLive === 1 ? '' : 's'}`}
          </span>
          {hiddenCount > 0 && (
            <Badge
              variant="outline"
              className="text-[10px] font-mono border-rose-500/40 bg-rose-500/10 text-rose-200"
              data-testid={`${testId}-hidden-warn`}
            >
              <AlertTriangle className="h-3 w-3 mr-1" />
              {lang === 'en'
                ? `${hiddenCount} hidden — contract violation`
                : `${hiddenCount} oculto(s) — violación de contrato`}
            </Badge>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          className="h-7 px-2 text-amber-200 hover:bg-amber-500/15 text-[11px]"
          data-testid={`${testId}-toggle`}
        >
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5" />
            : <ChevronDown className="h-3.5 w-3.5" />}
          {expanded
            ? (lang === 'en' ? 'Collapse' : 'Colapsar')
            : (lang === 'en' ? 'Expand'   : 'Expandir')}
        </Button>
      </div>

      {expanded && (
        <div className="flex flex-col gap-2" data-testid={`${testId}-list`}>
          {wcItems.map((it) => {
            const fid  = it?.fixture_id || `${it?.teams?.home?.name}-${it?.teams?.away?.name}`;
            const home = it?.teams?.home?.name || '—';
            const away = it?.teams?.away?.name || '—';
            const lg   = it?.league?.name || 'FIFA World Cup';
            const ctr  = it?.league?.country || 'World';
            const min  = it?.elapsed != null ? `${it.elapsed}'` : (it?.status_short || 'LIVE');
            const pending = pendingMarket(it);

            return (
              <div
                key={fid}
                className="rounded-lg border border-amber-300/30 bg-background/60 p-2.5 flex flex-col gap-1.5"
                data-testid={`${testId}-row-${fid}`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className="text-[11px] font-mono tabular-nums text-amber-300 shrink-0"
                    data-testid={`${testId}-row-${fid}-minute`}
                  >
                    {min}
                  </span>
                  <span
                    className="text-[13px] font-semibold text-foreground truncate"
                    data-testid={`${testId}-row-${fid}-teams`}
                  >
                    {home} <span className="text-muted-foreground">vs</span> {away}
                  </span>
                  <Badge
                    variant="outline"
                    className="text-[10px] font-mono border-amber-300/40 bg-amber-500/10 text-amber-200"
                    data-testid={`${testId}-row-${fid}-league`}
                  >
                    <Globe className="h-2.5 w-2.5 mr-1" />
                    {lg}{ctr && ctr !== lg ? ` · ${ctr}` : ''}
                  </Badge>
                </div>

                {pending && (
                  <div
                    className="text-[11px] text-amber-200/85 italic flex items-center gap-1.5"
                    data-testid={`${testId}-row-${fid}-pending`}
                  >
                    <AlertTriangle className="h-3 w-3" />
                    {lang === 'en'
                      ? 'Visible / pending market (no odds yet) — capture them manually below.'
                      : 'Visible / pendiente de mercado (sin cuotas aún) — captúralas manualmente abajo.'}
                  </div>
                )}

                {/* F93-style manual odds CTA — ALWAYS available for WC */}
                <ManualOddsInline
                  fixtureId={fid}
                  lang={lang}
                  testId={`${testId}-row-${fid}-manual-odds`}
                />
              </div>
            );
          })}
        </div>
      )}

      <div
        className="text-[10.5px] text-amber-200/70 italic"
        data-testid={`${testId}-footnote`}
      >
        {lang === 'en'
          ? 'Per F94.2, the World Cup is always visible — never hidden by filters.'
          : 'Según F94.2, la Copa del Mundo es siempre visible — nunca se oculta por filtros.'}
      </div>
    </div>
  );
}

export default WorldCupLiveCard;
