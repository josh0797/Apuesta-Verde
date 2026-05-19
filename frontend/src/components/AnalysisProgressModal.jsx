import { useEffect, useState, useRef } from 'react';
import { Loader2, CheckCircle2, AlertCircle, Sparkles, X } from 'lucide-react';
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

export function AnalysisProgressModal({ jobId, onClose, onDone, sport }) {
  const { lang } = useI18n();
  const stages = STAGE_COPY[lang] || STAGE_COPY.es;
  const terms = sportTerms(lang, sport);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const doneFiredRef = useRef(false);

  useEffect(() => {
    if (!jobId) return undefined;
    let cancelled = false;
    let timeoutId;

    const poll = async () => {
      if (cancelled) return;
      try {
        const r = await api.get(`/analysis/jobs/${jobId}`);
        if (cancelled) return;
        setJob(r.data);
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
  }, [jobId, onDone]);

  if (!jobId) return null;

  const stage = job?.stage || 'queued';
  const progress = job?.progress ?? 0;
  const isDone = stage === 'done';
  const isFailed = stage === 'failed' || !!error;
  const canDismiss = isDone || isFailed;

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
          </div>
        )}

        {isFailed && (
          <div className="rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-300" data-testid="progress-modal-error">
            {error || job?.error || job?.message || 'Unknown error'}
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
