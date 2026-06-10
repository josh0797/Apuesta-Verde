/**
 * EnginePickConfirmModal
 * =======================
 *
 * Two-step modal that asks the user, BEFORE we record a settlement or
 * a live track-in, whether they actually wagered exactly the engine's
 * recommendation — or a different market/line/odds.
 *
 *   Step 1 — yes/no question:
 *     "El engine recomendó <X>. ¿Fue exactamente la apuesta que realizaste?"
 *
 *   Step 2 (only if "No") — form for actual_market / actual_selection /
 *   actual_line / actual_odds, with normalisation for both decimal and
 *   american odds.
 *
 * The modal is intentionally UI-only — it returns its result via
 * ``onConfirm({actualBet})`` and the parent component decides what to
 * do with it (track, settle, backfill, etc).
 *
 * Props
 * -----
 * open               boolean — show/hide
 * onClose()          called on cancel
 * onConfirm(payload) called with:
 *                     { followed_engine: true }
 *                       — or —
 *                     { followed_engine: false,
 *                       actual_market, actual_selection,
 *                       actual_line, actual_odds }
 * engine.market      string  — e.g. "total_runs"
 * engine.selection   string  — e.g. "UNDER"
 * engine.line        number  — e.g. 9.5
 * engine.label       string  — pretty label for the question ("UNDER 9.5")
 * sport              "baseball" | "football"
 * lang               "es" | "en"
 */
