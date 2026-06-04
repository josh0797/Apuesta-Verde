/**
 * LiveRecommendationTimeline — fetches and renders the history of live
 * recommendation snapshots for a match. Supports manual backfill via an
 * inline form. Fail-soft: never blocks the parent UI on errors.
 */

import { useState, useEffect, useCallback } from 'react';
import {
  History, CheckCircle2, XCircle, Eye, Hand, RotateCw, AlertTriangle,
  Plus, Send,
} from 'lucide-react';
import { Button } from './ui/button';
import { api } from '@/lib/api';
import { toast } from 'sonner';

const STATUS_META = {
  hit:               { label: 'Cumplido',   tone: 'emerald', Icon: CheckCircle2 },
  miss:              { label: 'Fallado',    tone: 'rose',    Icon: XCircle      },
  open:              { label: 'En observación', tone: 'cyan',  Icon: Eye        },
  manual_recorded:   { label: 'Manual',     tone: 'violet',  Icon: Hand         },
  superseded:        { label: 'Reemplazado', tone: 'slate', Icon: History      },
  void:              { label: 'Anulado',    tone: 'slate',   Icon: XCircle      },
};

const TONE_CLS = {
  emerald: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  cyan:    'bg-cyan-500/15 text-cyan-200 border-cyan-500/30',
  rose:    'bg-rose-500/15 text-rose-200 border-rose-500/30',
  slate:   'bg-slate-500/15 text-slate-200 border-slate-500/30',
  violet:  'bg-violet-500/15 text-violet-200 border-violet-500/30',
  amber:   'bg-amber-500/15 text-amber-200 border-amber-500/30',
};

