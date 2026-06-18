/* Sprint-D7-G · Calibration Diagnostics Panel.

A self-contained React component that fetches a pre-computed
calibration diagnostics report from the backend and renders:

  1. Reliability curve (SVG, model vs market_devig vs ideal y=x).
  2. Calibration intercept / slope (with the "ideal" reference).
  3. Brier and log-loss comparison (model vs market vs de-vigged).
  4. AUC and sharpness.
  5. Realized edge per bucket.
  6. CLV opening vs closing.
  7. Verdict tags (using the rubric provided by the user).

Pure presentation: no business logic, no mutation of remote state.
*/
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertCircle, TrendingDown, TrendingUp, Info } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";

/* ── Verdict label dictionary ────────────────────────────────────────── */
const VERDICT_META = {
  WELL_CALIBRATED_AND_BEATS_MARKET: {
    icon: TrendingUp,
    variant: "default",
    label: "Modelo bate al mercado",
    detail: "Brier modelo < Brier mercado AND log-loss modelo < log-loss mercado. Hay señal real; revisar conversión a picks y precios.",
  },
  WELL_CALIBRATED_BUT_NO_EDGE_OR_WORSE: {
    icon: TrendingDown,
    variant: "secondary",
    label: "Sin ventaja práctica",
    detail: "Modelo bien calibrado pero igual o peor que mercado. CLV ≤ 0 y ROI ≈ −vig. No hay edge.",
  },
  MIS_CALIBRATED_BUT_DISCRIMINATIVE: {
    icon: Info,
    variant: "outline",
    label: "Rescatable vía calibración",
    detail: "AUC razonable + orden de riesgo correcto, pero slope/intercept mal. Se puede mejorar con calibración paramétrica.",
  },
  LOW_DISCRIMINATION_AUC_NEAR_0_50: {
    icon: AlertCircle,
    variant: "destructive",
    label: "Necesita nueva especificación",
    detail: "AUC ≈ 0.50. No hay ordenamiento informativo. No es rescatable con simples ajustes.",
  },
  INCONCLUSIVE_OR_MIXED: {
    icon: Info,
    variant: "outline",
    label: "Resultado mixto",
    detail: "Revisar métricas individualmente.",
  },
};

