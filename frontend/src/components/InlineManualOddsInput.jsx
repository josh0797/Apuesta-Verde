import { useState } from 'react';
import { Calculator } from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';

/**
 * InlineManualOddsInput
 * ---------------------
 * Lightweight inline replacement for the "Cuota aprox.: —" line shown on
 * MLB pick cards when the engine produced a structural lean but no
 * automatic odds were available. Mirrors the heavier ManualOddsReviewPanel
 * flow but stays compact enough to live INSIDE the recommendation row.
 *
 * Posts to `POST /api/mlb/picks/{pick_id}/manual-odds` (same endpoint as
 * the batch review panel) and shows the resulting `value_status` +
 * `manual_edge_pct` so the user can decide on the spot whether the price
 * has value.
 *
 * Constraints / scope:
 *   • Baseball-only (caller gates with `sport === "baseball"`).
 *   • Accepts both "1.85" and "1,85" (Spanish comma decimal).
 *   • Hidden when the pick already has automatic odds (caller decides).
 *   • Fail-soft: any API error surfaces via toast — local state is
 *     untouched so the user can retry.
 */
export function InlineManualOddsInput({ pickId, lang = 'es', testId }) {
  const [value, setValue]   = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved]   = useState(null);

  if (!pickId) return null;

  const ranLabel = lang === 'en' ? 'Add manual odds' : 'Agregar cuota manual';
  const help = lang === 'en'
    ? 'Paste your bookie odds to compute edge'
    : 'Pega la cuota de tu bookie para calcular edge';

  const submit = async () => {
    if (saving || !value) return;
    setSaving(true);
    try {
      const r = await api.post(`/mlb/picks/${pickId}/manual-odds`, {
        manual_odds: value,
        promote_if_value: false,
      });
      setSaved(r.data);
      const edge = Number(r.data?.manual_edge_pct ?? 0);
      toast.success(
        lang === 'en'
          ? `Saved — ${r.data?.value_status} (${edge >= 0 ? '+' : ''}${edge.toFixed(1)}%)`
          : `Cuota guardada — ${r.data?.value_status} (edge ${edge >= 0 ? '+' : ''}${edge.toFixed(1)}%)`,
      );
    } catch (err) {
      const detail = err?.response?.data?.detail
        || (lang === 'en' ? 'Could not save odds' : 'No se pudo guardar la cuota');
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const statusCls = saved?.value_status === 'VALUE'
    ? 'text-emerald-300'
    : saved?.value_status === 'FAIR_VALUE'
      ? 'text-amber-300'
      : saved?.value_status
        ? 'text-rose-300'
        : 'text-muted-foreground';

  return (
    <div
      className="rounded-md border border-cyan-500/25 bg-cyan-500/[0.05] px-2.5 py-2 flex flex-wrap items-center gap-2"
      data-testid={testId || 'inline-manual-odds-input'}
    >
      <label
        htmlFor={`inline-odds-${pickId}`}
        className="text-[11px] text-cyan-200 flex items-center gap-1.5 shrink-0"
      >
        <Calculator className="h-3 w-3" />
        {ranLabel}
      </label>
      <input
        id={`inline-odds-${pickId}`}
        type="text"
        inputMode="decimal"
        placeholder={lang === 'en' ? '1.90 or 1,90' : '1.90 o 1,90'}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-20 text-[12px] tabular-nums bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
        data-testid={`${testId || 'inline-manual-odds-input'}-field`}
      />
      <button
        type="button"
        onClick={submit}
        disabled={saving || !value}
        className="text-[10.5px] px-2 py-1 rounded border border-cyan-500/30 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        data-testid={`${testId || 'inline-manual-odds-input'}-submit`}
      >
        {saving
          ? (lang === 'en' ? 'Saving…' : 'Guardando…')
          : (lang === 'en' ? 'Save' : 'Guardar')}
      </button>
      {saved ? (
        <span
          className={`text-[10.5px] tabular-nums ${statusCls}`}
          data-testid={`${testId || 'inline-manual-odds-input'}-status`}
        >
          {saved.value_status}
          {' '}
          ({Number(saved.manual_edge_pct ?? 0) >= 0 ? '+' : ''}
          {Number(saved.manual_edge_pct ?? 0).toFixed(1)}%)
        </span>
      ) : (
        <span className="text-[10px] text-muted-foreground/80 ml-auto">{help}</span>
      )}
    </div>
  );
}

export default InlineManualOddsInput;