import { useMemo, useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { CheckCircle2, AlertCircle, ArrowLeft } from 'lucide-react';

const MLB_MARKETS = [
  { value: 'total_runs',     es: 'Total de carreras' },
  { value: 'f5_total_runs',  es: 'F5 Total (5 inn.)' },
  { value: 'run_line',       es: 'Run Line ±1.5' },
  { value: 'moneyline',      es: 'Moneyline' },
];

const FOOTBALL_MARKETS = [
  { value: 'total_goals',    es: 'Total de goles' },
  { value: 'btts',           es: 'Ambos marcan (BTTS)' },
  { value: 'double_chance',  es: 'Doble oportunidad (DC)' },
  { value: 'moneyline_1x2',  es: 'Ganador 1X2' },
  { value: 'handicap',       es: 'Hándicap' },
];

const SIDE_OPTIONS = {
  total_runs:    [{v:'UNDER',es:'Menos de'},{v:'OVER',es:'Más de'}],
  f5_total_runs: [{v:'UNDER',es:'F5 Menos de'},{v:'OVER',es:'F5 Más de'}],
  run_line:      [{v:'HOME',es:'Local'},{v:'AWAY',es:'Visitante'}],
  moneyline:     [{v:'HOME',es:'Local (ML)'},{v:'AWAY',es:'Visitante (ML)'}],
  total_goals:   [{v:'UNDER',es:'Menos de'},{v:'OVER',es:'Más de'}],
  btts:          [{v:'YES',es:'Sí'},{v:'NO',es:'No'}],
  double_chance: [{v:'1X',es:'1X'},{v:'12',es:'12'},{v:'X2',es:'X2'}],
  moneyline_1x2: [{v:'HOME',es:'Local'},{v:'DRAW',es:'Empate'},{v:'AWAY',es:'Visitante'}],
  handicap:      [{v:'HOME',es:'Local'},{v:'AWAY',es:'Visitante'}],
};

/** Normalise an odds string ("1,72", "1.72", "-110", "+125") to decimal. */
function normaliseOdds(raw) {
  if (raw == null || raw === '') return null;
  const s = String(raw).trim().replace(',', '.');
  // American odds?
  if (/^[+-]\d+$/.test(s)) {
    const n = parseInt(s, 10);
    if (!Number.isFinite(n) || n === 0) return null;
    return n > 0 ? (1 + n / 100) : (1 + 100 / Math.abs(n));
  }
  const n = parseFloat(s);
  if (!Number.isFinite(n) || n < 1.01) return null;
  return n;
}

export function EnginePickConfirmModal({
  open, onClose, onConfirm,
  engine = {}, sport = 'baseball', lang = 'es',
}) {
  const [step, setStep] = useState(0);   // 0 = Yes/No question, 1 = form
  const [market, setMarket] = useState(() => engine.market || '');
  const [side, setSide] = useState('');
  const [line, setLine] = useState('');
  const [oddsRaw, setOddsRaw] = useState('');
  const [oddsErr, setOddsErr] = useState(false);

  // NOTE: Reset on open is handled by the parent via a changing `key`
  // prop. This component does NOT auto-reset internally, which keeps
  // the render free of set-state-in-effect side effects.

  const marketOptions = sport === 'football' ? FOOTBALL_MARKETS : MLB_MARKETS;
  const sideOptions   = SIDE_OPTIONS[market] || [];

  const engineLabel = useMemo(() => {
    if (engine.label) return engine.label;
    const sel = (engine.selection || '').toUpperCase();
    const ln  = engine.line != null ? ` ${engine.line}` : '';
    return `${sel}${ln}`.trim() || '—';
  }, [engine.label, engine.selection, engine.line]);

  function handleYes() {
    onConfirm?.({ followed_engine: true });
    onClose?.();
  }

  function handleSubmit(e) {
    e?.preventDefault?.();
    const odds = normaliseOdds(oddsRaw);
    if (oddsRaw && odds == null) {
      setOddsErr(true);
      return;
    }
    onConfirm?.({
      followed_engine:  false,
      actual_market:    market || null,
      actual_selection: side || null,
      actual_line:      line === '' ? null : Number(String(line).replace(',', '.')),
      actual_odds:      odds,
    });
    onClose?.();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose?.(); }}>
      <DialogContent
        className="sm:max-w-md"
        data-testid="engine-pick-confirm-modal"
      >
        {step === 0 ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-base">
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                {lang === 'en'
                  ? 'Did you bet the engine pick exactly?'
                  : '¿Apostaste la recomendación exacta?'}
              </DialogTitle>
              <DialogDescription className="text-xs opacity-80 pt-2">
                {lang === 'en'
                  ? 'The engine recommended:'
                  : 'El engine recomendó:'}
                <div
                  className="mt-2 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 font-mono text-emerald-200 text-sm"
                  data-testid="engine-pick-label"
                >
                  {engineLabel}
                </div>
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="grid grid-cols-2 gap-2 pt-2">
              <Button
                variant="outline"
                onClick={() => setStep(1)}
                className="border-amber-500/40 text-amber-200 hover:bg-amber-500/10"
                data-testid="engine-pick-confirm-no"
              >
                <AlertCircle className="h-3.5 w-3.5 mr-1.5" />
                {lang === 'en' ? 'No, I bet differently' : 'No, aposté diferente'}
              </Button>
              <Button
                onClick={handleYes}
                className="bg-emerald-600 hover:bg-emerald-500"
                data-testid="engine-pick-confirm-yes"
              >
                <CheckCircle2 className="h-3.5 w-3.5 mr-1.5" />
                {lang === 'en' ? 'Yes, exactly' : 'Sí, exactamente'}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-base">
                <button
                  type="button"
                  onClick={() => setStep(0)}
                  className="opacity-70 hover:opacity-100"
                  data-testid="engine-pick-back-btn"
                >
                  <ArrowLeft className="h-4 w-4" />
                </button>
                {lang === 'en' ? "What did you actually bet?" : '¿Qué apostaste realmente?'}
              </DialogTitle>
              <DialogDescription className="text-xs opacity-80">
                {lang === 'en'
                  ? 'Pick your actual market, side, line and odds.'
                  : 'Elige tu mercado, lado, línea y cuota reales.'}
              </DialogDescription>
            </DialogHeader>

            <div className="grid grid-cols-1 gap-3 py-3">
              {/* Market */}
              <div className="space-y-1.5">
                <Label className="text-[11px]" htmlFor="user-market">
                  {lang === 'en' ? 'Market' : 'Mercado'}
                </Label>
                <Select value={market} onValueChange={setMarket}>
                  <SelectTrigger id="user-market" data-testid="user-market-select">
                    <SelectValue placeholder={lang === 'en' ? 'Choose market' : 'Elige mercado'} />
                  </SelectTrigger>
                  <SelectContent>
                    {marketOptions.map((m) => (
                      <SelectItem
                        key={m.value} value={m.value}
                        data-testid={`user-market-opt-${m.value}`}
                      >
                        {m.es}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Side */}
              {sideOptions.length > 0 && (
                <div className="space-y-1.5">
                  <Label className="text-[11px]" htmlFor="user-side">
                    {lang === 'en' ? 'Side' : 'Lado'}
                  </Label>
                  <Select value={side} onValueChange={setSide}>
                    <SelectTrigger id="user-side" data-testid="user-side-select">
                      <SelectValue placeholder={lang === 'en' ? 'Choose side' : 'Elige lado'} />
                    </SelectTrigger>
                    <SelectContent>
                      {sideOptions.map((s) => (
                        <SelectItem
                          key={s.v} value={s.v}
                          data-testid={`user-side-opt-${s.v}`}
                        >
                          {s.es}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {/* Line — only for total / handicap / RL markets */}
              {['total_runs','f5_total_runs','total_goals','run_line','handicap']
                .includes(market) && (
                <div className="space-y-1.5">
                  <Label className="text-[11px]" htmlFor="user-line">
                    {lang === 'en' ? 'Line' : 'Línea'}
                  </Label>
                  <Input
                    id="user-line"
                    inputMode="decimal"
                    placeholder={lang === 'en' ? '9.5 / 10.5 / -1.5' : '9.5 / 10.5 / -1.5'}
                    value={line}
                    onChange={(e) => setLine(e.target.value)}
                    data-testid="user-line-input"
                  />
                </div>
              )}

              {/* Odds — accepts decimal or american */}
              <div className="space-y-1.5">
                <Label className="text-[11px]" htmlFor="user-odds">
                  {lang === 'en' ? 'Your odds (decimal or american)' : 'Tu cuota (decimal o americana)'}
                </Label>
                <Input
                  id="user-odds"
                  inputMode="decimal"
                  placeholder="1.72  /  1,83  /  -110  /  +125"
                  value={oddsRaw}
                  onChange={(e) => { setOddsRaw(e.target.value); setOddsErr(false); }}
                  className={oddsErr ? 'border-rose-500/60' : ''}
                  data-testid="user-odds-input"
                />
                {oddsErr && (
                  <p
                    className="text-[10px] text-rose-300"
                    data-testid="user-odds-error"
                  >
                    {lang === 'en'
                      ? 'Invalid odds. Use 1.50, 1,50, -110 or +125.'
                      : 'Cuota inválida. Usa 1.50, 1,50, -110 o +125.'}
                  </p>
                )}
              </div>
            </div>

            <DialogFooter className="grid grid-cols-2 gap-2">
              <Button
                type="button" variant="outline"
                onClick={() => onClose?.()}
                data-testid="engine-pick-cancel-btn"
              >
                {lang === 'en' ? 'Cancel' : 'Cancelar'}
              </Button>
              <Button
                type="submit"
                className="bg-emerald-600 hover:bg-emerald-500"
                disabled={!market || !side}
                data-testid="engine-pick-submit-btn"
              >
                {lang === 'en' ? 'Save my bet' : 'Guardar mi apuesta'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default EnginePickConfirmModal;
