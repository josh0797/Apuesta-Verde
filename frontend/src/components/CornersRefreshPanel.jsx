import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { RefreshCw, AlertTriangle, CheckCircle2, Clock, Loader2 } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Phase F82.1-adjust — Corners refresh panel.
 *
 * Rendered when ``corners_snapshot.status === 'PENDING_BACKGROUND_ENRICHMENT'``
 * or when ``corners_snapshot.reason_codes`` contains ``CORNERS_EXTERNAL_ENRICHMENT_DEFERRED``.
 *
 * Flow:
 *   1. User clicks "Actualizar córners con 365Scores".
 *   2. Component POSTs /api/football/corners-enrichment/run-now (hard 8s timeout server-side).
 *   3. If SUCCESS → render the updated snapshot, notify parent via onUpdated().
 *   4. If TIMEOUT → offer fallback "Reintentar en segundo plano" (calls /background).
 *   5. If UNAVAILABLE / MATCH_NOT_FOUND / ERROR → render explicit error + retry.
 *   6. Background fallback polls /status/{match_id} every 3 s until terminal.
 */
const FRIENDLY_REASONS = {
  SCORE365_FETCH_TIMEOUT:        'La solicitud a 365Scores tardó demasiado.',
  SCORE365_ID_MISSING:           'No se pudo resolver el ID del partido en 365Scores.',
  SCORE365_BLOCKED_OR_EMPTY:     '365Scores no devolvió datos útiles para este partido.',
  CORNERS_NO_APISPORTS_STATS:    'API-Sports no tiene estadísticas de córners para este partido.',
  CORNERS_NO_THESTATSAPI_BLOCK:  'TheStatsAPI no provee córners en este partido.',
  CORNERS_PROVIDER_BREAKER_OPEN: 'El proveedor externo está temporalmente bloqueado.',
  MATCH_NOT_FOUND:               'El partido no se encuentra en la corrida más reciente del analista.',
  BACKGROUND_JOB_CRASHED:        'El job en segundo plano falló inesperadamente.',
  EXTERNAL_FETCH_CRASHED:        'La obtención externa falló inesperadamente.',
  CORNERS_UNAVAILABLE:           'No hay datos de córners disponibles.',
};

function translateReasonCodes(codes) {
  if (!Array.isArray(codes) || codes.length === 0) return null;
  const useful = codes.filter((c) => FRIENDLY_REASONS[c]);
  if (useful.length === 0) return codes.join(' · ');
  return useful.map((c) => FRIENDLY_REASONS[c]).join(' · ');
}

