import { useState } from 'react';
import { ChevronDown, AlertCircle, Activity, Target, Gauge, Calculator, Flame, ShieldAlert, ShieldCheck } from 'lucide-react';

/**
 * ManualOddsReviewPanel — renders MLB games that the v2 engine identified as
 * having a structural lean but for which automatic odds were not available.
 *
 * These games come from the new `summary.structural_lean_requires_odds`
 * bucket (and optionally `summary.watchlist_manual_odds`) populated by the
 * MLB-V5 orchestrator. Critically they are NOT routed to "Descartados por
 * mercado frágil" anymore — instead the user gets a clear "Revisión
 * manual — falta cuota" card with suggested markets and a "Pegar tu cuota
 * manual" input that computes EV client-side.
 *
 * Constraints:
 *  - Baseball-only — caller (DashboardPage) must skip rendering for other sports.
 *  - Renders nothing when the bucket is empty.
 *  - Does NOT touch the existing "Descartados por mercado frágil" rendering
 *    for football/basketball.
 */

const TONE_BY_CLASSIFICATION = {
  STRUCTURAL_LEAN:          { tone: 'cyan',   label: 'Revisión manual — falta cuota' },
  WATCHLIST_MANUAL_REVIEW:  { tone: 'amber',  label: 'Watchlist — cuota manual' },
};

