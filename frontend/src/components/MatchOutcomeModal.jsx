/**
 * MatchOutcomeModal — Pick settlement with "Actual bet" recording.
 *
 * Phase 42 (Line Learning Engine, Entrega A — Feature 7).
 *
 * Renders a Shadcn Dialog with:
 *   1. The engine recommendation (read-only summary chips).
 *   2. Optional "Actual bet" form — different market/selection/line/odds
 *      than the engine when the user took a more protected line.
 *   3. Result radio: Won / Lost / Push / Void / Cancelled / Refund /
 *      CashoutWin / CashoutLoss.
 *   4. Submit → POST /api/picks/track with the full TrackIn payload,
 *      including the actual_* fields. The backend builds a
 *      line_learning sample and persists it to mongo.
 *
 * Caller wires `onConfirmed(payload)` to refresh UI state (or pass null
 * to just close).
 */
import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { normalizeManualOddsInput, normalizeDecimalOdds } from '@/lib/normalizeDecimalOdds';
import { toast } from 'sonner';

/**
 * Normalize a free-text line input ("9,5", "9.5") into a JS Number,
 * or null when invalid. We allow any positive number — line semantics
 * are decided by the market type.
 */
function normalizeLineInput(value) {
  if (value === null || value === undefined) return null;
  const raw = String(value).replace(',', '.').trim();
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

const OUTCOME_BUTTONS = [
  { value: 'won',           label_es: '✅ Gané',        label_en: '✅ Won',          tone: 'emerald' },
  { value: 'lost',          label_es: '❌ Perdí',       label_en: '❌ Lost',         tone: 'red' },
  { value: 'push',          label_es: '↩ Push',         label_en: '↩ Push',          tone: 'amber' },
  { value: 'void',          label_es: '↩ Void',         label_en: '↩ Void',          tone: 'amber' },
  { value: 'refund',        label_es: '⊘ Refund',       label_en: '⊘ Refund',        tone: 'slate' },
  { value: 'cancelled',     label_es: '⊘ Cancelada',    label_en: '⊘ Cancelled',     tone: 'slate' },
  { value: 'cashout_win',   label_es: '💰 Cashout +',   label_en: '💰 Cashout +',    tone: 'emerald' },
  { value: 'cashout_loss',  label_es: '💸 Cashout −',   label_en: '💸 Cashout −',    tone: 'red' },
];

const TONE_CLASSES = {
  emerald: 'border-emerald-500/40 hover:bg-emerald-500/15 text-emerald-200',
  red:     'border-red-500/40 hover:bg-red-500/15 text-red-200',
  amber:   'border-amber-500/40 hover:bg-amber-500/15 text-amber-200',
  slate:   'border-slate-500/40 hover:bg-slate-500/15 text-slate-200',
};

const TONE_SELECTED = {
  emerald: 'bg-emerald-500/30 text-emerald-100',
  red:     'bg-red-500/30 text-red-100',
  amber:   'bg-amber-500/30 text-amber-100',
  slate:   'bg-slate-500/30 text-slate-100',
};

export function MatchOutcomeModal({
  open,
  onOpenChange,
  match,
  engineRec,             // { market, selection, line, odds, projection, source }
  apiClient,             // axios-like instance (we don't import api directly so tests can mock)
  lang = 'es',
  onConfirmed,
  testIdPrefix = 'outcome-modal',
}) {
  const matchId = match?.match_id || '';
  // Initialize "actual bet" with engine values so the modal opens with
  // the user's pre-selected market filled in; they can edit anything.
  // To reset when the modal opens with a different engineRec, the parent
  // should pass a changing `key` prop — this avoids set-state-in-effect.
  const [actualMarket,    setActualMarket]    = useState(engineRec?.market    || '');
  const [actualSelection, setActualSelection] = useState(engineRec?.selection || '');
  const [actualLine,      setActualLine]      = useState(
    engineRec?.line != null ? String(engineRec.line) : '',
  );
  const [actualOdds,      setActualOdds]      = useState(
    engineRec?.odds != null ? String(engineRec.odds) : '',
  );
  const [finalValue,      setFinalValue]      = useState('');
  const [outcome,         setOutcome]         = useState(null);
  const [submitting,      setSubmitting]      = useState(false);
  const [learningPreview, setLearningPreview] = useState(null);

  const isUnder = ((actualSelection || engineRec?.selection || '').toLowerCase().includes('under'));
  const engineLine = normalizeLineInput(engineRec?.line != null ? String(engineRec.line) : '');
  const userLine   = normalizeLineInput(actualLine);
  const lineDistance = (engineLine != null && userLine != null)
    ? Math.round((userLine - engineLine) * 100) / 100
    : null;

  const submit = async () => {
    if (!outcome) {
      toast.error(lang === 'en' ? 'Pick an outcome first' : 'Marca un resultado primero');
      return;
    }
    setSubmitting(true);
    try {
      const lineNum     = normalizeLineInput(actualLine);
      const oddsNum     = normalizeManualOddsInput(actualOdds) ?? normalizeDecimalOdds(actualOdds);
      const finalNum    = normalizeLineInput(finalValue);
      const isManual    = (
        actualMarket    !== (engineRec?.market    || '') ||
        actualSelection !== (engineRec?.selection || '') ||
        (engineLine != null && userLine != null && lineDistance !== 0) ||
        (oddsNum != null && engineRec?.odds != null && Math.abs(oddsNum - engineRec.odds) > 0.01)
      );

      const payload = {
        run_id:    `outcome-modal-${matchId}-${Date.now()}`,
        match_id:  String(matchId),
        sport:     match?.sport || 'football',
        league:    match?.league || '',
        match_label: `${match?.home_team?.name || 'Home'} vs ${match?.away_team?.name || 'Away'}`,
        market:        engineRec?.market    || actualMarket,
        selection:     engineRec?.selection || actualSelection,
        line:          engineLine,
        odds:          engineRec?.odds || null,
        confidence_score: engineRec?.confidence ?? 0,
        outcome,
        // Phase 42 — Line Learning fields.
        actual_market:     actualMarket,
        actual_selection:  actualSelection,
        actual_line:       lineNum,
        actual_odds:       oddsNum,
        actual_outcome:    outcome,
        engine_projection: engineRec?.projection ?? null,
        final_value:       finalNum,
        market_type:       inferMarketType(engineRec?.market || actualMarket, actualSelection),
        source:            isManual ? 'manual' : 'engine',
        is_live:           !!engineRec?.is_live,
      };
      const res = await apiClient.post('/picks/track', payload);
      const learning = res?.data?.line_learning || null;
      setLearningPreview(learning);
      toast.success(lang === 'en' ? 'Pick recorded' : 'Pick guardado');
      if (typeof onConfirmed === 'function') {
        onConfirmed({ payload, response: res?.data });
      }
      // Auto-close 2 seconds later so the user can read the learning summary.
      setTimeout(() => onOpenChange(false), 2000);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || (lang === 'en' ? 'Save failed' : 'No se pudo guardar');
      toast.error(typeof msg === 'string' ? msg : 'Error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md sm:max-w-lg" data-testid={`${testIdPrefix}-${matchId}`}>
        <DialogHeader>
          <DialogTitle>{lang === 'en' ? 'Mark outcome' : 'Marcar resultado'}</DialogTitle>
          <DialogDescription className="text-xs">
            {lang === 'en'
              ? 'Record what really happened. If your actual bet differed from the engine, fill the fields below — the learning loop uses them.'
              : 'Registra lo que realmente pasó. Si tu apuesta real fue distinta a la del engine, completa los campos — el sistema aprende de eso.'}
          </DialogDescription>
        </DialogHeader>

        {/* Engine recommendation summary */}
        <div
          className="rounded-md border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 space-y-1"
          data-testid={`${testIdPrefix}-engine-summary-${matchId}`}
        >
          <div className="text-[10px] uppercase tracking-wider opacity-70">
            {lang === 'en' ? 'Engine recommendation' : 'Recomendación del engine'}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Badge variant="outline">{engineRec?.market || '—'}</Badge>
            <Badge variant="outline">{engineRec?.selection || '—'}</Badge>
            {engineRec?.line != null && <Badge variant="outline">Línea {engineRec.line}</Badge>}
            {engineRec?.odds != null && <Badge variant="outline">@ {engineRec.odds}</Badge>}
            {engineRec?.projection != null && <Badge variant="outline">Proy. {engineRec.projection}</Badge>}
          </div>
        </div>

        {/* Actual bet form */}
        <div className="space-y-2 border-t border-border pt-2">
          <div className="text-[10px] uppercase tracking-wider opacity-70">
            {lang === 'en' ? 'Your actual bet (if different)' : 'Tu apuesta real (si fue distinta)'}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label htmlFor={`actual-market-${matchId}`} className="text-[10px] opacity-70">
                {lang === 'en' ? 'Market' : 'Mercado'}
              </Label>
              <Input
                id={`actual-market-${matchId}`}
                value={actualMarket}
                onChange={(e) => setActualMarket(e.target.value)}
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-actual-market-${matchId}`}
                placeholder="total_runs_under"
              />
            </div>
            <div>
              <Label htmlFor={`actual-selection-${matchId}`} className="text-[10px] opacity-70">
                {lang === 'en' ? 'Selection' : 'Selección'}
              </Label>
              <Input
                id={`actual-selection-${matchId}`}
                value={actualSelection}
                onChange={(e) => setActualSelection(e.target.value)}
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-actual-selection-${matchId}`}
                placeholder="Under 10.0"
              />
            </div>
            <div>
              <Label htmlFor={`actual-line-${matchId}`} className="text-[10px] opacity-70">
                {lang === 'en' ? 'Line' : 'Línea'}
              </Label>
              <Input
                id={`actual-line-${matchId}`}
                value={actualLine}
                onChange={(e) => setActualLine(e.target.value.replace(/[^0-9.,-]/g, ''))}
                type="text"
                inputMode="decimal"
                pattern="[0-9]+([.,][0-9]+)?"
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-actual-line-${matchId}`}
                placeholder="10.0"
              />
            </div>
            <div>
              <Label htmlFor={`actual-odds-${matchId}`} className="text-[10px] opacity-70">
                {lang === 'en' ? 'Odds' : 'Cuota'}
              </Label>
              <Input
                id={`actual-odds-${matchId}`}
                value={actualOdds}
                onChange={(e) => setActualOdds(e.target.value.replace(/[^0-9.,]/g, ''))}
                type="text"
                inputMode="decimal"
                pattern="[0-9]+([.,][0-9]+)?"
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-actual-odds-${matchId}`}
                placeholder="1.26"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label htmlFor={`final-value-${matchId}`} className="text-[10px] opacity-70">
                {lang === 'en' ? 'Final value (e.g. total runs)' : 'Valor final (ej. carreras totales)'}
              </Label>
              <Input
                id={`final-value-${matchId}`}
                value={finalValue}
                onChange={(e) => setFinalValue(e.target.value.replace(/[^0-9.,-]/g, ''))}
                type="text"
                inputMode="decimal"
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-final-value-${matchId}`}
                placeholder="10"
              />
            </div>
            {lineDistance !== null && Math.abs(lineDistance) > 0.001 && (
              <div className="flex items-end">
                <span
                  className={`text-[10px] px-2 py-1 rounded-md border ${
                    (isUnder && lineDistance > 0) || (!isUnder && lineDistance < 0)
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                      : 'border-amber-500/40 bg-amber-500/10 text-amber-200'
                  }`}
                  data-testid={`${testIdPrefix}-line-distance-${matchId}`}
                >
                  Δlínea {lineDistance > 0 ? '+' : ''}{lineDistance}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Outcome buttons */}
        <div className="space-y-2 border-t border-border pt-2">
          <div className="text-[10px] uppercase tracking-wider opacity-70">
            {lang === 'en' ? 'Result' : 'Resultado'}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {OUTCOME_BUTTONS.map((b) => (
              <button
                key={b.value}
                type="button"
                onClick={() => setOutcome(b.value)}
                disabled={submitting}
                className={`text-[11px] py-1.5 px-2 rounded-md border transition-colors ${TONE_CLASSES[b.tone]} ${
                  outcome === b.value ? TONE_SELECTED[b.tone] : ''
                }`}
                data-testid={`${testIdPrefix}-outcome-${b.value}-${matchId}`}
              >
                {lang === 'en' ? b.label_en : b.label_es}
              </button>
            ))}
          </div>
        </div>

        {/* Learning preview (rendered after submit) */}
        {learningPreview && (
          <div
            className="rounded-md border border-fuchsia-500/30 bg-fuchsia-500/5 px-3 py-2 space-y-1"
            data-testid={`${testIdPrefix}-learning-${matchId}`}
          >
            <div className="text-[10px] uppercase tracking-wider opacity-70">
              {lang === 'en' ? 'Learning summary' : 'Resumen de aprendizaje'}
            </div>
            <div className="text-[11px] flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="border-fuchsia-400/40 text-fuchsia-200">
                {learningPreview.classification || '—'}
              </Badge>
              {(learningPreview.reason_codes || []).map((rc) => (
                <Badge key={rc} variant="outline" className="text-[10px]">
                  {rc}
                </Badge>
              ))}
            </div>
            <p className="text-[11px] opacity-80 leading-snug">
              {lang === 'en' ? learningPreview.summary_en : learningPreview.summary_es}
            </p>
            {learningPreview.observe_only && (
              <p className="text-[10px] italic opacity-70">
                {lang === 'en'
                  ? '(observe-only — does not bias recommendations yet)'
                  : '(modo observe-only — todavía no sesga recomendaciones)'}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid={`${testIdPrefix}-cancel-${matchId}`}
          >
            {lang === 'en' ? 'Cancel' : 'Cancelar'}
          </Button>
          <Button
            onClick={submit}
            disabled={!outcome || submitting}
            data-testid={`${testIdPrefix}-submit-${matchId}`}
          >
            {submitting
              ? (lang === 'en' ? 'Saving…' : 'Guardando…')
              : (lang === 'en' ? 'Save' : 'Guardar')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function inferMarketType(market, selection) {
  const blob = `${market || ''} ${selection || ''}`.toLowerCase();
  if (blob.includes('runs')) return 'total_runs';
  if (blob.includes('puntos') || blob.includes('points')) return 'total_points';
  if (blob.includes('spread') || blob.includes('handicap')) return 'spread';
  if (blob.includes('btts') || blob.includes('goals') || blob.includes('over') || blob.includes('under')) {
    return 'total_goals';
  }
  return 'other';
}

export default MatchOutcomeModal;
