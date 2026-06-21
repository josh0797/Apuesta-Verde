/**
 * Sprint F.3 — Top365TrendsPanel
 * ================================
 *
 * Replaces the legacy "Revisión manual — alternativas posibles" block
 * with the real Top Trends scraped from 365Scores.
 *
 * Observe-only: every trend is evidence, never modifies picks/edge.
 *
 * Props
 * -----
 *   item          : the dashboard row (must expose `match_id`,
 *                    `home_team` / `away_team`, kickoff metadata, and
 *                    optionally `league` / `competition_id_365scores`
 *                    / `external_urls.365scores`).
 *   testId        : data-testid base (default "top365-trends")
 *   onlyTop       : when true, only items with isTop=true are rendered.
 *                    (defaults to false — show everything 365Scores
 *                    returned.)
 *   defaultOpen   : initial open state (default false — lazy fetch on
 *                    first expand).
 */
import { useState, useCallback, useMemo } from 'react';
import { ChevronDown, ChevronUp, Sparkles, AlertTriangle, RefreshCcw, Star, ExternalLink } from 'lucide-react';
import { useI18n } from '@/lib/i18n';
import { api } from '@/lib/api';

// ────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────
const MARKET_LABEL = {
  ML:          { es: 'Ganador',         en: 'Money Line' },
  OU_GOALS:    { es: 'Goles O/U',       en: 'Goals O/U' },
  '1H_ML':     { es: 'Ganador 1T',      en: '1H Winner' },
  FIRST_GOAL:  { es: 'Primer gol',      en: 'First goal' },
  BTTS:        { es: 'Ambos anotan',    en: 'BTTS' },
};

const SCOPE_LABEL = {
  home:        { es: 'Local',           en: 'Home' },
  away:        { es: 'Visita',          en: 'Away' },
  first_half:  { es: '1er tiempo',      en: '1st half' },
  all:         { es: 'General',         en: 'All' },
};

const TEAM_SIDE_LABEL = {
  home:        { es: 'Local',           en: 'Home' },
  away:        { es: 'Visita',          en: 'Away' },
  both:        { es: 'Ambos',           en: 'Both' },
  unknown:     { es: 'Equipo ?',         en: 'Team ?' },
};

const CONFIDENCE_CLS = {
  HIGH:    'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  MEDIUM:  'bg-amber-500/15  text-amber-200  border-amber-500/30',
  LOW:     'bg-slate-500/15  text-slate-300  border-slate-500/30',
};

const REASON_TEXT = {
  // F.2 reason codes
  F2_TOP_TRENDS_FOUND:        { es: 'Tendencias obtenidas de 365Scores.',                          en: 'Trends fetched from 365Scores.' },
  F2_FROM_CACHE:              { es: 'Tendencias desde caché (TTL vigente).',                       en: 'Trends from cache (TTL valid).' },
  F2_TOP_TRENDS_EMPTY:        { es: '365Scores no publicó tendencias para este partido.',          en: '365Scores did not publish trends for this match.' },
  F2_TRANSPORT_UNAVAILABLE:   { es: 'Transporte 365Scores no disponible (revisar Scrape.do).',     en: '365Scores transport unavailable (check Scrape.do).' },
  F2_TRANSPORT_ERROR:         { es: 'Error transitorio consultando 365Scores.',                    en: 'Transient error querying 365Scores.' },
  F2_PARSE_FAILED:            { es: '365Scores respondió, pero el formato es inválido.',           en: '365Scores responded but the format is invalid.' },
  F2_IDENTITY_NOT_RESOLVED:   { es: 'No se pudo identificar el partido en 365Scores.',             en: 'Could not identify this match on 365Scores.' },
  F2_GAME_ID_MISSING:         { es: 'Falta el game_id de 365Scores para este partido.',            en: 'Missing the 365Scores game_id for this match.' },
  // Identity reasons (forwarded when identity fails)
  F1_NO_CANDIDATES:           { es: '365Scores no tiene candidatos para este partido.',            en: 'No 365Scores candidates for this match.' },
  F1_MULTIPLE_CANDIDATES:     { es: '365Scores devolvió múltiples candidatos ambiguos.',           en: '365Scores returned multiple ambiguous candidates.' },
  F1_SOURCE_TIMEOUT_OR_ERROR: { es: '365Scores no respondió a tiempo.',                            en: '365Scores did not respond on time.' },
  F1_MISSING_REQUIRED_INPUTS: { es: 'Faltan datos canónicos del partido.',                         en: 'Missing canonical match inputs.' },
};