export const CornersRefreshPanel = ({
  matchId,
  cornersSnapshot,
  onUpdated,
  testIdPrefix = 'corners-refresh',
}) => {
  const [phase, setPhase]     = useState('idle');   // idle | loading | success | timeout | error | bg_queued | bg_polling
  const [result, setResult]   = useState(null);     // last response payload
  const [errorMsg, setErrMsg] = useState(null);
  const pollTimer = useRef(null);

  // ── Guard: only render when the snapshot is in the deferred state ──
  const reasonCodes = Array.isArray(cornersSnapshot?.reason_codes)
    ? cornersSnapshot.reason_codes : [];
  const isDeferred = (
    cornersSnapshot?.status === 'PENDING_BACKGROUND_ENRICHMENT'
    || reasonCodes.includes('CORNERS_EXTERNAL_ENRICHMENT_DEFERRED')
  );

  // ── Cleanup polling on unmount ──
  useEffect(() => () => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
  }, []);

  const finishWithResult = (payload) => {
    setResult(payload);
    if (typeof onUpdated === 'function' && payload && typeof payload === 'object') {
      try { onUpdated(payload); } catch (_e) { /* swallow */ }
    }
  };

  const pollStatusOnce = async () => {
    try {
      const res = await axios.get(
        `${BACKEND_URL}/api/football/corners-enrichment/status/${encodeURIComponent(matchId)}`,
      );
      const data = res.data || {};
      if (data.status === 'SUCCESS') {
        setPhase('success');
        finishWithResult(data.result);
        return;
      }
      if (data.status === 'FAILED' || data.status === 'NOT_FOUND') {
        setPhase('error');
        setErrMsg(translateReasonCodes(data?.result?.reason_codes)
                   || 'No se pudo completar la actualización en segundo plano.');
        finishWithResult(data.result);
        return;
      }
      // Still QUEUED / RUNNING — keep polling.
      pollTimer.current = setTimeout(pollStatusOnce, 3000);
    } catch (_err) {
      setPhase('error');
      setErrMsg('Error de red al consultar el estado del job en segundo plano.');
    }
  };

  const handleRunNow = async () => {
    setPhase('loading');
    setErrMsg(null);
    setResult(null);
    try {
      const res = await axios.post(
        `${BACKEND_URL}/api/football/corners-enrichment/run-now`,
        { match_id: matchId },
        { timeout: 12000 },  // a touch above the 8s server-side cap
      );
      const data = res.data || {};
      finishWithResult(data);
      if (data.status === 'SUCCESS') {
        setPhase('success');
      } else if (data.status === 'TIMEOUT') {
        setPhase('timeout');
        setErrMsg('365Scores tardó más de 8 segundos.');
      } else if (data.status === 'MATCH_NOT_FOUND') {
        setPhase('error');
        setErrMsg('El partido no está disponible en la corrida actual.');
      } else {
        // UNAVAILABLE / ERROR / any non-success status.
        setPhase('error');
        setErrMsg(translateReasonCodes(data.reason_codes) || 'No se pudieron obtener córners.');
      }
    } catch (err) {
      // Network-level / client-side error.
      setPhase('error');
      const detail = err?.response?.data?.detail || err?.message || 'Error desconocido';
      setErrMsg(`Error al contactar el servidor: ${detail}`);
    }
  };

  const handleBackground = async () => {
    setPhase('bg_queued');
    setErrMsg(null);
    try {
      await axios.post(
        `${BACKEND_URL}/api/football/corners-enrichment/background`,
        { match_id: matchId },
        { timeout: 5000 },
      );
      setPhase('bg_polling');
      pollTimer.current = setTimeout(pollStatusOnce, 3000);
    } catch (_err) {
      setPhase('error');
      setErrMsg('No se pudo encolar la actualización en segundo plano.');
    }
  };

  // ── Render guard ──
  if (!matchId) return null;
  if (!isDeferred && phase === 'idle') return null;

  const renderHeader = () => (
    <div className="flex items-center gap-2">
      <RefreshCw className="h-4 w-4 text-sky-300" />
      <span className="font-semibold text-slate-100 text-sm">Córners — datos diferidos</span>
      <Badge variant="outline" className="border-sky-500/40 text-sky-200 text-[10px]">365Scores</Badge>
    </div>
  );

  const renderIdle = () => (
    <>
      <p className="text-xs text-slate-400">
        No tenemos estadísticas de córners en línea para este partido. Puedes intentar
        obtenerlas ahora desde 365Scores (puede tardar hasta 8 segundos).
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={handleRunNow}
          data-testid={`${testIdPrefix}-run-now-btn`}
          className="bg-sky-600 hover:bg-sky-500 text-white"
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Actualizar córners con 365Scores
        </Button>
      </div>
    </>
  );

  const renderLoading = () => (
    <div
      className="flex items-center gap-2 text-xs text-slate-300"
      data-testid={`${testIdPrefix}-loading`}
    >
      <Loader2 className="h-4 w-4 animate-spin text-sky-300" />
      <span>Consultando 365Scores… (máx. 8 s)</span>
    </div>
  );

  const renderSuccess = () => {
    const cm = result?.current_match || {};
    return (
      <div data-testid={`${testIdPrefix}-success`} className="space-y-1.5">
        <div className="flex items-center gap-2 text-xs text-emerald-300">
          <CheckCircle2 className="h-4 w-4" />
          <span>Córners actualizados desde {result?.source || '365Scores'}.</span>
        </div>
        <div className="text-xs text-slate-200 grid grid-cols-3 gap-2">
          <span data-testid={`${testIdPrefix}-home`}>Local: <b>{cm.home ?? '—'}</b></span>
          <span data-testid={`${testIdPrefix}-away`}>Visitante: <b>{cm.away ?? '—'}</b></span>
          <span data-testid={`${testIdPrefix}-total`}>Total: <b>{cm.total ?? '—'}</b></span>
        </div>
      </div>
    );
  };

  const renderTimeout = () => (
    <div data-testid={`${testIdPrefix}-timeout`} className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-amber-300">
        <Clock className="h-4 w-4" />
        <span>{errorMsg || '365Scores tardó demasiado.'}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={handleBackground}
          data-testid={`${testIdPrefix}-bg-btn`}
          className="border-sky-500/40 text-sky-200 hover:bg-sky-500/10"
        >
          Reintentar en segundo plano
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleRunNow}
          data-testid={`${testIdPrefix}-retry-btn`}
          className="text-slate-300 hover:text-white"
        >
          Volver a intentar ahora
        </Button>
      </div>
    </div>
  );

  const renderError = () => (
    <div data-testid={`${testIdPrefix}-error`} className="space-y-2">
      <div className="flex items-start gap-2 text-xs text-rose-300">
        <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5" />
        <span>{errorMsg || 'No se pudieron obtener córners.'}</span>
      </div>
      <Button
        size="sm"
        variant="outline"
        onClick={handleRunNow}
        data-testid={`${testIdPrefix}-retry-btn`}
        className="border-slate-500/40 text-slate-200 hover:bg-slate-700/30"
      >
        Reintentar
      </Button>
    </div>
  );

  const renderBgQueued = () => (
    <div
      className="flex items-center gap-2 text-xs text-slate-300"
      data-testid={`${testIdPrefix}-bg-queued`}
    >
      <Loader2 className="h-4 w-4 animate-spin text-sky-300" />
      <span>Tarea encolada — procesando…</span>
    </div>
  );

  const renderBgPolling = () => (
    <div
      className="flex items-center gap-2 text-xs text-slate-300"
      data-testid={`${testIdPrefix}-bg-polling`}
    >
      <Loader2 className="h-4 w-4 animate-spin text-sky-300" />
      <span>Esperando resultado del job en segundo plano…</span>
    </div>
  );

  return (
    <Card
      className="border-slate-700/60 bg-slate-900/40"
      data-testid={`${testIdPrefix}-root`}
    >
      <CardContent className="py-3 px-4 space-y-2">
        {renderHeader()}
        {phase === 'idle'       && renderIdle()}
        {phase === 'loading'    && renderLoading()}
        {phase === 'success'    && renderSuccess()}
        {phase === 'timeout'    && renderTimeout()}
        {phase === 'error'      && renderError()}
        {phase === 'bg_queued'  && renderBgQueued()}
        {phase === 'bg_polling' && renderBgPolling()}
      </CardContent>
    </Card>
  );
};

export default CornersRefreshPanel;