/* ── Reliability curve SVG ──────────────────────────────────────────── */
function ReliabilityChart({ buckets }) {
  const W = 540, H = 360, PAD = 48;
  const innerW = W - 2 * PAD, innerH = H - 2 * PAD;
  const xs = (p) => PAD + p * innerW;
  const ys = (p) => (H - PAD) - p * innerH;

  // Ideal line y=x: (0,0) → (1,1).
  const idealPath = `M ${xs(0)} ${ys(0)} L ${xs(1)} ${ys(1)}`;

  // Model curve: bucket centre → realized hit rate.
  // Bucket centre = (lo+hi)/2 on x; predicted_mean on y is also useful
  // for "calibration calibration" view, but the standard reliability
  // diagram uses (mean_p_pred, hit_rate).
  const modelPts = buckets
    .filter((b) => b.n > 0 && b.mean_p_pred !== null && b.hit_rate !== null)
    .map((b) => ({ x: b.mean_p_pred, y: b.hit_rate, n: b.n, b }));
  const marketPts = buckets
    .filter((b) => b.n > 0 && b.mean_p_market_devig !== null && b.hit_rate !== null)
    .map((b) => ({ x: b.mean_p_market_devig, y: b.hit_rate, n: b.n, b }));

  const path = (pts) => pts.map((p, i) => `${i === 0 ? "M" : "L"} ${xs(p.x)} ${ys(p.y)}`).join(" ");

  // Axis ticks.
  const ticks = [0, 0.2, 0.4, 0.6, 0.8, 1.0];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label="Reliability curve" data-testid="reliability-chart-svg">
      {/* Grid */}
      {ticks.map((t) => (
        <g key={`grid-${t}`}>
          <line x1={xs(t)} y1={ys(0)} x2={xs(t)} y2={ys(1)} stroke="hsl(var(--border))" strokeWidth="0.5" />
          <line x1={xs(0)} y1={ys(t)} x2={xs(1)} y2={ys(t)} stroke="hsl(var(--border))" strokeWidth="0.5" />
        </g>
      ))}
      {/* Axis ticks labels */}
      {ticks.map((t) => (
        <g key={`lbl-${t}`}>
          <text x={xs(t)} y={H - PAD + 18} textAnchor="middle" fontSize="11" fill="hsl(var(--muted-foreground))">
            {t.toFixed(1)}
          </text>
          <text x={PAD - 8} y={ys(t) + 4} textAnchor="end" fontSize="11" fill="hsl(var(--muted-foreground))">
            {t.toFixed(1)}
          </text>
        </g>
      ))}
      {/* Axis labels */}
      <text x={W / 2} y={H - 8} textAnchor="middle" fontSize="12" fill="hsl(var(--foreground))">
        Predicted probability (or market de-vig)
      </text>
      <text x={14} y={H / 2} transform={`rotate(-90 14 ${H / 2})`} textAnchor="middle" fontSize="12" fill="hsl(var(--foreground))">
        Realized hit rate
      </text>

      {/* Ideal line */}
      <path d={idealPath} stroke="hsl(var(--muted-foreground))" strokeWidth="1" strokeDasharray="4 4" fill="none" />

      {/* Market line (de-vig) */}
      <path d={path(marketPts)} stroke="hsl(220 60% 55%)" strokeWidth="2" fill="none" opacity="0.7" />
      {marketPts.map((p, i) => (
        <circle key={`mk-${i}`} cx={xs(p.x)} cy={ys(p.y)} r={Math.min(8, 2 + Math.sqrt(p.n) / 2)} fill="hsl(220 60% 55%)" opacity="0.7" />
      ))}

      {/* Model line */}
      <path d={path(modelPts)} stroke="hsl(12 80% 55%)" strokeWidth="2.5" fill="none" />
      {modelPts.map((p, i) => (
        <circle key={`md-${i}`} cx={xs(p.x)} cy={ys(p.y)} r={Math.min(10, 2 + Math.sqrt(p.n) / 2)} fill="hsl(12 80% 55%)" stroke="hsl(var(--background))" strokeWidth="1.5" data-testid={`reliability-point-${i}`} />
      ))}

      {/* Legend */}
      <g transform={`translate(${W - 175}, ${PAD + 8})`}>
        <rect x="0" y="0" width="170" height="58" fill="hsl(var(--card))" stroke="hsl(var(--border))" rx="4" />
        <line x1="8" y1="14" x2="28" y2="14" stroke="hsl(12 80% 55%)" strokeWidth="2.5" />
        <text x="34" y="18" fontSize="11" fill="hsl(var(--foreground))">Modelo (predicted)</text>
        <line x1="8" y1="30" x2="28" y2="30" stroke="hsl(220 60% 55%)" strokeWidth="2" opacity="0.7" />
        <text x="34" y="34" fontSize="11" fill="hsl(var(--foreground))">Mercado (de-vig)</text>
        <line x1="8" y1="46" x2="28" y2="46" stroke="hsl(var(--muted-foreground))" strokeWidth="1" strokeDasharray="4 4" />
        <text x="34" y="50" fontSize="11" fill="hsl(var(--foreground))">Ideal y=x</text>
      </g>
    </svg>
  );
}

