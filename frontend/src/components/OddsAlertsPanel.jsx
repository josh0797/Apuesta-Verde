import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Bell, RefreshCw, CheckCircle2, AlertTriangle, Loader2, BellOff,
} from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Sprint E.2 / UI · Odds Alerts Panel.
 *
 * Reads from ``GET /api/odds/alerts`` and lets the operator acknowledge
 * single alerts via ``POST /api/odds/alerts/ack``. Strict
 * ``observe_only``: no bet flows.
 *
 * Visual contract:
 *   - Filter dropdowns (signal_type, severity, acked).
 *   - Refresh button.
 *   - Empty state with a friendly message.
 *   - One card per alert with severity badge + signal-type badge +
 *     match_id + market + outcome + a per-row "Ack" button.
 */
const SIGNAL_TYPE_LABEL = {
  OUTLIER:       'Outlier vs consenso',
  EDGE_VS_MODEL: 'Edge vs modelo',
  FAST_MOVE:     'Movimiento rápido',
  DISPERSION:    'Dispersión entre bookies',
};

const SEVERITY_TONE = {
  HIGH:   'bg-rose-700/40 text-rose-200 border-rose-600/60',
  MEDIUM: 'bg-amber-700/40 text-amber-200 border-amber-600/60',
  LOW:    'bg-slate-700/40 text-slate-200 border-slate-600/60',
};

const SIGNAL_TONE = {
  OUTLIER:       'bg-fuchsia-700/40 text-fuchsia-200 border-fuchsia-600/60',
  EDGE_VS_MODEL: 'bg-emerald-700/40 text-emerald-200 border-emerald-600/60',
  FAST_MOVE:     'bg-sky-700/40 text-sky-200 border-sky-600/60',
  DISPERSION:    'bg-indigo-700/40 text-indigo-200 border-indigo-600/60',
};

