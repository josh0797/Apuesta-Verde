import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Calculator, Plus, Trash2, ChevronDown, ChevronUp, Wand2 } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Sprint E.3 / Fix 2 · Comparador manual de cuotas.
 *
 * Lets the operator paste odds from one or more bookmakers and see
 * the implied probability + edge vs the model's probability for every
 * row, on demand. ``observe_only`` — no bets, never mutates the pick.
 *
 * Contract:
 *   - Markets: every ``MANUAL_MARKET_TYPES`` exposed by the backend.
 *   - ``model_prob_pct`` precarga del pick (cuando esté), editable.
 *   - Rows: ``{ id, market, selection, line, bookmaker, odd }``.
 *   - Implied  = 1/odd.
 *   - Edge     = (model_prob - implied) * 100  → in percentage points.
 *
 * Props:
 *   - matchId, homeName, awayName              (required)
 *   - initialModelProbPct                       (optional, precarga)
 *   - initialMarketTypeFromPick                 (optional, precarga)
 *   - testIdPrefix
 */
export const MarketComparatorPanel = ({
  matchId,
  homeName, awayName,
  initialModelProbPct = null,
  initialMarketTypeFromPick = null,
  testIdPrefix = 'market-comparator',
}) => {
  const [collapsed, setCollapsed]   = useState(true);
  const [marketCatalog, setCatalog] = useState(null);
  const [loading, setLoading]       = useState(false);
  const [modelProbStr, setModelProbStr] = useState(
    initialModelProbPct != null ? String(Number(initialModelProbPct).toFixed(2)) : '',
  );
  const [rows, setRows] = useState([_newRow(initialMarketTypeFromPick)]);

  // Fetch catalog the first time the panel opens (lazy → no cost when
  // collapsed).
  useEffect(() => {
    if (!collapsed && !marketCatalog && !loading) {
      setLoading(true);
      axios
        .get(`${BACKEND_URL}/api/football/manual-market-options`)
        .then((res) => setCatalog(res.data || null))
        .catch(() => setCatalog({ available: false, market_types: [],
                                    options_by_market: {} }))
        .finally(() => setLoading(false));
    }
  }, [collapsed, marketCatalog, loading]);

  const marketTypes = marketCatalog?.market_types || [];
  const optionsByMarket = marketCatalog?.options_by_market || {};

  const modelProb = useMemo(() => {
    const p = parseFloat(modelProbStr);
    if (!isFinite(p)) return null;
    if (p <= 0 || p > 100) return null;
    return p / 100.0;
  }, [modelProbStr]);

  const updateRow = (id, patch) => {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };
  const deleteRow = (id) => setRows((rs) => rs.filter((r) => r.id !== id));
  const addRow = () => setRows((rs) => [...rs, _newRow(initialMarketTypeFromPick)]);

  return (
    <Card
      className="border-violet-700/40 bg-violet-900/10"
      data-testid={`${testIdPrefix}-panel`}
    >
      <CardHeader
        className="pb-2 cursor-pointer select-none"
        onClick={() => setCollapsed((c) => !c)}
        data-testid={`${testIdPrefix}-toggle`}
      >
        <CardTitle className="text-sm flex items-center justify-between text-violet-200">
          <span className="flex items-center gap-2">
            <Calculator className="h-4 w-4" />
            Comparador manual de cuotas
            {rows.length > 1 && (
              <Badge className="bg-violet-700/40 text-violet-100 border-violet-600/60">
                {rows.length} filas
              </Badge>
            )}
          </span>
          {collapsed ? (
            <ChevronDown className="h-4 w-4 text-slate-400" />
          ) : (
            <ChevronUp className="h-4 w-4 text-slate-400" />
          )}
        </CardTitle>
        {collapsed && (
          <p className="text-[11px] text-slate-400 mt-1">
            Calculá implied + edge vs modelo sin tocar el pick original.
            <span className="ml-1 italic">observe_only</span>.
          </p>
        )}
      </CardHeader>

      {!collapsed && (
        <CardContent className="space-y-3">
          {/* Model prob bar */}
          <div className="flex items-center gap-2 flex-wrap">
            <Label className="text-[11px] text-slate-300">
              Probabilidad del modelo (%)
            </Label>
            <Input
              type="number" step="0.1" min="0.1" max="99.9"
              value={modelProbStr}
              onChange={(e) => setModelProbStr(e.target.value)}
              className="h-8 text-xs w-[110px]"
              placeholder="ej. 55.0"
              data-testid={`${testIdPrefix}-model-prob`}
            />
            <span className="text-[11px] text-slate-400">
              {modelProb != null
                ? `≈ implied ${(modelProb).toFixed(4)}`
                : 'ingresá un valor entre 0.1 y 99.9'}
            </span>
            {initialModelProbPct != null && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setModelProbStr(String(Number(initialModelProbPct).toFixed(2)))}
                data-testid={`${testIdPrefix}-reset-model`}
                className="ml-auto text-[10px] text-violet-200"
              >
                <Wand2 className="h-3 w-3 mr-1" />
                Restaurar pick
              </Button>
            )}
          </div>

          {loading && (
            <p className="text-[11px] text-slate-400">Cargando catálogo de mercados…</p>
          )}

          {/* Rows */}
          <div className="space-y-2">
            {rows.map((row, idx) => (
              <ComparatorRow
                key={row.id}
                row={row}
                idx={idx}
                marketTypes={marketTypes}
                optionsByMarket={optionsByMarket}
                modelProb={modelProb}
                onChange={(patch) => updateRow(row.id, patch)}
                onDelete={() => deleteRow(row.id)}
                canDelete={rows.length > 1}
                testIdPrefix={`${testIdPrefix}-row-${idx}`}
              />
            ))}
          </div>

          <Button
            size="sm"
            variant="outline"
            onClick={addRow}
            data-testid={`${testIdPrefix}-add-row`}
            className="border-violet-700/40 text-violet-200 hover:bg-violet-900/20"
          >
            <Plus className="h-3 w-3 mr-1" />
            Agregar fila
          </Button>

          <p className="text-[10px] text-slate-500 leading-relaxed">
            Implied = 1 / cuota. Edge = (P modelo − implied) × 100 pp. Si el edge
            es positivo, el modelo cree que la probabilidad real es mayor a la
            del mercado. Este panel <span className="font-mono">no</span> ejecuta
            apuestas — sólo te ayuda a comparar cuotas observadas.
          </p>
        </CardContent>
      )}
    </Card>
  );
};