/* ── Metric row helper ─────────────────────────────────────────────── */
function MetricRow({ label, value, valueRight, hint, severity }) {
  const colorClass = severity === "good"
    ? "text-emerald-600 dark:text-emerald-400"
    : severity === "bad"
    ? "text-rose-600 dark:text-rose-400"
    : "text-foreground";
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 border-b border-border/40 last:border-0">
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">{label}</span>
        {hint && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="w-3.5 h-3.5 text-muted-foreground/60" />
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                <p className="text-xs">{hint}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>
      <div className="flex items-baseline gap-3">
        <span className={`font-mono text-sm tabular-nums ${colorClass}`} data-testid={`metric-${label.replace(/\s+/g, '-').toLowerCase()}`}>
          {value}
        </span>
        {valueRight && <span className="font-mono text-xs text-muted-foreground tabular-nums">{valueRight}</span>}
      </div>
    </div>
  );
}

function fmtNum(x, opts = {}) {
  const { digits = 4, sign = false, pct = false } = opts;
  if (x === null || x === undefined) return "n/a";
  if (typeof x !== "number") return String(x);
  const v = pct ? x * 100 : x;
  const s = v.toFixed(digits);
  return sign && v > 0 ? `+${s}${pct ? "%" : ""}` : `${s}${pct ? "%" : ""}`;
}