function asNumber(v) {
  if (v === null || v === undefined) return null;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Compute client-side expected value (edge) from a pasted decimal odds.
 *
 * EV = (probability * (odds - 1)) - (1 - probability)
 *
 * Returned as percentage (positive ⇒ profitable). When the engine produced
 * a Poisson-based probability we trust it directly; for Run Line picks the
 * `coverProbability` from v2 is also already a probability percentage.
 */
function computeEdge(decimalOdds, probabilityPct) {
  const o = parseFloat(decimalOdds);
  const p = parseFloat(probabilityPct);
  if (!Number.isFinite(o) || o <= 1 || !Number.isFinite(p) || p <= 0 || p >= 100) return null;
  const prob = p / 100;
  const ev = (prob * (o - 1)) - (1 - prob);
  return {
    edgePct:        ev * 100,
    impliedProbPct: (1 / o) * 100,
    breakEvenOdds:  prob > 0 ? 1 / prob : null,
  };
}

/**
 * ExplosiveInningRiskPanel — renders the Explosive Inning Risk Score
 * computed by `compute_explosive_inning_risk` in the MLB orchestrator.
 *
 * Replaces the older "Mercados a revisar manualmente" chip list because
 * the actual signal users need before reviewing odds is whether this
 * game has structural Under risk (HIGH = block FG Under; MEDIUM = -10
 * conf + prefer F5; LOW = Under sostenible).
 *
 * Renders nothing when `explosive_inning_risk` is absent so games that
 * weren't evaluated by the new layer fall back gracefully.
 */
const REASON_LABELS_ES = {
  POWER_BAT_MAX_OPS_GT_800:    'Power bats detectados (OPS > 0.800)',
  POWER_BAT_MAX_OPS_GT_770:    'Power bats detectados (OPS > 0.770)',
  BULLPEN_ERA7D_GT_5:          'Bullpen ERA últimos 7d > 5.00 (fatiga alta)',
  BULLPEN_PITCH_STRESS_GT_2:   'Bullpen con pitch-stress > 2.0 (≥90 pitches/48h)',
  BULLPEN_PITCH_STRESS_GT_1_5: 'Bullpen con pitch-stress > 1.5 (≥67 pitches/48h)',
  ACTIVE_SERIES_H2H_GT_12:     'Series activa: promedio H2H > 12 carreras',
  ACTIVE_SERIES_H2H_GT_10:     'Series activa: promedio H2H > 10 carreras por encuentro',
  HITTER_PARK_FACTOR:          'Parque ofensivo (run factor > 1.10)',
  LINE_GAP_LT_1_0:             'Gap línea-modelo < 1.0 carreras (margen casi nulo)',
  LINE_GAP_LT_1_5:             'Gap línea-modelo < 1.5 carreras (margen frágil)',
  SCRIPT_SURVIVAL_LT_50:       'Script Survival < 50 (guion Under colapsa probable)',
  SCRIPT_SURVIVAL_LT_60:       'Script Survival < 60 (guion Under inestable)',
};

const RISK_META = {
  LOW: {
    icon:    ShieldCheck,
    label:   'LOW',
    headline:'Under sostenible',
    sub:     'Sin señales de inning explosivo.',
    // Hardcoded Tailwind classes (JIT-safe — no dynamic interpolation).
    wrapper:    'border-emerald-500/30 bg-emerald-500/[0.05]',
    iconColor:  'text-emerald-300',
    badge:      'border-emerald-500/45 bg-emerald-500/15 text-emerald-200',
    headingClr: 'text-emerald-200',
    reasonText: 'text-emerald-100/95',
    dot:        'bg-emerald-400',
    bdValue:    'text-emerald-200',
  },
  MEDIUM: {
    icon:    Flame,
    label:   'MEDIUM',
    headline:'Under penalizado',
    sub:     '-10 a -25 pts de confianza · preferir F5 Under sobre Full Game.',
    wrapper:    'border-amber-500/30 bg-amber-500/[0.05]',
    iconColor:  'text-amber-300',
    badge:      'border-amber-500/45 bg-amber-500/15 text-amber-200',
    headingClr: 'text-amber-200',
    reasonText: 'text-amber-100/95',
    dot:        'bg-amber-400',
    bdValue:    'text-amber-200',
  },
  HIGH: {
    icon:    ShieldAlert,
    label:   'HIGH',
    headline:'Full Game Under bloqueado',
    sub:     'Evaluar Over protegido (triple gate). Si falla → descartar.',
    wrapper:    'border-rose-500/30 bg-rose-500/[0.05]',
    iconColor:  'text-rose-300',
    badge:      'border-rose-500/45 bg-rose-500/15 text-rose-200',
    headingClr: 'text-rose-200',
    reasonText: 'text-rose-100/95',
    dot:        'bg-rose-400',
    bdValue:    'text-rose-200',
  },
};

function ExplosiveInningRiskPanel({ risk, action, idx }) {
  if (!risk || !risk.risk_level) return null;
  const lvl = (risk.risk_level || '').toUpperCase();
  const meta = RISK_META[lvl] || RISK_META.LOW;
  const Icon = meta.icon;
  const reasons = Array.isArray(risk.reasons) ? risk.reasons : [];
  const score = Number.isFinite(parseFloat(risk.risk_score))
    ? parseFloat(risk.risk_score) : null;
  const breakdown = (risk && typeof risk.breakdown === 'object') ? risk.breakdown : {};

  const actionLabel = (() => {
    if (!action || !action.action) return null;
    switch (action.action) {
      case 'FLIP_TO_OVER':       return 'Flip a Over (3 gates OK)';
      case 'DISCARD_NO_FLIP':    return 'Descartado (Over no validó gates)';
      case 'PREFER_F5_UNDER':    return 'Swap a F5 Under';
      default:                    return action.action;
    }
  })();

  return (
    <div
      className={`rounded-md border ${meta.wrapper} px-3 py-2.5 space-y-2`}
      data-testid={`explosive-inning-risk-panel-${idx}`}
    >
      {/* Header — level + score + headline */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${meta.iconColor}`} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/80">
                Explosive Inning Risk
              </span>
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border ${meta.badge} font-semibold`}
                data-testid={`explosive-risk-level-${idx}`}
              >
                {meta.label}
              </span>
              {score !== null ? (
                <span className="text-[10px] tabular-nums text-muted-foreground/85">
                  score {score}/100
                </span>
              ) : null}
            </div>
            <div className={`text-[12px] mt-0.5 font-medium ${meta.headingClr}`}>
              {meta.headline}
            </div>
          </div>
        </div>
        {actionLabel ? (
          <span
            className="shrink-0 text-[10px] px-2 py-0.5 rounded-full border border-border/60 bg-background/50 text-muted-foreground tabular-nums"
            data-testid={`explosive-risk-action-${idx}`}
          >
            {actionLabel}
          </span>
        ) : null}
      </div>

      {/* Sub-headline / impact summary */}
      <p className="text-[11px] text-muted-foreground/90 leading-relaxed">
        {meta.sub}
      </p>

      {/* Reasons list — single line per signal */}
      {reasons.length > 0 ? (
        <ul className="space-y-1" data-testid={`explosive-risk-reasons-${idx}`}>
          {reasons.map((code) => {
            const label = REASON_LABELS_ES[code] || code;
            return (
              <li
                key={code}
                className={`flex items-start gap-1.5 text-[11px] ${meta.reasonText}`}
                data-testid={`explosive-risk-reason-${idx}-${code}`}
              >
                <span className={`mt-[5px] inline-block h-1 w-1 rounded-full ${meta.dot} shrink-0`} />
                <span>{label}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-[11px] text-muted-foreground/70 italic">
          Sin señales activas — Under sigue el flujo normal.
        </p>
      )}

      {/* Breakdown details (collapsed) */}
      {Object.keys(breakdown).length > 0 ? (
        <details className="text-[10.5px] text-muted-foreground/80">
          <summary className="cursor-pointer hover:text-muted-foreground">
            Ver desglose por categoría
          </summary>
          <ul className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 pl-1 font-mono tabular-nums">
            {Object.entries(breakdown).map(([k, v]) => (
              <li key={k} className="flex justify-between">
                <span className="text-muted-foreground/70">{k}</span>
                <span className={v > 0 ? meta.bdValue : 'text-muted-foreground/60'}>
                  {v > 0 ? `+${v}` : v}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function ManualReviewRow({ item, idx, testId }) {
  const [expanded, setExpanded] = useState(false);
  const [manualOdds, setManualOdds] = useState('');
  const cls = item.classification || 'STRUCTURAL_LEAN';
  const meta = TONE_BY_CLASSIFICATION[cls] || TONE_BY_CLASSIFICATION.STRUCTURAL_LEAN;
  const v2 = item.mlb_script_v2 || {};
  const sq = item.structural_quality || {};

  const marginNum     = asNumber(v2.marginProjection);
  const coverNum      = asNumber(v2.coverProbability);
  const erNum         = asNumber(v2.expectedRuns);
  // BUGFIX — new fields surfaced from the engine.
  const edgeNum       = asNumber(v2.edgeVsLine);
  const probUnderNum  = asNumber(v2.probabilityUnder);
  const probOverNum   = asNumber(v2.probabilityOver);
  const probModel     = v2.probabilityModel || null;
  const probDebug     = v2.probabilityDebug || null;
  const recommendedMarket = v2.recommendedLine || (probDebug && probDebug.recommended_market);

  // Client-side EV calculation when the user pastes their bookie odds.
  const edgeCalc = manualOdds && coverNum !== null
    ? computeEdge(manualOdds, coverNum)
    : null;

  return (
    <div
      className={`rounded-lg border border-${meta.tone}-500/25 bg-${meta.tone}-500/[0.04] overflow-hidden`}
      data-testid={testId || `manual-review-row-${idx}`}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-foreground/[0.02] transition-colors"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid={`manual-review-row-${idx}-toggle`}
      >
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <AlertCircle className={`h-4 w-4 shrink-0 text-${meta.tone}-300/80`} />
          <span className="font-medium text-sm truncate">{item.match_label}</span>
          <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] border border-${meta.tone}-500/40 bg-${meta.tone}-500/15 text-${meta.tone}-200`}>
            {meta.label}
          </span>
          {sq.level ? (
            <span className="text-[10px] text-muted-foreground/80">
              calidad estructural · {sq.level} ({sq.score}/100)
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {marginNum !== null ? (
            <span className="text-[10px] tabular-nums text-muted-foreground">
              marg {marginNum >= 0 ? '+' : ''}{marginNum.toFixed(2)}
            </span>
          ) : null}
          {coverNum !== null ? (
            <span className="text-[10px] tabular-nums text-muted-foreground">
              cover {coverNum.toFixed(0)}%
            </span>
          ) : null}
          <ChevronDown className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-border/30 pt-3" data-testid={`manual-review-row-${idx}-body`}>
          {/* Reason */}
          {item.reason ? (
            <p className="text-[12px] text-muted-foreground leading-relaxed">
              {item.reason}
            </p>
          ) : null}

          {/* v2 script metrics */}
          {(marginNum !== null || coverNum !== null || erNum !== null || v2.recommendedLine || edgeNum !== null) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px]">
              {marginNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Activity className="h-3 w-3" /> Projected margin</div>
                  <div className="font-medium tabular-nums mt-0.5">{marginNum >= 0 ? '+' : ''}{marginNum.toFixed(2)} carreras</div>
                </div>
              ) : null}
              {coverNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40" data-testid={`manual-review-row-${idx}-cover-prob`}>
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Target className="h-3 w-3" /> Cover probability</div>
                  <div className="font-medium tabular-nums mt-0.5">{coverNum.toFixed(1)}%</div>
                </div>
              ) : null}
              {erNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]"><Gauge className="h-3 w-3" /> Expected runs</div>
                  <div className="font-medium tabular-nums mt-0.5">{erNum.toFixed(1)}</div>
                </div>
              ) : null}
              {v2.recommendedLine ? (
                <div className="rounded-md border border-emerald-500/20 px-2 py-1.5 bg-emerald-500/[0.05]">
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">Línea recomendada</div>
                  <div className="font-medium mt-0.5 text-emerald-200">{v2.recommendedLine}</div>
                </div>
              ) : null}
              {/* BUGFIX — Edge vs Line displayed when the engine produced a totals
                  recommendation. Positive number ⇒ Over has cushion; negative ⇒ Under. */}
              {edgeNum !== null ? (
                <div
                  className={`rounded-md border px-2 py-1.5 ${edgeNum >= 0 ? 'border-emerald-500/20 bg-emerald-500/[0.04]' : 'border-cyan-500/20 bg-cyan-500/[0.04]'}`}
                  data-testid={`manual-review-row-${idx}-edge-vs-line`}
                >
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">Edge vs línea</div>
                  <div className={`font-medium tabular-nums mt-0.5 ${edgeNum >= 0 ? 'text-emerald-200' : 'text-cyan-200'}`}>
                    {edgeNum >= 0 ? '+' : ''}{edgeNum.toFixed(2)} carreras
                  </div>
                </div>
              ) : null}
              {probUnderNum !== null && probOverNum !== null ? (
                <div className="rounded-md border border-border/40 px-2 py-1.5 bg-background/40" data-testid={`manual-review-row-${idx}-under-over-probs`}>
                  <div className="flex items-center gap-1 text-muted-foreground/70 text-[10px]">Probabilidades</div>
                  <div className="font-medium tabular-nums mt-0.5 text-[10.5px]">
                    U {probUnderNum.toFixed(0)}% · O {probOverNum.toFixed(0)}%
                  </div>
                </div>
              ) : null}
            </div>
          )}

          {/* BUGFIX — Probability model provenance / debug. Visible in expanded body
              so the user can audit how `coverProbability` was derived. */}
          {probModel ? (
            <details className="rounded-md border border-border/40 bg-background/30 px-2.5 py-1.5 text-[10.5px]" data-testid={`manual-review-row-${idx}-prob-debug`}>
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground/80 flex items-center gap-1.5">
                <Calculator className="h-3 w-3" />
                Modelo probabilístico: <span className="font-medium text-foreground/80">{probModel}</span>
              </summary>
              <ul className="mt-2 space-y-0.5 text-muted-foreground/85 font-mono leading-relaxed">
                <li>Projected runs · {erNum !== null ? erNum.toFixed(1) : '—'}</li>
                <li>Recommended market · {recommendedMarket || '—'}</li>
                <li>P(Under {(recommendedMarket||'').toString().match(/\d+(\.\d+)?/)?.[0] || '?'}) · {probUnderNum !== null ? `${probUnderNum.toFixed(1)}%` : '—'}</li>
                <li>P(Over  {(recommendedMarket||'').toString().match(/\d+(\.\d+)?/)?.[0] || '?'}) · {probOverNum  !== null ? `${probOverNum.toFixed(1)}%`  : '—'}</li>
                <li>Edge vs line · {edgeNum !== null ? `${edgeNum >= 0 ? '+' : ''}${edgeNum.toFixed(2)} carreras` : '—'}</li>
                <li>Cover probability (mercado recomendado) · {coverNum !== null ? `${coverNum.toFixed(1)}%` : '—'}</li>
              </ul>
            </details>
          ) : null}

          {/* Explosive Inning Risk (replaces the older "Mercados a revisar
              manualmente" chips). Rendered only when the new layer
              produced a verdict — otherwise gracefully omitted. */}
          {item.explosive_inning_risk ? (
            <ExplosiveInningRiskPanel
              risk={item.explosive_inning_risk}
              action={item.explosive_inning_risk_action}
              idx={idx}
            />
          ) : (item.suggested_markets || []).length ? (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground/80 mb-1.5">
                Mercados sugeridos para revisar
              </div>
              <div className="flex flex-wrap gap-1.5">
                {item.suggested_markets.map((m, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] border border-border bg-background/60"
                    data-testid={`manual-review-row-${idx}-market-${i}`}
                  >
                    {m}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {/* Manual odds paste — client-side EV calculator (BUGFIX P2 activation).
              The recommended `coverProbability` is honoured (Poisson for totals,
              dominance for Run Line) and we compute EV = prob*(odds-1) - (1-prob). */}
          <div className="rounded-md border border-border/60 bg-background/30 px-3 py-2 space-y-2" data-testid={`manual-review-row-${idx}-odds-calc`}>
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <label className="text-[11px] text-muted-foreground flex items-center gap-1.5" htmlFor={`odds-input-${idx}`}>
                <Calculator className="h-3 w-3" />
                Pega tu cuota decimal para {recommendedMarket || 'este mercado'}:
              </label>
              <input
                id={`odds-input-${idx}`}
                type="number"
                step="0.01"
                min="1.01"
                placeholder="1.90"
                value={manualOdds}
                onChange={(e) => setManualOdds(e.target.value)}
                className="w-24 text-[12px] tabular-nums bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-cyan-500/40"
                data-testid={`manual-review-row-${idx}-odds-input`}
              />
            </div>
            {edgeCalc ? (
              <div
                className={`rounded border px-2 py-1.5 text-[11px] grid grid-cols-3 gap-2 tabular-nums ${edgeCalc.edgePct >= 0 ? 'border-emerald-500/30 bg-emerald-500/[0.06]' : 'border-rose-500/30 bg-rose-500/[0.05]'}`}
                data-testid={`manual-review-row-${idx}-edge-result`}
              >
                <div>
                  <div className="text-muted-foreground/70 text-[10px]">EV</div>
                  <div className={`font-semibold ${edgeCalc.edgePct >= 0 ? 'text-emerald-200' : 'text-rose-300'}`}>
                    {edgeCalc.edgePct >= 0 ? '+' : ''}{edgeCalc.edgePct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground/70 text-[10px]">Implied</div>
                  <div className="text-foreground/80">{edgeCalc.impliedProbPct.toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-muted-foreground/70 text-[10px]">Modelo</div>
                  <div className="text-foreground/80">{coverNum?.toFixed(1)}%</div>
                </div>
              </div>
            ) : manualOdds ? (
              <div className="text-[10px] text-rose-300/80">Cuota debe ser &gt; 1.01.</div>
            ) : null}
          </div>

          {/* Structural quality reasons */}
          {(sq.reasons || []).length ? (
            <details className="text-[10px] text-muted-foreground/80">
              <summary className="cursor-pointer hover:text-muted-foreground">¿Por qué entró aquí?</summary>
              <ul className="mt-1.5 space-y-0.5 pl-4 list-disc">
                {sq.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function ManualOddsReviewPanel({ items, lang = 'es', testId }) {
  if (!items || items.length === 0) return null;
  const headline = lang === 'en'
    ? `Manual review — odds missing (${items.length})`
    : `Revisión manual — falta cuota (${items.length})`;
  const subcopy = lang === 'en'
    ? 'No automatic odds for these games. The MLB engine found possible markets — paste your bookmaker odds to compute edge.'
    : 'Sin cuota automática. El análisis estructural encontró posibles mercados para revisar con tu bookie.';

  return (
    <div className="space-y-3" data-testid={testId || 'manual-odds-review-section'}>
      <div className="rounded-lg border border-cyan-500/25 bg-cyan-500/[0.05] px-3 py-2 text-xs uppercase tracking-wide font-semibold text-cyan-200 flex items-center gap-2">
        <AlertCircle className="h-3.5 w-3.5" />
        {headline}
      </div>
      <p className="text-xs text-muted-foreground">{subcopy}</p>
      <div className="grid gap-2">
        {items.map((it, i) => (
          <ManualReviewRow key={(it.match_id || '') + '-' + i} item={it} idx={i} />
        ))}
      </div>
    </div>
  );
}

export default ManualOddsReviewPanel;
