import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, Settings2, RefreshCw, CheckCircle2 } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Phase F83 — Manual Market Identity Panel.
 *
 * Shown when the backend returns ``REQUIRES_MARKET_IDENTIFICATION``
 * for a pick. Lets the operator:
 *   1. View the detected odd.
 *   2. Enter the actual odd manually.
 *   3. Pick the market_type from a whitelist.
 *   4. Pick the selection / line (when required).
 *   5. Hit "Recalcular con mercado manual" → posts to
 *      ``/api/football/manual-market-reprice`` and renders the
 *      recalculated edge / verdict.
 *
 * Props:
 *   - matchId, detectedOdd: from the requires_market_identity bucket.
 *   - candidateMarkets: optional list of suggested markets (from the
 *     resolver) to pre-fill the dropdown when available.
 *   - testIdPrefix
 */
export const ManualMarketIdentityPanel = ({
  matchId, detectedOdd, candidateMarkets = [], testIdPrefix = 'manual-market',
}) => {
  const [options, setOptions]               = useState(null);
  const [marketType, setMarketType]         = useState('');
  const [selection, setSelection]           = useState('');
  const [line, setLine]                     = useState('');
  const [manualOdd, setManualOdd]           = useState(detectedOdd ? String(detectedOdd) : '');
  const [submitting, setSubmitting]         = useState(false);
  const [result, setResult]                 = useState(null);
  const [error, setError]                   = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await axios.get(`${BACKEND_URL}/api/football/manual-market-options`);
        if (alive) setOptions(res.data);
      } catch (e) {
        if (alive) setError('No se pudo cargar la lista de mercados.');
      }
    })();
    return () => { alive = false; };
  }, []);

  const opts = useMemo(() => {
    if (!options || !marketType) return null;
    return options.options_by_market?.[marketType] || null;
  }, [options, marketType]);

  const handleSubmit = async () => {
    setError(null);
    setResult(null);
    if (!marketType || !selection) {
      setError('Seleccioná tipo de mercado y selección.');
      return;
    }
    if (opts?.requires_line && !line) {
      setError('Este mercado requiere una línea.');
      return;
    }
    const oddNum = parseFloat(manualOdd);
    if (!oddNum || oddNum < 1.01) {
      setError('La cuota manual debe ser >= 1.01');
      return;
    }
    setSubmitting(true);
    try {
      const res = await axios.post(
        `${BACKEND_URL}/api/football/manual-market-reprice`,
        {
          match_id: matchId,
          detected_odd: detectedOdd,
          manual_odd: oddNum,
          market_type: marketType,
          selection,
          line: opts?.requires_line ? parseFloat(line) : null,
          source: 'USER_MANUAL_INPUT',
        },
      );
      setResult(res.data);
    } catch (e) {
      setError(e.response?.data?.detail || 'Error al recalcular.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!options) {
    return (
      <Card className="border-amber-700/40 bg-amber-900/10" data-testid={`${testIdPrefix}-loading`}>
        <CardContent className="py-3 text-xs text-slate-300">
          Cargando opciones de mercado…
        </CardContent>
      </Card>
    );
  }

  return (
    <Card
      className="border-amber-700/40 bg-amber-900/10"
      data-testid={`${testIdPrefix}-panel`}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2 text-amber-200">
          <Settings2 className="h-4 w-4" />
          Identificación manual de mercado
        </CardTitle>
        {detectedOdd && (
          <p className="text-[11px] text-slate-300 mt-1">
            Cuota detectada: <span className="font-mono text-amber-100">{detectedOdd}</span> —
            no se puede calcular edge hasta confirmar a qué mercado pertenece.
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {candidateMarkets.length > 0 && (
          <div className="flex flex-wrap gap-1 text-[10px] text-slate-300">
            Sugeridos:
            {candidateMarkets.map((c, i) => (
              <Badge key={i} className="bg-slate-700/60 text-slate-200">
                {c.identity_key || `${c.market}|${c.selection}`}
              </Badge>
            ))}
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label className="text-[11px] text-slate-300">Mercado</Label>
            <Select value={marketType} onValueChange={(v) => { setMarketType(v); setSelection(''); setLine(''); }}>
              <SelectTrigger data-testid={`${testIdPrefix}-market-type`} className="bg-slate-900/60">
                <SelectValue placeholder="Elegir..." />
              </SelectTrigger>
              <SelectContent>
                {options.market_types.map((mt) => (
                  <SelectItem key={mt} value={mt}>{mt}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[11px] text-slate-300">Selección</Label>
            <Select value={selection} onValueChange={setSelection} disabled={!opts}>
              <SelectTrigger data-testid={`${testIdPrefix}-selection`} className="bg-slate-900/60">
                <SelectValue placeholder="Elegir..." />
              </SelectTrigger>
              <SelectContent>
                {opts?.selections?.map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {opts?.requires_line && (
            <div>
              <Label className="text-[11px] text-slate-300">Línea</Label>
              <Select value={line} onValueChange={setLine}>
                <SelectTrigger data-testid={`${testIdPrefix}-line`} className="bg-slate-900/60">
                  <SelectValue placeholder="Elegir..." />
                </SelectTrigger>
                <SelectContent>
                  {opts.allowed_lines.map((l) => (
                    <SelectItem key={l} value={String(l)}>{l}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div>
            <Label className="text-[11px] text-slate-300">Cuota manual</Label>
            <Input
              type="number"
              step="0.01"
              min="1.01"
              value={manualOdd}
              onChange={(e) => setManualOdd(e.target.value)}
              data-testid={`${testIdPrefix}-manual-odd`}
              className="bg-slate-900/60"
            />
          </div>
        </div>

        <Button
          onClick={handleSubmit}
          disabled={submitting}
          className="w-full bg-amber-600 hover:bg-amber-500 text-amber-50"
          data-testid={`${testIdPrefix}-submit`}
        >
          {submitting ? (
            <><RefreshCw className="h-3 w-3 mr-1 animate-spin" /> Recalculando…</>
          ) : (
            <>Recalcular con mercado manual</>
          )}
        </Button>

        {error && (
          <div
            className="flex items-start gap-2 text-xs text-rose-200 bg-rose-900/20 rounded px-2 py-1.5"
            data-testid={`${testIdPrefix}-error`}
          >
            <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {result?.recalculated_pick && (
          <div
            className="border border-emerald-700/40 bg-emerald-900/10 rounded p-2 text-xs space-y-1"
            data-testid={`${testIdPrefix}-result`}
          >
            <div className="flex items-center gap-2 font-semibold text-emerald-200">
              <CheckCircle2 className="h-4 w-4" />
              {result.recalculated_pick.recommended_market}
            </div>
            <div className="grid grid-cols-2 gap-x-3 text-[11px] text-slate-300">
              <span>Edge manual: <span className="text-emerald-200">{result.recalculated_pick.manual_edge}%</span></span>
              <span>Implícita: <span className="text-slate-200">{result.recalculated_pick.implied_probability}%</span></span>
              <span>Modelo: <span className="text-slate-200">{result.recalculated_pick.model_probability}%</span></span>
              <span>Fragilidad: <span className="text-slate-200">{result.recalculated_pick.fragility_score}</span></span>
              <span>Confianza: <span className="text-slate-200">{result.recalculated_pick.confidence}</span></span>
              <span>Categoría: <span className="text-slate-200">{result.recalculated_pick.tolerance_category}</span></span>
            </div>
            <div className="text-[11px] text-slate-200 italic pt-1">
              {result.recalculated_pick.verdict}
            </div>
            {Array.isArray(result.warnings) && result.warnings.length > 0 && (
              <ul className="list-disc pl-5 text-[10px] text-amber-200/80 pt-1">
                {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default ManualMarketIdentityPanel;
