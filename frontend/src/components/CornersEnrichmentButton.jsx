import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Loader2, Flag, RefreshCw, AlertCircle, CheckCircle2,
  TrendingUp, TrendingDown, Minus, Link2, Link2Off, Target,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';

/**
 * CornersEnrichmentButton — manual trigger for the 365Scores corners
 * enrichment on a football match.
 *
 * Flow:
 *   1. Click → POST /football/corners-enrichment/run-now (sync, hard 8s timeout).
 *   2. If status === "TIMEOUT" → automatic fallback to POST /background,
 *      then poll GET /status/{match_id} every 3s (max ~60s).
 *   3. On SUCCESS → update local state with snapshot + show mini summary.
 *   4. On UNAVAILABLE/ERROR → toast error, expose retry.
 *
 * Hidden for non-football sports.
 *
 * Phase F82.2 — Scores24 has been deprecated as the external confirmator.
 * All labels now say "365Scores" and read the new audit field
 * ``football_corner_365_cross_applied`` (with legacy fallback to
 * ``football_corner_cross_applied`` for picks generated before F82.2).
 */
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_TICKS   = 20; // ≈ 60s total

export function CornersEnrichmentButton({ match, lang = 'es' }) {
  const matchId  = match?.match_id;
  const sport    = match?.sport || 'football';
  const initial  = match?.corners_snapshot
                || match?.football_data_enrichment?.corners
                || null;

  // F59/F60 + F82.2 — Engine cross profile L5 vs L15 (already computed
  // during analysis). Two known locations:
  //   1. snake_case as written by football_corner_cross_integration.
  //   2. camelCase mirror under footballHistoricalProfile (UI convention).
  const crossProfile = match?.combined_football_corner_profile_cross
                    || match?.footballHistoricalProfile?.combinedFootballCornerProfileCross
                    || null;
  // Phase F82.2 — prefer the new 365Scores audit; fall back to the
  // legacy Scores24 audit for backwards-compat with old payloads.
  const crossAudit   = match?.football_corner_365_cross_applied
                    || match?.football_corner_cross_applied
                    || null;

  const [snapshot,  setSnapshot]  = useState(initial);
  const [state,     setState]     = useState('idle');     // idle|loading|polling|done|error
  const [error,     setError]     = useState(null);
  const pollRef = useRef({ ticks: 0, timer: null });

  const T = lang === 'en'
    ? {
        title:        'Corners (365Scores)',
        cta:          'Fetch corners stats',
        refresh:      'Refresh',
        loading:      'Fetching…',
        polling:      'Running in background — polling…',
        timeoutNote:  'Slow response — switched to background.',
        empty:        '365Scores returned no usable data.',
        errorPrefix:  'Corners fetch failed',
        avgFor:       'Avg corners (for)',
        avgAgainst:   'Avg corners (against)',
        over95Rate:   'Over 9.5 rate',
        source:       'Source',
        updated:      'Updated',
        // Cross profile — Phase F82.2: Scores24 → 365Scores labels.
        crossTitle:   'Engine cross profile (L5 vs L15)',
        supports:     'Engine supports',
        confDelta:    'Confidence Δ',
        fragDelta:    'Fragility Δ',
        l5:           'L5',
        l15:          'L15',
        forShort:     'For',
        againstShort: 'Against',
        extConfirms:  '365Scores confirms',
        extConflict:  '365Scores conflicts',
        gateBlocked:  'External confirmation unavailable',
        marketFamily: 'Suggested market',
      }
    : {
        title:        'Córners (365Scores)',
        cta:          'Cargar stats de córners',
        refresh:      'Refrescar',
        loading:      'Consultando…',
        polling:      'Ejecutando en segundo plano…',
        timeoutNote:  'Respuesta lenta — se movió a background.',
        empty:        '365Scores no devolvió datos utilizables.',
        errorPrefix:  'Falló la carga de córners',
        avgFor:       'Promedio córners (a favor)',
        avgAgainst:   'Promedio córners (en contra)',
        over95Rate:   'Tasa Over 9.5',
        source:       'Fuente',
        updated:      'Actualizado',
        // Cross profile — Phase F82.2: Scores24 → 365Scores labels.
        crossTitle:   'Perfil cruzado del engine (L5 vs L15)',
        supports:     'El engine apunta a',
        confDelta:    'Δ Confianza',
        fragDelta:    'Δ Fragilidad',
        l5:           'L5',
        l15:          'L15',
        forShort:     'A favor',
        againstShort: 'En contra',
        extConfirms:  '365Scores confirma',
        extConflict:  '365Scores contradice',
        gateBlocked:  'Confirmación externa no disponible',
        marketFamily: 'Mercado sugerido',
      };

  const clearPoll = () => {
    if (pollRef.current.timer) {
      clearTimeout(pollRef.current.timer);
      pollRef.current.timer = null;
    }
    pollRef.current.ticks = 0;
  };

  useEffect(() => () => clearPoll(), []);

  // Hide for non-football matches. The early-return MUST sit after all
  // hooks (useState / useRef / useEffect) so React's hook ordering
  // contract isn't broken when the same component re-renders with a
  // non-football match between renders.
  if (sport !== 'football' || !matchId) return null;

  const handleSuccess = (result) => {
    setSnapshot(result);
    setState('done');
    setError(null);
  };

  const handleUnavailable = (msg) => {
    setState('error');
    setError(msg || T.empty);
    try { toast.error(msg || T.empty); } catch (_e) { /* sonner unavailable */ }
  };

  const pollStatus = () => {
    pollRef.current.ticks += 1;
    if (pollRef.current.ticks > POLL_MAX_TICKS) {
      clearPoll();
      handleUnavailable(T.errorPrefix + ' (timeout)');
      return;
    }
    api.get(`/football/corners-enrichment/status/${encodeURIComponent(matchId)}`)
      .then((res) => {
        const data = res?.data || res || {};
        const st   = data.status;
        if (st === 'SUCCESS') {
          clearPoll();
          handleSuccess(data.result || {});
        } else if (st === 'FAILED' || st === 'NOT_FOUND') {
          clearPoll();
          handleUnavailable(data.result?.reason_codes?.[0] || T.empty);
        } else {
          // QUEUED or RUNNING — keep polling
          pollRef.current.timer = setTimeout(pollStatus, POLL_INTERVAL_MS);
        }
      })
      .catch((e) => {
        clearPoll();
        const msg = e?.response?.data?.detail || e?.message || T.errorPrefix;
        handleUnavailable(typeof msg === 'string' ? msg : T.errorPrefix);
      });
  };

  const triggerBackground = async () => {
    setState('polling');
    try {
      await api.post('/football/corners-enrichment/background', { match_id: matchId });
      try { toast.message(T.timeoutNote); } catch (_e) { /* sonner unavailable */ }
      pollRef.current.timer = setTimeout(pollStatus, POLL_INTERVAL_MS);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || T.errorPrefix;
      handleUnavailable(typeof msg === 'string' ? msg : T.errorPrefix);
    }
  };

  const trigger = async () => {
    if (state === 'loading' || state === 'polling') return;
    setState('loading');
    setError(null);
    try {
      const res  = await api.post('/football/corners-enrichment/run-now', { match_id: matchId });
      const data = res?.data || res || {};
      const st   = data.status;
      if (st === 'SUCCESS') {
        handleSuccess(data);
      } else if (st === 'TIMEOUT') {
        // Hard fallback: queue background job + poll
        await triggerBackground();
      } else if (st === 'MATCH_NOT_FOUND') {
        handleUnavailable('MATCH_NOT_FOUND');
      } else {
        // UNAVAILABLE / ERROR / unknown
        handleUnavailable(data.reason_codes?.[0] || T.empty);
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || T.errorPrefix;
      handleUnavailable(typeof msg === 'string' ? msg : T.errorPrefix);
    }
  };

  const busy   = state === 'loading' || state === 'polling';
  const hasSnap = snapshot && snapshot.available === true;

  return (
    <div
      className="rounded-lg border border-border/60 bg-background/30 p-3 flex flex-col gap-2"
      data-testid={`corners-enrichment-${matchId}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11.5px] uppercase tracking-wider text-muted-foreground">
          <Flag className="h-3.5 w-3.5" />
          <span>{T.title}</span>
        </div>
        <Button
          size="sm"
          variant={hasSnap ? 'ghost' : 'secondary'}
          onClick={trigger}
          disabled={busy}
          data-testid={`corners-enrichment-btn-${matchId}`}
          className="text-[11.5px] h-7 px-2"
        >
          {busy
            ? <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
            : hasSnap
              ? <RefreshCw className="h-3.5 w-3.5 mr-1" />
              : null}
          {state === 'loading' ? T.loading
            : state === 'polling' ? T.polling
            : hasSnap            ? T.refresh
            :                      T.cta}
        </Button>
      </div>

      {state === 'error' && error && (
        <div
          className="flex items-start gap-1.5 text-[11.5px] text-rose-300/90"
          data-testid={`corners-enrichment-error-${matchId}`}
        >
          <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
          <span>{T.errorPrefix}: {String(error)}</span>
        </div>
      )}

      {/* F59/F60 — Engine cross profile (always render when available,
          independent of 365Scores button state). */}
      {crossProfile?.available && (
        <CrossProfileBlock
          cross={crossProfile}
          audit={crossAudit}
          labels={T}
        />
      )}

      {hasSnap && <CornersSummary snapshot={snapshot} labels={T} />}
    </div>
  );
}

// ─── F59/F60 Engine cross profile block ─────────────────────────────
const PROFILE_TONE = {
  STRONG_CORNERS_UNDER_CROSS: { tone: 'cool',    label_es: 'Under fuerte',     label_en: 'Strong UNDER',  Icon: TrendingDown },
  LOW_CORNERS_CROSS:          { tone: 'cool',    label_es: 'Pocos córners',    label_en: 'Low corners',   Icon: TrendingDown },
  STRONG_CORNERS_OVER_CROSS:  { tone: 'warm',    label_es: 'Over fuerte',      label_en: 'Strong OVER',   Icon: TrendingUp   },
  HIGH_CORNERS_CROSS:         { tone: 'warm',    label_es: 'Muchos córners',   label_en: 'High corners',  Icon: TrendingUp   },
  ASYMMETRIC_CORNERS_PROFILE: { tone: 'neutral', label_es: 'Asimétrico',       label_en: 'Asymmetric',    Icon: Minus        },
  MIXED_CORNERS_PROFILE:      { tone: 'neutral', label_es: 'Mixto',            label_en: 'Mixed',         Icon: Minus        },
};

function CrossProfileBlock({ cross, audit, labels }) {
  const meta    = PROFILE_TONE[cross.profile] || PROFILE_TONE.MIXED_CORNERS_PROFILE;
  const Icon    = meta.Icon;
  const support = cross.supports || 'NEUTRAL';
  const cDelta  = Number(cross.confidence_delta || 0);
  const fDelta  = Number(cross.fragility_delta  || 0);
  const home    = cross.home || {};
  const away    = cross.away || {};

  const supportTone =
    support === 'OVER'  ? 'text-amber-300'
  : support === 'UNDER' ? 'text-cyan-300'
  :                       'text-muted-foreground';

  const pillTone =
    meta.tone === 'warm'    ? 'border-amber-400/40 bg-amber-500/10 text-amber-200'
  : meta.tone === 'cool'    ? 'border-cyan-400/40 bg-cyan-500/10 text-cyan-200'
  :                           'border-border/60 bg-background/40 text-muted-foreground';

  // External 365Scores status (Phase F82.2).
  // The cross block now carries explicit ``external_source``,
  // ``external_confirmation`` and ``external_conflict`` flags written
  // by ``football_corner_365_cross_integration.attach_365_corner_confirmation``.
  // Audit field (``football_corner_365_cross_applied``) is also read for
  // observability when no confirmation/conflict triggered.
  let externalLabel = null;
  let ExternalIcon  = null;
  let externalTone  = '';
  let externalTestId = null;
  if (cross.external_confirmation) {
    externalLabel = labels.extConfirms;
    ExternalIcon  = Link2;
    externalTone  = 'text-emerald-300';
    externalTestId = 'corner-cross-external-confirms';
  } else if (cross.external_conflict) {
    externalLabel = labels.extConflict;
    ExternalIcon  = Link2Off;
    externalTone  = 'text-rose-300';
    externalTestId = 'corner-cross-external-conflicts';
  } else if (
    // Surface "External confirmation unavailable" when:
    //   - the new 365Scores audit explicitly says so, OR
    //   - the legacy Scores24 gate denied the fetch (backwards compat).
    (audit && (audit.external_source == null || audit.gate_should_fetch === false))
    || (Array.isArray(cross.external_reason_codes)
        && cross.external_reason_codes.includes('NO_365SCORES_CONFIRMATION_AVAILABLE'))
  ) {
    externalLabel = labels.gateBlocked;
    ExternalIcon  = Link2Off;
    externalTone  = 'text-muted-foreground/80';
    externalTestId = 'corner-cross-external-unavailable';
  }

  return (
    <div
      className="pt-2 border-t border-border/40 flex flex-col gap-2"
      data-testid="corner-cross-profile"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10.5px] uppercase tracking-wider text-muted-foreground">
          {labels.crossTitle}
        </div>
        {externalLabel && (
          <div
            className={`flex items-center gap-1 text-[10.5px] ${externalTone}`}
            data-testid={externalTestId}
          >
            <ExternalIcon className="h-3 w-3" />
            <span>{externalLabel}</span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] ${pillTone}`}>
          <Icon className="h-3 w-3" />
          {meta.label_es && labels.crossTitle.includes('cruzado') ? meta.label_es : meta.label_en}
        </span>
        <span className={`text-[11px] ${supportTone}`}>
          <Target className="inline h-3 w-3 mr-1" />
          {labels.supports}: <span className="font-semibold">{support}</span>
        </span>
        {cross.recommended_market_family && (
          <span className="text-[10.5px] text-muted-foreground">
            · {labels.marketFamily}: <span className="text-foreground/90 font-mono-tabular">{cross.recommended_market_family}</span>
          </span>
        )}
      </div>

      {cross.narrative_es && (
        <p className="text-[11.5px] text-foreground/85 leading-relaxed">
          {cross.narrative_es}
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <CrossSideRow team="H" side={home} labels={labels} />
        <CrossSideRow team="A" side={away} labels={labels} />
      </div>

      {(cDelta !== 0 || fDelta !== 0) && (
        <div className="flex items-center gap-3 text-[10.5px] text-muted-foreground">
          {cDelta !== 0 && (
            <span>
              {labels.confDelta}: <span className={cDelta > 0 ? 'text-emerald-300' : 'text-rose-300'}>{cDelta > 0 ? '+' : ''}{cDelta}</span>
            </span>
          )}
          {fDelta !== 0 && (
            <span>
              {labels.fragDelta}: <span className={fDelta > 0 ? 'text-rose-300' : 'text-emerald-300'}>{fDelta > 0 ? '+' : ''}{fDelta}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function CrossSideRow({ team, side, labels }) {
  const cf5  = side.corners_for_l5;
  const cf15 = side.corners_for_l15;
  const cfd  = side.corners_for_delta;
  const ca5  = side.corners_against_l5;
  const ca15 = side.corners_against_l15;
  const cad  = side.corners_against_delta;

  const fmtPair = (l5, l15, delta) => {
    if (l5 == null && l15 == null) return '—';
    const arrow = delta == null ? '' : delta > 0.15 ? ' ▲' : delta < -0.15 ? ' ▼' : '';
    return `${fmtNum(l5) ?? '—'} / ${fmtNum(l15) ?? '—'}${arrow}`;
  };

  return (
    <>
      <div className="flex items-center gap-1">
        <span className="text-muted-foreground font-mono-tabular w-3">{team}</span>
        <span className="text-muted-foreground">{labels.forShort}:</span>
        <span className="font-mono-tabular">{fmtPair(cf5, cf15, cfd)}</span>
      </div>
      <div className="flex items-center gap-1">
        <span className="text-muted-foreground">{labels.againstShort}:</span>
        <span className="font-mono-tabular">{fmtPair(ca5, ca15, cad)}</span>
      </div>
    </>
  );
}

function CornersSummaryCell({ label, value, testId }) {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-[12px] font-mono-tabular">{value ?? '—'}</span>
    </div>
  );
}

function CornersSummary({ snapshot, labels }) {
  const cm     = snapshot?.current_match || snapshot?.match || {};
  const home   = cm?.home || snapshot?.home || {};
  const away   = cm?.away || snapshot?.away || {};
  const source = snapshot?.source || 'unknown';
  const ts     = snapshot?.fetched_at || snapshot?.updated_at;

  // Normalise: API responses may have numeric per-team values (e.g.
  // current_match.home == 5) or dict-shaped per-team objects. We accept
  // both.
  const homeAvgFor      = typeof home === 'number' ? home : (home?.corners_avg_for ?? home?.avg_corners_for);
  const awayAvgFor      = typeof away === 'number' ? away : (away?.corners_avg_for ?? away?.avg_corners_for);
  const homeAvgAgainst  = typeof home === 'number' ? null : (home?.corners_avg_against ?? home?.avg_corners_against);
  const awayAvgAgainst  = typeof away === 'number' ? null : (away?.corners_avg_against ?? away?.avg_corners_against);
  const homeOver95      = typeof home === 'number' ? null : home?.over_9_5_rate;
  const awayOver95      = typeof away === 'number' ? null : away?.over_9_5_rate;

  return (
    <div
      className="grid grid-cols-2 gap-x-3 gap-y-1.5 pt-1 border-t border-border/40"
      data-testid="corners-summary"
    >
      <CornersSummaryCell label={`${labels.avgFor} (H)`}     value={fmtNum(homeAvgFor)} />
      <CornersSummaryCell label={`${labels.avgFor} (A)`}     value={fmtNum(awayAvgFor)} />
      <CornersSummaryCell label={`${labels.avgAgainst} (H)`} value={fmtNum(homeAvgAgainst)} />
      <CornersSummaryCell label={`${labels.avgAgainst} (A)`} value={fmtNum(awayAvgAgainst)} />
      {(homeOver95 != null || awayOver95 != null) && (
        <>
          <CornersSummaryCell label={`${labels.over95Rate} (H)`} value={fmtPct(homeOver95)} />
          <CornersSummaryCell label={`${labels.over95Rate} (A)`} value={fmtPct(awayOver95)} />
        </>
      )}
      <div className="col-span-2 flex items-center justify-between pt-1 text-[10px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <CheckCircle2 className="h-3 w-3 text-emerald-400/80" />
          {labels.source}: {source}
        </span>
        {ts && <span>{labels.updated}: {fmtTime(ts)}</span>}
      </div>
    </div>
  );
}

function fmtNum(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}
function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  return (n <= 1 ? n * 100 : n).toFixed(0) + '%';
}
function fmtTime(iso) {
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso; }
}

export default CornersEnrichmentButton;