/* ── Main panel ────────────────────────────────────────────────────── */
export function CalibrationDiagnosticsPanel() {
  const [index, setIndex] = useState(null);
  const [market, setMarket] = useState("OVER_2_5");
  const [scope, setScope]   = useState("top5_2425");
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    axios.get(`${BACKEND_URL}/api/football/diagnostics/calibration/index`)
      .then((res) => { if (!cancelled) setIndex(res.data); })
      .catch((err) => { if (!cancelled) setError(err.message || "fetch_failed_index"); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!market || !scope) return;
    let cancelled = false;
    setLoading(true); setError(null); setReport(null);
    axios.get(`${BACKEND_URL}/api/football/diagnostics/calibration`,
              { params: { market, scope } })
      .then((res) => { if (!cancelled) setReport(res.data); })
      .catch((err) => {
        if (!cancelled) {
          const detail = err.response?.data?.detail;
          setError(typeof detail === "string" ? detail
                    : detail?.reason || err.message || "fetch_failed");
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [market, scope]);

  const verdictTags = report?.verdict?.tags || [];

  const m = report?.model_vs_market;
  const d = report?.discrimination;
  const c = report?.calibration;
  const clv = report?.clv?.all_predictions;
  const clvPicks = report?.clv?.picks_only;
  const meta = report?.meta;

  // Severity helpers.
  const sevBrier = (m?.delta_brier_vs_devig != null && m.delta_brier_vs_devig < 0) ? "good" : "bad";
  const sevLogLoss = (m?.delta_logloss_vs_devig != null && m.delta_logloss_vs_devig < 0) ? "good" : "bad";
  const sevSlope = (c?.slope != null && c.slope > 0.85 && c.slope < 1.15) ? "good"
                    : (c?.slope != null && (c.slope < 0.5 || c.slope > 1.5)) ? "bad" : null;
  const sevAuc = (d?.auc_model != null && d.auc_model > 0.55) ? "good"
                  : (d?.auc_model != null && d.auc_model <= 0.52) ? "bad" : null;
  const sevCLV = (clv?.clv_pp_mean != null && clv.clv_pp_mean < 0) ? "good"
                  : (clv?.clv_pp_mean != null && clv.clv_pp_mean > 0) ? "bad" : null;

  return (
    <div className="space-y-4" data-testid="calibration-diagnostics-panel">
      {/* Filters */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Calibration Diagnostics</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5 min-w-[180px]">
            <label className="text-xs text-muted-foreground">Mercado</label>
            <Select value={market} onValueChange={setMarket}>
              <SelectTrigger data-testid="market-selector">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(index?.markets || ["OVER_2_5", "UNDER_2_5", "DRAW"]).map((mk) => (
                  <SelectItem key={mk} value={mk}>{mk}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5 min-w-[220px]">
            <label className="text-xs text-muted-foreground">Scope</label>
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger data-testid="scope-selector">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(index?.scopes || ["premier_2425", "top5_2425", "premier_multiseason"]).map((sc) => (
                  <SelectItem key={sc} value={sc}>{sc}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {meta && (
            <div className="ml-auto text-right">
              <div className="text-xs text-muted-foreground">N records</div>
              <div className="font-mono text-base tabular-nums" data-testid="n-records">{meta.n_records}</div>
              <div className="text-xs text-muted-foreground">base rate: {fmtNum(meta.base_rate_hit, { digits: 3, pct: true })}</div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Error / loading */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {loading && (
        <Card>
          <CardContent className="pt-6 space-y-2">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-64 w-full" />
          </CardContent>
        </Card>
      )}

      {/* Verdict bar */}
      {report && verdictTags.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="verdict-tags">
          {verdictTags.map((t) => {
            const meta = VERDICT_META[t] || { icon: Info, variant: "outline", label: t, detail: "" };
            const Icon = meta.icon;
            return (
              <TooltipProvider key={t}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant={meta.variant} className="gap-1.5 cursor-help" data-testid={`verdict-tag-${t.toLowerCase()}`}>
                      <Icon className="w-3 h-3" />
                      {meta.label}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-sm">
                    <p className="text-xs">{meta.detail}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            );
          })}
        </div>
      )}

      {/* Main content */}
      {report && (
        <Tabs defaultValue="reliability" className="w-full">
          <TabsList>
            <TabsTrigger value="reliability" data-testid="tab-reliability">Reliability curve</TabsTrigger>
            <TabsTrigger value="metrics" data-testid="tab-metrics">Metrics</TabsTrigger>
            <TabsTrigger value="buckets" data-testid="tab-buckets">Per-bucket</TabsTrigger>
          </TabsList>

          <TabsContent value="reliability">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Reliability curve — {report.market} · {report.scope}</CardTitle>
              </CardHeader>
              <CardContent>
                <ReliabilityChart buckets={report.reliability_curve} />
                <p className="text-xs text-muted-foreground mt-3">
                  Cada punto representa un bucket de 10 puntos porcentuales de probabilidad.
                  El tamaño del círculo es proporcional a √n. La línea naranja es el modelo;
                  la línea azul es el mercado de-vig; la diagonal punteada es la calibración ideal.
                </p>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="metrics" className="space-y-3">
            <div className="grid md:grid-cols-2 gap-3">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Calibration (OLS y ~ a + b·p)</CardTitle>
                </CardHeader>
                <CardContent>
                  <MetricRow label="intercept" value={fmtNum(c?.intercept, { digits: 4 })} hint="Ideal = 0. Si > 0 el modelo subestima sistemáticamente." />
                  <MetricRow label="slope" value={fmtNum(c?.slope, { digits: 4 })} severity={sevSlope} hint="Ideal = 1. Slope < 1 = modelo demasiado confiado; > 1 = demasiado conservador." />
                  <MetricRow label="R²" value={fmtNum(c?.r_squared, { digits: 4 })} hint="Cuánta varianza del outcome explican las predicciones." />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Model vs Market</CardTitle>
                </CardHeader>
                <CardContent>
                  <MetricRow label="Brier model" value={fmtNum(m?.brier_model)} hint="Menor es mejor. Es el MSE entre probabilidad y outcome." />
                  <MetricRow label="Brier market (de-vig)" value={fmtNum(m?.brier_market_devig)} />
                  <MetricRow label="Δ Brier (model − market_devig)" value={fmtNum(m?.delta_brier_vs_devig, { sign: true })} severity={sevBrier} hint="Negativo = modelo mejor que mercado de-vig." />
                  <MetricRow label="Log-loss model" value={fmtNum(m?.logloss_model)} />
                  <MetricRow label="Log-loss market (de-vig)" value={fmtNum(m?.logloss_market_devig)} />
                  <MetricRow label="Δ Log-loss" value={fmtNum(m?.delta_logloss_vs_devig, { sign: true })} severity={sevLogLoss} hint="Negativo = modelo mejor calibrado en cola." />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Discrimination & Sharpness</CardTitle>
                </CardHeader>
                <CardContent>
                  <MetricRow label="AUC modelo" value={fmtNum(d?.auc_model)} severity={sevAuc} hint="0.5 = aleatorio; >0.6 = útil; >0.7 = bueno." />
                  <MetricRow label="AUC mercado (de-vig)" value={fmtNum(d?.auc_market_devig)} />
                  <MetricRow label="Sharpness modelo (stdev)" value={fmtNum(d?.sharpness_stdev_model)} hint="Stdev de las predicciones. Alta = más confiado en los extremos." />
                  <MetricRow label="Sharpness mercado (stdev)" value={fmtNum(d?.sharpness_stdev_market_devig)} />
                  <MetricRow label="|p − 0.5| modelo" value={fmtNum(d?.sharpness_dist_model)} hint="Distancia media al 50%." />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">CLV (Closing Line Value)</CardTitle>
                </CardHeader>
                <CardContent>
                  <MetricRow label="N con cierre" value={fmtNum(clv?.n_with_close, { digits: 0 })} />
                  <MetricRow label="CLV mean (pp)" value={fmtNum(clv?.clv_pp_mean, { sign: true })} severity={sevCLV} hint="Negativo = closing line cerró a favor del modelo (good)." />
                  <MetricRow label="CLV stdev (pp)" value={fmtNum(clv?.clv_pp_stdev)} />
                  <MetricRow label="CLV log-odds mean" value={fmtNum(clv?.clv_log_odds_mean, { sign: true, digits: 6 })} />
                  {clvPicks && (
                    <>
                      <div className="text-xs text-muted-foreground mt-2 pt-2 border-t border-border/40">Solo picks (fired):</div>
                      <MetricRow label="N picks con cierre" value={fmtNum(clvPicks?.n_picks_with_close, { digits: 0 })} />
                      <MetricRow label="CLV picks mean (pp)" value={fmtNum(clvPicks?.clv_pp_mean, { sign: true })} severity={clvPicks?.clv_pp_mean < 0 ? "good" : "bad"} />
                    </>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <TabsContent value="buckets">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Reliability buckets — realized edge per bucket</CardTitle>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="buckets-table">
                  <thead>
                    <tr className="border-b border-border/60 text-xs text-muted-foreground">
                      <th className="text-left py-2 pr-3">Bucket</th>
                      <th className="text-right py-2 pr-3">n</th>
                      <th className="text-right py-2 pr-3">⟨p_pred⟩</th>
                      <th className="text-right py-2 pr-3">hit_rate</th>
                      <th className="text-right py-2 pr-3">CI95</th>
                      <th className="text-right py-2 pr-3">⟨p_mkt_devig⟩</th>
                      <th className="text-right py-2 pr-3">realized_edge_pp</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.reliability_curve.map((b, i) => (
                      <tr key={i} className="border-b border-border/30 last:border-0">
                        <td className="py-1.5 pr-3 font-mono text-xs">{b.bucket}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{b.n}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{fmtNum(b.mean_p_pred, { digits: 3 })}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{fmtNum(b.hit_rate, { digits: 3 })}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-xs text-muted-foreground">
                          {b.hit_rate_ci95?.[0] !== null && b.hit_rate_ci95?.[1] !== null
                            ? `[${b.hit_rate_ci95[0]?.toFixed(2)}, ${b.hit_rate_ci95[1]?.toFixed(2)}]`
                            : "—"}
                        </td>
                        <td className="py-1.5 pr-3 text-right tabular-nums">{fmtNum(b.mean_p_market_devig, { digits: 3 })}</td>
                        <td className={`py-1.5 pr-3 text-right tabular-nums font-medium ${b.realized_edge_pp == null ? "" : b.realized_edge_pp > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
                          {b.realized_edge_pp != null ? `${b.realized_edge_pp > 0 ? "+" : ""}${b.realized_edge_pp.toFixed(2)}` : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

export default CalibrationDiagnosticsPanel;