function reasonText(code, lang) {
  const entry = REASON_TEXT[code];
  if (!entry) return code || '';
  return entry[lang] || entry.es;
}

/**
 * Extract a usable kickoff ISO string from the dashboard row.
 *
 * Falls back across the most common field names seen on football
 * picks / discarded rows: `commence_time`, `kickoff_iso`,
 * `match_kickoff`, `start_time`.
 */
function extractKickoffIso(item) {
  return (
    item?.commence_time
    || item?.kickoff_iso
    || item?.match_kickoff
    || item?.start_time
    || item?.kickoff
    || null
  );
}

function extractTeamName(side, item) {
  const t = side === 'home' ? item?.home_team : item?.away_team;
  if (typeof t === 'string') return t;
  if (t && typeof t === 'object') return t.name || t.team_name || null;
  if (side === 'home') return item?.home_team_name || null;
  if (side === 'away') return item?.away_team_name || null;
  return null;
}

function extractMatchUrl(item) {
  // 365Scores URL can live in several places; we try the most common.
  return (
    item?.external_urls?.['365scores']
    || item?.external_urls?.score365
    || item?.external_ids?.['365scores']?.url
    || item?.match_url
    || null
  );
}

// ────────────────────────────────────────────────────────────────────────
// Trend row
// ────────────────────────────────────────────────────────────────────────
function TrendRow({ trend, lang, testId, idx }) {
  const marketLbl = (MARKET_LABEL[trend.market] || {})[lang]
                     || (MARKET_LABEL[trend.market] || {}).es
                     || trend.market;
  const scopeLbl = (SCOPE_LABEL[trend.scope] || {})[lang]
                    || (SCOPE_LABEL[trend.scope] || {}).es
                    || trend.scope;
  const sideLbl = (TEAM_SIDE_LABEL[trend.team_side] || {})[lang]
                   || (TEAM_SIDE_LABEL[trend.team_side] || {}).es
                   || trend.team_side;
  const confCls = CONFIDENCE_CLS[trend.confidence] || CONFIDENCE_CLS.LOW;
  const sample = trend.sample;
  const pct = trend.percentage != null ? Math.round(trend.percentage * 100) : null;
  return (
    <li
      className={`rounded-md border px-2.5 py-2 space-y-1.5 ${
        trend.is_top
          ? 'border-cyan-500/40 bg-cyan-500/5'
          : 'border-border/50 bg-background/30'
      }`}
      data-testid={`${testId}-row-${idx}`}
    >
      <div className="flex items-start gap-2 flex-wrap">
        {trend.is_top && (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide bg-cyan-500/15 text-cyan-200 border border-cyan-500/40"
            data-testid={`${testId}-row-${idx}-top-badge`}
          >
            <Star className="h-2.5 w-2.5" />
            Top
          </span>
        )}
        <span
          className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-500/15 text-slate-300 border border-slate-500/30"
          data-testid={`${testId}-row-${idx}-market`}
        >
          {marketLbl}
        </span>
        {trend.team_side !== 'unknown' && (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-500/10 text-slate-300 border border-slate-500/20"
            data-testid={`${testId}-row-${idx}-side`}
          >
            {sideLbl}
          </span>
        )}
        {trend.scope && trend.scope !== 'all' && trend.scope !== trend.team_side && (
          <span
            className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-500/10 text-slate-300 border border-slate-500/20"
            data-testid={`${testId}-row-${idx}-scope`}
          >
            {scopeLbl}
          </span>
        )}
        <span
          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${confCls}`}
          data-testid={`${testId}-row-${idx}-confidence`}
        >
          {trend.confidence}
        </span>
      </div>
      <div
        className="text-[12px] leading-snug text-foreground"
        data-testid={`${testId}-row-${idx}-text`}
      >
        {trend.raw}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {sample && (
          <span
            className="text-[10px] mono font-mono-tabular text-muted-foreground"
            data-testid={`${testId}-row-${idx}-sample`}
          >
            {sample.hits}/{sample.total}
          </span>
        )}
        {pct != null && (
          <div
            className="flex-1 min-w-[60px] max-w-[140px] h-1.5 rounded-full bg-slate-500/20 overflow-hidden"
            data-testid={`${testId}-row-${idx}-bar`}
          >
            <div
              className="h-full bg-cyan-400/70"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        {pct != null && (
          <span
            className="text-[10px] mono font-mono-tabular text-cyan-300"
            data-testid={`${testId}-row-${idx}-pct`}
          >
            {pct}%
          </span>
        )}
      </div>
    </li>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Empty / error states (no fabricated trends — observe_only)
// ────────────────────────────────────────────────────────────────────────
function StatusBlock({ tone, icon: Icon, title, message, testId }) {
  const toneCls = tone === 'warn'
    ? 'border-amber-500/30 bg-amber-500/5 text-amber-100'
    : 'border-slate-500/30 bg-slate-500/5 text-slate-300';
  return (
    <div
      className={`rounded-md border px-3 py-2.5 flex items-start gap-2 ${toneCls}`}
      data-testid={`${testId}-status`}
    >
      {Icon && <Icon className="h-4 w-4 mt-0.5 shrink-0" />}
      <div className="space-y-0.5">
        <div className="text-[11px] font-semibold uppercase tracking-wide">{title}</div>
        <div className="text-[11px] leading-snug">{message}</div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Main panel
// ────────────────────────────────────────────────────────────────────────
export function Top365TrendsPanel({
  item, testId = 'top365-trends',
  onlyTop = false, defaultOpen = false,
}) {
  const { lang } = useI18n();
  const [open, setOpen] = useState(Boolean(defaultOpen));
  const [state, setState] = useState({ loading: false, fetched: false,
                                         data: null, error: null });

  const commenceIso = useMemo(() => extractKickoffIso(item), [item]);
  const homeName = useMemo(() => extractTeamName('home', item), [item]);
  const awayName = useMemo(() => extractTeamName('away', item), [item]);
  const matchUrl = useMemo(() => extractMatchUrl(item), [item]);
  const internalId = useMemo(
    () => item?.match_id || item?.id || item?.fixture_id || '',
    [item],
  );

  // Sprint-D9 fix: solo el `internal_match_id` es estrictamente requerido.
  // Los demás campos (home/away/commence_time) ahora los completa el
  // backend desde Mongo cuando faltan. Antes el frontend bloqueaba con
  // F2_IDENTITY_REQUIRED espurio para picks de shape parcial.
  const canFetch = Boolean(internalId);

  const doFetch = useCallback(async (forceRefresh = false) => {
    if (!canFetch) {
      setState({ loading: false, fetched: true, data: null,
                  error: { reason_code: 'F2_IDENTITY_REQUIRED',
                            message: lang === 'en'
                              ? 'Match internal id is missing — cannot query 365Scores.'
                              : 'Falta el id interno del partido — no se puede consultar 365Scores.' } });
      return;
    }
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const res = await api.post('/football/365scores/top-trends', {
        internal_match_id: String(internalId),
        home_team:         homeName || undefined,
        away_team:         awayName || undefined,
        commence_time:     commenceIso || undefined,
        competition:       item?.league || item?.competition || null,
        competition_id:    item?.competition_id_365scores || null,
        match_url:         matchUrl,
        language:          lang === 'en' ? 'en' : 'es',
        only_top:          Boolean(onlyTop),
        force_refresh:     Boolean(forceRefresh),
      });
      setState({ loading: false, fetched: true, data: res.data, error: null });
    } catch (err) {
      setState({ loading: false, fetched: true, data: null,
                  error: { reason_code: 'NETWORK_ERROR',
                            message: err?.message || 'Network error' } });
    }
  }, [canFetch, internalId, homeName, awayName, commenceIso, matchUrl, item, lang, onlyTop]);

  const handleToggle = useCallback(() => {
    setOpen((wasOpen) => {
      const next = !wasOpen;
      if (next && !state.fetched && !state.loading) {
        doFetch(false);
      }
      return next;
    });
  }, [state.fetched, state.loading, doFetch]);

  const handleRefresh = useCallback((e) => {
    e?.stopPropagation?.();
    doFetch(true);
  }, [doFetch]);

  const trends = state.data?.trends || [];
  const visibleTrends = onlyTop ? trends.filter((t) => t.is_top) : trends;
  const headerCount = state.data?.trends_count != null
    ? state.data.trends_count
    : (visibleTrends.length || null);

  return (
    <div
      className="rounded-md border border-cyan-500/20 bg-cyan-500/5"
      data-testid={testId}
    >
      <button
        type="button"
        onClick={handleToggle}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-cyan-500/10 transition-colors rounded-md"
        data-testid={`${testId}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="h-3.5 w-3.5 text-cyan-300 shrink-0" />
          <span className="text-[11px] uppercase tracking-wide text-cyan-200 font-semibold">
            {lang === 'en' ? 'Top Trends — 365Scores' : 'Tendencias Top — 365Scores'}
          </span>
          {headerCount != null && (
            <span
              className="text-[10px] mono font-mono-tabular text-cyan-200 border border-cyan-500/30 bg-cyan-500/10 rounded px-1.5"
              data-testid={`${testId}-count`}
            >
              {headerCount}
            </span>
          )}
          {state.data?.from_cache && (
            <span
              className="text-[9px] uppercase tracking-wide text-slate-300 border border-slate-500/30 bg-slate-500/10 rounded px-1.5"
              data-testid={`${testId}-cache-badge`}
            >
              cache
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {open && state.fetched && (
            <span
              role="button"
              tabIndex={0}
              onClick={handleRefresh}
              onKeyDown={(e) => { if (e.key === 'Enter') handleRefresh(e); }}
              className="text-cyan-300 hover:text-cyan-200 p-1 rounded hover:bg-cyan-500/15"
              data-testid={`${testId}-refresh`}
              title={lang === 'en' ? 'Refresh' : 'Refrescar'}
            >
              <RefreshCcw className="h-3.5 w-3.5" />
            </span>
          )}
          {open
            ? <ChevronUp className="h-4 w-4 text-cyan-300" />
            : <ChevronDown className="h-4 w-4 text-cyan-300" />}
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2" data-testid={`${testId}-body`}>
          {state.loading && (
            <div
              className="flex items-center gap-2 text-[11px] text-muted-foreground"
              data-testid={`${testId}-loading`}
            >
              <RefreshCcw className="h-3.5 w-3.5 animate-spin" />
              {lang === 'en' ? 'Loading trends from 365Scores…'
                              : 'Cargando tendencias de 365Scores…'}
            </div>
          )}
          {!state.loading && state.error && (
            <StatusBlock
              tone="warn" icon={AlertTriangle}
              title={state.error.reason_code}
              message={state.error.message}
              testId={testId}
            />
          )}
          {!state.loading && !state.error && state.data && (
            <>
              {!state.data.available && (
                <StatusBlock
                  tone="warn" icon={AlertTriangle}
                  title={state.data.reason_code || 'F2_UNKNOWN'}
                  message={reasonText(state.data.reason_code, lang)}
                  testId={testId}
                />
              )}
              {state.data.available && visibleTrends.length === 0 && (
                <StatusBlock
                  tone="info"
                  title={state.data.reason_code || 'F2_TOP_TRENDS_EMPTY'}
                  message={lang === 'en'
                    ? 'No qualifying top trends for this match.'
                    : 'Sin tendencias destacadas para este partido.'}
                  testId={testId}
                />
              )}
              {state.data.available && visibleTrends.length > 0 && (
                <ul className="space-y-2" data-testid={`${testId}-list`}>
                  {visibleTrends.map((t, i) => (
                    <TrendRow
                      key={t.trend_id || `${i}-${t.raw}`}
                      trend={t} lang={lang} testId={testId} idx={i}
                    />
                  ))}
                </ul>
              )}
              {/* Footer: observe-only + provenance */}
              <div
                className="text-[10px] text-muted-foreground italic flex items-center gap-1 pt-1"
                data-testid={`${testId}-footer`}
              >
                <span>observe_only</span>
                {state.data.identity?.game_id && (
                  <>
                    <span>·</span>
                    <span className="mono font-mono-tabular">
                      game_id={state.data.identity.game_id}
                    </span>
                  </>
                )}
                {matchUrl && (
                  <>
                    <span>·</span>
                    <a
                      href={matchUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-cyan-300 hover:underline inline-flex items-center gap-0.5"
                      data-testid={`${testId}-source-link`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      365Scores
                      <ExternalLink className="h-2.5 w-2.5" />
                    </a>
                  </>
                )}
              </div>
            </>
          )}
          {!state.loading && !state.fetched && (
            <div className="text-[11px] text-muted-foreground italic">
              {lang === 'en' ? 'Click to load.' : 'Clic para cargar.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default Top365TrendsPanel;
