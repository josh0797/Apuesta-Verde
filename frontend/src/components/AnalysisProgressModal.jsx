import { useEffect, useState, useRef } from 'react';
import { Loader2, CheckCircle2, AlertCircle, Sparkles, X, Bug, ChevronDown } from 'lucide-react';
import { api } from '@/lib/api';
import { useI18n, sportTerms } from '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';

/**
 * Modal that polls /api/analysis/jobs/{id} and renders pipeline progress.
 */
const STAGE_COPY = {
  es: {
    queued: 'En cola',
    ingesting: ({ eventPlural }) => `Obteniendo ${eventPlural}…`,
    enriching: 'Enriqueciendo datos (cuotas, contexto, h2h)…',
    analyzing: 'Analizando con IA…',
    done: 'Completado',
    failed: 'Falló',
  },
  en: {
    queued: 'Queued',
    ingesting: ({ eventPlural }) => `Fetching ${eventPlural}…`,
    enriching: 'Enriching data (odds, context, h2h)…',
    analyzing: 'Analyzing with AI…',
    done: 'Completed',
    failed: 'Failed',
  },
};

function stageText(stages, stage, terms) {
  const v = stages[stage];
  return typeof v === 'function' ? v(terms) : v || stage;
}

/**
 * Map raw backend / driver errors into user-friendly Spanish/English copy.
 * The classic example is BSON's `documents must have only string keys, key
 * was N` which is utterly opaque to end users — we explain what happened
 * and that the system already self-protected against it.
 */
function humanizeError(raw, lang) {
  if (!raw) return lang === 'en' ? 'Unknown error' : 'Error desconocido';
  const text = String(raw);
  // BSON / Mongo numeric-key error
  if (/documents must have only string keys/i.test(text) || /InvalidDocument/i.test(text)) {
    return lang === 'en'
      ? 'Could not save the analysis because the payload contained numeric keys. The system has been hardened against this case — please try again. If it persists, share this message with support.'
      : 'No se pudo guardar el análisis porque el payload contenía claves numéricas. El sistema ya se reforzó contra este caso — por favor reintenta. Si persiste, comparte este mensaje con soporte.';
  }
  // Ingress / gateway timeouts
  if (/502|504|timeout/i.test(text)) {
    return lang === 'en'
      ? 'The analysis took too long and the gateway closed the connection. The background job may still finish — refresh the dashboard in ~60 seconds.'
      : 'El análisis tardó demasiado y la pasarela cortó la conexión. El job en background puede haber terminado igual — recarga el dashboard en ~60 segundos.';
  }
  // LLM credit / quota
  if (/insufficient.*credits?|quota|rate.?limit/i.test(text)) {
    return lang === 'en'
      ? 'AI credit limit reached. Top up your Universal Key balance from Profile → Universal Key → Add Balance.'
      : 'Se alcanzó el límite de créditos de IA. Recarga tu Universal Key en Perfil → Universal Key → Añadir saldo.';
  }
  // No matches available
  if (/no .* matches available/i.test(text)) {
    return lang === 'en'
      ? 'No relevant Tier 1/2/3 matches are available in the next 48 hours. Try again later or enable Tier 4 fallback.'
      : 'No hay partidos Tier 1/2/3 relevantes en las próximas 48 horas. Intenta más tarde o activa el fallback Tier 4.';
  }
  // Fall back to the raw message, trimmed.
  return text.length > 240 ? text.slice(0, 240) + '…' : text;
}