// ─── Single row ─────────────────────────────────────────────────────────
const ComparatorRow = ({
  row, idx, marketTypes, optionsByMarket, modelProb,
  onChange, onDelete, canDelete, testIdPrefix,
}) => {
  const mkOptions = optionsByMarket?.[row.market] || null;
  const selections = mkOptions?.selections || [];
  const requiresLine = !!mkOptions?.requires_line;
  const allowedLines = mkOptions?.allowed_lines || [];

  const calc = useMemo(() => _computeRow(row, modelProb), [row, modelProb]);

  return (
    <div
      className="rounded-md border border-slate-700/40 bg-slate-900/40 p-2 space-y-2"
      data-testid={testIdPrefix}
    >
      <div className="grid grid-cols-12 gap-2 items-end">
        {/* Market */}
        <div className="col-span-12 md:col-span-3">
          <Label className="text-[10px] text-slate-400">Mercado</Label>
          <Select
            value={row.market || ''}
            onValueChange={(v) => onChange({ market: v, selection: '', line: '' })}
          >
            <SelectTrigger
              className="h-8 text-xs"
              data-testid={`${testIdPrefix}-market`}
            >
              <SelectValue placeholder="Mercado" />
            </SelectTrigger>
            <SelectContent>
              {marketTypes.length === 0 && (
                <SelectItem value="DOUBLE_CHANCE">DOUBLE_CHANCE</SelectItem>
              )}
              {marketTypes.map((m) => (
                <SelectItem key={m} value={m}>{m}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Selection */}
        <div className="col-span-6 md:col-span-2">
          <Label className="text-[10px] text-slate-400">Selección</Label>
          <Select
            value={row.selection || ''}
            onValueChange={(v) => onChange({ selection: v })}
            disabled={!selections.length}
          >
            <SelectTrigger
              className="h-8 text-xs"
              data-testid={`${testIdPrefix}-selection`}
            >
              <SelectValue placeholder="—" />
            </SelectTrigger>
            <SelectContent>
              {selections.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Line */}
        <div className="col-span-6 md:col-span-2">
          <Label className="text-[10px] text-slate-400">Línea</Label>
          {requiresLine ? (
            <Select
              value={row.line || ''}
              onValueChange={(v) => onChange({ line: v })}
            >
              <SelectTrigger
                className="h-8 text-xs"
                data-testid={`${testIdPrefix}-line`}
              >
                <SelectValue placeholder="—" />
              </SelectTrigger>
              <SelectContent>
                {allowedLines.map((l) => (
                  <SelectItem key={l} value={String(l)}>{l}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              disabled value="—"
              className="h-8 text-xs bg-slate-900/40 text-slate-500"
            />
          )}
        </div>

        {/* Bookmaker (free text) */}
        <div className="col-span-6 md:col-span-2">
          <Label className="text-[10px] text-slate-400">Bookmaker</Label>
          <Input
            value={row.bookmaker}
            onChange={(e) => onChange({ bookmaker: e.target.value })}
            placeholder="Pinnacle / bet365 / …"
            className="h-8 text-xs"
            data-testid={`${testIdPrefix}-bookmaker`}
          />
        </div>

        {/* Odd */}
        <div className="col-span-4 md:col-span-2">
          <Label className="text-[10px] text-slate-400">Cuota</Label>
          <Input
            type="number" step="0.01" min="1.01"
            value={row.odd}
            onChange={(e) => onChange({ odd: e.target.value })}
            placeholder="2.10"
            className="h-8 text-xs"
            data-testid={`${testIdPrefix}-odd`}
          />
        </div>

        {/* Delete */}
        <div className="col-span-2 md:col-span-1 flex justify-end">
          {canDelete && (
            <Button
              size="icon"
              variant="ghost"
              onClick={onDelete}
              data-testid={`${testIdPrefix}-delete`}
              className="h-8 w-8 text-slate-400 hover:text-rose-300"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>

      {/* Result row */}
      <div
        className="text-[11px] font-mono flex flex-wrap gap-x-4 gap-y-1
                    pt-1 border-t border-slate-700/30"
        data-testid={`${testIdPrefix}-result`}
      >
        <span className="text-slate-400">
          implied:{' '}
          <span className="text-slate-100">
            {calc.implied != null ? calc.implied.toFixed(4) : '—'}
          </span>
        </span>
        <span className="text-slate-400">
          implied %:{' '}
          <span className="text-slate-100">
            {calc.implied != null ? `${(calc.implied * 100).toFixed(2)}%` : '—'}
          </span>
        </span>
        <span className="text-slate-400">
          edge:{' '}
          {calc.edgePp != null ? (
            <span className={
              calc.edgePp >= 4 ? 'text-emerald-300 font-semibold' :
              calc.edgePp > 0  ? 'text-emerald-400'                :
              calc.edgePp >= -2 ? 'text-slate-200'                   :
                                  'text-rose-300'
            }>{calc.edgePp >= 0 ? '+' : ''}{calc.edgePp.toFixed(2)} pp</span>
          ) : '—'}
        </span>
        {calc.reason && (
          <span className="text-amber-300">{calc.reason}</span>
        )}
      </div>
    </div>
  );
};


// ─── Helpers ────────────────────────────────────────────────────────────
function _newRow(initialMarketType) {
  return {
    id:         (typeof crypto !== 'undefined' && crypto.randomUUID)
                  ? crypto.randomUUID()
                  : `r-${Math.random().toString(36).slice(2)}`,
    market:     initialMarketType || '',
    selection:  '',
    line:       '',
    bookmaker:  '',
    odd:        '',
  };
}

function _computeRow(row, modelProb) {
  const oddNum = parseFloat(row.odd);
  if (!isFinite(oddNum) || oddNum <= 1.0) {
    return { implied: null, edgePp: null, reason: row.odd ? 'Cuota inválida' : null };
  }
  const implied = 1.0 / oddNum;
  if (modelProb == null) {
    return { implied, edgePp: null, reason: 'Falta P modelo' };
  }
  const edgePp = (modelProb - implied) * 100.0;
  return { implied, edgePp, reason: null };
}

export default MarketComparatorPanel;