export const OddsAlertsPanel = ({
  matchId = null,
  testIdPrefix = 'odds-alerts',
}) => {
  const [alerts, setAlerts]         = useState([]);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState(null);
  const [signalType, setSignalType] = useState('all');
  const [severity, setSeverity]     = useState('all');
  const [ackedFilter, setAckedFilter] = useState('open');   // open|all|acked
  const [busyAlertId, setBusyAlertId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { limit: 100, since_hours: 48 };
      if (matchId) params.match_id = matchId;
      if (signalType !== 'all') params.signal_type = signalType;
      if (severity !== 'all') params.severity = severity;
      if (ackedFilter === 'open') params.acked = false;
      if (ackedFilter === 'acked') params.acked = true;
      const res = await axios.get(`${BACKEND_URL}/api/odds/alerts`, { params });
      setAlerts(Array.isArray(res.data?.alerts) ? res.data.alerts : []);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Error al cargar alertas.');
    } finally {
      setLoading(false);
    }
  }, [matchId, signalType, severity, ackedFilter]);

  useEffect(() => { load(); }, [load]);

  const handleAck = useCallback(async (alertId) => {
    if (!alertId) return;
    setBusyAlertId(alertId);
    try {
      await axios.post(`${BACKEND_URL}/api/odds/alerts/ack`,
                        { alert_id: alertId });
      await load();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Error al confirmar.');
    } finally {
      setBusyAlertId(null);
    }
  }, [load]);

  const stats = useMemo(() => {
    const counts = { HIGH: 0, MEDIUM: 0, LOW: 0 };
    for (const a of alerts) {
      const s = (a.severity || 'LOW').toUpperCase();
      if (counts[s] != null) counts[s] += 1;
    }
    return counts;
  }, [alerts]);

  return (
    <Card
      className="border-slate-700/40 bg-slate-900/40"
      data-testid={`${testIdPrefix}-panel`}
    >
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between flex-wrap gap-2 text-sm">
          <span className="flex items-center gap-2 text-slate-100">
            <Bell className="h-4 w-4 text-amber-300" />
            Alertas de cuotas
            <span className="ml-1 text-[11px] text-slate-400">
              ({alerts.length} en vista)
            </span>
          </span>
          <span className="flex items-center gap-1.5">
            <Badge className={SEVERITY_TONE.HIGH} data-testid={`${testIdPrefix}-count-high`}>
              {stats.HIGH} HIGH
            </Badge>
            <Badge className={SEVERITY_TONE.MEDIUM} data-testid={`${testIdPrefix}-count-medium`}>
              {stats.MEDIUM} MED
            </Badge>
            <Badge className={SEVERITY_TONE.LOW} data-testid={`${testIdPrefix}-count-low`}>
              {stats.LOW} LOW
            </Badge>
          </span>
        </CardTitle>
        <p className="text-[11px] text-slate-400 mt-1">
          observe_only · solo lectura. Las alertas se generan a partir de
          <span className="font-mono"> odds_snapshots</span> (live monitor)
          + detector heurístico (outlier / edge / fast move / dispersión).
        </p>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          <Select value={signalType} onValueChange={setSignalType}>
            <SelectTrigger
              className="h-8 text-xs w-[170px]"
              data-testid={`${testIdPrefix}-filter-signal`}
            >
              <SelectValue placeholder="Tipo de señal" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas las señales</SelectItem>
              <SelectItem value="OUTLIER">Outlier vs consenso</SelectItem>
              <SelectItem value="EDGE_VS_MODEL">Edge vs modelo</SelectItem>
              <SelectItem value="FAST_MOVE">Movimiento rápido</SelectItem>
              <SelectItem value="DISPERSION">Dispersión bookies</SelectItem>
            </SelectContent>
          </Select>

          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger
              className="h-8 text-xs w-[140px]"
              data-testid={`${testIdPrefix}-filter-severity`}
            >
              <SelectValue placeholder="Severidad" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              <SelectItem value="HIGH">HIGH</SelectItem>
              <SelectItem value="MEDIUM">MEDIUM</SelectItem>
              <SelectItem value="LOW">LOW</SelectItem>
            </SelectContent>
          </Select>

          <Select value={ackedFilter} onValueChange={setAckedFilter}>
            <SelectTrigger
              className="h-8 text-xs w-[140px]"
              data-testid={`${testIdPrefix}-filter-acked`}
            >
              <SelectValue placeholder="Estado" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="open">Abiertas</SelectItem>
              <SelectItem value="acked">Confirmadas</SelectItem>
              <SelectItem value="all">Todas</SelectItem>
            </SelectContent>
          </Select>

          <Button
            size="sm"
            variant="outline"
            onClick={load}
            disabled={loading}
            data-testid={`${testIdPrefix}-refresh`}
            className="ml-auto"
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3 mr-1" />
            )}
            Refrescar
          </Button>
        </div>

        {error && (
          <div
            className="text-[11px] text-rose-300 flex items-center gap-2"
            data-testid={`${testIdPrefix}-error`}
          >
            <AlertTriangle className="h-3 w-3" />
            {String(error)}
          </div>
        )}

        {/* Empty state */}
        {!loading && alerts.length === 0 && !error && (
          <div
            className="text-center py-6 text-slate-400 text-[12px]"
            data-testid={`${testIdPrefix}-empty`}
          >
            <BellOff className="h-5 w-5 mx-auto mb-1 opacity-60" />
            No hay alertas que coincidan con los filtros actuales.
          </div>
        )}

        {/* Alerts list */}
        <div className="space-y-2">
          {alerts.map((a) => (
            <AlertRow
              key={a.alert_id}
              alert={a}
              onAck={() => handleAck(a.alert_id)}
              busy={busyAlertId === a.alert_id}
              testIdPrefix={`${testIdPrefix}-row-${a.alert_id}`}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
};

const AlertRow = ({ alert, onAck, busy, testIdPrefix }) => {
  const sig = alert.signal_type || 'UNKNOWN';
  const sev = (alert.severity || 'LOW').toUpperCase();
  const payload = alert.payload || {};
  const isAcked = !!alert.acked;
  const fmt = (n, d = 4) => (n == null ? '—' : Number(n).toFixed(d));

  return (
    <div
      className={`rounded-md border p-2 text-[11px]
                   ${isAcked
                       ? 'bg-slate-900/20 border-slate-800/40 opacity-70'
                       : 'bg-slate-900/40 border-slate-700/50'}`}
      data-testid={testIdPrefix}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <Badge className={SEVERITY_TONE[sev] || SEVERITY_TONE.LOW}>{sev}</Badge>
        <Badge className={SIGNAL_TONE[sig] || 'bg-slate-700/40 text-slate-200 border-slate-600/60'}>
          {SIGNAL_TYPE_LABEL[sig] || sig}
        </Badge>
        {alert.match_id && (
          <span className="font-mono text-slate-400">match: {alert.match_id}</span>
        )}
        {alert.market && (
          <span className="text-slate-300">{alert.market}</span>
        )}
        {alert.outcome_name && (
          <span className="text-slate-200">
            {alert.outcome_name}
            {alert.outcome_point != null ? ` (${alert.outcome_point})` : ''}
          </span>
        )}
        {alert.occurrences > 1 && (
          <span className="text-amber-300 text-[10px]">
            ×{alert.occurrences}
          </span>
        )}
        {!isAcked ? (
          <Button
            size="sm"
            onClick={onAck}
            disabled={busy}
            data-testid={`${testIdPrefix}-ack-btn`}
            className="ml-auto h-7 bg-emerald-700 hover:bg-emerald-600 text-white"
          >
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> :
              <><CheckCircle2 className="h-3 w-3 mr-1" /> Ack</>}
          </Button>
        ) : (
          <span className="ml-auto text-emerald-300 text-[10px] flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" />
            confirmada
          </span>
        )}
      </div>

      {/* Per-signal-type detail line */}
      <div className="mt-1 text-slate-400 font-mono text-[10px] flex flex-wrap gap-x-3 gap-y-0.5">
        {sig === 'OUTLIER' && (
          <>
            <span>book: {payload.bookmaker_key || alert.bookmaker_key || '—'}</span>
            <span>implied: {fmt(payload.bookmaker_implied)}</span>
            <span>vs consenso: {fmt(payload.consensus_implied)}</span>
            <span>z: {payload.z_score ?? '—'}</span>
            <span>n_books: {payload.n_books ?? '—'}</span>
          </>
        )}
        {sig === 'EDGE_VS_MODEL' && (
          <>
            <span>modelo: {fmt(payload.model_prob)}</span>
            <span>consenso: {fmt(payload.consensus_implied)}</span>
            <span>edge: {payload.edge_pp ?? '—'}pp</span>
            <span>mejor: {payload.best_bookmaker || '—'} @ {payload.best_price ?? '—'}</span>
          </>
        )}
        {sig === 'FAST_MOVE' && (
          <>
            <span>book: {payload.bookmaker_key || '—'}</span>
            <span>de: {fmt(payload.from_implied)}</span>
            <span>a: {fmt(payload.to_implied)}</span>
            <span>Δ: {payload.delta_pp ?? '—'}pp</span>
            <span>en: {payload.elapsed_sec ?? '—'}s</span>
          </>
        )}
        {sig === 'DISPERSION' && (
          <>
            <span>min: {fmt(payload.min_implied)}</span>
            <span>max: {fmt(payload.max_implied)}</span>
            <span>med: {fmt(payload.median_implied)}</span>
            <span>spread: {payload.dispersion_pp ?? '—'}pp</span>
            <span>n_books: {payload.n_books ?? '—'}</span>
          </>
        )}
        {alert.updated_at && (
          <span className="ml-auto text-slate-500">
            {new Date(alert.updated_at).toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
};

export default OddsAlertsPanel;