export function AnalysisProgressModal({ jobId, onClose, onDone, onRetry, sport }) {
  const { lang } = useI18n();
  const stages = STAGE_COPY[lang] || STAGE_COPY.es;
  const terms = sportTerms(lang, sport);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  // Fix: stuck-at-5% detection. If the backend job is alive (we get 200
  // responses) but `progress` doesn't move for STALL_THRESHOLD_MS,
  // surface a banner with a "retry" CTA and explain the user can
  // continue waiting OR close the modal (background polling keeps going).
  const [stalled, setStalled] = useState(false);
  const doneFiredRef = useRef(false);
  const transientFailRef = useRef(0);
  const lastProgressRef = useRef({ value: -1, ts: 0 });

  // Threshold: progress must move at least once every 60s.
  const STALL_THRESHOLD_MS = 60_000;

  useEffect(() => {
    if (!jobId) return undefined;
    let cancelled = false;
    let timeoutId;
    // Reset stall tracker when jobId changes (new run). The ref mutation
    // is allowed inside effects; the React state reset happens via the
    // next poll() iteration which compares `currentProgress` against
    // the freshly reset `lastProgressRef.current.value=-1`.
    lastProgressRef.current = { value: -1, ts: Date.now() };

    // Resilient polling: a single Cloudflare 520 / 502 / 504 / network blip
    // must NOT kill the modal. The background job is still running. We
    // only give up after 8 consecutive failures (~20s of unreachable
    // backend) OR when the job itself reports `stage=failed`.
    const TRANSIENT_THRESHOLD = 8;
    const isTransient = (e) => {
      const s = e?.response?.status;
      // No response = network blip / Cloudflare 520.
      if (!e?.response) return true;
      // 502 (bad gateway), 503 (svc unavailable), 504 (gateway timeout),
      // 520-527 (Cloudflare origin errors), 408 (timeout)
      if (s === 408 || s === 502 || s === 503 || s === 504) return true;
      if (s >= 520 && s <= 527) return true;
      return false;
    };

    const poll = async () => {
      if (cancelled) return;
      try {
        const r = await api.get(`/analysis/jobs/${jobId}`);
        if (cancelled) return;
        // Successful poll → reset the transient counter and clear banner.
        transientFailRef.current = 0;
        if (reconnecting) setReconnecting(false);
        setJob(r.data);

        // Stall detection: track when `progress` last changed. If it
        // hasn't moved for STALL_THRESHOLD_MS, raise the stall banner.
        const currentProgress = Number(r.data?.progress ?? 0);
        const now = Date.now();
        if (currentProgress !== lastProgressRef.current.value) {
          lastProgressRef.current = { value: currentProgress, ts: now };
          if (stalled) setStalled(false);
        } else if (
          lastProgressRef.current.ts > 0
          && (now - lastProgressRef.current.ts) >= STALL_THRESHOLD_MS
          && r.data?.stage !== 'done'
          && r.data?.stage !== 'failed'
        ) {
          if (!stalled) setStalled(true);
        }

        const stage = r.data?.stage;
        if (stage === 'done' && !doneFiredRef.current) {
          doneFiredRef.current = true;
          if (typeof onDone === 'function' && r.data?.result) {
            onDone(r.data.result);
          }
          return;
        }
        if (stage === 'failed') {
          setError(r.data?.error || r.data?.message || 'Failed');
          return;
        }
      } catch (e) {
        if (cancelled) return;
        if (isTransient(e)) {
          transientFailRef.current += 1;
          setReconnecting(true);
          // Exponential-ish backoff: 1.5s, 2s, 3s, 4s, 5s … capped at 5s
          const delay = Math.min(5000, 1500 + transientFailRef.current * 500);
          if (transientFailRef.current >= TRANSIENT_THRESHOLD) {
            setError(
              lang === 'en'
                ? `Connection to backend lost (${transientFailRef.current} consecutive errors). The analysis may still complete — refresh to check.`
                : `Se perdió conexión con el backend (${transientFailRef.current} errores seguidos). El análisis puede seguir corriendo — recarga para verificar.`
            );
            return;
          }
          timeoutId = setTimeout(poll, delay);
          return;
        }
        // Non-transient (e.g. 401 / 403 / 404 / 4xx) → give up immediately.
        setError(e?.response?.data?.detail || e.message);
        return;
      }
      timeoutId = setTimeout(poll, 1500);
    };

    poll();
    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, onDone, lang, stalled, reconnecting]);

  if (!jobId) return null;

  const stage = job?.stage || 'queued';
  const progress = job?.progress ?? 0;
  const isDone = stage === 'done';
  const isFailed = stage === 'failed' || !!error;
  const canDismiss = isDone || isFailed;

  // Surface the engine's pipeline_meta when the job finishes so the user
  // sees why no picks materialised instead of staring at "Completed 100%".
  const pipelineMeta = job?.result?.result?.pipeline_meta || job?.result?.pipeline_meta || null;
  const finalPicks   = Number(pipelineMeta?.final_picks_count ?? 0);
  const finalRescued = Number(pipelineMeta?.final_rescued_count ?? 0);
  const isDoneEmpty  = isDone && finalPicks === 0 && finalRescued === 0;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4"
      data-testid="analysis-progress-modal"
      role="dialog"
      aria-modal="true"
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-background/80 backdrop-blur-sm" aria-hidden />
      {/* Card */}
      <div className="relative w-full max-w-md rounded-xl border border-border bg-card shadow-2xl p-6 space-y-4">
        <div className="flex items-start gap-3">
          <div className={`h-10 w-10 rounded-full flex items-center justify-center shrink-0 ${
            isFailed
              ? 'bg-red-500/10 border border-red-500/30'
              : isDone
                ? 'bg-emerald-500/10 border border-emerald-500/30'
                : 'bg-cyan-500/10 border border-cyan-500/30'
          }`}>
            {isFailed ? (
              <AlertCircle className="h-5 w-5 text-red-400" />
            ) : isDone ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-400" />
            ) : (
              <Sparkles className="h-5 w-5 text-cyan-300 animate-pulse" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold tracking-tight" data-testid="progress-modal-title">
              {lang === 'es' ? 'Generando picks' : 'Generating picks'}
              {sport ? ` · ${sport.toUpperCase()}` : ''}
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5" data-testid="progress-modal-stage">
              {stageText(stages, stage, terms)}
            </p>
          </div>
          {canDismiss && (
            <button
              onClick={onClose}
              data-testid="progress-modal-close"
              className="p-1 rounded-md hover:bg-white/5 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {!isFailed && (
          <div className="space-y-2">
            <Progress
              value={progress}
              data-testid="progress-modal-bar"
              className={isDone ? 'bg-emerald-500/10' : ''}
            />
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span data-testid="progress-modal-message" className="truncate pr-2">{job?.message || '…'}</span>
              <span className="tabular-nums font-medium" data-testid="progress-modal-pct">{progress}%</span>
            </div>
            {/* Transient connectivity banner: shown when the polling
                endpoint is returning 520/502/504/no-response while the
                backend job is presumably still running. */}
            {reconnecting && !isDone && (
              <div
                className="text-[10px] text-amber-300/90 bg-amber-500/5 border border-amber-500/20 rounded px-2 py-1"
                data-testid="progress-modal-reconnecting"
              >
                {lang === 'en'
                  ? 'Connection blip — backend may be slow, retrying…'
                  : 'Conexión inestable — reintentando con el backend…'}
              </div>
            )}

            {/* Fix: stuck-at-5% stall banner. Surfaces when the
                progress hasn't advanced for 60s+. The backend job
                might still be alive (rate limiter sleep), so we
                offer the user both: keep waiting OR retry. The modal
                can also be closed — polling continues in background
                via the parent's onClose handler. */}
            {stalled && !isDone && !isFailed && (
              <div
                className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] space-y-1.5"
                role="status"
                data-testid="progress-modal-stalled"
              >
                <div className="font-semibold text-amber-200 flex items-center gap-1.5">
                  <AlertCircle className="h-3.5 w-3.5" />
                  {lang === 'en' ? 'Generation is taking longer than expected' : 'La generación tardó demasiado'}
                </div>
                <p className="text-amber-200/85 leading-snug">
                  {lang === 'en'
                    ? 'API-Sports may be rate-limiting the request. You can keep waiting, retry, or continue in background.'
                    : 'Puede que API-Sports esté limitando los requests. Puedes seguir esperando, reintentar o continuar en background.'}
                </p>
                <div className="flex flex-wrap gap-2 pt-1">
                  {typeof onRetry === 'function' && (
                    <Button
                      size="sm"
                      variant="secondary"
                      className="h-7 text-[11px]"
                      onClick={() => {
                        // Reset stall state locally so the new run gets a fresh
                        // observation window. The parent triggers a new job.
                        lastProgressRef.current = { value: -1, ts: Date.now() };
                        setStalled(false);
                        onRetry();
                      }}
                      data-testid="progress-modal-stall-retry"
                    >
                      {lang === 'en' ? 'Retry' : 'Reintentar'}
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[11px]"
                    onClick={onClose}
                    data-testid="progress-modal-stall-close"
                  >
                    {lang === 'en' ? 'Continue in background' : 'Continuar en background'}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {isFailed && (
          <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300" data-testid="progress-modal-error">
            {humanizeError(error || job?.error || job?.message, lang)}
          </div>
        )}

        {!canDismiss && (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>
              {lang === 'es'
                ? 'Puedes cerrar esta ventana en cualquier momento — la generación seguirá en background.'
                : 'You can close this window any time — generation continues in the background.'}
            </span>
          </div>
        )}

        {!canDismiss && (
          <div className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              data-testid="progress-modal-bg-close"
            >
              {lang === 'es' ? 'Continuar en background' : 'Run in background'}
            </Button>
          </div>
        )}

        {isDone && pipelineMeta?.fallback_used && (
          <div
            className="rounded-md border border-cyan-500/30 bg-cyan-500/5 px-3 py-2 text-xs space-y-0.5"
            data-testid="progress-modal-fallback-banner"
          >
            <div className="font-semibold text-cyan-200 flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5" />
              {pipelineMeta?.sport === 'basketball'
                ? (lang === 'es' ? 'Se usó ESPN como respaldo' : 'ESPN fallback was used')
                : (lang === 'es' ? 'Se usó MLB Stats API como respaldo' : 'MLB Stats API fallback was used')}
            </div>
            <div className="text-cyan-200/80 leading-relaxed">
              {pipelineMeta?.sport === 'basketball'
                ? (lang === 'es'
                    ? `API-Sports no encontró juegos (${pipelineMeta.api_sports_games_found ?? 0}). ESPN NBA devolvió ${pipelineMeta.espn_nba_games_found ?? 0} juegos.`
                    : `API-Sports returned ${pipelineMeta.api_sports_games_found ?? 0} games. ESPN NBA returned ${pipelineMeta.espn_nba_games_found ?? 0}.`)
                : (lang === 'es'
                    ? `API-Sports no encontró juegos (${pipelineMeta.api_sports_games_found ?? 0}). MLB Stats API devolvió ${pipelineMeta.mlb_stats_api_games_found ?? 0} juegos.`
                    : `API-Sports returned ${pipelineMeta.api_sports_games_found ?? 0} games. MLB Stats API returned ${pipelineMeta.mlb_stats_api_games_found ?? 0}.`)}
            </div>
          </div>
        )}

        {isDone && pipelineMeta?.external_rescue_count > 0 && (
          <div
            className="rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 text-xs space-y-0.5"
            data-testid="progress-modal-rescue-banner"
          >
            <div className="font-semibold text-emerald-200 flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5" />
              {lang === 'es'
                ? `${pipelineMeta.external_rescue_count} partido(s) rescatado(s) con fuentes externas`
                : `${pipelineMeta.external_rescue_count} match(es) rescued via external sources`}
            </div>
            <div className="text-emerald-200/80 leading-relaxed">
              {lang === 'es'
                ? 'Pitchers o lineups que faltaban en API-Sports se recuperaron vía RotoWire, MLB.com, FantasyPros o ESPN.'
                : 'Missing pitchers or lineups were recovered via RotoWire, MLB.com, FantasyPros or ESPN.'}
            </div>
          </div>
        )}

        {isDone && isDoneEmpty && (
          <div
            className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs space-y-1"
            data-testid="progress-modal-empty-state"
          >
            <div className="font-semibold text-amber-200">
              {lang === 'es' ? 'No hay picks recomendados hoy' : 'No recommended picks today'}
            </div>
            <div className="text-amber-200/80 leading-relaxed">
              {humanizeAbortReason(pipelineMeta?.abort_reason, lang)}
            </div>
          </div>
        )}

        {isDone && pipelineMeta && (
          <PipelineDebugPanel meta={pipelineMeta} lang={lang} />
        )}

        {isDone && (
          <div className="flex justify-end">
            <Button
              size="sm"
              onClick={onClose}
              data-testid="progress-modal-done-close"
            >
              {lang === 'es' ? 'Ver resultados' : 'View results'}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Pipeline debug helpers
// ────────────────────────────────────────────────────────────────────────

const ABORT_REASON_COPY = {
  es: {
    no_value_found:                      'El engine ejecutó el análisis completo, pero ningún mercado superó los filtros de valor.',
    no_candidates_for_sport:             'No se encontraron partidos disponibles para este deporte en las próximas horas.',
    no_priority_fixtures:                'No hay partidos Tier 1/2 prioritarios disponibles en las próximas 48h.',
    no_probable_pitchers_all_sources:    'No se pudieron confirmar pitchers ni en StatsAPI ni en MLB.com.',
    all_games_already_played_or_finished:'Todos los juegos ya están en curso o finalizados (filtrados por seguridad).',
    no_games_for_date:                   'No se encontraron juegos MLB para esta fecha.',
    no_games_all_sources:                'No se encontraron juegos MLB para esta fecha en API-Sports ni en MLB Stats API.',
    games_found_but_missing_pitchers:    'Hay juegos MLB programados, pero faltan pitchers confirmados. El engine no analiza hasta tener ambos abridores.',
  },
  en: {
    no_value_found:                      'The engine ran end-to-end, but no market cleared the value filters.',
    no_candidates_for_sport:             'No matches available for this sport in the upcoming window.',
    no_priority_fixtures:                'No Tier 1/2 priority matches in the next 48h.',
    no_probable_pitchers_all_sources:    'Could not confirm starting pitchers from StatsAPI nor MLB.com.',
    all_games_already_played_or_finished:'All games are already live or finished (filtered for safety).',
    no_games_for_date:                   'No MLB games scheduled for this date.',
    no_games_all_sources:                'No MLB games found for this date in API-Sports nor MLB Stats API.',
    games_found_but_missing_pitchers:    'There are MLB games scheduled, but starting pitchers are not confirmed yet. The engine waits for both starters.',
  },
};

function humanizeAbortReason(reason, lang) {
  if (!reason) {
    return lang === 'es'
      ? 'El engine sí ejecutó el análisis, pero no encontró mercados con suficiente valor.'
      : 'The engine completed analysis but no market had enough value.';
  }
  const dict = ABORT_REASON_COPY[lang] || ABORT_REASON_COPY.es;
  return dict[reason] || (lang === 'es'
    ? `El análisis no llegó a ejecutarse por completo. Motivo: ${reason}`
    : `The analysis did not complete fully. Reason: ${reason}`);
}

function PipelineDebugPanel({ meta, lang }) {
  const [open, setOpen] = useState(false);
  const isMLB = (meta?.sport === 'baseball') || meta?.date_basis === 'America/New_York';

  // Order rows so the most actionable metrics surface first.
  const rows = [
    { k: lang === 'es' ? 'Fecha analizada'      : 'Date analysed',  v: meta.date_str },
    { k: lang === 'es' ? 'Zona horaria'         : 'Timezone',       v: meta.date_basis },
    isMLB && { k: lang === 'es' ? 'Fuente primaria' : 'Primary source', v: meta.primary_source || 'unknown' },
    isMLB && meta.fallback_used && { k: lang === 'es' ? 'Fallback usado' : 'Fallback used', v: meta.fallback_reason || 'yes' },
    isMLB && meta.api_sports_games_found != null && { k: lang === 'es' ? 'Juegos API-Sports' : 'API-Sports games', v: meta.api_sports_games_found },
    isMLB && (meta.mlb_stats_api_games_found ?? 0) > 0 && { k: lang === 'es' ? 'Juegos MLB Stats API' : 'MLB Stats API games', v: meta.mlb_stats_api_games_found },
    !isMLB && meta?.sport === 'basketball' && (meta.espn_nba_games_found ?? 0) > 0 && { k: lang === 'es' ? 'Juegos ESPN NBA' : 'ESPN NBA games', v: meta.espn_nba_games_found },
    (meta.external_rescue_count ?? 0) > 0 && { k: lang === 'es' ? 'Rescatados externos' : 'External rescues', v: meta.external_rescue_count },
    isMLB && { k: lang === 'es' ? 'Juegos en schedule'  : 'Schedule games', v: meta.schedule_games_found },
    isMLB && { k: lang === 'es' ? 'Pitchers confirmados': 'Confirmed pitchers', v: meta.confirmed_games },
    isMLB && (meta.games_missing_pitchers ?? 0) > 0 && { k: lang === 'es' ? 'Juegos sin pitcher' : 'Games missing pitcher', v: meta.games_missing_pitchers },
    isMLB && meta.games_processed != null && { k: lang === 'es' ? 'Juegos procesados'   : 'Games processed',    v: meta.games_processed },
    isMLB && (meta.dropped_past_or_finished ?? 0) > 0 && { k: lang === 'es' ? 'Descartados (finalizados)' : 'Dropped (past/finished)', v: meta.dropped_past_or_finished },
    isMLB && (meta.dropped_missing_pitchers ?? 0) > 0 && { k: lang === 'es' ? 'Descartados (sin pitcher)' : 'Dropped (missing pitcher)', v: meta.dropped_missing_pitchers },
    isMLB && (meta.dropped_low_pitcher_data ?? 0) > 0 && { k: lang === 'es' ? 'Descartados (datos pitcher <3 ap.)' : 'Dropped (low pitcher data)', v: meta.dropped_low_pitcher_data },
    !isMLB && { k: lang === 'es' ? 'Partidos ingestados'    : 'Ingested matches',  v: meta.ingested_total },
    !isMLB && { k: lang === 'es' ? 'Candidatos seleccionados': 'Candidates picked', v: meta.candidates_count },
    { k: lang === 'es' ? 'Picks recomendados'  : 'Recommended picks', v: meta.final_picks_count ?? meta.picks_total },
    { k: lang === 'es' ? 'Picks rescatados'    : 'Rescued picks',     v: meta.final_rescued_count ?? meta.rescued_total },
    { k: lang === 'es' ? 'Picks descartados'   : 'Discarded picks',   v: meta.final_discarded_count ?? meta.discarded_total },
    meta.blocked_by_time_filter ? { k: lang === 'es' ? 'Bloqueados por tiempo' : 'Time-blocked', v: meta.blocked_by_time_filter } : null,
    !isMLB && meta.primary_source && { k: lang === 'es' ? 'Fuente primaria' : 'Primary source', v: meta.primary_source },
    { k: lang === 'es' ? 'Fuente usada'        : 'Source used', v: meta.source_used || 'unknown' },
    isMLB && { k: lang === 'es' ? 'Cache status' : 'Cache status', v: meta.cache_status || 'n/a' },
    meta.abort_reason ? { k: lang === 'es' ? 'Abort reason' : 'Abort reason', v: meta.abort_reason } : null,
  ].filter(Boolean);

  return (
    <div
      className="rounded-md border border-border/60 bg-muted/30 text-xs"
      data-testid="progress-modal-pipeline-debug"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-white/[0.02] transition-colors"
        data-testid="progress-modal-pipeline-debug-toggle"
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5 font-semibold text-muted-foreground">
          <Bug className="w-3.5 h-3.5" />
          {lang === 'es' ? 'Pipeline debug' : 'Pipeline debug'}
        </span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 grid grid-cols-1 gap-1" data-testid="progress-modal-pipeline-debug-body">
          {rows.map((r, i) => (
            <div
              key={i}
              className="flex items-start justify-between gap-3 leading-tight"
              data-testid={`pipeline-debug-row-${slugify(r.k)}`}
            >
              <span className="text-muted-foreground">{r.k}</span>
              <span className="font-mono-tabular text-foreground/90 break-all text-right">
                {String(r.v ?? '—')}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function slugify(s) {
  return String(s || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
