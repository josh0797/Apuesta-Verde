/**
 * UserBetBackfillModal — Retroactive editor for the user's actual bet
 * on a previously-tracked pick. Drives PATCH /api/picks/{uid}/user-bet.
 *
 * Lives in HistoryPage. Only enabled for already-settled rows so we
 * can re-compute divergence against the official final score.
 */
import { useState } from 'react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { Pencil } from 'lucide-react';

const MARKETS = {
  baseball: [
    { value: 'total_runs',     es: 'Total de carreras' },
    { value: 'f5_total_runs',  es: 'F5 Total (5 inn.)' },
    { value: 'run_line',       es: 'Run Line ±1.5' },
    { value: 'moneyline',      es: 'Moneyline' },
  ],
  football: [
    { value: 'total_goals',    es: 'Total de goles' },
    { value: 'btts',           es: 'Ambos marcan (BTTS)' },
    { value: 'double_chance',  es: 'Doble oportunidad (DC)' },
    { value: 'moneyline_1x2',  es: 'Ganador 1X2' },
    { value: 'handicap',       es: 'Hándicap' },
  ],
};
const SIDES = {
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

function normaliseOdds(raw) {
  if (raw == null || raw === '') return null;
  const s = String(raw).trim().replace(',', '.');
  if (/^[+-]\d+$/.test(s)) {
    const n = parseInt(s, 10);
    if (!Number.isFinite(n) || n === 0) return null;
    return n > 0 ? (1 + n / 100) : (1 + 100 / Math.abs(n));
  }
  const n = parseFloat(s);
  if (!Number.isFinite(n) || n < 1.01) return null;
  return n;
}

export function UserBetBackfillModal({
  open, onClose, row, lang = 'es', onSaved,
}) {
  const sport = (row?.sport || 'baseball').toLowerCase();
  const sportKey = sport === 'football' ? 'football' : 'baseball';
  const initBet = row?.actual_bet || {};
  const [market, setMarket]     = useState(() => initBet.market    || row?.market || '');
  const [side, setSide]         = useState(() => initBet.selection || row?.selection || '');
  const [line, setLine]         = useState(() => initBet.line ?? row?.line ?? '');
  const [oddsRaw, setOddsRaw]   = useState(() =>
    initBet.odds != null ? String(initBet.odds)
    : (row?.odds != null ? String(row.odds) : '')
  );
  const [saving, setSaving]     = useState(false);

  // NOTE: Reset is handled by the parent via a changing `key` prop
  // (we use the pick_id of the row). Avoids set-state-in-effect lints.

  if (!row) return null;
  const marketsOpts = MARKETS[sportKey] || MARKETS.baseball;
  const sideOpts    = SIDES[market]     || [];

  const submit = async (e) => {
    e?.preventDefault?.();
    const odds = normaliseOdds(oddsRaw);
    setSaving(true);
    try {
      const body = {
        market,
        selection: side,
        line: line === '' || line == null ? null : Number(String(line).replace(',', '.')),
        odds,
      };
      const res = await api.patch(`/picks/${encodeURIComponent(row.pick_id)}/user-bet`, body);
      toast.success(lang === 'en' ? 'Backfill saved' : 'Backfill guardado');
      onSaved?.(res.data);
      onClose?.();
    } catch (err) {
      toast.error(err?.response?.data?.detail || (lang === 'en' ? 'Save failed' : 'No se pudo guardar'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose?.(); }}>
      <DialogContent
        className="sm:max-w-md"
        data-testid="user-bet-backfill-modal"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Pencil className="h-4 w-4 text-cyan-400" />
            {lang === 'en' ? 'Edit your actual bet' : 'Editar tu apuesta real'}
          </DialogTitle>
          <DialogDescription className="text-xs opacity-80 pt-1">
            {lang === 'en'
              ? 'Set the market, side, line and odds you actually wagered. We will recompute divergence vs the engine pick.'
              : 'Define el mercado, lado, línea y cuota que realmente apostaste. Recalcularemos la divergencia vs el pick del engine.'}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3 py-2">
          <div>
            <Label className="text-[11px]" htmlFor="bf-market">{lang === 'en' ? 'Market' : 'Mercado'}</Label>
            <Select value={market} onValueChange={setMarket}>
              <SelectTrigger id="bf-market" data-testid="bf-market-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {marketsOpts.map((m) => (
                  <SelectItem key={m.value} value={m.value}>{m.es}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {sideOpts.length > 0 && (
            <div>
              <Label className="text-[11px]" htmlFor="bf-side">{lang === 'en' ? 'Side' : 'Lado'}</Label>
              <Select value={side} onValueChange={setSide}>
                <SelectTrigger id="bf-side" data-testid="bf-side-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sideOpts.map((s) => (
                    <SelectItem key={s.v} value={s.v}>{s.es}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {['total_runs','f5_total_runs','total_goals','run_line','handicap'].includes(market) && (
            <div>
              <Label className="text-[11px]" htmlFor="bf-line">{lang === 'en' ? 'Line' : 'Línea'}</Label>
              <Input
                id="bf-line"
                inputMode="decimal"
                value={line}
                onChange={(e) => setLine(e.target.value)}
                data-testid="bf-line-input"
              />
            </div>
          )}
          <div>
            <Label className="text-[11px]" htmlFor="bf-odds">
              {lang === 'en' ? 'Odds (decimal or american)' : 'Cuota (decimal o americana)'}
            </Label>
            <Input
              id="bf-odds"
              inputMode="decimal"
              placeholder="1.72 / 1,72 / -110 / +125"
              value={oddsRaw}
              onChange={(e) => setOddsRaw(e.target.value)}
              data-testid="bf-odds-input"
            />
          </div>
          <DialogFooter className="grid grid-cols-2 gap-2 pt-2">
            <Button
              type="button" variant="outline"
              onClick={() => onClose?.()}
              data-testid="bf-cancel"
            >
              {lang === 'en' ? 'Cancel' : 'Cancelar'}
            </Button>
            <Button
              type="submit"
              disabled={saving || !market || !side}
              className="bg-cyan-600 hover:bg-cyan-500"
              data-testid="bf-submit"
            >
              {saving
                ? (lang === 'en' ? 'Saving…' : 'Guardando…')
                : (lang === 'en' ? 'Save' : 'Guardar')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default UserBetBackfillModal;