function Badge({ tone = 'slate', children, testId }) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center px-1.5 py-0.5 rounded-md border text-[9px] font-semibold uppercase tracking-wide ${TONE_CLS[tone]}`}
    >
      {children}
    </span>
  );
}

function EventCard({ event }) {
  const status = event.status || 'open';
  const meta = STATUS_META[status] || STATUS_META.open;
  const Icon = meta.Icon;
  const rec = event.recommendation || {};
  const outcome = event.outcome || {};

  return (
    <div
      data-testid={`live-reco-event-${event.event_id}`}
      className="border border-border/50 rounded-lg p-2.5 flex flex-col gap-1.5 bg-card/40"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 ${meta.tone === 'emerald' ? 'text-emerald-300' : meta.tone === 'rose' ? 'text-rose-300' : meta.tone === 'violet' ? 'text-violet-300' : meta.tone === 'cyan' ? 'text-cyan-300' : 'text-slate-400'}`} />
          <span className="text-xs font-semibold text-foreground">
            {event.minute !== null && event.minute !== undefined ? `${event.minute}'` : '—'}
          </span>
          <span className="text-xs text-foreground/90">
            {rec.market || rec.title || '—'}
          </span>
          {event.score?.label && (
            <span className="text-[10px] text-muted-foreground font-mono-tabular">
              {event.score.label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 flex-wrap justify-end">
          <Badge tone={event.source === 'manual' ? 'violet' : 'cyan'}>
            {event.source === 'manual' ? 'MANUAL' : 'ENGINE'}
          </Badge>
          <Badge tone={meta.tone}>{meta.label.toUpperCase()}</Badge>
        </div>
      </div>

      {(rec.selection || rec.confidence) && (
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          {rec.selection && (
            <span className="truncate">{rec.selection}</span>
          )}
          {rec.confidence !== null && rec.confidence !== undefined && (
            <span>Conf: <span className="font-mono-tabular text-foreground/90">{rec.confidence}/100</span></span>
          )}
        </div>
      )}

      {event.reason && (
        <p className="text-[11px] text-muted-foreground leading-snug">{event.reason}</p>
      )}

      {outcome.settlement_reason && (status === 'hit' || status === 'miss') && (
        <p className={`text-[11px] leading-snug inline-flex items-start gap-1 ${status === 'hit' ? 'text-emerald-200' : 'text-rose-200'}`}>
          <CheckCircle2 className="h-3 w-3 mt-0.5 shrink-0" />
          {outcome.settlement_reason}
          {outcome.settled_minute !== null && outcome.settled_minute !== undefined && (
            <span className="text-muted-foreground"> · @ {outcome.settled_minute}'{outcome.settled_score ? ` (${outcome.settled_score})` : ''}</span>
          )}
        </p>
      )}
    </div>
  );
}

function ManualEventForm({ matchId, matchLabel, onSubmit, onClose }) {
  const [form, setForm] = useState({
    minute: '',
    score_label: '',
    market: 'BTTS YES',
    selection: '',
    confidence: '',
    reason: '',
    result: 'pending',
    settled_minute: '',
    settled_score: '',
    settlement_reason: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!form.minute || !form.market) return;
    setSubmitting(true);
    try {
      const scoreParts = (form.score_label || '').split('-').map((s) => parseInt(s.trim(), 10));
      const payload = {
        sport: 'football',
        match_id: matchId,
        match_label: matchLabel,
        minute: parseInt(form.minute, 10),
        score: scoreParts.length === 2 && !Number.isNaN(scoreParts[0]) ? {
          home: scoreParts[0],
          away: scoreParts[1],
          label: form.score_label,
        } : { label: form.score_label },
        recommendation: {
          market: form.market,
          selection: form.selection || form.market,
          confidence: form.confidence ? parseInt(form.confidence, 10) : null,
          recommended_action: 'LIVE_ENTRY',
        },
        reason: form.reason || null,
        reason_codes: ['MANUAL_BACKFILL'],
        outcome: {
          result: form.result,
          settled_minute: form.settled_minute ? parseInt(form.settled_minute, 10) : null,
          settled_score: form.settled_score || null,
          settlement_reason: form.settlement_reason || null,
        },
      };
      await onSubmit(payload);
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls = "w-full px-2 py-1 text-xs bg-background border border-border/60 rounded-md text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:border-cyan-500/60";

  return (
    <form onSubmit={submit} className="border border-border/60 rounded-lg p-3 flex flex-col gap-2 bg-card/30" data-testid="manual-event-form">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-foreground/80">
        Registrar evento manual
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input className={inputCls} placeholder="Minuto (ej. 42)" value={form.minute} onChange={(e) => update('minute', e.target.value)} data-testid="manual-input-minute" />
        <input className={inputCls} placeholder="Marcador (ej. 1-0)" value={form.score_label} onChange={(e) => update('score_label', e.target.value)} data-testid="manual-input-score" />
        <input className={inputCls} placeholder="Mercado (ej. BTTS YES)" value={form.market} onChange={(e) => update('market', e.target.value)} data-testid="manual-input-market" />
        <input className={inputCls} placeholder="Selección" value={form.selection} onChange={(e) => update('selection', e.target.value)} data-testid="manual-input-selection" />
        <input className={inputCls} placeholder="Confianza 0-100" value={form.confidence} onChange={(e) => update('confidence', e.target.value)} data-testid="manual-input-confidence" />
        <select className={inputCls} value={form.result} onChange={(e) => update('result', e.target.value)} data-testid="manual-input-result">
          <option value="pending">Pendiente</option>
          <option value="hit">Cumplido</option>
          <option value="miss">Fallado</option>
          <option value="void">Anulado</option>
        </select>
      </div>
      <textarea className={inputCls} rows={2} placeholder="Razón / contexto" value={form.reason} onChange={(e) => update('reason', e.target.value)} data-testid="manual-input-reason" />
      {(form.result === 'hit' || form.result === 'miss') && (
        <div className="grid grid-cols-2 gap-2">
          <input className={inputCls} placeholder="Minuto resuelto" value={form.settled_minute} onChange={(e) => update('settled_minute', e.target.value)} data-testid="manual-input-settled-minute" />
          <input className={inputCls} placeholder="Marcador resuelto (ej. 1-1)" value={form.settled_score} onChange={(e) => update('settled_score', e.target.value)} data-testid="manual-input-settled-score" />
          <textarea className={`${inputCls} col-span-2`} rows={2} placeholder="Razón de resolución" value={form.settlement_reason} onChange={(e) => update('settlement_reason', e.target.value)} data-testid="manual-input-settlement-reason" />
        </div>
      )}
      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose} data-testid="manual-cancel">
          Cancelar
        </Button>
        <Button type="submit" size="sm" disabled={submitting || !form.minute || !form.market} data-testid="manual-submit">
          <Send className="h-3 w-3 mr-1" />
          Guardar
        </Button>
      </div>
    </form>
  );
}

export function LiveRecommendationTimeline({ matchId, matchLabel, league, sport = 'football', testId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [showManual, setShowManual] = useState(false);

  const fetchTimeline = useCallback(async () => {
    if (!matchId) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.get('/live/recommendation-events', {
        params: {
          match_id:    matchId,
          sport,
          limit:       50,
          auto_settle: true,
        },
      });
      if (r.data?.ok) {
        setItems(Array.isArray(r.data.items) ? r.data.items : []);
      } else {
        setError(r.data?.reason || 'no se pudo cargar');
        setItems([]);
      }
    } catch (err) {
      setError(String(err?.message || err));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [matchId, sport]);

  useEffect(() => { fetchTimeline(); }, [fetchTimeline]);

  const submitManual = async (payload) => {
    try {
      // Inject defaults from the parent context if the form did not.
      const finalPayload = {
        ...payload,
        sport:       payload.sport       || sport,
        match_id:    payload.match_id    || matchId,
        match_label: payload.match_label || matchLabel || null,
        league:      payload.league      || league     || null,
      };
      const r = await api.post('/live/recommendation-events/manual', finalPayload);
      if (r.data?.ok) {
        toast.success('Evento manual guardado.');
        setShowManual(false);
        await fetchTimeline();
      } else {
        const reason = r.data?.reason;
        toast.error(
          reason === 'invalid_or_duplicate'
            ? 'Duplicado o payload inválido.'
            : `No se pudo guardar el evento${reason ? `: ${reason}` : ''}.`
        );
      }
    } catch (err) {
      setError(String(err?.message || err));
      toast.error(`Error: ${err?.message || err}`);
    }
  };

  if (!matchId) return null;

  return (
    <section
      data-testid={testId || `live-reco-timeline-${matchId}`}
      className="border border-border/60 rounded-xl bg-card/50 p-3 flex flex-col gap-2.5"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <History className="h-4 w-4 text-cyan-300" />
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground/90">
            Historial Live
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            type="button" variant="ghost" size="sm"
            onClick={fetchTimeline}
            disabled={loading}
            data-testid="live-reco-refresh"
            title="Refrescar timeline"
          >
            <RotateCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          <Button
            type="button" variant="ghost" size="sm"
            onClick={() => setShowManual((v) => !v)}
            data-testid="live-reco-toggle-manual"
            title="Registrar evento manual"
          >
            <Plus className="h-3 w-3 mr-1" />
            Manual
          </Button>
        </div>
      </header>

      {error && (
        <p className="text-[11px] text-amber-200 inline-flex items-start gap-1" data-testid="live-reco-error">
          <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      {showManual && (
        <ManualEventForm
          matchId={matchId}
          matchLabel={matchLabel}
          onSubmit={submitManual}
          onClose={() => setShowManual(false)}
        />
      )}

      {items.length === 0 && !loading && (
        <p className="text-[11px] text-muted-foreground" data-testid="live-reco-empty">
          Sin recomendaciones live registradas todavía.
        </p>
      )}

      <div className="flex flex-col gap-2">
        {items.map((ev) => (
          <EventCard key={ev.event_id} event={ev} />
        ))}
      </div>
    </section>
  );
}

export default LiveRecommendationTimeline;
